"""Client for the Patch Bundle Registry API.

The old Jman ``patch-bundles`` branch was retired. The registry exposes the
same Morphe bundles as channel pointers plus release-level metadata.
"""
import os
import time
import urllib.error
import urllib.parse

from http_utils import fetch_url

REGISTRY_BASE = os.environ.get(
    "PATCH_REGISTRY_BASE", "https://patch-bundles.api.jmancentral.com"
).rstrip("/")
MORPHE_BUNDLE_TYPE = "Morphe:V1"
CHANNELS = ("stable", "latest", "dev")
PAGE_SIZE = 100


def _get_json(path, params=None, timeout=60):
    url = REGISTRY_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(3):
        try:
            return fetch_url(url, timeout=timeout, as_json=True)
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise last_error or RuntimeError(f"Failed to fetch {url}")


def health():
    return _get_json("/health")


def fetch_channel_bundles(channel, bundle_type=MORPHE_BUNDLE_TYPE, page_size=PAGE_SIZE):
    """Return every current bundle pointer for one release channel."""
    if channel not in CHANNELS:
        raise ValueError(f"Unsupported registry channel: {channel}")

    rows = []
    offset = 0
    while True:
        page = _get_json(
            "/bundles",
            {
                "bundle_type": bundle_type,
                "channel": channel,
                "limit": page_size,
                "offset": offset,
            },
        )
        data = page.get("data") or []
        rows.extend(data)
        offset += len(data)
        total = page.get("total")
        if not data or (isinstance(total, int) and offset >= total):
            break
        if offset > 10000:
            raise RuntimeError(
                f"Registry pagination exceeded the API offset limit for {channel}"
            )
    return rows


def fetch_current_morphe_channels(channels=CHANNELS, bundle_type=MORPHE_BUNDLE_TYPE):
    """Fetch current channel pointers for all Morphe bundles.

    Returns ``{channel: [bundle, ...]}``. Any failed channel raises so the
    caller never writes a partial tree (which would look like removals).
    """
    return {
        channel: fetch_channel_bundles(channel, bundle_type=bundle_type)
        for channel in channels
    }

_GENERATION_CACHE = {"value": None, "expires": 0.0}


def get_generation(refresh=False):
    """Return the current catalog generation, cached briefly."""
    now = time.time()
    if not refresh and _GENERATION_CACHE["value"] and now < _GENERATION_CACHE["expires"]:
        return _GENERATION_CACHE["value"]
    generation = health().get("generation")
    if generation:
        _GENERATION_CACHE["value"] = generation
        _GENERATION_CACHE["expires"] = now + 60
    return generation


def fetch_catalog_patches(bundle_id, generation=None):
    """Return a complete verified patch list for one bundle.

    Uses the generation-aware ``/catalog/patches`` cursor API. Returns ``None``
    when the registry has no verified patch list for the bundle.
    """
    generation = generation or get_generation()
    rows = []
    cursor = 0
    generation_retries = 0
    while True:
        try:
            page = _get_json(
                "/catalog/patches",
                {
                    "bundle_id": bundle_id,
                    "generation": generation,
                    "limit": 100,
                    "page_cursor": cursor,
                    "compact": "true",
                },
            )
        except urllib.error.HTTPError as e:
            if e.code == 409 and generation_retries < 2:
                generation_retries += 1
                generation = get_generation(refresh=True)
                rows = []
                cursor = 0
                continue
            if e.code == 404:
                return None
            raise
        rows.extend(page.get("rows") or [])
        next_cursor = page.get("next_cursor")
        if next_cursor is None:
            break
        cursor = next_cursor
    return rows
