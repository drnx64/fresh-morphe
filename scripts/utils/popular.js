/**
 * Popular apps — the short list pinned above the alphabetical directory.
 *
 * The Apps page is a registry: 1,600+ packages ordered by name, so the apps
 * most people actually patch were scattered through the page. This is a
 * curated, tiered list, not a popularity score.
 *
 * Three ranks, in order:
 *   0. apps shipped by Morphe Official (the `morphe` bundle) — the patches this
 *      site exists to track, so they lead. Membership is read from the bundle
 *      itself, which means a new official app joins the row with no edit here.
 *   1. social apps, ordered by worldwide reach — WhatsApp, Instagram, TikTok…
 *      including the regional giants (LINE, KakaoTalk, VK, imo) because "most
 *      patched" is a global question, not a US one.
 *   2. the remaining household names.
 *
 * Matching is by exact package id. A package that is not in a bundle simply
 * does not render, so stale entries here are harmless.
 */

/** The official bundle: repo `morpheapp/morphe-patches`, shown as `Morphe Patches`. */
export const OFFICIAL_BUNDLE = 'morphe'

/**
 * Social apps by worldwide reach. Order matters — it is the display order
 * inside rank 1, so the most-installed messengers lead.
 */
const SOCIAL = [
  'com.whatsapp',
  'com.instagram.android',
  'com.facebook.katana',
  'com.zhiliaoapp.musically',
  'org.telegram.messenger',
  'com.facebook.orca',
  'com.snapchat.android',
  'com.facebook.lite',
  'com.discord',
  'com.twitter.android',
  'com.reddit.frontpage',
  'com.pinterest',
  'com.linkedin.android',
  'com.tencent.mm',
  'jp.naver.line',
  'com.kakao.talk',
  'com.vkontakte.android',
  'imo.android.imo',
  'com.viber.voip',
  'com.tumblr',
  'tv.twitch.android.app',
  'com.instagram.barcelona',
  'com.microsoft.teams',
  'com.google.android.apps.tachyon',
  'us.zoom.videomeetings',
  'com.skype.raider',
  'com.Slack',
  'com.google.android.youtube',
  'com.google.android.apps.youtube.music',
]

/** Tier 0: apps almost every patch user knows by name (non-social). */
const TIER_0 = [
  'com.android.chrome',
  'com.google.android.gm',
  'com.google.android.apps.photos',
  'com.google.android.apps.maps',
  'com.google.android.googlequicksearchbox',
  'com.google.android.apps.docs',
  'com.google.android.apps.nbu.files',
  'com.google.android.calendar',
  'com.google.android.keep',
  'com.google.android.apps.youtube.kids',
  'com.netflix.mediaclient',
  'com.microsoft.office.outlook',
  'com.microsoft.teams',
]

/** Tier 1: widely installed, one row below the headline names. */
const TIER_1 = [
  'com.google.android.apps.docs.editors.docs',
  'com.google.android.apps.docs.editors.sheets',
  'com.google.android.apps.docs.editors.slides',
  'com.google.android.apps.wellbeing',
  'com.google.android.apps.subscriptions.red',
  'com.google.android.apps.magazines',
  'com.google.android.apps.podcasts',
  'com.google.android.apps.turbo',
  'com.google.android.deskclock',
  'com.google.android.apps.restore',
  'com.google.android.apps.authenticator2',
  'com.google.android.apps.translate',
  'com.google.android.apps.adm',
  'com.google.android.apps.chromecast.app',
  'com.adobe.reader',
  'com.adobe.psmobile',
  'com.adobe.lrmobile',
  'com.adobe.scan.android',
  'com.adobe.acrobat.sign',
  'com.microsoft.emmx',
  'com.microsoft.office.word',
  'com.microsoft.office.excel',
  'com.microsoft.office.powerpoint',
  'com.microsoft.skydrive',
  'com.microsoft.office.officehubrow',
  'com.microsoft.bing',
  'com.amazon.mShop.android.shopping',
  'com.amazon.avod.thirdpartyclient',
  'com.ubercab',
  'com.airbnb.android',
  'com.canva.editor',
  'com.brave.browser',
  'org.mozilla.firefox',
  'org.videolan.vlc',
  'com.shazam.android',
  'com.dropbox.android',
  'com.paypal.android.p2pmobile',
  'com.duolingo',
  'com.evernote',
  'com.todoist',
  'com.google.android.apps.nbu.paisa.user',
]

/** package id -> rank inside the non-official tiers (0 = most prominent). */
const POPULAR_TIER = new Map()
;[TIER_0, TIER_1].forEach((packages, tier) => {
  for (const pkg of packages) {
    if (!POPULAR_TIER.has(pkg)) POPULAR_TIER.set(pkg, tier)
  }
})

/** package id -> position in the social list (0 = most reached). */
const SOCIAL_RANK = new Map()
SOCIAL.forEach((pkg, index) => {
  if (!SOCIAL_RANK.has(pkg)) SOCIAL_RANK.set(pkg, index)
})

/**
 * Prominence tier of a package, or `null` when it is not on the list.
 * @param {string} packageName
 * @returns {number|null}
 */
export function popularTier(packageName) {
  const tier = POPULAR_TIER.get(String(packageName || '').toLowerCase())
  return tier === undefined ? null : tier
}

/**
 * Whether a package belongs in the Featured row.
 * @param {string} packageName
 */
export function isPopular(packageName) {
  return popularTier(packageName) !== null
}

/**
 * Whether an app is shipped by Morphe Official.
 * @param {{ bundles?: Array<{ bundleName?: string }> }} app
 * @returns {boolean}
 */
export function isMorpheOfficial(app) {
  return (app?.bundles || []).some((b) => b?.bundleName === OFFICIAL_BUNDLE)
}

/**
 * Sort key for one app; `null` means "not featured material".
 *
 * @param {{ package: string, bundles?: Array<{bundleName?: string}> }} app
 * @returns {number|null}
 */
function featuredRank(app) {
  if (isMorpheOfficial(app)) return 0
  const social = SOCIAL_RANK.get(String(app.package || '').toLowerCase())
  if (social !== undefined) return 1 + social / 1000
  const tier = popularTier(app.package)
  if (tier === null) return null
  return 2 + tier
}

/**
 * Featured apps, in display order: official first, then social by reach, then
 * the rest of the famous list — each group alphabetically.
 *
 * Icon-less apps are skipped: the whole point of the row is instant
 * recognition, and a letter tile for YouTube is worse than not showing it.
 *
 * @param {Array<{ package: string, name: string, iconUrl?: string, bundles?: Array<{bundleName?: string}> }>} appIndex
 * @param {number} [limit]
 * @returns {Array<object>}
 */
export function featuredApps(appIndex, limit = 12) {
  const picked = []
  for (const app of appIndex || []) {
    const rank = featuredRank(app)
    if (rank === null) continue
    if (!app.iconUrl) continue
    picked.push({ app, rank })
  }
  picked.sort((a, b) => a.rank - b.rank || a.app.name.localeCompare(b.app.name))
  return picked.slice(0, limit).map((entry) => entry.app)
}

/** Total size of the curated list, for tests and dev logs. */
export const POPULAR_COUNT = POPULAR_TIER.size + SOCIAL_RANK.size
