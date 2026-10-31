// Rechter Bereich eines Projekts: Dateien (Explorer + Ansicht), Änderungen (Diff, Commit, Push, Branch, PR).
// Am PC als eigene Insel rechts, am Handy/Tablet als großer Dialog mit denselben Tabs.
import { api, rawUrl } from "./api.js";
import { esc, fmtBytes, fmtRel, formDialog, md, openModal, popMenu, toast } from "./ui.js";

export const TABS = [["files", "folder_open", "Dateien"], ["changes", "difference", "Änderungen"]];

export function tabsHtml(tab, extra = "") {
  return `<div class="pane-tabs" role="tablist">${TABS.map(([k, ic, t]) => `<button type="button" role="tab" data-pane-tab="${k}" class="${tab === k ? "on" : ""}">
    <span class="ms">${ic}</span><span>${t}</span></button>`).join("")}${extra}</div>`;
}

// Inhalt eines Tabs in el zeichnen. isCurrent(el) sagt, ob el noch angezeigt wird (sonst nichts mehr hineinschreiben).
export function fillTab(el, projectId, tab, { onChange } = {}) {
  const token = (el.dataset.tok = String(Math.random()));
  const isCurrent = () => el.isConnected && el.dataset.tok === token;
  if (tab === "files") mountFiles(el, projectId, isCurrent);
  else renderChanges(el, projectId, { onChange, isCurrent });
}

// ---- Dateien ----
function fileIcon(name, isDir) {
  if (isDir) return ["folder", "ic-folder"];
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["html", "htm"].includes(ext)) return ["html", "ic-html"];
  if (["js", "mjs", "cjs", "ts", "tsx", "jsx"].includes(ext)) return ["javascript", "ic-js"];
  if (ext === "py") return ["terminal", "ic-py"];
  if (["css", "scss", "sass", "less"].includes(ext)) return ["css", "ic-css"];
  if (["json", "toml", "yaml", "yml", "ini", "cfg", "sql"].includes(ext)) return ["data_object", "ic-data"];
  if (["md", "markdown", "txt", "rst"].includes(ext)) return ["description", "ic-doc"];
  if (["png", "jpg", "jpeg", "gif", "svg", "webp", "ico"].includes(ext)) return ["image", "ic-img"];
  if (["sh", "bash", "bat", "ps1"].includes(ext)) return ["terminal", "ic-sh"];
  return ["draft", "ic-file"];
}

const lines = (src) => (src ? src.split("\n").map((l, i) => `<span class="ln">${i + 1}</span><span class="lc">${esc(l) || " "}</span>`).join("\n") : `<span class="muted">(leere Datei)</span>`);

