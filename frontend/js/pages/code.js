// "/code" – code area: projects (GitHub repos in data/projects), their threads, a thread itself.
//   /code                 projects + "Projekt hinzufügen" (your repos, new repo, URL, empty)
//   /code/:project        threads of the project, git state, changes
//   /code/:project/neu    new thread (chat view with the code bar)
//   /code/:project/:id    a thread (chat view with the code bar)
import { api } from "../api.js";
import { navigate } from "../router.js";
import { mainAgent, state } from "../store.js";
import { MODES } from "../ui/codebar.js";
import { $, attempt, closeSheet, enableSwipe, esc, fmtRel, openSheet, toast } from "../ui/dom.js";
import { setAgent, setView } from "../ui/shell.js";
import { newCodeThread, openChat } from "./chat.js";

let poll = null; // refreshes a project page while it is being cloned
const stopPoll = () => { clearTimeout(poll); poll = null; };

function page(html) {
  stopPoll();
  setAgent(mainAgent());
  setView("page");
  $("page").innerHTML = html;
}

function offBox() {
  return `<div class="page-head"><h1>Code</h1></div>
    <div class="soon-box"><span class="ms">code_off</span>Der Code-Bereich ist ausgeschaltet.<br>
    <small>Hier arbeitet Ida direkt in deinen GitHub-Projekten – mit echten Datei- und Shell-Werkzeugen.</small><br><br>
    <a class="es-btn primary" href="/einstellungen/code">In den Einstellungen einschalten</a></div>`;
}

