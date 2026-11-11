// Stacks: alle Docker-Stacks des Servers. Seitenleiste mit Suche/Filter, Übersicht als Karten,
// Detail mit Aktionen (Start/Stopp/Neustart/Aktualisieren …), Containern, Logs und Editor für Compose und .env.
import { api, followJob } from "../api.js";
import { COLORS, lineChart } from "../chart.js";
import { navigate } from "../app.js";
import { liveIndex, live, stacks } from "../store.js";
import { attempt, confirmDialog, ctrState, esc, fmtBytes, fmtPct, fmtRate, openModal, popMenu, sheetHead, STATES, toast } from "../ui.js";

const FILTERS = [["all", "Alle"], ["running", "Läuft"], ["issue", "Probleme"], ["stopped", "Aus"]];
let q = "", filter = "all";

const ACTIONS = {
  start: { icon: "play_arrow", label: "Starten" },
  stop: { icon: "stop", label: "Stoppen", confirm: "Alle Container dieses Stacks werden gestoppt." },
  restart: { icon: "restart_alt", label: "Neu starten", confirm: "Alle Container dieses Stacks werden neu gestartet." },
  update: { icon: "system_update_alt", label: "Aktualisieren", confirm: "Neue Images werden geladen (docker compose pull) und die Container neu erstellt (up -d)." },
  up: { icon: "published_with_changes", label: "Übernehmen", sub: "docker compose up -d – Änderungen an Compose/.env anwenden" },
  down: { icon: "power_settings_new", label: "Herunterfahren", sub: "docker compose down – Container entfernen (Volumes bleiben)", confirm: "Alle Container des Stacks werden entfernt. Daten in Volumes bleiben erhalten.", danger: true },
};

const stateOf = (s) => STATES[s.state] || STATES.stopped;
const matches = (s) => (!q || s.name.toLowerCase().includes(q.toLowerCase()) || s.containers.some((c) => c.image.toLowerCase().includes(q.toLowerCase())))
  && (filter === "all" || (filter === "issue" ? ["problem", "partial"].includes(s.state) : filter === "running" ? s.state === "running" : s.state === "stopped"));

export default {
  id: "stacks", label: "Stacks", icon: "deployed_code", path: "/stacks",
  match(p) {
    const m = /^\/stacks(?:\/([^/]+))?$/.exec(p);
    return m ? { name: m[1] ? decodeURIComponent(m[1]) : null } : null;
  },

  side(box, params) {
    box.innerHTML = `<div class="sidebar-head" id="sideHead">
        <div class="brand-row"><span class="brand-title">Stacks</span><span class="count-pill" id="stCount"></span></div>
        <label class="side-search"><span class="ms">search</span><input id="stQ" type="search" placeholder="Stack oder Image suchen" autocomplete="off" value="${esc(q)}"></label>
        <div class="filter-row" id="stFilter">${FILTERS.map(([k, l]) => `<button type="button" class="fchip ${filter === k ? "on" : ""}" data-f="${k}">${l}</button>`).join("")}</div>
      </div><div class="tree-scroll" id="stList"><div class="side-empty">Lade …</div></div>`;
    const measure = () => box.style.setProperty("--sh", `${box.querySelector("#sideHead").offsetHeight}px`);
    let list = null;
    const draw = () => {
      if (!list) return;
      const idx = liveIndex(live.data).byStack;
      const shown = list.filter(matches);
      box.querySelector("#stCount").textContent = `${list.length}`;
      box.querySelector("#stList").innerHTML = shown.map((s) => {
        const [ic, , cls] = stateOf(s);
        const l = idx[s.name];
        return `<a class="side-item ${params.name === s.name ? "active" : ""}" href="/stacks/${encodeURIComponent(s.name)}">
          <span class="ms st-${cls}">${s.job ? "progress_activity" : ic}</span>
          <span class="si-main"><span class="tree-label">${esc(s.name)}</span><small>${s.total ? `${s.running}/${s.total} laufen` : "aus"}${l ? ` · ${fmtBytes(l.mem)}` : ""}</small></span>
          ${l ? `<span class="tree-count">${fmtPct(l.cpu, 1)}</span>` : ""}</a>`;
      }).join("") || `<div class="side-empty">${list.length ? "Nichts gefunden." : "Keine Stacks gefunden."}</div>`;
      box.querySelector(".side-item.active")?.scrollIntoView({ block: "nearest" });
    };
    box.querySelector("#stQ").oninput = (e) => { q = e.target.value; draw(); document.dispatchEvent(new Event("stacks:filter")); };
    box.querySelector("#stFilter").onclick = (e) => {
      const b = e.target.closest("[data-f]");
      if (!b) return;
      filter = b.dataset.f;
      box.querySelectorAll(".fchip").forEach((x) => x.classList.toggle("on", x === b));
      draw();
      document.dispatchEvent(new Event("stacks:filter"));
    };
    requestAnimationFrame(measure);
    const u1 = stacks.sub((d) => { list = d; draw(); });
    const u2 = live.sub(() => draw());
    return () => { u1(); u2(); };
  },

  mount(el, params, ctx) {
    return params.name ? detail(el, params.name, ctx) : grid(el, ctx);
  },
};

