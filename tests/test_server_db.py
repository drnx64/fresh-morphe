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
    # A fresh import has nothing stale: prune only removes rows that left the
    # index after the DB was built.
    assert result["pruned"] == {"bundles": 0, "apps": 0, "repos": 0}


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
        for key, (source_key, index_entry) in rows.items():
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
            # The importer flags a bundle prerelease when *either* source says
            # so; on Windows the two case-variant index keys share one physical
            # file, so the record alone can disagree with its index entry.
            assert got["isPreRelease"] == bool(
                expected.get("isPreRelease") or index_entry.get("isPreRelease")
            ), key
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
        rows = conn.execute("SELECT package FROM apps_fts WHERE apps_fts MATCH 'nuvio' LIMIT 5").fetchall()
        assert rows
        # patches_fts is gone for good: nothing queried it (patch search is
        # LIKE '%..%', whose leading wildcard cannot use an FTS index) yet its
        # token index cost ~15 MB. Its shadow tables and triggers go with it.
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert not any(name.startswith("patches_fts") for name in names)
        assert "patches_name_idx" not in names
        assert not {"patches_ai", "patches_ad", "patches_au"} & names
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
    # Re-importing an index that still matches the DB prunes nothing.
    assert second["pruned"] == {"bundles": 0, "apps": 0, "repos": 0}


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


