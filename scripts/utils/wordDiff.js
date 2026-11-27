/**
 * Word-level LCS diff for inline before/after rendering.
 * Segments: { type: 'same' | 'del' | 'add', text: string }
 * Falls back to whole-text del/add when either side exceeds MAX_WORDS tokens.
 */

const MAX_WORDS = 400

function tokenize(text) {
  return text.match(/\S+|\s+/g) || []
}

function lcsSegments(a, b) {
  const n = a.length
  const m = b.length
  const w = m + 1
  const dp = new Uint32Array((n + 1) * w)
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * w + j] = a[i] === b[j]
        ? dp[(i + 1) * w + (j + 1)] + 1
        : Math.max(dp[(i + 1) * w + j], dp[i * w + (j + 1)])
    }
  }

  const segs = []
  const push = (type, text) => {
    const last = segs[segs.length - 1]
    if (last && last.type === type) last.text += text
    else segs.push({ type, text })
  }

  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push('same', a[i])
      i++
      j++
    } else if (dp[(i + 1) * w + j] >= dp[i * w + (j + 1)]) {
      push('del', a[i])
      i++
    } else {
      push('add', b[j])
      j++
    }
  }
  while (i < n) push('del', a[i++])
  while (j < m) push('add', b[j++])
  return segs
}

export function wordDiff(oldText, newText) {
  const oldStr = oldText == null ? '' : String(oldText)
  const newStr = newText == null ? '' : String(newText)
  if (oldStr === newStr) return [{ type: 'same', text: oldStr }]
  const a = tokenize(oldStr)
  const b = tokenize(newStr)
  if (a.length > MAX_WORDS || b.length > MAX_WORDS) {
    const segs = []
    if (oldStr) segs.push({ type: 'del', text: oldStr })
    if (newStr) segs.push({ type: 'add', text: newStr })
    return segs
  }
  return lcsSegments(a, b)
}

export function wordDiffHtml(oldText, newText, esc) {
  let html = ''
  for (const seg of wordDiff(oldText, newText)) {
    if (seg.type === 'same') html += esc(seg.text)
    else html += `<span class="word-diff-${seg.type}">${esc(seg.text)}</span>`
  }
  return html
}
