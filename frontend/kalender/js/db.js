// Lokaler Speicher des Kalenders (IndexedDB "ida-kalender", Schema 2) – die Quelle für alles, was die App offline zeigt.
// Der Speicher steckt hinter einer kleinen Schnittstelle („Backend“): im Browser IndexedDB, in Tests und als Notnagel
// (privater Modus, kein IndexedDB) ein Arbeitsspeicher-Ersatz mit demselben Verhalten. Kein DOM-Zugriff beim Laden.
//
// Stores:  occ     Vorkommen (Schlüssel id; Indizes day, last, event_id)
//          events  Details je Termin (Schlüssel event_id)
//          outbox  wartende Änderungen (Schlüssel seq, automatisch; Indizes target, state)
//          meta    Einzelwerte (cals, settings, window, schema …)
//          kv      Schema 1 (alter Spiegel) – wird nach der Übernahme nicht mehr benutzt und in Schema 3 gelöscht
// Regeln für künftige Migrationen: outbox nie löschen, nur umformen; occ/events sind Zwischenspeicher und dürfen geleert werden.

export const SCHEMA = 3;   // 3: Store `files` (Anhänge, die noch auf den Abgleich warten)
export const DB_NAME = "ida-kalender";
export const STORES = {
  occ: { keyPath: "id", indexes: { day: "day", last: "last", event_id: "event_id" } },
  events: { keyPath: "event_id" },
  outbox: { keyPath: "seq", autoIncrement: true, indexes: { target: "target", state: "state" } },
  meta: {},
  files: { keyPath: "opId" },
  kv: {},
};

/** Zustand des Speichers für die Oberfläche: ok = dauerhaft (IndexedDB); sonst nur bis zum Schließen der App. */
export const state = { ok: true, error: "", kind: "none" };

// ---------------- Arbeitsspeicher-Backend ----------------
export function memBackend() {
  const data = {}, counters = {};
  const st = (s) => (data[s] ||= new Map());
  const keyOf = (s, v, k) => (k !== undefined ? k : STORES[s]?.keyPath ? v[STORES[s].keyPath] : undefined);
  const match = (val, { eq, gte, lte }) => (eq === undefined || val === eq) && (gte === undefined || val >= gte) && (lte === undefined || val <= lte);
  const clone = (v) => (v === undefined ? v : structuredClone(v));
  const apply = (o) => {
    if (o.op === "clear") st(o.store).clear();
    else if (o.op === "del") st(o.store).delete(o.key);
    else {
      let k = keyOf(o.store, o.value, o.key);
      if (k === undefined && STORES[o.store]?.autoIncrement) { counters[o.store] = (counters[o.store] || 0) + 1; k = counters[o.store]; }
      const v = clone(o.value);
      if (STORES[o.store]?.autoIncrement && STORES[o.store].keyPath) v[STORES[o.store].keyPath] = k;
      else if (typeof k === "number") counters[o.store] = Math.max(counters[o.store] || 0, k);
      st(o.store).set(k, v);
      return k;
    }
  };
  return {
    kind: "memory",
    async open() { return this; },
    async get(s, k) { return clone(st(s).get(k)); },
    async put(s, v, k) { return apply({ op: "put", store: s, value: v, key: k }); },
    async del(s, k) { apply({ op: "del", store: s, key: k }); },
    async clear(s) { apply({ op: "clear", store: s }); },
    async all(s, q = {}) {
      let rows = [...st(s).entries()];
      const f = q.index ? STORES[s].indexes[q.index] : null;
      if (f) rows = rows.filter(([, v]) => match(v[f], q));
      const cmp = (x, y) => (x < y ? -1 : x > y ? 1 : 0); // wie IndexedDB: nach Index, dann nach Schlüssel
      return rows.sort((a, c) => (f ? cmp(a[1][f], c[1][f]) : 0) || cmp(a[0], c[0])).map(([, v]) => clone(v));
    },
    async keys(s) { return [...st(s).keys()]; },
    async batch(ops) { // alles oder nichts
      const snapshot = Object.fromEntries(Object.entries(data).map(([k, m]) => [k, new Map(m)])), c = { ...counters };
      try { return ops.map(apply); } catch (e) { for (const k of Object.keys(data)) delete data[k]; Object.assign(data, snapshot); Object.assign(counters, c); throw e; }
    },
  };
}

