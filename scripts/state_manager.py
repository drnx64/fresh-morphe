import os
import json
import re

# Define base paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_DIR = os.path.join(DATA_DIR, "state")
RAW_DIR = os.path.join(DATA_DIR, "raw")
OUTPUT_DIR = os.path.join(DATA_DIR, "output")
ROOT_DIR = BASE_DIR
ROOT_DATA_DIR = os.path.join(ROOT_DIR, "data")

# State files paths
CURRENT_SNAPSHOT_PATH = os.path.join(STATE_DIR, "current_snapshot.json")
DAILY_BUFFER_PATH = os.path.join(STATE_DIR, "daily_buffer.json")
LAST_RUN_PATH = os.path.join(STATE_DIR, "last_run.json")

CHANGELOG_JSON_PATH = os.path.join(OUTPUT_DIR, "changelog.json")
CHANGELOG_MD_PATH = os.path.join(OUTPUT_DIR, "changelog.md")

# Repo list files
CUSTOM_REPO_PATH = os.path.join(ROOT_DATA_DIR, "custom_repo.txt")
IGNORE_REPO_PATH = os.path.join(ROOT_DATA_DIR, "ignore_repo.txt")

# Split data files (kebab-case)
CORE_JSON_PATH = os.path.join(ROOT_DATA_DIR, "core.json")
BUNDLES_JSON_PATH = os.path.join(ROOT_DATA_DIR, "bundles.json")
BUNDLES_DIR = os.path.join(ROOT_DATA_DIR, "bundles")
CHANGES_JSON_PATH = os.path.join(ROOT_DATA_DIR, "changes.json")
STATS_JSON_PATH = os.path.join(ROOT_DATA_DIR, "stats.json")

def ensure_dirs():
    """Ensure all required directories exist."""
    for path in [STATE_DIR, RAW_DIR, OUTPUT_DIR, ROOT_DATA_DIR]:
        os.makedirs(path, exist_ok=True)

def load_json(filepath, default=None):
    """Safely load a JSON file, returning the default if it doesn't exist or is invalid."""
    if default is None:
        default = {}
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {filepath}: {e}. Returning default.")
        return default