def _write_mini_data(data_dir, index, details, extra_state=None):
    """A minimal pipeline data dir: one live bundle, optional state files."""
    os.makedirs(os.path.join(data_dir, "bundles"), exist_ok=True)
    os.makedirs(os.path.join(data_dir, "state"), exist_ok=True)
    with open(os.path.join(data_dir, "bundles", "_index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh)
    for key, record in details.items():
        path = os.path.join(data_dir, "bundles", key.replace(":", "_") + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
    for filename, value in (extra_state or {}).items():
        with open(os.path.join(data_dir, "state", filename), "w", encoding="utf-8") as fh:
            json.dump(value, fh)


def _mini_bundle(repo_url="https://github.com/live/repo"):
    entry = {
        "bundle": "live",
        "channel": "stable",
        "version": "1.0",
        "apps": [],
        "repo_url": repo_url,
    }
    return {"live:stable": entry}, {"live:stable": dict(entry)}


def test_prune_removes_stale_bundles_and_theirs(tmp_path):
    """Rows that leave `_index.json` die on the next import.

    The bundle goes first, then everything only it claimed: its patches (and
    targets), its bundle_apps links, the app left orphaned, and the repo —
    with its releases — that lost its last bundle. This is the phone's
    558-row / 27%-of-patches fix.
    """
    data_dir = str(tmp_path / "data")
    index, details = _mini_bundle()
    _write_mini_data(data_dir, index, details)
    db_path = str(tmp_path / "prune.db")
    import_data(db_path, data_dir, reset=True)

    conn = connect(db_path)
    try:
        repo_id = conn.execute(
            "INSERT INTO repos(url, url_key, slug) VALUES (?, ?, ?)",
            ("https://github.com/gone/repo", "https://github.com/gone/repo", "gone/repo"),
        ).lastrowid
        conn.execute(
            "INSERT INTO releases(repo_id, tag, published_at) VALUES (?, 'v1', '2026-01-01')",
            (repo_id,),
        )
        bundle_id = conn.execute(
            "INSERT INTO bundles(repo_id, bundle, channel) VALUES (?, 'gone', 'stable')",
            (repo_id,),
        ).lastrowid
        app_id = conn.execute(
            "INSERT INTO apps(package, name) VALUES ('com.gone', 'Gone')"
        ).lastrowid
        conn.execute(
            "INSERT INTO bundle_apps(bundle_id, app_id, name) VALUES (?, ?, 'Gone')",
            (bundle_id, app_id),
        )
        patch_id = conn.execute(
            "INSERT INTO patches(bundle_id, app_id, name) VALUES (?, ?, 'Stale patch')",
            (bundle_id, app_id),
        ).lastrowid
        conn.execute(
            "INSERT INTO patch_targets(patch_id, version) VALUES (?, '1.0')",
            (patch_id,),
        )
        conn.commit()
    finally:
        conn.close()

    result = import_data(db_path, data_dir, reset=False)

    assert result["pruned"] == {"bundles": 1, "apps": 1, "repos": 1}
    conn = connect(db_path, readonly=True)
    try:
        probes = {
            "stale bundle": ("SELECT COUNT(*) FROM bundles WHERE id = ?", (bundle_id,)),
            "its patch": ("SELECT COUNT(*) FROM patches WHERE id = ?", (patch_id,)),
            "its targets": ("SELECT COUNT(*) FROM patch_targets WHERE patch_id = ?", (patch_id,)),
            "its app link": ("SELECT COUNT(*) FROM bundle_apps WHERE bundle_id = ?", (bundle_id,)),
            "its orphaned app": ("SELECT COUNT(*) FROM apps WHERE id = ?", (app_id,)),
            "its repo": ("SELECT COUNT(*) FROM repos WHERE id = ?", (repo_id,)),
            "its releases": ("SELECT COUNT(*) FROM releases WHERE repo_id = ?", (repo_id,)),
        }
        for label, (sql, params) in probes.items():
            assert conn.execute(sql, params).fetchone()[0] == 0, label
        # The live bundle and its repo are untouched.
        assert conn.execute(
            "SELECT COUNT(*) FROM bundles WHERE bundle = 'live' AND channel = 'stable'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM repos WHERE url_key = 'https://github.com/live/repo'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_prune_spares_worker_tracked_repos(tmp_path):
    """Repos the worker tracks in `repo_sources` are exempt from pruning.

    The worker feeds those rows from the awesome-morphe mirror — they are not
    in `_index.json` at all — so deleting them would fight a writer that owns
    them. An untracked repo with no bundles still goes.
    """
    data_dir = str(tmp_path / "data")
    index, details = _mini_bundle()
    _write_mini_data(data_dir, index, details)
    db_path = str(tmp_path / "worker.db")
    import_data(db_path, data_dir, reset=True)

    conn = connect(db_path)
    try:
        # (a) tracked repo whose bundle left the index
        tracked_id = conn.execute(
            "INSERT INTO repos(url, url_key, slug) VALUES (?, ?, ?)",
            ("https://github.com/tracked/one", "https://github.com/tracked/one", "tracked/one"),
        ).lastrowid
        conn.execute(
            "INSERT INTO repo_sources(url_key, branch, hash) VALUES (?, 'main', 'abc')",
            ("https://github.com/tracked/one",),
        )
        tracked_bundle_id = conn.execute(
            "INSERT INTO bundles(repo_id, bundle, channel) VALUES (?, 'tracked-one', 'stable')",
            (tracked_id,),
        ).lastrowid
        # (b) tracked repo with no bundles at all
        tracked_two_id = conn.execute(
            "INSERT INTO repos(url, url_key, slug) VALUES (?, ?, ?)",
            ("https://github.com/tracked/two", "https://github.com/tracked/two", "tracked/two"),
        ).lastrowid
        conn.execute(
            "INSERT INTO repo_sources(url_key, branch, hash) VALUES (?, 'dev', 'def')",
            ("https://github.com/tracked/two",),
        )
        # (c) untracked repo with no bundles — e.g. left over from an older run
        dead_id = conn.execute(
            "INSERT INTO repos(url, url_key, slug) VALUES (?, ?, ?)",
            ("https://github.com/dead/repo", "https://github.com/dead/repo", "dead/repo"),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    result = import_data(db_path, data_dir, reset=False)

    assert result["pruned"] == {"bundles": 0, "apps": 0, "repos": 1}
    conn = connect(db_path, readonly=True)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM bundles WHERE id = ?", (tracked_bundle_id,)
        ).fetchone()[0] == 1
        for repo_id in (tracked_id, tracked_two_id):
            assert conn.execute(
                "SELECT COUNT(*) FROM repos WHERE id = ?", (repo_id,)
            ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM repos WHERE id = ?", (dead_id,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_release_cache_cannot_recreate_missing_repos(tmp_path):
    """The release loop annotates existing repos only.

    It used to call `ensure_repo`, re-creating repos that had left the index
    on every run — rows prune would delete again in the same transaction. A
    repo pinned in `repo_sources` (exempt from prune) makes the difference
    observable: nothing may conjure it here.
    """
    data_dir = str(tmp_path / "data")
    index, details = _mini_bundle()
    _write_mini_data(
        data_dir,
        index,
        details,
        extra_state={
            "release_cache.json": {
                "https://github.com/live/repo": {
                    "fetched_at": 1.0,
                    "releases": [
                        {"tag": "v9", "body": "notes", "dateReleased": "2026-01-01"}
                    ],
                },
                "https://github.com/absent/repo": {
                    "fetched_at": 1.0,
                    "releases": [
                        {"tag": "v1", "body": "stale", "dateReleased": "2026-01-01"}
                    ],
                },
            }
        },
    )
    db_path = str(tmp_path / "release-cache.db")
    import_data(db_path, data_dir, reset=True)

    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO repo_sources(url_key, branch, hash) VALUES ('https://github.com/absent/repo', 'main', 'abc')"
        )
        conn.commit()
    finally:
        conn.close()

    import_data(db_path, data_dir, reset=False)

    conn = connect(db_path, readonly=True)
    try:
        keys = {row[0] for row in conn.execute("SELECT url_key FROM repos")}
        assert keys == {"https://github.com/live/repo"}
        # The live repo still gets its note from the cache.
        assert conn.execute(
            "SELECT COUNT(*) FROM releases r JOIN repos rp ON rp.id = r.repo_id "
            "WHERE rp.url_key = 'https://github.com/live/repo' AND r.tag = 'v9'"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM releases WHERE tag = 'v1'").fetchone()[0] == 0
    finally:
        conn.close()


def test_vacuum_only_runs_when_the_freelist_is_big(tmp_path):
    """Prune's freed pages are returned to the OS — but only when it pays.

    The phone DB has no auto_vacuum, so deletions leave ~14 MB on the
    freelist. The guard makes vacuuming self-limiting: below the threshold
    (or the min page count, or while locked) it must do nothing.
    """
    from server.import_legacy import vacuum_if_needed

    path = tmp_path / "vacuum.db"
    init_db(str(path))
    conn = connect(str(path))
    try:
        payload = "x" * 300
        for i in range(4000):
            conn.execute(
                "INSERT INTO jobs(started_at, status, stats_json) VALUES (?, 'ok', ?)",
                (f"2026-10-{i:05d}", payload),
            )
        conn.commit()
        conn.execute("DELETE FROM jobs")
        conn.commit()
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        assert free > 64 and free > pages * 0.12, "fixture must exceed the default guard"

        # Skipped: below min_pages, then below threshold. Freelist untouched.
        assert vacuum_if_needed(conn, threshold=0.12, min_pages=free + 1) == 0
        assert vacuum_if_needed(conn, threshold=1.0, min_pages=0) == 0
        assert conn.execute("PRAGMA freelist_count").fetchone()[0] == free

        # Fires: reports the reclaimed page count and empties the freelist.
        assert vacuum_if_needed(conn, threshold=0.12, min_pages=64) == free
        assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
        # Now near-empty, the default guard skips it again on the next import.
        assert vacuum_if_needed(conn) == 0
    finally:
        conn.close()


def test_init_db_drops_legacy_patches_fts_and_name_index(tmp_path):
    """Older DBs carry a patches_fts token index nothing ever queried.

    schema.sql stopped creating it, but `CREATE ... IF NOT EXISTS` never
    removes anything — so `_migrate()` drops the triggers first (they write
    INTO the FTS table), then the table and the dead patches(name) index.
    """
    path = tmp_path / "legacy-fts.db"
    conn = connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE patches (
              id INTEGER PRIMARY KEY,
              bundle_id INTEGER NOT NULL,
              app_id INTEGER NOT NULL,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE patch_targets (
              patch_id INTEGER NOT NULL,
              version TEXT NOT NULL DEFAULT '',
              version_code TEXT NOT NULL DEFAULT '',
              min_sdk INTEGER,
              experimental INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (patch_id, version, version_code)
            );
            CREATE INDEX patches_name_idx ON patches(name);
            CREATE VIRTUAL TABLE patches_fts USING fts5(
              name, description, content='patches', content_rowid='id'
            );
            CREATE TRIGGER patches_ai AFTER INSERT ON patches BEGIN
              INSERT INTO patches_fts(rowid, name, description)
              VALUES (new.id, new.name, new.description);
            END;
            CREATE TRIGGER patches_ad AFTER DELETE ON patches BEGIN
              INSERT INTO patches_fts(patches_fts, rowid, name, description)
              VALUES ('delete', old.id, old.name, old.description);
            END;
            """
        )
        conn.commit()
    finally:
        conn.close()

    init_db(str(path))

    conn = connect(str(path), readonly=True)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert not any(name.startswith("patches_fts") for name in names)
        assert "patches_name_idx" not in names
        assert not {"patches_ai", "patches_ad", "patches_au"} & names
        # The real tables and the kept apps_fts survive, still in sync.
        assert "patches" in names and "patch_targets" in names
        assert "apps_fts" in names and "apps_ai" in names
    finally:
        conn.close()
