// IDA Docs – Oberfläche: Dokumentliste, Markdown-Editor, Seitenvorschau in DIN A4, Einstellungen, Export.
import { render } from "./markdown.js";

// Muss zu DEFAULTS in dokumente/app/store.py passen: nur Abweichungen davon werden in .idadocs gespeichert.
const DEFAULTS = { marginTop: 25, marginBottom: 20, marginLeft: 30, marginRight: 20, font: "Arial", size: 11, lineHeight: 1.5, paraSpacing: 6,
  align: "justify", h1: 16, h2: 14, h3: 12, headingNumbers: true, pageNumbers: true, pageNumberPos: "center" };
const FONTS = { Arial: 'Arial, "Liberation Sans", Helvetica, sans-serif', "Times New Roman": '"Times New Roman", "Liberation Serif", Times, serif',
  Calibri: 'Calibri, Carlito, "Segoe UI", sans-serif', Georgia: "Georgia, serif", Verdana: "Verdana, DejaVu Sans, sans-serif", "Courier New": '"Courier New", monospace' };
const PAGE_W = 210, PAGE_H = 297, GAP = 10, PX = 96 / 25.4;
const $ = (s, r = document) => r.querySelector(s);
const el = (h) => { const t = document.createElement("template"); t.innerHTML = h.trim(); return t.content.firstElementChild; };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ico = (n) => `<span class="ms">${n}</span>`;
const prefs = { mode: localStorage.getItem("docs.mode") || "pages", dark: localStorage.getItem("docs.dark") === "1" };
const st = { doc: null, mpane: "src", timer: 0, rtimer: 0, saving: false, dirty: false };
const app = $("#app");

async function api(path, opt = {}) {
  const r = await fetch("/api/v1" + path, { ...opt, headers: { "content-type": "application/json" }, body: opt.body ? JSON.stringify(opt.body) : undefined });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.status === 204 ? null : r.json();
}
function toast(t) { const b = $("#toast"); b.textContent = t; b.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => b.classList.remove("show"), 2600); }
const cfg = (s) => ({ ...DEFAULTS, ...(s || {}) });
const diff = (s) => Object.fromEntries(Object.entries(s || {}).filter(([k, v]) => k in DEFAULTS && v !== DEFAULTS[k]));
const when = (t) => new Date(t * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });

// ---------------------------------------------------------------- Startseite
async function home() {
  history.replaceState(null, "", "#/");
  st.doc = null;
  let docs = [], backup = null, offline = false;
  try { [docs, backup] = await Promise.all([api("/docs"), api("/backup").catch(() => null)]); localStorage.setItem("docs.list", JSON.stringify(docs)); }
  catch { offline = true; docs = JSON.parse(localStorage.getItem("docs.list") || "[]"); }
  app.innerHTML = `<div class="home"><div class="home-in"><h1>Docs</h1><p class="sub">Aufsätze und Projektarbeiten in Markdown, auf DIN A4.</p>
    <div class="row"><button class="btn sm" id="imp">${ico("upload_file")}Importieren</button>
      ${offline ? `<span class="chip bad">${ico("cloud_off")}Offline – nur gemerkte Liste</span>` : backup?.enabled ? `<span class="chip ${backup.last_error ? "bad" : "ok"}">${ico("cloud_done")}Drive-Backup ${backup.last_error ? "Fehler" : "an"}</span>` : `<span class="chip">${ico("cloud_off")}Drive-Backup aus</span>`}</div>
    <div id="list">${docs.length ? docs.map((d) => `<div class="dcard" role="button" tabindex="0" data-id="${d.id}"><b>${esc(d.title)}</b><p>${esc(d.preview || "Leer")}</p><small>${when(d.updated)} · ${d.words} Wörter</small>
      <button class="del" data-del="${d.id}" aria-label="Löschen">${ico("delete")}</button></div>`).join("") : `<div class="empty">${ico("description")}<b>Noch kein Dokument</b><br>Leg mit „Neu“ los.</div>`}</div></div>
    <button class="btn pri fab" id="new">${ico("add")}Neu</button><input type="file" id="file" accept=".idadocs,.md,.markdown,.txt" hidden></div>`;
  $("#new").onclick = async () => { try { const d = await api("/docs", { method: "POST", body: { title: "Neues Dokument", content: "# Titel\n\nHier beginnt dein Text.\n" } }); open(d.id); } catch (e) { toast(e.message); } };
  $("#imp").onclick = () => $("#file").click();
  $("#file").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { const d = await api("/import", { method: "POST", body: { name: f.name, text: await f.text() } }); open(d.id); } catch (er) { toast(er.message); }
  };
  $("#list").onclick = async (e) => {
    const del = e.target.closest("[data-del]");
    if (del) { e.stopPropagation(); if (confirm("Dokument wirklich löschen?")) { await api("/docs/" + del.dataset.del, { method: "DELETE" }).catch((er) => toast(er.message)); localStorage.removeItem("docs.c." + del.dataset.del); home(); } return; }
    const c = e.target.closest("[data-id]"); if (c) open(c.dataset.id);
  };
}

