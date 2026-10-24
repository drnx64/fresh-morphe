/**
 * Color helpers for the daily color game.
 *
 * The site's date colors are HSL values with a fixed saturation/lightness
 * per color scheme, so a day's color is fully described by its hue.
 */
import { dayHue } from './format.js'

const SATURATION = { dark: 68, light: 62 }
const LIGHTNESS = { dark: 70, light: 34 }

function pad2(n) {
  return String(n).padStart(2, '0')
}

/** Local-midnight ISO date (YYYY-MM-DD), never UTC. */
export function localISODate(date = new Date()) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

/** Local ISO date shifted by whole days. */
export function addDays(dateStr, days) {
  const [y, m, d] = String(dateStr).split('-').map(Number)
  const shifted = new Date(y, (m || 1) - 1, (d || 1) + days)
  return localISODate(shifted)
}

export function hslToRgb(h, s, l) {
  const hue = ((h % 360) + 360) % 360
  const sat = s / 100
  const light = l / 100
  const c = (1 - Math.abs(2 * light - 1)) * sat
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1))
  const m = light - c / 2

  let r = 0
  let g = 0
  let b = 0
  if (hue < 60) [r, g, b] = [c, x, 0]
  else if (hue < 120) [r, g, b] = [x, c, 0]
  else if (hue < 180) [r, g, b] = [0, c, x]
  else if (hue < 240) [r, g, b] = [0, x, c]
  else if (hue < 300) [r, g, b] = [x, 0, c]
  else [r, g, b] = [c, 0, x]

  return [r, g, b].map((v) => Math.round((v + m) * 255))
}

export function hslToHex(h, s, l) {
  return '#' + hslToRgb(h, s, l)
    .map((v) => v.toString(16).padStart(2, '0'))
    .join('')
}

/** Hex of a hue in the current color scheme's fixed S/L. */
export function hueHex(hue, isLight) {
  const mode = isLight ? 'light' : 'dark'
  return hslToHex(hue, SATURATION[mode], LIGHTNESS[mode])
}

/** The color the site will use for an ISO date. */
export function dayHex(dateStr, isLight) {
  return hueHex(dayHue(dateStr), isLight)
}

/** Shortest circular distance between two hues, 0-180. */
export function hueDistance(a, b) {
  const diff = Math.abs(a - b) % 360
  return diff > 180 ? 360 - diff : diff
}

/** 0-100 score: 100 is an exact hue match. */
export function closeness(diff) {
  return Math.round((1 - diff / 180) * 100)
}

/** Parse #RRGGBB into [r, g, b], or null. */
export function hexToRgb(hex) {
  const match = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim())
  if (!match) return null
  const value = parseInt(match[1], 16)
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255]
}

/** Hue and saturation of a hex color: {hue, saturation}. */
export function hueFromHex(hex) {
  const rgb = hexToRgb(hex)
  if (!rgb) return { hue: 0, saturation: 0 }
  const [r, g, b] = rgb.map((v) => v / 255)
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const delta = max - min
  if (delta === 0) return { hue: 0, saturation: 0 }

  let hue
  if (max === r) hue = ((g - b) / delta) % 6
  else if (max === g) hue = (b - r) / delta + 2
  else hue = (r - g) / delta + 4

  return {
    hue: Math.round(((hue * 60) + 360) % 360),
    saturation: Math.round((delta / (1 - Math.abs(max + min - 1))) * 100),
  }
}
