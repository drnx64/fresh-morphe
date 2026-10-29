"""Shared GitHub/GitLab helpers for repository discovery and metadata."""

import contextlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException

DEFAULT_TIMEOUT = 15
DEFAULT_BRANCHES = ("main", "master", "dev")
UNAVAILABLE_HTTP_CODES = (404, 451)
DEAD_LINK_HTTP_CODES = (404, 410)

_REPO_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:github|gitlab)\.com/[^\s\"'<>\\]+",
    re.IGNORECASE,
)
_TRAILING_URL_PUNCTUATION = ".,);]}>"


def is_http_url(value):
    if not isinstance(value, str):
        return False
    parsed = urllib.parse.urlsplit(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def parse_repo_url(repo_url):
    """Return (platform, full_name) for a GitHub/GitLab repository URL."""
    if not isinstance(repo_url, str) or not repo_url.strip():
        return None, None

    value = repo_url.strip()
    if "://" not in value:
        value = f"https://{value}"

    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return None, None

    host = (parsed.hostname or "").lower()
    raw_path = parsed.path.strip("/")
    path = urllib.parse.unquote(raw_path)
    if not path:
        return None, None

    if host in ("github.com", "www.github.com", "api.github.com"):
        if host == "api.github.com" and path.startswith("repos/"):
            path = path.removeprefix("repos/")
        owner, separator, remainder = path.partition("/")
        repo = remainder.split("/", 1)[0].removesuffix(".git")
        if owner and separator and repo:
            return "github", f"{owner}/{repo}"
        return None, None

    if host in ("gitlab.com", "www.gitlab.com"):
        if raw_path.startswith("api/v4/projects/"):
            encoded = raw_path.removeprefix("api/v4/projects/").split("/", 1)[0]
            project = urllib.parse.unquote(encoded)
        else:
            project = path.split("/-/", 1)[0].removesuffix(".git")
        project = project.strip("/")
        parts = project.split("/")
        if len(parts) >= 2 and all(parts):
            return "gitlab", project
    return None, None


def build_repo_url(platform, repo):
    if platform not in ("github", "gitlab") or not repo:
        return ""
    return f"https://{platform}.com/{repo.strip('/')}"


def build_raw_url(platform, repo, branch, file_path):
    repo = str(repo or "").strip("/")
    branch = str(branch or "").strip("/")
    file_path = str(file_path or "").strip("/")
    if not repo or not branch or not file_path:
        return ""
    if platform == "github":
        return f"https://raw.githubusercontent.com/{repo}/{branch}/{file_path}"
    if platform == "gitlab":
        encoded_repo = urllib.parse.quote(repo, safe="")
        encoded_file = urllib.parse.quote(file_path, safe="")
        return (
            f"https://gitlab.com/api/v4/projects/{encoded_repo}"
            f"/repository/files/{encoded_file}/raw?ref={branch}"
        )
    return ""


def repo_key(platform, repo):
    if not platform or not repo:
        return ""
    return f"{platform}:{repo.strip('/').lower()}"


def repo_key_from_url(repo_url):
    platform, repo = parse_repo_url(repo_url)
    return repo_key(platform, repo)


def _set_query_param(url, pattern, param):
    if re.search(pattern, url):
        return re.sub(pattern, rf"\1{param}", url)
    return f"{url}&{param}" if "?" in url else f"{url}?{param}"


def normalize_image_url(url, size=128):
    if not url or not isinstance(url, str):
        return ""
    if url.startswith("/"):
        url = f"https://gitlab.com{url}"
    if url.startswith("//"):
        url = f"https:{url}"
    if "googleusercontent.com" in url or "ggpht.com" in url:
        return re.sub(r"=(?:s|w)\d+.*$", "", url) + f"=s{size}-rw"
    if "avatars.githubusercontent.com" in url or "gravatar.com" in url:
        return _set_query_param(url, r"([?&])(?:size|s)=\d+", f"s={size}")
    if "gitlab.com" in url:
        return _set_query_param(url, r"([?&])width=\d+", f"width={size}")
    return url


def get_auth_headers(url, headers=None):
    result = dict(headers or {})
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    is_github = host == "github.com" or host.endswith(".github.com") or host.endswith(
        ".githubusercontent.com"
    )
    if "User-Agent" not in result:
        result["User-Agent"] = "FreshMorphe/1.0"

    token = os.environ.get("GITHUB_TOKEN", "")
    is_release_download = (
        "/releases/download/" in parsed.path or host == "objects.githubusercontent.com"
    )
    if (
        token
        and is_github
        and parsed.scheme == "https"
        and not is_release_download
        and "Authorization" not in result
    ):
        result["Authorization"] = f"Bearer {token}"
    return result


def fetch(
    url,
    headers=None,
    timeout=DEFAULT_TIMEOUT,
    as_json=False,
    binary=False,
    method=None,
):
    request_headers = get_auth_headers(url, headers)
    if method:
        request = urllib.request.Request(url, headers=request_headers, method=method)
    else:
        request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content = response.read()
        if binary:
            return content
        decoded = content.decode("utf-8-sig")
        return json.loads(decoded) if as_json else decoded


def format_request_error(error):
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    return str(reason) or error.__class__.__name__


def check_link_status(url, timeout=5):
    try:
        request = urllib.request.Request(url, headers=get_auth_headers(url))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl().rstrip("/")
            if final_url.lower() != url.rstrip("/").lower():
                return {"is_redirect": True, "final_url": final_url}
            return {"is_active": True}
    except urllib.error.HTTPError as error:
        if error.code in DEAD_LINK_HTTP_CODES:
            return {"is_dead": True, "error": f"HTTP {error.code}"}
        return {"error": f"HTTP {error.code}"}
    except (OSError, HTTPException, ValueError) as error:
        return {"error": str(error)}


def _iter_repo_urls(value):
    if isinstance(value, dict):
        if value.get("disabled") is True or value.get("enabled") is False:
            return
        for nested in value.values():
            yield from _iter_repo_urls(nested)
        return
    if isinstance(value, list):
        for nested in value:
            yield from _iter_repo_urls(nested)
        return
    if not isinstance(value, str):
        return
    for match in _REPO_URL_RE.finditer(value):
        yield match.group(0).rstrip(_TRAILING_URL_PUNCTUATION)


def find_repo_urls(value):
    """Collect unique repository URLs from arbitrary provider JSON/text."""
    seen = set()
    results = []
    for url in _iter_repo_urls(value):
        platform, repo = parse_repo_url(url)
        if not platform or not repo:
            continue
        normalized = build_repo_url(platform, repo)
        key = repo_key(platform, repo)
        if key in seen:
            continue
        seen.add(key)
        results.append({"platform": platform, "repo": repo, "url": normalized})
    return results


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    return default if default is not None else {}
