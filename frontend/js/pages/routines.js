// "/routinen" list, "/routinen/neu" + "/routinen/:id/bearbeiten" form, "/routinen/:id" details.
import { api } from "../api.js";
import { navigate } from "../router.js";
import { agentOf, agents, colors, mainAgent, modelInfo, state } from "../store.js";
import { $, attempt, autogrow, esc, every, everySec, fmtDateTime, md, sw, toast } from "../ui/dom.js";
import { openTimePicker } from "../ui/timepicker.js";
import { setAgent, setView } from "../ui/shell.js";

const ICONS = ["schedule", "inbox", "mail", "event", "alarm", "notifications", "savings", "menu_book", "school", "home_iot_device", "cloud", "search"];
const MODES = { new: "bei neuen Einträgen", change: "bei jeder Änderung", contains: "wenn Text vorkommt" };

const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
function daysText(days) {
  const d = [...(days?.length ? days : [0, 1, 2, 3, 4, 5, 6])].sort();
  if (d.length === 7) return "täglich";
  if (d.join() === "0,1,2,3,4") return "Mo–Fr";
  if (d.join() === "5,6") return "Sa+So";
  return d.map((i) => DAYS[i]).join(", ");
}
const timesText = (r) => `🕐 ${daysText(r.days)} ${r.times.join(", ")}`;
function whenText(r) {
  if (r.on_demand) return "🔗 nur auf Abruf";
  return r.trigger ? (r.trigger.type === "script" ? "🔔 eigener Code" : `🔔 ${MODES[r.trigger.mode]}`) : r.times?.length ? timesText(r) : every(r.interval_min);
}

export async function routinesPage() {
  setAgent(mainAgent());
  setView("page");
  const list = await api.get("/routines");
  $("page").innerHTML = `
    <div class="page-head"><h1>Routinen</h1><span class="tag">${list.filter((r) => r.enabled).length} aktiv</span></div>
    <a class="add-btn" href="/routinen/neu"><span class="ms">add</span>Neue Routine</a>
    <div id="routinePageList">${list.length ? list.map((r) => {
      const a = agentOf(r.agent_id), c = colors(a);
      const next = r.running ? "läuft gerade" : !r.enabled ? "Pausiert" : r.trigger ? `prüft ${everySec(r.trigger.every_sec)}` : r.next_run ? "Nächste: " + fmtDateTime(r.next_run) : "";
      return `<div class="rcard ${r.enabled ? "" : "off"}" data-href="/routinen/${r.id}">
        <span class="ric" style="background:${c["--container"]};color:${c["--accent"]}"><span class="ms">${esc(r.icon)}</span></span>
        <div class="rmain"><b>${esc(r.name)}</b><div class="rdesc">${esc(r.prompt.slice(0, 90))}</div>
          <div class="rmeta"><span>${esc(whenText(r))}</span><span>${esc(next)}</span><span style="color:${c["--accent"]}">${esc(a.name)}</span>
          ${r.last ? `<span>${r.last.ok ? "✓" : "✗"} ${fmtDateTime(r.last.at)}</span>` : ""}</div></div>
        ${sw(r.enabled, `data-toggle="${r.id}" style="--sw:${c["--accent"]}" aria-label="${esc(r.name)} an/aus"`)}
      </div>`;
    }).join("") : `<div class="empty">Noch keine Routinen. Du kannst auch einfach Ida schreiben: „Prüf alle 30 Minuten meine Mails“.</div>`}</div>`;
  $("routinePageList").onclick = async (e) => {
    const t = e.target.closest("[data-toggle]");
    if (!t) return;
    e.stopPropagation();
    const r = list.find((x) => x.id === t.dataset.toggle);
    await attempt(() => api.patch(`/routines/${r.id}`, { enabled: !r.enabled }));
    routinesPage();
  };
}

