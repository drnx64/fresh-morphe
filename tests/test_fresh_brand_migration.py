"""Tests for the semantic awesome -> fresh state migration."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from migrate_fresh_brand import (
    _migrate_discovered,
    _migrate_provider_runs,
    _migrate_review,
)


def test_discovered_migrates_sources_only():
    data = {
        "repos": {
            "github:nvbangg/awesome-morphe": {
                "repo": "nvbangg/awesome-morphe",
                "sources": ["awesome", "github-search"],
                "topics": ["awesome"],
                "description": "an awesome mirror",
            }
        }
    }
    assert _migrate_discovered(data) is True
    entry = data["repos"]["github:nvbangg/awesome-morphe"]
    assert entry["sources"] == ["fresh", "github-search"]
    assert entry["repo"] == "nvbangg/awesome-morphe"
    assert entry["topics"] == ["awesome"]
    assert entry["description"] == "an awesome mirror"


def test_review_migrates_sources_only():
    data = {"items": [{"repo": "acme/patches", "sources": ["awesome"]}]}
    assert _migrate_review(data) is True
    assert data["items"][0]["sources"] == ["fresh"]


def test_provider_runs_moves_key_and_keeps_newer_data():
    data = {"awesome": {"count": 2}, "fresh": {"count": 3}}
    assert _migrate_provider_runs(data) is True
    assert "awesome" not in data
    assert data["fresh"]["count"] == 3


def test_no_change_is_reported():
    data = {"repos": {"github:acme/patches": {"sources": ["fresh"]}}}
    assert _migrate_discovered(data) is False
