// Graph (Dashboard): Stacks, Container, öffentliche Adressen und Zero-Trust-Apps als Netz – wie der Wissensgraph im Wiki.
// Filter je Art, Suche, Tippen auf einen Punkt zeigt Details (Stack öffnen, Adresse öffnen, wer verbunden ist).
import { api } from "../api.js";
import { mountGraph } from "../graph.js";
import { esc } from "../ui.js";

const KINDS = {
  stack: { label: "Stacks", icon: "deployed_code", color: "#7fd3ff" },
  container: { label: "Container", icon: "widgets", color: "#81c995" },
  domain: { label: "Adressen", icon: "public", color: "#fbbc6e" },
  access: { label: "Zero Trust", icon: "lock", color: "#c3b9f5" },
  tunnel: { label: "Tunnel", icon: "cable", color: "#f28fad" },
};
const COLORS = { protected: "#78d9c7", public: "#fbbc6e", unknown: "#9aa0a6", err: "#f28b82", off: "#8e9099" };
const ONE = { stack: "Stack", container: "Container", domain: "Adresse", access: "Zero-Trust-App", tunnel: "Tunnel" };
const STATE_TXT = { running: "Läuft", partial: "Teilweise", problem: "Problem", stopped: "Aus", exited: "Beendet", created: "Erstellt", restarting: "Startet neu", paused: "Pausiert", dead: "Tot" };

// Aussehen je Punkt: Farbe, Größe, gestrichelt
function look(n) {
  if (n.kind === "stack") {
    const off = n.state === "stopped" || n.sub === "aus";
    return { color: n.state === "problem" ? COLORS.err : off ? COLORS.off : KINDS.stack.color, size: 11, dashed: off };
  }
  if (n.kind === "container") {
    const on = n.state === "running";
    return { color: !on ? COLORS.off : n.health === "unhealthy" ? COLORS.err : KINDS.container.color, size: 6, dashed: !on };
  }
  if (n.kind === "domain") return { color: COLORS[n.access === "access" ? "protected" : n.access] || COLORS.unknown, size: 5.5, dashed: !!n.orphan };
  if (n.kind === "access") return { color: KINDS.access.color, size: 8.5, dashed: !!n.bypass };
  return { color: KINDS.tunnel.color, size: 9.5 };
}

