// Text in einer Antwort markieren -> kleiner Knopf "Fragen" -> der Ausschnitt hängt als Zitat über dem Eingabefeld,
// die nächste Nachricht bezieht sich darauf. Gesendet wird er als Markdown-Zitat ("> ...") vor dem Text.
// (Dieselbe Datei liegt in frontend/js/ui/quote.js – Änderungen immer in beiden Kopien machen.)
import { autolink } from "./dom.js";

const MAX = 2000;
let quote = "";
let fab = null;
let wired = false;
let timer = null;

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const input = () => document.getElementById("input");

export const hasQuote = () => !!quote;

// Nachricht mit vorangestelltem Zitat (und Zitat danach leeren)
export function withQuote(text) {
  if (!quote) return text;
  const q = quote.split("\n").map((l) => `> ${l}`).join("\n");
  clearQuote();
  return `${q}\n\n${text}`;
}

// Eigene Nachricht anzeigen: führendes Zitat als Block, Rest als Text
export function userHtml(text) {
  const m = /^((?:> ?[^\n]*\n?)+)\n+([\s\S]*)$/.exec(text || "");
  if (!m) return autolink(esc(text)); // Links in deinen Nachrichten anklickbar
  const q = m[1].split("\n").filter((l) => l.startsWith(">")).map((l) => l.replace(/^> ?/, "")).join("\n");
  return `<span class="msg-quote"><span class="ms">format_quote</span><span>${autolink(esc(q))}</span></span>${autolink(esc(m[2]))}`;
}

function drawChip() {
  const chip = document.getElementById("quoteChip");
  if (!chip) return;
  chip.hidden = !quote;
  chip.innerHTML = quote
    ? `<span class="ms">format_quote</span><span class="qc-text">${esc(quote)}</span><button type="button" class="icon-btn sm" data-quote-x title="Zitat entfernen" aria-label="Zitat entfernen"><span class="ms">close</span></button>`
    : "";
  chip.querySelector("[data-quote-x]")?.addEventListener("click", () => { clearQuote(); input()?.focus(); });
}

export function clearQuote() { quote = ""; drawChip(); document.getElementById("input")?.dispatchEvent(new Event("input")); }

function setQuote(text) {
  quote = text.trim().slice(0, MAX);
  drawChip();
  const i = input();
  if (i) { i.focus(); i.dispatchEvent(new Event("input")); }
}

// Markierter Text, wenn er ganz in einer Antwort liegt
function selected() {
  const s = getSelection();
  if (!s || s.isCollapsed || !s.rangeCount) return null;
  const r = s.getRangeAt(0);
  const el = (r.commonAncestorContainer.nodeType === 1 ? r.commonAncestorContainer : r.commonAncestorContainer.parentElement)?.closest(".msg.bot");
  const text = s.toString().trim();
  return el && text ? { text, rect: lastRect(r) } : null;
}
function lastRect(r) {
  const rs = r.getClientRects();
  return rs.length ? rs[rs.length - 1] : r.getBoundingClientRect();
}

function place(rect) {
  const w = fab.offsetWidth, h = fab.offsetHeight;
  const above = rect.top > h + 16;
  fab.style.left = `${Math.max(8, Math.min(rect.right - w / 2, innerWidth - w - 8))}px`;
  fab.style.top = `${above ? rect.top - h - 8 : Math.min(rect.bottom + 8, innerHeight - h - 8)}px`;
}

function update() {
  const s = selected();
  if (!s) { fab.hidden = true; return; }
  fab.hidden = false;
  place(s.rect);
}

// ---- Lange auf eine Nachricht drücken (wie bei WhatsApp): sie tritt hervor, darunter stehen Antworten, Kopieren und Auswählen ----
const HOLD_MS = 420;   // kürzer als das Markieren des Systems, das sonst zuerst käme
let holdTimer = null;
let holdStart = null;
let held = null;

function messageText(m) {
  const blocks = [...m.querySelectorAll(":scope > .md-text, :scope > .md")].map((b) => b.innerText.trim()).filter(Boolean);
  if (blocks.length) return blocks.join("\n\n");
  const c = m.cloneNode(true);
  c.querySelectorAll(".msg-quote, .msg-meta, .toolcall, .ask, .shown, .approval, .route-rate").forEach((x) => x.remove());
  return (c.textContent || "").trim();
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* unten der alte Weg */ }
  const t = document.createElement("textarea");
  t.value = text;
  t.style.cssText = "position:fixed;opacity:0";
  document.body.appendChild(t);
  t.select();
  const ok = document.execCommand("copy");
  t.remove();
  return ok;
}

function closeHold() {
  held?.remove();
  held = null;
  removeEventListener("keydown", holdKey);
  removeEventListener("resize", closeHold);
}
function holdKey(e) { if (e.key === "Escape") closeHold(); }

// Lange Antworten mit vielen Befehlen: in der Vorschau stehen nur Text und ein Hinweis, wie viele Befehle es waren
function previewOf(msg) {
  const twin = msg.cloneNode(true);
  const tools = twin.querySelectorAll(".toolcall, .ask").length;
  twin.querySelectorAll("iframe, .toolcall, .ask, .approval, .route-rate").forEach((x) => x.remove());
  if (tools) {
    const n = document.createElement("div");
    n.className = "hold-tools";
    n.innerHTML = `<span class="ms">terminal</span>${tools} ${tools === 1 ? "Befehl" : "Befehle"} genutzt`;
    twin.prepend(n);
  }
  return twin;
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));

