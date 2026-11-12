// IDA Mail: Gmail im IDA-Stil. Ordner, Liste, Lesen (HTML abgeschottet), Schreiben/Antworten/Weiterleiten, live per SSE.
import { esc, autolink } from "./shared/js/dom.js";

const API = "/api/v1";
const $ = (id) => document.getElementById(id);
const S = { me: "", sendAs: [], labels: [], label: "INBOX", q: "", items: [], next: null, thread: null, tid: null, images: new Set(), contacts: [] };
const COLORS = ["rot", "orange", "gelb", "salbei", "gruen", "tuerkis", "blau", "lavendel", "lila", "koralle"];

// ---------------- Hilfen ----------------
const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
async function api(path, opts = {}) {
  // Lesende Anfragen versucht die Seite bei kurzen Aussetzern (Gmail bremst, Tunnel hakt) selbst noch zweimal, bevor ein Fehler kommt.
  const tries = (opts.method || "GET") === "GET" ? 3 : 1;
  let r;
  for (let i = 0; ; i++) {
    try {
      r = await fetch(API + path, { credentials: "same-origin", cache: "no-store", redirect: "manual", ...opts });
    } catch (e) {
      if (e.name === "AbortError" || i + 1 >= tries) throw e.name === "AbortError" ? e : new Error("Keine Verbindung – nochmal versuchen");
      await wait(700 * (i + 1)); continue;
    }
    if (![502, 503, 504].includes(r.status) || i + 1 >= tries) break;
    await wait(Math.min(5, Number(r.headers.get("retry-after")) || 0) * 1000 || 700 * (i + 1));
    if (opts.signal?.aborted) throw new DOMException("abgebrochen", "AbortError");
  }
  if (r.type === "opaqueredirect" || r.status === 401 || r.status === 403) throw Object.assign(new Error("Anmeldung abgelaufen – Seite neu laden"), { login: true });
  const d = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) throw Object.assign(new Error(d?.detail || `Fehler ${r.status}`), { status: r.status });
  return d;
}
const post = (path, body) => api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });

let toastTimer;
function toast(text, action) {
  let t = document.querySelector(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.innerHTML = esc(text) + (action ? ` <button class="toast-act">${esc(action.label)}</button>` : "");
  t.style.pointerEvents = action ? "auto" : "none";
  if (action) t.querySelector(".toast-act").onclick = () => { t.classList.remove("show"); action.run(); };
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), action ? 5000 : 2600);
}
const fail = (e) => toast(e.message || String(e));

const fmtTime = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const fmtDay = new Intl.DateTimeFormat("de-DE", { day: "numeric", month: "short" });
const fmtFull = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
function when(ms) {
  const d = new Date(ms), now = new Date();
  if (d.toDateString() === now.toDateString()) return fmtTime.format(d);
  if (d.getFullYear() === now.getFullYear()) return fmtDay.format(d);
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit" });
}
function color(key) {
  let h = 0;
  for (const c of String(key || "?")) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const c = COLORS[h % COLORS.length];
  return `background:var(--evc-${c});color:var(--ev-${c})`;
}
const avatar = (name, key) => `<span class="av" style="${color(key || name)}">${esc((name || "?").replace(/[^\p{L}\p{N}]/gu, "")[0] || "?")}</span>`;
const size = (n) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n > 1e3 ? `${Math.round(n / 1e3)} KB` : `${n} B`);
const fileIcon = (t) => (t.startsWith("image/") ? "image" : t === "application/pdf" ? "picture_as_pdf" : t.startsWith("audio/") ? "audio_file" : t.startsWith("video/") ? "video_file" : /zip|compressed/.test(t) ? "folder_zip" : "description");
const addr = (p) => (p.name && p.name !== p.email.split("@")[0] ? `${p.name} <${p.email}>` : p.email);

// ---------------- Adresse (Ordner, Suche, offene Mail) ----------------
// Pfade: "/" Posteingang, "/l/<Ordner>", "/t/<Mail>", Suche als "?q=" (bei offener Mail zusätzlich "?l=<Ordner>")
const pathFor = ({ label, q, t }) => {
  const h = new URLSearchParams();
  if (q) h.set("q", q);
  if (t && label !== "INBOX") h.set("l", label);
  const base = t ? `/t/${encodeURIComponent(t)}` : label !== "INBOX" ? `/l/${encodeURIComponent(label)}` : "/";
  return base + (h.size ? `?${h}` : "");
};
function readHash() {
  const p = location.pathname, h = new URLSearchParams(location.search);
  const dec = (x) => { try { return decodeURIComponent(x); } catch { return x; } };
  let m;
  S.q = h.get("q") || ""; S.tid = null; S.label = "INBOX";
  if ((m = /^\/t\/([^/]+)/.exec(p))) { S.tid = dec(m[1]); S.label = h.get("l") || "INBOX"; }
  else if ((m = /^\/l\/([^/]+)/.exec(p))) S.label = dec(m[1]);
}
function setHash({ label = S.label, q = S.q, t = null } = {}, push = true) {
  const url = pathFor({ label, q, t });
  if (url !== location.pathname + location.search) history[push ? "pushState" : "replaceState"](null, "", url);
  route();
}
window.addEventListener("popstate", route);

