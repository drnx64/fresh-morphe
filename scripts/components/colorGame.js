/**
 * Daily color game — guess tomorrow's date color.
 *
 * The site tints each day with a deterministic hue (day of year, golden
 * angle), so the game is fair and fully local: pick a color today, the card
 * locks it, and it is scored the next day. State lives in localStorage;
 * nothing leaves the browser.
 */
import { el } from '../ui.js'
import { dateParts, dayHue } from '../utils/format.js'
import {
  addDays, closeness, dayHex, hexToRgb, hueDistance, hueFromHex, hueHex,
  localISODate,
} from '../utils/color.js'

const STORAGE_KEY = 'morphe_daily_color'
const WIN_CLOSENESS = 90

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

function schemeIsLight() {
  return window.matchMedia('(prefers-color-scheme: light)').matches
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

export function renderColorGame() {
  const game = el('section', { class: 'color-game', 'aria-labelledby': 'color-game-title' })
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

  function buildLocked(pending, light) {
    const hex = guessHex(pendingGuess(pending), light)
    return el('div', { class: 'color-game-body' }, [
      el('div', { class: 'color-game-locked' }, [
        el('span', { class: 'color-game-swatch color-game-swatch--lg', style: { background: hex } }),
        el('span', { class: 'color-game-locked-text' }, [
          el('span', { class: 'color-game-note' }, [`Locked for ${prettyDate(pending.date)}`]),
          el('span', { class: 'color-game-hex color-game-hex--big' }, [hex]),
        ]),
      ]),
      el('span', { class: 'color-game-note' }, ['Come back tomorrow to see how close you were.']),
    ])
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
      paint()
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

  function paint() {
    const today = localISODate()
    renderedDate = today
    const light = schemeIsLight()
    let state = readState()
    const settled = settleState(state, today)
    if (settled.changed) writeState(settled.state)
    state = settled.state

    game.replaceChildren()
    game.appendChild(buildHead(state, today, light))

    if (state.pending) {
      game.appendChild(buildLocked(state.pending, light))
      return
    }
    game.appendChild(buildGuess(addDays(today, 1), light))
    if (state.last) game.appendChild(buildResult(state.last, light))
  }

  // Repaint when a tab left open overnight becomes visible, or when the OS
  // switches between light and dark (the fixed S/L changes the hexes).
  const onVisibility = () => {
    if (!game.isConnected) {
      document.removeEventListener('visibilitychange', onVisibility)
      return
    }
    if (document.visibilityState === 'visible' && localISODate() !== renderedDate) paint()
  }
  const schemeQuery = window.matchMedia('(prefers-color-scheme: light)')
  const onScheme = () => {
    if (!game.isConnected) {
      schemeQuery.removeEventListener?.('change', onScheme)
      return
    }
    paint()
  }
  document.addEventListener('visibilitychange', onVisibility)
  schemeQuery.addEventListener?.('change', onScheme)

  paint()
  return game
}
