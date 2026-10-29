// Detail-Sheet, Editor, Serien-Rückfrage, Schnelleingabe, Suche, Papierkorb, Verlauf, Einstellungen, Kalender-Einstellungen, Erinnerung.
import { api } from "./api.js";
import { S, calOf, send, writable } from "./store.js";
import { addDays, ago, countdown, dayMonth, durText, hm, longDay, monthName, parseYmd, shortDay, weekdayShort, whenText, ymd } from "./dates.js";
import { byIda, card, cstyle, esc } from "./views.js";
import { pickDate, pickTime } from "./pickers.js";

const $ = (s, r = document) => r.querySelector(s);
let hooks = { changed: () => {}, jump: () => {} };
export const setHooks = (h) => { hooks = { ...hooks, ...h }; };

// ---------------- Ebenen + Zurück-Geste (Android-Zurück schließt erst das Sheet) ----------------
const layers = [];
function pushLayer(close) { layers.push(close); history.pushState({ ...history.state, layer: layers.length }, ""); }
/** Wird bei popstate aufgerufen; true = es wurde eine Ebene geschlossen. */
let waiters = [];
export function popLayer() { const c = layers.pop(); if (c) { c(); waiters.shift()?.(); return true; } return false; }
/** Oberste Ebene schließen (über die History, damit Android-Zurück dasselbe tut); Promise, wenn sie zu ist. */
const closeTop = () => new Promise((r) => { if (!layers.length) return r(); waiters.push(r); history.back(); });
export const layerOpen = () => layers.length > 0;

/** Sheet/Dialog. kind: "full" (Editor, Listen) | "bottom" (Auswahl) */
function overlay(html, kind = "full") {
  const sc = document.createElement("div"); sc.className = kind === "bottom" ? "bs-scrim" : "ed-scrim";
  const box = document.createElement("div"); box.className = kind === "bottom" ? "bs" : "ed"; box.innerHTML = html;
  document.body.append(sc, box);
  requestAnimationFrame(() => box.classList.add("in"));
  let closed = false;
  const remove = () => { if (closed) return; closed = true; box.classList.remove("in"); sc.remove(); setTimeout(() => box.remove(), 220); };
  pushLayer(remove);
  sc.onclick = closeTop;
  box.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeTop));
  return { box, close: closeTop };
}

// ---------------- Snackbar ----------------
let tt;
export function toast(msg, actions = []) {
  const el = $("#toast");
  el.innerHTML = `<span>${esc(msg)}</span>${actions.map((a, i) => `<button data-i="${i}">${esc(a.label)}</button>`).join("")}`;
  el.classList.toggle("act", actions.length > 0);
  el.classList.add("show");
  el.querySelectorAll("button").forEach((b) => (b.onclick = () => { el.classList.remove("show"); actions[+b.dataset.i].run(); }));
  clearTimeout(tt); tt = setTimeout(() => el.classList.remove("show"), actions.length ? 6500 : 2800);
}
const fail = (e, retry) => toast(e.message || "Fehler", retry ? [{ label: "Erneut versuchen", run: retry }] : []);
const undoAction = (r) => (r?.change_id ? [{ label: "Rückgängig", run: async () => { try { await api(`/changes/${r.change_id}/undo`, { method: "POST" }); hooks.changed(); } catch (x) { fail(x); } } }] : []);

/** Auswahl als Bottom-Sheet; gibt den Wert zurück (oder null). */
export function choose(title, options, { multi = false, selected = [] } = {}) {
  return new Promise((res) => {
    let done = false, sel = new Set(selected);
    const { box, close } = overlay(`<div class="handle"></div><h3 class="bs-t">${esc(title)}</h3>${options.map(([v, l, ic, style]) => `<button class="opt${sel.has(v) ? " on" : ""}" data-v="${esc(v)}" style="${style || ""}">${ic ? `<span class="ms">${ic}</span>` : ""}<span>${esc(l)}</span>${multi || sel.has(v) ? '<span class="ms ck">check</span>' : ""}</button>`).join("")}${multi ? '<button class="save wide" data-ok>Übernehmen</button>' : ""}`, "bottom");
    const finish = (v) => { if (done) return; done = true; close().then(() => res(v)); };
    box.querySelectorAll("[data-v]").forEach((b) => (b.onclick = () => {
      if (!multi) return finish(b.dataset.v);
      sel.has(b.dataset.v) ? sel.delete(b.dataset.v) : sel.add(b.dataset.v); b.classList.toggle("on");
    }));
    $("[data-ok]", box)?.addEventListener("click", () => finish([...sel]));
    const obs = new MutationObserver(() => { if (!box.isConnected) { obs.disconnect(); if (!done) { done = true; res(null); } } });
    obs.observe(document.body, { childList: true });
  });
}
export const askScope = (verb) => choose(`${verb} – wiederholter Termin`, [["this", "Nur dieser Termin", "event"], ["following", "Dieser und alle folgenden", "keyboard_double_arrow_right"], ["all", "Alle Termine der Serie", "event_repeat"]]);
const confirmBox = (title, yes) => choose(title, [["y", yes, "check"]]).then((v) => v === "y");

/** Termin ändern (Ziehen im Raster): Serien-Rückfrage, dann Snackbar mit Rückgängig. */
export async function patchEvent(ev, body, verb = "Verschieben") {
  let scope = "all";
  if (ev.recurring) { scope = await askScope(verb); if (!scope) { hooks.changed(); return; } }
  try {
    const r = await send(`/events/${encodeURIComponent(ev.id)}?scope=${scope}`, { method: "PATCH", body }, ev.id);
    toast(r.queued ? "Offline – wird später gesendet" : `„${ev.title}“ geändert`, undoAction(r));
  } catch (x) { fail(x); }
  hooks.changed();
}

export async function deleteEvent(ev) {
  let scope = "all";
  if (ev.recurring) { scope = await askScope("Löschen"); if (!scope) { hooks.changed(); return; } }
  try {
    const r = await send(`/events/${encodeURIComponent(ev.id)}?scope=${scope}`, { method: "DELETE" }, ev.id);
    toast(r.queued ? "Offline – wird später gelöscht" : "Termin gelöscht", undoAction(r));
  } catch (x) { fail(x); }
  hooks.changed();
}

// ---------------- kleines Markdown mit abhakbaren Checklisten ----------------
function md(text) {
  return esc(text).split("\n").map((line, i) => {
    const m = /^\s*[-*] \[( |x|X)\] (.*)$/.exec(line);
    if (m) return `<button class="ckl${m[1] !== " " ? " on" : ""}" data-line="${i}"><span class="ms">${m[1] !== " " ? "check_box" : "check_box_outline_blank"}</span><span>${m[2]}</span></button>`;
    return `<div>${line.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>') || "&nbsp;"}</div>`;
  }).join("");
}

// ---------------- Detail ----------------
const ACT = { create: "angelegt", update: "geändert", delete: "gelöscht", restore: "wiederhergestellt", undo: "zurückgenommen", move: "verschoben" };
const alarmText = (m) => (m === 0 ? "Zum Beginn" : m % 10080 === 0 ? `${m / 10080} Woche(n) vorher` : m % 1440 === 0 ? `${m / 1440} Tag(e) vorher` : m % 60 === 0 ? `${m / 60} Std. vorher` : `${m} Min. vorher`);
let detailOpen = null;

