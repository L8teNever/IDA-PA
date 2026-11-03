// Leistung: CPU, RAM und Netz des Servers im Verlauf (beliebiger Zeitraum, zurückblättern, per Ziehen zoomen)
// und wer wie viel verbraucht – je Stack oder je Container, als Diagramm und Tabelle.
import { api } from "../api.js";
import { COLORS, lineChart } from "../chart.js";
import { liveIndex, live } from "../store.js";
import { bar, esc, fmtBytes, fmtDateTime, fmtPct, fmtRate, openModal, sheetHead } from "../ui.js";

const RANGES = [["1h", "1 Std", 3600], ["6h", "6 Std", 21600], ["24h", "24 Std", 86400], ["7d", "7 Tage", 604800], ["30d", "30 Tage", 2592000], ["1y", "1 Jahr", 31536000]];
const METRICS = [["cpu", "CPU"], ["mem", "RAM"], ["net_rx", "Netz ↓"], ["net_tx", "Netz ↑"]];
const fmtFor = (m) => (m === "cpu" ? (v) => fmtPct(v, 2) : m === "mem" ? (v) => fmtBytes(v) : (v) => fmtRate(v));
const nowS = () => Math.floor(Date.now() / 1000);

let group = "stack", metric = "cpu", sort = ["cpu", -1];

