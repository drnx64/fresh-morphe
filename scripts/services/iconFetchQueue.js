/**
 * Icon fetch queue — viewport-first warming of the icon cache.
 *
 * Display never waits on this queue: <img> elements lazy-load originals on
 * their own. The queue only warms IndexedDB with resized WebP data URLs so
 * later renders are instant and survive offline.
 *
 * Two lanes share one worker, one fetch at a time:
 *  - Priority: icons scrolled into view (IntersectionObserver) fetch first.
 *  - Sweep: the rest of the icon universe one at a time with 1–2s pauses.
 *
 * 429/403 responses back off exponentially inside iconCache.loadImage, which
 * pauses both lanes (rate-limited URLs are requeued, not failed). Remaining
 * work persists to localStorage so a reload resumes where it left off.
 */
import {
  fetchAndCacheIcon,
  getCachedIconDataUrl,
  registerIconUrls,
  pruneStoredImages,
} from './iconCache.js'

const QUEUE_KEY = 'morphe_icon_queue_v1'
const SWEEP_PAUSE_MIN_MS = 1000
const SWEEP_PAUSE_JITTER_MS = 1000
const PERSIST_THROTTLE_MS = 2000
// How often a backgrounded tab re-checks whether it became visible again.
const HIDDEN_POLL_MS = 2000

/** @type {Set<string>} all http icon URLs from app_cache */
let universe = new Set()
/** @type {string[]} sweep lane (not yet attempted this session) */
let pending = []
/** @type {string[]} visible icons, fetched first */
let priorityQueue = []
/** @type {Set<string>} successfully cached this session */
let done = new Set()
/** @type {string[]} ordinary failures this session (retryable) */
let failed = []
let inited = false
let sweepStarted = false
let workerRunning = false
/** @type {((done: number, total: number) => void)|null} */
let progressCb = null
/** @type {Array<{resolve: (value: {failed: number}) => void}>} */
let idleWaiters = []
/** @type {IntersectionObserver|null} */
let io = null
/** @type {MutationObserver|null} */
let mo = null
let persistTimer = null
let persistDirty = false

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function report() {
  if (universe.size && progressCb) progressCb(done.size, universe.size)
}

function addFailed(url) {
  if (!failed.includes(url)) failed.push(url)
}

function persistNow() {
  persistDirty = false
  try {
    localStorage.setItem(QUEUE_KEY, JSON.stringify({ pending, failed, savedAt: Date.now() }))
  } catch {
    /* storage full or unavailable — the queue still works this session */
  }
}

function persistSoon() {
  persistDirty = true
  if (persistTimer) return
  persistTimer = setTimeout(() => {
    persistTimer = null
    if (persistDirty) persistNow()
  }, PERSIST_THROTTLE_MS)
}

function restore() {
  try {
    const raw = localStorage.getItem(QUEUE_KEY)
    if (!raw) return
    const saved = JSON.parse(raw)
    const have = new Set(pending)
    for (const url of [...(saved.pending || []), ...(saved.failed || [])]) {
      if (typeof url === 'string' && universe.has(url) && !have.has(url) && !done.has(url)) {
        pending.push(url)
        have.add(url)
      }
    }
  } catch {
    /* ignore corrupt saved state */
  }
}

function prioritize(url) {
  if (!universe.has(url) || done.has(url) || priorityQueue.includes(url)) return
  const i = pending.indexOf(url)
  if (i >= 0) pending.splice(i, 1)
  const f = failed.indexOf(url)
  if (f >= 0) failed.splice(f, 1)
  priorityQueue.push(url)
  ensureWorker()
}

async function fetchOne(url) {
  try {
    const result = await fetchAndCacheIcon(url)
    if (result) {
      done.add(url)
    } else {
      addFailed(url)
    }
  } catch (err) {
    if (err && err.rateLimited) {
      // loadImage armed the shared backoff; requeue for a later pass.
      pending.unshift(url)
    } else {
      addFailed(url)
    }
  }
  report()
  persistSoon()
}

