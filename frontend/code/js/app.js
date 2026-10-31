// IDA Code – Aufbau wie Kalender und Wiki: schmale Leiste links, Seitenleiste mit Projekten und Threads,
// schwebende Kopfzeile, Inhalt darunter; rechts (am PC) Dateien / Änderungen / Vorschau des Projekts.
import { api } from "./api.js";
import { ctx, detach, mountNew, mountThread } from "./chat.js";
import { bindComposer, composerHtml, dropFiles, refreshChip } from "./composer.js";
import { fillTab, openPaneModal, tabsHtml } from "./panes.js";
import { navigate, parse, setUrl, startRouter, urlOf } from "./router.js";
import { openSettings } from "./settings.js";
import { MODES, codeEnabled, loadBoot, loadTree, loadUsage, mainAgent, modeOf, onChange, projectById, repoName, state } from "./store.js";
import { $, attempt, confirmDialog, esc, fmtRel, formDialog, openModal, popMenu, sheetHead, toast } from "./ui.js";

let route = { view: "home" };
let cur = null;           // aktuelles Projekt (mit git-Status), falls route.project
let thread = null;        // geöffneter Thread (Titel usw.)
let q = "";               // Suche in der Seitenleiste
let paneTab = localStorage.getItem("code-pane") || null; // offener Tab rechts (PC), null = zu
const open = new Set();   // aufgeklappte Projekte in der Seitenleiste
const wide = () => matchMedia("(min-width: 1200px)").matches;
const desktop = () => matchMedia("(min-width: 961px)").matches;

// ---------- Rahmen ----------
export async function startApp(root) {
  root.innerHTML = `<div class="cd-shell" id="shell">
      <nav class="cd-rail" id="rail" aria-label="Navigation"></nav>
      <aside class="cd-sidebar" id="side">
        <div class="sidebar-head" id="sideHead">
          <div class="brand-row"><span class="brand-title"><span class="brand-dot"></span>IDA Code</span>
            <span class="brand-actions">
              <button type="button" class="icon-btn mobile-only" data-act="settings" title="Einstellungen" aria-label="Einstellungen"><span class="ms">settings</span></button>
              <button type="button" class="icon-btn mobile-only" data-act="drawer-close" aria-label="Schließen"><span class="ms">close</span></button></span></div>
          <label class="side-search"><span class="ms">search</span><input id="sideQ" type="search" placeholder="Threads suchen" autocomplete="off"></label>
        </div>
        <div class="tree-scroll" id="sideList"><div class="side-empty">Lade …</div></div>
        <div class="sidebar-foot" id="sideFoot"></div>
      </aside>
      <main class="cd-stage" id="stage"></main>
      <aside class="cd-pane" id="pane" hidden></aside>
    </div>
    <nav class="cd-bottom-nav" id="bnav" aria-label="Navigation"></nav>
    <div class="drawer-scrim" id="scrim" hidden></div>`;

  $("sideQ").oninput = () => { q = $("sideQ").value; drawSide(); };
  $("scrim").onclick = closeDrawer;
  document.addEventListener("click", onClick);
  addEventListener("resize", () => { drawPane(); measureSide(); });
  addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") { e.preventDefault(); openDrawer(); $("sideQ").focus(); }
    if ((e.ctrlKey || e.metaKey) && e.key === ".") { e.preventDefault(); setPane(paneTab ? null : "files"); }
    if (e.key === "Escape" && document.body.classList.contains("drawer-open")) closeDrawer();
  });
  onChange(() => { drawSide(); drawRail(); drawFoot(); });

  try {
    await loadBoot();
  } catch (e) {
    $("stage").innerHTML = `<div class="empty-stage"><span class="ms big">cloud_off</span><b>IDA ist gerade nicht erreichbar.</b><br><small>${esc(e.message)}</small><br><br>
      <button type="button" class="btn-primary" onclick="location.reload()"><span class="ms">refresh</span>Erneut versuchen</button></div>`;
    return;
  }
  loadUsage();
  setInterval(() => !document.hidden && loadUsage(), 180000);
  poll();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  document.addEventListener("code:thread", (e) => {
    // neuer Thread angelegt: Adresse anpassen und in der Seitenleiste zeigen
    setUrl(urlOf({ project: e.detail.project, thread: e.detail.id }));
    route = parse();
    thread = { id: e.detail.id, title: "Neuer Thread" };
    poll();
  });
  document.addEventListener("code:done", (e) => { if (!e.detail?.left) { poll(); refreshProject(); } });
  document.addEventListener("code:busy", () => poll());
  document.addEventListener("code:waiting", () => poll());
  let filesTimer = null;
  document.addEventListener("code:files", () => { clearTimeout(filesTimer); filesTimer = setTimeout(refreshProject, 1500); });
  startRouter(show);
}

