/**
 * Apps page - what's new + directory of all apps across bundles.
 */

import { el, mount } from '../ui.js'
import * as store from '../store.js'
import { buildAppIndex, resolveAppName, getAppIconUrl, renderAppIcon, suggestFuzzy } from '../utils/misc.js'
import { getPlayStoreUrl } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { groupAppsIntoFamilies, developerLabel } from '../utils/appFamily.js'
import { featuredApps } from '../utils/popular.js'
import { renderTodayUpdates } from './todayUpdates.js'
import { skeletonGrid } from '../components/skeleton.js'

const RENDER_BATCH = 24
const STACK_FILL_BATCH = 24

// Fan geometry in px, by distance from the focused card. The near step is the
// reference deck's EXPAND_GAP (110px on a 96px card) scaled to our 68px cards,
// and it shrinks on short bands so distant cards still get room — piecewise
// spacing keeps the whole family inside the fixed band: no scrollbar, no
// hover-scroll loops, ever.
const FAN_NEAR_STEP = 78
const FAN_FAR_STEP_MIN = 2 // slivers stay hoverable even in a huge family
const FAN_DENSE_STEP = 40 // below this the fringe drops its text (edges only)
const FAN_CLOSE_MS = 210 // fade-out before the open classes drop
const FAN_RESIZE_MS = 120

let unsubscribeBundles = null

// One pile is open at a time: opening a stack shuts the others. Module-level
// because the grid is rebuilt on every refill and a stale closer must not
// outlive the node it closes.
const stackClosers = new Map()

// Per-stack fan relayout, refreshed on viewport resize (the band is
// viewport-height based). Cleared with stackClosers on every refill.
const fanLayouts = new Map()

let fanResizeTimer = null
window.addEventListener('resize', () => {
  if (fanResizeTimer) clearTimeout(fanResizeTimer)
  fanResizeTimer = setTimeout(() => {
    fanResizeTimer = null
    for (const layout of fanLayouts.values()) layout()
  }, FAN_RESIZE_MS)
})

// Dim scrim behind an open fan: it turns "click anywhere else" into a
// collapse and doubles as the backdrop that makes the centered deck read as
// its own surface over the grid.
let stackBackdrop = null
let stackBackdropOwner = ''

function dropStackBackdrop(owner = null) {
  if (!stackBackdrop) return
  if (owner !== null && stackBackdropOwner !== owner) return
  stackBackdrop.remove()
  stackBackdrop = null
  stackBackdropOwner = ''
}

function showStackBackdrop(owner, onDismiss) {
  dropStackBackdrop()
  stackBackdropOwner = owner
  stackBackdrop = el('div', { class: 'app-stack-backdrop', 'aria-hidden': 'true' })
  stackBackdrop.addEventListener('pointerdown', (e) => {
    e.preventDefault()
    onDismiss()
  })
  // Wheel over the scrim would scroll the grid behind the dim; the fan itself
  // must never scroll either (pinch-zoom stays intact — see touch-action).
  stackBackdrop.addEventListener('wheel', (e) => {
    if (!e.ctrlKey) e.preventDefault()
  }, { passive: false })
  document.body.appendChild(stackBackdrop)
}

function openApp(app) {
  window.dispatchEvent(new CustomEvent('open-app', {
    detail: { app: { package: app.package, app_name: app.name } },
  }))
}

function makeCardInteractive(card, activate) {
  card.style.cursor = 'pointer'
  card.setAttribute('role', 'button')
  card.setAttribute('tabindex', '0')
  card.addEventListener('click', activate)
  card.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    activate()
  })
  return card
}

function buildAppCard(app, iconCache, onActivate) {
  const card = el('div', { class: 'app-card' })
  const iconHtml = renderAppIcon({ package: app.package, app_name: app.name }, iconCache)
  const bundleCount = app.bundles.length
  const developer = developerLabel(app.package, app.developer)
  card.innerHTML = `
    <div class="app-card-main">
      ${iconHtml}
      <div class="app-card-info">
        <span class="app-card-name">${escHtml(app.name)}</span>
        ${developer ? `<span class="app-card-dev">${escHtml(developer)}</span>` : ''}
        <span class="app-card-pkg">${escHtml(app.package)}</span>
      </div>
      <span class="app-card-bundle-count">${bundleCount} bundle${bundleCount !== 1 ? 's' : ''}</span>
    </div>
  `
  return makeCardInteractive(card, onActivate || (() => openApp(app)))
}

