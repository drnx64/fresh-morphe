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
   * App family: a pile of its own app cards.
   *
   * Collapsed, every card occupies the same spot: the front one is the family's
   * first app, and each card behind it steps up 7px and shrinks slightly, so
   * only its top edge shows — the card-stack silhouette, made of real cards
   * rather than a decorated icon. Opening lays them out in a single column
   * (`top: index * step`), so the pile visibly unpacks instead of a panel
   * sliding out under a header.
   *
   * Only the first four cards are built up front (a 50-app publisher is not
   * free to paint); the rest are appended in batches once the pile opens.
   */
  function buildAppStack(family, gen) {
    const { id, label, source, apps } = family
    const count = apps.length
    const pileId = `app-stack-${id.replace(/[^a-z0-9]+/gi, '-')}`
    const stack = el('div', { class: `app-stack ${source === 'developer' ? 'is-publisher' : 'is-namespace'}` })
    stack.style.setProperty('--count', String(count))
    // Depth of the visible pile, capped at four cards (three peeking edges)
    stack.style.setProperty('--layers', String(Math.min(3, count - 1)))

    // Only visible once open: names the family and collapses the pile again.
    const caption = el('button', {
      class: 'app-stack-caption',
      type: 'button',
      'aria-expanded': 'false',
      'aria-controls': pileId,
      'aria-label': `Collapse ${label}, ${count} apps`,
    })
    caption.innerHTML = `
      <span class="app-stack-eyebrow">${source === 'developer' ? 'Publisher' : 'Package family'}</span>
      <span class="app-stack-caption-label">${escHtml(label)}</span>
      <span class="app-stack-count">${count}</span>
      <span class="app-stack-chevron" aria-hidden="true"></span>
    `

    const pile = el('div', { class: 'app-stack-pile', id: pileId, role: 'group', 'aria-label': `${label}, ${count} apps` })

    function buildNote(index) {
      const card = buildAppCard(apps[index], store.get('iconCache') || {})
      card.classList.add('app-stack-note')
      card.style.setProperty('--i', String(index))
      card.style.setProperty('--d', String(Math.min(index, 3)))
      if (index === 0) {
        card.classList.add('app-stack-note--front')
        if (count > 1) {
          card.appendChild(el('span', { class: 'app-stack-chip', 'aria-hidden': 'true' }, [`+${count - 1}`]))
        }
      }
      return card
    }

    let built = 0
    function buildUpTo(limit) {
      const end = Math.min(limit, count)
      while (built < end) {
        pile.appendChild(buildNote(built))
        built++
      }
    }
    buildUpTo(4)

    let filling = false
    function fillRemaining() {
      if (filling || built >= count) return
      filling = true
      const fillBatch = () => {
        if (gen !== listGeneration) return
        buildUpTo(built + STACK_FILL_BATCH)
        if (built < count) requestAnimationFrame(fillBatch)
        else filling = false
      }
      requestAnimationFrame(fillBatch)
    }

    // The whole pile is one control while collapsed: the cards inside are inert
    // so a click anywhere on the stack opens it rather than the app under it.
    const expander = el('button', {
      class: 'app-stack-expander',
      type: 'button',
      'aria-expanded': 'false',
      'aria-controls': pileId,
      'aria-label': `Expand ${label}, ${count} apps`,
    })

    function setExpanded(open) {
      stack.classList.toggle('is-open', open)
      for (const control of [expander, caption]) {
        control.setAttribute('aria-expanded', open ? 'true' : 'false')
      }
      // Collapsed cards and the caption must not be tabbable; the expander owns
      // the interaction until the pile is open.
      pile.toggleAttribute('inert', !open)
      caption.tabIndex = open ? 0 : -1
      caption.setAttribute('aria-hidden', open ? 'false' : 'true')
      if (open) {
        openStacks.add(id)
        fillRemaining()
      } else {
        openStacks.delete(id)
      }
    }

    expander.addEventListener('click', () => setExpanded(true))
    caption.addEventListener('click', () => setExpanded(false))

    stack.appendChild(el('div', { class: 'app-stack-caption-wrap' }, [caption]))
    stack.appendChild(pile)
    stack.appendChild(expander)
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
        grid.appendChild(
          entry.kind === 'family'
            ? buildAppStack(entry, gen)
            : buildAppCard(entry.app, store.get('iconCache') || {})
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
