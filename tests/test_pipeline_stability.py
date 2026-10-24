"""Tests for pipeline stability: claims, snapshot rebuild, orphan pruning,
carry-forward reconciliation, and diff removal suppression."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import state_manager
import diff_engine
from state_manager import (
    repo_slug, load_claimed_keys, is_claimed,
    rebuild_snapshot_from_bundles, save_bundles_split,
)
from parse_bundles import reconcile_parsed_bundles
from download_bundles import group_tree_files


# ── repo_slug ─────────────────────────────────────────────────────────────

class TestRepoSlug:
    def test_github(self):
        assert repo_slug("https://github.com/Owner/Repo") == "owner/repo"

    def test_github_git_suffix(self):
        assert repo_slug("https://github.com/owner/repo.git") == "owner/repo"

    def test_gitlab(self):
        assert repo_slug("https://gitlab.com/owner/repo") == "owner/repo"

    def test_trailing_slash(self):
        assert repo_slug("https://github.com/owner/repo/") == "owner/repo"

    def test_not_a_repo_url(self):
        assert repo_slug("") == ""
        assert repo_slug(None) == ""
        assert repo_slug("https://example.com/a/b") == ""


# ── claims helpers ────────────────────────────────────────────────────────

def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)


@pytest.fixture
def claims_env(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    state = tmp_path / "state"
    raw.mkdir()
    state.mkdir()
    monkeypatch.setattr(state_manager, "RAW_DIR", str(raw))
    monkeypatch.setattr(state_manager, "STATE_DIR", str(state))
    monkeypatch.setattr(state_manager, "CUSTOM_REPO_PATH", str(tmp_path / "custom_repo.txt"))
    monkeypatch.setattr(state_manager, "IGNORE_REPO_PATH", str(tmp_path / "ignore_repo.txt"))
    return tmp_path


def _tree(*names):
    files = []
    for name in names:
        files.append({"path": f"patch-bundles/{name}/stable/patches-bundle.json"})
        files.append({"path": f"patch-bundles/{name}/stable/patches-list.json"})
    return files


class TestLoadClaimedKeys:
    def test_tree_and_external_claims(self, claims_env):
        _write_json(str(claims_env / "raw" / "tree.json"), _tree("foo", "bar"))
        _write_json(str(claims_env / "state" / "external_repos.json"), {
            "archive_available": True,
            "added": [{"owner": "Ext", "repo": "Repo", "bundle_slug": "ext-repo", "channels": ["stable"]}],
            "errors": [{"repo": "err/repo", "error": "boom"}],
        })
        claims = load_claimed_keys()
        assert "foo:stable" in claims["keys"]
        assert "bar:stable" in claims["keys"]
        assert "ext/repo" in claims["repos"]
        assert "err/repo" in claims["repos"]

    def test_ignore_repo_skipped(self, claims_env):
        _write_json(str(claims_env / "raw" / "tree.json"), _tree("foo", "ignored"))
        _write_json(str(claims_env / "state" / "external_repos.json"),
                    {"archive_available": True, "added": [], "errors": []})
        (claims_env / "ignore_repo.txt").write_text("ignored/whatever\n", encoding="utf-8")
        claims = load_claimed_keys()
        assert "ignored:stable" not in claims["keys"]
        assert "foo:stable" in claims["keys"]

    def test_missing_tree_returns_none(self, claims_env):
        _write_json(str(claims_env / "state" / "external_repos.json"),
                    {"archive_available": True, "added": [], "errors": []})
        assert load_claimed_keys() is None

    def test_missing_external_index_returns_none(self, claims_env):
        _write_json(str(claims_env / "raw" / "tree.json"), _tree("foo"))
        assert load_claimed_keys() is None

    def test_archive_unavailable_returns_none(self, claims_env):
        _write_json(str(claims_env / "raw" / "tree.json"), _tree("foo"))
        _write_json(str(claims_env / "state" / "external_repos.json"),
                    {"archive_available": False, "added": [], "errors": []})
        assert load_claimed_keys() is None


class TestIsClaimed:
    def test_key_claimed(self):
        claims = {"keys": {"foo:stable"}, "repos": set()}
        assert is_claimed("foo:stable", {}, claims)

    def test_repo_claimed(self):
        claims = {"keys": set(), "repos": {"owner/repo"}}
        rec = {"repo_url": "https://github.com/Owner/Repo"}
        assert is_claimed("unknown:stable", rec, claims)

    def test_not_claimed(self):
        claims = {"keys": {"foo:stable"}, "repos": {"owner/repo"}}
        rec = {"repo_url": "https://github.com/other/repo"}
        assert not is_claimed("dead:stable", rec, claims)

    def test_none_claims_means_everything_claimed(self):
        assert is_claimed("whatever:stable", {}, None)


# ── rebuild from bundles ──────────────────────────────────────────────────

class TestRebuildSnapshot:
    @pytest.fixture
    def bundles_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "bundles"
        d.mkdir()
        monkeypatch.setattr(state_manager, "BUNDLES_DIR", str(d))
        return d

    def test_orphan_files_excluded(self, bundles_dir):
        _write_json(str(bundles_dir / "_index.json"), {"foo:stable": {}})
        _write_json(str(bundles_dir / "foo_stable.json"),
                    {"bundle": "foo", "channel": "stable", "apps": []})
        _write_json(str(bundles_dir / "orphan_stable.json"),
                    {"bundle": "orphan", "channel": "stable", "apps": []})
        snapshot = rebuild_snapshot_from_bundles()
        assert "foo:stable" in snapshot
        assert "orphan:stable" not in snapshot

    def test_missing_index_returns_empty(self, bundles_dir):
        _write_json(str(bundles_dir / "foo_stable.json"),
                    {"bundle": "foo", "channel": "stable", "apps": []})
        assert rebuild_snapshot_from_bundles() == {}

    def test_index_entry_without_file_skipped(self, bundles_dir):
        _write_json(str(bundles_dir / "_index.json"),
                    {"foo:stable": {}, "gone:dev": {}})
        _write_json(str(bundles_dir / "foo_stable.json"),
                    {"bundle": "foo", "channel": "stable", "apps": []})
        snapshot = rebuild_snapshot_from_bundles()
        assert list(snapshot) == ["foo:stable"]


# ── save_bundles_split pruning ────────────────────────────────────────────

class TestSaveBundlesSplitPruning:
    @pytest.fixture
    def bundles_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "bundles"
        d.mkdir()
        monkeypatch.setattr(state_manager, "BUNDLES_DIR", str(d))
        return d

    def test_orphan_pruned_and_current_kept(self, bundles_dir):
        _write_json(str(bundles_dir / "orphan_stable.json"),
                    {"bundle": "orphan", "channel": "stable", "apps": []})
        data = {"foo:stable": {"bundle": "foo", "channel": "stable", "apps": []}}
        save_bundles_split(data)
        assert not (bundles_dir / "orphan_stable.json").exists()
        assert (bundles_dir / "foo_stable.json").exists()
        assert (bundles_dir / "_index.json").exists()
        index = json.loads((bundles_dir / "_index.json").read_text(encoding="utf-8"))
        assert list(index) == ["foo:stable"]

    def test_all_current_kept(self, bundles_dir):
        data = {"foo:stable": {"bundle": "foo", "channel": "stable", "apps": []}}
        save_bundles_split(data)
        assert (bundles_dir / "foo_stable.json").exists()

    def test_empty_data_does_not_prune(self, bundles_dir):
        _write_json(str(bundles_dir / "foo_stable.json"),
                    {"bundle": "foo", "channel": "stable", "apps": []})
        save_bundles_split({})
        assert (bundles_dir / "foo_stable.json").exists()


# ── index app package list (frontend paints from the index alone) ─────────

class TestIndexAppPackages:
    @pytest.fixture
    def bundles_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "bundles"
        d.mkdir()
        monkeypatch.setattr(state_manager, "BUNDLES_DIR", str(d))
        return d

    @staticmethod
    def _index(bundles_dir):
        return json.loads((bundles_dir / "_index.json").read_text(encoding="utf-8"))

    def test_index_lists_package_names_only(self, bundles_dir):
        data = {"foo:stable": {"bundle": "foo", "channel": "stable", "apps": [
            {"package": "com.a", "patches": [{"name": "p1"}]},
            {"package": "com.b", "patches": []},
        ]}}
        save_bundles_split(data)
        entry = self._index(bundles_dir)["foo:stable"]
        assert entry["apps"] == ["com.a", "com.b"]
        assert all(isinstance(pkg, str) for pkg in entry["apps"])
        assert entry["app_count"] == 2

    def test_index_app_count_matches_apps(self, bundles_dir):
        data = {"foo:stable": {"bundle": "foo", "channel": "stable", "apps": [
            {"package": "com.a"}, {"package": "com.b"}, {"package": "com.c"},
        ]}}
        save_bundles_split(data)
        entry = self._index(bundles_dir)["foo:stable"]
        assert entry["app_count"] == len(entry["apps"])

    def test_index_apps_absent_from_bundle_file_metadata(self, bundles_dir):
        # The index must not leak per-app detail (patches stay in the file)
        data = {"foo:stable": {"bundle": "foo", "channel": "stable", "apps": [
            {"package": "com.a", "patches": [{"name": "p1"}]},
        ]}}
        save_bundles_split(data)
        assert self._index(bundles_dir)["foo:stable"]["apps"] == ["com.a"]

    def test_snapshot_rebuild_ignores_index_apps(self, bundles_dir):
        _write_json(str(bundles_dir / "_index.json"),
                    {"foo:stable": {"bundle": "foo", "channel": "stable",
                                    "apps": ["com.stale"]}})
        _write_json(str(bundles_dir / "foo_stable.json"),
                    {"bundle": "foo", "channel": "stable",
                     "apps": [{"package": "com.real", "patches": []}]})
        snapshot = rebuild_snapshot_from_bundles()
        # Rebuild reads the file (full record), never the index copy
        assert snapshot["foo:stable"]["apps"] == [{"package": "com.real", "patches": []}]

    def test_committed_index_stays_in_sync(self):
        """The site's cold boot must never see an index without package lists."""
        index = state_manager.load_json(
            os.path.join(state_manager.BUNDLES_DIR, "_index.json"), default={})
        if not index:
            pytest.skip("no committed data/bundles/_index.json")
        missing = [k for k, v in index.items() if not isinstance(v.get("apps"), list)]
        assert not missing, f"index entries missing apps[]: {missing[:5]}"


