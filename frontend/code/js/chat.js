// Ein Thread: Verlauf anzeigen, neue Antworten live mitlesen (NDJSON von IDA), Freigaben („Darf ich?“) beantworten.
// Die Antwort läuft auf dem Server weiter, auch wenn die Seite gewechselt oder die App geschlossen wird.
import { api, followChat, streamChat } from "./api.js";
import { loadTree, mainAgent, modelInfo, state } from "./store.js";
import { navigate, urlOf } from "./router.js";
import { userHtml } from "./quote.js";
import { $, attempt, esc, fmtDateTime, fmtDur, md, toast } from "./ui.js";

// aktueller Thread: id (null = neu), project, mode (Rechte), busy (Antwort läuft)
export const ctx = { id: null, project: null, mode: "ask", busy: false, model: null, accountId: null, lastId: null };
let following = null; // AbortController der Antwort, der wir gerade folgen

const fire = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail }));

// Schlüssel für Entwurf und Warteschlange: Thread („t-…“) oder neuer Thread im Projekt („p-…“)
export const threadKey = () => (ctx.id ? `t-${ctx.id}` : ctx.project ? `p-${ctx.project.id}` : null);

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
  Object.assign(ctx, { id: null, project, mode, busy: false, model: null, accountId: null, lastId: null });
  const a = mainAgent();
  box().innerHTML = `<div class="code-intro"><span class="ms">terminal</span><b>Neuer Thread in ${esc(project.name)}</b>
    <small>${esc(a?.name || "Ida")} arbeitet direkt im Projekt – liest Code, ändert Dateien, führt Befehle aus. Was soll gemacht werden?</small></div>`;
  fire("code:model", { model: null, account: null });
  fire("code:mounted");
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
    else if (m.role === "assistant") renderBot(addBot(), m.parts, m.text, { ...m.meta, id: m.id });
    else addSys(m.text);
  }
  scroll(true);
  ctx.lastId = c.messages.at(-1)?.id || null;
  if (c.running) follow((onEvent, signal) => followChat(c.id, onEvent, signal), c.running_since);
  fire("code:mounted");
  return c;
}

// Zurück in der App (Handy entsperrt, Tab wieder vorne, Netz wieder da): die alte Verbindung hängt oft still,
// ohne Fehler. Läuft etwas oder hat sich etwas getan, den Thread neu laden – er liest die laufende Antwort von vorn mit.
let hiddenAt = 0, resyncing = false;
async function resync() {
  const id = ctx.id, project = ctx.project;
  if (!id || !project || resyncing) return;
  resyncing = true;
  try {
    const c = await api.get(`/chats/${encodeURIComponent(id)}`).catch(() => null);
    if (!c || ctx.id !== id) return;
    if (ctx.busy || c.running || (c.messages.at(-1)?.id || null) !== ctx.lastId) await mountThread(project, id);
  } catch { /* nächstes Mal */ } finally { resyncing = false; }
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) hiddenAt = Date.now();
  else if (Date.now() - hiddenAt > 1500) resync();
});
addEventListener("online", resync);
addEventListener("pageshow", (e) => { if (e.persisted) resync(); }); // aus dem Zurück-Cache des Browsers

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
  r.innerHTML = filesHtml(files) + (text ? `<div class="msg me">${userHtml(text)}</div>` : "");
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

