// "/erinnerungen" – things that happen ONCE („erinnere mich morgen um 9 an …“, „mach das am Freitag“).
// Routines repeat; reminders fire once: "remind" only sends a notification (no AI), "task" lets the agent do it.
import { api } from "../api.js";
import { agentOf, agents, colors, mainAgent } from "../store.js";
import { $, attempt, autogrow, esc, fmtDateTime, sw, toast } from "../ui/dom.js";
import { openTimePicker } from "../ui/timepicker.js";
import { setAgent, setView } from "../ui/shell.js";

const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const pad = (n) => String(n).padStart(2, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
function dayLabel(iso) {
  const d = new Date(iso + "T12:00"), t = new Date();
  const diff = Math.round((new Date(isoDay(d) + "T12:00") - new Date(isoDay(t) + "T12:00")) / 86400000);
  const base = `${DAYS[(d.getDay() + 6) % 7]} ${pad(d.getDate())}.${pad(d.getMonth() + 1)}.`;
  return diff === 0 ? `heute (${base})` : diff === 1 ? `morgen (${base})` : diff === 2 ? `übermorgen (${base})` : base;
}
const whenLabel = (iso) => { const d = new Date(iso); return `${dayLabel(isoDay(d))}, ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const STATUS = { pending: "geplant", running: "läuft", done: "erledigt", failed: "fehlgeschlagen" };

export async function remindersPage() {
  setAgent(mainAgent());
  setView("page");
  const list = await api.get("/reminders");
  const open = list.filter((r) => r.status === "pending" || r.status === "running");
  const done = list.filter((r) => !open.includes(r));
  const row = (r) => {
    const a = agentOf(r.agent_id), c = colors(a);
    return `<div class="rcard rem ${open.includes(r) ? "" : "off"}" data-id="${r.id}">
      <span class="ric" style="background:${c["--container"]};color:${c["--accent"]}"><span class="ms">${r.kind === "task" ? "task_alt" : "alarm"}</span></span>
      <div class="rmain"><b>${esc(r.text)}</b>
        <div class="rmeta"><span>${esc(whenLabel(r.due_at))}</span><span>${r.kind === "task" ? `${esc(a.name)} erledigt es` : "nur Erinnerung"}</span>
        ${open.includes(r) ? "" : `<span>${STATUS[r.status] || r.status}</span>`}${r.show_on_home ? "" : `<span>nicht im Tagesplan</span>`}${r.created_by !== "user" ? `<span>von ${esc(agentOf(r.created_by).name)}</span>` : ""}</div>
        ${r.result && r.kind === "task" && !open.includes(r) ? `<div class="rdesc">${esc(r.result.slice(0, 200))}</div>` : ""}</div>
      <div class="rem-act">
        ${open.includes(r) ? `<button class="icon-btn" data-act="edit" aria-label="Bearbeiten"><span class="ms">edit</span></button>
        <button class="icon-btn" data-act="fire" aria-label="Jetzt auslösen" title="Jetzt auslösen"><span class="ms">play_arrow</span></button>` : ""}
        <button class="icon-btn" data-act="del" aria-label="Löschen"><span class="ms">delete</span></button></div></div>`;
  };
  $("page").innerHTML = `
    <div class="page-head"><h1>Erinnerungen</h1><span class="tag">${open.length} offen</span></div>
    <p class="sub-intro">Für alles, was <b>einmal</b> passieren soll – „erinnere mich morgen um 9 an …“ oder „mach das am Freitag“. Was sich wiederholt, sind <a href="/routinen">Routinen</a>. Du kannst Ida auch einfach im Chat sagen: „Erinnere mich morgen um 9 an den Zahnarzt“.</p>
    <button class="add-btn" id="remNew"><span class="ms">add_alarm</span>Neue Erinnerung</button>
    <div id="remForm"></div>
    <div id="remList">${open.map(row).join("") || `<div class="empty">Keine offenen Erinnerungen.</div>`}</div>
    ${done.length ? `<div class="grp" style="margin-top:18px">Erledigt (14 Tage)</div><div>${done.map(row).join("")}</div>` : ""}`;
  $("remNew").onclick = () => form(null);
  $("page").onclick = async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const id = b.closest("[data-id]").dataset.id;
    const r = list.find((x) => x.id === id);
    if (b.dataset.act === "edit") return form(r);
    if (b.dataset.act === "fire" && await attempt(() => api.post(`/reminders/${id}/fire`), "Ausgelöst")) setTimeout(remindersPage, 800);
    if (b.dataset.act === "del" && confirm("Erinnerung löschen?") && await attempt(() => api.del(`/reminders/${id}`), "Gelöscht")) remindersPage();
  };
}

function form(r) {
  const start = r ? new Date(r.due_at) : (() => { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); return d; })();
  const f = { text: r?.text || "", kind: r?.kind || "remind", agent_id: r?.agent_id || mainAgent().id, day: isoDay(start), time: `${pad(start.getHours())}:${pad(start.getMinutes())}`,
    show_on_home: r ? r.show_on_home : true };
  const box = $("remForm");
  $("remNew").hidden = true;
  const keep = () => { f.text = $("rText").value; };
  const draw = () => {
    box.innerHTML = `<section class="rf-sec rem-form">
      <h2 class="rf-h"><span class="ms">add_alarm</span>${r ? "Erinnerung ändern" : "Neue Erinnerung"}</h2>
      <label class="fld"><span>Woran erinnern / was tun?</span><textarea id="rText" class="autogrow" rows="2" placeholder="z. B. Zahnarzt anrufen">${esc(f.text)}</textarea></label>
      <div class="seg seg-sub" id="rKind"><button type="button" data-k="remind" class="${f.kind === "remind" ? "on" : ""}"><span class="ms">alarm</span>Nur erinnern</button><button type="button" data-k="task" class="${f.kind === "task" ? "on" : ""}"><span class="ms">task_alt</span>Agent erledigt es</button></div>
      ${f.kind === "task" ? `<div class="flabel">Wer?</div><div class="chipset" id="rAgents">${agents().map((a) => `<button type="button" data-a="${a.id}" class="${f.agent_id === a.id ? "on" : ""}">${esc(a.name)}</button>`).join("")}</div>` : ""}
      <div class="flabel">Wann?</div>
      <div class="chipset" id="rDays">${Array.from({ length: 7 }, (_, i) => { const d = new Date(); d.setDate(d.getDate() + i); return isoDay(d); })
        .map((d, i) => `<button type="button" data-day="${d}" class="${d === f.day ? "on" : ""}">${i === 0 ? "Heute" : i === 1 ? "Morgen" : i === 2 ? "Übermorgen" : dayLabel(d)}</button>`).join("")}
        <label class="time-add-btn once-date"><span class="ms">calendar_month</span>Anderes Datum<input type="date" id="rDate" min="${isoDay(new Date())}" value="${f.day}"></label></div>
      <div class="chipset" style="margin-top:8px"><button type="button" class="time-chip once-time" id="rTime"><span class="ms">schedule</span>${f.time}<span class="ms">expand_more</span></button></div>
      <div class="rf-sum"><span class="ms">info</span><span>${f.kind === "task"
        ? `<b>${esc(agentOf(f.agent_id).name)}</b> erledigt das <b>${esc(dayLabel(f.day))} um ${f.time} Uhr</b> – das Ergebnis kommt als Benachrichtigung.`
        : `Du bekommst <b>${esc(dayLabel(f.day))} um ${f.time} Uhr</b> eine Benachrichtigung – ohne KI, kostet kein Limit.`}</span></div>
      <div class="slist toggles rf-toggle"><div class="srow"><span class="smain"><b>Im Tagesplan auf der Startseite</b><small>An dem Tag steht sie dort mit Uhrzeit</small></span>${sw(f.show_on_home, 'id="rHome" aria-label="Im Tagesplan zeigen"')}</div></div>
      <div class="err" id="rErr"></div>
      <div class="btn-row"><button class="es-btn" id="rCancel">Abbrechen</button><button class="es-btn primary" id="rSave"><span class="ms">check</span>${r ? "Speichern" : "Anlegen"}</button></div>
    </section>`;
    autogrow($("rText"));
    $("rKind").onclick = (e) => { const b = e.target.closest("[data-k]"); if (!b) return; keep(); f.kind = b.dataset.k; draw(); };
    if ($("rAgents")) $("rAgents").onclick = (e) => { const b = e.target.closest("[data-a]"); if (!b) return; keep(); f.agent_id = b.dataset.a; draw(); };
    $("rDays").onclick = (e) => { const b = e.target.closest("[data-day]"); if (!b) return; keep(); f.day = b.dataset.day; draw(); };
    $("rDate").parentElement.onclick = (e) => { e.preventDefault(); try { $("rDate").showPicker(); } catch { $("rDate").focus(); } };
    $("rDate").onchange = () => { if ($("rDate").value) { keep(); f.day = $("rDate").value; draw(); } };
    $("rTime").onclick = () => openTimePicker($("rTime"), { value: f.time, action: "übernehmen", icon: "check", onPick: (v) => { keep(); f.time = v; draw(); } });
    $("rHome").onclick = () => { f.show_on_home = !f.show_on_home; $("rHome").classList.toggle("on", f.show_on_home); };
    $("rCancel").onclick = () => { box.innerHTML = ""; $("remNew").hidden = false; };
    $("rSave").onclick = async () => {
      keep();
      if (!f.text.trim()) { $("rErr").textContent = "Bitte eintragen, woran erinnert werden soll."; return; }
      const body = { text: f.text, kind: f.kind, when: `${f.day}T${f.time}`, agent_id: f.agent_id, show_on_home: f.show_on_home };
      try {
        await (r ? api.patch(`/reminders/${r.id}`, body) : api.post("/reminders", body));
        toast(r ? "Gespeichert" : "Erinnerung angelegt");
        remindersPage();
      } catch (err) { $("rErr").textContent = err.message; }
    };
    $("rText").focus();
  };
  draw();
  box.scrollIntoView({ block: "nearest" });
}
