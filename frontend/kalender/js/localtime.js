// „Naive“ Ortszeit rechnen (ohne Zeitzonen-Fallen) – für die Vorschau von Änderungen, die noch auf den Abgleich warten.
// Ein Zeitpunkt ist hier ein Objekt {y, mo, d, h, mi} in der Zeit des Kalenders (Europe/Berlin); gerechnet wird über Date.UTC.
// Reine Logik, ohne DOM.

const pad = (n) => String(n).padStart(2, "0");

export const parse = (s) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(s || "");
  if (!m) return null;
  return { y: +m[1], mo: +m[2], d: +m[3], h: +(m[4] || 0), mi: +(m[5] || 0), dateOnly: m[4] === undefined };
};
export const ms = (t) => Date.UTC(t.y, t.mo - 1, t.d, t.h, t.mi);
export const from = (n) => { const x = new Date(n); return { y: x.getUTCFullYear(), mo: x.getUTCMonth() + 1, d: x.getUTCDate(), h: x.getUTCHours(), mi: x.getUTCMinutes() }; };
export const addMin = (t, n) => from(ms(t) + n * 60000);
export const addDays = (t, n) => from(ms(t) + n * 86400000);
export const diffMin = (a, b) => Math.round((ms(a) - ms(b)) / 60000);
export const dayStr = (t) => `${t.y}-${pad(t.mo)}-${pad(t.d)}`;
export const key = (t) => `${t.y}${pad(t.mo)}${pad(t.d)}T${pad(t.h)}${pad(t.mi)}`;
export const weekday = (t) => (new Date(ms(t)).getUTCDay() + 6) % 7;     // 0 = Montag
export const daysInMonth = (y, mo) => new Date(Date.UTC(y, mo, 0)).getUTCDate();

/** Zeitversatz der Zone in Minuten für diese Ortszeit (Sommer-/Winterzeit eingerechnet). */
export function offsetMin(t, tz) {
  const guess = ms(t);
  const at = (utc) => {
    const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
      .formatToParts(new Date(utc)).map((x) => [x.type, x.value]));
    return (Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute) - utc) / 60000;
  };
  const o1 = at(guess - at(guess) * 60000);   // zweimal annähern: am Umstellungstag stimmt sonst die Stunde nicht
  return at(guess - o1 * 60000);
}

/** „2026-10-30T15:00:00+01:00“ aus der Ortszeit. */
export function iso(t, tz) {
  const off = offsetMin(t, tz), a = Math.abs(off);
  return `${dayStr(t)}T${pad(t.h)}:${pad(t.mi)}:00${off < 0 ? "-" : "+"}${pad(Math.floor(a / 60))}:${pad(a % 60)}`;
}
