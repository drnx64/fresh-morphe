"""Tests for Fresh env names with legacy MORPHE_* fallbacks."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.config import _env


def test_fresh_env_wins(monkeypatch):
    monkeypatch.setenv("FRESH_DB_PATH", "/fresh.db")
    monkeypatch.setenv("MORPHE_DB_PATH", "/legacy.db")
    assert _env("FRESH_DB_PATH", "MORPHE_DB_PATH", "/default.db") == "/fresh.db"


def test_legacy_env_is_fallback(monkeypatch):
    monkeypatch.delenv("FRESH_DB_PATH", raising=False)
    monkeypatch.setenv("MORPHE_DB_PATH", "/legacy.db")
    assert _env("FRESH_DB_PATH", "MORPHE_DB_PATH", "/default.db") == "/legacy.db"


def test_default_used_when_both_missing(monkeypatch):
    monkeypatch.delenv("FRESH_DB_PATH", raising=False)
    monkeypatch.delenv("MORPHE_DB_PATH", raising=False)
    assert _env("FRESH_DB_PATH", "MORPHE_DB_PATH", "/default.db") == "/default.db"
