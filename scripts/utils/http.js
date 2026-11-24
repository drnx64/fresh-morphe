/**
 * fetch helpers shared by boot and the lazy bundle loader.
 *
 * Every request gets a hard timeout so one hung connection can never stall
 * boot or a modal, and data JSON can opt into revalidation (`no-cache`)
 * instead of GitHub Pages' `max-age=600`.
 */

import { devLog } from './devLog.js'

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
 * @param {{ timeoutMs?: number, cache?: RequestCache, headers?: Object,
 *   signal?: AbortSignal }} [opts]
 *   `signal` lets a caller cancel a request it no longer needs (the API/static
 *   race aborts the loser); an external abort always rejects — it is not a
 *   failure, so it never returns `fallback`, and its AbortError reaches the
 *   caller so the race can tell "cancelled" apart from "the API is down".
 *   `cache: 'no-cache'` forces a conditional revalidation (ETag  304) so the
 *   always-fresh site data stays fresh without `?_t=` cache busting.
 * @returns {Promise<*>}
 */
export async function fetchJson(url, fallback, { timeoutMs = DEFAULT_TIMEOUT_MS, cache, headers, signal } = {}) {
  const controller = new AbortController()
  const started = performance.now()
  const onExternalAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) onExternalAbort()
    else signal.addEventListener('abort', onExternalAbort, { once: true })
  }
  devLog('fetch start', url, { timeoutMs, cache: cache || 'default' })
  try {
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try {
      const resp = await fetch(url, {
        ...(cache ? { cache } : {}),
        ...(headers ? { headers } : {}),
        signal: controller.signal,
      })
      if (!resp.ok) throw new Error(`${url} returned ${resp.status}`)
      const data = await resp.json()
      devLog('fetch ok', url, resp.status, `${Math.round(performance.now() - started)}ms`)
      return data
    } finally {
      clearTimeout(timer)
    }
  } catch (err) {
    if (signal?.aborted) {
      devLog('fetch aborted', url, `${Math.round(performance.now() - started)}ms`)
      throw signal.reason || new DOMException('Aborted', 'AbortError')
    }
    if (controller.signal.aborted) {
      devLog('fetch timeout', url, `${Math.round(performance.now() - started)}ms`)
      throw new Error(`Request timed out: ${url}`)
    }
    if (fallback !== undefined) {
      devLog('fetch fallback', url, err.message, `${Math.round(performance.now() - started)}ms`)
      return fallback
    }
    devLog('fetch failed', url, err.message, `${Math.round(performance.now() - started)}ms`)
    throw err
  } finally {
    signal?.removeEventListener('abort', onExternalAbort)
  }
}
