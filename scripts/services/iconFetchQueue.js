/**
 * Icon fetch queue — viewport-first warming of the icon cache.
 *
 * Display never waits on this queue: <img> elements lazy-load originals on
 * their own. The queue only warms IndexedDB with resized WebP data URLs so
 * later renders are instant and survive offline.
 *
 * Two lanes share one worker:
 *  - Priority: icons scrolled into view (IntersectionObserver) fetch back to
 *    back, up to PRIORITY_BATCH at a time.
 *  - Sweep: the rest of the icon universe in batches of SWEEP_BATCH with a
 *    short pause between batches, so a full sweep takes a couple of minutes
 *    instead of ~20.
 *
 * 429/403 responses back off exponentially inside iconCache.loadImage, which
 * pauses both lanes (rate-limited URLs are requeued, not failed) and ratchets
 * the sweep back down to one request at a time. Remaining work persists to
 * localStorage so a reload resumes where it left off.
 */
import {
  fetchAndCacheIcon,
  getCachedIconDataUrl,
  hydrateIconCache,
  registerIconUrls,
  pruneStoredImages,
} from './iconCache.js'
import { devLog } from '../utils/devLog.js'

const QUEUE_KEY = 'fresh_icon_queue_v1'
// Icons per batch. Four is enough to turn a ~21 minute serial sweep into a
// couple of minutes without looking like a scraper; the shared 429/403 gate in
// iconCache still protects every request.
const SWEEP_BATCH = 4
// Visible icons may jump the queue, but a fast scroll must not open 40 sockets.
const PRIORITY_BATCH = 3
const SWEEP_PAUSE_MIN_MS = 300
const SWEEP_PAUSE_JITTER_MS = 400
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
  let outcome = 'failed'
  try {
    const result = await fetchAndCacheIcon(url)
    if (result) {
      done.add(url)
      outcome = 'done'
    } else {
      addFailed(url)
    }
  } catch (err) {
    if (err && err.rateLimited) {
      // loadImage armed the shared backoff; requeue for a later pass.
      pending.unshift(url)
      outcome = 'rateLimited'
    } else {
      addFailed(url)
    }
  }
  report()
  persistSoon()
  return outcome
}

// Rate-limit pressure ratchets the sweep down to one request at a time; a clean
// batch lets it climb back towards SWEEP_BATCH one step at a time.
let sweepBatch = SWEEP_BATCH

async function workerLoop() {
  while (true) {
    // Backgrounded tabs burn no network on the cache-warming sweep; the
    // worker resumes within HIDDEN_POLL_MS of the tab becoming visible.
    if (typeof document !== 'undefined' && document.hidden) {
      if (!priorityQueue.length && !(sweepStarted && pending.length)) break
      await sleep(HIDDEN_POLL_MS)
      continue
    }
    const fromPriority = priorityQueue.length > 0
    if (!fromPriority && !(sweepStarted && pending.length)) break

    const source = fromPriority ? priorityQueue : pending
    const size = fromPriority ? PRIORITY_BATCH : sweepBatch
    const batch = source.splice(0, Math.min(size, source.length))
    if (!batch.length) continue

    const outcomes = await Promise.all(batch.map(fetchOne))
    if (outcomes.includes('rateLimited')) {
      sweepBatch = 1
    } else if (!fromPriority) {
      sweepBatch = Math.min(SWEEP_BATCH, sweepBatch + 1)
    }
    // Visible icons go back-to-back; the sweep paces itself between batches.
    if (!fromPriority && (pending.length || priorityQueue.length)) {
      await sleep(SWEEP_PAUSE_MIN_MS + Math.random() * SWEEP_PAUSE_JITTER_MS)
    }
  }
}

function onIdle() {
  persistNow()
  devLog('icon queue idle', { done: done.size, pending: pending.length, failed: failed.length })
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
 * Reconcile the queues with what IndexedDB already had.
 * @param {string[]} warmUrls
 */
function applyHydration(warmUrls) {
  if (!warmUrls || !warmUrls.length) return
  const warm = new Set(warmUrls)
  for (const url of warmUrls) done.add(url)
  pending = pending.filter((url) => !warm.has(url))
  priorityQueue = priorityQueue.filter((url) => !warm.has(url))
  failed = failed.filter((url) => !warm.has(url))
  devLog('icon queue hydrated', { warm: warm.size, pending: pending.length })
  report()
  persistSoon()
}

/**
 * Register the icon universe from app_cache and start observing the DOM so
 * icons scrolled into view jump the sweep queue. No network happens here.
 * Idempotent - safe if called more than once per load.
 *
 * Returns the hydration promise so callers can start the sweep only once the
 * warm set is known (otherwise progress starts at 0 on every reload and the
 * sweep re-attempts icons IndexedDB already has).
 *
 * @param {Object<string,string>} iconMap
 * @returns {Promise<string[]>} icon URLs already warm in IndexedDB
 */
export function initIconQueue(iconMap) {
  if (inited) return Promise.resolve([])
  inited = true
  registerIconUrls(iconMap)
  for (const val of Object.values(iconMap)) {
    if (typeof val !== 'string' || !val.startsWith('http')) continue
    universe.add(val)
    if (getCachedIconDataUrl(val)) done.add(val)
    else pending.push(val)
  }
  restore()
  // A fresh page has an empty memory map even when IndexedDB holds the whole
  // universe, so everything above looks cold. Reconcile once the batch read
  // lands: warmed URLs leave both queues and the progress bar starts where the
  // cache actually is instead of at zero.
  const hydration = hydrateIconCache(iconMap)
    .then(applyHydration)
    .catch((err) => {
      devLog('icon queue hydrate failed', err?.message)
      return []
    })
  devLog('icon queue init', {
    universe: universe.size,
    warm: done.size,
    pending: pending.length,
  })
  if (typeof window !== 'undefined') window.addEventListener('beforeunload', persistNow)
  startObservers()
  return hydration
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
  devLog('icon queue start', { pending: pending.length, priority: priorityQueue.length, failed: failed.length })
  report()
  ensureWorker()
  return new Promise((resolve) => {
    idleWaiters.push({ resolve })
  })
}
