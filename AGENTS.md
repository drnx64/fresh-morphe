# Fresh Morphe — Agent Reference

## Commands

| Task | Command | Notes |
|------|---------|-------|
| Dev server | `npm run dev` | `npx serve .` on port 3000. Never use `file://`. |
| Build | `npm run build` | `node scripts/build.js` — copies to `docs/` (gitignored build output, pushed to `gh-pages` by CI). |
| Preview | `npm run preview` | `npx serve docs` — preview production build locally. |
| Python tests | `python -m pytest tests/ -v` | Requires `pip install -r requirements.txt`. |
| Run pipeline | `python scripts/run_pipeline.py` | Requires `GITHUB_TOKEN` env var. |
| Discover sources | `python scripts/discover_sources.py` | GitHub/GitLab search + verification; state in `data/state/discovered_repos.json`. |
| Manual finalization | `python scripts/merge_daily_buffer.py --finalize` | Force-flush daily buffer to changelog. |
| Verify/purge phantoms | `python scripts/cleanup_phantoms.py` | Idempotent — re-run after changing REMOVED/claims logic. |
| Migrate brand state | `python scripts/migrate_fresh_brand.py --no-db` | Rewrites `awesome` source labels to `fresh`; omit `--no-db` to move the SQLite file. |
| Phone API | `python -m server.api --host 127.0.0.1 --port 8090` | Read-only SQLite API (stdlib `ThreadingHTTPServer`); publicly fronted by ngrok. |
| Phone worker | `python -m server.worker --workers 4` | Incremental SQLite refresh from awesome-morphe; supports `--limit`, `--force`, `--dry-run`, `--json`, `--no-notify` (Discord + Telegram), `--no-telegram` (digest only). Posts one Discord message per run when `DISCORD_WEBHOOK_URL` is set, and one Telegram digest per UTC day when `TG_TOKEN`/`TG_CHAT` are set. |
| Phone pipeline | `sh deploy/termux/run-pipeline.sh` | Hourly full pipeline in `--db-only` mode (no static/RSS/Telegram), then imports JSON into SQLite. Lock at `.pipeline.lock`, logs to `logs/pipeline.log`. |
| Test Discord | `python -m server.notify --test` | Posts and edits one test message using `DISCORD_WEBHOOK_URL`. |
| Worker loop | `sh deploy/termux/worker-loop.sh` | Phone hourly loop; lock at `.worker.lock`, logs to `logs/worker.log`. |
| Phone watchdog | `sh deploy/termux/watchdog.sh` | Cron every 10 min; revives a dead API, ngrok, or worker loop. Silent when healthy, logs to `logs/watchdog.log`. |
| Import legacy data | `python -m server.import_legacy --reset` | Rebuild `data/fresh.db` from `data/` (falls back to `data/morphe.db` until migrated). |
| Export static API | `python -m server.static_export` | Writes `data/exports/v1/` for GitHub Pages fallback. |

### Verification order

`python -m pytest tests/ -v` → `npm run build`

CI runs both on every push/PR to `main`. Data updates (hourly) use `[skip ci]` and deploy to `gh-pages` directly from `update.yml`.

## Architecture

