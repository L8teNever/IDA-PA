/* ============================================================
   IDA HTML Editor — Service Worker  (sw.js)
   Cache-first für lokale Assets, Network-first für CDN-Ressourcen
   ============================================================ */

const CACHE_NAME = 'ida-html-2';

/* Alle lokalen Dateien, die gecacht werden sollen */
const LOCAL_ASSETS = [
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './css/ida.css',
  './lang.js',
  './fonts/GoogleSansFlex-latin.woff2',
  './fonts/GoogleSansFlex-latin-ext.woff2',
  './fonts/jetbrains-mono-400.ttf',
  './fonts/jetbrains-mono-500.ttf',
  './fonts/jetbrains-mono-600.ttf',
  './lib/jszip.min.js',
  './lib/html2canvas.min.js',
  './lib/prism.js',
  './lib/prism-tomorrow.min.css',
];

/* index.html bekommt Network-first, damit Änderungen sofort sichtbar sind */
const HTML_ASSETS = ['./', './index.html'];

/* ---- Install: lokale Assets in den Cache laden ---- */
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[IDA HTML SW] Installing – caching local assets');
      return cache.addAll(LOCAL_ASSETS);
    })
  );
  self.skipWaiting();
});

/* ---- Activate: alten Cache aufräumen ---- */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => {
            console.log('[IDA HTML SW] Deleting old cache:', key);
            return caches.delete(key);
          })
      )
    )
  );
  self.clients.claim();
});

/* ---- Fetch: Strategie je nach URL ---- */
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  const isLocal = url.origin === self.location.origin;
  const isHtml = url.pathname === '/' || url.pathname.endsWith('/') || url.pathname.endsWith('/index.html');

  if (isLocal && isHtml) {
    /* index.html: Network-first – Änderungen sofort sichtbar, Cache als Offline-Fallback */
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
  } else if (isLocal) {
    /* Andere lokale Dateien: Cache-first, Fallback auf Netzwerk */
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (response && response.status === 200 && response.type === 'basic') {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
  } else {
    /* CDN-Ressourcen (Monaco, Google Fonts, Cloudflare CDN):
       Network-first – bei Offline den Cache nutzen */
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
  }
});
