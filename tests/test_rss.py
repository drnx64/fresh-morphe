"""Tests for generate_site.py RSS builders (changelog + daily digest)."""
import sys
import os
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import generate_site as gs

SITE = "https://example.test"

REGISTRY = {
    "com.play.app": {"name": "Play App", "name_source": "play", "icon_url": "https://i/p.png"},
    "com.fd.app": {"name": "FD App", "name_source": "fdroid", "icon_url": "https://f-droid/i.png"},
}


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(gs, "_app_cache", dict(REGISTRY))


def parse(xml_text):
    return ET.fromstring(xml_text)


def items(xml_text):
    return parse(xml_text).findall("./channel/item")


def desc(item):
    return item.findtext("description") or ""


def app(pkg, badge="UPDATED APP"):
    return {"package": pkg, "badge_type": badge, "app_name": pkg}


def bundle(name="repo", channel="stable", badge="UPDATED", apps=None, patches=None):
    return {
        "bundle": name,
        "channel": channel,
        "badge_type": badge,
        "patches_name": patches or name,
        "apps": apps if apps is not None else [app("com.play.app")],
    }


class TestChangelogFeed:
    def test_well_formed_and_item_count(self):
        feed = gs.build_changelog_feed([{"date": "2026-10-23", "affected_bundles": [bundle()]}], SITE)
        assert len(items(feed)) == 1

    def test_play_app_is_linked(self):
        feed = gs.build_changelog_feed([{"date": "2026-10-23", "affected_bundles": [bundle()]}], SITE)
        body = desc(items(feed)[0])
        assert '<a href="https://play.google.com/store/apps/details?id=com.play.app">Play App</a>' in body

    def test_fdroid_app_is_linked_to_fdroid(self):
        feed = gs.build_changelog_feed(
            [{"date": "2026-10-23", "affected_bundles": [bundle(apps=[app("com.fd.app")])]}], SITE)
        assert "https://f-droid.org/en/packages/com.fd.app/" in desc(items(feed)[0])

    def test_unresolved_app_uses_derived_name_and_apkmirror(self):
        feed = gs.build_changelog_feed(
            [{"date": "2026-10-23", "affected_bundles": [bundle(apps=[app("com.example.gallery")])]}], SITE)
        body = desc(items(feed)[0])
        assert "Gallery" in body
        assert "https://www.apkmirror.com/?s=com.example.gallery" in body
        assert ">com.example.gallery<" not in body

    def test_bundle_without_apps_gets_description(self):
        entry = bundle(apps=[])
        feed = gs.build_changelog_feed([{"date": "2026-10-23", "affected_bundles": [entry]}], SITE)
        assert "channel: stable" in desc(items(feed)[0])

    def test_newest_day_first(self):
        changelog = [
            {"date": "2026-10-23", "affected_bundles": [bundle()]},
            {"date": "2026-10-22", "affected_bundles": [bundle()]},
        ]
        feed = gs.build_changelog_feed(changelog, SITE)
        dates = [item.findtext("pubDate") for item in items(feed)]
        assert "23 Oct 2026" in dates[0] and "22 Oct 2026" in dates[1]

    def test_guid_is_unique_per_app(self):
        feed = gs.build_changelog_feed(
            [{"date": "2026-10-23", "affected_bundles": [bundle(apps=[app("com.play.app"), app("com.fd.app")])]}], SITE)
        guids = [item.findtext("guid") for item in items(feed)]
        assert len(guids) == len(set(guids))

    def test_cdata_terminator_is_escaped(self):
        feed = gs._feed_document(SITE, "x.xml", "T", "D", [{
            "title": "t", "link": "l", "description": "a ]]> b", "guid": "g", "pubDate": "d",
        }])
        assert "a ]]> b" not in feed
        assert "a ]]&gt; b" in feed
        assert len(items(feed)) == 1


