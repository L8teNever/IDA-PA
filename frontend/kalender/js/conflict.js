// Konflikte: Ich habe offline etwas geändert, in der Zwischenzeit hat jemand (z. B. Ida) denselben Termin geändert. Reine Logik, ohne DOM.
//
// Eintrag (Outbox): body = was ich ändern wollte, base = Werte VOR meiner Änderung (beim Speichern gemerkt), server = aktueller Termin
// (kommt mit der 409-Antwort). Betrifft die Änderung des Servers ANDERE Felder als meine, wird automatisch zusammengeführt;
// nur bei denselben Feldern fragt die App.
import * as ob from "./outbox.js";

export const LABELS = {
  title: "Titel", start: "Beginn", end: "Ende", all_day: "Ganztägig", location: "Ort", description: "Beschreibung", url: "Link", color: "Farbe",
  status: "Status", transparency: "Frei/Belegt", travel_min: "Reisezeit", alarms: "Erinnerungen", alarm_action: "Erinnerungsart", alarm_task: "Aufgabe",
  calendar_id: "Kalender", repeat: "Wiederholung", rrule: "Wiederholung",
};
const TIME = new Set(["start", "end"]);

/** Werte vergleichbar machen: Zeiten auf Minuten, Listen als Text, leer = leer. */
export function norm(key, v) {
  if (v === undefined || v === null || v === "") return "";
  if (TIME.has(key) && typeof v === "string") return v.slice(0, 16);
  if (Array.isArray(v)) return JSON.stringify([...v].sort());
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Server-Wert eines Feldes aus seiner Termin-Ansicht (für „Wiederholung“ der Klartext). */
const serverValue = (server, key) => (key === "repeat" || key === "rrule" ? server.rrule || "" : key === "end" && server.all_day ? server.end : server[key]);

/** Für jedes Feld, das ich ändern wollte: was war vorher, was will ich, was ist jetzt auf dem Server – und wer von uns hat es angefasst? */
export function diff(entry) {
  const mine = entry.body || {}, base = entry.base || {}, server = entry.server || {};
  const rows = [];
  for (const key of Object.keys(mine)) {
    if (key === "sequence" || key === "uid" || mine[key] === undefined) continue;
    const m = norm(key, mine[key]), s = norm(key, serverValue(server, key)), b = key in base ? norm(key, base[key]) : null;
    rows.push({ key, label: LABELS[key] || key, mine: mine[key], server: serverValue(server, key), same: m === s, serverChanged: b === null ? !(m === s) : s !== b });
  }
  return rows;
}

/** Zusammenführen ohne Nachfrage? Ja, wenn der Server an keinem Feld etwas geändert hat, das ich auch ändern wollte
 *  (oder wir dasselbe wollen). Serien-Vorkommen („_“ im Ziel) werden nie automatisch zusammengeführt. */
export function autoMerge(entry) {
  if (entry.type !== "update" || !entry.server || entry.gone || String(entry.target).includes("_")) return { ok: false, conflicts: diff(entry) };
  const rows = diff(entry), conflicts = rows.filter((r) => !r.same && r.serverChanged);
  return conflicts.length ? { ok: false, conflicts } : { ok: true, conflicts: [] };
}

/** (Neue opId: der Server hat die alte Anfrage samt Fehlerantwort gemerkt – eine geänderte Anfrage ist eine neue.)
 *  Neuer Eintrag nach „Meine übernehmen“ bzw. automatischem Zusammenführen: gleiche Änderung auf dem neuen Stand des Servers. */
export const rebase = (entry, auto = false) => ({ opId: ob.uuid(), state: "pending", tries: 0, nextTry: 0, error: "", gone: false, server: null, baseSequence: entry.server?.sequence ?? entry.baseSequence, merges: (entry.merges || 0) + (auto ? 1 : 0) });

/** Termin wurde gelöscht, ich wollte ihn ändern: „Neu anlegen“ aus dem letzten bekannten Stand + meinen Änderungen. */
export function recreateBody(last, mine) {
  const keep = ["title", "location", "description", "url", "color", "status", "transparency", "travel_min", "alarms", "alarm_action", "alarm_task", "calendar_id", "all_day", "start", "end"];
  const b = {};
  for (const k of keep) if (last && last[k] !== undefined && last[k] !== null) b[k] = last[k];
  if (b.start && b.start.length > 16 && !b.all_day) b.start = b.start.slice(0, 16);
  if (b.end && b.end.length > 16 && !b.all_day) b.end = b.end.slice(0, 16);
  return { ...b, ...mine, uid: `${ob.uuid()}@ida-kalender` };
}
