import os
import re
import shutil
import json
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape
from state_manager import ensure_dirs, ROOT_DIR, ROOT_DATA_DIR, OUTPUT_DIR, save_json, load_json
from app_metadata import load_cache, resolve_name as _resolve_app_name, store_link

PUBLIC_DATA_DIR = os.path.join(ROOT_DIR, "public", "data")
REPOS_LIST_PATH = os.path.join(ROOT_DATA_DIR, "repos_list.txt")
README_PATH = os.path.join(ROOT_DIR, "README.md")

_app_cache = None


def _registry():
    global _app_cache
    if _app_cache is None:
        _app_cache = load_cache()
    return _app_cache


def _display_app_name(app):
    """Resolved display name; falls back to a derived name, never the package."""
    pkg = app.get("package", "") or ""
    name, _source = _resolve_app_name(pkg, _registry(), app.get("app_name", ""))
    return name


def _rfc2822(date_str):
    """Convert YYYY-MM-DD to RFC 2822 date string."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
    except Exception:
        return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _app_link(app):
    """HTML link to the app's store page, labelled with its resolved name."""
    name = xml_escape(_display_app_name(app))
    url, _store = store_link(app.get("package", ""), _registry())
    return f'<a href="{xml_escape(url)}">{name}</a>' if url else name


def _build_item_description(bundle_entry):
    """Build an HTML description snippet for a changelog item."""
    lines = []
    badge = bundle_entry.get("badge_type", "UPDATED")
    channel = bundle_entry.get("channel", bundle_entry.get("channels", ["unknown"]))
    if isinstance(channel, list):
        channel = ", ".join(channel)
    lines.append(f"<p><strong>{xml_escape(str(badge))}</strong> — channel: {xml_escape(str(channel))}</p>")
    apps = bundle_entry.get("apps", [])
    if apps:
        lines.append("<ul>")
        for app in apps:
            bt = app.get("badge_type", "CHANGED")
            lines.append(f"<li>[{xml_escape(str(bt))}] {_app_link(app)}</li>")
        lines.append("</ul>")
    return "".join(lines)


