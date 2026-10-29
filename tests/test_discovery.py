"""Tests for source discovery verification and state."""

import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import state_manager
from discovery import providers, runner, state, verify


BUNDLE = {
    "name": "Test",
    "version": "1.0",
    "download_url": "https://github.com/acme/patches/releases/download/v1/test.mpp",
}
PATCH_LIST = {"patches": [{"name": "One"}, {"name": "Two"}]}


def _fake_fetch(branch_manifests):
    def fake(url, **kwargs):
        for branch, payload in branch_manifests.items():
            if f"/{branch}/" in url:
                return payload["bundle"] if url.endswith("patches-bundle.json") else payload["list"]
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    return fake


def test_state_migrates_legacy_provider_labels(tmp_path, monkeypatch):
    discovered = tmp_path / "discovered_repos.json"
    discovered.write_text(json.dumps({
        "repos": {"github:acme/patches": {"sources": ["awesome", "github-search"]}},
    }), encoding="utf-8")
    monkeypatch.setattr(state, "discovered_repos_path", lambda: str(discovered))
    assert state.load_state()["repos"]["github:acme/patches"]["sources"] == [
        "fresh",
        "github-search",
    ]


def test_provider_runs_migrates_legacy_key(tmp_path, monkeypatch):
    runs = tmp_path / "provider_runs.json"
    runs.write_text(json.dumps({"awesome": {"count": 2}, "fresh": {"count": 3}}), encoding="utf-8")
    monkeypatch.setattr(state, "provider_runs_path", lambda: str(runs))
    loaded = state.load_provider_runs()
    assert "awesome" not in loaded
    assert loaded["fresh"]["count"] == 3


def test_verify_accepts_both_manifests(monkeypatch):
    monkeypatch.setattr(
        verify, "fetch", _fake_fetch({"main": {"bundle": BUNDLE, "list": PATCH_LIST}})
    )
    result = verify.verify_repo({
        "platform": "github",
        "repo": "acme/patches",
        "url": "https://github.com/acme/patches",
    })
    assert result["status"] == "verified"
    assert result["channels"]["stable"]["branch"] == "main"
    assert result["channels"]["stable"]["patch_count"] == 2


def test_verify_requires_patch_list(monkeypatch):
    def fake(url, **kwargs):
        if url.endswith("patches-bundle.json"):
            return BUNDLE
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(verify, "fetch", fake)
    result = verify.verify_repo({
        "platform": "github",
        "repo": "acme/patches",
        "url": "https://github.com/acme/patches",
    })
    assert result["status"] == "review"
    assert "patches-list.json" in result["reason"]


def test_verify_rejects_missing_mpp(monkeypatch):
    bad_bundle = {**BUNDLE, "download_url": "https://github.com/acme/patches"}
    monkeypatch.setattr(
        verify,
        "fetch",
        _fake_fetch({"main": {"bundle": bad_bundle, "list": PATCH_LIST}}),
    )
    result = verify.verify_repo({
        "platform": "github",
        "repo": "acme/patches",
        "url": "https://github.com/acme/patches",
    })
    assert result["status"] == "review"
    assert ".mpp" in result["reason"]


def test_verify_discovers_stable_and_dev(monkeypatch):
    monkeypatch.setattr(
        verify,
        "fetch",
        _fake_fetch({
            "main": {"bundle": BUNDLE, "list": PATCH_LIST},
            "dev": {"bundle": BUNDLE, "list": PATCH_LIST},
        }),
    )
    result = verify.verify_repo({
        "platform": "github",
        "repo": "acme/patches",
        "url": "https://github.com/acme/patches",
    })
    assert set(result["channels"]) == {"stable", "dev"}


def test_verify_transient_error(monkeypatch):
    def fake(url, **kwargs):
        raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)

    monkeypatch.setattr(verify, "fetch", fake)
    result = verify.verify_repo({
        "platform": "github",
        "repo": "acme/patches",
        "url": "https://github.com/acme/patches",
    })
    assert result["status"] == "transient"


