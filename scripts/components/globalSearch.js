/**
 * GlobalSearch — global search bar in header, searches apps + bundles.
 * Supports fuzzy search with "Did you mean?" suggestions.
 */
import { el } from '../ui.js'
import * as store from '../store.js'
import { buildAppIndex, suggestFuzzy, resolveAppName, groupBundleCards } from '../utils/misc.js'
import { escHtml } from '../utils/html.js'
import { formatVersion } from '../utils/format.js'
import { SEARCH_ICON } from '../utils/svg.js'

const MAX_APP_RESULTS = 6
const MAX_BUNDLE_RESULTS = 4
const MAX_RECENT = 5
const RECENT_STORAGE_KEY = 'morphe_recent_searches'

function getRecentSearches() {
  try {
    return JSON.parse(localStorage.getItem(RECENT_STORAGE_KEY)) || []
  } catch {
    return []
  }
}

function saveRecentSearch(query) {
  if (!query || query.length < 2) return
  const recent = getRecentSearches().filter((r) => r !== query)
  recent.unshift(query)
  localStorage.setItem(RECENT_STORAGE_KEY, JSON.stringify(recent.slice(0, MAX_RECENT)))
}

const MOBILE_QUERY = '(max-width: 767px)'

export function renderGlobalSearch() {
  const root = el('div', { class: 'global-search-root' })
  const wrapper = el('div', { class: 'global-search', id: 'global-search' })

  const input = el('input', {
    type: 'text',
    class: 'global-search-input',
    placeholder: 'Search apps, bundles...',
    'aria-label': 'Global search',
    autocomplete: 'off',
  })

  const clearBtn = el('button', {
    class: 'global-search-clear',
    'aria-label': 'Clear search',
  }, ['✕'])

  const iconEl = el('span', { class: 'global-search-icon', dangerouslySetInnerHTML: SEARCH_ICON })

  const dropdown = el('div', { class: 'global-search-dropdown' })

  wrapper.appendChild(iconEl)
  wrapper.appendChild(input)
  wrapper.appendChild(clearBtn)
  wrapper.appendChild(dropdown)

  // Mobile: the header keeps only a search button; the bar opens as a sheet.
  const mobileBtn = el('button', {
    class: 'global-search-open',
    type: 'button',
    'aria-label': 'Open search',
    'aria-haspopup': 'dialog',
    dangerouslySetInnerHTML: SEARCH_ICON,
  })
  mobileBtn.addEventListener('click', openSheet)
  root.appendChild(mobileBtn)
  root.appendChild(wrapper)

  const mobileQuery = window.matchMedia(MOBILE_QUERY)
  let sheet = null
  let sheetRow = null
  let sheetOpen = false

  function ensureSheet() {
    if (sheet) return
    sheet = el('div', {
      class: 'search-sheet',
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': 'Search',
      hidden: '',
    })
    const panel = el('div', { class: 'search-sheet-panel' })
    sheetRow = el('div', { class: 'search-sheet-row' })
    const cancel = el('button', { class: 'search-sheet-cancel', type: 'button' }, ['Cancel'])
    cancel.addEventListener('click', closeSheet)
    sheetRow.appendChild(cancel)
    panel.appendChild(sheetRow)
    sheet.appendChild(panel)
    sheet.addEventListener('click', (e) => {
      if (!panel.contains(e.target)) closeSheet()
    })
  }

  function openSheet() {
    ensureSheet()
    closeDropdown()
    if (!sheet.isConnected) document.body.appendChild(sheet)
    sheetRow.insertBefore(wrapper, sheetRow.querySelector('.search-sheet-cancel'))
    sheet.hidden = false
    sheetOpen = true
    document.addEventListener('keydown', onSheetKey)
    requestAnimationFrame(() => {
      sheet.classList.add('open')
      document.body.classList.add('search-sheet-open')
      input.focus()
    })
  }

  function closeSheet() {
    if (!sheetOpen || !sheet) return
    sheetOpen = false
    document.removeEventListener('keydown', onSheetKey)
    sheet.classList.remove('open')
    document.body.classList.remove('search-sheet-open')
    closeDropdown()
    root.appendChild(wrapper)
    if (mobileQuery.matches) mobileBtn.focus({ preventScroll: true })
    setTimeout(() => {
      if (sheetOpen || !sheet) return
      sheet.hidden = true
    }, 220)
  }

  function onSheetKey(e) {
    if (e.key === 'Escape') {
      e.preventDefault()
      closeSheet()
    }
  }

  mobileQuery.addEventListener?.('change', (e) => {
    if (!e.matches && sheetOpen) closeSheet()
  })

  let appIndex = null
  let bundleList = null
  let selectedIndex = -1
  let currentResults = []

  function getAppIndex() {
    if (!appIndex) {
      const bundles = store.get('bundles') || {}
      const nameCache = store.get('nameCache') || {}
      const iconCache = store.get('iconCache') || {}
      appIndex = buildAppIndex(bundles, nameCache, iconCache)
    }
    return appIndex
  }

  // Rebuilt only when the store's bundle map is replaced (boot), not per keystroke
  function getBundleList() {
    if (bundleList) return bundleList
    const bundles = store.get('bundles') || {}
    bundleList = Object.values(groupBundleCards(bundles)).map((group) => ({
      name: group.patches_name || group.bundle,
      bundle: group.bundle,
      channels: group.channels,
      version: group.version || '',
      appCount: Math.max(group.apps?.length || 0, group.appCount || 0),
    }))
    return bundleList
  }

  function search(query) {
    const q = query.toLowerCase().trim()
    if (!q) {
      closeDropdown()
      return
    }

    const index = getAppIndex()
    const bundles = getBundleList()
    currentResults = []
    selectedIndex = -1

    const appResults = index
      .filter((app) =>
        app.name.toLowerCase().includes(q) || app.package.toLowerCase().includes(q)
      )
      .slice(0, MAX_APP_RESULTS)

    const bundleResults = bundles
      .filter((b) =>
        b.name.toLowerCase().includes(q) || b.bundle.toLowerCase().includes(q)
      )
      .slice(0, MAX_BUNDLE_RESULTS)

    for (const app of appResults) {
      currentResults.push({ type: 'app', app })
    }
    for (const bundle of bundleResults) {
      currentResults.push({ type: 'bundle', bundle })
    }

    renderDropdown(q, appResults, bundleResults, index)
  }

  function renderDropdown(query, appResults, bundleResults, appIndexList) {
    dropdown.replaceChildren()

    if (appResults.length === 0 && bundleResults.length === 0) {
      const suggestions = suggestFuzzy(query, appIndexList)
      if (suggestions.length > 0) {
        const suggestEl = el('div', { class: 'global-search-suggestion' })
        suggestEl.innerHTML = `Did you mean: `
        for (let i = 0; i < suggestions.length; i++) {
          const sug = suggestions[i]
          const link = el('button', { class: 'global-search-suggestion-link' }, [sug.name])
          link.addEventListener('click', () => {
            input.value = sug.name
            search(sug.name)
          })
          suggestEl.appendChild(link)
          if (i < suggestions.length - 1) {
            suggestEl.appendChild(document.createTextNode(', '))
          }
        }
        dropdown.appendChild(suggestEl)
        dropdown.classList.add('open')
        return
      }
      const emptyEl = el('div', { class: 'global-search-empty' }, ['No results found.'])
      dropdown.appendChild(emptyEl)
      dropdown.classList.add('open')
      return
    }

    if (appResults.length > 0) {
      const group = el('div', { class: 'global-search-group' })
      group.appendChild(el('div', { class: 'global-search-group-label' }, ['Apps']))
      for (const app of appResults) {
        const item = el('div', {
          class: 'global-search-item',
          role: 'option',
        })
        const iconCache = store.get('iconCache') || {}
        const iconUrl = iconCache[app.package] || ''
        item.innerHTML = `
          ${iconUrl
            ? `<img class="global-search-item-icon" src="${escHtml(iconUrl)}" alt="" loading="lazy" onerror="this.style.display='none'">`
            : `<div class="global-search-item-icon global-search-item-icon--fallback">${app.name.charAt(0).toUpperCase()}</div>`
          }
          <div class="global-search-item-info">
            <span class="global-search-item-name">${escHtml(app.name)}</span>
            <span class="global-search-item-meta">${escHtml(app.package)}</span>
          </div>
          <span class="global-search-item-badge">${app.bundles.length} bundle${app.bundles.length !== 1 ? 's' : ''}</span>
        `
        item.addEventListener('click', () => {
          window.dispatchEvent(new CustomEvent('open-app', {
            detail: {
              app: { package: app.package, app_name: app.name },
              bundleName: app.bundles[0]?.bundleName || '',
              channels: app.bundles[0]?.channels || [],
            },
          }))
          saveRecentSearch(input.value.trim())
          closeDropdown()
          closeSheet()
        })
        group.appendChild(item)
      }
      dropdown.appendChild(group)
    }

    if (bundleResults.length > 0) {
      const group = el('div', { class: 'global-search-group' })
      group.appendChild(el('div', { class: 'global-search-group-label' }, ['Bundles']))
      for (const bundle of bundleResults) {
        const item = el('div', {
          class: 'global-search-item',
          role: 'option',
        })
        const channelBadges = bundle.channels.map((ch) =>
          `<span class="channel-badge ${ch}">${ch}</span>`
        ).join(' ')
        item.innerHTML = `
          <div class="global-search-item-icon global-search-item-icon--fallback">${bundle.name.charAt(0).toUpperCase()}</div>
          <div class="global-search-item-info">
            <span class="global-search-item-name">${escHtml(bundle.name)}</span>
            <span class="global-search-item-meta">${channelBadges} ${bundle.version ? escHtml(formatVersion(bundle.version)) : ''} · ${bundle.appCount} apps</span>
          </div>
        `
        item.addEventListener('click', () => {
          window.dispatchEvent(new CustomEvent('open-bundle', {
            detail: { bundleName: bundle.bundle, channels: bundle.channels },
          }))
          saveRecentSearch(input.value.trim())
          closeDropdown()
          closeSheet()
        })
        group.appendChild(item)
      }
      dropdown.appendChild(group)
    }

    dropdown.classList.add('open')
  }

  function closeDropdown() {
    dropdown.classList.remove('open')
    dropdown.replaceChildren()
    selectedIndex = -1
    currentResults = []
  }

  function showRecentSearches() {
    const recent = getRecentSearches()
    if (recent.length === 0) return

    dropdown.replaceChildren()
    const group = el('div', { class: 'global-search-group' })
    group.appendChild(el('div', { class: 'global-search-group-label' }, ['Recent']))

    for (const query of recent) {
      const item = el('div', { class: 'global-search-item global-search-item--recent', role: 'option' })
      item.innerHTML = `
        <span class="global-search-item-icon global-search-item-icon--recent">&#8635;</span>
        <div class="global-search-item-info">
          <span class="global-search-item-name">${escHtml(query)}</span>
        </div>
      `
      item.addEventListener('click', () => {
        input.value = query
        search(query)
      })
      group.appendChild(item)
    }

    dropdown.appendChild(group)
    dropdown.classList.add('open')
  }

  function navigateResults(direction) {
    const items = dropdown.querySelectorAll('.global-search-item')
    if (items.length === 0) return

    items.forEach((item) => item.classList.remove('selected'))

    if (direction === 'down') {
      selectedIndex = (selectedIndex + 1) % items.length
    } else {
      selectedIndex = selectedIndex <= 0 ? items.length - 1 : selectedIndex - 1
    }

    items[selectedIndex].classList.add('selected')
    items[selectedIndex].scrollIntoView({ block: 'nearest' })
  }

  function selectResult() {
    const items = dropdown.querySelectorAll('.global-search-item')
    if (selectedIndex >= 0 && selectedIndex < items.length) {
      items[selectedIndex].click()
    }
  }

  // Debounced so a burst of keystrokes costs one search, not one per key —
  // the empty state clears immediately so Backspace never feels laggy.
  let searchTimer = null
  input.addEventListener('input', () => {
    const hasValue = input.value.length > 0
    clearBtn.classList.toggle('visible', hasValue)
    clearTimeout(searchTimer)
    if (!hasValue) {
      search('')
      return
    }
    searchTimer = setTimeout(() => search(input.value), 150)
  })

  input.addEventListener('focus', () => {
    if (input.value.trim()) {
      search(input.value)
    } else {
      showRecentSearches()
    }
  })

  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      navigateResults('down')
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      navigateResults('up')
    } else if (e.key === 'Enter') {
      e.preventDefault()
      selectResult()
    } else if (e.key === 'Escape') {
      if (sheetOpen) {
        e.preventDefault()
        closeSheet()
        return
      }
      input.blur()
      closeDropdown()
    }
  })

  clearBtn.addEventListener('click', () => {
    input.value = ''
    clearBtn.classList.remove('visible')
    closeDropdown()
    input.focus()
  })

  document.addEventListener('click', (e) => {
    if (!wrapper.contains(e.target)) {
      closeDropdown()
    }
  })

  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA') {
      e.preventDefault()
      if (mobileQuery.matches) openSheet()
      else input.focus()
    }
  })

  store.subscribe('bundles', () => {
    appIndex = null
    bundleList = null
  })

  return root
}
