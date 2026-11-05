"""The release cache only keeps the releases the app ever shows (last 3).

The cache is a tracked file (6.3 MB before this cap) and the phone DB mirrors
it row for row, so both grow only with repos, never with release history.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from update_release_cache import RELEASE_KEEP, trim_releases  # noqa: E402


def _release(tag, date):
    return {"tag": tag, "body": f"notes {tag}", "dateReleased": date}


class TestTrimReleases:
    def test_keeps_newest_three(self):
        releases = [
            _release("v1", "2026-01-01"),
            _release("v3", "2026-03-01"),
            _release("v2", "2026-02-01"),
            _release("v5", "2026-05-01"),
            _release("v4", "2026-04-01"),
        ]
        kept = trim_releases(releases)
        assert [r["tag"] for r in kept] == ["v5", "v4", "v3"]
        assert RELEASE_KEEP == 3

    def test_short_list_is_untouched(self):
        releases = [_release("v2", "2026-02-01"), _release("v1", "2026-01-01")]
        assert trim_releases(releases) == releases

    def test_empty_and_none(self):
        assert trim_releases([]) == []
        assert trim_releases(None) == []
