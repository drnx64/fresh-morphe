/**
 * Shared circuit breaker for the optional phone API.
 *
 * Once the boot request fails, stop trying the API for every bundle-detail
 * fetch. The page still works from the static snapshot; a reload retries.
 *
 * The state is also published to listeners so the header's source indicator
 * can say whether the phone database or the static snapshot is answering.
 */
let available = true

/** @type {Set<(available: boolean) => void>} */
const listeners = new Set()

function notify() {
  for (const fn of listeners) {
    try {
      fn(available)
    } catch (e) {
      console.error('[apiState] listener error:', e)
    }
  }
}

export function markApiDown() {
  if (!available) return
  available = false
  notify()
}

/** The API answered again (a later successful request, or a recovery path). */
export function markApiUp() {
  if (available) return
  available = true
  notify()
}

export function isApiAvailable() {
  return available
}

/**
 * Subscribe to API availability changes.
 * @param {(available: boolean) => void} fn
 * @returns {Function} unsubscribe
 */
export function onApiStateChange(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
