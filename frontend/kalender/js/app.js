// Hülle: Top-Bar mit Mini-Monat, Floating Toolbar + FAB (am Tablet/PC Navigation Rail), Drawer/Seitenleiste,
// Routing, Blättern mit dem Finger, Tastenkürzel, Live-Updates (auch von Ida), Erinnerungen.
import { api, live } from "./api.js";
import * as repo from "./repo.js";
import * as files from "./files.js";
import * as alarms from "./alarms.js";
import * as sync from "./sync.js";
import { persist } from "./storage.js";
import { Q, S, cachedEvents, calOf, flushQueue, importLegacy, loadEvents, loadMeta, queued, refreshQ, saveHidden } from "./store.js";
import { addDays, addMonths, daysBetween, hm, isoWeek, monthName, monthShort, parseYmd, sameDay, setWeekStart, shortDay, startOfDay, ymd } from "./dates.js";
import { agenda, cstyle, esc, gridDays, miniMonth, month, monthday, skeleton, timegrid, year } from "./views.js";
import { bindGrid, bindGrip, bindSwipe, longPress, swallowClick } from "./gestures.js";
import {
  choose, deleteEvent, layerOpen, openAsk, openCalendar, openConflict, openDetail, openEditor, openQuick, openSearch, openSettings, openSync,
  patchEvent, popLayer, setApiHref, setHooks, showAlarm, toast,
} from "./sheets.js";

const $ = (s, r = document) => r.querySelector(s);
const VIEWS = {
  monthday: ["monat", "Monat + Tag", "calendar_view_month"], agenda: ["liste", "Liste", "view_agenda"], day: ["tag", "Tag", "calendar_view_day"],
  days3: ["3tage", "3 Tage", "view_week"], week: ["woche", "Woche", "calendar_view_week"], month: ["vollmonat", "Monat", "grid_view"], year: ["jahr", "Jahr", "event_note"],
};
const SLUG = Object.fromEntries(Object.entries(VIEWS).map(([k, v]) => [v[0], k]));
const phone = () => innerWidth < 600;

const drawerWait = [];
/** Drawer schließen (über die History, wie Android-Zurück); Promise, wenn er zu ist. */
const closeDrawer = () => new Promise((r) => { if (!document.body.classList.contains("dr-open")) return r(); drawerWait.push(r); history.back(); });
let root, base = "", installEvt = null, rendering = 0, dir = 0, prevView = null, miniOpen = false, miniDate = null;

export async function mount(el, opts = {}) {
  noAutofill();
  root = el; base = opts.pathBase || "";
  setApiHref(`${base}/api/v1`);
  await loadMeta();
  setWeekStart(S.settings.week_start ?? 1);
  S.view = phone() ? (S.settings.default_view || "monthday") : (S.settings.default_view_desktop || "week");
  if (!VIEWS[S.view]) S.view = "monthday";
  fromPath();
  shell();
  setHooks({
    changed: async (metaToo) => { if (metaToo === true) { await loadMeta(); setWeekStart(S.settings.week_start ?? 1); drawer(); } else if (metaToo) flash([metaToo]); await sync.pull().catch(() => {}); render(true); },
    jump: (e) => { const d = e.all_day ? parseYmd(e.start) : startOfDay(new Date(e.start)); S.date = S.sel = d; setView("day"); },
    only: (id) => { S.hidden = new Set(S.cals.map((c) => c.id).filter((x) => x !== id)); saveHidden(); drawer(); paint(true); },
  });
  addEventListener("popstate", () => {
    if (popLayer()) return;
    if (document.body.classList.contains("dr-open")) { document.body.classList.remove("dr-open"); drawerWait.splice(0).forEach((r) => r()); return; }
    fromPath(); render();
  });
  addEventListener("online", () => { sync.syncNow().catch(() => {}); });
  addEventListener("kal:outbox", () => paintOffline());
  repo.onChange(() => render(true));
  addEventListener("kal:net", () => { paintOffline(); if (S.net === "online") render(true); });
  $("#offline").addEventListener("click", (e) => { if (e.target.closest("[data-login]")) location.reload(); else if (e.target.closest("[data-sync]")) openSync(); });
  sync.onPushed((sum) => {
    if (sum.sent && sum.stoppedFromOffline !== true && S.net === "online") toast(`${sum.sent} Offline-Änderung${sum.sent > 1 ? "en" : ""} gespeichert`);
    if (sum.merged) toast("Mit einer Änderung auf dem Server zusammengeführt");
    if (sum.conflicts) toast(`${sum.conflicts} Konflikt${sum.conflicts > 1 ? "e" : ""}`, [{ label: "Ansehen", run: () => openSync() }]);
    else if (sum.errors) toast(`${sum.errors} Änderung${sum.errors > 1 ? "en konnten" : " konnte"} nicht gespeichert werden`, [{ label: "Ansehen", run: () => openSync() }]);
  });
  addEventListener("kal:conflict", () => openSync());
  addEventListener("kal:sw-synced", async () => { await refreshQ().catch(() => {}); sync.pull().catch(() => {}); });   // der Service Worker hat im Hintergrund gesendet
  addEventListener("keydown", keys);
  matchMedia("(prefers-color-scheme: light)").addEventListener?.("change", applyTheme);
  addEventListener("resize", () => { document.body.classList.toggle("wide", innerWidth >= 1000); });
  addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; document.body.classList.add("can-install"); });
  live(onLive, (ok) => { if (ok) sync.syncNow().catch(() => {}); });
  // sofort aus dem Spiegel zeigen, dann vom Server (nie ein leerer Bildschirm)
  const cached = await cachedEvents();
  if (cached) { S.events = cached; paint(true); } else $("#stage").innerHTML = `<div class="view">${skeleton()}</div>`;
  await importLegacy().catch(() => {});
  await files.prune().catch(() => {});
  await refreshQ().catch(() => {});
  await render(true);
  sync.start();
  persist();
  const q = new URLSearchParams(location.search);
  if (q.get("neu")) openEditor();
  if (q.get("neu") || q.get("heute")) history.replaceState(history.state, "", location.pathname);
  setInterval(tick, 30000);
  tick();
}

