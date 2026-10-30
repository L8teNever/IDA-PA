// Lesezugriff auf den lokalen Speicher (db.js). Die Ansichten lesen nur hierüber – online wie offline.
import * as db from "./db.js";
import * as outbox from "./outbox.js";
import * as preview from "./preview.js";

const ymdOf = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const dayStr = (x) => (typeof x === "string" ? x.slice(0, 10) : ymdOf(x));

/** Vorkommen im Bereich [a, b) (Datum oder JJJJ-MM-TT): gespeicherter Stand + Vorschau der wartenden Änderungen aus der Outbox. */
export async function range(a, b) {
  const from = dayStr(a), to = dayStr(b);
  let rows = (await db.all("occ", { index: "last", gte: from })).filter((e) => e.day < to).map(({ day, last, ...e }) => e);
  const ops = await outbox.all();
  if (ops.length) {
    const [cals, meta] = await Promise.all([db.get("meta", "cals"), db.get("meta", "meta")]);
    rows = preview.apply(rows, ops, { a: from, b: to, cals: cals || [], tz: meta?.timezone || "Europe/Berlin" });
  }
  return rows.sort((x, y) => (x.all_day === y.all_day ? (x.start < y.start ? -1 : x.start > y.start ? 1 : 0) : x.all_day ? -1 : 1));
}

/** Wurde schon einmal ein Fenster gespiegelt? (sonst gibt es offline nichts zu zeigen) */
export const windowInfo = () => db.get("meta", "window");

/** Spiegel: alle Vorkommen im Bereich [from, to) ersetzen – in einer Transaktion. */
export async function saveRange(events, from, to) {
  const f = dayStr(from), t = dayStr(to);
  const old = await db.all("occ", { index: "last", gte: f });
  const ops = old.filter((e) => e.day < t).map((e) => ({ op: "del", store: "occ", key: e.id }));
  for (const e of events) ops.push({ op: "put", store: "occ", value: db.normOcc(e) });
  const w = (await windowInfo()) || {};
  ops.push({ op: "put", store: "meta", key: "window", value: { at: Date.now(), from: !w.from || f < w.from ? f : w.from, to: !w.to || t > w.to ? t : w.to } });
  await db.batch(ops);
}

/** Alles aus dem Speicher (für die Offline-Suche). */
export async function everything() {
  const [rows, win] = await Promise.all([db.all("occ"), windowInfo()]);
  return { events: rows.map(({ day, last, ...e }) => e), window: win };
}

export const saveMeta = (key, value) => db.put("meta", value, key);
export const loadMeta = (key) => db.get("meta", key);

// ---------------- Abgleich anwenden (Paket 6/8) ----------------
const listeners = new Set();
/** Wird nach jedem Abgleich und jeder lokalen Änderung aufgerufen – die Oberfläche zeichnet dann neu. */
export const onChange = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
export const emit = (what = "sync") => listeners.forEach((fn) => { try { fn(what); } catch { /* ein kaputter Hörer darf die anderen nicht stören */ } });

export const syncMeta = () => db.get("meta", "sync");

/** Antwort von GET /sync einarbeiten – in EINER Transaktion. Termine mit offenen Änderungen (`protectedIds`) bleiben unberührt,
 *  bis ihre Änderung gesendet ist. Alles, was die Antwort zu einem Termin nennt, ersetzt ALLE seine Vorkommen. */
export async function apply(res, { protectedIds = new Set() } = {}) {
  const ops = [], prev = (await syncMeta()) || {};
  const rows = await db.all("occ");
  const gone = new Set();
  if (res.full) {
    for (const r of rows) if (!protectedIds.has(r.event_id)) ops.push({ op: "del", store: "occ", key: r.id });
    for (const id of await db.keys("events")) if (!protectedIds.has(id)) ops.push({ op: "del", store: "events", key: id });
  } else {
    const ids = new Set([...res.upserts.map((u) => u.event_id), ...res.removed_events]);
    for (const r of rows) if (ids.has(r.event_id) && !protectedIds.has(r.event_id)) { ops.push({ op: "del", store: "occ", key: r.id }); gone.add(r.event_id); }
    for (const id of ids) if (!protectedIds.has(id)) ops.push({ op: "del", store: "events", key: id });
  }
  for (const u of res.upserts) if (!protectedIds.has(u.event_id)) ops.push({ op: "put", store: "occ", value: db.normOcc(u) });
  if (res.calendars) ops.push({ op: "put", store: "meta", key: "cals", value: res.calendars });
  if (res.settings) ops.push({ op: "put", store: "meta", key: "settings", value: res.settings });
  const from = res.full ? res.from : prev.from || res.from, to = res.full ? res.to : prev.to || res.to;
  const now = Date.now();
  ops.push({ op: "put", store: "meta", key: "sync", value: { cursor: res.cursor, from, to, metaAt: res.meta_at, at: now, fullDay: res.full ? today() : prev.fullDay || "" } });
  ops.push({ op: "put", store: "meta", key: "window", value: { at: now, from, to } });
  await db.batch(ops);
  return { upserts: res.upserts.length, removed: res.removed_events.length, full: !!res.full };
}
const today = () => ymdOf(new Date());

// ---------------- Details ----------------
const evId = (occId) => occId.split("_")[0];
/** Gespeicherte Details dieses Vorkommens (kommen vom Server und werden beim nächsten Abgleich verworfen, wenn sich der Termin ändert). */
export async function cachedDetail(occId) {
  const hit = await db.get("events", evId(occId));
  return hit && hit._for === occId ? hit : null;
}
export const putDetail = (occId, e) => db.put("events", { ...e, _for: occId, event_id: evId(occId) });
/** Notlösung offline: was das Vorkommen selbst hergibt (Titel, Zeit, Ort … – ohne Beschreibung). */
export async function occDetail(occId) {
  const o = await db.get("occ", occId);
  if (!o) return null;
  const { day, last, ...e } = o;
  return { ...e, partial: true };
}
