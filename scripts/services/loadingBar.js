/**
 * Top loading bar — a job registry for the thin bar at the top of the page.
 *
 * The bar used to be boot-only: `loadData` drove it from 5% to 100%, then
 * `finishBoot` hid it, so every deferred job after that — icon caching, avatar
 * caching, the 2.75 MB changelog — ran with no ambient feedback at all. The
 * work toast that reports them closes itself after 1.5s, which left the site
 * looking idle while it was still fetching.
 *
 * Jobs are registered here rather than writing the store directly, because the
 * bar has exactly one percentage and concurrent work would fight over it. The
 * bar reports the most recently begun job (a browser tab shows one bar, not a
 * sum of them), a job with an unknown total renders as an indeterminate sweep,
 * and the bar hides only when the last job ends — boot included, so a job that
 * outlives boot keeps it on screen instead of being cut off mid-flight.
 *
 * The status pill stays boot-only (`announce`). It is fixed over the sticky
 * header, so leaving it up for a six-minute icon sweep would park a chip on the
 * logo; the bar alone is the ambient signal for background work.
 */
import * as store from '../store.js'

/**
 * @typedef {Object} Job
 * @property {string} label
 * @property {number} done
 * @property {number} total - 0 means unknown
 * @property {boolean} counts - append "done/total" to the pill
 * @property {boolean} announce - may use the status pill
 */

/** @type {Map<string, Job>} */
const jobs = new Map()

// A job that ends in under this long should not flash the bar.
const SHOW_DELAY_MS = 150
let showTimer = null

/**
 * Register a job and make it the one the bar reports.
 * @param {string} id - stable per job; re-beginning the same id resets it
 * @param {string} [label] - job name, shown in the status pill when `announce`
 * @param {{counts?: boolean, announce?: boolean}} [opts] - `counts` appends
 *   "done/total" to the pill; `announce` lets the job use the pill at all
 */
export function beginJob(id, label = '', opts = {}) {
  // Re-insert so Map order keeps working as begin order for `currentJob`.
  jobs.delete(id)
  jobs.set(id, {
    label,
    done: 0,
    total: 0,
    counts: !!opts.counts,
    announce: !!opts.announce,
  })
  show()
  publish()
}

/**
 * Report progress for a running job. Unknown ids are ignored, so a late
 * callback from work that already ended cannot resurrect the bar.
 * @param {string} id
 * @param {number} done
 * @param {number} total - 0 means "unknown", rendered indeterminate
 * @param {string} [label] - replaces the label when given
 */
export function progressJob(id, done, total = 0, label = '') {
  const job = jobs.get(id)
  if (!job) return
  job.done = Number(done) || 0
  job.total = Number(total) || 0
  if (label) job.label = label
  publish()
}

/**
 * Drop a job. The bar falls back to whatever is still running, and hides once
 * nothing is.
 * @param {string} id
 */
export function endJob(id) {
  if (!jobs.delete(id)) return
  if (!jobs.size) {
    hide()
    return
  }
  publish()
}

/** @returns {Job | undefined} */
function currentJob() {
  let last
  for (const job of jobs.values()) last = job
  return last
}

/**
 * The job allowed to use the status pill — the last active one that opted in.
 * Background sweeps never announce, so the pill keeps boot's final text until
 * boot ends instead of being wiped by an icon sweep starting alongside it.
 * @returns {Job | undefined}
 */
function announcingJob() {
  let last
  for (const job of jobs.values()) if (job.announce) last = job
  return last
}

function publish() {
  const job = currentJob()
  if (!job) return

  // An unknown total cannot be drawn as a percentage, so the CSS sweep owns the
  // width instead. `store.set` skips unchanged values, so this is cheap.
  const indeterminate = !(job.total > 0)
  store.set('loadingIndeterminate', indeterminate)
  store.set(
    'loadingProgress',
    indeterminate ? 0 : Math.max(0, Math.min(100, (job.done / job.total) * 100)),
  )

  // Empty means "no pill" — see the note on `announce` in the header comment.
  const pill = announcingJob()
  if (!pill) {
    store.set('loadingStatus', '')
    return
  }
  const counted = pill.counts && pill.total > 0
  store.set('loadingStatus', counted ? `${pill.label} ${pill.done}/${pill.total}` : pill.label)
}

function show() {
  // Boot starts with `loading` already true, so the bar is up immediately and
  // the delay only applies to background jobs arriving at an idle page.
  if (store.get('loading') || showTimer) return
  showTimer = setTimeout(() => {
    showTimer = null
    store.set('loading', true)
    publish()
  }, SHOW_DELAY_MS)
}

function hide() {
  if (showTimer) {
    clearTimeout(showTimer)
    showTimer = null
  }
  store.set('loading', false)
}