export async function openDetail(id) {
  let e;
  try { e = (await api(`/events/${encodeURIComponent(id)}`)).event; } catch (x) { return fail(x); }
  const ro = e.read_only, cal = calOf(e.calendar_id);
  const sc = document.createElement("div"); sc.className = "ev-scrim";
  const sh = document.createElement("div"); sh.className = "ev-sheet";
  const row = (ic, t, extra = "") => (t ? `<div class="es-row"><span class="ms">${ic}</span><div class="es-c">${t}</div>${extra}</div>` : "");
  const s = new Date(e.start), en = new Date(e.end), mins = Math.round((en - s) / 6e4);
  const [cd, cdc] = countdown(e);
  sh.style.cssText = cstyle(e.color);
  sh.innerHTML = `<div class="es-stripe"></div><div class="handle"></div>
    <div class="es-head"><div class="es-ttl"><h2>${esc(e.title)}</h2>
      <div class="es-time">${esc(whenText(e))}</div>
      <div class="es-sub">${e.all_day ? "" : durText(mins)}${cd ? ` <span class="es-badge ${cdc}">${cd}</span>` : ""}${e.status === "cancelled" ? ' <span class="es-badge">abgesagt</span>' : e.status === "tentative" ? ' <span class="es-badge">vorläufig</span>' : ""}${byIda(e) ? ' <span class="chip-ida"><span class="ms">auto_awesome</span>Ida</span>' : ""}</div></div>
      <button class="icon-btn plain" data-more><span class="ms">more_vert</span></button><button class="icon-btn plain" data-x><span class="ms">close</span></button></div>
    <div class="es-body">
      ${row("event_repeat", esc(e.recurrence_text))}
      ${row("location_on", esc(e.location), e.location ? '<button class="icon-btn plain sm" data-copy title="Ort kopieren"><span class="ms">content_copy</span></button>' : "")}
      ${row("notifications", (e.alarms || []).map(alarmText).join(", ") + (e.alarm_action === "agent" ? `<div class="s">Ida kümmert sich${e.alarm_task ? `: ${esc(e.alarm_task)}` : ""}</div>` : ""))}
      ${e.travel_min ? row("directions_car", `${durText(e.travel_min)} Reisezeit`) : ""}
      ${row("calendar_month", `<span class="dot" style="${cstyle(cal?.color || e.color)}"></span>${esc(cal?.name || e.calendar)}`)}
      ${e.url ? row("link", `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.url)}</a>`) : ""}
      ${e.description ? row("notes", `<div class="es-desc">${md(e.description)}</div>`) : ""}
      <div class="es-hist" id="esHist"></div>
    </div>
    ${ro ? "" : `<div class="es-actions"><button class="btn f" data-edit><span class="ms">edit</span>Bearbeiten</button><button class="btn i" data-dup title="Duplizieren"><span class="ms">content_copy</span></button><button class="btn i" data-share title="Teilen"><span class="ms">share</span></button><button class="btn i danger" data-del title="Löschen"><span class="ms">delete</span></button></div>`}`;
  document.body.append(sc, sh);
  requestAnimationFrame(() => document.body.classList.add("ev-open"));
  const remove = () => { document.body.classList.remove("ev-open", "ev-full"); detailOpen = null; setTimeout(() => { sc.remove(); sh.remove(); }, 300); };
  detailOpen = remove;
  pushLayer(remove);
  sc.onclick = closeTop;
  $("[data-x]", sh).onclick = closeTop;
  dragSheet(sh);

  if (!e.id.startsWith("hol_")) api(`/changes?event_id=${encodeURIComponent(e.event_id)}&limit=1`).then((r) => {
    const c = r.changes[0]; if (!c) return;
    $("#esHist", sh).innerHTML = `<span class="ms">history</span><span>${c.by === "user" ? "von dir" : "von Ida"} ${ACT[c.action] || c.action} · ${ago(c.at)}</span>${c.undone ? "" : `<button class="link" data-undo="${c.id}">Rückgängig</button>`}`;
    $("[data-undo]", sh)?.addEventListener("click", async () => { try { await api(`/changes/${c.id}/undo`, { method: "POST" }); closeTop(); hooks.changed(); } catch (x) { fail(x); } });
  }).catch(() => {});

  $("[data-copy]", sh)?.addEventListener("click", () => { navigator.clipboard?.writeText(e.location); toast("Ort kopiert"); });
  sh.querySelectorAll(".ckl").forEach((b) => (b.onclick = async () => {
    if (ro) return;
    const lines = e.description.split("\n"), i = +b.dataset.line;
    lines[i] = lines[i].replace(/\[( |x|X)\]/, (m) => (m === "[ ]" ? "[x]" : "[ ]"));
    e.description = lines.join("\n");
    b.classList.toggle("on"); b.querySelector(".ms").textContent = b.classList.contains("on") ? "check_box" : "check_box_outline_blank";
    try { await send(`/events/${encodeURIComponent(e.event_id)}?scope=all`, { method: "PATCH", body: { description: e.description } }, e.id); } catch (x) { fail(x); }
  }));
  $("[data-more]", sh).onclick = async () => {
    const v = await choose(e.title, [["show", "Im Kalender zeigen", "calendar_view_day"], ["text", "Als Text kopieren", "content_copy"], ["ics", "Als .ics herunterladen", "download"]]);
    if (v === "show") { await closeTop(); hooks.jump(e); }
    if (v === "text") { navigator.clipboard?.writeText(shareText(e)); toast("Kopiert"); }
    if (v === "ics") downloadIcs(e);
  };
  if (!ro) {
    $("[data-edit]", sh).onclick = () => closeTop().then(() => openEditor({ event: e }));
    $("[data-dup]", sh).onclick = () => closeTop().then(() => openEditor({ event: { ...e, recurring: false, rrule: "" }, copy: true }));
    $("[data-share]", sh).onclick = async () => {
      const text = shareText(e);
      if (navigator.share) { try { await navigator.share({ title: e.title, text }); } catch { /* abgebrochen */ } } else { navigator.clipboard?.writeText(text); toast("Termin als Text kopiert"); }
    };
    $("[data-del]", sh).onclick = () => closeTop().then(() => deleteEvent(e));
  }
}
const shareText = (e) => [e.title, whenText(e), e.location, e.description].filter(Boolean).join("\n");
function downloadIcs(e) {
  const f = (d) => new Date(d).toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const dt = e.all_day ? `DTSTART;VALUE=DATE:${e.start.replace(/-/g, "")}\r\nDTEND;VALUE=DATE:${e.end.replace(/-/g, "")}` : `DTSTART:${f(e.start)}\r\nDTEND:${f(e.end)}`;
  const ics = `BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//IDA Kalender//DE\r\nBEGIN:VEVENT\r\nUID:${e.uid || e.id}\r\nDTSTAMP:${f(new Date())}\r\n${dt}\r\nSUMMARY:${e.title.replace(/[,;]/g, "\\$&")}\r\n${e.location ? `LOCATION:${e.location.replace(/[,;]/g, "\\$&")}\r\n` : ""}END:VEVENT\r\nEND:VCALENDAR\r\n`;
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([ics], { type: "text/calendar" })); a.download = `${e.title.replace(/[^\wäöüÄÖÜß -]/g, "")}.ics`; a.click();
}

/** Detail-Sheet: Griff hochziehen = vollhoch, runterziehen = schließen. */
function dragSheet(sh) {
  const handle = sh.querySelector(".handle"), head = sh.querySelector(".es-head");
  for (const h of [handle, head]) h.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button") || innerWidth >= 1000) return;
    const y0 = e.clientY; let dy = 0;
    sh.style.transition = "none";
    const move = (m) => { dy = m.clientY - y0; sh.style.transform = `translateY(${Math.max(dy, -40)}px)`; };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      sh.style.transition = ""; sh.style.transform = "";
      if (dy > 110) closeTop(); else if (dy < -50) document.body.classList.add("ev-full");
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  });
}

// ---------------- Editor ----------------
const ALARMS = [[10, "10 Min."], [60, "1 Std."], [1440, "1 Tag"]];
const COLORS = ["rot", "koralle", "orange", "gelb", "salbei", "gruen", "tuerkis", "blau", "lavendel", "lila", "graphit"];
const WD_RR = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
const presetOf = (r) => {
  if (!r) return "";
  if (/^FREQ=DAILY$/.test(r)) return "daily";
  if (/^FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR$/.test(r)) return "weekdays";
  if (/^FREQ=WEEKLY(;BYDAY=[A-Z]{2})?$/.test(r)) return "weekly";
  if (/^FREQ=MONTHLY$/.test(r)) return "monthly";
  if (/^FREQ=YEARLY$/.test(r)) return "yearly";
  return "custom";
};

