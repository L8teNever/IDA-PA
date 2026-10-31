// Offline-Outbox für Schreibvorgänge: vergibt eindeutige Idempotency-Keys, damit kein Speichern doppelt ausgeführt wird.
import { outboxAdd, outboxAll, outboxDelete, outboxPut } from "./db.js";

function makeKey() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `wk-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export async function enqueue(method, path, body, meta = {}) {
  const item = await outboxAdd({
    key: makeKey(),
    method,
    path,
    body,
    meta,
    state: "pending",
    created_at: new Date().toISOString(),
    error: null,
    server_copy: null,
  });
  if (typeof navigator !== "undefined" && "serviceWorker" in navigator) {
    navigator.serviceWorker.ready
      .then((reg) => reg.sync?.register?.("wiki-outbox"))
      .catch(() => {});
  }
  return item;
}

export async function all() {
  return outboxAll();
}

export async function remove(seq) {
  return outboxDelete(seq);
}

export async function markConflict(item, detail, serverCopy = null) {
  await outboxPut({
    ...item,
    state: "conflict",
    error: detail || "Bearbeitungskonflikt",
    server_copy: serverCopy,
  });
}

export async function countSummary() {
  const items = await outboxAll();
  return {
    pending: items.filter((x) => x.state === "pending").length,
    conflict: items.filter((x) => x.state === "conflict").length,
  };
}
