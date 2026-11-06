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


def _migrate(conn):
    """Add columns shipped after a database was first created.

    `schema.sql` only runs CREATE TABLE IF NOT EXISTS, so an existing phone DB
    keeps its old column set. Each entry here is idempotent: a column is added
    only when PRAGMA table_info proves it is missing. Objects schema.sql no
    longer creates are dropped the same way — DROP ... IF EXISTS is a no-op
    on databases that never had them.
    """
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(apps)").fetchall()
    }
    if "developer" not in columns:
        conn.execute("ALTER TABLE apps ADD COLUMN developer TEXT NOT NULL DEFAULT ''")

    # patches_fts was never queried (patch search is LIKE '%..%', which an FTS
    # index cannot serve) but its token index held ~15 MB; patches_name_idx was
    # equally dead. Triggers first — they write INTO patches_fts, so dropping
    # the table under them would leave them failing on every patch write.
    for trigger in ("patches_ai", "patches_ad", "patches_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    conn.execute("DROP TABLE IF EXISTS patches_fts")
    conn.execute("DROP INDEX IF EXISTS patches_name_idx")
    conn.commit()


# Newest N releases kept per repo. The UI asks for 30; the API clamps to
# RELEASE_KEEP, and nothing downstream needs more than a page of history.
RELEASE_KEEP = 3


def _table_exists(conn, name):
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def _dedupe_patches(conn):
    """Drop repeated (bundle, app, name) patch rows before the unique index.

    Older bundle records repeated a package or a patch, so `upsert_bundle`
    inserted the same patch — and its `patch_targets` — more than once. The
    unique index in schema.sql refuses to be created over duplicates, so this
    runs first on existing databases. The surviving row is the one carrying
    the most targets (the fully-populated copy); deleting it cascades the
    extra targets away with the losers.
    """
    if not _table_exists(conn, "patches"):
        return 0
    cur = conn.execute(
        """
        DELETE FROM patches WHERE id IN (
          SELECT id FROM (
            SELECT p.id, ROW_NUMBER() OVER (
              PARTITION BY p.bundle_id, p.app_id, p.name
              ORDER BY COALESCE(tc.n, 0) DESC, p.id DESC
            ) AS rn
            FROM patches p
            LEFT JOIN (
              SELECT patch_id, COUNT(*) AS n FROM patch_targets GROUP BY patch_id
            ) tc ON tc.patch_id = p.id
          ) WHERE rn > 1
        )
        """
    )
    return cur.rowcount


def prune_releases(conn, keep=RELEASE_KEEP, repo_id=None):
    """Keep only the newest `keep` releases per repo.

    `release_cache.json` holds up to 15 releases per repository, which grew the
    phone DB to thousands of mostly-unread bodies. Newest-first by publish date
    (tag as tie-break) is the same order the API serves.
    """
    if keep <= 0:
        return 0
    if not _table_exists(conn, "releases"):
        return 0
    where = "WHERE repo_id = ?" if repo_id is not None else ""
    params = (keep,) if repo_id is None else (repo_id, keep)
    cur = conn.execute(
        f"""
        DELETE FROM releases WHERE id IN (
          SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (
              PARTITION BY repo_id
              ORDER BY published_at DESC, tag DESC, id DESC
            ) AS rn
            FROM releases {where}
          ) WHERE rn > ?
        )
        """,
        params,
    )
    return cur.rowcount


def init_db(path=None):
    path = os.path.abspath(path or DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        # Clear legacy duplicates first: CREATE UNIQUE INDEX fails outright
        # over rows that violate it.
        _dedupe_patches(conn)
        prune_releases(conn)
        conn.commit()
        with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
            schema = fh.read()
        conn.executescript(schema)
        conn.commit()
        _migrate(conn)
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
