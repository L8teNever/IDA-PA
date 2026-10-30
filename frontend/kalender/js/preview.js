// Vorschau wartender Änderungen: so sähe der Kalender aus, wenn der Server sie schon hätte. Reine Logik (ohne DOM).
//
// apply(rows, ops, ctx) legt die offenen Änderungen der Outbox der Reihe nach über die gespeicherten Vorkommen.
//   rows: Vorkommen aus dem lokalen Speicher (Format wie GET /events), ops: Outbox-Einträge,
//   ctx:  { a, b: Bereich als JJJJ-MM-TT [a, b), cals: Kalender, tz: Zeitzone des Kalenders }
// Einfache Wiederholungen (täglich, werktags, wöchentlich mit Tagen, monatlich am Tag, jährlich; Intervall, Anzahl, Ende) werden selbst
// berechnet; alles andere zeigt nur den ersten Termin mit `_partial` – nach dem Abgleich ist die Serie vollständig.
import * as T from "./localtime.js";

const eventId = (id) => String(id).split("_")[0];
const keyOf = (id) => (String(id).includes("_") ? String(id).split("_")[1] : null);
const DAYS = { mo: 0, di: 1, mi: 2, do: 3, fr: 4, sa: 5, so: 6, tu: 1, we: 2, th: 3, su: 6 };
const RR_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];

/** Wiederholung aus dem Body (repeat als Text/Objekt oder fertige RRULE) → { freq, interval, days, count, until, nth } oder null (keine) / "?" (zu kompliziert) */
export function ruleOf(body) {
  const r = body.repeat, raw = body.rrule;
  let o = null;
  if (typeof r === "string") {
    if (!r || r === "none") return body.rrule ? parseRrule(raw) : null;
    o = r === "weekdays" ? { freq: "weekly", days: [0, 1, 2, 3, 4] } : { freq: r };
  } else if (r && typeof r === "object") {
    if (!r.freq && !r.repeat) return null;
    o = { freq: r.freq || r.repeat, interval: r.interval, days: r.days, count: r.count, until: r.until, nth: r.nth };
  } else if (raw) return parseRrule(raw);
  else return null;
  const freq = { daily: "daily", weekly: "weekly", monthly: "monthly", yearly: "yearly", weekdays: "weekly" }[String(o.freq).toLowerCase()];
  if (!freq) return "?";
  const days = (o.days || []).map((d) => (typeof d === "number" ? d : DAYS[String(d).toLowerCase().slice(0, 2)]));
  if (days.some((d) => d === undefined)) return "?";
  return { freq, interval: Math.max(1, +o.interval || 1), days, count: o.count ? +o.count : 0, until: o.until ? String(o.until).slice(0, 10) : "", nth: o.nth ? +o.nth : 0 };
}

function parseRrule(raw) {
  const p = Object.fromEntries(String(raw).replace("RRULE:", "").split(";").map((x) => x.split("=")));
  const freq = { DAILY: "daily", WEEKLY: "weekly", MONTHLY: "monthly", YEARLY: "yearly" }[p.FREQ];
  if (!freq) return "?";
  const by = p.BYDAY ? p.BYDAY.split(",") : [];
  if (by.some((x) => !/^[A-Z]{2}$/.test(x)) || p.BYSETPOS || p.BYMONTH || (p.BYMONTHDAY && p.BYMONTHDAY.includes(","))) return "?";
  const until = p.UNTIL ? `${p.UNTIL.slice(0, 4)}-${p.UNTIL.slice(4, 6)}-${p.UNTIL.slice(6, 8)}` : "";
  return { freq, interval: +p.INTERVAL || 1, days: by.map((x) => RR_DAYS.indexOf(x)), count: +p.COUNT || 0, until, nth: 0 };
}

