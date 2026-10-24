/**
 * Bundle card — horizontal row layout, expandable apps list.
 */
import { el } from '../ui.js'
import * as store from '../store.js'
import { getStaleness, getStoredVersions, getDisplayAvatar, getDisplayBundleImage, avatarStackHtml, groupBundleCards } from '../utils/misc.js'
import { mountAppRows, sortAppsByName } from '../utils/appList.js'
import { ensureBundleChannels, isBundlePartial } from '../services/bundleLoader.js'
import { getRepoInfo, getAddMorpheUrl, getAuthorLink } from '../utils/url.js'
import { makeBundleKey } from '../utils/bundleKey.js'
import { escHtml } from '../utils/html.js'
import { formatVersion } from '../utils/format.js'
import { GITHUB_SVG, GITLAB_SVG, HISTORY_ICON, CHEVRON_DOWN } from '../utils/svg.js'

export function renderBundleCard(bundle) {
  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}

  const repoInfo = getRepoInfo(bundle.repo_url)
  const addMorpheUrl = getAddMorpheUrl(bundle.repo_url)
  const iconSvg = repoInfo.isGitLab ? GITLAB_SVG : GITHUB_SVG

  const apps = sortAppsByName(bundle.apps || [], nameCache)
  // While bundle files stream in, cards render from the index first — use its app_count
  const count = Math.max(apps.length, bundle.appCount || 0)

  let updatedBadge = ''
  if (bundle.version) {
    const todayStr = new Date().toISOString().split('T')[0]
    const liveDataDate = store.get('liveDataDate') || ''
    if (liveDataDate === todayStr) {
      const stored = getStoredVersions()
      const prev = stored[bundle.bundle]
      if (prev && prev !== bundle.version) {
        updatedBadge = '<span class="bundle-updated-badge">Updated</span>'
      }
    }
  }

  const versionTag = bundle.version
    ? `<span class="bundle-version-tag">${escHtml(formatVersion(bundle.version))}</span>`
    : ''

  const starsHtml = bundle.stars
    ? `<span class="bundle-stars-badge" title="${bundle.stars} stars">★ ${bundle.stars >= 1000 ? (bundle.stars / 1000).toFixed(1) + 'k' : bundle.stars}</span>`
    : ''

  const archivedHtml = bundle.isArchived
    ? '<span class="bundle-archived-badge" title="This repository is archived">Archived</span>'
    : ''

  const preReleaseHtml = bundle.isPreRelease
    ? '<span class="bundle-pre-release-badge" title="Pre-release version">Pre-release</span>'
    : ''

  const releaseDate =
    store.get('bundles')?.[`${bundle.bundle}:stable`]?.release_date ||
    store.get('bundles')?.[`${bundle.bundle}:latest`]?.release_date ||
    store.get('bundles')?.[`${bundle.bundle}:dev`]?.release_date || ''
  const staleness = getStaleness(releaseDate)
  const stalenessHtml = staleness
    ? `<span class="staleness-badge staleness--${staleness.level}" title="Released ${releaseDate}">${staleness.label}</span>`
    : ''

  const channelBadges = bundle.channels.map((ch) =>
    `<span class="channel-badge ${ch}">${ch}</span>`
  ).join('')

  const avatarUrl = getDisplayAvatar(bundle.repo_url, bundle.avatarUrl)
  const bundleImageUrl = getDisplayBundleImage(bundle.repo_url, bundle.bundleImageUrl)
  const isNewBundle = bundle.badge_type === 'NEW BUNDLE'

  const card = el('div', {
    class: `bundle-card bundle-card--row${isNewBundle ? ' bundle-card--new' : ''}`,
    'data-bundle-name': bundle.bundle,
    'data-badge-type': bundle.badge_type || '',
  })

  const row = el('div', { class: 'bundle-card-row', role: 'button', tabindex: '0' })
  row.innerHTML = `
    <div class="bundle-card-avatar-area">
      ${avatarStackHtml(
        bundleImageUrl,
        avatarUrl,
        `<span>${(bundle.patches_name || bundle.bundle).charAt(0).toUpperCase()}</span>`,
        'bundle-card-avatar',
        'bundle-card-avatar-placeholder',
      )}
    </div>
    <div class="bundle-card-row-info">
      <div class="bundle-card-row-top">
        <span class="bundle-card-row-name" title="${escHtml(bundle.patches_name || bundle.bundle)}">${escHtml(bundle.patches_name || bundle.bundle)}</span>
        ${updatedBadge}
      </div>
      <div class="bundle-card-row-bottom">
        <span class="bundle-author">${getAuthorLink(bundle.repo_url)}</span>
        <div class="bundle-card-row-badges">
          <div class="channel-badges-group">${channelBadges}</div>
          ${versionTag}
          ${starsHtml}
          ${stalenessHtml}
        </div>
      </div>
    </div>
    <div class="bundle-card-row-meta">
      <span class="bundle-card-row-count">${count} app${count !== 1 ? 's' : ''}</span>
      <div class="bundle-card-row-actions">
        <a href="${escHtml(bundle.repo_url)}" class="bundle-card-row-icon-link" target="_blank" rel="noopener" title="View Source Repository" onclick="event.stopPropagation()">${iconSvg}</a>
        <button class="bundle-card-row-icon-link history-btn" data-bundle="${escHtml(bundle.bundle)}" title="View changelog history" onclick="event.stopPropagation()">${HISTORY_ICON}</button>
      </div>
      <span class="bundle-card-row-chevron">${CHEVRON_DOWN}</span>
    </div>
  `

  const toggle = () => toggleExpand(card, bundle, nameCache, iconCache)
    .catch((err) => console.error('[bundleCard] expand failed:', err))
  row.addEventListener('click', toggle)
  row.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      toggle()
    }
  })

  const historyBtn = row.querySelector('.history-btn')
  historyBtn.addEventListener('click', (e) => {
    e.stopPropagation()
    window.dispatchEvent(new CustomEvent('open-bundle-history', { detail: { bundleName: bundle.bundle } }))
  })

  card.appendChild(row)

  return card
}

