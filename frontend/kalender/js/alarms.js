// Erinnerungen ohne Server: solange die App offen ist und keine Verbindung besteht, rechnet sie selbst aus, was fällig ist.
// Online kommt die Erinnerung vom Server (Live-Stream / Push) – beide Wege merken sich, was schon gezeigt wurde, damit nichts doppelt klingelt.
// Reine Logik (ohne DOM).
export const GRACE = 15 * 60 * 1000;      // wie der Server: nach einer Pause nur Erinnerungen der letzten 15 Minuten nachholen
export const KEEP = 3 * 24 * 3600 * 1000; // gezeigte Erinnerungen so lange merken

export const keyOf = (occ, minutes) => `${occ}|${minutes}`;

const startOf = (e) => (e.all_day ? new Date(`${e.start}T00:00:00`) : new Date(e.start));

/** Fällige Erinnerungen aus den lokalen Vorkommen. `shown` = { schlüssel: Zeitpunkt }. Gibt Meldungen im Format der Server-Erinnerung zurück. */
export function due(occs, now, shown = {}) {
  const t = +now, out = [];
  for (const e of occs) {
    if (e.holiday || e.read_only || e.status === "cancelled" || e._pending === "delete" || !e.alarms?.length) continue;
    const s = startOf(e), end = e.all_day ? new Date(`${e.end}T00:00:00`) : new Date(e.end);
    if (+end <= t) continue;
    for (const m of e.alarms) {
      const at = +s - m * 60000;
      if (at > t || at < t - GRACE) continue;
      const key = keyOf(e.id, m);
      if (shown[key]) continue;
      out.push({ key, type: "alarm", occurrence_id: e.id, event_id: e.event_id, title: e.title, calendar: e.calendar, color: e.color, start: e.start, end: e.end,
        all_day: e.all_day, location: e.location, minutes: m, action: e.alarm_action || "notify", task: "", local: true });
    }
  }
  return out.sort((a, b) => a.start.localeCompare(b.start));
}

/** Gemerkte Schlüssel ergänzen und alte entfernen. */
export function remember(shown, keys, now) {
  const out = {};
  for (const [k, at] of Object.entries(shown || {})) if (+now - at < KEEP) out[k] = at;
  for (const k of keys) out[k] = +now;
  return out;
}