def save_json(filepath, data):
    """Safely save data to a JSON file with pretty printing."""
    ensure_dirs()
    temp_path = filepath + ".tmp"
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        if os.path.exists(filepath):
            os.remove(filepath)
        os.rename(temp_path, filepath)
        return True
    except Exception as e:
        print(f"Error saving to {filepath}: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        return False

def save_new_snapshot(snapshot_data):
    """Save the new snapshot, overwriting the previous one."""
    ensure_dirs()
    snapshot_data = _strip_icon_url(snapshot_data)
    return save_json(CURRENT_SNAPSHOT_PATH, snapshot_data)

def load_current_snapshot():
    return load_json(CURRENT_SNAPSHOT_PATH, default={})


def repo_slug(repo_url):
    """Normalize a github/gitlab repo URL to 'owner/repo' (lowercase, no .git)."""
    if not isinstance(repo_url, str) or not repo_url:
        return ""
    m = re.match(r"^https?://(?:www\.)?(?:github|gitlab)\.com/([^/]+)/([^/#?]+)", repo_url.strip().lower())
    if not m:
        return ""
    repo = m.group(2).rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{m.group(1)}/{repo}"


def load_claimed_keys():
    """Authoritative set of bundle keys still expected upstream.

    Returns {"keys": set of "bundle:channel" from the Jman tree (minus
    custom/ignore skip names), "repos": set of "owner/repo" claimed by the
    external-repos index} — or None when the claim universe is unusable
    (missing tree, missing external index, or archive fetch failed), in which
    case callers must conservatively suppress all removals.
    """
    tree_files = load_json(os.path.join(RAW_DIR, "tree.json"), default=[])
    ext_path = os.path.join(STATE_DIR, "external_repos.json")
    ext = load_json(ext_path, default={}) if os.path.exists(ext_path) else None
    if not tree_files or not ext or not ext.get("archive_available", True):
        return None

    skip = set()
    for filepath in (CUSTOM_REPO_PATH, IGNORE_REPO_PATH):
        for owner, repo, _ in load_repo_list(filepath):
            skip.add(owner.lower().replace("_", "-"))
            skip.add(f"{owner.lower()}-{repo.lower().replace('_', '-')}")

    from download_bundles import group_tree_files  # lazy: avoids import cycle
    keys = set()
    for name, channels in group_tree_files(tree_files).items():
        if name.lower() in skip:
            continue
        for channel in channels:
            keys.add(f"{name}:{channel}")

    # Registry entries can preserve historical keys that were merged/renamed
    # upstream. Keep those claimed so reconciliation carries them forward
    # instead of reporting a false removal.
    for item in tree_files:
        for legacy_key in item.get("legacy_keys") or []:
            keys.add(legacy_key)

    repos = set()
    for entry in ext.get("added", []):
        owner = str(entry.get("owner", "")).lower()
        repo = str(entry.get("repo", "")).lower()
        if owner and repo:
            repos.add(f"{owner}/{repo}")
    for entry in ext.get("errors", []):
        repo = str(entry.get("repo", "")).lower().strip().strip("/")
        if repo:
            repos.add(repo)

    return {"keys": keys, "repos": repos}


def is_claimed(key, record, claims):
    """True if a bundle key/repo is still claimed upstream (or claims are unavailable)."""
    if claims is None:
        return True
    if key in claims["keys"]:
        return True
    slug = repo_slug((record or {}).get("repo_url", ""))
    return bool(slug) and slug in claims["repos"]


def rebuild_snapshot_from_bundles():
    """Rebuild snapshot from committed data/bundles/*.json files.

    Used when current_snapshot.json is missing (e.g. CI fresh checkout).
    Only files referenced by _index.json are included — stray/orphan files
    must never re-enter the snapshot (they would resurface as false
    REMOVED BUNDLE diffs on every run).
    """
    if not os.path.isdir(BUNDLES_DIR):
        print("[snapshot] No data/bundles/ directory found, starting with empty snapshot")
        return {}

    index = load_json(os.path.join(BUNDLES_DIR, "_index.json"), default={})
    if not index:
        print("[snapshot] No _index.json — starting with empty snapshot")
        return {}

    snapshot = {}
    for key in index:
        filepath = os.path.join(BUNDLES_DIR, key.replace(":", "_") + ".json")
        if not os.path.exists(filepath):
            continue
        try:
            record = load_json(filepath, default=None)
            if not record:
                continue
            for app in record.get("apps", []):
                app.pop("icon_url", None)
            snapshot[key] = record
        except Exception as e:
            print(f"[snapshot] Error reading {filepath}: {e}")

    print(f"[snapshot] Rebuilt snapshot from {len(snapshot)} indexed bundle files")
    return snapshot

def load_daily_buffer():
    return load_json(DAILY_BUFFER_PATH, default={
        "date": "",
        "lastChecked": "",
        "scan_counter": 0,
        "affected_bundles": {}
    })

def save_daily_buffer(buffer_data):
    return save_json(DAILY_BUFFER_PATH, buffer_data)

def save_last_run(last_run_data):
    return save_json(LAST_RUN_PATH, last_run_data)

def load_last_run():
    return load_json(LAST_RUN_PATH, default={})

def save_core_json(data):
    return save_json(CORE_JSON_PATH, data)

def save_stats_json(data):
    return save_json(STATS_JSON_PATH, data)

def save_changes_json(data):
    return save_json(CHANGES_JSON_PATH, data)

def _strip_icon_url(data):
    """Remove icon_url from all app entries in bundle data."""
    for record in data.values():
        for app in record.get("apps", []):
            app.pop("icon_url", None)
    return data


def save_bundles_json(data):
    data = _strip_icon_url(dict(data))
    return save_json(BUNDLES_JSON_PATH, data)


def save_bundles_split(data):
    """Save bundles as individual files: data/bundles/_index.json + data/bundles/<key>.json"""
    data = _strip_icon_url(dict(data))
    os.makedirs(BUNDLES_DIR, exist_ok=True)

    index = {}
    for key, record in data.items():
        index[key] = {
            "bundle": record.get("bundle", ""),
            "channel": record.get("channel", ""),
            "version": record.get("version", ""),
            "repo_url": record.get("repo_url", ""),
            "patches_name": record.get("patches_name", ""),
            "release_tag": record.get("release_tag", ""),
            "release_date": record.get("release_date", ""),
            "app_count": len(record.get("apps", [])),
            # Package list lets the site paint the full apps directory from the
            # index alone; per-app `patches` stay in the per-bundle file and are
            # fetched on demand (see scripts/services/bundleLoader.js).
            "apps": [app.get("package", "") for app in record.get("apps", [])],
            "stars": record.get("stars", 0),
            "avatarUrl": record.get("avatarUrl", ""),
            "bundleImageUrl": record.get("bundleImageUrl", ""),
            "repoDescription": record.get("repoDescription", ""),
            "isArchived": record.get("isArchived", False),
            "isPreRelease": record.get("isPreRelease", False),
            "isFork": record.get("isFork", False),
            "forkParent": record.get("forkParent", ""),
        }
        filename = key.replace(":", "_") + ".json"
        save_json(os.path.join(BUNDLES_DIR, filename), record)

    save_json(os.path.join(BUNDLES_DIR, "_index.json"), index)

    # Prune orphaned per-bundle files (no longer in the index) so stale
    # bundles cannot resurface on a CI snapshot rebuild.
    if data:
        expected = {key.replace(":", "_") + ".json" for key in index}
        pruned = 0
        for filename in os.listdir(BUNDLES_DIR):
            if filename == "_index.json" or not filename.endswith(".json"):
                continue
            if filename not in expected:
                try:
                    os.remove(os.path.join(BUNDLES_DIR, filename))
                    pruned += 1
                except OSError as e:
                    print(f"[bundles] Failed to prune {filename}: {e}")
        if pruned:
            print(f"[bundles] Pruned {pruned} orphaned bundle files")

    print(f"[bundles] Split {len(data)} bundles into {BUNDLES_DIR}")
    return True

def load_core_json():
    return load_json(CORE_JSON_PATH, default={})

def load_repo_list(filepath):
    """Load a repo list file (custom_repo.txt or ignore_repo.txt).

    Returns a list of (owner, repo, platform) tuples where platform is 'github' or 'gitlab'.
    Lines starting with '#' are ignored. Empty lines are ignored.
    Format: owner/repo or gl:owner/repo for GitLab.
    """
    repos = []
    if not os.path.exists(filepath):
        return repos
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                platform = "github"
                entry = line
                if line.startswith("gl:") or line.startswith("gitlab:"):
                    platform = "gitlab"
                    entry = line.split(":", 1)[1].strip()
                m = re.match(r"^([^/]+)/([^/#\s]+)", entry)
                if m:
                    repos.append((m.group(1).strip(), m.group(2).strip(), platform))
    except Exception as e:
        print(f"Error loading repo list from {filepath}: {e}")
    return repos

def save_repo_list(filepath, repos):
    """Save a list of (owner, repo, platform) tuples back to a repo list file.

    Lines starting with '#' are preserved. Existing content before the first entry is kept.
    """

    header_lines = []
    new_entries = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                in_header = True
                for line in f:
                    stripped = line.strip()
                    if in_header and (not stripped or stripped.startswith("#")):
                        header_lines.append(line.rstrip("\n"))
                    else:
                        in_header = False
        except Exception:
            header_lines = []
    for owner, repo, platform in repos:
        if platform == "gitlab":
            new_entries.append(f"gl:{owner}/{repo}")
        else:
            new_entries.append(f"{owner}/{repo}")
    content = "\n".join(header_lines + new_entries) + "\n"
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"Error saving repo list to {filepath}: {e}")