- **Frontend:** Vanilla ES Modules (`scripts/`). No build step, no framework. Hash-based SPA routing (`/#/`, `/#/bundles`).
- **Pipeline:** Python 3.11 scripts (`scripts/`). Crawls GitHub for `.mpp` patch bundles, parses, fingerprints, diffs, writes JSON to `data/`. Zero pip dependencies (stdlib `urllib` only; `Pillow` for image processing, `pytest` for tests).
- **Data layer:** Pipeline outputs static JSON files (`data/core.json`, `data/changes.json`, `data/bundles/`, etc.). Build script copies them to `docs/`, which is pushed to the `gh-pages` branch for GitHub Pages.
- **Hosting:** GitHub Pages serves the `gh-pages` branch (root). CI (GitHub Actions) runs pipeline every hour, commits data changes to `main`, then builds and force-pushes the site to `gh-pages`. Code pushes to `main` trigger the same build+push via `deploy.yml`. The `gh-pages` branch is fully automated — never edit it manually.
- **Phone backend:** `server/` imports legacy JSON into SQLite (`data/fresh.db`; legacy `data/morphe.db` is used until migrated), refreshes it incrementally from awesome-morphe, and serves a read-only API on `127.0.0.1:8090` via `ThreadingHTTPServer`; ngrok fronts it. `deploy/termux/` holds the phone boot/start scripts; `run-pipeline.sh` runs the full pipeline hourly in `--db-only` mode and syncs JSON into SQLite, while the incremental `worker-loop.sh` remains the default until the upstream Jman patch source is migrated. The frontend prefers the API and falls back to static `data/` (`public/config.js`); the Actions pipeline still maintains the static fallback.

### Data flow

```
FETCH → PARSE → DIFF → ACCUMULATE → PUBLISH
```

1. **FETCH**: `fetch_patch_tree.py` + `download_bundles.py` + `fetch_external_repos.py`
2. **PARSE**: `parse_bundles.py` + `fetch_patches_names.py`
3. **DIFF**: `diff_engine.py` (fingerprints + diff) — compares `current_snapshot.json` vs new parse
4. **ACCUMULATE**: `merge_daily_buffer.py` — daily changelog accumulation
5. **PUBLISH**: `generate_site.py` + `telegram.py` — static files, RSS, Telegram notifications

If no changes and no day rollover, pipeline exits silently after step 3. `write_data_files(has_changes=...)` controls what `changes.json` contains — empty when no changes.

**Removal safety:** `state_manager.load_claimed_keys()` (Jman tree + external repo index) gates every REMOVED — inputs missing or `archive_available` False suppresses removals wholesale; channel migrations are matched by `repo_slug` (moving `repo:dev` → `repo:stable` never reports REMOVED+NEW). `rebuild_snapshot_from_bundles()` reads only `_index.json`, and `save_bundles_split()` prunes orphan files on every write — invariant: bundle file count = index keys + `_index.json`. `tests/test_pipeline_stability.py` covers all of this; `scripts/cleanup_phantoms.py` re-verifies and purges historical phantoms.

## Key gotchas

