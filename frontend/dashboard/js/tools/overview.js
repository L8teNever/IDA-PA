// Übersicht: Server jetzt (CPU, RAM, Netz, Platte mit Verlauf der letzten Stunde), Stacks mit Problemen, größte Verbraucher.
import { api } from "../api.js";
import { spark } from "../chart.js";
import { liveIndex, live, stacks } from "../store.js";
import { bar, esc, fmtBytes, fmtPct, fmtRate, fmtUptime, STATES } from "../ui.js";

export default {
  id: "overview", label: "Übersicht", icon: "space_dashboard", path: "/",
  match: (p) => (p === "/" ? {} : null),
  mount(el, _p, ctx) {
    ctx.top(`<div class="topbar-left"><h2><span class="ms">space_dashboard</span><span class="tb-title">Übersicht</span></h2></div>
      <div class="topbar-actions"><span class="tb-sub" id="ovHost"></span></div>`);
    el.innerHTML = `<div class="page">
      <div class="tiles" id="ovTiles">${'<div class="tile skel"></div>'.repeat(4)}</div>
      <div class="cols">
        <section class="card"><div class="card-head"><h3><span class="ms">deployed_code</span>Stacks</h3><a class="btn-tonal sm" href="/stacks">Alle</a></div><div id="ovStacks" class="muted">Lade …</div></section>
        <section class="card"><div class="card-head"><h3><span class="ms">bolt</span>Größte Verbraucher jetzt</h3><a class="btn-tonal sm" href="/leistung">Leistung</a></div><div id="ovTop" class="muted">Lade …</div></section>
      </div></div>`;
    let hist = null;
    const loadHist = async () => {
      const end = Math.floor(Date.now() / 1000);
      hist = await api.get("/monitor/host", { start: end - 3600, end, points: 60 }).catch(() => null);
    };
    loadHist().then(() => drawTiles(live.data));
    const histTimer = setInterval(loadHist, 60000);

    function drawTiles(now) {
      if (!now?.host) return;
      const h = now.host;
      document.getElementById("ovHost").textContent = `${now.hostname || ""} · läuft seit ${fmtUptime(h.uptime)}`;
      const tile = (href, icon, label, value, sub, frac, sp) => `<a class="tile" href="${href}"><div class="tile-top"><span class="ms">${icon}</span>${label}</div>
        <div class="tile-val">${value}</div><div class="tile-sub">${sub}</div>${frac != null ? bar(frac) : ""}${sp || ""}</a>`;
      document.getElementById("ovTiles").innerHTML =
        tile("/leistung", "memory", "CPU", fmtPct(h.cpu), `${h.cores} Kerne · Last ${h.load1?.toFixed(2)}`, (h.cpu || 0) / 100, spark(hist?.cpu, "var(--accent)", { max: 100 }))
        + tile("/leistung", "memory_alt", "RAM", fmtBytes(h.mem), `von ${fmtBytes(h.mem_total)}${h.swap ? ` · Swap ${fmtBytes(h.swap)}` : ""}`, h.mem / h.mem_total, spark(hist?.mem, "#c3b9f5", { max: h.mem_total }))
        + tile("/leistung", "swap_vert", "Netz", `↓ ${fmtRate(h.net_rx)}`, `↑ ${fmtRate(h.net_tx)}${now.ifaces?.length ? ` · ${esc(now.ifaces.join(", "))}` : ""}`, null, spark(hist?.net_rx, "#81c995"))
        + tile("/speicher", "hard_drive", "Platte", fmtBytes(h.disk_used), `frei ${fmtBytes(h.disk_free)} von ${fmtBytes(h.disk_total)}`, h.disk_used / h.disk_total, "");
      drawTop(now);
    }
    function drawTop(now) {
      const { byStack } = liveIndex(now);
      const list = Object.entries(byStack);
      const row = ([name, v], val, frac) => `<a class="mini-row" href="/stacks/${encodeURIComponent(name)}"><span class="mini-name">${esc(name)}</span>${bar(frac)}<b>${val}</b></a>`;
      const cpu = [...list].sort((a, b) => b[1].cpu - a[1].cpu).slice(0, 5);
      const mem = [...list].sort((a, b) => b[1].mem - a[1].mem).slice(0, 5);
      const maxC = Math.max(1, ...cpu.map((x) => x[1].cpu)), maxM = Math.max(1, ...mem.map((x) => x[1].mem));
      document.getElementById("ovTop").innerHTML = list.length ? `<div class="mini-h">CPU</div>${cpu.map((x) => row(x, fmtPct(x[1].cpu, 2), x[1].cpu / maxC)).join("")}
        <div class="mini-h">RAM</div>${mem.map((x) => row(x, fmtBytes(x[1].mem), x[1].mem / maxM)).join("")}` : `<span class="muted">Noch keine Messung – kommt in ein paar Sekunden.</span>`;
    }
    function drawStacks(list) {
      if (!list) return;
      const n = (s) => list.filter((x) => x.state === s).length;
      const bad = list.filter((x) => x.state === "problem" || x.state === "partial");
      const pill = (s, c) => `<span class="state-pill ${STATES[s][2]}"><span class="ms">${STATES[s][0]}</span>${c} ${STATES[s][1]}</span>`;
      document.getElementById("ovStacks").innerHTML = `<div class="pill-row">${pill("running", n("running"))}${pill("partial", n("partial"))}${pill("problem", n("problem"))}${pill("stopped", n("stopped"))}</div>
        ${bad.length ? `<div class="mini-h">Braucht Aufmerksamkeit</div>${bad.map((s) => `<a class="mini-row" href="/stacks/${encodeURIComponent(s.name)}">
          <span class="ms st-${STATES[s.state][2]}">${STATES[s.state][0]}</span><span class="mini-name">${esc(s.name)}</span><b>${s.running}/${s.total}</b></a>`).join("")}`
        : `<div class="all-good"><span class="ms">verified</span>Alles läuft, wie es soll.</div>`}`;
    }
    const u1 = live.sub((d) => drawTiles(d));
    const u2 = stacks.sub((d) => drawStacks(d));
    return () => { u1(); u2(); clearInterval(histTimer); };
  },
};
