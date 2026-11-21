// IDA Health – Geräte mit ida-connect: Übersicht (live), je Gerät Verlauf, Terminal, Dateien, Docker, System.
// Nutzt die Bausteine des Dashboards (Diagramme, Dialoge, Formate) unter /static/shared/dashboard/.
import { api } from "/static/shared/dashboard/api.js";
import { COLORS, lineChart, spark } from "/static/shared/dashboard/chart.js";
import { loginView, settingsView } from "./settings.js";
import { $, attempt, bar, confirmDialog, ctrState, esc, fmtAgo, fmtBytes, fmtPct, fmtRate, fmtUptime, openModal, sheetHead, toast } from "/static/shared/dashboard/ui.js";

const app = $("app");
const S = { devices: [], me: {}, live: null, sparks: {}, view: null };
const RANGES = [["3600", "1 Std"], ["21600", "6 Std"], ["86400", "24 Std"], ["604800", "7 Tage"], ["2592000", "30 Tage"], ["31536000", "1 Jahr"]];
const TABS = [["info", "monitoring", "Übersicht"], ["term", "terminal", "Terminal"], ["files", "folder", "Dateien"], ["docker", "deployed_code", "Docker"], ["sys", "settings", "System"]];
const fmtTemp = (v) => (v == null ? "–" : `${Math.round(v)} °C`);
const DEV_ICON = (d) => /raspberry|bcm2/i.test(`${d.info?.board} ${d.info?.cpu_model}`) ? "developer_board" : /arm|aarch/i.test(d.info?.arch || "") ? "memory" : "dns";

