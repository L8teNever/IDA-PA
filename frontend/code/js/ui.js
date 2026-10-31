// Kleine Bausteine der Oberfläche: Escaping, Toast, Zeitangaben, Markdown, Dialoge und Auswahlmenüs.
export const $ = (id) => document.getElementById(id);

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 2400);
}

// Führt eine Aktion aus und zeigt Fehler als Toast; gibt das Ergebnis oder undefined zurück.
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

const fmtTime = (iso) => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
export const fmtDateTime = (iso) => new Date(iso).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
export function fmtRel(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return fmtTime(iso);
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "gestern";
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}
// "12 s" / "1 Min 05 s"
export function fmtDur(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 1 ? "<1 s" : s < 60 ? `${s} s` : `${Math.floor(s / 60)} Min ${String(s % 60).padStart(2, "0")} s`;
}
export function fmtBytes(n) {
  if (n == null || isNaN(n)) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// Sicheres, schlankes Markdown für Antworten: Codeblöcke (mit Kopieren), Inline-Code, fett/kursiv, Überschriften, Listen, Links.
export function md(src) {
  const blocks = [];
  let s = esc(src).replace(/```([\w+-]*)[^\n]*\n?([\s\S]*?)```/g, (_, lang, code) => { blocks.push([lang, code.replace(/\n$/, "")]); return `\u0000${blocks.length - 1}\u0000`; });
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/^#{1,4} (.+)$/gm, '<b class="md-h">$1</b>')
    .replace(/^\s*[-*] (.+)$/gm, '<span class="md-li">$1</span>')
    .replace(/^\s*(\d+)\. (.+)$/gm, '<span class="md-li num" data-n="$1">$2</span>')
    // Blöcke (Listenpunkte, Überschriften, Code) brechen selbst um – der Zeilenumbruch danach wäre eine Leerzeile zu viel
    .replace(/(<\/span>|<b class="md-h">[^<]*<\/b>|\u0000\d+\u0000)\n/g, "$1")
    .replace(/\n(\u0000\d+\u0000)/g, "$1");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => {
    const [lang, code] = blocks[+i];
    return `<div class="md-code"><div class="md-code-head"><span>${lang || "code"}</span><button type="button" class="md-copy" title="Kopieren"><span class="ms">content_copy</span></button></div><pre>${code}</pre></div>`;
  });
}
document.addEventListener("click", (e) => {
  const b = e.target.closest(".md-copy");
  if (!b) return;
  const pre = b.closest(".md-code")?.querySelector("pre");
  navigator.clipboard?.writeText(pre?.textContent || "").then(() => toast("Kopiert"), () => toast("Kopieren nicht möglich"));
});

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
    back.classList.add("closing");
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
addEventListener("keydown", (e) => { if (e.key === "Escape" && modalClose && !document.querySelector(".dd-menu.show, .pop.show")) modalClose(); });

export const sheetHead = (title, icon = "") => `<div class="sheet-head"><h2>${icon ? `<span class="ms">${icon}</span>` : ""}${esc(title)}</h2>
  <button type="button" class="icon-btn" data-close aria-label="Schließen"><span class="ms">close</span></button></div>`;

// Rückfrage mit Ja/Nein; true bei Bestätigung
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