export function openEditor({ event: ev, start, end, copy, allDay } = {}) {
  const editing = !!(ev && ev.id) && !copy;
  const dur = Number(S.settings.default_duration_min) || 60;
  const nextHour = () => { const n = new Date(); n.setHours(n.getHours() + 1, 0, 0, 0); return n; };
  const s0 = ev ? (ev.all_day ? parseYmd(ev.start) : new Date(ev.start)) : start || nextHour();
  const e0 = ev ? (ev.all_day ? addDays(parseYmd(ev.end), -1) : new Date(ev.end)) : end || new Date(s0.getTime() + dur * 6e4);
  const defCal = (S.cals.find((c) => c.is_default && !c.read_only) || writable()[0]);
  const st = {
    title: ev?.title || "", all_day: ev ? !!ev.all_day : !!allDay, calendar_id: ev?.calendar_id || defCal?.id,
    sd: ymd(s0), stime: hm(s0), ed: ymd(e0), etime: hm(e0),
    repeat: presetOf(ev?.rrule), custom: null, alarms: ev ? [...(ev.alarms || [])] : [...(defCal?.default_alarms || [])],
    location: ev?.location || "", description: ev?.description || "", url: ev?.url || "",
    color: ev?.color && ev.color !== calOf(ev.calendar_id)?.color ? ev.color : "",
    status: ev?.status === "tentative" ? "tentative" : (ev?.transparency || "busy") === "free" ? "free" : "busy",
    travel: ev?.travel_min || 0, agent: ev?.alarm_action === "agent", task: ev?.alarm_task || "",
    more: !!(ev && (ev.url || ev.travel_min || ev.status === "tentative" || ev.alarm_action === "agent")),
  };
  const snapshot = JSON.stringify(st);
  const { box } = overlay("");
  let conflictTimer, suggTimer, sugg = [];
  const dt = () => (st.all_day ? [parseYmd(st.sd), addDays(parseYmd(st.ed), 1)] : [new Date(`${st.sd}T${st.stime}`), new Date(`${st.ed}T${st.etime}`)]);
  const wd = () => parseYmd(st.sd).getDay(), wdIdx = () => (wd() + 6) % 7;

  const draw = () => {
    const cal = calOf(st.calendar_id), sd = parseYmd(st.sd);
    const reps = [["", "Nie"], ["daily", "Täglich"], ["weekly", `Wöchentlich am ${weekdayShort(sd)}`], ["monthly", `Monatlich am ${sd.getDate()}.`], ["yearly", "Jährlich"], ["weekdays", "Werktags"]];
    box.innerHTML = `<div class="ed-top"><button class="icon-btn plain" data-x><span class="ms">close</span></button><h2>${editing ? "Bearbeiten" : "Neuer Termin"}</h2><button class="save" data-save>Speichern</button></div>
      <input type="text" class="ttl" id="fTitle" placeholder="Titel" value="${esc(st.title)}" autocomplete="off" enterkeyhint="done">
      <div class="sugg" id="sugg"></div>
      <div class="warn" id="warn" hidden></div>
      <div class="frow"><span class="ms">schedule</span><span class="fl">Ganztägig</span><button class="sw${st.all_day ? " on" : ""}" id="fAll"></button></div>
      <div class="frow dt"><span class="ms"></span><button class="fbtn" data-pick="sd">${shortDay(parseYmd(st.sd))}</button>${st.all_day ? "" : `<button class="fbtn t" data-pick="stime">${st.stime}</button>`}</div>
      <div class="frow dt"><span class="ms"></span><button class="fbtn" data-pick="ed">${shortDay(parseYmd(st.ed))}</button>${st.all_day ? "" : `<button class="fbtn t" data-pick="etime">${st.etime}</button>`}</div>
      <div class="frow wrap"><span class="ms">event_repeat</span><div class="chips">${reps.map(([v, l]) => `<button class="pc${st.repeat === v ? " on" : ""}" data-rep="${v}">${l}</button>`).join("")}<button class="pc${st.repeat === "custom" ? " on" : ""}" data-rep="custom">${st.repeat === "custom" ? esc(st.custom ? customText(st.custom) : ev?.recurrence_text || "Benutzerdefiniert") : "Benutzerdefiniert…"}</button></div></div>
      <div class="frow"><span class="ms">calendar_month</span><button class="pc cal" id="fCal"><span class="dot" style="${cstyle(cal?.color || "blau")}"></span>${esc(cal?.name || "Kalender")}<span class="ms">arrow_drop_down</span></button></div>
      <div class="frow wrap"><span class="ms">notifications</span><div class="chips">${ALARMS.map(([m, l]) => `<button class="pc${st.alarms.includes(m) ? " on" : ""}" data-al="${m}">${l}</button>`).join("")}${st.alarms.filter((m) => !ALARMS.some(([x]) => x === m)).map((m) => `<button class="pc on" data-al="${m}">${alarmText(m).replace(" vorher", "")}</button>`).join("")}<button class="pc" id="alCustom"><span class="ms">add</span>eigene</button></div></div>
      <div class="frow"><span class="ms">location_on</span><input type="text" id="fLoc" placeholder="Ort" value="${esc(st.location)}" list="locs" autocomplete="off"><datalist id="locs">${[...new Set(S.events.map((x) => x.location).filter(Boolean))].slice(0, 30).map((l) => `<option value="${esc(l)}">`).join("")}</datalist></div>
      <div class="frow top"><span class="ms">notes</span><textarea id="fDesc" rows="3" placeholder="Beschreibung (- [ ] für eine Checkliste)">${esc(st.description)}</textarea></div>
      <button class="more-btn" id="fMore"><span class="ms">${st.more ? "expand_less" : "expand_more"}</span>${st.more ? "Weniger Optionen" : "Mehr Optionen"}</button>
      ${st.more ? `
      <div class="frow wrap"><span class="ms">palette</span><div class="chips"><button class="swatch${st.color ? "" : " on"}" data-col="" style="${cstyle(cal?.color || "blau")}" title="Kalenderfarbe"></button>${COLORS.map((c) => `<button class="swatch${st.color === c ? " on" : ""}" data-col="${c}" style="${cstyle(c)}"></button>`).join("")}</div></div>
      <div class="frow wrap"><span class="ms">event_busy</span><div class="chips">${[["busy", "Beschäftigt"], ["free", "Frei"], ["tentative", "Vorläufig"]].map(([v, l]) => `<button class="pc${st.status === v ? " on" : ""}" data-status="${v}">${l}</button>`).join("")}</div></div>
      <div class="frow wrap"><span class="ms">directions_car</span><div class="chips">${[0, 15, 30, 60].map((m) => `<button class="pc${st.travel === m ? " on" : ""}" data-travel="${m}">${m ? `${m} Min.` : "Keine Reisezeit"}</button>`).join("")}</div></div>
      <div class="frow"><span class="ms">auto_awesome</span><span class="fl">Ida kümmert sich bei der Erinnerung</span><button class="sw${st.agent ? " on" : ""}" id="fAgent"></button></div>
      ${st.agent ? `<div class="frow"><span class="ms"></span><input type="text" id="fTask" placeholder="Was soll Ida dann tun? z. B. „Schick mir die Adresse“" value="${esc(st.task)}"></div>` : ""}
      <div class="frow"><span class="ms">link</span><input type="url" id="fUrl" placeholder="Link" value="${esc(st.url)}"></div>` : ""}`;
    bind();
    checkConflicts();
  };
  const read = () => {
    st.title = $("#fTitle", box).value; st.location = $("#fLoc", box).value; st.description = $("#fDesc", box).value;
    if ($("#fUrl", box)) st.url = $("#fUrl", box).value;
    if ($("#fTask", box)) st.task = $("#fTask", box).value;
  };
  const redraw = () => { read(); draw(); };
  const bind = () => {
    $("[data-x]", box).onclick = async () => { read(); if (JSON.stringify(st) !== snapshot && !(await confirmBox("Änderungen verwerfen?", "Verwerfen"))) return; closeTop(); };
    $("#fAll", box).onclick = () => { read(); st.all_day = !st.all_day; draw(); };
    box.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => {
      read();
      const k = b.dataset.pick, isDate = k === "sd" || k === "ed";
      const [s1, e1] = dt(), keep = e1 - s1;
      (isDate ? pickDate : pickTime)(st[k], (v) => {
        st[k] = v;
        if (k === "sd" || k === "stime") { // Dauer bleibt beim Verschieben des Starts erhalten
          const ns = st.all_day ? parseYmd(st.sd) : new Date(`${st.sd}T${st.stime}`), ne = new Date(ns.getTime() + Math.max(keep, st.all_day ? 864e5 : 15 * 6e4));
          if (st.all_day) st.ed = ymd(addDays(ne, -1)); else { st.ed = ymd(ne); st.etime = hm(ne); }
        }
        draw();
      });
    }));
    box.querySelectorAll("[data-rep]").forEach((b) => (b.onclick = async () => {
      read();
      if (b.dataset.rep === "custom") { const c = await customRepeat(st.custom || { freq: "weekly", interval: 1, days: [wdIdx()], end: "never" }, parseYmd(st.sd)); if (c) { st.custom = c; st.repeat = "custom"; } draw(); return; }
      st.repeat = b.dataset.rep; draw();
    }));
    $("#fCal", box).onclick = async () => {
      read();
      const v = await choose("Kalender", writable().map((c) => [c.id, c.name, "circle", cstyle(c.color)]), { selected: [st.calendar_id] });
      if (v) st.calendar_id = v; draw();
    };
    box.querySelectorAll("[data-al]").forEach((b) => (b.onclick = () => { read(); const m = +b.dataset.al; st.alarms = st.alarms.includes(m) ? st.alarms.filter((x) => x !== m) : [...st.alarms, m]; draw(); }));
    $("#alCustom", box).onclick = async () => {
      read();
      const v = await choose("Erinnerung", [["0", "Zum Beginn"], ["5", "5 Min. vorher"], ["15", "15 Min. vorher"], ["30", "30 Min. vorher"], ["120", "2 Std. vorher"], ["2880", "2 Tage vorher"], ["10080", "1 Woche vorher"]]);
      if (v != null && !st.alarms.includes(+v)) st.alarms.push(+v); draw();
    };
    $("#fMore", box).onclick = () => { read(); st.more = !st.more; draw(); };
    box.querySelectorAll("[data-col]").forEach((b) => (b.onclick = () => { read(); st.color = b.dataset.col; draw(); }));
    box.querySelectorAll("[data-status]").forEach((b) => (b.onclick = () => { read(); st.status = b.dataset.status; draw(); }));
    box.querySelectorAll("[data-travel]").forEach((b) => (b.onclick = () => { read(); st.travel = +b.dataset.travel; draw(); }));
    $("#fAgent", box)?.addEventListener("click", () => { read(); st.agent = !st.agent; draw(); });
    const t = $("#fTitle", box);
    t.oninput = () => { clearTimeout(suggTimer); suggTimer = setTimeout(loadSugg, 180); };
    t.onkeydown = (k) => { if (k.key === "Enter") $("[data-save]", box).click(); };
    $("[data-save]", box).onclick = save;
    showSugg();
  };
  const loadSugg = async () => {
    const q = $("#fTitle", box)?.value.trim() || "";
    if (editing || q.length < 2) { sugg = []; return showSugg(); }
    try { sugg = (await api(`/suggest?prefix=${encodeURIComponent(q)}`)).suggestions.filter((x) => x.title.toLowerCase() !== q.toLowerCase()); } catch { sugg = []; }
    showSugg();
  };
  const showSugg = () => {
    const el = $("#sugg", box); if (!el) return;
    el.innerHTML = sugg.map((x, i) => `<button class="c" data-s="${i}"><span class="ms">history</span>${esc(x.title)}<small>${x.all_day ? "ganztägig" : durText(x.duration_min)}${x.location ? ` · ${esc(x.location)}` : ""}</small></button>`).join("");
    el.querySelectorAll("[data-s]").forEach((b) => (b.onclick = () => {
      const x = sugg[+b.dataset.s]; read();
      Object.assign(st, { title: x.title, location: x.location || st.location, alarms: x.alarms?.length ? x.alarms : st.alarms, calendar_id: calOf(x.calendar_id) && !calOf(x.calendar_id).read_only ? x.calendar_id : st.calendar_id, all_day: x.all_day });
      if (x.color) st.color = x.color;
      if (!x.all_day) { const ns = new Date(`${st.sd}T${st.stime}`), ne = new Date(ns.getTime() + x.duration_min * 6e4); st.ed = ymd(ne); st.etime = hm(ne); }
      sugg = []; draw();
    }));
  };
  const checkConflicts = () => {
    clearTimeout(conflictTimer);
    conflictTimer = setTimeout(async () => {
      const w = $("#warn", box); if (!w) return;
      if (st.all_day || st.status === "free") { w.hidden = true; return; }
      try {
        const r = await api(`/conflicts?start=${st.sd}T${st.stime}&end=${st.ed}T${st.etime}${editing ? `&exclude=${encodeURIComponent(ev.id)}` : ""}`);
        const c = r.conflicts[0];
        w.hidden = !c;
        if (c) w.innerHTML = `<span class="ms">warning</span>Überschneidet sich mit „${esc(c.title)}“ (${hm(new Date(c.start))}–${hm(new Date(c.end))})${r.conflicts.length > 1 ? ` und ${r.conflicts.length - 1} weiteren` : ""}`;
      } catch { w.hidden = true; }
    }, 250);
  };
  const save = async () => {
    read();
    if (!st.title.trim()) { toast("Bitte einen Titel eingeben"); $("#fTitle", box).focus(); return; }
    const body = {
      title: st.title.trim(), calendar_id: st.calendar_id, all_day: st.all_day, location: st.location, description: st.description, url: st.url,
      alarms: st.alarms, color: st.color || null, transparency: st.status === "free" ? "free" : "busy", status: st.status === "tentative" ? "tentative" : "confirmed",
      travel_min: st.travel, alarm_action: st.agent ? "agent" : "notify", alarm_task: st.agent ? st.task : "",
      start: st.all_day ? st.sd : `${st.sd}T${st.stime}`, end: st.all_day ? ymd(addDays(parseYmd(st.ed), 1)) : `${st.ed}T${st.etime}`,
    };
    if (st.repeat === "custom") { if (st.custom) body.repeat = customBody(st.custom); }
    else if (st.repeat === "weekly") body.repeat = { freq: "weekly", days: [wdIdx()] };
    else body.repeat = st.repeat || "none";
    if (!editing && body.repeat === "none") delete body.repeat;
    if (!st.all_day && body.end <= body.start) { toast("Das Ende liegt vor dem Start"); return; }
    if (st.all_day && st.ed < st.sd) { toast("Das Ende liegt vor dem Start"); return; }
    let scope = "all";
    if (editing && ev.recurring) { scope = await askScope("Speichern"); if (!scope) return; }
    try {
      const r = editing ? await send(`/events/${encodeURIComponent(ev.id)}?scope=${scope}`, { method: "PATCH", body }, ev.id) : await send("/events", { method: "POST", body });
      closeTop();
      toast(r.queued ? "Offline gespeichert – wird später gesendet" : editing ? "Gespeichert" : "Termin angelegt", undoAction(r));
      hooks.changed(r.event?.event_id);
    } catch (x) { fail(x, save); }
  };
  draw();
  if (!editing) setTimeout(() => $("#fTitle", box)?.focus(), 120);
}

