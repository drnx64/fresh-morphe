"""Populate app_cache.json from committed bundle data.

Unified cache: data/state/app_cache.json maps package -> {name, icon_url}.
"""
import os
import sys
import time
import json
import random

sys.path.insert(0, os.path.dirname(__file__))

from state_manager import ROOT_DATA_DIR, STATE_DIR, load_json, save_json, ensure_dirs
from icon_fetcher import APP_CACHE_PATH
from app_metadata import valid_icon_url

BUNDLES_DIR = os.path.join(ROOT_DATA_DIR, "bundles")


def extract_all_packages():
    packages = set()
    for fname in os.listdir(BUNDLES_DIR):
        if fname.startswith("_") or not fname.endswith(".json"):
            continue
        fpath = os.path.join(BUNDLES_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                bundle = json.load(f)
            for app in bundle.get("apps", []):
                pkg = app.get("package", "").lower().strip()
                if pkg:
                    packages.add(pkg)
        except Exception as e:
            print(f"  [warn] skipping {fname}: {e}")
    return sorted(packages)


def main():
    ensure_dirs()
    packages = extract_all_packages()
    print(f"Found {len(packages)} unique packages in committed bundles.")

    cache = load_json(APP_CACHE_PATH, default={})

    uncached = [p for p in packages if not valid_icon_url((cache.get(p) or {}).get("icon_url"))]
    print(f"App cache: {len(cache)} existing, {len(uncached)} to fetch")

    if uncached:
        from icon_fetcher import fetch_app_icon
        print(f"\nFetching {len(uncached)} icons from Play Store...")
        for i, pkg in enumerate(uncached):
            if i > 0 and i % 10 == 0:
                print(f"  Progress: {i}/{len(uncached)}")
            fetch_app_icon(pkg)
            time.sleep(random.uniform(0.3, 0.8))
        print(f"  Progress: {len(uncached)}/{len(uncached)} (done)")

    cache = load_json(APP_CACHE_PATH, default={})
    with_icon = sum(1 for v in cache.values() if isinstance(v, dict) and valid_icon_url(v.get("icon_url")))
    with_name = sum(1 for v in cache.values() if isinstance(v, dict) and v.get("name"))
    print(f"\nFinal: {with_icon} with icon, {with_name} with name")
    print(f"  app_cache.json: {os.path.getsize(APP_CACHE_PATH) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
