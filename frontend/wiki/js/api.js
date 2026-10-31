// HTTP-Client für die Wiki-API (`/api/v1/*`). Respektiert einen optionalen Proxy-Präfix aus `<meta name="wiki-base">`.

export function basePrefix() {
  if (typeof document === "undefined") return "";
  const m = document.querySelector('meta[name="wiki-base"]');
  const v = m?.getAttribute("content") || "";
  return v === "__BASE__" ? "" : v.replace(/\/+$/, "");
}

export function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${basePrefix()}${p}`;
}

export class ApiError extends Error {
  constructor(status, detail, data = {}) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.data = data;
  }
}

export async function request(method, path, body = undefined, { headers = {}, signal } = {}) {
  const opts = {
    method,
    credentials: "same-origin",
    headers: { ...headers },
    signal,
  };
  if (body !== undefined && !(body instanceof FormData)) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  } else if (body instanceof FormData) {
    opts.body = body;
  }
  const res = await fetch(apiUrl(path), opts);
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json().catch(() => ({})) : null;
  if (!res.ok) {
    throw new ApiError(res.status, data?.detail || res.statusText || "Anfrage fehlgeschlagen", data || {});
  }
  return data;
}

export const get = (path, opts) => request("GET", path, undefined, opts);
export const post = (path, body, opts) => request("POST", path, body, opts);
export const put = (path, body, opts) => request("PUT", path, body, opts);
export const patch = (path, body, opts) => request("PATCH", path, body, opts);
export const del = (path, opts) => request("DELETE", path, undefined, opts);

export async function uploadFile(pageId, file) {
  const fd = new FormData();
  fd.append("file", file, file.name || "datei");
  return request("POST", `/api/v1/files/${encodeURIComponent(pageId)}`, fd);
}
