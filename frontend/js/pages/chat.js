// Chat view: shows a conversation and streams new answers. Used by "/", "/a/:id" (new chat) and "/chat/:id".
import { api, streamChat } from "../api.js";
import { setUrl } from "../router.js";
import { agentById, agentOf, state } from "../store.js";
import { $, esc, fmtDateTime, md, toast } from "../ui/dom.js";
import { flashMood, moodForTool, setLive } from "../ui/mascot.js";
import { setAgent, setView } from "../ui/shell.js";

// current conversation: { id|null, agentId, model, accountId }
export const ctx = { id: null, agentId: null, model: null, accountId: null, busy: false };

const stageOf = (a) => (a.home === "timeline" ? "home" : "agent");

export function newChat(agent) {
  Object.assign(ctx, { id: null, agentId: agent.id, busy: false });
  $("messages").innerHTML = "";
}

export async function openChat(id) {
  const c = await api.get(`/chats/${encodeURIComponent(id)}`);
  const a = agentOf(c.agent_id);
  setAgent(a);
  Object.assign(ctx, { id: c.id, agentId: a.id, model: c.model, accountId: c.account_id, busy: false });
  document.dispatchEvent(new CustomEvent("chat:model", { detail: { model: c.model, account: c.account_id } }));
  setView("chat", { agentStage: stageOf(a) });
  const box = $("messages");
  box.innerHTML = `<div class="chat-day">${esc(c.title)} · ${fmtDateTime(c.created_at)}</div>`;
  for (const m of c.messages) {
    if (m.role === "user") addUser(m.text, m.meta?.skill);
    else if (m.role === "assistant") renderBot(addBot(), m.parts, m.text, m.meta);
    else addSys(m.text);
  }
  box.scrollTop = box.scrollHeight;
}

function row(role) {
  const r = document.createElement("div");
  r.className = "row " + role;
  if (role === "bot") r.innerHTML = `<span class="mini"></span>`;
  $("messages").appendChild(r);
  return r;
}

function addUser(text, skill) {
  const r = row("me");
  r.innerHTML = `<div class="msg me">${skill ? `<span class="skill-tag"><span class="ms">auto_fix_high</span>/${esc(skill)}</span> ` : ""}${esc(text)}</div>`;
  scroll();
}

function addBot() {
  const r = row("bot");
  const m = document.createElement("div");
  m.className = "msg bot md";
  r.appendChild(m);
  return m;
}

export function addSys(text, icon = "swap_horiz") {
  const d = document.createElement("div");
  d.className = "sysline";
  d.innerHTML = `<span class="ms">${icon}</span>${esc(text)}`;
  $("messages").appendChild(d);
  scroll();
}

function toolHtml(p) {
  const status = p.out === undefined ? "progress_activity" : p.error ? "error" : "check_circle";
  return `<details class="toolcall ${p.error ? "err" : ""}"><summary><span class="ms ${p.out === undefined ? "spin" : ""}">${status}</span>
    ${esc(p.server ? p.server + " › " : "")}<b>${esc(p.name)}</b></summary>
    <pre>${esc(JSON.stringify(p.input ?? {}, null, 2))}</pre>${p.out !== undefined ? `<pre>${esc(p.out)}</pre>` : ""}</details>`;
}

function renderBot(el, parts, text, meta) {
  const html = (parts || []).map((p) => (p.t === "text" ? `<div class="md-text">${md(p.text)}</div>` : toolHtml(p))).join("");
  el.innerHTML = html || (text ? `<div class="md-text">${md(text)}</div>` : "");
  if (meta?.error) el.insertAdjacentHTML("beforeend", `<div class="msg-err"><span class="ms">error</span>${esc(meta.error)}</div>`);
  if (!el.innerHTML) el.innerHTML = `<div class="typing"><i></i><i></i><i></i></div>`;
}

function scroll() {
  const b = $("messages");
  if (b.scrollHeight - b.scrollTop - b.clientHeight < 160) b.scrollTop = b.scrollHeight;
}

export async function send(text, { skill = null, model, accountId, effort } = {}) {
  if (ctx.busy) return toast("Die Antwort läuft noch …");
  const agent = agentById(ctx.agentId) || state.agent;
  ctx.busy = true;
  if (!document.body.classList.contains("chatting")) {
    setView("chat", { agentStage: stageOf(agent) });
    $("messages").innerHTML = `<div class="chat-day">${new Date().toLocaleString("de-DE", { weekday: "long", hour: "2-digit", minute: "2-digit" })}</div>`;
  }
  addUser(text, skill);
  const el = addBot();
  const parts = [];
  const tools = {};
  renderBot(el, parts);
  setLive("thinking");
  let ok = false, meta = {};
  try {
    await streamChat({ chat_id: ctx.id, agent_id: agent.id, text, skill, model, account_id: accountId, effort }, (ev) => {
      if (ev.chat) {
        if (!ctx.id) { ctx.id = ev.chat.id; setUrl(`/chat/${ev.chat.id}`, { push: !location.pathname.startsWith("/chat/") }); }
      } else if (ev.text !== undefined) {
        const last = parts[parts.length - 1];
        if (last?.t === "text") last.text += ev.text; else parts.push({ t: "text", text: ev.text });
      } else if (ev.tool) {
        const p = { t: "tool", ...ev.tool };
        tools[p.id] = p; parts.push(p);
        setLive(moodForTool(p.name));
      } else if (ev.tool_result) {
        const p = tools[ev.tool_result.id];
        if (p) { p.out = ev.tool_result.content; p.error = ev.tool_result.error; }
      } else if (ev.sys) {
        addSys(ev.sys);
      } else if (ev.done) {
        ok = ev.done.ok;
        if (!ok) meta.error = ev.done.error;
      }
      renderBot(el, parts, "", meta);
      scroll();
    });
  } catch (e) {
    meta.error = e.message;
    renderBot(el, parts, "", meta);
  } finally {
    ctx.busy = false;
    setLive(null);
    flashMood(ok ? "happy" : "sad");
    document.dispatchEvent(new CustomEvent("chat:done"));
  }
}