- **`docs/` is the build output and is gitignored.** `npm run build` generates it. Never edit manually, never commit it. CI pushes it to the `gh-pages` branch.
- **`data/raw/` and `data/output/` are gitignored.** Large generated files.
- **`data/state/` is partially gitignored.** `current_snapshot.json`, `previous_snapshot.json`, `external_repos.json`, `last_tg_msg.json` are gitignored. Caches (`app_cache.json`, `daily_buffer.json`, `last_run.json`, `patches_names_cache.json`, `release_cache.json`) are tracked.
- **CSS is split into ordered files** (`assets/css/1-tokens.css` … `11-responsive.css`, loaded in numeric order in `index.html`). No CSS modules.
- **SVG icons** are inline strings exported from `scripts/utils/svg.js`. No icon font or library.
- **Python imports** use `sys.path.append` — scripts must run from repo root.
- **No node_modules.** Zero npm dependencies. `npx serve` used for dev/preview only.
- **Bundle keys are `repo:channel` with channels `stable | latest | dev`.** Always go through `scripts/utils/bundleKey.js` (`parseBundleKey`, `makeBundleKey`, `pickChannel`, `findBundleRecord`) — never split on `:` ad hoc. `CHANNEL_PREFERENCE = ['dev','latest','stable']` for storage order; display picks `stable || latest || dev`. `fresh_hide_dev` hides dev only (`latest` stays visible); `.channel-badge.latest` uses `--state-plum`.
- **The frontend never hangs on a bad fetch.** `fetchJson(url, fallback)` always resolves; boot paints from the IndexedDB bundle cache (`services/bundleCache.js`, 24h TTL, version-delta) when the network fails or is slow; background work reports via `components/workToast.js` with Retry/Dismiss. Every floating promise needs a `.catch` — `window.onunhandledrejection` in `index.html` renders the full error page.
- **API-first data has a static fallback.** `public/config.js` sets `apiBase`/`fallbackBase`; `services/siteData.js` tries the API then `data/`, marking the API down on failure (`services/apiState.js`). Ngrok requires `ngrok-skip-browser-warning: true`, so `services/config.js` adds it and `server/api.py` must allow it in CORS. The changelog is ~4.4 MB; `loadChangelog` uses `CHANGELOG_TIMEOUT_MS`, not the 5s default.
- **Don't match the ngrok process with `pgrep -x`.** Android truncates `comm` to a long path, so `pgrep -x ngrok` fails and `start-stack.sh` would start duplicates. Match `bin/ngrok http` in `ps`/`pgrep -f` instead.
- **The phone worker is SQLite-native, not a JSON pipeline.** `server/worker.py` reads the awesome-morphe mirror (upstream repo name; the discovery provider label is now `fresh`), hashes each `(repo, branch)` in `repo_sources`, maps `main`→`stable` and `dev`→`dev`, and never touches legacy `latest` rows. First successful run sets `meta.worker_bootstrap_done=1` and suppresses changelog entries; later source-hash changes emit real changelog items. Keep `meta.schema_version` at `1` when adding tables. Restart `server.api` after changing `server/` code — `start-stack.sh` only restarts a dead API.
- **The Jman patch source moved to a registry.** `Jman-Github/ReVanced-Patch-Bundles` now redirects to `Patch-Bundle-Registry`; its `bundles` branch no longer contains `patch-bundles/`. `fetch_patch_tree.py` fails fast on an empty tree to prevent bogus removals. Migrate `fetch_patch_tree.py`/`download_bundles.py` to the registry API (`https://patch-bundles.api.jmancentral.com`) before enabling the phone pipeline cron.
- **Daily changes are mirrored from JSON, not recomputed in SQLite.** `server.import_legacy.sync_changes()` upserts finalized `changelog.json` days and the current `changes.json` aggregate into the date-keyed `changes` table, updates `last_checked` on no-change scans, and refreshes `snapshots['changes']`. `scripts/merge_daily_buffer.py` resets `changes.json`/counter stats when the UTC date changes, even on silent runs. `data_version` includes `changes.json` and `changelog.json`.
- **Worker Discord notifications are one message per run.** `server/worker.py` posts “running”, then edits the same message by ID through `server/notify.py`; failures are logged but never fail the worker. `DISCORD_WEBHOOK_URL` is loaded from `.env` by `server/config.py` (shell env wins). Use `--no-notify` for manual runs or `python -m server.notify --test` to verify without touching a worker run.
- **Telegram is one digest per UTC day, shared by CI and the phone.** `scripts/telegram.py:send_or_edit` keys on the UTC day plus a content hash in `data/state/last_tg_msg.json`, so whichever caller runs first sends and the other edits or skips — never two posts. The worker builds the digest from its own accumulated day (`telegram_entries()` → `whats_new.generate_whats_new()`), because the phone pipeline runs `--db-only` and would otherwise never notify. The phone `.env` needs `TG_TOKEN` and `TG_CHAT`; without them `notify_telegram()` returns `False` silently. Bootstrap and `--limit` scans are excluded from the digest. `tests/conftest.py` strips all three notification credentials so a local `.env` can never post during tests.
- **Verbose dev logging is localhost-only.** Frontend: `scripts/utils/devLog.js` enables when `location.hostname` is loopback; override with `?debug=1`/`0` or localStorage `fresh_debug` (legacy `morphe_debug` still works). API: `server/devlog.py` enables for loopback `Host` headers; override with `FRESH_DEBUG=1`/`0` (legacy `MORPHE_DEBUG` still works). Production keeps existing warn/error logs, not per-request dev noise.
- **Termux shell scripts must use LF endings.** CRLF makes `sh` fail on the first `set -u` with `Illegal option -`; normalize any `deploy/termux/*.sh` before uploading. When killing phone processes over SSH, never let the `ps | awk` pattern match the SSH command itself.
- **Icon fallback order is Play → F-Droid → APKMirror → manual overrides → initial tile.** `scripts/apkmirror_source.py` accepts only search rows whose title matches the name derived from the package id, because APKMirror returns unrelated popular apps for unknown packages. Individual APKMirror pages are Cloudflare-blocked; only the search page is used. Manual URLs live in `data/state/icon_overrides.json` and win over every automatic source. Malformed `icon_url` values (legacy `{}`) are normalized by `app_metadata.load_cache()`/`normalize_cache()`, and `retry_empty_icons(max_packages=40)` retries the oldest empty entries each run.
- **Icons warm lazily; display never waits on the cache.** `services/iconFetchQueue.js`: IntersectionObserver priority lane (icons scrolled into view) + paced sweep (6 per batch, 1–2s pauses), shared 429/403 exponential backoff in `iconCache.loadImage`, remaining queue persisted to localStorage. `renderAppIcon` serves warm WebP data URLs and tags cold icons `data-icon-url` for the observer.
- **`main` is PR-only (ruleset + required checks).** Never `git push` directly to `main` — rejected for everyone. Land changes: branch → `gh pr create` → `gh pr merge --auto --merge` (merge commit; waits for required checks `Python Tests` + `Production Build`). Never put `[skip ci]` in PR commits — required checks never report and auto-merge stalls. The sole direct pusher is `update.yml`, authenticating as the repo deploy key (secret `FRESH_DEPLOY_KEY` (fallback `MORPHE_DEPLOY_KEY`), ruleset bypass actor `DeployKey` — the GitHub Actions app can't be a bypass actor on personal repos). Emergency direct push: `PUT /repos/{owner}/{repo}/rulesets/{id}` with `"enforcement": "disabled"` (resend the full definition — PUT replaces, it doesn't merge), push, re-enable.

