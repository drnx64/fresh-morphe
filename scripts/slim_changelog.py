"""Rewrite data/changelog.json into the compact history shape.

`build_changelog_entry()` slims new days as they are finalized, but the days
already on disk still carry full patch prose. This script back-fills them.

Descriptions are the only thing dropped, and no historical reader renders
them (see scripts/changelog_shape.py). Names, `changes` tags, badge types,
scan numbers and bundle metadata are all preserved, so the changelog page,
bundle history, RSS and the Telegram digest keep working unchanged.

Usage:
    python scripts/slim_changelog.py           # dry run (report only)
    python scripts/slim_changelog.py --apply   # write the file

Idempotent: a second run reports 0 bytes saved and leaves the file alone.
After applying, re-sync the phone DB so its `changes` rows shrink too:
    python -m server.import_legacy --reset
"""
import argparse
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from changelog_shape import slim_changelog
from state_manager import ROOT_DATA_DIR, load_json, save_json

CHANGELOG_PATH = os.path.join(ROOT_DATA_DIR, "changelog.json")


def _minified_size(data):
    """Bytes the built site actually ships (build.js minifies this file)."""
    return len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _mb(value):
    return f"{value / 1048576:.2f} MB"


def main():
    parser = argparse.ArgumentParser(description="Slim data/changelog.json patch prose")
    parser.add_argument("--apply", action="store_true", help="write changes (default is dry run)")
    args = parser.parse_args()

    if not os.path.exists(CHANGELOG_PATH):
        print(f"No changelog at {CHANGELOG_PATH}")
        return 0

    changelog = load_json(CHANGELOG_PATH, default=[])
    if not isinstance(changelog, list):
        print(f"Unexpected changelog shape: {type(changelog).__name__}")
        return 1

    slimmed = slim_changelog(changelog)
    before = _minified_size(changelog)
    after = _minified_size(slimmed)

    print(f"Days: {len(changelog)}")
    print(f"Minified payload: {_mb(before)} -> {_mb(after)} ({before - after} bytes saved)")
    if before == after:
        print("Already compact - nothing to do.")
        return 0

    # Report the worst offenders so the win is attributable.
    pairs = sorted(
        ((_minified_size(day), _minified_size(compact), compact.get("date", "?"))
         for day, compact in zip(changelog, slimmed)),
        reverse=True,
    )
    for day_before, day_after, date in pairs[:5]:
        print(f"  {date}: {_mb(day_before)} -> {_mb(day_after)}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write the file.")
        return 0

    save_json(CHANGELOG_PATH, slimmed)
    print(f"\nWrote {CHANGELOG_PATH}")
    print("Re-run `python -m server.import_legacy --reset` to shrink the phone DB too.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
