// Netzfehler sauber unterscheiden (reine Logik, ohne DOM – von api.js benutzt und in Node getestet).
//
// kind: offline  – kein Netz / Zeitüberschreitung → Änderungen warten
//       auth     – Anmeldung (Cloudflare Access) abgelaufen: die Antwort ist eine Weiterleitung oder eine Login-Seite
//       server   – 5xx, 429 oder unlesbare Antwort → später wiederholen
//       conflict – 409: der Termin wurde zwischenzeitlich geändert
//       gone     – 404: der Termin gibt es nicht mehr
//       invalid  – 400/403/422 mit Meldung des Kalenders → Eintrag bleibt, Fehler anzeigen

export const KINDS = ["offline", "auth", "server", "conflict", "gone", "invalid"];

export class ApiError extends Error {
  constructor(msg, status = 0, kind = "server", extra = {}) { super(msg); this.status = status; this.kind = kind; Object.assign(this, extra); }
  get offline() { return this.kind === "offline"; }
}

/** Antwort des Servers → Fehlerart (oder null bei Erfolg). `json` ist der gelesene Körper oder null, wenn er kein JSON war. */
export function classifyResponse({ status, type, ok, json }) {
  if (type === "opaqueredirect") return "auth";              // Access leitet zur Anmeldung um (redirect: "manual")
  if (ok && json !== null) return null;
  if (ok) return "auth";                                      // 200, aber kein JSON: eine Anmeldeseite statt der API
  if (status === 401) return "auth";
  if (status === 403) return json === null ? "auth" : "invalid";
  if (status === 409) return "conflict";
  if (status === 404) return json === null ? "server" : "gone";
  if (status === 429 || status >= 500) return "server";
  if (json === null) return "server";
  return "invalid";
}

/** `fetch` hat geworfen: Zeitüberschreitung oder kein Netz → offline. Bei scheinbar vorhandenem Netz prüft `probe()`
 *  (ein Aufruf von /health ohne Weiterleitung folgen) ob es die Anmeldung ist. */
export async function classifyFailure({ aborted = false, online = true, probe }) {
  if (aborted || !online) return "offline";
  try { return (await probe?.()) === "auth" ? "auth" : "offline"; } catch { return "offline"; }
}

export const MESSAGES = {
  offline: "Offline", auth: "Bitte neu anmelden", server: "Der Server antwortet gerade nicht richtig", conflict: "Der Termin wurde zwischenzeitlich geändert",
  gone: "Gibt es nicht mehr", invalid: "Ungültige Eingabe",
};
