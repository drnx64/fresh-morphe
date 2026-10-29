"""Tests for source discovery verification and state."""

import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import state_manager
from discovery import runner, state, verify


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
