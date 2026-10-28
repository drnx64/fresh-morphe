/**
 * Watchlist — localStorage-based app tracking + browser notifications.
 */

const KEY = 'morphe_watchlist'

export function getWatchlist() {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) || '[]')
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === 'string') : []
  } catch {
    return []
  }
}

export function isWatched(pkg) {
  return getWatchlist().includes(pkg)
}

export function toggleWatched(pkg) {
  const list = getWatchlist()
  const idx = list.indexOf(pkg)
  if (idx >= 0) list.splice(idx, 1)
  else list.push(pkg)
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch {
    /* ignore */
  }
  return list.includes(pkg)
}

export function isNotificationSupported() {
  return typeof window !== 'undefined' && 'Notification' in window
}

export function ensureNotificationPermission() {
  if (!isNotificationSupported()) return
  if (Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {})
  }
}

export function notifyWatchedUpdates(changes) {
  if (!changes?.affected_bundles?.length) return
  if (!isNotificationSupported() || Notification.permission !== 'granted') return
  const watched = new Set(getWatchlist())
  if (watched.size === 0) return
  const updated = []
  for (const bundle of changes.affected_bundles) {
    for (const app of bundle.apps || []) {
      if (
        watched.has(app.package) &&
        (app.badge_type === 'NEW APP' || app.badge_type === 'UPDATED APP')
      ) {
        updated.push(app.name || app.package)
      }
    }
  }
  if (updated.length === 0) return
  try {
    new Notification('Morphe Tracker', {
      body: `${updated.length} watched app${updated.length !== 1 ? 's' : ''} updated: ${updated.slice(0, 3).join(', ')}${updated.length > 3 ? '…' : ''}`,
      tag: 'morphe-watch-updates',
    })
  } catch {
    /* ignore */
  }
}
