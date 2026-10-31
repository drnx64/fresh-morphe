/**
 * GitHub emoji shortcodes for release notes.
 *
 * Patch projects write release notes with `:sparkles:`-style codes, which are
 * meaningless once the markdown is escaped and dumped into the page. This maps
 * the common subset — anything unknown is left verbatim, so a shortcode we do
 * not know never turns into a hole in the text.
 */

/** @type {Record<string, string>} */
const EMOJI = {
  '+1': '👍',
  '-1': '👎',
  '100': '💯',
  alarm_clock: '⏰',
  alien: '👽',
  ambulance: '🚑',
  android: '🤖',
  anger: '💢',
  art: '🎨',
  arrow_backward: '◀️',
  arrow_down: '⬇️',
  arrow_forward: '▶️',
  arrow_left: '⬅️',
  arrow_right: '➡️',
  arrow_up: '⬆️',
  arrows_counterclockwise: '🔄',
  asterisk: '✳️',
  balloon: '🎈',
  bar_chart: '📊',
  beer: '🍺',
  bell: '🔔',
  black_circle: '⚫',
  blue_heart: '💙',
  boom: '💥',
  bookmark: '🔖',
  book: '📖',
  books: '📚',
  broken_heart: '💔',
  bug: '🐛',
  bulb: '💡',
  cake: '🍰',
  calendar: '📆',
  card_file_box: '🗃️',
  chart_with_upwards_trend: '📈',
  checkered_flag: '🏁',
  clipboard: '📋',
  closed_lock_with_key: '🔐',
  cloud: '☁️',
  coffee: '☕',
  computer: '💻',
  construction: '🚧',
  cool: '🆒',
  crown: '👑',
  dart: '🎯',
  dash: '💨',
  eyes: '👀',
  file_folder: '📁',
  fire: '🔥',
  floppy_disk: '💾',
  free: '🆓',
  gear: '⚙️',
  gem: '💎',
  gift: '🎁',
  globe_with_meridians: '🌐',
  globe: '🌐',
  hammer: '🔨',
  hammer_and_wrench: '🛠️',
  heart: '❤️',
  heavy_check_mark: '✔️',
  heavy_exclamation_mark: '❗',
  heavy_minus_sign: '➖',
  heavy_plus_sign: '➕',
  hourglass: '⌛',
  information_source: 'ℹ️',
  iphone: '📱',
  key: '🔑',
  label: '🏷️',
  large_blue_circle: '🔵',
  large_green_circle: '🟢',
  large_orange_circle: '🟠',
  large_yellow_circle: '🟡',
  link: '🔗',
  lock: '🔒',
  loud_sound: '🔊',
  mag: '🔍',
  mag_right: '🔎',
  medal_sports: '🏅',
  mega: '📣',
  memo: '📝',
  moneybag: '💰',
  moon: '🌙',
  muscle: '💪',
  negative_squared_cross_mark: '❎',
  new: '🆕',
  no_entry: '⛔',
  no_entry_sign: '🚫',
  ok: '🆗',
  ok_hand: '👌',
  package: '📦',
  paperclip: '📎',
  pencil2: '✏️',
  pizza: '🍕',
  pray: '🙏',
  pushpin: '📌',
  question: '❓',
  recycle: '♻️',
  red_circle: '🔴',
  repeat: '🔁',
  rocket: '🚀',
  rotating_light: '🚨',
  round_pushpin: '📍',
  see_no_evil: '🙈',
  shield: '🛡️',
  snowflake: '❄️',
  soon: '🔜',
  sparkles: '✨',
  sparkling_heart: '💖',
  speech_balloon: '💬',
  star: '⭐',
  star2: '🌟',
  stop_sign: '🛑',
  sunny: '☀️',
  tada: '🎉',
  thought_balloon: '💭',
  thumbsdown: '👎',
  thumbsup: '👍',
  top: '🔝',
  triangular_flag_on_post: '🚩',
  trophy: '🏆',
  twisted_rightwards_arrows: '🔀',
  umbrella: '☂️',
  unlock: '🔓',
  up: '🆙',
  warning: '⚠️',
  wave: '👋',
  white_check_mark: '✅',
  white_circle: '⚪',
  wrench: '🔧',
  x: '❌',
  zap: '⚡',
}

const SHORTCODE = /:([a-z0-9_+-]+):/gi

/**
 * Replace known `:shortcode:` tokens with their emoji.
 * @param {string} text
 * @returns {string}
 */
export function renderEmoji(text) {
  if (!text) return ''
  return String(text).replace(SHORTCODE, (match, name) => EMOJI[name.toLowerCase()] || match)
}

/**
 * Pull a leading emoji (or shortcode) off a heading so a remapped section label
 * keeps the author's flourish.
 * @param {string} heading
 * @returns {{ emoji: string, rest: string }}
 */
export function splitLeadingEmoji(heading) {
  const text = String(heading || '')
  const match = text.match(/^(\p{Extended_Pictographic}(?:\uFE0F|\u200D\p{Extended_Pictographic})*|:[a-z0-9_+-]+:)\s*/iu)
  if (!match) return { emoji: '', rest: text }
  return { emoji: renderEmoji(match[1]), rest: text.slice(match[0].length) }
}
