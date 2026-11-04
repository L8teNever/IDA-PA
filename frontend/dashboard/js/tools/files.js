// Tool „Dateien“: Datei-Explorer für den ganzen Server. Am PC wählt ein Klick aus und ein Doppelklick öffnet, am Handy öffnet
// ein Tippen und langes Drücken wählt aus. Rechtsklick oder ⋮ zeigt alles, was mit einem Eintrag geht.
import { api, ApiError, followJob } from "../api.js";
import { attempt, confirmDialog, esc, fmtBytes, fmtDateTime, openModal, popMenu, sheetHead, toast } from "../ui.js";
import { openEditor } from "./files-editor.js";
import { droppedFiles, upload } from "./files-upload.js";

const IMAGE = /\.(png|jpe?g|gif|webp|avif|bmp|ico|svg)$/i, VIDEO = /\.(mp4|webm|mov|m4v|ogv)$/i, AUDIO = /\.(mp3|wav|ogg|oga|m4a|flac|aac|opus)$/i;
const PDF = /\.pdf$/i, PAGE = /\.html?$/i, ARCHIVE = /\.(zip|tar|tar\.gz|tgz|tar\.bz2|tbz2|tar\.xz|txz)$/i;
const OTHER_ARCHIVE = /\.(7z|rar|gz|bz2|xz|zst)$/i;
const CODE = /\.(js|mjs|cjs|ts|tsx|jsx|py|sh|bash|php|rb|go|rs|java|c|h|cpp|cs|sql|lua|vue)$/i, DATA = /\.(json|ya?ml|toml|xml|ini|conf|cfg|env|csv|lock)$/i, DOC = /\.(md|txt|log|rst)$/i;
const THUMB_MAX = 25 * 1024 * 1024;

const raw = (p, download) => `/api/v1/fs/raw?${new URLSearchParams({ path: p, ...(download ? { download: "true" } : {}) })}`;
const coarse = () => matchMedia("(pointer: coarse)").matches;
const previewable = (e) => e.type === "file" && (IMAGE.test(e.name) || VIDEO.test(e.name) || AUDIO.test(e.name) || PDF.test(e.name) || PAGE.test(e.name));

function iconOf(e) {
  if (e.type === "broken") return ["link_off", "ic-err"];
  if (e.type === "dir") return [e.link ? "folder_special" : "folder", "ic-dir"];
  if (e.type === "other") return ["settings_input_component", "ic-mute"];
  const n = e.name;
  if (IMAGE.test(n)) return ["image", "ic-img"];
  if (VIDEO.test(n)) return ["movie", "ic-vid"];
  if (AUDIO.test(n)) return ["music_note", "ic-aud"];
  if (PDF.test(n)) return ["picture_as_pdf", "ic-pdf"];
  if (ARCHIVE.test(n) || OTHER_ARCHIVE.test(n)) return ["folder_zip", "ic-zip"];
  if (PAGE.test(n)) return ["html", "ic-code"];
  if (CODE.test(n) || /^(Dockerfile|Makefile)/.test(n)) return ["code", "ic-code"];
  if (DATA.test(n) || n.startsWith(".env")) return ["data_object", "ic-data"];
  if (DOC.test(n)) return ["description", "ic-doc"];
  return ["draft", "ic-mute"];
}

// bleibt beim Ordnerwechsel erhalten
let clip = null; // { items: [pfad], move, from }
let view = localStorage.getItem("fs-view") || "list";
let hidden = localStorage.getItem("fs-hidden") === "1";
let sort = JSON.parse(localStorage.getItem("fs-sort") || '{"by":"name","up":true}');

function ask(title, { label, value = "", ok = "OK", icon = "edit", selectStem = false }) {
  return new Promise((resolve) => {
    let out = null;
    const m = openModal(`${sheetHead(title, icon)}<label class="fs-field"><span>${esc(label)}</span><input id="fsAsk" type="text" spellcheck="false" autocomplete="off" value="${esc(value)}"></label>
      <div class="sheet-actions"><button type="button" class="btn-tonal" data-close>Abbrechen</button><button type="button" class="btn-primary" id="fsOk">${esc(ok)}</button></div>`,
    { onClose: () => resolve(out) });
    const input = m.el.querySelector("#fsAsk");
    const go = () => { out = input.value.trim() || null; m.close(); };
    m.el.querySelector("#fsOk").onclick = go;
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } };
    setTimeout(() => {
      input.focus();
      const dot = selectStem ? value.lastIndexOf(".") : -1;
      input.setSelectionRange(0, dot > 0 ? dot : value.length);
    }, 60);
  });
}

