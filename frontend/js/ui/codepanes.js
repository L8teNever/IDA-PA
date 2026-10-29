// Desktop layout of a code thread (like T3 Code): threads of all projects on the left, the chat in the middle,
// files / changes / preview on the right. Both side panes can be hidden (Ctrl+B / Ctrl+.) and resized by dragging.
// Below 1100px the panes are hidden (CSS) and the chips of the code bar are used instead.
import { api } from "../api.js";
import { $, closeSheet, esc, fmtRel, openSheet } from "./dom.js";
import { mountFileExplorer } from "./fileviewer.js";
import { MODES, renderChanges } from "./codebar.js";
import { openDrawer } from "./shell.js";

const TABS = [["files", "folder_open", "Dateien"], ["changes", "difference", "Änderungen"], ["preview", "visibility", "Vorschau"]];
const body = document.body;
let cur = null;        // { project, getId, tab }
let lastHtml = null;   // { projectId, path } – last HTML file opened in the explorer
let q = "";            // thread search

// ---- panes: hide / resize (kept in localStorage) ----
const setW = (name, px) => body.style.setProperty(name, `${px}px`);
const saved = (k) => +localStorage.getItem(k) || 0;
if (saved("code-cl")) setW("--cl", saved("code-cl"));
if (saved("code-cr")) setW("--cr", saved("code-cr"));
for (const side of ["left", "right"]) if (localStorage.getItem(`code-hide-${side}`)) body.classList.add(`no-${side}`);

export function togglePane(side) {
  const on = body.classList.toggle(`no-${side}`);
  localStorage.setItem(`code-hide-${side}`, on ? "1" : "");
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-pane]");
  if (b) togglePane(b.dataset.pane);
});
addEventListener("keydown", (e) => {
  if (!body.classList.contains("code-thread") || !(e.ctrlKey || e.metaKey)) return;
  if (e.key === "b") { e.preventDefault(); togglePane("left"); }
  if (e.key === ".") { e.preventDefault(); togglePane("right"); }
});

function dragHandle(side) {
  const h = document.createElement("div");
  h.className = `cp-resize ${side}`;
  h.title = "Breite ändern";
  h.onpointerdown = (e) => {
    e.preventDefault();
    h.setPointerCapture(e.pointerId);
    const name = side === "left" ? "--cl" : "--cr";
    const move = (ev) => {
      const w = (side === "left" ? ev.clientX : innerWidth - ev.clientX) - 10; // panes float 10px from the edge
      setW(name, Math.max(220, Math.min(w, innerWidth * 0.45)));
    };
    const up = () => {
      h.removeEventListener("pointermove", move);
      h.removeEventListener("pointerup", up);
      const px = parseInt(body.style.getPropertyValue(name));
      if (px) localStorage.setItem(side === "left" ? "code-cl" : "code-cr", px);
    };
    h.addEventListener("pointermove", move);
    h.addEventListener("pointerup", up);
  };
  return h;
}

export function mountCodePanes(project, getId) {
  const same = cur && cur.project.id === project.id;
  cur = { project, getId, tab: same ? cur.tab : "files" };
  drawLeft();
  drawRight();
}

// ---- left: threads of all projects, grouped, with search ----
function drawLeft() {
  const { project } = cur;
  const box = $("codeLeft");
  box.innerHTML = `<div class="cp-head">
      <button type="button" class="icon-btn" id="cpMenu" aria-label="Menü"><span class="ms">menu</span></button>
      <a class="cp-proj" href="/code" title="Alle Projekte"><b>Code</b><small>Alle Projekte</small></a>
      <button type="button" class="icon-btn" data-pane="left" title="Leiste ausblenden (Strg+B)"><span class="ms">left_panel_close</span></button>
    </div>
    <a class="cp-new" href="/code/${project.id}/neu"><span class="ms">edit_square</span>Neuer Thread in ${esc(project.name)}</a>
    <div class="search cp-search"><span class="ms">search</span><input id="cpQ" placeholder="Threads suchen" value="${esc(q)}"></div>
    <div class="cp-list" id="cpThreads"><div class="empty">Lade …</div></div>`;
  box.appendChild(dragHandle("left"));
  $("cpMenu").onclick = openDrawer;
  $("cpQ").oninput = () => { q = $("cpQ").value; paintThreads(); };
  refreshThreads();
}

let groups = []; // [{ project, threads }]
async function refreshThreads() {
  if (!cur) return;
  try {
    const projects = await api.get("/code/projects");
    groups = await Promise.all(projects.filter((p) => p.threads || p.id === cur?.project.id).map(async (p) => ({
      project: p, threads: await api.get(`/code/projects/${p.id}/threads`).catch(() => []),
    })));
  } catch { return; }
  paintThreads();
}

