// Kleine Bausteine der Oberfläche: Escaping, Toast, Zahlenformate, Dialoge und Auswahlmenüs (wie in IDA Code).
export const $ = (id) => document.getElementById(id);

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 2600);
}

export async function attempt(fn, okText) {
  try {
    const r = await fn();
    if (okText) toast(okText);
    return r;
  } catch (e) {
    toast(e.message || "Fehler");
    return undefined;
  }
}

// ---- Formate ----
const nf = (n, d = 1) => Number(n).toLocaleString("de-DE", { maximumFractionDigits: d, minimumFractionDigits: 0 });
export function fmtBytes(n, d = 1) {
  if (n == null || isNaN(n)) return "–";
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  let v = Math.abs(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${nf(Math.sign(n) * v, v >= 100 || i === 0 ? 0 : d)} ${u[i]}`;
}
export const fmtRate = (n) => (n == null ? "–" : `${fmtBytes(n)}/s`);
export const fmtPct = (n, d = 1) => (n == null ? "–" : `${nf(n, n < 10 ? d : 0)} %`);
export function fmtUptime(s) {
  if (!s) return "–";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} T ${h} Std` : h ? `${h} Std ${m} Min` : `${m} Min`;
}
export const fmtTime = (sec) => new Date(sec * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
export const fmtDay = (sec) => new Date(sec * 1000).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
export const fmtDateTime = (sec) => new Date(sec * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
export function fmtAgo(sec) {
  if (!sec) return "";
  const s = Math.round(Date.now() / 1000 - sec);
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} Min`;
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std`;
  return `vor ${Math.round(s / 86400)} T`;
}

// Zustand eines Stacks/Containers -> [Icon, Text, CSS-Klasse]
export const STATES = {
  running: ["check_circle", "Läuft", "ok"],
  partial: ["timelapse", "Teilweise", "warn"],
  problem: ["error", "Problem", "err"],
  stopped: ["stop_circle", "Aus", "off"],
  exited: ["stop_circle", "Beendet", "off"],
  created: ["radio_button_unchecked", "Erstellt", "off"],
  restarting: ["autorenew", "Startet neu", "warn"],
  paused: ["pause_circle", "Pausiert", "warn"],
  dead: ["error", "Tot", "err"],
};
export function ctrState(c) {
  if (c.state === "running" && c.health === "unhealthy") return ["error", "Unhealthy", "err"];
  if (c.state === "running" && c.health === "starting") return ["hourglass_top", "Startet", "warn"];
  return STATES[c.state] || ["help", c.state || "?", "off"];
}

// ---- Dialog (am PC mittig, am Handy als Sheet von unten) ----
let modalClose = null;
export function openModal(html, { wide = false, tall = false, onClose } = {}) {
  closeModal();
  const back = document.createElement("div");
  back.className = "cd-backdrop";
  back.innerHTML = `<div class="cd-sheet ${wide ? "wide" : ""} ${tall ? "tall" : ""}" role="dialog" aria-modal="true">${html}</div>`;
  document.body.appendChild(back);
  requestAnimationFrame(() => back.classList.add("show"));
  const sheet = back.firstElementChild;
  const close = () => {
    if (!back.isConnected) return;
    back.classList.remove("show");
    setTimeout(() => back.remove(), 220);
    if (modalClose === close) modalClose = null;
    onClose?.();
  };
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  sheet.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) close(); });
  modalClose = close;
  return { el: sheet, close };
}
export function closeModal() { modalClose?.(); }
addEventListener("keydown", (e) => { if (e.key === "Escape" && modalClose && !document.querySelector(".pop.show")) modalClose(); });

export const sheetHead = (title, icon = "") => `<div class="sheet-head"><h2>${icon ? `<span class="ms">${icon}</span>` : ""}<span class="tb-title">${esc(title)}</span></h2>
  <button type="button" class="icon-btn" data-close aria-label="Schließen"><span class="ms">close</span></button></div>`;

