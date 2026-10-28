import os
import sys
import re
import subprocess
from datetime import datetime, timezone
from config import CHANGELOG_MAX_ENTRIES
from app_metadata import derive_name_from_package
from state_manager import (
    load_daily_buffer,
    save_daily_buffer,
    load_json,
    save_json,
    save_new_snapshot,
    save_core_json,
    save_stats_json,
    save_changes_json,
    save_bundles_split,
    save_bundles_json,
    load_core_json,
    load_stats_json,
    load_changes_json,
    load_bundles_json,
    load_current_snapshot,
    ensure_dirs,
    RAW_DIR,
    STATE_DIR,
    OUTPUT_DIR,
    ROOT_DATA_DIR,
    ROOT_DIR
)

RELEASE_CACHE_PATH = os.path.join(STATE_DIR, "release_cache.json")
REPO_CACHE_PATH = os.path.join(STATE_DIR, "repo_cache.json")
REPO_META_FIELDS = (
    "stars",
    "avatarUrl",
    "repoDescription",
    "isArchived",
    "isFork",
    "forkParent",
    "bundleImageUrl",
)

APP_PRECEDENCE = {"NEW APP": 0, "MAJOR UPDATE": 1, "UPDATED APP": 2, "REMOVED APP": 3}
BUNDLE_PRECEDENCE = {"NEW BUNDLE": 0, "UPDATED": 1, "REMOVED BUNDLE": 2}


def now_utc_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_snapshot_stats(snapshot):
    unique_bundles = set()
    for key, b_rec in snapshot.items():
        name = key.split(':')[0]
        repo = b_rec.get("repo_url", "")
        unique_bundles.add((name, repo))
    total_bundles = len(unique_bundles)

    unique_packages = set()
    for b_rec in snapshot.values():
        for app in b_rec.get("apps", []):
            unique_packages.add(app["package"])
    total_apps = len(unique_packages)

    return total_bundles, total_apps


def build_changelog_entry(date_str, affected_bundles_dict):
    """Builds a structured changelog entry from the buffer's affected_bundles dict."""
    return {
        "date": date_str,
        "lastChecked": now_utc_iso(),
        "affected_bundles": list(affected_bundles_dict.values())
    }


