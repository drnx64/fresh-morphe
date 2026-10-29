"""Tests for the incremental SQLite worker."""
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.api import build_detail, build_index
from server.db import connect, get_meta, get_snapshot, init_db
from server.import_legacy import ensure_repo, upsert_bundle
from server.worker import Worker, choose_bundle_name, parse_patches_doc


APP = "com.example.app"
REPO = "https://github.com/example/foo-patches"
MAIN_SLUG = "github~example~foo-patches~main"
DEV_SLUG = "github~example~foo-patches~dev"


def patches_doc(version="1.2.3", patch_name="Example patch", description="Example description"):
    return {
        "version": version,
        "patches": [{
            "name": patch_name,
            "description": description,
            "use": True,
            "options": [{"key": "enable", "description": "Enable it"}],
            "compatiblePackages": [{
                "packageName": APP,
                "name": "Example App",
                "targets": [
                    {"version": "1.0.0", "versionCodes": [100, 101], "minSdk": 23},
                    {"version": "1.1.0"},
                ],
            }],
        }],
    }


def release_doc(version="1.2.3", description="Release notes"):
    return {
        "version": version,
        "description": description,
        "created_at": "2026-10-24T15:03:20",
        "download_url": f"https://github.com/example/foo-patches/releases/download/v{version}/patches-{version}.mpp",
    }


class FakeMirror:
    def __init__(self, docs):
        self.docs = dict(docs)
        self.calls = []
        self.fail_patch = False

    def __call__(self, url):
        self.calls.append(url)
        key = url.split("mirror.test/", 1)[-1]
        if self.fail_patch and "data/patches/" in key:
            raise RuntimeError("temporary mirror failure")
        if key not in self.docs:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return self.docs[key]


def make_mirror(repo=REPO, main_hash="main-1", dev_hash="dev-1"):
    slug_repo = repo
    slug_name = slug_repo.split("/")[-1]
    slug_owner = slug_repo.split("/")[-2]
    prefix = f"github~{slug_owner}~{slug_name}"
    docs = {
        "data/repos.json": {
            repo: {
                "name": "Example Patches",
                "description": "Example repository",
                "avatarUrl": "https://example.test/avatar.png",
                "stars": 42,
                "main": main_hash,
                "dev": dev_hash,
            },
        },
        f"data/patches/{prefix}~main.json": patches_doc(),
        f"data/patches/{prefix}~dev.json": patches_doc("1.3.0-dev.1"),
        f"data/bundles/{prefix}~main.json": release_doc(),
        f"data/bundles/{prefix}~dev.json": release_doc("1.3.0-dev.1", "Dev notes"),
    }
    return docs


def test_parse_patches_doc_expands_targets_and_options():
    apps = parse_patches_doc(patches_doc())
    assert len(apps) == 1
    app = apps[0]
    assert app["package"] == APP
    assert app["app_name"] == "Example App"
    assert len(app["patches"]) == 1
    patch = app["patches"][0]
    assert patch["use"] is True
    assert patch["compatible_versions"] == ["1.0.0", "1.1.0"]
    assert len(patch["targets"]) == 3
    assert patch["targets"][0]["version_code"] == "100"
    assert patch["targets"][0]["min_sdk"] == 23
    assert patch["options"] == [{"key": "enable", "description": "Enable it"}]


def test_parse_patches_doc_supports_dict_compatibility():
    doc = {
        "version": "1.0.0",
        "patches": [{
            "name": "Dict package patch",
            "use": True,
            "compatiblePackages": {
                "com.dict.app": ["1.0.0", "1.1.0"],
                "com.no.versions": None,
            },
            "options": [],
        }, {
            "name": "Compatibility patch",
            "use": True,
            "compatiblePackages": {"com.pretty.app": ["old"]},
            "compatibility": [{
                "packageName": "com.pretty.app",
                "name": "Pretty App",
                "targets": [{"version": "2.0.0", "experimental": True}],
            }],
            "options": [],
        }],
    }
    apps = {app["package"]: app for app in parse_patches_doc(doc)}
    assert apps["com.dict.app"]["patches"][0]["compatible_versions"] == ["1.0.0", "1.1.0"]
    assert apps["com.no.versions"]["patches"][0]["compatible_versions"] == []
    assert apps["com.pretty.app"]["app_name"] == "Pretty App"
    assert apps["com.pretty.app"]["patches"][0]["targets"] == [{
        "version": "2.0.0",
        "version_code": "",
        "min_sdk": None,
        "experimental": True,
    }]


