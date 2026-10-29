"""Verify that a candidate repository is a Morphe patch bundle source."""

import urllib.error
import urllib.parse

from repo_utils import build_raw_url, fetch, format_request_error, parse_repo_url

BUNDLE_MANIFEST = "patches-bundle.json"
LIST_MANIFEST = "patches-list.json"
DEFAULT_BRANCHES = ("main", "master", "dev")


def _is_unavailable(error):
    return isinstance(error, urllib.error.HTTPError) and error.code in (404, 410, 451)


def _valid_download_url(value):
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        return False
    return urllib.parse.urlsplit(value).path.lower().endswith(".mpp")


def _patch_count(payload):
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        patches = payload.get("patches")
        if isinstance(patches, list):
            return len(patches)
        if isinstance(patches, dict):
            return len(patches)
    return 0


def candidate_branches(repo_entry):
    branches = []
    for branch in (
        repo_entry.get("default_branch"),
        *DEFAULT_BRANCHES,
    ):
        branch = str(branch or "").strip()
        if branch and branch not in branches:
            branches.append(branch)
    return branches


def verify_branch(platform, repo, branch):
    bundle_url = build_raw_url(platform, repo, branch, BUNDLE_MANIFEST)
    list_url = build_raw_url(platform, repo, branch, LIST_MANIFEST)
    if not bundle_url or not list_url:
        return {"status": "review", "reason": "cannot build manifest URLs"}

    bundle = None
    try:
        bundle = fetch(bundle_url, timeout=20, as_json=True)
    except (OSError, ValueError, urllib.error.URLError) as error:
        if not _is_unavailable(error):
            return {
                "status": "transient",
                "reason": f"bundle manifest: {format_request_error(error)}",
            }

    if not isinstance(bundle, dict):
        return {"status": "review", "reason": "patches-bundle.json is not an object"}

    download_url = bundle.get("download_url")
    if not _valid_download_url(download_url):
        return {
            "status": "review",
            "reason": "patches-bundle.json has no valid .mpp download_url",
        }

    try:
        patch_list = fetch(list_url, timeout=20, as_json=True)
    except (OSError, ValueError, urllib.error.URLError) as error:
        if _is_unavailable(error):
            return {"status": "review", "reason": "patches-list.json not found"}
        return {
            "status": "transient",
            "reason": f"patch list: {format_request_error(error)}",
        }

    patch_count = _patch_count(patch_list)
    if patch_count <= 0:
        return {"status": "review", "reason": "patches-list.json contains no patches"}

    channel = "dev" if branch == "dev" else "stable"
    return {
        "status": "verified",
        "channel": channel,
        "evidence": {
            "branch": branch,
            "bundle_path": BUNDLE_MANIFEST,
            "list_path": LIST_MANIFEST,
            "download_url": download_url,
            "patch_count": patch_count,
            "name": bundle.get("name", ""),
            "version": bundle.get("version", ""),
        },
    }


def verify_repo(repo_entry):
    platform, repo = parse_repo_url(repo_entry.get("url", ""))
    if not platform or not repo:
        platform = repo_entry.get("platform", "")
        repo = repo_entry.get("repo", "")
    if not platform or not repo:
        return {"status": "review", "reason": "invalid repository URL"}

    results = []
    errors = []
    for branch in candidate_branches(repo_entry):
        result = verify_branch(platform, repo, branch)
        if result.get("status") == "verified":
            results.append(result)
        elif result.get("status") == "transient":
            errors.append(result["reason"])
        elif result.get("reason"):
            errors.append(result["reason"])

    if not results:
        reason = errors[0] if errors else "no manifests found"
        if any("HTTP 403" in item or "HTTP 429" in item for item in errors):
            return {"status": "transient", "reason": reason}
        return {"status": "review", "reason": reason}

    channels = {}
    for result in results:
        channel = result["channel"]
        current = channels.get(channel)
        evidence = result["evidence"]
        if current is None:
            channels[channel] = evidence
            continue
        if channel == "stable" and evidence["branch"] == "main":
            channels[channel] = evidence
        elif channel == "dev" and evidence["branch"] == "dev":
            channels[channel] = evidence

    primary = channels.get("stable") or channels.get("dev") or next(iter(channels.values()))
    return {
        "status": "verified",
        "channels": channels,
        "evidence": primary,
    }