/** Anfangszeiten (naive Ortszeit) der Serie im Bereich [a, b) – höchstens 400. `start` ist der erste Termin. */
export function starts(rule, start, a, b) {
  const endMs = Date.parse(`${b}T00:00:00Z`), until = rule.until ? Date.parse(`${rule.until}T23:59:59Z`) : Infinity;
  const all = [];
  let n = 0;
  const push = (t) => {                                   // false = Ende der Serie bzw. des Bereichs erreicht
    if (T.ms(t) < T.ms(start)) return true;
    if (T.ms(t) > until || (rule.count && n >= rule.count) || Date.UTC(t.y, t.mo - 1, t.d) >= endMs) return false;
    n++; all.push(t);
    return true;
  };
  const at = (day) => ({ ...day, h: start.h, mi: start.mi });
  if (rule.freq === "daily") {
    for (let i = 0; i < 6000; i++) if (!push(at(T.addDays(start, i * rule.interval)))) break;
  } else if (rule.freq === "weekly") {
    const days = rule.days.length ? [...rule.days].sort((x, y) => x - y) : [T.weekday(start)];
    const monday = T.addDays(start, -T.weekday(start));
    outer: for (let w = 0; w < 1600; w++) for (const d of days) if (!push(at(T.addDays(monday, w * 7 * rule.interval + d)))) break outer;
  } else if (rule.freq === "monthly") {
    for (let i = 0; i < 600; i++) {
      const m0 = start.mo - 1 + i * rule.interval, y = start.y + Math.floor(m0 / 12), mo = (m0 % 12) + 1, dim = T.daysInMonth(y, mo);
      let d;
      if (rule.nth && rule.days.length) {                  // „der 2. Dienstag“, „der letzte Freitag“
        const wd = rule.days[0], w1 = T.weekday({ y, mo, d: 1, h: 0, mi: 0 });
        d = rule.nth > 0 ? 1 + ((wd - w1 + 7) % 7) + (rule.nth - 1) * 7 : dim - ((T.weekday({ y, mo, d: dim, h: 0, mi: 0 }) - wd + 7) % 7) + (rule.nth + 1) * 7;
        if (d < 1 || d > dim) continue;
      } else { d = start.d; if (d > dim) continue; }      // den 31. gibt es nicht in jedem Monat
      if (!push({ y, mo, d, h: start.h, mi: start.mi })) break;
    }
  } else if (rule.freq === "yearly") {
    for (let i = 0; i < 300; i++) {
      const y = start.y + i * rule.interval;
      if (start.mo === 2 && start.d === 29 && T.daysInMonth(y, 2) < 29) continue;
      if (!push({ y, mo: start.mo, d: start.d, h: start.h, mi: start.mi })) break;
    }
  }
  return all.filter((t) => T.dayStr(t) >= a && T.dayStr(t) < b).slice(0, 400);
}

const field = (o, b, cals) => {
  for (const k of ["title", "location", "status", "transparency", "travel_min", "alarms", "alarm_action", "alarm_task", "description", "url"]) if (k in b && b[k] !== undefined) o[k] = b[k];
  if ("description" in b) o.has_description = !!b.description;
  if ("color" in b) o.color = b.color || (cals.find((c) => c.id === o.calendar_id)?.color ?? o.color);
  const cid = b.calendar_id || (b.calendar && cals.find((c) => c.name.toLowerCase() === String(b.calendar).toLowerCase())?.id);
  if (cid) { const c = cals.find((x) => x.id === cid); if (c) { o.calendar_id = c.id; o.calendar = c.name; if (!b.color && !o._ownColor) o.color = c.color; } }
};

function view(op, ctx, startT, endT, allDay, b) {
  const id = eventId(op.target), cal = ctx.cals.find((c) => c.id === (b.calendar_id || "")) || ctx.cals.find((c) => c.is_default) || ctx.cals[0] || {};
  const o = {
    id, event_id: id, calendar_id: cal.id || "", calendar: cal.name || "", title: "", start: "", end: "", all_day: allDay, location: "",
    color: b.color || cal.color || "blau", recurring: false, recurrence_text: "", alarms: [], status: "confirmed", transparency: "busy", travel_min: 0,
    created_by: "user", updated_by: "user", read_only: false, has_description: !!b.description, description: b.description || "", url: b.url || "", sequence: 0, updated_at: "", _own: true,
  };
  field(o, b, ctx.cals);
  o.start = allDay ? T.dayStr(startT) : T.iso(startT, ctx.tz);
  o.end = allDay ? T.dayStr(endT) : T.iso(endT, ctx.tz);
  return o;
}

