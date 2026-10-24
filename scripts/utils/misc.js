/**
 * Miscellaneous utilities — version comparison, bundle grouping, sorting, etc.
 */
import { escHtml } from './html.js'
import { resolveAvatarUrl, resolveBundleImage } from './url.js'
import { parseBundleKey } from './bundleKey.js'
import { getCachedAvatarDataUrl, getCachedIconDataUrl } from '../services/iconCache.js'

export function compareVersions(a, b) {
  const pa = a.split('.').map(Number)
  const pb = b.split('.').map(Number)
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const na = i < pa.length ? pa[i] : 0
    const nb = i < pb.length ? pb[i] : 0
    if (na > nb) return 1
    if (na < nb) return -1
  }
  return 0
}

export function isAppPreRelease(bundleName, pkgName, bundlesData) {
  const inStable = bundlesData[`${bundleName}:stable`]?.apps?.some((a) => a.package === pkgName)
  const inLatest = bundlesData[`${bundleName}:latest`]?.apps?.some((a) => a.package === pkgName)
  const inDev = bundlesData[`${bundleName}:dev`]?.apps?.some((a) => a.package === pkgName)
  return !!inDev && !inStable && !inLatest
}

export function groupAffectedBundles(affectedBundles) {
  const appPrecedence = { 'NEW APP': 0, 'MAJOR UPDATE': 1, 'UPDATED APP': 2, 'REMOVED APP': 3 }
  const grouped = {}

  for (const b of affectedBundles) {
    const bName = b.bundle
    if (!grouped[bName]) {
      grouped[bName] = { bundle: bName, channels: [], apps: [], badge_type: b.badge_type, version: b.version || '', repo_url: b.repo_url || '', patches_name: b.patches_name || '', extra_badges: b.extra_badges || [], previous_version: b.previous_version || '', new_version: b.new_version || '', avatarUrl: getDisplayAvatar(b.repo_url || '', b.avatarUrl || ''), bundleImageUrl: getDisplayBundleImage(b.repo_url || '', b.bundleImageUrl || '') }
    }
    if (!grouped[bName].channels.includes(b.channel)) {
      grouped[bName].channels.push(b.channel)
    }
    if (b.extra_badges) {
      for (const eb of b.extra_badges) {
        if (!grouped[bName].extra_badges.includes(eb)) {
          grouped[bName].extra_badges.push(eb)
        }
      }
    }
    for (const app of b.apps || []) {
      const existing = grouped[bName].apps.find((a) => a.package === app.package)
      if (!existing) {
        grouped[bName].apps.push({ ...app, scan_numbers: app.scan_numbers ? [...app.scan_numbers] : [] })
      } else {
        if ((appPrecedence[app.badge_type] ?? 99) < (appPrecedence[existing.badge_type] ?? 99)) {
          existing.badge_type = app.badge_type
        }
        if (app.patch_diff) {
          existing.patch_diff = app.patch_diff
        }
        if (app.scan_numbers) {
          for (const sn of app.scan_numbers) {
            if (!existing.scan_numbers?.includes(sn)) {
              if (!existing.scan_numbers) existing.scan_numbers = []
              existing.scan_numbers.push(sn)
            }
          }
        }
      }
    }
    if (b.badge_type === 'NEW BUNDLE') {
      grouped[bName].badge_type = 'NEW BUNDLE'
    } else if (b.badge_type === 'REMOVED BUNDLE' && grouped[bName].badge_type === 'REMOVED BUNDLE') {
      // all channels removed — keep as removed
    } else if (b.badge_type === 'REMOVED BUNDLE') {
      // mixed: some channel removed, another active — don't override active badge
    } else if (grouped[bName].badge_type === 'REMOVED BUNDLE') {
      // active channel update overrides removed badge
      grouped[bName].badge_type = b.badge_type
    }
  }

  return grouped
}

export function getNextScanTime() {
  const now = new Date()
  const utcHour = now.getUTCHours()
  const slot = Math.floor(utcHour / 3) * 3
  let nextHour = slot + 3
  if (now.getUTCMinutes() < 1 && utcHour === slot) {
    nextHour = slot
  }
  const next = new Date(now)
  if (nextHour >= 24) {
    next.setUTCDate(next.getUTCDate() + 1)
    next.setUTCHours(0, 1, 0, 0)
  } else {
    next.setUTCHours(nextHour, 1, 0, 0)
  }
  if (next <= now) {
    next.setUTCDate(next.getUTCDate() + 1)
    next.setUTCHours(0, 1, 0, 0)
  }
  return next
}

export function getScanBatch() {
  return Math.floor(new Date().getUTCHours() / 3) + 1
}

