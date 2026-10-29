import os
import shutil
import json
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from state_manager import load_json, save_json, ensure_dirs, RAW_DIR, STATE_DIR, CUSTOM_REPO_PATH, IGNORE_REPO_PATH, load_repo_list, load_last_run, save_last_run
from config import SKIP_CACHE_TTL_DAYS
from http_utils import fetch_url
from registry_api import fetch_catalog_patches
from file_sha_store import load_file_shas, save_file_shas, get_changed_files, update_file_shas, prune_stale_shas

def group_tree_files(tree_files):
    """
    Groups the flat tree file list into {bundle_name: {channel: { 'bundle_path': ..., 'list_path': ... }}}
    Supports both conceptual subfolders (stable/patches-bundle.json) and actual flat naming (1fexd-stable-patches-bundle.json).
    """
    bundles = {}
    
    for item in tree_files:
        path = item.get("path", "")
        parts = path.split('/')
        if len(parts) < 3 or parts[0] != "patch-bundles":
            continue
            
        bundle_folder = parts[1]
        
        # Determine channel and file type
        channel = None
        file_type = None
        
        if len(parts) == 4:
            # Case A: patch-bundles/my-bundle/stable/patches-bundle.json
            ch = parts[2]
            filename = parts[3]
            if ch in ["stable", "dev", "latest"]:
                channel = ch
                if filename == "patches-bundle.json":
                    file_type = "bundle"
                elif filename == "patches-list.json":
                    file_type = "list"
        elif len(parts) == 3:
            # Case B: patch-bundles/1fexd-patch-bundles/1fexd-stable-patches-bundle.json
            filename = parts[2]
            if "-stable-patches-bundle.json" in filename:
                channel = "stable"
                file_type = "bundle"
            elif "-stable-patches-list.json" in filename:
                channel = "stable"
                file_type = "list"
            elif "-dev-patches-bundle.json" in filename:
                channel = "dev"
                file_type = "bundle"
            elif "-dev-patches-list.json" in filename:
                channel = "dev"
                file_type = "list"
            elif "-latest-patches-bundle.json" in filename:
                channel = "latest"
                file_type = "bundle"
            elif "-latest-patches-list.json" in filename:
                channel = "latest"
                file_type = "list"
                
        if channel and file_type:
            # Clean bundle folder name if it has -patch-bundles suffix (optional but keeps things neat)
            bundle_name = bundle_folder
            if bundle_name.endswith("-patch-bundles"):
                bundle_name = bundle_name[:-14]
            elif bundle_name.endswith("-patches"):
                bundle_name = bundle_name[:-8]
                
            if bundle_name not in bundles:
                bundles[bundle_name] = {}
            channel_entry = bundles[bundle_name].setdefault(channel, {})
            channel_entry[f"{file_type}_path"] = path
            if item.get("registry"):
                channel_entry["registry"] = item["registry"]
            if item.get("legacy_keys"):
                channel_entry["legacy_keys"] = item["legacy_keys"]

    return bundles

def download_file_with_retry(url, max_retries=3, timeout=20):
    for attempt in range(1, max_retries + 1):
        try:
            return fetch_url(url, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            print(f"Error fetching {url}: HTTP {e.code}")
        except Exception as e:
            print(f"Error fetching {url}: {e}")

        if attempt < max_retries:
            time.sleep(2 ** attempt)

    return None


_RELEASE_ASSET_CACHE = {}


def _release_repo_and_tag(download_url):
    """Return ``(asset_base_url, tag, 'owner/repo')`` for a release URL.

    Handles GitHub ``/releases/download/<tag>/`` and GitLab
    ``/-/releases/<tag>/downloads/`` URLs, plus generic package URLs.
    """
    parsed = urllib.parse.urlsplit(download_url)
    path = parsed.path

    marker = "/releases/download/"
    if marker in path:
        prefix, rest = path.split(marker, 1)
        tag = rest.split("/", 1)[0]
        base_path = prefix + marker + tag + "/"
        repo_path = prefix.strip("/")
        repo = repo_path if repo_path.count("/") == 1 else None
        base_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, base_path, "", "")
        )
        return base_url, tag, repo

    gitlab_marker = "/-/releases/"
    if gitlab_marker in path:
        prefix, rest = path.split(gitlab_marker, 1)
        tag = rest.split("/", 1)[0]
        base_path = path.rsplit("/", 1)[0] + "/"
        base_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, base_path, "", "")
        )
        repo_path = prefix.strip("/")
        repo = repo_path if repo_path.count("/") == 1 else None
        return base_url, tag, repo

    # GitLab generic packages and other direct asset hosts: assume siblings
    # live next to the bundle artifact.
    if "/" in path:
        base_path = path.rsplit("/", 1)[0] + "/"
        base_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, base_path, "", "")
        )
        return base_url, None, None

    return None, None, None


