/**
 * Bundle detail page — hero, stats, channel versions, and app list.
 */
import { el, mount } from '../ui.js'
import * as store from '../store.js'
import {
  resolveAppName,
  getStaleness,
  renderAppIcon,
  getDisplayAvatar,
  getDisplayBundleImage,
  avatarStackHtml,
  normalizeRepoUrl,
} from '../utils/misc.js'
import { ensureBundleKey } from '../services/bundleLoader.js'
import { makeBundleKey, parseBundleKey } from '../utils/bundleKey.js'
import { getRepoInfo, getAddMorpheUrl, getAuthorLink } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { formatVersion, getTimeAgo } from '../utils/format.js'
import { GITHUB_SVG, GITLAB_SVG, ARROW_LEFT } from '../utils/svg.js'

const CHANNEL_LABEL = { stable: 'Stable', latest: 'Latest', dev: 'Dev' }

function compactStars(stars) {
  if (!stars) return '0'
  return stars >= 1000 ? `${(stars / 1000).toFixed(1)}k` : String(stars)
}

function patchCount(record) {
  return (record?.apps || []).reduce((n, app) => n + (app.patches?.length || 0), 0)
}

function fact(value, label, title = '') {
  return el('div', { class: 'bundle-fact', title: title || null }, [
    el('span', { class: 'bundle-fact-value' }, [String(value)]),
    el('span', { class: 'bundle-fact-label' }, [label]),
  ])
}

function newestFirst(a, b) {
  const aDate = Date.parse(a.record.release_date || '') || 0
  const bDate = Date.parse(b.record.release_date || '') || 0
  if (aDate !== bDate) return bDate - aDate
  return String(b.record.version || '').localeCompare(String(a.record.version || ''), undefined, { numeric: true })
}

