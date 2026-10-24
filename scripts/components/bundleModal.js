/**
 * BundleModal — shows bundle details: apps list, description, metadata.
 * Triggered by clicking a bundle card.
 */
import { el } from '../ui.js'
import { openModal } from './modal.js'
import * as store from '../store.js'
import { getDisplayAvatar, getDisplayBundleImage, avatarStackHtml } from '../utils/misc.js'
import { mountAppRows, sortAppsByName } from '../utils/appList.js'
import { getAddMorpheUrl, getAuthorLink, getRepoInfo } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { formatVersion } from '../utils/format.js'
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

  mountAppRows(appsList, apps, {
    prefix: 'bundle-modal-app',
    nameCache,
    iconCache,
    onOpen: (app) => {
      window.dispatchEvent(new CustomEvent('open-app', {
        detail: { app, bundleName, channels: bundleData.channels || channels },
      }))
    },
  })
}
