"""Tests for the Discord worker notifier."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.notify import (
    DiscordNotifier,
    build_finished_payload,
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
    assert embed["title"] == "Morphe worker running"


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
    assert embed["title"] == "Morphe worker finished with errors"
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
