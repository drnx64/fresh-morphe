/**
 * Release history for a bundle. Only the phone API stores historical release
 * bodies; the static snapshot still exposes the current release notes inline.
 */
import { fetchJson } from '../utils/http.js'
import { API_ENABLED, API_HEADERS, apiUrl } from '../config.js'
import { devLog } from '../utils/devLog.js'
import { isApiAvailable } from './apiState.js'

const RELEASE_TIMEOUT_MS = 5000

export async function loadReleases(bundleName, channels = []) {
  if (!API_ENABLED || !isApiAvailable() || !bundleName) return []

  const tried = new Set()
  const order = [...channels, 'stable', 'latest', 'dev'].filter((channel) => {
    if (!channel || tried.has(channel)) return false
    tried.add(channel)
    return true
  })

  for (const channel of order) {
    try {
      const data = await fetchJson(
        apiUrl(`/v1/bundles/${encodeURIComponent(bundleName)}/${encodeURIComponent(channel)}/releases?limit=30`),
        { releases: [] },
        { timeoutMs: RELEASE_TIMEOUT_MS, cache: 'no-cache', headers: API_HEADERS },
      )
      if (Array.isArray(data?.releases) && data.releases.length) {
        devLog('releases loaded', bundleName, channel, data.releases.length)
        return data.releases
      }
    } catch (err) {
      console.warn('[releases] failed for', bundleName, channel, err.message)
      devLog('releases channel failed', bundleName, channel, err.message)
    }
  }
  devLog('releases empty', bundleName, order)
  return []
}