let pollTimer = null;
async function poll() {
  clearTimeout(pollTimer);
  let busy = false;
  try { busy = await loadTree(); } catch { /* nächster Versuch */ }
  const p = cur && projectById(cur.id);
  // ein Projekt wurde fertig geklont: Projektseite neu zeichnen
  if (p && cur.status !== p.status && route.view === "project") show(route);
  pollTimer = setTimeout(poll, busy || ctx.busy ? 4000 : 30000);
}

// ---------- Leiste links ----------
function drawRail() {
  const ready = cur?.status === "ready";
  const btn = (attrs, active, icon, label, disabled = false) => `<button type="button" class="rail-btn ${active ? "active" : ""}" ${attrs} ${disabled ? "disabled" : ""}>
    <span class="rail-ic"><span class="ms">${icon}</span></span><span class="rail-lbl">${label}</span></button>`;
  const fabTitle = route.project ? "Neuer Thread" : "Projekt hinzufügen";
  $("rail").innerHTML = `<button type="button" class="rail-fab" data-act="new" title="${fabTitle}" aria-label="${fabTitle}"><span class="ms">${route.project ? "edit_square" : "add"}</span></button>
    <div class="rail-group">${btn('data-act="home"', route.view === "home", "folder_code", "Projekte")}</div>
    <div class="rail-group">
      ${btn('data-pane="files"', paneTab === "files" && wide() && ready, "folder_open", "Dateien", !ready)}
      ${btn('data-pane="changes"', paneTab === "changes" && wide() && ready, "difference", "Änderungen", !ready)}
      ${btn('data-pane="preview"', paneTab === "preview" && wide() && ready, "visibility", "Vorschau", !ready)}
    </div>
    <div class="rail-spacer"></div>
    ${btn('data-act="settings" title="Einstellungen"', false, "settings", "Einstellungen")}`;
  const changes = (cur?.git?.files || []).length;
  const chBadge = $("rail").querySelector('[data-pane="changes"] .rail-ic');
  if (changes && chBadge) chBadge.insertAdjacentHTML("beforeend", `<b class="rail-badge">${changes > 99 ? "99+" : changes}</b>`);
  drawBottom();
}

function drawBottom() {
  const ready = cur?.status === "ready";
  const b = (attrs, active, icon, label, disabled = false) => `<button type="button" class="bnav-btn ${active ? "active" : ""}" ${attrs} ${disabled ? "disabled" : ""}><span class="ms">${icon}</span>${label}</button>`;
  $("bnav").innerHTML = b('data-act="home"', route.view === "home", "folder_code", "Projekte")
    + b('data-act="drawer"', false, "forum", "Threads")
    + `<button type="button" class="bnav-fab" data-act="new" aria-label="${route.project ? "Neuer Thread" : "Projekt hinzufügen"}"><span class="ms">${route.project ? "edit_square" : "add"}</span></button>`
    + b('data-pane="files"', false, "folder_open", "Dateien", !ready)
    + b('data-act="settings"', false, "settings", "Mehr");
  document.body.classList.toggle("in-thread", route.view === "thread");
}

// ---------- Seitenleiste ----------
const threadIcon = (t) => (t.waiting ? "front_hand" : t.running ? "progress_activity" : modeOf(t.permission_mode)[1]);
function drawSide() {
  const box = $("sideList");
  if (!box) return;
  if (!state.loaded) return;
  if (!codeEnabled()) { box.innerHTML = `<div class="side-empty">Der Code-Bereich ist aus.<br><button type="button" class="btn-tonal sm" data-act="settings">Einstellungen</button></div>`; return; }
  const needle = q.trim().toLowerCase();
  const scroll = box.scrollTop;
  const html = state.projects.map((p) => {
    const threads = p.threadList.filter((t) => !needle || t.title.toLowerCase().includes(needle));
    if (needle && !threads.length && !p.name.toLowerCase().includes(needle)) return "";
    const active = route.project === p.id;
    const expanded = needle || open.has(p.id) || active;
    const busy = p.threadList.some((t) => t.running || t.waiting) || p.status === "cloning";
    return `<div class="side-proj ${expanded ? "open" : ""}">
      <div class="side-proj-row ${active && route.view === "project" ? "active" : ""}">
        <button type="button" class="side-chev" data-toggle="${p.id}" aria-label="${expanded ? "Zuklappen" : "Aufklappen"}"><span class="ms">chevron_right</span></button>
        <a class="side-proj-link" href="/p/${p.id}"><span class="ms proj-ic">${p.repo_url ? "folder_code" : "folder"}</span>
          <span class="tree-label">${esc(p.name)}</span>${busy ? `<span class="ms spin side-busy">progress_activity</span>` : `<span class="tree-count">${p.threadList.length || ""}</span>`}</a>
        <a class="icon-btn sm side-add" href="/p/${p.id}/neu" title="Neuer Thread in ${esc(p.name)}" aria-label="Neuer Thread"><span class="ms">add</span></a>
      </div>
      ${expanded ? `<div class="side-threads">${threads.map((t) => `<a class="side-thread ${route.thread === t.id ? "active" : ""} ${t.waiting ? "wait" : ""}" href="/p/${p.id}/t/${t.id}">
          <span class="ms ${t.running && !t.waiting ? "spin" : ""}">${threadIcon(t)}</span>
          <span class="tree-label">${esc(t.title)}</span><span class="tree-count">${t.waiting ? "Freigabe" : t.running ? "" : fmtRel(t.updated_at)}</span></a>`).join("")
        || `<div class="side-none">${p.status === "cloning" ? "wird geklont …" : "Noch keine Threads"}</div>`}</div>` : ""}
    </div>`;
  }).join("");
  box.innerHTML = html || `<div class="side-empty">${needle ? "Nichts gefunden." : "Noch keine Projekte."}</div>`;
  box.scrollTop = scroll;
  measureSide();
}

