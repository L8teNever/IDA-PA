// IDA Launcher: Apps laden, suchen, öffnen. Aktualisiert sich nur, solange die Seite sichtbar ist (spart Akku).
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

let apps = [];
let timer = 0;

function letters(name) {
  const w = name.replace(/^https?:\/\//, "").split(/[\s._-]+/).filter(Boolean);
  return (w.length > 1 ? w[0][0] + w[1][0] : (w[0] || "?").slice(0, 2)).replace(/^./, (c) => c.toUpperCase());
}

function card(a, first) {
  return `
    <a class="lc-card ${first ? "first" : ""}" href="${esc(a.url)}" rel="noopener">
      <span class="lc-ico" data-l="${esc(letters(a.name))}"><img src="${esc(a.icon)}" alt="" loading="lazy" decoding="async"></span>
      <span class="lc-name" title="${esc(a.name)}">${esc(a.name)}</span>
      <span class="lc-host">${esc(a.host)}</span>
      ${a.protected ? "" : `<span class="lc-open">offen</span>`}
    </a>`;
}

function draw() {
  const q = norm($("lcSearch").value.trim());
  const list = q ? apps.filter((a) => norm(`${a.name} ${a.host}`).includes(q)) : apps;
  const mine = list.filter((a) => a.ida), rest = list.filter((a) => !a.ida);
  const sec = (title, arr, off) => (arr.length ? `<h2 class="lc-sec">${title}</h2><div class="lc-grid">${arr.map((a, i) => card(a, q && i + off === 0)).join("")}</div>` : "");
  $("lcGrid").innerHTML = sec("IDA", mine, 0) + sec(mine.length ? "Weitere Apps" : "Apps", rest, mine.length);
  // Fehlt das Symbol, bleibt der Anfangsbuchstabe
  for (const img of $("lcGrid").querySelectorAll(".lc-ico img")) {
    img.addEventListener("error", () => { const p = img.parentElement; img.remove(); p.textContent = p.dataset.l; }, { once: true });
  }
  const e = $("lcEmpty");
  e.hidden = list.length > 0;
  if (!list.length) e.textContent = apps.length ? "Nichts gefunden." : e.dataset.err || "Noch keine Apps.";
}

async function load() {
  try {
    const r = await fetch("/api/v1/apps", { credentials: "same-origin", cache: "no-store" });
    const d = await r.json();
    $("lcTitle").textContent = d.title || "Apps";
    $("lcEmpty").dataset.err = d.error ? `Fehler: ${d.error}` : "";
    apps = d.apps || [];
  } catch {
    $("lcEmpty").dataset.err = "Keine Verbindung.";
  }
  draw();
}

function schedule() {
  clearInterval(timer);
  if (!document.hidden) timer = setInterval(load, 60_000);
}

$("lcSearch").addEventListener("input", draw);
$("lcSearch").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("lcGrid").querySelector(".lc-card")?.click();
  else if (e.key === "Escape") { e.target.value = ""; draw(); }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== $("lcSearch") && !e.ctrlKey && !e.metaKey) { e.preventDefault(); $("lcSearch").focus(); }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); schedule(); });
load();
schedule();
