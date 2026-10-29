"""Discovery providers for Fresh Morphe patch bundle repositories."""

import os
import re
import time
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
FRESH_REPOS_URL = (
    "https://raw.githubusercontent.com/nvbangg/awesome-morphe/main/data/repos.json"
)
FRESH_TREE_URL = (
    "https://api.github.com/repos/nvbangg/awesome-morphe/git/trees/main?recursive=1"
)
FRESH_BUNDLE_SOURCE_PREFIX = "data/bundle-sources/"

GITHUB_SEARCH_QUERIES = (
    '"morphe patches" in:name,description,readme',
    "morphe-patches in:name,description",
    "topic:morphe",
    '"patches-bundle.json" morphe',
)

# GitHub search allows 30 requests/minute authenticated and returns at most 1000
# results per query, so 10 pages of 100 is the hard ceiling.
GITHUB_SEARCH_PAGES_ENV = "DISCOVERY_SEARCH_PAGES"
GITHUB_SEARCH_PAGES_DEFAULT = 3
GITHUB_SEARCH_PAGES_MAX = 10
GITHUB_SEARCH_DELAY_ENV = "DISCOVERY_SEARCH_DELAY"
GITHUB_SEARCH_DELAY_DEFAULT = 2.1


FAST_PROVIDERS = (
    ("official", "official_bundles"),
    ("jman", "jman_registry"),
    ("fresh", "fresh_morphe"),
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


def _fresh_bundle_sources():
    tree = fetch_json(FRESH_TREE_URL)
    paths = [
        item["path"]
        for item in tree.get("tree", [])
        if isinstance(item, dict)
        and item.get("type") == "blob"
        and str(item.get("path", "")).startswith(FRESH_BUNDLE_SOURCE_PREFIX)
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
                candidates.extend(_from_payload(future.result(), "fresh"))
            except Exception:
                continue
    return candidates


def fresh_morphe():
    candidates = _from_json_url(FRESH_REPOS_URL, "fresh")
    try:
        candidates.extend(_fresh_bundle_sources())
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


REPOS_LIST_PATH = os.path.join(state_manager.ROOT_DATA_DIR, "repos_list.txt")
_GITHUB_REPO_RE = re.compile(
    r"(?:github\.com|gitlab\.com)/([^/\s>\"']+/[^/\s>\"']+)", re.IGNORECASE
)


def known_repo_keys():
    """Repos already tracked in ``data/repos_list.txt``.

    Those are written by ``fetch_external_repos`` from the morphe-archive list
    and are fetched by the pipeline already, so discovery must not spend its
    request budget verifying them again.
    """
    keys = set()
    if not os.path.exists(REPOS_LIST_PATH):
        return keys
    try:
        with open(REPOS_LIST_PATH, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                match = _GITHUB_REPO_RE.search(line)
                if match:
                    platform = "gitlab" if "gitlab.com" in line.lower() else "github"
                    keys.add(repo_key(platform, match.group(1).rstrip(".,;/")))
                    continue
                match = re.match(r"^([^/#\s]+)/([^/#\s]+)", line)
                if match:
                    keys.add(repo_key("github", f"{match.group(1)}/{match.group(2)}"))
    except OSError:
        return keys
    return keys


def search_page_limit():
    """How many result pages to read per query (env-tunable, hard-capped)."""
    try:
        value = int(os.environ.get(GITHUB_SEARCH_PAGES_ENV, GITHUB_SEARCH_PAGES_DEFAULT))
    except (TypeError, ValueError):
        value = GITHUB_SEARCH_PAGES_DEFAULT
    return max(1, min(GITHUB_SEARCH_PAGES_MAX, value))


def _search_delay():
    try:
        value = float(os.environ.get(GITHUB_SEARCH_DELAY_ENV, GITHUB_SEARCH_DELAY_DEFAULT))
    except (TypeError, ValueError):
        value = GITHUB_SEARCH_DELAY_DEFAULT
    return max(0.0, value)


def github_search(queries=GITHUB_SEARCH_QUERIES, per_page=100, pages=None, delay=None):
    if not os.environ.get("GITHUB_TOKEN"):
        raise ProviderUnavailable("GITHUB_TOKEN is not set")

    pages = search_page_limit() if pages is None else max(
        1, min(GITHUB_SEARCH_PAGES_MAX, int(pages))
    )
    delay = _search_delay() if delay is None else max(0.0, float(delay))

    candidates = []
    seen_keys = set()
    first_request = True
    for query in queries:
        encoded = urllib.parse.quote(query, safe="")
        for page in range(1, pages + 1):
            if not first_request and delay:
                # 30 searches/minute authenticated — pace every request after the first.
                time.sleep(delay)
            first_request = False
            url = (
                "https://api.github.com/search/repositories"
                f"?q={encoded}&sort=updated&order=desc&per_page={per_page}&page={page}"
            )
            payload = fetch_json(url)
            items = payload.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("archived") or item.get("fork"):
                    continue
                platform, repo = parse_repo_url(item.get("html_url", ""))
                if not platform or not repo:
                    continue
                key = repo_key(platform, repo)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
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
            if len(items) < per_page:
                break
    return candidates


def run_fast_provider(name):
    functions = {
        "official_bundles": official_bundles,
        "jman_registry": jman_registry,
        "fresh_morphe": fresh_morphe,
        "archive_repos": archive_repos,
        "custom_repos": custom_repos,
    }
    function = functions.get(name)
    if not function:
        raise ValueError(f"unknown provider: {name}")
    return function()
