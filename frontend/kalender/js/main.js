import { setBase } from "./api.js";
import { mount } from "./app.js";

const base = document.querySelector('meta[name="kal-base"]')?.content || "";
setBase(base + "/api/v1");
mount(document.getElementById("app"), { pathBase: base, embedded: new URLSearchParams(location.search).has("eingebettet") });

if ("serviceWorker" in navigator && !base) {
  addEventListener("load", () => navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {}));
}