const repoName = (url) => (url || "").replace(/^https?:\/\/[^/]+\//, "").replace(/^file:\/\/.*\//, "").replace(/\.git$/, "");
const statusLine = (p) => (p.status === "cloning" ? `<span class="c-running"><span class="ms spin">progress_activity</span>wird geklont … ${esc(p.status_text || "")}</span>`
  : p.status === "error" ? `<span class="c-err"><span class="ms">error</span>${esc((p.status_text || "Fehler").split("\n").pop())}</span>` : "");

// ---- /code ----
export async function codePage() {
  if (!state.boot?.code_enabled) return page(offBox());
  page(`<div class="page-head"><h1>Code</h1></div>
    <div id="ghLine"></div>
    <button class="big-new" id="addProject"><span class="ms">add</span>Projekt hinzufügen</button>
    <div id="projList"><div class="empty">Lade …</div></div>`);
  $("addProject").onclick = addProjectSheet;
  const [gh, list] = await Promise.all([api.get("/github/status"), api.get("/code/projects")]);
  if (!$("projList")) return;
  $("ghLine").innerHTML = gh.connected
    ? `<div class="gh-line"><img src="${esc(gh.avatar || "")}" alt=""><span>Angemeldet bei GitHub als <b>${esc(gh.login)}</b></span><a href="/einstellungen/github">ändern</a></div>`
    : `<a class="gh-line warn" href="/einstellungen/github"><span class="ms">link</span><span>Mit GitHub anmelden – für deine Repos, Push und Pull Requests</span><span class="ms">chevron_right</span></a>`;
  $("projList").innerHTML = list.length ? `<div class="clist">${list.map((p) => `<a class="crow" href="/code/${p.id}" data-id="${p.id}">
      <span class="ag-dot proj-dot"><span class="ms">${p.repo_url ? "folder_code" : "folder"}</span></span>
      <span class="cmain"><span class="ctop"><b>${esc(p.name)}</b><span class="ctime">${fmtRel(p.updated_at)}</span></span>
      <small>${statusLine(p) || (p.running ? `<span class="c-running"><span class="ms spin">progress_activity</span>${p.running} Thread${p.running > 1 ? "s" : ""} arbeite${p.running > 1 ? "n" : "t"} …</span>`
        : `${p.repo_url ? esc(repoName(p.repo_url)) : "nur lokal"} · ${p.threads} Thread${p.threads === 1 ? "" : "s"}`)}</small></span></a>`).join("")}</div>`
    : `<div class="empty">Noch keine Projekte. Füge ein GitHub-Repo hinzu oder starte ein leeres Projekt.</div>`;
  if (list.some((p) => p.status === "cloning")) poll = setTimeout(() => location.pathname === "/code" && codePage(), 2000);
}

// sheet: your repos | new repo | URL or empty
async function addProjectSheet() {
  let tab = "repos";
  const gh = await api.get("/github/status").catch(() => ({}));
  const s = openSheet("", "code-sheet");
  const create = async (body, btn) => {
    if (btn) btn.disabled = true;
    try {
      const p = await api.post("/code/projects", body);
      closeSheet();
      navigate(`/code/${p.id}`);
    } catch (e) { $("apErr").textContent = e.message; if (btn) btn.disabled = false; }
  };
  const draw = async () => {
    s.innerHTML = `<div class="handle"></div>
      <div class="cs-head"><h2 class="cs-title">Projekt hinzufügen</h2><button class="icon-btn" data-x aria-label="Schließen"><span class="ms">close</span></button></div>
      <div class="seg" id="apTabs">${[["repos", "Meine Repos"], ["new", "Neues Repo"], ["url", "Adresse / leer"]].map(([k, t]) => `<button type="button" data-tab="${k}" class="${tab === k ? "on" : ""}">${t}</button>`).join("")}</div>
      <div id="apBody"></div><div class="ferr" id="apErr"></div>`;
    s.querySelector("[data-x]").onclick = closeSheet;
    s.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; draw(); }));
    const body = $("apBody");
    const needGh = `<div class="rf-sum"><span class="ms">link</span><span>Dafür erst <a href="/einstellungen/github">mit GitHub anmelden</a>.</span></div>`;
    if (tab === "repos") {
      if (!gh.connected) { body.innerHTML = needGh; return; }
      body.innerHTML = `<div class="search"><span class="ms">search</span><input id="apQ" placeholder="Repos durchsuchen"></div><div id="apRepos"><div class="empty">Lade …</div></div>`;
      const load = async () => {
        try {
          const repos = await api.get(`/github/repos?q=${encodeURIComponent($("apQ")?.value || "")}`);
          $("apRepos").innerHTML = repos.length ? `<div class="slist">${repos.map((r, i) => `<button type="button" class="srow" data-i="${i}">
            <span class="sic"><span class="ms">${r.private ? "lock" : "public"}</span></span>
            <span class="smain"><b>${esc(r.full_name)}</b><small>${esc(r.description || r.default_branch)}</small></span><span class="ms schev">download</span></button>`).join("")}</div>`
            : `<div class="empty">Keine Repos gefunden.</div>`;
          $("apRepos").querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => { const r = repos[+b.dataset.i]; create({ name: r.name, repo_url: r.clone_url }, b); }));
        } catch (e) { $("apRepos").innerHTML = `<div class="ferr">${esc(e.message)}</div>`; }
      };
      let t;
      $("apQ").oninput = () => { clearTimeout(t); t = setTimeout(load, 300); };
      load();
    } else if (tab === "new") {
      if (!gh.connected) { body.innerHTML = needGh; return; }
      body.innerHTML = `<label class="fld"><span>Name des Repos (bei ${esc(gh.login)})</span><input id="apName" maxlength="100" placeholder="mein-projekt"></label>
        <label class="fld"><span>Beschreibung (optional)</span><input id="apDesc" maxlength="300"></label>
        <div class="srow"><span class="smain"><b>Privat</b><small>Nur du (und wen du einlädst) siehst das Repo</small></span><button type="button" class="sw on" id="apPriv"></button></div>
        <div class="wiz-nav"><button class="es-btn primary" id="apGo"><span class="ms">add</span>Repo erstellen</button></div>`;
      $("apPriv").onclick = () => $("apPriv").classList.toggle("on");
      $("apGo").onclick = () => {
        const name = $("apName").value.trim();
        if (!name) { $("apErr").textContent = "Name fehlt"; return; }
        create({ new_repo: { name, private: $("apPriv").classList.contains("on"), description: $("apDesc").value.trim() } }, $("apGo"));
      };
    } else {
      body.innerHTML = `<label class="fld"><span>Repo-Adresse zum Klonen (https://…) – leer = leeres Projekt</span><input id="apUrl" placeholder="https://github.com/name/repo"></label>
        <label class="fld"><span>Name (optional bei Adresse)</span><input id="apName" maxlength="80"></label>
        <div class="wiz-nav"><button class="es-btn primary" id="apGo"><span class="ms">check</span>Hinzufügen</button></div>
        <p class="hint">Private Repos klappen, wenn du mit GitHub angemeldet bist. Ein leeres Projekt kannst du später auf GitHub veröffentlichen.</p>`;
      $("apGo").onclick = () => {
        const url = $("apUrl").value.trim(), name = $("apName").value.trim();
        if (!url && !name) { $("apErr").textContent = "Adresse oder Name fehlt"; return; }
        create({ name, repo_url: url || null }, $("apGo"));
      };
    }
  };
  draw();
}

