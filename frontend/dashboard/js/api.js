// HTTP-Client für /api/v1 des Dashboards. Ändernde Aufrufe gehen immer als JSON (Schutz vor fremden Seiten).
const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function request(method, path, body) {
  const init = { method, headers: {}, credentials: "same-origin" };
  if (method !== "GET") { init.body = JSON.stringify(body ?? {}); init.headers["content-type"] = "application/json"; }
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
  get: (p, q) => request("GET", q ? `${p}?${new URLSearchParams(Object.entries(q).filter(([, v]) => v != null && v !== ""))}` : p),
  post: (p, b) => request("POST", p, b),
  put: (p, b) => request("PUT", p, b),
};

// Einem Vorgang folgen (NDJSON): onEvent bekommt {job} / {line} / {done}
export async function followJob(id, onEvent, signal) {
  const r = await fetch(`${BASE}/jobs/${encodeURIComponent(id)}/live`, { signal });
  if (!r.ok) throw new ApiError(`Fehler ${r.status}`, r.status);
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
