/**
 * App families — collapse apps that share one publisher into a stack.
 *
 * Grouping keys on the publisher identity and on nothing else: two apps
 * stack only when their identities normalize to the same key. A store name
 * normalizes by case, punctuation and legal-entity suffixes only (`GmbH`,
 * `Inc`, `LLC`, `Ltd`) — never descriptive words, because those can carry
 * the publisher's identity: "VKL" and "VKL Apps" are different developers
 * on the Play Store, so they must not share a stack.
 *
 * Three explicit maps do the rest of the merging — facts we know, never
 * similarity:
 *
 * - PUBLISHER_PARENT folds aliases of one company onto one key: product
 *   teams under their owner (Instagram → Meta), legal spellings under the
 *   brand ("Samsung Electronics Co., Ltd." → samsung, "Pocket Prep, Inc." →
 *   pocketprep), and historic or regional roots under the current company
 *   (com.sec → samsung, com.sonymobile → sony, com.hihonor → honor).
 * - PACKAGE_PREFIX_PARENT owns a package prefix to a company when the
 *   two-segment namespace is shared with strangers: `com.ss.android` is
 *   ByteDance's, while `com.ss.squarehome2` (Square Home) is not — reading
 *   `ss` as the brand would stack them, which is the VKL bug in another
 *   dress. An owned prefix outranks even a store name, because Play often
 *   lists the product ("TikTok") rather than the company.
 * - NAMESPACE_GENERIC_BRANDS refuses category words as namespace brands:
 *   `com.android.*` spans Google, Xiaomi, OnePlus and LineageOS, so
 *   "android" names no publisher. A word many publishers could wear is not
 *   an identity, so those apps stay single cards.
 *
 * When Play never resolved a package, the pipeline leaves the namespace in
 * `app.developer` (`com.google`). That is the same publisher the store
 * lists as "Google LLC", so the namespace root is read back as a name —
 * but only from a trusted registrar root (`com.google`, `org.telegram`,
 * `jp.co.celsys` → "google", "telegram", "celsys"), never from a
 * brand-shaped prefix (`proton.android`, `tv.twitch`: their tails would
 * merge Proton, Deezer and Hide.me into one "Android" stack). The recovered
 * name keys exactly like a store name, so one company is one stack. A
 * namespace that yields no trusted name never groups: a package id is not a
 * publisher name.
 *
 * A developer stacks from two apps up: the same publisher name twice is a
 * strong signal. There is deliberately no second, weaker rule — a stack that
 * hides one app behind an unrelated neighbour is worse than one more card on
 * the grid, and the browse list is capped anyway.
 */

const COUNTRY_CODE_PATTERN = /^[a-z]{2}$/

/** Second-level markers that sit between a country code and the publisher. */
const ORGANISATION_MARKERS = new Set(['ac', 'co', 'com', 'edu', 'gov', 'ne', 'net', 'or', 'org'])

/**
 * Corporate-form suffixes only: words that name a legal entity rather than
 * the publisher. Descriptive words (`apps`, `games`, `media`, `group`,
 * `studios`…) are deliberately absent — see the header: stripping them made
 * "VKL Apps" and "VKL" one key.
 */
const DEVELOPER_NOISE_PATTERN =
  /\b(gmbh|inc|llc|ltd|limited|ab|ag|as|oy|se|bv|nv|sa|sas|sarl|co|corp|corporation|company|pte|pty|plc|spa|srl|kft|doo|aps|sro|ooo)\b/g

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

