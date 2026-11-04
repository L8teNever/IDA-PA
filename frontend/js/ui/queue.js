// Message queue (like T3 Code): while Ida answers, new messages wait here and go out one after another.
// Each one can be sent right away ("Jetzt senden" stops the running answer and sends it next),
// taken back into the input field to edit (with its files), or deleted. The queue belongs to the open chat;
// draft.js keeps it on the server, so it survives closing the app and changing the chat.
import { api } from "../api.js";
import { ctx, send, stopAnswer } from "../pages/chat.js";
import { restoreFiles } from "./attach.js";
import { $, esc } from "./dom.js";

let items = []; // { id, text, skill, opts }
let steer = false; // "Jetzt senden" pressed: the stop it causes must not pause the queue
let seq = 0;

export const queued = () => items.length;

export function enqueue(text, skill, opts) {
  items.push({ id: ++seq, text, skill, opts });
  render();
}

// for the draft
export const queueItems = () => items.map((x) => ({ text: x.text, skill: x.skill || null, model: x.opts?.model || null, account_id: x.opts?.accountId || null,
  effort: x.opts?.effort || null, files: (x.opts?.files || []).map((f) => f.id) }));

export function restoreQueue(list = []) {
  items = [...list.map((x) => ({ id: ++seq, text: x.text, skill: x.skill, opts: { model: x.model, accountId: x.account_id, effort: x.effort, files: x.files } })), ...items];
  render();
}

export function clearQueue() {
  items = []; steer = false;
  render();
}

function next() {
  const it = items.shift();
  render();
  if (it) send(it.text, { skill: it.skill, ...it.opts });
}

function sendNow(id) {
  const i = items.findIndex((x) => x.id === id);
  if (i < 0) return;
  items.unshift(...items.splice(i, 1)); // to the front
  if (ctx.busy) { steer = true; render(); stopAnswer(); } else next();
}

const thumbs = (list) => list?.length ? `<span class="q-files">${list.map((f) => f.kind === "image"
  ? `<img src="/api/v1/files/${esc(f.id)}" alt="" title="${esc(f.name)}">` : `<span class="ms" title="${esc(f.name)}">attach_file</span>`).join("")}</span>` : "";

function render() {
  document.dispatchEvent(new CustomEvent("queue:change"));
  const box = $("queue");
  box.hidden = !items.length;
  document.body.classList.toggle("has-queue", items.length > 0);
  box.innerHTML = items.length ? `<div class="q-head"><span class="ms">schedule_send</span>${items.length === 1 ? "1 Nachricht wartet" : `${items.length} Nachrichten warten`}
      <small>${steer ? "wird gleich gesendet …" : ctx.busy ? "geht raus, sobald Ida fertig ist" : "pausiert"}</small></div>
    ${items.map((x, n) => `<div class="q-item" data-id="${x.id}"><span class="q-n">${n + 1}</span>
      ${thumbs(x.opts?.files)}<span class="q-text">${x.skill ? `<b>/${esc(x.skill)}</b> ` : ""}${esc(x.text)}</span>
      <button type="button" class="q-btn now" data-act="now" title="Antwort unterbrechen und diese Nachricht jetzt senden"><span class="ms">bolt</span><span class="q-lbl">Jetzt senden</span></button>
      <button type="button" class="q-btn" data-act="edit" title="Zurück ins Eingabefeld zum Bearbeiten" aria-label="Bearbeiten"><span class="ms">edit</span></button>
      <button type="button" class="q-btn" data-act="del" title="Aus der Warteschlange löschen" aria-label="Löschen"><span class="ms">close</span></button></div>`).join("")}` : "";
}

export function initQueue() {
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
      input.value = (it.skill ? `/${it.skill} ` : "") + it.text + (input.value.trim() ? "\n" + input.value : "");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      restoreFiles(it.opts?.files);
      input.focus();
    } else (it.opts?.files || []).forEach((f) => api.del(`/uploads/${f.id}`).catch(() => {}));
    render();
  };
  // an answer ended: the next one goes out – unless the user stopped it on purpose (then the queue waits)
  document.addEventListener("chat:done", (e) => {
    const stopped = e.detail?.stopped;
    if (items.length && (!stopped || steer)) { steer = false; setTimeout(next, 150); }
    else render();
  });
  document.addEventListener("chat:busy", render);
}