// ---------------- Benutzerdefinierte Wiederholung ----------------
const FREQ = [["daily", "Tag(e)"], ["weekly", "Woche(n)"], ["monthly", "Monat(e)"], ["yearly", "Jahr(e)"]];
const WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
function customText(c) {
  const unit = { daily: ["Täglich", "Tage"], weekly: ["Wöchentlich", "Wochen"], monthly: ["Monatlich", "Monate"], yearly: ["Jährlich", "Jahre"] }[c.freq];
  let t = c.interval > 1 ? `Alle ${c.interval} ${unit[1]}` : unit[0];
  if (c.freq === "weekly" && c.days?.length) t += ` am ${c.days.map((d) => WD[d]).join(", ")}`;
  if (c.freq === "monthly" && c.nth) t += ` am ${c.nth === -1 ? "letzten" : `${c.nth}.`} ${WD[c.days[0]]}`;
  if (c.end === "until") t += ` bis ${dayMonth(parseYmd(c.until))}`;
  if (c.end === "count") t += `, ${c.count}-mal`;
  return t;
}
function customBody(c) {
  const b = { freq: c.freq, interval: c.interval };
  if (c.freq === "weekly") b.days = c.days;
  if (c.freq === "monthly" && c.nth) { b.days = c.days; b.nth = c.nth; }
  if (c.end === "until") b.until = c.until;
  if (c.end === "count") b.count = c.count;
  return b;
}
function customRepeat(init, start) {
  return new Promise((res) => {
    const c = { ...init, days: [...(init.days || [])], until: init.until || ymd(addDays(start, 90)), count: init.count || 10 };
    let done = false;
    const { box, close } = overlay("", "bottom");
    const nth = Math.ceil(start.getDate() / 7), wdi = (start.getDay() + 6) % 7;
    const draw = () => {
      box.innerHTML = `<div class="handle"></div><h3 class="bs-t">Wiederholung</h3>
        <div class="frow"><span class="fl">Alle</span><input type="number" min="1" max="99" id="rInt" value="${c.interval}" class="num"><div class="chips">${FREQ.map(([v, l]) => `<button class="pc${c.freq === v ? " on" : ""}" data-f="${v}">${l}</button>`).join("")}</div></div>
        ${c.freq === "weekly" ? `<div class="frow"><div class="chips wdc">${WD.map((d, i) => `<button class="pc round${c.days.includes(i) ? " on" : ""}" data-d="${i}">${d}</button>`).join("")}</div></div>` : ""}
        ${c.freq === "monthly" ? `<div class="frow"><div class="chips"><button class="pc${c.nth ? "" : " on"}" data-n="0">am ${start.getDate()}.</button><button class="pc${c.nth ? " on" : ""}" data-n="1">am ${nth}. ${WD[wdi]}</button></div></div>` : ""}
        <div class="frow"><span class="fl">Endet</span><div class="chips"><button class="pc${c.end === "never" ? " on" : ""}" data-e="never">nie</button><button class="pc${c.end === "until" ? " on" : ""}" data-e="until">am ${c.end === "until" ? dayMonth(parseYmd(c.until)) : "…"}</button><button class="pc${c.end === "count" ? " on" : ""}" data-e="count">nach ${c.end === "count" ? `${c.count}-mal` : "…"}</button></div></div>
        ${c.end === "count" ? `<div class="frow"><span class="fl">Anzahl</span><input type="number" min="1" max="999" id="rCnt" class="num" value="${c.count}"></div>` : ""}
        <p class="hint">${esc(customText(c))}</p><button class="save wide" data-ok>Übernehmen</button>`;
      $("#rInt", box).onchange = (e) => { c.interval = Math.max(1, +e.target.value || 1); draw(); };
      $("#rCnt", box)?.addEventListener("change", (e) => { c.count = Math.max(1, +e.target.value || 1); draw(); });
      box.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => { c.freq = b.dataset.f; c.nth = 0; if (c.freq === "weekly" && !c.days.length) c.days = [wdi]; draw(); }));
      box.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => { const d = +b.dataset.d; c.days = c.days.includes(d) ? c.days.filter((x) => x !== d) : [...c.days, d].sort(); if (!c.days.length) c.days = [d]; draw(); }));
      box.querySelectorAll("[data-n]").forEach((b) => (b.onclick = () => { c.nth = +b.dataset.n ? nth : 0; c.days = c.nth ? [wdi] : c.days; draw(); }));
      box.querySelectorAll("[data-e]").forEach((b) => (b.onclick = () => {
        c.end = b.dataset.e;
        if (c.end === "until") pickDate(c.until, (v) => { c.until = v; draw(); }); else draw();
      }));
      $("[data-ok]", box).onclick = () => { done = true; close().then(() => res(c)); };
    };
    draw();
    const obs = new MutationObserver(() => { if (!box.isConnected) { obs.disconnect(); if (!done) res(null); } });
    obs.observe(document.body, { childList: true });
  });
}

