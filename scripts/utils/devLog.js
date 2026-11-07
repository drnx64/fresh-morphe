/**
 * Localhost-only development logging.
 *
 * Verbose logs are enabled when the page is served from a loopback host.
 * Production remains limited to the existing warn/error output. Use
 * `?debug=1` / `?debug=0` or localStorage `fresh_debug` to override for a
 * single browser without rebuilding.
 */
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]'])

function readStored() {
  try {
    const value =
      localStorage.getItem('fresh_debug') ??
      localStorage.getItem('morphe_debug')
    if (value !== null) {
      localStorage.setItem('fresh_debug', value)
      localStorage.removeItem('morphe_debug')
    }
    return value
  } catch {
    return null
  }
}

let query = null
let hostname = ''
try {
  query = new URLSearchParams(window.location.search).get('debug')
  hostname = window.location.hostname
} catch {
  /* non-browser import; dev logging stays off */
}

export const IS_LOCAL_DEV = LOCAL_HOSTS.has(hostname)
const stored = readStored()
export const DEV_MODE = query !== null
  ? query === '1'
  : stored !== null
    ? stored === '1'
    : IS_LOCAL_DEV

const STYLE = 'color:#a281ad;font-weight:bold'

export function devLog(...args) {
  if (!DEV_MODE) return
  console.log('%c[dev]', STYLE, ...args)
}

export function devWarn(...args) {
  if (!DEV_MODE) return
  console.warn('%c[dev]', STYLE, ...args)
}

export function devTime(label) {
  if (!DEV_MODE) return () => {}
  const started = performance.now()
  return (extra) => devLog(label, `${Math.round(performance.now() - started)}ms`, extra ?? '')
}