// ---------------- Live-Verbindung ----------------
function connectLive() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/live${S.view?.dev ? `?dev=${S.view.dev}` : ""}`);
  S.live = ws;
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.t === "ping") return;
    const d = S.devices.find((x) => x.id === m.dev);
    if (!d) { if (m.t === "info") loadDevices(); return; }
    if (m.t === "m") {
      d.latest = m.m; d.last_seen = Date.now() / 1000; d.online = true;
      (S.sparks[d.id] ||= []).push(m.m.cpu);
      if (S.sparks[d.id].length > 60) S.sparks[d.id].shift();
    } else if (m.t === "info") { d.info = m.info; d.online = true; }
    else if (m.t === "online") d.online = m.online;
    S.view?.onLive?.(d, m);
  };
  ws.onclose = () => { if (S.live === ws) setTimeout(connectLive, 3000); };
}
function focusLive(dev) {
  if (S.live?.readyState === 1) S.live.send(JSON.stringify({ t: "focus", dev: dev || null }));
}

async function loadDevices() {
  const r = await api.get("/devices");
  S.devices = r.devices;
  return S.devices;
}

// ---------------- Router ----------------
async function route() {
  S.view?.destroy?.();
  S.view = null;
  const m = location.hash.match(/^#\/d\/([^/]+)(?:\/(\w+))?/);
  if (location.hash === "#/einstellungen") S.view = await settingsView(app, topbar, S.me);
  else if (m) await deviceView(decodeURIComponent(m[1]), m[2] || "info");
  else await overview();
}
addEventListener("hashchange", route);

function topbar(title, { back = false, actions = "", sub = "" } = {}) {
  return `<header class="hp-top">${back ? `<a class="icon-btn" href="#/" aria-label="Zurück"><span class="ms">arrow_back</span></a>` : ""}
    <h1><span class="ms">${back ? "devices" : "monitor_heart"}</span><span class="hp-title">${esc(title)}</span>${sub ? `<small>${sub}</small>` : ""}</h1>
    <span class="spacer"></span>${actions}</header>`;
}

// ---------------- Übersicht ----------------
function devCard(d) {
  const m = d.latest || {};
  const mem = m.mem?.[1] ? m.mem[0] / m.mem[1] : null;
  const disk = (m.disks || []).reduce((a, x) => [a[0] + x[1], a[1] + x[2]], [0, 0]);
  const temp = Math.max(...(m.temps || []).map((t) => t[1]), -Infinity);
  const rx = (m.net || []).reduce((a, n) => a + n[1], 0), tx = (m.net || []).reduce((a, n) => a + n[2], 0);
  const mesh = d.info?.mesh?.ip;
  return `<a class="hp-dev ${d.online ? "" : "off"}" href="#/d/${encodeURIComponent(d.id)}" data-dev="${esc(d.id)}">
    <div class="hp-dev-head"><span class="hp-dev-ic"><span class="ms">${DEV_ICON(d)}</span></span>
      <div style="min-width:0;flex:1"><div class="hp-dev-name">${esc(d.name)}</div>
      <div class="hp-dev-sub">${esc([d.info?.os, d.info?.arch].filter(Boolean).join(" · ") || "noch keine Angaben")}</div></div>
      <span class="hp-dot ${d.online ? "ok" : ""}" title="${d.online ? "online" : `offline ${fmtAgo(d.last_seen)}`}"></span></div>
    ${d.online || m.cpu != null ? `<div class="hp-kv">
      <span><span class="ms">memory</span>CPU</span>${bar((m.cpu || 0) / 100)}<b>${fmtPct(m.cpu)}</b>
      <span><span class="ms">memory_alt</span>RAM</span>${bar(mem)}<b>${fmtBytes(m.mem?.[0])}</b>
      <span><span class="ms">hard_drive</span>Platte</span>${bar(disk[1] ? disk[0] / disk[1] : 0)}<b>${disk[1] ? fmtPct((disk[0] / disk[1]) * 100) : "–"}</b>
      <span><span class="ms">thermostat</span>Temp.</span><span></span><b>${isFinite(temp) ? fmtTemp(temp) : "–"}</b>
      <span><span class="ms">swap_vert</span>Netz</span><span></span><b>↓ ${fmtRate(rx)} · ↑ ${fmtRate(tx)}</b>
    </div>` : `<div class="hp-dev-sub">offline ${esc(fmtAgo(d.last_seen))}</div>`}
    ${mesh ? `<div class="hp-dev-sub"><span class="ms sm">hub</span> Mesh ${esc(mesh)}${d.info?.route === "mesh" ? " · verbunden darüber" : ""}</div>` : ""}
    ${spark(S.sparks[d.id] || [], COLORS[0], { max: 100 })}
  </a>`;
}

async function overview() {
  app.innerHTML = topbar("Health", { actions: `<a class="icon-btn" href="#/einstellungen" title="Einstellungen" aria-label="Einstellungen"><span class="ms">settings</span></a>
      <button class="btn-primary" id="hpAdd"><span class="ms">add</span>Gerät hinzufügen</button>` }) +
    `<main class="page">${S.me.setup ? `<a class="hint" href="#/einstellungen" style="text-decoration:none"><span class="ms">shield</span><span><b>Noch nicht abgesichert.</b>
      Unter Einstellungen Cloudflare Access eintragen und Health mit IDA verbinden – bis dahin kommt man nur mit dem Einrichtungs-Code hinein.</span></a>` : ""}
      <div class="hp-grid" id="hpGrid"></div></main>`;
  $("hpAdd").onclick = addDevice;
  focusLive(null);
  const grid = $("hpGrid");
  const draw = () => {
    grid.innerHTML = S.devices.length ? S.devices.map(devCard).join("")
      : `<div class="hp-empty" style="grid-column:1/-1"><span class="ms">devices</span><div>Noch keine Geräte.<br>Installiere <b>ida-connect</b> auf einem Gerät und koppel es hier.</div></div>`;
  };
  await attempt(loadDevices);
  // Verlauf für die Mini-Diagramme (letzte Stunde)
  await Promise.all(S.devices.filter((d) => !S.sparks[d.id]).map(async (d) => {
    const h = await api.get(`/devices/${d.id}/history`, { range: 3600, points: 60 }).catch(() => null);
    if (h) S.sparks[d.id] = h.cpu.filter((v) => v != null);
  }));
  draw();
  let pending = false;
  S.view = {
    onLive: () => { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; draw(); }); } },
  };
}

function addDevice() {
  const m = openModal(`${sheetHead("Gerät hinzufügen", "add_circle")}
    <div class="hp-form">
      <p class="sheet-hint">Auf dem Gerät (Linux, z. B. Raspberry Pi) als root ausführen – installiert ida-connect von GitHub und koppelt es direkt:</p>
      <label>Name<input class="cd-input" id="adName" value="" placeholder="z. B. Raspberry Pi Wohnzimmer" maxlength="60"></label>
      ${S.me.mesh ? `<label class="hp-check"><input type="checkbox" id="adMesh" checked> Ins Cloudflare Mesh aufnehmen</label>` : ""}
      <button class="btn-primary" id="adGo"><span class="ms">key</span>Befehl erstellen</button>
      <div id="adOut"></div>
      <p class="sheet-hint">Ohne Code geht es auch: <code>curl -fsSL ${esc(S.me.install_url || "")} | sudo sh</code>, dann <code>http://&lt;gerät&gt;:1111</code> öffnen und „Mit IDA verbinden“.</p>
    </div>`, { wide: true });
  m.el.querySelector("#adGo").onclick = () => attempt(async () => {
    const r = await api.post("/pair-codes", { name: m.el.querySelector("#adName").value.trim() || "Gerät", mesh: !!m.el.querySelector("#adMesh")?.checked });
    m.el.querySelector("#adOut").innerHTML = `<div class="hp-cmd"><code>${esc(r.command)}</code><button class="icon-btn sm" id="adCopy" title="Kopieren"><span class="ms">content_copy</span></button></div>
      <p class="muted small">Gilt 10 Minuten und nur einmal. Das Gerät erscheint hier, sobald es verbunden ist.</p>`;
    m.el.querySelector("#adCopy").onclick = () => navigator.clipboard.writeText(r.command).then(() => toast("Kopiert"));
  });
}

// ---------------- Gerät ----------------
async function deviceView(id, tab) {
  let d = S.devices.find((x) => x.id === id);
  if (!d) { await attempt(loadDevices); d = S.devices.find((x) => x.id === id); }
  if (!d) { location.hash = "#/"; return; }
  const sub = () => `<span class="hp-dot ${d.online ? "ok" : ""}"></span> ${d.online ? "online" : `offline ${esc(fmtAgo(d.last_seen))}`}`;
  app.innerHTML = topbar(d.name, { back: true, sub: sub() }) +
    `<nav class="hp-tabs">${TABS.map(([k, ic, l]) => `<button data-t="${k}" class="${k === tab ? "on" : ""}"><span class="ms">${ic}</span>${l}</button>`).join("")}</nav>
     <main class="page" id="hpBody"></main>`;
  app.querySelectorAll(".hp-tabs button").forEach((b) => { b.onclick = () => { location.hash = `#/d/${encodeURIComponent(id)}/${b.dataset.t}`; }; });
  focusLive(id);
  const body = $("hpBody");
  const views = { info: infoTab, term: termTab, files: filesTab, docker: dockerTab, sys: sysTab };
  const v = (await views[tab]?.(d, body)) || {};
  S.view = {
    dev: id,
    onLive: (dd, m) => {
      if (dd.id !== id) return;
      const s = app.querySelector(".hp-top small");
      if (s && m.t !== "m") s.innerHTML = sub();
      v.onLive?.(dd, m);
    },
    destroy: () => { v.destroy?.(); focusLive(null); },
  };
}

