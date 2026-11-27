/**
 * Modal — accessible focus-trap modal with ESC close.
 * Supports stacking: openModal({ stack: true }) opens above the current modal.
 */
import { el } from '../ui.js'
import { CLOSE_ICON } from '../utils/svg.js'

const modalStack = []

function topModal() {
  return modalStack[modalStack.length - 1] || null
}

/**
 * Open a modal with content.
 * @param {Object} options
 * @param {string} options.title
 * @param {string|Node} options.content
 * @param {string} [options.className]
 * @param {number} [options.maxWidth]
 * @param {boolean} [options.hideHeader]
 * @param {boolean} [options.stack] - open above current modal instead of replacing it
 * @param {Node} [options.headerContent] - replaces the title row content
 * @param {boolean} [options.hideTitle] - omit the text title (keeps close button)
 */
export function openModal({
  title,
  content,
  className = '',
  maxWidth = 700,
  hideHeader = false,
  stack = false,
  headerContent = null,
  hideTitle = false,
}) {
  if (!stack) {
    while (modalStack.length > 0) destroyModal(modalStack.pop())
  }

  const overlay = el('div', { class: `modal-overlay ${className}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title })

  const modalBox = el('div', { class: 'modal-box', style: { maxWidth: maxWidth + 'px' } })

  if (!hideHeader) {
    const header = el('div', { class: `modal-header${headerContent ? ' modal-header--custom' : ''}` })
    if (headerContent) header.appendChild(headerContent)
    if (!hideTitle) header.appendChild(el('h2', { class: 'modal-title' }, [title]))
    header.appendChild(el('button', { class: 'modal-close', 'aria-label': 'Close modal', dangerouslySetInnerHTML: CLOSE_ICON }))
    const closeBtn = header.querySelector('.modal-close')
    closeBtn.addEventListener('click', closeModal)
    modalBox.appendChild(header)
  }

  const body = el('div', { class: 'modal-body' })
  if (typeof content === 'string') {
    body.innerHTML = content
  } else if (content instanceof Node) {
    body.appendChild(content)
  }

  modalBox.appendChild(body)
  overlay.appendChild(modalBox)

  // Overlay click to close (only its own)
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeModal()
  })

  document.addEventListener('keydown', handleEsc)
  document.addEventListener('keydown', handleFocusTrap)

  document.body.appendChild(overlay)
  requestAnimationFrame(() => overlay.classList.add('open'))
  document.body.style.overflow = 'hidden'
  modalStack.push(overlay)

  // Focus trap
  requestAnimationFrame(() => {
    const focusable = overlay.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
    if (focusable.length) focusable[0].focus()
  })

  return overlay
}

function destroyModal(overlay) {
  overlay.classList.remove('open')
  setTimeout(() => overlay.remove(), 300)
}

export function closeModal() {
  const overlay = modalStack.pop()
  if (!overlay) return
  destroyModal(overlay)
  if (modalStack.length === 0) {
    document.body.style.overflow = ''
    document.removeEventListener('keydown', handleEsc)
    document.removeEventListener('keydown', handleFocusTrap)
  } else {
    // Re-focus the modal now on top
    requestAnimationFrame(() => {
      const next = topModal()
      if (!next) return
      const focusable = next.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
      if (focusable.length) focusable[0].focus()
    })
  }
}

function handleEsc(e) {
  if (e.key === 'Escape') closeModal()
}

function handleFocusTrap(e) {
  if (e.key !== 'Tab') return
  const activeModal = topModal()
  if (!activeModal) return
  const focusable = activeModal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
  if (focusable.length === 0) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault()
    first.focus()
  }
}

export function isModalOpen() {
  return modalStack.length > 0
}
