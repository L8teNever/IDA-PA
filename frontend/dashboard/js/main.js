// Einstiegspunkt der IDA-Dashboard-PWA: Oberfläche starten, Service Worker für den schnellen Start.
import { startApp } from "./app.js";

startApp(document.getElementById("app"));

if ("serviceWorker" in navigator) {
  addEventListener("load", async () => {
    try {
      const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      reg.addEventListener("updatefound", () => {
        const nw = reg.installing;
        nw?.addEventListener("statechange", () => { if (nw.state === "installed" && navigator.serviceWorker.controller) nw.postMessage({ type: "skip" }); });
      });
    } catch { /* ohne Service Worker geht es auch */ }
  });
}
