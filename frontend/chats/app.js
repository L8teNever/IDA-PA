// IDA Chats: deine Beeper-Chats lesen und schreiben. Live per Server-Sent Events – nur solange die Seite sichtbar ist (Akku).
import { esc, autolink } from "./shared/js/dom.js";

const API = "/api/v1";
const $ = (s) => document.querySelector(s);
const BASE = "/";
const COLORS = ["rot", "orange", "gelb", "salbei", "gruen", "tuerkis", "blau", "lavendel", "lila", "koralle"];
let AUTOREAD = new Map();   // automatisch gelesen: ID → "open" (beim Öffnen) oder "always" (sofort, ohne Öffnen)
let PREFS = { snaps_quiet: false, snap_join: true };   // Einstellungen für alle Geräte (im Dienst gespeichert)
let MUTES = new Map();   // stummgeschaltete Chats: ID → bis (ms, 0 = bis du es aufhebst)
const S = { chats: [], next: null, q: "", f: "all", id: null, only: "", msgs: [], more: false, oldest: null, seq: 0, sending: false, reply: null, edit: null, voice: {}, vopen: {} };

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
let avV = 2;
const avSrc = (id, user) => `${API}/avatar/${encodeURIComponent(id)}${user ? `?user=${encodeURIComponent(user)}` : `?v=${avV}`}`;   // v=2: Gruppen ohne Bild haben jetzt eine Collage
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
// Neue Ansicht in den Verlauf. Liegt oben noch der Eintrag eines gerade geschlossenen Menüs, wird der ersetzt statt einen draufzulegen.
function pushView(state, title, path) {
  if (history.state?.ctx) { const { ctx, ...rest } = state || {}; history.replaceState(rest, title, path); }
  else history.pushState(state, title, path);
}
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
    if (id && fromList && push) pushView({ fromList: true }, "", urlFor(id));
    else history.replaceState(id ? history.state : null, "", urlFor(id));
  }
  route();
}
function back() {
  if (history.state?.fromList) history.back();   // davor liegt die Übersicht
  else go(null, false);
}
window.addEventListener("popstate", () => {
  if ($("#ctx") && !readMenu()) closeMenu();                 // Zurück-Taste schließt das offene Menü
  else if (!$("#ctx") && readMenu()) history.replaceState(history.state, "", menuBase());   // Menü-Adresse ohne Menü (Vorwärts)
  if (OV && !readOv()) { const c = OV; OV = null; c(); }   // Zurück-Taste schließt Bild/Video
  if ($("#srch") && !readSrch()) dropSrch();                // … die Suche
  if ($("#info") && !readInfo() && !readSrch()) dropInfo();   // … und die Info-Seite
  if (!$("#tools").hidden !== readLsrch()) showLsrch(readLsrch());
  if (!!$("#sett") !== readSett()) { if (readSett()) openSett(false); else dropSett(); }   // … und die Einstellungen   // … und die Chat-Suche in der Übersicht
  route();
  if (!OV) openFromUrl();
});
// Bild- und Video-Vollbild haben eine eigene Adresse (…/foto/<Nachricht>[/<Nr>], …/video/<Nachricht>, …/bild):
// Zurück schließt sie, ein Neuladen oder ein geteilter Link öffnet sie wieder.
let OV = null;   // Schließen-Funktion des offenen Vollbilds (ohne Verlauf anzufassen)
const ovBase = () => urlFor(readUrl());
const readOv = () => /^chat\/[^/]+\/(foto|video|bild|album)(?:\/|$)/.test(location.pathname.slice(BASE.length)) || /^bild$/.test(location.pathname.slice(BASE.length));
function ovPath(sub) { const b = ovBase(); return `${b}${b.endsWith("/") ? "" : "/"}${sub}`; }
function ovOpen(sub, close) {
  const path = ovPath(sub), was = OV;
  OV = close;
  if (was) return history.replaceState(history.state, "", path);   // Vollbild ersetzt Vollbild
  if (location.pathname === path) history.replaceState(history.state, "", ovBase());   // per Link geöffnet: davor liegt der Chat
  pushView({ ...(history.state || {}), ov: true }, "", path);
}
function ovSet(sub) { if (OV) history.replaceState(history.state, "", ovPath(sub)); }
function ovClosed() {   // über den eigenen Knopf, Wischen oder Esc geschlossen
  if (!OV) return;
  OV = null;
  if (history.state?.ov) history.back(); else history.replaceState(history.state, "", ovBase());
}
function openFromUrl() {
  const m = /^chat\/[^/]+\/(foto|video|bild|album|info|suche)(?:\/([^/]+))?(?:\/(\d+))?/.exec(location.pathname.slice(BASE.length));
  if (!m && readOv()) return history.replaceState(history.state, "", ovBase());
  if (!m || !S.id) return;
  if (m[1] === "info") return $("#info") ? undefined : openInfo();
  if (m[1] === "suche") return $("#srch") ? undefined : openSearch();
  const msg = m[2] && S.msgs.find((x) => x.id === decodeURIComponent(m[2]));
  if (m[1] === "album" && msg && albumView(msg.id)) return;
  if (m[1] === "foto" && msg) {
    const f = msg.files.filter((x) => x.kind === "image" && !x.sticker && x.src)[(+m[3] || 1) - 1];
    if (f) return gallery(f.src);
  }
  if (m[1] === "video" && msg) { const el = document.getElementById(`m-${msg.id}`)?.querySelector(".vid, [data-a=igvideo], [data-a=embed]"); if (el) return el.dataset.a === "embed" ? playEmbed(el) : playVideo(el); }
  history.replaceState(history.state, "", ovBase());   // nicht (mehr) im geladenen Verlauf: einfach den Chat zeigen
}
// Übergang Übersicht ⇄ Chat (nur am Handy, wo immer nur eins von beiden zu sehen ist): Das Profilbild wandert aus der Zeile in den
// Kopf, die Leiste unten wird zur Eingabe (links → Plus, Mitte → Textfeld, rechts → Senden) – und zurück. Die Namen dafür stehen in app.css.
const VT_OK = !!document.startViewTransition, VT_PHONE = matchMedia("(max-width: 860px)"), VT_CALM = matchMedia("(prefers-reduced-motion: reduce)");
let vtReady = false;   // erst nach dem ersten Aufbau: beim Laden der Seite gibt es keinen Übergang
function route() {
  const to = readUrl(), from = S.id, plain = /^(chat\/[^/]+)?$/.test(location.pathname.slice(BASE.length));
  if (!vtReady || !VT_OK || !VT_PHONE.matches || VT_CALM.matches || !plain || !!to === document.body.classList.contains("reading") || $("#ctx") || $("#sett") || $("#info")) { vtReady = true; document.documentElement.classList.remove("vt"); return routeNow(); }
  const mark = (id) => {   // genau ein Profilbild in der Liste trägt den Namen: das des Chats, der auf- oder zugeht
    document.querySelectorAll("#list .vt-av").forEach((x) => x.classList.remove("vt-av"));
    if (id) document.querySelector(`#list [data-id="${CSS.escape(id)}"] .av`)?.classList.add("vt-av");
  };
  mark(to || from);
  document.documentElement.classList.add("vt");
  const t = document.startViewTransition(() => { routeNow(); mark(to || from); });
  // "vt" bleibt bis zum nächsten Wechsel ohne Übergang stehen: Nähme man es hier weg, liefe das Reinschieben (pagein/pageback) nach dem
  // Übergang noch einmal los – die Seite wackelt dann kurz.
  t.finished.finally(() => mark(null));
}
function routeNow() {
  let id = readUrl();
  const g = id && grpOf(id); if (g && g.id !== id) { history.replaceState(history.state, "", urlFor(g.id)); id = g.id; }
  document.body.classList.toggle("reading", !!id);
  if (id === S.id) return drawList();
  const c = id && MC.get(id);
  TS.open = false; TS.sx = 0;
  S.id = id; S.msgs = c ? c.msgs.slice() : []; S.more = c ? c.more : false; S.oldest = c ? c.oldest : null; S.reply = null; S.edit = null; S.to = null;
  S.only = id && filterKeep() ? FILT[id] || "" : "";   // Filter: gemerkt oder – beim Verlassen des Chats – zurückgesetzt
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
// keep: stilles Auffrischen (live) lässt schon nachgeladene ältere Chats stehen, statt die Liste wieder auf die erste Seite zu kürzen
let moreBusy = false;
async function loadChats({ more = false, quiet = false, keep = quiet } = {}) {
  if (more) { if (moreBusy || !S.next) return; moreBusy = true; }
  const seq = ++listSeq;
  if (!more && !quiet && !S.chats.length) $("#list").innerHTML = '<div class="skel"></div>'.repeat(7);
  try {
    const p = new URLSearchParams({ q: S.q, unread: S.f === "unread" && !S.q, inbox: S.f === "archive" ? "archive" : "primary" });
    if (more && S.next) p.set("cursor", S.next);
    const d = await api(`/chats?${p}`);
    if (seq !== listSeq) return;
    if (S.q && !more) {   // Suche: Beepers Treffer zuerst, dahinter, was nur das Gerät kennt (z. B. ältere Chats)
      const ids = new Set(d.items.map((c) => c.id));
      d.items = d.items.concat(localFind(S.q).filter((c) => !ids.has(c.id)));
      SQ.set(`${S.f}|${S.q}`, { items: d.items }); if (SQ.size > 100) SQ.delete(SQ.keys().next().value);
    }
    S.busyQ = false;
    const had = new Set(S.chats.map((c) => c.id)), got = new Set(d.items.map((c) => c.id));
    let tidy = false; for (const c of d.items) if (!c.unread && c.id in seen) { delete seen[c.id]; tidy = true; }   // Beeper sieht es jetzt auch so
    if (tidy) saveSeen();
    dirAdd(d.items);
    if (more) { S.chats = S.chats.concat(d.items.filter((c) => !had.has(c.id))); S.next = d.next; S.paged = true; }
    else if (keep && S.paged) S.chats = d.items.concat(S.chats.filter((c) => !got.has(c.id)));   // ältere Seiten bleiben, S.next auch
    else { S.chats = d.items; S.next = d.next; S.paged = false; }
    drawList(); cacheList();
    if (!more && !S.q) { loadIda(); loadUntis(); }
    if (more) {   // die neu dazugekommenen Zeilen tauchen nacheinander auf
      const els = [...document.querySelectorAll("#list .row")].filter((el) => !had.has(el.dataset.id) && !had.has(grpOf(el.dataset.id)?.id)).slice(0, 14);
      els.forEach((el, i) => { el.style.animationDelay = `${i * 28}ms`; el.classList.add("rin"); });
    }
  } catch (e) {
    if (seq === listSeq) { S.busyQ = false; if (S.q) drawList(); }
    if (seq === listSeq && !quiet) $("#list").innerHTML = errBox(e);
  } finally { if (more) { moreBusy = false; if (seq !== listSeq && $("#more")) { moreObs.disconnect(); moreObs.observe($("#more")); } } }   // überholt: noch einmal prüfen
}
// Gelesen-Markierung: Öffnen allein ändert in Beeper nichts. "Als gelesen markieren" markiert den Chat in Beeper als gelesen (und beim Absender, wenn das Netzwerk das kann).
// Der lokale Merker hält den Chat gelesen, bis Beeper selbst 0 meldet – Beeper braucht dafür ein paar Sekunden (Snapchat länger).
// Gemerkt wird die Zeit der letzten Nachricht beim Markieren: Kommt danach etwas Neues, gilt der Chat wieder als ungelesen.
// (Früher wurde der Merker nach der Antwort des Dienstes gelöscht – lieferte Beeper kurz danach noch die alte Zahl, sprang der Chat zurück auf ungelesen.)
const SEEN_KEY = "chats-seen";
const seen = (() => { try { const o = JSON.parse(localStorage.getItem(SEEN_KEY) || "{}"); for (const k in o) o[k] = String(o[k]).replace(/^\d+\|/, ""); return o; } catch { return {}; } })();
const saveSeen = () => { try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch {} };
const left = (c) => (c && c.unread && !(c.id in seen && (c.at || "") <= seen[c.id]) ? c.unread : 0);   // ungelesen, bis markiert (oder Neues kommt)
function markSeen(c) { if (c) seen[c.id] = c.at || ""; }
function unSeen(c) { for (const x of [c, ...(c.merged || [])]) delete seen[x.id]; saveSeen(); }
async function markRead(c) {
  if (!c || c.marking) return;
  const id = c.id, mem = (c.merged || []).map((x) => known(x.id) || x);
  mem.forEach(markSeen); markSeen(c); saveSeen(); refreshReadBar(); drawList();
  c.marking = true;
  try {
    await api(`/chats/${encodeURIComponent(id)}/read`, { method: "POST", body: "{}" });
    if (isUntis(id)) loadUntis(); if (isIda(id)) loadIda();
  } catch (e) {
    unSeen(c); refreshReadBar(); drawList(); toast(`Als gelesen markieren ging nicht: ${e.message}`);
  } finally { c.marking = false; }
}
async function markUnread(c, msgId) {   // in Beeper wieder ungelesen – auf Wunsch ab einer bestimmten Nachricht
  if (!c) return;
  const before = c.unread;
  unSeen(c);
  c.unread = Math.max(1, c.unread || 0); refreshReadBar(); drawList();
  try {
    const r = await api(`/chats/${encodeURIComponent(c.id)}/unread`, { method: "POST", body: JSON.stringify(msgId ? { message_id: msgId } : {}) });
    if (r.unread) c.unread = r.unread;
    refreshReadBar(); drawList(); toast("Als ungelesen markiert");
  } catch (e) {
    c.unread = before; markSeen(c); saveSeen(); refreshReadBar(); drawList(); toast(`Als ungelesen markieren ging nicht: ${e.message}`);
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
// Vorschläge zum Zusammenführen (Einstellungen): dieselbe Person in mehreren Apps – der Dienst sucht, du entscheidest (org.py)
let SUGG = null;
async function loadSugg() { try { SUGG = (await api("/merges/suggestions")).items; redrawSett(); } catch { /* ohne Vorschläge */ } }
function suggSheet(sg, picked = null, nameVal = "") {
  closeMenu();
  const all = [...sg.chats], on = picked || new Set(all.map((x) => x.id));
  const d = document.createElement("div"); d.className = "ctx"; d.id = "ctx";
  const draw = () => {
    const n = all.filter((x) => on.has(x.id)).length;
    d.innerHTML = `<div class="ctx-back"></div><div class="sheet fwd gset"><div class="fwd-top"><b>Zusammenführen?</b><button class="icon-btn" data-x="close" aria-label="Schließen"><span class="ms">close</span></button></div>
    <div class="fwd-list">
      <div class="gset-h">${esc(sg.reason || "Vorschlag")} – stimmt das? Abwählen, was nicht dazugehört.</div>
      ${all.map((x) => `<button class="fwd-row ${on.has(x.id) ? "on" : ""}" data-m="${esc(x.id)}">${netAv(x)}<span class="fwd-t">${esc(clean(x.title))}<small>${esc(netName(x.network))}</small></span>${tick(on.has(x.id))}</button>`).join("")}
      <button class="fwd-row" data-add="1"><span class="av"><span class="ms">add</span></span><span class="fwd-t">Weitere Chats dazunehmen</span></button>
      <div class="gset-h">Name (frei lassen = ${esc(clean(all.find((x) => on.has(x.id))?.title || all[0].title))})</div>
      <label class="search"><span class="ms">edit</span><input id="sgname" maxlength="100" value="${esc(nameVal)}" autocomplete="off"></label>
    </div>
    <div class="sugg-btns"><button class="btn ghost" data-no="1">Nicht vorschlagen</button><button class="btn fwd-go" ${n < 2 ? "disabled" : ""}>Zusammenführen${n > 2 ? ` · ${n}` : ""}</button></div></div>`;
  };
  draw();
  d.addEventListener("click", async (e) => {
    if (e.target.closest("[data-x=close]") || e.target.classList.contains("ctx-back")) return closeMenu();
    nameVal = d.querySelector("#sgname")?.value || "";
    const m = e.target.closest("[data-m]");
    if (m) { const id = m.dataset.m; if (on.has(id)) on.delete(id); else on.add(id); draw(); return; }
    if (e.target.closest("[data-add]")) {
      const first = all.find((x) => on.has(x.id)) || all[0];
      return chatPick({ title: "Dazunehmen …", verb: "Dazunehmen", multi: true, from: first, skip: new Set(all.map((x) => x.id)) }, (to) => {
        for (const c of to.flatMap((c) => c.merged || [c])) if (!all.some((x) => x.id === c.id)) { all.push({ id: c.id, title: c.title, network: c.network || "" }); on.add(c.id); }
        suggSheet({ ...sg, chats: all }, on, nameVal);
      });
    }
    if (e.target.closest("[data-no]")) {
      closeMenu();
      try { await api("/merges/suggestions/dismiss", { method: "POST", body: JSON.stringify({ chats: sg.chats.map((x) => x.id) }) }); } catch (err) { return toast(`Ging nicht: ${err.message}`); }
      SUGG = (SUGG || []).filter((x) => x.id !== sg.id); redrawSett(); return toast("Wird nicht mehr vorgeschlagen");
    }
    if (!e.target.closest(".fwd-go")) return;
    const pick = all.filter((x) => on.has(x.id)), name = nameVal.trim();
    if (pick.length < 2) return;
    closeMenu();
    try {
      const r = await api("/merges", { method: "POST", body: JSON.stringify({ chats: pick.map((x) => ({ id: x.id, title: clean(x.title || ""), network: x.network || "" })) }) });
      if (name && r.id) await api(`/merges/${encodeURIComponent(r.id)}`, { method: "PATCH", body: JSON.stringify({ name }) });
      // Abgewählte: genau diese Paarungen nicht wieder vorschlagen
      const off = sg.chats.filter((x) => !on.has(x.id)).map((x) => x.id);
      if (off.length) for (const o of off) api("/merges/suggestions/dismiss", { method: "POST", body: JSON.stringify({ chats: [o, ...pick.map((x) => x.id)] }) }).catch(() => {});
      await loadMerges(); SUGG = (SUGG || []).filter((x) => x.id !== sg.id); redrawSett();
      toast(`${name || clean(pick[0].title)}: ${pick.length} Chats zusammengeführt`);
    } catch (err) { toast(`Zusammenführen ging nicht: ${err.message}`); }
  });
  document.body.append(d);
}
async function loadMerges() {
  try {
    MERGES = (await api("/merges")).items;
    try { localStorage.setItem("chats-merges", JSON.stringify(MERGES)); } catch {}
    drawList();
  } catch { /* letzter Stand bleibt */ }
}
// Chat „Ida“ (chats/app/ida.py): direkt mit Ida schreiben, ihre Benachrichtigungen kommen auch hier an. Steht wie ein Chat in der Liste.
let IDA = (() => { try { return JSON.parse(localStorage.getItem("chats-ida") || "null"); } catch { return null; } })();
const isIda = (id) => id === "ida";
async function loadIda() {
  try {
    const d = await api("/ida");
    IDA = d.on ? d.chat || IDA : null;
    try { localStorage.setItem("chats-ida", JSON.stringify(IDA)); } catch { /* egal */ }
    drawList(); if (isIda(S.id) && $(".chead")) refreshReadBar();
  } catch { /* letzter Stand bleibt */ }
}
// WebUntis-Nachrichten (chats/app/untis.py): pro Lehrkraft ein Chat „untis:<name>“; alle Lehrkräfte findet die Suche (neuer Chat)
let UNTIS = (() => { try { return { on: false, items: [], teachers: [], ...JSON.parse(localStorage.getItem("chats-untis") || "{}") }; } catch { return { on: false, items: [], teachers: [] }; } })();
const isUntis = (id) => String(id || "").startsWith("untis:");
const saveUntis = () => { try { localStorage.setItem("chats-untis", JSON.stringify(UNTIS)); } catch { /* egal */ } };
async function loadUntis() {
  try {
    const d = await api("/untis/chats");
    UNTIS = { ...UNTIS, on: d.on, items: d.on ? d.error && UNTIS.items.length ? UNTIS.items : d.items : [], error: d.error || "" };
    if (!d.on) UNTIS.teachers = [];
    saveUntis(); drawList(); if (isUntis(S.id) && $(".chead")) refreshReadBar();
    if (d.on && !UNTIS.teachers.length) { UNTIS.teachers = (await api("/untis/teachers")).items; saveUntis(); }
  } catch { /* letzter Stand bleibt */ }
}
const untisChat = (id) => UNTIS.items.find((x) => x.id === id) || UNTIS.teachers.find((x) => x.id === id) || (isUntis(id) ? { id, title: id.slice(6).replace(/-/g, " "), network: "untis", type: "single", untis: true, unread: 0 } : null);
const ownChat = (id) => isIda(id) || isUntis(id);   // nicht aus Beeper: nur Text, keine Reaktionen/Anhänge/Bearbeiten
const extraRows = () => [...(IDA ? [IDA] : []), ...UNTIS.items];   // eigene Chats neben denen aus Beeper
const known = (id) => isIda(id) ? IDA : isUntis(id) ? untisChat(id) : S.chats.find((x) => x.id === id) || PINS.find((x) => x.id === id) || DIR.get(id) || orgStub(id);

// ---------------- Verzeichnis: jeder Chat, der einmal geladen war (Name, Netzwerk), im Browser gemerkt ----------------
// Damit findet die Suche Leute sofort beim Tippen (Beepers Antwort kommt danach dazu), und Chats, die nicht mehr in der
// geladenen Liste stehen (Listen, Ausgeblendet, aus einer Meldung geöffnet), haben trotzdem gleich Namen und Bild.
const DIR_KEY = "chats-dir", DIR_MAX = 1500;
const DIR = (() => { try { return new Map(JSON.parse(localStorage.getItem(DIR_KEY) || "[]").map((c) => [c.id, c])); } catch { return new Map(); } })();
let dirT;
function dirAdd(items) {
  let changed = false;
  for (const c of items) {
    if (!c?.id || isGrp(c.id)) continue;
    const o = DIR.get(c.id), n = { id: c.id, title: c.title || o?.title || "", network: c.network || o?.network || "", type: c.type || o?.type || "single", at: c.at || o?.at || "" };
    if (!o || o.title !== n.title || o.at !== n.at || o.network !== n.network) { DIR.set(c.id, n); changed = true; }
  }
  if (!changed) return;
  clearTimeout(dirT);
  dirT = setTimeout(() => {
    const all = [...DIR.values()].sort((a, b) => (b.at || "").localeCompare(a.at || "")).slice(0, DIR_MAX);
    if (all.length < DIR.size) { DIR.clear(); all.forEach((c) => DIR.set(c.id, c)); }
    try { localStorage.setItem(DIR_KEY, JSON.stringify(all)); } catch { /* voll: dann nur im Speicher */ }
  }, 1500);
}
// Vergleich ohne Groß/klein, Akzente und Schnörkel (꧁☬…, unsichtbare Zeichen) – „moro“ findet „꧁ঔৣ☬Moro☬ঔৣ꧂“
const norm = (t) => clean(t).normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
function localFind(q) {
  const words = norm(q).split(/\s+/).filter(Boolean); if (!words.length) return [];
  const hit = (t) => { const h = norm(t); return words.every((w) => h.includes(w)); };
  const ids = new Set();
  for (const g of MERGES) if (hit(`${g.name || ""} ${g.chats.map((c) => c.title).join(" ")}`)) g.chats.forEach((c) => ids.add(c.id));
  for (const c of [...DIR.values(), ...S.chats]) if (hit(`${c.title} ${netName(c.network)}`)) ids.add(c.id);
  return [...ids].map((id) => S.chats.find((x) => x.id === id) || DIR.get(id)).filter(Boolean).sort((a, b) => (b.at || "").localeCompare(a.at || "")).slice(0, 60);
}
// Zuletzt geladene Startseite: beim Öffnen der App sofort da, Frisches kommt gleich danach
const LIST_KEY = "chats-list";
function cacheList() { if (!S.q && S.f === "all") try { localStorage.setItem(LIST_KEY, JSON.stringify(S.chats.slice(0, 80))); } catch { /* egal */ } }
const SQ = new Map();   // Suchergebnisse der Sitzung: Tippen, Löschen, wieder Tippen ist sofort da
function restoreList() {   // Suche zu: gleich wieder die gemerkte Startseite, Frisches kommt dahinter
  S.q = ""; S.busyQ = false; S.next = null;
  let c = []; if (S.f !== "unread" && S.f !== "archive") try { c = JSON.parse(localStorage.getItem(LIST_KEY) || "[]"); } catch { /* egal */ }
  S.chats = c; if (c.length) drawList();
  loadChats(c.length ? { quiet: true, keep: false } : {});
}

// ---------------- Ordnung: Startseite (immer / nur bei Neuem / ausgeblendet), Netzwerke, eigene Listen – im Dienst, für alle Geräte ----------------
let ORG = (() => { try { return { chats: [], nets: {}, labels: [], ...JSON.parse(localStorage.getItem("chats-org") || "{}") }; } catch { return { chats: [], nets: {}, labels: [] }; } })();
let VIS = new Map(ORG.chats.map((x) => [x.id, x]));
function orgSet(o) {
  ORG = { chats: o.chats || [], nets: o.nets || {}, labels: o.labels || [] }; VIS = new Map(ORG.chats.map((x) => [x.id, x]));
  try { localStorage.setItem("chats-org", JSON.stringify(ORG)); } catch { /* egal */ }
  if (S.f.startsWith("l:") && !ORG.labels.some((l) => `l:${l.id}` === S.f)) setFilter("all");
  drawList(); redrawSett(); redrawInfo(); fbtnState();
}
async function loadOrg() { try { orgSet(await api("/org")); } catch { /* letzter Stand bleibt */ } }
function orgStub(id) {
  const x = VIS.get(id) || ORG.labels.flatMap((l) => l.chats).find((c) => c.id === id);
  return x && x.title ? { id, title: x.title, network: x.network, type: "single", at: "", unread: 0 } : undefined;
}
const VIS_TEXT = { "": "Immer", show: "Immer", new: "Nur bei neuen Nachrichten", hidden: "Ausgeblendet" };
const netVis = (c) => c.merged || grpOf(c.id) ? "" : ORG.nets[netKey(c.network)] || "";   // Netzwerk-Regel gilt nicht für Zusammengeführte
const ownVis = (c) => VIS.get(c.id)?.mode || VIS.get(grpOf(c.id)?.id)?.mode || "";
const visOf = (c) => { if (!c) return ""; const v = ownVis(c); return v === "show" ? "" : v || netVis(c); };
const labelOf = () => S.f.startsWith("l:") ? ORG.labels.find((l) => l.id === S.f.slice(2)) : null;
const inLabel = (l, c) => l.chats.some((x) => x.id === c.id || (c.merged || []).some((m) => m.id === x.id));
function shown(c) {
  if (S.q || S.f === "archive") return true;
  const l = labelOf(); if (l) return inLabel(l, c);
  const v = visOf(c);
  if (S.f === "hidden") return !!v;
  return !v || (v === "new" && !!left(c));
}
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
  if (!g) return;
  const m = g.chats.find((x) => x.id === chat), name = clean(g.name || g.chats[0]?.title || "Chat");
  const ok = await sure(chat ? `${clean(m?.title || "Chat")} herausnehmen?` : "Zusammenführung aufheben?",
    chat ? `Der Chat steht danach wieder einzeln in der Liste.` : `„${name}“ wird wieder zu ${g.chats.length} einzelnen Chats (${g.chats.map((x) => netName(x.network)).join(", ")}). Eigener Name und Einstellungen der Gruppe gehen verloren.`,
    chat ? "Herausnehmen" : "Aufheben", true);
  if (!ok) return;
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
  pushView({ ...(history.state || {}), info: true }, "", path);
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
  const isGroup = c.type === "group", ida = isIda(c.id), untis = isUntis(c.id);
  const phone = !isGroup && !c.merged ? other(one)?.phone : "";
  const media = S.msgs.flatMap((m) => m.deleted ? [] : m.files.filter((f) => f.kind === "image" || f.kind === "video")).slice(-60).reverse();
  const nLinks = S.msgs.reduce((a, m) => a + (m.deleted ? 0 : (m.links?.length || (/https?:\/\//.test(m.text || "") ? 1 : 0))), 0);
  const nFiles = S.msgs.reduce((a, m) => a + (m.deleted ? 0 : m.files.filter((f) => f.kind === "file" || f.kind === "audio").length), 0);
  const pinned = PINS.some((p) => p.id === c.id);
  const untisCh = chats.find((x) => x.untis);
  const sub = c.merged ? c.merged.map((x) => netName(x.network)).join(" · ") : ida ? "Deine persönliche Assistentin" : untis ? [untisCh?.class_teacher ? "Klassenlehrkraft" : "Lehrkraft", untisCh?.school || "WebUntis"].filter(Boolean).join(" · ") : isGroup ? `Gruppe · ${one ? one.total : "…"} Mitglieder` : phone || netName(c.network);
  const row = (icon, main, small = "", attrs = "") => `<button class="irow" ${attrs}><span class="ms">${icon}</span><span class="it"><b>${main}</b>${small ? `<small>${small}</small>` : ""}</span></button>`;
  const desc = chats.map((x) => x.description || x.about).find(Boolean);
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
        ${ida && one?.ida_url ? `<a href="${esc(one.ida_url)}" target="_blank" rel="noopener"><span class="ms">open_in_new</span>IDA</a>` : ""}
        ${untis && untisCh?.web_url ? `<a href="${esc(untisCh.web_url)}" target="_blank" rel="noopener"><span class="ms">open_in_new</span>WebUntis</a>` : ""}
      </div></div>
    ${d?.error ? `<div class="icard ierr"><span class="ms">cloud_off</span>${esc(d.error)}</div>` : ""}
    ${desc ? `<div class="icard"><small>${isGroup ? "Beschreibung" : "Info"}</small><div class="idesc">${autolink(esc(desc))}</div></div>` : ""}
    ${untisCh ? `<div class="icard"><small>Unterricht bei dir</small>
      ${untisCh.class_teacher ? row("verified", "Klassenlehrkraft", untisCh.school || "Deine Klasse") : ""}
      ${(untisCh.subjects || []).length ? `<div class="ufachs">${untisCh.subjects.map((s) => `<span class="ufach">${esc(s)}</span>`).join("")}</div>` : '<p class="ihint">Keine Fächer in WebUntis hinterlegt.</p>'}
    </div>` : ""}
    ${ida ? `<div class="icard"><small>Profilbild</small>
      ${row("add_a_photo", "Eigenes Profilbild wählen", one?.custom_avatar ? "Eigenes Bild aktiv – antippen zum Ändern" : "Standard-Symbol durch ein eigenes Bild ersetzen", 'data-i="av-up"')}
      ${one?.custom_avatar ? row("delete", "Eigenes Bild entfernen", "Zurück zum Standard-Symbol", 'data-i="av-del"') : ""}
    </div>` : ""}
    ${media.length || nLinks || nFiles ? `<div class="icard"><div class="ich"><small>Medien, Links und Dateien</small><small>${media.length} · ${nLinks} · ${nFiles}</small></div>
      ${media.length ? `<div class="igrid">${media.slice(0, 9).map((f) => `<button class="ithumb" data-i="media" data-src="${esc(f.src || "")}" data-kind="${f.kind}">${f.kind === "image" ? `<img loading="lazy" alt="" src="${mediaUrl(f)}">` : f.poster ? `<img loading="lazy" alt="" src="${mediaUrl({ src: f.poster })}">` : ""}${f.kind === "video" ? '<span class="ms">play_circle</span>' : ""}</button>`).join("")}</div>` : ""}
      <p class="ihint">Aus den geladenen Nachrichten</p></div>` : ""}
    ${c.merged ? `<div class="icard"><small>Zusammengeführte Chats</small>${c.merged.map((x) => { const a = chats.find((y) => y.id === x.id), p = other(a);
        return `<div class="iperson">${netAv(x, true)}<span class="it"><b>${esc(clean(x.title))}</b><small>${esc([netName(x.network), a?.untis && (a.subjects || []).join(", "), p?.phone, p?.username && "@" + p.username].filter(Boolean).join(" · "))}</small></span></div>`; }).join("")}
      ${row("tune", "Name, Profilbild, Standard", "", 'data-i="gset"')}</div>` : ""}
    ${isGroup && one ? `<div class="icard"><div class="ich"><small>${one.total} Mitglieder</small></div>${one.people.filter((p) => !p.bot).sort((a, b) => (b.self - a.self) || (b.admin - a.admin) || a.name.localeCompare(b.name)).map((p) =>
        `<div class="iperson">${avatar(clean(p.name), c.id, p.id)}<span class="it"><b>${p.self ? "Du" : esc(clean(p.name) || "Unbekannt")}</b><small>${esc([p.phone, p.username && "@" + p.username].filter(Boolean).join(" · "))}</small></span>${p.admin ? '<span class="iadm">Admin</span>' : ""}</div>`).join("")}</div>` : ""}
    ${!isGroup && !c.merged && other(one) && (other(one).username || other(one).email) ? `<div class="icard">${other(one).username ? row("alternate_email", esc(other(one).username), "Benutzername") : ""}${other(one).email ? row("mail", esc(other(one).email), "E-Mail") : ""}</div>` : ""}
    <div class="icard">
      ${expiry ? row("timer", "Selbstlöschende Nachrichten", `Nach ${per(expiry)}`) : ""}
      ${row(isMuted(c.id) ? "notifications_off" : "notifications", isMuted(c.id) ? "Stummgeschaltet" : "Stummschalten", isMuted(c.id) ? muteText(c.id) : "Keine Benachrichtigungen aufs Handy", 'data-i="mute"')}
      ${!ownChat(c.id) ? row("mark_chat_read", "Automatisch als gelesen", AR_TEXT[arOf(c.id)], 'data-i="autoread"') : ""}
      ${row(visOf(c) ? "visibility_off" : "visibility", "Auf der Startseite", esc(VIS_TEXT[visOf(c)] + (!ownVis(c) && netVis(c) ? ` (wie alle ${netName(c.network)}-Chats)` : "")), 'data-i="vis"')}
      ${row("label", "Listen", esc(ORG.labels.filter((l) => inLabel(l, c)).map((l) => l.name).join(", ") || "In keiner Liste"), 'data-i="lists"')}
      ${!ownChat(c.id) ? row("archive", "Archivieren", "In Beeper archivieren – taucht bei einer neuen Nachricht wieder auf", 'data-i="arch"') : ""}
      ${chats.some((x) => x.muted) ? row("notifications_off", "In Beeper stummgeschaltet", "Auch hier keine Benachrichtigungen") : ""}
      ${left(c) ? row("done_all", "Als gelesen markieren", "", 'data-i="read"') : !ownChat(c.id) ? row("mark_chat_unread", "Als ungelesen markieren", "", 'data-i="unread"') : ""}
      ${!ida ? row("call_merge", "Mit anderem Chat zusammenführen", "", 'data-i="merge"') : ""}
      ${c.merged ? `<button class="irow danger" data-i="split"><span class="ms">call_split</span><span class="it"><b>Zusammenführung aufheben</b></span></button>` : ""}
    </div>
  </div>`;
}
function refreshAv(id) {
  avV++; AV_OK.clear(); AV_NO.clear(); ABOUT.delete(id);
  if ($(".chead")) $(".chead").outerHTML = head();
  drawList();
  api(`/chats/${encodeURIComponent(id)}/about`).then((d) => { ABOUT.set(id, { t: Date.now(), d }); redrawInfo(); }).catch(() => redrawInfo());
}
function uploadIdaAvatar() {
  const inp = document.createElement("input"); inp.type = "file"; inp.accept = "image/*";
  inp.onchange = async () => {
    const f = inp.files?.[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f, f.name || "avatar.png");
    try {
      const r = await fetch(`${API}/avatar/ida`, { method: "PUT", credentials: "same-origin", body: fd });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
      toast("Profilbild gespeichert"); refreshAv("ida");
    } catch (e) { toast(`Ging nicht: ${e.message}`); }
  };
  inp.click();
}
async function deleteIdaAvatar() {
  try { await api("/avatar/ida", { method: "DELETE" }); toast("Standard-Profilbild wiederhergestellt"); refreshAv("ida"); }
  catch (e) { toast(`Ging nicht: ${e.message}`); }
}
document.addEventListener("click", (e) => {
  const av = e.target.closest("#info .ihero > .av, #info .iperson .av, #msgs .bw > .av"); if (av && avatarBig(av)) return;
  const a = e.target.closest("#info [data-i]")?.dataset.i; if (!a) return;
  const c = cur(); if (!c) return;
  if (a === "close" || a === "chat") return closeInfo();
  if (a === "pin") { togglePin(c); setTimeout(() => { const el = $("#info"); if (el) el.innerHTML = infoHtml(cur(), ABOUT.get(c.id)?.d); }, 50); return; }
  if (a === "search") return openSearch();
  if (a === "av-up") return uploadIdaAvatar();
  if (a === "av-del") return deleteIdaAvatar();
  if (a === "media") { const t = e.target.closest("[data-src]"); if (t.dataset.kind === "image") return gallery(t.dataset.src); closeInfo(); const el = [...document.querySelectorAll("#msgs .vid")].find((v) => v.dataset.src === t.dataset.src); el?.scrollIntoView({ block: "center" }); return; }
  if (a === "gset") return groupSettings(c);
  if (a === "mute") return muteMenu(c);
  if (a === "autoread") return autoReadMenu(c);
  if (a === "vis") return visMenu(c);
  if (a === "lists") return labelPick(c);
  if (a === "arch") { closeInfo(); return archiveChat(c, true); }
  if (a === "read") { markRead(c); return closeInfo(); }
  if (a === "unread") { markUnread(c); return closeInfo(); }
  if (a === "merge") return chatPick({ title: "Zusammenführen mit …", verb: "Zusammenführen", multi: true, from: c, skip: new Set(["ida", ...(c.merged || [c]).map((x) => x.id)]) }, (to) => { closeInfo(); doMerge(c, to); });
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
  pushView({ ...(history.state || {}), srch: true }, "", path);
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
  const on = PINS.some((p) => p.id === c.id), own = ownChat(c.id), ida = isIda(c.id), d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet">
    <button data-p="pin"><span class="ms">push_pin</span>${on ? "Nicht mehr anpinnen" : "Anpinnen"}</button>
    <button data-p="mute"><span class="ms">${isMuted(c.id) ? "notifications" : "notifications_off"}</span>${isMuted(c.id) ? "Stummschaltung ändern" : "Stummschalten"}</button>
    ${!ida ? '<button data-p="merge"><span class="ms">call_merge</span>Mit anderem Chat zusammenführen</button>' : ""}
    ${c.merged ? '<button data-p="gset"><span class="ms">tune</span>Name, Profilbild, Standard</button>' : ""}
    ${c.merged ? '<button data-p="split"><span class="ms">call_split</span>Zusammenführung aufheben</button>' : ""}
    ${left(c) ? '<button data-p="read"><span class="ms">done_all</span>Als gelesen markieren</button>' : !own ? '<button data-p="unread"><span class="ms">mark_chat_unread</span>Als ungelesen markieren</button>' : ""}
    <button data-p="vis"><span class="ms">${visOf(c) ? "visibility_off" : "visibility"}</span>Auf der Startseite<small>${esc(VIS_TEXT[visOf(c)])}</small></button>
    <button data-p="lists"><span class="ms">label</span>Listen<small>${esc(ORG.labels.filter((l) => inLabel(l, c)).map((l) => l.name).join(", "))}</small></button>
    ${!own ? `<button data-p="arch"><span class="ms">${S.f === "archive" ? "unarchive" : "archive"}</span>${S.f === "archive" ? "Aus dem Archiv holen" : "Archivieren"}</button>` : ""}</div>`;
  d.addEventListener("click", (e) => {
    const p = e.target.closest("[data-p]")?.dataset.p;
    closeMenu();
    if (p === "vis") return visMenu(c);
    if (p === "lists") return labelPick(c);
    if (p === "arch") return archiveChat(c, S.f !== "archive");
    if (p === "pin") togglePin(c);
    if (p === "read") markRead(c);
    if (p === "unread") markUnread(c);
    if (p === "merge") chatPick({ title: "Zusammenführen mit …", verb: "Zusammenführen", multi: true, from: c, skip: new Set(["ida", ...(c.merged || [c]).map((x) => x.id)]) }, (to) => doMerge(c, to));
    if (p === "split") unMerge(grpOf(c.id));
    if (p === "gset") groupSettings(c);
    if (p === "mute") muteMenu(c);
  });
  document.body.append(d);
}
const rowChat = (id) => { const g = MERGES.find((x) => x.id === id); return g ? grpRow(g) : known(id); };
let rowT, rowHeld = false;
$("#list").addEventListener("pointerdown", (e) => {
  const r = e.target.closest(".row, .ptile"); rowHeld = false;
  if (!r || e.pointerType === "mouse") return;
  const x = e.clientX, y = e.clientY;
  const stop = () => { clearTimeout(rowT); removeEventListener("pointermove", mv); removeEventListener("pointerup", stop); removeEventListener("pointercancel", stop); };
  const mv = (m) => { if (Math.abs(m.clientX - x) > 10 || Math.abs(m.clientY - y) > 10) stop(); };
  addEventListener("pointermove", mv); addEventListener("pointerup", stop); addEventListener("pointercancel", stop);
  rowT = setTimeout(() => { stop(); const c = rowChat(r.dataset.id); if (c) { rowHeld = true; navigator.vibrate?.(10); rowMenu(c); } }, 480);
});
$("#list").addEventListener("contextmenu", (e) => {
  const r = e.target.closest(".row, .ptile"); if (!r) return;
  e.preventDefault();
  const c = rowChat(r.dataset.id); if (c && !$("#ctx")) rowMenu(c);
});
function lastLine(c) {   // "Du: …" / in Gruppen "Name: …" / sonst nur der Text; ohne Text ein Hinweis aufs Netzwerk
  const l = c.last;
  if (!l) return `<i>${esc(c.merged ? c.merged.map((x) => netName(x.network)).join(" · ") : netName(c.network))}</i>`;
  const who = l.mine ? "Du: " : l.sender ? `${clean(l.sender)}: ` : "";
  return `${who ? `<b>${esc(who)}</b>` : ""}${l.text ? esc(clean(l.text)) : "<i>Anhang</i>"}`;
}
const moreObs = new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting)) { moreObs.disconnect(); loadChats({ more: true }); } }, { root: $("#list"), rootMargin: "0px 0px 400px 0px" });
function drawList() {
  const pinIds = new Set(PINS.map((p) => p.id)), showPins = S.f === "all" && !S.q;
  const pinned = showPins ? collapse(PINS.map((p) => { const c = known(p.id); return c ? { ...c, pinned: true } : p; })) : [];
  const pinGrp = new Set(pinned.map((c) => c.id));
  const raw = S.q ? [...S.chats, ...(IDA ? [IDA] : []), ...UNTIS.items, ...UNTIS.teachers.filter((t) => !UNTIS.items.some((x) => x.id === t.id)).filter((c) => norm(c.title).includes(norm(S.q)))]
    : S.f === "archive" ? S.chats : [...S.chats, ...extraRows().filter((x) => !S.chats.some((c) => c.id === x.id))];
  const all = collapse(showPins ? raw.filter((c) => !pinIds.has(c.id)) : raw).filter((c) => !pinGrp.has(c.id));
  const rest = (S.f === "unread" && !S.q ? all.filter(left) : all.filter(shown)).sort((a, b) => S.q ? 0 : (b.at || "").localeCompare(a.at || ""));
  // Listen und „Ausgeblendet“: Chats, die (noch) nicht geladen sind, kommen aus dem Gemerkten dazu
  const lb = labelOf();
  if (!S.q && (lb || S.f === "hidden")) {
    const have = new Set(all.flatMap((c) => [c.id, ...(c.merged || []).map((m) => m.id)]));
    const want = lb ? lb.chats : ORG.chats.filter((x) => x.mode === "new" || x.mode === "hidden");
    for (const x of want) if (!have.has(x.id)) { const c = rowChat(grpOf(x.id)?.id || x.id) || { id: x.id, title: x.title, network: x.network, unread: 0 }; if (!rest.some((r) => r.id === c.id)) rest.push(c); }
  }
  // Zähler (Titel, App-Symbol): ausgeblendete Chats zählen nicht mit
  const n = [...pinned, ...collapse([...S.chats, ...extraRows()].filter((c) => !pinIds.has(c.id))).filter((c) => visOf(c) !== "hidden")].reduce((a, c) => a + left(c), 0);
  document.title = n ? `(${n}) Chats` : "Chats";
  const ib = $("#idabtn"); if (ib) ib.classList.toggle("unread", !!(IDA && left(IDA)));
  if (navigator.setAppBadge) (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => {});
  if (!rest.length && !pinned.length && !S.next) {
    const [ic, t] = S.q ? ["search_off", S.busyQ ? "Suche …" : "Nichts gefunden."] : lb ? ["label", `Noch niemand in „${esc(lb.name)}“. Chat lange drücken → Listen.`]
      : S.f === "hidden" ? ["visibility", "Nichts ausgeblendet."] : S.f === "unread" ? ["done_all", "Alles gelesen."] : ["forum", "Keine Chats."];
    $("#list").innerHTML = `<div class="empty"><span class="ms">${ic}</span>${t}</div>`; return;
  }
  const row = (c, pin) => `<div class="row ${left(c) ? "unread" : ""} ${c.id === S.id ? "sel" : ""}" data-id="${esc(c.id)}">
      ${pickAv(c)}<div class="rmain"><div class="rtop"><span class="rtitle">${esc(clean(c.title))}</span><span class="rtime">${when(c.at)}</span></div>
      <div class="rsub"><span class="last">${lastLine(c)}</span>${isMuted(c.id) ? '<span class="ms mute">notifications_off</span>' : ""}${pin ? '<span class="ms pin">push_pin</span>' : ""}${left(c) ? `<span class="cnt">${c.unread}</span>` : c.snaps && !(c.id in seen && (c.at || "") <= seen[c.id]) ? `<span class="snapchip" title="Neue Snaps (ohne Meldung)"><span class="ms">photo_camera</span>${c.snaps > 1 ? c.snaps : ""}</span>` : ""}</div></div></div>`;
  // Angepinnte Chats: große runde Bilder nebeneinander ganz oben (wie in Beeper), darunter die übrige Liste
  const tile = (c) => `<button type="button" class="ptile ${left(c) ? "unread" : ""} ${c.id === S.id ? "sel" : ""}" data-id="${esc(c.id)}">
      <span class="pav">${pickAv(c)}${left(c) ? `<span class="cnt">${c.unread}</span>` : ""}</span>
      <span class="pname"><span>${esc(clean(c.title))}</span>${isMuted(c.id) ? '<span class="ms">notifications_off</span>' : ""}</span></button>`;
  $("#list").innerHTML = (pinned.length ? `<div class="pins">${pinned.map(tile).join("")}</div>` : "") + rest.map((c) => row(c, false)).join("")
    + (S.q && S.busyQ ? '<div class="more" id="qbusy"><span class="ms">refresh</span></div>' : "")
    + (S.next ? '<button class="more" id="more" aria-label="Mehr laden"><span class="ms">refresh</span></button>' : "");
  moreObs.disconnect(); if ($("#more")) moreObs.observe($("#more"));   // kommt das Ende in Sicht, lädt die nächste Seite von selbst
  if (S.id && $(".chead")) refreshReadBar();
}
$("#list").addEventListener("click", (e) => {
  if (e.target.closest("#more")) return loadChats({ more: true });
  if (rowHeld) { rowHeld = false; return; }
  const av = e.target.closest(".row .av"); if (av && avatarBig(av)) return;
  const r = e.target.closest(".row, .ptile");
  if (!r) return;
  go(r.dataset.id);
});
// Filter (Alle / Ungelesen / Ausgeblendet / Archiv / eigene Listen) hinter dem Filter-Knopf links in der Leiste unten; das Menü klappt nach oben auf
const FILTERS = [["all", "forum", "Alle"], ["unread", "mark_chat_unread", "Ungelesen"], ["hidden", "visibility_off", "Ausgeblendet"], ["archive", "archive", "Archiv"]];
const filters = () => [...FILTERS, ...ORG.labels.map((l) => [`l:${l.id}`, "label", l.name])];
function fbtnState() { const b = $("#fbtn"), f = filters().find((x) => x[0] === S.f); b.classList.toggle("on", S.f !== "all"); b.setAttribute("aria-label", `Filter: ${f ? f[2] : "Alle"}`); }
function setFilter(f) {
  if (f === S.f) return;
  // Listen und „Ausgeblendet“ brauchen dieselbe Liste wie „Alle“ – dann nicht neu laden, nur anders filtern
  const same = (x) => x === "all" || x === "hidden" || x.startsWith("l:");
  const reuse = same(f) && same(S.f) && S.chats.length;
  S.f = f; fbtnState();
  if (reuse) { drawList(); listIn(); $("#list").scrollTop = 0; } else { S.chats = []; loadChats(); }
}
$("#fbtn").onclick = (e) => {
  e.preventDefault(); closeMenu();
  const r = e.currentTarget.getBoundingClientRect(), d = document.createElement("div"), fs = filters();
  d.className = "ctx"; d.id = "ctx";
  const btn = ([f, ic, t]) => `<button data-f="${esc(f)}" class="${S.f === f ? "on" : ""}"><span class="ms">${ic}</span><span class="fl">${esc(t)}</span>${S.f === f ? '<span class="ms tick">check</span>' : ""}</button>`;
  d.innerHTML = `<div class="ctx-back clear"></div><div class="ctx-box fmenu" style="bottom:${innerHeight - r.top + 8}px;left:${Math.max(8, r.left)}px">${fs.slice(0, FILTERS.length).map(btn).join("")}
    ${fs.length > FILTERS.length ? `<div class="fsep"></div>${fs.slice(FILTERS.length).map(btn).join("")}` : ""}
    <div class="fsep"></div><button data-f="+"><span class="ms">new_label</span>Neue Liste …</button></div>`;
  d.addEventListener("click", (ev) => {
    const f = ev.target.closest("[data-f]")?.dataset.f; closeMenu();
    if (f === "+") return labelNew();
    if (f) setFilter(f);
  });
  document.body.append(d);
};
let qT;
$("#q").addEventListener("input", () => {
  clearTimeout(qT);
  const q = $("#q").value.trim();
  if (!q) { qT = setTimeout(restoreList, 120); return; }
  // Sofort: was das Gerät schon kennt (und frühere Antworten dieser Sitzung); Beeper sucht kurz danach im Hintergrund weiter
  S.q = q; S.next = null; S.busyQ = true; S.chats = SQ.get(`${S.f}|${q}`)?.items || localFind(q); drawList(); $("#list").scrollTop = 0;
  qT = setTimeout(() => loadChats({ quiet: true, keep: false }), 220);
});
// Chat-Suche: hinter dem Such-Knopf rechts unten, mit eigener Adresse (…/suche), damit die Zurück-Taste sie wieder schließt
const readLsrch = () => location.pathname.slice(BASE.length) === "suche";
function showLsrch(on) {
  $("#tools").hidden = !on; document.body.classList.toggle("lsrch", on);
  if (on) { $("#q").focus(); return; }
  clearTimeout(qT); $("#q").blur();
  if ($("#q").value || S.q) { $("#q").value = ""; restoreList(); }
}
$("#sbtn").onclick = () => { if (!readLsrch()) pushView({ lsrch: true }, "", `${BASE}suche`); showLsrch(true); };
$("#qback").onclick = () => { if (history.state?.lsrch) history.back(); else { history.replaceState(null, "", BASE); showLsrch(false); } };
$("#q").addEventListener("keydown", (e) => { if (e.key === "Escape") $("#qback").click(); });
$("#setbtn").onclick = () => openSett();
if ($("#idabtn")) $("#idabtn").onclick = () => go("ida");

// ---------------- Nach unten ziehen lädt die Chats neu; danach tauchen die Zeilen schnell nacheinander auf ----------------
function listIn() {   // Auftritt der Liste: Zeile für Zeile, leicht versetzt
  const els = [...document.querySelectorAll("#list .ptile, #list .row")].slice(0, 16);
  els.forEach((el, i) => { el.style.animationDelay = `${i * 28}ms`; el.classList.add("rin"); });
  setTimeout(() => els.forEach((el) => { el.classList.remove("rin"); el.style.animationDelay = ""; }), 16 * 28 + 400);
}
async function pullReload() {
  await Promise.all([loadChats({ quiet: true, keep: false }), loadPins()]);
  if (S.id) loadMsgs({ quiet: true });
  drawList(); listIn();
}
{
  const list = $("#list"), PULL = 72, MAX = 120;
  const ptr = document.createElement("div"); ptr.className = "ptr"; ptr.innerHTML = '<span class="ms">refresh</span>'; list.before(ptr);
  let y0 = null, x0 = 0, dy = 0, on = false, busy = false;
  const set = (v, anim) => {
    list.style.transition = ptr.style.transition = anim ? "transform .28s cubic-bezier(.2,0,0,1), opacity .2s" : "none";
    list.style.transform = v ? `translateY(${v}px)` : "";
    ptr.style.transform = `translateY(${v - 48}px) rotate(${v * 3}deg)`; ptr.style.opacity = Math.min(1, v / PULL);
    ptr.classList.toggle("go", v >= PULL);
  };
  list.addEventListener("touchstart", (e) => { y0 = !busy && e.touches.length === 1 && list.scrollTop <= 0 ? e.touches[0].clientY : null; x0 = e.touches[0].clientX; dy = 0; on = false; }, { passive: true });
  list.addEventListener("touchmove", (e) => {
    if (y0 === null) return;
    const d = e.touches[0].clientY - y0, dx = Math.abs(e.touches[0].clientX - x0);
    if (!on) { if (d < 10 || dx > d || list.scrollTop > 0) { if (d < -6 || dx > 14) y0 = null; return; } on = true; rowHeld = false; }
    dy = Math.min(MAX, (d - 10) * .5); set(Math.max(0, dy), false);
    if (dy >= PULL && !ptr.dataset.buzz) { ptr.dataset.buzz = "1"; buzz(8); } else if (dy < PULL) delete ptr.dataset.buzz;
  }, { passive: true });
  const end = async () => {
    if (y0 === null || !on) { y0 = null; return; }
    y0 = null; on = false; delete ptr.dataset.buzz;
    if (dy < PULL) return set(0, true);
    busy = true; set(56, true); ptr.classList.add("spin");
    try { await pullReload(); } finally { busy = false; ptr.classList.remove("spin"); set(0, true); }
  };
  list.addEventListener("touchend", end); list.addEventListener("touchcancel", end);
}

// ---------------- Unterhaltung ----------------
const cur = () => S.id && rowChat(S.id);
// In zusammengeführten Chats: zu welchem echten Chat gehört eine Nachricht, wie heißt sie dort, und wohin geht, was ich schreibe?
const cid = (m) => m?.chat || S.id, mid = (m) => m?.mid || m?.id;
function target() {
  const c = cur(); if (!c?.merged) return S.id;
  if (S.to && c.merged.some((x) => x.id === S.to)) return S.to;
  if (activeOnly()) return S.only;   // nur WhatsApp gezeigt → auch darüber schreiben
  const g = grpOf(c.id); if (g?.send && c.merged.some((x) => x.id === g.send)) return g.send;
  const last = [...S.msgs].reverse().find((m) => m.chat && !m.mine) || [...S.msgs].reverse().find((m) => m.chat);
  return last?.chat || c.merged[0].id;
}
const NET_NAME = { snapchat: "Snapchat", instagram: "Instagram", whatsapp: "WhatsApp", signal: "Signal", telegram: "Telegram", discord: "Discord", messenger: "Messenger",
  ida: "Ida", untis: "WebUntis", gmessages: "SMS", googlechat: "Google Chat", slack: "Slack", linkedin: "LinkedIn", x: "X", imessage: "iMessage" };
const netName = (n) => NET_NAME[netKey(n)] || String(n || "Chat");
// Kleine Logos der Apps fürs Abzeichen (eigene vereinfachte Zeichnungen, Farbe kommt aus [data-net] in app.css)
const BUBBLE = "M12 3a9 9 0 0 0-7.8 13.5L3 21l4.6-1.2A9 9 0 1 0 12 3z";
const NET_SVG = {
  whatsapp: `<path d="${BUBBLE}" fill="none" stroke="currentColor" stroke-width="2"/><path d="M9.2 7.3c-.7 0-1.6.8-1.6 1.9 0 2.8 4 7 7.3 7.2 1 0 1.8-.8 1.8-1.5l-.1-.5-2-1-1 .9c-1.1-.5-2.4-1.8-2.9-2.9l.9-1-.9-2.1z" fill="currentColor"/>`,
  instagram: '<rect x="3.5" y="3.5" width="17" height="17" rx="5" fill="none" stroke="currentColor" stroke-width="2.2"/><circle cx="12" cy="12" r="3.9" fill="none" stroke="currentColor" stroke-width="2.2"/><circle cx="17.2" cy="6.8" r="1.3" fill="currentColor"/>',
  snapchat: '<path d="M12 2.8c3.1 0 5.2 2.3 5.2 5.2v2.2l1.5-.5c.6 0 .9.7.3 1l-1.8 1c.6 2 2.1 3.4 3.7 3.8-.2.8-1.3 1.1-2.5 1.2l-.4 1.3c-1.4-.3-2.4.1-3.5.9-.8.6-1.5.9-2.5.9s-1.7-.3-2.5-.9c-1.1-.8-2.1-1.2-3.5-.9l-.4-1.3c-1.2-.1-2.3-.4-2.5-1.2 1.6-.4 3.1-1.8 3.7-3.8l-1.8-1c-.6-.3-.3-1 .3-1l1.5.5V8c0-2.9 2.1-5.2 5.2-5.2z" fill="currentColor"/>',
  telegram: '<path d="M2.8 11.3 20.3 4.4c.8-.3 1.6.4 1.4 1.3l-3 14.1c-.2.9-1.2 1.2-1.9.7l-4.7-3.5-2.3 2.3c-.3.3-.9.1-.9-.4v-3.6l8.4-7.6-10.4 6.5-3.9-1.3c-.9-.3-.9-1.5-.2-1.6z" fill="currentColor"/>',
  signal: `<path d="${BUBBLE}" fill="currentColor"/>`,
  messenger: `<path d="${BUBBLE}" fill="currentColor"/><path d="m6.5 14.5 4-4.3 2.3 2.1 4.7-2.8-4 4.3-2.3-2.1z" fill="#7a3cff"/>`,
  discord: '<path d="M6.8 6.2c3.3-1.4 7.1-1.4 10.4 0 1.8 2.7 2.7 5.6 2.5 9.1-1.5 1.2-3.1 1.9-4.7 2.3l-1-1.6c-2.7.7-5.3.7-8 0l-1 1.6c-1.6-.4-3.2-1.1-4.7-2.3-.2-3.5.7-6.4 2.5-9.1z" fill="currentColor"/><circle cx="9.2" cy="12.3" r="1.6" fill="#5865f2"/><circle cx="14.8" cy="12.3" r="1.6" fill="#5865f2"/>',
  gmessages: '<path d="M4 4h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H8l-4 4V5a1 1 0 0 1 1-1z" fill="currentColor"/>',
  imessage: `<path d="${BUBBLE}" fill="currentColor"/>`,
  untis: '<path d="M12 3 1.5 8.5 12 14l8.5-4.45V16h2V8.5z" fill="currentColor"/><path d="M5.5 12.6v3.9C7.3 18.4 9.5 19.5 12 19.5s4.7-1.1 6.5-3V12.6L12 16z" fill="currentColor"/>',
  ida: '<path d="M12 2.5l2.3 6.2 6.2 2.3-6.2 2.3L12 19.5l-2.3-6.2L3.5 11l6.2-2.3z" fill="currentColor"/><path d="M19 15.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" fill="currentColor"/>',
};
const netMark = (n) => { const k = netKey(n); return NET_SVG[k] ? `<svg viewBox="0 0 24 24" aria-hidden="true">${NET_SVG[k]}</svg>` : esc(netName(n)[0]); };
// Profilbild mit kleinem Abzeichen unten rechts (wie in Beeper): Logo der App – bei zusammengeführten Chats ein eigenes Zeichen
const netAv = (x, big = false) => `<span class="avw ${big ? "big" : ""}">${avatar(clean(x.title), x.id)}${x.merged
  ? `<i class="nbadge merged" title="Zusammengeführt: ${esc(x.merged.map((m) => netName(m.network)).join(", "))}"><span class="ms">merge</span></i>`
  : `<i class="nbadge" data-net="${netKey(x.network)}" title="${esc(netName(x.network))}">${netMark(x.network)}</i>`}</span>`;
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
  const sub = [c?.ida ? "Deine Assistentin" : c?.untis ? "WebUntis" : "", c?.type === "group" ? "Gruppe" : ""].filter(Boolean).join(" · ");   // kein App-Name unter dem Namen (WhatsApp, Snapchat …)
  return `<div class="chead"><button class="icon-btn back float" data-a="back" aria-label="Zurück"><span class="ms">arrow_back</span></button><div class="cinfo float" data-a="info" role="button" title="Info anzeigen">${avatar(title, c?.av || S.id)}
    <div class="t"><b>${esc(title)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</div></div>${c?.merged ? filterBtn() : ""}</div>`;
}
// Filter oben rechts (nur bei zusammengeführten Chats): zeigt nur die Nachrichten einer App. Ob er beim Verlassen des Chats zurückgesetzt wird,
// stellt man in den Einstellungen ein („Filter merken“, nur auf diesem Gerät; aus = zurücksetzen).
const FK_KEEP = "chats-filter-keep", FK_MAP = "chats-filter";
const filterKeep = () => { try { return localStorage.getItem(FK_KEEP) === "1"; } catch { return false; } };
const FILT = (() => { try { return JSON.parse(localStorage.getItem(FK_MAP) || "{}"); } catch { return {}; } })();   // Chat → gewähltes Netzwerk (nur bei „Filter merken“)
const saveFilt = () => { try { localStorage.setItem(FK_MAP, JSON.stringify(filterKeep() ? FILT : {})); } catch { /* egal */ } };
function activeOnly() { const c = cur(); return c?.merged && S.only && c.merged.some((x) => x.id === S.only) ? S.only : ""; }
function filterBtn() {
  const o = activeOnly(), x = o && cur().merged.find((m) => m.id === o);
  return `<button class="icon-btn filt float${o ? " on" : ""}" data-a="filter" aria-label="Filter" title="${o ? `Nur ${esc(netName(x.network))}` : "Filter"}"><span class="ms">filter_list</span>${o ? "<i></i>" : ""}</button>`;
}
function setOnly(id) {
  S.only = id || ""; S.to = null;
  if (S.id && filterKeep()) { if (S.only) FILT[S.id] = S.only; else delete FILT[S.id]; saveFilt(); }   // ohne „Filter merken“ gilt er nur, bis man den Chat verlässt
  const h = $(".chead"); if (h) h.outerHTML = head();
  drawMsgs({ stick: true }); toChip();
}
function filterMenu() {
  const c = cur(); if (!c?.merged) return;
  const same = (n) => c.merged.filter((x) => netName(x.network) === n).length > 1;   // zwei Chats derselben App: den Namen dazuschreiben
  sheetPick([["", "forum", "Alle Apps", "Alle Nachrichten zusammen"],
    ...c.merged.map((x) => [x.id, "chat", netName(x.network), same(netName(x.network)) ? clean(x.title) : "Nur diese Nachrichten"])], activeOnly(), (v) => setOnly(v));
}
function setFilterKeep(on) {
  try { localStorage.setItem(FK_KEEP, on ? "1" : "0"); } catch { /* egal */ }
  if (!on) for (const k in FILT) delete FILT[k];
  else if (S.id && S.only) FILT[S.id] = S.only;
  saveFilt(); redrawSett();
}
function readBar(c) {
  return left(c) ? `<div class="readbar" id="readbar"><button class="chip" data-a="seen">Als gelesen markieren</button></div>` : "";
}
function refreshReadBar() {   // nur bei echtem Wechsel anfassen – sonst flackert die Leiste bei jedem Neuzeichnen der Liste
  const c = cur(), old = $("#readbar:not(.out)"), want = !!left(c);
  if (old && !want) { old.classList.add("out"); setTimeout(() => old.remove(), 200); }
  if (!want) { const u = $("#unsep:not(.out)"); if (u) { u.classList.add("out"); setTimeout(() => u.remove(), 260); } }
  const d = $("#dock"); if (d && want && !old) d.insertAdjacentHTML("afterbegin", readBar(c));
  const ub = $("#unseenBtn"); if (ub) ub.hidden = want;
}
async function openChat() {
  drawn = { id: null, ids: new Set() };   // auch derselbe Chat noch einmal: Auftritt von vorn
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
  if (ownChat(S.id)) { ta.placeholder = isIda(S.id) ? "Nachricht an Ida" : `Nachricht an ${clean(cur()?.title || "")} (WebUntis)`; sendMode(); }
  $("#toBtn").onclick = toMenu; toChip();
  $("#recbar").addEventListener("click", (e) => { const a = e.target.closest("[data-rec]")?.dataset.rec; if (a) recAction(a); });
  const conv = $("#conv");
  conv.ondragover = (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); conv.classList.add("drop"); } };
  conv.ondragleave = (e) => { if (e.target === conv || !conv.contains(e.relatedTarget)) conv.classList.remove("drop"); };
  conv.ondrop = (e) => { conv.classList.remove("drop"); const f = [...(e.dataTransfer?.files || [])]; if (f.length) { e.preventDefault(); pickFiles(f); } };
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); $("#form").requestSubmit(); } });
  $("#form").addEventListener("submit", (e) => { e.preventDefault(); if (ta.value.trim()) send(); else if (!S.edit && !ownChat(S.id)) startRec(); });
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
  if (older) { if (olderBusy) return; olderBusy = true; }
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
    const sig = (l) => l.map((m) => `${m.id}|${m.text}|${m.edited ? 1 : 0}|${(m.read_by || []).length}|${(m.reactions || []).map((r) => r.key + r.n).join()}|${m.deleted}${m.was ? 1 : 0}|${m.pending ? 1 : 0}|${(m.options || []).join(",")}`).join(";");
    const before = sig(S.msgs);
    const landed = (m) => m.pendingFile ? m.sentAt && d.items.some((x) => x.mine && x.files.length && Date.parse(x.at) >= Date.parse(m.at) - 10000)
      : m.pendingFwd ? d.items.some((x) => x.mine && x.ida_kind === "forward" && Date.parse(x.at) >= Date.parse(m.at) - 15000)
      : d.items.some((x) => x.mine && x.text === m.text);
    // Was im Zeitraum der geladenen Seite liegt, dort aber fehlt, gibt es nicht mehr (z. B. für mich gelöscht) – nicht aus dem Speicher zurückholen
    const lo = d.items[0]?.at, hi = d.items.at(-1)?.at, got = new Set(d.items.map((m) => m.id));
    const stale = (m) => !m.pending && m.at && lo && m.at >= lo && m.at <= hi && !got.has(m.id);
    S.msgs = [...keep.values()].filter((m) => !stale(m) && (!m.pending || !landed(m)) && (!m.typing || got.has(m.id))).sort((a, b) => (a.at || "").localeCompare(b.at || ""));
    if (!keepOld) { S.more = d.more; S.oldest = d.oldest; }
    cacheMsgs();
    setTimeout(prefetchOlder, 300);
    if (quiet && !first && !older && before === sig(S.msgs) && $("#msgs")?.children.length) return;   // nichts Neues: nicht neu zeichnen (Scrollstand, Player)
    if (first && !older && before === sig(S.msgs) && $("#msgs .bw")) return;   // aus dem Speicher schon richtig gezeichnet
    drawMsgs({ stick: first || atEnd, keepTop: older }); toChip();
  } catch (e) {
    if (older) toast(`Ältere Nachrichten: ${e.message}`);
    else if (!quiet && id === S.id) { const b = $("#msgs"); if (b) b.innerHTML = errBox(e); }
  } finally { if (older) olderBusy = false; }
}
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
function mediaUrl(f) {
  if (f.local) return f.local;
  const s = String(f.src || "");
  if (s.startsWith("untis:")) return `${API}/untis/file?src=${encodeURIComponent(s)}${f.mime ? `&mime=${encodeURIComponent(f.mime)}` : ""}${f.name ? `&name=${encodeURIComponent(f.name)}` : ""}`;
  if (s.startsWith("ida:")) return `${API}/ida/file?src=${encodeURIComponent(s)}${f.mime ? `&mime=${encodeURIComponent(f.mime)}` : ""}${f.name ? `&name=${encodeURIComponent(f.name)}` : ""}`;
  return `${API}/media?src=${encodeURIComponent(s)}${f.mime ? `&mime=${encodeURIComponent(f.mime)}` : ""}`;
}
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
// … aber nur bei Snapchat und nur, solange es in den Einstellungen an ist. Alles andere (z. B. mehrere WhatsApp-Videos) bleibt einzeln bzw. wird ein Album.
const msgNet = (m) => { const c = cur(); return netKey((c?.merged ? c.merged.find((x) => x.id === (m.chat || target())) : c)?.network); };
const snapClip = (m) => PREFS.snap_join && vidOnly(m) && msgNet(m) === "snapchat";
// Album: mehrere Bilder und/oder Videos hintereinander vom selben Absender
const picOnly = (m) => m && !m.deleted && !m.pending && !m.text && !m.reply_to && m.files.length && m.files.every((f) => (f.kind === "image" || f.kind === "video") && !f.sticker && f.src) && !snapClip(m);
const ALBUMS = new Map();   // ID der ersten Nachricht → alle Dateien des Albums (für die Übersicht)
const thumbOf = (f) => f.kind === "image" ? mediaUrl(f) : f.poster ? mediaUrl({ src: f.poster }) : "";
function stackHtml(fs) {   // Tipp aufs oberste Bild/Video öffnet es; Tipp daneben (Rand, Zahl, Knopf) die Übersicht des Albums
  const top = fs[0], r = top.w && top.h ? ` style="aspect-ratio:${top.w}/${top.h}"` : "";
  const front = top.kind === "video"
    ? `<div class="vid sk-top"${r} data-a="video" data-src="${esc(top.src)}" data-mime="${esc(top.mime)}">${top.poster ? `<img class="poster" loading="lazy" alt="" src="${mediaUrl({ src: top.poster })}">` : ""}<span class="ms">play_arrow</span></div>`
    : `<img class="pic sk-top" loading="lazy" alt="Bild" data-a="zoom" data-src="${esc(top.src)}"${r} src="${mediaUrl(top)}">`;
  return `<div class="stackw" data-a="album"><div class="stack">${fs.slice(1, 3).reverse().map((f, i) => thumbOf(f) ? `<img class="sk sk${i}" loading="lazy" alt="" src="${thumbOf(f)}">` : `<i class="sk sk${i} skv"></i>`).join("")}
    ${front}<span class="skn">${fs.length}</span></div><button class="skgrid" type="button" aria-label="Alle ${fs.length} ansehen" title="Alle ansehen"><span class="ms">grid_view</span></button></div>`;
}
// Übersicht eines Albums: alle Bilder und Videos als Kacheln, mit eigener Adresse (…/album/<Nachricht>) – Zurück schließt sie
function albumView(id) {
  const fs = ALBUMS.get(id); if (!fs) return false;
  document.querySelector(".album")?.remove();
  const m = S.msgs.find((x) => x.id === id), who = m ? (m.mine ? "Du" : clean(m.sender) || clean(cur()?.title || "")) : "";
  const d = document.createElement("div"); d.className = "album";
  d.innerHTML = `<div class="al-top"><button class="icon-btn" data-al="close" aria-label="Zurück"><span class="ms">arrow_back</span></button><div class="lb-info"><b>${esc(who)}</b><small>${fs.length} Medien${m?.at ? ` · ${fmtDay.format(new Date(m.at))}, ${fmtTime.format(new Date(m.at))}` : ""}</small></div></div>
    <div class="al-grid">${fs.map((f, i) => `<button type="button" class="al-tile" data-i="${i}">${thumbOf(f) ? `<img loading="lazy" alt="" src="${thumbOf(f)}">` : ""}${f.kind === "video" ? `<span class="ms">play_arrow</span>${f.duration ? `<small>${mmss(f.duration)}</small>` : ""}` : ""}</button>`).join("")}</div>`;
  const close = (fromHistory) => { if (fromHistory !== true) ovClosed(); d.classList.add("out"); setTimeout(() => d.remove(), 180); };
  d.addEventListener("click", (e) => {
    if (e.target.closest("[data-al=close]")) return close();
    const t = e.target.closest(".al-tile"); if (!t) return;
    const f = fs[+t.dataset.i];
    d.remove();   // das Bild/Video übernimmt die Adresse; Zurück führt von dort in den Chat
    if (f.kind === "video") { t.dataset.src = f.src; t.dataset.mime = f.mime || ""; playVideo(t); } else gallery(f.src);
  });
  addEventListener("keydown", function k(e) { if (!d.isConnected) return removeEventListener("keydown", k); if (e.key === "Escape" && !document.querySelector(".lightbox, .vp")) close(); });
  document.body.append(d);
  ovOpen(`album/${encodeURIComponent(id)}`, () => close(true));
  return true;
}
// Gruppen: Name steht wie bei Signal frei über der ersten Blase einer Folge, jede Person mit eigener Farbe
const WHO_COLORS = ["#f28b82", "#fbbc04", "#81c995", "#78d9ec", "#8ab4f8", "#c58af9", "#ff8bcb", "#fcad70", "#a8dab5", "#d7aefb"];
const whoColor = (k) => { let h = 0; for (const ch of String(k || "")) h = (h * 31 + ch.charCodeAt(0)) | 0; return WHO_COLORS[Math.abs(h) % WHO_COLORS.length]; };
// Videos von Instagram, YouTube und TikTok direkt hier abspielen (Instagram direkt im Video-Player, sonst eingebetteter Player)
function embedOf(u) {
  let m;
  if ((m = /instagram\.com\/(?:[\w.]+\/)?(reels?|p|tv)\/([\w-]+)/i.exec(u || ""))) return { src: `https://www.instagram.com/${m[1] === "p" ? "p" : "reel"}/${m[2]}/embed/`, name: "Instagram", play: m[1] !== "p", videoUrl: m[1] !== "p" ? u : "" };
  if ((m = /(?:youtube\.com\/(?:watch\?(?:[^#]*&)?v=|shorts\/|embed\/|live\/)|youtu\.be\/)([\w-]{11})/i.exec(u || ""))) return { src: `https://www.youtube-nocookie.com/embed/${m[1]}?autoplay=1&playsinline=1&rel=0`, name: "YouTube", play: true };
  if ((m = /tiktok\.com\/@[\w.-]+\/video\/(\d+)/i.exec(u || ""))) return { src: `https://www.tiktok.com/embed/v2/${m[1]}`, name: "TikTok", play: true };
  return null;
}
const URL_RE = /https?:\/\/[^\s<>"')\]]+/g;
const lk = (u) => String(u || "").trim().toLowerCase().replace(/^https?:\/\/(www\.)?/, "").replace(/[?#].*$/, "").replace(/\/+$/, "");
// Besteht der Text nur aus einem Link (Instagram schickt ihn doppelt: „url (url?id=…)“)? → die Adresse, sonst ""
function soleLink(m) {
  const us = (m.text || "").match(URL_RE) || [];
  if (!us.length || (m.text || "").replace(URL_RE, "").replace(/[\s()]/g, "") || new Set(us.map(lk)).size !== 1) return "";
  return us.sort((a, b) => a.length - b.length)[0].replace(/[.,!?:;]+$/, "");
}
const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
function lpHtml(l, pic = null) {   // pic: Vorschaubild aus dem Anhang (schärfer als das der Webseite)
  const e = embedOf(l.url), img = pic ? mediaUrl(pic) : l.img ? (l.ext || mediaUrl({ src: l.img })) : "";
  const w = pic ? pic.w : l.w, h = pic ? pic.h : l.h, r = w && h ? ` style="aspect-ratio:${w}/${h}"` : "";
  const shot = img ? (e ? `<button type="button" class="lp-pic" data-a="${e.videoUrl ? "igvideo" : "embed"}" data-url="${esc(l.url)}"><img loading="lazy" alt=""${r} src="${img}"><span class="ms">${e.play ? "play_arrow" : "open_in_full"}</span></button>`
    : pic ? `<button type="button" class="lp-pic" data-a="zoom" data-src="${esc(pic.src)}"><img loading="lazy" alt=""${r} src="${img}"></button>`
    : `<a class="lp-pic" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer"><img loading="lazy" alt=""${r} src="${img}"></a>`) : "";
  const title = l.title || (e ? `${e.name}-${e.play ? "Video" : "Beitrag"}` : host(l.url));
  return `<div class="lp${pic ? " share" : ""}">${shot}<a class="lp-txt" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer"><b>${esc(title)}</b>${l.summary ? `<i>${esc(l.summary)}</i>` : ""}<small>${esc(host(l.url))}</small></a></div>`;
}
function bubble(m, tail = true, stack = null, head = true) {
  if (m.typing) return `<div class="bw" id="m-typing" data-id="typing"><div class="bcol"><div class="b typing" aria-label="Ida schreibt"><span class="dots"><i></i><i></i><i></i></span></div></div></div>`;
  // Geteilter Beitrag: nur der Link als Text und genau ein Bild (sein Vorschaubild) → eine Karte statt Bild + Link + zweitem Bild
  const sole = !m.deleted && soleLink(m);
  const share = sole && !stack && m.files.length === 1 && m.files[0].kind === "image" && !m.files[0].sticker && m.files[0].src && !m.pending ? m.files[0] : null;
  const hasVid = !m.deleted && m.files.some((f) => f.kind === "video");
  const hideSoleVid = !!(sole && hasVid && embedOf(sole));
  const visual = !share && !m.deleted && m.files.length && m.files.every((f) => (f.kind === "image" || f.kind === "video") && !f.sticker);
  const files = share ? "" : stack ? stackHtml(stack) : visual && !m.pending ? `<div class="mwrap"><div class="mcol">${m.files.map((f) => fileHtml(f, CLIPS.get(m.id) || null)).join("")}</div></div>` : m.files.map((f) => fileHtml(f)).join("");
  const prev = hideSoleVid ? [] : (m.links || []).filter((l) => l.title || l.img);
  const onlyLink = !!sole && (share || hideSoleVid || prev.some((l) => lk(l.url) === lk(sole)));
  const split = visual && !stack && ((m.text && !onlyLink) || prev.length > 0);   // Bild/Video und Text: zwei Blasen
  // Gelöscht: Hinweis – und darunter, was drinstand, wenn der Dienst die Nachricht vorher schon gesehen hatte
  const was = m.deleted && m.was ? `<div class="was">${(m.was.files || []).map((f) => fileHtml(f)).join("")}${m.was.text ? `<span class="tx">${autolink(esc(m.was.text))}</span>` : ""}</div>` : "";
  // Ungeöffneter Snap: Beeper liefert ihn ohne Bild und Text – statt einer leeren Blase ein Hinweis
  const snapPh = !m.deleted && !m.text && !m.files.length && !m.pending && (m.type === "IMAGE" || m.type === "VIDEO") && msgNet(m) === "snapchat"
    ? `<span class="snapph"><span class="ms">${m.type === "VIDEO" ? "videocam" : "photo_camera"}</span>${m.mine ? "Snap gesendet" : `Neuer Snap${m.type === "VIDEO" ? " (Video)" : ""} – öffnen in Snapchat`}</span>` : "";
  const body = snapPh ? snapPh : m.deleted ? `<span class="gone"><span class="ms">block</span>${m.mine ? "Du hast diese Nachricht gelöscht" : "Diese Nachricht wurde gelöscht"}</span>${was}` : m.text && !onlyLink ? `<span class="tx">${mentionHtml(autolink(esc(m.text)))}</span>` : "";
  const links = m.deleted ? "" : share ? lpHtml(prev.find((l) => lk(l.url) === lk(sole)) || { url: sole }, share) : prev.map((l) => lpHtml(l)).join("");
  const ikind = isIda(S.id) ? (m.ida_kind || (m.notice ? "notice" : "chat")) : "";
  const itag = ikind === "notice" ? `<span class="ikind-tag"><span class="ms">notifications</span>${esc(m.sender && m.sender !== "Ida" ? `Mitteilung · ${clean(m.sender)}` : "Mitteilung von Ida")}</span>`
    : ikind === "forward" ? `<span class="ikind-tag"><span class="ms">forward</span>${m.mine ? "Weitergeleitet an Ida" : "Antwort zur Weiterleitung"}</span>` : "";
  const fwdLoad = m.pendingFwd ? `<div class="fwd-wait"><span class="ms spin">progress_activity</span>Wird an Ida geschickt …</div>` : "";
  const opts = !m.mine && m.options?.length ? `<div class="ida-opts">${m.options.map((o) => `<button type="button" class="ida-opt" data-a="ida-opt" data-opt="${esc(o)}">${esc(o)}</button>`).join("")}</div>` : "";
  const group = "";
  const name = head && cur()?.type === "group" && !m.mine && clean(m.sender) ? `<div class="gname" style="color:${whoColor(m.sender_id || m.sender)}">${esc(clean(m.sender))}</div>` : "";
  const quote = "";
  const more = opts;
  const rx = m.reactions?.length ? `<div class="rx">${m.reactions.map((r) => `<span class="${r.mine ? "mine" : ""}" data-a="rx" data-id="${esc(m.id)}" data-k="${esc(r.key)}">${esc(r.key)}${r.n > 1 ? ` ${r.n}` : ""}</span>`).join("")}</div>` : "";
  const tools = m.pending || m.deleted ? "" : `<button class="rep" data-a="reply" data-id="${esc(m.id)}" aria-label="Antworten" title="Antworten"><span class="ms">reply</span></button>`;
  const face = cur()?.type === "group" && !m.mine ? (tail ? avatar(clean(m.sender), cid(m), m.sender_id) : '<span class="av gap"></span>') : "";
  const prog = m.pendingFile ? `<div class="prog"><i style="width:${Math.round((m.progress || 0) * 100)}%"></i></div>` : "";
  const mc = cur()?.merged, net = mc ? netKey((mc.find((x) => x.id === (m.chat || target())) || {}).network) : null;
  return `${name}${m.reply_to && !m.deleted ? replyHead(m, net) : ""}<div class="bw ${m.mine ? "me" : ""} ${face ? "grp" : ""} ${m.reply_to && !m.deleted ? "isrep" : ""}"${net !== null ? ` data-net="${net}"` : ""}${ikind ? ` data-ikind="${esc(ikind)}"` : ""} id="m-${esc(m.id)}" data-id="${esc(m.id)}"><span class="swipe ms">reply</span>${face}${tools}<div class="bcol">${quote}${split ? `<div class="b media ${m.mine ? "me" : ""}">${group}${files}</div>` : ""}<div class="b ${m.mine ? "me" : ""} ${share ? "sharebub" : ""} ${!m.mine && atMe(m) ? "atme" : ""} ${m.pending ? "pending" : ""} ${m.pendingFwd ? "pfwd" : ""} ${m.deleted ? "del" : ""} ${!split && visual && (!m.text || onlyLink) ? "media" : ""}"${ikind ? ` data-ikind="${esc(ikind)}"` : ""}>${itag}${split ? "" : group}${split ? "" : files}${prog}${body}${fwdLoad}${links}${rx}${tmLine(m)}</div>${more}</div><span class="st">${m.at && !m.pending ? fmtTime.format(new Date(m.at)) : ""}</span></div>`;
}
const NETS = [["ida", "ida"], ["untis", "untis"], ["snapchat", "snapchat"], ["instagram", "instagram"], ["whatsapp", "whatsapp"], ["signal", "signal"], ["telegram", "telegram"], ["discord", "discord"],
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
let olderObs = null, olderBusy = false;
const keepBusy = () => olderBusy || !!cascade;   // nicht während des Auftritts beim Öffnen (dann steht der Chat noch nicht unten)
let cascade = null;   // läuft gerade der Auftritt eines frisch geöffneten Chats? { id, t0 }
const CAS_N = 16, CAS_STEP = 22;
function drawMsgs({ stick = false, keepTop = false } = {}) {
  const box = $("#msgs"); if (!box) return;
  ogApply();
  const only = activeOnly(), L = only ? S.msgs.filter((m) => !m.chat || m.chat === only) : S.msgs;   // Filter oben rechts: nur ein Netzwerk eines zusammengeführten Chats
  const fresh = drawn.id === S.id && !keepTop ? L.slice(-6).filter((m) => !drawn.ids.has(m.id)) : [];
  const prevH = box.scrollHeight, prevTop = box.scrollTop;
  S.replies = new Map();
  for (const m of S.msgs) if (m.reply_to) S.replies.set(m.reply_to, [...(S.replies.get(m.reply_to) || []), m.id]);
  let last = "", html = S.more ? '<button class="older" id="older" data-a="older" aria-label="Ältere laden"><span class="ms">refresh</span></button>' : "";
  if (!L.length) html += `<div class="empty">${only ? `Keine Nachrichten über ${esc(netName((cur().merged.find((x) => x.id === only) || {}).network))} – oben rechts den Filter ändern.` : "Noch keine Nachrichten."}</div>`;
  const lastMine = [...L].reverse().find((m) => m.mine && !m.deleted && !m.pending)?.id;
  // Trennlinie über der ersten ungelesenen Nachricht (wie in Beeper), solange der Chat nicht als gelesen markiert ist
  let unFrom = -1;
  { const cc = cur(), nu = only ? 0 : left(cc); if (nu) { let k = 0; for (let x = L.length - 1; x >= 0; x--) { if (L[x].mine) { unFrom = x + 1; break; } unFrom = x; if (++k === nu) break; } if (unFrom >= L.length) unFrom = -1; } }
  for (let i = 0; i < L.length; i++) {
    const m = L[i];
    if (unFrom >= 0 && i >= unFrom) { html += `<div class="unsep" id="unsep"><span>${left(cur())} ungelesen</span></div>`; unFrom = -1; }
    const k = m.at ? dayKey(m.at) : last;
    if (k !== last) { last = k; html += `<div class="day">${esc(fmtDay.format(new Date(m.at)))}</div>`; }
    let j = i, files = null;   // Bilder-/Video-Folge vom selben Absender innerhalb von 3 Minuten → ein Stapel
    CLIPS.delete(m.id); ALBUMS.delete(m.id);
    if (snapClip(m)) {   // nur Snapchat: Stücke desselben Absenders (im zusammengeführten Chat: desselben Netzwerks) werden ein Video
      while (snapClip(L[j + 1]) && L[j + 1].sender_id === m.sender_id && L[j + 1].mine === m.mine && Date.parse(L[j + 1].at) - Date.parse(L[j].at) < 90000 && dayKey(L[j + 1].at) === k) j++;
      if (j > i) CLIPS.set(m.id, L.slice(i, j + 1).map((x) => x.files[0]));
    } else if (picOnly(m)) {
      while (picOnly(L[j + 1]) && L[j + 1].sender_id === m.sender_id && L[j + 1].mine === m.mine && Date.parse(L[j + 1].at) - Date.parse(L[j].at) < 180000 && dayKey(L[j + 1].at) === k) j++;
      files = L.slice(i, j + 1).flatMap((x) => x.files);
      if (files.length < 2) files = null; else ALBUMS.set(m.id, files);
    }
    const n = L[j + 1];   // Bild nur an der letzten Nachricht einer Folge vom selben Absender (wie in Beeper)
    const p = L[i - 1];   // Name nur an der ersten Nachricht einer Folge vom selben Absender
    const lm = L.slice(i, j + 1).some((x) => x.id === lastMine);
    html += bubble(m, !n || n.mine || n.sender_id !== m.sender_id || (n.at && dayKey(n.at) !== k), files, !p || p.mine || p.sender_id !== m.sender_id || (p.at && dayKey(p.at) !== k));
    if (lm) html += seenRow(L.find((x) => x.id === lastMine));
    i = j;
  }
  box.innerHTML = html;
  // Chat geht auf: die Nachrichten tauchen von unten nach oben nacheinander auf. Kommt währenddessen Frisches und der Verlauf wird
  // neu gezeichnet, läuft der Auftritt an derselben Stelle weiter (negative Verzögerung) statt neu zu starten oder zu springen.
  if (drawn.id !== S.id && L.length && !keepTop) cascade = { id: S.id, t0: performance.now() };
  drawn = { id: S.id, ids: new Set(L.map((m) => m.id)) };
  for (const m of fresh) document.getElementById(`m-${m.id}`)?.classList.add("in");
  if (cascade && cascade.id === S.id && performance.now() - cascade.t0 < CAS_N * CAS_STEP + 300) {
    const gone = performance.now() - cascade.t0;
    [...box.querySelectorAll(".bw, .day, .unsep, .seenrow")].slice(-CAS_N).reverse().forEach((el, i) => { if (!el.classList.contains("in")) { el.style.animationDelay = `${Math.round(i * CAS_STEP - gone)}ms`; el.classList.add("cin"); } });
  } else cascade = null;
  if (cascade) { const c0 = cascade; setTimeout(() => { if (cascade === c0) { cascade = null; const o = $("#older"); if (o && olderObs) { olderObs.unobserve(o); olderObs.observe(o); } } }, CAS_N * CAS_STEP + 320); }
  $("#conv").dataset.net = cur()?.merged ? "" : netKey(cur()?.network);   // eigene Nachrichten in Netzwerkfarbe, eingehende mit Akzenten in Netzwerkfarbe
  paint();
  if (keepTop) box.scrollTop = box.scrollHeight - prevH + prevTop;
  else if (stick) { atBottom = true; box.scrollTop = box.scrollHeight; requestAnimationFrame(pinEnd); }
  else box.scrollTop = prevTop;
  growth.disconnect(); growth.observe(box); for (const c of box.children) growth.observe(c);
  olderObs?.disconnect(); olderObs = null;
  if ($("#older")) { olderObs = new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting) && !keepBusy()) loadMsgs({ older: true }); }, { root: box, rootMargin: "400px 0px 0px 0px" }); olderObs.observe($("#older")); }
  if (!box.dataset.pin) { box.dataset.pin = 1; for (const ev of ["load", "loadedmetadata", "error"]) box.addEventListener(ev, pinEnd, true); }
  ogFetch();
}
// Link-Vorschau selbst holen, wenn Beeper keine mitliefert (z. B. bei Snapchat): Bild, Titel, Text der Seite.
// Einmal pro Adresse, gemerkt für die Sitzung; nur die letzten 40 Nachrichten, damit beim Hochscrollen nicht alles auf einmal lädt.
const OG = new Map();
const urlOf = (m) => !m || m.deleted || (m.links || []).some((l) => l.title || l.img) ? "" : soleLink(m) || ((m.text || "").match(/https?:\/\/[^\s<>"')\]]+/) || [""])[0].replace(/[.,!?:;]+$/, "");
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
  if (a.dataset.a === "filter") return filterMenu();
  if (a.dataset.a === "older") return loadMsgs({ older: true });
  if (a.dataset.a === "reply") return setReply(S.msgs.find((m) => m.id === a.dataset.id));
  if (a.dataset.a === "ida-opt") {
    const m = bubbleMsg(a); if (m) m.options = [];
    return send(a.dataset.opt);
  }
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
  if (a.dataset.a === "igvideo") return playVideo(a);
  if (a.dataset.a === "embed") return playEmbed(a);
  if (a.dataset.a === "speed") return cycleSpeed(a);
  if (a.dataset.a === "seek") return seek(a.closest(".voice"), e);
  if (a.dataset.a === "vtext") return voiceText(a);
  if (a.dataset.a === "album") { const m = bubbleMsg(a); return m && albumView(m.id); }
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
    audio.src = mediaUrl({ src, mime: v.dataset.mime || "" });
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
  const m = bubbleMsg(el), mime = el.dataset.mime || "video/mp4", igUrl = el.dataset.url || "";
  // Abspielliste: bei zusammengefügten Snapchat-Stücken alle, sonst nur das eine Video
  const clips = igUrl ? [{ url: igUrl, duration: 0 }] : (m && CLIPS.get(m.id)) || [{ src: el.dataset.src, mime: el.dataset.mime, duration: 0 }];
  const urlOf = (c) => c.url ? `${API}/video?url=${encodeURIComponent(c.url)}` : mediaUrl(c);
  const dur = clips.map((c) => c.duration || 0);
  const kindName = igUrl ? "Instagram Video" : `${(mime.split("/")[1] || "mp4").split(";")[0].replace("quicktime", "mov").toUpperCase()} Video`;
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
    v.addEventListener("error", () => {
      if (v !== act) return;
      if (igUrl) { vA.removeAttribute("src"); d.remove(); playEmbed(el); return; }
      toast("Video konnte nicht geladen werden");
    });
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
// Geteiltes Video (Instagram, YouTube, TikTok) im eingebetteten Player der Seite; oben rechts öffnet es in deren App
function playEmbed(el) {
  const e = embedOf(el.dataset.url); if (!e) return;
  document.querySelector(".vp")?.remove();
  const m = bubbleMsg(el), d = document.createElement("div");
  d.className = "vp emb";
  d.innerHTML = `<div class="vp-top"><button class="icon-btn" data-v="close" aria-label="Zurück"><span class="ms">arrow_back</span></button><div><b>${esc(e.name)}</b><small>${m && !m.mine && m.sender ? esc(clean(m.sender)) : esc(host(el.dataset.url))}</small></div>
    <a class="icon-btn" href="${esc(el.dataset.url)}" target="_blank" rel="noopener noreferrer" aria-label="In ${esc(e.name)} öffnen" title="In ${esc(e.name)} öffnen"><span class="ms">open_in_new</span></a></div>
    <div class="vp-stage emb-stage"><div class="emb-wait"><span class="ms">refresh</span></div><iframe src="${esc(e.src)}" allow="autoplay; encrypted-media; fullscreen; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe></div>`;
  d.querySelector("iframe").addEventListener("load", () => d.querySelector(".emb-wait")?.remove(), { once: true });
  const close = (fromHistory) => { if (fromHistory !== true) ovClosed(); d.classList.add("out"); setTimeout(() => d.remove(), 180); };
  d.addEventListener("click", (ev) => { if (ev.target.closest("[data-v=close]")) close(); });
  addEventListener("keydown", function k(ev) { if (!d.isConnected) return removeEventListener("keydown", k); if (ev.key === "Escape") close(); });
  document.body.append(d);
  ovOpen(m ? `video/${encodeURIComponent(m.id)}` : "bild", () => close(true));
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
  if (matchMedia("(pointer: coarse)").matches) getSelection()?.removeAllRanges();   // langes Drücken: keine Markierung stehen lassen
  const src = document.getElementById(`m-${m.id}`)?.querySelector(".b");
  if (!src) return;
  const r = src.getBoundingClientRect(), mine = new Set((m.reactions || []).filter((x) => x.mine).map((x) => x.key));
  const d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  const emo = (k) => `<button class="emo ${mine.has(k) ? "on" : ""}" data-r="${esc(k)}" aria-label="${esc(k)}">${esc(k)}</button>`;
  if (m.typing) return;
  if (ownChat(S.id)) {   // Ida / WebUntis: keine Reaktionen, kein Bearbeiten oder Löschen; bei Untis Antworten (auf genau diese Nachricht), Weiterleiten und An Ida schicken
    const un = isUntis(S.id) && !m.pending;
    d.innerHTML = `<div class="ctx-back"></div><div class="ctx-stack ${m.mine ? "me" : ""}"><div class="ctx-msg"></div><div class="ctx-box">
      ${un && !m.mine && m.can_reply !== false ? '<button data-c="reply"><span class="ms">reply</span>Antworten</button>' : ""}
      ${m.text ? '<button data-c="copy"><span class="ms">content_copy</span>Text kopieren</button>' : ""}
      ${!m.pending && (m.text || m.files.length) ? '<button data-c="fwd"><span class="ms">forward</span>Weiterleiten</button>' : ""}
      ${un && IDA ? '<button data-c="ida"><span class="ms">auto_awesome</span>An Ida schicken</button>' : ""}
      ${m.files[0] ? '<button data-c="open"><span class="ms">open_in_new</span>Datei öffnen</button>' : ""}</div></div>`;
  } else d.innerHTML = `<div class="ctx-back"></div><div class="ctx-stack ${m.mine ? "me" : ""}">
    ${m.pending || m.deleted ? "" : `<div class="ctx-rx">${QUICK.map(emo).join("")}<button class="emo plus" data-c="more" aria-label="Mehr"><span class="ms">add</span></button></div>
    <div class="ctx-more" hidden>${MORE.map(emo).join("")}</div>`}
    <div class="ctx-msg"></div>
    <div class="ctx-box">
      <button data-c="reply"><span class="ms">reply</span>Antworten</button>
      ${m.pending ? "" : '<button data-c="info"><span class="ms">info</span>Info</button>'}
      ${m.text ? '<button data-c="copy"><span class="ms">content_copy</span>Text kopieren</button>' : ""}
      ${m.mine && m.text && !m.files.length && !m.pending && !m.deleted ? '<button data-c="edit"><span class="ms">edit</span>Bearbeiten</button>' : ""}
      ${!m.pending && !m.deleted && (m.text || m.files.length) ? '<button data-c="fwd"><span class="ms">forward</span>Weiterleiten</button>' : ""}
      ${IDA && !m.pending && (m.text || m.files.length || m.was) ? '<button data-c="ida"><span class="ms">auto_awesome</span>An Ida schicken</button>' : ""}
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
    if (c === "ida") toIda(m);
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
    // feste Höhe statt max-height: natürliche Höhe der Blase, höchstens so viel wie frei ist (nichts kann sie mehr zusammendrücken)
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, Math.max(160, innerHeight - 24 - sat - rest))}px`;
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
// „An Ida schicken“: sofort in den Ida-Chat wechseln, Platzhalter mit Lade-Animation zeigen, dann Nachricht + Verlauf schicken (chats/app/ida.py → /ida/forward)
function lineOf(x) {
  const f = x.deleted ? x.was : x, files = (f?.files || []).map((a) => a.transcript ? `[Sprachnachricht: ${a.transcript}]` : `[${{ image: "Bild", video: "Video", audio: "Audio" }[a.kind] || "Datei"}${a.name ? ` ${a.name}` : ""}]`);
  const links = (x.links || []).filter((l) => l.title).map((l) => `[Link: ${l.title}]`);
  return { who: clean(x.sender), mine: !!x.mine, at: x.at || "", text: [x.deleted ? "(gelöscht)" : "", f?.text || "", ...files, ...links].filter(Boolean).join(" ").slice(0, 3900) };
}
async function toIda(m) {
  const c = cur(), src = m.chat ? known(m.chat) || { id: m.chat } : c, i = S.msgs.indexOf(m);
  const before = S.msgs.slice(Math.max(0, i - 8), Math.max(0, i)).filter((x) => !x.pending && !x.typing && (!m.chat || x.chat === m.chat)).map(lineOf);
  const msg = lineOf(m), chatTitle = clean(c?.title || src?.title || ""), net = netName(src?.network || c?.network);
  const body = { chat_id: m.chat || cid(m), title: chatTitle, network: net, group: c?.type === "group", message: msg, before };
  const headTxt = `↪ Aus dem Chat „${chatTitle || "Chat"}“${net ? ` (${net}` + (c?.type === "group" ? ", Gruppe" : "") + ")" : ""}`;
  const whoTxt = msg.mine ? "Ich" : msg.who || chatTitle || "Jemand";
  const tmp = { id: `tmp-fwd-${Date.now()}`, text: `${headTxt} – ${whoTxt}:\n\n${msg.text || "(ohne Text)"}`, mine: true, pending: true, pendingFwd: true, ida_kind: "forward", at: new Date().toISOString(), files: [], type: "TEXT", sender: "" };
  go("ida");
  if (!S.msgs.some((x) => x.id === tmp.id)) { S.msgs.push(tmp); drawMsgs({ stick: true }); }
  try {
    await api("/ida/forward", { method: "POST", body: JSON.stringify(body) });
    tmp.pendingFwd = false;
    loadMsgs({ quiet: true }); loadIda();
  } catch (e) {
    S.msgs = S.msgs.filter((x) => x !== tmp);
    if (S.id === "ida") drawMsgs();
    toast(`Ging nicht an Ida: ${e.message}`);
  }
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
  if (!await sure(everyone ? "Für alle löschen?" : "Nur bei dir löschen?", everyone ? "Das lässt sich nicht rückgängig machen." : "", "Löschen", true)) return;
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
// Jedes Menü/Sheet (#ctx, z. B. das Long-Press-Menü) bekommt eine eigene Adresse (…/menue): die Zurück-Taste am Handy schließt es.
// Ein Beobachter erledigt das für alle Menüs an einer Stelle; ersetzt ein Menü direkt das nächste, bleibt es bei einem Eintrag.
const MENU = "menue";
const readMenu = () => /(?:^|\/)menue$/.test(location.pathname.slice(BASE.length));
const menuBase = () => { const p = location.pathname.replace(/menue$/, ""); return p === BASE ? p : p.replace(/\/$/, ""); };
let menuOn = false;
new MutationObserver(() => {
  const on = !!document.getElementById("ctx");
  if (on === menuOn) return;
  menuOn = on;
  if (on) { if (!readMenu()) { const b = location.pathname; history.pushState({ ...(history.state || {}), ctx: true }, "", `${b}${b.endsWith("/") ? "" : "/"}${MENU}`); } }
  else if (readMenu()) { if (history.state?.ctx) history.back(); else history.replaceState(history.state, "", menuBase()); }
}).observe(document.body, { childList: true });
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
const pickAv = (c) => netAv({ title: c.title, id: c.av || c.id, network: c.network, merged: c.merged });
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
  const base = () => {
    const raw = [...PINS.map((p) => known(p.id) || p), ...S.chats, ...extraRows(), ...UNTIS.teachers];
    const seenIds = new Set(), uniq = [];
    for (const c of raw) if (c && !seenIds.has(c.id)) { seenIds.add(c.id); uniq.push(c); }
    return keep(uniq);
  };
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
      try {
        const r = await api(`/chats?${new URLSearchParams({ q, inbox: "" })}`);
        if (n === seq && $("#ctx") === d) {
          const loc = base().filter((c) => clean(c.title).toLowerCase().includes(lq));
          const ids = new Set(r.items.map((x) => x.id));
          items = keep([...r.items, ...loc.filter((x) => !ids.has(x.id))]);
          draw();
        }
      } catch { /* dann bleibt der lokale Treffer */ }
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
    const c = cur(); if (c) { c.at = new Date().toISOString(); c.unread = 0; unSeen(c); refreshReadBar(); drawList(); }
    loadChats({ quiet: true });
  } catch (e) {
    S.msgs = S.msgs.filter((m) => m !== tmp); drawMsgs(); ta.value = text;
    setReply(rep); toast(`Nicht gesendet: ${e.message}`);
  }
  S.sending = false; $("#sendBtn").disabled = false; ta.focus();
}
function sendMode() {   // leeres Feld: Mikrofon (wie in Beeper), sonst Senden
  const b = $("#sendBtn"), ta = $("#txt"); if (!b || !ta) return;
  const txt = !!ta.value.trim() || ownChat(S.id);   // Ida: immer Senden (keine Sprachnachrichten)
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
  loadMutes(); loadOrg(); live(true); checkVersion();
});
// Nach einem Update: ein offener Tab hätte sonst noch den alten Code im Speicher. Beim Zurückholen (höchstens einmal pro Minute)
// kurz die Version abfragen und neu laden, wenn sie sich geändert hat und gerade nichts getippt wird.
const MYVER = /\/s\/([^/]+)\//.exec(import.meta.url)?.[1];
let verAt = 0;
async function checkVersion() {
  if (!MYVER || Date.now() - verAt < 60000) return;
  verAt = Date.now();
  try {
    const { v } = await (await fetch("/version", { cache: "no-store" })).json();
    if (v && v !== MYVER && !$("#txt")?.value && !document.querySelector(".ctx")) location.reload();
  } catch {}
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});

// ---------------- Benachrichtigungen aufs Handy (Web Push) ----------------
// Die Glocke oben meldet dieses Gerät an. Danach weckt der Server das Handy nur, wenn eine Nachricht kommt – die App selbst
// läuft dafür nicht (kein Timer, keine offene Verbindung), das kostet praktisch keinen Akku.
// In der Android-App (android/, docs/CHATS-ANDROID.md) gibt es kein Web Push: dort meldet sich das Gerät über die App bei Googles FCM an.
// Die App stellt dafür window.IDAChatsNative bereit; die Firebase-Verbindungsdaten holt sie sich hier vom Dienst (nichts davon steckt in der APK).
const NATIVE = window.IDAChatsNative || null;
const FCM_KEY = "chats-fcm-token";
const canPush = !!NATIVE || ("serviceWorker" in navigator && "PushManager" in window && "Notification" in window);
async function nativePushOn(quiet = false) {
  try { NATIVE.pushSetup(JSON.stringify(await api("/fcm/client"))); }   // Antwort kommt als Ereignis "ida-fcm"
  catch (e) { if (!quiet) toast(e.status === 404 ? "Firebase ist noch nicht eingerichtet – unten unter „Android-App“ die beiden Dateien hochladen" : `Anmelden ging nicht: ${e.message}`); }
}
addEventListener("ida-fcm", async (e) => {
  const { token, error } = e.detail || {};
  if (error || !token) { toast(`Benachrichtigungen gingen nicht an: ${error || "kein Token"}`); return pushState(true); }
  try {
    const was = localStorage.getItem(FCM_KEY);
    if (was && was !== token) api("/fcm/unregister", { method: "POST", body: JSON.stringify({ token: was }) }).catch(() => {});
    await api("/fcm/register", { method: "POST", body: JSON.stringify({ token }) });
    localStorage.setItem(FCM_KEY, token);
    if (was !== token) toast("Benachrichtigungen an");
  } catch (err) { toast(`Anmelden ging nicht: ${err.message}`); }
  pushState(true); loadFcm();
});
addEventListener("ida-open", (e) => { if (e.detail?.chat) go(e.detail.chat); });   // Tipp auf eine Meldung der Android-App
const b64key = (k) => { const s = atob((k + "=".repeat((4 - k.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from(s, (c) => c.charCodeAt(0)); };
async function pushSub() { try { return await (await navigator.serviceWorker.ready).pushManager.getSubscription(); } catch { return null; } }
async function pushOn() {
  if (NATIVE) return nativePushOn();
  if (Notification.permission === "denied") return toast("Benachrichtigungen sind blockiert – in den Browser-Einstellungen für diese Seite erlauben");
  if (await Notification.requestPermission() !== "granted") return toast("Ohne Erlaubnis keine Benachrichtigungen");
  try {
    const { key } = await api("/push");
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription() || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64key(key) });
    await api("/push/subscribe", { method: "POST", body: JSON.stringify(sub.toJSON()) });
    toast("Benachrichtigungen an");
  } catch (e) { toast(`Anmelden ging nicht: ${e.message}`); }
  pushState(true);
}
async function pushOff() {
  if (NATIVE) {
    const token = localStorage.getItem(FCM_KEY);
    if (token) await api("/fcm/unregister", { method: "POST", body: JSON.stringify({ token }) }).catch(() => {});
    localStorage.removeItem(FCM_KEY); NATIVE.pushOff();
    toast("Benachrichtigungen aus"); loadFcm(); return pushState(true);
  }
  const sub = await pushSub();
  if (sub) { await api("/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint: sub.endpoint }) }).catch(() => {}); await sub.unsubscribe().catch(() => {}); }
  toast("Benachrichtigungen aus"); pushState(true);
}
// Stand auf diesem Gerät: "on" | "off" | "denied" | "none" (Browser kann kein Push). Beim Start einmal: Abo beim Server frisch halten.
let PUSH = canPush ? "off" : "none";
async function pushState(redraw) {
  if (NATIVE) { const st = NATIVE.pushState(); PUSH = st === "on" && !localStorage.getItem(FCM_KEY) ? "off" : st; }
  else if (canPush) PUSH = Notification.permission === "denied" ? "denied" : Notification.permission === "granted" && await pushSub() ? "on" : "off";
  if (redraw) redrawSett();
  return PUSH;
}
pushState().then((st) => { if (NATIVE) { if (st === "on") nativePushOn(true); return; } if (st === "on") pushSub().then((s) => s && api("/push/subscribe", { method: "POST", body: JSON.stringify(s.toJSON()) }).catch(() => {})); });
async function pushTest() {
  if (NATIVE) {
    try { await api("/fcm/test", { method: "POST", body: JSON.stringify({ token: localStorage.getItem(FCM_KEY) || "" }) }); toast("Probemeldung ist unterwegs"); }
    catch (e) { toast(`Ging nicht: ${e.message}`); }
    return;
  }
  try { (await navigator.serviceWorker.ready).showNotification("Chats", { body: "So sieht eine Benachrichtigung aus.", icon: "/icons/icon-192.png", badge: "/icons/badge-96.png", tag: "test" }); }
  catch (e) { toast(`Ging nicht: ${e.message}`); }
}

// ---------------- Einstellungen (Zahnrad in der Leiste unten, eigene Adresse …/einstellungen) ----------------
const readSett = () => /^einstellungen(?:\/menue)?$/.test(location.pathname.slice(BASE.length));
function openSett(push = true) {
  if ($("#sett")) return;
  if (push && !readSett()) pushView({ sett: true }, "", `${BASE}einstellungen`);
  const el = document.createElement("div"); el.className = "info sett"; el.id = "sett";
  $(".side").append(el); document.body.classList.add("setts");
  redrawSett(); pushState(true); loadFcm(); loadSugg(); loadUntisStatus();
}
function dropSett() {
  const el = $("#sett"); if (!el) return;
  document.body.classList.remove("setts");
  el.id = ""; el.classList.add("out"); setTimeout(() => el.remove(), 200);
}
function closeSett() {
  if (readSett()) { if (history.state?.sett) history.back(); else history.replaceState(null, "", BASE); }
  dropSett();
}
const chatOf = (id) => rowChat(id) || { id, title: "Chat" };
function redrawSett() {
  const el = $("#sett"); if (!el) return;
  const row = (icon, main, small = "", attrs = "", end = "") => `<button class="irow" ${attrs}><span class="ms">${icon}</span><span class="it"><b>${main}</b>${small ? `<small>${small}</small>` : ""}</span>${end}</button>`;
  const person = (c, small, attrs) => `<button class="irow iperson" ${attrs}>${avatar(clean(c.title || "Chat"), c.av || c.id)}<span class="it"><b>${esc(clean(c.title || "Chat"))}</b><small>${esc(small)}</small></span></button>`;
  const muted = [...MUTES.keys()].filter(isMuted).map(chatOf);
  const auto = [...AUTOREAD.keys()].map(chatOf);
  const pushTxt = { on: "An – neue Nachrichten melden sich auf diesem Gerät", off: "Aus auf diesem Gerät", denied: NATIVE ? "In Android blockiert – in den App-Einstellungen Benachrichtigungen erlauben" : "Im Browser blockiert – dort für diese Seite erlauben", none: "Dieser Browser kann keine Benachrichtigungen" }[PUSH];
  el.innerHTML = `<div class="ihead"><button class="icon-btn" data-s="close" aria-label="Zurück"><span class="ms">arrow_back</span></button><b>Einstellungen</b></div>
  <div class="ibody">
    <div class="icard"><small>Benachrichtigungen</small>
      ${row(PUSH === "on" ? "notifications_active" : "notifications_off", "Benachrichtigungen", pushTxt, `data-s="push"${PUSH === "none" ? " disabled" : ""}`, `<span class="sw${PUSH === "on" ? " on" : ""}" aria-hidden="true"></span>`)}
      ${PUSH === "on" ? row("science", "Probemeldung", "Zeigt eine Benachrichtigung auf diesem Gerät", 'data-s="test"') : ""}
      <p class="ihint">Kostet keinen Akku: Der Server weckt das Handy nur, wenn eine Nachricht kommt.</p></div>
    <div class="icard"><small>WebUntis</small>
      ${UNTIS.on ? row("school", `Verbunden als ${esc(UNTIS.user || "")}`, esc([UNTIS.school, UNTIS.error ? `Gerade nicht erreichbar: ${UNTIS.error}` : `${UNTIS.items.length} ${UNTIS.items.length === 1 ? "Chat" : "Chats"}, ${UNTIS.teachers.length} Lehrkräfte`].filter(Boolean).join(" · ")), 'data-s="untis"')
        + row("logout", "Abmelden", "Die Lehrer-Chats verschwinden aus der Liste", 'data-s="untisoff"')
        : row("login", "Mit WebUntis anmelden", "Nachrichten deiner Lehrkräfte als Chats – lesen, antworten, neu schreiben", 'data-s="untis"')}
      <p class="ihint">Pro Lehrkraft ein Chat. Schreiben antwortet auf ihre letzte Nachricht, gibt es keine, geht eine neue an sie (Betreff = Anfang des Textes). Lehrkräfte ohne Nachrichten findest du über die Suche. Die Anmeldedaten bleiben auf deinem Server.</p></div>
    <div class="icard"><small>Android-App</small>
      ${row(FCM.client ? "check_circle" : "upload_file", "google-services.json", FCM.client ? `Hochgeladen – Projekt ${esc(FCM.project)}${FCM.package ? `, App ${esc(FCM.package)}` : ""}` : "Aus Firebase: App hinzufügen → Datei herunterladen", 'data-s="fcmfile" data-k="client"')}
      ${row(FCM.account ? "check_circle" : "key", "Schlüssel des Dienstkontos", FCM.account ? `Hochgeladen – Projekt ${esc(FCM.account_project)}` : "Aus Firebase: Projekteinstellungen → Dienstkonten → Neuen privaten Schlüssel generieren", 'data-s="fcmfile" data-k="account"')}
      ${FCM.client || FCM.account ? row("delete", "Firebase-Dateien entfernen", `${FCM.devices} ${FCM.devices === 1 ? "Gerät" : "Geräte"} angemeldet – werden mit abgemeldet`, 'data-s="fcmdel"') : ""}
      <p class="ihint">${FCM.client && FCM.account && FCM.project !== FCM.account_project ? "Achtung: Die beiden Dateien gehören zu verschiedenen Firebase-Projekten. " : ""}Für Benachrichtigungen in der Android-App. Beide Dateien bleiben auf deinem Server – sie stehen weder im Repo noch in der APK, der Schlüssel wird nie wieder ausgegeben.</p></div>
    <div class="icard"><small>Snapchat</small>
      ${row("photo_camera", "Snaps nicht als neue Nachricht", PREFS.snaps_quiet ? "An – nur Textnachrichten melden sich und schieben den Chat nach oben" : "Aus – jeder Snap zählt wie eine Nachricht", 'data-s="snaps"', `<span class="sw${PREFS.snaps_quiet ? " on" : ""}" aria-hidden="true"></span>`)}
      ${row("movie", "Video-Stücke zusammenfügen", PREFS.snap_join ? "An – Snapchat teilt lange Videos in Stücke, hier werden sie ein Video" : "Aus – jedes Stück steht einzeln da", 'data-s="snapjoin"', `<span class="sw${PREFS.snap_join ? " on" : ""}" aria-hidden="true"></span>`)}
      <p class="ihint">Gilt auf allen Geräten. Snaps siehst du weiter: im Chat und in der Liste als kleines gelbes Kamera-Zeichen, nur ohne Meldung und ohne Hochrutschen.</p></div>
    <div class="icard"><small>Filter im Chat</small>
      ${row("filter_list", "Filter merken", filterKeep() ? "An – der Filter bleibt pro Chat, bis du ihn änderst" : "Aus – der Filter springt auf „Alle Apps“ zurück, sobald du den Chat verlässt", 'data-s="fkeep"', `<span class="sw${filterKeep() ? " on" : ""}" aria-hidden="true"></span>`)}
      <p class="ihint">Den Filter findest du oben rechts in zusammengeführten Chats: Er zeigt dort nur die Nachrichten einer App, zum Beispiel nur WhatsApp. Geschrieben wird dann auch darüber. Gilt auf diesem Gerät.</p></div>
    <div class="icard"><small>Startseite</small>
      ${nets().map((n) => row(ORG.nets[n] ? "visibility_off" : "visibility", `${esc(netName(n))}-Chats`, VIS_TEXT[ORG.nets[n] || ""], `data-s="netvis" data-net="${n}"`)).join("")}
      <p class="ihint">Ausgeblendete Chats findest du über den Filter „Ausgeblendet“ und die Suche. Zusammengeführte Chats bleiben immer auf der Startseite – darin schreibst du weiter auch über ein ausgeblendetes Netzwerk. Benachrichtigungen ändert das nicht, dafür gibt es Stummschalten.</p></div>
    ${ORG.chats.length ? `<div class="icard"><small>Eigene Einstellung pro Chat</small>${ORG.chats.map((x) => person(chatOf(x.id).title === "Chat" ? x : chatOf(x.id), VIS_TEXT[x.mode], `data-s="vis" data-id="${esc(x.id)}"`)).join("")}</div>` : ""}
    <div class="icard"><small>Zusammenführen – Vorschläge</small>
      ${SUGG === null ? '<p class="ihint">Suche …</p>' : SUGG.length ? SUGG.map((sg, i) => `<button class="irow iperson sugg" data-s="sugg" data-n="${i}"><span class="savs">${sg.chats.slice(0, 3).map((x) => netAv(x)).join("")}</span>
        <span class="it"><b>${esc(sg.chats.map((x) => clean(x.title)).filter((t, j, l) => l.indexOf(t) === j).join(" · "))}</b><small>${esc(`${sg.reason} · ${sg.chats.map((x) => netName(x.network)).join(", ")}`)}</small></span><span class="ms">chevron_right</span></button>`).join("")
        : '<p class="ihint">Gerade nichts – dieselbe Person in mehreren Apps findet sich hier, sobald es passt.</p>'}
      <p class="ihint">Nach Namen über verschiedene Apps hinweg. Antippen zum Prüfen: abwählen, weitere dazunehmen, Namen geben und speichern.</p></div>
    <div class="icard"><small>Listen</small>
      ${ORG.labels.map((l) => row("label", esc(l.name), `${l.chats.length} ${l.chats.length === 1 ? "Chat" : "Chats"}`, `data-s="label" data-l="${l.id}"`)).join("")}
      ${row("new_label", "Neue Liste", "Zum Beispiel „Tagesmutter“ – erscheint im Filter unten links", 'data-s="labelnew"')}</div>
    <div class="icard"><small>Stummgeschaltet</small>
      ${muted.length ? muted.map((c) => person(c, muteText(c.id), `data-s="mute" data-id="${esc(c.id)}"`)).join("") : '<p class="ihint">Kein Chat stumm. Stummschalten: Chat öffnen, oben auf den Namen tippen.</p>'}</div>
    ${auto.length ? `<div class="icard"><small>Automatisch als gelesen</small>${auto.map((c) => person(c, AR_TEXT[arOf(c.id)], `data-s="auto" data-id="${esc(c.id)}"`)).join("")}</div>` : ""}
  </div>`;
}
document.addEventListener("click", (e) => {
  const t = e.target.closest("#sett [data-s]"); if (!t) return;
  const a = t.dataset.s, c = t.dataset.id && chatOf(t.dataset.id);
  if (a === "close") return closeSett();
  if (a === "push") return PUSH === "on" ? pushOff() : pushOn();
  if (a === "test") return pushTest();
  if (a === "snaps") return setPref("snaps_quiet", !PREFS.snaps_quiet);
  if (a === "fcmfile") return fcmUpload(t.dataset.k);
  if (a === "fcmdel") return fcmRemove();
  if (a === "snapjoin") return setPref("snap_join", !PREFS.snap_join);
  if (a === "fkeep") return setFilterKeep(!filterKeep());
  if (a === "untis") return untisLogin();
  if (a === "untisoff") return sure("Von WebUntis abmelden?", "Die Lehrer-Chats verschwinden aus der Liste. In WebUntis selbst bleibt alles.", "Abmelden", true).then((ok) => ok && untisOff());
  if (a === "mute") return muteMenu(c);
  if (a === "auto") return autoReadMenu(c);
  if (a === "netvis") return netVisMenu(t.dataset.net);
  if (a === "vis") return visMenu(c);
  if (a === "label") return labelMenu(ORG.labels.find((l) => l.id === t.dataset.l));
  if (a === "labelnew") return labelNew();
  if (a === "sugg") { const sg = SUGG?.[+t.dataset.n]; if (sg) suggSheet(sg); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#sett") && !$("#ctx")) closeSett(); });
// Tipp auf eine Meldung bei offener App: den Chat öffnen
navigator.serviceWorker?.addEventListener("message", (e) => {
  if (e.data?.type !== "open") return;
  const m = /^\/chat\/([^/]+)/.exec(e.data.url || ""); go(m ? decodeURIComponent(m[1]) : null);
});
// Chat geöffnet: seine Meldungen in der Leiste wegräumen
// In der Android-App über die Brücke; gelesen auf einem anderen Gerät oder in Beeper nimmt der Server sie dort weg (push.py → dismiss).
async function clearNotes(id) {
  try { window.IDAChatsNative?.dismiss?.(id); } catch { /* ältere App */ }
  try { for (const n of await (await navigator.serviceWorker.ready).getNotifications({ tag: `chat-${id}` })) n.close(); } catch { /* egal */ }
}

// Firebase-Dateien für die Android-App: Stand anzeigen, hochladen, entfernen
let FCM = { client: false, account: false, project: "", account_project: "", package: "", devices: 0 };
async function loadFcm() { try { FCM = await api("/fcm"); redrawSett(); } catch { /* bleibt */ } }
function fcmUpload(kind) {
  const inp = document.createElement("input"); inp.type = "file"; inp.accept = "application/json,.json";
  inp.onchange = async () => {
    const f = inp.files?.[0]; if (!f) return;
    if (f.size > 20000) return toast("Die Datei ist zu groß – das ist nicht die richtige");
    try {
      FCM = await api(`/fcm/${kind}`, { method: "PUT", body: await f.text() }); redrawSett();
      toast(kind === "client" ? "google-services.json gespeichert" : "Schlüssel gespeichert");
      if (NATIVE && FCM.client && PUSH === "on") nativePushOn(true);
    } catch (e) { toast(`Ging nicht: ${e.message}`); }
  };
  inp.click();
}
async function fcmRemove() {
  try { FCM = await api("/fcm", { method: "DELETE" }); localStorage.removeItem(FCM_KEY); NATIVE?.pushOff(); redrawSett(); pushState(true); toast("Firebase-Dateien entfernt"); }
  catch (e) { toast(`Ging nicht: ${e.message}`); }
}
// WebUntis: Schule suchen, Benutzername und Passwort – der Server prüft die Anmeldung sofort (chats/app/untis.py)
async function loadUntisStatus() { try { const d = await api("/untis"); UNTIS = { ...UNTIS, ...d }; saveUntis(); redrawSett(); } catch { /* bleibt */ } }
function untisLogin() {
  closeMenu();
  const d = document.createElement("div"); d.className = "ctx"; d.id = "ctx";
  let pick = UNTIS.on ? { name: UNTIS.school, school: "", server: UNTIS.server } : null, qT;
  d.innerHTML = `<div class="ctx-back"></div><form class="sheet namesheet untissheet"><b>Mit WebUntis anmelden</b>
    <label class="search"><span class="ms">search</span><input name="q" placeholder="Schule suchen (Name oder Ort)" autocomplete="off" enterkeyhint="search"></label>
    <div class="uschools" id="uschools"></div>
    <label class="search"><span class="ms">person</span><input name="user" placeholder="Benutzername" autocomplete="username" autocapitalize="off" value="${esc(UNTIS.user || "")}"></label>
    <label class="search"><span class="ms">key</span><input name="pw" type="password" placeholder="Passwort" autocomplete="current-password"></label>
    <button class="btn" type="submit">Anmelden</button></form>`;
  const f = d.querySelector("form"), box = d.querySelector("#uschools"), q = f.q;
  const drawPick = () => { box.innerHTML = pick ? `<button type="button" class="irow on" data-u="re"><span class="ms">check_circle</span><span class="it"><b>${esc(pick.name)}</b><small>${esc(pick.address || pick.server)}</small></span></button>` : ""; };
  drawPick();
  q.oninput = () => { clearTimeout(qT); qT = setTimeout(async () => {
    const v = q.value.trim(); if (v.length < 3) return drawPick();
    try {
      const r = await api(`/untis/schools?q=${encodeURIComponent(v)}`); if (q.value.trim() !== v) return;
      box.innerHTML = r.items.length ? r.items.map((x, i) => `<button type="button" class="irow" data-u="${i}"><span class="ms">school</span><span class="it"><b>${esc(x.name)}</b><small>${esc(x.address || x.server)}</small></span></button>`).join("")
        : `<p class="ihint">${esc(r.hint || "Keine Schule gefunden – anders schreiben oder den Ort dazu")}</p>`;
      box.onclick = (e) => { const b = e.target.closest("[data-u]"); if (!b || b.dataset.u === "re") return; pick = r.items[+b.dataset.u]; q.value = ""; drawPick(); f.user.focus(); };
    } catch (e) { box.innerHTML = `<p class="ihint">${esc(e.message)}</p>`; }
  }, 300); };
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (!pick?.school && !(UNTIS.on && pick)) { toast("Erst die Schule suchen und antippen"); return q.focus(); }
    if (!f.user.value.trim() || !f.pw.value) { toast("Benutzername und Passwort fehlen"); return; }
    const btn = f.querySelector("button[type=submit]"); btn.disabled = true; btn.textContent = "Melde an …";
    try {
      const r = await api("/untis", { method: "PUT", body: JSON.stringify({ server: pick.server, school: pick.school || UNTIS.school_login || "", school_name: pick.name || "", user: f.user.value.trim(), password: f.pw.value }) });
      UNTIS = { ...UNTIS, ...r, teachers: [] }; saveUntis(); closeMenu(); toast("Mit WebUntis verbunden"); loadUntis(); redrawSett();
    } catch (err) { btn.disabled = false; btn.textContent = "Anmelden"; toast(err.message); }
  };
  d.querySelector(".ctx-back").onclick = closeMenu;
  document.body.append(d); setTimeout(() => (pick ? f.user : q).focus(), 60);
}
async function untisOff() {
  try { await api("/untis", { method: "DELETE" }); UNTIS = { on: false, items: [], teachers: [] }; saveUntis(); drawList(); redrawSett(); toast("Von WebUntis abgemeldet"); }
  catch (e) { toast(`Ging nicht: ${e.message}`); }
}
async function loadPrefs() {
  try { const was = PREFS.snap_join; PREFS = { ...PREFS, ...(await api("/prefs")) }; redrawSett(); if (was !== PREFS.snap_join && $("#msgs") && S.msgs.length) drawMsgs(); } catch { /* bleibt */ }
}
async function setPref(k, v) {
  const was = PREFS[k]; PREFS = { ...PREFS, [k]: v }; redrawSett();
  try { PREFS = { ...PREFS, ...(await api("/prefs", { method: "PUT", body: JSON.stringify({ [k]: v }) })) }; loadChats({ quiet: true }); }
  catch (e) { PREFS = { ...PREFS, [k]: was }; toast(`Ging nicht: ${e.message}`); }
  redrawSett(); if (k === "snap_join" && $("#msgs") && S.msgs.length) drawMsgs();
}

// ---------------- Stummschalten (nur Benachrichtigungen; Beeper bleibt unberührt) ----------------
function isMuted(id) { const u = MUTES.get(id); return u !== undefined && (u === 0 || u > Date.now()); }
function muteText(id) {
  const u = MUTES.get(id); if (!u) return "Bis du es aufhebst";
  const d = new Date(u); return `Bis ${d.toDateString() === new Date().toDateString() ? "" : `${fmtShort.format(d)}, `}${fmtTime.format(d)} Uhr`;
}
async function loadMutes() {
  try { const r = await api("/mutes"); MUTES = new Map(r.items.map((x) => [x.id, x.until * 1000])); drawList(); redrawSett(); } catch { /* bleibt */ }
}
function redrawInfo() { const el = $("#info"), c = cur(); if (el && c) el.innerHTML = infoHtml(c, ABOUT.get(c.id)?.d); redrawSett(); }
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
function autoRead() { const c = cur(); if (c && (arOf(c.id) || ownChat(c.id)) && left(c) && document.visibilityState === "visible") markRead(c); }
async function loadAutoRead() {
  try { const r = await api("/autoread"); AUTOREAD = new Map(r.items.map((x) => [x.id, x.mode])); autoRead(); redrawSett(); } catch { /* bleibt */ }
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
// ---------------- Startseite, Listen, Archiv ----------------
// Netzwerke für die Einstellungen: alle, die in geladenen oder gemerkten Chats vorkommen, plus die schon eingestellten
const nets = () => [...new Set([...[...DIR.values(), ...S.chats].map((c) => netKey(c.network)), ...Object.keys(ORG.nets)].filter(Boolean))].sort((a, b) => netName(a).localeCompare(netName(b)));
async function orgCall(path, body, method = "PUT") {
  try { const r = await api(path, { method, body: body ? JSON.stringify(body) : undefined }); orgSet(r); return r; }
  catch (e) { toast(`Ging nicht: ${e.message}`); loadOrg(); return null; }
}
function sheetPick(items, now, done) {   // kleines Auswahl-Blatt: [[Wert, Symbol, Titel, Hinweis], …]
  closeMenu();
  const d = document.createElement("div"); d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet">${items.map(([k, ic, t, sm]) =>
    `<button data-v="${esc(k)}"${k === now ? ' class="on"' : ""}><span class="ms">${k === now ? "check" : ic}</span>${esc(t)}${sm ? `<small>${esc(sm)}</small>` : ""}</button>`).join("")}</div>`;
  d.addEventListener("click", (e) => { const v = e.target.closest("[data-v]")?.dataset.v; closeMenu(); if (v !== undefined && v !== now) done(v); });
  document.body.append(d);
}
function visMenu(c) {
  const net = netVis(c), own = ownVis(c);
  const items = [
    ...(net ? [["", "settings", `Wie alle ${netName(c.network)}-Chats`, VIS_TEXT[net]]] : []),
    [net ? "show" : "", "visibility", "Immer zeigen", ""],
    ["new", "mark_chat_unread", "Nur bei neuen Nachrichten", "Sonst nur über den Filter „Ausgeblendet“"],
    ["hidden", "visibility_off", "Ausblenden", "Nie auf der Startseite"]];
  sheetPick(items, net ? own : own === "show" ? "" : own, async (mode) => {
    const r = await orgCall(`/chats/${encodeURIComponent(c.id)}/vis`, { mode, title: clean(c.title || ""), network: c.merged ? "" : c.network || "" });
    if (r) toast(mode === "hidden" ? `${clean(c.title)} ausgeblendet – zu finden unter „Ausgeblendet“` : mode === "new" ? `${clean(c.title)} erscheint nur noch bei neuen Nachrichten` : `${clean(c.title)} wieder auf der Startseite`);
  });
}
function netVisMenu(n) {
  sheetPick([["", "visibility", "Immer zeigen", ""], ["new", "mark_chat_unread", "Nur bei neuen Nachrichten", ""], ["hidden", "visibility_off", "Ausblenden", "Nie auf der Startseite"]],
    ORG.nets[n] || "", (mode) => orgCall(`/nets/${n}/vis`, { mode }));
}
// Rückfrage im Stil der App (kein confirm(): das zeigt die WebView der Android-App nicht zuverlässig) → Promise<true|false>
function sure(title, text, yes, danger = false) {
  closeMenu();
  return new Promise((res) => {
    const d = document.createElement("div"); d.className = "ctx"; d.id = "ctx";
    d.innerHTML = `<div class="ctx-back"></div><div class="sheet namesheet sure"><b>${esc(title)}</b>${text ? `<p>${esc(text)}</p>` : ""}
      <div class="sure-btns"><button class="btn ghost" data-s="0" type="button">Abbrechen</button><button class="btn ${danger ? "danger" : ""}" data-s="1" type="button">${esc(yes)}</button></div></div>`;
    d.addEventListener("click", (e) => {
      const b = e.target.closest("[data-s]");
      if (!b && !e.target.classList.contains("ctx-back")) return;
      closeMenu(); res(b?.dataset.s === "1");
    });
    document.body.append(d);
  });
}
function nameSheet(title, value, done) {   // Eingabe für einen Namen (kein prompt(): das kann die WebView der Android-App nicht)
  closeMenu();
  const d = document.createElement("div"); d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><form class="sheet namesheet"><b>${esc(title)}</b><label class="search"><span class="ms">label</span><input maxlength="40" value="${esc(value)}" placeholder="Name, z. B. Tagesmutter" autocomplete="off" enterkeyhint="done"></label><button class="btn" type="submit">Speichern</button></form>`;
  const inp = d.querySelector("input");
  d.querySelector("form").onsubmit = (e) => { e.preventDefault(); const v = inp.value.trim(); if (!v) return inp.focus(); closeMenu(); done(v); };
  d.querySelector(".ctx-back").onclick = closeMenu;
  document.body.append(d); setTimeout(() => inp.focus(), 60);
}
function labelNew(c = null) {
  nameSheet("Neue Liste", "", async (name) => {
    const r = await orgCall("/labels", { name }, "POST"); if (!r) return;
    if (c) await orgCall(`/labels/${r.created}/chats/${encodeURIComponent(c.id)}`, { on: true, title: clean(c.title || ""), network: c.merged ? "" : c.network || "" });
    toast(c ? `${clean(c.title)} ist jetzt in „${name}“` : `Liste „${name}“ angelegt – lange auf einen Chat drücken, um ihn hinzuzufügen`);
  });
}
function labelPick(c) {   // Chat in Listen an- und abhaken
  closeMenu();
  const d = document.createElement("div"); d.className = "ctx"; d.id = "ctx";
  const draw = () => `<div class="ctx-back"></div><div class="sheet">${ORG.labels.map((l) => { const on = inLabel(l, c);
      return `<button data-l="${l.id}" class="${on ? "on" : ""}"><span class="ms">${on ? "check_box" : "check_box_outline_blank"}</span>${esc(l.name)}<small>${l.chats.length}</small></button>`; }).join("")}
    <button data-l="+"><span class="ms">new_label</span>Neue Liste …</button></div>`;
  d.innerHTML = draw();
  d.addEventListener("click", async (e) => {
    if (e.target.classList.contains("ctx-back")) return closeMenu();
    const id = e.target.closest("[data-l]")?.dataset.l; if (!id) return;
    if (id === "+") return labelNew(c);
    const l = ORG.labels.find((x) => x.id === id); if (!l) return;
    const on = !inLabel(l, c), ids = on ? [c.id] : [c.id, ...(c.merged || []).map((m) => m.id)].filter((x) => l.chats.some((y) => y.id === x));
    for (const x of ids) await orgCall(`/labels/${id}/chats/${encodeURIComponent(x)}`, { on, title: clean(c.title || ""), network: c.merged ? "" : c.network || "" });
    if (d.isConnected) d.innerHTML = draw();
  });
  document.body.append(d);
}
function labelMenu(l) {
  if (!l) return;
  sheetPick([["open", "filter_list", "Anzeigen", "Als Filter in der Liste"], ["rename", "edit", "Umbenennen", ""], ["del", "delete", "Liste löschen", "Die Chats bleiben, nur die Liste geht"]], "", (a) => {
    if (a === "open") { closeSett(); return setFilter(`l:${l.id}`); }
    if (a === "rename") return nameSheet("Liste umbenennen", l.name, (name) => orgCall(`/labels/${l.id}`, { name }));
    if (a === "del") orgCall(`/labels/${l.id}`, null, "DELETE").then((r) => r && toast(`Liste „${l.name}“ gelöscht`));
  });
}
async function archiveChat(c, on) {
  const ids = new Set([c.id, ...(c.merged || []).map((m) => m.id)]), before = S.chats;
  S.chats = S.chats.filter((x) => !ids.has(x.id)); drawList();
  try {
    await api(`/chats/${encodeURIComponent(c.id)}/archive`, { method: "POST", body: JSON.stringify({ archived: on }) });
    toast(on ? `${clean(c.title)} archiviert · Tippen macht es rückgängig` : `${clean(c.title)} zurück im Posteingang`, on ? () => archiveChat(c, false) : null);
    if (!on) loadChats({ quiet: true });
    if (S.id === c.id && on) back();
  } catch (e) { S.chats = before; drawList(); toast(`Ging nicht: ${e.message}`); }
}
loadAutoRead();
loadMutes();
loadPrefs();
if (readMenu()) history.replaceState(history.state, "", menuBase());   // Neuladen mit offenem Menü: das Menü ist weg, die Adresse auch
route();
if (readLsrch()) showLsrch(true);
if (readSett()) openSett(false);
try { const c = JSON.parse(localStorage.getItem(LIST_KEY) || "[]"); if (c.length && !S.chats.length && !S.q) { S.chats = c; S.next = null; drawList(); } } catch { /* egal */ }
loadChats(S.chats.length ? { quiet: true, keep: false } : {});
loadOrg();
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
if (NATIVE) { const p = NATIVE.pendingChat(); if (p) go(p); }   // App über eine Meldung gestartet: gleich in den Chat
