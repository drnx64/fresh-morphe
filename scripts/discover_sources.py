"""CLI for discovering Morphe patch bundle sources."""

import argparse
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: F401 - loads .env before provider requests
from discovery.runner import run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and verify without writing state files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run providers and re-verify every candidate",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of candidates to verify this run",
    )
    parser.add_argument(
        "--no-search",
        action="store_true",
        help="Skip GitHub repository search",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable summary",
    )
    args = parser.parse_args(argv)

    try:
        summary = run(
            force=args.force,
            limit=args.limit,
            search=not args.no_search,
            dry_run=args.dry_run,
            quiet=args.json,
        )
    except Exception as error:
        print(f"[discovery] failed: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
