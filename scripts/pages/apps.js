/**
 * Apps page — today's updates + directory of all apps across bundles.
 */
import { el, mount } from '../ui.js'
import * as store from '../store.js'
import { buildAppIndex, resolveAppName, getAppIconUrl, renderAppIcon, suggestFuzzy } from '../utils/misc.js'
import { getPlayStoreUrl } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { renderTodayUpdates } from './todayUpdates.js'
import { skeletonGrid } from '../components/skeleton.js'

let unsubscribeBundles = null

export function renderApps(container) {
  const page = el('div', { class: 'apps-page' })

  // Today's updates (hero section on apps page)
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

  function rebuildIndex() {
    appIndex = buildAppIndex(
      store.get('bundles') || {},
      store.get('nameCache') || {},
      store.get('iconCache') || {},
    )
  }

  function renderAppsList(query = '') {
    const gen = ++listGeneration
    grid.replaceChildren()

    // Booting: bundle files still streaming in — show skeletons, not "no apps"
    if (store.get('loading') && appIndex.length === 0) {
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

    let rendered = 0
    const BATCH = 24
    function renderBatch() {
      if (gen !== listGeneration) return
      const end = Math.min(rendered + BATCH, list.length)
      for (let i = rendered; i < end; i++) {
        const app = list[i]
        const card = el('div', { class: 'app-card' })
        const iconCache = store.get('iconCache') || {}
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
        card.style.cursor = 'pointer'
        card.setAttribute('role', 'button')
        card.setAttribute('tabindex', '0')

        card.addEventListener('click', () => {
          window.dispatchEvent(new CustomEvent('open-app', {
            detail: { app: { package: app.package, app_name: app.name } },
          }))
        })
        card.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            window.dispatchEvent(new CustomEvent('open-app', {
              detail: { app: { package: app.package, app_name: app.name } },
            }))
          }
        })
        grid.appendChild(card)
      }
      rendered = end
      if (rendered < list.length) requestAnimationFrame(renderBatch)
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
