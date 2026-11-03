// Gemeinsame Daten mit automatischer Aktualisierung, solange jemand zuschaut (und die Seite sichtbar ist).
import { api } from "./api.js";

function poller(path, ms) {
  let data = null, err = null, timer = null, running = null;
  const subs = new Set();
  async function tick() {
    clearTimeout(timer);
    if (!subs.size) return;
    if (!document.hidden) {
      try { running = api.get(path); data = await running; err = null; } catch (e) { err = e; }
      running = null;
      subs.forEach((fn) => fn(data, err));
    }
    if (subs.size) timer = setTimeout(tick, ms);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && subs.size) tick(); });
  return {
    get data() { return data; },
    sub(fn) {
      subs.add(fn);
      if (data) fn(data, err);
      if (subs.size === 1) tick();
      return () => { subs.delete(fn); if (!subs.size) clearTimeout(timer); };
    },
    refresh: () => tick(),
  };
}

export const live = poller("/monitor/now", 15000);    // Server + Container jetzt
export const stacks = poller("/stacks", 10000);       // alle Stacks

// Live-Werte je Container-Name / je Stack (Summe)
export function liveIndex(now) {
  const byName = {}, byStack = {};
  for (const c of now?.containers || []) {
    byName[c.name] = c;
    const s = (byStack[c.stack] ||= { cpu: 0, mem: 0, net_rx: 0, net_tx: 0 });
    s.cpu += c.cpu || 0; s.mem += c.mem || 0; s.net_rx += c.net_rx || 0; s.net_tx += c.net_tx || 0;
  }
  return { byName, byStack };
}

export const app = { info: null };
