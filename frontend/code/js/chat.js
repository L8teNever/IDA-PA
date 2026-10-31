// Ein Thread: Verlauf anzeigen, neue Antworten live mitlesen (NDJSON von IDA), Freigaben („Darf ich?“) beantworten.
// Die Antwort läuft auf dem Server weiter, auch wenn die Seite gewechselt oder die App geschlossen wird.
import { api, followChat, streamChat } from "./api.js";
import { mainAgent } from "./store.js";
import { $, attempt, esc, fmtDateTime, fmtDur, md, toast } from "./ui.js";

// aktueller Thread: id (null = neu), project, mode (Rechte), busy (Antwort läuft)
export const ctx = { id: null, project: null, mode: "ask", busy: false, model: null, accountId: null };
let following = null; // AbortController der Antwort, der wir gerade folgen

const fire = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail }));

export function detach() {
  if (following) { following.abort(); following = null; }
  if (ctx.busy) { ctx.busy = false; fire("code:done", { ok: true, left: true }); }
}

const box = () => $("msgs");
function scroll(force = false) {
  const b = box();
  if (b && (force || b.scrollHeight - b.scrollTop - b.clientHeight < 180)) b.scrollTop = b.scrollHeight;
}

// neuer Thread in einem Projekt
export function mountNew(project, mode = "ask") {
  detach();
  Object.assign(ctx, { id: null, project, mode, busy: false, model: null, accountId: null });
  const a = mainAgent();
  box().innerHTML = `<div class="code-intro"><span class="ms">terminal</span><b>Neuer Thread in ${esc(project.name)}</b>
    <small>${esc(a?.name || "Ida")} arbeitet direkt im Projekt – liest Code, ändert Dateien, führt Befehle aus. Was soll gemacht werden?</small></div>`;
  fire("code:model", { model: null, account: null });
}

// vorhandener Thread
export async function mountThread(project, id) {
  detach();
  const c = await api.get(`/chats/${encodeURIComponent(id)}`);
  if (c.kind !== "code") throw new Error("Kein Code-Thread");
  Object.assign(ctx, { id: c.id, project, mode: c.permission_mode || "ask", busy: false, model: c.model, accountId: c.account_id });
  fire("code:model", { model: c.model, account: c.account_id });
  const b = box();
  b.innerHTML = `<div class="chat-day">${esc(c.title)} · ${fmtDateTime(c.created_at)}</div>`;
  for (const m of c.messages) {
    if (m.role === "user") addUser(m.text, m.meta?.files);
    else if (m.role === "assistant") renderBot(addBot(), m.parts, m.text, m.meta);
    else addSys(m.text);
  }
  scroll(true);
  if (c.running) follow((onEvent, signal) => followChat(c.id, onEvent, signal), c.running_since);
  return c;
}

function row(role) {
  const r = document.createElement("div");
  r.className = `row ${role}`;
  box().appendChild(r);
  return r;
}

const FILE_ICON = { pdf: "picture_as_pdf", text: "description", image: "image", file: "draft" };
function filesHtml(files) {
  if (!files?.length) return "";
  return `<div class="msg-files">${files.map((f) => f.kind === "image"
    ? `<a class="mf-pic" href="/api/v1/files/${esc(f.id)}" target="_blank" rel="noopener" title="${esc(f.name)}"><img src="/api/v1/files/${esc(f.id)}" alt="${esc(f.name)}" loading="lazy"></a>`
    : `<a class="mf-file" href="/api/v1/files/${esc(f.id)}" ${f.kind === "file" ? `download="${esc(f.name)}"` : `target="_blank" rel="noopener"`}><span class="ms">${FILE_ICON[f.kind] || "description"}</span><span>${esc(f.name)}</span></a>`).join("")}</div>`;
}

function addUser(text, files) {
  const r = row("me");
  r.innerHTML = filesHtml(files) + (text ? `<div class="msg me">${esc(text)}</div>` : "");
  r.querySelectorAll("img").forEach((i) => i.addEventListener("load", () => scroll(), { once: true }));
  scroll(true);
}

function addBot() {
  const r = row("bot");
  r.innerHTML = `<span class="bot-av"><span class="ms">terminal</span></span>`;
  const m = document.createElement("div");
  m.className = "msg bot";
  r.appendChild(m);
  return m;
}

export function addSys(text, icon = "info") {
  const d = document.createElement("div");
  d.className = "sysline";
  d.innerHTML = `<span class="ms">${icon}</span>${esc(text)}`;
  box()?.appendChild(d);
  scroll();
}