/** Ohne Verbindung: Erinnerungen selbst auslösen (aus dem lokalen Speicher). Online macht das der Server – beide merken sich, was schon gezeigt wurde. */
export async function localAlarms() {
  if (S.net === "online") return;
  const now = new Date();
  const shown = (await repo.loadMeta("alarmsShown")) || {};
  const list = alarms.due(await repo.range(addDays(now, -1), addDays(now, 2)), now, shown);
  if (!list.length) return;
  await repo.saveMeta("alarmsShown", alarms.remember(shown, list.map((x) => x.key), now));
  if (inQuiet()) return;
  for (const a of list) fireAlarm(a);
}
async function rememberAlarm(evt) {
  const shown = (await repo.loadMeta("alarmsShown")) || {};
  await repo.saveMeta("alarmsShown", alarms.remember(shown, [alarms.keyOf(evt.occurrence_id, evt.minutes)], new Date()));
}
function fireAlarm(evt) {
  showAlarm(evt);
  if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
    navigator.serviceWorker?.ready.then((r) => r.showNotification(evt.title, { body: `${evt.all_day ? "Heute" : hm(new Date(evt.start))}${evt.location ? ` · ${evt.location}` : ""}`, tag: evt.occurrence_id, icon: `${base}/manifest.webmanifest` }))
      .catch(() => new Notification(evt.title));
  }
}

function tick() {
  localAlarms().catch(() => {});
  const n = $("#nowLine"), d = new Date();
  if (n) n.style.top = `${(d.getHours() * 60 + d.getMinutes()) / 60 * S.hh}px`;
  $("#todayNum").textContent = d.getDate();
}

// ---------------- Routing: /monat/2026-10-30, /woche/…, /termin/<id> ----------------
function fromPath() {
  const p = location.pathname.slice(base.length).split("/").filter(Boolean);
  if (SLUG[p[0]]) { S.view = SLUG[p[0]]; if (/^\d{4}-\d{2}-\d{2}$/.test(p[1] || "")) { S.date = parseYmd(p[1]); S.sel = S.date; } }
  if (new URLSearchParams(location.search).get("heute")) S.date = S.sel = startOfDay(new Date());
  if (S.view === "agenda") { S.agFrom = startOfDay(S.date); S.agTo = addDays(S.agFrom, 60); }
  if (p[0] === "termin" && p[1]) setTimeout(() => openDetail(decodeURIComponent(p[1])), 300);
}
function pushPath() {
  const path = `${base}/${VIEWS[S.view][0]}/${ymd(S.date)}`;
  if (location.pathname !== path) history.replaceState(history.state, "", path);
}

