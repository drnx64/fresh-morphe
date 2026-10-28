"""Read-only phone API for MorpheTracker.

Stdlib only so Termux does not need a web framework. The process binds to
localhost; Cloudflare Tunnel (or a VPS reverse proxy) is the public edge.
"""
import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from server.config import API_HOST, API_PORT, DB_PATH, GZIP_MIN_BYTES, PUBLIC_MAX_AGE
from server.db import connect, get_meta, get_snapshot, table_counts, utc_now
from server.devlog import dev_logging_enabled


def json_dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def as_int(value, default, minimum=0, maximum=None):
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def build_index(conn):
    rows = conn.execute(
        """
        SELECT b.id, b.bundle, b.channel, b.index_version, b.release_tag, b.release_date,
               b.patches_name, b.app_count, b.is_prerelease,
               r.url AS repo_url, r.stars, r.avatar_url, r.bundle_image_url,
               r.description AS repo_description, r.is_archived, r.is_fork, r.fork_parent
        FROM bundles b
        JOIN repos r ON r.id = b.repo_id
        ORDER BY b.bundle, b.channel
        """
    ).fetchall()
    app_rows = conn.execute(
        """
        SELECT ba.bundle_id, a.package
        FROM bundle_apps ba
        JOIN apps a ON a.id = ba.app_id
        ORDER BY ba.bundle_id, ba.position
        """
    ).fetchall()
    apps_by_bundle = {}
    for row in app_rows:
        apps_by_bundle.setdefault(row["bundle_id"], []).append(row["package"])

    index = {}
    for row in rows:
        key = f'{row["bundle"]}:{row["channel"]}'
        packages = apps_by_bundle.get(row["id"], [])
        index[key] = {
            "bundle": row["bundle"],
            "channel": row["channel"],
            "version": row["index_version"],
            "repo_url": row["repo_url"],
            "patches_name": row["patches_name"],
            "release_tag": row["release_tag"],
            "release_date": row["release_date"],
            "app_count": len(packages) if packages else row["app_count"],
            "apps": packages,
            "stars": row["stars"],
            "avatarUrl": row["avatar_url"],
            "bundleImageUrl": row["bundle_image_url"],
            "repoDescription": row["repo_description"],
            "isArchived": bool(row["is_archived"]),
            "isPreRelease": bool(row["is_prerelease"]),
            "isFork": bool(row["is_fork"]),
            "forkParent": row["fork_parent"],
        }
    return index


def build_detail(conn, bundle, channel):
    row = conn.execute(
        """
        SELECT b.*, r.url AS repo_url
        FROM bundles b
        JOIN repos r ON r.id = b.repo_id
        WHERE b.bundle = ? AND b.channel = ?
        """,
        (bundle, channel),
    ).fetchone()
    if not row:
        return None

    app_rows = conn.execute(
        """
        SELECT ba.app_id, ba.name, ba.position, a.package
        FROM bundle_apps ba
        JOIN apps a ON a.id = ba.app_id
        WHERE ba.bundle_id = ?
        ORDER BY ba.position
        """,
        (row["id"],),
    ).fetchall()
    patch_rows = conn.execute(
        """
        SELECT p.app_id, p.name, p.description, p.use_default, p.options_json,
               p.compatible_versions_json, ba.position
        FROM patches p
        JOIN bundle_apps ba ON ba.bundle_id = p.bundle_id AND ba.app_id = p.app_id
        WHERE p.bundle_id = ?
        ORDER BY ba.position, p.id
        """,
        (row["id"],),
    ).fetchall()

    apps = {}
    order = []
    for app_row in app_rows:
        app_id = app_row["app_id"]
        apps[app_id] = {
            "app_name": app_row["name"] or app_row["package"],
            "package": app_row["package"],
            "patches": [],
        }
        order.append(app_id)

    for patch in patch_rows:
        app = apps.get(patch["app_id"])
        if not app:
            continue
        try:
            options = json.loads(patch["options_json"] or "[]")
        except ValueError:
            options = []
        try:
            versions = json.loads(patch["compatible_versions_json"] or "[]")
        except ValueError:
            versions = []
        app["patches"].append({
            "name": patch["name"],
            "description": patch["description"],
            "use": bool(patch["use_default"]),
            "options": options,
            "compatible_versions": versions,
        })

    return {
        "bundle": row["bundle"],
        "channel": row["channel"],
        "repo_url": row["repo_url"],
        "created_at": row["created_at"],
        "version": row["version"],
        "description": row["description"],
        "apps": [apps[app_id] for app_id in order],
        "patches_name": row["patches_name"],
        "fingerprint": row["fingerprint"],
        "release_notes": row["release_notes"],
        "release_tag": row["release_tag"],
        "release_date": row["release_date"],
        "isPreRelease": bool(row["is_prerelease"]),
    }