// ---------------- Schnelleingabe mit Live-Vorschau ----------------
export function openQuick() {
  const { box, close } = overlay(`<div class="handle"></div><h3 class="bs-t">Schnelleingabe</h3>
    <input type="text" class="qin" id="qIn" placeholder="morgen 15 Uhr Zahnarzt @Praxis #Privat !30m" autocomplete="off" enterkeyhint="done">
    <div class="qprev" id="qPrev"><p class="hint">Datum, Uhrzeit, Dauer, „jeden Montag“, <b>@Ort</b>, <b>#Kalender</b> und <b>!Erinnerung</b> erkenne ich im Satz – ohne KI.</p></div>
    <div class="row-btns"><button class="tonal" id="qEdit"><span class="ms">edit</span>Bearbeiten</button><button class="save" id="qSave">Speichern</button></div>`, "bottom");
  const input = $("#qIn", box); let q = null, tm;
  const mark = (text, spans) => {
    let out = "", i = 0;
    const list = [...spans].sort((a, b) => a.start - b.start);
    list.forEach((sp, k) => {
      const st = Math.max(sp.start, i), en = Math.min(sp.end, list[k + 1]?.start ?? sp.end); // überlappende Treffer: der spätere gewinnt
      if (en <= st) return;
      out += esc(text.slice(i, st)) + `<mark class="k-${sp.kind}">${esc(text.slice(st, en).trimEnd())}</mark>${text.slice(st, en).endsWith(" ") ? " " : ""}`; i = en;
    });
    return out + esc(text.slice(i));
  };
  const preview = async () => {
    const text = input.value.trim();
    if (!text) { q = null; return; }
    try { q = await api("/quick", { method: "POST", body: { text } }); } catch { q = null; return; }
    const cal = S.cals.find((c) => c.name === q.calendar) || S.cals.find((c) => c.is_default);
    const ev = { title: q.title || "(ohne Titel)", start: q.start, end: q.end, all_day: q.all_day, color: cal?.color || "blau" };
    $("#qPrev", box).innerHTML = `<div class="qmark">${mark(input.value, q.spans || [])}</div>
      <div class="card" style="${cstyle(ev.color)}"><div class="bar4"></div><div class="ct"><div class="tt">${esc(ev.title)}</div><div class="sub">${esc(whenText({ ...ev, start: q.all_day ? q.start : q.start, end: q.all_day ? q.end : q.end }))}${q.location ? ` · ${esc(q.location)}` : ""}${cal ? ` · ${esc(cal.name)}` : ""}</div></div>
      <div class="icons">${q.rrule ? '<span class="ms">repeat</span>' : ""}${q.alarms?.length ? '<span class="ms">notifications</span>' : ""}</div></div>`;
  };
  const asEvent = () => { const cal = S.cals.find((c) => c.name === q.calendar); return { title: q.title, start: q.start, end: q.end, all_day: q.all_day, location: q.location, alarms: q.alarms, rrule: q.rrule, calendar_id: cal?.id, recurrence_text: "" }; };
  input.oninput = () => { clearTimeout(tm); tm = setTimeout(preview, 150); };
  const saveNow = async () => {
    await preview();
    if (!q?.title) { toast("Bitte einen Titel dazuschreiben"); return; }
    const cal = S.cals.find((c) => c.name === q.calendar);
    const body = { title: q.title, start: q.start, end: q.end, all_day: q.all_day, location: q.location, alarms: q.alarms, ...(q.rrule ? { rrule: q.rrule } : {}), ...(cal ? { calendar_id: cal.id } : {}) };
    try { const r = await send("/events", { method: "POST", body }); close(); toast(r.queued ? "Offline gespeichert" : `„${q.title}“ angelegt`, undoAction(r)); hooks.changed(r.event?.event_id); } catch (x) { fail(x); }
  };
  input.onkeydown = (k) => { if (k.key === "Enter") saveNow(); };
  $("#qSave", box).onclick = saveNow;
  $("#qEdit", box).onclick = async () => { await preview(); await close(); openEditor(q ? { event: asEvent(), copy: true } : {}); };
  setTimeout(() => input.focus(), 120);
}

