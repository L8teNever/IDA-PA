// Service worker der Chats-App: macht sie installierbar und zeigt bei fehlender Verbindung einen Hinweis. Cached nichts (Chats sind immer live).
const OFFLINE = `<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chats – offline</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#000;color:#e3e2e9;font:16px system-ui,sans-serif;text-align:center}
button{margin-top:18px;height:44px;padding:0 20px;border:0;border-radius:22px;background:#7ee2b8;color:#003824;font:inherit;font-weight:600}</style></head>
<body><div><p>Gerade nicht erreichbar.<br>Prüf deine Verbindung.</p><button onclick="location.reload()">Nochmal versuchen</button></div></body></html>`;
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { "content-type": "text/html; charset=utf-8" } })));
});