const CREATE = (list, op, ctx) => {
  const b = op.body, start = T.parse(b.start), end = T.parse(b.end);
  if (!start) return list;
  const allDay = !!b.all_day || start.dateOnly;
  const dur = allDay ? Math.max(1, end ? T.diffMin(end, start) / 1440 : 1) : (end ? T.diffMin(end, start) : (+b.duration_min || 60));
  const rule = ruleOf(b);
  const mk = (s) => {
    const e = allDay ? T.addDays(s, dur) : T.addMin(s, dur);
    const o = view(op, ctx, s, e, allDay, b);
    if (rule) { o.id = `${o.event_id}_${T.key(allDay ? { ...s, h: 0, mi: 0 } : s)}`; o.recurring = true; }
    o._pending = "create";
    return o;
  };
  if (!rule) { const o = mk(start); return overlaps(o, ctx) ? [...list, o] : list; }
  if (rule === "?") { const o = mk(start); o._partial = true; return overlaps(o, ctx) ? [...list, o] : list; }
  const base = { ...start, h: allDay ? 0 : start.h, mi: allDay ? 0 : start.mi };
  const found = starts(rule, base, T.dayStr(T.addDays(T.parse(ctx.a), -Math.ceil(dur / (allDay ? 1 : 1440)) - 1)), ctx.b);
  return [...list, ...found.map(mk).filter((o) => overlaps(o, ctx))];
};

const overlaps = (o, ctx) => o.start.slice(0, 10) < ctx.b && lastDay(o) >= ctx.a;
const lastDay = (o) => {
  if (o.all_day) return T.dayStr(T.addDays(T.parse(o.end), -1));
  const e = T.parse(o.end);
  return o.end.slice(11, 16) === "00:00" ? T.dayStr(T.addDays(e, -1)) : T.dayStr(e);
};

function matches(list, op) {
  const id = eventId(op.target), key = keyOf(op.target), scope = op.scope || "all";
  const ref = list.find((o) => o.id === op.target) || (key ? null : list.find((o) => o.event_id === id));
  const rows = list.filter((o) => o.event_id === id);
  if (!key || scope === "all" && !key) return { ref: ref || rows[0], hit: rows };
  if (scope === "this") return { ref, hit: ref ? [ref] : [] };
  if (scope === "following") { const from = ref ? ref.start : null; return { ref, hit: from ? rows.filter((o) => o.start >= from) : rows }; }
  return { ref: ref || rows[0], hit: rows };
}

const UPDATE = (list, op, ctx) => {
  const b = op.body, { ref, hit } = matches(list, op);
  if (!hit.length) return list;
  const ids = new Set(hit.map((o) => o.id));
  const timeChange = ["start", "end", "all_day", "duration_min"].some((k) => k in b && b[k] !== undefined);
  const rule = "repeat" in b || "rrule" in b ? ruleOf(b) : undefined;
  return list.map((o) => {
    if (!ids.has(o.id)) return o;
    const n = { ...o, _pending: op.state === "conflict" ? "conflict" : op.state === "error" ? "error" : "pending" };
    if (b.color) n._ownColor = true;
    field(n, b, ctx.cals);
    if (rule !== undefined) n._partial = true;
    if (timeChange && ref) {
      const newStart = T.parse(b.start) || T.parse(ref.start), refStart = T.parse(ref.start), oStart = T.parse(o.start), oEnd = T.parse(o.end);
      const allDay = b.all_day !== undefined ? !!b.all_day : n.all_day;
      const delta = T.diffMin(newStart, refStart);
      const durOld = T.diffMin(oEnd, oStart);
      const s = T.addMin(oStart, delta);
      let dur = durOld;
      if (b.end && b.start) dur = T.diffMin(T.parse(b.end), T.parse(b.start));
      else if (b.end) dur = T.diffMin(T.parse(b.end), refStart) - 0;
      else if (b.duration_min) dur = +b.duration_min;
      n.all_day = allDay;
      n.start = allDay ? T.dayStr(s) : T.iso(s, ctx.tz);
      n.end = allDay ? T.dayStr(T.addMin(s, dur)) : T.iso(T.addMin(s, dur), ctx.tz);
    }
    return n;
  }).filter((o) => overlaps(o, ctx));
};

const DELETE = (list, op) => {
  const { hit } = matches(list, op);
  const ids = new Set(hit.map((o) => o.id));
  return list.filter((o) => !ids.has(o.id));
};

const STEPS = { create: CREATE, update: UPDATE, delete: DELETE };

export function apply(rows, ops, ctx) {
  let list = rows.map((r) => ({ ...r }));
  for (const op of ops) list = STEPS[op.type]?.(list, op, ctx) ?? list;
  return list;
}

export const sortRows = (l) => l.sort((x, y) => (x.all_day === y.all_day ? (x.start < y.start ? -1 : x.start > y.start ? 1 : 0) : x.all_day ? -1 : 1));