def _github_release_assets(download_url):
    """Map release asset names to download URLs (GitHub only, cached)."""
    base_url, tag, repo = _release_repo_and_tag(download_url)
    if not base_url or not tag or not repo or "github.com" not in base_url:
        return {}

    cache_key = f"{repo}@{tag}"
    if cache_key in _RELEASE_ASSET_CACHE:
        return _RELEASE_ASSET_CACHE[cache_key]

    api_url = f"https://api.github.com/repos/{repo}/releases/tags/{urllib.parse.quote(tag)}"
    assets = {}
    try:
        release = fetch_url(api_url, timeout=20, as_json=True)
        for asset in release.get("assets") or []:
            name = asset.get("name", "")
            url = asset.get("browser_download_url", "")
            if name and url:
                assets[name] = url
    except Exception as e:
        print(f"  Release asset lookup failed for {repo}@{tag}: {e}")

    _RELEASE_ASSET_CACHE[cache_key] = assets
    return assets


def _synthesize_bundle_json(registry):
    version = registry.get("version", "")
    if version.startswith("v") and len(version) > 1 and version[1].isdigit():
        version = version[1:]
    source_url = registry.get("source_url", "")
    data = {
        "created_at": registry.get("created_at", ""),
        "description": registry.get("description", ""),
        "download_url": registry.get("download_url", ""),
        "signature_download_url": registry.get("signature_download_url", ""),
        "version": version,
    }
    if source_url:
        # Normally parsed from download_url; the explicit hint also covers
        # GitLab package URLs that do not contain the project path.
        data["source_url"] = source_url
        data["patches"] = {"url": source_url}
    return json.dumps(data, indent=2, ensure_ascii=False)


def _synthesize_patch_list(registry):
    bundle_id = registry.get("bundle_id", "")
    if not bundle_id:
        return None
    rows = fetch_catalog_patches(bundle_id)
    if not rows:
        return None

    patches = []
    for row in rows:
        compatible = []
        for package in row.get("compatiblePackages") or row.get("packages") or []:
            name = package.get("name") or package.get("packageName")
            if not name:
                continue
            versions = package.get("versions")
            compatible.append({
                "packageName": name,
                "versions": versions if isinstance(versions, list) else [],
            })
        patches.append({
            "name": row.get("name") or "",
            "description": row.get("description") or "",
            "use": row.get("use", True),
            "options": row.get("options") or [],
            "compatiblePackages": compatible,
        })

    return json.dumps({
        "NOTE": "Synthesized from the Patch Bundle Registry catalog.",
        "version": registry.get("version", ""),
        "patches": patches,
    }, indent=2, ensure_ascii=False)


