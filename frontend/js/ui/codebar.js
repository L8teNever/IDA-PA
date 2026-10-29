// Code area pieces shared by the thread view and the project page:
// the bar above a code thread (project, branch, rights), the changes sheet (diff, commit, push, PR) and approval cards.
import { api } from "../api.js";
import { $, attempt, closeSheet, esc, openSheet, toast } from "./dom.js";
import { openFileSheet } from "./fileviewer.js";
import { openDrawer } from "./shell.js";

export const MODES = [
  ["ask", "front_hand", "Nachfragen", "Jede Änderung und jeder Befehl wartet auf dein OK"],
  ["edits", "edit_note", "Edits automatisch", "Dateien ändern ohne Nachfrage, Befehle fragen nach"],
  ["full", "bolt", "Voller Zugriff", "Alles ohne Nachfrage"],
];
const modeOf = (m) => MODES.find((x) => x[0] === m) || MODES[0];

// Sticky bar at the top of a code thread
export function codeBar(project, mode) {
  const g = project.git || {};
  const changes = (g.files || []).length;
  return `<div class="code-bar" id="codeBar">
    <button type="button" class="icon-btn cb-menu" id="cbMenu" aria-label="Menü"><span class="ms">menu</span></button>
    <button type="button" class="icon-btn cb-pane" data-pane="left" aria-label="Threads ein-/ausblenden" title="Threads (Strg+B)"><span class="ms">left_panel_open</span></button>
    <a class="icon-btn cb-back" href="/code/${project.id}" aria-label="Zum Projekt"><span class="ms">arrow_back</span></a>
    <div class="cb-main"><b>${esc(project.name)}</b>
      <small><span class="ms">alt_route</span>${esc(g.branch || project.default_branch)}${g.ahead ? ` · ${g.ahead}↑` : ""}${g.behind ? ` · ${g.behind}↓` : ""}</small></div>
    <button type="button" class="cb-chip" id="cbFiles" data-files="${project.id}" title="Dateien & Vorschau">
      <span class="ms">folder_open</span><span>Dateien</span></button>
    <button type="button" class="cb-chip" id="cbMode" title="${esc(modeOf(mode)[3])}"><span class="ms">${modeOf(mode)[1]}</span><span>${modeOf(mode)[2]}</span></button>
    <button type="button" class="cb-chip ${changes ? "has" : ""}" id="cbChanges" data-changes="${project.id}" title="Änderungen, Commit, Push, Pull Request">
      <span class="ms">difference</span><span>${changes ? `${changes} Änderung${changes === 1 ? "" : "en"}` : "Änderungen"}</span>${changes ? `<b class="cb-n">${changes}</b>` : ""}</button>
    <button type="button" class="icon-btn cb-pane" data-pane="right" aria-label="Dateien ein-/ausblenden" title="Dateien, Änderungen, Vorschau (Strg+.)"><span class="ms">right_panel_open</span></button>
  </div>`;
}

// mode menu of the bar; onPick(mode) saves it
export function bindCodeBar(onPick) {
  const b = $("cbMode");
  if (!b) return;
  b.onclick = () => {
    const s = openSheet(`<h2 class="cs-title">Rechte in diesem Thread</h2>
      <div class="slist">${MODES.map(([id, ic, name, desc]) => `<button type="button" class="srow" data-mode="${id}">
        <span class="sic"><span class="ms">${ic}</span></span><span class="smain"><b>${name}</b><small>${desc}</small></span></button>`).join("")}</div>
      <p class="hint">Gilt ab der nächsten Nachricht.</p>`);
    s.onclick = async (e) => {
      const r = e.target.closest("[data-mode]");
      if (!r) return;
      closeSheet();
      await onPick(r.dataset.mode);
    };
  };
}

export async function refreshBar(projectId, mode) {
  const bar = $("codeBar");
  if (!bar) return;
  const p = await api.get(`/code/projects/${projectId}`).catch(() => null);
  if (p && $("codeBar")) { bar.outerHTML = codeBar(p, mode); document.dispatchEvent(new CustomEvent("code:bar")); }
}

// ---- approvals ("Nachfragen") ----
// paths inside the project folder (data/projects/<id>/…) relative to it
const rel = (f) => String(f).replace(/^.*[\\/]projects[\\/][0-9a-f]{12}[\\/]/, "");

function approvalBody(a) {
  const i = a.input || {};
  if (i.command) return `<pre class="ap-cmd">${esc(i.command)}</pre>${i.description ? `<small>${esc(i.description)}</small>` : ""}`;
  const raw = i.file_path || i.path || i.notebook_path;
  const file = raw && rel(raw);
  if (file && (i.old_string !== undefined || i.new_string !== undefined)) {
    return `<div class="ap-file">${esc(file)}</div><pre class="diff">${(i.old_string || "").split("\n").map((l) => `<i class="del">-${esc(l)}</i>`).join("")}${(i.new_string || "").split("\n").map((l) => `<i class="add">+${esc(l)}</i>`).join("")}</pre>`;
  }
  if (file && i.content !== undefined) return `<div class="ap-file">${esc(file)}</div><pre class="diff">${String(i.content).split("\n").slice(0, 60).map((l) => `<i class="add">+${esc(l)}</i>`).join("")}</pre>`;
  return `<pre class="ap-cmd">${esc(JSON.stringify(i, null, 2))}</pre>`;
}

