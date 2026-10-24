/**
 * Work toast — stacked bottom-right toasts for background work
 * (progress, failure counts, retry actions). Non-blocking by design:
 * background tasks never gate the page, they report through here.
 */
import { el } from '../ui.js'

let stack = null

function ensureStack() {
  if (!stack || !stack.isConnected) {
    stack = el('div', { class: 'work-toast-stack' })
    document.body.appendChild(stack)
  }
  return stack
}

/**
 * Show a work toast. Returns a handle for driving it.
 * @param {string} title
 * @returns {{
 *   setTitle: (text: string) => void,
 *   setProgress: (done: number, total: number) => void,
 *   setDetail: (text: string) => void,
 *   setActions: (actions: Array<{label: string, primary?: boolean, onClick: Function}>) => void,
 *   finish: (text?: string, opts?: {autoCloseMs?: number, fillBar?: boolean}) => void,
 *   close: () => void,
 *   isClosed: () => boolean,
 * }}
 */
export function showWorkToast(title) {
  const container = el('div', { class: 'work-toast', role: 'status' })
  const titleEl = el('div', { class: 'work-toast-title' }, [title])
  const detailEl = el('div', { class: 'work-toast-detail' })
  const barTrack = el('div', { class: 'work-toast-bar', style: { display: 'none' } })
  const barFill = el('div', { class: 'work-toast-bar-fill' })
  barTrack.appendChild(barFill)
  const actionsEl = el('div', { class: 'work-toast-actions' })
  const closeBtn = el('button', { class: 'work-toast-close', 'aria-label': 'Dismiss notification' }, ['✕'])

  container.append(titleEl, detailEl, barTrack, actionsEl, closeBtn)
  ensureStack().appendChild(container)
  requestAnimationFrame(() => container.classList.add('open'))

  let closed = false
  let autoCloseTimer = null

  const toast = {
    setTitle(text) {
      titleEl.textContent = text
    },
    setProgress(done, total) {
      barTrack.style.display = ''
      const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
      barFill.style.width = `${pct}%`
      detailEl.textContent = `${done} / ${total}`
    },
    setDetail(text) {
      detailEl.textContent = text
    },
    setActions(actions) {
      actionsEl.replaceChildren()
      for (const action of actions) {
        const btn = el('button', { class: `work-toast-btn${action.primary ? ' work-toast-btn--primary' : ''}` }, [action.label])
        btn.addEventListener('click', action.onClick)
        actionsEl.appendChild(btn)
      }
    },
    finish(text, { autoCloseMs = 3000, fillBar = true } = {}) {
      if (fillBar) barFill.style.width = '100%'
      if (text) detailEl.textContent = text
      toast.setActions([])
      clearTimeout(autoCloseTimer)
      autoCloseTimer = setTimeout(() => toast.close(), autoCloseMs)
    },
    close() {
      if (closed) return
      closed = true
      clearTimeout(autoCloseTimer)
      container.classList.remove('open')
      setTimeout(() => container.remove(), 250)
    },
    isClosed() {
      return closed
    },
  }

  closeBtn.addEventListener('click', toast.close)
  return toast
}
