// Sperre für den Abgleich: nur ein Fenster bzw. der Service Worker sendet gleichzeitig (sonst würde alles doppelt probiert).
// Web Locks, wo vorhanden; sonst ein Zeitstempel im lokalen Speicher (älter als 60 s gilt als frei).
import * as db from "./db.js";

const NAME = "kal-sync", STALE = 60000;
let local = null;   // innerhalb dieses Fensters: dasselbe Versprechen weitergeben, statt zweimal zu laufen

/** Führt fn aus, wenn niemand sonst gerade abgleicht; sonst null. */
export async function withLock(fn, { name = NAME } = {}) {
  if (local) return null;
  const run = async () => { local = true; try { return await fn(); } finally { local = null; } };
  if (globalThis.navigator?.locks?.request) return navigator.locks.request(name, { ifAvailable: true }, (lock) => (lock ? run() : null));
  const t = await db.get("meta", "lock");
  if (t && Date.now() - t < STALE) return null;
  await db.put("meta", Date.now(), "lock");
  try { return await run(); } finally { await db.del("meta", "lock"); }
}
