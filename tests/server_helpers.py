"""Shared expectations for the SQLite importer/API tests.

The pipeline data changes every hour and can contain several keys that
collapse to the same bundle/channel pair on case-insensitive filesystems.
Derive expectations from the source data instead of pinning counts.
"""
import json
import os


def load_source_index(data_dir):
    with open(os.path.join(data_dir, "bundles", "_index.json"), encoding="utf-8") as fh:
        return json.load(fh)


def deduped_bundles(source_index):
    """Return the bundle rows the importer will actually store.

    ``import_legacy`` iterates sorted index keys and upserts by
    ``(bundle, channel)``, so later keys win when two repositories collapse
    onto the same bundle (case-only duplicates).
    """
    rows = {}
    for key in sorted(source_index):
        entry = source_index[key]
        bundle = entry.get("bundle") or key.rsplit(":", 1)[0]
        channel = entry.get("channel") or key.rsplit(":", 1)[-1]
        rows[f"{bundle}:{channel}"] = (key, entry)
    return rows


def deduped_record(record):
    """Mirror the importer's per-record dedupe.

    Some source bundle files repeat a package or a patch name; `upsert_bundle`
    stores each (app, patch) once, so tests comparing rows to the source must
    apply the same rule.
    """
    out = dict(record)
    seen_apps = set()
    apps = []
    for app in record.get("apps") or []:
        package = app.get("package") or ""
        if not package or package in seen_apps:
            continue
        seen_apps.add(package)
        patched = dict(app)
        seen_patches = set()
        patches = []
        for patch in app.get("patches") or []:
            name = patch.get("name")
            if not name or name in seen_patches:
                continue
            seen_patches.add(name)
            patches.append(patch)
        if patches or "patches" not in app:
            patched["patches"] = patches
        apps.append(patched)
    out["apps"] = apps
    return out


def expected_counts(data_dir, source_index=None):
    if source_index is None:
        source_index = load_source_index(data_dir)
    rows = deduped_bundles(source_index)
    packages = set()
    bundle_apps = 0
    latest = 0
    patches = 0
    for key, entry in rows.values():
        apps = entry.get("apps") or []
        if entry.get("channel") == "latest":
            latest += 1
        bundle_apps += len({package for package in apps if package})
        packages.update(package for package in apps if package)

        path = os.path.join(data_dir, "bundles", key.replace(":", "_") + ".json")
        with open(path, encoding="utf-8") as fh:
            detail = json.load(fh)
        seen_apps = set()
        for app in detail.get("apps") or []:
            package = app.get("package") or ""
            if not package or package in seen_apps:
                continue
            seen_apps.add(package)
            # The importer stores one row per (app, patch name).
            seen_patches = set()
            for patch in app.get("patches") or []:
                name = patch.get("name")
                if name and name not in seen_patches:
                    seen_patches.add(name)
                    patches += 1

    return {
        "bundles": len(rows),
        "latest": latest,
        "bundle_apps": bundle_apps,
        "apps": len(packages),
        "patches": patches,
    }
