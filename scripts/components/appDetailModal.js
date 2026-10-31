/**
 * AppDetailModal — tabbed layout: Patches | Changes.
 * Floating close button, hero section.
 *
 * The modal is built from the app directory alone and paints immediately: an
 * app like YouTube is listed by 111 bundle keys, and waiting for all of them
 * (overnight, `ensureAppBundles`) before showing anything was the reason this
 * modal felt slow. Patches are fetched for the *selected* bundle only, on
 * demand, behind a skeleton.
 *
 * A row of bundle chips under the hero shows every bundle that supports the
 * app. It replaces the old random pick, which made the same app look different
 * on every open and offered no way to see the other providers.
 */
import { el } from '../ui.js'
import { openModal, closeModal } from './modal.js'
import * as store from '../store.js'
import {
  resolveAppName, getAppIconUrl, copyToClipboard, getDisplayAvatar, avatarStackHtml,
} from '../utils/misc.js'
import { getPlayStoreUrl } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { parseBundleKey, makeBundleKey } from '../utils/bundleKey.js'
import { ensureBundleKey } from '../services/bundleLoader.js'
import { CLOSE_ICON, VERSION_ARROW } from '../utils/svg.js'

const PLAY_STORE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" d="M.859 11.981V1.741c0-.672.79-1.098 1.434-.771L12.37 6.09c.662.336.662 1.207 0 1.543l-10.077 5.12c-.644.327-1.434-.099-1.434-.772M9.23 9.23l-8.1-8.101m8.1 3.364l-8.1 8.1"/></svg>'

// Display order inside one bundle: stable is what people install, dev is the
// freshest. The directory's key order is alphabetical (dev, latest, stable), so
// relying on it would quietly show dev patches for every app.
const CHANNEL_DISPLAY_ORDER = ['stable', 'latest', 'dev']

/**
 * Every bundle that ships patches for `pkg`, de-duped by bundle name and
 * ordered deterministically: the bundle the user came from first, then
 * stable-first providers by display name.
 * @param {Object} bundles - store bundle map (`name:channel` → record)
 * @param {string} pkg
 * @param {boolean} hideDev
 * @param {string} [preferred] - bundle name the modal was opened from
 * @returns {Array<Object>}
 */
function bundleCandidates(bundles, pkg, hideDev, preferred = '') {
  const byName = new Map()
  for (const [key, bundle] of Object.entries(bundles)) {
    const { name, channel } = parseBundleKey(key)
    if (hideDev && channel === 'dev') continue
    const apps = bundle.apps || []
    const supports = apps.some((a) => (typeof a === 'string' ? a : a.package) === pkg)
    if (!supports) continue

    const existing = byName.get(name)
    if (existing) {
      existing.channels.push(channel)
      continue
    }
    byName.set(name, {
      bundleName: name,
      patchesName: bundle.patches_name || name,
      repoUrl: bundle.repo_url || '',
      avatarUrl: bundle.avatarUrl || '',
      channels: [channel],
    })
  }

  const list = [...byName.values()]
  for (const entry of list) {
    entry.channels.sort(
      (a, b) => CHANNEL_DISPLAY_ORDER.indexOf(a) - CHANNEL_DISPLAY_ORDER.indexOf(b)
    )
  }
  const stableFirst = (entry) => (entry.channels.includes('stable') ? 0 : 1)
  return list.sort((a, b) => {
    if (a.bundleName === preferred) return -1
    if (b.bundleName === preferred) return 1
    return stableFirst(a) - stableFirst(b) || a.patchesName.localeCompare(b.patchesName)
  })
}

