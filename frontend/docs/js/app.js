// IDA Docs – Oberfläche: Dokumentliste, Markdown-Editor, Seitenvorschau in DIN A4, Einstellungen, Export.
import { render, bibEntry, citeShort, sortSources, stripComment, maskNotes } from "./markdown.js";
import { highlightLines } from "./highlight.js";
import { createIda } from "./ida.js";
import { suggest } from "./complete.js";

// Muss zu DEFAULTS in dokumente/app/store.py passen: nur Abweichungen davon werden in .idadocs gespeichert.
const DEFAULTS = { marginTop: 25, marginBottom: 20, marginLeft: 30, marginRight: 20, font: "Arial", size: 11, lineHeight: 1.5, paraSpacing: 6,
  align: "justify", h1: 16, h2: 14, h3: 12, headingNumbers: true, pageNumbers: true, pageNumberPos: "center", citeStyle: "kurz", bibStyle: "din" };
const FONTS = { Arial: 'Arial, "Liberation Sans", Helvetica, sans-serif', "Times New Roman": '"Times New Roman", "Liberation Serif", Times, serif',
  Calibri: 'Calibri, Carlito, "Segoe UI", sans-serif', Georgia: "Georgia, serif", Verdana: "Verdana, DejaVu Sans, sans-serif", "Courier New": '"Courier New", monospace' };
const PAGE_W = 210, PAGE_H = 297, GAP = 10, PX = 96 / 25.4;
const $ = (s, r = document) => r.querySelector(s);
const el = (h) => { const t = document.createElement("template"); t.innerHTML = h.trim(); return t.content.firstElementChild; };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ico = (n) => `<span class="ms">${n}</span>`;
const prefs = { mode: localStorage.getItem("docs.mode") || "pages", dark: localStorage.getItem("docs.dark") === "1", split: parseFloat(localStorage.getItem("docs.split")) || 0.5, hidePv: localStorage.getItem("docs.hidePv") === "1", edFs: parseInt(localStorage.getItem("docs.edfs")) || 14 };
const st = { pageStarts: {}, res: { used: [], missing: [] }, doc: null, mpane: "src", timer: 0, rtimer: 0, saving: false, dirty: false, rev: 0 };
const app = $("#app");
const ida = createIda({ st, $, el, esc, ico, toast, api, switchTab: (n) => switchTab(n) });

async function api(path, opt = {}) {
  const body = opt.body ? JSON.stringify(opt.body) : undefined;
  // keepalive (speichert auch beim Schließen) lehnt der Browser über 64 KB ab: Große Dokumente gehen deshalb normal raus
  const r = await fetch("/api/v1" + path, { keepalive: opt.method === "PUT" && new Blob([body || ""]).size < 60000, ...opt, headers: { "content-type": "application/json" }, body });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.status === 204 ? null : r.json();
}
function toast(t) { const b = $("#toast"); b.textContent = t; b.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => b.classList.remove("show"), 2600); }
const cfg = (s) => ({ ...DEFAULTS, ...(s || {}) });
const diff = (s) => Object.fromEntries(Object.entries(s || {}).filter(([k, v]) => k in DEFAULTS && v !== DEFAULTS[k]));
const when = (t) => new Date(t * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });

// ---------------------------------------------------------------- Startseite
const browserTitle = () => { document.title = st.doc?.title.trim() || "IDA Docs"; };
async function home() {
  history.replaceState(null, "", "#/");
  st.doc = null; browserTitle(); ida.stop();
  let docs = [], offline = false;
  await syncPending().catch(() => {});
  try { docs = await api("/docs"); localStorage.setItem("docs.list", JSON.stringify(docs)); }
  catch { offline = true; docs = JSON.parse(localStorage.getItem("docs.list") || "[]"); }
  app.innerHTML = `<div class="home"><div class="home-in"><h1>Docs</h1><p class="sub">Aufsätze und Projektarbeiten in Markdown, auf DIN A4.</p>
    <div class="row"><button class="btn sm" id="imp">${ico("upload_file")}Importieren</button><a class="btn sm" href="/api/v1/backup" download>${ico("folder_zip")}Backup (ZIP)</a>
      ${offline ? `<span class="chip bad">${ico("cloud_off")}Offline – nur gemerkte Liste</span>` : ""}</div>
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
  if (cache?.dirty && (!d || cache.t > d.updated * 1000)) d = { id, title: cache.title, content: cache.content, settings: cache.settings, sources: cache.sources, tabs: cache.tabs, tabName: cache.tabName, updated: cache.t / 1000, _local: true };
  const tabs = [{ id: "main", title: d.tabName || "Text", content: d.content }, ...(d.tabs || []).map((t) => ({ id: t.id, title: t.title, content: t.content }))];
  const tab = Math.min(tabs.length - 1, Math.max(0, parseInt(localStorage.getItem("docs.tab." + id)) || 0));
  st.doc = { id, title: d.title, content: tabs[tab].content, settings: d.settings || {}, sources: d.sources || cache?.sources || [], tabs, tab };
  st.dirty = !!d._local; st.mpane = "src"; st.rev = 0; browserTitle();
  app.innerHTML = `<div class="ed"><div class="bar"><button class="btn ic" id="back" aria-label="Zurück">${ico("arrow_back")}</button>
      <input class="title" id="title" maxlength="200" placeholder="Titel" value="${esc(st.doc.title)}"><span class="state" id="state"></span>
      <button class="btn ic" id="aib" aria-label="Mit Ida chatten" title="Mit Ida chatten">${ico("auto_awesome")}</button><button class="btn ic" id="pvt"></button><button class="btn ic" id="cfg" aria-label="Seiteneinstellungen">${ico("tune")}</button><button class="btn ic" id="more" aria-label="Mehr">${ico("more_vert")}</button></div>
    <div class="mtabs"><button data-m="src" class="on">Schreiben</button><button data-m="pv">Vorschau</button><button data-m="ai">Ida</button></div>
    <div class="tabsbar" id="tabs" role="tablist"></div>
    <div class="split" data-m="src"><div class="card sr"><div class="tools" id="tools"></div><div class="srcwrap"><div class="hl" id="hl" aria-hidden="true"><div></div></div><textarea class="src" id="src" spellcheck="true" placeholder="Schreib hier in Markdown …"></textarea><div class="mirror" id="mirror" aria-hidden="true"></div><div class="marks" id="marks" aria-hidden="true"><div></div></div>${ida.pill}</div></div>
      <div class="gutter" id="gut" role="separator" aria-orientation="vertical" aria-label="Breite ändern" title="Ziehen = Breite ändern, Doppelklick = halb und halb"></div>
      <div class="card pv"><div class="prevbar"><button class="btn sm on" id="vmode"></button><button class="btn sm" id="vdark"></button><button class="btn sm" id="vopen" title="Vorschau in neuem Browser-Tab öffnen">${ico("open_in_new")}Neuer Tab</button><span class="grow"></span><span class="meta" id="meta"></span></div><div class="prev" id="prev"></div></div>${ida.html}</div></div>`;
  $("#src").value = st.doc.content;
  $(".srcwrap").style.setProperty("--ed-fs", prefs.edFs + "px");
  $(".srcwrap").addEventListener("wheel", (e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); edZoom(e.deltaY < 0 ? 1 : -1); } }, { passive: false });
  hlLines = []; drawHl();
  buildTools(); buildTabs(); splitSetup(); ida.setup(id);
  $("#tabs").onclick = tabClick;
  $("#back").onclick = async () => { await flush(); home(); };
  $("#title").oninput = (e) => { st.doc.title = e.target.value; browserTitle(); changed(); };
  $("#src").oninput = (e) => { st.doc.content = e.target.value; changed(); };
  $("#src").addEventListener("scroll", () => { const y = `translateY(${-$("#src").scrollTop}px)`; $("#marks").firstChild.style.transform = y; $("#hl").firstChild.style.transform = y; });
  new ResizeObserver(() => drawMarks()).observe($("#src"));
  slashSetup();
  selMenuSetup();
  histSetup();
  $("#cfg").onclick = settingsPanel;
  $("#more").onclick = (e) => moreMenu(e.currentTarget);
  $(".mtabs").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; st.mpane = b.dataset.m; $(".split").dataset.m = b.dataset.m; document.querySelectorAll(".mtabs button").forEach((x) => x.classList.toggle("on", x === b)); if (b.dataset.m === "pv") fit(); else if (b.dataset.m === "ai") ida.shown(); else drawMarks(); };
  $("#vopen").onclick = () => window.open(`#/v/${id}/${st.doc.tabs[st.doc.tab].id}`, "_blank");
  previewBar(); stateText(st.dirty ? "Lokal gesichert" : "Gespeichert");
  if (st.dirty) schedule();
}
function previewBar() {
  $("#vmode").onclick = () => { prefs.mode = prefs.mode === "pages" ? "web" : "pages"; localStorage.setItem("docs.mode", prefs.mode); view(); layout(); };
  $("#vdark").onclick = () => { prefs.dark = !prefs.dark; localStorage.setItem("docs.dark", prefs.dark ? "1" : "0"); view(); layout(); };
  new ResizeObserver(fit).observe($("#prev"));
  view(); layout();
}
// Nur die Vorschau eines Tabs in einem eigenen Browser-Tab. Sie speichert nie und läuft live mit, solange im Editor geschrieben wird.
const live = "BroadcastChannel" in window ? new BroadcastChannel("docs.live") : null;
async function viewer(id, tabId) {
  const cache = JSON.parse(localStorage.getItem("docs.c." + id) || "null");
  let d = null;
  try { d = await api("/docs/" + id); } catch (e) { if (!cache) { toast(e.message); return home(); } }
  if (cache?.dirty && (!d || cache.t > d.updated * 1000)) d = cache;
  const apply = (x) => {
    const tabs = [{ id: "main", title: x.tabName || "Text", content: x.content }, ...(x.tabs || [])], t = tabs.find((y) => y.id === tabId) || tabs[0];
    st.doc = { id, title: x.title, content: t.content, settings: x.settings || {}, sources: x.sources || [], tabs, tab: tabs.indexOf(t) };
    browserTitle();
  };
  apply(d);
  app.innerHTML = `<div class="ed viewer"><div class="card pv"><div class="prevbar"><button class="btn sm on" id="vmode"></button><button class="btn sm" id="vdark"></button><span class="grow"></span><span class="meta" id="meta"></span></div><div class="prev" id="prev"></div></div></div>`;
  previewBar();
  if (live) live.onmessage = (e) => { if (e.data.docId === id) { apply(e.data); layout(); } };
}
function view() {
  $("#vmode").innerHTML = prefs.mode === "pages" ? `${ico("description")}Seiten` : `${ico("language")}Web`;
  $("#vdark").innerHTML = prefs.dark ? `${ico("dark_mode")}Dunkel` : `${ico("light_mode")}Hell`;
}
const stateText = (t) => { const s = $("#state"); if (s) s.textContent = t; };
// Was gespeichert wird: "content" ist immer der erste Tab, die anderen stehen in "tabs"
function payload() {
  const d = st.doc, [main, ...rest] = d.tabs;
  return { title: d.title, content: main.content, settings: diff(d.settings), sources: d.sources, tabs: rest.map(({ id, title, content }) => ({ id, title, content })), tabName: main.title };
}
function changed() {
  ghost = null; st.dirty = true; st.rev++; stateText("…"); drawHl();
  st.doc.tabs[st.doc.tab].content = st.doc.content;
  localStorage.setItem("docs.c." + st.doc.id, JSON.stringify({ ...payload(), t: Date.now(), dirty: true }));
  clearTimeout(st.rtimer); st.rtimer = setTimeout(() => { layout(); live?.postMessage({ docId: st.doc.id, ...payload() }); }, 250);
  schedule();
}
function schedule() { clearTimeout(st.timer); st.timer = setTimeout(save, 700); }
async function save() {
  clearTimeout(st.timer);
  if (!st.doc || !st.dirty || st.saving) return;
  const id = st.doc.id, rev = st.rev, body = payload();
  st.saving = true; stateText("Speichert …");
  try {
    await api("/docs/" + id, { method: "PUT", body });
    if (st.doc && st.doc.id === id && st.rev === rev) { st.dirty = false; localStorage.removeItem("docs.c." + id); stateText("Gespeichert"); }
    else schedule();
  } catch { stateText("Offline – lokal gesichert"); st.timer = setTimeout(save, 8000); }
  st.saving = false;
}
async function flush() { clearTimeout(st.rtimer); if (st.dirty) await save(); }
addEventListener("online", () => { if (st.dirty) save(); syncPending(); });
document.addEventListener("visibilitychange", () => { if (document.hidden && st.dirty) save(); });
addEventListener("pagehide", () => { if (st.dirty) save(); });
// Lokal gesicherte, noch nicht hochgeladene Dokumente (z. B. nach Offline-Schreiben) nachliefern – auch wenn man sie nicht wieder öffnet
async function syncPending() {
  for (const k of Object.keys(localStorage).filter((x) => x.startsWith("docs.c."))) {
    const id = k.slice(7), c = JSON.parse(localStorage.getItem(k) || "null");
    if (!c?.dirty || st.doc?.id === id) continue;
    try { await api("/docs/" + id, { method: "PUT", body: { title: c.title, content: c.content, settings: diff(c.settings), sources: c.sources, tabs: c.tabs, tabName: c.tabName } }); if (JSON.parse(localStorage.getItem(k) || "null")?.t === c.t) localStorage.removeItem(k); }
    catch (e) { if (/nicht gefunden/i.test(e.message)) localStorage.removeItem(k); }
  }
}