function askConflict(names) {
  return new Promise((resolve) => {
    let out = null;
    const shown = names.slice(0, 5).map((n) => `„${esc(n)}“`).join(", ") + (names.length > 5 ? ` und ${names.length - 5} weitere` : "");
    const m = openModal(`${sheetHead("Gibt es schon", "file_copy")}<p class="sheet-hint">${shown} ${names.length === 1 ? "liegt" : "liegen"} dort schon.</p>
      <div class="sheet-actions"><button type="button" class="btn-tonal" data-c="skip">Überspringen</button><button type="button" class="btn-tonal" data-c="keep">Beide behalten</button><button type="button" class="btn-danger" data-c="replace">Ersetzen</button></div>`,
    { onClose: () => resolve(out) });
    m.el.addEventListener("click", (e) => { const b = e.target.closest("[data-c]"); if (b) { out = b.dataset.c; m.close(); } });
  });
}

// Vorgang im Hintergrund: unten mitlaufen lassen, am Ende Bescheid sagen
async function runJob(start, { onDone } = {}) {
  const job = await attempt(start);
  if (!job) return null;
  const bar = document.createElement("div");
  bar.className = "fs-job";
  bar.innerHTML = `<span class="ms spin">progress_activity</span><span>${esc(job.title)} …</span>`;
  document.body.appendChild(bar);
  const lines = [];
  let result = null;
  try {
    await followJob(job.id, (ev) => { if (ev.line) lines.push(ev.line); if (ev.done) result = ev.done; });
  } catch { /* läuft auf dem Server weiter */ }
  bar.remove();
  if (result && !result.ok) {
    const why = lines.filter((l) => !l.startsWith("$ ") && !l.startsWith("→")).slice(-4).join("\n") || "Unbekannter Fehler";
    openModal(`${sheetHead("Das ging nicht", "error")}<p class="sheet-hint">${esc(job.title)}</p><pre class="fs-err">${esc(why)}</pre>`);
  } else if (!result) toast("Läuft im Hintergrund weiter");
  onDone?.(result);
  return result?.ok ? job : null;
}

