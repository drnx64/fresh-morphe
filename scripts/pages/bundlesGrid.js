/**
 * Bundles grid — groups bundles, renders BundleCards.
 * Includes sort dropdown (name, stars, app count, version).
 *
 * renderBundleCardsInto() can be called again against an existing grid to
 * refill it (dashboard subscriptions use this as data streams in).
 */
import { el } from '../ui.js'
import * as store from '../store.js'
import { resolveAppName, sortBundleNames, groupBundleCards } from '../utils/misc.js'
import { renderBundleCard } from './bundleCard.js'

const SORT_OPTIONS = [
  { value: 'name', label: 'Name' },
  { value: 'stars', label: 'Stars' },
  { value: 'apps', label: 'App Count' },
  { value: 'version', label: 'Version' },
]

// Invalidates any in-flight progressive-render chain when a refill starts
let renderGeneration = 0

// Cards render a batch per animation frame, so a card from the bottom of the
// grid does not exist yet when #/bundles lands. Arrivals from What's New and
// the search dropdown set a target; the loop below waits for that card, then
// scrolls to it. ~10 seconds is far longer than any real render.
const FOCUS_FRAME_BUDGET = 600
let focusTarget = null
let focusFrames = 0

/**
 * Scroll to a bundle's card once the grid has rendered it.
 * Safe to call before navigating — it polls rather than assuming the grid exists,
 * and it is a no-op when the card never appears (filtered out, or gone).
 * @param {string} bundleName
 */
export function focusBundleCard(bundleName) {
  if (!bundleName) return
  focusTarget = String(bundleName)
  focusFrames = 0
  requestAnimationFrame(tryFocusBundleCard)
}

function tryFocusBundleCard() {
  if (!focusTarget) return
  const grid = document.getElementById('bundles-grid-container')
  // Escape only what an attribute-string selector needs; CSS.escape() would
  // escape leading digits, which is wrong inside a quoted value.
  const needle = focusTarget.replace(/["\\]/g, '\\$&')
  const card = grid?.querySelector(`[data-bundle-name="${needle}"]`)
  if (card) {
    focusTarget = null
    card.scrollIntoView({ block: 'center', behavior: 'smooth' })
    card.classList.add('bundle-highlight-pulse')
    card.addEventListener('animationend', () => card.classList.remove('bundle-highlight-pulse'), { once: true })
    return
  }
  if (++focusFrames > FOCUS_FRAME_BUDGET) {
    focusTarget = null
    return
  }
  requestAnimationFrame(tryFocusBundleCard)
}

export function renderBundlesGrid() {
  const section = el('section', { class: 'bundles-section', 'aria-labelledby': 'bundles-heading' })

  const headingRow = el('div', { class: 'bundles-heading-row' })
  headingRow.appendChild(el('h2', { class: 'section-title', id: 'bundles-heading' }, ['Patch Bundles']))

  const sortSelect = el('select', { class: 'bundles-sort-select', 'aria-label': 'Sort bundles' })
  for (const opt of SORT_OPTIONS) {
    sortSelect.appendChild(el('option', { value: opt.value }, [opt.label]))
  }
  sortSelect.value = store.get('bundleSort') || 'name'
  headingRow.appendChild(sortSelect)
  section.appendChild(headingRow)

  const grid = el('div', { class: 'bundles-grid', id: 'bundles-grid-container' })
  section.appendChild(grid)

  sortSelect.addEventListener('change', () => {
    store.set('bundleSort', sortSelect.value)
    renderBundleCardsInto(grid)
  })
  renderBundleCardsInto(grid)

  return section
}

function effectiveAppCount(bundle) {
  return Math.max(bundle.apps?.length || 0, bundle.appCount || 0)
}

/**
 * Fill a grid element with bundle cards built from current store state.
 * @param {HTMLElement} grid
 */
export function renderBundleCardsInto(grid) {
  const gen = ++renderGeneration
  grid.replaceChildren()

  const bundles = store.get('bundles') || {}
  const filters = store.get('filters') || {}
  const nameCache = store.get('nameCache') || {}

  // Group by bundle name (shared with bundleCard's on-demand re-grouping)
  const grouped = groupBundleCards(bundles, (b) => {
    if (filters.channel !== 'all' && b.channel !== filters.channel) return false
    if (filters.hideArchived && b.isArchived) return false
    if (filters.hidePreRelease && b.isPreRelease) return false
    return true
  })

  let list = Object.values(grouped)

  // Search filter
  if (filters.search) {
    const q = filters.search.toLowerCase()
    list = list.filter((b) => {
      if (b.bundle.toLowerCase().includes(q)) return true
      if (b.patches_name?.toLowerCase().includes(q)) return true
      return b.apps?.some(
        (app) =>
          resolveAppName(app, nameCache).toLowerCase().includes(q) ||
          app.package.toLowerCase().includes(q),
      )
    })
  }

  // Sort
  const sortBy = store.get('bundleSort') || 'name'
  if (sortBy === 'name') {
    list = sortBundleNames(list)
  } else {
    list.sort((a, b) => {
      if (sortBy === 'stars') return (b.stars || 0) - (a.stars || 0)
      if (sortBy === 'apps') return effectiveAppCount(b) - effectiveAppCount(a)
      if (sortBy === 'version') return (b.version || '').localeCompare(a.version || '')
      return 0
    })
  }

  if (list.length === 0) {
    grid.appendChild(el('div', { class: 'loading-state' }, ['No matching Morphe bundles found.']))
    return
  }

  // Progressive rendering
  let rendered = 0
  const BATCH = 12
  const total = list.length

  function renderBatch() {
    if (gen !== renderGeneration) return
    const end = Math.min(rendered + BATCH, total)
    for (let i = rendered; i < end; i++) {
      grid.appendChild(renderBundleCard(list[i]))
    }
    rendered = end
    if (rendered < total) {
      requestAnimationFrame(renderBatch)
    }
  }

  renderBatch()
}