// ---------------- Gerüst ----------------
function shell() {
  document.body.classList.toggle("wide", innerWidth >= 1000);
  root.innerHTML = `<div class="scrim" id="scrim"></div><aside class="drawer" id="drawer"></aside>
    <div class="shell"><header class="top" id="top">
        <button class="hq menu-btn" id="menu" aria-label="Menü"><span class="ms">menu</span></button>
        <div class="hpill">
          <button class="title" id="title"></button>
          <div class="pc-nav"><button class="icon-btn plain" id="prev" aria-label="Zurück"><span class="ms">chevron_left</span></button><button class="icon-btn plain" id="next" aria-label="Weiter"><span class="ms">chevron_right</span></button></div>
          <button class="icon-btn plain" id="search" aria-label="Suche"><span class="ms">search</span></button>
          <button class="icon-btn plain today-btn" id="today" aria-label="Heute"><span class="ms">calendar_today</span><b id="todayNum">${new Date().getDate()}</b></button>
        </div>
      </header>
      <div class="minidrop" id="minidrop"></div>
      <div class="offline" id="offline"></div>
      <div class="stage" id="stage"></div></div>
    <nav class="bar" id="bar"><span class="rail-logo ida-mini" aria-hidden="true"><i></i><i></i></span><div class="tb" id="tb"></div><button class="fab" id="fab" aria-label="Neuer Termin"><span class="ms">add</span></button></nav>
    <div class="fab-menu" id="fabMenu"></div>`;
  $("#menu").onclick = () => { document.body.classList.add("dr-open"); history.pushState({ ...history.state, drawer: 1 }, ""); };
  $("#scrim").onclick = () => history.back();
  $("#prev").onclick = () => nav(-1); $("#next").onclick = () => nav(1);
  $("#today").onclick = () => { dir = daysBetween(S.date, new Date()) > 0 ? 1 : -1; S.date = S.sel = startOfDay(new Date()); if (S.view === "agenda") { S.agFrom = S.date; S.agTo = addDays(S.date, 60); } go(); };
  $("#search").onclick = openSearch;
  $("#title").onclick = () => toggleMini(!miniOpen);
  $("#tb").onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.v === "more") { const v = await choose("Ansicht", Object.entries(VIEWS).map(([k, [, l, ic]]) => [k, l, ic]), { selected: [S.view] }); if (v) setView(v); return; }
    setView(b.dataset.v);
  };
  // FAB: Antippen = Termin zur nächsten vollen Stunde, lange drücken = Menü
  const fab = $("#fab");
  let held = false, t;
  fab.addEventListener("pointerdown", () => { held = false; t = setTimeout(() => { held = true; navigator.vibrate?.(8); fabMenu(true); }, 450); });
  fab.addEventListener("pointerup", () => clearTimeout(t));
  fab.addEventListener("pointerleave", () => clearTimeout(t));
  fab.addEventListener("contextmenu", (e) => e.preventDefault());
  fab.onclick = () => { if (held) return; if (document.body.classList.contains("fab-open")) return fabMenu(false); openEditor({ start: defaultStart(), fromFab: true }); };
  $("#stage").addEventListener("click", stageClick);
  $("#stage").addEventListener("contextmenu", rightClick);
  longPress($("#stage"), ".m-cell", (c) => { const d = parseYmd(c.dataset.day); S.sel = d; openEditor({ start: defaultStart(d) }); });
  pager($("#stage"));
  drawer();
}

function fabMenu(open) {
  document.body.classList.toggle("fab-open", open);
  const m = $("#fabMenu");
  if (!open) { m.innerHTML = ""; return; }
  const items = [["event", "Termin", () => openEditor({ start: defaultStart(), fromFab: true })], ["bolt", "Schnelleingabe", openQuick],
    ["today", "Ganztägig", () => openEditor({ start: new Date(S.sel), allDay: true, fromFab: true })], ["content_copy", "Aus Vorlage", fromTemplate],
    ...(S.meta.ask && S.net === "online" ? [["auto_awesome", "Ida fragen", () => openAsk()]] : [])];
  m.innerHTML = items.map(([ic, l], i) => `<button data-i="${i}" style="--i:${i}"><span class="ms">${ic}</span>${l}</button>`).join("");
  const acts = items.map((x) => x[2]);
  m.querySelectorAll("button").forEach((b) => (b.onclick = () => { fabMenu(false); acts[+b.dataset.i](); }));
  setTimeout(() => addEventListener("pointerdown", function off(e) { if (!e.target.closest("#fabMenu,#fab")) { fabMenu(false); removeEventListener("pointerdown", off); } }), 0);
}

/** „Aus Vorlage“: ein früherer Termin als Vorlage (Titel, Dauer, Ort, Kalender, Erinnerungen) – nur Datum und Zeit sind neu. */
async function fromTemplate() {
  const seen = new Map();
  for (const e of [...S.events].reverse()) if (!e.holiday && !e.read_only && !seen.has(e.title.toLowerCase())) seen.set(e.title.toLowerCase(), e);
  const list = [...seen.values()].slice(0, 12);
  if (!list.length) return toast("Noch keine Termine als Vorlage – leg erst einen an");
  const id = await choose("Vorlage wählen", list.map((e) => [e.id, `${e.title}${e.location ? ` · ${e.location}` : ""}`, "event", cstyle(e.color)]));
  const t = list.find((e) => e.id === id); if (!t) return;
  const s = defaultStart(), dur = new Date(t.end) - new Date(t.start);
  openEditor({ event: { ...t, start: t.all_day ? ymd(s) : s.toISOString(), end: t.all_day ? ymd(addDays(s, 1)) : new Date(s.getTime() + dur).toISOString(), rrule: "", recurring: false }, copy: true });
}
const doneKey = "kal-done";
const doneSet = () => new Set(JSON.parse(localStorage.getItem(doneKey) || "[]"));
const isDone = (id) => doneSet().has(id);
function toggleDone(id) { const s = doneSet(); s.has(id) ? s.delete(id) : s.add(id); localStorage.setItem(doneKey, JSON.stringify([...s].slice(-500))); }

