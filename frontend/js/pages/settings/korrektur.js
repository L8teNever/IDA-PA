// Einstellungen → Tippfehler: which engine corrects typos first in the Chats app; the other one takes over at a limit or error.
import { api } from "../../api.js";
import { state } from "../../store.js";
import { $, esc, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

export async function render(el) {
  const s = await api.get("/korrektur/settings");
  let engine = s.engine;
  const draw = () => {
    el.innerHTML = subHead("Tippfehler", "/einstellungen") + `
      <p class="sub-intro">Die <b>Chats-App</b> schlägt dir über dem Eingabefeld eine korrigierte Version vor, sobald du kurz nicht tippst. Hier wählst du, wer zuerst korrigiert. Erreicht ein Claude-Account sein Limit, nimmt Haiku den nächsten Account; geht gar nichts mehr, springt die andere Engine ein.</p>
      <div class="seg" id="kEng">${s.engines.map((e) => `<button type="button" data-e="${e.id}" class="${e.id === engine ? "on" : ""}">${esc(e.name)}</button>`).join("")}</div>
      <div class="rf-sum"><span class="ms">info</span><span>${engine === "aus" ? "Keine Vorschläge: die Leiste über dem Eingabefeld erscheint nicht, und es wird nichts verbraucht."
        : engine === "haiku"
        ? `<b>${esc(s.model)}</b> versteht den Satz und trifft auch grobe Tippfehler gut, braucht aber ein paar Sekunden und zählt ins Claude-Limit. Bei Limit oder Fehler übernimmt das lokale T5-Modell.`
        : "Das <b>lokale T5-Modell</b> läuft im Stack, kostet kein Limit, findet aber weniger Fehler. Bei Fehler übernimmt Claude Haiku."}</span></div>`;
    $("kEng").onclick = async (e) => {
      const b = e.target.closest("[data-e]"); if (!b || b.dataset.e === engine) return;
      try { await api.patch("/korrektur/settings", { engine: b.dataset.e }); engine = b.dataset.e; state.boot.korrektur = engine; draw(); }
      catch (err) { toast(`Nicht gespeichert: ${err.message}`); }
    };
  };
  draw();
}
