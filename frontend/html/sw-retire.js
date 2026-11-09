/* ============================================================
   IDA HTML – Abschalt-Service-Worker für die alten CLEO-Adressen
   Früher lief der Editor unter /version/vN/ mit eigenem Service Worker.
   Der liefert die alte Seite sonst ewig aus dem Cache. Dieser Worker
   ersetzt ihn, löscht nur die alten Caches (Projekte liegen im
   localStorage und bleiben), meldet sich ab und lädt die neue Seite.
   ============================================================ */
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => !n.startsWith('ida-html')).map((n) => caches.delete(n)));
    await self.registration.unregister();
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach((c) => c.navigate('/').catch(() => {}));
  })());
});
