"""Quick stats on app_cache.json vs committed bundles."""
import os, json

from app_metadata import valid_icon_url

BUNDLES_DIR = "data/bundles"
pkgs = set()
for f in os.listdir(BUNDLES_DIR):
    if f.endswith(".json") and not f.startswith("_"):
        try:
            data = json.load(open(os.path.join(BUNDLES_DIR, f), encoding="utf-8"))
            for app in data.get("apps", []):
                p = app.get("package", "").lower().strip()
                if p:
                    pkgs.add(p)
        except:
            pass
print(f"{len(pkgs)} unique packages in committed bundles")

cache_path = "data/state/app_cache.json"
cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
with_icon = sum(1 for v in cache.values() if isinstance(v, dict) and valid_icon_url(v.get("icon_url")))
with_name = sum(1 for v in cache.values() if isinstance(v, dict) and v.get("name"))
uncached = [p for p in pkgs if p not in cache]
print(f"App cache: {with_icon} with icon, {with_name} with name, {len(uncached)} uncached")
