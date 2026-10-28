import os
import sys
import hashlib
import json
from datetime import datetime, timezone
from state_manager import (
    load_current_snapshot,
    rebuild_snapshot_from_bundles,
    load_json,
    save_json,
    save_last_run,
    load_last_run,
    ensure_dirs,
    load_claimed_keys,
    is_claimed,
    repo_slug,
    RAW_DIR
)

def _repo_key_map(snapshot):
    """Map normalized repo slug -> list of snapshot keys sharing that repo."""
    mapping = {}
    for key, record in snapshot.items():
        slug = repo_slug(record.get("repo_url", ""))
        if slug:
            mapping.setdefault(slug, []).append(key)
    return mapping

def compute_fingerprint(bundle_name, apps, channel):
    """Computes fingerprint = SHA256(bundle_name + canonical_hash(apps) + channel)."""
    if not apps:
        hash_input = f"{bundle_name}{channel}"
    elif isinstance(apps[0], str):
        sorted_apps = sorted([pkg.lower().strip() for pkg in apps])
        hash_input = f"{bundle_name}{''.join(sorted_apps)}{channel}"
    else:
        canonical_apps = []
        for app in apps:
            pkg = app.get("package", "").lower().strip()
            app_name = app.get("app_name", "")
            canonical_patches = []
            for patch in app.get("patches", []):
                opts = sorted(
                    [{"key": str(o.get("key", "")), "description": str(o.get("description", ""))} for o in patch.get("options", [])],
                    key=lambda x: x["key"]
                )
                comp_vers = sorted(str(v) for v in patch.get("compatible_versions", []))
                canonical_patches.append({
                    "name": str(patch.get("name", "")),
                    "description": str(patch.get("description", "")),
                    "use": bool(patch.get("use", True)),
                    "options": opts,
                    "compatible_versions": comp_vers
                })
            canonical_patches.sort(key=lambda x: x["name"])
            canonical_apps.append({
                "package": pkg,
                "app_name": app_name,
                "patches": canonical_patches
            })
        canonical_apps.sort(key=lambda x: x["package"])
        hash_input = json.dumps({
            "bundle_name": bundle_name,
            "channel": channel,
            "apps": canonical_apps
        }, sort_keys=True)

    sha256 = hashlib.sha256()
    sha256.update(hash_input.encode('utf-8'))
    return sha256.hexdigest()


def generate_bundle_fingerprints():
    """Read parsed_bundles.json, compute fingerprints, write back."""
    parsed_bundles_path = os.path.join(RAW_DIR, "parsed_bundles.json")
    parsed_bundles = load_json(parsed_bundles_path, default={})
    if not parsed_bundles:
        print("No parsed bundles found to fingerprint.")
        return
    print(f"Generating fingerprints for {len(parsed_bundles)} bundles...")
    for key, record in parsed_bundles.items():
        bundle_name = record["bundle"]
        channel = record["channel"]
        apps = record.get("apps", [])
        fingerprint = compute_fingerprint(bundle_name, apps, channel)
        record["fingerprint"] = fingerprint
        print(f" - {key}: {fingerprint[:10]}...")
    save_json(parsed_bundles_path, parsed_bundles)
    print("Fingerprints generated.")


def apps_are_different(old_app, new_app):
    """
    Checks if there are actual differences in the patches, options, or compatible versions
    of the app between the old and new snapshot.
    """
    def normalize_app_data(app):
        normalized_patches = []
        for patch in app.get("patches", []):
            opts = patch.get("options") or []
            normalized_opts = sorted(
                [{"key": str(opt.get("key", "")), "description": str(opt.get("description", ""))} for opt in opts],
                key=lambda x: x["key"]
            )
            comp_vers = sorted(str(v) for v in patch.get("compatible_versions", []))
            normalized_patches.append({
                "name": str(patch.get("name", "")),
                "description": str(patch.get("description", "")),
                "use": bool(patch.get("use", True)),
                "options": normalized_opts,
                "compatible_versions": comp_vers
            })
        normalized_patches.sort(key=lambda x: x["name"])
        return {
            "app_name": str(app.get("app_name", "")),
            "patches": normalized_patches
        }

    return normalize_app_data(old_app) != normalize_app_data(new_app)

