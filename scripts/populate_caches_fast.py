"""Fast cache population - batch approach with unified app_cache.json."""
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
        except Exception:
            pass
    return sorted(packages)


def fetch_batch(pkgs):
    from icon_fetcher import fetch_app_icon
    success = 0
    for pkg in pkgs:
        try:
            result = fetch_app_icon(pkg)
            if result:
                success += 1
        except Exception:
            pass
        time.sleep(random.uniform(0.2, 0.5))
    return success


def main():
    ensure_dirs()
    packages = extract_all_packages()
    print(f"Found {len(packages)} unique packages.")

    cache = load_json(APP_CACHE_PATH, default={})
    uncached = [p for p in packages if not valid_icon_url((cache.get(p) or {}).get("icon_url"))]
    print(f"Cached: {len(cache)}, to fetch: {len(uncached)}")

    if uncached:
        batch_size = 50
        total_ok = 0
        for start in range(0, len(uncached), batch_size):
            batch = uncached[start:start + batch_size]
            ok = fetch_batch(batch)
            total_ok += ok
            print(f"  Batch {start // batch_size + 1}: {ok}/{len(batch)} ok | Total: {total_ok}/{len(uncached)}")
            time.sleep(1)

    cache = load_json(APP_CACHE_PATH, default={})
    with_icon = sum(1 for v in cache.values() if isinstance(v, dict) and valid_icon_url(v.get("icon_url")))
    with_name = sum(1 for v in cache.values() if isinstance(v, dict) and v.get("name"))
    print(f"\nFinal: {with_icon} icons, {with_name} names")
    print(f"  app_cache.json: {os.path.getsize(APP_CACHE_PATH) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
