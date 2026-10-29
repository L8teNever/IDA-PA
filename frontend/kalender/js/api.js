// REST + Live-Updates. Basis konfigurierbar (eingebettet in IDA: "/kal/api/v1").
let base = "/api/v1";
export const setBase = (b) => { base = b; };

export class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }

export async function api(path, { method = "GET", body, form } = {}) {
  let res;
  try {
    res = await fetch(base + path, {
      method, headers: body ? { "content-type": "application/json" } : undefined,
      body: form || (body ? JSON.stringify(body) : undefined),
    });
  } catch { throw new ApiError("offline", 0); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.detail || data.message || `Fehler ${res.status}`, res.status);
  return data;
}

/** SSE mit Neuverbinden; onChange wird bei jeder Änderung (auch fremder, z. B. von Ida) aufgerufen. */
export function live(onChange, onState) {
  let es, closed = false;
  const open = () => {
    if (closed) return;
    es = new EventSource(base + "/stream");
    es.onopen = () => onState?.(true);
    es.onmessage = (m) => { try { onChange(JSON.parse(m.data)); } catch { /* ping */ } };
    es.onerror = () => { onState?.(false); es.close(); setTimeout(open, 3000); };
  };
  open();
  return () => { closed = true; es?.close(); };
}
