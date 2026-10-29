"""Minimal stdlib HTTP helpers — replaces requests for pipeline use."""
import json
import os
import time
import urllib.error
import urllib.request


def get_auth_headers(url="", extra=None):
    """Return headers with User-Agent and optional GITHUB_TOKEN auth."""
    headers = dict(extra) if extra else {}
    headers.setdefault("User-Agent", "FreshMorphe/1.0")
    parsed = urllib.request.urlsplit(url)
    is_github = any(h in (parsed.hostname or "") for h in ("github.com", "githubusercontent.com"))
    if is_github:
        token = os.environ.get("GITHUB_TOKEN")
        if token and "Authorization" not in headers:
            headers["Authorization"] = f"token {token}"
    return headers


def fetch_url(url, headers=None, timeout=30, as_json=False, binary=False):
    """Fetch a URL with retries. Returns str, bytes, or parsed JSON."""
    req_headers = get_auth_headers(url, headers)
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if binary:
                    return data
                text = data.decode("utf-8")
                return json.loads(text) if as_json else text
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(1)
    raise last_err or RuntimeError(f"Failed to fetch {url}")


def http_get_status(url, headers=None, timeout=15):
    """Return HTTP status code for a URL (lightweight check)."""
    req_headers = get_auth_headers(url, headers)
    try:
        req = urllib.request.Request(url, headers=req_headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0
