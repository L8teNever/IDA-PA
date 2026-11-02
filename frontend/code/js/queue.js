// Warteschlange (wie im IDA-Chat): während die KI arbeitet, warten neue Nachrichten hier und gehen nacheinander raus.
// Jede kann sofort gesendet werden („Jetzt senden“ stoppt die laufende Antwort und schickt sie als Nächstes),
// zurück ins Eingabefeld zum Bearbeiten geholt oder gelöscht werden. Die Schlange gehört zum offenen Thread;
// wird ein anderer Thread geöffnet, landet, was noch wartet, im Entwurf des alten Threads (geht nicht verloren).
import { ctx, send, stopAnswer, threadKey } from "./chat.js";
import { appendDraft } from "./draft.js";
import { $, esc } from "./ui.js";

let items = []; // { id, text, opts }
let key = null; // Thread, zu dem die Schlange gehört
let steer = false; // „Jetzt senden“ gedrückt: der Stopp dadurch soll die Schlange nicht anhalten
let seq = 0;

export function enqueue(text, opts) {
  key = threadKey();
  items.push({ id: ++seq, text, opts });
  render();
}

function next() {
  const it = items.shift();
  render();
  if (it) send(it.text, it.opts);
}

function sendNow(id) {
  const i = items.findIndex((x) => x.id === id);
  if (i < 0) return;
  items.unshift(...items.splice(i, 1)); // nach vorne
  if (ctx.busy) { steer = true; render(); stopAnswer(); } else next();
}

function render() {
  const box = $("queue");
  if (!box) return;
  box.hidden = !items.length;
  box.innerHTML = items.length ? `<div class="q-head"><span class="ms">schedule_send</span>${items.length === 1 ? "1 Nachricht wartet" : `${items.length} Nachrichten warten`}
      <small>${steer ? "wird gleich gesendet …" : ctx.busy ? "geht raus, sobald die Antwort fertig ist" : "pausiert"}</small></div>
    ${items.map((x, n) => `<div class="q-item" data-id="${x.id}"><span class="q-n">${n + 1}</span>
      <span class="q-text">${x.opts?.files?.length ? `📎${x.opts.files.length} ` : ""}${esc(x.text)}</span>
      <button type="button" class="q-btn now" data-act="now" title="Antwort unterbrechen und diese Nachricht jetzt senden"><span class="ms">bolt</span><span class="q-lbl">Jetzt senden</span></button>
      <button type="button" class="q-btn" data-act="edit" title="Zurück ins Eingabefeld zum Bearbeiten" aria-label="Bearbeiten"><span class="ms">edit</span></button>
      <button type="button" class="q-btn" data-act="del" title="Aus der Warteschlange löschen" aria-label="Löschen"><span class="ms">close</span></button></div>`).join("")}` : "";
}

// nach dem Zeichnen des Eingabefelds aufrufen (das Feld wird pro Thread neu gezeichnet)
export function bindQueue() {
  $("queue").onclick = (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const id = +b.closest("[data-id]").dataset.id;
    const it = items.find((x) => x.id === id);
    if (!it) return;
    if (b.dataset.act === "now") return sendNow(id);
    items = items.filter((x) => x.id !== id);
    if (b.dataset.act === "edit") {
      const input = $("input");
      input.value = it.text + (input.value.trim() ? "\n" + input.value : "");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }
    render();
  };
  render();
}

// eine Antwort ist fertig: die nächste geht raus – außer der Nutzer hat bewusst gestoppt (dann wartet die Schlange)
document.addEventListener("code:done", (e) => {
  if (e.detail?.left) return; // Thread verlassen / Verbindung neu aufgebaut: kein echtes Ende
  if (items.length && (!e.detail?.stopped || steer)) { steer = false; setTimeout(next, 150); }
  else render();
});
document.addEventListener("code:busy", render);

// Thread verlassen oder App geschlossen: was noch wartet, kommt in den Entwurf (sonst wäre es weg)
export function stashQueue(keepalive = false) {
  if (!items.length) return;
  const text = items.map((x) => x.text).filter(Boolean).join("\n\n");
  items = []; steer = false;
  render();
  appendDraft(key, text, keepalive);
}
addEventListener("pagehide", () => stashQueue(true));
// neuer Thread hat seine ID bekommen
document.addEventListener("code:thread", (e) => { if (key === `p-${e.detail.project}`) key = `t-${e.detail.id}`; });
document.addEventListener("code:mounted", () => {
  if (!items.length) return render();
  if (key !== threadKey()) return stashQueue(); // anderer Thread geöffnet: Wartendes in den Entwurf des alten Threads
  if (!ctx.busy) {
    steer = false;
    setTimeout(next, 150); // derselbe Thread nach Verbindungsabbruch neu geladen und die Antwort ist schon fertig
  }
  render();
});
