// Draft of the open chat: what is not sent yet – the input field, its files and the waiting messages – lies on the
// server, per chat (or per "new chat" of an agent). App closed, chat changed: everything is there again.
// This module owns the change of chat: it saves the old chat's draft, clears the field and loads the new one.
import { api } from "../api.js";
import { ctx } from "../pages/chat.js";
import { draftFiles, forgetFiles, restoreFiles } from "./attach.js";
import { $, toast } from "./dom.js";
import { clearQueue, queueItems, restoreQueue } from "./queue.js";

const SAVE_MS = 600;
const url = (k) => `/chat-drafts/${encodeURIComponent(k)}`;
const blank = () => ({ text: "", files: [], queue: [] });
const EMPTY = JSON.stringify(blank());

let key = null;     // chat whose draft is in the field; null while the chat changes
let synced = EMPTY; // the state on the server
let pending = null; // not saved yet: { key, body }
let timer = null;
let saving = Promise.resolve();
let visit = 0;     // only the latest opening of a chat may fill the field

const keyOf = () => (ctx.id ? `c-${ctx.id}` : ctx.project ? `p-${ctx.project.id}` : ctx.agentId ? `n-${ctx.agentId}` : null);
const current = () => ({ text: $("input").value, files: draftFiles().map((f) => f.id), queue: queueItems() });

function save(k, body, keepalive = false) {
  if (keepalive) { // the page is closing: this still arrives when the page is gone
    fetch(`/api/v1${url(k)}`, { method: "PUT", keepalive: true, credentials: "same-origin", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
    return;
  }
  saving = saving.then(() => api.put(url(k), body)).catch(() => toast("Entwurf konnte nicht gespeichert werden"));
}

function flush(keepalive = false) {
  clearTimeout(timer);
  timer = null;
  if (!pending) return;
  const p = pending;
  pending = null;
  if (p.key === key) synced = JSON.stringify(p.body);
  save(p.key, p.body, keepalive);
}

function changed() {
  if (!key) return;
  clearTimeout(timer);
  timer = null;
  const body = current();
  if (JSON.stringify(body) === synced) { pending = null; return; }
  pending = { key, body };
  timer = setTimeout(flush, SAVE_MS);
}

function setInput(text) {
  const el = $("input");
  el.value = text;
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function leave() {
  visit++;
  flush();
  key = null;
  synced = EMPTY;
  clearQueue();
  forgetFiles();
  setInput("");
}

async function enter() {
  const k = keyOf();
  if (k === key) return;
  key = k;
  synced = EMPTY;
  if (!k) return;
  const mine = ++visit;
  await saving; // what was just saved must be in what we load
  let d;
  try { d = await api.get(url(k)); } catch { return; }
  if (mine !== visit) return;
  const typed = !!$("input").value.trim() || draftFiles().length > 0 || queueItems().length > 0; // the user was faster: that stays
  if (!$("input").value.trim() && d.text) setInput(d.text);
  restoreFiles(d.files);
  restoreQueue(d.queue);
  if (!typed) synced = JSON.stringify(current());
  changed();
}

// a new chat got its id: the draft moves along
function moved() {
  if (!key) return;
  const old = key;
  clearTimeout(timer);
  timer = null;
  pending = null;
  key = keyOf();
  synced = EMPTY;
  save(old, blank());
  changed();
}

export function initDraft() {
  $("input").addEventListener("input", changed);
  document.addEventListener("attach:change", changed);
  document.addEventListener("queue:change", changed);
  document.addEventListener("chat:switch", leave);
  document.addEventListener("chat:opened", enter);
  document.addEventListener("chat:id", moved);
  document.addEventListener("visibilitychange", () => { if (document.hidden) flush(true); });
  addEventListener("pagehide", () => flush(true));
}
