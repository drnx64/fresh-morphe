/**
 * Date formatting and time utilities.
 */

export function ordinalSuffix(n) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return s[(v - 20) % 10] || s[v] || s[0]
}

export function padNum(n) {
  return n < 10 ? '0' + n : '' + n
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export function formatFriendlyDate(dateStr) {
  if (!dateStr) return '-'
  const parts = dateStr.split('-')
  if (parts.length === 3) {
    const monthIndex = parseInt(parts[1], 10) - 1
    const day = parseInt(parts[2], 10)
    if (monthIndex >= 0 && monthIndex < 12) {
      return `${MONTHS[monthIndex]} ${day}, ${parts[0]}`
    }
  }
  return dateStr
}

/**
 * Split an ISO date into display pieces so the day can be styled on its own.
 * @param {string} dateStr - ISO date (YYYY-MM-DD)
 * @returns {{day: string, month: string, year: string}}
 */
export function dateParts(dateStr) {
  const [year, month, day] = String(dateStr || '').split('-')
  const monthIndex = parseInt(month, 10) - 1
  return {
    day: String(parseInt(day, 10) || ''),
    month: MONTHS[monthIndex] || '',
    year: year || '',
  }
}

/**
 * Normalize a version string to exactly one leading "v".
 * Handles raw data that sometimes includes "v" already (e.g. "v1.24.2" vs "1.24.2").
 * @param {string} v
 * @returns {string} '' if empty, otherwise 'v...' form
 */
export function formatVersion(v) {
  if (!v) return ''
  const s = String(v).trim()
  if (!s) return ''
  return 'v' + s.replace(/^v+/i, '')
}

/**
 * Reduce noisy CI-style version strings to the version itself.
 * "privacy-tiktok-multi-v0.78.0-20261023-195538" -> "v0.78.0"
 * "1.13.0-dev.1" -> "v1.13.0-dev.1" (alpha suffixes kept)
 * @param {string} v
 * @returns {string}
 */
export function shortVersion(v) {
  if (!v) return ''
  const s = String(v).trim()
  if (!s) return ''
  const match = s.match(/(\d+\.\d+\.\d+(?:-[a-z][\w.]*)?)/i)
  return match ? 'v' + match[1] : formatVersion(s)
}

const BADGE_LABELS = {
  UPDATED: 'UPDATE',
  'UPDATED APP': 'PATCHED',
}

/**
 * Short, human badge label for a pipeline badge_type.
 * One word per badge; keeps the raw value for CSS classes.
 * @param {string} type
 * @returns {string}
 */
export function badgeLabel(type) {
  if (!type) return ''
  return BADGE_LABELS[type] || type
}

/**
 * Stable hue per calendar day, spread by the golden angle. Uses the day of the
 * year (not the day of the month) so the same number in two months never
 * renders the same color.
 * @param {string} dateStr - ISO date (YYYY-MM-DD)
 * @returns {number} 0-359
 */
export function dayHue(dateStr) {
  const [year, month, day] = String(dateStr || '').split('-').map(Number)
  if (!year || !month || !day) return 28
  const dayOfYear = Math.round(
    (Date.UTC(year, month - 1, day) - Date.UTC(year, 0, 0)) / 86400000,
  )
  return Math.round((dayOfYear * 137.508) % 360)
}

export function formatTime(isoStr) {
  if (!isoStr) return '-'
  try {
    const d = new Date(isoStr)
    const date = new Intl.DateTimeFormat('en-US', {
      month: 'long', day: 'numeric', year: 'numeric',
    }).format(d)
    const time = new Intl.DateTimeFormat('en-US', {
      hour: 'numeric', minute: '2-digit', hour12: true,
    }).format(d)
    return `${date} at ${time}`
  } catch {
    return isoStr
  }
}

export function getTimeAgo(isoStr) {
  if (!isoStr) return '-'
  try {
    const then = new Date(isoStr)
    const now = new Date()
    const diffMs = now.getTime() - then.getTime()
    if (diffMs < 0) return 'just now'
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return 'just now'
    if (diffMin < 60) return `${diffMin}m ago`
    const diffHrs = Math.floor(diffMin / 60)
    const remainMin = diffMin % 60
    if (diffHrs < 24) return `${diffHrs}h ${remainMin}m ago`
    const diffDays = Math.floor(diffHrs / 24)
    return `${diffDays}d ago`
  } catch {
    return '-'
  }
}