let lastList = "";
function route() {
  const composing = document.body.classList.contains("composing");
  if (location.pathname === "/compose") {   // "Neue Mail" hat eine eigene Adresse
    if (!composing) { const q = new URLSearchParams(location.search); compose("new", null, { to: q.get("to") || "", subject: q.get("subject") || "" }); }
    return;
  }
  if (composing) {   // Zurück-Taste o. Ä.: angefangene Mail als Entwurf sichern, dann schließen
    if (C.dirty && ($("cText").value.trim() || $("cTo").value.trim())) submit(true);
    document.body.classList.remove("composing"); C.pushed = false;
  }
  if (location.hash.length > 1) {   // alte Adresse mit #: auf den neuen Pfad umstellen
    const h = new URLSearchParams(location.hash.slice(1));
    history.replaceState(null, "", pathFor({ label: h.get("l") || "INBOX", q: h.get("q") || "", t: h.get("t") }));
  }
  const before = lastList;
  readHash();
  $("q").value = S.q;
  lastList = `${S.label}|${S.q}`;
  drawLabels();
  if (lastList !== before) loadList();
  if (S.tid) openThread(S.tid); else closeReader();
  document.body.classList.remove("nav-open");
}

// ---------------- Ordner ----------------
async function loadLabels() {
  try {
    S.labels = await api("/labels");
    drawLabels();
  } catch (e) {
    if (e.status === 409) showSetup(e.message);
  }
}
function drawLabels() {
  const sys = S.labels.filter((l) => l.system), own = S.labels.filter((l) => !l.system);
  const a = (l, extra = "") => `<a href="${pathFor({ label: l.id, q: "", t: null })}" class="${S.label === l.id && !S.q ? "on" : ""}" data-l="${esc(l.id)}">
    ${extra || `<span class="ms">${l.icon}</span>`}<span class="n">${esc(l.name)}</span>${l.unread && !["SENT", "TRASH", "DRAFT"].includes(l.id) ? `<b>${l.unread}</b>` : l.id === "DRAFT" && l.total ? `<b>${l.total}</b>` : ""}</a>`;
  $("labels").innerHTML = sys.map((l) => a(l)).join("") + a({ id: "ALL", name: "Alle Mails", icon: "all_inbox" })
    + (own.length ? `<div class="sep">Labels</div>` + own.map((l) => a(l, `<span class="dotc" style="background:${esc(l.color || "#8e9099")}"></span>`)).join("") : "");
  const cur = S.labels.find((l) => l.id === S.label);
  $("labelName").textContent = S.q ? `Suche: ${S.q}` : S.label === "ALL" ? "Alle Mails" : cur?.name || "Posteingang";
  const inbox = S.labels.find((l) => l.id === "INBOX")?.unread || 0;
  document.title = inbox ? `(${inbox}) Mail` : "Mail";
  if (navigator.setAppBadge) (inbox ? navigator.setAppBadge(inbox) : navigator.clearAppBadge()).catch(() => {});
}
$("labels").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-l]");
  if (!a) return;
  e.preventDefault();
  setHash({ label: a.dataset.l, q: "" });
});

// ---------------- Liste ----------------
let listSeq = 0, listAbort;
async function loadList({ more = false, quiet = false } = {}) {
  const seq = ++listSeq;
  listAbort?.abort(); listAbort = new AbortController();
  const signal = listAbort.signal;
  if (!more && !quiet) $("list").innerHTML = '<div class="skel"></div>'.repeat(6);
  try {
    const p = new URLSearchParams({ label: S.q ? "ALL" : S.label, q: S.q });
    if (more && S.next) p.set("page", S.next);
    const d = await api(`/threads?${p}`, { signal });
    if (seq !== listSeq) return;
    S.items = more ? S.items.concat(d.items) : d.items;
    S.next = d.next;
    drawList();
  } catch (e) {
    if (seq !== listSeq || e.name === "AbortError") return;
    if (e.status === 409) return showSetup(e.message);
    $("list").innerHTML = `<div class="empty"><span class="ms">cloud_off</span>${esc(e.message)}<br><button class="es-btn primary" onclick="location.reload()">Nochmal versuchen</button></div>`;
  }
}
function drawList() {
  if (!S.items.length) {
    $("list").innerHTML = `<div class="empty"><span class="ms">${S.q ? "search_off" : "inbox"}</span>${S.q ? "Nichts gefunden." : "Hier ist nichts."}</div>`;
    return;
  }
  $("list").innerHTML = S.items.map((t) => `<div class="trow ${t.unread ? "unread" : ""} ${t.id === S.tid ? "sel" : ""}" data-t="${esc(t.id)}">
      ${avatar(t.from.find((n) => n !== "Ich") || t.from[0] || "?")}
      <div class="tmain"><div class="ttop"><span class="tfrom">${t.draft && t.from.length <= 1 ? '<span class="tdraft">Entwurf</span>' : esc(t.from.join(", "))}${t.count > 1 ? `<span class="cnt">${t.count}</span>` : ""}</span><span class="tdate">${when(t.date)}</span></div>
        <div class="tsub">${esc(t.subject || "(kein Betreff)")}</div><div class="tsnip">${esc(t.snippet)}</div></div>
      <button class="tstar ${t.starred ? "on" : ""}" data-star="${esc(t.id)}" aria-label="Markieren"><span class="ms ${t.starred ? "" : "fill0"}">star</span></button></div>`).join("")
    + (S.next ? `<div class="more"><button id="moreBtn"><span class="ms">expand_more</span>Mehr laden</button></div>` : "");
  $("moreBtn")?.addEventListener("click", () => loadList({ more: true }));
}
$("list").addEventListener("click", async (e) => {
  const star = e.target.closest("[data-star]");
  if (star) {
    e.stopPropagation();
    const t = S.items.find((x) => x.id === star.dataset.star);
    if (!t) return;
    t.starred = !t.starred; drawList();
    post(`/threads/${t.id}/modify`, t.starred ? { add: ["STARRED"] } : { remove: ["STARRED"] }).catch(fail);
    return;
  }
  const row = e.target.closest("[data-t]");
  if (row) setHash({ t: row.dataset.t });
});

