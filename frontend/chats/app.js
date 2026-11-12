// IDA Chats: deine Beeper-Chats lesen und schreiben. Live per Server-Sent Events – nur solange die Seite sichtbar ist (Akku).
import { esc, autolink } from "./shared/js/dom.js";

const API = "/api/v1";
const $ = (s) => document.querySelector(s);
const BASE = "/";
const COLORS = ["rot", "orange", "gelb", "salbei", "gruen", "tuerkis", "blau", "lavendel", "lila", "koralle"];
let AUTOREAD = new Map();   // automatisch gelesen: ID → "open" (beim Öffnen) oder "always" (sofort, ohne Öffnen)
let MUTES = new Map();   // stummgeschaltete Chats: ID → bis (ms, 0 = bis du es aufhebst)
const S = { chats: [], next: null, q: "", f: "all", id: null, msgs: [], more: false, oldest: null, seq: 0, sending: false, reply: null, edit: null, voice: {}, vopen: {} };

const fmtDay = new Intl.DateTimeFormat("de-DE", { weekday: "long", day: "2-digit", month: "long" });
const fmtDayY = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "2-digit", month: "long", year: "numeric" });
const fmtShort = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit" });
const fmtTime = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const dayKey = (iso) => new Date(iso).toDateString();
function when(iso) {
  if (!iso) return "";
  const d = new Date(iso), days = Math.round((new Date(new Date().toDateString()) - new Date(d.toDateString())) / 864e5);
  return days === 0 ? fmtTime.format(d) : days === 1 ? "gestern" : days < 7 ? d.toLocaleDateString("de-DE", { weekday: "short" }) : fmtShort.format(d);
}
function color(key) {
  let h = 0;
  for (const c of String(key || "?")) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const c = COLORS[h % COLORS.length];
  return `background:var(--evc-${c});color:var(--ev-${c})`;
}
const letter = (t) => (String(t || "?").replace(/[^\p{L}\p{N}]/gu, "")[0] || "?");
// Profilbild aus Beeper; fehlt es (404), bleibt der farbige Anfangsbuchstabe darunter stehen
// Profilbilder: einmal geladen (oder fehlend) merken, damit sie beim Neuzeichnen sofort da sind statt neu einzublenden
const AV_OK = new Set(), AV_NO = new Set();
const avSrc = (id, user) => `${API}/avatar/${encodeURIComponent(id)}${user ? `?user=${encodeURIComponent(user)}` : "?v=2"}`;   // v=2: Gruppen ohne Bild haben jetzt eine Collage
const avatar = (t, id, user = "") => { const u = id && avSrc(id, user), ok = AV_OK.has(u);
  return `<span class="av" style="${color(user || id || t)}">${esc(letter(t))}${u && !AV_NO.has(u) ? `<img class="avimg${ok ? " ok" : ""}" alt="" ${ok ? "" : 'loading="lazy" '}decoding="${ok ? "sync" : "async"}" src="${u}">` : ""}</span>`; };
document.addEventListener("error", (e) => { if (e.target.classList?.contains("avimg")) { AV_NO.add(e.target.getAttribute("src")); e.target.remove(); } }, true);
document.addEventListener("load", (e) => { if (e.target.classList?.contains("avimg")) { AV_OK.add(e.target.getAttribute("src")); e.target.classList.add("ok"); } }, true);
const clean = (t) => String(t || "").replace(/[­​-‏‪-‮⁠]/g, "");   // unsichtbare Zeichen in Namen

async function api(path, opts = {}) {
  const r = await fetch(API + path, { credentials: "same-origin", cache: "no-store", redirect: "manual", ...opts, headers: { "content-type": "application/json", ...(opts.headers || {}) } });
  if (r.type === "opaqueredirect" || r.status === 401 || r.status === 403) throw Object.assign(new Error("Anmeldung abgelaufen"), { login: true });
  if (!r.ok) {
    let m = `HTTP ${r.status}`;
    try { m = (await r.json()).detail || m; } catch { /* egal */ }
    throw Object.assign(new Error(m), { status: r.status });
  }
  return r.json();
}
function toast(t, run) {
  const el = $("#toast"); el.textContent = t; el.hidden = false; el.style.cursor = run ? "pointer" : ""; el.onclick = run ? () => { el.hidden = true; run(); } : null;
  clearTimeout(toast.t); toast.t = setTimeout(() => (el.hidden = true), 4500);
}
const errBox = (e) => `<div class="empty"><span class="ms">${e.login ? "lock" : e.status === 409 ? "link_off" : "cloud_off"}</span>${esc(e.message)}<br><button class="btn" onclick="location.reload()">Nochmal versuchen</button></div>`;

// ---------------- Adresse ----------------
const urlFor = (id) => BASE + (id ? `chat/${encodeURIComponent(id)}` : "");
function readUrl() {
  const m = /^chat\/([^/]+)/.exec(location.pathname.slice(BASE.length));
  return m ? decodeURIComponent(m[1]) : null;
}
// Verlauf bleibt immer flach: Übersicht → ein Chat. Chat-Wechsel ersetzt den Eintrag, Zurück führt direkt zur Übersicht
// (auch die Zurück-Taste am Handy), statt alle vorher geöffneten Chats nacheinander zu zeigen.
function go(id, push = true) {
  if (urlFor(id) !== location.pathname) {
    const fromList = !readUrl();
    if (id && fromList && push) history.pushState({ fromList: true }, "", urlFor(id));
    else history.replaceState(id ? history.state : null, "", urlFor(id));
  }
  route();
}
function back() {
  if (history.state?.fromList) history.back();   // davor liegt die Übersicht
  else go(null, false);
}
window.addEventListener("popstate", () => {
  if (OV && !readOv()) { const c = OV; OV = null; c(); }   // Zurück-Taste schließt Bild/Video
  if ($("#srch") && !readSrch()) dropSrch();                // … die Suche
  if ($("#info") && !readInfo() && !readSrch()) dropInfo();   // … und die Info-Seite
  route();
  if (!OV) openFromUrl();
});
// Bild- und Video-Vollbild haben eine eigene Adresse (…/foto/<Nachricht>[/<Nr>], …/video/<Nachricht>, …/bild):
// Zurück schließt sie, ein Neuladen oder ein geteilter Link öffnet sie wieder.
let OV = null;   // Schließen-Funktion des offenen Vollbilds (ohne Verlauf anzufassen)
const ovBase = () => urlFor(readUrl());
const readOv = () => /^chat\/[^/]+\/(foto|video|bild)(?:\/|$)/.test(location.pathname.slice(BASE.length)) || /^bild$/.test(location.pathname.slice(BASE.length));
function ovPath(sub) { const b = ovBase(); return `${b}${b.endsWith("/") ? "" : "/"}${sub}`; }
function ovOpen(sub, close) {
  const path = ovPath(sub), was = OV;
  OV = close;
  if (was) return history.replaceState(history.state, "", path);   // Vollbild ersetzt Vollbild
  if (location.pathname === path) history.replaceState(history.state, "", ovBase());   // per Link geöffnet: davor liegt der Chat
  history.pushState({ ...(history.state || {}), ov: true }, "", path);
}
function ovSet(sub) { if (OV) history.replaceState(history.state, "", ovPath(sub)); }
function ovClosed() {   // über den eigenen Knopf, Wischen oder Esc geschlossen
  if (!OV) return;
  OV = null;
  if (history.state?.ov) history.back(); else history.replaceState(history.state, "", ovBase());
}
function openFromUrl() {
  const m = /^chat\/[^/]+\/(foto|video|bild|info|suche)(?:\/([^/]+))?(?:\/(\d+))?/.exec(location.pathname.slice(BASE.length));
  if (!m && readOv()) return history.replaceState(history.state, "", ovBase());
  if (!m || !S.id) return;
  if (m[1] === "info") return $("#info") ? undefined : openInfo();
  if (m[1] === "suche") return $("#srch") ? undefined : openSearch();
  const msg = m[2] && S.msgs.find((x) => x.id === decodeURIComponent(m[2]));
  if (m[1] === "foto" && msg) {
    const f = msg.files.filter((x) => x.kind === "image" && !x.sticker && x.src)[(+m[3] || 1) - 1];
    if (f) return gallery(f.src);
  }
  if (m[1] === "video" && msg) { const el = document.getElementById(`m-${msg.id}`)?.querySelector(".vid"); if (el) return playVideo(el); }
  history.replaceState(history.state, "", ovBase());   // nicht (mehr) im geladenen Verlauf: einfach den Chat zeigen
}
function route() {
  let id = readUrl();
  const g = id && grpOf(id); if (g && g.id !== id) { history.replaceState(history.state, "", urlFor(g.id)); id = g.id; }
  document.body.classList.toggle("reading", !!id);
  if (id === S.id) return drawList();
  const c = id && MC.get(id);
  TS.open = false; TS.sx = 0;
  S.id = id; S.msgs = c ? c.msgs.slice() : []; S.more = c ? c.more : false; S.oldest = c ? c.oldest : null; S.reply = null; S.edit = null; S.to = null;
  document.querySelectorAll("#list .row").forEach((r) => r.classList.toggle("sel", r.dataset.id === id));
  if (!$("#list .row")) drawList();
  if (id) { openChat(); clearNotes(id); } else $("#conv").innerHTML = `<div class="conv-empty"><span class="ms">forum</span><p>Wähle einen Chat aus.</p></div>`;
}

// ---------------- Nachrichten-Zwischenspeicher ----------------
// Pro Chat die zuletzt gesehenen Nachrichten (im Speicher und im Browser), damit ein Chat sofort da ist; danach kommt Frisches nach.
const MC_KEY = "chats-msgs", MC_CHATS = 40, MC_MSGS = 150;
const MC = (() => { try { return new Map(Object.entries(JSON.parse(localStorage.getItem(MC_KEY) || "{}"))); } catch { return new Map(); } })();
let mcT;
function cacheMsgs() {
  if (!S.id) return;
  const msgs = S.msgs.filter((m) => !m.pending).slice(-MC_MSGS);
  MC.delete(S.id); MC.set(S.id, { msgs, more: S.more || msgs.length < S.msgs.length, oldest: msgs.length < S.msgs.length ? null : S.oldest });
  while (MC.size > MC_CHATS) MC.delete(MC.keys().next().value);
  clearTimeout(mcT);
  mcT = setTimeout(() => { try { localStorage.setItem(MC_KEY, JSON.stringify(Object.fromEntries(MC))); } catch { /* voll: dann nur im Speicher */ } }, 800);
}
// Die nächste ältere Seite schon im Hintergrund holen, damit "Ältere laden" sofort geht
const PRE = new Map();
function prefetchOlder() {
  if (!S.id || !S.more || !S.oldest) return;
  const k = `${S.id}|${S.oldest}`;
  if (PRE.has(k)) return;
  if (PRE.size > 30) PRE.clear();
  const p = api(`/chats/${encodeURIComponent(S.id)}/messages?cursor=${encodeURIComponent(S.oldest)}&direction=before`);
  p.catch(() => PRE.delete(k));
  PRE.set(k, p);
}

