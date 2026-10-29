// Ansichten: Monat + Tag, Monat, Zeitraster (Tag/3 Tage/Woche), Liste, Jahr, Mini-Monat, Skelett.
import { S, queuedIds, visible } from "./store.js";
import {
  addDays, daysBetween, dayMonth, hm, isWeekend, isoWeek, longDay, monthGrid, monthName, monthShort, parseYmd, relDay, sameDay, shortDay,
  startOfDay, weekStart, weekdayShort, ymd,
} from "./dates.js";
import { columns } from "./layout.js";

export const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const cstyle = (c) => `--c:var(--ev-${c});--cc:var(--evc-${c})`;
const st = (e) => (e.status === "cancelled" ? " cancelled" : "") + (e.status === "tentative" ? " tentative" : "") + (new Date(e.end) < new Date() ? " past" : "") + (S.flash.has(e.event_id) ? " flash" : "");
export const byIda = (e) => e.created_by && !["user", "system"].includes(e.created_by);
const holidayOf = (l) => l.find((e) => e.holiday);

/** Start/Ende eines Vorkommens als lokale Tage (Ende exklusiv). */
function span(e) {
  if (e.all_day) return [parseYmd(e.start), parseYmd(e.end)];
  const s = new Date(e.start), en = new Date(e.end);
  return [startOfDay(s), hm(en) === "00:00" && en > s ? startOfDay(en) : addDays(startOfDay(en), 1)];
}

/** Termine nach Tagen (ymd) gruppieren; mehrtägige erscheinen an jedem Tag. */
export function byDay(list, a, b) {
  const m = new Map();
  for (const e of list) {
    const [s, en] = span(e);
    for (let d = s < a ? a : s; d < en && d < b; d = addDays(d, 1)) { const k = ymd(d); (m.get(k) || m.set(k, []).get(k)).push(e); }
  }
  for (const l of m.values()) l.sort((x, y) => (y.all_day - x.all_day) || x.start.localeCompare(y.start));
  return m;
}

/** Karte wie im Entwurf: Farbbalken, Titel, Zeit · Ort, rechts Icons (Serie, Erinnerung, Ida). */
export function card(e, { date = false, mark = (h) => h, more = 0 } = {}) {
  const s = new Date(e.start), en = new Date(e.end);
  const time = e.all_day ? "ganztägig" : `${hm(s)} – ${hm(en)}`;
  const sub = [date ? (e.all_day ? shortDay(parseYmd(e.start)) : shortDay(s)) : "", time, e.location].filter(Boolean).join(" · ");
  const q = queuedIds().has(e.id) ? '<span class="ms" title="Wird gesendet, sobald du online bist">cloud_upload</span>' : "";
  return `<div class="card${st(e)}" data-id="${esc(e.id)}" style="${cstyle(e.color)}"><div class="bar4"></div>
    <div class="ct"><div class="tt">${mark(esc(e.title))}${e.age ? ` <small>(${e.age})</small>` : ""}</div><div class="sub">${mark(esc(sub))}${more ? ` · + ${more} weitere` : ""}</div></div>
    <div class="icons">${q}${e.recurring ? '<span class="ms">repeat</span>' : ""}${e.alarms?.length ? '<span class="ms">notifications</span>' : ""}${byIda(e) ? '<span class="chip-ida"><span class="ms">auto_awesome</span>Ida</span>' : ""}</div></div>`;
}

export const emptyBox = (t = "Nichts geplant", btn = true) => `<div class="empty-state"><div class="face big"></div><p>${t}</p>${btn ? '<button class="tonal" data-new><span class="ms">add</span>Termin anlegen</button>' : ""}</div>`;