// ---------------- Lesen ----------------
function closeReader() {
  S.thread = null;
  document.body.classList.remove("reading");
  $("reader").innerHTML = `<div class="reader-empty"><span class="ms">mail</span><p>Wähle eine Mail aus.</p></div>`;
  document.querySelectorAll(".trow.sel").forEach((r) => r.classList.remove("sel"));
}
let threadSeq = 0, threadAbort;
async function openThread(tid, { quiet = false } = {}) {
  const seq = ++threadSeq;
  threadAbort?.abort(); threadAbort = new AbortController();
  const signal = threadAbort.signal;
  document.body.classList.add("reading");
  document.querySelectorAll(".trow").forEach((r) => r.classList.toggle("sel", r.dataset.t === tid));
  if (!quiet && S.thread?.id !== tid) $("reader").innerHTML = `<div class="rbody"><div class="skel" style="height:40px;margin-top:70px"></div><div class="skel" style="height:240px"></div></div>`;
  try {
    const t = await api(`/threads/${encodeURIComponent(tid)}`, { signal });
    if (seq !== threadSeq) return;
    // live: nur neu zeichnen, wenn eine Mail dazukam/wegfiel – sonst bleiben aufgeklappte Mails, Scrollstand und Bilder, wie sie sind
    const same = quiet && S.thread?.id === t.id && S.thread.messages.map((m) => m.id).join() === t.messages.map((m) => m.id).join();
    S.thread = t;
    if (same) return;
    drawThread();
    if (t.unread) {   // geöffnet = gelesen
      post(`/threads/${tid}/modify`, { remove: ["UNREAD"] }).then(() => { const i = S.items.find((x) => x.id === tid); if (i) { i.unread = false; drawList(); } loadLabels(); }).catch(() => {});
    }
  } catch (e) {
    if (seq !== threadSeq) return;
    $("reader").innerHTML = `${readerBar(false)}<div class="empty"><span class="ms">error</span>${esc(e.message)}</div>`;
  }
}
const readerBar = (actions = true) => `<div class="rbar"><button class="icon-btn back-btn" data-act="back" aria-label="Zurück"><span class="ms">arrow_back</span></button>
  ${actions ? `<button class="icon-btn" data-act="archive" title="Archivieren" aria-label="Archivieren"><span class="ms">archive</span></button>
  <button class="icon-btn" data-act="trash" title="${S.thread?.labels.includes("TRASH") ? "Wiederherstellen" : "Löschen"}"><span class="ms">${S.thread?.labels.includes("TRASH") ? "restore_from_trash" : "delete"}</span></button>
  <button class="icon-btn" data-act="unread" title="Als ungelesen markieren" aria-label="Als ungelesen markieren"><span class="ms">mark_email_unread</span></button>
  <button class="icon-btn" data-act="spam" title="${S.thread?.labels.includes("SPAM") ? "Kein Spam" : "Spam"}"><span class="ms">${S.thread?.labels.includes("SPAM") ? "report_off" : "report"}</span></button>
  <span class="sp"></span>
  <button class="icon-btn" data-act="star" title="Markieren" aria-label="Markieren"><span class="ms ${S.thread?.starred ? "" : "fill0"}" style="${S.thread?.starred ? "color:var(--ev-gelb)" : ""}">star</span></button>` : ""}</div>`;