// ---------- Übersicht als Karten ----------
function grid(el, ctx) {
  ctx.top(`<div class="topbar-left"><button type="button" class="icon-btn mobile-only" data-drawer aria-label="Liste"><span class="ms">menu</span></button>
    <h2><span class="ms">deployed_code</span><span class="tb-title">Stacks</span></h2></div>
    <div class="topbar-actions"><button type="button" class="icon-btn" id="stRefresh" title="Neu laden"><span class="ms">refresh</span></button>
      <button type="button" class="btn-primary act-btn" id="stNew" title="Neuen Stack mit eigener Compose-Datei einrichten"><span class="ms">add</span><span class="lbl">Neuer Stack</span></button></div>`);
  document.getElementById("stRefresh").onclick = () => stacks.refresh();
  document.getElementById("stNew").onclick = () => newStack();
  el.innerHTML = `<div class="page"><div class="filter-row mobile-only-flex" id="gFilter"></div><div class="stack-grid" id="stGrid"><div class="empty-stage">Lade …</div></div></div>`;
  let list = null;
  const draw = () => {
    if (!list) return;
    const idx = liveIndex(live.data).byStack;
    const shown = list.filter(matches);
    el.querySelector("#gFilter").innerHTML = FILTERS.map(([k, l]) => `<button type="button" class="fchip ${filter === k ? "on" : ""}" data-f="${k}">${l}</button>`).join("");
    el.querySelector("#stGrid").innerHTML = shown.map((s) => {
      const [ic, label, cls] = stateOf(s);
      const l = idx[s.name];
      return `<a class="stack-card" href="/stacks/${encodeURIComponent(s.name)}">
        <div class="sc-top"><span class="sc-ic st-bg-${cls}"><span class="ms">${s.kind === "compose" ? "stacks" : "deployed_code"}</span></span>
          <span class="state-pill ${cls}"><span class="ms ${s.job ? "spin" : ""}">${s.job ? "progress_activity" : ic}</span>${s.job ? esc(s.job.title.split(":")[0]) : label}</span></div>
        <div class="sc-main"><b>${esc(s.name)}</b><small>${s.dir ? esc(s.dir) : esc(s.containers[0]?.image || "")}</small></div>
        <div class="sc-foot"><span><span class="ms sm">view_in_ar</span>${s.running}/${s.total}</span>
          ${l ? `<span><span class="ms sm">memory</span>${fmtPct(l.cpu, 1)}</span><span><span class="ms sm">memory_alt</span>${fmtBytes(l.mem)}</span>` : ""}
          ${s.self ? `<span class="pill">Dashboard</span>` : ""}</div></a>`;
    }).join("") || `<div class="empty-stage">Nichts gefunden.</div>`;
  };
  el.querySelector("#gFilter").onclick = (e) => {
    const b = e.target.closest("[data-f]");
    if (b) { filter = b.dataset.f; draw(); document.querySelectorAll("#stFilter .fchip").forEach((x) => x.classList.toggle("on", x.dataset.f === filter)); }
  };
  const onFilter = () => draw();
  document.addEventListener("stacks:filter", onFilter);
  const u1 = stacks.sub((d) => { list = d; draw(); });
  const u2 = live.sub(() => draw());
  return () => { u1(); u2(); document.removeEventListener("stacks:filter", onFilter); };
}

// ---------- Neuer Stack: Compose einfügen oder hochladen ----------
const SAMPLE = `services:
  app:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "8080:80"
`;

// Name aus dem Dateinamen bzw. „name:“ der Compose-Datei vorschlagen
const slug = (t) => String(t || "").toLowerCase().replace(/\.(ya?ml|env)$/, "").replace(/[^a-z0-9_-]+/g, "-").replace(/^[-_]+|-+$/g, "").slice(0, 63);
const DEFAULT_FILES = /^(docker-)?compose(\.[a-z0-9_-]+)?\.ya?ml$/i;