// ---------------------------------------------------------------- Editor
async function open(id) {
  history.replaceState(null, "", "#/d/" + id);
  let d = null;
  const cache = JSON.parse(localStorage.getItem("docs.c." + id) || "null");
  try { d = await api("/docs/" + id); } catch (e) { if (!cache) { toast(e.message); return home(); } }
  if (cache?.dirty && (!d || cache.t > d.updated * 1000)) d = { id, title: cache.title, content: cache.content, settings: cache.settings, updated: cache.t / 1000, _local: true };
  st.doc = { id, title: d.title, content: d.content, settings: d.settings || {} };
  st.dirty = !!d._local; st.mpane = "src";
  app.innerHTML = `<div class="ed"><div class="bar"><button class="btn ic" id="back" aria-label="Zurück">${ico("arrow_back")}</button>
      <input class="title" id="title" maxlength="200" placeholder="Titel" value="${esc(st.doc.title)}"><span class="state" id="state"></span>
      <button class="btn ic" id="cfg" aria-label="Seiteneinstellungen">${ico("tune")}</button><button class="btn ic" id="more" aria-label="Mehr">${ico("more_vert")}</button></div>
    <div class="mtabs"><button data-m="src" class="on">Schreiben</button><button data-m="pv">Vorschau</button></div>
    <div class="split" data-m="src"><div class="card sr"><div class="tools" id="tools"></div><textarea class="src" id="src" spellcheck="true" placeholder="Schreib hier in Markdown …"></textarea></div>
      <div class="card pv"><div class="prevbar"><button class="btn sm on" id="vmode"></button><button class="btn sm" id="vdark"></button><span class="grow"></span><span class="meta" id="meta"></span></div><div class="prev" id="prev"></div></div></div></div>`;
  $("#src").value = st.doc.content;
  buildTools();
  $("#back").onclick = async () => { await flush(); home(); };
  $("#title").oninput = (e) => { st.doc.title = e.target.value; changed(); };
  $("#src").oninput = (e) => { st.doc.content = e.target.value; changed(); };
  $("#src").onkeydown = (e) => { if (e.key === "Tab") { e.preventDefault(); ins("  "); } };
  $("#cfg").onclick = settingsPanel;
  $("#more").onclick = (e) => moreMenu(e.currentTarget);
  $(".mtabs").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; st.mpane = b.dataset.m; $(".split").dataset.m = b.dataset.m; document.querySelectorAll(".mtabs button").forEach((x) => x.classList.toggle("on", x === b)); if (b.dataset.m === "pv") fit(); };
  $("#vmode").onclick = () => { prefs.mode = prefs.mode === "pages" ? "web" : "pages"; localStorage.setItem("docs.mode", prefs.mode); view(); layout(); };
  $("#vdark").onclick = () => { prefs.dark = !prefs.dark; localStorage.setItem("docs.dark", prefs.dark ? "1" : "0"); view(); layout(); };
  new ResizeObserver(fit).observe($("#prev"));
  view(); layout(); stateText(st.dirty ? "Lokal gesichert" : "Gespeichert");
  if (st.dirty) schedule();
}
function view() {
  $("#vmode").innerHTML = prefs.mode === "pages" ? `${ico("description")}Seiten` : `${ico("language")}Web`;
  $("#vdark").innerHTML = prefs.dark ? `${ico("dark_mode")}Dunkel` : `${ico("light_mode")}Hell`;
}
const stateText = (t) => { const s = $("#state"); if (s) s.textContent = t; };
function changed() {
  st.dirty = true; stateText("…");
  localStorage.setItem("docs.c." + st.doc.id, JSON.stringify({ title: st.doc.title, content: st.doc.content, settings: st.doc.settings, t: Date.now(), dirty: true }));
  clearTimeout(st.rtimer); st.rtimer = setTimeout(layout, 250);
  schedule();
}
function schedule() { clearTimeout(st.timer); st.timer = setTimeout(save, 900); }
async function save() {
  clearTimeout(st.timer);
  if (!st.doc || !st.dirty || st.saving) return;
  const { id, title, content, settings } = st.doc, t = Date.now();
  st.saving = true; stateText("Speichert …");
  try {
    await api("/docs/" + id, { method: "PUT", body: { title, content, settings: diff(settings) } });
    if (st.doc && st.doc.id === id && st.doc.content === content && st.doc.title === title) { st.dirty = false; localStorage.removeItem("docs.c." + id); stateText("Gespeichert"); }
    else schedule();
  } catch { stateText("Offline – lokal gesichert"); st.timer = setTimeout(save, 8000); }
  st.saving = false;
}
async function flush() { clearTimeout(st.rtimer); if (st.dirty) await save(); }
addEventListener("online", () => st.dirty && save());
document.addEventListener("visibilitychange", () => { if (document.hidden && st.dirty) save(); });

