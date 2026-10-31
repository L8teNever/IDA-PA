// Einstiegspunkt der IDA-Code-PWA: Oberfläche starten, eigene Auswahlmenüs, Service Worker für den schnellen Start.
import { startApp } from "./app.js";
import { initSelects } from "./select.js";

initSelects();
startApp(document.getElementById("app"));

if ("serviceWorker" in navigator) {
  addEventListener("load", async () => {
    try {
      const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      // neue Version: sofort übernehmen, beim nächsten Öffnen ist sie da
      reg.addEventListener("updatefound", () => {
        const nw = reg.installing;
        nw?.addEventListener("statechange", () => { if (nw.state === "installed" && navigator.serviceWorker.controller) nw.postMessage({ type: "skip" }); });
      });
    } catch { /* ohne Service Worker geht es auch */ }
  });
}