function newStack() {
  const m = openModal(`${sheetHead("Neuer Stack", "add_box")}<form class="ff-form" novalidate><div class="ff-scroll">
      <label class="ff"><span>Name</span><input class="cd-input" id="nsName" placeholder="z. B. uptime-kuma" autocomplete="off" spellcheck="false" autocapitalize="off">
        <small class="ff-hint" id="nsWhere">Kleinbuchstaben, Ziffern, - und _. Daraus wird der Ordner im Stack-Verzeichnis.</small></label>
      <div class="ff"><span>Compose-Datei</span>
        <div class="ns-tools"><button type="button" class="btn-tonal" data-up="compose"><span class="ms">upload_file</span><span class="lbl">Hochladen</span></button>
          <button type="button" class="btn-tonal" data-a="paste"><span class="ms">content_paste</span><span class="lbl">Einfügen</span></button>
          <button type="button" class="btn-tonal" data-a="sample"><span class="ms">draft</span><span class="lbl">Beispiel</span></button></div>
        <textarea class="cd-input ns-drop" id="nsCompose" rows="14" spellcheck="false" wrap="off" placeholder="Compose hier einfügen oder eine Datei hierher ziehen"></textarea>
        <small class="ff-hint" id="nsInfo">Gespeichert als compose.yaml. Ziehen &amp; Ablegen geht auch.</small></div>
      <details class="ns-env"><summary><span class="ms">key</span>.env-Datei (optional)</summary>
        <div class="ff"><div class="ns-tools"><button type="button" class="btn-tonal" data-up="env"><span class="ms">upload_file</span><span class="lbl">.env hochladen</span></button></div>
          <textarea class="cd-input ns-drop" id="nsEnv" rows="6" spellcheck="false" wrap="off" placeholder="SCHLUESSEL=wert"></textarea>
          <small class="ff-hint">Für Passwörter und Einstellungen, auf die die Compose-Datei mit \${NAME} zeigt.</small></div></details>
      <div class="ff ff-switch"><div><span>Gleich starten</span><small class="ff-hint">Prüft die Datei, lädt die Images und startet (docker compose up -d)</small></div>
        <button type="button" class="sw on" id="nsStart" aria-label="Gleich starten"></button></div>
      <div class="ff-error" id="ffErr" hidden></div></div>
      <input type="file" id="nsFile" hidden accept=".yml,.yaml,.env,.txt,text/yaml,text/plain">
      <div class="sheet-actions"><button type="button" class="btn-tonal" data-close>Abbrechen</button>
        <button class="btn-primary" type="submit" id="nsOk"><span class="ms">rocket_launch</span><span class="lbl">Einrichten</span></button></div></form>`, { wide: true });
  const box = m.el, $ = (s) => box.querySelector(s);
  const name = $("#nsName"), compose = $("#nsCompose"), env = $("#nsEnv"), file = $("#nsFile"), err = $("#ffErr");
  let nameTouched = false, target = "compose", start = true;

  const info = () => {
    const t = compose.value;
    const svc = /^services:\s*$/m.test(t) ? [...t.split(/^services:\s*$/m)[1].matchAll(/^ {2}([A-Za-z0-9_.-]+):/gm)].map((x) => x[1]) : [];
    $("#nsInfo").textContent = !t.trim() ? "Gespeichert als compose.yaml. Ziehen & Ablegen geht auch."
      : svc.length ? `Dienste: ${svc.join(", ")}` : "Kein Abschnitt „services:“ gefunden";
    const declared = /^name:\s*["']?([A-Za-z0-9_-]+)/m.exec(t)?.[1];
    if (declared && !nameTouched) name.value = slug(declared);
  };
  const fill = (which, text, fname = "") => {
    if (which === "env") { env.value = text; box.querySelector(".ns-env").open = true; return; }
    compose.value = text;
    if (!nameTouched && fname && !DEFAULT_FILES.test(fname)) name.value = slug(fname);
    info();
  };
  const readFile = async (f, which) => {
    if (f.size > 2_000_000) return toast("Die Datei ist zu groß");
    const text = await f.text();
    fill(which === "auto" ? (/\.env$|^\.env/i.test(f.name) ? "env" : "compose") : which, text, f.name);
  };

  name.oninput = () => { nameTouched = !!name.value; };
  compose.oninput = info;
  compose.onkeydown = env.onkeydown = (e) => {
    if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); e.target.setRangeText("  ", e.target.selectionStart, e.target.selectionEnd, "end"); }
  };
  for (const ta of [compose, env]) {
    ta.ondragover = (e) => { e.preventDefault(); ta.classList.add("over"); };
    ta.ondragleave = () => ta.classList.remove("over");
    ta.ondrop = (e) => {
      ta.classList.remove("over");
      const f = e.dataTransfer?.files?.[0];
      if (!f) return;
      e.preventDefault();
      readFile(f, ta === env ? "env" : "auto");
    };
  }
  file.onchange = () => { if (file.files[0]) readFile(file.files[0], target); file.value = ""; };
  box.addEventListener("click", async (e) => {
    const up = e.target.closest("[data-up]");
    if (up) { target = up.dataset.up; file.click(); return; }
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "sample") compose.value.trim() ? toast("Erst das Feld leeren – das Beispiel ersetzt sonst deinen Text") : fill("compose", SAMPLE);
    if (a === "paste") {
      try { fill("compose", await navigator.clipboard.readText()); } catch { compose.focus(); toast("Kein Zugriff auf die Zwischenablage – mit Strg+V einfügen"); }
    }
    if (e.target.closest("#nsStart")) { start = !start; $("#nsStart").classList.toggle("on", start); }
  });

  $("form").onsubmit = async (e) => {
    e.preventDefault();
    err.hidden = true;
    const n = slug(name.value);
    if (!n) { err.textContent = "Gib dem Stack einen Namen."; err.hidden = false; return name.focus(); }
    if (!compose.value.trim()) { err.textContent = "Füg eine Compose-Datei ein oder lad eine hoch."; err.hidden = false; return compose.focus(); }
    const btn = $("#nsOk");
    btn.disabled = true;
    try {
      const r = await api.post("/stacks", { name: n, compose: compose.value, env: env.value, start });
      m.close();
      toast(r.job ? `${n} wird eingerichtet …` : `${n} angelegt`);
      await stacks.refresh();
      navigate(`/stacks/${encodeURIComponent(r.name)}`);
    } catch (x) {
      err.textContent = x.message || "Fehler";
      err.hidden = false;
      btn.disabled = false;
    }
  };
  if (!matchMedia("(pointer: coarse)").matches) name.focus();
}

