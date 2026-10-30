// Zustand, Laden der Termine, Offline-Spiegel (IndexedDB über repo.js) und Warteschlange für Offline-Änderungen.
import { api, ApiError, onNet } from "./api.js";
import { addDays, addMonths, monthGrid, startOfDay, weekStart, ymd } from "./dates.js";
import * as repo from "./repo.js";
import * as db from "./db.js";
import * as outbox from "./outbox.js";
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

// ---- Schreiben: direkt, wenn es klappt – sonst in die Outbox (siehe docs/KALENDER-OFFLINE-AUFGABEN.md Paket 11–13) ----
/** Zustand der Outbox für die Oberfläche (synchron lesbar): wird nach jeder Änderung neu geladen. */
export const Q = { count: 0, pending: 0, conflicts: 0, errors: 0, ids: new Set() };
export const queued = () => Q.count;
export const queuedIds = () => Q.ids;
export async function refreshQ() {
  const all = await outbox.all();
  Q.count = all.length;
  Q.pending = all.filter((e) => e.state === "pending" || e.state === "sending").length;
  Q.conflicts = all.filter((e) => e.state === "conflict").length;
  Q.errors = all.filter((e) => e.state === "error").length;
  Q.ids = new Set(all.flatMap((e) => [e.target, outbox.eventOf(e.target)]));
  navigator.setAppBadge?.(Q.count || 0).catch?.(() => {});
  if (!Q.count) navigator.clearAppBadge?.().catch?.(() => {});
  dispatchEvent(new CustomEvent("kal:outbox", { detail: { ...Q } }));
}
repo.onChange(() => { refreshQ().catch(() => {}); });
sync.onPushed(async () => { await refreshQ().catch(() => {}); if (!Q.count) dispatchEvent(new Event("kal:synced")); });

const dec = decodeURIComponent;
/** Anfrage der Oberfläche → Outbox-Änderung (oder null, wenn es keine Termin-Änderung ist). */
export async function toOp(path, opts = {}) {
  const [p, qs] = path.split("?");
  const scope = new URLSearchParams(qs || "").get("scope") || "all", method = (opts.method || "GET").toUpperCase();
  const seqOf = async (t) => (await db.get("occ", t))?.sequence ?? (await db.get("events", outbox.eventOf(t)))?.sequence ?? null;
  const titleOf = async (t) => (await db.get("occ", t))?.title || "";
  let m;
  if (p === "/events" && method === "POST") {
    return { type: "create", target: outbox.newLocalId(), body: { ...opts.body, uid: opts.body?.uid || `${outbox.uuid()}@ida-kalender` }, title: opts.body?.title || "" };
  }
  if ((m = /^\/events\/([^/]+)\/restore$/.exec(p)) && method === "POST") return { type: "restore", target: dec(m[1]), title: "", body: {} };
  if ((m = /^\/events\/([^/]+)$/.exec(p)) && (method === "PATCH" || method === "DELETE")) {
    const target = dec(m[1]), body = opts.body || {};
    // Werte vor meiner Änderung merken: danach lässt sich bei einem Konflikt sagen, wer welches Feld angefasst hat
    const had = { ...((await db.get("events", outbox.eventOf(target))) || {}), ...((await db.get("occ", target)) || {}) };
    const base = {};
    for (const k of Object.keys(body)) if (k in had) base[k] = had[k];
    return { type: method === "PATCH" ? "update" : "delete", target, scope, body, base, baseSequence: await seqOf(target), title: body.title || (await titleOf(target)) };
  }
  return null;
}

/** Eine Änderung speichern.
 *  Online: erst direkt (kurze Frist) – dann sieht man Fehler wie „Ende vor Anfang“ sofort. Offline, bei Zeitüberschreitung oder
 *  Serverfehler: in die Outbox – die Oberfläche wartet nie, der Abgleich sendet später (mit Idempotency-Key, also nie doppelt). */
export async function send(path, opts, id) {
  const op = await toOp(path, opts);
  if (!op) return api(path, opts);
  const opId = outbox.uuid();
  if (S.net === "online") {
    try {
      const [p, o] = sync.requestFor({ ...op, opId });
      const res = await api(p, { ...o, timeout: 4000 });
      sync.results.set(opId, res);
      sync.pull().catch(() => {});
      return res;
    } catch (e) {
      if (!(e instanceof ApiError) || !["offline", "auth", "server"].includes(e.kind)) {
        if (e instanceof ApiError && (e.kind === "conflict" || e.kind === "gone") && op.type !== "create") {   // jemand war schneller: in die Abgleich-Liste, die App zeigt den Konflikt
          const entry = await outbox.add({ ...op, opId });
          if (entry) await outbox.mark(entry.seq, { state: "conflict", gone: e.kind === "gone", server: e.server || null, mine: op.body, error: e.message });
          await refreshQ(); repo.emit("local");
          dispatchEvent(new CustomEvent("kal:conflict"));
        }
        throw e;
      }
    }
  }
  const entry = await outbox.add({ ...op, opId });
  await refreshQ(); repo.emit("local");
  navigator.storage?.persist?.().catch?.(() => {});
  navigator.serviceWorker?.ready.then((r) => r.sync?.register("kal-outbox")).catch(() => {});   // Background Sync: auch bei geschlossener App senden
  sync.push().catch(() => {});
  const own = entry && entry.opId === opId;
  return {
    queued: true, local: true, localId: op.type === "create" ? op.target : undefined,
    undo: own ? async () => {
      const cur = await outbox.get(entry.seq);
      if (cur && cur.opId === opId && cur.state === "pending") { await outbox.remove(cur.seq); await refreshQ(); repo.emit("local"); return; }  // noch nicht gesendet: einfach zurücknehmen
      const res = sync.results.get(opId);
      if (res?.change_id) { await api(`/changes/${res.change_id}/undo`, { method: "POST" }); await sync.pull(); return; }
      throw new Error("Rückgängig ist nicht mehr möglich");
    } : null,
  };
}

/** Alte Warteschlange (localStorage „kal-queue“, App vor der Outbox) einmalig in die Outbox übernehmen – nichts geht verloren. */
export async function importLegacy() {
  let q;
  try { q = JSON.parse(localStorage.getItem("kal-queue") || "[]"); } catch { q = []; }
  if (!q.length) { localStorage.removeItem("kal-queue"); return 0; }
  const have = new Set((await outbox.all()).map((e) => e.opId)), rest = [];
  let n = 0;
  for (const it of q) {
    const opId = it.qid || `legacy-${await hash(JSON.stringify(it))}`;
    if (have.has(opId)) continue;
    const op = await toOp(it.path, { method: it.method, body: it.body });
    if (!op) { rest.push(it); continue; }
    await outbox.add({ ...op, opId }); n++;
  }
  if (rest.length) localStorage.setItem("kal-queue", JSON.stringify(rest)); else localStorage.removeItem("kal-queue");
  await refreshQ();
  return n;
}
async function hash(t) {
  const b = new TextEncoder().encode(t);
  const h = await crypto.subtle.digest("SHA-256", b);
  return [...new Uint8Array(h)].slice(0, 12).map((x) => x.toString(16).padStart(2, "0")).join("");
}

/** Wartende Änderungen senden (der Abgleich übernimmt alles weitere). */
export const flushQueue = () => sync.push();
