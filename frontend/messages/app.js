// IDA Nachrichten: was die KIs dir schicken (Nachrichten an „user“). Kein Polling: geladen wird beim Öffnen,
// wenn die App wieder sichtbar wird und wenn ein Push ankommt (der Service Worker sagt Bescheid).
const API = "/api/v1";
const $ = (s) => document.querySelector(s);
const list = $("#list"), count = $("#count"), more = $("#more"), bell = $("#bell"), banner = $("#push"), readall = $("#readall");
const PAGE = 50;
let items = [], unread = 0, lastLoad = 0, full = false, openId = location.hash.slice(1) || null;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const linkify = (s) => esc(s).replace(/https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"]/g, (u) => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
const fmtDay = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" });
const fmtTime = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });

function when(iso) {
  const d = new Date(iso), now = new Date();
  const days = Math.round((new Date(now.toDateString()) - new Date(d.toDateString())) / 864e5);
  const t = fmtTime.format(d);
  return days === 0 ? t : days === 1 ? `gestern ${t}` : `${fmtDay.format(d)} ${t}`;
}

async function api(path, opts = {}) {
  const r = await fetch(API + path, { credentials: "same-origin", cache: "no-store", redirect: "manual", ...opts,
    headers: { "content-type": "application/json", ...(opts.headers || {}) } });
  if (r.type === "opaqueredirect" || r.status === 401 || r.status === 403) throw Object.assign(new Error("login"), { login: true });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function row(m) {
  const cls = ["msg", m.read ? "" : "new", m.priority === "high" ? "high" : "", m.id === openId ? "open" : ""].filter(Boolean).join(" ");
  const title = m.title && m.title !== m.from_name ? `<div class="title">${esc(m.title)}</div>` : "";
  return `<article class="${cls}" data-id="${m.id}"><div class="meta"><span class="who">${esc(m.from_name)}</span><time>${when(m.at)}</time></div>${title}<div class="text">${linkify(m.text)}</div></article>`;
}

function render() {
  list.innerHTML = items.length ? items.map(row).join("") : `<div class="empty">Noch keine Nachrichten.</div>`;
  count.hidden = !unread;
  count.textContent = unread;
  readall.hidden = !unread;
  more.hidden = full || items.length < PAGE;
  if (navigator.setAppBadge) (unread ? navigator.setAppBadge(unread) : navigator.clearAppBadge()).catch(() => {});
}

const scrollToOpen = () => openId && list.querySelector(`[data-id="${CSS.escape(openId)}"]`)?.scrollIntoView({ block: "center" });

async function load() {
  lastLoad = Date.now();
  try {
    const d = await api(`/messages/inbox?limit=${PAGE}`);
    items = d.items; unread = d.unread; full = d.items.length < PAGE;
    render();
    scrollToOpen();
    if (openId) markRead(openId);
  } catch (e) {
    list.innerHTML = e.login
      ? `<div class="empty">Anmeldung abgelaufen.<br><button class="ghost" onclick="location.reload()">Neu anmelden</button></div>`
      : `<div class="empty">Gerade nicht erreichbar.<br><button class="ghost" onclick="location.reload()">Nochmal versuchen</button></div>`;
  }
}

async function loadMore() {
  const last = items[items.length - 1];
  if (!last) return;
  const d = await api(`/messages/inbox?limit=${PAGE}&before=${encodeURIComponent(last.id)}`);
  items = items.concat(d.items); unread = d.unread; full = d.items.length < PAGE;
  render();
}

async function markRead(id) {
  const m = items.find((x) => x.id === id);
  if (!m || m.read) return;
  m.read = true; unread = Math.max(0, unread - 1);
  render();
  api(`/messages/${encodeURIComponent(id)}/read`, { method: "POST" }).catch(() => {});
}

list.addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  const el = e.target.closest(".msg");
  if (!el) return;
  el.classList.toggle("open");
  markRead(el.dataset.id);
});
readall.addEventListener("click", async () => {
  items.forEach((m) => (m.read = true)); unread = 0; render();
  await api("/messages/read-all", { method: "POST" }).catch(() => {});
});
more.addEventListener("click", () => loadMore().catch(() => {}));
// wieder sichtbar: neu laden, aber nicht öfter als alle 20 s
document.addEventListener("visibilitychange", () => { if (!document.hidden && Date.now() - lastLoad > 20000) load(); });

// ---------- Push ----------
const b64ToBytes = (s) => Uint8Array.from(atob((s + "=".repeat((4 - (s.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
let reg = null;

function deviceName() {
  const ua = navigator.userAgent;
  const os = /Android/.test(ua) ? "Android" : ios ? "iOS" : /Windows/.test(ua) ? "Windows" : /Mac/.test(ua) ? "Mac" : /Linux/.test(ua) ? "Linux" : "Gerät";
  const br = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  return `${os} · ${br}${standalone ? " (App)" : ""}`;
}

async function saveSub(sub) {
  await api("/push/subscribe", { method: "POST", body: JSON.stringify({ ...sub.toJSON(), device: deviceName() }) });
  localStorage.setItem("pushSaved", String(Date.now()));
}

function showBanner(html) { banner.innerHTML = html; banner.hidden = !html; }

async function refreshPush() {
  if (!reg || !("PushManager" in window)) {
    bell.hidden = true;
    showBanner(ios && !standalone ? "Für Benachrichtigungen: Teilen → <b>Zum Home-Bildschirm</b>, dann die App von dort öffnen." : "");
    return;
  }
  const sub = await reg.pushManager.getSubscription();
  bell.classList.toggle("off", !sub);
  if (sub) {
    showBanner("");
    // einmal am Tag beim Server auffrischen (falls er das Gerät vergessen hat)
    if (Date.now() - Number(localStorage.getItem("pushSaved") || 0) > 864e5) saveSub(sub).catch(() => {});
  } else if (Notification.permission === "denied") {
    showBanner("Benachrichtigungen sind für diese Seite blockiert. Erlaub sie in den Browser- bzw. App-Einstellungen.");
  } else {
    showBanner(`Neue Nachrichten als Pop-up aufs Handy bekommen?<button id="on">Benachrichtigungen einschalten</button>`);
    $("#on").onclick = enablePush;
  }
}

async function enablePush() {
  try {
    if ((await Notification.requestPermission()) !== "granted") return refreshPush();
    const { key } = await api("/push/key");
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
    await saveSub(sub);
    await api("/push/test", { method: "POST" }).catch(() => {});
  } catch (e) {
    showBanner(`Einschalten hat nicht geklappt (${esc(e.message)}).`);
    return;
  }
  refreshPush();
}

bell.addEventListener("click", async () => {
  const sub = reg && (await reg.pushManager.getSubscription());
  if (!sub) return enablePush();
  if (!confirm("Benachrichtigungen auf diesem Gerät ausschalten?")) return;
  await api("/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint: sub.endpoint }) }).catch(() => {});
  await sub.unsubscribe();
  localStorage.removeItem("pushSaved");
  refreshPush();
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data?.type === "new") load();
    if (e.data?.type === "open") { openId = e.data.id; load(); }
  });
  navigator.serviceWorker.register("sw.js", { scope: "./" }).then(async () => { reg = await navigator.serviceWorker.ready; refreshPush(); }, () => refreshPush());
} else {
  refreshPush();
}
window.addEventListener("hashchange", () => { openId = location.hash.slice(1) || null; render(); scrollToOpen(); if (openId) markRead(openId); });
load();