// ---- Übersicht eines Geräts ----
async function infoTab(d, body) {
  let range = +(localStorage.hpRange || 3600);
  body.innerHTML = `<div class="range-bar"><div class="seg-mini big" id="rg">${RANGES.map(([k, l]) => `<button data-r="${k}" class="${+k === range ? "on" : ""}">${l}</button>`).join("")}</div>
      <span class="spacer"></span><span class="muted small" id="rgInfo"></span></div>
    <div class="tiles" id="tiles"></div>
    <div class="hp-cols">
      <section class="card"><div class="card-head"><h3><span class="ms">memory</span>CPU</h3><span class="legend"><i style="background:${COLORS[0]}"></i>Mittel <i class="dash" style="background:${COLORS[0]}"></i>Spitze</span></div><div class="chart-box" id="chCpu"></div></section>
      <section class="card"><div class="card-head"><h3><span class="ms">memory_alt</span>RAM</h3><span class="muted small" id="memTot"></span></div><div class="chart-box" id="chMem"></div></section>
      <section class="card"><div class="card-head"><h3><span class="ms">thermostat</span>Temperatur</h3><span class="legend"><i style="background:${COLORS[3]}"></i>Mittel <i class="dash" style="background:${COLORS[9]}"></i>Spitze</span></div><div class="chart-box" id="chTemp"></div></section>
      <section class="card"><div class="card-head"><h3><span class="ms">swap_vert</span>Netz</h3><span class="legend"><i style="background:${COLORS[2]}"></i>Empfangen <i style="background:${COLORS[1]}"></i>Gesendet</span></div><div class="chart-box" id="chNet"></div></section>
      <section class="card"><div class="card-head"><h3><span class="ms">hard_drive</span>Platten-Durchsatz</h3><span class="legend"><i style="background:${COLORS[4]}"></i>Lesen <i style="background:${COLORS[5]}"></i>Schreiben</span></div><div class="chart-box" id="chIo"></div></section>
      <section class="card"><div class="card-head"><h3><span class="ms">speed</span>Last</h3><span class="muted small">Load (1 Min)</span></div><div class="chart-box" id="chLoad"></div></section>
    </div>
    <div class="hp-cols" id="details"></div>`;
  const opt = (fmt, extra = {}) => ({ t: [], series: [], fmt, height: 190, ...extra });
  const ch = {
    cpu: lineChart($("chCpu"), opt((v) => `${Math.round(v)} %`, { max: 100 })),
    mem: lineChart($("chMem"), opt((v) => fmtBytes(v, 0), { bytes: true })),
    temp: lineChart($("chTemp"), opt((v) => `${Math.round(v)} °`, { min: 50 })),
    net: lineChart($("chNet"), opt((v) => fmtRate(v), { bytes: true })),
    io: lineChart($("chIo"), opt((v) => fmtRate(v), { bytes: true })),
    load: lineChart($("chLoad"), opt((v) => (Math.round(v * 10) / 10).toString().replace(".", ","))),
  };
  let hist = null;
  async function load() {
    hist = await api.get(`/devices/${d.id}/history`, { range, points: 300 }).catch((e) => { toast(e.message); return null; });
    if (!hist) return;
    const end = Math.floor(Date.now() / 1000), rr = [end - range, end];
    const zoom = (a, b) => { range = Math.max(300, b - a); load(); };
    ch.cpu.update({ t: hist.t, range: rr, step: hist.step, onZoom: zoom, series: [{ label: "Mittel", color: COLORS[0], values: hist.cpu, fill: true }, { label: "Spitze", color: COLORS[0], values: hist.cpu_max, dash: true }] });
    ch.mem.update({ t: hist.t, range: rr, step: hist.step, max: hist.mem_total.find((v) => v) || undefined, series: [{ label: "Belegt", color: COLORS[4], values: hist.mem, fill: true }, { label: "Swap", color: COLORS[5], values: hist.swap }] });
    ch.temp.update({ t: hist.t, range: rr, step: hist.step, series: [{ label: "Mittel", color: COLORS[3], values: hist.temp, fill: true }, { label: "Spitze", color: COLORS[9], values: hist.temp_max, dash: true }] });
    ch.net.update({ t: hist.t, range: rr, step: hist.step, series: [{ label: "Empfangen", color: COLORS[2], values: hist.rx, fill: true }, { label: "Gesendet", color: COLORS[1], values: hist.tx, fill: true }] });
    ch.io.update({ t: hist.t, range: rr, step: hist.step, series: [{ label: "Lesen", color: COLORS[4], values: hist.rd, fill: true }, { label: "Schreiben", color: COLORS[5], values: hist.wr, fill: true }] });
    ch.load.update({ t: hist.t, range: rr, step: hist.step, series: [{ label: "Load", color: COLORS[6], values: hist.load1, fill: true }] });
    $("rgInfo").textContent = `↓ ${fmtBytes(hist.total_rx)} · ↑ ${fmtBytes(hist.total_tx)} im Zeitraum`;
    const mt = hist.mem_total.find((v) => v);
    $("memTot").textContent = mt ? `von ${fmtBytes(mt)}` : "";
  }
  $("rg").onclick = (e) => {
    const b = e.target.closest("[data-r]");
    if (!b) return;
    range = +b.dataset.r; localStorage.hpRange = range;
    $("rg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    load();
  };

  function drawNow(dd) {
    const m = dd.latest || {}, i = dd.info || {};
    const disk = (m.disks || []).reduce((a, x) => [a[0] + x[1], a[1] + x[2]], [0, 0]);
    const temps = m.temps || [];
    const tmax = temps.length ? Math.max(...temps.map((t) => t[1])) : null;
    const rx = (m.net || []).reduce((a, n) => a + n[1], 0), tx = (m.net || []).reduce((a, n) => a + n[2], 0);
    const tile = (icon, label, value, sub, frac) => `<div class="tile"><div class="tile-top"><span class="ms">${icon}</span>${label}</div><div class="tile-val">${value}</div><div class="tile-sub">${sub}</div>${frac != null ? bar(frac) : ""}</div>`;
    $("tiles").innerHTML = [
      tile("memory", "CPU", fmtPct(m.cpu), `${i.cores || "?"} Kerne · Load ${(m.load || []).map((x) => String(x).replace(".", ",")).join(" / ") || "–"}`, (m.cpu || 0) / 100),
      tile("memory_alt", "RAM", fmtBytes(m.mem?.[0]), `von ${fmtBytes(m.mem?.[1])}${m.swap?.[1] ? ` · Swap ${fmtBytes(m.swap[0])}` : ""}`, m.mem?.[1] ? m.mem[0] / m.mem[1] : 0),
      tile("hard_drive", "Speicher", disk[1] ? fmtBytes(disk[1] - disk[0]) + " frei" : "–", disk[1] ? `${fmtBytes(disk[0])} von ${fmtBytes(disk[1])} belegt` : "", disk[1] ? disk[0] / disk[1] : null),
      tile("thermostat", "Temperatur", fmtTemp(tmax), (m.fans || []).length ? m.fans.map((f) => `${f[1]} U/min`).join(" · ") : temps.length ? `${temps.length} Sensoren` : "keine Sensoren", tmax != null ? tmax / 100 : null),
      tile("swap_vert", "Netz", `↓ ${fmtRate(rx)}`, `↑ ${fmtRate(tx)}`),
      tile("schedule", "Laufzeit", fmtUptime(m.up), i.kernel ? `Kernel ${esc(i.kernel)}` : ""),
    ].join("");
    const rows = (items) => `<div class="hp-list">${items.join("") || `<div class="muted small pad">–</div>`}</div>`;
    $("details").innerHTML = `
      <section class="card"><div class="card-head"><h3><span class="ms">memory</span>Kerne</h3></div>
        <div class="hp-cores">${(m.cores || []).map((c, k) => `<div class="hp-core">#${k}<b>${Math.round(c)} %</b>${bar(c / 100)}</div>`).join("")}</div></section>
      <section class="card"><div class="card-head"><h3><span class="ms">thermostat</span>Sensoren</h3></div>
        ${rows([...temps.map((t) => `<div class="hp-row"><span class="ms">thermostat</span><span class="grow">${esc(t[0])}</span><span class="num">${fmtTemp(t[1])}</span></div>`),
                ...(m.fans || []).map((f) => `<div class="hp-row"><span class="ms">mode_fan</span><span class="grow">${esc(f[0])}</span><span class="num">${f[1]} U/min</span></div>`)])}</section>
      <section class="card"><div class="card-head"><h3><span class="ms">hard_drive</span>Laufwerke</h3></div>
        ${rows((m.disks || []).map((x) => `<div class="hp-row"><span class="ms">folder</span><span class="grow">${esc(x[0])}</span>${bar(x[1] / x[2])}<span class="num">${fmtBytes(x[1])} / ${fmtBytes(x[2])}</span></div>`))}</section>
      <section class="card"><div class="card-head"><h3><span class="ms">lan</span>Netzwerk</h3></div>
        ${rows((m.net || []).map((n) => `<div class="hp-row"><span class="ms">${/^w/.test(n[0]) ? "wifi" : /warp|wg|tun|tailscale/i.test(n[0]) ? "vpn_lock" : "settings_ethernet"}</span><span class="grow">${esc(n[0])}</span><span class="num">↓ ${fmtRate(n[1])} · ↑ ${fmtRate(n[2])}</span></div>`))}
        ${i.mesh?.ip ? `<div class="muted small">Cloudflare Mesh: <b>${esc(i.mesh.ip)}</b></div>` : ""}</section>`;
  }
  drawNow(d);
  await load();
  let last = 0;
  return {
    onLive: (dd, m) => {
      if (m.t !== "m" && m.t !== "info") return;
      drawNow(dd);
      // Verlauf alle 30 s nachladen (nur im Kurzzeitraum sinnvoll)
      if (range <= 21600 && Date.now() - last > 30000) { last = Date.now(); load(); }
    },
    destroy: () => Object.values(ch).forEach((c) => c.destroy()),
  };
}

// ---- Terminal ----
let xtermLoaded = null;
function loadXterm() {
  const add = (tag, attrs) => new Promise((res, rej) => { const e = Object.assign(document.createElement(tag), attrs); e.onload = res; e.onerror = rej; document.head.appendChild(e); });
  return (xtermLoaded ||= Promise.all([add("link", { rel: "stylesheet", href: "/static/vendor/xterm/xterm.css" }), add("script", { src: "/static/vendor/xterm/xterm.js" })])
    .then(() => add("script", { src: "/static/vendor/xterm/addon-fit.js" })));
}

async function termTab(d, body) {
  if (!d.online) { body.innerHTML = `<div class="hint"><span class="ms">cloud_off</span>Das Gerät ist offline – die Shell geht nur, wenn es verbunden ist.</div>`; return {}; }
  body.innerHTML = `<div class="hp-term-bar"><span class="muted small">Shell als root auf <b>${esc(d.name)}</b> – wie SSH, nur über Health. Jede Sitzung steht im Protokoll.</span><span class="spacer"></span>
    <button class="btn-tonal sm" id="tmNew"><span class="ms">refresh</span>Neu verbinden</button></div><div class="hp-term" id="tm"></div>`;
  await loadXterm();
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#7fd3ff";
  const term = new Terminal({ fontFamily: "ui-monospace, Menlo, Consolas, monospace", fontSize: 14, cursorBlink: true, scrollback: 5000,
    theme: { background: "#08090c", foreground: "#e3e2e6", cursor: accent } });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($("tm"));
  fit.fit();
  let ws = null;
  const dec = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const send = (m) => ws?.readyState === 1 && ws.send(JSON.stringify(m));
  function connect() {
    ws?.close();
    term.reset();
    const me = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/devices/${encodeURIComponent(d.id)}/shell?cols=${term.cols}&rows=${term.rows}`);
    ws = me;
    me.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.t === "out") term.write(dec(m.d));
      else if (m.t === "exit") term.write(`\r\n\x1b[90m[Sitzung beendet${m.error ? `: ${m.error}` : ""}]\x1b[0m\r\n`);
    };
    me.onclose = () => { if (ws === me) term.write("\r\n\x1b[90m[getrennt]\x1b[0m\r\n"); };
  }
  term.onData((data) => send({ t: "in", d: data }));
  const ro = new ResizeObserver(() => { fit.fit(); send({ t: "resize", cols: term.cols, rows: term.rows }); });
  ro.observe($("tm"));
  $("tmNew").onclick = connect;
  connect();
  term.focus();
  return { destroy: () => { ro.disconnect(); const w = ws; ws = null; w?.close(); term.dispose(); } };
}

