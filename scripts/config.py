"""Centralized configuration for Fresh Morphe pipeline.

All tuneable values in one place. Override via environment variables.
"""
import os
import re as _re


def _load_dotenv(path=".env"):
    """Minimal .env loader — parses KEY=VALUE lines, no external deps."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = _re.match(r'^(export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)', line)
            if m:
                key, val = m.group(2), m.group(3).strip()
                if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
                    val = val[1:-1]
                os.environ.setdefault(key, val)


_load_dotenv()

# Pipeline schedule
SCAN_INTERVAL_HOURS = int(os.environ.get("SCAN_INTERVAL_HOURS", "3"))

# Cooldown — skip full pipeline if last successful run was less than this many hours ago.
# Acts as the effective scan interval; cron fires hourly but pipeline self-throttles.
SCANNER_COOLDOWN_HOURS = float(os.environ.get("SCANNER_COOLDOWN_HOURS", "2.5"))

# Changelog
CHANGELOG_MAX_ENTRIES = int(os.environ.get("CHANGELOG_MAX_ENTRIES", "15"))

# Release cache — only refresh when cache is older than this (hours)
RELEASE_CACHE_TTL_HOURS = int(os.environ.get("RELEASE_CACHE_TTL_HOURS", "24"))

# HTTP retries
HTTP_MAX_RETRIES = int(os.environ.get("HTTP_MAX_RETRIES", "3"))
HTTP_TIMEOUT_SECONDS = int(os.environ.get("HTTP_TIMEOUT_SECONDS", "30"))

# Icon cache
ICON_CACHE_MAX_ENTRIES = int(os.environ.get("ICON_CACHE_MAX_ENTRIES", "600"))

# Announcement expiry (ms)
ANNOUNCEMENT_EXPIRY_MS = int(os.environ.get("ANNOUNCEMENT_EXPIRY_MS", "86400000"))

# Download skip cache — bundles skipped as incomplete/non-morphe are cached
# and only retried after this many days.
SKIP_CACHE_TTL_DAYS = int(os.environ.get("SKIP_CACHE_TTL_DAYS", "30"))

# Website base URL (used in Telegram deep links)
SITE_URL = os.environ.get("SITE_URL", "https://drnx64.github.io/fresh-morphe")