const DECIDED = { allow: ["check_circle", "Erlaubt"], always: ["done_all", "Immer erlaubt"], deny: ["block", "Abgelehnt"], stopped: ["stop_circle", "Abgebrochen"] };
export function approvalHtml(a) {
  const d = a.decision && (DECIDED[a.decision] || DECIDED.stopped);
  return `<div class="approval ${d ? "decided" : ""}" data-ap="${a.id}">
    <div class="ap-head"><span class="ms">${d ? d[0] : "front_hand"}</span><b>${d ? d[1] : "Darf ich?"}</b><span class="ap-tool">${esc(a.tool)}</span></div>
    ${d ? "" : approvalBody(a)}
    ${d ? "" : `<div class="ap-btns">
      <button type="button" class="es-btn primary" data-approve="allow" data-id="${a.id}"><span class="ms">check</span>Erlauben</button>
      <button type="button" class="es-btn" data-approve="always" data-id="${a.id}" title="${esc(a.tool)} in diesem Thread nicht mehr fragen"><span class="ms">done_all</span>Immer</button>
      <button type="button" class="es-btn" data-approve="deny" data-id="${a.id}"><span class="ms">close</span>Ablehnen</button></div>`}
  </div>`;
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-approve]");
  if (!b) return;
  b.closest(".ap-btns")?.querySelectorAll("button").forEach((x) => (x.disabled = true));
  await attempt(() => api.post(`/code/approvals/${b.dataset.id}`, { decision: b.dataset.approve }));
});

// ---- changes sheet: diff, commit, push, branch, pull request ----
function diffHtml(text) {
  if (!text.trim()) return `<div class="empty">Keine Änderungen seit dem letzten Commit.</div>`;
  return `<pre class="diff big">${text.split("\n").map((l) => {
    const c = l.startsWith("diff --git") ? "file" : l.startsWith("+++") || l.startsWith("---") ? "meta" : l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
    return `<i class="${c}">${esc(l)}</i>`;
  }).join("")}</pre>`;
}

export async function openChanges(projectId, { onChange } = {}) {
  const s = openSheet(`<div class="empty">Lade Änderungen …</div>`, "code-sheet");
  await renderChanges(s, projectId, { onChange, onClose: closeSheet });
}

