// Abgleich mit dem Server (Lesen): holt nur, was sich geändert hat, und legt es in den lokalen Speicher (repo.apply).
// Die Oberfläche liest immer aus dem Speicher; dieser Abgleich hält ihn aktuell – bei App-Start, wenn das Netz wiederkommt,
// beim Zurückkehren in die App, bei Änderungen (auch von Ida) und alle 5 Minuten.
import { api, ApiError } from "./api.js";
import * as db from "./db.js";
import * as repo from "./repo.js";

export const BACK_MONTHS = 3, AHEAD_MONTHS = 12;
const pad = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** Gewünschter Zeitraum offline: 3 Monate zurück bis 12 Monate voraus (volle Monate). [von, bis) */
export function desiredWindow(now = new Date()) {
  return { from: ymd(new Date(now.getFullYear(), now.getMonth() - BACK_MONTHS, 1)), to: ymd(new Date(now.getFullYear(), now.getMonth() + AHEAD_MONTHS, 1)) };
}

/** Reine Entscheidung: voll oder Delta, mit welchem Zeitraum? Einmal pro Tag voll (Feiertage, Zeitraum verschiebt sich). */
export function plan(meta, { full = false, now = new Date() } = {}) {
  const want = desiredWindow(now), today = ymd(now);
  if (full || !meta || meta.cursor == null || meta.fullDay !== today) return { full: true, since: null, ...want, metaAt: "" };
  return { full: false, since: meta.cursor, from: meta.from, to: meta.to, metaAt: meta.metaAt || "" };
}

let running = null, again = false;
export const status = { last: 0, error: null, running: false };

/** Eine Abfrage nach Änderungen. Läuft schon eine, wird danach noch einmal geprüft (nichts geht verloren, nichts läuft doppelt). */
export function pull(opts = {}) {
  if (running) { again = true; return running; }
  running = (async () => {
    status.running = true;
    try {
      do {
        again = false;
        const p = plan(await repo.syncMeta(), opts);
        const q = new URLSearchParams({ from: p.from, to: p.to });
        if (!p.full) { q.set("since", p.since); if (p.metaAt) q.set("meta_at", p.metaAt); }
        const res = await api(`/sync?${q}`);
        const protectedIds = await protectedEvents();
        const done = await repo.apply(res, { protectedIds });
        status.last = Date.now(); status.error = null;
        if (done.full || done.upserts || done.removed) repo.emit("sync");
      } while (again);
      return true;
    } catch (e) {
      status.error = e;
      if (e instanceof ApiError && ["offline", "auth"].includes(e.kind)) return false;
      if (e instanceof ApiError && e.kind === "invalid" && /Zeitraum|Ende/.test(e.message)) return false;
      throw e;
    } finally { status.running = false; running = null; }
  })();
  return running;
}

/** Termine, deren lokale Vorschau von einer wartenden Änderung stammt – der Abgleich fasst sie nicht an (kommt mit der Outbox). */
async function protectedEvents() { return new Set(); }

/** Liegt der Bereich außerhalb des gespeicherten Zeitraums? (dann gibt es offline nichts zu zeigen) */
export async function covers(a, b) {
  const m = await repo.syncMeta();
  if (!m) return false;
  const slack = (s, n) => ymd(new Date(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10) + n));  // Monatsraster reicht ein paar Tage über den Monat hinaus
  return ymd(a) >= slack(m.from, -14) && ymd(b) <= slack(m.to, 14);
}

/** Online über den Zeitraum hinaus geblättert: den Monat direkt holen und merken (fällt beim nächsten vollen Abgleich wieder raus). */
export async function extend(a, b) {
  const m = (await repo.syncMeta()) || {};
  const f = ymd(a), t = ymd(b);
  const res = await api(`/events?from=${f}&to=${t}`);
  const old = await db.all("occ", { index: "last", gte: f });
  const ops = old.filter((e) => e.day < t).map((e) => ({ op: "del", store: "occ", key: e.id }));
  for (const e of res.events) ops.push({ op: "put", store: "occ", value: db.normOcc(e) });
  const nm = { ...m, from: m.from && m.from < f ? m.from : f, to: m.to && m.to > t ? m.to : t };
  ops.push({ op: "put", store: "meta", key: "sync", value: nm });
  ops.push({ op: "put", store: "meta", key: "window", value: { at: Date.now(), from: nm.from, to: nm.to } });
  await db.batch(ops);
  repo.emit("extend");
}

/** Auslöser registrieren. `visible` ist true, wenn die App im Vordergrund ist. */
export function start({ onNetBack } = {}) {
  const go = () => pull().catch(() => {});
  addEventListener("online", go);
  addEventListener("focus", go);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") go(); });
  setInterval(() => { if (document.visibilityState === "visible") go(); }, 5 * 60 * 1000);
  go();
  return go;
}
