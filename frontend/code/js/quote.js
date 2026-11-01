// Text in einer Antwort markieren -> kleiner Knopf "Fragen" -> der Ausschnitt hängt als Zitat über dem Eingabefeld,
// die nächste Nachricht bezieht sich darauf. Gesendet wird er als Markdown-Zitat ("> ...") vor dem Text.
// (Dieselbe Datei liegt in frontend/js/ui/quote.js – Änderungen immer in beiden Kopien machen.)
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
  if (!m) return esc(text);
  const q = m[1].split("\n").filter((l) => l.startsWith(">")).map((l) => l.replace(/^> ?/, "")).join("\n");
  return `<span class="msg-quote"><span class="ms">format_quote</span><span>${esc(q)}</span></span>${esc(m[2])}`;
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
}
