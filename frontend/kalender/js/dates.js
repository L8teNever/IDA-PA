// Datums-Hilfen (de-DE). Alle Berechnungen in Ortszeit des Geräts; Wochenstart aus den Einstellungen (1 = Mo, 0 = So).
export const pad = (n) => String(n).padStart(2, "0");
export const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
export const parseYmd = (s) => { const [y, m, d] = s.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
export const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
export const addMonths = (d, n) => new Date(d.getFullYear(), d.getMonth() + n, 1);
export const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
export const sameDay = (a, b) => ymd(a) === ymd(b);
export const daysBetween = (a, b) => Math.round((startOfDay(b) - startOfDay(a)) / 864e5);
export const isWeekend = (d) => d.getDay() === 0 || d.getDay() === 6;

let ws = 1;
export const setWeekStart = (v) => { ws = Number(v) === 0 ? 0 : 1; };
export const weekStart = (d) => addDays(startOfDay(d), -((d.getDay() - ws + 7) % 7));

export function isoWeek(d) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  return Math.ceil(((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / 864e5 + 1) / 7);
}

const F = (o) => new Intl.DateTimeFormat("de-DE", o);
const fMonth = F({ month: "long" }), fLong = F({ weekday: "long", day: "numeric", month: "long" }), fMonShort = F({ month: "short" });
const fShort = F({ weekday: "short", day: "numeric", month: "short" }), fWd = F({ weekday: "short" }), fDay = F({ day: "numeric", month: "short" });
export const monthName = (d) => fMonth.format(d);
export const monthShort = (d) => fMonShort.format(d);
export const longDay = (d) => fLong.format(d);
export const shortDay = (d) => fShort.format(d);
export const weekdayShort = (d) => fWd.format(d).replace(".", "");
export const dayMonth = (d) => fDay.format(d);

/** Raster eines Monats: 6 Wochen ab Wochenstart. */
export function monthGrid(d) {
  const s = weekStart(new Date(d.getFullYear(), d.getMonth(), 1));
  return Array.from({ length: 42 }, (_, i) => addDays(s, i));
}

export function relDay(d) {
  const n = daysBetween(new Date(), d);
  return n === 0 ? "Heute" : n === 1 ? "Morgen" : n === -1 ? "Gestern" : null;
}

export function durText(min) {
  if (min < 60) return `${min} Min.`;
  const h = Math.floor(min / 60), m = min % 60;
  if (min % 1440 === 0) return min === 1440 ? "1 Tag" : `${min / 1440} Tage`;
  return m ? `${h} Std. ${m} Min.` : `${h} Std.`;
}

/** Zeitspanne eines Vorkommens in Klartext. */
export function whenText(ev) {
  const s = new Date(ev.start), e = new Date(ev.end);
  if (ev.all_day) {
    const last = addDays(parseYmd(ev.end), -1), first = parseYmd(ev.start);
    return daysBetween(first, last) > 0 ? `${shortDay(first)} – ${shortDay(last)}` : longDay(first);
  }
  if (sameDay(s, e)) return `${longDay(s)} · ${hm(s)} – ${hm(e)}`;
  return `${shortDay(s)} ${hm(s)} – ${shortDay(e)} ${hm(e)}`;
}

/** „Noch 3 Tage“, „In 2 Std.“, „Läuft gerade“, „Vorbei“ */
export function countdown(ev) {
  const now = new Date(), s = ev.all_day ? parseYmd(ev.start) : new Date(ev.start), e = ev.all_day ? parseYmd(ev.end) : new Date(ev.end);
  if (e <= now) return ["Vorbei", ""];
  if (s <= now) return ["Läuft gerade", "now"];
  const min = Math.round((s - now) / 6e4), days = daysBetween(now, s);
  if (days === 0) return [min < 60 ? `In ${min} Min.` : `In ${Math.round(min / 60)} Std.`, "next"];
  if (days === 1) return ["Morgen", "next"];
  return [`Noch ${days} Tage`, ""];
}

export function ago(iso) {
  const min = Math.round((Date.now() - new Date(iso)) / 6e4);
  if (min < 1) return "gerade eben";
  if (min < 60) return `vor ${min} Min.`;
  if (min < 1440) return `vor ${Math.round(min / 60)} Std.`;
  return `vor ${Math.round(min / 1440)} Tagen`;
}
