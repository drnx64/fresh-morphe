/**
 * Presence matrix — shared renderer for cross-bundle patch comparison.
 *
 * Rows are the union of patch names across the given bundles, columns are the
 * bundles themselves, so a patch present in K of N bundles is always visible
 * (never dropped, unlike a strict intersection).
 *
 * Used by the app-detail compare overlay and the /diff page.
 */
import { el } from '../ui.js'
import { escHtml } from '../utils/html.js'
import { wordDiffHtml } from '../utils/wordDiff.js'
import { formatVersion } from '../utils/format.js'
import { buildCompareRows, filterRows } from '../utils/compareMatrix.js'

const ON_BADGE = '<span class="badge badge--default-on">ON</span>'

/** First column of the matrix: name + ON + share count + differs flag. */
function nameCellHtml(row, colCount) {
  const anyOn = [...row.by.values()].some((p) => p.use)
  const countChip = row.shared
    ? `<span class="compare-count">${row.count}/${colCount}</span>`
    : '<span class="compare-count compare-count--only">only</span>'
  return (
    `<span class="compare-name">${escHtml(row.name)}</span>` +
    (anyOn ? ON_BADGE : '') +
    countChip +
    (row.shared ? '<span class="compare-shared-flag">shared</span>' : '') +
    (row.differs ? '<span class="compare-flag">differs</span>' : '')
  )
}

/**
 * Expandable row body. When bundles disagree on content the description and
 * versions are rendered per bundle with wordDiffHtml against the first bundle.
 */
function buildCompareDetail(row, cols) {
  const wrap = el('div', { class: 'compare-detail' })
  const variants = [...row.by.entries()]

  if (!row.differs) {
    const patch = variants[0][1]
    wrap.appendChild(el('div', { class: 'compare-detail-note' }, [
      `Identical in ${row.count} bundle${row.count !== 1 ? 's' : ''}`,
    ]))
    wrap.appendChild(el('p', { class: 'compare-detail-desc' }, [patch.description || 'No description']))
    const meta = []
    if (patch.compatible_versions?.length) meta.push(`Versions: ${patch.compatible_versions.join(', ')}`)
    if (patch.options?.length) meta.push(`Options: ${patch.options.length}`)
    if (meta.length) wrap.appendChild(el('div', { class: 'compare-detail-meta' }, [meta.join('   ·   ')]))
    return wrap
  }

  wrap.appendChild(el('div', { class: 'compare-detail-note' }, ['Content differs between bundles']))
  const [baseCol, basePatch] = variants[0]
  const baseVersions = [...(basePatch.compatible_versions || [])].join(', ')

  for (const [ci, patch] of variants) {
    const col = cols[ci]
    const card = el('div', { class: 'compare-variant' })

    const head = el('div', { class: 'compare-variant-head' })
    head.innerHTML =
      (col.avatarUrl
        ? `<img class="compare-variant-avatar" src="${escHtml(col.avatarUrl)}" alt="" loading="lazy" onerror="this.style.display='none'">`
        : '') +
      `<span class="compare-variant-name">${escHtml(col.label)}</span>` +
      (col.version ? `<span class="compare-variant-version">${escHtml(formatVersion(col.version))}</span>` : '') +
      (ci === baseCol ? '<span class="compare-variant-ref">reference</span>' : '') +
      (patch.use ? ON_BADGE : '')
    card.appendChild(head)

    const versions = [...(patch.compatible_versions || [])].join(', ')
    const baseDesc = basePatch.description || ''
    const desc = patch.description || ''
    const sameDesc = baseDesc === desc
    const sameVersions = baseVersions === versions

    card.appendChild(el('div', {
      class: 'compare-variant-desc',
      dangerouslySetInnerHTML: sameDesc ? escHtml(desc || 'No description') : wordDiffHtml(baseDesc, desc, escHtml),
    }))
    if (!sameVersions) {
      card.appendChild(el('div', {
        class: 'compare-variant-versions',
        dangerouslySetInnerHTML:
          `<span class="compare-variant-label">Versions:</span> ${wordDiffHtml(baseVersions, versions, escHtml)}`,
      }))
    }
    wrap.appendChild(card)
  }
  return wrap
}

/**
 * Build the summary + filters + matrix.
 *
 * @param {Array<{label: string, avatarUrl?: string, version?: string,
 *   channel?: string, patches: Array<Object>}>} cols
 * @param {Object} [state] mutable view state so filter/expand survive rebuilds
 * @param {'all'|'shared'|'unique'|'differs'} [state.filter]
 * @param {Set<string>} [state.expanded] row keys that are open
 * @returns {HTMLElement}
 */