export default {
  id: "graph", label: "Graph", icon: "hub", path: "/graph",
  match: (p) => (p === "/graph" ? {} : null),
  mount(el, params, ctx) {
    const hidden = new Set((params.query.aus ?? "tunnel").split(",").filter(Boolean));
    let data = null, g = null, q = params.query.q || "", alive = true;
    ctx.top(`<div class="topbar-left"><h2><span class="ms">hub</span><span class="tb-title">Graph</span></h2></div>
      <div class="topbar-actions"><span class="tb-sub" id="grCount"></span>
      <button type="button" class="icon-btn" id="grFit" aria-label="Einpassen" title="Alles ins Bild"><span class="ms">fit_screen</span></button>
      <button type="button" class="icon-btn" id="grReload" aria-label="Neu laden" title="Neu laden"><span class="ms">refresh</span></button></div>`);
    el.innerHTML = `<div class="gr-page">
      <div class="gr-bar">
        <div class="seg-mini gr-chips" id="grChips"></div>
        <label class="side-search gr-find"><span class="ms">search</span><input id="grQ" type="search" placeholder="Stack, Container oder Adresse" autocomplete="off" value="${esc(q)}"></label>
      </div>
      <div class="gr-wrap"><canvas class="gr-canvas" id="grCanvas"></canvas>
        <div class="gr-empty" id="grEmpty">Lade …</div>
        <div class="gr-legend" id="grLegend"></div>
        <div class="gr-card" id="grCard" hidden></div>
      </div></div>`;
    const $ = (id) => el.querySelector(`#${id}`);

    function chips() {
      $("grChips").innerHTML = Object.entries(KINDS).map(([k, v]) => `<button type="button" class="${hidden.has(k) ? "" : "on"}" data-kind="${k}">
        <i class="gr-dot" style="background:${v.color}"></i>${v.label}${data?.counts?.[k] ? ` <span class="muted">${data.counts[k]}</span>` : ""}</button>`).join("");
    }
    $("grLegend").innerHTML = [
      ["#7fd3ff", "Stack"], ["#81c995", "Container läuft"], [COLORS.off, "aus", true], [COLORS.err, "Problem"],
      [COLORS.protected, "Adresse mit Zero Trust"], [COLORS.public, "Adresse öffentlich"], ["#c3b9f5", "Zero-Trust-App"], ["#f28fad", "Tunnel"],
    ].map(([c, t, d]) => `<span><i style="${d ? `border:1.5px dashed ${c}` : `background:${c}`}"></i>${t}</span>`).join("");

    function build() {
      g?.destroy();
      g = null;
      $("grCard").hidden = true;
      if (!data) return;
      const nodes = data.nodes.filter((n) => !hidden.has(n.kind)).map((n) => ({ ...n, ...look(n) }));
      const ids = new Set(nodes.map((n) => n.id));
      const edges = data.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ ...e, embed: e.kind === "tunnel" || e.kind === "path" }));
      $("grCount").textContent = `${nodes.length} Punkte · ${edges.length} Verbindungen`;
      $("grEmpty").hidden = !!nodes.length;
      $("grEmpty").textContent = data.error && !data.nodes.length ? data.error : "Nichts zu zeigen – Filter oben einschalten.";
      if (!nodes.length) return;
      g = mountGraph($("grCanvas"), { nodes, edges }, { highlightQuery: q, onSelectNode: (n) => card(n, edges, nodes) });
    }

    function card(n, edges, nodes) {
      const byId = new Map(nodes.map((x) => [x.id, x]));
      const near = edges.filter((e) => e.source === n.id || e.target === n.id).map((e) => byId.get(e.source === n.id ? e.target : e.source)).filter(Boolean);
      const k = KINDS[n.kind];
      const rows = [];
      if (n.kind === "stack" || n.kind === "container") rows.push(`<div><b>Zustand</b><span>${esc(STATE_TXT[n.state] || n.state || "?")}${n.health === "unhealthy" ? " · unhealthy" : ""}</span></div>`);
      if (n.sub) rows.push(`<div><b>${n.kind === "access" ? "Policies" : "Info"}</b><span class="${n.kind === "container" ? "mono" : ""}">${esc(n.sub)}</span></div>`);
      if (n.kind === "domain") {
        rows.push(`<div><b>Schutz</b><span>${esc(n.detail || (n.access === "access" ? "Zero Trust" : n.access === "public" ? "öffentlich" : "unbekannt"))}</span></div>`);
        if (n.service) rows.push(`<div><b>Ziel</b><span class="mono">${esc(n.service)}</span></div>`);
        if (n.orphan) rows.push(`<div><b>Hinweis</b><span>Nur die Access-App kennt diese Adresse – keine Tunnel-Route auf diesem Server</span></div>`);
      }
      if (n.kind === "access" && n.domains?.length) rows.push(`<div><b>Domains</b><span class="mono">${n.domains.map(esc).join("<br>")}</span></div>`);
      if (n.bypass) rows.push(`<div><b>Achtung</b><span>Bypass – lässt alle ohne Login durch</span></div>`);
      const nearHtml = near.length ? `<div class="gr-near">${near.slice(0, 30).map((x) => `<span class="pill"><i class="gr-dot" style="background:${look(x).color}"></i>${esc(x.label)}</span>`).join("")}${near.length > 30 ? `<span class="muted small">+${near.length - 30}</span>` : ""}</div>` : "";
      const actions = [
        n.stack ? `<a class="btn-tonal sm" href="/stacks/${encodeURIComponent(n.stack)}"><span class="ms">deployed_code</span>Stack öffnen</a>` : "",
        n.url ? `<a class="btn-tonal sm" href="${esc(n.url)}" target="_blank" rel="noopener"><span class="ms">open_in_new</span>Öffnen</a>` : "",
        n.kind === "domain" && !n.orphan ? `<a class="btn-tonal sm" href="/veroeffentlichen?q=${encodeURIComponent(n.label)}"><span class="ms">travel_explore</span>Veröffentlichung ändern</a>` : "",
      ].join("");
      const c = $("grCard");
      c.innerHTML = `<div class="gr-card-head"><span class="gr-card-ic" style="color:${look(n).color}"><span class="ms">${k.icon}</span></span>
          <div class="gr-card-title"><small>${ONE[n.kind]}</small><b class="ellip">${esc(n.label)}</b></div>
          <button type="button" class="icon-btn sm" data-close-card aria-label="Schließen"><span class="ms">close</span></button></div>
        <div class="gr-rows">${rows.join("")}</div>${nearHtml}${actions ? `<div class="btn-row">${actions}</div>` : ""}`;
      c.hidden = false;
    }

    el.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-kind]");
      if (chip) {
        const k = chip.dataset.kind;
        hidden.has(k) ? hidden.delete(k) : hidden.add(k);
        ctx.setQuery({ aus: [...hidden].join(",") === "tunnel" ? null : [...hidden].join(",") || "-" });
        chips();
        build();
      }
      if (e.target.closest("[data-close-card]")) $("grCard").hidden = true;
    });
    $("grQ").oninput = (e) => { q = e.target.value; g?.setQuery(q); ctx.setQuery({ q: q || null }); };
    document.getElementById("grFit").onclick = () => g?.resetView();
    document.getElementById("grReload").onclick = () => load();

    async function load() {
      $("grEmpty").hidden = false;
      $("grEmpty").textContent = "Lade …";
      const d = await api.get("/graph").catch((e) => ({ nodes: [], edges: [], error: e.message }));
      if (!alive) return;
      data = d;
      chips();
      build();
    }
    chips();
    load();
    return () => { alive = false; g?.destroy(); };
  },
};
