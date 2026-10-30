/**
 * Apps page - what's new + directory of all apps across bundles.
 */

import { el, mount } from '../ui.js'
import * as store from '../store.js'
import { buildAppIndex, resolveAppName, getAppIconUrl, renderAppIcon, suggestFuzzy } from '../utils/misc.js'
import { getPlayStoreUrl } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { groupAppsIntoFamilies } from '../utils/appFamily.js'
import { renderTodayUpdates } from './todayUpdates.js'
import { skeletonGrid } from '../components/skeleton.js'

const RENDER_BATCH = 24
const STACK_FILL_BATCH = 24

let unsubscribeBundles = null

function openApp(app) {
  window.dispatchEvent(new CustomEvent('open-app', {
    detail: { app: { package: app.package, app_name: app.name } },
  }))
}

function makeCardInteractive(card, activate) {
  card.style.cursor = 'pointer'
  card.setAttribute('role', 'button')
  card.setAttribute('tabindex', '0')
  card.addEventListener('click', activate)
  card.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    activate()
  })
  return card
}

function buildAppCard(app, iconCache) {
  const card = el('div', { class: 'app-card' })
  const iconHtml = renderAppIcon({ package: app.package, app_name: app.name }, iconCache)
  const bundleCount = app.bundles.length
  card.innerHTML = `
    <div class="app-card-main">
      ${iconHtml}
      <div class="app-card-info">
        <span class="app-card-name">${escHtml(app.name)}</span>
        <span class="app-card-pkg">${escHtml(app.package)}</span>
      </div>
      <span class="app-card-bundle-count">${bundleCount} bundle${bundleCount !== 1 ? 's' : ''}</span>
    </div>
  `
  return makeCardInteractive(card, () => openApp(app))
}