# ── reconcile (stale drop + carry-forward) ────────────────────────────────

class TestReconcile:
    def test_stale_parsed_dropped(self):
        parsed = {"stale:stable": {"repo_url": "https://github.com/x/stale"}}
        claims = {"keys": {"live:stable"}, "repos": {"owner/live"}}
        result, dropped, carried = reconcile_parsed_bundles(parsed, {}, claims)
        assert result == {}
        assert dropped == ["stale:stable"]

    def test_claimed_missing_carried_forward(self):
        old = {"foo:stable": {"bundle": "foo", "repo_url": "https://github.com/o/f"}}
        claims = {"keys": {"foo:stable"}, "repos": set()}
        result, dropped, carried = reconcile_parsed_bundles({}, old, claims)
        assert "foo:stable" in result
        assert carried == ["foo:stable"]
        assert dropped == []

    def test_unclaimed_missing_not_carried(self):
        old = {"dead:stable": {"bundle": "dead", "repo_url": "https://github.com/o/d"}}
        claims = {"keys": {"other:stable"}, "repos": {"o/alive"}}
        result, dropped, carried = reconcile_parsed_bundles({}, old, claims)
        assert result == {}
        assert carried == []

    def test_none_claims_keeps_all_and_drops_nothing(self):
        parsed = {"a:stable": {"repo_url": "https://github.com/x/a"}}
        old = {"b:stable": {"bundle": "b"}}
        result, dropped, carried = reconcile_parsed_bundles(dict(parsed), old, None)
        assert dropped == []
        assert set(result) == {"a:stable", "b:stable"}

    def test_claimed_parsed_kept(self):
        parsed = {"foo:stable": {"repo_url": "https://github.com/o/f"}}
        claims = {"keys": {"foo:stable"}, "repos": set()}
        result, dropped, carried = reconcile_parsed_bundles(parsed, {}, claims)
        assert list(result) == ["foo:stable"]
        assert dropped == []


