// "/code" – Unterseite „Code“ (Platzhalter, Inhalt folgt).
import { mainAgent } from "../store.js";
import { setAgent, setView } from "../ui/shell.js";

export function codePage() {
  setAgent(mainAgent());
  setView("page");
  document.getElementById("page").innerHTML = `
    <div class="page-head"><h1>Code</h1></div>
    <div class="soon-box"><span class="ms">code</span>Hier entsteht die Code-Seite.</div>`;
}
