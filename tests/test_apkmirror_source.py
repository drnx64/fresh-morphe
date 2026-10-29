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
