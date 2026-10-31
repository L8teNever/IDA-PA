// Synchronisation zwischen IndexedDB (`db.js`), Offline-Outbox (`outbox.js`) und dem Wiki-Server (`/api/v1/sync` + SSE `/api/v1/stream`).
import { ApiError, apiUrl, get, request } from "./api.js";
import { deletePages, getMeta, putPages, setMeta } from "./db.js";
import * as outbox from "./outbox.js";

let sse = null;
const listeners = new Set();

export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify(evt) {
  for (const fn of listeners) {
    try { fn(evt); } catch {}
  }
}

async function withLock(name, fn) {
  if (typeof navigator !== "undefined" && navigator.locks?.request) {
    return navigator.locks.request(name, { ifAvailable: true }, async (lock) => {
      if (!lock) return null;
      return fn();
    });
  }
  return fn();
}

export async function push() {
  return withLock("wiki-outbox-push", async () => {
    const queue = (await outbox.all()).filter((x) => x.state === "pending");
    let sent = 0;
    for (const item of queue) {
      try {
        const data = await request(item.method, item.path, item.body, {
          headers: { "Idempotency-Key": item.key },
        });
        await outbox.remove(item.seq);
        if (data?.page) await putPages([data.page]);
        sent += 1;
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 409) {
            await outbox.markConflict(item, err.message, err.data?.server || null);
            notify({ type: "conflict", item });
            continue;
          }
          if (err.status >= 400 && err.status < 500) {
            await outbox.markConflict(item, err.message, null);
            notify({ type: "error", item, detail: err.message });
            continue;
          }
          return { sent, stopped: "server" };
        }
        return { sent, stopped: "offline" };
      }
    }
    if (sent > 0) notify({ type: "pushed", sent });
    return { sent, stopped: null };
  });
}

export async function pull({ full = false } = {}) {
  const since = full ? 0 : (await getMeta("cursor", 0)) || 0;
  const data = await get(`/api/v1/sync?since=${since}`);
  if (data.full) {
    await putPages(data.pages || [], { replaceAll: true });
  } else {
    if (data.deleted_ids?.length) await deletePages(data.deleted_ids);
    if (data.pages?.length) await putPages(data.pages);
  }
  await setMeta("cursor", data.cursor || 0);
  await setMeta("spaces", data.spaces || ["privat", "memory", "ki"]);
  await setMeta("folders", data.folders || []);
  await setMeta("pending_proposals", data.pending_proposals || 0);
  notify({ type: "pulled", cursor: data.cursor, pending_proposals: data.pending_proposals || 0 });
  return data;
}

export async function syncAll({ full = false } = {}) {
  await push().catch(() => {});
  return pull({ full });
}

export function startStream() {
  if (typeof EventSource === "undefined" || sse) return;
  const connect = async () => {
    const cursor = (await getMeta("cursor", 0)) || 0;
    sse = new EventSource(apiUrl(`/api/v1/stream?since=${cursor}`));
    sse.onmessage = async (ev) => {
      let msg = {};
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "ready") {
        notify({ type: "online", online: true });
        return;
      }
      if (msg.type === "changed" || msg.type === "resync" || msg.type === "proposal") {
        await pull().catch(() => {});
        notify(msg);
      }
    };
    sse.onerror = () => {
      notify({ type: "online", online: navigator.onLine });
    };
  };
  connect();
}

export function stopStream() {
  if (sse) {
    sse.close();
    sse = null;
  }
}
