/**
 * Cross-bundle patch comparison model — pure, no DOM.
 *
 * A patch has no id/slug in the bundle files, so lowercased name is the only
 * join key available across bundles (matches diff_engine.py and /diff).
 */

function canonicalContent(patch) {
  return JSON.stringify({
    description: patch.description || '',
    use: !!patch.use,
    options: patch.options || [],
    compatible_versions: [...(patch.compatible_versions || [])].sort(),
  })
}

/**
 * True when the same patch name carries different content across bundles.
 * @param {Array<Object>} variants
 * @returns {boolean}
 */
export function differsAcross(variants) {
  if (!variants || variants.length < 2) return false
  const first = canonicalContent(variants[0])
  return variants.some((v) => canonicalContent(v) !== first)
}

/**
 * Build comparison rows from per-bundle patch lists.
 *
 * Uses the *union* of patch names so a patch present in 2-of-N bundles is
 * still reported as shared — never dropped.
 *
 * @param {Array<{patches?: Array<Object>}>} cols one entry per selected bundle
 * @returns {Array<{name: string, key: string, by: Map<number, Object>,
 *   count: number, shared: boolean, differs: boolean, onlyCol: number}>}
 *   sorted by bundle-count desc, then name asc.
 */
export function buildCompareRows(cols) {
  const rowsByKey = new Map()

  cols.forEach((col, ci) => {
    for (const patch of col.patches || []) {
      const name = patch && patch.name ? String(patch.name) : ''
      const key = name.toLowerCase()
      if (!key) continue
      let row = rowsByKey.get(key)
      if (!row) {
        row = { name, key, by: new Map() }
        rowsByKey.set(key, row)
      }
      if (!row.by.has(ci)) row.by.set(ci, patch)
    }
  })

  const rows = [...rowsByKey.values()]
  for (const row of rows) {
    row.count = row.by.size
    row.shared = row.count >= 2
    row.differs = differsAcross([...row.by.values()])
    row.onlyCol = row.count === 1 ? row.by.keys().next().value : -1
  }

  rows.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
  return rows
}

/**
 * @param {Array<Object>} rows
 * @param {'all'|'shared'|'unique'|'differs'} filter
 * @returns {Array<Object>}
 */
export function filterRows(rows, filter) {
  if (filter === 'shared') return rows.filter((r) => r.shared)
  if (filter === 'unique') return rows.filter((r) => !r.shared)
  if (filter === 'differs') return rows.filter((r) => r.differs)
  return rows
}
