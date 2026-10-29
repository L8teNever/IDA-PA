// Zustand, Laden der Termine, Offline-Spiegel (IndexedDB) und Warteschlange für Offline-Änderungen.
import { api, ApiError } from "./api.js";
import { addDays, addMonths, monthGrid, startOfDay, weekStart, ymd } from "./dates.js";

const hiddenKey = "kal-hidden";
export const S = {
  view: "monthday", date: startOfDay(new Date()), sel: startOfDay(new Date()),
  events: [], cals: [], settings: {}, meta: {}, offline: false, loadedAt: null, loaded: false,
  agFrom: startOfDay(new Date()), agTo: addDays(startOfDay(new Date()), 60),
  mdMode: localStorage.getItem("kal-md") || "month", // Monat + Tag: week | month | full
  hh: Number(localStorage.getItem("kal-hh")) || 64,   // Stundenhöhe im Zeitraster (Pinch)
  hidden: new Set(JSON.parse(localStorage.getItem(hiddenKey) || "[]")),
  flash: new Set(),                                   // gerade von Ida geänderte Termine (blinken einmal)
};
export const saveHidden = () => localStorage.setItem(hiddenKey, JSON.stringify([...S.hidden]));
export const calOf = (id) => S.cals.find((c) => c.id === id);
export const writable = () => S.cals.filter((c) => !c.read_only && c.kind !== "holidays");

// ---- IndexedDB (ein Store, Schlüssel → Wert) ----
let dbp;
const idb = () => dbp ||= new Promise((res, rej) => {
  const r = indexedDB.open("ida-kalender", 1);
  r.onupgradeneeded = () => r.result.createObjectStore("kv");
  r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
});
export async function kvGet(k) { try { const d = await idb(); return await new Promise((res) => { const q = d.transaction("kv").objectStore("kv").get(k); q.onsuccess = () => res(q.result); q.onerror = () => res(undefined); }); } catch { return undefined; } }
export async function kvSet(k, v) { try { const d = await idb(); d.transaction("kv", "readwrite").objectStore("kv").put(v, k); } catch { /* privater Modus */ } }

/** Sichtbarer Bereich der Ansicht [von, bis) */
export function rangeOf(view = S.view, d = S.date) {
  if (view === "monthday" || view === "month") { const g = monthGrid(d); return [g[0], addDays(g[41], 1)]; }
  if (view === "year") return [new Date(d.getFullYear(), 0, 1), new Date(d.getFullYear() + 1, 0, 1)];
  if (view === "week") { const w = weekStart(d); return [w, addDays(w, 7)]; }
  if (view === "day") return [d, addDays(d, 1)];
  if (view === "days3") return [d, addDays(d, 3)];
  return [S.agFrom, S.agTo];
}

const inRange = (e, a, b) => new Date(e.end) > a && new Date(e.start) < b;

export async function loadMeta() {
  const [cals, settings, meta] = await Promise.allSettled([api("/calendars"), api("/settings"), api("/meta")]);
  if (cals.status === "fulfilled") { S.cals = cals.value.calendars; kvSet("cals", S.cals); } else S.cals = (await kvGet("cals")) || [];
  if (settings.status === "fulfilled") { S.settings = settings.value; kvSet("settings", S.settings); } else S.settings = (await kvGet("settings")) || {};
  S.meta = meta.status === "fulfilled" ? meta.value : {};
}

/** Erst sofort aus dem Spiegel (kein leerer Bildschirm), dann vom Server. */
export async function cachedEvents() {
  const [a, b] = rangeOf();
  const win = await kvGet("window");
  return win ? win.events.filter((x) => inRange(x, a, b)) : null;
}

export async function loadEvents() {
  const [a, b] = rangeOf();
  try {
    const r = await api(`/events?from=${ymd(a)}&to=${ymd(b)}`);
    S.events = r.events; S.offline = false; S.loadedAt = new Date();
    document.body.classList.remove("is-offline");
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 0) throw e;
    const win = await kvGet("window");
    S.events = (win?.events || []).filter((x) => inRange(x, a, b));
    S.offline = true; S.loadedAt = win ? new Date(win.at) : null;
    document.body.classList.add("is-offline");
  }
  S.loaded = true;
}

/** ±3 Monate im Hintergrund spiegeln, damit der Kalender offline lesbar bleibt. */
export async function mirror() {
  try {
    const a = addMonths(new Date(), -3), b = addMonths(new Date(), 4);
    const r = await api(`/events?from=${ymd(a)}&to=${ymd(b)}`);
    await kvSet("window", { at: Date.now(), from: ymd(a), to: ymd(b), events: r.events });
  } catch { /* offline */ }
}

export const visible = () => S.events.filter((e) => !S.hidden.has(e.calendar_id) && (S.settings.show_holidays !== false || !e.holiday));

// ---- Offline-Warteschlange ----
const qKey = "kal-queue";
const queue = () => JSON.parse(localStorage.getItem(qKey) || "[]");
export const queued = () => queue().length;
export const queuedIds = () => new Set(queue().map((q) => q.id).filter(Boolean));

/** Senden; ohne Verbindung wird die Änderung gemerkt und später nachgeholt. */
export async function send(path, opts, id) {
  try { return await api(path, opts); } catch (e) {
    if (e instanceof ApiError && e.status === 0) {
      localStorage.setItem(qKey, JSON.stringify([...queue(), { path, id, ...opts }]));
      return { queued: true };
    }
    throw e;
  }
}

export async function flushQueue() {
  const q = queue(); if (!q.length) return 0;
  const rest = []; let done = 0;
  for (const it of q) {
    try { await api(it.path, it); done++; } catch (e) { if (e instanceof ApiError && e.status === 0) rest.push(it); else done++; /* Konflikt/Fehler: verwerfen */ }
  }
  localStorage.setItem(qKey, JSON.stringify(rest));
  return done;
}
