// Lesezugriff auf den lokalen Speicher (db.js). Die Ansichten lesen nur hierüber – online wie offline.
import * as db from "./db.js";

const ymdOf = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const dayStr = (x) => (typeof x === "string" ? x.slice(0, 10) : ymdOf(x));

/** Vorkommen im Bereich [a, b) (Datum oder JJJJ-MM-TT). */
export async function range(a, b) {
  const from = dayStr(a), to = dayStr(b);
  const rows = await db.all("occ", { index: "last", gte: from });
  return rows.filter((e) => e.day < to).map(({ day, last, ...e }) => e).sort((x, y) => (x.all_day === y.all_day ? (x.start < y.start ? -1 : x.start > y.start ? 1 : 0) : x.all_day ? -1 : 1));
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
