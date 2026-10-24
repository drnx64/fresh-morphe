/**
 * fetch helpers shared by boot and the lazy bundle loader.
 *
 * Every request gets a hard timeout so one hung connection can never stall
 * boot or a modal, and data JSON can opt into revalidation (`no-cache`)
 * instead of GitHub Pages' `max-age=600`.
 */

const DEFAULT_TIMEOUT_MS = 15000

/**
 * Fetch a URL with an AbortController timeout.
 * @param {string} url
 * @param {RequestInit} [init]
 * @param {number} [timeoutMs]
 * @returns {Promise<Response>}
 */
export async function fetchWithTimeout(url, init = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } catch (err) {
    if (controller.signal.aborted) throw new Error(`Request timed out: ${url}`)
    throw err
  } finally {
    clearTimeout(timer)
  }
}

/**
 * Fetch and parse JSON.
 * @param {string} url
 * @param {*} [fallback] returned instead of throwing when the fetch fails
 * @param {{ timeoutMs?: number, cache?: RequestCache }} [opts]
 *   `cache: 'no-cache'` forces a conditional revalidation (ETag → 304) so the
 *   always-fresh site data stays fresh without `?_t=` cache busting.
 * @returns {Promise<*>}
 */
export async function fetchJson(url, fallback, { timeoutMs = DEFAULT_TIMEOUT_MS, cache } = {}) {
  const controller = new AbortController()
  try {
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try {
      const resp = await fetch(url, {
        ...(cache ? { cache } : {}),
        signal: controller.signal,
      })
      if (!resp.ok) throw new Error(`${url} returned ${resp.status}`)
      return await resp.json()
    } finally {
      clearTimeout(timer)
    }
  } catch (err) {
    if (controller.signal.aborted) throw new Error(`Request timed out: ${url}`)
    if (fallback !== undefined) return fallback
    throw err
  }
}