// ---------------------------------------------------------------- Werkzeugleiste
function buildTools() {
  const T = [["H1", "h1", "Überschrift 1"], ["H2", "h2", "Überschrift 2"], ["H3", "h3", "Überschrift 3"], "|", ["format_bold", "b", "Fett"], ["format_italic", "i", "Kursiv"], ["code", "code", "Code"], "|",
    ["format_list_bulleted", "ul", "Liste"], ["format_list_numbered", "ol", "Nummerierte Liste"], ["format_quote", "quote", "Zitat"], ["table", "table", "Tabelle"], ["link", "link", "Link"], ["image", "img", "Bild per Adresse"], ["footnote", "fn", "Fußnote"], "|",
    ["toc", "toc", "Inhaltsverzeichnis"], ["insert_page_break", "pb", "Seitenumbruch"], ["article", "title", "Titelblatt"], ["menu_book", "src", "Quellenverzeichnis"], ["verified_user", "decl", "Selbstständigkeitserklärung"]];
  $("#tools").innerHTML = T.map((t) => t === "|" ? "<i></i>" : /^H\d$/.test(t[0]) ? `<button data-a="${t[1]}" title="${t[2]}" aria-label="${t[2]}">${t[0]}</button>` : `<button data-a="${t[1]}" title="${t[2]}" aria-label="${t[2]}">${ico(t[0])}</button>`).join("");
  $("#tools").onclick = (e) => { const b = e.target.closest("[data-a]"); if (b) act(b.dataset.a); };
}
function ins(text, selStart, selEnd) {
  const t = $("#src"); t.focus();
  const a = t.selectionStart, b = t.selectionEnd;
  t.setRangeText(text, a, b, "end");
  if (selStart != null) t.setSelectionRange(a + selStart, a + selEnd);
  t.dispatchEvent(new Event("input"));
}
const wrap = (l, r, ph) => { const t = $("#src"), s = t.value.slice(t.selectionStart, t.selectionEnd) || ph; ins(l + s + r, l.length, l.length + s.length); };
function prefix(p) {
  const t = $("#src"); t.focus();
  let a = t.value.lastIndexOf("\n", t.selectionStart - 1) + 1; let e = t.value.indexOf("\n", t.selectionEnd); if (e < 0) e = t.value.length;
  const lines = t.value.slice(a, e).split("\n").map((l, i) => (p === "1." ? `${i + 1}. ` : p) + l.replace(/^(#{1,4}\s+|[-*]\s+|\d+\.\s+|>\s?)/, ""));
  t.setRangeText(lines.join("\n"), a, e, "end"); t.dispatchEvent(new Event("input"));
}
const block = (s) => { const t = $("#src"); const pre = t.selectionStart && t.value[t.selectionStart - 1] !== "\n" ? "\n\n" : t.selectionStart ? "\n" : ""; ins(pre + s + "\n"); };
function act(a) {
  const today = new Date().toLocaleDateString("de-DE", { day: "2-digit", month: "long", year: "numeric" });
  ({ h1: () => prefix("# "), h2: () => prefix("## "), h3: () => prefix("### "), b: () => wrap("**", "**", "fett"), i: () => wrap("*", "*", "kursiv"), code: () => wrap("`", "`", "code"),
    ul: () => prefix("- "), ol: () => prefix("1."), quote: () => prefix("> "), link: () => wrap("[", "](https://)", "Text"), img: () => ins("![Beschreibung](https://)"),
    fn: () => { const t = $("#src"), n = (t.value.match(/^\[\^\d+\]:/gm) || []).length + 1, pos = t.selectionEnd; t.setRangeText(`[^${n}]`, pos, pos, "end"); t.value = t.value.replace(/\s*$/, "") + `\n\n[^${n}]: Text der Fußnote.\n`; t.dispatchEvent(new Event("input")); },
    table: () => block("| Spalte 1 | Spalte 2 | Spalte 3 |\n|----------|:--------:|---------:|\n| Text     | Text     | Text     |\n"),
    toc: () => block("[TOC]\n"), pb: () => block("\\newpage\n"),
    title: () => block(`::: title\nTitel der Arbeit\n\nUntertitel oder Thema\n\nvon Vorname Nachname\n\nKlasse / Kurs · Schule\n\nBetreuende Lehrkraft: Name\n\nAbgabe: ${today}\n:::\n\n\\newpage\n`),
    src: () => block("# Quellenverzeichnis {-}\n\n- Nachname, Vorname: *Titel des Werks*. Ort: Verlag, Jahr.\n- Autor/Organisation: „Titel der Seite“. https://adresse.de (abgerufen am " + today + ").\n"),
    decl: () => block(`# Selbstständigkeitserklärung {-}\n\nHiermit erkläre ich, dass ich die vorliegende Arbeit selbstständig und nur mit den angegebenen Quellen und Hilfsmitteln angefertigt habe. Alle wörtlich oder sinngemäß übernommenen Stellen habe ich als solche gekennzeichnet.\n\nOrt, ${today}\n\n\n\n______________________________  \nUnterschrift\n`) })[a]?.();
}

// ---------------------------------------------------------------- Seiten
function vars(s) {
  const c = cfg(s), al = { justify: "justify", left: "left", center: "center", right: "right" }[c.align] || "justify";
  return `--doc-font:${FONTS[c.font] || FONTS.Arial};--fs:${c.size};--lh:${c.lineHeight};--ps:${c.paraSpacing};--al:${al};--h1:${c.h1};--h2:${c.h2};--h3:${c.h3}`;
}
let measure = null;
function layout() {
  const prev = $("#prev"); if (!prev || !st.doc) return;
  const c = cfg(st.doc.settings), { html, headings } = render(st.doc.content, { numbers: c.headingNumbers });
  if (prefs.mode === "web") {
    prev.innerHTML = `<div class="webdoc web doc ${prefs.dark ? "dark" : ""}" style="${vars(c)};--fs:${Math.max(c.size, 12)}">${html.replace(/ href="#h-[^"]*"/g, "")}</div>`;
    $("#meta").textContent = `${st.doc.content.trim() ? st.doc.content.trim().split(/\s+/).length : 0} Wörter`; fit(); return;
  }
  const cw = PAGE_W - c.marginLeft - c.marginRight, ch = PAGE_H - c.marginTop - c.marginBottom;
  if (cw < 40 || ch < 40) { prev.innerHTML = '<div class="empty">Die Ränder sind zu groß für DIN A4.</div>'; return; }
  const colCss = `width:${cw}mm;height:${ch}mm;column-width:${cw}mm;column-gap:${GAP}mm;`;
  measure ||= document.body.appendChild(el('<div class="measure doc"></div>'));
  measure.style.cssText = vars(c) + ";" + colCss;
  measure.innerHTML = html;
  const pitch = (cw + GAP) * PX;
  const count = () => Math.max(1, Math.round((measure.scrollWidth + GAP * PX) / pitch));
  const place = () => { const x0 = measure.getBoundingClientRect().left; const m = {}; headings.forEach((h) => { const n = measure.querySelector("#" + CSS.escape(h.id)); if (n) m[h.id] = Math.floor((n.getBoundingClientRect().left - x0 + 2) / pitch) + 1; }); return m; };
  let pages = place();
  if (measure.querySelector(".pg")) {   // Inhaltsverzeichnis: Seitenzahlen eintragen und danach noch einmal messen (Zeilen bleiben gleich lang)
    for (let pass = 0; pass < 2; pass++) { measure.querySelectorAll(".pg").forEach((p) => { p.textContent = pages[p.dataset.h] || ""; }); const np = place(); if (JSON.stringify(np) === JSON.stringify(pages)) break; pages = np; }
  }
  const n = count(), inner = measure.innerHTML.replace(/ id="h-[^"]*"/g, "").replace(/ href="#h-[^"]*"/g, "");
  const pos = { left: "left", center: "left:0;right:0;text-align:center", right: "right" }[c.pageNumberPos];
  const pn = (i) => !c.pageNumbers ? "" : `<div class="pgn" style="bottom:${Math.max(4, c.marginBottom / 2 - 2)}mm;${c.pageNumberPos === "center" ? pos : c.pageNumberPos === "left" ? `left:${c.marginLeft}mm` : `right:${c.marginRight}mm`}">${i + 1}</div>`;
  let out = "";
  for (let i = 0; i < n; i++) out += `<div class="sheet ${prefs.dark ? "dark" : ""}"><div class="clip" style="left:${c.marginLeft}mm;top:${c.marginTop}mm;width:${cw}mm;height:${ch}mm"><div class="flow doc" style="${vars(c)};${colCss}transform:translateX(-${i * (cw + GAP)}mm)">${inner}</div></div>${pn(i)}</div>`;
  const keep = prev.scrollTop;
  prev.innerHTML = `<div class="sheets">${out}</div>`;
  prev.scrollTop = keep;
  $("#meta").textContent = `${n} ${n === 1 ? "Seite" : "Seiten"} · ${st.doc.content.trim() ? st.doc.content.trim().split(/\s+/).length : 0} Wörter`;
  fit();
}
function fit() {
  const prev = $("#prev"), sh = prev?.querySelector(".sheets"); if (!sh) return;
  const w = prev.clientWidth - 24, full = PAGE_W * PX;
  sh.style.zoom = Math.min(1.4, Math.max(0.3, w / full));
  prev.style.overflowX = "hidden";
}

// ---------------------------------------------------------------- Einstellungen
function settingsPanel() {
  const s = cfg(st.doc.settings);
  const num = (k, label, step = 1, unit = "") => `<label class="fld"><span>${label}${unit ? ` <small>${unit}</small>` : ""}</span><input type="number" data-k="${k}" step="${step}" min="0" value="${s[k]}"></label>`;
  const sel = (k, label, opts) => `<label class="fld"><span>${label}</span><select data-k="${k}">${opts.map(([v, t]) => `<option value="${v}" ${s[k] === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>`;
  const chk = (k, label) => `<label class="fld"><span>${label}</span><input type="checkbox" data-k="${k}" ${s[k] ? "checked" : ""}></label>`;
  const scrim = el('<div class="scrim"></div>');
  const p = el(`<aside class="sheetp"><header>Seite &amp; Schrift<button class="btn ic" id="x" aria-label="Schließen">${ico("close")}</button></header><div class="body">
    <h3>SEITENRÄNDER (MM)</h3>${num("marginTop", "Oben", 0.5)}${num("marginBottom", "Unten", 0.5)}${num("marginLeft", "Links", 0.5)}${num("marginRight", "Rechts", 0.5)}
    <h3>TEXT</h3>${sel("font", "Schriftart", Object.keys(FONTS).map((f) => [f, f]))}${num("size", "Schriftgröße", 0.5, "pt")}${num("lineHeight", "Zeilenabstand", 0.05, "×")}${num("paraSpacing", "Abstand nach Absatz", 1, "pt")}
    ${sel("align", "Ausrichtung", [["justify", "Blocksatz"], ["left", "Linksbündig"], ["center", "Zentriert"], ["right", "Rechtsbündig"]])}
    <h3>ÜBERSCHRIFTEN</h3>${num("h1", "Überschrift 1", 0.5, "pt")}${num("h2", "Überschrift 2", 0.5, "pt")}${num("h3", "Überschrift 3", 0.5, "pt")}${chk("headingNumbers", "Nummerieren (1, 1.1 …)")}
    <h3>SEITENZAHL</h3>${chk("pageNumbers", "Anzeigen")}${sel("pageNumberPos", "Position", [["center", "Unten Mitte"], ["right", "Unten rechts"], ["left", "Unten links"]])}
    <div class="row" style="margin-top:18px"><button class="btn sm" id="reset">${ico("restart_alt")}Auf Standard zurücksetzen</button></div>
    <p class="fld"><small>Standard: DIN A4, Ränder 30/20/25/20 mm, ${DEFAULTS.font} ${DEFAULTS.size} pt, Zeilenabstand 1,5, Blocksatz. In der .idadocs-Datei stehen nur Abweichungen davon.</small></p></div></aside>`);
  document.body.append(scrim, p);
  const close = () => { scrim.remove(); p.remove(); };
  scrim.onclick = $("#x", p).onclick = close;
  p.addEventListener("input", (e) => {
    const k = e.target.dataset.k; if (!k) return;
    const v = e.target.type === "checkbox" ? e.target.checked : e.target.type === "number" ? parseFloat(e.target.value) : e.target.value;
    if (typeof v === "number" && (isNaN(v) || v < 0)) return;
    st.doc.settings = { ...st.doc.settings, [k]: v }; changed();
  });
  $("#reset", p).onclick = () => { st.doc.settings = {}; changed(); close(); settingsPanel(); };
}

// ---------------------------------------------------------------- Menü, Export
function download(name, text, type) { const a = el(`<a download="${esc(name)}"></a>`); a.href = URL.createObjectURL(new Blob([text], { type })); a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); }
const fname = () => (st.doc.title || "Dokument").replace(/[^\p{L}\p{N} _-]+/gu, "").trim() || "Dokument";
function moreMenu(btn) {
  const r = btn.getBoundingClientRect();
  const m = el(`<div class="menu" style="top:${r.bottom + 6}px;right:12px"><button data-x="pdf">${ico("picture_as_pdf")}Als PDF speichern</button><button data-x="md">${ico("markdown")}Als .md herunterladen</button>
    <button data-x="ida">${ico("download")}Als .idadocs herunterladen</button><button data-x="del">${ico("delete")}Dokument löschen</button></div>`);
  const scrim = el('<div class="scrim" style="background:none"></div>');
  document.body.append(scrim, m);
  const close = () => { m.remove(); scrim.remove(); };
  scrim.onclick = close;
  m.onclick = async (e) => {
    const x = e.target.closest("[data-x]")?.dataset.x; if (!x) return; close();
    if (x === "md") download(fname() + ".md", st.doc.content, "text/markdown");
    if (x === "ida") download(fname() + ".idadocs", JSON.stringify({ format: "idadocs", version: 1, id: st.doc.id, title: st.doc.title, settings: diff(st.doc.settings), content: st.doc.content }, null, 1), "application/json");
    if (x === "pdf") printPdf();
    if (x === "del" && confirm("Dokument wirklich löschen?")) { await api("/docs/" + st.doc.id, { method: "DELETE" }).catch((er) => toast(er.message)); localStorage.removeItem("docs.c." + st.doc.id); st.dirty = false; home(); }
  };
}
function printPdf() {
  // Immer die weißen Seiten drucken (egal ob Web-Modus oder dunkles Papier): kurz im Seitenmodus neu aufbauen
  const old = { ...prefs }; prefs.mode = "pages"; prefs.dark = false; layout();
  let root = $("#printroot") || document.body.appendChild(el('<div id="printroot"></div>'));
  root.innerHTML = $(".sheets").outerHTML; root.querySelector(".sheets").style.zoom = 1;
  const t = document.title; document.title = fname();
  const done = () => { document.title = t; root.innerHTML = ""; Object.assign(prefs, old); layout(); removeEventListener("afterprint", done); };
  addEventListener("afterprint", done);
  setTimeout(() => print(), 50);
}

// ---------------------------------------------------------------- Start
document.addEventListener("click", (e) => { if (e.target.closest(".sheets a, .webdoc a[href^='#']")) e.preventDefault(); });
const m = location.hash.match(/^#\/d\/([a-z0-9]+)/);
m ? open(m[1]) : home();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