/**
 * Aliases of one company folded onto one key.
 *
 * Every entry is a fact about ownership or spelling, never name similarity:
 *
 * - Product teams under their owner: Instagram and WhatsApp ship under
 *   their own names on Play while Facebook and Messenger read "Meta
 *   Platforms, Inc." — without the map the four never stack.
 * - Legal spellings under the brand: "Samsung Electronics Co., Ltd." and
 *   "Pocket Prep, Inc." are the same publishers as the `com.samsung` and
 *   `com.pocketprep` namespaces once the corporate suffixes come off; the
 *   residue (`samsung electronics`, `pocket prep`) maps home.
 * - Historic, regional and subsidiary names under the current company:
 *   `com.sec` and `com.sonymobile` are Samsung's and Sony's old roots,
 *   `com.hihonor` is Honor's, `com.zhiliaoapp` is TikTok's ByteDance
 *   entity, Sony LIV lists "Sony Pictures Networks India".
 *
 * Keys are `normalizeDeveloper` output; values are the canonical key.
 */
const PUBLISHER_PARENT = {
  instagram: 'meta',
  whatsapp: 'meta',
  'meta platforms': 'meta',
  facebook: 'meta',
  messenger: 'meta',
  threads: 'meta',
  google: 'google',
  youtube: 'google',
  sec: 'samsung',
  'samsung electronics': 'samsung',
  sonymobile: 'sony',
  sonyericsson: 'sony',
  'sony pictures networks india private': 'sony',
  miui: 'xiaomi',
  'beijing xiaomi mobile software': 'xiaomi',
  hihonor: 'honor',
  'honor information technology': 'honor',
  zhiliaoapp: 'bytedance',
  'nothing technology': 'nothing',
  'halfbrick studios': 'halfbrick',
  'pocket prep': 'pocketprep',
  'tencent technology shenzhen': 'tencent',
  'petr n levka urbandroid': 'urbandroid',
  'new star games': 'newstargames',
  'not doppler': 'notdoppler',
  'noctua software': 'noctuasoftware',
  'tom bayley': 'tombayley',
  'tuya smart': 'tuya',
  'nuvio media': 'nuvio',
  'pryshedko dev': 'pryshedko',
  'bongo solutions': 'bongo',
  'angel television private': 'angel',
}

/** Display label for a parent group key; falls back to the first member's name. */
const PUBLISHER_GROUP_LABEL = {
  meta: 'Meta',
  google: 'Google',
  samsung: 'Samsung',
  sony: 'Sony',
  xiaomi: 'Xiaomi',
  honor: 'Honor',
  bytedance: 'ByteDance',
  tencent: 'Tencent',
  oneplus: 'OnePlus',
  nothing: 'Nothing',
  halfbrick: 'Halfbrick',
  pocketprep: 'Pocket Prep',
  urbandroid: 'Urbandroid',
  angel: 'Angel Television',
  newstargames: 'New Star Games',
  notdoppler: 'Not Doppler',
  noctuasoftware: 'Noctua Software',
  tombayley: 'Tom Bayley',
  tuya: 'Tuya',
  nuvio: 'Nuvio',
  pryshedko: 'Pryshedko',
  bongo: 'Bongo',
}

/**
 * Category words that cannot be a namespace brand.
 *
 * `com.android.*` spans Google (Chrome), Xiaomi (Recorder, Calendar,
 * Contacts), OnePlus (Launcher) and LineageOS (Gallery) — "android" names
 * no one publisher, so reading it as one would stack four companies. Same
 * for `com.app`, `com.live`, `com.vpn`, `com.community`, `com.application`
 * and the usual suspects (mobile, game, free, pro, tools…): a word many
 * publishers could wear is not an identity, so such apps stay single cards.
 * Store names are unaffected — Play declaring a developer is a fact.
 */
const NAMESPACE_GENERIC_BRANDS = new Set([
  'android',
  'app',
  'apps',
  'application',
  'applications',
  'live',
  'vpn',
  'community',
  'mobile',
  'game',
  'games',
  'free',
  'pro',
  'official',
  'beta',
  'demo',
  'test',
  'tools',
  'tool',
  'service',
  'services',
  'network',
  'system',
  'systems',
  'store',
  'shop',
  'cloud',
  'social',
  'chat',
  'mail',
  'news',
  'photo',
  'video',
  'music',
  'weather',
  'clock',
  'notes',
  'launcher',
  'browser',
  'keyboard',
  'dialer',
  'contacts',
  'calendar',
  'player',
  'manager',
  'backup',
  'secure',
])

