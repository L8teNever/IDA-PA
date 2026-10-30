// REST + Live-Updates. Basis konfigurierbar (eingebettet in IDA: "/kal/api/v1").
// Fehler werden nach Art unterschieden (net.js): offline ≠ Anmeldung abgelaufen ≠ Serverfehler.
import { ApiError, MESSAGES, classifyFailure, classifyResponse } from "./net.js";

export { ApiError };
let base = "/api/v1";
export const setBase = (b) => { base = b; };
export const TIMEOUT = 10000;

let netHandler = null;
/** Der Store meldet sich hier an und bekommt "online" | "offline" | "auth" bei jedem Ergebnis. */
export const onNet = (fn) => { netHandler = fn; };
const tell = (s) => netHandler?.(s);

/** Ein Aufruf von /health, der Weiterleitungen nicht folgt: Access-Anmeldung → "auth", sonst "ok"; kein Netz wirft. */
export async function probe() {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 4000);
  try {
    const r = await fetch(`${base}/health`, { redirect: "manual", credentials: "same-origin", signal: ctl.signal, cache: "no-store" });
    if (r.type === "opaqueredirect") return "auth";
    if (r.ok && (r.headers.get("content-type") || "").includes("json")) return "ok";
    return r.status === 401 || r.status === 403 || r.ok ? "auth" : "ok";
  } finally { clearTimeout(t); }
}

export async function api(path, { method = "GET", body, form, headers = {}, timeout = TIMEOUT } = {}) {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), timeout);
  let res;
  try {
    res = await fetch(base + path, {
      method, redirect: "manual", credentials: "same-origin", signal: ctl.signal,
      headers: { ...(body ? { "content-type": "application/json" } : {}), ...headers },
      body: form || (body ? JSON.stringify(body) : undefined),
    });
  } catch (e) {
    clearTimeout(t);
    const kind = await classifyFailure({ aborted: e?.name === "AbortError", online: navigator.onLine !== false, probe });
    tell(kind);
    throw new ApiError(kind === "auth" ? MESSAGES.auth : "offline", 0, kind);
  }
  clearTimeout(t);
  let json = null;
  try { json = await res.json(); } catch { /* keine JSON-Antwort */ }
  const kind = classifyResponse({ status: res.status, type: res.type, ok: res.ok, json });
  if (!kind) { tell("online"); return json; }
  tell(kind === "auth" ? "auth" : "online");
  const detail = json?.detail || json?.message;
  throw new ApiError(typeof detail === "string" ? detail : kind === "server" ? `${MESSAGES.server} (${res.status || "?"})` : MESSAGES[kind], res.status, kind, { server: json?.server });
}

/** SSE mit Neuverbinden; onChange wird bei jeder Änderung (auch fremder, z. B. von Ida) aufgerufen.
 *  onState(true|false, "auth"?): Verbindung steht / ist weg; bei abgelaufener Anmeldung wird nicht endlos neu verbunden. */
export function live(onChange, onState) {
  let es, closed = false, timer = null, waitingAuth = false;
  const open = () => {
    if (closed || waitingAuth) return;
    es = new EventSource(base + "/stream");
    es.onopen = () => onState?.(true);
    es.onmessage = (m) => { try { onChange(JSON.parse(m.data)); } catch { /* ping */ } };
    es.onerror = async () => {
      onState?.(false); es.close();
      if (navigator.onLine !== false) {
        let k = "ok"; try { k = await probe(); } catch { /* offline */ }
        if (k === "auth") { waitingAuth = true; tell("auth"); onState?.(false, "auth"); return; }
      }
      timer = setTimeout(open, 3000);
    };
  };
  const retry = () => { if (waitingAuth && navigator.onLine !== false && document.visibilityState === "visible") { waitingAuth = false; open(); } };
  addEventListener("visibilitychange", retry); addEventListener("online", retry);
  open();
  return () => { closed = true; clearTimeout(timer); es?.close(); removeEventListener("visibilitychange", retry); removeEventListener("online", retry); };
}
