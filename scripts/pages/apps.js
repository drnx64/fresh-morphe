/**
 * Apps page - what's new + directory of all apps across bundles.
 */

import { el, mount } from '../ui.js'
import * as store from '../store.js'
import { buildAppIndex, resolveAppName, getAppIconUrl, renderAppIcon, suggestFuzzy } from '../utils/misc.js'
import { getPlayStoreUrl } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { groupAppsIntoFamilies, developerLabel } from '../utils/appFamily.js'
import { appRank } from '../utils/popular.js'
import { renderTodayUpdates } from './todayUpdates.js'
import { skeletonGrid } from '../components/skeleton.js'

const RENDER_BATCH = 24
const STACK_FILL_BATCH = 24
// Browse shows a curated head: this many grid cards (a family counts as one).
// Search is uncapped — it is the door to the rest of the registry.
const BROWSE_CARD_LIMIT = 48

// Fan geometry in px, by distance from the focused card. The near step is the
// reference deck's EXPAND_GAP (110px on a 96px card) scaled to our band: cards
// may shingle a little (the focused one always on top), and spacing compresses
// on short bands so distant cards still get room — piecewise spacing keeps the
// whole family inside the band: no scrollbar, no hover-scroll loops, ever.
const FAN_NEAR_STEP = 78
const FAN_FAR_STEP_MIN = 2 // slivers stay hoverable even in a huge family
const FAN_DENSE_STEP = 40 // below this the fringe drops its text (edges only)
const FAN_FADE_PX = 56 // edge strip over which cards dissolve toward nothing
const FAN_FADE_MIN = 0.15 // opacity floor, so a compressed fringe stays visible
const FAN_CLOSE_MS = 210 // fade-out before the open classes drop
const FAN_RESIZE_MS = 120
const HOVER_OPEN_MS = 150 // hover-intent dwell before a pointer opens the fan
const HOVER_CLOSE_MS = 250 // grace after the pointer leaves before it closes
const WHEEL_STEP_PX = 40 // wheel travel that steps the pile one card
const WHEEL_IDLE_MS = 150 // a longer pause starts a fresh wheel gesture

// Desktop-class pointers only: touch and pen never open a fan by hovering.
const canHover = window.matchMedia('(hover: hover) and (pointer: fine)').matches

let unsubscribeBundles = null

// One pile is open at a time: opening a stack shuts the others. Module-level
// because the grid is rebuilt on every refill and a stale closer must not
// outlive the node it closes.
const stackClosers = new Map()

// Per-stack fan relayout, refreshed on viewport resize (the band is
// viewport-height based). Cleared with stackClosers on every refill.
const fanLayouts = new Map()

// Per-stack document-listener teardown (Escape + outside pointer). A refill
// discards the old nodes but must not lose an open pile's identity — the
// listeners are detached explicitly rather than through setExpanded(false),
// which would also clear openStacks.
const stackDismissers = new Map()

