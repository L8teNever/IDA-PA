// Einstellungen → Startseite: which calendar tool fills the day plan (called by the server itself, no AI).
import { api } from "../../api.js";
import { $, attempt, esc, fmtTime, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

export async function render(el) {
  const [s, connectors] = await Promise.all([api.get("/home-calendar"), api.get("/connectors")]);
  const saved = s.saved || {};
  let mode = saved.off ? "off" : saved.source === "kalender" ? "ida" : saved.connector ? "fixed" : s.local ? "ida" : "auto";
  const usable = connectors.filter((c) => c.enabled && c.tools.length);
  const f = { connector: saved.connector || s.candidates[0]?.connector || usable[0]?.id || "", tool: saved.tool || s.candidates[0]?.tool || "",
    args: saved.args ? JSON.stringify(saved.args, null, 1) : "" };

  const draw = () => {
    const conn = connectors.find((c) => c.id === f.connector);
    const calTools = new Set(s.candidates.filter((x) => x.connector === f.connector).map((x) => x.tool));
    const tools = [...(conn?.tools || [])].sort((a, b) => calTools.has(b) - calTools.has(a));
    const auto = s.candidates[0];
    el.innerHTML = subHead("Startseite", "/einstellungen") + `
      <p class="sub-intro">Im <b>Tagesplan</b> auf der Startseite stehen deine Termine von heute – <b>ohne KI, kostet kein Limit</b>.${s.local ? " Am besten aus deinem eigenen <a href=\"/kalender\">IDA Kalender</a> (lokal, immer aktuell); alternativ holt IDA sie über einen MCP-Server, höchstens alle 5 Minuten neu." : " IDA holt sie direkt über einen MCP-Server, höchstens alle 5 Minuten neu."}</p>
      <div class="seg" id="hMode">${s.local ? `<button type="button" data-m="ida" class="${mode === "ida" ? "on" : ""}">IDA Kalender</button>` : ""}<button type="button" data-m="auto" class="${mode === "auto" ? "on" : ""}">Automatisch</button><button type="button" data-m="fixed" class="${mode === "fixed" ? "on" : ""}">Selbst wählen</button><button type="button" data-m="off" class="${mode === "off" ? "on" : ""}">Aus</button></div>
      ${mode === "ida" ? `<div class="rf-sum"><span class="ms">calendar_month</span><span>Die Termine kommen direkt aus deinem lokalen <a href="/kalender">IDA Kalender</a> – sofort aktuell, nichts geht nach außen. Ein Tipp auf einen Termin öffnet ihn dort.</span></div>`
      : mode === "auto" ? `<div class="rf-sum"><span class="ms">info</span><span>${auto
        ? `Nimmt <b class="mono">${esc(auto.tool)}</b> von <b>${esc(auto.connector_name)}</b> und fragt nur den heutigen Tag ab.`
        : "Kein Kalender-Tool gefunden. Verbinde unter <a href=\"/einstellungen/mcp\">MCP-Server</a> einen Server mit einem Tool wie „termine_liste“ oder „list_events“."}</span></div>`
      : mode === "off" ? `<div class="rf-sum"><span class="ms">info</span><span>Kein Kalender im Tagesplan – dort stehen nur die markierten Routinen und Erinnerungen.</span></div>`
      : usable.length ? `
        <div class="fld-row"><label class="fld"><span>MCP-Server</span><select id="hConn">${usable.map((c) => `<option value="${c.id}" ${c.id === f.connector ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label>
        <label class="fld"><span>Tool</span><select id="hTool">${tools.map((t) => `<option ${t === f.tool ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label></div>
        <label class="fld"><span>Argumente als JSON – leer = Datum automatisch aus dem Tool ableiten</span><textarea id="hArgs" rows="3" class="mono" placeholder='{"von": "{heute_start}", "bis": "{heute_ende}"}'>${esc(f.args)}</textarea></label>
        <p class="rf-note">Platzhalter: <code>{heute_start}</code> <code>{heute_ende}</code> (Uhrzeit mit Zeitzone), <code>{heute}</code> (JJJJ-MM-TT), <code>{monat}</code> (JJJJ-MM).</p>
        <button type="button" class="es-btn" id="hSuggest"><span class="ms">auto_fix_high</span>Argumente vorschlagen</button>`
      : `<div class="soon-box"><span class="ms">extension</span>Erst einen MCP-Server mit Tools verbinden (Einstellungen → MCP-Server).</div>`}
      ${mode !== "off" ? `<button type="button" class="es-btn" id="hTest" style="margin-top:10px"><span class="ms">science</span>Heute abrufen</button><pre class="test-out" id="hOut" hidden></pre>` : ""}
      <div class="rf-sum" style="margin-top:14px"><span class="ms">visibility</span><span>Welche <a href="/routinen">Routinen</a> und <a href="/erinnerungen">Erinnerungen</a> dort auch stehen, stellst du bei jeder einzeln ein („Im Tagesplan“). Ida kann das beim Anlegen selbst setzen.</span></div>
      <div id="hErr" class="ferr"></div>
      <div class="wiz-nav"><button class="es-btn primary" id="hSave"><span class="ms">check</span>Speichern</button></div>`;
    bind();
  };

  const keep = () => {
    if ($("hConn")) { f.connector = $("hConn").value; f.tool = $("hTool").value; f.args = $("hArgs").value; }
  };
  const body = () => {
    if (mode === "off") return { off: true };
    if (mode === "ida") return { source: "kalender" };
    if (mode === "auto") return {};
    let args = null;
    if (f.args.trim()) {
      try { args = JSON.parse(f.args); } catch { throw new Error("Die Argumente sind kein gültiges JSON"); }
    }
    return { connector: f.connector, tool: f.tool, args };
  };

  const bind = () => {
    $("hMode").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; keep(); mode = b.dataset.m; draw(); };
    if ($("hConn")) {
      $("hConn").onchange = () => { keep(); f.tool = ""; f.args = ""; draw(); };
      $("hTool").onchange = () => { keep(); f.args = ""; draw(); };
      $("hSuggest").onclick = async () => {
        keep();
        const j = await attempt(() => api.get(`/home-calendar/suggest?connector=${encodeURIComponent(f.connector)}&tool=${encodeURIComponent(f.tool)}`));
        if (j) { f.args = Object.keys(j.args).length ? JSON.stringify(j.args, null, 1) : "{}"; draw(); }
      };
    }
    if ($("hTest")) $("hTest").onclick = async () => {
      keep();
      $("hOut").hidden = false; $("hOut").textContent = "…";
      try {
        const j = await api.post("/home-calendar/test", body());
        $("hOut").textContent = j.error ? `⚠ ${j.error}` : j.events.length
          ? `✓ ${j.events.length} Termin(e) heute (${j.tool}):\n` + j.events.map((e) => `${e.all_day ? "ganztägig" : fmtTime(e.at) + (e.end ? "–" + fmtTime(e.end) : "")}  ${e.title}`).join("\n")
          : `✓ Abruf geklappt (${j.tool}), heute keine Termine.`;
      } catch (err) { $("hOut").textContent = "⚠ " + err.message; }
    };
    $("hSave").onclick = async () => {
      keep();
      $("hErr").textContent = "";
      try {
        await api.put("/home-calendar", body());
        toast("Gespeichert");
        render(el);
      } catch (err) { $("hErr").textContent = err.message; }
    };
  };
  draw();
}