function drawThread() {
  const t = S.thread;
  const names = Object.fromEntries(S.labels.map((l) => [l.id, l.name]));
  const chips = t.labels.filter((l) => names[l] && !["INBOX", "UNREAD", "STARRED", "IMPORTANT", "SENT", "DRAFT"].includes(l)).map((l) => `<span class="chip">${esc(names[l])}</span>`).join("");
  const last = t.messages.length - 1;
  $("reader").innerHTML = readerBar() + `<div class="rbody"><h2 class="rsub">${esc(t.subject || "(kein Betreff)")}</h2><div class="rlabels">${chips}</div>
    ${t.messages.map((m, i) => msgHtml(m, i !== last && !m.unread && !m.draft_id)).join("")}
    <div class="replybar"><button class="es-btn primary" data-act="reply"><span class="ms">reply</span>Antworten</button>
      ${t.messages[last] && t.messages[last].to.length + t.messages[last].cc.length > 1 ? `<button class="es-btn" data-act="replyall"><span class="ms">reply_all</span>Allen antworten</button>` : ""}
      <button class="es-btn" data-act="forward"><span class="ms">forward</span>Weiterleiten</button></div></div>`;
  t.messages.forEach((m) => { if (m.html) frame(m); });
  $("reader").scrollTop = 0;
}

function msgHtml(m, closed) {
  const to = [...m.to, ...m.cc].map((p) => (p.email.toLowerCase() === S.me ? "mich" : p.name)).join(", ");
  const att = m.attachments.map((a) => `<a class="att" href="${API}/messages/${m.id}/attachments/${a.id}?name=${encodeURIComponent(a.name)}&type=${encodeURIComponent(a.type)}" target="_blank" rel="noopener">
      <span class="ms">${fileIcon(a.type)}</span><span>${esc(a.name)}</span><small>${size(a.size)}</small></a>`).join("");
  return `<article class="msg ${closed ? "closed" : ""}" data-m="${esc(m.id)}">
    <div class="mhead" data-act="toggle">${avatar(m.from.name, m.from.email)}<div class="mwho">
      <div class="mname">${esc(m.from.name)}<small>${esc(m.from.email)}</small></div>
      <div class="mto">${m.draft_id ? '<span class="tdraft">Entwurf</span> · ' : ""}an ${esc(to || "–")}</div><div class="msnip">${esc(m.snippet)}</div></div>
      <div class="mdate" title="${esc(fmtFull.format(new Date(m.date)))}">${when(m.date)}</div></div>
    <div class="mbody">${m.html ? `<div class="imgs" hidden><span class="ms">image_not_supported</span>Bilder von außen sind blockiert.<button data-act="images">Bilder anzeigen</button></div><iframe class="mframe" sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" title="Mail"></iframe>` : `<div class="mtext">${plain(m.text)}</div>`}</div>
    ${att ? `<div class="matt">${att}</div>` : ""}
    ${m.draft_id ? `<div class="mact"><button class="pill" data-act="editdraft"><span class="ms">edit</span>Entwurf bearbeiten</button><button class="pill" data-act="deldraft"><span class="ms">delete</span>Verwerfen</button></div>` : ""}
  </article>`;
}

// Text-Mail: Zitate (Zeilen mit „>“) eingerückt, Links anklickbar
function plain(text) {
  const out = [];
  let quote = [];
  const flush = () => { if (quote.length) out.push(`<blockquote>${autolink(esc(quote.join("\n")))}</blockquote>`); quote = []; };
  for (const line of String(text || "").replace(/\r/g, "").split("\n")) {
    if (/^\s*>/.test(line)) quote.push(line.replace(/^\s*> ?/, ""));
    else { flush(); out.push(autolink(esc(line))); }
  }
  flush();
  return out.join("\n").replace(/\n?(<blockquote>)/g, "$1").replace(/(<\/blockquote>)\n/g, "$1");
}

// HTML-Mail in einem abgeschotteten Rahmen: keine Skripte, keine Formulare, fremde Bilder erst auf Wunsch, Links in neuem Tab
function frame(m) {
  const box = document.querySelector(`.msg[data-m="${CSS.escape(m.id)}"]`);
  const f = box?.querySelector("iframe");
  if (!f) return;
  const allow = S.images.has(m.id);
  const remote = /<img[^>]+src\s*=\s*["']?https?:|url\(\s*["']?https?:|background\s*=\s*["']?https?:/i.test(m.html);
  box.querySelector(".imgs").hidden = allow || !remote;
  const ext = allow ? " https: http:" : "";
  const csp = `default-src 'none'; img-src 'self' data:${ext}; style-src 'unsafe-inline'${ext}; font-src data:${ext}; media-src 'none'`;
  const html = m.html
    .replace(/<meta[^>]+http-equiv\s*=\s*["']?refresh[^>]*>/gi, "")
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/cid:([^"'\s)>]+)/gi, (all, cid) => (m.inline[cid] ? `${API}/messages/${m.id}/attachments/${m.inline[cid]}?type=image%2Fpng` : all));
  f.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}"><base target="_blank">
    <style>html{background:#fff}body{margin:0;padding:16px;color:#1b1b1f;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;overflow-wrap:anywhere}
    img{max-width:100%;height:auto}table{max-width:100%!important}pre{white-space:pre-wrap}a{color:#a33a00}</style></head><body>${html}</body></html>`;
  f.onload = () => {
    const fit = () => { try { f.style.height = `${f.contentDocument.documentElement.scrollHeight + 2}px`; } catch { f.style.height = "70vh"; } };
    fit();
    try { new ResizeObserver(fit).observe(f.contentDocument.body); } catch { /* egal */ }
  };
}

