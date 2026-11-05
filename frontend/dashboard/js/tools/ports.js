// Ports (Dashboard): alle offenen TCP/UDP-Ports des Servers – Schnittstelle, Prozess, Docker-Container, Tunnel-Adresse mit Schutz.
// Filter, Suche und „Freien Port finden“ (z. B. für einen neuen Dienst im Bereich 8440–8499).
import { api } from "../api.js";
import { attempt, esc, toast } from "../ui.js";

const SCOPE = {
  all: ["public", "alle Schnittstellen", "pill warn", "Lauscht auf 0.0.0.0/:: – auch über die öffentliche IP des Servers erreichbar, wenn keine Firewall davor ist"],
  public: ["language", "öffentliche IP", "pill warn", "Lauscht auf einer öffentlichen IP"],
  private: ["lan", "intern", "pill", "Nur im internen Netz (z. B. 10.7.0.1 für den Tunnel)"],
  local: ["home", "nur lokal", "pill ok", "Nur auf dem Server selbst (127.0.0.1)"],
};
const FILTERS = [["all", "Alle"], ["docker", "Docker"], ["host", "Ohne Docker"], ["open", "Nach außen offen"], ["tunnel", "Mit Adresse"]];

export default {
  id: "ports", label: "Ports", icon: "lan", path: "/ports",
  match: (p) => (p === "/ports" ? {} : null),
  mount(el, params, ctx) {
    let data = null, alive = true, q = params.query.q || "", filter = params.query.f || "all";
    ctx.top(`<div class="topbar-left"><h2><span class="ms">lan</span><span class="tb-title">Ports</span></h2></div>
      <div class="topbar-actions"><span class="tb-sub" id="ptCount"></span>
      <button type="button" class="icon-btn" id="ptReload" aria-label="Neu laden"><span class="ms">refresh</span></button></div>`);
    el.innerHTML = `<div class="page">
      <div class="tiles" id="ptTiles"></div>
      <section class="card"><div class="card-head wrap"><h3><span class="ms">search</span>Freien Port finden</h3></div>
        <div class="pt-free"><input class="cd-input" id="ptFrom" type="number" min="1" max="65535" value="8440" aria-label="von">
          <span class="muted">bis</span><input class="cd-input" id="ptTo" type="number" min="1" max="65535" value="8499" aria-label="bis">
          <button type="button" class="btn-tonal" id="ptFind"><span class="ms">search</span>Suchen</button></div>
        <div id="ptFreeOut" class="pill-row"></div></section>
      <section class="card"><div class="card-head wrap"><h3><span class="ms">lan</span>Offene Ports</h3>
        <label class="side-search pb-search"><span class="ms">search</span><input id="ptQ" type="search" placeholder="Port, Stack, Prozess oder Adresse" autocomplete="off" value="${esc(q)}"></label></div>
        <div class="seg-mini gr-chips" id="ptFilter">${FILTERS.map(([k, l]) => `<button type="button" class="${k === filter ? "on" : ""}" data-f="${k}">${l}</button>`).join("")}</div>
        <div id="ptList" class="muted">Lade …</div></section></div>`;
    const $ = (id) => el.querySelector(`#${id}`);

    async function load() {
      data = await api.get("/ports").catch((e) => ({ error: e.message, ports: [] }));
      if (alive) draw();
    }

    const text = (r) => [r.port, r.proto, ...r.addresses, ...r.processes.map((p) => p.name), ...r.docker.flatMap((d) => [d.stack, d.container, d.service, d.image]),
      ...r.routes.map((t) => t.url)].join(" ").toLowerCase();
    const keep = (r) => (filter === "all" || (filter === "docker" && r.kind === "docker") || (filter === "host" && r.kind === "host")
      || (filter === "open" && (r.scope === "all" || r.scope === "public")) || (filter === "tunnel" && r.routes.length)) && (!q || text(r).includes(q.toLowerCase()));

    function draw() {
      const all = data.ports;
      const open = all.filter((r) => r.scope === "all" || r.scope === "public");
      $("ptTiles").innerHTML = [
        ["lan", "Offene Ports", all.length, `${all.filter((r) => r.proto === "tcp").length} TCP · ${all.filter((r) => r.proto === "udp").length} UDP`],
        ["deployed_code", "Von Docker", all.filter((r) => r.kind === "docker").length, "veröffentlichte Container-Ports"],
        ["public", "Nach außen offen", open.length, open.length ? "auf allen Schnittstellen oder öffentlicher IP" : "alles intern", open.length ? "warn" : "ok"],
        ["travel_explore", "Mit Tunnel-Adresse", all.filter((r) => r.routes.length).length, "über Cloudflare erreichbar"],
      ].map(([ic, t, v, sub, cls]) => `<div class="tile pt-tile ${cls || ""}"><div class="tile-top"><span class="ms">${ic}</span>${t}</div><div class="tile-val">${v}</div><div class="tile-sub">${sub}</div></div>`).join("");
      const rows = all.filter(keep);
      $("ptCount").textContent = `${rows.length} von ${all.length}`;
      if (data.error) { $("ptList").innerHTML = `<div class="note warn"><span class="ms">error</span><div>${esc(data.error)}</div></div>`; return; }
      $("ptList").innerHTML = (data.process_names ? "" : `<div class="muted small pt-hint">Prozessnamen sind aus dem Container nicht lesbar – Docker-Zuordnung und Adressen gehen trotzdem.</div>`)
        + (rows.length ? `<div class="pb-rows">${rows.map(row).join("")}</div>` : `<div class="muted pad">Nichts gefunden.</div>`);
    }

    function row(r) {
      const [si, sl, sc, st] = SCOPE[r.scope] || SCOPE.private;
      const who = r.docker.length
        ? r.docker.map((d) => `<span class="pt-who"><span class="ms sm">deployed_code</span>${d.stack ? `<a href="/stacks/${encodeURIComponent(d.stack)}">${esc(d.stack)}</a> / ` : ""}${esc(d.service)}<span class="muted mono"> :${d.private}</span>${d.state !== "running" ? ` <span class="pill">${esc(d.state)}</span>` : ""}</span>`).join("")
        : r.processes.length ? r.processes.map((p) => `<span class="pt-who"><span class="ms sm">terminal</span>${esc(p.name)}<span class="muted"> · PID ${p.pid}</span></span>`).join("")
          : `<span class="pt-who muted"><span class="ms sm">help</span>unbekannter Prozess</span>`;
      const routes = r.routes.map((t) => `<a class="pt-route" href="${esc(t.url)}" target="_blank" rel="noopener" title="${esc(t.detail)}"><span class="ms sm">${t.access === "access" ? "lock" : "public"}</span>${esc(t.url.replace(/^https:\/\//, ""))}</a>`).join("");
      return `<div class="pb-row pt-row">
        <div class="pt-port"><b class="mono">${r.port}</b><small class="muted">${r.proto.toUpperCase()}</small></div>
        <div class="pb-main">${who}
          <small class="muted pt-addr">${r.addresses.map((a) => `<span class="mono">${esc(a.includes(":") ? `[${a}]` : a)}</span>`).join(" · ")}</small>
          ${routes ? `<div class="pt-routes">${routes}</div>` : ""}</div>
        <span class="${sc}" title="${esc(st)}"><span class="ms sm">${si}</span>${sl}</span></div>`;
    }

    el.addEventListener("click", (e) => {
      const f = e.target.closest("[data-f]");
      if (f) {
        filter = f.dataset.f;
        el.querySelectorAll("#ptFilter [data-f]").forEach((b) => b.classList.toggle("on", b === f));
        ctx.setQuery({ f: filter === "all" ? null : filter });
        draw();
      }
      const fp = e.target.closest("[data-copy]");
      if (fp) navigator.clipboard?.writeText(fp.dataset.copy).then(() => toast(`Port ${fp.dataset.copy} kopiert`), () => {});
    });
    $("ptQ").oninput = (e) => { q = e.target.value; ctx.setQuery({ q: q || null }); if (data) draw(); };
    $("ptFind").onclick = async () => {
      const r = await attempt(() => api.get("/ports/free", { start: $("ptFrom").value, end: $("ptTo").value, count: 8 }));
      if (!r) return;
      $("ptFreeOut").innerHTML = r.free.length ? r.free.map((p) => `<button type="button" class="pill ok pt-freebtn" data-copy="${p}" title="Kopieren"><span class="ms sm">content_copy</span>${p}</button>`).join("")
        : `<span class="muted small">Im Bereich ${r.start}–${r.end} ist alles belegt.</span>`;
    };
    document.getElementById("ptReload").onclick = () => load();

    load();
    const timer = setInterval(() => { if (!document.hidden) load(); }, 30000);
    return () => { alive = false; clearInterval(timer); };
  },
};
