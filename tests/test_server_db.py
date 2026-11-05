"""Integration tests for the SQLite migration and read-only API builders."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from server_helpers import deduped_bundles, deduped_record, expected_counts, load_source_index

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


def test_migrates_old_database_without_developer(tmp_path):
    """An existing phone DB predates the developer column; init_db adds it."""
    db_path = tmp_path / "old.db"
    conn = connect(str(db_path))
    try:
        conn.execute("CREATE TABLE apps (id INTEGER PRIMARY KEY, package TEXT, name TEXT, icon_url TEXT)")
        conn.execute("INSERT INTO apps(package, name, icon_url) VALUES ('com.x', 'X', '')")
        conn.commit()
    finally:
        conn.close()

    init_db(str(db_path))

    conn = connect(str(db_path), readonly=True)
    try:
        row = conn.execute("SELECT developer FROM apps WHERE package = 'com.x'").fetchone()
        assert row["developer"] == ""
    finally:
        conn.close()


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
            # The API adds `developer` (publisher), which bundle source files
            # only carry once the phone has enriched them. Everything else must
            # match the source record exactly — after the importer's dedupe of
            # repeated packages/patch names.
            expected = deduped_record(expected)
            without_developer = lambda app: {k: v for k, v in app.items() if k != "developer"}
            assert [without_developer(a) for a in got["apps"]] == [
                without_developer(a) for a in expected["apps"]
            ], key
            assert all(app["developer"] for app in got["apps"]), key
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


def _make_repo(conn):
    cur = conn.execute(
        "INSERT INTO repos(url, url_key, slug) VALUES (?, ?, ?)",
        ("https://github.com/x/y", "github.com/x/y", "x/y"),
    )
    return cur.lastrowid


def test_init_db_purges_duplicate_patch_rows(tmp_path):
    """A legacy DB with repeated (bundle, app, name) patches is cleaned and
    then protected by the unique index.

    The duplicates used to multiply `patch_targets`; the survivor keeps its
    targets and the losers' go with them.
    """
    path = tmp_path / "dupes.db"
    init_db(str(path))
    conn = connect(str(path))
    try:
        conn.execute("DROP INDEX patches_bundle_app_name_idx")
        repo_id = _make_repo(conn)
        cur = conn.execute(
            "INSERT INTO bundles(repo_id, bundle, channel) VALUES (?, 'x', 'stable')",
            (repo_id,),
        )
        bundle_id = cur.lastrowid
        cur = conn.execute("INSERT INTO apps(package, name) VALUES ('com.x', 'X')")
        app_id = cur.lastrowid
        target_ids = []
        for _ in range(3):
            cur = conn.execute(
                "INSERT INTO patches(bundle_id, app_id, name) VALUES (?, ?, 'Patch')",
                (bundle_id, app_id),
            )
            patch_id = cur.lastrowid
            target_ids.append(patch_id)
            for version in ("1.0", "2.0"):
                conn.execute(
                    "INSERT INTO patch_targets(patch_id, version) VALUES (?, ?)",
                    (patch_id, version),
                )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM patches").fetchone()[0] == 3
        conn.close()

        init_db(str(path))

        conn = connect(str(path), readonly=True)
        assert conn.execute("SELECT COUNT(*) FROM patches").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM patch_targets").fetchone()[0] == 2
        # The index is in place: a second duplicate insert is refused.
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO patches(bundle_id, app_id, name) VALUES (?, ?, 'Patch')",
                (bundle_id, app_id),
            )
    finally:
        conn.close()


def test_release_history_is_capped_per_repo(tmp_path):
    from server.db import RELEASE_KEEP, prune_releases

    path = tmp_path / "releases.db"
    init_db(str(path))
    conn = connect(str(path))
    try:
        repo_id = _make_repo(conn)
        for day in range(1, 9):
            conn.execute(
                "INSERT INTO releases(repo_id, tag, published_at) VALUES (?, ?, ?)",
                (repo_id, f"v{day}", f"2026-10-{day:02d}"),
            )
        conn.commit()

        prune_releases(conn)
        rows = conn.execute(
            "SELECT tag FROM releases WHERE repo_id = ? ORDER BY published_at DESC",
            (repo_id,),
        ).fetchall()
        assert [r["tag"] for r in rows] == ["v8", "v7", "v6"][:RELEASE_KEEP]

        # An older tag arriving later cannot push out the newest three.
        from server.import_legacy import upsert_release

        upsert_release(conn, repo_id, "v1", published_at="2026-09-01")
        assert conn.execute(
            "SELECT COUNT(*) FROM releases WHERE repo_id = ?", (repo_id,)
        ).fetchone()[0] == RELEASE_KEEP
        # ... but a newer one lands and the oldest falls off.
        upsert_release(conn, repo_id, "v9", published_at="2026-11-01")
        tags = [
            r["tag"]
            for r in conn.execute(
                "SELECT tag FROM releases WHERE repo_id = ? ORDER BY published_at DESC",
                (repo_id,),
            )
        ]
        assert tags == ["v9", "v8", "v7"]
    finally:
        conn.close()


def test_upsert_bundle_skips_repeated_packages_and_patches(tmp_path):
    """A source record that repeats a package/patch stores each row once."""
    from server.import_legacy import upsert_bundle

    path = tmp_path / "record.db"
    init_db(str(path))
    conn = connect(str(path))
    try:
        repo_id = _make_repo(conn)
        app = {
            "package": "com.x",
            "app_name": "X",
            "patches": [
                {"name": "Patch", "targets": [{"version": "1.0"}]},
                {"name": "Patch", "targets": [{"version": "1.0"}]},
                {"name": "Other", "targets": [{"version": "1.0"}]},
            ],
        }
        record = {"apps": [app, dict(app)], "version": "1.0"}
        bundle_id = upsert_bundle(
            conn, repo_id, "x", "stable", record, {"version": "1.0"}, "2026-11-05T00:00:00Z"
        )
        conn.commit()

        assert conn.execute(
            "SELECT COUNT(*) FROM bundle_apps WHERE bundle_id = ?", (bundle_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM patches WHERE bundle_id = ?", (bundle_id,)
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM patch_targets t JOIN patches p ON p.id = t.patch_id "
            "WHERE p.bundle_id = ?",
            (bundle_id,),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT app_count FROM bundles WHERE id = ?", (bundle_id,)
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_build_releases_is_clamped_to_stored_history(migrated):
    from server.db import RELEASE_KEEP

    db_path, _ = migrated
    conn = connect(str(db_path), readonly=True)
    try:
        repo_id = conn.execute("SELECT repo_id FROM releases LIMIT 1").fetchone()[0]
        assert len(build_releases(conn, repo_id, limit=200)) <= RELEASE_KEEP
    finally:
        conn.close()