/**
 * Package prefixes whose owning company we know as fact.
 *
 * The two-segment namespace is sometimes shared by unrelated publishers:
 * ByteDance's TikTok family lives under `com.ss.android.*` while Square
 * Home (Total_Apps, a Korean indie) sits at `com.ss.squarehome2`. Grouping
 * on the `ss` brand would stack them — so the prefix itself is declared
 * here, ahead of any name reading. Keys are exact package prefixes.
 */
const PACKAGE_PREFIX_PARENT = {
  'com.ss.android': 'bytedance',
}

/** Developer groups stack from this size up. */
export const APP_FAMILY_DEVELOPER_MIN_SIZE = 2

/**
 * Publisher namespace of a package name.
 *
 * `namespaceIdentity` reads the publisher name back out of it; display uses
 * it as the last resort (see developerLabel).
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
 * Group key for a developer name: "Rovio Ltd." -> "rovio".
 *
 * Only corporate-form suffixes come off — "VKL Apps" stays "vkl apps" so it
 * never merges with "VKL". Returns '' for blank and generic names so they
 * never form a family.
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
  return PUBLISHER_PARENT[key] || key
}

/**
 * Display label for a normalized developer group key.
 * @param {string} developerKey - output of `normalizeDeveloper`
 * @param {string} [fallback] - first member's raw developer name
 * @returns {string}
 */
export function developerGroupLabel(developerKey, fallback = '') {
  return PUBLISHER_GROUP_LABEL[developerKey] || String(fallback || '').trim()
}

/**
 * True when the "developer" is only the app's own package prefix — the
 * pipeline's namespace fallback for packages it never resolved on Play
 * (`com.goodreads` / package `com.goodreads`), not a name a publisher chose.
 * @param {{ package?: string, developer?: string }} app
 * @returns {boolean}
 */
function isPackagePrefixDeveloper(app) {
  const developer = String(app?.developer ?? '')
  const packageName = String(app?.package ?? '')
  if (!developer || !packageName) return false
  return packageName === developer || packageName.startsWith(`${developer}.`)
}

/**
 * Publisher name the package namespace carries, when its root is a trusted
 * one: `com.google` → "google", `org.telegram` → "telegram",
 * `jp.co.celsys` → "celsys". A registrar-style root (an organisation marker,
 * or a country code behind one) makes the segment after it the brand — the
 * very name Play would have shown. Brand-shaped roots are refused: reading
 * the tail of `proton.android` or `tv.twitch` would merge Proton, Deezer and
 * Hide.me into one "Android" stack — the VKL bug in another dress.
 *
 * @param {string} packageName
 * @returns {{ key: string, label: string } | null} null when the namespace
 *   names no publisher we can trust
 */
function namespaceIdentity(packageName) {
  const segments = packageNamespace(packageName)
    .split('.')
    .filter(Boolean)
  const brand =
    segments.length >= 3
      ? segments[2]
      : segments.length === 2 && ORGANISATION_MARKERS.has(segments[0])
        ? segments[1]
        : ''
  if (!brand) return null
  // A category word is not a publisher: `com.android.*` alone spans Google,
  // Xiaomi, OnePlus and LineageOS, so recovering "android" from it would
  // stack four companies (see NAMESPACE_GENERIC_BRANDS).
  if (NAMESPACE_GENERIC_BRANDS.has(brand)) return null
  const key = normalizeDeveloper(brand)
  if (!key) return null
  return {
    key,
    label: developerGroupLabel(key, '') || brand.charAt(0).toUpperCase() + brand.slice(1),
  }
}

/**
 * Publisher identity for one app — the store name when we have one,
 * otherwise the name the package namespace carries.
 *
 * A namespace string in `developer` (`com.google`, the pipeline's fallback)
 * is not a name, so it never keys directly; it is read through
 * `namespaceIdentity` instead, which is what makes `com.google` and
 * "Google LLC" one key. A prefix without a dot (`flar2` for
 * `flar2.devcheck`) is a name that merely matches the package root, not a
 * namespace, and keys as itself.
 *
 * @param {string} packageName
 * @param {string} [developer]
 * @returns {{ key: string, label: string } | null} null when no publisher
 *   signal exists — such an app never groups.
 */