// Kleines Formular: fields = [{ name, label, value, placeholder, type: "text"|"switch"|"textarea", hint }]
// Gibt die Werte zurück (oder null bei Abbruch).
export function formDialog(title, fields, { ok = "Speichern", icon = "" } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const html = fields.map((f) => f.type === "switch"
      ? `<div class="sw-row"><span class="sw-txt"><b>${esc(f.label)}</b>${f.hint ? `<small>${esc(f.hint)}</small>` : ""}</span>
          <button type="button" class="sw ${f.value ? "on" : ""}" data-sw="${f.name}" aria-label="${esc(f.label)}"></button></div>`
      : `<label class="field-row"><span>${esc(f.label)}</span>${f.type === "textarea"
        ? `<textarea class="cd-input" name="${f.name}" rows="4" placeholder="${esc(f.placeholder || "")}">${esc(f.value || "")}</textarea>`
        : `<input class="cd-input" name="${f.name}" value="${esc(f.value || "")}" placeholder="${esc(f.placeholder || "")}" autocomplete="off" maxlength="${f.max || 300}">`}
        ${f.hint ? `<small>${esc(f.hint)}</small>` : ""}</label>`).join("");
    const m = openModal(`${sheetHead(title, icon)}<form class="sheet-form" id="cdForm">${html}<div class="ferr" id="cdErr"></div>
      <div class="sheet-actions"><button type="button" class="btn-tonal" data-close>Abbrechen</button><button class="btn-primary" type="submit">${esc(ok)}</button></div></form>`,
    { onClose: () => { if (!done) resolve(null); } });
    m.el.querySelectorAll("[data-sw]").forEach((b) => (b.onclick = () => b.classList.toggle("on")));
    const form = m.el.querySelector("#cdForm");
    form.onsubmit = (e) => {
      e.preventDefault();
      const out = {};
      for (const f of fields) {
        if (f.type === "switch") out[f.name] = form.querySelector(`[data-sw="${f.name}"]`).classList.contains("on");
        else out[f.name] = form.elements[f.name].value.trim();
        if (f.required && !out[f.name]) { m.el.querySelector("#cdErr").textContent = `${f.label} fehlt`; form.elements[f.name]?.focus(); return; }
      }
      done = true;
      resolve(out);
      m.close();
    };
    form.querySelector("input, textarea")?.focus();
  });
}

// ---- Auswahlmenü an einem Knopf (wie die Dropdowns): items = [{ value, icon, label, sub, on, danger }] ----
let popClose = null;
export function popMenu(anchor, items, onPick, { align = "left", cls = "" } = {}) {
  if (popClose) { const was = popClose.anchor === anchor; popClose(); if (was) return; }
  const m = document.createElement("div");
  m.className = `pop ${cls}`;
  m.setAttribute("role", "menu");
  m.innerHTML = items.map((it, i) => it === "-" ? `<div class="pop-sep"></div>` : it.head ? `<div class="pop-head">${esc(it.head)}</div>`
    : `<button type="button" role="menuitem" class="pop-item ${it.on ? "sel" : ""} ${it.danger ? "danger" : ""}" data-i="${i}">
      ${it.icon ? `<span class="ms">${it.icon}</span>` : ""}<span class="pop-txt"><b>${esc(it.label)}</b>${it.sub ? `<small>${esc(it.sub)}</small>` : ""}</span>
      ${it.on ? `<span class="ms pop-check">check</span>` : ""}</button>`).join("");
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  const w = m.offsetWidth, h = m.offsetHeight;
  let left = align === "right" ? r.right - w : r.left;
  left = Math.max(8, Math.min(left, innerWidth - w - 8));
  const below = innerHeight - r.bottom - 8;
  const up = below < h && r.top > below;
  m.style.left = `${left}px`;
  if (up) { m.style.bottom = `${innerHeight - r.top + 6}px`; m.classList.add("up"); m.style.maxHeight = `${r.top - 14}px`; }
  else { m.style.top = `${r.bottom + 6}px`; m.style.maxHeight = `${below - 6}px`; }
  requestAnimationFrame(() => m.classList.add("show"));
  const close = () => {
    m.classList.remove("show");
    setTimeout(() => m.remove(), 160);
    document.removeEventListener("mousedown", outside, true);
    removeEventListener("keydown", key, true);
    removeEventListener("resize", close);
    if (popClose === close) popClose = null;
  };
  close.anchor = anchor;
  const outside = (e) => { if (!m.contains(e.target) && !anchor.contains(e.target)) close(); };
  const key = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); anchor.focus(); } };
  setTimeout(() => document.addEventListener("mousedown", outside, true));
  addEventListener("keydown", key, true);
  addEventListener("resize", close);
  m.onclick = (e) => {
    const b = e.target.closest("[data-i]");
    if (!b) return;
    close();
    onPick(items[+b.dataset.i].value);
  };
  popClose = close;
  m.querySelector(".pop-item.sel, .pop-item")?.focus({ preventScroll: true });
  return close;
}
export function closePop() { popClose?.(); }