export default {
  id: "monitor", label: "Leistung", icon: "monitoring", path: "/leistung",
  match: (p) => (p === "/leistung" ? {} : null),

  mount(el, params, ctx) {
    const q = params.query || {};
    // Zeitraum: r = feste Länge bis jetzt (oder bis end), from/to = frei gewählt (Zoom)
    let r = RANGES.find((x) => x[0] === q.r) ? q.r : "6h";
    let from = +q.from || null, to = +q.to || +q.end || null;
    const win = () => {
      if (from && to) return [from, to];
      const len = RANGES.find((x) => x[0] === r)[2];
      const e = to || nowS();
      return [e - len, e];
    };
    const isLive = () => !to;
    const charts = {};
    let usage = [], alive = true;

    ctx.top(`<div class="topbar-left"><h2><span class="ms">monitoring</span><span class="tb-title">Leistung</span></h2></div>
      <div class="topbar-actions"><span class="tb-sub hide-mobile" id="mnSpan"></span></div>`);
    el.innerHTML = `<div class="page">
      <div class="range-bar">
        <div class="seg-mini big" id="mnRange">${RANGES.map(([k, l]) => `<button type="button" data-r="${k}">${l}</button>`).join("")}</div>
        <span class="spacer"></span>
        <button type="button" class="icon-btn" id="mnPrev" title="Früher"><span class="ms">chevron_left</span></button>
        <label class="date-pick" title="Zeitpunkt wählen"><span class="ms">event</span><input type="datetime-local" id="mnAt"></label>
        <button type="button" class="icon-btn" id="mnNext" title="Später"><span class="ms">chevron_right</span></button>
        <button type="button" class="btn-tonal sm" id="mnNow"><span class="ms">update</span>Jetzt</button>
      </div>
      <div class="tiles" id="mnTiles">${'<div class="tile skel"></div>'.repeat(4)}</div>
      <section class="card"><div class="card-head"><h3><span class="ms">memory</span>CPU</h3><span class="legend"><i style="background:${COLORS[0]}"></i>Mittel <i class="dash" style="background:${COLORS[0]}"></i>Spitze</span></div><div class="chart-box" id="chCpu"></div></section>
      <section class="card"><div class="card-head"><h3><span class="ms">memory_alt</span>RAM</h3><span class="muted small" id="mnMemTot"></span></div><div class="chart-box" id="chMem"></div></section>
      <section class="card"><div class="card-head"><h3><span class="ms">swap_vert</span>Netz</h3><span class="legend"><i style="background:${COLORS[2]}"></i>Empfangen <i style="background:${COLORS[1]}"></i>Gesendet</span></div><div class="chart-box" id="chNet"></div></section>
      <section class="card"><div class="card-head wrap"><h3><span class="ms">leaderboard</span>Verbrauch je <span id="mnGroupLbl">Stack</span></h3>
          <div class="head-ctrls"><div class="seg-mini" id="mnGroup"><button type="button" data-g="stack">Stacks</button><button type="button" data-g="name">Container</button></div>
          <div class="seg-mini" id="mnMetric">${METRICS.map(([k, l]) => `<button type="button" data-m="${k}">${l}</button>`).join("")}</div></div></div>
        <div class="chart-box" id="chTop"></div>
        <div class="table-wrap"><table class="utable" id="mnTable"></table></div></section>
    </div>`;
    const $ = (id) => el.querySelector("#" + id) || document.getElementById(id); // mnSpan liegt in der Kopfzeile
    const zoom = (a, b) => { from = a; to = b; load(); };
    const hostOpts = { onZoom: zoom, height: 200 };

    function syncUi() {
      const [a, b] = win();
      el.querySelectorAll("#mnRange button").forEach((x) => x.classList.toggle("on", !from && x.dataset.r === r));
      el.querySelectorAll("#mnGroup button").forEach((x) => x.classList.toggle("on", x.dataset.g === group));
      el.querySelectorAll("#mnMetric button").forEach((x) => x.classList.toggle("on", x.dataset.m === metric));
      $("mnGroupLbl").textContent = group === "stack" ? "Stack" : "Container";
      $("mnSpan").textContent = `${fmtDateTime(a)} – ${isLive() ? "jetzt" : fmtDateTime(b)}`;
      $("mnNext").disabled = isLive();
      $("mnNow").hidden = isLive();
      const d = new Date(b * 1000);
      $("mnAt").value = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      ctx.setQuery({ r: from ? null : r, from: from || null, to: from ? to : null, end: !from && to ? to : null });
    }

    async function load() {
      syncUi();
      const [a, b] = win();
      const pts = Math.min(400, Math.max(60, Math.floor(el.clientWidth / 3)));
      const [h, u] = await Promise.all([api.get("/monitor/host", { start: a, end: b, points: pts }), api.get("/monitor/usage", { start: a, end: b })]).catch(() => [null, null]);
      if (!alive || !h) return;
      usage = u || [];
      const base = { t: h.t, step: h.step, range: [a, b] };
      const set = (k, box, o) => { if (charts[k]) charts[k].update(o); else charts[k] = lineChart($(box), o); };
      set("cpu", "chCpu", { ...hostOpts, ...base, max: 100, fmt: (v) => fmtPct(v), series: [
        { label: "Mittel", color: COLORS[0], values: h.cpu }, { label: "Spitze", color: COLORS[0], values: h.cpu_max, fill: false, dash: true, width: 1.2 }] });
      const memTotal = Math.max(0, ...h.mem_total.filter((v) => v != null));
      $("mnMemTot").textContent = memTotal ? `von ${fmtBytes(memTotal)}` : "";
      set("mem", "chMem", { ...hostOpts, ...base, max: memTotal || undefined, bytes: true, fmt: (v) => fmtBytes(v), series: [
        { label: "Belegt", color: COLORS[4], values: h.mem }, ...(h.swap.some((v) => v) ? [{ label: "Swap", color: COLORS[5], values: h.swap, fill: false }] : [])] });
      set("net", "chNet", { ...hostOpts, ...base, bytes: true, fmt: (v) => fmtRate(v), series: [
        { label: "Empfangen", color: COLORS[2], values: h.net_rx }, { label: "Gesendet", color: COLORS[1], values: h.net_tx, fill: false }] });
      drawTiles(h);
      drawTop();
      drawTable();
    }

    function drawTiles(h) {
      const vals = (arr) => arr.filter((v) => v != null);
      const avg = (arr) => { const v = vals(arr); return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null; };
      const max = (arr) => { const v = vals(arr); return v.length ? Math.max(...v) : null; };
      const memTot = max(h.mem_total);
      const tile = (icon, label, value, sub, frac) => `<div class="tile"><div class="tile-top"><span class="ms">${icon}</span>${label}</div><div class="tile-val">${value}</div><div class="tile-sub">${sub}</div>${frac != null ? bar(frac) : ""}</div>`;
      $("mnTiles").innerHTML = tile("memory", "CPU im Mittel", fmtPct(avg(h.cpu)), `Spitze ${fmtPct(max(h.cpu_max))}`, (avg(h.cpu) || 0) / 100)
        + tile("memory_alt", "RAM im Mittel", fmtBytes(avg(h.mem)), `Spitze ${fmtBytes(max(h.mem_max))}`, memTot ? (avg(h.mem) || 0) / memTot : null)
        + tile("download", "Empfangen", fmtBytes(h.total_rx), `Spitze ${fmtRate(max(h.net_rx))}`)
        + tile("upload", "Gesendet", fmtBytes(h.total_tx), `Spitze ${fmtRate(max(h.net_tx))}`);
    }

    async function drawTop() {
      const [a, b] = win();
      const d = await api.get("/monitor/series", { metric, group, start: a, end: b, top: 6, points: 200 }).catch(() => null);
      if (!alive || !d) return;
      const series = Object.entries(d.series).map(([k, v], i) => ({ label: k, color: COLORS[i % COLORS.length], values: v, fill: false }));
      const o = { t: d.t, step: d.step, range: [a, b], series, height: 220, bytes: metric !== "cpu", fmt: fmtFor(metric), onZoom: zoom };
      if (charts.top) charts.top.update(o); else charts.top = lineChart($("chTop"), o);
    }

    function rows() {
      if (group === "name") return usage.map((u) => ({ key: u.name, sub: u.stack, ...u }));
      const by = {};
      for (const u of usage) {
        const s = (by[u.stack] ||= { key: u.stack, sub: "", n: 0, cpu: 0, cpu_max: 0, mem: 0, mem_max: 0, rx: 0, tx: 0, blk_r: 0, blk_w: 0 });
        s.n++; s.cpu += u.cpu || 0; s.cpu_max += u.cpu_max || 0; s.mem += u.mem || 0; s.mem_max += u.mem_max || 0;
        s.rx += u.rx; s.tx += u.tx; s.blk_r += u.blk_r; s.blk_w += u.blk_w;
      }
      return Object.values(by).map((s) => ({ ...s, sub: `${s.n} Container` }));
    }

    function drawTable() {
      const idx = liveIndex(live.data);
      const nowOf = (r) => (group === "stack" ? idx.byStack[r.key] : idx.byName[r.key]);
      const list = rows().sort((x, y) => sort[1] * (((x[sort[0]] ?? -1) > (y[sort[0]] ?? -1)) ? 1 : (x[sort[0]] ?? -1) < (y[sort[0]] ?? -1) ? -1 : 0));
      const maxCpu = Math.max(0.001, ...list.map((r) => r.cpu || 0)), maxMem = Math.max(1, ...list.map((r) => r.mem || 0));
      const th = (k, l, cls = "") => `<th class="${cls} ${sort[0] === k ? "sorted" : ""}" data-sort="${k}">${l}${sort[0] === k ? `<span class="ms">${sort[1] < 0 ? "arrow_downward" : "arrow_upward"}</span>` : ""}</th>`;
      $("mnTable").innerHTML = `<thead><tr>${th("key", group === "stack" ? "Stack" : "Container")}${th("cpu", "Ø CPU", "num")}${th("cpu_max", "Spitze", "num hide-mobile")}${th("mem", "Ø RAM", "num")}${th("mem_max", "Spitze", "num hide-mobile")}${th("rx", "↓ Daten", "num")}${th("tx", "↑ Daten", "num hide-mobile")}<th class="num hide-mobile">Jetzt</th></tr></thead>
        <tbody>${list.map((r) => {
          const n = nowOf(r);
          return `<tr data-key="${esc(r.key)}"><td><b>${esc(r.key)}</b><small>${esc(r.sub)}</small></td>
          <td class="num"><span class="cell-bar">${bar((r.cpu || 0) / maxCpu)}${fmtPct(r.cpu, 2)}</span></td><td class="num hide-mobile muted">${fmtPct(r.cpu_max, 2)}</td>
          <td class="num"><span class="cell-bar">${bar((r.mem || 0) / maxMem)}${fmtBytes(r.mem)}</span></td><td class="num hide-mobile muted">${fmtBytes(r.mem_max)}</td>
          <td class="num">${fmtBytes(r.rx)}</td><td class="num hide-mobile">${fmtBytes(r.tx)}</td>
          <td class="num hide-mobile muted">${n ? `${fmtPct(n.cpu, 1)} · ${fmtBytes(n.mem)}` : "–"}</td></tr>`;
        }).join("") || `<tr><td colspan="8" class="muted pad">Noch keine Messwerte in diesem Zeitraum.</td></tr>`}</tbody>`;
    }

    // ---- Bedienung ----
    $("mnRange").onclick = (e) => { const b = e.target.closest("[data-r]"); if (b) { r = b.dataset.r; from = null; load(); } };
    const shift = (dir) => { const [a, b] = win(); const len = b - a; let nb = b + dir * len; if (nb >= nowS()) { to = null; from = null; } else if (from) { from = a + dir * len; to = nb; } else to = nb; load(); };
    $("mnPrev").onclick = () => shift(-1);
    $("mnNext").onclick = () => shift(1);
    $("mnNow").onclick = () => { from = null; to = null; load(); };
    $("mnAt").onchange = (e) => { const t = Math.floor(new Date(e.target.value).getTime() / 1000); if (!t) return; const [a, b] = win(); const len = b - a; from = null; to = t >= nowS() ? null : Math.min(nowS(), t + Math.floor(len / 2)); if (to) { from = to - len; } load(); };
    $("mnGroup").onclick = (e) => { const b = e.target.closest("[data-g]"); if (b) { group = b.dataset.g; syncUi(); drawTop(); drawTable(); } };
    $("mnMetric").onclick = (e) => { const b = e.target.closest("[data-m]"); if (b) { metric = b.dataset.m; sort = [{ net_rx: "rx", net_tx: "tx" }[metric] || metric, -1]; syncUi(); drawTop(); drawTable(); } };
    $("mnTable").onclick = (e) => {
      const h = e.target.closest("[data-sort]");
      if (h) { sort = [h.dataset.sort, sort[0] === h.dataset.sort ? -sort[1] : h.dataset.sort === "key" ? 1 : -1]; return drawTable(); }
      const row = e.target.closest("tr[data-key]");
      if (row) openDetail(row.dataset.key, group, win());
    };

    load();
    // Live: solange der Zeitraum bis „jetzt“ geht, regelmäßig nachladen
    const timer = setInterval(() => { if (isLive() && !document.hidden) load(); }, 30000);
    const u = live.sub(() => drawTable());
    return () => { alive = false; clearInterval(timer); u(); Object.values(charts).forEach((c) => c.destroy()); };
  },
};