// ---------------- Monat (Raster mit Balken für mehrtägige Termine) ----------------
function monthRows(days, map, { pills, wk }) {
  const today = new Date(), out = [];
  for (let r = 0; r < days.length / 7; r++) {
    const week = days.slice(r * 7, r * 7 + 7), a = week[0], b = addDays(week[6], 1);
    // Balken: ganztägige/mehrtägige Termine dieser Woche auf Spuren verteilen
    const multi = [], seen = new Set();
    for (const d of week) for (const e of map.get(ymd(d)) || []) {
      const [s, en] = span(e);
      if ((e.all_day || daysBetween(s, en) > 1) && !seen.has(e.id)) { seen.add(e.id); multi.push({ e, c0: Math.max(0, daysBetween(a, s)), c1: Math.min(7, daysBetween(a, en < b ? en : b)) }); }
    }
    multi.sort((x, y) => x.c0 - y.c0 || (y.c1 - y.c0) - (x.c1 - x.c0));
    const lanes = [];
    for (const m of multi) { let l = lanes.findIndex((end) => end <= m.c0); if (l < 0) { l = lanes.length; lanes.push(0); } lanes[l] = m.c1; m.lane = l; }
    const maxLanes = pills ? 2 : 0, shownLanes = Math.min(lanes.length, maxLanes);
    let h = `<div class="m-row${pills ? " pills" : ""}" style="--lanes:${shownLanes}">${wk ? `<div class="kw">${isoWeek(a)}</div>` : ""}<div class="m-days">`;
    for (const d of week) {
      const l = map.get(ymd(d)) || [], hol = holidayOf(l);
      const cls = [d.getMonth() !== S.date.getMonth() ? "out" : "", sameDay(d, today) ? "today" : "", sameDay(d, S.sel) ? "sel" : "", isWeekend(d) || hol ? "we" : ""].join(" ");
      const singles = l.filter((e) => !multi.some((m) => m.e.id === e.id && m.lane < maxLanes));
      const hiddenMulti = l.filter((e) => multi.some((m) => m.e.id === e.id && m.lane >= maxLanes)).length;
      const room = pills ? 3 : 0;
      h += `<div class="m-cell ${cls}" data-day="${ymd(d)}"><div class="n">${d.getDate()}</div>${hol && !pills ? `<div class="hol">${esc(hol.title)}</div>` : ""}`;
      if (pills) {
        h += `<div class="m-pills">${singles.slice(0, room).map((e) => `<span class="pill${e.holiday ? " hol" : ""}${st(e)}" data-id="${esc(e.id)}" style="${cstyle(e.color)}">${e.all_day ? "" : `<b>${hm(new Date(e.start))}</b> `}${esc(e.title)}</span>`).join("")}
          ${singles.length + hiddenMulti > room ? `<span class="more">+${singles.length + hiddenMulti - room}</span>` : ""}</div>`;
      } else {
        h += `<div class="dots">${l.filter((e) => !e.holiday).slice(0, 3).map((e) => `<i style="${cstyle(e.color)}"></i>`).join("")}</div>`;
      }
      h += "</div>";
    }
    h += "</div>";
    if (pills) h += multi.filter((m) => m.lane < maxLanes).map((m) => `<span class="mbar${m.e.holiday ? " hol" : ""}${st(m.e)}" data-id="${esc(m.e.id)}" style="${cstyle(m.e.color)};--c0:${m.c0};--c1:${m.c1};--lane:${m.lane}">${esc(m.e.title)}</span>`).join("");
    out.push(h + "</div>");
  }
  return out.join("");
}

function wdRow(days, wk) { return `<div class="wd-row${wk ? " wk" : ""}">${wk ? "<span></span>" : ""}${days.slice(0, 7).map((d) => `<span>${weekdayShort(d)}</span>`).join("")}</div>`; }

/** Monat + Tag (Standard am Handy): Raster mit Punkten, Griff, Tagesliste. */
export function monthday() {
  const g = monthGrid(S.date), map = byDay(visible(), g[0], addDays(g[41], 1)), wk = S.settings.show_week_numbers !== false;
  let days = g;
  if (S.mdMode === "week") { const i = Math.max(0, g.findIndex((d) => sameDay(d, S.sel))); days = g.slice(i - (i % 7), i - (i % 7) + 7); }
  const dl = (map.get(ymd(S.sel)) || []);
  const hol = holidayOf(dl);
  return `<div class="md md-${S.mdMode}">${wdRow(g, wk)}<div class="m-grid">${monthRows(days, map, { pills: S.mdMode === "full", wk })}</div>
    <div class="grip" id="grip" title="Ziehen: Raster größer oder kleiner"><i></i></div>
    <div class="dayh">${longDay(S.sel)}${hol ? ` <span class="hol-chip">${esc(hol.title)}</span>` : ""}</div>
    ${dl.filter((e) => !e.holiday).length ? dl.filter((e) => !e.holiday).map((e) => card(e)).join("") : emptyBox()}</div>`;
}

/** Monat mit Text-Pillen (PC/Tablet) */
export function month() {
  const g = monthGrid(S.date), map = byDay(visible(), g[0], addDays(g[41], 1)), wk = S.settings.show_week_numbers !== false;
  return `<div class="month">${wdRow(g, wk)}<div class="m-grid full">${monthRows(g, map, { pills: true, wk })}</div></div>`;
}

// ---------------- Zeitraster ----------------
export function gridDays() {
  if (S.view === "week") {
    const w = weekStart(S.date), all = Array.from({ length: 7 }, (_, i) => addDays(w, i));
    return S.settings.hide_weekends ? all.filter((d) => !isWeekend(d)) : all;
  }
  return Array.from({ length: S.view === "days3" ? 3 : 1 }, (_, i) => addDays(S.date, i));
}