// ---------------------------------------------------------------- Tabs: mehrere getrennte Texte in einem Dokument (Seiteneinstellungen und Quellen gelten für alle)
function buildTabs() {
  const d = st.doc;
  $("#tabs").innerHTML = d.tabs.map((t, i) => `<div class="tab ${i === d.tab ? "on" : ""}" role="tab" aria-selected="${i === d.tab}" data-t="${i}"><button class="tn" title="${i === d.tab ? "Klicken zum Umbenennen" : esc(t.title)}">${esc(t.title)}</button>${i === d.tab && d.tabs.length > 1 ? `<button class="tx" aria-label="Tab löschen" title="Tab löschen">${ico("close")}</button>` : ""}</div>`).join("") + `<button class="tadd" aria-label="Neuer Tab" title="Neuer Tab">${ico("add")}</button>`;
}
function showTab(n, top = 0) {
  const d = st.doc, ta = $("#src"), t = d.tabs[n];
  d.tab = n; d.content = t.content; ta.value = t.content;
  hist.load?.(t.undo);
  if (t.view) ta.setSelectionRange(t.view.s, t.view.e); else ta.setSelectionRange(0, 0);
  localStorage.setItem("docs.tab." + d.id, n);
  buildTabs(); drawHl(); ida.tabChanged();
  ta.scrollTop = t.view?.top || 0; ta.dispatchEvent(new Event("scroll"));
  clearTimeout(st.rtimer); layout();
  $("#prev").scrollTop = t.view?.prev || top;
}
function switchTab(n) {
  const d = st.doc, ta = $("#src"); if (n === d.tab || !d.tabs[n]) return;
  Object.assign(d.tabs[d.tab], { content: ta.value, view: { top: ta.scrollTop, s: ta.selectionStart, e: ta.selectionEnd, prev: $("#prev").scrollTop }, undo: hist.save?.() });
  showTab(n);
}
function tabClick(e) {
  const d = st.doc;
  if (e.target.closest(".tadd")) {
    if (d.tabs.length >= 20) return toast("Mehr als 20 Tabs gehen nicht");
    let k = d.tabs.length + 1; while (d.tabs.some((t) => t.title === `Tab ${k}`)) k++;
    d.tabs.push({ id: Date.now().toString(36), title: `Tab ${k}`, content: "" });
    switchTab(d.tabs.length - 1); changed(); $("#src").focus(); return;
  }
  const box = e.target.closest("[data-t]"); if (!box) return;
  const n = +box.dataset.t, t = d.tabs[n];
  if (e.target.closest(".tx")) {
    if (t.content.trim() && !confirm(`Tab „${t.title}“ wirklich löschen? Der Text darin ist dann weg.`)) return;
    d.tabs.splice(n, 1); showTab(Math.min(n, d.tabs.length - 1)); changed(); return;
  }
  if (n !== d.tab) return switchTab(n);
  renameTab(t, box);
}
function renameTab(t, anchor) {
  const r = anchor.getBoundingClientRect(), scrim = el('<div class="scrim" style="background:none"></div>');
  const m = el(`<form class="tabname" style="top:${r.bottom + 6}px;left:${Math.max(8, Math.min(r.left, innerWidth - 298))}px"><label for="tabn">Name des Tabs</label><input id="tabn" maxlength="60" autocomplete="off" value="${esc(t.title)}"><div class="row"><button type="button" class="btn sm" id="tabc">Abbrechen</button><button class="btn sm pri">Speichern</button></div></form>`);
  document.body.append(scrim, m);
  const input = $("#tabn", m), close = () => { scrim.remove(); m.remove(); $("#src").focus(); };
  scrim.onclick = $("#tabc", m).onclick = close;
  m.onkeydown = (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } };
  m.onsubmit = (e) => { e.preventDefault(); const name = input.value.trim(); if (!name) return input.focus(); t.title = name; close(); buildTabs(); changed(); };
  input.focus(); input.select();
}

