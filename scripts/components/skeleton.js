/**
 * Skeleton loading placeholders.
 */
import { el } from '../ui.js'

export function skeletonBlock() {
  return el('div', { class: 'skeleton-block' })
}

export function skeletonText(lines = 3) {
  const container = el('div', { class: 'skeleton-text' })
  for (let i = 0; i < lines; i++) {
    container.appendChild(el('div', { class: 'skeleton-line', style: { width: `${60 + Math.random() * 40}%` } }))
  }
  return container
}

export function skeletonCard() {
  return el('div', { class: 'skeleton-card' }, [
    skeletonBlock(),
    skeletonText(2),
  ])
}

export function skeletonGrid(count = 6) {
  const grid = el('div', { class: 'skeleton-grid' })
  for (let i = 0; i < count; i++) {
    grid.appendChild(skeletonCard())
  }
  return grid
}

export function skeletonStats() {
  const container = el('div', { class: 'skeleton-stats' })
  for (let i = 0; i < 4; i++) {
    container.appendChild(el('div', { class: 'skeleton-stat-card' }, [skeletonBlock()]))
  }
  return container
}