def build_releases(conn, repo_id, limit=50):
    rows = conn.execute(
        """
        SELECT tag, name, body, prerelease, published_at
        FROM releases
        WHERE repo_id = ?
        ORDER BY published_at DESC, tag DESC
        LIMIT ?
        """,
        (repo_id, limit),
    ).fetchall()
    return [
        {
            "tag": row["tag"],
            "name": row["name"],
            "body": row["body"],
            "prerelease": bool(row["prerelease"]),
            "dateReleased": row["published_at"],
        }
        for row in rows
    ]


def bundle_repo_id(conn, bundle, channel):
    row = conn.execute(
        "SELECT repo_id FROM bundles WHERE bundle = ? AND channel = ?",
        (bundle, channel),
    ).fetchone()
    return row["repo_id"] if row else None


def search_apps(conn, query, limit=20):
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT package, name, icon_url
        FROM apps
        WHERE package LIKE ? OR name LIKE ?
        ORDER BY CASE WHEN package = ? THEN 0 WHEN package LIKE ? THEN 1 ELSE 2 END, name
        LIMIT ?
        """,
        (like, like, query, f"{query}%", limit),
    ).fetchall()
    return [
        {"package": row["package"], "name": row["name"], "icon_url": row["icon_url"]}
        for row in rows
    ]


def app_detail(conn, package):
    app = conn.execute(
        "SELECT id, package, name, icon_url FROM apps WHERE package = ?",
        (package,),
    ).fetchone()
    if not app:
        return None
    rows = conn.execute(
        """
        SELECT b.bundle, b.channel, b.version, b.release_tag, b.release_date,
               b.patches_name, r.url AS repo_url
        FROM bundle_apps ba
        JOIN bundles b ON b.id = ba.bundle_id
        JOIN repos r ON r.id = b.repo_id
        WHERE ba.app_id = ?
        ORDER BY b.bundle, b.channel
        """,
        (app["id"],),
    ).fetchall()
    patch_count = conn.execute(
        """
        SELECT COUNT(*) AS n FROM patches p
        JOIN apps a ON a.id = p.app_id
        WHERE a.package = ?
        """,
        (package,),
    ).fetchone()["n"]
    return {
        "package": app["package"],
        "name": app["name"],
        "icon_url": app["icon_url"],
        "patch_count": patch_count,
        "bundles": [
            {
                "bundle": row["bundle"],
                "channel": row["channel"],
                "version": row["version"],
                "release_tag": row["release_tag"],
                "release_date": row["release_date"],
                "patches_name": row["patches_name"],
                "repo_url": row["repo_url"],
            }
            for row in rows
        ],
    }


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "MorpheTrackerAPI/1"

    def log_message(self, fmt, *args):
        if getattr(self, "_dev", False):
            return
        print(f"[api] {self.address_string()} {fmt % args}", flush=True)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, If-None-Match, ngrok-skip-browser-warning")
        self.send_header("Access-Control-Expose-Headers", "ETag")

    def _send_json(self, data, status=200, max_age=PUBLIC_MAX_AGE):
        self._last_status = status
        raw = json_dumps(data).encode("utf-8")
        etag = '"' + hashlib.sha256(raw).hexdigest()[:32] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self._cors()
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", f"public, max-age={max_age}, stale-while-revalidate=86400")
            self.end_headers()
            return

        encoding = None
        body = raw
        if len(raw) >= GZIP_MIN_BYTES and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(raw, compresslevel=6)
            encoding = "gzip"

        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", f"public, max-age={max_age}, stale-while-revalidate=86400")
        self.send_header("Vary", "Accept-Encoding")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error(self, status, message):
        self._send_json({"error": message, "status": status}, status=status, max_age=0)

    def _open_db(self):
        return connect(self.server.db_path, readonly=True)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._dev = dev_logging_enabled(self.headers.get("Host"))
        self._request_started = time.perf_counter()
        self._last_status = 0
        self._request_path = urlparse(self.path).path
        try:
            self._handle_get()
        finally:
            if self._dev:
                elapsed_ms = (time.perf_counter() - self._request_started) * 1000
                host = self.headers.get("Host", "")
                print(
                    f"[api:dev] {self.command} {self._request_path} "
                    f"{self._last_status or 0} {elapsed_ms:.1f}ms host={host}",
                    flush=True,
                )

    def _handle_get(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if path == "/":
            self._root()
            return

        if path == "/healthz":
            self._health()
            return

        if not path.startswith("/v1/"):
            self._send_error(404, "not found")
            return

        segments = [segment for segment in path[len("/v1/"):].split("/") if segment]
        try:
            conn = self._open_db()
        except (sqlite3.Error, OSError):
            self._send_error(503, "database unavailable")
            return
        try:
            self._route(conn, segments, query)
        except Exception as exc:  # keep the API alive on a bad row
            print(f"[api] error {path}: {exc}", flush=True)
            self._send_error(500, "internal error")
        finally:
            conn.close()

    def _root(self):
        self._send_json({
            "ok": True,
            "service": "MorpheTracker read-only API",
            "version": "v1",
            "endpoints": {
                "health": "/healthz",
                "meta": "/v1/meta",
                "index": "/v1/index",
                "bundle": "/v1/bundles/{name}/{channel}",
                "releases": "/v1/bundles/{name}/{channel}/releases",
                "app": "/v1/apps/{package}",
                "search": "/v1/search?q={query}",
                "changelog": "/v1/changelog",
            },
        }, max_age=0)

    def _health(self):
        try:
            conn = self._open_db()
            try:
                conn.execute("SELECT 1").fetchone()
                payload = {
                    "ok": True,
                    "data_version": get_meta(conn, "data_version", ""),
                    "last_success_at": get_meta(conn, "last_success_at", ""),
                    "counts": table_counts(conn),
                    "checked_at": utc_now(),
                }
            finally:
                conn.close()
            self._send_json(payload, max_age=0)
        except (sqlite3.Error, OSError):
            self._send_json({"ok": False, "error": "database unavailable", "checked_at": utc_now()}, status=503, max_age=0)

    def _route(self, conn, segments, query):
        if not segments:
            self._send_error(404, "not found")
            return

        head = segments[0]
        if head == "meta":
            self._meta(conn)
        elif head == "index":
            self._send_json(build_index(conn))
        elif head in ("core", "stats", "changes", "app-cache", "repo-cache"):
            name = head.replace("-", "_")
            value = get_snapshot(conn, name, None)
            if value is None:
                if head == "stats":
                    value = {
                        "total_bundles": int(get_meta(conn, "bundle_count", "0")),
                        "total_apps": int(get_meta(conn, "app_count", "0")),
                    }
                else:
                    value = {}
            self._send_json(value)
        elif head == "changelog":
            limit = as_int((query.get("limit") or ["30"])[0], 30, 1, 200)
            rows = conn.execute(
                "SELECT payload_json FROM changes ORDER BY date DESC LIMIT ?", (limit,)
            ).fetchall()
            self._send_json([json.loads(row["payload_json"]) for row in rows])
        elif head == "bundles":
            self._bundles(conn, segments, query)
        elif head == "repos":
            self._repo_releases(conn, segments, query)
        elif head == "apps":
            self._apps(conn, segments, query)
        elif head == "search":
            self._search(conn, query)
        else:
            self._send_error(404, "not found")

    def _meta(self, conn):
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
        counts = table_counts(conn)
        last_job = conn.execute(
            "SELECT started_at, finished_at, status, stats_json, error FROM jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        payload = {
            "schema_version": int(meta.get("schema_version", "1")),
            "data_version": meta.get("data_version", ""),
            "generated_at": meta.get("generated_at", ""),
            "last_success_at": meta.get("last_success_at", ""),
            "imported_at": meta.get("imported_at", ""),
            "source": meta.get("source", ""),
            "counts": counts,
            "latest_count": int(meta.get("latest_count", "0")),
            "last_job": dict(last_job) if last_job else None,
        }
        self._send_json(payload)

    def _bundles(self, conn, segments, query):
        if len(segments) == 1:
            index = build_index(conn)
            channel = (query.get("channel") or [""])[0]
            term = (query.get("q") or [""])[0].lower()
            items = []
            for key, entry in index.items():
                if channel and entry["channel"] != channel:
                    continue
                if term and term not in key.lower() and term not in entry.get("patches_name", "").lower():
                    continue
                items.append(entry)
            limit = as_int((query.get("limit") or ["100"])[0], 100, 1, 1000)
            offset = as_int((query.get("offset") or ["0"])[0], 0, 0)
            self._send_json({"total": len(items), "items": items[offset:offset + limit]})
            return

        bundle = segments[1]
        channel = segments[2] if len(segments) >= 3 else "stable"
        if len(segments) >= 4 and segments[3] == "releases":
            repo_id = bundle_repo_id(conn, bundle, channel)
            if not repo_id:
                self._send_error(404, "bundle not found")
                return
            limit = as_int((query.get("limit") or ["50"])[0], 50, 1, 200)
            self._send_json({"bundle": bundle, "channel": channel, "releases": build_releases(conn, repo_id, limit)})
            return
        if len(segments) != 3:
            self._send_error(404, "not found")
            return
        detail = build_detail(conn, bundle, channel)
        if not detail:
            self._send_error(404, "bundle not found")
            return
        self._send_json(detail)

    def _repo_releases(self, conn, segments, query):
        if len(segments) < 3:
            self._send_error(404, "not found")
            return
        slug = f"{segments[1]}/{segments[2]}".lower()
        row = conn.execute("SELECT id FROM repos WHERE slug = ?", (slug,)).fetchone()
        if not row:
            self._send_error(404, "repo not found")
            return
        limit = as_int((query.get("limit") or ["50"])[0], 50, 1, 200)
        self._send_json({"repo": slug, "releases": build_releases(conn, row["id"], limit)})

    def _apps(self, conn, segments, query):
        if len(segments) == 1:
            term = (query.get("q") or [""])[0].strip()
            limit = as_int((query.get("limit") or ["20"])[0], 20, 1, 100)
            if term:
                self._send_json({"items": search_apps(conn, term, limit)})
                return
            rows = conn.execute(
                "SELECT package, name, icon_url FROM apps ORDER BY name LIMIT ?", (limit,)
            ).fetchall()
            self._send_json({"items": [dict(row) for row in rows]})
            return
        if len(segments) != 2:
            self._send_error(404, "not found")
            return
        detail = app_detail(conn, segments[1])
        if not detail:
            self._send_error(404, "app not found")
            return
        self._send_json(detail)

    def _search(self, conn, query):
        term = (query.get("q") or [""])[0].strip()
        if len(term) < 2:
            self._send_json({"apps": [], "patches": []})
            return
        limit = as_int((query.get("limit") or ["20"])[0], 20, 1, 50)
        like = f"%{term}%"
        apps = search_apps(conn, term, limit)
        patches = conn.execute(
            """
            SELECT p.name, p.description, a.package, a.name AS app_name
            FROM patches p
            JOIN apps a ON a.id = p.app_id
            WHERE p.name LIKE ? OR p.description LIKE ?
            GROUP BY p.name, a.package
            ORDER BY p.name
            LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
        self._send_json({
            "apps": apps,
            "patches": [
                {"name": row["name"], "description": row["description"], "package": row["package"], "app_name": row["app_name"]}
                for row in patches
            ],
        })


def serve(db_path=DB_PATH, host=API_HOST, port=API_PORT):
    if not os.path.exists(db_path):
        raise SystemExit(f"database not found: {db_path} (run server.import_legacy first)")
    server = ThreadingHTTPServer((host, port), ApiHandler)
    server.daemon_threads = True
    server.allow_reuse_address = True
    server.db_path = db_path
    print(f"[api] listening on http://{host}:{port} using {db_path}", flush=True)
    print("[api] verbose logging: localhost requests (MORPHE_DEBUG=1/0 overrides)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the read-only MorpheTracker API")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--host", default=API_HOST)
    parser.add_argument("--port", type=int, default=API_PORT)
    args = parser.parse_args(argv)
    serve(args.db, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