// ---------------------------------------------------------------- Schriftgröße nur im Editor (Strg + / Strg - / Strg 0, Strg + Mausrad); die Vorschau bleibt, wie sie ist
function edZoom(step) {
  const ta = $("#src"), old = prefs.edFs, fs = step === 0 ? 14 : Math.min(32, Math.max(10, old + step)); if (!ta || fs === old) return;
  const y = ta.scrollTop * fs / old;
  prefs.edFs = fs; localStorage.setItem("docs.edfs", fs);
  $(".srcwrap").style.setProperty("--ed-fs", fs + "px");
  drawMarks(); ta.scrollTop = y; ta.dispatchEvent(new Event("scroll"));
}
document.addEventListener("keydown", (e) => {   // im ganzen Editor abfangen, sonst vergrößert der Browser die komplette Seite
  if (!(e.ctrlKey || e.metaKey) || e.altKey || !$("#src")) return;
  const step = { "+": 1, "=": 1, "-": -1, 0: 0 }[e.key];
  if (step !== undefined) { e.preventDefault(); edZoom(step); }
});

// ---------------------------------------------------------------- Breite von Editor und Vorschau
function applySplit() {
  const sp = $(".split"); sp.style.setProperty("--spf", prefs.split); sp.classList.toggle("nopv", prefs.hidePv);
  const b = $("#pvt"), label = prefs.hidePv ? "Vorschau einblenden" : "Vorschau ausblenden";
  b.innerHTML = ico(prefs.hidePv ? "right_panel_open" : "right_panel_close"); b.title = label; b.setAttribute("aria-label", label);
  localStorage.setItem("docs.split", prefs.split); localStorage.setItem("docs.hidePv", prefs.hidePv ? "1" : "0");
}
function splitSetup() {
  const g = $("#gut"), sp = $(".split");
  applySplit();
  $("#pvt").onclick = () => { prefs.hidePv = !prefs.hidePv; applySplit(); layout(); };
  g.ondblclick = () => { prefs.split = 0.5; applySplit(); };
  g.onpointerdown = (e) => {
    e.preventDefault(); g.setPointerCapture(e.pointerId); g.classList.add("on");
    const r = sp.getBoundingClientRect(), w = r.width - (sp.classList.contains("aion") ? 388 : 0); let raw = prefs.split;
    g.onpointermove = (m) => { raw = (m.clientX - r.left) / w; prefs.split = Math.min(0.85, Math.max(0.2, raw)); sp.style.setProperty("--spf", prefs.split); };
    g.onpointerup = g.onpointercancel = () => {
      g.onpointermove = g.onpointerup = g.onpointercancel = null; g.classList.remove("on");
      if (raw > 0.92) { prefs.hidePv = true; prefs.split = 0.5; }   // ganz nach rechts gezogen: Vorschau zu
      applySplit();
    };
  };
}

