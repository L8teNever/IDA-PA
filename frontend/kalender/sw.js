// Service Worker: App-Hülle offline (versionierte Dateien unter /s/<version>/), Daten kommen aus der API bzw. dem lokalen Speicher der App.
// Updates: ein neuer Worker wartet (kein skipWaiting), bis die Seite „skip“ schickt – die App tut das erst nach dem Abgleich
// der Offline-Änderungen (js/update.js). Die letzten zwei App-Versionen bleiben im Cache, damit offene Fenster nie ihre Dateien verlieren.
// Der Server ersetzt __VERSION__ und __SHELL__ (alle Dateien der Hülle) beim Ausliefern.
const V = "__VERSION__";
const CACHE = `kal-${V}`;
const META = "kal-meta";
const SHELL = __SHELL__;

self.addEventListener("install", (e) => {
  // alles oder nichts: fehlt eine Datei, wird dieser Worker nicht aktiv und der bisherige bleibt
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
    const keep = new Set([META, ...list.map((v) => `kal-${v}`)]);
    for (const k of await caches.keys()) if (k.startsWith("kal-") && !keep.has(k)) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("message", (e) => {
  if (e.data?.type === "skip") self.skipWaiting();
  else if (e.data?.type === "version") e.source?.postMessage({ type: "version", version: V });
});

self.addEventListener("fetch", (e) => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/") || u.pathname.startsWith("/ics/")) return;
  if (u.pathname.startsWith("/s/")) { // unveränderlich → Cache zuerst (jede Version, auch die vorige)
    e.respondWith(caches.match(r).then((hit) => hit || fetch(r).then((res) => { if (res.ok) { const c = res.clone(); caches.open(CACHE).then((x) => x.put(r, c)); } return res; })));
    return;
  }
  if (r.mode === "navigate") { // Seite: Netz zuerst, offline die gemerkte Hülle (für jeden Pfad: /monat/…, /termin/…)
    e.respondWith(fetch(r).then((res) => { if (res.ok) { const c = res.clone(); caches.open(CACHE).then((x) => x.put("/", c)); } return res; })
      .catch(async () => (await (await caches.open(CACHE)).match("/")) || (await caches.match("/")) || new Response("Offline", { status: 503 })));
  }
});

// Web Push (Opt-in in den Einstellungen): Erinnerung auch bei geschlossener App
self.addEventListener("push", (e) => {
  let p = {};
  try { p = e.data ? e.data.json() : {}; } catch { p = { title: e.data?.text() }; }
  const when = p.all_day ? "Heute" : p.start ? new Date(p.start).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "";
  const body = p.body || [when, p.location].filter(Boolean).join(" · ");
  e.waitUntil(self.registration.showNotification(p.title || "IDA Kalender", {
    body, tag: p.occurrence_id || "kalender", icon: `/s/${V}/icons/icon-192.png`, badge: `/s/${V}/icons/maskable-192.png`,
    data: { url: p.occurrence_id ? `/termin/${encodeURIComponent(p.occurrence_id)}` : "/" }, vibrate: [80, 40, 80],
  }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = e.notification.data?.url || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    const c = list.find((w) => new URL(w.url).origin === location.origin);
    return c ? c.navigate(url).then((w) => w?.focus()) : self.clients.openWindow(url);
  }));
});