def test_parse_patches_doc_supports_top_level_list():
    doc = [{
        "name": "List patch",
        "description": "From a list-format bundle",
        "use": True,
        "options": [],
        "compatiblePackages": [{
            "packageName": "com.list.app",
            "name": "List App",
            "versions": ["1.0.0"],
        }],
    }]
    apps = parse_patches_doc(doc)
    assert len(apps) == 1
    assert apps[0]["package"] == "com.list.app"
    assert apps[0]["patches"][0]["compatible_versions"] == ["1.0.0"]


def test_choose_bundle_name_prefers_existing_owner_alias(tmp_path):
    db = tmp_path / "names.db"
    init_db(str(db))
    conn = connect(str(db))
    try:
        with conn:
            repo_id = ensure_repo(conn, "https://github.com/MorpheApp/morphe-patches")
            for bundle in ("morpheapp", "morphe"):
                upsert_bundle(
                    conn, repo_id, bundle, "stable",
                    {"apps": [], "version": "1.0.0"},
                    {"version": "1.0.0"}, "2026-10-24T00:00:00Z",
                )
            target = type("Target", (), {"url": "https://github.com/MorpheApp/morphe-patches"})
            assert choose_bundle_name(conn, repo_id, target) == "morpheapp"
    finally:
        conn.close()


def test_choose_bundle_name_prefers_repo_derived_alias(tmp_path):
    db = tmp_path / "names2.db"
    init_db(str(db))
    conn = connect(str(db))
    try:
        with conn:
            repo_id = ensure_repo(conn, "https://github.com/0-BlackSpectrum-0/channel-blacklist-patch")
            for bundle in ("channel-blacklist", "0-blackspectrum-0"):
                upsert_bundle(
                    conn, repo_id, bundle, "stable",
                    {"apps": [], "version": "1.0.0"},
                    {"version": "1.0.0"}, "2026-10-24T00:00:00Z",
                )
            target = type("Target", (), {"url": "https://github.com/0-BlackSpectrum-0/channel-blacklist-patch"})
            assert choose_bundle_name(conn, repo_id, target) == "channel-blacklist"
    finally:
        conn.close()


def test_worker_bootstraps_incrementally_and_merges_changelog(tmp_path):
    db = tmp_path / "morphe.db"
    docs = make_mirror()
    mirror = FakeMirror(docs)
    worker = Worker(str(db), source_base="https://mirror.test/", fetcher=mirror)

    first = worker.run(workers=1)
    assert first["errors"] == []
    assert first["bootstrap_suppressed"] is True
    assert first["written"] == 2
    assert first["changes"] == 0

    conn = connect(str(db), readonly=True)
    try:
        assert get_meta(conn, "worker_bootstrap_done") == "1"
        assert get_meta(conn, "source") == "sqlite-worker"
        index = build_index(conn)
        assert set(index) == {"foo:stable", "foo:dev"}
        detail = build_detail(conn, "foo", "stable")
        assert detail["version"] == "1.2.3"
        assert detail["apps"][0]["package"] == APP
        assert conn.execute("SELECT COUNT(*) FROM patch_targets").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 0
        # Bootstrap runs emit no changelog items, but the snapshot still carries
        # the UTC day so the frontend can label an empty "What's New".
        snapshot = get_snapshot(conn, "changes")
        assert snapshot["affected_bundles"] == []
        assert snapshot["date"] == get_meta(conn, "last_success_at")[:10]
        assert get_snapshot(conn, "core")["date"]
        assert get_snapshot(conn, "stats")["total_apps"] == 1
    finally:
        conn.close()

    calls_after_first = len(mirror.calls)
    second = worker.run(workers=1)
    assert second["fetched"] == 0
    assert second["unchanged"] == 2
    assert len(mirror.calls) == calls_after_first + 1

    mirror.docs["data/repos.json"][REPO]["main"] = "main-2"
    mirror.docs[f"data/patches/{MAIN_SLUG}.json"] = patches_doc("1.2.4", "Example patch", "Updated description")
    mirror.docs[f"data/bundles/{MAIN_SLUG}.json"] = release_doc("1.2.4", "New release notes")
    third = worker.run(workers=1)
    assert third["errors"] == []
    assert third["fetched"] == 1
    assert third["changes"] == 1

    conn = connect(str(db), readonly=True)
    try:
        detail = build_detail(conn, "foo", "stable")
        assert detail["version"] == "1.2.4"
        assert detail["release_notes"] == "New release notes"
        payload = get_snapshot(conn, "changes")
        assert len(payload["affected_bundles"]) == 1
        entry = payload["affected_bundles"][0]
        assert entry["badge_type"] == "UPDATED"
        assert entry["apps"][0]["badge_type"] == "UPDATED APP"
        assert entry["extra_badges"] == ["VERSION BUMP"]
        assert conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 1
    finally:
        conn.close()


