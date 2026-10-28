// Chat view: shows a conversation and streams new answers. Used by "/", "/a/:id" (new chat) and "/chat/:id",
// and for the threads of the code area ("/code/:project/:thread"): same view with the code bar on top.
import { api, followChat, streamChat } from "../api.js";
import { setUrl } from "../router.js";
import { agentById, agentOf, mainAgent, state } from "../store.js";
import { approvalHtml, bindCodeBar, codeBar, refreshBar } from "../ui/codebar.js";
import { $, esc, fmtDateTime, md, toast } from "../ui/dom.js";
import { flashMood, moodForTool, setLive } from "../ui/mascot.js";
import { setAgent, setView } from "../ui/shell.js";

// current conversation: { id|null, agentId, model, accountId }; code threads also: project (from /code/projects/:id), mode (rights)
export const ctx = { id: null, agentId: null, model: null, accountId: null, busy: false, project: null, mode: "ask" };

const stageOf = (a) => (a.home === "timeline" ? "home" : "agent");

// the answer this page is following right now (the answer itself runs on the server and goes on without us)
let following = null; // AbortController

function detach() {
  if (following) { following.abort(); following = null; }
}

// the composer still shows "answering" from a stream we left: back to normal
function idle() {
  if (document.body.classList.contains("answering")) document.dispatchEvent(new CustomEvent("chat:done", { detail: { stopped: false, ok: true } }));
}

export function newChat(agent) {
  detach();
  Object.assign(ctx, { id: null, agentId: agent.id, busy: false, project: null });
  document.dispatchEvent(new CustomEvent("chat:switch"));
  idle();
  $("messages").innerHTML = "";
}

// the bar above a code thread: project, branch, rights, changes
function showCodeBar() {
  $("messages").insertAdjacentHTML("afterbegin", codeBar(ctx.project, ctx.mode));
  bindBar();
}
function bindBar() {
  bindCodeBar(async (mode) => {
    if (ctx.id) {
      try { await api.patch(`/code/threads/${ctx.id}`, { permission_mode: mode }); } catch (e) { return toast(e.message); }
    }
    ctx.mode = mode;
    refreshBar(ctx.project.id, mode);
  });
}
document.addEventListener("code:bar", () => { if (ctx.project) bindBar(); });
document.addEventListener("code:changed", () => { if (ctx.project) refreshBar(ctx.project.id, ctx.mode); });

// "/code/:project/neu": a new thread of a code project (always the main agent)
export function newCodeThread(project, mode = "ask") {
  detach();
  const a = mainAgent();
  setAgent(a);
  document.dispatchEvent(new CustomEvent("chat:switch"));
  Object.assign(ctx, { id: null, agentId: a.id, busy: false, project, mode });
  idle();
  setView("chat", { agentStage: "code" });
  $("messages").innerHTML = `<div class="code-intro"><span class="ms">terminal</span><b>Neuer Thread in ${esc(project.name)}</b>
    <small>${esc(a.name)} arbeitet direkt im Projekt – liest Code, ändert Dateien, führt Befehle aus. Was soll gemacht werden?</small></div>`;
  showCodeBar();
  $("input").placeholder = `Was soll ${a.name} in ${project.name} machen?`;
  $("input").focus();
}

export async function openChat(id) {
  detach();
  const c = await api.get(`/chats/${encodeURIComponent(id)}`);
  const a = agentOf(c.agent_id);
  const project = c.kind === "code" ? await api.get(`/code/projects/${c.project_id}`) : null;
  setAgent(a);
  if (c.id !== ctx.id) document.dispatchEvent(new CustomEvent("chat:switch")); // waiting messages belong to the old chat
  Object.assign(ctx, { id: c.id, agentId: a.id, model: c.model, accountId: c.account_id, busy: false, project, mode: c.permission_mode || "ask" });
  document.dispatchEvent(new CustomEvent("chat:model", { detail: { model: c.model, account: c.account_id } }));
  setView("chat", { agentStage: project ? "code" : stageOf(a) });
  const box = $("messages");
  box.innerHTML = `<div class="chat-day">${esc(c.title)} · ${fmtDateTime(c.created_at)}</div>`;
  if (project) { showCodeBar(); $("input").placeholder = `Was soll ${a.name} in ${project.name} machen?`; }
  for (const m of c.messages) {
    if (m.role === "user") addUser(m.text, m.meta?.skill, m.meta?.files);
    else if (m.role === "assistant") renderBot(addBot(), m.parts, m.text, m.meta);
    else addSys(m.text);
  }
  box.scrollTop = box.scrollHeight;
  // the answer is still being written (page was switched or the app closed meanwhile): follow it again
  if (c.running) follow((onEvent, signal) => followChat(c.id, onEvent, signal), c.running_since);
  else idle();
}

