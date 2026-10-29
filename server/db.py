"""SQLite helpers for the phone-hosted Fresh Morphe service."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from server.config import DB_PATH, SCHEMA_PATH


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def connect(path=None, readonly=False):
    """Open a configured SQLite connection.

    API readers use mode=ro so a bug can never mutate the database. WAL is
    enabled by the importer/writer; readers inherit the journal mode.
    """
    path = os.path.abspath(path or DB_PATH)
    if readonly:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA cache_size = -16000")
    return conn


def init_db(path=None):
    path = os.path.abspath(path or DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
            schema = fh.read()
        conn.executescript(schema)
        conn.commit()
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)",
            (utc_now(),),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def get_snapshot(conn, name, default=None):
    row = conn.execute("SELECT json FROM snapshots WHERE name = ?", (name,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["json"])
    except (TypeError, ValueError):
        return default


def set_snapshot(conn, name, value):
    conn.execute(
        "INSERT INTO snapshots(name, json) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET json = excluded.json",
        (name, json_dumps(value)),
    )


def table_counts(conn):
    tables = (
        "repos",
        "bundles",
        "apps",
        "bundle_apps",
        "patches",
        "patch_targets",
        "releases",
        "changes",
        "jobs",
        "repo_sources",
    )
    return {table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] for table in tables}


def integrity_check(conn):
    return conn.execute("PRAGMA integrity_check").fetchone()[0]


def close_db(conn):
    try:
        conn.close()
    except sqlite3.Error:
        pass