function paintThreads() {
  const box = $("cpThreads");
  if (!box || !cur) return;
  const id = cur.getId();
  const needle = q.trim().toLowerCase();
  const icon = (t) => (t.waiting ? "front_hand" : t.running ? "progress_activity" : (MODES.find((x) => x[0] === t.permission_mode) || MODES[0])[1]);
  const html = groups.map(({ project, threads }) => {
    const list = threads.filter((t) => !needle || t.title.toLowerCase().includes(needle));
    if (needle && !list.length) return "";
    const open = project.id === cur.project.id || needle;
    return `<details class="cp-group" ${open ? "open" : ""}><summary><span class="ms">folder_code</span><b>${esc(project.name)}</b><small>${threads.length}</small></summary>
      ${list.map((t) => `<a class="cp-thread ${t.id === id ? "on" : ""} ${t.waiting ? "wait" : ""}" href="/code/${project.id}/${t.id}">
        <span class="ms ${t.running ? "spin" : ""}">${icon(t)}</span>
        <span class="cp-tmain"><b>${esc(t.title)}</b><small>${t.waiting ? "wartet auf Freigabe" : t.running ? "arbeitet …" : fmtRel(t.updated_at)}</small></span></a>`).join("") || `<div class="cp-none">Noch keine Threads</div>`}
    </details>`;
  }).join("");
  box.innerHTML = html || `<div class="empty">Keine Threads gefunden.</div>`;
}

// ---- right: files | changes | preview (on the phone the same tabs in a sheet) ----
const tabsHtml = (tab, closeBtn) => `<div class="cp-tabs">${TABS.map(([k, ic, t]) => `<button type="button" data-tab="${k}" class="${tab === k ? "on" : ""}"><span class="ms">${ic}</span><span class="cp-tl">${t}</span></button>`).join("")}${closeBtn}</div>`;

function fillTab(el, projectId, tab, isCurrent) {
  if (tab === "files") mountFileExplorer(el, projectId);
  else if (tab === "changes") renderChanges(el, projectId, { onChange: () => document.dispatchEvent(new CustomEvent("code:changed")) });
  else drawPreview(el, projectId, isCurrent);
}

function drawRight() {
  const { project } = cur;
  const box = $("codeRight");
  box.innerHTML = tabsHtml(cur.tab, `<button type="button" class="icon-btn" data-pane="right" title="Bereich ausblenden (Strg+.)"><span class="ms">right_panel_close</span></button>`)
    + `<div class="cp-body" id="cpBody"></div>`;
  box.appendChild(dragHandle("right"));
  box.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { cur.tab = b.dataset.tab; drawRight(); }));
  const tab = cur.tab;
  fillTab($("cpBody"), project.id, tab, (el) => el.isConnected && cur?.tab === tab);
}

let sheetTab = null; // tab of the open phone sheet
export function openCodeSheet(projectId, tab = "files") {
  sheetTab = tab;
  const s = openSheet("", "code-sheet");
  s.innerHTML = `<div class="handle"></div>${tabsHtml(tab, `<button type="button" class="icon-btn" data-x aria-label="Schließen"><span class="ms">close</span></button>`)}
    <div class="cp-body cs-pane" id="csBody"></div>`;
  s.querySelector("[data-x]").onclick = closeSheet;
  s.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => openCodeSheet(projectId, b.dataset.tab)));
  fillTab($("csBody"), projectId, tab, (el) => el.isConnected && sheetTab === tab);
}
document.addEventListener("code:sheet", (e) => openCodeSheet(e.detail.project, e.detail.tab));

async function drawPreview(el, projectId, isCurrent) {
  let path = lastHtml?.projectId === projectId ? lastHtml.path : null;
  if (!path) {
    try {
      const d = await api.get(`/code/projects/${projectId}/files?path=`);
      path = d.entries.find((e) => !e.is_dir && e.name.toLowerCase() === "index.html")?.path
        || d.entries.find((e) => !e.is_dir && /\.html?$/i.test(e.name))?.path;
    } catch { /* shown below */ }
  }
  if (!isCurrent(el)) return;
  if (!path) { el.innerHTML = `<div class="empty">Keine HTML-Datei im Projekt. Öffne im Tab „Dateien“ eine HTML-Datei, dann erscheint sie hier.</div>`; return; }
  const src = `/api/v1/code/projects/${projectId}/raw/${encodeURI(path.replace(/\\/g, "/"))}`;
  el.innerHTML = `<div class="cp-pv-bar"><span class="mono">${esc(path)}</span>
      <button type="button" class="icon-btn ib-sm" data-pv-reload title="Neu laden"><span class="ms">refresh</span></button></div>
    <iframe class="cp-pv-frame" src="${src}" sandbox="allow-scripts allow-forms allow-popups"></iframe>`;
  el.querySelector("[data-pv-reload]").onclick = () => { el.querySelector("iframe").src = `${src}?_r=${Date.now()}`; };
}

// Ida changed something / a commit happened / an HTML file was opened: bring the panes up to date
document.addEventListener("fe:html", (e) => { lastHtml = e.detail; });
document.addEventListener("code:bar", () => {
  if (!cur) return;
  refreshThreads();
  if (cur.tab === "changes") drawRight();
  document.querySelectorAll(".cp-pv-frame").forEach((f) => { f.src = `${f.src.split("?")[0]}?_r=${Date.now()}`; });
});
document.addEventListener("code:pane", (e) => {
  if (!cur) return;
  if (body.classList.contains("no-right")) togglePane("right");
  cur.tab = e.detail;
  drawRight();
});
document.addEventListener("chat:done", () => { if (cur) refreshThreads(); });