// ---------------- Liste ----------------
let listSeq = 0;
async function loadChats({ more = false, quiet = false } = {}) {
  const seq = ++listSeq;
  if (!more && !quiet && !S.chats.length) $("#list").innerHTML = '<div class="skel"></div>'.repeat(7);
  try {
    const p = new URLSearchParams({ q: S.q, unread: S.f === "unread", inbox: S.f === "archive" ? "archive" : "primary" });
    if (more && S.next) p.set("cursor", S.next);
    const d = await api(`/chats?${p}`);
    if (seq !== listSeq) return;
    S.chats = more ? S.chats.concat(d.items) : d.items; S.next = d.next;
    drawList();
  } catch (e) {
    if (seq === listSeq && !quiet) $("#list").innerHTML = errBox(e);
  }
}
// Gelesen-Markierung: Öffnen allein ändert in Beeper nichts. "Als gelesen markieren" markiert den Chat in Beeper als gelesen (und beim Absender, wenn das Netzwerk das kann).
// Der lokale Merker sorgt nur dafür, dass der Chat sofort gelesen aussieht, bis Beeper die neue Ungelesen-Zahl liefert.
const SEEN_KEY = "chats-seen";
const seen = (() => { try { return JSON.parse(localStorage.getItem(SEEN_KEY) || "{}"); } catch { return {}; } })();
const seenKey = (c) => `${c.unread}|${c.at || ""}`;   // gilt nur, solange seitdem nichts Neues kam (sonst blieb ein Chat mit gleicher Zahl fälschlich "gelesen")
const left = (c) => (c && c.unread && seen[c.id] !== seenKey(c) ? c.unread : 0);   // gilt als ungelesen, bis markiert (oder neue Nachricht kommt)
function markSeen(c) {
  if (!c) return;
  if (c.unread) seen[c.id] = seenKey(c); else delete seen[c.id];
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch {}
}
async function markRead(c) {
  if (!c) return;
  const id = c.id, before = c.unread, mem = (c.merged || []).map((x) => known(x.id)).filter(Boolean);
  mem.forEach(markSeen); markSeen(c); refreshReadBar(); drawList();
  try {
    await api(`/chats/${encodeURIComponent(id)}/read`, { method: "POST", body: "{}" });
    c.unread = 0; markSeen(c); mem.forEach((x) => { x.unread = 0; markSeen(x); }); drawList();
  } catch (e) {
    delete seen[id]; try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch {}
    c.unread = before; refreshReadBar(); drawList(); toast(`Als gelesen markieren ging nicht: ${e.message}`);
  }
}
async function markUnread(c, msgId) {   // in Beeper wieder ungelesen – auf Wunsch ab einer bestimmten Nachricht
  if (!c) return;
  const before = c.unread;
  delete seen[c.id]; try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch {}
  c.unread = Math.max(1, c.unread || 0); refreshReadBar(); drawList();
  try {
    const r = await api(`/chats/${encodeURIComponent(c.id)}/unread`, { method: "POST", body: JSON.stringify(msgId ? { message_id: msgId } : {}) });
    if (r.unread) c.unread = r.unread;
    refreshReadBar(); drawList(); toast("Als ungelesen markiert");
  } catch (e) {
    c.unread = before; markSeen(c); refreshReadBar(); drawList(); toast(`Als ungelesen markieren ging nicht: ${e.message}`);
  }
}
// ---------------- Angepinnte Chats (echte Beeper-Pins, höchstens 3 – stehen immer oben) ----------------
let PINS = (() => { try { return JSON.parse(localStorage.getItem("chats-pins") || "[]"); } catch { return []; } })(), PIN_MAX = 3;
async function loadPins() {
  try {
    const d = await api("/pins");
    PINS = d.items; PIN_MAX = d.max || 3;
    try { localStorage.setItem("chats-pins", JSON.stringify(PINS)); } catch {}
    drawList();
  } catch { /* dann bleibt der letzte Stand */ }
}
async function togglePin(c) {
  const on = !PINS.some((p) => p.id === c.id), before = PINS;
  if (on && PINS.length >= PIN_MAX) return toast(`Höchstens ${PIN_MAX} Chats anpinnen – löse erst einen.`);
  PINS = on ? [...PINS, { ...c, pinned: true }] : PINS.filter((p) => p.id !== c.id);
  drawList();
  try { await api(`/chats/${encodeURIComponent(c.id)}/pin`, { method: "POST", body: JSON.stringify({ pinned: on }) }); loadPins(); }
  catch (e) { PINS = before; drawList(); toast(`${on ? "Anpinnen" : "Lösen"} ging nicht: ${e.message}`); }
}
// ---------------- Zusammengeführte Chats (nur hier, Beeper bleibt unberührt) ----------------
// Eine Gruppe heißt nach ihrem ersten Chat; ihre ID ist dessen ID. In der Liste steht sie als eine Zeile, im Chat als ein gemeinsamer Verlauf.
let MERGES = (() => { try { return JSON.parse(localStorage.getItem("chats-merges") || "[]"); } catch { return []; } })();
const grpOf = (id) => MERGES.find((g) => g.chats.some((c) => c.id === id));
const isGrp = (id) => MERGES.some((g) => g.id === id);
async function loadMerges() {
  try {
    MERGES = (await api("/merges")).items;
    try { localStorage.setItem("chats-merges", JSON.stringify(MERGES)); } catch {}
    drawList();
  } catch { /* letzter Stand bleibt */ }
}
const known = (id) => S.chats.find((x) => x.id === id) || PINS.find((x) => x.id === id);
function grpRow(g) {   // Gruppe als eine Zeile: Titel vom ersten Chat, neueste Zeit, Ungelesene zusammengezählt
  const live = g.chats.map((m) => ({ ...m, ...(known(m.id) || {}) })), head = live[0];
  const av = g.avatar && g.chats.some((x) => x.id === g.avatar) ? g.avatar : g.id;
  return { ...head, id: g.id, av, title: g.name || head.title || g.chats[0].title, at: live.map((c) => c.at || "").sort().at(-1), unread: live.reduce((a, c) => a + (left(c) ? c.unread : 0), 0),
    network: [...new Set(live.map((c) => c.network).filter(Boolean))].join(" + "), last: live.map((c) => c.last).filter(Boolean).sort((a, b) => (a.at || "").localeCompare(b.at || "")).at(-1) || null, type: live.length && live.every((c) => c.type === "group") ? "group" : "single", merged: live };
}
function collapse(list) {   // Mitglieder einer Gruppe verschwinden hinter der Gruppenzeile (an der Stelle des neuesten Mitglieds)
  const done = new Set(), out = [];
  for (const c of list) {
    const g = grpOf(c.id);
    if (!g) { out.push(c); continue; }
    if (done.has(g.id)) continue;
    done.add(g.id); out.push(grpRow(g));
  }
  return out;
}
async function doMerge(a, list) {
  const one = (c) => (c.merged ? c.merged : [c]).map((x) => ({ id: x.id, title: clean(x.title || ""), network: x.network || "" }));
  const all = [...one(a), ...list.flatMap(one)].filter((x, i, l) => l.findIndex((y) => y.id === x.id) === i);
  if (all.length > 10) return toast("Höchstens 10 Chats zusammenführen");
  try {
    await api("/merges", { method: "POST", body: JSON.stringify({ chats: all }) });
    await loadMerges(); toast(list.length === 1 ? `${clean(a.title)} und ${clean(list[0].title)} zusammengeführt` : `${list.length + 1} Chats zusammengeführt`);
    const g = grpOf(a.id); if (S.id && g && g.chats.some((c) => c.id === S.id)) go(g.id, false);
  } catch (e) { toast(`Zusammenführen ging nicht: ${e.message}`); }
}
async function unMerge(g, chat = "") {
  try {
    await api(`/merges/${encodeURIComponent(g.id)}${chat ? `?chat=${encodeURIComponent(chat)}` : ""}`, { method: "DELETE" });
    await loadMerges(); toast(chat ? "Chat herausgenommen" : "Zusammenführung aufgehoben");
    if (S.id === g.id) { MC.delete(g.id); S.id = null; route(); }
  } catch (e) { toast(`Ging nicht: ${e.message}`); }
}
// Einstellungen einer Gruppe: eigener Name, von welchem Chat das Profilbild kommt, über welchen standardmäßig gesendet wird
function groupSettings(c) {
  const g = grpOf(c.id); if (!g) return;
  closeMenu();
  const d = document.createElement("div"), mem = grpRow(g).merged, av = g.avatar || g.id;
  const label = (x) => `${esc(clean(x.title))}<small>${esc(netName(x.network))}</small>`;
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet fwd gset"><div class="fwd-top"><b>Zusammengeführter Chat</b><button class="icon-btn" data-x="close" aria-label="Schließen"><span class="ms">close</span></button></div>
    <div class="fwd-list">
      <div class="gset-h">Name</div><label class="search"><span class="ms">edit</span><input id="gname" maxlength="100" placeholder="${esc(clean(mem[0].title))}" value="${esc(g.name || "")}" autocomplete="off"></label>
      <div class="gset-h">Profilbild</div>${mem.map((x) => `<button class="fwd-row ${x.id === av ? "on" : ""}" data-av="${esc(x.id)}">${netAv(x)}<span class="fwd-t">${label(x)}</span>${tick(x.id === av)}</button>`).join("")}
      <div class="gset-h">Standardmäßig senden über</div>
      <button class="fwd-row ${g.send ? "" : "on"}" data-send=""><span class="av"><span class="ms">auto_mode</span></span><span class="fwd-t">Automatisch<small>Chat der letzten Nachricht</small></span>${tick(!g.send)}</button>
      ${mem.map((x) => `<button class="fwd-row ${x.id === g.send ? "on" : ""}" data-send="${esc(x.id)}" >${netAv(x)}<span class="fwd-t">${label(x)}</span>${tick(x.id === g.send)}</button>`).join("")}
    </div><button class="btn fwd-go">Speichern</button></div>`;
  let pAv = av, pSend = g.send || "";
  const mark = (attr, v) => d.querySelectorAll(`[${attr}]`).forEach((b) => { const on = b.getAttribute(attr) === v; b.classList.toggle("on", on); b.lastElementChild.classList.toggle("on", on); });
  d.addEventListener("click", async (e) => {
    if (e.target.closest("[data-x=close]") || e.target.classList.contains("ctx-back")) return closeMenu();
    const a = e.target.closest("[data-av]"), sn = e.target.closest("[data-send]");
    if (a) { pAv = a.dataset.av; return mark("data-av", pAv); }
    if (sn) { pSend = sn.dataset.send; return mark("data-send", pSend); }
    if (!e.target.closest(".fwd-go")) return;
    const name = d.querySelector("#gname").value.trim();
    closeMenu();
    try {
      await api(`/merges/${encodeURIComponent(g.id)}`, { method: "PATCH", body: JSON.stringify({ name, avatar: pAv === g.id ? "" : pAv, send: pSend }) });
      S.to = null; await loadMerges();
      if (S.id === g.id && $(".chead")) { $(".chead").outerHTML = head(); refreshReadBar(); toChip(); drawMsgs(); }
      toast("Gespeichert");
    } catch (err) { toast(`Speichern ging nicht: ${err.message}`); }
  });
  document.body.append(d);
}
// ---------------- Info-Seite: Tipp auf den Chat-Kopf (wie "Kontaktinfo" bei WhatsApp) ----------------
const ABOUT = new Map();   // pro Chat kurz gemerkt, damit die Seite beim zweiten Öffnen sofort da ist
// Die Info-Seite hat eine eigene Adresse (…/chat/<id>/info): die Zurück-Taste am Handy schließt sie.
function dropInfo() {
  const el = $("#info"); if (!el) return;
  el.classList.add("out"); setTimeout(() => el.remove(), 200);
}
const readInfo = () => /^chat\/[^/]+\/info$/.test(location.pathname.slice(BASE.length));
function closeInfo() {
  if (readInfo()) { if (history.state?.info) history.back(); else history.replaceState(history.state, "", urlFor(S.id)); }
  dropInfo();
}
async function openInfo() {
  const c = cur(); if (!c || $("#info")) return;
  const el = document.createElement("div");
  el.className = "info"; el.id = "info";
  $("#conv").append(el);
  const path = `${urlFor(c.id)}/info`;
  if (location.pathname === path) history.replaceState(history.state, "", urlFor(c.id));   // per Link geöffnet: davor liegt der Chat
  history.pushState({ ...(history.state || {}), info: true }, "", path);
  const draw = (d) => { if (el.isConnected) el.innerHTML = infoHtml(c, d); };
  const hit = ABOUT.get(c.id);
  draw(hit?.d || null);
  if (hit && Date.now() - hit.t < 60000) return;
  try { const d = await api(`/chats/${encodeURIComponent(c.id)}/about`); ABOUT.set(c.id, { t: Date.now(), d }); draw(d); }
  catch (e) { if (!hit) draw({ error: e.message }); }
}
function infoHtml(c, d) {
  const title = clean(c.title || "Chat"), chats = d?.chats || [], one = chats[0];
  const other = (x) => (x?.people || []).find((p) => !p.self && !p.bot);
  const isGroup = c.type === "group";
  const phone = !isGroup && !c.merged ? other(one)?.phone : "";
  const media = S.msgs.flatMap((m) => m.deleted ? [] : m.files.filter((f) => f.kind === "image" || f.kind === "video")).slice(-60).reverse();
  const nLinks = S.msgs.reduce((a, m) => a + (m.deleted ? 0 : (m.links?.length || (/https?:\/\//.test(m.text || "") ? 1 : 0))), 0);
  const nFiles = S.msgs.reduce((a, m) => a + (m.deleted ? 0 : m.files.filter((f) => f.kind === "file" || f.kind === "audio").length), 0);
  const pinned = PINS.some((p) => p.id === c.id);
  const sub = c.merged ? c.merged.map((x) => netName(x.network)).join(" · ") : isGroup ? `Gruppe · ${one ? one.total : "…"} Mitglieder` : phone || netName(c.network);
  const row = (icon, main, small = "", attrs = "") => `<button class="irow" ${attrs}><span class="ms">${icon}</span><span class="it"><b>${main}</b>${small ? `<small>${small}</small>` : ""}</span></button>`;
  const desc = chats.map((x) => x.description).find(Boolean);
  const expiry = chats.map((x) => x.expiry).find(Boolean);
  const per = (sec) => sec >= 86400 ? `${Math.round(sec / 86400)} Tag${sec >= 172800 ? "e" : ""}` : sec >= 3600 ? `${Math.round(sec / 3600)} Std.` : `${Math.round(sec / 60)} Min.`;
  return `<div class="ihead"><button class="icon-btn" data-i="close" aria-label="Zurück"><span class="ms">arrow_back</span></button><b>${isGroup ? "Gruppeninfo" : "Kontaktinfo"}</b></div>
  <div class="ibody">
    <div class="ihero">${avatar(title, c.av || c.id)}<h2>${esc(title)}</h2><p>${esc(sub)}</p>
      <div class="iacts">
        <button data-i="chat"><span class="ms">chat</span>Chat</button>
        <button data-i="pin"><span class="ms">push_pin</span>${pinned ? "Gelöst" : "Anpinnen"}</button>
        <button data-i="search"><span class="ms">search</span>Suchen</button>
        ${phone ? `<a href="tel:${esc(phone.replace(/[^\d+]/g, ""))}"><span class="ms">call</span>Anrufen</a>` : ""}
      </div></div>
    ${d?.error ? `<div class="icard ierr"><span class="ms">cloud_off</span>${esc(d.error)}</div>` : ""}
    ${desc ? `<div class="icard"><small>${isGroup ? "Beschreibung" : "Info"}</small><div class="idesc">${autolink(esc(desc))}</div></div>` : ""}
    ${media.length || nLinks || nFiles ? `<div class="icard"><div class="ich"><small>Medien, Links und Dateien</small><small>${media.length} · ${nLinks} · ${nFiles}</small></div>
      ${media.length ? `<div class="igrid">${media.slice(0, 9).map((f) => `<button class="ithumb" data-i="media" data-src="${esc(f.src || "")}" data-kind="${f.kind}">${f.kind === "image" ? `<img loading="lazy" alt="" src="${mediaUrl(f)}">` : f.poster ? `<img loading="lazy" alt="" src="${mediaUrl({ src: f.poster })}">` : ""}${f.kind === "video" ? '<span class="ms">play_circle</span>' : ""}</button>`).join("")}</div>` : ""}
      <p class="ihint">Aus den geladenen Nachrichten</p></div>` : ""}
    ${c.merged ? `<div class="icard"><small>Zusammengeführte Chats</small>${c.merged.map((x) => { const a = chats.find((y) => y.id === x.id), p = other(a);
        return `<div class="iperson">${netAv(x, true)}<span class="it"><b>${esc(clean(x.title))}</b><small>${esc([netName(x.network), p?.phone, p?.username && "@" + p.username].filter(Boolean).join(" · "))}</small></span></div>`; }).join("")}
      ${row("tune", "Name, Profilbild, Standard", "", 'data-i="gset"')}</div>` : ""}
    ${isGroup && one ? `<div class="icard"><div class="ich"><small>${one.total} Mitglieder</small></div>${one.people.filter((p) => !p.bot).sort((a, b) => (b.self - a.self) || (b.admin - a.admin) || a.name.localeCompare(b.name)).map((p) =>
        `<div class="iperson">${avatar(clean(p.name), c.id, p.id)}<span class="it"><b>${p.self ? "Du" : esc(clean(p.name) || "Unbekannt")}</b><small>${esc([p.phone, p.username && "@" + p.username].filter(Boolean).join(" · "))}</small></span>${p.admin ? '<span class="iadm">Admin</span>' : ""}</div>`).join("")}</div>` : ""}
    ${!isGroup && !c.merged && other(one) && (other(one).username || other(one).email) ? `<div class="icard">${other(one).username ? row("alternate_email", esc(other(one).username), "Benutzername") : ""}${other(one).email ? row("mail", esc(other(one).email), "E-Mail") : ""}</div>` : ""}
    <div class="icard">
      ${expiry ? row("timer", "Selbstlöschende Nachrichten", `Nach ${per(expiry)}`) : ""}
      ${row(isMuted(c.id) ? "notifications_off" : "notifications", isMuted(c.id) ? "Stummgeschaltet" : "Stummschalten", isMuted(c.id) ? muteText(c.id) : "Keine Benachrichtigungen aufs Handy", 'data-i="mute"')}
      ${row("mark_chat_read", "Automatisch als gelesen", AR_TEXT[arOf(c.id)], 'data-i="autoread"')}
      ${chats.some((x) => x.muted) ? row("notifications_off", "In Beeper stummgeschaltet", "Auch hier keine Benachrichtigungen") : ""}
      ${left(c) ? row("done_all", "Als gelesen markieren", "", 'data-i="read"') : row("mark_chat_unread", "Als ungelesen markieren", "", 'data-i="unread"')}
      ${row("call_merge", "Mit anderem Chat zusammenführen", "", 'data-i="merge"')}
      ${c.merged ? `<button class="irow danger" data-i="split"><span class="ms">call_split</span><span class="it"><b>Zusammenführung aufheben</b></span></button>` : ""}
    </div>
  </div>`;
}
document.addEventListener("click", (e) => {
  const av = e.target.closest("#info .ihero > .av, #info .iperson .av, #msgs .bw > .av"); if (av && avatarBig(av)) return;
  const a = e.target.closest("#info [data-i]")?.dataset.i; if (!a) return;
  const c = cur(); if (!c) return;
  if (a === "close" || a === "chat") return closeInfo();
  if (a === "pin") { togglePin(c); setTimeout(() => { const el = $("#info"); if (el) el.innerHTML = infoHtml(cur(), ABOUT.get(c.id)?.d); }, 50); return; }
  if (a === "search") return openSearch();
  if (a === "media") { const t = e.target.closest("[data-src]"); if (t.dataset.kind === "image") return gallery(t.dataset.src); closeInfo(); const el = [...document.querySelectorAll("#msgs .vid")].find((v) => v.dataset.src === t.dataset.src); el?.scrollIntoView({ block: "center" }); return; }
  if (a === "gset") return groupSettings(c);
  if (a === "mute") return muteMenu(c);
  if (a === "autoread") return autoReadMenu(c);
  if (a === "read") { markRead(c); return closeInfo(); }
  if (a === "unread") { markUnread(c); return closeInfo(); }
  if (a === "merge") return chatPick({ title: "Zusammenführen mit …", verb: "Zusammenführen", multi: true, from: c, skip: new Set((c.merged || [c]).map((x) => x.id)) }, (to) => { closeInfo(); doMerge(c, to); });
  if (a === "split") { closeInfo(); return unMerge(grpOf(c.id)); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#ctx") && !$(".lightbox")) { if ($("#srch")) closeSrch(); else if ($("#info")) closeInfo(); } });

// ---------------- Suche im Chat (…/chat/<id>/suche): Text, Medienart, ein Tag; bei Zusammenführung auch nur eine App ----------------
const readSrch = () => /^chat\/[^/]+\/suche$/.test(location.pathname.slice(BASE.length));
function dropSrch() { const el = $("#srch"); if (!el) return; el.classList.add("out"); setTimeout(() => el.remove(), 200); }
function closeSrch() {
  if (readSrch()) { if (history.state?.srch) history.back(); else history.replaceState(history.state, "", $("#info") ? `${urlFor(S.id)}/info` : urlFor(S.id)); }
  dropSrch();
}
const SR = { q: "", media: "", day: "", only: "", items: [], next: "", busy: false, seq: 0 };
const SR_KINDS = [["", "Alles", "chat"], ["image", "Fotos", "image"], ["video", "Videos", "movie"], ["link", "Links", "link"], ["file", "Dateien", "description"]];
function openSearch() {
  const c = cur(); if (!c || $("#srch")) return;
  Object.assign(SR, { q: "", media: "", day: "", only: "", items: [], next: "", busy: false });
  const el = document.createElement("div");
  el.className = "info srch"; el.id = "srch";
  const nets = c.merged ? `<div class="schips">${[{ id: "", network: "" }, ...c.merged].map((x) => `<button class="chip ${x.id === "" ? "on" : ""}" data-only="${esc(x.id)}"${x.id ? ` data-net="${netKey(x.network)}"` : ""}>${x.id ? esc(netName(x.network)) : "Alle Apps"}</button>`).join("")}</div>` : "";
  el.innerHTML = `<div class="ihead"><button class="icon-btn" data-s="close" aria-label="Zurück"><span class="ms">arrow_back</span></button>
      <label class="sfield"><span class="ms">search</span><input id="sq" type="search" placeholder="In ${esc(clean(c.title || "Chat"))} suchen" enterkeyhint="search" autocomplete="off"></label></div>
    <div class="sfilters"><div class="schips">${SR_KINDS.map(([k, t, ic]) => `<button class="chip ${k === "" ? "on" : ""}" data-kind="${k}"><span class="ms">${ic}</span>${t}</button>`).join("")}</div>${nets}
      <div class="schips"><label class="chip sday"><span class="ms">calendar_today</span><span id="sdayt">Datum</span><input id="sday" type="date" max="${new Date().toLocaleDateString("sv")}"></label>
      <button class="chip" data-s="noday" hidden><span class="ms">close</span>Tag entfernen</button></div></div>
    <div class="ibody" id="sres"><div class="empty sempty"><span class="ms">manage_search</span>Suchbegriff eingeben, Medienart oder Tag wählen</div></div>`;
  $("#conv").append(el);
  const path = `${urlFor(c.id)}/suche`;
  if (location.pathname === path) history.replaceState(history.state, "", urlFor(c.id));
  history.pushState({ ...(history.state || {}), srch: true }, "", path);
  const q = el.querySelector("#sq");
  let t = null;
  q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { SR.q = q.value.trim(); runSearch(); }, 350); });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(t); SR.q = q.value.trim(); runSearch(); q.blur(); } });
  el.querySelector("#sday").addEventListener("change", (e) => { SR.day = e.target.value; runSearch(); });
  el.addEventListener("click", (e) => {
    const k = e.target.closest("[data-kind]"), o = e.target.closest("[data-only]"), a = e.target.closest("[data-s]")?.dataset.s;
    if (k) { SR.media = k.dataset.kind; el.querySelectorAll("[data-kind]").forEach((b) => b.classList.toggle("on", b === k)); return runSearch(); }
    if (o) { SR.only = o.dataset.only; el.querySelectorAll("[data-only]").forEach((b) => b.classList.toggle("on", b === o)); return runSearch(); }
    if (a === "close") return closeSrch();
    if (a === "noday") { SR.day = ""; el.querySelector("#sday").value = ""; return runSearch(); }
    if (a === "more") return runSearch(true);
    const hit = e.target.closest("[data-hit]"); if (hit) return openHit(SR.items[+hit.dataset.hit], hit);
  });
  if (matchMedia("(pointer: fine)").matches) q.focus();
}
async function runSearch(more = false) {
  const el = $("#srch"), c = cur(); if (!el || !c) return;
  const dl = SR.day ? new Date(`${SR.day}T00:00:00`) : null;
  el.querySelector("#sdayt").textContent = dl ? fmtShort.format(dl) + (dl.getFullYear() !== new Date().getFullYear() ? `.${dl.getFullYear()}` : "") : "Datum";
  el.querySelector(".sday").classList.toggle("on", !!dl);
  el.querySelector("[data-s=noday]").hidden = !dl;
  if (!SR.q && !SR.media && !dl) { SR.items = []; SR.next = ""; return drawSearch(); }
  const seq = ++SR.seq;
  const p = new URLSearchParams();
  if (SR.q) p.set("q", SR.q);
  if (SR.media) p.set("media", SR.media);
  if (dl) { p.set("start", new Date(dl.getTime() - 1).toISOString()); p.set("end", new Date(dl.getTime() + 864e5).toISOString()); }
  if (SR.only) p.set("only", SR.only);
  if (more && SR.next) p.set("cursor", SR.next);
  SR.busy = true; if (!more) { SR.items = []; drawSearch(); }
  try {
    const d = await api(`/chats/${encodeURIComponent(c.id)}/search?${p}`);
    if (seq !== SR.seq) return;
    SR.items = more ? [...SR.items, ...d.items] : d.items; SR.next = d.next || "";
  } catch (e) { if (seq === SR.seq) { SR.busy = false; return drawSearch(e.message); } }
  SR.busy = false; drawSearch();
}
function drawSearch(err = "") {
  const box = $("#sres"); if (!box) return;
  if (err) { box.innerHTML = `<div class="empty"><span class="ms">cloud_off</span>${esc(err)}</div>`; return; }
  if (!SR.items.length) { box.innerHTML = SR.busy ? '<div class="skel"></div><div class="skel"></div>' : `<div class="empty sempty"><span class="ms">${SR.q || SR.media || SR.day ? "search_off" : "manage_search"}</span>${SR.q || SR.media || SR.day ? "Nichts gefunden" : "Suchbegriff eingeben, Medienart oder Tag wählen"}</div>`; return; }
  const c = cur(), netOf = (m) => c?.merged ? netKey((c.merged.find((x) => x.id === m.chat) || {}).network) : null;
  const visual = SR.media === "image" || SR.media === "video";
  const hl = (t) => { let h = esc(t.length > 220 ? `${t.slice(0, 220)}…` : t); for (const w of SR.q.split(/\s+/).filter((x) => x.length > 1)) h = h.replace(new RegExp(`(${esc(w).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"), "<mark>$1</mark>"); return h; };
  let html = "";
  if (visual) {
    const tiles = SR.items.flatMap((m, i) => m.files.filter((f) => f.kind === "image" || f.kind === "video").map((f) => ({ f, i })));
    html = `<div class="igrid">${tiles.map(({ f, i }) => `<button class="ithumb" data-hit="${i}" data-src="${esc(f.src || "")}">${f.kind === "image" ? `<img loading="lazy" alt="" src="${mediaUrl(f)}">` : f.poster ? `<img loading="lazy" alt="" src="${mediaUrl({ src: f.poster })}">` : ""}${f.kind === "video" ? '<span class="ms">play_circle</span>' : ""}</button>`).join("")}</div>`;
  } else {
    let last = "";
    html = SR.items.map((m, i) => {
      const day = m.at ? (new Date(m.at).getFullYear() === new Date().getFullYear() ? fmtDay : fmtDayY).format(new Date(m.at)) : "", head = day !== last ? `<div class="sday-h">${esc(day)}</div>` : ""; last = day;
      const f = m.files[0], net = netOf(m);
      const what = m.text ? hl(m.text) : f ? `<i>${f.kind === "image" ? "📷 Foto" : f.kind === "video" ? "🎥 Video" : f.kind === "audio" ? "🎤 Sprachnachricht" : `📎 ${esc(f.name || "Datei")}`}</i>` : "<i>Nachricht</i>";
      const thumb = f && (f.kind === "image" || f.poster) ? `<img class="sthumb" loading="lazy" alt="" src="${mediaUrl(f.kind === "image" ? f : { src: f.poster })}">` : "";
      return `${head}<button class="shit" data-hit="${i}"${net !== null ? ` data-net="${net}"` : ""}><span class="it"><b>${m.mine ? "Du" : esc(clean(m.sender) || clean(c?.title || ""))}${net ? ` <span class="snet">${esc(netName((c.merged.find((x) => x.id === m.chat) || {}).network))}</span>` : ""}<small>${m.at ? fmtTime.format(new Date(m.at)) : ""}</small></b><span class="stx">${what}</span></span>${thumb}</button>`;
    }).join("");
  }
  box.innerHTML = html + (SR.next ? `<button class="btn smore" data-s="more">${SR.busy ? "Lädt …" : "Mehr laden"}</button>` : "");
}
async function openHit(m, el) {
  if (!m) return;
  const f = el.dataset.src ? m.files.find((x) => x.src === el.dataset.src) : null;
  if (f && f.kind === "image") {   // Fotos aus den Treffern als Galerie
    const pics = SR.items.flatMap((x) => x.files.filter((y) => y.kind === "image" && !y.sticker && y.src).map((y) => ({ src: y.src, url: mediaUrl(y), at: x.at, sub: "bild", m: { ...x, chat: x.chat || S.id }, name: y.name, who: x.mine ? "Du" : clean(x.sender) || clean(cur()?.title || "") })));
    return lightbox(null, pics, Math.max(0, pics.findIndex((p) => p.src === f.src)));
  }
  // sonst: zur Nachricht im Verlauf springen (ältere Seiten nachladen, bis sie da ist)
  const find = () => document.getElementById(`m-${m.id}`);
  let n = 0;
  while (!S.msgs.some((x) => x.id === m.id) && S.more && n++ < 25) await loadMsgs({ older: true });
  if (!S.msgs.some((x) => x.id === m.id)) return toast("Diese Nachricht liegt zu weit zurück");
  if (history.state?.srch && history.state?.info && $("#info")) { history.go(-2); dropSrch(); dropInfo(); }   // Suche kam aus der Info-Seite: beide zu
  else { closeSrch(); if ($("#info")) closeInfo(); }
  setTimeout(() => { const b = find(); if (!b) return; b.scrollIntoView({ block: "center" }); b.classList.add("flash"); setTimeout(() => b.classList.remove("flash"), 1600); }, 260);
}
function rowMenu(c) {
  closeMenu();
  const on = PINS.some((p) => p.id === c.id), d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet">
    <button data-p="pin"><span class="ms">push_pin</span>${on ? "Nicht mehr anpinnen" : "Anpinnen"}</button>
    <button data-p="mute"><span class="ms">${isMuted(c.id) ? "notifications" : "notifications_off"}</span>${isMuted(c.id) ? "Stummschaltung ändern" : "Stummschalten"}</button>
    <button data-p="merge"><span class="ms">call_merge</span>Mit anderem Chat zusammenführen</button>
    ${c.merged ? '<button data-p="gset"><span class="ms">tune</span>Name, Profilbild, Standard</button>' : ""}
    ${c.merged ? '<button data-p="split"><span class="ms">call_split</span>Zusammenführung aufheben</button>' : ""}
    ${left(c) ? '<button data-p="read"><span class="ms">done_all</span>Als gelesen markieren</button>' : '<button data-p="unread"><span class="ms">mark_chat_unread</span>Als ungelesen markieren</button>'}</div>`;
  d.addEventListener("click", (e) => {
    const p = e.target.closest("[data-p]")?.dataset.p;
    closeMenu();
    if (p === "pin") togglePin(c);
    if (p === "read") markRead(c);
    if (p === "unread") markUnread(c);
    if (p === "merge") chatPick({ title: "Zusammenführen mit …", verb: "Zusammenführen", multi: true, from: c, skip: new Set((c.merged || [c]).map((x) => x.id)) }, (to) => doMerge(c, to));
    if (p === "split") unMerge(grpOf(c.id));
    if (p === "gset") groupSettings(c);
    if (p === "mute") muteMenu(c);
  });
  document.body.append(d);
}
const rowChat = (id) => { const g = MERGES.find((x) => x.id === id); return g ? grpRow(g) : known(id); };
let rowT, rowHeld = false;
$("#list").addEventListener("pointerdown", (e) => {
  const r = e.target.closest(".row"); rowHeld = false;
  if (!r || e.pointerType === "mouse") return;
  const x = e.clientX, y = e.clientY;
  const stop = () => { clearTimeout(rowT); removeEventListener("pointermove", mv); removeEventListener("pointerup", stop); removeEventListener("pointercancel", stop); };
  const mv = (m) => { if (Math.abs(m.clientX - x) > 10 || Math.abs(m.clientY - y) > 10) stop(); };
  addEventListener("pointermove", mv); addEventListener("pointerup", stop); addEventListener("pointercancel", stop);
  rowT = setTimeout(() => { stop(); const c = rowChat(r.dataset.id); if (c) { rowHeld = true; navigator.vibrate?.(10); rowMenu(c); } }, 480);
});
$("#list").addEventListener("contextmenu", (e) => {
  const r = e.target.closest(".row"); if (!r) return;
  e.preventDefault();
  const c = rowChat(r.dataset.id); if (c && !$("#ctx")) rowMenu(c);
});
function lastLine(c) {   // "Du: …" / in Gruppen "Name: …" / sonst nur der Text; ohne Text ein Hinweis aufs Netzwerk
  const l = c.last;
  if (!l) return `<i>${esc(c.merged ? c.merged.map((x) => netName(x.network)).join(" · ") : netName(c.network))}</i>`;
  const who = l.mine ? "Du: " : l.sender ? `${clean(l.sender)}: ` : "";
  return `${who ? `<b>${esc(who)}</b>` : ""}${l.text ? esc(clean(l.text)) : "<i>Anhang</i>"}`;
}
function drawList() {
  const pinIds = new Set(PINS.map((p) => p.id)), showPins = S.f === "all" && !S.q;
  const pinned = showPins ? collapse(PINS.map((p) => { const c = S.chats.find((x) => x.id === p.id); if (c) c.pinned = true; return c || p; })) : [];
  const pinGrp = new Set(pinned.map((c) => c.id));
  const rest = collapse(showPins ? S.chats.filter((c) => !pinIds.has(c.id)) : S.chats).filter((c) => !pinGrp.has(c.id));
  const n = [...pinned, ...rest].reduce((a, c) => a + left(c), 0);
  $("#sub").textContent = n ? `${n} ungelesen` : "aus Beeper";
  document.title = n ? `(${n}) Chats` : "Chats";
  if (navigator.setAppBadge) (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => {});
  if (!S.chats.length && !pinned.length) { $("#list").innerHTML = `<div class="empty"><span class="ms">${S.q ? "search_off" : "forum"}</span>${S.q ? "Nichts gefunden." : "Keine Chats."}</div>`; return; }
  const row = (c, pin) => `<div class="row ${left(c) ? "unread" : ""} ${c.id === S.id ? "sel" : ""}" data-id="${esc(c.id)}">
      ${pickAv(c)}<div class="rmain"><div class="rtop"><span class="rtitle">${esc(clean(c.title))}</span><span class="rtime">${when(c.at)}</span></div>
      <div class="rsub"><span class="last">${lastLine(c)}</span>${isMuted(c.id) ? '<span class="ms mute">notifications_off</span>' : ""}${pin ? '<span class="ms pin">push_pin</span>' : ""}${left(c) ? `<span class="cnt">${c.unread}</span>` : ""}</div></div></div>`;
  $("#list").innerHTML = pinned.map((c) => row(c, true)).join("") + (pinned.length && rest.length ? '<div class="pinsep"></div>' : "") + rest.map((c) => row(c, false)).join("")
    + (S.next ? '<button class="more" id="more">Mehr laden</button>' : "");
  if (S.id && $(".chead")) refreshReadBar();
}
$("#list").addEventListener("click", (e) => {
  if (e.target.closest("#more")) return loadChats({ more: true });
  if (rowHeld) { rowHeld = false; return; }
  const av = e.target.closest(".row .av"); if (av && avatarBig(av)) return;
  const r = e.target.closest(".row");
  if (!r) return;
  const c = S.chats.find((x) => x.id === r.dataset.id);
  go(r.dataset.id);
});
// Filter (Alle / Ungelesen / Archiv) hinter dem Filter-Knopf rechts in der Suche
const FILTERS = [["all", "forum", "Alle"], ["unread", "mark_chat_unread", "Ungelesen"], ["archive", "archive", "Archiv"]];
$("#fbtn").onclick = (e) => {
  e.preventDefault(); closeMenu();
  const r = e.currentTarget.getBoundingClientRect(), d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back clear"></div><div class="ctx-box fmenu" style="top:${r.bottom + 6}px;right:${Math.max(8, innerWidth - r.right)}px">${FILTERS.map(([f, ic, t]) =>
    `<button data-f="${f}" class="${S.f === f ? "on" : ""}"><span class="ms">${ic}</span>${t}${S.f === f ? '<span class="ms tick">check</span>' : ""}</button>`).join("")}</div>`;
  d.addEventListener("click", (ev) => {
    const f = ev.target.closest("[data-f]")?.dataset.f; closeMenu();
    if (!f || f === S.f) return;
    S.f = f; const b = $("#fbtn"); b.classList.toggle("on", f !== "all"); b.setAttribute("aria-label", `Filter: ${FILTERS.find((x) => x[0] === f)[2]}`);
    S.chats = []; loadChats();
  });
  document.body.append(d);
};
let qT;
$("#q").addEventListener("input", () => { clearTimeout(qT); qT = setTimeout(() => { S.q = $("#q").value.trim(); S.chats = []; loadChats(); }, 350); });
$("#refresh").onclick = () => { loadChats({ quiet: true }); if (S.id) loadMsgs({ quiet: true }); };

// ---------------- Unterhaltung ----------------
const cur = () => S.id && rowChat(S.id);
// In zusammengeführten Chats: zu welchem echten Chat gehört eine Nachricht, wie heißt sie dort, und wohin geht, was ich schreibe?
const cid = (m) => m?.chat || S.id, mid = (m) => m?.mid || m?.id;
function target() {
  const c = cur(); if (!c?.merged) return S.id;
  if (S.to && c.merged.some((x) => x.id === S.to)) return S.to;
  const g = grpOf(c.id); if (g?.send && c.merged.some((x) => x.id === g.send)) return g.send;
  const last = [...S.msgs].reverse().find((m) => m.chat && !m.mine) || [...S.msgs].reverse().find((m) => m.chat);
  return last?.chat || c.merged[0].id;
}
const NET_NAME = { snapchat: "Snapchat", instagram: "Instagram", whatsapp: "WhatsApp", signal: "Signal", telegram: "Telegram", discord: "Discord", messenger: "Messenger",
  gmessages: "SMS", googlechat: "Google Chat", slack: "Slack", linkedin: "LinkedIn", x: "X", imessage: "iMessage" };
const netName = (n) => NET_NAME[netKey(n)] || String(n || "Chat");
// Profilbild mit kleinem Netzwerk-Abzeichen unten rechts (wie in Beeper)
const netAv = (x, big = false) => `<span class="avw ${big ? "big" : ""}">${avatar(clean(x.title), x.id)}<i class="nbadge" data-net="${netKey(x.network)}">${esc(netName(x.network)[0])}</i></span>`;
function toChip() {
  const b = $("#toBtn"), c = cur(), ta = $("#txt"); if (!b) return;
  b.hidden = !c?.merged;
  $("#conv").dataset.net = c?.merged ? "" : netKey(c?.network);   // gemeinsamer Verlauf: Farbe pro Nachricht statt für den ganzen Chat
  const form = $("#form");   // Eingabe in der Farbe des Netzwerks, über das gesendet wird
  if (form) form.dataset.net = netKey((c?.merged ? c.merged.find((x) => x.id === target()) || c.merged[0] : c)?.network);
  if (!c?.merged) { if (ta) ta.placeholder = "Nachricht"; return closeTo(); }
  const t = c.merged.find((x) => x.id === target()) || c.merged[0];
  b.innerHTML = netAv(t); b.title = `Senden über ${netName(t.network)} an ${clean(t.title)}`;
  if (ta && !S.edit) ta.placeholder = `${netName(t.network)}-Nachricht`;
  if ($("#topanel")) drawTo();
}
// "Senden an …": klappt über dem Eingabefeld auf, eine Karte pro Chat
const closeTo = () => { $("#topanel")?.remove(); $("#toBtn")?.classList.remove("open"); };
function drawTo() {
  const c = cur(), p = $("#topanel"); if (!c?.merged || !p) return;
  const on = target();
  p.innerHTML = `<div class="to-h">Senden an …</div>${c.merged.map((x) => `<button class="to-card ${x.id === on ? "on" : ""}" data-to="${esc(x.id)}">${netAv(x, true)}
    <span class="to-t"><b>${esc(clean(x.title))}</b><small>über ${esc(netName(x.network))}</small></span>${x.id === on ? '<span class="ms to-ok">check_circle</span>' : ""}</button>`).join("")}
    <button class="to-set" data-set="1"><span class="ms">tune</span>Name, Profilbild, Standard</button>`;
}
function toMenu() {
  const c = cur(); if (!c?.merged) return;
  if ($("#topanel")) return closeTo();
  closeMenu();
  const p = document.createElement("div");
  p.className = "topanel"; p.id = "topanel";
  $("#form").before(p); $("#toBtn").classList.add("open");
  drawTo();
  p.addEventListener("click", (e) => {
    const t = e.target.closest("[data-to]")?.dataset.to;
    if (t) { S.to = t; buzz(10); if (S.reply?.chat && S.reply.chat !== t) setReply(null); toChip(); setTimeout(closeTo, 160); $("#txt")?.focus(); }
    if (e.target.closest("[data-set]")) { closeTo(); groupSettings(c); }
  });
}
document.addEventListener("pointerdown", (e) => { if ($("#topanel") && !e.target.closest("#topanel, #toBtn")) closeTo(); }, true);
function head() {
  const c = cur(), title = clean(c?.title || "Chat");
  return `<div class="chead"><button class="icon-btn back float" data-a="back" aria-label="Zurück"><span class="ms">arrow_back</span></button><div class="cinfo float" data-a="info" role="button" title="Info anzeigen">${avatar(title, c?.av || S.id)}
    <div class="t"><b>${esc(title)}</b><small>${esc(c?.network || "")}${c?.type === "group" ? " · Gruppe" : ""}</small></div></div></div>`;
}
function readBar(c) {
  return left(c) ? `<div class="readbar" id="readbar"><button class="chip" data-a="seen">Als gelesen markieren</button></div>` : "";
}
function refreshReadBar() {
  const c = cur(), old = $("#readbar");
  if (old) old.remove();
  if (!left(c)) $("#unsep")?.remove();
  const d = $("#dock"); if (d && left(c)) d.insertAdjacentHTML("afterbegin", readBar(c));
  const ub = $("#unseenBtn"); if (ub) ub.hidden = !!left(c);
}
async function openChat() {
  $("#conv").innerHTML = `${head()}<div class="msgs" id="msgs"><div class="skel"></div><div class="skel"></div></div>
    <div class="dock" id="dock">${readBar(cur())}<div class="fixbar float" id="fixbar" hidden></div><div class="replybar float" id="replybar" hidden></div><form class="composer" id="form">
    <button class="icon-btn attach" id="attachBtn" type="button" aria-label="Anhängen" title="Foto, Video oder Datei"><span class="ms">add</span></button>
    <div class="inwrap"><button class="tochip" id="toBtn" type="button" aria-label="Senden an …" hidden></button>
    <textarea id="txt" rows="1" placeholder="Nachricht" enterkeyhint="send"></textarea></div>
    <button class="send mic" id="sendBtn" type="submit" aria-label="Sprachnachricht aufnehmen"><span class="ms">mic</span></button></form>
    <div class="recbar" id="recbar" hidden><button class="icon-btn" type="button" data-rec="cancel" aria-label="Verwerfen"><span class="ms">delete</span></button>
      <span class="recdot"></span><span class="rectime" id="rectime">0:00</span><div class="recwave" id="recwave"></div>
      <button class="icon-btn" type="button" data-rec="pause" aria-label="Pause"><span class="ms">pause</span></button>
      <button class="send" type="button" data-rec="send" aria-label="Sprachnachricht senden"><span class="ms">send</span></button></div></div>`;
  // Kopf und Eingabe schweben über dem Verlauf: der Verlauf bekommt oben/unten so viel Platz, wie sie gerade hoch sind
  const ro = new ResizeObserver(() => { const cv = $("#conv"), d = $("#dock"); if (cv && d) { cv.style.setProperty("--dockh", `${d.offsetHeight}px`); const b = $("#msgs"); if (b && atBottom) b.scrollTop = b.scrollHeight; } });
  ro.observe($("#dock"));
  const ta = $("#txt");
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`; sendMode(); fixLater(); mentionPick(); });
  ta.addEventListener("paste", (e) => { const f = [...(e.clipboardData?.files || [])]; if (f.length) { e.preventDefault(); pickFiles(f); } });
  $("#fixbar").addEventListener("click", (e) => {
    const t = S.fix?.text; if (!t) return;
    if (e.target.closest("[data-fix=send]")) return send(t);
    ta.value = t; ta.dispatchEvent(new Event("input")); hideFix(); ta.focus();   // Text antippen: Vorschlag ins Feld übernehmen
  });
  $("#attachBtn").onclick = attachMenu;
  $("#toBtn").onclick = toMenu; toChip();
  $("#recbar").addEventListener("click", (e) => { const a = e.target.closest("[data-rec]")?.dataset.rec; if (a) recAction(a); });
  const conv = $("#conv");
  conv.ondragover = (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); conv.classList.add("drop"); } };
  conv.ondragleave = (e) => { if (e.target === conv || !conv.contains(e.relatedTarget)) conv.classList.remove("drop"); };
  conv.ondrop = (e) => { conv.classList.remove("drop"); const f = [...(e.dataTransfer?.files || [])]; if (f.length) { e.preventDefault(); pickFiles(f); } };
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); $("#form").requestSubmit(); } });
  $("#form").addEventListener("submit", (e) => { e.preventDefault(); if (ta.value.trim()) send(); else if (!S.edit) startRec(); });
  $("#conv").dataset.net = cur()?.merged ? "" : netKey(cur()?.network);
  if (S.msgs.length) drawMsgs({ stick: true });   // sofort aus dem Speicher, Frisches kommt gleich
  if (!cur()) loadChats({ quiet: true }).then(() => { if (S.id && $(".chead")) { $(".chead").outerHTML = head(); refreshReadBar(); } });
  await loadMsgs({ first: true });
  autoRead();
  if (cur()?.type === "group" && !ABOUT.get(S.id)) { const id = S.id; loadPeople(id).then(() => { if (S.id === id && $("#msgs")) drawMsgs({ stick: atBottom }); }); }
  if (!OV) openFromUrl();
  if (matchMedia("(pointer: fine)").matches) ta.focus();
}
async function loadMsgs({ first = false, quiet = false, older = false } = {}) {
  const id = S.id, seq = ++S.seq;
  try {
    const pk = older && S.oldest ? `${id}|${S.oldest}` : "";
    if (older && !S.oldest) return;
    const d = await (PRE.get(pk) || api(`/chats/${encodeURIComponent(id)}/messages${pk ? `?cursor=${encodeURIComponent(S.oldest)}&direction=before` : ""}`));
    if (pk) PRE.delete(pk);
    if (id !== S.id || seq !== S.seq) return;
    // Liegen aus dem Speicher schon ältere Nachrichten vor als die neueste Seite, bleibt deren Stand für "Ältere laden"
    const keepOld = !older && S.oldest && S.msgs.length && d.items.length && (S.msgs[0].at || "") < (d.items[0].at || "");
    const box = $("#msgs"), atEnd = !box || box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    const keep = new Map(S.msgs.map((m) => [m.id, m]));
    for (const m of d.items) keep.set(m.id, m);
    const sig = (l) => l.map((m) => `${m.id}|${m.text}|${m.edited ? 1 : 0}|${(m.read_by || []).length}|${(m.reactions || []).map((r) => r.key + r.n).join()}|${m.deleted}${m.was ? 1 : 0}|${m.pending ? 1 : 0}`).join(";");
    const before = sig(S.msgs);
    const landed = (m) => m.pendingFile ? m.sentAt && d.items.some((x) => x.mine && x.files.length && Date.parse(x.at) >= Date.parse(m.at) - 10000)
      : d.items.some((x) => x.mine && x.text === m.text);
    // Was im Zeitraum der geladenen Seite liegt, dort aber fehlt, gibt es nicht mehr (z. B. für mich gelöscht) – nicht aus dem Speicher zurückholen
    const lo = d.items[0]?.at, hi = d.items.at(-1)?.at, got = new Set(d.items.map((m) => m.id));
    const stale = (m) => !m.pending && m.at && lo && m.at >= lo && m.at <= hi && !got.has(m.id);
    S.msgs = [...keep.values()].filter((m) => !stale(m) && (!m.pending || !landed(m))).sort((a, b) => (a.at || "").localeCompare(b.at || ""));
    if (!keepOld) { S.more = d.more; S.oldest = d.oldest; }
    cacheMsgs();
    setTimeout(prefetchOlder, 300);
    if (quiet && !first && !older && before === sig(S.msgs) && $("#msgs")?.children.length) return;   // nichts Neues: nicht neu zeichnen (Scrollstand, Player)
    if (first && !older && before === sig(S.msgs) && $("#msgs .bw")) return;   // aus dem Speicher schon richtig gezeichnet
    drawMsgs({ stick: first || atEnd, keepTop: older }); toChip();
  } catch (e) {
    if (!quiet && id === S.id) { const b = $("#msgs"); if (b) b.innerHTML = errBox(e); }
  }
}
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const mediaUrl = (f) => f.local ? f.local : `${API}/media?src=${encodeURIComponent(f.src || "")}${f.mime ? `&mime=${encodeURIComponent(f.mime)}` : ""}`;
function bars(key) {   // Wellenform: aus der Adresse abgeleitet, damit sie pro Nachricht gleich bleibt
  let h = 7;
  for (const c of String(key)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return Array.from({ length: 34 }, (_, i) => { h = (h * 1103515245 + 12345) >>> 0; return 18 + ((h >>> 16) % 70) * (0.55 + 0.45 * Math.sin((i / 34) * Math.PI)); });
}
function fileHtml(f, clips = null) {
  const src = esc(f.src || "");
  if (f.kind === "image") {
    const r = f.w && f.h ? ` style="aspect-ratio:${f.w}/${f.h}"` : "";
    return `<img class="pic ${f.sticker ? "stk" : ""}" loading="lazy" data-a="zoom" data-src="${src}" alt="${esc(f.name || "Bild")}"${r} src="${mediaUrl(f)}">`;
  }
  if (f.kind === "audio") {
    const t = f.transcript || S.voice[f.src];
    return `<div class="voice" data-src="${src}" data-mime="${esc(f.mime)}" data-dur="${f.duration || 0}"><button class="play" data-a="play" aria-label="Abspielen"><span class="ms">play_arrow</span></button>
      <div class="wave" data-a="seek">${bars(f.src).map((h) => `<i style="height:${h.toFixed(0)}%"></i>`).join("")}</div>
      <span class="dur">${f.duration ? mmss(f.duration) : ""}</span><button class="spd" data-a="speed" aria-label="Tempo">1×</button>
      <button class="vtbtn ${t ? "has" : ""}" data-a="vtext" data-src="${src}" aria-label="Als Text" title="Als Text anzeigen"><span class="ms">notes</span></button></div>${S.vopen[f.src] && t ? `<div class="vtext">${esc(t)}</div>` : ""}`;
  }
  if (f.kind === "video") {
    const r = f.w && f.h ? ` style="aspect-ratio:${f.w}/${f.h}"` : "";
    const total = clips ? clips.reduce((a, c) => a + (c.duration || 0), 0) : f.duration;
    return `<div class="vid"${r} data-a="video" data-src="${src}" data-mime="${esc(f.mime)}">${f.poster ? `<img class="poster" loading="lazy" alt="" src="${mediaUrl({ src: f.poster })}">` : ""}<span class="ms">play_arrow</span>${total ? `<small>${mmss(total)}</small>` : ""}${clips ? `<i class="vparts">${clips.length} Teile</i>` : ""}</div>`;
  }
  return `<a class="file" href="${mediaUrl(f)}" download="${esc(f.name || "Datei")}"><span class="ms">attach_file</span><span>${esc(f.name || "Datei")}${f.size ? ` <small>${f.size > 1e6 ? (f.size / 1e6).toFixed(1) + " MB" : Math.round(f.size / 1e3) + " KB"}</small>` : ""}</span></a>`;
}
// Lesebestätigung wie in Beeper: ein Häkchen = gesendet, zwei (mint) = gelesen. Wer wann gelesen hat, steht unter Halten → Info.
// In der Blase nur noch die Uhr beim Senden; Gesendet/Gesehen steht einmal unter meiner letzten Nachricht (seenRow).
const ticks = (m) => m.mine && !m.deleted && m.pending ? '<span class="ms tick">schedule</span>' : "";
function seenRow(m) {
  const r = (m.read_by || []).map((x) => clean(x.name)).filter(Boolean);
  const grp = cur()?.type === "group";
  const t = !m.read_by?.length ? "Gesendet" : grp && r.length ? `Gesehen von ${esc(r.length > 4 ? `${r.slice(0, 4).join(", ")} +${r.length - 4}` : r.join(", "))}` : "Gesehen";
  return `<div class="seenrow ${m.read_by?.length ? "read" : ""}"><span class="ms">${m.read_by?.length ? "done_all" : "check"}</span>${t}</div>`;
}
// Antworten wie in Beeper: über der Blase wer wem geantwortet hat, darunter die zitierte Nachricht gedimmt als kleine Blase,
// an der Seite eine dünne Linie, die beides verbindet. Antippen springt zur Nachricht.
function replyHead(m, net = null) {   // net: im gemeinsamen Verlauf die Farbe des Netzwerks dieser Nachricht
  const dn = net !== null ? ` data-net="${net}"` : "";
  const q = S.msgs.find((x) => x.id === m.reply_to);
  const f = q && !q.deleted && q.files[0];
  const th = !q?.text && f && !f.sticker && f.src && (f.kind === "image" || f.kind === "video")
    ? (f.kind === "image" || f.poster ? `<img class="rq-th" loading="lazy" alt="" src="${mediaUrl(f.kind === "image" ? f : { src: f.poster })}">`
      : `<video class="rq-th" preload="metadata" muted playsinline src="${mediaUrl(f)}#t=0.1"></video>`) + (f.kind === "video" ? '<span class="ms">play_arrow</span>' : "") : "";
  if (th) return `<div class="rqrow ${q.mine ? "r" : "l"} ${m.mine ? "me" : "them"} ${cur()?.type === "group" && !m.mine ? "grp" : ""}"${dn} data-a="jump" data-id="${esc(m.reply_to)}"><span class="rqline"></span><span class="rq-b th">${th}</span></div>`;
  const what = !q ? "Nachricht" : q.deleted ? "Gelöschte Nachricht" : q.text ? esc(q.text.replace(/\s+/g, " ").slice(0, 200))
    : f ? ({ audio: "🎤 Sprachnachricht", video: "🎬 Video", image: f.sticker ? "Sticker" : "📷 Foto" }[f.kind] || `📎 ${esc(f.name || "Datei")}`) : "Nachricht";
  const side = q ? (q.mine ? "r" : "l") : (m.mine ? "r" : "l");   // die Pille steht dort, wo die zitierte Nachricht steht
  const grp = cur()?.type === "group" && !m.mine;
  return `<div class="rqrow ${side} ${m.mine ? "me" : "them"} ${grp ? "grp" : ""}"${dn} data-a="jump" data-id="${esc(m.reply_to)}"><span class="rqline"></span><span class="rq-b">${what}</span></div>`;
}
function tmLine(m) {   // klein in der Blase: "bearbeitet", Häkchen – das Netzwerk zeigt die Farbe, die Uhrzeit kommt beim Wischen nach links
  const bits = m.edited && !m.deleted ? "bearbeitet" : "";
  const t = ticks(m);
  return bits || t ? `<span class="tm">${bits}${t}</span>` : "";
}
// Mehrere Bilder hintereinander (Album) als Stapel wie in Beeper: oben das erste, dahinter leicht verdreht die nächsten
// Snapchat teilt Videos in ~10-Sekunden-Stücke: aufeinanderfolgende reine Videos vom selben Absender werden ein Video
const vidOnly = (m) => m && !m.deleted && !m.pending && !m.text && !m.reply_to && m.files.length === 1 && m.files[0].kind === "video" && m.files[0].src;
const CLIPS = new Map();   // ID der ersten Nachricht → alle Stücke
const picOnly = (m) => m && !m.deleted && !m.pending && !m.text && !m.reply_to && m.files.length && m.files.every((f) => f.kind === "image" && !f.sticker && f.src);
function stackHtml(fs) {
  const top = fs[0], r = top.w && top.h ? ` style="aspect-ratio:${top.w}/${top.h}"` : "";
  return `<div class="stackw"><div class="stack" data-a="zoom" data-src="${esc(top.src)}">${fs.slice(1, 3).reverse().map((f, i) => `<img class="sk sk${i}" loading="lazy" alt="" src="${mediaUrl(f)}">`).join("")}
    <img class="pic sk-top" loading="lazy" alt="Bild"${r} src="${mediaUrl(top)}"><span class="skn">${fs.length}</span></div></div>`;
}
// Gruppen: Name steht wie bei Signal frei über der ersten Blase einer Folge, jede Person mit eigener Farbe
const WHO_COLORS = ["#f28b82", "#fbbc04", "#81c995", "#78d9ec", "#8ab4f8", "#c58af9", "#ff8bcb", "#fcad70", "#a8dab5", "#d7aefb"];
const whoColor = (k) => { let h = 0; for (const ch of String(k || "")) h = (h * 31 + ch.charCodeAt(0)) | 0; return WHO_COLORS[Math.abs(h) % WHO_COLORS.length]; };
function bubble(m, tail = true, stack = null, head = true) {
  const visual = !m.deleted && m.files.length && m.files.every((f) => (f.kind === "image" || f.kind === "video") && !f.sticker);
  const files = stack ? stackHtml(stack) : visual && !m.pending ? `<div class="mwrap"><div class="mcol">${m.files.map((f) => fileHtml(f, CLIPS.get(m.id) || null)).join("")}</div></div>` : m.files.map((f) => fileHtml(f)).join("");
  const split = visual && !stack && (m.text || (m.links || []).some((l) => l.title || l.img));   // Bild/Video und Text: zwei Blasen
  // Gelöscht: Hinweis – und darunter, was drinstand, wenn der Dienst die Nachricht vorher schon gesehen hatte
  const was = m.deleted && m.was ? `<div class="was">${(m.was.files || []).map((f) => fileHtml(f)).join("")}${m.was.text ? `<span class="tx">${autolink(esc(m.was.text))}</span>` : ""}</div>` : "";
  // Besteht der Text nur aus dem Link, der darunter als Vorschau kommt, reicht die Vorschau (sonst steht er doppelt da)
  const lk = (u) => String(u || "").trim().toLowerCase().replace(/^https?:\/\/(www\.)?/, "").replace(/[/#]+$/, "");
  const onlyLink = !m.deleted && m.text && (m.links || []).some((l) => (l.title || l.img) && lk(l.url) === lk(m.text));
  const body = m.deleted ? `<span class="gone"><span class="ms">block</span>${m.mine ? "Du hast diese Nachricht gelöscht" : "Diese Nachricht wurde gelöscht"}</span>${was}` : m.text && !onlyLink ? `<span class="tx">${mentionHtml(autolink(esc(m.text)))}</span>` : "";
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
  const links = m.deleted ? "" : (m.links || []).filter((l) => l.title || l.img).map((l) => `<a class="lp" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${l.img ? `<img loading="lazy" alt=""${l.w && l.h ? ` style="aspect-ratio:${l.w}/${l.h}"` : ""} src="${l.ext ? l.ext : mediaUrl({ src: l.img })}">` : ""}<span><b>${esc(l.title || host(l.url))}</b>${l.summary ? `<i>${esc(l.summary)}</i>` : ""}<small>${esc(host(l.url))}</small></span></a>`).join("");
  const group = "";
  const name = head && cur()?.type === "group" && !m.mine && clean(m.sender) ? `<div class="gname" style="color:${whoColor(m.sender_id || m.sender)}">${esc(clean(m.sender))}</div>` : "";
  const quote = "";
  const more = "";
  const rx = m.reactions?.length ? `<div class="rx">${m.reactions.map((r) => `<span class="${r.mine ? "mine" : ""}" data-a="rx" data-id="${esc(m.id)}" data-k="${esc(r.key)}">${esc(r.key)}${r.n > 1 ? ` ${r.n}` : ""}</span>`).join("")}</div>` : "";
  const tools = m.pending || m.deleted ? "" : `<button class="rep" data-a="reply" data-id="${esc(m.id)}" aria-label="Antworten" title="Antworten"><span class="ms">reply</span></button>`;
  const face = cur()?.type === "group" && !m.mine ? (tail ? avatar(clean(m.sender), cid(m), m.sender_id) : '<span class="av gap"></span>') : "";
  const prog = m.pendingFile ? `<div class="prog"><i style="width:${Math.round((m.progress || 0) * 100)}%"></i></div>` : "";
  const mc = cur()?.merged, net = mc ? netKey((mc.find((x) => x.id === (m.chat || target())) || {}).network) : null;
  return `${name}${m.reply_to && !m.deleted ? replyHead(m, net) : ""}<div class="bw ${m.mine ? "me" : ""} ${face ? "grp" : ""} ${m.reply_to && !m.deleted ? "isrep" : ""}"${net !== null ? ` data-net="${net}"` : ""} id="m-${esc(m.id)}" data-id="${esc(m.id)}"><span class="swipe ms">reply</span>${face}${tools}<div class="bcol">${quote}${split ? `<div class="b media ${m.mine ? "me" : ""}">${group}${files}</div>` : ""}<div class="b ${m.mine ? "me" : ""} ${!m.mine && atMe(m) ? "atme" : ""} ${m.pending ? "pending" : ""} ${m.deleted ? "del" : ""} ${!split && visual && !m.text ? "media" : ""}">${split ? "" : group}${split ? "" : files}${prog}${body}${links}${rx}${tmLine(m)}</div>${more}</div><span class="st">${m.at && !m.pending ? fmtTime.format(new Date(m.at)) : ""}</span></div>`;
}
const NETS = [["snapchat", "snapchat"], ["instagram", "instagram"], ["whatsapp", "whatsapp"], ["signal", "signal"], ["telegram", "telegram"], ["discord", "discord"],
  ["messenger", "messenger"], ["facebook", "messenger"], ["gmessages", "gmessages"], ["google messages", "gmessages"], ["googlechat", "googlechat"], ["google chat", "googlechat"],
  ["slack", "slack"], ["linkedin", "linkedin"], ["twitter", "x"], ["imessage", "imessage"], ["sms", "gmessages"]];
const netKey = (n) => { n = String(n || "").toLowerCase(); return (NETS.find(([k]) => n.includes(k)) || [, ""])[1]; };
// Steht der Chat am Ende? Nur dann darf nachladendes (Bilder, Tastatur) ans Ende schieben – wer hochgescrollt hat, bleibt dort.
let atBottom = true;
document.addEventListener("scroll", (e) => { const b = e.target; if (b.id === "msgs") atBottom = b.scrollHeight - b.scrollTop - b.clientHeight < 150; }, { capture: true, passive: true });
// Nachladendes (Bilder, Video-Vorschauen, Link-Vorschauen, Schrift) ändert die Höhe erst nach dem Zeichnen.
// Solange der Chat am Ende steht, hält ihn das am Ende – sonst landet man beim Öffnen irgendwo darüber.
const pinEnd = () => { const b = $("#msgs"); if (b && atBottom) b.scrollTop = b.scrollHeight; };
const growth = new ResizeObserver(pinEnd);
document.fonts?.ready.then(pinEnd);
let drawn = { id: null, ids: new Set() };
function drawMsgs({ stick = false, keepTop = false } = {}) {
  const box = $("#msgs"); if (!box) return;
  ogApply();
  const fresh = drawn.id === S.id && !keepTop ? S.msgs.slice(-6).filter((m) => !drawn.ids.has(m.id)) : [];
  const prevH = box.scrollHeight, prevTop = box.scrollTop;
  S.replies = new Map();
  for (const m of S.msgs) if (m.reply_to) S.replies.set(m.reply_to, [...(S.replies.get(m.reply_to) || []), m.id]);
  let last = "", html = S.more ? '<button class="older" data-a="older">Ältere laden</button>' : "";
  if (!S.msgs.length) html += '<div class="empty">Noch keine Nachrichten.</div>';
  const lastMine = [...S.msgs].reverse().find((m) => m.mine && !m.deleted && !m.pending)?.id;
  // Trennlinie über der ersten ungelesenen Nachricht (wie in Beeper), solange der Chat nicht als gelesen markiert ist
  let unFrom = -1;
  { const cc = cur(), nu = left(cc); if (nu) { let k = 0; for (let x = S.msgs.length - 1; x >= 0; x--) { if (S.msgs[x].mine) { unFrom = x + 1; break; } unFrom = x; if (++k === nu) break; } if (unFrom >= S.msgs.length) unFrom = -1; } }
  for (let i = 0; i < S.msgs.length; i++) {
    const m = S.msgs[i];
    if (unFrom >= 0 && i >= unFrom) { html += `<div class="unsep" id="unsep"><span>${left(cur())} ungelesen</span></div>`; unFrom = -1; }
    const k = m.at ? dayKey(m.at) : last;
    if (k !== last) { last = k; html += `<div class="day">${esc(fmtDay.format(new Date(m.at)))}</div>`; }
    let j = i, files = null;   // Bilder-Folge vom selben Absender innerhalb von 3 Minuten → ein Stapel
    if (vidOnly(m)) {
      while (vidOnly(S.msgs[j + 1]) && S.msgs[j + 1].sender_id === m.sender_id && S.msgs[j + 1].mine === m.mine && Date.parse(S.msgs[j + 1].at) - Date.parse(S.msgs[j].at) < 90000 && dayKey(S.msgs[j + 1].at) === k) j++;
      if (j > i) CLIPS.set(m.id, S.msgs.slice(i, j + 1).map((x) => x.files[0])); else CLIPS.delete(m.id);
    } else if (picOnly(m)) {
      while (picOnly(S.msgs[j + 1]) && S.msgs[j + 1].sender_id === m.sender_id && S.msgs[j + 1].mine === m.mine && Date.parse(S.msgs[j + 1].at) - Date.parse(S.msgs[j].at) < 180000 && dayKey(S.msgs[j + 1].at) === k) j++;
      files = S.msgs.slice(i, j + 1).flatMap((x) => x.files);
      if (files.length < 2) files = null;
    }
    const n = S.msgs[j + 1];   // Bild nur an der letzten Nachricht einer Folge vom selben Absender (wie in Beeper)
    const p = S.msgs[i - 1];   // Name nur an der ersten Nachricht einer Folge vom selben Absender
    const lm = S.msgs.slice(i, j + 1).some((x) => x.id === lastMine);
    html += bubble(m, !n || n.mine || n.sender_id !== m.sender_id || (n.at && dayKey(n.at) !== k), files, !p || p.mine || p.sender_id !== m.sender_id || (p.at && dayKey(p.at) !== k));
    if (lm) html += seenRow(S.msgs.find((x) => x.id === lastMine));
    i = j;
  }
  box.innerHTML = html;
  drawn = { id: S.id, ids: new Set(S.msgs.map((m) => m.id)) };
  for (const m of fresh) document.getElementById(`m-${m.id}`)?.classList.add("in");
  $("#conv").dataset.net = cur()?.merged ? "" : netKey(cur()?.network);   // eigene Nachrichten in Netzwerkfarbe, eingehende mit Akzenten in Netzwerkfarbe
  paint();
  if (keepTop) box.scrollTop = box.scrollHeight - prevH + prevTop;
  else if (stick) { atBottom = true; box.scrollTop = box.scrollHeight; requestAnimationFrame(pinEnd); }
  else box.scrollTop = prevTop;
  growth.disconnect(); growth.observe(box); for (const c of box.children) growth.observe(c);
  if (!box.dataset.pin) { box.dataset.pin = 1; for (const ev of ["load", "loadedmetadata", "error"]) box.addEventListener(ev, pinEnd, true); }
  ogFetch();
}
// Link-Vorschau selbst holen, wenn Beeper keine mitliefert (z. B. bei Snapchat): Bild, Titel, Text der Seite.
// Einmal pro Adresse, gemerkt für die Sitzung; nur die letzten 40 Nachrichten, damit beim Hochscrollen nicht alles auf einmal lädt.
const OG = new Map();
const urlOf = (m) => !m || m.deleted || (m.links || []).some((l) => l.title || l.img) ? "" : ((m.text || "").match(/https?:\/\/[^\s<>"')\]]+/) || [""])[0].replace(/[.,!?:;]+$/, "");
function ogApply() {
  for (const m of S.msgs) {
    const u = urlOf(m), c = u && OG.get(u);
    if (c && c !== 1 && (c.title || c.img)) m.links = [{ url: u, title: c.title, summary: c.summary, img: c.img ? "x" : "", ext: c.img ? `${API}/link/img?u=${encodeURIComponent(c.img)}` : "", w: c.w, h: c.h }];
  }
}
function ogFetch() {
  const id = S.id, todo = [...new Set(S.msgs.slice(-40).map(urlOf).filter((u) => u && !OG.has(u)))].slice(0, 6);
  if (!todo.length) return;
  for (const u of todo) OG.set(u, 1);
  Promise.all(todo.map((u) => api(`/link?url=${encodeURIComponent(u)}`).then((r) => OG.set(u, r), () => OG.set(u, {}))))
    .then(() => { if (S.id === id && todo.some((u) => OG.get(u)?.title || OG.get(u)?.img)) drawMsgs({ stick: atBottom }); });
}
$("#conv").addEventListener("click", (e) => {
  const a = e.target.closest("[data-a]");
  if (!a) return;
  if (a.dataset.a === "back") return back();
  if (a.dataset.a === "gset") return groupSettings(cur());
  if (a.dataset.a === "info") return openInfo();
  if (a.dataset.a === "older") return loadMsgs({ older: true });
  if (a.dataset.a === "reply") return setReply(S.msgs.find((m) => m.id === a.dataset.id));
  if (a.dataset.a === "seen") return markRead(cur());
  if (a.dataset.a === "unseen") return markUnread(cur());
  if (a.dataset.a === "rx") { const m = S.msgs.find((x) => x.id === a.dataset.id); return m && toggleReaction(m, a.dataset.k); }
  if (a.dataset.a === "cancel") return S.edit ? setEdit(null) : setReply(null);
  if (a.dataset.a === "replies") {
    const ids = S.replies?.get(a.dataset.id) || []; if (!ids.length) return;
    const i = ((+a.dataset.n || 0)) % ids.length; a.dataset.n = i + 1;
    const el = document.getElementById(`m-${ids[i]}`);
    if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200); }
    return;
  }
  if (a.dataset.a === "jump") { const el = document.getElementById(`m-${a.dataset.id}`); if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200); } return; }
  if (a.dataset.a === "play") return togglePlay(a.closest(".voice"));
  if (a.dataset.a === "speed") return cycleSpeed(a);
  if (a.dataset.a === "seek") return seek(a.closest(".voice"), e);
  if (a.dataset.a === "vtext") return voiceText(a);
  if (a.dataset.a === "zoom") return a.dataset.src ? gallery(a.dataset.src) : lightbox(a.src);
  if (a.dataset.a === "video") return playVideo(a);
});
// ---------------- Sprachnachrichten ----------------
const audio = new Audio();
audio.preload = "none";
let cur$ = null, speed = 1;   // cur$ = Adresse der laufenden Nachricht
const voiceEl = (src) => [...document.querySelectorAll(".voice")].find((v) => v.dataset.src === src);
function paint() {   // Zustand der Player nach jedem Neuzeichnen und bei jedem Tick
  document.querySelectorAll(".voice").forEach((v) => {
    const on = v.dataset.src === cur$, d = on && isFinite(audio.duration) ? audio.duration : +v.dataset.dur || 0;
    const p = on && d ? audio.currentTime / d : 0;
    v.classList.toggle("playing", on && !audio.paused);
    v.querySelector(".play .ms").textContent = on && !audio.paused ? "pause" : "play_arrow";
    v.querySelectorAll(".wave i").forEach((b, i, all) => b.classList.toggle("on", i / all.length < p));
    v.querySelector(".dur").textContent = d ? mmss(on && audio.currentTime > 0 ? audio.currentTime : d) : "";
    v.querySelector(".spd").textContent = `${speed}×`.replace("1.5", "1,5");
  });
}
audio.addEventListener("timeupdate", paint);
audio.addEventListener("play", paint);
audio.addEventListener("pause", paint);
audio.addEventListener("ended", () => { audio.currentTime = 0; paint(); });
audio.addEventListener("error", () => { toast("Sprachnachricht konnte nicht geladen werden"); paint(); });
function togglePlay(v) {
  const src = v.dataset.src;
  if (cur$ !== src) {
    audio.pause(); cur$ = src;
    audio.src = `${API}/media?src=${encodeURIComponent(src)}${v.dataset.mime ? `&mime=${encodeURIComponent(v.dataset.mime)}` : ""}`;
    audio.playbackRate = speed;
  }
  if (audio.paused) audio.play().catch(() => {}); else audio.pause();
  paint();
}
function cycleSpeed(btn) {
  speed = speed === 1 ? 1.5 : speed === 1.5 ? 2 : 1;
  audio.playbackRate = speed; paint();
}
function seek(v, e) {
  if (v.dataset.src !== cur$) togglePlay(v);
  const r = v.querySelector(".wave").getBoundingClientRect(), p = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  const go = () => { if (isFinite(audio.duration)) audio.currentTime = audio.duration * p; };
  isFinite(audio.duration) ? go() : audio.addEventListener("loadedmetadata", go, { once: true });
}
async function voiceText(btn) {
  const src = btn.dataset.src, f = S.msgs.flatMap((m) => m.files).find((x) => x.src === src);
  S.vopen[src] = !S.vopen[src];
  if (S.vopen[src] && !(f?.transcript || S.voice[src])) {
    btn.disabled = true;
    try { S.voice[src] = (await api(`/transcript?src=${encodeURIComponent(src)}`)).text || "(kein Text erkannt)"; } catch (e) { S.vopen[src] = false; toast(e.message); }
  }
  drawMsgs();
}
// Video-Player wie in Beeper: oben "Video · MP4 Video", groß in der Mitte, dicker Spulbalken, Text der Nachricht, unten Pause, Reaktion, Antworten, Download
function playVideo(el) {
  document.querySelector(".vp")?.remove();
  const m = bubbleMsg(el), mime = el.dataset.mime || "video/mp4";
  // Abspielliste: bei zusammengefügten Snapchat-Stücken alle, sonst nur das eine Video
  const clips = (m && CLIPS.get(m.id)) || [{ src: el.dataset.src, mime: el.dataset.mime, duration: 0 }];
  const urlOf = (c) => `${API}/media?src=${encodeURIComponent(c.src)}${c.mime ? `&mime=${encodeURIComponent(c.mime)}` : ""}`;
  const dur = clips.map((c) => c.duration || 0);
  const kindName = `${(mime.split("/")[1] || "mp4").split(";")[0].replace("quicktime", "mov").toUpperCase()} Video`;
  const d = document.createElement("div");
  d.className = "vp";
  d.innerHTML = `<div class="vp-top"><button class="icon-btn" data-v="close" aria-label="Zurück"><span class="ms">arrow_back</span></button><div><b>Video</b><small>${esc(kindName)}${clips.length > 1 ? ` · ${clips.length} Teile` : ""}${m && !m.mine && m.sender ? ` · ${esc(clean(m.sender))}` : ""}</small></div></div>
    <div class="vp-stage"><video playsinline preload="auto"></video><video playsinline preload="auto" class="next"></video><button class="vp-big" data-v="play" aria-label="Pause"><span class="ms">pause</span></button></div>
    <div class="vp-bar"><div class="vp-track" data-v="seek">${clips.length > 1 ? '<span class="vp-cuts"></span>' : ""}<i class="vp-fill"></i><b class="vp-thumb"></b><i class="vp-end"></i></div><span class="vp-time">0:00</span></div>
    ${m?.text ? `<div class="vp-cap">${autolink(esc(m.text))}</div>` : ""}
    <div class="vp-rx" hidden>${QUICK.map((k) => `<button data-r="${esc(k)}">${esc(k)}</button>`).join("")}</div>
    <div class="vp-acts"><button data-v="play" aria-label="Pause"><span class="ms">pause</span></button>${m ? '<button data-v="emo" aria-label="Reagieren"><span class="ms">add_reaction</span></button><button data-v="reply" aria-label="Antworten"><span class="ms">reply</span></button>' : ""}
      <a data-v="dl" href="${urlOf(clips[0])}" download="video.mp4" aria-label="Herunterladen"><span class="ms">download</span></a></div>`;
  const [vA, vB] = d.querySelectorAll("video"), fill = d.querySelector(".vp-fill"), thumb = d.querySelector(".vp-thumb"), time = d.querySelector(".vp-time"), big = d.querySelector(".vp-big");
  const cuts = d.querySelector(".vp-cuts"), dl = d.querySelector("[data-v=dl]");
  // Zwei Video-Elemente: eins spielt, das andere lädt schon das nächste Stück – beim Ende wird nur umgeschaltet (kein Ruckler)
  let act = vA, idle = vB, i = 0, raf = 0, hideT, wantPlay = true;
  const load = (v, k) => { v.dataset.k = k; v.src = urlOf(clips[k]); v.load(); };
  const before = (k) => dur.slice(0, k).reduce((a, x) => a + x, 0);
  const total = () => dur.reduce((a, x) => a + x, 0);
  const paintBar = () => {
    const t = before(i) + (act.currentTime || 0), T = total(), p = T ? Math.min(1, t / T) : 0;
    fill.style.width = `calc(${p * 100}% - 6px)`; thumb.style.left = `${p * 100}%`;
    time.textContent = `${mmss(t)} / ${T ? mmss(T) : "–:–"}`;
    if (cuts && T) cuts.innerHTML = dur.slice(0, -1).map((_, k) => `<i style="left:${(before(k + 1) / T) * 100}%"></i>`).join("");
  };
  const icons = () => { const ic = act.paused && !wantPlay ? "play_arrow" : "pause"; d.querySelectorAll("[data-v=play] .ms").forEach((x) => (x.textContent = ic)); };
  const loop = () => { paintBar(); if (!act.paused) raf = requestAnimationFrame(loop); };   // nur solange es läuft
  const flash = () => { big.classList.add("show"); clearTimeout(hideT); if (wantPlay) hideT = setTimeout(() => big.classList.remove("show"), 900); };
  const show = () => { act.classList.remove("next"); idle.classList.add("next"); dl.href = urlOf(clips[i]); };
  const preloadNext = () => { if (i + 1 < clips.length) { if (idle.dataset.k !== String(i + 1)) load(idle, i + 1); } };
  function go(k, at = 0) {   // zu Stück k springen (at = Sekunde darin)
    if (k === +idle.dataset.k && idle.readyState >= 1) { [act, idle] = [idle, act]; act.pause(); }
    else if (k !== i || !act.src) load(act, k);
    i = k; show();
    const start = () => { try { act.currentTime = at; } catch { /* noch nicht bereit */ } if (wantPlay) act.play().catch(() => {}); paintBar(); };
    act.readyState >= 1 ? start() : act.addEventListener("loadedmetadata", start, { once: true });
    idle.pause(); preloadNext();
  }
  for (const v of [vA, vB]) {
    v.addEventListener("loadedmetadata", () => { const k = +v.dataset.k; if (isFinite(v.duration)) dur[k] = v.duration; paintBar(); });
    v.addEventListener("play", () => { if (v !== act) return; icons(); flash(); cancelAnimationFrame(raf); loop(); });
    v.addEventListener("pause", () => { if (v !== act) return; icons(); cancelAnimationFrame(raf); paintBar(); });
    v.addEventListener("ended", () => { if (v !== act) return; if (i + 1 < clips.length) go(i + 1); else { wantPlay = false; icons(); flash(); } });
    v.addEventListener("error", () => { if (v === act) toast("Video konnte nicht geladen werden"); });
  }
  const toggle = () => { if (act.ended && i === clips.length - 1) { wantPlay = true; return go(0); } wantPlay = act.paused; wantPlay ? act.play() : act.pause(); icons(); flash(); };
  const track = d.querySelector(".vp-track");
  const seekTo = (x) => {   // Position auf dem gemeinsamen Balken → Stück + Sekunde darin
    const r = track.getBoundingClientRect(), T = total(); if (!T) return;
    let t = Math.min(1, Math.max(0, (x - r.left) / r.width)) * T, k = 0;
    while (k < clips.length - 1 && t >= dur[k]) { t -= dur[k]; k++; }
    if (k === i) { act.currentTime = t; paintBar(); } else go(k, t);
  };
  track.addEventListener("pointerdown", (e) => { track.setPointerCapture(e.pointerId); track.classList.add("drag"); seekTo(e.clientX); });
  track.addEventListener("pointermove", (e) => { if (track.hasPointerCapture(e.pointerId)) seekTo(e.clientX); });
  track.addEventListener("pointerup", () => track.classList.remove("drag"));
  const close = (fromHistory) => { if (fromHistory !== true) ovClosed(); wantPlay = false; vA.pause(); vB.pause(); cancelAnimationFrame(raf); d.classList.add("out"); setTimeout(() => { vA.removeAttribute("src"); vB.removeAttribute("src"); d.remove(); }, 180); };
  d.addEventListener("click", (e) => {
    const r = e.target.closest("[data-r]"); if (r && m) { toggleReaction(m, r.dataset.r); d.querySelector(".vp-rx").hidden = true; return toast(`${r.dataset.r} gesendet`); }
    const a = e.target.closest("[data-v]")?.dataset.v;
    if (a === "close") return close();
    if (a === "play" || (!a && e.target.closest(".vp-stage"))) return toggle();
    if (a === "emo") { const x = d.querySelector(".vp-rx"); x.hidden = !x.hidden; return; }
    if (a === "reply") { close(); return setReply(m); }
  });
  addEventListener("keydown", function k(e) { if (!d.isConnected) return removeEventListener("keydown", k); if (e.key === "Escape") close(); if (e.key === " ") { e.preventDefault(); toggle(); } });
  document.body.append(d);
  ovOpen(m ? `video/${encodeURIComponent(m.id)}` : "bild", () => close(true));
  go(0);
}
// Vollbild wie in Beeper: alle Bilder des Chats, durchwischen nach links/rechts, nach unten wischen schließt
function lightbox(src, items = null, start = 0) {
  document.querySelector(".lightbox")?.remove();
  const list = items || [{ url: src }];
  const d = document.createElement("div");
  d.className = "lightbox";
  d.innerHTML = `<div class="lb-top"><button class="icon-btn" data-lb="close" aria-label="Schließen"><span class="ms">close</span></button><div class="lb-info"></div></div>
    <div class="lb-track">${list.map((x) => `<div class="lb-slide"><img alt="" decoding="async" ${Math.abs(list.indexOf(x) - start) < 2 ? "" : 'loading="lazy" '}src="${x.url}"></div>`).join("")}</div>
    <div class="lb-bot"><div class="vp-rx" hidden>${QUICK.map((k) => `<button data-r="${esc(k)}">${esc(k)}</button>`).join("")}</div>
    <div class="vp-acts"><button data-lb="emo" aria-label="Reagieren"><span class="ms">add_reaction</span></button><button data-lb="reply" aria-label="Antworten"><span class="ms">reply</span></button>
      <button data-lb="fwd" aria-label="Weiterleiten"><span class="ms">forward</span></button><a data-lb="dl" aria-label="Herunterladen" download><span class="ms">download</span></a></div></div>`;
  const track = d.querySelector(".lb-track"), info = d.querySelector(".lb-info"), dl = d.querySelector("[data-lb=dl]"), rx = d.querySelector(".vp-rx");
  let cur_ = start;
  const show = () => { const x = list[cur_] || {}; ovSet(x.sub || "bild"); info.innerHTML = `<b>${esc(x.who || "")}</b><small>${x.at ? `${fmtDay.format(new Date(x.at))}, ${fmtTime.format(new Date(x.at))}` : ""}${list.length > 1 ? ` · ${cur_ + 1} von ${list.length}` : ""}</small>`; dl.href = x.url; dl.download = x.name || "bild.jpg";
    for (const b of d.querySelectorAll("[data-lb=emo], [data-lb=reply], [data-lb=fwd]")) b.hidden = !x.m; rx.hidden = true; };
  track.addEventListener("scroll", () => { const i = Math.round(track.scrollLeft / track.clientWidth); if (i !== cur_) { cur_ = i; show(); } }, { passive: true });
  const close = (fromHistory) => { if (fromHistory !== true) ovClosed(); d.classList.add("out"); setTimeout(() => d.remove(), 180); };
  d.addEventListener("click", (e) => {
    const m = list[cur_]?.m, r = e.target.closest("[data-r]");
    if (r && m) { toggleReaction(m, r.dataset.r); rx.hidden = true; return toast(`${r.dataset.r} gesendet`); }
    const a = e.target.closest("[data-lb]")?.dataset.lb;
    if (a === "emo") { rx.hidden = !rx.hidden; return; }
    if (a === "reply" && m) { close(); return setReply(m); }
    if (a === "fwd" && m) { close(); return forwardPick(m); }
    if (a === "close" || e.target.classList.contains("lb-slide")) close();
  });
  let y0 = null, dy = 0;   // nach unten wischen schließt
  d.addEventListener("touchstart", (e) => { y0 = e.touches.length === 1 ? e.touches[0].clientY : null; dy = 0; }, { passive: true });
  d.addEventListener("touchmove", (e) => { if (y0 === null) return; dy = e.touches[0].clientY - y0; if (dy > 0) { track.style.transform = `translateY(${dy}px)`; d.style.setProperty("--lbo", Math.max(.3, 1 - dy / 400)); } }, { passive: true });
  d.addEventListener("touchend", () => { if (y0 === null) return; y0 = null; if (dy > 110) return close(); track.style.transform = ""; d.style.removeProperty("--lbo"); });
  addEventListener("keydown", function k(e) { if (!d.isConnected) return removeEventListener("keydown", k);
    if (e.key === "Escape") close(); if (e.key === "ArrowRight" || e.key === "ArrowLeft") track.scrollBy({ left: (e.key === "ArrowRight" ? 1 : -1) * track.clientWidth, behavior: "smooth" }); });
  document.body.append(d);
  ovOpen(list[start]?.sub || "bild", () => close(true));
  track.scrollLeft = start * track.clientWidth; show();
}
function gallery(src) {   // alle Bilder des geladenen Chats, beim angetippten anfangen
  const pics = S.msgs.flatMap((m) => m.deleted ? [] : m.files.filter((f) => f.kind === "image" && !f.sticker && f.src).map((f, n) => ({ src: f.src, url: mediaUrl(f), at: m.at, sub: `foto/${encodeURIComponent(m.id)}${n ? `/${n + 1}` : ""}`, m, name: f.name, who: m.mine ? "Du" : clean(m.sender) || clean(cur()?.title || "") })));
  const i = pics.findIndex((p) => p.src === src);
  if (i < 0) return lightbox(mediaUrl({ src }));
  lightbox(null, pics, i);
}
function avatarBig(el) {   // Profilbild groß ansehen
  const img = el.querySelector?.(".avimg.ok"); if (!img) return false;
  lightbox(null, [{ url: img.getAttribute("src"), who: el.closest(".row, .iperson, .ihero, .bw")?.querySelector(".rtitle, .it b, h2")?.textContent || "" }], 0);
  return true;
}

// ---------------- Wischen, langes Drücken, Menü ----------------
const buzz = (ms = 12) => navigator.vibrate?.(ms);
function bubbleMsg(el) { const w = el.closest(".bw"); return w ? S.msgs.find((m) => m.id === w.dataset.id) : null; }
// Langes Drücken (oder Rechtsklick): die Nachricht kommt nach vorne, der Chat dahinter wird unscharf;
// darüber die Reaktionen, darunter die Aktionen – wie in Beeper am Handy.
const QUICK = ["❤️", "👍", "😂", "😮", "😢", "🙏"];
const MORE = ["🔥", "👏", "🥰", "😍", "🤣", "😅", "😭", "😡", "🤔", "👀", "🎉", "💯", "✅", "❌", "🙈", "😎", "🤝", "💀"];
function menu(m) {
  closeMenu();
  const src = document.getElementById(`m-${m.id}`)?.querySelector(".b");
  if (!src) return;
  const r = src.getBoundingClientRect(), mine = new Set((m.reactions || []).filter((x) => x.mine).map((x) => x.key));
  const d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  const emo = (k) => `<button class="emo ${mine.has(k) ? "on" : ""}" data-r="${esc(k)}" aria-label="${esc(k)}">${esc(k)}</button>`;
  d.innerHTML = `<div class="ctx-back"></div><div class="ctx-stack ${m.mine ? "me" : ""}">
    ${m.pending || m.deleted ? "" : `<div class="ctx-rx">${QUICK.map(emo).join("")}<button class="emo plus" data-c="more" aria-label="Mehr"><span class="ms">add</span></button></div>
    <div class="ctx-more" hidden>${MORE.map(emo).join("")}</div>`}
    <div class="ctx-msg"></div>
    <div class="ctx-box">
      <button data-c="reply"><span class="ms">reply</span>Antworten</button>
      ${m.pending ? "" : '<button data-c="info"><span class="ms">info</span>Info</button>'}
      ${m.text ? '<button data-c="copy"><span class="ms">content_copy</span>Text kopieren</button>' : ""}
      ${m.mine && m.text && !m.files.length && !m.pending && !m.deleted ? '<button data-c="edit"><span class="ms">edit</span>Bearbeiten</button>' : ""}
      ${!m.pending && !m.deleted && (m.text || m.files.length) ? '<button data-c="fwd"><span class="ms">forward</span>Weiterleiten</button>' : ""}
      ${m.files[0] ? '<button data-c="open"><span class="ms">open_in_new</span>Datei öffnen</button>' : ""}
      ${m.mine || m.pending ? "" : '<button data-c="unread"><span class="ms">mark_chat_unread</span>Ab hier ungelesen</button>'}
      ${m.deleted && m.was?.text ? '<button data-c="copywas"><span class="ms">content_copy</span>Text kopieren</button>' : ""}
      ${m.mine && !m.pending && !m.deleted ? '<button class="danger" data-c="delall"><span class="ms">delete_forever</span>Für alle löschen</button>' : ""}
      ${m.pending ? "" : `<button class="danger" data-c="delme"><span class="ms">delete</span>${m.mine && !m.deleted ? "Nur für mich löschen" : "Für mich löschen"}</button>`}
    </div></div>`;
  const clone = src.cloneNode(true);
  clone.removeAttribute("id"); clone.querySelectorAll("[id]").forEach((x) => x.removeAttribute("id"));
  clone.style.width = `${r.width}px`;
  d.querySelector(".ctx-msg").append(clone);
  d.querySelector(".ctx-msg").dataset.net = document.getElementById(`m-${m.id}`)?.dataset.net ?? netKey(cur()?.network);
  d.addEventListener("click", (e) => {
    const rb = e.target.closest("[data-r]");
    if (rb) { closeMenu(); return toggleReaction(m, rb.dataset.r); }
    const c = e.target.closest("[data-c]")?.dataset.c;
    if (c === "more") { d.querySelector(".ctx-more").hidden = false; e.target.closest(".plus").remove(); return place(); }
    if (e.target.closest(".ctx-msg")) return;
    closeMenu();
    if (c === "reply") setReply(m);
    if (c === "info") msgInfo(m);
    if (c === "edit") setEdit(m);
    if (c === "fwd") forwardPick(m);
    if (c === "copy") navigator.clipboard?.writeText(m.text).then(() => toast("Kopiert"), () => toast("Kopieren nicht möglich"));
    if (c === "unread") markUnread(m.chat ? { ...(known(m.chat) || { id: m.chat }), id: m.chat } : cur(), mid(m));
    if (c === "copywas") navigator.clipboard?.writeText(m.was.text).then(() => toast("Kopiert"), () => toast("Kopieren nicht möglich"));
    if (c === "delall") deleteMsg(m, true);
    if (c === "delme") deleteMsg(m, false);
    if (c === "open") window.open(mediaUrl(m.files[0]), "_blank", "noopener");
  });
  document.body.append(d);
  src.closest(".bw").classList.add("lifted");
  const stack = d.querySelector(".ctx-stack");
  const box = stack.querySelector(".ctx-msg");
  // Lange Nachrichten: die Blase bekommt so viel Höhe wie neben Reaktionen und Aktionen frei ist und scrollt darin
  const fade = () => box.classList.toggle("more", box.scrollHeight - box.scrollTop - box.clientHeight > 4);
  box.addEventListener("scroll", fade, { passive: true });
  function place() {   // so nah wie möglich an der echten Stelle, aber ganz im Bild
    const sat = parseFloat(getComputedStyle(document.body).getPropertyValue("--sat")) || 0;
    // Platz = Bildschirm minus Reaktionsleiste, Menü und Abstände (direkt gemessen, nicht über die Stapelhöhe)
    const rest = [...stack.children].filter((x) => x !== box && x.offsetParent).reduce((a, x) => a + x.offsetHeight + 8, 0);
    box.style.maxHeight = `${Math.max(160, innerHeight - 24 - sat - rest)}px`;
    box.scrollTop = 0;
    fade();
    const h = stack.offsetHeight, w = stack.offsetWidth, top0 = r.top - (stack.querySelector(".ctx-msg").offsetTop);
    const top = Math.max(12 + sat, Math.min(top0, innerHeight - h - 12));
    const left = m.mine ? Math.min(r.right, innerWidth - 8) - w : Math.max(8, r.left);
    stack.style.top = `${top}px`; stack.style.left = `${Math.max(8, Math.min(left, innerWidth - w - 8))}px`;
  }
  place();
  const onRs = () => (d.isConnected ? place() : removeEventListener("resize", onRs));
  addEventListener("resize", onRs);
  buzz(20);
}
// Nachrichten-Info: wann gesendet, über welches Netzwerk und wer sie wann gelesen hat (statt "Gelesen von …" im Chat)
function msgInfo(m) {
  closeMenu();
  const at = (iso) => { const d = new Date(iso); return `${fmtDay.format(d)}, ${fmtTime.format(d)}`; };
  const mc = cur()?.merged, via = mc ? (mc.find((x) => x.id === m.chat) || {}).network : cur()?.network;
  const row = (av, t, sub, icon = "") => `<div class="fwd-row inf">${av}<span class="fwd-t">${t}<small>${sub}</small></span>${icon}</div>`;
  const seen = m.read_by || [];
  const d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet fwd gset"><div class="fwd-top"><b>Nachrichteninfo</b><button class="icon-btn" data-x="close" aria-label="Schließen"><span class="ms">close</span></button></div>
    <div class="fwd-list">
      ${row('<span class="av"><span class="ms">' + (m.mine ? "send" : "call_received") + "</span></span>", m.mine ? "Gesendet" : `Von ${esc(clean(m.sender) || "Unbekannt")}`, `${m.at ? esc(at(m.at)) : ""}${via ? ` · ${esc(netName(via))}` : ""}`)}
      ${m.edited ? row('<span class="av"><span class="ms">edit</span></span>', "Bearbeitet", "") : ""}
      ${m.mine ? `<div class="gset-h">Gelesen von${seen.length ? ` (${seen.length})` : ""}</div>
      ${seen.length ? seen.map((r) => row(avatar(clean(r.name) || "?", cid(m), r.id), esc(clean(r.name) || "Jemand"), r.at ? esc(at(r.at)) : "Zeit unbekannt", '<span class="ms inf-ok">done_all</span>')).join("")
        : `<div class="inf-none">${cur()?.type === "group" ? "Noch hat niemand die Nachricht gelesen." : "Noch nicht gelesen."}</div>`}` : ""}
    </div></div>`;
  d.addEventListener("click", (e) => { if (e.target.closest("[data-x=close]") || e.target.classList.contains("ctx-back")) closeMenu(); });
  document.body.append(d);
}
async function toggleReaction(m, key) {
  const had = (m.reactions || []).find((x) => x.key === key && x.mine);
  const before = JSON.stringify(m.reactions || []);
  m.reactions = m.reactions || [];
  if (had) { had.n -= 1; had.mine = false; m.reactions = m.reactions.filter((x) => x.n > 0); }
  else { const e = m.reactions.find((x) => x.key === key); if (e) { e.n += 1; e.mine = true; } else m.reactions.push({ key, n: 1, mine: true }); }
  drawMsgs(); buzz(10);
  try {
    const path = `/chats/${encodeURIComponent(cid(m))}/messages/${encodeURIComponent(mid(m))}/react`;
    if (had) await api(`${path}?key=${encodeURIComponent(key)}`, { method: "DELETE" });
    else await api(path, { method: "POST", body: JSON.stringify({ key }) });
    setTimeout(() => loadMsgs({ quiet: true }), 1200);
  } catch (e) {
    m.reactions = JSON.parse(before); drawMsgs(); toast(`Reaktion ging nicht: ${e.message}`);
  }
}
async function deleteMsg(m, everyone) {
  if (!confirm(everyone ? "Nachricht für alle löschen? Das lässt sich nicht rückgängig machen." : "Nachricht nur bei dir löschen?")) return;
  const id = S.id;
  try {
    await api(`/chats/${encodeURIComponent(cid(m))}/messages/${encodeURIComponent(mid(m))}${everyone ? "?everyone=true" : ""}`, { method: "DELETE" });
    if (id !== S.id) return;
    if (everyone) { if (m.text || m.files.length) m.was = { text: m.text, files: m.files }; m.deleted = true; m.text = ""; m.files = []; m.reactions = []; }
    else S.msgs = S.msgs.filter((x) => x !== m);
    drawMsgs({ keepTop: true });
    toast(everyone ? "Für alle gelöscht" : "Bei dir gelöscht");
    setTimeout(() => loadMsgs({ quiet: true }), 1500);
  } catch (e) { toast(`Löschen ging nicht: ${e.message}`); }
}
const closeMenu = () => { document.getElementById("ctx")?.remove(); document.querySelectorAll(".bw.lifted").forEach((x) => x.classList.remove("lifted")); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
const gest = { w: null, x: 0, y: 0, dx: 0, t: 0, lock: false, armed: false, dir: 1 };
function gestEnd() {
  clearTimeout(gest.t);
  if (gest.w) { gest.w.style.transform = ""; gest.w.classList.remove("armed", "drag"); }
  gest.w = null;
}
const msgsEl = () => document.getElementById("msgs");
document.addEventListener("touchstart", (e) => {
  const w = e.target.closest?.("#msgs .bw");
  if (!w || e.touches.length !== 1 || e.target.closest(".voice,video,.vid,a")) return;
  if (TS.open) return;
  const t = e.touches[0];
  // Fremde Nachrichten nach rechts ziehen, eigene (rechts stehend) nach links
  Object.assign(gest, { w, x: t.clientX, y: t.clientY, dx: 0, lock: false, armed: false, dir: w.classList.contains("me") ? -1 : 1 });
  gest.t = setTimeout(() => { const m = bubbleMsg(w); if (m && !gest.lock) { gest.w = null; menu(m); } }, 450);
}, { passive: true });
document.addEventListener("touchmove", (e) => {
  if (!gest.w) return;
  const t = e.touches[0], dx = t.clientX - gest.x, dy = t.clientY - gest.y;
  if (!gest.lock && (Math.abs(dy) > 10 || Math.abs(dx) > 10)) {
    clearTimeout(gest.t);
    if (Math.abs(dx) > Math.abs(dy) * 1.4 && dx * gest.dir > 0) gest.lock = true; else return gestEnd();
  }
  if (!gest.lock) return;
  if (e.cancelable) e.preventDefault();   // nur die eine Nachricht bewegt sich, der Chat bleibt stehen
  gest.dx = Math.max(0, Math.min(dx * gest.dir, 90));
  gest.w.classList.add("drag");
  gest.w.style.transform = `translateX(${gest.dx * 0.8 * gest.dir}px)`;
  const armed = gest.dx > 60;
  if (armed && !gest.armed) buzz(15);
  gest.armed = armed; gest.w.classList.toggle("armed", armed);
}, { passive: false });
document.addEventListener("touchend", () => {
  if (gest.w && gest.armed) { const m = bubbleMsg(gest.w); if (m && !m.pending) setReply(m); }
  gestEnd();
});
document.addEventListener("touchcancel", gestEnd);
// Uhrzeiten: nach links ziehen. Weit genug gezogen rastet es ein (kurzes Ruckeln) und bleibt offen; zurück nach rechts schieben schließt es.
const TS = { x: 0, y: 0, on: false, dead: false, open: false, sx: 0, snap: false };
const TS_MAX = 64, TS_SNAP = 40;
function tsSet(b, sx) { TS.sx = sx; b.style.setProperty("--sx", `${sx}px`); }
document.addEventListener("touchstart", (e) => {
  // Auf eigenen Nachrichten ist Nach-links-Ziehen „Antworten“; Uhrzeiten dort nur schließen, nicht öffnen
  const b = e.target.closest?.("#msgs"); TS.dead = !b || e.touches.length !== 1 || !!e.target.closest(".voice,video,.vid,.rq") || (!TS.open && !!e.target.closest(".bw.me"));
  if (TS.dead) return;
  TS.x = e.touches[0].clientX; TS.y = e.touches[0].clientY; TS.on = false; TS.snap = TS.open;
}, { passive: true });
document.addEventListener("touchmove", (e) => {
  if (TS.dead) return;
  const dx = e.touches[0].clientX - TS.x, dy = e.touches[0].clientY - TS.y, b = msgsEl(); if (!b) return;
  if (!TS.on) {
    if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { TS.dead = true; return; }
    if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy) * 1.4 && (dx < 0 || TS.open)) { TS.on = true; b.classList.add("ts"); } else return;
  }
  if (e.cancelable) e.preventDefault();
  const base = TS.open ? TS_MAX : 0, sx = Math.min(TS_MAX, Math.max(0, base - dx * .7));
  const past = sx >= TS_SNAP;
  if (past !== TS.snap) { TS.snap = past; buzz(8); }   // Raste spürbar
  tsSet(b, sx);
}, { passive: false });
const tsEnd = () => {
  if (!TS.on) return; TS.on = false;
  const b = msgsEl(); if (!b) return;
  b.classList.remove("ts");
  TS.open = TS.sx >= TS_SNAP;
  b.classList.toggle("tsopen", TS.open);
  if (TS.open) tsSet(b, TS_MAX); else { TS.sx = 0; b.style.removeProperty("--sx"); }
};
document.addEventListener("touchend", tsEnd); document.addEventListener("touchcancel", tsEnd);
document.addEventListener("contextmenu", (e) => {   // Rechtsklick am PC, langes Drücken am Handy (falls der Browser es meldet)
  const w = e.target.closest?.("#msgs .bw");
  if (!w || e.target.closest("video")) return;
  const m = bubbleMsg(w);
  if (m && !m.pending) { e.preventDefault(); menu(m); }
});

// Bearbeiten: der Text steht im Eingabefeld, Senden speichert die Änderung (nur eigene Textnachrichten; ob es geht, entscheidet das Netzwerk)
function setEdit(m) {
  const ta = $("#txt"), bar = $("#replybar"); if (!ta || !bar) return;
  if (m) S.reply = null;
  S.edit = m || null;
  bar.hidden = !m;
  const mc = cur()?.merged;   // gemeinsamer Verlauf: Leiste in der Farbe des Netzwerks, auf das geantwortet wird
  if (m && mc) bar.dataset.net = netKey((mc.find((x) => x.id === (m.chat || target())) || {}).network); else delete bar.dataset.net;
  ta.value = m ? m.text : ""; ta.dispatchEvent(new Event("input"));
  if (m) { bar.innerHTML = `<span class="ms">edit</span><div><b>Bearbeiten</b>${esc(m.text.slice(0, 100))}</div><button class="icon-btn" data-a="cancel" aria-label="Abbrechen"><span class="ms">close</span></button>`; ta.focus(); }
}
async function saveEdit() {
  const m = S.edit, ta = $("#txt"), text = ta.value.trim(), id = S.id;
  if (!m || !text || S.sending) return;
  if (text === m.text) return setEdit(null);
  const old = m.text, was = m.edited;
  S.sending = true;
  m.text = text; m.edited = true; setEdit(null); drawMsgs();
  try {
    await api(`/chats/${encodeURIComponent(cid(m))}/messages/${encodeURIComponent(mid(m))}`, { method: "PUT", body: JSON.stringify({ text }) });
    cacheMsgs();
  } catch (e) {
    m.text = old; m.edited = was;
    if (id === S.id) { drawMsgs(); setEdit(m); $("#txt").value = text; $("#txt").dispatchEvent(new Event("input")); }
    toast(`Bearbeiten ging nicht: ${e.message}`);
  }
  S.sending = false;
}
// Weiterleiten: Chat aussuchen, dann schickt der Server Text und Anhänge dorthin (Beeper hat kein echtes Weiterleiten – es kommt als neue Nachricht an)
function forwardPick(m) {
  chatPick({ title: "Weiterleiten an", verb: "An", tail: " senden" }, async (to) => {
    toast(`Wird an ${clean(to.title)} weitergeleitet …`);
    try {
      await api(`/chats/${encodeURIComponent(to.merged ? to.merged[0].id : to.id)}/forward`, { method: "POST", body: JSON.stringify({ text: m.text || "",
        files: m.files.filter((f) => f.src).map((f) => ({ src: f.src, mime: f.mime || "", name: f.name || "", kind: f.kind, duration: f.duration || 0, sticker: !!f.sticker, gif: !!f.gif })) }) });
      toast(`Weitergeleitet an ${clean(to.title)}`, () => go(to.id));
      loadChats({ quiet: true }); if (S.id === to.id) loadMsgs({ quiet: true });
    } catch (err) { toast(`Weiterleiten ging nicht: ${err.message}`); }
  });
}
// Ein Chat als Zeile in Auswahl-Fenstern: Profilbild mit Netzwerk-Abzeichen, Name, darunter Netzwerk(e); rechts ein runder Haken
const pickAv = (c) => netAv({ title: c.title, id: c.av || c.id, network: c.merged ? c.merged[0].network : c.network });
const pickSub = (c) => c.merged ? `Zusammengeführt · ${c.merged.map((x) => netName(x.network)).join(", ")}` : netName(c.network);
const tick = (on) => `<span class="pk ${on ? "on" : ""}"><span class="ms">check</span></span>`;
function chatPick({ title, verb, tail = "", skip = new Set(), multi = false, from = null }, done) {
  closeMenu();
  const d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet fwd"><div class="fwd-top"><b>${esc(title)}</b><button class="icon-btn" data-x="close" aria-label="Abbrechen"><span class="ms">close</span></button></div>
    ${multi ? '<div class="fwd-chips" hidden></div>' : ""}
    <label class="search"><span class="ms">search</span><input type="search" placeholder="Chat suchen" autocomplete="off"></label><div class="fwd-list"></div>
    <button class="btn fwd-go" disabled>${esc(verb)}</button></div>`;
  const list = d.querySelector(".fwd-list"), inp = d.querySelector("input"), goBtn = d.querySelector(".fwd-go");
  let items = [], pick = null, t, seq = 0;
  const picks = new Map();   // Mehrfachauswahl (Zusammenführen): bleibt auch beim Suchen erhalten
  const on = (c) => (multi ? picks.has(c.id) : pick?.id === c.id);
  const keep = (l) => collapse(l).filter((c) => !skip.has(c.id) && !(c.merged || []).some((x) => skip.has(x.id)));
  const base = () => { const ids = new Set(PINS.map((p) => p.id)); return keep([...PINS, ...S.chats.filter((c) => !ids.has(c.id))]); };
  const draw = () => {
    list.innerHTML = items.length ? items.map((c) => `<button class="fwd-row ${on(c) ? "on" : ""}" data-id="${esc(c.id)}">${pickAv(c)}
      <span class="fwd-t">${esc(clean(c.title))}<small>${esc(pickSub(c))}</small></span>${tick(on(c))}</button>`).join("")
      : '<div class="empty">Nichts gefunden.</div>';
    if (multi) {   // oben die Auswahl als Chips: der Ausgangschat fest, die gewählten zum Wieder-Abwählen
      const chips = d.querySelector(".fwd-chips");
      chips.innerHTML = (from ? `<span class="fchip base">${pickAv(from)}<span>${esc(clean(from.title))}</span></span>` : "")
        + [...picks.values()].map((c) => `<button class="fchip" data-un="${esc(c.id)}">${pickAv(c)}<span>${esc(clean(c.title))}</span><span class="ms">close</span></button>`).join("");
      chips.hidden = !picks.size && !from;
      goBtn.disabled = !picks.size; goBtn.textContent = picks.size ? `${verb} · ${picks.size + 1} Chats` : "Chats auswählen"; return;
    }
    goBtn.disabled = !pick; goBtn.textContent = pick ? `${verb} ${clean(pick.title)}${tail}` : verb;
  };
  items = base(); draw();
  inp.addEventListener("input", () => {
    clearTimeout(t);
    const q = inp.value.trim(), lq = q.toLowerCase(), n = ++seq;
    items = q ? base().filter((c) => clean(c.title).toLowerCase().includes(lq)) : base(); draw();
    if (q) t = setTimeout(async () => {   // auch Chats finden, die gerade nicht in der Liste stehen
      try { const r = await api(`/chats?${new URLSearchParams({ q, inbox: "" })}`); if (n === seq && $("#ctx") === d) { items = keep(r.items); draw(); } } catch { /* dann bleibt der lokale Treffer */ }
    }, 350);
  });
  d.addEventListener("click", async (e) => {
    if (e.target.closest("[data-x=close]") || e.target.classList.contains("ctx-back")) return closeMenu();
    const un = e.target.closest("[data-un]");
    if (un) { picks.delete(un.dataset.un); return draw(); }
    const r = e.target.closest(".fwd-row");
    if (r) {
      const c = items.find((x) => x.id === r.dataset.id) || null;
      if (multi && c) { if (picks.has(c.id)) picks.delete(c.id); else picks.set(c.id, c); } else pick = c;
      return draw();
    }
    if (!e.target.closest(".fwd-go") || (multi ? !picks.size : !pick)) return;
    const to = multi ? [...picks.values()] : pick;
    closeMenu(); done(to);
  });
  document.body.append(d);
  if (matchMedia("(pointer: fine)").matches) inp.focus();
}
function setReply(m) {
  if (m && S.edit) setEdit(null);
  S.reply = m || null;
  if (m?.chat && cur()?.merged?.some((x) => x.id === m.chat) && S.to !== m.chat) { S.to = m.chat; toChip(); }
  const bar = $("#replybar"); if (!bar) return;
  bar.hidden = !m;
  if (m) { bar.innerHTML = `<span class="ms">reply</span><div><b>${esc(m.mine ? "Du" : clean(m.sender))}</b>${esc((m.text || (m.files[0] ? "Anhang" : "")).slice(0, 100))}</div><button class="icon-btn" data-a="cancel" aria-label="Abbrechen"><span class="ms">close</span></button>`; $("#txt").focus(); }
}
// ---------------- Erwähnungen (@): beim Tippen Mitglieder vorschlagen, in Blasen hervorheben ----------------
// Gesendet wird "@Name" plus die Liste der Gewählten; der Dienst macht daraus die echte Erwähnung im Netzwerk.
const people = (id) => (ABOUT.get(id)?.d?.chats || []).flatMap((x) => x.people || []).filter((p) => !p.bot && p.name);
async function loadPeople(id) {
  const hit = ABOUT.get(id); if (hit && Date.now() - hit.t < 600000) return;
  try { ABOUT.set(id, { t: Date.now(), d: await api(`/chats/${encodeURIComponent(id)}/about`) }); } catch { /* ohne Vorschläge */ }
}
const myNames = () => people(S.id).filter((p) => p.self).map((p) => clean(p.name)).filter(Boolean);
function atMe(m) {
  if (m.at_me) return true;
  const t = (m.text || "").toLowerCase();
  return t.includes("@") && (t.includes("@room") || t.includes("@alle") || t.includes("@everyone") || myNames().some((n) => t.includes(`@${n.toLowerCase()}`)));
}
function mentionHtml(h) {   // h ist schon escaped; bekannte Namen zuerst (auch mit Leerzeichen), sonst ein Wort nach dem @
  if (!h.includes("@")) return h;
  const names = [...new Set(people(S.id).map((p) => esc(clean(p.name))))].filter(Boolean).sort((a, b) => b.length - a.length);
  const alt = names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const re = new RegExp(`(^|[\\s(>])@(${alt ? `${alt}|` : ""}[\\p{L}\\p{N}_.-]+)`, "gu");
  const mine = new Set(myNames().map((n) => esc(n).toLowerCase()));
  return h.replace(re, (all, pre, n) => `${pre}<span class="ment${mine.has(n.toLowerCase()) || /^(room|alle|everyone)$/i.test(n) ? " me" : ""}">@${n}</span>`);
}
function mentionPick() {
  const ta = $("#txt"), box = $("#mentbox"), c = cur();
  const hide = () => { if (box) box.hidden = true; };
  if (!ta || !c || c.type !== "group") return hide();
  const pre = ta.value.slice(0, ta.selectionStart), m = pre.match(/(?:^|\s)@([\p{L}\p{N}_.-]{0,30})$/u);
  if (!m) return hide();
  const id = target(); if (!ABOUT.get(id)) { loadPeople(id).then(() => { if ($("#txt") === ta) mentionPick(); }); return hide(); }
  const q = m[1].toLowerCase();
  const list = people(id).filter((p) => !p.self && clean(p.name).toLowerCase().split(/\s+/).some((w) => w.startsWith(q)) || (!p.self && !q)).slice(0, 6);
  if (!list.length) return hide();
  const el = box || (() => { const d = document.createElement("div"); d.className = "mentbox float"; d.id = "mentbox"; $("#dock").prepend(d);
    d.addEventListener("pointerdown", (e) => e.preventDefault());   // Tastatur bleibt offen
    d.addEventListener("click", (e) => { const b = e.target.closest("[data-pid]"); if (b) mentionTake(b.dataset.pid, b.dataset.n); }); return d; })();
  el.innerHTML = list.map((p) => `<button type="button" data-pid="${esc(p.id)}" data-n="${esc(clean(p.name))}">${avatar(clean(p.name), id, p.id)}<span><b>${esc(clean(p.name))}</b>${p.username ? `<small>@${esc(p.username)}</small>` : ""}</span></button>`).join("");
  el.hidden = false;
}
function mentionTake(pid, name) {
  const ta = $("#txt"), pos = ta.selectionStart, pre = ta.value.slice(0, pos).replace(/@[\p{L}\p{N}_.-]{0,30}$/u, `@${name} `);
  ta.value = pre + ta.value.slice(pos); ta.selectionStart = ta.selectionEnd = pre.length;
  S.ments = [...(S.ments || []).filter((x) => x.id !== pid), { id: pid, name }];
  ta.dispatchEvent(new Event("input")); ta.focus();
}

// ---------------- Tippfehler-Vorschlag (Dienst "korrektur"): 1 s nach dem letzten Tippen, über dem Eingabefeld ----------------
function hideFix() { clearTimeout(S.fixT); S.fix = null; S.fixSeq = (S.fixSeq || 0) + 1; think(false); const b = $("#fixbar"); if (b) b.hidden = true; }
function think(on) { $("#sendBtn")?.classList.toggle("think", on); }   // Ring um den Senden-Knopf, solange die Korrektur läuft
// Kontext für Haiku: die letzten Nachrichten mit Text (der Dienst kürzt auf die eingestellte Zahl, Standard 5)
const fixCtx = () => (S.msgs || []).filter((m) => m.text && !m.deleted && !m.pending).slice(-20).map((m) => ({ who: m.mine ? "Ich" : clean(m.sender || "") || "Andere", text: m.text.slice(0, 1000) }));
function fixLater() {
  hideFix();
  const ta = $("#txt"), text = ta?.value || "";
  if (!text.trim() || S.edit || S.fixOff) return;
  const seq = S.fixSeq;
  S.fixT = setTimeout(async () => {
    think(true);
    let d; try { d = await api("/korrektur", { method: "POST", body: JSON.stringify({ text, context: fixCtx() }) }); } catch { if (seq === S.fixSeq) think(false); return; }
    if (seq !== S.fixSeq) return;
    think(false);
    const bar = $("#fixbar");
    if (seq !== S.fixSeq || !bar || $("#txt")?.value !== text || !d.changed) return;
    S.fix = d;
    bar.innerHTML = `<span class="ms">spellcheck</span><div class="fixt" title="Antippen zum Übernehmen">${d.parts.map((p) => p.neu ? `<mark>${esc(p.t)}</mark>` : esc(p.t)).join("")}</div>
      <button class="send" type="button" data-fix="send" aria-label="Korrigiert senden"><span class="ms">send</span></button>`;
    bar.hidden = false;
  }, 1000);
}
async function send(override) {
  const ta = $("#txt"), text = (typeof override === "string" ? override : ta.value).trim();
  hideFix();
  if (S.edit) return saveEdit();
  if (!text || S.sending) return;
  S.sending = true; $("#sendBtn").disabled = true;
  const tmp = { id: `tmp${Date.now()}`, text, reply_to: S.reply?.id || null, reactions: [], mine: true, pending: true, at: new Date().toISOString(), files: [], type: "TEXT", sender: "" };
  S.msgs.push(tmp); drawMsgs({ stick: true });
  ta.value = ""; ta.style.height = "auto"; sendMode(); if ($("#mentbox")) $("#mentbox").hidden = true;
  const rep = S.reply; setReply(null);
  try {
    await api(`/chats/${encodeURIComponent(rep?.chat || target())}/send`, { method: "POST", body: JSON.stringify({ text, reply_to: rep ? mid(rep) : null, mentions: (S.ments || []).filter((x) => text.includes(`@${x.name}`)) }) });
    S.ments = [];
    setTimeout(() => loadMsgs({ quiet: true }), 600);
    const c = cur(); if (c) { c.at = new Date().toISOString(); c.unread = 0; markSeen(c); refreshReadBar(); drawList(); }
    loadChats({ quiet: true });
  } catch (e) {
    S.msgs = S.msgs.filter((m) => m !== tmp); drawMsgs(); ta.value = text;
    setReply(rep); toast(`Nicht gesendet: ${e.message}`);
  }
  S.sending = false; $("#sendBtn").disabled = false; ta.focus();
}
function sendMode() {   // leeres Feld: Mikrofon (wie in Beeper), sonst Senden
  const b = $("#sendBtn"), ta = $("#txt"); if (!b || !ta) return;
  const txt = !!ta.value.trim();
  b.classList.toggle("mic", !txt); b.querySelector(".ms").textContent = txt ? "send" : "mic";
  b.setAttribute("aria-label", txt ? "Senden" : "Sprachnachricht aufnehmen");
}

// ---------------- Anhänge senden ----------------
function attachMenu() {
  closeMenu();
  const d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet">
    <button data-p="media"><span class="ms" style="color:var(--ev-violet, #c6a0ff)">photo_library</span>Fotos & Videos</button>
    <button data-p="camera"><span class="ms" style="color:var(--ev-pink, #ff9ecf)">photo_camera</span>Kamera</button>
    <button data-p="file"><span class="ms" style="color:var(--ev-blue, #8ab4ff)">description</span>Datei</button></div>`;
  d.addEventListener("click", (e) => {
    const p = e.target.closest("[data-p]")?.dataset.p;
    closeMenu();
    if (!p) return;
    const inp = document.createElement("input");
    inp.type = "file"; inp.multiple = p !== "camera";
    if (p === "media") inp.accept = "image/*,video/*";
    if (p === "camera") { inp.accept = "image/*"; inp.capture = "environment"; }
    inp.onchange = () => pickFiles([...inp.files]);
    inp.click();
  });
  document.body.append(d);
}
const MAX_UP = 95 * 1024 * 1024;
function pickFiles(files) {
  files = files.filter((f) => f.size > 0);
  const big = files.filter((f) => f.size > MAX_UP);
  if (big.length) toast(`${big.length > 1 ? "Dateien" : big[0].name} zu groß (höchstens 95 MB)`);
  files = files.filter((f) => f.size <= MAX_UP).slice(0, 10);
  if (!files.length) return;
  closeMenu();
  const urls = files.map((f) => (/^(image|video)\//.test(f.type) ? URL.createObjectURL(f) : ""));
  const d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  const prev = files.map((f, i) => f.type.startsWith("image/") ? `<img src="${urls[i]}" alt="">`
    : f.type.startsWith("video/") ? `<video src="${urls[i]}" muted playsinline></video>`
    : `<div class="pfile"><span class="ms">description</span><b>${esc(f.name)}</b><small>${size(f.size)}</small></div>`).join("");
  d.innerHTML = `<div class="ctx-back"></div><div class="preview">
    <div class="ptop"><button class="icon-btn" data-x="close" aria-label="Abbrechen"><span class="ms">close</span></button><b>${files.length > 1 ? `${files.length} Dateien` : "Senden"}</b></div>
    <div class="pitems ${files.length > 1 ? "many" : ""}">${prev}</div>
    <form class="composer pcomp"><textarea rows="1" placeholder="Bildunterschrift" enterkeyhint="send"></textarea>
    <button class="send" type="submit" aria-label="Senden"><span class="ms">send</span></button></form></div>`;
  const done = () => { urls.forEach((u) => u && URL.revokeObjectURL(u)); closeMenu(); };
  d.addEventListener("click", (e) => { if (e.target.closest("[data-x=close]") || e.target.classList.contains("ctx-back")) done(); });
  const cap = d.querySelector("textarea");
  cap.value = $("#txt")?.value || "";
  cap.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); d.querySelector("form").requestSubmit(); } });
  d.querySelector("form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = cap.value.trim();
    done();
    if ($("#txt")) { $("#txt").value = ""; sendMode(); }
    files.reduce((p, f, i) => p.then(() => sendFile(f, { text: i === 0 ? text : "" })), Promise.resolve());
  });
  document.body.append(d);
  if (matchMedia("(pointer: fine)").matches) cap.focus();
}
const size = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
function kindOf(t) { return t.startsWith("image/") ? "image" : t.startsWith("video/") ? "video" : t.startsWith("audio/") ? "audio" : "file"; }
function sendFile(file, { text = "", voice = false, duration = 0 } = {}) {
  const id = S.id, rep = S.reply; setReply(null);
  const local = URL.createObjectURL(file);
  const tmp = { id: `tmp${Date.now()}${Math.random()}`, text, reply_to: rep?.id || null, reactions: [], mine: true, pending: true, pendingFile: true, at: new Date().toISOString(),
    type: voice ? "VOICE" : "MEDIA", sender: "", progress: 0, files: [{ local, kind: voice ? "audio" : kindOf(file.type), mime: file.type, name: file.name, size: file.size, duration }] };
  S.msgs.push(tmp); drawMsgs({ stick: true });
  const fd = new FormData();
  fd.append("file", file, file.name || (voice ? "Sprachnachricht.webm" : "datei"));
  if (text) fd.append("text", text);
  if (rep?.id) fd.append("reply_to", mid(rep));
  const to = rep?.chat || target();
  if (voice) { fd.append("voice", "true"); fd.append("duration", String(duration)); }
  return new Promise((ok) => {   // XHR statt fetch: so gibt es einen Fortschrittsbalken
    const x = new XMLHttpRequest();
    x.open("POST", `${API}/chats/${encodeURIComponent(to)}/upload`);
    x.upload.onprogress = (e) => { if (e.lengthComputable) { tmp.progress = e.loaded / e.total; const bar = document.querySelector(`#m-${CSS.escape(tmp.id)} .prog i`); if (bar) bar.style.width = `${Math.round(tmp.progress * 100)}%`; } };
    x.onload = () => {
      if (x.status >= 200 && x.status < 300) {
        tmp.sentAt = Date.now();
        setTimeout(() => loadMsgs({ quiet: true }), 1500); setTimeout(() => loadMsgs({ quiet: true }), 5000);
        loadChats({ quiet: true });
      } else {
        let msg = `Fehler ${x.status}`; try { msg = JSON.parse(x.responseText).detail || msg; } catch {}
        fail(msg);
      }
      ok();
    };
    x.onerror = () => { fail("Keine Verbindung"); ok(); };
    x.send(fd);
    function fail(msg) { S.msgs = S.msgs.filter((m) => m !== tmp); URL.revokeObjectURL(local); if (id === S.id) drawMsgs(); toast(`Nicht gesendet: ${msg}`); }
  });
}

