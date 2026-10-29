"""Tests for shared GitHub/GitLab repository helpers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from repo_utils import (
    build_raw_url,
    build_repo_url,
    find_repo_urls,
    get_auth_headers,
    normalize_image_url,
    parse_repo_url,
    repo_key,
)


def test_parse_github_url():
    assert parse_repo_url("https://github.com/Owner/Repo/tree/main") == (
        "github",
        "Owner/Repo",
    )


def test_parse_gitlab_subgroup():
    assert parse_repo_url("https://gitlab.com/group/sub/repo/-/tree/main") == (
        "gitlab",
        "group/sub/repo",
    )


def test_parse_gitlab_api_url():
    assert parse_repo_url(
        "https://gitlab.com/api/v4/projects/group%2Fsub%2Frepo/repository/files/a"
    ) == ("gitlab", "group/sub/repo")


def test_parse_scheme_less_repo():
    assert parse_repo_url("github.com/Owner/Repo") == ("github", "Owner/Repo")


def test_build_repo_and_raw_urls():
    assert build_repo_url("github", "Owner/Repo") == "https://github.com/Owner/Repo"
    assert build_raw_url("github", "Owner/Repo", "main", "patches-list.json") == (
        "https://raw.githubusercontent.com/Owner/Repo/main/patches-list.json"
    )
    assert "group%2Fsub%2Frepo" in build_raw_url(
        "gitlab", "group/sub/repo", "main", "patches-bundle.json"
    )


def test_repo_key_is_case_insensitive():
    assert repo_key("github", "Owner/Repo") == "github:owner/repo"


def test_find_repo_urls_nested_and_deduped():
    payload = {
        "sources": [
            {"url": "https://github.com/a/one"},
            {"url": "https://github.com/A/One"},
            {"disabled": True, "url": "https://github.com/b/two"},
            {"nested": ["https://gitlab.com/g/sub/three/-/tree/main"]},
        ]
    }
    found = find_repo_urls(payload)
    assert [(item["platform"], item["repo"]) for item in found] == [
        ("github", "a/one"),
        ("gitlab", "g/sub/three"),
    ]


def test_normalize_image_urls():
    assert normalize_image_url("https://avatars.githubusercontent.com/u/1?s=460") == (
        "https://avatars.githubusercontent.com/u/1?s=128"
    )
    assert normalize_image_url("https://gitlab.com/uploads/-/avatar.png") == (
        "https://gitlab.com/uploads/-/avatar.png?width=128"
    )
    assert normalize_image_url("/uploads/avatar.png").startswith("https://gitlab.com/")


def test_github_token_only_added_to_github(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    headers = get_auth_headers("https://api.github.com/repos/a/b")
    assert headers["Authorization"] == "Bearer token"
    assert "Authorization" not in get_auth_headers("https://example.com/a")


def test_github_token_skipped_for_release_downloads(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    headers = get_auth_headers(
        "https://github.com/a/b/releases/download/v1/bundle.mpp"
    )
    assert "Authorization" not in headers