function applyTheme() {
  const t = S.settings.theme || "dark";
  const light = t === "light" || (t === "system" && matchMedia("(prefers-color-scheme: light)").matches);
  document.body.classList.toggle("light", light);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", light ? "#f8f9ff" : "#000000");
}

function defaultStart(day = S.sel) {
  const n = new Date(), d = new Date(day);
  if (sameDay(d, n)) d.setHours(n.getHours() + 1, 0, 0, 0); else d.setHours(9, 0, 0, 0);
  return d;
}

function toolbar() {
  const favs = (S.settings.toolbar_views || ["agenda", "days3", "monthday"]).filter((v) => VIEWS[v]).slice(0, 3);
  const items = favs.map((v) => [v, ...VIEWS[v].slice(1)]);
  const extra = !favs.includes(S.view);
  $("#tb").innerHTML = items.map(([v, l, ic]) => `<button class="t${v === S.view ? " on" : ""}" data-v="${v}" aria-label="${l}"><span class="ms">${ic}</span><span class="lbl">${l}</span></button>`).join("")
    + `<button class="t${extra ? " on" : ""}" data-v="more" aria-label="Alle Ansichten"><span class="ms">${extra ? VIEWS[S.view][2] : "more_horiz"}</span><span class="lbl">${extra ? VIEWS[S.view][1] : "Mehr"}</span></button>`;
}

function drawer() {
  const cals = S.cals.filter((c) => c.kind !== "holidays").map((c) => `<button class="dr-item cal${S.hidden.has(c.id) ? "" : " on"}" data-cal="${c.id}" style="${cstyle(c.color)}"><i></i><span class="lbl">${esc(c.name)}</span>${c.agent_access === "write" ? '<em class="ms tiny" title="Ida darf hier schreiben">auto_awesome</em>' : ""}</button>`).join("");
  $("#drawer").innerHTML = `<div class="dr-head"><div class="face"></div><b>Kalender</b></div>
    <div class="dr-mini" id="drMini"></div>
    <div class="dr-views">${Object.entries(VIEWS).map(([k, [, l, ic]]) => `<button class="dr-item${k === S.view ? " act" : ""}" data-view="${k}"><span class="ms">${ic}</span>${l}</button>`).join("")}</div>
    <h3>Meine Kalender</h3>${cals}
    <button class="dr-item" id="dNew"><span class="ms">add</span>Neuer Kalender</button>
    <div class="dr-item"><span class="ms">celebration</span><span class="grow">Feiertage</span><button class="sw${S.settings.show_holidays !== false ? " on" : ""}" id="dHol"></button></div>
    <h3>Mehr</h3>
    <button class="dr-item" id="dSet"><span class="ms">settings</span><span class="grow">Einstellungen</span><b class="cnt" id="syncCnt" title="Änderungen, die noch auf den Abgleich warten"></b></button>
    <button class="dr-item install" id="dInst"><span class="ms">install_mobile</span>App installieren</button>
    <p class="dr-ios" id="drIos" hidden>Am iPhone: Teilen → „Zum Home-Bildschirm“.</p>`;
  const d = $("#drawer"), close = closeDrawer;
  let lastTap = { id: null, t: 0 };
  d.querySelectorAll("[data-cal]").forEach((b) => (b.onclick = () => {
    if (swallowClick()) return;
    const id = b.dataset.cal, now = Date.now();
    if (lastTap.id === id && now - lastTap.t < 320) { S.hidden = new Set(S.cals.map((c) => c.id).filter((x) => x !== id)); toast(`Nur „${calOf(id).name}“`, [{ label: "Alle zeigen", run: () => { S.hidden.clear(); saveHidden(); drawer(); paint(true); } }]); }
    else S.hidden.has(id) ? S.hidden.delete(id) : S.hidden.add(id);
    lastTap = { id, t: now };
    saveHidden(); drawer(); paint(true);
  }));
  longPress(d, "[data-cal]", async (b) => { await close(); openCalendar(calOf(b.dataset.cal)); });
  d.querySelectorAll("[data-view]").forEach((b) => (b.onclick = async () => { await close(); setView(b.dataset.view); }));
  $("#dNew").onclick = async () => { await close(); openCalendar(null); };
  $("#dHol").onclick = async () => { S.settings = await api("/settings", { method: "PUT", body: { show_holidays: S.settings.show_holidays === false } }); drawer(); paint(true); };
  $("#syncCnt").textContent = Q.count || "";
  $("#dSet").onclick = async () => { await close(); openSettings(); };
  $("#dInst").onclick = async () => { if (installEvt) { installEvt.prompt(); await installEvt.userChoice; installEvt = null; document.body.classList.remove("can-install"); } };
  $("#drIos").hidden = !/iphone|ipad/i.test(navigator.userAgent) || matchMedia("(display-mode: standalone)").matches;
  drawMini();
}

