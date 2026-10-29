"""Structured logging utility for Fresh Morphe pipeline scripts.

Usage:
    from log import log
    log.info("Pipeline started")
    log.warning("Rate limit approaching")
    log.error("Failed to fetch data")
"""
import os
import sys
from datetime import datetime, timezone


VERBOSE = os.environ.get("VERBOSE", "").lower() in ("1", "true", "yes")


class Logger:
    def _ts(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def info(self, msg: str):
        print(f"[{self._ts()}] [INFO] {msg}", flush=True)

    def warn(self, msg: str):
        print(f"[{self._ts()}] [WARN] {msg}", file=sys.stderr, flush=True)

    warning = warn

    def error(self, msg: str):
        print(f"[{self._ts()}] [ERROR] {msg}", file=sys.stderr, flush=True)

    def debug(self, msg: str):
        if VERBOSE:
            print(f"[{self._ts()}] [DEBUG] {msg}", flush=True)


log = Logger()