// ---------------------------------------------------------------- Werkzeugleiste
function buildTools() {
  const T = [["undo", "undo", "Rückgängig (Strg+Z)"], ["redo", "redo", "Wiederholen (Strg+Y)"], "|", ["H1", "h1", "Überschrift 1 (Strg+Alt+1)"], ["H2", "h2", "Überschrift 2 (Strg+Alt+2)"], ["H3", "h3", "Überschrift 3 (Strg+Alt+3)"], "|", ["format_bold", "b", "Fett (Strg+B)"], ["format_italic", "i", "Kursiv (Strg+I)"], ["code", "code", "Code (Strg+E)"], "|",
    ["format_align_left", "al", "Linksbündig"], ["format_align_center", "ac", "Zentriert"], ["format_align_right", "ar", "Rechtsbündig"], "|",
    ["format_list_bulleted", "ul", "Liste"], ["format_list_numbered", "ol", "Nummerierte Liste"], ["format_quote", "quote", "Zitat"], ["table", "table", "Tabelle"], ["link", "link", "Link (Strg+K)"], ["image", "img", "Bild per Adresse"], ["superscript", "fn", "Fußnote"], "|",
    ["sticky_note_2", "notes", "Notizen & Entwürfe (Strg+M = Notiz einfügen)"], ["toc", "toc", "Inhaltsverzeichnis"], ["insert_page_break", "pb", "Seitenumbruch"], ["article", "title", "Titelblatt"], ["library_books", "lib", "Quellen (Archiv)"], ["menu_book", "src", "Quellenverzeichnis einfügen"], ["verified_user", "decl", "Selbstständigkeitserklärung"]];
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

// ---------------------------------------------------------------- Format-Menü über markiertem Text
const SELFMT = [["format_bold", "Fett (Strg+B)", "b"], ["format_italic", "Kursiv (Strg+I)", "i"], ["format_strikethrough", "Durchgestrichen (Strg+Shift+X)", "s"], ["code", "Code (Strg+E)", "code"], ["link", "Link (Strg+K)", "link"], "|", ["comment", "Auskommentieren (Strg+/)", "cmt"], ["sticky_note_2", "Notiz (Strg+M)", "note"], "|", ["auto_awesome", "Ida fragen (Strg+J)", "ida"]];
function selToggle(l, r) {   // Markierung mit l…r umschließen – oder wieder entfernen, wenn sie schon so umschlossen ist
  const t = $("#src"); let a = t.selectionStart, b = t.selectionEnd; const v = t.value;
  if (v.slice(a - l.length, a) === l && v.slice(b, b + r.length) === r) { t.setRangeText(v.slice(a, b), a - l.length, b + r.length, "select"); }
  else if (v.slice(a, a + l.length) === l && v.slice(b - r.length, b) === r && b - a >= l.length + r.length) { t.setRangeText(v.slice(a + l.length, b - r.length), a, b, "select"); }
  else t.setRangeText(l + v.slice(a, b) + r, a, b, "end"), t.setSelectionRange(a + l.length, b + l.length);
  t.dispatchEvent(new Event("input"));
}
function selComment() {   // jede markierte Zeile mit ## versehen bzw. das ## wieder entfernen
  const t = $("#src"), v = t.value; const a = v.lastIndexOf("\n", t.selectionStart - 1) + 1; let e = v.indexOf("\n", t.selectionEnd); if (e < 0) e = v.length;
  const lines = v.slice(a, e).split("\n"), done = lines.filter((l) => /^##(?![#\s])/.test(l)), free = lines.filter((l) => l.trim() && !/^[#\s]/.test(l));
  const off = done.length && !free.length;
  if (!off && !free.length) return toast("Überschriften und eingerückte Zeilen lassen sich so nicht auskommentieren");
  t.setRangeText(lines.map((l) => off ? l.replace(/^##(?![#\s])/, "") : l.trim() && !/^[#\s]/.test(l) ? "##" + l : l).join("\n"), a, e, "select");
  t.dispatchEvent(new Event("input"));
}
function selLink() {   // [Text](https://) – Adresse ist danach markiert; ohne Markierung steht "Text" als Platzhalter da
  const t = $("#src"), a = t.selectionStart, b = t.selectionEnd, s = t.value.slice(a, b);
  t.setRangeText("[" + s + "](https://)", a, b, "end");
  if (s) t.setSelectionRange(t.selectionEnd - 9, t.selectionEnd - 1); else t.setSelectionRange(a + 1, a + 1);
  t.dispatchEvent(new Event("input"));
}
function fmtKey(l, r) {   // Tastenkürzel: mit Markierung umschließen/lösen, ohne Markierung auf-, beim zweiten Druck wieder zumachen
  const t = $("#src"), a = t.selectionStart, v = t.value;
  if (t.selectionEnd > a) return selToggle(l, r);
  const inside = v.slice(a - l.length, a) === l && v.slice(a, a + r.length) === r && (l.length > 1 || (v[a - 2] !== l && v[a + 1] !== r));
  if (inside) t.setSelectionRange(a + r.length, a + r.length);   // Cursor steht zwischen den Zeichen: einfach raus ans Ende
  else { t.setRangeText(l + r, a, a, "end"); t.setSelectionRange(a + l.length, a + l.length); t.dispatchEvent(new Event("input")); }
}
const KEYS = { b: () => fmtKey("**", "**"), i: () => fmtKey("*", "*"), e: () => fmtKey("`", "`"), k: selLink, m: () => fmtKey("[[Notiz: ", "]]"), "/": selComment, j: () => $("#aipill")?.click() };
document.addEventListener("keydown", (e) => {   // Strg+B Fett · Strg+I Kursiv · Strg+E Code · Strg+Shift+X Durchgestrichen · Strg+K Link · Strg+M Notiz · Strg+/ Kommentar · Strg+J Ida · Strg+Alt+1–3 Überschrift
  const t = $("#src"); if (!t || document.activeElement !== t || !(e.ctrlKey || e.metaKey)) return;
  const k = e.key.toLowerCase();
  if (e.altKey && !e.shiftKey && /^Digit[123]$/.test(e.code)) { e.preventDefault(); return prefix("#".repeat(+e.code.slice(5)) + " "); }
  if (e.altKey) return;
  if (e.shiftKey && k === "x") { e.preventDefault(); return fmtKey("~~", "~~"); }
  if (!e.shiftKey && KEYS[k]) { e.preventDefault(); KEYS[k](); }
});
function selMenuSetup() {
  const wrapEl = $(".srcwrap"), t = $("#src"), box = el(`<div class="selmenu" hidden>${SELFMT.map((x) => x === "|" ? "<i></i>" : `<button data-f="${x[2]}" title="${x[1]}" aria-label="${x[1]}">${ico(x[0])}</button>`).join("")}</div>`);
  wrapEl.append(box);
  const gauge = el('<div class="mirror" aria-hidden="true"></div>'); wrapEl.append(gauge);
  const hide = () => { box.hidden = true; };
  function show() {
    if (document.activeElement !== t || t.selectionEnd <= t.selectionStart || matchMedia("(pointer: coarse)").matches) return hide();
    gauge.style.width = t.clientWidth + "px"; gauge.innerHTML = esc(t.value.slice(0, t.selectionStart)) + '<span id="selpt">\u200b</span>';
    const pt = gauge.querySelector("#selpt"), y = pt.offsetTop - t.scrollTop, x = pt.offsetLeft;
    box.hidden = false;
    const w = box.offsetWidth, h = box.offsetHeight, top = y - h - 6 < 4 ? y + 28 : y - h - 6;
    if (top > t.clientHeight - h) return hide();
    box.style.top = top + "px"; box.style.left = Math.max(8, Math.min(x, t.clientWidth - w - 8)) + "px";
  }
  for (const ev of ["mouseup", "keyup"]) t.addEventListener(ev, () => setTimeout(show));
  for (const ev of ["input", "blur", "scroll"]) t.addEventListener(ev, hide);
  t.addEventListener("mousedown", hide);
  box.onmousedown = (e) => e.preventDefault();
  box.onclick = (e) => {
    const b = e.target.closest("[data-f]"); if (!b) return; const f = b.dataset.f; t.focus();
    if (f === "b") selToggle("**", "**"); else if (f === "i") selToggle("*", "*"); else if (f === "s") selToggle("~~", "~~"); else if (f === "code") selToggle("`", "`");
    else if (f === "link") selLink();
    else if (f === "cmt") selComment(); else if (f === "note") selToggle("[[Notiz: ", "]]");
    else if (f === "ida") { $("#aipill")?.click(); }
    hide();
  };
}
function align(kind) {   // markierten Text (oder Platzhalter) in einen ausgerichteten Block setzen
  const t = $("#src"), sel = t.value.slice(t.selectionStart, t.selectionEnd).replace(/^:::\s*\w+\n|\n:::\s*$/g, "") || "Text";
  const pre = t.selectionStart && t.value[t.selectionStart - 1] !== "\n" ? "\n\n" : t.selectionStart ? "\n" : "";
  ins(`${pre}::: ${kind}\n${sel}\n:::\n`);
}
const block = (s) => { const t = $("#src"); const pre = t.selectionStart && t.value[t.selectionStart - 1] !== "\n" ? "\n\n" : t.selectionStart ? "\n" : ""; ins(pre + s + "\n"); };
function act(a) {
  const today = new Date().toLocaleDateString("de-DE", { day: "2-digit", month: "long", year: "numeric" });
  ({ undo: () => hist.undo?.(), redo: () => hist.redo?.(), h1: () => prefix("# "), h2: () => prefix("## "), h3: () => prefix("### "), b: () => wrap("**", "**", "fett"), i: () => wrap("*", "*", "kursiv"), code: () => wrap("`", "`", "code"),
    ul: () => prefix("- "), ol: () => prefix("1."), quote: () => prefix("> "), link: () => wrap("[", "](https://)", "Text"), img: () => ins("![Beschreibung](https://)"),
    fn: () => { const t = $("#src"), n = (t.value.match(/^\[\^\d+\]:/gm) || []).length + 1, pos = t.selectionEnd; t.setRangeText(`[^${n}]`, pos, pos, "end"); t.value = t.value.replace(/\s*$/, "") + `\n\n[^${n}]: Text der Fußnote.\n`; t.dispatchEvent(new Event("input")); },
    table: () => block("| Spalte 1 | Spalte 2 | Spalte 3 |\n|----------|:--------:|---------:|\n| Text     | Text     | Text     |\n"),
    notes: notesPanel, lib: sourcesPanel, al: () => align("left"), ac: () => align("center"), ar: () => align("right"), toc: () => block("[TOC]\n"), pb: () => block("\\newpage\n"),
    title: () => block(`::: title\nTitel der Arbeit\n\nUntertitel oder Thema\n\nvon Vorname Nachname\n\nKlasse / Kurs · Schule\n\nBetreuende Lehrkraft: Name\n\nAbgabe: ${today}\n:::\n\n\\newpage\n`),
    src: () => block("[QUELLEN]\n"),
    decl: () => block(`# Selbstständigkeitserklärung {-}\n\nHiermit erkläre ich, dass ich die vorliegende Arbeit selbstständig und nur mit den angegebenen Quellen und Hilfsmitteln angefertigt habe. Alle wörtlich oder sinngemäß übernommenen Stellen habe ich als solche gekennzeichnet.\n\nOrt, ${today}\n\n\n\n______________________________  \nUnterschrift\n`) })[a]?.();
}

// ---------------------------------------------------------------- Schnellbefehle (/ne + Tab)
// Tippe / (oder \) und ein paar Buchstaben: Vorschläge erscheinen unten im Editor, Tab übernimmt den ersten (Pfeile wählen, Esc schließt).
const SLASH = [
  ["newpage", "Seitenumbruch", () => "\\newpage\n"], ["toc", "Inhaltsverzeichnis", () => "[TOC]\n"], ["quellen", "Quellenverzeichnis", () => "[QUELLEN]\n"],
  ["titelblatt", "Titelblatt", (t) => `::: title\nTitel der Arbeit\n\nUntertitel oder Thema\n\nvon Vorname Nachname\n\nKlasse / Kurs · Schule\n\nBetreuende Lehrkraft: Name\n\nAbgabe: ${t}\n:::\n\n\\newpage\n`],
  ["mitte", "Zentriert schreiben", () => "::: center\nText\n:::\n"], ["rechts", "Rechtsbündig schreiben", () => "::: right\nText\n:::\n"], ["links", "Linksbündig schreiben", () => "::: left\nText\n:::\n"],
  ["entwurf", "Entwurf (noch nicht fertig)", () => "::: entwurf\nSchnell aufschreiben, später überarbeiten …\n:::\n"], ["notiz", "Notiz (nie im Dokument)", () => "[[Notiz: Text]]"], ["kommentar", "Kommentar (##…, nie im Dokument)", () => "##"],
  ["tabelle", "Tabelle", () => "| Spalte 1 | Spalte 2 | Spalte 3 |\n|----------|:--------:|---------:|\n| Text     | Text     | Text     |\n"],
  ["zitat", "Zitat mit Quelle", () => "> „Text“ [@quelle, S. 1]\n"], ["bild", "Bild", () => "![Beschreibung](https://)"], ["linie", "Trennlinie", () => "---\n"],
  ["h1", "Überschrift 1", () => "# "], ["h2", "Überschrift 2", () => "## "], ["h3", "Überschrift 3", () => "### "],
  ["selbststaendigkeit", "Selbstständigkeitserklärung", (t) => `# Selbstständigkeitserklärung {-}\n\nHiermit erkläre ich, dass ich die vorliegende Arbeit selbstständig und nur mit den angegebenen Quellen und Hilfsmitteln angefertigt habe. Alle wörtlich oder sinngemäß übernommenen Stellen habe ich als solche gekennzeichnet.\n\nOrt, ${t}\n\n\n\n______________________________  \nUnterschrift\n`],
];
function slashSetup() {
  const ta = $("#src"), box = el('<div class="slash" hidden></div>'); ta.parentElement.append(box);
  let list = [], sel = 0, from = 0;
  const hide = () => { box.hidden = true; list = []; };
  const token = () => { const p = ta.selectionStart; if (p !== ta.selectionEnd) return null; const m = ta.value.slice(0, p).match(/(^|[\s])([\/\\])([a-zäöüß0-9]*)$/i); return m ? { q: m[3].toLowerCase(), at: p - m[3].length - 1, p } : null; };
  const draw = () => { box.innerHTML = list.map((c, i) => `<button type="button" data-i="${i}" class="${i === sel ? "on" : ""}"><b>/${c[0]}</b><span>${esc(c[1])}</span></button>`).join("") + '<small>Tab = einfügen</small>'; box.hidden = false; };
  const refresh = () => {
    const t = token(); if (!t) return hide();
    list = SLASH.filter((c) => c[0].startsWith(t.q)).concat(SLASH.filter((c) => !c[0].startsWith(t.q) && t.q.length > 1 && c[0].includes(t.q))).slice(0, 5);
    if (!list.length) return hide();
    sel = 0; from = t.at; draw();
  };
  const take = (c) => {
    const t = token(); if (!t) return hide();
    const today = new Date().toLocaleDateString("de-DE", { day: "2-digit", month: "long", year: "numeric" });
    const before = ta.value.slice(0, t.at), text = c[2](today), isBlock = /\n$/.test(text) && !/^#{1,3} $/.test(text);
    const pre = isBlock && before && !before.endsWith("\n") ? "\n\n" : "";
    ta.setRangeText(pre + text, t.at, t.p, "end"); hide(); ta.dispatchEvent(new Event("input")); ta.focus();
  };
  ta.addEventListener("input", refresh);
  ta.addEventListener("click", refresh);
  ta.addEventListener("blur", () => setTimeout(hide, 150));
  box.onmousedown = (e) => { const b = e.target.closest("[data-i]"); if (b) { e.preventDefault(); take(list[b.dataset.i]); } };
  ta.addEventListener("keydown", (e) => {
    if (!box.hidden && list.length) {
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); take(list[sel]); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); sel = (sel + 1) % list.length; draw(); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); sel = (sel + list.length - 1) % list.length; draw(); return; }
      if (e.key === "Escape") { e.preventDefault(); hide(); return; }
    }
    if (e.key === "Escape" && ghost) { e.preventDefault(); ghost = null; placeGhost(); return; }
    if (e.key === "Tab") { e.preventDefault(); if (ghost) acceptGhost(); else ins("  "); }
  });
  ta.addEventListener("input", (e) => {
    ghost = null;
    const p = ta.selectionStart, v = ta.value;
    if (/^insert/.test(e.inputType || "") && p === ta.selectionEnd && box.hidden && !matchMedia("(pointer: coarse)").matches) {   // am Handy gäbe es keine Tab-Taste
      const ls = v.lastIndexOf("\n", p - 1) + 1; let le = v.indexOf("\n", p); if (le < 0) le = v.length;
      const s = hlLines[v.slice(0, p).split("\n").length - 1]?.includes('"l-code') ? null : suggest(v.slice(ls, p), v.slice(p, le), v.slice(le + 1));
      if (s) ghost = { at: p, ...s };
    }
    placeGhost();
  });
  const moved = () => { if (ghost && (ta.selectionStart !== ghost.at || ta.selectionEnd !== ghost.at)) { ghost = null; placeGhost(); } };
  ta.addEventListener("keyup", moved); ta.addEventListener("mouseup", moved);
  ta.addEventListener("blur", () => { ghost = null; placeGhost(); });
}
// Grauer Vorschlag am Cursor: liegt nur in der Einfärbung, im Text steht er erst nach Tab
let ghost = null;
function placeGhost() {
  const old = $("#hl .ghost"); if (old) { const p = old.parentNode; old.remove(); p.normalize(); }
  if (!ghost) return;
  const before = $("#src").value.slice(0, ghost.at), lines = before.split("\n"), line = $("#hl").firstChild.children[lines.length - 1]; if (!line) return;
  let col = lines.at(-1).length;
  const walk = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
  for (let t; (t = walk.nextNode());) {
    if (col > t.length) { col -= t.length; continue; }
    const span = document.createElement("span"); span.className = "ghost"; span.textContent = ghost.show;
    const tail = t.splitText(col); tail.parentNode.insertBefore(span, tail);
    return;
  }
}
function acceptGhost() {
  const ta = $("#src"), { at, text, caret } = ghost;
  ghost = null;
  ta.setRangeText(text, at, at, "end"); ta.setSelectionRange(at + caret, at + caret);
  ta.dispatchEvent(new Event("input"));
}

// ---------------------------------------------------------------- Rückgängig / Wiederholen
// Eigene Historie, weil das Browser-Undo bei Schnellbefehlen und Knöpfen (setRangeText) abreißt. Strg+Z, Strg+Y / Strg+Umschalt+Z, Knöpfe in der Leiste.
const hist = {};
function histSetup() {
  const ta = $("#src"), fresh = () => [{ v: ta.value, s: ta.selectionStart, e: ta.selectionEnd }]; let H = fresh(), i = 0, last = 0, kind = "";
  const btns = () => { const u = $('[data-a="undo"]'), r = $('[data-a="redo"]'); if (u) u.disabled = i <= 0; if (r) r.disabled = i >= H.length - 1; };
  const rec = (type) => {
    if (ta.value === H[i].v) return;
    const typing = /^(insert|delete)/.test(type || "") && type !== "insertFromPaste" && type !== "insertFromDrop";
    const now = Date.now(), snap = { v: ta.value, s: ta.selectionStart, e: ta.selectionEnd };
    H.length = i + 1;
    if (typing && i > 0 && kind === type.slice(0, 6) && now - last < 800) H[i] = snap;   // Tippen zu einem Schritt zusammenfassen
    else { H.push(snap); if (H.length > 400) H.shift(); i = H.length - 1; }
    last = now; kind = typing ? type.slice(0, 6) : ""; btns();
  };
  const go = (n) => {
    if (n < 0 || n >= H.length) return;
    i = n; const h = H[i]; ta.value = h.v; ta.setSelectionRange(h.s, h.e); last = 0; kind = "";
    st.doc.content = h.v; changed(); ta.dispatchEvent(new Event("scroll")); btns(); ta.focus();
  };
  hist.undo = () => go(i - 1); hist.redo = () => go(i + 1); hist.rec = () => rec("");
  hist.save = () => ({ H, i }); hist.load = (x) => { H = x?.H || fresh(); i = x?.i || 0; last = 0; kind = ""; btns(); };   // jeder Tab hat seine eigene Historie
  ta.addEventListener("input", (e) => rec(e.inputType));
  ta.addEventListener("beforeinput", (e) => { if (e.inputType === "historyUndo") { e.preventDefault(); hist.undo(); } else if (e.inputType === "historyRedo") { e.preventDefault(); hist.redo(); } });
  ta.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); hist.undo(); } else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); hist.redo(); }
  });
  btns();
}

