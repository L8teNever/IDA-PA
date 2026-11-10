// IDA Launcher: Apps laden, suchen, öffnen. Aktualisiert sich nur, solange die Seite sichtbar ist (spart Akku).
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

let apps = [];
let timer = 0;
let again = 0;

function letters(name) {
  const w = name.replace(/^https?:\/\//, "").split(/[\s._-]+/).filter(Boolean);
  return (w.length > 1 ? w[0][0] + w[1][0] : (w[0] || "?").slice(0, 2)).replace(/^./, (c) => c.toUpperCase());
}

function card(a, first) {
  return `
    <a class="lc-card ${first ? "first" : ""}" href="${esc(a.url)}" data-host="${esc(a.host)}" rel="noopener">
      ${a.up === null || a.up === undefined ? "" : `<span class="lc-dot ${a.up ? "up" : "down"}" title="${a.up ? "erreichbar" : "nicht erreichbar"}"></span>`}
      <span class="lc-ico" data-l="${esc(letters(a.name))}"><img src="${esc(a.icon)}" alt="" loading="lazy" decoding="async"></span>
      <span class="lc-name" title="${esc(a.name)}">${esc(a.name)}</span>
      <span class="lc-host">${esc(a.host)}</span>
      ${a.protected ? "" : `<span class="lc-open">offen</span>`}
    </a>`;
}

const SHOW = 6; // so viele Apps je Bereich auf der Startansicht

function draw() {
  const q = norm($("lcSearch").value.trim());
  const all = location.hash === "#alle";
  const e = $("lcEmpty");
  let html = "";
  const sec = (title, arr, extra = "") => (arr.length ? `<h2 class="lc-sec">${title}${extra}</h2><div class="lc-grid">${arr.map((a) => card(a, q && a === first)).join("")}</div>` : "");
  let first = null;
  if (q || all) {
    const list = q ? apps.filter((a) => norm(`${a.name} ${a.host}`).includes(q)) : apps;
    const mine = list.filter((a) => a.ida), rest = list.filter((a) => !a.ida);
    first = mine[0] || rest[0];
    html = (all && !q ? `<button type="button" class="lc-back" id="lcBack"><span class="ms">arrow_back</span> Zurück</button>` : "")
      + sec("IDA", mine) + sec(mine.length ? "Weitere Apps" : "Apps", rest);
    e.hidden = list.length > 0;
    if (!list.length) e.textContent = apps.length ? "Nichts gefunden." : e.dataset.err || "Noch keine Apps.";
  } else {
    // Startansicht: IDA-Apps, dann was du oft und zuletzt benutzt – alles andere über Suche oder „Alle Apps“
    const rest = apps.filter((a) => !a.ida);
    const often = rest.filter((a) => a.n >= 2).sort((x, y) => y.n - x.n || y.last - x.last).slice(0, SHOW);
    const recent = rest.filter((a) => a.last && !often.includes(a)).sort((x, y) => y.last - x.last).slice(0, SHOW);
    html = sec("IDA", apps.filter((a) => a.ida)) + sec("Oft genutzt", often) + sec("Zuletzt genutzt", recent);
    if (rest.length) {
      html += `<div class="lc-all"><button type="button" class="lc-allbtn" id="lcAll"><span class="ms">apps</span> Alle Apps <small>${rest.length}</small></button>
        ${often.length || recent.length ? "" : `<p>Hier erscheinen bald die Apps, die du oft oder zuletzt geöffnet hast. Den Rest findest du über die Suche.</p>`}</div>`;
    }
    e.hidden = apps.length > 0;
    if (!apps.length) e.textContent = e.dataset.err || "Noch keine Apps.";
  }
  $("lcGrid").innerHTML = html;
  // Fehlt das Symbol, bleibt der Anfangsbuchstabe
  for (const img of $("lcGrid").querySelectorAll(".lc-ico img")) {
    img.addEventListener("error", () => { const p = img.parentElement; img.remove(); p.textContent = p.dataset.l; }, { once: true });
  }
}

// Öffnen merken (für „Oft“ und „Zuletzt“); sendBeacon läuft auch beim Seitenwechsel noch durch
function noteVisit(link) {
  const host = link.dataset.host;
  const a = apps.find((x) => x.host === host);
  if (a) { a.n += 1; a.last = Math.floor(Date.now() / 1000); }
  navigator.sendBeacon?.("/api/v1/visit", new Blob([JSON.stringify({ host })], { type: "application/json" }));
}

$("lcGrid").addEventListener("click", (e) => {
  if (e.target.closest("#lcAll")) { location.hash = "alle"; return; }
  if (e.target.closest("#lcBack")) { location.hash = ""; history.replaceState(null, "", location.pathname); draw(); return; }
  const link = e.target.closest(".lc-card");
  if (link) noteVisit(link);
});
$("lcGrid").addEventListener("auxclick", (e) => { const l = e.target.closest(".lc-card"); if (l && e.button === 1) noteVisit(l); });
window.addEventListener("hashchange", () => { draw(); scrollTo(0, 0); });

async function load() {
  try {
    const r = await fetch("/api/v1/apps", { credentials: "same-origin", cache: "no-store" });
    const d = await r.json();
    $("lcTitle").textContent = d.title || "Apps";
    $("lcEmpty").dataset.err = d.error ? `Fehler: ${d.error}` : "";
    apps = d.apps || [];
    clearTimeout(again);
    if (d.pending) again = setTimeout(load, 4000); // Titel und Status werden noch im Hintergrund geholt
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
  if (e.key === "Enter") { const c = $("lcGrid").querySelector(".lc-card"); if (c) { noteVisit(c); c.click(); } }
  else if (e.key === "Escape") { e.target.value = ""; draw(); }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== $("lcSearch") && !e.ctrlKey && !e.metaKey) { e.preventDefault(); $("lcSearch").focus(); }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); schedule(); });
