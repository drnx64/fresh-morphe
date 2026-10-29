/**
 * What's New section — collapsible, shows affected bundles from today's changes.
 * Hierarchy: NEW BUNDLE → bundles with NEW APPs → bundles with UPDATED APPs.
 * Shows version comparison tags (v1.0.1 → v1.0.2).
 * Collapse state persisted in localStorage.
 * Includes a summary peek row visible when collapsed.
 */
import { el } from '../ui.js'
import * as store from '../store.js'
import { groupAffectedBundles, resolveAppName, getAppIconUrl, renderAppIcon } from '../utils/misc.js'
import { getAuthorLink } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { dateParts, dayHue, shortVersion, badgeLabel } from '../utils/format.js'
import { CHEVRON_DOWN, VERSION_ARROW, CHEVRON_RIGHT } from '../utils/svg.js'
import { renderColorGame } from '../components/colorGame.js'

const HIERARCHY_ORDER = { 'NEW BUNDLE': 0, 'UPDATED': 1, 'REMOVED BUNDLE': 2 }
const STORAGE_KEY = 'fresh_updates_collapsed'
const HIDE_DEV_KEY = 'fresh_hide_dev'

function isHideDev() {
  return localStorage.getItem(HIDE_DEV_KEY) === 'true'
}

export function renderTodayUpdates() {
  const section = el('section', { class: 'today-updates-section', 'aria-labelledby': 'today-heading' })

  const isCollapsed = localStorage.getItem(STORAGE_KEY) !== 'false'

  const changes = store.get('changes')
  const todayBundles = Array.isArray(changes?.affected_bundles) ? changes.affected_bundles : []
  const latest = changes?.latest
  const latestBundles = Array.isArray(latest?.affected_bundles) ? latest.affected_bundles : []
  const usingLatest = todayBundles.length === 0 && latestBundles.length > 0
  const displayDate = usingLatest
    ? (latest.date || '')
    : (changes?.date || store.get('liveDataDate') || '')
  let affectedBundles = usingLatest ? latestBundles : todayBundles

  const headingRow = el('div', { class: 'today-updates-heading-row' })
  const headingLeft = el('div', { class: 'today-updates-heading-left' })
  headingLeft.appendChild(el('h2', { class: 'section-title', id: 'today-heading' }, ["What's New"]))

  if (displayDate) {
    const liveParts = dateParts(displayDate)
    const liveDateEl = el('span', { class: 'today-updates-date' }, [
      el('span', { class: 'today-updates-date-day' }, [liveParts.day]),
      ` ${liveParts.month}, ${liveParts.year}`,
    ])
    liveDateEl.style.setProperty('--date-hue', String(dayHue(displayDate)))
    headingLeft.appendChild(liveDateEl)
  }
  if (usingLatest) {
    headingLeft.appendChild(el('span', {
      class: 'today-updates-latest',
      title: 'No changes today - showing the most recent updates',
    }, ['Latest']))
  }
  headingRow.appendChild(headingLeft)

  const headingRight = el('div', { class: 'today-updates-heading-right' })
  const hideDev = isHideDev()
  const hideDevBtn = el('button', {
    class: `today-hide-dev-btn${hideDev ? ' active' : ''}`,
    type: 'button',
    title: 'Hide dev-channel changes',
    'aria-pressed': String(hideDev),
  }, [hideDev ? 'Dev hidden' : 'Dev'])
  headingRight.appendChild(hideDevBtn)

  const toggleBtn = el('button', {
    class: 'today-updates-toggle',
    'aria-label': 'Toggle what\'s new',
    'aria-expanded': String(!isCollapsed),
    'aria-controls': 'today-updates-body',
  })
  toggleBtn.innerHTML = `<span class="today-updates-toggle-icon">${CHEVRON_DOWN}</span>`
  headingRight.appendChild(toggleBtn)
  headingRow.appendChild(headingRight)
  section.appendChild(headingRow)
  section.appendChild(renderColorGame())

  const body = el('div', { class: 'today-updates-body' })

  if (affectedBundles && isHideDev()) {
    affectedBundles = affectedBundles.filter((b) => b.channel !== 'dev')
  }

  const grouped = affectedBundles && affectedBundles.length > 0 ? groupAffectedBundles(affectedBundles) : {}
  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}

  hideDevBtn.addEventListener('click', () => {
    const next = !isHideDev()
    if (next) localStorage.setItem(HIDE_DEV_KEY, 'true')
    else localStorage.removeItem(HIDE_DEV_KEY)
    const parent = section.parentElement
    const fresh = renderTodayUpdates()
    if (parent) parent.replaceChild(fresh, section)
  })

  // Summary peek row — visible when collapsed, hidden when expanded
  const summaryEl = el('div', { class: 'today-updates-summary', 'aria-hidden': 'true' })
  if (affectedBundles && affectedBundles.length > 0) {
    const entries = Object.values(grouped).filter((e) => e.badge_type !== 'REMOVED BUNDLE')
    const newBundleCount = entries.filter((e) => e.badge_type === 'NEW BUNDLE').length
    const updatedBundleCount = entries.filter((e) => e.badge_type === 'UPDATED').length
    const totalApps = entries.reduce((sum, e) => sum + (e.apps?.filter((a) => a.badge_type !== 'REMOVED APP').length || 0), 0)

    const parts = []
    if (newBundleCount > 0) parts.push(`${newBundleCount} new bundle${newBundleCount !== 1 ? 's' : ''}`)
    if (updatedBundleCount > 0) parts.push(`${updatedBundleCount} updated`)
    if (totalApps > 0) parts.push(`${totalApps} app${totalApps !== 1 ? 's' : ''}`)

    const summaryLeft = el('div', { class: 'today-updates-summary-left' })
    summaryLeft.innerHTML = `<span class="today-updates-summary-text">${parts.join(' &middot; ')}</span>`

    const summaryRight = el('div', { class: 'today-updates-summary-right' })
    const totalCount = newBundleCount + updatedBundleCount
    summaryRight.innerHTML = `<span class="today-updates-summary-count">${totalCount}</span><span class="today-updates-summary-chevron">${CHEVRON_DOWN}</span>`

    summaryEl.appendChild(summaryLeft)
    summaryEl.appendChild(summaryRight)
  } else {
    summaryEl.innerHTML = '<span class="today-updates-summary-text today-updates-summary-text--empty">No updates detected yet today.</span>'
  }
  section.appendChild(summaryEl)
  summaryEl.addEventListener('click', () => {
    if (section.classList.contains('collapsed')) {
      toggleBtn.click()
    }
  })

  if (!affectedBundles || affectedBundles.length === 0) {
    applyCollapsed(section, summaryEl, isCollapsed)
    setupToggle(section, summaryEl, toggleBtn)
    return section
  }

  const sortedEntries = Object.entries(grouped)
    .filter(([, entry]) => entry.badge_type !== 'REMOVED BUNDLE')
    .sort(([, a], [, b]) => {
    const aPriority = HIERARCHY_ORDER[a.badge_type] ?? 99
    const bPriority = HIERARCHY_ORDER[b.badge_type] ?? 99
    if (aPriority !== bPriority) return aPriority - bPriority

    const aNewApps = a.apps.some((app) => app.badge_type === 'NEW APP')
    const bNewApps = b.apps.some((app) => app.badge_type === 'NEW APP')
    if (aNewApps && !bNewApps) return -1
    if (!aNewApps && bNewApps) return 1

    return 0
  })

  function buildBundleEl(bundleName, entry) {
    const bundleEl = el('div', { class: 'today-bundle-entry' })

    const headerParts = [
      el('span', { class: `badge badge--${(entry.badge_type || 'updated').toLowerCase().replace(/\s+/g, '-')}` }, [badgeLabel(entry.badge_type) || 'UPDATE']),
    ]

    const nameEl = el('span', {
      class: 'today-bundle-name',
      role: 'button',
      tabindex: '0',
      title: 'View bundle details',
    }, [entry.patches_name || bundleName])
    nameEl.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('open-bundle', {
        detail: { bundleName, channels: entry.channels },
      }))
    })
    nameEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('open-bundle', {
          detail: { bundleName, channels: entry.channels },
        }))
      }
    })
    headerParts.push(nameEl)
    headerParts.push(el('span', { class: 'bundle-author', dangerouslySetInnerHTML: getAuthorLink(entry.repo_url) }))

    const prevVer = shortVersion(entry.previous_version)
    const nextVer = shortVersion(entry.new_version)
    const hasVersionTag = prevVer && nextVer && prevVer !== nextVer
    if (hasVersionTag) {
      headerParts.push(el('span', { class: 'today-version-tag' }, [
        prevVer,
        el('span', { class: 'today-version-arrow', dangerouslySetInnerHTML: VERSION_ARROW }),
        nextVer,
      ]))
    } else if (nextVer) {
      headerParts.push(el('span', { class: 'today-version-tag' }, [nextVer]))
    }

    if (entry.extra_badges?.includes('VERSION BUMP') && !hasVersionTag && !entry.new_version) {
      headerParts.push(el('span', { class: 'badge badge--version-bump' }, ['VERSION BUMP']))
    }

    const header = el('div', { class: 'today-bundle-header' }, headerParts)
    bundleEl.appendChild(header)

    for (const app of entry.apps || []) {
      if (app.badge_type === 'REMOVED APP') continue

      const appEl = el('div', { class: 'today-app-entry' })

      const iconHtml = renderAppIcon(app, iconCache, 'sm')

      const badgeClass = app.badge_type ? `badge--${app.badge_type.toLowerCase().replace(/\s+/g, '-')}` : ''
      const badgeHtml = app.badge_type ? `<span class="badge ${badgeClass}">${escHtml(badgeLabel(app.badge_type))}</span>` : ''

      const appName = resolveAppName(app, nameCache)
      const appPrevVer = shortVersion(app.previous_version)
      const appNextVer = shortVersion(app.new_version)
      let versionHtml = ''
      if (appPrevVer && appNextVer && appPrevVer !== appNextVer) {
        versionHtml = `<span class="today-app-version-diff">${escHtml(appPrevVer)} ${VERSION_ARROW} ${escHtml(appNextVer)}</span>`
      } else if (appNextVer) {
        versionHtml = `<span class="today-app-version-diff">${escHtml(appNextVer)}</span>`
      }

      appEl.innerHTML = `
        <div class="today-app-main">
          ${badgeHtml}
          ${iconHtml}
          <span class="today-app-name">${escHtml(appName)}</span>
          ${versionHtml}
        </div>
      `

      appEl.style.cursor = 'pointer'
      appEl.setAttribute('role', 'button')
      appEl.setAttribute('tabindex', '0')
      appEl.addEventListener('click', () => {
        window.dispatchEvent(new CustomEvent('open-app', {
          detail: { app, bundleName, channels: entry.channels },
        }))
      })
      appEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          window.dispatchEvent(new CustomEvent('open-app', {
            detail: { app, bundleName, channels: entry.channels },
          }))
        }
      })

      bundleEl.appendChild(appEl)
    }

    return bundleEl
  }

  // Split into left (new bundles + updated with new apps) and right (updated with only updated apps)
  const leftEntries = []
  const rightEntries = []

  for (const [bundleName, entry] of sortedEntries) {
    const isNewBundle = entry.badge_type === 'NEW BUNDLE'
    const hasNewApps = entry.apps?.some((app) => app.badge_type === 'NEW APP')
    if (isNewBundle || hasNewApps) {
      leftEntries.push([bundleName, entry])
    } else {
      rightEntries.push([bundleName, entry])
    }
  }

  if (rightEntries.length > 0 && leftEntries.length > 0) {
    const leftCol = el('div', { class: 'today-column today-column--left' })
    const rightCol = el('div', { class: 'today-column today-column--right' })

    for (const [bName, bEntry] of leftEntries) {
      leftCol.appendChild(buildBundleEl(bName, bEntry))
    }
    for (const [bName, bEntry] of rightEntries) {
      rightCol.appendChild(buildBundleEl(bName, bEntry))
    }

    body.appendChild(leftCol)
    body.appendChild(rightCol)
  } else {
    for (const [bName, bEntry] of sortedEntries) {
      body.appendChild(buildBundleEl(bName, bEntry))
    }
  }

  const bodyWrap = el('div', { class: 'today-updates-body-wrap', id: 'today-updates-body' })
  bodyWrap.appendChild(body)
  section.appendChild(bodyWrap)
  applyCollapsed(section, summaryEl, isCollapsed)
  setupToggle(section, summaryEl, toggleBtn)

  return section
}

function applyCollapsed(section, summaryEl, isCollapsed) {
  // Class-only: CSS animates grid-template-rows, so nothing re-measures
  // scrollHeight and rapid clicks simply retarget the running transition.
  section.classList.toggle('collapsed', isCollapsed)
  summaryEl.classList.toggle('today-updates-summary--hidden', !isCollapsed)
}

function setupToggle(section, summaryEl, toggleBtn) {
  toggleBtn.addEventListener('click', () => {
    const isNowCollapsed = !section.classList.contains('collapsed')
    applyCollapsed(section, summaryEl, isNowCollapsed)
    localStorage.setItem(STORAGE_KEY, String(isNowCollapsed))
    toggleBtn.setAttribute('aria-expanded', String(!isNowCollapsed))
  })
}
