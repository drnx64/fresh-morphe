/**
 * Build script — copies all needed files to docs/ for GitHub Pages.
 * Run: node scripts/build.js
 */
import { cpSync, mkdirSync, existsSync, readdirSync, rmSync, writeFileSync, readFileSync } from 'fs'
import { join, dirname, relative, sep } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const ROOT = join(__dirname, '..')
const DOCS = join(ROOT, 'docs')

/**
 * Copy a JSON file to docs/ with whitespace stripped. The pipeline writes
 * indented JSON (readable git diffs), but these bytes are what visitors
 * download — minifying cuts changelog.json ~43% before gzip even applies.
 * Parse/stringify round-trips the value with identical semantics.
 * @param {string} srcPath
 * @param {string} destPath
 */
function writeJsonMinified(srcPath, destPath) {
  writeFileSync(destPath, JSON.stringify(JSON.parse(readFileSync(srcPath, 'utf8'))))
}

console.log('[build] Starting build to docs/...')

// Clean docs/
if (existsSync(DOCS)) {
  rmSync(DOCS, { recursive: true })
  console.log('[build] Cleaned docs/')
}
mkdirSync(DOCS, { recursive: true })

// .nojekyll — prevents GitHub Pages Jekyll from excluding underscore-prefixed
// files (e.g. data/bundles/_index.json)
writeFileSync(join(DOCS, '.nojekyll'), '')
console.log('[build] .nojekyll')

// 1. Copy index.html
cpSync(join(ROOT, 'index.html'), join(DOCS, 'index.html'))
console.log('[build] index.html')

// 2. Copy assets/
cpSync(join(ROOT, 'assets'), join(DOCS, 'assets'), { recursive: true })
console.log('[build] assets/')

// 3. Copy scripts/ — frontend only ever loads ES modules, so ship .js only.
//    Skips pipeline Python, __pycache__/ and the gitignored temp/ scratch dir.
const SKIP_DIRS = new Set(['__pycache__', 'temp'])
function copyJsModules(srcDir, destDir) {
  mkdirSync(destDir, { recursive: true })
  let count = 0
  for (const entry of readdirSync(srcDir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue
      count += copyJsModules(join(srcDir, entry.name), join(destDir, entry.name))
    } else if (entry.name.endsWith('.js')) {
      cpSync(join(srcDir, entry.name), join(destDir, entry.name))
      count++
    }
  }
  return count
}
const scriptFileCount = copyJsModules(join(ROOT, 'scripts'), join(DOCS, 'scripts'))
console.log(`[build] scripts/ (${scriptFileCount} .js files)`)

// 3.5. Production index.html: one stylesheet + modulepreload hints.
//     Source keeps its 11 ordered CSS files (dev needs no build step); docs
//     gets a single concatenation — 11 requests become 1 — and a preload hint
//     for every module scripts/app.js can reach, so the browser fetches the
//     whole graph at high priority instead of discovering it one import at a
//     time as execution unwinds.
const leadingNumber = (name) => {
  const m = name.match(/^\d+/)
  return m ? parseInt(m[0], 10) : Number.MAX_SAFE_INTEGER
}

const cssDir = join(ROOT, 'assets', 'css')
const cssNames = readdirSync(cssDir)
  .filter((name) => name.endsWith('.css'))
  .sort((a, b) => leadingNumber(a) - leadingNumber(b) || a.localeCompare(b))

const cssBundle = cssNames
  .map((name) => `/* ${name} */\n${readFileSync(join(cssDir, name), 'utf8')}`)
  .join('\n')
writeFileSync(join(DOCS, 'assets', 'css', 'all.css'), cssBundle)
for (const name of cssNames) rmSync(join(DOCS, 'assets', 'css', name))
console.log(`[build] assets/css/all.css (${cssNames.length} files, ${Math.round(Buffer.byteLength(cssBundle) / 1024)} KB)`)

const docsIndexPath = join(DOCS, 'index.html')
let docsIndex = readFileSync(docsIndexPath, 'utf8')
const nl = docsIndex.includes('\r\n') ? '\r\n' : '\n'

