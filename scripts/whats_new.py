"""Generate the Telegram "what's new" digest.

Reads daily_buffer.json + parsed_bundles.json, renders a list of blocks, then
packs them into at most MAX_MESSAGES Telegram messages. Continuation messages
never repeat the banner. Output goes to scripts/temp/whats-new.{md,json}.
"""
import os
import sys
import json
from datetime import datetime, timezone
from html import escape as _esc
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(__file__))

from state_manager import (
    DAILY_BUFFER_PATH, RAW_DIR, ROOT_DATA_DIR,
    load_json, ensure_dirs,
)
from icon_fetcher import APP_CACHE_PATH
from app_metadata import resolve_name, store_link
from config import SITE_URL

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "temp")

PACK_BUDGET = 3900  # per-message budget under Telegram's 4096 cap, with slack for the edit stamp
MAX_MESSAGES = 2  # 1 normally, a continuation message on busy days
MAX_BUNDLES = 8
MAX_APPS = 6
MAX_PATCHES = 5

SECTION_ORDER = ["new_bundles", "new_apps", "updated_apps"]
SECTION_LABELS = {
    "new_bundles": "\u2501" * 25 + "\n<b><u>NEW BUNDLES</u></b>" + "\n" + "\u2501" * 25,
    "new_apps": "\u2501" * 25 + "\n<b><u>NEW APPS</u></b>" + "\n" + "\u2501" * 25,
    "updated_apps": "\u2501" * 25 + "\n<b><u>UPDATED APPS</u></b>" + "\n" + "\u2501" * 25,
}
CONT_MARKER = "<i>\u25c2 continued</i>"
MORE_LABEL = "see full changelog"
LINK_ROOM = 160  # headroom held back in the last message for the overflow link


def _load_app_name(pkg, app_cache, bundle_name=""):
    """Best display name for a package — never the raw package id."""
    name, _source = resolve_name(pkg, app_cache, bundle_name)
    return name


def _get_patches_for_app(bundle_name, pkg, parsed_bundles):
    for key, rec in parsed_bundles.items():
        if rec.get("bundle", "") == bundle_name:
            for app in rec.get("apps", []):
                if app.get("package", "").lower() == pkg.lower():
                    return [p.get("name", "unknown") for p in app.get("patches", [])]
    return []


def _patch_link(patch_name, pkg):
    label = _esc(patch_name)
    if not pkg:
        return label
    url = f"{SITE_URL}/?open-app={quote(pkg, safe='')}&patch={quote(patch_name, safe='')}"
    return f'<a href="{url}">{label}</a>'


def _build_sections(entries, app_cache, parsed_bundles):
    sections = {"new_bundles": [], "new_apps": [], "updated_apps": []}

    for bundle_key, entry in entries.items():
        bundle_name = entry.get("bundle", bundle_key.split(":")[0])
        badge_type = entry.get("badge_type", "")
        patches_name = entry.get("patches_name", "") or bundle_name
        apps = entry.get("apps", [])

        if badge_type == "NEW BUNDLE":
            app_items = []
            for app in apps:
                pkg = app.get("package", "")
                app_name = _load_app_name(pkg, app_cache, app.get("app_name", ""))
                pd = app.get("patch_diff", {})
                added = pd.get("patches_added", [])

                patches = []
                if added:
                    for p in added:
                        patches.append({"name": p.get("name", "unknown"), "badge": "+", "pkg": pkg})
                else:
                    for pn in _get_patches_for_app(bundle_name, pkg, parsed_bundles):
                        patches.append({"name": pn, "badge": "+", "pkg": pkg})

                if patches:
                    app_items.append({"name": app_name, "patches": patches, "pkg": pkg})

            if app_items:
                sections["new_bundles"].append({
                    "bundle_name": bundle_name, "patches_name": patches_name, "apps": app_items,
                })

        elif badge_type == "UPDATED":
            new_app_items = []
            updated_app_items = []

            for app in apps:
                pkg = app.get("package", "")
                app_name = _load_app_name(pkg, app_cache, app.get("app_name", ""))
                app_badge = app.get("badge_type", "")
                pd = app.get("patch_diff", {})
                added = pd.get("patches_added", [])
                removed = pd.get("patches_removed", [])
                modified = pd.get("patches_modified", [])

                patches = []
                for p in added:
                    patches.append({"name": p.get("name", "unknown"), "badge": "+", "pkg": pkg})
                for p in modified:
                    patches.append({"name": p.get("name", "unknown"), "badge": "~", "pkg": pkg})
                for p in removed:
                    patches.append({"name": p.get("name", "unknown"), "badge": "-", "pkg": pkg})

                if app_badge == "NEW APP":
                    if not patches:
                        for pn in _get_patches_for_app(bundle_name, pkg, parsed_bundles):
                            patches.append({"name": pn, "badge": "+", "pkg": pkg})
                    if patches:
                        new_app_items.append({"name": app_name, "patches": patches, "pkg": pkg})
                elif app_badge in ("UPDATED APP", "MAJOR UPDATE"):
                    if patches:
                        updated_app_items.append({"name": app_name, "patches": patches, "pkg": pkg})
                elif app_badge == "REMOVED APP":
                    updated_app_items.append({"name": app_name, "patches": [], "removed": True, "pkg": pkg})

            if new_app_items:
                sections["new_apps"].append({
                    "bundle_name": bundle_name, "patches_name": patches_name, "apps": new_app_items,
                })
            if updated_app_items:
                sections["updated_apps"].append({
                    "bundle_name": bundle_name, "patches_name": patches_name, "apps": updated_app_items,
                })

        elif badge_type == "REMOVED BUNDLE":
            app_items = []
            for app in apps:
                pkg = app.get("package", "")
                app_name = _load_app_name(pkg, app_cache, app.get("app_name", ""))
                app_items.append({"name": app_name, "patches": [], "removed": True, "pkg": pkg})
            if app_items:
                sections["updated_apps"].append({
                    "bundle_name": bundle_name, "patches_name": patches_name, "apps": app_items,
                })

    for items in sections.values():
        for item in items:
            for app in item.get("apps", []):
                app["url"], app["store"] = store_link(app.get("pkg", ""), app_cache)

    return sections