// ---------------------------------------------------------------- Notizen & Entwürfe
// [[Notiz]] erscheint nie im Dokument; "::: entwurf" … ":::" ist Text, der noch nicht fertig ist (auf dem Bildschirm gelb markiert, im PDF normal).
function openItems() {
  const t = st.doc.content, out = [];
  for (const m of t.matchAll(/\[\[([\s\S]*?)\]\]/g)) out.push({ d: false, at: m.index, end: m.index + m[0].length, text: m[1].replace(/^Notiz:\s*/i, "").trim().slice(0, 140) || "(leer)" });
  for (const m of t.matchAll(/^:::\s*entwurf\s*\n([\s\S]*?)^:::\s*$/gm)) out.push({ d: true, at: m.index, end: m.index + m[0].length, text: m[1].trim().slice(0, 140) || "(leer)" });
  return out.sort((a, b) => a.at - b.at);
}
function notesPanel() {
  const scrim = el('<div class="scrim"></div>');
  const p = el(`<aside class="sheetp"><header>Notizen &amp; Entwürfe<button class="btn ic" id="x" aria-label="Schließen">${ico("close")}</button></header><div class="body">
    <div class="row" style="margin-top:6px"><button class="btn sm" id="addn">${ico("sticky_note_2")}Notiz</button><button class="btn sm" id="addd">${ico("edit_note")}Entwurf</button></div>
    <p class="fld"><small><b>Notiz</b> <code>[[Notiz: …]]</code> erscheint nie im Dokument. <b>Entwurf</b> ist Text, der noch nicht fertig ist: Er bleibt im Dokument, ist am Bildschirm gelb markiert und vor dem PDF-Export erinnere ich dich daran.</small></p>
    <div class="notes-list" id="nl"></div></div></aside>`);
  document.body.append(scrim, p);
  const close = () => { scrim.remove(); p.remove(); };
  scrim.onclick = $("#x", p).onclick = close;
  const fill = () => { const L = openItems(); $("#nl", p).innerHTML = L.length ? L.map((n, k) => `<button class="nitem ${n.d ? "d" : ""}" data-k="${k}"><small>${n.d ? "Entwurf" : "Notiz"}</small>${esc(n.text)}</button>`).join("") : '<p class="fld"><small>Noch nichts markiert.</small></p>'; return L; };
  let L = fill();
  $("#nl", p).onclick = (e) => { const b = e.target.closest("[data-k]"); if (!b) return; const n = L[b.dataset.k], t = $("#src"); close(); t.focus(); t.setSelectionRange(n.at, n.end); const line = t.value.slice(0, n.at).split("\n").length; t.scrollTop = Math.max(0, (line - 4) * prefs.edFs * 1.6); };
  $("#addn", p).onclick = () => { close(); wrap("[[Notiz: ", "]]", "Text"); };
  $("#addd", p).onclick = () => { close(); const t = $("#src"), sel = t.value.slice(t.selectionStart, t.selectionEnd); const pre = t.selectionStart && t.value[t.selectionStart - 1] !== "\n" ? "\n\n" : ""; t.setRangeText(`${pre}::: entwurf\n${sel || "Schnell aufschreiben, später überarbeiten …"}\n:::\n`, t.selectionStart, t.selectionEnd, "end"); t.dispatchEvent(new Event("input")); };
}

