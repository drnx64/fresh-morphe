# Fresh Morphe — Architecture Reference

## Overview

Fresh Morphe is a fully automated monitoring system that crawls the
[Jman-Github/ReVanced-Patch-Bundles](https://github.com/Jman-Github/ReVanced-Patch-Bundles)
registry, discovers Morphe `.mpp` patch bundles, parses their compatible apps and patches,
detects changes via SHA-256 fingerprinting, accumulates daily changelogs, enriches app data
with Google Play Store icons, and renders a dark-themed static web dashboard.

The pipeline runs on GitHub Actions every 3 hours and commits results back to the repo,
which is served via GitHub Pages. A Service Worker provides offline caching and
stale-while-revalidate for the data files.

---

## File Tree

```
fresh-morphe/
|
|-- .env                          # Telegram bot token & chat ID (secrets, gitignored)
|-- .gitignore
|-- sw.js                         # Service Worker (stale-while-revalidate caching)
|-- index.html                    # Dashboard SPA entry point
|-- changelog.html                # Historical changelog SPA entry point
|-- ARCHITECTURE.md               # This file
|-- README.md                     # Project README
|-- requirements.txt              # Python deps: requests, beautifulsoup4, lxml, python-dotenv
|
|-- assets/
|   |-- app-init.js               # Storage clearing + DOM init dispatch
|   |-- app-utils.js              # Shared utility functions
|   |-- app-data.js               # IndexedDB wrapper + data fetching
|   |-- app-dashboard.js          # Dashboard rendering
|   |-- app-release.js            # Release notes parser
|   |-- app-modal.js              # Modal renderers
|   |-- app-changelog.js          # Changelog page renderer
|   |-- style.css                 # Dark theme design system (~2500 lines)
|
|-- data/
|   |-- core.json                 # Dashboard metadata (date, last_run, lastChecked)
|   |-- stats.json                # Dashboard statistics (total_bundles, total_apps, etc.)
|   |-- changes.json              # Today's affected bundles
|   |-- bundles.json              # Full snapshot data for frontend rendering
|   |-- changelog.json            # Root-level copy of output/changelog.json
|   |
|   |-- raw/
|   |   |-- tree.json             # GitHub Git Trees API output (file listing of bundles repo)
|   |   |-- parsed_bundles.json   # Parsed + fingerprinted bundle/app/patch data (with icon_url)
|   |   |-- diff_result.json      # Current pipeline run's diff (affected_bundles with badge_type)
|   |   |-- bundles/              # Downloaded raw bundle JSONs (gitignored)
|   |
|   |-- state/
|   |   |-- current_snapshot.json # Most recent parse result (with fingerprints)
|   |   |-- previous_snapshot.json# Snapshot before current (for diffing)
|   |   |-- previous_snapshot.json# Snapshot before current (for diffing)
|   |   |-- daily_buffer.json     # Today's accumulated changes
|   |   |-- last_run.json         # Pipeline metadata (errors, counts, timestamps)
|   |   |-- app_cache.json        # Verified names/icons by package (Play, F-Droid, APKMirror)
|   |
|   |-- output/
|       |-- changelog.json        # Structured changelog (array of day entries)
|       |-- changelog.md          # Markdown changelog (human-readable)
|
|-- scripts/
    |-- run_pipeline.py           # Orchestrator (steps 1-9)
    |-- state_manager.py          # File I/O, dir management, snapshot rotation
    |-- fetch_patch_tree.py       # Step 1-2: crawl GitHub Git Trees API
    |-- download_bundles.py       # Step 3: download & validate Morphe bundle JSONs
    |-- parse_bundles.py          # Step 4: extract apps, patches, repo URLs + icon enrichment
    |-- icon_fetcher.py           # Play Store icon scraper + fallback orchestration
|-- fdroid_source.py          # F-Droid metadata fallback
|-- apkmirror_source.py       # APKMirror search fallback (title-verified)
    |-- fingerprint_engine.py     # Step 5: SHA-256 fingerprinting
    |-- diff_engine.py            # Step 6: compare old vs new fingerprints
    |-- merge_daily_buffer.py     # Step 7-8: buffer, finalize day, update data files
    |-- generate_site.py          # Step 9: sync static files to docs root
```

---

## Pipeline Data Flow

```
UPSTREAM REGISTRY                          LOCAL DISK                        WEB UI
(Jman-Github/                              (Fresh Morphe repo)
 ReVanced-Patch-Bundles)

bundles branch
  |
  |--[1] fetch_patch_tree.py-------------> data/raw/tree.json
  |       (GitHub Git Trees API)
  |
  |--[2] download_bundles.py-------------> data/raw/bundles/<name>/<ch>/*.json
  |       (raw.githubusercontent.com)       (patches-bundle + patches-list)
  |
  |--[3] parse_bundles.py----------------> data/raw/parsed_bundles.json
  |       (compatiblePackages, repo URLs)   (each app gets icon_url from icon_fetcher.py)
  |       + enrich with Play Store icons
  |
  |--[4] fingerprint_engine.py-----------> (fingerprint fields added)
  |
  |--[5] diff_engine.py------------------> data/raw/diff_result.json
  |       (compare vs previous_snapshot)   (affected_bundles with badge_type)
  |
  |--[6] merge_daily_buffer.py-----------> data/state/daily_buffer.json
  |       (accumulate changes)
  |
  |       +-- day rollover? ------------> data/output/changelog.json (append)
  |       |                               data/output/changelog.md (prepend)
  |
  |       +-- always: -------> data/state/current_snapshot.json (updated)
  |                            data/core.json, data/stats.json, data/changes.json, data/bundles.json
  |
  |--[7] sync static files to docs root
  |
  +--[8] Browser loads JS modules -----> fetch data/core.json + data/stats.json + data/changes.json + data/bundles.json + data/changelog.json
        + Service Worker caches both      -> render dashboard / changelog
          (stale-while-revalidate)
```

---

## Script-by-Script Breakdown

### 1. `state_manager.py` (128 lines)

**Role:** Core I/O layer — all file read/write goes through this module.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `ensure_dirs()` | Creates all directories under `data/` |
| `load_json(path, default)` | JSON load with error handling, returns default on failure |
| `save_json(path, data)` | Atomic write via `.tmp` temp file + rename |
| `save_new_snapshot(data)` | Moves current to previous, saves new current |
| `load_current_snapshot()` | Loads `data/state/current_snapshot.json` |
| `load_previous_snapshot()` | Loads `data/state/previous_snapshot.json` |
| `load_daily_buffer()` / `save_daily_buffer()` | Daily buffer I/O |
| `save_last_run()` / `load_last_run()` | Pipeline metadata I/O |
| `save_core_json()` / `load_core_json()` | Dashboard metadata I/O |
| `save_stats_json()` / `load_stats_json()` | Dashboard statistics I/O |
| `save_changes_json()` / `load_changes_json()` | Today's changes I/O |
| `save_bundles_json()` / `load_bundles_json()` | Full snapshot I/O |

---

### 2. `fetch_patch_tree.py` (57 lines)

**Role:** Crawls the upstream registry file tree.

**What it does:**
- Calls GitHub Git Trees API on the `bundles` branch of `Jman-Github/ReVanced-Patch-Bundles`
- Recursively fetches the full tree
- Filters to only `patch-bundles/` directory blobs
- Saves all file metadata to `data/raw/tree.json`
- Uses `GITHUB_TOKEN` env var for auth, 3 retries with exponential backoff

---

### 3. `download_bundles.py` (209 lines)

**Role:** Downloads actual bundle JSON files from the registry.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `group_tree_files(tree_files)` | Parses flat tree into `{bundle: {channel: {bundle_path, list_path}}}`. Handles both nested folder layout (`my-bundle/stable/`) and flat naming (`my-bundle-stable-patches-bundle.json`) |
| `download_file_with_retry(path)` | Downloads from `raw.githubusercontent.com`, 3 retries |
| `is_morphe_bundle(bundle_json)` | Checks if `download_url` ends with `.mpp` AND has >= 8 path segments |
| `download_all_bundles()` | Orchestrator: clears raw dir, iterates groups, downloads + validates + saves both `patches-bundle.json` and `patches-list.json` |

---

### 4. `parse_bundles.py` (320 lines)

**Role:** Transforms raw downloads into structured bundle records with app icon enrichment.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `get_app_name(package_name)` | Maps known package names to friendly names (e.g., `com.google.android.youtube` -> `YouTube`). Falls back to heuristic from last path segment |
| `extract_repo_url(bundle_json, name)` | Extracts repo URL from `download_url`, `patches.url`, `integrations.url`, or `description` |
| `_extract_versions(raw)` | Normalizes version entries (handles both strings and objects with `version` key) |
| `validate_and_parse_bundle(name, channel)` | Validates files, extracts `compatiblePackages`, builds structured records with patch details |
| `parse_all_bundles()` | Orchestrator: iterates all downloaded bundles, calls `enrich_parsed_bundles_with_icons()`, saves to `parsed_bundles.json` |

**Output data shape (`parsed_bundles.json`):**
```json
{
  "bundle-name:stable": {
    "bundle": "bundle-name",
    "channel": "stable",
    "repo_url": "https://github.com/username/revanced-patches",
    "created_at": "2026-06-17T06:17:24",
    "apps": [
      {
        "app_name": "YouTube",
        "package": "com.google.android.youtube",
        "icon_url": "https://play-lh.googleusercontent.com/...",
        "patches": [
          {
            "name": "Hide Shorts",
            "description": "Hides Shorts tab",
            "use": true,
            "options": [{"key": "opt1", "description": "..."}],
            "compatible_versions": ["18.01.32", "18.02.33"]
          }
        ]
      }
    ],
    "fingerprint": "abc123..."
  }
}
```

The registry entry is populated by `icon_fetcher.py` during parsing. Store fallbacks run in order: Play Store (`og:image`), F-Droid, APKMirror search, then manual `data/state/icon_overrides.json`; unresolved apps get an initial-letter tile in the UI.

---

### 5. `icon_fetcher.py`

**Role:** Resolves app names and icons, with store fallbacks.

**How it works:**
- Play Store: requests `https://play.google.com/store/apps/details?id={package_name}` with a browser User-Agent and extracts `og:image`/`og:title`
- F-Droid: `fdroid_source.py` reads the package page for names and repo icons
- APKMirror: `apkmirror_source.py` reads the public search page and accepts a row only when its title matches the name derived from the package id
- Manual overrides in `data/state/icon_overrides.json` win over every automatic source
- Results cached in `data/state/app_cache.json`; malformed `icon_url` values are normalized to `""`
- `retry_empty_icons(max_packages=40)` retries the oldest empty entries each run
- Batch-enriches all apps after parsing via `enrich_parsed_bundles_with_icons()` and the fallback helpers

---

### 6. `fingerprint_engine.py` (83 lines)

**Role:** Generates SHA-256 fingerprints for change detection.

**How it works:**
- Canonicalizes each bundle's content: sorts patches by name, options by key, versions alphabetically
- Serializes to deterministic JSON
- SHA-256 hashes the canonical JSON
- Stores the fingerprint in the `parsed_bundles.json` record

---

### 7. `diff_engine.py` (151 lines)

**Role:** Compares current parse result against the previous snapshot.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `apps_are_different(old_app, new_app)` | Deep comparison: normalizes both apps' patches, options, versions; compares canonical JSON |
| `diff_snapshots()` | Loads old + new snapshots, iterates bundle:channel keys |

**Output (`diff_result.json`) — uses `affected_bundles` format:**
```json
{
  "affected_bundles": [
    {
      "bundle": "bundle-name",
      "channel": "stable",
      "badge_type": "NEW BUNDLE",
      "apps": [
        {"app_name": "YouTube", "package": "com.google.android.youtube", "badge_type": "NEW APP"}
      ]
    }
  ]
}
```

Returns `False` if no changes detected (pipeline exits silently).

---

### 8. `merge_daily_buffer.py` (382 lines)

**Role:** The most complex script — manages daily change accumulation, day rollover, and all output file generation.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `merge_apps_with_status(app_list, status)` | Merges apps into buffer with status precedence: new > updated > removed |
| `build_changelog_entry(date_str, affected_bundles_dict)` | Creates `{date, lastChecked, affected_bundles[]}` for JSON changelog |
| `generate_markdown_changelog(date_str, affected_bundles_dict)` | Generates Markdown with NEW/UPDATED/PRE-RELEASE badges |
| `finalize_buffer(buffer_data)` | Finalizes a 24h window: appends to `changelog.json`, prepends to `changelog.md`, writes data files |
| `update_data_files(today, buffer, snapshot)` | Updates data files with current snapshot + today's changes |
| `update_daily_buffer_run()` | Main entry point: handles day rollover, merges diff, saves snapshot, triggers notification |

**Status precedence logic:**
- `NEW APP` overrides `UPDATED APP` or `REMOVED APP`
- `UPDATED APP` overrides `REMOVED APP`
- `NEW BUNDLE` overrides `UPDATED`

**Data files output shape:**
```json
{
  "date": "2026-06-21",
  "last_run": "2026-06-21T12:01:00",
  "lastChecked": "2026-06-21T12:01:00",
  "stats": {
    "total_bundles": 59,
    "total_apps": 291,
    "new_apps_today": 14,
    "new_bundles_today": 2
  },
  "changes": {
    "affected_bundles": [
      {
        "bundle": "bundle-name",
        "channel": "stable",
        "badge_type": "UPDATED",
        "apps": [
          {"app_name": "YouTube", "package": "com.google.android.youtube", "badge_type": "NEW APP", "icon_url": "https://..."}
        ]
      }
    ]
  },
  "bundles": { ... }
}
```

---

### 9. `generate_site.py` (67 lines)

**Role:** Syncs static files to the docs root. Reads files from disk and writes them
back (preserving them during CI checkouts). Copies `changelog.json` to `data/changelog.json`.

This is **not** a template engine — the actual working static files are maintained
directly on disk. The script exists to ensure files survive a fresh GitHub Actions checkout.

---

### 10. `run_pipeline.py` (65 lines)

**Role:** Orchestrator — runs all pipeline steps in order.

**Execution flow:**
1. `ensure_dirs()`
2. `fetch_bundle_tree()` -> tree.json
3. `download_all_bundles()` -> raw bundles
4. `parse_all_bundles()` -> parsed_bundles.json (with icon enrichment)
5. `generate_bundle_fingerprints()` -> adds fingerprints
6. `diff_snapshots()` -> diff_result.json
7. If no changes AND no day rollover needed -> **exit silently**
8. `update_daily_buffer_run()` -> buffer, snapshots, data files
9. `generate_static_files()` -> sync files to docs root

---

## Frontend Architecture

### Entry Points

| File | Route | What it does |
|------|-------|--------------|
| `index.html` | `/` (Dashboard) | Registers Service Worker -> loads `app.js` -> `initDashboard()` |
| `changelog.html` | `/changelog.html` | Registers Service Worker -> loads `app.js` -> `initChangelog()` |

### Service Worker (`sw.js`)

- **Cache strategy:** Stale-while-revalidate for `data/core.json`, `data/stats.json`, `data/changes.json`, `data/bundles.json`, and `data/changelog.json`
- **Static assets:** Cache-first (HTML, CSS, JS, fonts)
- **Background refresh:** When fresh data is fetched, posts `DATA_UPDATED` message to all clients
- **On message:** Dashboard re-fetches and re-renders stats, updates, and bundle grid
- **On message:** Changelog page re-invokes `initChangelog()`
- **Cache versioning:** Cache named `fresh-morphe-v1`, old caches purged on activate

### `app.js` Key Functions

| Function | Trigger | What it renders |
|----------|---------|-----------------|
| `initDashboard()` | `DOMContentLoaded` + `#nav-dashboard.active` | Fetches data files, renders stats, today's updates, bundle grid with filters |
| `initChangelog()` | `DOMContentLoaded` + `#nav-changelog.active` | Fetches `data/changelog.json` + data files, renders historical changelog |
| `renderStats(data)` | Called by `initDashboard` | Populates 4 stat cards from `data.stats` |
| `renderTodayUpdates(data)` | Called by `initDashboard` | Groups `changes.affected_bundles` by bundle name, renders with status badges + app icons |
| `filterAndRenderBundles()` | Called by `initDashboard` + filter events | Groups bundle:channel entries by base name, applies search/channel filters, sorts, renders expandable cards with app icons, version chips, patch details |
| `renderChangelog(changelog, bundlesData)` | Called by `initChangelog` | Iterates changelog entries, groups by bundle, renders cards with date headers, status badges, app icons |
| `getAppIconHtml(iconUrl, sizeClass)` | Utility | Generates `<img>` tag with `onerror="this.remove()"` fallback |
| `groupAffectedBundles(affectedBundles)` | Shared utility | Groups bundles, merges channels, deduplicates apps with status precedence |
| `getAuthorLink(repoUrl)` | Utility | Extracts GitHub/GitLab author from repo URL, generates `<a>` tag |
| `getRepoInfo(repoUrl)` | Utility | Extracts `{isGitLab, path}` from repo URL |
| `isAppPreRelease(bundleName, pkgName, bundlesData)` | Utility | Returns true if app is in dev channel but NOT in stable channel |

### App Icons

App icons are loaded from Google Play Store (`icon_url` field fetched by `icon_fetcher.py`).
The `getAppIconHtml()` function generates an `<img>` tag that:
- Uses the pre-fetched `icon_url` from the data
- Has `onerror="this.remove()"` to cleanly remove broken images
- Uses `loading="lazy"` for deferred loading
- Has `alt=""` for accessibility (decorative images)
- Supports three size classes: `app-icon` (20px), `app-icon-lg` (28px), `app-icon-modal` (32px)

### Bundle Card Rendering (Dashboard)

Each bundle card displays:
1. **Header**: Bundle name + channel badges (green=stable, amber=dev) + GitHub/GitLab icon
2. **Summary**: "N compatible apps"
3. **Expandable drawer**: App mini-cards with icons, version chips, patch counts
4. **"Add to Morphe" button**: Links to `https://morphe.software/add-source?github=<path>` or `?gitlab=<path>`

Bundle sort priority:
```
[morphe, piko, rookieenough, hoo-dles, paresh-maheshwari, brosssh, patcheddit]
```
Remaining bundles sorted by app count descending, then alphabetically.

### Changelog Page Rendering

Each day card shows:
1. **Date header** (e.g., "June 21, 2026")
2. **Per-bundle groups** with status badges
3. **App list**: Status badge + app icon + app name + bundle name
4. Apps sorted: NEW APP first, then UPDATED APP, then REMOVED APP

---

## Badge Reference

| Badge | CSS class | Color | Purpose |
|-------|-----------|-------|---------|
| NEW BUNDLE | `.badge-new-bundle` | Purple | A bundle appeared for the first time |
| UPDATED | `.badge-updated-bundle` | Blue | An existing bundle received app changes |
| NEW APP | `.badge-new` | Green | A new app was added to a bundle |
| UPDATED APP | `.badge-updated` | Blue | An existing app's patches/options changed |
| REMOVED APP | `.badge-removed` | Red | An app was removed from a bundle |
| PRE-RELEASE | `.badge-pre-release` | Amber | App exists in dev channel but not stable |

---

## Key Data Shapes

### `data/core.json` (Dashboard Metadata)
```json
{
  "date": "2026-06-21",
  "last_run": "2026-06-21T12:01:00",
  "lastChecked": "2026-06-21T12:01:00"
}
```

### `data/stats.json` (Dashboard Statistics)
```json
{
  "total_bundles": 59,
  "total_apps": 291,
  "new_apps_today": 14,
  "new_bundles_today": 2
}
```

### `data/changes.json` (Today's Changes)
```json
{
  "affected_bundles": [
    {
      "bundle": "bundle-name", "channel": "stable",
      "badge_type": "UPDATED",
      "apps": [{"app_name": "YouTube", "package": "com.google.android.youtube", "badge_type": "NEW APP", "icon_url": "https://..."}]
    }
  ]
}
```

### `data/bundles.json` (Full Snapshot)
The complete bundle database, keyed by `bundle-name:channel`, with full app/patch/version data and release notes.

### `data/output/changelog.json` (Historical)
```json
[
  { "date": "2026-06-21", "lastChecked": "...", "affected_bundles": [...] },
  { "date": "2026-06-20", "lastChecked": "...", "affected_bundles": [...] }
]
```

### `data/state/daily_buffer.json` (Accumulation Buffer)
```json
{
  "date": "2026-06-21",
  "affected_bundles": {
    "bundle-name:stable": {"bundle": "name", "channel": "stable", "badge_type": "UPDATED", "apps": [...]}
  }
}
```

---

## CSS Architecture (`assets/style.css`)

The design system uses CSS custom properties with a dark theme:

```
:root ──> --bg-main (dark navy #0b0f19)
       ──> --bg-card (semi-transparent glass)
       ──> --primary-accent (#6366f1 indigo)
       ──> --color-stable (#10b981 green)
       ──> --color-dev (#f59e0b amber)
       ──> --color-critical (#f43f5e red)
       ──> --color-normal (#3b82f6 blue)
       ──> --color-rare (#8b5cf6 purple)
```

Key layout features:
- **Glow orbs**: Fixed background blur elements (indigo + purple)
- **Glassmorphism**: Cards use `backdrop-filter: blur(10px)` with semi-transparent backgrounds
- **Responsive grid**: 1 col mobile -> 2 col tablet -> 3 col desktop
- **Bundle cards**: Clickable, expandable with smooth `max-height` transition
- **App icons**: 20px rounded images with `object-fit: cover` and flex-shrink for graceful removal
- **Patch items**: Expandable to show description + options

---

## Development Notes

### Running the Pipeline Locally

```bash
pip install -r requirements.txt
set GITHUB_TOKEN=your_github_token
set TG_TOKEN=your_telegram_token
set TG_CHAT=your_chat_id
python scripts/run_pipeline.py
```

### Running a Local Dev Server

```bash
python -m http.server 8080
```

Then open `http://localhost:8080` (required for Service Worker, which does not work with `file://`).

### Key Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `GITHUB_TOKEN` | Yes | GitHub API auth for fetching tree |
| `TG_TOKEN` | No (optional) | Telegram bot token for notifications |
| `TG_CHAT` | No (optional) | Telegram chat ID for notifications |

### Manual Finalization

```bash
python scripts/merge_daily_buffer.py --finalize
```

Forces the current daily buffer to finalize immediately (creates changelog entry, triggers notification), even if there's no day rollover.

### Icon Cache

App metadata is cached in `data/state/app_cache.json` (Play/F-Droid/APKMirror names and icons), with per-source lookup caches in `fdroid_cache.json` and `apkmirror_cache.json`. To clear the registry and force re-fetch:

```bash
echo {} > data/state/app_cache.json
```

The cache is checked before each pipeline run so previously fetched icons are not re-scraped. Add manual fixes to `data/state/icon_overrides.json`, which take precedence over all automatic sources.