function drawMini() {
  const el = $("#drMini"); if (!el) return;
  miniDate ||= new Date(S.date.getFullYear(), S.date.getMonth(), 1);
  el.innerHTML = miniMonth(miniDate);
  bindMini(el, () => drawMini());
}
function bindMini(el, redraw) {
  el.querySelectorAll("[data-mini]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); miniDate = addMonths(miniDate, +b.dataset.mini); redraw(); }));
  el.querySelectorAll("[data-jump]").forEach((b) => (b.onclick = () => {
    S.date = S.sel = parseYmd(b.dataset.jump);
    if (miniOpen) toggleMini(false);
    if (S.view === "agenda") { S.agFrom = S.date; S.agTo = addDays(S.date, 60); }
    go();
  }));
  let x0;
  el.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; }, { passive: true });
  el.addEventListener("touchend", (e) => { const dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 50) { miniDate = addMonths(miniDate, dx < 0 ? 1 : -1); redraw(); } }, { passive: true });
}

function toggleMini(open) {
  if (open === miniOpen) return;
  miniOpen = open;
  const el = $("#minidrop");
  if (open) {
    miniDate = new Date(S.date.getFullYear(), S.date.getMonth(), 1);
    const draw = () => { el.innerHTML = miniMonth(miniDate); bindMini(el, draw); };
    draw();
    // Hochwischen schließt
    let y0; el.ontouchstart = (e) => { y0 = e.touches[0].clientY; }; el.ontouchend = (e) => { if (y0 - e.changedTouches[0].clientY > 50) toggleMini(false); };
  }
  document.body.classList.toggle("mini-open", open);
  $("#title").classList.toggle("open", open);
}

// ---------------- Navigation ----------------
function setView(v) {
  if (!VIEWS[v]) return;
  if (v === "agenda" && S.view !== "agenda") { S.agFrom = startOfDay(sameDay(S.sel, S.date) ? S.sel : S.date); S.agTo = addDays(S.agFrom, 60); }
  if (v !== S.view) navigator.vibrate?.(8);
  S.view = v; dir = 0; go();
}
function nav(n) {
  dir = n;
  const v = S.view;
  if (v === "monthday" || v === "month") { S.date = addMonths(S.date, n); S.sel = sameDay(new Date(S.date.getFullYear(), S.date.getMonth(), 1), new Date(new Date().getFullYear(), new Date().getMonth(), 1)) ? startOfDay(new Date()) : S.date; }
  else if (v === "year") S.date = new Date(S.date.getFullYear() + n, 0, 1);
  else if (v === "week") S.date = addDays(S.date, 7 * n);
  else if (v === "days3") S.date = addDays(S.date, 3 * n);
  else if (v === "day") S.date = addDays(S.date, n);
  if (!["monthday", "month"].includes(v)) S.sel = S.date;
  go();
}
function go() { if (miniOpen) toggleMini(false); pushPath(); render(); }

function stageClick(e) {
  if (swallowClick()) return;
  if (e.target.closest("[data-new]")) { openEditor({ start: defaultStart() }); return; }
  if (e.target.closest("[data-adtoggle]")) { S.adOpen = !S.adOpen; paint(true); return; }
  if (e.target.closest("[data-earlier]")) { earlier(); return; }
  const ev = e.target.closest("[data-id]");
  if (ev) { openDetail(ev.dataset.id); return; }
  const ym = e.target.closest("[data-month]");
  if (ym) { S.date = S.sel = parseYmd(ym.dataset.month); dir = 0; setView("monthday"); return; }
  const col = e.target.closest(".tg-col");
  if (col && e.pointerType !== "touch" && !matchMedia("(pointer: coarse)").matches) return; // Maus: aufziehen geht über Ziehen
  if (col) return; // Touch: lange drücken zum Anlegen
  const day = e.target.closest("[data-day]");
  if (!day) return;
  const d = parseYmd(day.dataset.day);
  if (day.hasAttribute("data-goday") || day.classList.contains("lh")) { S.date = S.sel = d; setView("day"); return; }
  if (day.classList.contains("m-cell")) {
    const dbl = sameDay(d, S.sel) && Date.now() - (stageClick.t || 0) < 350;
    stageClick.t = Date.now();
    if (dbl || S.view === "month") { S.date = S.sel = d; setView("day"); return; }
    S.sel = d;
    if (d.getMonth() !== S.date.getMonth()) { dir = d > S.date ? 1 : -1; S.date = new Date(d.getFullYear(), d.getMonth(), 1); go(); return; }
    paint(true);
  }
}

