/**
 * Controls section — view toggle, channel filter, archived/pre-release toggles.
 */
import { el } from '../ui.js'
import * as store from '../store.js'

export function renderControls() {
  const section = el('section', { class: 'controls-section', 'aria-labelledby': 'controls-heading' })
  section.appendChild(el('h2', { class: 'sr-only', id: 'controls-heading' }, ['Filter and View Options']))

  const filtersRow = el('div', { class: 'filters-row' })

  // View toggle
  const viewToggleGroup = el('div', { class: 'view-toggle-group', id: 'view-toggle-group' })
  const gridBtn = el('button', { class: `view-toggle-opt${store.get('viewMode') === 'grid' ? ' active' : ''}`, 'data-view': 'grid', title: 'Grid view' })
  gridBtn.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M1 2.5A1.5 1.5 0 0 1 2.5 1h2A1.5 1.5 0 0 1 6 2.5v2A1.5 1.5 0 0 1 4.5 6h-2A1.5 1.5 0 0 1 1 4.5v-2zm5 0A1.5 1.5 0 0 1 7.5 1h2A1.5 1.5 0 0 1 11 2.5v2A1.5 1.5 0 0 1 9.5 6h-2A1.5 1.5 0 0 1 6 4.5v-2zm5 0A1.5 1.5 0 0 1 12.5 1h2A1.5 1.5 0 0 1 16 2.5v2A1.5 1.5 0 0 1 14.5 6h-2A1.5 1.5 0 0 1 11 4.5v-2z"/></svg> <span>Grid</span>'
  gridBtn.addEventListener('click', () => setView('grid'))

  const listBtn = el('button', { class: `view-toggle-opt${store.get('viewMode') === 'list' ? ' active' : ''}`, 'data-view': 'list', title: 'Compact view' })
  listBtn.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M2.5 12a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5zm0-4a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5zm0-4a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5z"/></svg> <span>Compact</span>'
  listBtn.addEventListener('click', () => setView('list'))

  viewToggleGroup.appendChild(gridBtn)
  viewToggleGroup.appendChild(listBtn)

  const searchRow = el('div', { class: 'search-row' }, [viewToggleGroup])
  filtersRow.appendChild(searchRow)

  // Channel filter
  const channelGroup = el('div', { class: 'filter-group' })
  channelGroup.appendChild(el('span', { class: 'filter-label' }, ['Channel:']))
  const channels = ['all', 'stable', 'dev', 'latest']
  const currentChannel = store.get('filters')?.channel || 'all'
  for (const ch of channels) {
    const btn = el('button', { class: `filter-btn${currentChannel === ch ? ' active' : ''}`, 'data-channel': ch }, [ch === 'all' ? 'All' : ch.charAt(0).toUpperCase() + ch.slice(1)])
    btn.addEventListener('click', () => {
      const filters = { ...store.get('filters'), channel: ch }
      store.set('filters', filters)
      updateFilterButtons(channelGroup, ch)
    })
    channelGroup.appendChild(btn)
  }
  filtersRow.appendChild(channelGroup)

  // Show/Hide toggles
  const showGroup = el('div', { class: 'filter-group' })
  showGroup.appendChild(el('span', { class: 'filter-label' }, ['Show:']))
  const filters = store.get('filters') || {}
  const archivedBtn = el('button', { class: `filter-btn${!filters.hideArchived ? ' active' : ''}` }, ['Archived'])
  archivedBtn.addEventListener('click', () => {
    const f = { ...store.get('filters'), hideArchived: !store.get('filters')?.hideArchived }
    store.set('filters', f)
    archivedBtn.classList.toggle('active', !f.hideArchived)
  })
  const preReleaseBtn = el('button', { class: `filter-btn${!filters.hidePreRelease ? ' active' : ''}` }, ['Pre-release'])
  preReleaseBtn.addEventListener('click', () => {
    const f = { ...store.get('filters'), hidePreRelease: !store.get('filters')?.hidePreRelease }
    store.set('filters', f)
    preReleaseBtn.classList.toggle('active', !f.hidePreRelease)
  })
  showGroup.appendChild(archivedBtn)
  showGroup.appendChild(preReleaseBtn)
  filtersRow.appendChild(showGroup)

  section.appendChild(filtersRow)
  return section
}

function setView(mode) {
  store.set('viewMode', mode)
  localStorage.setItem('fresh_view', mode)
  document.querySelectorAll('.view-toggle-opt').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.view === mode)
  })
}

function updateFilterButtons(container, activeChannel) {
  container.querySelectorAll('.filter-btn[data-channel]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.channel === activeChannel)
  })
}
