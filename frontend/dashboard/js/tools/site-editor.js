// Website-Editor (Tool Websites): derselbe Monaco-Editor wie in IDA HTML – Explorer, Tabs, Snippets, Formatieren, Live-Vorschau.
// Arbeitet direkt auf den Dateien der Website (Tool-Dateien-API). Monaco lädt erst beim ersten Öffnen (/vendor/monaco).
import { api, ApiError } from "../api.js";
import { confirmDialog, esc, fmtBytes, popMenu, toast } from "../ui.js";
import { formDialog } from "../form.js";
import { upload } from "./files-upload.js";
import { CSS_SNIPPETS, HTML_SNIPPETS, JS_SNIPPETS } from "./site-snippets.js";

const MONACO = "/vendor/monaco/vs";
const LANGS = { html: "html", htm: "html", css: "css", scss: "scss", less: "less", js: "javascript", mjs: "javascript", cjs: "javascript", ts: "typescript",
  json: "json", webmanifest: "json", md: "markdown", markdown: "markdown", xml: "xml", svg: "xml", yml: "yaml", yaml: "yaml", py: "python", php: "php",
  sh: "shell", txt: "plaintext", conf: "ini", ini: "ini", toml: "ini", vue: "html", jsx: "javascript", tsx: "typescript" };
const TEXT_EXT = new Set([...Object.keys(LANGS), "csv", "log", "env", "gitignore", "htaccess", "map"]);
const ICONS = { html: ["code", "#e5734f"], css: ["palette", "#4f9fe5"], scss: ["palette", "#d46fa6"], js: ["javascript", "#e5c34f"], mjs: ["javascript", "#e5c34f"],
  ts: ["javascript", "#4f8fe5"], json: ["data_object", "#e5c34f"], md: ["article", "#9aa0a6"], svg: ["image", "#e5a04f"], png: ["image", "#7fc98f"], jpg: ["image", "#7fc98f"],
  jpeg: ["image", "#7fc98f"], gif: ["image", "#7fc98f"], webp: ["image", "#7fc98f"], ico: ["image", "#7fc98f"], woff: ["font_download", "#9aa0a6"], woff2: ["font_download", "#9aa0a6"],
  ttf: ["font_download", "#9aa0a6"], pdf: ["picture_as_pdf", "#f28b82"], mp4: ["movie", "#b18cff"], mp3: ["music_note", "#b18cff"], py: ["terminal", "#4fa3e5"] };
const DEVICES = { desktop: ["desktop_windows", "Desktop", ""], tablet: ["tablet", "Tablet", "768px"], mobile: ["smartphone", "Handy", "390px"] };
const ext = (p) => (p.split("/").pop().match(/\.([^.]+)$/)?.[1] || "").toLowerCase();
const isText = (p) => TEXT_EXT.has(ext(p)) || !/\./.test(p.split("/").pop());
const pref = (k, d) => localStorage.getItem("se-" + k) ?? d;
const setPref = (k, v) => localStorage.setItem("se-" + k, v);

let monacoReady = null;
function loadMonaco() {
  if (monacoReady) return monacoReady;
  monacoReady = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `${MONACO}/editor/editor.main.css`;
    document.head.appendChild(css);
    const s = document.createElement("script");
    s.src = `${MONACO}/loader.js`;
    s.onerror = () => { monacoReady = null; reject(new Error("Der Editor lässt sich nicht laden")); };
    s.onload = () => {
      window.require.config({ paths: { vs: MONACO } });
      window.require(["vs/editor/editor.main"], () => { setup(window.monaco); resolve(window.monaco); }, reject);
    };
    document.head.appendChild(s);
  });
  return monacoReady;
}

