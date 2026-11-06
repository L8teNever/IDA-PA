// Service Worker: App-Hülle offline (versionierte Dateien unter /s/<version>/). Die Aufgaben selbst kommen immer frisch aus der API.
// Der Server ersetzt __VERSION__ und __SHELL__ (alle Dateien der Hülle) beim Ausliefern. Die letzten zwei Versionen bleiben im Cache.
const V = "__VERSION__";
const CACHE = `tasks-${V}`;
const SHELL = __SHELL__;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const mine = (await caches.keys()).filter((k) => k.startsWith("tasks-") && k !== CACHE);
    for (const k of mine.slice(0, -1)) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  if (u.pathname.startsWith("/s/")) {   // unveränderlich → Cache zuerst
    e.respondWith(caches.match(r).then((hit) => hit || fetch(r).then((res) => { if (res.ok) { const c = res.clone(); caches.open(CACHE).then((x) => x.put(r, c)); } return res; })));
    return;
  }
  if (r.mode === "navigate") {           // Seite: Netz zuerst, offline die gemerkte Hülle
    e.respondWith(fetch(r).then((res) => { if (res.ok) { const c = res.clone(); caches.open(CACHE).then((x) => x.put("/", c)); } return res; })
      .catch(async () => (await caches.match("/")) || new Response("Offline", { status: 503 })));
  }
});