$("reader").addEventListener("click", async (e) => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  const act = el.dataset.act, t = S.thread;
  const msgEl = el.closest(".msg"), m = msgEl && t?.messages.find((x) => x.id === msgEl.dataset.m);
  if (act === "back") return history.length > 1 ? history.back() : setHash({ t: null });
  if (act === "toggle") {
    if (e.target.closest("a")) return;
    msgEl.classList.toggle("closed");
    if (!msgEl.classList.contains("closed") && m?.html) frame(m);
    return;
  }
  if (act === "images") { S.images.add(m.id); return frame(m); }
  if (!t) return;
  if (act === "reply" || act === "replyall" || act === "forward") return compose(act, t.messages[t.messages.length - 1]);
  if (act === "editdraft") return compose("draft", m);
  if (act === "deldraft") {
    await api(`/drafts/${m.draft_id}`, { method: "DELETE" }).catch(fail);
    toast("Entwurf verworfen");
    return t.messages.length > 1 ? openThread(t.id, { quiet: true }) : leave();
  }
  if (act === "star") {
    t.starred = !t.starred; drawThread();
    return post(`/threads/${t.id}/modify`, t.starred ? { add: ["STARRED"] } : { remove: ["STARRED"] }).then(() => loadList({ quiet: true })).catch(fail);
  }
  const ops = {
    archive: [() => post(`/threads/${t.id}/modify`, { remove: ["INBOX"] }), () => post(`/threads/${t.id}/modify`, { add: ["INBOX"] }), "Archiviert"],
    trash: t.labels.includes("TRASH")
      ? [() => post(`/threads/${t.id}/trash?undo=true`), () => post(`/threads/${t.id}/trash`), "Wiederhergestellt"]
      : [() => post(`/threads/${t.id}/trash`), () => post(`/threads/${t.id}/trash?undo=true`), "In den Papierkorb gelegt"],
    unread: [() => post(`/threads/${t.id}/modify`, { add: ["UNREAD"] }), () => post(`/threads/${t.id}/modify`, { remove: ["UNREAD"] }), "Als ungelesen markiert"],
    spam: t.labels.includes("SPAM")
      ? [() => post(`/threads/${t.id}/modify`, { remove: ["SPAM"], add: ["INBOX"] }), () => post(`/threads/${t.id}/modify`, { add: ["SPAM"], remove: ["INBOX"] }), "Kein Spam mehr"]
      : [() => post(`/threads/${t.id}/modify`, { add: ["SPAM"], remove: ["INBOX"] }), () => post(`/threads/${t.id}/modify`, { remove: ["SPAM"], add: ["INBOX"] }), "Als Spam markiert"],
  }[act];
  if (!ops) return;
  try {
    await ops[0]();
    const tid = t.id;
    S.items = S.items.filter((x) => x.id !== tid);
    drawList();
    leave();
    toast(ops[2], { label: "Rückgängig", run: () => ops[1]().then(() => { loadList({ quiet: true }); loadLabels(); }).catch(fail) });
    loadLabels();
  } catch (err) { fail(err); }
});
function leave() { setHash({ t: null }, false); }

// ---------------- Schreiben ----------------
const C = { mode: "new", files: [], fwd: [], skip: new Set(), draftId: "", threadId: "", inReplyTo: "", references: "", fwdFrom: "", dirty: false };
function quoteText(m) {
  const head = `Am ${fmtFull.format(new Date(m.date))} schrieb ${addr(m.from)}:`;
  const body = (m.text || htmlToText(m.html)).trim().split("\n").map((l) => `> ${l}`).join("\n");
  return `\n\n${head}\n${body}`;
}
function htmlToText(html) {
  const d = new DOMParser().parseFromString(String(html || "").replace(/<(br|\/p|\/div|\/tr|\/h\d|\/li)\b[^>]*>/gi, "$&\n"), "text/html");
  d.querySelectorAll("style,script,head").forEach((x) => x.remove());
  return (d.body?.textContent || "").replace(/\n{3,}/g, "\n\n");
}
const reSubject = (s, pre) => (new RegExp(`^${pre}:`, "i").test(s || "") ? s : `${pre}: ${s || ""}`);

