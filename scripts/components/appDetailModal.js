/**
 * AppDetailModal — tabbed layout: Patches | Changes.
 * Floating close button, hero section. Scoped to the single bundle the app was
 * opened from (a random supporting bundle when opened from the app list).
 */
import { el } from '../ui.js'
import { openModal, closeModal } from './modal.js'
import * as store from '../store.js'
import { resolveAppName, getAppIconUrl, copyToClipboard } from '../utils/misc.js'
import { getPlayStoreUrl } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { wordDiffHtml } from '../utils/wordDiff.js'
import { parseBundleKey, findBundleRecord } from '../utils/bundleKey.js'
import { CLOSE_ICON } from '../utils/svg.js'

const PLAY_STORE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" d="M.859 11.981V1.741c0-.672.79-1.098 1.434-.771L12.37 6.09c.662.336.662 1.207 0 1.543l-10.077 5.12c-.644.327-1.434-.099-1.434-.772M9.23 9.23l-8.1-8.101m8.1 3.364l-8.1 8.1"/></svg>'

export function openAppDetailModal({ app, bundleName, channels = [], patchName = null }) {
  if (!app) return
  if (app.badge_type === 'REMOVED APP') return

  const bundles = store.get('bundles') || {}
  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}

  const pkg = app.package
  const appName = resolveAppName(app, nameCache)
  const iconUrl = getAppIconUrl({ package: pkg }, iconCache)
  const hideDev = localStorage.getItem('fresh_hide_dev') === 'true'

  // One bundle only: the source the app was opened from, or a random bundle
  // that supports it when opened from the directory. Never list every bundle.
  const candidates = []
  for (const [key, bundle] of Object.entries(bundles)) {
    const { name: bName, channel } = parseBundleKey(key)
    if (hideDev && channel === 'dev') continue
    if (!bundle.apps?.some((a) => a.package === pkg)) continue
    const existing = candidates.find((b) => b.bundleName === bName)
    if (existing) {
      if (!existing.channels.includes(channel)) existing.channels.push(channel)
    } else {
      candidates.push({
        bundleName: bName,
        patchesName: bundle.patches_name || bName,
        channels: [channel],
      })
    }
  }

  const sourceBundle = candidates.find((b) => b.bundleName === bundleName)
    || (candidates.length ? candidates[Math.floor(Math.random() * candidates.length)] : null)

  let patches = []
  if (sourceBundle) {
    const { record: bundleData } = findBundleRecord(
      bundles, sourceBundle.bundleName, sourceBundle.channels
    )
    if (bundleData) {
      const appData = bundleData.apps?.find((a) => a.package === pkg)
      if (appData) patches = appData.patches || []
    }
  }

  // Check if there are changes
  const hasChanges = !!(app.patch_diff && (
    (app.patch_diff.patches_added || []).length > 0 ||
    (app.patch_diff.patches_removed || []).length > 0 ||
    (app.patch_diff.patches_modified || []).length > 0
  ))

  const content = el('div', { class: 'app-detail-content' })

  // Floating close button
  const closeBtn = el('button', {
    class: 'app-detail-close-btn',
    'aria-label': 'Close modal',
    dangerouslySetInnerHTML: CLOSE_ICON,
  })
  closeBtn.addEventListener('click', closeModal)
  content.appendChild(closeBtn)

  // Hero section
  const iconHtml = iconUrl
    ? `<img class="app-detail-icon" src="${escHtml(iconUrl)}" alt="" loading="lazy" onerror="this.style.display='none'">`
    : `<div class="app-detail-icon app-detail-icon--fallback">${appName.charAt(0).toUpperCase()}</div>`

  // Attribution: only the bundle this app was opened from.
  const sourceHtml = sourceBundle
    ? ` <span class="app-detail-source">by <a href="#/bundle/${encodeURIComponent(sourceBundle.bundleName)}" title="Open bundle page">${escHtml(sourceBundle.patchesName)}</a></span>`
    : ''

  const heroEl = el('div', { class: 'app-detail-hero' })
  heroEl.innerHTML = `
    ${iconHtml}
    <div class="app-detail-hero-info">
      <h3 class="app-detail-name">${escHtml(appName)}${sourceHtml}</h3>
      <span class="app-detail-pkg">${escHtml(pkg)}</span>
      <a class="app-detail-playstore" href="${escHtml(getPlayStoreUrl(pkg))}" target="_blank" rel="noopener">
        ${PLAY_STORE_SVG} View on Google Play
      </a>
    </div>
  `
  content.appendChild(heroEl)

  heroEl.querySelectorAll('.app-detail-source a').forEach((a) => {
    a.addEventListener('click', () => closeModal())
  })

  // Copy package name on click
  const pkgEl = heroEl.querySelector('.app-detail-pkg')
  if (pkgEl) {
    pkgEl.classList.add('copyable')
    pkgEl.setAttribute('title', 'Click to copy package name')
    pkgEl.addEventListener('click', (e) => {
      e.stopPropagation()
      copyToClipboard(pkg, pkgEl)
    })
  }

  // Tabs — always scoped to one bundle, so Patches comes first.
  const tabNames = ['Patches']
  if (hasChanges) tabNames.push('Changes')
  const initialTab = tabNames[0]

  const tabsEl = el('div', { class: 'modal-tabs' })
  const tabContents = {}

  for (const tabName of tabNames) {
    const tabBtn = el('button', { class: `modal-tab${tabName === initialTab ? ' active' : ''}` }, [tabName])
    tabsEl.appendChild(tabBtn)

    const tabContent = el('div', { class: `modal-tab-content${tabName === initialTab ? ' active' : ''}` })
    tabContents[tabName] = tabContent

    tabBtn.addEventListener('click', () => {
      tabsEl.querySelectorAll('.modal-tab').forEach((t) => t.classList.remove('active'))
      Object.values(tabContents).forEach((c) => c.classList.remove('active'))
      tabBtn.classList.add('active')
      tabContent.classList.add('active')
    })
  }

  // ── Patches tab ──────────────────────────────────────
  const patchesTab = tabContents['Patches']
  const patchesList = el('div', { class: 'app-detail-patches' })

  function renderPatchList(list) {
    patchesList.replaceChildren()
    if (list.length === 0) {
      patchesList.appendChild(el('div', { class: 'app-detail-patch-empty' }, ['No patch data available.']))
      return
    }
    const sorted = [...list].sort((a, b) => a.name.localeCompare(b.name))
    for (const patch of sorted) {
      const patchEl = el('div', { class: 'app-detail-patch' })
      const defaultBadge = patch.use ? '<span class="badge badge--default-on">ON</span>' : ''
      const versionsHtml = patch.compatible_versions?.length
        ? `<span class="app-detail-patch-versions">${escHtml(patch.compatible_versions.join(', '))}</span>`
        : '<span class="app-detail-patch-versions app-detail-patch-versions--any">Any version</span>'
      patchEl.innerHTML = `
        <div class="app-detail-patch-header">
          <span class="app-detail-patch-name">${escHtml(patch.name)}</span>
          ${defaultBadge}
        </div>
        ${patch.description ? `<p class="app-detail-patch-desc">${escHtml(patch.description)}</p>` : ''}
        <div class="app-detail-patch-meta">${versionsHtml}</div>
      `
      patchesList.appendChild(patchEl)
    }
  }

  if (sourceBundle) {
    renderPatchList(patches)
    patchesTab.appendChild(patchesList)

    // Deep-link: highlight the target patch
    if (patchName) {
      const target = [...patchesList.querySelectorAll('.app-detail-patch')].find(
        (n) => n.querySelector('.app-detail-patch-name')?.textContent === patchName
      )
      if (target) {
        target.classList.add('app-detail-patch--highlight')
        target.scrollIntoView({ block: 'center', behavior: 'smooth' })
        setTimeout(() => target.classList.remove('app-detail-patch--highlight'), 2500)
      }
    }
  } else {
    patchesTab.innerHTML = '<div class="empty-state">No patch information available.</div>'
  }

  // ── Changes tab ──────────────────────────────────────
  if (hasChanges) {
    const changesTab = tabContents['Changes']
    const diffList = el('div', { class: 'app-detail-diff' })
    for (const added of app.patch_diff.patches_added || []) {
      const row = el('div', { class: 'app-detail-diff-row app-detail-diff-row--added' })
      row.innerHTML = `<span class="app-detail-diff-icon">+</span><span>${escHtml(added.name)}</span>`
      diffList.appendChild(row)
    }
    for (const removed of app.patch_diff.patches_removed || []) {
      const row = el('div', { class: 'app-detail-diff-row app-detail-diff-row--removed' })
      row.innerHTML = `<span class="app-detail-diff-icon">−</span><span>${escHtml(removed.name)}</span>`
      diffList.appendChild(row)
    }
    for (const modified of app.patch_diff.patches_modified || []) {
      const row = el('div', { class: 'app-detail-diff-row app-detail-diff-row--modified' })
      const changes = (modified.changes || []).map((c) => escHtml(c)).join(', ')
      row.innerHTML = `<span class="app-detail-diff-icon">~</span><span>${escHtml(modified.name)}<span class="app-detail-diff-changes">${changes}</span></span>`
      diffList.appendChild(row)
      if (typeof modified.previous_description === 'string') {
        const desc = el('div', { class: 'app-detail-diff-desc' })
        desc.innerHTML = wordDiffHtml(modified.previous_description, modified.description || '', escHtml)
        diffList.appendChild(desc)
      }
    }
    changesTab.appendChild(diffList)
  }

  content.appendChild(tabsEl)
  for (const tabContent of Object.values(tabContents)) {
    content.appendChild(tabContent)
  }

  openModal({
    title: appName,
    content,
    className: 'app-detail-modal',
    maxWidth: 600,
    hideHeader: true,
  })
}
