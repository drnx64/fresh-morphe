/**
 * Hash-based router for GitHub Pages compatibility.
 * Supports parameterized routes: /bundle/:name
 */
import { devLog } from './utils/devLog.js'

/** @type {Array<{pattern: RegExp, handler: Function, name: string}>} */
const routes = []

/** @type {Function|null} */
let notFoundHandler = null

/** @type {string|null} */
let currentRoute = null

/**
 * Register a route.
 * @param {string} path - Route path with optional :param placeholders
 * @param {Function} handler - Called with (params) where params is an object
 * @param {string} [name] - Optional route name
 */
export function addRoute(path, handler, name) {
  const pattern = pathToRegex(path)
  routes.push({ pattern, handler, name: name || path, paramNames: paramNamesFromPath(path) })
}

/**
 * Set a fallback handler for unmatched routes.
 * @param {Function} handler
 */
export function setNotFound(handler) {
  notFoundHandler = handler
}

/** @type {boolean} */
let started = false

/**
 * Start listening to hash changes and navigate to current hash.
 */
export function start() {
  if (started) return
  started = true
  window.addEventListener('hashchange', handleHashChange)
  handleHashChange()
}

/**
 * Navigate to a hash path.
 * @param {string} path - e.g. '/bundles' or '/bundle/morphe'
 */
export function navigate(path) {
  window.location.hash = path
}

/**
 * Get the current route path (without #).
 * @returns {string}
 */
export function getCurrentPath() {
  return window.location.hash.slice(1) || '/'
}

function handleHashChange() {
  const path = getCurrentPath()
  if (path === currentRoute) return
  devLog('route', path)
  currentRoute = path

  for (const route of routes) {
    const match = path.match(route.pattern)
    if (match) {
      const params = extractParams(route.paramNames, match)
      try {
        route.handler(params)
      } catch (e) {
        console.error(`[router] Error in handler for ${route.name}:`, e)
      }
      window.scrollTo({ top: 0, behavior: 'smooth' })
      return
    }
  }

  if (notFoundHandler) {
    notFoundHandler(path)
  } else {
    console.warn(`[router] No route matched: ${path}`)
  }

  window.scrollTo({ top: 0, behavior: 'smooth' })
}

/**
 * Convert a path pattern like '/bundle/:name' to a regex.
 * @param {string} path
 * @returns {RegExp}
 */
function pathToRegex(path) {
  const escaped = path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const withParams = escaped.replace(/:([a-zA-Z_][a-zA-Z0-9_]*)/g, '([^/]+)')
  return new RegExp(`^#?/?${withParams}/?$`)
}

/**
 * Collect `:param` names from a route path, before they are compiled away.
 * @param {string} path
 * @returns {string[]}
 */
function paramNamesFromPath(path) {
  return (path.match(/:([a-zA-Z_][a-zA-Z0-9_]*)/g) || []).map((p) => p.slice(1))
}

/**
 * Extract named params from a match.
 * @param {string[]} paramNames
 * @param {RegExpMatchArray} match
 * @returns {Object<string, string>}
 */
function extractParams(paramNames, match) {
  const params = {}
  paramNames.forEach((name, i) => {
    params[name] = decodeURIComponent(match[i + 1])
  })
  return params
}

/**
 * Force re-evaluate the current route (e.g. after state change).
 */
export function rerender() {
  currentRoute = null
  handleHashChange()
}