function compose(mode = "new", m = null, prefill = {}) {
  Object.assign(C, { mode, files: [], fwd: [], skip: new Set(), draftId: "", threadId: "", inReplyTo: "", references: "", fwdFrom: "", dirty: false });
  let to = prefill.to || "", cc = "", subject = prefill.subject || "", text = prefill.text || "", title = "Neue Mail";
  if (m && (mode === "reply" || mode === "replyall")) {
    const mine = m.from.email.toLowerCase() === S.me;
    to = mine ? m.to.map(addr).join(", ") : m.reply_to || addr(m.from);
    if (mode === "replyall") cc = [...m.to, ...m.cc].filter((p) => p.email.toLowerCase() !== S.me && p.email !== m.from.email).map(addr).join(", ");
    subject = reSubject(m.subject, "Re"); text = quoteText(m); title = mode === "replyall" ? "Allen antworten" : "Antworten";
    Object.assign(C, { threadId: m.thread_id, inReplyTo: m.message_id, references: m.references });
  } else if (m && mode === "forward") {
    subject = reSubject(m.subject, "WG"); title = "Weiterleiten";
    text = `\n\n---------- Weitergeleitete Nachricht ----------\nVon: ${addr(m.from)}\nDatum: ${fmtFull.format(new Date(m.date))}\nBetreff: ${m.subject}\nAn: ${m.to.map(addr).join(", ")}\n\n${(m.text || htmlToText(m.html)).trim()}`;
    C.fwd = m.attachments; C.fwdFrom = m.id;
  } else if (m && mode === "draft") {
    to = m.to.map(addr).join(", "); cc = m.cc.map(addr).join(", "); subject = m.subject; text = m.text || htmlToText(m.html); title = "Entwurf";
    Object.assign(C, { draftId: m.draft_id, threadId: S.thread?.messages.length > 1 ? m.thread_id : "" });
  }
  const lbl = (a) => (a.name ? `${a.name} <${a.email}>` : a.email);
  const def = S.sendAs.find((a) => a.default) || S.sendAs[0];
  const from = S.sendAs.length > 1 ? `<div class="cf"><label>Von</label><div class="dd" id="cFromDd"><input type="hidden" id="cFrom" value="${esc(lbl(def))}">
      <button type="button" class="dd-btn" aria-haspopup="listbox" aria-expanded="false"><span class="dd-val">${esc(lbl(def))}</span><span class="ms">expand_more</span></button>
      <div class="dd-menu" role="listbox" hidden>${S.sendAs.map((a) => `<button type="button" role="option" class="dd-opt ${a === def ? "on" : ""}" data-v="${esc(lbl(a))}"><span class="dd-t"><b>${esc(a.name || a.email.split("@")[0])}</b><small>${esc(a.email)}</small></span><span class="ms">${a === def ? "check" : ""}</span></button>`).join("")}</div></div></div>` : "";
  $("compose").innerHTML = `<div class="sug" id="sug" hidden></div><div class="chead"><h2>${title}</h2><button type="button" class="icon-btn" data-c="close" aria-label="Schließen"><span class="ms">close</span></button></div>
    <div class="cfields">${from}
      <div class="cf"><label for="cTo">An</label><input id="cTo" data-ppl value="${esc(to)}" autocomplete="off" inputmode="email"><button type="button" class="tog" data-c="cc">Cc/Bcc</button></div>
      <div class="cf" id="cCcRow" ${cc ? "" : "hidden"}><label for="cCc">Cc</label><input id="cCc" data-ppl value="${esc(cc)}" autocomplete="off"></div>
      <div class="cf" id="cBccRow" hidden><label for="cBcc">Bcc</label><input id="cBcc" data-ppl autocomplete="off"></div>
      <div class="cf"><label for="cSub">Betreff</label><input id="cSub" value="${esc(subject)}"></div></div>
    <textarea id="cText" placeholder="Nachricht">${esc(text)}</textarea>
    <div class="cfiles" id="cFiles"></div>
    <div class="cfoot"><button type="submit" class="es-btn primary" id="cSend"><span class="ms">send</span>Senden</button>
      <label class="icon-btn" title="Anhang" aria-label="Anhang"><span class="ms">attach_file</span><input type="file" id="cFile" multiple hidden></label>
      <span class="sp"></span><span class="hint" id="cHint"></span>
      <button type="button" class="icon-btn" data-c="draft" title="Als Entwurf speichern" aria-label="Als Entwurf speichern"><span class="ms">save</span></button>
      <button type="button" class="icon-btn" data-c="discard" title="Verwerfen" aria-label="Verwerfen"><span class="ms">delete</span></button></div>`;
  drawFiles();
  document.body.classList.add("composing");
  if (mode === "new" && location.pathname !== "/compose") { history.pushState(null, "", "/compose"); C.pushed = true; }
  const ta = $("cText");
  setTimeout(() => {
    if (!to) $("cTo").focus();
    else { ta.focus(); ta.setSelectionRange(0, 0); ta.scrollTop = 0; }
  }, 50);
}
function drawFiles() {
  const own = C.files.map((f, i) => `<span class="att"><span class="ms">${fileIcon(f.type || "")}</span><span>${esc(f.name)}</span><small>${size(f.size)}</small><button type="button" data-rm="${i}" aria-label="Entfernen"><span class="ms">close</span></button></span>`);
  const fwd = C.fwd.map((a) => `<span class="att ${C.skip.has(a.id) ? "off" : ""}"><span class="ms">${fileIcon(a.type)}</span><span>${esc(a.name)}</span><small>${size(a.size)}</small><button type="button" data-fw="${esc(a.id)}" aria-label="An/aus"><span class="ms">${C.skip.has(a.id) ? "undo" : "close"}</span></button></span>`);
  $("cFiles").innerHTML = own.concat(fwd).join("");
}
// ---------------- Eigenes Dropdown (Von) ----------------
document.addEventListener("click", (e) => {
  const dd = e.target.closest(".dd");
  document.querySelectorAll(".dd.open").forEach((d) => { if (d !== dd) { d.classList.remove("open"); d.querySelector(".dd-menu").hidden = true; d.querySelector(".dd-btn").setAttribute("aria-expanded", "false"); } });
  if (!dd) return;
  const opt = e.target.closest(".dd-opt");
  if (opt) {
    dd.querySelector("input").value = opt.dataset.v;
    dd.querySelector(".dd-val").textContent = opt.dataset.v;
    dd.querySelectorAll(".dd-opt").forEach((o) => { o.classList.toggle("on", o === opt); o.querySelector(".ms").textContent = o === opt ? "check" : ""; });
    C.dirty = true;
  } else if (!e.target.closest(".dd-btn")) return;
  const open = !opt && !dd.classList.contains("open");
  dd.classList.toggle("open", open);
  dd.querySelector(".dd-menu").hidden = !open;
  dd.querySelector(".dd-btn").setAttribute("aria-expanded", String(open));
});

