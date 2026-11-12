// IDA Chats: deine Beeper-Chats lesen und schreiben. Live per Server-Sent Events – nur solange die Seite sichtbar ist (Akku).
import { esc, autolink } from "./shared/js/dom.js";

const API = "/api/v1";
const $ = (s) => document.querySelector(s);
const BASE = "/";
const COLORS = ["rot", "orange", "gelb", "salbei", "gruen", "tuerkis", "blau", "lavendel", "lila", "koralle"];
const S = { chats: [], next: null, q: "", f: "all", id: null, msgs: [], more: false, oldest: null, seq: 0, sending: false, reply: null, voice: {} };

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
const avatar = (t, id) => `<span class="av" style="${color(id || t)}">${esc(letter(t))}</span>`;
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
function go(id, push = true) {
  if (urlFor(id) !== location.pathname) history[push ? "pushState" : "replaceState"](null, "", urlFor(id));
  route();
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
function drawList() {
  const n = S.chats.reduce((a, c) => a + (c.unread || 0), 0);
  $("#sub").textContent = n ? `${n} ungelesen` : "aus Beeper";
  document.title = n ? `(${n}) Chats` : "Chats";
  if (navigator.setAppBadge) (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => {});
  if (!S.chats.length) { $("#list").innerHTML = `<div class="empty"><span class="ms">${S.q ? "search_off" : "forum"}</span>${S.q ? "Nichts gefunden." : "Keine Chats."}</div>`; return; }
  $("#list").innerHTML = S.chats.map((c) => `<div class="row ${c.unread ? "unread" : ""} ${c.id === S.id ? "sel" : ""}" data-id="${esc(c.id)}">
      ${avatar(clean(c.title), c.id)}<div class="rmain"><div class="rtop"><span class="rtitle">${esc(clean(c.title))}</span><span class="rtime">${when(c.at)}</span></div>
      <div class="rsub">${c.network ? `<span class="net">${esc(c.network)}</span>` : ""}${c.type === "group" ? '<span class="ms" style="font-size:16px">group</span>' : ""}${c.unread ? `<span class="cnt">${c.unread}</span>` : ""}</div></div></div>`).join("")
    + (S.next ? '<button class="more" id="more">Mehr laden</button>' : "");
}
$("#list").addEventListener("click", (e) => {
  if (e.target.closest("#more")) return loadChats({ more: true });
  const r = e.target.closest(".row");
  if (!r) return;
  const c = S.chats.find((x) => x.id === r.dataset.id);
  if (c) c.unread = 0;
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
async function openChat() {
  $("#conv").innerHTML = `${head()}<div class="msgs" id="msgs"><div class="skel"></div><div class="skel"></div></div>
    <div class="replybar" id="replybar" hidden></div><form class="composer" id="form"><textarea id="txt" rows="1" placeholder="Nachricht" enterkeyhint="send"></textarea>
    <button class="send" id="sendBtn" type="submit" aria-label="Senden"><span class="ms">send</span></button></form>`;
  const ta = $("#txt");
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`; });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); $("#form").requestSubmit(); } });
  $("#form").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  if (!cur()) loadChats({ quiet: true }).then(() => { if (S.id && $(".chead")) $(".chead").outerHTML = head(); });
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
    S.msgs = [...keep.values()].filter((m) => !m.pending || !d.items.some((x) => x.mine && x.text === m.text)).sort((a, b) => (a.at || "").localeCompare(b.at || ""));
    if (!older) { S.more = d.more; S.oldest = d.oldest; } else { S.more = d.more; S.oldest = d.oldest; }
    drawMsgs({ stick: first || atEnd, keepTop: older });
  } catch (e) {
    if (!quiet && id === S.id) { const b = $("#msgs"); if (b) b.innerHTML = errBox(e); }
  }
}
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
function fileHtml(f, m) {
  const src = encodeURIComponent(f.src || "");
  if (f.kind === "image") return `<img loading="lazy" alt="${esc(f.name || "Bild")}" src="${API}/image?src=${src}">`;
  if (f.kind === "audio") {
    const t = S.voice[f.src];
    return `<div class="voice"><span class="ms">mic</span><div class="vt"><b>Sprachnachricht${f.duration ? ` · ${mmss(f.duration)}` : ""}</b>${t ? `<span class="vtext">${esc(t)}</span>` : `<button class="mini" data-a="voice" data-src="${esc(f.src)}">Als Text anzeigen</button>`}</div></div>`;
  }
  if (f.kind === "video") return `<div class="file"><span class="ms">${f.gif ? "gif_box" : "videocam"}</span>${f.gif ? "GIF" : "Video"}${f.duration ? ` · ${mmss(f.duration)}` : ""} <small>(in Beeper ansehen)</small></div>`;
  return `<div class="file"><span class="ms">attach_file</span>${esc(f.name || "Datei")}</div>`;
}
function bubble(m) {
  const files = m.files.map((f) => fileHtml(f, m)).join("");
  const body = m.deleted ? "Nachricht gelöscht" : m.text ? `<span class="tx">${autolink(esc(m.text))}</span>` : "";
  const group = cur()?.type === "group" && !m.mine ? `<span class="who">${esc(clean(m.sender))}</span>` : "";
  const q = m.reply_to && S.msgs.find((x) => x.id === m.reply_to);
  const quote = m.reply_to ? `<div class="quote" data-a="jump" data-id="${esc(m.reply_to)}"><b>${esc(q ? (q.mine ? "Du" : clean(q.sender)) : "Antwort")}</b>${esc(q ? (q.text || (q.files[0] ? "Anhang" : "")).slice(0, 120) : "Nachricht")}</div>` : "";
  const rx = m.reactions?.length ? `<div class="rx">${m.reactions.map((r) => `<span class="${r.mine ? "mine" : ""}">${esc(r.key)}${r.n > 1 ? ` ${r.n}` : ""}</span>`).join("")}</div>` : "";
  const tools = m.pending || m.deleted ? "" : `<button class="rep" data-a="reply" data-id="${esc(m.id)}" aria-label="Antworten" title="Antworten"><span class="ms">reply</span></button>`;
  return `<div class="bw ${m.mine ? "me" : ""}" id="m-${esc(m.id)}">${tools}<div class="b ${m.mine ? "me" : ""} ${m.pending ? "pending" : ""} ${m.deleted ? "del" : ""}">${group}${quote}${files}${body}${rx}<span class="tm">${m.at ? fmtTime.format(new Date(m.at)) : "…"}</span></div></div>`;
}
function drawMsgs({ stick = false, keepTop = false } = {}) {
  const box = $("#msgs"); if (!box) return;
  const prevH = box.scrollHeight, prevTop = box.scrollTop;
  let last = "", html = S.more ? '<button class="older" data-a="older">Ältere laden</button>' : "";
  if (!S.msgs.length) html += '<div class="empty">Noch keine Nachrichten.</div>';
  for (const m of S.msgs) {
    const k = m.at ? dayKey(m.at) : last;
    if (k !== last) { last = k; html += `<div class="day">${esc(fmtDay.format(new Date(m.at)))}</div>`; }
    html += bubble(m);
  }
  box.innerHTML = html;
  if (keepTop) box.scrollTop = box.scrollHeight - prevH + prevTop;
  else if (stick) { box.scrollTop = box.scrollHeight; box.querySelectorAll("img").forEach((i) => i.addEventListener("load", () => { box.scrollTop = box.scrollHeight; }, { once: true })); }
  else box.scrollTop = prevTop;
}
$("#conv").addEventListener("click", (e) => {
  const a = e.target.closest("[data-a]");
  if (!a) return;
  if (a.dataset.a === "back") return history.length > 1 ? history.back() : go(null);
  if (a.dataset.a === "older") return loadMsgs({ older: true });
  if (a.dataset.a === "reply") return setReply(S.msgs.find((m) => m.id === a.dataset.id));
  if (a.dataset.a === "cancel") return setReply(null);
  if (a.dataset.a === "jump") { const el = document.getElementById(`m-${a.dataset.id}`); if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200); } return; }
  if (a.dataset.a === "voice") {
    a.disabled = true; a.textContent = "Wird umgewandelt …";
    return api(`/transcript?src=${encodeURIComponent(a.dataset.src)}`).then((d) => { S.voice[a.dataset.src] = d.text || "(kein Text erkannt)"; drawMsgs(); }, (e) => { a.disabled = false; a.textContent = "Als Text anzeigen"; toast(e.message); });
  }
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
  ta.value = ""; ta.style.height = "auto";
  const rep = S.reply; setReply(null);
  try {
    await api(`/chats/${encodeURIComponent(S.id)}/send`, { method: "POST", body: JSON.stringify({ text, reply_to: S.reply?.id || null }) });
    setTimeout(() => loadMsgs({ quiet: true }), 600);
    const c = cur(); if (c) { c.at = new Date().toISOString(); c.unread = 0; }
    loadChats({ quiet: true });
  } catch (e) {
    S.msgs = S.msgs.filter((m) => m !== tmp); drawMsgs(); ta.value = text;
    setReply(rep); toast(`Nicht gesendet: ${e.message}`);
  }
  S.sending = false; $("#sendBtn").disabled = false; ta.focus();
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
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return live(false);
  loadChats({ quiet: true }); if (S.id) loadMsgs({ quiet: true });
  live(true);
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
route();
loadChats();
live(!document.hidden);