def test_worker_keeps_legacy_latest_and_release_notes_when_bundle_metadata_disappears(tmp_path):
    db = tmp_path / "morphe.db"
    docs = make_mirror()
    mirror = FakeMirror(docs)
    worker = Worker(str(db), source_base="https://mirror.test/", fetcher=mirror)

    init_db(str(db))
    conn = connect(str(db))
    try:
        with conn:
            repo_id = ensure_repo(conn, REPO)
            upsert_bundle(
                conn, repo_id, "foo", "latest",
                {"apps": [], "version": "0.9.0", "release_notes": "Legacy notes"},
                {"version": "0.9.0"}, "2026-10-24T00:00:00Z",
            )
    finally:
        conn.close()

    first = worker.run(workers=1)
    assert first["errors"] == []

    mirror.docs.pop(f"data/bundles/{MAIN_SLUG}.json")
    mirror.docs["data/repos.json"][REPO]["main"] = "main-2"
    mirror.docs[f"data/patches/{MAIN_SLUG}.json"] = patches_doc("1.2.5")
    second = worker.run(workers=1)
    assert second["errors"] == []

    conn = connect(str(db), readonly=True)
    try:
        detail = build_detail(conn, "foo", "stable")
        assert detail["release_notes"] == "Release notes"
        latest = conn.execute(
            "SELECT version, latest_origin FROM bundles WHERE bundle = 'foo' AND channel = 'latest'"
        ).fetchone()
        assert latest["version"] == "0.9.0"
        assert latest["latest_origin"] == "legacy"
    finally:
        conn.close()


def test_worker_retries_after_fetch_failure_without_advancing_hash(tmp_path):
    db = tmp_path / "morphe.db"
    docs = make_mirror()
    mirror = FakeMirror(docs)
    mirror.fail_patch = True
    worker = Worker(str(db), source_base="https://mirror.test/", fetcher=mirror)

    failed = worker.run(force=True, limit=1, workers=1)
    assert len(failed["errors"]) == 1
    assert failed["written"] == 0

    conn = connect(str(db), readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM repo_sources").fetchone()[0] == 0
        assert get_meta(conn, "worker_bootstrap_done") is None
    finally:
        conn.close()

    mirror.fail_patch = False
    recovered = worker.run(workers=1)
    assert recovered["errors"] == []
    assert recovered["written"] == 2

    conn = connect(str(db), readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM repo_sources").fetchone()[0] == 2
        assert get_meta(conn, "worker_bootstrap_done") == "1"
    finally:
        conn.close()


def test_worker_dry_run_does_not_write(tmp_path):
    db = tmp_path / "morphe.db"
    mirror = FakeMirror(make_mirror())
    worker = Worker(str(db), source_base="https://mirror.test/", fetcher=mirror)

    result = worker.run(force=True, limit=1, workers=1, dry_run=True)
    assert result["written"] == 1
    assert result["errors"] == []

    conn = connect(str(db), readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM bundles").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM repo_sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        conn.close()

class FakeNotifier:
    def __init__(self):
        self.started = []
        self.finished = []

    def start(self, summary):
        self.started.append(dict(summary))
        return "message-1"

    def finish(self, summary, message_id=None, error=None):
        self.finished.append((dict(summary), message_id, error))
        return True


def test_worker_calls_notifier_for_one_start_and_finish(tmp_path):
    db = tmp_path / "notify.db"
    worker = Worker(str(db), source_base="https://mirror.test/", fetcher=FakeMirror(make_mirror()))
    notifier = FakeNotifier()

    result = worker.run(workers=1, notifier=notifier)

    assert result["bootstrap_suppressed"] is True
    assert len(notifier.started) == 1
    assert len(notifier.finished) == 1
    summary, message_id, error = notifier.finished[0]
    assert message_id == "message-1"
    assert error is None
    assert summary["finished_at"]
    assert summary["changed_bundles"] == []


def test_telegram_entries_adapts_the_change_list():
    from server.worker import telegram_entries

    entry = {"bundle": "foo", "channel": "stable", "badge_type": "UPDATED", "apps": []}
    payload = {"date": "2026-10-29", "affected_bundles": [entry, "not-a-dict", None]}
    assert telegram_entries(payload) == {"foo:stable": entry}


def test_notify_telegram_needs_credentials(monkeypatch):
    from server.worker import notify_telegram

    monkeypatch.delenv("TG_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT", raising=False)
    payload = {"date": "2026-10-29", "affected_bundles": [{"bundle": "foo"}]}
    assert notify_telegram(payload) is False


def test_notify_telegram_ignores_empty_days(monkeypatch):
    from server.worker import notify_telegram

    monkeypatch.setenv("TG_TOKEN", "token")
    monkeypatch.setenv("TG_CHAT", "chat")
    assert notify_telegram({"date": "2026-10-29", "affected_bundles": []}) is False