// ---------- Detail eines Stacks ----------
// ---------- Adressen, Netze und Daten eines Containers ----------
const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const infoRow = (icon, title, body) => `<div class="info-row"><span class="ms" title="${esc(title)}">${icon}</span><div class="info-main">${body}</div></div>`;
const infoBlock = (title, rows) => `<div class="info-block"><h4>${title}</h4>${rows.join("")}</div>`;

function portRow(p) {
  const proto = p.proto === "tcp" ? "" : ` <span class="muted small">${esc(p.proto)}</span>`;
  if (!p.public) return infoRow("lock", "Nur im Docker-Netz", `<span class="mono">${p.private}</span>${proto} <span class="pill">nur intern</span>`);
  const where = p.ip === "127.0.0.1" ? "nur auf dem Server" : p.ip === "0.0.0.0" || !p.ip ? "alle Adressen des Servers" : "";
  const host = !p.ip || p.ip === "0.0.0.0" ? "" : p.ip.includes(":") ? `[${p.ip}]:` : `${p.ip}:`;
  return infoRow("settings_ethernet", "Port", `<span class="mono">${esc(host)}${p.public}</span> <span class="ms sm muted">arrow_forward</span> <span class="mono">${p.private}</span>${proto}${where ? ` <span class="pill${where.startsWith("alle") ? " warn" : ""}">${where}</span>` : ""}`);
}

function mountRow(m) {
  const volume = m.type === "volume";
  const icon = volume ? "inventory_2" : m.type === "tmpfs" ? "memory" : "folder";
  const from = volume ? `${esc(m.name)}` : esc(m.source || m.type);
  return infoRow(icon, volume ? "Docker-Volume" : m.type === "tmpfs" ? "Im Arbeitsspeicher" : "Ordner vom Server", `<span class="mono">${from}</span>
    <small class="mono"><span class="ms sm">subdirectory_arrow_right</span>${esc(m.target)}</small>${volume && m.source ? `<small class="mono muted">${esc(m.source)}</small>` : ""}${m.rw ? "" : ` <span class="pill">nur lesen</span>`}`);
}

// Schutz einer öffentlichen Adresse: Cloudflare Zero Trust (Access) davor oder für jeden offen
const ACCESS = {
  access: ["lock", "Zero Trust", "pill ok"],
  public: ["public", "öffentlich", "pill warn"],
  unknown: ["help", "Schutz unbekannt", "pill"],
};
function urlRow(u) {
  const [icon, label, cls] = ACCESS[u.access] || ACCESS.unknown;
  const src = u.access_source === "api" ? "laut Cloudflare-API" : "Test ohne Login";
  const pill = u.access ? ` <span class="${cls}" title="${esc(`${u.access_detail || ""} (${src})`)}"><span class="ms sm">${icon}</span>${label}</span>` : "";
  return infoRow(u.access === "access" ? "lock" : "public", "Öffentliche Adresse", `<a class="mono" href="${esc(u.url)}" target="_blank" rel="noopener">${esc(u.url.replace(/^https:\/\//, ""))}</a>${pill}`);
}

function infoHtml(c, urls, open) {
  const addr = [...urls.map(urlRow), ...(c.port_list || []).map(portRow)];
  const nets = (c.networks || []).map((n) => infoRow("hub", "Netzwerk", `<span class="mono">${esc(n.name)}</span>${n.ip ? ` <small class="mono muted">${esc(n.ip)}</small>` : ""}`));
  const data = (c.mounts || []).map(mountRow);
  const off = c.state !== "running" ? `<div class="muted small">Läuft gerade nicht, deshalb sind Ports und IPs eventuell leer.</div>` : "";
  const sum = [urls.length ? count(urls.length, "URL", "URLs") : "", c.port_list?.length ? count(c.port_list.length, "Port", "Ports") : "", nets.length ? count(nets.length, "Netz", "Netze") : "", data.length ? count(data.length, "Mount", "Mounts") : ""].filter(Boolean).join(" · ");
  return `<details class="info-ctr" ${open ? "open" : ""}><summary><span class="ms chev">chevron_right</span><span class="info-title"><b>${esc(c.service)}</b><small>${esc(sum || "Keine Ports, Netze oder Mounts")}</small></span></summary>
    <div class="info-body">${off}${infoBlock("Image", [infoRow("deployed_code", "Image", `<span class="mono">${esc(c.image)}</span>`)])}
    ${addr.length ? infoBlock("Adressen", addr) : ""}${nets.length ? infoBlock("Netzwerke", nets) : ""}${data.length ? infoBlock("Daten", data) : ""}</div></details>`;
}

