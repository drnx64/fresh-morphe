import os
import re
import json
from datetime import datetime
from state_manager import (
    load_json, save_json, ensure_dirs, RAW_DIR, STATE_DIR, load_last_run,
    save_last_run, COMMON_PACKAGES, load_current_snapshot,
    rebuild_snapshot_from_bundles, load_claimed_keys, is_claimed,
)
from app_metadata import derive_name_from_package


def get_app_name(package_name):
    """Normalize package names to a readable display name."""
    pkg = (package_name or "").lower().strip()
    if pkg in COMMON_PACKAGES:
        return COMMON_PACKAGES[pkg]
    return derive_name_from_package(pkg)

def _gitlab_project_path(url):
    """Extract the full project path from a GitLab URL, handling subgroups."""
    m = re.search(r"gitlab\.com/(.+)", url)
    if not m:
        return None
    raw = m.group(1).split("?")[0]  # strip query params
    raw = raw.rstrip("/")
    # Remove trailing /-/... or /releases/... segments (non-project URL parts)
    raw = re.sub(r"/-/(?:releases|issues|merge_requests|blob|tree|raw).*$", "", raw)
    raw = re.sub(r"/releases/.*$", "", raw)
    # Normalise API paths: /api/v4/projects/OWNER%2FREPO/...
    api_m = re.match(r"api/v4/projects/([^/]+(?:%2F[^/]+)*)", raw)
    if api_m:
        return api_m.group(1).replace("%2F", "/")
    return raw


def _extract_repo_url_from_match(m):
    """Reconstruct a repo URL from a (github|gitlab) regex match that captures 2 segments."""
    platform = m.group(1)
    if platform == "github":
        return f"https://github.com/{m.group(2)}/{m.group(3)}"
    # GitLab: use full project path from the original URL
    path = _gitlab_project_path(m.group(0))
    if path:
        return f"https://gitlab.com/{path}"
    return None


_GIT_PATTERN = re.compile(r"https://(github|gitlab)\.com/([^/]+)/([^/]+)")


def _scan_url_for_repo(url, bundle_name):
    if not isinstance(url, str) or not url:
        return None
    for m in _GIT_PATTERN.finditer(url):
        result = _extract_repo_url_from_match(m)
        if result:
            return result
    return None


def extract_repo_url(bundle_json, bundle_name):
    """
    Extract github/gitlab repository URL from patches-bundle.json.
    Prefers an explicit source_url from the registry, then checks download_url,
    then falls back to patches.url, integrations.url, description, etc.
    """
    source_url = bundle_json.get("source_url")
    if isinstance(source_url, str) and source_url:
        result = _scan_url_for_repo(source_url, bundle_name)
        if result:
            return result

    download_url = bundle_json.get("download_url")
    if isinstance(download_url, str) and download_url:
        result = _scan_url_for_repo(download_url, bundle_name)
        if result:
            return result

    # 1. Look for patches.url
    patches = bundle_json.get("patches", {})
    if isinstance(patches, dict) and "url" in patches:
        result = _scan_url_for_repo(patches["url"], bundle_name)
        if result:
            return result

    # 2. Look for integrations.url
    integrations = bundle_json.get("integrations", {})
    if isinstance(integrations, dict) and "url" in integrations:
        result = _scan_url_for_repo(integrations["url"], bundle_name)
        if result:
            return result

    # 3. Look in description
    description = bundle_json.get("description", "")
    if description:
        result = _scan_url_for_repo(description, bundle_name)
        if result:
            return result

    # 4. Fallback default
    return f"https://github.com/{bundle_name}/revanced-patches"


def _extract_versions(raw):
    """
    Normalize a list of version entries into plain version strings.
    Handles both string items and object items with a 'version' key.
    """
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if isinstance(item, str) and item:
            result.append(item)
        elif isinstance(item, dict):
            v = item.get("version")
            if v:
                result.append(str(v))
    return result