// ---- Dateien ----
async function filesTab(d, body) {
  let path = sessionStorage[`hpPath:${d.id}`] || "/";
  body.innerHTML = `<section class="card"><div class="card-head wrap"><div class="hp-crumbs" id="fc"></div>
    <div class="btn-row"><button class="btn-tonal sm" id="fUp"><span class="ms">upload</span>Hochladen</button><button class="btn-tonal sm" id="fMk"><span class="ms">create_new_folder</span>Ordner</button>
    <button class="btn-tonal sm" id="fNew"><span class="ms">note_add</span>Datei</button></div></div>
    <div class="hp-list hp-files" id="fl"></div><input type="file" id="fFile" hidden multiple></section>`;
  const join = (a, b) => (a.endsWith("/") ? a : a + "/") + b;
  async function open(p) {
    const r = await api.get(`/devices/${d.id}/fs/list`, { path: p }).catch((e) => { toast(e.message); return null; });
    if (!r) return;
    path = p; sessionStorage[`hpPath:${d.id}`] = p;
    const parts = p.split("/").filter(Boolean);
    $("fc").innerHTML = `<button data-p="/"><span class="ms sm">home</span> /</button>` + parts.map((x, k) => `<span>›</span><button data-p="/${esc(parts.slice(0, k + 1).join("/"))}">${esc(x)}</button>`).join("");
    $("fl").innerHTML = (p !== "/" ? `<div class="hp-row" data-dir="${esc(p.replace(/\/[^/]+\/?$/, "") || "/")}"><span class="ms d">arrow_upward</span><span class="grow">..</span></div>` : "") +
      r.entries.map(([n, t, size, mtime, mode]) => {
        const dir = t.includes("d"), full = join(p, n);
        return `<div class="hp-row" ${dir ? `data-dir="${esc(full)}"` : `data-file="${esc(full)}"`} tabindex="0">
          <span class="ms ${dir ? "d" : ""}">${dir ? (t === "ld" ? "folder_special" : "folder") : t === "l" ? "link" : "draft"}</span>
          <span class="grow">${esc(n)}</span><span class="num small">${dir ? "" : fmtBytes(size)}</span><span class="num small hide-mobile">${new Date(mtime * 1000).toLocaleDateString("de-DE")} · ${esc(mode)}</span>
          <span class="acts">${dir ? "" : `<button class="icon-btn sm" data-act="dl" title="Herunterladen"><span class="ms">download</span></button>`}
          <button class="icon-btn sm" data-act="mv" title="Umbenennen"><span class="ms">edit</span></button><button class="icon-btn sm" data-act="rm" title="Löschen"><span class="ms">delete</span></button></span></div>`;
      }).join("") || `<div class="muted small pad">Leer</div>`;
  }
  $("fc").onclick = (e) => { const b = e.target.closest("[data-p]"); if (b) open(b.dataset.p); };
  $("fl").onclick = async (e) => {
    const row = e.target.closest(".hp-row");
    if (!row) return;
    const target = row.dataset.dir || row.dataset.file;
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "dl") { location.href = `/api/v1/devices/${d.id}/fs/download?path=${encodeURIComponent(target)}`; return; }
    if (act === "rm") {
      if (!(await confirmDialog("Löschen?", `${target} wird auf dem Gerät gelöscht${row.dataset.dir ? " – mit allem darin" : ""}.`, { ok: "Löschen", danger: true }))) return;
      await attempt(() => api.post(`/devices/${d.id}/fs/delete`, { path: target, recursive: !!row.dataset.dir }), "Gelöscht");
      return open(path);
    }
    if (act === "mv") {
      const to = prompt("Neuer Pfad", target);
      if (to && to !== target) { await attempt(() => api.post(`/devices/${d.id}/fs/rename`, { path: target, to }), "Umbenannt"); open(path); }
      return;
    }
    if (row.dataset.dir) open(row.dataset.dir);
    else editFile(d, target, () => open(path));
  };
  $("fMk").onclick = async () => { const n = prompt("Name des Ordners"); if (n) { await attempt(() => api.post(`/devices/${d.id}/fs/mkdir`, { path: join(path, n) })); open(path); } };
  $("fNew").onclick = () => { const n = prompt("Name der Datei"); if (n) editFile(d, join(path, n), () => open(path), true); };
  $("fUp").onclick = () => $("fFile").click();
  $("fFile").onchange = async () => {
    for (const f of $("fFile").files) {
      toast(`Lade ${f.name} hoch …`);
      const r = await fetch(`/api/v1/devices/${d.id}/fs/upload?path=${encodeURIComponent(join(path, f.name))}`, { method: "POST", body: f, credentials: "same-origin" });
      toast(r.ok ? `${f.name} hochgeladen` : `${f.name}: ${(await r.json().catch(() => ({}))).detail || r.status}`);
    }
    $("fFile").value = "";
    open(path);
  };
  await open(path);
  return {};
}