// ---------------- Sprachnachricht aufnehmen ----------------
const R = { rec: null, chunks: [], start: 0, paused: 0, pauseAt: 0, t: 0, stream: null, ana: null, ctx: null, levels: [] };
function recMime() {
  for (const t of ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/mp4", "audio/webm"]) if (window.MediaRecorder?.isTypeSupported?.(t)) return t;
  return "";
}
async function startRec() {
  if (R.rec) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return toast("Aufnehmen geht in diesem Browser nicht");
  try {
    R.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch { return toast("Kein Zugriff aufs Mikrofon – in den Browser-Einstellungen erlauben"); }
  const mime = recMime();
  R.rec = new MediaRecorder(R.stream, mime ? { mimeType: mime, audioBitsPerSecond: 48000 } : undefined);
  R.chunks = []; R.levels = []; R.start = Date.now(); R.paused = 0; R.pauseAt = 0;
  R.rec.ondataavailable = (e) => e.data.size && R.chunks.push(e.data);
  R.rec.start(250);
  try {
    R.ctx = new (window.AudioContext || window.webkitAudioContext)();
    R.ana = R.ctx.createAnalyser(); R.ana.fftSize = 512;
    R.ctx.createMediaStreamSource(R.stream).connect(R.ana);
  } catch { R.ana = null; }
  $("#form").hidden = true; $("#recbar").hidden = false; $("#recwave").innerHTML = "";
  buzz(25);
  const buf = new Uint8Array(256);
  R.t = setInterval(() => {
    if (!R.rec) return;
    const el = recElapsed();
    $("#rectime").textContent = mmss(el);
    if (R.rec.state !== "recording") return;
    let lvl = 0.15;
    if (R.ana) { R.ana.getByteTimeDomainData(buf); let sum = 0; for (const v of buf) sum += (v - 128) ** 2; lvl = Math.min(1, Math.sqrt(sum / buf.length) / 40 + 0.08); }
    R.levels.push(lvl);
    const w = $("#recwave"), n = Math.max(10, Math.floor(w.clientWidth / 5));
    w.innerHTML = R.levels.slice(-n).map((l) => `<i style="height:${Math.round(l * 100)}%"></i>`).join("");
    if (el > 15 * 60) recAction("send");   // höchstens 15 Minuten
  }, 100);
}
const recElapsed = () => (((R.pauseAt || Date.now()) - R.start - R.paused) / 1000);
function stopRec() {
  clearInterval(R.t);
  R.stream?.getTracks().forEach((t) => t.stop());
  R.ctx?.close().catch(() => {});
  const f = $("#form"), b = $("#recbar"); if (f) f.hidden = false; if (b) b.hidden = true;
}
function recAction(a) {
  const r = R.rec; if (!r) return;
  if (a === "pause") {
    const btn = document.querySelector('[data-rec="pause"] .ms');
    if (r.state === "recording") { r.pause(); R.pauseAt = Date.now(); btn.textContent = "mic"; document.getElementById("recbar").classList.add("paused"); }
    else { r.resume(); R.paused += Date.now() - R.pauseAt; R.pauseAt = 0; btn.textContent = "pause"; document.getElementById("recbar").classList.remove("paused"); }
    return;
  }
  const dur = recElapsed();
  R.rec = null;
  r.onstop = () => {
    stopRec();
    if (a !== "send") return;
    if (dur < 0.7) return toast("Zu kurz – zum Aufnehmen antippen, dann sprechen");
    const type = (r.mimeType || "audio/webm").split(";")[0];
    const ext = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm";
    sendFile(new File(R.chunks, `Sprachnachricht.${ext}`, { type }), { voice: true, duration: Math.round(dur * 10) / 10 });
  };
  r.stop();
  if (a === "cancel") buzz(10);
}

// ---------------- Live (nur sichtbar) ----------------
let es = null, rT;
function live(on) {
  if (!on) { es?.close(); es = null; return; }
  if (es) return;
  es = new EventSource(`${API}/stream`);
  es.onmessage = (e) => {
    let d = {};
    try { d = JSON.parse(e.data); } catch { return; }
    const gid = d.id && (grpOf(d.id)?.id || d.id);
    if (d.type === "chat" && d.new && gid !== S.id) toast(`Neue Nachricht von ${clean(d.title)}`, () => go(gid));
    clearTimeout(rT);
    rT = setTimeout(() => { loadChats({ quiet: true }); loadPins(); if (S.id && (!d.id || gid === S.id)) loadMsgs({ quiet: true }).then(autoRead); }, 250);
  };
}
// Gelesen-Häkchen und Reaktionen im offenen Chat: ändern den Chat in der Liste nicht, darum fragt die Seite den offenen Chat alle paar Sekunden
// Im Hintergrund (App zu, Bildschirm aus) läuft nichts: kein Timer, keine offene Verbindung – das spart Akku.
let pollT = null;
function poll(on) {
  clearInterval(pollT); pollT = null;
  if (on) pollT = setInterval(() => { if (S.id && !S.sending) loadMsgs({ quiet: true }); }, 5000);
}
poll(!document.hidden);
document.addEventListener("visibilitychange", () => {
  poll(!document.hidden);
  if (document.hidden) return live(false);
  loadChats({ quiet: true }); if (S.id) { loadMsgs({ quiet: true }); clearNotes(S.id); }
  loadMutes(); live(true);
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});

// ---------------- Benachrichtigungen aufs Handy (Web Push) ----------------
// Die Glocke oben meldet dieses Gerät an. Danach weckt der Server das Handy nur, wenn eine Nachricht kommt – die App selbst
// läuft dafür nicht (kein Timer, keine offene Verbindung), das kostet praktisch keinen Akku.
const canPush = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const b64key = (k) => { const s = atob((k + "=".repeat((4 - k.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from(s, (c) => c.charCodeAt(0)); };
async function pushSub() { try { return await (await navigator.serviceWorker.ready).pushManager.getSubscription(); } catch { return null; } }
async function drawBell() {
  const b = $("#bell"); if (!b || !canPush) return;
  const on = Notification.permission === "granted" && !!(await pushSub());
  b.hidden = false; b.classList.toggle("on", on);
  b.firstElementChild.textContent = on ? "notifications_active" : "notifications_off";
  b.title = on ? "Benachrichtigungen an" : "Benachrichtigungen einschalten";
  if (on) { const s = await pushSub(); api("/push/subscribe", { method: "POST", body: JSON.stringify(s.toJSON()) }).catch(() => {}); }   // Abo frisch halten
}
async function pushOn() {
  if (Notification.permission === "denied") return toast("Benachrichtigungen sind blockiert – in den Browser-Einstellungen für diese Seite erlauben");
  if (await Notification.requestPermission() !== "granted") return toast("Ohne Erlaubnis keine Benachrichtigungen");
  try {
    const { key } = await api("/push");
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription() || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64key(key) });
    await api("/push/subscribe", { method: "POST", body: JSON.stringify(sub.toJSON()) });
    toast("Benachrichtigungen an");
  } catch (e) { toast(`Anmelden ging nicht: ${e.message}`); }
  drawBell();
}
async function pushOff() {
  const sub = await pushSub();
  if (sub) { await api("/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint: sub.endpoint }) }).catch(() => {}); await sub.unsubscribe().catch(() => {}); }
  toast("Benachrichtigungen aus"); drawBell();
}
$("#bell")?.addEventListener("click", async () => {
  if (!$("#bell").classList.contains("on")) return pushOn();
  closeMenu();
  const d = document.createElement("div"); d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet"><button data-b="info"><span class="ms">notifications_active</span>Benachrichtigungen sind an<small>Stumm pro Chat: Chat-Kopf antippen</small></button>
    <button data-b="off"><span class="ms">notifications_off</span>Auf diesem Gerät ausschalten</button></div>`;
  d.addEventListener("click", (e) => { const b = e.target.closest("[data-b]")?.dataset.b; closeMenu(); if (b === "off") pushOff(); });
  document.body.append(d);
});
drawBell();
// Tipp auf eine Meldung bei offener App: den Chat öffnen
navigator.serviceWorker?.addEventListener("message", (e) => {
  if (e.data?.type !== "open") return;
  const m = /^\/chat\/([^/]+)/.exec(e.data.url || ""); go(m ? decodeURIComponent(m[1]) : null);
});
// Chat geöffnet: seine Meldungen in der Leiste wegräumen
async function clearNotes(id) {
  try { for (const n of await (await navigator.serviceWorker.ready).getNotifications({ tag: `chat-${id}` })) n.close(); } catch { /* egal */ }
}

// ---------------- Stummschalten (nur Benachrichtigungen; Beeper bleibt unberührt) ----------------
function isMuted(id) { const u = MUTES.get(id); return u !== undefined && (u === 0 || u > Date.now()); }
function muteText(id) {
  const u = MUTES.get(id); if (!u) return "Bis du es aufhebst";
  const d = new Date(u); return `Bis ${d.toDateString() === new Date().toDateString() ? "" : `${fmtShort.format(d)}, `}${fmtTime.format(d)} Uhr`;
}
async function loadMutes() {
  try { const r = await api("/mutes"); MUTES = new Map(r.items.map((x) => [x.id, x.until * 1000])); drawList(); } catch { /* bleibt */ }
}
function redrawInfo() { const el = $("#info"), c = cur(); if (el && c) el.innerHTML = infoHtml(c, ABOUT.get(c.id)?.d); }
async function setMute(c, hours) {
  const was = MUTES.get(c.id);
  if (hours === null) MUTES.delete(c.id); else MUTES.set(c.id, hours ? Date.now() + hours * 36e5 : 0);
  drawList(); redrawInfo();
  try {
    if (hours === null) await api(`/chats/${encodeURIComponent(c.id)}/mute`, { method: "DELETE" });
    else { const r = await api(`/chats/${encodeURIComponent(c.id)}/mute`, { method: "PUT", body: JSON.stringify({ hours }) }); MUTES.set(c.id, r.until * 1000); }
    toast(hours === null ? "Stummschaltung aufgehoben" : `Stumm ${hours === 1 ? "für 1 Stunde" : hours ? `für ${hours} Stunden` : "bis du es aufhebst"}`);
  } catch (e) { if (was === undefined) MUTES.delete(c.id); else MUTES.set(c.id, was); toast(`Ging nicht: ${e.message}`); }
  drawList(); redrawInfo();
}
function muteMenu(c) {
  closeMenu();
  const on = isMuted(c.id), d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet">
    ${on ? `<button data-m="off"><span class="ms">notifications</span>Stummschaltung aufheben<small>${esc(muteText(c.id))}</small></button>` : ""}
    <button data-m="1"><span class="ms">schedule</span>1 Stunde</button>
    <button data-m="8"><span class="ms">bedtime</span>8 Stunden</button>
    <button data-m="0"><span class="ms">notifications_off</span>Bis ich es aufhebe</button></div>`;
  d.addEventListener("click", (e) => {
    const m = e.target.closest("[data-m]")?.dataset.m; closeMenu();
    if (m === "off") setMute(c, null); else if (m !== undefined) setMute(c, +m);
  });
  document.body.append(d);
}
// ---------------- Automatisch als gelesen (pro Chat; "Sofort" erledigt der Dienst, auch ohne offene App) ----------------
const AR_TEXT = { "": "Aus – du markierst selbst", open: "Beim Öffnen des Chats", always: "Sofort, auch ohne Öffnen (keine Benachrichtigung)" };
const arOf = (id) => AUTOREAD.get(id) || AUTOREAD.get(grpOf(id)?.id) || "";
function autoRead() { const c = cur(); if (c && arOf(c.id) && left(c) && document.visibilityState === "visible") markRead(c); }
async function loadAutoRead() {
  try { const r = await api("/autoread"); AUTOREAD = new Map(r.items.map((x) => [x.id, x.mode])); autoRead(); } catch { /* bleibt */ }
}
async function setAutoRead(c, mode) {
  const was = arOf(c.id);
  if (mode) AUTOREAD.set(c.id, mode); else AUTOREAD.delete(c.id);
  redrawInfo();
  try {
    await api(`/chats/${encodeURIComponent(c.id)}/autoread`, { method: "PUT", body: JSON.stringify({ mode }) });
    toast(mode ? `Automatisch gelesen: ${mode === "open" ? "beim Öffnen" : "sofort"}` : "Automatisch gelesen aus");
    if (mode) autoRead();
  } catch (e) { if (was) AUTOREAD.set(c.id, was); else AUTOREAD.delete(c.id); toast(`Ging nicht: ${e.message}`); }
  redrawInfo();
}
function autoReadMenu(c) {
  closeMenu();
  const now = arOf(c.id), d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet">${[["", "block", "Aus"], ["open", "visibility", "Beim Öffnen"], ["always", "bolt", "Sofort"]].map(([k, ic, t]) =>
    `<button data-r="${k}"${k === now ? ' class="on"' : ""}><span class="ms">${k === now ? "check" : ic}</span>${t}<small>${esc(AR_TEXT[k])}</small></button>`).join("")}</div>`;
  d.addEventListener("click", (e) => {
    const r = e.target.closest("[data-r]")?.dataset.r; closeMenu();
    if (r !== undefined && r !== now) setAutoRead(c, r);
  });
  document.body.append(d);
}
loadAutoRead();
loadMutes();
route();
loadChats();
live(!document.hidden);

// ---------------- Tastatur am Handy ----------------
// Ohne das schiebt die Tastatur (vor allem am iPhone) die ganze Seite samt Kopfzeile nach oben. Die Seite wird stattdessen auf den sichtbaren Bereich gesetzt.
if (window.visualViewport && matchMedia("(pointer: coarse)").matches) {
  const vv = visualViewport;
  const fit = () => {
    const open = innerHeight - vv.height > 120 || vv.offsetTop > 0;
    document.body.style.setProperty("--vvh", `${vv.height}px`);
    document.body.style.setProperty("--vvt", `${vv.offsetTop}px`);
    const was = document.body.classList.contains("kb");
    document.body.classList.toggle("kb", open);
    if (open) { scrollTo(0, 0); if (!was && atBottom) { const b = $("#msgs"); if (b) requestAnimationFrame(() => { b.scrollTop = b.scrollHeight; }); } }
  };
  vv.addEventListener("resize", fit); vv.addEventListener("scroll", fit);
}
loadPins();
loadMerges();