def validate_and_parse_bundle(bundle_name, channel):
    """
    Validates files for bundle_name:channel and parses metadata.
    Returns parsed dictionary or raises ValueError.
    """
    bundle_dir = os.path.join(RAW_DIR, "bundles", bundle_name, channel)
    bundle_file = os.path.join(bundle_dir, "patches-bundle.json")
    list_file = os.path.join(bundle_dir, "patches-list.json")
    
    if not os.path.exists(bundle_file) or not os.path.exists(list_file):
        raise ValueError("Missing patches-bundle.json or patches-list.json")
        
    try:
        with open(bundle_file, "r", encoding="utf-8") as f:
            bundle_json = json.load(f)
    except Exception as e:
        raise ValueError(f"Malformed patches-bundle.json: {e}")
        
    download_url = bundle_json.get("download_url")
    if not isinstance(download_url, str) or not download_url.lower().endswith(".mpp"):
        raise ValueError("Not a Morphe bundle: download_url must end with .mpp")
        
    try:
        with open(list_file, "r", encoding="utf-8") as f:
            list_json = json.load(f)
    except Exception as e:
        raise ValueError(f"Malformed patches-list.json: {e}")
        
    # Verify we have basic structure
    if not isinstance(list_json, dict) or "patches" not in list_json:
        raise ValueError("patches-list.json is missing required 'patches' field or is not a JSON object")
        
    patches = list_json["patches"]
    if not isinstance(patches, list):
        raise ValueError("'patches' field in patches-list.json is not a list")
        
    # Extract unique packages and their friendly names
    unique_packages = {}
    for patch in patches:
        if not isinstance(patch, dict) or "compatiblePackages" not in patch:
            continue
        comp_pkgs = patch["compatiblePackages"]
        
        # compatiblePackages can be a dict (pkg -> list of versions) or a list of packages
        if isinstance(comp_pkgs, dict):
            for pkg in comp_pkgs.keys():
                if pkg and isinstance(pkg, str):
                    pkg_clean = pkg.lower().strip()
                    if pkg_clean not in unique_packages:
                        unique_packages[pkg_clean] = None
        elif isinstance(comp_pkgs, list):
            for pkg in comp_pkgs:
                if isinstance(pkg, str):
                    pkg_clean = pkg.lower().strip()
                    if pkg_clean not in unique_packages:
                        unique_packages[pkg_clean] = None
                elif isinstance(pkg, dict):
                    pkg_name = pkg.get("packageName") or pkg.get("package")
                    if pkg_name and isinstance(pkg_name, str):
                        pkg_clean = pkg_name.lower().strip()
                        friendly_name = pkg.get("name")
                        if friendly_name and isinstance(friendly_name, str):
                            # Prioritize friendly name from the JSON
                            unique_packages[pkg_clean] = friendly_name.strip()
                        elif pkg_clean not in unique_packages:
                            unique_packages[pkg_clean] = None
                            
    if not unique_packages:
        raise ValueError("No compatible packages found in patches list")
        
    # Normalize repo url and created_at
    repo_url = extract_repo_url(bundle_json, bundle_name)
    
    created_at = bundle_json.get("created_at")
    if not created_at:
        # Fallback to file creation time or current time
        created_at = datetime.now().isoformat()

    # Build a per-package patch list (name, description, versions, options, use)
    patches_by_package = {}
    for patch in patches:
        if not isinstance(patch, dict):
            continue

        patch_name = patch.get("name") or ""
        patch_desc = patch.get("description") or ""
        patch_use = patch.get("use", patch.get("default", True))
        raw_options = patch.get("options") or []
        patch_options = []
        if isinstance(raw_options, list):
            for opt in raw_options:
                if isinstance(opt, dict):
                    patch_options.append({
                        "key": opt.get("key") or opt.get("name") or "",
                        "description": opt.get("description") or "",
                    })

        comp_pkgs = patch.get("compatiblePackages")
        target_packages = []

        if isinstance(comp_pkgs, dict):
            for pkg, ver_list in comp_pkgs.items():
                if pkg and isinstance(pkg, str):
                    versions = _extract_versions(ver_list)
                    target_packages.append({"pkg": pkg.lower().strip(), "versions": versions})
        elif isinstance(comp_pkgs, list):
            for item in comp_pkgs:
                if isinstance(item, str):
                    target_packages.append({"pkg": item.lower().strip(), "versions": []})
                elif isinstance(item, dict):
                    pkg_name = item.get("packageName") or item.get("package") or ""
                    if pkg_name:
                        raw_targets = item.get("targets") or item.get("versions") or []
                        versions = _extract_versions(raw_targets)
                        target_packages.append({"pkg": pkg_name.lower().strip(), "versions": versions})
        else:
            # Universal patch (no compatible packages specified)
            target_packages.append({"pkg": "", "versions": []})

        if not target_packages:
            target_packages.append({"pkg": "", "versions": []})

        patch_record = {
            "name": patch_name,
            "description": patch_desc,
            "use": patch_use,
            "options": patch_options,
        }

        for target in target_packages:
            pkg_key = target["pkg"]
            entry = dict(patch_record)
            entry["compatible_versions"] = target["versions"]
            patches_by_package.setdefault(pkg_key, []).append(entry)

    # Build compatibility app list with their patches
    apps = []
    for pkg in sorted(unique_packages.keys()):
        friendly_name = unique_packages[pkg]
        if not friendly_name:
            friendly_name = get_app_name(pkg)
        apps.append({
            "app_name": friendly_name,
            "package": pkg,
            "patches": patches_by_package.get(pkg, []),
        })
        
    return {
        "bundle": bundle_name,
        "channel": channel,
        "repo_url": repo_url,
        "created_at": created_at,
        "version": bundle_json.get("version", ""),
        "description": bundle_json.get("description", ""),
        "apps": apps
    }



