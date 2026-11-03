// Service Worker für IDA Dashboard: die Oberfläche liegt unter /s/<version>/ im Cache und startet sofort.
// Daten (Stacks, Messwerte) kommen immer frisch vom Server – die API wird nie zwischengespeichert.
const V = "__VERSION__";
const CACHE = `dashboard-${V}`;
const SHELL = __SHELL__;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith("dashboard-") && k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("message", (e) => {
  if (e.data?.type === "skip") self.skipWaiting();
});

self.addEventListener("fetch", (e) => {
  const r = e.request;
  const u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  if (u.pathname.startsWith("/s/")) {
    e.respondWith(caches.match(r).then((hit) => hit || fetch(r).then((res) => {
      if (res.ok) { const clone = res.clone(); caches.open(CACHE).then((x) => x.put(r, clone)); }
      return res;
    })));
    return;
  }
  if (r.mode === "navigate") {
    e.respondWith(fetch(r).then((res) => {
      if (res.ok) { const clone = res.clone(); caches.open(CACHE).then((x) => x.put("/", clone)); }
      return res;
    }).catch(async () => (await (await caches.open(CACHE)).match("/")) || new Response("Offline", { status: 503 })));
  }
});
