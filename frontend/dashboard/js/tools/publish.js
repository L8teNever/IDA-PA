// Veröffentlichen (nur IDA Server): alle Tunnel-Adressen mit Ziel und Schutz; neue Adresse in einem Schritt
// (Tunnel-Regel + DNS + optional Zero Trust), Schutz an/aus, Adresse entfernen.
import { api } from "../api.js";
import { formDialog, lines } from "../form.js";
import { stepsHtml } from "../jobview.js";
import { attempt, confirmDialog, esc, openModal, popMenu, sheetHead, toast } from "../ui.js";

const ACCESS = { access: ["lock", "Zero Trust", "pill ok"], public: ["public", "Öffentlich", "pill warn"] };

export default {
  id: "publish", label: "Veröffentlichen", icon: "travel_explore", path: "/veroeffentlichen",
  match: (p) => (p === "/veroeffentlichen" ? {} : null),
  mount(el, _p, ctx) {
    let data = null, q = (_p.query?.q || "").toLowerCase(), alive = true;
    ctx.top(`<div class="topbar-left"><h2><span class="ms">travel_explore</span><span class="tb-title">Veröffentlichen</span></h2></div>
      <div class="topbar-actions"><button type="button" class="icon-btn" id="pbReload" aria-label="Neu laden"><span class="ms">refresh</span></button>
      <button type="button" class="btn-primary" id="pbNew"><span class="ms">add</span>Neue Adresse</button></div>`);
    el.innerHTML = `<div class="page"><div id="pbInfo"></div>
      <section class="card"><div class="card-head wrap"><h3><span class="ms">alt_route</span>Adressen im Tunnel</h3>
        <label class="side-search pb-search"><span class="ms">search</span><input id="pbQ" type="search" placeholder="Adresse, Ziel oder Stack" autocomplete="off" value="${esc(q)}"></label></div>
        <div id="pbList" class="muted">Lade …</div></section></div>`;
    document.getElementById("pbNew").onclick = () => create();
    document.getElementById("pbReload").onclick = () => load();
    el.querySelector("#pbQ").oninput = (e) => { q = e.target.value.toLowerCase(); draw(); };

    async function load() {
      data = await api.get("/publish").catch((e) => ({ error: e.message, routes: [], tunnels: [], targets: [] }));
      if (alive) draw();
    }

    function draw() {
      const info = el.querySelector("#pbInfo");
      if (data.enabled === false) {
        info.innerHTML = `<div class="note warn"><span class="ms">key_off</span><div><code>CLOUDFLARE_API_TOKEN</code> fehlt in der <code>.env</code> des IDA-Stacks (derselbe Token wie beim Cloudflare-MCP).
          Rechte: Cloudflare Tunnel Edit, DNS Edit, Access: Apps and Policies Edit.</div></div>`;
      } else if (data.error) {
        info.innerHTML = `<div class="note warn"><span class="ms">error</span><div>Cloudflare: ${esc(data.error)}</div></div>`;
      } else {
        info.innerHTML = `<div class="note"><span class="ms">info</span><div>${data.tunnels.length} Tunnel · ${data.routes.length} Adressen${data.host_ip ? ` · Server für den Tunnel: <code>${esc(data.host_ip)}</code>` : ""}.
          Neue Adresse = Tunnel-Regel, DNS-Eintrag und auf Wunsch Zero Trust in einem Schritt.</div></div>`;
        const dup = data.routes.filter((r) => r.shadowed_by);
        if (dup.length) info.innerHTML += `<div class="note warn"><span class="ms">content_copy</span><div>${dup.length} doppelte ${dup.length === 1 ? "Regel" : "Regeln"} im Tunnel:
          ${dup.map((r) => `<b>${esc(r.hostname + r.path)}</b>`).join(", ")}. cloudflared nimmt immer die erste passende Regel, die doppelte greift nie –
          über ⋮ bei der Zeile entfernen (die Adresse selbst bleibt).</div></div>`;
      }
      const rows = data.routes.filter((r) => !q || [r.hostname + r.path, r.service, r.stack, r.container, r.tunnel].join(" ").toLowerCase().includes(q));
      el.querySelector("#pbList").innerHTML = rows.length ? `<div class="pb-rows">${rows.map(row).join("")}</div>`
        : `<div class="muted pad">${data.routes.length ? "Nichts gefunden." : "Noch keine Adressen."}</div>`;
    }

    function row(r, i) {
      const [ai, al, ac] = ACCESS[r.access?.state] || ["help", "?", "pill"];
      const idx = data.routes.indexOf(r);
      const dup = r.shadowed_by ? `<span class="pill warn" title="Die Regel ${esc(r.shadowed_host)} → ${esc(r.shadowed_by)} steht weiter oben und greift zuerst"><span class="ms sm">content_copy</span>doppelt – greift nie</span>` : "";
      return `<div class="pb-row ${r.shadowed_by ? "pb-dup" : ""}">
        <span class="ms pb-ic">${r.shadowed_by ? "content_copy" : r.access?.state === "access" ? "lock" : "public"}</span>
        <div class="pb-main"><a class="mono ellip" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.hostname + r.path)}</a>
          <small class="muted ellip"><span class="ms sm">subdirectory_arrow_right</span><span class="mono">${esc(r.service)}</span>${r.stack ? ` · <a href="/stacks/${encodeURIComponent(r.stack)}">${esc(r.stack)}</a>` : ""}${data.tunnels.length > 1 ? ` · ${esc(r.tunnel)}` : ""}</small></div>
        ${dup || `<span class="${ac}" title="${esc(r.access?.detail || "")}"><span class="ms sm">${ai}</span>${al}</span>`}
        <button type="button" class="icon-btn sm" data-r="${idx}" aria-label="Aktionen"><span class="ms">more_vert</span></button></div>`;
    }

    el.addEventListener("click", (e) => {
      const b = e.target.closest("[data-r]");
      if (b) menu(b, data.routes[+b.dataset.r]);
    });

    async function removeDup(r) {
      if (!(await confirmDialog("Doppelte Regel entfernen?", `${r.hostname}${r.path} → ${r.service} greift nie, weil ${r.shadowed_host} → ${r.shadowed_by} weiter oben steht. Nur diese eine Regel kommt raus; DNS und Zero Trust bleiben.`, { ok: "Entfernen", danger: true }))) return;
      if (await attempt(() => api.post("/publish/remove-rule", { tunnel_id: r.tunnel_id, index: r.index, hostname: r.hostname, service: r.service }), "Doppelte Regel entfernt")) load();
    }

    function menu(anchor, r) {
      const zt = r.access?.state === "access";
      if (r.shadowed_by) return popMenu(anchor, [
        { head: `Greift nie – ${r.shadowed_host} → ${r.shadowed_by} steht weiter oben` },
        { value: "dup", icon: "delete", label: "Doppelte Regel entfernen", danger: true, sub: "nur diese Zeile, DNS und Zero Trust bleiben" },
      ], (v) => { if (v === "dup") removeDup(r); }, { align: "right" });
      popMenu(anchor, [
        ...(r.access?.detail ? [{ head: r.access.detail }] : []),
        { value: "open", icon: "open_in_new", label: "Öffnen" },
        { value: "target", icon: "alt_route", label: "Ziel ändern", sub: r.service },
        { value: "access", icon: zt ? "lock_open" : "lock", label: zt ? "Zero Trust entfernen" : "Mit Zero Trust schützen" },
        "-",
        { value: "remove", icon: "delete", label: "Adresse entfernen", danger: true, sub: "Tunnel-Regel und DNS-Eintrag" },
      ], (v) => {
        if (v === "open") window.open(r.url, "_blank", "noopener");
        if (v === "target") create(r);
        if (v === "access") access(r, zt);
        if (v === "remove") remove(r);
      }, { align: "right" });
    }

    function result(title, res) {
      if (!res) return;
      openModal(`${sheetHead(title, res.ok ? "check_circle" : "error")}${stepsHtml(res.steps)}
        ${res.url && res.ok ? `<p class="sheet-hint">Erreichbar unter <a href="${esc(res.url)}" target="_blank" rel="noopener">${esc(res.url)}</a> (DNS braucht manchmal ein paar Sekunden).</p>` : ""}
        <div class="sheet-actions"><button type="button" class="btn-primary" data-close>OK</button></div>`);
      load();
    }

    async function create(cur) {
      if (!data?.tunnels?.length) return toast(data?.error || "Kein Cloudflare-Tunnel gefunden");
      const targets = [{ value: "", label: "Eigenes Ziel eingeben" }, ...data.targets.map((t) => ({ value: t.service, label: `${t.stack || t.container} · ${t.container} :${t.private} → ${t.service}` }))];
      const fields = [
        { name: "hostname", label: "Adresse", value: cur?.hostname || "", placeholder: "app.kulbarts.com" },
        { name: "path", label: "Pfad (optional)", value: cur?.path || "", placeholder: "leer = alles", hint: "Achtung: cloudflared liest den Pfad als Regex, z. B. ^/api" },
        { name: "pick", label: "Ziel", type: "select", value: cur ? "" : targets[1]?.value || "", options: targets, hint: "Container mit veröffentlichtem Port auf dem Server" },
        { name: "service", label: "Eigenes Ziel", value: cur?.service || "", placeholder: "http://10.7.0.1:8080", show: (v) => !v.pick },
        { name: "tunnel_id", label: "Tunnel", type: "select", value: cur?.tunnel_id || data.tunnels[0].id, options: data.tunnels.filter((t) => t.remote).map((t) => ({ value: t.id, label: t.name })) },
        ...(cur ? [] : [{ name: "zero_trust", label: "Mit Zero Trust schützen", type: "switch", value: true, hint: "nur nach Login über Cloudflare Access" },
          { name: "emails", label: "Erlaubte E-Mails", type: "textarea", rows: 2, placeholder: "du@gmail.com\n@kulbarts.com", show: (v) => v.zero_trust }]),
      ];
      let res = null;
      const send = async (v, replace) => api.post("/publish", { hostname: v.hostname, path: v.path, service: v.pick || v.service, tunnel_id: v.tunnel_id,
        zero_trust: !!v.zero_trust, emails: lines(v.emails), replace });
      const r = await formDialog(cur ? `Ziel ändern: ${cur.hostname}${cur.path}` : "Neue Adresse veröffentlichen", fields, {
        ok: cur ? "Speichern" : "Veröffentlichen", icon: "travel_explore",
        submit: async (v) => {
          try { res = await send(v, !!cur); } catch (e) {
            if (e.status !== 409) return e.message;
            if (!(await confirmDialog("Adresse ersetzen?", e.message, { ok: "Ersetzen", danger: true }))) return "Nicht ersetzt";
            try { res = await send(v, true); } catch (e2) { return e2.message; }
          }
          return "";
        },
      });
      if (r) result(res.ok ? "Veröffentlicht" : "Teilweise fehlgeschlagen", res);
    }

    async function access(r, on) {
      if (on) {
        if (!(await confirmDialog("Zero Trust entfernen?", `${r.url} ist danach für jeden ohne Login erreichbar.`, { ok: "Öffentlich machen", danger: true }))) return;
        const res = await attempt(() => api.post("/publish/access", { hostname: r.hostname, path: r.path, enabled: false }));
        if (res) { toast(res.detail); load(); }
        return;
      }
      const v = await formDialog(`Zero Trust: ${r.hostname}${r.path}`, [
        { name: "emails", label: "Erlaubte E-Mails", type: "textarea", rows: 3, placeholder: "du@gmail.com\n@kulbarts.com", hint: "eine pro Zeile, @domain.de = ganze Domain" },
      ], { ok: "Schützen", icon: "lock" });
      if (!v) return;
      const res = await attempt(() => api.post("/publish/access", { hostname: r.hostname, path: r.path, enabled: true, emails: lines(v.emails) }));
      if (res) { toast(res.detail); load(); }
    }

    async function remove(r) {
      const v = await formDialog(`Entfernen: ${r.hostname}${r.path}`, [
        { type: "head", key: "i", label: `Die Tunnel-Regel → ${r.service} kommt raus. Der DNS-Eintrag wird gelöscht, wenn sonst nichts mehr auf diesen Host zeigt.` },
        { name: "access", label: "Auch die Access-App löschen", type: "switch", value: false, hint: "nur die App genau für diese Adresse" },
      ], { ok: "Entfernen", icon: "delete" });
      if (!v) return;
      const res = await attempt(() => api.post("/publish/remove", { tunnel_id: r.tunnel_id, hostname: r.hostname, path: r.path, dns: true, access: v.access }));
      result(res?.ok ? "Entfernt" : "Teilweise fehlgeschlagen", res);
    }

    load();
    return () => { alive = false; };
  },
};