def _dedup_sections(sections):
    for key in sections:
        seen = {}
        for item in sections[key]:
            bname = item["bundle_name"]
            if bname not in seen or len(item.get("apps", [])) > len(seen[bname].get("apps", [])):
                seen[bname] = item
        sections[key] = list(seen.values())
    return sections


def _app_label(app):
    """App name as a store-page link when one can be built."""
    name = _esc(app["name"])
    url = app.get("url", "")
    return f'<a href="{_esc(url)}">{name}</a>' if url else name


def _render_bundle(item):
    """Render a single bundle as a block of lines."""
    lines = []
    lines.append(f'\u25b8 <b>{_esc(item["patches_name"])}</b>')

    apps = item.get("apps", [])
    for app in apps:
        if app.get("removed"):
            lines.append(f'    - <s>{_app_label(app)}</s>')
            continue
        lines.append(f'    - <b>{_app_label(app)}</b>')
        for patch in app.get("patches", []):
            badge = patch["badge"]
            pkg = patch.get("pkg", "")
            name = patch["name"]
            if badge == "-":
                lines.append(f'        {badge} {_esc(name)}')
            else:
                lines.append(f'        {badge} {_patch_link(name, pkg)}')

    return "\n".join(lines)


def _sans_bold(text):
    """Map ASCII letters/digits to Mathematical Sans-Serif Bold glyphs.

    Telegram has no heading tags, so the banner relies on these. Generated
    rather than hand-typed to avoid mistyping the code points.
    """
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(0x1D5D4 + ord(ch) - ord("A")))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D5EE + ord(ch) - ord("a")))
        elif "0" <= ch <= "9":
            out.append(chr(0x1D7EC + ord(ch) - ord("0")))
        else:
            out.append(ch)
    return "".join(out)


def _render_header(date=None):
    today = datetime.now(timezone.utc)
    if date:
        try:
            today = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return "\n".join([
        _sans_bold("TODAY'S UPDATES"),
        f"Date: {today.strftime('%B %d, %Y')}",
        "\u2501" * 25,
    ])


def _render_blocks(sections, date=None):
    """Render the digest as an ordered list of blocks (highest priority first)."""
    blocks = [_render_header(date)]

    for key in SECTION_ORDER:
        items = sections.get(key, [])
        if not items:
            continue
        blocks.append(SECTION_LABELS[key])
        for item in items:
            blocks.append(_render_bundle(item))

    return blocks


def _more_link(dropped):
    url = f"{SITE_URL}/#/changelog"
    return f'<a href="{url}">\u2026 {dropped} more ({MORE_LABEL})</a>'