def _download_registry_asset(registry, filename):
    """Download a metadata file from a registry bundle's release assets.

    Falls back to synthesizing JSON from registry metadata when a release does
    not publish ``patches-bundle.json`` / ``patches-list.json`` (notably
    GitLab package releases).
    """
    download_url = registry.get("download_url", "")
    if not download_url:
        return None

    candidates = []
    base_url, tag, repo = _release_repo_and_tag(download_url)
    if base_url:
        candidates.append(base_url + filename)
    if repo and "github.com" in (base_url or ""):
        candidates.append(f"https://raw.githubusercontent.com/{repo}/HEAD/{filename}")

    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        content = download_file_with_retry(url)
        if content:
            return content

    asset_url = _github_release_assets(download_url).get(filename)
    if asset_url:
        content = download_file_with_retry(asset_url)
        if content:
            return content

    if filename == "patches-bundle.json":
        return _synthesize_bundle_json(registry)
    if filename == "patches-list.json":
        return _synthesize_patch_list(registry)

    return None

def is_morphe_bundle(bundle_json):
    download_url = bundle_json.get("download_url")

    if not isinstance(download_url, str):
        return False

    if not download_url.lower().endswith(".mpp"):
        return False

    path_parts = download_url.split("/")
    if len(path_parts) < 8:
        return False

    return True

def _load_skip_bundle_names():
    """Load custom and ignore repos and return a set of bundle names to skip.

    Each custom/ignore repo's owner name is used as a potential bundle name
    prefix to match against Jman's bundle names.
    """
    skip = set()
    for filepath in (CUSTOM_REPO_PATH, IGNORE_REPO_PATH):
        for owner, repo, _ in load_repo_list(filepath):
            skip.add(owner.lower().replace("_", "-"))
            skip.add(f"{owner.lower()}-{repo.lower().replace('_', '-')}")
    return skip


def _load_skip_cache():
    """Load skip cache from last_run.json's download_errors.

    Returns a dict mapping bundle_key -> {error, last_attempted}.
    Entries without last_attempted (legacy) are treated as stale (always retry).
    """
    last_run = load_last_run()
    errors = last_run.get("download_errors", [])
    cache = {}
    for entry in errors:
        key = entry.get("bundle", "")
        if key:
            cache[key] = {
                "error": entry.get("error", ""),
                "last_attempted": entry.get("last_attempted"),
            }
    return cache


def _is_cache_fresh(entry):
    """Check if a skip cache entry is still within the TTL window."""
    ts = entry.get("last_attempted")
    if not ts:
        return False
    try:
        last_attempted = datetime.fromisoformat(ts)
        if last_attempted.tzinfo is None:
            last_attempted = last_attempted.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last_attempted) < timedelta(days=SKIP_CACHE_TTL_DAYS)
    except (ValueError, TypeError):
        return False