export function renderApps(container) {
  const page = el('div', { class: 'apps-page' })

  // What's New (hero section on apps page)
  page.appendChild(renderTodayUpdates())

  // Apps directory
  const header = el('div', { class: 'apps-header' }, [
    el('h2', { class: 'section-title' }, ['Apps Directory']),
  ])

  const searchInput = el('input', {
    type: 'text',
    class: 'apps-search-input',
    placeholder: 'Search apps by name or package...',
    'aria-label': 'Search apps',
  })

  const clearBtn = el('button', {
    class: 'search-bar-clear',
    'aria-label': 'Clear search',
    style: { display: 'none' },
  }, ['✕'])

  const searchRow = el('div', { class: 'search-row' }, [searchInput, clearBtn])

  const grid = el('div', { class: 'apps-grid', id: 'apps-grid' })

  // Featured row: the household names, pinned above the alphabetical registry.
  // Hidden while searching — a search is already the user's own shortlist.
  const featuredEl = el('section', {
    class: 'apps-featured',
    'aria-label': 'Popular apps',
    hidden: true,
  })

  page.appendChild(header)
  page.appendChild(featuredEl)
  page.appendChild(searchRow)
  page.appendChild(grid)
  mount(container, page)

  // Build app index
  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}
  const developerCache = store.get('developerCache') || {}
  let appIndex = buildAppIndex(store.get('bundles') || {}, nameCache, iconCache, developerCache)

  // Invalidates an in-flight progressive-render chain when the list refills
  let listGeneration = 0

  // Stacks the user opened; survives a refill so streaming bundles don't shut them
  const openStacks = new Set()

  function rebuildIndex() {
    appIndex = buildAppIndex(
      store.get('bundles') || {},
      store.get('nameCache') || {},
      store.get('iconCache') || {},
      store.get('developerCache') || {},
    )
  }

  /**
   * Paint the Popular row. Icon-less apps are skipped: the row exists for
   * instant recognition, and a letter tile for YouTube is not that.
   */
  function renderFeatured() {
    featuredEl.replaceChildren()
    const apps = featuredApps(appIndex, 12)
    if (apps.length === 0) {
      featuredEl.hidden = true
      return
    }
    const head = el('div', { class: 'apps-featured-head' }, [
      el('h3', { class: 'apps-featured-title' }, ['Popular']),
      el('span', { class: 'apps-featured-sub' }, ['Apps people patch most']),
    ])
    const row = el('div', { class: 'apps-featured-row' })
    const icons = store.get('iconCache') || {}
    for (const app of apps) {
      const card = buildAppCard(app, icons)
      card.classList.add('app-card--featured')
      row.appendChild(card)
    }
    featuredEl.append(head, row)
    featuredEl.hidden = false
  }

  /**
   * App family: a deck of its own app cards.
   *
   * Collapsed, every card occupies the same spot in the grid cell: the front
   * one is the family's first app, each card behind it steps up, shrinks and
   * tilts slightly, so only its top edge shows — the card-stack silhouette,
   * made of real cards rather than a decorated icon.
   *
   * Clicking the pile (never hovering — touch, pointer and keyboard share one
   * path) opens the deck as a viewport-centered fan: a fixed, height-
   * constrained box over a dim scrim that never scrolls. The hovered or
   * clicked card locks to the vertical center of the band; cards before it
   * fan upward, cards after it downward, symmetrically around it. Distant
   * cards compress into slivers, so even a 50-app publisher fits the band
   * without a scrollbar — reach beyond the visible steps by hovering or
   * tapping a sliver, which pulls that card to the center and re-fans.
   *
   * Only the first four cards are built up front (a 50-app publisher is not
   * free to paint); the rest are appended in batches once the pile opens.
   */
  function buildAppStack(family, gen) {
    const { id, label, source, apps } = family
    const count = apps.length
    const pileId = `app-stack-${id.replace(/[^a-z0-9]+/gi, '-')}`
    const stack = el('div', { class: `app-stack ${source === 'developer' ? 'is-publisher' : 'is-namespace'}` })
    // Depth of the visible pile, capped at four cards (three peeking edges)
    stack.style.setProperty('--layers', String(Math.min(3, count - 1)))

    // Only visible once open: names the family and collapses the pile again.
    const caption = el('button', {
      class: 'app-stack-caption',
      type: 'button',
      'aria-expanded': 'false',
      'aria-controls': pileId,
      'aria-label': `Collapse ${label}, ${count} apps`,
    })
    caption.innerHTML = `
      <span class="app-stack-eyebrow">${source === 'developer' ? 'Publisher' : 'Package family'}</span>
      <span class="app-stack-caption-label">${escHtml(label)}</span>
      <span class="app-stack-count">${count}</span>
      <span class="app-stack-chevron" aria-hidden="true"></span>
    `

    const pile = el('div', { class: 'app-stack-pile', id: pileId, role: 'group', 'aria-label': `${label}, ${count} apps` })

    let built = 0
    let fanFocus = 0

    function buildNote(index) {
      const member = apps[index]
      const card = buildAppCard(member, store.get('iconCache') || {}, () => {
        // Collapsed, the expander owns the click. Open, clicking anything but
        // the centered card just pulls that card to the middle of the fan.
        if (!isOpen) return
        if (index !== fanFocus) {
          setFanFocus(index)
          return
        }
        openApp(member)
      })
      card.classList.add('app-stack-note')
      card.style.setProperty('--i', String(index))
      card.style.setProperty('--d', String(Math.min(index, 3)))
      if (index === 0) {
        card.classList.add('app-stack-note--front')
        if (count > 1) {
          card.appendChild(el('span', { class: 'app-stack-chip', 'aria-hidden': 'true' }, [`+${count - 1}`]))
        }
      }
      // Hovering a visible card in the open fan makes it the focused one: it
      // locks to the band's center and the rest re-fan around it. This is a
      // deliberate target hover — nothing moves unless a card is under the
      // pointer, and there is no auto-scroll listener anywhere.
      card.addEventListener('mouseenter', () => {
        if (isOpen && index !== fanFocus) setFanFocus(index)
      })
      return card
    }

    let filling = false
    function buildUpTo(limit) {
      const end = Math.min(limit, count)
      while (built < end) {
        pile.appendChild(buildNote(built))
        built++
      }
    }
    buildUpTo(4)

    function fillRemaining() {
      if (filling || built >= count) return
      filling = true
      const fillBatch = () => {
        if (gen !== listGeneration) return
        buildUpTo(built + STACK_FILL_BATCH)
        layoutFan()
        if (built < count) requestAnimationFrame(fillBatch)
        else filling = false
      }
      requestAnimationFrame(fillBatch)
    }

    /**
     * Place every built card in the open fan. Spacing is piecewise by
     * distance to the focused card: full reference-style gaps nearby
     * (|Δ| × near), compressed slivers beyond, clamped so the farthest card
     * lands on the band edge — the whole family stays inside the fixed band
     * with no scrollbar. Writes `--y` (fan offset + the band-centering term,
     * so Δ=0 locks the focused card to the vertical center), `--o`, `--s`,
     * `--z` plus the is-focus/is-far classes and the roving tabindex.
     */
    function layoutFan() {
      if (!isOpen) return
      const bandH = pile.clientHeight
      if (!bandH) return // detached (rebuilt mid-refill) — the rAF pass lays it out
      const cardH = parseFloat(getComputedStyle(stack).getPropertyValue('--card-h')) || 68
      const half = Math.max(0, (bandH - cardH) / 2)
      const farthest = Math.max(1, Math.max(fanFocus, built - 1 - fanFocus))
      // Near steps adapt to the band: the full reference gap where there is
      // room, tighter on short viewports so the fringe still gets its share
      // of the edge instead of piling up at it.
      const near = Math.min(FAN_NEAR_STEP, half * 0.34)
      const mid = near * 2
      const room = Math.max(0, half - mid)
      const farStep = farthest > 2
        ? Math.min(FAN_NEAR_STEP, Math.max(FAN_FAR_STEP_MIN, room / (farthest - 2)))
        : FAN_NEAR_STEP
      // Dense = the family is too big for readable steps: the far cards become
      // a tight fringe of edges (text would only smear at that spacing).
      const dense = farStep < FAN_DENSE_STEP
      stack.classList.toggle('is-dense', dense)
      const floor = dense ? 0.18 : 0.35

      for (let i = 0; i < built; i++) {
        const card = pile.children[i]
        if (!card) continue
        const delta = i - fanFocus
        const abs = Math.abs(delta)
        let y = abs <= 2 ? abs * near : mid + (abs - 2) * farStep
        // Clamped to the band edge: even a 50-app family lands inside the box
        // (on a short viewport the outer cards share the last sliver).
        y = Math.min(y, half)
        if (delta < 0) y = -y
        y += half // the focused card sits at the band's vertical center

        let opacity
        if (abs === 0) opacity = 1
        else if (abs === 1) opacity = 0.95
        else if (abs === 2) opacity = 0.75
        else {
          const t = farthest > 2 ? (abs - 2) / (farthest - 2) : 1
          opacity = Math.max(floor, 0.7 - 0.52 * Math.min(1, t))
        }

        card.style.setProperty('--y', `${y.toFixed(1)}px`)
        card.style.setProperty('--o', String(opacity))
        // The reference's target pop: focused card 1.02, the rest 0.98.
        card.style.setProperty('--s', abs === 0 ? '1.02' : '0.98')
        card.style.setProperty('--z', String(400 - abs))
        card.classList.toggle('is-focus', abs === 0)
        card.classList.toggle('is-far', abs > 2)
        card.tabIndex = abs === 0 ? 0 : -1
      }
    }

    /** Re-center the fan on another card; also moves the caret when asked. */
    function setFanFocus(index, { moveDomFocus = false } = {}) {
      const next = Math.max(0, Math.min(count - 1, index))
      if (next !== fanFocus) {
        fanFocus = next
        layoutFan()
      }
      if (moveDomFocus) {
        const card = pile.children[fanFocus]
        if (card) card.focus({ preventScroll: true })
      }
    }

    // The whole pile is one control while collapsed: the cards inside are inert
    // so a click anywhere on the stack opens it rather than the app under it.
    const expander = el('button', {
      class: 'app-stack-expander',
      type: 'button',
      'aria-expanded': 'false',
      'aria-controls': pileId,
      'aria-label': `Expand ${label}, ${count} apps`,
    })

    // One open/close flag drives everything; classes are presentational
    // (`is-open` is kept through the fade-out so the fan never teleports).
    let isOpen = false
    let closeTimer = null
    let onEscape = null

    function clearCloseTimer() {
      if (closeTimer) {
        clearTimeout(closeTimer)
        closeTimer = null
      }
    }

    function setExpanded(open, { via = 'api', force = false } = {}) {
      if (!force && open === isOpen) return

      if (open) {
        clearCloseTimer()
        stack.classList.remove('is-closing')
        isOpen = true
        stack.classList.add('is-open')
        fanFocus = 0
        for (const control of [expander, caption]) {
          control.setAttribute('aria-expanded', 'true')
        }
        // Collapsed cards and the caption must not be tabbable; the expander
        // owns the interaction until the pile is open.
        pile.toggleAttribute('inert', false)
        caption.tabIndex = 0
        caption.setAttribute('aria-hidden', 'false')

        // One pile at a time — a second one would overlap the first
        for (const [otherId, close] of stackClosers) {
          if (otherId !== id) close()
        }
        stackClosers.set(id, () => setExpanded(false, { via: 'elsewhere' }))
        openStacks.add(id)
        fanLayouts.set(id, layoutFan)
        // The fan is open to everyone now: a dim scrim doubles as the
        // click-away catcher (a hovered pile no longer closes on pointer-out —
        // click, Escape or the caption do).
        showStackBackdrop(id, () => setExpanded(false, { via: 'outside' }))
        // Measure the band and place the cards now, then once more next frame:
        // a stack rebuilt during a refill lays out while still detached.
        layoutFan()
        requestAnimationFrame(layoutFan)
        fillRemaining()
        onEscape = (e) => {
          if (e.key === 'Escape') setExpanded(false, { via: 'escape' })
        }
        document.addEventListener('keydown', onEscape)
        // A deliberate open hands focus to the collapse control, the first
        // thing inside the fan.
        if (via === 'click') caption.focus({ preventScroll: true })
      } else {
        // The fan fades out in place first (is-closing keeps the open layout
        // while opacity drops); the class swap at the end of the timer lets
        // the pile settle back into the grid cell under the fade-in.
        const wasOpen = stack.classList.contains('is-open')
        if (wasOpen) stack.classList.add('is-closing')
        isOpen = false
        openStacks.delete(id)
        stackClosers.delete(id)
        fanLayouts.delete(id)
        for (const control of [expander, caption]) {
          control.setAttribute('aria-expanded', 'false')
        }
        // Collapsed cards and the caption must not be tabbable; the expander
        // owns the interaction until the pile is open.
        pile.toggleAttribute('inert', true)
        caption.tabIndex = -1
        caption.setAttribute('aria-hidden', 'true')
        // The scrim fades with the fan; if another pile opened meanwhile it
        // owns the backdrop already (owner guard inside dropStackBackdrop).
        const scrim = stackBackdropOwner === id ? stackBackdrop : null
        if (wasOpen && scrim) {
          scrim.classList.add('is-out')
          setTimeout(() => {
            if (stackBackdrop === scrim) dropStackBackdrop(id)
          }, FAN_CLOSE_MS)
        } else {
          dropStackBackdrop(id)
        }
        if (onEscape) {
          document.removeEventListener('keydown', onEscape)
          onEscape = null
        }
        // The cards are about to go inert; focus would fall back to <body>.
        // is-closing puts the expander back on screen for this.
        if (stack.contains(document.activeElement)) expander.focus({ preventScroll: true })
        if (wasOpen) {
          clearCloseTimer()
          closeTimer = setTimeout(() => {
            closeTimer = null
            if (isOpen) return // reopened while fading
            stack.classList.remove('is-open', 'is-closing')
          }, FAN_CLOSE_MS)
        }
      }
    }

    expander.addEventListener('click', () => setExpanded(true, { via: 'click' }))
    caption.addEventListener('click', () => setExpanded(false, { via: 'click' }))

    // Caption and pile ride in one box: collapsed it sits in flow, open it floats
    // viewport-centered over the scrim
    const overlay = el('div', { class: 'app-stack-overlay' })
    overlay.appendChild(el('div', { class: 'app-stack-caption-wrap' }, [caption]))
    overlay.appendChild(pile)

    // Keyboard fan control: arrows walk the focus one card at a time, Home and
    // End jump to the deck's head and tail. Enter/Space on the focused card
    // open the app (the card's own key handler), Escape collapses the pile.
    overlay.addEventListener('keydown', (e) => {
      if (!isOpen) return
      let next = null
      if (e.key === 'ArrowDown') next = Math.min(fanFocus + 1, built - 1)
      else if (e.key === 'ArrowUp') next = Math.max(fanFocus - 1, 0)
      else if (e.key === 'Home') next = 0
      else if (e.key === 'End') next = built - 1
      else return
      e.preventDefault()
      setFanFocus(next, { moveDomFocus: true })
    })

    // The fan never scrolls: wheel gestures over it must not scroll the page
    // behind the scrim either (pinch-zoom stays intact).
    overlay.addEventListener('wheel', (e) => {
      if (!e.ctrlKey) e.preventDefault()
    }, { passive: false })

    stack.appendChild(overlay)
    stack.appendChild(expander)
    // Force: a fresh node is "collapsed" by class already, but its caption and
    // pile still need the collapsed attributes (inert, aria-hidden, tabIndex).
    setExpanded(openStacks.has(id), { force: true })
    return stack
  }

  function renderAppsList(query = '') {
    const gen = ++listGeneration
    grid.replaceChildren()
    // The old stacks are detached: their closers, fan layouts and any backdrop
    // go with them
    stackClosers.clear()
    fanLayouts.clear()
    dropStackBackdrop()

    if (query) featuredEl.hidden = true
    else renderFeatured()

    // Booting: the app index is still being built — show skeletons, not "no apps"
    if (store.get('booting') && appIndex.length === 0) {
      grid.appendChild(skeletonGrid(8))
      return
    }

    let list = appIndex
    if (query) {
      const q = query.toLowerCase()
      list = list.filter((app) =>
        app.name.toLowerCase().includes(q) || app.package.toLowerCase().includes(q)
      )
    } else {
      // Browse order: cards whose icon we already have lead, then A–Z inside
      // each tier. Search results stay as typed — a lookup wants rank, not
      // artwork. Icon downloads landing later re-run this via iconCache.
      list = [...list].sort(
        (a, b) =>
          (a.iconUrl ? 0 : 1) - (b.iconUrl ? 0 : 1) || a.name.localeCompare(b.name)
      )
    }

    if (list.length === 0) {
      const suggestionEl = el('div', { class: 'search-no-results' })
      if (query) {
        const suggestions = suggestFuzzy(query, appIndex)
        if (suggestions.length > 0) {
          suggestionEl.innerHTML = `<span class="search-no-results-text">No apps found for "${escHtml(query)}".</span>`
          const didYouMean = el('div', { class: 'search-suggestion' })
          didYouMean.innerHTML = `Did you mean: `
          for (let i = 0; i < suggestions.length; i++) {
            const sug = suggestions[i]
            const link = el('button', { class: 'search-suggestion-link' }, [sug.name])
            link.addEventListener('click', () => {
              searchInput.value = sug.name
              clearBtn.style.display = ''
              renderAppsList(sug.name)
            })
            didYouMean.appendChild(link)
            if (i < suggestions.length - 1) {
              didYouMean.appendChild(document.createTextNode(', '))
            }
          }
          suggestionEl.appendChild(didYouMean)
        } else {
          suggestionEl.innerHTML = `<span class="search-no-results-text">No apps found for "${escHtml(query)}".</span>`
        }
      } else {
        suggestionEl.innerHTML = '<span class="search-no-results-text">No apps found.</span>'
      }
      grid.appendChild(suggestionEl)
      return
    }

    // A search result set stays flat: the user is looking for one package inside
    // the family, so folding the whole publisher back into a stack would hide it.
    const entries = query ? list.map((app) => ({ kind: 'app', app })) : groupAppsIntoFamilies(list)

    let rendered = 0
    function renderBatch() {
      if (gen !== listGeneration) return
      const end = Math.min(rendered + RENDER_BATCH, entries.length)
      for (let i = rendered; i < end; i++) {
        const entry = entries[i]
        grid.appendChild(
          entry.kind === 'family'
            ? buildAppStack(entry, gen)
            : buildAppCard(entry.app, store.get('iconCache') || {})
        )
      }
      rendered = end
      if (rendered < entries.length) requestAnimationFrame(renderBatch)
    }
    renderBatch()
  }

  renderAppsList()

  // Streaming boot: refill the grid as bundle files land (keeps search input focus)
  if (unsubscribeBundles) unsubscribeBundles()
  let unsubCacheKeys = []
  function handleStoreChange() {
    if (!page.isConnected) {
      unsubscribeBundles()
      for (const off of unsubCacheKeys) off()
      unsubCacheKeys = []
      // An open pile's backdrop lives on <body>, outliving the page
      dropStackBackdrop()
      stackClosers.clear()
      fanLayouts.clear()
      return
    }
    rebuildIndex()
    renderAppsList(searchInput.value)
  }
  unsubscribeBundles = store.subscribe('bundles', handleStoreChange)
  // Icons and publishers land after the bundle index (boot paints from the
  // IndexedDB cache first), and the Popular row needs both.
  for (const key of ['iconCache', 'developerCache']) {
    unsubCacheKeys.push(store.subscribe(key, handleStoreChange))
  }

  let debounceTimer
  searchInput.addEventListener('input', () => {
    clearTimeout(debounceTimer)
    const hasValue = searchInput.value.length > 0
    clearBtn.style.display = hasValue ? '' : 'none'
    debounceTimer = setTimeout(() => renderAppsList(searchInput.value), 200)
  })

  clearBtn.addEventListener('click', () => {
    searchInput.value = ''
    clearBtn.style.display = 'none'
    renderAppsList('')
    searchInput.focus()
  })

  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      searchInput.value = ''
      clearBtn.style.display = 'none'
      renderAppsList('')
    }
  })
}
