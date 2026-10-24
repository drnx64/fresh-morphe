/**
 * Icon / avatar cache — IndexedDB + canvas resize to WebP data URLs.
 */

import { idbGet, idbGetMany, idbSetMany, idbKeys, idbDeleteMany } from './indexedDB.js'

/** @type {Object<string, string>} */
const imageCache = {}
/** @type {Object<string, string>} */
const urlToPkg = {}
/** @type {Object<string, string>} URL -> dataUrl (repo/author avatars) */
const avatarCache = {}

const MAX_STORED_IMAGES = 600
const MAX_STORED_AVATARS = 256
const ICON_MAX = 96
const AVATAR_MAX = 96

function pkgKey(pkg) {
  return `icon_${pkg}`
}

function hashStr(s) {
  let hash = 0
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) - hash) + s.charCodeAt(i) | 0
  }
  return 'img_' + Math.abs(hash).toString(36)
}

function resolveIdbKey(iconUrl) {
  const pkg = urlToPkg[iconUrl]
  if (pkg) return pkgKey(pkg)
  return hashStr(iconUrl)
}

function avatarKey(url) {
  return 'avatar_' + hashStr(url)
}

export async function pruneStoredImages() {
  try {
    const keys = await idbKeys('icon_')
    const hashKeys = await idbKeys('img_')
    const allKeys = [...keys, ...hashKeys]
    if (allKeys.length <= MAX_STORED_IMAGES) return
    const inUse = new Set()
    for (const url of Object.keys(imageCache)) inUse.add(resolveIdbKey(url))
    let toDelete = allKeys.length - MAX_STORED_IMAGES
    const del = []
    for (const k of allKeys) {
      if (toDelete <= 0) break
      if (!inUse.has(k)) {
        del.push(k)
        toDelete--
      }
    }
    if (del.length) {
      await idbDeleteMany(del)
    }
  } catch {
    /* ignore */
  }
}

async function pruneStoredAvatars() {
  try {
    const keys = await idbKeys('avatar_')
    if (keys.length <= MAX_STORED_AVATARS) return
    const inUse = new Set(Object.keys(avatarCache).map(avatarKey))
    let toDelete = keys.length - MAX_STORED_AVATARS
    const del = []
    for (const k of keys) {
      if (toDelete <= 0) break
      if (!inUse.has(k)) {
        del.push(k)
        toDelete--
      }
    }
    if (del.length) {
      await idbDeleteMany(del)
    }
  } catch {
    /* ignore */
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const RATE_BACKOFF_BASE_MS = 1000
const RATE_BACKOFF_MAX_MS = 60000
// A hung connection must not stall the single-threaded icon queue forever.
const ICON_FETCH_TIMEOUT_MS = 12000

/**
 * Shared backoff for 429/403 responses — icons and avatars both pass
 * through loadImage, so one gate protects the whole cache-warming effort.
 * Attempts reset on a success observed after the gate has expired.
 */
let rateLimitedUntil = 0
let rateAttempts = 0

function noteRateLimit() {
  rateAttempts++
  rateLimitedUntil = Date.now() + Math.min(RATE_BACKOFF_MAX_MS, RATE_BACKOFF_BASE_MS * 2 ** Math.min(rateAttempts - 1, 6))
}

function resizeToDataUrl(img, maxDim) {
  try {
    const canvas = document.createElement('canvas')
    let w = img.naturalWidth
    let h = img.naturalHeight
    if (w > maxDim || h > maxDim) {
      const ratio = Math.min(maxDim / w, maxDim / h)
      w = Math.round(w * ratio)
      h = Math.round(h * ratio)
    }
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    ctx.drawImage(img, 0, 0, w, h)
    const webpUrl = canvas.toDataURL('image/webp', 0.8)
    if (webpUrl.length > 23) return webpUrl
    return canvas.toDataURL('image/jpeg', 0.8)
  } catch {
    return null
  }
}

/**
 * Fetch an image, resize to a WebP data URL.
 * Resolves null on ordinary failures (network, 404, decode).
 * Throws {rateLimited: true} on 429/403 after arming the shared backoff —
 * callers must requeue rather than count it as a permanent failure.
 */
async function loadImage(url, maxDim = ICON_MAX) {
  while (Date.now() < rateLimitedUntil) {
    await sleep(rateLimitedUntil - Date.now())
  }
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), ICON_FETCH_TIMEOUT_MS)
  try {
    let resp
    try {
      resp = await fetch(url, { signal: ctrl.signal })
    } catch {
      // Network error or timeout abort — ordinary failure, not rate limiting.
      return null
    }
    if (resp.status === 429 || resp.status === 403) {
      noteRateLimit()
      const err = new Error(`Rate limited (${resp.status}) for ${url}`)
      err.rateLimited = true
      throw err
    }
    if (!resp.ok) return null
    // A success only counts as "recovered" once an armed gate has expired —
    // batch-mates succeeding during backoff must not clear it.
    if (Date.now() >= rateLimitedUntil) {
      rateAttempts = 0
      rateLimitedUntil = 0
    }
    let blob
    try {
      blob = await resp.blob()
    } catch {
      return null
    }
    const objectUrl = URL.createObjectURL(blob)
    try {
      const img = await new Promise((resolve, reject) => {
        const i = new Image()
        i.onload = () => resolve(i)
        i.onerror = () => reject(new Error('decode failed'))
        i.src = objectUrl
      })
      return resizeToDataUrl(img, maxDim)
    } catch {
      return null
    } finally {
      URL.revokeObjectURL(objectUrl)
    }
  } finally {
    clearTimeout(timer)
  }
}

