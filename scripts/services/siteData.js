/**
 * Boot data source: phone API first, static JSON snapshot as fallback.
 *
 * Both sources return the same shapes the rest of the app already expects.
 * A failed API request is never fatal - the previous static Pages deployment
 * remains a complete, read-only site.
 */
import { fetchJson } from '../utils/http.js'
import { API_ENABLED, API_HEADERS, apiUrl, fallbackUrl } from '../config.js'
import { devLog, devTime } from '../utils/devLog.js'
import { isApiAvailable, markApiDown } from './apiState.js'

const API_TIMEOUT_MS = 5000
const CHANGELOG_TIMEOUT_MS = 30000
const STATIC_TIMEOUT_MS = 15000

async function loadApi() {
  const common = { timeoutMs: API_TIMEOUT_MS, cache: 'no-cache', headers: API_HEADERS }
  const [core, stats, changes, appCache, repoCache, index] = await Promise.all([
    fetchJson(apiUrl('/v1/core'), undefined, common),
    fetchJson(apiUrl('/v1/stats'), undefined, common),
    fetchJson(apiUrl('/v1/changes'), undefined, common),
    fetchJson(apiUrl('/v1/app-cache'), {}, { timeoutMs: API_TIMEOUT_MS, headers: API_HEADERS }),
    fetchJson(apiUrl('/v1/repo-cache'), {}, { timeoutMs: API_TIMEOUT_MS, headers: API_HEADERS }),
    fetchJson(apiUrl('/v1/index'), undefined, common),
  ])
  return { source: 'api', core, stats, changes, appCache, repoCache, index }
}

async function loadStatic() {
  const [core, stats, changes, appCache, repoCache, index] = await Promise.all([
    fetchJson(fallbackUrl('core.json'), undefined, { timeoutMs: STATIC_TIMEOUT_MS, cache: 'no-cache' }),
    fetchJson(fallbackUrl('stats.json'), undefined, { timeoutMs: STATIC_TIMEOUT_MS, cache: 'no-cache' }),
    fetchJson(fallbackUrl('changes.json'), undefined, { timeoutMs: STATIC_TIMEOUT_MS, cache: 'no-cache' }),
    fetchJson(fallbackUrl('state/app_cache.json'), {}, { timeoutMs: STATIC_TIMEOUT_MS }),
    fetchJson(fallbackUrl('state/repo_cache.json'), {}, { timeoutMs: STATIC_TIMEOUT_MS }),
    fetchJson(fallbackUrl('bundles/_index.json'), undefined, { timeoutMs: STATIC_TIMEOUT_MS }),
  ])
  return { source: 'static', core, stats, changes, appCache, repoCache, index }
}

/**
 * @returns {Promise<{source: 'api'|'static', core: Object, stats: Object,
 *   changes: Object, appCache: Object, repoCache: Object, index: Object}>}
 */
export async function loadSiteData() {
  const done = devTime('site data')
  if (API_ENABLED) {
    try {
      const data = await loadApi()
      devLog('site data source', 'api', Object.keys(data.index || {}).length, 'bundles')
      done({ source: 'api' })
      return data
    } catch (err) {
      markApiDown()
      console.warn('[siteData] API unavailable, falling back to static data:', err.message)
      devLog('API fallback reason', err.message)
    }
  }
  const data = await loadStatic()
  devLog('site data source', 'static', Object.keys(data.index || {}).length, 'bundles')
  done({ source: 'static' })
  return data
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
        devLog('changelog source', 'api', data.length, 'days')
        done({ source: 'api' })
        return data
      }
    } catch (err) {
      console.warn('[siteData] API changelog unavailable, using static:', err.message)
      devLog('changelog API fallback reason', err.message)
    }
  }
  const data = await fetchJson(fallbackUrl('changelog.json'), [], { timeoutMs: STATIC_TIMEOUT_MS })
  devLog('changelog source', 'static', Array.isArray(data) ? data.length : 0, 'days')
  done({ source: 'static' })
  return Array.isArray(data) ? data : []
}
