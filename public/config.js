/*
 * Runtime configuration. Kept out of the ES module graph so an operator can
 * point the static site at the phone API without rebuilding.
 *
 * apiBase: public API origin, e.g. "https://morphe.example.com". Leave empty
 *          to use the GitHub Pages JSON snapshots only.
 * fallbackBase: static JSON root, relative to the site root by default.
 */
window.MORPHE_CONFIG = Object.assign(
  {
    apiBase: 'https://certify-slashed-duo.ngrok-free.dev',
    fallbackBase: 'data',
  },
  window.MORPHE_CONFIG || {},
)
