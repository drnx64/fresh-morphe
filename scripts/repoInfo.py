"""Fetch repository metadata from GitHub/GitLab APIs.

Gets stars, avatarUrl, repoDescription, isArchived, isFork, forkParent,
bundleImageUrl, and stores them in bundle data files.
"""

import functools
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))

from repo_utils import (
    fetch,
    format_request_error,
    get_auth_headers,
    normalize_image_url,
    parse_repo_url,
)
from state_manager import ROOT_DATA_DIR, load_json, save_json

BUNDLES_DIR = os.path.join(ROOT_DATA_DIR, "bundles")
CONCURRENCY = 8
UNAVAILABLE_HTTP_CODES = (404, 451)


def _request_error(error):
    details = {"error": format_request_error(error)}
    if isinstance(error, urllib.error.HTTPError) and error.code in UNAVAILABLE_HTTP_CODES:
        details["isArchived"] = True
    return details


@functools.cache
def fetch_gitlab_user_avatar(owner):
    try:
        data = fetch(
            f"https://gitlab.com/api/v4/users?username={owner}",
            timeout=5,
            as_json=True,
        )
        if isinstance(data, list) and data:
            return normalize_image_url(data[0].get("avatar_url"))
    except Exception:
        pass
    return ""


def fetch_gitlab_repo_details(repo):
    query = """
    query GetProject($path: ID!) {
      project(fullPath: $path) {
        fullPath
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
    request = urllib.request.Request(
        "https://gitlab.com/api/graphql",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "FreshMorphe/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                return {"error": "Invalid repository data"}
            if data.get("errors"):
                return {"error": "Repository query failed"}
            query_data = data.get("data")
            if not isinstance(query_data, dict) or "project" not in query_data:
                return {"error": "Invalid repository data"}
            project = query_data["project"]
            if project is None:
                return {"isArchived": True, "error": "Repository not found or inaccessible"}
            if not isinstance(project, dict) or not project.get("fullPath"):
                return {"error": "Invalid repository data"}

            avatar = normalize_image_url(project.get("avatarUrl"))
            if not avatar and "/" in repo:
                avatar = fetch_gitlab_user_avatar(repo.split("/")[0])
            forked_from = project.get("forkedFrom") or {}

            return {
                "stars": project.get("starCount", 0),
                "avatarUrl": avatar,
                "repoDescription": (project.get("description") or "")[:200],
                "isArchived": bool(project.get("archived")),
                "isFork": bool(forked_from),
                "forkParent": forked_from.get("fullPath") or "",
                "fullName": project.get("fullPath") or repo,
                "bundleImageUrl": _probe_bundle_image(f"https://gitlab.com/{repo}"),
            }
    except (OSError, ValueError, urllib.error.URLError) as error:
        return _request_error(error)


def _probe_bundle_image(repo_url):
    platform, repo = parse_repo_url(repo_url)
    if not platform or not repo:
        return ""
    if platform == "github":
        raw = f"https://github.com/{repo}/raw/HEAD/patches-bundle.png"
    else:
        raw = f"https://gitlab.com/{repo}/-/raw/HEAD/patches-bundle.png"
    try:
        request = urllib.request.Request(
            raw,
            method="HEAD",
            headers=get_auth_headers(raw),
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            content_type = response.headers.get("Content-Type") or ""
            if response.status == 200 and content_type.startswith("image/"):
                return raw
    except Exception:
        return ""
    return ""


def _fetch_github_repo(repo):
    url = f"https://api.github.com/repos/{repo}"
    try:
        response = fetch(url, timeout=10, as_json=True)
        if not response or not isinstance(response, dict):
            return {}
        parent = response.get("parent") or {}
        return {
            "stars": response.get("stargazers_count", 0),
            "avatarUrl": normalize_image_url(
                (response.get("owner") or {}).get("avatar_url")
            ),
            "repoDescription": (response.get("description") or "")[:200],
            "isArchived": bool(response.get("archived")),
            "isFork": bool(response.get("fork")),
            "forkParent": parent.get("full_name") or "",
            "fullName": response.get("full_name") or repo,
            "bundleImageUrl": _probe_bundle_image(f"https://github.com/{repo}"),
        }
    except (OSError, ValueError, urllib.error.URLError) as error:
        return _request_error(error)


def _fetch_gitlab_repo(repo):
    return fetch_gitlab_repo_details(repo)


def _fetch_repo_details(repo_url):
    platform, repo = parse_repo_url(repo_url)
    if not platform or not repo:
        return {}
    if platform == "github":
        return _fetch_github_repo(repo)
    if platform == "gitlab":
        return _fetch_gitlab_repo(repo)
    return {}


def _iter_index_entries(index):
    if isinstance(index, dict):
        for key, entry in index.items():
            if not isinstance(entry, dict):
                continue
            yield key, entry
        return
    for entry in index or []:
        if isinstance(entry, dict):
            yield None, entry


def _unique_repo_urls(index):
    urls = {}
    for _, entry in _iter_index_entries(index):
        repo_url = entry.get("repo_url", "")
        if repo_url and repo_url not in urls:
            urls[repo_url] = entry
    return urls


def process(bundle_index):
    """Fetch metadata for every unique repo_url in the bundle index."""
    repo_urls = _unique_repo_urls(bundle_index)
    if not repo_urls:
        print("[repoInfo] No repos to check")
        return {}

    print(f"[repoInfo] Fetching metadata for {len(repo_urls)} repos...")
    results = {}
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        future_to_url = {
            executor.submit(_fetch_repo_details, url): url for url in repo_urls
        }
        for index, future in enumerate(as_completed(future_to_url)):
            url = future_to_url[future]
            try:
                details = future.result()
                if details:
                    results[url] = details
            except Exception as error:
                print(f"[-] Error fetching {url}: {error}")
            if (index + 1) % 20 == 0:
                print(f"[repoInfo] Progress: {index + 1}/{len(repo_urls)}")

    archived_count = sum(1 for value in results.values() if value.get("isArchived"))
    print(f"[repoInfo] Done: {len(results)} repos fetched, {archived_count} archived")
    return results


def update_bundle_files(repo_metadata):
    """Update split bundle files and the index with repo metadata."""
    if not repo_metadata:
        return

    index_path = os.path.join(BUNDLES_DIR, "_index.json")
    index = load_json(index_path, default={})
    updated = 0

    for key, entry in _iter_index_entries(index):
        repo_url = entry.get("repo_url", "")
        meta = repo_metadata.get(repo_url, {})
        if not meta:
            continue

        bundle_key = key or f"{entry.get('bundle', '')}:{entry.get('channel', '')}"
        if not bundle_key or bundle_key == ":":
            continue

        for field in (
            "stars",
            "avatarUrl",
            "repoDescription",
            "isArchived",
            "isFork",
            "forkParent",
            "bundleImageUrl",
        ):
            if field in meta:
                entry[field] = meta[field]

        bundle_path = os.path.join(
            BUNDLES_DIR, bundle_key.replace(":", "_") + ".json"
        )
        if os.path.exists(bundle_path):
            bundle_data = load_json(bundle_path, default={})
            for field in (
                "stars",
                "avatarUrl",
                "repoDescription",
                "isArchived",
                "isFork",
                "forkParent",
                "bundleImageUrl",
            ):
                if field in meta:
                    bundle_data[field] = meta[field]
            save_json(bundle_path, bundle_data)
            updated += 1

    if isinstance(index, dict):
        save_json(index_path, index)
    print(f"[repoInfo] Updated {updated} bundle files")


if __name__ == "__main__":
    index_path = os.path.join(BUNDLES_DIR, "_index.json")
    index = load_json(index_path, default={})
    results = process(index)
    update_bundle_files(results)
