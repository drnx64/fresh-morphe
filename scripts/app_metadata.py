"""Resolve app display names and icon URLs from verified metadata sources.

The pipeline learns about apps only through patch bundles, so many packages
never appear on Google Play (forks, F-Droid-only apps, region-locked builds).
This module owns the fallback ladder so every surface — bundle data, the site's
app_cache.json, RSS and the Telegram digest — agrees on one answer and never
prints a raw package identifier as a name.

Name priority (highest first):
  1. verified store name from app_cache.json (Play Store og:title, F-Droid)
  2. bundle-provided app_name, when it is a real name and not the package id
  3. name derived from the package identifier (com.instagram.android -> Instagram)

Icon priority:
  1. verified store icon URL from app_cache.json (Play Store, F-Droid)
  2. none — the frontend draws an initial-letter tile, no request is made

app_cache.json is the persistent registry: every entry keeps `name`,
`icon_url`, the provenance of each (`name_source`, `icon_source`) and
`updated_at`, so a bad source can be audited and refreshed without guessing.
The file is already published by the build, so the frontend needs no change.
"""
import html
import os
import re
import time
from urllib.parse import quote

from state_manager import STATE_DIR, load_json, save_json

APP_CACHE_PATH = os.path.join(STATE_DIR, "app_cache.json")

PLAY_APP_URL = "https://play.google.com/store/apps/details?id={}"
FDROID_PAGE_URL = "https://f-droid.org/en/packages/{}/"
APKMIRROR_SEARCH_URL = "https://www.apkmirror.com/?s={}"

STORE_PLAY = "play"
STORE_FDROID = "fdroid"
STORE_APKMIRROR = "apkmirror"

PLAY_SOURCE = "play"
FDROID_SOURCE = "fdroid"
BUNDLE_SOURCE = "bundle"
DERIVED_SOURCE = "derived"
MANUAL_SOURCE = "manual"

ICON_OVERRIDES_PATH = os.path.join(STATE_DIR, "icon_overrides.json")

# Package segments that describe the platform or a distribution flavour rather
# than the app itself. Dropped before picking the most specific segment.
GENERIC_SEGMENTS = frozenset({
    "android", "app", "apps", "application", "mobile", "client", "free",
    "pro", "lite", "plus", "hd", "tv", "beta", "release", "demo", "premium",
    "core", "official", "web", "api", "lib", "libs", "common", "base",
    "test", "tests",
})

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WHITESPACE_RE = re.compile(r"\s+")
_FDROID_SUFFIX_RE = re.compile(r"\s*[|\u2013-]\s*F-Droid.*$", re.IGNORECASE | re.DOTALL)


def derive_name_from_package(package_name):
    """Turn a package identifier into a presentable name.

    `com.instagram.android` -> `Instagram`, `com.laurencedawson.reddit_sync`
    -> `Reddit Sync`, `myapp` -> `Myapp`. Never returns an empty string, so
    callers can sort and render without guards.
    """
    raw = package_name if isinstance(package_name, str) else ""
    raw = raw.strip()
    if not raw:
        return "Unknown app"

    parts = [p for p in raw.split(".") if p]
    if not parts:
        return "Unknown app"
    candidates = (parts[1:] if len(parts) > 1 else parts)
    meaningful = [p for p in candidates if p.lower() not in GENERIC_SEGMENTS]
    pick = meaningful[-1] if meaningful else parts[-1]

    words = []
    for chunk in re.split(r"[_\-\s]+", pick):
        if chunk:
            words.extend(part for part in _CAMEL_RE.sub(" ", chunk).split(" ") if part)
    name = " ".join(w[:1].upper() + w[1:] for w in words).strip()
    name = _WHITESPACE_RE.sub(" ", name)
    if not name:
        return "Unknown app"
    return name