function drawFoot() {
  const gh = state.github;
  $("sideFoot").innerHTML = gh?.connected
    ? `<button type="button" class="foot-gh" data-act="github">${gh.avatar ? `<img src="${esc(gh.avatar)}" alt="">` : `<span class="ms">account_circle</span>`}<span>${esc(gh.login)}</span></button>
       <span class="foot-ok"><span class="ms">cloud_done</span>GitHub</span>`
    : `<button type="button" class="foot-gh" data-act="github"><span class="ms">link_off</span><span>GitHub verbinden</span></button>`;
  measureSide();
}

// Höhe von Kopf und Fuß der Seitenleiste für das Durchscrollen darunter
function measureSide() {
  const side = $("side");
  if (!side) return;
  side.style.setProperty("--sh", `${$("sideHead").offsetHeight}px`);
  side.style.setProperty("--sf", `${$("sideFoot").offsetHeight}px`);
}

function openDrawer() { if (desktop()) return; $("side").classList.add("open"); $("scrim").hidden = false; document.body.classList.add("drawer-open"); }
function closeDrawer() { $("side").classList.remove("open"); $("scrim").hidden = true; document.body.classList.remove("drawer-open"); }

// ---------- rechter Bereich ----------
// tab = offen zeigen (null = zu); am Handy/Tablet als Dialog
function setPane(tab) {
  if (!cur || cur.status !== "ready") return;
  if (!wide()) { if (tab) openPaneModal(cur.id, tab, { onChange: refreshProject }); return; }
  paneTab = tab || null;
  localStorage.setItem("code-pane", paneTab || "");
  drawPane(true);
  drawRail();
}

let paneFor = null; // { project, tab } – was rechts gerade gezeigt wird
function drawPane(force = false) {
  const pane = $("pane");
  const showIt = wide() && paneTab && cur?.status === "ready" && route.view !== "home";
  pane.hidden = !showIt;
  document.body.classList.toggle("pane-open", !!showIt);
  if (!showIt) { paneFor = null; return; }
  if (!force && paneFor?.project === cur.id && paneFor?.tab === paneTab) return;
  paneFor = { project: cur.id, tab: paneTab };
  pane.innerHTML = `${tabsHtml(paneTab, `<button type="button" class="icon-btn sm" data-pane-close title="Schließen (Strg+.)" aria-label="Bereich schließen"><span class="ms">right_panel_close</span></button>`)}<div class="pane-body" id="paneBody"></div>`;
  fillTab($("paneBody"), cur.id, paneTab, { onChange: refreshProject });
}

// git-Status des Projekts neu holen (Kopfzeile, Änderungen-Zähler, offener Änderungen-Tab)
let refreshing = false;
async function refreshProject() {
  if (!cur || refreshing) return;
  refreshing = true;
  try {
    const p = await api.get(`/code/projects/${cur.id}`);
    if (!cur || p.id !== cur.id) return;
    cur = p;
    drawRail();
    drawTopbarChips();
    if (paneTab === "changes") drawPane(true);
    if (route.view === "project") drawProjectInfo();
  } catch { /* egal */ } finally { refreshing = false; }
}

// ---------- Ansichten ----------
async function show(r) {
  closeDrawer();
  const leavingThread = route.view === "thread" && (r.view !== "thread" || r.thread !== route.thread || r.project !== route.project);
  if (leavingThread) { detach(); dropFiles(); }
  route = r;
  if (r.project) open.add(r.project);
  if (r.project && cur?.id !== r.project) cur = null;
  if (!r.project) cur = null;
  thread = null;
  drawRail();
  drawSide();
  drawPane();
  if (!state.boot) return;
  if (r.view === "home") return drawHome();
  if (!codeEnabled()) return drawHome();
  const stage = $("stage");
  if (!cur) stage.innerHTML = `<div class="empty-stage"><span class="ms spin big">progress_activity</span></div>`;
  try {
    const p = await api.get(`/code/projects/${encodeURIComponent(r.project)}`);
    if (route !== r) return;
    cur = p;
  } catch {
    toast("Projekt nicht gefunden");
    return navigate("/", { replace: true });
  }
  drawRail();
  drawPane();
  if (r.view === "project") return drawProject();
  if (cur.status !== "ready") { toast("Das Projekt ist noch nicht bereit"); return navigate(urlOf({ project: cur.id }), { replace: true }); }
  return drawThread(r);
}

