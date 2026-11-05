// Websites (nur IDA Server): statische Seiten und SPAs hosten – Dateien oder GitHub-Repo, Hostname/Pfad, öffentlich oder Zero Trust.
import { api } from "../api.js";
import { formDialog, lines } from "../form.js";
import { jobDialog } from "../jobview.js";
import { attempt, confirmDialog, esc, fmtAgo, popMenu, toast } from "../ui.js";

const PROT = { true: ["lock", "Zero Trust", "pill ok"], false: ["public", "Öffentlich", "pill warn"] };

export default {
  id: "sites", label: "Websites", icon: "language", path: "/websites",
  match: (p) => (p === "/websites" || p === "/" ? {} : null),
  mount(el, _p, ctx) {
    let data = null, alive = true;
    ctx.top(`<div class="topbar-left"><h2><span class="ms">language</span><span class="tb-title">Websites</span></h2></div>
      <div class="topbar-actions"><button type="button" class="btn-primary" id="stNew"><span class="ms">add</span>Neue Website</button></div>`);
    el.innerHTML = `<div class="page"><div id="stInfo"></div><div class="site-grid" id="stList"><div class="muted pad">Lade …</div></div></div>`;
    document.getElementById("stNew").onclick = () => create();

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
        : `<div class="note"><span class="ms">dns</span><div>Alle Seiten laufen im nginx <b>${esc(n.name)}</b>, der Tunnel erreicht ihn unter <code>${esc(data.upstream)}</code>. Dateien: <code>${esc(data.dir)}</code></div></div>`;
      const list = el.querySelector("#stList");
      if (!data.sites.length) {
        list.innerHTML = `<div class="empty-stage"><span class="ms big">language</span><br>Noch keine Website. Leg eine an – aus einem GitHub-Repo oder mit eigenen Dateien.</div>`;
        return;
      }
      list.innerHTML = data.sites.map(card).join("");
    }

    function card(s) {
      const [pi, pl, pc] = PROT[!!s.zero_trust];
      const src = s.source === "github"
        ? `<span class="ms sm">code</span><span class="mono ellip">${esc(s.repo.replace(/^https:\/\/(www\.)?github\.com\//, ""))}</span><span class="muted">@${esc(s.branch)}</span>`
        : `<span class="ms sm">folder</span><span>Eigene Dateien</span>`;
      const state = s.status === "error" ? `<span class="pill err" title="${esc(s.last_error)}"><span class="ms sm">error</span>Fehler</span>`
        : s.status === "setting_up" ? `<span class="pill"><span class="ms sm spin">progress_activity</span>Wird eingerichtet</span>` : "";
      return `<div class="site-card">
        <div class="site-top"><span class="site-ic"><span class="ms">${s.spa ? "web" : "description"}</span></span>
          <div class="site-main"><b>${esc(s.name)}</b><a class="mono ellip" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url.replace(/^https:\/\//, ""))}</a></div>
          <button type="button" class="icon-btn sm" data-menu="${esc(s.name)}" aria-label="Aktionen"><span class="ms">more_vert</span></button></div>
        <div class="site-meta">${src}</div>
        <div class="pill-row"><span class="${pc}"><span class="ms sm">${pi}</span>${pl}</span>${s.spa ? `<span class="pill">SPA</span>` : ""}${s.root ? `<span class="pill mono">/${esc(s.root)}</span>` : ""}
          ${s.auto_minutes ? `<span class="pill"><span class="ms sm">autorenew</span>alle ${s.auto_minutes} Min</span>` : ""}${state}</div>
        ${s.source === "github" ? `<div class="muted small">${s.last_commit ? `Stand <span class="mono">${esc(s.last_commit)}</span> · ${fmtAgo(s.last_deploy)}` : "Noch nicht geholt"}${s.last_error && s.status !== "error" ? ` · <span class="st-err">${esc(s.last_error)}</span>` : ""}</div>` : ""}
        <div class="btn-row">${s.source === "github" ? `<button type="button" class="btn-tonal sm" data-deploy="${esc(s.name)}"><span class="ms">sync</span>Aktualisieren</button>` : ""}
          <a class="btn-tonal sm" href="/files?path=${encodeURIComponent(s.folder + (s.root ? "/" + s.root : ""))}"><span class="ms">folder_open</span>Dateien</a></div>
      </div>`;
    }

    el.addEventListener("click", (e) => {
      const d = e.target.closest("[data-deploy]");
      if (d) jobDialog(`Aktualisieren: ${d.dataset.deploy}`, () => api.post(`/sites/${encodeURIComponent(d.dataset.deploy)}/deploy`), { onDone: load });
      const m = e.target.closest("[data-menu]");
      if (m) menu(m, data.sites.find((s) => s.name === m.dataset.menu));
    });

    function menu(anchor, s) {
      popMenu(anchor, [
        { value: "open", icon: "open_in_new", label: "Öffnen", sub: s.url.replace(/^https:\/\//, "") },
        { value: "edit", icon: "tune", label: "Einstellungen", sub: "SPA, Ordner, Branch, Auto-Update" },
        { value: "access", icon: s.zero_trust ? "lock_open" : "lock", label: s.zero_trust ? "Zugriff ändern" : "Mit Zero Trust schützen" },
        "-",
        { value: "remove", icon: "delete", label: "Website entfernen", danger: true, sub: "Tunnel-Regel, DNS und Access, die sie angelegt hat" },
      ], (v) => {
        if (v === "open") window.open(s.url, "_blank", "noopener");
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
        { type: "head", key: "info", label: `${s.url} geht offline. Was die Website bei Cloudflare angelegt hat (Tunnel-Regel, DNS, Access-App), kommt mit weg.` },
        { name: "delete_files", label: "Auch die Dateien löschen", type: "switch", hint: s.folder },
      ], { ok: "Entfernen", icon: "delete" });
      if (r) jobDialog(`Website entfernen: ${s.name}`, () => api.post(`/sites/${encodeURIComponent(s.name)}/remove`, { delete_files: r.delete_files }), { onDone: load });
    }

    load();
    const timer = setInterval(() => { if (!document.hidden) load(); }, 20000);
    return () => { alive = false; clearInterval(timer); };
  },
};
