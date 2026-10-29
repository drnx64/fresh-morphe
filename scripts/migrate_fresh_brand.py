"""Migrate pre-rebrand state to Fresh Morphe naming.

Only semantic labels move: the upstream repository is still named
``awesome-morphe``, so repo names, URLs, topics, and descriptions are left
untouched. Provider source labels and the ``provider_runs`` key change from
``awesome`` to ``fresh``.

Usage:
    python scripts/migrate_fresh_brand.py [--dry-run] [--no-db]
"""

import argparse
import json
import os
import shutil
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import state_manager


LEGACY_PROVIDER = "awesome"
PROVIDER = "fresh"


def _normalize_sources(value):
    if not isinstance(value, list):
        return value, False
    changed = False
    result = []
    for item in value:
        if item == LEGACY_PROVIDER:
            item = PROVIDER
            changed = True
        if item not in result:
            result.append(item)
    return result, changed or result != value


def _migrate_discovered(data):
    changed = False
    repos = data.get("repos")
    if not isinstance(repos, dict):
        return changed
    for entry in repos.values():
        if not isinstance(entry, dict):
            continue
        sources, touched = _normalize_sources(entry.get("sources"))
        if touched:
            entry["sources"] = sources
            changed = True
    return changed


def _migrate_review(data):
    changed = False
    items = data.get("items")
    if not isinstance(items, list):
        return changed
    for entry in items:
        if not isinstance(entry, dict):
            continue
        sources, touched = _normalize_sources(entry.get("sources"))
        if touched:
            entry["sources"] = sources
            changed = True
    return changed


def _migrate_provider_runs(data):
    if LEGACY_PROVIDER not in data:
        return False
    if PROVIDER in data:
        legacy = data.pop(LEGACY_PROVIDER)
        current = data.get(PROVIDER)
        if isinstance(legacy, dict) and isinstance(current, dict):
            for key, value in legacy.items():
                current.setdefault(key, value)
        return True
    data[PROVIDER] = data.pop(LEGACY_PROVIDER)
    return True


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _save(path, data):
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _migrate_file(path, migrate, dry_run):
    if not os.path.exists(path):
        return False
    data = _load(path)
    if not isinstance(data, dict) or not migrate(data):
        return False
    if not dry_run:
        _save(path, data)
    return True


def migrate_db(dry_run):
    target = os.path.join(state_manager.DATA_DIR, "fresh.db")
    legacy = os.path.join(state_manager.DATA_DIR, "morphe.db")
    if os.path.exists(target) or not os.path.exists(legacy):
        return False
    if not dry_run:
        shutil.move(legacy, target)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-db", action="store_true", help="leave the SQLite file alone")
    args = parser.parse_args(argv)

    state_dir = state_manager.STATE_DIR
    targets = (
        ("discovered_repos.json", _migrate_discovered),
        ("discovery_review.json", _migrate_review),
        ("provider_runs.json", _migrate_provider_runs),
    )
    changed = []
    for filename, migrate in targets:
        path = os.path.join(state_dir, filename)
        if _migrate_file(path, migrate, args.dry_run):
            changed.append(filename)

    if not args.no_db and migrate_db(args.dry_run):
        changed.append("data/morphe.db -> data/fresh.db")

    prefix = "would migrate" if args.dry_run else "migrated"
    if changed:
        print(f"{prefix}: {', '.join(changed)}")
    else:
        print("already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