const menuBtn = `<button type="button" class="icon-btn mobile-only" data-act="drawer" aria-label="Menü"><span class="ms">menu</span></button>`;

// ---- Projekte ----
function drawHome() {
  const stage = $("stage");
  document.title = "IDA Code";
  if (!codeEnabled()) {
    stage.innerHTML = `<div class="stage-topbar"><div class="topbar-left">${menuBtn}<h2><span class="ms">folder_code</span><span class="tb-title">Projekte</span></h2></div></div>
      <div class="stage-scroll"><div class="home-wrap"><div class="hero-box"><span class="ms big">code_off</span><h3>Der Code-Bereich ist ausgeschaltet</h3>
        <p>Hier arbeitet Ida direkt in deinen GitHub-Projekten – mit echten Datei- und Shell-Werkzeugen.</p>
        <button type="button" class="btn-primary" data-act="enable"><span class="ms">toggle_on</span>Einschalten</button></div></div></div>`;
    return;
  }
  const list = state.projects;
  const card = (p) => {
    const running = p.threadList.filter((t) => t.running).length, waiting = p.threadList.filter((t) => t.waiting).length;
    const status = p.status === "cloning" ? `<span class="c-run"><span class="ms spin">progress_activity</span>wird geklont …</span>`
      : p.status === "error" ? `<span class="c-err"><span class="ms">error</span>${esc((p.status_text || "Fehler").split("\n").pop())}</span>`
      : waiting ? `<span class="c-wait"><span class="ms">front_hand</span>${waiting} wartet auf Freigabe</span>`
      : running ? `<span class="c-run"><span class="ms spin">progress_activity</span>${running} Thread${running > 1 ? "s" : ""} arbeite${running > 1 ? "n" : "t"}</span>`
      : `<span>${p.threadList.length} Thread${p.threadList.length === 1 ? "" : "s"}</span>`;
    return `<a class="proj-card" href="/p/${p.id}"><span class="proj-card-ic"><span class="ms">${p.repo_url ? "folder_code" : "folder"}</span></span>
      <span class="proj-card-main"><b>${esc(p.name)}</b><small>${p.repo_url ? esc(repoName(p.repo_url)) : "nur lokal"}</small></span>
      <span class="proj-card-foot">${status}<span class="muted">${fmtRel(p.updated_at)}</span></span></a>`;
  };
  const gh = state.github;
  stage.innerHTML = `<div class="stage-topbar"><div class="topbar-left">${menuBtn}<h2><span class="ms">folder_code</span><span class="tb-title">Projekte</span></h2>
      ${list.length ? `<span class="count-pill">${list.length}</span>` : ""}</div>
      <div class="topbar-actions"><button type="button" class="btn-primary" data-act="add-project"><span class="ms">add</span><span class="hide-xs">Projekt hinzufügen</span></button></div></div>
    <div class="stage-scroll"><div class="home-wrap">
      ${list.length ? `<div class="proj-grid">${list.map(card).join("")}</div>`
        : state.loaded ? `<div class="hero-box"><span class="ms big">folder_code</span><h3>Noch keine Projekte</h3><p>Füge ein GitHub-Repo hinzu oder starte ein leeres Projekt – dann arbeitet Ida direkt darin.</p>
          <button type="button" class="btn-primary" data-act="add-project"><span class="ms">add</span>Projekt hinzufügen</button></div>`
        : `<div class="empty-stage"><span class="ms spin big">progress_activity</span></div>`}
      <div class="set-gl">GitHub</div>
      <div class="set-list"><button type="button" class="set-li" data-act="github">${gh?.connected && gh.avatar ? `<img class="gh-av" src="${esc(gh.avatar)}" alt="">` : `<span class="ms">${gh?.connected ? "account_circle" : "link"}</span>`}
        <span class="ct"><span>${gh?.connected ? `Angemeldet als ${esc(gh.login)}` : "Mit GitHub anmelden"}</span><small>${gh?.connected ? "Deine Repos, Push und Pull Requests sind bereit" : "Für deine Repos, Push und Pull Requests"}</small></span>
        <span class="ms">chevron_right</span></button></div>
    </div></div>`;
}
onChange(() => { if (route.view === "home" && $("stage") && !document.querySelector(".cd-backdrop")) drawHome(); });

