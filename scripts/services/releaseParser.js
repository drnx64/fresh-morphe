/**
 * Release notes parser — markdown to structured sections + HTML rendering.
 */
import { escHtml } from '../utils/html.js'
import { renderEmoji, splitLeadingEmoji } from '../utils/emoji.js'

function cleanEntryDesc(str) {
  if (!str) return ''
  return str
    .replace(/\s*\(\[#[0-9]+\]\([^)]+\)\)\s*$/, '')
    .replace(/\s*\[\#[0-9]+\]\([^)]+\)\s*$/, '')
    .replace(/\s*\(#[0-9]+\)\s*$/, '')
    .trim()
}

function isStructuredSection(lines) {
  const nonEmpty = lines.filter((l) => l.trim())
  if (nonEmpty.length === 0) return false
  const listItems = nonEmpty.filter((l) => /^[*\-]/.test(l)).length
  if (listItems / nonEmpty.length <= 0.5) return false
  const listTexts = nonEmpty.filter((l) => /^[*\-]/.test(l)).map((l) => l.replace(/^[\s]*[-*]\s+/, ''))
  const structuredItems = listTexts.filter((t) => /^\*\*[^*]+\*\*/.test(t)).length
  return structuredItems / listTexts.length > 0.5
}

function parseStructuredEntries(raw, section) {
  const entries = raw.map((l) => l.replace(/^[\s]*[-*]\s+/, ''))

  for (const text of entries) {
    let commitLink = ''
    let body = text

    const commitMatch = text.match(/\s*\(\[([a-f0-9]{6,40})\]\(([^)]+)\)\)\s*$/)
    if (commitMatch) {
      commitLink = commitMatch[0].trim()
      body = text.slice(0, text.indexOf(commitMatch[0])).trim()
    }

    const scopeMatch = body.match(/^\*\*([^*]+)\*\*:\s*(.+)/)
    if (scopeMatch) {
      section.entries.push({ type: 'change', scope: scopeMatch[1].trim(), description: cleanEntryDesc(scopeMatch[2]), commitLink })
      continue
    }

    const csMatch = body.match(/^(feat|fix|chore|docs|refactor)\(([^)]+)\):\s*(.+)/)
    if (csMatch) {
      section.entries.push({ type: 'change', scope: csMatch[2].trim(), description: cleanEntryDesc(csMatch[3]), changeType: csMatch[1], commitLink })
      continue
    }

    const boldScope = body.match(/^\*\*([^*]+)\*\*\s+(.+)/)
    if (boldScope) {
      section.entries.push({ type: 'change', scope: boldScope[1].trim(), description: cleanEntryDesc(boldScope[2]), commitLink })
      continue
    }

    const actionAppMatch = body.match(/^(add|fix|update|remove|bump|improve)\s+(.+)/i)
    if (actionAppMatch) {
      section.entries.push({ type: 'change', scope: cleanEntryDesc(actionAppMatch[2]), description: actionAppMatch[1].toLowerCase(), changeType: actionAppMatch[1].toLowerCase(), commitLink })
      continue
    }

    if (commitLink) {
      const desc = cleanEntryDesc(body)
      if (desc) {
        section.entries.push({ type: 'change', scope: desc, description: '', commitLink })
        continue
      }
    }

    const colonSplit = body.match(/^([A-Za-z][A-Za-z0-9 ._-]+?):\s*(.+)/)
    if (colonSplit) {
      section.entries.push({ type: 'change', scope: colonSplit[1].trim(), description: cleanEntryDesc(colonSplit[2]), commitLink })
      continue
    }

    const desc = cleanEntryDesc(body)
    if (desc) {
      section.entries.push({ type: 'change', scope: '', description: desc, commitLink })
    }
  }
}

/**
 * A heading that only states a version (`## 1.2.3`, `### **26.9.0**`, `# [v2](url)`)
 * is noise: the bundle page already shows the version. Anything with words in it
 * (`## 1.2.3 - fixes`, `## Features`) is a real section and is kept.
 */
const VERSION_HEADING =
  /^#{1,6}\s+(?:\*\*|__)?(?:\[[^\]]*\]\([^)]*\)|v?\d[\w.\-+]*)(?:\*\*|__)?\s*(?:\([^)]*\))?\s*$/

export function stripVersionHeader(text) {
  if (!text) return ''
  const lines = text.split('\n')
  let idx = 0
  while (idx < lines.length && !lines[idx].trim()) idx++
  if (idx < lines.length) {
    if (VERSION_HEADING.test(lines[idx].trim())) lines.splice(idx, 1)
  }
  return lines.join('\n').trim()
}

export function parseReleaseNotes(text) {
  if (!text) return []
  const sections = []
  const lines = text.split('\n')

  function addLine(heading, line) {
    const s = { heading, rawLines: [line], entries: [], mode: 'structured', markdown: '' }
    sections.push(s)
    return s
  }

  let current = null

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim()

    // Only a version/link heading is noise here — `## Features` used to match the
    // bare-word branch and vanish, taking the whole section with it.
    if (VERSION_HEADING.test(trimmed)) continue
    if (/^\[.+\]\(.+\)/.test(trimmed) && trimmed.includes('|')) continue

    // `#### Added` reaches here as a subsection; strip the whole run of hashes so
    // the label is not literally `#### Added`.
    if (/^#{3,}\s*\S/.test(trimmed)) {
      current = addLine(trimmed.replace(/^#+\s*/, '').trim(), '')
      continue
    }

    if (/^##(?!\s*#)\s*\S/.test(trimmed)) {
      current = addLine(trimmed.replace(/^##\s+/, '').trim(), '')
      continue
    }

    if (/^={2,}\s*$/.test(trimmed) && i > 0) {
      const nameLine = lines[i - 1].trim()
      if (nameLine && !nameLine.startsWith('#') && !nameLine.startsWith('=') && nameLine.length < 60) {
        current = addLine(nameLine, '')
        continue
      }
    }

    if (/^={2,}\s*$/.test(trimmed) || /^-{2,}\s*$/.test(trimmed)) continue

    if (current && current.rawLines) {
      if (trimmed) current.rawLines.push(trimmed)
    } else if (trimmed) {
      current = addLine('Overview', trimmed)
    }
  }

  for (const section of sections) {
    const raw = section.rawLines || []
    if (isStructuredSection(raw)) {
      section.mode = 'structured'
      parseStructuredEntries(raw, section)
    } else {
      section.mode = 'markdown'
      section.markdown = raw.join('\n')
    }
    delete section.rawLines
  }

  return sections.filter((s) => {
    if (s.mode === 'structured') return s.entries.length > 0
    return (s.markdown || '').trim().length > 0
  }).filter((s) => !/^v?[\d]+\.[\d]+/.test(s.heading.trim()))
  .filter((s) => {
    const h = s.heading.toLowerCase().trim()
    const irrelevant = ['overview', 'need help', 'verification', 'verify', 'about', 'usage']
    for (const ir of irrelevant) {
      if (h === ir || h.startsWith(ir) || h.includes(ir)) return false
    }
    return true
  })
}

export function getSectionClass(heading) {
  const h = heading.toLowerCase()
  if (h.includes('bug fix') || h.includes('fix')) return 'release-section--fixes'
  if (h.includes('feature') || h.includes('feat')) return 'release-section--features'
  if (h.includes('support') || h.includes('update')) return 'release-section--support'
  return 'release-section--other'
}

export function getSectionLabel(heading) {
  // `🐛 Bug fixes` used to become the emoji-less `Bug Fixes`; the author's
  // flourish now survives the remap.
  const { emoji, rest } = splitLeadingEmoji(heading)
  const h = rest.toLowerCase()
  let label = rest
  if (h.includes('bug fix') || h.includes('fix')) label = 'Bug Fixes'
  else if (h.includes('feature') || h.includes('feat')) label = 'Features'
  else if (h.includes('support') || h.includes('update')) label = 'Updates'
  else if (h.includes('announce')) label = 'Announcement'
  return emoji ? `${emoji} ${label}` : label
}

export function renderChangeType(type) {
  if (!type) return ''
  const t = type.toLowerCase()
  if (t === 'add' || t === 'bump' || t === 'feat') return '<span class="change-type change-type--add">+</span>'
  if (t === 'fix') return '<span class="change-type change-type--fix">&#10003;</span>'
  if (t === 'remove') return '<span class="change-type change-type--remove">&#8722;</span>'
  if (t === 'update' || t === 'chore' || t === 'refactor') return '<span class="change-type change-type--update">&#8631;</span>'
  if (t === 'improve' || t === 'docs') return '<span class="change-type change-type--improve">&#8593;</span>'
  return ''
}

export function renderCommitLink(linkText) {
  if (!linkText) return ''
  const match = linkText.match(/\(\[([a-f0-9]{6,40})\]\(([^)]+)\)\)/)
  if (match) {
    return `<a href="${escHtml(match[2])}" target="_blank" rel="noopener" class="release-commit-link" title="${escHtml(match[2])}">${escHtml(match[1])}</a>`
  }
  return ''
}

const TABLE_SEPARATOR = /^\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?$/

function splitTableRow(line) {
  let row = line.trim()
  if (row.startsWith('|')) row = row.slice(1)
  if (row.endsWith('|')) row = row.slice(0, -1)
  return row.split('|').map((cell) => cell.trim())
}

function isTableStart(lines, i) {
  const row = lines[i]
  const next = lines[i + 1]
  if (!row || !next || !row.includes('|')) return false
  return next.includes('|') && TABLE_SEPARATOR.test(next.trim()) && next.includes('-')
}

function isListLine(line) {
  return /^\s*(?:[-*+]|\d+[.)])\s+\S/.test(line)
}

/** True when a line opens a block, so paragraph accumulation must stop. */
function startsBlock(lines, i) {
  const t = lines[i].trim()
  if (!t) return true
  if (/^(`{3,}|~{3,})/.test(t)) return true
  if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) return true
  if (/^#{1,6}\s/.test(t)) return true
  if (/^>/.test(t)) return true
  if (isListLine(lines[i])) return true
  return isTableStart(lines, i)
}

/** Only absolute web/mail links survive; anything else becomes an inert `#`. */
function safeHref(url) {
  return /^(?:https?:|mailto:|#|\/)/i.test(String(url).trim()) ? url : '#'
}

/**
 * Inline markdown → HTML.
 *
 * The string is escaped first, so every tag in the output was added here — the
 * source cannot smuggle markup in. Code spans are held aside so a shortcode or
 * `**` inside them stays literal.
 * @param {string} str
 * @returns {string}
 */
export function renderInlineMarkdown(str) {
  if (!str) return ''
  let result = escHtml(str)

  const code = []
  result = result.replace(/`([^`]+)`/g, (_, body) => {
    code.push(body)
    return `\u0000${code.length - 1}\u0000`
  })

  result = renderEmoji(result)
  result = result.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  result = result.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
  result = result.replace(/~~([^~]+)~~/g, '<del>$1</del>')
  result = result.replace(
    /!\[([^\]]*)\]\(([^)]+)\)/g,
    (_, alt, src) => `<img src="${safeHref(src)}" alt="${alt}" loading="lazy">`
  )
  result = result.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    (_, text, href) => `<a href="${safeHref(href)}" target="_blank" rel="noopener">${text}</a>`
  )
  result = result.replace(
    /(^|[\s()>])(https?:\/\/[^\s<)]+)/g,
    '$1<a href="$2" target="_blank" rel="noopener" class="release-link">$2</a>'
  )

  // Authors hard-wrap long lines and sometimes write a literal <br>; both mean
  // "new line" here rather than "join into one sentence".
  result = result.replace(/&lt;br\s*\/?&gt;/gi, '<br>')
  result = result.replace(/\n/g, '<br>')

  return result.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${code[Number(i)]}</code>`)
}

/**
 * Block markdown → HTML for release notes.
 *
 * Line-based on purpose: release notes mix fenced code, headings, task lists and
 * tables far more often than they nest structures, and a line loop handles all
 * of those without a full parser.
 * @param {string} text
 * @returns {string}
 */
export function renderMarkdown(text) {
  if (!text) return ''
  const lines = String(text).replace(/\r\n?/g, '\n').split('\n')
  let html = ''
  let i = 0

  while (i < lines.length) {
    const trimmed = lines[i].trim()
    if (!trimmed) {
      i++
      continue
    }

    // ── fenced code ──────────────────────────────────────
    const fence = trimmed.match(/^(`{3,}|~{3,})\s*([^\s`]*)\s*$/)
    if (fence) {
      const closing = new RegExp(`^${fence[1][0]}{${fence[1].length},}\\s*$`)
      const body = []
      i++
      while (i < lines.length && !closing.test(lines[i].trim())) {
        body.push(lines[i])
        i++
      }
      if (i < lines.length) i++
      const lang = fence[2] ? ` class="language-${escHtml(fence[2])}"` : ''
      html += `<pre class="release-code"><code${lang}>${escHtml(body.join('\n'))}</code></pre>`
      continue
    }

    // ── horizontal rule ──────────────────────────────────
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      html += '<hr>'
      i++
      continue
    }

    // ── heading (the section header is already the visual h3) ──
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      const level = Math.min(heading[1].length + 3, 6)
      html += `<h${level}>${renderInlineMarkdown(heading[2].trim())}</h${level}>`
      i++
      continue
    }

    // ── blockquote ───────────────────────────────────────
    if (/^>/.test(trimmed)) {
      const quote = []
      while (i < lines.length && /^>/.test(lines[i].trim())) {
        quote.push(lines[i].trim().replace(/^>\s?/, ''))
        i++
      }
      html += `<blockquote><p>${renderInlineMarkdown(quote.join('\n'))}</p></blockquote>`
      continue
    }

    // ── table ────────────────────────────────────────────
    if (isTableStart(lines, i)) {
      const head = splitTableRow(lines[i])
      i += 2
      let rows = ''
      while (i < lines.length && lines[i].trim() && lines[i].includes('|') && !/^#{1,6}\s/.test(lines[i].trim())) {
        const cells = splitTableRow(lines[i])
        rows += `<tr>${cells.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join('')}</tr>`
        i++
      }
      html += `<table><thead><tr>${head
        .map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`)
        .join('')}</tr></thead>`
      if (rows) html += `<tbody>${rows}</tbody>`
      html += '</table>'
      continue
    }

    // ── lists (bulleted, ordered, task) ──────────────────
    if (isListLine(lines[i])) {
      const ordered = /^\s*\d+[.)]\s+/.test(lines[i])
      const items = []
      while (i < lines.length && isListLine(lines[i]) && /^\s*\d+[.)]\s+/.test(lines[i]) === ordered) {
        let item = lines[i].replace(/^\s*(?:[-*+]|\d+[.)])\s+/, '')
        let checked = null
        const box = item.match(/^\[([ xX])\]\s*(.*)$/)
        if (box) {
          checked = box[1].toLowerCase() === 'x'
          item = box[2]
        }
        i++
        // Lazy continuation: `parseReleaseNotes` trims every line before it gets
        // here, so indentation alone cannot mark a wrapped line. A non-blank line
        // that does not open a block belongs to the item above it.
        while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) {
          item += `\n${lines[i].trim()}`
          i++
        }
        items.push({ text: item, checked })
      }
      const hasTask = items.some((item) => item.checked !== null)
      const tag = ordered ? 'ol' : 'ul'
      const cls = hasTask && !ordered ? ' class="release-task-list"' : ''
      html += `<${tag}${cls}>`
      for (const item of items) {
        if (item.checked === null) {
          html += `<li>${renderInlineMarkdown(item.text)}</li>`
        } else {
          html += `<li class="release-task${item.checked ? ' is-checked' : ''}">`
          html += `<span class="release-task-box" aria-hidden="true">${item.checked ? '☑' : '☐'}</span>`
          html += `<span class="release-task-text">${renderInlineMarkdown(item.text)}</span></li>`
        }
      }
      html += `</${tag}>`
      continue
    }

    // ── paragraph ────────────────────────────────────────
    const para = []
    while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) {
      para.push(lines[i].trim())
      i++
    }
    if (para.length) {
      html += `<p>${renderInlineMarkdown(para.join('\n'))}</p>`
    } else {
      // Unreachable, but a line loop that cannot advance would hang the page.
      i++
    }
  }

  return html
}

export function renderReleaseSections(parsed) {
  if (!parsed.length) return ''
  let html = ''
  for (const section of parsed) {
    const sectionClass = getSectionClass(section.heading)
    const sectionLabel = getSectionLabel(section.heading)
    html += `<div class="release-section ${sectionClass}">`
    html += `<div class="release-section-header">${renderInlineMarkdown(sectionLabel)}</div>`

    if (section.mode === 'markdown') {
      html += `<div class="release-section-markdown">${renderMarkdown(section.markdown || '')}</div>`
    } else {
      for (const entry of section.entries) {
        if (entry.type === 'change') {
          html += '<div class="release-entry">'
          const ctHtml = renderChangeType(entry.changeType)
          if (ctHtml) html += ctHtml
          if (entry.scope) {
            const parts = entry.scope.split(' - ')
            const appName = parts[0]
            const featureName = parts.slice(1).join(' - ')
            html += `<span class="release-entry-scope">${renderEmoji(escHtml(appName))}</span>`
            if (featureName) html += `<span class="release-entry-feature">${renderEmoji(escHtml(featureName))}</span>`
          }
          if (entry.description && !ctHtml) {
            html += `<span class="release-entry-desc">${renderInlineMarkdown(entry.description)}</span>`
          }
          if (entry.commitLink) html += renderCommitLink(entry.commitLink)
          html += '</div>'
        } else if (entry.type === 'text') {
          html += `<div class="release-entry release-entry--text">${renderInlineMarkdown(entry.text || '')}</div>`
        }
      }
    }
    html += '</div>'
  }
  return html
}
