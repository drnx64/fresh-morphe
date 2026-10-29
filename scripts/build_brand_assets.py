"""Generate Fresh Morphe brand assets from the original artwork.

Sources (tracked at the repo root):
  MORPHE TRACKER.jpg       -> favicon / OG image
  MORPHE TRACKER (1).webp  -> transparent header logo

Run from the repo root:
  python scripts/build_brand_assets.py
"""

import os
import sys

from PIL import Image

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_DIR = os.path.join(ROOT_DIR, "public")

ICON_SOURCE = os.path.join(ROOT_DIR, "MORPHE TRACKER.jpg")
LOGO_SOURCE = os.path.join(ROOT_DIR, "MORPHE TRACKER (1).webp")

FAVICON_SIZES = {
    "favicon-32.png": 32,
    "favicon-192.png": 192,
    "favicon-512.png": 512,
    "apple-touch-icon.png": 180,
}


def _load(source):
    if not os.path.exists(source):
        raise SystemExit(f"missing source image: {source}")
    return Image.open(source)


def _save_png(image, name, size):
    out = image.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    path = os.path.join(PUBLIC_DIR, name)
    out.save(path, "PNG", optimize=True)
    print(f"[brand] {name} ({size}x{size})")


def build():
    os.makedirs(PUBLIC_DIR, exist_ok=True)

    icon = _load(ICON_SOURCE)
    if icon.width != icon.height:
        side = min(icon.width, icon.height)
        left = (icon.width - side) // 2
        top = (icon.height - side) // 2
        icon = icon.crop((left, top, left + side, top + side))

    for name, size in FAVICON_SIZES.items():
        _save_png(icon, name, size)

    ico_path = os.path.join(PUBLIC_DIR, "favicon.ico")
    icon.convert("RGB").save(
        ico_path,
        sizes=[(16, 16), (32, 32), (48, 48)],
    )
    print("[brand] favicon.ico (16/32/48)")

    og_path = os.path.join(PUBLIC_DIR, "fresh-og.jpg")
    icon.convert("RGB").save(og_path, "JPEG", quality=92, optimize=True)
    print(f"[brand] fresh-og.jpg ({icon.width}x{icon.height})")

    logo = _load(LOGO_SOURCE)
    if logo.mode != "RGBA":
        logo = logo.convert("RGBA")
    max_logo = 256
    if max(logo.size) > max_logo:
        logo.thumbnail((max_logo, max_logo), Image.Resampling.LANCZOS)
    logo_path = os.path.join(PUBLIC_DIR, "fresh-logo.webp")
    logo.save(logo_path, "WEBP", lossless=True, method=6)
    print(f"[brand] fresh-logo.webp ({logo.width}x{logo.height}, alpha)")


if __name__ == "__main__":
    try:
        build()
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"[brand] failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