def patch_diff_details(old_patch, new_patch):
    """Return a list of human-readable change descriptions between old and new patch."""
    changes = []
    if old_patch.get("description") != new_patch.get("description"):
        changes.append("description updated")
    if old_patch.get("use") != new_patch.get("use"):
        old_use = old_patch.get("use", True)
        new_use = new_patch.get("use", True)
        changes.append("enabled" if new_use and not old_use else "disabled")
    old_opts = {o.get("key", ""): o.get("description", "") for o in old_patch.get("options", [])}
    new_opts = {o.get("key", ""): o.get("description", "") for o in new_patch.get("options", [])}
    if old_opts != new_opts:
        added_opts = [k for k in new_opts if k not in old_opts]
        removed_opts = [k for k in old_opts if k not in new_opts]
        changed_opts = [k for k in old_opts if k in new_opts and old_opts[k] != new_opts[k]]
        parts = []
        if added_opts:
            parts.append(f"+{len(added_opts)} option{'s' if len(added_opts) > 1 else ''}")
        if removed_opts:
            parts.append(f"-{len(removed_opts)} option{'s' if len(removed_opts) > 1 else ''}")
        if changed_opts:
            parts.append(f"~{len(changed_opts)} option{'s' if len(changed_opts) > 1 else ''}")
        if parts:
            changes.append("options: " + ", ".join(parts))
    old_cv = set(str(v) for v in old_patch.get("compatible_versions", []))
    new_cv = set(str(v) for v in new_patch.get("compatible_versions", []))
    if old_cv != new_cv:
        added_vers = new_cv - old_cv
        removed_vers = old_cv - new_cv
        parts = []
        if added_vers:
            parts.append(f"+{len(added_vers)} version{'s' if len(added_vers) > 1 else ''}")
        if removed_vers:
            parts.append(f"-{len(removed_vers)} version{'s' if len(removed_vers) > 1 else ''}")
        if parts:
            changes.append("versions: " + ", ".join(parts))
    return changes

def compute_patch_diff(old_app, new_app):
    """Compute which patches were added/removed between old and new app state."""
    old_patches = {p.get("name", ""): p for p in old_app.get("patches", [])}
    new_patches = {p.get("name", ""): p for p in new_app.get("patches", [])}
    
    old_names = set(old_patches.keys())
    new_names = set(new_patches.keys())
    
    added_names = sorted(new_names - old_names)
    removed_names = sorted(old_names - new_names)
    
    # Also detect modified patches (same name, different content)
    modified_names = sorted([
        n for n in (old_names & new_names)
        if old_patches[n] != new_patches[n]
    ])
    
    def enrich(names, source):
        return [{"name": n, "description": source.get(n, {}).get("description", "")} for n in names]
    
    return {
        "patches_added": enrich(added_names, new_patches),
        "patches_removed": enrich(removed_names, old_patches),
        "patches_modified": [
            {
                "name": n,
                "description": new_patches.get(n, {}).get("description", ""),
                **(
                    {"previous_description": old_patches[n].get("description", "")}
                    if old_patches[n].get("description") != new_patches[n].get("description")
                    else {}
                ),
                "changes": patch_diff_details(old_patches[n], new_patches[n])
            }
            for n in modified_names
        ]
    }

