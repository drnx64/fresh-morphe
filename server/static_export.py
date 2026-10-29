"""Export a compact read-only snapshot for GitHub Pages / CDN fallback."""
import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from server.api import build_detail, build_index
from server.config import DB_PATH, EXPORT_DIR
from server.db import connect, get_meta, get_snapshot, table_counts, utc_now


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def export_db(db_path=DB_PATH, out_dir=EXPORT_DIR):
    db_path = os.path.abspath(db_path)
    out_dir = os.path.abspath(out_dir)
    if not os.path.exists(db_path):
        raise SystemExit(f"database not found: {db_path}")

    conn = connect(db_path, readonly=True)
    try:
        index = build_index(conn)
        meta = {
            "schema_version": int(get_meta(conn, "schema_version", "1")),
            "data_version": get_meta(conn, "data_version", ""),
            "generated_at": get_meta(conn, "generated_at", ""),
            "last_success_at": get_meta(conn, "last_success_at", ""),
            "counts": table_counts(conn),
            "source": "sqlite-export",
            "exported_at": utc_now(),
        }
        snapshots = {
            "stats": get_snapshot(conn, "stats", {}),
            "core": get_snapshot(conn, "core", {}),
            "changes": get_snapshot(conn, "changes", {}),
            "app_cache": get_snapshot(conn, "app_cache", {}),
            "repo_cache": get_snapshot(conn, "repo_cache", {}),
        }
        changelog_rows = conn.execute(
            "SELECT payload_json FROM changes ORDER BY date DESC"
        ).fetchall()
        changelog = [json.loads(row["payload_json"]) for row in changelog_rows]
        details = {}
        for key in index:
            bundle, channel = key.rsplit(":", 1)
            record = build_detail(conn, bundle, channel)
            if record:
                details[key] = record
    finally:
        conn.close()

    parent = os.path.dirname(out_dir)
    Path(parent).mkdir(parents=True, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix=".fresh-export-", dir=parent)
    try:
        write_json(os.path.join(tmp_dir, "meta.json"), meta)
        write_json(os.path.join(tmp_dir, "index.json"), index)
        write_json(os.path.join(tmp_dir, "stats.json"), snapshots["stats"])
        write_json(os.path.join(tmp_dir, "core.json"), snapshots["core"])
        write_json(os.path.join(tmp_dir, "changes.json"), snapshots["changes"])
        write_json(os.path.join(tmp_dir, "changelog.json"), changelog)
        write_json(os.path.join(tmp_dir, "app_cache.json"), snapshots["app_cache"])
        write_json(os.path.join(tmp_dir, "repo_cache.json"), snapshots["repo_cache"])
        write_json(os.path.join(tmp_dir, "bundles", "_index.json"), index)
        for key, record in details.items():
            write_json(
                os.path.join(tmp_dir, "bundles", key.replace(":", "_") + ".json"),
                record,
            )

        if os.path.exists(out_dir):
            shutil.rmtree(out_dir)
        os.replace(tmp_dir, out_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return {
        "out": out_dir,
        "bundles": len(details),
        "changelog_days": len(changelog),
        "data_version": meta["data_version"],
        "bytes": sum(
            os.path.getsize(os.path.join(root, name))
            for root, _, names in os.walk(out_dir)
            for name in names
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export SQLite data as static JSON fallback")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--out", default=EXPORT_DIR)
    args = parser.parse_args(argv)
    result = export_db(args.db, args.out)
    print(
        f"[export] {result['out']} · {result['bundles']} bundles · "
        f"{result['changelog_days']} changelog days · {result['bytes']} bytes · "
        f"version {result['data_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