async function addProject() {
  if (!codeEnabled()) return openSettings();
  const gh = await api.get("/github/status").catch(() => ({}));
  let tab = gh.connected ? "repos" : "url";
  const m = openModal(`${sheetHead("Projekt hinzufügen", "add")}
    <div class="seg-group three" id="apTabs">${[["repos", "Meine Repos"], ["new", "Neues Repo"], ["url", "Adresse / leer"]].map(([k, t]) => `<button type="button" class="seg" data-tab="${k}">${t}</button>`).join("")}</div>
    <div class="sheet-scroll" id="apBody"></div><div class="ferr" id="apErr"></div>`, { wide: true });
  const el = m.el;
  const err = (t) => { el.querySelector("#apErr").textContent = t; };
  const create = async (body, btn) => {
    if (btn) btn.disabled = true;
    err("");
    try {
      const p = await api.post("/code/projects", body);
      m.close();
      await loadTree().catch(() => {});
      navigate(urlOf({ project: p.id }));
    } catch (e) { err(e.message); if (btn) btn.disabled = false; }
  };
  const needGh = `<div class="hero-box small"><span class="ms">link</span><p>Dafür erst mit GitHub anmelden.</p><button type="button" class="btn-tonal" data-act="github">GitHub verbinden</button></div>`;
  const draw = () => {
    el.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    const body = el.querySelector("#apBody");
    err("");
    if (tab === "repos") {
      if (!gh.connected) { body.innerHTML = needGh; return; }
      body.innerHTML = `<label class="side-search"><span class="ms">search</span><input id="apQ" type="search" placeholder="Repos durchsuchen" autocomplete="off"></label><div id="apRepos" class="repo-list"><div class="side-empty">Lade …</div></div>`;
      let t;
      const load = async () => {
        const qv = el.querySelector("#apQ")?.value || "";
        try {
          const repos = await api.get(`/github/repos?q=${encodeURIComponent(qv)}`);
          const box = el.querySelector("#apRepos");
          if (!box) return;
          box.innerHTML = repos.length ? repos.map((r, i) => `<button type="button" class="set-li" data-i="${i}"><span class="ms">${r.private ? "lock" : "public"}</span>
            <span class="ct"><span>${esc(r.full_name)}</span><small>${esc(r.description || r.default_branch || "")}</small></span><span class="ms">download</span></button>`).join("") : `<div class="side-empty">Keine Repos gefunden.</div>`;
          box.querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => { const r = repos[+b.dataset.i]; create({ name: r.name, repo_url: r.clone_url }, b); }));
        } catch (e) { const box = el.querySelector("#apRepos"); if (box) box.innerHTML = `<div class="ferr">${esc(e.message)}</div>`; }
      };
      el.querySelector("#apQ").oninput = () => { clearTimeout(t); t = setTimeout(load, 300); };
      el.querySelector("#apQ").focus();
      load();
    } else if (tab === "new") {
      if (!gh.connected) { body.innerHTML = needGh; return; }
      body.innerHTML = `<form class="sheet-form" id="apNew"><label class="field-row"><span>Name des Repos (bei ${esc(gh.login)})</span><input class="cd-input" name="name" maxlength="100" placeholder="mein-projekt" autocomplete="off"></label>
        <label class="field-row"><span>Beschreibung (optional)</span><input class="cd-input" name="desc" maxlength="300" autocomplete="off"></label>
        <div class="sw-row"><span class="sw-txt"><b>Privat</b><small>Nur du (und wen du einlädst) siehst das Repo</small></span><button type="button" class="sw on" id="apPriv" aria-label="Privat"></button></div>
        <div class="sheet-actions"><button class="btn-primary" type="submit"><span class="ms">add</span>Repo erstellen</button></div></form>`;
      el.querySelector("#apPriv").onclick = (e) => e.currentTarget.classList.toggle("on");
      const f = el.querySelector("#apNew");
      f.elements.name.focus();
      f.onsubmit = (e) => {
        e.preventDefault();
        const name = f.elements.name.value.trim();
        if (!name) return err("Name fehlt");
        create({ new_repo: { name, private: el.querySelector("#apPriv").classList.contains("on"), description: f.elements.desc.value.trim() } }, e.submitter);
      };
    } else {
      body.innerHTML = `<form class="sheet-form" id="apUrl"><label class="field-row"><span>Repo-Adresse zum Klonen (https://…) – leer lassen für ein leeres Projekt</span><input class="cd-input" name="url" placeholder="https://github.com/name/repo" autocomplete="off"></label>
        <label class="field-row"><span>Name (optional bei Adresse)</span><input class="cd-input" name="name" maxlength="80" autocomplete="off"></label>
        <p class="sheet-hint">Private Repos klappen, wenn du mit GitHub angemeldet bist. Ein leeres Projekt kannst du später auf GitHub veröffentlichen.</p>
        <div class="sheet-actions"><button class="btn-primary" type="submit"><span class="ms">check</span>Hinzufügen</button></div></form>`;
      const f = el.querySelector("#apUrl");
      f.elements.url.focus();
      f.onsubmit = (e) => {
        e.preventDefault();
        const url = f.elements.url.value.trim(), name = f.elements.name.value.trim();
        if (!url && !name) return err("Adresse oder Name fehlt");
        create({ name, repo_url: url || null }, e.submitter);
      };
    }
  };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; draw(); }));
  draw();
}

