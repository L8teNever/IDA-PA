// IDA Tasks: drei Bereiche nebeneinander – links Erledigt, in der Mitte Heute (Start), rechts Geplant.
// Karten wischen: nach rechts = planen („mach ich am …“), nach links = erledigt. In „Erledigt“: rechts = zurückholen, links = löschen.
// Antippen = Details (Priorität, Tag, Frist, Notiz). Unten: neue Aufgabe tippen oder mit Stella sprechen (js/voice.js).
import { openVoice } from "./voice.js";

const VIEWS = ["erledigt", "heute", "geplant"];
const WD = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const WDL = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
const MON = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."];
const PRIO = [["Keine", "remove"], ["Niedrig", "keyboard_arrow_down"], ["Mittel", "drag_handle"], ["Hoch", "priority_high"]];
const AGENTS = { stella: "Stella", ida: "Ida" };

const S = { i: 1, lists: { erledigt: [], heute: [], geplant: [] }, counts: {}, today: ymd(new Date()), voice: false, es: null, open: null };
const $ = (id) => document.getElementById(id);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const haptic = () => navigator.vibrate?.(8);

function ymd(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function parseYmd(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
function addDays(s, n) { const d = parseYmd(s); d.setDate(d.getDate() + n); return ymd(d); }
function diff(a, b) { return Math.round((parseYmd(a) - parseYmd(b)) / 864e5); }
function dayText(s) {
  const n = diff(s, S.today);
  if (n === 0) return "Heute";
  if (n === 1) return "Morgen";
  if (n === 2) return "Übermorgen";
  if (n === -1) return "Gestern";
  const d = parseYmd(s);
  return n > 0 && n < 7 ? WDL[d.getDay()] : `${WD[d.getDay()]}, ${d.getDate()}. ${MON[d.getMonth()]}`;
}
function longDate(s) { const d = parseYmd(s); return `${WDL[d.getDay()]}, ${d.getDate()}. ${MON[d.getMonth()]}`; }

// ---------------- API ----------------
async function api(method, path, body) {
  const r = await fetch(`/api/v1${path}`, { method, headers: body ? { "content-type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined, credentials: "same-origin" });
  if (!r.ok) {
    let msg = `Fehler ${r.status}`;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : Array.isArray(j.detail) ? j.detail[0]?.msg || msg : msg; } catch { /* kein JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

let loading = null, again = false;
async function load() {
  if (loading) { again = true; return loading; }
  loading = (async () => {
    try {
      const [h, g, e, c] = await Promise.all([api("GET", "/tasks?view=heute"), api("GET", "/tasks?view=geplant"), api("GET", "/tasks?view=erledigt&limit=150"), api("GET", "/counts")]);
      S.lists = { heute: h.tasks, geplant: g.tasks, erledigt: e.tasks };
      S.counts = c;
      S.today = h.today || S.today;
      $("offline").hidden = true;
      render();
    } catch {
      $("offline").hidden = false;
    }
  })();
  await loading;
  loading = null;
  if (again) { again = false; await load(); }
}

// ---------------- Oberfläche ----------------
function shell() {
  $("app").innerHTML = `
    <header class="top">
      <div class="ida-mini" aria-hidden="true"><i></i><i></i></div>
      <nav class="tabs" id="tabs" style="--i:${S.i}"><span class="ind"></span>
        ${VIEWS.map((v, i) => `<button class="tab ${i === S.i ? "on" : ""}" data-i="${i}"><span class="ms">${["task_alt", "today", "event_upcoming"][i]}</span><span class="lbl">${["Erledigt", "Heute", "Geplant"][i]}</span><span class="n" id="n-${v}">0</span></button>`).join("")}
      </nav>
    </header>
    <div class="offline" id="offline" hidden>Keine Verbindung – versuche es gleich wieder</div>
    <main class="panes" id="panes"><div class="track" id="track" style="--i:${S.i}">
      ${VIEWS.map((v) => `<section class="pane" data-v="${v}"><div class="pane-in" id="p-${v}"></div></section>`).join("")}
    </div></main>
    <div class="bar">
      <form class="add" id="add" autocomplete="off"><input id="addIn" placeholder="Neue Aufgabe …" enterkeyhint="done" maxlength="300">
        <button class="icon-btn go" id="addGo" type="submit" aria-label="Hinzufügen" hidden><span class="ms">arrow_upward</span></button></form>
      <button class="fab" id="talk" aria-label="Mit Stella sprechen" hidden><span class="ms">graphic_eq</span></button>
    </div>
    <div class="ev-scrim" id="scrim"></div><div class="ev-sheet" id="sheet" role="dialog" aria-modal="true"></div>
    <div class="ttoast" id="tt"><span id="ttText"></span><button id="ttBtn"></button></div>`;
  $("tabs").addEventListener("click", (e) => { const b = e.target.closest(".tab"); if (b) go(+b.dataset.i); });
  $("add").onsubmit = (e) => { e.preventDefault(); add(); };
  $("addIn").oninput = () => { $("addGo").hidden = !$("addIn").value.trim(); };
  $("talk").onclick = () => openVoice({ onChange: load });
  $("scrim").onclick = closeSheet;
  bindPaneSwipe();
}

function go(i, quiet) {
  S.i = Math.max(0, Math.min(2, i));
  $("track").style.setProperty("--i", S.i);
  $("tabs").style.setProperty("--i", S.i);
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("on", +b.dataset.i === S.i));
  $("addIn").placeholder = S.i === 2 ? "Neue Aufgabe für morgen …" : "Neue Aufgabe …";
  if (!quiet) haptic();
}

function chips(t) {
  const c = [];
  if (t.priority) c.push(`<span class="chip p${t.priority}"><span class="ms">${PRIO[t.priority][1]}</span>${PRIO[t.priority][0]}</span>`);
  if (t.state === "geplant" && t.day) c.push(`<span class="chip"><span class="ms">event</span>${esc(dayText(t.day))}</span>`);
  if (t.due && !t.done) c.push(`<span class="chip ${t.overdue ? "late" : ""}"><span class="ms">flag</span>${t.overdue ? "überfällig · " : "bis "}${esc(dayText(t.due))}</span>`);
  if (t.done && t.done_at) c.push(`<span class="chip"><span class="ms">check</span>${esc(dayText(ymd(new Date(t.done_at))))}</span>`);
  if (AGENTS[t.created_by]) c.push(`<span class="chip by"><span class="ms">auto_awesome</span>von ${AGENTS[t.created_by]}</span>`);
  if (t.note) c.push(`<span class="chip note"><span class="ms">notes</span>${esc(t.note.split("\n")[0].slice(0, 60))}</span>`);
  return c.join("");
}

function card(t) {
  const del = t.done;
  return `<div class="card ${t.done ? "done" : ""}" data-id="${t.id}" data-p="${t.priority}">
    <div class="card-bg r"><span class="ms">${del ? "undo" : "event"}</span>${del ? "Zurückholen" : "Planen"}</div>
    <div class="card-bg l ${del ? "del" : ""}">${del ? "Löschen" : "Erledigt"}<span class="ms">${del ? "delete" : "check"}</span></div>
    <div class="card-fg"><button class="chk" aria-label="${t.done ? "Nicht erledigt" : "Erledigt"}"><span class="ms">check</span></button>
      <div class="ct"><b>${esc(t.title)}</b><div class="meta">${chips(t)}</div></div></div></div>`;
}

function empty(v) {
  const m = {
    heute: ["wb_sunny", "Alles erledigt für heute", "Tipp unten etwas ein oder sag es Stella."],
    geplant: ["event_upcoming", "Nichts geplant", "Wisch eine Aufgabe nach rechts, dann wählst du den Tag."],
    erledigt: ["task_alt", "Noch nichts erledigt", "Wisch eine Aufgabe nach links, wenn sie fertig ist."],
  }[v];
  return `<div class="empty"><span class="ms">${m[0]}</span><b>${m[1]}</b>${m[2]}</div>`;
}

function render() {
  for (const v of VIEWS) {
    const n = $(`n-${v}`);
    if (!n) continue;
    const c = v === "erledigt" ? S.lists.erledigt.length : S.counts[v] ?? S.lists[v].length;
    n.textContent = c;
    n.toggleAttribute("data-zero", !c);   // leere Reiter ohne „0“ – ruhiger und mehr Platz
  }
  const list = S.lists;
  const head = (h1, sub) => `<div class="ph"><h1>${h1}</h1><small>${sub}</small></div>`;
  $("p-heute").innerHTML = head("Heute", esc(longDate(S.today)))
    + (list.heute.length ? `<div class="list">${list.heute.map(card).join("")}</div><div class="swipe-hint">→ planen · ← erledigt</div>` : empty("heute"));
  // Geplant nach Tagen gruppiert
  let g = "", last = "";
  for (const t of list.geplant) {
    if (t.day !== last) { g += `${last ? "</div>" : ""}<div class="group"><span class="ms">event</span>${esc(dayText(t.day))}${diff(t.day, S.today) >= 7 ? "" : ` · ${parseYmd(t.day).getDate()}. ${MON[parseYmd(t.day).getMonth()]}`}</div><div class="list">`; last = t.day; }
    g += card(t);
  }
  $("p-geplant").innerHTML = head("Geplant", list.geplant.length ? `${list.geplant.length} Aufgabe${list.geplant.length > 1 ? "n" : ""}` : "")
    + (g ? g + "</div>" : empty("geplant"));
  $("p-erledigt").innerHTML = head("Erledigt", list.erledigt.length ? "← löschen · → zurückholen" : "")
    + (list.erledigt.length ? `<div class="list">${list.erledigt.map(card).join("")}</div>` : empty("erledigt"));
  document.querySelectorAll(".card").forEach(bindCard);
  if (S.open) refreshSheet();
}

const find = (id) => [...S.lists.heute, ...S.lists.geplant, ...S.lists.erledigt].find((t) => t.id === id);

// ---------------- Aktionen ----------------
async function patch(id, body, msg, undo) {
  try {
    const r = await api("PATCH", `/tasks/${id}`, body);
    if (msg) toast(msg, undo ? { label: "Rückgängig", run: () => patch(id, undo) } : null);
    await load();
    return r.task;
  } catch (e) { toast(e.message); await load(); }
}

async function add() {
  const inp = $("addIn"), title = inp.value.trim();
  if (!title) return;
  inp.value = "";
  $("addGo").hidden = true;
  const day = S.i === 2 ? addDays(S.today, 1) : null;
  try {
    await api("POST", "/tasks", { title, day });
    if (S.i === 0) go(1, true);
    toast(day ? "Für morgen geplant" : "Hinzugefügt");
    await load();
  } catch (e) { inp.value = title; toast(e.message); }
}

function done(t, el) {
  haptic();
  el?.classList.add("gone");
  setTimeout(() => patch(t.id, { done: true }, `„${t.title.slice(0, 40)}“ erledigt`, { done: false }), el ? 220 : 0);
}

async function remove(t, el) {
  haptic();
  el?.classList.add("gone");
  try {
    await api("DELETE", `/tasks/${t.id}`);
    toast("Gelöscht", { label: "Rückgängig", run: async () => {
      await api("POST", "/tasks", { title: t.title, note: t.note, priority: t.priority, due: t.due }).catch((e) => toast(e.message));
      load();
    } });
  } catch (e) { toast(e.message); }
  load();
}

function plan(t, day) {
  const prev = t.day && diff(t.day, S.today) > 0 ? t.day : null;
  closeSheet();
  patch(t.id, { day }, day && diff(day, S.today) > 0 ? `Geplant: ${dayText(day)}` : "Wieder auf heute", { day: prev });
}

// ---------------- Wischen ----------------
let swallow = 0;
function bindCard(el) {
  const fg = el.querySelector(".card-fg");
  const t = find(el.dataset.id);
  if (!t) return;
  let x0 = 0, y0 = 0, dx = 0, mode = null, id = null;
  const limit = () => Math.min(120, el.offsetWidth * 0.3);
  fg.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    x0 = e.clientX; y0 = e.clientY; dx = 0; mode = "maybe"; id = e.pointerId;
  });
  fg.addEventListener("pointermove", (e) => {
    if (!mode || e.pointerId !== id) return;
    const mx = e.clientX - x0, my = e.clientY - y0;
    if (mode === "maybe") {
      if (Math.abs(my) > 10 && Math.abs(my) > Math.abs(mx)) { mode = null; return; }
      if (Math.abs(mx) < 10) return;
      mode = "swipe";
      el.classList.add("drag");
      fg.setPointerCapture(id);
    }
    dx = mx;
    const armed = Math.abs(dx) > limit();
    if (armed !== el.classList.contains("armed")) { el.classList.toggle("armed", armed); if (armed) haptic(); }
    el.classList.toggle("sw-r", dx > 0);
    el.classList.toggle("sw-l", dx < 0);
    fg.style.transform = `translateX(${dx}px)`;
  });
  const end = () => {
    if (mode !== "swipe") { mode = null; return; }
    mode = null;
    swallow = Date.now();
    el.classList.remove("drag");
    const armed = Math.abs(dx) > limit();
    el.classList.remove("armed");
    if (!armed) { fg.style.transform = ""; setTimeout(() => el.classList.remove("sw-r", "sw-l"), 300); return; }
    if (dx > 0) {
      fg.style.transform = "";
      setTimeout(() => el.classList.remove("sw-r"), 300);
      if (t.done) patch(t.id, { done: false }, "Zurückgeholt", { done: true });
      else openPlan(t);
    } else {
      fg.style.transform = `translateX(-110%)`;
      if (t.done) remove(t, el); else done(t, el);
    }
  };
  fg.addEventListener("pointerup", end);
  fg.addEventListener("pointercancel", () => { if (mode === "swipe") { dx = 0; end(); } mode = null; });
  fg.addEventListener("click", (e) => {
    if (Date.now() - swallow < 350) return;
    if (e.target.closest(".chk")) {
      e.stopPropagation();
      if (t.done) patch(t.id, { done: false }, "Zurückgeholt", { done: true }); else done(t, el);
      return;
    }
    openDetail(t);
  });
}

// Wischen auf freier Fläche (nicht auf einer Karte) wechselt den Bereich
function bindPaneSwipe() {
  const p = $("panes"), tr = $("track");
  let x0 = 0, y0 = 0, mode = null, w = 1;
  p.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 1 || e.target.closest(".card")) { mode = null; return; }
    x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; mode = "maybe"; w = p.offsetWidth;
  }, { passive: true });
  p.addEventListener("touchmove", (e) => {
    if (!mode) return;
    const dx = e.touches[0].clientX - x0, dy = e.touches[0].clientY - y0;
    if (mode === "maybe") {
      if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { mode = null; return; }
      if (Math.abs(dx) < 12) return;
      mode = "pan";
      tr.classList.add("drag");
    }
    const edge = (S.i === 0 && dx > 0) || (S.i === 2 && dx < 0) ? 0.3 : 1;
    tr.style.transform = `translateX(calc(${S.i * -33.3333}% + ${dx * edge}px))`;
  }, { passive: true });
  p.addEventListener("touchend", (e) => {
    if (mode !== "pan") { mode = null; return; }
    mode = null;
    const dx = e.changedTouches[0].clientX - x0;
    tr.classList.remove("drag");
    tr.style.transform = "";
    if (Math.abs(dx) > w * 0.22) go(S.i + (dx < 0 ? 1 : -1));
  });
}

// ---------------- Sheets ----------------
function openSheet(html, id) {
  S.open = id || null;
  $("sheet").innerHTML = `<div class="handle"></div>${html}`;
  document.body.classList.add("ev-open");
}
function closeSheet() {
  if (!document.body.classList.contains("ev-open")) return;
  const a = document.activeElement;
  if (a && $("sheet").contains(a)) a.blur();   // speichert Titel/Notiz (blur) vor dem Schließen
  document.body.classList.remove("ev-open");
  S.open = null;
}
addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

function planChoices() {
  const out = [["Morgen", 1, "wb_twilight"], ["Übermorgen", 2, "looks_two"]];
  const wd = parseYmd(S.today).getDay();
  const toSat = (6 - wd + 7) % 7 || 7, toMon = (1 - wd + 7) % 7 || 7;
  if (toSat > 2) out.push(["Am Wochenende", toSat, "weekend"]);
  if (toMon > 2) out.push(["Nächste Woche", toMon, "date_range"]);
  out.push(["In einer Woche", 7, "event_repeat"], ["In zwei Wochen", 14, "calendar_month"]);
  const seen = new Set();
  return out.filter(([, n]) => !seen.has(n) && seen.add(n));
}

function openPlan(t) {
  const cur = t.day && diff(t.day, S.today) > 0;
  openSheet(`<div class="sh-h"><h2>Wann machst du das?</h2><button class="icon-btn es-x" data-x><span class="ms">close</span></button></div>
    <div class="hint" style="margin:-6px 4px 14px;color:var(--muted);font-size:.875rem">„${esc(t.title)}“ verschwindet bis dahin von der Startseite.</div>
    <div class="chips">${planChoices().map(([l, n, ic]) => { const d = addDays(S.today, n); return `<button class="pick" data-d="${d}"><span class="ms">${ic}</span><span><b>${l}</b><small>${esc(longDate(d))}</small></span></button>`; }).join("")}</div>
    <label class="fld"><span>Anderer Tag</span><input class="in" type="date" id="planDate" min="${addDays(S.today, 1)}" value="${cur ? t.day : ""}"></label>
    ${cur ? `<div class="sheet-acts"><button class="es-btn" data-d=""><span class="ms">today</span>Doch heute</button></div>` : ""}`);
  const sh = $("sheet");
  sh.querySelector("[data-x]").onclick = closeSheet;
  sh.querySelectorAll("[data-d]").forEach((b) => { b.onclick = () => plan(t, b.dataset.d || null); });
  $("planDate").onchange = (e) => { if (e.target.value) plan(t, e.target.value); };
}

function openDetail(t) {
  S.open = t.id;
  openSheet(detailHtml(t), t.id);
  bindDetail(t);
}

function detailHtml(t) {
  const future = t.day && diff(t.day, S.today) > 0;
  return `<div class="sh-h"><h2>Aufgabe</h2><button class="icon-btn es-x" data-x><span class="ms">close</span></button></div>
    <label class="fld"><input class="in" id="dTitle" value="${esc(t.title)}" maxlength="300" enterkeyhint="done"></label>
    <div class="fld"><span>Priorität</span><div class="seg" id="dPrio">${PRIO.map(([l, ic], p) => `<button data-p="${p}" class="${t.priority === p ? "on" : ""}"><span class="ms">${ic}</span>${l}</button>`).join("")}</div></div>
    <div class="row2">
      <label class="fld"><span>Mach ich am</span><div class="dwrap"><input class="in" type="date" id="dDay" min="${S.today}" value="${future ? t.day : ""}">${future ? `<button class="icon-btn clr" data-clr="day" aria-label="Heute"><span class="ms">close</span></button>` : ""}</div></label>
      <label class="fld"><span>Fertig bis</span><div class="dwrap"><input class="in" type="date" id="dDue" value="${t.due || ""}">${t.due ? `<button class="icon-btn clr" data-clr="due" aria-label="Keine Frist"><span class="ms">close</span></button>` : ""}</div></label>
    </div>
    <label class="fld"><span>Notiz</span><textarea class="in" id="dNote" rows="3" maxlength="5000" placeholder="Details, Links, Gedanken …">${esc(t.note)}</textarea></label>
    <div class="sheet-acts">
      ${t.done ? `<button class="es-btn" id="dUndo"><span class="ms">undo</span>Zurückholen</button>` : `<button class="es-btn primary" id="dDone"><span class="ms">check</span>Erledigt</button>`}
      ${t.done ? "" : `<button class="es-btn" id="dPlan"><span class="ms">event</span>Planen</button>`}
      <button class="es-btn danger" id="dDel" aria-label="Löschen" style="flex:0 0 52px"><span class="ms">delete</span></button>
    </div>
    ${AGENTS[t.created_by] ? `<div class="swipe-hint">Angelegt von ${AGENTS[t.created_by]}</div>` : ""}`;
}

function bindDetail(t) {
  const sh = $("sheet");
  sh.querySelector("[data-x]").onclick = closeSheet;
  const save = async (body) => {
    try { const r = await api("PATCH", `/tasks/${t.id}`, body); Object.assign(t, r.task); load(); } catch (e) { toast(e.message); }
  };
  $("dTitle").onblur = () => { const v = $("dTitle").value.trim(); if (v && v !== t.title) save({ title: v }); };
  $("dTitle").onkeydown = (e) => { if (e.key === "Enter") $("dTitle").blur(); };
  $("dNote").onblur = () => { const v = $("dNote").value; if (v.trim() !== (t.note || "")) save({ note: v }); };
  $("dPrio").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    haptic();
    $("dPrio").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    save({ priority: +b.dataset.p });
  };
  $("dDay").onchange = (e) => save({ day: e.target.value || null });
  $("dDue").onchange = (e) => save({ due: e.target.value || null });
  sh.querySelectorAll("[data-clr]").forEach((b) => { b.onclick = (e) => { e.preventDefault(); save({ [b.dataset.clr]: null }); }; });
  $("dDone")?.addEventListener("click", () => { closeSheet(); done(t, document.querySelector(`.card[data-id="${t.id}"]`)); });
  $("dUndo")?.addEventListener("click", () => { closeSheet(); patch(t.id, { done: false }, "Zurückgeholt", { done: true }); });
  $("dPlan")?.addEventListener("click", () => openPlan(t));
  $("dDel").onclick = () => { closeSheet(); remove(t, document.querySelector(`.card[data-id="${t.id}"]`)); };
}

// Die offene Detail-Ansicht übernimmt Änderungen von außen (Stella, Ida, anderes Gerät), solange nichts darin fokussiert ist
function refreshSheet() {
  const t = find(S.open);
  if (!t || !$("dTitle")) return;
  if ($("sheet").contains(document.activeElement) && document.activeElement !== document.body) return;
  $("sheet").innerHTML = `<div class="handle"></div>${detailHtml(t)}`;
  bindDetail(t);
}

// ---------------- Toast ----------------
let ttTimer = 0;
function toast(text, action) {
  $("ttText").textContent = text;
  const b = $("ttBtn");
  b.textContent = action?.label || "";
  b.onclick = () => { $("tt").classList.remove("show"); action?.run(); };
  $("tt").classList.add("show");
  clearTimeout(ttTimer);
  ttTimer = setTimeout(() => $("tt").classList.remove("show"), action ? 5000 : 2400);
}

// ---------------- Live (nur solange sichtbar – Akku) ----------------
let reloadTimer = 0;
function live(on) {
  if (!on) { S.es?.close(); S.es = null; return; }
  if (S.es) return;
  S.es = new EventSource("/api/v1/stream");
  S.es.onmessage = () => { clearTimeout(reloadTimer); reloadTimer = setTimeout(load, 150); };
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return live(false);
  const t = ymd(new Date());
  if (t !== S.today) S.today = t;   // Mitternacht: Geplantes von heute rutscht auf die Startseite
  load();
  live(true);
});

async function boot() {
  shell();
  go(1, true);
  load();
  live(!document.hidden);
  try {
    const i = await api("GET", "/info");
    S.voice = i.voice;
    $("talk").hidden = !i.voice;
    if (i.voice && new URLSearchParams(location.search).has("sprechen")) {
      history.replaceState(null, "", "/");
      openVoice({ onChange: load });
    }
  } catch { /* offline: ohne Sprechen */ }
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}
boot();