def load_stats_json():
    return load_json(STATS_JSON_PATH, default={})

def load_changes_json():
    return load_json(CHANGES_JSON_PATH, default={})

def load_bundles_json():
    return load_json(BUNDLES_JSON_PATH, default={})


# ── Shared utilities ──────────────────────────────────────

COMMON_PACKAGES = {
    "com.google.android.youtube": "YouTube",
    "com.google.android.apps.youtube.music": "YouTube Music",
    "com.reddit.frontpage": "Reddit",
    "com.twitter.android": "Twitter",
    "com.instagram.android": "Instagram",
    "com.zhiliaoapp.musically": "TikTok",
    "com.spotify.music": "Spotify",
    "com.whatsapp": "WhatsApp",
    "org.telegram.messenger": "Telegram",
    "com.facebook.katana": "Facebook",
    "com.facebook.orca": "Messenger",
    "com.discord": "Discord",
    "com.netflix.mediaclient": "Netflix",
    "at.gv.oe.app": "OE App",
    "com.snapchat.android": "Snapchat",
    "com.pinsight.pinsight": "Pinsight",
    "com.google.android.apps.photos": "Google Photos",
    "com.google.android.apps.maps": "Google Maps",
    "com.google.android.gm": "Gmail",
}


def match_release_to_version(version, releases):
    """Match a version string to a GitHub release entry. Uses exact match first, then substring."""
    if not version:
        return None
    v_clean = version.lower().lstrip("v")
    for r in releases:
        tag_clean = r.get("tag", "").lower().lstrip("v")
        if tag_clean == v_clean:
            return r
    for r in releases:
        tag_clean = r.get("tag", "").lower().lstrip("v")
        if v_clean in tag_clean or tag_clean in v_clean:
            return r
    return None


def cleanup_orphaned_state():
    """Remove stale entries from app_cache.json that are no longer referenced by any bundle."""
    snapshot = load_json(CURRENT_SNAPSHOT_PATH, default={})
    if not snapshot:
        return

    current_pkgs = set()
    for record in snapshot.values():
        for app in record.get("apps", []):
            pkg = app.get("package", "")
            if pkg:
                current_pkgs.add(pkg.lower().strip())

    app_cache_path = os.path.join(STATE_DIR, "app_cache.json")
    cache = load_json(app_cache_path, default={})
    if cache:
        pruned = {k: v for k, v in cache.items() if k in current_pkgs}
        if len(pruned) < len(cache):
            save_json(app_cache_path, pruned)
            print(f"[*] Cleaned app_cache: {len(cache)} -> {len(pruned)} entries")
