// DOM helpers shared by all pages.
export const $ = (id) => document.getElementById(id);

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function toast(text) {
  let t = document.querySelector(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 2200);
}

// Bottom sheet (the design's event sheet). Returns the sheet element.
export function openSheet(html) {
  const s = $("evSheet");
  s.innerHTML = `<div class="handle"></div>` + html;
  document.body.classList.add("ev-open");
  return s;
}
export function closeSheet() { document.body.classList.remove("ev-open", "mood-sheet"); }

// Runs an async action, shows its error as toast; returns its result or undefined.
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

export const fmtTime = (iso) => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
export const fmtDate = (iso) => new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
export const fmtDateTime = (iso) => new Date(iso).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
export function fmtRel(iso) {
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString() ? fmtTime(iso) : fmtDate(iso);
}
export function every(min) {
  return min % 1440 === 0 ? `alle ${min / 1440} Tag(e)` : min % 60 === 0 ? `alle ${min / 60} Std` : `alle ${min} Min`;
}
export function everySec(s) {
  return s < 60 ? `alle ${s} Sek` : s % 3600 === 0 ? `alle ${s / 3600} Std` : s % 60 === 0 ? `alle ${s / 60} Min` : `alle ${s} Sek`;
}

// Switch element of the design: <button class="sw on">
export const sw = (on, attrs = "") => `<button type="button" class="sw ${on ? "on" : ""}" ${attrs}></button>`;

// Swipe a row sideways: onSwipe() (e.g. dismiss), plain tap: onTap()
export function enableSwipe(el, { onSwipe, onTap }) {
  let x0 = null, dx = 0;
  el.addEventListener("pointerdown", (e) => { x0 = e.clientX; dx = 0; el.style.transition = "none"; });
  el.addEventListener("pointermove", (e) => {
    if (x0 === null) return;
    dx = e.clientX - x0;
    if (Math.abs(dx) > 8) { el.style.transform = `translateX(${dx}px)`; el.style.opacity = 1 - Math.min(Math.abs(dx) / 220, 0.8); }
  });
  const end = () => {
    if (x0 === null) return;
    el.style.transition = "";
    if (Math.abs(dx) > 90 && onSwipe) {
      el.style.transform = `translateX(${dx > 0 ? 120 : -120}%)`; el.style.opacity = 0;
      setTimeout(onSwipe, 220);
    } else {
      el.style.transform = ""; el.style.opacity = "";
      if (Math.abs(dx) < 8 && onTap) onTap();
    }
    x0 = null;
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", () => { x0 = null; el.style.transform = ""; el.style.opacity = ""; });
}

// Minimal, safe markdown for answers: code blocks, inline code, bold/italic, headings, lists, links.
export function md(src) {
  const blocks = [];
  let s = esc(src).replace(/```(\w*)\n?([\s\S]*?)```/g, (_, _l, code) => { blocks.push(code); return `\u0000${blocks.length - 1}\u0000`; });
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/^#{1,3} (.+)$/gm, "<b class=\"md-h\">$1</b>")
    .replace(/^\s*[-*] (.+)$/gm, "<span class=\"md-li\">$1</span>")
    .replace(/^\s*(\d+)\. (.+)$/gm, "<span class=\"md-li num\" data-n=\"$1\">$2</span>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<pre class="md-pre">${blocks[+i]}</pre>`);
}
