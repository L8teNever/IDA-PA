// Einstellungen → Gedächtnis (?bereich=<scope>&q=): entries per scope, search, add, edit, tidy up + undo.
import { api } from "../../api.js";
import { setUrl } from "../../router.js";
import { PALETTE, colors } from "../../store.js";
import { $, attempt, closeSheet, esc, fmtDateTime, openSheet, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

const TYPES = { fact: "Fakt", preference: "Vorliebe", rule: "Regel", event: "Ereignis" };

export async function render(el) {
  const params = new URLSearchParams(location.search);
  let scope = params.get("bereich") || "shared";
  let q = params.get("q") || "";
  const [scopes, status] = await Promise.all([api.get("/memory/scopes"), api.get("/memory/status")]);
  if (!scopes.some((s) => s.id === scope)) scope = "shared";

  const draw = async () => {
    setUrl(`/einstellungen/gedaechtnis?bereich=${scope}${q ? "&q=" + encodeURIComponent(q) : ""}`);
    const sc = scopes.find((s) => s.id === scope);
    const items = await api.get(`/memory?scope=${scope}&q=${encodeURIComponent(q)}`);
    el.innerHTML = subHead("Gedächtnis", "/einstellungen") + `
      <p class="sub-intro">Jeder Agent hat sein <b>eigenes</b> Gedächtnis, dazu gibt es ein <b>gemeinsames</b>. „Fest“ steht immer im Prompt, der Rest wird passend zur Aufgabe ausgewählt.
        Suche: ${esc(status.engine)}${status.vectors ? ` + Bedeutung (${esc(status.vectors)}, ${status.embedded}/${status.total})` : " – für Suche nach Bedeutung: <span class=\"mono\">ollama pull nomic-embed-text</span>"}.</p>
      <div class="chipset" id="mScopes">${scopes.map((s) => {
        const c = s.palette ? colors(s) : PALETTE.lila;
        return `<button data-s="${s.id}" class="${s.id === scope ? "on" : ""}" style="${s.id === scope ? `background:${c["--container"]};color:${c["--on-container"]}` : ""}">${esc(s.label)} (${s.count})${s.enabled ? "" : " ⏸"}</button>`;
      }).join("")}</div>
      ${sc.enabled ? "" : `<p class="sub-intro warn">Gedächtnis ist für diesen Agenten ausgeschaltet – Einträge bleiben, er sieht sie aber nicht.</p>`}
      <div class="search" style="margin-top:12px"><span class="ms">search</span><input id="mQ" placeholder="Im Gedächtnis suchen" value="${esc(q)}"></div>
      <div class="btn-row"><button class="add-btn" id="mAdd"><span class="ms">add</span>Eintrag</button>
        <button class="es-btn" id="mTidy"><span class="ms">cleaning_services</span>Aufräumen (KI)</button>
        ${sc.backup ? `<button class="es-btn" id="mUndo"><span class="ms">undo</span>Rückgängig</button>` : ""}</div>
      <div class="slist" id="mList">${items.map((e) => `<div class="srow col" data-id="${e.id}">
        <span class="smain"><b class="wrap">${e.pinned ? "📌 " : ""}${esc(e.text)}</b>
        <small>${TYPES[e.type] || "Fakt"} · Wichtigkeit ${e.importance}${e.uses ? ` · ${e.uses}× genutzt` : ""}${e.expires ? " · gültig bis " + new Date(e.expires).toLocaleDateString("de-DE") : ""} · von ${esc(e.by_label)} · ${fmtDateTime(e.updated_at)}</small></span></div>`).join("")
        || `<div class="empty">${q ? "Nichts gefunden." : "Noch keine Einträge."}</div>`}</div>`;
    $("mScopes").onclick = (e) => { const b = e.target.closest("[data-s]"); if (b) { scope = b.dataset.s; draw(); } };
    let t;
    $("mQ").oninput = () => { clearTimeout(t); t = setTimeout(() => { q = $("mQ").value.trim(); draw().then(() => { $("mQ").focus(); $("mQ").setSelectionRange(q.length, q.length); }); }, 300); };
    $("mAdd").onclick = () => entrySheet(scope, null, draw);
    $("mList").onclick = (e) => { const r = e.target.closest("[data-id]"); if (r) entrySheet(scope, items.find((x) => x.id === r.dataset.id), draw); };
    $("mTidy").onclick = async () => {
      if (!confirm("Ein günstiges Modell fasst Doppeltes zusammen und löscht Veraltetes. Das zählt auf dein Limit und lässt sich rückgängig machen. Fortfahren?")) return;
      $("mTidy").disabled = true; $("mTidy").textContent = "räumt auf …";
      const j = await attempt(() => api.post("/memory/consolidate", { scope }));
      if (j) toast(`${j.vorher} → ${j.nachher} Einträge (${j.geloescht} gelöscht, ${j.zusammengefuehrt} zusammengeführt)`);
      sc.backup = true; draw();
    };
    if ($("mUndo")) $("mUndo").onclick = async () => { if (await attempt(() => api.post("/memory/undo", { scope }), "Wiederhergestellt")) { sc.backup = false; draw(); } };
  };
  await draw();
}

function entrySheet(scope, e, done) {
  const isNew = !e;
  const v = e ? { ...e } : { text: "", type: "fact", pinned: false, importance: 3, expires: null };
  openSheet(`<div class="sub-head" style="padding-top:0"><h1 style="font-size:22px">${isNew ? "Neuer Eintrag" : "Eintrag"}</h1><button class="icon-btn es-x" id="eClose"><span class="ms">close</span></button></div>
    <label class="fld"><span>Ein Fakt, kurz</span><textarea id="eText" rows="3" maxlength="600">${esc(v.text)}</textarea></label>
    <div class="fld-row">
      <label class="fld"><span>Art</span><select id="eType">${Object.entries(TYPES).map(([k, t]) => `<option value="${k}" ${v.type === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <label class="fld"><span>Wichtigkeit</span><select id="eImp">${[1, 2, 3, 4, 5].map((n) => `<option ${v.importance === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <label class="fld"><span>gültig (Tage, leer = immer)</span><input id="eDays" type="number" min="0" max="3650" value=""></label>
    </div>
    <label class="tool-item"><input type="checkbox" id="ePin" ${v.pinned ? "checked" : ""}><span>Fest gemerkt (immer im Prompt)</span></label>
    <div id="eErr" class="ferr"></div>
    <div class="wiz-nav">${isNew ? `<button class="es-btn" id="eCancel">Abbrechen</button>` : `<button class="es-btn danger" id="eDel"><span class="ms">delete</span>Löschen</button>`}
      <button class="es-btn primary" id="eSave"><span class="ms">check</span>Speichern</button></div>`);
  $("eClose").onclick = closeSheet;
  if ($("eCancel")) $("eCancel").onclick = closeSheet;
  if ($("eDel")) $("eDel").onclick = async () => { if (await attempt(() => api.del(`/memory/${e.id}?scope=${scope}`), "Gelöscht")) { closeSheet(); done(); } };
  $("eSave").onclick = async () => {
    const body = { scope, text: $("eText").value, type: $("eType").value, importance: +$("eImp").value, pinned: $("ePin").checked };
    if ($("eDays").value !== "") body.expires_in_days = +$("eDays").value;
    try {
      const r = isNew ? await api.post("/memory", body) : await api.patch(`/memory/${e.id}`, body);
      if (r.hinweis) toast(r.hinweis);
      closeSheet(); done();
    } catch (err) { $("eErr").textContent = err.message; }
  };
}

