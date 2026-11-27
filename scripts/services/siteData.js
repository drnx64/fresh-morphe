/**
 * Boot data source: the phone API raced against the static JSON snapshot,
 * fetched in two waves.
 *
 * Wave 1 loads everything the landing page renders from (core, stats, today's
 * changes, the app cache, the bundle index — ~3 MB of it). Wave 2 loads the
 * repo cache (avatars, star metadata) only after wave 1 has settled, so the
 * 1.9 MB index never competes with it for bandwidth.
 *
 * Each wave runs both sources concurrently and the first to deliver every
 * file in the wave wins (Promise.any semantics: a source that fails keeps the
 * other one alive rather than deciding the wave). The loser's in-flight
 * requests are aborted, so the winner's bytes are all the visitor downloads.
 *
 * Both sources return the same shapes the rest of the app already expects.
 * A failed API request is never fatal - the previous static Pages deployment
 * remains a complete, read-only site.
 */
import { fetchJson } from '../utils/http.js'
import { API_ENABLED, API_HEADERS, apiUrl, fallbackUrl } from '../config.js'
import { devLog, devTime } from '../utils/devLog.js'
import { isApiAvailable, markApiDown, markApiUp } from './apiState.js'

const API_TIMEOUT_MS = 5000
const CHANGELOG_TIMEOUT_MS = 30000
const STATIC_TIMEOUT_MS = 15000

// Per-source request options. Always-fresh files revalidate (no-cache), the
// app/repo caches keep the browser cache, and only the API carries headers.
const API_COMMON = { timeoutMs: API_TIMEOUT_MS, cache: 'no-cache', headers: API_HEADERS }
const API_CACHED = { timeoutMs: API_TIMEOUT_MS, headers: API_HEADERS }
const STATIC_COMMON = { timeoutMs: STATIC_TIMEOUT_MS, cache: 'no-cache' }
const STATIC_CACHED = { timeoutMs: STATIC_TIMEOUT_MS }

/**
 * Sentinel fallback for `strict` files: fetchJson returns THIS exact
 * reference when a strict source has nothing, and the fetcher converts that
 * into a rejection so an empty answer can never win the race against real
 * data. Non-strict cache files keep the original per-call `{}` fallback
 * (a missing cache must not fail the wave).
 */
const EMPTY = {}

/**
 * Every boot file in both flavors.
 *   path    URL, relative to the API or the static fallback root
 *   opts    request options for that source (see the tables above)
 *   empty   return a fresh {} instead of throwing when the fetch fails
 *   strict  a fallback {} counts as a failure (identity-checked against EMPTY)
 */
const ENDPOINTS = {
  core: {
    api: { path: '/v1/core', opts: API_COMMON },
    static: { path: 'core.json', opts: STATIC_COMMON },
  },
  stats: {
    api: { path: '/v1/stats', opts: API_COMMON },
    static: { path: 'stats.json', opts: STATIC_COMMON },
  },
  changes: {
    api: { path: '/v1/changes', opts: API_COMMON },
    static: { path: 'changes.json', opts: STATIC_COMMON },
  },
  appCache: {
    api: { path: '/v1/app-cache', opts: API_CACHED, empty: true },
    static: { path: 'state/app_cache.json', opts: STATIC_CACHED, empty: true },
  },
  repoCache: {
    api: { path: '/v1/repo-cache', opts: API_CACHED, strict: true },
    static: { path: 'state/repo_cache.json', opts: STATIC_CACHED, strict: true },
  },
  index: {
    api: { path: '/v1/index', opts: API_COMMON },
    static: { path: 'bundles/_index.json', opts: STATIC_CACHED },
  },
}

const WAVE1_FILES = ['core', 'stats', 'changes', 'appCache', 'index']
const WAVE2_FILES = ['repoCache']

function fetchFrom(source, file, signal) {
  const e = ENDPOINTS[file][source]
  const fallback = e.strict ? EMPTY : e.empty ? {} : undefined
  const url = source === 'api' ? apiUrl(e.path) : fallbackUrl(e.path)
  return fetchJson(url, fallback, { ...e.opts, signal }).then((value) => {
    if (value === EMPTY) throw new Error(`${url} returned no data`)
    return value
  })
}

