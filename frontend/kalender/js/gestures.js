// Gesten: Termin aufziehen, verschieben, verlängern (Zeitraster), Pinch für die Stundenhöhe,
// Griff in „Monat + Tag“, Wischen auf Karten in der Liste, langes Drücken auf Tage.
import { S } from "./store.js";
import { parseYmd } from "./dates.js";

const haptic = () => navigator.vibrate?.(8);
const SNAP = 15;
let suppressClick = 0;
/** Ein Klick direkt nach einer Geste soll nichts öffnen. */
export const swallowClick = () => Date.now() - suppressClick < 400;

function blockScroll(on) {
  const v = document.querySelector(".view");
  if (!v) return;
  v.style.touchAction = on ? "none" : "";
  v.style.overflowY = on ? "hidden" : "";
}

/** Zeitraster: cb = { create(start, end), move(ev, start, end, edge), repaint(), shift: Tage pro Zeitraum } */
export function bindGrid(view, cb) {
  const body = view.querySelector(".tg-body");
  if (!body) return;
  const HH = () => S.hh;
  const minsAt = (col, y) => Math.max(0, Math.min(1440, Math.round((y - col.getBoundingClientRect().top) / HH() * 60 / SNAP) * SNAP));
  const colAt = (x, y) => document.elementsFromPoint(x, y).find((n) => n.classList?.contains("tg-col"));
  const dateAt = (col, mins) => { const d = parseYmd(col.dataset.day); d.setHours(0, mins, 0, 0); return d; };

  body.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.isPrimary === false) return;
    const blk = e.target.closest(".blk"), col = e.target.closest(".tg-col");
    if (!col) return;
    const touch = e.pointerType !== "mouse", x0 = e.clientX, y0 = e.clientY;
    let mode = null, timer = null, ghost = null, startMin = 0, origCol = col, offMin = 0, durMin = 0;
    const ev = blk ? S.events.find((x) => x.id === blk.dataset.id) : null;
    if (blk && (!ev || ev.read_only)) return;

    const begin = () => {
      haptic();
      blockScroll(true);
      if (blk) {
        mode = e.target.classList.contains("rs") ? "resize" : "move";
        const day0 = parseYmd(col.dataset.day).getTime();
        startMin = Math.round((new Date(ev.start) - day0) / 6e4);
        durMin = Math.round((new Date(ev.end) - new Date(ev.start)) / 6e4);
        offMin = minsAt(col, y0) - startMin;
        blk.classList.add("lift");
      } else {
        mode = "create";
        startMin =Math.floor(((y0 - col.getBoundingClientRect().top) / HH() * 60) / SNAP) * SNAP;
        ghost = document.createElement("div"); ghost.className = "ghost";
        col.append(ghost); draw(startMin + 60);
      }
    };
    const draw = (endMin) => {
      const a = Math.min(startMin, endMin), b = Math.max(startMin + SNAP, endMin);
      ghost.style.top = `${a / 60 * HH()}px`; ghost.style.height = `${(b - a) / 60 * HH()}px`;
      ghost.textContent = `${fmt(a)} – ${fmt(b)}`;
      ghost.dataset.a = a; ghost.dataset.b = b;
    };
    if (touch) timer = setTimeout(begin, 380);

    let lastX = x0;
    const move = (m) => {
      const dx = m.clientX - x0, dy = m.clientY - y0;
      lastX = m.clientX;
      if (mode === "move") {
        const r = view.getBoundingClientRect();
        view.classList.toggle("edge-l", m.clientX < r.left + 28);
        view.classList.toggle("edge-r", m.clientX > r.right - 28);
      }
      if (!mode) {
        if (touch) { if (Math.hypot(dx, dy) > 8) cleanup(); return; }
        if (Math.hypot(dx, dy) < 5) return;
        begin();
      }
      m.preventDefault();
      autoScroll(view, m.clientY);
      const c = colAt(m.clientX, m.clientY) || origCol;
      if (mode === "create") { if (c === col) draw(minsAt(col, m.clientY) || startMin + SNAP); return; }
      if (mode === "resize") {
        const end = Math.max(startMin + SNAP, minsAt(col, m.clientY));
        blk.style.height = `${(end - startMin) / 60 * HH() - 2}px`; blk.dataset.end = end; snapHaptic(end);
        return;
      }
      if (c !== origCol) { c.append(blk); origCol = c; blk.style.left = "1px"; blk.style.width = "calc(100% - 3px)"; haptic(); }
      const s = Math.max(0, Math.min(1440 - SNAP, minsAt(c, m.clientY) - offMin));
      blk.style.top = `${s / 60 * HH()}px`; blk.dataset.start = s; snapHaptic(s);
    };
    let lastSnap = null;
    const snapHaptic = (v) => { if (v !== lastSnap) { lastSnap = v; navigator.vibrate?.(4); } };

    const up = () => {
      const was = mode;
      cleanup();
      if (!was) return;
      suppressClick = Date.now();
      if (was === "create") {
        const a = +ghost.dataset.a, b = +ghost.dataset.b; ghost.remove();
        cb.create(dateAt(col, a), dateAt(col, b));
      } else if (was === "resize") {
        if (blk.dataset.end) cb.move(ev, dateAt(col, startMin), dateAt(col, +blk.dataset.end)); else cb.repaint();
      } else {
        const s = blk.dataset.start != null ? +blk.dataset.start : startMin;
        const r = view.getBoundingClientRect(), edge = lastX < r.left + 28 ? -1 : lastX > r.right - 28 ? 1 : 0;
        const a = dateAt(origCol, s);
        if (edge) { // über den Bildschirmrand: in den vorigen/nächsten Zeitraum
          a.setDate(a.getDate() + edge * (cb.shift || 1));
          return cb.move(ev, a, new Date(a.getTime() + durMin * 6e4), edge);
        }
        if (origCol === col && s === startMin) return cb.repaint();
        cb.move(ev, a, dateAt(origCol, s + durMin));
      }
    };
    const cleanup = () => {
      clearTimeout(timer);
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", up);
      blockScroll(false);
      view.classList.remove("edge-l", "edge-r");
      blk?.classList.remove("lift");
      if (mode === null && ghost) ghost.remove();
      mode = null;
    };
    addEventListener("pointermove", move, { passive: false });
    addEventListener("pointerup", up);
    addEventListener("pointercancel", up);
  });

  // Pinch: Stundenhöhe 40–120 px, wird gemerkt
  let d0 = 0, h0 = 0, raf = 0;
  const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  view.addEventListener("touchstart", (e) => { if (e.touches.length === 2) { d0 = dist(e.touches); h0 = S.hh; } }, { passive: true });
  view.addEventListener("touchmove", (e) => {
    if (e.touches.length !== 2 || !d0) return;
    e.preventDefault();
    const hh = Math.round(Math.max(40, Math.min(120, h0 * dist(e.touches) / d0)));
    if (hh === S.hh) return;
    const ratio = hh / S.hh; S.hh = hh;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => { const v = document.querySelector(".view"), mid = v.scrollTop + v.clientHeight / 2; cb.repaint(); const nv = document.querySelector(".view"); nv.scrollTop = mid * ratio - nv.clientHeight / 2; });
  }, { passive: false });
  view.addEventListener("touchend", () => { if (d0) { d0 = 0; localStorage.setItem("kal-hh", S.hh); } }, { passive: true });
}

