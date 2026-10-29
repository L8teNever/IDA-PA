// Service Worker: App-Hülle offline (versionierte Dateien unter /s/<version>/), Daten kommen aus der API bzw. IndexedDB der App.
const V = "__VERSION__";
const CACHE = `kal-${V}`;
const SHELL = ["/", "/manifest.webmanifest",
  `/s/${V}/shared/css/tokens.css`, `/s/${V}/shared/css/components.css`, `/s/${V}/css/calendar.css`,
  `/s/${V}/js/main.js`, `/s/${V}/js/app.js`, `/s/${V}/js/api.js`, `/s/${V}/js/store.js`, `/s/${V}/js/dates.js`, `/s/${V}/js/layout.js`,
  `/s/${V}/js/views.js`, `/s/${V}/js/sheets.js`, `/s/${V}/js/gestures.js`, `/s/${V}/js/pickers.js`,
  `/s/${V}/shared/fonts/GoogleSansFlex-latin.woff2`, `/s/${V}/shared/fonts/GoogleSansFlex-latin-ext.woff2`, `/s/${V}/shared/fonts/MaterialSymbolsRounded.woff2`,
  `/s/${V}/icons/icon-192.png`];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => Promise.all(SHELL.map((u) => c.add(u).catch(() => {})))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/") || u.pathname.startsWith("/ics/")) return;
  if (u.pathname.startsWith("/s/")) { // unveränderlich → Cache zuerst
    e.respondWith(caches.match(r).then((hit) => hit || fetch(r).then((res) => { const c = res.clone(); caches.open(CACHE).then((x) => x.put(r, c)); return res; })));
    return;
  }
  if (r.mode === "navigate") { // Seite: Netz zuerst, offline die gemerkte Hülle
    e.respondWith(fetch(r).then((res) => { const c = res.clone(); caches.open(CACHE).then((x) => x.put("/", c)); return res; }).catch(() => caches.match("/")));
  }
});
