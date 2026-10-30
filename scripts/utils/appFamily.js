/**
 * App families — collapse apps that share one publisher into a stack.
 *
 * Primary signal is the developer/publisher name the pipeline resolves per app
 * (`app.developer`, Play Store "Offered by" → APKMirror fallback). Until that
 * field is populated, the fallback is the reverse-domain package namespace: the
 * prefix one publisher uses for all of its apps, normally two segments
 * (`com.trieudadlovestun`), or three behind a country code (`jp.co.celsys`).
 *
 * Without grouping a handful of publishers bury the directory — `com.google`
 * alone owns 21 packages and `com.trieudadlovestun` 50 exam trainers.
 *
 * A developer stacks from two apps (same publisher is a strong signal). A
 * namespace needs at least three, or two that keep sharing a third segment
 * (`app.simple.inure` / `app.simple.inure.play`): two apps under one domain are
 * otherwise unrelated products, and stacking them hides one behind the other.
 */

const COUNTRY_CODE_PATTERN = /^[a-z]{2}$/

/** Second-level markers that sit between a country code and the publisher. */
const ORGANISATION_MARKERS = new Set(['ac', 'co', 'com', 'edu', 'gov', 'ne', 'net', 'or', 'org'])

/** Corporate suffixes and filler words that do not identify a publisher. */
const DEVELOPER_NOISE_PATTERN =
  /\b(gmbh|inc|llc|ltd|limited|ab|ag|as|oy|se|bv|nv|sa|sas|sarl|co|corp|corporation|company|pte|pty|plc|spa|srl|kft|doo|aps|sro|ooo|group|holdings|studios?|technologies|technology|software|labs?|interactive|entertainment|games?|media|mobile|apps?)\b/g

/** Names that mean "we do not know", not "these apps belong together". */
const GENERIC_DEVELOPERS = new Set([
  'unknown',
  'unknown developer',
  'n a',
  'na',
  'none',
  'various',
  'various developers',
  'independent',
  'independent developer',
])

/** Namespaces with at least this many apps always become a stack. */
export const APP_FAMILY_MIN_SIZE = 3

/** A pair of apps also stacks when they share this many package segments. */
export const APP_FAMILY_PAIR_DEPTH = 3

/** Developer groups stack from this size up. */
export const APP_FAMILY_DEVELOPER_MIN_SIZE = 2

/**
 * Publisher namespace of a package name.
 * @param {string} packageName - e.g. `jp.co.celsys.paint`
 * @returns {string} e.g. `jp.co.celsys` ('' for an empty name)
 */
export function packageNamespace(packageName) {
  const segments = String(packageName ?? '')
    .toLowerCase()
    .split('.')
    .filter(Boolean)
  if (segments.length <= 1) return segments[0] ?? ''
  const behindCountryCode =
    COUNTRY_CODE_PATTERN.test(segments[0]) && ORGANISATION_MARKERS.has(segments[1])
  return segments.slice(0, behindCountryCode ? 3 : 2).join('.')
}

/**
 * Group key for a developer name: "Rovio Entertainment Ltd." -> "rovio".
 * Returns '' for blank and generic names so they never form a family.
 * @param {string} name
 * @returns {string}
 */
export function normalizeDeveloper(name) {
  const cleaned = String(name ?? '')
    .toLowerCase()
    .replace(/[®™©]/g, ' ')
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!cleaned) return ''
  const stripped = cleaned.replace(DEVELOPER_NOISE_PATTERN, ' ').replace(/\s+/g, ' ').trim()
  const key = stripped || cleaned
  if (GENERIC_DEVELOPERS.has(key)) return ''
  return key
}

/**
 * Number of leading package segments two names share.
 * @param {string} a
 * @param {string} b
 * @returns {number}
 */
export function sharedSegmentCount(a, b) {
  const left = String(a ?? '').toLowerCase().split('.')
  const right = String(b ?? '').toLowerCase().split('.')
  let shared = 0
  while (shared < left.length && shared < right.length && left[shared] === right[shared]) {
    shared++
  }
  return shared
}

/**
 * Decide whether one namespace's apps should be collapsed into a stack.
 * @param {Array<{ package: string }>} members
 * @returns {boolean}
 */
export function qualifiesAsFamily(members) {
  if (!Array.isArray(members) || members.length < 2) return false
  if (members.length >= APP_FAMILY_MIN_SIZE) return true
  return sharedSegmentCount(members[0]?.package, members[1]?.package) >= APP_FAMILY_PAIR_DEPTH
}

/**
 * Split an app list into single cards and stacks.
 *
 * Developer families win: an app already claimed by its publisher's stack is not
 * added to a namespace stack as well, so the two rules never fight over it.
 *
 * Returns `{ kind: 'app', app }` and
 * `{ kind: 'family', id, label, source, apps }` entries in the order the apps
 * were given — a stack takes the slot of its first member, so an alphabetically
 * sorted list stays alphabetical at the card level.
 *
 * @param {Array<{ package: string, developer?: string }>} apps
 * @returns {Array<{ kind: 'app', app: object } | { kind: 'family', id: string, label: string, source: 'developer'|'namespace', apps: object[] }>}
 */
export function groupAppsIntoFamilies(apps) {
  const list = Array.isArray(apps) ? apps : []

  const developerGroups = new Map()
  const namespaceGroups = new Map()
  for (const app of list) {
    const developerKey = normalizeDeveloper(app?.developer)
    if (developerKey) {
      const bucket = developerGroups.get(developerKey)
      if (bucket) bucket.apps.push(app)
      else {
        developerGroups.set(developerKey, {
          id: `dev:${developerKey}`,
          label: String(app.developer).trim(),
          apps: [app],
        })
      }
    }

    const namespace = packageNamespace(app?.package)
    if (namespace) {
      const bucket = namespaceGroups.get(namespace)
      if (bucket) bucket.apps.push(app)
      else namespaceGroups.set(namespace, { id: `ns:${namespace}`, label: namespace, apps: [app] })
    }
  }

  /** @type {Map<object, object>} app -> family it belongs to */
  const families = new Map()

  for (const group of developerGroups.values()) {
    if (group.apps.length < APP_FAMILY_DEVELOPER_MIN_SIZE) continue
    const family = { ...group, source: 'developer' }
    for (const app of family.apps) families.set(app, family)
  }

  for (const group of namespaceGroups.values()) {
    const free = group.apps.filter((app) => !families.has(app))
    if (!qualifiesAsFamily(free)) continue
    const family = { ...group, apps: free, source: 'namespace' }
    for (const app of free) families.set(app, family)
  }

  const entries = []
  const placed = new Set()
  for (const app of list) {
    const family = families.get(app)
    if (!family) {
      entries.push({ kind: 'app', app })
      continue
    }
    if (placed.has(family)) continue
    placed.add(family)
    entries.push({
      kind: 'family',
      id: family.id,
      label: family.label,
      source: family.source,
      apps: family.apps,
    })
  }
  return entries
}
