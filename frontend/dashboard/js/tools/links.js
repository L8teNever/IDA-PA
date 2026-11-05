// Weiterleitungen (nur IDA Server): Kurzlinks anlegen und verwalten. Weitergeleitet wird im öffentlichen Dienst „links“.
// Jedes Kürzel gilt auf allen vier Domains. Regeln: wie oft, wie lange, welche Geräte – der Dialog zeigt sie live in einem Satz.
import { api } from "../api.js";
import { formDialog } from "../form.js";
import { stepsHtml } from "../jobview.js";
import { attempt, confirmDialog, esc, fmtDateTime, openModal, popMenu, sheetHead, toast } from "../ui.js";

const STATUS = {
  active: ["check_circle", "Aktiv", "pill ok"], scheduled: ["schedule", "Geplant", "pill"], expired: ["timer_off", "Abgelaufen", "pill warn"],
  used_up: ["block", "Aufgebraucht", "pill warn"], disabled: ["pause_circle", "Aus", "pill"],
};
const REASON = { disabled: "deaktiviert", not_yet: "noch nicht aktiv", expired: "abgelaufen", used_up: "aufgebraucht", device_used: "Gerät hatte ihn schon", other_device: "anderes Gerät" };

const toInput = (sec) => (sec ? new Date(sec * 1000 - new Date(sec * 1000).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "");
const fromInput = (v) => (v ? Math.floor(new Date(v).getTime() / 1000) : null);
const hoursText = (h) => `${String(h).replace(".", ",")} ${h === 1 ? "Stunde" : "Stunden"}`;
const copy = (text) => attempt(() => navigator.clipboard.writeText(text), "Kopiert");

// Die Regeln eines Links als kurze Teilsätze (Liste und Vorschau im Dialog)
function rules(l) {
  const out = [l.max_uses === 1 ? "einmal öffnen" : l.max_uses ? `${l.max_uses}× öffnen` : "unbegrenzt oft"];
  if (l.valid_from && l.valid_until) out.push(`${fmtDateTime(l.valid_from)} – ${fmtDateTime(l.valid_until)}`);
  else if (l.valid_from) out.push(`ab ${fmtDateTime(l.valid_from)}`);
  else if (l.valid_until) out.push(`bis ${fmtDateTime(l.valid_until)}`);
  if (l.hours) out.push(`${hoursText(l.hours)} ab ${l.hours_from === "first" ? "dem ersten Öffnen" : "Erstellen"}`);
  if (l.device_mode === "once") out.push("jedes Gerät nur einmal");
  if (l.device_mode === "bind") out.push("nur das erste Gerät");
  if (l.confirm) out.push("mit „Weiter“-Seite");
  return out;
}

function payload(v) {
  return {
    target: v.target, note: v.note, domain: v.domain, code: v.code, confirm: !!v.confirm, device_mode: v.device_mode,
    max_uses: v.uses_mode === "once" ? 1 : v.uses_mode === "count" ? Number(v.uses) || null : null,
    valid_from: v.time_mode === "range" ? fromInput(v.from) : null,
    valid_until: v.time_mode === "range" || v.time_mode === "until" ? fromInput(v.until) : null,
    hours: v.time_mode === "hours" ? Number(v.hours) || null : null,
    hours_from: v.hours_from,
  };
}

function problem(p, v) {
  if (!v.target.trim()) return "Wohin soll der Link führen?";
  if (v.uses_mode === "count" && !p.max_uses) return "Wie oft darf er geöffnet werden?";
  if (v.time_mode === "until" && !p.valid_until) return "Bis wann soll der Link gelten?";
  if (v.time_mode === "range" && !(p.valid_from && p.valid_until)) return "Zeitraum: Von und Bis ausfüllen";
  if (v.time_mode === "hours" && !p.hours) return "Wie viele Stunden soll der Link gelten?";
  return "";
}

export default {
  id: "links", label: "Weiterleitungen", icon: "link", path: "/weiterleitungen",
  match: (p) => (p === "/weiterleitungen" ? {} : null),
  mount(el, _p, ctx) {
    let data = null, q = "", alive = true;
    ctx.top(`<div class="topbar-left"><h2><span class="ms">link</span><span class="tb-title">Weiterleitungen</span></h2></div>
      <div class="topbar-actions"><button type="button" class="icon-btn" id="lkReload" aria-label="Neu laden"><span class="ms">refresh</span></button>
      <button type="button" class="btn-primary" id="lkNew"><span class="ms">add</span>Neuer Link</button></div>`);
    el.innerHTML = `<div class="page"><div id="lkInfo"></div>
      <section class="card"><div class="card-head wrap"><h3><span class="ms">link</span>Links</h3>
        <label class="side-search pb-search"><span class="ms">search</span><input id="lkQ" type="search" placeholder="Kürzel, Ziel oder Notiz" autocomplete="off"></label></div>
        <div id="lkList" class="muted">Lade …</div></section></div>`;
    document.getElementById("lkNew").onclick = () => edit();
    document.getElementById("lkReload").onclick = () => load();
    el.querySelector("#lkQ").oninput = (e) => { q = e.target.value.toLowerCase(); draw(); };

    async function load() {
      data = await api.get("/links").catch((e) => ({ error: e.message, links: [], domains: [], tunnels: [] }));
      if (alive) draw();
    }

    function info() {
      const open = data.domains.filter((d) => d.ready === false);
      const locked = data.domains.filter((d) => d.access);
      let out = "";
      if (data.error) out += `<div class="note warn"><span class="ms">error</span><div>${esc(data.error)}</div></div>`;
      if (data.cloudflare === false) out += `<div class="note warn"><span class="ms">key_off</span><div><code>CLOUDFLARE_API_TOKEN</code> fehlt – die Domains lassen sich so nicht automatisch einrichten.</div></div>`;
      if (data.upstream_error) out += `<div class="note warn"><span class="ms">error</span><div>${esc(data.upstream_error)}</div></div>`;
      if (locked.length) out += `<div class="note warn"><span class="ms">lock</span><div>Eine Access-App schützt ${locked.map((d) => `<b>${esc(d.domain)}</b>`).join(", ")}. Dort verlangen Links einen Login.</div></div>`;
      if (open.length && !data.upstream_error) {
        out += `<div class="note warn"><span class="ms">dns</span><div>Noch nicht eingerichtet: ${open.map((d) => `<b>${esc(d.domain)}</b>`).join(", ")}.
          Tunnel-Regel und DNS, ohne Zero Trust.<div class="lk-note-act"><button type="button" class="btn-tonal" id="lkSetup"><span class="ms">bolt</span>Domains einrichten</button></div></div></div>`;
      }
      return out;
    }

    function draw() {
      el.querySelector("#lkInfo").innerHTML = info();
      const b = el.querySelector("#lkSetup");
      if (b) b.onclick = () => setup();
      const rows = data.links.filter((l) => !q || [l.code, l.target, l.note].join(" ").toLowerCase().includes(q));
      el.querySelector("#lkList").innerHTML = rows.length ? `<div class="pb-rows">${rows.map(row).join("")}</div>`
        : `<div class="muted pad">${data.links.length ? "Nichts gefunden." : "Noch keine Links. Leg oben den ersten an."}</div>`;
    }

    function row(l) {
      const [ic, label, cls] = STATUS[l.status] || STATUS.active;
      const i = data.links.indexOf(l);
      const off = l.status !== "active" && l.status !== "scheduled";
      return `<div class="pb-row ${off ? "lk-off" : ""}">
        <span class="ms pb-ic">${ic}</span>
        <div class="pb-main"><a class="mono ellip" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.url.replace("https://", ""))}</a>
          <small class="muted ellip"><span class="ms sm">subdirectory_arrow_right</span><span class="ellip">${esc(l.note || l.target)}</span></small>
          <small class="muted ellip">${esc(rules(l).join(" · "))}</small></div>
        <span class="lk-uses" title="So oft geöffnet"><span class="ms sm">ads_click</span>${l.uses}${l.max_uses ? `/${l.max_uses}` : ""}</span>
        <span class="${cls} lk-state">${label}</span>
        <button type="button" class="icon-btn sm" data-copy="${i}" aria-label="Link kopieren"><span class="ms">content_copy</span></button>
        <button type="button" class="icon-btn sm" data-m="${i}" aria-label="Aktionen"><span class="ms">more_vert</span></button></div>`;
    }

    el.addEventListener("click", (e) => {
      const c = e.target.closest("[data-copy]");
      if (c) return void copy(data.links[+c.dataset.copy].url);
      const m = e.target.closest("[data-m]");
      if (m) menu(m, data.links[+m.dataset.m]);
    });

    function menu(anchor, l) {
      popMenu(anchor, [
        { head: l.target },
        { value: "stats", icon: "bar_chart", label: "Aufrufe ansehen", sub: `${l.uses}× geöffnet` },
        { value: "edit", icon: "edit", label: "Bearbeiten" },
        { value: "urls", icon: "link", label: "Alle Adressen" },
        { value: "toggle", icon: l.enabled ? "pause_circle" : "play_circle", label: l.enabled ? "Deaktivieren" : "Aktivieren" },
        { value: "reset", icon: "restart_alt", label: "Zähler zurücksetzen", sub: "auch das gebundene Gerät" },
        "-",
        { value: "delete", icon: "delete", label: "Löschen", danger: true },
      ], (v) => {
        if (v === "stats") stats(l);
        if (v === "edit") edit(l);
        if (v === "urls") addresses(l, "Adressen");
        if (v === "toggle") toggle(l);
        if (v === "reset") reset(l);
        if (v === "delete") remove(l);
      }, { align: "right" });
    }

    // Alle Adressen eines Links zum Kopieren; nach dem Anlegen dieselbe Ansicht als Ergebnis
    function addresses(l, title) {
      const m = openModal(`${sheetHead(title, "link")}<div class="lk-urls">${l.urls.map((u, i) => `<div class="lk-url ${i ? "" : "main"}">
          <span class="mono ellip">${esc(u.replace("https://", ""))}</span>
          <button type="button" class="icon-btn sm" data-u="${esc(u)}" aria-label="Kopieren"><span class="ms">content_copy</span></button></div>`).join("")}</div>
        <p class="sheet-hint">Das Kürzel funktioniert auf allen Adressen. ${esc(rules(l).join(" · "))}.</p>
        <div class="sheet-actions"><button type="button" class="btn-primary" data-close>Fertig</button></div>`);
      m.el.addEventListener("click", (e) => { const b = e.target.closest("[data-u]"); if (b) copy(b.dataset.u); });
    }

    async function edit(cur) {
      const main = data?.domains?.[0]?.domain || "";
      const time = cur?.valid_from ? "range" : cur?.valid_until ? "until" : cur?.hours ? "hours" : "forever";
      let saved = null;
      const fields = [
        { name: "target", label: "Wohin soll der Link führen?", value: cur?.target || "", placeholder: "https://…" },
        { name: "note", label: "Notiz (optional)", value: cur?.note || "", placeholder: "z. B. Lebenslauf für Firma X" },
        { name: "uses_mode", label: "Wie oft?", type: "seg", value: !cur?.max_uses ? "any" : cur.max_uses === 1 ? "once" : "count",
          options: [{ value: "any", label: "Unbegrenzt" }, { value: "once", label: "Einmal" }, { value: "count", label: "Mehrmals" }] },
        { name: "uses", label: "Wie oft darf er geöffnet werden?", type: "number", min: 1, value: cur?.max_uses > 1 ? cur.max_uses : 5, show: (v) => v.uses_mode === "count" },
        { name: "time_mode", label: "Wie lange?", type: "seg", value: time,
          options: [{ value: "forever", label: "Immer" }, { value: "until", label: "Bis Datum" }, { value: "range", label: "Zeitraum" }, { value: "hours", label: "Stunden" }] },
        { name: "from", label: "Von", type: "datetime", value: toInput(cur?.valid_from), show: (v) => v.time_mode === "range" },
        { name: "until", label: "Bis", type: "datetime", value: toInput(cur?.valid_until), show: (v) => v.time_mode === "range" || v.time_mode === "until" },
        { name: "hours", label: "Wie viele Stunden?", type: "number", min: 1, value: cur?.hours || 24, show: (v) => v.time_mode === "hours" },
        { name: "hours_from", label: "Die Zeit läuft", type: "seg", value: cur?.hours_from || "first", show: (v) => v.time_mode === "hours",
          options: [{ value: "first", label: "ab erstem Öffnen" }, { value: "create", label: "ab Erstellen" }] },
        { name: "device_mode", label: "Welche Geräte?", type: "seg", value: cur?.device_mode || "any",
          options: [{ value: "any", label: "Alle" }, { value: "once", label: "Jedes nur einmal" }, { value: "bind", label: "Nur das erste" }],
          hint: "Das Gerät wird im Browser gemerkt. Ein privates Fenster oder ein anderer Browser zählt als neues Gerät." },
        { type: "head", key: "more", label: "Weitere Einstellungen" },
        { name: "domain", label: "Angezeigte Adresse", type: "select", value: cur?.domain || main, options: (data?.domains || []).map((d) => ({ value: d.domain, label: d.domain })), hint: "Das Kürzel funktioniert trotzdem auf allen." },
        { name: "code", label: "Eigenes Kürzel (optional)", value: cur?.code || "", placeholder: "leer = zufällig", hint: "Zufällige Kürzel sind nicht zu erraten, eigene schon eher." },
        { name: "confirm", label: "Erst eine „Weiter“-Seite zeigen", type: "switch", value: !!cur?.confirm, hint: "Für Einmallinks per Mail: Mail-Scanner öffnen Links vorab und würden ihn sonst verbrauchen." },
        { type: "live", key: "sum", render: (v) => `<span class="ms">info</span><span>${esc(rules(payload(v)).join(" · "))}</span>` },
      ];
      const ok = await formDialog(cur ? `Bearbeiten: ${cur.code}` : "Neuer Link", fields, {
        ok: cur ? "Speichern" : "Link erstellen", icon: "link",
        submit: async (v) => {
          const p = payload(v);
          const bad = problem(p, v);
          if (bad) return bad;
          try { saved = cur ? await api.put(`/links/${cur.id}`, { ...p, enabled: !!cur.enabled }) : await api.post("/links", p); } catch (e) { return e.message; }
          return "";
        },
      });
      if (!ok) return;
      await load();
      if (cur) toast("Gespeichert"); else addresses(saved, "Link erstellt");
    }

    async function toggle(l) {
      if (await attempt(() => api.post(`/links/${l.id}/enabled`, { enabled: !l.enabled }), l.enabled ? "Deaktiviert" : "Aktiviert")) load();
    }

    async function reset(l) {
      if (!(await confirmDialog("Zähler zurücksetzen?", `${l.code} gilt danach wieder wie neu: Zähler, erstes Öffnen und gemerkte Geräte starten von vorn. Die Liste der Aufrufe bleibt.`, { ok: "Zurücksetzen" }))) return;
      if (await attempt(() => api.post(`/links/${l.id}/reset`), "Zurückgesetzt")) load();
    }

    async function remove(l) {
      if (!(await confirmDialog("Link löschen?", `${l.url.replace("https://", "")} funktioniert danach auf keiner Adresse mehr.`, { ok: "Löschen", danger: true }))) return;
      if (await attempt(() => api.del(`/links/${l.id}`), "Gelöscht")) load();
    }

    async function stats(l) {
      const d = await attempt(() => api.get(`/links/${l.id}`));
      if (!d) return;
      const bound = d.device_mode === "bind" ? (d.bound ? `Gebunden an Gerät ${esc(d.bound)}` : "Noch an kein Gerät gebunden") : "";
      openModal(`${sheetHead(d.code, "bar_chart")}
        <div class="lk-stats"><div><b>${d.uses}${d.max_uses ? `/${d.max_uses}` : ""}</b><small>geöffnet</small></div><div><b>${d.devices}</b><small>Geräte</small></div>
          <div><b>${d.expires ? esc(fmtDateTime(d.expires)) : "–"}</b><small>läuft ab</small></div></div>
        ${bound ? `<p class="sheet-hint">${bound}</p>` : ""}
        <div class="lk-visits">${d.visits.length ? d.visits.map(visit).join("") : `<div class="muted pad">Noch keine Aufrufe.</div>`}</div>
        <div class="sheet-actions"><button type="button" class="btn-primary" data-close>Schließen</button></div>`, { wide: true });
    }

    const visit = (v) => `<div class="lk-visit"><span class="ms sm ${v.ok ? "lk-ok" : "lk-no"}">${v.ok ? "check_circle" : "block"}</span>
      <span>${esc(fmtDateTime(v.at))}</span><span class="muted ellip">${esc([v.ok ? "" : REASON[v.reason] || v.reason, system(v.agent), v.country, v.device].filter(Boolean).join(" · "))}</span></div>`;

    async function setup() {
      let tunnel_id = data.tunnels[0]?.id || "";
      if (data.tunnels.length > 1) {
        const v = await formDialog("Domains einrichten", [{ name: "tunnel_id", label: "Über welchen Tunnel?", type: "select", value: tunnel_id, options: data.tunnels.map((t) => ({ value: t.id, label: t.name })) }], { ok: "Einrichten", icon: "bolt" });
        if (!v) return;
        tunnel_id = v.tunnel_id;
      }
      const res = await attempt(() => api.post("/links/setup", { tunnel_id }));
      if (!res) return;
      openModal(`${sheetHead(res.ok ? "Domains eingerichtet" : "Nicht alles hat geklappt", res.ok ? "check_circle" : "error")}${stepsHtml(res.steps)}
        <p class="sheet-hint">DNS und Zertifikate brauchen manchmal ein paar Minuten.</p>
        <div class="sheet-actions"><button type="button" class="btn-primary" data-close>OK</button></div>`);
      load();
    }

    load();
    return () => { alive = false; };
  },
};

function system(agent = "") {
  const hit = [["iPhone", "iPhone"], ["iPad", "iPad"], ["Android", "Android"], ["Windows", "Windows"], ["Macintosh", "Mac"], ["Linux", "Linux"]].find(([k]) => agent.includes(k));
  return hit ? hit[1] : "";
}