export function sortBundleNames(list) {
  const orderList = ['morphe', 'piko', 'rookieenough', 'hoo-dles', 'paresh-maheshwari', 'brosssh', 'patcheddit']
  return [...list].sort((a, b) => {
    const aIndex = orderList.indexOf(a.bundle)
    const bIndex = orderList.indexOf(b.bundle)
    if (aIndex !== -1 && bIndex !== -1) return aIndex - bIndex
    if (aIndex !== -1) return -1
    if (bIndex !== -1) return 1
    const aStars = a.stars ?? 0
    const bStars = b.stars ?? 0
    if (bStars !== aStars) return bStars - aStars
    const aCount = a.apps?.length ?? 0
    const bCount = b.apps?.length ?? 0
    if (bCount !== aCount) return bCount - aCount
    return a.bundle.localeCompare(b.bundle)
  })
}

const GENERIC_PACKAGE_SEGMENTS = new Set([
  'android', 'app', 'apps', 'application', 'mobile', 'client', 'free', 'pro',
  'lite', 'plus', 'hd', 'tv', 'beta', 'release', 'demo', 'premium',
])

/**
 * Human-readable name derived from a package identifier, used only as a last
 * resort when neither the name cache nor bundle metadata carries a name
 * (index-only records for packages missing from app_cache). Never throws and
 * never returns an empty string, so callers can sort/display without guards.
 * `com.instagram.android` → `Instagram`.
 * @param {string} pkg
 * @returns {string}
 */
export function nameFromPackage(pkg) {
  const raw = typeof pkg === 'string' ? pkg : ''
  if (!raw) return 'Unknown app'
  const parts = raw.split('.').filter(Boolean)
  const candidates = (parts.length > 1 ? parts.slice(1) : parts)
    .filter((seg) => !GENERIC_PACKAGE_SEGMENTS.has(seg.toLowerCase()))
  const pick = candidates.length ? candidates[candidates.length - 1] : parts[parts.length - 1]
  const pretty = pick
    .split(/[_\-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
  return pretty || raw
}

export function resolveAppName(app, nameCache) {
  const n = app && app.package ? nameCache[app.package] : ''
  if (typeof n === 'string' && n) return n
  if (app && typeof app.app_name === 'string' && app.app_name) return app.app_name
  return nameFromPackage(app && app.package)
}

export function scoreAppSearch(query, name, pkg) {
  const q = query.toLowerCase().trim()
  if (!q) return 0
  const n = name.toLowerCase()
  const p = pkg.toLowerCase()
  let score = 0
  if (n === q || p === q) score += 1000
  if (n.startsWith(q)) score += 800
  if (p.startsWith(q)) score += 700
  if (n.split(/[\s.\-]+/).some((w) => w.startsWith(q))) score += 450
  if (p.split(/[\s.\-]+/).some((w) => w.startsWith(q))) score += 350
  if (n.includes(q)) score += 250
  if (p.includes(q)) score += 150
  return score
}

export function getAppIconUrl(app, iconCache) {
  if (!app) return ''
  const fromCache = app.package ? iconCache[app.package] : ''
  return fromCache && typeof fromCache === 'string' ? fromCache : ''
}

/**
 * Avatar URL for display: repo_cache map → bundle field → IndexedDB data URL.
 * Prefers warm base64/WebP cache; falls back to remote URL.
 * @param {string} repoUrl
 * @param {string} [fallback]
 * @returns {string}
 */
export function getDisplayAvatar(repoUrl, fallback = '') {
  const url = resolveAvatarUrl(repoUrl, fallback)
  if (!url) return ''
  return getCachedAvatarDataUrl(url) || url
}

/**
 * Bundle cover image (patches-bundle.png) for display.
 * Prefers warm base64/WebP cache; falls back to remote URL.
 * @param {string} repoUrl
 * @param {string} [bundleField] - bundle.bundleImageUrl
 * @returns {string} '' when the repo has no bundle image
 */
export function getDisplayBundleImage(repoUrl, bundleField = '') {
  const url = resolveBundleImage(repoUrl, bundleField)
  if (!url) return ''
  return getCachedAvatarDataUrl(url) || url
}

/**
 * Avatar stack HTML: primary image → fallback image → letter placeholder.
 * Each layer hides itself on error and reveals the next sibling.
 * @param {string} primaryUrl - bundle image URL (may be '')
 * @param {string} fallbackUrl - author avatar URL (may be '')
 * @param {string} letterHtml - placeholder inner HTML
 * @param {string} imgClass - class for both <img> elements
 * @param {string} phClass - class for placeholder element
 * @returns {string}
 */
export function avatarStackHtml(primaryUrl, fallbackUrl, letterHtml, imgClass, phClass) {
  const urls = []
  if (primaryUrl) urls.push(primaryUrl)
  if (fallbackUrl && fallbackUrl !== primaryUrl) urls.push(fallbackUrl)
  const reveal = `onerror="this.style.display='none';if(this.nextElementSibling)this.nextElementSibling.style.display='flex'"`
  let html = ''
  for (let i = 0; i < urls.length; i++) {
    html += `<img class="${imgClass}" src="${escHtml(urls[i])}" alt="" loading="lazy"${i > 0 ? ' style="display:none"' : ''} ${reveal}>`
  }
  html += `<div class="${phClass}"${urls.length ? ' style="display:none"' : ''}>${letterHtml}</div>`
  return html
}

export function renderAppIcon(app, iconCache, size = 'default') {
  const iconUrl = getAppIconUrl(app, iconCache)
  const sizeClass = size === 'sm' ? ' app-icon--sm' : ''
  if (iconUrl) {
    const warm = getCachedIconDataUrl(iconUrl)
    if (warm) {
      return `<img class="app-icon${sizeClass}" src="${escHtml(warm)}" alt="">`
    }
    // Cold: the browser lazy-loads the original itself; data-icon-url lets
    // the fetch queue warm IndexedDB once this element scrolls into view.
    return `<img class="app-icon app-icon--loading${sizeClass}" src="${escHtml(iconUrl)}" alt="" loading="lazy" data-icon-url="${escHtml(iconUrl)}" onload="this.classList.remove('app-icon--loading')" onerror="this.classList.remove('app-icon--loading');this.style.display='none'">`
  }
  const name = app.app_name || app.package || '?'
  return `<div class="app-icon app-icon--fallback${sizeClass}">${name.charAt(0).toUpperCase()}</div>`
}

export function levenshteinDistance(a, b) {
  const m = a.length
  const n = b.length
  const dp = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0))
  for (let i = 0; i <= m; i++) dp[i][0] = i
  for (let j = 0; j <= n; j++) dp[0][j] = j
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1
      dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    }
  }
  return dp[m][n]
}

