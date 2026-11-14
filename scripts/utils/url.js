/**
 * URL utilities — site constants, repo info, author links, Play Store URLs.
 */
import * as store from '../store.js'

export const SITE_URL = 'https://drnx64.github.io/fresh-morphe'
export const GITHUB_REPO_URL = 'https://github.com/drnx64/fresh-morphe'

export function getRepoInfo(repoUrl) {
  if (!repoUrl) return { isGitLab: false, path: '' }
  const isGitLab = repoUrl.includes('gitlab.com')
  let path = ''
  if (isGitLab) {
    const m = repoUrl.match(/https:\/\/gitlab\.com\/(.+)/)
    if (m) path = m[1].replace(/\.git$/, '').replace(/\/+$/, '')
  } else {
    const m = repoUrl.match(/https:\/\/github\.com\/([^/]+\/[^/]+)/)
    if (m) path = m[1].replace(/\.git$/, '')
  }
  return { isGitLab, path }
}

export function getAuthorLink(repoUrl) {
  if (!repoUrl) return 'unknown'
  const gitlabMatch = repoUrl.match(/https:\/\/gitlab\.com\/([^/]+)/)
  if (gitlabMatch) {
    const author = gitlabMatch[1]
    return `<a href="https://gitlab.com/${author}" target="_blank" class="author-link">@${author}</a>`
  }
  const match = repoUrl.match(/https:\/\/github\.com\/([^/]+)/)
  if (match) {
    const author = match[1]
    return `<a href="https://github.com/${author}" target="_blank" class="author-link">@${author}</a>`
  }
  return 'unknown'
}

/**
 * Ask the GitHub avatar proxy for the size we actually display (≤56px CSS,
 * 112 = 2×) instead of the full-size original. Only GitHub-hosted avatars are
 * touched — other hosts get the URL unchanged.
 * @param {string} url
 * @returns {string}
 */
export function withAvatarSize(url) {
  if (!url || !url.includes('avatars.githubusercontent.com')) return url
  if (/[?&](size|s)=\d+/.test(url)) return url
  return `${url}${url.includes('?') ? '&' : '?'}size=112`
}

/**
 * Play Store icon CDN: `=s0` serves the untouched 512px original (up to ~200 KB);
 * `=s96` matches the cache's resize cap and cuts the transfer ~15×. Non-Play
 * URLs and URLs without an `=s0` hint pass through unchanged.
 * @param {string} url
 * @returns {string}
 */
export function shrinkPlayIconUrl(url) {
  if (typeof url !== 'string' || !url.includes('play-lh.googleusercontent.com')) return url
  return url.replace(/=s0(?=-|$)/, '=s96')
}

/**
 * Resolve owner/repo avatar URL from repoAvatarMap (repo_cache) or bundle field.
 * @param {string} repoUrl
 * @param {string} [fallback] - bundle.avatarUrl when map has no entry
 * @returns {string}
 */
export function resolveAvatarUrl(repoUrl, fallback = '') {
  if (repoUrl) {
    const map = store.get('repoAvatarMap') || {}
    const fromMap = map[repoUrl]
    if (fromMap) return withAvatarSize(fromMap)
  }
  return withAvatarSize(fallback || '')
}

/**
 * Resolve a bundle's cover image (patches-bundle.png) URL.
 * Priority: repo_cache map (repo_cache.json) → bundle record field.
 * @param {string} repoUrl
 * @param {string} [bundleField] - bundle.bundleImageUrl when map has no entry
 * @returns {string} '' when no bundle image exists
 */
export function resolveBundleImage(repoUrl, bundleField = '') {
  if (repoUrl) {
    const map = store.get('repoBundleImageMap') || {}
    const fromMap = map[repoUrl]
    if (fromMap) return fromMap
  }
  return bundleField || ''
}

export function getAddMorpheUrl(repoUrl) {
  const info = getRepoInfo(repoUrl)
  const param = info.isGitLab ? 'gitlab' : 'github'
  return `https://morphe.software/add-source?${param}=${encodeURIComponent(info.path)}`
}

/**
 * App-aware add-source deep link: one app, its bundle's repo, its name and
 * icon, so Morphe can prefill the source it adds. The owner/repo slash stays
 * literal (segments are encoded individually) to match the published link
 * format. Returns '' when the repo URL has no usable path — the caller keeps
 * its deep-link fallback for that case.
 * @param {string} repoUrl - bundle.repo_url
 * @param {Object} [appInfo]
 * @param {string} [appInfo.pkg] - package id (`app` param)
 * @param {string} [appInfo.appName] - display name (`appName` param)
 * @param {string} [appInfo.iconUrl] - remote http(s) icon URL (`appIcon` param)
 * @returns {string}
 */
export function getAppAddMorpheUrl(repoUrl, { pkg = '', appName = '', iconUrl = '' } = {}) {
  const info = getRepoInfo(repoUrl)
  if (!info.path) return ''
  const param = info.isGitLab ? 'gitlab' : 'github'
  const path = info.path.split('/').map(encodeURIComponent).join('/')
  const query = [`${param}=${path}`]
  if (pkg) query.push(`app=${encodeURIComponent(pkg)}`)
  if (appName) query.push(`appName=${encodeURIComponent(appName)}`)
  if (iconUrl && /^https?:/i.test(iconUrl)) query.push(`appIcon=${encodeURIComponent(iconUrl)}`)
  return `https://morphe.software/add-source?${query.join('&')}`
}

export function getPlayStoreUrl(packageName) {
  return `https://play.google.com/store/apps/details?id=${encodeURIComponent(packageName)}`
}