export async function routineDetail({ id }) {
  setView("page");
  const r = await api.get(`/routines/${id}`);
  const a = agentOf(r.agent_id), c = colors(a);
  setAgent(mainAgent());
  const t = r.trigger;
  $("page").innerHTML = `
    <div class="sub-head"><a class="icon-btn back" href="/routinen" aria-label="Zurück"><span class="ms">arrow_back</span></a><h1>${esc(r.name)}</h1>
      <button type="button" id="rEnabled" class="state-toggle ${r.enabled ? "on" : ""}" style="--sw:${c["--accent"]}" aria-pressed="${r.enabled}"
        title="${r.enabled ? "Routine pausieren" : "Routine aktivieren"}"><span class="state-text">${r.enabled ? "Aktiv" : "Pausiert"}</span><span class="sw ${r.enabled ? "on" : ""}" aria-hidden="true"></span></button></div>
    ${r.enabled ? "" : `<div class="state-note"><span class="ms">pause_circle</span><span>Diese Routine ist pausiert und startet nicht von selbst. „Jetzt ausführen“ geht trotzdem.</span></div>`}
    <div class="pcard">
      <div class="kv"><span>Agent</span><b style="color:${c["--accent"]}">${esc(a.name)}</b></div>
      <div class="kv"><span>Start</span><b>${r.on_demand ? "nur auf Abruf – startet nur, wenn ein Agent sie per MCP aufruft (routine_run_now) oder du „Jetzt ausführen“ drückst" : t?.type === "script" ? `eigener Code · prüft ${everySec(t.every_sec)}` : t ? `${esc(t.connector)} › ${esc(t.tool)} · ${MODES[t.mode]}${t.contains ? ` „${esc(t.contains)}“` : ""} · ${everySec(t.every_sec)}` : r.times?.length ? esc(timesText(r)) : every(r.interval_min)}</b></div>
      <div class="kv"><span>Modell</span><b>${esc(r.model ? modelInfo(r.model).name : "wie der Agent")}</b></div>
      <div class="kv"><span>Account</span><b>${esc(r.account_id ? (state.boot.accounts.find((x) => x.id === r.account_id)?.name || r.account_id) : "wie der Agent")}</b></div>
      <div class="kv"><span>Limit-Fallback</span><b>${r.fallback === null ? "wie der Agent" : r.fallback ? "an" : "aus"}</b></div>
      <div class="kv"><span>Gedächtnis</span><b>${r.memory_enabled === null ? "wie der Agent" : r.memory_enabled ? "an" : "aus"}</b></div>
      ${r.trackers?.length ? `<div class="kv"><span>Tracker</span><b>startet, wenn ${r.trackers.map((x) => `<a href="/tracker">„${esc(x.name)}“</a>`).join(", ")} zur Uhrzeit nicht abgehakt ist</b></div>` : ""}
      <div class="kv"><span>Startseite</span><b>${r.show_on_home ? "im Tagesplan" : "nicht im Tagesplan"}</b></div>
      ${t ? `<div class="kv"><span>Letzte Prüfung</span><b>${t.last_check ? fmtDateTime(t.last_check) : "noch nicht"}${t.last_error ? ` · ⚠ ${esc(t.last_error)}` : t.last_note ? ` · ${esc(t.last_note)}` : ""}</b></div>
             ${t.last_fired ? `<div class="kv"><span>Zuletzt ausgelöst</span><b>${fmtDateTime(t.last_fired)}</b></div>` : ""}`
           : `<div class="kv"><span>Nächster Lauf</span><b>${!r.enabled ? "pausiert" : r.on_demand ? "wenn aufgerufen" : r.next_run ? fmtDateTime(r.next_run) : "–"}</b></div>`}
    </div>
    <div class="pcard"><div class="pc-head"><b>Aufgabe</b></div><div class="md">${md(r.prompt)}</div></div>
    <div class="btn-row">
      <button class="es-btn primary" id="rRun"><span class="ms">play_arrow</span>Jetzt ausführen</button>
      ${t ? `<button class="es-btn" id="rCheck"><span class="ms">notifications_active</span>Jetzt prüfen</button>` : ""}
      <a class="es-btn" href="/routinen/${r.id}/bearbeiten"><span class="ms">edit</span>Bearbeiten</a>
      <button class="es-btn danger" id="rDel"><span class="ms">delete</span>Löschen</button>
    </div>
    <div class="grp">Verlauf</div>
    <div class="slist">${r.runs.length ? r.runs.map((x) => `<details class="srow run">
      <summary><span class="sic" style="color:${x.ok ? "#7ee2b8" : "#ff8f8f"}"><span class="ms">${x.via === "trigger" ? "notifications_active" : x.via === "tracker" ? "checklist" : x.ok ? "check_circle" : "error"}</span></span>
      <span class="smain"><b>${fmtDateTime(x.at)}${x.via === "tracker" ? " · vom Tracker" : ""}</b><small>${Math.round(x.ms / 1000)} s${x.account ? " · " + esc(x.account) : ""}${x.tools.length ? " · 🔧 " + esc(x.tools.join(", ")) : ""}</small></span></summary>
      <div class="md run-text">${md(x.text)}</div></details>`).join("") : `<div class="empty">Noch nicht gelaufen.</div>`}</div>`;
  $("rEnabled").onclick = async () => { await attempt(() => api.patch(`/routines/${id}`, { enabled: !r.enabled })); routineDetail({ id }); };
  $("rRun").onclick = async () => { await attempt(() => api.post(`/routines/${id}/run`), "In die Warteschlange gestellt"); };
  if ($("rCheck")) $("rCheck").onclick = async () => {
    const j = await attempt(() => api.post(`/routines/${id}/check`));
    if (j) toast(j.error ? "Fehler: " + j.error : j.note + (j.started ? " → gestartet" : ""));
    routineDetail({ id });
  };
  $("rDel").onclick = async () => {
    if (!confirm(`Routine „${r.name}“ löschen?`)) return;
    if (await attempt(() => api.del(`/routines/${id}`), "Gelöscht")) navigate("/routinen");
  };
}