function autoScroll(view, y) {
  const r = view.getBoundingClientRect();
  if (y < r.top + 48) view.scrollTop -= 12; else if (y > r.bottom - 120) view.scrollTop += 12;
}
const fmt = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

/** Griff zwischen Monatsraster und Tagesliste: hoch = Wochenzeile, runter = Vollmonat mit Text. */
export function bindGrip(view, onChange) {
  const g = view.querySelector("#grip");
  if (!g) return;
  g.addEventListener("pointerdown", (e) => {
    const y0 = e.clientY;
    g.setPointerCapture(e.pointerId);
    const up = (u) => {
      g.removeEventListener("pointerup", up);
      const dy = u.clientY - y0, order = ["week", "month", "full"], i = order.indexOf(S.mdMode);
      let n = i;
      if (dy < -30) n = Math.max(0, i - 1); else if (dy > 30) n = Math.min(2, i + 1); else n = (i + 1) % 3;
      if (n !== i) { S.mdMode = order[n]; localStorage.setItem("kal-md", S.mdMode); haptic(); onChange(); }
    };
    g.addEventListener("pointerup", up);
  });
}

/** Liste: Karte nach links wischen = löschen (mit Rückgängig), nach rechts = Erinnerung (künftig) bzw. erledigt (vorbei). */
export function bindSwipe(view, onDelete, onRight) {
  view.addEventListener("pointerdown", (e) => {
    const card = e.target.closest(".swipe-wrap .card");
    if (!card || e.button !== 0) return;
    const x0 = e.clientX, y0 = e.clientY, w = card.offsetWidth;
    let dragging = false, dx = 0;
    const move = (m) => {
      dx = m.clientX - x0;
      if (!dragging) {
        if (Math.abs(m.clientY - y0) > 10) return cleanup();
        if (Math.abs(dx) < 12) return;
        dragging = true; card.parentElement.classList.add("swiping");
      }
      card.style.transition = "none"; card.style.transform = `translateX(${dx}px)`;
      card.parentElement.classList.toggle("right", dx > 0);
      card.parentElement.classList.toggle("armed", Math.abs(dx) > w * 0.35);
    };
    const up = () => {
      cleanup();
      if (!dragging) return;
      suppressClick = Date.now();
      card.style.transition = "";
      if (-dx > w * 0.35) { card.style.transform = "translateX(-110%)"; haptic(); setTimeout(() => onDelete(card.dataset.id), 180); }
      else {
        if (dx > w * 0.35) { haptic(); onRight(card.dataset.id); }
        card.style.transform = ""; card.parentElement.classList.remove("swiping", "armed", "right");
      }
    };
    const cleanup = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); removeEventListener("pointercancel", up); };
    addEventListener("pointermove", move); addEventListener("pointerup", up); addEventListener("pointercancel", up);
  });
}

/** Langes Drücken auf ein Element (Tag im Monat, Kalender im Drawer). */
export function longPress(root, selector, cb, ms = 480) {
  let t = null;
  root.addEventListener("pointerdown", (e) => {
    const el = e.target.closest(selector);
    if (!el) return;
    const x0 = e.clientX, y0 = e.clientY;
    t = setTimeout(() => { suppressClick = Date.now(); haptic(); cb(el); }, ms);
    const stop = (m) => { if (m.type !== "pointermove" || Math.hypot(m.clientX - x0, m.clientY - y0) > 8) { clearTimeout(t); removeEventListener("pointermove", stop); removeEventListener("pointerup", stop); } };
    addEventListener("pointermove", stop); addEventListener("pointerup", stop);
  });
  root.addEventListener("contextmenu", (e) => { if (e.target.closest(selector)) e.preventDefault(); });
}
