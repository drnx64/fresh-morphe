"""F-Droid metadata source for apps that Google Play cannot resolve.

Many patched apps are forks, F-Droid builds or region-locked packages with no
Play Store page. F-Droid publishes a per-package page that carries the app name
and, for packages hosted in its repository, a stable icon URL — exactly the
two fields the registry needs.

Only unresolved packages are queried (the pipeline already asks Play first),
one page at a time with jitter, and answers are cached in
data/state/fdroid_cache.json so hourly runs do not re-scrape the same apps.

Network errors are never cached as "not found": the package is retried on the
next run, while a definitive 404 is remembered for NEGATIVE_TTL_DAYS.
"""
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from html import unescape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app_metadata
from state_manager import STATE_DIR, load_json, save_json

CACHE_PATH = os.path.join(STATE_DIR, "fdroid_cache.json")
PAGE_URL = "https://f-droid.org/en/packages/{}/"
ICON_URL = "https://f-droid.org/repo/icons-640/{}.png"

CACHE_TTL_DAYS = 7
NEGATIVE_TTL_DAYS = 3
MAX_RETRIES = 2
BASE_DELAY = 0.6  # seconds between page requests, plus jitter
REQUEST_TIMEOUT = 12

HEADERS = {
    "User-Agent": "MorpheTracker/1.0 (+https://github.com/drnx64/morphe-track-patches)",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}

_TITLE_RE = re.compile(r"<title>\s*(.*?)\s*</title>", re.IGNORECASE | re.DOTALL)


class FdroidUnavailable(Exception):
    """Raised when a page could not be read for a transient reason."""


def _http_get(url):
    """Return the response body; raise HTTPError for a definitive status."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_page(pkg, page):
    """Extract (name, icon_url) from a package page; either may be ""."""
    name = ""
    m = _TITLE_RE.search(page)
    if m:
        title = app_metadata.clean_app_name(m.group(1))
        if title and title.lower() != "f-droid" and pkg.lower() not in title.lower():
            name = title

    icon_url = ""
    icon_pattern = re.compile(r"icons-640/" + re.escape(pkg) + r"\.png", re.IGNORECASE)
    if icon_pattern.search(page):
        icon_url = ICON_URL.format(pkg)
    return name, icon_url


def _cache_entry_fresh(entry, ttl_days):
    if not isinstance(entry, dict):
        return False
    checked = entry.get("checked_at")
    if not isinstance(checked, (int, float)):
        return False
    return (time.time() - checked) < ttl_days * 86400


def _load_cache():
    data = load_json(CACHE_PATH, default={})
    return data if isinstance(data, dict) else {}


def fetch_app(pkg, cache=None):
    """Look up one package on F-Droid using the cache.

    Returns `{"name", "icon_url", "found"}`; `found` is False when the package
    has no page. Raises FdroidUnavailable when the lookup should be retried
    later, so callers never cache a transient outage as a negative answer.
    """
    pkg = (pkg or "").lower().strip()
    if not pkg:
        return {"name": "", "icon_url": "", "found": False}

    cache = _load_cache() if cache is None else cache
    cached = cache.get(pkg)
    if isinstance(cached, dict):
        ttl = CACHE_TTL_DAYS if cached.get("found") else NEGATIVE_TTL_DAYS
        if _cache_entry_fresh(cached, ttl):
            return cached

    url = PAGE_URL.format(pkg)
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            page = _http_get(url)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                result = {"name": "", "icon_url": "", "found": False, "checked_at": int(time.time())}
                cache[pkg] = result
                return result
            last_error = err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last_error = err
        else:
            name, icon_url = _parse_page(pkg, page)
            result = {
                "name": name,
                "icon_url": icon_url,
                "found": bool(name or icon_url),
                "checked_at": int(time.time()),
            }
            cache[pkg] = result
            return result

        if attempt < MAX_RETRIES:
            time.sleep(BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5))

    raise FdroidUnavailable(f"{pkg}: {last_error}")


def enrich_unresolved(cache, packages=None, sleep=True, max_lookups=None):
    """Fill names/icons for unresolved packages from F-Droid.

    `cache` is the app_cache registry, mutated in place. Returns the number of
    packages that gained at least one field.
    """
    wanted = sorted(set(packages or cache.keys()))
    pending = [p for p in wanted if app_metadata.is_unresolved(cache, p)]
    if max_lookups is not None:
        pending = pending[:max_lookups]
    if not pending:
        print("[fdroid] No unresolved packages to look up")
        return 0

    print(f"[fdroid] Looking up {len(pending)} unresolved packages...")
    fdroid_cache = _load_cache()
    improved = 0
    for i, pkg in enumerate(pending):
        try:
            result = fetch_app(pkg, fdroid_cache)
        except FdroidUnavailable as err:
            print(f"[fdroid] {err} (will retry next run)")
            continue

        if result.get("name") or result.get("icon_url"):
            app_metadata.upsert(
                pkg, cache,
                name=result.get("name", ""), name_source=app_metadata.FDROID_SOURCE,
                icon_url=result.get("icon_url", ""), icon_source=app_metadata.FDROID_SOURCE,
            )
            improved += 1
        if sleep and i < len(pending) - 1:
            time.sleep(BASE_DELAY + random.uniform(0, 0.5))

    save_json(CACHE_PATH, fdroid_cache)
    print(f"[fdroid] Resolved {improved}/{len(pending)} packages")
    return improved


def main():
    if "--stats" in sys.argv:
        cache = app_metadata.load_cache()
        fdroid_cache = _load_cache()
        resolved = sum(1 for e in cache.values() if isinstance(e, dict) and e.get("name_source") == app_metadata.FDROID_SOURCE)
        icons = sum(1 for e in cache.values() if isinstance(e, dict) and e.get("icon_source") == app_metadata.FDROID_SOURCE)
        print(f"[fdroid] registry: {len(cache)} entries | names from F-Droid: {resolved} | icons from F-Droid: {icons}")
        print(f"[fdroid] lookup cache: {len(fdroid_cache)} packages")
        return 0

    pkgs = [a for a in sys.argv[1:] if not a.startswith("-")]
    cache = app_metadata.load_cache()
    if pkgs:
        fdroid_cache = _load_cache()
        for pkg in pkgs:
            try:
                print(pkg, "->", fetch_app(pkg, fdroid_cache))
            except FdroidUnavailable as err:
                print(pkg, "-> unavailable:", err)
        save_json(CACHE_PATH, fdroid_cache)
        return 0

    enrich_unresolved(cache)
    app_metadata.save_cache(cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