/**
 * Load one wave of files, racing the API against the static snapshot.
 *
 * The first source to deliver every file in the wave wins; the loser is
 * aborted so the winner's bytes are all the visitor downloads. apiState
 * bookkeeping: an API win marks the API up, a genuine API failure marks it
 * down, and a rejection caused by our own abort (the static side won) leaves
 * the breaker alone — a slow API is not a broken one.
 *
 * @param {string[]} files keys of ENDPOINTS
 * @param {string} label dev-log label
 * @returns {Promise<Object>} `{ source, ...values keyed by file }`
 */
async function loadWave(files, label) {
  const done = devTime(`site data ${label}`)
  const controller = new AbortController()
  const { signal } = controller

  const run = (source) => Promise.all(files.map((file) => fetchFrom(source, file, signal)))

  // First to SUCCEED wins: a source that fails is recorded and the other one
  // keeps going, matching the old sequential "API, else static" fallback.
  let apiError = null
  let staticError = null
  const racing = API_ENABLED && isApiAvailable()
  const apiWave = racing
    ? run('api').then(
        (values) => ({ source: 'api', values }),
        (err) => {
          apiError = err
          throw err
        },
      )
    : null
  const staticWave = run('static').then(
    (values) => ({ source: 'static', values }),
    (err) => {
      staticError = err
      throw err
    },
  )

  let winner
  try {
    winner = await Promise.any(apiWave ? [apiWave, staticWave] : [staticWave])
  } catch (err) {
    // Both sources failed (no abort has happened yet, so every error here is
    // genuine). The static error is what the old loader would have surfaced.
    if (apiError) markApiDown()
    throw staticError || apiError || err
  }

  // A winner exists — stop whatever is still in flight, then let the API
  // attempt settle so a genuine failure is told apart from our abort.
  controller.abort()
  if (winner.source === 'api') {
    markApiUp()
  } else if (apiWave) {
    const apiErr = await apiWave.then(
      () => null,
      (err) => err,
    )
    if (apiErr && apiErr.name !== 'AbortError') {
      markApiDown()
      console.warn('[siteData] API unavailable, falling back to static data:', apiErr.message)
      devLog('API fallback reason', apiErr.message)
    }
  }

  const data = { source: winner.source }
  files.forEach((file, i) => {
    data[file] = winner.values[i]
  })
  devLog('site data', label, 'source', winner.source, files.join(','))
  done({ source: winner.source })
  return data
}

/**
 * Boot data as two waves.
 *
 * wave 1 — everything the landing page needs; wave 2 — the repo cache, begun
 * only once wave 1 has settled so it never competes with the bundle index.
 * wave 2 never rejects: it resolves null when wave 1 fails (the boot error
 * page owns that path) or when both of its own sources come up empty.
 *
 * @returns {{ wave1: Promise<Object>, wave2: Promise<Object|null> }}
 */
export function loadSiteData() {
  const wave1 = loadWave(WAVE1_FILES, 'wave 1')
  const wave2 = wave1.then(
    () =>
      loadWave(WAVE2_FILES, 'wave 2').catch((err) => {
        console.warn('[siteData] repo cache unavailable, avatars will stay empty:', err.message)
        devLog('wave 2 failed', err.message)
        return null
      }),
    () => null,
  )
  return { wave1, wave2 }
}

/**
 * Full changelog for the changelog page and bundle history modal.
 * @returns {Promise<Object[]>}
 */
export async function loadChangelog() {
  const done = devTime('changelog load')
  if (API_ENABLED && isApiAvailable()) {
    try {
      const data = await fetchJson(apiUrl('/v1/changelog?limit=200'), undefined, {
        timeoutMs: CHANGELOG_TIMEOUT_MS,
        cache: 'no-cache',
        headers: API_HEADERS,
      })
      if (Array.isArray(data)) {
        markApiUp()
        devLog('changelog source', 'api', data.length, 'days')
        done({ source: 'api' })
        return data
      }
    } catch (err) {
      console.warn('[siteData] API changelog unavailable, using static:', err.message)
      devLog('changelog API fallback reason', err.message)
      markApiDown()
    }
  }
  const data = await fetchJson(fallbackUrl('changelog.json'), [], { timeoutMs: STATIC_TIMEOUT_MS })
  devLog('changelog source', 'static', Array.isArray(data) ? data.length : 0, 'days')
  done({ source: 'static' })
  return Array.isArray(data) ? data : []
}
