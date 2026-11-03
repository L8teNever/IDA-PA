// Speicher: Platte (belegt/frei, Verlauf), Anteil von Docker, Verbrauch je Stack (Images, Volumes, Ordner, Container)
// sowie Listen der Volumes und Images. Aufräumen von ungenutzten Images und Build-Cache.
import { api } from "../api.js";
import { COLORS, donut, lineChart } from "../chart.js";
import { attempt, confirmDialog, esc, fmtAgo, fmtBytes, fmtPct, toast } from "../ui.js";

const PARTS = [["images", "Images", COLORS[0]], ["volumes", "Volumes", COLORS[4]], ["files", "Ordner", COLORS[3]], ["rw", "Container", COLORS[1]]];
let listTab = "volumes", histRange = 2592000, showAll = false;

export default {
  id: "storage", label: "Speicher", icon: "hard_drive", path: "/speicher",
  match: (p) => (p === "/speicher" ? {} : null),

  mount(el, _p, ctx) {
    let data = null, chart = null, alive = true, poll = null;
    ctx.top(`<div class="topbar-left"><h2><span class="ms">hard_drive</span><span class="tb-title">Speicher</span></h2></div>
      <div class="topbar-actions"><span class="tb-sub hide-mobile" id="sgUpd"></span>
      <button type="button" class="icon-btn" id="sgRefresh" title="Neu berechnen (auch Ordnergrößen)"><span class="ms">refresh</span></button></div>`);
    el.innerHTML = `<div class="page">
      <div class="cols">
        <section class="card"><div class="card-head"><h3><span class="ms">hard_drive</span>Platte</h3></div><div class="donut-row" id="sgDisk"></div></section>
        <section class="card"><div class="card-head"><h3><span class="ms">deployed_code</span>Docker</h3></div><div class="donut-row" id="sgDocker"></div><div class="btn-row" id="sgPrune"></div></section>
      </div>
      <section class="card"><div class="card-head"><h3><span class="ms">show_chart</span>Belegung im Verlauf</h3>
        <div class="seg-mini" id="sgHist">${[[604800, "7 Tage"], [2592000, "30 Tage"], [31536000, "1 Jahr"]].map(([s, l]) => `<button type="button" data-s="${s}" class="${s === histRange ? "on" : ""}">${l}</button>`).join("")}</div></div>
        <div class="chart-box" id="sgChart"></div></section>
      <section class="card"><div class="card-head wrap"><h3><span class="ms">stacks</span>Je Stack</h3>
        <span class="legend">${PARTS.map(([, l, c]) => `<i style="background:${c}"></i>${l}`).join(" ")}</span></div>
        <div id="sgStacks" class="hbars">Lade …</div></section>
      <section class="card"><div class="card-head"><div class="seg-mini" id="sgTab"><button type="button" data-t="volumes">Volumes</button><button type="button" data-t="images">Images</button></div><span class="muted small" id="sgTabInfo"></span></div>
        <div class="table-wrap"><table class="utable" id="sgList"></table></div></section>
    </div>`;
    const $ = (id) => el.querySelector("#" + id) || document.getElementById(id); // sgUpd liegt in der Kopfzeile

    async function load() {
      const d = await api.get("/storage").catch(() => null);
      if (!alive || !d) return;
      data = d;
      draw();
      clearTimeout(poll);
      if (d.busy) poll = setTimeout(load, 3000);   // Berechnung läuft noch
    }

    function draw() {
      const d = data, k = d.disk, dk = d.docker;
      $("sgUpd").textContent = d.busy ? "wird berechnet …" : d.updated ? `berechnet ${fmtAgo(d.updated)}${d.dirs_updated ? ` · Ordner ${fmtAgo(d.dirs_updated)}` : ""}` : "";
      const dockerSum = dk ? dk.images + dk.containers + dk.volumes + dk.build_cache : 0;
      const filesSum = d.stacks.reduce((s, x) => s + (x.files || 0), 0);
      const other = Math.max(0, k.disk_used - dockerSum - filesSum);
      const legend = (parts) => `<div class="legend-list">${parts.map((p) => `<div><i style="background:${p.color}"></i><span>${esc(p.label)}</span><b>${fmtBytes(p.value)}</b></div>`).join("")}</div>`;
      const diskParts = [{ label: "Docker", value: dockerSum, color: COLORS[0] }, { label: "Stack-Ordner", value: filesSum, color: COLORS[3] },
        { label: "Sonstiges", value: other, color: COLORS[11] }, { label: "Frei", value: k.disk_free, color: "rgba(255,255,255,.08)" }];
      $("sgDisk").innerHTML = donut(diskParts, { center: `<b>${fmtPct((k.disk_used / k.disk_total) * 100, 0)}</b><small>von ${fmtBytes(k.disk_total)}</small>` }) + legend(diskParts);
      if (dk) {
        const parts = [{ label: `Images (${dk.images_count})`, value: dk.images, color: COLORS[0] }, { label: `Volumes (${dk.volumes_count})`, value: dk.volumes, color: COLORS[4] },
          { label: `Container (${dk.containers_count})`, value: dk.containers, color: COLORS[1] }, { label: "Build-Cache", value: dk.build_cache, color: COLORS[5] }];
        $("sgDocker").innerHTML = donut(parts, { center: `<b>${fmtBytes(dockerSum)}</b><small>Docker</small>` }) + legend(parts);
        $("sgPrune").innerHTML = `<button type="button" class="btn-tonal sm" data-prune="images" ${dk.images_unused ? "" : "disabled"}><span class="ms">delete_sweep</span>Ungenutzte Images (${fmtBytes(dk.images_unused)})</button>
          <button type="button" class="btn-tonal sm" data-prune="build" ${dk.build_cache ? "" : "disabled"}><span class="ms">cleaning_services</span>Build-Cache (${fmtBytes(dk.build_cache)})</button>`
          + (dk.volumes_unused ? `<span class="muted small">Ungenutzte Volumes: ${fmtBytes(dk.volumes_unused)} (werden nicht automatisch gelöscht)</span>` : "");
      } else $("sgDocker").innerHTML = `<span class="muted">${esc(d.error || "Lade …")}</span>`;
      drawStacks();
      drawList();
    }

    function drawStacks() {
      const list = data.stacks.filter((s) => s.total > 0);
      const max = Math.max(1, ...list.map((s) => s.total));
      const shown = showAll ? list : list.slice(0, 15);
      $("sgStacks").innerHTML = shown.map((s) => `<a class="hbar-row" href="/stacks/${encodeURIComponent(s.name)}" title="${PARTS.map(([key, l]) => `${l}: ${fmtBytes(s[key])}`).join(" · ")}">
          <span class="hb-name">${esc(s.name)}</span>
          <span class="hb-track">${PARTS.map(([key, , c]) => s[key] ? `<i style="width:${(s[key] / max) * 100}%;background:${c}"></i>` : "").join("")}</span>
          <b>${fmtBytes(s.total)}</b></a>`).join("")
        + (list.length > 15 ? `<button type="button" class="btn-tonal sm more-btn" id="sgMore">${showAll ? "Weniger zeigen" : `Alle ${list.length} zeigen`}</button>` : "")
        + `<p class="sheet-hint">Images, die mehrere Stacks nutzen, zählen bei jedem mit. „Ordner“ = der Stack-Ordner auf der Platte (z. B. Welten, Datenbanken als Bind-Mount).</p>`;
    }

    function drawList() {
      el.querySelectorAll("#sgTab button").forEach((x) => x.classList.toggle("on", x.dataset.t === listTab));
      if (listTab === "volumes") {
        $("sgTabInfo").textContent = `${data.volumes.length} Volumes`;
        $("sgList").innerHTML = `<thead><tr><th>Volume</th><th class="hide-mobile">Stack</th><th>Benutzt von</th><th class="num">Größe</th></tr></thead><tbody>${data.volumes.map((v) => `<tr>
          <td class="mono break">${esc(v.name)}</td><td class="hide-mobile">${esc(v.stack || "–")}</td>
          <td>${v.used_by.length ? esc(v.used_by.join(", ")) : `<span class="pill warn">ungenutzt</span>`}</td><td class="num">${fmtBytes(v.size)}</td></tr>`).join("")}</tbody>`;
      } else {
        $("sgTabInfo").textContent = `${data.images.length} Images`;
        $("sgList").innerHTML = `<thead><tr><th>Image</th><th class="num">Container</th><th class="num">Größe</th></tr></thead><tbody>${data.images.map((i) => `<tr>
          <td class="mono break">${i.tags.length ? i.tags.map(esc).join("<br>") : `<span class="muted">&lt;ohne Namen&gt; ${esc(i.id)}</span>`}</td>
          <td class="num">${i.containers || `<span class="pill warn">0</span>`}</td><td class="num">${fmtBytes(i.size)}</td></tr>`).join("")}</tbody>`;
      }
    }

    async function loadHist() {
      const end = Math.floor(Date.now() / 1000), start = end - histRange;
      const h = await api.get("/monitor/host", { start, end, points: 200 }).catch(() => null);
      if (!alive || !h) return;
      const total = Math.max(0, ...h.disk_total.filter((v) => v != null));
      const o = { t: h.t, step: h.step, range: [start, end], bytes: true, max: total || undefined, height: 180, fmt: (v) => fmtBytes(v),
        series: [{ label: "Belegt", color: COLORS[3], values: h.disk_used }] };
      if (chart) chart.update(o); else chart = lineChart($("sgChart"), o);
    }

    const onClick = async (e) => {
      const p = e.target.closest("[data-prune]");
      if (p && !p.disabled) {
        const what = p.dataset.prune;
        const ok = await confirmDialog(what === "images" ? "Ungenutzte Images löschen?" : "Build-Cache leeren?",
          what === "images" ? "Alle Images, die gerade kein Container nutzt, werden gelöscht. Bei Bedarf lädt Docker sie neu herunter." : "Zwischenstände von Builds werden gelöscht. Der nächste Build dauert dann länger.",
          { ok: "Löschen", danger: true });
        if (!ok) return;
        p.disabled = true;
        const r = await attempt(() => api.post("/storage/prune", { what }));
        if (r) { toast(`${fmtBytes(r.reclaimed)} frei geworden`); setTimeout(load, 1500); }
        return;
      }
      if (e.target.closest("#sgMore")) { showAll = !showAll; drawStacks(); }
    };
    el.addEventListener("click", onClick);
    $("sgTab").onclick = (e) => { const b = e.target.closest("[data-t]"); if (b) { listTab = b.dataset.t; drawList(); } };
    $("sgHist").onclick = (e) => {
      const b = e.target.closest("[data-s]");
      if (!b) return;
      histRange = +b.dataset.s;
      el.querySelectorAll("#sgHist button").forEach((x) => x.classList.toggle("on", x === b));
      loadHist();
    };
    document.getElementById("sgRefresh").onclick = async () => { await attempt(() => api.post("/storage/refresh"), "Wird neu berechnet …"); setTimeout(load, 500); };

    load();
    loadHist();
    return () => { alive = false; clearTimeout(poll); chart?.destroy(); };
  },
};