// ---------------- Suche ----------------
const FLAGS = [["recurring", "Serien", "repeat"], ["alarm", "mit Erinnerung", "notifications"], ["by_agent", "von Ida", "auto_awesome"], ["all_day", "ganztägig", "today"], ["trash", "im Papierkorb", "delete"]];
const RANGES = [["", "Alle Zeiten"], ["upcoming", "Demnächst"], ["past", "Vergangen"], ["week", "Diese Woche"], ["month", "Dieser Monat"]];
export function openSearch() {
  const el = document.createElement("div"); el.className = "search";
  const f = { q: "", flags: new Set(), cals: [], range: "" };
  el.innerHTML = `<div class="sfield"><button class="icon-btn plain" data-b><span class="ms">arrow_back</span></button><input id="sIn" placeholder="Termine suchen" autocomplete="off" enterkeyhint="search"><button class="icon-btn plain" id="sClr" hidden><span class="ms">close</span></button></div>
    <div class="fchips" id="sChips"></div><div class="res" id="sRes"></div>`;
  document.body.append(el);
  requestAnimationFrame(() => el.classList.add("in"));
  let closed = false;
  const remove = () => { if (closed) return; closed = true; el.remove(); };
  pushLayer(remove);
  const input = $("#sIn", el), res = $("#sRes", el); let tm, sel = -1;
  $("[data-b]", el).onclick = closeTop;
  $("#sClr", el).onclick = () => { input.value = ""; f.q = ""; run(); input.focus(); };
  const chips = () => {
    const calLabel = f.cals.length ? f.cals.map((id) => calOf(id)?.name).join(", ") : "Alle Kalender";
    $("#sChips", el).innerHTML = `<button class="pc${f.cals.length ? " on" : ""}" data-c><span class="ms">calendar_month</span>${esc(calLabel)}<span class="ms">arrow_drop_down</span></button>
      <button class="pc${f.range ? " on" : ""}" data-r><span class="ms">date_range</span>${RANGES.find((r) => r[0] === f.range)[1]}<span class="ms">arrow_drop_down</span></button>
      ${FLAGS.map(([v, l, ic]) => `<button class="pc${f.flags.has(v) ? " on" : ""}" data-f="${v}"><span class="ms">${ic}</span>${l}</button>`).join("")}`;
    $("[data-c]", el).onclick = async () => { const v = await choose("Kalender", S.cals.map((c) => [c.id, c.name, "circle", cstyle(c.color)]), { multi: true, selected: f.cals }); if (v) { f.cals = v; chips(); run(); } };
    $("[data-r]", el).onclick = async () => { const v = await choose("Zeitraum", RANGES.map(([k, l]) => [k || "all", l]), { selected: [f.range || "all"] }); if (v) { f.range = v === "all" ? "" : v; chips(); run(); } };
    el.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => { f.flags.has(b.dataset.f) ? f.flags.delete(b.dataset.f) : f.flags.add(b.dataset.f); chips(); run(); }));
  };
  const words = () => f.q.toLowerCase().split(/\s+/).filter((w) => w.length > 1 && !w.startsWith("-")).map((w) => w.replace(/"/g, ""));
  const hl = (html) => {
    const ws = words().map((w) => esc(w).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    return ws.length ? html.replace(new RegExp(`(${ws.join("|")})`, "gi"), "<mark>$1</mark>") : html;
  };
  const rangeParams = () => {
    const now = new Date(), d = ymd(now);
    if (f.range === "upcoming") return `&from=${d}&to=${ymd(addDays(now, 365))}`;
    if (f.range === "past") return `&from=${ymd(addDays(now, -730))}&to=${d}`;
    if (f.range === "week") { const w = addDays(now, -((now.getDay() + 6) % 7)); return `&from=${ymd(w)}&to=${ymd(addDays(w, 7))}`; }
    if (f.range === "month") return `&from=${ymd(new Date(now.getFullYear(), now.getMonth(), 1))}&to=${ymd(new Date(now.getFullYear(), now.getMonth() + 1, 1))}`;
    return "";
  };
  const group = (list) => {
    let out = "", m = null;
    for (const e of list) {
      const d = e.all_day ? parseYmd(e.start) : new Date(e.start), k = `${d.getFullYear()}-${d.getMonth()}`;
      if (k !== m) { m = k; out += `<div class="sm">${monthName(d)} ${d.getFullYear()}</div>`; }
      out += card(e, { date: true, mark: hl, more: e.more });
    }
    return out;
  };
  const empty = async () => {
    const recent = JSON.parse(localStorage.getItem("kal-recent") || "[]");
    let next = [];
    try { next = (await api(`/events?from=${ymd(new Date())}&to=${ymd(addDays(new Date(), 30))}`)).events.filter((e) => !e.holiday && new Date(e.end) > new Date()).slice(0, 5); } catch { /* offline */ }
    const titles = [...new Set(S.events.filter((e) => !e.holiday).map((e) => e.title))].slice(0, 8);
    res.innerHTML = `${recent.length ? `<h4>Zuletzt gesucht <button class="link" data-clear>löschen</button></h4><div class="chips pad">${recent.map((r) => `<button class="pc" data-q="${esc(r)}"><span class="ms">history</span>${esc(r)}</button>`).join("")}</div>` : ""}
      ${titles.length ? `<h4>Häufig</h4><div class="chips pad">${titles.map((t) => `<button class="pc" data-q="${esc(t)}">${esc(t)}</button>`).join("")}</div>` : ""}
      <h4>Nächste Termine</h4>${next.map((e) => card(e, { date: true })).join("") || '<p class="hint pad">Nichts in den nächsten 30 Tagen.</p>'}`;
    res.querySelectorAll("[data-q]").forEach((b) => (b.onclick = () => { input.value = b.dataset.q; f.q = b.dataset.q; run(); }));
    $("[data-clear]", res)?.addEventListener("click", () => { localStorage.removeItem("kal-recent"); empty(); });
    bindHits();
  };
  const run = async () => {
    f.q = input.value.trim(); $("#sClr", el).hidden = !f.q; sel = -1;
    if (!f.q && !f.flags.size && !f.cals.length && !f.range) return empty();
    let r;
    try { r = await api(`/search?q=${encodeURIComponent(f.q)}${f.cals.length ? `&calendars=${f.cals.join(",")}` : ""}${f.flags.size ? `&flags=${[...f.flags].join(",")}` : ""}${rangeParams()}`); }
    catch (x) { res.innerHTML = `<p class="hint pad">${esc(x.status === 0 ? "Offline – die Suche braucht gerade eine Verbindung." : x.message)}</p>`; return; }
    const jump = r.range && !r.query.trim() ? `<button class="jump" data-jump="${r.range.from.slice(0, 10)}"><span class="ms">event</span>Springe zu ${esc(shortDay(new Date(r.range.from)))}</button>` : "";
    const rangeChip = r.range && r.range.label !== "Zeitraum" ? `<div class="chips pad"><span class="pc on"><span class="ms">date_range</span>${esc(r.range.label)}</span></div>` : "";
    res.innerHTML = jump + rangeChip + (r.total
      ? (r.upcoming.length ? `<h4>${r.range ? "Treffer" : "Demnächst"}</h4>${group(r.upcoming)}` : "") + (r.past.length ? `<h4>Vergangen</h4>${group(r.past)}` : "")
      : `<div class="empty-state"><div class="face big"></div><p>Nichts gefunden</p></div>`);
    $("[data-jump]", res)?.addEventListener("click", async (b) => { const d = b.currentTarget.dataset.jump; await closeTop(); hooks.jump({ start: d, all_day: true }); });
    bindHits();
  };
  const bindHits = () => res.querySelectorAll(".card[data-id]").forEach((n) => (n.onclick = () => {
    if (f.q) localStorage.setItem("kal-recent", JSON.stringify([f.q, ...JSON.parse(localStorage.getItem("kal-recent") || "[]").filter((x) => x !== f.q)].slice(0, 8)));
    openDetail(n.dataset.id);
  }));
  input.oninput = () => { clearTimeout(tm); tm = setTimeout(run, 150); };
  input.onkeydown = (k) => {
    const hits = [...res.querySelectorAll(".card[data-id]")];
    if (k.key === "ArrowDown" || k.key === "ArrowUp") { k.preventDefault(); sel = Math.max(0, Math.min(hits.length - 1, sel + (k.key === "ArrowDown" ? 1 : -1))); hits.forEach((h, i) => h.classList.toggle("kb", i === sel)); hits[sel]?.scrollIntoView({ block: "nearest" }); }
    else if (k.key === "Enter" && hits[sel]) hits[sel].click();
    else if (k.key === "Escape") closeTop();
  };
  chips(); empty();
  setTimeout(() => input.focus(), 60);
}

// ---------------- Papierkorb ----------------
export function openTrash() {
  const { box } = overlay("");
  const draw = async () => {
    let t; try { t = await api("/trash"); } catch (x) { return fail(x); }
    box.innerHTML = `<div class="ed-top"><button class="icon-btn plain" data-close><span class="ms">arrow_back</span></button><h2>Papierkorb</h2>${t.events.length ? '<button class="pc danger" id="tEmpty">Leeren</button>' : ""}</div>
      <p class="hint">Gelöschte Termine bleiben hier ${S.settings.trash_days || 30} Tage, dann sind sie endgültig weg.</p>
      ${t.events.length ? t.events.map((e) => `<div class="card" style="${cstyle(e.color)}"><div class="bar4"></div><div class="ct"><div class="tt">${esc(e.title)}</div><div class="sub">${esc(whenText(e))} · gelöscht ${esc(ago(e.deleted_at))}</div></div><button class="pc" data-r="${e.event_id}">Wiederherstellen</button></div>`).join("") : '<div class="empty-state"><div class="face big"></div><p>Der Papierkorb ist leer</p></div>'}`;
    box.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeTop));
    box.querySelectorAll("[data-r]").forEach((b) => (b.onclick = async () => { try { await api(`/events/${b.dataset.r}/restore`, { method: "POST" }); toast("Wiederhergestellt"); hooks.changed(); draw(); } catch (x) { fail(x); } }));
    $("#tEmpty", box)?.addEventListener("click", async () => { if (await confirmBox("Papierkorb endgültig leeren?", "Endgültig löschen")) { await api("/trash", { method: "DELETE" }); draw(); hooks.changed(); } });
  };
  draw();
}

// ---------------- Verlauf („Was hat Ida geändert?“) ----------------
export function openHistory() {
  const { box } = overlay("");
  let who = "";
  const draw = async () => {
    let r; try { r = await api(`/changes?limit=80${who ? `&by=${who}` : ""}`); } catch (x) { return fail(x); }
    let day = null;
    box.innerHTML = `<div class="ed-top"><button class="icon-btn plain" data-close><span class="ms">arrow_back</span></button><h2>Verlauf</h2></div>
      <div class="chips pad"><button class="pc${who ? "" : " on"}" data-w="">Alle</button><button class="pc${who === "agents" ? " on" : ""}" data-w="agents"><span class="ms">auto_awesome</span>Nur Ida</button></div>
      ${r.changes.filter((c) => who !== "agents" || c.by !== "user").map((c) => {
        const d = longDay(new Date(c.at)), head = d !== day ? `<div class="sm">${(day = d)}</div>` : "";
        return `${head}<div class="hrow"><span class="ms ${c.by === "user" ? "" : "ida"}">${c.by === "user" ? "person" : "auto_awesome"}</span><div class="ct"><div class="tt">${esc(c.title)}</div><div class="sub">${c.by === "user" ? "Du" : "Ida"} · ${ACT[c.action] || esc(c.action)} · ${hm(new Date(c.at))}${c.undone ? " · zurückgenommen" : ""}</div></div>${c.undone || c.action === "undo" ? "" : `<button class="pc" data-u="${c.id}">Rückgängig</button>`}</div>`;
      }).join("") || '<div class="empty-state"><div class="face big"></div><p>Noch nichts passiert</p></div>'}`;
    box.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeTop));
    box.querySelectorAll("[data-w]").forEach((b) => (b.onclick = () => { who = b.dataset.w; draw(); }));
    box.querySelectorAll("[data-u]").forEach((b) => (b.onclick = async () => { try { await api(`/changes/${b.dataset.u}/undo`, { method: "POST" }); toast("Zurückgenommen"); hooks.changed(); draw(); } catch (x) { fail(x); } }));
  };
  draw();
}

