/**
 * BundleModal — shows bundle details: apps list, description, metadata.
 * Triggered by clicking a bundle card.
 *
 * Paints from the index entry immediately (name, version, stars, apps) and
 * fills in what only the per-bundle file has — the repo description and each
 * app's patch count — once it lands. Opening the modal used to wait for every
 * channel of the bundle first, which on a cold cache meant a dead click.
 */
import { el } from '../ui.js'
import { openModal } from './modal.js'
import * as store from '../store.js'
import { getDisplayAvatar, getDisplayBundleImage, avatarStackHtml } from '../utils/misc.js'
import { mountAppRows, sortAppsByName } from '../utils/appList.js'
import { getAddMorpheUrl, getAuthorLink, getRepoInfo } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { formatVersion } from '../utils/format.js'
import { ensureBundleChannels } from '../services/bundleLoader.js'
import { GITHUB_SVG, GITLAB_SVG } from '../utils/svg.js'

/**
 * Open the bundle detail modal.
 * @param {Object} options
 * @param {string} options.bundleName
 * @param {string[]} options.channels
 */
export function openBundleModal({ bundleName, channels = [] }) {
  if (!bundleName) return

  const bundles = store.get('bundles') || {}
  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}

  const bundleData =
    bundles[`${bundleName}:stable`] ||
    bundles[`${bundleName}:latest`] ||
    bundles[`${bundleName}:dev`]
  if (!bundleData) return

  const displayName = bundleData.patches_name || bundleName
  const repoInfo = getRepoInfo(bundleData.repo_url)
  const addMorpheUrl = getAddMorpheUrl(bundleData.repo_url)
  const authorHtml = getAuthorLink(bundleData.repo_url)
  const iconSvg = repoInfo.isGitLab ? GITLAB_SVG : GITHUB_SVG

  const avatarUrl = getDisplayAvatar(bundleData.repo_url, bundleData.avatarUrl)
  const bundleImageUrl = getDisplayBundleImage(bundleData.repo_url, bundleData.bundleImageUrl)

  const content = el('div', { class: 'bundle-modal-content' })

  const headerEl = el('div', { class: 'bundle-modal-header' })
  headerEl.innerHTML = `
    <div class="bundle-modal-avatar-area">
      ${avatarStackHtml(
        bundleImageUrl,
        avatarUrl,
        `<span>${displayName.charAt(0).toUpperCase()}</span>`,
        'bundle-modal-avatar',
        'bundle-modal-avatar-placeholder',
      )}
    </div>
    <div class="bundle-modal-info">
      <h3 class="bundle-modal-name">${escHtml(displayName)}</h3>
      <span class="bundle-modal-author">${authorHtml}</span>
      <div class="bundle-modal-meta">
        ${bundleData.version ? `<span class="bundle-modal-version">${escHtml(formatVersion(bundleData.version))}</span>` : ''}
        ${(bundleData.channels || channels).map((ch) => `<span class="channel-badge ${ch}">${ch}</span>`).join('')}
        ${bundleData.stars ? `<span class="bundle-stars-badge" title="${bundleData.stars} stars">★ ${bundleData.stars}</span>` : ''}
        ${bundleData.isArchived ? '<span class="bundle-archived-badge">Archived</span>' : ''}
      </div>
    </div>
  `
  // Phones: the identity block rides in the sticky modal header instead of
  // scrolling away inside the body (and the duplicate text title is dropped).
  const isPhone = window.matchMedia('(max-width: 639px)').matches
  if (!isPhone) content.appendChild(headerEl)

  if (bundleData.repoDescription) {
    const descEl = el('div', { class: 'bundle-modal-description' }, [bundleData.repoDescription])
    content.appendChild(descEl)
  }

  const apps = sortAppsByName(bundleData.apps || [], nameCache)
  const partial = !!bundleData._partial

  const appsSection = el('div', { class: 'bundle-modal-apps-section' })
  appsSection.innerHTML = `<h4 class="bundle-modal-section-title">Apps (${apps.length})</h4>`
  const appsList = el('div', { class: 'bundle-modal-apps-list' })
  appsSection.appendChild(appsList)
  content.appendChild(appsSection)

  const actionsEl = el('div', { class: 'bundle-modal-actions' })
  actionsEl.innerHTML = `
    <a href="${escHtml(bundleData.repo_url)}" class="btn btn--secondary" target="_blank" rel="noopener">
      ${iconSvg} View Source
    </a>
    <a href="${escHtml(addMorpheUrl)}" class="btn btn--primary" target="_blank" rel="noopener">
      Add to Morphe
    </a>
  `
  content.appendChild(actionsEl)

  // Shell first — modal paints before rows are batched in
  openModal({
    title: displayName,
    content,
    className: 'bundle-modal',
    maxWidth: 600,
    headerContent: isPhone ? headerEl : null,
    hideTitle: isPhone,
  })

  // Patch counts for the rows, filled once the bundle file lands.
  const patchCounts = new Map()
  let countsSettled = !partial // full records already carry them

  /**
   * Paint every mounted row's patch count. Called after each row batch *and*
   * after the detail load, because either can finish first — the file is often
   * already warm (opened from the app modal) and settles before the first rAF
   * batch mounts a single row.
   */
  function syncPatchCounts() {
    if (!countsSettled && !patchCounts.size) return
    appsList.querySelectorAll('.bundle-modal-app-patches').forEach((span) => {
      const row = span.closest('.bundle-modal-app-row')
      const app = apps[Number(row?.dataset.idx)]
      const count = app?.package ? patchCounts.get(app.package) : undefined
      if (count === undefined) {
        // Still loading: leave the placeholder alone. Settled: the load failed
        // or the app is not in the file, so say so instead of spinning forever.
        if (!countsSettled) return
        span.textContent = '—'
        span.title = 'Patch count unavailable'
      } else {
        span.textContent = `${count} patch${count !== 1 ? 'es' : ''}`
      }
      span.classList.remove('is-pending')
    })
  }

  mountAppRows(appsList, apps, {
    prefix: 'bundle-modal-app',
    nameCache,
    iconCache,
    // Index entries list packages but carry no patches, so a count read now
    // would say "0 patches" for every row. Updated below once detail lands.
    countsKnown: !partial,
    onBatch: partial ? syncPatchCounts : undefined,
    onOpen: (app) => {
      window.dispatchEvent(new CustomEvent('open-app', {
        detail: { app, bundleName, channels: bundleData.channels || channels },
      }))
    },
  })

  if (partial) {
    fillMissingDetails({
      bundleName, channels, content, appsSection, appsList, patchCounts, syncPatchCounts,
      settle: () => { countsSettled = true; syncPatchCounts() },
    })
  }
}