function workBand() {
  const m = /^(\d\d):(\d\d)\s*-\s*(\d\d):(\d\d)$/.exec(S.settings.work_hours || "");
  if (!m) return "";
  const a = (+m[1] * 60 + +m[2]) / 60 * S.hh, b = (+m[3] * 60 + +m[4]) / 60 * S.hh;
  return `<div class="work" style="top:${a}px;height:${b - a}px"></div>`;
}

export function timegrid() {
  const HH = S.hh, days = gridDays(), n = days.length, today = new Date();
  const map = byDay(visible(), days[0], addDays(days[n - 1], 1));
  const cols = `grid-template-columns: repeat(${n}, minmax(0, 1fr))`;
  let h = `<div class="tg-head" style="${cols}">${days.map((d) => `<div class="${sameDay(d, today) ? "today" : ""}${isWeekend(d) ? " we" : ""}" data-day="${ymd(d)}" data-goday>${weekdayShort(d)}<b>${d.getDate()}</b></div>`).join("")}</div>`;
  const ad = days.map((d) => (map.get(ymd(d)) || []).filter((e) => e.all_day));
  const maxAd = Math.max(0, ...ad.map((l) => l.length)), collapsed = maxAd > 2 && !S.adOpen;
  if (maxAd) {
    h += `<div class="tg-allday" style="${cols}">${ad.map((l) => `<div>${(collapsed ? l.slice(0, 2) : l).map((e) => `<span class="pill${e.holiday ? " hol" : ""}${st(e)}" data-id="${esc(e.id)}" style="${cstyle(e.color)}">${esc(e.title)}</span>`).join("")}${collapsed && l.length > 2 ? `<span class="more" data-adtoggle>+${l.length - 2}</span>` : ""}</div>`).join("")}
      ${maxAd > 2 ? `<button class="ad-toggle" data-adtoggle><span class="ms">${collapsed ? "expand_more" : "expand_less"}</span></button>` : ""}</div>`;
  }
  h += `<div class="tg-body" style="${cols};height:${24 * HH}px;--hh:${HH}px"><div class="tg-hours">${Array.from({ length: 23 }, (_, i) => `<span style="top:${(i + 1) * HH}px">${String(i + 1).padStart(2, "0")}:00</span>`).join("")}</div>`;
  for (const d of days) {
    const day0 = startOfDay(d).getTime();
    const placed = columns((map.get(ymd(d)) || []).filter((e) => !e.all_day).map((ev) => ({
      ev, s: Math.max(0, (new Date(ev.start) - day0) / 6e4), e: Math.min(1440, (new Date(ev.end) - day0) / 6e4),
    })));
    h += `<div class="tg-col" data-day="${ymd(d)}">${workBand()}${placed.map((p) => {
      const cols3 = Math.min(3, p.cols);
      if (p.col >= 3) return `<span class="blk-more" data-id="${esc(p.ev.id)}" style="top:${p.s / 60 * HH}px">+1</span>`;
      const top = p.s / 60 * HH, hgt = Math.max(18, (p.e - p.s) / 60 * HH - 2), w = 100 / cols3;
      const travel = p.ev.travel_min ? `<div class="travel" style="top:${top - p.ev.travel_min / 60 * HH}px;height:${p.ev.travel_min / 60 * HH}px;left:calc(${p.col * w}% + 1px);width:calc(${w}% - 3px);${cstyle(p.ev.color)}"></div>` : "";
      const long = p.e - p.s >= 30;
      return `${travel}<div class="blk${st(p.ev)}${p.ev.read_only ? " ro" : ""}" data-id="${esc(p.ev.id)}" style="${cstyle(p.ev.color)};top:${top}px;height:${hgt}px;left:calc(${p.col * w}% + 1px);width:calc(${w}% - 3px)">
        <b>${esc(p.ev.title)}</b>${long && hgt > 34 ? `<small>${hm(new Date(p.ev.start))} – ${hm(new Date(p.ev.end))}${p.ev.location && hgt > 52 ? ` · ${esc(p.ev.location)}` : ""}</small>` : ""}${p.ev.read_only ? "" : '<i class="rs"></i>'}</div>`;
    }).join("")}${sameDay(d, today) ? `<div class="now-line" id="nowLine" style="top:${(today.getHours() * 60 + today.getMinutes()) / 60 * HH}px"></div>` : ""}</div>`;
  }
  return h + "</div>";
}

