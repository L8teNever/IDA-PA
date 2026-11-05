// Updates (nur IDA Server): neue Versionen der laufenden Docker-Images finden und einspielen.
// Einstellbar: wann gesucht wird, was bei einem Fund passiert und für welche Dienste. Der Dialog zeigt die Regel live in einem Satz.
import { api } from "../api.js";
import { formDialog } from "../form.js";
import { jobDialog } from "../jobview.js";
import { attempt, confirmDialog, esc, fmtAgo, fmtDateTime, openModal, sheetHead, toast } from "../ui.js";

const STATUS = {
  update: ["upgrade", "Update", "pill warn"], current: ["check_circle", "Aktuell", "pill ok"], error: ["error", "Nicht prüfbar", "pill err"],
  local: ["construction", "Lokal", "pill"], pinned: ["push_pin", "Feste Version", "pill"],
};
const FILTERS = [["update", "Updates"], ["all", "Alle"], ["other", "Nicht prüfbar"]];
const DAILY_AT = { 1: "stündlich", 24: "alle 24 Stunden" };

const everyText = (h) => DAILY_AT[h] || `alle ${h} Stunden`;
const shortImage = (i) => i.replace(/^ghcr\.io\/l8tenever\//, "");
const dienst = (n) => `${n} ${n === 1 ? "Dienst" : "Dienste"}`;

// Die Regel als Satz (Karte und Vorschau im Dialog)
function rule(s) {
  if (s.check === "off") return "Sucht nicht von selbst. Nur per Knopf.";
  const when = s.check === "daily" ? `Sucht täglich um ${s.check_at} Uhr` : `Sucht ${everyText(s.every_hours)}`;
  if (s.action === "notify") return `${when} und zeigt neue Versionen nur an.`;
  const n = s.picks.length;
  const names = n <= 3 ? s.picks.join(", ") : `${n} ausgewählte Dienste`;
  const who = s.scope === "only" ? `nur ${n ? names : "die Auswahl"}` : s.scope === "except" && n ? `alles außer ${names}` : "alles";
  return s.action === "now" ? `${when} und aktualisiert ${who} sofort.` : `${when} und aktualisiert ${who} um ${s.apply_at} Uhr.`;
}

const payload = (v) => ({ check: v.check, every_hours: Number(v.every_hours) || 6, check_at: v.check_at, action: v.check === "off" ? "notify" : v.action,
  apply_at: v.apply_at, scope: v.scope, picks: v.scope === "all" ? [] : v.picks, cleanup: !!v.cleanup });

export default {
  id: "updates", label: "Updates", icon: "system_update_alt", path: "/updates",
  match: (p) => (p === "/updates" ? {} : null),
  mount(el, _p, ctx) {
    let data = null, q = "", filter = "update", alive = true, timer = 0;
    ctx.top(`<div class="topbar-left"><h2><span class="ms">system_update_alt</span><span class="tb-title">Updates</span></h2></div>
      <div class="topbar-actions"><button type="button" class="btn-tonal" id="upCheck"><span class="ms">search</span><span class="up-lbl">Jetzt suchen</span></button>
      <button type="button" class="btn-primary" id="upAll" hidden><span class="ms">upgrade</span><span class="up-lbl">Alle aktualisieren</span></button></div>`);
    el.innerHTML = `<div class="page"><div id="upInfo"></div><div class="tiles" id="upTiles"></div>
      <section class="card"><div class="card-head wrap"><h3><span class="ms">schedule</span>Automatik</h3>
        <button type="button" class="btn-tonal" id="upEdit"><span class="ms">tune</span>Einstellen</button></div>
        <p class="up-rule" id="upRule"></p><div class="up-next muted" id="upNext"></div></section>
      <section class="card"><div class="card-head wrap"><h3><span class="ms">deployed_code</span>Dienste</h3>
        <label class="side-search pb-search"><span class="ms">search</span><input id="upQ" type="search" placeholder="Stack, Dienst oder Image" autocomplete="off"></label></div>
        <div class="seg-mini gr-chips" id="upFilter"></div><div id="upList" class="muted">Lade …</div></section>
      <section class="card"><div class="card-head"><h3><span class="ms">history</span>Verlauf</h3></div><div id="upHist"></div></section></div>`;
    const $ = (id) => document.getElementById(id);

    async function load() {
      const d = await api.get("/updates").catch((e) => ({ error: e.message }));
      if (!alive) return;
      if (d.error && data) return toast(d.error);
      data = d;
      draw();
      clearTimeout(timer);
      if (data.job || data.self_pending) timer = setTimeout(load, 5000);   // läuft etwas: Stand mitverfolgen
    }

    const open = () => data.results.filter((r) => r.status === "update" && r.can);

    function info() {
      let out = "";
      if (data.check_error) out += `<div class="note warn"><span class="ms">error</span><div>Letzte Suche fehlgeschlagen: ${esc(data.check_error)}</div></div>`;
      if (data.watchtower) out += `<div class="note warn"><span class="ms">warning</span><div><b>Watchtower läuft auch.</b> Dann aktualisieren zwei Dienste, und die Regeln hier gelten nicht für ihn. Nimm <code>autoupdate</code> aus <code>COMPOSE_PROFILES</code>.</div></div>`;
      if (data.job) out += `<div class="note"><span class="ms spin">progress_activity</span><div>${esc(data.job.title)} laufen gerade.<div class="lk-note-act"><button type="button" class="btn-tonal" id="upWatch"><span class="ms">terminal</span>Ausgabe ansehen</button></div></div></div>`;
      if (data.self_pending) out += `<div class="note"><span class="ms spin">progress_activity</span><div>Der Server startet neu, um sich selbst zu aktualisieren.</div></div>`;
      return out;
    }

    function tiles() {
      const r = data.results;
      const manual = r.filter((x) => x.status === "update" && !x.can).length;
      const other = r.filter((x) => x.status === "error").length;
      const tile = (ic, label, val, sub) => `<div class="tile"><div class="tile-top"><span class="ms">${ic}</span>${label}</div><div class="tile-val">${val}</div><div class="tile-sub">${esc(sub)}</div></div>`;
      return tile("upgrade", "Updates", open().length, manual ? `dazu ${manual} nur von Hand` : "bereit zum Einspielen")
        + tile("check_circle", "Aktuell", r.filter((x) => x.status === "current").length, `von ${r.length} Diensten`)
        + tile("help", "Nicht prüfbar", other, `${r.filter((x) => x.status === "local").length} lokal gebaut, ${r.filter((x) => x.status === "pinned").length} fest`)
        + tile("search", "Letzte Suche", data.last_check ? esc(fmtAgo(data.last_check)) : "–", data.last_check ? fmtDateTime(data.last_check) : "läuft gleich an");
    }

    function next() {
      const t = [];
      if (data.next.check) t.push(`Nächste Suche ${fmtDateTime(data.next.check)}`);
      if (data.next.apply) t.push(`nächstes Update ${fmtDateTime(data.next.apply)}`);
      return esc(t.join(" · "));
    }

    const keep = (r) => (filter === "all" || (filter === "update" && r.status === "update") || (filter === "other" && (r.status === "error" || r.status === "local" || r.status === "pinned")))
      && (!q || [r.stack, r.service, r.container, r.image].join(" ").toLowerCase().includes(q));

    function draw() {
      if (data.error) { $("upInfo").innerHTML = `<div class="note warn"><span class="ms">error</span><div>${esc(data.error)}</div></div>`; return; }
      $("upInfo").innerHTML = info();
      $("upTiles").innerHTML = tiles();
      $("upRule").textContent = rule(data.settings);
      $("upNext").innerHTML = next();
      const all = $("upAll");
      if (all) { all.hidden = !open().length || !!data.job; all.querySelector(".up-lbl").textContent = `Alle aktualisieren (${open().length})`; }
      const counts = { update: data.results.filter((r) => r.status === "update").length, all: data.results.length, other: data.results.filter((r) => ["error", "local", "pinned"].includes(r.status)).length };
      $("upFilter").innerHTML = FILTERS.map(([k, l]) => `<button type="button" class="${k === filter ? "on" : ""}" data-f="${k}">${l} ${counts[k]}</button>`).join("");
      const rows = data.results.filter(keep);
      const stacks = [...new Set(rows.map((r) => r.stack))].sort();
      $("upList").innerHTML = stacks.length ? stacks.map((s) => group(s, rows.filter((r) => r.stack === s))).join("")
        : `<div class="muted pad">${!data.last_check ? "Noch nicht gesucht." : filter === "update" && !q ? "Alles aktuell." : "Nichts gefunden."}</div>`;
      $("upHist").innerHTML = data.history.length ? `<div class="pb-rows">${data.history.map(past).join("")}</div>` : `<div class="muted pad">Noch keine Updates.</div>`;
    }

    function group(name, rows) {
      const ready = rows.filter((r) => r.status === "update" && r.can);
      return `<div class="up-stack"><div class="up-head"><b class="ellip">${esc(name)}</b>
        ${ready.length && !data.job ? `<button type="button" class="btn-tonal sm" data-up="${esc(ready.map((r) => r.key).join(" "))}">Aktualisieren${ready.length > 1 ? ` (${ready.length})` : ""}</button>` : ""}</div>
        <div class="pb-rows">${rows.map(item).join("")}</div></div>`;
    }

    function item(r) {
      const [ic, label, cls] = STATUS[r.status] || STATUS.error;
      const note = r.message || r.why;
      const auto = r.status === "update" && r.can && data.settings.action !== "notify" && !r.auto;
      return `<div class="pb-row"><span class="ms pb-ic">${ic}</span>
        <div class="pb-main"><span class="ellip">${esc(r.service)}</span><small class="muted ellip mono">${esc(shortImage(r.image))}</small>
          ${note ? `<small class="muted ellip">${esc(note)}</small>` : ""}</div>
        ${auto ? `<span class="pill" title="Von der Automatik ausgenommen">Automatik aus</span>` : ""}
        <span class="${cls}">${label}</span>
        ${r.status === "update" && r.can && !data.job ? `<button type="button" class="icon-btn sm" data-up="${esc(r.key)}" aria-label="${esc(r.service)} aktualisieren"><span class="ms">upgrade</span></button>` : ""}</div>`;
    }

    function past(h) {
      const parts = [h.updated.length && `${dienst(h.updated.length)} aktualisiert`, h.failed.length && `${h.failed.length} fehlgeschlagen`,
        h.problem.length && `${h.problem.length} läuft nicht sauber`, h.later.length && `${h.later.length} verschoben`, h.running && "Server startet neu"].filter(Boolean);
      const names = [...h.failed, ...h.problem, ...h.updated, ...h.later].join(", ");
      return `<button type="button" class="pb-row up-past" data-log="${esc(h.id)}"><span class="ms ${h.ok ? "lk-ok" : "lk-no"}">${h.ok ? "check_circle" : "error"}</span>
        <div class="pb-main"><span class="ellip">${esc(parts.join(" · ") || "Nichts zu tun")}</span><small class="muted ellip">${esc(names)}</small></div>
        <small class="muted up-when">${esc(fmtDateTime(h.at))} · ${h.auto ? "Automatik" : "von Hand"}</small></button>`;
    }

    async function search() {
      const b = $("upCheck");
      b.disabled = true;
      const r = await attempt(() => api.post("/updates/check"));
      if (!alive) return;
      b.disabled = false;
      if (r) toast(r.found ? `${r.found} ${r.found === 1 ? "Update" : "Updates"} gefunden` : "Alles aktuell");
      load();
    }

    async function run(keys) {
      const rows = data.results.filter((r) => keys.includes(r.key));
      const own = rows.some((r) => r.own);
      const list = rows.length <= 6 ? rows.map((r) => r.key).join(", ") : dienst(rows.length);
      if (!(await confirmDialog("Jetzt aktualisieren?", `${list} ${rows.length === 1 ? "wird" : "werden"} kurz neu gestartet.${own ? " Der Server ist auch dabei, die Seite ist dann kurz weg." : ""}`, { ok: "Aktualisieren" }))) return;
      await jobDialog("Updates", () => api.post("/updates/apply", { keys }), { onDone: () => alive && load() });
    }

    async function showLog(id) {
      const d = await attempt(() => api.get(`/updates/log/${id}`));
      if (!d) return;
      const m = openModal(`${sheetHead("Protokoll", "terminal")}<pre class="log job-log"></pre>
        <div class="sheet-actions"><button type="button" class="btn-primary" data-close>Schließen</button></div>`, { wide: true });
      m.el.querySelector("pre").textContent = d.lines.join("\n") || "Keine Ausgabe.";
    }

    async function edit() {
      const s = data.settings;
      const options = data.stacks.flatMap((x) => [{ value: x.name, label: x.name },
        ...(x.services.length > 1 ? x.services.map((n) => ({ value: `${x.name}/${n}`, label: n, parent: x.name })) : [])]);
      const auto = (v) => v.check !== "off" && v.action !== "notify";
      const fields = [
        { name: "check", label: "Wann suchen?", type: "seg", value: s.check,
          options: [{ value: "interval", label: "Regelmäßig" }, { value: "daily", label: "Täglich um" }, { value: "off", label: "Nur per Knopf" }] },
        { name: "every_hours", label: "Wie oft?", type: "select", value: s.every_hours, show: (v) => v.check === "interval",
          options: [1, 3, 6, 12, 24].map((h) => ({ value: h, label: h === 1 ? "Jede Stunde" : `Alle ${h} Stunden` })) },
        { name: "check_at", label: "Uhrzeit", type: "time", value: s.check_at, show: (v) => v.check === "daily" },
        { name: "action", label: "Neue Version gefunden: was dann?", type: "seg", value: s.action, show: (v) => v.check !== "off",
          options: [{ value: "notify", label: "Nur anzeigen" }, { value: "now", label: "Sofort updaten" }, { value: "at", label: "Um Uhrzeit" }] },
        { name: "apply_at", label: "Update um", type: "time", value: s.apply_at, show: (v) => v.check !== "off" && v.action === "at" },
        { name: "scope", label: "Für welche Dienste?", type: "seg", value: s.scope, show: auto,
          options: [{ value: "all", label: "Alle" }, { value: "except", label: "Alle außer" }, { value: "only", label: "Nur diese" }] },
        { name: "picks", label: "Auswahl", type: "multi", value: [...s.picks], options, show: (v) => auto(v) && v.scope !== "all",
          hint: "Ein ganzer Stack oder einzelne Dienste. Datenbanken lässt man oft besser von Hand." },
        { name: "cleanup", label: "Alte Images löschen", type: "switch", value: s.cleanup, show: auto, hint: "Erst nachdem der neue Container sauber läuft. Spart Platz, ein Zurück zur alten Version geht dann nicht mehr." },
        { type: "live", key: "sum", render: (v) => `<span class="ms">info</span><span>${esc(rule(payload(v)))}</span>` },
      ];
      const ok = await formDialog("Automatik", fields, {
        ok: "Speichern", icon: "schedule",
        submit: async (v) => {
          const p = payload(v);
          if (auto(p) && p.scope === "only" && !p.picks.length) return "Wähle mindestens einen Dienst aus";
          try { await api.put("/updates/settings", p); } catch (e) { return e.message; }
          return "";
        },
      });
      if (ok) { toast("Gespeichert"); load(); }
    }

    $("upCheck").onclick = search;
    $("upAll").onclick = () => run(open().map((r) => r.key));
    $("upEdit").onclick = edit;
    $("upQ").oninput = (e) => { q = e.target.value.toLowerCase(); draw(); };
    el.addEventListener("click", (e) => {
      const f = e.target.closest("[data-f]");
      if (f) { filter = f.dataset.f; return draw(); }
      const u = e.target.closest("[data-up]");
      if (u) return void run(u.dataset.up.split(" "));
      const l = e.target.closest("[data-log]");
      if (l) return void showLog(l.dataset.log);
      if (e.target.closest("#upWatch")) jobDialog("Updates", async () => ({ job: data.job }), { onDone: () => alive && load() });
    });

    load();
    return () => { alive = false; clearTimeout(timer); };
  },
};
