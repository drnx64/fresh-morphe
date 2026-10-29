"""Tests for apkmirror_source.py - search parsing and caching (no network)."""
import sys
import os
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import apkmirror_source as am
import app_metadata as m

ROW = """
<div class="appRow">
  <img alt="Instagram 452.1.0.53.64"
       src="https://www.apkmirror.com/wp-content/themes/APKMirror/ap_resize/ap_resize.php?src=https%3A%2F%2Fdownloadr2.apkmirror.com%2Fwp-content%2Fuploads%2F2022%2F08%2F91%2F6309b6f6b92ef.png&amp;w=32&amp;h=32&amp;q=100">
  <h5><a href="/apk/instagram/instagram/">Instagram 452.1.0.53.64</a></h5>
</div>
"""

NO_MATCH = """
<div class="appRow">
  <img alt="CapCut - Video Editor 19.7.0" src="https://www.apkmirror.com/i/capcut.png?w=32&amp;h=32">
</div>
"""


def test_parse_results_matches_derived_name():
    name, icon = am.parse_results("com.instagram.android", ROW)
    assert name == "Instagram"
    assert icon.startswith("https://www.apkmirror.com/")
    assert "w=256" in icon and "h=256" in icon
    assert "&amp;" not in icon


def test_parse_results_rejects_unrelated_popular_results():
    assert am.parse_results("com.snaptube.premium", NO_MATCH) == ("", "")


def test_name_matches_is_case_and_punctuation_insensitive():
    assert am.name_matches("com.instagram.android", "Instagram 452.1.0")
    assert am.name_matches("com.google.android.youtube", "YouTube 21.40.161")
    assert not am.name_matches("com.snaptube.premium", "CapCut - Video Editor 19.7.0")


def test_fetch_app_caches_negative_result(monkeypatch):
    monkeypatch.setattr(am, "_http_get", lambda url: NO_MATCH)
    cache = {}
    result = am.fetch_app("com.snaptube.premium", cache)
    assert result["found"] is False
    assert cache["com.snaptube.premium"]["found"] is False
    assert isinstance(cache["com.snaptube.premium"]["checked_at"], int)


def test_fetch_app_does_not_cache_transient_403(monkeypatch):
    def blocked(url):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(am, "_http_get", blocked)
    monkeypatch.setattr(am, "MAX_RETRIES", 0)
    cache = {}
    try:
        am.fetch_app("com.instagram.android", cache)
    except am.ApkmirrorUnavailable:
        pass
    else:
        raise AssertionError("expected ApkmirrorUnavailable")
    assert cache == {}


def test_enrich_records_apkmirror_provenance(monkeypatch):
    monkeypatch.setattr(am, "_http_get", lambda url: ROW)
    monkeypatch.setattr(am, "save_json", lambda *args, **kwargs: None)
    cache = {"com.instagram.android": {"name": "", "icon_url": ""}}
    assert am.enrich_unresolved(cache, sleep=False) == 1
    entry = cache["com.instagram.android"]
    assert entry["name"] == "Instagram"
    assert entry["name_source"] == m.STORE_APKMIRROR
    assert entry["icon_source"] == m.STORE_APKMIRROR


# ── package-id verification (title match misses real hits) ─────────────────

# "Sync for reddit" is published as com.laurencedawson.reddit_sync, which derives
# the name "Reddit Sync" - so the title gate rejects a correct search hit.
SYNC_SEARCH = """
<div class="appRow">
  <img alt="Sync for reddit v23.06.30-13:39" src="https://www.apkmirror.com/i/sync.png?w=32&amp;h=32">
  <h5><a href="/apk/red-apps-ltd/sync-for-reddit/sync-for-reddit-v23-06-30-1339-release/#disqus_thread">Sync for reddit</a></h5>
</div>
<div class="appRow">
  <img alt="CapCut - Video Editor 19.7.0" src="https://www.apkmirror.com/i/capcut.png?w=32&amp;h=32">
  <h5><a href="/apk/bytedance-pte-ltd/capcut/capcut-19-7-0-release/#disqus_thread">CapCut</a></h5>
</div>
"""

SYNC_APP_PAGE = """
<header>
  <div class="icon-container"><img class="ellipsisText" style="width:96px; height:96px;"
    alt="Sync for reddit" id="primaryimage"
    src="/wp-content/themes/APKMirror/ap_resize/ap_resize.php?src=https%3A%2F%2Fdownloadr2.apkmirror.com%2F62.png&amp;w=96&amp;h=96&amp;q=100"></div>
  <h1 title="Sync for reddit" class="marginZero wrapText app-title fontBlack noHover">Sync for reddit</h1>
</header>
<a href="https://play.google.com/store/apps/details?id=com.laurencedawson.reddit_sync" class="accent_color">Play Store</a>
"""

CAPCUT_APP_PAGE = """
<header>
  <img id="primaryimage" src="/wp-content/themes/APKMirror/ap_resize/ap_resize.php?src=x.png&amp;w=96&amp;h=96">
  <h1 class="app-title">CapCut</h1>
</header>
<a href="https://play.google.com/store/apps/details?id=com.bytedance.capcut">Play Store</a>
"""