// Theme „IDA (Standard)“ und Snippets wie in IDA HTML
let filesForHints = () => [];
function setup(monaco) {
  const bg0 = "#000000", bg2 = "#1d1f23", line = "#1d1f23", line2 = "#282a2f", text = "#e3e2e6", accent = "#78d9ec";
  monaco.editor.defineTheme("ida-html", {
    base: "vs-dark", inherit: true,
    rules: [
      { token: "comment", foreground: "6e7689", fontStyle: "italic" }, { token: "keyword", foreground: "ff85b8" }, { token: "string", foreground: "f4cf6e" },
      { token: "number", foreground: "b18cff" }, { token: "tag", foreground: "1ad6cf" }, { token: "attribute.name", foreground: "6aa8ff" },
      { token: "attribute.value", foreground: "5fd1a0" },
    ],
    colors: {
      "editor.background": bg0, "editor.foreground": text, "editor.lineHighlightBackground": "#080808", "editorLineNumber.foreground": "#e3e2e673",
      "editorLineNumber.activeForeground": accent, "editor.selectionBackground": "#78d9ec40", "editorCursor.foreground": accent,
      "editorIndentGuide.background": line, "editorIndentGuide.activeBackground": accent, "editorGutter.background": bg0, "editorWidget.background": bg2,
      "editorWidget.border": line2, "editorSuggestWidget.background": bg2, "editorSuggestWidget.border": line2, "editorSuggestWidget.selectedBackground": "#78d9ec4d",
      "scrollbar.shadow": "#00000000",
    },
  });
  const snippets = (language, list) => ({
    provideCompletionItems(model, pos) {
      const before = model.getLineContent(pos.lineNumber).slice(0, pos.column - 1);
      if (language === "css" && /^\s*[\w-]+\s*:/.test(before)) return { suggestions: [] };
      const word = model.getWordUntilPosition(pos);
      if (!word.word) return { suggestions: [] };
      const lt = word.startColumn > 1 && before[word.startColumn - 2] === "<";
      return {
        suggestions: list.map(([label, detail, body]) => ({
          label: { label, description: detail }, kind: monaco.languages.CompletionItemKind.Snippet, insertText: body,
          insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, detail, sortText: "0_" + label,
          range: new monaco.Range(pos.lineNumber, word.startColumn - (lt && body.startsWith("<") ? 1 : 0), pos.lineNumber, word.endColumn),
        })),
      };
    },
  });
  monaco.languages.registerCompletionItemProvider("html", snippets("html", HTML_SNIPPETS));
  monaco.languages.registerCompletionItemProvider("css", snippets("css", CSS_SNIPPETS));
  monaco.languages.registerCompletionItemProvider("javascript", snippets("javascript", JS_SNIPPETS));
  // href="…" / src="…": Dateien der Website vorschlagen (relativ zur offenen Datei)
  const fileHints = {
    triggerCharacters: ['"', "'", "/"],
    provideCompletionItems(model, pos) {
      const before = model.getLineContent(pos.lineNumber).slice(0, pos.column - 1);
      const m = /(href|src|url\()\s*=?\s*["']?([^"')]*)$/i.exec(before);
      if (!m) return { suggestions: [] };
      const { current, all } = filesForHints();
      const from = current.split("/").slice(0, -1);
      return {
        suggestions: all.filter((f) => f !== current).map((f) => {
          const to = f.split("/"), file = to.pop();
          let c = 0;
          while (c < from.length && c < to.length && from[c] === to[c]) c++;
          const rel = [...Array(from.length - c).fill(".."), ...to.slice(c), file].join("/");
          return { label: rel, kind: monaco.languages.CompletionItemKind.File, insertText: rel, detail: f, sortText: String(from.length - c) + rel,
            range: new monaco.Range(pos.lineNumber, pos.column - m[2].length, pos.lineNumber, pos.column) };
        }),
      };
    },
  };
  for (const l of ["html", "css", "javascript"]) monaco.languages.registerCompletionItemProvider(l, fileHints);
}

// site: Eintrag aus /api/v1/sites (folder, root, domains …)
export async function openSiteEditor(site, { file } = {}) {
  const base = site.folder;                                   // Ordner der Website
  const served = site.root ? `${base}/${site.root}` : base;   // davon wird ausgeliefert
  const url = site.url.replace(/\/$/, "");
  const box = document.createElement("div");
  box.className = "se";
  box.innerHTML = `<div class="se-bar">
      <button type="button" class="icon-btn" data-a="close" aria-label="Schließen" title="Schließen"><span class="ms">arrow_back</span></button>
      <button type="button" class="icon-btn se-only-narrow" data-a="tree" aria-label="Explorer" title="Explorer"><span class="ms">left_panel_open</span></button>
      <div class="fe-title"><b><span>${esc(site.name)}</span></b><small class="mono">${esc(url.replace(/^https:\/\//, ""))}</small></div>
      <button type="button" class="icon-btn" data-a="format" title="Formatieren (Shift+Alt+F)" aria-label="Formatieren"><span class="ms">format_align_left</span></button>
      <button type="button" class="icon-btn" data-a="find" title="Suchen und ersetzen (Strg+F)" aria-label="Suchen"><span class="ms">search</span></button>
      <button type="button" class="icon-btn" data-a="more" title="Ansicht" aria-label="Ansicht"><span class="ms">tune</span></button>
      <button type="button" class="icon-btn" data-a="preview" title="Vorschau ein/aus" aria-label="Vorschau"><span class="ms">preview</span></button>
      <button type="button" class="btn-primary act-btn" data-a="save" disabled title="Speichern (Strg+S)"><span class="ms">save</span><span class="lbl">Speichern</span></button>
    </div>
    <div class="se-main">
      <aside class="se-tree">
        <div class="se-tree-head"><span>EXPLORER</span><span class="spacer"></span>
          <button type="button" class="icon-btn sm" data-a="newfile" title="Neue Datei" aria-label="Neue Datei"><span class="ms">note_add</span></button>
          <button type="button" class="icon-btn sm" data-a="newdir" title="Neuer Ordner" aria-label="Neuer Ordner"><span class="ms">create_new_folder</span></button>
          <button type="button" class="icon-btn sm" data-a="upload" title="Hochladen" aria-label="Hochladen"><span class="ms">upload</span></button>
          <button type="button" class="icon-btn sm" data-a="refresh" title="Neu laden" aria-label="Neu laden"><span class="ms">refresh</span></button></div>
        <div class="se-tree-list" id="seTree"><div class="muted pad">Lade …</div></div>
        <input type="file" id="seUp" multiple hidden>
      </aside>
      <section class="se-code">
        <div class="se-tabs" id="seTabs"></div>
        <div class="se-host" id="seHost"><div class="fe-load"><span class="ms spin">progress_activity</span>Lade Editor …</div></div>
      </section>
      <section class="se-prev">
        <div class="se-prev-bar"><div class="seg-mini" id="seDev">${Object.entries(DEVICES).map(([k, [ic, l]]) => `<button type="button" data-dev="${k}" title="${l}"><span class="ms sm">${ic}</span></button>`).join("")}</div>
          <span class="se-addr mono" id="seAddr"></span>
          <button type="button" class="icon-btn sm" data-a="reload" title="Neu laden" aria-label="Vorschau neu laden"><span class="ms">refresh</span></button>
          <a class="icon-btn sm" id="seOpen" target="_blank" rel="noopener" title="In neuem Tab öffnen" aria-label="In neuem Tab öffnen"><span class="ms">open_in_new</span></a></div>
        <div class="se-stage" id="seStage"><iframe id="seFrame" title="Vorschau" referrerpolicy="no-referrer" sandbox="allow-scripts allow-forms allow-modals allow-popups allow-same-origin allow-downloads"></iframe></div>
      </section>
    </div>
    <div class="fe-status se-status"><span id="seLang"></span><span id="sePos"></span><span id="seSize"></span><span id="seState"></span></div>`;
  document.body.appendChild(box);
  document.body.classList.add("fe-open");
  requestAnimationFrame(() => box.classList.add("show"));
  const $ = (s) => box.querySelector(s);

  // ---------- Zustand ----------
  const open = new Map();   // path → { model, version, saved, readonly, view }
  let active = null, ed = null, monaco = null;
  const expanded = new Set([base]);
  const tree = new Map();   // dir → entries
  let previewOn = pref("preview", innerWidth > 900 ? "1" : "0") === "1";
  let device = pref("device", "desktop");
  let previewPath = "";

  const dirty = (f) => f && f.model.getValue() !== f.saved;
  const anyDirty = () => [...open.values()].some(dirty);
  const rel = (p) => p.slice(base.length + 1);
  filesForHints = () => ({ current: active ? rel(active) : "", all: [...tree.values()].flat().filter((e) => e.type === "file").map((e) => rel(e.path)) });

  // ---------- Schließen ----------
  const guard = (e) => { if (anyDirty()) { e.preventDefault(); e.returnValue = ""; } };
  addEventListener("beforeunload", guard);
  const onKey = (e) => {
    if (e.key === "Escape" && !box.querySelector(".find-widget.visible, .suggest-widget.visible") && !document.querySelector(".cd-backdrop, .pop")) { e.preventDefault(); close(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && !ed?.hasTextFocus()) { e.preventDefault(); save(); }
  };
  addEventListener("keydown", onKey, true);
  function closeNow() {
    removeEventListener("beforeunload", guard);
    removeEventListener("keydown", onKey, true);
    document.body.classList.remove("fe-open");
    box.classList.remove("show");
    setTimeout(() => { ed?.dispose(); for (const f of open.values()) f.model.dispose(); box.remove(); }, 200);
  }
  async function close() {
    const n = [...open.values()].filter(dirty).length;
    if (n && !(await confirmDialog("Änderungen verwerfen?", `${n} ${n === 1 ? "Datei hat" : "Dateien haben"} ungespeicherte Änderungen.`, { ok: "Verwerfen", danger: true }))) return;
    closeNow();
  }

  // ---------- Explorer ----------
  async function loadDir(dir) {
    const d = await api.get("/fs/list", { path: dir });
    tree.set(dir, d.entries.filter((e) => e.name !== ".git"));
  }
  function drawTree() {
    const rows = [];
    const walk = (dir, depth) => {
      for (const e of tree.get(dir) || []) {
        const isDir = e.type === "dir";
        const openDir = isDir && expanded.has(e.path);
        const [ic, col] = isDir ? [openDir ? "folder_open" : "folder", "#c9a86a"] : ICONS[ext(e.name)] || ["description", "#9aa0a6"];
        const tag = e.path === served && served !== base ? `<small class="se-tag">ausgeliefert</small>` : "";
        rows.push(`<div class="se-row ${e.path === active ? "on" : ""} ${open.has(e.path) && dirty(open.get(e.path)) ? "dirty" : ""}" data-p="${esc(e.path)}" data-t="${e.type}" style="--d:${depth}" title="${esc(rel(e.path))}">
          <span class="ms se-chev">${isDir ? (openDir ? "expand_more" : "chevron_right") : ""}</span><span class="ms se-ic" style="color:${col}">${ic}</span>
          <span class="se-name">${esc(e.name)}</span>${tag}<button type="button" class="icon-btn sm se-rmenu" data-rm aria-label="Aktionen"><span class="ms">more_horiz</span></button></div>`);
        if (openDir) walk(e.path, depth + 1);
      }
    };
    walk(base, 0);
    $("#seTree").innerHTML = rows.join("") || `<div class="side-empty">Der Ordner ist leer.<br>Leg oben eine Datei an oder lade welche hoch.</div>`;
  }
  async function refresh() {
    try {
      await Promise.all([...expanded].map((d) => loadDir(d).catch(() => expanded.delete(d))));
      drawTree();
    } catch (e) { toast(e.message); }
  }
  $("#seTree").addEventListener("click", async (e) => {
    const row = e.target.closest(".se-row");
    if (!row) return;
    const p = row.dataset.p, t = row.dataset.t;
    if (e.target.closest("[data-rm]")) return rowMenu(e.target.closest("[data-rm]"), p, t);
    if (t === "dir") {
      if (expanded.has(p)) expanded.delete(p);
      else { expanded.add(p); await loadDir(p).catch((x) => toast(x.message)); }
      drawTree();
    } else {
      await openFile(p);
      box.classList.remove("tree-open");
    }
  });
  $("#seTree").addEventListener("contextmenu", (e) => {
    const row = e.target.closest(".se-row");
    if (!row) return;
    e.preventDefault();
    rowMenu(row.querySelector("[data-rm]"), row.dataset.p, row.dataset.t);
  });
  const dirOf = (p, t) => (t === "dir" ? p : p.split("/").slice(0, -1).join("/"));
  function rowMenu(anchor, p, t) {
    popMenu(anchor, [
      ...(t === "file" ? [{ value: "view", icon: "visibility", label: "In der Vorschau zeigen", sub: urlFor(p) ? urlFor(p).replace(/^https:\/\//, "") : "liegt nicht im ausgelieferten Ordner" }] : []),
      { value: "newfile", icon: "note_add", label: "Neue Datei hier" }, { value: "newdir", icon: "create_new_folder", label: "Neuer Ordner hier" },
      "-", { value: "rename", icon: "edit", label: "Umbenennen" }, { value: "del", icon: "delete", label: "Löschen", danger: true },
    ], async (v) => {
      if (v === "view") { setPreview(p); if (!previewOn) togglePreview(); }
      if (v === "newfile") newItem(dirOf(p, t), false);
      if (v === "newdir") newItem(dirOf(p, t), true);
      if (v === "rename") rename(p);
      if (v === "del") del(p, t);
    }, { align: "right" });
  }
  async function newItem(dir, folder) {
    const r = await formDialog(folder ? "Neuer Ordner" : "Neue Datei", [{ name: "name", label: "Name", placeholder: folder ? "assets" : "index.html",
      hint: `in ${rel(dir) || "/"}` }], { ok: "Anlegen", icon: folder ? "create_new_folder" : "note_add", wide: false });
    if (!r?.name) return;
    try {
      const names = r.name.split("/").filter(Boolean);  // „css/style.css“ legt den Ordner gleich mit an
      let d = dir;
      for (const [i, n] of names.entries()) {
        const last = i === names.length - 1;
        const path = `${d}/${n}`;
        if (!last || folder) {
          if (!(tree.get(d) || []).some((e) => e.name === n)) await api.post("/fs/new", { dir: d, name: n, folder: true }).catch((x) => { if (!(x instanceof ApiError && x.status === 409)) throw x; });
          expanded.add(path);
        } else {
          await api.post("/fs/new", { dir: d, name: n });
        }
        expanded.add(d);
        await loadDir(d);
        d = path;
      }
      if (folder) await loadDir(d);
      drawTree();
      if (!folder) await openFile(d);
    } catch (e) { toast(e.message); }
  }
  async function rename(p) {
    const old = p.split("/").pop();
    const r = await formDialog("Umbenennen", [{ name: "name", label: "Neuer Name", value: old }], { ok: "Umbenennen", icon: "edit", wide: false });
    if (!r?.name || r.name === old) return;
    try {
      const res = await api.post("/fs/rename", { path: p, name: r.name });
      const np = res?.path || `${p.split("/").slice(0, -1).join("/")}/${r.name}`;
      if (open.has(p)) { // offene Datei unter neuem Namen weiterführen
        const f = open.get(p);
        open.delete(p);
        const model = monaco.editor.createModel(f.model.getValue(), langOf(np), monaco.Uri.parse("file://" + np));
        f.model.dispose();
        open.set(np, { ...f, model });
        if (active === p) { active = null; show(np); }
      }
      await refresh();
      drawTabs();
    } catch (e) { toast(e.message); }
  }
  async function del(p, t) {
    if (!(await confirmDialog(`${t === "dir" ? "Ordner" : "Datei"} löschen?`, `${rel(p)} wird ${t === "dir" ? "samt Inhalt " : ""}gelöscht. Das lässt sich nicht rückgängig machen.`, { ok: "Löschen", danger: true }))) return;
    try {
      const j = await api.post("/fs/delete", { items: [p] });
      for (let i = 0; i < 150; i++) { const s = await api.get(`/jobs/${j.id}`); if (s.done) { if (!s.ok) throw new Error("Löschen ging nicht"); break; } await new Promise((r) => setTimeout(r, 200)); }
      for (const k of [...open.keys()]) if (k === p || k.startsWith(p + "/")) closeTab(k, true);
      await refresh();
      toast("Gelöscht");
    } catch (e) { toast(e.message); }
  }
  $("#seUp").onchange = async (e) => {
    const list = [...e.target.files].map((file) => ({ file, rel: file.name }));
    e.target.value = "";
    if (!list.length) return;
    await upload(list, uploadDir);
    await refresh();
    reloadPreview();
  };
  let uploadDir = base;

  // ---------- Tabs & Editor ----------
  const langOf = (p) => LANGS[ext(p)] || "plaintext";
  async function openFile(p) {
    if (open.has(p)) return show(p);
    if (!isText(p)) { // Bilder, Schriften …: in der Vorschau bzw. neuem Tab zeigen
      const u = urlFor(p);
      if (u) { setPreview(p); if (!previewOn) togglePreview(); } else toast("Das ist keine Textdatei");
      return;
    }
    try {
      const d = await api.get("/fs/text", { path: p });
      const model = monaco.editor.createModel(d.content, langOf(p), monaco.Uri.parse("file://" + p));
      model.onDidChangeContent(() => { mark(); });
      open.set(p, { model, version: d.version, saved: d.content, readonly: d.readonly, view: null });
      show(p);
    } catch (e) { toast(e.message); }
  }
  function show(p) {
    const cur = active && open.get(active);
    if (cur) cur.view = ed.saveViewState();
    active = p;
    const f = open.get(p);
    $("#seHost .se-empty")?.remove();
    ed.setModel(f.model);
    if (f.view) ed.restoreViewState(f.view);
    ed.updateOptions({ readOnly: !!f.readonly });
    ed.focus();
    $("#seLang").textContent = `${f.model.getLanguageId()}${f.readonly ? " · nur lesen" : ""}`;
    if (urlFor(p) && /\.html?$/i.test(p)) setPreview(p);
    drawTabs();
    drawTree();
    status();
  }
  function drawTabs() {
    $("#seTabs").innerHTML = [...open.entries()].map(([p, f]) => {
      const [ic, col] = ICONS[ext(p)] || ["description", "#9aa0a6"];
      return `<div class="se-tab ${p === active ? "on" : ""} ${dirty(f) ? "dirty" : ""}" data-tab="${esc(p)}" title="${esc(rel(p))}"><span class="ms" style="color:${col}">${ic}</span>
        <span>${esc(p.split("/").pop())}</span><button type="button" class="se-x" data-x aria-label="Schließen"><i></i><span class="ms">close</span></button></div>`;
    }).join("");
    $("#seTabs").querySelector(".on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  async function closeTab(p, force = false) {
    const f = open.get(p);
    if (!f) return;
    if (!force && dirty(f) && !(await confirmDialog("Änderungen verwerfen?", `${p.split("/").pop()} hat ungespeicherte Änderungen.`, { ok: "Verwerfen", danger: true }))) return;
    open.delete(p);
    if (active === p) {
      active = null;
      const next = [...open.keys()].pop();
      if (next) show(next);
      else { ed.setModel(null); emptyState(); }
    }
    f.model.dispose();
    drawTabs();
    drawTree();
    mark();
  }
  $("#seTabs").addEventListener("click", (e) => {
    const t = e.target.closest("[data-tab]");
    if (!t) return;
    if (e.target.closest("[data-x]")) closeTab(t.dataset.tab);
    else show(t.dataset.tab);
  });
  $("#seTabs").addEventListener("auxclick", (e) => { const t = e.target.closest("[data-tab]"); if (t && e.button === 1) closeTab(t.dataset.tab); });
  function emptyState() {
    $("#seLang").textContent = "";
    $("#sePos").textContent = "";
    $("#seSize").textContent = "";
    const d = document.createElement("div");
    d.className = "se-empty";
    d.innerHTML = `<span class="ms big">draft</span><b>Keine Datei geöffnet</b><span>Wähl links im Explorer eine Datei aus.</span>`;
    $("#seHost").appendChild(d);
  }
  function mark() {
    $('[data-a="save"]').disabled = !anyDirty();
    const f = active && open.get(active);
    $("#seTabs").querySelector(".on")?.classList.toggle("dirty", dirty(f));
    for (const [p, x] of open) $("#seTabs").querySelector(`[data-tab="${CSS.escape(p)}"]`)?.classList.toggle("dirty", dirty(x));
    $("#seState").textContent = anyDirty() ? "Ungespeichert" : "";
  }
  function status() {
    const f = active && open.get(active);
    if (!f || !ed) return;
    const p = ed.getPosition();
    $("#sePos").textContent = `Zeile ${p.lineNumber}, Spalte ${p.column} · ${f.model.getLineCount()} Zeilen`;
    $("#seSize").textContent = fmtBytes(new Blob([f.model.getValue()]).size);
  }

  // Speichern: aktuelle Datei (Strg+S) oder alle (Knopf)
  async function saveOne(p, force = false) {
    const f = open.get(p);
    if (!f || !dirty(f) || f.readonly) return true;
    const text = f.model.getValue();
    try {
      const r = await api.put("/fs/text", { path: p, content: text, version: force ? null : f.version });
      f.version = r.version;
      f.saved = text;
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        if (await confirmDialog("Datei wurde geändert", `${p.split("/").pop()} wurde inzwischen woanders geändert. Mit deiner Fassung überschreiben?`, { ok: "Überschreiben", danger: true })) return saveOne(p, true);
        return false;
      }
      toast(e.message || "Speichern ging nicht");
      return false;
    }
  }
  let saving = false;
  async function save(all = true) {
    if (saving) return;
    saving = true;
    $('[data-a="save"]').disabled = true;
    const list = all ? [...open.keys()].filter((p) => dirty(open.get(p))) : [active].filter(Boolean);
    let ok = 0;
    for (const p of list) if (await saveOne(p)) ok++;
    saving = false;
    mark();
    drawTree();
    status();
    if (ok) { toast(ok === 1 ? "Gespeichert" : `${ok} Dateien gespeichert`); reloadPreview(); }
  }

  // ---------- Vorschau ----------
  function urlFor(p) {
    if (p !== served && !p.startsWith(served + "/")) return "";
    const r = p.slice(served.length + 1);
    return `${url}/${r.replace(/(^|\/)index\.html?$/i, "$1").split("/").map(encodeURIComponent).join("/")}`;
  }
  function setPreview(p) {
    previewPath = p;
    if (previewOn) loadPreview();
  }
  function loadPreview() {
    const u = (previewPath && urlFor(previewPath)) || `${url}/`;
    $("#seAddr").textContent = u.replace(/^https:\/\//, "");
    $("#seOpen").href = u;
    const fr = $("#seFrame");
    const target = u + (u.includes("?") ? "&" : "?") + "_ida=" + Date.now(); // am Cache vorbei
    fr.src = target;
  }
  function reloadPreview() { if (previewOn) loadPreview(); }
  function togglePreview() {
    previewOn = !previewOn;
    setPref("preview", previewOn ? "1" : "0");
    box.classList.toggle("prev-on", previewOn);
    $('[data-a="preview"]').classList.toggle("on", previewOn);
    if (previewOn) loadPreview();
    else $("#seFrame").src = "about:blank";
  }
  function setDevice(d) {
    device = DEVICES[d] ? d : "desktop";
    setPref("device", device);
    $("#seStage").dataset.dev = device;
    $("#seStage").style.setProperty("--dev-w", DEVICES[device][2] || "100%");
    box.querySelectorAll("[data-dev]").forEach((b) => b.classList.toggle("on", b.dataset.dev === device));
  }
  $("#seDev").onclick = (e) => { const b = e.target.closest("[data-dev]"); if (b) setDevice(b.dataset.dev); };

  // ---------- Ansicht ----------
  const opts = { wrap: pref("wrap", "1") === "1", minimap: pref("minimap", "0") === "1", size: Number(pref("size", 13.5)) };
  function viewMenu(anchor) {
    popMenu(anchor, [
      { value: "wrap", icon: "wrap_text", label: "Zeilen umbrechen", on: opts.wrap }, { value: "minimap", icon: "map", label: "Minimap", on: opts.minimap },
      "-", { head: "Schriftgröße" }, ...[12, 13.5, 15, 17].map((s) => ({ value: "s" + s, icon: "format_size", label: `${String(s).replace(".", ",")} px`, on: opts.size === s })),
      "-", { value: "palette", icon: "terminal", label: "Befehlspalette", sub: "F1" },
    ], (v) => {
      if (v === "wrap") opts.wrap = !opts.wrap;
      if (v === "minimap") opts.minimap = !opts.minimap;
      if (v.startsWith?.("s")) opts.size = Number(v.slice(1));
      if (v === "palette") { ed.focus(); ed.trigger("ida", "editor.action.quickCommand"); return; }
      setPref("wrap", opts.wrap ? "1" : "0"); setPref("minimap", opts.minimap ? "1" : "0"); setPref("size", opts.size);
      ed.updateOptions({ wordWrap: opts.wrap ? "on" : "off", minimap: { enabled: opts.minimap }, fontSize: opts.size, lineHeight: Math.round(opts.size * 1.5) });
    }, { align: "right" });
  }

  box.addEventListener("click", (e) => {
    const b = e.target.closest("[data-a]");
    if (!b) return;
    const a = b.dataset.a;
    if (a === "close") close();
    if (a === "save") save(true);
    if (a === "format") ed?.getAction("editor.action.formatDocument")?.run();
    if (a === "find") { ed?.focus(); ed?.getAction("actions.find")?.run(); }
    if (a === "more") viewMenu(b);
    if (a === "preview") togglePreview();
    if (a === "reload") loadPreview();
    if (a === "tree") box.classList.toggle("tree-open");
    if (a === "newfile") newItem(base, false);
    if (a === "newdir") newItem(base, true);
    if (a === "upload") { uploadDir = base; $("#seUp").click(); }
    if (a === "refresh") refresh();
  });
  box.querySelector(".se-main").addEventListener("click", (e) => {
    if (box.classList.contains("tree-open") && !e.target.closest(".se-tree")) box.classList.remove("tree-open");
  });

  // ---------- Start ----------
  box.classList.toggle("prev-on", previewOn);
  $('[data-a="preview"]').classList.toggle("on", previewOn);
  setDevice(device);
  try {
    [monaco] = await Promise.all([loadMonaco(), loadDir(base)]);
    if (served !== base) { expanded.add(served); await loadDir(served).catch(() => expanded.delete(served)); }
  } catch (e) {
    closeNow();
    throw e;
  }
  drawTree();
  $("#seHost").innerHTML = "";
  ed = monaco.editor.create($("#seHost"), {
    theme: "ida-html", automaticLayout: true, fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: opts.size, fontLigatures: true,
    lineHeight: Math.round(opts.size * 1.5), minimap: { enabled: opts.minimap }, scrollBeyondLastLine: false, smoothScrolling: true, cursorBlinking: "smooth",
    cursorSmoothCaretAnimation: "on", padding: { top: 12, bottom: 12 }, tabSize: 2, wordWrap: opts.wrap ? "on" : "off", bracketPairColorization: { enabled: true },
    guides: { indentation: true, bracketPairs: true }, autoClosingBrackets: "always", autoClosingQuotes: "always", autoSurround: "languageDefined", autoIndent: "full",
    suggestOnTriggerCharacters: true, quickSuggestions: { other: true, comments: false, strings: false }, quickSuggestionsDelay: 10, acceptSuggestionOnEnter: "on",
    tabCompletion: "on", suggestSelection: "first", wordBasedSuggestions: "off", suggest: { preview: true, showSnippets: true, showWords: false },
    formatOnPaste: true, renderWhitespace: "selection", stickyScroll: { enabled: false }, linkedEditing: true, model: null,
  });
  ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => save(false));
  ed.addCommand(monaco.KeyMod.Shift | monaco.KeyMod.Alt | monaco.KeyCode.KeyF, () => ed.getAction("editor.action.formatDocument")?.run());
  ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyW, () => active && closeTab(active));
  ed.onDidChangeCursorPosition(status);
  emptyState();
  const start = file || [`${served}/index.html`, `${served}/index.htm`, `${base}/index.html`].find((p) => (tree.get(p.split("/").slice(0, -1).join("/")) || []).some((e) => e.path === p));
  if (start) await openFile(start);
  else if (previewOn) loadPreview();
}