// ---------------- Kalender-Einstellungen (langes Drücken im Drawer) ----------------
export function openCalendar(cal) {
  const isNew = !cal;
  const c = cal ? { ...cal } : { name: "", color: "blau", agent_access: "write", default_alarms: [] };
  const { box } = overlay("");
  const draw = () => {
    const ro = c.read_only;
    box.innerHTML = `<div class="ed-top"><button class="icon-btn plain" data-close><span class="ms">close</span></button><h2>${isNew ? "Neuer Kalender" : "Kalender"}</h2>${ro ? "" : '<button class="save" data-save>Speichern</button>'}</div>
      ${ro ? `<h3 class="bs-t">${esc(c.name)}</h3><p class="hint">Feiertage kommen automatisch für dein Bundesland; ein- und ausschalten und das Bundesland wählen kannst du im Menü.</p>` : `<input type="text" class="ttl" id="cName" placeholder="Name" value="${esc(c.name)}" autocomplete="off">`}
      <div class="group"><div class="gl">Farbe</div><div class="chips">${COLORS.map((x) => `<button class="swatch${c.color === x ? " on" : ""}" data-col="${x}" style="${cstyle(x)}"></button>`).join("")}</div></div>
      ${ro ? "" : `<div class="group"><div class="gl">Was darf Ida hier?</div><div class="chips">${[["write", "Lesen und schreiben"], ["read", "Nur lesen"], ["none", "Nichts"]].map(([v, l]) => `<button class="pc${c.agent_access === v ? " on" : ""}" data-acc="${v}">${l}</button>`).join("")}</div></div>
      <div class="group"><div class="gl">Standard-Erinnerung für neue Termine</div><div class="chips">${[[10, "10 Min."], [30, "30 Min."], [60, "1 Std."], [1440, "1 Tag"]].map(([m, l]) => `<button class="pc${c.default_alarms.includes(m) ? " on" : ""}" data-al="${m}">${l}</button>`).join("")}</div></div>`}
      ${isNew ? "" : `<div class="group list">
        ${c.is_default || ro ? "" : '<button class="li" id="cDef"><span class="ms">star</span><span>Als Standardkalender</span></button>'}
        <a class="li" href="${apiHref(`/export.ics?calendar=${c.id}`)}" download="${esc(c.name)}.ics"><span class="ms">download</span><span>Als .ics exportieren</span></a>
        <button class="li" id="cOnly"><span class="ms">filter_alt</span><span>Nur diesen anzeigen</span></button>
        ${ro || c.is_default ? "" : '<button class="li danger" id="cDel"><span class="ms">delete</span><span>Kalender löschen</span></button>'}</div>`}`;
    box.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeTop));
    box.querySelectorAll("[data-col]").forEach((b) => (b.onclick = () => { readName(); c.color = b.dataset.col; draw(); if (ro) patchCal({ color: c.color }); }));
    box.querySelectorAll("[data-acc]").forEach((b) => (b.onclick = () => { readName(); c.agent_access = b.dataset.acc; draw(); }));
    box.querySelectorAll("[data-al]").forEach((b) => (b.onclick = () => { readName(); const m = +b.dataset.al; c.default_alarms = c.default_alarms.includes(m) ? c.default_alarms.filter((x) => x !== m) : [...c.default_alarms, m]; draw(); }));
    $("#cDef", box)?.addEventListener("click", async () => { await patchCal({ is_default: true }); toast("Ist jetzt der Standardkalender"); closeTop(); });
    $("#cOnly", box)?.addEventListener("click", async () => { await closeTop(); hooks.only(c.id); });
    $("#cDel", box)?.addEventListener("click", async () => {
      if (!(await confirmBox(`„${c.name}“ mit allen Terminen löschen?`, "Löschen"))) return;
      try { await api(`/calendars/${c.id}`, { method: "DELETE" }); closeTop(); hooks.changed(true); } catch (x) { fail(x); }
    });
    $("[data-save]", box)?.addEventListener("click", async () => {
      readName();
      if (!c.name.trim()) return toast("Bitte einen Namen eingeben");
      const body = { name: c.name.trim(), color: c.color, agent_access: c.agent_access, default_alarms: c.default_alarms };
      try { await (isNew ? api("/calendars", { method: "POST", body }) : api(`/calendars/${c.id}`, { method: "PATCH", body })); closeTop(); hooks.changed(true); } catch (x) { fail(x); }
    });
  };
  const readName = () => { const n = $("#cName", box); if (n) c.name = n.value; };
  const patchCal = async (body) => { try { await api(`/calendars/${c.id}`, { method: "PATCH", body }); hooks.changed(true); } catch (x) { fail(x); } };
  draw();
  if (isNew) setTimeout(() => $("#cName", box)?.focus(), 120);
}
let apiBase = "/api/v1";
export const setApiHref = (b) => { apiBase = b; };
const apiHref = (p) => apiBase + p;

