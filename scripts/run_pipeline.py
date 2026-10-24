"""MorpheTracker pipeline — 5-step flow.

FETCH → PARSE → DIFF → ACCUMULATE → PUBLISH
"""
import os
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load .env before any other imports so GITHUB_TOKEN/TG_TOKEN/TG_CHAT are available
from config import _load_dotenv
_load_dotenv()

from log import log
from state_manager import (
    ensure_dirs, load_last_run, save_last_run, save_json, load_json,
    save_new_snapshot, load_daily_buffer, STATE_DIR, RAW_DIR,
)
from config import SCANNER_COOLDOWN_HOURS

# ── Step imports ──
from fetch_patch_tree import fetch_bundle_tree
from download_bundles import download_all_bundles
from fetch_external_repos import fetch_external_repos
from parse_bundles import parse_all_bundles
from fetch_patches_names import fetch_patches_names
from diff_engine import generate_bundle_fingerprints, diff_snapshots
from merge_daily_buffer import update_daily_buffer_run, write_data_files
from update_release_cache import update_release_cache
import repoInfo


def _update_last_run_success(start_time: datetime):
    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()
    try:
        last_run_data = load_last_run()
        last_run_data["error"] = None
        last_run_data["error_type"] = None
        last_run_data["failed_at"] = None
        last_run_data["elapsed_seconds"] = round(elapsed, 1)
        last_run_data["completed_at"] = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        snapshot = load_json(os.path.join(RAW_DIR, "parsed_bundles.json"), default={})
        last_run_data["summary"] = {
            "total_bundles": len(snapshot),
            "total_apps": sum(len(r.get("apps", [])) for r in snapshot.values()),
            "duration_seconds": round(elapsed, 1),
        }
        save_last_run(last_run_data)
    except Exception:
        pass


def run():
    start_time = datetime.now()
    log.info("=== STARTING MORPHE PATCH TRACKER PIPELINE ===")
    log.info(f"Current local time: {start_time.isoformat()}")

    try:
        ensure_dirs()

        # ── FETCH ──
        log.info("STEP 1: Fetching patch tree")
        fetch_bundle_tree()

        log.info("STEP 2: Downloading bundles")
        download_all_bundles()

        log.info("STEP 2b: Fetching external repos")
        fetch_external_repos()

        # ── PARSE ──
        log.info("STEP 3: Parsing bundles")
        parse_all_bundles()

        log.info("STEP 3b: Fetching patches names")
        fetch_patches_names()

        # ── REPO INFO (avatars, stars, descriptions) ──
        log.info("STEP 3c: Fetching repo metadata (avatars, stars)")
        try:
            index_path = os.path.join(STATE_DIR, "..", "bundles", "_index.json")
            bundle_index = load_json(index_path, default=[])
            if bundle_index:
                cache_path = os.path.join(STATE_DIR, "repo_cache.json")
                repo_cache = load_json(cache_path, default={})
                now_ts = time.time()
                CACHE_TTL = 30 * 24 * 3600  # 30 days

                # `isFork` is required so caches predating fork metadata refresh once.
                fresh_urls = {url for url, entry in repo_cache.items()
                              if now_ts - entry.get("fetched_at", 0) < CACHE_TTL
                              and "bundleImageUrl" in entry
                              and "isFork" in entry}
                stale_entries = [e for e in bundle_index.values()
                                 if e.get("repo_url", "") and e["repo_url"] not in fresh_urls]

                if stale_entries:
                    log.info(f"  Fetching {len(stale_entries)} repos (cache miss)")
                    fresh_results = repoInfo.process(stale_entries)
                    for url, meta in fresh_results.items():
                        repo_cache[url] = {**meta, "bundleImageUrl": meta.get("bundleImageUrl", ""), "fetched_at": now_ts}
                    save_json(cache_path, repo_cache)
                    repoInfo.update_bundle_files(fresh_results)
                else:
                    log.info("  All repos fresh in cache, skipping API calls")
        except Exception as e:
            log.warning(f"  Repo info fetch failed (non-fatal): {e}")

        # ── DIFF ──
        log.info("STEP 4: Fingerprinting + diffing")
        generate_bundle_fingerprints()
        has_changes = diff_snapshots()

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        buffer_data = load_daily_buffer()
        is_rollover = buffer_data.get("date") and buffer_data["date"] != today_str

        # Release cache (only when relevant)
        if has_changes or is_rollover:
            log.info("  Updating release cache")
            changed_repo_urls = set()
            diff_result = load_json(os.path.join(RAW_DIR, "diff_result.json"), default={})
            for bundle in diff_result.get("affected_bundles", []):
                repo_url = bundle.get("repo_url", "")
                if repo_url:
                    changed_repo_urls.add(repo_url)
            update_release_cache(changed_repo_urls=changed_repo_urls or None)

        # ── ACCUMULATE ──
        if not has_changes and not is_rollover:
            log.info("STEP 5: Silent run — syncing data files")
            write_data_files(has_changes=False, preserve_changes_json=True)
            new_snapshot = load_json(os.path.join(RAW_DIR, "parsed_bundles.json"), default={})
            if new_snapshot:
                save_new_snapshot(new_snapshot)
            _publish_silent()
            _update_last_run_success(start_time)
            return

        log.info("STEP 5: Accumulating daily buffer")
        update_daily_buffer_run()

        # ── PUBLISH ──
        log.info("STEP 6: Generating static files + RSS")
        from generate_site import generate_static_files, generate_rss_feed, generate_daily_feed
        generate_static_files()
        generate_rss_feed()
    generate_daily_feed()

        # Telegram notification
        log.info("STEP 7: Telegram notification")
        try:
            from whats_new import generate_whats_new
            from telegram import send_or_edit
            tg_token = os.environ.get("TG_TOKEN", "")
            tg_chat = os.environ.get("TG_CHAT", "")
            if tg_token and tg_chat:
                text = generate_whats_new()
                if text:
                    send_or_edit(text)
                else:
                    log.info("  No changes to notify")
            else:
                log.info("  TG_TOKEN/TG_CHAT not set, skipping")
        except Exception as e:
            log.warning(f"  Telegram failed: {e}")

        _update_last_run_success(start_time)
        log.info("=== PIPELINE RUN COMPLETE ===")

    except Exception as e:
        end_time = datetime.now()
        elapsed = (end_time - start_time).total_seconds()
        log.error(f"Pipeline failed after {elapsed:.1f}s: {e}")
        traceback.print_exc()
        try:
            last_run_data = load_last_run()
            last_run_data["error"] = str(e)
            last_run_data["error_type"] = type(e).__name__
            last_run_data["failed_at"] = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            last_run_data["elapsed_seconds"] = round(elapsed, 1)
            save_last_run(last_run_data)
        except Exception:
            pass
        sys.exit(1)


def _publish_silent():
    from generate_site import generate_static_files, generate_rss_feed, generate_daily_feed
    generate_static_files()
    generate_rss_feed()
    generate_daily_feed()
    log.info("SILENT RUN COMPLETE")


if __name__ == "__main__":
    run()
