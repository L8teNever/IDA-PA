// Service worker: makes IDA installable as an app. It caches nothing (data must always be live);
// only when the server cannot be reached, a page load shows a short offline notice instead of an error.
const OFFLINE = `<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>IDA – offline</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#000;color:#e3e2e9;font:16px system-ui,sans-serif;text-align:center}
button{margin-top:18px;height:48px;padding:0 22px;border:0;border-radius:24px;background:#7fd3ff;color:#00344a;font:inherit;font-weight:600}</style></head>
<body><div><div style="font-size:40px">😴</div><p>IDA ist gerade nicht erreichbar.<br>Prüf deine Verbindung.</p>
<button onclick="location.reload()">Nochmal versuchen</button></div></body></html>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return; // everything else: straight to the network
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { "content-type": "text/html; charset=utf-8" } })));
});