export async function editFile(d, path, after, isNew = false) {
  let r = { content: "", binary: false, truncated: false };
  if (!isNew) {
    r = await api.get(`/devices/${d.id}/fs/read`, { path }).catch((e) => { toast(e.message); return null; });
    if (!r) return;
  }
  const ro = r.binary || r.truncated;
  const m = openModal(`${sheetHead(path.split("/").pop(), "edit_document")}<div class="muted small break">${esc(path)}</div>
    ${ro ? `<div class="hint"><span class="ms">info</span>${r.binary ? "Binärdatei – nur herunterladen." : "Zu groß zum Bearbeiten (über 2 MB) – herunterladen."}</div>` : `<textarea class="hp-editor" id="edT" spellcheck="false">${esc(r.content)}</textarea>`}
    <div class="sheet-actions"><a class="btn-tonal" href="/api/v1/devices/${d.id}/fs/download?path=${encodeURIComponent(path)}"><span class="ms">download</span>Herunterladen</a>
    <button class="btn-tonal" data-close>Schließen</button>${ro ? "" : `<button class="btn-primary" id="edS"><span class="ms">save</span>Speichern</button>`}</div>`, { wide: true, tall: true });
  const t = m.el.querySelector("#edT");
  t?.addEventListener("keydown", (e) => {
    if (e.key === "Tab") { e.preventDefault(); t.setRangeText("\t", t.selectionStart, t.selectionEnd, "end"); }
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); m.el.querySelector("#edS").click(); }
  });
  const s = m.el.querySelector("#edS");
  if (s) s.onclick = () => attempt(async () => { await api.post(`/devices/${d.id}/fs/write`, { path, content: t.value }); after?.(); }, "Gespeichert");
}