// ---------------- Adressvorschläge ----------------
let contactsP = null, sugIdx = -1;
const loadContacts = () => (contactsP ||= api("/contacts").then((l) => { S.contacts = l; return l; }).catch(() => { contactsP = null; return []; }));
const norm = (x) => x.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
function suggest(input) {
  const box = $("sug");
  const parts = input.value.split(","), cur = parts[parts.length - 1].trim();
  if (!cur || cur.includes("<")) return hideSug();
  const q = norm(cur);
  const hits = (S.contacts || []).filter((c) => norm(`${c.name} ${c.email}`).includes(q)).slice(0, 6);
  if (!hits.length) return hideSug();
  sugIdx = 0;
  box.innerHTML = hits.map((c, i) => `<button type="button" class="sug-i ${i ? "" : "on"}" data-v="${esc(addr(c))}">${avatar(c.name || c.email, c.email)}<span class="dd-t"><b>${esc(c.name || c.email)}</b>${c.name ? `<small>${esc(c.email)}</small>` : ""}</span></button>`).join("");
  const r = input.getBoundingClientRect(), host = $("compose").getBoundingClientRect();
  box.style.top = `${r.bottom - host.top}px`;
  box.hidden = false; box.target = input;
}
function hideSug() { const b = $("sug"); if (b) { b.hidden = true; b.target = null; } sugIdx = -1; }
function pick(v) {
  const input = $("sug").target; if (!input) return;
  const parts = input.value.split(",").slice(0, -1).map((x) => x.trim()).filter(Boolean);
  input.value = [...parts, v].join(", ") + ", ";
  hideSug(); C.dirty = true; input.focus();
}
$("compose").addEventListener("input", (e) => { if (e.target.matches("[data-ppl]")) { loadContacts().then(() => e.target === document.activeElement && suggest(e.target)); } });
$("compose").addEventListener("focusin", (e) => { if (e.target.matches("[data-ppl]")) loadContacts(); });
$("compose").addEventListener("focusout", (e) => { if (e.target.matches("[data-ppl]")) setTimeout(() => { if (!document.activeElement?.closest?.("#sug")) hideSug(); }, 150); });
$("compose").addEventListener("keydown", (e) => {
  const box = $("sug");
  if (box.hidden || !e.target.matches("[data-ppl]")) return;
  const items = [...box.children];
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    sugIdx = (sugIdx + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items.forEach((x, i) => x.classList.toggle("on", i === sugIdx));
  } else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(items[sugIdx]?.dataset.v || items[0].dataset.v); }
  else if (e.key === "Escape") { e.stopPropagation(); hideSug(); }
}, true);
$("compose").addEventListener("pointerdown", (e) => { const i = e.target.closest(".sug-i"); if (i) { e.preventDefault(); pick(i.dataset.v); } });