async function rightClick(e) {
  const el = e.target.closest("[data-id]");
  if (!el || matchMedia("(pointer: coarse)").matches) return;
  e.preventDefault();
  let ev; try { ev = (await api(`/events/${encodeURIComponent(el.dataset.id)}`)).event; } catch { return; }
  if (ev.read_only) return openDetail(ev.id);
  const v = await choose(ev.title, [["edit", "Bearbeiten", "edit"], ["dup", "Duplizieren", "content_copy"], ["color", "Farbe", "palette"], ["del", "Löschen", "delete"]]);
  if (v === "edit") openEditor({ event: ev });
  if (v === "dup") openEditor({ event: ev, copy: true });
  if (v === "del") deleteEvent(ev);
  if (v === "color") {
    const c = await choose("Farbe", ["rot", "koralle", "orange", "gelb", "salbei", "gruen", "tuerkis", "blau", "lavendel", "lila", "graphit"].map((x) => [x, x[0].toUpperCase() + x.slice(1).replace("ue", "ü"), "circle", cstyle(x)]));
    if (c) patchEvent(ev, { color: c }, "Farbe ändern");
  }
}

async function earlier() {
  const v = $(".view"), h0 = v.scrollHeight, t0 = v.scrollTop;
  S.agFrom = addDays(S.agFrom, -30);
  await render(true);
  const nv = $(".view"); nv.scrollTop = t0 + (nv.scrollHeight - h0);
}

/** Blättern: der Inhalt folgt dem Finger und federt in die nächste Periode. */
function pager(el) {
  let x0, y0, t0, axis = null, v = null;
  el.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 1 || S.view === "agenda" || e.target.closest(".blk.lift,.swipe-wrap,#grip")) { axis = "none"; return; }
    x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; t0 = Date.now(); axis = null; v = $(".view");
  }, { passive: true });
  el.addEventListener("touchmove", (e) => {
    if (axis === "none" || e.touches.length !== 1 || !v) return;
    const dx = e.touches[0].clientX - x0, dy = e.touches[0].clientY - y0;
    if (!axis) { if (Math.abs(dx) > 10 || Math.abs(dy) > 10) axis = Math.abs(dx) > Math.abs(dy) * 1.3 ? "x" : "y"; else return; }
    if (axis !== "x" || v.querySelector(".lift")) return;
    v.style.transition = "none"; v.style.transform = `translateX(${dx}px)`; v.style.opacity = String(1 - Math.min(0.4, Math.abs(dx) / 800));
  }, { passive: true });
  el.addEventListener("touchend", (e) => {
    if (axis !== "x" || !v) return;
    const dx = e.changedTouches[0].clientX - x0, fast = Math.abs(dx) / (Date.now() - t0) > 0.5;
    if (Math.abs(dx) > innerWidth * 0.28 || (fast && Math.abs(dx) > 50)) {
      v.style.transition = "transform .18s var(--emph), opacity .18s"; v.style.transform = `translateX(${dx < 0 ? -innerWidth : innerWidth}px)`; v.style.opacity = "0";
      setTimeout(() => nav(dx < 0 ? 1 : -1), 140);
    } else { v.style.transition = "transform .45s var(--spring), opacity .3s"; v.style.transform = ""; v.style.opacity = ""; }
  }, { passive: true });
}

function keys(e) {
  if (e.target.closest("input,textarea,select") || e.ctrlKey || e.metaKey || e.altKey) return;
  if (layerOpen() && e.key !== "Escape") return;
  const k = e.key.toLowerCase(), m = { d: "day", w: "week", m: "month", a: "agenda", j: "year", 3: "days3" };
  if (m[k]) setView(m[k]);
  else if (k === "t") $("#today").click();
  else if (k === "c" || k === "n") { e.preventDefault(); openEditor({ start: defaultStart() }); }
  else if (k === "arrowleft") nav(-1); else if (k === "arrowright") nav(1);
  else if (k === "/") { e.preventDefault(); openSearch(); }
  else if (k === "escape") { if (layerOpen() || document.body.classList.contains("dr-open")) history.back(); if (miniOpen) toggleMini(false); fabMenu(false); }
}