// ---- Docker ----
async function dockerTab(d, body) {
  body.innerHTML = `<section class="card"><div class="card-head"><h3><span class="ms">deployed_code</span>Docker</h3><button class="icon-btn" id="dkR" title="Neu laden"><span class="ms">refresh</span></button></div><div id="dk" class="hp-list"><div class="muted small pad">Lade …</div></div></section>`;
  async function load() {
    const r = await api.get(`/devices/${d.id}/docker`).catch((e) => { $("dk").innerHTML = `<div class="hint"><span class="ms">error</span>${esc(e.message)}</div>`; return null; });
    if (!r) return;
    if (!r.docker) { $("dk").innerHTML = `<div class="hint"><span class="ms">info</span>Auf diesem Gerät ist kein Docker installiert.</div>`; return; }
    const ctr = (c) => {
      const [ic, lbl, cls] = ctrState(c);
      return `<div class="hp-ctr"><span class="ms st-${cls}">${ic}</span><span class="grow"><b>${esc(c.service || c.name)}</b> <span class="muted small">${esc(c.image)} · ${esc(c.status)}</span></span>
        <button class="btn-tonal sm" data-c="${esc(c.name)}" data-a="logs"><span class="ms">article</span>Logs</button>
        ${c.state === "running" ? `<button class="btn-tonal sm" data-c="${esc(c.name)}" data-a="restart"><span class="ms">restart_alt</span></button><button class="btn-tonal sm" data-c="${esc(c.name)}" data-a="stop"><span class="ms">stop</span></button>`
          : `<button class="btn-tonal sm" data-c="${esc(c.name)}" data-a="start"><span class="ms">play_arrow</span></button>`}</div>`;
    };
    const names = new Set(r.stacks.map((s) => s.name));
    $("dk").innerHTML = r.stacks.map((s) => `<div class="hp-stack"><div class="hp-stack-head"><span class="ms" style="color:var(--accent)">stacks</span><b>${esc(s.name)}</b><span class="muted small">${esc(s.status)}</span><span class="spacer"></span>
        <button class="btn-tonal sm" data-s="${esc(s.name)}" data-a="update"><span class="ms">system_update_alt</span>Aktualisieren</button>
        <button class="btn-tonal sm" data-s="${esc(s.name)}" data-a="restart"><span class="ms">restart_alt</span></button>
        <button class="btn-tonal sm" data-s="${esc(s.name)}" data-a="${/running/.test(s.status) ? "stop" : "up"}"><span class="ms">${/running/.test(s.status) ? "stop" : "play_arrow"}</span></button>
        <button class="btn-tonal sm" data-s="${esc(s.name)}" data-a="files"><span class="ms">edit_document</span>Dateien</button></div>
        ${r.containers.filter((c) => c.stack === s.name).map(ctr).join("")}</div>`).join("") +
      (r.containers.some((c) => !names.has(c.stack)) ? `<div class="hp-stack"><div class="hp-stack-head"><b>Einzelne Container</b></div>${r.containers.filter((c) => !names.has(c.stack)).map(ctr).join("")}</div>` : "") ||
      `<div class="muted small pad">Keine Container.</div>`;
  }
  $("dkR").onclick = load;
  $("dk").onclick = async (e) => {
    const b = e.target.closest("[data-a]");
    if (!b) return;
    const a = b.dataset.a;
    if (b.dataset.c && a === "logs") {
      const r = await attempt(() => api.get(`/devices/${d.id}/docker/logs`, { name: b.dataset.c, tail: 400 }));
      if (r) openModal(`${sheetHead(`Logs: ${b.dataset.c}`, "article")}<pre class="log tall" style="max-height:65vh;overflow:auto">${esc(r.logs)}</pre>`, { wide: true, tall: true });
      return;
    }
    if (b.dataset.s && a === "files") {
      const r = await attempt(() => api.get(`/devices/${d.id}/docker/stack/files`, { name: b.dataset.s }));
      if (r) {
        const m = openModal(`${sheetHead(`Dateien: ${b.dataset.s}`, "edit_document")}<div class="hp-list">${r.files.map((f) => `<div class="hp-row" data-f="${esc(f)}" style="cursor:pointer"><span class="ms">draft</span><span class="grow">${esc(f)}</span></div>`).join("")}</div>`);
        m.el.onclick = (ev) => { const row = ev.target.closest("[data-f]"); if (row) { m.close(); editFile(d, row.dataset.f, null, false); } };
      }
      return;
    }
    if (a === "down" || a === "stop") { if (!(await confirmDialog("Stoppen?", `${b.dataset.s || b.dataset.c} wird gestoppt.`, { ok: "Stoppen", danger: true }))) return; }
    b.disabled = true;
    b.querySelector(".ms").classList.add("spin");
    const r = await attempt(() => b.dataset.s ? api.post(`/devices/${d.id}/docker/stack`, { name: b.dataset.s, action: a }) : api.post(`/devices/${d.id}/docker/container`, { name: b.dataset.c, action: a }));
    if (r) {
      toast(r.ok ? "Erledigt" : "Fehlgeschlagen");
      if (!r.ok || a === "update") openModal(`${sheetHead(r.ok ? "Fertig" : "Fehler", r.ok ? "check_circle" : "error")}<pre class="log tall" style="max-height:60vh;overflow:auto">${esc(r.output)}</pre>`, { wide: true });
    }
    load();
  };
  await load();
  return {};
}