function detail(el, name, ctx) {
  let s = null, files = [], filesDrawn = false, fileIdx = 0, dirty = false, original = "", jobAbort = null, followed = null, chart = null;
  const alive = { v: true };
  el.innerHTML = `<div class="page">
    <section class="card job-card" id="sdJob" hidden></section>
    <section class="card"><div class="card-head"><h3><span class="ms">view_in_ar</span>Container</h3><span class="muted" id="sdCount"></span></div><div id="sdCtrs" class="ctr-list"></div></section>
    <section class="card" id="sdInfoCard" hidden><div class="card-head"><h3><span class="ms">lan</span>Adressen, Netze und Daten</h3></div><div id="sdInfo" class="info-list"></div></section>
    <section class="card"><div class="card-head"><h3><span class="ms">monitoring</span>Verbrauch (24 Std)</h3>
      <div class="seg-mini" id="sdMetric"><button type="button" class="on" data-m="cpu">CPU</button><button type="button" data-m="mem">RAM</button><button type="button" data-m="net">Netz</button></div></div>
      <div id="sdChart" class="chart-box"></div></section>
    <section class="card" id="sdFilesCard"><div class="card-head"><h3><span class="ms">description</span>Dateien</h3><span class="muted mono small" id="sdDir"></span></div><div id="sdFiles"></div></section>
  </div>`;

  function drawTop() {
    const [ic, label, cls] = s ? stateOf(s) : ["hourglass_top", "…", "off"];
    const busy = !!s?.job;
    const can = (a) => s && !busy && (s.files_ok || ["start", "stop", "restart"].includes(a)) && !(s.self && a === "down");
    const b = (a, primary) => `<button type="button" class="${primary ? "btn-primary" : "btn-tonal"} act-btn" data-act="${a}" ${can(a) ? "" : "disabled"} title="${ACTIONS[a].label}"><span class="ms">${ACTIONS[a].icon}</span><span class="lbl">${ACTIONS[a].label}</span></button>`;
    const running = s && s.running > 0;
    ctx.top(`<div class="topbar-left"><a class="icon-btn" href="/stacks" aria-label="Zurück"><span class="ms">arrow_back</span></a>
      <div class="tb-stack"><span class="tb-title">${esc(name)}</span><span class="tb-sub"><span class="ms st-${cls} ${busy ? "spin" : ""}">${busy ? "progress_activity" : ic}</span><span>${busy ? esc(s.job.title) : `${label}${s ? ` · ${s.running}/${s.total} Container` : ""}`}</span></span></div></div>
      <div class="topbar-actions">${running ? b("restart") + b("stop") : b("start", true)}${b("update")}
        <button type="button" class="icon-btn" id="sdMore" aria-label="Mehr" ${s ? "" : "disabled"}><span class="ms">more_vert</span></button></div>`);
  }

  function drawCtrs() {
    const idx = liveIndex(live.data).byName;
    el.querySelector("#sdCount").textContent = s ? `${s.running} von ${s.total} laufen` : "";
    el.querySelector("#sdCtrs").innerHTML = !s ? "Lade …" : s.containers.length ? s.containers.map((c) => {
      const [ic, label, cls] = ctrState(c);
      const l = idx[c.name];
      return `<div class="ctr-row">
        <span class="ctr-ic st-bg-${cls}"><span class="ms">${ic}</span></span>
        <div class="ctr-main"><b>${esc(c.service)}${c.service !== c.name ? ` <small class="muted">${esc(c.name)}</small>` : ""}${c.self ? ` <span class="pill">Dashboard</span>` : ""}</b>
          <small><span class="st-${cls}">${label}</span> · ${esc(c.status)}</small>
          <small class="mono">${esc(c.image)}${c.ports.length ? ` · ${c.ports.map(esc).join(", ")}` : ""}</small></div>
        ${l ? `<div class="ctr-live"><span title="CPU (Anteil am ganzen Server)"><span class="ms sm">memory</span>${fmtPct(l.cpu, 2)}</span><span title="RAM"><span class="ms sm">memory_alt</span>${fmtBytes(l.mem)}</span>
          <span title="Netz ↓/↑"><span class="ms sm">swap_vert</span>${fmtRate(l.net_rx)}</span></div>` : ""}
        <button type="button" class="icon-btn sm" data-logs="${c.id}" title="Logs"><span class="ms">article</span></button>
        <button type="button" class="icon-btn sm" data-cmenu="${c.id}" aria-label="Aktionen"><span class="ms">more_vert</span></button></div>`;
    }).join("") : `<div class="muted pad">Gerade keine Container – mit „Starten“ wird der Stack hochgefahren.</div>`;
  }

  // nur neu zeichnen, wenn sich etwas geändert hat: sonst klappen offene Container bei jedem Messwert zu
  let infoKey = "";
  function drawInfo() {
    const key = s ? JSON.stringify([s.urls, s.containers.map((c) => [c.id, c.image, c.state, c.port_list, c.networks, c.mounts])]) : "";
    if (key === infoKey) return;
    infoKey = key;
    el.querySelector("#sdInfoCard").hidden = !s?.containers.length;
    if (s) el.querySelector("#sdInfo").innerHTML = s.containers.map((c) => infoHtml(c, s.urls?.[c.id] || [], s.containers.length === 1)).join("");
  }

  async function loadDetail() {
    const d = await api.get(`/stacks/${encodeURIComponent(name)}`).catch((e) => { if (e.status === 404) el.innerHTML = `<div class="empty-stage"><span class="ms big">search_off</span><br>Stack „${esc(name)}“ gibt es nicht (mehr).</div>`; return null; });
    if (!d || !alive.v) return;
    s = d;
    if (!filesDrawn || files.map((f) => f.path).join() !== d.files.map((f) => f.path).join()) { files = d.files; filesDrawn = true; drawFiles(); }
    drawTop(); drawCtrs(); drawInfo();
    if (d.job && followed !== d.job.id) follow(d.job);
  }

  // ---- Vorgang (Live-Ausgabe) ----
  function follow(job) {
    followed = job.id;
    jobAbort?.abort();
    jobAbort = new AbortController();
    const box = el.querySelector("#sdJob");
    box.hidden = false;
    box.innerHTML = `<div class="card-head"><h3><span class="ms spin" id="jIc">progress_activity</span><span id="jTitle">${esc(job.title)}</span></h3>
      <button type="button" class="icon-btn sm" id="jClose" aria-label="Ausblenden"><span class="ms">close</span></button></div><pre class="log" id="jLog"></pre>`;
    box.querySelector("#jClose").onclick = () => { box.hidden = true; };
    const log = box.querySelector("#jLog");
    followJob(job.id, (ev) => {
      if (ev.line != null) {
        const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
        log.textContent += ev.line + "\n";
        if (stick) log.scrollTop = log.scrollHeight;
      }
      if (ev.done) {
        const ic = box.querySelector("#jIc");
        ic.classList.remove("spin");
        ic.textContent = ev.done.ok ? "check_circle" : "error";
        ic.className = `ms st-${ev.done.ok ? "ok" : "err"}`;
        toast(ev.done.ok ? `${job.title} – fertig` : `${job.title} – fehlgeschlagen`);
        stacks.refresh();
        setTimeout(() => { loadDetail(); live.refresh(); }, 800);
      }
    }, jobAbort.signal).catch(() => {});
  }

  async function run(action) {
    const a = ACTIONS[action];
    if (a.confirm && !(await confirmDialog(`${a.label}: ${name}?`, a.confirm, { ok: a.label, danger: a.danger || action === "stop" }))) return;
    if (action === "up" && dirty && !(await saveFile())) return;
    const r = await attempt(() => api.post(`/stacks/${encodeURIComponent(name)}/action`, { action }));
    if (!r) return;
    s = { ...s, job: r.job };
    drawTop();
    follow(r.job);
  }

  // ---- Dateien ----
  function drawFiles() {
    const box = el.querySelector("#sdFiles");
    el.querySelector("#sdDir").textContent = s?.dir || "";
    if (!s) return;
    if (!s.files_ok) {
      box.innerHTML = `<div class="hint"><span class="ms">folder_off</span><div>${s.kind === "compose"
        ? `Die Compose-Dateien liegen in <code>${esc(s.dir || "?")}</code> – dieser Ordner ist im Dashboard nicht eingebunden. Bearbeiten und Aktualisieren gehen erst, wenn er unter <code>STACKS_ROOT</code> liegt (siehe docs/DASHBOARD.md).`
        : "Einzelner Container ohne Compose – hier gibt es keine Dateien. Starten, Stoppen, Neu starten und Logs gehen trotzdem."}</div></div>`;
      return;
    }
    box.innerHTML = `<div class="file-tabs">${files.map((f, i) => `<button type="button" class="ftab ${i === fileIdx ? "on" : ""}" data-fi="${i}">
        <span class="ms">${f.kind === "env" ? "key" : "data_object"}</span>${esc(f.name)}${f.exists ? "" : ` <small>(neu)</small>`}</button>`).join("")}</div>
      <div class="editor-wrap"><div class="gutter" id="edGut"></div><textarea class="editor" id="ed" spellcheck="false" autocapitalize="off" autocomplete="off" wrap="off"></textarea></div>
      <div class="ed-bar"><span class="muted small" id="edState"></span><span class="spacer"></span>
        <button type="button" class="btn-tonal" id="edReset" disabled><span class="ms">undo</span><span class="lbl">Verwerfen</span></button>
        <button type="button" class="btn-tonal" id="edSave" disabled><span class="ms">save</span><span class="lbl">Speichern</span></button>
        <button type="button" class="btn-primary" id="edApply" ${s.self ? 'title="Das Dashboard selbst wird dabei ausgelassen"' : ""}><span class="ms">published_with_changes</span><span class="lbl">Speichern &amp; übernehmen</span></button></div>`;
    const ed = box.querySelector("#ed"), gut = box.querySelector("#edGut");
    const lines = () => { gut.textContent = Array.from({ length: ed.value.split("\n").length }, (_, i) => i + 1).join("\n"); };
    const mark = () => {
      dirty = ed.value !== original;
      box.querySelector("#edSave").disabled = !dirty;
      box.querySelector("#edReset").disabled = !dirty;
      box.querySelector("#edState").textContent = dirty ? "Ungespeicherte Änderungen" : files[fileIdx]?.exists ? "Gespeichert" : "Datei gibt es noch nicht – wird beim Speichern angelegt";
      lines();
    };
    ed.oninput = mark;
    ed.onscroll = () => { gut.scrollTop = ed.scrollTop; };
    ed.onkeydown = (e) => {
      if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); ed.setRangeText("  ", ed.selectionStart, ed.selectionEnd, "end"); mark(); }
      if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); if (dirty) saveFile(); }
    };
    box.querySelector("#edReset").onclick = () => { ed.value = original; mark(); };
    box.querySelector("#edSave").onclick = () => saveFile();
    box.querySelector("#edApply").onclick = () => run("up");
    box.querySelector(".file-tabs").onclick = async (e) => {
      const b = e.target.closest("[data-fi]");
      if (!b || +b.dataset.fi === fileIdx) return;
      if (dirty && !(await confirmDialog("Änderungen verwerfen?", "Die aktuelle Datei hat ungespeicherte Änderungen.", { ok: "Verwerfen", danger: true }))) return;
      fileIdx = +b.dataset.fi;
      drawFiles();
    };
    loadFile();
    mark();
  }

  async function loadFile() {
    const f = files[fileIdx];
    const ed = el.querySelector("#ed");
    if (!f || !ed) return;
    ed.value = "";
    ed.disabled = true;
    const d = await attempt(() => api.get(`/stacks/${encodeURIComponent(name)}/file`, { path: f.path }));
    ed.disabled = false;
    original = d?.content ?? "";
    ed.value = original;
    ed.dispatchEvent(new Event("input"));
  }

  async function saveFile() {
    const f = files[fileIdx], ed = el.querySelector("#ed");
    const r = await attempt(() => api.put(`/stacks/${encodeURIComponent(name)}/file`, { path: f.path, content: ed.value }), `${f.name} gespeichert`);
    if (!r) return false;
    original = ed.value;
    f.exists = true;
    ed.dispatchEvent(new Event("input"));
    return true;
  }

  // ---- Verbrauch ----
  let metric = "cpu";
  async function drawChart() {
    const end = Math.floor(Date.now() / 1000), start = end - 86400;
    const q2 = { group: "stack", names: name, start, end, points: 200 };
    const box = el.querySelector("#sdChart");
    const series = [];
    let data;
    if (metric === "net") {
      const [rx, tx] = await Promise.all([api.get("/monitor/series", { ...q2, metric: "net_rx" }), api.get("/monitor/series", { ...q2, metric: "net_tx" })]).catch(() => [null, null]);
      data = rx;
      if (rx) series.push({ label: "Empfangen", color: COLORS[2], values: rx.series[name] || [] }, { label: "Gesendet", color: COLORS[1], values: tx.series[name] || [], fill: false });
    } else {
      data = await api.get("/monitor/series", { ...q2, metric }).catch(() => null);
      if (data) series.push({ label: metric === "cpu" ? "CPU" : "RAM", color: metric === "cpu" ? COLORS[0] : COLORS[4], values: data.series[name] || [] });
    }
    if (!alive.v || !data) return;
    const opts = { t: data.t, step: data.step, range: [start, end], series, height: 180, bytes: metric !== "cpu",
      fmt: metric === "cpu" ? (v) => fmtPct(v, 2) : metric === "mem" ? (v) => fmtBytes(v) : (v) => fmtRate(v) };
    if (chart) chart.update(opts); else chart = lineChart(box, opts);
  }
  el.querySelector("#sdMetric").onclick = (e) => {
    const b = e.target.closest("[data-m]");
    if (!b) return;
    metric = b.dataset.m;
    el.querySelectorAll("#sdMetric button").forEach((x) => x.classList.toggle("on", x === b));
    drawChart();
  };

  // ---- Klicks ----
  const onClick = async (e) => {
    const act = e.target.closest("[data-act]");
    if (act && !act.disabled) return run(act.dataset.act);
    if (e.target.closest("#sdMore")) {
      const items = ["up", "down"].map((a) => ({ value: a, icon: ACTIONS[a].icon, label: ACTIONS[a].label, sub: ACTIONS[a].sub, danger: ACTIONS[a].danger }))
        .filter((it) => s.files_ok && !(s.self && it.value === "down"));
      items.push("-", { value: "refresh", icon: "refresh", label: "Neu laden" });
      return popMenu(e.target.closest("#sdMore"), items, (v) => (v === "refresh" ? loadDetail() : run(v)), { align: "right" });
    }
    const lg = e.target.closest("[data-logs]");
    if (lg) return openLogs(lg.dataset.logs);
    const cm = e.target.closest("[data-cmenu]");
    if (cm) {
      const c = s.containers.find((x) => x.id === cm.dataset.cmenu);
      const items = [{ value: "logs", icon: "article", label: "Logs" }, "-"];
      if (c.state === "running") items.push({ value: "restart", icon: "restart_alt", label: "Neu starten" }, { value: "stop", icon: "stop", label: "Stoppen", danger: true });
      else items.push({ value: "start", icon: "play_arrow", label: "Starten" });
      return popMenu(cm, items, async (v) => {
        if (v === "logs") return openLogs(c.id);
        if (c.self && v !== "start") return toast("Das ist das Dashboard selbst – das geht nur auf dem Server");
        if (v === "stop" && !(await confirmDialog(`${c.name} stoppen?`, "Der Container wird angehalten.", { ok: "Stoppen", danger: true }))) return;
        const ok = await attempt(() => api.post(`/containers/${c.id.slice(0, 12)}/action`, { action: v }), `${c.name}: ${v === "start" ? "gestartet" : v === "stop" ? "gestoppt" : "neu gestartet"}`);
        if (ok) { stacks.refresh(); loadDetail(); }
      }, { align: "right" });
    }
  };
  document.addEventListener("click", onClick);
  const onUnload = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  addEventListener("beforeunload", onUnload);

  drawTop();
  drawCtrs();
  loadDetail().then(drawChart);
  const u1 = stacks.sub((list) => {
    const x = list?.find((y) => y.name === name);
    if (!x || !s) return;
    s = { ...s, ...x };
    drawTop(); drawCtrs(); drawInfo();
    if (x.job && followed !== x.job.id) follow(x.job);
  });
  const u2 = live.sub(() => drawCtrs());
  return () => { alive.v = false; u1(); u2(); jobAbort?.abort(); chart?.destroy(); document.removeEventListener("click", onClick); removeEventListener("beforeunload", onUnload); };
}