def test_runner_merges_dedupes_and_verifies(monkeypatch, tmp_path):
    monkeypatch.setattr(state_manager, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "FAST_PROVIDERS", (("custom", "custom_repos"),))
    monkeypatch.setattr(
        runner,
        "run_fast_provider",
        lambda name: [
            {
                "platform": "github",
                "repo": "acme/patches",
                "url": "https://github.com/acme/patches",
                "source": "custom",
            },
            {
                "platform": "github",
                "repo": "ACME/Patches",
                "url": "https://github.com/ACME/Patches",
                "source": "custom",
            },
        ],
    )
    monkeypatch.setattr(runner, "ignored_repo_keys", lambda: set())
    monkeypatch.setattr(runner, "known_repo_keys", lambda: set())
    monkeypatch.setattr(
        runner,
        "verify_repo",
        lambda entry: {
            "status": "verified",
            "channels": {"stable": {"branch": "main", "patch_count": 1}},
            "evidence": {"branch": "main", "patch_count": 1},
        },
    )

    summary = runner.run(force=True, search=False, quiet=True)

    assert summary["candidates"] == 1
    assert summary["verified"] == 1
    assert summary["baseline"] is True
    saved = json.loads((tmp_path / "discovered_repos.json").read_text(encoding="utf-8"))
    assert saved["baseline_pending"] is True
    assert list(saved["repos"]) == ["github:acme/patches"]
    assert saved["repos"]["github:acme/patches"]["sources"] == ["custom"]
    assert state.baseline_pending() is True
    assert state.clear_baseline_pending() is True
    assert state.baseline_pending() is False


def test_runner_keeps_verified_repo_on_transient(monkeypatch, tmp_path):
    monkeypatch.setattr(state_manager, "STATE_DIR", str(tmp_path))
    state.save_state({
        "baseline_done": True,
        "baseline_pending": False,
        "baseline_date": "2026-01-01",
        "repos": {
            "github:acme/patches": {
                "platform": "github",
                "repo": "acme/patches",
                "url": "https://github.com/acme/patches",
                "status": "verified",
                "first_seen": "2026-01-01T00:00:00Z",
                "last_seen": "2026-01-01T00:00:00Z",
                "sources": ["custom"],
                "channels": {"stable": {"branch": "main"}},
                "evidence": {"branch": "main"},
                "last_verified": "2026-01-01T00:00:00Z",
            }
        },
    })
    monkeypatch.setattr(runner, "FAST_PROVIDERS", ())
    monkeypatch.setattr(runner, "ignored_repo_keys", lambda: set())
    monkeypatch.setattr(runner, "known_repo_keys", lambda: set())
    monkeypatch.setattr(
        runner,
        "verify_repo",
        lambda entry: {"status": "transient", "reason": "HTTP 403"},
    )

    runner.run(force=True, search=False, quiet=True)

    saved = state.load_state()
    entry = saved["repos"]["github:acme/patches"]
    assert entry["status"] == "verified"
    assert entry["last_error"] == "HTTP 403"


def _search_item(index):
    return {
        "html_url": f"https://github.com/acme/repo{index}",
        "default_branch": "main",
        "archived": False,
        "fork": False,
        "description": f"repo {index}",
        "topics": ["morphe"],
        "pushed_at": "2026-10-01T00:00:00Z",
        "stargazers_count": 1,
    }