// ---------------- Live: Änderungen (auch von Ida) und Erinnerungen ----------------
function flash(ids) {
  ids.forEach((i) => S.flash.add(i));
  setTimeout(() => { ids.forEach((i) => S.flash.delete(i)); document.querySelectorAll(".flash").forEach((n) => n.classList.remove("flash")); }, 2200);
}
function inQuiet() {
  const m = /^(\d\d):(\d\d)\s*-\s*(\d\d):(\d\d)$/.exec(S.settings.quiet_hours || ""); if (!m) return false;
  const n = new Date(), x = n.getHours() * 60 + n.getMinutes(), a = +m[1] * 60 + +m[2], b = +m[3] * 60 + +m[4];
  return a < b ? x >= a && x < b : x >= a || x < b;
}
const VERB = { create: "angelegt", update: "geändert", delete: "gelöscht", restore: "wiederhergestellt", undo: "zurückgenommen", move: "verschoben" };
function onLive(evt) {
  if (evt.type === "alarm") {
    rememberAlarm(evt).catch(() => {});
    if (inQuiet()) return;
    fireAlarm(evt);
    return;
  }
  if (evt.type !== "changed") return;
  const agent = evt.by && !["user", "sync", "system"].includes(evt.by);
  if (agent && S.settings.highlight_agent !== false) {
    flash(evt.ids || []);
    const acts = evt.action !== "delete" && evt.ids?.[0] ? [{ label: "Ansehen", run: () => openDetail(evt.ids[0]) }] : [];
    if (evt.change_id) acts.push({ label: "Rückgängig", run: async () => { try { await api(`/changes/${evt.change_id}/undo`, { method: "POST" }); await sync.pull(); } catch (x) { toast(x.message); } } });
    toast(`Ida hat „${evt.title || "einen Termin"}“ ${VERB[evt.action] || "geändert"}`, acts);
  }
  sync.pull().catch(() => {});
}

// ---------------- Zeichnen ----------------
function titleText() {
  const d = S.date, short = innerWidth < 440;   // schmal: „Okt. 2026“, damit KW-Chip, Suche und Heute in die Pille passen
  const mon = (x) => (short ? monthShort(x) : monthName(x));
  const kw = (x) => `<span class="kw">KW ${isoWeek(x)}</span>`;
  if (S.view === "year") return `<span class="tx">${d.getFullYear()}</span>`;
  if (S.view === "day") return `<span class="tx">${shortDay(d)}</span>${kw(d)}`;
  if (S.view === "week" || S.view === "days3") { const g = gridDays(); return `<span class="tx">${mon(g[0])} ${g[0].getFullYear()}</span>${kw(g[0])}`; }
  return `<span class="tx">${mon(d)} ${d.getFullYear()}</span>`;
}

export async function render(quiet) {
  const id = ++rendering;
  try { await loadEvents(); } catch (e) { toast(e.message, [{ label: "Erneut versuchen", run: () => render() }]); }
  if (id !== rendering) return;
  paint(quiet);
}

function paintOffline() {
  const n = Q.count, wait = n ? ` · ${n} Änderung${n > 1 ? "en" : ""} warten` : "", bar = $("#offline");
  let kind = "";
  if (S.net === "auth") { bar.innerHTML = `Anmeldung abgelaufen${wait} <button type="button" class="link" data-login>Anmelden</button>`; kind = "auth"; }
  else if (S.net === "offline") { bar.innerHTML = `Offline – Stand ${S.loadedAt ? hm(S.loadedAt) : "?"}${wait}${S.outside ? " · dieser Zeitraum ist nicht gespeichert" : ""}${Q.conflicts ? ` · <button type="button" class="link" data-sync>${Q.conflicts} Konflikt${Q.conflicts > 1 ? "e" : ""}</button>` : ""}`; kind = "offline"; }
  else if (Q.conflicts) { bar.innerHTML = `${Q.conflicts} Konflikt${Q.conflicts > 1 ? "e" : ""} <button type="button" class="link" data-sync>Ansehen</button>`; kind = "conflict"; }
  else if (Q.errors) { bar.innerHTML = `${Q.errors} Änderung${Q.errors > 1 ? "en konnten" : " konnte"} nicht gespeichert werden <button type="button" class="link" data-sync>Ansehen</button>`; kind = "conflict"; }
  else if (Q.pending) { bar.innerHTML = `${Q.pending} Änderung${Q.pending > 1 ? "en" : ""} werden gesendet <button type="button" class="link" data-sync>Details</button>`; kind = "pending"; }
  else bar.textContent = "";
  bar.dataset.kind = kind;
  document.body.classList.toggle("show-bar", !!kind);
  document.body.classList.toggle("syncing", S.net === "online" && Q.pending > 0 && !Q.conflicts);
  const c = $("#syncCnt"); if (c) c.textContent = Q.count || "";
}