def reconcile_parsed_bundles(parsed_bundles, old_snapshot, claims):
    """Reconcile this run's parse output with the previous snapshot.

    - Drops records no longer claimed upstream (stale raw dirs left behind
      by removed/renamed bundles).
    - Carries forward previously-parsed records whose keys are still claimed
      upstream but missing from this run's parse — transient download/parse
      failures must never look like removals.

    Returns (parsed_bundles, dropped_keys, carried_keys).
    """
    stale = []
    if claims is not None:
        stale = [k for k, v in parsed_bundles.items() if not is_claimed(k, v, claims)]
        for key in stale:
            del parsed_bundles[key]

    carried = []
    for key, record in old_snapshot.items():
        if key in parsed_bundles:
            continue
        if is_claimed(key, record, claims):
            parsed_bundles[key] = record
            carried.append(key)

    if stale:
        print(f"[*] Reconcile: dropped {len(stale)} unclaimed stale bundles")
    if carried:
        print(f"[*] Reconcile: carried forward {len(carried)} claimed bundles missing from parse")
    return parsed_bundles, stale, carried


def parse_all_bundles():
    bundles_raw_dir = os.path.join(RAW_DIR, "bundles")
    if not os.path.exists(bundles_raw_dir):
        print("No raw bundles directory found. Run download_bundles.py first.")
        return
        
    parsed_bundles = {}
    errors = []
    
    # Load last run data to append parsing errors (merged from download step)
    last_run_data = load_last_run()
    if "parse_errors" not in last_run_data:
        last_run_data["parse_errors"] = []
        
    for bundle_name in os.listdir(bundles_raw_dir):
        bundle_path = os.path.join(bundles_raw_dir, bundle_name)
        if not os.path.isdir(bundle_path):
            continue
            
        for channel in os.listdir(bundle_path):
            channel_path = os.path.join(bundle_path, channel)
            if not os.path.isdir(channel_path):
                continue
                
            bundle_key = f"{bundle_name}:{channel}"
            try:
                print(f"[+] Parsing {bundle_key}...")
                record = validate_and_parse_bundle(bundle_name, channel)
                parsed_bundles[bundle_key] = record
            except Exception as e:
                err_msg = str(e)
                print(f"[-] Skip {bundle_key} - Validation failed: {err_msg}")
                errors.append({
                    "bundle": bundle_key,
                    "error": err_msg
                })

    # Reconcile with the previous snapshot: drop stale unclaimed dirs,
    # carry forward claimed keys missing from this run (transient failures).
    old_snapshot = load_current_snapshot()
    if not old_snapshot:
        old_snapshot = rebuild_snapshot_from_bundles()
    claims = load_claimed_keys()
    parsed_bundles, dropped_keys, carried_keys = reconcile_parsed_bundles(
        parsed_bundles, old_snapshot, claims
    )

    # Enrich with Google Play Store app icons
    print("\n--- Enriching apps with Play Store icons ---")
    try:
        from icon_fetcher import enrich_parsed_bundles_with_icons
        enrich_parsed_bundles_with_icons(parsed_bundles)
    except Exception as e:
        print(f"[-] Icon enrichment failed (non-fatal): {e}")

    # Enrich with Google Play Store app names
    print("\n--- Enriching apps with Play Store names ---")
    try:
        from icon_fetcher import enrich_parsed_bundles_with_names
        enrich_parsed_bundles_with_names(parsed_bundles)
    except Exception as e:
        print(f"[-] Name enrichment failed (non-fatal): {e}")

    # Fall back to F-Droid for packages Play Store could not resolve
    print("\n--- Looking up unresolved apps on F-Droid ---")
    try:
        from icon_fetcher import enrich_parsed_bundles_with_store_fallbacks
        enrich_parsed_bundles_with_store_fallbacks(parsed_bundles)
    except Exception as e:
        print(f"[-] F-Droid enrichment failed (non-fatal): {e}")

    # Save parsed bundles
    save_json(os.path.join(RAW_DIR, "parsed_bundles.json"), parsed_bundles)
    
    # Merge parse results into last_run.json
    last_run_data["parse_errors"] = errors
    save_last_run(last_run_data)
    
    print(f"Parsed {len(parsed_bundles)} valid bundles. Encountered {len(errors)} errors.")

if __name__ == "__main__":
    ensure_dirs()
    parse_all_bundles()
