/**
 * Changelog page — historical changelog with pagination.
 */
import { el, mount } from '../ui.js'
import * as store from '../store.js'
import { renderAppIcon, groupAffectedBundles, resolveAppName } from '../utils/misc.js'
import { escHtml } from '../utils/html.js'
import { TELEGRAM_ICON, ARROW_LEFT, ARROW_RIGHT } from '../utils/svg.js'

const HIDE_DEV_KEY = 'morphe_hide_dev'

export function renderChangelog(container) {
  const page = el('div', { class: 'changelog-page' })

  const headingRow = el('div', { class: 'changelog-heading-row' })
  headingRow.appendChild(el('h2', { class: 'section-title' }, ['Changelog']))
  const hideDev = localStorage.getItem(HIDE_DEV_KEY) === 'true'
  const hideDevBtn = el('button', {
    class: `today-hide-dev-btn${hideDev ? ' active' : ''}`,
    type: 'button',
    title: 'Hide dev-channel changes',
    'aria-pressed': String(hideDev),
  }, [hideDev ? 'Dev hidden' : 'Dev'])
  hideDevBtn.addEventListener('click', () => {
    if (localStorage.getItem(HIDE_DEV_KEY) === 'true') localStorage.removeItem(HIDE_DEV_KEY)
    else localStorage.setItem(HIDE_DEV_KEY, 'true')
    renderChangelog(container)
  })
  headingRow.appendChild(hideDevBtn)
  page.appendChild(headingRow)

  const telegramBanner = el('div', { class: 'changelog-telegram' })
  telegramBanner.innerHTML = `
    <div class="changelog-telegram-inner">
      <div class="changelog-telegram-icon-wrap">
        ${TELEGRAM_ICON}
      </div>
      <div class="changelog-telegram-body">
        <span class="changelog-telegram-title">Stay updated!</span>
        <span class="changelog-telegram-subtitle">Get real-time patch changelog updates in your pocket.</span>
      </div>
      <a href="https://t.me/morphepatchtracker" target="_blank" rel="noopener" class="changelog-telegram-cta">
        Join
      </a>
    </div>
    <span class="changelog-telegram-hint">Free · No spam · Leave anytime</span>
  `
  page.appendChild(telegramBanner)

  const changelog = store.get('changelog') || []

  if (changelog.length === 0) {
    mount(container, el('div', { class: 'loading-state' }, ['No changelog data available.']))
    return
  }

  const DAYS_PER_PAGE = 7
  let currentPage = 0
  const totalPages = Math.ceil(changelog.length / DAYS_PER_PAGE)

  const contentArea = el('div', { class: 'changelog-content' })
  const pagination = el('div', { class: 'changelog-pagination' })

  page.appendChild(contentArea)
  page.appendChild(pagination)
  mount(container, page)

  function renderPage() {
    contentArea.replaceChildren()
    pagination.replaceChildren()

    const iconCache = store.get('iconCache') || {}
  const nameCache = store.get('nameCache') || {}
    const start = currentPage * DAYS_PER_PAGE
    const end = Math.min(start + DAYS_PER_PAGE, changelog.length)
    const pageDays = changelog.slice(start, end)

    for (const day of pageDays) {
      const dayEl = el('div', { class: 'changelog-day' })
      dayEl.appendChild(el('h3', { class: 'changelog-date' }, [day.date]))

      for (const bundle of Object.values(groupAffectedBundles(
        (day.affected_bundles || []).filter((b) => !(hideDev && b.channel === 'dev')),
      ))) {
        const bundleEl = el('div', { class: 'changelog-bundle' })
        bundleEl.appendChild(el('span', { class: 'changelog-bundle-name' }, [bundle.patches_name || bundle.bundle]))

        for (const app of bundle.apps || []) {
          const appEl = el('div', { class: 'changelog-app changelog-app--clickable' })
          const badgeClass = app.badge_type ? `badge--${app.badge_type.toLowerCase().replace(/\s+/g, '-')}` : ''
          const iconHtml = renderAppIcon(app, iconCache, 'sm')
          appEl.innerHTML = `
            ${app.badge_type ? `<span class="badge ${badgeClass}">${escHtml(app.badge_type)}</span>` : ''}
            ${iconHtml}
            <span class="changelog-app-name">${escHtml(resolveAppName(app, nameCache))}</span>
          `
          appEl.style.cursor = 'pointer'
          appEl.setAttribute('role', 'button')
          appEl.setAttribute('tabindex', '0')
          const openApp = () => {
            window.dispatchEvent(new CustomEvent('open-app', {
              detail: {
                app: { package: app.package, app_name: app.app_name },
                bundleName: bundle.bundle || '',
              },
            }))
          }
          appEl.addEventListener('click', openApp)
          appEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openApp() }
          })
          bundleEl.appendChild(appEl)
        }
        dayEl.appendChild(bundleEl)
      }
      contentArea.appendChild(dayEl)
    }

    // Pagination
    if (totalPages > 1) {
      if (currentPage > 0) {
        const prevBtn = el('button', { class: 'pagination-btn pagination-btn--prev' })
        prevBtn.innerHTML = `${ARROW_LEFT} Previous`
        prevBtn.addEventListener('click', () => { currentPage--; renderPage() })
        pagination.appendChild(prevBtn)
      }
      pagination.appendChild(el('span', { class: 'pagination-info' }, [`Page ${currentPage + 1} of ${totalPages}`]))
      if (currentPage < totalPages - 1) {
        const nextBtn = el('button', { class: 'pagination-btn pagination-btn--next' })
        nextBtn.innerHTML = `Next ${ARROW_RIGHT}`
        nextBtn.addEventListener('click', () => { currentPage++; renderPage() })
        pagination.appendChild(nextBtn)
      }
    }
  }

  renderPage()
}