def assign_scan_numbers(buffer_bundles, incoming, scan_counter):
    """Assign scan_numbers to incoming apps and merge into buffer."""
    for bundle_entry in incoming:
        b_key = f"{bundle_entry['bundle']}:{bundle_entry['channel']}"

        if b_key not in buffer_bundles:
            apps = []
            for a in bundle_entry.get("apps", []):
                app_entry = dict(a)
                app_entry["scan_numbers"] = [scan_counter]
                apps.append(app_entry)
            buffer_bundles[b_key] = {
                "bundle": bundle_entry["bundle"],
                "channel": bundle_entry["channel"],
                "badge_type": bundle_entry["badge_type"],
                "apps": apps,
                "repo_url": bundle_entry.get("repo_url", ""),
                "patches_name": bundle_entry.get("patches_name", ""),
                "extra_badges": bundle_entry.get("extra_badges", []),
                "previous_version": bundle_entry.get("previous_version", ""),
                "new_version": bundle_entry.get("new_version", ""),
                "avatarUrl": bundle_entry.get("avatarUrl", ""),
                "bundleImageUrl": bundle_entry.get("bundleImageUrl", ""),
            }
        else:
            existing = buffer_bundles[b_key]
            if BUNDLE_PRECEDENCE.get(bundle_entry["badge_type"], 99) < BUNDLE_PRECEDENCE.get(existing["badge_type"], 99):
                existing["badge_type"] = bundle_entry["badge_type"]

            for eb in bundle_entry.get("extra_badges", []):
                if eb not in existing.get("extra_badges", []):
                    existing.setdefault("extra_badges", []).append(eb)
            if not existing.get("previous_version") and bundle_entry.get("previous_version"):
                existing["previous_version"] = bundle_entry["previous_version"]
            if bundle_entry.get("new_version"):
                existing["new_version"] = bundle_entry["new_version"]
            if not existing.get("avatarUrl") and bundle_entry.get("avatarUrl"):
                existing["avatarUrl"] = bundle_entry["avatarUrl"]
            if not existing.get("bundleImageUrl") and bundle_entry.get("bundleImageUrl"):
                existing["bundleImageUrl"] = bundle_entry["bundleImageUrl"]

            existing_app_map = {a["package"]: a for a in existing["apps"]}
            for app in bundle_entry.get("apps", []):
                if app["package"] not in existing_app_map:
                    new_app = dict(app)
                    new_app["scan_numbers"] = [scan_counter]
                    existing["apps"].append(new_app)
                else:
                    extant = existing_app_map[app["package"]]
                    if APP_PRECEDENCE.get(app["badge_type"], 99) < APP_PRECEDENCE.get(extant["badge_type"], 99):
                        extant["badge_type"] = app["badge_type"]
                    if scan_counter not in extant.get("scan_numbers", []):
                        extant.setdefault("scan_numbers", []).append(scan_counter)
                    # Preserve promoted_from flag
                    if "promoted_from" in app:
                        extant["promoted_from"] = app["promoted_from"]
                    # Preserve/update patch diff data (merge, don't overwrite)
                    if "patch_diff" in app:
                        existing_diff = extant.get("patch_diff", {})
                        new_diff = app["patch_diff"]
                        for key in ("patches_added", "patches_removed", "patches_modified"):
                            existing_list = existing_diff.get(key, [])
                            incoming_list = new_diff.get(key, [])
                            merged = {}
                            for item in existing_list:
                                name = item["name"] if isinstance(item, dict) else item
                                merged[name] = item
                            for item in incoming_list:
                                name = item["name"] if isinstance(item, dict) else item
                                if name not in merged:
                                    merged[name] = item
                            existing_diff[key] = list(merged.values())
                        extant["patch_diff"] = existing_diff


def generate_markdown_changelog(date_str, affected_bundles_dict):
    """Generates a clean Markdown changelog block for the day."""
    lines = [f"## [{date_str}]"]

    snapshot = load_current_snapshot()

    def is_pre_release_app(bundle_name, package_name):
        stable_key = f"{bundle_name}:stable"
        dev_key = f"{bundle_name}:dev"
        stable_apps = snapshot.get(stable_key, {}).get("apps", [])
        dev_apps = snapshot.get(dev_key, {}).get("apps", [])
        in_stable = any(a["package"].lower().strip() == package_name.lower().strip() for a in stable_apps)
        in_dev = any(a["package"].lower().strip() == package_name.lower().strip() for a in dev_apps)
        return in_dev and not in_stable

    # Group by base bundle name
    updates_by_bundle = {}
    for b_key, info in affected_bundles_dict.items():
        b_name = info["bundle"]
        if b_name not in updates_by_bundle:
            updates_by_bundle[b_name] = {"channels": [], "apps": [], "badge_type": info["badge_type"]}
        if info["channel"] not in updates_by_bundle[b_name]["channels"]:
            updates_by_bundle[b_name]["channels"].append(info["channel"])
        for app in info.get("apps", []):
            existing = next((a for a in updates_by_bundle[b_name]["apps"] if a["package"] == app["package"]), None)
            if existing:
                if APP_PRECEDENCE.get(app["badge_type"], 99) < APP_PRECEDENCE.get(existing["badge_type"], 99):
                    existing["badge_type"] = app["badge_type"]
                app_scan = app.get("scan_numbers", [])
                if app_scan:
                    existing.setdefault("scan_numbers", [])
                    for sn in app_scan:
                        if sn not in existing["scan_numbers"]:
                            existing["scan_numbers"].append(sn)
            else:
                updates_by_bundle[b_name]["apps"].append(dict(app))
        if BUNDLE_PRECEDENCE.get(info["badge_type"], 99) < BUNDLE_PRECEDENCE.get(updates_by_bundle[b_name]["badge_type"], 99):
            updates_by_bundle[b_name]["badge_type"] = info["badge_type"]

    if not updates_by_bundle:
        lines.append("- No updates.")
        lines.append("")
        return "\n".join(lines)

    for b_name in sorted(updates_by_bundle.keys()):
        info = updates_by_bundle[b_name]
        is_new_bundle = info["badge_type"] == "NEW BUNDLE"

        if is_new_bundle:
            channels_str = ", ".join(sorted(info["channels"]))
            lines.append(f"- **NEW BUNDLE** Bundle by **{b_name}** ({channels_str})")
        elif info["badge_type"] == "REMOVED BUNDLE":
            lines.append(f"- **REMOVED** Bundle **{b_name}**")
        else:
            lines.append(f"- **UPDATED** Bundle **{b_name}**")

        sorted_apps = sorted(
            info["apps"],
            key=lambda x: (x.get("app_name") or derive_name_from_package(x.get("package", ""))).lower(),
        )
        for app in sorted_apps:
            status = app["badge_type"]
            is_pre = is_pre_release_app(b_name, app["package"])
            pre_str = " [PRE-RELEASE]" if is_pre else ""
            app_name = app.get("app_name") or derive_name_from_package(app["package"])
            lines.append(f"  - **{status}**{pre_str} {app_name} (`{app['package']}`) in {b_name} patches")

    lines.append("")
    return "\n".join(lines)


