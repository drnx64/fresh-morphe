/**
 * On-demand bundle file loader.
 *
 * The site boots from `data/bundles/_index.json` alone (card metadata +
 * package names). Per-app `patches` live in `data/bundles/<name>_<channel>.json`
 * and are fetched only when something actually reads them — app detail modal,
 * bundle page, card expansion. Records are merged into the store's `bundles`
 * object in place, so `buildAppIndex`'s identity-keyed cache stays valid and
 * no page re-renders behind a modal.
 */
import * as store from '../store.js'
import { fetchWithTimeout } from '../utils/http.js'
import { devLog } from '../utils/devLog.js'
import { CHANNELS, makeBundleKey, parseBundleKey } from '../utils/bundleKey.js'
import { API_ENABLED, API_HEADERS, apiUrl, fallbackUrl } from '../config.js'
import { isApiAvailable } from './apiState.js'

const DETAIL_TIMEOUT_MS = 10000

/** In-flight fetches, keyed by bundle key (single-flight). */
const pending = new Map()

let detailsLoadedListener = null

/**
 * Register a callback fired after any bundle detail lands in the store —
 * app.js uses it to persist newly-warmed records into IndexedDB.
 * @param {((key: string) => void)|null} fn
 */
export function onBundleDetailsLoaded(fn) {
  detailsLoadedListener = fn
}

/**
 * @param {string} key
 * @returns {boolean} true when the record has no per-app patch data yet
 */
export function isBundlePartial(key) {
  const record = (store.get('bundles') || {})[key]
  return !record || record._partial === true
}

function bundleFileUrl(key) {
  return fallbackUrl(`bundles/${key.replace(':', '_')}.json`)
}

function bundleApiUrl(key) {
  const { name, channel } = parseBundleKey(key)
  return apiUrl(`/v1/bundles/${encodeURIComponent(name)}/${encodeURIComponent(channel)}`)
}

async function loadKey(key) {
  const bundles = store.get('bundles') || {}
  const sources = API_ENABLED && isApiAvailable()
    ? [{ url: bundleApiUrl(key), headers: API_HEADERS }, { url: bundleFileUrl(key) }]
    : [{ url: bundleFileUrl(key) }]
  let lastError = null
  devLog('bundle detail load', key, sources.length, 'sources')

  for (const source of sources) {
    try {
      const init = source.headers ? { headers: source.headers } : {}
      const resp = await fetchWithTimeout(source.url, init, DETAIL_TIMEOUT_MS)
      if (!resp.ok) throw new Error(`${resp.status}`)
      const data = await resp.json()
      if (data && typeof data === 'object' && data.apps) {
        // Index carries card metadata the file lacks (stars, isArchived, …)
        const merged = { ...(bundles[key] || {}), ...data, _partial: false }
        bundles[key] = merged
        devLog('bundle detail loaded', key, data.apps.length, 'apps')
        detailsLoadedListener?.(key)
        return merged
      }
      throw new Error('unexpected payload')
    } catch (err) {
      lastError = err
      devLog('bundle detail source failed', source.url, err.message)
    }
  }

  console.warn('[bundleLoader] failed to load', key, lastError?.message || 'unknown error')
  // Best effort: index-only (or previously cached) record keeps the UI usable
  return bundles[key] || null
}

/**
 * Ensure a single bundle key has full detail, with single-flight de-duping.
 * @param {string} key - canonical `name:channel`
 * @returns {Promise<Object|null>} the record (possibly still index-only)
 */
export function ensureBundleKey(key) {
  if (!isBundlePartial(key)) return Promise.resolve((store.get('bundles') || {})[key])
  if (pending.has(key)) return pending.get(key)
  const promise = loadKey(key).finally(() => pending.delete(key))
  pending.set(key, promise)
  return promise
}

/**
 * Ensure every listed bundle key has full detail.
 * @param {string[]} keys
 * @returns {Promise<Object[]>}
 */
export function ensureBundleKeys(keys) {
  return Promise.all(keys.filter(Boolean).map(ensureBundleKey))
}

/**
 * Ensure every existing channel of a bundle has full detail.
 * @param {string} name - bundle name (without channel)
 * @param {string[]} [channels] - defaults to all channels present in the store
 * @returns {Promise<Object[]>}
 */
export function ensureBundleChannels(name, channels = []) {
  const bundles = store.get('bundles') || {}
  const wanted = []
  for (const channel of (channels.length ? channels : CHANNELS)) {
    const key = makeBundleKey(name, channel)
    if (bundles[key]) wanted.push(key)
  }
  return ensureBundleKeys(wanted)
}

/**
 * Ensure every bundle record containing `pkg` has full detail — the app
 * detail modal renders patches for each of them.
 * @param {string} pkg - package name
 * @param {{ excludeChannels?: string[] }} [opts]
 * @returns {Promise<Object[]>}
 */
export function ensureAppBundles(pkg, { excludeChannels = [] } = {}) {
  const bundles = store.get('bundles') || {}
  const keys = []
  for (const [key, record] of Object.entries(bundles)) {
    if (excludeChannels.includes(parseBundleKey(key).channel)) continue
    if (!record._partial) continue
    const apps = record.apps || []
    if (apps.some((a) => (typeof a === 'string' ? a : a.package) === pkg)) keys.push(key)
  }
  return ensureBundleKeys(keys)
}
