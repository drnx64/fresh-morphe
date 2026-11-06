"""Import the existing JSON pipeline output into the phone SQLite database.

Usage:
    python -m server.import_legacy --reset
    python -m server.import_legacy --db ~/fresh-morphe/fresh.db --data-dir ./data
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from server.config import DATA_DIR, DB_PATH
from server.db import (
    connect,
    init_db,
    json_dumps,
    prune_releases,
    set_meta,
    set_snapshot,
    table_counts,
    utc_now,
)


REPO_RE = re.compile(r"^https?://(?:www\.)?(github|gitlab)\.com/([^/]+)/([^/#?]+)", re.I)
BUNDLE_CHANNELS = {"stable", "latest", "dev"}


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        print(f"[import] warning: cannot read {path}: {exc}", file=sys.stderr)
        return default


def normalize_url(url):
    """Return a stable URL key while preserving the original display URL."""
    if not isinstance(url, str):
        return ""
    value = url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4].rstrip("/")
    return value


def repo_parts(url):
    match = REPO_RE.match(url or "")
    if not match:
        return "", "", ""
    platform, owner, repo = match.group(1).lower(), match.group(2), match.group(3)
    return platform, owner, repo


def repo_slug(url):
    platform, owner, repo = repo_parts(url)
    if not platform:
        return ""
    return f"{owner}/{repo}".lower()


def as_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return 0.0
    return 0.0


def lower_map(data):
    result = {}
    for key, value in (data or {}).items():
        norm = normalize_url(key)
        if norm:
            result[norm.lower()] = (key, value)
        if isinstance(value, dict) and value.get("repo_url"):
            other = normalize_url(value["repo_url"]).lower()
            if other:
                result.setdefault(other, (key, value))
    return result


def ensure_repo(conn, url, cache_entry=None, record=None, now=None):
    url = normalize_url(url)
    if not url:
        return None
    cache_entry = cache_entry or {}
    record = record or {}
    url_key = url.lower()
    platform, owner, repo = repo_parts(url)
    slug = repo_slug(url)

    values = {
        "url": url,
        "url_key": url_key,
        "slug": slug,
        "platform": platform,
        "owner": owner,
        "repo": repo,
        "name": cache_entry.get("name", "") or "",
        "description": record.get("repoDescription") or cache_entry.get("repoDescription") or "",
        "avatar_url": record.get("avatarUrl") or cache_entry.get("avatarUrl") or "",
        "bundle_image_url": record.get("bundleImageUrl") or cache_entry.get("bundleImageUrl") or "",
        "stars": int(record.get("stars") or cache_entry.get("stars") or 0),
        "is_archived": 1 if (record.get("isArchived") or cache_entry.get("isArchived")) else 0,
        "is_fork": 1 if (record.get("isFork") or cache_entry.get("isFork")) else 0,
        "fork_parent": record.get("forkParent") or cache_entry.get("forkParent") or "",
        "fetched_at": as_float(cache_entry.get("fetched_at")),
    }

    row = conn.execute(
        "SELECT id, name, description, avatar_url, bundle_image_url, stars, "
        "is_archived, is_fork, fork_parent, fetched_at FROM repos WHERE url_key = ?",
        (url_key,),
    ).fetchone()
    if row:
        repo_id = row["id"]
        # Source records (eg. awesome-morphe's repos.json) carry only a subset
        # of the metadata. Never erase richer values with an empty refresh.
        values["name"] = values["name"] or row["name"]
        values["description"] = values["description"] or row["description"]
        values["avatar_url"] = values["avatar_url"] or row["avatar_url"]
        values["bundle_image_url"] = values["bundle_image_url"] or row["bundle_image_url"]
        values["stars"] = values["stars"] or row["stars"]
        if "isArchived" not in cache_entry and "isArchived" not in record:
            values["is_archived"] = row["is_archived"]
        if "isFork" not in cache_entry and "isFork" not in record:
            values["is_fork"] = row["is_fork"]
        values["fork_parent"] = values["fork_parent"] or row["fork_parent"]
        values["fetched_at"] = values["fetched_at"] or row["fetched_at"]
        conn.execute(
            """
            UPDATE repos SET
              url = ?, slug = ?, platform = ?, owner = ?, repo = ?,
              name = ?, description = ?, avatar_url = ?, bundle_image_url = ?,
              stars = ?, is_archived = ?, is_fork = ?, fork_parent = ?, fetched_at = ?
            WHERE id = ?
            """,
            (
                values["url"], values["slug"], values["platform"], values["owner"], values["repo"],
                values["name"], values["description"], values["avatar_url"], values["bundle_image_url"],
                values["stars"], values["is_archived"], values["is_fork"], values["fork_parent"],
                values["fetched_at"], repo_id,
            ),
        )
        return repo_id

    cur = conn.execute(
        """
        INSERT INTO repos(
          url, url_key, slug, platform, owner, repo, name, description,
          avatar_url, bundle_image_url, stars, is_archived, is_fork, fork_parent, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            values["url"], values["url_key"], values["slug"], values["platform"], values["owner"],
            values["repo"], values["name"], values["description"], values["avatar_url"],
            values["bundle_image_url"], values["stars"], values["is_archived"], values["is_fork"],
            values["fork_parent"], values["fetched_at"],
        ),
    )
    return cur.lastrowid


