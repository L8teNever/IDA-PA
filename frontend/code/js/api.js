// HTTP-Client: alle Aufrufe gehen an /api/v1 dieser App, die sie an IDA weiterreicht.
const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function request(method, path, body) {
  const init = { method, headers: {}, credentials: "same-origin" };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.body = JSON.stringify(body); init.headers["content-type"] = "application/json"; }
  let r;
  try { r = await fetch(BASE + path, init); } catch { throw new ApiError("Keine Verbindung", 0); }
  const data = r.headers.get("content-type")?.includes("application/json") ? await r.json().catch(() => null) : null;
  if (!r.ok) {
    const d = data?.detail;
    const msg = Array.isArray(d) ? d.map((x) => `${x.loc?.slice(-1)[0] ?? ""}: ${x.msg}`).join(", ") : d || `Fehler ${r.status}`;
    throw new ApiError(msg, r.status);
  }
  return data;
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b) => request("POST", p, b ?? {}),
  patch: (p, b) => request("PATCH", p, b),
  put: (p, b) => request("PUT", p, b),
  del: (p) => request("DELETE", p),
};

// Antworten kommen als NDJSON-Zeilen; onEvent bekommt jede Zeile als Objekt.
export function streamChat(body, onEvent, signal) {
  return ndjson(BASE + "/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal }, onEvent);
}

// Einer Antwort folgen, die auf dem Server schon läuft (nach Seitenwechsel oder erneutem Öffnen).
export function followChat(chatId, onEvent, signal) {
  return ndjson(`${BASE}/chats/${encodeURIComponent(chatId)}/live`, { signal }, onEvent);
}

async function ndjson(url, init, onEvent) {
  const r = await fetch(url, init);
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new ApiError(d.detail || `Fehler ${r.status}`, r.status);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

export const rawUrl = (projectId, path) => `${BASE}/code/projects/${projectId}/raw/${encodeURI(String(path).replace(/\\/g, "/"))}`;
