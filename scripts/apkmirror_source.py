"""APKMirror search fallback for apps with no Play Store or F-Droid page.

Only the public search page is used - individual APKMirror app pages sit behind
Cloudflare - and a result is accepted only when its title matches the name
derived from the package identifier. Search results for unknown packages are
often unrelated popular apps, so a loose "first row" match would poison the
registry.

Answers are cached in data/state/apkmirror_cache.json. A transient failure
(403, Cloudflare challenge, timeout) is never cached as "not found"; a search
that completed without a matching row is remembered for NEGATIVE_TTL_DAYS.
"""
import html
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app_metadata
from state_manager import STATE_DIR, load_json, save_json

CACHE_PATH = os.path.join(STATE_DIR, "apkmirror_cache.json")
SEARCH_URL = "https://www.apkmirror.com/?s={}"

CACHE_TTL_DAYS = 14
NEGATIVE_TTL_DAYS = 3
MAX_RETRIES = 2
BASE_DELAY = 0.8
REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.apkmirror.com/",
}

_ROW_RE = re.compile(r'<div class="appRow">(.*?)(?=<div class="appRow">|$)', re.S)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
_ALT_RE = re.compile(r'\balt="([^"]*)"', re.I)
_SRC_RE = re.compile(r'\bsrc="([^"]*)"', re.I)
_VERSION_RE = re.compile(r"\s+v?\d[\w.\-+]*$")
_CHALLENGE_MARKERS = (
    "just a moment",
    "enable javascript and cookies to continue",
    "cf-challenge",
    "attention required! | cloudflare",
)


class ApkmirrorUnavailable(Exception):
    """Raised when APKMirror could not be read for a transient reason."""


def _http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _is_challenge(page):
    lower = page.lower()
    return any(marker in lower for marker in _CHALLENGE_MARKERS)


def _normalize_name(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())


def strip_version(title):
    """Drop a trailing version number from an APKMirror result title."""
    text = html.unescape(str(title or "")).strip()
    return _VERSION_RE.sub("", text).strip() or text


def name_matches(package_name, title):
    """True when a result title is the app derived from the package id."""
    derived = _normalize_name(app_metadata.derive_name_from_package(package_name))
    candidate = _normalize_name(strip_version(title))
    if len(derived) < 4 or not candidate:
        return False
    return candidate == derived or candidate.startswith(derived)


def _icon_url(src):
    url = html.unescape(str(src or "")).strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://www.apkmirror.com" + url
    if not url.startswith(("http://", "https://")):
        return ""
    return re.sub(r"([?&])w=\d+", r"\1w=256", re.sub(r"([?&])h=\d+", r"\1h=256", url))


def parse_results(package_name, page):
    """Return `(name, icon_url)` for the first matching search row, or ("", "")."""
    for row in _ROW_RE.findall(page or ""):
        for image in _IMG_RE.findall(row):
            alt = _ALT_RE.search(image)
            src = _SRC_RE.search(image)
            if not alt or not src:
                continue
            title = html.unescape(alt.group(1)).strip()
            if not name_matches(package_name, title):
                continue
            icon = _icon_url(src.group(1))
            if icon:
                return strip_version(title), icon
    return "", ""


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
    """Look up one package on APKMirror using the search-page cache."""
    pkg = (pkg or "").lower().strip()
    if not pkg:
        return {"name": "", "icon_url": "", "found": False}

    cache = _load_cache() if cache is None else cache
    cached = cache.get(pkg)
    if isinstance(cached, dict):
        ttl = CACHE_TTL_DAYS if cached.get("found") else NEGATIVE_TTL_DAYS
        if _cache_entry_fresh(cached, ttl):
            return cached

    derived = app_metadata.derive_name_from_package(pkg)
    queries = [pkg]
    if derived and derived != "Unknown app" and _normalize_name(derived) != _normalize_name(pkg):
        queries.append(derived)

    last_error = None
    completed = False
    for query in queries:
        url = SEARCH_URL.format(urllib.parse.quote(query, safe=""))
        for attempt in range(MAX_RETRIES + 1):
            try:
                page = _http_get(url)
            except urllib.error.HTTPError as err:
                last_error = err
                if err.code in (403, 429):
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as err:
                last_error = err
            else:
                if _is_challenge(page):
                    last_error = "cloudflare challenge"
                    break
                completed = True
                name, icon_url = parse_results(pkg, page)
                if name or icon_url:
                    result = {
                        "name": name,
                        "icon_url": icon_url,
                        "found": True,
                        "checked_at": int(time.time()),
                    }
                    cache[pkg] = result
                    return result
                break

            if attempt < MAX_RETRIES:
                time.sleep(BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5))

    if completed:
        result = {
            "name": "",
            "icon_url": "",
            "found": False,
            "checked_at": int(time.time()),
        }
        cache[pkg] = result
        return result

    raise ApkmirrorUnavailable(f"{pkg}: {last_error}")


def enrich_unresolved(cache, packages=None, sleep=True, max_lookups=None):
    """Fill names/icons for unresolved packages from APKMirror search."""
    wanted = sorted(set(packages or cache.keys()))
    pending = [p for p in wanted if app_metadata.is_unresolved(cache, p)]
    if max_lookups is not None:
        pending = pending[:max_lookups]
    if not pending:
        print("[apkmirror] No unresolved packages to look up")
        return 0

    print(f"[apkmirror] Looking up {len(pending)} unresolved packages...")
    mirror_cache = _load_cache()
    improved = 0
    for i, pkg in enumerate(pending):
        try:
            result = fetch_app(pkg, mirror_cache)
        except ApkmirrorUnavailable as err:
            print(f"[apkmirror] {err} (will retry next run)")
            continue

        if result.get("name") or result.get("icon_url"):
            app_metadata.upsert(
                pkg, cache,
                name=result.get("name", ""), name_source=app_metadata.STORE_APKMIRROR,
                icon_url=result.get("icon_url", ""), icon_source=app_metadata.STORE_APKMIRROR,
            )
            improved += 1
        if sleep and i < len(pending) - 1:
            time.sleep(BASE_DELAY + random.uniform(0, 0.5))

    save_json(CACHE_PATH, mirror_cache)
    print(f"[apkmirror] Resolved {improved}/{len(pending)} packages")
    return improved


def main():
    if "--stats" in sys.argv:
        cache = app_metadata.load_cache()
        mirror_cache = _load_cache()
        icons = sum(
            1 for entry in cache.values()
            if isinstance(entry, dict)
            and entry.get("icon_source") == app_metadata.STORE_APKMIRROR
        )
        print(f"[apkmirror] registry: {len(cache)} entries | icons from APKMirror: {icons}")
        print(f"[apkmirror] lookup cache: {len(mirror_cache)} packages")
        return 0

    pkgs = [a for a in sys.argv[1:] if not a.startswith("-")]
    cache = app_metadata.load_cache()
    if pkgs:
        mirror_cache = _load_cache()
        for pkg in pkgs:
            try:
                print(pkg, "->", fetch_app(pkg, mirror_cache))
            except ApkmirrorUnavailable as err:
                print(pkg, "-> unavailable:", err)
        save_json(CACHE_PATH, mirror_cache)
        return 0

    enrich_unresolved(cache)
    app_metadata.save_cache(cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
