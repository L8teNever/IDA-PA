// Outbox: alle Änderungen, die noch nicht auf dem Server sind. Reine Logik + Speicher (db.js), kein DOM.
//
// Eintrag: { seq, v: 1, opId, type: "create"|"update"|"delete"|"restore", target, scope, body, baseSequence, title,
//            state: "pending"|"sending"|"conflict"|"error", tries, nextTry, createdAt, error, server, mine }
//   target  = Vorkommen-ID (bei „create“ die lokale ID „local-<uuid>“); bei Serien „<id>_<JJJJMMTTTHHMM>“
//   opId    = Idempotency-Key (dieselbe Änderung wird nie doppelt ausgeführt, auch wenn die Verbindung beim Senden abreißt)
//   server  = bei Konflikt der aktuelle Stand des Servers; mine = was ich ändern wollte
// Lokale IDs enthalten bewusst keinen Unterstrich („local-…“): vor dem ersten „_“ steht immer die Termin-ID.
import * as db from "./db.js";

export const V = 1;
export const uuid = () => (globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`);
export const newLocalId = () => `local-${uuid().replace(/-/g, "").slice(0, 16)}`;
export const isLocal = (id) => String(id).startsWith("local-");
export const eventOf = (target) => String(target).split("_")[0];

/** Zusammenfassen: eine neue Änderung `n` kommt zu den vorhandenen `list`. Nur wartende („pending“) Einträge werden angefasst, nie
 *  gerade gesendete. Gibt die neue Liste zurück (kann kürzer werden: anlegen + löschen heben sich auf). Reine Funktion. */
export function coalesce(list, n) {
  const ev = eventOf(n.target);
  const idx = list.map((e, i) => [e, i]).filter(([e]) => eventOf(e.target) === ev).map(([, i]) => i);
  const last = idx.length ? idx[idx.length - 1] : -1;
  const e = last >= 0 ? list[last] : null;
  const out = list.slice();
  // Termin noch gar nicht auf dem Server (lokale ID, Anlegen wartet): Änderungen und Löschen betreffen nur diesen Eintrag – auch wenn Anhänge dahinter stehen
  const mine = idx.map((i) => list[i]);
  const born = mine.find((x) => x.type === "create" && isLocal(x.target) && x.state === "pending");
  if (born && mine.every((x) => x.state === "pending") && n.type !== "upload") {
    if (n.type === "delete" && (n.scope || "all") === "all") return out.filter((x) => eventOf(x.target) !== ev);
    if (n.type === "update" && !String(n.target).includes("_")) {
      const i = out.indexOf(born);
      out[i] = { ...born, body: { ...born.body, ...n.body }, title: n.body?.title || born.title }; return out;
    }
  }
  if (!e || e.state !== "pending") return [...out, n];
  const same = e.target === n.target && (e.scope || "all") === (n.scope || "all");
  if (e.type === "create" && isLocal(e.target)) {
    if (n.type === "update" && !String(n.target).includes("_")) {   // einzelner Termin: anlegen + ändern = anlegen mit den neuen Werten
      out[last] = { ...e, body: { ...e.body, ...n.body }, title: n.body?.title || e.title }; return out;
    }
    if (n.type === "delete" && (n.scope || "all") === "all") return out.filter((x) => eventOf(x.target) !== ev);  // anlegen + löschen = nichts
  }
  if (e.type === "update" && same) {
    if (n.type === "update") { out[last] = { ...e, body: { ...e.body, ...n.body }, title: n.body?.title || e.title }; return out; }
    if (n.type === "delete") { out[last] = { ...n, baseSequence: e.baseSequence, createdAt: e.createdAt }; return out; }
  }
  if (e.type === "delete" && same && n.type === "delete") return out;
  return [...out, n];
}

// ---------------- Speicher ----------------
export const all = async () => (await db.all("outbox")).sort((a, b) => a.seq - b.seq);
export const pending = async (now = Date.now()) => (await all()).filter((e) => e.state === "pending" && (e.nextTry || 0) <= now);
/** Nächster Eintrag, der gesendet werden darf: je Termin immer der erste in der Reihe. Wartet dieser noch (Wartezeit nach einem Fehler)
 *  oder hängt in einem Konflikt/Fehler, warten auch alle späteren Einträge desselben Termins – sonst käme z. B. ein Anhang vor dem Anlegen an. */
export async function nextDue(skip = new Set(), now = Date.now()) {
  const seen = new Set(skip);
  for (const e of await all()) {
    const ev = eventOf(e.target);
    if (seen.has(ev)) continue;
    if (e.state === "pending" && (e.nextTry || 0) <= now) return e;
    seen.add(ev);
  }
  return null;
}
export const count = async () => (await db.count("outbox"));
export const countOpen = async () => (await all()).filter((e) => e.state !== "done").length;
export const get = (seq) => db.get("outbox", seq);

/** Neue Änderung vormerken (mit Zusammenfassen). Gibt den Eintrag zurück, oder null, wenn sich alles aufgehoben hat. */
export async function add(op) {
  const entry = { v: V, opId: uuid(), state: "pending", tries: 0, nextTry: 0, createdAt: Date.now(), scope: "all", body: {}, ...op };
  const before = await all();
  const after = coalesce(before, entry);
  const kept = new Set(after.map((e) => e.seq).filter((s) => s !== undefined));
  const ops = [];
  for (const e of before) if (!kept.has(e.seq)) { ops.push({ op: "del", store: "outbox", key: e.seq }); if (e.type === "upload") ops.push({ op: "del", store: "files", key: e.opId }); }
  for (const e of after) {
    if (e.seq === undefined) ops.push({ op: "put", store: "outbox", value: e });
    else { const old = before.find((x) => x.seq === e.seq); if (JSON.stringify(old) !== JSON.stringify(e)) ops.push({ op: "put", store: "outbox", value: e }); }
  }
  if (ops.length) await db.batch(ops);
  const fresh = await all();
  return fresh.find((e) => e.opId === entry.opId) || fresh.find((e) => eventOf(e.target) === eventOf(entry.target)) || null;   // sonst: hat sich aufgehoben
}

export async function mark(seq, patch) {
  const e = await db.get("outbox", seq);
  if (!e) return null;
  const n = { ...e, ...patch };
  await db.put("outbox", n);
  return n;
}
export const remove = (seq) => db.del("outbox", seq);

/** Nach dem Anlegen auf dem Server: lokale ID → echte ID in allen wartenden Einträgen (auch in Vorkommen-IDs von Serien). */
export async function remap(localId, realId, sequence = 0) {
  const ops = [];
  for (const f of await db.all("files")) if (eventOf(f.target) === localId) ops.push({ op: "put", store: "files", value: { ...f, target: String(f.target).replace(localId, realId) } });
  for (const e of await all()) {
    if (eventOf(e.target) !== localId) continue;
    ops.push({ op: "put", store: "outbox", value: { ...e, target: String(e.target).replace(localId, realId), baseSequence: e.type === "create" ? e.baseSequence : sequence } });
  }
  if (ops.length) await db.batch(ops);
}

/** Termine, deren lokale Vorschau von einer offenen Änderung stammt – der Abgleich fasst sie nicht an. */
export async function protectedEvents() { return new Set((await all()).map((e) => eventOf(e.target))); }

/** Wartezeit nach dem n-ten Fehlversuch: 5 s · 3^n, höchstens 15 Minuten. */
export const backoff = (tries) => Math.min(5000 * 3 ** Math.max(0, tries - 1), 15 * 60 * 1000);

export async function summary() {
  const l = await all();
  return { total: l.length, pending: l.filter((e) => e.state === "pending" || e.state === "sending").length, conflict: l.filter((e) => e.state === "conflict").length, error: l.filter((e) => e.state === "error").length };
}