function openHold(msg) {
  const text = messageText(msg);
  if (!text) return;
  closeHold();
  navigator.vibrate?.(12);
  getSelection()?.removeAllRanges();
  const me = msg.classList.contains("me");
  const r = msg.getBoundingClientRect();
  held = document.createElement("div");
  held.className = "hold";
  held.innerHTML = `<div class="hold-copy"></div><div class="hold-menu" role="menu">
    <button type="button" role="menuitem" data-hold="reply"><span class="ms">reply</span>Antworten</button>
    <button type="button" role="menuitem" data-hold="copy"><span class="ms">content_copy</span>Kopieren</button>
    <button type="button" role="menuitem" data-hold="select"><span class="ms">select_all</span>Auswählen</button></div>`;
  const copy = held.firstElementChild, menu = held.lastElementChild;
  const line = document.createElement("div");
  line.className = `row ${me ? "me" : "bot"}`;
  line.appendChild(previewOf(msg));
  copy.appendChild(line);
  document.body.appendChild(held);

  // Bildschirm messen (sichtbarer Bereich, also ohne Tastatur), Vorschau und Menü darin unterbringen
  const vw = visualViewport?.width || innerWidth, vh = visualViewport?.height || innerHeight;
  const pad = 12, gap = 10;
  const w = Math.min(r.width, vw - 2 * pad);
  const left = clamp(r.left, pad, vw - pad - w);
  copy.style.left = `${left}px`;
  copy.style.width = `${w}px`;
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  const room = vh - 2 * pad - gap - mh;   // so hoch darf die Vorschau höchstens werden
  let menuTop;
  if (room < 72) {
    // sehr flach (Handy quer mit Tastatur): nur das Menü, mittig
    copy.hidden = true;
    menuTop = (vh - mh) / 2;
    menu.style.left = `${(vw - mw) / 2}px`;
  } else {
    const natural = copy.scrollHeight;
    const h = Math.min(natural, room);
    if (natural > room) copy.classList.add("cut");   // lange Nachricht: unten ausgeblendet statt hart abgeschnitten
    // die Nachricht bleibt möglichst, wo sie war
    const top = clamp(r.top, pad, vh - pad - gap - mh - h);
    copy.style.top = `${top}px`;
    copy.style.height = `${h}px`;
    menuTop = top + h + gap;
    const ml = me ? left + w - mw : left;
    menu.style.left = `${clamp(ml, pad, vw - pad - mw)}px`;
  }
  menu.style.top = `${Math.max(pad, menuTop)}px`;
  requestAnimationFrame(() => held?.classList.add("show"));
  held.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-hold]");
    if (!b) return closeHold();
    if (b.dataset.hold === "reply") { closeHold(); setQuote(text); return; }
    if (b.dataset.hold === "select") { closeHold(); getSelection()?.selectAllChildren(msg); return; }
    const ok = await copyText(text);
    b.innerHTML = `<span class="ms">${ok ? "check" : "error"}</span>${ok ? "Kopiert" : "Ging nicht"}`;
    setTimeout(closeHold, 550);
  });
  addEventListener("keydown", holdKey);
  addEventListener("resize", closeHold, { once: true });   // Drehen des Geräts: neu öffnen statt schief stehen lassen
}

function cancelHold() { clearTimeout(holdTimer); holdTimer = null; holdStart = null; }

function bindHold() {
  document.addEventListener("pointerdown", (e) => {
    cancelHold();
    if (e.button || held) return;
    const msg = e.target.closest?.(".msg.me, .msg.bot");
    if (!msg || e.target.closest("a, button, summary, input, textarea, select, iframe, pre.out")) return;
    holdStart = { x: e.clientX, y: e.clientY };
    holdTimer = setTimeout(() => { holdTimer = null; holdStart = null; openHold(msg); }, HOLD_MS);
  });
  document.addEventListener("pointermove", (e) => {
    if (holdStart && Math.hypot(e.clientX - holdStart.x, e.clientY - holdStart.y) > 10) cancelHold();
  });
  for (const ev of ["pointerup", "pointercancel"]) document.addEventListener(ev, cancelHold);
  addEventListener("scroll", cancelHold, true);
  // das Kontextmenü des Handys soll beim Halten nicht dazwischenfunken
  document.addEventListener("contextmenu", (e) => { if ((holdTimer || held) && e.target.closest?.(".msg, .hold")) e.preventDefault(); });
}

export function initQuote() {
  // Zitat-Feld über dem Eingabefeld (wird bei jedem Neuaufbau der Eingabe neu angelegt)
  quote = "";
  document.getElementById("quoteChip")?.remove();
  const form = document.getElementById("composer");
  if (form) {
    const chip = document.createElement("div");
    chip.id = "quoteChip";
    chip.className = "quote-chip";
    chip.hidden = true;
    form.insertBefore(chip, form.firstChild);
  }
  if (wired) return;
  wired = true;
  fab = document.createElement("button");
  fab.type = "button";
  fab.className = "quote-fab";
  fab.hidden = true;
  fab.innerHTML = `<span class="ms">format_quote</span>Fragen`;
  document.body.appendChild(fab);
  // die Markierung soll beim Klicken bleiben
  fab.addEventListener("pointerdown", (e) => e.preventDefault());
  fab.addEventListener("mousedown", (e) => e.preventDefault());
  fab.addEventListener("click", () => {
    const s = selected();
    fab.hidden = true;
    if (s) { setQuote(s.text); getSelection()?.removeAllRanges(); }
  });
  document.addEventListener("selectionchange", () => { clearTimeout(timer); timer = setTimeout(update, 120); });
  addEventListener("scroll", () => { if (!fab.hidden) update(); }, true);
  addEventListener("resize", () => { if (!fab.hidden) update(); });
  bindHold();
}