// ---- Werkzeuge und Freigaben ----
const rel = (f) => String(f).replace(/^.*[\\/]projects[\\/][0-9a-f]{12}[\\/]/, ""); // Pfade im Projektordner relativ zeigen
function toolTitle(p) {
  const i = p.input || {};
  const f = i.file_path || i.path || i.notebook_path;
  if (i.command) return `<code>${esc(String(i.command).split("\n")[0].slice(0, 90))}</code>`;
  if (f) return `<code>${esc(rel(f))}</code>`;
  if (i.pattern) return `<code>${esc(i.pattern)}</code>`;
  return "";
}
function toolHtml(p) {
  const status = p.out === undefined ? "progress_activity" : p.error ? "error" : "check_circle";
  return `<details class="toolcall ${p.error ? "err" : ""}"><summary><span class="ms ${p.out === undefined ? "spin" : ""}">${status}</span>
    <b>${esc(p.server ? `${p.server} › ` : "")}${esc(p.name)}</b>${toolTitle(p)}</summary>
    <pre>${esc(JSON.stringify(p.input ?? {}, null, 2))}</pre>${p.out !== undefined ? `<pre class="out">${esc(p.out)}</pre>` : ""}</details>`;
}

function approvalBody(a) {
  const i = a.input || {};
  if (i.command) return `<pre class="ap-cmd">${esc(i.command)}</pre>${i.description ? `<small>${esc(i.description)}</small>` : ""}`;
  const raw = i.file_path || i.path || i.notebook_path;
  const file = raw && rel(raw);
  if (file && (i.old_string !== undefined || i.new_string !== undefined)) {
    return `<div class="ap-file">${esc(file)}</div><pre class="diff small">${(i.old_string || "").split("\n").map((l) => `<i class="del">-${esc(l)}</i>`).join("")}${(i.new_string || "").split("\n").map((l) => `<i class="add">+${esc(l)}</i>`).join("")}</pre>`;
  }
  if (file && i.content !== undefined) return `<div class="ap-file">${esc(file)}</div><pre class="diff small">${String(i.content).split("\n").slice(0, 60).map((l) => `<i class="add">+${esc(l)}</i>`).join("")}</pre>`;
  return `<pre class="ap-cmd">${esc(JSON.stringify(i, null, 2))}</pre>`;
}
const DECIDED = { allow: ["check_circle", "Erlaubt"], always: ["done_all", "Immer erlaubt"], deny: ["block", "Abgelehnt"], stopped: ["stop_circle", "Abgebrochen"] };
function approvalHtml(a) {
  const d = a.decision && (DECIDED[a.decision] || DECIDED.stopped);
  return `<div class="approval ${d ? "decided" : ""}">
    <div class="ap-head"><span class="ms">${d ? d[0] : "front_hand"}</span><b>${d ? d[1] : "Darf ich?"}</b><span class="ap-tool">${esc(a.tool)}</span></div>
    ${d ? "" : `${approvalBody(a)}<div class="btn-row">
      <button type="button" class="btn-primary" data-approve="allow" data-id="${esc(a.id)}"><span class="ms">check</span>Erlauben</button>
      <button type="button" class="btn-tonal" data-approve="always" data-id="${esc(a.id)}" title="${esc(a.tool)} in diesem Thread nicht mehr fragen"><span class="ms">done_all</span>Immer</button>
      <button type="button" class="btn-tonal" data-approve="deny" data-id="${esc(a.id)}"><span class="ms">close</span>Ablehnen</button></div>`}
  </div>`;
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-approve]");
  if (!b) return;
  b.closest(".btn-row")?.querySelectorAll("button").forEach((x) => (x.disabled = true));
  const ok = await attempt(() => api.post(`/code/approvals/${encodeURIComponent(b.dataset.id)}`, { decision: b.dataset.approve }));
  if (ok === undefined) b.closest(".btn-row")?.querySelectorAll("button").forEach((x) => (x.disabled = false));
});

