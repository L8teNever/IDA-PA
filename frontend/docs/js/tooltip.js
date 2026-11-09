// Schnelle Tooltips: Ersetzt die trägen Browser-Hinweise (title) durch eine eigene Sprechblase nach ~150 ms.
// Gilt für alles mit title oder (bei Symbol-Knöpfen ohne Text) aria-label. Nur bei Maus/Stift, nicht bei Touch.
const DELAY = 150;
let tip, timer, cur, saved;

function text(e) {
  const t = e.getAttribute("title");
  if (t) return t;
  const a = e.getAttribute("aria-label");
  return a && e.matches("button, [role=button]") && !e.textContent.replace(/\s+/g, "").replace(/^[a-z_]+$/, "") ? a : "";
}
function hide() {
  clearTimeout(timer); timer = 0;
  if (tip) tip.classList.remove("show");
  if (cur && saved != null && !cur.hasAttribute("title")) cur.setAttribute("title", saved);
  cur = saved = null;
}
function show(e, s) {
  tip ||= Object.assign(document.createElement("div"), { className: "tip", role: "tooltip" });
  if (!tip.isConnected) document.body.append(tip);
  tip.textContent = s;
  const r = e.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
  let top = r.bottom + 8;
  if (top + h > innerHeight - 4) top = Math.max(4, r.top - h - 8);
  tip.style.left = Math.max(6, Math.min(r.left + r.width / 2 - w / 2, innerWidth - w - 6)) + "px";
  tip.style.top = top + "px";
  tip.classList.add("show");
}

export function initTooltips() {
  addEventListener("pointerover", (ev) => {
    if (ev.pointerType === "touch") return;
    const e = ev.target.closest?.("[title], button[aria-label], [role=button][aria-label]");
    if (e === cur) return;
    hide();
    if (!e) return;
    const s = text(e);
    if (!s) return;
    cur = e;
    saved = e.getAttribute("title");
    if (saved != null) e.removeAttribute("title");   // sonst zeigt der Browser seinen eigenen, späten Hinweis
    timer = setTimeout(() => { if (cur === e && e.isConnected) show(e, s); }, DELAY);
  }, true);
  for (const t of ["pointerdown", "keydown", "scroll", "blur"]) addEventListener(t, hide, true);
  addEventListener("pointerout", (ev) => { if (cur && !cur.contains(ev.relatedTarget)) hide(); }, true);
}