export function suggestFuzzy(query, appList, maxSuggestions = 3) {
  if (!query || appList.length === 0) return []
  const q = query.toLowerCase()
  const threshold = Math.max(2, Math.floor(q.length * 0.4))
  // An edit distance ≤ threshold implies the two strings' lengths differ by
  // at most that much, so candidates outside the band can never make the cut —
  // filtering first skips their (dominant) Levenshtein passes entirely.
  const candidates = appList.filter((app) => {
    const name = app.name || ''
    const pkg = app.package || ''
    return (
      Math.abs(name.length - q.length) <= threshold ||
      Math.abs(pkg.length - q.length) <= threshold
    )
  })
  const scored = candidates.map((app) => {
    const name = app.name.toLowerCase()
    const pkg = app.package.toLowerCase()
    let distance = levenshteinDistance(q, name)
    if (distance > q.length) distance = levenshteinDistance(q, pkg)
    return { app, distance }
  })
  scored.sort((a, b) => a.distance - b.distance)
  return scored
    .filter((s) => s.distance <= threshold)
    .slice(0, maxSuggestions)
    .map((s) => s.app)
}

let versionsCache = null

export function getStoredVersions() {
  if (!versionsCache) {
    try {
      versionsCache = JSON.parse(localStorage.getItem('morphe_versions') || '{}')
    } catch {
      versionsCache = {}
    }
  }
  return versionsCache
}

export function setStoredVersion(bundle, version) {
  const stored = getStoredVersions()
  if (stored[bundle] === version) return
  stored[bundle] = version
  try {
    localStorage.setItem('morphe_versions', JSON.stringify(stored))
  } catch {
    // ignore
  }
}

export function daysSince(dateStr) {
  if (!dateStr) return null
  const dt = new Date(dateStr)
  if (isNaN(dt.getTime())) return null
  const now = new Date()
  return Math.floor((now.getTime() - dt.getTime()) / (1000 * 60 * 60 * 24))
}

export function getStaleness(dateStr) {
  const d = daysSince(dateStr)
  if (d === null) return null
  if (d <= 7) return { days: d, level: 'fresh', label: `${d}d` }
  if (d <= 14) return { days: d, level: 'moderate', label: `${d}d` }
  return { days: d, level: 'stale', label: `${d}d` }
}

/**
 * Merge bundle records into one card group per bundle name — the shape
 * BundleCards render from (channels, top version/stars/app count, deduped
 * app union). Index-only records work: apps are `{package}` at minimum.
 * @param {Object} bundles - store bundle map (key → record)
 * @param {((record: Object) => boolean)|null} [include] - per-record filter
 * @returns {Object} bundle name → card record
 */