def test_github_search_paginates_until_short_page(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    calls = []

    def fake(url, headers=None, timeout=20):
        calls.append(url)
        page = 1 if "&page=1" in url else 2
        count = 100 if page == 1 else 3
        return {"items": [_search_item(page * 1000 + i) for i in range(count)]}

    monkeypatch.setattr(providers, "fetch_json", fake)
    result = providers.github_search(queries=("q",), pages=3, delay=0)

    assert len(calls) == 2
    assert "&page=1" in calls[0]
    assert "&page=2" in calls[1]
    assert len(result) == 103


def test_github_search_stops_at_page_cap(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("DISCOVERY_SEARCH_PAGES", "99")
    assert providers.search_page_limit() == providers.GITHUB_SEARCH_PAGES_MAX

    calls = []

    def fake(url, headers=None, timeout=20):
        calls.append(url)
        page = int(url.rsplit("&page=", 1)[1])
        return {"items": [_search_item(page * 1000 + i) for i in range(100)]}

    monkeypatch.setattr(providers, "fetch_json", fake)
    result = providers.github_search(queries=("q",), delay=0)

    assert len(calls) == providers.GITHUB_SEARCH_PAGES_MAX
    assert len(result) == providers.GITHUB_SEARCH_PAGES_MAX * 100


def test_github_search_dedupes_across_queries(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(
        providers,
        "fetch_json",
        lambda url, **kwargs: {"items": [_search_item(1)]},
    )
    result = providers.github_search(queries=("a", "b"), pages=1, delay=0)
    assert len(result) == 1


def test_github_search_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(providers.ProviderUnavailable):
        providers.github_search()


def test_scanned_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(state_manager, "STATE_DIR", str(tmp_path))

    assert state.load_scanned() == {}
    assert state.append_scanned([("github:acme/patch", "2026-10-29T00:00:00Z")]) == [
        "github:acme/patch"
    ]
    assert state.load_scanned() == {"github:acme/patch": "2026-10-29T00:00:00Z"}

    # Already cached entries are never rewritten.
    assert state.append_scanned([("github:acme/patch", "2026-10-30T00:00:00Z")]) == []
    assert state.load_scanned()["github:acme/patch"] == "2026-10-29T00:00:00Z"


def test_scanned_cache_skips_rescan_when_state_lost(tmp_path, monkeypatch):
    monkeypatch.setattr(state_manager, "STATE_DIR", str(tmp_path))
    state.append_scanned([("github:acme/patches", state.utc_now())])
    state.save_state({
        "baseline_done": True,
        "repos": {
            "github:acme/patches": {
                "platform": "github",
                "repo": "acme/patches",
                "url": "https://github.com/acme/patches",
                "status": "review",
                "sources": ["custom"],
            }
        },
    })
    monkeypatch.setattr(runner, "FAST_PROVIDERS", ())
    monkeypatch.setattr(runner, "ignored_repo_keys", lambda: set())
    monkeypatch.setattr(runner, "known_repo_keys", lambda: set())
    calls = []
    monkeypatch.setattr(
        runner,
        "verify_repo",
        lambda entry: calls.append(entry) or {"status": "review", "reason": "x"},
    )

    summary = runner.run(force=False, search=False, quiet=True)

    assert calls == []
    assert summary["checked_this_run"] == 0


def test_runner_checkpoints_state_per_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(state_manager, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "FAST_PROVIDERS", (("custom", "custom_repos"),))
    monkeypatch.setattr(runner, "run_fast_provider", lambda name: [])
    monkeypatch.setattr(runner, "ignored_repo_keys", lambda: set())
    monkeypatch.setattr(runner, "known_repo_keys", lambda: set())
    saved = []
    real_save = runner.save_state
    monkeypatch.setattr(
        runner, "save_state", lambda snapshot: saved.append(1) or real_save(snapshot)
    )

    runner.run(force=True, search=False, quiet=True)

    # One checkpoint after the provider loop plus the final save.
    assert len(saved) >= 2
    assert (tmp_path / "discovered_repos.json").exists()


def test_runner_reports_newly_verified_repos(monkeypatch, tmp_path):
    monkeypatch.setattr(state_manager, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "FAST_PROVIDERS", (("custom", "custom_repos"),))
    monkeypatch.setattr(
        runner,
        "run_fast_provider",
        lambda name: [{
            "platform": "github",
            "repo": "acme/patches",
            "url": "https://github.com/acme/patches",
            "source": "custom",
        }],
    )
    monkeypatch.setattr(runner, "ignored_repo_keys", lambda: set())
    monkeypatch.setattr(runner, "known_repo_keys", lambda: set())
    monkeypatch.setattr(
        runner,
        "verify_repo",
        lambda entry: {
            "status": "verified",
            "channels": {"stable": {"branch": "main", "patch_count": 2}},
            "evidence": {"branch": "main", "patch_count": 2, "name": "X Patches"},
        },
    )

    summary = runner.run(force=True, search=False, quiet=True)

    assert summary["found"] == [{
        "repo": "acme/patches",
        "url": "https://github.com/acme/patches",
        "name": "X Patches",
        "channels": ["stable"],
    }]
    assert "github:acme/patches" in state.load_scanned()


def test_known_repo_keys_parses_repos_list(tmp_path, monkeypatch):
    path = tmp_path / "repos_list.txt"
    path.write_text(
        "# Fresh Morphe - All Known Repositories\n"
        "# Total: 3 repos\n"
        "\n"
        "020krJ/morpheArc -> https://github.com/020krJ/morpheArc\n"
        "Acme/Patches -> https://github.com/Acme/Patches/\n"
        "gl:someone/gitlab-patches -> https://gitlab.com/someone/gitlab-patches\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(providers, "REPOS_LIST_PATH", str(path))

    keys = providers.known_repo_keys()

    # Owner case is normalized away, headers and blank lines are ignored.
    assert "github:020krj/morphearc" in keys
    assert "github:acme/patches" in keys
    assert "gitlab:someone/gitlab-patches" in keys
    assert len(keys) == 3


def test_known_repo_keys_without_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "REPOS_LIST_PATH", str(tmp_path / "missing.txt"))
    assert providers.known_repo_keys() == set()


def test_known_repo_is_skipped_but_stays_in_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state_manager, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "FAST_PROVIDERS", ())
    monkeypatch.setattr(runner, "ignored_repo_keys", lambda: set())
    monkeypatch.setattr(runner, "known_repo_keys", lambda: {"github:acme/tracked"})
    state.save_state({
        "baseline_done": True,
        "repos": {
            "github:acme/tracked": {
                "platform": "github",
                "repo": "acme/tracked",
                "url": "https://github.com/acme/tracked",
                "status": "verified",
                "last_verified": "2026-01-01T00:00:00Z",
                "channels": {"stable": {"branch": "main", "patch_count": 2}},
                "sources": ["github-search"],
            },
        },
    })
    calls = []
    monkeypatch.setattr(
        runner,
        "verify_repo",
        lambda entry: calls.append(entry) or {"status": "verified", "channels": {}},
    )

    summary = runner.run(force=True, search=False, quiet=True)

    # Already tracked by the pipeline — never re-verified.
    assert calls == []
    assert summary["checked_this_run"] == 0
    assert summary["known_skipped"] == 1
    assert summary["found"] == []

    # State survives so load_verified_repos() keeps its channels.
    entry = state.load_state()["repos"]["github:acme/tracked"]
    assert entry["channels"] == {"stable": {"branch": "main", "patch_count": 2}}
    assert "github:acme/tracked" not in state.load_scanned()


def test_untracked_repo_is_still_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(state_manager, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "FAST_PROVIDERS", ())
    monkeypatch.setattr(runner, "ignored_repo_keys", lambda: set())
    monkeypatch.setattr(runner, "known_repo_keys", lambda: {"github:acme/tracked"})
    state.save_state({
        "baseline_done": True,
        "repos": {
            "github:acme/tracked": {
                "platform": "github",
                "repo": "acme/tracked",
                "url": "https://github.com/acme/tracked",
                "status": "new",
                "sources": ["github-search"],
            },
            "github:newbie/fresh": {
                "platform": "github",
                "repo": "newbie/fresh",
                "url": "https://github.com/newbie/fresh",
                "status": "new",
                "sources": ["github-search"],
            },
        },
    })
    calls = []
    monkeypatch.setattr(
        runner,
        "verify_repo",
        lambda entry: calls.append(entry["repo"]) or {
            "status": "verified",
            "channels": {"stable": {"branch": "main", "patch_count": 1}},
            "evidence": {"name": "Fresh"},
        },
    )

    summary = runner.run(force=True, search=False, quiet=True)

    assert calls == ["newbie/fresh"]
    assert summary["known_skipped"] == 1
    assert [item["repo"] for item in summary["found"]] == ["newbie/fresh"]