def download_all_bundles():
    tree_json_path = os.path.join(RAW_DIR, "tree.json")
    tree_files = load_json(tree_json_path, default=[])
    
    if not tree_files:
        print("No files found in tree.json. Run fetch_patch_tree.py first.")
        return
        
    bundles = group_tree_files(tree_files)
    print(f"Discovered {len(bundles)} distinct bundles in tree.")

    # SHA-based skip: only download bundles whose files changed since last run
    known_shas = load_file_shas()
    changed_tree = get_changed_files(tree_files, known_shas)
    changed_paths = {item["path"] for item in changed_tree}

    # Filter bundles to only those with at least one changed file,
    # or whose files are missing locally (fresh CI checkout has no raw files —
    # SHA-skip alone would leave the snapshot incomplete and every unchanged
    # bundle would look "removed").
    if known_shas:
        before = len(bundles)
        filtered = {}
        for b_name, channels in bundles.items():
            for ch, paths in channels.items():
                bp = paths.get("bundle_path", "")
                lp = paths.get("list_path", "")
                need_force = False
                if bp and lp:
                    local_dir = os.path.join(RAW_DIR, "bundles", b_name, ch)
                    need_force = not (
                        os.path.isfile(os.path.join(local_dir, "patches-bundle.json"))
                        and os.path.isfile(os.path.join(local_dir, "patches-list.json"))
                    )
                if bp in changed_paths or lp in changed_paths or need_force:
                    filtered.setdefault(b_name, {})[ch] = paths
        bundles = filtered
        skipped = before - len(bundles)
        if skipped:
            print(f"  SHA filter: {skipped} bundles unchanged and present locally (skipping)")
    else:
        print("  First run: no stored SHAs, downloading all bundles")

    # Load skip list from custom/ignore repos to avoid downloading duplicates
    skip_bundles = _load_skip_bundle_names()
    if skip_bundles:
        print(f"Skipping {len(skip_bundles)} bundle names from custom/ignore repos")
        before = len(bundles)
        bundles = {k: v for k, v in bundles.items() if k.lower() not in skip_bundles}
        print(f"  Filtered from {before} to {len(bundles)} bundles (skipped {before - len(bundles)})")

    # Load skip cache — bundles previously skipped as incomplete or non-morphe
    skip_cache = _load_skip_cache()
    cached_skips = sum(1 for k in skip_cache if _is_cache_fresh(skip_cache[k]))
    if cached_skips:
        print(f"Skip cache: {cached_skips} bundles within TTL ({SKIP_CACHE_TTL_DAYS}d), will skip silently")
    
    # Download to a temp directory, then swap atomically.
    bundles_raw_dir = os.path.join(RAW_DIR, "bundles")
    temp_dir = bundles_raw_dir + "_downloading"
    
    # Clean up any leftover temp dir from a previous failed run
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
        
    # Build work items: each item carries the synthetic tree paths plus the
    # registry record needed to resolve release assets.
    work_items = []
    errors = []
    
    for bundle_name, channels in bundles.items():
        for channel, paths in channels.items():
            bundle_path = paths.get("bundle_path")
            list_path = paths.get("list_path")
            bundle_key = f"{bundle_name}:{channel}"
            
            if not bundle_path or not list_path:
                cached = skip_cache.get(bundle_key)
                if cached and _is_cache_fresh(cached):
                    continue
                err_msg = f"Incomplete bundle+channel pair. Missing bundle_path or list_path."
                print(f"[-] Skip {bundle_key} - {err_msg}")
                errors.append({
                    "bundle": bundle_key,
                    "error": err_msg,
                    "last_attempted": datetime.now(timezone.utc).isoformat(),
                })
                continue
                
            # Check skip cache for this bundle
            cached = skip_cache.get(bundle_key)
            if cached and _is_cache_fresh(cached):
                continue
                
            work_items.append({
                "bundle_key": bundle_key,
                "bundle_name": bundle_name,
                "channel": channel,
                "bundle_path": bundle_path,
                "list_path": list_path,
                "registry": paths.get("registry") or {},
            })

    print(f"Downloading {len(work_items)} bundle+channel pairs with 3 workers...")

    def _download_one(item):
        """Download a single bundle+channel pair. Returns (bundle_key, result_dict)."""
        bundle_key = item["bundle_key"]
        bundle_name = item["bundle_name"]
        channel = item["channel"]
        registry = item.get("registry") or {}

        if registry:
            bundle_content = _download_registry_asset(registry, "patches-bundle.json")
            if not bundle_content:
                return (bundle_key, {"error": "Failed to download patches-bundle.json"})
            list_content = _download_registry_asset(registry, "patches-list.json")
            if not list_content:
                return (bundle_key, {"error": "Failed to download patches-list.json"})
        else:
            # Backward compatibility for an old tree.json left on disk.
            prefix = "https://raw.githubusercontent.com/Jman-Github/ReVanced-Patch-Bundles/bundles/"
            bundle_content = download_file_with_retry(prefix + item["bundle_path"])
            if not bundle_content:
                return (bundle_key, {"error": "Failed to download patches-bundle.json"})
            list_content = download_file_with_retry(prefix + item["list_path"])
            if not list_content:
                return (bundle_key, {"error": "Failed to download patches-list.json"})
            
        # Parse and validate as Morphe bundle
        try:
            bundle_json = json.loads(bundle_content)
        except Exception as e:
            return (bundle_key, {"error": f"Failed to parse patches-bundle.json as JSON: {e}"})

        if registry:
            # Registry metadata is authoritative for identity; some upstream
            # patches-bundle.json files are stale or omit fields entirely.
            if registry.get("download_url"):
                bundle_json["download_url"] = registry["download_url"]
            if registry.get("source_url"):
                bundle_json["source_url"] = registry["source_url"]
            if not bundle_json.get("version"):
                bundle_json["version"] = registry.get("version", "")
            if not bundle_json.get("created_at"):
                bundle_json["created_at"] = registry.get("created_at", "")
            if not bundle_json.get("description"):
                bundle_json["description"] = registry.get("description", "")
            bundle_content = json.dumps(bundle_json, indent=2, ensure_ascii=False)

        if not is_morphe_bundle(bundle_json):
            return (bundle_key, {"error": "Not a Morphe bundle"})
            
        return (bundle_key, {
            "bundle_content": bundle_content,
            "list_content": list_content,
            "bundle_name": bundle_name,
            "channel": channel,
        })

    downloaded_count = 0
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_download_one, item): item for item in work_items}
        for future in as_completed(futures):
            bundle_key, result = future.result()
            if "error" in result:
                print(f"[-] {bundle_key} error: {result['error']}")
                errors.append({
                    "bundle": bundle_key,
                    "error": result["error"],
                    "last_attempted": datetime.now(timezone.utc).isoformat(),
                })
            else:
                bundle_name = result["bundle_name"]
                channel = result["channel"]
                dest_dir = os.path.join(temp_dir, bundle_name, channel)
                os.makedirs(dest_dir, exist_ok=True)
                with open(os.path.join(dest_dir, "patches-bundle.json"), "w", encoding="utf-8") as f:
                    f.write(result["bundle_content"])
                with open(os.path.join(dest_dir, "patches-list.json"), "w", encoding="utf-8") as f:
                    f.write(result["list_content"])
                downloaded_count += 1
            
    print(f"Successfully downloaded {downloaded_count} bundle+channel pairs.")

    # Update SHA store with SHAs for files that were downloaded
    if changed_tree:
        new_shas = load_file_shas()
        updated_tree = [item for item in changed_tree if item["path"] in changed_paths]
        update_file_shas(updated_tree, new_shas)
        new_shas = prune_stale_shas(new_shas, {item["path"] for item in tree_files})
        save_file_shas(new_shas)
        print(f"  SHA store updated: {len(new_shas)} tracked files")
    
    # Merge downloaded files over the existing raw tree: keep every bundle
    # that was not re-downloaded this run (a fresh checkout simply gets a
    # full download below), overlay the changed ones from temp_dir.
    bundles_old = bundles_raw_dir + "_old"
    if os.path.exists(bundles_old):
        shutil.rmtree(bundles_old, ignore_errors=True)

    if os.path.exists(bundles_raw_dir):
        for attempt in range(3):
            try:
                os.rename(bundles_raw_dir, bundles_old)
                break
            except PermissionError:
                if attempt < 2:
                    print(f"[warn] rename locked, retrying in 2s (attempt {attempt + 1}/3)")
                    time.sleep(2)
                else:
                    print("[warn] rename failed, trying shutil.move")
                    shutil.move(bundles_raw_dir, bundles_old)

    if os.path.exists(bundles_old):
        os.makedirs(bundles_raw_dir, exist_ok=True)
        for entry in os.listdir(bundles_old):
            src = os.path.join(bundles_old, entry)
            dst = os.path.join(bundles_raw_dir, entry)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        shutil.rmtree(bundles_old, ignore_errors=True)

    if os.path.exists(temp_dir):
        os.makedirs(bundles_raw_dir, exist_ok=True)
        for entry in os.listdir(temp_dir):
            src = os.path.join(temp_dir, entry)
            dst = os.path.join(bundles_raw_dir, entry)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    # Merge download results into last_run.json
    last_run_data = load_last_run()
    last_run_data["download_errors"] = errors
    last_run_data["downloaded_count"] = downloaded_count
    save_last_run(last_run_data)

if __name__ == "__main__":
    ensure_dirs()
    download_all_bundles()
