/**
 * Bundle state cache — one IndexedDB record holding the last known
 * _index.json + per-bundle records. Boots paint instantly from it while
 * the network revalidates (stale-while-revalidate).
 *
 * Records written here are mostly index-only (`_partial: true`); full detail
 * lands later via services/bundleLoader.js and is persisted on its own.
 * A content signature keeps repeated boots from rewriting the record, and the
 * first save after this change shrinks any legacy ~8 MB record to one
 * index-sized entry (the payload only gained a `signature` field).
 */
import { idbGet, idbSet } from './indexedDB.js'
import { devLog } from '../utils/devLog.js'

const CACHE_KEY = 'bundle_state_v1'
// Guards against files rewritten without a version bump (e.g. pipeline fixes)
const MAX_AGE_MS = 24 * 60 * 60 * 1000

/** Signature of the last state written (or read) this session. */
let lastSignature = null

/**
 * Cheap content signature: index shape, versions, and which records carry
 * full detail. Two boots with unchanged data produce the same string.
 * @param {Object} index
 * @param {Object} records
 * @returns {string}
 */
export function stateSignature(index, records) {
  const parts = [String(Object.keys(index || {}).length)]
  for (const [key, entry] of Object.entries(index || {})) {
    const record = records?.[key]
    parts.push(key, entry.version || '', record && !record._partial ? '1' : '0')
  }
  return parts.join('|')
}

/**
 * @returns {Promise<{index: Object, records: Object}|null>}
 */
export async function loadBundleState() {
  try {
    const cached = await idbGet(CACHE_KEY)
    if (!cached || typeof cached !== 'object') {
      devLog('bundle cache read', 'empty')
      return null
    }
    if (!cached.index || !cached.records) {
      devLog('bundle cache read', 'invalid')
      return null
    }
    if (Date.now() - (cached.savedAt || 0) > MAX_AGE_MS) {
      devLog('bundle cache read', 'expired')
      return null
    }
    lastSignature = cached.signature || null
    devLog('bundle cache read', 'hit', Object.keys(cached.records).length, 'records')
    return { index: cached.index, records: cached.records }
  } catch (err) {
    devLog('bundle cache read failed', err.message)
    return null
  }
}

/**
 * Persist current bundle state. `index` should omit keys whose record is
 * incomplete so they revalidate on the next boot. Skips the write entirely
 * when nothing changed since the last one.
 * @param {Object} index
 * @param {Object} records
 * @returns {Promise<boolean>} whether a write happened
 */
export async function saveBundleState(index, records) {
  const signature = stateSignature(index, records)
  if (signature === lastSignature) {
    devLog('bundle cache write', 'unchanged')
    return false
  }
  try {
    await idbSet(CACHE_KEY, { savedAt: Date.now(), index, records, signature })
    lastSignature = signature
    devLog('bundle cache write', 'saved', Object.keys(records || {}).length, 'records')
    return true
  } catch (err) {
    devLog('bundle cache write failed', err.message)
    return false
  }
}