def diff_snapshots():
    new_snapshot_path = os.path.join(RAW_DIR, "parsed_bundles.json")
    new_snapshot = load_json(new_snapshot_path, default={})
    old_snapshot = load_current_snapshot()

    # Rebuild from committed bundle files if snapshot is empty (e.g. CI fresh checkout)
    if not old_snapshot:
        print("[*] No current snapshot found — rebuilding from data/bundles/*.json")
        old_snapshot = rebuild_snapshot_from_bundles()
        # Persist the rebuilt snapshot so next local run is fast
        from state_manager import save_new_snapshot
        if old_snapshot:
            save_new_snapshot(old_snapshot)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    baseline_suppression = False
    try:
        from discovery.state import baseline_pending
        baseline_suppression = baseline_pending()
    except Exception:
        pass

    old_repo_keys = _repo_key_map(old_snapshot)
    new_repo_keys = _repo_key_map(new_snapshot)

    affected_bundles = []

    # Process each bundle:channel in the new snapshot
    for bundle_key, new_rec in new_snapshot.items():
        bundle_name = new_rec.get("bundle") or bundle_key.split(":")[0]
        channel = new_rec.get("channel") or (bundle_key.split(":")[1] if ":" in bundle_key else "stable")
        new_apps_list = new_rec.get("apps", [])

        if bundle_key not in old_snapshot:
            # Identity migration? Same repo already known under a different
            # key that vanished this run — not a genuinely new bundle.
            slug = repo_slug(new_rec.get("repo_url", ""))
            migrated = slug and any(
                old_key != bundle_key
                and old_key.split(":")[0] != bundle_name
                and old_key not in new_snapshot
                for old_key in old_repo_keys.get(slug, [])
            )
            if migrated:
                print(f"[*] Diff: {bundle_key} is an identity migration, not a new bundle")
                continue
            if baseline_suppression:
                print(f"[*] Diff: {bundle_key} discovered during baseline - suppressing new-bundle event")
                continue
            # Scenario B: New Bundle — include all its apps
            print(f"[+] Diff: Found new bundle {bundle_key}")
            apps = []
            for app in new_apps_list:
                apps.append({
                    "app_name": app["app_name"],
                    "package": app["package"],
                    "badge_type": "NEW APP"
                })
            affected_bundles.append({
                "bundle": bundle_name,
                "channel": channel,
                "badge_type": "NEW BUNDLE",
                "apps": apps,
                "repo_url": new_rec.get("repo_url", ""),
                "patches_name": new_rec.get("patches_name", ""),
                "previous_version": "",
                "new_version": new_rec.get("version", ""),
                "avatarUrl": new_rec.get("avatarUrl", ""),
                "bundleImageUrl": new_rec.get("bundleImageUrl", ""),
            })
        else:
            # Scenario C: Existing bundle — check fingerprint
            old_rec = old_snapshot[bundle_key]
            new_fp = new_rec.get("fingerprint")
            old_fp = old_rec.get("fingerprint")

            if new_fp != old_fp:
                old_app_map = {app["package"].lower().strip(): app for app in old_rec.get("apps", [])}
                new_app_map = {app["package"].lower().strip(): app for app in new_apps_list}

                changed_apps = []

                for pkg, app in new_app_map.items():
                    if pkg not in old_app_map:
                        # Check if app was promoted from dev (pre-release) to stable
                        promoted = False
                        if channel == "stable":
                            dev_key = f"{bundle_name}:dev"
                            if dev_key in old_snapshot:
                                old_dev_apps = old_snapshot[dev_key].get("apps", [])
                                if any(a["package"].lower().strip() == pkg for a in old_dev_apps):
                                    promoted = True
                                    print(f"[->] Diff: App {app['app_name']} ({pkg}) promoted from dev to stable in {bundle_key}")
                        if promoted:
                            changed_apps.append({
                                "app_name": app["app_name"],
                                "package": app["package"],
                                "badge_type": "UPDATED APP",
                                "promoted_from": "dev"
                            })
                        else:
                            print(f"[+] Diff: Found new app {app['app_name']} ({pkg}) in {bundle_key}")
                            changed_apps.append({
                                "app_name": app["app_name"],
                                "package": app["package"],
                                "badge_type": "NEW APP"
                            })
                    elif apps_are_different(old_app_map[pkg], app):
                        print(f"[~] Diff: Found updated app {app['app_name']} ({pkg}) in {bundle_key}")
                        patch_diff = compute_patch_diff(old_app_map[pkg], app)
                        app_badge = "UPDATED APP"
                        total_changes = (
                            len(patch_diff.get("patches_added", []))
                            + len(patch_diff.get("patches_removed", []))
                            + len(patch_diff.get("patches_modified", []))
                        )
                        if total_changes >= 5:
                            app_badge = "MAJOR UPDATE"
                        changed_apps.append({
                            "app_name": app["app_name"],
                            "package": app["package"],
                            "badge_type": app_badge,
                            "patch_diff": patch_diff
                        })

                for pkg, app in old_app_map.items():
                    if pkg not in new_app_map:
                        print(f"[-] Diff: Found removed app {app['app_name']} ({pkg}) in {bundle_key}")
                        changed_apps.append({
                            "app_name": app["app_name"],
                            "package": app["package"],
                            "badge_type": "REMOVED APP"
                        })

                # Only emit the bundle if at least one app-level change exists
                if changed_apps:
                    old_fp_display = old_fp[:8] if old_fp else "None"
                    new_fp_display = new_fp[:8] if new_fp else "None"
                    print(f"[+] Diff: Bundle {bundle_key} fingerprint changed ({old_fp_display} -> {new_fp_display})")
                    # Detect version bump
                    old_ver = old_rec.get("version", "")
                    new_ver = new_rec.get("version", "")
                    extra_badges = []
                    if old_ver and new_ver and old_ver != new_ver:
                        extra_badges.append("VERSION BUMP")
                        print(f"[^] Diff: Version bump in {bundle_key} ({old_ver} -> {new_ver})")
                    affected_bundles.append({
                        "bundle": bundle_name,
                        "channel": channel,
                        "badge_type": "UPDATED",
                        "apps": changed_apps,
                        "repo_url": new_rec.get("repo_url", ""),
                        "patches_name": new_rec.get("patches_name", ""),
                        "extra_badges": extra_badges,
                        "previous_version": old_ver,
                        "new_version": new_ver,
                        "avatarUrl": new_rec.get("avatarUrl", ""),
                        "bundleImageUrl": new_rec.get("bundleImageUrl", ""),
                    })
                else:
                    print(f"[*] Diff: Bundle {bundle_key} fingerprint changed but no app-level changes. Skipping.")

    has_changes = len(affected_bundles) > 0

    # Detect removed bundles (in old snapshot but not in new)
    claims = load_claimed_keys()
    for bundle_key in old_snapshot:
        if bundle_key not in new_snapshot:
            old_rec = old_snapshot[bundle_key]
            bundle_name = old_rec.get("bundle") or bundle_key.split(":")[0]
            channel = old_rec.get("channel") or (bundle_key.split(":")[1] if ":" in bundle_key else "stable")
            old_apps_list = old_rec.get("apps", [])

            if is_claimed(bundle_key, old_rec, claims):
                print(f"[*] Diff: {bundle_key} still claimed upstream — suppressing removal (fetch/parse failure?)")
                continue

            slug = repo_slug(old_rec.get("repo_url", ""))
            migrated = slug and any(
                new_key != bundle_key and new_key.split(":")[0] != bundle_name
                for new_key in new_repo_keys.get(slug, [])
            )
            if migrated:
                print(f"[*] Diff: {bundle_key} gone but its repo lives under a new key — identity migration")
                continue

            print(f"[-] Diff: Found removed bundle {bundle_key}")
            removed_apps = []
            for app in old_apps_list:
                removed_apps.append({
                    "app_name": app.get("app_name", ""),
                    "package": app.get("package", ""),
                    "badge_type": "REMOVED APP"
                })
            affected_bundles.append({
                "bundle": bundle_name,
                "channel": channel,
                "badge_type": "REMOVED BUNDLE",
                "apps": removed_apps,
                "repo_url": old_rec.get("repo_url", ""),
                "patches_name": old_rec.get("patches_name", ""),
                "avatarUrl": old_rec.get("avatarUrl", ""),
                "bundleImageUrl": old_rec.get("bundleImageUrl", ""),
            })

    has_changes = len(affected_bundles) > 0

    # Update last_run.json with metadata
    last_run_data = load_last_run()
    last_run_data["has_changes"] = has_changes
    last_run_data["affected_bundles_count"] = len(affected_bundles)
    last_run_data["lastChecked"] = now_utc
    save_last_run(last_run_data)

    # Save diff result
    diff_data = {
        "affected_bundles": affected_bundles
    }
    save_json(os.path.join(RAW_DIR, "diff_result.json"), diff_data)

    if not has_changes:
        print("[*] No changes detected. Short-circuiting execution.")
        return False

    print(f"Diff complete. Found {len(affected_bundles)} affected bundles.")
    return True

if __name__ == "__main__":
    ensure_dirs()
    diff_snapshots()
