/**
 * Bundle detail page — full bundle view with app list.
 */
import { el, mount } from '../ui.js'
import * as store from '../store.js'
import { resolveAppName, getAppIconUrl, getStaleness, renderAppIcon } from '../utils/misc.js'
import { ensureBundleKey } from '../services/bundleLoader.js'
import { makeBundleKey } from '../utils/bundleKey.js'
import { getRepoInfo, getAddMorpheUrl, getAuthorLink } from '../utils/url.js'
import { escHtml } from '../utils/html.js'
import { formatVersion } from '../utils/format.js'
import { GITHUB_SVG, GITLAB_SVG, ARROW_LEFT } from '../utils/svg.js'

export async function renderBundleDetail(container, bundleName) {
  const bundles = store.get('bundles') || {}
  const nameCache = store.get('nameCache') || {}
  const iconCache = store.get('iconCache') || {}

  // Find all channel versions (stable is primary; latest-only bundles work too)
  const primaryKeys = ['stable', 'latest', 'dev'].map((ch) => makeBundleKey(bundleName, ch))
  const primaryKey = primaryKeys.find((key) => bundles[key])

  if (!primaryKey) {
    mount(container, el('div', { class: 'loading-state' }, [`Bundle "${bundleName}" not found.`]))
    return
  }

  // Patch detail lives in the per-bundle file — the route paints its shell
  // from the index first, then fills in once the file lands (usually cached,
  // so this resolves before the browser paints the placeholder).
  const skeleton = el('div', { class: 'bundle-detail-page' }, [
    el('div', { class: 'loading-state' }, ['Loading bundle…']),
  ])
  mount(container, skeleton)
  await ensureBundleKey(primaryKey)
  if (!skeleton.isConnected) return // user navigated away while it loaded

  const stable = bundles[`${bundleName}:stable`]
  const latest = bundles[`${bundleName}:latest`]
  const dev = bundles[`${bundleName}:dev`]
  const bundle = stable || latest || dev

  const page = el('div', { class: 'bundle-detail-page' })

  // Back link
  const backLink = el('a', { href: '#/bundles', class: 'back-link' })
  backLink.innerHTML = `${ARROW_LEFT} Back to Bundles`
  page.appendChild(backLink)

  // Title
  const repoInfo = getRepoInfo(bundle.repo_url)
  const iconSvg = repoInfo.isGitLab ? GITLAB_SVG : GITHUB_SVG
  const addMorpheUrl = getAddMorpheUrl(bundle.repo_url)

  const titleSection = el('div', { class: 'bundle-detail-header' }, [
    el('h1', { class: 'bundle-detail-title' }, [bundle.patches_name || bundleName]),
    el('div', { class: 'bundle-detail-author', dangerouslySetInnerHTML: `by ${getAuthorLink(bundle.repo_url)}` }),
    el('div', { class: 'bundle-detail-meta' }, [
      el('span', { class: 'bundle-detail-version' }, [formatVersion(bundle.version) || 'unknown']),
      el('span', { class: 'bundle-detail-channels' }, [
        stable ? el('span', { class: 'channel-badge stable' }, ['stable']) : null,
        dev ? el('span', { class: 'channel-badge dev' }, ['dev']) : null,
        latest ? el('span', { class: 'channel-badge latest' }, ['latest']) : null,
      ].filter(Boolean)),
    ]),
    el('div', { class: 'bundle-detail-actions' }, [
      (() => {
        const btn = el('a', { href: bundle.repo_url, class: 'btn btn--secondary', target: '_blank', rel: 'noopener' })
        btn.innerHTML = `${iconSvg} View Source`
        return btn
      })(),
      el('a', { href: addMorpheUrl, class: 'btn btn--primary', target: '_blank', rel: 'noopener' }, ['Add to Morphe']),
    ]),
  ])
  page.appendChild(titleSection)

  // App list
  const apps = [...(bundle.apps || [])].sort((x, y) =>
    resolveAppName(x, nameCache).localeCompare(resolveAppName(y, nameCache)),
  )

  const appsSection = el('div', { class: 'bundle-detail-apps' }, [
    el('h2', { class: 'section-title' }, [`${apps.length} Compatible App${apps.length !== 1 ? 's' : ''}`]),
  ])

  const appsGrid = el('div', { class: 'bundle-detail-apps-grid' })
  for (const app of apps) {
    const card = el('div', { class: 'app-card' })
    const iconCache = store.get('iconCache') || {}
    const iconHtml = renderAppIcon(app, iconCache)

    const patchCount = app.patches?.length || 0
    card.innerHTML = `
      <div class="app-card-main">
        ${iconHtml}
        <div class="app-card-info">
          <span class="app-card-name">${escHtml(resolveAppName(app, nameCache))}</span>
          <span class="app-card-pkg">${escHtml(app.package)}</span>
          <span class="app-card-patches">${patchCount} patch${patchCount !== 1 ? 'es' : ''}</span>
        </div>
      </div>
    `
    card.style.cursor = 'pointer'
    card.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('open-app', {
        detail: { app, bundleName, channels: bundle.channels || [bundle.channel] },
      }))
    })
    appsGrid.appendChild(card)
  }
  appsSection.appendChild(appsGrid)
  page.appendChild(appsSection)

  mount(container, page)
}