// ---------------------------------------------------------------- Quellenarchiv
// Pro Dokument eine Liste von Quellen (in der .idadocs, nicht in der .md). Zitieren mit [@schluessel], Verzeichnis mit [QUELLEN].
const TYPES = [["buch", "Buch"], ["artikel", "Artikel / Zeitschrift"], ["web", "Webseite"], ["sonst", "Sonstiges"]];
const asciiName = (t) => String(t).toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss").replace(/[^a-z0-9]+/g, "");
function makeKey(src, own) {
  const first = String(src.author || "").split(/;|\sund\s/)[0].split(",")[0];
  const base = (asciiName(first) || asciiName(src.title || "").slice(0, 12) || "quelle") + (src.year || "");
  let k = base, n = 0;
  while (st.doc.sources.some((x) => x.key === k && x.key !== own)) { k = base + (n < 26 ? String.fromCharCode(97 + n) : n); n++; }
  return k;
}
const keyRe = (k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const citeCount = (k) => (st.doc.tabs.map((t) => t.content).join("\n").match(new RegExp("\\[@" + keyRe(k) + "[,\\]]", "g")) || []).length;
const italic = (t) => esc(t).replace(/\*([^*]+)\*/g, "<i>$1</i>");

function sourcesPanel() {
  const scrim = el('<div class="scrim"></div>');
  const p = el(`<aside class="sheetp"><header>Quellen<button class="btn ic" id="x" aria-label="Schließen">${ico("close")}</button></header><div class="body" id="pb"></div></aside>`);
  document.body.append(scrim, p);
  const close = () => { scrim.remove(); p.remove(); };
  scrim.onclick = $("#x", p).onclick = close;
  const body = $("#pb", p), S = () => st.doc.sources;
  const cfgNow = () => cfg(st.doc.settings);

  function list() {
    const miss = st.res.missing || [];
    body.onclick = body.oninput = body.onchange = null;
    body.innerHTML = `<div class="row" style="margin-top:6px"><button class="btn sm pri" id="new">${ico("add")}Neue Quelle</button></div>
      ${miss.length ? `<p class="warn">Zitat ohne Quelle: ${miss.map((k) => `<code>${esc(k)}</code>`).join(", ")}</p>` : ""}
      <div class="notes-list">${S().length ? sortSources(S()).map((x) => { const n = citeCount(x.key); return `<div class="srci" data-k="${esc(x.key)}"><b>${esc(citeShort(x))}</b> <code>[@${esc(x.key)}]</code>
        <p>${italic(bibEntry(x, cfgNow().bibStyle))}</p><small class="${n ? "" : "bad"}">${n ? `${n}× zitiert` : "nicht zitiert"}${x.quotes?.length ? ` · ${x.quotes.length} Textstelle${x.quotes.length > 1 ? "n" : ""}` : ""}</small>
        <div class="row"><button class="btn sm" data-a="cite">Zitat einfügen</button><button class="btn sm" data-a="edit">Bearbeiten</button></div></div>`; }).join("") : '<p class="fld"><small>Noch keine Quellen. Leg eine an – mit Adresse, ISBN oder DOI füllt die App Titel und Autor selbst aus.</small></p>'}</div>
      <p class="fld"><small>Im Text zitieren mit <code>[@schluessel]</code> oder mit Seite <code>[@schluessel, 4]</code>. <code>[QUELLEN]</code> erzeugt das Quellenverzeichnis aus den zitierten Quellen. Zitierweise und Stil stellst du im Regler-Symbol ein.</small></p>`;
    $("#new", body).onclick = () => edit(null);
    body.onclick = (e) => {
      const b = e.target.closest("[data-a]"); if (!b) return;
      const x = S().find((o) => o.key === b.closest("[data-k]").dataset.k); if (!x) return;
      if (b.dataset.a === "cite") { close(); ins(`[@${x.key}]`); } else edit(x);
    };
  }

  function edit(src) {
    const cur = src ? structuredClone(src) : { type: "buch", author: "", year: "", title: "", container: "", publisher: "", place: "", url: "", accessed: "", note: "", key: "", quotes: [] };
    cur.quotes ||= [];
    const f = (k, label, type = "text", ph = "") => `<label class="fld col"><span>${label}</span><input type="${type}" data-f="${k}" placeholder="${ph}" value="${esc(cur[k] || "")}"></label>`;
    const draw = () => {
      body.innerHTML = `<label class="fld"><span>Art</span><select data-f="type">${TYPES.map(([v, t]) => `<option value="${v}" ${cur.type === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <div class="fld lookup"><input id="q" type="text" placeholder="Adresse, ISBN oder DOI einfügen …"><button class="btn sm" id="look">${ico("auto_fix_high")}Ausfüllen</button></div>
        ${f("author", "Autor (Nachname, Vorname · mehrere mit ;)", "text", "Müller, Anna")}${f("year", "Jahr", "text", "2020")}${f("title", "Titel")}
        ${cur.type === "artikel" ? f("container", "Zeitschrift / Sammelband") + f("note", "Heft, Seiten") : ""}
        ${cur.type !== "web" ? f("place", "Ort") : ""}${f("publisher", cur.type === "web" ? "Webseite / Herausgeber" : "Verlag")}
        ${cur.type === "web" || cur.type === "sonst" || cur.url ? f("url", "Adresse (URL)", "text", "https://") + f("accessed", "Abgerufen am", "date") : ""}
        ${f("key", "Schlüssel zum Zitieren", "text", "leer = automatisch")}
        <h3>TEXTSTELLEN (ZITATSAMMLUNG)</h3>
        ${cur.quotes.map((q, k) => `<div class="quote"><textarea data-q="${k}" rows="3" placeholder="Wörtliche Textstelle …">${esc(q.text)}</textarea>
          <div class="row"><input type="text" data-qp="${k}" placeholder="Seite" value="${esc(q.page || "")}"><button class="btn sm" data-qi="${k}">In Text einfügen</button><button class="btn sm" data-qd="${k}">Entfernen</button></div></div>`).join("")}
        <div class="row"><button class="btn sm" id="addq">${ico("format_quote")}Textstelle hinzufügen</button></div>
        <div class="row" style="margin-top:14px"><button class="btn sm pri" id="save">Speichern</button><button class="btn sm" id="cancel">Zurück</button>${src ? `<button class="btn sm" id="del">Löschen</button>` : ""}</div>`;
    };
    const commit = () => {   // Quelle sichern; false, wenn etwas fehlt
      if (!cur.title.trim() && !cur.author.trim()) { toast("Titel oder Autor fehlt"); return false; }
      cur.quotes = cur.quotes.filter((q) => q.text.trim());
      cur.key = cur.key.trim().replace(/[\s,\[\]]+/g, "") || makeKey(cur, src?.key);
      if (S().some((x) => x.key === cur.key && x.key !== src?.key)) { toast("Diesen Schlüssel gibt es schon"); return false; }
      if (src && src.key !== cur.key) {   // Schlüssel umbenannt: Zitate im Text mitziehen
        const t = $("#src"), re = new RegExp("\\[@" + keyRe(src.key) + "(?=[,\\]])", "g");
        st.doc.tabs.forEach((x, n) => { if (n !== st.doc.tab) x.content = x.content.replace(re, `[@${cur.key}`); });
        t.value = t.value.replace(re, `[@${cur.key}`); st.doc.content = t.value; hist.rec?.();
      }
      st.doc.sources = src ? S().map((x) => (x === src ? cur : x)) : [...S(), cur];
      src = cur; changed(); return true;
    };
    const flat = Object.keys(cur);   // nur bekannte Felder per Eingabe setzen
    body.onclick = body.oninput = body.onchange = null;
    draw();
    body.oninput = body.onchange = (e) => {
      const t = e.target;
      if (t.dataset.f && flat.includes(t.dataset.f)) { cur[t.dataset.f] = t.value; if (t.dataset.f === "type" && e.type === "change") draw(); }
      else if (t.dataset.q != null) cur.quotes[t.dataset.q].text = t.value;
      else if (t.dataset.qp != null) cur.quotes[t.dataset.qp].page = t.value;
    };
    body.onclick = async (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.id === "cancel") return list();
      if (b.id === "save") { if (commit()) list(); return; }
      if (b.id === "del") { if (confirm("Quelle löschen?")) { st.doc.sources = S().filter((x) => x.key !== src.key); changed(); list(); } return; }
      if (b.id === "addq") { cur.quotes.push({ text: "", page: "" }); draw(); return; }
      if (b.dataset.qd != null) { cur.quotes.splice(+b.dataset.qd, 1); draw(); return; }
      if (b.dataset.qi != null) {
        const q = cur.quotes[+b.dataset.qi]; if (!q.text.trim() || !commit()) return;
        close(); block(`> „${q.text.trim().replace(/\n+/g, " ")}“ [@${cur.key}${q.page ? ", " + q.page : ""}]\n`); return;
      }
      if (b.id === "look") {
        const q = $("#q", body).value.trim(); if (!q) return toast("Erst Adresse, ISBN oder DOI einfügen");
        b.disabled = true;
        try { const r = await api("/lookup?q=" + encodeURIComponent(q)); for (const [k, v] of Object.entries(r)) if (v && flat.includes(k)) cur[k] = v; draw(); toast("Ausgefüllt – bitte kurz prüfen"); }
        catch (er) { b.disabled = false; toast(er.message); }
      }
    };
  }
  list();
}

// ---------------------------------------------------------------- Seiten
function vars(s) {
  const c = cfg(s), font = (FONTS[c.font] || FONTS.Arial).replace(/"/g, "'"), al = { justify: "justify", left: "left", center: "center", right: "right" }[c.align] || "justify";
  return `--doc-font:${font};--fs:${c.size};--lh:${c.lineHeight};--ps:${c.paraSpacing};--al:${al};--h1:${c.h1};--h2:${c.h2};--h3:${c.h3}`;
}
let measure = null;
function layout() {
  const prev = $("#prev"); if (!prev || !st.doc) return;
  const c = cfg(st.doc.settings), { html, headings, used, missing } = render(st.doc.content, { numbers: c.headingNumbers, sources: st.doc.sources, citeStyle: c.citeStyle, bibStyle: c.bibStyle });
  st.res = { used, missing };
  if (prefs.mode === "web") {
    prev.innerHTML = `<div class="webdoc web doc ${prefs.dark ? "dark" : ""}" style="${vars(c)};--fs:${Math.max(c.size, 12)}">${html.replace(/ href="#h-[^"]*"/g, "")}</div>`;
    $("#meta").textContent = `${st.doc.content.trim() ? st.doc.content.trim().split(/\s+/).length : 0} Wörter`; st.pageStarts = {}; drawMarks(); fit(); return;
  }
  const cw = PAGE_W - c.marginLeft - c.marginRight, ch = PAGE_H - c.marginTop - c.marginBottom;
  if (cw < 40 || ch < 40) { prev.innerHTML = '<div class="empty">Die Ränder sind zu groß für DIN A4.</div>'; return; }
  const colCss = `width:${cw}mm;height:${ch}mm;column-width:${cw}mm;column-gap:${GAP}mm;`, pitch = (cw + GAP) * PX;
  // Jeder Abschnitt zwischen zwei \newpage ist ein eigener Fluss in eigenen Spalten: Der Umbruch braucht so weder Messen noch Platzhalter.
  const parts = sections(html);
  measure ||= document.body.appendChild(el('<div class="measure"></div>'));
  measure.style.cssText = `${vars(c)};width:${cw}mm`;
  const S = parts.map(({ frag, line }) => { const n = el('<div class="sec doc"></div>'); n.style.cssText = colCss; n.append(frag); measure.append(n); return { n, line, first: 1, pages: 1 }; });
  measure.replaceChildren(...S.map((x) => x.n));
  const number = () => { let p = 1; for (const x of S) { x.first = p; x.pages = columns(x.n, pitch); p += x.pages; } return p - 1; };
  const place = () => {
    number();
    const m = {};
    for (const x of S) { const x0 = x.n.getBoundingClientRect().left; x.n.querySelectorAll("h1[id],h2[id],h3[id]").forEach((h) => { m[h.id] = x.first + Math.floor((h.getBoundingClientRect().left - x0 + 2) / pitch); }); }
    return m;
  };
  let pages = place();
  if (measure.querySelector(".pg")) {   // Inhaltsverzeichnis: Seitenzahlen eintragen und danach noch einmal messen (Zeilen bleiben gleich lang)
    for (let pass = 0; pass < 2; pass++) { measure.querySelectorAll(".pg").forEach((p) => { p.textContent = pages[p.dataset.h] || ""; }); const np = place(); if (JSON.stringify(np) === JSON.stringify(pages)) break; pages = np; }
  }
  const n = number();
  st.pageStarts = pageStarts(S, pitch);
  const pos = { left: "left", center: "left:0;right:0;text-align:center", right: "right" }[c.pageNumberPos];
  const pn = (i) => !c.pageNumbers ? "" : `<div class="pgn" style="bottom:${Math.max(4, c.marginBottom / 2 - 2)}mm;${c.pageNumberPos === "center" ? pos : c.pageNumberPos === "left" ? `left:${c.marginLeft}mm` : `right:${c.marginRight}mm`}">${i + 1}</div>`;
  let out = "";
  for (const x of S) {
    const inner = x.n.innerHTML.replace(/ id="h-[^"]*"/g, "").replace(/ href="#h-[^"]*"/g, "");
    for (let i = 0; i < x.pages; i++) out += `<div class="sheet ${prefs.dark ? "dark" : ""}"><div class="clip" style="left:${c.marginLeft}mm;top:${c.marginTop}mm;width:${cw}mm;height:${ch}mm"><div class="flow doc" style="${vars(c)};${colCss}transform:translateX(-${i * (cw + GAP)}mm)">${inner}</div></div>${pn(x.first - 1 + i)}</div>`;
  }
  const keep = prev.scrollTop;
  prev.innerHTML = `<div class="sheets-w"><div class="sheets">${out}</div></div>`;
  fit();   // erst skalieren, dann die Scrollposition zurück: Vorher hat der Bereich noch nicht seine Höhe
  prev.scrollTop = keep;
  $("#meta").textContent = `${n} ${n === 1 ? "Seite" : "Seiten"} · ${st.doc.content.trim() ? st.doc.content.trim().split(/\s+/).length : 0} Wörter`;
  drawMarks(); relayoutOnImages();
}
// Zerlegt das HTML an jedem \newpage (auch wenn es in einem :::-Block steht) in Abschnitte. line = Markdown-Zeile, mit der der Abschnitt beginnt.
function sections(html) {
  const root = document.createElement("div"); root.innerHTML = html;
  const out = [], hasContent = (f) => f.textContent.trim() || f.querySelector?.("img,table,hr");
  let line = 0;
  const push = (frag) => { const blanks = frag.querySelectorAll(":scope > .blank"); for (let k = blanks.length - 1; k >= 0 && !blanks[k].nextElementSibling; k--) blanks[k].remove(); out.push({ frag, line }); };
  for (let pb; (pb = root.querySelector(".pb"));) {
    const r = document.createRange(); r.setStart(root, 0); r.setEndBefore(pb);
    const frag = r.extractContents();
    if (out.length || hasContent(frag)) push(frag);   // \newpage ganz am Anfang erzeugt keine leere erste Seite
    line = +pb.dataset.e;
    let up = pb.parentElement; pb.remove();
    while (up && up !== root && !up.childElementCount && !up.textContent.trim()) { const p = up.parentElement; up.remove(); up = p; }
  }
  const rest = document.createDocumentFragment(); rest.append(...root.childNodes);
  push(rest);
  return out;
}
// Wie viele Spalten (= Seiten) belegt ein Abschnitt? Gezählt wird, wo wirklich etwas steht, damit ein überhängender Abstand keine leere Seite macht.
function columns(n, pitch) {
  const x0 = n.getBoundingClientRect().left, r = document.createRange(); r.selectNodeContents(n);
  let max = 0; for (const q of r.getClientRects()) if (q.width || q.height) max = Math.max(max, q.left - x0);
  return Math.floor((max + 2) / pitch) + 1;
}
// Bilder haben erst nach dem Laden ihre Höhe: dann neu umbrechen (je Bild höchstens dreimal, damit ein kaputtes Bild keine Schleife auslöst).
const imgTries = new Map();
function relayoutOnImages() {
  measure.querySelectorAll("img").forEach((img) => {
    const src = img.src, n = imgTries.get(src) || 0;
    if (img.complete || n >= 3) return;
    imgTries.set(src, n + 1);
    const again = () => { clearTimeout(st.rtimer); st.rtimer = setTimeout(layout, 100); };
    img.addEventListener("load", again, { once: true }); img.addEventListener("error", again, { once: true });
  });
}
// Wo beginnt welche Seite im Markdown? Pro Block (data-l) die Startseite, bei Absätzen über den Seitenwechsel hinweg das erste Wort der neuen Seite.
const SKIPTOK = /^(#{1,6}|[-*+]|\d+[.)]|\|+|:?-{2,}:?|>+|:::\w*)$/;
function pageStarts(S, pitch) {
  const text = st.doc.content.replace(/\r\n?/g, "\n"), masked = maskNotes(text), ls = [0];
  for (let i = text.indexOf("\n"); i >= 0; i = text.indexOf("\n", i + 1)) ls.push(i + 1);
  const out = {}, fixed = new Set();   // die erste Seite eines Abschnitts beginnt genau hinter dem \newpage
  for (const x of S) if (x.first >= 2) { out[x.first] = x.line < ls.length ? ls[x.line] : text.length; fixed.add(x.first); }
  const put = (pg, off) => { if (pg >= 2 && off != null && !fixed.has(pg) && !(pg in out && out[pg] <= off)) out[pg] = off; };
  const wordsOf = (n) => { const w = [], tw = document.createTreeWalker(n, NodeFilter.SHOW_TEXT); for (let t; (t = tw.nextNode());) for (const m of t.data.matchAll(/\S+/g)) w.push([t, m.index, m.index + m[0].length]); return w; };
  for (const sec of S) {
  const x0 = sec.n.getBoundingClientRect().left, col = (x) => sec.first + Math.floor((x - x0 + 2) / pitch);
  const colOf = (w) => { const r = document.createRange(); r.setStart(w[0], w[1]); r.setEnd(w[0], w[2]); const q = r.getClientRects()[0]; return q ? col(q.left) : 0; };
  sec.n.querySelectorAll("[data-l]").forEach((n) => {
    const a = +n.dataset.l, b = +n.dataset.e, rects = n.getClientRects();
    if (!rects.length || a >= ls.length) return;
    const c0 = col(rects[0].left), c1 = col(rects[rects.length - 1].left);
    put(c0, ls[a]);
    if (c1 <= c0 || n.querySelector("[data-l]")) return;
    const words = wordsOf(n); if (!words.length) return;
    const seg = masked.slice(ls[a], ls[b] ?? text.length).split("\n").map((l) => { const c = stripComment(l); return c + " ".repeat(l.length - c.length); }).join("\n");
    const src = [...seg.matchAll(/\S+/g)].filter((m) => !SKIPTOK.test(m[0])).map((m) => ls[a] + m.index);
    for (let pg = c0 + 1; pg <= c1; pg++) {
      let lo = 0, hi = words.length - 1, k = -1;
      while (lo <= hi) { const mid = (lo + hi) >> 1; if (colOf(words[mid]) >= pg) { k = mid; hi = mid - 1; } else lo = mid + 1; }
      if (k < 0 || !src.length) continue;
      put(pg, src.length === words.length ? src[k] : src[Math.min(src.length - 1, Math.floor((k * src.length) / words.length))]);
    }
  });
  }
  return out;
}
// Editor-Einfärbung: nur die geänderten Zeilen neu setzen, damit auch lange Texte ohne Verzögerung bleiben
let hlLines = [];
function drawHl() {
  const ta = $("#src"), box = $("#hl")?.firstChild; if (!ta || !box) return;
  const next = highlightLines(ta.value), old = hlLines, kids = box.children;
  box.style.width = ta.clientWidth + "px";
  let a = 0, e = 0;
  while (a < old.length && a < next.length && old[a] === next[a]) a++;
  while (e < old.length - a && e < next.length - a && old[old.length - 1 - e] === next[next.length - 1 - e]) e++;
  if (a === next.length && a === old.length) return placeGhost();
  const stale = box.querySelector(".ghost"); if (stale) { const p = stale.parentNode; stale.remove(); p.normalize(); }
  for (let i = old.length - e - 1; i >= a; i--) kids[i].remove();
  const t = document.createElement("template"); t.innerHTML = next.slice(a, next.length - e).join("");
  box.insertBefore(t.content, kids[a] || null);
  hlLines = next;
  placeGhost();
}
// gestrichelte Linie im Markdown-Editor dort, wo im Dokument die nächste DIN-A4-Seite beginnt
function drawMarks() {
  const ta = $("#src"), mir = $("#mirror"), mk = $("#marks"); if (!ta || !mir || !mk) return;
  drawHl();
  const text = ta.value, list = Object.entries(st.pageStarts || {}).map(([p, o]) => [+p, o]).filter(([, o]) => o <= text.length).sort((x, y) => x[1] - y[1]);
  let html = "", prev = 0;
  for (const [p, o] of list) { html += esc(text.slice(prev, o)) + `<i class="mk" data-p="${p}"></i>`; prev = o; }
  mir.style.width = ta.clientWidth + "px";
  mir.innerHTML = html + esc(text.slice(prev)) + "\u200b";
  mk.firstChild.innerHTML = [...mir.querySelectorAll(".mk")].map((m) => `<div class="pgline" style="top:${m.offsetTop}px"><span>Seite ${m.dataset.p}</span></div>`).join("");
  mk.firstChild.style.transform = `translateY(${-ta.scrollTop}px)`;
}
// Skaliert per transform: Anders als "zoom" ändert das den Umbruch nicht, die Seiten bleiben also exakt so wie gemessen.
function fit() {
  const prev = $("#prev"), wrap = prev?.querySelector(".sheets-w"), sh = wrap?.firstChild; if (!sh) return;
  const full = PAGE_W * PX, z = Math.min(1.4, Math.max(0.3, (prev.clientWidth - 24) / full));
  sh.style.transform = `scale(${z})`;
  wrap.style.width = `${full * z}px`; wrap.style.height = `${sh.offsetHeight * z}px`;
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
    <h3>QUELLEN</h3>${sel("citeStyle", "Zitieren im Text", [["kurz", "Kurzbeleg (Müller 2020, S. 4)"], ["fuss", "Fußnote"]])}${sel("bibStyle", "Verzeichnis-Stil", [["din", "Deutsch (Autor (Jahr): Titel)"], ["apa", "APA"]])}
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
const fname = () => (st.doc.title + (st.doc.tabs.length > 1 ? ` - ${st.doc.tabs[st.doc.tab].title}` : "") || "Dokument").replace(/[^\p{L}\p{N} _-]+/gu, "").trim() || "Dokument";
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
    if (x === "ida") download((st.doc.title || "Dokument").replace(/[^\p{L}\p{N} _-]+/gu, "").trim().concat(".idadocs").replace(/^\.idadocs$/, "Dokument.idadocs"), JSON.stringify({ format: "idadocs", version: 1, id: st.doc.id, ...payload() }, null, 1), "application/json");
    if (x === "pdf") { const n = openItems().filter((o) => o.d).length; if (!n || confirm(`Es ${n === 1 ? "ist noch 1 Stelle" : `sind noch ${n} Stellen`} als Entwurf markiert. Trotzdem als PDF speichern?`)) printPdf(); }
    if (x === "del" && confirm("Dokument wirklich löschen?")) { await api("/docs/" + st.doc.id, { method: "DELETE" }).catch((er) => toast(er.message)); localStorage.removeItem("docs.c." + st.doc.id); st.dirty = false; home(); }
  };
}
function printPdf() {
  // Immer die weißen Seiten drucken (egal ob Web-Modus oder dunkles Papier): kurz im Seitenmodus neu aufbauen
  const old = { ...prefs }; prefs.mode = "pages"; prefs.dark = false; layout();
  let root = $("#printroot") || document.body.appendChild(el('<div id="printroot"></div>'));
  root.innerHTML = $(".sheets").outerHTML;
  const t = document.title; document.title = fname();
  const done = () => { document.title = t; root.innerHTML = ""; Object.assign(prefs, old); layout(); removeEventListener("afterprint", done); };
  addEventListener("afterprint", done);
  setTimeout(() => print(), 50);
}

// ---------------------------------------------------------------- Start
document.addEventListener("click", (e) => { if (e.target.closest(".sheets a, .webdoc a[href^='#']")) e.preventDefault(); });
const m = location.hash.match(/^#\/d\/([a-z0-9]+)/), v = location.hash.match(/^#\/v\/([a-z0-9]+)\/([\w-]+)/);
v ? viewer(v[1], v[2]) : m ? open(m[1]) : home();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
