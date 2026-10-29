"""Daily changes lifecycle: same-day accumulation and next-day reset."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import merge_daily_buffer as mdb


@pytest.fixture
def state(monkeypatch):
    data = {
        "core": {},
        "stats": {},
        "changes": {},
        "split": {},
        "bundles": {},
    }
    monkeypatch.setattr(mdb, "load_core_json", lambda: data["core"])
    monkeypatch.setattr(mdb, "load_stats_json", lambda: data["stats"])
    monkeypatch.setattr(mdb, "load_changes_json", lambda: data["changes"])
    monkeypatch.setattr(mdb, "save_core_json", lambda value: data.__setitem__("core", value))
    monkeypatch.setattr(mdb, "save_stats_json", lambda value: data.__setitem__("stats", value))
    monkeypatch.setattr(mdb, "save_changes_json", lambda value: data.__setitem__("changes", value))
    monkeypatch.setattr(mdb, "save_bundles_split", lambda value: data.__setitem__("split", value))
    monkeypatch.setattr(mdb, "save_bundles_json", lambda value: data.__setitem__("bundles", value))
    monkeypatch.setattr(mdb, "merge_release_notes", lambda value: None)
    monkeypatch.setattr(mdb, "apply_repo_metadata", lambda value: None)
    return data


SNAPSHOT = {
    "foo:stable": {
        "bundle": "foo",
        "channel": "stable",
        "repo_url": "https://github.com/acme/foo",
        "apps": [{"package": "com.example.foo"}],
    }
}


def test_same_day_no_changes_preserves_aggregate(state):
    state["core"] = {"date": "2026-10-26", "last_run": "old", "lastChecked": "old"}
    state["stats"] = {"new_apps_today": 2, "new_bundles_today": 1}
    state["changes"] = {"affected_bundles": [{"bundle": "foo", "channel": "stable"}]}

    mdb.update_data_files("2026-10-26", {"affected_bundles": {}}, SNAPSHOT)

    assert state["changes"]["affected_bundles"] == [{"bundle": "foo", "channel": "stable"}]
    assert state["stats"]["new_apps_today"] == 2
    assert state["stats"]["new_bundles_today"] == 1
    assert state["core"]["date"] == "2026-10-26"


def test_next_day_no_changes_resets_aggregate(state):
    state["core"] = {"date": "2026-10-26", "last_run": "old", "lastChecked": "old"}
    state["stats"] = {"new_apps_today": 2, "new_bundles_today": 1}
    state["changes"] = {"affected_bundles": [{"bundle": "foo", "channel": "stable"}]}

    mdb.update_data_files("2026-10-27", {"affected_bundles": {}}, SNAPSHOT)

    assert state["changes"] == {"affected_bundles": []}
    assert state["stats"]["new_apps_today"] == 0
    assert state["stats"]["new_bundles_today"] == 0
    assert state["core"]["date"] == "2026-10-27"


def test_same_day_new_changes_update_aggregate(state):
    state["core"] = {"date": "2026-10-26", "last_run": "old", "lastChecked": "old"}
    state["stats"] = {"new_apps_today": 1, "new_bundles_today": 0}
    state["changes"] = {"affected_bundles": [{"bundle": "old", "channel": "stable"}]}
    buffer = {
        "affected_bundles": {
            "foo:stable": {
                "bundle": "foo",
                "channel": "stable",
                "badge_type": "UPDATED",
                "apps": [
                    {"package": "com.example.foo", "badge_type": "NEW APP"},
                    {"package": "com.example.bar", "badge_type": "UPDATED APP"},
                ],
            }
        }
    }

    mdb.update_data_files("2026-10-26", buffer, SNAPSHOT)

    assert state["changes"]["affected_bundles"][0]["bundle"] == "foo"
    assert state["stats"]["new_apps_today"] == 1
    assert state["core"]["date"] == "2026-10-26"


def test_silent_write_resets_on_new_day(state, monkeypatch):
    monkeypatch.setattr(mdb, "now_utc_iso", lambda: "2026-10-27T12:00:00Z")
    monkeypatch.setattr(mdb, "load_current_snapshot", lambda: SNAPSHOT)
    state["core"] = {"date": "2026-10-26", "last_run": "old", "lastChecked": "old"}
    state["stats"] = {"new_apps_today": 2, "new_bundles_today": 1}
    state["changes"] = {"affected_bundles": [{"bundle": "foo", "channel": "stable"}]}

    mdb.write_data_files(has_changes=False, preserve_changes_json=True)

    assert state["core"]["date"] == "2026-10-27"
    assert state["changes"] == {"affected_bundles": []}
    assert state["stats"]["new_apps_today"] == 0
    assert state["stats"]["new_bundles_today"] == 0


def test_silent_write_keeps_same_day_changes(state, monkeypatch):
    monkeypatch.setattr(mdb, "now_utc_iso", lambda: "2026-10-27T12:00:00Z")
    monkeypatch.setattr(mdb, "load_current_snapshot", lambda: SNAPSHOT)
    state["core"] = {"date": "2026-10-27", "last_run": "old", "lastChecked": "old"}
    state["stats"] = {"new_apps_today": 2, "new_bundles_today": 1}
    state["changes"] = {"affected_bundles": [{"bundle": "foo", "channel": "stable"}]}

    mdb.write_data_files(has_changes=False, preserve_changes_json=True)

    assert state["changes"]["affected_bundles"] == [{"bundle": "foo", "channel": "stable"}]
    assert state["stats"]["new_apps_today"] == 2
