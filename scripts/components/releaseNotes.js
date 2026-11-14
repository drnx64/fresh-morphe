/**
 * Shared release-note rendering for bundle detail and history.
 */
import {
  parseReleaseNotes,
  renderMarkdown,
  renderReleaseSections,
  stripVersionHeader,
} from '../services/releaseParser.js'

export function releaseNotesHtml(text) {
  const clean = stripVersionHeader(text || '')
  if (!clean) return ''
  const sections = renderReleaseSections(parseReleaseNotes(clean))
  if (sections) return sections
  return `<div class="release-section release-section--other">
    <div class="release-section-header">Release notes</div>
    <div class="release-section-markdown">${renderMarkdown(clean)}</div>
  </div>`
}

export function releaseDateLabel(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}