let fanResizeTimer = null
window.addEventListener('resize', () => {
  if (fanResizeTimer) clearTimeout(fanResizeTimer)
  fanResizeTimer = setTimeout(() => {
    fanResizeTimer = null
    for (const layout of fanLayouts.values()) layout()
  }, FAN_RESIZE_MS)
})

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

  // "Showing 48 of N cards" — the browse list is a curated doorway, not the
  // whole registry; search is the door to everything else. Hidden while a
  // query is active (search results are never capped).
  const dirNote = el('p', { class: 'apps-dir-note', hidden: true })

  const grid = el('div', { class: 'apps-grid', id: 'apps-grid' })

  page.appendChild(header)
  page.appendChild(searchRow)
  page.appendChild(dirNote)
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
   * App family: a deck of its own app cards.
   *
   * Collapsed, every card occupies the same spot in the grid cell: the front
   * one is the family's first app, each card behind it steps up, shrinks and
   * tilts slightly, so only its top edge shows — the card-stack silhouette,
   * made of real cards rather than a decorated icon, its edges rising into
   * the row gap above the cell (every cell is one card tall).
   *
   * A tap or click — or, when the pointer can hover, a HOVER_OPEN_MS dwell —
   * fans the deck out in place: an absolutely positioned band anchored on the
   * card's own grid cell, painting over the neighbours without reflowing the
   * grid — the page behind stays sharp and scrollable.
   * The page keeps scrolling under the fan: it rides along with the cell and
   * collapses once the cell leaves the viewport. The focused card holds the
   * card's position; cards before it fan upward, cards after it downward.
   * Nothing slides on its own — the focus moves only by hand: the wheel over
   * the fan steps it one card at a time (chaining to the page at either end),
   * a click on a card pulls it to the middle, and the arrow keys walk it. A
   * pointer that leaves the stack closes it after HOVER_CLOSE_MS unless
   * keyboard focus is still inside; Escape, a click outside, and a re-click
   * on the band close it immediately.
   * Distant cards compress into slivers, and everything past the band's edge
   * dissolves over FAN_FADE_PX, so even a 50-app publisher reads as a soft
   * stack rather than a clipped list.
   *
   * Only the first four cards are built up front (a 50-app publisher is not
   * free to paint); the rest are appended in batches once the pile opens.
   */
  function buildAppStack(family, gen) {
    const { id, label, apps } = family
    const count = apps.length
    const pileId = `app-stack-${id.replace(/[^a-z0-9]+/gi, '-')}`
    const stack = el('div', { class: 'app-stack' })

    // Only visible once open: names the family and collapses the pile again.
    const caption = el('button', {
      class: 'app-stack-caption',
      type: 'button',
      'aria-expanded': 'false',
      'aria-controls': pileId,
      'aria-label': `Collapse ${label}, ${count} apps`,
    })
    caption.innerHTML = `
      <span class="app-stack-eyebrow">Publisher</span>
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
      // Hover only highlights (CSS `:hover`); it never moves the cards. The
      // focus steps by hand: the wheel over the fan, a click on a card, or
      // the arrow keys — nothing slides on its own.
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
     * lands on the band edge — the whole family stays inside the band with no
     * scrollbar. The band itself is derived from the viewport room around
     * this cell (never from a possibly-detached pile), so the fan opens at
     * the card's own position without reflowing the grid, and the outer cards
     * dissolve over FAN_FADE_PX at the top and bottom edges. (The grid behind
     * the band stays sharp — nothing blurs it.)
     * Writes `--y`, `--o`, `--s`, `--z`, the overlay's `top` and the pile's
     * `--fan-h` plus the is-focus/is-far classes and the roving tabindex.
     * Returns false while the stack cannot be measured (detached mid-refill)
     * so the caller can retry next frame.
     */
    function layoutFan() {
      if (!isOpen) return false
      const rect = stack.getBoundingClientRect()
      if (!rect.width) return false // detached (rebuilt mid-refill) — retry next frame

      const cardH = parseFloat(getComputedStyle(stack).getPropertyValue('--card-h')) || 90
      const navH = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--nav-height')) || 56
      // The caption is hidden while open, so the band is cards only — and the
      // focused card must land where the front card sits in the cell: its top
      // edge (peeking edges rise into the row gap, so padding-top is 0 now —
      // still read, so any future inset aligns without a code change).
      const padTop = parseFloat(getComputedStyle(stack).paddingTop) || 0
      // Desired band mirrors CSS's --fan-h fallback, then clamps to the room
      // left around this cell: below the sticky header, above the bottom edge.
      // Only the size clamps — the box shifts instead, so a card sitting near
      // a viewport edge still gets a full fan rather than a squashed one.
      const target = Math.min(window.innerHeight * 0.56, window.innerHeight - navH - 100, 540)
      // Mirrors the overlay's max-height exactly, so the box can never be
      // clipped by it at the bottom edge.
      const avail = window.innerHeight - navH - 32
      const half = Math.max(0, Math.min((target - cardH) / 2, (avail - cardH) / 2))
      const boxH = cardH + 2 * half
      const boxTop = Math.min(
        Math.max(rect.top + padTop - half, navH + 4),
        Math.max(navH + 4, window.innerHeight - 4 - boxH),
      )
      // The pile hugs the band; the overlay is positioned so the focused card
      // lands on the cell's front card. Both are absolute — no reflow.
      stack.style.setProperty('--fan-h', `${Math.round(cardH + 2 * half)}px`)
      overlay.style.top = `${Math.round(boxTop - rect.top)}px`

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

        // Edge dissolve: over the last FAN_FADE_PX at either band edge a card
        // fades out and shrinks slightly, so the stack melts instead of being
        // cut off. The focused card never fades; FAN_FADE_MIN keeps a
        // compressed fringe faintly visible so it stays hoverable.
        const distEdge = Math.min(y, 2 * half - y)
        const fade = abs === 0 ? 1 : Math.max(FAN_FADE_MIN, Math.min(1, distEdge / FAN_FADE_PX))
        const baseScale = abs === 0 ? 1.02 : 0.98

        card.style.setProperty('--y', `${y.toFixed(1)}px`)
        card.style.setProperty('--o', String(Math.round(opacity * fade * 100) / 100))
        card.style.setProperty('--s', (baseScale * (0.94 + 0.06 * fade)).toFixed(3))
        card.style.setProperty('--z', String(400 - abs))
        card.classList.toggle('is-focus', abs === 0)
        card.classList.toggle('is-far', abs > 2)
        card.tabIndex = abs === 0 ? 0 : -1
      }
      return true
    }

    /**
     * Re-measure the band over the next frames until layoutFan() gets a real
     * answer. A stack rebuilt during a refill lays out while still detached
     * (zero-width rect), and a single rAF rescue can land in that same window
     * — which is how the fan used to collapse to one card parked at y=0.
     */
    let fanLayoutTries = 0
    function scheduleFanLayout() {
      fanLayoutTries = 0
      const attempt = () => {
        if (!isOpen) return
        if (layoutFan()) return
        if (++fanLayoutTries < 10) requestAnimationFrame(attempt)
      }
      requestAnimationFrame(attempt)
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
    let onOutside = null
    let hoverOpenTimer = null
    let hoverCloseTimer = null
    let fanObserver = null

    function clearCloseTimer() {
      if (closeTimer) {
        clearTimeout(closeTimer)
        closeTimer = null
      }
    }

    // Dismissal lives outside the band: a click that lands outside the stack
    // collapses the fan (there is no scrim to catch it), Escape does the
    // same, and an IntersectionObserver closes it once the cell scrolls out
    // of the viewport — the page pans normally and the fan rides along.
    // `click` rather than `pointerdown`: a scroll gesture never fires one, so
    // panning from anywhere lets the fan ride instead of snapping it shut.
    // Detached explicitly — setExpanded(false) is not the only way a node
    // goes away (a refill discards it while still open).
    function attachDismiss() {
      detachDismiss() // idempotent: never stack two sets of listeners
      onOutside = (e) => {
        if (stack.contains(e.target)) {
          // Inside the stack: a card's click is the card's own business, but
          // a click on the cell's own box — the strip no card covers — is the
          // re-click that collapses the fan.
          if (e.target !== stack) return
          setExpanded(false, { via: 're-click' })
          return
        }
        setExpanded(false, { via: 'outside' })
      }
      onEscape = (e) => {
        if (e.key === 'Escape') setExpanded(false, { via: 'escape' })
      }
      document.addEventListener('click', onOutside, true)
      document.addEventListener('keydown', onEscape)
      if ('IntersectionObserver' in window) {
        fanObserver = new IntersectionObserver((entries) => {
          for (const entry of entries) {
            if (!entry.isIntersecting) setExpanded(false, { via: 'scrolled-away' })
          }
        }, { threshold: 0 })
        fanObserver.observe(stack)
      }
    }

    function detachDismiss() {
      if (onOutside) {
        document.removeEventListener('click', onOutside, true)
        onOutside = null
      }
      if (onEscape) {
        document.removeEventListener('keydown', onEscape)
        onEscape = null
      }
      if (fanObserver) {
        fanObserver.disconnect()
        fanObserver = null
      }
      if (hoverOpenTimer) {
        clearTimeout(hoverOpenTimer)
        hoverOpenTimer = null
      }
      if (hoverCloseTimer) {
        clearTimeout(hoverCloseTimer)
        hoverCloseTimer = null
      }
    }

    function setExpanded(open, { via = 'api', force = false } = {}) {
      if (!force && open === isOpen) return

      if (open) {
        clearCloseTimer()
        if (hoverOpenTimer) {
          clearTimeout(hoverOpenTimer)
          hoverOpenTimer = null
        }
        if (hoverCloseTimer) {
          clearTimeout(hoverCloseTimer)
          hoverCloseTimer = null
        }
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
        // No scrim element at all: the fan paints only its own cards over
        // the grid. Escape and a click outside the stack collapse it (the
        // caption and a re-click do too).
        attachDismiss()
        // Measure the band and place the cards now, then keep retrying: a
        // stack rebuilt during a refill lays out while still detached.
        layoutFan()
        scheduleFanLayout()
        fillRemaining()
        // The caption is hidden while open, so a deliberate open hands focus
        // to the centered card instead (hover opens leave focus alone — a
        // pointer must never steal it from the keyboard).
        if (via === 'click') {
          const card = pile.children[fanFocus]
          if (card) {
            card.tabIndex = 0
            card.focus({ preventScroll: true })
          }
        }
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
        detachDismiss()
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

    expander.addEventListener('click', () => {
      // Already open (a hybrid touch race can land the click after a hover
      // opened it): pull focus to the centered card instead of no-op'ing.
      if (isOpen) {
        const card = pile.children[fanFocus]
        if (card) card.focus({ preventScroll: true })
        return
      }
      setExpanded(true, { via: 'click' })
    })
    caption.addEventListener('click', () => setExpanded(false, { via: 'click' }))

    // Desktop hover: a short dwell opens the fan, a short grace closes it
    // after the pointer leaves — but never while keyboard focus is inside
    // (focus leaving re-arms the same close below). Touch and pen are out:
    // canHover is false there, so they only ever open by tap.
    if (canHover) {
      stack.addEventListener('mouseenter', () => {
        if (hoverCloseTimer) {
          clearTimeout(hoverCloseTimer)
          hoverCloseTimer = null
        }
        if (isOpen || hoverOpenTimer) return
        hoverOpenTimer = setTimeout(() => {
          hoverOpenTimer = null
          if (stack.isConnected && stack.matches(':hover')) {
            setExpanded(true, { via: 'hover' })
          }
        }, HOVER_OPEN_MS)
      })
      stack.addEventListener('mouseleave', () => {
        if (hoverOpenTimer) {
          clearTimeout(hoverOpenTimer)
          hoverOpenTimer = null
        }
        if (!isOpen) return
        if (hoverCloseTimer) clearTimeout(hoverCloseTimer)
        hoverCloseTimer = setTimeout(() => {
          hoverCloseTimer = null
          if (!stack.isConnected || !isOpen) return
          if (stack.matches(':hover') || stack.contains(document.activeElement)) return
          setExpanded(false, { via: 'hover-leave' })
        }, HOVER_CLOSE_MS)
      })
    }

    // Focus moved out of the stack (Tab away): re-arm the close, or a
    // keyboard-opened fan would stay open forever with no pointer near it.
    stack.addEventListener('focusout', (e) => {
      if (!isOpen || !stack.isConnected) return
      if (stack.contains(e.relatedTarget)) return // moved within the fan
      if (stack.matches(':hover')) return // pointer still here
      if (hoverCloseTimer) clearTimeout(hoverCloseTimer)
      hoverCloseTimer = setTimeout(() => {
        hoverCloseTimer = null
        if (!stack.isConnected || !isOpen) return
        if (stack.matches(':hover') || stack.contains(document.activeElement)) return
        setExpanded(false, { via: 'focus-left' })
      }, HOVER_CLOSE_MS)
    })

    // Caption and pile ride in one box: collapsed it sits in flow, open it is
    // anchored over the grid on the card's own cell
    const overlay = el('div', { class: 'app-stack-overlay' })
    const captionWrap = el('div', { class: 'app-stack-caption-wrap' }, [caption])
    overlay.appendChild(captionWrap)
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

    // Manual pile scroll: the wheel steps the focus one card at a time —
    // nothing slides on its own. At either end of the pile the wheel chains
    // to the page, so an open fan never traps a scroll. Touch never fires
    // wheel: the page pans normally there and the fan rides along with its
    // cell (the observer collapses it when the cell leaves the viewport).
    // Attached to the overlay: only a card (pointer-events: auto) can be its
    // target, so wheeling over empty band area still scrolls the page below.
    let wheelAcc = 0
    let wheelAt = 0
    overlay.addEventListener(
      'wheel',
      (e) => {
        if (!isOpen) return
        const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? window.innerHeight : 1
        const dy = e.deltaY * unit
        if (!dy) return
        const forward = dy > 0
        // At the pile's end (or its start, scrolling back) let the scroll
        // pass through to the page — scroll chaining, not a scroll trap.
        if (forward ? fanFocus >= built - 1 : fanFocus <= 0) {
          wheelAcc = 0
          return
        }
        e.preventDefault()
        const now = performance.now()
        if (now - wheelAt > WHEEL_IDLE_MS || (wheelAcc && Math.sign(wheelAcc) !== Math.sign(dy))) {
          wheelAcc = 0 // a fresh gesture, or the direction flipped
        }
        wheelAt = now
        wheelAcc += dy
        if (Math.abs(wheelAcc) < WHEEL_STEP_PX) return
        wheelAcc = 0
        // The roving focus follows only when the keyboard already had it — a
        // wheel is a pointer gesture and must not steal focus from elsewhere.
        setFanFocus(fanFocus + (forward ? 1 : -1), {
          moveDomFocus: pile.contains(document.activeElement),
        })
      },
      { passive: false },
    )

    stack.appendChild(overlay)
    stack.appendChild(expander)
    // Force: a fresh node is "collapsed" by class already, but its caption and
    // pile still need the collapsed attributes (inert, aria-hidden, tabIndex).
    setExpanded(openStacks.has(id), { force: true })
    stackDismissers.set(id, detachDismiss)
    return stack
  }

  function renderAppsList(query = '') {
    const gen = ++listGeneration
    grid.replaceChildren()
    // The cap note belongs to the uncapped browse view only — every early
    // return below (booting, no results) clears it first.
    dirNote.hidden = true
    // The old stacks are detached: their document listeners, closers and fan
    // layouts go with them. openStacks itself survives, so the piles reopen
    // on the fresh nodes below.
    for (const detach of stackDismissers.values()) detach()
    stackDismissers.clear()
    stackClosers.clear()
    fanLayouts.clear()

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
      // Browse order: the apps people know lead — Morphe Official first, then
      // social apps by worldwide reach, then the household-name tiers — and
      // the A–Z registry follows. Icon-less packages are held back until their
      // icon lands (the iconCache subscription re-runs this), so browsing
      // never shows a wall of letter tiles; search still matches them, since
      // a lookup wants the package, artwork or not. With no icon data at all
      // (still booting, or app_cache failed) the filter stands down — letter
      // tiles beat an empty grid reading as "No apps found."
      const anyIcons = Object.keys(store.get('iconCache') || {}).length > 0
      list = list
        .filter((app) => app.iconUrl || !anyIcons)
        .sort((a, b) => {
          const ra = appRank(a) ?? Number.MAX_VALUE
          const rb = appRank(b) ?? Number.MAX_VALUE
          return ra - rb || a.name.localeCompare(b.name)
        })
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
    // Browse shows a doorway, not the whole registry: families group first, then
    // the card list caps at BROWSE_CARD_LIMIT (a group card counts as one card).
    // Search never caps — it is how the rest of the registry stays reachable.
    const cards = entries.slice(0, BROWSE_CARD_LIMIT)
    if (cards.length < entries.length) {
      dirNote.textContent =
        `Showing ${cards.length} of ${entries.length} cards — search to find any app.`
      dirNote.hidden = false
    }

    let rendered = 0
    function renderBatch() {
      if (gen !== listGeneration) return
      const end = Math.min(rendered + RENDER_BATCH, cards.length)
      for (let i = rendered; i < end; i++) {
        const entry = cards[i]
        grid.appendChild(
          entry.kind === 'family'
            ? buildAppStack(entry, gen)
            : buildAppCard(entry.app, store.get('iconCache') || {})
        )
      }
      rendered = end
      if (rendered < cards.length) requestAnimationFrame(renderBatch)
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
      for (const detach of stackDismissers.values()) detach()
      stackDismissers.clear()
      stackClosers.clear()
      fanLayouts.clear()
      return
    }
    rebuildIndex()
    renderAppsList(searchInput.value)
  }
  unsubscribeBundles = store.subscribe('bundles', handleStoreChange)
  // Icons and publishers land after the bundle index (boot paints from the
  // IndexedDB cache first): the browse filter needs the icons, the family
  // grouping needs the publishers.
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
