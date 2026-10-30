// Zustand, Laden der Termine, Offline-Spiegel (IndexedDB über repo.js) und Warteschlange für Offline-Änderungen.
import { api, ApiError, onNet } from "./api.js";
import { addDays, addMonths, monthGrid, startOfDay, weekStart, ymd } from "./dates.js";
import * as repo from "./repo.js";
import * as sync from "./sync.js";

const hiddenKey = "kal-hidden";
export const S = {
  view: "monthday", date: startOfDay(new Date()), sel: startOfDay(new Date()),
  events: [], cals: [], settings: {}, meta: {}, loadedAt: null, loaded: false, outside: false,
  net: navigator.onLine === false ? "offline" : "online",   // online | offline | auth (Anmeldung abgelaufen) | syncing
  get offline() { return this.net === "offline" || this.net === "auth"; },
  agFrom: startOfDay(new Date()), agTo: addDays(startOfDay(new Date()), 60),
  mdMode: localStorage.getItem("kal-md") || "month", // Monat + Tag: week | month | full
  hh: Number(localStorage.getItem("kal-hh")) || 64,   // Stundenhöhe im Zeitraster (Pinch)
  hidden: new Set(JSON.parse(localStorage.getItem(hiddenKey) || "[]")),
  flash: new Set(),                                   // gerade von Ida geänderte Termine (blinken einmal)
};
/** Netzzustand setzen (kommt aus api.js bei jedem Aufruf, aus dem Browser bei online/offline) und die Oberfläche benachrichtigen. */
export function setNet(state) {
  if (S.net === state) return;
  S.net = state;
  document.body.classList.toggle("is-offline", state === "offline");
  document.body.classList.toggle("needs-login", state === "auth");
  dispatchEvent(new CustomEvent("kal:net", { detail: state }));
}
onNet(setNet);
addEventListener("offline", () => setNet("offline"));
addEventListener("online", () => { if (S.net === "offline") setNet("online"); });

export const saveHidden = () => localStorage.setItem(hiddenKey, JSON.stringify([...S.hidden]));
export const calOf = (id) => S.cals.find((c) => c.id === id);
export const writable = () => S.cals.filter((c) => !c.read_only && c.kind !== "holidays");

/** Sichtbarer Bereich der Ansicht [von, bis) */
export function rangeOf(view = S.view, d = S.date) {
  if (view === "monthday" || view === "month") { const g = monthGrid(d); return [g[0], addDays(g[41], 1)]; }
  if (view === "year") return [new Date(d.getFullYear(), 0, 1), new Date(d.getFullYear() + 1, 0, 1)];
  if (view === "week") { const w = weekStart(d); return [w, addDays(w, 7)]; }
  if (view === "day") return [d, addDays(d, 1)];
  if (view === "days3") return [d, addDays(d, 3)];
  return [S.agFrom, S.agTo];
}

export async function loadMeta() {
  const [cals, settings, meta] = await Promise.allSettled([api("/calendars"), api("/settings"), api("/meta")]);
  if (cals.status === "fulfilled") { S.cals = cals.value.calendars; repo.saveMeta("cals", S.cals); } else S.cals = (await repo.loadMeta("cals")) || [];
  if (settings.status === "fulfilled") { S.settings = settings.value; repo.saveMeta("settings", S.settings); } else S.settings = (await repo.loadMeta("settings")) || {};
  S.meta = meta.status === "fulfilled" ? meta.value : ((await repo.loadMeta("meta")) || {});
  if (meta.status === "fulfilled") repo.saveMeta("meta", S.meta);
}

/** Erst sofort aus dem Speicher (kein leerer Bildschirm), dann vom Server. */
export async function cachedEvents() {
  const [a, b] = rangeOf();
  return (await repo.windowInfo()) ? repo.range(a, b) : null;
}

/** Die Ansichten lesen aus dem lokalen Speicher (sofort, online wie offline); der Abgleich (sync.js) hält ihn aktuell.
 *  Nur beim allerersten Start ohne gespeicherte Daten muss das Netz einmal direkt gefragt werden. */
export async function loadEvents() {
  const [a, b] = rangeOf();
  const win = await repo.windowInfo();
  S.outside = false;
  if (!win) {
    const r = await api(`/events?from=${ymd(a)}&to=${ymd(b)}`);
    S.events = r.events; S.loadedAt = new Date();
    sync.pull({ full: true }).catch(() => {});
  } else {
    if (!(await sync.covers(a, b))) {
      if (S.net === "online") await sync.extend(a, b).catch(() => {});
      S.outside = !(await sync.covers(a, b));
    }
    S.events = await repo.range(a, b);
    S.loadedAt = new Date(Math.max(win.at || 0, sync.status.last || 0));
  }
  S.loaded = true;
}

export const visible = () => S.events.filter((e) => !S.hidden.has(e.calendar_id) && (S.settings.show_holidays !== false || !e.holiday));

// ---- Offline-Warteschlange (bis zur Outbox, siehe docs/KALENDER-OFFLINE-AUFGABEN.md Paket 11) ----
const qKey = "kal-queue";
const queue = () => JSON.parse(localStorage.getItem(qKey) || "[]");
const saveQueue = (q) => localStorage.setItem(qKey, JSON.stringify(q));
export const queued = () => queue().length;
export const queuedIds = () => new Set(queue().map((q) => q.id).filter(Boolean));
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());

/** Senden; ohne Verbindung wird die Änderung gemerkt und später nachgeholt. */
export async function send(path, opts, id) {
  try { return await api(path, opts); } catch (e) {
    if (e instanceof ApiError && (e.kind === "offline" || e.kind === "auth")) {
      saveQueue([...queue(), { path, id, qid: uid(), ...opts }]);
      return { queued: true };
    }
    throw e;
  }
}

let flushing = null;
/** Wartende Änderungen senden. Nur ein Durchlauf gleichzeitig (sonst käme alles doppelt an); jede gesendete Änderung wird sofort
 *  aus der Warteschlange genommen, auch wenn die App mittendrin schließt; neue Einträge während des Sendens bleiben erhalten. */
export function flushQueue() {
  return flushing ||= (async () => {
    let done = 0;
    for (const it of queue()) {
      try { await api(it.path, it); done++; } catch (e) {
        if (e instanceof ApiError && (e.kind === "offline" || e.kind === "auth")) continue; // bleibt in der Warteschlange
        done++; // Konflikt/Fehler: verwerfen
      }
      saveQueue(queue().filter((x) => (it.qid ? x.qid !== it.qid : JSON.stringify(x) !== JSON.stringify(it))));
    }
    if (!queue().length) dispatchEvent(new Event("kal:synced")); // alles gesendet (auch für wartende App-Updates)
    return done;
  })().finally(() => { flushing = null; });
}
