/**
 * Daily color game — guess tomorrow's date color.
 *
 * The site tints each day with a deterministic hue (day of year, golden
 * angle), so the game is fair and fully local: pick a color today, the card
 * locks it, and it is scored the next day. State lives in localStorage;
 * nothing leaves the browser.
 *
 * Once locked, the card flies into the header and collapses to a compact chip
 * (today's color, the locked pick, and the streak flame).
 */
import { el } from '../ui.js'
import { openModal } from './modal.js'
import { dateParts, dayHue } from '../utils/format.js'
import {
  addDays, closeness, dayHex, hexToRgb, hueDistance, hueFromHex, hueHex,
  localISODate,
} from '../utils/color.js'

const STORAGE_KEY = 'morphe_daily_color'
const CHIP_SLOT_ID = 'color-game-slot'
const WIN_CLOSENESS = 90

// Filled flame (Material "local fire department").
const FIRE_PATH = 'M13.5.67s.74 2.65.74 4.8c0 2.06-1.35 3.73-3.41 3.73-2.07 0-3.63-1.67-3.63-3.73l.03-.36C5.21 7.51 4 10.62 4 14c0 4.42 3.58 8 8 8s8-3.58 8-8C20 8.61 17.41 3.8 13.5.67zM11.71 19c-1.78 0-3.22-1.4-3.22-3.14 0-1.62 1.05-2.76 2.81-3.12 1.77-.36 3.6-1.21 4.62-2.58.39 1.29.59 2.65.59 4.04 0 2.65-2.15 4.8-4.8 4.8z'

function readState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function writeState(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // Storage can be unavailable (private mode); the game still works per visit.
  }
}

/** The site uses an explicit `data-theme`; the game tint must match it. */
function schemeIsLight() {
  const theme = document.documentElement.getAttribute('data-theme')
  if (theme) return theme !== 'dark'
  return window.matchMedia('(prefers-color-scheme: light)').matches
}