def clean_app_name(name):
    """Normalise a scraped store name; return "" when it is unusable.

    Play Store page titles arrive HTML-escaped (`&amp;`) and sometimes carry a
    store suffix, so they need unescaping and trimming before display.
    """
    if not isinstance(name, str):
        return ""
    text = html.unescape(name)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        return ""
    text = _FDROID_SUFFIX_RE.sub("", text).strip()
    for suffix in (" - Apps on Google Play", " - Google Play"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    return text


def is_real_name(name, package_name):
    """True when `name` looks like a name rather than the package echoed back."""
    if not isinstance(name, str):
        return False
    candidate = name.strip()
    if not candidate:
        return False
    if package_name and candidate.lower() == str(package_name).lower().strip():
        return False
    return any(ch.isalpha() for ch in candidate)


def _entry_parts(cache, package_name):
    entry = cache.get(package_name)
    return entry if isinstance(entry, dict) else {}


def resolve_name(package_name, cache=None, bundle_name=""):
    """Return `(display_name, source)`; always a non-empty name."""
    pkg = (package_name or "").lower().strip()
    entry = _entry_parts(cache, pkg) if isinstance(cache, dict) else {}

    verified = clean_app_name(entry.get("name", ""))
    if is_real_name(verified, pkg):
        return verified, entry.get("name_source") or PLAY_SOURCE

    from_bundle = clean_app_name(bundle_name)
    if is_real_name(from_bundle, pkg):
        return from_bundle, BUNDLE_SOURCE

    return derive_name_from_package(pkg), DERIVED_SOURCE


def valid_icon_url(value):
    """Return a usable HTTP(S) icon URL, or "" for malformed cache values."""
    if isinstance(value, str):
        value = value.strip()
        if value.startswith(("http://", "https://")):
            return value
    return ""


def resolve_icon(package_name, cache=None):
    """Return `(icon_url, source)`; the URL may be "" (frontend fallback tile)."""
    pkg = (package_name or "").lower().strip()
    entry = _entry_parts(cache, pkg) if isinstance(cache, dict) else {}
    url = valid_icon_url(entry.get("icon_url"))
    if url:
        return url, entry.get("icon_source") or PLAY_SOURCE
    return "", ""


def store_link(package_name, cache=None):
    """Return `(url, store)` for an app's store page.

    Play Store when the registry provenance says Play (legacy entries without
    provenance count, since they were scraped from Play), the F-Droid package
    page when only F-Droid resolved the app, and an APKMirror search as the
    last resort — it needs no slug lookup and never 404s on a valid query.
    """
    pkg = (package_name or "").lower().strip()
    if not pkg:
        return "", ""

    entry = _entry_parts(cache, pkg) if isinstance(cache, dict) else {}
    sources = {entry.get("name_source", ""), entry.get("icon_source", "")}
    quoted = quote(pkg, safe="")

    if PLAY_SOURCE in sources:
        return PLAY_APP_URL.format(quoted), STORE_PLAY
    if FDROID_SOURCE in sources:
        return FDROID_PAGE_URL.format(quoted), STORE_FDROID
    if STORE_APKMIRROR in sources:
        return APKMIRROR_SEARCH_URL.format(quoted), STORE_APKMIRROR
    if entry.get("name") or entry.get("icon_url"):
        return PLAY_APP_URL.format(quoted), STORE_PLAY
    return APKMIRROR_SEARCH_URL.format(quoted), STORE_APKMIRROR


def normalize_cache(cache):
    """Repair malformed entries in place and return the number changed."""
    if not isinstance(cache, dict):
        return 0
    changed = 0
    for pkg, entry in list(cache.items()):
        entry_changed = False
        if not isinstance(entry, dict):
            cache[pkg] = {}
            entry = cache[pkg]
            entry_changed = True
        clean = valid_icon_url(entry.get("icon_url"))
        if entry.get("icon_url") != clean or "icon_url" not in entry:
            entry["icon_url"] = clean
            entry_changed = True
        if entry_changed:
            changed += 1
    return changed


def load_cache(path=APP_CACHE_PATH):
    """Load the registry, repairing malformed icon values in memory."""
    data = load_json(path, default={})
    if not isinstance(data, dict):
        return {}
    normalize_cache(data)
    return data


def upsert(package_name, cache, name="", name_source="", icon_url="", icon_source=""):
    """Record resolved metadata in the registry (mutates `cache` in place).

    Empty values never overwrite a previously verified one — sources are
    probed in priority order, so a later miss must not erase an earlier hit.
    """
    pkg = (package_name or "").lower().strip()
    if not pkg:
        return cache

    entry = cache.get(pkg)
    if not isinstance(entry, dict):
        entry = {}
        cache[pkg] = entry

    changed = False
    clean_name = clean_app_name(name)
    if is_real_name(clean_name, pkg) and clean_name != entry.get("name"):
        entry["name"] = clean_name
        entry["name_source"] = name_source or entry.get("name_source", "")
        changed = True
    icon_url = valid_icon_url(icon_url)
    if icon_url:
        if icon_url != entry.get("icon_url"):
            entry["icon_url"] = icon_url
            entry["icon_source"] = icon_source or entry.get("icon_source", "")
            changed = True

    if changed:
        entry["updated_at"] = int(time.time())
    entry.setdefault("icon_url", "")
    return cache


def save_cache(cache, path=APP_CACHE_PATH):
    normalize_cache(cache)
    save_json(path, cache)


def load_overrides(path=ICON_OVERRIDES_PATH):
    """Load manual icon/name overrides keyed by package."""
    data = load_json(path, default={})
    return data if isinstance(data, dict) else {}


def apply_icon_overrides(cache, path=ICON_OVERRIDES_PATH):
    """Apply manual overrides on top of every store source (mutates cache)."""
    overrides = load_overrides(path)
    applied = 0
    for pkg, value in overrides.items():
        if not isinstance(pkg, str) or not pkg.strip():
            continue
        if isinstance(value, str):
            value = {"icon_url": value}
        if not isinstance(value, dict):
            continue
        icon_url = valid_icon_url(value.get("icon_url"))
        name = value.get("name", "")
        if not icon_url and not name:
            continue
        upsert(
            pkg, cache,
            name=name, name_source=MANUAL_SOURCE if name else "",
            icon_url=icon_url, icon_source=MANUAL_SOURCE if icon_url else "",
        )
        applied += 1
    return applied


def is_unresolved(cache, package_name):
    """True when the registry has neither a real name nor an icon URL."""
    pkg = (package_name or "").lower().strip()
    entry = _entry_parts(cache, pkg)
    name_ok = is_real_name(clean_app_name(entry.get("name", "")), pkg)
    icon_ok = bool(valid_icon_url(entry.get("icon_url")))
    return not (name_ok and icon_ok)
