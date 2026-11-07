/**
 * HTML string escaping with entity decoding.
 * Decodes common HTML entities first, then re-escapes for safe insertion.
 */
const ENTITY_MAP = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&#39;': "'",
  '&#x27;': "'",
  '&apos;': "'",
  '&nbsp;': ' ',
  '&middot;': '·',
  '&hellip;': '…',
  '&mdash;': '—',
  '&ndash;': '–',
  '&copy;': '©',
  '&reg;': '®',
  '&trade;': '™',
  '&euro;': '€',
  '&pound;': '£',
  '&yen;': '¥',
  '&cent;': '¢',
  '&deg;': '°',
  '&plusmn;': '±',
  '&times;': '×',
  '&divide;': '÷',
  '&para;': '¶',
  '&sect;': '§',
  '&dagger;': '†',
  '&Dagger;': '‡',
  '&lsquo;': '\u2018',
  '&rsquo;': '\u2019',
  '&ldquo;': '\u201C',
  '&rdquo;': '\u201D',
  '&bull;': '•',
  '&rarr;': '→',
  '&larr;': '←',
  '&hearts;': '♥',
  '&diams;': '◆',
}

function decodeEntities(str) {
  return str.replace(/&(#x?[0-9a-fA-F]+|\w+);/g, (match) => ENTITY_MAP[match] || match)
}

export function escHtml(str) {
  if (!str) return ''
  return decodeEntities(String(str))
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