// 11 stylesheet links → the single bundle. The count must match the files
// on disk exactly: a stray or missing link would ship an unstyled section
// once the originals are removed from docs/.
const cssTagCount = (docsIndex.match(/<link rel="stylesheet" href="assets\/css\/[^"]+" \/>/g) || []).length
if (cssTagCount !== cssNames.length) {
  throw new Error(
    `[build] index.html links ${cssTagCount} assets/css files but assets/css holds ${cssNames.length}`,
  )
}
let cssLinkPlaced = false
docsIndex = docsIndex.replace(
  /(?:[ \t]*<link rel="stylesheet" href="assets\/css\/[^"]+" \/>\r?\n?)+/g,
  () => {
    if (cssLinkPlaced) return ''
    cssLinkPlaced = true
    return `    <link rel="stylesheet" href="assets/css/all.css" />${nl}`
  },
)

// Walk the import graph from scripts/app.js: static, re-export and dynamic
// edges. Every reachable module (except the entry itself — index.html loads
// it with a script tag) gets a preload hint. A spec that resolves nowhere is
// a broken import, and no bundler exists here to catch it, so fail the build.
const MODULE_IMPORT_RE = /^\s*(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"](\.[^'"]+)['"]/gm
const DYNAMIC_IMPORT_RE = /\bimport\(\s*['"](\.[^'"]+)['"]\s*\)/g
const entryModule = join(ROOT, 'scripts', 'app.js')
const moduleGraph = new Set()
const moduleQueue = [entryModule]
while (moduleQueue.length) {
  const file = moduleQueue.shift()
  if (moduleGraph.has(file)) continue
  moduleGraph.add(file)
  const src = readFileSync(file, 'utf8')
  const specs = new Set([
    ...[...src.matchAll(MODULE_IMPORT_RE)].map((m) => m[1]),
    ...[...src.matchAll(DYNAMIC_IMPORT_RE)].map((m) => m[1]),
  ])
  for (const spec of specs) {
    const resolved = join(dirname(file), spec)
    if (!existsSync(resolved)) {
      throw new Error(`[build] ${relative(ROOT, file).split(sep).join('/')} imports missing file ${spec}`)
    }
    if (!moduleGraph.has(resolved)) moduleQueue.push(resolved)
  }
}
const preloadLinks = [...moduleGraph]
  .filter((file) => file !== entryModule)
  .map((file) => relative(ROOT, file).split(sep).join('/'))
if (!docsIndex.includes('<!-- modulepreload -->')) {
  throw new Error('[build] index.html is missing the <!-- modulepreload --> marker')
}
docsIndex = docsIndex.replace(
  /[ \t]*<!-- modulepreload -->/,
  preloadLinks.map((href) => `    <link rel="modulepreload" href="${href}" />`).join(nl),
)
writeFileSync(docsIndexPath, docsIndex)
console.log(`[build] index.html (all.css + ${preloadLinks.length} modulepreload hints)`)

// 4. Copy public files (favicon, feed, msg.txt)
for (const file of ['feed.xml', 'feed-daily.xml']) {
  const src = join(ROOT, file)
  if (existsSync(src)) {
    cpSync(src, join(DOCS, file))
    console.log(`[build] ${file}`)
  }
}

const publicDir = join(ROOT, 'public')
if (existsSync(publicDir)) {
  for (const file of readdirSync(publicDir)) {
    cpSync(join(publicDir, file), join(DOCS, file))
    console.log(`[build] public/${file}`)
  }
  // Also preserve public/ path — index.html and app.js reference public/*
  cpSync(publicDir, join(DOCS, 'public'), { recursive: true })
  console.log('[build] public/ (preserved path)')
}

// 5. Copy data/ (essential files only)
const DATA_DIR = join(ROOT, 'data')
const DOCS_DATA = join(DOCS, 'data')

if (existsSync(DATA_DIR)) {
  mkdirSync(DOCS_DATA, { recursive: true })

  // Essential root files — everything the frontend actually fetches
  // (scripts/app.js). repos_list.txt ships as-is for external consumers.
  const ESSENTIAL_FILES = ['core.json', 'stats.json', 'changes.json', 'changelog.json', 'repos_list.txt']
  for (const file of ESSENTIAL_FILES) {
    const src = join(DATA_DIR, file)
    if (existsSync(src)) {
      if (file.endsWith('.json')) writeJsonMinified(src, join(DOCS_DATA, file))
      else cpSync(src, join(DOCS_DATA, file))
    }
  }
  console.log('[build] data/ (essential files)')

  // state/ directory — only the two caches the frontend loads. The rest
  // (snapshots, daily buffer, run metadata) is pipeline-internal.
  const STATE_DIR = join(DATA_DIR, 'state')
  const DOCS_STATE = join(DOCS_DATA, 'state')
  const STATE_FILES = ['app_cache.json', 'repo_cache.json']
  if (existsSync(STATE_DIR)) {
    mkdirSync(DOCS_STATE, { recursive: true })
    let stateCount = 0
    for (const file of STATE_FILES) {
      if (!existsSync(join(STATE_DIR, file))) continue
      writeJsonMinified(join(STATE_DIR, file), join(DOCS_STATE, file))
      stateCount++
    }
    console.log(`[build] data/state/ (${stateCount} files)`)
  }

  // bundles/ directory — every bundle JSON is fetched on demand, so all of it
  // ships, minified.
  const BUNDLES_DIR = join(DATA_DIR, 'bundles')
  const DOCS_BUNDLES = join(DOCS_DATA, 'bundles')
  if (existsSync(BUNDLES_DIR)) {
    mkdirSync(DOCS_BUNDLES, { recursive: true })
    let bundleCount = 0
    for (const file of readdirSync(BUNDLES_DIR)) {
      if (!file.endsWith('.json')) continue
      writeJsonMinified(join(BUNDLES_DIR, file), join(DOCS_BUNDLES, file))
      bundleCount++
    }
    console.log(`[build] data/bundles/ (${bundleCount} files)`)
  }
}

console.log('[build] Done!')
