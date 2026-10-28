// App frame: header with agent switcher, side drawer, which area of <main> is visible.
import { api } from "../api.js";
import { navigate } from "../router.js";
import { agents, colors, mainAgent, onChange, state } from "../store.js";
import { $, closeSheet, esc } from "./dom.js";

// "home" (main agent: infos + today) | "agent" (start page of another agent) | "page" | "chat"
export function setView(mode, { agentStage } = {}) {
  const b = document.body;
  b.classList.remove("chatting", "on-page", "view-timeline", "show-mini", "show-bell", "tap-view");
  const code = mode === "chat" && agentStage === "code"; // thread of the code area: own panes instead of the header
  b.classList.toggle("code-thread", code);
  if (!code) { $("codeLeft").innerHTML = ""; $("codeRight").innerHTML = ""; }
  closeDrawers();
  closeSheet();
  $("agentWrap").classList.remove("open");
  b.classList.remove("model-open", "slash-open");
  const stage = mode === "chat" ? agentStage : mode;
  $("home").hidden = stage !== "home";
  $("agentHome").hidden = stage !== "agent";
  $("page").hidden = mode !== "page";
  $("page").classList.remove("page-wide");
  if (stage === "home") b.classList.add("view-timeline");
  if (mode === "page") b.classList.add("on-page");
  if (mode === "chat") b.classList.add("chatting");
  else $("messages").innerHTML = "";
  if (mode === "page") $("page").scrollTop = 0;
}

export function setAgent(a) {
  if (!a) return;
  state.agent = a;
  localStorage.setItem("agent", a.id);
  for (const [k, v] of Object.entries(colors(a))) document.documentElement.style.setProperty(k, v);
  $("agentName").textContent = a.name;
  $("agentDot").textContent = a.name[0];
  $("input").placeholder = `Schreib ${a.name} was…`;
  $("sideAgent").textContent = a.name;
  renderMenu();
}

function renderMenu() {
  $("agentMenu").innerHTML = agents().filter((a) => a.enabled).map((a) => {
    const c = colors(a);
    return `<button role="menuitem" data-id="${a.id}" class="${a.id === state.agent?.id ? "sel" : ""}">
      <span class="mdot" style="background:${c["--accent"]};color:${c["--on-accent"]}">${esc(a.name[0])}</span>
      <span class="meta">${esc(a.name)}<small>${esc(a.description)}</small></span>
      <span class="ms check">check</span></button>`;
  }).join("");
}

export const agentHome = (a) => (a.is_main ? "/" : `/a/${a.id}`);

export function openDrawer() {
  closeDrawers();
  const p = location.pathname;
  document.querySelectorAll("#sideNav [data-nav]").forEach((x) => {
    const n = x.dataset.nav;
    x.classList.toggle("on", n === "home" ? p === "/" : p.startsWith("/" + n));
  });
  document.body.classList.add("drawer-menu");
}
export function closeDrawers() { document.body.classList.remove("drawer-chats", "drawer-routinen", "drawer-menu"); }

export function initShell() {
  $("btnMenu").onclick = openDrawer;
  $("scrim").onclick = closeDrawers;
  $("sideNav").addEventListener("click", (e) => { if (e.target.closest("a")) closeDrawers(); });
  $("btnHome").onclick = () => navigate("/");
  $("agentBtn").onclick = (e) => { e.stopPropagation(); $("agentWrap").classList.toggle("open"); };
  $("agentMenu").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $("agentWrap").classList.remove("open");
    const a = agents().find((x) => x.id === b.dataset.id);
    navigate(agentHome(a || mainAgent()));
  };
  document.addEventListener("click", (e) => { if (!$("agentWrap").contains(e.target)) $("agentWrap").classList.remove("open"); });
  addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawers(); closeSheet(); $("agentWrap").classList.remove("open"); } });
  $("evScrim").onclick = closeSheet;
  onChange(() => {
    renderMenu();
    const n = state.boot?.unread || 0;
    $("navBadge").hidden = !n;
    $("navBadge").textContent = n;
    const code = document.querySelector('#sideNav [data-nav="code"]');
    if (code) code.hidden = !state.boot?.code_enabled; // the code area is off until switched on in the settings
  });
}
