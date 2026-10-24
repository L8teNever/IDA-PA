// Einstellungen → MCP-Server (list, add sheet, detail page with tools and agent access).
import { api } from "../../api.js";
import { navigate } from "../../router.js";
import { colors, state } from "../../store.js";
import { $, attempt, closeSheet, esc, fmtDateTime, openSheet, sw, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

const BASE = "/einstellungen/mcp";
const statusOf = (m) => (!m.enabled ? ["#45464f", "Aus"] : m.status === "error" ? ["#ff8f8f", "Nicht erreichbar"] : m.status === "new" ? ["#ffc85a", "Neu"] : ["#7ee2b8", `${m.tools.length} Tools`]);
const names = (ids) => ids.map((i) => state.boot.agents.find((a) => a.id === i)).filter(Boolean)
  .map((a) => `<span style="color:${colors(a)["--accent"]}">${esc(a.name)}</span>`).join(" · ");

export async function render(el, id) {
  if (id) return detail(el, id);
  const list = await api.get("/connectors");
  el.innerHTML = subHead("MCP-Server", "/einstellungen") + `
    <p class="sub-intro">Verbundene MCP-Server (Streamable HTTP) und welche Agents sie benutzen dürfen.</p>
    <button class="add-btn" id="newMcp"><span class="ms">add</span>MCP hinzufügen</button>
    <div class="slist">${list.map((m) => {
      const [col, txt] = statusOf(m);
      return `<a class="srow" href="${BASE}/${m.id}"><span class="sdot" style="background:${col}"></span>
        <span class="smain"><b>${esc(m.name)}</b><small><span style="color:${m.status === "error" && m.enabled ? "#ff8f8f" : "inherit"}">${esc(txt)}</span>${m.agents.length ? " · " + names(m.agents) : ""}</small></span>
        <span class="ms schev">chevron_right</span></a>`;
    }).join("") || `<div class="empty">Noch keine Server.</div>`}</div>`;
  $("newMcp").onclick = () => addSheet(el);
}

function addSheet(el) {
  const s = { name: "", url: "", token: "", agents: [state.boot.agents.find((a) => a.is_main)?.id].filter(Boolean) };
  const draw = () => {
    openSheet(`<h2 class="sh-title">MCP hinzufügen</h2>
      <label class="fld"><span>Name (optional)</span><input id="mName" value="${esc(s.name)}" placeholder="Mein Server"></label>
      <label class="fld"><span>Server-URL – gern mit Token (…?token=…), er wird in der Liste versteckt</span><input id="mUrl" value="${esc(s.url)}" placeholder="https://mcp.beispiel.de/mcp" inputmode="url"></label>
      <label class="fld"><span>Bearer-Token separat (optional)</span><input id="mToken" type="password" value="${esc(s.token)}"></label>
      <div class="flabel">Welche Agents dürfen ihn benutzen?</div>
      <div class="chipset" id="mAgents">${state.boot.agents.map((a) => `<button data-a="${a.id}" class="${s.agents.includes(a.id) ? "on" : ""}">${esc(a.name)}</button>`).join("")}</div>
      <div id="mErr" class="ferr"></div>
      <div class="wiz-nav"><button class="es-btn" id="mCancel">Abbrechen</button><button class="es-btn primary" id="mAdd"><span class="ms">add</span>Hinzufügen</button></div>`);
    const keep = () => { s.name = $("mName").value; s.url = $("mUrl").value; s.token = $("mToken").value; };
    $("mAgents").onclick = (e) => { const b = e.target.closest("[data-a]"); if (!b) return; keep(); const i = b.dataset.a; s.agents = s.agents.includes(i) ? s.agents.filter((x) => x !== i) : [...s.agents, i]; draw(); };
    $("mCancel").onclick = closeSheet;
    $("mAdd").onclick = async () => {
      keep();
      $("mAdd").disabled = true;
      try {
        const m = await api.post("/connectors", { name: s.name, url: s.url, token: s.token || null, agents: s.agents });
        closeSheet();
        toast(m.status === "ok" ? `${m.name}: ${m.tools.length} Tools geladen` : `${m.name} hinzugefügt – ${m.status_text}`);
        navigate(`${BASE}/${m.id}`);
      } catch (err) { $("mErr").textContent = err.message; $("mAdd").disabled = false; }
    };
  };
  draw();
}

async function detail(el, id) {
  const m = await api.get(`/connectors/${id}`);
  const [col, txt] = statusOf(m);
  el.innerHTML = subHead(m.name, BASE) + `
    <div class="pcard">
      <div class="kv"><span>Status</span><b><span class="sdot inline" style="background:${col}"></span>${esc(txt)}${m.status === "error" ? " – " + esc(m.status_text) : ""}</b></div>
      <div class="kv"><span>URL</span><b class="mono">${esc(m.url)}${m.auth ? " 🔒" : ""}</b></div>
      <div class="kv"><span>Geprüft</span><b>${m.checked_at ? fmtDateTime(m.checked_at) : "–"}</b></div>
    </div>
    <div class="slist toggles"><div class="srow"><span class="smain"><b>Aktiv</b><small>Aus = kein Agent kann ihn benutzen</small></span>${sw(m.enabled, 'id="mEnabled"')}</div></div>
    <div class="btn-row"><button class="es-btn" id="mRefresh"><span class="ms">refresh</span>Tools neu laden</button><button class="es-btn danger" id="mDel"><span class="ms">delete</span>Entfernen</button></div>
    <div class="grp">Agents mit Zugriff</div>
    <div class="chipset" id="mAgents">${state.boot.agents.map((a) => `<button data-a="${a.id}" class="${m.agents.includes(a.id) ? "on" : ""}">${esc(a.name)}</button>`).join("")}</div>
    <p class="sub-intro" style="margin-top:8px">Welche Tools ein Agent davon nutzen darf, stellst du beim Agent ein.</p>
    <div class="grp">Tools (${m.tools.length}) – ausgeschaltete kann niemand nutzen</div>
    <div class="slist">${m.tools.map((t) => `<div class="srow"><span class="smain"><b class="mono">${esc(t)}</b></span>${sw(!m.disabled_tools.includes(t), `data-tool="${esc(t)}"`)}</div>`).join("") || `<div class="empty">Keine Tool-Liste – „Tools neu laden“.</div>`}</div>`;
  $("mEnabled").onclick = async () => { await attempt(() => api.patch(`/connectors/${id}`, { enabled: !m.enabled })); detail(el, id); };
  $("mRefresh").onclick = async () => { const r = await attempt(() => api.post(`/connectors/${id}/refresh`)); if (r) toast(r.status === "ok" ? r.status_text : "Fehler: " + r.status_text); detail(el, id); };
  $("mDel").onclick = async () => { if (confirm(`${m.name} entfernen?`) && (await attempt(() => api.del(`/connectors/${id}`), "Entfernt"))) navigate(BASE); };
  $("mAgents").onclick = async (e) => {
    const b = e.target.closest("[data-a]");
    if (!b) return;
    const on = !m.agents.includes(b.dataset.a);
    if (await attempt(() => api.put(`/agents/${b.dataset.a}/connectors/${id}`, { allowed: on, tools: null }))) detail(el, id);
  };
  el.querySelectorAll("[data-tool]").forEach((b) => (b.onclick = async () => {
    const t = b.dataset.tool;
    const disabled = m.disabled_tools.includes(t) ? m.disabled_tools.filter((x) => x !== t) : [...m.disabled_tools, t];
    if (await attempt(() => api.patch(`/connectors/${id}`, { disabled_tools: disabled }))) { m.disabled_tools = disabled; b.classList.toggle("on"); }
  }));
}
