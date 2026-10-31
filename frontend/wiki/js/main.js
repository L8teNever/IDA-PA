// Einstiegspunkt der IDA Wiki PWA: startet die Oberfläche und registriert den Offline-Service-Worker.
import { basePrefix } from "./api.js";
import { startWikiApp } from "./app.js";
import { initSelects } from "./select.js";
import * as sync from "./sync.js";

initSelects();
const root = document.getElementById("app");
startWikiApp(root);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", async () => {
    try {
      const reg = await navigator.serviceWorker.register(`${basePrefix()}/sw.js`, { type: "module", scope: `${basePrefix()}/` });
      reg.addEventListener("updatefound", () => {
        const nw = reg.installing;
        nw?.addEventListener("statechange", async () => {
          if (nw.state === "installed" && navigator.serviceWorker.controller) {
            await sync.push().catch(() => {});
            nw.postMessage({ type: "skip" });
          }
        });
      });
      navigator.serviceWorker.addEventListener("message", (e) => {
        if (e.data?.type === "synced") sync.pull().catch(() => {});
      });
    } catch {}
  });
}
