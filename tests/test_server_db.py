"""Integration tests for the SQLite migration and read-only API builders."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from server_helpers import deduped_bundles, expected_counts, load_source_index

from server.api import app_detail, build_detail, build_index, build_releases, search_apps
from server.config import DATA_DIR
from server.db import connect, get_snapshot, init_db, set_snapshot, table_counts
from server.import_legacy import import_data


@pytest.fixture(scope="module")
def migrated(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("morphe-db") / "morphe.db"
    result = import_data(str(db_path), DATA_DIR, reset=True)
    return db_path, result


@pytest.fixture(scope="module")
def source_index():
    return load_source_index(DATA_DIR)


def test_import_counts(migrated, source_index):
    _, result = migrated
    counts = result["counts"]
    with open(os.path.join(DATA_DIR, "changelog.json"), encoding="utf-8") as fh:
        changelog = json.load(fh)
    with open(os.path.join(DATA_DIR, "core.json"), encoding="utf-8") as fh:
        core = json.load(fh)
    with open(os.path.join(DATA_DIR, "changes.json"), encoding="utf-8") as fh:
        changes = json.load(fh)
    expected_days = {day.get("date") for day in changelog if day.get("date")}
    if changes.get("affected_bundles"):
        expected_days.add(core.get("date"))
    expected = expected_counts(DATA_DIR, source_index)
    assert counts["bundles"] == expected["bundles"]
    assert result["stats"]["latest"] == expected["latest"]
    assert counts["bundle_apps"] == expected["bundle_apps"]
    assert counts["apps"] == expected["apps"]
    assert counts["patches"] == expected["patches"]
    assert counts["releases"] > 0
    assert counts["changes"] == len(expected_days)
    assert counts["repos"] > 0
    assert result["missing_records"] == []


def test_latest_is_preserved(migrated, source_index):
    db_path, _ = migrated
    expected = expected_counts(DATA_DIR, source_index)["latest"]
    conn = connect(str(db_path), readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM bundles WHERE channel = 'latest'").fetchone()[0] == expected
        row = conn.execute(
            "SELECT latest_origin FROM bundles WHERE bundle = 'santodan' AND channel = 'latest'"
        ).fetchone()
        assert row is not None
        assert row[0] == "legacy"
    finally:
        conn.close()


def test_index_matches_source(migrated, source_index):
    db_path, _ = migrated
    rows = deduped_bundles(source_index)
    conn = connect(str(db_path), readonly=True)
    try:
        api_index = build_index(conn)
        assert set(api_index) == set(rows)
        for key, (source_key, expected) in rows.items():
            path = os.path.join(DATA_DIR, "bundles", source_key.replace(":", "_") + ".json")
            with open(path, encoding="utf-8") as fh:
                detail = json.load(fh)
            packages = [
                app.get("package")
                for app in detail.get("apps") or []
                if app.get("package")
            ]
            got = api_index[key]
            assert got["bundle"] == expected["bundle"]
            assert got["channel"] == expected["channel"]
            assert got["version"] == expected.get("version", "")
            assert got["repo_url"].lower() == (
                detail.get("repo_url") or expected.get("repo_url", "")
            ).lower()
            assert got["release_tag"] == (
                detail.get("release_tag") or expected.get("release_tag", "")
            )
            assert got["release_date"] == (
                detail.get("release_date") or expected.get("release_date", "")
            )
            assert got["apps"] == packages
            assert got["app_count"] == len(packages)
    finally:
        conn.close()


def test_details_match_source(migrated, source_index):
    db_path, _ = migrated
    rows = deduped_bundles(source_index)
    conn = connect(str(db_path), readonly=True)
    try:
        for key, (source_key, _) in rows.items():
            bundle, channel = key.rsplit(":", 1)
            path = os.path.join(DATA_DIR, "bundles", source_key.replace(":", "_") + ".json")
            with open(path, encoding="utf-8") as fh:
                expected = json.load(fh)
            got = build_detail(conn, bundle, channel)
            assert got is not None, key
            assert got["apps"] == expected["apps"], key
            assert got["version"] == expected["version"]
            assert got["repo_url"].lower() == expected.get("repo_url", "").lower()
            assert got["fingerprint"] == expected.get("fingerprint", "")
            assert got["release_tag"] == expected.get("release_tag", "")
            assert got["release_date"] == expected.get("release_date", "")
            assert got["isPreRelease"] == bool(expected.get("isPreRelease"))
    finally:
        conn.close()


def test_releases_and_history(migrated):
    db_path, _ = migrated
    conn = connect(str(db_path), readonly=True)
    try:
        repo_id = conn.execute(
            "SELECT repo_id FROM bundles WHERE bundle = 'santodan' AND channel = 'stable'"
        ).fetchone()[0]
        releases = build_releases(conn, repo_id, limit=10)
        assert releases
        assert any(item["tag"] for item in releases)
        assert any(item["body"] for item in releases)
    finally:
        conn.close()


def test_app_and_search(migrated):
    db_path, _ = migrated
    conn = connect(str(db_path), readonly=True)
    try:
        detail = app_detail(conn, "com.nuvio.tv")
        assert detail is not None
        assert detail["name"]
        assert detail["bundles"]
        results = search_apps(conn, "nuvio", limit=10)
        assert any(row["package"] == "com.nuvio.tv" for row in results)
    finally:
        conn.close()


def test_fts_is_populated(migrated, source_index):
    db_path, _ = migrated
    expected = expected_counts(DATA_DIR, source_index)
    conn = connect(str(db_path), readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM apps_fts").fetchone()[0] == expected["apps"]
        assert conn.execute("SELECT COUNT(*) FROM patches_fts").fetchone()[0] == expected["patches"]
        rows = conn.execute("SELECT package FROM apps_fts WHERE apps_fts MATCH 'nuvio' LIMIT 5").fetchall()
        assert rows
    finally:
        conn.close()


def test_reimport_is_idempotent(migrated):
    db_path, first = migrated
    second = import_data(str(db_path), DATA_DIR, reset=False)
    assert second["counts"]["bundles"] == first["counts"]["bundles"]
    assert second["counts"]["repos"] == first["counts"]["repos"]
    assert second["counts"]["apps"] == first["counts"]["apps"]
    assert second["counts"]["bundle_apps"] == first["counts"]["bundle_apps"]
    assert second["counts"]["patches"] == first["counts"]["patches"]
    assert second["counts"]["releases"] == first["counts"]["releases"]
    assert second["counts"]["changes"] == first["counts"]["changes"]


def test_today_changes_are_stored(migrated):
    db_path, _ = migrated
    with open(os.path.join(DATA_DIR, "core.json"), encoding="utf-8") as fh:
        core = json.load(fh)
    with open(os.path.join(DATA_DIR, "changes.json"), encoding="utf-8") as fh:
        changes = json.load(fh)
    conn = connect(str(db_path), readonly=True)
    try:
        row = conn.execute(
            "SELECT payload_json FROM changes WHERE date = ?", (core.get("date"),)
        ).fetchone()
        if changes.get("affected_bundles"):
            assert row is not None
            assert json.loads(row["payload_json"])["affected_bundles"] == changes["affected_bundles"]
        snapshot = conn.execute(
            "SELECT json FROM snapshots WHERE name = 'changes'"
        ).fetchone()
        payload = json.loads(snapshot["json"])
        # The snapshot mirrors changes.json plus a `date` key, and falls back to
        # the newest finalized day when today has no entries yet.
        assert payload["affected_bundles"] == changes.get("affected_bundles", [])
        if changes.get("date"):
            assert payload["date"] == changes["date"]
        else:
            assert payload["date"] == core.get("date")
        if not changes.get("affected_bundles"):
            assert payload.get("latest"), "quiet day should expose the newest real day"
    finally:
        conn.close()


def test_quiet_scan_keeps_the_days_changes(tmp_path):
    """A scan that finds nothing new must not blank "What's New".

    The worker used to snapshot only the current run's delta, so every quiet
    hourly run replaced the day's payload with an empty list.
    """
    from server.worker import merge_changes, today_change_payload

    path = tmp_path / "worker.db"
    init_db(str(path))
    conn = connect(str(path))
    try:
        first_scan = "2026-10-29T01:00:00Z"
        delta = [{"bundle": "alpha", "channel": "stable", "badge_type": "UPDATED", "apps": []}]
        with conn:
            merge_changes(conn, delta, first_scan)
            set_snapshot(conn, "changes", today_change_payload(conn, first_scan))
        assert get_snapshot(conn, "changes")["affected_bundles"] == delta

        quiet_scan = "2026-10-29T02:00:00Z"
        with conn:
            set_snapshot(conn, "changes", today_change_payload(conn, quiet_scan))

        payload = get_snapshot(conn, "changes")
        assert payload["date"] == "2026-10-29"
        assert [entry["bundle"] for entry in payload["affected_bundles"]] == ["alpha"]
    finally:
        conn.close()


def test_new_utc_day_starts_with_an_empty_payload(tmp_path):
    """The day key is UTC, matching the pipeline's rollover boundary."""
    from server.worker import merge_changes, today_change_payload

    path = tmp_path / "rollover.db"
    init_db(str(path))
    conn = connect(str(path))
    try:
        with conn:
            merge_changes(
                conn,
                [{"bundle": "alpha", "channel": "stable", "badge_type": "UPDATED", "apps": []}],
                "2026-10-29T23:59:00Z",
            )
        next_day = today_change_payload(conn, "2026-10-30T00:05:00Z")
        assert next_day == {"date": "2026-10-30", "affected_bundles": []}
    finally:
        conn.close()


def test_readonly_connection_cannot_write(migrated):
    db_path, _ = migrated
    conn = connect(str(db_path), readonly=True)
    try:
        with pytest.raises(Exception):
            conn.execute("CREATE TABLE should_not_exist(x)")
    finally:
        conn.close()


def test_schema_init_is_idempotent(tmp_path):
    path = tmp_path / "empty.db"
    init_db(str(path))
    init_db(str(path))
    conn = connect(str(path), readonly=True)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "1"
        assert table_counts(conn)["bundles"] == 0
    finally:
        conn.close()