def apply_repo_metadata(bundles):
    """Re-apply cached repo metadata (stars, description, fork, …) to bundles.

    The snapshot is rebuilt from parsing every run, so metadata added by the
    repoInfo step would otherwise be dropped when bundle files are rewritten.
    """
    repo_cache = load_json(REPO_CACHE_PATH, default={})
    if not repo_cache:
        return
    for bundle_data in bundles.values():
        meta = repo_cache.get(bundle_data.get("repo_url", ""))
        if not meta:
            continue
        for field in REPO_META_FIELDS:
            if field in meta:
                bundle_data[field] = meta[field]


def merge_release_notes(bundles):
    """Read release notes from cache and merge into bundles dict."""
    release_cache = load_json(RELEASE_CACHE_PATH, default={})
    if not release_cache:
        return
    for bundle_key, bundle_data in bundles.items():
        repo_url = bundle_data.get("repo_url", "")
        if not repo_url:
            continue
        repo_cache = release_cache.get(repo_url, {})
        releases = repo_cache.get("releases", [])
        if not releases:
            continue
        version = bundle_data.get("version", "")
        matched = _match_release_to_version(version, releases)
        if matched:
            bundle_data["release_notes"] = matched.get("body", "")
            bundle_data["release_tag"] = matched.get("tag", "")
            bundle_data["release_date"] = matched.get("dateReleased", "")
            bundle_data["isPreRelease"] = _is_pre_release(matched)
        else:
            latest = None
            for r in releases:
                if not r.get("prerelease", False):
                    latest = r
                    break
            if not latest and releases:
                latest = releases[0]
            if latest:
                bundle_data["release_notes"] = latest.get("body", "")
                bundle_data["release_tag"] = latest.get("tag", "")
                bundle_data["release_date"] = latest.get("dateReleased", "")
                bundle_data["isPreRelease"] = _is_pre_release(latest)


def _is_pre_release(release):
    """Determine if a release is a pre-release based on API flag or tag name."""
    if release.get("prerelease"):
        return True
    tag = release.get("tag", "").lower()
    pre_release_patterns = ["-alpha", "-beta", "-rc", "-dev", "-snapshot", "-canary", "-nightly"]
    return any(p in tag for p in pre_release_patterns)


def _match_release_to_version(version, releases):
    from state_manager import match_release_to_version
    return match_release_to_version(version, releases)


