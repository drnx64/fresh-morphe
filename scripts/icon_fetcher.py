"""Fetch app names and icon URLs from Google Play Store.

Stores URLs only (not base64) — the frontend handles fetching/resizing/caching.
Unified cache: data/state/app_cache.json maps package -> {name, icon_url}.
"""
import re
import sys
import os
import time
import random
import urllib.error
import urllib.request
from app_metadata import (
    APP_CACHE_PATH, PLAY_SOURCE, apply_icon_overrides, clean_app_name, is_real_name,
    normalize_cache, resolve_name, upsert, valid_icon_url,
)
from state_manager import load_json, save_json

# Re-exported for callers that already import the path from here.
__all__ = [
    "APP_CACHE_PATH",
    "fetch_app_icon",
    "fetch_and_cache_app_name",
    "fetch_app_name",
    "enrich_parsed_bundles_with_icons",
    "enrich_parsed_bundles_with_names",
    "enrich_parsed_bundles_with_store_fallbacks",
    "enrich_parsed_bundles_with_apkmirror_fallbacks",
    "apply_resolved_names",
    "apply_parsed_bundle_icon_overrides",
    "retry_empty_icons",
]

PLAY_STORE_URL = "https://play.google.com/store/apps/details?id={}"

MAX_RETRIES = 2
BASE_DELAY = 0.96

# A 404/410 from Play means the package is not published — retrying is pure
# latency. A 2026-10-29 run burned 14 of its 30 CI minutes re-requesting 404s.
PERMANENT_HTTP_CODES = (404, 410)

# Bound per-run icon work so one run can never eat the whole workflow budget.
ICON_FETCH_LIMIT_ENV = "ICON_FETCH_LIMIT"
ICON_FETCH_LIMIT_DEFAULT = 40
ICON_RETRY_DAYS_ENV = "ICON_RETRY_DAYS"
ICON_RETRY_DAYS_DEFAULT = 7

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_OG_IMAGE_RE = re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"', re.IGNORECASE)
_OG_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"', re.IGNORECASE)
_TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _icon_fetch_limit():
    return max(1, _int_env(ICON_FETCH_LIMIT_ENV, ICON_FETCH_LIMIT_DEFAULT))


def _icon_attempt_cutoff():
    days = max(0, _int_env(ICON_RETRY_DAYS_ENV, ICON_RETRY_DAYS_DEFAULT))
    return int(time.time()) - days * 86400


def _is_blocked_response(html_text):
    lower = html_text.lower()
    for marker in ("before you continue", "consent.google", "recaptcha", "unusual traffic", " automated queries"):
        if marker in lower:
            return True
    return False


def _fetch_page_with_retry(url, retries=MAX_RETRIES):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                if _is_blocked_response(text):
                    delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                    print(f"  [blocked] attempt {attempt + 1}/{retries}, retrying in {delay:.1f}s...")
                    time.sleep(delay)
                    continue
                return text
        except urllib.error.HTTPError as e:
            if e.code in PERMANENT_HTTP_CODES:
                # The app does not exist; retrying only wastes the run budget.
                return None
            delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
            print(f"  [error] attempt {attempt + 1}/{retries}: HTTP {e.code}, retrying in {delay:.1f}s...")
            time.sleep(delay)
        except Exception as e:
            delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
            print(f"  [error] attempt {attempt + 1}/{retries}: {e}, retrying in {delay:.1f}s...")
            time.sleep(delay)
    return None


def _clean_play_store_name(name):
    """Strip Play Store suffixes and HTML entities from an app name."""
    return clean_app_name(name)


def _fetch_app_details(package_name):
    """Fetch name + icon URL from Play Store. Returns {name, icon_url} or empty dicts."""
    url = PLAY_STORE_URL.format(package_name)
    html = _fetch_page_with_retry(url)
    if not html:
        return {}, {}

    name = ""
    icon_url = ""

    # Extract icon URL
    m = _OG_IMAGE_RE.search(html)
    if m:
        icon_url = m.group(1).strip()

    # Extract app name
    m = _OG_TITLE_RE.search(html)
    if m:
        name = _clean_play_store_name(m.group(1).strip())
    else:
        m = _TITLE_RE.search(html)
        if m:
            name = _clean_play_store_name(m.group(1).strip())

    return name, icon_url


def fetch_app_icon(package_name, skip_cache=False):
    """Fetch icon URL from cache or Play Store. Returns the URL string."""
    if not package_name or not isinstance(package_name, str):
        return ""

    pkg = package_name.lower().strip()
    cache = load_json(APP_CACHE_PATH, default={})

    if not skip_cache and pkg in cache:
        entry = cache[pkg]
        if isinstance(entry, dict):
            cached_icon = valid_icon_url(entry.get("icon_url"))
            if cached_icon:
                return cached_icon

    name, icon_url = _fetch_app_details(pkg)

    cache = load_json(APP_CACHE_PATH, default={})
    normalize_cache(cache)
    upsert(pkg, cache, name=name, name_source=PLAY_SOURCE,
           icon_url=icon_url, icon_source=PLAY_SOURCE)
    if not icon_url:
        entry = cache.get(pkg)
        if isinstance(entry, dict):
            # Remember the miss so the next runs skip this package instead of
            # re-requesting the same 404 every hour.
            entry["last_icon_attempt"] = int(time.time())
    save_json(APP_CACHE_PATH, cache)

    return icon_url


