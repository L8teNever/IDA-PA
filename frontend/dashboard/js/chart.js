// Diagramme als SVG (ohne Bibliothek): Linien/Flächen mit Tooltip und Zoom per Ziehen, Ring, Mini-Verlauf.
import { esc, fmtDateTime, fmtDay, fmtTime } from "./ui.js";

export const COLORS = ["#7fd3ff", "#f6aea9", "#81c995", "#fde293", "#c3b9f5", "#fbbc6e", "#78d9ec", "#d7aefb", "#b5d6a7", "#f28b82", "#8ab4f8", "#b4b8c0"];
const NS = "http://www.w3.org/2000/svg";
const TICKS = [60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800, 1209600, 2592000];

function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}

// Für Bytes: Achse in 1024er-Schritten, damit die Beschriftung glatte Werte zeigt
function niceMaxBytes(v) {
  if (!(v > 0)) return 1024;
  const e = Math.floor(Math.log(v) / Math.log(1024));
  const u = 1024 ** e;
  return niceMax(v / u) * u;
}

/**
 * Liniendiagramm. opts: { t:[Sekunden], series:[{label,color,values,fill,dash}], fmt(v), range:[a,b], step,
 *   min (kleinster oberer Rand), max (fester oberer Rand), bytes (Achse in 1024ern), height, onZoom(a,b), stacked }
 */
