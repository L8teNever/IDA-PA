// App-Updates ohne Datenverlust: Der neue Service Worker wartet, bis die Änderungen aus der Warteschlange gesendet sind;
// erst dann wird die App neu geladen. (Siehe docs/KALENDER-OFFLINE-AUFGABEN.md, Paket 4.)
import { S, flushQueue, queued } from "./store.js";
import { toast } from "./sheets.js";

let reg = null, waitingForSync = false, offered = false;

/** Anzahl wartender Offline-Änderungen (heute die alte Warteschlange; mit der Outbox kommt deren Zähler dazu). */
export const pendingCount = () => queued();

async function apply() {
  const w = reg?.waiting;
  if (!w) return;
  if (pendingCount() > 0) {
    if (S.net === "online") await flushQueue().catch(() => {});
    if (pendingCount() > 0) { waitingForSync = true; toast("Wird nach dem Abgleich aktualisiert"); return; }
  }
  waitingForSync = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => location.reload(), { once: true });
  w.postMessage({ type: "skip" });
}

function offer() {
  if (offered) return;
  offered = true;
  toast("Neue Version da", [{ label: "Neu laden", run: () => { offered = false; apply(); } }], { sticky: true });
}

export function start({ scope = "/" } = {}) {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("/sw.js", { scope }).then((r) => {
    reg = r;
    const watch = (sw) => sw?.addEventListener("statechange", () => { if (sw.state === "installed" && navigator.serviceWorker.controller) offer(); });
    if (r.waiting && navigator.serviceWorker.controller) offer();
    r.addEventListener("updatefound", () => watch(r.installing));
    const check = () => r.update().catch(() => {});
    addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") check(); });
    setInterval(check, 30 * 60 * 1000);
  }).catch(() => {});
  addEventListener("kal:net", () => { if (S.net === "online" && waitingForSync) apply(); });
  addEventListener("kal:synced", () => { if (waitingForSync) apply(); });
}
