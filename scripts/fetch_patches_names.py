"""Fetch patches_name from build.gradle.kts for each unique repo_url.

Caches results in data/state/patches_names_cache.json.
Only fetches if cache is older than 30 days or missing.
Merges patches_name into parsed_bundles.json.
"""

import os
import re
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from state_manager import STATE_DIR, RAW_DIR, load_json, save_json

CACHE_PATH = os.path.join(STATE_DIR, "patches_names_cache.json")
CACHE_MAX_AGE_DAYS = 30

_GRADLE_ABOUT_RE = re.compile(r'about\s*\{[^}]*name\s*=\s*"([^"]+)"', re.DOTALL)

_CLEAN_PATTERNS = [
    re.compile(r'\s+for use with\s+Morphe', re.IGNORECASE),
    re.compile(r'\s+for\s+Morphe', re.IGNORECASE),
]


def _clean_patches_name(name):
    """Strip noise suffixes from patches name."""
    for pat in _CLEAN_PATTERNS:
        name = pat.sub('', name)
    return name.strip()


def _extract_owner_from_url(repo_url):
    """Extract owner/username from a repo URL as fallback name."""
    if not repo_url or not isinstance(repo_url, str):
        return None
    m = re.match(r"https://(github|gitlab)\.com/([^/]+)/", repo_url)
    return m.group(2) if m else None


def _fetch_gradle_kts(owner, repo, platform="github"):
    """Try to fetch patches/build.gradle.kts from a repo's default branch."""
    for branch in ("main", "master"):
        if platform == "gitlab":
            raw_url = f"https://gitlab.com/{owner}/{repo}/-/raw/{branch}/patches/build.gradle.kts"
        else:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/patches/build.gradle.kts"
        req = urllib.request.Request(raw_url, headers={"User-Agent": "FreshMorphe/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            return None
        except Exception:
            return None
    return None


def _extract_name_from_gradle(content):
    """Extract name from about { name = "..." } block in build.gradle.kts."""
    m = _GRADLE_ABOUT_RE.search(content)
    if m:
        return _clean_patches_name(m.group(1))
    return None


def _parse_repo_url(repo_url):
    """Extract (owner, repo, platform) from a full repo URL."""
    if not repo_url or not isinstance(repo_url, str):
        return None
    m = re.match(r"https://(github|gitlab)\.com/([^/]+)/([^/]+)", repo_url)
    if m:
        return m.group(2), m.group(3), m.group(1)
    return None


def _load_cache():
    """Load the patches names cache."""
    data = load_json(CACHE_PATH, default={"last_updated": None, "names": {}})
    if not isinstance(data, dict):
        data = {"last_updated": None, "names": {}}
    if "names" not in data:
        data["names"] = {}
    # Re-clean all cached names
    for url, name in list(data["names"].items()):
        if name:
            cleaned = _clean_patches_name(name)
            if cleaned != name:
                data["names"][url] = cleaned
    return data


def _is_cache_stale(cache):
    """Check if cache is older than CACHE_MAX_AGE_DAYS."""
    last_updated = cache.get("last_updated")
    if not last_updated:
        return True
    try:
        last_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - last_dt > timedelta(days=CACHE_MAX_AGE_DAYS)
    except (ValueError, TypeError):
        return True


def fetch_patches_names():
    """Main entry point. Fetches patches names and merges into parsed_bundles.json."""
    parsed_path = os.path.join(RAW_DIR, "parsed_bundles.json")
    parsed = load_json(parsed_path, default={})
    if not parsed:
        print("[patches_names] No parsed_bundles.json found, skipping.")
        return

    # Collect unique repo_urls
    repo_urls = set()
    for record in parsed.values():
        url = record.get("repo_url", "")
        if url:
            repo_urls.add(url)

    if not repo_urls:
        print("[patches_names] No repo_urls found, skipping.")
        return

    cache = _load_cache()
    stale = _is_cache_stale(cache)
    names = cache.get("names", {})

    # Determine which URLs need fetching (missing from cache or cache is stale)
    to_fetch = []
    for url in repo_urls:
        if url not in names or not names[url]:
            to_fetch.append(url)

    if not to_fetch and not stale:
        print(f"[patches_names] Cache is fresh ({len(names)} entries), no fetch needed.")
        _merge_names(parsed, names)
        return

    if stale and not to_fetch:
        print(f"[patches_names] Cache is stale but all {len(repo_urls)} URLs already cached, refreshing...")
        to_fetch = list(repo_urls)

    if not to_fetch:
        to_fetch = list(repo_urls)

    print(f"[patches_names] Fetching {len(to_fetch)} patch names (cache has {len(names)})...")
    fetched = 0
    for i, url in enumerate(to_fetch):
        parsed_repo = _parse_repo_url(url)
        if not parsed_repo:
            print(f"  [{i+1}/{len(to_fetch)}] SKIP - can't parse repo URL: {url}")
            continue

        owner, repo, platform = parsed_repo
        content = _fetch_gradle_kts(owner, repo, platform)
        if content:
            name = _extract_name_from_gradle(content)
            if name:
                names[url] = name
                fetched += 1
                safe_name = name.encode("ascii", "replace").decode("ascii")
                print(f"  [{i+1}/{len(to_fetch)}] OK {owner}/{repo} -> \"{safe_name}\"")
            else:
                names[url] = owner
                fetched += 1
                print(f"  [{i+1}/{len(to_fetch)}] FALLBACK {owner}/{repo} -> \"{owner}\" (no name in about block)")
        else:
            names[url] = owner
            fetched += 1
            print(f"  [{i+1}/{len(to_fetch)}] FALLBACK {owner}/{repo} -> \"{owner}\" (no build.gradle.kts)")

        # Rate limit: 100ms between requests
        if i < len(to_fetch) - 1:
            time.sleep(0.1)

    # Save cache
    cache["names"] = names
    cache["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_json(CACHE_PATH, cache)
    print(f"[patches_names] Saved {len(names)} names to cache ({fetched} newly fetched)")

    # Merge into parsed_bundles.json
    _merge_names(parsed, names)


def _merge_names(parsed, names):
    """Merge patches_name into parsed_bundles.json records."""
    updated = 0
    for record in parsed.values():
        repo_url = record.get("repo_url", "")
        if not repo_url:
            continue
        existing = record.get("patches_name", "")
        cached = names.get(repo_url, "")
        if cached and cached != existing:
            record["patches_name"] = cached
            updated += 1
        elif not existing and not cached:
            # No cached name, fall back to owner
            owner = _extract_owner_from_url(repo_url)
            if owner:
                record["patches_name"] = owner
                updated += 1

    if updated > 0:
        parsed_path = os.path.join(RAW_DIR, "parsed_bundles.json")
        save_json(parsed_path, parsed)
        print(f"[patches_names] Updated {updated} bundles with patches_name")
    else:
        print(f"[patches_names] All bundles already have patches_name")


if __name__ == "__main__":
    fetch_patches_names()