def _stub_pages(search_page, app_pages):
    """Fake _http_get: search URLs return the search page, /apk/ URLs the rest."""
    def _get(url):
        if "?s=" in url:
            return search_page
        slug = url.replace(am.APP_BASE, "")
        if slug in app_pages:
            return app_pages[slug]
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    return _get


def _no_sleep(monkeypatch):
    monkeypatch.setattr(am, "BASE_DELAY", 0)
    monkeypatch.setattr(am.random, "uniform", lambda a, b: 0)


def test_app_slugs_skips_developer_only_links():
    page = '<div class="appRow"><a href="/apk/red-apps-ltd/">dev</a></div>'
    assert am.app_slugs(page) == []


def test_app_slugs_keeps_app_links_in_order():
    assert am.app_slugs(SYNC_SEARCH) == [
        "/apk/red-apps-ltd/sync-for-reddit/",
        "/apk/bytedance-pte-ltd/capcut/",
    ]


def test_app_slugs_respects_limit():
    assert len(am.app_slugs(SYNC_SEARCH, limit=1)) == 1


def test_package_id_present_accepts_exact_id():
    assert am.package_id_present("com.laurencedawson.reddit_sync", SYNC_APP_PAGE)


def test_package_id_present_rejects_sibling_variants():
    # A page naming `...reddit_sync` must not validate the .pro / .dev variants.
    assert not am.package_id_present("com.laurencedawson.reddit_sync.pro", SYNC_APP_PAGE)
    assert not am.package_id_present("com.laurencedawson.reddit_sync.dev", SYNC_APP_PAGE)


def test_package_id_present_does_not_match_longer_id():
    page = "<span>com.laurencedawson.reddit_sync.pro</span>"
    assert not am.package_id_present("com.laurencedawson.reddit_sync", page)


def test_parse_app_page_reads_title_and_primary_image():
    name, icon = am.parse_app_page(SYNC_APP_PAGE)
    assert name == "Sync for reddit"
    assert icon.startswith("https://www.apkmirror.com/")
    assert "w=256" in icon and "h=256" in icon


def test_fetch_app_verifies_package_when_title_mismatch(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(am, "_http_get", _stub_pages(
        SYNC_SEARCH, {"/apk/red-apps-ltd/sync-for-reddit/": SYNC_APP_PAGE}))
    cache = {}
    result = am.fetch_app("com.laurencedawson.reddit_sync", cache)
    assert result["found"] is True
    assert result["name"] == "Sync for reddit"
    assert "ap_resize" in result["icon_url"]
    assert cache["com.laurencedawson.reddit_sync"]["found"] is True


def test_fetch_app_rejects_app_page_for_a_different_package(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(am, "_http_get", _stub_pages(
        SYNC_SEARCH,
        {"/apk/red-apps-ltd/sync-for-reddit/": CAPCUT_APP_PAGE,
         "/apk/bytedance-pte-ltd/capcut/": CAPCUT_APP_PAGE},
    ))
    cache = {}
    result = am.fetch_app("com.laurencedawson.reddit_sync", cache)
    assert result["found"] is False
    assert result["icon_url"] == ""


def test_unverified_miss_is_not_recorded_when_page_fetch_fails(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(am, "MAX_RETRIES", 0)

    def blocked(url):
        if "?s=" in url:
            return SYNC_SEARCH
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(am, "_http_get", blocked)
    cache = {}
    try:
        am.fetch_app("com.laurencedawson.reddit_sync", cache)
    except am.ApkmirrorUnavailable:
        pass
    else:
        raise AssertionError("expected ApkmirrorUnavailable")
    assert cache == {}


# ── cache migration: negatives from the pre-verification rule ──────────────

def test_legacy_negative_is_rechecked_not_trusted(monkeypatch):
    """A negative recorded before verification could not see the app page."""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(am, "_http_get", _stub_pages(
        SYNC_SEARCH, {"/apk/red-apps-ltd/sync-for-reddit/": SYNC_APP_PAGE}))
    legacy = {"com.laurencedawson.reddit_sync": {
        "name": "", "icon_url": "", "found": False,
        "checked_at": int(__import__("time").time()),
    }}
    result = am.fetch_app("com.laurencedawson.reddit_sync", legacy)
    assert result["found"] is True
    assert result["name"] == "Sync for reddit"


def test_negative_from_current_stage_is_trusted(monkeypatch):
    def boom(url):
        raise AssertionError("should not hit the network")

    monkeypatch.setattr(am, "_http_get", boom)
    cache = {"com.x": {
        "name": "", "icon_url": "", "found": False,
        "checked_at": int(__import__("time").time()),
        "verify_stage": am.VERIFY_STAGE,
    }}
    assert am.fetch_app("com.x", cache)["found"] is False


def test_page_budget_stops_fetching_and_does_not_cache(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(am, "_http_get", _stub_pages(
        SYNC_SEARCH, {"/apk/red-apps-ltd/sync-for-reddit/": SYNC_APP_PAGE}))
    monkeypatch.setattr(am, "_pages_fetched", am.MAX_APP_PAGES_PER_RUN)
    cache = {}
    try:
        am.fetch_app("com.laurencedawson.reddit_sync", cache)
    except am.ApkmirrorUnavailable as err:
        assert "budget" in str(err)
    else:
        raise AssertionError("expected ApkmirrorUnavailable")
    assert cache == {}


