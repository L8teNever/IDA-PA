// Eigene Datums- und Zeitwahl im IDA-Stil (statt der Browser-Felder) – als kleines Sheet.
import { addMonths, monthGrid, monthName, parseYmd, pad, sameDay, weekdayShort, ymd } from "./dates.js";

function sheet(html) {
  const sc = document.createElement("div"); sc.className = "pk-scrim";
  const el = document.createElement("div"); el.className = "pk"; el.innerHTML = html;
  document.body.append(sc, el);
  requestAnimationFrame(() => document.body.classList.add("pk-open"));
  const close = () => { document.body.classList.remove("pk-open"); setTimeout(() => { sc.remove(); el.remove(); }, 250); };
  sc.onclick = close;
  return { el, close };
}

export function pickDate(value, onPick) {
  let cur = parseYmd(value), view = new Date(cur.getFullYear(), cur.getMonth(), 1);
  const { el, close } = sheet("");
  const draw = () => {
    const g = monthGrid(view), today = new Date();
    el.innerHTML = `<div class="pk-head"><span class="pk-big">${cur.getDate()}. ${monthName(cur)}</span><span class="pk-hint">${cur.getFullYear()}</span></div>
      <div class="mini-h"><button class="icon-btn plain" data-m="-1"><span class="ms">chevron_left</span></button><b>${monthName(view)} ${view.getFullYear()}</b><button class="icon-btn plain" data-m="1"><span class="ms">chevron_right</span></button></div>
      <div class="mini-g big">${g.slice(0, 7).map((d) => `<span class="wd">${weekdayShort(d).slice(0, 2)}</span>`).join("")}${g.map((d) => `<button class="${d.getMonth() !== view.getMonth() ? "out" : ""}${sameDay(d, today) ? " today" : ""}${sameDay(d, cur) ? " sel" : ""}" data-d="${ymd(d)}">${d.getDate()}</button>`).join("")}</div>
      <div class="pk-quick"><button data-q="0">Heute</button><button data-q="1">Morgen</button><button data-q="7">In 1 Woche</button></div>`;
    el.querySelectorAll("[data-m]").forEach((b) => (b.onclick = () => { view = addMonths(view, +b.dataset.m); draw(); }));
    el.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => { close(); onPick(b.dataset.d); }));
    el.querySelectorAll("[data-q]").forEach((b) => (b.onclick = () => { const d = new Date(); d.setDate(d.getDate() + +b.dataset.q); close(); onPick(ymd(d)); }));
  };
  draw();
  // Wischen wechselt den Monat
  let x0 = null;
  el.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; }, { passive: true });
  el.addEventListener("touchend", (e) => { const dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 60) { view = addMonths(view, dx < 0 ? 1 : -1); draw(); } }, { passive: true });
}

const QUICK = ["08:00", "09:00", "12:00", "14:00", "16:00", "18:00", "20:00"];
export function pickTime(value, onPick) {
  let [h, m] = value.split(":").map(Number);
  m = Math.round(m / 5) * 5 % 60;
  const { el, close } = sheet("");
  const draw = () => {
    el.innerHTML = `<div class="pk-head"><span class="pk-big">${pad(h)}:${pad(m)}</span><span class="pk-hint">Uhr</span></div>
      <div class="pk-label">Stunde</div><div class="pk-grid h">${Array.from({ length: 24 }, (_, i) => `<button data-h="${i}" class="${i === h ? "on" : ""}">${pad(i)}</button>`).join("")}</div>
      <div class="pk-label">Minute</div><div class="pk-grid m">${Array.from({ length: 12 }, (_, i) => `<button data-mi="${i * 5}" class="${i * 5 === m ? "on" : ""}">${pad(i * 5)}</button>`).join("")}</div>
      <div class="pk-quick">${QUICK.map((q) => `<button data-q="${q}">${q}</button>`).join("")}</div>
      <button class="save wide" data-ok>Übernehmen</button>`;
    el.querySelectorAll("[data-h]").forEach((b) => (b.onclick = () => { h = +b.dataset.h; draw(); }));
    el.querySelectorAll("[data-mi]").forEach((b) => (b.onclick = () => { m = +b.dataset.mi; close(); onPick(`${pad(h)}:${pad(m)}`); }));
    el.querySelectorAll("[data-q]").forEach((b) => (b.onclick = () => { close(); onPick(b.dataset.q); }));
    el.querySelector("[data-ok]").onclick = () => { close(); onPick(`${pad(h)}:${pad(m)}`); };
  };
  draw();
}
