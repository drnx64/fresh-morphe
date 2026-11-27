/**
 * Runtime data-source configuration.
 *
 * The public site can run against the phone API, the static JSON snapshot, or
 * both (API first, snapshot fallback). Resolution order:
 *   1. ?api=<origin> on the current URL (handy for testing a quick tunnel)
 *   2. localStorage "fresh_api_base" (set it in devtools to pin an origin)
 *   3. public/config.js -> window.FRESH_CONFIG.apiBase
 *   4. static-only
 */
const params = new URLSearchParams(window.location.search)
const stored = (() => {
  try {
    const value =
      localStorage.getItem('fresh_api_base') ||
      localStorage.getItem('morphe_api_base') ||
      ''
    if (value) {
      localStorage.setItem('fresh_api_base', value)
      localStorage.removeItem('morphe_api_base')
    }
    return value
  } catch {
    return ''
  }
})()

function trimSlash(value) {
  return String(value || '').replace(/\/+$/, '')
}

function joinUrl(base, path) {
  return `${trimSlash(base)}/${String(path || '').replace(/^\/+/, '')}`
}

const configured = window.FRESH_CONFIG || window.MORPHE_CONFIG || {}
export const API_BASE = trimSlash(params.get('api') || stored || configured.apiBase || '')
export const API_ENABLED = Boolean(API_BASE)
export const FALLBACK_BASE = trimSlash(configured.fallbackBase || 'data')

function isNgrok(base) {
  try {
    const host = new URL(base).hostname
    return /(^|\.)(ngrok-free\.(dev|app)|ngrok\.(io|app))$/.test(host)
  } catch {
    return false
  }
}

// ngrok's free plan serves an HTML browser warning before the proxy response.
// This header opts API requests out of that interstitial.
export const API_HEADERS = isNgrok(API_BASE)
  ? { 'ngrok-skip-browser-warning': 'true' }
  : {}

export function apiUrl(path) {
  return joinUrl(API_BASE, path)
}

export function fallbackUrl(path) {
  return joinUrl(FALLBACK_BASE, path)
}
