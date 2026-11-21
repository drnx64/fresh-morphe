/**
 * Stats section — 4 stat cards.
 *
 * "Last checked" lives under the What's New heading on the apps page instead:
 * freshness answers a question about the changes, and nobody reads the strip
 * of stat cards to find it.
 */
import { el } from '../ui.js'
import * as store from '../store.js'

export function renderStatsSection() {
  const section = el('section', { class: 'stats-section', 'aria-labelledby': 'stats-heading' })
  section.appendChild(el('h2', { class: 'sr-only', id: 'stats-heading' }, ['Quick Statistics']))

  const bundles = store.get('bundles') || {}
  const bundleList = Object.values(bundles)
  const seen = new Set()
  for (const b of bundleList) {
    for (const app of b.apps || []) seen.add(app.package)
  }
  const totalApps = seen.size
  const stats = store.get('stats') || {}

  const statsGrid = el('div', { class: 'stats-grid' }, [
    makeStatCard('Total Bundles', String(bundleList.length)),
    makeStatCard('Total Apps', String(totalApps)),
    makeStatCard('New Apps Today', String(stats.new_apps_today ?? '-'), true),
    makeStatCard('New Bundles Today', String(stats.new_bundles_today ?? '-'), true),
  ])

  section.appendChild(statsGrid)
  return section
}

function makeStatCard(label, value, highlight = false) {
  const card = el('div', { class: `stat-card${highlight ? ' highlight' : ''}` }, [
    el('span', { class: 'stat-label' }, [label]),
    el('span', { class: 'stat-value' }, [value]),
  ])
  return card
}
