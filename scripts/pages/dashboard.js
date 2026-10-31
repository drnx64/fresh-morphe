/**
 * Dashboard page — stats, controls, bundles grid.
 */
import { el, mount } from '../ui.js'
import * as store from '../store.js'
import { renderStatsSection } from './statsSection.js'
import { renderControls } from './controls.js'
import { renderBundlesGrid, renderBundleCardsInto } from './bundlesGrid.js'

let unsubscribe = null

function refillGrid(page) {
  const gridContainer = page.querySelector('#bundles-grid-container')
  if (gridContainer) renderBundleCardsInto(gridContainer)
}

function refreshStats(page) {
  const statsSection = page.querySelector('.stats-section')
  if (statsSection) statsSection.replaceWith(renderStatsSection())
}

export function renderDashboard(container) {
  const page = el('div', { class: 'dashboard-page' })

  page.appendChild(renderStatsSection())
  page.appendChild(renderControls())
  page.appendChild(renderBundlesGrid())

  mount(container, page)

  // Re-subscribe on each visit; drop the previous page's subscriptions first
  if (unsubscribe) unsubscribe()
  const unsubBundles = store.subscribe('bundles', () => {
    if (!page.isConnected) {
      unsubscribe()
      return
    }
    refreshStats(page)
    refillGrid(page)
  })
  const unsubFilters = store.subscribe('filters', () => {
    if (!page.isConnected) {
      unsubscribe()
      return
    }
    refillGrid(page)
  })
  unsubscribe = () => {
    unsubBundles()
    unsubFilters()
  }
}