// ---- Projektseite ----
function statusLine(p) {
  return p.status === "cloning" ? `<span class="c-run"><span class="ms spin">progress_activity</span>wird geklont … ${esc(p.status_text || "")}</span>`
    : p.status === "error" ? `<span class="c-err"><span class="ms">error</span>${esc((p.status_text || "Fehler").split("\n").pop())}</span>` : "";
}

function drawProject() {
  const p = cur;
  document.title = `${p.name} – IDA Code`;
  $("stage").innerHTML = `<div class="stage-topbar"><div class="topbar-left">${menuBtn}
      <a class="icon-btn hide-mobile" href="/" title="Alle Projekte" aria-label="Alle Projekte"><span class="ms">arrow_back</span></a>
      <h2><span class="ms">${p.repo_url ? "folder_code" : "folder"}</span><span class="tb-title">${esc(p.name)}</span></h2></div>
      <div class="topbar-actions">
        <a class="btn-primary ${p.status === "ready" ? "" : "disabled"}" href="/p/${p.id}/neu"><span class="ms">edit_square</span><span class="hide-xs">Neuer Thread</span></a>
        <button type="button" class="icon-btn" data-act="project-menu" title="Projekt" aria-label="Projekt-Menü"><span class="ms">more_vert</span></button></div></div>
    <div class="stage-scroll"><div class="proj-wrap">
      <div class="info-card" id="projInfo"></div>
      <div class="set-gl">Threads</div>
      <div id="projThreads"></div>
    </div></div>`;
  drawProjectInfo();
  drawProjectThreads();
}

function drawProjectInfo() {
  const box = $("projInfo");
  if (!box || !cur) return;
  const p = cur, g = p.git || {}, files = (g.files || []).length, ready = p.status === "ready";
  box.innerHTML = `${statusLine(p) ? `<div class="info-status">${statusLine(p)}</div>` : ""}
    <div class="kv"><span>Repo</span><b>${p.repo_url ? `<span class="mono">${esc(repoName(p.repo_url))}</span>` : "nur lokal – noch nicht auf GitHub"}</b></div>
    ${ready ? `<div class="kv"><span>Branch</span><b><span class="ms">alt_route</span>${esc(g.branch || p.default_branch)}${g.ahead ? ` · ${g.ahead} nicht gepusht` : ""}${g.behind ? ` · ${g.behind} neuer auf GitHub` : ""}</b></div>
    <div class="kv"><span>Änderungen</span><b>${files ? `${files} Datei${files === 1 ? "" : "en"} geändert` : "keine"}</b></div>` : ""}
    ${p.git_error ? `<div class="ferr">${esc(p.git_error)}</div>` : ""}
    ${ready ? `<div class="btn-row">
      <button type="button" class="${files ? "btn-primary" : "btn-tonal"}" data-pane="changes"><span class="ms">difference</span>${files ? "Änderungen ansehen" : "Git & Verlauf"}</button>
      <button type="button" class="btn-tonal" data-pane="files"><span class="ms">folder_open</span>Dateien</button>
      <button type="button" class="btn-tonal" data-pane="preview"><span class="ms">visibility</span>Vorschau</button>
      ${!p.repo_url ? `<button type="button" class="btn-tonal" data-act="publish"><span class="ms">publish</span>Auf GitHub veröffentlichen</button>` : ""}</div>` : ""}`;
}

function drawProjectThreads() {
  const box = $("projThreads");
  if (!box || !cur) return;
  const p = projectById(cur.id);
  const list = p?.threadList || [];
  box.innerHTML = list.length ? `<div class="thread-list">${list.map((t) => {
    const last = t.last ? (t.last.role === "user" ? "Du: " : "") + t.last.text.split("\n")[0] : "";
    return `<div class="thread-card ${t.waiting ? "wait" : ""}"><a class="thread-link" href="/p/${cur.id}/t/${t.id}">
        <span class="thread-ic"><span class="ms ${t.running && !t.waiting ? "spin" : ""}">${threadIcon(t)}</span></span>
        <span class="thread-main"><span class="thread-top"><b>${esc(t.title)}</b><span class="muted">${fmtRel(t.updated_at)}</span></span>
        <small>${t.waiting ? `<span class="c-wait">wartet auf deine Freigabe</span>` : t.running ? `<span class="c-run">arbeitet gerade …</span>` : esc(last)}</small></span></a>
      <button type="button" class="icon-btn sm" data-del-thread="${t.id}" title="Thread löschen" aria-label="Thread löschen"><span class="ms">delete</span></button></div>`;
  }).join("")}</div>`
    : cur.status === "ready" ? `<div class="hero-box small"><span class="ms">forum</span><p>Noch keine Threads. Starte einen – z. B. „Erklär mir den Aufbau des Projekts“.</p>
      <a class="btn-primary" href="/p/${cur.id}/neu"><span class="ms">edit_square</span>Neuer Thread</a></div>`
    : `<div class="side-empty">Threads gehen, sobald das Projekt bereit ist.</div>`;
}
onChange(() => { if (route.view === "project") drawProjectThreads(); if (route.view === "thread") drawThreadTitle(); });