function prefersReducedMotion() {
  return document.documentElement.classList.contains('reduced-motion')
    || window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function prettyDate(isoStr) {
  const { day, month } = dateParts(isoStr)
  return month && day ? `${month} ${day}` : isoStr
}

/** The hue a stored guess represents; null when it has no usable hue. */
function guessHue(guess) {
  if (typeof guess === 'number') return guess
  if (!hexToRgb(guess)) return null
  const { hue, saturation } = hueFromHex(guess)
  return saturation < 8 ? null : hue
}

/** Swatch/readout hex for a stored guess (hex string or legacy hue number). */
function guessHex(guess, light) {
  if (typeof guess === 'string' && hexToRgb(guess)) return guess
  return hueHex(Number(guess) || 0, light)
}

/** The stored guess for a pending entry (new `guess` or legacy `hex`). */
function pendingGuess(pending) {
  return pending.guess ?? pending.hex
}

/** Flame intensity tiers — hotter flame every few streak days. */
function fireTier(streak) {
  if (streak >= 14) return 'inferno'
  if (streak >= 7) return 'blaze'
  if (streak >= 3) return 'flame'
  return 'ember'
}

function fireSvg(streak) {
  return `<svg class="color-game-fire color-game-fire--${fireTier(streak)}" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="${FIRE_PATH}"/></svg>`
}

/**
 * Score a pending guess once its target date has arrived.
 * @returns {{state: Object, changed: boolean}}
 */
function settleState(state, today) {
  const pending = state.pending
  if (!pending || !pending.date || pending.date > today) {
    return { state, changed: false }
  }
  const guess = pendingGuess(pending)
  const hue = guessHue(guess)
  const diff = hue == null ? 180 : hueDistance(hue, dayHue(pending.date))
  const close = closeness(diff)
  const onTime = pending.date === today
  const win = close >= WIN_CLOSENESS
  const streak = win && onTime ? (state.streak || 0) + 1 : 0
  return {
    changed: true,
    state: {
      ...state,
      pending: null,
      last: { date: pending.date, guess, diff, closeness: close, win, onTime },
      streak,
      best: Math.max(state.best || 0, streak),
    },
  }
}

/** Yesterday's outcome: your pick vs the real tint, plus the score line. */
function buildResult(last, light) {
  const pickedHex = guessHex(last.guess, light)
  const actualHex = dayHex(last.date, light)
  const won = last.win && last.onTime
  let message = `${last.closeness}% close · ${Math.round(last.diff)}° off`
  if (won) message = `Correct · ${message}`
  else if (last.win && !last.onTime) message = `Right hue, but you missed the day · ${message}`

  const pair = el('div', { class: 'color-game-pair' }, [
    el('span', { class: 'color-game-pair-item' }, [
      el('span', { class: 'color-game-note' }, ['Your pick']),
      el('span', { class: 'color-game-swatch', style: { background: pickedHex } }),
      el('span', { class: 'color-game-hex' }, [pickedHex]),
    ]),
    el('span', { class: 'color-game-pair-item' }, [
      el('span', { class: 'color-game-note' }, [prettyDate(last.date)]),
      el('span', { class: 'color-game-swatch', style: { background: actualHex } }),
      el('span', { class: 'color-game-hex' }, [actualHex]),
    ]),
  ])

  return el('div', { class: 'color-game-body color-game-result' }, [
    pair,
    el('span', {
      class: `color-game-score ${won ? 'color-game-score--win' : 'color-game-score--miss'}`,
      role: 'status',
      'aria-live': 'polite',
    }, [message]),
  ])
}

/** Detail popover for the locked chip. */
function openGameModal() {
  const today = localISODate()
  let state = readState()
  const settled = settleState(state, today)
  if (settled.changed) writeState(settled.state)
  state = settled.state
  if (!state.pending) {
    syncColorGameChip()
    return
  }

  const light = schemeIsLight()
  const pending = state.pending
  const todayHex = dayHex(today, light)
  const pickedHex = guessHex(pendingGuess(pending), light)
  const streak = state.streak || 0

  const content = el('div', { class: 'color-game-detail' }, [
    el('div', { class: 'color-game-detail-row' }, [
      el('span', { class: 'color-game-swatch color-game-swatch--lg', style: { background: todayHex } }),
      el('span', { class: 'color-game-detail-text' }, [
        el('span', { class: 'color-game-detail-label' }, [`Today · ${prettyDate(today)}`]),
        el('span', { class: 'color-game-hex color-game-hex--big' }, [todayHex]),
      ]),
    ]),
    el('div', { class: 'color-game-detail-row' }, [
      el('span', { class: 'color-game-swatch color-game-swatch--lg', style: { background: pickedHex } }),
      el('span', { class: 'color-game-detail-text' }, [
        el('span', { class: 'color-game-detail-label' }, [`Your pick · locked for ${prettyDate(pending.date)}`]),
        el('span', { class: 'color-game-hex color-game-hex--big' }, [pickedHex]),
      ]),
    ]),
    el('div', {
      class: 'color-game-detail-streak',
      dangerouslySetInnerHTML: `${fireSvg(streak)}<span class="color-game-detail-streak-num">${streak}</span><span class="color-game-note">day streak${state.best ? ` · best ${state.best}` : ''}</span>`,
    }),
  ])

  if (state.last) {
    content.appendChild(el('div', { class: 'color-game-detail-sep' }))
    content.appendChild(el('span', { class: 'color-game-detail-label' }, [`Last result · ${prettyDate(state.last.date)}`]))
    content.appendChild(buildResult(state.last, light))
  }

  content.appendChild(el('p', { class: 'color-game-note color-game-detail-foot' }, ['Come back tomorrow to see how close you were.']))

  openModal({
    title: 'Daily color',
    content,
    className: 'color-game-modal',
    maxWidth: 400,
  })
}

/** Compact header chip: today's tint, the locked pick, and the streak flame. */
function buildChip(state, today, light) {
  const pending = state.pending
  const pickedHex = guessHex(pendingGuess(pending), light)
  const todayHex = dayHex(today, light)
  const streak = state.streak || 0
  const date = prettyDate(pending.date)
  return el('button', {
    type: 'button',
    class: 'color-game-chip',
    'aria-haspopup': 'dialog',
    title: `Today ${todayHex} · locked ${pickedHex} for ${date} · streak ${streak} — click for details`,
    'aria-label': `Daily color. Today ${todayHex}, your pick ${pickedHex} for ${date}, streak ${streak}. Open details.`,
    onclick: openGameModal,
  }, [
    el('span', { class: 'color-game-chip-colors' }, [
      el('span', { class: 'color-game-swatch', style: { background: todayHex } }),
      el('span', { class: 'color-game-swatch', style: { background: pickedHex } }),
    ]),
    el('span', {
      class: 'color-game-chip-count',
      dangerouslySetInnerHTML: `${fireSvg(streak)}<span>${streak}</span>`,
    }),
  ])
}

let chipSyncBound = false

/** Keep the header chip current across pages, midnight rollover, and themes. */
export function syncColorGameChip() {
  if (!chipSyncBound) {
    chipSyncBound = true
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') syncColorGameChip()
    })
    window.matchMedia('(prefers-color-scheme: light)')
      .addEventListener?.('change', syncColorGameChip)
    window.addEventListener('theme-change', syncColorGameChip)
  }
  const slot = document.getElementById(CHIP_SLOT_ID)
  if (!slot) return
  let state = readState()
  const today = localISODate()
  const settled = settleState(state, today)
  if (settled.changed) writeState(settled.state)
  state = settled.state
  if (state.pending) slot.replaceChildren(buildChip(state, today, schemeIsLight()))
  else slot.replaceChildren()
}

