/**
 * Shared circuit breaker for the optional phone API.
 *
 * Once the boot request fails, stop trying the API for every bundle-detail
 * fetch. The page still works from the static snapshot; a reload retries.
 */
let available = true

export function markApiDown() {
  available = false
}

export function isApiAvailable() {
  return available
}
