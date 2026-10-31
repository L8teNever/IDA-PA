// Interaktiver Obsidian-artiger Force-Directed-Wissensgraph auf HTML5 Canvas.
// Farben nach Bereich (`privat` = Hellblau, `memory` = Lavendel, `ki` = Salbeigrün, `ghost` = gestrichelt, `attachment` = Bernstein),
// Mausrad-/Pinch-Zoom, Pan, Knoten-Ziehen, Hover-Nachbarschafts-Fokus & Klick zum Öffnen/Anlegen.
//
// Für viele Seiten (1000+) gebaut: Abstoßung per Barnes-Hut-Quadtree (n·log n statt n²), Federn wie in d3-force
// (Stärke 1/min(Grad), damit ein Index mit 500 Links nicht alles zu einem Klumpen zieht), gebündeltes Zeichnen
// und Beschriftungen, die sich nicht überlappen (wichtige Knoten zuerst, Rest erst beim Hineinzoomen).

const SPACE_COLORS = {
  privat: "#7fd3ff",
  memory: "#c3b9f5",
  ki: "#81c995",
  ghost: "#7b7f8a",
  attachment: "#fbbc6e",
};
const BG = "#07080a";

const THETA2 = 0.81; // Barnes-Hut-Genauigkeit (θ = 0.9)
const VELOCITY_DECAY = 0.6;
const ALPHA_MIN = 0.002;
const ALPHA_DECAY = 1 - Math.pow(ALPHA_MIN, 1 / 300);

// Quadtree für die Abstoßung: Blätter halten Knoten, innere Zellen Masse und Schwerpunkt
function buildTree(nodes) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const n of nodes) {
    if (n.x < x0) x0 = n.x; if (n.x > x1) x1 = n.x;
    if (n.y < y0) y0 = n.y; if (n.y > y1) y1 = n.y;
  }
  const size = Math.max(x1 - x0, y1 - y0, 1) + 1;
  const root = { x: x0, y: y0, s: size, kids: null, body: null, more: null, m: 0, cx: 0, cy: 0 };
  for (const n of nodes) insert(root, n, 0);
  mass(root);
  return root;
}

function insert(q, n, depth) {
  if (!q.kids) {
    if (!q.body) { q.body = n; return; }
    if (depth > 24 || q.s < 1e-3) { (q.more ||= []).push(n); return; } // praktisch gleiche Position: im selben Blatt sammeln
    const old = q.body;
    q.body = null;
    const h = q.s / 2;
    q.kids = [0, 1, 2, 3].map((i) => ({ x: q.x + (i & 1) * h, y: q.y + (i >> 1) * h, s: h, kids: null, body: null, more: null, m: 0, cx: 0, cy: 0 }));
    for (const b of [old, ...(q.more || [])]) insert(q.kids[quad(q, b)], b, depth + 1);
    q.more = null;
  }
  insert(q.kids[quad(q, n)], n, depth + 1);
}

function quad(q, n) {
  const h = q.s / 2;
  return (n.x >= q.x + h ? 1 : 0) + (n.y >= q.y + h ? 2 : 0);
}

function mass(q) {
  if (!q.kids) {
    const all = q.body ? [q.body, ...(q.more || [])] : [];
    q.m = all.length;
    for (const b of all) { q.cx += b.x; q.cy += b.y; }
    if (q.m) { q.cx /= q.m; q.cy /= q.m; }
    return;
  }
  for (const k of q.kids) {
    mass(k);
    q.m += k.m;
    q.cx += k.cx * k.m;
    q.cy += k.cy * k.m;
  }
  if (q.m) { q.cx /= q.m; q.cy /= q.m; }
}