function row(role) {
  const r = document.createElement("div");
  r.className = "row " + role;
  if (role === "bot") r.innerHTML = `<span class="mini"></span>`;
  $("messages").appendChild(r);
  return r;
}

const FILE_ICON = { pdf: "picture_as_pdf", text: "description", image: "image" };
function filesHtml(files) {
  if (!files?.length) return "";
  return `<div class="msg-files">${files.map((f) => f.kind === "image"
    ? `<a class="mf-pic" href="/api/v1/files/${f.id}" target="_blank" rel="noopener" title="${esc(f.name)}"><img src="/api/v1/files/${f.id}" alt="${esc(f.name)}" loading="lazy"></a>`
    : `<a class="mf-file" href="/api/v1/files/${f.id}" target="_blank" rel="noopener"><span class="ms">${FILE_ICON[f.kind] || "description"}</span><span>${esc(f.name)}</span></a>`).join("")}</div>`;
}

function addUser(text, skill, files) {
  const r = row("me");
  r.innerHTML = filesHtml(files) + (text || skill ? `<div class="msg me">${skill ? `<span class="skill-tag"><span class="ms">auto_fix_high</span>/${esc(skill)}</span> ` : ""}${esc(text)}</div>` : "");
  r.querySelectorAll("img").forEach((i) => i.addEventListener("load", scroll, { once: true }));
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

// "12 s" / "1 Min 05 s"
export function fmtDur(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 1 ? "<1 s" : s < 60 ? `${s} s` : `${Math.floor(s / 60)} Min ${String(s % 60).padStart(2, "0")} s`;
}

function renderBot(el, parts, text, meta) {
  const html = (parts || []).map((p) => (p.t === "text" ? `<div class="md-text">${md(p.text)}</div>` : p.t === "approval" ? approvalHtml(p) : toolHtml(p))).join("");
  el.innerHTML = html || (text ? `<div class="md-text">${md(text)}</div>` : "");
  if (meta?.stopped) el.insertAdjacentHTML("beforeend", `<div class="msg-stopped"><span class="ms">stop_circle</span>Gestoppt</div>`);
  if (meta?.error) el.insertAdjacentHTML("beforeend", `<div class="msg-err"><span class="ms">error</span>${esc(meta.error)}</div>`);
  if (!el.innerHTML) el.innerHTML = `<div class="typing"><i></i><i></i><i></i></div>`;
  // how long the answer took
  if (meta?.ms != null) el.insertAdjacentHTML("beforeend", `<div class="msg-meta" title="So lange hat die Antwort gedauert"><span class="ms">timer</span>${fmtDur(meta.ms)}</div>`);
}

function scroll() {
  const b = $("messages");
  if (b.scrollHeight - b.scrollTop - b.clientHeight < 160) b.scrollTop = b.scrollHeight;
}

// stop button: ends the answer that is running (the text so far stays)
export async function stopAnswer() {
  if (!ctx.busy || !ctx.id) return;
  try { await api.post(`/chats/${ctx.id}/stop`); } catch (e) { toast(e.message); }
}

// Shows an answer while it streams in. start(onEvent, signal) opens the stream (new message or following a running one).
async function follow(start, since = null) {
  const el = addBot();
  // runs for … (counts on while the answer is written; from the server's start time when we come back later)
  const t0 = since ? new Date(since).getTime() : Date.now();
  const clock = document.createElement("div");
  clock.className = "msg-meta live";
  el.closest(".row").after(clock);
  const tick = () => { clock.innerHTML = `<span class="ms">timer</span>läuft seit ${fmtDur(Date.now() - t0)}`; };
  tick();
  const timer = setInterval(tick, 1000);
  const parts = [];
  const tools = {};
  const meta = {};
  let ok = false;
  const ctrl = new AbortController();
  following = ctrl;
  ctx.busy = true;
  document.dispatchEvent(new CustomEvent("chat:busy"));
  renderBot(el, parts);
  setLive("thinking");
  const onEvent = (ev) => {
    if (ev.chat) {
      if (!ctx.id) {
        ctx.id = ev.chat.id;
        if (ctx.project) setUrl(`/code/${ctx.project.id}/${ev.chat.id}`); // replaces /code/:project/neu
        else setUrl(`/chat/${ev.chat.id}`, { push: !location.pathname.startsWith("/chat/") });
      }
      return;
    }
    if (ev.text !== undefined) {
      const last = parts[parts.length - 1];
      if (last?.t === "text") last.text += ev.text; else parts.push({ t: "text", text: ev.text });
    } else if (ev.tool) {
      const p = { t: "tool", ...ev.tool };
      tools[p.id] = p; parts.push(p);
      setLive(moodForTool(p.name));
    } else if (ev.tool_result) {
      const p = tools[ev.tool_result.id];
      if (p) { p.out = ev.tool_result.content; p.error = ev.tool_result.error; }
    } else if (ev.approval) {
      parts.push({ t: "approval", ...ev.approval });
      setLive(null);
    } else if (ev.approval_done) {
      const p = parts.find((x) => x.t === "approval" && x.id === ev.approval_done.id);
      if (p) p.decision = ev.approval_done.decision;
      setLive("thinking");
    } else if (ev.sys) {
      addSys(ev.sys);
    } else if (ev.done) {
      ok = ev.done.ok;
      if (ev.done.ms != null) meta.ms = ev.done.ms;
      if (ev.done.stopped) meta.stopped = true;
      else if (!ok) meta.error = ev.done.error;
    }
    renderBot(el, parts, "", meta);
    scroll();
  };
  try {
    await start(onEvent, ctrl.signal);
  } catch (e) {
    if (ctrl.signal.aborted) return; // left the chat: the answer goes on on the server, nothing to do here
    if (ctx.id && (e.status === undefined || (e.status === 404 && /läuft keine/.test(e.message)))) {
      // connection lost (phone asleep, network): the answer goes on – show the chat again and follow it
      el.closest(".row")?.remove();
      following = null;
      setTimeout(() => { if (!following && ctx.id) openChat(ctx.id).catch(() => {}); }, 1500);
      return;
    }
    meta.error = e.message;
    renderBot(el, parts, "", meta);
  } finally {
    clearInterval(timer);
    clock.remove();
    if (following === ctrl) {
      following = null;
      ctx.busy = false;
      setLive(null);
      if (!meta.stopped && !ctrl.signal.aborted) flashMood(ok ? "happy" : "sad");
      document.dispatchEvent(new CustomEvent("chat:done", { detail: { stopped: !!meta.stopped, ok } }));
      if (ctx.project) refreshBar(ctx.project.id, ctx.mode); // the agent may have changed files
    }
  }
}

export async function send(text, { skill = null, model, accountId, effort, files = [] } = {}) {
  if (ctx.busy) return toast("Die Antwort läuft noch …");
  const agent = agentById(ctx.agentId) || state.agent;
  if (!document.body.classList.contains("chatting")) {
    setView("chat", { agentStage: ctx.project ? "code" : stageOf(agent) });
    $("messages").innerHTML = `<div class="chat-day">${new Date().toLocaleString("de-DE", { weekday: "long", hour: "2-digit", minute: "2-digit" })}</div>`;
    if (ctx.project) showCodeBar();
  }
  $("messages").querySelector(".code-intro")?.remove();
  addUser(text, skill, files);
  const body = { chat_id: ctx.id, agent_id: agent.id, text, skill, model, account_id: accountId, effort, files: files.map((f) => f.id) };
  if (ctx.project) Object.assign(body, { project_id: ctx.id ? null : ctx.project.id, permission_mode: ctx.mode });
  await follow((onEvent, signal) => streamChat(body, onEvent, signal));
}