function projectMenu(anchor) {
  const p = cur;
  popMenu(anchor, [
    { value: "rename", icon: "edit", label: "Umbenennen" },
    ...(!p.repo_url && p.status === "ready" ? [{ value: "publish", icon: "publish", label: "Auf GitHub veröffentlichen" }] : []),
    "-",
    { value: "delete", icon: "delete", label: "Projekt löschen", sub: "Ordner und alle Threads – das Repo auf GitHub bleibt", danger: true },
  ], async (v) => {
    if (v === "rename") {
      const r = await formDialog("Projekt umbenennen", [{ name: "name", label: "Name", value: p.name, required: true, max: 80 }], { icon: "edit" });
      if (r && await attempt(() => api.patch(`/code/projects/${p.id}`, { name: r.name }), "Umbenannt")) { await loadTree().catch(() => {}); show(route); }
    }
    if (v === "publish") publish();
    if (v === "delete" && await confirmDialog("Projekt löschen?", `„${p.name}“ mit allen Threads löschen? Nicht gepushte Änderungen gehen verloren. Das Repo auf GitHub bleibt.`, { ok: "Löschen", danger: true })) {
      if (await attempt(() => api.del(`/code/projects/${p.id}`), "Projekt gelöscht")) { await loadTree().catch(() => {}); navigate("/"); }
    }
  }, { align: "right" });
}

async function publish() {
  const r = await formDialog("Auf GitHub veröffentlichen", [
    { name: "name", label: "Name des neuen Repos", value: cur.name.toLowerCase().replace(/[^a-z0-9._-]+/g, "-"), required: true, max: 100 },
    { name: "private", label: "Privat", hint: "Nur du (und wen du einlädst) siehst das Repo", type: "switch", value: true },
  ], { ok: "Veröffentlichen", icon: "publish" });
  if (r && await attempt(() => api.post(`/code/projects/${cur.id}/publish`, { name: r.name, private: r.private }), "Auf GitHub veröffentlicht")) { await loadTree().catch(() => {}); show(route); }
}

// ---- Thread ----
async function drawThread(r) {
  const p = cur;
  const a = mainAgent();
  $("stage").innerHTML = `<div class="stage-topbar thread-bar"><div class="topbar-left">${menuBtn}
      <a class="icon-btn" href="/p/${p.id}" title="Zum Projekt" aria-label="Zum Projekt"><span class="ms">arrow_back</span></a>
      <div class="tb-stack"><b class="tb-title" id="thTitle">${r.thread ? "…" : "Neuer Thread"}</b><small class="tb-sub"><span>${esc(p.name)}</span><span class="ms">alt_route</span><span id="thBranch">${esc(p.git?.branch || p.default_branch)}</span></small></div></div>
      <div class="topbar-actions" id="thChips"></div></div>
    <div class="msgs" id="msgs"></div>
    <div class="composer-wrap">${composerHtml(`Was soll ${a?.name || "Ida"} in ${p.name} machen?`)}</div>`;
  bindComposer();
  drawTopbarChips();
  try {
    if (r.thread) {
      thread = await mountThread(p, r.thread);
      if (route !== r) return;
      drawThreadTitle();
    } else {
      mountNew(p, localStorage.getItem("code-mode") || "ask");
      drawTopbarChips();
    }
  } catch {
    toast("Thread nicht gefunden");
    return navigate(urlOf({ project: p.id }), { replace: true });
  }
  drawTopbarChips();
  refreshChip();
  if (desktop()) $("input")?.focus();
}

function drawThreadTitle() {
  if (route.view !== "thread" || !$("thTitle")) return;
  const t = projectById(route.project)?.threadList.find((x) => x.id === (ctx.id || route.thread));
  const title = t?.title || thread?.title || (ctx.id ? "Thread" : "Neuer Thread");
  $("thTitle").textContent = title;
  document.title = `${title} – IDA Code`;
}