def finalize_buffer(buffer_data):
    """
    Finalizes the daily buffer:
    1. Appends to data/changelog.json
    2. Prepends to data/output/changelog.md
    3. Writes the daily markdown changelog to data/output/today_changelog.md
    4. Generates data/core.json, data/stats.json, data/changes.json, data/bundles.json
    """
    ensure_dirs()
    date_str = buffer_data["date"]
    affected_bundles_dict = buffer_data.get("affected_bundles", {})

    # Skip finalization if the buffer has no changes (idempotency)
    if not affected_bundles_dict:
        print(f"[*] Skipping finalization for {date_str}: buffer is empty.")
        return

    print(f"[*] Finalizing daily buffer for date: {date_str}...")

    try:
        # 1. Update changelog.json — write directly to data/changelog.json (tracked by git)
        changelog_json_path = os.path.join(ROOT_DATA_DIR, "changelog.json")
        changelog_json = load_json(changelog_json_path, default=[])

        changelog_json = [entry for entry in changelog_json if entry.get("date") != date_str]
        new_entry = build_changelog_entry(date_str, affected_bundles_dict)
        changelog_json.insert(0, new_entry)
        changelog_json = changelog_json[:CHANGELOG_MAX_ENTRIES]
        save_json(changelog_json_path, changelog_json)
        print(f"[+] Changelog JSON written: {len(changelog_json)} entries")
    except Exception as e:
        print(f"[!] Failed to write changelog.json: {e}")

    try:
        # 2. Update changelog.md
        changelog_md_path = os.path.join(OUTPUT_DIR, "changelog.md")
        daily_md = generate_markdown_changelog(date_str, affected_bundles_dict)

        today_changelog_path = os.path.join(OUTPUT_DIR, "today_changelog.md")
        with open(today_changelog_path, "w", encoding="utf-8") as f:
            f.write(daily_md)

        existing_md = ""
        if os.path.exists(changelog_md_path):
            try:
                with open(changelog_md_path, "r", encoding="utf-8") as f:
                    existing_md = f.read()
            except Exception as e:
                print(f"Error reading changelog.md: {e}")

        header = "# Morphe Patch Tracker Changelog\n\n"
        if existing_md.startswith(header):
            content_body = existing_md[len(header):]
        else:
            content_body = existing_md

        with open(changelog_md_path, "w", encoding="utf-8") as f:
            f.write(header)
            f.write(daily_md)
            f.write(content_body)
    except Exception as e:
        print(f"[!] Failed to write changelog.md: {e}")

    try:
        # 3. Update data files (core.json, stats.json, changes.json, bundles.json)
        snapshot = load_current_snapshot()
        total_bundles, total_apps = _compute_snapshot_stats(snapshot)

        all_apps = []
        for b_info in affected_bundles_dict.values():
            all_apps.extend(b_info.get("apps", []))

        core = {
            "date": date_str,
            "last_run": now_utc_iso(),
            "lastChecked": now_utc_iso()
        }
        stats = {
            "total_bundles": total_bundles,
            "total_apps": total_apps,
            "new_apps_today": len(set(a["package"] for a in all_apps if a.get("badge_type") == "NEW APP")),
            "new_bundles_today": len(set(b["bundle"] for b in affected_bundles_dict.values() if b.get("badge_type") == "NEW BUNDLE"))
        }
        changes = {
            "affected_bundles": list(affected_bundles_dict.values())
        }
        bundles = dict(snapshot)
        merge_release_notes(bundles)
        apply_repo_metadata(bundles)
        save_core_json(core)
        save_stats_json(stats)
        save_changes_json(changes)
        save_bundles_split(bundles)
        save_bundles_json(bundles)
        print("[+] Finalization state files written successfully.")
    except Exception as e:
        print(f"[!] Failed to write state files: {e}")


