// Service worker der Nachrichten-App: zeigt Pushes als Benachrichtigung und öffnet beim Antippen die Nachricht.
// Er cached nichts und läuft nur, wenn ein Push kommt oder du die App öffnest (kein Hintergrund-Abfragen).
const OFFLINE = `<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nachrichten – offline</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#000;color:#e3e2e9;font:16px system-ui,sans-serif;text-align:center}
button{margin-top:18px;height:44px;padding:0 20px;border:0;border-radius:22px;background:#7fd3ff;color:#00344a;font:inherit;font-weight:600}</style></head>
<body><div><p>Gerade nicht erreichbar.<br>Prüf deine Verbindung.</p><button onclick="location.reload()">Nochmal versuchen</button></div></body></html>`;
const ICON = "/static/messages/icons/icon-192.png?v=1";
const BADGE = "/static/messages/icons/badge-96.png?v=1";  // Android-Statusleiste: einfarbige Sprechblase

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { "content-type": "text/html; charset=utf-8" } })));
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data ? e.data.text() : "" }; }
  const title = d.title && d.from && d.title !== d.from && !d.title.startsWith(d.from) ? `${d.from}: ${d.title}` : d.title || d.from || "IDA";
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    wins.forEach((w) => w.postMessage({ type: "new", id: d.id }));
    await self.registration.showNotification(title, {
      body: d.body || "", tag: d.id || "ida", renotify: true, icon: ICON, badge: BADGE, lang: "de",
      timestamp: d.at ? Date.parse(d.at) : Date.now(), requireInteraction: d.priority === "high",
      vibrate: d.priority === "high" ? [200, 100, 200, 100, 200] : [150, 80, 150], data: { id: d.id },
    });
  })());
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const id = e.notification.data?.id && e.notification.data.id !== "test" ? e.notification.data.id : "";
  const url = new URL(self.registration.scope + (id ? `#${id}` : "")).href;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const w = wins.find((c) => c.url.startsWith(self.registration.scope));
    if (w) { w.postMessage({ type: "open", id }); return w.focus(); }
    return self.clients.openWindow(url);
  })());
});

// Der Browser hat das Abo erneuert: neues beim Server eintragen
self.addEventListener("pushsubscriptionchange", (e) => {
  e.waitUntil((async () => {
    const { key } = await (await fetch("/api/v1/push/key", { credentials: "same-origin" })).json();
    const raw = Uint8Array.from(atob((key + "=".repeat((4 - (key.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
    const sub = await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: raw });
    await fetch("/api/v1/push/subscribe", { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...sub.toJSON(), device: "erneuert" }) });
  })());
});
