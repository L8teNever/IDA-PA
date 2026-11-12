// Einstellungen → Tippfehler: which engine corrects typos first in the Chats app; the other one takes over at a limit or error.
import { api } from "../../api.js";
import { state } from "../../store.js";
import { $, esc, sw, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

export async function render(el) {
  const s = await api.get("/korrektur/settings");
  let engine = s.engine;
  const o = { vorfilter: s.vorfilter, kontext: s.kontext, kontext_n: s.kontext_n };
  const save = async (patch) => {
    try { Object.assign(o, await api.patch("/korrektur/settings", patch)); draw(); } catch (err) { toast(`Nicht gespeichert: ${err.message}`); }
  };
  const draw = () => {
    el.innerHTML = subHead("Tippfehler", "/einstellungen") + `
      <p class="sub-intro">Die <b>Chats-App</b> schlägt dir über dem Eingabefeld eine korrigierte Version vor, sobald du kurz nicht tippst. Hier wählst du, wer zuerst korrigiert. Erreicht ein Claude-Account sein Limit, nimmt Haiku den nächsten Account; geht gar nichts mehr, springt die andere Engine ein.</p>
      <div class="seg" id="kEng">${s.engines.map((e) => `<button type="button" data-e="${e.id}" class="${e.id === engine ? "on" : ""}">${esc(e.name)}</button>`).join("")}</div>
      <div class="rf-sum"><span class="ms">info</span><span>${engine === "aus" ? "Keine Vorschläge: die Leiste über dem Eingabefeld erscheint nicht, und es wird nichts verbraucht."
        : engine === "haiku"
        ? `<b>${esc(s.model)}</b> versteht den Satz und trifft auch grobe Tippfehler gut, braucht aber ein paar Sekunden und zählt ins Claude-Limit. Bei Limit oder Fehler übernimmt das lokale T5-Modell.`
        : "Das <b>lokale T5-Modell</b> läuft im Stack, kostet kein Limit, findet aber weniger Fehler. Bei Fehler übernimmt Claude Haiku."}</span></div>
      <div class="srow"><span class="smain"><b>Schneller Vorab-Check</b><small>Ein Wörterbuch (Deutsch + Englisch) prüft in Millisekunden, ob überhaupt ein Wort falsch sein kann. Nur dann fragt er die KI – „Hi, wie geht's“ kostet so nichts.</small></span>${sw(o.vorfilter, 'data-k="vorfilter"')}</div>
      <div class="srow"><span class="smain"><b>Chat-Verlauf als Kontext</b><small>Haiku sieht die letzten Nachrichten des Chats und versteht so besser, was du sagen willst. Korrigiert wird nur deine neue Nachricht.</small></span>${sw(o.kontext, 'data-k="kontext"')}</div>
      ${o.kontext ? `<div class="srow"><span class="smain"><b>Nachrichten als Kontext</b><small>Standard: 5</small></span><div class="seg" id="kN">${[3, 5, 10, 20].map((n) => `<button type="button" data-n="${n}" class="${n === o.kontext_n ? "on" : ""}">${n}</button>`).join("")}</div></div>` : ""}`;
    el.querySelectorAll("[data-k]").forEach((b) => (b.onclick = () => save({ [b.dataset.k]: !o[b.dataset.k] })));
    if ($("kN")) $("kN").onclick = (e) => { const b = e.target.closest("[data-n]"); if (b && +b.dataset.n !== o.kontext_n) save({ kontext_n: +b.dataset.n }); };
    $("kEng").onclick = async (e) => {
      const b = e.target.closest("[data-e]"); if (!b || b.dataset.e === engine) return;
      try { await api.patch("/korrektur/settings", { engine: b.dataset.e }); engine = b.dataset.e; state.boot.korrektur = engine; draw(); }
      catch (err) { toast(`Nicht gespeichert: ${err.message}`); }
    };
  };
  draw();
}