export function lineChart(el, opts) {
  el.classList.add("chart");
  el.innerHTML = `<svg class="chart-svg"></svg><div class="chart-tip" hidden></div><div class="chart-empty" hidden>Noch keine Messwerte in diesem Zeitraum</div>`;
  const svg = el.querySelector("svg"), tip = el.querySelector(".chart-tip"), empty = el.querySelector(".chart-empty");
  let o = opts, geo = null, brush = null;

  function draw() {
    const W = Math.max(200, el.clientWidth), H = o.height || 200;
    const pl = 54, pr = 10, pt = 10, pb = 24;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", W);
    svg.setAttribute("height", H);
    const t = o.t || [];
    const series = (o.series || []).filter((s) => s.values?.some((v) => v != null));
    empty.hidden = !!(t.length && series.length);
    const [a, b] = o.range || [t[0], t[t.length - 1]];
    if (!t.length || !series.length || !(b > a)) { svg.innerHTML = ""; geo = null; return; }
    // gestapelt: Werte aufsummieren (für Stacks/Container übereinander)
    const vals = series.map((s) => s.values);
    const shown = o.stacked ? vals.map((_, i) => t.map((__, k) => vals.slice(0, i + 1).reduce((acc, v) => (v[k] == null ? acc : (acc ?? 0) + v[k]), null))) : vals;
    let top = o.max ?? Math.max(o.min ?? 0, ...shown.flat().filter((v) => v != null));
    if (o.max == null) top = o.bytes ? niceMaxBytes(top * 1.05) : niceMax(top * 1.05);
    const x = (s) => pl + ((s - a) / (b - a)) * (W - pl - pr);
    const y = (v) => pt + (1 - v / top) * (H - pt - pb);
    const gap = (o.step || 0) * 2.5;
    let out = "";
    // Gitter + y-Achse
    for (let i = 0; i <= 4; i++) {
      const v = (top / 4) * i, yy = y(v);
      out += `<line class="grid" x1="${pl}" x2="${W - pr}" y1="${yy}" y2="${yy}"/><text class="ax" x="${pl - 8}" y="${yy + 4}" text-anchor="end">${esc(o.fmt ? o.fmt(v) : v)}</text>`;
    }
    // x-Achse
    const span = b - a;
    const want = span / Math.max(2, Math.floor((W - pl) / 110));
    const step = TICKS.find((s) => s >= want) || 2592000;
    const tz = new Date().getTimezoneOffset() * 60;
    for (let s = Math.ceil((a - tz) / step) * step + tz; s <= b; s += step) {
      const xx = x(s);
      const lbl = step >= 86400 ? fmtDay(s) : span > 86400 && new Date(s * 1000).getHours() === 0 && new Date(s * 1000).getMinutes() === 0 ? fmtDay(s) : fmtTime(s);
      out += `<line class="grid v" x1="${xx}" x2="${xx}" y1="${pt}" y2="${H - pb}"/><text class="ax" x="${xx}" y="${H - 6}" text-anchor="middle">${lbl}</text>`;
    }
    // Linien (von hinten nach vorn, damit die erste Serie oben liegt)
    for (let i = series.length - 1; i >= 0; i--) {
      const s = series[i], v = shown[i];
      let line = "", area = "", seg = [];
      const flush = () => {
        if (!seg.length) return;
        line += "M" + seg.map(([xx, yy]) => `${xx.toFixed(1)},${yy.toFixed(1)}`).join("L");
        const base = o.stacked && i > 0 ? null : y(0);
        if (s.fill !== false && base != null) area += `M${seg[0][0].toFixed(1)},${base}L` + seg.map(([xx, yy]) => `${xx.toFixed(1)},${yy.toFixed(1)}`).join("L") + `L${seg[seg.length - 1][0].toFixed(1)},${base}Z`;
        seg = [];
      };
      let last = null;
      t.forEach((ts, k) => {
        if (v[k] == null || (last != null && gap && ts - last > gap)) flush();
        if (v[k] != null) { seg.push([x(ts), y(v[k])]); last = ts; }
      });
      flush();
      const gid = `g${Math.random().toString(36).slice(2, 8)}`;
      if (area) out += `<defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${s.color}" stop-opacity=".32"/><stop offset="1" stop-color="${s.color}" stop-opacity="0"/></linearGradient></defs><path d="${area}" fill="url(#${gid})"/>`;
      out += `<path d="${line}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" stroke-linejoin="round" stroke-linecap="round" ${s.dash ? 'stroke-dasharray="4 4" opacity=".7"' : ""}/>`;
    }
    out += `<line class="cross" x1="0" x2="0" y1="${pt}" y2="${H - pb}" visibility="hidden"/><g class="dots"></g><rect class="brush" x="0" y="${pt}" width="0" height="${H - pt - pb}" visibility="hidden"/>`;
    out += `<rect class="hit" x="${pl}" y="${pt}" width="${W - pl - pr}" height="${H - pt - pb}" fill="transparent"/>`;
    svg.innerHTML = out;
    geo = { W, H, pl, pr, pt, pb, a, b, x, y, series, shown, t };
  }

  function nearest(px) {
    const { a, b, pl, pr, W, t } = geo;
    const s = a + ((px - pl) / (W - pl - pr)) * (b - a);
    let lo = 0, hi = t.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (t[m] < s) lo = m; else hi = m; }
    return Math.abs(t[lo] - s) <= Math.abs(t[hi] - s) ? lo : hi;
  }
  const px = (e) => { const r = svg.getBoundingClientRect(); return ((e.clientX - r.left) / r.width) * geo.W; };

  function hover(e) {
    if (!geo) return;
    const p = px(e);
    if (brush) {
      const x0 = Math.max(geo.pl, Math.min(brush, p)), x1 = Math.min(geo.W - geo.pr, Math.max(brush, p));
      const r = svg.querySelector(".brush");
      r.setAttribute("x", x0); r.setAttribute("width", x1 - x0); r.setAttribute("visibility", "visible");
    }
    const k = nearest(p);
    const ts = geo.t[k], xx = geo.x(ts);
    const cross = svg.querySelector(".cross");
    cross.setAttribute("x1", xx); cross.setAttribute("x2", xx); cross.setAttribute("visibility", "visible");
    svg.querySelector(".dots").innerHTML = geo.series.map((s, i) => geo.shown[i][k] == null ? "" : `<circle cx="${xx}" cy="${geo.y(geo.shown[i][k])}" r="4" fill="${s.color}" stroke="#121316" stroke-width="2"/>`).join("");
    tip.innerHTML = `<b>${fmtDateTime(ts)}</b>` + geo.series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}<em>${s.values[k] == null ? "–" : esc(o.fmt ? o.fmt(s.values[k]) : s.values[k])}</em></span>`).join("")
      + (o.extra ? o.extra(k) : "");
    tip.hidden = false;
    const r = el.getBoundingClientRect(), sr = svg.getBoundingClientRect();
    const left = sr.left - r.left + (xx / geo.W) * sr.width;
    const tw = tip.offsetWidth;
    tip.style.left = `${Math.max(4, Math.min(left + 14 + tw > r.width ? left - tw - 14 : left + 14, r.width - tw - 4))}px`;
  }
  function leave() {
    if (!geo) return;
    tip.hidden = true;
    svg.querySelector(".cross")?.setAttribute("visibility", "hidden");
    const d = svg.querySelector(".dots"); if (d) d.innerHTML = "";
  }
  svg.addEventListener("pointermove", hover);
  svg.addEventListener("pointerleave", () => { if (!brush) leave(); });
  svg.addEventListener("pointerdown", (e) => {
    if (!geo || !o.onZoom || e.pointerType !== "mouse" || e.button !== 0) return;
    brush = px(e);
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointerup", (e) => {
    if (brush == null) return;
    const p = px(e), x0 = Math.min(brush, p), x1 = Math.max(brush, p);
    brush = null;
    svg.querySelector(".brush")?.setAttribute("visibility", "hidden");
    if (x1 - x0 < 8) return;
    const toT = (xx) => geo.a + ((xx - geo.pl) / (geo.W - geo.pl - geo.pr)) * (geo.b - geo.a);
    o.onZoom(Math.round(toT(Math.max(geo.pl, x0))), Math.round(toT(Math.min(geo.W - geo.pr, x1))));
  });
  const ro = new ResizeObserver(() => draw());
  ro.observe(el);
  draw();
  return {
    update(n) { o = { ...o, ...n }; draw(); },
    destroy() { ro.disconnect(); },
  };
}

// Ring: parts = [{ label, value, color }], Mitte als HTML
export function donut(parts, { size = 168, thick = 20, center = "" } = {}) {
  const total = parts.reduce((s, p) => s + Math.max(0, p.value || 0), 0) || 1;
  const r = (size - thick) / 2, c = 2 * Math.PI * r;
  let off = 0;
  const arcs = parts.map((p) => {
    const len = (Math.max(0, p.value || 0) / total) * c;
    const gapLen = len > 4 ? 2 : 0;
    const s = `<circle r="${r}" cx="${size / 2}" cy="${size / 2}" fill="none" stroke="${p.color}" stroke-width="${thick}" stroke-dasharray="${Math.max(0, len - gapLen)} ${c}" stroke-dashoffset="${-off}" transform="rotate(-90 ${size / 2} ${size / 2})"><title>${esc(p.label)}</title></circle>`;
    off += len;
    return s;
  }).join("");
  return `<div class="donut" style="width:${size}px;height:${size}px"><svg viewBox="0 0 ${size} ${size}"><circle r="${r}" cx="${size / 2}" cy="${size / 2}" fill="none" stroke="var(--surface-highest)" stroke-width="${thick}"/>${arcs}</svg><div class="donut-c">${center}</div></div>`;
}

// kleiner Verlauf für Kacheln
export function spark(values, color = "var(--accent)", { max } = {}) {
  const v = (values || []).filter((x) => x != null);
  if (v.length < 2) return `<svg class="spark" viewBox="0 0 100 30"></svg>`;
  const top = max ?? (Math.max(...v) || 1);
  const pts = v.map((x, i) => `${((i / (v.length - 1)) * 100).toFixed(1)},${(28 - (x / top) * 26).toFixed(1)}`);
  return `<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none"><path d="M0,30L${pts.join("L")}L100,30Z" fill="${color}" opacity=".15"/><path d="M${pts.join("L")}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

export { NS };
