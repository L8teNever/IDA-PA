// Time picker in the IDA design (instead of the browser's <input type="time">).
// openTimePicker(anchor, { value: "07:00", taken: ["07:00"], onPick: (hhmm) => … })
// Hours 00–23 and minutes in 5-minute steps as tappable grids, plus a few common times.
const QUICK = ["06:00", "07:00", "08:00", "12:00", "18:00", "20:00", "22:00"];
const pad = (n) => String(n).padStart(2, "0");

let current = null; // { el, close }

export function closeTimePicker() {
  if (current) current.close();
}

export function openTimePicker(anchor, { value = "12:00", taken = [], onPick }) {
  closeTimePicker();
  let [h, m] = value.split(":").map(Number);
  m = Math.round(m / 5) * 5 % 60;

  const el = document.createElement("div");
  el.className = "dd-menu tp";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-label", "Uhrzeit wählen");
  document.body.appendChild(el);

  const draw = () => {
    const now = `${pad(h)}:${pad(m)}`;
    el.innerHTML = `
      <div class="tp-head"><span class="tp-big">${now}</span><span class="tp-hint">${taken.includes(now) ? "schon eingetragen" : "Uhr"}</span></div>
      <div class="tp-label">Stunde</div>
      <div class="tp-grid tp-h">${Array.from({ length: 24 }, (_, i) => `<button type="button" data-h="${i}" class="${i === h ? "on" : ""}">${pad(i)}</button>`).join("")}</div>
      <div class="tp-label">Minute</div>
      <div class="tp-grid tp-m">${Array.from({ length: 12 }, (_, i) => i * 5).map((x) => `<button type="button" data-m="${x}" class="${x === m ? "on" : ""}">:${pad(x)}</button>`).join("")}</div>
      <div class="tp-label">Schnell</div>
      <div class="tp-quick">${QUICK.map((q) => `<button type="button" data-q="${q}" ${taken.includes(q) ? "disabled" : ""}>${q}</button>`).join("")}</div>
      <div class="tp-actions"><button type="button" class="es-btn" data-act="cancel">Abbrechen</button><button type="button" class="es-btn primary" data-act="ok" ${taken.includes(now) ? "disabled" : ""}><span class="ms">add</span>${now} hinzufügen</button></div>`;
  };
  draw();

  const place = () => {
    // phones: a sheet at the bottom edge (always fully visible, easy to reach with the thumb)
    if (innerWidth < 600) {
      el.classList.add("sheet");
      Object.assign(el.style, { left: "8px", right: "8px", width: "auto", top: "", bottom: "8px", maxHeight: `${innerHeight - 24}px` });
      return;
    }
    el.classList.remove("sheet");
    el.style.right = "";
    const r = anchor.getBoundingClientRect();
    const w = Math.min(340, innerWidth - 16);
    el.style.width = `${w}px`;
    el.style.left = `${Math.max(8, Math.min(r.left, innerWidth - w - 8))}px`;
    const below = innerHeight - r.bottom - 12, above = r.top - 12;
    const up = below < el.scrollHeight && above > below;
    el.style.maxHeight = `${Math.max(220, up ? above : below)}px`;
    el.style.top = up ? "" : `${r.bottom + 6}px`;
    el.style.bottom = up ? `${innerHeight - r.top + 6}px` : "";
    el.classList.toggle("up", up);
  };
  place();
  requestAnimationFrame(() => el.classList.add("show"));

  const pick = (v) => { close(); onPick(v); };
  el.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    if (b.dataset.h) { h = +b.dataset.h; draw(); }
    else if (b.dataset.m) { m = +b.dataset.m; draw(); }
    else if (b.dataset.q) pick(b.dataset.q);
    else if (b.dataset.act === "ok") pick(`${pad(h)}:${pad(m)}`);
    else if (b.dataset.act === "cancel") close();
  };
  const outside = (e) => { if (!el.contains(e.target) && !anchor.contains(e.target)) close(); };
  const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); close(); anchor.focus(); } };
  const onScroll = (e) => { if (!el.contains(e.target)) place(); };
  setTimeout(() => document.addEventListener("pointerdown", outside), 0);
  document.addEventListener("keydown", onKey);
  addEventListener("resize", place);
  addEventListener("scroll", onScroll, true);

  function close() {
    if (current?.el !== el) return;
    current = null;
    document.removeEventListener("pointerdown", outside);
    document.removeEventListener("keydown", onKey);
    removeEventListener("resize", place);
    removeEventListener("scroll", onScroll, true);
    el.classList.add("closing");
    setTimeout(() => el.remove(), 150);
  }
  current = { el, close };
}