/**
 * Register app_cache icon URLs so IndexedDB keys stay package-based
 * (stable across sessions) instead of falling back to URL hashes.
 * @param {Object<string,string>} iconMap
 */
export function registerIconUrls(iconMap) {
  for (const [pkg, val] of Object.entries(iconMap)) {
    if (val && typeof val === 'string' && val.startsWith('http')) {
      urlToPkg[val] = pkg
    }
  }
}

export async function fetchAndCacheIcon(iconUrl) {
  if (!iconUrl || typeof iconUrl !== 'string') return null
  if (iconUrl.startsWith('data:')) return iconUrl
  if (imageCache[iconUrl]) return imageCache[iconUrl]
  const idbKey = resolveIdbKey(iconUrl)
  const cached = await idbGet(idbKey)
  if (cached) {
    imageCache[iconUrl] = cached
    return cached
  }
  if (iconUrl.startsWith('http')) {
    const dataUrl = await loadImage(iconUrl)
    if (dataUrl) {
      imageCache[iconUrl] = dataUrl
      idbSetMany([[idbKey, dataUrl]])
      return dataUrl
    }
  }
  return null
}

export function getCachedIconDataUrl(iconUrl) {
  if (!iconUrl || typeof iconUrl !== 'string') return undefined
  if (iconUrl.startsWith('data:')) return iconUrl
  return imageCache[iconUrl]
}

/** Sync memory lookup for avatar data URL (undefined if not warm). */
export function getCachedAvatarDataUrl(url) {
  if (!url || typeof url !== 'string') return undefined
  if (url.startsWith('data:')) return url
  return avatarCache[url]
}

/** Fetch avatar, resize to WebP data URL, store in IndexedDB. */
export async function fetchAndCacheAvatar(url) {
  if (!url || typeof url !== 'string') return null
  if (url.startsWith('data:')) return url
  if (avatarCache[url]) return avatarCache[url]
  const key = avatarKey(url)
  const cached = await idbGet(key)
  if (cached) {
    avatarCache[url] = cached
    return cached
  }
  if (url.startsWith('http')) {
    const dataUrl = await loadImage(url, AVATAR_MAX)
    if (dataUrl) {
      avatarCache[url] = dataUrl
      idbSetMany([[key, dataUrl]])
      await pruneStoredAvatars()
      return dataUrl
    }
  }
  return null
}

/**
 * Warm avatar cache: hydrate from IndexedDB, then network-fetch misses
 * strictly one at a time (no parallel requests) as WebP data URLs.
 * @param {string[]} urls
 * @param {(loaded: number, total: number) => void} [onProgress]
 * @returns {Promise<{failed: number}>}
 */
export async function preloadAvatars(urls, onProgress) {
  const unique = [...new Set(urls.filter((u) => u && typeof u === 'string' && !u.startsWith('data:')))]
  if (!unique.length) return { failed: 0 }

  const cold = unique.filter((u) => !avatarCache[u])
  if (cold.length) {
    try {
      const found = await idbGetMany(cold.map(avatarKey))
      for (const url of cold) {
        const val = found.get(avatarKey(url))
        if (val) avatarCache[url] = val
      }
    } catch {
      /* ignore */
    }
  }

  const missing = unique.filter((u) => !avatarCache[u])
  // Hydrated-from-IndexedDB URLs count as done so callers' progress reaches total
  const hydrated = unique.length - missing.length
  onProgress?.(hydrated, unique.length)
  let loaded = 0
  let failed = 0

  for (const url of missing) {
    try {
      const result = await fetchAndCacheAvatar(url)
      if (!result) failed++
    } catch {
      failed++
    }
    loaded++
    onProgress?.(hydrated + loaded, unique.length)
  }

  return { failed }
}