export async function renderBundleDetail(container, bundleName) {
  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}

  // The same repo can be indexed under the crawler name and the release name.
  // Resolve by repo URL so one page shows every channel exactly once.
  const collectRecords = () => {
    const bundles = store.get('bundles') || {}
    const routeRecord = bundles[makeBundleKey(bundleName, 'stable')]
      || bundles[makeBundleKey(bundleName, 'latest')]
      || bundles[makeBundleKey(bundleName, 'dev')]
    if (!routeRecord) return null
    const repoKey = normalizeRepoUrl(routeRecord.repo_url)
    const records = []
    for (const [key, record] of Object.entries(bundles)) {
      const { name, channel } = parseBundleKey(key)
      const sameRepo = repoKey
        ? normalizeRepoUrl(record.repo_url) === repoKey
        : name === bundleName
      if (sameRepo) records.push({ name, channel, record })
    }
    return records.sort(newestFirst)
  }

  const initial = collectRecords()
  if (!initial?.length) {
    mount(container, el('div', { class: 'loading-state' }, [`Bundle "${bundleName}" not found.`]))
    return
  }

  // Patch detail lives in the per-bundle file — the route paints its shell
  // from the index first, then fills in once the file lands (usually cached,
  // so this resolves before the browser paints the placeholder).
  const primaryKey = makeBundleKey(initial[0].name, initial[0].channel)
  const skeleton = el('div', { class: 'bundle-detail-page' }, [
    el('div', { class: 'loading-state' }, ['Loading bundle…']),
  ])
  mount(container, skeleton)
  await ensureBundleKey(primaryKey)
  if (!skeleton.isConnected) return // user navigated away while it loaded

  const records = collectRecords() || initial
  const channels = ['stable', 'latest', 'dev']
    .map((channel) => ({ channel, entry: records.find((entry) => entry.channel === channel) }))
    .filter((row) => row.entry)
    .map((row) => ({ channel: row.channel, record: row.entry.record }))

  const bundle = records[0].record

  const page = el('div', { class: 'bundle-detail-page' })

  const backLink = el('a', { href: '#/bundles', class: 'back-link' })
  backLink.innerHTML = `${ARROW_LEFT} Back to Bundles`
  page.appendChild(backLink)

  // ── Hero ────────────────────────────────────────────
  const repoInfo = getRepoInfo(bundle.repo_url)
  const iconSvg = repoInfo.isGitLab ? GITLAB_SVG : GITHUB_SVG
  const addMorpheUrl = getAddMorpheUrl(bundle.repo_url)
  const displayName = bundle.patches_name || records[0].name
  const apps = [...(bundle.apps || [])].sort((x, y) =>
    resolveAppName(x, nameCache).localeCompare(resolveAppName(y, nameCache)),
  )

  const avatarUrl = getDisplayAvatar(bundle.repo_url, bundle.avatarUrl)
  const bundleImageUrl = getDisplayBundleImage(bundle.repo_url, bundle.bundleImageUrl)
  const avatarHtml = avatarStackHtml(
    bundleImageUrl,
    avatarUrl,
    `<span>${displayName.charAt(0).toUpperCase()}</span>`,
    'bundle-hero-avatar-img',
    'bundle-hero-avatar-ph',
  )

  const staleness = getStaleness(bundle.release_date)
  const badges = el('div', { class: 'bundle-hero-badges' })
  for (const { channel } of channels) {
    badges.appendChild(el('span', { class: `channel-badge ${channel}` }, [channel]))
  }
  if (bundle.version) badges.appendChild(el('span', { class: 'bundle-version-tag' }, [formatVersion(bundle.version)]))
  if (bundle.stars) {
    badges.appendChild(el('span', { class: 'bundle-stars-badge', title: `${bundle.stars} stars` }, [`★ ${compactStars(bundle.stars)}`]))
  }
  if (bundle.isPreRelease) badges.appendChild(el('span', { class: 'bundle-pre-release-badge' }, ['Pre-release']))
  if (bundle.isArchived) badges.appendChild(el('span', { class: 'bundle-archived-badge', title: 'This repository is archived' }, ['Archived']))
  if (bundle.isFork) {
    const forkOwner = String(bundle.forkParent || '').split('/')[0]
    badges.appendChild(el('span', {
      class: 'bundle-fork-badge',
      title: bundle.forkParent ? `Forked from ${bundle.forkParent}` : 'Fork of another patch bundle',
    }, [forkOwner ? `Fork of @${forkOwner}` : 'Fork']))
  }
  if (staleness) {
    badges.appendChild(el('span', {
      class: `staleness-badge staleness--${staleness.level}`,
      title: `Released ${bundle.release_date || 'unknown'}`,
    }, [`${staleness.label} old`]))
  }

  const hero = el('div', { class: 'bundle-hero' }, [
    el('div', { class: 'bundle-hero-avatar', dangerouslySetInnerHTML: avatarHtml }),
    el('div', { class: 'bundle-hero-info' }, [
      el('h1', { class: 'bundle-hero-title' }, [displayName]),
      el('div', { class: 'bundle-hero-author', dangerouslySetInnerHTML: `by ${getAuthorLink(bundle.repo_url)}` }),
      bundle.repoDescription
        ? el('p', { class: 'bundle-hero-desc' }, [bundle.repoDescription])
        : null,
      badges,
    ].filter(Boolean)),
    el('div', { class: 'bundle-hero-actions' }, [
      (() => {
        const btn = el('a', { href: bundle.repo_url, class: 'btn btn--secondary', target: '_blank', rel: 'noopener' })
        btn.innerHTML = `${iconSvg} View Source`
        return btn
      })(),
      el('a', { href: addMorpheUrl, class: 'btn btn--primary', target: '_blank', rel: 'noopener' }, ['Add to Morphe']),
    ]),
  ])
  page.appendChild(hero)

  // ── Facts ───────────────────────────────────────────
  const appCount = Math.max(apps.length, bundle.app_count || 0)
  const patches = patchCount(bundle)
  const facts = el('div', { class: 'bundle-facts' }, [
    fact(appCount, appCount === 1 ? 'App' : 'Apps'),
    fact(patches, patches === 1 ? 'Patch' : 'Patches'),
    fact(compactStars(bundle.stars), bundle.stars === 1 ? 'Star' : 'Stars'),
    fact(getTimeAgo(bundle.release_date), 'Last release', bundle.release_date || ''),
  ])
  page.appendChild(facts)

  // ── Channels ────────────────────────────────────────
  const channelRows = channels.map(({ channel, record }) => {
    const recordApps = Math.max((record.apps || []).length, record.app_count || 0)
    const flags = []
    if (record.isPreRelease) flags.push(el('span', { class: 'bundle-pre-release-badge' }, ['Pre-release']))
    if (record.isArchived) flags.push(el('span', { class: 'bundle-archived-badge' }, ['Archived']))
    return el('div', { class: 'bundle-channel-row' }, [
      el('span', { class: `channel-badge ${channel}` }, [CHANNEL_LABEL[channel]]),
      el('div', { class: 'bundle-channel-main' }, [
        el('span', { class: 'bundle-channel-version' }, [formatVersion(record.version) || 'unknown']),
        ...flags,
      ]),
      el('span', { class: 'bundle-channel-sub' }, [
        `${recordApps} app${recordApps === 1 ? '' : 's'}`,
        record.release_date ? ` · ${getTimeAgo(record.release_date)}` : '',
      ].join('')),
    ])
  })

  const channelPanel = el('section', { class: 'bundle-panel' }, [
    el('h2', { class: 'bundle-panel-title' }, ['Channels']),
    el('div', { class: 'bundle-channel-list' }, channelRows),
  ])
  page.appendChild(channelPanel)

  // ── Apps ────────────────────────────────────────────
  const appsSection = el('div', { class: 'bundle-detail-apps' }, [
    el('div', { class: 'bundle-apps-heading' }, [
      el('h2', { class: 'section-title' }, [`${appCount} Compatible App${appCount !== 1 ? 's' : ''}`]),
      patches ? el('span', { class: 'bundle-apps-sub' }, [`${patches} patches in this bundle`]) : null,
    ].filter(Boolean)),
  ])

  const appsGrid = el('div', { class: 'bundle-detail-apps-grid' })
  for (const app of apps) {
    const card = el('div', { class: 'app-card' })
    const iconHtml = renderAppIcon(app, iconCache)

    const count = app.patches?.length || 0
    card.innerHTML = `
      <div class="app-card-main">
        ${iconHtml}
        <div class="app-card-info">
          <span class="app-card-name">${escHtml(resolveAppName(app, nameCache))}</span>
          <span class="app-card-pkg">${escHtml(app.package)}</span>
          <span class="app-card-patches">${count} patch${count !== 1 ? 'es' : ''}</span>
        </div>
      </div>
    `
    card.style.cursor = 'pointer'
    card.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('open-app', {
        detail: { app, bundleName: records[0].name, channels: channels.map((entry) => entry.channel) },
      }))
    })
    appsGrid.appendChild(card)
  }
  appsSection.appendChild(appsGrid)
  page.appendChild(appsSection)

  mount(container, page)
}
