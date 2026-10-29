"""APKMirror fallback for apps with no Play Store or F-Droid page.

A result is accepted only when it can be proven to be the requested package.
Search rows are matched by title first (cheap, no extra request); when that
fails - which is common, because the name derived from the package id is often
not the app's real name ("Sync for reddit" ships as `reddit_sync`) - the
candidate app pages are fetched and the page must name the exact package id.
That check cannot be satisfied by an unrelated app, which matters because
APKMirror pads every search with popular apps: an unknown package still returns
40-50 rows, mostly CapCut.

Answers are cached in data/state/apkmirror_cache.json. A transient failure
(403, Cloudflare challenge, timeout) is never cached as "not found"; a search
that completed without a verified match is remembered for NEGATIVE_TTL_DAYS.
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
APP_BASE = "https://www.apkmirror.com"
# Distinct app pages fetched to prove one package. Search order puts the real
# match first and popular apps after it, so two attempts covers the realistic
# case without turning a miss into a crawl.
MAX_APP_PAGES = 2
# Hard ceiling on app-page fetches per process. Each page is ~340 KB, and a
# negative written before package-id verification could not see the app page at
# all, so those get re-checked - this keeps that sweep from becoming a crawl.
MAX_APP_PAGES_PER_RUN = 60
# Bumped when the acceptance rule changes. A negative recorded under an older
# rule is not evidence of absence, so it is re-checked instead of trusted.
VERIFY_STAGE = 2

_pages_fetched = 0

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
_APP_LINK_RE = re.compile(r'href="(/apk/[^"#?]+)')
_APP_TITLE_RE = re.compile(r'<h1[^>]*class="[^"]*app-title[^"]*"[^>]*>(.*?)</h1>', re.S)
_PRIMARY_IMAGE_RE = re.compile(r"<img\b[^>]*\bid=\"primaryimage\"[^>]*>", re.I)
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


def _package_id_re(package_name):
    """Exact package id, bounded so a sibling variant cannot satisfy it."""
    return re.compile(
        r"(?<![A-Za-z0-9._])" + re.escape(package_name) + r"(?![A-Za-z0-9._])"
    )


def package_id_present(package_name, page):
    """True when `page` names this exact package.

    APKMirror app pages link back to the Play Store listing, and the package id
    rides in that href. The boundaries matter: a page for `...reddit_sync` must
    not validate `...reddit_sync.pro`, and vice versa.
    """
    return _package_id_re(str(package_name or "").strip().lower()).search(page or "") is not None


def app_slugs(page, limit=MAX_APP_PAGES):
    """Distinct `/apk/{developer}/{app}/` links from the search rows, in order.

    Only 3+ segment paths count: `/apk/{developer}/` is a developer index, not
    an app, and using it would verify against the wrong page.
    """
    slugs = []
    for row in _ROW_RE.findall(page or ""):
        link = _APP_LINK_RE.search(row)
        if not link:
            continue
        segments = [part for part in link.group(1).split("/") if part]
        if len(segments) < 3:
            continue
        slug = "/" + "/".join(segments[:3]) + "/"
        if slug in slugs:
            continue
        slugs.append(slug)
        if len(slugs) >= limit:
            break
    return slugs


def parse_app_page(page):
    """Return `(name, icon_url)` read from an APKMirror app page."""
    title = _APP_TITLE_RE.search(page or "")
    name = html.unescape(title.group(1)).strip() if title else ""
    icon_url = ""
    image = _PRIMARY_IMAGE_RE.search(page or "")
    if image:
        src = _SRC_RE.search(image.group(0))
        if src:
            icon_url = _icon_url(src.group(1))
    return name, icon_url


def resolve_by_package_page(package_name, search_page):
    """Prove a search hit by package id, then read its name and icon.

    Needed because `parse_results` compares the row title against the name
    derived from the package id, and those often differ: `reddit_sync` derives
    "Reddit Sync" while the app is titled "Sync for reddit", so a real hit is
    rejected. A page naming the exact package id cannot be a different app.

    Returns `("", "")` when no candidate carries the package id. Raises
    `ApkmirrorUnavailable` on a transient failure so the miss is never cached
    as permanent.
    """
    slugs = app_slugs(search_page)
    for index, slug in enumerate(slugs):
        global _pages_fetched
        if _pages_fetched >= MAX_APP_PAGES_PER_RUN:
            raise ApkmirrorUnavailable(
                f"{package_name}: app-page budget spent for this run"
            )
        _pages_fetched += 1
        try:
            app_page = _http_get(APP_BASE + slug)
        except urllib.error.HTTPError as err:
            if err.code in (403, 429):
                raise ApkmirrorUnavailable(f"{package_name}: {slug} HTTP {err.code}")
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise ApkmirrorUnavailable(f"{package_name}: {slug} {err}")
        if _is_challenge(app_page):
            raise ApkmirrorUnavailable(f"{package_name}: {slug} cloudflare challenge")
        if package_id_present(package_name, app_page):
            name, icon_url = parse_app_page(app_page)
            if name or icon_url:
                return name, icon_url
        if index < len(slugs) - 1:
            time.sleep(BASE_DELAY + random.uniform(0, 0.5))
    return "", ""


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
        # A negative recorded before package-id verification never saw the app
        # page, so it cannot prove the package is absent. Re-check it instead.
        trustworthy = cached.get("found") or cached.get("verify_stage") == VERIFY_STAGE
        if trustworthy and _cache_entry_fresh(cached, ttl):
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
                if not (name or icon_url):
                    # Title matching failed - possibly a real hit whose title
                    # differs from the package-derived name. Verify by package id.
                    name, icon_url = resolve_by_package_page(pkg, page)
                if name or icon_url:
                    result = {
                        "name": name,
                        "icon_url": icon_url,
                        "found": True,
                        "checked_at": int(time.time()),
                        "verify_stage": VERIFY_STAGE,
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
            "verify_stage": VERIFY_STAGE,
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
