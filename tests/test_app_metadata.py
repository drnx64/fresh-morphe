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
        monkeypatch.setattr(icons, "_fetch_app_details", lambda pkg: ("X", "https://i/x.png", "X Corp"))
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
        monkeypatch.setattr(icons, "_fetch_app_details", lambda pkg: ("", "", ""))

        assert icons.fetch_app_icon("com.missing") == ""
        entry = m.load_cache(str(path))["com.missing"]
        assert entry["icon_url"] == ""
        assert entry["last_icon_attempt"] > 0


class TestDeveloper:
    """Publisher resolution: Play "Offered by" -> package namespace."""

    def test_package_namespace_two_segments(self):
        assert m.package_namespace("com.simplemobiletools.gallery") == "com.simplemobiletools"
        assert m.package_namespace("com.simplemobiletools.gallery.pro") == "com.simplemobiletools"

    def test_package_namespace_behind_country_code(self):
        assert m.package_namespace("jp.co.celsys.paint") == "jp.co.celsys"

    def test_package_namespace_single_segment(self):
        assert m.package_namespace("youtube") == "youtube"
        assert m.package_namespace("") == ""

    def test_store_developer_wins(self):
        cache = {"com.x": {"developer": "Rovio Entertainment Ltd.", "developer_source": "play"}}
        assert m.resolve_developer("com.x", cache) == ("Rovio Entertainment Ltd.", "play")

    def test_namespace_is_the_fallback(self):
        developer, source = m.resolve_developer("com.simplemobiletools.gallery", {})
        assert developer == "com.simplemobiletools"
        assert source == m.NAMESPACE_SOURCE

    def test_upsert_records_developer(self):
        cache = {}
        m.upsert("com.x", cache, developer="Adobe Inc.", developer_source="play")
        assert cache["com.x"]["developer"] == "Adobe Inc."
        assert cache["com.x"]["developer_source"] == "play"

    def test_empty_developer_never_overwrites(self):
        cache = {"com.x": {"developer": "Adobe Inc.", "developer_source": "play"}}
        m.upsert("com.x", cache, developer="")
        assert cache["com.x"]["developer"] == "Adobe Inc."

    def test_play_overwrites_namespace_fallback(self):
        cache = {"com.x": {"developer": "com.x", "developer_source": m.NAMESPACE_SOURCE}}
        m.upsert("com.x", cache, developer="Google LLC", developer_source="play")
        assert cache["com.x"]["developer"] == "Google LLC"
        assert cache["com.x"]["developer_source"] == "play"

    def test_generic_developer_is_rejected(self):
        assert m.clean_developer("Google Play") == ""
        assert m.clean_developer("unknown developer") == ""
        assert m.clean_developer("  Adobe Inc.  ") == "Adobe Inc."

    def test_ensure_developer_stores_namespace_once(self):
        cache = {}
        assert m.ensure_developer("com.simplemobiletools.gallery", cache) is True
        assert cache["com.simplemobiletools.gallery"]["developer"] == "com.simplemobiletools"
        assert m.ensure_developer("com.simplemobiletools.gallery", cache) is False

    def test_unresolved_when_only_namespace_is_known(self):
        assert m.is_developer_unresolved({"developer": "com.x", "developer_source": m.NAMESPACE_SOURCE}) is True
        assert m.is_developer_unresolved({"developer": "Adobe Inc.", "developer_source": "play"}) is False

    def test_developer_coverage_counts_store_names(self):
        cache = {
            "a": {"developer": "Adobe Inc.", "developer_source": "play"},
            "b": {"developer": "com.b", "developer_source": m.NAMESPACE_SOURCE},
        }
        coverage = m.developer_coverage(cache)
        assert coverage == {"resolved": 2, "from_store": 1, "total": 2, "percent": 100.0}

    def test_apply_resolved_developers_writes_both_sources(self):
        cache = {"com.x": {"developer": "Adobe Inc.", "developer_source": "play"}}
        parsed = {"k": {"apps": [{"package": "com.x"}, {"package": "com.simplemobiletools.gallery"}]}}
        icons.apply_resolved_developers(parsed, cache)
        assert parsed["k"]["apps"][0]["developer"] == "Adobe Inc."
        assert parsed["k"]["apps"][1]["developer"] == "com.simplemobiletools"