def _pack_lines(blocks, budget, max_messages, reserve):
    """Pack blocks line by line, breaking only where Telegram's limit demands.

    Every continuation message gets CONT_MARKER instead of the banner, and the
    final message holds `reserve` chars back for an overflow pointer.

    Returns the list of messages, or None if they would exceed max_messages.
    """
    chunks = []
    current = ""

    def room():
        return budget - (reserve if len(chunks) + 1 >= max_messages else 0)

    def open_next(line):
        nonlocal current
        chunks.append(current)
        current = f"{CONT_MARKER}\n\n{line}"

    for block in blocks:
        lines = block.split("\n")
        for i, line in enumerate(lines):
            if i == 0:
                separator = "\n\n" if current else ""
                if len(current) + len(separator) + len(line) > room():
                    if not current:
                        pass  # single line longer than the budget — take it as-is
                    elif len(chunks) + 1 >= max_messages:
                        return None
                    else:
                        open_next(line)
                        continue
                current = f"{current}{separator}{line}"
            else:
                if len(current) + 1 + len(line) > room():
                    if len(chunks) + 1 >= max_messages:
                        return None
                    open_next(line)
                    continue
                current = f"{current}\n{line}"

    if current:
        chunks.append(current)
    return chunks or None


def _is_section_label(block):
    return block in SECTION_LABELS.values()


def _content_blocks(blocks):
    """Blocks that carry actual content (banner and section labels excluded)."""
    return [b for i, b in enumerate(blocks) if i and not _is_section_label(b)]


def _pack_blocks(blocks, budget=PACK_BUDGET, max_messages=MAX_MESSAGES, link_room=LINK_ROOM):
    """Pack blocks into at most max_messages Telegram messages.

    Blocks arrive highest-priority first, so overflow is always dropped from
    the tail — UPDATED APPS yields before NEW APPS yields before NEW BUNDLES —
    and is replaced by a pointer to the full changelog. The last content block
    is never dropped: the digest always shows something.
    """
    if not blocks or not any(blocks):
        return []

    chunks = _pack_lines(blocks, budget, max_messages, reserve=0)
    if chunks is not None:
        return chunks

    kept = list(blocks)
    dropped = 0
    while len(_content_blocks(kept)) > 1:
        kept.pop()
        dropped += 1
        while len(kept) > 2 and _is_section_label(kept[-1]):
            kept.pop()
        chunks = _pack_lines(kept, budget, max_messages, reserve=link_room)
        if chunks is not None:
            chunks[-1] = f"{chunks[-1]}\n\n{_more_link(dropped)}"
            return chunks

    # single content block that still won't fit — show it truncated
    return _pack_lines(kept, budget, max_messages, reserve=0) or [blocks[0]]


def generate_whats_new(entries=None, date=None):
    """Generate the changelog diff from a daily buffer or supplied entries.

    Args:
        entries: optional ``{bundle_key: entry}`` mapping. Defaults to today's
            daily buffer.
        date: optional UTC day (YYYY-MM-DD) for the banner.

    Returns list[str] - one chunk per Telegram message.
    """
    ensure_dirs()
    buffer = load_json(DAILY_BUFFER_PATH, default={})
    if entries is None:
        entries = buffer.get("affected_bundles", {})
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not entries:
        print("[-] No daily buffer entries")
        return None

    app_cache = load_json(APP_CACHE_PATH, default={})
    parsed_path = os.path.join(RAW_DIR, "parsed_bundles.json")
    parsed_bundles = load_json(parsed_path, default={})

    sections = _build_sections(entries, app_cache, parsed_bundles)
    sections = _dedup_sections(sections)

    if not any(sections.values()):
        print("[-] No changes to report")
        return None

    for key in sections:
        sections[key] = sections[key][:MAX_BUNDLES]
        for item in sections[key]:
            item["apps"] = item["apps"][:MAX_APPS]
            for app in item["apps"]:
                if not app.get("removed") and len(app.get("patches", [])) > MAX_PATCHES:
                    remaining = len(app["patches"]) - MAX_PATCHES
                    app["patches"] = app["patches"][:MAX_PATCHES]
                    app["patches"].append({"name": f"+{remaining} more", "badge": "*", "pkg": ""})

    chunks = _pack_blocks(_render_blocks(sections, day))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    md_path = os.path.join(OUTPUT_DIR, "whats-new.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n\n--- next message ---\n\n".join(chunks))
    print(f"[+] Wrote {md_path} ({len(chunks)} chunk{'s' if len(chunks) > 1 else ''})")

    json_path = os.path.join(OUTPUT_DIR, "whats-new.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "chunks": chunks,
            "total_chunks": len(chunks),
            "date": day,
        }, f, indent=2)

    return chunks


if __name__ == "__main__":
    result = generate_whats_new()
    if result:
        for i, chunk in enumerate(result):
            print(f"\n--- Chunk {i + 1}/{len(result)} ({len(chunk)} chars) ---")
            print(chunk)