def update_data_files(today_str, buffer_data, snapshot):
    """
    Updates data/core.json + data/stats.json + data/changes.json + data/bundles.json with the current snapshot and today's accumulated changes.
    """
    total_bundles, total_apps = _compute_snapshot_stats(snapshot)

    affected_bundles_list = list(buffer_data.get("affected_bundles", {}).values())
    has_new_changes = len(affected_bundles_list) > 0

    if not has_new_changes:
        existing_core = load_core_json()
        existing_stats = load_stats_json()
        existing_changes = load_changes_json()
        same_day = existing_core.get("date") == today_str
        if same_day and existing_changes and "affected_bundles" in existing_changes:
            changes_to_save = existing_changes
            new_apps_today = existing_stats.get("new_apps_today", 0)
            new_bundles_today = existing_stats.get("new_bundles_today", 0)
        else:
            changes_to_save = {"affected_bundles": []}
            new_apps_today = 0
            new_bundles_today = 0
    else:
        all_apps = []
        for b_info in buffer_data.get("affected_bundles", {}).values():
            all_apps.extend(b_info.get("apps", []))
        changes_to_save = {
            "affected_bundles": affected_bundles_list
        }
        new_apps_today = len(set(a["package"] for a in all_apps if a.get("badge_type") == "NEW APP"))
        new_bundles_today = len(set(b["bundle"] for b in buffer_data.get("affected_bundles", {}).values() if b.get("badge_type") == "NEW BUNDLE"))

    core = {
        "date": today_str,
        "last_run": now_utc_iso(),
        "lastChecked": now_utc_iso()
    }
    stats = {
        "total_bundles": total_bundles,
        "total_apps": total_apps,
        "new_apps_today": new_apps_today,
        "new_bundles_today": new_bundles_today
    }
    bundles = dict(snapshot)
    merge_release_notes(bundles)
    apply_repo_metadata(bundles)
    save_core_json(core)
    save_stats_json(stats)
    save_changes_json(changes_to_save)
    save_bundles_split(bundles)
    save_bundles_json(bundles)
    print("[*] Live state files updated with the current snapshot.")


def write_data_files(has_changes=True, preserve_changes_json=False):
    """Read current snapshot and write all 4 data files. Safe to call anytime.

    If preserve_changes_json is True, changes.json is not overwritten unless
    the UTC date has changed, in which case the new day starts empty.
    Used on silent runs to keep the previously-written accumulated changes.
    """
    # Load snapshot directly — do NOT rotate snapshots here
    # Snapshot rotation only happens in update_daily_buffer_run()
    snapshot = load_current_snapshot()
    if not snapshot:
        # Fallback: try loading parsed_bundles.json if snapshot not yet written
        parsed_path = os.path.join(RAW_DIR, "parsed_bundles.json")
        snapshot = load_json(parsed_path, default={})
    if not snapshot:
        print("[*] No snapshot data to write.")
        return

    total_bundles, total_apps = _compute_snapshot_stats(snapshot)

    existing_core = load_core_json()
    existing_stats = load_stats_json()

    now = now_utc_iso()
    today_str = now.split("T")[0]
    date_changed = existing_core.get("date") != today_str
    new_apps_today = existing_stats.get("new_apps_today", 0)
    new_bundles_today = existing_stats.get("new_bundles_today", 0)
    core = {
        "date": today_str,
        "last_run": existing_core.get("last_run") or now,
        "lastChecked": existing_core.get("lastChecked") or now
    }

    # Write changes.json: empty when no changes, otherwise keep existing historical data
    # If preserve_changes_json is True (silent run), skip writing changes.json entirely
    changes_to_save = None
    if date_changed:
        # A new UTC day always starts with an empty public changes payload.
        changes_to_save = {"affected_bundles": []}
        new_apps_today = 0
        new_bundles_today = 0
    elif not preserve_changes_json:
        if has_changes:
            existing_changes = load_changes_json()
            # Clean patches_name in historical changes entries
            if existing_changes and "affected_bundles" in existing_changes:
                _clean_re = re.compile(r'\s+for use with\s+Morphe', re.IGNORECASE)
                _clean_re2 = re.compile(r'\s+for\s+Morphe', re.IGNORECASE)
                for ab in existing_changes["affected_bundles"]:
                    if ab.get("patches_name"):
                        ab["patches_name"] = _clean_re.sub('', ab["patches_name"]).strip()
                        ab["patches_name"] = _clean_re2.sub('', ab["patches_name"]).strip()
            changes_to_save = existing_changes if existing_changes and "affected_bundles" in existing_changes else {"affected_bundles": []}
        else:
            changes_to_save = {"affected_bundles": []}

    stats = {
        "total_bundles": total_bundles,
        "total_apps": total_apps,
        "new_apps_today": new_apps_today,
        "new_bundles_today": new_bundles_today
    }

    bundles = dict(snapshot)
    merge_release_notes(bundles)
    apply_repo_metadata(bundles)
    save_core_json(core)
    save_stats_json(stats)
    if changes_to_save is not None:
        save_changes_json(changes_to_save)
    save_bundles_split(bundles)
    save_bundles_json(bundles)
    print(f"[*] Data files synced: {total_bundles} bundles, {total_apps} apps.")