export function mountGraph(canvas, data, { onSelectNode, highlightQuery = "" } = {}) {
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);

  const total = (data.nodes || []).length;
  const big = total > 120;
  const nodes = (data.nodes || []).map((n, idx) => {
    // Sonnenblumen-Spirale als Start: gleichmäßig verteilt, der Platz wächst mit der Knotenzahl
    const angle = idx * 2.399963;
    const radius = n.center ? 0 : 14 * Math.sqrt(idx + 1);
    const deg = n.degree || 0;
    return {
      ...n,
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
      vx: 0,
      vy: 0,
      links: 0,
      r: n.center ? 11 : big ? Math.min(14, 3.2 + Math.sqrt(deg) * 0.75) : Math.min(13, 5.2 + Math.sqrt(deg) * 1.9),
      fixed: !!n.center,
    };
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = (data.edges || [])
    .map((e) => ({ source: byId.get(e.source), target: byId.get(e.target), embed: e.embed }))
    .filter((e) => e.source && e.target && e.source !== e.target);

  const neighbors = new Map(nodes.map((n) => [n.id, new Set([n.id])]));
  for (const e of edges) {
    neighbors.get(e.source.id).add(e.target.id);
    neighbors.get(e.target.id).add(e.source.id);
    e.source.links++;
    e.target.links++;
  }
  // Federn wie d3-force: schwach an Knoten mit vielen Links, der leichtere Knoten bewegt sich mehr
  for (const e of edges) {
    const a = e.source.links, b = e.target.links;
    e.strength = 1 / Math.min(a, b);
    e.bias = a / (a + b);
    e.len = (e.embed ? 40 : big ? 46 : 80) + e.source.r + e.target.r;
  }
  // Wichtigste Knoten zuerst beschriften
  const labelOrder = [...nodes].sort((a, b) => (b.center ? 1 : 0) - (a.center ? 1 : 0) || b.links - a.links);
  const maxLinks = labelOrder.length ? Math.max(1, labelOrder[0].links) : 1;

  let width = 600;
  let height = 400;
  let camX = 0;
  let camY = 0;
  let scale = 1;
  let alpha = 1;
  let alphaTarget = 0;
  let touched = false; // Nutzer hat gezoomt/verschoben → Kamera nicht mehr automatisch einpassen
  let hovered = null;
  let dragging = null;
  let panning = false;
  let panStart = { x: 0, y: 0, camX: 0, camY: 0 };
  let moved = false;
  let query = highlightQuery.trim().toLowerCase();
  let raf = 0;
  let destroyed = false;
  let dirty = true;
  const pointers = new Map();
  let pinch = null;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    width = Math.max(200, rect.width || 600);
    height = Math.max(180, rect.height || 360);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    if (!touched) fit(false);
    dirty = true;
  }

  function bounds() {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) {
      x0 = Math.min(x0, n.x - n.r); x1 = Math.max(x1, n.x + n.r);
      y0 = Math.min(y0, n.y - n.r); y1 = Math.max(y1, n.y + n.r);
    }
    return { x0, y0, x1, y1 };
  }

  // Alles ins Bild setzen; smooth = sanft hingleiten (während sich der Graph noch ausbreitet)
  function fit(smooth) {
    if (!nodes.length) return;
    const b = bounds();
    const pad = 36;
    const s = Math.max(0.04, Math.min(big ? 1.6 : 1.3, Math.min((width - pad * 2) / Math.max(1, b.x1 - b.x0), (height - pad * 2) / Math.max(1, b.y1 - b.y0))));
    const cx = -((b.x0 + b.x1) / 2) * s;
    const cy = -((b.y0 + b.y1) / 2) * s;
    const k = smooth ? 0.12 : 1;
    scale += (s - scale) * k;
    camX += (cx - camX) * k;
    camY += (cy - camY) * k;
  }

  function screenToWorld(sx, sy) {
    return { x: (sx - width / 2 - camX) / scale, y: (sy - height / 2 - camY) / scale };
  }

  function findNodeAt(sx, sy) {
    const p = screenToWorld(sx, sy);
    let best = null;
    let bestD = Infinity;
    for (const n of nodes) {
      const hitR = n.r + 6 / scale;
      const d = (p.x - n.x) ** 2 + (p.y - n.y) ** 2;
      if (d <= hitR * hitR && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  function tick() {
    alpha += (alphaTarget - alpha) * ALPHA_DECAY;
    if (alpha < ALPHA_MIN && !alphaTarget) { alpha = 0; return false; }

    // Federn
    for (const e of edges) {
      const s = e.source, t = e.target;
      let dx = t.x + t.vx - s.x - s.vx;
      let dy = t.y + t.vy - s.y - s.vy;
      let d = Math.hypot(dx, dy) || 1e-3;
      const l = ((d - e.len) / d) * alpha * e.strength;
      dx *= l; dy *= l;
      t.vx -= dx * e.bias; t.vy -= dy * e.bias;
      s.vx += dx * (1 - e.bias); s.vy += dy * (1 - e.bias);
    }

    // Abstoßung (Barnes-Hut)
    const charge = big ? -70 : -140;
    const tree = buildTree(nodes);
    for (const n of nodes) repel(tree, n, charge * alpha);

    // Sanft zur Mitte (hält lose Inseln und verwaiste Seiten beisammen)
    // Seiten ohne Links ziehen stärker, sonst treiben sie weit hinaus und der ganze Graph wird winzig
    const g = big ? 0.035 : 0.05;
    for (const n of nodes) {
      const k = (n.links ? g : 0.12) * alpha;
      n.vx -= n.x * k;
      n.vy -= n.y * k;
    }

    for (const n of nodes) {
      if (n === dragging) { n.vx = n.vy = 0; continue; }
      if (n.fixed) { n.x = n.y = n.vx = n.vy = 0; continue; }
      n.vx *= VELOCITY_DECAY;
      n.vy *= VELOCITY_DECAY;
      n.x += n.vx;
      n.y += n.vy;
      if (!Number.isFinite(n.x) || !Number.isFinite(n.y)) { n.x = (Math.random() - 0.5) * 50; n.y = (Math.random() - 0.5) * 50; n.vx = n.vy = 0; }
    }
    return true;
  }

  function repel(q, n, k) {
    if (!q.m) return;
    const dx = q.cx - n.x;
    const dy = q.cy - n.y;
    let d2 = dx * dx + dy * dy;
    if (q.kids && (q.s * q.s) / d2 >= THETA2) {
      for (const c of q.kids) repel(c, n, k);
      return;
    }
    if (!q.kids) {
      // Blatt: Knoten einzeln (sich selbst überspringen)
      for (const b of q.body ? [q.body, ...(q.more || [])] : []) {
        if (b === n) continue;
        let bx = b.x - n.x, by = b.y - n.y, bd2 = bx * bx + by * by;
        if (bd2 < 1) { bx = (Math.random() - 0.5) || 0.1; by = (Math.random() - 0.5) || 0.1; bd2 = 1; }
        n.vx += (bx * k) / bd2;
        n.vy += (by * k) / bd2;
      }
      return;
    }
    if (d2 < 1) d2 = 1;
    n.vx += (dx * k * q.m) / d2;
    n.vy += (dy * k * q.m) / d2;
  }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.translate(width / 2 + camX, height / 2 + camY);
    ctx.scale(scale, scale);

    const focusSet = hovered ? neighbors.get(hovered.id) : null;
    const matches = (n) => !query || n.label.toLowerCase().includes(query) || (n.path || "").toLowerCase().includes(query);
    const lw = 1 / scale;

    // Kanten gebündelt: ein Pfad pro Stil statt tausend einzelne Striche
    const baseAlpha = edges.length > 1500 ? 0.06 : edges.length > 500 ? 0.09 : 0.16;
    const groups = [[], [], []]; // normal, gestrichelt (Einbettung), hervorgehoben
    for (const e of edges) {
      if (focusSet && focusSet.has(e.source.id) && focusSet.has(e.target.id) && (e.source === hovered || e.target === hovered)) groups[2].push(e);
      else groups[e.embed ? 1 : 0].push(e);
    }
    const stroke = (list, style, width, dash) => {
      if (!list.length) return;
      ctx.beginPath();
      for (const e of list) { ctx.moveTo(e.source.x, e.source.y); ctx.lineTo(e.target.x, e.target.y); }
      ctx.setLineDash(dash);
      ctx.strokeStyle = style;
      ctx.lineWidth = width;
      ctx.stroke();
    };
    const dimEdges = focusSet ? 0.025 : baseAlpha;
    stroke(groups[0], `rgba(255,255,255,${dimEdges})`, lw, []);
    stroke(groups[1], `rgba(255,255,255,${dimEdges})`, lw, [4 * lw, 4 * lw]);
    stroke(groups[2], "rgba(127,211,255,0.7)", 1.6 * lw, []);
    ctx.setLineDash([]);

    // Knoten gebündelt nach Farbe und Abblendung
    const fills = new Map();
    const ghosts = [];
    const halos = [];
    for (const n of nodes) {
      const dim = (focusSet && !focusSet.has(n.id)) || !matches(n);
      if (n.center || n === hovered || (query && !dim)) halos.push(n);
      if (n.kind === "ghost") { ghosts.push([n, dim]); continue; }
      const key = `${SPACE_COLORS[n.space] || SPACE_COLORS.privat}|${dim ? 1 : 0}`;
      (fills.get(key) || fills.set(key, []).get(key)).push(n);
    }
    ctx.fillStyle = "rgba(127,211,255,0.22)";
    for (const n of halos) { ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 5, 0, Math.PI * 2); ctx.fill(); }
    for (const [key, list] of fills) {
      const [color, dim] = key.split("|");
      ctx.globalAlpha = dim === "1" ? 0.18 : 1;
      ctx.fillStyle = color;
      ctx.beginPath();
      for (const n of list) { ctx.moveTo(n.x + n.r, n.y); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2); }
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (ghosts.length) {
      ctx.setLineDash([3, 2.5]);
      ctx.lineWidth = 1.4;
      for (const dimPass of [true, false]) {
        ctx.globalAlpha = dimPass ? 0.18 : 1;
        ctx.beginPath();
        for (const [n, dim] of ghosts) if (dim === dimPass) { ctx.moveTo(n.x + n.r, n.y); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2); }
        ctx.fillStyle = "#181a1f";
        ctx.fill();
        ctx.strokeStyle = SPACE_COLORS.ghost;
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }

    // Beschriftungen in Bildschirmgröße (gleich groß bei jedem Zoom), ohne Überlappung:
    // wichtige Knoten zuerst, beim Hineinzoomen kommen immer mehr dazu
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const fontSize = width < 420 ? 10.5 : 11.5;
    ctx.font = `500 ${fontSize}px "Google Sans Flex", system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.lineJoin = "round";
    const placed = [];
    const free = (x0, y0, x1, y1) => !placed.some((b) => x0 < b[2] && x1 > b[0] && y0 < b[3] && y1 > b[1]);
    const budget = Math.max(12, Math.round((width * height) / 9000));
    const must = [hovered, ...nodes.filter((n) => n.center), ...(query ? nodes.filter((n) => matches(n)) : [])].filter(Boolean);
    const seen = new Set();
    for (const n of [...must, ...labelOrder]) {
      if (seen.has(n)) continue;
      seen.add(n);
      const forced = must.includes(n);
      if (!forced) {
        if (placed.length >= budget) break;
        if (focusSet && !focusSet.has(n.id)) continue;
        if (query && !matches(n)) continue;
        // ab welchem Zoom ein Knoten beschriftet wird, hängt von seiner Wichtigkeit ab
        const importance = Math.sqrt(n.links / maxLinks);
        if (scale * (0.35 + importance * 2.2) < (big ? 0.6 : 0.5)) continue;
      }
      const sx = width / 2 + camX + n.x * scale;
      const sy = height / 2 + camY + (n.y + n.r) * scale + 3;
      if (sx < -80 || sx > width + 80 || sy < -20 || sy > height + 20) continue;
      const text = n.label.length > 30 ? `${n.label.slice(0, 29)}…` : n.label;
      const w = ctx.measureText(text).width;
      const box = [sx - w / 2 - 2, sy - 1, sx + w / 2 + 2, sy + fontSize + 2];
      if (!forced && !free(...box)) continue;
      placed.push(box);
      const dim = (focusSet && !focusSet.has(n.id)) || (query && !matches(n));
      ctx.globalAlpha = dim ? 0.25 : 1;
      ctx.strokeStyle = BG;
      ctx.lineWidth = 3;
      ctx.strokeText(text, sx, sy);
      ctx.fillStyle = n === hovered || n.center ? "#ffffff" : "#cfd2da";
      ctx.fillText(text, sx, sy);
    }
    ctx.globalAlpha = 1;
  }

  function loop() {
    if (destroyed) return;
    const moving = tick();
    if (moving && !touched) fit(true);
    if (moving || dirty) {
      draw();
      dirty = false;
    }
    raf = requestAnimationFrame(loop);
  }

  function pointerCoords(ev) {
    const r = canvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  function zoomAt(p, factor) {
    const before = screenToWorld(p.x, p.y);
    scale = Math.max(0.03, Math.min(4, scale * factor));
    camX = p.x - width / 2 - before.x * scale;
    camY = p.y - height / 2 - before.y * scale;
    dirty = true;
  }

  const onDown = (ev) => {
    const p = pointerCoords(ev);
    pointers.set(ev.pointerId, p);
    canvas.setPointerCapture?.(ev.pointerId);
    if (pointers.size === 2) {
      // zweiter Finger: Pinch-Zoom statt Ziehen
      const [a, b] = [...pointers.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1 };
      dragging = null;
      panning = false;
      alphaTarget = 0;
      touched = true;
      return;
    }
    moved = false;
    const hit = findNodeAt(p.x, p.y);
    if (hit) {
      dragging = hit;
      alphaTarget = 0.3;
      alpha = Math.max(alpha, 0.3);
    } else {
      panning = true;
      panStart = { x: p.x, y: p.y, camX, camY };
    }
  };

  const onMove = (ev) => {
    const p = pointerCoords(ev);
    if (pointers.has(ev.pointerId)) pointers.set(ev.pointerId, p);
    if (pinch && pointers.size >= 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      zoomAt({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, d / pinch.d);
      pinch.d = d;
      moved = true;
      return;
    }
    if (dragging) {
      moved = true;
      touched = true;
      const w = screenToWorld(p.x, p.y);
      dragging.x = w.x;
      dragging.y = w.y;
      dirty = true;
      return;
    }
    if (panning) {
      if (Math.hypot(p.x - panStart.x, p.y - panStart.y) > 3) { moved = true; touched = true; }
      camX = panStart.camX + (p.x - panStart.x);
      camY = panStart.camY + (p.y - panStart.y);
      dirty = true;
      return;
    }
    if (ev.pointerType === "mouse") {
      const h = findNodeAt(p.x, p.y);
      if (h !== hovered) { hovered = h; dirty = true; }
      canvas.style.cursor = hovered ? "pointer" : "grab";
    }
  };

  const onUp = (ev) => {
    const p = pointerCoords(ev);
    pointers.delete(ev.pointerId);
    if (pinch) {
      if (pointers.size < 2) pinch = null;
      if (!pointers.size) moved = false;
      return;
    }
    const clickedNode = dragging || (!moved ? findNodeAt(p.x, p.y) : null);
    if (!moved && clickedNode && onSelectNode) onSelectNode(clickedNode);
    if (dragging) alphaTarget = 0;
    dragging = null;
    panning = false;
  };

  const onLeave = () => {
    if (hovered && !dragging) { hovered = null; dirty = true; }
  };

  const onWheel = (ev) => {
    ev.preventDefault();
    touched = true;
    zoomAt(pointerCoords(ev), Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.01 : 0.0018)));
  };

  canvas.style.touchAction = "none";
  const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
  ro?.observe(canvas);
  resize();
  // Großteil des Einschwingens vorab (zeitlich begrenzt), dann einpassen – sonst sieht man erst ein Gewimmel
  const t0 = performance.now();
  while (alpha > 0.06 && performance.now() - t0 < (big ? 350 : 120)) tick();
  fit(false);

  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);
  canvas.addEventListener("pointerleave", onLeave);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  loop();

  return {
    setQuery(q) {
      query = String(q || "").trim().toLowerCase();
      dirty = true;
    },
    resetView() {
      touched = false;
      fit(false);
      alpha = Math.max(alpha, 0.15);
      dirty = true;
    },
    destroy() {
      destroyed = true;
      cancelAnimationFrame(raf);
      ro?.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointercancel", onUp);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("wheel", onWheel);
    },
  };
}