def enrich_parsed_bundles_with_icons(parsed_bundles):
    """Enrich apps with icon URLs from cache, fetching uncached ones."""
    all_packages = set()
    for record in parsed_bundles.values():
        for app in record.get("apps", []):
            pkg = app.get("package", "").lower().strip()
            if pkg:
                all_packages.add(pkg)

    cache = load_json(APP_CACHE_PATH, default={})
    normalize_cache(cache)
    cutoff = _icon_attempt_cutoff()
    uncached = [
        p for p in all_packages
        if not valid_icon_url((cache.get(p) or {}).get("icon_url"))
        and ((cache.get(p) or {}).get("last_icon_attempt") or 0) < cutoff
    ]
    uncached.sort(key=lambda p: (cache.get(p) or {}).get("last_icon_attempt") or 0)
    limit = _icon_fetch_limit()
    if len(uncached) > limit:
        print(
            f"[icons] {len(uncached)} uncached packages; fetching {limit} this run "
            f"({ICON_FETCH_LIMIT_ENV})"
        )
        uncached = uncached[:limit]

    if uncached:
        print(f"[icons] Fetching icons for {len(uncached)} uncached packages...")
        for i, pkg in enumerate(uncached):
            if i > 0 and i % 10 == 0:
                print(f"[icons] Progress: {i}/{len(uncached)}")
            fetch_app_icon(pkg)
            if i < len(uncached) - 1:
                time.sleep(random.uniform(0.3, 0.8))

        cache = load_json(APP_CACHE_PATH, default={})

    return parsed_bundles


def retry_empty_icons(max_packages=None):
    cache = load_json(APP_CACHE_PATH, default={})
    fixed = normalize_cache(cache)
    if fixed:
        print(f"[icons] Normalized {fixed} malformed cache entries")
        save_json(APP_CACHE_PATH, cache)

    empty_pkgs = [
        p for p, v in cache.items()
        if isinstance(v, dict) and not valid_icon_url(v.get("icon_url"))
    ]
    empty_pkgs.sort(key=lambda p: (
        cache[p].get("last_icon_attempt") or 0,
        cache[p].get("updated_at") or 0,
    ))
    if max_packages is not None:
        empty_pkgs = empty_pkgs[:max_packages]

    if not empty_pkgs:
        print("[icons] No empty entries to retry.")
        return

    print(f"[icons] Retrying {len(empty_pkgs)} previously failed packages...")
    success = 0
    attempted = []
    for i, pkg in enumerate(empty_pkgs):
        if i > 0 and i % 10 == 0:
            print(f"[icons] Retry progress: {i}/{len(empty_pkgs)} ({success} recovered)")
        result = fetch_app_icon(pkg, skip_cache=True)
        if result:
            success += 1
        else:
            attempted.append(pkg)
        if i < len(empty_pkgs) - 1:
            time.sleep(random.uniform(0.5, 1.2))

    cache = load_json(APP_CACHE_PATH, default={})
    normalize_cache(cache)
    now = int(time.time())
    for pkg in attempted:
        entry = cache.get(pkg)
        if isinstance(entry, dict) and not valid_icon_url(entry.get("icon_url")):
            entry["last_icon_attempt"] = now
    save_json(APP_CACHE_PATH, cache)
    print(f"[icons] Retry done: {success}/{len(empty_pkgs)} recovered")


def fetch_and_cache_app_name(package_name):
    """Fetch app name from cache or Play Store. Returns the name or empty string."""
    if not package_name or not isinstance(package_name, str):
        return ""

    pkg = package_name.lower().strip()
    cache = load_json(APP_CACHE_PATH, default={})

    if pkg in cache:
        entry = cache[pkg]
        if isinstance(entry, dict):
            return entry.get("name", "")
        return ""

    name, icon_url = _fetch_app_details(pkg)

    cache = load_json(APP_CACHE_PATH, default={})
    normalize_cache(cache)
    upsert(pkg, cache, name=name, name_source=PLAY_SOURCE,
           icon_url=icon_url, icon_source=PLAY_SOURCE)
    save_json(APP_CACHE_PATH, cache)

    return name or ""