// Was die KI mit chat_zeigen zeigt: Bild, Live-Vorschau (HTML/SVG, isoliert) oder Datei. Die Vorschau lädt erst, wenn die Antwort fertig ist,
// sonst würde sie bei jedem neuen Textstück neu laden.
function shownFile(p) {
  if (!/(^|_)chat_zeigen$/.test(p.name || "") || p.error || p.out === undefined) return null;
  try { return JSON.parse(p.out).datei || null; } catch { return null; }
}
function shownHtml(f, ready) {
  const url = `/api/v1/files/${esc(f.id)}`, open = f.vorschau ? `${url}/preview` : url;
  const body = f.kind === "image" ? `<a href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="${esc(f.name)}" loading="lazy"></a>`
    : f.vorschau ? (ready ? `<iframe src="${url}/preview" sandbox="allow-scripts allow-forms allow-popups" loading="lazy" title="${esc(f.name)}"></iframe>` : `<div class="shown-wait">Vorschau lädt, sobald die Antwort fertig ist …</div>`)
    : "";
  return `<div class="shown"><div class="shown-head"><span class="ms">${f.kind === "image" ? "image" : f.vorschau ? "web" : "description"}</span><b>${esc(f.titel || f.name)}</b>
    <a href="${open}" target="_blank" rel="noopener" title="Groß öffnen" aria-label="Groß öffnen"><span class="ms">open_in_new</span></a>
    <a href="${url}" download="${esc(f.name)}" title="Herunterladen" aria-label="Herunterladen"><span class="ms">download</span></a></div>${body}</div>`;
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
  const html = (parts || []).map((p) => (p.t === "text" ? `<div class="md">${md(p.text)}</div>` : p.t === "approval" ? approvalHtml(p) : shownFile(p) ? shownHtml(shownFile(p), !!meta?.id) : toolHtml(p))).join("");
  // aufgeklappte Werkzeuge bleiben beim Neuzeichnen offen
  const open = new Set([...el.querySelectorAll("details")].flatMap((d, i) => (d.open ? [i] : [])));
  el.innerHTML = html || (text ? `<div class="md">${md(text)}</div>` : "");
  if (open.size) el.querySelectorAll("details").forEach((d, i) => { if (open.has(i)) d.open = true; });
  if (meta?.stopped) el.insertAdjacentHTML("beforeend", `<div class="msg-note"><span class="ms">stop_circle</span>Gestoppt</div>`);
  if (meta?.error) el.insertAdjacentHTML("beforeend", `<div class="msg-err"><span class="ms">error</span>${esc(meta.error)}</div>`);
  if (!el.innerHTML) el.innerHTML = `<div class="typing"><i></i><i></i><i></i></div>`;
  // bei "Auto": welches Modell es wurde (Grund beim Darüberfahren) und Bewertung, aus der Auto lernt
  const r = meta?.route;
  const rate = r && meta.id ? `<span class="route-rate" data-mid="${esc(meta.id)}">${RATINGS.map(([k, ic, t]) =>
    `<button type="button" data-rate="${k}" class="${r.rating === k ? "on" : ""}" title="${t}" aria-label="${t}"><span class="ms">${ic}</span></button>`).join("")}
    <button type="button" data-better class="${r.better ? "on" : ""}" title="Ein bestimmtes anderes Modell wäre besser gewesen" aria-label="Besseres Modell angeben"><span class="ms">swap_horiz</span></button>
    <span class="route-better">${r.better ? `→ ${esc(modelInfo(r.better).name)}` : ""}</span></span>` : "";
  const routed = r ? `<span class="msg-route" title="${esc(r.reason || "")}"><span class="ms">auto_awesome</span>Auto → ${esc(modelInfo(r.model).name)}${r.effort ? ` · ${EFFORT_NAMES[r.effort] || r.effort}` : ""}</span>${rate}` : "";
  const branch = meta?.id ? `<button type="button" class="msg-branch" data-branch="${esc(meta.id)}" title="Ab hier abzweigen: neuer Thread mit allem bis zu dieser Antwort – danach getrennt" aria-label="Ab hier abzweigen"><span class="ms">call_split</span>Abzweigen</button>` : "";
  if (meta?.ms != null || routed || branch)
    el.insertAdjacentHTML("beforeend", `<div class="msg-note msg-meta">${routed}${meta?.ms != null ? `<span title="So lange hat die Antwort gedauert"><span class="ms">timer</span>${fmtDur(meta.ms)}</span>` : ""}${branch}</div>`);
}

const EFFORT_NAMES = { low: "Niedrig", medium: "Mittel", high: "Hoch", xhigh: "Extra", max: "Max" };
const RATINGS = [["ok", "thumb_up", "Modell passte"], ["weak", "arrow_upward", "Zu schwach – ein stärkeres wäre besser gewesen"], ["strong", "arrow_downward", "Zu stark – ein kleineres hätte gereicht"]];
async function sendRating(box, body) {
  const route = await attempt(async () => (await api.post("/autoroute/rate", { message_id: box.dataset.mid, ...body })).route, "Danke – Auto merkt sich das");
  if (!route) return;
  box.querySelectorAll("[data-rate]").forEach((x) => x.classList.toggle("on", x.dataset.rate === route.rating));
  box.querySelector("[data-better]").classList.toggle("on", !!route.better);
  box.querySelector(".route-better").innerHTML = route.better ? `→ ${esc(modelInfo(route.better).name)}` : "";
}
document.addEventListener("click", (e) => {
  const b = e.target.closest(".route-rate [data-rate]");
  if (b) return sendRating(b.closest(".route-rate"), { rating: b.dataset.rate });
  const pick = e.target.closest(".route-rate [data-better]");
  if (!pick) return;
  const box = pick.closest(".route-rate"), slot = box.querySelector(".route-better");
  if (slot.querySelector("select")) return;
  slot.innerHTML = `<select aria-label="Besseres Modell"><option value="">Welches wäre besser gewesen?</option>${(state.boot?.models || [])
    .map((m) => `<option value="${esc(m)}">${esc(modelInfo(m).name)}${m.startsWith("agy:") ? " (Antigravity)" : ""}</option>`).join("")}</select>`;
  const sel = slot.querySelector("select");
  sel.focus();
  sel.onchange = () => sel.value && sendRating(box, { better_model: sel.value });
});

