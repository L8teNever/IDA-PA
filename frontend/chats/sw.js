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

// ---------------- Benachrichtigungen (Web Push) ----------------
// Der Server schickt nur, wenn wirklich eine neue Nachricht da ist – das Handy schläft sonst (kein Timer, keine Verbindung).
// Als Bild dient das Profilbild der Person bzw. des Chats, rund zugeschnitten; fehlt es, das App-Symbol.
async function roundIcon(id) {
  try {
    const r = await fetch(`/api/v1/avatar/${encodeURIComponent(id)}`, { credentials: "same-origin" });
    if (!r.ok || typeof OffscreenCanvas === "undefined") return null;
    const img = await createImageBitmap(await r.blob()), n = 192, c = new OffscreenCanvas(n, n), g = c.getContext("2d");
    const s = Math.min(img.width, img.height);
    g.beginPath(); g.arc(n / 2, n / 2, n / 2, 0, Math.PI * 2); g.clip();
    g.drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, n, n);
    const b = new Uint8Array(await (await c.convertToBlob({ type: "image/png" })).arrayBuffer());
    let bin = ""; for (let i = 0; i < b.length; i += 0x8000) bin += String.fromCharCode(...b.subarray(i, i + 0x8000));
    return `data:image/png;base64,${btoa(bin)}`;
  } catch { return null; }
}
self.addEventListener("push", (e) => {
  let p = {};
  try { p = e.data ? e.data.json() : {}; } catch { /* leer */ }
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    // App ist gerade offen und sichtbar: dort erscheint schon der Hinweis oben – keine zusätzliche Meldung
    if (wins.some((w) => w.visibilityState === "visible" && w.focused)) return;
    const icon = (p.av && await roundIcon(p.av)) || "/icons/icon-192.png";
    await self.registration.showNotification(p.title || "Chats", {
      body: p.body || "Neue Nachricht", icon, badge: "/icons/badge-96.png", tag: `chat-${p.chat || ""}`, renotify: true,
      timestamp: p.at ? Date.parse(p.at) || Date.now() : Date.now(), data: { chat: p.chat || "" },
    });
  })());
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = e.notification.data?.chat ? `/chat/${encodeURIComponent(e.notification.data.chat)}` : "/";
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const w = wins.find((x) => new URL(x.url).origin === location.origin);
    if (w) { await w.focus(); w.postMessage({ type: "open", url }); return; }
    await self.clients.openWindow(url);
  })());
});