// ---------------- IndexedDB-Backend ----------------
export function idbBackend(factory = globalThis.indexedDB) {
  let dbp;
  const open = () => (dbp ||= new Promise((res, rej) => {
    if (!factory) return rej(new Error("IndexedDB nicht verfügbar"));
    const r = factory.open(DB_NAME, SCHEMA);
    r.onupgradeneeded = (ev) => {
      const db = r.result, old = ev.oldVersion;
      for (const [name, def] of Object.entries(STORES)) {
        if (db.objectStoreNames.contains(name)) continue;
        if (name === "kv" && old < 1) continue; // der alte Spiegel nur dort, wo es ihn schon gab
        const os = db.createObjectStore(name, { keyPath: def.keyPath, autoIncrement: !!def.autoIncrement });
        for (const [ix, path] of Object.entries(def.indexes || {})) os.createIndex(ix, path);
      }
    };
    r.onsuccess = () => {
      r.result.onversionchange = () => { r.result.close(); dbp = null; }; // ein neueres Fenster darf aktualisieren
      res(r.result);
    };
    r.onerror = () => rej(r.error);
    r.onblocked = () => {};
  }));
  const done = (tx) => new Promise((res, rej) => { tx.oncomplete = () => res(); tx.onerror = () => rej(tx.error); tx.onabort = () => rej(tx.error || new Error("abgebrochen")); });
  const req = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const range = ({ eq, gte, lte }) => (eq !== undefined ? IDBKeyRange.only(eq) : gte !== undefined && lte !== undefined ? IDBKeyRange.bound(gte, lte)
    : gte !== undefined ? IDBKeyRange.lowerBound(gte) : lte !== undefined ? IDBKeyRange.upperBound(lte) : undefined);
  return {
    kind: "indexeddb",
    async open() { await open(); return this; },
    async get(s, k) { const d = await open(); return req(d.transaction(s).objectStore(s).get(k)); },
    async put(s, v, k) { const d = await open(); const tx = d.transaction(s, "readwrite"); const r = req(tx.objectStore(s).put(v, STORES[s].keyPath ? undefined : k)); await done(tx); return r; },
    async del(s, k) { const d = await open(); const tx = d.transaction(s, "readwrite"); tx.objectStore(s).delete(k); await done(tx); },
    async clear(s) { const d = await open(); const tx = d.transaction(s, "readwrite"); tx.objectStore(s).clear(); await done(tx); },
    async all(s, q = {}) {
      const d = await open(); const os = d.transaction(s).objectStore(s);
      return req((q.index ? os.index(q.index) : os).getAll(q.index ? range(q) : undefined));
    },
    async keys(s) { const d = await open(); return req(d.transaction(s).objectStore(s).getAllKeys()); },
    async batch(ops) { // eine Transaktion: alles oder nichts
      const d = await open(); const names = [...new Set(ops.map((o) => o.store))];
      if (!names.length) return [];
      const tx = d.transaction(names, "readwrite"), out = [];
      for (const o of ops) {
        const os = tx.objectStore(o.store);
        if (o.op === "clear") os.clear(); else if (o.op === "del") os.delete(o.key);
        else { const r = os.put(o.value, STORES[o.store].keyPath ? undefined : o.key); r.onsuccess = () => out.push(r.result); }
      }
      await done(tx);
      return out;
    },
  };
}

// ---------------- gemeinsame Schnittstelle ----------------
let backend = null, opening = null;

/** Für Tests: festes Backend setzen (null = beim nächsten Zugriff neu wählen). */
export function setBackend(b) { backend = b; opening = null; state.ok = true; state.error = ""; state.kind = b?.kind || "none"; }

export async function b() {
  if (backend) return backend;
  return (opening ||= (async () => {
    try { backend = await idbBackend().open(); state.kind = "indexeddb"; }
    catch (e) { // privater Modus o. Ä.: die App läuft weiter, nur ohne dauerhaften Speicher
      backend = memBackend(); state.ok = false; state.error = String(e?.message || e); state.kind = "memory";
    }
    try { await migrate(backend); } catch (e) { state.error = `Übernahme alter Daten fehlgeschlagen: ${e?.message || e}`; }
    return backend;
  })());
}

export const get = async (s, k) => (await b()).get(s, k);
export const put = async (s, v, k) => (await b()).put(s, v, k);
export const del = async (s, k) => (await b()).del(s, k);
export const clear = async (s) => (await b()).clear(s);
export const all = async (s, q) => (await b()).all(s, q);
export const keys = async (s) => (await b()).keys(s);
export const batch = async (ops) => (await b()).batch(ops);
export const count = async (s) => (await keys(s)).length;

// ---------------- Vorkommen: Tagesfelder für schnelle Bereichsabfragen ----------------
const pad = (n) => String(n).padStart(2, "0");
export function dayAdd(ymd, n) {
  const [y, m, d] = ymd.split("-").map(Number), t = new Date(Date.UTC(y, m - 1, d + n));
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}`;
}
/** Letzter Tag, den das Vorkommen berührt (inklusive). Ganztägig: Ende ist exklusiv; sonst zählt 00:00 als Ende des Vortags. */
export function lastDay(v) {
  const s = v.start.slice(0, 10);
  let e = v.end.slice(0, 10);
  if (v.all_day || (v.end.length > 10 && v.end.slice(11, 19) === "00:00:00")) e = dayAdd(e, -1);
  return e < s ? s : e;
}
export const normOcc = (v) => ({ ...v, day: v.start.slice(0, 10), last: lastDay(v) });

// ---------------- Übernahme der Daten aus Schema 1 ----------------
/** Läuft nach dem Öffnen und ist wiederholbar: kopiert den alten Spiegel (kv) in die neuen Stores. Die alte Warteschlange
 *  (localStorage "kal-queue") bleibt bis zur Outbox (Paket 11) unangetastet – lieber doppelt versuchen als verlieren. */
export async function migrate(be) {
  const done = await be.get("meta", "schema");
  if (done >= SCHEMA) return { skipped: true };
  const ops = [];
  let occ = 0;
  let win = null, cals, settings;
  if (done === undefined) try { [win, cals, settings] = await Promise.all([be.get("kv", "window"), be.get("kv", "cals"), be.get("kv", "settings")]); } catch { /* kein kv-Store: neue Installation */ }
  if (win?.events) {
    for (const e of win.events) if (e?.start && e?.end) { ops.push({ op: "put", store: "occ", value: normOcc(e) }); occ++; }
    ops.push({ op: "put", store: "meta", key: "window", value: { at: win.at, from: win.from, to: win.to } });
  }
  if (cals) ops.push({ op: "put", store: "meta", key: "cals", value: cals });
  if (settings) ops.push({ op: "put", store: "meta", key: "settings", value: settings });
  ops.push({ op: "put", store: "meta", key: "schema", value: SCHEMA });
  await be.batch(ops);
  return { migrated: true, occ };
}
