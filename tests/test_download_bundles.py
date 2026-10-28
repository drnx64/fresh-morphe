"""Tests for download_bundles.py — group_tree_files pure function."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from download_bundles import group_tree_files


def test_case_a_four_parts():
    tree = [
        {"path": "patch-bundles/my-bundle/stable/patches-bundle.json", "download_url": "https://example.com/a.mpp"},
        {"path": "patch-bundles/my-bundle/stable/patches-list.json", "download_url": "https://example.com/b.mpp"},
    ]
    result = group_tree_files(tree)
    assert "my-bundle" in result
    assert "stable" in result["my-bundle"]
    assert result["my-bundle"]["stable"]["bundle_path"] is not None
    assert result["my-bundle"]["stable"]["list_path"] is not None


def test_case_b_three_parts():
    tree = [
        {"path": "patch-bundles/1fexd-patch-bundles/1fexd-stable-patches-bundle.json", "download_url": "https://example.com/a.mpp"},
        {"path": "patch-bundles/1fexd-patch-bundles/1fexd-stable-patches-list.json", "download_url": "https://example.com/b.mpp"},
    ]
    result = group_tree_files(tree)
    assert "1fexd" in result
    assert "stable" in result["1fexd"]


def test_suffix_stripping():
    tree = [
        {"path": "patch-bundles/my-bundle-patch-bundles/stable/patches-bundle.json", "download_url": "https://example.com/a.mpp"},
        {"path": "patch-bundles/my-bundle-patch-bundles/stable/patches-list.json", "download_url": "https://example.com/b.mpp"},
    ]
    result = group_tree_files(tree)
    assert "my-bundle" in result


def test_empty_tree():
    assert group_tree_files([]) == {}


def test_non_morphe_files_ignored():
    tree = [
        {"path": "patch-bundles/something/readme.md", "download_url": "https://example.com/c.mpp"},
    ]
    result = group_tree_files(tree)
    assert len(result) == 0


def test_dev_channel():
    tree = [
        {"path": "patch-bundles/my-bundle/dev/patches-bundle.json", "download_url": "https://example.com/a.mpp"},
        {"path": "patch-bundles/my-bundle/dev/patches-list.json", "download_url": "https://example.com/b.mpp"},
    ]
    result = group_tree_files(tree)
    assert "my-bundle" in result
    assert "dev" in result["my-bundle"]

def test_group_tree_preserves_registry_metadata():
    registry = {"bundle_id": "abc", "download_url": "https://example.com/a.mpp"}
    tree = [
        {"path": "patch-bundles/my-bundle/stable/patches-bundle.json", "registry": registry, "legacy_keys": ["old:stable"]},
        {"path": "patch-bundles/my-bundle/stable/patches-list.json", "registry": registry, "legacy_keys": ["old:stable"]},
    ]
    result = group_tree_files(tree)
    assert result["my-bundle"]["stable"]["registry"]["bundle_id"] == "abc"
    assert result["my-bundle"]["stable"]["legacy_keys"] == ["old:stable"]
