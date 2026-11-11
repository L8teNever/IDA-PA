// Websites (nur IDA Server): statische Seiten und SPAs hosten – Dateien oder GitHub-Repo, Hostname/Pfad, öffentlich oder Zero Trust.
import { api } from "../api.js";
import { formDialog, lines } from "../form.js";
import { jobDialog } from "../jobview.js";
import { attempt, confirmDialog, esc, fmtAgo, popMenu, toast } from "../ui.js";
import { navigate } from "../app.js";
import { openSiteEditor } from "./site-editor.js";

const PROT = { true: ["lock", "Zero Trust", "pill ok"], false: ["public", "Öffentlich", "pill warn"] };

const FILTERS = [["all", "Alle"], ["public", "Öffentlich"], ["zt", "Zero Trust"], ["github", "GitHub"], ["files", "Eigene Dateien"], ["err", "Fehler"]];
const bare = (u) => u.replace(/^https:\/\//, "");

export default {
  id: "sites", label: "Websites", icon: "language", path: "/websites",
  match: (p) => (p === "/websites" || p === "/" ? {} : null),
  mount(el, _p, ctx) {
    let data = null, alive = true, q = "", filter = sessionStorage.getItem("st-filter") || "all";
    ctx.top(`<div class="topbar-left"><h2><span class="ms">language</span><span class="tb-title">Websites</span></h2><span class="count-pill" id="stCount" hidden></span></div>
      <div class="topbar-actions"><button type="button" class="btn-primary" id="stNew"><span class="ms">add</span><span class="lbl">Neue Website</span></button></div>`);
    el.innerHTML = `<div class="page">
      <div id="stInfo"></div>
      <div class="st-tools"><label class="fs-search st-search"><span class="ms">search</span><input id="stQ" type="search" placeholder="Name oder Domain suchen" autocomplete="off"></label>
        <div class="filter-row" id="stFilter">${FILTERS.map(([k, l]) => `<button type="button" class="fchip" data-f="${k}">${l}</button>`).join("")}</div></div>
      <div class="site-grid" id="stList"><div class="muted pad">Lade …</div></div></div>`;
    document.getElementById("stNew").onclick = () => create();
    el.querySelector("#stQ").oninput = (e) => { q = e.target.value.trim().toLowerCase(); drawList(); };
    el.querySelector("#stFilter").onclick = (e) => {
      const b = e.target.closest("[data-f]");
      if (!b) return;
      filter = b.dataset.f;
      sessionStorage.setItem("st-filter", filter);
      drawList();
    };

    async function load() {
      data = await api.get("/sites").catch((e) => { el.querySelector("#stList").innerHTML = `<div class="empty-stage">${esc(e.message)}</div>`; return null; });
      if (data && alive) draw();
    }

    function draw() {
      const n = data.nginx;
      const warn = [];
      if (!n.found) warn.push(`Der nginx-Dienst <b>sites</b> fehlt im Stack – <code>docker compose up -d sites</code> auf dem Server.`);
      else if (n.state !== "running") warn.push(`Der nginx-Dienst <b>${esc(n.name)}</b> läuft gerade nicht (${esc(n.state)}).`);
      if (!data.cloudflare) warn.push("Ohne <code>CLOUDFLARE_API_TOKEN</code> werden die Seiten nur ausgeliefert, aber nicht veröffentlicht.");
      else if (data.upstream_error) warn.push(esc(data.upstream_error));
      el.querySelector("#stInfo").innerHTML = warn.length ? `<div class="note warn"><span class="ms">warning</span><div>${warn.join("<br>")}</div></div>`
        : `<div class="st-status"><span class="st-led"></span><span>nginx <b>${esc(n.name)}</b> läuft</span><span class="st-sep">·</span><span class="mono ellip" title="Der Tunnel erreicht die Seiten hier">${esc(data.upstream)}</span>
           <span class="st-sep">·</span><a class="mono ellip" href="/files?path=${encodeURIComponent(data.dir)}" title="Ordner im Tool Dateien öffnen">${esc(data.dir)}</a></div>`;
      const c = el.querySelector("#stCount");
      c.hidden = !data.sites.length;
      c.textContent = data.sites.length;
      drawList();
    }

    const fits = (s) => {
      if (filter === "public" && s.zero_trust) return false;
      if (filter === "zt" && !s.zero_trust) return false;
      if (filter === "github" && s.source !== "github") return false;
      if (filter === "files" && s.source === "github") return false;
      if (filter === "err" && !(s.status === "error" || s.last_error)) return false;
      return !q || [s.name, s.repo, ...s.domains.map((d) => d.hostname + d.path)].some((x) => (x || "").toLowerCase().includes(q));
    };

    function drawList() {
      if (!data) return;
      el.querySelectorAll("#stFilter [data-f]").forEach((b) => b.classList.toggle("on", b.dataset.f === filter));
      const list = el.querySelector("#stList");
      if (!data.sites.length) {
        list.innerHTML = `<div class="empty-stage"><span class="ms big">language</span><br>Noch keine Website. Leg eine an – aus einem GitHub-Repo oder mit eigenen Dateien.</div>`;
        return;
      }
      const shown = data.sites.filter(fits).sort((a, b) => a.name.localeCompare(b.name, "de"));
      list.innerHTML = shown.length ? shown.map(card).join("")
        : `<div class="empty-stage"><span class="ms big">search_off</span><br>Keine Website passt dazu.</div>`;
    }

    function card(s) {
      const [pi, pl, pc] = PROT[!!s.zero_trust];
      const src = s.source === "github"
        ? `<span class="ms sm">code</span><span class="mono ellip">${esc(s.repo.replace(/^https:\/\/(www\.)?github\.com\//, ""))}</span><span class="muted">@${esc(s.branch)}</span>`
        : `<span class="ms sm">folder</span><span class="mono ellip">${esc(s.folder.split("/").pop())}${s.root ? "/" + esc(s.root) : ""}</span>`;
      const state = s.status === "error" ? `<span class="pill err" title="${esc(s.last_error)}"><span class="ms sm">error</span>Fehler</span>`
        : s.status === "setting_up" ? `<span class="pill"><span class="ms sm spin">progress_activity</span>Wird eingerichtet</span>` : "";
      const doms = s.domains.map((d) => `<span class="st-dom ${d.main ? "main" : ""}">
          <a href="${esc(d.url)}" target="_blank" rel="noopener" title="${esc(d.url)} öffnen"><span class="ms">${d.main ? "public" : "link"}</span><span class="ellip">${esc(bare(d.url))}</span><span class="ms st-go">open_in_new</span></a>
          ${d.main ? "" : `<button type="button" class="st-dom-x" data-undom="${esc(s.name)}" data-host="${esc(d.hostname)}" data-path="${esc(d.path)}" title="Domain entfernen" aria-label="Domain entfernen"><span class="ms">close</span></button>`}</span>`).join("");
      return `<div class="site-card" data-card="${esc(s.name)}">
        <div class="site-top"><span class="site-ic"><img src="${esc(s.url)}/favicon.ico" alt="" loading="lazy" onerror="this.remove()"><span class="ms">${s.spa ? "web" : "description"}</span></span>
          <div class="site-main"><b class="ellip">${esc(s.name)}</b><span class="site-meta">${src}</span></div>
          <button type="button" class="icon-btn sm" data-menu="${esc(s.name)}" aria-label="Aktionen"><span class="ms">more_vert</span></button></div>
        <div class="st-doms">${doms}<button type="button" class="st-dom-add" data-adddom="${esc(s.name)}" title="Weitere Domain, die auf diese Seite zeigt"><span class="ms">add</span>Domain</button></div>
        <div class="pill-row"><span class="${pc}"><span class="ms sm">${pi}</span>${pl}</span>${s.spa ? `<span class="pill">SPA</span>` : ""}${s.root ? `<span class="pill mono">/${esc(s.root)}</span>` : ""}
          ${s.auto_minutes ? `<span class="pill"><span class="ms sm">autorenew</span>alle ${s.auto_minutes} Min</span>` : ""}${state}</div>
        ${s.source === "github" ? `<div class="muted small">${s.last_commit ? `Stand <span class="mono">${esc(s.last_commit)}</span> · ${fmtAgo(s.last_deploy)}` : "Noch nicht geholt"}${s.last_error && s.status !== "error" ? ` · <span class="st-err">${esc(s.last_error)}</span>` : ""}</div>` : ""}
        <div class="btn-row st-actions"><button type="button" class="btn-tonal sm st-edit" data-edit="${esc(s.name)}"><span class="ms">code</span>Bearbeiten</button>
          <a class="btn-tonal sm" href="${esc(s.url)}" target="_blank" rel="noopener"><span class="ms">open_in_new</span>Öffnen</a>
          ${s.source === "github" ? `<button type="button" class="btn-tonal sm" data-deploy="${esc(s.name)}"><span class="ms">sync</span>Aktualisieren</button>` : ""}</div>
      </div>`;
    }

    const byName = (n) => data.sites.find((s) => s.name === n);
    el.addEventListener("click", (e) => {
      const d = e.target.closest("[data-deploy]");
      if (d) return jobDialog(`Aktualisieren: ${d.dataset.deploy}`, () => api.post(`/sites/${encodeURIComponent(d.dataset.deploy)}/deploy`), { onDone: load });
      const m = e.target.closest("[data-menu]");
      if (m) return menu(m, byName(m.dataset.menu));
      const a = e.target.closest("[data-adddom]");
      if (a) return addDomain(byName(a.dataset.adddom));
      const x = e.target.closest("[data-undom]");
      if (x) return removeDomain(byName(x.dataset.undom), x.dataset.host, x.dataset.path);
      const ed = e.target.closest("[data-edit]");
      if (ed) return editFiles(byName(ed.dataset.edit));
      // Klick auf die freie Fläche der Karte: Editor öffnen (Links und Knöpfe machen ihr Eigenes)
      const c = e.target.closest("[data-card]");
      if (c && !e.target.closest("a, button") && !getSelection()?.toString()) editFiles(byName(c.dataset.card));
    });

    async function editFiles(s) {
      try { await openSiteEditor(s); } catch (e) { toast(e.message || "Der Editor lässt sich nicht öffnen"); }
    }

    async function addDomain(s) {
      const r = await formDialog(`Domain hinzufügen: ${s.name}`, [
        { type: "head", key: "info", label: `Die Seite ist danach zusätzlich unter dieser Adresse erreichbar – mit denselben Dateien${s.zero_trust ? " und demselben Zero-Trust-Schutz" : ""}.` },
        { name: "hostname", label: "Adresse", placeholder: "www.kulbarts.com", hint: "Hostname im Internet" },
        { name: "path", label: "Pfad (optional)", placeholder: "/blog", hint: "nur unter diesem Pfad" },
      ], { ok: "Hinzufügen", icon: "add_link" });
      if (r?.hostname) jobDialog(`Domain hinzufügen: ${r.hostname}${r.path || ""}`, () => api.post(`/sites/${encodeURIComponent(s.name)}/domains`, { hostname: r.hostname, path: r.path || "" }), { onDone: load });
    }

    async function removeDomain(s, host, path) {
      if (!(await confirmDialog("Domain entfernen?", `${host}${path} zeigt danach nicht mehr auf ${s.name}. Was dafür bei Cloudflare angelegt wurde (Tunnel-Regel, DNS, Access), kommt mit weg. Die Dateien bleiben.`, { ok: "Entfernen", danger: true }))) return;
      jobDialog(`Domain entfernen: ${host}${path}`, () => api.post(`/sites/${encodeURIComponent(s.name)}/domains/remove`, { hostname: host, path }), { onDone: load });
    }

    function menu(anchor, s) {
      popMenu(anchor, [
        { value: "code", icon: "code", label: "Im Editor bearbeiten", sub: "Dateien, Code und Live-Vorschau" },
        { value: "open", icon: "open_in_new", label: "Öffnen", sub: bare(s.url) },
        { value: "files", icon: "folder_open", label: "Im Tool Dateien öffnen", sub: "Hochladen, Entpacken, Verschieben" },
        "-",
        { value: "domain", icon: "add_link", label: "Domain hinzufügen", sub: "weitere Adresse für dieselbe Seite" },
        { value: "edit", icon: "tune", label: "Einstellungen", sub: "SPA, Ordner, Branch, Auto-Update" },
        { value: "access", icon: s.zero_trust ? "lock_open" : "lock", label: s.zero_trust ? "Zugriff ändern" : "Mit Zero Trust schützen" },
        "-",
        { value: "remove", icon: "delete", label: "Website entfernen", danger: true, sub: "Tunnel-Regel, DNS und Access, die sie angelegt hat" },
      ], (v) => {
        if (v === "code") editFiles(s);
        if (v === "open") window.open(s.url, "_blank", "noopener");
        if (v === "files") navigate(`/files?path=${encodeURIComponent(s.folder + (s.root ? "/" + s.root : ""))}`);
        if (v === "domain") addDomain(s);
        if (v === "edit") edit(s);
        if (v === "access") access(s);
        if (v === "remove") remove(s);
      }, { align: "right" });
    }

    async function tunnelOptions() {
      if (!data?.cloudflare) return [];
      const t = await api.get("/sites/tunnels").catch((e) => { toast(e.message); return { tunnels: [] }; });
      return t.tunnels.map((x) => ({ value: x.id, label: `${x.name}${x.status ? ` (${x.status})` : ""}` }));
    }

    async function create() {
      const tunnels = await tunnelOptions();
      const gh = (v) => v.source === "github";
      const fields = [
        { name: "name", label: "Name", placeholder: "z. B. portfolio", hint: "Ordnername – Buchstaben, Zahlen, - und _" },
        { name: "hostname", label: "Adresse", placeholder: "seite.kulbarts.com", hint: "Hostname im Internet" },
        { name: "path", label: "Pfad (optional)", placeholder: "/blog", hint: "nur unter diesem Pfad, z. B. kulbarts.com/blog" },
        { name: "source", label: "Inhalt", type: "seg", value: "github", options: [{ value: "github", label: "GitHub-Repo", icon: "code" }, { value: "files", label: "Eigene Dateien", icon: "folder" }] },
        { name: "repo", label: "Repo", placeholder: "https://github.com/L8teNever/seite", show: gh },
        { name: "branch", label: "Branch", value: "main", show: gh },
        { name: "token", label: "Token für private Repos (optional)", type: "password", hint: "GitHub-Token mit Leserecht – wird nur hier gespeichert, nie im Repo", show: gh },
        { name: "auto", label: "Auto-Update", type: "select", value: "0", show: gh, options: [{ value: "0", label: "Aus – nur per Knopf" }, { value: "5", label: "alle 5 Minuten" }, { value: "15", label: "alle 15 Minuten" }, { value: "60", label: "stündlich" }, { value: "1440", label: "täglich" }] },
        { name: "root", label: "Ordner, der ausgeliefert wird (optional)", placeholder: "dist", hint: "leer = alles; z. B. dist, build oder public" },
        { name: "spa", label: "SPA-Modus", type: "switch", hint: "React, Vue … – alle Routen liefern index.html" },
        ...(tunnels.length ? [{ name: "tunnel_id", label: "Cloudflare-Tunnel", type: "select", options: tunnels }] : []),
        { name: "zero_trust", label: "Mit Zero Trust schützen", type: "switch", hint: "nur nach Login über Cloudflare Access" },
        { name: "emails", label: "Erlaubte E-Mails", type: "textarea", rows: 2, placeholder: "du@gmail.com\n@kulbarts.com", hint: "eine pro Zeile, @domain.de = ganze Domain", show: (v) => v.zero_trust },
      ];
      let job = null;
      const r = await formDialog("Neue Website", fields, {
        ok: "Anlegen & veröffentlichen", icon: "language",
        submit: async (v) => {
          try {
            job = await api.post("/sites", { name: v.name, hostname: v.hostname, path: v.path, source: v.source, repo: v.repo, branch: v.branch || "main",
              token: v.token, auto_minutes: Number(v.auto || 0), root: v.root, spa: v.spa, tunnel_id: v.tunnel_id || "", zero_trust: v.zero_trust, emails: lines(v.emails) });
          } catch (e) { return e.message; }
          return "";
        },
      });
      if (r && job) { load(); await jobDialog(`Website anlegen: ${job.site.name}`, async () => job, { onDone: load }); }
    }

    async function edit(s) {
      const gh = s.source === "github";
      const fields = [
        ...(gh ? [{ name: "branch", label: "Branch", value: s.branch },
          { name: "token", label: s.has_token ? "Token (gespeichert – leer lassen behält ihn, „-“ entfernt ihn)" : "Token für private Repos (optional)", type: "password" },
          { name: "auto", label: "Auto-Update", type: "select", value: String(s.auto_minutes || 0), options: [{ value: "0", label: "Aus – nur per Knopf" }, { value: "5", label: "alle 5 Minuten" }, { value: "15", label: "alle 15 Minuten" }, { value: "60", label: "stündlich" }, { value: "1440", label: "täglich" }] }] : []),
        { name: "root", label: "Ordner, der ausgeliefert wird", value: s.root, placeholder: "leer = alles" },
        { name: "spa", label: "SPA-Modus", type: "switch", value: !!s.spa },
      ];
      const r = await formDialog(`Einstellungen: ${s.name}`, fields, { icon: "tune" });
      if (!r) return;
      const body = { root: r.root, spa: r.spa };
      if (gh) { body.branch = r.branch; body.auto_minutes = Number(r.auto || 0); if (r.token) body.token = r.token === "-" ? "" : r.token; }
      if (await attempt(() => api.put(`/sites/${encodeURIComponent(s.name)}`, body), "Gespeichert")) load();
    }

    async function access(s) {
      const r = await formDialog(`Zugriff: ${s.name}`, [
        { name: "zero_trust", label: "Mit Zero Trust schützen", type: "switch", value: !!s.zero_trust, hint: "aus = für jeden ohne Login erreichbar" },
        { name: "emails", label: "Erlaubte E-Mails", type: "textarea", rows: 3, value: (s.emails || []).join("\n"), placeholder: "du@gmail.com\n@kulbarts.com", show: (v) => v.zero_trust },
      ], { icon: "lock" });
      if (!r) return;
      if (!r.zero_trust && s.zero_trust && !(await confirmDialog("Schutz entfernen?", `${s.url} ist danach für jeden ohne Login erreichbar.`, { ok: "Öffentlich machen", danger: true }))) return;
      const res = await attempt(() => api.put(`/sites/${encodeURIComponent(s.name)}`, { zero_trust: r.zero_trust, emails: lines(r.emails) }));
      if (res) { toast(res.detail || "Gespeichert"); load(); }
    }

    async function remove(s) {
      const r = await formDialog(`Website entfernen: ${s.name}`, [
        { type: "head", key: "info", label: `${s.domains.map((d) => bare(d.url)).join(", ")} ${s.domains.length > 1 ? "gehen" : "geht"} offline. Was die Website bei Cloudflare angelegt hat (Tunnel-Regel, DNS, Access-App), kommt mit weg.` },
        { name: "delete_files", label: "Auch die Dateien löschen", type: "switch", hint: s.folder },
      ], { ok: "Entfernen", icon: "delete" });
      if (r) jobDialog(`Website entfernen: ${s.name}`, () => api.post(`/sites/${encodeURIComponent(s.name)}/remove`, { delete_files: r.delete_files }), { onDone: load });
    }

    load();
    const timer = setInterval(() => { if (!document.hidden) load(); }, 20000);
    return () => { alive = false; clearInterval(timer); };
  },
};