export function openAppDetailModal({ app, bundleName, channels = [], patchName = null }) {
  if (!app) return
  if (app.badge_type === 'REMOVED APP') return

  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}

  const pkg = app.package
  const appName = resolveAppName(app, nameCache)
  const iconUrl = getAppIconUrl({ package: pkg }, iconCache)
  const hideDev = localStorage.getItem('fresh_hide_dev') === 'true'

  const candidates = bundleCandidates(store.get('bundles') || {}, pkg, hideDev, bundleName)
  let selected = candidates.find((c) => c.bundleName === bundleName) || candidates[0] || null

  // The caller's channel hint wins for the bundle it was opened from.
  const hintFor = (candidate) =>
    (candidate.bundleName === bundleName && channels.length ? channels : candidate.channels)

  const content = el('div', { class: 'app-detail-content' })

  // Floating close button
  const closeBtn = el('button', {
    class: 'app-detail-close-btn',
    'aria-label': 'Close modal',
    dangerouslySetInnerHTML: CLOSE_ICON,
  })
  closeBtn.addEventListener('click', closeModal)
  content.appendChild(closeBtn)

  // ── Hero ────────────────────────────────────────────
  const iconHtml = iconUrl
    ? `<img class="app-detail-icon" src="${escHtml(iconUrl)}" alt="" loading="lazy" onerror="this.style.display='none'">`
    : `<div class="app-detail-icon app-detail-icon--fallback">${appName.charAt(0).toUpperCase()}</div>`

  const heroEl = el('div', { class: 'app-detail-hero' })
  heroEl.innerHTML = `
    ${iconHtml}
    <div class="app-detail-hero-info">
      <h3 class="app-detail-name">${escHtml(appName)}<span class="app-detail-source"></span></h3>
      <span class="app-detail-pkg">${escHtml(pkg)}</span>
      <a class="app-detail-playstore" href="${escHtml(getPlayStoreUrl(pkg))}" target="_blank" rel="noopener">
        ${PLAY_STORE_SVG} View on Google Play
      </a>
    </div>
  `
  content.appendChild(heroEl)

  const sourceEl = heroEl.querySelector('.app-detail-source')

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

  // ── Bundle chips ────────────────────────────────────
  // One chip per bundle, avatar first. Horizontally scrollable: the busiest app
  // in the registry is listed by 111 bundle keys.
  const chipsEl = el('div', {
    class: 'app-detail-bundles',
    role: 'group',
    'aria-label': 'Bundles that support this app',
  })
  const chipsGroupEl = el('div', { class: 'app-detail-bundles-group' })
  if (candidates.length > 1) {
    chipsGroupEl.appendChild(el('div', { class: 'app-detail-bundles-label' }, [
      `Bundles (${candidates.length})`,
    ]))
  }
  chipsGroupEl.appendChild(chipsEl)
  content.appendChild(chipsGroupEl)

  const chipEls = new Map()
  for (const candidate of candidates) {
    const avatarUrl = getDisplayAvatar(candidate.repoUrl, candidate.avatarUrl)
    const chip = el('button', {
      class: 'app-detail-bundle-chip',
      type: 'button',
      'aria-pressed': 'false',
      title: candidate.patchesName,
    })
    chip.innerHTML = `
      <span class="app-detail-bundle-avatar">${
        avatarStackHtml(
          '',
          avatarUrl,
          `<span>${escHtml(candidate.patchesName.charAt(0).toUpperCase())}</span>`,
          'app-detail-bundle-avatar-img',
          'app-detail-bundle-avatar-ph',
        )
      }</span>
      <span class="app-detail-bundle-name">${escHtml(candidate.patchesName)}</span>
    `
    chip.addEventListener('click', () => selectBundle(candidate))
    chipsEl.appendChild(chip)
    chipEls.set(candidate.bundleName, chip)
  }

  // ── Tabs — Patches, plus Changes when the app has any ──
  const hasChanges = !!(app.patch_diff && (
    (app.patch_diff.patches_added || []).length > 0 ||
    (app.patch_diff.patches_removed || []).length > 0 ||
    (app.patch_diff.patches_modified || []).length > 0
  ))

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

  // ── Patches tab (+ channel compare) ──────────────────
  // One bundle can publish stable, latest and dev with different patch sets, so
  // a patch list alone is only half the story: the channel row switches it and
  // the delta line spells out what differs, without leaving the modal.
  const channelBar = el('div', { class: 'app-detail-channels', hidden: true })
  const channelList = el('div', {
    class: 'app-detail-channel-list',
    role: 'group',
    'aria-label': 'Channels for this bundle',
  })
  channelBar.appendChild(el('span', { class: 'app-detail-channels-label' }, ['Channels']))
  channelBar.appendChild(channelList)

  const deltaEl = el('div', { class: 'app-detail-channel-delta', hidden: true })

  const patchesTab = tabContents['Patches']
  const patchesList = el('div', { class: 'app-detail-patches' })
  patchesTab.appendChild(channelBar)
  patchesTab.appendChild(deltaEl)
  patchesTab.appendChild(patchesList)

  /** Patches of this app inside one bundle record (records are app objects or bare packages). */
  function appPatchesFrom(record) {
    const apps = record?.apps || []
    const appData = apps.find((a) => (typeof a === 'string' ? a : a.package) === pkg)
    return appData?.patches || []
  }

  // `name:channel` → patches. Per modal session: bundles do not change while it
  // is open, and a channel the user toggles back to must not refetch.
  const appPatchCache = new Map()

  /**
   * Load one channel's patches for this app, fetching the bundle file if the
   * store only holds its index row.
   * @param {string} bundleName
   * @param {string} channel
   * @returns {Promise<Array>}
   */
  async function patchesFor(bundleName, channel) {
    const key = makeBundleKey(bundleName, channel)
    if (appPatchCache.has(key)) return appPatchCache.get(key)
    let record = (store.get('bundles') || {})[key]
    if (record && record._partial) {
      await ensureBundleKey(key)
      record = (store.get('bundles') || {})[key]
    }
    const patches = appPatchesFrom(record)
    // A failed fetch leaves an index-only record; caching that would hide the
    // patches for the rest of the session.
    if (record && !record._partial) appPatchCache.set(key, patches)
    return patches
  }

  function renderPatchSkeleton() {
    patchesList.replaceChildren()
    for (let i = 0; i < 3; i++) {
      const row = el('div', { class: 'app-detail-patch app-detail-patch--skeleton' })
      row.innerHTML = `
        <div class="skeleton-line" style="width:${55 + i * 10}%"></div>
        <div class="skeleton-line" style="width:${80 - i * 8}%"></div>
      `
      patchesList.appendChild(row)
    }
    patchesList.setAttribute('aria-busy', 'true')
  }

  function renderPatchList(list, channel = '') {
    patchesList.removeAttribute('aria-busy')
    patchesList.replaceChildren()
    if (list.length === 0) {
      const text = channel
        ? `No patches for this app in the ${channel} channel.`
        : 'No patch data available.'
      patchesList.appendChild(el('div', { class: 'app-detail-patch-empty' }, [text]))
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

  function highlightPatch(name) {
    const target = [...patchesList.querySelectorAll('.app-detail-patch')].find(
      (n) => n.querySelector('.app-detail-patch-name')?.textContent === name
    )
    if (!target) return
    target.classList.add('app-detail-patch--highlight')
    target.scrollIntoView({ block: 'center', behavior: 'smooth' })
    setTimeout(() => target.classList.remove('app-detail-patch--highlight'), 2500)
  }

  // Incremented per request so a fast chip switch cannot render a stale list.
  let patchRequest = 0

  async function renderPatches(candidate, { channel = '', highlight = null } = {}) {
    if (!candidate) {
      renderPatchList([])
      return
    }
    const chan = channel || channelState?.active || ''
    const request = ++patchRequest
    const key = makeBundleKey(candidate.bundleName, chan)
    const cached = appPatchCache.get(key)
    if (cached) {
      renderPatchList(cached, chan)
      if (highlight) highlightPatch(highlight)
      return
    }

    const record = (store.get('bundles') || {})[key]
    if (record && !record._partial) {
      // Already fully loaded — no skeleton flash for the common case
      const patches = appPatchesFrom(record)
      appPatchCache.set(key, patches)
      renderPatchList(patches, chan)
      if (highlight) highlightPatch(highlight)
      return
    }

    if (record) {
      renderPatchSkeleton()
      // bundleLoader swallows its own errors (index-only fallback), so the
      // skeleton must not survive a failed load.
      await ensureBundleKey(key)
      if (request !== patchRequest) return
    }

    const patches = await patchesFor(candidate.bundleName, chan)
    if (request !== patchRequest) return
    renderPatchList(patches, chan)
    if (highlight) highlightPatch(highlight)
  }

  // ── Channel compare ──────────────────────────────────
  /** @type {{ channels: string[], active: string, patches: Map<string, Array> }|null} */
  let channelState = null
  let channelRequest = 0

  /** Name-level diff between two patch sets of the same app. */
  function diffPatchSets(base, next) {
    const index = (list) => new Map(list.map((p) => [p.name, p]))
    const baseByName = index(base)
    const nextByName = index(next)
    const added = []
    const removed = []
    const changed = []
    for (const [name, patch] of nextByName) {
      const before = baseByName.get(name)
      if (!before) {
        added.push(name)
        continue
      }
      const versionsChanged =
        (before.compatible_versions || []).join(', ') !== (patch.compatible_versions || []).join(', ')
      if (versionsChanged || before.description !== patch.description || !!before.use !== !!patch.use) {
        changed.push(name)
      }
    }
    for (const name of baseByName.keys()) {
      if (!nextByName.has(name)) removed.push(name)
    }
    const byName = (a, b) => a.localeCompare(b)
    return { added: added.sort(byName), removed: removed.sort(byName), changed: changed.sort(byName) }
  }

  const channelButtons = new Map()

  function paintChannelButtons() {
    for (const [channel, button] of channelButtons) {
      const active = channelState?.active === channel
      button.classList.toggle('is-active', active)
      button.setAttribute('aria-pressed', String(active))
    }
  }

  /** Counts land one channel at a time; a channel whose file is still loading shows an ellipsis. */
  function paintChannelCount(channel, count) {
    const button = channelButtons.get(channel)
    if (!button) return
    const badge = button.querySelector('.app-detail-channel-count')
    if (badge) badge.textContent = String(count)
    button.title = `${count} patch${count === 1 ? '' : 'es'} in ${channel}`
  }

  function maskedName(name) {
    return name.length > 26 ? `${name.slice(0, 25)}…` : name
  }

  function renderDelta(candidate) {
    const state = channelState
    if (!state || state.channels.length < 2) {
      deltaEl.hidden = true
      return
    }
    const base = state.channels[0]
    const mine = state.patches.get(state.active)
    const reference = state.patches.get(base)
    if (!mine || !reference || state.active === base) {
      deltaEl.hidden = true
      return
    }

    const { added, removed, changed } = diffPatchSets(reference, mine)
    deltaEl.hidden = false
    deltaEl.replaceChildren()
    deltaEl.appendChild(el('span', { class: 'app-detail-channel-delta-label' }, [`vs ${base}`]))

    if (added.length + removed.length + changed.length === 0) {
      deltaEl.appendChild(el('span', { class: 'app-detail-channel-delta-same' }, ['Same patches']))
      return
    }

    const tags = [
      ...added.map((name) => ({ name, kind: 'added', sign: '+' })),
      ...removed.map((name) => ({ name, kind: 'removed', sign: '−' })),
      ...changed.map((name) => ({ name, kind: 'modified', sign: '~' })),
    ]
    const shown = tags.slice(0, 8)
    for (const tag of shown) {
      const chip = el('span', {
        class: `app-detail-channel-delta-tag app-detail-channel-delta-tag--${tag.kind}`,
        title: `${tag.sign} ${tag.name}`,
      }, [`${tag.sign} ${maskedName(tag.name)}`])
      deltaEl.appendChild(chip)
    }
    if (tags.length > shown.length) {
      deltaEl.appendChild(
        el('span', { class: 'app-detail-channel-delta-tag app-detail-channel-delta-tag--more' }, [
          `+${tags.length - shown.length} more`,
        ])
      )
    }
  }

  function selectChannel(candidate, channel) {
    if (!channelState || channelState.active === channel) return
    channelState.active = channel
    paintChannelButtons()
    renderDelta(candidate)
    renderPatches(candidate, { channel }).catch(() => {})
  }

  /**
   * Build the channel row for a bundle and warm every channel in the background
   * so the counts and the delta line are real, not placeholders.
   */
  function setupChannels(candidate, { initial = false } = {}) {
    const channels = candidate.channels || []
    const hints = hintFor(candidate)
    const active = channels.find((ch) => hints.includes(ch)) || channels[0] || ''
    channelState = { channels, active, patches: new Map() }
    channelButtons.clear()
    channelList.replaceChildren()
    deltaEl.hidden = true

    channelBar.hidden = channels.length < 2
    if (channels.length >= 2) {
      for (const channel of channels) {
        const button = el('button', {
          class: 'app-detail-channel',
          type: 'button',
          'aria-pressed': 'false',
          'data-channel': channel,
        })
        button.innerHTML = `${channel}<span class="app-detail-channel-count">…</span>`
        button.addEventListener('click', () => selectChannel(candidate, channel))
        channelList.appendChild(button)
        channelButtons.set(channel, button)
      }
      paintChannelButtons()
    }

    renderPatches(candidate, { channel: active, highlight: initial ? patchName : null }).catch(() => {})

    const request = ++channelRequest
    for (const channel of channels) {
      patchesFor(candidate.bundleName, channel)
        .then((patches) => {
          if (request !== channelRequest || !channelState) return
          channelState.patches.set(channel, patches)
          paintChannelCount(channel, patches.length)
          renderDelta(candidate)
        })
        .catch(() => {})
    }
  }

  function renderSource() {
    if (!sourceEl) return
    sourceEl.innerHTML = selected
      ? ` by <a href="#/bundle/${encodeURIComponent(selected.bundleName)}" title="Open bundle page">${escHtml(selected.patchesName)}</a>`
      : ''
    sourceEl.querySelectorAll('a').forEach((a) => a.addEventListener('click', closeModal))
  }

  function selectBundle(candidate, { initial = false } = {}) {
    selected = candidate
    for (const [name, chip] of chipEls) {
      chip.setAttribute('aria-pressed', String(name === candidate.bundleName))
      chip.classList.toggle('is-active', name === candidate.bundleName)
    }
    renderSource()
    setupChannels(candidate, { initial })
  }

  // ── Changes tab ──────────────────────────────────────
  // One row per patch, and for a rewritten description one compact pair: the
  // old text above, the arrow, the new text below. A word-level diff of a
  // 200-word blurb is unreadable in a 600px modal and made a "description
  // updated" change look like a wall of red and green.
  if (hasChanges) {
    const changesTab = tabContents['Changes']
    const diffList = el('div', { class: 'app-detail-diff' })
    for (const added of app.patch_diff.patches_added || []) {
      const row = el('div', { class: 'app-detail-diff-row app-detail-diff-row--added' })
      row.innerHTML = `<span class="app-detail-diff-icon">+</span><span class="app-detail-diff-name">${escHtml(added.name)}</span>`
      diffList.appendChild(row)
    }
    for (const removed of app.patch_diff.patches_removed || []) {
      const row = el('div', { class: 'app-detail-diff-row app-detail-diff-row--removed' })
      row.innerHTML = `<span class="app-detail-diff-icon">−</span><span class="app-detail-diff-name">${escHtml(removed.name)}</span>`
      diffList.appendChild(row)
    }
    for (const modified of app.patch_diff.patches_modified || []) {
      const row = el('div', { class: 'app-detail-diff-row app-detail-diff-row--modified' })
      row.innerHTML = `<span class="app-detail-diff-icon">~</span><span class="app-detail-diff-name">${escHtml(modified.name)}</span>`

      // "description updated" is already implied by the pair below it; every
      // other field the diff engine reports stays visible as a short tag.
      const tags = (modified.changes || []).filter((c) => !/description/i.test(c))
      if (tags.length) {
        const tagBox = el('span', { class: 'app-detail-diff-tags' })
        for (const tag of tags) tagBox.appendChild(el('span', { class: 'app-detail-diff-tag' }, [tag]))
        row.appendChild(tagBox)
      }
      diffList.appendChild(row)

      if (typeof modified.previous_description === 'string' && modified.previous_description) {
        const pair = el('div', { class: 'app-detail-diff-pair' })
        const oldText = el('p', { class: 'app-detail-diff-old', title: modified.previous_description }, [
          modified.previous_description,
        ])
        const newText = el('p', { class: 'app-detail-diff-new' })
        newText.innerHTML = `<span class="app-detail-diff-arrow" aria-label="changed to">${VERSION_ARROW}</span>`
        newText.appendChild(document.createTextNode(modified.description || ''))
        pair.appendChild(oldText)
        pair.appendChild(newText)
        diffList.appendChild(pair)
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

  // Paint the identity + chips, then patches for the selected bundle.
  if (selected) {
    selectBundle(selected, { initial: true })
    const activeChip = chipEls.get(selected.bundleName)
    if (activeChip) activeChip.scrollIntoView({ block: 'nearest', inline: 'center' })
  } else {
    renderPatchList([])
    renderSource()
  }
}
