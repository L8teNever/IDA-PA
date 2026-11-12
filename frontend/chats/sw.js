// Service worker der Chats-App: macht sie installierbar und zeigt bei fehlender Verbindung einen Hinweis. Chats und Nachrichten sind immer live;
// nur die App-Dateien (/s/<version>/…) liegen im Handy – so startet die App schneller und das Funkmodul bleibt öfter aus (Akku).
const OFFLINE = `<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chats – offline</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#000;color:#e3e2e9;font:16px system-ui,sans-serif;text-align:center}
button{margin-top:18px;height:44px;padding:0 20px;border:0;border-radius:22px;background:#7ee2b8;color:#003824;font:inherit;font-weight:600}</style></head>
<body><div><p>Gerade nicht erreichbar.<br>Prüf deine Verbindung.</p><button onclick="location.reload()">Nochmal versuchen</button></div></body></html>`;
self.addEventListener("install", () => self.skipWaiting());
const STATIC = "chats-static";
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
let pruned = "";
async function cached(req, ver) {
  const c = await caches.open(STATIC);
  if (ver && pruned !== ver) {   // neue Version: Dateien älterer Versionen wegräumen
    pruned = ver;
    for (const k of await c.keys()) { const m = /\/s\/([^/]+)\//.exec(new URL(k.url).pathname); if (m && m[1] !== ver) c.delete(k); }
  }
  const hit = await c.match(req);
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok && r.type === "basic") c.put(req, r.clone());
  return r;
}
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method === "GET" && u.origin === location.origin && u.pathname.startsWith("/s/")) {
    return e.respondWith(cached(e.request, /^\/s\/([^/]+)\//.exec(u.pathname)?.[1] || ""));
  }
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { "content-type": "text/html; charset=utf-8" } })));
});
