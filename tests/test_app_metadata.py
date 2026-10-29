"""Tests for app_metadata.py — name/icon resolution and the app_cache registry."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import app_metadata as m
import icon_fetcher as icons


class TestDeriveName:
    def test_strips_platform_segment(self):
        assert m.derive_name_from_package("com.instagram.android") == "Instagram"

    def test_uses_last_meaningful_segment(self):
        assert m.derive_name_from_package("com.google.android.apps.photos") == "Photos"

    def test_splits_underscores_and_title_cases(self):
        assert m.derive_name_from_package("com.laurencedawson.reddit_sync") == "Reddit Sync"

    def test_splits_dashes(self):
        assert m.derive_name_from_package("com.example.my-app") == "My App"

    def test_splits_camel_case(self):
        assert m.derive_name_from_package("com.example.redditSync") == "Reddit Sync"

    def test_ignores_api_suffix(self):
        assert m.derive_name_from_package("com.termux.api") == "Termux"

    def test_single_segment(self):
        assert m.derive_name_from_package("myapp") == "Myapp"

    def test_empty_input_never_returns_empty(self):
        assert m.derive_name_from_package("") == "Unknown app"
        assert m.derive_name_from_package(None) == "Unknown app"

    def test_dots_only(self):
        assert m.derive_name_from_package("...") == "Unknown app"

    def test_all_generic_segments_still_yields_something(self):
        name = m.derive_name_from_package("com.android.app")
        assert isinstance(name, str) and name


class TestCleanName:
    def test_unescapes_html_entities(self):
        assert m.clean_app_name("Goodreads: Book Tracker &amp; More") == "Goodreads: Book Tracker & More"

    def test_strips_play_store_suffix(self):
        assert m.clean_app_name("Instagram - Apps on Google Play") == "Instagram"

    def test_strips_fdroid_suffix(self):
        assert m.clean_app_name("F-Droid | F-Droid - Free and Open Source Android App Repository") == "F-Droid"

    def test_collapses_whitespace(self):
        assert m.clean_app_name("  Reddit \n  Sync  ") == "Reddit Sync"

    def test_rejects_non_string(self):
        assert m.clean_app_name(None) == ""
        assert m.clean_app_name({"name": "x"}) == ""


class TestResolveName:
    def test_verified_cache_name_wins(self):
        cache = {"com.foo.bar": {"name": "Foo Bar", "name_source": "play"}}
        assert m.resolve_name("com.foo.bar", cache, "Ignored") == ("Foo Bar", "play")

    def test_source_defaults_to_play_for_legacy_entries(self):
        cache = {"com.foo.bar": {"name": "Foo Bar"}}
        assert m.resolve_name("com.foo.bar", cache)[1] == m.PLAY_SOURCE

    def test_package_echo_in_cache_is_not_a_name(self):
        cache = {"com.foo.bar": {"name": "com.foo.bar"}}
        assert m.resolve_name("com.foo.bar", cache, "Bundle Name") == ("Bundle Name", m.BUNDLE_SOURCE)

    def test_package_echo_falls_back_to_derivation(self):
        cache = {"com.instagram.android": {"name": "com.instagram.android"}}
        name, source = m.resolve_name("com.instagram.android", cache)
        assert name == "Instagram"
        assert source == m.DERIVED_SOURCE

    def test_bundle_name_used_without_cache(self):
        assert m.resolve_name("com.foo.bar", {}, "Bar App") == ("Bar App", m.BUNDLE_SOURCE)

    def test_bundle_name_equal_to_package_is_rejected(self):
        assert m.resolve_name("com.foo.bar", {}, "com.foo.bar")[1] == m.DERIVED_SOURCE

    def test_never_returns_the_raw_package(self):
        for pkg in ("com.foo.bar", "org.fdroid.fdroid", "single"):
            assert m.resolve_name(pkg, {})[0] != pkg

    def test_handles_malformed_cache_entry(self):
        assert m.resolve_name("com.foo.bar", {"com.foo.bar": "not-a-dict"})[1] == m.DERIVED_SOURCE

    def test_empty_package(self):
        assert m.resolve_name("", {})[0] == "Unknown app"


class TestResolveIcon:
    def test_https_url_and_source(self):
        cache = {"com.x": {"icon_url": "https://f-droid.org/repo/icons-640/com.x.png", "icon_source": "fdroid"}}
        assert m.resolve_icon("com.x", cache) == ("https://f-droid.org/repo/icons-640/com.x.png", "fdroid")

    def test_http_url_accepted(self):
        cache = {"com.x": {"icon_url": "http://example.com/i.png"}}
        assert m.resolve_icon("com.x", cache)[0] == "http://example.com/i.png"

    def test_relative_path_rejected(self):
        cache = {"com.x": {"icon_url": "data/icons/com.x.png"}}
        assert m.resolve_icon("com.x", cache) == ("", "")

    def test_missing_icon(self):
        assert m.resolve_icon("com.x", {"com.x": {"name": "X", "icon_url": ""}}) == ("", "")

    def test_malformed_icon_is_a_miss(self):
        assert m.resolve_icon("com.x", {"com.x": {"icon_url": {}}}) == ("", "")
        assert m.resolve_icon("com.x", {"com.x": {"icon_url": None}}) == ("", "")

    def test_normalize_cache_repairs_malformed_values(self):
        cache = {
            "com.a": {"icon_url": {}},
            "com.b": {"icon_url": None},
            "com.c": "junk",
            "com.d": {"icon_url": "https://i/d.png"},
        }
        assert m.normalize_cache(cache) == 3
        assert cache["com.a"]["icon_url"] == ""
        assert cache["com.b"]["icon_url"] == ""
        assert cache["com.c"] == {"icon_url": ""}
        assert cache["com.d"]["icon_url"] == "https://i/d.png"


class TestIconOverrides:
    def test_override_wins_and_records_manual_source(self, tmp_path):
        path = tmp_path / "icon_overrides.json"
        path.write_text('{"com.x": {"name": "X App", "icon_url": "https://i/x.png"}}', encoding="utf-8")
        cache = {"com.x": {"icon_url": "", "icon_source": "play"}}
        assert m.apply_icon_overrides(cache, str(path)) == 1
        assert cache["com.x"]["icon_url"] == "https://i/x.png"
        assert cache["com.x"]["icon_source"] == m.MANUAL_SOURCE
        assert cache["com.x"]["name"] == "X App"

    def test_string_override_is_an_icon_url(self, tmp_path):
        path = tmp_path / "icon_overrides.json"
        path.write_text('{"com.x": "https://i/x.png"}', encoding="utf-8")
        cache = {}
        assert m.apply_icon_overrides(cache, str(path)) == 1
        assert cache["com.x"]["icon_url"] == "https://i/x.png"


class TestUpsert:
    def test_creates_entry_with_provenance(self):
        cache = {}
        m.upsert("com.x", cache, name="X App", name_source="play",
                 icon_url="https://i/x.png", icon_source="play")
        entry = cache["com.x"]
        assert entry["name"] == "X App"
        assert entry["name_source"] == "play"
        assert entry["icon_url"] == "https://i/x.png"
        assert entry["icon_source"] == "play"
        assert isinstance(entry["updated_at"], int)

    def test_does_not_erase_verified_name(self):
        cache = {"com.x": {"name": "Verified", "name_source": "play"}}
        m.upsert("com.x", cache)
        assert cache["com.x"]["name"] == "Verified"
        assert cache["com.x"]["name_source"] == "play"

    def test_does_not_erase_verified_icon(self):
        cache = {"com.x": {"icon_url": "https://i/x.png", "icon_source": "play"}}
        m.upsert("com.x", cache, icon_url="")
        assert cache["com.x"]["icon_url"] == "https://i/x.png"

    def test_empty_package_ignored(self):
        cache = {}
        m.upsert("", cache, name="Nobody")
        assert cache == {}

    def test_normalises_package_key(self):
        cache = {}
        m.upsert("  COM.X ", cache, name="X")
        assert "com.x" in cache

    def test_ignores_package_echo_name(self):
        cache = {}
        m.upsert("com.x", cache, name="com.x")
        assert cache["com.x"].get("name", "") == ""


class TestRegistryPersistence:
    def test_roundtrip(self, tmp_path):
        path = str(tmp_path / "app_cache.json")
        cache = {}
        m.upsert("com.x", cache, name="X App", icon_url="https://i/x.png")
        m.save_cache(cache, path)
        assert m.load_cache(path)["com.x"]["name"] == "X App"

    def test_missing_file_loads_empty(self, tmp_path):
        assert m.load_cache(str(tmp_path / "nope.json")) == {}

    def test_load_repairs_malformed_icons(self, tmp_path):
        path = tmp_path / "app_cache.json"
        path.write_text('{"com.x": {"icon_url": {}}}', encoding="utf-8")
        cache = m.load_cache(str(path))
        assert cache["com.x"]["icon_url"] == ""


class TestFetchIconCache:
    def test_malformed_cache_value_is_a_miss(self, tmp_path, monkeypatch):
        path = tmp_path / "app_cache.json"
        path.write_text('{"com.x": {"icon_url": {}}}', encoding="utf-8")
        monkeypatch.setattr(icons, "APP_CACHE_PATH", str(path))
        monkeypatch.setattr(icons, "_fetch_app_details", lambda pkg: ("X", "https://i/x.png"))
        assert icons.fetch_app_icon("com.x") == "https://i/x.png"
        assert m.load_cache(str(path))["com.x"]["icon_url"] == "https://i/x.png"


class TestIconFetchBudget:
    """Guards against the 2026-10-29 CI stall: 404s were retried forever."""

    def test_play_404_is_not_retried(self, monkeypatch):
        import urllib.error

        calls = []

        def fake_urlopen(request, timeout=15):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)

        monkeypatch.setattr(icons.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(icons.time, "sleep", lambda seconds: None)

        assert icons._fetch_page_with_retry("https://play.google.com/store/x") is None
        assert len(calls) == 1

    def test_transient_error_is_retried(self, monkeypatch):
        import urllib.error

        calls = []

        def fake_urlopen(request, timeout=15):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 500, "Server Error", None, None)

        monkeypatch.setattr(icons.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(icons.time, "sleep", lambda seconds: None)

        assert icons._fetch_page_with_retry("https://play.google.com/store/x") is None
        assert len(calls) == icons.MAX_RETRIES

    def test_recent_miss_is_skipped(self, tmp_path, monkeypatch):
        import json
        import time

        path = tmp_path / "app_cache.json"
        path.write_text(json.dumps({
            "com.gone": {"icon_url": "", "last_icon_attempt": int(time.time())},
            "com.fresh": {"icon_url": ""},
        }), encoding="utf-8")
        monkeypatch.setattr(icons, "APP_CACHE_PATH", str(path))
        fetched = []
        monkeypatch.setattr(
            icons, "fetch_app_icon", lambda pkg, skip_cache=False: fetched.append(pkg) or ""
        )

        parsed = {"bundle": {"apps": [{"package": "com.gone"}, {"package": "com.fresh"}]}}
        icons.enrich_parsed_bundles_with_icons(parsed)

        assert fetched == ["com.fresh"]

    def test_fetch_is_capped_per_run(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setenv("ICON_FETCH_LIMIT", "2")
        path = tmp_path / "app_cache.json"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(icons, "APP_CACHE_PATH", str(path))
        fetched = []
        monkeypatch.setattr(
            icons, "fetch_app_icon", lambda pkg, skip_cache=False: fetched.append(pkg) or ""
        )

        packages = [f"com.app{index}" for index in range(5)]
        parsed = {"bundle": {"apps": [{"package": pkg} for pkg in packages]}}
        icons.enrich_parsed_bundles_with_icons(parsed)

        assert len(fetched) == 2
        assert set(fetched) <= set(packages)

    def test_failed_lookup_records_attempt_timestamp(self, tmp_path, monkeypatch):
        path = tmp_path / "app_cache.json"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(icons, "APP_CACHE_PATH", str(path))
        monkeypatch.setattr(icons, "_fetch_app_details", lambda pkg: ("", ""))

        assert icons.fetch_app_icon("com.missing") == ""
        entry = m.load_cache(str(path))["com.missing"]
        assert entry["icon_url"] == ""
        assert entry["last_icon_attempt"] > 0


class TestIsUnresolved:
    def test_fully_resolved(self):
        cache = {"com.x": {"name": "X", "icon_url": "https://i/x.png"}}
        assert m.is_unresolved(cache, "com.x") is False

    def test_missing_icon(self):
        assert m.is_unresolved({"com.x": {"name": "X"}}, "com.x") is True

    def test_missing_name(self):
        assert m.is_unresolved({"com.x": {"icon_url": "https://i/x.png"}}, "com.x") is True

    def test_absent_entry(self):
        assert m.is_unresolved({}, "com.x") is True

    def test_package_echo_counts_as_missing_name(self):
        assert m.is_unresolved({"com.x": {"name": "com.x", "icon_url": "https://i/x.png"}}, "com.x") is True


class TestStoreLink:
    def test_play_source(self):
        cache = {"com.x": {"name": "X", "name_source": "play", "icon_url": "https://i/x.png"}}
        url, store = m.store_link("com.x", cache)
        assert url == "https://play.google.com/store/apps/details?id=com.x"
        assert store == m.STORE_PLAY

    def test_fdroid_source(self):
        cache = {"com.x": {"name": "X", "name_source": "fdroid", "icon_url": "https://f-droid.org/i.png"}}
        url, store = m.store_link("com.x", cache)
        assert url == "https://f-droid.org/en/packages/com.x/"
        assert store == m.STORE_FDROID

    def test_play_wins_when_sources_are_mixed(self):
        cache = {"com.x": {"name": "X", "name_source": "fdroid", "icon_source": "play"}}
        assert m.store_link("com.x", cache)[1] == m.STORE_PLAY

    def test_legacy_entry_without_provenance_is_play(self):
        cache = {"com.x": {"name": "X", "icon_url": "https://i/x.png"}}
        assert m.store_link("com.x", cache)[1] == m.STORE_PLAY

    def test_apkmirror_source_links_to_search(self):
        cache = {"com.x": {"name": "X", "name_source": "apkmirror", "icon_url": "https://i/x.png"}}
        url, store = m.store_link("com.x", cache)
        assert url == "https://www.apkmirror.com/?s=com.x"
        assert store == m.STORE_APKMIRROR

    def test_unknown_app_falls_back_to_apkmirror_search(self):
        url, store = m.store_link("com.some.app", {})
        assert url == "https://www.apkmirror.com/?s=com.some.app"
        assert store == m.STORE_APKMIRROR

    def test_empty_entry_falls_back_to_apkmirror(self):
        assert m.store_link("com.x", {"com.x": {}})[1] == m.STORE_APKMIRROR

    def test_package_is_quote_escaped(self):
        url, _ = m.store_link("com.x/../weird", {})
        assert "com.x%2F..%2Fweird" in url

    def test_empty_package(self):
        assert m.store_link("", {}) == ("", "")

    def test_handles_malformed_cache(self):
        assert m.store_link("com.x", {"com.x": "junk"})[1] == m.STORE_APKMIRROR