// ---------------- Einstellungen (gruppierte Listen im Android-17-Stil) ----------------
const VIEWS = [["monthday", "Monat + Tag"], ["agenda", "Liste"], ["day", "Tag"], ["days3", "3 Tage"], ["week", "Woche"], ["month", "Monat"], ["year", "Jahr"]];
const STATES = [["BW", "Baden-Württemberg"], ["BY", "Bayern"], ["BE", "Berlin"], ["BB", "Brandenburg"], ["HB", "Bremen"], ["HH", "Hamburg"], ["HE", "Hessen"], ["MV", "Mecklenburg-Vorpommern"], ["NI", "Niedersachsen"], ["NW", "Nordrhein-Westfalen"], ["RP", "Rheinland-Pfalz"], ["SL", "Saarland"], ["SN", "Sachsen"], ["ST", "Sachsen-Anhalt"], ["SH", "Schleswig-Holstein"], ["TH", "Thüringen"]];
export async function openSettings(section) {
  const { box } = overlay("");
  const put = async (patch) => { try { S.settings = await api("/settings", { method: "PUT", body: patch }); hooks.changed(); draw(); } catch (x) { fail(x); } };
  const draw = async () => {
    const s = S.settings;
    let feed = null, backups = { backups: [] };
    try { [feed, backups] = await Promise.all([api("/feed"), api("/backups")]); } catch { /* offline */ }
    const sw = (k, label, sub = "") => `<div class="li"><div class="ct"><span>${label}</span>${sub ? `<small>${sub}</small>` : ""}</div><button class="sw${s[k] ? " on" : ""}" data-k="${k}"></button></div>`;
    const sel = (k, label, opts) => `<button class="li" data-sel="${k}"><div class="ct"><span>${label}</span><small>${esc((opts.find(([v]) => String(v) === String(s[k])) || ["", "–"])[1])}</small></div><span class="ms">chevron_right</span></button>`;
    const txt = (k, label, ph) => `<div class="li"><div class="ct"><span>${label}</span></div><input type="text" class="li-in" data-t="${k}" value="${esc(s[k] ?? "")}" placeholder="${ph}"></div>`;
    const total = S.cals.reduce((n, c) => n + (c.events || 0), 0);
    const perm = "Notification" in window ? Notification.permission : "unsupported";
    box.innerHTML = `<div class="ed-top"><button class="icon-btn plain" data-close><span class="ms">arrow_back</span></button><h2>Einstellungen</h2></div>
      <div class="gl">Allgemein</div><div class="group list">
        ${sel("week_start", "Wochenstart", [[1, "Montag"], [0, "Sonntag"]])}
        ${sel("default_view", "Standardansicht am Handy", VIEWS)}
        ${sel("default_view_desktop", "Standardansicht am PC", VIEWS)}
        <button class="li" data-favs><div class="ct"><span>Lieblingsansichten in der Leiste</span><small>${(s.toolbar_views || []).map((v) => VIEWS.find((x) => x[0] === v)?.[1]).join(" · ")}</small></div><span class="ms">chevron_right</span></button>
        ${sw("show_week_numbers", "Kalenderwochen anzeigen")}
        ${sw("hide_weekends", "Wochenende in der Woche ausblenden")}
        ${txt("work_hours", "Arbeitszeit", "08:00-18:00")}
        ${txt("second_timezone", "Zweite Zeitzone", "z. B. America/New_York")}</div>
      <div class="gl">Termine</div><div class="group list">
        ${sel("default_duration_min", "Standarddauer", [[15, "15 Min."], [30, "30 Min."], [45, "45 Min."], [60, "1 Std."], [90, "1,5 Std."], [120, "2 Std."]])}
        <button class="li" data-defcal><div class="ct"><span>Standardkalender</span><small>${esc(S.cals.find((c) => c.is_default)?.name || "–")}</small></div><span class="ms">chevron_right</span></button>
        ${sw("show_holidays", "Feiertage anzeigen")}
        ${sel("state", "Bundesland für Feiertage", [["", `Standard (${S.meta.state || "NW"})`], ...STATES])}</div>
      <div class="gl">Benachrichtigungen</div><div class="group list">
        <div class="li"><div class="ct"><span>In der App</span><small>${perm === "granted" ? "erlaubt" : perm === "denied" ? "im Browser blockiert" : perm === "unsupported" ? "nicht möglich" : "noch nicht erlaubt"}</small></div>${perm === "default" ? '<button class="pc" id="nPerm">Erlauben</button>' : ""}</div>
        ${txt("quiet_hours", "Ruhezeiten", "22:00-07:00")}
        <div class="li"><div class="ct"><span>Erinnerungen bei geschlossener App</span><small>${S.meta.ida ? "kommen über IDA als Info" : "über das Abo in einer Kalender-App (ICSx⁵/DAVx⁵) – dann erinnert das Handy selbst"}</small></div></div></div>
      <div class="gl">Ida</div><div class="group list">
        ${S.cals.filter((c) => !c.read_only).map((c) => `<button class="li" data-acc="${c.id}"><div class="ct"><span><span class="dot" style="${cstyle(c.color)}"></span>${esc(c.name)}</span><small>${{ write: "Ida darf lesen und schreiben", read: "Ida darf nur lesen", none: "Ida sieht nichts" }[c.agent_access]}</small></div><span class="ms">chevron_right</span></button>`).join("")}
        ${sw("agent_context", "Termine der nächsten 24 Std. im Gedächtnis", "Ida kennt deinen Tag, ohne nachzuschauen")}
        ${sw("highlight_agent", "Änderungen von Ida hervorheben")}</div>
      <div class="gl" data-sec="data">Daten</div><div class="group list">
        <a class="li" href="${apiHref("/export.ics")}" download="kalender.ics"><span class="ms">download</span><div class="ct"><span>Alles als .ics exportieren</span></div></a>
        <label class="li"><span class="ms">upload</span><div class="ct"><span>.ics importieren</span><small>Doppelte Termine werden erkannt</small></div><input type="file" id="sImp" accept=".ics,text/calendar" hidden></label>
        ${feed ? `<div class="li col"><div class="ct"><span>Abo-Link</span><small>nur lesen – z. B. für ICSx⁵ am Handy</small></div><div class="feed"><input type="text" readonly value="${esc(feed.url)}"><button class="icon-btn plain" id="fCopy" title="Kopieren"><span class="ms">content_copy</span></button><button class="icon-btn plain" id="fRot" title="Neuen Link erzeugen"><span class="ms">autorenew</span></button></div></div>` : ""}
        <button class="li" id="sBak"><span class="ms">backup</span><div class="ct"><span>Backup jetzt anlegen</span><small>${backups.backups.length} Stände, nächtlich automatisch (${backups.keep || 14} werden behalten)</small></div></button>
        ${backups.backups.slice(0, 5).map((b) => `<a class="li sub" href="${apiHref(`/backups/${encodeURIComponent(b.name || b)}`)}" download><span class="ms">description</span><div class="ct"><span>${esc(b.name || b)}</span>${b.size ? `<small>${Math.round(b.size / 1024)} KB</small>` : ""}</div></a>`).join("")}
        <button class="li" data-trash><span class="ms">delete</span><div class="ct"><span>Papierkorb</span></div></button></div>
      <div class="gl">Über</div><div class="group list">
        <div class="li"><div class="ct"><span>IDA Kalender</span><small>Version ${esc(S.meta.version || "?")} · ${total} Termine in ${S.cals.length} Kalendern · Zeitzone ${esc(S.meta.timezone || "")}</small></div></div></div>`;
    box.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeTop));
    box.querySelectorAll("[data-k]").forEach((b) => (b.onclick = () => put({ [b.dataset.k]: !b.classList.contains("on") })));
    box.querySelectorAll("[data-t]").forEach((i) => (i.onchange = () => put({ [i.dataset.t]: i.value.trim() })));
    box.querySelectorAll("[data-sel]").forEach((b) => (b.onclick = async () => {
      const k = b.dataset.sel;
      const opts = { week_start: [[1, "Montag"], [0, "Sonntag"]], default_view: VIEWS, default_view_desktop: VIEWS, default_duration_min: [[15, "15 Min."], [30, "30 Min."], [45, "45 Min."], [60, "1 Std."], [90, "1,5 Std."], [120, "2 Std."]], state: [["", "Standard"], ...STATES] }[k];
      const v = await choose(b.querySelector("span").textContent, opts.map(([x, l]) => [String(x), l]), { selected: [String(s[k])] });
      if (v != null) put({ [k]: ["week_start", "default_duration_min"].includes(k) ? +v : v });
    }));
    $("[data-favs]", box).onclick = async () => {
      const v = await choose("Drei Lieblingsansichten", VIEWS, { multi: true, selected: s.toolbar_views || [] });
      if (v?.length) put({ toolbar_views: v.slice(0, 3) });
    };
    $("[data-defcal]", box).onclick = async () => {
      const v = await choose("Standardkalender", writable().map((c) => [c.id, c.name, "circle", cstyle(c.color)]), { selected: [S.cals.find((c) => c.is_default)?.id] });
      if (v) { await api(`/calendars/${v}`, { method: "PATCH", body: { is_default: true } }); hooks.changed(true); draw(); }
    };
    box.querySelectorAll("[data-acc]").forEach((b) => (b.onclick = async () => {
      const c = calOf(b.dataset.acc);
      const v = await choose(`Ida in „${c.name}“`, [["write", "Lesen und schreiben"], ["read", "Nur lesen"], ["none", "Nichts"]], { selected: [c.agent_access] });
      if (v) { await api(`/calendars/${c.id}`, { method: "PATCH", body: { agent_access: v } }); hooks.changed(true); setTimeout(draw, 200); }
    }));
    $("#nPerm", box)?.addEventListener("click", async () => { await Notification.requestPermission(); draw(); });
    $("#sImp", box).onchange = async (x) => {
      const file = x.target.files[0]; if (!file) return;
      const fd = new FormData(); fd.append("file", file);
      try { const r = await api("/import", { method: "POST", form: fd }); toast(`${r.imported ?? r.created ?? 0} Termine importiert${r.skipped ? `, ${r.skipped} schon da` : ""}`); hooks.changed(true); } catch (e) { fail(e); }
    };
    $("#fCopy", box)?.addEventListener("click", () => { navigator.clipboard?.writeText(feed.url); toast("Abo-Link kopiert"); });
    $("#fRot", box)?.addEventListener("click", async () => { if (await confirmBox("Neuen Abo-Link erzeugen? Der alte hört sofort auf zu funktionieren.", "Neuen Link erzeugen")) { await api("/feed/rotate", { method: "POST" }); draw(); } });
    $("#sBak", box).onclick = async () => { try { await api("/backups", { method: "POST" }); toast("Backup angelegt"); draw(); } catch (x) { fail(x); } };
    $("[data-trash]", box).onclick = () => openTrash();
    if (section) { box.querySelector(`[data-sec="${section}"]`)?.scrollIntoView(); section = null; }
  };
  draw();
}

// ---------------- Erinnerung fällig (App offen) ----------------
export function showAlarm(a) {
  const ev = { ...a, id: a.occurrence_id };
  const { box, close } = overlay(`<div class="handle"></div><div class="alarm" style="${cstyle(a.color)}"><span class="ms">notifications_active</span>
      <h2>${esc(a.title)}</h2><p>${esc(whenText(ev))}${a.location ? ` · ${esc(a.location)}` : ""}</p></div>
    <div class="chips center"><span class="hint">Schlummern:</span>${[5, 10, 30].map((m) => `<button class="pc" data-snooze="${m}">${m} Min.</button>`).join("")}</div>
    <div class="row-btns"><button class="tonal" data-open><span class="ms">open_in_new</span>Öffnen</button><button class="save" data-ok>OK</button></div>`, "bottom");
  $("[data-ok]", box).onclick = close;
  $("[data-open]", box).onclick = () => close().then(() => openDetail(a.occurrence_id));
  box.querySelectorAll("[data-snooze]").forEach((b) => (b.onclick = () => { closeTop(); setTimeout(() => showAlarm(a), +b.dataset.snooze * 6e4); toast(`Erinnere dich in ${b.dataset.snooze} Min. wieder`); }));
}

export const isDetailOpen = () => !!detailOpen;
export { alarmText };
