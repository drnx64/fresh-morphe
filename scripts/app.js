/**
 * MorpheTracker — Vanilla JS Entry Point
 * Boot: init store, load data, register routes, render shell.
 */

// ── Verbose Logging with Colors ──
const _LOG_COLORS = { log: '#948d81', info: '#6b9fd4', warn: '#d4a06b', error: '#b5533f', debug: '#a281ad' }
for (const [method, color] of Object.entries(_LOG_COLORS)) {
  const orig = console[method].bind(console)
  console[method] = (...args) => orig(`%c[Morphe]`, `color:${color};font-weight:bold`, ...args)
}
window.addEventListener('error', (e) => console.error('Uncaught:', e.message, e.filename, e.lineno, e.error))
window.addEventListener('unhandledrejection', (e) => console.error('Unhandled promise:', e.reason))

// ── Theme ──
const savedTheme = localStorage.getItem('morphe_theme') || 'light'
document.documentElement.setAttribute('data-theme', savedTheme)

function updateThemeMeta(theme) {
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.content = theme === 'dark' ? '#1b1420' : '#fbf7f4'
}

function toggleTheme() {
  const root = document.documentElement
  const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'

  // Freeze color transitions for the swap, then repaint once.
  root.classList.add('theme-switching')
  root.setAttribute('data-theme', next)
  localStorage.setItem('morphe_theme', next)
  updateThemeMeta(next)
  const btn = document.querySelector('.theme-toggle')
  if (btn) btn.innerHTML = next === 'dark' ? MOON_ICON : SUN_ICON

  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      root.classList.remove('theme-switching')
      window.dispatchEvent(new CustomEvent('theme-change'))
    })
  })
}

const SUN_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>'
const MOON_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'

import * as store from './store.js'
import * as router from './router.js'
import { el, mount } from './ui.js'
import { openAppDetailModal } from './components/appDetailModal.js'
import { openBundleHistoryModal } from './components/bundleHistoryModal.js'
import { openBundleModal } from './components/bundleModal.js'
import { renderGlobalSearch } from './components/globalSearch.js'
import { syncColorGameChip } from './components/colorGame.js'
import { showWorkToast } from './components/workToast.js'
import { preloadAvatars } from './services/iconCache.js'
import { initIconQueue, runIconQueue } from './services/iconFetchQueue.js'
import { loadBundleState, saveBundleState } from './services/bundleCache.js'
import {
  ensureAppBundles, ensureBundleChannels, onBundleDetailsLoaded,
} from './services/bundleLoader.js'
import { fetchJson } from './utils/http.js'
import { SITE_URL, GITHUB_REPO_URL, shrinkPlayIconUrl, withAvatarSize } from './utils/url.js'
import { parseBundleKey } from './utils/bundleKey.js'
import { normalizeRepoUrl } from './utils/misc.js'

// ── Default State ──
store.init({
  bundles: {},
  iconCache: {},
  nameCache: {},
  repoAvatarMap: {},
  repoBundleImageMap: {},
  changelog: [],
  liveDataDate: '',
  lastChecked: '',
  stats: null,
  changes: null,
  loading: true,
  loadingProgress: 0,
  loadingStatus: 'Initializing...',
  filters: { search: '', channel: 'all', hideArchived: false, hidePreRelease: false },
  viewMode: localStorage.getItem('morphe_view') || 'grid',
  changelogViewMode: localStorage.getItem('morphe_changelog_view') || 'grid',
  updatesViewMode: localStorage.getItem('morphe_updates_view') || 'timeline',
  lastVisitScan: localStorage.getItem('morphe_last_visit_scan') || '',
  fetchErrors: [],
  reducedMotion: localStorage.getItem('morphe_reduced_motion') === 'true',
  liveClockUTC: '',
  liveClockLocal: '',
})

// ── Page Modules (lazy-loaded) ──
const pages = {
  dashboard: () => import('./pages/dashboard.js'),
  apps: () => import('./pages/apps.js'),
  bundleDetail: () => import('./pages/bundleDetail.js'),
  changelog: () => import('./pages/changelog.js'),
  diff: () => import('./pages/diff.js'),
}

