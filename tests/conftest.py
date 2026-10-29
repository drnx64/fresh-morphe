"""Shared fixtures for the phone API / importer integration tests."""
import json
import os
import sys

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT_DIR, "data")
BUNDLE_CHANNELS = {"stable", "latest", "dev"}

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


@pytest.fixture(autouse=True)
def _no_outbound_notifications(monkeypatch):
    """Keep tests off Discord/Telegram even when a local .env has real tokens."""
    for key in ("TG_TOKEN", "TG_CHAT", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="session")
def source_index():
    with open(os.path.join(DATA_DIR, "bundles", "_index.json"), encoding="utf-8") as fh:
        return json.load(fh)

@pytest.fixture(scope="session")
def canonical_bundles(source_index):
    """Mirror the importer's last-write-wins view of the bundle index.

    The pipeline has emitted case-variant keys (eg. ``ImmortalZeus:stable`` and
    ``immortalzeus:stable``) for the same repo. The database stores one row per
    ``bundle:channel``, so tests compare against the canonicalized view rather
    than the raw index length.
    """
    result = {}
    for key in sorted(source_index):
        entry = source_index[key]
        bundle = entry.get("bundle") or key.rsplit(":", 1)[0]
        channel = entry.get("channel") or key.rsplit(":", 1)[-1]
        if channel not in BUNDLE_CHANNELS:
            channel = "stable"
        path = os.path.join(DATA_DIR, "bundles", key.replace(":", "_") + ".json")
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        result[f"{bundle}:{channel}"] = (entry, record)
    return result