function mountFiles(el, projectId, isCurrent) {
  let cur = "", q = "", dir = null;
  let htmlTab = "render", mdTab = "render", device = "full";

  const loadDir = async (path = "") => {
    cur = path;
    el.innerHTML = `<div class="pane-loading"><span class="ms spin">progress_activity</span>Lade Dateien …</div>`;
    try { dir = await api.get(`/code/projects/${projectId}/files?path=${encodeURIComponent(path)}`); }
    catch (e) { if (isCurrent()) el.innerHTML = `<div class="pane-box"><div class="ferr">${esc(e.message)}</div></div>`; return; }
    if (isCurrent()) drawDir();
  };

  const drawDir = () => {
    const parts = cur ? cur.split("/").filter(Boolean) : [];
    let acc = "";
    const crumbs = [`<button type="button" class="fe-crumb" data-cd=""><span class="ms">home</span></button>`].concat(parts.map((p) => {
      acc += (acc ? "/" : "") + p;
      return `<span class="fe-sep">/</span><button type="button" class="fe-crumb" data-cd="${esc(acc)}">${esc(p)}</button>`;
    })).join("");
    const needle = q.toLowerCase().trim();
    const list = dir.entries.filter((e) => !needle || e.name.toLowerCase().includes(needle));
    el.innerHTML = `<div class="pane-box">
      <div class="fe-bar"><div class="fe-crumbs">${crumbs}</div>
        ${dir.parent !== null ? `<button type="button" class="icon-btn sm" data-up title="Ebene nach oben"><span class="ms">arrow_upward</span></button>` : ""}
        <button type="button" class="icon-btn sm" data-reload title="Neu laden"><span class="ms">refresh</span></button></div>
      <label class="pane-search"><span class="ms">search</span><input type="search" placeholder="In diesem Ordner filtern" value="${esc(q)}"></label>
      <div class="fe-list">${list.length ? list.map((e) => {
        const [ic, cls] = fileIcon(e.name, e.is_dir);
        return `<button type="button" class="fe-row" data-path="${esc(e.path)}" data-dir="${e.is_dir ? 1 : 0}">
          <span class="fe-ic ${cls}"><span class="ms">${ic}</span></span>
          <span class="fe-main"><span class="fe-name">${esc(e.name)}</span><small>${e.is_dir ? "Ordner" : `${fmtBytes(e.size)}${e.modified_at ? ` · ${fmtRel(e.modified_at)}` : ""}`}</small></span>
          <span class="ms fe-arrow">${e.is_dir ? "chevron_right" : ""}</span></button>`;
      }).join("") : `<div class="pane-empty">${q ? "Keine Treffer." : "Dieser Ordner ist leer."}</div>`}</div></div>`;
    el.querySelectorAll("[data-cd]").forEach((b) => (b.onclick = () => loadDir(b.dataset.cd)));
    el.querySelector("[data-up]")?.addEventListener("click", () => loadDir(dir.parent || ""));
    el.querySelector("[data-reload]").onclick = () => loadDir(cur);
    const inp = el.querySelector(".pane-search input");
    inp.oninput = () => { q = inp.value; const pos = inp.selectionStart; drawDir(); const n = el.querySelector(".pane-search input"); n.focus(); n.setSelectionRange(pos, pos); };
    el.querySelectorAll(".fe-row").forEach((r) => (r.onclick = () => (r.dataset.dir === "1" ? loadDir(r.dataset.path) : loadFile(r.dataset.path))));
  };

  const loadFile = async (path) => {
    el.innerHTML = `<div class="pane-loading"><span class="ms spin">progress_activity</span>Lade ${esc(path.split("/").pop())} …</div>`;
    let data;
    try { data = await api.get(`/code/projects/${projectId}/file?path=${encodeURIComponent(path)}`); }
    catch (e) { if (isCurrent()) { el.innerHTML = `<div class="pane-box"><div class="ferr">${esc(e.message)}</div><button type="button" class="btn-tonal" data-back><span class="ms">arrow_back</span>Zurück</button></div>`; el.querySelector("[data-back]").onclick = () => loadDir(cur); } return; }
    if (isCurrent()) drawFile(data);
  };

  const drawFile = (data) => {
    const isHtml = [".html", ".htm"].includes(data.ext);
    const isMd = [".md", ".markdown"].includes(data.ext);
    const isImg = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"].includes(data.ext);
    const raw = rawUrl(projectId, data.path);
    const seg = (key, val, opts) => `<div class="seg-mini">${opts.map(([v, ic, t]) => `<button type="button" class="${val === v ? "on" : ""}" data-${key}="${v}"><span class="ms">${ic}</span>${t}</button>`).join("")}</div>`;
    let tools = "";
    if (isHtml) {
      tools = seg("ht", htmlTab, [["render", "visibility", "Vorschau"], ["code", "code", "Code"]]);
      if (htmlTab === "render") tools += `<div class="dev-row">${[["full", "laptop", "Volle Breite"], ["tablet", "tablet", "Tablet"], ["mobile", "smartphone", "Handy"]].map(([d, ic, t]) => `<button type="button" class="icon-btn sm ${device === d ? "active" : ""}" data-dev="${d}" title="${t}"><span class="ms">${ic}</span></button>`).join("")}</div>`;
    } else if (isMd) tools = seg("mdt", mdTab, [["render", "visibility", "Vorschau"], ["code", "code", "Quelltext"]]);
    if (data.is_text) tools += `<button type="button" class="icon-btn sm" data-copy title="Inhalt kopieren"><span class="ms">content_copy</span></button>`;
    tools += `<a class="icon-btn sm" href="${raw}" target="_blank" rel="noopener" title="In neuem Tab öffnen"><span class="ms">open_in_new</span></a>`;

    let body;
    if (isHtml && htmlTab === "render") body = `<div class="pv-frame-wrap dev-${device}"><iframe class="pv-frame" src="${raw}" sandbox="allow-scripts allow-forms allow-popups"></iframe></div>`;
    else if (isMd && mdTab === "render") body = `<div class="pv-md md">${md(data.content || "")}</div>`;
    else if (isImg) body = `<div class="pv-img"><img src="${raw}" alt="${esc(data.name)}"></div>`;
    else if (data.is_text) body = `${data.truncated ? `<p class="sheet-hint">Sehr große Datei – die Ansicht ist gekürzt.</p>` : ""}<pre class="code-view">${lines(data.content)}</pre>`;
    else body = `<div class="pane-empty"><span class="ms big">inventory_2</span><b>${esc(data.name)}</b><br><small>Binärdatei · ${fmtBytes(data.size)}</small><br><br>
      <a class="btn-primary" href="${raw}" download="${esc(data.name)}"><span class="ms">download</span>Herunterladen</a></div>`;

    el.innerHTML = `<div class="pane-box file-mode">
      <div class="fv-head"><button type="button" class="icon-btn sm" data-back title="Zurück zum Ordner"><span class="ms">arrow_back</span></button>
        <div class="fv-meta"><b title="${esc(data.path)}">${esc(data.name)}</b><small>${esc(data.path)} · ${fmtBytes(data.size)}</small></div></div>
      <div class="fv-tools">${tools}</div>
      <div class="fv-body">${body}</div></div>`;
    el.querySelector("[data-back]").onclick = () => loadDir(cur);
    el.querySelectorAll("[data-ht]").forEach((b) => (b.onclick = () => { htmlTab = b.dataset.ht; drawFile(data); }));
    el.querySelectorAll("[data-mdt]").forEach((b) => (b.onclick = () => { mdTab = b.dataset.mdt; drawFile(data); }));
    el.querySelectorAll("[data-dev]").forEach((b) => (b.onclick = () => { device = b.dataset.dev; drawFile(data); }));
    el.querySelector("[data-copy]")?.addEventListener("click", () => navigator.clipboard?.writeText(data.content || "").then(() => toast("Kopiert"), () => toast("Kopieren nicht möglich")));
  };

  loadDir("");
}