function closeCompose() {
  document.body.classList.remove("composing");
  if (location.pathname !== "/compose") return;
  if (C.pushed) { C.pushed = false; history.back(); }   // zurück zur Seite davor (popstate lädt sie)
  else { history.replaceState(null, "", "/"); route(); }
}
function formData(draft) {
  const fd = new FormData();
  const v = (id) => $(id)?.value.trim() || "";
  fd.set("to", v("cTo")); fd.set("cc", v("cCc")); fd.set("bcc", v("cBcc")); fd.set("subject", $("cSub").value); fd.set("text", $("cText").value);
  fd.set("sender", v("cFrom")); fd.set("thread_id", C.threadId || ""); fd.set("in_reply_to", C.inReplyTo || ""); fd.set("references", C.references || "");
  fd.set("draft_id", C.draftId || ""); fd.set("forward_from", C.fwdFrom || ""); fd.set("forward_skip", [...C.skip].join(","));
  if (draft) fd.set("draft", "true");
  for (const f of C.files) fd.append("files", f, f.name);
  return fd;
}
async function submit(draft) {
  const btn = $("cSend");
  if (!draft && !$("cTo").value.trim() && !$("cCc")?.value.trim() && !$("cBcc")?.value.trim()) { toast("An wen soll die Mail gehen?"); return $("cTo").focus(); }
  if (!draft && !$("cSub").value.trim() && !confirm("Ohne Betreff senden?")) return;
  btn.disabled = true; $("cHint").textContent = draft ? "Speichert …" : "Sendet …";
  try {
    const r = await api("/send", { method: "POST", body: formData(draft) });
    if (draft) { C.draftId = r.draft_id; C.dirty = false; $("cHint").textContent = "Entwurf gespeichert"; toast("Entwurf gespeichert"); }
    else { closeCompose(); toast("Gesendet"); if (S.thread && r.thread_id === S.thread.id) openThread(S.thread.id, { quiet: true }); }
    loadList({ quiet: true }); loadLabels();
  } catch (e) { fail(e); $("cHint").textContent = ""; }
  btn.disabled = false;
}
$("compose").addEventListener("submit", (e) => { e.preventDefault(); submit(false); });
$("compose").addEventListener("input", () => { C.dirty = true; });
$("compose").addEventListener("change", (e) => {
  if (e.target.id !== "cFile") return;
  C.files.push(...e.target.files); e.target.value = ""; drawFiles();
});
$("compose").addEventListener("click", async (e) => {
  const rm = e.target.closest("[data-rm]"), fw = e.target.closest("[data-fw]"), c = e.target.closest("[data-c]");
  if (rm) { C.files.splice(+rm.dataset.rm, 1); return drawFiles(); }
  if (fw) { C.skip.has(fw.dataset.fw) ? C.skip.delete(fw.dataset.fw) : C.skip.add(fw.dataset.fw); return drawFiles(); }
  if (!c) return;
  if (c.dataset.c === "cc") { $("cCcRow").hidden = false; $("cBccRow").hidden = false; return $("cCc").focus(); }
  if (c.dataset.c === "draft") return submit(true);
  if (c.dataset.c === "discard") {
    if (C.draftId) await api(`/drafts/${C.draftId}`, { method: "DELETE" }).catch(() => {});
    closeCompose(); return toast("Verworfen");
  }
  if (c.dataset.c === "close") {
    if (C.dirty && ($("cText").value.trim() || $("cTo").value.trim())) await submit(true);  // nichts geht verloren: beim Schließen als Entwurf sichern
    closeCompose();
  }
});
$("sheetScrim").addEventListener("click", () => $("compose").querySelector('[data-c="close"]')?.click());
$("composeBtn").onclick = $("fab").onclick = () => compose("new");
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (document.body.classList.contains("composing")) $("compose").querySelector('[data-c="close"]')?.click();
  else if (document.body.classList.contains("nav-open")) document.body.classList.remove("nav-open");
  else if (S.tid) leave();
});

// ---------------- Suche, Menü ----------------
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("q").blur(); setHash({ q: $("q").value.trim(), t: null }); } });
$("q").addEventListener("search", () => { if (!$("q").value && S.q) setHash({ q: "", t: null }); });
$("menuBtn").onclick = () => document.body.classList.add("nav-open");
$("scrim").onclick = () => document.body.classList.remove("nav-open");
$("refreshBtn").onclick = () => { loadList({ quiet: true }); loadLabels(); if (S.tid) openThread(S.tid, { quiet: true }); };

// ---------------- Live ----------------
let es = null, reloadTimer = null;
function live(on) {
  if (!on) { es?.close(); es = null; $("liveDot").classList.remove("on"); return; }
  if (es) return;
  es = new EventSource(`${API}/stream`);
  es.onopen = () => $("liveDot").classList.add("on");
  es.onerror = () => $("liveDot").classList.remove("on");
  es.onmessage = (e) => {
    let d = {};
    try { d = JSON.parse(e.data); } catch { return; }
    if (d.type === "new") toast(`Neue Mail von ${d.from}: ${d.subject || "(kein Betreff)"}`, d.thread ? { label: "Öffnen", run: () => setHash({ t: d.thread }) } : null);
    clearTimeout(reloadTimer);
    reloadTimer = setTimeout(() => {
      loadList({ quiet: true }); loadLabels();
      if (S.tid && (!d.thread || d.thread === S.tid) && !document.body.classList.contains("composing")) openThread(S.tid, { quiet: true });
    }, 400);
  };
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return live(false);   // im Hintergrund: keine Verbindung, kein Abfragen
  live(true); loadList({ quiet: true }); loadLabels();
});

// ---------------- Start ----------------
function showSetup(msg) {
  $("list").innerHTML = `<div class="empty"><span class="ms">link_off</span>${esc(msg)}</div>`;
  $("account").textContent = "nicht verbunden";
}
async function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  try {
    const me = await api("/me");
    S.me = (me.email || "").toLowerCase(); S.sendAs = me.send_as || [];
    $("account").textContent = me.email || "";
  } catch (e) { if (e.status === 409) showSetup(e.message); }
  loadLabels();
  route();
  live(!document.hidden);
  const p = new URLSearchParams(location.search);
  if (p.has("neu") || p.has("to")) compose("new", null, { to: p.get("to") || "", subject: p.get("subject") || "" });   // alte Adresse /?neu=1
}
boot();