export function groupBundleCards(bundles, include = null) {
  const grouped = {}
  for (const b of Object.values(bundles)) {
    if (!b?.bundle || (include && !include(b))) continue

    if (!grouped[b.bundle]) {
      grouped[b.bundle] = {
        bundle: b.bundle,
        channels: [b.channel],
        repo_url: b.repo_url,
        patches_name: b.patches_name,
        version: b.version || '',
        created_at: b.created_at,
        apps: [...(b.apps || [])],
        appCount: b.app_count || 0,
        stars: b.stars || 0,
        avatarUrl: getDisplayAvatar(b.repo_url, b.avatarUrl),
        bundleImageUrl: getDisplayBundleImage(b.repo_url, b.bundleImageUrl),
        repoDescription: b.repoDescription || '',
        isArchived: b.isArchived || false,
        isPreRelease: b.isPreRelease || false,
      }
      continue
    }

    const g = grouped[b.bundle]
    if (b.version && !g.version) g.version = b.version
    if (!g.channels.includes(b.channel)) g.channels.push(b.channel)
    if (!g.patches_name && b.patches_name) g.patches_name = b.patches_name
    if ((b.stars || 0) > (g.stars || 0)) g.stars = b.stars
    if ((b.app_count || 0) > g.appCount) g.appCount = b.app_count || 0
    const bAv = getDisplayAvatar(b.repo_url, b.avatarUrl)
    if (bAv && !g.avatarUrl) g.avatarUrl = bAv
    const bImg = getDisplayBundleImage(b.repo_url, b.bundleImageUrl)
    if (bImg && !g.bundleImageUrl) g.bundleImageUrl = bImg
    if (b.repoDescription && !g.repoDescription) g.repoDescription = b.repoDescription
    if (b.isArchived) g.isArchived = true
    if (b.isPreRelease) g.isPreRelease = true
    const existingPkgs = new Set(g.apps.map((a) => a.package))
    for (const app of b.apps || []) {
      if (!existingPkgs.has(app.package)) {
        g.apps.push(app)
        existingPkgs.add(app.package)
      }
    }
  }
  return grouped
}

// Cache keyed on the bundles/nameCache/iconCache object identities — bundles
// is set twice at boot (cache paint, then index) and detail loads merge into
// it in place, so Apps/Diff/GlobalSearch visits reuse one index per session.
let appIndexCache = { bundles: null, nameCache: null, iconCache: null, value: null }

export function buildAppIndex(bundlesData, nameCache, iconCache) {
  if (
    appIndexCache.bundles === bundlesData &&
    appIndexCache.nameCache === nameCache &&
    appIndexCache.iconCache === iconCache
  ) {
    return appIndexCache.value
  }
  const map = new Map()
  for (const key of Object.keys(bundlesData)) {
    const bundle = bundlesData[key]
    const { name: bundleName } = parseBundleKey(key)
    const repoUrl = bundle.repo_url || `https://github.com/${bundleName}/revanced-patches`
    for (const app of bundle.apps || []) {
      let entry = map.get(app.package)
      if (!entry) {
        entry = {
          package: app.package,
          name: resolveAppName(app, nameCache),
          iconUrl: getAppIconUrl(app, iconCache),
          bundles: [],
          patchesNames: [],
        }
        map.set(app.package, entry)
      }
      if (bundle.patches_name && !entry.patchesNames.includes(bundle.patches_name)) {
        entry.patchesNames.push(bundle.patches_name)
      }
      let ref = entry.bundles.find((b) => b.bundleName === bundleName && b.repoUrl === repoUrl)
      if (!ref) {
        ref = { bundleName, repoUrl, version: '', channels: [], patchesName: bundle.patches_name }
        entry.bundles.push(ref)
      }
      if (!ref.channels.includes(bundle.channel)) ref.channels.push(bundle.channel)
      if (bundle.version && !ref.version) ref.version = bundle.version
    }
  }
  const value = [...map.values()].sort((a, b) => a.name.localeCompare(b.name))
  appIndexCache = { bundles: bundlesData, nameCache, iconCache, value }
  return value
}

/**
 * Copy text to clipboard and briefly flash the element.
 * @param {string} text
 * @param {HTMLElement} [el]
 */
export async function copyToClipboard(text, el) {
  try {
    await navigator.clipboard.writeText(text)
    if (el) {
      el.classList.add('copied-flash')
      setTimeout(() => el.classList.remove('copied-flash'), 600)
    }
  } catch {
    // Fallback for older browsers
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    if (el) {
      el.classList.add('copied-flash')
      setTimeout(() => el.classList.remove('copied-flash'), 600)
    }
  }
}