/**
 * Load a bundle's detail after the modal is already on screen: repo description
 * (index entries do not carry it) and the per-app patch counts.
 * @param {Object} opts
 * @param {() => void} opts.settle - marks the load finished, for either outcome
 */
function fillMissingDetails({
  bundleName, channels, content, appsSection, appsList, patchCounts, syncPatchCounts, settle,
}) {
  ensureBundleChannels(bundleName, channels)
    .then(() => {
      if (!appsList.isConnected) return // modal closed while loading
      const record = store.get('bundles')?.[`${bundleName}:stable`]
        || store.get('bundles')?.[`${bundleName}:latest`]
        || store.get('bundles')?.[`${bundleName}:dev`]

      // The loader hands back the index-only record when the file fails, and an
      // index record's apps carry no patches — counting them would print
      // "0 patches" for every row, which is a lie the `finally` can't undo.
      if (!record || record._partial) return

      if (record.repoDescription && !content.querySelector('.bundle-modal-description')) {
        const descEl = el('div', { class: 'bundle-modal-description' }, [record.repoDescription])
        content.insertBefore(descEl, appsSection)
      }

      for (const app of record.apps || []) {
        if (app?.package) patchCounts.set(app.package, (app.patches || []).length)
      }
      // No-op when the file had no apps: the finally below still settles.
      syncPatchCounts()
    })
    .catch((err) => console.error('[bundleModal] detail fill failed:', err))
    // A failed load must not leave rows looking like they are still loading.
    .finally(settle)
}