function drawTopbarChips() {
  const box = $("thChips");
  if (!box || !cur) return;
  const files = (cur.git?.files || []).length;
  const m = modeOf(ctx.mode);
  box.innerHTML = `<button type="button" class="chip mode-chip mode-${m[0]}" data-act="mode" title="${esc(m[3])}"><span class="ms">${m[1]}</span><span class="hide-xs">${m[2]}</span></button>
    <button type="button" class="chip ${files ? "has" : ""}" data-pane="changes" title="Änderungen, Commit, Push, Pull Request"><span class="ms">difference</span><span class="hide-xs">${files ? `${files} Änderung${files === 1 ? "" : "en"}` : "Änderungen"}</span>${files ? `<b class="chip-n mobile-only">${files}</b>` : ""}</button>
    <button type="button" class="icon-btn" data-pane="files" title="Dateien & Vorschau (Strg+.)" aria-label="Dateien"><span class="ms">folder_open</span></button>
    ${ctx.id ? `<button type="button" class="icon-btn" data-act="thread-menu" title="Thread" aria-label="Thread-Menü"><span class="ms">more_vert</span></button>` : ""}`;
  const b = $("thBranch");
  if (b) b.textContent = cur.git?.branch || cur.default_branch;
}

function modeMenu(anchor) {
  popMenu(anchor, [{ head: "Rechte in diesem Thread" }, ...MODES.map(([id, ic, name, desc]) => ({ value: id, icon: ic, label: name, sub: desc, on: ctx.mode === id }))], async (mode) => {
    if (ctx.id) { if (await attempt(() => api.patch(`/code/threads/${ctx.id}`, { permission_mode: mode })) === undefined) return; }
    else localStorage.setItem("code-mode", mode);
    ctx.mode = mode;
    drawTopbarChips();
    toast(`${modeOf(mode)[2]} – gilt ab der nächsten Nachricht`);
  }, { align: "right" });
}

function threadMenu(anchor) {
  const id = ctx.id;
  if (!id) return;
  popMenu(anchor, [{ value: "rename", icon: "edit", label: "Umbenennen" }, "-", { value: "delete", icon: "delete", label: "Thread löschen", danger: true }], async (v) => {
    if (v === "rename") {
      const t = projectById(cur.id)?.threadList.find((x) => x.id === id);
      const r = await formDialog("Thread umbenennen", [{ name: "title", label: "Titel", value: t?.title || thread?.title || "", required: true, max: 120 }], { icon: "edit" });
      if (r && await attempt(() => api.patch(`/code/threads/${id}`, { title: r.title }), "Umbenannt")) { thread = { ...thread, title: r.title }; await loadTree().catch(() => {}); drawThreadTitle(); }
    }
    if (v === "delete") deleteThread(id);
  }, { align: "right" });
}

async function deleteThread(id) {
  if (!await confirmDialog("Thread löschen?", "Der Verlauf dieses Threads wird gelöscht. Änderungen im Projekt bleiben.", { ok: "Löschen", danger: true })) return;
  if (await attempt(() => api.del(`/chats/${encodeURIComponent(id)}`), "Thread gelöscht")) {
    await loadTree().catch(() => {});
    if (route.thread === id || ctx.id === id) navigate(urlOf({ project: cur.id }));
    else drawProjectThreads();
  }
}

// ---------- Klicks ----------
async function onClick(e) {
  const t = e.target;
  const act = t.closest("[data-act]");
  const paneBtn = t.closest("[data-pane]");
  if (t.closest("[data-pane-close]")) return setPane(null);
  const tabBtn = t.closest("#pane [data-pane-tab]");
  if (tabBtn) { paneTab = tabBtn.dataset.paneTab; localStorage.setItem("code-pane", paneTab); drawPane(true); drawRail(); return; }
  if (paneBtn && !paneBtn.disabled) {
    closeDrawer();
    const tab = paneBtn.dataset.pane;
    // Leiste links schaltet um (nochmal klicken = zu), Knöpfe im Inhalt öffnen nur
    return setPane(paneBtn.closest("#rail") && paneTab === tab && wide() ? null : tab);
  }
  const tog = t.closest("[data-toggle]");
  if (tog) { const id = tog.dataset.toggle; if (open.has(id) && route.project !== id) open.delete(id); else open.add(id); drawSide(); return; }
  const del = t.closest("[data-del-thread]");
  if (del) return deleteThread(del.dataset.delThread);
  if (t.closest("#sideList a")) closeDrawer();
  if (!act) return;
  switch (act.dataset.act) {
    case "home": return navigate("/");
    case "new": return route.project ? (cur?.status === "ready" ? navigate(urlOf({ project: route.project, thread: null })) : toast("Das Projekt ist noch nicht bereit")) : addProject();
    case "add-project": return addProject();
    case "settings": closeDrawer(); return openSettings();
    case "github": closeDrawer(); return openSettings("github");
    case "drawer": return openDrawer();
    case "drawer-close": return closeDrawer();
    case "enable":
      if (await attempt(() => api.put("/code/settings", { enabled: true }), "Code-Bereich an")) { await loadBoot(); await poll(); show(route); }
      return;
    case "project-menu": return projectMenu(act);
    case "publish": return publish();
    case "mode": return modeMenu(act);
    case "thread-menu": return threadMenu(act);
    default:
  }
}