// Verlauf eines Stacks/Containers: CPU, RAM, Netz im gewählten Zeitraum
function openDetail(key, grp, [a, b]) {
  const m = openModal(`${sheetHead(key, grp === "stack" ? "stacks" : "view_in_ar")}
    <div class="sheet-scroll"><p class="sheet-hint">${fmtDateTime(a)} – ${fmtDateTime(b)}${grp === "stack" ? ` · <a href="/stacks/${encodeURIComponent(key)}">Stack öffnen</a>` : ""}</p>
    <div class="mini-h">CPU (Anteil am ganzen Server)</div><div class="chart-box" id="dCpu"></div>
    <div class="mini-h">RAM</div><div class="chart-box" id="dMem"></div>
    <div class="mini-h">Netz</div><div class="chart-box" id="dNet"></div></div>`, { wide: true });
  const q = { group: grp, names: key, start: a, end: b, points: 240 };
  const get = (metric) => api.get("/monitor/series", { ...q, metric }).catch(() => ({ t: [], series: {} }));
  Promise.all(["cpu", "mem", "net_rx", "net_tx"].map(get)).then(([c, me, rx, tx]) => {
    const base = (d) => ({ t: d.t, step: d.step, range: [a, b], height: 170 });
    lineChart(m.el.querySelector("#dCpu"), { ...base(c), fmt: (v) => fmtPct(v, 2), series: [{ label: "CPU", color: COLORS[0], values: c.series[key] || [] }] });
    lineChart(m.el.querySelector("#dMem"), { ...base(me), bytes: true, fmt: (v) => fmtBytes(v), series: [{ label: "RAM", color: COLORS[4], values: me.series[key] || [] }] });
    lineChart(m.el.querySelector("#dNet"), { ...base(rx), bytes: true, fmt: (v) => fmtRate(v), series: [
      { label: "Empfangen", color: COLORS[2], values: rx.series[key] || [] }, { label: "Gesendet", color: COLORS[1], values: tx.series[key] || [], fill: false }] });
  });
}
