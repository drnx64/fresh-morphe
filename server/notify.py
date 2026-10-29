"""Discord webhook notifications for phone worker runs.

Only the worker entry point enables this module. Every run posts one
"running" message and edits that same message when it finishes, so a healthy
hourly loop produces one Discord message instead of two.
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

from server.db import utc_now


DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"
DISCORD_TIMEOUT_SECONDS = 10
DISCORD_MAX_ATTEMPTS = 3
DISCORD_MAX_CHANGES = 10
DISCORD_MAX_ERRORS = 3
DISCORD_MAX_DESCRIPTION = 3900
DISCORD_MAX_FIELD = 1000

COLOR_RUNNING = 0xE0A84B
COLOR_OK = 0x3BA55D
COLOR_PARTIAL = 0xE0A84B
COLOR_ERROR = 0xED4245
COLOR_DISCOVERY = 0x5865F2

SCANNED_CACHE_LABEL = "scanned_repos.txt"


def _clean(value, limit):
    text = str(value or "").replace("\r", "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _format_timestamp(value):
    """Render a stored ISO timestamp as explicit UTC with seconds."""
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return raw


def _parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_duration(started_at, finished_at):
    started = _parse_timestamp(started_at)
    finished = _parse_timestamp(finished_at)
    if not started or not finished:
        return "unknown"
    seconds = max(0, int((finished - started).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remainder:02d}s ({seconds}s)"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m ({seconds}s)"


def _change_lines(summary):
    entries = summary.get("changed_bundles") or []
    if not entries:
        return "No bundle changes."

    lines = []
    for entry in entries[:DISCORD_MAX_CHANGES]:
        if not isinstance(entry, dict):
            continue
        key = f'{entry.get("bundle", "?")}:{entry.get("channel", "?")}'
        bits = [str(entry.get("badge_type") or "UPDATED")]
        bits.extend(str(badge) for badge in (entry.get("extra_badges") or []) if badge)
        app_count = entry.get("app_count")
        if app_count:
            bits.append(f'{app_count} app{"s" if app_count != 1 else ""}')
        previous = str(entry.get("previous_version") or "").lstrip("v")
        current = str(entry.get("new_version") or "").lstrip("v")
        if previous and current and previous != current:
            bits.append(f"v{previous} → v{current}")
        elif current:
            bits.append(f"v{current}")
        lines.append(f"• `{key}` — {' · '.join(bits)}")

    hidden = len(entries) - len(lines)
    if hidden > 0:
        lines.append(f"_+{hidden} more_")
    return "\n".join(lines) if lines else "No bundle changes."


def _worker_field(summary):
    return (
        f'Repos `{summary.get("repos", 0)}` · Targets `{summary.get("targets", 0)}`\n'
        f'Unchanged `{summary.get("unchanged", 0)}` · Fetched `{summary.get("fetched", 0)}` · '
        f'Written `{summary.get("written", 0)}`'
    )


def _database_field(summary):
    counts = summary.get("counts") or {}
    lines = [
        f'Changes `{summary.get("changes", 0)}` · Errors `{len(summary.get("errors") or [])}`',
        f'Version `{summary.get("data_version") or "n/a"}`',
    ]
    if counts:
        lines.append(
            f'Bundles `{counts.get("bundles", 0)}` · Apps `{counts.get("apps", 0)}` · '
            f'Jobs `{counts.get("jobs", 0)}`'
        )
    return "\n".join(lines)


def _errors_field(summary, error=None):
    errors = list(summary.get("errors") or [])
    if error is not None:
        errors.append({"url": "worker", "branch": "", "error": str(error)})
    if not errors:
        return None
    lines = []
    for item in errors[:DISCORD_MAX_ERRORS]:
        url = str(item.get("url") or "unknown")
        branch = str(item.get("branch") or "")
        label = f"{url}@{branch}" if branch else url
        detail = _clean(item.get("error"), 180)
        lines.append(f'• `{_clean(label, 120)}`' + (f": {detail}" if detail else ""))
    hidden = len(errors) - len(lines)
    if hidden > 0:
        lines.append(f"_+{hidden} more_")
    return _clean("\n".join(lines), DISCORD_MAX_FIELD)


def build_running_payload(summary):
    started = _format_timestamp(summary.get("started_at"))
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": "Fresh Morphe worker running",
            "description": f"Started: `{started}`\nWaiting for mirror results…",
            "color": COLOR_RUNNING,
            "footer": {"text": "Fresh Morphe • phone worker"},
        }],
    }


def build_finished_payload(summary, error=None):
    errors = list(summary.get("errors") or [])
    if error is not None:
        errors.append({"url": "worker", "branch": "", "error": str(error)})
    failed = bool(errors)
    title = "Fresh Morphe worker finished with errors" if failed else "Fresh Morphe worker finished"
    color = COLOR_ERROR if error is not None else (COLOR_PARTIAL if failed else COLOR_OK)
    started = _format_timestamp(summary.get("started_at"))
    finished = _format_timestamp(summary.get("finished_at") or utc_now())
    duration = _format_duration(summary.get("started_at"), summary.get("finished_at") or utc_now())

    description_parts = [
        f"Started: `{started}`",
        f"Finished: `{finished}`",
        f"Duration: `{duration}`",
        "",
        "**Changed bundles**",
        _change_lines(summary),
    ]
    if summary.get("bootstrap_suppressed"):
        description_parts.extend(["", "_Bootstrap run — changelog suppressed._"])
    if error is not None:
        description_parts.extend(["", f"**Error:** {_clean(error, 500)}"])

    fields = [
        {"name": "Worker", "value": _clean(_worker_field(summary), DISCORD_MAX_FIELD), "inline": True},
        {"name": "Database", "value": _clean(_database_field(summary), DISCORD_MAX_FIELD), "inline": True},
    ]
    error_field = _errors_field(summary, error)
    if error_field:
        fields.append({"name": "Errors", "value": error_field, "inline": False})

    return {
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": title,
            "description": _clean("\n".join(description_parts), DISCORD_MAX_DESCRIPTION),
            "color": color,
            "fields": fields,
            "footer": {"text": "Fresh Morphe • phone worker"},
        }],
    }


def _found_lines(summary):
    found = summary.get("found") or []
    if not found:
        return "No new repositories found."
    lines = []
    for item in found[:DISCORD_MAX_CHANGES]:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name") or item.get("repo") or "?", 80)
        url = str(item.get("url") or "").replace("https://", "").replace("http://", "")
        channels = ", ".join(str(channel) for channel in (item.get("channels") or []))
        lines.append(f"**{name}** — {_clean(url or item.get('repo'), 180)}")
        suffix = f" · `{channels}`" if channels else ""
        lines.append(f"[ saved to {SCANNED_CACHE_LABEL} ]{suffix}")
    hidden = len(found) - min(len(found), DISCORD_MAX_CHANGES)
    if hidden > 0:
        lines.append(f"_+{hidden} more_")
    return "\n".join(lines) if lines else "No new repositories found."


def _provider_field(summary):
    providers = summary.get("providers") or {}
    if not providers:
        return None
    bits = []
    for name, info in providers.items():
        count = (info or {}).get("count", 0)
        status = (info or {}).get("status", "ok")
        marker = "!" if status in ("error", "skipped") else ""
        bits.append(f"`{name}{marker}` {count}")
    return _clean(" · ".join(bits), DISCORD_MAX_FIELD)


def build_discovery_running_payload(summary):
    started = _format_timestamp(summary.get("started_at"))
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": "Finding new repositories/bundles…",
            "description": f"Started: `{started}`\nScanning GitHub + known sources…",
            "color": COLOR_DISCOVERY,
            "footer": {"text": "Fresh Morphe • discovery"},
        }],
    }


def build_discovery_payload(summary, error=None):
    errors = list(summary.get("errors") or [])
    if error is not None:
        errors.append(f"discovery: {error}")
    found = summary.get("found") or []
    if error is not None:
        title = "Discovery failed"
        color = COLOR_ERROR
    elif found:
        title = f"Found {len(found)} bundle{'s' if len(found) != 1 else ''}"
        color = COLOR_OK
    else:
        title = "No new repositories found"
        color = COLOR_PARTIAL

    started = _format_timestamp(summary.get("started_at"))
    finished = _format_timestamp(summary.get("finished_at") or utc_now())
    duration = _format_duration(
        summary.get("started_at"), summary.get("finished_at") or utc_now()
    )
    description_parts = [
        f"Started: `{started}`",
        f"Finished: `{finished}`",
        f"Duration: `{duration}`",
        "",
        f"**{title}**",
        _found_lines(summary),
    ]
    if error is not None:
        description_parts.extend(["", f"**Error:** {_clean(error, 500)}"])

    fields = []
    provider_field = _provider_field(summary)
    if provider_field:
        fields.append({"name": "Sources", "value": provider_field, "inline": False})
    fields.append({
        "name": "Scan",
        "value": (
            f'Candidates `{summary.get("candidates", 0)}` · '
            f'Verified `{summary.get("verified", 0)}` · '
            f'Review `{summary.get("review", 0)}`\n'
            f'Checked this run `{summary.get("checked_this_run", 0)}` · '
            f'Cached `{summary.get("scanned", 0)}`'
        ),
        "inline": False,
    })
    error_field = _errors_field({"errors": [{"url": item, "error": ""} for item in errors]})
    if error_field:
        fields.append({"name": "Errors", "value": error_field, "inline": False})

    return {
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": title,
            "description": _clean("\n".join(description_parts), DISCORD_MAX_DESCRIPTION),
            "color": color,
            "fields": fields,
            "footer": {"text": "Fresh Morphe • discovery"},
        }],
    }


def build_pipeline_running_payload(summary):
    started = _format_timestamp(summary.get("started_at"))
    mode = "phone (db-only)" if summary.get("db_only") else "full"
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": "Fetching bundles — worker running…",
            "description": (
                f"Started: `{started}`\n"
                f"Mode: `{mode}`\n"
                "Downloading, parsing, and diffing patches…"
            ),
            "color": COLOR_RUNNING,
            "footer": {"text": "Fresh Morphe • pipeline"},
        }],
    }


def build_pipeline_finished_payload(summary, error=None):
    errors = list(summary.get("errors") or [])
    if error is not None:
        errors.append(str(error))
    failed = bool(errors)
    title = "Pipeline finished with errors" if failed else "Pipeline finished"
    color = COLOR_ERROR if error is not None else (COLOR_PARTIAL if failed else COLOR_OK)

    started = _format_timestamp(summary.get("started_at"))
    finished = _format_timestamp(summary.get("finished_at") or utc_now())
    duration = _format_duration(
        summary.get("started_at"), summary.get("finished_at") or utc_now()
    )
    description_parts = [
        f"Started: `{started}`",
        f"Finished: `{finished}`",
        f"Duration: `{duration}`",
        "",
        f'Changes `{summary.get("changes", 0)}` · '
        f'Bundles `{summary.get("bundles", 0)}` · '
        f'Errors `{len(errors)}`',
    ]
    if summary.get("silent"):
        description_parts.extend(["", "_Silent run — no bundle changes._"])
    if error is not None:
        description_parts.extend(["", f"**Error:** {_clean(error, 500)}"])

    fields = [{
        "name": "Mode",
        "value": "phone (db-only)" if summary.get("db_only") else "full",
        "inline": True,
    }]
    error_field = _errors_field({"errors": [{"url": item, "error": ""} for item in errors]})
    if error_field:
        fields.append({"name": "Errors", "value": error_field, "inline": False})

    return {
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": title,
            "description": _clean("\n".join(description_parts), DISCORD_MAX_DESCRIPTION),
            "color": color,
            "fields": fields,
            "footer": {"text": "Fresh Morphe • pipeline"},
        }],
    }


def _valid_webhook_url(value):
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"discord.com", "discordapp.com"}
        and parsed.path.startswith("/api/webhooks/")
    )


class DiscordNotifier:
    """One-message-per-run Discord notifier.

    Network failures are logged and swallowed so notifications can never make
    the worker fail. If the initial post fails, the finished payload is posted
    as a normal message instead of being lost.
    """

    def __init__(self, webhook_url, transport=None, sleeper=None, timeout=DISCORD_TIMEOUT_SECONDS):
        self.webhook_url = str(webhook_url or "").strip()
        self.enabled = _valid_webhook_url(self.webhook_url)
        self.transport = transport or self._http_json
        self.sleeper = sleeper or time.sleep
        self.timeout = timeout

    @classmethod
    def from_env(cls):
        return cls(os.environ.get(DISCORD_WEBHOOK_ENV, ""))

    def _endpoint(self, suffix=""):
        return self.webhook_url.rstrip("/") + suffix

    def post(self, payload):
        """Post a payload and return the Discord message id (or None)."""
        if not self.enabled:
            return None
        try:
            response = self.transport(
                "POST",
                self._endpoint("?wait=true"),
                payload,
            )
        except Exception as exc:
            print(f"[notify] could not post message: {exc}", flush=True)
            return None
        return str((response or {}).get("id") or "") or None

    def edit(self, message_id, payload):
        """Edit an existing message. Posts a new one if the id is missing."""
        if not self.enabled:
            return False
        try:
            if message_id:
                self.transport(
                    "PATCH",
                    self._endpoint(f"/messages/{message_id}?wait=true"),
                    payload,
                )
            else:
                self.transport("POST", self._endpoint("?wait=true"), payload)
            return True
        except Exception as exc:
            print(f"[notify] could not edit message: {exc}", flush=True)
            return False

    def start(self, summary):
        if not self.enabled:
            return None
        try:
            response = self.transport(
                "POST",
                self._endpoint("?wait=true"),
                build_running_payload(summary),
            )
        except Exception as exc:
            print(f"[notify] could not post running message: {exc}", flush=True)
            return None
        message_id = str((response or {}).get("id") or "")
        if message_id:
            print("[notify] running message posted", flush=True)
        else:
            print("[notify] running message posted without an id", flush=True)
        return message_id or None

    def finish(self, summary, message_id=None, error=None):
        if not self.enabled:
            return False
        payload = build_finished_payload(summary, error=error)
        try:
            if message_id:
                self.transport(
                    "PATCH",
                    self._endpoint(f"/messages/{message_id}?wait=true"),
                    payload,
                )
                print("[notify] final message edited", flush=True)
            else:
                self.transport("POST", self._endpoint("?wait=true"), payload)
                print("[notify] final message posted", flush=True)
            return True
        except Exception as exc:
            print(f"[notify] could not finish notification: {exc}", flush=True)
            return False

    def _http_json(self, method, url, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        last_error = None
        for attempt in range(1, DISCORD_MAX_ATTEMPTS + 1):
            request = urllib.request.Request(
                url,
                data=body,
                method=method,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "FreshMorpheWorker/1.0",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
            except urllib.error.HTTPError as exc:
                last_error = exc
                retry_after = None
                try:
                    detail = json.loads(exc.read().decode("utf-8"))
                    retry_after = float(detail.get("retry_after") or 0)
                except (ValueError, TypeError):
                    retry_after = None
                if (exc.code == 429 or 500 <= exc.code < 600) and attempt < DISCORD_MAX_ATTEMPTS:
                    self.sleeper(min(30.0, retry_after if retry_after is not None else attempt * 2.0))
                    continue
                raise RuntimeError(f"Discord HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < DISCORD_MAX_ATTEMPTS:
                    self.sleeper(attempt * 2.0)
                    continue
                raise RuntimeError("Discord request failed") from exc
        raise RuntimeError("Discord request failed") from last_error


def main(argv=None):
    parser = argparse.ArgumentParser(description="Send a Discord test notification")
    parser.add_argument("--test", action="store_true", help="post and edit one test message")
    args = parser.parse_args(argv)
    if not args.test:
        parser.error("only --test is supported")
    notifier = DiscordNotifier.from_env()
    if not notifier.enabled:
        print(f"[notify] {DISCORD_WEBHOOK_ENV} is missing or invalid", flush=True)
        return 1
    summary = {
        "started_at": utc_now(),
        "repos": 0,
        "targets": 0,
        "unchanged": 0,
        "fetched": 0,
        "written": 0,
        "changes": 0,
        "changed_bundles": [],
        "errors": [],
    }
    message_id = notifier.start(summary)
    summary["finished_at"] = utc_now()
    notifier.finish(summary, message_id=message_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
