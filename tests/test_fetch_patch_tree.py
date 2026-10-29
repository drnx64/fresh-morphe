"""Tests for registry-backed patch tree discovery."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fetch_patch_tree


def _bundle(
    source_url="https://github.com/Owner/Repo",
    alias="new-alias",
    bundle_id="abc123",
    channel="stable",
    version="v1.2.3",
):
    return {
        "id": bundle_id,
        "version": version,
        "download_url": f"{source_url}/releases/download/{version}/patches-{version}.mpp",
        "channels": [channel],
        "source_id": "src1",
        "file_hash": "hash1",
        "metadata_status": "verified",
        "patch_list_available": True,
        "patch_count": 5,
        "source": {
            "id": "src1",
            "url": source_url,
            "namespace": "Owner",
            "repo": "Repo",
            "aliases": [alias],
        },
    }


def test_empty_registry_refuses_to_write_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_patch_tree, "RAW_DIR", str(tmp_path))
    monkeypatch.setattr(fetch_patch_tree, "fetch_current_morphe_channels", lambda: {
        "stable": [], "latest": [], "dev": []
    })
    monkeypatch.setattr(fetch_patch_tree, "_load_existing_records", lambda: {})

    with pytest.raises(RuntimeError, match="no usable Morphe channel bundles"):
        fetch_patch_tree.fetch_bundle_tree()


def test_uses_historical_bundle_name_and_legacy_keys():
    existing = {
        "old-name:stable": {
            "bundle": "old-name",
            "repo_url": "https://github.com/Owner/Repo",
        },
        "owner-repo:stable": {
            "bundle": "owner-repo",
            "repo_url": "https://github.com/Owner/Repo",
        },
    }
    entries = fetch_patch_tree.build_registry_tree(
        {"stable": [_bundle()]}, existing
    )

    assert [e["path"] for e in entries] == [
        "patch-bundles/old-name/stable/patches-bundle.json",
        "patch-bundles/old-name/stable/patches-list.json",
    ]
    assert entries[0]["registry"]["bundle_id"] == "abc123"
    assert entries[0]["registry"]["version"] == "v1.2.3"
    assert "old-name:stable" in entries[0]["legacy_keys"]
    assert "owner-repo:stable" in entries[0]["legacy_keys"]


def test_new_source_uses_alias_and_sanitizes():
    entries = fetch_patch_tree.build_registry_tree(
        {"stable": [_bundle(alias="A New Alias!")]}, {}
    )
    assert entries[0]["path"] == "patch-bundles/a-new-alias/stable/patches-bundle.json"


def test_name_collision_gets_repo_suffix():
    existing = {
        "duplicate:stable": {
            "bundle": "duplicate",
            "repo_url": "https://github.com/Other/Project",
        },
    }
    entries = fetch_patch_tree.build_registry_tree(
        {"stable": [_bundle(alias="duplicate")]}, existing
    )
    assert entries[0]["path"] == "patch-bundles/duplicate-repo/stable/patches-bundle.json"


def test_builds_entries_for_multiple_channels_without_duplicates():
    bundle = _bundle(channel="stable")
    bundle["channels"] = ["stable", "latest"]
    entries = fetch_patch_tree.build_registry_tree(
        {"stable": [bundle], "latest": [bundle], "dev": []}, {}
    )
    paths = [e["path"] for e in entries]
    assert "patch-bundles/new-alias/stable/patches-list.json" in paths
    assert "patch-bundles/new-alias/latest/patches-list.json" in paths
    assert len(paths) == 4


def test_fetch_writes_tree_json(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_patch_tree, "RAW_DIR", str(tmp_path))
    monkeypatch.setattr(fetch_patch_tree, "fetch_current_morphe_channels", lambda: {
        "stable": [_bundle()], "latest": [], "dev": []
    })
    monkeypatch.setattr(fetch_patch_tree, "_load_existing_records", lambda: {})

    files = fetch_patch_tree.fetch_bundle_tree()

    assert len(files) == 2
    assert os.path.exists(tmp_path / "tree.json")