// ── Nav Tabs Config ──
const NAV_TABS = [
  { path: '#/', label: 'Apps', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/></svg>' },
  { path: '#/changelog', label: 'Changelog', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16"/><path d="M4 12h10"/><path d="M4 18h13"/></svg>' },
  { path: '#/bundles', label: 'Bundles', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="M3.3 7 12 12l8.7-5"/><path d="M12 22V12"/></svg>' },
  { path: '#/diff', label: 'Diff', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v4"/><path d="M16 3v4"/><rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 14l3 3 5-6"/></svg>' },
]

// ── Shell Layout ──
function renderShell() {
  const root = document.getElementById('root')

  // Header with title + desktop nav
  const header = el('header', { class: 'app-header' }, [
    el('div', { class: 'header-content' }, [
      el('div', { class: 'header-top-row' }, [
        el('div', { class: 'header-title-group' }, [
          el('div', { class: 'header-title-row' }, [
            el('img', { class: 'header-logo', src: 'public/tracker_logo.webp', alt: '', width: '28', height: '28' }),
            el('h1', { id: 'main-title', class: 'header-title-clickable', title: 'Double-click to toggle reduced motion' }, ['Morphe Tracker']),
          ]),
          el('span', { class: 'subtitle' }, ['Patch monitoring & changelog dashboard']),
        ]),
        el('div', { class: 'header-right-row', id: 'header-actions' }, [
          el('div', { class: 'header-color-slot', id: 'color-game-slot' }),
        ]),
      ]),
      el('nav', { class: 'app-nav', id: 'app-nav', 'aria-label': 'Main navigation' }),
    ]),
  ])

  // Populate desktop nav tabs
  const navEl = header.querySelector('#app-nav')
  for (const tab of NAV_TABS) {
    const link = el('a', { href: tab.path, class: 'nav-tab' }, [tab.label])
    navEl.appendChild(link)
  }

  // Title double-click → toggle reduced motion
  const title = header.querySelector('#main-title')
  title.addEventListener('dblclick', () => {
    const next = !store.get('reducedMotion')
    store.set('reducedMotion', next)
    localStorage.setItem('morphe_reduced_motion', String(next))
  })

  // Global search bar
  const globalSearch = renderGlobalSearch()
  header.querySelector('#header-actions').prepend(globalSearch)

  // Theme toggle button
  const themeBtn = el('button', {
    class: 'theme-toggle',
    'aria-label': 'Toggle dark mode',
    title: 'Toggle theme',
    dangerouslySetInnerHTML: savedTheme === 'dark' ? MOON_ICON : SUN_ICON,
  })
  themeBtn.addEventListener('click', toggleTheme)
  header.querySelector('#header-actions').appendChild(themeBtn)

  // Boot progress bar — determinate top bar + status pill while loadData runs
  const bootProgress = el('div', {
    class: 'boot-progress',
    id: 'boot-progress',
    role: 'progressbar',
    'aria-valuemin': '0',
    'aria-valuemax': '100',
    'aria-valuenow': '0',
    'aria-label': 'Loading site data',
  }, [
    el('div', { class: 'boot-progress-track' }, [
      el('div', { class: 'boot-progress-fill', id: 'boot-progress-fill' }),
    ]),
    el('div', { class: 'boot-progress-status', id: 'boot-progress-status' }, [
      store.get('loadingStatus') || 'Starting…',
    ]),
  ])

  const pageContainer = el('main', { id: 'page-container', class: 'page-container dashboard-page' })

  const footer = el('footer', { class: 'app-footer' }, [
    el('div', { class: 'footer-inner' }, [
      el('div', { class: 'footer-links' }, [
        el('a', { href: `${SITE_URL}/feed.xml`, target: '_blank', rel: 'noopener' }, ['RSS Feed']),
        el('span', { class: 'footer-sep' }, ['|']),
        el('a', { href: GITHUB_REPO_URL, target: '_blank', rel: 'noopener' }, ['GitHub']),
      ]),
      el('p', { class: 'footer-disclaimer' }, [
        'MorpheTracker is not affiliated with or endorsed by any app developers.',
      ]),
    ]),
  ])

  // Bottom tab bar (mobile)
  const bottomNav = el('nav', { class: 'bottom-nav', id: 'bottom-nav', 'aria-label': 'Primary navigation' })
  for (const tab of NAV_TABS) {
    const link = el('a', { href: tab.path, class: 'nav-tab' }, [
      el('span', { dangerouslySetInnerHTML: tab.icon }),
      el('span', {}, [tab.label]),
    ])
    bottomNav.appendChild(link)
  }

  mount(root, [header, bootProgress, pageContainer, footer, bottomNav])

  // Drive the progress bar from store state (shell renders once — no leak)
  const progressFill = bootProgress.querySelector('#boot-progress-fill')
  const progressStatus = bootProgress.querySelector('#boot-progress-status')
  store.subscribe('loadingProgress', (progress) => {
    const clamped = Math.max(0, Math.min(100, progress))
    progressFill.style.width = `${clamped}%`
    bootProgress.setAttribute('aria-valuenow', String(Math.round(clamped)))
  })
  store.subscribe('loadingStatus', (status) => {
    progressStatus.textContent = status
  })
  store.subscribe('loading', (loading) => {
    if (!loading) bootProgress.classList.add('done')
  })
}

// ── Routes ──
function setupRoutes() {
  router.addRoute('/', async () => {
    const { renderApps } = await pages.apps()
    renderApps(document.getElementById('page-container'))
  }, 'apps')

  router.addRoute('/bundles', async () => {
    const { renderDashboard } = await pages.dashboard()
    renderDashboard(document.getElementById('page-container'))
  }, 'dashboard')

  router.addRoute('/bundle/:bundleName', async (params) => {
    try {
      const { renderBundleDetail } = await pages.bundleDetail()
      await renderBundleDetail(document.getElementById('page-container'), params.bundleName)
    } catch (err) {
      console.error('[router] bundle detail failed:', err)
    }
  }, 'bundleDetail')

  router.addRoute('/changelog', async () => {
    const { renderChangelog } = await pages.changelog()
    const container = document.getElementById('page-container')
    mount(container, el('div', { class: 'loading-state' }, ['Loading changelog...']))
    await ensureChangelog()
    renderChangelog(container)
  }, 'changelog')

  router.addRoute('/diff', async () => {
    const { renderDiff } = await pages.diff()
    renderDiff(document.getElementById('page-container'))
  }, 'diff')

  router.setNotFound(() => {
    router.navigate('/')
  })
}

// ── Data Loading ──
let deferredPreloads = null

// Index backing the current bundle map — kept so on-demand detail loads can
// persist what they warm without threading it through every caller.
let currentBundleIndex = null

// Lazily fetch the 2.75 MB changelog (only /changelog + history modal need it)
let changelogPromise = null
function ensureChangelog() {
  if (!changelogPromise) {
    changelogPromise = fetchJson('data/changelog.json')
      .then((data) => {
        store.set('changelog', Array.isArray(data) ? data : [])
      })
      .catch((err) => {
        console.error('[app] Changelog load failed:', err)
        changelogPromise = null
      })
  }
  return changelogPromise
}

function setBootProgress(progress, status) {
  store.set('loadingProgress', progress)
  if (status) store.set('loadingStatus', status)
}

function offerOfflineNotice() {
  const toast = showWorkToast('Data refresh failed')
  toast.setDetail('Showing cached data')
  toast.setActions([
    { label: 'Retry', primary: true, onClick: () => location.reload() },
    { label: 'Dismiss', onClick: () => toast.close() },
  ])
}

function finishBoot(status = 'Loading complete') {
  setBootProgress(100, status)
  store.set('loading', false)

  // Record last visit for "new scan" detection
  const today = new Date().toISOString().split('T')[0]
  if (store.get('lastVisitScan') !== today) {
    localStorage.setItem('morphe_last_visit_scan', today)
    store.set('lastVisitScan', today)
  }
}

function offerImageRetry(failedCount, runPass, toast) {
  if (toast.isClosed()) toast = showWorkToast('Image caching')
  const retry = async () => {
    toast.setActions([])
    toast.setDetail('Retrying…')
    try {
      const stillFailed = await runPass()
      if (stillFailed > 0) {
        // Retried once and still failing — auto-dismiss the toast.
        toast.finish(
          `${stillFailed} image${stillFailed !== 1 ? 's' : ''} failed to cache`,
          { fillBar: false },
        )
      } else {
        toast.finish('Images cached')
      }
    } catch (err) {
      console.error('[app] Image retry failed:', err)
      toast.finish('Retry failed', { fillBar: false })
    }
  }
  toast.setDetail(`${failedCount} image${failedCount !== 1 ? 's' : ''} failed to cache`)
  toast.setActions([
    { label: 'Retry', primary: true, onClick: retry },
    { label: 'Dismiss', onClick: () => toast.close() },
  ])
}

// Background-warm the icon queue first (the apps grid is the landing view and
// its icons are the ones scrolled into view), then the avatars (bundles page).
// One progress toast reports both phases, with retry on failures.
async function cacheImagesInBackground(iconMap, repoAvatarMap, repoBundleImageMap) {
  const iconTotal = Object.values(iconMap).filter((v) => typeof v === 'string' && v.startsWith('http')).length
  const avatarUrls = [...new Set(
    [...Object.values(repoAvatarMap), ...Object.values(repoBundleImageMap)]
      .filter((u) => u && typeof u === 'string' && !u.startsWith('data:')),
  )]
  if (!iconTotal && !avatarUrls.length) return

  const toast = showWorkToast('Caching images')
  // Brief, non-nagging notice: the stack closes itself after 1.5s whether or
  // not the sweep is done. Failures below re-open a fresh toast with Retry.
  const autoHide = setTimeout(() => toast.close(), 1500)

  const runPass = async () => {
    let failed = 0
    if (iconTotal) {
      toast.setTitle('Caching icons')
      const iconRes = await runIconQueue((done, total) => toast.setProgress(done, total), { includeFailed: true })
      failed += iconRes.failed
    }
    if (avatarUrls.length) {
      toast.setTitle('Caching avatars')
      const avRes = await preloadAvatars(avatarUrls, (loaded) => toast.setProgress(loaded, avatarUrls.length))
      failed += avRes.failed
    }
    return failed
  }

  try {
    const failed = await runPass()
    clearTimeout(autoHide)
    if (failed > 0) {
      offerImageRetry(failed, runPass, toast)
    } else {
      toast.finish('Images cached', { autoCloseMs: 1500 })
    }
  } catch (err) {
    clearTimeout(autoHide)
    console.error('[app] Image caching failed:', err)
    const failToast = toast.isClosed() ? showWorkToast('Image caching failed') : toast
    failToast.setDetail('Image caching failed')
    failToast.setActions([{ label: 'Dismiss', onClick: () => failToast.close() }])
  }
}

/**
 * Index entries carry bare package names; the UI's app helpers expect
 * `{package}` objects (per-app `patches` arrive later, on demand).
 * @param {Object} entry - an _index.json record
 * @returns {Object[]}
 */
function normalizeIndexApps(entry) {
  const apps = entry.apps
  if (!Array.isArray(apps)) return []
  return apps.map((a) => (typeof a === 'string' ? { package: a } : a))
}

/**
 * Assemble the store's bundle map from the network index alone: cached full
 * records while their version still matches, otherwise an index-only entry
 * (`_partial: true`) that services/bundleLoader.js fills in when read.
 * @param {Object} index - _index.json
 * @param {{index: Object, records: Object}|null} cachedState
 * @returns {Object}
 */
function buildBundlesFromIndex(index, cachedState) {
  const cachedRecords = cachedState?.records || null
  const cachedIndex = cachedState?.index || null
  const bundles = {}
  let withoutApps = 0

  for (const key of Object.keys(index)) {
    const indexEntry = index[key]
    const cachedRecord = cachedRecords?.[key]
    const versionsMatch = cachedIndex?.[key]?.version === indexEntry.version

    if (cachedRecord && versionsMatch && !cachedRecord._partial && Array.isArray(cachedRecord.apps)) {
      // Full detail already local (and still current) — paint it as-is
      bundles[key] = { ...indexEntry, ...cachedRecord, _partial: false }
      continue
    }
    const apps = normalizeIndexApps(indexEntry)
    if (!Array.isArray(indexEntry.apps)) withoutApps++
    bundles[key] = { ...indexEntry, apps, _partial: true }
  }

  if (withoutApps) {
    console.warn(`[app] ${withoutApps} index entries have no apps[] — regenerate data/bundles/_index.json`)
  }
  return bundles
}

async function loadData() {
  try {
    setBootProgress(5, 'Loading site data…')

    // The cache read and the first network wave start together, so IndexedDB
    // latency never sits in front of a request — and all six data files are
    // requested in one round trip instead of four sequential waves.
    const cachedStatePromise = loadBundleState()
    const wavePromise = Promise.all([
      fetchJson('data/core.json', undefined, { cache: 'no-cache' }),
      fetchJson('data/stats.json', undefined, { cache: 'no-cache' }),
      fetchJson('data/changes.json', undefined, { cache: 'no-cache' }),
      fetchJson('data/state/app_cache.json', {}),
      fetchJson('data/state/repo_cache.json', {}),
      fetchJson('data/bundles/_index.json'),
    ])

    // Instant paint from IndexedDB while the network wave is still in flight
    const cachedState = await cachedStatePromise
    if (cachedState) store.set('bundles', { ...cachedState.records })

    let coreRes, statsRes, changesRes, cacheRes, repoCacheRes, index
    try {
      ;[coreRes, statsRes, changesRes, cacheRes, repoCacheRes, index] = await wavePromise
    } catch (err) {
      if (cachedState) {
        deferredPreloads = null
        offerOfflineNotice()
        finishBoot('Offline — cached data')
        return
      }
      throw err
    }

    store.merge({
      liveDataDate: coreRes.date || '',
      lastChecked: coreRes.last_run || '',
      stats: statsRes,
      changes: changesRes,
    })
    setBootProgress(65, 'Loading caches…')

    // Icon + name caches — degraded (fallback icons, resolved names) if missing
    const iconCache = {}
    const nameCache = {}
    for (const [pkg, entry] of Object.entries(cacheRes)) {
      if (entry && typeof entry === 'object') {
        // Store the downsized CDN variant once here so display, the fetch
        // queue and IndexedDB keys all agree on one canonical URL.
        if (entry.icon_url) iconCache[pkg] = shrinkPlayIconUrl(entry.icon_url)
        if (entry.name) nameCache[pkg] = entry.name
      }
    }
    store.merge({ iconCache, nameCache })
    initIconQueue(iconCache)

    // Repo owner avatars (repo_cache.json → repo_url → avatarUrl)
    const repoAvatarMap = {}
    const repoBundleImageMap = {}
    const repoMetaByUrl = new Map()
    if (repoCacheRes && typeof repoCacheRes === 'object') {
      for (const [repoUrl, entry] of Object.entries(repoCacheRes)) {
        if (!entry || typeof entry !== 'object') continue
        repoMetaByUrl.set(normalizeRepoUrl(repoUrl), entry)
        if (entry.avatarUrl) {
          // Same normalization rule as resolveAvatarUrl — the preload keys
          // must match the URLs the display layer looks up.
          repoAvatarMap[repoUrl] = withAvatarSize(entry.avatarUrl)
        }
        if (entry.bundleImageUrl) {
          repoBundleImageMap[repoUrl] = entry.bundleImageUrl
        }
      }
    }
    store.merge({ repoAvatarMap, repoBundleImageMap })

    // Background-warm icon + avatar caches. Deferred so they never compete
    // with the boot requests above.
    deferredPreloads = () => {
      cacheImagesInBackground(iconCache, repoAvatarMap, repoBundleImageMap).catch((err) => {
        console.error('[app] Image caching failed:', err)
      })
    }

    if (!index || Object.keys(index).length === 0) {
      finishBoot()
      return
    }

    // Apps directory from the index alone — per-app patch detail is fetched
    // on demand (services/bundleLoader.js), never as a 742-file boot loop.
    setBootProgress(85, 'Building app directory…')
    currentBundleIndex = index
    const bundles = buildBundlesFromIndex(index, cachedState)

    // The index is rebuilt from parsing each run, so it can miss repo metadata
    // (stars, description, archived/fork state) that repo_cache.json still has.
    // Overlay it here so stars show even before the pipeline rewrites the index.
    for (const record of Object.values(bundles)) {
      const meta = repoMetaByUrl.get(normalizeRepoUrl(record.repo_url))
      if (!meta) continue
      if (typeof meta.stars === 'number') record.stars = meta.stars
      if (meta.repoDescription) record.repoDescription = meta.repoDescription
      if ('isArchived' in meta) record.isArchived = meta.isArchived
      if ('isFork' in meta) {
        record.isFork = meta.isFork
        record.forkParent = meta.forkParent || ''
      }
    }
    store.set('bundles', { ...bundles })

    // No-op when the signature matches what is already cached
    saveBundleState(index, bundles)

    // Icon/avatar preloads now that the boot requests are done
    if (deferredPreloads) {
      deferredPreloads()
      deferredPreloads = null
    }

    finishBoot('Data up to date')
  } catch (err) {
    console.error('[app] Data loading failed:', err)
    store.set('fetchErrors', [...store.get('fetchErrors'), err.message])
    store.set('loading', false)
    // Show error state in page container
    const container = document.getElementById('page-container')
    if (container) {
      container.innerHTML = `
        <div class="error-page-overlay">
          <div class="error-page-box">
            <div class="error-page-icon">!</div>
            <h2 class="error-page-title">Failed to load data</h2>
            <p class="error-page-subtitle">${err.message || 'Network error'}</p>
            <div class="error-page-actions">
              <button class="error-page-btn error-page-btn-primary" onclick="location.reload()">Retry</button>
            </div>
          </div>
        </div>
      `
    }
  }
}

// ── Active Nav State ──
function updateActiveNav() {
  const hash = window.location.hash || '#/'
  document.querySelectorAll('.nav-tab').forEach((tab) => {
    const href = tab.getAttribute('href')
    if (!href) return
    const isActive = hash === href || (href !== '#/' && hash.startsWith(href))
    tab.classList.toggle('active', isActive)
    tab.setAttribute('aria-current', isActive ? 'page' : 'false')
  })
}
window.addEventListener('hashchange', updateActiveNav)

// ── Reduced Motion ──
function applyReducedMotion() {
  document.documentElement.classList.toggle('reduced-motion', store.get('reducedMotion'))
}
store.subscribe('reducedMotion', applyReducedMotion)

// ── Modal Event Listeners ──
// Patches only exist in per-bundle files, so every entry point waits for the
// records it needs (a single round trip, single-flighted and cached) before
// the modal opens — otherwise the Patches tab renders empty.
function hideDevChannels() {
  return localStorage.getItem('morphe_hide_dev') === 'true' ? ['dev'] : []
}

async function handleOpenApp(detail) {
  const pkg = detail?.app?.package
  if (pkg) await ensureAppBundles(pkg, { excludeChannels: hideDevChannels() })
  openAppDetailModal(detail)
}

async function handleOpenBundle(detail) {
  if (detail?.bundleName) await ensureBundleChannels(detail.bundleName, detail.channels || [])
  openBundleModal(detail)
}

// Persist bundle detail warmed by bundleLoader once the main thread is idle;
// saveBundleState's signature check makes this a no-op when nothing changed.
let persistTimer = null
onBundleDetailsLoaded(() => {
  if (persistTimer || !currentBundleIndex) return
  const run = () => {
    persistTimer = null
    saveBundleState(currentBundleIndex, store.get('bundles') || {})
  }
  if (typeof requestIdleCallback === 'function') {
    persistTimer = requestIdleCallback(run, { timeout: 4000 })
  } else {
    persistTimer = setTimeout(run, 2000)
  }
})

window.addEventListener('open-app', (e) => {
  handleOpenApp(e.detail).catch((err) => console.error('[app] open-app failed:', err))
})

window.addEventListener('open-bundle-history', async (e) => {
  await ensureChangelog()
  openBundleHistoryModal(e.detail.bundleName, e.detail.bundleNames)
})

window.addEventListener('open-bundle', (e) => {
  handleOpenBundle(e.detail).catch((err) => console.error('[app] open-bundle failed:', err))
})

// ── URL Parameter Deep Linking ──
async function handleUrlParams() {
  const params = new URLSearchParams(window.location.search)
  const openApp = params.get('open-app')
  const patchName = params.get('patch')
  if (openApp) {
    // Deep links can land before loadData finishes; wait for the directory
    await ensureAppBundles(openApp, { excludeChannels: hideDevChannels() })
    const bundles = store.get('bundles') || {}
    // Find app by package across all bundles
    for (const [key, bundle] of Object.entries(bundles)) {
      const appData = bundle.apps?.find((a) => (typeof a === 'string' ? a : a.package) === openApp)
      if (appData) {
        const app = typeof appData === 'string' ? { package: appData } : appData
        openAppDetailModal({
          app,
          bundleName: parseBundleKey(key).name,
          channels: [bundle.channel || 'stable'],
          patchName,
        })
        break
      }
    }
    // Clean URL
    history.replaceState(null, '', window.location.pathname + window.location.hash)
  }
}

// ── Boot ──
async function boot() {
  applyReducedMotion()
  updateThemeMeta(savedTheme)
  renderShell()
  syncColorGameChip()
  setupRoutes()
  updateActiveNav()

  // Load data in background, then start router
  loadData().then(() => {
    router.rerender()
    updateActiveNav()
    handleUrlParams().catch((err) => console.error('[app] deep link failed:', err))
  })

  // Also start router immediately so hash routes work while data loads
  router.start()
}

boot()
