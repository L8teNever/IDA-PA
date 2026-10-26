// Thin wrapper around fetch for the /api/v1 endpoints.
const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function request(method, path, body) {
  const init = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.body = JSON.stringify(body); init.headers["content-type"] = "application/json"; }
  const r = await fetch(BASE + path, init);
  const data = r.headers.get("content-type")?.includes("application/json") ? await r.json() : null;
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

// Chat answers come as NDJSON lines; onEvent gets every parsed line.
export function streamChat(body, onEvent, signal) {
  return ndjson(BASE + "/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal }, onEvent);
}

// Follows an answer that is already running on the server (after a page switch / reopening the app).
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
