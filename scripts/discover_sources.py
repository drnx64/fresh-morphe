"""CLI for discovering Morphe patch bundle sources.

Runs independently of the fetch/parse/diff pipeline so a slow GitHub search
never blocks a worker run. Posts one Discord message when it starts and edits
that same message with the results when it ends.
"""

import argparse
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)

import config  # noqa: F401 - loads .env before provider requests
from discovery.runner import run
from discovery.state import utc_now


def _notifier(disabled):
    if disabled:
        return None
    try:
        from server.notify import DiscordNotifier
    except Exception:
        return None
    instance = DiscordNotifier.from_env()
    return instance if instance.enabled else None


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
        "--no-notify",
        action="store_true",
        help="Do not post Discord messages",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable summary",
    )
    args = parser.parse_args(argv)

    notifier = _notifier(args.no_notify)
    message_id = None
    started_at = utc_now()
    if notifier is not None:
        from server.notify import build_discovery_running_payload

        message_id = notifier.post(
            build_discovery_running_payload({"started_at": started_at})
        )

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
        if notifier is not None:
            from server.notify import build_discovery_payload

            notifier.edit(
                message_id,
                build_discovery_payload(
                    {"started_at": started_at, "finished_at": utc_now()},
                    error=str(error),
                ),
            )
        return 1

    if notifier is not None:
        from server.notify import build_discovery_payload

        notifier.edit(message_id, build_discovery_payload(summary))

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
