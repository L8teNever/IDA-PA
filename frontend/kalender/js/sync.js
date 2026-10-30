// Abgleich mit dem Server (Lesen): holt nur, was sich geändert hat, und legt es in den lokalen Speicher (repo.apply).
// Die Oberfläche liest immer aus dem Speicher; dieser Abgleich hält ihn aktuell – bei App-Start, wenn das Netz wiederkommt,
// beim Zurückkehren in die App, bei Änderungen (auch von Ida) und alle 5 Minuten.
import { api, ApiError } from "./api.js";
import * as db from "./db.js";
import { autoMerge, rebase } from "./conflict.js";
import { withLock } from "./lock.js";
import * as outbox from "./outbox.js";
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
const protectedEvents = () => outbox.protectedEvents();

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
export function start() {
  const go = () => syncNow().catch(() => {});
  addEventListener("online", go);
  addEventListener("focus", go);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") go(); });
  setInterval(() => { if (document.visibilityState === "visible") go(); }, 5 * 60 * 1000);
  go();
  return go;
}

// ---------------- Senden (Outbox → Server) ----------------
const enc = encodeURIComponent;
/** Antworten gesendeter Änderungen (für „Rückgängig“ kurz nach dem Speichern): opId → { change_id, event } */
export const results = new Map();
const pushed = new Set();
export const onPushed = (fn) => { pushed.add(fn); return () => pushed.delete(fn); };

/** Die HTTP-Anfrage zu einem Outbox-Eintrag. Jede trägt ihren Idempotency-Key: dieselbe Anfrage wirkt nie zweimal. */
export function requestFor(e) {
  const headers = { "Idempotency-Key": e.opId }, scope = e.scope || "all", seq = e.baseSequence;
  if (e.type === "create") return ["/events", { method: "POST", body: e.body, headers }];
  if (e.type === "update") return [`/events/${enc(e.target)}?scope=${scope}`, { method: "PATCH", body: seq == null ? e.body : { ...e.body, sequence: seq }, headers }];
  if (e.type === "delete") return [`/events/${enc(e.target)}?scope=${scope}${seq == null ? "" : `&sequence=${seq}`}`, { method: "DELETE", headers }];
  if (e.type === "restore") return [`/events/${enc(outbox.eventOf(e.target))}/restore`, { method: "POST", headers }];
  throw new Error(`Unbekannte Art: ${e.type}`);
}

let kick = null;
/** Alle wartenden Änderungen der Reihe nach senden. Nur ein Durchlauf gleichzeitig (auch über Fenster/Service Worker hinweg). */
export async function push() {
  const out = await withLock(async () => {
    const sum = { sent: 0, conflicts: 0, errors: 0, merged: 0, stopped: "" };
    for (const e of await outbox.all()) if (e.state === "sending") await outbox.mark(e.seq, { state: "pending" });   // vorheriger Durchlauf abgestürzt: Idempotenz macht Wiederholen sicher
    const blocked = new Set();
    for (let guard = 0; guard < 5000; guard++) {
      const e = (await outbox.pending()).find((x) => !blocked.has(outbox.eventOf(x.target)));
      if (!e) break;
      await outbox.mark(e.seq, { state: "sending" });
      try {
        const [path, opts] = requestFor(e);
        const res = await api(path, { ...opts, timeout: 15000 });
        if (e.type === "create" && res.existing && res.deleted) {
          await outbox.mark(e.seq, { state: "conflict", gone: true, tries: 0, error: "Dieser Termin wurde inzwischen gelöscht." });
          blocked.add(outbox.eventOf(e.target)); sum.conflicts++; continue;
        }
        results.set(e.opId, res);
        if (e.type === "create" && res.event) await outbox.remap(outbox.eventOf(e.target), res.event.event_id, res.event.sequence ?? 0);
        await outbox.remove(e.seq);
        sum.sent++;
      } catch (err) {
        if (!(err instanceof ApiError)) { await outbox.mark(e.seq, { state: "pending" }); throw err; }
        if (["offline", "server", "auth"].includes(err.kind)) {
          const tries = (e.tries || 0) + 1;
          await outbox.mark(e.seq, { state: "pending", tries, nextTry: err.kind === "auth" ? 0 : Date.now() + outbox.backoff(tries), error: err.message });
          sum.stopped = err.kind; break;
        }
        if (err.kind === "conflict" && e.type === "update" && (e.merges || 0) < 3) {      // anderes Feld geändert → ohne Nachfrage zusammenführen
          const withServer = { ...e, server: err.server || null };
          if (err.server && autoMerge(withServer).ok) { await outbox.mark(e.seq, { ...rebase(withServer, true), state: "pending" }); sum.merged++; continue; }
        }
        blocked.add(outbox.eventOf(e.target));
        if (err.kind === "conflict" || err.kind === "gone") {
          await outbox.mark(e.seq, { state: "conflict", gone: err.kind === "gone", server: err.server || null, mine: e.body, error: err.message }); sum.conflicts++;
        } else { await outbox.mark(e.seq, { state: "error", error: err.message }); sum.errors++; }
      }
    }
    return sum;
  });
  if (!out) return null;
  if (out.sent || out.conflicts || out.errors || out.merged) { await pull().catch(() => {}); repo.emit("push"); }
  for (const fn of pushed) { try { fn(out); } catch { /* Hörer */ } }
  schedule();
  return out;
}

/** Danach: für den nächsten Versuch (Wartezeit nach Fehlern) einen Zeitgeber setzen. */
async function schedule() {
  clearTimeout(kick);
  const next = (await outbox.all()).filter((e) => e.state === "pending" && e.nextTry > Date.now()).map((e) => e.nextTry).sort((a, b) => a - b)[0];
  if (next) kick = setTimeout(() => push().catch(() => {}), Math.max(1000, next - Date.now()));
}

/** Senden, dann lesen. */
export async function syncNow() { const r = await push(); await pull().catch(() => {}); return r; }

// ---------------- Konflikte lösen ----------------
/** Nutzer entscheidet: "mine" (meine Änderung auf dem neuen Stand erneut senden), "server" (verwerfen, Stand des Servers behalten),
 *  "recreate" (gelöschten Termin neu anlegen), "discard" (verwerfen). */
export async function resolve(seq, choice) {
  const e = await outbox.get(seq);
  if (!e) return;
  if (choice === "mine") await outbox.mark(seq, rebase(e));
  else if (choice === "recreate") {
    const { recreateBody } = await import("./conflict.js");
    const last = (await db.get("occ", e.target)) || (await db.get("events", outbox.eventOf(e.target))) || {};
    await outbox.mark(seq, { opId: outbox.uuid(), type: "create", target: outbox.newLocalId(), body: recreateBody(last, e.body), state: "pending", tries: 0, nextTry: 0, gone: false, server: null, error: "" });
  } else await outbox.remove(seq);          // "server" / "discard"
  repo.emit("local");
  if (choice === "mine" || choice === "recreate") push().catch(() => {}); else await pull().catch(() => {});
}

/** Fehlgeschlagenen/wartenden Eintrag noch einmal versuchen. */
export async function retry(seq) { await outbox.mark(seq, { opId: outbox.uuid(), state: "pending", tries: 0, nextTry: 0, error: "" }); return push(); }
export async function discard(seq) { await outbox.remove(seq); repo.emit("local"); await pull().catch(() => {}); }
