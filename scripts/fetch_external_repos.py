import os
import re
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from state_manager import (
    load_json, save_json, ensure_dirs, RAW_DIR, STATE_DIR, DATA_DIR,
    CUSTOM_REPO_PATH, IGNORE_REPO_PATH,
    load_repo_list, save_repo_list
)
from discovery.runner import load_verified_repos, split_repo_target

REPOS_TXT_URL = "https://raw.githubusercontent.com/rushiforai/morphe-archive/main/repos.txt"
REPOS_LIST_PATH = os.path.join(DATA_DIR, "repos_list.txt")
EXTERNAL_INDEX_PATH = os.path.join(STATE_DIR, "external_repos.json")
BUNDLES_RAW_DIR = os.path.join(RAW_DIR, "bundles")


def fetch_repos_txt():
    """Fetch the repos.txt file from morphe-archive."""
    print("  Fetching repos.txt from morphe-archive...")
    req = urllib.request.Request(REPOS_TXT_URL, headers={"User-Agent": "MorpheTracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8")
            return text
    except Exception as e:
        print(f"  Failed to fetch repos.txt: {e}")
        return None


def parse_repos_txt(text):
    """Parse repos.txt content into list of (owner, repo) tuples."""
    repos = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([^/#]+)/([^/#\s]+)", line)
        if m:
            repos.append((m.group(1).strip(), m.group(2).strip()))
    return repos


def save_repos_list(repos):
    """Write all known repos (owner/repo -> URL) to repos_list.txt."""
    lines = [
        "# Morphe Tracker - All Known Repositories",
        "# Each line: owner/repo -> full GitHub URL",
        f"# Last updated: {datetime.now(timezone.utc).isoformat()}",
        f"# Total: {len(repos)} repos",
        "",
    ]
    for owner, repo in sorted(repos, key=lambda x: (x[0].lower(), x[1].lower())):
        lines.append(f"{owner}/{repo} -> https://github.com/{owner}/{repo}")
    content = "\n".join(lines) + "\n"
    ensure_dirs()
    try:
        with open(REPOS_LIST_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Saved {len(repos)} repos to {REPOS_LIST_PATH}")
    except Exception as e:
        print(f"  Failed to save repos list: {e}")


def build_known_repo_urls():
    """Scan downloaded Jman bundles to extract all known repo URLs."""
    known = set()
    if not os.path.exists(BUNDLES_RAW_DIR):
        return known

    for bundle_name in os.listdir(BUNDLES_RAW_DIR):
        bundle_dir = os.path.join(BUNDLES_RAW_DIR, bundle_name)
        if not os.path.isdir(bundle_dir):
            continue
        for channel in os.listdir(bundle_dir):
            bundle_json_path = os.path.join(bundle_dir, channel, "patches-bundle.json")
            if not os.path.exists(bundle_json_path):
                continue
            try:
                with open(bundle_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            url = _extract_repo_url_from_bundle_json(data, bundle_name)
            if url:
                known.add(_normalize_url(url))
    return known


def _extract_repo_url_from_bundle_json(bundle_json, bundle_name):
    """Extract a repo URL from a patches-bundle.json dict.

    Mirrors the logic in parse_bundles.extract_repo_url but works
    with an already-loaded dict.
    """
    download_url = bundle_json.get("download_url")
    if isinstance(download_url, str) and download_url:
        result = _scan_url(download_url)
        if result:
            return result

    patches = bundle_json.get("patches", {})
    if isinstance(patches, dict) and "url" in patches:
        result = _scan_url(patches["url"])
        if result:
            return result

    integrations = bundle_json.get("integrations", {})
    if isinstance(integrations, dict) and "url" in integrations:
        result = _scan_url(integrations["url"])
        if result:
            return result

    description = bundle_json.get("description", "")
    if description:
        result = _scan_url(description)
        if result:
            return result

    return None


_GIT_PATTERN = re.compile(r"https://(github|gitlab)\.com/([^/]+)/([^/]+)")


def _reconstruct_url(platform, group2, group3):
    if platform == "github":
        return f"https://github.com/{group2}/{group3}"
    if platform == "gitlab":
        return f"https://gitlab.com/{group2}/{group3}"
    return None


def _scan_url(url):
    if not isinstance(url, str) or not url:
        return None
    for m in _GIT_PATTERN.finditer(url):
        result = _reconstruct_url(m.group(1), m.group(2), m.group(3))
        if result:
            return result
    return None


def _normalize_url(url):
    return url.lower().rstrip("/")


def is_repo_tracked(owner, repo, known_urls):
    """Check if owner/repo is already tracked in known URLs.

    Checks both GitHub and GitLab URL patterns since some repos
    in the archive are hosted on GitLab.
    """
    github_url = _normalize_url(f"https://github.com/{owner}/{repo}")
    if github_url in known_urls:
        return True
    gitlab_url = _normalize_url(f"https://gitlab.com/{owner}/{repo}")
    if gitlab_url in known_urls:
        return True
    return False


def try_fetch_source_file(owner, repo, filename, platform="github", branches=None):
    """Try to fetch a file from the repo's default branch.

    Returns (content, branch) or (None, None).
    """
    for branch in branches or ("main", "master", "dev"):
        if platform == "gitlab":
            raw_url = f"https://gitlab.com/{owner}/{repo}/-/raw/{branch}/{filename}"
        else:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
        req = urllib.request.Request(raw_url, headers={"User-Agent": "MorpheTracker/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8")
                return content, branch
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            print(f"    HTTP {e.code} fetching {filename} from {owner}/{repo}/{branch}")
            return None, None
        except Exception as e:
            print(f"    Error fetching {filename} from {owner}/{repo}/{branch}: {e}")
            return None, None
    return None, None


def fetch_github_releases(owner, repo, max_retries=3):
    """Fetch releases for a GitHub repo.  Returns list of release dicts or []."""
    token = os.environ.get("GITHUB_TOKEN", "")
    url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=10"
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "MorpheTracker/1.0",
        })
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                releases = []
                for r in data:
                    tag = r.get("tag_name", "")
                    prerelease = r.get("prerelease", False)
                    published = r.get("published_at", "")
                    assets = []
                    for a in r.get("assets", []):
                        assets.append({
                            "name": a.get("name", ""),
                            "url": a.get("browser_download_url", ""),
                        })
                    releases.append({
                        "tag": tag,
                        "prerelease": prerelease,
                        "published_at": published,
                        "assets": assets,
                    })
                return releases
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if e.code in (429, 403) and attempt < max_retries - 1:
                wait = (attempt + 1) * 5
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            print(f"    HTTP {e.code} fetching releases for {owner}/{repo}")
            return []
        except Exception as e:
            print(f"    Error fetching releases for {owner}/{repo}: {e}")
            return []
    return []


def _make_bundle_slug(owner, repo):
    """Create a unique slug for an external bundle.

    Uses the GitHub owner name (lowercased).  If it collides with an existing
    bundle directory on disk, appends the repo name.
    """
    slug = owner.lower().replace("_", "-")
    if "/" in repo:
        slug = f"{slug}-{repo}".lower().replace("_", "-").replace("/", "-")
    if os.path.exists(os.path.join(BUNDLES_RAW_DIR, slug)):
        slug = f"{slug}-{repo.lower().replace('_', '-').replace('/', '-')}"
    return slug


def process_external_repo(
    owner, repo, platform="github", branch=None, requested_channels=None
):
    """Attempt to fetch and save a Morphe bundle from an external repo.

    Returns a dict describing what was done, or None on skip.
    platform: 'github' or 'gitlab'
    """
    bundle_slug = _make_bundle_slug(owner, repo)
    platform_label = "GitLab" if platform == "gitlab" else "GitHub"
    print(f"  Processing {owner}/{repo} [{platform_label}] -> bundle name: {bundle_slug}")

    branches = (branch,) if branch else None
    # 1. Fetch patches-bundle.json and patches-list.json from source
    bundle_content, branch = try_fetch_source_file(
        owner, repo, "patches-bundle.json", platform, branches
    )
    if not bundle_content:
        print(f"    SKIP - no patches-bundle.json found in source")
        return None

    list_content, _ = try_fetch_source_file(
        owner, repo, "patches-list.json", platform, (branch,)
    )
    if not list_content:
        print(f"    SKIP — no patches-list.json found in source")
        return None

    # 2. Validate the bundle JSON
    try:
        bundle_json = json.loads(bundle_content)
    except json.JSONDecodeError as e:
        print(f"    SKIP — invalid patches-bundle.json: {e}")
        return None

    download_url = bundle_json.get("download_url")
    if not isinstance(download_url, str) or not download_url.lower().endswith(".mpp"):
        print(f"    SKIP — download_url does not point to a .mpp file")
        return None

    path_parts = download_url.split("/")
    if len(path_parts) < 8:
        print(f"    SKIP — download_url path too short ({len(path_parts)} segments)")
        return None

    # 3. Determine channel(s) based on release tags
    releases = fetch_github_releases(owner, repo) if platform == "github" else []
    latest_stable = None
    latest_dev = None
    today = datetime.now(timezone.utc).isoformat()

    for rel in releases:
        if rel["prerelease"]:
            if latest_dev is None or rel["tag"] > latest_dev["tag"]:
                latest_dev = rel
        else:
            if latest_stable is None or rel["tag"] > latest_stable["tag"]:
                latest_stable = rel

    channels_to_create = []
    if latest_stable:
        channels_to_create.append(("stable", latest_stable))
    else:
        channels_to_create.append(("stable", None))

    if latest_dev or "dev" in (requested_channels or ()):
        channels_to_create.append(("dev", latest_dev))

    # 4. Save files for each channel
    for channel, release in channels_to_create:
        dest_dir = os.path.join(BUNDLES_RAW_DIR, bundle_slug, channel)
        os.makedirs(dest_dir, exist_ok=True)

        # Save patches-bundle.json (possibly with updated version from release)
        if release:
            bundle_json["version"] = release["tag"].lstrip("v")
            bundle_json["created_at"] = release.get("published_at", today)
            # Try to update download_url to point to the release .mpp asset
            for asset in release["assets"]:
                if asset["name"].endswith(".mpp"):
                    bundle_json["download_url"] = asset["url"]
                    break

        with open(os.path.join(dest_dir, "patches-bundle.json"), "w", encoding="utf-8") as f:
            json.dump(bundle_json, f, indent=2, ensure_ascii=False)

        # Save patches-list.json
        list_path = os.path.join(dest_dir, "patches-list.json")
        with open(list_path, "w", encoding="utf-8") as f:
            f.write(list_content)

        print(f"    Saved {bundle_slug}:{channel} ({len(list_content)} bytes patches-list)")

    # 5. Try to update the download_url in stable from the latest stable release asset
    #    (already done above inside the loop)

    return {
        "owner": owner,
        "repo": repo,
        "bundle_slug": bundle_slug,
        "branch": branch,
        "channels": [c for c, _ in channels_to_create],
        "version": bundle_json.get("version", ""),
        "repo_url": f"https://{'gitlab' if platform == 'gitlab' else 'github'}.com/{owner}/{repo}",
        "platform": platform,
    }


def fetch_external_repos():
    """Main entry point:
    1. Load custom_repo.txt — these repos are fetched directly (not from Jman)
    2. Load ignore_repo.txt — these repos are skipped entirely
    3. Fetch repos.txt, filter out custom/ignored repos, download the rest
    """
    ensure_dirs()

    # 0a. Load custom repos — fetch these directly, skip them in Jman scan
    custom_repos = load_repo_list(CUSTOM_REPO_PATH)
    custom_keys = set()
    for owner, repo, platform in custom_repos:
        custom_keys.add(f"{owner}/{repo}".lower())
    print(f"  Custom repos to fetch: {len(custom_repos)}")
    for owner, repo, platform in custom_repos:
        print(f"    - {owner}/{repo} [{platform}]")

    # 0b. Load ignored repos — skip these entirely
    ignore_repos = load_repo_list(IGNORE_REPO_PATH)
    ignore_keys = set()
    for owner, repo, _ in ignore_repos:
        ignore_keys.add(f"{owner}/{repo}".lower())
    if ignore_repos:
        print(f"  Ignored repos (will skip): {len(ignore_repos)}")

    # 0c. Load verified discovery candidates
    discovered_targets = []
    discovered_keys = set()
    try:
        for entry in load_verified_repos():
            target = split_repo_target(entry)
            if not target:
                continue
            key = f"{target['owner']}/{target['name']}".lower()
            if key in custom_keys or key in ignore_keys or key in discovered_keys:
                continue
            channels = target.get("channels", {})
            primary = channels.get("stable") or channels.get("dev") or {}
            target["branch"] = primary.get("branch", "")
            target["requested_channels"] = list(channels.keys())
            discovered_targets.append(target)
            discovered_keys.add(key)
    except Exception as e:
        print(f"  Discovery state unavailable: {e}")
    if discovered_targets:
        print(f"  Verified discovery candidates: {len(discovered_targets)}")

    # 0d. Build known repo URLs from currently-downloaded Jman bundles
    known_urls = build_known_repo_urls()
    print(f"  Found {len(known_urls)} known repo URLs from downloaded bundles")

    # 0e. Already-tracked filter helper
    def is_already_tracked(owner, repo):
        key = f"{owner}/{repo}".lower()
        if key in custom_keys:
            return True
        if key in ignore_keys:
            return True
        return is_repo_tracked(owner, repo, known_urls)

    # 1. Process custom repos first (fetch directly, not from Jman)
    custom_results = []
    custom_errors = []
    custom_success_keys = set()
    for owner, repo, platform in custom_repos:
        try:
            result = process_external_repo(owner, repo, platform)
            if result:
                custom_results.append(result)
                custom_success_keys.add(f"{owner}/{repo}".lower())
                print(f"    [OK] Custom repo added: {owner}/{repo}")
            else:
                custom_errors.append({"repo": f"{owner}/{repo}", "error": "No viable bundle data"})
        except Exception as e:
            print(f"    [ERR] Error processing custom repo {owner}/{repo}: {e}")
            custom_errors.append({"repo": f"{owner}/{repo}", "error": str(e)})
        time.sleep(0.3)

    # Remove successfully processed repos from custom_repo.txt
    if custom_success_keys:
        remaining = [(o, r, p) for o, r, p in custom_repos if f"{o}/{r}".lower() not in custom_success_keys]
        save_repo_list(CUSTOM_REPO_PATH, remaining)
        print(f"  Removed {len(custom_success_keys)} successfully processed repos from {CUSTOM_REPO_PATH}")
        # Also update known_urls so these repos are not re-fetched as missing
        for owner, repo, platform in custom_repos:
            if f"{owner}/{repo}".lower() in custom_success_keys:
                url = f"https://{'gitlab' if platform == 'gitlab' else 'github'}.com/{owner}/{repo}"
                known_urls.add(_normalize_url(url))

    # 1b. Process verified discovery candidates
    discovered_results = []
    discovered_errors = []
    for target in discovered_targets:
        owner = target["owner"]
        repo = target["name"]
        platform = target["platform"]
        key = f"{owner}/{repo}".lower()
        if key in custom_success_keys or is_repo_tracked(owner, repo, known_urls):
            continue
        try:
            result = process_external_repo(
                owner,
                repo,
                platform,
                branch=target.get("branch"),
                requested_channels=target.get("requested_channels"),
            )
            if result:
                discovered_results.append(result)
                known_urls.add(_normalize_url(result["repo_url"]))
                print(f"    [OK] Discovered repo added: {owner}/{repo}")
            else:
                discovered_errors.append({
                    "repo": f"{owner}/{repo}",
                    "error": "No viable bundle data",
                })
        except Exception as e:
            print(f"    [ERR] Error processing discovered repo {owner}/{repo}: {e}")
            discovered_errors.append({"repo": f"{owner}/{repo}", "error": str(e)})
        time.sleep(0.3)

    # 2. Fetch repos.txt
    repos_text = fetch_repos_txt()
    if not repos_text:
        print("  No repos.txt data — skipping archive repo fetch")
        # Still save index with custom results
        save_json(EXTERNAL_INDEX_PATH, {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "archive_available": False,
            "added": custom_results + discovered_results,
            "errors": custom_errors + discovered_errors,
            "custom_fetched": len(custom_results),
            "discovered_fetched": len(discovered_results),
            "discovery_verified": len(discovered_targets),
            "total_added": len(custom_results) + len(discovered_results),
            "total_errors": len(custom_errors) + len(discovered_errors),
        })
        # Still save custom repos to repos_list.txt
        custom_repo_list = [(o, r) for o, r, p in custom_repos if f"{o}/{r}".lower() in custom_success_keys]
        if custom_repo_list:
            from state_manager import save_repos_list as save_repos_list_file
            save_repos_list_file(custom_repo_list)
        return

    # 3. Parse into repo list
    all_repos = parse_repos_txt(repos_text)
    archive_count = len(all_repos)
    print(f"  Found {len(all_repos)} repos in repos.txt")

    # 3a. Add successfully processed custom repos to all_repos
    for owner, repo, platform in custom_repos:
        key = f"{owner}/{repo}".lower()
        if key in custom_success_keys and (owner, repo) not in all_repos:
            all_repos.append((owner, repo))
            print(f"  Added custom repo to repos list: {owner}/{repo}")

    # 3b. Save/refresh the local repos list file
    save_repos_list(all_repos)

    # 4. Filter out repos that are already tracked (in Jman, custom, or ignored)
    missing_repos = []
    for owner, repo in all_repos:
        if f"{owner}/{repo}".lower() in discovered_keys:
            continue
        if not is_already_tracked(owner, repo):
            missing_repos.append((owner, repo))

    print(f"  Repos not yet tracked in Jman/custom/ignore: {len(missing_repos)}")

    # 5. Skip repos that are clearly not Morphe patch repos
    skip_patterns = [
        r"(?i)awesome-revanced",
        r"(?i)universal-revanced-manager",
        r"(?i)builder-for-morphe",
        r"(?i)jadx-morphe",
    ]
    filtered = []
    for owner, repo in missing_repos:
        full = f"{owner}/{repo}"
        if any(re.search(p, full) for p in skip_patterns):
            print(f"  SKIP {full} — known non-patch repo")
            continue
        filtered.append((owner, repo))
    missing_repos = filtered

    # 6. Process each missing repo
    results = list(custom_results) + list(discovered_results)
    errors = list(custom_errors) + list(discovered_errors)
    for owner, repo in missing_repos:
        try:
            result = process_external_repo(owner, repo)
            if result:
                results.append(result)
                print(f"    [OK] Added {owner}/{repo}")
            else:
                errors.append({"repo": f"{owner}/{repo}", "error": "No viable bundle data"})
        except Exception as e:
            print(f"    [ERR] Error processing {owner}/{repo}: {e}")
            errors.append({"repo": f"{owner}/{repo}", "error": str(e)})
        time.sleep(0.3)  # be gentle to GitHub's API

    # 7. Save index of processed external repos
    save_json(EXTERNAL_INDEX_PATH, {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "archive_available": archive_count > 0,
        "added": results,
        "errors": errors,
        "custom_fetched": len(custom_results),
        "discovered_fetched": len(discovered_results),
        "discovery_verified": len(discovered_targets),
        "total_added": len(results),
        "total_errors": len(errors),
    })

    print(f"  Added {len(results)} external repos ({len(errors)} errors)")
    if custom_results:
        print(f"    Custom: {len(custom_results)}")
    if discovered_results:
        print(f"    Discovered: {len(discovered_results)}")
    if results:
        for r in results:
            print(f"    - {r['owner']}/{r['repo']} -> {r['bundle_slug']} ({', '.join(r['channels'])})")


if __name__ == "__main__":
    ensure_dirs()
    fetch_external_repos()
