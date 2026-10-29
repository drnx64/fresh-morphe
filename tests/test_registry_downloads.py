"""Tests for resolving registry release assets without the old Jman branch."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import download_bundles


def test_release_url_parts():
    base, tag, repo = download_bundles._release_repo_and_tag(
        "https://github.com/Owner/Repo/releases/download/v1.2.3/patches-1.2.3.mpp"
    )
    assert base == "https://github.com/Owner/Repo/releases/download/v1.2.3/"
    assert tag == "v1.2.3"
    assert repo == "Owner/Repo"


def test_download_asset_uses_release_asset_url(monkeypatch):
    calls = []

    def fake_download(url, *args, **kwargs):
        calls.append(url)
        return "content" if url.endswith("patches-list.json") else None

    monkeypatch.setattr(download_bundles, "download_file_with_retry", fake_download)
    monkeypatch.setattr(download_bundles, "_release_asset_cache_clear", lambda: None, raising=False)

    content = download_bundles._download_registry_asset(
        {"download_url": "https://github.com/Owner/Repo/releases/download/v1.2.3/patches-1.2.3.mpp"},
        "patches-list.json",
    )

    assert content == "content"
    assert calls[0] == "https://github.com/Owner/Repo/releases/download/v1.2.3/patches-list.json"


def test_download_asset_falls_back_to_release_api(monkeypatch):
    monkeypatch.setattr(download_bundles, "download_file_with_retry", lambda *a, **k: None)
    monkeypatch.setattr(
        download_bundles,
        "_github_release_assets",
        lambda url: {"patches-list.json": "https://example.com/api-asset"},
    )

    def fake_download(url, *args, **kwargs):
        return "fallback" if url == "https://example.com/api-asset" else None

    monkeypatch.setattr(download_bundles, "download_file_with_retry", fake_download)

    content = download_bundles._download_registry_asset(
        {"download_url": "https://github.com/Owner/Repo/releases/download/v1.2.3/patches-1.2.3.mpp"},
        "patches-list.json",
    )

    assert content == "fallback"


def test_missing_registry_download_url_returns_none():
    assert download_bundles._download_registry_asset({}, "patches-list.json") is None