export function buildCompareMatrix(cols, state = {}) {
  const colCount = cols.length
  const rows = buildCompareRows(cols)

  state.filter = state.filter || 'all'
  if (!state.expanded) state.expanded = new Set()

  const sharedCount = rows.filter((r) => r.shared).length
  const uniqueCount = rows.length - sharedCount
  const differsCount = rows.filter((r) => r.differs).length

  const root = el('div', { class: 'compare-overlay' })

  const summary = el('div', { class: 'compare-summary' })
  summary.innerHTML =
    `<b>${rows.length}</b> patches` +
    ` <span class="compare-summary-sep">·</span> ` +
    `<b class="compare-summary-shared">${sharedCount}</b> shared` +
    ` <span class="compare-summary-sep">·</span> ` +
    `<b>${uniqueCount}</b> unique` +
    ` <span class="compare-summary-sep">·</span> ` +
    `<b class="compare-summary-differs">${differsCount}</b> differ`
  root.appendChild(summary)

  // Channel chip only when it disambiguates (non-stable bundles, or duplicate labels)
  const labelCounts = new Map()
  for (const c of cols) labelCounts.set(c.label, (labelCounts.get(c.label) || 0) + 1)
  const showChannel = cols.some((c) => c.channel && c.channel !== 'stable') || [...labelCounts.values()].some((n) => n > 1)

  const filterDefs = [
    ['all', 'All', rows.length],
    ['shared', 'Shared', sharedCount],
    ['unique', 'Unique', uniqueCount],
    ['differs', 'Differs', differsCount],
  ]
  const filterBar = el('div', { class: 'compare-filters', role: 'group', 'aria-label': 'Filter patches' })
  const filterButtons = []
  const scroll = el('div', { class: 'compare-scroll' })

  function renderTable() {
    scroll.replaceChildren()
    const list = filterRows(rows, state.filter)
    if (!list.length) {
      scroll.appendChild(el('div', { class: 'compare-empty' }, ['No patches match this filter']))
      return
    }

    const table = el('table', { class: 'compare-matrix' })

    const headRow = el('tr')
    headRow.appendChild(el('th', { class: 'compare-th compare-th--name' }, ['Patch']))
    for (const col of cols) {
      const th = el('th', { class: 'compare-th', title: col.label })
      th.innerHTML =
        (col.avatarUrl
          ? `<img class="compare-th-avatar" src="${escHtml(col.avatarUrl)}" alt="" loading="lazy" onerror="this.style.display='none'">`
          : '') +
        `<span class="compare-th-name">${escHtml(col.label)}</span>` +
        (showChannel && col.channel
          ? `<span class="channel-badge channel-badge--sm ${escHtml(col.channel)}">${escHtml(col.channel)}</span>`
          : '')
      headRow.appendChild(th)
    }
    const thead = el('thead')
    thead.appendChild(headRow)
    table.appendChild(thead)

    const tbody = el('tbody')
    let lastGroup = null
    for (const row of list) {
      const group = row.shared ? 'shared' : 'unique'
      if (group !== lastGroup) {
        lastGroup = group
        const groupRow = el('tr', { class: 'compare-group-row' })
        groupRow.appendChild(el('td', {
          class: 'compare-group-cell',
          colspan: String(colCount + 1),
        }, [
          group === 'shared'
            ? `Shared — in 2 or more of ${colCount} bundles`
            : `Unique — in only 1 of ${colCount} bundles`,
        ]))
        tbody.appendChild(groupRow)
      }

      const isOpen = state.expanded.has(row.key)
      const tr = el('tr', {
        class: `compare-row ${row.shared ? 'compare-row--shared' : 'compare-row--unique'}${isOpen ? ' compare-row--open' : ''}`,
        role: 'button',
        tabindex: '0',
        'aria-expanded': String(isOpen),
      })

      const nameTd = el('td', { class: 'compare-cell-name' })
      nameTd.innerHTML = nameCellHtml(row, colCount)
      tr.appendChild(nameTd)

      for (let ci = 0; ci < colCount; ci++) {
        const present = row.by.has(ci)
        tr.appendChild(el('td', {
          class: `compare-cell ${present ? 'compare-cell--on' : 'compare-cell--off'}`,
          title: `${present ? 'Present in' : 'Not in'} ${cols[ci].label}`,
        }, [el('span', { class: 'compare-dot', 'aria-hidden': 'true' })]))
      }

      const detailTr = el('tr', { class: 'compare-detail-row' })
      const detailTd = el('td', { class: 'compare-detail-cell', colspan: String(colCount + 1) })
      detailTd.appendChild(buildCompareDetail(row, cols))
      detailTr.appendChild(detailTd)
      detailTr.hidden = !isOpen

      const toggle = () => {
        const open = state.expanded.has(row.key)
        if (open) state.expanded.delete(row.key)
        else state.expanded.add(row.key)
        detailTr.hidden = open
        tr.classList.toggle('compare-row--open', !open)
        tr.setAttribute('aria-expanded', String(!open))
      }
      tr.addEventListener('click', toggle)
      tr.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          toggle()
        }
      })

      tbody.appendChild(tr)
      tbody.appendChild(detailTr)
    }

    table.appendChild(tbody)
    scroll.appendChild(table)
  }

  for (const [id, label, count] of filterDefs) {
    const btn = el('button', {
      type: 'button',
      class: `compare-filter${id === state.filter ? ' active' : ''}`,
      onclick: () => {
        state.filter = id
        filterButtons.forEach((b, i) => b.classList.toggle('active', filterDefs[i][0] === id))
        renderTable()
      },
    }, [`${label} (${count})`])
    filterButtons.push(btn)
    filterBar.appendChild(btn)
  }

  root.appendChild(filterBar)
  root.appendChild(scroll)
  renderTable()
  return root
}
