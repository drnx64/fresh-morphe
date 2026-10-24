/**
 * Build script — copies all needed files to docs/ for GitHub Pages.
 * Run: node scripts/build.js
 */
import { cpSync, mkdirSync, existsSync, readdirSync, rmSync, writeFileSync, readFileSync } from 'fs'
import { join, dirname } from 'path'
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

// 4. Copy public files (favicon, feed, msg.txt)
for (const file of ['favicon.svg', 'feed.xml', 'feed-daily.xml']) {
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
