"""Tests for mirroring current-day and finalized changes into SQLite."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.db import connect, get_meta, get_snapshot, init_db
from server.import_legacy import sync_changes


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle)


def make_data_dir(tmp_path, date, changed, last_checked):
    data_dir = tmp_path / "data"
    write_json(data_dir / "core.json", {"date": date, "lastChecked": last_checked})
    write_json(data_dir / "changes.json", {"affected_bundles": changed})
    write_json(data_dir / "changelog.json", [
        {"date": "2026-10-25", "lastChecked": "2026-10-25T23:00:00Z", "affected_bundles": []},
    ])
    write_json(data_dir / "state" / "last_run.json", {
        "lastChecked": last_checked,
        "completed_at": last_checked,
    })
    return data_dir


def open_db(tmp_path):
    path = tmp_path / "morphe.db"
    init_db(str(path))
    return connect(str(path))


def test_sync_changes_inserts_today_and_snapshot(tmp_path):
    changed = [{"bundle": "foo", "channel": "stable", "badge_type": "UPDATED"}]
    data_dir = make_data_dir(tmp_path, "2026-10-26", changed, "2026-10-26T12:00:00Z")
    conn = open_db(tmp_path)
    try:
        sync_changes(conn, str(data_dir), "2026-10-26T12:01:00Z")
        conn.commit()

        rows = conn.execute("SELECT date, last_checked, payload_json FROM changes ORDER BY date").fetchall()
        assert [row["date"] for row in rows] == ["2026-10-25", "2026-10-26"]
        today = json.loads(rows[1]["payload_json"])
        assert today["date"] == "2026-10-26"
        assert today["affected_bundles"] == changed
        assert get_snapshot(conn, "changes") == {"affected_bundles": changed}
        assert get_meta(conn, "last_checked") == "2026-10-26T12:00:00Z"
        assert get_meta(conn, "last_success_at") == "2026-10-26T12:00:00Z"
    finally:
        conn.close()


def test_sync_changes_same_day_upserts(tmp_path):
    first = [{"bundle": "foo", "channel": "stable", "badge_type": "UPDATED"}]
    second = first + [{"bundle": "bar", "channel": "dev", "badge_type": "NEW BUNDLE"}]
    data_dir = make_data_dir(tmp_path, "2026-10-26", first, "2026-10-26T12:00:00Z")
    conn = open_db(tmp_path)
    try:
        sync_changes(conn, str(data_dir), "2026-10-26T12:01:00Z")
        write_json(data_dir / "core.json", {
            "date": "2026-10-26",
            "lastChecked": "2026-10-26T13:00:00Z",
        })
        write_json(data_dir / "changes.json", {"affected_bundles": second})
        write_json(data_dir / "state" / "last_run.json", {
            "lastChecked": "2026-10-26T13:00:00Z",
            "completed_at": "2026-10-26T13:00:00Z",
        })
        sync_changes(conn, str(data_dir), "2026-10-26T13:01:00Z")
        conn.commit()

        rows = conn.execute("SELECT payload_json FROM changes WHERE date = '2026-10-26'").fetchall()
        assert len(rows) == 1
        assert len(json.loads(rows[0]["payload_json"])["affected_bundles"]) == 2
        assert get_snapshot(conn, "changes") == {"affected_bundles": second}
        assert get_meta(conn, "last_checked") == "2026-10-26T13:00:00Z"
    finally:
        conn.close()


def test_sync_changes_new_day_resets_snapshot(tmp_path):
    changed = [{"bundle": "foo", "channel": "stable", "badge_type": "UPDATED"}]
    data_dir = make_data_dir(tmp_path, "2026-10-26", changed, "2026-10-26T12:00:00Z")
    conn = open_db(tmp_path)
    try:
        sync_changes(conn, str(data_dir), "2026-10-26T12:01:00Z")
        write_json(data_dir / "core.json", {
            "date": "2026-10-27",
            "lastChecked": "2026-10-27T00:30:00Z",
        })
        write_json(data_dir / "changes.json", {"affected_bundles": []})
        write_json(data_dir / "state" / "last_run.json", {
            "lastChecked": "2026-10-27T00:30:00Z",
            "completed_at": "2026-10-27T00:30:00Z",
        })
        sync_changes(conn, str(data_dir), "2026-10-27T00:31:00Z")
        conn.commit()

        assert get_snapshot(conn, "changes") == {"affected_bundles": []}
        assert conn.execute("SELECT COUNT(*) FROM changes WHERE date = '2026-10-27'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM changes WHERE date = '2026-10-26'").fetchone()[0] == 1
        assert get_meta(conn, "last_checked") == "2026-10-27T00:30:00Z"
    finally:
        conn.close()