async function workerLoop() {
  while (true) {
    // Backgrounded tabs burn no network on the cache-warming sweep; the
    // worker resumes within HIDDEN_POLL_MS of the tab becoming visible.
    if (typeof document !== 'undefined' && document.hidden) {
      if (!priorityQueue.length && !(sweepStarted && pending.length)) break
      await sleep(HIDDEN_POLL_MS)
      continue
    }
    if (priorityQueue.length) {
      await fetchOne(priorityQueue.shift())
      continue
    }
    if (sweepStarted && pending.length) {
      await fetchOne(pending.shift())
      if (pending.length || priorityQueue.length) {
        await sleep(SWEEP_PAUSE_MIN_MS + Math.random() * SWEEP_PAUSE_JITTER_MS)
      }
      continue
    }
    break
  }
}

function onIdle() {
  persistNow()
  pruneStoredImages().catch(() => {})
  const waiters = idleWaiters
  idleWaiters = []
  for (const w of waiters) w.resolve({ failed: failed.length })
}

function ensureWorker() {
  if (workerRunning) return
  workerRunning = true
  workerLoop()
    .catch((err) => console.error('[iconQueue] worker error:', err))
    .finally(() => {
      workerRunning = false
      if (priorityQueue.length || (sweepStarted && pending.length)) {
        ensureWorker()
      } else {
        onIdle()
      }
    })
}

function forEachIconElement(root, fn) {
  if (root.matches && root.matches('[data-icon-url]')) fn(root)
  if (root.querySelectorAll) root.querySelectorAll('[data-icon-url]').forEach(fn)
}

function startObservers() {
  if (io || typeof IntersectionObserver === 'undefined') return
  io = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue
      io.unobserve(entry.target)
      const url = entry.target.getAttribute('data-icon-url')
      if (url) prioritize(url)
    }
  }, { rootMargin: '200px' })
  mo = new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType === 1) forEachIconElement(node, (el) => io.observe(el))
      }
      for (const node of m.removedNodes) {
        if (node.nodeType === 1) forEachIconElement(node, (el) => io.unobserve(el))
      }
    }
  })
  const start = () => {
    mo.observe(document.body, { childList: true, subtree: true })
    forEachIconElement(document.body, (el) => io.observe(el))
  }
  if (document.body) start()
  else document.addEventListener('DOMContentLoaded', start, { once: true })
}

/**
 * Register the icon universe from app_cache and start observing the DOM so
 * icons scrolled into view jump the sweep queue. No network happens here.
 * Idempotent — safe if called more than once per load.
 * @param {Object<string,string>} iconMap
 */
export function initIconQueue(iconMap) {
  if (inited) return
  inited = true
  registerIconUrls(iconMap)
  for (const val of Object.values(iconMap)) {
    if (typeof val !== 'string' || !val.startsWith('http')) continue
    universe.add(val)
    if (getCachedIconDataUrl(val)) done.add(val)
    else pending.push(val)
  }
  restore()
  if (typeof window !== 'undefined') window.addEventListener('beforeunload', persistNow)
  startObservers()
}

/**
 * Start (or resume) the paced sweep of remaining icons. Resolves when both
 * lanes are idle. Requeues prior failures when includeFailed is set (retry).
 * @param {(done: number, total: number) => void} [onProgress]
 * @param {{includeFailed?: boolean}} [opts]
 * @returns {Promise<{failed: number}>}
 */
export function runIconQueue(onProgress, { includeFailed = false } = {}) {
  if (onProgress) progressCb = onProgress
  if (!inited) return Promise.resolve({ failed: 0 })
  if (includeFailed && failed.length) {
    const requeue = failed.filter((url) => !pending.includes(url) && !priorityQueue.includes(url))
    failed = []
    pending.push(...requeue)
  }
  sweepStarted = true
  report()
  ensureWorker()
  return new Promise((resolve) => {
    idleWaiters.push({ resolve })
  })
}