// ---------- Logs eines Containers ----------
function openLogs(cid) {
  let tail = 300, timer = null;
  const m = openModal(`${sheetHead("Logs", "article")}
    <div class="log-bar"><div class="seg-mini" id="lgTail">${[100, 300, 1000, 5000].map((n) => `<button type="button" data-n="${n}" class="${n === tail ? "on" : ""}">${n}</button>`).join("")}</div>
      <label class="log-follow"><button type="button" class="sw on" id="lgFollow" aria-label="Mitlaufen"></button>Mitlaufen</label>
      <input class="cd-input log-filter" id="lgQ" type="search" placeholder="Filtern" autocomplete="off"></div>
    <pre class="log tall" id="lgOut">Lade …</pre>`, { wide: true, tall: true, onClose: () => clearInterval(timer) });
  const out = m.el.querySelector("#lgOut"), qIn = m.el.querySelector("#lgQ"), fol = m.el.querySelector("#lgFollow");
  let text = "";
  const render = () => {
    const needle = qIn.value.trim().toLowerCase();
    const stick = out.scrollTop + out.clientHeight >= out.scrollHeight - 30;
    const lines = text.split("\n").filter((l) => !needle || l.toLowerCase().includes(needle));
    out.innerHTML = lines.map((l) => {
      const mm = /^(\S+Z) (.*)$/.exec(l);
      const body = mm ? mm[2] : l;
      const cls = /\b(error|err|fatal|exception|panic|traceback)\b/i.test(body) ? "lg-err" : /\bwarn(ing)?\b/i.test(body) ? "lg-warn" : "";
      return `<span class="${cls}">${mm ? `<i>${esc(new Date(mm[1]).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }))}</i> ` : ""}${esc(body)}</span>`;
    }).join("\n");
    if (stick) out.scrollTop = out.scrollHeight;
  };
  const load = async () => {
    const d = await api.get(`/containers/${cid.slice(0, 12)}/logs`, { tail }).catch((e) => ({ name: "", text: `Fehler: ${e.message}` }));
    m.el.querySelector(".sheet-head .tb-title").textContent = `Logs · ${d.name}`;
    text = d.text.trimEnd();
    render();
  };
  m.el.querySelector("#lgTail").onclick = (e) => {
    const b = e.target.closest("[data-n]");
    if (!b) return;
    tail = +b.dataset.n;
    m.el.querySelectorAll("#lgTail button").forEach((x) => x.classList.toggle("on", x === b));
    load();
  };
  qIn.oninput = render;
  const arm = () => { clearInterval(timer); if (fol.classList.contains("on")) timer = setInterval(() => !document.hidden && load(), 3000); };
  fol.onclick = () => { fol.classList.toggle("on"); arm(); };
  load().then(() => { out.scrollTop = out.scrollHeight; });
  arm();
}
