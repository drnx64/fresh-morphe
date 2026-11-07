/**
 * Minimal reactive state store — pub/sub pattern.
 * Replaces React's useReducer + context.
 */

/** @type {Object<string, any>} */
const state = {}

/** @type {Object<string, Set<Function>>} */
const listeners = {}

/** @type {Set<Function>} */
const globalListeners = new Set()

/**
 * Get a state value. No key = entire state (shallow copy).
 * @param {string} [key]
 * @returns {any}
 */
export function get(key) {
  if (key === undefined) return { ...state }
  return state[key]
}

/**
 * Set a state value and notify subscribers.
 * @param {string} key
 * @param {any} value
 */
export function set(key, value) {
  const old = state[key]
  state[key] = value
  if (old !== value) {
    notify(key)
    for (const fn of globalListeners) {
      try { fn(key, value, old) } catch (e) { console.error('[store] global listener error:', e) }
    }
  }
}

/**
 * Merge an object into state, notifying for each changed key.
 * @param {Object<string, any>} partial
 */
export function merge(partial) {
  for (const [key, value] of Object.entries(partial)) {
    set(key, value)
  }
}

/**
 * Subscribe to changes on a specific key.
 * @param {string} key
 * @param {Function} fn - called with (newValue, oldValue)
 * @returns {Function} unsubscribe function
 */
export function subscribe(key, fn) {
  if (!listeners[key]) listeners[key] = new Set()
  listeners[key].add(fn)
  return () => listeners[key].delete(fn)
}

/**
 * Subscribe to all state changes.
 * @param {Function} fn - called with (key, newValue, oldValue)
 * @returns {Function} unsubscribe function
 */
export function subscribeAll(fn) {
  globalListeners.add(fn)
  return () => globalListeners.delete(fn)
}

/**
 * Initialize state from localStorage (for persisted values).
 * @param {Object<string, any>} defaults
 */
export function init(defaults) {
  for (const [key, value] of Object.entries(defaults)) {
    if (state[key] === undefined) state[key] = value
  }
}

function notify(key) {
  const subs = listeners[key]
  if (subs) {
    for (const fn of subs) {
      try { fn(state[key]) } catch (e) { console.error('[store] listener error:', e) }
    }
  }
}
