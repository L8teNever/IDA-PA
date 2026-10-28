// Desktop layout of a code thread (like T3): threads of the project on the left, the chat in the middle,
// files & preview / changes on the right. Below 1100px the panes are hidden (CSS) and the code bar's chips are used instead.
import { api } from "../api.js";
import { $, esc, fmtRel } from "./dom.js";
import { mountFileExplorer } from "./fileviewer.js";
import { openChanges, MODES } from "./codebar.js";
import { openDrawer } from "./shell.js";

let cur = null; // { project, getId, tab }

export function mountCodePanes(project, getId) {
  const same = cur && cur.project.id === project.id;
  cur = { project, getId, tab: same ? cur.tab : "files" };
  drawLeft();
  if (!same || !$("cpBody")) drawRight();
}

async function drawLeft() {
  const { project } = cur;
  const box = $("codeLeft");
  box.innerHTML = `<div class="cp-head">
      <button type="button" class="icon-btn" id="cpMenu" aria-label="Menü"><span class="ms">menu</span></button>
      <a class="cp-proj" href="/code/${project.id}" title="Zum Projekt"><b>${esc(project.name)}</b><small>Projekt öffnen</small></a>
    </div>
    <a class="cp-new" href="/code/${project.id}/neu"><span class="ms">edit_square</span>Neuer Thread</a>
    <div class="cp-list" id="cpThreads"><div class="empty">Lade …</div></div>`;
  $("cpMenu").onclick = openDrawer;
  await refreshThreads();
}

async function refreshThreads() {
  if (!cur || !$("cpThreads")) return;
  const { project, getId } = cur;
  let list;
  try { list = await api.get(`/code/projects/${project.id}/threads`); } catch { return; }
  if (!cur || !$("cpThreads")) return;
  const id = getId();
  const icon = (m) => (MODES.find((x) => x[0] === m) || MODES[0])[1];
  $("cpThreads").innerHTML = list.length ? list.map((t) => `<a class="cp-thread ${t.id === id ? "on" : ""}" href="/code/${project.id}/${t.id}">
      <span class="ms">${t.waiting ? "front_hand" : t.running ? "progress_activity" : icon(t.permission_mode)}</span>
      <span class="cp-tmain"><b>${esc(t.title)}</b><small>${fmtRel(t.updated_at)}</small></span></a>`).join("")
    : `<div class="empty">Noch keine Threads.</div>`;
}

function drawRight() {
  const { project } = cur;
  $("codeRight").innerHTML = `<div class="cp-tabs">
      <button type="button" data-tab="files" class="${cur.tab === "files" ? "on" : ""}"><span class="ms">folder_open</span>Dateien</button>
      <button type="button" data-tab="changes" class="${cur.tab === "changes" ? "on" : ""}"><span class="ms">difference</span>Änderungen</button>
      <button type="button" class="icon-btn mini" id="cpWide" title="Bereich breiter / schmaler"><span class="ms">left_panel_open</span></button>
    </div>
    <div class="cp-body" id="cpBody"></div>`;
  $("codeRight").querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { cur.tab = b.dataset.tab; drawRight(); }));
  $("cpWide").onclick = () => {
    const on = document.body.classList.toggle("code-wide");
    localStorage.setItem("code-wide", on ? "1" : "");
  };
  if (cur.tab === "files") { mountFileExplorer($("cpBody"), project.id); return; }
  drawChanges();
}

async function drawChanges() {
  const body = $("cpBody");
  if (!body || !cur || cur.tab !== "changes") return;
  const { project } = cur;
  let st;
  try { st = await api.get(`/code/projects/${project.id}/git`); } catch (e) { body.innerHTML = `<div class="ferr">${esc(e.message)}</div>`; return; }
  if (!$("cpBody") || cur?.tab !== "changes") return;
  body.innerHTML = `<div class="cs-info"><span><span class="ms">alt_route</span><b>${esc(st.branch)}</b>${st.ahead ? ` · ${st.ahead} nicht gepusht` : ""}${st.behind ? ` · ${st.behind} neuer auf GitHub` : ""}</span></div>
    ${st.files.length ? `<div class="cp-files">${st.files.map((f) => `<button type="button" class="cs-file" data-open><b class="st-${f.code === "?" ? "A" : f.code}">${esc(f.code === "?" ? "A" : f.code)}</b>${esc(f.path)}</button>`).join("")}</div>`
      : `<div class="empty">Keine Änderungen seit dem letzten Commit.</div>`}
    <button type="button" class="es-btn primary" id="cpOpenChanges"><span class="ms">commit</span>Diff, Commit & Push</button>`;
  const open = () => openChanges(project.id, { onChange: () => document.dispatchEvent(new CustomEvent("code:changed")) });
  $("cpOpenChanges").onclick = open;
  body.querySelectorAll("[data-open]").forEach((b) => (b.onclick = open));
}

// the agent changed files / a commit happened: update the panes
document.addEventListener("code:bar", () => { if (cur) { refreshThreads(); if (cur.tab === "changes") drawChanges(); } });
document.addEventListener("chat:done", () => { if (cur) refreshThreads(); });
if (localStorage.getItem("code-wide")) document.body.classList.add("code-wide");