function renderBot(el, parts, text, meta) {
  const html = (parts || []).map((p) => (p.t === "text" ? `<div class="md">${md(p.text)}</div>` : p.t === "approval" ? approvalHtml(p) : toolHtml(p))).join("");
  // aufgeklappte Werkzeuge bleiben beim Neuzeichnen offen
  const open = new Set([...el.querySelectorAll("details")].flatMap((d, i) => (d.open ? [i] : [])));
  el.innerHTML = html || (text ? `<div class="md">${md(text)}</div>` : "");
  if (open.size) el.querySelectorAll("details").forEach((d, i) => { if (open.has(i)) d.open = true; });
  if (meta?.stopped) el.insertAdjacentHTML("beforeend", `<div class="msg-note"><span class="ms">stop_circle</span>Gestoppt</div>`);
  if (meta?.error) el.insertAdjacentHTML("beforeend", `<div class="msg-err"><span class="ms">error</span>${esc(meta.error)}</div>`);
  if (!el.innerHTML) el.innerHTML = `<div class="typing"><i></i><i></i><i></i></div>`;
  if (meta?.ms != null) el.insertAdjacentHTML("beforeend", `<div class="msg-note" title="So lange hat die Antwort gedauert"><span class="ms">timer</span>${fmtDur(meta.ms)}</div>`);
}

// Stopp-Knopf: beendet die laufende Antwort (das Geschriebene bleibt)
export async function stopAnswer() {
  if (!ctx.busy || !ctx.id) return;
  await attempt(() => api.post(`/chats/${encodeURIComponent(ctx.id)}/stop`));
}

async function follow(start, since = null) {
  const el = addBot();
  const t0 = since ? new Date(since).getTime() : Date.now();
  const clock = document.createElement("div");
  clock.className = "msg-note live";
  el.after(clock);
  const tick = () => { clock.innerHTML = `<span class="ms spin">progress_activity</span>arbeitet seit ${fmtDur(Date.now() - t0)}`; };
  tick();
  const timer = setInterval(tick, 1000);
  const parts = [], tools = {}, meta = {};
  let ok = false;
  const ctrl = new AbortController();
  following = ctrl;
  ctx.busy = true;
  fire("code:busy");
  renderBot(el, parts);
  const onEvent = (ev) => {
    if (ev.chat) {
      if (!ctx.id) { ctx.id = ev.chat.id; fire("code:thread", { id: ev.chat.id, project: ctx.project.id }); }
      return;
    }
    if (ev.text !== undefined) {
      const last = parts[parts.length - 1];
      if (last?.t === "text") last.text += ev.text; else parts.push({ t: "text", text: ev.text });
    } else if (ev.tool) {
      const p = { t: "tool", ...ev.tool };
      tools[p.id] = p;
      parts.push(p);
    } else if (ev.tool_result) {
      const p = tools[ev.tool_result.id];
      if (p) { p.out = ev.tool_result.content; p.error = ev.tool_result.error; }
      fire("code:files"); // die KI hat evtl. Dateien geändert
    } else if (ev.approval) {
      parts.push({ t: "approval", ...ev.approval });
      fire("code:waiting");
    } else if (ev.approval_done) {
      const p = parts.find((x) => x.t === "approval" && x.id === ev.approval_done.id);
      if (p) p.decision = ev.approval_done.decision;
    } else if (ev.sys) {
      addSys(ev.sys, "swap_horiz");
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
    if (ctrl.signal.aborted) return; // Thread verlassen: die Antwort läuft auf dem Server weiter
    if (ctx.id && (e.status === undefined || e.status === 0 || (e.status === 404 && /läuft keine/.test(e.message)))) {
      // Verbindung weg (Handy schläft, Netz): die Antwort läuft weiter – Thread neu laden und wieder mitlesen
      el.closest(".row")?.remove();
      following = null;
      const id = ctx.id, project = ctx.project;
      setTimeout(() => { if (!following && ctx.id === id) mountThread(project, id).catch(() => {}); }, 1500);
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
      fire("code:done", { ok, stopped: !!meta.stopped });
    }
  }
}

export async function send(text, { model, accountId, effort, files = [] } = {}) {
  if (ctx.busy) return toast("Die Antwort läuft noch …");
  const agent = mainAgent();
  if (!agent) return toast("Kein Agent in IDA gefunden");
  box().querySelector(".code-intro")?.remove();
  if (!box().querySelector(".chat-day")) box().insertAdjacentHTML("afterbegin", `<div class="chat-day">${new Date().toLocaleString("de-DE", { weekday: "long", hour: "2-digit", minute: "2-digit" })}</div>`);
  addUser(text, files);
  const body = { chat_id: ctx.id, agent_id: agent.id, text, model, account_id: accountId, effort, files: files.map((f) => f.id),
    project_id: ctx.id ? null : ctx.project.id, permission_mode: ctx.mode };
  await follow((onEvent, signal) => streamChat(body, onEvent, signal));
}