## File structure

```
index.html                    — Entry point (vanilla HTML shell)
public/config.js              — API base / static fallback config (loaded before boot)
assets/css/                   — Ordered CSS files (tokens → base → chrome → pages → responsive)
scripts/
  app.js                      — Entry point: boot, router, data loading
  router.js                   — Hash-based SPA router
  store.js                    — Reactive pub/sub state
  ui.js                       — DOM helpers (el, html, mount)
  config.js                   — API URL, ngrok header, static fallback URL
  services/                   — Data fetch, icon/avatar cache, bundle cache, API state, releases
  pages/                      — Page renderers (dashboard, apps, changelog, diff, bundles)
  components/                 — Reusable UI (header, footer, modal, skeleton, release notes)
  utils/                      — SVG icons, formatting, URL helpers, bundle keys, HTTP
data/                         — Pipeline output (JSON files) and phone SQLite DB
server/                       — SQLite importer, incremental worker, read-only API, static export
deploy/termux/                — Phone boot/start scripts, worker loop, DB-only pipeline
scripts/ (Python)             — Pipeline scripts (crawl, parse, diff, publish, cleanup)
  discovery/                  — GitHub/GitLab source discovery + verification
tests/                        — pytest suite (pipeline and API invariants)
```

## Conventions

- Commit messages: conventional commits (`feat`, `fix`, `refactor`, `chore`, etc.) with scopes like `logic`, `ui`, `data`, `pipeline`.
- Vanilla JS: ES modules (`type="module"`), no framework, no build step.
- State management: `store.js` pub/sub pattern (get/set/subscribe).
- Routing: hash-based (`/#/path`), route matching with `:param` support.
- Dark theme only. Color vars in `:root` in `assets/style.css`.
