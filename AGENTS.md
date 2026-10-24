# MorpheTracker — Agent Reference

## Commands

| Task | Command | Notes |
|------|---------|-------|
| Dev server | `npm run dev` | `npx serve .` on port 3000. Never use `file://`. |
| Build | `npm run build` | `node scripts/build.js` — copies to `docs/` (gitignored build output, pushed to `gh-pages` by CI). |
| Preview | `npm run preview` | `npx serve docs` — preview production build locally. |
| Python tests | `python -m pytest tests/ -v` | Requires `pip install -r requirements.txt`. |
| Run pipeline | `python scripts/run_pipeline.py` | Requires `GITHUB_TOKEN` env var. |
| Manual finalization | `python scripts/merge_daily_buffer.py --finalize` | Force-flush daily buffer to changelog. |
| Verify/purge phantoms | `python scripts/cleanup_phantoms.py` | Idempotent — re-run after changing REMOVED/claims logic. |

### Verification order

`python -m pytest tests/ -v` → `npm run build`

CI runs both on every push/PR to `main`. Data updates (hourly) use `[skip ci]` and deploy to `gh-pages` directly from `update.yml`.

## Architecture

- **Frontend:** Vanilla ES Modules (`scripts/`). No build step, no framework. Hash-based SPA routing (`/#/`, `/#/bundles`).
- **Pipeline:** Python 3.11 scripts (`scripts/`). Crawls GitHub for `.mpp` patch bundles, parses, fingerprints, diffs, writes JSON to `data/`. Zero pip dependencies (stdlib `urllib` only; `Pillow` for image processing, `pytest` for tests).
- **Data layer:** Pipeline outputs static JSON files (`data/core.json`, `data/changes.json`, `data/bundles/`, etc.). Build script copies them to `docs/`, which is pushed to the `gh-pages` branch for GitHub Pages.
- **Hosting:** GitHub Pages serves the `gh-pages` branch (root). CI (GitHub Actions) runs pipeline every hour, commits data changes to `main`, then builds and force-pushes the site to `gh-pages`. Code pushes to `main` trigger the same build+push via `deploy.yml`. The `gh-pages` branch is fully automated — never edit it manually.

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
- **CSS is one monolithic file** (`assets/style.css`, ~4400 lines). No CSS modules.
- **SVG icons** are inline strings exported from `scripts/utils/svg.js`. No icon font or library.
- **Python imports** use `sys.path.append` — scripts must run from repo root.
- **No node_modules.** Zero npm dependencies. `npx serve` used for dev/preview only.
- **Bundle keys are `repo:channel` with channels `stable | latest | dev`.** Always go through `scripts/utils/bundleKey.js` (`parseBundleKey`, `makeBundleKey`, `pickChannel`, `findBundleRecord`) — never split on `:` ad hoc. `CHANNEL_PREFERENCE = ['dev','latest','stable']` for storage order; display picks `stable || latest || dev`. `morphe_hide_dev` hides dev only (`latest` stays visible); `.channel-badge.latest` uses `--state-plum`.
- **The frontend never hangs on a bad fetch.** `fetchJson(url, fallback)` always resolves; boot paints from the IndexedDB bundle cache (`services/bundleCache.js`, 24h TTL, version-delta) when the network fails or is slow; background work reports via `components/workToast.js` with Retry/Dismiss. Every floating promise needs a `.catch` — `window.onunhandledrejection` in `index.html` renders the full error page.
- **Icons warm lazily; display never waits on the cache.** `services/iconFetchQueue.js`: IntersectionObserver priority lane (icons scrolled into view) + paced sweep (6 per batch, 1–2s pauses), shared 429/403 exponential backoff in `iconCache.loadImage`, remaining queue persisted to localStorage. `renderAppIcon` serves warm WebP data URLs and tags cold icons `data-icon-url` for the observer.
- **`main` is PR-only (ruleset + required checks).** Never `git push` directly to `main` — rejected for everyone. Land changes: branch → `gh pr create` → `gh pr merge --auto --merge` (merge commit; waits for required checks `Python Tests` + `Production Build`). Never put `[skip ci]` in PR commits — required checks never report and auto-merge stalls. The sole direct pusher is `update.yml`, authenticating as the repo deploy key (secret `MORPHE_DEPLOY_KEY`, ruleset bypass actor `DeployKey` — the GitHub Actions app can't be a bypass actor on personal repos). Emergency direct push: `PUT /repos/{owner}/{repo}/rulesets/{id}` with `"enforcement": "disabled"` (resend the full definition — PUT replaces, it doesn't merge), push, re-enable.

## File structure

```
index.html                    — Entry point (vanilla HTML shell)
assets/style.css              — Monolithic CSS (dark theme)
scripts/
  app.js                      — Entry point: boot, router, data loading
  router.js                   — Hash-based SPA router
  store.js                    — Reactive pub/sub state
  ui.js                       — DOM helpers (el, html, mount)
  services/                   — Data fetch, icon/avatar cache, bundle cache, icon queue, IndexedDB
  pages/                      — Page renderers (dashboard, apps, changelog, diff)
  components/                 — Reusable UI (header, footer, modal, skeleton, work toast)
  utils/                      — SVG icons, formatting, URL helpers, bundle keys, misc
data/                         — Pipeline output (JSON files)
scripts/ (Python)             — Pipeline scripts (crawl, parse, diff, publish, cleanup)
tests/                        — pytest suite (pipeline invariants)
```

## Conventions

- Commit messages: conventional commits (`feat`, `fix`, `refactor`, `chore`, etc.) with scopes like `logic`, `ui`, `data`, `pipeline`.
- Vanilla JS: ES modules (`type="module"`), no framework, no build step.
- State management: `store.js` pub/sub pattern (get/set/subscribe).
- Routing: hash-based (`/#/path`), route matching with `:param` support.
- Dark theme only. Color vars in `:root` in `assets/style.css`.
