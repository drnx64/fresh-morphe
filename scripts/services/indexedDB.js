/**
 * IndexedDB wrapper — FreshMorpheCache database.
 */

const DB_NAME = 'FreshMorpheCache'
const LEGACY_DB_NAME = 'MorpheTrackerCache'
const STORE_NAME = 'store'
const DB_VERSION = 1

/** @type {Promise<IDBDatabase>|null} */
let dbPromise = null

function openDB() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION)
      req.onupgradeneeded = () => {
        req.result.createObjectStore(STORE_NAME)
      }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => {
        dbPromise = null
        reject(req.error)
      }
    })
  }
  return dbPromise
}

async function withStore(mode, fn) {
  try {
    const db = await openDB()
    const tx = db.transaction(STORE_NAME, mode)
    const store = tx.objectStore(STORE_NAME)
    return await fn(store, tx)
  } catch (err) {
    console.error('[indexedDB] transaction exception:', err)
    return null
  }
}

function txDone(tx) {
  return new Promise((resolve) => {
    tx.oncomplete = () => resolve()
    tx.onabort = () => resolve()
    tx.onerror = () => resolve()
  })
}

export async function idbSet(key, val) {
  try {
    const db = await openDB()
    const tx = db.transaction(STORE_NAME, 'readwrite')
    tx.objectStore(STORE_NAME).put(val, key)
    await txDone(tx)
  } catch (err) {
    console.error(`[indexedDB] idbSet("${key}") error:`, err)
  }
}

export async function idbSetMany(entries) {
  if (!entries.length) return
  try {
    const db = await openDB()
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    for (const [key, val] of entries) store.put(val, key)
    await txDone(tx)
  } catch (err) {
    console.error('[indexedDB] idbSetMany error:', err)
  }
}

export async function idbGet(key) {
  return withStore('readonly', (store) => {
    return new Promise((resolve) => {
      const req = store.get(key)
      req.onsuccess = () => resolve(req.result ?? null)
      req.onerror = () => resolve(null)
    })
  })
}

export async function idbGetMany(keys) {
  /** @type {Map<string, any>} */
  const map = new Map()
  if (!keys.length) return map
  const result = await withStore('readonly', (store) => {
    return new Promise((resolve) => {
      const tx = store.transaction
      let pending = keys.length
      for (const key of keys) {
        const req = store.get(key)
        req.onsuccess = () => {
          if (req.result !== undefined && req.result !== null) {
            map.set(key, req.result)
          }
          pending -= 1
          if (pending === 0) resolve(map)
        }
        req.onerror = () => {
          pending -= 1
          if (pending === 0) resolve(map)
        }
      }
      tx.onerror = () => resolve(map)
    })
  })
  return result || map
}

export async function idbKeys(prefix = '') {
  const result = await withStore('readonly', (store) => {
    return new Promise((resolve) => {
      /** @type {string[]} */
      const keys = []
      const req = store.openCursor()
      req.onsuccess = () => {
        const cur = req.result
        if (cur) {
          const key = String(cur.key)
          if (!prefix || key.startsWith(prefix)) keys.push(key)
          cur.continue()
        } else {
          resolve(keys)
        }
      }
      req.onerror = () => resolve(keys)
    })
  })
  return result || []
}

export async function idbDeleteMany(keys) {
  if (!keys.length) return
  try {
    const db = await openDB()
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    for (const k of keys) store.delete(k)
    await txDone(tx)
  } catch (err) {
    console.error('[indexedDB] idbDeleteMany error:', err)
  }
}

export async function clearAllCaches() {
  try {
    const db = await openDB()
    db.close()
    dbPromise = null
    indexedDB.deleteDatabase(DB_NAME)
    indexedDB.deleteDatabase(LEGACY_DB_NAME)
  } catch (err) {
    console.error('[indexedDB] clearAllCaches error:', err)
  }
}