export function renderColorGame() {
  const host = el('div', { class: 'color-game-host' })
  let renderedDate = ''

  function buildHead(state, today, light) {
    const todayHex = dayHex(today, light)
    const todayWrap = el('div', { class: 'color-game-today' }, [
      el('h3', { class: 'color-game-label', id: 'color-game-title' }, ["Today's color"]),
      el('span', {
        class: 'color-game-swatch',
        style: { background: todayHex },
        title: todayHex,
      }),
      el('span', { class: 'color-game-hex' }, [todayHex]),
    ])
    const streak = el('span', { class: 'color-game-streak' }, [
      `Streak ${state.streak || 0} · Best ${state.best || 0}`,
    ])
    return el('div', { class: 'color-game-head' }, [todayWrap, streak])
  }

  function buildGuess(tomorrow, light) {
    const picker = el('input', {
      type: 'color',
      class: 'color-game-picker',
      id: 'color-game-pick',
      value: dayHex(localISODate(), light),
      'aria-label': "Tomorrow's color",
    })
    const hexEl = el('span', { class: 'color-game-hex color-game-hex--big' })

    const update = () => {
      hexEl.textContent = picker.value
    }
    picker.addEventListener('input', update)
    update()

    const submit = el('button', { class: 'btn btn--primary btn--sm', type: 'button' }, ['Lock in'])
    submit.addEventListener('click', () => {
      const state = readState()
      state.pending = { date: tomorrow, guess: picker.value }
      writeState(state)
      paint({ animate: true })
    })

    return el('div', { class: 'color-game-body' }, [
      el('label', { class: 'color-game-guess-label', for: 'color-game-pick' }, [`Pick what ${prettyDate(tomorrow)} looks like`]),
      el('div', { class: 'color-game-guess-row' }, [
        picker,
        hexEl,
        el('span', { class: 'color-game-note' }, [`${WIN_CLOSENESS}%+ close keeps your streak.`]),
      ]),
      el('div', { class: 'color-game-actions' }, [submit]),
    ])
  }

  function buildCard(state, today, light) {
    const card = el('section', { class: 'color-game', 'aria-labelledby': 'color-game-title' })
    card.appendChild(buildHead(state, today, light))
    card.appendChild(buildGuess(addDays(today, 1), light))
    if (state.last) card.appendChild(buildResult(state.last, light))
    return card
  }

  /** FLIP: lift the locked card into the header chip while it collapses. */
  function flyToChip(card, fromRect, chip) {
    const toRect = chip.getBoundingClientRect()
    if (!fromRect.width || !fromRect.height || !toRect.width) return
    const reduced = prefersReducedMotion()
    const duration = reduced ? 200 : 560

    const clone = card.cloneNode(true)
    clone.classList.add('color-game--flying')
    Object.assign(clone.style, {
      position: 'fixed',
      top: `${fromRect.top}px`,
      left: `${fromRect.left}px`,
      width: `${fromRect.width}px`,
      height: `${fromRect.height}px`,
      margin: '0',
      zIndex: '200',
      pointerEvents: 'none',
      transformOrigin: 'top left',
      contain: 'layout paint',
    })
    document.body.appendChild(clone)

    const dx = toRect.left - fromRect.left
    const dy = toRect.top - fromRect.top
    const sx = toRect.width / fromRect.width
    const sy = toRect.height / fromRect.height
    const midX = dx * 0.62
    const midY = dy * 0.62 - (reduced ? 0 : 18)

    if (typeof clone.animate === 'function') {
      const flight = clone.animate([
        { transform: 'translate(0, 0) scale(1, 1)', opacity: 1, borderRadius: '14px' },
        {
          transform: `translate(${midX}px, ${midY}px) scale(${1 - (1 - sx) * 0.45}, ${1 - (1 - sy) * 0.45})`,
          opacity: 0.95,
          borderRadius: '12px',
          offset: 0.55,
        },
        {
          transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`,
          opacity: 0,
          borderRadius: '999px',
        },
      ], { duration, easing: reduced ? 'ease-out' : 'cubic-bezier(.2, .75, .25, 1)' })
      flight.onfinish = () => clone.remove()
    } else {
      clone.remove()
    }

    // Arrive as a compact pill: a quick shrink into place (always runs, so the
    // hand-off is visible even when the long flight is reduced).
    chip.animate([
      { opacity: 0, transform: 'scale(1.4)' },
      { opacity: 1, transform: 'scale(1)' },
    ], {
      duration: reduced ? 140 : 300,
      delay: reduced ? 0 : Math.round(duration * 0.55),
      easing: 'cubic-bezier(.2, .8, .2, 1)',
      fill: 'backwards',
    })
  }

  function paint({ animate = false } = {}) {
    const today = localISODate()
    renderedDate = today
    const light = schemeIsLight()
    let state = readState()
    const settled = settleState(state, today)
    if (settled.changed) writeState(settled.state)
    state = settled.state

    const slot = document.getElementById(CHIP_SLOT_ID)
    const oldCard = animate ? host.querySelector('.color-game') : null
    const oldRect = oldCard ? oldCard.getBoundingClientRect() : null

    if (state.pending) {
      const chip = buildChip(state, today, light)
      if (slot) slot.replaceChildren(chip)

      // Collapse the inline slot so the page settles at the same pace as the
      // card flying into the header, instead of jumping up in one frame.
      if (oldRect) {
        host.style.height = `${host.offsetHeight}px`
        host.style.overflow = 'hidden'
        host.replaceChildren()
        requestAnimationFrame(() => {
          host.style.transition = 'height 0.3s var(--ease)'
          host.style.height = '0px'
        })
        setTimeout(() => {
          host.style.transition = ''
          host.style.height = ''
          host.style.overflow = ''
        }, 360)
      } else {
        host.replaceChildren()
      }

      if (oldRect && slot && chip.isConnected) flyToChip(oldCard, oldRect, chip)
      return
    }

    host.style.transition = ''
    host.style.height = ''
    host.style.overflow = ''
    if (slot) slot.replaceChildren()
    host.replaceChildren(buildCard(state, today, light))
  }

  // Repaint when a tab left open overnight becomes visible, or when the OS
  // switches between light and dark (the fixed S/L changes the hexes).
  const onVisibility = () => {
    if (!host.isConnected) {
      document.removeEventListener('visibilitychange', onVisibility)
      return
    }
    if (document.visibilityState === 'visible' && localISODate() !== renderedDate) paint()
  }
  const schemeQuery = window.matchMedia('(prefers-color-scheme: light)')
  const onScheme = () => {
    if (!host.isConnected) {
      schemeQuery.removeEventListener?.('change', onScheme)
      window.removeEventListener('theme-change', onScheme)
      return
    }
    paint()
  }
  document.addEventListener('visibilitychange', onVisibility)
  schemeQuery.addEventListener?.('change', onScheme)
  window.addEventListener('theme-change', onScheme)

  paint()
  return host
}