function publisherIdentity(packageName, developer) {
  // An owned package prefix is the strongest signal: Play often lists the
  // product ("TikTok") rather than the company, so `com.ss.android.*` keys
  // as ByteDance even when the store name says something else — while
  // `com.ss.squarehome2` (Square Home, Total_Apps) never matches and stays
  // out of that stack (see PACKAGE_PREFIX_PARENT).
  const pkg = String(packageName ?? '').toLowerCase()
  for (const [prefix, parent] of Object.entries(PACKAGE_PREFIX_PARENT)) {
    if (pkg === prefix || pkg.startsWith(`${prefix}.`)) {
      const label = developerGroupLabel(parent) || parent.charAt(0).toUpperCase() + parent.slice(1)
      return { key: parent, label }
    }
  }
  const raw = String(developer ?? '').trim()
  const namespaceString =
    raw.includes('.') && isPackagePrefixDeveloper({ package: packageName, developer: raw })
  if (raw && !namespaceString) {
    const key = normalizeDeveloper(raw)
    if (key) return { key, label: developerGroupLabel(key, raw) || raw }
  }
  return namespaceIdentity(packageName)
}

/**
 * Human publisher label for one app: store name when we have it, the name
 * the namespace carries otherwise (`com.google` → "Google", the same name
 * Play uses), and the raw value last — `proton.android` prints as itself
 * rather than being guessed into "Android".
 *
 * The modal/card used to print the raw package id (`com.google.android.yt`)
 * with nothing else, which tells a user nothing about who ships the app. This
 * prefers the resolved developer and folds subsidiaries to their parent
 * (Facebook → Meta, YouTube → Google).
 *
 * @param {string} packageName
 * @param {string} [developer] - resolved developer, if any
 * @returns {string} '' only when neither signal exists
 */
export function developerLabel(packageName, developer = '') {
  const identity = publisherIdentity(packageName, developer)
  if (identity) return identity.label
  return String(developer ?? '').trim()
}

/**
 * Split an app list into single cards and publisher stacks.
 *
 * Grouping reads the publisher identity (see `publisherIdentity`): a store
 * name, or the name the package namespace carries when Play never resolved
 * one — `com.google` and "Google LLC" are one publisher and one stack. Two
 * distinct publishers whose names merely rhyme — "VKL" and "VKL Apps" — stay
 * two cards, and a namespace that yields no trusted name never groups. A
 * stack takes the slot of its first member, so a sorted list stays sorted at
 * the card level.
 *
 * Returns `{ kind: 'app', app }` and
 * `{ kind: 'family', id, label, apps }` entries in the order the apps were
 * given.
 *
 * @param {Array<{ package: string, developer?: string }>} apps
 * @returns {Array<{ kind: 'app', app: object } | { kind: 'family', id: string, label: string, apps: object[] }>}}
 */
export function groupAppsIntoFamilies(apps) {
  const list = Array.isArray(apps) ? apps : []

  const developerGroups = new Map()
  for (const app of list) {
    const identity = publisherIdentity(app?.package, app?.developer)
    if (!identity) continue
    const bucket = developerGroups.get(identity.key)
    if (bucket) bucket.apps.push(app)
    else {
      developerGroups.set(identity.key, {
        id: `dev:${identity.key}`,
        label: identity.label,
        apps: [app],
      })
    }
  }

  /** @type {Map<object, object>} app -> family it belongs to */
  const families = new Map()
  for (const group of developerGroups.values()) {
    if (group.apps.length < APP_FAMILY_DEVELOPER_MIN_SIZE) continue
    for (const app of group.apps) families.set(app, group)
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
      apps: family.apps,
    })
  }
  return entries
}