export async function routineForm({ id } = {}) {
  setAgent(mainAgent());
  setView("page");
  const [r, connectors] = await Promise.all([id ? api.get(`/routines/${id}`) : null, api.get("/connectors")]);
  const f = r ? { ...r } : { name: "", icon: "schedule", prompt: "", agent_id: mainAgent().id, model: null, account_id: null, interval_min: 60, trigger: null, fallback: null, memory_enabled: null, times: null, days: null, show_on_home: false };
  let mode = f.on_demand ? "demand" : f.trigger ? "mcp" : f.times?.length ? "times" : "interval";
  let times = f.times?.length ? [...f.times] : ["07:00"];
  let days = f.days?.length ? [...f.days] : [0, 1, 2, 3, 4, 5, 6];
  const t = f.trigger ? { ...f.trigger } : { connector: connectors.find((c) => c.tools.length)?.id || "", tool: "", args: {}, every_sec: 60, mode: "new", contains: "" };
  let kind = t.type === "script" ? "script" : "mcp";
  if (!t.code) t.code = exampleScript(connectors);
  const unit = f.interval_min % 1440 === 0 ? 1440 : f.interval_min % 60 === 0 ? 60 : 1;
  const tri = (v) => `<option value="" ${v === null ? "selected" : ""}>wie der Agent</option><option value="1" ${v === true ? "selected" : ""}>an</option><option value="0" ${v === false ? "selected" : ""}>aus</option>`;

  const draw = () => {
    const conn = connectors.find((c) => c.id === t.connector);
    $("page").innerHTML = `
      <div class="sub-head"><a class="icon-btn back" href="${id ? "/routinen/" + id : "/routinen"}" aria-label="Zurück"><span class="ms">arrow_back</span></a><h1>${id ? "Routine bearbeiten" : "Neue Routine"}</h1></div>
      <section class="rf-sec">
        <h2 class="rf-h"><span class="ms">badge</span>Allgemein</h2>
        <label class="fld"><span>Name</span><input id="fName" maxlength="60" value="${esc(f.name)}" placeholder="z. B. Mail-Check"></label>
        <div class="flabel">Icon</div>
        <div class="chipset icons" id="fIcons">${ICONS.map((ic) => `<button type="button" data-ic="${ic}" class="${f.icon === ic ? "on" : ""}"><span class="ms">${ic}</span></button>`).join("")}</div>
        <div class="flabel">Welcher Agent führt sie aus?</div>
        <div class="chipset" id="fAgents">${agents().map((a) => `<button type="button" data-a="${a.id}" class="${f.agent_id === a.id ? "on" : ""}">${esc(a.name)}</button>`).join("")}</div>
        <div class="slist toggles rf-toggle"><div class="srow"><span class="smain"><b>Im Tagesplan auf der Startseite</b><small>Zeigt die heutigen Läufe dieser Routine im Tagesplan</small></span>${sw(f.show_on_home, 'id="fHome" aria-label="Im Tagesplan zeigen"')}</div></div>
      </section>
      <section class="rf-sec">
        <h2 class="rf-h"><span class="ms">task_alt</span>Aufgabe</h2>
        <p class="rf-note">Wird bei jedem Lauf so ausgeführt – muss ohne Chat verständlich sein.</p>
        <label class="fld"><textarea id="fPrompt" class="autogrow" rows="3" placeholder="Was soll der Agent jedes Mal tun?">${esc(f.prompt)}</textarea></label>
      </section>
      <section class="rf-sec">
        <h2 class="rf-h"><span class="ms">event_repeat</span>Wann starten?</h2>
      <div class="seg" id="fMode"><button type="button" data-m="interval" class="${mode === "interval" ? "on" : ""}">⏱ Intervall</button><button type="button" data-m="times" class="${mode === "times" ? "on" : ""}">🕐 Uhrzeiten</button><button type="button" data-m="mcp" class="${mode === "mcp" ? "on" : ""}">🔔 Bei Neuem</button><button type="button" data-m="demand" class="${mode === "demand" ? "on" : ""}">🔗 Auf Abruf</button></div>
      <p class="sub-intro rf-once-hint"><span class="ms">alarm</span>Routinen wiederholen sich. Soll etwas nur <b>einmal</b> passieren, z. B. „morgen um 9“, nimm eine <a href="/erinnerungen">Erinnerung</a>.</p>
      ${mode === "times" ? `
        <div class="flabel" style="margin-top:4px">Uhrzeiten</div>
        <div class="chipset time-chips" id="fTimes">${times.map((x) => `<span class="time-chip"><span class="ms">schedule</span>${esc(x)}<button type="button" data-rm="${esc(x)}" aria-label="${esc(x)} entfernen"><span class="ms">close</span></button></span>`).join("")}
          <button type="button" class="time-add-btn" id="fTimeAdd"><span class="ms">add</span>Uhrzeit</button></div>
        <div class="flabel">An welchen Tagen?</div>
        <div class="chipset" id="fDays">${DAYS.map((d, i) => `<button type="button" data-d="${i}" class="${days.includes(i) ? "on" : ""}">${d}</button>`).join("")}
          <button type="button" class="day-preset" data-preset="all">täglich</button><button type="button" class="day-preset" data-preset="week">Mo–Fr</button><button type="button" class="day-preset" data-preset="weekend">Wochenende</button></div>
        <div class="rf-sum"><span class="ms">info</span><span>Läuft <b>${esc(daysText(days))}</b> um <b>${esc(times.join(", ") || "–")} Uhr</b> · deutsche Zeit, Sommer-/Winterzeit wird beachtet</span></div>`
      : mode === "demand" ? `
        <div class="rf-sum"><span class="ms">info</span><span><b>Kein Zeitplan.</b> Die Routine startet nur, wenn ein Agent sie per MCP aufruft (<code>routine_run_now</code>, gern mit einem Hinweis für den Lauf) oder du auf „Jetzt ausführen“ drückst. So kann z. B. Ida einen festen Ablauf anstoßen, wenn sie ihn braucht.</span></div>`
      : mode === "interval" ? `
        <div class="fld-row"><label class="fld"><span>alle</span><input id="fEvery" type="number" min="1" value="${f.interval_min / unit}"></label>
        <label class="fld"><span>Einheit</span><select id="fUnit"><option value="1" ${unit === 1 ? "selected" : ""}>Minuten</option><option value="60" ${unit === 60 ? "selected" : ""}>Stunden</option><option value="1440" ${unit === 1440 ? "selected" : ""}>Tage</option></select></label></div>`
      : `
        <div class="seg seg-sub" id="tKind"><button type="button" data-k="mcp" class="${kind === "mcp" ? "on" : ""}">Einfach</button><button type="button" data-k="script" class="${kind === "script" ? "on" : ""}"><span class="ms">code</span>Eigener Code</button></div>
        ${kind === "script" ? scriptEditor(t, connectors) : `
        <p class="sub-intro" style="margin:0 4px 10px">Der Server ruft das Tool selbst in festen Abständen auf – <b>ohne KI, kostet kein Limit</b> – und startet die Routine nur, wenn die Bedingung erfüllt ist. Nimm ein <b>lesendes</b> Tool (z. B. Mails suchen).</p>
        ${connectors.some((c) => c.tools.length) ? `
        <div class="fld-row"><label class="fld"><span>MCP-Server</span><select id="tConn">${connectors.filter((c) => c.tools.length).map((c) => `<option value="${c.id}" ${c.id === t.connector ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label>
        <label class="fld"><span>Tool</span><select id="tTool">${(conn?.tools || []).map((x) => `<option ${x === t.tool ? "selected" : ""}>${esc(x)}</option>`).join("")}</select></label></div>
        <label class="fld"><span>Argumente als JSON (optional), z. B. {"query": "is:unread"}</span><textarea id="tArgs" rows="2">${esc(typeof t.args === "string" ? t.args : Object.keys(t.args || {}).length ? JSON.stringify(t.args) : "")}</textarea></label>
        <div class="fld-row"><label class="fld"><span>prüfen alle … Sekunden (ab 10)</span><input id="tEvery" type="number" min="10" value="${t.every_sec}"></label>
        <label class="fld"><span>Bedingung</span><select id="tMode">${Object.entries(MODES).map(([k, v]) => `<option value="${k}" ${t.mode === k ? "selected" : ""}>${v}</option>`).join("")}</select></label></div>
        ${t.mode === "contains" ? `<label class="fld"><span>Text</span><input id="tContains" maxlength="200" value="${esc(t.contains)}"></label>` : ""}
        <button type="button" class="es-btn" id="tTest"><span class="ms">science</span>Tool testen</button>
        <pre class="test-out" id="tOut" hidden></pre>`
        : `<div class="soon-box"><span class="ms">extension</span>Erst einen MCP-Server mit Tools verbinden (Einstellungen → MCP-Server).</div>`}`}`}
      </section>
      <details class="rf-sec rf-adv" ${f.model || f.account_id || f.fallback !== null || f.memory_enabled !== null ? "open" : ""}>
        <summary class="rf-h"><span class="ms">tune</span>Ausführung <small>${f.model || f.account_id || f.fallback !== null || f.memory_enabled !== null ? "angepasst" : "alles wie der Agent"}</small><span class="ms rf-chev">expand_more</span></summary>
      <div class="fld-row">
        <label class="fld"><span>Modell</span><select id="fModel"><option value="">wie der Agent</option>${state.boot.models.map((m) => `<option value="${m}" ${f.model === m ? "selected" : ""}>${esc(modelInfo(m).name)}</option>`).join("")}</select></label>
        <label class="fld"><span>Account</span><select id="fAcc"><option value="">wie der Agent</option>${state.boot.accounts.map((a) => `<option value="${a.id}" ${f.account_id === a.id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select></label>
      </div>
      <div class="fld-row">
        <label class="fld"><span>Bei Limit anderen Account nehmen</span><select id="fFallback">${tri(f.fallback)}</select></label>
        <label class="fld"><span>Gedächtnis</span><select id="fMemory">${tri(f.memory_enabled)}</select></label>
      </div>
      </details>
      <div id="fErr" class="ferr"></div>
      <div class="wiz-nav"><a class="es-btn" href="${id ? "/routinen/" + id : "/routinen"}">Abbrechen</a><button class="es-btn primary" id="fSave"><span class="ms">check</span>${id ? "Speichern" : "Anlegen"}</button></div>`;
    bind();
  };

  const keep = () => {
    f.name = $("fName").value; f.prompt = $("fPrompt").value;
    f.model = $("fModel").value || null; f.account_id = $("fAcc").value || null;
    const tri3 = (v) => (v === "" ? null : v === "1");
    f.fallback = tri3($("fFallback").value); f.memory_enabled = tri3($("fMemory").value);
    if ($("fEvery")) f.interval_min = Math.max(1, Math.round(+$("fEvery").value * +$("fUnit").value));
    if ($("tCode")) { t.code = $("tCode").value; t.every_sec = +$("tEverySc").value; }
    if ($("tConn")) {
      t.connector = $("tConn").value; t.tool = $("tTool").value; t.args = $("tArgs").value;
      t.every_sec = +$("tEvery").value; t.mode = $("tMode").value; if ($("tContains")) t.contains = $("tContains").value;
    }
  };

  const bind = () => {
    autogrow($("fPrompt"));
    if ($("fTimes")) {
      $("fTimes").onclick = (e) => {
        const rm = e.target.closest("[data-rm]");
        if (rm) { keep(); times = times.filter((x) => x !== rm.dataset.rm); draw(); }
      };
      $("fTimeAdd").onclick = () => openTimePicker($("fTimeAdd"), {
        value: times.length ? times[times.length - 1] : "07:00",
        taken: times,
        onPick: (v) => { keep(); if (!times.includes(v)) times = [...times, v].sort(); draw(); },
      });
      $("fDays").onclick = (e) => {
        const b = e.target.closest("[data-d],[data-preset]");
        if (!b) return;
        keep();
        if (b.dataset.preset) days = { all: [0, 1, 2, 3, 4, 5, 6], week: [0, 1, 2, 3, 4], weekend: [5, 6] }[b.dataset.preset];
        else {
          const d = +b.dataset.d;
          days = days.includes(d) ? days.filter((x) => x !== d) : [...days, d].sort();
        }
        draw();
      };
    }
    $("fIcons").onclick = (e) => { const b = e.target.closest("[data-ic]"); if (!b) return; keep(); f.icon = b.dataset.ic; draw(); };
    $("fAgents").onclick = (e) => { const b = e.target.closest("[data-a]"); if (!b) return; keep(); f.agent_id = b.dataset.a; draw(); };
    $("fHome").onclick = () => { f.show_on_home = !f.show_on_home; $("fHome").classList.toggle("on", f.show_on_home); };
    $("fMode").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; keep(); mode = b.dataset.m; draw(); };
    if ($("tKind")) $("tKind").onclick = (e) => { const b = e.target.closest("[data-k]"); if (!b) return; keep(); kind = b.dataset.k; draw(); };
    if ($("tCode")) bindScriptEditor(connectors, keep, t);
    if ($("tConn")) {
      $("tConn").onchange = () => { keep(); t.tool = ""; draw(); };
      $("tMode").onchange = () => { keep(); draw(); };
      $("tTest").onclick = async () => {
        keep();
        $("tOut").hidden = false; $("tOut").textContent = "…";
        try {
          const j = await api.post("/routines/test-trigger", { trigger: { ...t, type: "mcp" } });
          $("tOut").textContent = `✓ ${j.items} Einträge erkannt. Ergebnis (Anfang):\n${j.preview || "(leer)"}`;
        } catch (err) { $("tOut").textContent = "⚠ " + err.message; }
      };
    }
    $("fSave").onclick = async () => {
      keep();
      const body = { name: f.name, icon: f.icon, prompt: f.prompt, agent_id: f.agent_id, model: f.model, account_id: f.account_id,
        fallback: f.fallback, memory_enabled: f.memory_enabled, show_on_home: f.show_on_home };
      if (mode === "demand") { body.on_demand = true; if (id) { body.trigger = null; body.times = null; } }
      else if (id) body.on_demand = false;
      if (mode === "demand") { /* nothing more */ }
      else if (mode === "interval") { body.interval_min = f.interval_min; if (id) { body.trigger = null; body.times = null; } }
      else if (mode === "times") {
        if (!times.length) { $("fErr").textContent = "Mindestens eine Uhrzeit hinzufügen."; return; }
        if (!days.length) { $("fErr").textContent = "Mindestens einen Tag auswählen."; return; }
        body.times = times; body.days = days;
        if (id) { body.trigger = null; }
      } else body.trigger = kind === "script" ? { type: "script", code: t.code, every_sec: t.every_sec } : { ...t, type: "mcp", code: "" };
      try {
        const out = id ? await api.patch(`/routines/${id}`, body) : await api.post("/routines", body);
        toast(id ? "Gespeichert" : "Routine angelegt");
        navigate(`/routinen/${out.id}`);
      } catch (err) { $("fErr").textContent = err.message; }
    };
  };
  draw();
}


// ---------- trigger with own code (runs on the server without AI, see backend services/trigger_script.py) ----------
function exampleScript(connectors) {
  const c = connectors.find((x) => x.tools.some((n) => /mail/i.test(n))) || connectors.find((x) => x.tools.length);
  const tool = c?.tools.find((n) => /mail.*(such|search|list)|(such|search|list).*mail/i.test(n)) || c?.tools[0] || "tool_name";
  return `# Läuft auf dem Server OHNE KI – kostet kein Limit.
# Die Routine startet nur, wenn fire("…") aufgerufen wird; der Text geht an den Agenten.
ergebnis = tool_json("${c?.id || "server"}", "${tool}", query="is:unread newer_than:2d")
liste = ergebnis if isinstance(ergebnis, list) else []
neu = new(liste)  # nur Einträge, die beim letzten Mal noch nicht da waren
if neu:
    titel = [str(m.get("betreff") or m.get("subject") or m) if isinstance(m, dict) else str(m) for m in neu[:5]]
    fire(f"{len(neu)} neue Mail(s): " + " | ".join(titel))
print("geprüft:", len(liste), "Einträge")
`;
}

function scriptEditor(t, connectors) {
  const usable = connectors.filter((c) => c.tools.length);
  return `
    <p class="sub-intro" style="margin:0 4px 10px">Ein kleines <b>Python-Skript</b>, das der Server in festen Abständen selbst ausführt – <b>ohne KI, kostet kein Limit</b>. Es fragt MCP-Tools ab und entscheidet selbst, ob die Routine startet.</p>
    <div class="code-wrap"><textarea id="tCode" class="code-ed" spellcheck="false" autocapitalize="off" autocomplete="off" rows="12">${esc(t.code)}</textarea></div>
    <details class="code-help"><summary><span class="ms">help</span>Was kann ich im Skript benutzen?</summary>
      <ul>
        <li><code>tool("server", "tool", arg=wert)</code> – ruft ein MCP-Tool auf, gibt den Text zurück</li>
        <li><code>tool_json(…)</code> – genauso, aber als JSON (Liste/Objekt), wenn möglich</li>
        <li><code>new(liste)</code> – nur Einträge, die bei der letzten Prüfung noch nicht da waren (die erste Prüfung merkt sich nur alles). Optional <code>key=lambda m: m["id"]</code></li>
        <li><code>fire("Info")</code> – startet die Routine, die Info bekommt der Agent mit</li>
        <li><code>state</code> – ein Dict, das zwischen den Prüfungen erhalten bleibt</li>
        <li><code>print(…)</code> – erscheint beim Testen und unter „Letzte Prüfung“</li>
        <li>Normales Python (<code>import re, datetime</code> …), höchstens 60 s pro Lauf</li>
      </ul></details>
    <details class="code-help" ${usable.length ? "" : "open"}><summary><span class="ms">extension</span>Server und Tools (antippen fügt ein)</summary>
      ${usable.length ? usable.map((c) => `<div class="code-srv"><b>${esc(c.name)}</b> <code>${esc(c.id)}</code></div>
        <div class="chipset code-tools">${c.tools.map((n) => `<button type="button" data-ins="${esc(`tool_json("${c.id}", "${n}")`)}">${esc(n)}</button>`).join("")}</div>`).join("")
        : `<p class="sub-intro">Noch kein MCP-Server mit Tools verbunden (Einstellungen → MCP-Server).</p>`}</details>
    <div class="fld-row"><label class="fld"><span>prüfen alle … Sekunden (ab 10)</span><input id="tEverySc" type="number" min="10" value="${t.every_sec}"></label></div>
    <div class="btn-row"><button type="button" class="es-btn" id="tRunCode"><span class="ms">play_arrow</span>Skript testen</button>
      <button type="button" class="es-btn" id="tExample"><span class="ms">restart_alt</span>Beispiel einsetzen</button></div>
    <pre class="test-out" id="tCodeOut" hidden></pre>`;
}

function bindScriptEditor(connectors, keep, t) {
  const ed = $("tCode");
  ed.onkeydown = (e) => { // Tab indents instead of leaving the field
    if (e.key !== "Tab") return;
    e.preventDefault();
    ed.setRangeText("    ", ed.selectionStart, ed.selectionEnd, "end");
  };
  document.querySelectorAll(".code-tools [data-ins]").forEach((b) => (b.onclick = () => {
    ed.focus();
    ed.setRangeText(b.dataset.ins, ed.selectionStart, ed.selectionEnd, "end");
  }));
  $("tExample").onclick = () => {
    if (!ed.value.trim() || confirm("Aktuellen Code durch das Beispiel ersetzen?")) ed.value = exampleScript(connectors);
  };
  $("tRunCode").onclick = async () => {
    keep();
    const out = $("tCodeOut");
    out.hidden = false;
    out.textContent = "läuft …";
    try {
      const j = await api.post("/routines/test-trigger", { trigger: { type: "script", code: t.code, every_sec: t.every_sec } });
      out.textContent = (j.error ? `⚠ Fehler: ${j.error}`
        : j.fire ? `✓ Würde die Routine starten.${j.info ? "\nInfo an den Agenten: " + j.info : ""}`
        : "✓ Läuft – würde die Routine jetzt nicht starten (kein fire).")
        + (j.log ? `\n\nAusgabe:\n${j.log}` : "")
        + "\n\n(Beim Testen zählt alles als neu – im echten Betrieb merkt sich die erste Prüfung nur den Stand.)";
    } catch (err) { out.textContent = "⚠ " + err.message; }
  };
}
