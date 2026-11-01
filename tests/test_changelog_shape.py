"""Changelog history payload shape: names kept, patch prose dropped."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from changelog_shape import slim_app, slim_changelog, slim_day, slim_patch_diff


APP = {
    "app_name": "Example",
    "package": "com.example.app",
    "badge_type": "UPDATED APP",
    "scan_numbers": [2, 5],
    "patch_diff": {
        "patches_added": [
            {"name": "New patch", "description": "a" * 500},
        ],
        "patches_removed": [],
        "patches_modified": [
            {
                "name": "Rewritten patch",
                "description": "b" * 500,
                "previous_description": "c" * 500,
                "changes": ["description updated"],
            },
        ],
    },
}


def day(app=None):
    return {
        "date": "2026-10-29",
        "lastChecked": "2026-10-29T23:00:00Z",
        "affected_bundles": [{
            "bundle": "foo",
            "channel": "stable",
            "badge_type": "UPDATED",
            "repo_url": "https://github.com/acme/foo",
            "patches_name": "Foo Patches",
            "extra_badges": ["VERSION BUMP"],
            "previous_version": "v1.0.0",
            "new_version": "v1.1.0",
            "avatarUrl": "",
            "bundleImageUrl": "",
            "apps": [app or APP],
        }],
    }


def test_descriptions_are_dropped_and_names_survive():
    slim = slim_app(APP)
    added = slim["patch_diff"]["patches_added"]
    modified = slim["patch_diff"]["patches_modified"]

    assert added == [{"name": "New patch"}]
    assert modified == [{"name": "Rewritten patch", "changes": ["description updated"]}]
    assert "description" not in added[0]
    assert "previous_description" not in modified[0]


def test_empty_patch_lists_are_omitted():
    slim = slim_app(APP)
    assert "patches_removed" not in slim["patch_diff"]


def test_a_diff_with_no_changes_is_dropped_entirely():
    app = {"package": "com.example.empty", "patch_diff": {
        "patches_added": [], "patches_removed": [], "patches_modified": [],
    }}
    slim = slim_app(app)
    assert "patch_diff" not in slim
    assert slim["package"] == "com.example.empty"


def test_app_fields_outside_the_diff_are_untouched():
    slim = slim_app(APP)
    assert slim["app_name"] == "Example"
    assert slim["badge_type"] == "UPDATED APP"
    assert slim["scan_numbers"] == [2, 5]


def test_bundle_metadata_is_preserved():
    slim = slim_day(day())
    bundle = slim["affected_bundles"][0]
    assert bundle["patches_name"] == "Foo Patches"
    assert bundle["extra_badges"] == ["VERSION BUMP"]
    assert bundle["previous_version"] == "v1.0.0"
    assert bundle["channel"] == "stable"
    assert slim["date"] == "2026-10-29"
    assert slim["lastChecked"] == "2026-10-29T23:00:00Z"


def test_input_is_not_mutated():
    # The buffer that feeds finalization is the same dict graph in some callers;
    # slimming must copy rather than edit in place.
    original = day()
    slim_day(original)
    assert original["affected_bundles"][0]["apps"][0]["patch_diff"]["patches_added"][0]["description"] == "a" * 500


def test_slimming_is_idempotent():
    once = slim_changelog([day()])
    twice = slim_changelog(once)
    assert once == twice


def test_legacy_shapes_are_tolerated():
    assert slim_day(None) is None
    # A day without (or with a non-list) affected_bundles passes through.
    assert "affected_bundles" not in slim_day({"date": "2026-10-29"})
    assert slim_day({"date": "2026-10-29", "affected_bundles": None})["affected_bundles"] is None
    legacy = {"patch_diff": {"patches_added": ["bare string name"]}}
    assert slim_app(legacy)["patch_diff"]["patches_added"] == ["bare string name"]
    assert slim_patch_diff("not a dict") == "not a dict"


def test_build_changelog_entry_slims_finalized_days():
    import merge_daily_buffer as mdb

    entry = mdb.build_changelog_entry("2026-10-29", {"foo:stable": day()["affected_bundles"][0]})
    app = entry["affected_bundles"][0]["apps"][0]
    assert app["patch_diff"]["patches_added"] == [{"name": "New patch"}]
    assert "description" not in app["patch_diff"]["patches_modified"][0]
    assert "patches_removed" not in app["patch_diff"]


def test_finalize_slims_new_days_and_backfills_old_ones(monkeypatch):
    """Days written before this shape existed must shrink on the next finalize."""
    import merge_daily_buffer as mdb

    written = {}

    def fake_save(path, data):
        written["path"] = path
        written["data"] = data

    def boom(*args, **kwargs):
        raise RuntimeError("stop after the changelog step")

    old_fat_day = day()  # still carries its descriptions
    monkeypatch.setattr(mdb, "ensure_dirs", lambda: None)
    monkeypatch.setattr(mdb, "load_json", lambda path, default=None: [old_fat_day])
    monkeypatch.setattr(mdb, "save_json", fake_save)
    monkeypatch.setattr(mdb, "generate_markdown_changelog", boom)
    monkeypatch.setattr(mdb, "load_current_snapshot", boom)

    mdb.finalize_buffer({"date": "2026-10-31", "affected_bundles": {
        "foo:stable": day()["affected_bundles"][0],
    }})

    assert written["path"].endswith("changelog.json")
    dates = [entry["date"] for entry in written["data"]]
    assert dates == ["2026-10-31", "2026-10-29"]

    for entry in written["data"]:
        for bundle in entry["affected_bundles"]:
            for app in bundle["apps"]:
                for key in ("patches_added", "patches_modified"):
                    for item in app["patch_diff"].get(key, []):
                        assert "description" not in item
                        assert "previous_description" not in item
                assert "patches_removed" not in app["patch_diff"]