// ---- Änderungen ----
function diffHtml(text) {
  if (!text.trim()) return `<div class="pane-empty"><span class="ms big">check_circle</span>Keine Änderungen seit dem letzten Commit.</div>`;
  return `<pre class="diff">${text.split("\n").map((l) => {
    const c = l.startsWith("diff --git") ? "file" : l.startsWith("+++") || l.startsWith("---") ? "meta" : l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
    return `<i class="${c}">${esc(l)}</i>`;
  }).join("")}</pre>`;
}

export async function renderChanges(el, projectId, { onChange, isCurrent = () => el.isConnected } = {}) {
  let file = null;
  const draw = async () => {
    let st, d;
    try {
      [st, d] = await Promise.all([api.get(`/code/projects/${projectId}/git`), api.get(`/code/projects/${projectId}/diff${file ? `?file=${encodeURIComponent(file)}` : ""}`)]);
    } catch (e) { if (isCurrent()) el.innerHTML = `<div class="pane-box"><div class="ferr">${esc(e.message)}</div></div>`; return; }
    if (!isCurrent()) return;
    const remote = st.remote ? st.remote.replace(/^https?:\/\//, "").replace(/\.git$/, "") : "";
    el.innerHTML = `<div class="pane-box">
      <div class="git-info">
        <span><span class="ms">alt_route</span><b>${esc(st.branch)}</b>${st.upstream ? ` → ${esc(st.upstream)}` : ""}</span>
        ${st.ahead ? `<span class="pill warn"><span class="ms">arrow_upward</span>${st.ahead} nicht gepusht</span>` : ""}
        ${st.behind ? `<span class="pill"><span class="ms">arrow_downward</span>${st.behind} neuer auf GitHub</span>` : ""}
        <span>${remote ? `<span class="ms">cloud</span>${esc(remote)}` : `<span class="ms">cloud_off</span>nur lokal`}</span>
        <span>${st.github ? `<span class="ms">account_circle</span>${esc(st.github)}` : `<span class="ms">link_off</span>GitHub nicht verbunden`}</span>
      </div>
      ${st.files.length ? `<div class="cs-files">${st.files.map((f) => { const c = f.code === "?" ? "A" : f.code; return `<button type="button" class="cs-file ${file === f.path ? "on" : ""}" data-file="${esc(f.path)}"><b class="st-${esc(c)}">${esc(c)}</b><span>${esc(f.path)}</span></button>`; }).join("")}
        ${file ? `<button type="button" class="cs-file" data-file=""><span class="ms">select_all</span><span>Alle</span></button>` : ""}</div>` : ""}
      ${diffHtml(d.diff)}${d.truncated ? `<p class="sheet-hint">Gekürzt – zu viele Änderungen für die Anzeige.</p>` : ""}
      ${st.files.length ? `<form class="commit-box" data-commit><input class="cd-input" name="msg" maxlength="300" placeholder="Commit-Nachricht – was wurde geändert?" autocomplete="off">
        <div class="btn-row"><button class="btn-primary" type="submit" data-how="commit"><span class="ms">commit</span>Commit</button>
        ${st.remote ? `<button class="btn-tonal" type="submit" data-how="push"><span class="ms">cloud_upload</span>Commit &amp; Push</button>` : ""}</div></form>` : ""}
      <div class="btn-row">
        ${st.remote && st.has_commits && (st.ahead || !st.upstream) ? `<button type="button" class="btn-tonal" data-act="push"><span class="ms">cloud_upload</span>Push</button>` : ""}
        ${!st.remote ? `<button type="button" class="btn-tonal" data-act="publish"><span class="ms">publish</span>Auf GitHub veröffentlichen</button>` : ""}
        <button type="button" class="btn-tonal" data-act="branch"><span class="ms">alt_route</span>Branch</button>
        ${st.remote ? `<button type="button" class="btn-tonal" data-act="pr"><span class="ms">merge</span>Pull Request</button>` : ""}
      </div><div class="ferr" data-err></div></div>`;
    const err = el.querySelector("[data-err]");
    const run = async (btn, fn, ok) => {
      if (btn) btn.disabled = true;
      err.textContent = "";
      try { const r = await fn(); if (ok) toast(typeof ok === "function" ? ok(r) : ok); onChange?.(); await draw(); return r; }
      catch (e) { err.textContent = e.message; if (btn) btn.disabled = false; return undefined; }
    };
    el.querySelectorAll("[data-file]").forEach((b) => (b.onclick = () => { file = b.dataset.file || null; draw(); }));
    const form = el.querySelector("[data-commit]");
    if (form) form.onsubmit = (e) => {
      e.preventDefault();
      const message = form.elements.msg.value.trim();
      if (!message) { err.textContent = "Commit-Nachricht fehlt"; form.elements.msg.focus(); return; }
      const push = e.submitter?.dataset.how === "push";
      run(e.submitter, () => api.post(`/code/projects/${projectId}/commit`, { message, push }), (r) => `Committet (${r.sha})${r.pushed ? " und gepusht" : ""}`);
    };
    el.querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
      const act = b.dataset.act;
      if (act === "push") return run(b, () => api.post(`/code/projects/${projectId}/push`), "Gepusht");
      if (act === "publish") {
        const v = await formDialog("Auf GitHub veröffentlichen", [{ name: "name", label: "Name des neuen Repos", required: true, max: 100 }, { name: "private", label: "Privat", hint: "Nur du (und wen du einlädst) siehst das Repo", type: "switch", value: true }], { ok: "Veröffentlichen", icon: "publish" });
        if (v) run(b, () => api.post(`/code/projects/${projectId}/publish`, { name: v.name, private: v.private }), "Auf GitHub veröffentlicht");
        return;
      }
      if (act === "branch") {
        const items = [{ value: "+new", icon: "add", label: "Neuer Branch …" }];
        if (st.branches.length > 1) items.push("-", { head: "Wechseln zu" }, ...st.branches.map((x) => ({ value: x, icon: "alt_route", label: x, on: x === st.branch })));
        popMenu(b, items, async (v) => {
          if (v === "+new") {
            const r = await formDialog("Neuer Branch", [{ name: "name", label: "Name", value: "ida/", placeholder: "ida/neues-feature", required: true, max: 120 }], { ok: "Anlegen", icon: "alt_route" });
            if (r) run(b, () => api.post(`/code/projects/${projectId}/branch`, { name: r.name, create: true }), `Auf ${r.name}`);
          } else if (v !== st.branch) run(b, () => api.post(`/code/projects/${projectId}/branch`, { name: v, create: false }), `Auf ${v}`);
        });
        return;
      }
      if (act === "pr") {
        const v = await formDialog("Pull Request", [{ name: "title", label: "Titel", required: true, max: 200 }, { name: "body", label: "Beschreibung (optional)", type: "textarea" }], { ok: "Erstellen", icon: "merge" });
        if (!v) return;
        const r = await run(b, () => api.post(`/code/projects/${projectId}/pr`, { title: v.title, body: v.body }), (x) => `Pull Request #${x.number} erstellt`);
        if (r?.url) window.open(r.url, "_blank", "noopener");
      }
    }));
  };
  el.innerHTML = `<div class="pane-loading"><span class="ms spin">progress_activity</span>Lade Änderungen …</div>`;
  await draw();
}

// Tabs als Dialog (Handy/Tablet oder wenn die Insel rechts zu ist)
export function openPaneModal(projectId, tab = "files", { onChange } = {}) {
  const m = openModal(`<div class="pane-modal">${tabsHtml(tab, `<button type="button" class="icon-btn" data-close aria-label="Schließen"><span class="ms">close</span></button>`)}<div class="pane-body" data-body></div></div>`, { wide: true, tall: true });
  const show = (t) => {
    m.el.querySelectorAll("[data-pane-tab]").forEach((b) => b.classList.toggle("on", b.dataset.paneTab === t));
    fillTab(m.el.querySelector("[data-body]"), projectId, t, { onChange });
  };
  m.el.querySelectorAll("[data-pane-tab]").forEach((b) => (b.onclick = () => show(b.dataset.paneTab)));
  show(tab);
  return m;
}
