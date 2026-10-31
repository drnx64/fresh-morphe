/**
 * Diff page — app-centric: search for an app, see all patches across bundles.
 * Cross-bundle patch highlighting for same-name patches.
 */
import { el, mount } from '../ui.js'
import * as store from '../store.js'
import { buildAppIndex, resolveAppName, renderAppIcon, suggestFuzzy, copyToClipboard, getDisplayAvatar, getDisplayBundleImage, avatarStackHtml } from '../utils/misc.js'
import { ensureAppBundles } from '../services/bundleLoader.js'
import { escHtml } from '../utils/html.js'
import { formatVersion } from '../utils/format.js'
import { parseBundleKey } from '../utils/bundleKey.js'
import { SEARCH_ICON, REFRESH_ICON, CHEVRON_DOWN } from '../utils/svg.js'
import { buildCompareMatrix } from '../components/compareMatrixTable.js'

const HIGHLIGHT_COLORS = [
  { bg: 'rgba(74, 127, 200, 0.15)', border: 'rgba(74, 127, 200, 0.4)', text: 'var(--state-info)' },
  { bg: 'rgba(127, 168, 118, 0.15)', border: 'rgba(127, 168, 118, 0.4)', text: 'var(--state-stable)' },
  { bg: 'rgba(201, 138, 95, 0.15)', border: 'rgba(201, 138, 95, 0.4)', text: 'var(--state-dev)' },
  { bg: 'rgba(162, 129, 173, 0.15)', border: 'rgba(162, 129, 173, 0.4)', text: 'var(--state-plum)' },
  { bg: 'rgba(181, 83, 63, 0.15)', border: 'rgba(181, 83, 63, 0.4)', text: 'var(--state-critical)' },
]

// Attach-once document listener: re-added per visit but always removes
// the previous instance so handlers don't accumulate.
let docClickHandler = null