// ---- System ----
async function sysTab(d, body) {
  const i = d.info || {};
  const kv = (k, v) => (v ? `<div class="hp-row"><span class="grow muted">${k}</span><span>${v}</span></div>` : "");
  body.innerHTML = `<div class="hp-cols">
    <section class="card"><div class="card-head"><h3><span class="ms">info</span>Gerät</h3></div><div class="hp-list">
      ${kv("Hostname", esc(i.hostname))}${kv("System", esc(i.os))}${kv("Kernel", esc(i.kernel))}${kv("Architektur", esc(i.arch))}${kv("Prozessor", esc(i.cpu_model))}
      ${kv("Board", esc(i.board))}${kv("Kerne", i.cores)}${kv("RAM", fmtBytes(i.mem_total))}${kv("Agent", `ida-connect ${esc(i.agent || "?")} (${esc(i.mode || "?")})`)}${kv("Verbunden über", i.route === "mesh" ? "<b>Cloudflare Mesh</b>" : i.route === "public" ? "öffentliche Adresse" : "")}
      ${kv("Gestartet", i.boot ? new Date(i.boot * 1000).toLocaleString("de-DE") : "")}${kv("Gekoppelt", new Date(d.created * 1000).toLocaleString("de-DE"))}</div></section>
    <section class="card"><div class="card-head"><h3><span class="ms">system_update</span>Updates</h3><button class="btn-tonal sm" id="upChk"><span class="ms">search</span>Prüfen</button></div>
      <div id="upInfo" class="muted small">Noch nicht geprüft.</div>
      <div class="btn-row"><button class="btn-primary" id="upSys"><span class="ms">system_update_alt</span>System aktualisieren</button>
      <button class="btn-tonal" id="upReboot"><span class="ms">restart_alt</span>Neu starten</button></div>
      <div class="hp-sub-head"><b>ida-connect</b> <span class="muted small">Version ${esc(i.agent || "?")}</span></div>
      <div id="agInfo" class="muted small">${i.update?.available ? `<b>Neue Version ${esc(i.update.latest)}</b> ist da.` : i.update?.latest ? "Aktuell." : "Noch nicht geprüft."}</div>
      <div class="btn-row"><button class="btn-tonal" id="agChk"><span class="ms">search</span>Nach Update suchen</button>
        <button class="btn-primary" id="upAgent" ${i.update?.available ? "" : "hidden"}><span class="ms">upgrade</span>Jetzt installieren</button>
        <label class="hp-check"><input type="checkbox" id="agAuto" ${i.auto_update !== false ? "checked" : ""}> Auto-Update (alle 6 h)</label></div>
      <small class="muted">Kopplung und Einstellungen bleiben beim Update. Startet die neue Version nicht, kommt die alte zurück. Auf dem Gerät: <code>sudo ida-connect update</code></small></section>
    <section class="card"><div class="card-head"><h3><span class="ms">hub</span>Cloudflare Mesh</h3></div>
      <div class="hp-list">${kv("Status", esc(i.mesh?.status || "unbekannt"))}${kv("Mesh-IP", i.mesh?.ip ? `<b>${esc(i.mesh.ip)}</b>` : "")}</div>
      ${i.mesh?.supported === false ? `<div class="hint"><span class="ms">info</span>32-Bit-Gerät: Cloudflare Mesh gibt es nur für 64-Bit (x86_64, aarch64).</div>` :
        `<p class="muted small">Mit Mesh bekommt das Gerät eine eigene IP im Cloudflare-Netz – alles, was darauf läuft, ist dann über WARP erreichbar.</p>
         <div class="btn-row">${S.me.mesh ? `<button class="btn-primary" id="msGo"><span class="ms">hub</span>${i.mesh?.status === "verbunden" ? "Neu einrichten" : "Einrichten"}</button>` : ""}
         <button class="btn-tonal" id="msTok"><span class="ms">key</span>Mit eigenem Token</button></div>`}</section>
    <section class="card"><div class="card-head"><h3><span class="ms">edit</span>Name & Notizen</h3></div><div class="hp-form">
      <label>Name<input class="cd-input" id="dvName" value="${esc(d.name)}" maxlength="60"></label>
      <label>Notizen<textarea class="cd-input" id="dvNotes" rows="3">${esc(d.notes || "")}</textarea></label>
      <div class="btn-row"><button class="btn-primary" id="dvSave"><span class="ms">save</span>Speichern</button><span class="spacer"></span><button class="btn-danger" id="dvDel"><span class="ms">delete</span>Gerät entfernen</button></div></div></section>
    <section class="card" style="grid-column:1/-1"><div class="card-head"><h3><span class="ms">history</span>Protokoll</h3></div><div class="table-wrap"><table class="utable" id="au"></table></div></section></div>`;
  const out = (title, r) => openModal(`${sheetHead(title, r.ok === false ? "error" : "check_circle")}${r.reboot ? `<div class="hint"><span class="ms">restart_alt</span>Ein Neustart ist nötig.</div>` : ""}<pre class="log tall" style="max-height:60vh;overflow:auto">${esc(r.output || "")}</pre>`, { wide: true });
  const busy = async (btn, fn) => { btn.disabled = true; btn.querySelector(".ms")?.classList.add("spin"); try { return await attempt(fn); } finally { btn.disabled = false; btn.querySelector(".ms")?.classList.remove("spin"); } };
  $("upChk").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api.get(`/devices/${d.id}/updates`);
    $("upInfo").innerHTML = r.pending < 0 ? "Paketmanager nicht erkannt." : `${r.pending ? `<b>${r.pending}</b> Pakete können aktualisiert werden` : "Alles aktuell"}${r.reboot ? " · Neustart nötig" : ""}.`;
  });
  $("upSys").onclick = async (e) => {
    if (!(await confirmDialog("System aktualisieren?", "Installiert alle Paket-Updates (apt/dnf/apk/pacman). Das kann einige Minuten dauern."))) return;
    const r = await busy(e.currentTarget, () => api.post(`/devices/${d.id}/update`, { target: "system" }));
    if (r) out("Systemupdate", r);
  };
  $("upAgent").onclick = (e) => busy(e.currentTarget, async () => { await api.post(`/devices/${d.id}/update`, { target: "agent" }); toast("Update läuft – das Gerät verbindet sich gleich neu"); });
  $("agChk").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api.get(`/devices/${d.id}/agent-update`);
    $("agInfo").innerHTML = r.error ? `Nachsehen ging nicht: ${esc(r.error)}` : r.available ? `<b>Neue Version ${esc(r.latest)}</b> ist da (installiert: ${esc(r.current)}).` : `Aktuell (${esc(r.current)}).`;
    $("upAgent").hidden = !r.available;
  });
  $("agAuto").onchange = async (e) => {
    const on = e.target.checked;
    if (!(await attempt(() => api.post(`/devices/${d.id}/auto-update`, { on }), on ? "Auto-Update an" : "Auto-Update aus"))) e.target.checked = !on;
  };
  $("upReboot").onclick = async () => {
    if (!(await confirmDialog("Neu starten?", `${d.name} startet neu und ist kurz offline.`, { ok: "Neu starten", danger: true }))) return;
    attempt(() => api.post(`/devices/${d.id}/exec`, { cmd: "(sleep 2; systemctl reboot || reboot) >/dev/null 2>&1 &", timeout: 10 }), "Startet neu …");
  };
  const mesh = (btn, token = "") => busy(btn, async () => { toast("Mesh wird eingerichtet – ein bis zwei Minuten …"); const r = await api.post(`/devices/${d.id}/mesh`, { token }); out("Cloudflare Mesh", { ok: true, output: r.output }); });
  $("msGo") && ($("msGo").onclick = (e) => mesh(e.currentTarget));
  $("msTok") && ($("msTok").onclick = (e) => { const t = prompt("Mesh-Token (Cloudflare → Networking → Mesh → Knoten)"); if (t) mesh(e.currentTarget, t.trim()); });
  $("dvSave").onclick = () => attempt(async () => {
    const r = await fetch(`/api/v1/devices/${d.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, credentials: "same-origin", body: JSON.stringify({ name: $("dvName").value, notes: $("dvNotes").value }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Fehler ${r.status}`);
    Object.assign(d, await r.json());
    app.querySelector(".hp-title").textContent = d.name;
  }, "Gespeichert");
  $("dvDel").onclick = async () => {
    if (!(await confirmDialog("Gerät entfernen?", `${d.name} wird aus Health gelöscht – mit allen Messwerten. ida-connect auf dem Gerät verbindet sich danach nicht mehr (dort neu koppeln oder deinstallieren).`, { ok: "Entfernen", danger: true }))) return;
    if (await attempt(() => api.del(`/devices/${d.id}`), "Entfernt")) { S.devices = S.devices.filter((x) => x.id !== d.id); location.hash = "#/"; }
  };
  const au = await api.get(`/devices/${d.id}/audit`).catch(() => ({ items: [] }));
  $("au").innerHTML = `<thead><tr><th>Zeit</th><th>Wer</th><th>Was</th><th>Details</th></tr></thead><tbody>${au.items.map((a) => `<tr><td class="num">${new Date(a.ts * 1000).toLocaleString("de-DE")}</td><td>${esc(a.who)}</td><td>${esc(a.action)}</td><td class="mono small break">${esc(a.detail.slice(0, 300))}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">Noch nichts.</td></tr>`}</tbody>`;
  return {};
}

// ---------------- Start ----------------
(async () => {
  S.me = (await api.get("/me").catch(() => ({}))) || {};
  if (!S.me.who) { loginView(app, S.me, () => location.reload()); return; }
  connectLive();
  await route();
})();