def _feed_document(site_url, feed_name, feed_title, feed_description, items):
    """Render an RSS 2.0 document for a list of {title, link, description, guid, pubDate}."""
    now_rfc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feed_url = f"{site_url}/{feed_name}"

    rss_items = []
    for item in items:
        # Descriptions are raw HTML inside CDATA; only break out of the section
        # if the content itself contains a terminator.
        description = item["description"].replace("]]>", "]]&gt;")
        rss_items.append(f"""    <item>
      <title>{xml_escape(item['title'])}</title>
      <link>{xml_escape(item['link'])}</link>
      <description><![CDATA[{description}]]></description>
      <pubDate>{item['pubDate']}</pubDate>
      <guid isPermaLink="false">{xml_escape(item['guid'])}</guid>
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{xml_escape(feed_title)}</title>
    <link>{xml_escape(site_url)}</link>
    <description>{xml_escape(feed_description)}</description>
    <language>en</language>
    <lastBuildDate>{now_rfc}</lastBuildDate>
    <atom:link href="{xml_escape(feed_url)}" rel="self" type="application/rss+xml"/>
{chr(10).join(rss_items)}
  </channel>
</rss>"""


def build_changelog_feed(changelog, site_url):
    """Build the per-app changelog feed body from changelog days."""
    items = []
    for day in changelog or []:
        date_str = day.get("date", "")
        affected = day.get("affected_bundles", [])
        for bundle_entry in affected:
            bundle_name = bundle_entry.get("bundle", "unknown")
            channel = bundle_entry.get("channel", "unknown")
            apps = bundle_entry.get("apps", [])
            badge = bundle_entry.get("badge_type", "UPDATED")
            for app in apps:
                bt = app.get("badge_type", "CHANGED")
                app_name = _display_app_name(app)
                desc = (
                    f"<p><strong>{xml_escape(str(bt))}</strong> &mdash; {_app_link(app)} "
                    f"in {xml_escape(str(bundle_name))} ({xml_escape(str(channel))}) "
                    f"&mdash; {xml_escape(str(date_str))}</p>"
                )
                items.append({
                    "title": f"[{bt}] {app_name} in {bundle_name}",
                    "description": desc,
                    "guid": f"{site_url}/changelog.html#{bundle_name}-{channel}-{date_str}-{app.get('package', app_name)}",
                    "pubDate": _rfc2822(date_str),
                    "link": f"{site_url}/index.html#bundle={bundle_name}",
                })
            if not apps:
                items.append({
                    "title": f"[{badge}] {bundle_name} patches — {date_str}",
                    "description": _build_item_description(bundle_entry),
                    "guid": f"{site_url}/changelog.html#{bundle_name}-{channel}-{date_str}",
                    "pubDate": _rfc2822(date_str),
                    "link": f"{site_url}/index.html#bundle={bundle_name}",
                })
    return _feed_document(
        site_url, "feed.xml", "Morphe Patch Tracker — Changelog",
        "Automated changelog tracking Morphe patch bundle updates across stable and dev channels.",
        items,
    )


def generate_rss_feed():
    """Write the per-app changelog feed to feed.xml."""
    changelog_path = os.path.join(ROOT_DATA_DIR, "changelog.json")
    if not os.path.exists(changelog_path):
        print("No changelog.json found, skipping RSS generation.")
        return

    changelog = load_json(changelog_path)
    if not changelog:
        print("Changelog is empty, skipping RSS generation.")
        return

    site_url = os.environ.get("SITE_URL", "https://drnx64.github.io/morphe-track-patches")
    rss_xml = build_changelog_feed(changelog, site_url)
    rss_path = os.path.join(ROOT_DIR, "feed.xml")
    with open(rss_path, "w", encoding="utf-8") as f:
        f.write(rss_xml)
    print(f"Writing RSS feed ({rss_xml.count('<item>')} items)...")


MAX_DAILY_DAYS = 30
MAX_DAILY_BUNDLES = 8
MAX_DAILY_APPS = 6
MAX_DAILY_PATCHES = 4


def _patch_summary(patch_diff):
    """Compact `+ added · ~ modified · - removed` patch summary (HTML)."""
    if not isinstance(patch_diff, dict):
        return ""

    def fmt(key, sign):
        names = [
            p.get("name", "") for p in patch_diff.get(key) or []
            if isinstance(p, dict) and p.get("name")
        ]
        if not names:
            return ""
        shown = ", ".join(xml_escape(n) for n in names[:MAX_DAILY_PATCHES])
        extra = f" +{len(names) - MAX_DAILY_PATCHES} more" if len(names) > MAX_DAILY_PATCHES else ""
        return f"{sign} {shown}{extra}"

    return " · ".join(part for part in (
        fmt("patches_added", "+"),
        fmt("patches_modified", "~"),
        fmt("patches_removed", "-"),
    ) if part)


def _render_day_body(bundles):
    """Digest-style HTML for one day of bundle updates."""
    blocks = []
    for bundle in bundles[:MAX_DAILY_BUNDLES]:
        name = bundle.get("patches_name") or bundle.get("bundle") or "Unknown bundle"
        meta = " · ".join(
            xml_escape(str(part)) for part in
            (bundle.get("channel", ""), bundle.get("badge_type", "")) if part
        )
        head = f"<p><strong>{xml_escape(str(name))}</strong>"
        if meta:
            head += f" <em>({meta})</em>"
        blocks.append(head + "</p>")

        apps = bundle.get("apps") or []
        if not apps:
            continue
        rows = []
        for app in apps[:MAX_DAILY_APPS]:
            badge = app.get("badge_type", "CHANGED")
            removed = app.get("removed") or badge == "REMOVED APP"
            label = _app_link(app)
            if removed:
                label = f"<s>{label}</s>"
            detail = _patch_summary(app.get("patch_diff"))
            row = f"<li>{label} <em>[{xml_escape(str(badge))}]</em>"
            if detail:
                row += f" — {detail}"
            rows.append(row + "</li>")
        if len(apps) > MAX_DAILY_APPS:
            rows.append(f"<li>… {len(apps) - MAX_DAILY_APPS} more apps</li>")
        blocks.append("<ul>" + "".join(rows) + "</ul>")

    if len(bundles) > MAX_DAILY_BUNDLES:
        blocks.append(f"<p>… {len(bundles) - MAX_DAILY_BUNDLES} more bundles</p>")
    return "".join(blocks) or "<p>No app changes.</p>"


def _daily_items(changelog, buffer, site_url):
    """One item per day, newest first, with today's in-progress buffer merged in."""
    days = []
    for day in changelog or []:
        if isinstance(day, dict):
            days.append([day.get("date", ""), list(day.get("affected_bundles") or [])])

    if isinstance(buffer, dict) and buffer.get("date") and buffer.get("affected_bundles"):
        entries = buffer["affected_bundles"]
        bundles = list(entries.values()) if isinstance(entries, dict) else list(entries)
        bundles = [b for b in bundles if isinstance(b, dict)]
        if bundles:
            for day in days:
                if day[0] == buffer["date"]:
                    day[1].extend(bundles)
                    bundles = []
                    break
            if bundles:
                days.append([buffer["date"], bundles])

    days.sort(key=lambda d: d[0], reverse=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    items = []
    for date_str, bundles in days[:MAX_DAILY_DAYS]:
        if not bundles:
            continue
        try:
            pretty = datetime.strptime(date_str, "%Y-%m-%d").strftime("%B %d, %Y")
        except (TypeError, ValueError):
            pretty = date_str
        items.append({
            "title": f"Today's updates — {pretty}" if date_str == today else f"Updates for {pretty}",
            "description": _render_day_body(bundles),
            "guid": f"{site_url}/feed-daily.xml#{date_str}",
            "pubDate": _rfc2822(date_str),
            "link": f"{site_url}/#/changelog",
        })
    return items


def build_daily_feed(changelog, buffer, site_url):
    """Build the one-item-per-day digest feed."""
    return _feed_document(
        site_url, "feed-daily.xml", "Morphe Patch Tracker — Daily Updates",
        "One entry per day listing the Morphe patch bundles and apps that changed.",
        _daily_items(changelog, buffer, site_url),
    )


def generate_daily_feed():
    """Write the daily digest feed to feed-daily.xml."""
    changelog = load_json(os.path.join(ROOT_DATA_DIR, "changelog.json"), default=[])
    buffer = load_json(os.path.join(ROOT_DATA_DIR, "state", "daily_buffer.json"), default={})
    site_url = os.environ.get("SITE_URL", "https://drnx64.github.io/morphe-track-patches")

    rss_xml = build_daily_feed(changelog, buffer, site_url)
    feed_path = os.path.join(ROOT_DIR, "feed-daily.xml")
    with open(feed_path, "w", encoding="utf-8") as f:
        f.write(rss_xml)
    print(f"Writing daily RSS feed ({rss_xml.count('<item>')} items)...")


def generate_static_files():
    ensure_dirs()

    # changelog.json is now maintained directly in data/changelog.json by merge_daily_buffer.py
    # No longer copy from data/output/ (which is gitignored and ephemeral on CI)

    # Vite serves data/ directly via server.fs.allow — no need to copy to public/data/
    # Bundles are split into data/bundles/<name>.json + data/bundles/_index.json

    # Update README repos table
    update_readme_repos_table()

    print("Static site files synced.")


def update_readme_repos_table():
    """Update the repository table in README.md from repos_list.txt."""
    if not os.path.exists(REPOS_LIST_PATH):
        print("No repos_list.txt found, skipping README update.")
        return

    if not os.path.exists(README_PATH):
        print("No README.md found, skipping README update.")
        return

    repos = []
    with open(REPOS_LIST_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([^/]+/[^/#\s]+)\s*->\s*(https://\S+)", line)
            if m:
                owner_repo = m.group(1)
                url = m.group(2)
                author = owner_repo.split("/")[0]
                repos.append((owner_repo, url, author))

    if not repos:
        print("No repos parsed from repos_list.txt, skipping README update.")
        return

    # Generate table rows
    table_rows = []
    for i, (owner_repo, url, author) in enumerate(sorted(repos, key=lambda x: x[0].lower()), 1):
        table_rows.append(
            f"| {i} | [{owner_repo}]({url}) | [@{author}](https://github.com/{author}) |"
        )

    table_header = f"| # | Repo | Author |\n|---|------|--------|\n"
    table_body = "\n".join(table_rows)

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    start_marker = "Thanks to every developer who publishes Morphe patches."
    end_marker = "Missing or new? Check"

    start_idx = readme.find(start_marker)
    end_idx = readme.find(end_marker)

    if start_idx == -1 or end_idx == -1:
        print("Could not find table markers in README.md, skipping update.")
        return

    start_line_end = readme.index("\n", start_idx) + 1
    end_line_start = readme.rindex("\n", 0, end_idx) + 1

    new_replacement = "\n" + table_header + table_body + "\n\n"

    updated_readme = readme[:start_line_end] + new_replacement + readme[end_line_start:]

    total = len(repos)
    updated_readme = re.sub(
        r'\(\d+\+? repos\)',
        f'({total} repos)',
        updated_readme
    )

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(updated_readme)

    print(f"README.md updated with {total} repositories in the table.")


if __name__ == "__main__":
    generate_static_files()