def enrich_parsed_bundles_with_names(parsed_bundles):
    """Enrich app names with Play Store names, falling back to existing names."""
    all_packages = set()
    for record in parsed_bundles.values():
        for app in record.get("apps", []):
            pkg = app.get("package", "").lower().strip()
            if pkg:
                all_packages.add(pkg)

    cache = load_json(APP_CACHE_PATH, default={})
    uncached = [p for p in all_packages if not is_real_name(_cached_name(cache, p), p)]

    if uncached:
        print(f"[names] Fetching Play Store names for {len(uncached)} uncached packages...")
        for i, pkg in enumerate(uncached):
            if i > 0 and i % 10 == 0:
                print(f"[names] Progress: {i}/{len(uncached)}")
            fetch_and_cache_app_name(pkg)
            if i < len(uncached) - 1:
                time.sleep(random.uniform(0.3, 0.8))

        cache = load_json(APP_CACHE_PATH, default={})

    apply_resolved_names(parsed_bundles, cache)
    return parsed_bundles


def _cached_name(cache, pkg):
    entry = cache.get(pkg)
    return entry.get("name", "") if isinstance(entry, dict) else ""


def apply_resolved_names(parsed_bundles, cache=None):
    """Write the best available name into bundle app records.

    Bundle metadata only contributes a name when it is a real one; verified
    store names win, and packages with no metadata keep whatever they had
    (never the raw package as a display name — readers resolve that lazily).
    """
    cache = load_json(APP_CACHE_PATH, default={}) if cache is None else cache
    for record in parsed_bundles.values():
        for app in record.get("apps", []):
            pkg = app.get("package", "").lower().strip()
            existing = app.get("app_name", "")
            name, _source = resolve_name(pkg, cache, existing)
            if name and name != existing:
                app["app_name"] = name
    return parsed_bundles


def enrich_parsed_bundles_with_store_fallbacks(parsed_bundles):
    """Query F-Droid for packages Play Store could not resolve.

    Runs after the Play passes, so only genuinely unresolved packages cost a
    request. Both names and icons land in the registry (app_cache.json), which
    is what the site and the digest read.
    """
    all_packages = set()
    for record in parsed_bundles.values():
        for app in record.get("apps", []):
            pkg = app.get("package", "").lower().strip()
            if pkg:
                all_packages.add(pkg)

    cache = load_json(APP_CACHE_PATH, default={})
    normalize_cache(cache)
    try:
        import fdroid_source
        fdroid_source.enrich_unresolved(cache, all_packages)
    except Exception as e:
        print(f"[-] F-Droid lookup failed (non-fatal): {e}")
        return parsed_bundles

    save_json(APP_CACHE_PATH, cache)
    apply_resolved_names(parsed_bundles, cache)
    return parsed_bundles


def enrich_parsed_bundles_with_apkmirror_fallbacks(parsed_bundles):
    """Query APKMirror search for packages Play and F-Droid could not resolve."""
    all_packages = set()
    for record in parsed_bundles.values():
        for app in record.get("apps", []):
            pkg = app.get("package", "").lower().strip()
            if pkg:
                all_packages.add(pkg)

    cache = load_json(APP_CACHE_PATH, default={})
    normalize_cache(cache)
    try:
        import apkmirror_source
        apkmirror_source.enrich_unresolved(cache, all_packages)
    except Exception as e:
        print(f"[-] APKMirror lookup failed (non-fatal): {e}")
        return parsed_bundles

    save_json(APP_CACHE_PATH, cache)
    apply_resolved_names(parsed_bundles, cache)
    return parsed_bundles


def apply_parsed_bundle_icon_overrides(parsed_bundles):
    """Apply data/state/icon_overrides.json after every automatic source."""
    cache = load_json(APP_CACHE_PATH, default={})
    normalize_cache(cache)
    applied = apply_icon_overrides(cache)
    if applied:
        save_json(APP_CACHE_PATH, cache)
        apply_resolved_names(parsed_bundles, cache)
        print(f"[icons] Applied {applied} manual overrides")
    return parsed_bundles


def fetch_app_name(package_name, fallback_name=""):
    """Fetch the app display name from Google Play Store (uses cache)."""
    name = fetch_and_cache_app_name(package_name)
    return name if name else fallback_name


if __name__ == "__main__":
    if "--retry-empty" in sys.argv:
        retry_empty_icons()
    elif "--stats" in sys.argv:
        cache = load_json(APP_CACHE_PATH, default={})
        total = len(cache)
        with_icon = sum(1 for v in cache.values() if isinstance(v, dict) and v.get("icon_url"))
        with_name = sum(1 for v in cache.values() if isinstance(v, dict) and v.get("name"))
        empty = total - with_icon
        print(f"Total: {total} | with icon: {with_icon} | with name: {with_name} | missing icon: {empty}")
    else:
        test_pkg = "com.instagram.android"
        icon = fetch_app_icon(test_pkg)
        print(f"Icon for {test_pkg}: {icon or '(not found)'}")
        name = fetch_app_name(test_pkg, "Instagram")
        print(f"Name for {test_pkg}: {name or '(not found)'}")