_COUNTRY_CODE_RE = re.compile(r"^[a-z]{2}$")
_ORG_MARKERS = {"ac", "co", "com", "edu", "gov", "ne", "net", "or", "org"}


def _package_namespace(package):
    """Publisher namespace of a package id.

    Mirrors scripts/app_metadata.package_namespace (that module is not
    importable from the server package) so the API and the pipeline label a
    publisher identically when the registry has no store-resolved name.
    """
    segments = [seg for seg in str(package or "").lower().split(".") if seg]
    if not segments:
        return ""
    if len(segments) == 1:
        return segments[0]
    behind_country_code = (
        bool(_COUNTRY_CODE_RE.match(segments[0])) and segments[1] in _ORG_MARKERS
    )
    return ".".join(segments[: 3 if behind_country_code else 2])


def ensure_app(conn, package, name, icon_url="", developer=""):
    package = (package or "").strip()
    if not package:
        return None
    row = conn.execute(
        "SELECT id, name, icon_url, developer FROM apps WHERE package = ?", (package,)
    ).fetchone()
    name = name or ""
    icon_url = icon_url or ""
    developer = developer or ""
    if row:
        next_name = name or row["name"]
        next_icon = icon_url or row["icon_url"]
        next_developer = developer or row["developer"]
        if (
            next_name != row["name"]
            or next_icon != row["icon_url"]
            or next_developer != row["developer"]
        ):
            conn.execute(
                "UPDATE apps SET name = ?, icon_url = ?, developer = ? WHERE id = ?",
                (next_name, next_icon, next_developer, row["id"]),
            )
        return row["id"]
    cur = conn.execute(
        "INSERT INTO apps(package, name, icon_url, developer) VALUES (?, ?, ?, ?)",
        (package, name, icon_url, developer),
    )
    return cur.lastrowid


