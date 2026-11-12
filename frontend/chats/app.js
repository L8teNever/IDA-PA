// IDA Chats: deine Beeper-Chats lesen und schreiben. Live per Server-Sent Events – nur solange die Seite sichtbar ist (Akku).
import { esc, autolink } from "./shared/js/dom.js";

const API = "/api/v1";
const $ = (s) => document.querySelector(s);
const BASE = "/";
const COLORS = ["rot", "orange", "gelb", "salbei", "gruen", "tuerkis", "blau", "lavendel", "lila", "koralle"];
const S = { chats: [], next: null, q: "", f: "all", id: null, msgs: [], more: false, oldest: null, seq: 0, sending: false, reply: null, voice: {}, vopen: {} };

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
const avatar = (t, id, user = "") => `<span class="av" style="${color(user || id || t)}">${esc(letter(t))}${id ? `<img class="avimg" alt="" loading="lazy" decoding="async" src="${API}/avatar/${encodeURIComponent(id)}${user ? `?user=${encodeURIComponent(user)}` : ""}">` : ""}</span>`;
document.addEventListener("error", (e) => { if (e.target.classList?.contains("avimg")) e.target.remove(); }, true);
document.addEventListener("load", (e) => { if (e.target.classList?.contains("avimg")) e.target.classList.add("ok"); }, true);
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
  const id = readUrl();
  document.body.classList.toggle("reading", !!id);
  if (id === S.id) return drawList();
  S.id = id; S.msgs = []; S.more = false; S.reply = null;
  drawList();
  if (id) openChat(); else $("#conv").innerHTML = `<div class="conv-empty"><span class="ms">forum</span><p>Wähle einen Chat aus.</p></div>`;
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
// Gelesen-Markierung: Beeper bekommt vom Öffnen nichts mit (kein Lesebestätigung an den Absender). Der Zustand "hier gelesen" liegt nur auf diesem Gerät.
const SEEN_KEY = "chats-seen";
const seen = (() => { try { return JSON.parse(localStorage.getItem(SEEN_KEY) || "{}"); } catch { return {}; } })();
const left = (c) => (c && c.unread && seen[c.id] !== c.unread ? c.unread : 0);   // gilt als ungelesen, bis markiert (oder neue Nachricht kommt)
function markSeen(c) {
  if (!c) return;
  if (c.unread) seen[c.id] = c.unread; else delete seen[c.id];
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch {}
}
function drawList() {
  const n = S.chats.reduce((a, c) => a + left(c), 0);
  $("#sub").textContent = n ? `${n} ungelesen` : "aus Beeper";
  document.title = n ? `(${n}) Chats` : "Chats";
  if (navigator.setAppBadge) (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => {});
  if (!S.chats.length) { $("#list").innerHTML = `<div class="empty"><span class="ms">${S.q ? "search_off" : "forum"}</span>${S.q ? "Nichts gefunden." : "Keine Chats."}</div>`; return; }
  $("#list").innerHTML = S.chats.map((c) => `<div class="row ${left(c) ? "unread" : ""} ${c.id === S.id ? "sel" : ""}" data-id="${esc(c.id)}">
      ${avatar(clean(c.title), c.id)}<div class="rmain"><div class="rtop"><span class="rtitle">${esc(clean(c.title))}</span><span class="rtime">${when(c.at)}</span></div>
      <div class="rsub">${c.network ? `<span class="net">${esc(c.network)}</span>` : ""}${c.type === "group" ? '<span class="ms" style="font-size:16px">group</span>' : ""}${left(c) ? `<span class="cnt">${c.unread}</span>` : ""}</div></div></div>`).join("")
    + (S.next ? '<button class="more" id="more">Mehr laden</button>' : "");
  if (S.id && $(".chead")) refreshReadBar();
}
$("#list").addEventListener("click", (e) => {
  if (e.target.closest("#more")) return loadChats({ more: true });
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
const cur = () => S.chats.find((c) => c.id === S.id);
function head() {
  const c = cur(), title = clean(c?.title || "Chat");
  return `<div class="chead"><button class="icon-btn back" data-a="back" aria-label="Zurück"><span class="ms">arrow_back</span></button>${avatar(title, S.id)}
    <div class="t"><b>${esc(title)}</b><small>${esc(c?.network || "")}${c?.type === "group" ? " · Gruppe" : ""}</small></div></div>`;
}
function readBar(c) {
  return left(c) ? `<div class="readbar" id="readbar"><span class="ms">visibility_off</span><div>${c.unread} ungelesen · für den Absender nicht als gelesen markiert</div><button class="chip" data-a="seen">Als gelesen markieren</button></div>` : "";
}
function refreshReadBar() {
  const c = cur(), old = $("#readbar");
  if (old) old.remove();
  const h = $(".chead"); if (h && left(c)) h.insertAdjacentHTML("afterend", readBar(c));
}
async function openChat() {
  $("#conv").innerHTML = `${head()}${readBar(cur())}<div class="msgs" id="msgs"><div class="skel"></div><div class="skel"></div></div>
    <div class="replybar" id="replybar" hidden></div><form class="composer" id="form">
    <button class="icon-btn attach" id="attachBtn" type="button" aria-label="Anhängen" title="Foto, Video oder Datei"><span class="ms">add</span></button>
    <textarea id="txt" rows="1" placeholder="Nachricht" enterkeyhint="send"></textarea>
    <button class="send mic" id="sendBtn" type="submit" aria-label="Sprachnachricht aufnehmen"><span class="ms">mic</span></button></form>
    <div class="recbar" id="recbar" hidden><button class="icon-btn" type="button" data-rec="cancel" aria-label="Verwerfen"><span class="ms">delete</span></button>
      <span class="recdot"></span><span class="rectime" id="rectime">0:00</span><div class="recwave" id="recwave"></div>
      <button class="icon-btn" type="button" data-rec="pause" aria-label="Pause"><span class="ms">pause</span></button>
      <button class="send" type="button" data-rec="send" aria-label="Sprachnachricht senden"><span class="ms">send</span></button></div>`;
  const ta = $("#txt");
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`; sendMode(); });
  ta.addEventListener("paste", (e) => { const f = [...(e.clipboardData?.files || [])]; if (f.length) { e.preventDefault(); pickFiles(f); } });
  $("#attachBtn").onclick = attachMenu;
  $("#recbar").addEventListener("click", (e) => { const a = e.target.closest("[data-rec]")?.dataset.rec; if (a) recAction(a); });
  const conv = $("#conv");
  conv.ondragover = (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); conv.classList.add("drop"); } };
  conv.ondragleave = (e) => { if (e.target === conv || !conv.contains(e.relatedTarget)) conv.classList.remove("drop"); };
  conv.ondrop = (e) => { conv.classList.remove("drop"); const f = [...(e.dataTransfer?.files || [])]; if (f.length) { e.preventDefault(); pickFiles(f); } };
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); $("#form").requestSubmit(); } });
  $("#form").addEventListener("submit", (e) => { e.preventDefault(); if (ta.value.trim()) send(); else startRec(); });
  if (!cur()) loadChats({ quiet: true }).then(() => { if (S.id && $(".chead")) { $(".chead").outerHTML = head(); refreshReadBar(); } });
  await loadMsgs({ first: true });
  if (matchMedia("(pointer: fine)").matches) ta.focus();
}
async function loadMsgs({ first = false, quiet = false, older = false } = {}) {
  const id = S.id, seq = ++S.seq;
  try {
    const d = await api(`/chats/${encodeURIComponent(id)}/messages${older && S.oldest ? `?cursor=${encodeURIComponent(S.oldest)}&direction=before` : ""}`);
    if (id !== S.id || seq !== S.seq) return;
    const box = $("#msgs"), atEnd = !box || box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    const keep = new Map(S.msgs.map((m) => [m.id, m]));
    for (const m of d.items) keep.set(m.id, m);
    const sig = (l) => l.map((m) => `${m.id}|${(m.read_by || []).length}|${(m.reactions || []).map((r) => r.key + r.n).join()}|${m.deleted}|${m.pending ? 1 : 0}`).join(";");
    const before = sig(S.msgs);
    const landed = (m) => m.pendingFile ? m.sentAt && d.items.some((x) => x.mine && x.files.length && Date.parse(x.at) >= Date.parse(m.at) - 10000)
      : d.items.some((x) => x.mine && x.text === m.text);
    S.msgs = [...keep.values()].filter((m) => !m.pending || !landed(m)).sort((a, b) => (a.at || "").localeCompare(b.at || ""));
    if (!older) { S.more = d.more; S.oldest = d.oldest; } else { S.more = d.more; S.oldest = d.oldest; }
    if (quiet && !first && !older && before === sig(S.msgs) && $("#msgs")?.children.length) return;   // nichts Neues: nicht neu zeichnen (Scrollstand, Player)
    drawMsgs({ stick: first || atEnd, keepTop: older });
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
    return `<div class="vid"${r} data-a="video" data-src="${src}" data-mime="${esc(f.mime)}"><span class="ms">play_circle</span>${f.duration ? `<small>${mmss(f.duration)}</small>` : ""}</div>`;
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
  const body = m.deleted ? "Nachricht gelöscht" : m.text ? `<span class="tx">${autolink(esc(m.text))}</span>` : "";
  const group = cur()?.type === "group" && !m.mine ? `<span class="who">${esc(clean(m.sender))}</span>` : "";
  const q = m.reply_to && S.msgs.find((x) => x.id === m.reply_to);
  const quote = m.reply_to ? `<div class="quote" data-a="jump" data-id="${esc(m.reply_to)}"><b>${esc(q ? (q.mine ? "Du" : clean(q.sender)) : "Antwort")}</b>${esc(q ? (q.text || (q.files[0] ? "Anhang" : "")).slice(0, 120) : "Nachricht")}</div>` : "";
  const rx = m.reactions?.length ? `<div class="rx">${m.reactions.map((r) => `<span class="${r.mine ? "mine" : ""}" data-a="rx" data-id="${esc(m.id)}" data-k="${esc(r.key)}">${esc(r.key)}${r.n > 1 ? ` ${r.n}` : ""}</span>`).join("")}</div>` : "";
  const tools = m.pending || m.deleted ? "" : `<button class="rep" data-a="reply" data-id="${esc(m.id)}" aria-label="Antworten" title="Antworten"><span class="ms">reply</span></button>`;
  const face = cur()?.type === "group" && !m.mine ? (tail ? avatar(clean(m.sender), S.id, m.sender_id) : '<span class="av gap"></span>') : "";
  const prog = m.pendingFile ? `<div class="prog"><i style="width:${Math.round((m.progress || 0) * 100)}%"></i></div>` : "";
  return `<div class="bw ${m.mine ? "me" : ""} ${face ? "grp" : ""}" id="m-${esc(m.id)}" data-id="${esc(m.id)}"><span class="swipe ms">reply</span>${face}${tools}<div class="b ${m.mine ? "me" : ""} ${m.pending ? "pending" : ""} ${m.deleted ? "del" : ""}">${group}${quote}${files}${prog}${body}${rx}<span class="tm">${m.at ? fmtTime.format(new Date(m.at)) : "…"}${ticks(m)}</span></div>${readLine(m)}</div>`;
}
const NETS = [["snapchat", "snapchat"], ["instagram", "instagram"], ["whatsapp", "whatsapp"], ["signal", "signal"], ["telegram", "telegram"], ["discord", "discord"],
  ["messenger", "messenger"], ["facebook", "messenger"], ["gmessages", "gmessages"], ["google messages", "gmessages"], ["googlechat", "googlechat"], ["google chat", "googlechat"],
  ["slack", "slack"], ["linkedin", "linkedin"], ["twitter", "x"], ["imessage", "imessage"], ["sms", "gmessages"]];
const netKey = (n) => { n = String(n || "").toLowerCase(); return (NETS.find(([k]) => n.includes(k)) || [, ""])[1]; };
function drawMsgs({ stick = false, keepTop = false } = {}) {
  const box = $("#msgs"); if (!box) return;
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
  box.dataset.net = netKey(cur()?.network);   // eigene Nachrichten in der Farbe des Netzwerks (Snapchat gelb, Instagram lila-rosa …)
  paint();
  if (keepTop) box.scrollTop = box.scrollHeight - prevH + prevTop;
  else if (stick) { box.scrollTop = box.scrollHeight; box.querySelectorAll("img").forEach((i) => i.addEventListener("load", () => { box.scrollTop = box.scrollHeight; }, { once: true })); }
  else box.scrollTop = prevTop;
}
$("#conv").addEventListener("click", (e) => {
  const a = e.target.closest("[data-a]");
  if (!a) return;
  if (a.dataset.a === "back") return back();
  if (a.dataset.a === "older") return loadMsgs({ older: true });
  if (a.dataset.a === "reply") return setReply(S.msgs.find((m) => m.id === a.dataset.id));
  if (a.dataset.a === "seen") { markSeen(cur()); refreshReadBar(); return drawList(); }
  if (a.dataset.a === "rx") { const m = S.msgs.find((x) => x.id === a.dataset.id); return m && toggleReaction(m, a.dataset.k); }
  if (a.dataset.a === "cancel") return setReply(null);
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
      ${m.files[0] ? '<button data-c="open"><span class="ms">open_in_new</span>Datei öffnen</button>' : ""}
    </div></div>`;
  const clone = src.cloneNode(true);
  clone.removeAttribute("id"); clone.querySelectorAll("[id]").forEach((x) => x.removeAttribute("id"));
  clone.style.width = `${r.width}px`;
  d.querySelector(".ctx-msg").append(clone);
  d.querySelector(".ctx-msg").dataset.net = netKey(cur()?.network);
  d.addEventListener("click", (e) => {
    const rb = e.target.closest("[data-r]");
    if (rb) { closeMenu(); return toggleReaction(m, rb.dataset.r); }
    const c = e.target.closest("[data-c]")?.dataset.c;
    if (c === "more") { d.querySelector(".ctx-more").hidden = false; e.target.closest(".plus").remove(); return place(); }
    if (e.target.closest(".ctx-msg")) return;
    closeMenu();
    if (c === "reply") setReply(m);
    if (c === "copy") navigator.clipboard?.writeText(m.text).then(() => toast("Kopiert"), () => toast("Kopieren nicht möglich"));
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
    const path = `/chats/${encodeURIComponent(S.id)}/messages/${encodeURIComponent(m.id)}/react`;
    if (had) await api(`${path}?key=${encodeURIComponent(key)}`, { method: "DELETE" });
    else await api(path, { method: "POST", body: JSON.stringify({ key }) });
    setTimeout(() => loadMsgs({ quiet: true }), 1200);
  } catch (e) {
    m.reactions = JSON.parse(before); drawMsgs(); toast(`Reaktion ging nicht: ${e.message}`);
  }
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

function setReply(m) {
  S.reply = m || null;
  const bar = $("#replybar"); if (!bar) return;
  bar.hidden = !m;
  if (m) { bar.innerHTML = `<span class="ms">reply</span><div><b>${esc(m.mine ? "Du" : clean(m.sender))}</b>${esc((m.text || (m.files[0] ? "Anhang" : "")).slice(0, 100))}</div><button class="icon-btn" data-a="cancel" aria-label="Abbrechen"><span class="ms">close</span></button>`; $("#txt").focus(); }
}
async function send() {
  const ta = $("#txt"), text = ta.value.trim();
  if (!text || S.sending) return;
  S.sending = true; $("#sendBtn").disabled = true;
  const tmp = { id: `tmp${Date.now()}`, text, reply_to: S.reply?.id || null, reactions: [], mine: true, pending: true, at: new Date().toISOString(), files: [], type: "TEXT", sender: "" };
  S.msgs.push(tmp); drawMsgs({ stick: true });
  ta.value = ""; ta.style.height = "auto"; sendMode();
  const rep = S.reply; setReply(null);
  try {
    await api(`/chats/${encodeURIComponent(S.id)}/send`, { method: "POST", body: JSON.stringify({ text, reply_to: rep?.id || null }) });
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
  if (rep?.id) fd.append("reply_to", rep.id);
  if (voice) { fd.append("voice", "true"); fd.append("duration", String(duration)); }
  return new Promise((ok) => {   // XHR statt fetch: so gibt es einen Fortschrittsbalken
    const x = new XMLHttpRequest();
    x.open("POST", `${API}/chats/${encodeURIComponent(id)}/upload`);
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
    if (d.type === "chat" && d.new && d.id !== S.id) toast(`Neue Nachricht von ${clean(d.title)}`, () => go(d.id));
    clearTimeout(rT);
    rT = setTimeout(() => { loadChats({ quiet: true }); if (S.id && (!d.id || d.id === S.id)) loadMsgs({ quiet: true }); }, 250);
  };
}
// Gelesen-Häkchen und Reaktionen im offenen Chat: ändern den Chat in der Liste nicht, darum fragt die Seite den offenen Chat alle paar Sekunden
setInterval(() => { if (S.id && !document.hidden && !S.sending) loadMsgs({ quiet: true }); }, 4000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return live(false);
  loadChats({ quiet: true }); if (S.id) loadMsgs({ quiet: true });
  live(true);
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
route();
loadChats();
live(!document.hidden);
