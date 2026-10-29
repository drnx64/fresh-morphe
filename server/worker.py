"""Incremental SQLite ingestion worker for the phone-hosted tracker.

The worker reads the awesome-morphe mirror directly, compares the upstream
branch hash with ``repo_sources``, and only rewrites bundles whose patch
manifest changed. It never deletes legacy ``latest`` rows and it suppresses
changelog entries on the first full bootstrap.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlparse

from server.api import build_detail
from server.config import DB_PATH
from server.db import (
    connect,
    get_meta,
    get_snapshot,
    init_db,
    json_dumps,
    set_meta,
    set_snapshot,
    table_counts,
    utc_now,
)
from server.import_legacy import ensure_repo, normalize_url, upsert_bundle, upsert_release
from server.notify import DiscordNotifier


DEFAULT_SOURCE_BASE = (
    os.environ.get("FRESH_SOURCE_BASE")
    or os.environ.get("MORPHE_SOURCE_BASE")
    or "https://raw.githubusercontent.com/nvbangg/awesome-morphe/main/"
)
WORKER_VERSION = "1"
BOOTSTRAP_META = "worker_bootstrap_done"
USER_AGENT = "FreshMorpheWorker/1.0"

BRANCH_CHANNELS = (("main", "stable"), ("dev", "dev"))
GENERIC_BUNDLE_NAMES = frozenset({
    "morphe", "morphe-patches", "patches", "patch", "revanced", "revanced-patches",
})
GENERIC_PACKAGE_SEGMENTS = frozenset({
    "android", "app", "apps", "application", "mobile", "client", "free",
    "pro", "lite", "plus", "hd", "tv", "beta", "release", "demo", "premium",
    "core", "official", "web", "api", "lib", "libs", "common", "base",
    "test", "tests",
})
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
WHITESPACE_RE = re.compile(r"\s+")


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Target:
    url: str
    url_key: str
    branch: str
    channel: str
    digest: str
    meta: dict
    slug: str


@dataclass
class FetchResult:
    target: Target
    patches: dict
    release: dict


def utc_stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def as_stars(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_repo_url(url):
    value = normalize_url(url)
    parts = urlparse(value)
    host = (parts.hostname or "").lower()
    path = [part for part in (parts.path or "").split("/") if part]
    if len(path) < 2:
        return "", "", ""
    if host == "github.com" or host.endswith(".github.com"):
        platform = "github"
    elif host == "gitlab.com" or host.endswith(".gitlab.com"):
        platform = "gitlab"
    else:
        platform = host.split(".")[0] if host else ""
    owner = path[0]
    repo = path[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return platform, owner, repo


def mirror_slug(url, branch):
    platform, owner, repo = parse_repo_url(url)
    if not platform or not owner or not repo:
        return ""
    return f"{platform}~{owner}~{repo}~{branch}"


def derive_bundle_name(url):
    _, owner, repo = parse_repo_url(url)
    raw = repo.lower().strip()
    if not raw:
        return owner.lower()
    if raw in GENERIC_BUNDLE_NAMES:
        return owner.lower()
    for suffix in ("-patches", "-patch"):
        if raw.endswith(suffix) and len(raw) > len(suffix):
            raw = raw[: -len(suffix)]
            break
    if raw in GENERIC_BUNDLE_NAMES or not raw:
        return owner.lower()
    return raw


def derive_name_from_package(package_name):
    raw = package_name if isinstance(package_name, str) else ""
    raw = raw.strip()
    if not raw:
        return "Unknown app"
    parts = [part for part in raw.split(".") if part]
    if not parts:
        return "Unknown app"
    candidates = parts[1:] if len(parts) > 1 else parts
    meaningful = [part for part in candidates if part.lower() not in GENERIC_PACKAGE_SEGMENTS]
    pick = meaningful[-1] if meaningful else parts[-1]
    words = []
    for chunk in re.split(r"[_\-\s]+", pick):
        if chunk:
            words.extend(part for part in CAMEL_RE.sub(" ", chunk).split(" ") if part)
    name = WHITESPACE_RE.sub(" ", " ".join(word[:1].upper() + word[1:] for word in words)).strip()
    return name or "Unknown app"


def normalize_targets(values):
    targets = []
    seen = set()
    for raw in values or []:
        versions = []
        version_code = ""
        min_sdk = None
        experimental = False
        if isinstance(raw, str):
            versions = [raw]
        elif isinstance(raw, dict):
            version = raw.get("version")
            if isinstance(version, list):
                versions = [str(item) for item in version if item is not None]
            elif version not in (None, ""):
                versions = [str(version)]
            version_codes = raw.get("versionCodes") or raw.get("version_codes") or []
            if not isinstance(version_codes, list):
                version_codes = [version_codes]
            if not versions and version_codes:
                versions = [""]
            for version_value in versions:
                codes = version_codes or [""]
                for code in codes:
                    code_str = str(code or "")
                    key = (version_value, code_str)
                    if key in seen:
                        continue
                    seen.add(key)
                    target = {
                        "version": version_value,
                        "version_code": code_str,
                        "min_sdk": raw.get("minSdk", raw.get("min_sdk")),
                        "experimental": bool(raw.get("isExperimental", raw.get("experimental"))),
                    }
                    targets.append(target)
            continue
        else:
            continue
        for version_value in versions:
            key = (str(version_value), "")
            if key in seen:
                continue
            seen.add(key)
            targets.append({
                "version": str(version_value),
                "version_code": "",
                "min_sdk": min_sdk,
                "experimental": experimental,
            })
    return targets


def patch_packages(patch):
    compatibility = {}
    for item in patch.get("compatibility") or []:
        if not isinstance(item, dict):
            continue
        package = str(item.get("packageName") or item.get("package") or "").lower()
        if package:
            compatibility[package] = item
    packages = []
    compatible_packages = patch.get("compatiblePackages")
    if isinstance(compatible_packages, dict):
        for package, value in compatible_packages.items():
            package_key = str(package or "").strip()
            if not package_key:
                continue
            entry = dict(compatibility.get(package_key.lower()) or {})
            entry["packageName"] = package_key
            if isinstance(value, dict):
                entry.update(value)
            elif isinstance(value, list):
                entry.setdefault("versions", value)
            packages.append(entry)
    elif isinstance(compatible_packages, list):
        for value in compatible_packages:
            if not isinstance(value, dict):
                continue
            entry = dict(value)
            package_key = str(entry.get("packageName") or entry.get("package") or "").lower()
            extra = compatibility.get(package_key)
            if extra:
                for key in ("name", "targets", "versions"):
                    if key not in entry and extra.get(key) is not None:
                        entry[key] = extra[key]
            packages.append(entry)
    if not packages and patch.get("packageName"):
        packages = [patch]
    if not packages and compatibility:
        packages = list(compatibility.values())
    return packages


def parse_patches_doc(doc, old_detail=None):
    old_names = {
        str(app.get("package") or "").lower(): app.get("app_name") or ""
        for app in (old_detail or {}).get("apps", [])
    }
    app_names = {
        str(key or "").lower(): str(value or "")
        for key, value in ((doc.get("appNames") if isinstance(doc, dict) else {}) or {}).items()
    }
    patches = doc if isinstance(doc, list) else (doc or {}).get("patches") or []
    apps = {}
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        patch_name = str(patch.get("name") or "").strip()
        if not patch_name:
            continue
        options = patch.get("options") or []
        if not isinstance(options, list):
            options = []
        normalized_options = [
            {
                "key": str(option.get("key") or ""),
                "description": str(option.get("description") or ""),
            }
            for option in options
            if isinstance(option, dict)
        ]
        for compatible in patch_packages(patch):
            if not isinstance(compatible, dict):
                continue
            package = str(compatible.get("packageName") or compatible.get("package") or "").strip()
            if not package:
                continue
            package_key = package.lower()
            app = apps.setdefault(package_key, {
                "app_name": old_names.get(package_key) or app_names.get(package_key) or "",
                "package": package_key,
                "patches": [],
            })
            friendly = str(compatible.get("name") or "").strip()
            if friendly and friendly.lower() != package_key:
                app["app_name"] = friendly
            target_values = (
                compatible.get("targets")
                or compatible.get("versions")
                or patch.get("targets")
                or []
            )
            targets = normalize_targets(target_values)
            versions = sorted({target["version"] for target in targets if target["version"]})
            app["patches"].append({
                "name": patch_name,
                "description": str(patch.get("description") or ""),
                "use": bool(patch.get("use")),
                "options": normalized_options,
                "compatible_versions": versions,
                "targets": targets,
            })
    for app in apps.values():
        if not app["app_name"]:
            app["app_name"] = app_names.get(app["package"]) or derive_name_from_package(app["package"])
    return [apps[key] for key in sorted(apps)]


def canonical_apps(apps):
    canonical = []
    for app in apps:
        canonical_patches = []
        for patch in app.get("patches") or []:
            opts = [
                {
                    "key": str(option.get("key") or ""),
                    "description": str(option.get("description") or ""),
                }
                for option in (patch.get("options") or [])
                if isinstance(option, dict)
            ]
            opts.sort(key=lambda item: (item["key"], item["description"]))
            versions = sorted(str(version) for version in (patch.get("compatible_versions") or []))
            canonical_patches.append({
                "name": str(patch.get("name") or ""),
                "description": str(patch.get("description") or ""),
                "use": bool(patch.get("use")),
                "options": opts,
                "compatible_versions": versions,
            })
        canonical_patches.sort(key=lambda item: item["name"])
        canonical.append({
            "package": str(app.get("package") or "").lower().strip(),
            "app_name": str(app.get("app_name") or ""),
            "patches": canonical_patches,
        })
    canonical.sort(key=lambda item: item["package"])
    return canonical


def compute_fingerprint(bundle_name, apps, channel):
    payload = json.dumps({
        "bundle_name": bundle_name,
        "channel": channel,
        "apps": canonical_apps(apps),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apps_are_different(old_app, new_app):
    def normalized(app):
        patches = []
        for patch in app.get("patches") or []:
            opts = [
                {
                    "key": str(option.get("key") or ""),
                    "description": str(option.get("description") or ""),
                }
                for option in (patch.get("options") or [])
                if isinstance(option, dict)
            ]
            opts.sort(key=lambda item: (item["key"], item["description"]))
            versions = sorted(str(version) for version in (patch.get("compatible_versions") or []))
            patches.append({
                "name": str(patch.get("name") or ""),
                "description": str(patch.get("description") or ""),
                "use": bool(patch.get("use")),
                "options": opts,
                "compatible_versions": versions,
            })
        patches.sort(key=lambda item: item["name"])
        return {"app_name": str(app.get("app_name") or ""), "patches": patches}
    return normalized(old_app) != normalized(new_app)


def patch_diff_details(old_patch, new_patch):
    changes = []
    if old_patch.get("description") != new_patch.get("description"):
        changes.append("description updated")
    if old_patch.get("use") != new_patch.get("use"):
        old_use = old_patch.get("use", True)
        new_use = new_patch.get("use", True)
        changes.append("enabled" if new_use and not old_use else "disabled")
    old_opts = {
        option.get("key", ""): option.get("description", "")
        for option in (old_patch.get("options") or [])
    }
    new_opts = {
        option.get("key", ""): option.get("description", "")
        for option in (new_patch.get("options") or [])
    }
    if old_opts != new_opts:
        added = [key for key in new_opts if key not in old_opts]
        removed = [key for key in old_opts if key not in new_opts]
        changed = [
            key for key in old_opts
            if key in new_opts and old_opts[key] != new_opts[key]
        ]
        parts = []
        if added:
            parts.append("options added: " + ", ".join(sorted(added)))
        if removed:
            parts.append("options removed: " + ", ".join(sorted(removed)))
        if changed:
            parts.append("options changed: " + ", ".join(sorted(changed)))
        changes.extend(parts)
    old_versions = sorted(str(version) for version in (old_patch.get("compatible_versions") or []))
    new_versions = sorted(str(version) for version in (new_patch.get("compatible_versions") or []))
    if old_versions != new_versions:
        added = [version for version in new_versions if version not in old_versions]
        removed = [version for version in old_versions if version not in new_versions]
        parts = []
        if added:
            parts.append("versions added: " + ", ".join(added))
        if removed:
            parts.append("versions removed: " + ", ".join(removed))
        changes.extend(parts)
    return changes


def patch_signature(patch):
    options = [
        {
            "key": str(option.get("key") or ""),
            "description": str(option.get("description") or ""),
        }
        for option in (patch.get("options") or [])
        if isinstance(option, dict)
    ]
    options.sort(key=lambda item: (item["key"], item["description"]))
    return {
        "name": str(patch.get("name") or ""),
        "description": str(patch.get("description") or ""),
        "use": bool(patch.get("use")),
        "options": options,
        "compatible_versions": sorted(
            str(version) for version in (patch.get("compatible_versions") or [])
        ),
    }


def compute_patch_diff(old_app, new_app):
    old_patches = {
        patch.get("name", ""): patch_signature(patch)
        for patch in (old_app.get("patches") or [])
    }
    new_patches = {
        patch.get("name", ""): patch_signature(patch)
        for patch in (new_app.get("patches") or [])
    }
    old_names = set(old_patches)
    new_names = set(new_patches)

    def enrich(names, source):
        return [
            {"name": name, "description": source.get(name, {}).get("description", "")}
            for name in names
        ]

    modified = sorted(name for name in (old_names & new_names) if old_patches[name] != new_patches[name])
    return {
        "patches_added": enrich(sorted(new_names - old_names), new_patches),
        "patches_removed": enrich(sorted(old_names - new_names), old_patches),
        "patches_modified": [
            {
                "name": name,
                "description": new_patches.get(name, {}).get("description", ""),
                **(
                    {"previous_description": old_patches[name].get("description", "")}
                    if old_patches[name].get("description", "") != new_patches[name].get("description", "")
                    else {}
                ),
                "changes": patch_diff_details(old_patches[name], new_patches[name]),
            }
            for name in modified
        ],
    }


def release_tag_for(version, download_url):
    path = urlparse(download_url or "").path
    parts = [part for part in path.split("/") if part]
    if "download" in parts:
        index = parts.index("download")
        if index + 1 < len(parts):
            return parts[index + 1]
    if version:
        return version if str(version).startswith("v") else f"v{version}"
    if parts:
        name = parts[-1]
        return name[:-4] if name.endswith(".mpp") else name
    return ""


def build_record(bundle, channel, target, patches_doc, release_doc, old_detail=None):
    old_detail = old_detail or {}
    release_doc = release_doc or {}
    apps = parse_patches_doc(patches_doc, old_detail)
    patches_version = patches_doc.get("version") if isinstance(patches_doc, dict) else ""
    version = (
        str(patches_version or "")
        or str(release_doc.get("version") or "")
        or str(old_detail.get("version") or "")
    )
    release_notes = (
        str(release_doc.get("description") or "")
        or str(old_detail.get("release_notes") or "")
        or str(old_detail.get("description") or "")
    )
    created_at = (
        str(release_doc.get("created_at") or "")
        or str(old_detail.get("created_at") or "")
        or utc_stamp()
    )
    tag = release_tag_for(version, release_doc.get("download_url") or "")
    if not tag:
        tag = str(old_detail.get("release_tag") or "")
    repo_name = str(target.meta.get("name") or "")
    description = release_notes or str(target.meta.get("description") or "") or str(old_detail.get("description") or "")
    prerelease = channel == "dev" or any(
        marker in version.lower() for marker in ("-dev", "-beta", "-alpha", "-pre")
    )
    return {
        "bundle": bundle,
        "channel": channel,
        "repo_url": target.url,
        "patches_name": repo_name or str(old_detail.get("patches_name") or ""),
        "version": version,
        "release_tag": tag,
        "release_date": created_at,
        "created_at": created_at,
        "description": description,
        "release_notes": release_notes,
        "fingerprint": compute_fingerprint(bundle, apps, channel),
        "isPreRelease": prerelease,
        "apps": apps,
        "avatarUrl": str(target.meta.get("avatarUrl") or ""),
        "bundleImageUrl": "",
    }


def build_change(bundle, channel, old_detail, new_record, dev_packages=None):
    old_detail = old_detail or {}
    dev_packages = dev_packages or set()
    if not old_detail:
        apps = [
            {
                "app_name": app.get("app_name") or derive_name_from_package(app.get("package", "")),
                "package": app.get("package", ""),
                "badge_type": "NEW APP",
            }
            for app in new_record.get("apps") or []
        ]
        return {
            "bundle": bundle,
            "channel": channel,
            "badge_type": "NEW BUNDLE",
            "apps": apps,
            "repo_url": new_record.get("repo_url", ""),
            "patches_name": new_record.get("patches_name", ""),
            "previous_version": "",
            "new_version": new_record.get("version", ""),
            "avatarUrl": new_record.get("avatarUrl", ""),
            "bundleImageUrl": new_record.get("bundleImageUrl", ""),
        }
    if old_detail.get("fingerprint") == new_record.get("fingerprint"):
        return None

    old_apps = {
        str(app.get("package") or "").lower(): app
        for app in old_detail.get("apps") or []
    }
    new_apps = {
        str(app.get("package") or "").lower(): app
        for app in new_record.get("apps") or []
    }
    changed = []
    for package, app in new_apps.items():
        if package not in old_apps:
            if channel == "stable" and package in dev_packages:
                changed.append({
                    "app_name": app.get("app_name") or derive_name_from_package(package),
                    "package": package,
                    "badge_type": "UPDATED APP",
                    "promoted_from": "dev",
                })
            else:
                changed.append({
                    "app_name": app.get("app_name") or derive_name_from_package(package),
                    "package": package,
                    "badge_type": "NEW APP",
                })
        elif apps_are_different(old_apps[package], app):
            patch_diff = compute_patch_diff(old_apps[package], app)
            total_changes = sum(len(patch_diff.get(key, [])) for key in (
                "patches_added", "patches_removed", "patches_modified",
            ))
            changed.append({
                "app_name": app.get("app_name") or derive_name_from_package(package),
                "package": package,
                "badge_type": "MAJOR UPDATE" if total_changes >= 5 else "UPDATED APP",
                "patch_diff": patch_diff,
            })
    for package, app in old_apps.items():
        if package not in new_apps:
            changed.append({
                "app_name": app.get("app_name") or derive_name_from_package(package),
                "package": package,
                "badge_type": "REMOVED APP",
            })
    if not changed:
        return None

    old_version = old_detail.get("version", "")
    new_version = new_record.get("version", "")
    extra = []
    if old_version and new_version and old_version != new_version:
        extra.append("VERSION BUMP")
    return {
        "bundle": bundle,
        "channel": channel,
        "badge_type": "UPDATED",
        "apps": changed,
        "repo_url": new_record.get("repo_url", ""),
        "patches_name": new_record.get("patches_name", ""),
        "extra_badges": extra,
        "previous_version": old_version,
        "new_version": new_version,
        "avatarUrl": new_record.get("avatarUrl", ""),
        "bundleImageUrl": new_record.get("bundleImageUrl", ""),
    }


def summarize_changes(changes):
    """Compact bundle-level summary for notifications and job stats."""
    summary = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        apps = change.get("apps") or []
        summary.append({
            "bundle": change.get("bundle", ""),
            "channel": change.get("channel", ""),
            "badge_type": change.get("badge_type", ""),
            "app_count": len(apps) if isinstance(apps, list) else 0,
            "previous_version": change.get("previous_version", ""),
            "new_version": change.get("new_version", ""),
            "extra_badges": list(change.get("extra_badges") or []),
        })
    return summary


def notify_safely(notifier, method, *args, **kwargs):
    """Notifications are best-effort and must never fail the worker."""
    if notifier is None:
        return None
    try:
        return getattr(notifier, method)(*args, **kwargs)
    except Exception as exc:
        print(f"[worker] notification failed: {exc}", flush=True)
        return None


def existing_bundle_rows(conn, repo_id):
    return conn.execute(
        "SELECT bundle, channel FROM bundles WHERE repo_id = ? ORDER BY bundle, channel",
        (repo_id,),
    ).fetchall()


def choose_bundle_name(conn, repo_id, target):
    rows = existing_bundle_rows(conn, repo_id) if repo_id else []
    names = sorted({row["bundle"] for row in rows if row["bundle"]})
    if names:
        _, owner, repo = parse_repo_url(target.url)
        candidates = [derive_bundle_name(target.url), owner.lower(), f"{owner}-{repo}".lower()]
        for candidate in candidates:
            for name in names:
                if name.lower() == candidate:
                    return name
        if len(names) == 1:
            return names[0]
        scores = {}
        for row in rows:
            scores[row["bundle"]] = scores.get(row["bundle"], 0) + {
                "stable": 2, "dev": 1, "latest": 0,
            }.get(row["channel"], 0)
        return sorted(names, key=lambda name: (-scores.get(name, 0), name.lower()))[0]

    used = {
        row["bundle"].lower()
        for row in conn.execute("SELECT DISTINCT bundle FROM bundles")
        if row["bundle"]
    }
    base = derive_bundle_name(target.url)
    _, owner, repo = parse_repo_url(target.url)
    if base.lower() not in used:
        return base
    for candidate in (f"{owner}-{base}", f"{owner}-{repo}", f"{base}-{owner}"):
        if candidate.lower() not in used:
            return candidate.lower()
    suffix = 2
    while f"{base}-{suffix}".lower() in used:
        suffix += 1
    return f"{base}-{suffix}".lower()


def merge_changes(conn, affected, now):
    date = now[:10]
    row = conn.execute(
        "SELECT payload_json FROM changes WHERE date = ?", (date,)
    ).fetchone()
    if row:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            payload = {}
    else:
        payload = {}
    payload["date"] = date
    payload["lastChecked"] = now
    existing = {
        f'{entry.get("bundle")}:{entry.get("channel")}': entry
        for entry in payload.get("affected_bundles") or []
        if isinstance(entry, dict)
    }
    for entry in affected:
        if not isinstance(entry, dict):
            continue
        key = f'{entry.get("bundle")}:{entry.get("channel")}'
        old_entry = existing.get(key)
        if old_entry:
            badges = list(old_entry.get("extra_badges") or [])
            for badge in entry.get("extra_badges") or []:
                if badge not in badges:
                    badges.append(badge)
            entry = dict(entry)
            entry["extra_badges"] = badges
        existing[key] = entry
    payload["affected_bundles"] = list(existing.values())
    conn.execute(
        "INSERT INTO changes(date, last_checked, payload_json) VALUES (?, ?, ?) "
        "ON CONFLICT(date) DO UPDATE SET last_checked = excluded.last_checked, payload_json = excluded.payload_json",
        (date, now, json_dumps(payload)),
    )
    return payload


def today_change_payload(conn, now):
    """Return the accumulated changelog payload for the UTC day containing ``now``.

    Snapshots must describe the whole day, not the current run's delta: a scan
    that finds nothing new used to write an empty ``affected_bundles`` list and
    blank the frontend's "What's New" section until the next pipeline import.
    """
    date = now[:10]
    row = conn.execute(
        "SELECT payload_json FROM changes WHERE date = ?", (date,)
    ).fetchone()
    payload = {}
    if row:
        try:
            loaded = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            loaded = {}
        if isinstance(loaded, dict):
            payload = loaded
    payload.setdefault("date", date)
    if not isinstance(payload.get("affected_bundles"), list):
        payload["affected_bundles"] = []
    return payload


def make_snapshots(conn, now):
    counts = table_counts(conn)
    today = now[:10]
    row = conn.execute("SELECT payload_json FROM changes WHERE date = ?", (today,)).fetchone()
    try:
        today_payload = json.loads(row["payload_json"]) if row else {}
    except (TypeError, ValueError):
        today_payload = {}
    affected = today_payload.get("affected_bundles") or []
    new_apps = {
        app.get("package")
        for entry in affected
        for app in entry.get("apps") or []
        if app.get("badge_type") == "NEW APP" and app.get("package")
    }
    new_bundles = {
        entry.get("bundle")
        for entry in affected
        if entry.get("badge_type") == "NEW BUNDLE" and entry.get("bundle")
    }

    core = get_snapshot(conn, "core", {}) or {}
    core.update({"date": today, "last_run": now, "lastChecked": now})
    set_snapshot(conn, "core", core)

    stats = get_snapshot(conn, "stats", {}) or {}
    stats.update({
        "total_bundles": conn.execute("SELECT COUNT(DISTINCT bundle) FROM bundles").fetchone()[0],
        "total_apps": counts.get("apps", 0),
        "new_apps_today": len(new_apps),
        "new_bundles_today": len(new_bundles),
    })
    set_snapshot(conn, "stats", stats)

    app_cache = get_snapshot(conn, "app_cache", {}) or {}
    for row in conn.execute("SELECT package, name, icon_url FROM apps"):
        entry = dict(app_cache.get(row["package"]) or {})
        entry["name"] = row["name"] or entry.get("name") or derive_name_from_package(row["package"])
        entry["icon_url"] = row["icon_url"] or entry.get("icon_url") or ""
        app_cache[row["package"]] = entry
    set_snapshot(conn, "app_cache", app_cache)

    repo_cache = get_snapshot(conn, "repo_cache", {}) or {}
    for row in conn.execute(
        "SELECT url, stars, avatar_url, description, is_archived, is_fork, "
        "bundle_image_url, fetched_at FROM repos"
    ):
        entry = dict(repo_cache.get(row["url"]) or {})
        entry.update({
            "stars": row["stars"],
            "avatarUrl": row["avatar_url"],
            "repoDescription": row["description"],
            "isArchived": bool(row["is_archived"]),
            "isFork": bool(row["is_fork"]),
            "bundleImageUrl": row["bundle_image_url"],
            "fetched_at": row["fetched_at"],
        })
        repo_cache[row["url"]] = entry
    set_snapshot(conn, "repo_cache", repo_cache)

    digest = hashlib.sha256()
    for row in conn.execute(
        "SELECT bundle, channel, version, fingerprint, release_tag FROM bundles ORDER BY bundle, channel"
    ):
        digest.update(
            f'{row["bundle"]}:{row["channel"]}|{row["version"]}|{row["fingerprint"]}|{row["release_tag"]}|'.encode("utf-8")
        )
    digest.update(
        f'|releases={counts.get("releases", 0)}|patches={counts.get("patches", 0)}'.encode("utf-8")
    )
    data_version = digest.hexdigest()[:16]

    if not get_meta(conn, "imported_at"):
        set_meta(conn, "imported_at", now)
    set_meta(conn, "schema_version", "1")
    set_meta(conn, "source", "sqlite-worker")
    set_meta(conn, "worker_version", WORKER_VERSION)
    set_meta(conn, "generated_at", now)
    set_meta(conn, "last_success_at", now)
    set_meta(conn, "last_checked", now)
    set_meta(conn, "data_version", data_version)
    set_meta(conn, "bundle_count", str(counts.get("bundles", 0)))
    set_meta(conn, "repo_count", str(counts.get("repos", 0)))
    set_meta(conn, "app_count", str(counts.get("apps", 0)))
    set_meta(conn, "bundle_app_count", str(counts.get("bundle_apps", 0)))
    set_meta(conn, "patch_count", str(counts.get("patches", 0)))
    set_meta(conn, "release_count", str(counts.get("releases", 0)))
    set_meta(
        conn,
        "latest_count",
        str(conn.execute("SELECT COUNT(*) FROM bundles WHERE channel = 'latest'").fetchone()[0]),
    )
    return data_version, counts


class Worker:
    def __init__(self, db_path, source_base=DEFAULT_SOURCE_BASE, fetcher=None, timeout=30):
        self.db_path = os.path.abspath(db_path)
        self.source_base = source_base.rstrip("/") + "/"
        self.fetcher = fetcher or self._http_fetch
        self.timeout = timeout

    def _url(self, path):
        return urljoin(self.source_base, path)

    def _http_fetch(self, url):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8"))

    def fetch_json(self, path, missing_ok=False):
        url = self._url(path)
        try:
            value = self.fetcher(url)
        except urllib.error.HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            raise FetchError(f"{exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            raise FetchError(f"{exc.reason} for {url}") from exc
        except (OSError, ValueError) as exc:
            raise FetchError(f"{exc} for {url}") from exc
        if value is None:
            if missing_ok:
                return None
            raise FetchError(f"empty response for {url}")
        if not isinstance(value, (dict, list)):
            raise FetchError(f"unexpected JSON for {url}")
        return value

    def build_targets(self, repos_doc):
        targets = []
        seen = set()
        for raw_url, meta in sorted((repos_doc or {}).items(), key=lambda item: str(item[0])):
            if not isinstance(meta, dict):
                continue
            url = normalize_url(raw_url)
            if not url:
                continue
            slug_url = url.lower()
            if slug_url in seen:
                continue
            seen.add(slug_url)
            for branch, channel in BRANCH_CHANNELS:
                digest = str(meta.get(branch) or "").strip()
                if not digest:
                    continue
                slug = mirror_slug(url, branch)
                if not slug:
                    continue
                targets.append(Target(
                    url=url,
                    url_key=slug_url,
                    branch=branch,
                    channel=channel,
                    digest=digest,
                    meta=meta,
                    slug=slug,
                ))
        return targets

    def load_hashes(self, conn):
        rows = conn.execute("SELECT url_key, branch, hash FROM repo_sources")
        return {(row["url_key"], row["branch"]): row["hash"] for row in rows}

    def fetch_target(self, target):
        patches = self.fetch_json(f"data/patches/{quote(target.slug, safe='~')}.json")
        release = self.fetch_json(f"data/bundles/{quote(target.slug, safe='~')}.json", missing_ok=True)
        return FetchResult(target, patches, release or {})

    def process(self, conn, result, now, bootstrap, dry_run=False):
        target = result.target
        if not dry_run:
            cache_entry = {
                "name": target.meta.get("name") or "",
                "repoDescription": target.meta.get("description") or "",
                "avatarUrl": target.meta.get("avatarUrl") or "",
                "stars": as_stars(target.meta.get("stars")),
                "fetched_at": time.time(),
            }
            with conn:
                repo_id = ensure_repo(conn, target.url, cache_entry=cache_entry)
                conn.execute(
                    "UPDATE repos SET stars = ?, fetched_at = ? WHERE id = ?",
                    (as_stars(target.meta.get("stars")), time.time(), repo_id),
                )
                bundle = choose_bundle_name(conn, repo_id, target)
                old_detail = build_detail(conn, bundle, target.channel)
                old_dev = build_detail(conn, bundle, "dev") if target.channel == "stable" else None
                record = build_record(bundle, target.channel, target, result.patches, result.release, old_detail)
                upsert_bundle(conn, repo_id, bundle, target.channel, record, {
                    "version": record["version"],
                    "release_tag": record["release_tag"],
                    "release_date": record["release_date"],
                    "patches_name": record["patches_name"],
                    "isPreRelease": record["isPreRelease"],
                }, now)
                if record["release_tag"]:
                    upsert_release(
                        conn,
                        repo_id,
                        record["release_tag"],
                        body=record["release_notes"] or record["description"],
                        prerelease=record["isPreRelease"],
                        published_at=record["release_date"],
                        fetched_at=time.time(),
                    )
                conn.execute(
                    "INSERT INTO repo_sources(url_key, branch, hash, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(url_key, branch) DO UPDATE SET hash = excluded.hash, updated_at = excluded.updated_at",
                    (target.url_key, target.branch, target.digest, now),
                )
        else:
            repo_row = conn.execute(
                "SELECT id FROM repos WHERE url_key = ?", (target.url_key,)
            ).fetchone()
            repo_id = repo_row["id"] if repo_row else None
            bundle = choose_bundle_name(conn, repo_id, target)
            old_detail = build_detail(conn, bundle, target.channel)
            old_dev = build_detail(conn, bundle, "dev") if target.channel == "stable" else None
            record = build_record(bundle, target.channel, target, result.patches, result.release, old_detail)
        change = None
        if not bootstrap:
            dev_packages = {
                str(app.get("package") or "").lower()
                for app in (old_dev or {}).get("apps") or []
            }
            change = build_change(bundle, target.channel, old_detail, record, dev_packages)
        return {
            "bundle": bundle,
            "channel": target.channel,
            "record": record,
            "change": change,
        }

    def run(self, force=False, limit=None, workers=4, dry_run=False, notifier=None):
        init_db(self.db_path)
        conn = connect(self.db_path)
        started_at = utc_now()
        job_id = None
        message_id = None
        summary = {
            "db": self.db_path,
            "started_at": started_at,
            "finished_at": "",
            "source": "sqlite-worker",
            "repos": 0,
            "targets": 0,
            "unchanged": 0,
            "fetched": 0,
            "written": 0,
            "errors": [],
            "bootstrap_suppressed": False,
            "data_version": "",
            "counts": {},
        }
        try:
            if notifier is not None:
                message_id = notify_safely(notifier, "start", summary)

            if not dry_run:
                with conn:
                    job_id = conn.execute(
                        "INSERT INTO jobs(started_at, status, stats_json) VALUES (?, 'running', '{}')",
                        (started_at,),
                    ).lastrowid

            repos_doc = self.fetch_json("data/repos.json")
            if not isinstance(repos_doc, dict) or not repos_doc:
                raise FetchError("data/repos.json did not contain any repositories")
            summary["repos"] = len(repos_doc)
            targets = self.build_targets(repos_doc)
            summary["targets"] = len(targets)

            hashes = self.load_hashes(conn)
            pending = []
            for target in targets:
                if force or hashes.get((target.url_key, target.branch)) != target.digest:
                    pending.append(target)
                else:
                    summary["unchanged"] += 1
            if limit is not None:
                pending = pending[: max(0, int(limit))]

            bootstrap_done = get_meta(conn, BOOTSTRAP_META, "0") == "1"
            bootstrap = not bootstrap_done
            changes = []
            if pending:
                with ThreadPoolExecutor(max_workers=max(1, min(int(workers or 1), len(pending)))) as pool:
                    futures = {pool.submit(self.fetch_target, target): target for target in pending}
                    for future in as_completed(futures):
                        target = futures[future]
                        try:
                            result = future.result()
                            summary["fetched"] += 1
                        except Exception as exc:
                            summary["errors"].append({
                                "url": target.url,
                                "branch": target.branch,
                                "error": str(exc),
                            })
                            continue
                        try:
                            item = self.process(conn, result, started_at, bootstrap, dry_run=dry_run)
                            summary["written"] += 1
                            if item["change"]:
                                changes.append(item["change"])
                        except Exception as exc:
                            summary["errors"].append({
                                "url": target.url,
                                "branch": target.branch,
                                "error": str(exc),
                            })

            if dry_run:
                summary["bootstrap_suppressed"] = bootstrap
                summary["changes"] = len(changes)
                summary["changed_bundles"] = summarize_changes(changes)
                summary["counts"] = table_counts(conn)
                summary["finished_at"] = utc_now()
                notify_safely(notifier, "finish", summary, message_id=message_id)
                return summary

            now = started_at
            with conn:
                if changes:
                    merge_changes(conn, changes, now)
                # Snapshot the day's accumulated entries, never just this run's
                # delta — otherwise a quiet scan blanks "What's New".
                set_snapshot(conn, "changes", today_change_payload(conn, now))
                data_version, counts = make_snapshots(conn, now)
                if bootstrap and not summary["errors"] and limit is None:
                    set_meta(conn, BOOTSTRAP_META, "1")
                    set_meta(conn, "worker_bootstrap_at", now)
                summary["bootstrap_suppressed"] = bootstrap
                summary["data_version"] = data_version
                summary["counts"] = counts
                summary["changes"] = len(changes)
                summary["changed_bundles"] = summarize_changes(changes)
                summary["finished_at"] = utc_now()
                if job_id is not None:
                    conn.execute(
                        "UPDATE jobs SET finished_at = ?, status = ?, stats_json = ? WHERE id = ?",
                        (
                            summary["finished_at"],
                            "partial" if summary["errors"] else "ok",
                            json_dumps(summary),
                            job_id,
                        ),
                    )
            notify_safely(notifier, "finish", summary, message_id=message_id)
            return summary
        except Exception as exc:
            summary["finished_at"] = utc_now()
            summary["error"] = str(exc)
            if job_id is not None:
                try:
                    with conn:
                        conn.execute(
                            "UPDATE jobs SET finished_at = ?, status = 'error', stats_json = ?, error = ? WHERE id = ?",
                            (summary["finished_at"], json_dumps(summary), str(exc), job_id),
                        )
                except Exception:
                    pass
            notify_safely(notifier, "finish", summary, message_id=message_id, error=exc)
            raise
        finally:
            conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Incrementally update Fresh Morphe SQLite from the awesome-morphe mirror")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--source-base", default=DEFAULT_SOURCE_BASE)
    parser.add_argument("--force", action="store_true", help="refetch every branch even when its hash is unchanged")
    parser.add_argument("--limit", type=int, default=None, help="fetch at most N changed branches")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-notify", action="store_true", help="skip Discord notifications for this run")
    args = parser.parse_args(argv)

    notifier = None
    if not args.dry_run and not args.no_notify:
        notifier = DiscordNotifier.from_env()
        if notifier.enabled:
            print("[worker] Discord notifications enabled", flush=True)

    worker = Worker(args.db, source_base=args.source_base)
    result = worker.run(
        force=args.force,
        limit=args.limit,
        workers=args.workers,
        dry_run=args.dry_run,
        notifier=notifier,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        mode = "dry-run" if args.dry_run else "updated"
        print(f"[worker] {mode} {result['db']}")
        print(
            f"[worker] repos={result['repos']} targets={result['targets']} "
            f"unchanged={result['unchanged']} fetched={result['fetched']} written={result['written']} "
            f"changes={result.get('changes', 0)} errors={len(result['errors'])}"
        )
        if result.get("bootstrap_suppressed"):
            print("[worker] bootstrap suppression active; changelog not emitted")
        for error in result["errors"][:5]:
            print(f"[worker] error {error['url']} ({error['branch']}): {error['error']}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