class TestPlayDeveloperParsing:
    def test_offered_by_link(self):
        html = (
            '<a href="https://play.google.com/store/apps/dev?id=1">'
            '<span class="x">Google LLC</span></a>'
        )
        assert icons._extract_developer(html) == "Google LLC"

    def test_itemprop_author(self):
        html = '<span itemprop="author"><a href="/store/apps/dev?id=2">Adobe Inc.</a></span>'
        assert icons._extract_developer(html) == "Adobe Inc."

    def test_missing_developer(self):
        assert icons._extract_developer("<html><body>nothing</body></html>") == ""

    def test_generic_offered_by_rejected(self):
        html = '<a href="/store/apps/dev?id=3">Google Play</a>'
        assert icons._extract_developer(html) == ""


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


class TestIconNegatives:
    """A package every store missed must stop eating the per-run budget."""

    def test_mark_stamps_source_and_timestamp(self):
        cache = {}
        assert m.mark_icon_negative("com.gone", cache, now=1000) is True
        entry = cache["com.gone"]
        assert entry["icon_url"] == ""
        assert entry["icon_source"] == m.ICON_NEGATIVE_SOURCE
        assert entry["icon_negative_at"] == 1000
        assert entry["last_icon_attempt"] == 1000

    def test_mark_never_overwrites_a_hit(self):
        cache = {"com.x": {"icon_url": "https://i/x.png", "icon_source": "play"}}
        assert m.mark_icon_negative("com.x", cache, now=1000) is False
        assert cache["com.x"]["icon_url"] == "https://i/x.png"
        assert "icon_negative_at" not in cache["com.x"]

    def test_mark_ignores_empty_package(self):
        cache = {}
        assert m.mark_icon_negative("  ", cache) is False
        assert cache == {}

    def test_fresh_negative_is_trusted(self):
        entry = {"icon_url": "", "icon_source": m.ICON_NEGATIVE_SOURCE, "icon_negative_at": 1000}
        assert m.is_icon_negative(entry, now=1000 + 86400) is True

    def test_expired_negative_is_retried(self):
        entry = {"icon_url": "", "icon_source": m.ICON_NEGATIVE_SOURCE, "icon_negative_at": 1000}
        # 90 days later the package gets another turn.
        assert m.is_icon_negative(entry, now=1000 + 91 * 86400) is False

    def test_resolved_entry_is_never_negative(self):
        entry = {"icon_url": "https://i/x.png", "icon_source": m.ICON_NEGATIVE_SOURCE}
        assert m.is_icon_negative(entry) is False

    def test_plain_miss_is_not_a_negative(self):
        assert m.is_icon_negative({"icon_url": "", "last_icon_attempt": 1000}) is False

    def test_malformed_stamp_is_not_a_negative(self):
        entry = {"icon_url": "", "icon_source": m.ICON_NEGATIVE_SOURCE, "icon_negative_at": "soon"}
        assert m.is_icon_negative(entry) is False

    def test_normalize_drops_stamp_once_an_icon_lands(self):
        cache = {
            "com.x": {
                "icon_url": "https://i/x.png",
                "icon_source": m.ICON_NEGATIVE_SOURCE,
                "icon_negative_at": 1000,
            }
        }
        m.normalize_cache(cache)
        assert "icon_negative_at" not in cache["com.x"]
        assert cache["com.x"].get("icon_source", "") != m.ICON_NEGATIVE_SOURCE

    def test_normalize_drops_non_numeric_stamp(self):
        cache = {"com.x": {"icon_url": "", "icon_negative_at": "yesterday"}}
        m.normalize_cache(cache)
        assert "icon_negative_at" not in cache["com.x"]

    def test_enrich_skips_a_fresh_negative(self, tmp_path, monkeypatch):
        import json
        import time

        path = tmp_path / "app_cache.json"
        path.write_text(json.dumps({
            "com.gone": {
                "icon_url": "",
                "icon_source": m.ICON_NEGATIVE_SOURCE,
                "icon_negative_at": int(time.time()),
                "last_icon_attempt": 1,
            },
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

    def test_enrich_retries_an_expired_negative(self, tmp_path, monkeypatch):
        import json

        path = tmp_path / "app_cache.json"
        path.write_text(json.dumps({
            "com.gone": {
                "icon_url": "",
                "icon_source": m.ICON_NEGATIVE_SOURCE,
                "icon_negative_at": 1000,
                "last_icon_attempt": 1000,
            },
        }), encoding="utf-8")
        monkeypatch.setattr(icons, "APP_CACHE_PATH", str(path))
        fetched = []
        monkeypatch.setattr(
            icons, "fetch_app_icon", lambda pkg, skip_cache=False: fetched.append(pkg) or ""
        )

        parsed = {"bundle": {"apps": [{"package": "com.gone"}]}}
        icons.enrich_parsed_bundles_with_icons(parsed)

        assert fetched == ["com.gone"]

    def test_retry_empty_skips_negatives(self, tmp_path, monkeypatch):
        import json
        import time

        path = tmp_path / "app_cache.json"
        path.write_text(json.dumps({
            "com.gone": {
                "icon_url": "",
                "icon_source": m.ICON_NEGATIVE_SOURCE,
                "icon_negative_at": int(time.time()),
                "last_icon_attempt": int(time.time()),
            },
            "com.stale": {"icon_url": "", "last_icon_attempt": 1},
        }), encoding="utf-8")
        monkeypatch.setattr(icons, "APP_CACHE_PATH", str(path))
        fetched = []
        monkeypatch.setattr(
            icons, "fetch_app_icon", lambda pkg, skip_cache=False: fetched.append(pkg) or ""
        )

        icons.retry_empty_icons(max_packages=10)

        assert fetched == ["com.stale"]


class TestMarkUnresolvedIcons:
    def _write_cache(self, tmp_path, payload):
        import json
        path = tmp_path / "app_cache.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_stamps_only_packages_attempted_this_run(self, tmp_path, monkeypatch):
        import time

        now = int(time.time())
        path = self._write_cache(tmp_path, {
            "com.missed": {"icon_url": "", "last_icon_attempt": now},
            "com.skipped": {"icon_url": "", "last_icon_attempt": now - 3600},
            "com.resolved": {"icon_url": "https://i/x.png", "icon_source": "play"},
        })
        monkeypatch.setattr(icons, "APP_CACHE_PATH", str(path))

        parsed = {"bundle": {"apps": [
            {"package": "com.missed"},
            {"package": "com.skipped"},
            {"package": "com.resolved"},
        ]}}
        assert icons.mark_unresolved_icons(parsed, since=now) == 1

        cache = m.load_cache(str(path))
        assert cache["com.missed"]["icon_source"] == m.ICON_NEGATIVE_SOURCE
        assert cache["com.missed"]["icon_negative_at"] >= now
        assert "icon_negative_at" not in cache["com.skipped"]
        assert cache["com.resolved"]["icon_url"] == "https://i/x.png"

    def test_does_not_restamp_an_existing_negative(self, tmp_path, monkeypatch):
        import time

        now = int(time.time())
        path = self._write_cache(tmp_path, {
            "com.gone": {
                "icon_url": "",
                "icon_source": m.ICON_NEGATIVE_SOURCE,
                "icon_negative_at": now - 86400,
                "last_icon_attempt": now,
            },
        })
        monkeypatch.setattr(icons, "APP_CACHE_PATH", str(path))

        parsed = {"bundle": {"apps": [{"package": "com.gone"}]}}
        assert icons.mark_unresolved_icons(parsed, since=now) == 0
        assert m.load_cache(str(path))["com.gone"]["icon_negative_at"] == now - 86400

    def test_no_packages_is_a_noop(self, tmp_path, monkeypatch):
        path = self._write_cache(tmp_path, {})
        monkeypatch.setattr(icons, "APP_CACHE_PATH", str(path))
        assert icons.mark_unresolved_icons({}, since=0) == 0


class TestIconCoverage:
    def test_counts_resolved_missing_and_negative(self):
        cache = {
            "com.a": {"icon_url": "https://i/a.png"},
            "com.b": {"icon_url": ""},
            "com.c": {
                "icon_url": "",
                "icon_source": m.ICON_NEGATIVE_SOURCE,
                "icon_negative_at": 1,
            },
            "com.d": "junk",
        }
        coverage = m.icon_coverage(cache)
        assert coverage["total"] == 4
        assert coverage["resolved"] == 1
        assert coverage["missing"] == 3
        assert coverage["percent"] == 25.0

    def test_empty_cache(self):
        assert m.icon_coverage({}) == {
            "resolved": 0, "total": 0, "missing": 0, "negative": 0, "percent": 0.0,
        }

