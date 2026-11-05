// Rahmen wie IDA Code, Kalender und Wiki: schmale Leiste links (Tools), optional Seitenleiste, schwebende Kopfzeile,
// Inhalt darunter; am Handy unten eine Leiste. Die Tools selbst liegen in js/tools/.
import { api } from "./api.js";
import { app } from "./store.js";
import { TOOLS, setTools } from "./tools/index.js";
import { $, esc } from "./ui.js";

let current = null;   // { tool, params, cleanup }
const desktop = () => matchMedia("(min-width: 961px)").matches;

export async function startApp(root) {
  root.innerHTML = `<div class="cd-shell" id="shell">
      <nav class="cd-rail" id="rail" aria-label="Navigation"></nav>
      <aside class="cd-sidebar" id="side" hidden></aside>
      <main class="cd-stage" id="stage"><div class="stage-topbar" id="topbar"></div><div class="stage-scroll" id="scroll"></div></main>
    </div>
    <nav class="cd-bottom-nav" id="bnav" aria-label="Navigation"></nav>
    <div class="drawer-scrim" id="scrim" hidden></div>`;
  $("scrim").onclick = closeDrawer;
  addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("drawer-open")) closeDrawer(); });
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-drawer]")) openDrawer();
    const a = e.target.closest("a[href]");
    if (!a || a.target || a.hasAttribute("download") || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin || u.pathname.startsWith("/api/") || u.pathname.startsWith("/s/")) return;
    e.preventDefault();
    navigate(u.pathname + u.search);
  });
  addEventListener("popstate", () => show());
  app.info = await api.get("/app").catch(() => ({ tools: [] }));
  setTools(app.info.nav);
  document.body.dataset.mode = app.info.mode || "dashboard";
  show();
}

export function navigate(url, { replace = false } = {}) {
  if (url !== location.pathname + location.search) history[replace ? "replaceState" : "pushState"](null, "", url);
  closeDrawer();
  show();
}

// nur die Adresse anpassen (z. B. Zeitraum im Diagramm), ohne neu zu zeichnen
export function setQuery(params) {
  const u = new URL(location.href);
  for (const [k, v] of Object.entries(params)) { if (v == null || v === "") u.searchParams.delete(k); else u.searchParams.set(k, v); }
  history.replaceState(null, "", u.pathname + u.search);
}

function route() {
  const path = location.pathname.replace(/\/+$/, "") || "/";
  for (const tool of TOOLS) {
    const params = tool.match(path);
    if (params) return { tool, params: { ...params, query: Object.fromEntries(new URLSearchParams(location.search)) } };
  }
  return { tool: TOOLS[0], params: { query: {} } };
}

function show() {
  const { tool, params } = route();
  const same = current?.tool === tool && JSON.stringify(current.params) === JSON.stringify(params);
  if (same) return;
  try { current?.cleanup?.(); } catch { /* egal */ }
  const name = app.info?.title || "IDA Dashboard";
  document.title = tool === TOOLS[0] ? name : `${tool.label} · ${name}`;
  drawNav(tool);
  const side = $("side");
  side.hidden = !tool.side;
  side.className = "cd-sidebar";
  side.innerHTML = "";
  const scroll = $("scroll");
  scroll.innerHTML = "";
  scroll.scrollTop = 0;
  $("topbar").innerHTML = "";
  const ctx = {
    top: (html) => { $("topbar").innerHTML = html; },
    scroll,
    navigate,
    setQuery,
    openDrawer,
  };
  const cleanups = [];
  if (tool.side) cleanups.push(tool.side(side, params, ctx));
  cleanups.push(tool.mount(scroll, params, ctx));
  current = { tool, params, cleanup: () => cleanups.forEach((f) => typeof f === "function" && f()) };
}

function drawNav(active) {
  const ida = app.info?.ida_url;
  const btn = (t) => `<a class="rail-btn ${t === active ? "active" : ""}" href="${t.path}" aria-label="${esc(t.label)}">
      <span class="rail-ic"><span class="ms">${t.icon}</span></span><span class="rail-lbl">${esc(t.label)}</span></a>`;
  $("rail").innerHTML = `<span class="rail-logo ida-mini" aria-hidden="true"><i></i><i></i></span>
    <div class="rail-group">${TOOLS.map(btn).join("")}</div>
    <div class="rail-spacer"></div>
    ${app.info?.mode === "server" && app.info?.dashboard_url ? `<a class="rail-btn" href="${esc(app.info.dashboard_url)}" target="_blank" rel="noopener"><span class="rail-ic"><span class="ms">space_dashboard</span></span><span class="rail-lbl">Dashboard</span></a>` : ""}
    ${ida ? `<a class="rail-btn" href="${esc(ida)}" target="_blank" rel="noopener"><span class="rail-ic"><span class="ms">face_5</span></span><span class="rail-lbl">IDA</span></a>` : ""}`;
  $("bnav").style.gridTemplateColumns = `repeat(${TOOLS.length}, 1fr)`;
  $("bnav").innerHTML = TOOLS.map((t) => `<a class="bnav-btn ${t === active ? "active" : ""}" href="${t.path}"><span class="ms">${t.icon}</span>${esc(t.label)}</a>`).join("");
}

export function openDrawer() {
  if (desktop() || $("side").hidden) return;
  $("side").classList.add("open");
  $("scrim").hidden = false;
  document.body.classList.add("drawer-open");
}
export function closeDrawer() {
  $("side")?.classList.remove("open");
  if ($("scrim")) $("scrim").hidden = true;
  document.body.classList.remove("drawer-open");
}