// ---------------- Liste ----------------
export function agenda() {
  const a = S.agFrom, b = S.agTo, map = byDay(visible(), a, b);
  let h = `<button class="load-more" data-earlier><span class="ms">expand_less</span>Frühere Termine</button>`, gap = null, lastMonth = null;
  const flushGap = (until) => {
    if (!gap) return;
    const last = addDays(until, -1);
    h += `<div class="gap">Nichts geplant · ${sameDay(gap, last) ? dayMonth(gap) : `${gap.getDate()}.–${dayMonth(last)}`}</div>`;
    gap = null;
  };
  for (let d = a; d < b; d = addDays(d, 1)) {
    if (d.getMonth() !== lastMonth && (lastMonth !== null || d.getDate() === 1)) {
      flushGap(d);
      const c = topColor(map, d);
      h += `<div class="banner" style="${cstyle(c)}"><span>${monthName(d)}</span><small>${d.getFullYear()}</small></div>`;
    }
    lastMonth = d.getMonth();
    const l = map.get(ymd(d));
    if (!l) { if (!sameDay(d, new Date())) { gap ||= d; continue; } }
    flushGap(d);
    const rel = relDay(d);
    h += `<div class="lh${rel === "Heute" ? "" : " m"}" data-day="${ymd(d)}">${rel ? `${rel.toUpperCase()} · ` : ""}${shortDay(d).toUpperCase()}</div>`;
    h += l ? l.map((e) => `<div class="swipe-wrap"><div class="swipe-bg"><span class="ms">delete</span></div>${card(e)}</div>`).join("") : '<div class="gap">Heute nichts geplant</div>';
  }
  flushGap(b);
  return h + '<div id="agEnd" class="ag-end"></div>';
}

function topColor(map, d) {
  const cnt = {};
  for (let x = new Date(d.getFullYear(), d.getMonth(), 1); x.getMonth() === d.getMonth(); x = addDays(x, 1)) for (const e of map.get(ymd(x)) || []) if (!e.holiday) cnt[e.color] = (cnt[e.color] || 0) + 1;
  return Object.entries(cnt).sort((p, q) => q[1] - p[1])[0]?.[0] || "blau";
}

// ---------------- Jahr ----------------
export function year() {
  const y = S.date.getFullYear(), a = new Date(y, 0, 1), map = byDay(visible(), a, new Date(y + 1, 0, 1)), today = new Date();
  let h = '<div class="year">';
  for (let m = 0; m < 12; m++) {
    const first = new Date(y, m, 1), g = monthGrid(first);
    h += `<div class="ym" data-month="${ymd(first)}"><div class="ym-t">${monthName(first)}</div><div class="ym-g">${g.slice(0, 7).map((d) => `<span class="wd">${weekdayShort(d)[0]}</span>`).join("")}`;
    for (const d of g) {
      if (d.getMonth() !== m) { h += "<span></span>"; continue; }
      const l = (map.get(ymd(d)) || []).filter((e) => !e.holiday);
      const band = l.find((e) => e.all_day && daysBetween(...span(e)) > 1);
      const dot = l.find((e) => !band || e !== band);
      h += `<span class="yd${sameDay(d, today) ? " today" : ""}${band ? " band" : ""}" style="${band ? cstyle(band.color) : dot ? cstyle(dot.color) : ""}">${d.getDate()}${dot ? "<i></i>" : ""}</span>`;
    }
    h += "</div></div>";
  }
  return h + "</div>";
}

// ---------------- Mini-Monat (Kopfzeile und Seitenleiste am PC) ----------------
export function miniMonth(d, events = visible()) {
  const g = monthGrid(d), map = byDay(events, g[0], addDays(g[41], 1)), today = new Date();
  return `<div class="mini"><div class="mini-h"><button class="icon-btn plain" data-mini="-1"><span class="ms">chevron_left</span></button><b>${monthName(d)} ${d.getFullYear()}</b><button class="icon-btn plain" data-mini="1"><span class="ms">chevron_right</span></button></div>
    <div class="mini-g">${g.slice(0, 7).map((x) => `<span class="wd">${weekdayShort(x).slice(0, 2)}</span>`).join("")}${g.map((x) => `<button class="${x.getMonth() !== d.getMonth() ? "out" : ""}${sameDay(x, today) ? " today" : ""}${sameDay(x, S.sel) ? " sel" : ""}" data-jump="${ymd(x)}">${x.getDate()}${(map.get(ymd(x)) || []).some((e) => !e.holiday) ? "<i></i>" : ""}</button>`).join("")}</div></div>`;
}

export function skeleton() {
  const c = ["blau", "gruen", "lila", "tuerkis", "orange"];
  return `<div class="skel">${Array.from({ length: 6 }, (_, i) => `<div class="sk" style="${cstyle(c[i % 5])};width:${70 + (i * 13) % 30}%"></div>`).join("")}</div>`;
}

export { monthShort };
