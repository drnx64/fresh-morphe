import os
import re

from state_manager import (
    BUNDLES_DIR, RAW_DIR, ensure_dirs, load_current_snapshot, load_json,
    rebuild_snapshot_from_bundles, repo_slug, save_json,
)
from registry_api import CHANNELS, fetch_current_morphe_channels


def _load_existing_records():
    """Return ``{bundle:channel -> {bundle, repo_url}}`` from local outputs.

    The snapshot is authoritative for the last successful parse. The bundle
    index is merged as a fallback so a fresh CI checkout still preserves
    historical bundle names and repo identities.
    """
    records = {}

    index = load_json(os.path.join(BUNDLES_DIR, "_index.json"), default={})
    for key, entry in index.items():
        records[key] = {
            "bundle": entry.get("bundle") or key.split(":", 1)[0],
            "repo_url": entry.get("repo_url", ""),
        }

    snapshot = load_current_snapshot()
    if not snapshot:
        snapshot = rebuild_snapshot_from_bundles()
    for key, entry in snapshot.items():
        current = records.setdefault(key, {})
        current["bundle"] = entry.get("bundle") or key.split(":", 1)[0]
        if entry.get("repo_url"):
            current["repo_url"] = entry["repo_url"]

    return records


def _source_slug(source):
    return repo_slug(source.get("url", ""))


def _legacy_keys_for_source(source, by_slug, by_name):
    slug = _source_slug(source)
    keys = set(by_slug.get(slug, set()))
    for alias in source.get("aliases") or []:
        keys.update(by_name.get(str(alias).lower(), set()))
    return sorted(keys)


def _sanitize_bundle_name(value):
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value).strip(".-")
    return value


def _choose_bundle_name(source, old_keys, used_names):
    slug = _source_slug(source)
    aliases = [str(a).lower() for a in (source.get("aliases") or [])]
    old_names = []
    for key in old_keys:
        name = key.split(":", 1)[0]
        if name and name not in old_names:
            old_names.append(name)

    if old_names:
        candidates = list(old_names)
    elif aliases:
        candidates = list(aliases)
    else:
        namespace = str(source.get("namespace") or "").lower()
        repo = str(source.get("repo") or "").lower()
        candidates = [namespace or repo or slug.split("/")[-1]]

    def score(name):
        lowered = name.lower()
        return (
            0 if lowered in aliases else 1,
            0 if lowered == str(source.get("namespace", "")).lower() else 1,
            len(name),
            name,
        )

    name = _sanitize_bundle_name(sorted(candidates, key=score)[0])
    if not name:
        name = _sanitize_bundle_name(slug.replace("/", "-")) or "bundle"

    if used_names.get(name) not in (None, slug):
        repo = slug.split("/")[-1] or "bundle"
        name = _sanitize_bundle_name(f"{name}-{repo}")
        suffix = 2
        while used_names.get(name) not in (None, slug):
            name = _sanitize_bundle_name(f"{name}-{suffix}")
            suffix += 1

    used_names[name] = slug
    return name


def build_registry_tree(bundles_by_channel, existing_records=None):
    """Turn registry channel records into the legacy tree.json shape.

    Two synthetic blob entries are emitted per bundle/channel, matching the
    paths consumed by ``download_bundles.group_tree_files``. Registry metadata
    rides along so downloads can resolve release assets without the retired
    Jman branch.
    """
    existing_records = existing_records or {}
    by_slug = {}
    by_name = {}
    for key, record in existing_records.items():
        bundle_name = record.get("bundle") or key.split(":", 1)[0]
        slug = repo_slug(record.get("repo_url", ""))
        if slug:
            by_slug.setdefault(slug, set()).add(key)
        by_name.setdefault(bundle_name.lower(), set()).add(key)

    entries = []
    used_names = {}
    for key, record in existing_records.items():
        name = (record.get("bundle") or key.split(":", 1)[0]).lower()
        used_names.setdefault(name, repo_slug(record.get("repo_url", "")))
    name_cache = {}
    seen = set()

    for channel in CHANNELS:
        for bundle in bundles_by_channel.get(channel, []):
            source = bundle.get("source") or {}
            source_id = bundle.get("source_id") or source.get("id") or ""
            dedupe_key = (source_id, channel)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            bundle_id = bundle.get("id", "")
            download_url = bundle.get("download_url", "")
            if not bundle_id or not download_url or not source:
                continue

            slug = _source_slug(source)
            if source_id not in name_cache:
                legacy_keys = _legacy_keys_for_source(source, by_slug, by_name)
                name_cache[source_id] = (
                    _choose_bundle_name(source, legacy_keys, used_names),
                    legacy_keys,
                )
            name, legacy_keys = name_cache[source_id]

            registry = {
                "bundle_id": bundle_id,
                "source_id": source_id,
                "source_url": source.get("url", ""),
                "version": bundle.get("version", ""),
                "created_at": bundle.get("created_at", ""),
                "description": bundle.get("description", ""),
                "download_url": download_url,
                "signature_download_url": bundle.get("signature_download_url") or "",
                "channels": bundle.get("channels", []),
                "metadata_status": bundle.get("metadata_status", ""),
                "patch_list_available": bundle.get("patch_list_available", False),
                "patch_count": bundle.get("patch_count", 0),
                "file_hash": bundle.get("file_hash", ""),
            }
            sha_base = ":".join(
                str(part)
                for part in (
                    "registry",
                    bundle_id,
                    bundle.get("file_hash") or bundle.get("version") or "",
                    channel,
                )
            )
            for filename, sha_suffix in (
                ("patches-bundle.json", "bundle"),
                ("patches-list.json", "list"),
            ):
                entry = {
                    "path": f"patch-bundles/{name}/{channel}/{filename}",
                    "mode": "100644",
                    "type": "blob",
                    "sha": f"{sha_base}:{sha_suffix}",
                    "size": 0,
                    "url": download_url,
                    "registry": registry,
                }
                if legacy_keys:
                    entry["legacy_keys"] = legacy_keys
                entries.append(entry)

    return entries


def fetch_bundle_tree():
    """Fetch the current Morphe bundle/channel tree from the registry.

    Refuses to write an empty tree: that would make every known bundle look
    removed on the next diff.
    """
    print("Fetching current Morphe bundle channels from Patch Bundle Registry...")
    bundles_by_channel = fetch_current_morphe_channels()
    total = sum(len(rows) for rows in bundles_by_channel.values())
    print(f"Registry returned {total} channel records.")

    entries = build_registry_tree(bundles_by_channel, _load_existing_records())
    if not entries:
        raise RuntimeError(
            "Patch Bundle Registry returned no usable Morphe channel bundles; "
            "refusing to write an empty tree"
        )

    save_json(os.path.join(RAW_DIR, "tree.json"), entries)
    print(f"Saved {len(entries)} tree entries ({len(entries) // 2} bundle+channel pairs).")
    return entries


if __name__ == "__main__":
    ensure_dirs()
    fetch_bundle_tree()