// Abzweigen: neuer Thread mit allem bis zu dieser Antwort (gleicher Kontext), danach zwei getrennte Wege
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-branch]");
  if (!b || !ctx.id || b.disabled) return;
  b.disabled = true;
  const t = await attempt(() => api.post(`/chats/${encodeURIComponent(ctx.id)}/fork`, { message_id: b.dataset.branch }), "Abgezweigt – neuer Thread");
  b.disabled = false;
  if (!t) return;
  await loadTree().catch(() => {});
  navigate(urlOf({ project: t.project_id, thread: t.id }));
});

// Stopp-Knopf: beendet die laufende Antwort (das Geschriebene bleibt)
export async function stopAnswer() {
  if (!ctx.busy || !ctx.id) return;
  await attempt(() => api.post(`/chats/${encodeURIComponent(ctx.id)}/stop`));
}

const RENDER_MS = 150;
async function follow(start, since = null) {
  const el = addBot();
  const t0 = since ? new Date(since).getTime() : Date.now();
  const clock = document.createElement("div");
  clock.className = "msg-note live";
  el.after(clock);
  // nur der Text tickt – das Symbol bleibt stehen (sonst startet die Animation jede Sekunde neu)
  clock.innerHTML = `<span class="ms spin">progress_activity</span><span></span>`;
  const label = clock.lastChild;
  const tick = () => { if (!document.hidden) label.textContent = `arbeitet seit ${fmtDur(Date.now() - t0)}`; };
  tick();
  const timer = setInterval(tick, 1000);
  const parts = [], tools = {}, meta = {};
  let ok = false;
  const ctrl = new AbortController();
  following = ctrl;
  ctx.busy = true;
  fire("code:busy");
  renderBot(el, parts);
  // Neuzeichnen gebündelt: höchstens alle RENDER_MS statt bei jedem Wortschnipsel (das ganze Markdown und alle
  // Werkzeug-Ausgaben neu aufzubauen, ist der Hauptgrund für warmes Handy und leeren Akku). Im Hintergrund gar nicht –
  // beim Zurückkommen wird einmal nachgezeichnet.
  let drawTimer = null, dirty = false;
  const draw = () => {
    clearTimeout(drawTimer);
    drawTimer = null;
    if (!dirty || document.hidden) return;
    dirty = false;
    renderBot(el, parts, "", meta);
    scroll();
  };
  const later = () => { dirty = true; if (!drawTimer) drawTimer = setTimeout(draw, RENDER_MS); };
  const onVisible = () => { if (!document.hidden) draw(); };
  document.addEventListener("visibilitychange", onVisible);
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
    } else if (ev.route) {
      meta.route = ev.route;
    } else if (ev.sys) {
      addSys(ev.sys, "swap_horiz");
    } else if (ev.done) {
      ok = ev.done.ok;
      if (ev.done.message_id) meta.id = ctx.lastId = ev.done.message_id;
      if (ev.done.ms != null) meta.ms = ev.done.ms;
      if (ev.done.stopped) meta.stopped = true;
      else if (!ok) meta.error = ev.done.error;
    }
    // Freigaben und das Ende sofort zeigen, alles andere gebündelt
    if (ev.approval || ev.approval_done || ev.done) { dirty = true; draw(); } else later();
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
    dirty = false;
    renderBot(el, parts, "", meta);
  } finally {
    clearInterval(timer);
    clearTimeout(drawTimer);
    document.removeEventListener("visibilitychange", onVisible);
    if (dirty && !ctrl.signal.aborted && el.isConnected) { dirty = false; renderBot(el, parts, "", meta); }
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
