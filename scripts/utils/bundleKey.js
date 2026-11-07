/**
 * Bundle key utilities — canonical parsing/resolution of `name:channel` keys.
 * Channels: stable, dev, latest (all supported everywhere consistently).
 */

export const CHANNELS = ['stable', 'dev', 'latest']

// Preference when reading a bundle's record/patches:
// dev (freshest patches) > latest > stable — preserves the historical
// dev-over-stable behavior and adds latest-only bundle support.
export const CHANNEL_PREFERENCE = ['dev', 'latest', 'stable']

/**
 * Split a `name:channel` bundle key into its parts.
 * Unknown/missing suffixes are treated as `stable`.
 * @param {string} key
 * @returns {{ name: string, channel: string }}
 */
export function parseBundleKey(key = '') {
  const idx = key.lastIndexOf(':')
  if (idx === -1) return { name: key, channel: 'stable' }
  const channel = key.slice(idx + 1)
  if (CHANNELS.includes(channel)) return { name: key.slice(0, idx), channel }
  return { name: key, channel: 'stable' }
}

/**
 * Build a canonical `name:channel` key.
 * @param {string} name
 * @param {string} [channel]
 */
export function makeBundleKey(name, channel = 'stable') {
  return `${name}:${channel}`
}

/**
 * Pick the best channel from a list using CHANNEL_PREFERENCE.
 * @param {string[]} [channels]
 * @returns {string}
 */
export function pickChannel(channels = []) {
  return CHANNEL_PREFERENCE.find((ch) => channels.includes(ch)) || channels[0] || 'stable'
}

/**
 * Find a bundle record in a bundles map by name, honoring a channel hint.
 * Falls back across all known channels when the hint has no record.
 * @param {Object} bundles - map of key -> bundle record
 * @param {string} name - bundle name (without channel)
 * @param {string[]} [channels] - preferred channels (e.g. from a grouping)
 * @returns {{ record: Object|undefined, key: string, channel: string|null }}
 */
export function findBundleRecord(bundles, name, channels = null) {
  const tryOrder = [
    ...(channels || []),
    ...CHANNEL_PREFERENCE.filter((ch) => !(channels || []).includes(ch)),
  ]
  for (const ch of tryOrder) {
    const key = makeBundleKey(name, ch)
    const record = bundles[key]
    if (record) return { record, key, channel: ch }
  }
  return { record: undefined, key: '', channel: null }
}
