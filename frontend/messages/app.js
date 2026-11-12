// IDA Nachrichten: was die KIs dir schicken (Nachrichten an „user“). Kein Polling: geladen wird beim Öffnen,
// wenn die App wieder sichtbar wird und wenn ein Push ankommt (der Service Worker sagt Bescheid).
// Bausteine aus IDA selbst (gleiche Farben je Agent, gleiches Markdown wie im Chat)
import { esc, md } from "../js/ui/dom.js";
import { PALETTE } from "../js/store.js";

const API = "/api/v1";
const $ = (s) => document.querySelector(s);
const list = $("#list"), sub = $("#sub"), more = $("#more"), bell = $("#bell"), banner = $("#push"), readall = $("#readall");
const PAGE = 50;
let items = [], unread = 0, lastLoad = 0, full = false, openId = location.hash.slice(1) || null;

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
  const c = PALETTE[m.from_palette] || PALETTE.blau;
  const cls = ["mcard", m.read ? "" : "unread", m.priority === "high" ? "high" : "", m.id === openId ? "open" : ""].filter(Boolean).join(" ");
  return `<article class="${cls}" data-id="${esc(m.id)}">
    <span class="ric" style="background:${c["--container"]};color:${c["--accent"]}"><span class="ms">${esc(m.icon || "notifications")}</span></span>
    <div class="rmain"><div class="mtop"><b>${esc(m.title || m.from_name)}</b><span>${when(m.at)}</span></div>
      <div class="mfrom">${esc(m.from_name)}${m.priority === "high" ? ' · <span class="hi">wichtig</span>' : ""}</div>
      <div class="mtext md">${md(m.text)}</div></div></article>`;
}

function render() {
  list.innerHTML = items.length ? items.map(row).join("") : `<div class="empty"><span class="ms">notifications</span>Noch keine Nachrichten.</div>`;
  sub.textContent = unread ? `${unread} neu` : "alles gelesen";
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
      ? `<div class="empty"><span class="ms">lock</span>Anmeldung abgelaufen.<br><button class="es-btn primary" onclick="location.reload()">Neu anmelden</button></div>`
      : `<div class="empty"><span class="ms">cloud_off</span>Gerade nicht erreichbar.<br><button class="es-btn primary" onclick="location.reload()">Nochmal versuchen</button></div>`;
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
  const el = e.target.closest(".mcard");
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

// Hinweis-Karte im IDA-Stil: Icon, Überschrift, Text, optional ein Knopf
function showBanner(html, { icon = "notifications", title = "", button = "" } = {}) {
  banner.hidden = !html;
  banner.innerHTML = html ? `<span class="ric"><span class="ms">${icon}</span></span><div class="pmain">${title ? `<b>${title}</b>` : ""}${html}
    ${button ? `<br><button class="es-btn primary" id="on"><span class="ms">notifications_active</span>${button}</button>` : ""}</div>` : "";
}

async function refreshPush() {
  if (!reg || !("PushManager" in window)) {
    bell.hidden = true;
    showBanner(ios && !standalone ? "Teilen → „Zum Home-Bildschirm“, dann die App von dort öffnen." : "", { icon: "add_to_home_screen", title: "Für Pop-ups als App installieren" });
    return;
  }
  const sub = await reg.pushManager.getSubscription();
  bell.classList.toggle("on", !!sub);
  bell.querySelector(".ms").textContent = sub ? "notifications_active" : "notifications_off";
  if (sub) {
    showBanner("");
    // einmal am Tag beim Server auffrischen (falls er das Gerät vergessen hat)
    if (Date.now() - Number(localStorage.getItem("pushSaved") || 0) > 864e5) saveSub(sub).catch(() => {});
  } else if (Notification.permission === "denied") {
    showBanner("Erlaub sie in den Einstellungen des Browsers bzw. der App für diese Seite.", { icon: "notifications_off", title: "Benachrichtigungen blockiert" });
  } else {
    showBanner("Neue Nachrichten von Ida kommen dann sofort als Pop-up aufs Handy.", { title: "Pop-ups einschalten?", button: "Einschalten" });
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
    showBanner(`Einschalten hat nicht geklappt (${esc(e.message)}).`, { icon: "error", title: "Hat nicht geklappt" });
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