// diff, commit, push, branch, PR – in a sheet (onClose set) or inline in the right pane of a code thread
export async function renderChanges(s, projectId, { onChange, onClose } = {}) {
  const draw = async (file = null) => {
    let st, d;
    try {
      [st, d] = await Promise.all([api.get(`/code/projects/${projectId}/git`), api.get(`/code/projects/${projectId}/diff${file ? `?file=${encodeURIComponent(file)}` : ""}`)]);
    } catch (e) { s.innerHTML = `<div class="handle"></div><div class="ferr">${esc(e.message)}</div>`; return; }
    const gh = !!st.github;
    s.innerHTML = `${onClose ? `<div class="handle"></div>` : ""}
      ${onClose ? `<div class="cs-head"><h2 class="cs-title">Änderungen</h2><button class="icon-btn" data-x aria-label="Schließen"><span class="ms">close</span></button></div>` : ""}
      <div class="cs-info"><span><span class="ms">alt_route</span><b>${esc(st.branch)}</b>${st.upstream ? ` → ${esc(st.upstream)}` : ""}${st.ahead ? ` · ${st.ahead} nicht gepusht` : ""}${st.behind ? ` · ${st.behind} neuer auf GitHub` : ""}</span>
        <span>${st.remote ? `<span class="ms">cloud</span>${esc(st.remote.replace(/^https?:\/\//, "").replace(/\.git$/, ""))}` : `<span class="ms">cloud_off</span>nur lokal`}</span>
        <span>${gh ? `<span class="ms">account_circle</span>${esc(st.github)}` : `<a href="/einstellungen/github"><span class="ms">link_off</span>GitHub nicht verbunden</a>`}</span></div>
      ${st.files.length ? `<div class="cs-files">${st.files.map((f) => `<button type="button" class="cs-file ${file === f.path ? "on" : ""}" data-file="${esc(f.path)}"><b class="st-${f.code === "?" ? "A" : f.code}">${esc(f.code === "?" ? "A" : f.code)}</b>${esc(f.path)}</button>`).join("")}
        ${file ? `<button type="button" class="cs-file" data-file=""><span class="ms">select_all</span>Alle</button>` : ""}</div>` : ""}
      ${diffHtml(d.diff)}${d.truncated ? `<p class="hint">Gekürzt – zu viele Änderungen für die Anzeige.</p>` : ""}
      ${st.files.length ? `<label class="fld"><span>Commit-Nachricht</span><input id="csMsg" maxlength="300" placeholder="Was wurde geändert?"></label>
        <div class="es-actions"><button class="es-btn primary" data-act="commit"><span class="ms">commit</span>Commit</button>
        ${st.remote ? `<button class="es-btn" data-act="commitpush"><span class="ms">cloud_upload</span>Commit & Push</button>` : ""}</div>` : ""}
      <div class="es-actions" style="margin-top:8px">
        ${st.remote && st.has_commits && (st.ahead || !st.upstream) ? `<button class="es-btn" data-act="push"><span class="ms">cloud_upload</span>Push</button>` : ""}
        ${!st.remote ? `<button class="es-btn" data-act="publish"><span class="ms">publish</span>Auf GitHub veröffentlichen</button>` : ""}
        <button class="es-btn" data-act="branch"><span class="ms">add</span>Neuer Branch</button>
        ${st.branches.length > 1 ? `<button class="es-btn" data-act="switch"><span class="ms">swap_horiz</span>Branch wechseln</button>` : ""}
        ${st.remote ? `<button class="es-btn" data-act="pr"><span class="ms">merge</span>Pull Request</button>` : ""}
      </div><div class="ferr" id="csErr"></div>`;
    if (onClose) s.querySelector("[data-x]").onclick = onClose;
    s.querySelectorAll("[data-file]").forEach((b) => (b.onclick = () => draw(b.dataset.file || null)));
    s.querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
      const act = b.dataset.act;
      const run = async (fn, ok) => {
        b.disabled = true;
        $("csErr").textContent = "";
        try { const r = await fn(); if (ok) toast(typeof ok === "function" ? ok(r) : ok); await draw(); onChange?.(); return r; }
        catch (e) { $("csErr").textContent = e.message; b.disabled = false; }
      };
      if (act === "commit" || act === "commitpush") {
        const message = $("csMsg").value.trim();
        if (!message) { $("csErr").textContent = "Commit-Nachricht fehlt"; $("csMsg").focus(); return; }
        await run(() => api.post(`/code/projects/${projectId}/commit`, { message, push: act === "commitpush" }), (r) => `Committet (${r.sha})${r.pushed ? " und gepusht" : ""}`);
      }
      if (act === "push") await run(() => api.post(`/code/projects/${projectId}/push`), "Gepusht");
      if (act === "publish") {
        const name = prompt("Name des neuen GitHub-Repos:", "");
        if (name === null) return;
        const priv = confirm("Privates Repo? (OK = privat, Abbrechen = öffentlich)");
        await run(() => api.post(`/code/projects/${projectId}/publish`, { name, private: priv }), "Auf GitHub veröffentlicht");
      }
      if (act === "branch") {
        const name = prompt("Name des neuen Branches (z. B. ida/neues-feature):", "ida/");
        if (name) await run(() => api.post(`/code/projects/${projectId}/branch`, { name, create: true }), `Auf ${name}`);
      }
      if (act === "switch") {
        const name = prompt(`Zu welchem Branch? (${st.branches.join(", ")})`, st.branches.find((x) => x !== st.branch) || "");
        if (name) await run(() => api.post(`/code/projects/${projectId}/branch`, { name, create: false }), `Auf ${name}`);
      }
      if (act === "pr") {
        const title = prompt("Titel des Pull Requests:", "");
        if (!title) return;
        const r = await run(() => api.post(`/code/projects/${projectId}/pr`, { title }), (x) => `Pull Request #${x.number} erstellt`);
        if (r?.url) window.open(r.url, "_blank", "noopener");
      }
    }));
  };
  await draw();
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#cbMenu")) openDrawer();
  const bChanges = e.target.closest("[data-changes]");
  if (bChanges?.id === "cbChanges" && document.body.classList.contains("code-thread") && matchMedia("(min-width: 1100px)").matches) {
    document.dispatchEvent(new CustomEvent("code:pane", { detail: "changes" }));
    return;
  }
  // phone: files and changes open one sheet with the tabs of the desktop's right pane
  const files = e.target.closest("#cbFiles");
  if ((files || bChanges?.id === "cbChanges") && document.body.classList.contains("code-thread")) {
    document.dispatchEvent(new CustomEvent("code:sheet", { detail: files ? { project: files.dataset.files, tab: "files" } : { project: bChanges.dataset.changes, tab: "changes" } }));
    return;
  }
  if (bChanges) openChanges(bChanges.dataset.changes, { onChange: () => document.dispatchEvent(new CustomEvent("code:changed")) });
  const bFiles = e.target.closest("[data-files]");
  if (bFiles) openFileSheet(bFiles.dataset.files);
});