/**
 * Expand/collapse a card's app rows. Patch counts live in the per-bundle
 * files, so the card's channels are pulled in on first open (one round trip,
 * single-flighted, usually already warm) and the rows are re-grouped from the
 * store afterwards.
 */
async function toggleExpand(card, bundle, nameCache, iconCache) {
  card.classList.toggle('expanded')

  let appsContainer = card.querySelector('.bundle-card-apps')
  if (!appsContainer) {
    appsContainer = el('div', { class: 'bundle-card-apps' })
    card.appendChild(appsContainer)

    const missing = bundle.channels.filter((ch) => isBundlePartial(makeBundleKey(bundle.bundle, ch)))
    if (missing.length) {
      appsContainer.innerHTML = '<div class="bundle-card-apps-loading">Loading apps…</div>'
      await ensureBundleChannels(bundle.bundle, bundle.channels)
    }

    const fresh = groupBundleCards(store.get('bundles') || {}, (b) =>
      b.bundle === bundle.bundle && bundle.channels.includes(b.channel))[bundle.bundle]
    const apps = sortAppsByName(fresh?.apps || [], nameCache)

    appsContainer.replaceChildren()
    mountAppRows(appsContainer, apps, {
      prefix: 'bundle-card-app',
      nameCache,
      iconCache,
      onBatch: () => {
        if (card.classList.contains('expanded')) {
          appsContainer.style.maxHeight = appsContainer.scrollHeight + 'px'
        }
      },
      onOpen: (app) => {
        window.dispatchEvent(new CustomEvent('open-app', {
          detail: {
            app: { package: app.package, app_name: app.app_name, patches: app.patches },
            bundleName: '',
            channels: [],
          },
        }))
      },
    })
  }

  // Rows land on the next frame; onBatch keeps the height in sync while open
  appsContainer.style.maxHeight = card.classList.contains('expanded')
    ? `${appsContainer.scrollHeight}px`
    : '0'
}