def upsert_bundle(conn, repo_id, bundle, channel, record, index_entry, imported_at):
    row = conn.execute(
        "SELECT id FROM bundles WHERE bundle = ? AND channel = ?",
        (bundle, channel),
    ).fetchone()
    release_notes = record.get("release_notes") or ""
    description = record.get("description") or release_notes
    detail_version = record.get("version") or index_entry.get("version") or ""
    values = (
        repo_id,
        bundle,
        channel,
        detail_version,
        index_entry.get("version") or detail_version,
        record.get("release_tag") or index_entry.get("release_tag") or "",
        record.get("release_date") or index_entry.get("release_date") or "",
        record.get("patches_name") or index_entry.get("patches_name") or "",
        description,
        release_notes,
        record.get("fingerprint") or "",
        record.get("created_at") or "",
        1 if (record.get("isPreRelease") or index_entry.get("isPreRelease")) else 0,
        "legacy" if channel == "latest" else "",
        len(record.get("apps") or []),
        imported_at,
    )
    if row:
        bundle_id = row["id"]
        conn.execute(
            """
            UPDATE bundles SET
              repo_id = ?, bundle = ?, channel = ?, version = ?, index_version = ?,
              release_tag = ?, release_date = ?, patches_name = ?, description = ?,
              release_notes = ?, fingerprint = ?, created_at = ?, is_prerelease = ?,
              latest_origin = ?, app_count = ?, imported_at = ?
            WHERE id = ?
            """,
            values + (bundle_id,),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO bundles(
              repo_id, bundle, channel, version, index_version, release_tag,
              release_date, patches_name, description, release_notes, fingerprint,
              created_at, is_prerelease, latest_origin, app_count, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        bundle_id = cur.lastrowid

    conn.execute("DELETE FROM patches WHERE bundle_id = ?", (bundle_id,))
    conn.execute("DELETE FROM bundle_apps WHERE bundle_id = ?", (bundle_id,))

    apps = record.get("apps") or []
    # Source records occasionally repeat a package or a patch name; insert each
    # (app, patch) once so the unique index never sees a conflict and targets
    # aren't written twice.
    seen_packages = set()
    seen_patches = set()
    app_count = 0
    for position, app in enumerate(apps):
        package = app.get("package") or ""
        if not package or package in seen_packages:
            continue
        seen_packages.add(package)
        app_name = app.get("app_name") or package
        cache = app_cache_get(package)
        developer = cache.get("developer", "") or _package_namespace(package)
        app_id = ensure_app(conn, package, app_name, cache.get("icon_url", ""), developer)
        if not app_id:
            continue
        app_count += 1
        conn.execute(
            "INSERT OR REPLACE INTO bundle_apps(bundle_id, app_id, name, position) VALUES (?, ?, ?, ?)",
            (bundle_id, app_id, app_name, position),
        )
        for patch in app.get("patches") or []:
            patch_name = patch.get("name") or ""
            if not patch_name:
                continue
            patch_key = (app_id, patch_name)
            if patch_key in seen_patches:
                continue
            seen_patches.add(patch_key)
            cur = conn.execute(
                """
                INSERT INTO patches(
                  bundle_id, app_id, name, description, use_default,
                  options_json, compatible_versions_json, fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bundle_id,
                    app_id,
                    patch_name,
                    patch.get("description") or "",
                    1 if patch.get("use") else 0,
                    json_dumps(patch.get("options") or []),
                    json_dumps(patch.get("compatible_versions") or []),
                    patch.get("fingerprint") or "",
                ),
            )
            patch_id = cur.lastrowid
            targets = patch.get("targets") or []
            if targets:
                for target in targets:
                    version = target.get("version") or ""
                    if not version:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO patch_targets(patch_id, version, version_code, min_sdk, experimental) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            patch_id,
                            str(version),
                            str(target.get("version_code") or ""),
                            target.get("min_sdk"),
                            1 if target.get("experimental") else 0,
                        ),
                    )
            else:
                for version in patch.get("compatible_versions") or []:
                    conn.execute(
                        "INSERT OR IGNORE INTO patch_targets(patch_id, version) VALUES (?, ?)",
                        (patch_id, str(version)),
                    )

    # Recount from what was actually stored (the record may list duplicates).
    conn.execute("UPDATE bundles SET app_count = ? WHERE id = ?", (app_count, bundle_id))

    return bundle_id


def upsert_release(conn, repo_id, tag, body="", prerelease=False, published_at="", fetched_at=0, name=""):
    if not repo_id or not tag:
        return
    conn.execute(
        """
        INSERT INTO releases(repo_id, tag, name, body, prerelease, published_at, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_id, tag) DO UPDATE SET
          name = CASE WHEN excluded.name != '' THEN excluded.name ELSE releases.name END,
          body = CASE WHEN excluded.body != '' THEN excluded.body ELSE releases.body END,
          prerelease = excluded.prerelease,
          published_at = CASE WHEN excluded.published_at != '' THEN excluded.published_at ELSE releases.published_at END,
          fetched_at = CASE WHEN excluded.fetched_at > 0 THEN excluded.fetched_at ELSE releases.fetched_at END
        """,
        (repo_id, tag, name, body, 1 if prerelease else 0, published_at, fetched_at),
    )
    # History is capped at RELEASE_KEEP rows per repo so a repository with a
    # long release feed can never bloat the phone DB.
    prune_releases(conn, repo_id=repo_id)


# Filled by import_data() so the nested helper can see the app cache without
# threading it through every call.
_APP_CACHE = {}


def app_cache_get(package):
    return _APP_CACHE.get(package, {}) if package else {}


def _upsert_change(conn, day):
    if not isinstance(day, dict) or not day.get("date"):
        return
    conn.execute(
        "INSERT INTO changes(date, last_checked, payload_json) VALUES (?, ?, ?) "
        "ON CONFLICT(date) DO UPDATE SET last_checked = excluded.last_checked, payload_json = excluded.payload_json",
        (day.get("date"), day.get("lastChecked") or "", json_dumps(day)),
    )


def sync_changes(conn, data_dir, now=None):
    """Mirror the pipeline's daily changes into SQLite.

    `changes.json` is the current UTC day's merged aggregate, while
    `changelog.json` holds finalized days. Both land in the one-row-per-date
    `changes` table; same-day imports upsert, a new date inserts a new row.
    """
    data_dir = os.path.abspath(data_dir)
    now = now or utc_now()
    core_path = os.path.join(data_dir, "core.json")
    changes_path = os.path.join(data_dir, "changes.json")
    changelog_path = os.path.join(data_dir, "changelog.json")
    last_run_path = os.path.join(data_dir, "state", "last_run.json")

    core = load_json(core_path, {}) or {}
    changes_exists = os.path.exists(changes_path)
    changes = load_json(changes_path, {}) or {}
    changelog = load_json(changelog_path, []) or []
    last_run = load_json(last_run_path, {}) or {}

    last_checked = (
        last_run.get("lastChecked")
        or last_run.get("completed_at")
        or core.get("lastChecked")
        or now
    )
    today = (
        (changes.get("date") if isinstance(changes, dict) else None)
        or core.get("date")
        or last_checked[:10]
        or now[:10]
    )

    for day in changelog:
        _upsert_change(conn, day)

    affected = changes.get("affected_bundles") if isinstance(changes, dict) else None
    if not isinstance(affected, list):
        affected = []

    latest = changes.get("latest") if isinstance(changes, dict) else None
    if not isinstance(latest, dict) or not latest.get("affected_bundles"):
        latest = None
        if not affected:
            for day in changelog:
                if not isinstance(day, dict) or day.get("date") == today:
                    continue
                if day.get("affected_bundles"):
                    latest = {
                        "date": day["date"],
                        "affected_bundles": day["affected_bundles"],
                    }
                    break

    if affected:
        _upsert_change(conn, {
            "date": today,
            "lastChecked": last_checked,
            "affected_bundles": affected,
        })
    elif today:
        # Keep the existing row's timestamp fresh even on a no-change scan.
        conn.execute(
            "UPDATE changes SET last_checked = ? WHERE date = ?",
            (last_checked, today),
        )

    if changes_exists:
        snapshot = {"date": today, "affected_bundles": affected}
        if latest:
            snapshot["latest"] = latest
        set_snapshot(conn, "changes", snapshot)

    set_meta(conn, "last_checked", last_checked)
    if last_run.get("completed_at"):
        set_meta(conn, "last_success_at", last_run["completed_at"])

    return {
        "date": today,
        "last_checked": last_checked,
        "affected_bundles": len(affected),
    }


def prune_stale(conn, index):
    """Delete bundle rows that left `_index.json`, and what they orphaned.

    `import_data(reset=False)` only ever upserts, so a repository that stopped
    publishing kept its bundle rows — and every patch under them — until a
    manual `--reset` (558 of 2143 rows on the phone, 27% of all patch rows).
    Bundles belonging to repos the worker tracks in `repo_sources` are exempt:
    those rows are the worker's, fed by the mirror rather than the index.
    Cascades (patches, patch_targets, bundle_apps, releases) do the rest, and
    apps/repos left with nothing are removed in the same transaction.

    Returns how many rows each table lost.
    """
    # Mirror the bundle loop's key parsing and channel normalization so the
    # live set holds exactly what the loop (re)inserts above.
    live = set()
    for key, entry in index.items():
        bundle = entry.get("bundle") or key.rsplit(":", 1)[0]
        channel = entry.get("channel") or key.rsplit(":", 1)[-1]
        if channel not in BUNDLE_CHANNELS:
            channel = "stable"
        live.add((bundle, channel))

    worker_keys = {
        row[0] for row in conn.execute("SELECT DISTINCT url_key FROM repo_sources")
    }
    # The live set is ~1600 tuples — too many bindings for one statement
    # (SQLITE_MAX_VARIABLE_NUMBER is 999) — so match it in Python and delete
    # the stale ids in chunks.
    stale_ids = [
        row["id"]
        for row in conn.execute(
            "SELECT b.id, b.bundle, b.channel, r.url_key "
            "FROM bundles b LEFT JOIN repos r ON r.id = b.repo_id"
        )
        if (row["bundle"], row["channel"]) not in live
        and row["url_key"] not in worker_keys
    ]
    removed_bundles = 0
    for start in range(0, len(stale_ids), 500):
        chunk = stale_ids[start:start + 500]
        placeholders = ",".join("?" * len(chunk))
        removed_bundles += conn.execute(
            f"DELETE FROM bundles WHERE id IN ({placeholders})", chunk
        ).rowcount

    # Apps no live bundle claims, then repos with no bundles left. Both are
    # exempt from nothing: a worker-tracked repo with zero bundles is kept
    # (the worker owns that row and may repopulate it), releases cascade.
    removed_apps = conn.execute(
        "DELETE FROM apps WHERE NOT EXISTS ("
        "  SELECT 1 FROM bundle_apps ba WHERE ba.app_id = apps.id)"
    ).rowcount
    removed_repos = conn.execute(
        "DELETE FROM repos WHERE NOT EXISTS ("
        "  SELECT 1 FROM bundles b WHERE b.repo_id = repos.id)"
        " AND url_key NOT IN (SELECT url_key FROM repo_sources)"
    ).rowcount
    return {"bundles": removed_bundles, "apps": removed_apps, "repos": removed_repos}


def vacuum_if_needed(conn, threshold=0.12, min_pages=64):
    """Return freed pages to the OS once deletions leave real slack.

    The phone DB has no auto_vacuum, so prune only moves pages onto the
    freelist (~14 MB sat there). VACUUM only when the freelist is over
    `threshold` of the file and at least `min_pages` big, so quiet hourly
    imports don't pay for it; the guard makes it self-limiting (after one run
    the freelist is ~0 and it won't run again until more is deleted).
    Concurrent readers/writer may hold the lock past busy_timeout — skip
    rather than fail the import. Returns the page count that was reclaimed,
    or 0 when skipped/locked.
    """
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    if free < min_pages or free < pages * threshold:
        return 0
    try:
        conn.execute("VACUUM")
    except sqlite3.Error:
        return 0
    return free


def import_data(db_path, data_dir, reset=False):
    global _APP_CACHE
    data_dir = os.path.abspath(data_dir)
    db_path = os.path.abspath(db_path)
    if reset:
        for suffix in ("", "-wal", "-shm"):
            candidate = db_path + suffix
            if os.path.exists(candidate):
                os.remove(candidate)

    init_db(db_path)
    conn = connect(db_path)
    imported_at = utc_now()

    index_path = os.path.join(data_dir, "bundles", "_index.json")
    index = load_json(index_path, {}) or {}
    if not index:
        conn.close()
        raise SystemExit(f"No bundle index at {index_path}")

    app_cache = load_json(os.path.join(data_dir, "state", "app_cache.json"), {}) or {}
    repo_cache = load_json(os.path.join(data_dir, "state", "repo_cache.json"), {}) or {}
    release_cache = load_json(os.path.join(data_dir, "state", "release_cache.json"), {}) or {}
    changelog = load_json(os.path.join(data_dir, "changelog.json"), []) or []
    _APP_CACHE = {str(k): v for k, v in app_cache.items() if isinstance(v, dict)}
    repo_cache_by_key = lower_map(repo_cache)

    with conn:
        if reset:
            for table in (
                "patches", "bundle_apps", "bundles", "releases", "repos",
                "apps", "changes", "snapshots", "jobs", "settings", "meta",
            ):
                conn.execute(f"DELETE FROM {table}")

        expected = Counter()
        missing_records = []
        bundle_count = 0
        app_packages = set()
        bundle_app_count = 0
        patch_count = 0
        latest_count = 0

        for key, index_entry in sorted(index.items()):
            bundle = index_entry.get("bundle") or key.rsplit(":", 1)[0]
            channel = index_entry.get("channel") or key.rsplit(":", 1)[-1]
            if channel not in BUNDLE_CHANNELS:
                channel = "stable"
            detail_path = os.path.join(data_dir, "bundles", key.replace(":", "_") + ".json")
            record = load_json(detail_path, None)
            if not record:
                missing_records.append(key)
                record = {"apps": [], **index_entry}
            repo_url = record.get("repo_url") or index_entry.get("repo_url") or ""
            cache = repo_cache_by_key.get(normalize_url(repo_url).lower(), (None, {}))[1]
            repo_id = ensure_repo(conn, repo_url, cache, record)
            if not repo_id:
                missing_records.append(key + " (no repo url)")
                continue

            upsert_bundle(conn, repo_id, bundle, channel, record, index_entry, imported_at)
            bundle_count += 1
            if channel == "latest":
                latest_count += 1
            apps = record.get("apps") or []
            # Mirror upsert_bundle's dedupe so stats describe stored rows.
            seen = set()
            for app in apps:
                package = app.get("package") or ""
                if not package or package in seen:
                    continue
                seen.add(package)
                bundle_app_count += 1
                app_packages.add(package)
                seen_patches = set()
                for patch in app.get("patches") or []:
                    if patch.get("name") and patch["name"] not in seen_patches:
                        seen_patches.add(patch["name"])
                        patch_count += 1

            # Make sure the current release is represented even when the
            # release cache predates this bundle.
            tag = record.get("release_tag") or index_entry.get("release_tag")
            notes = record.get("release_notes") or record.get("description") or ""
            if tag:
                upsert_release(
                    conn,
                    repo_id,
                    tag,
                    body=notes,
                    prerelease=bool(record.get("isPreRelease") or index_entry.get("isPreRelease")),
                    published_at=record.get("release_date") or index_entry.get("release_date") or "",
                    fetched_at=0,
                )

        release_count = 0
        for url, cache in release_cache.items():
            repo_url = normalize_url(url)
            if not repo_url:
                continue
            # Lookup only: the bundle loop above already ensured every repo the
            # index knows about. Calling ensure_repo here used to re-create
            # repos that left the index on every run — rows prune_stale() would
            # then delete again in the same transaction.
            row = conn.execute(
                "SELECT id FROM repos WHERE url_key = ?", (repo_url.lower(),)
            ).fetchone()
            if not row:
                continue
            repo_id = row["id"]
            fetched_at = as_float(cache.get("fetched_at"))
            for release in cache.get("releases") or []:
                tag = release.get("tag") or ""
                if not tag:
                    continue
                upsert_release(
                    conn,
                    repo_id,
                    tag,
                    body=release.get("body") or "",
                    prerelease=bool(release.get("prerelease") or release.get("isPrerelease") or release.get("isPreRelease")),
                    published_at=release.get("dateReleased") or release.get("published_at") or release.get("publishedAt") or "",
                    fetched_at=fetched_at,
                    name=release.get("name") or "",
                )
                release_count += 1

        pruned = prune_stale(conn, index)

        sync_changes(conn, data_dir, imported_at)

        for name, filename in (
            ("core", "core.json"),
            ("stats", "stats.json"),
            ("app_cache", os.path.join("state", "app_cache.json")),
            ("repo_cache", os.path.join("state", "repo_cache.json")),
        ):
            value = load_json(os.path.join(data_dir, filename), None)
            if value is not None:
                set_snapshot(conn, name, value)

        core = load_json(os.path.join(data_dir, "core.json"), {}) or {}
        last_run = load_json(os.path.join(data_dir, "state", "last_run.json"), {}) or {}
        generated_at = (
            last_run.get("completed_at")
            or last_run.get("lastChecked")
            or core.get("lastChecked")
            or imported_at
        )
        digest = hashlib.sha256()
        for key in sorted(index):
            entry = index[key]
            digest.update(f"{key}|{entry.get('version', '')}|{entry.get('release_tag', '')}|".encode("utf-8"))
        digest.update(f"|releases={release_count}|patches={patch_count}".encode("utf-8"))
        for filename in ("changes.json", "changelog.json"):
            value = load_json(os.path.join(data_dir, filename), None)
            if value is not None:
                digest.update(f"|{filename}=".encode("utf-8"))
                digest.update(json_dumps(value).encode("utf-8"))
        data_version = digest.hexdigest()[:16]

        set_meta(conn, "schema_version", "1")
        set_meta(conn, "source", "legacy-import")
        set_meta(conn, "imported_at", imported_at)
        set_meta(conn, "generated_at", generated_at)
        set_meta(conn, "last_success_at", generated_at)
        set_meta(conn, "data_version", data_version)
        set_meta(conn, "bundle_count", str(bundle_count))
        set_meta(conn, "repo_count", str(conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]))
        set_meta(conn, "app_count", str(len(app_packages)))
        set_meta(conn, "bundle_app_count", str(bundle_app_count))
        set_meta(conn, "patch_count", str(patch_count))
        release_count = conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
        set_meta(conn, "release_count", str(release_count))
        set_meta(conn, "latest_count", str(latest_count))

        stats = {
            "bundles": bundle_count,
            "repos": conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0],
            "apps": len(app_packages),
            "bundle_apps": bundle_app_count,
            "patches": patch_count,
            "releases": release_count,
            "latest": latest_count,
            "changelog_days": len(changelog),
            "missing_detail_files": len(missing_records),
            "pruned": pruned,
            "data_version": data_version,
        }
        conn.execute(
            "INSERT INTO jobs(started_at, finished_at, status, stats_json) VALUES (?, ?, 'ok', ?)",
            (imported_at, utc_now(), json_dumps(stats)),
        )

    # Outside the transaction: VACUUM cannot run in one. Returns the prune
    # deletions' freelist pages to the OS (self-limiting, lock-tolerant).
    vacuum_if_needed(conn)
    counts = table_counts(conn)
    conn.close()
    return {
        "db": db_path,
        "data_version": data_version,
        "counts": counts,
        "stats": stats,
        "pruned": pruned,
        "missing_records": missing_records,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import legacy Fresh Morphe JSON into SQLite")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--reset", action="store_true", help="delete the existing DB before importing")
    parser.add_argument("--json", action="store_true", help="print the import summary as JSON")
    args = parser.parse_args(argv)

    result = import_data(args.db, args.data_dir, reset=args.reset)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"[import] wrote {result['db']} (data_version {result['data_version']})")
        print("[import] counts:", result["counts"])
        if any(result["pruned"].values()):
            print("[import] pruned stale rows:", result["pruned"])
        if result["missing_records"]:
            print(f"[import] warning: {len(result['missing_records'])} records skipped")
            for key in result["missing_records"][:10]:
                print("  -", key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
