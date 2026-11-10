// Warteschlange (wie im IDA-Chat): während die KI arbeitet, warten neue Nachrichten hier und gehen nacheinander raus.
// Sie liegt auf dem Server: Thread gewechselt, Seite zu oder anderes Gerät – nichts geht verloren, und der Server
// schickt die nächste selbst ab, sobald die Antwort fertig ist (auch wenn gerade niemand zuschaut).
// Angehängte Dateien bleiben dabei, auch beim Bearbeiten. Jede kann sofort gesendet werden („Jetzt senden“ stoppt die laufende Antwort und schickt sie als Nächstes),
// zurück ins Eingabefeld zum Bearbeiten geholt oder gelöscht werden. Ein bewusster Stopp pausiert die Schlange.
import { api } from "./api.js";
import { ctx, mountThread } from "./chat.js";
import { restoreFiles } from "./composer.js";
import { appendDraft } from "./draft.js";
import { $, attempt, esc } from "./ui.js";

let items = []; // vom Server: { id, text, model, account_id, effort, files, attachments }
let local = []; // neuer Thread, der noch keine ID hat: wartet hier, bis er eine bekommt
let error = null; // die nächste konnte nicht starten (Grund vom Server)
let steer = false; // „Jetzt senden“ gedrückt
let loadedFor = null;

const url = (id) => `/code/queue/${encodeURIComponent(id)}`;
const toServer = (text, opts = {}) => ({ text, model: opts.model || null, account_id: opts.accountId || null, effort: opts.effort || null, skill: opts.skill || null,
  files: (opts.files || []).map((f) => f.id) });

// zurück ins Eingabefeld, samt Dateien
function giveBack(text, files) {
  const input = $("input");
  if (input && text) {
    input.value = text + (input.value.trim() ? "\n" + input.value : "");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  restoreFiles(files);
  input?.focus();
}

function apply(q) {
  if (!q) return;
  items = q.items || [];
  error = q.error || null;
  render();
}

async function load(id = ctx.id) {
  if (!id) { items = []; error = null; return render(); }
  let q;
  try { q = await api.get(url(id)); } catch { return null; }
  if (ctx.id !== id) return null;
  loadedFor = id;
  apply(q);
  return q;
}

export async function enqueue(text, opts) {
  if (!ctx.id) { local.push({ id: `l${local.length}`, ...toServer(text, opts), attachments: opts.files || [] }); return render(); }
  const id = ctx.id;
  const q = await attempt(() => api.post(url(id), toServer(text, opts)));
  if (!q) return giveBack(text, opts.files); // nicht angekommen: nichts geht verloren
  if (ctx.id !== id) return;
  apply(q);
  if (q?.running && !ctx.busy) remount(); // lief schon nichts mehr: der Server hat sie gleich gestartet
}

const remount = () => { if (ctx.id && ctx.project) mountThread(ctx.project, ctx.id).catch(() => {}); };

async function sendNow(itemId) {
  if (!ctx.id) return;
  steer = true;
  render();
  const q = await attempt(() => api.post(`${url(ctx.id)}/${encodeURIComponent(itemId)}/now`));
  if (!q) { steer = false; return render(); }
  apply(q);
  if (!ctx.busy) { steer = false; remount(); } // lief nichts: sie ist schon unterwegs
}

function all() { return [...items, ...local]; }

const thumbs = (list) => list?.length ? `<span class="q-files">${list.map((f) => f.kind === "image"
  ? `<img src="/api/v1/files/${esc(f.id)}" alt="" title="${esc(f.name)}">` : `<span class="ms" title="${esc(f.name)}">attach_file</span>`).join("")}</span>` : "";

function render() {
  const box = $("queue");
  if (!box) return;
  const list = all();
  box.hidden = !list.length;
  const status = steer ? "wird gleich gesendet …" : error ? `pausiert – ${error}` : ctx.busy ? "geht raus, sobald die Antwort fertig ist" : "pausiert";
  box.innerHTML = list.length ? `<div class="q-head"><span class="ms">schedule_send</span>${list.length === 1 ? "1 Nachricht wartet" : `${list.length} Nachrichten warten`}
      <small>${esc(status)}</small></div>
    ${list.map((x, n) => `<div class="q-item" data-id="${esc(x.id)}"><span class="q-n">${n + 1}</span>
      ${thumbs(x.attachments)}<span class="q-text">${esc(x.text)}</span>
      <button type="button" class="q-btn now" data-act="now" title="Antwort unterbrechen und diese Nachricht jetzt senden"><span class="ms">bolt</span><span class="q-lbl">Jetzt senden</span></button>
      <button type="button" class="q-btn" data-act="edit" title="Zurück ins Eingabefeld zum Bearbeiten" aria-label="Bearbeiten"><span class="ms">edit</span></button>
      <button type="button" class="q-btn" data-act="del" title="Aus der Warteschlange löschen" aria-label="Löschen"><span class="ms">close</span></button></div>`).join("")}` : "";
}

// nach dem Zeichnen des Eingabefelds aufrufen (das Feld wird pro Thread neu gezeichnet)
export function bindQueue() {
  $("queue").onclick = async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const itemId = b.closest("[data-id]").dataset.id;
    const it = all().find((x) => x.id === itemId);
    if (!it) return;
    if (b.dataset.act === "now") return local.includes(it) ? undefined : sendNow(itemId);
    if (local.includes(it)) local = local.filter((x) => x !== it);
    else {
      const q = await attempt(() => api.del(`${url(ctx.id)}/${encodeURIComponent(itemId)}`));
      if (!q) return;
      apply(q);
    }
    if (b.dataset.act === "edit") giveBack(it.text, it.attachments);
    else (it.attachments || []).forEach((f) => api.del(`/uploads/${f.id}`).catch(() => {}));
    render();
  };
  render();
}

// eine Antwort ist fertig: der Server schickt die nächste selbst – hier nur nachsehen und der neuen Antwort folgen
document.addEventListener("code:done", async (e) => {
  if (e.detail?.left) return; // Thread verlassen / neu geladen: kein echtes Ende
  steer = false;
  if (!ctx.id) return render();
  const id = ctx.id;
  const q = await load(id);
  if (q?.running && ctx.id === id && !ctx.busy) remount();
});
document.addEventListener("code:busy", render);

// Thread verlassen: der Server behält seine Schlange; nur was noch keinen Thread hatte, kommt in den Entwurf
export function stashQueue(keepalive = false) {
  if (local.length) {
    const text = local.map((x) => x.text).filter(Boolean).join("\n\n");
    const files = local.flatMap((x) => x.attachments || []);
    local = [];
    appendDraft(ctx.project ? `p-${ctx.project.id}` : null, text, files, keepalive);
  }
  items = []; error = null; steer = false; loadedFor = null;
  render();
}
addEventListener("pagehide", () => stashQueue(true));
// neuer Thread hat seine ID bekommen: was lokal wartet, kommt jetzt auf den Server
document.addEventListener("code:thread", async () => {
  const wait = local;
  local = [];
  for (const x of wait) {
    const q = await attempt(() => api.post(url(ctx.id), { text: x.text, model: x.model, account_id: x.account_id, effort: x.effort, files: x.files }));
    if (q) apply(q);
    else giveBack(x.text, x.attachments);
  }
  render();
});
document.addEventListener("code:mounted", () => { if (loadedFor !== ctx.id || !ctx.busy) load(); else render(); });
