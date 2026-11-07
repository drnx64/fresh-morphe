/**
 * BundleHistoryModal — shows version history with bundle avatar and app icons.
 */
import { el } from '../ui.js'
import { openModal } from './modal.js'
import * as store from '../store.js'
import { renderAppIcon, getDisplayAvatar, getDisplayBundleImage, avatarStackHtml, resolveAppName } from '../utils/misc.js'
import { escHtml } from '../utils/html.js'
import { formatVersion } from '../utils/format.js'
import { VERSION_ARROW } from '../utils/svg.js'
import { loadReleases } from '../services/releases.js'
import { releaseDateLabel, releaseNotesHtml } from './releaseNotes.js'

export async function openBundleHistoryModal(bundleName, bundleNames = []) {
  if (!bundleName) return

  const changelog = store.get('changelog') || []
  const bundles = store.get('bundles') || {}
  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}
  const names = bundleNames.length ? bundleNames : [bundleName]

  const bundleData = names
    .flatMap((name) => [
      bundles[`${name}:stable`],
      bundles[`${name}:latest`],
      bundles[`${name}:dev`],
    ])
    .find(Boolean)
  const displayName = bundleData?.patches_name || bundleName
  const avatarUrl = getDisplayAvatar(bundleData?.repo_url || '', bundleData?.avatarUrl || '')
  const bundleImageUrl = getDisplayBundleImage(bundleData?.repo_url || '', bundleData?.bundleImageUrl || '')

  const entries = []
  for (const day of changelog) {
    if (!day.affected_bundles) continue
    const matching = day.affected_bundles.filter((b) => names.includes(b.bundle))
    if (matching.length > 0) {
      entries.push({ date: day.date, lastChecked: day.lastChecked, bundles: matching })
    }
  }
  entries.sort((a, b) => (b.date || '').localeCompare(a.date || ''))

  const releaseChannels = ['stable', 'latest', 'dev'].filter((channel) =>
    names.some((name) => bundles[`${name}:${channel}`]),
  )
  const releases = await loadReleases(bundleName, releaseChannels)

  const content = el('div', { class: 'bundle-history-content' })

  if (entries.length === 0 && releases.length === 0) {
    content.innerHTML = `<div class="empty-state">No version history available for ${escHtml(displayName)}.</div>`
  } else {
    const headerRow = el('div', { class: 'bundle-history-header' })
    headerRow.innerHTML = `
      <div class="bundle-history-avatar-area">
        ${avatarStackHtml(
          bundleImageUrl,
          avatarUrl,
          `<span>${displayName.charAt(0).toUpperCase()}</span>`,
          'bundle-history-avatar',
          'bundle-history-avatar-placeholder',
        )}
      </div>
      <div class="bundle-history-header-info">
        <span class="bundle-history-label">History</span>
        ${bundleData?.version ? `<span class="bundle-history-version">${formatVersion(bundleData.version)}</span>` : ''}
      </div>
    `
    content.appendChild(headerRow)

    if (releases.length) {
      const releasesSection = el('div', { class: 'bundle-history-releases' }, [
        el('div', { class: 'bundle-history-section-title' }, ['Releases']),
      ])
      for (const release of releases) {
        const item = el('details', { class: 'bundle-history-release' })
        const summary = el('summary', { class: 'bundle-history-release-summary' })
        summary.innerHTML = `
          <span class="bundle-history-release-tag">${escHtml(release.tag || release.name || 'release')}</span>
          <span class="bundle-history-release-name">${escHtml(release.name || '')}</span>
          <span class="bundle-history-release-date">${escHtml(releaseDateLabel(release.dateReleased))}</span>
        `
        const body = el('div', { class: 'release-notes' })
        body.innerHTML = releaseNotesHtml(release.body)
        item.append(summary, body)
        releasesSection.appendChild(item)
      }
      content.appendChild(releasesSection)
    }

    const timeline = el('div', { class: 'bundle-history-timeline' })
    for (const entry of entries) {
      const dayEl = el('div', { class: 'bundle-history-day' })
      const dateObj = entry.date ? new Date(entry.date + 'T00:00:00Z') : null
      const dateStr = dateObj
        ? dateObj.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
        : 'Unknown date'

      const dayHeader = el('div', { class: 'bundle-history-day-header' }, [
        el('span', { class: 'bundle-history-date' }, [dateStr]),
      ])
      if (entry.lastChecked) {
        const checkedDate = new Date(entry.lastChecked)
        const timeStr = checkedDate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })
        dayHeader.appendChild(el('span', { class: 'bundle-history-time' }, [timeStr + ' UTC']))
      }
      dayEl.appendChild(dayHeader)

      for (const affected of entry.bundles) {
        const bundleRow = el('div', { class: 'bundle-history-entry' })
        const badgeType = affected.badge_type || 'UPDATED'
        const badgeClass = `badge--${badgeType.toLowerCase().replace(/\s+/g, '-')}`
        bundleRow.appendChild(el('span', { class: `badge ${badgeClass}` }, [badgeType]))

        if (affected.channel) {
          bundleRow.appendChild(el('span', { class: `channel-badge ${affected.channel}` }, [affected.channel]))
        }

        if (affected.previous_version && affected.new_version) {
          bundleRow.appendChild(el('span', { class: 'bundle-history-version-change' }, [
            formatVersion(affected.previous_version),
            el('span', { class: 'bundle-history-arrow', dangerouslySetInnerHTML: VERSION_ARROW }),
            formatVersion(affected.new_version),
          ]))
        } else if (affected.new_version) {
          bundleRow.appendChild(el('span', { class: 'bundle-history-version-change' }, [formatVersion(affected.new_version)]))
        }

        if (affected.apps?.length > 0) {
          const appsList = el('div', { class: 'bundle-history-apps' })
          for (const app of affected.apps) {
            const appBadge = app.badge_type
              ? `<span class="badge badge--${app.badge_type.toLowerCase().replace(/\s+/g, '-')}">${escHtml(app.badge_type)}</span>`
              : ''
            const iconHtml = renderAppIcon(app, iconCache, 'sm')
            const appRow = el('div', { class: 'bundle-history-app' })
            appRow.innerHTML = `${appBadge} ${iconHtml} <span>${escHtml(resolveAppName(app, nameCache))}</span>`
            appsList.appendChild(appRow)
          }
          bundleRow.appendChild(appsList)
        }

        dayEl.appendChild(bundleRow)
      }
      timeline.appendChild(dayEl)
    }
    content.appendChild(timeline)
  }

  openModal({
    title: `${displayName} — History`,
    content,
    className: 'bundle-history-modal',
    maxWidth: 600,
  })
}