export function renderDiff(container) {
  const page = el('div', { class: 'diff-page' })
  page.appendChild(el('h2', { class: 'section-title' }, ['Patch Explorer']))

  const subtitle = el('p', { class: 'section-helper' }, ['Search for an app to see all patches across bundles.'])
  page.appendChild(subtitle)

  const searchWrapper = el('div', { class: 'diff-app-search' })
  const searchInput = el('input', {
    type: 'text',
    class: 'diff-app-search-input',
    placeholder: 'Search for an app (e.g. YouTube, TikTok)...',
    'aria-label': 'Search app for patch comparison',
  })
  const searchIcon = el('span', { class: 'diff-app-search-icon', dangerouslySetInnerHTML: SEARCH_ICON })
  const clearBtn = el('button', { class: 'global-search-clear', 'aria-label': 'Clear' }, ['✕'])
  const searchDropdown = el('div', { class: 'diff-app-search-dropdown' })

  searchWrapper.appendChild(searchIcon)
  searchWrapper.appendChild(searchInput)
  searchWrapper.appendChild(clearBtn)
  searchWrapper.appendChild(searchDropdown)

  const resultArea = el('div', { class: 'diff-result' })
  const suggestionsArea = el('div', { class: 'diff-suggestions' })

  page.appendChild(searchWrapper)
  page.appendChild(suggestionsArea)
  page.appendChild(resultArea)
  mount(container, page)

  const bundles = store.get('bundles') || {}
  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}
  const appIndex = buildAppIndex(bundles, nameCache, iconCache)

  // Filter apps with 2+ distinct bundles for suggestions
  const multiBundleApps = appIndex.filter((app) => {
    const uniqueBundles = new Set(app.bundles.map((b) => b.bundleName))
    return uniqueBundles.size >= 2
  })

  function getRandomSuggestions(count = 8) {
    const shuffled = [...multiBundleApps].sort(() => Math.random() - 0.5)
    return shuffled.slice(0, count)
  }

  function renderSuggestions() {
    suggestionsArea.replaceChildren()
    const suggestions = getRandomSuggestions(8)
    if (suggestions.length === 0) return

    const header = el('div', { class: 'diff-suggestions-header' })
    header.innerHTML = `<span class="diff-suggestions-title">Suggested Apps</span>`
    const refreshBtn = el('button', { class: 'diff-suggestions-refresh', 'aria-label': 'Refresh suggestions', dangerouslySetInnerHTML: REFRESH_ICON })
    refreshBtn.addEventListener('click', renderSuggestions)
    header.appendChild(refreshBtn)
    suggestionsArea.appendChild(header)

    const grid = el('div', { class: 'diff-suggestions-grid' })
    for (const app of suggestions) {
      const card = el('div', { class: 'diff-suggestion-card', role: 'button', tabindex: '0' })
      const iconHtml = renderAppIcon({ package: app.package, app_name: app.name }, iconCache)
      const bundleCount = app.bundles.length
      card.innerHTML = `
        ${iconHtml}
        <div class="diff-suggestion-info">
          <span class="diff-suggestion-name">${escHtml(app.name)}</span>
          <span class="diff-suggestion-meta">${bundleCount} bundle${bundleCount !== 1 ? 's' : ''}</span>
        </div>
      `
      card.addEventListener('click', () => {
        searchInput.value = app.name
        clearBtn.classList.add('visible')
        selectApp(app.package)
      })
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          searchInput.value = app.name
          clearBtn.classList.add('visible')
          selectApp(app.package)
        }
      })
      grid.appendChild(card)
    }
    suggestionsArea.appendChild(grid)
  }

  renderSuggestions()

  let debounceTimer
  let selectedPkg = null

  searchInput.addEventListener('input', () => {
    clearTimeout(debounceTimer)
    const q = searchInput.value.trim()
    clearBtn.classList.toggle('visible', q.length > 0)

    if (!q) {
      searchDropdown.classList.remove('open')
      searchDropdown.replaceChildren()
      selectedPkg = null
      return
    }

    debounceTimer = setTimeout(() => {
      const ql = q.toLowerCase()
      const matches = appIndex.filter((app) =>
        app.name.toLowerCase().includes(ql) || app.package.toLowerCase().includes(ql)
      ).slice(0, 8)

      searchDropdown.replaceChildren()

      if (matches.length === 0) {
        const suggestions = suggestFuzzy(q, appIndex)
        if (suggestions.length > 0) {
          const sugEl = el('div', { class: 'diff-app-search-suggestion' })
          sugEl.innerHTML = `Did you mean: `
          for (let i = 0; i < suggestions.length; i++) {
            const link = el('button', { class: 'global-search-suggestion-link' }, [suggestions[i].name])
            link.addEventListener('click', () => {
              searchInput.value = suggestions[i].name
              clearBtn.classList.add('visible')
              selectApp(suggestions[i].package)
            })
            sugEl.appendChild(link)
            if (i < suggestions.length - 1) sugEl.appendChild(document.createTextNode(', '))
          }
          searchDropdown.appendChild(sugEl)
        } else {
          searchDropdown.appendChild(el('div', { class: 'global-search-empty' }, ['No apps found.']))
        }
      } else {
        for (const app of matches) {
          const item = el('div', { class: 'diff-app-search-item' })
          const iconUrl = iconCache[app.package] || ''
          item.innerHTML = `
            ${iconUrl
              ? `<img class="app-icon" src="${escHtml(iconUrl)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
              : ''
            }
            <div class="app-icon app-icon--fallback" ${iconUrl ? 'style="display:none"' : ''}>${app.name.charAt(0).toUpperCase()}</div>
            <div class="global-search-item-info">
              <span class="global-search-item-name">${escHtml(app.name)}</span>
              <span class="global-search-item-meta">${escHtml(app.package)}</span>
            </div>
          `
          item.addEventListener('click', () => selectApp(app.package))
          searchDropdown.appendChild(item)
        }
      }
      searchDropdown.classList.add('open')
    }, 150)
  })

  searchInput.addEventListener('focus', () => {
    if (searchInput.value.trim()) {
      searchInput.dispatchEvent(new Event('input'))
    }
  })

  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      searchInput.blur()
      searchDropdown.classList.remove('open')
    }
  })

  clearBtn.addEventListener('click', () => {
    searchInput.value = ''
    clearBtn.classList.remove('visible')
    searchDropdown.classList.remove('open')
    searchDropdown.replaceChildren()
    selectedPkg = null
    resultArea.replaceChildren()
    suggestionsArea.style.display = ''
    renderSuggestions()
    searchInput.focus()
  })

  if (docClickHandler) document.removeEventListener('click', docClickHandler)
  docClickHandler = (e) => {
    if (!searchWrapper.isConnected) {
      document.removeEventListener('click', docClickHandler)
      docClickHandler = null
      return
    }
    if (!searchWrapper.contains(e.target)) {
      searchDropdown.classList.remove('open')
    }
  }
  document.addEventListener('click', docClickHandler)

  function selectApp(pkg) {
    selectedPkg = pkg
    searchDropdown.classList.remove('open')
    searchDropdown.replaceChildren()
    suggestionsArea.style.display = 'none'
    // Patch lists live in the per-bundle files; warm ones resolve before the
    // browser paints, so the loading state only ever shows on a real fetch.
    resultArea.innerHTML = '<div class="loading-state">Loading patch data…</div>'
    ensureAppBundles(pkg)
      .then(() => renderAppBundles(pkg))
      .catch((err) => {
        console.error('[diff] patch data failed:', err)
        renderAppBundles(pkg)
      })
  }

  function renderAppBundles(pkg) {
    resultArea.replaceChildren()

    const appEntry = appIndex.find((a) => a.package === pkg)
    if (!appEntry) {
      resultArea.innerHTML = '<div class="empty-state">App not found.</div>'
      return
    }

    const appName = resolveAppName(appEntry, nameCache)
    const iconHtml = renderAppIcon({ package: pkg, app_name: appName }, iconCache)

    const headerEl = el('div', { class: 'diff-app-header' })
    headerEl.innerHTML = `
      ${iconHtml}
      <div class="diff-app-header-info">
        <h3 class="diff-app-header-name">${escHtml(appName)}</h3>
        <span class="diff-app-header-pkg">${escHtml(pkg)}</span>
      </div>
    `
    resultArea.appendChild(headerEl)

    const patchBundleMap = new Map()

    for (const [key, bundle] of Object.entries(bundles)) {
      const { name: bName, channel } = parseBundleKey(key)
      const appData = bundle.apps?.find((a) => a.package === pkg)
      if (!appData) continue

      const patches = appData.patches || []
      if (patches.length === 0) continue

      const bKey = `${bName}:${channel}`
      if (!patchBundleMap.has(bKey)) {
        patchBundleMap.set(bKey, {
          name: bundle.patches_name || bName,
          bundle: bName,
          channel,
          version: bundle.version || '',
          avatarUrl: getDisplayAvatar(bundle.repo_url, bundle.avatarUrl),
          bundleImageUrl: getDisplayBundleImage(bundle.repo_url, bundle.bundleImageUrl),
          patches,
        })
      } else {
        const existing = patchBundleMap.get(bKey)
        if (bundle.version && !existing.version) existing.version = bundle.version
      }
    }

    if (patchBundleMap.size === 0) {
      resultArea.appendChild(el('div', { class: 'empty-state' }, ['No patch data found for this app.']))
      return
    }

    // ── Bundle picker: toggle which bundles are displayed ──
    const selectedKeys = new Set(patchBundleMap.keys())
    const pillByKey = new Map()

    const pickerEl = el('div', { class: 'diff-bundle-picker' })
    const pillsRow = el('div', { class: 'diff-bundle-picker-pills' })
    const controlsRow = el('div', { class: 'diff-bundle-picker-controls' })

    const sortedKeys = [...patchBundleMap.keys()].sort((a, b) => {
      const ia = patchBundleMap.get(a)
      const ib = patchBundleMap.get(b)
      return ia.name.localeCompare(ib.name) || a.localeCompare(b)
    })

    for (const bKey of sortedKeys) {
      const info = patchBundleMap.get(bKey)
      const pill = el('button', { class: 'diff-bundle-pill active', type: 'button', title: `Toggle ${info.name}` })
      pill.innerHTML = `${escHtml(info.name)} <span class="channel-badge channel-badge--sm ${info.channel}">${info.channel}</span>`
      pill.addEventListener('click', () => {
        if (selectedKeys.has(bKey)) selectedKeys.delete(bKey)
        else selectedKeys.add(bKey)
        pill.classList.toggle('active', selectedKeys.has(bKey))
        renderGroups()
      })
      pillByKey.set(bKey, pill)
      pillsRow.appendChild(pill)
    }

    function setAll(on) {
      selectedKeys.clear()
      if (on) for (const k of patchBundleMap.keys()) selectedKeys.add(k)
      for (const [, pill] of pillByKey) pill.classList.toggle('active', on)
      renderGroups()
    }
    const allBtn = el('button', { class: 'diff-bundle-picker-btn', type: 'button' }, ['All'])
    const noneBtn = el('button', { class: 'diff-bundle-picker-btn', type: 'button' }, ['None'])
    allBtn.addEventListener('click', () => setAll(true))
    noneBtn.addEventListener('click', () => setAll(false))
    controlsRow.appendChild(allBtn)
    controlsRow.appendChild(noneBtn)

    pickerEl.appendChild(pillsRow)
    if (patchBundleMap.size >= 2) {
      pickerEl.appendChild(controlsRow)
      resultArea.appendChild(pickerEl)
    }

    // ── View toggle: presence matrix (default) or grouped bundle sections ──
    let activeView = 'matrix'
    // Survives rebuilds so filter + expanded rows don't reset on pill toggles
    const matrixState = {}

    const viewRow = el('div', { class: 'diff-view-toggle', role: 'group', 'aria-label': 'Comparison view' })
    const matrixBtn = el('button', { type: 'button', class: 'diff-view-btn active' }, ['Matrix'])
    const sectionsBtn = el('button', { type: 'button', class: 'diff-view-btn' }, ['Sections'])

    function setView(view) {
      activeView = view
      matrixBtn.classList.toggle('active', view === 'matrix')
      sectionsBtn.classList.toggle('active', view === 'sections')
      renderGroups()
    }
    matrixBtn.addEventListener('click', () => setView('matrix'))
    sectionsBtn.addEventListener('click', () => setView('sections'))
    viewRow.appendChild(matrixBtn)
    viewRow.appendChild(sectionsBtn)
    resultArea.appendChild(viewRow)

    const groupsEl = el('div', { class: 'diff-bundle-groups' })
    resultArea.appendChild(groupsEl)

    // Collapse state persists across renderGroups() (pill toggles, All/None)
    const collapsedKeys = new Set()

    const renderBundleGroup = (bundles, label, crossBundlePatches, patchColorMap) => {
      if (bundles.length === 0) return
      const groupEl = el('div', { class: 'diff-bundle-group' })
      groupEl.appendChild(el('h4', { class: 'diff-bundle-group-title' }, [label]))
      for (const bundleInfo of bundles) {
      const sectionKey = `${bundleInfo.bundle}:${bundleInfo.channel}`
      const bundleSection = el('div', { class: `diff-bundle-section${collapsedKeys.has(sectionKey) ? ' collapsed' : ''}` })

      const channelBadges = `<span class="channel-badge ${bundleInfo.channel}">${bundleInfo.channel}</span>`
      const titleRow = el('div', {
        class: 'diff-bundle-section-header',
        role: 'button',
        tabindex: '0',
        'aria-expanded': collapsedKeys.has(sectionKey) ? 'false' : 'true',
      })
      titleRow.innerHTML = `
        <div class="diff-bundle-section-info">
          ${avatarStackHtml(
            bundleInfo.bundleImageUrl,
            bundleInfo.avatarUrl,
            (bundleInfo.name || '?').charAt(0).toUpperCase(),
            'diff-bundle-section-avatar',
            'diff-bundle-section-avatar diff-bundle-section-avatar--fallback',
          )}
          <span class="diff-bundle-section-name">${escHtml(bundleInfo.name)}</span>
          ${channelBadges}
          ${bundleInfo.version ? `<span class="bundle-version-tag">${escHtml(formatVersion(bundleInfo.version))}</span>` : ''}
        </div>
        <div class="diff-bundle-section-side">
          <span class="diff-bundle-section-count">${bundleInfo.patches.length} patches</span>
          <span class="diff-bundle-section-toggle">${CHEVRON_DOWN}</span>
        </div>
      `
      const toggleSection = () => {
        const nowCollapsed = bundleSection.classList.toggle('collapsed')
        if (nowCollapsed) collapsedKeys.add(sectionKey)
        else collapsedKeys.delete(sectionKey)
        titleRow.setAttribute('aria-expanded', nowCollapsed ? 'false' : 'true')
      }
      titleRow.addEventListener('click', toggleSection)
      titleRow.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          toggleSection()
        }
      })
      bundleSection.appendChild(titleRow)

      const patchesList = el('div', { class: 'diff-bundle-patches' })
      const sorted = [...bundleInfo.patches].sort((a, b) => a.name.localeCompare(b.name))

      for (const patch of sorted) {
        const patchEl = el('div', { class: 'diff-patch-row' })
        const patchLower = patch.name.toLowerCase()
        const sharedWith = crossBundlePatches.get(patchLower)
        const color = patchColorMap.get(patchLower)

        if (sharedWith && color) {
          patchEl.style.background = color.bg
          patchEl.style.borderLeft = `3px solid ${color.border}`
        }

        const onBadge = patch.use
          ? '<span class="badge badge--default-on">ON</span>'
          : ''

        const versionsHtml = patch.compatible_versions?.length
          ? `<span class="diff-patch-versions">${escHtml(patch.compatible_versions.join(', '))}</span>`
          : ''

        let sharedBadgesHtml = ''
        if (sharedWith) {
          const others = sharedWith.filter((b) => b !== bundleInfo.name)
          if (others.length > 0) {
            const shown = others.slice(0, 3)
            const rest = others.slice(3)
            sharedBadgesHtml = shown
              .map((b) => `<span class="diff-patch-shared-badge" title="Also in ${escHtml(b)}">${escHtml(b)}</span>`)
              .join('')
            if (rest.length > 0) {
              sharedBadgesHtml += `<span class="diff-patch-shared-badge" title="${escHtml(rest.join(', '))}">+${rest.length}</span>`
            }
          }
        }

        patchEl.innerHTML = `
          <div class="diff-patch-row-header">
            <span class="diff-patch-row-name copyable" title="Click to copy">${escHtml(patch.name)}</span>
            ${onBadge}
            ${sharedBadgesHtml}
          </div>
          ${patch.description ? `<p class="diff-patch-row-desc">${escHtml(patch.description)}</p>` : ''}
          ${versionsHtml ? `<div class="diff-patch-row-meta">${versionsHtml}</div>` : ''}
        `

        const nameEl = patchEl.querySelector('.diff-patch-row-name')
        if (nameEl) {
          nameEl.addEventListener('click', (e) => {
            e.stopPropagation()
            copyToClipboard(patch.name, nameEl)
          })
        }

        patchesList.appendChild(patchEl)
      }

      bundleSection.appendChild(patchesList)
      groupEl.appendChild(bundleSection)
      }
      groupsEl.appendChild(groupEl)
    }

    function renderGroups() {
      groupsEl.replaceChildren()

      const selectedInfos = sortedKeys
        .filter((k) => selectedKeys.has(k))
        .map((k) => patchBundleMap.get(k))

      if (selectedInfos.length === 0) {
        groupsEl.appendChild(el('div', { class: 'empty-state' }, ['No bundles selected. Use the pills above to add bundles.']))
        return
      }

      if (activeView === 'matrix') {
        const cols = selectedInfos.map((info) => ({
          label: info.name,
          avatarUrl: info.bundleImageUrl || info.avatarUrl,
          version: info.version,
          channel: info.channel,
          patches: info.patches,
        }))
        groupsEl.appendChild(buildCompareMatrix(cols, matrixState))
        return
      }

      // Single pass: patchLower → Set of bundle keys containing it
      const patchBundlesMap = new Map()
      const bundleKeyToName = new Map()
      for (const info of selectedInfos) {
        bundleKeyToName.set(info.bundle, info.name)
        for (const p of info.patches) {
          const lower = p.name.toLowerCase()
          let set = patchBundlesMap.get(lower)
          if (!set) {
            set = new Set()
            patchBundlesMap.set(lower, set)
          }
          set.add(info.bundle)
        }
      }
      const crossBundlePatches = new Map()
      for (const [lower, keys] of patchBundlesMap) {
        if (keys.size > 1) {
          crossBundlePatches.set(lower, [...keys].map((k) => bundleKeyToName.get(k) || k))
        }
      }

      const patchColorMap = new Map()
      let colorIdx = 0
      for (const [patchName] of crossBundlePatches) {
        patchColorMap.set(patchName, HIGHLIGHT_COLORS[colorIdx % HIGHLIGHT_COLORS.length])
        colorIdx++
      }

      const stableBundles = selectedInfos.filter((b) => b.channel === 'stable').sort((a, b) => a.name.localeCompare(b.name))
      const devBundles = selectedInfos.filter((b) => b.channel === 'dev').sort((a, b) => a.name.localeCompare(b.name))
      const latestBundles = selectedInfos.filter((b) => b.channel === 'latest').sort((a, b) => a.name.localeCompare(b.name))

      renderBundleGroup(stableBundles, 'Stable', crossBundlePatches, patchColorMap)
      renderBundleGroup(devBundles, 'Dev', crossBundlePatches, patchColorMap)
      renderBundleGroup(latestBundles, 'Latest', crossBundlePatches, patchColorMap)
    }

    renderGroups()
  }
}
