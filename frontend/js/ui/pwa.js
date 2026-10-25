// Install as an app (PWA): registers the service worker and offers "App installieren" in the menu
// as soon as the browser allows it (Chrome/Edge; on iPhone: Teilen → Zum Home-Bildschirm).
import { $, toast } from "./dom.js";

let prompt = null;

export function initPwa() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  const btn = $("btnInstall");
  addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    prompt = e;
    btn.hidden = false;
  });
  addEventListener("appinstalled", () => {
    btn.hidden = true;
    prompt = null;
    toast("IDA ist jetzt als App installiert");
  });
  btn.onclick = async () => {
    if (!prompt) return;
    prompt.prompt();
    await prompt.userChoice.catch(() => null);
    prompt = null;
    btn.hidden = true;
  };
}