# ── diff suppression ──────────────────────────────────────────────────────

def _record(bundle, channel="stable", repo="", apps=None):
    return {
        "bundle": bundle,
        "channel": channel,
        "version": "1.0",
        "repo_url": repo,
        "apps": apps or [],
        "fingerprint": "fp-" + bundle + channel,
    }


class TestDiffSuppression:
    @pytest.fixture
    def diff_env(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw"
        state = tmp_path / "state"
        raw.mkdir()
        state.mkdir()
        monkeypatch.setattr(state_manager, "RAW_DIR", str(raw))
        monkeypatch.setattr(state_manager, "STATE_DIR", str(state))
        monkeypatch.setattr(state_manager, "CURRENT_SNAPSHOT_PATH", str(state / "current_snapshot.json"))
        monkeypatch.setattr(state_manager, "LAST_RUN_PATH", str(state / "last_run.json"))
        monkeypatch.setattr(state_manager, "CUSTOM_REPO_PATH", str(tmp_path / "custom_repo.txt"))
        monkeypatch.setattr(state_manager, "IGNORE_REPO_PATH", str(tmp_path / "ignore_repo.txt"))
        monkeypatch.setattr(diff_engine, "RAW_DIR", str(raw))
        return tmp_path

    def _setup(self, env, old, new, tree_names=(), ext=None):
        _write_json(str(env / "raw" / "parsed_bundles.json"), new)
        _write_json(str(env / "state" / "current_snapshot.json"), old)
        _write_json(str(env / "raw" / "tree.json"), _tree(*tree_names))
        _write_json(str(env / "state" / "external_repos.json"),
                    ext or {"archive_available": True, "added": [], "errors": []})

    def _run(self, env):
        has_changes = diff_engine.diff_snapshots()
        result = json.loads((env / "raw" / "diff_result.json").read_text(encoding="utf-8"))
        return has_changes, result["affected_bundles"]

    def test_claimed_missing_key_suppresses_removal(self, diff_env):
        old = {"foo:stable": _record("foo", repo="https://github.com/o/f")}
        self._setup(diff_env, old, {}, tree_names=("foo",))
        has_changes, affected = self._run(diff_env)
        assert has_changes is False
        assert affected == []

    def test_unclaimed_key_reported_removed(self, diff_env):
        old = {"dead:stable": _record("dead", repo="https://github.com/o/dead")}
        self._setup(diff_env, old, {}, tree_names=("other",))
        has_changes, affected = self._run(diff_env)
        assert has_changes is True
        assert affected[0]["badge_type"] == "REMOVED BUNDLE"
        assert affected[0]["bundle"] == "dead"

    def test_identity_migration_suppressed(self, diff_env):
        old = {"oldname:stable": _record("oldname", repo="https://github.com/o/same")}
        new = {"newname:stable": _record("newname", repo="https://github.com/o/same")}
        self._setup(diff_env, old, new, tree_names=("newname",))
        has_changes, affected = self._run(diff_env)
        assert has_changes is False
        assert affected == []

    def test_none_claims_suppress_all_removals(self, diff_env):
        old = {"dead:stable": _record("dead", repo="https://github.com/o/dead")}
        self._setup(diff_env, old, {}, tree_names=(), ext={"archive_available": True, "added": [], "errors": []})
        has_changes, affected = self._run(diff_env)
        assert has_changes is False
        assert affected == []

    def test_genuine_removal_still_reported(self, diff_env):
        old = {"dead:stable": _record("dead", repo="https://github.com/o/dead")}
        self._setup(diff_env, old, {}, tree_names=("other",))
        has_changes, affected = self._run(diff_env)
        assert has_changes is True
        assert affected[0]["badge_type"] == "REMOVED BUNDLE"

    def test_new_bundle_still_reported(self, diff_env):
        new = {"fresh:stable": _record("fresh", repo="https://github.com/o/fresh")}
        self._setup(diff_env, {}, new, tree_names=("fresh",))
        has_changes, affected = self._run(diff_env)
        assert has_changes is True
        assert affected[0]["badge_type"] == "NEW BUNDLE"

    def test_channel_swap_same_name_reported(self, diff_env):
        old = {"foo:stable": _record("foo", "stable", repo="https://github.com/o/f")}
        new = {"foo:dev": _record("foo", "dev", repo="https://github.com/o/f")}
        self._setup(diff_env, old, new, tree_names=("other",))
        has_changes, affected = self._run(diff_env)
        badges = {a["badge_type"] for a in affected}
        assert "REMOVED BUNDLE" in badges
        assert "NEW BUNDLE" in badges


class TestRepoMetadataOverlay:
    def test_cached_metadata_survives_snapshot_rewrite(self, tmp_path, monkeypatch):
        import merge_daily_buffer as mdb

        cache = {
            "https://github.com/up/repo": {
                "stars": 1234,
                "avatarUrl": "https://avatars.example/a.png",
                "repoDescription": "Patches",
                "isArchived": False,
                "isFork": True,
                "forkParent": "parent/repo",
                "bundleImageUrl": "https://img.example/b.png",
                "error": "HTTP 404",
            }
        }
        cache_path = tmp_path / "repo_cache.json"
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
        monkeypatch.setattr(mdb, "REPO_CACHE_PATH", str(cache_path))

        bundles = {
            "mybundle:stable": {"repo_url": "https://github.com/up/repo", "stars": 0},
            "norepo:stable": {"repo_url": ""},
        }
        mdb.apply_repo_metadata(bundles)

        record = bundles["mybundle:stable"]
        assert record["stars"] == 1234
        assert record["repoDescription"] == "Patches"
        assert record["isFork"] is True
        assert record["forkParent"] == "parent/repo"
        assert "error" not in record
        assert bundles["norepo:stable"] == {"repo_url": ""}