// ---- /code/:project ----
export async function projectPage({ project }) {
  if (!state.boot?.code_enabled) return page(offBox());
  let p;
  try { p = await api.get(`/code/projects/${encodeURIComponent(project)}`); } catch { toast("Projekt nicht gefunden"); return navigate("/code", { replace: true }); }
  page(`<div class="sub-head"><a class="icon-btn back" href="/code" aria-label="Zurück"><span class="ms">arrow_back</span></a><h1>${esc(p.name)}</h1>
      <button class="icon-btn" id="pjMenu" aria-label="Projekt-Einstellungen"><span class="ms">more_vert</span></button></div>
    <div class="pcard proj-card" id="pjCard"></div>
    <a class="big-new ${p.status === "ready" ? "" : "disabled"}" href="/code/${p.id}/neu"><span class="ms">edit_square</span>Neuer Thread</a>
    <div id="thList"></div>
    <p class="hint">Zum Löschen eine Zeile zur Seite wischen.</p>`);
  const g = p.git || {};
  const files = (g.files || []).length;
  $("pjCard").innerHTML = `${statusLine(p) ? `<div class="pj-status">${statusLine(p)}</div>` : ""}
    <div class="pj-row"><span class="ms">${p.repo_url ? "cloud" : "cloud_off"}</span>${p.repo_url ? `<span class="mono">${esc(repoName(p.repo_url))}</span>` : "nur lokal – noch kein GitHub-Repo"}</div>
    ${p.status === "ready" ? `<div class="pj-row"><span class="ms">alt_route</span><b>${esc(g.branch || p.default_branch)}</b>${g.ahead ? ` · ${g.ahead} Commit${g.ahead > 1 ? "s" : ""} nicht gepusht` : ""}${g.behind ? ` · ${g.behind} neuer auf GitHub` : ""}</div>
    <div class="es-actions" style="margin-top:12px"><button class="es-btn ${files ? "primary" : ""}" data-changes="${p.id}"><span class="ms">difference</span>${files ? `${files} Änderung${files === 1 ? "" : "en"}` : "Änderungen & Git"}</button></div>` : ""}
    ${p.git_error ? `<div class="ferr">${esc(p.git_error)}</div>` : ""}`;
  $("pjMenu").onclick = () => projectMenu(p);
  if (p.status === "cloning") { poll = setTimeout(() => location.pathname === `/code/${p.id}` && projectPage({ project: p.id }), 1500); return; }
  const list = await api.get(`/code/projects/${p.id}/threads`);
  if (!$("thList")) return;
  const icon = (m) => (MODES.find((x) => x[0] === m) || MODES[0])[1];
  $("thList").innerHTML = list.length ? `<div class="clist">${list.map((t) => {
    const last = t.last ? (t.last.role === "user" ? "Du: " : "") + t.last.text.split("\n")[0] : "";
    return `<a class="crow" href="/code/${p.id}/${t.id}" data-id="${t.id}">
      <span class="ag-dot proj-dot"><span class="ms">${icon(t.permission_mode)}</span></span>
      <span class="cmain"><span class="ctop"><b>${esc(t.title)}</b><span class="ctime">${fmtRel(t.updated_at)}</span></span>
      <small>${t.waiting ? `<span class="c-wait"><span class="ms">front_hand</span>wartet auf deine Freigabe</span>`
        : t.running ? `<span class="c-running"><span class="ms spin">progress_activity</span>arbeitet gerade …</span>` : esc(last)}</small></span></a>`;
  }).join("")}</div>` : `<div class="empty">Noch keine Threads. Starte einen – z. B. „Erklär mir den Aufbau des Projekts“.</div>`;
  $("thList").querySelectorAll(".crow").forEach((el) => enableSwipe(el, {
    onSwipe: async () => {
      if (!confirm("Thread löschen?")) { el.style.transform = ""; el.style.opacity = ""; return; }
      await attempt(() => api.del(`/chats/${el.dataset.id}`), "Thread gelöscht");
      projectPage({ project: p.id });
    },
  }));
  if (list.some((t) => t.running)) poll = setTimeout(() => location.pathname === `/code/${p.id}` && projectPage({ project: p.id }), 4000);
}