def update_daily_buffer_run(publish=True):
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    buffer_data = load_daily_buffer()

    # If the buffer date is from a previous day, finalize it first
    finalized = False
    if buffer_data.get("date") and buffer_data["date"] != today_str:
        if buffer_data.get("affected_bundles"):
            finalize_buffer(buffer_data)
            finalized = True
            # Clear public/msg.txt on day rollover
            msg_path = os.path.join(ROOT_DIR, "public", "msg.txt")
            try:
                with open(msg_path, "w", encoding="utf-8") as f:
                    f.write("[]")
                print(f"[+] Cleared announcements: {msg_path}")
            except Exception as e:
                print(f"[!] Failed to clear announcements: {e}")
        else:
            print(f"[*] Older buffer found for {buffer_data['date']} but it was empty. Skipping finalization.")

        buffer_data = {
            "date": today_str,
            "lastChecked": now_utc_iso(),
            "scan_counter": 0,
            "affected_bundles": {}
        }
    elif not buffer_data.get("date"):
        buffer_data = {
            "date": today_str,
            "lastChecked": now_utc_iso(),
            "scan_counter": 0,
            "affected_bundles": {}
        }
    else:
        buffer_data["lastChecked"] = now_utc_iso()

    # Load current run diff
    diff_path = os.path.join(RAW_DIR, "diff_result.json")
    diff_result = load_json(diff_path, default={"affected_bundles": []})
    incoming = diff_result.get("affected_bundles", [])

    # Increment scan counter if there are incoming changes
    scan_counter = buffer_data.get("scan_counter", 0)
    if incoming:
        scan_counter += 1
        buffer_data["scan_counter"] = scan_counter

    # Merge incoming affected_bundles into buffer with scan numbers
    buffer_bundles = buffer_data.setdefault("affected_bundles", {})
    assign_scan_numbers(buffer_bundles, incoming, scan_counter)

    # Save daily buffer
    save_daily_buffer(buffer_data)

    # Save the parsed bundles as the new current snapshot (and rotate rollbacks)
    new_snapshot_path = os.path.join(RAW_DIR, "parsed_bundles.json")
    new_snapshot = load_json(new_snapshot_path, default={})
    if new_snapshot:
        save_new_snapshot(new_snapshot)
        print("[*] Current snapshot updated.")

        # Always update data files with the latest snapshot and today's accumulated changes
        update_data_files(today_str, buffer_data, new_snapshot)

    # If we finalized a previous day, trigger site generation and notification
    if finalized:
        if publish:
            print("[*] Running site generator...")
            gen_script = os.path.join(os.path.dirname(__file__), "generate_site.py")
            subprocess.run([sys.executable, gen_script], check=True)
        else:
            print("[*] DB-only mode: skipping site generator.")

    # If --finalize argument is passed, force finalization of the current day
    elif len(sys.argv) > 1 and sys.argv[1] == "--finalize":
        if buffer_data.get("affected_bundles"):
            finalize_buffer(buffer_data)

            buffer_data = {
                "date": today_str,
                "lastChecked": now_utc_iso(),
                "affected_bundles": {}
            }
            save_daily_buffer(buffer_data)

            if publish:
                print("[*] Running site generator (forced finalization)...")
                gen_script = os.path.join(os.path.dirname(__file__), "generate_site.py")
                subprocess.run([sys.executable, gen_script], check=True)
            else:
                print("[*] DB-only mode: skipping site generator.")

            print("[*] Today's changelog saved to output directory.")
        else:
            print("[*] Force finalization skipped: daily buffer is empty.")


if __name__ == "__main__":
    ensure_dirs()
    update_daily_buffer_run()
