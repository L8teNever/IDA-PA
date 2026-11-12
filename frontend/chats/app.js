// IDA Chats: deine Beeper-Chats lesen und schreiben. Live per Server-Sent Events – nur solange die Seite sichtbar ist (Akku).
import { esc, autolink } from "./shared/js/dom.js";

const API = "/api/v1";
const $ = (s) => document.querySelector(s);
const BASE = "/";
const COLORS = ["rot", "orange", "gelb", "salbei", "gruen", "tuerkis", "blau", "lavendel", "lila", "koralle"];
const S = { chats: [], next: null, q: "", f: "all", id: null, msgs: [], more: false, oldest: null, seq: 0, sending: false, reply: null, edit: null, voice: {}, vopen: {} };

const fmtDay = new Intl.DateTimeFormat("de-DE", { weekday: "long", day: "2-digit", month: "long" });
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
const avSrc = (id, user) => `${API}/avatar/${encodeURIComponent(id)}${user ? `?user=${encodeURIComponent(user)}` : ""}`;
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
window.addEventListener("popstate", route);
function route() {
  let id = readUrl();
  const g = id && grpOf(id); if (g && g.id !== id) { history.replaceState(history.state, "", urlFor(g.id)); id = g.id; }
  document.body.classList.toggle("reading", !!id);
  if (id === S.id) return drawList();
  const c = id && MC.get(id);
  S.id = id; S.msgs = c ? c.msgs.slice() : []; S.more = c ? c.more : false; S.oldest = c ? c.oldest : null; S.reply = null; S.edit = null; S.to = null;
  document.querySelectorAll("#list .row").forEach((r) => r.classList.toggle("sel", r.dataset.id === id));
  if (!$("#list .row")) drawList();
  if (id) openChat(); else $("#conv").innerHTML = `<div class="conv-empty"><span class="ms">forum</span><p>Wähle einen Chat aus.</p></div>`;
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
    network: [...new Set(live.map((c) => c.network).filter(Boolean))].join(" + "), type: live.length && live.every((c) => c.type === "group") ? "group" : "single", merged: live };
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
      <div class="gset-h">Profilbild</div>${mem.map((x) => `<button class="fwd-row ${x.id === av ? "on" : ""}" data-av="${esc(x.id)}">${netAv(x)}<span class="fwd-t">${label(x)}</span><span class="ms">${x.id === av ? "radio_button_checked" : "radio_button_unchecked"}</span></button>`).join("")}
      <div class="gset-h">Standardmäßig senden über</div>
      <button class="fwd-row ${g.send ? "" : "on"}" data-send=""><span class="av"><span class="ms">auto_mode</span></span><span class="fwd-t">Automatisch<small>Chat der letzten Nachricht</small></span><span class="ms">${g.send ? "radio_button_unchecked" : "radio_button_checked"}</span></button>
      ${mem.map((x) => `<button class="fwd-row ${x.id === g.send ? "on" : ""}" data-send="${esc(x.id)}" >${netAv(x)}<span class="fwd-t">${label(x)}</span><span class="ms">${x.id === g.send ? "radio_button_checked" : "radio_button_unchecked"}</span></button>`).join("")}
    </div><button class="btn fwd-go">Speichern</button></div>`;
  let pAv = av, pSend = g.send || "";
  const mark = (attr, v) => d.querySelectorAll(`[${attr}]`).forEach((b) => { const on = b.getAttribute(attr) === v; b.classList.toggle("on", on); b.lastElementChild.textContent = on ? "radio_button_checked" : "radio_button_unchecked"; });
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
function closeInfo() {
  const el = $("#info"); if (!el) return;
  el.classList.add("out"); setTimeout(() => el.remove(), 200);
}
async function openInfo() {
  const c = cur(); if (!c || $("#info")) return;
  const el = document.createElement("div");
  el.className = "info"; el.id = "info";
  $("#conv").append(el);
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
      ${chats.some((x) => x.muted) ? row("notifications_off", "Stummgeschaltet", "In Beeper") : ""}
      ${left(c) ? row("done_all", "Als gelesen markieren", "", 'data-i="read"') : row("mark_chat_unread", "Als ungelesen markieren", "", 'data-i="unread"')}
      ${row("call_merge", "Mit anderem Chat zusammenführen", "", 'data-i="merge"')}
      ${c.merged ? `<button class="irow danger" data-i="split"><span class="ms">call_split</span><span class="it"><b>Zusammenführung aufheben</b></span></button>` : ""}
    </div>
  </div>`;
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("#info [data-i]")?.dataset.i; if (!a) return;
  const c = cur(); if (!c) return;
  if (a === "close" || a === "chat") return closeInfo();
  if (a === "pin") { togglePin(c); setTimeout(() => { const el = $("#info"); if (el) el.innerHTML = infoHtml(cur(), ABOUT.get(c.id)?.d); }, 50); return; }
  if (a === "search") { closeInfo(); return toast("Suche im Chat gibt es noch nicht"); }
  if (a === "media") { const t = e.target.closest("[data-src]"); if (t.dataset.kind === "image") return lightbox(mediaUrl({ src: t.dataset.src })); closeInfo(); const el = [...document.querySelectorAll("#msgs .vid")].find((v) => v.dataset.src === t.dataset.src); el?.scrollIntoView({ block: "center" }); return; }
  if (a === "gset") return groupSettings(c);
  if (a === "read") { markRead(c); return closeInfo(); }
  if (a === "unread") { markUnread(c); return closeInfo(); }
  if (a === "merge") return chatPick({ title: `${clean(c.title)} zusammenführen mit`, verb: "Zusammenführen", multi: true, skip: new Set((c.merged || [c]).map((x) => x.id)) }, (to) => { closeInfo(); doMerge(c, to); });
  if (a === "split") { closeInfo(); return unMerge(grpOf(c.id)); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#info") && !$("#ctx")) closeInfo(); });
function rowMenu(c) {
  closeMenu();
  const on = PINS.some((p) => p.id === c.id), d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet">
    <button data-p="pin"><span class="ms">push_pin</span>${on ? "Nicht mehr anpinnen" : "Anpinnen"}</button>
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
    if (p === "merge") chatPick({ title: `${clean(c.title)} zusammenführen mit`, verb: "Zusammenführen", multi: true, skip: new Set((c.merged || [c]).map((x) => x.id)) }, (to) => doMerge(c, to));
    if (p === "split") unMerge(grpOf(c.id));
    if (p === "gset") groupSettings(c);
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
      ${avatar(clean(c.title), c.av || c.id)}<div class="rmain"><div class="rtop"><span class="rtitle">${esc(clean(c.title))}</span><span class="rtime">${when(c.at)}</span></div>
      <div class="rsub">${c.network ? `<span class="net">${esc(c.network)}</span>` : ""}${c.type === "group" ? '<span class="ms" style="font-size:16px">group</span>' : ""}${c.merged ? '<span class="ms" style="font-size:16px" title="Zusammengeführt">call_merge</span>' : ""}${pin ? '<span class="ms pin">push_pin</span>' : ""}${left(c) ? `<span class="cnt">${c.unread}</span>` : ""}</div></div></div>`;
  $("#list").innerHTML = pinned.map((c) => row(c, true)).join("") + (pinned.length && rest.length ? '<div class="pinsep"></div>' : "") + rest.map((c) => row(c, false)).join("")
    + (S.next ? '<button class="more" id="more">Mehr laden</button>' : "");
  if (S.id && $(".chead")) refreshReadBar();
}
$("#list").addEventListener("click", (e) => {
  if (e.target.closest("#more")) return loadChats({ more: true });
  if (rowHeld) { rowHeld = false; return; }
  const r = e.target.closest(".row");
  if (!r) return;
  const c = S.chats.find((x) => x.id === r.dataset.id);
  go(r.dataset.id);
});
document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => {
  S.f = b.dataset.f; document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === b)); S.chats = []; loadChats();
}));
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
    if (t) { S.to = t; buzz(10); toChip(); setTimeout(closeTo, 160); $("#txt")?.focus(); }
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
  return left(c) ? `<div class="readbar" id="readbar"><span class="ms">visibility_off</span><div>${c.unread} ungelesen</div><button class="chip" data-a="seen">Als gelesen markieren</button></div>` : "";
}
function refreshReadBar() {
  const c = cur(), old = $("#readbar");
  if (old) old.remove();
  const h = $(".chead"); if (h && left(c)) h.insertAdjacentHTML("afterend", readBar(c));
  const ub = $("#unseenBtn"); if (ub) ub.hidden = !!left(c);
}
async function openChat() {
  $("#conv").innerHTML = `${head()}${readBar(cur())}<div class="msgs" id="msgs"><div class="skel"></div><div class="skel"></div></div>
    <div class="dock" id="dock"><div class="replybar float" id="replybar" hidden></div><form class="composer" id="form">
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
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`; sendMode(); });
  ta.addEventListener("paste", (e) => { const f = [...(e.clipboardData?.files || [])]; if (f.length) { e.preventDefault(); pickFiles(f); } });
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
function fileHtml(f) {
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
    return `<div class="vid"${r} data-a="video" data-src="${src}" data-mime="${esc(f.mime)}">${f.poster ? `<img class="poster" loading="lazy" alt="" src="${mediaUrl({ src: f.poster })}">` : ""}<span class="ms">play_circle</span>${f.duration ? `<small>${mmss(f.duration)}</small>` : ""}</div>`;
  }
  return `<a class="file" href="${mediaUrl(f)}" download="${esc(f.name || "Datei")}"><span class="ms">attach_file</span><span>${esc(f.name || "Datei")}${f.size ? ` <small>${f.size > 1e6 ? (f.size / 1e6).toFixed(1) + " MB" : Math.round(f.size / 1e3) + " KB"}</small>` : ""}</span></a>`;
}
// Lesebestätigung wie in Beeper: ein Häkchen = gesendet, zwei (mint) = gelesen. In Gruppen steht unter der letzten gelesenen Nachricht, wer sie gelesen hat.
const ticks = (m) => !m.mine || m.deleted ? "" : m.pending ? '<span class="ms tick">schedule</span>'
  : m.read_by?.length ? '<span class="ms tick read" title="Gelesen">done_all</span>' : '<span class="ms tick" title="Gesendet">check</span>';
function readLine(m) {
  if (!m.mine || !m.read_by?.length || cur()?.type !== "group") return "";
  const last = [...S.msgs].reverse().find((x) => x.mine && x.read_by?.length);
  if (last !== m) return "";
  const t = (r) => clean(r.name) || "Jemand";
  const names = m.read_by.map(t), shown = names.slice(0, 3).join(", ") + (names.length > 3 ? ` und ${names.length - 3} weitere` : "");
  return `<div class="readline" title="${esc(m.read_by.map((r) => `${t(r)} ${r.at ? fmtTime.format(new Date(r.at)) : ""}`).join("\n"))}"><span class="ms">done_all</span>Gelesen von ${esc(shown)}</div>`;
}
function bubble(m, tail = true) {
  const files = m.files.map((f) => fileHtml(f)).join("");
  // Gelöscht: Hinweis – und darunter, was drinstand, wenn der Dienst die Nachricht vorher schon gesehen hatte
  const was = m.deleted && m.was ? `<div class="was">${(m.was.files || []).map((f) => fileHtml(f)).join("")}${m.was.text ? `<span class="tx">${autolink(esc(m.was.text))}</span>` : ""}</div>` : "";
  const body = m.deleted ? `<span class="gone"><span class="ms">block</span>${m.mine ? "Du hast diese Nachricht gelöscht" : "Diese Nachricht wurde gelöscht"}</span>${was}` : m.text ? `<span class="tx">${autolink(esc(m.text))}</span>` : "";
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
  const links = m.deleted ? "" : (m.links || []).filter((l) => l.title || l.img).map((l) => `<a class="lp" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${l.img ? `<img loading="lazy" alt=""${l.w && l.h ? ` style="aspect-ratio:${l.w}/${l.h}"` : ""} src="${mediaUrl({ src: l.img })}">` : ""}<span><b>${esc(l.title || host(l.url))}</b>${l.summary ? `<i>${esc(l.summary)}</i>` : ""}<small>${esc(host(l.url))}</small></span></a>`).join("");
  const group = cur()?.type === "group" && !m.mine ? `<span class="who">${esc(clean(m.sender))}</span>` : "";
  const q = m.reply_to && S.msgs.find((x) => x.id === m.reply_to);
  const quote = m.reply_to ? `<div class="quote" data-a="jump" data-id="${esc(m.reply_to)}"><b>${esc(q ? (q.mine ? "Du" : clean(q.sender)) : "Antwort")}</b>${esc(q ? (q.text || (q.files[0] ? "Anhang" : "")).slice(0, 120) : "Nachricht")}</div>` : "";
  const rx = m.reactions?.length ? `<div class="rx">${m.reactions.map((r) => `<span class="${r.mine ? "mine" : ""}" data-a="rx" data-id="${esc(m.id)}" data-k="${esc(r.key)}">${esc(r.key)}${r.n > 1 ? ` ${r.n}` : ""}</span>`).join("")}</div>` : "";
  const tools = m.pending || m.deleted ? "" : `<button class="rep" data-a="reply" data-id="${esc(m.id)}" aria-label="Antworten" title="Antworten"><span class="ms">reply</span></button>`;
  const face = cur()?.type === "group" && !m.mine ? (tail ? avatar(clean(m.sender), cid(m), m.sender_id) : '<span class="av gap"></span>') : "";
  const prog = m.pendingFile ? `<div class="prog"><i style="width:${Math.round((m.progress || 0) * 100)}%"></i></div>` : "";
  const mc = cur()?.merged, net = mc ? netKey((mc.find((x) => x.id === (m.chat || target())) || {}).network) : null;
  return `<div class="bw ${m.mine ? "me" : ""} ${face ? "grp" : ""}"${net !== null ? ` data-net="${net}"` : ""} id="m-${esc(m.id)}" data-id="${esc(m.id)}"><span class="swipe ms">reply</span>${face}${tools}<div class="b ${m.mine ? "me" : ""} ${m.pending ? "pending" : ""} ${m.deleted ? "del" : ""}">${group}${quote}${files}${prog}${body}${links}${rx}<span class="tm">${m.chat && cur()?.merged ? `${esc((cur().merged.find((x) => x.id === m.chat) || {}).network || "")} · ` : ""}${m.edited && !m.deleted ? "bearbeitet · " : ""}${m.at ? fmtTime.format(new Date(m.at)) : "…"}${ticks(m)}</span></div>${readLine(m)}</div>`;
}
const NETS = [["snapchat", "snapchat"], ["instagram", "instagram"], ["whatsapp", "whatsapp"], ["signal", "signal"], ["telegram", "telegram"], ["discord", "discord"],
  ["messenger", "messenger"], ["facebook", "messenger"], ["gmessages", "gmessages"], ["google messages", "gmessages"], ["googlechat", "googlechat"], ["google chat", "googlechat"],
  ["slack", "slack"], ["linkedin", "linkedin"], ["twitter", "x"], ["imessage", "imessage"], ["sms", "gmessages"]];
const netKey = (n) => { n = String(n || "").toLowerCase(); return (NETS.find(([k]) => n.includes(k)) || [, ""])[1]; };
// Steht der Chat am Ende? Nur dann darf nachladendes (Bilder, Tastatur) ans Ende schieben – wer hochgescrollt hat, bleibt dort.
let atBottom = true;
document.addEventListener("scroll", (e) => { const b = e.target; if (b.id === "msgs") atBottom = b.scrollHeight - b.scrollTop - b.clientHeight < 150; }, { capture: true, passive: true });
let drawn = { id: null, ids: new Set() };
function drawMsgs({ stick = false, keepTop = false } = {}) {
  const box = $("#msgs"); if (!box) return;
  const fresh = drawn.id === S.id && !keepTop ? S.msgs.slice(-6).filter((m) => !drawn.ids.has(m.id)) : [];
  const prevH = box.scrollHeight, prevTop = box.scrollTop;
  let last = "", html = S.more ? '<button class="older" data-a="older">Ältere laden</button>' : "";
  if (!S.msgs.length) html += '<div class="empty">Noch keine Nachrichten.</div>';
  S.msgs.forEach((m, i) => {
    const k = m.at ? dayKey(m.at) : last;
    if (k !== last) { last = k; html += `<div class="day">${esc(fmtDay.format(new Date(m.at)))}</div>`; }
    const n = S.msgs[i + 1];   // Bild nur an der letzten Nachricht einer Folge vom selben Absender (wie in Beeper)
    html += bubble(m, !n || n.mine || n.sender_id !== m.sender_id || (n.at && dayKey(n.at) !== k));
  });
  box.innerHTML = html;
  drawn = { id: S.id, ids: new Set(S.msgs.map((m) => m.id)) };
  for (const m of fresh) document.getElementById(`m-${m.id}`)?.classList.add("in");
  $("#conv").dataset.net = cur()?.merged ? "" : netKey(cur()?.network);   // eigene Nachrichten in Netzwerkfarbe, eingehende mit Akzenten in Netzwerkfarbe
  paint();
  if (keepTop) box.scrollTop = box.scrollHeight - prevH + prevTop;
  else if (stick) { atBottom = true; box.scrollTop = box.scrollHeight; box.querySelectorAll("img").forEach((i) => i.addEventListener("load", () => { if (atBottom) box.scrollTop = box.scrollHeight; }, { once: true })); }
  else box.scrollTop = prevTop;
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
  if (a.dataset.a === "jump") { const el = document.getElementById(`m-${a.dataset.id}`); if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200); } return; }
  if (a.dataset.a === "play") return togglePlay(a.closest(".voice"));
  if (a.dataset.a === "speed") return cycleSpeed(a);
  if (a.dataset.a === "seek") return seek(a.closest(".voice"), e);
  if (a.dataset.a === "vtext") return voiceText(a);
  if (a.dataset.a === "zoom") return lightbox(a.src);
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
function playVideo(el) {
  const v = document.createElement("video");
  v.controls = true; v.autoplay = true; v.playsInline = true; v.className = "vplay";
  v.src = `${API}/media?src=${encodeURIComponent(el.dataset.src)}${el.dataset.mime ? `&mime=${encodeURIComponent(el.dataset.mime)}` : ""}`;
  v.addEventListener("error", () => toast("Video konnte nicht geladen werden"));
  el.replaceWith(v);
}
function lightbox(src) {
  const d = document.createElement("div");
  d.className = "lightbox"; d.innerHTML = `<img alt="" src="${src}"><button class="icon-btn" aria-label="Schließen"><span class="ms">close</span></button>`;
  d.onclick = () => d.remove();
  document.body.append(d);
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
  function place() {   // so nah wie möglich an der echten Stelle, aber ganz im Bild
    const h = stack.offsetHeight, w = stack.offsetWidth, top0 = r.top - (stack.querySelector(".ctx-msg").offsetTop);
    const top = Math.max(12 + (parseFloat(getComputedStyle(document.body).getPropertyValue("--sat")) || 0), Math.min(top0, innerHeight - h - 12));
    const left = m.mine ? Math.min(r.right, innerWidth - 8) - w : Math.max(8, r.left);
    stack.style.top = `${top}px`; stack.style.left = `${Math.max(8, Math.min(left, innerWidth - w - 8))}px`;
  }
  place();
  buzz(20);
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
const gest = { w: null, x: 0, y: 0, dx: 0, t: 0, lock: false, armed: false };
function gestEnd() {
  clearTimeout(gest.t);
  if (gest.w) { gest.w.style.transform = ""; gest.w.classList.remove("armed", "drag"); }
  gest.w = null;
}
const msgsEl = () => document.getElementById("msgs");
document.addEventListener("touchstart", (e) => {
  const w = e.target.closest?.("#msgs .bw");
  if (!w || e.touches.length !== 1 || e.target.closest(".voice,video,.vid,a")) return;
  const t = e.touches[0];
  Object.assign(gest, { w, x: t.clientX, y: t.clientY, dx: 0, lock: false, armed: false });
  gest.t = setTimeout(() => { const m = bubbleMsg(w); if (m && !gest.lock) { gest.w = null; menu(m); } }, 450);
}, { passive: true });
document.addEventListener("touchmove", (e) => {
  if (!gest.w) return;
  const t = e.touches[0], dx = t.clientX - gest.x, dy = t.clientY - gest.y;
  if (!gest.lock && (Math.abs(dy) > 10 || Math.abs(dx) > 10)) {
    clearTimeout(gest.t);
    if (Math.abs(dx) > Math.abs(dy) * 1.4 && dx > 0) gest.lock = true; else return gestEnd();
  }
  if (!gest.lock) return;
  if (e.cancelable) e.preventDefault();   // nur die eine Nachricht bewegt sich, der Chat bleibt stehen
  gest.dx = Math.min(dx, 90);
  gest.w.classList.add("drag");
  gest.w.style.transform = `translateX(${gest.dx * 0.8}px)`;
  const armed = gest.dx > 60;
  if (armed && !gest.armed) buzz(15);
  gest.armed = armed; gest.w.classList.toggle("armed", armed);
}, { passive: false });
document.addEventListener("touchend", () => {
  if (gest.w && gest.armed) { const m = bubbleMsg(gest.w); if (m && !m.pending) setReply(m); }
  gestEnd();
});
document.addEventListener("touchcancel", gestEnd);
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
function chatPick({ title, verb, tail = "", skip = new Set(), multi = false }, done) {
  closeMenu();
  const d = document.createElement("div");
  d.className = "ctx"; d.id = "ctx";
  d.innerHTML = `<div class="ctx-back"></div><div class="sheet fwd"><div class="fwd-top"><b>${esc(title)}</b><button class="icon-btn" data-x="close" aria-label="Abbrechen"><span class="ms">close</span></button></div>
    <label class="search"><span class="ms">search</span><input type="search" placeholder="Chat suchen" autocomplete="off"></label><div class="fwd-list"></div>
    <button class="btn fwd-go" disabled>${esc(verb)}</button></div>`;
  const list = d.querySelector(".fwd-list"), inp = d.querySelector("input"), goBtn = d.querySelector(".fwd-go");
  let items = [], pick = null, t, seq = 0;
  const picks = new Map();   // Mehrfachauswahl (Zusammenführen): bleibt auch beim Suchen erhalten
  const on = (c) => (multi ? picks.has(c.id) : pick?.id === c.id);
  const keep = (l) => collapse(l).filter((c) => !skip.has(c.id) && !(c.merged || []).some((x) => skip.has(x.id)));
  const base = () => { const ids = new Set(PINS.map((p) => p.id)); return keep([...PINS, ...S.chats.filter((c) => !ids.has(c.id))]); };
  const draw = () => {
    list.innerHTML = items.length ? items.map((c) => `<button class="fwd-row ${on(c) ? "on" : ""}" data-id="${esc(c.id)}">${avatar(clean(c.title), c.av || c.id)}
      <span class="fwd-t">${esc(clean(c.title))}<small>${esc(c.network || "")}</small></span><span class="ms">${on(c) ? (multi ? "check_box" : "check_circle") : (multi ? "check_box_outline_blank" : "radio_button_unchecked")}</span></button>`).join("")
      : '<div class="empty">Nichts gefunden.</div>';
    if (multi) { goBtn.disabled = !picks.size; goBtn.textContent = picks.size ? `${verb} (${picks.size + 1} Chats)` : "Chats auswählen"; return; }
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
  const bar = $("#replybar"); if (!bar) return;
  bar.hidden = !m;
  if (m) { bar.innerHTML = `<span class="ms">reply</span><div><b>${esc(m.mine ? "Du" : clean(m.sender))}</b>${esc((m.text || (m.files[0] ? "Anhang" : "")).slice(0, 100))}</div><button class="icon-btn" data-a="cancel" aria-label="Abbrechen"><span class="ms">close</span></button>`; $("#txt").focus(); }
}
async function send() {
  const ta = $("#txt"), text = ta.value.trim();
  if (S.edit) return saveEdit();
  if (!text || S.sending) return;
  S.sending = true; $("#sendBtn").disabled = true;
  const tmp = { id: `tmp${Date.now()}`, text, reply_to: S.reply?.id || null, reactions: [], mine: true, pending: true, at: new Date().toISOString(), files: [], type: "TEXT", sender: "" };
  S.msgs.push(tmp); drawMsgs({ stick: true });
  ta.value = ""; ta.style.height = "auto"; sendMode();
  const rep = S.reply; setReply(null);
  try {
    await api(`/chats/${encodeURIComponent(rep?.chat || target())}/send`, { method: "POST", body: JSON.stringify({ text, reply_to: rep ? mid(rep) : null }) });
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
    rT = setTimeout(() => { loadChats({ quiet: true }); loadPins(); if (S.id && (!d.id || gid === S.id)) loadMsgs({ quiet: true }); }, 250);
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
  loadChats({ quiet: true }); if (S.id) loadMsgs({ quiet: true });
  live(true);
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
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
