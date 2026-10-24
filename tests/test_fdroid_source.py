"""Tests for fdroid_source.py — page parsing, caching and enrichment (no network)."""
import sys
import os
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import fdroid_source as fd
import app_metadata as m

PAGE = """
<html><head><title>F-Droid | F-Droid - Free and Open Source Android App Repository</title></head>
<body><img src="/repo/icons-640/org.fdroid.fdroid.png" alt="icon"></body></html>
"""

PAGE_NAMED = """
<html><head><title>Sync for Reddit &amp; Friends | F-Droid</title></head>
<body><img src="/repo/icons-640/com.laurencedawson.reddit_sync.png"></body></html>
"""

NO_ICON_PAGE = "<html><head><title>Termux | F-Droid</title></head><body></body></html>"


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Redirect the lookup cache and the registry save into a temp dir."""
    cache_path = str(tmp_path / "fdroid_cache.json")
    monkeypatch.setattr(fd, "CACHE_PATH", cache_path)
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)
    return cache_path


class TestParsePage:
    def test_name_and_icon(self):
        name, icon = fd._parse_page("com.laurencedawson.reddit_sync", PAGE_NAMED)
        assert name == "Sync for Reddit & Friends"
        assert icon == "https://f-droid.org/repo/icons-640/com.laurencedawson.reddit_sync.png"

    def test_placeholder_title_is_not_a_name(self):
        name, icon = fd._parse_page("org.fdroid.fdroid", PAGE)
        assert name == ""
        assert icon.endswith("org.fdroid.fdroid.png")

    def test_missing_icon(self):
        name, icon = fd._parse_page("com.termux", NO_ICON_PAGE)
        assert name == "Termux"
        assert icon == ""

    def test_other_package_icon_is_not_used(self):
        _, icon = fd._parse_page("com.other", PAGE_NAMED)
        assert icon == ""


class TestFetchApp:
    def test_found_and_cached(self, isolated_cache, monkeypatch):
        calls = []

        def fake_get(url):
            calls.append(url)
            return PAGE_NAMED

        monkeypatch.setattr(fd, "_http_get", fake_get)
        cache = {}
        first = fd.fetch_app("com.laurencedawson.reddit_sync", cache)
        assert first["found"] is True
        assert first["name"] == "Sync for Reddit & Friends"
        assert cache["com.laurencedawson.reddit_sync"]["found"] is True

        second = fd.fetch_app("com.laurencedawson.reddit_sync", cache)
        assert second == first
        assert len(calls) == 1

    def test_404_is_a_definitive_miss(self, isolated_cache, monkeypatch):
        def fake_get(url):
            raise _http_error(404)

        monkeypatch.setattr(fd, "_http_get", fake_get)
        cache = {}
        result = fd.fetch_app("com.not.there", cache)
        assert result["found"] is False
        assert cache["com.not.there"]["found"] is False

    def test_negative_cache_expires(self, isolated_cache, monkeypatch):
        now = [1_000_000.0]
        monkeypatch.setattr(fd.time, "time", lambda: now[0])
        calls = []

        def fake_get(url):
            calls.append(url)
            raise _http_error(404)

        monkeypatch.setattr(fd, "_http_get", fake_get)
        cache = {}
        fd.fetch_app("com.not.there", cache)
        fd.fetch_app("com.not.there", cache)
        assert len(calls) == 1

        now[0] += (fd.NEGATIVE_TTL_DAYS + 1) * 86400
        fd.fetch_app("com.not.there", cache)
        assert len(calls) == 2

    def test_transient_failure_is_not_cached(self, isolated_cache, monkeypatch):
        def fake_get(url):
            raise urllib.error.URLError("boom")

        monkeypatch.setattr(fd, "_http_get", fake_get)
        cache = {}
        with pytest.raises(fd.FdroidUnavailable):
            fd.fetch_app("com.flaky", cache)
        assert "com.flaky" not in cache

    def test_retries_then_succeeds(self, isolated_cache, monkeypatch):
        state = {"n": 0}

        def fake_get(url):
            state["n"] += 1
            if state["n"] == 1:
                raise urllib.error.URLError("first attempt fails")
            return PAGE_NAMED

        monkeypatch.setattr(fd, "_http_get", fake_get)
        result = fd.fetch_app("com.laurencedawson.reddit_sync", {})
        assert result["found"] is True

    def test_blank_package(self, isolated_cache):
        assert fd.fetch_app("", {})["found"] is False


class TestEnrichUnresolved:
    def test_only_unresolved_packages_are_queried(self, isolated_cache, monkeypatch):
        queried = []

        def fake_get(url):
            queried.append(url)
            return PAGE_NAMED

        monkeypatch.setattr(fd, "_http_get", fake_get)
        cache = {
            "com.laurencedawson.reddit_sync": {"name": "Sync", "icon_url": "https://i/x.png"},
            "com.missing.app": {},
        }
        fd.enrich_unresolved(cache, ["com.laurencedawson.reddit_sync", "com.missing.app"], sleep=False)
        assert len(queried) == 1
        assert "com.missing.app" in queried[0]

    def test_records_fdroid_provenance(self, isolated_cache, monkeypatch):
        monkeypatch.setattr(fd, "_http_get", lambda url: PAGE_NAMED)
        cache = {"com.laurencedawson.reddit_sync": {}}
        fd.enrich_unresolved(cache, ["com.laurencedawson.reddit_sync"], sleep=False)
        entry = cache["com.laurencedawson.reddit_sync"]
        assert entry["name"] == "Sync for Reddit & Friends"
        assert entry["name_source"] == m.FDROID_SOURCE
        assert entry["icon_source"] == m.FDROID_SOURCE

    def test_transient_error_leaves_entry_untouched(self, isolated_cache, monkeypatch):
        def fake_get(url):
            raise urllib.error.URLError("down")

        monkeypatch.setattr(fd, "_http_get", fake_get)
        cache = {"com.x": {}}
        fd.enrich_unresolved(cache, ["com.x"], sleep=False)
        assert cache["com.x"] == {}

    def test_nothing_to_do(self, isolated_cache):
        cache = {"com.x": {"name": "X", "icon_url": "https://i/x.png"}}
        assert fd.enrich_unresolved(cache, ["com.x"], sleep=False) == 0

    def test_max_lookups_caps_requests(self, isolated_cache, monkeypatch):
        calls = []

        def fake_get(url):
            calls.append(url)
            raise _http_error(404)

        monkeypatch.setattr(fd, "_http_get", fake_get)
        fd.enrich_unresolved({}, ["a.b", "c.d", "e.f"], sleep=False, max_lookups=2)
        assert len(calls) == 2

    def test_cache_file_written(self, isolated_cache, monkeypatch):
        monkeypatch.setattr(fd, "_http_get", lambda url: PAGE_NAMED)
        fd.enrich_unresolved({"com.laurencedawson.reddit_sync": {}}, sleep=False)
        assert os.path.exists(isolated_cache)