// commit/push/branch in the changes sheet: the project page shows the new state
document.addEventListener("code:changed", () => {
  const m = /^\/code\/([^/]+)$/.exec(location.pathname);
  if (m) projectPage({ project: m[1] });
});

function projectMenu(p) {
  const s = openSheet(`<h2 class="cs-title">${esc(p.name)}</h2>
    <div class="slist">
      <button type="button" class="srow" data-a="rename"><span class="sic"><span class="ms">edit</span></span><span class="smain"><b>Umbenennen</b></span></button>
      ${!p.repo_url && p.status === "ready" ? `<button type="button" class="srow" data-a="publish"><span class="sic"><span class="ms">publish</span></span><span class="smain"><b>Auf GitHub veröffentlichen</b><small>Neues Repo erstellen und hochladen</small></span></button>` : ""}
      <button type="button" class="srow" data-a="delete"><span class="sic"><span class="ms">delete</span></span><span class="smain"><b>Projekt löschen</b><small>Ordner und alle Threads hier – das Repo auf GitHub bleibt</small></span></button>
    </div>`);
  s.onclick = async (e) => {
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (!a) return;
    closeSheet();
    if (a === "rename") {
      const name = prompt("Neuer Name:", p.name);
      if (name && await attempt(() => api.patch(`/code/projects/${p.id}`, { name }), "Umbenannt")) projectPage({ project: p.id });
    }
    if (a === "publish") {
      const name = prompt("Name des neuen GitHub-Repos:", p.name.toLowerCase().replace(/[^a-z0-9._-]+/g, "-"));
      if (name === null) return;
      const priv = confirm("Privates Repo? (OK = privat, Abbrechen = öffentlich)");
      if (await attempt(() => api.post(`/code/projects/${p.id}/publish`, { name, private: priv }), "Auf GitHub veröffentlicht")) projectPage({ project: p.id });
    }
    if (a === "delete" && confirm(`Projekt „${p.name}“ mit allen Threads löschen? Nicht gepushte Änderungen gehen verloren.`)) {
      if (await attempt(() => api.del(`/code/projects/${p.id}`), "Projekt gelöscht")) navigate("/code");
    }
  };
}

// ---- /code/:project/neu and /code/:project/:thread ----
export async function threadPage({ project, thread }) {
  stopPoll();
  if (!state.boot?.code_enabled) return page(offBox());
  if (thread === "neu") {
    let p;
    try { p = await api.get(`/code/projects/${encodeURIComponent(project)}`); } catch { toast("Projekt nicht gefunden"); return navigate("/code", { replace: true }); }
    if (p.status !== "ready") { toast("Das Projekt ist noch nicht bereit"); return navigate(`/code/${p.id}`, { replace: true }); }
    return newCodeThread(p, "ask");
  }
  try { await openChat(thread); } catch { toast("Thread nicht gefunden"); navigate(`/code/${project}`, { replace: true }); }
}
