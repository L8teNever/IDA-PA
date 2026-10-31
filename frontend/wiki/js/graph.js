// Interaktiver Obsidian-artiger Force-Directed-Wissensgraph auf HTML5 Canvas.
// Farben nach Bereich (`privat` = Hellblau, `memory` = Lavendel, `ki` = Salbeigrün, `ghost` = gestrichelt, `attachment` = Bernstein),
// Mausrad-/Pinch-Zoom, Pan, Knoten-Ziehen, Hover-Nachbarschafts-Fokus & Klick zum Öffnen/Anlegen.

const SPACE_COLORS = {
  privat: "#7fd3ff",
  memory: "#c3b9f5",
  ki: "#81c995",
  ghost: "#7b7f8a",
  attachment: "#fbbc6e",
};

export function mountGraph(canvas, data, { onSelectNode, highlightQuery = "" } = {}) {
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);

  const nodes = (data.nodes || []).map((n, idx) => {
    const angle = (idx / Math.max(1, data.nodes.length)) * Math.PI * 2;
    const radius = n.center ? 0 : 70 + (idx % 7) * 28;
    return {
      ...n,
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
      vx: 0,
      vy: 0,
      r: n.center ? 11 : Math.min(13, 5.2 + Math.sqrt(n.degree || 0) * 1.9),
    };
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = (data.edges || [])
    .map((e) => ({ source: byId.get(e.source), target: byId.get(e.target), embed: e.embed }))
    .filter((e) => e.source && e.target);

  const neighbors = new Map(nodes.map((n) => [n.id, new Set([n.id])]));
  for (const e of edges) {
    neighbors.get(e.source.id).add(e.target.id);
    neighbors.get(e.target.id).add(e.source.id);
  }

  let width = 600;
  let height = 400;
  let camX = 0;
  let camY = 0;
  let scale = 1;
  let alpha = 1.0;
  let hovered = null;
  let dragging = null;
  let panning = false;
  let panStart = { x: 0, y: 0, camX: 0, camY: 0 };
  let moved = false;
  let query = highlightQuery.trim().toLowerCase();
  let raf = 0;
  let destroyed = false;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    width = Math.max(200, rect.width || 600);
    height = Math.max(180, rect.height || 360);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    alpha = Math.max(alpha, 0.25);
  }

  function screenToWorld(sx, sy) {
    return {
      x: (sx - width / 2 - camX) / scale,
      y: (sy - height / 2 - camY) / scale,
    };
  }

  function findNodeAt(sx, sy) {
    const p = screenToWorld(sx, sy);
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const hitR = n.r + 6 / scale;
      const dx = p.x - n.x;
      const dy = p.y - n.y;
      if (dx * dx + dy * dy <= hitR * hitR) return n;
    }
    return null;
  }

  function stepPhysics() {
    if (alpha < 0.004 && !dragging) return;
    const repulse = 1550;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      // Sanfte Zentrierung
      a.vx -= a.x * 0.006 * alpha;
      a.vy -= a.y * 0.006 * alpha;
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 4) {
          dx = (Math.random() - 0.5) * 2;
          dy = (Math.random() - 0.5) * 2;
          d2 = 4;
        }
        if (d2 > 90000) continue;
        const f = (repulse * alpha) / d2;
        const fx = dx * f * 0.04;
        const fy = dy * f * 0.04;
        a.vx -= fx;
        a.vy -= fy;
        b.vx += fx;
        b.vy += fy;
      }
    }
    for (const e of edges) {
      const dx = e.target.x - e.source.x;
      const dy = e.target.y - e.source.y;
      const dist = Math.hypot(dx, dy) || 1;
      const targetLen = e.embed ? 55 : 92;
      const k = ((dist - targetLen) / dist) * 0.045 * alpha;
      const fx = dx * k;
      const fy = dy * k;
      e.source.vx += fx;
      e.source.vy += fy;
      e.target.vx -= fx;
      e.target.vy -= fy;
    }
    for (const n of nodes) {
      if (n === dragging || n.center) {
        if (n.center && n !== dragging) {
          n.x *= 0.85;
          n.y *= 0.85;
        }
        continue;
      }
      n.vx *= 0.82;
      n.vy *= 0.82;
      n.x += n.vx;
      n.y += n.vy;
    }
    alpha *= 0.975;
  }

  function draw() {
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    ctx.translate(width / 2 + camX, height / 2 + camY);
    ctx.scale(scale, scale);

    const focusSet = hovered ? neighbors.get(hovered.id) : null;

    // Kanten zeichnen
    for (const e of edges) {
      const active = !focusSet || (focusSet.has(e.source.id) && focusSet.has(e.target.id));
      ctx.beginPath();
      if (e.embed) ctx.setLineDash([4 / scale, 4 / scale]);
      else ctx.setLineDash([]);
      ctx.moveTo(e.source.x, e.source.y);
      ctx.lineTo(e.target.x, e.target.y);
      ctx.strokeStyle = active
        ? (hovered ? "rgba(127,211,255,0.62)" : "rgba(255,255,255,0.16)")
        : "rgba(255,255,255,0.04)";
      ctx.lineWidth = (hovered && active ? 1.8 : 1.1) / Math.sqrt(scale);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // Knoten & Labels zeichnen
    const fontSize = Math.max(10, Math.min(13, 11.5 / Math.pow(scale, 0.25)));
    ctx.font = `500 ${fontSize}px "Google Sans Flex", system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";

    for (const n of nodes) {
      const color = SPACE_COLORS[n.space] || SPACE_COLORS.privat;
      const matchesQuery = !query || n.label.toLowerCase().includes(query) || (n.path || "").toLowerCase().includes(query);
      const dimmed = (focusSet && !focusSet.has(n.id)) || !matchesQuery;

      ctx.save();
      ctx.globalAlpha = dimmed ? 0.2 : 1;

      if (n.center || n === hovered || (query && matchesQuery)) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 5, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(127,211,255,0.22)";
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      if (n.kind === "ghost") {
        ctx.fillStyle = "#181a1f";
        ctx.fill();
        ctx.setLineDash([3, 2.5]);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.6;
        ctx.stroke();
        ctx.setLineDash([]);
      } else {
        ctx.fillStyle = color;
        ctx.fill();
      }

      if (scale > 0.52 || n.center || n === hovered || (n.degree || 0) >= 3) {
        ctx.fillStyle = n === hovered || n.center ? "#ffffff" : "#cfd2da";
        ctx.fillText(n.label.slice(0, 34), n.x, n.y + n.r + 4);
      }
      ctx.restore();
    }

    ctx.restore();
  }

  function loop() {
    if (destroyed) return;
    stepPhysics();
    draw();
    raf = requestAnimationFrame(loop);
  }

  function pointerCoords(ev) {
    const r = canvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  const onDown = (ev) => {
    const p = pointerCoords(ev);
    moved = false;
    const hit = findNodeAt(p.x, p.y);
    if (hit) {
      dragging = hit;
      alpha = Math.max(alpha, 0.35);
    } else {
      panning = true;
      panStart = { x: p.x, y: p.y, camX, camY };
    }
    canvas.setPointerCapture?.(ev.pointerId);
  };

  const onMove = (ev) => {
    const p = pointerCoords(ev);
    if (dragging) {
      moved = true;
      const w = screenToWorld(p.x, p.y);
      dragging.x = w.x;
      dragging.y = w.y;
      alpha = Math.max(alpha, 0.25);
      return;
    }
    if (panning) {
      if (Math.hypot(p.x - panStart.x, p.y - panStart.y) > 3) moved = true;
      camX = panStart.camX + (p.x - panStart.x);
      camY = panStart.camY + (p.y - panStart.y);
      return;
    }
    hovered = findNodeAt(p.x, p.y);
    canvas.style.cursor = hovered ? "pointer" : "grab";
  };

  const onUp = (ev) => {
    const p = pointerCoords(ev);
    const clickedNode = dragging || (!moved ? findNodeAt(p.x, p.y) : null);
    if (!moved && clickedNode && onSelectNode) {
      onSelectNode(clickedNode);
    }
    dragging = null;
    panning = false;
  };

  const onWheel = (ev) => {
    ev.preventDefault();
    const p = pointerCoords(ev);
    const before = screenToWorld(p.x, p.y);
    const factor = ev.deltaY < 0 ? 1.12 : 0.89;
    scale = Math.max(0.28, Math.min(3.5, scale * factor));
    camX = p.x - width / 2 - before.x * scale;
    camY = p.y - height / 2 - before.y * scale;
  };

  const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
  ro?.observe(canvas);
  resize();

  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  loop();

  return {
    setQuery(q) {
      query = String(q || "").trim().toLowerCase();
    },
    resetView() {
      camX = 0;
      camY = 0;
      scale = 1;
      alpha = 0.5;
    },
    destroy() {
      destroyed = true;
      cancelAnimationFrame(raf);
      ro?.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("wheel", onWheel);
    },
  };
}
