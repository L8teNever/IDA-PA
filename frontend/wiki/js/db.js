// Lokale IndexedDB-Datenbank (`ida-wiki-v1`) für Offline-Lesen, blitzschnelles Umschalten und Outbox-Warteschlange.
// Besitzt einen automatischen In-Memory-Fallback für Node-Tests und eingeschränkte Browser-Kontexte.

const DB_NAME = "ida-wiki-v1";
const DB_VER = 1;

const mem = {
  pages: new Map(),
  meta: new Map(),
  outbox: new Map(),
  seq: 0,
};

let dbPromise = null;

function hasIDB() {
  return typeof indexedDB !== "undefined";
}

function openDB() {
  if (!hasIDB()) return Promise.resolve(null);
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VER);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("pages")) {
          const ps = db.createObjectStore("pages", { keyPath: "id" });
          ps.createIndex("by_path", "path", { unique: false });
          ps.createIndex("by_space", "space", { unique: false });
        }
        if (!db.objectStoreNames.contains("meta")) {
          db.createObjectStore("meta", { keyPath: "key" });
        }
        if (!db.objectStoreNames.contains("outbox")) {
          db.createObjectStore("outbox", { keyPath: "seq", autoIncrement: true });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return dbPromise;
}

function txDone(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

function reqVal(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function getMeta(key, fallback = null) {
  const db = await openDB();
  if (!db) return mem.meta.has(key) ? mem.meta.get(key) : fallback;
  const row = await reqVal(db.transaction("meta", "readonly").objectStore("meta").get(key));
  return row ? row.value : fallback;
}

export async function setMeta(key, value) {
  const db = await openDB();
  if (!db) {
    mem.meta.set(key, value);
    return;
  }
  const tx = db.transaction("meta", "readwrite");
  tx.objectStore("meta").put({ key, value });
  await txDone(tx);
}

export async function putPages(pages, { replaceAll = false } = {}) {
  const db = await openDB();
  if (!db) {
    if (replaceAll) mem.pages.clear();
    for (const p of pages) mem.pages.set(p.id, structuredClone(p));
    return;
  }
  const tx = db.transaction("pages", "readwrite");
  const st = tx.objectStore("pages");
  if (replaceAll) st.clear();
  for (const p of pages) st.put(p);
  await txDone(tx);
}

export async function deletePages(ids) {
  if (!ids?.length) return;
  const db = await openDB();
  if (!db) {
    for (const id of ids) mem.pages.delete(id);
    return;
  }
  const tx = db.transaction("pages", "readwrite");
  const st = tx.objectStore("pages");
  for (const id of ids) st.delete(id);
  await txDone(tx);
}

export async function getAllPages() {
  const db = await openDB();
  if (!db) return [...mem.pages.values()].sort((a, b) => a.path.localeCompare(b.path));
  const list = await reqVal(db.transaction("pages", "readonly").objectStore("pages").getAll());
  return list.sort((a, b) => a.path.localeCompare(b.path));
}

export async function getPage(ref) {
  if (!ref) return null;
  const clean = String(ref).trim().replace(/^\/+|\/+$/g, "").replace(/\.md$/i, "");
  const all = await getAllPages();
  return all.find((p) => p.id === clean || p.path.toLowerCase() === clean.toLowerCase()) || null;
}

export async function outboxAdd(entry) {
  const db = await openDB();
  if (!db) {
    const seq = ++mem.seq;
    const item = { ...structuredClone(entry), seq };
    mem.outbox.set(seq, item);
    return item;
  }
  const tx = db.transaction("outbox", "readwrite");
  const seq = await reqVal(tx.objectStore("outbox").add(entry));
  await txDone(tx);
  return { ...entry, seq };
}

export async function outboxAll() {
  const db = await openDB();
  if (!db) return [...mem.outbox.values()].sort((a, b) => a.seq - b.seq);
  return reqVal(db.transaction("outbox", "readonly").objectStore("outbox").getAll());
}

export async function outboxPut(item) {
  const db = await openDB();
  if (!db) {
    mem.outbox.set(item.seq, structuredClone(item));
    return;
  }
  const tx = db.transaction("outbox", "readwrite");
  tx.objectStore("outbox").put(item);
  await txDone(tx);
}

export async function outboxDelete(seq) {
  const db = await openDB();
  if (!db) {
    mem.outbox.delete(seq);
    return;
  }
  const tx = db.transaction("outbox", "readwrite");
  tx.objectStore("outbox").delete(seq);
  await txDone(tx);
}

export async function resetForTests() {
  mem.pages.clear();
  mem.meta.clear();
  mem.outbox.clear();
  mem.seq = 0;
}
