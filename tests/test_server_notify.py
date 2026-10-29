"""Tests for the Discord worker notifier."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.notify import (
    DiscordNotifier,
    build_discovery_payload,
    build_discovery_running_payload,
    build_finished_payload,
    build_pipeline_finished_payload,
    build_pipeline_running_payload,
    build_running_payload,
)


def summary(**overrides):
    value = {
        "started_at": "2026-10-26T09:25:48Z",
        "finished_at": "2026-10-26T09:27:53Z",
        "repos": 530,
        "targets": 698,
        "unchanged": 16,
        "fetched": 682,
        "written": 682,
        "changes": 1,
        "data_version": "b73d080da8adffe5",
        "counts": {"bundles": 1364, "apps": 1421, "jobs": 5},
        "errors": [],
        "changed_bundles": [{
            "bundle": "foo",
            "channel": "stable",
            "badge_type": "UPDATED",
            "app_count": 2,
            "previous_version": "1.2.3",
            "new_version": "1.2.4",
            "extra_badges": ["VERSION BUMP"],
        }],
    }
    value.update(overrides)
    return value


def test_running_payload_has_seconds_utc():
    embed = build_running_payload(summary())["embeds"][0]
    assert "2026-10-26 09:25:48 UTC" in embed["description"]
    assert embed["title"] == "Fresh Morphe worker running"


def test_finished_payload_includes_duration_summary_and_bundle_changes():
    embed = build_finished_payload(summary())["embeds"][0]
    assert "2026-10-26 09:25:48 UTC" in embed["description"]
    assert "2026-10-26 09:27:53 UTC" in embed["description"]
    assert "2m 05s (125s)" in embed["description"]
    assert "`foo:stable` — UPDATED · VERSION BUMP · 2 apps · v1.2.3 → v1.2.4" in embed["description"]
    assert embed["color"] == 0x3BA55D


def test_finished_payload_marks_errors():
    payload = build_finished_payload(summary(errors=[{
        "url": "https://github.com/example/repo",
        "branch": "main",
        "error": "boom",
    }]))
    embed = payload["embeds"][0]
    assert embed["title"] == "Fresh Morphe worker finished with errors"
    assert embed["color"] == 0xE0A84B
    assert any(field["name"] == "Errors" for field in embed["fields"])


def test_notifier_posts_once_then_edits_same_message():
    calls = []

    def transport(method, url, payload):
        calls.append((method, url, payload))
        return {"id": "12345"}

    notifier = DiscordNotifier(
        "https://discord.com/api/webhooks/1/token",
        transport=transport,
    )
    message_id = notifier.start(summary())
    assert message_id == "12345"
    assert notifier.finish(summary(), message_id=message_id) is True

    assert [call[0] for call in calls] == ["POST", "PATCH"]
    assert calls[0][1].endswith("?wait=true")
    assert calls[1][1].endswith("/messages/12345?wait=true")
    assert "Morphe worker finished" in calls[1][2]["embeds"][0]["title"]


def test_notifier_posts_finished_message_when_start_fails():
    calls = []

    def transport(method, url, payload):
        calls.append((method, url, payload))
        if len(calls) == 1:
            raise RuntimeError("temporary failure")
        return {"id": "12345"}

    notifier = DiscordNotifier(
        "https://discord.com/api/webhooks/1/token",
        transport=transport,
    )
    assert notifier.start(summary()) is None
    assert notifier.finish(summary(), message_id=None) is True
    assert [call[0] for call in calls] == ["POST", "POST"]
    assert "Morphe worker finished" in calls[1][2]["embeds"][0]["title"]


def test_notifier_disabled_without_url():
    notifier = DiscordNotifier("")
    assert notifier.enabled is False
    assert notifier.start(summary()) is None
    assert notifier.finish(summary()) is False


def discovery_summary(**overrides):
    value = {
        "started_at": "2026-10-29T02:00:00Z",
        "finished_at": "2026-10-29T02:03:30Z",
        "candidates": 1200,
        "verified": 1140,
        "review": 60,
        "checked_this_run": 40,
        "scanned": 1210,
        "providers": {
            "official": {"count": 12, "status": "ok"},
            "github-search": {"count": 88, "status": "ok"},
        },
        "errors": [],
        "found": [{
            "name": "X Patches",
            "repo": "user/repo",
            "url": "https://github.com/user/repo",
            "channels": ["stable"],
        }],
    }
    value.update(overrides)
    return value


def test_discovery_running_payload_is_blue_and_mentions_search():
    embed = build_discovery_running_payload(discovery_summary())["embeds"][0]
    assert embed["title"] == "Finding new repositories/bundles…"
    assert "2026-10-29 02:00:00 UTC" in embed["description"]
    assert embed["color"] == 0x5865F2


def test_discovery_payload_reports_found_bundles_and_cache_line():
    embed = build_discovery_payload(discovery_summary())["embeds"][0]
    assert embed["title"] == "Found 1 bundle"
    assert "**X Patches** — github.com/user/repo" in embed["description"]
    assert "[ saved to scanned_repos.txt ] · `stable`" in embed["description"]
    assert "3m 30s (210s)" in embed["description"]
    assert embed["color"] == 0x3BA55D


def test_discovery_payload_without_found_repos():
    embed = build_discovery_payload(discovery_summary(found=[]))["embeds"][0]
    assert embed["title"] == "No new repositories found"
    assert "No new repositories found." in embed["description"]
    assert embed["color"] == 0xE0A84B


def test_discovery_payload_caps_long_lists():
    found = [
        {
            "name": f"Repo {index}",
            "repo": f"user/repo{index}",
            "url": f"https://github.com/user/repo{index}",
            "channels": [],
        }
        for index in range(14)
    ]
    embed = build_discovery_payload(discovery_summary(found=found))["embeds"][0]
    assert embed["title"] == "Found 14 bundles"
    assert "**Repo 9**" in embed["description"]
    assert "**Repo 10**" not in embed["description"]
    assert "_+4 more_" in embed["description"]


def test_discovery_payload_marks_errors():
    payload = build_discovery_payload(discovery_summary(), error="boom")
    embed = payload["embeds"][0]
    assert embed["title"] == "Discovery failed"
    assert embed["color"] == 0xED4245
    assert any(field["name"] == "Errors" for field in embed["fields"])


def test_pipeline_running_and_finished_payloads():
    running = build_pipeline_running_payload({
        "started_at": "2026-10-29T02:10:00Z",
        "db_only": True,
    })["embeds"][0]
    assert running["title"] == "Fetching bundles — worker running…"
    assert "phone (db-only)" in running["description"]

    finished = build_pipeline_finished_payload({
        "started_at": "2026-10-29T02:10:00Z",
        "finished_at": "2026-10-29T02:14:12Z",
        "db_only": True,
        "changes": 62,
        "bundles": 1415,
        "errors": [],
    })["embeds"][0]
    assert finished["title"] == "Pipeline finished"
    assert "4m 12s (252s)" in finished["description"]
    assert "Changes `62` · Bundles `1415` · Errors `0`" in finished["description"]
    assert finished["color"] == 0x3BA55D


def test_pipeline_finished_payload_marks_silent_and_error():
    silent = build_pipeline_finished_payload({
        "started_at": "2026-10-29T02:10:00Z",
        "finished_at": "2026-10-29T02:10:20Z",
        "silent": True,
    })["embeds"][0]
    assert "Silent run" in silent["description"]
    assert silent["color"] == 0x3BA55D

    failed = build_pipeline_finished_payload(
        {"started_at": "2026-10-29T02:10:00Z", "finished_at": "2026-10-29T02:11:00Z"},
        error="kaboom",
    )["embeds"][0]
    assert failed["title"] == "Pipeline finished with errors"
    assert failed["color"] == 0xED4245
    assert "kaboom" in failed["description"]


def test_notifier_post_then_edit_same_message():
    calls = []

    def transport(method, url, payload):
        calls.append((method, url, payload))
        return {"id": "777"}

    notifier = DiscordNotifier(
        "https://discord.com/api/webhooks/1/token",
        transport=transport,
    )
    message_id = notifier.post(build_discovery_running_payload(discovery_summary()))
    assert message_id == "777"
    assert notifier.edit(message_id, build_discovery_payload(discovery_summary())) is True

    assert [call[0] for call in calls] == ["POST", "PATCH"]
    assert calls[1][1].endswith("/messages/777?wait=true")
    assert calls[1][2]["embeds"][0]["title"] == "Found 1 bundle"


def test_notifier_edit_without_id_posts_instead():
    calls = []

    def transport(method, url, payload):
        calls.append((method, url, payload))
        return {"id": "1"}

    notifier = DiscordNotifier(
        "https://discord.com/api/webhooks/1/token",
        transport=transport,
    )
    assert notifier.edit(None, build_discovery_payload(discovery_summary())) is True
    assert [call[0] for call in calls] == ["POST"]


def test_notifier_post_and_edit_are_safe_without_webhook():
    notifier = DiscordNotifier("")
    assert notifier.post(build_discovery_running_payload({})) is None
    assert notifier.edit("1", build_discovery_running_payload({})) is False
