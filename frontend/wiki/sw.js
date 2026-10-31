// Service Worker für IDA Wiki: App-Shell offline unter /s/<version>/, lokaler IndexedDB-Speicher + Hintergrund-Outbox-Sync.
import * as outbox from "/s/__VERSION__/js/outbox.js";
import * as sync from "/s/__VERSION__/js/sync.js";

const V = "__VERSION__";
const CACHE = `wiki-${V}`;
const META = "wiki-meta";
const SHELL = __SHELL__;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const meta = await caches.open(META);
    const hit = await meta.match("versions");
    let list = hit ? await hit.json() : [];
    if (!list.includes(V)) list.push(V);
    list = list.slice(-2);
    await meta.put("versions", new Response(JSON.stringify(list)));
    const keep = new Set([META, ...list.map((v) => `wiki-${v}`)]);
    for (const k of await caches.keys()) {
      if (k.startsWith("wiki-") && !keep.has(k)) await caches.delete(k);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("message", (e) => {
  if (e.data?.type === "skip") self.skipWaiting();
  else if (e.data?.type === "version") e.source?.postMessage({ type: "version", version: V });
  else if (e.data?.type === "test-sync") e.waitUntil(runSync().catch(() => {}));
});

self.addEventListener("sync", (e) => {
  if (e.tag === "wiki-outbox") e.waitUntil(runSync());
});

async function runSync() {
  const r = await sync.push();
  const left = (await outbox.all().catch(() => [])).filter((x) => x.state === "pending");
  for (const c of await self.clients.matchAll({ type: "window", includeUncontrolled: true })) {
    c.postMessage({ type: "synced", result: r });
  }
  if (r && (r.stopped === "offline" || r.stopped === "server" || left.length)) {
    throw new Error("später erneut versuchen");
  }
}

self.addEventListener("fetch", (e) => {
  const r = e.request;
  const u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/") || u.pathname.startsWith("/mcp")) {
    return;
  }
  if (u.pathname.startsWith("/s/")) {
    e.respondWith(
      caches.match(r).then((hit) => hit || fetch(r).then((res) => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then((x) => x.put(r, clone));
        }
        return res;
      })),
    );
    return;
  }
  if (r.mode === "navigate") {
    e.respondWith(
      fetch(r).then((res) => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then((x) => x.put("/", clone));
        }
        return res;
      }).catch(async () =>
        (await (await caches.open(CACHE)).match("/")) ||
        (await caches.match("/")) ||
        new Response("Offline", { status: 503 })
      ),
    );
  }
});