export function confirmDialog(title, text, { ok = "OK", danger = false } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const m = openModal(`${sheetHead(title)}<p class="sheet-hint">${esc(text)}</p>
      <div class="sheet-actions"><button type="button" class="btn-tonal" data-close>Abbrechen</button>
      <button type="button" class="${danger ? "btn-danger" : "btn-primary"}" id="cdOk">${esc(ok)}</button></div>`, { onClose: () => { if (!done) resolve(false); } });
    m.el.querySelector("#cdOk").onclick = () => { done = true; resolve(true); m.close(); };
    m.el.querySelector("#cdOk").focus();
  });
}

// ---- Auswahlmenü an einem Knopf: items = [{ value, icon, label, sub, on, danger } | "-" | { head }] ----
let popClose = null;
export function popMenu(anchor, items, onPick, { align = "left" } = {}) {
  if (popClose) { const was = popClose.anchor === anchor; popClose(); if (was) return; }
  const m = document.createElement("div");
  m.className = "pop";
  m.setAttribute("role", "menu");
  m.innerHTML = items.map((it, i) => it === "-" ? `<div class="pop-sep"></div>` : it.head ? `<div class="pop-head">${esc(it.head)}</div>`
    : `<button type="button" role="menuitem" class="pop-item ${it.on ? "sel" : ""} ${it.danger ? "danger" : ""}" data-i="${i}">
      ${it.icon ? `<span class="ms">${it.icon}</span>` : ""}<span class="pop-txt"><b>${esc(it.label)}</b>${it.sub ? `<small>${esc(it.sub)}</small>` : ""}</span>
      ${it.on ? `<span class="ms pop-check">check</span>` : ""}</button>`).join("");
  document.body.appendChild(m);
  let rect = anchor.getBoundingClientRect();
  const place = () => {
    // Knopf neu gezeichnet (Live-Aktualisierung)? Dann an der alten Stelle bleiben.
    if (anchor.isConnected) rect = anchor.getBoundingClientRect();
    const r = rect;
    m.style.top = m.style.bottom = m.style.maxHeight = "";
    m.classList.remove("up");
    const w = m.offsetWidth, h = m.scrollHeight;
    let left = align === "right" ? r.right - w : r.left;
    left = Math.max(8, Math.min(left, innerWidth - w - 8));
    const below = innerHeight - r.bottom - 8;
    const up = below < h && r.top > below;
    m.style.left = `${left}px`;
    if (up) { m.style.bottom = `${innerHeight - r.top + 6}px`; m.classList.add("up"); m.style.maxHeight = `${r.top - 14}px`; }
    else { m.style.top = `${r.bottom + 6}px`; m.style.maxHeight = `${below - 6}px`; }
  };
  place();
  requestAnimationFrame(() => m.classList.add("show"));
  // Am Handy feuert "resize" ständig (Adressleiste ein/aus, Tastatur) – dann nur neu ausrichten.
  // Schließen nur, wenn sich die Breite wirklich ändert (Drehen, Fenster am PC).
  let lastW = innerWidth;
  const resize = () => { if (Math.abs(innerWidth - lastW) > 40) close(); else place(); };
  const close = () => {
    m.classList.remove("show");
    setTimeout(() => m.remove(), 160);
    document.removeEventListener("mousedown", outside, true);
    removeEventListener("keydown", key, true);
    removeEventListener("resize", resize);
    if (popClose === close) popClose = null;
  };
  close.anchor = anchor;
  const outside = (e) => { if (!m.contains(e.target) && !anchor.contains(e.target)) close(); };
  const key = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); anchor.focus(); } };
  setTimeout(() => document.addEventListener("mousedown", outside, true));
  addEventListener("keydown", key, true);
  addEventListener("resize", resize);
  m.onclick = (e) => {
    const b = e.target.closest("[data-i]");
    if (!b) return;
    close();
    onPick(items[+b.dataset.i].value);
  };
  popClose = close;
  return close;
}

// Balken (0..1) mit Farbe je nach Auslastung
export function bar(frac, { warn = 0.75, err = 0.9 } = {}) {
  const f = Math.max(0, Math.min(1, frac || 0));
  return `<span class="bar ${f >= err ? "err" : f >= warn ? "warn" : ""}"><i style="width:${(f * 100).toFixed(1)}%"></i></span>`;
}