class TestDailyFeed:
    def test_one_item_per_day(self):
        changelog = [
            {"date": "2026-10-23", "affected_bundles": [bundle()]},
            {"date": "2026-10-22", "affected_bundles": [bundle()]},
        ]
        feed = gs.build_daily_feed(changelog, {}, SITE)
        assert len(items(feed)) == 2

    def test_newest_first_and_unique_guids(self):
        changelog = [
            {"date": "2026-10-22", "affected_bundles": [bundle()]},
            {"date": "2026-10-23", "affected_bundles": [bundle()]},
        ]
        feed = gs.build_daily_feed(changelog, {}, SITE)
        guids = [item.findtext("guid") for item in items(feed)]
        assert guids[0].endswith("#2026-10-23")
        assert len(guids) == len(set(guids))

    def test_today_buffer_appended(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        buffer = {"date": today, "affected_bundles": {"repo:stable": bundle()}}
        feed = gs.build_daily_feed([{"date": "2026-10-23", "affected_bundles": [bundle()]}], buffer, SITE)
        feed_items = items(feed)
        assert len(feed_items) == 2
        assert feed_items[0].findtext("title").startswith("Today's updates")

    def test_buffer_merges_into_matching_day(self):
        buffer = {"date": "2026-10-23", "affected_bundles": {"repo:stable": bundle(apps=[app("com.fd.app")])}}
        feed = gs.build_daily_feed(
            [{"date": "2026-10-23", "affected_bundles": [bundle()]}], buffer, SITE)
        feed_items = items(feed)
        assert len(feed_items) == 1
        body = desc(feed_items[0])
        assert "com.play.app" in body and "com.fd.app" in body

    def test_body_lists_apps_with_store_links_and_patch_summary(self):
        entry = bundle(apps=[app("com.play.app")])
        entry["apps"][0]["patch_diff"] = {"patches_added": [{"name": "New patch"}]}
        feed = gs.build_daily_feed([{"date": "2026-10-23", "affected_bundles": [entry]}], {}, SITE)
        body = desc(items(feed)[0])
        assert "<strong>repo</strong>" in body
        assert '<a href="https://play.google.com/store/apps/details?id=com.play.app">Play App</a>' in body
        assert "+ New patch" in body

    def test_removed_app_is_struck_through(self):
        entry = bundle(apps=[app("com.play.app", "REMOVED APP")])
        feed = gs.build_daily_feed([{"date": "2026-10-23", "affected_bundles": [entry]}], {}, SITE)
        assert "<s>" in desc(items(feed)[0])

    def test_names_and_markup_are_escaped(self):
        entry = bundle(patches="A & B <tag>")
        feed = gs.build_daily_feed([{"date": "2026-10-23", "affected_bundles": [entry]}], {}, SITE)
        body = desc(items(feed)[0])
        assert "A &amp; B &lt;tag&gt;" in body
        assert len(items(feed)) == 1

    def test_empty_days_are_skipped(self):
        changelog = [
            {"date": "2026-10-23", "affected_bundles": []},
            {"date": "2026-10-22", "affected_bundles": [bundle()]},
        ]
        assert len(items(gs.build_daily_feed(changelog, {}, SITE))) == 1

    def test_days_are_capped(self):
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        changelog = [
            {"date": (base - timedelta(days=i)).strftime("%Y-%m-%d"), "affected_bundles": [bundle()]}
            for i in range(gs.MAX_DAILY_DAYS + 5)
        ]
        assert len(items(gs.build_daily_feed(changelog, {}, SITE))) == gs.MAX_DAILY_DAYS

    def test_bundles_are_capped_with_overflow_note(self):
        bundles = [bundle(name=f"r{i}") for i in range(12)]
        body = desc(items(gs.build_daily_feed([{"date": "2026-10-23", "affected_bundles": bundles}], {}, SITE))[0])
        assert "4 more bundles" in body

    def test_patch_list_overflow(self):
        entry = bundle(apps=[app("com.play.app")])
        entry["apps"][0]["patch_diff"] = {"patches_added": [{"name": f"P{i}"} for i in range(7)]}
        body = desc(items(gs.build_daily_feed([{"date": "2026-10-23", "affected_bundles": [entry]}], {}, SITE))[0])
        assert "+3 more" in body

    def test_malformed_buffer_is_ignored(self):
        feed = gs.build_daily_feed([{"date": "2026-10-23", "affected_bundles": [bundle()]}], "junk", SITE)
        assert len(items(feed)) == 1

    def test_feed_self_link_points_at_daily_feed(self):
        feed = gs.build_daily_feed([{"date": "2026-10-23", "affected_bundles": [bundle()]}], {}, SITE)
        atom = parse(feed).find("./channel/{http://www.w3.org/2005/Atom}link")
        assert atom.get("href") == f"{SITE}/feed-daily.xml"
