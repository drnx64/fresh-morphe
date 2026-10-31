/**
 * Changelog page — historical changelog, collapsed by day.
 */
import { el, mount } from '../ui.js'
import * as store from '../store.js'
import { renderAppIcon, groupAffectedBundles, resolveAppName } from '../utils/misc.js'
import { escHtml } from '../utils/html.js'
import { dateParts, dayHue, badgeLabel } from '../utils/format.js'
import { TELEGRAM_ICON, CHEVRON_DOWN } from '../utils/svg.js'

const HIDE_DEV_KEY = 'fresh_hide_dev'

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

function buildDayBody(bundles, iconCache, nameCache) {
  const fragment = document.createDocumentFragment()

  for (const bundle of Object.values(groupAffectedBundles(bundles))) {
    const bundleEl = el('div', { class: 'changelog-bundle' })
    bundleEl.appendChild(el('span', { class: 'changelog-bundle-name' }, [bundle.patches_name || bundle.bundle]))

    for (const app of bundle.apps || []) {
      const appEl = el('div', { class: 'changelog-app changelog-app--clickable' })
      const badgeClass = app.badge_type ? `badge--${app.badge_type.toLowerCase().replace(/\s+/g, '-')}` : ''
      const iconHtml = renderAppIcon(app, iconCache, 'sm')
      appEl.innerHTML = `
        ${app.badge_type ? `<span class="badge ${badgeClass}">${escHtml(badgeLabel(app.badge_type))}</span>` : ''}
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
    fragment.appendChild(bundleEl)
  }

  return fragment
}

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

  const iconCache = store.get('iconCache') || {}
  const nameCache = store.get('nameCache') || {}

  const contentArea = el('div', { class: 'changelog-content' })
  page.appendChild(contentArea)
  mount(container, page)

  changelog.forEach((day, index) => {
    const bundles = (day.affected_bundles || []).filter((b) => !(hideDev && b.channel === 'dev'))
    const appCount = bundles.reduce((n, b) => n + (b.apps || []).length, 0)
    const bodyId = `changelog-day-${index}`

    const dateEl = el('span', { class: 'changelog-date' }, [
      el('span', { class: 'changelog-date-day' }, [dateParts(day.date).day]),
      ` ${dateParts(day.date).month}, ${dateParts(day.date).year}`,
    ])
    dateEl.style.setProperty('--date-hue', String(dayHue(day.date)))

    const toggle = el('button', {
      type: 'button',
      class: 'changelog-day-toggle',
      'aria-expanded': 'false',
      'aria-controls': bodyId,
    }, [
      dateEl,
      el('span', { class: 'changelog-day-meta' }, [
        plural(bundles.length, 'bundle'),
        ' · ',
        plural(appCount, 'app'),
      ]),
      el('span', {
        class: 'changelog-day-chevron',
        'aria-hidden': 'true',
        dangerouslySetInnerHTML: CHEVRON_DOWN,
      }),
    ])

    const body = el('div', { class: 'changelog-day-body', id: bodyId })
    body.hidden = true
    let built = false

    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') === 'true'
      if (!open && !built) {
        body.appendChild(buildDayBody(bundles, iconCache, nameCache))
        built = true
      }
      toggle.setAttribute('aria-expanded', String(!open))
      body.hidden = open
      dayEl.classList.toggle('open', !open)
    })

    const dayEl = el('div', { class: 'changelog-day' }, [
      el('h3', { class: 'changelog-day-heading' }, [toggle]),
      body,
    ])
    contentArea.appendChild(dayEl)
  })
}