export function renderApps(container) {
  const page = el('div', { class: 'apps-page' })

  // What's New (hero section on apps page)
  page.appendChild(renderTodayUpdates())

  // Apps directory
  const header = el('div', { class: 'apps-header' }, [
    el('h2', { class: 'section-title' }, ['Apps Directory']),
  ])

  const searchInput = el('input', {
    type: 'text',
    class: 'apps-search-input',
    placeholder: 'Search apps by name or package...',
    'aria-label': 'Search apps',
  })

  const clearBtn = el('button', {
    class: 'search-bar-clear',
    'aria-label': 'Clear search',
    style: { display: 'none' },
  }, ['✕'])

  const searchRow = el('div', { class: 'search-row' }, [searchInput, clearBtn])

  const grid = el('div', { class: 'apps-grid', id: 'apps-grid' })

  page.appendChild(header)
  page.appendChild(searchRow)
  page.appendChild(grid)
  mount(container, page)

  // Build app index
  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}
  let appIndex = buildAppIndex(store.get('bundles') || {}, nameCache, iconCache)

  // Invalidates an in-flight progressive-render chain when the list refills
  let listGeneration = 0

  // Stacks the user opened; survives a refill so streaming bundles don't shut them
  const openStacks = new Set()

  function rebuildIndex() {
    appIndex = buildAppIndex(
      store.get('bundles') || {},
      store.get('nameCache') || {},
      store.get('iconCache') || {},
    )
  }

  /**
   * App family: the card itself is a stack of cards.
   *
   * The collapsed face is a real `.app-card` with two card-shaped layers
   * peeking out behind its top edge — narrower the further back, so the pile
   * reads as "more cards under this one". A row of icons does not: it says
   * "four apps" while pretending to be one card.
   *
   * Members are built on first expand (a 50-app publisher is not free to paint)
   * and the panel animates with grid-template-rows, so no height measuring runs.
   */
  function buildAppStack(family, gen, iconCache) {
    const { id, label, source, apps } = family
    const panelId = `app-stack-${id.replace(/[^a-z0-9]+/gi, '-')}`
    const stack = el('div', { class: `app-stack ${source === 'developer' ? 'is-publisher' : 'is-namespace'}` })
    const toggle = el('div', { class: 'app-card app-stack-toggle' })

    const leadIcon = renderAppIcon({ package: apps[0].package, app_name: apps[0].name }, iconCache)
    const eyebrow = source === 'developer' ? 'Publisher' : 'Package family'
    toggle.innerHTML = `
      <div class="app-card-main">
        ${leadIcon}
        <div class="app-card-info">
          <span class="app-stack-eyebrow">${eyebrow}</span>
          <span class="app-card-name${source === 'namespace' ? ' is-namespace' : ''}">${escHtml(label)}</span>
          <span class="app-card-pkg">${apps.length} apps</span>
        </div>
        <span class="app-stack-count" aria-hidden="true">${apps.length}</span>
        <span class="app-stack-chevron" aria-hidden="true"></span>
      </div>
    `
    toggle.setAttribute('aria-controls', panelId)
    toggle.setAttribute('aria-expanded', 'false')
    toggle.setAttribute('aria-label', `${label}, ${apps.length} apps`)

    const panel = el('div', { class: 'app-stack-panel', id: panelId })
    panel.innerHTML = '<div class="app-stack-panel-inner"><div class="app-stack-members"></div></div>'
    const membersBox = panel.firstElementChild.firstElementChild
    let membersBuilt = false

    function fillMembers() {
      if (membersBuilt) return
      membersBuilt = true
      let rendered = 0
      const fillBatch = () => {
        if (gen !== listGeneration) return
        const end = Math.min(rendered + STACK_FILL_BATCH, apps.length)
        for (; rendered < end; rendered++) {
          const card = buildAppCard(apps[rendered], store.get('iconCache') || {})
          card.classList.add('app-stack-member')
          // Stagger the entrance; the panel is already opening around them
          card.style.setProperty('--i', String(rendered))
          membersBox.appendChild(card)
        }
        if (rendered < apps.length) requestAnimationFrame(fillBatch)
      }
      fillBatch()
    }

    function setExpanded(open) {
      stack.classList.toggle('is-open', open)
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false')
      // Collapsed members must not be tabbable or announced while they animate out
      panel.toggleAttribute('inert', !open)
      panel.setAttribute('aria-hidden', open ? 'false' : 'true')
      if (open) {
        openStacks.add(id)
        fillMembers()
      } else {
        openStacks.delete(id)
      }
    }

    makeCardInteractive(toggle, () => setExpanded(!stack.classList.contains('is-open')))

    // Two card-shaped layers behind the real card. They are siblings of the
    // toggle, absolutely positioned inside the stack's top padding.
    stack.appendChild(el('span', { class: 'app-stack-layer app-stack-layer--back', 'aria-hidden': 'true' }))
    stack.appendChild(el('span', { class: 'app-stack-layer app-stack-layer--mid', 'aria-hidden': 'true' }))
    stack.appendChild(toggle)
    stack.appendChild(panel)
    setExpanded(openStacks.has(id))
    return stack
  }

  function renderAppsList(query = '') {
    const gen = ++listGeneration
    grid.replaceChildren()

    // Booting: the app index is still being built — show skeletons, not "no apps"
    if (store.get('booting') && appIndex.length === 0) {
      grid.appendChild(skeletonGrid(8))
      return
    }

    let list = appIndex
    if (query) {
      const q = query.toLowerCase()
      list = list.filter((app) =>
        app.name.toLowerCase().includes(q) || app.package.toLowerCase().includes(q)
      )
    }

    if (list.length === 0) {
      const suggestionEl = el('div', { class: 'search-no-results' })
      if (query) {
        const suggestions = suggestFuzzy(query, appIndex)
        if (suggestions.length > 0) {
          suggestionEl.innerHTML = `<span class="search-no-results-text">No apps found for "${escHtml(query)}".</span>`
          const didYouMean = el('div', { class: 'search-suggestion' })
          didYouMean.innerHTML = `Did you mean: `
          for (let i = 0; i < suggestions.length; i++) {
            const sug = suggestions[i]
            const link = el('button', { class: 'search-suggestion-link' }, [sug.name])
            link.addEventListener('click', () => {
              searchInput.value = sug.name
              clearBtn.style.display = ''
              renderAppsList(sug.name)
            })
            didYouMean.appendChild(link)
            if (i < suggestions.length - 1) {
              didYouMean.appendChild(document.createTextNode(', '))
            }
          }
          suggestionEl.appendChild(didYouMean)
        } else {
          suggestionEl.innerHTML = `<span class="search-no-results-text">No apps found for "${escHtml(query)}".</span>`
        }
      } else {
        suggestionEl.innerHTML = '<span class="search-no-results-text">No apps found.</span>'
      }
      grid.appendChild(suggestionEl)
      return
    }

    // A search result set stays flat: the user is looking for one package inside
    // the family, so folding the whole publisher back into a stack would hide it.
    const entries = query ? list.map((app) => ({ kind: 'app', app })) : groupAppsIntoFamilies(list)

    let rendered = 0
    function renderBatch() {
      if (gen !== listGeneration) return
      const end = Math.min(rendered + RENDER_BATCH, entries.length)
      for (let i = rendered; i < end; i++) {
        const entry = entries[i]
        const cache = store.get('iconCache') || {}
        grid.appendChild(
          entry.kind === 'family'
            ? buildAppStack(entry, gen, cache)
            : buildAppCard(entry.app, cache)
        )
      }
      rendered = end
      if (rendered < entries.length) requestAnimationFrame(renderBatch)
    }
    renderBatch()
  }

  renderAppsList()

  // Streaming boot: refill the grid as bundle files land (keeps search input focus)
  if (unsubscribeBundles) unsubscribeBundles()
  unsubscribeBundles = store.subscribe('bundles', () => {
    if (!page.isConnected) {
      unsubscribeBundles()
      return
    }
    rebuildIndex()
    renderAppsList(searchInput.value)
  })

  let debounceTimer
  searchInput.addEventListener('input', () => {
    clearTimeout(debounceTimer)
    const hasValue = searchInput.value.length > 0
    clearBtn.style.display = hasValue ? '' : 'none'
    debounceTimer = setTimeout(() => renderAppsList(searchInput.value), 200)
  })

  clearBtn.addEventListener('click', () => {
    searchInput.value = ''
    clearBtn.style.display = 'none'
    renderAppsList('')
    searchInput.focus()
  })

  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      searchInput.value = ''
      clearBtn.style.display = 'none'
      renderAppsList('')
    }
  })
}
