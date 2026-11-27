/**
 * Header source indicator — which dataset is answering right now.
 *
 *   Live    the phone API (`data/fresh.db`, refreshed every hour)
 *   Static  the GitHub Pages snapshot, used when the API is unreachable
 *
 * Small on purpose: a pill in the top bar, not a banner. It reads the boot
 * `dataSource` from the store and flips if the API circuit breaker changes
 * later in the session.
 */
import { el } from '../ui.js'
import * as store from '../store.js'
import { API_ENABLED } from '../config.js'
import { isApiAvailable, onApiStateChange } from '../services/apiState.js'
import { devLog } from '../utils/devLog.js'

const STATES = {
  api: {
    label: 'Live',
    className: 'is-live',
    title: 'Live — reading the phone database (updated hourly)',
  },
  static: {
    label: 'Static',
    className: 'is-static',
    title: 'Static — phone API unreachable, showing the last Pages snapshot',
  },
}

export function renderSourceIndicator() {
  const pill = el('span', {
    class: 'source-indicator',
    role: 'status',
    hidden: 'hidden',
  })

  function paint() {
    if (!API_ENABLED) {
      pill.hidden = true
      return
    }
    const source = store.get('dataSource')
    if (source !== 'api' && source !== 'static') {
      pill.hidden = true
      return
    }
    // A later API failure overrides the boot result for this session.
    const mode = source === 'api' && isApiAvailable() ? 'api' : 'static'
    const state = STATES[mode]
    pill.hidden = false
    pill.className = `source-indicator ${state.className}`
    pill.title = state.title
    pill.replaceChildren(
      el('span', { class: 'source-indicator-dot', 'aria-hidden': 'true' }),
      el('span', { class: 'source-indicator-label' }, [state.label]),
    )
  }

  paint()
  store.subscribe('dataSource', () => {
    paint()
    devLog('source indicator', store.get('dataSource'))
  })
  onApiStateChange(() => paint())

  return pill
}