export default {
  id: "files", label: "Dateien", icon: "folder_open", path: "/files",
  match: (p) => (p === "/files" ? {} : null),
  mount(el, params, ctx) {
    let path = params.query.path || "", data = null, sel = new Set(), anchor = null, filter = "", alive = true, viewer = null;
    el.innerHTML = `<div class="page fs" id="fsRoot"><div id="fsBars"></div><div id="fsList" class="fs-list"></div>
      <div class="fs-drop" id="fsDrop" hidden><span class="ms">upload</span><b>Zum Hochladen loslassen</b></div>
      <input type="file" id="fsFiles" multiple hidden><input type="file" id="fsDir" webkitdirectory multiple hidden><input type="file" id="fsArc" accept=".zip,.tar,.gz,.tgz,.bz2,.xz" hidden></div>`;
    const $ = (id) => el.querySelector("#" + id);
    const shown = () => {
      const q = filter.toLowerCase();
      const list = (data?.entries || []).filter((e) => (hidden || !e.name.startsWith(".")) && (!q || e.name.toLowerCase().includes(q)));
      const dir = sort.up ? 1 : -1;
      const val = { name: (e) => e.name.toLowerCase(), size: (e) => e.size ?? -1, mtime: (e) => e.mtime }[sort.by];
      return list.sort((a, b) => (b.type === "dir") - (a.type === "dir") || (val(a) < val(b) ? -dir : val(a) > val(b) ? dir : 0));
    };
    const entryOf = (name) => data.entries.find((e) => e.name === name);
    const picked = () => [...sel].map(entryOf).filter(Boolean);
    const goto = (p) => ctx.navigate(`/files?path=${encodeURIComponent(p)}`);
    const reload = async () => { await load(); };

    function drawTop() {
      const parts = (data?.path || "").split("/").filter(Boolean);
      const root = data?.root || "/", skip = root === "/" ? 0 : root.split("/").filter(Boolean).length;
      let at = root === "/" ? "" : root;
      const crumbs = parts.slice(skip).map((p, i, a) => { at += "/" + p; return `<span class="ms sep">chevron_right</span><a href="/files?path=${encodeURIComponent(at)}" class="${i === a.length - 1 ? "cur" : ""}">${esc(p)}</a>`; }).join("");
      ctx.top(`<div class="topbar-left fs-top"><a class="icon-btn ${data?.parent != null ? "" : "off"}" ${data?.parent != null ? `href="/files?path=${encodeURIComponent(data.parent)}"` : ""} aria-label="Eine Ebene hoch"><span class="ms">arrow_upward</span></a>
          <nav class="fs-crumbs" id="fsCrumbs" aria-label="Pfad"><a href="/files?path=${encodeURIComponent(root)}" class="${parts.length === skip ? "cur" : ""}" title="${esc(root)}"><span class="ms">hard_drive</span>${root === "/" ? "" : esc(root)}</a>${crumbs}</nav></div>
        <div class="topbar-actions"><button type="button" class="btn-tonal act-btn" id="fsNew" ${data?.readonly ? "disabled" : ""}><span class="ms">add</span><span class="lbl">Neu</span></button>
          <button type="button" class="btn-primary act-btn" id="fsUp" ${data?.readonly ? "disabled" : ""}><span class="ms">upload</span><span class="lbl">Hochladen</span></button></div>`);
      const c = document.getElementById("fsCrumbs");
      if (c) c.scrollLeft = c.scrollWidth;
    }

    function drawBars() {
      const list = shown(), n = sel.size;
      const only = n === 1 ? picked()[0] : null;
      const b = (a, icon, label, extra = "") => `<button type="button" class="fs-act ${extra}" data-a="${a}" title="${label}"><span class="ms">${icon}</span><span class="lbl">${label}</span></button>`;
      const selBar = n ? `<div class="fs-bar sel"><button type="button" class="icon-btn sm" data-a="none" aria-label="Auswahl aufheben"><span class="ms">close</span></button><b>${n} ausgewählt</b><span class="fs-gap"></span>
          ${b("copy", "content_copy", "Kopieren")}${data.readonly ? "" : b("cut", "content_cut", "Ausschneiden")}${only && !data.readonly ? b("rename", "edit", "Umbenennen") : ""}${only?.type === "file" ? b("download", "download", "Laden") : ""}
          ${only && ARCHIVE.test(only.name) && !data.readonly ? b("extract", "unarchive", "Entpacken") : ""}${data.readonly ? "" : b("zip", "folder_zip", "Packen")}${data.readonly ? "" : b("delete", "delete", "Löschen", "danger")}</div>` : "";
      const clipBar = clip ? `<div class="fs-bar clip"><span class="ms">${clip.move ? "content_cut" : "content_copy"}</span><span class="fs-clip-t">${clip.items.length} ${clip.items.length === 1 ? "Element" : "Elemente"} ${clip.move ? "ausgeschnitten" : "kopiert"}</span><span class="fs-gap"></span>
          <button type="button" class="btn-tonal sm" data-a="clear">Abbrechen</button><button type="button" class="btn-primary sm" data-a="paste" ${data?.readonly ? "disabled" : ""}><span class="ms">content_paste</span>Hier einfügen</button></div>` : "";
      const count = data ? `${list.length} ${list.length === 1 ? "Eintrag" : "Einträge"}${data.more ? ` · ${data.more} weitere nicht gezeigt` : ""}${data.usage ? ` · ${fmtBytes(data.usage.free)} frei` : ""}` : "";
      const keep = $("fsFilter");
      const had = keep && document.activeElement === keep;
      const pos = keep?.selectionStart;
      // Die Auswahl-Leiste ersetzt die Werkzeug-Zeile (gleich hoch): sonst rutscht die Liste beim ersten Klick weg
      $("fsBars").innerHTML = `${clipBar}${selBar || `<div class="fs-tools"><label class="fs-search"><span class="ms">search</span><input id="fsFilter" type="search" placeholder="In diesem Ordner suchen" value="${esc(filter)}" autocomplete="off"></label>
        <small class="muted fs-count">${esc(count)}</small><span class="fs-gap"></span>
        <button type="button" class="icon-btn sm" id="fsSort" title="Sortieren" aria-label="Sortieren"><span class="ms">sort</span></button>
        <button type="button" class="icon-btn sm ${hidden ? "on" : ""}" id="fsHide" title="Versteckte Dateien ${hidden ? "ausblenden" : "zeigen"}" aria-label="Versteckte Dateien"><span class="ms">${hidden ? "visibility" : "visibility_off"}</span></button>
        <div class="seg-mini fs-seg"><button type="button" class="${view === "list" ? "on" : ""}" data-view="list" aria-label="Liste"><span class="ms">view_list</span></button><button type="button" class="${view === "grid" ? "on" : ""}" data-view="grid" aria-label="Kacheln"><span class="ms">grid_view</span></button></div></div>`}`;
      if (had) { const f = $("fsFilter"); f.focus(); f.setSelectionRange(pos, pos); }
    }

    function rowHtml(e) {
      const [ic, cls] = iconOf(e), on = sel.has(e.name);
      const thumb = view === "grid" && e.type === "file" && IMAGE.test(e.name) && e.size < THUMB_MAX && !data.readonly;
      const meta = e.type === "dir" ? "Ordner" : e.type === "broken" ? "Link ins Leere" : e.type === "other" ? "Gerät oder Socket" : fmtBytes(e.size);
      const cut = clip?.move && clip.items.includes(e.path) ? "cut" : "";
      const name = `<span class="fs-name" title="${esc(e.name)}">${esc(e.name)}${e.link ? `<span class="ms fs-link" title="Link auf ${esc(e.link)}">north_east</span>` : ""}</span>`;
      const check = `<button type="button" class="fs-check" data-check aria-label="Auswählen"><span class="ms">${on ? "check_circle" : "radio_button_unchecked"}</span></button>`;
      const more = `<button type="button" class="icon-btn sm fs-more" data-more aria-label="Aktionen"><span class="ms">more_vert</span></button>`;
      const attrs = `class="fs-row ${on ? "on" : ""} ${cut}" data-name="${esc(e.name)}" ${data.readonly ? "" : 'draggable="true"'}`;
      if (view === "grid") return `<div ${attrs}>${check}${more}<div class="fs-thumb">${thumb ? `<img src="${raw(e.path)}" alt="" loading="lazy" draggable="false">` : `<span class="ms fill ${cls}">${ic}</span>`}</div>${name}<small>${esc(meta)}</small></div>`;
      return `<div ${attrs}>${check}<span class="ms fill fs-ic ${cls}">${ic}</span><div class="fs-main">${name}<small class="fs-sub">${esc(meta)} · ${e.mtime ? fmtDateTime(e.mtime) : ""}</small></div>
        <span class="fs-col size">${e.type === "file" ? fmtBytes(e.size) : ""}</span><span class="fs-col date">${e.mtime ? fmtDateTime(e.mtime) : ""}</span><span class="fs-col own" title="${esc(e.mode)}">${esc(e.owner)}</span>${more}</div>`;
    }

    function drawList() {
      const list = shown();
      const box = $("fsList");
      box.className = `fs-list ${view}`;
      box.innerHTML = list.length ? list.map(rowHtml).join("") : `<div class="empty-stage"><span class="ms big">${filter ? "search_off" : "folder_off"}</span><br>${filter ? "Nichts gefunden" : "Dieser Ordner ist leer"}${filter || data.readonly ? "" : "<br><small>Dateien hierher ziehen oder „Hochladen“ wählen</small>"}</div>`;
    }

    const draw = () => { drawBars(); drawList(); };

    async function load() {
      let d;
      try { d = await api.get("/fs/list", { path }); } catch (e) {
        if (!alive) return;
        $("fsList").innerHTML = `<div class="empty-stage"><span class="ms big">${e.status === 403 ? "lock" : "search_off"}</span><br>${esc(e.message)}<br><a class="fs-home" href="/files">Zum Anfang</a></div>`;
        return;
      }
      if (!alive) return;
      data = d;
      path = d.path;
      sel = new Set([...sel].filter((n) => d.entries.some((e) => e.name === n)));
      drawTop();
      draw();
    }

    // ---- öffnen ----
    async function open(e) {
      if (e.type === "dir") return goto(e.path);
      if (e.type !== "file") return toast(e.type === "broken" ? `Der Link zeigt ins Leere: ${e.link}` : "Das ist keine normale Datei");
      if (previewable(e)) return preview(e);
      if (ARCHIVE.test(e.name)) {
        if (data.readonly || !(await confirmDialog("Entpacken?", `${e.name} in einen eigenen Ordner entpacken?`, { ok: "Entpacken" }))) return;
        return extract(e);
      }
      return edit(e);
    }
    async function edit(e) {
      try {
        await openEditor(e.path, { onSaved: reload });
      } catch (err) {
        if (err instanceof ApiError && (err.status === 415 || err.status === 413)) return infoSheet(e, err.message);
        toast(err.message || "Öffnen ging nicht");
      }
    }

    function preview(start) {
      const list = shown().filter(previewable);
      let i = Math.max(0, list.findIndex((x) => x.name === start.name));
      const box = document.createElement("div");
      box.className = "fv";
      const stage = () => {
        const e = list[i];
        const url = raw(e.path);
        const body = IMAGE.test(e.name) ? `<img src="${url}" alt="${esc(e.name)}">`
          : VIDEO.test(e.name) ? `<video src="${url}" controls autoplay playsinline></video>`
          : AUDIO.test(e.name) ? `<div class="fv-audio"><span class="ms fill">music_note</span><audio src="${url}" controls autoplay></audio></div>`
          : PDF.test(e.name) ? `<iframe src="${url}" title="${esc(e.name)}"></iframe>`
          : `<iframe src="${url}" sandbox="allow-scripts" title="${esc(e.name)}"></iframe>`;
        box.innerHTML = `<div class="fv-bar"><button type="button" class="icon-btn" data-v="close" aria-label="Schließen"><span class="ms">close</span></button>
            <div class="fe-title"><b>${esc(e.name)}</b><small>${fmtBytes(e.size)} · ${list.length > 1 ? `${i + 1} von ${list.length}` : fmtDateTime(e.mtime)}</small></div>
            ${PAGE.test(e.name) || /\.svg$/i.test(e.name) ? `<button type="button" class="icon-btn" data-v="edit" title="Bearbeiten" aria-label="Bearbeiten"><span class="ms">edit</span></button>` : ""}
            <a class="icon-btn" href="${raw(e.path, true)}" download="${esc(e.name)}" title="Herunterladen" aria-label="Herunterladen"><span class="ms">download</span></a></div>
          <div class="fv-stage ${PDF.test(e.name) || PAGE.test(e.name) ? "page-doc" : ""}">${body}
            ${list.length > 1 ? `<button type="button" class="fv-nav prev" data-v="prev" aria-label="Vorherige"><span class="ms">chevron_left</span></button><button type="button" class="fv-nav next" data-v="next" aria-label="Nächste"><span class="ms">chevron_right</span></button>` : ""}</div>`;
      };
      const step = (d) => { i = (i + d + list.length) % list.length; stage(); };
      const close = () => { viewer = null; removeEventListener("keydown", key, true); box.classList.remove("show"); setTimeout(() => box.remove(), 180); };
      const key = (e) => {
        if (e.key === "Escape") { e.stopPropagation(); close(); }
        if (e.key === "ArrowLeft") step(-1);
        if (e.key === "ArrowRight") step(1);
      };
      box.onclick = (e) => {
        const a = e.target.closest("[data-v]")?.dataset.v;
        if (a === "close" || e.target.classList.contains("fv-stage")) close();
        if (a === "prev") step(-1);
        if (a === "next") step(1);
        if (a === "edit") { const x = list[i]; close(); edit(x); }
      };
      let touch = null;
      box.addEventListener("touchstart", (e) => { touch = e.touches.length === 1 ? e.touches[0].clientX : null; }, { passive: true });
      box.addEventListener("touchend", (e) => { if (touch != null && list.length > 1 && Math.abs(e.changedTouches[0].clientX - touch) > 60) step(e.changedTouches[0].clientX < touch ? 1 : -1); touch = null; });
      stage();
      document.body.appendChild(box);
      requestAnimationFrame(() => box.classList.add("show"));
      addEventListener("keydown", key, true);
      viewer = close;
    }

    function infoSheet(e, note = "") {
      const row = (k, v) => (v ? `<div class="fs-kv"><span>${k}</span><b class="mono">${esc(v)}</b></div>` : "");
      const m = openModal(`${sheetHead(e.name, iconOf(e)[0])}${note ? `<p class="sheet-hint">${esc(note)}</p>` : ""}<div class="fs-info">${row("Pfad", e.path)}${e.link ? row("Link auf", e.link) : ""}${e.size != null ? row("Größe", fmtBytes(e.size)) : ""}
        ${e.mtime ? row("Geändert", fmtDateTime(e.mtime)) : ""}${row("Besitzer", e.owner)}${row("Rechte", e.mode)}</div>
        <div class="sheet-actions">${e.type === "file" && !data.readonly ? `<a class="btn-tonal" href="${raw(e.path, true)}" download="${esc(e.name)}"><span class="ms">download</span>Herunterladen</a>` : ""}<button type="button" class="btn-primary" data-close>Schließen</button></div>`);
      return m;
    }

    // ---- ändern ----
    const setClip = (move) => {
      clip = { items: picked().map((e) => e.path), move, from: data.path };
      sel.clear();
      draw();
    };
    async function paste(dest = data.path, c = clip, keep = false) {
      if (!c) return;
      const body = { items: c.items, dest, move: c.move };
      const q = await attempt(() => api.post("/fs/conflicts", body));
      if (!q) return;
      let conflict = "keep";
      if (q.names.length) {
        conflict = await askConflict(q.names);
        if (!conflict) return;
      }
      await runJob(() => api.post("/fs/paste", { ...body, conflict }), { onDone: (r) => {
        if (r?.ok && c.move && !keep) clip = null;
        if (alive) reload();
      } });
    }
    async function rename(e) {
      const name = await ask("Umbenennen", { label: "Neuer Name", value: e.name, ok: "Umbenennen", selectStem: e.type !== "dir" });
      if (!name || name === e.name) return;
      if (await attempt(() => api.post("/fs/rename", { path: e.path, name }))) { sel = new Set([name]); reload(); }
    }
    async function remove(list) {
      const one = list.length === 1 ? list[0] : null;
      const dirs = list.filter((e) => e.type === "dir").length;
      const text = `${one ? `„${one.name}“` : `${list.length} Elemente`}${dirs ? (one ? " mit allem, was darin liegt," : ` (davon ${dirs} ${dirs === 1 ? "Ordner" : "Ordner"} mit Inhalt)`) : ""} wird endgültig gelöscht. Das lässt sich nicht rückgängig machen.`;
      if (!(await confirmDialog("Wirklich löschen?", text, { ok: "Löschen", danger: true }))) return;
      await runJob(() => api.post("/fs/delete", { items: list.map((e) => e.path) }), { onDone: () => { sel.clear(); if (alive) reload(); } });
    }
    const extract = (e) => runJob(() => api.post("/fs/extract", { path: e.path }), { onDone: (r) => { if (r?.ok) toast("Entpackt"); if (alive) reload(); } });
    const zip = (list) => runJob(() => api.post("/fs/compress", { items: list.map((e) => e.path) }), { onDone: (r) => { if (r?.ok) toast("Gepackt"); if (alive) reload(); } });
    function download(e) {
      const a = document.createElement("a");
      a.href = raw(e.path, true);
      a.download = e.name;
      a.click();
    }
    async function newItem(folder) {
      const name = await ask(folder ? "Neuer Ordner" : "Neue Datei", { label: "Name", ok: "Anlegen", icon: folder ? "create_new_folder" : "note_add" });
      if (!name) return;
      const e = await attempt(() => api.post("/fs/new", { dir: data.path, name, folder }));
      if (!e) return;
      await reload();
      if (!folder) edit(e);
    }
    async function send(list, { unpack = false } = {}) {
      if (!list.length) return;
      const dir = data.path;
      const done = await upload(list, dir);
      if (alive && dir === data.path) await reload();
      const archives = done.filter((n) => !n.includes("/") && ARCHIVE.test(n));
      if (!archives.length || !alive) return;
      if (!unpack && !(archives.length === 1 && done.length === 1 && await confirmDialog("Entpacken?", `${archives[0]} gleich entpacken?`, { ok: "Entpacken" }))) return;
      for (const n of archives) await extract({ path: `${dir === "/" ? "" : dir}/${n}` });
    }

    function menu(e, target) {
      if (!sel.has(e.name)) { sel = new Set([e.name]); anchor = e.name; draw(); }
      const many = sel.size > 1, ro = data.readonly;
      const items = [];
      if (!many) {
        items.push({ value: "open", icon: e.type === "dir" ? "folder_open" : "open_in_new", label: "Öffnen" });
        if (e.type === "file" && !IMAGE.test(e.name.replace(/\.svg$/i, "")) && !VIDEO.test(e.name) && !AUDIO.test(e.name) && !PDF.test(e.name)) items.push({ value: "edit", icon: "code", label: "Im Editor öffnen" });
        if (e.type === "file" && !ro) items.push({ value: "download", icon: "download", label: "Herunterladen" });
        if (ARCHIVE.test(e.name) && !ro) items.push({ value: "extract", icon: "unarchive", label: "Entpacken" });
        items.push("-");
      }
      items.push({ value: "copy", icon: "content_copy", label: "Kopieren" });
      if (!ro) items.push({ value: "cut", icon: "content_cut", label: "Ausschneiden" });
      if (!many && !ro) items.push({ value: "rename", icon: "edit", label: "Umbenennen" });
      if (!ro) items.push({ value: "zip", icon: "folder_zip", label: "Als ZIP packen" });
      if (!many) items.push({ value: "path", icon: "link", label: "Pfad kopieren" }, { value: "info", icon: "info", label: "Eigenschaften" });
      if (!ro) items.push("-", { value: "delete", icon: "delete", label: "Löschen", danger: true });
      popMenu(target, items, (v) => act(v, e));
    }

    function act(a, e) {
      const list = picked();
      const one = e || (list.length === 1 ? list[0] : null);
      if (a === "open") return open(one);
      if (a === "edit") return edit(one);
      if (a === "download") return download(one);
      if (a === "extract") return extract(one);
      if (a === "copy") return setClip(false);
      if (a === "cut") return setClip(true);
      if (a === "rename") return rename(one);
      if (a === "zip") return zip(list);
      if (a === "delete") return remove(list);
      if (a === "info") return infoSheet(one);
      if (a === "path") return navigator.clipboard?.writeText(one.path).then(() => toast("Pfad kopiert"), () => toast(one.path));
      if (a === "none") { sel.clear(); return draw(); }
      if (a === "clear") { clip = null; return draw(); }
      if (a === "paste") return paste();
    }

    function toggle(name, e) {
      const names = shown().map((x) => x.name);
      if (e?.shiftKey && anchor && names.includes(anchor)) {
        const [a, b] = [names.indexOf(anchor), names.indexOf(name)].sort((x, y) => x - y);
        names.slice(a, b + 1).forEach((n) => sel.add(n));
      } else if (sel.has(name)) sel.delete(name);
      else { sel.add(name); anchor = name; }
      draw();
    }

    // ---- Eingaben ----
    let held = null, heldFired = false, heldAt = [0, 0], lastClick = {};
    const onClick = (e) => {
      if (!document.contains(el)) return;
      const inside = e.target.closest("#fsRoot");
      if (e.target.closest("#fsNew")) return popMenu(e.target.closest("#fsNew"), [{ value: "dir", icon: "create_new_folder", label: "Ordner" }, { value: "file", icon: "note_add", label: "Datei" }], (v) => newItem(v === "dir"), { align: "right" });
      if (e.target.closest("#fsUp")) return popMenu(e.target.closest("#fsUp"), [{ value: "fsFiles", icon: "upload_file", label: "Dateien", sub: "Auch mehrere auf einmal" }, { value: "fsDir", icon: "drive_folder_upload", label: "Ordner", sub: "Mit allen Unterordnern" }, { value: "fsArc", icon: "unarchive", label: "ZIP hochladen und entpacken" }], (v) => $(v).click(), { align: "right" });
      if (!inside) return;
      const a = e.target.closest("[data-a]");
      if (a) return act(a.dataset.a);
      const vw = e.target.closest("[data-view]");
      if (vw) { view = vw.dataset.view; localStorage.setItem("fs-view", view); return draw(); }
      if (e.target.closest("#fsHide")) { hidden = !hidden; localStorage.setItem("fs-hidden", hidden ? "1" : "0"); return draw(); }
      if (e.target.closest("#fsSort")) {
        const opt = (by, label, icon) => ({ value: by, icon, label, on: sort.by === by, sub: sort.by === by ? (sort.up ? "Aufsteigend" : "Absteigend") : "" });
        return popMenu(e.target.closest("#fsSort"), [opt("name", "Name", "sort_by_alpha"), opt("size", "Größe", "straighten"), opt("mtime", "Datum", "schedule")], (by) => {
          sort = sort.by === by ? { by, up: !sort.up } : { by, up: by === "name" };
          localStorage.setItem("fs-sort", JSON.stringify(sort));
          draw();
        }, { align: "right" });
      }
      const row = e.target.closest(".fs-row");
      if (!row) { if (e.target.closest("#fsList") && sel.size) { sel.clear(); draw(); } return; }
      const name = row.dataset.name, ent = entryOf(name);
      if (e.target.closest("[data-more]")) return menu(ent, e.target.closest("[data-more]"));
      if (e.target.closest("[data-check]")) return toggle(name, e);
      if (heldFired) { heldFired = false; return; }
      if (coarse()) return sel.size ? toggle(name) : open(ent);
      if (e.ctrlKey || e.metaKey || e.shiftKey) return toggle(name, e);
      // Doppelklick selbst erkennen: der erste Klick zeichnet die Liste neu, ein dblclick-Ereignis käme deshalb nie an
      const now = Date.now();
      if (lastClick.name === name && now - lastClick.at < 450) { lastClick = {}; return open(ent); }
      lastClick = { name, at: now };
      sel = new Set([name]);
      anchor = name;
      draw();
    };
    const onContext = (e) => {
      const row = e.target.closest(".fs-row");
      if (!row || !e.target.closest("#fsRoot")) return;
      e.preventDefault();
      const x = e.clientX, y = e.clientY;
      menu(entryOf(row.dataset.name), { getBoundingClientRect: () => ({ left: x, right: x, top: y, bottom: y }), contains: () => false, focus() {} });
    };
    const onDown = (e) => {
      const row = e.target.closest(".fs-row");
      if (!row || e.pointerType !== "touch" || e.target.closest("button")) return;
      heldFired = false;
      heldAt = [e.clientX, e.clientY];
      held = setTimeout(() => { heldFired = true; navigator.vibrate?.(15); toggle(row.dataset.name); }, 450);
    };
    const stopHold = (e) => { if (e.type !== "pointermove" || Math.hypot(e.clientX - heldAt[0], e.clientY - heldAt[1]) > 10) clearTimeout(held); };
    const onInput = (e) => {
      if (e.target.id === "fsFilter") { filter = e.target.value; sel.clear(); draw(); }
    };
    const onKey = (e) => {
      if (viewer || document.querySelector(".fe, .cd-backdrop, .pop.show") || !data) return;
      const t = e.target;
      if (t.matches?.("input, textarea")) { if (e.key === "Escape") { filter = ""; t.blur(); draw(); } return; }
      const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase(), list = picked();
      if (mod && k === "a") { e.preventDefault(); sel = new Set(shown().map((x) => x.name)); return draw(); }
      if (mod && k === "c" && list.length) return setClip(false);
      if (mod && k === "x" && list.length && !data.readonly) return setClip(true);
      if (mod && k === "v" && clip && !data.readonly) return paste();
      if (e.key === "Delete" && list.length && !data.readonly) return remove(list);
      if (e.key === "F2" && list.length === 1 && !data.readonly) return rename(list[0]);
      if (e.key === "Enter" && list.length === 1) return open(list[0]);
      if (e.key === "Backspace" && data.parent != null) return goto(data.parent);
      if (e.key === "Escape" && sel.size) { sel.clear(); draw(); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        const names = shown().map((x) => x.name);
        if (!names.length) return;
        e.preventDefault();
        const at = names.indexOf(anchor), next = names[Math.max(0, Math.min(names.length - 1, at + (e.key === "ArrowDown" ? 1 : -1)))];
        sel = new Set([next]);
        anchor = next;
        draw();
        el.querySelector(`.fs-row[data-name="${CSS.escape(next)}"]`)?.scrollIntoView({ block: "nearest" });
      }
    };

    // Ziehen: Dateien vom Rechner laden hoch, Einträge auf einen Ordner verschieben sich
    const external = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
    let depth = 0;
    const onEnter = (e) => { if (external(e) && !data?.readonly) { depth++; $("fsDrop").hidden = false; } };
    const onLeave = (e) => { if (external(e) && --depth <= 0) { depth = 0; $("fsDrop").hidden = true; } };
    const onOver = (e) => {
      const row = e.target.closest?.(".fs-row");
      el.querySelectorAll(".fs-row.drop").forEach((x) => x !== row && x.classList.remove("drop"));
      if (external(e)) return e.preventDefault();
      if (row && entryOf(row.dataset.name)?.type === "dir" && !sel.has(row.dataset.name)) { e.preventDefault(); row.classList.add("drop"); }
    };
    const onDrop = async (e) => {
      depth = 0;
      $("fsDrop").hidden = true;
      if (external(e)) {
        e.preventDefault();
        if (!data.readonly) send(await droppedFiles(e.dataTransfer));
        return;
      }
      const row = e.target.closest?.(".fs-row");
      if (!row) return;
      e.preventDefault();
      row.classList.remove("drop");
      paste(entryOf(row.dataset.name).path, { items: picked().map((x) => x.path), move: true }, true);
    };
    const onDragStart = (e) => {
      const row = e.target.closest?.(".fs-row");
      if (!row) return;
      if (!sel.has(row.dataset.name)) { sel = new Set([row.dataset.name]); anchor = row.dataset.name; drawBars(); row.classList.add("on"); }
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", row.dataset.name);
    };

    const fileInput = (id, opt) => { $(id).onchange = (e) => {
      const list = [...e.target.files].map((file) => ({ file, rel: file.webkitRelativePath || file.name }));
      e.target.value = "";
      send(list, opt);
    }; };
    fileInput("fsFiles");
    fileInput("fsDir");
    fileInput("fsArc", { unpack: true });

    document.addEventListener("click", onClick);
    el.addEventListener("contextmenu", onContext);
    el.addEventListener("pointerdown", onDown);
    for (const n of ["pointerup", "pointercancel", "pointermove"]) el.addEventListener(n, stopHold);
    el.addEventListener("input", onInput);
    addEventListener("keydown", onKey);
    el.addEventListener("dragenter", onEnter);
    el.addEventListener("dragleave", onLeave);
    el.addEventListener("dragover", onOver);
    el.addEventListener("drop", onDrop);
    el.addEventListener("dragstart", onDragStart);

    drawTop();
    load();
    return () => {
      alive = false;
      viewer?.();
      document.removeEventListener("click", onClick);
      removeEventListener("keydown", onKey);
    };
  },
};
