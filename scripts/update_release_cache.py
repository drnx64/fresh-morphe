import os, re, json, sys, time, urllib.request, urllib.error, urllib.parse, html as html_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from state_manager import load_json, save_json, ensure_dirs, STATE_DIR, RAW_DIR, match_release_to_version
from config import RELEASE_CACHE_TTL_HOURS

RELEASE_CACHE_PATH = os.path.join(STATE_DIR, "release_cache.json")
CACHE_TTL_HOURS = RELEASE_CACHE_TTL_HOURS


def fetch_github_releases(owner, repo, retry=3):
    token = os.environ.get("GITHUB_TOKEN", "")
    url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=15"
    for attempt in range(retry):
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "FreshMorphe/1.0")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                releases = []
                for r in data:
                    tag = r.get("tag_name", "")
                    body = r.get("body", "")
                    prerelease = r.get("prerelease", False)
                    date_released = r.get("published_at", "")
                    if tag:
                        releases.append({"tag": tag, "body": body, "prerelease": prerelease, "dateReleased": date_released})
                return releases
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if e.code in (429, 403) and attempt < retry - 1:
                wait = (attempt + 1) * 5
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            print(f"    HTTP {e.code} for GitHub {owner}/{repo}")
            return []
        except Exception as e:
            print(f"    Error for GitHub {owner}/{repo}: {e}")
            return []
    return []


def fetch_gitlab_releases(project_path, retry=3):
    token = os.environ.get("GITLAB_TOKEN", "")
    encoded_path = urllib.parse.quote(project_path, safe="")
    url = f"https://gitlab.com/api/v4/projects/{encoded_path}/releases?per_page=15"
    for attempt in range(retry):
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        req.add_header("User-Agent", "FreshMorphe/1.0")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                releases = []
                for r in data:
                    tag = r.get("tag_name", "")
                    description = r.get("description", "") or ""
                    if r.get("description_html"):
                        description = html_mod.unescape(re.sub(r'<[^>]+>', '', r["description_html"]))
                    date_released = r.get("released_at") or r.get("created_at", "")
                    if tag:
                        releases.append({"tag": tag, "body": description, "prerelease": False, "dateReleased": date_released})
                return releases
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if e.code in (429, 403) and attempt < retry - 1:
                wait = (attempt + 1) * 5
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            print(f"    HTTP {e.code} for GitLab {project_path}")
            return []
        except Exception as e:
            print(f"    Error for GitLab {project_path}: {e}")
            return []
    return []



def _extract_repos_from_parsed_bundles(parsed_bundles):
    repos = {}
    for bundle_key, bundle_data in parsed_bundles.items():
        repo_url = bundle_data.get("repo_url", "")
        if not repo_url:
            continue
        repos.setdefault(repo_url, []).append((bundle_key, bundle_data.get("version", "")))
    return repos


def _is_cache_stale(repo_cache):
    if not repo_cache or "fetched_at" not in repo_cache:
        return True
    try:
        from datetime import datetime, timezone
        fetched = datetime.fromisoformat(repo_cache["fetched_at"])
        age = datetime.now(timezone.utc) - fetched
        return age.total_seconds() > CACHE_TTL_HOURS * 3600
    except Exception:
        return True


def _normalize_url(url):
    return url.lower().rstrip("/")


def update_release_cache(changed_repo_urls=None):
    """Refresh release cache, optionally scoped to only changed repos.

    Args:
        changed_repo_urls: Optional set/list of repo URLs whose bundles changed
            in the latest diff. When provided, only these repos (plus cold-cache
            repos with no entry) are fetched. All other repos are skipped.
            When None, fetch all stale repos (legacy behavior).
    """
    ensure_dirs()
    from datetime import datetime, timezone

    parsed_path = os.path.join(RAW_DIR, "parsed_bundles.json")
    parsed_bundles = load_json(parsed_path, default={})
    if not parsed_bundles:
        print("No parsed bundles found. Run parse_bundles.py first.")
        return

    cache = load_json(RELEASE_CACHE_PATH, default={})
    repos = _extract_repos_from_parsed_bundles(parsed_bundles)
    print(f"Found {len(repos)} unique repo URLs across {len(parsed_bundles)} bundles")

    # Normalize changed URLs for fast lookup
    changed_set = None
    if changed_repo_urls is not None:
        changed_set = {_normalize_url(u) for u in changed_repo_urls}
        print(f"Diff-aware mode: will refresh {len(changed_set)} changed repos + cold-cache repos")

    fetched = 0
    skipped = 0

    # Build work items for parallel fetching
    work_items = []
    for repo_url, bundle_versions in repos.items():
        norm_url = _normalize_url(repo_url)
        repo_cache = cache.get(repo_url, {})

        should_fetch = False
        reason = ""

        if changed_set is not None:
            if norm_url in changed_set:
                should_fetch = True
                reason = "bundle changed"
            elif not repo_cache or "fetched_at" not in repo_cache:
                should_fetch = True
                reason = "cold cache"
            else:
                skipped += 1
                continue
        else:
            if _is_cache_stale(repo_cache):
                should_fetch = True
                reason = "cache stale"
            else:
                skipped += 1
                continue

        work_items.append((repo_url, reason))

    if not work_items:
        print(f"No repos need fetching. Cache has {len(cache)} entries, all fresh.")
        save_json(RELEASE_CACHE_PATH, cache)
        return cache

    print(f"Fetching releases for {len(work_items)} repos with 10 workers...")

    def _fetch_one(item):
        repo_url, reason = item
        github_match = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
        gitlab_match = re.search(r"gitlab\.com/(.+)", repo_url)
        releases = []
        if github_match:
            owner = github_match.group(1)
            repo_name = github_match.group(2)
            releases = fetch_github_releases(owner, repo_name)
        elif gitlab_match:
            project_path = gitlab_match.group(1).rstrip("/")
            project_path = re.sub(r"/-/(?:releases|issues|merge_requests|blob|tree|raw).*$", "", project_path)
            project_path = re.sub(r"/releases/.*$", "", project_path)
            api_m = re.match(r"api/v4/projects/([^/]+(?:%2F[^/]+)*)", project_path)
            if api_m:
                project_path = api_m.group(1).replace("%2F", "/")
            releases = fetch_gitlab_releases(project_path)
        return (repo_url, reason, releases)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_one, item): item for item in work_items}
        for future in as_completed(futures):
            repo_url, reason, releases = future.result()
            cache[repo_url] = {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "releases": releases
            }
            fetched += 1

    save_json(RELEASE_CACHE_PATH, cache)
    print(f"\nRelease cache saved: {fetched} fetched, {skipped} skipped, {len(cache)} total.")
    return cache


if __name__ == "__main__":
    ensure_dirs()
    update_release_cache()