function paint(quiet) {
  $("#title").innerHTML = `${titleText()}<span class="ms caret">arrow_drop_down</span>`;
  toolbar();
  $("#drawer").querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("act", b.dataset.view === S.view));
  $("#today").classList.toggle("pulse", Math.abs(daysBetween(new Date(), S.date)) > 40);
  document.body.dataset.view = S.view;
  applyTheme();
  paintOffline();
  const stage = $("#stage"), old = stage.firstElementChild, keep = quiet && prevView === S.view ? old?.scrollTop : null;
  const html = { monthday, month, agenda, year }[S.view]?.() ?? timegrid();
  const v = document.createElement("div");
  const zoom = prevView && prevView !== S.view && (S.view === "day" || prevView === "day");
  v.className = `view v-${S.view}${quiet && prevView === S.view ? "" : zoom ? " zoom" : dir < 0 ? " back" : dir > 0 ? " fwd" : " fade"}`;
  v.innerHTML = html; stage.replaceChildren(v);
  dir = 0; prevView = S.view;
  // Hover-Vorschau am PC
  const byId = new Map(S.events.map((e) => [e.id, e]));
  v.querySelectorAll("[data-id]").forEach((n) => { const e = byId.get(n.dataset.id); if (e) n.title = `${e.title}\n${e.all_day ? "ganztägig" : `${hm(new Date(e.start))} – ${hm(new Date(e.end))}`}${e.location ? `\n${e.location}` : ""}`; });
  if (["week", "day", "days3"].includes(S.view)) {
    const wh = /^(\d\d):/.exec(S.settings.work_hours || ""), now = new Date();
    const show = gridDays().some((d) => sameDay(d, now));
    v.scrollTop = keep ?? Math.max(0, ((show ? now.getHours() : wh ? +wh[1] : 8) - 1) * S.hh);
    bindGrid(v, {
      create: (s, e) => openEditor({ start: s, end: e }),
      move: async (ev, s, e, edge) => { await patchEvent(ev, ev.all_day ? {} : { start: `${ymd(s)}T${hm(s)}`, end: `${ymd(e)}T${hm(e)}` }); if (edge) nav(edge); },
      repaint: () => paint(true),
      shift: { week: 7, days3: 3, day: 1 }[S.view],
    });
  } else if (keep) v.scrollTop = keep;
  if (S.view === "monthday") bindGrip(v, () => paint(true));
  if (S.view === "agenda") {
    bindSwipe(v, (id) => { const ev = S.events.find((x) => x.id === id); if (ev) deleteEvent(ev); }, async (id) => {
      const ev = S.events.find((x) => x.id === id); if (!ev) return;
      if (new Date(ev.end) < new Date()) { toggleDone(ev.id); paint(true); toast(isDone(ev.id) ? `„${ev.title}“ erledigt` : "Wieder offen"); return; }
      if (ev.read_only) return;
      const m = await choose(`Erinnerung für „${ev.title}“`, [["10", "10 Min. vorher"], ["60", "1 Std. vorher"], ["1440", "1 Tag vorher"], ["0", "Zum Beginn"]]);
      if (m != null) patchEvent(ev, { alarms: [...new Set([...(ev.alarms || []), +m])] }, "Erinnerung");
    });
    if (!quiet) { const t = v.querySelector(`.lh[data-day="${ymd(S.agFrom)}"]`); if (t) v.scrollTop = 0; }
    const end = v.querySelector("#agEnd");
    if (end) new IntersectionObserver(async (en, o) => { if (en[0].isIntersecting) { o.disconnect(); S.agTo = addDays(S.agTo, 45); const t0 = $(".view").scrollTop; await render(true); $(".view").scrollTop = t0; } }, { root: v, rootMargin: "400px" }).observe(end);
  }
  // Leiste beim Runterscrollen weg, beim Hochscrollen zurück; Top-Bar bekommt beim Scrollen eine Fläche
  let last = v.scrollTop;
  v.addEventListener("scroll", () => {
    const y = v.scrollTop;
    $("#top").classList.toggle("scrolled", y > 4);
    if (phone() && Math.abs(y - last) > 8) { document.body.classList.toggle("bar-hidden", y > last && y > 60); last = y; }
  }, { passive: true });
  document.body.classList.remove("bar-hidden");
  drawMini();
}

/** Keine Autofill-Leiste (Passwörter, Karten, Adressen) über der Tastatur: alle Eingabefelder bekommen „autocomplete=off“ – auch später geöffnete. */
function noAutofill() {
  const fix = (n) => {
    if (!(n instanceof Element)) return;
    for (const f of n.matches("input,textarea") ? [n, ...n.querySelectorAll("input,textarea")] : n.querySelectorAll("input,textarea")) {
      if (f.type === "file" || f.type === "checkbox") continue;
      f.setAttribute("autocomplete", "off");
      f.setAttribute("data-lpignore", "true");      // Passwortmanager
      f.setAttribute("data-form-type", "other");
      if (!f.name) f.name = `kal-${f.id || f.placeholder?.toLowerCase().replace(/\W+/g, "-") || "feld"}`;   // kein Name wie „city“ oder „address“, der Adress-Autofill auslöst
    }
  };
  fix(document.body);
  new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach(fix))).observe(document.body, { childList: true, subtree: true });
}
