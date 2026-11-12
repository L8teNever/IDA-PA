// Service worker von IDA Mail: macht die App installierbar. Er cached nichts (Mails sind immer live); ist der Server nicht
// erreichbar, zeigt ein Seitenaufruf einen kurzen Hinweis statt eines Fehlers.
const OFFLINE = `<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mail – offline</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#000;color:#e3e2e6;font:16px system-ui,sans-serif;text-align:center}
button{margin-top:18px;height:44px;padding:0 20px;border:0;border-radius:22px;background:#ff9f7a;color:#4a1600;font:inherit;font-weight:600}</style></head>
<body><div><p>Mail ist gerade nicht erreichbar.<br>Prüf deine Verbindung.</p><button onclick="location.reload()">Nochmal versuchen</button></div></body></html>`;
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { "content-type": "text/html; charset=utf-8" } })));
});
