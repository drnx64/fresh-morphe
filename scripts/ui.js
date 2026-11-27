/**
 * DOM helpers — lightweight element creation and mounting.
 */

/**
 * Create a DOM element with attributes and children.
 * @param {string} tag
 * @param {Object<string, string|number|boolean|Function>} [attrs]
 * @param {Array<Node|string>} [children]
 * @returns {HTMLElement}
 */
export function el(tag, attrs, children) {
  const node = document.createElement(tag)
  if (attrs) {
    for (const [key, val] of Object.entries(attrs)) {
      if (key.startsWith('on') && typeof val === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), val)
      } else if (key === 'class') {
        node.className = val
      } else if (key === 'dangerouslySetInnerHTML' && typeof val === 'string') {
        node.innerHTML = val
      } else if (key === 'style' && typeof val === 'object') {
        Object.assign(node.style, val)
      } else if (key === 'dataset' && typeof val === 'object') {
        for (const [dk, dv] of Object.entries(val)) {
          node.dataset[dk] = dv
        }
      } else if (typeof val === 'boolean') {
        if (val) node.setAttribute(key, '')
        else node.removeAttribute(key)
      } else if (val != null) {
        node.setAttribute(key, String(val))
      }
    }
  }
  if (children) {
    for (const child of children) {
      if (child == null || child === false) continue
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child)
    }
  }
  return node
}

/**
 * Parse an HTML string into a DOM fragment.
 * @param {string} html
 * @returns {DocumentFragment}
 */
export function html(htmlStr) {
  const t = document.createElement('template')
  t.innerHTML = htmlStr.trim()
  return t.content
}

/**
 * Mount nodes into a target element, replacing existing children.
 * @param {HTMLElement} target
 * @param {Node|Node[]} nodes
 */
export function mount(target, nodes) {
  target.replaceChildren()
  if (!nodes) return
  const arr = Array.isArray(nodes) ? nodes : [nodes]
  for (const n of arr) {
    if (n) target.appendChild(n)
  }
}

