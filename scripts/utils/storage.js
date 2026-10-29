/**
 * Fresh Morphe storage helpers.
 *
 * Fresh keys use the `fresh_` prefix. One-time migration copies any legacy
 * `morphe_*` values to their new names so themes, watchlists, search history
 * and the icon queue survive the rebrand.
 */

const FRESH_PREFIX = 'fresh_'
const LEGACY_PREFIX = 'morphe_'

export function freshKey(key) {
  if (key.startsWith(FRESH_PREFIX)) return key
  if (key.startsWith(LEGACY_PREFIX)) return FRESH_PREFIX + key.slice(LEGACY_PREFIX.length)
  return FRESH_PREFIX + key
}

export function legacyKey(key) {
  const name = key.startsWith(FRESH_PREFIX) ? key.slice(FRESH_PREFIX.length) : key
  return LEGACY_PREFIX + name
}

export function migrateLegacyStorage() {
  try {
    const keys = []
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i)
      if (key && key.startsWith(LEGACY_PREFIX)) keys.push(key)
    }
    for (const oldKey of keys) {
      const newKey = freshKey(oldKey)
      if (localStorage.getItem(newKey) === null) {
        localStorage.setItem(newKey, localStorage.getItem(oldKey))
      }
      localStorage.removeItem(oldKey)
    }
  } catch {
    /* private mode / disabled storage */
  }
}

export function storageGet(key, fallback = null) {
  try {
    const value = localStorage.getItem(freshKey(key))
    if (value !== null) return value
    const legacy = localStorage.getItem(legacyKey(key))
    if (legacy !== null) {
      const name = freshKey(key)
      localStorage.setItem(name, legacy)
      localStorage.removeItem(legacyKey(key))
      return legacy
    }
  } catch {
    /* ignore */
  }
  return fallback
}

export function storageSet(key, value) {
  try {
    localStorage.setItem(freshKey(key), value)
  } catch {
    /* ignore */
  }
}

export function storageRemove(key) {
  try {
    localStorage.removeItem(freshKey(key))
  } catch {
    /* ignore */
  }
}