load();
schedule();

// ── Hintergrundbilder ──────────────────────────────────────────────
let bgs = [];

function showBg() {
  const el = $("lcBg");
  if (!bgs.length) { el.classList.remove("on"); el.style.backgroundImage = ""; return; }
  // zufällig, aber nicht zweimal hintereinander dasselbe (wenn es mehrere gibt)
  const last = localStorage.getItem("lcBgLast");
  const pool = bgs.length > 1 ? bgs.filter((b) => b.id !== last) : bgs;
  const pick = pool[Math.floor(Math.random() * pool.length)];
  localStorage.setItem("lcBgLast", pick.id);
  const img = new Image();
  img.onload = () => { el.style.backgroundImage = `url("${pick.url}")`; el.classList.add("on"); };
  img.src = pick.url;
}

async function loadBgs() {
  try { bgs = (await (await fetch("/api/v1/backgrounds", { cache: "no-store" })).json()).items || []; } catch { bgs = []; }
}

function drawThumbs() {
  $("lcThumbs").innerHTML = bgs.map((b) => `<div class="lc-thumb"><img src="${esc(b.url)}" alt="" loading="lazy"><button type="button" data-id="${esc(b.id)}" title="Löschen" aria-label="Löschen"><span class="ms">delete</span></button></div>`).join("");
}

// Vor dem Hochladen auf max. 2400 px verkleinern und als JPEG speichern (spart Platz und Ladezeit); GIFs bleiben unverändert
async function shrink(file) {
  if (file.type === "image/gif" || file.size < 600 * 1024) return file;
  try {
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, 2400 / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.86));
    return blob && blob.size < file.size ? blob : file;
  } catch { return file; }
}

$("lcBgBtn").addEventListener("click", () => { drawThumbs(); $("lcDlgMsg").textContent = bgs.length ? "" : "Noch keine Bilder."; $("lcDlg").showModal(); });
$("lcDlgClose").addEventListener("click", () => $("lcDlg").close());
$("lcDlg").addEventListener("click", (e) => { if (e.target === $("lcDlg")) $("lcDlg").close(); });
$("lcDlg").addEventListener("close", showBg);

$("lcThumbs").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-id]");
  if (!b) return;
  const r = await fetch(`/api/v1/background/${encodeURIComponent(b.dataset.id)}`, { method: "DELETE" });
  if (r.ok) { bgs = bgs.filter((x) => x.id !== b.dataset.id); drawThumbs(); $("lcDlgMsg").textContent = bgs.length ? "" : "Noch keine Bilder."; }
});

$("lcFile").addEventListener("change", async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  const msg = $("lcDlgMsg");
  let ok = 0, err = "";
  for (const [i, f] of files.entries()) {
    msg.textContent = `Lade hoch … ${i + 1}/${files.length}`;
    try {
      const body = await shrink(f);
      const r = await fetch("/api/v1/backgrounds", { method: "POST", body, headers: { "content-type": "application/octet-stream" } });
      if (r.ok) ok++; else err = (await r.json().catch(() => ({}))).detail || "Hochladen fehlgeschlagen.";
    } catch { err = "Keine Verbindung."; }
  }
  await loadBgs();
  drawThumbs();
  msg.textContent = err ? `${ok} hochgeladen. ${err}` : `${ok} hochgeladen.`;
});

loadBgs().then(showBg);

// ── Uhr ────────────────────────────────────────────────────────────
const fmtTime = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
const fmtDate = new Intl.DateTimeFormat("de-DE", { weekday: "long", day: "numeric", month: "long", timeZone: "Europe/Berlin" });
let clockT = 0;
function tick() {
  const d = new Date();
  $("lcTime").textContent = fmtTime.format(d);
  $("lcDate").textContent = fmtDate.format(d);
  clearTimeout(clockT);
  if (!document.hidden) clockT = setTimeout(tick, 60_000 - (d.getSeconds() * 1000 + d.getMilliseconds()) + 50); // genau zur nächsten Minute
}
document.addEventListener("visibilitychange", tick);
tick();
