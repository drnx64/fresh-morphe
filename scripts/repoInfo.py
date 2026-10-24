"""Fetch repository metadata from GitHub/GitLab APIs.

Gets: stars, avatarUrl, repoDescription, isArchived, bundleImageUrl.
Stores results in bundle data files.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))

from state_manager import ROOT_DATA_DIR, load_json, save_json

BUNDLES_DIR = os.path.join(ROOT_DATA_DIR, "bundles")
CONCURRENCY = 5


def _parse_repo_url(repo_url):
    """Extract source and repo from a URL. Returns (source, repo) or (None, None)."""
    if not repo_url or not isinstance(repo_url, str):
        return None, None
    lower = repo_url.lower()
    for source in ("github.com", "gitlab.com"):
        if source in lower:
            prefix = f"https://{source}/"
            if prefix in repo_url:
                repo = repo_url[len(prefix):].rstrip("/")
                if repo and "/" in repo:
                    return source.replace(".com", ""), repo
    return None, None


def _probe_bundle_image(repo_url):
    """Check for patches-bundle.png at the repo root (default branch).

    Uses /raw/HEAD/ so no branch name is needed; follows redirect.
    Returns the raw URL if found (200 + image/*), else "".
    """
    source, repo = _parse_repo_url(repo_url)
    if not source or not repo:
        return ""
    if source == "github":
        raw = f"https://github.com/{repo}/raw/HEAD/patches-bundle.png"
    elif source == "gitlab":
        raw = f"https://gitlab.com/{repo}/-/raw/HEAD/patches-bundle.png"
    else:
        return ""
    try:
        req = urllib.request.Request(raw, method="HEAD", headers={"User-Agent": "MorpheTracker/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type") or ""
            if resp.status == 200 and content_type.startswith("image/"):
                return raw
    except Exception:
        return ""
    return ""


def _fetch_github_repo(repo):
    """Fetch repo details from GitHub API."""
    url = f"https://api.github.com/repos/{repo}"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "MorpheTracker/1.0",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            parent = data.get("parent") or {}
            return {
                "stars": data.get("stargazers_count", 0),
                "avatarUrl": (data.get("owner", {}).get("avatar_url") or ""),
                "repoDescription": (data.get("description") or "")[:200],
                "isArchived": bool(data.get("archived")),
                "isFork": bool(data.get("fork")),
                "forkParent": parent.get("full_name") or "",
                "bundleImageUrl": _probe_bundle_image(f"https://github.com/{repo}"),
            }
    except urllib.error.HTTPError as e:
        if e.code in (404, 451):
            return {"isArchived": True, "error": f"HTTP {e.code}"}
        return {}
    except Exception:
        return {}


def _fetch_gitlab_repo(repo):
    """Fetch repo details from GitLab GraphQL API."""
    query = """
    query GetProject($path: ID!) {
      project(fullPath: $path) {
        description
        archived
        starCount
        avatarUrl
        forkedFrom {
          fullPath
        }
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"path": repo}}).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://gitlab.com/api/graphql",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "MorpheTracker/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            project = data.get("data", {}).get("project")
            if not project:
                return {"isArchived": True, "error": "404 Not Found"}
            forked_from = project.get("forkedFrom") or {}
            return {
                "stars": project.get("starCount", 0),
                "avatarUrl": (project.get("avatarUrl") or ""),
                "repoDescription": (project.get("description") or "")[:200],
                "isArchived": bool(project.get("archived")),
                "isFork": bool(forked_from),
                "forkParent": forked_from.get("fullPath") or "",
                "bundleImageUrl": _probe_bundle_image(f"https://gitlab.com/{repo}"),
            }
    except Exception:
        return {}


def _fetch_repo_details(repo_url):
    """Fetch repo details based on URL. Returns dict with metadata."""
    source, repo = _parse_repo_url(repo_url)
    if not source or not repo:
        return {}
    if source == "github":
        return _fetch_github_repo(repo)
    elif source == "gitlab":
        return _fetch_gitlab_repo(repo)
    return {}


def process(bundle_index):
    """Fetch repo metadata for all bundles in the index.

    Args:
        bundle_index: list of bundle dicts from _index.json (mutated in place).

    Returns:
        dict mapping repo_url -> metadata for downstream use.
    """
    # Collect unique repo URLs
    repo_urls = {}
    for entry in bundle_index:
        repo_url = entry.get("repo_url", "")
        if repo_url and repo_url not in repo_urls:
            repo_urls[repo_url] = entry

    if not repo_urls:
        print("[repoInfo] No repos to check")
        return {}

    print(f"[repoInfo] Fetching metadata for {len(repo_urls)} repos...")
    results = {}

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        future_to_url = {
            executor.submit(_fetch_repo_details, url): url
            for url in repo_urls
        }
        for i, future in enumerate(as_completed(future_to_url)):
            url = future_to_url[future]
            try:
                details = future.result()
                if details:
                    results[url] = details
            except Exception as e:
                print(f"[-] Error fetching {url}: {e}")

            if (i + 1) % 20 == 0:
                print(f"[repoInfo] Progress: {i + 1}/{len(repo_urls)}")

    archived_count = sum(1 for v in results.values() if v.get("isArchived"))
    print(f"[repoInfo] Done: {len(results)} repos fetched, {archived_count} archived")
    return results


def update_bundle_files(repo_metadata):
    """Update individual bundle JSON files with repo metadata."""
    if not repo_metadata:
        return

    index_path = os.path.join(BUNDLES_DIR, "_index.json")
    index = load_json(index_path, default=[])

    updated = 0
    for entry in index:
        repo_url = entry.get("repo_url", "")
        meta = repo_metadata.get(repo_url, {})
        if not meta:
            continue

        bundle_key = f"{entry['bundle']}:{entry['channel']}"
        bundle_path = os.path.join(BUNDLES_DIR, f"{bundle_key}.json")

        if os.path.exists(bundle_path):
            bundle_data = load_json(bundle_path, default={})
            for field in ("stars", "avatarUrl", "repoDescription", "isArchived", "isFork", "forkParent", "bundleImageUrl"):
                if field in meta:
                    bundle_data[field] = meta[field]
            save_json(bundle_path, bundle_data)
            updated += 1

    print(f"[repoInfo] Updated {updated} bundle files")


if __name__ == "__main__":
    index_path = os.path.join(BUNDLES_DIR, "_index.json")
    index = load_json(index_path, default=[])
    results = process(index)
    update_bundle_files(results)
