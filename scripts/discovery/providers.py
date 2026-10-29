"""Discovery providers for Morphe patch bundle repositories."""

import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import state_manager
from repo_utils import build_repo_url, fetch, find_repo_urls, parse_repo_url, repo_key

OFFICIAL_BUNDLES_URL = "https://morphe-patches.software/data/bundles.json"
JMAN_SOURCES_URL = (
    "https://raw.githubusercontent.com/Jman-Github/Patch-Bundle-Registry/"
    "bundles/config/sources.json"
)
ARCHIVE_REPOS_URL = (
    "https://raw.githubusercontent.com/rushiforai/morphe-archive/main/repos.txt"
)
AWESOME_REPOS_URL = (
    "https://raw.githubusercontent.com/nvbangg/awesome-morphe/main/data/repos.json"
)
AWESOME_TREE_URL = (
    "https://api.github.com/repos/nvbangg/awesome-morphe/git/trees/main?recursive=1"
)
AWESOME_BUNDLE_SOURCE_PREFIX = "data/bundle-sources/"

GITHUB_SEARCH_QUERIES = (
    '"morphe patches" in:name,description,readme',
    "morphe-patches in:name,description",
    "topic:morphe",
    '"patches-bundle.json" morphe',
)

FAST_PROVIDERS = (
    ("official", "official_bundles"),
    ("jman", "jman_registry"),
    ("awesome", "awesome_morphe"),
    ("archive", "archive_repos"),
    ("custom", "custom_repos"),
)


class ProviderUnavailable(Exception):
    pass


def _github_headers():
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_json(url, headers=None, timeout=20):
    merged = dict(headers or {})
    if "api.github.com" in url:
        merged = {**_github_headers(), **merged}
    return fetch(url, headers=merged, timeout=timeout, as_json=True)


def fetch_text(url, headers=None, timeout=20):
    return fetch(url, headers=headers, timeout=timeout)


def _candidate(platform, repo, source, **extra):
    repo = str(repo or "").strip("/")
    if not platform or not repo:
        return None
    item = {
        "platform": platform,
        "repo": repo,
        "url": build_repo_url(platform, repo),
        "source": source,
    }
    item.update({key: value for key, value in extra.items() if value not in (None, "")})
    return item


def _from_payload(payload, source):
    return [
        _candidate(item["platform"], item["repo"], source)
        for item in find_repo_urls(payload)
    ]


def _from_json_url(url, source, headers=None):
    return _from_payload(fetch_json(url, headers=headers), source)


def official_bundles():
    return _from_json_url(
        OFFICIAL_BUNDLES_URL,
        "official",
        headers={"Referer": "https://morphe-patches.software/"},
    )


def jman_registry():
    payload = fetch_json(JMAN_SOURCES_URL)
    candidates = []
    if isinstance(payload, dict):
        for source in payload.values():
            if not isinstance(source, dict):
                continue
            if source.get("disabled") is True or source.get("enabled") is False:
                continue
            for value in (source.get("patches"), source.get("url")):
                platform, repo = parse_repo_url(value)
                if platform and repo:
                    candidates.append(_candidate(platform, repo, "jman"))
                    break
    return candidates or _from_payload(payload, "jman")


def _awesome_bundle_sources():
    tree = fetch_json(AWESOME_TREE_URL)
    paths = [
        item["path"]
        for item in tree.get("tree", [])
        if isinstance(item, dict)
        and item.get("type") == "blob"
        and str(item.get("path", "")).startswith(AWESOME_BUNDLE_SOURCE_PREFIX)
        and str(item.get("path", "")).endswith(".json")
    ]
    if not paths:
        return []

    candidates = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_to_path = {
            executor.submit(
                fetch_json,
                f"https://raw.githubusercontent.com/nvbangg/awesome-morphe/main/{path}",
            ): path
            for path in paths
        }
        for future in as_completed(future_to_path):
            try:
                candidates.extend(_from_payload(future.result(), "awesome"))
            except Exception:
                continue
    return candidates


def awesome_morphe():
    candidates = _from_json_url(AWESOME_REPOS_URL, "awesome")
    try:
        candidates.extend(_awesome_bundle_sources())
    except Exception:
        pass
    return candidates


def _parse_archive_repos(text):
    candidates = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        urls = find_repo_urls(line)
        if urls:
            for item in urls:
                candidates.append(_candidate(item["platform"], item["repo"], "archive"))
            continue

        platform = "github"
        entry = line
        if line.startswith("gl:") or line.startswith("gitlab:"):
            platform = "gitlab"
            entry = line.split(":", 1)[1].strip()
        match = re.match(r"^([^/#\s]+)/([^/#\s]+)", entry)
        if match:
            candidates.append(
                _candidate(platform, f"{match.group(1)}/{match.group(2)}", "archive")
            )
    return candidates


def archive_repos():
    return _parse_archive_repos(fetch_text(ARCHIVE_REPOS_URL))


def custom_repos():
    candidates = []
    for owner, repo, platform in state_manager.load_repo_list(
        state_manager.CUSTOM_REPO_PATH
    ):
        candidates.append(_candidate(platform, f"{owner}/{repo}", "custom"))
    return candidates


def ignored_repo_keys():
    keys = set()
    for owner, repo, platform in state_manager.load_repo_list(
        state_manager.IGNORE_REPO_PATH
    ):
        keys.add(repo_key(platform, f"{owner}/{repo}"))
    return keys


def github_search(queries=GITHUB_SEARCH_QUERIES, per_page=100):
    if not os.environ.get("GITHUB_TOKEN"):
        raise ProviderUnavailable("GITHUB_TOKEN is not set")

    candidates = []
    for query in queries:
        encoded = urllib.parse.quote(query, safe="")
        url = (
            "https://api.github.com/search/repositories"
            f"?q={encoded}&sort=updated&order=desc&per_page={per_page}"
        )
        payload = fetch_json(url)
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            if item.get("archived") or item.get("fork"):
                continue
            platform, repo = parse_repo_url(item.get("html_url", ""))
            if not platform or not repo:
                continue
            candidate = _candidate(
                platform,
                repo,
                "github-search",
                default_branch=item.get("default_branch", ""),
                description=item.get("description", ""),
                topics=item.get("topics", []),
                pushed_at=item.get("pushed_at", ""),
                stars=item.get("stargazers_count", 0),
            )
            if candidate:
                candidates.append(candidate)
    return candidates


def run_fast_provider(name):
    functions = {
        "official_bundles": official_bundles,
        "jman_registry": jman_registry,
        "awesome_morphe": awesome_morphe,
        "archive_repos": archive_repos,
        "custom_repos": custom_repos,
    }
    function = functions.get(name)
    if not function:
        raise ValueError(f"unknown provider: {name}")
    return function()
