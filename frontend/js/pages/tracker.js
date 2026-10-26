// "/tracker", "/tracker/neu", "/tracker/<id>" – daily things to tick off („Tablette morgens + abends“), by you or by the agents.
// A slot with a time is followed up when it is still open then: a notification (no AI) or a linked routine.
// With a duration (brush teeth 3 min) a timer runs first (/tracker/<id>/timer or the NFC link), then it is ticked.
import { api } from "../api.js";
import { navigate } from "../router.js";
import { agentOf, mainAgent } from "../store.js";
import { $, attempt, esc, sw, toast } from "../ui/dom.js";
import { openTimePicker } from "../ui/timepicker.js";
import { setAgent, setView } from "../ui/shell.js";

const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const ICONS = ["task_alt", "medication", "pill", "water_drop", "fitness_center", "self_improvement", "local_florist", "pets", "bedtime", "menu_book", "restaurant", "cleaning_services"];
const PRESETS = {
  once: [{ label: "Täglich", time: null }],
  twice: [{ label: "Morgens", time: "08:00" }, { label: "Abends", time: "20:00" }],
  thrice: [{ label: "1. Mal", time: null }, { label: "2. Mal", time: null }, { label: "3. Mal", time: null }],
};
const ACTIONS = { none: "Nichts", notify: "Benachrichtigen", routine: "Routine starten" };
const DURATIONS = [0, 60, 120, 180, 300, 600];
const durText = (s) => (s % 60 ? `${Math.floor(s / 60) ? Math.floor(s / 60) + " Min " : ""}${s % 60} s` : `${s / 60} Min`);
const clockText = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function daysText(days) {
  const d = [...(days?.length ? days : [0, 1, 2, 3, 4, 5, 6])].sort();
  if (d.length === 7) return "täglich";
  if (d.join() === "0,1,2,3,4") return "Mo–Fr";
  if (d.join() === "5,6") return "Sa+So";
  return d.map((i) => DAYS[i]).join(", ");
}
const slotText = (s) => (s.time ? `${s.label} ${s.time}` : s.label);
const byText = (by) => (by === "link" ? "per NFC" : by && by !== "user" ? `von ${agentOf(by).name}` : "");
const dayLabel = (iso) => { const d = new Date(iso + "T12:00"); return `${DAYS[(d.getDay() + 6) % 7]} ${d.getDate()}.${d.getMonth() + 1}.`; };

function missedText(t, routines) {
  if (t.on_missed === "none") return "";
  const r = routines.find((x) => x.id === t.routine_id);
  return t.on_missed === "notify" ? "🔔 erinnert, wenn offen" : `⚙ startet „${r ? r.name : "?"}“, wenn offen`;
}

export async function trackerPage({ id } = {}) {
  setAgent(mainAgent());
  setView("page");
  const [list, routines] = await Promise.all([api.get("/trackers"), api.get("/routines")]);
  const due = list.filter((t) => t.enabled && t.today.due);
  const total = due.reduce((n, t) => n + t.today.total, 0), done = due.reduce((n, t) => n + t.today.done, 0);
  const card = (t) => {
    const today = t.today;
    const c = new Date(t.created_at), created = `${c.getFullYear()}-${String(c.getMonth() + 1).padStart(2, "0")}-${String(c.getDate()).padStart(2, "0")}`;
    return `<div class="rcard trk ${t.enabled ? "" : "off"}" data-id="${t.id}">
      <div class="trk-head">
        <span class="ric"><span class="ms">${esc(t.icon)}</span></span>
        <div class="rmain"><b>${esc(t.name)}</b>${t.note ? `<div class="rdesc">${esc(t.note)}</div>` : ""}
          <div class="rmeta"><span>${esc(daysText(t.days))}</span>${t.streak ? `<span>🔥 ${t.streak} ${t.streak === 1 ? "Tag" : "Tage"}</span>` : ""}
          ${missedText(t, routines) ? `<span>${esc(missedText(t, routines))}</span>` : ""}${t.enabled ? "" : "<span>pausiert</span>"}${t.show_on_home ? "" : "<span>nicht im Tagesplan</span>"}${t.links ? `<span>📶 ${t.links} NFC-Link${t.links > 1 ? "s" : ""}</span>` : ""}${t.duration_s ? `<span>⏱ ${durText(t.duration_s)}</span>` : ""}</div></div>
        <div class="rem-act"><button class="icon-btn" data-act="edit" aria-label="Bearbeiten"><span class="ms">edit</span></button></div>
      </div>
      ${today.due ? `<div class="trk-slots">${today.slots.map((s) => `<button type="button" class="trk-slot ${s.done ? "done" : ""}" data-slot="${esc(s.key)}" aria-pressed="${s.done}"
          title="${s.done ? "Zurücknehmen" : "Abhaken"}"><span class="trk-box"><span class="ms">${s.done ? "check" : ""}</span></span>
          <span class="trk-lbl"><b>${esc(s.label)}</b><small>${esc([s.time, byText(s.by)].filter(Boolean).join(" · ") || (s.done ? "erledigt" : "offen"))}</small></span></button>`).join("")}</div>`
        : `<div class="trk-free"><span class="ms">event_available</span>Heute nicht dran</div>`}
      ${t.duration_s && today.done < today.total ? `<a class="trk-timer" href="/tracker/${t.id}/timer"><span class="ms">timer</span>${clockText(t.duration_s)} starten
        <small>danach wird ${t.next && t.slots.length > 1 ? `„${esc(t.slots.find((s) => s.key === t.next)?.label || "")}“ ` : ""}abgehakt</small></a>` : ""}
      <div class="trk-dots" aria-label="Letzte 14 Tage">${t.recent.map((d, i) => {
        const cls = !d.due || (d.day < created && !d.done) ? "free" : d.done === d.total ? "full" : d.done ? "part" : "open";
        const today_ = i === t.recent.length - 1;
        return `<button type="button" class="trk-dot ${cls} ${today_ ? "today" : ""}" data-day="${d.day}" title="${dayLabel(d.day)}: ${d.due ? `${d.done}/${d.total}` : "nicht dran"}"
          aria-label="${dayLabel(d.day)}: ${d.done} von ${d.total}"><i></i><small>${DAYS[(new Date(d.day + "T12:00").getDay() + 6) % 7][0]}</small></button>`;
      }).join("")}</div>
    </div>`;
  };
  $("page").innerHTML = `
    <div class="page-head"><h1>Tracker</h1>${total ? `<span class="tag">${done}/${total} heute erledigt</span>` : ""}</div>
    <p class="sub-intro">Für alles, was du <b>regelmäßig abhakst</b> – „Tablette morgens und abends“. Ist etwas zur Uhrzeit noch offen, kann Ida dich erinnern oder eine <a href="/routinen">Routine</a> starten.
      Du kannst Ida auch einfach sagen: „Ich muss jeden Morgen und Abend meine Tablette nehmen“ oder „Hab die Tablette genommen“.</p>
    <button class="add-btn" id="trkNew"><span class="ms">add_task</span>Neuer Tracker</button>
    <div id="trkForm"></div>
    <div id="trkList">${list.map(card).join("") || `<div class="empty">Noch keine Tracker.</div>`}</div>`;
  $("trkNew").onclick = () => navigate("/tracker/neu");
  const again = () => trackerPage({ id });
  $("trkList").onclick = async (e) => {
    const el = e.target.closest("[data-act],[data-slot],[data-day]");
    if (!el) return;
    const t = list.find((x) => x.id === el.closest("[data-id]").dataset.id);
    if (el.dataset.act === "edit") return navigate(`/tracker/${t.id}`);
    if (el.dataset.slot) {
      const s = t.today.slots.find((x) => x.key === el.dataset.slot);
      if (await attempt(() => api.post(`/trackers/${t.id}/check`, { slot: s.key, undo: s.done }), s.done ? "Zurückgenommen" : `✓ ${s.label} abgehakt`)) again();
      return;
    }
    // a dot: tick the next open slot of that day; a full day: take the last tick back
    const d = t.recent.find((x) => x.day === el.dataset.day);
    if (!d.due && !d.done) return toast(`${dayLabel(d.day)}: nicht dran`);
    const undo = d.done === d.total;
    if (undo && !confirm(`${dayLabel(d.day)}: letzten Haken zurücknehmen?`)) return;
    if (await attempt(() => api.post(`/trackers/${t.id}/check`, { day: d.day, undo }), undo ? "Zurückgenommen" : `${dayLabel(d.day)} nachgetragen`)) again();
  };
  // /tracker/neu or /tracker/<id>: the form for it right away
  if (id) {
    const t = id === "neu" ? null : list.find((x) => x.id === id);
    if (id !== "neu" && !t) { toast("Tracker nicht gefunden"); return navigate("/tracker", { replace: true }); }
    form(t, routines);
  }
}

function form(t, routines) {
  const f = t ? { ...t, slots: t.slots.map((s) => ({ ...s })), days: t.days?.length ? [...t.days] : [0, 1, 2, 3, 4, 5, 6] }
    : { name: "", icon: "medication", note: "", slots: PRESETS.twice.map((s) => ({ ...s })), days: [0, 1, 2, 3, 4, 5, 6], on_missed: "notify", routine_id: null, show_on_home: true, enabled: true, duration_s: 0 };
  const box = $("trkForm");
  $("trkNew").hidden = true;
  const keep = () => {
    f.name = $("tName").value;
    f.note = $("tNote").value;
    box.querySelectorAll("[data-lbl]").forEach((i) => { f.slots[+i.dataset.lbl].label = i.value; });
    if ($("tRoutine")) f.routine_id = $("tRoutine").value || null;
    if ($("tDurMin")?.value) f.duration_s = Math.round(parseFloat($("tDurMin").value.replace(",", ".")) * 60) || 0;
  };
  const timed = () => f.slots.filter((s) => s.time);
  const summary = () => {
    if (f.on_missed === "none") return "Nur zum Abhaken – es wird nicht nachgehakt.";
    if (!timed().length) return "Zum Nachhaken braucht mindestens ein Zeitfenster eine Uhrzeit.";
    const what = f.on_missed === "notify" ? "bekommst du eine Benachrichtigung (ohne KI)"
      : `startet Ida die Routine <b>${esc(routines.find((r) => r.id === f.routine_id)?.name || "…")}</b>`;
    const when = timed().map((s) => `<b>${esc(s.label || "–")}</b> um <b>${s.time}</b>`).join(" bzw. ");
    return `Ist ${when} noch nicht abgehakt, ${what} – je Zeitfenster höchstens einmal am Tag.`;
  };
  const durSummary = () => (f.duration_s
    ? `Beim Öffnen des NFC-Links (oder mit „Timer starten“) läuft ein <b>${durText(f.duration_s)}</b>-Timer, das Display bleibt an – danach wird automatisch abgehakt.`
    : "Ohne Timer: Antippen bzw. der NFC-Link hakt sofort ab.");
  const links = t ? linkEditor(t) : null;
  const draw = () => {
    const r = routines.find((x) => x.id === f.routine_id);
    box.innerHTML = `<section class="rf-sec rem-form">
      <h2 class="rf-h"><span class="ms">add_task</span>${t ? "Tracker ändern" : "Neuer Tracker"}</h2>
      <label class="fld"><span>Was?</span><input id="tName" maxlength="60" value="${esc(f.name)}" placeholder="z. B. Tablette nehmen"></label>
      <label class="fld"><span>Notiz (optional)</span><input id="tNote" maxlength="500" value="${esc(f.note)}" placeholder="z. B. 1 Tablette, nach dem Essen"></label>
      <div class="flabel">Icon</div>
      <div class="chipset icons" id="tIcons">${ICONS.map((ic) => `<button type="button" data-ic="${ic}" class="${f.icon === ic ? "on" : ""}"><span class="ms">${ic}</span></button>`).join("")}</div>
      <div class="flabel">Wann abhaken?</div>
      <div class="chipset" id="tPresets"><button type="button" class="day-preset" data-p="once">1× täglich</button><button type="button" class="day-preset" data-p="twice">Morgens + Abends</button><button type="button" class="day-preset" data-p="thrice">3× täglich (egal wann)</button></div>
      <div class="trk-edit" id="tSlots">${f.slots.map((s, i) => `<div class="trk-erow">
        <input class="mini-in" data-lbl="${i}" maxlength="30" value="${esc(s.label)}" placeholder="z. B. Morgens">
        ${s.time ? `<span class="time-chip"><button type="button" class="trk-tpick" data-time="${i}" aria-label="Uhrzeit ändern"><span class="ms">schedule</span>${s.time}</button><button type="button" data-notime="${i}" aria-label="Ohne Uhrzeit"><span class="ms">close</span></button></span>`
          : `<button type="button" class="time-add-btn" data-time="${i}"><span class="ms">schedule</span>Uhrzeit</button>`}
        ${f.slots.length > 1 ? `<button type="button" class="icon-btn" data-rm="${i}" aria-label="Zeitfenster entfernen"><span class="ms">delete</span></button>` : ""}</div>`).join("")}
        ${f.slots.length < 6 ? `<button type="button" class="time-add-btn" id="tAddSlot"><span class="ms">add</span>Zeitfenster</button>` : ""}</div>
      <div class="flabel">Dauer (optional) – z. B. 3 Min Zähne putzen</div>
      <div class="chipset" id="tDur">${DURATIONS.map((d) => `<button type="button" data-dur="${d}" class="${f.duration_s === d ? "on" : ""}">${d ? durText(d) : "Ohne"}</button>`).join("")}
        <label class="trk-dur-own">eigene: <input class="mini-in" id="tDurMin" inputmode="decimal" placeholder="Min" value="${f.duration_s && !DURATIONS.includes(f.duration_s) ? String(+(f.duration_s / 60).toFixed(2)).replace(".", ",") : ""}"> Min</label></div>
      <div class="rf-sum"><span class="ms">timer</span><span>${durSummary()}</span></div>
      <div class="flabel">An welchen Tagen?</div>
      <div class="chipset" id="tDays">${DAYS.map((d, i) => `<button type="button" data-d="${i}" class="${f.days.includes(i) ? "on" : ""}">${d}</button>`).join("")}
        <button type="button" class="day-preset" data-preset="all">täglich</button><button type="button" class="day-preset" data-preset="week">Mo–Fr</button></div>
      <div class="flabel">Wenn zur Uhrzeit nicht abgehakt</div>
      <div class="seg seg-sub" id="tMissed">${Object.entries(ACTIONS).map(([k, v]) => `<button type="button" data-m="${k}" class="${f.on_missed === k ? "on" : ""}">${v}</button>`).join("")}</div>
      ${f.on_missed === "routine" ? `
        <label class="fld"><span>Routine</span><select id="tRoutine"><option value="">– Routine wählen –</option>${routines.map((x) => `<option value="${x.id}" ${x.id === f.routine_id ? "selected" : ""}>${esc(x.name)}${x.on_demand ? " (auf Abruf)" : ""}</option>`).join("")}</select></label>
        <p class="rf-note" style="margin-top:6px">Am besten eine Routine <b>„Auf Abruf“</b> – sonst läuft sie zusätzlich nach ihrem eigenen Zeitplan. Sie erfährt, welches Zeitfenster offen ist.
          ${r && !r.on_demand ? "<br>⚠ Die gewählte Routine hat einen eigenen Zeitplan." : ""} <a href="/routinen/neu">Neue Routine anlegen</a></p>` : ""}
      <div class="rf-sum"><span class="ms">info</span><span>${summary()}</span></div>
      <div class="slist toggles rf-toggle">
        <div class="srow"><span class="smain"><b>Im Tagesplan auf der Startseite</b><small>Dort direkt abhaken</small></span>${sw(f.show_on_home, 'id="tHome" aria-label="Im Tagesplan zeigen"')}</div>
        ${t ? `<div class="srow"><span class="smain"><b>Aktiv</b><small>Pausiert: kein Nachhaken, nicht im Tagesplan</small></span>${sw(f.enabled, 'id="tEnabled" aria-label="Aktiv"')}</div>` : ""}</div>
      <div class="err" id="tErr"></div>
      <div class="btn-row">
        ${t ? `<button class="es-btn danger" id="tDel"><span class="ms">delete</span>Löschen</button>` : ""}
        ${t && t.on_missed !== "none" && t.slots.some((s) => s.time) ? `<button class="es-btn" id="tTest"><span class="ms">notifications_active</span>Nachhaken testen</button>` : ""}
        <button class="es-btn" id="tCancel">Abbrechen</button><button class="es-btn primary" id="tSave"><span class="ms">check</span>${t ? "Speichern" : "Anlegen"}</button></div>
    </section>${t ? `<section class="rf-sec" id="tLinks"></section>` : ""}`;
    bind();
    if (t) links.draw();
  };
  const bind = () => {
    $("tIcons").onclick = (e) => { const b = e.target.closest("[data-ic]"); if (!b) return; keep(); f.icon = b.dataset.ic; draw(); };
    $("tPresets").onclick = (e) => {
      const b = e.target.closest("[data-p]");
      if (!b) return;
      keep();
      // keep the keys of existing slots, so earlier ticks stay attached
      f.slots = PRESETS[b.dataset.p].map((s, i) => ({ ...s, key: f.slots[i]?.key }));
      draw();
    };
    $("tSlots").onclick = (e) => {
      const b = e.target.closest("[data-time],[data-notime],[data-rm],#tAddSlot");
      if (!b) return;
      keep();
      if (b.id === "tAddSlot") { f.slots.push({ label: "", time: null }); return draw(); }
      if (b.dataset.rm) { f.slots.splice(+b.dataset.rm, 1); return draw(); }
      if (b.dataset.notime) { f.slots[+b.dataset.notime].time = null; return draw(); }
      const s = f.slots[+b.dataset.time];
      openTimePicker(b, { value: s.time || "08:00", action: "übernehmen", icon: "check", onPick: (v) => { keep(); s.time = v; draw(); } });
    };
    $("tDays").onclick = (e) => {
      const b = e.target.closest("[data-d],[data-preset]");
      if (!b) return;
      keep();
      if (b.dataset.preset) f.days = b.dataset.preset === "all" ? [0, 1, 2, 3, 4, 5, 6] : [0, 1, 2, 3, 4];
      else { const d = +b.dataset.d; f.days = f.days.includes(d) ? f.days.filter((x) => x !== d) : [...f.days, d].sort(); }
      draw();
    };
    $("tDur").onclick = (e) => { const b = e.target.closest("[data-dur]"); if (!b) return; keep(); f.duration_s = +b.dataset.dur; $("tDurMin").value = ""; draw(); };
    $("tDurMin").onchange = () => { keep(); draw(); };
    $("tMissed").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; keep(); f.on_missed = b.dataset.m; draw(); };
    if ($("tRoutine")) $("tRoutine").onchange = () => { keep(); draw(); };
    $("tHome").onclick = () => { f.show_on_home = !f.show_on_home; $("tHome").classList.toggle("on", f.show_on_home); };
    if ($("tEnabled")) $("tEnabled").onclick = () => { f.enabled = !f.enabled; $("tEnabled").classList.toggle("on", f.enabled); };
    $("tCancel").onclick = () => navigate("/tracker");
    if ($("tDel")) $("tDel").onclick = async () => {
      if (confirm(`Tracker „${t.name}“ samt Verlauf löschen?`) && await attempt(() => api.del(`/trackers/${t.id}`), "Gelöscht")) navigate("/tracker");
    };
    if ($("tTest")) $("tTest").onclick = async () => {
      const s = t.slots.find((x) => x.time);
      const j = await attempt(() => api.post(`/trackers/${t.id}/missed/${s.key}`));
      if (j) toast(j.action === "routine" ? "Routine gestartet" : j.action === "notify" ? "Benachrichtigung geschickt" : "Nichts eingestellt");
    };
    $("tSave").onclick = async () => {
      keep();
      if (!f.name.trim()) { $("tErr").textContent = "Bitte eintragen, was abgehakt werden soll."; return; }
      if (!f.days.length) { $("tErr").textContent = "Mindestens einen Tag wählen."; return; }
      const body = { name: f.name, note: f.note, icon: f.icon, slots: f.slots.map((s) => ({ key: s.key || null, label: s.label, time: s.time || null })),
        days: f.days, on_missed: f.on_missed, routine_id: f.on_missed === "routine" ? f.routine_id : null, show_on_home: f.show_on_home, duration_s: f.duration_s || 0 };
      if (t) body.enabled = f.enabled;
      try {
        await (t ? api.patch(`/trackers/${t.id}`, body) : api.post("/trackers", body));
        toast(t ? "Gespeichert" : "Tracker angelegt");
        navigate("/tracker");
      } catch (err) { $("tErr").textContent = err.message; }
    };
  };
  draw();
  if (!t) $("tName").focus();
  box.scrollIntoView({ block: "start" });
}

// NFC / tap links of a tracker: opening one ticks it (the slot that fits the time, or a fixed one).
function linkEditor(t) {
  let list = null;
  const url = (l) => location.origin + l.path;
  const target = (l) => (l.slot ? `immer „${t.slots.find((s) => s.key === l.slot)?.label || "?"}“` : "automatisch nach Uhrzeit");
  const used = (l) => (l.uses ? `${l.uses}× benutzt, zuletzt ${new Date(l.last_used_at).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}` : "noch nicht benutzt");
  const nfc = "NDEFReader" in window;
  const render = () => {
    const box = $("tLinks");
    if (!box) return;
    box.innerHTML = `<h2 class="rf-h"><span class="ms">nfc</span>NFC- & Abhak-Links</h2>
      <p class="rf-note">Öffnet man so einen Link, wird ${esc(t.name)} ${t.duration_s ? `nach einem <b>${durText(t.duration_s)}</b>-Timer (Display bleibt an)` : "sofort"} abgehakt – mit kleiner Animation. Schreib ihn z. B. mit der App „NFC Tools“ auf einen NFC-Tag
        und kleb ihn an die Tablettendose. Ohne festes Zeitfenster wählt IDA das passende nach der Uhrzeit (morgens → Morgens, abends → Abends, sonst das nächste offene).
        Das Handy muss IDA dabei erreichen (z. B. über deinen Cloudflare-Zugang).</p>
      ${list === null ? `<div class="empty">Lade …</div>` : list.map((l) => `<div class="trk-link" data-link="${l.id}">
        <div class="trk-link-main"><b>${esc(l.name)}</b><small>${esc(target(l))} · ${esc(used(l))}</small><code>${esc(url(l))}</code></div>
        <div class="trk-link-act">
          <button type="button" class="icon-btn" data-la="copy" aria-label="Link kopieren" title="Kopieren"><span class="ms">content_copy</span></button>
          ${navigator.share ? `<button type="button" class="icon-btn" data-la="share" aria-label="Teilen" title="Teilen"><span class="ms">share</span></button>` : ""}
          ${nfc ? `<button type="button" class="icon-btn" data-la="nfc" aria-label="Auf NFC-Tag schreiben" title="Auf NFC-Tag schreiben"><span class="ms">contactless</span></button>` : ""}
          <button type="button" class="icon-btn" data-la="del" aria-label="Link löschen" title="Löschen"><span class="ms">delete</span></button></div></div>`).join("")}
      <div class="trk-link-new">
        <input class="mini-in" id="lName" maxlength="40" placeholder="Name, z. B. Nachttisch">
        <label class="fld"><select id="lSlot"><option value="">Automatisch nach Uhrzeit</option>${t.slots.map((s) => `<option value="${esc(s.key)}">immer „${esc(s.label)}“</option>`).join("")}</select></label>
        <button type="button" class="es-btn" id="lAdd"><span class="ms">add_link</span>Link erstellen</button></div>`;
    $("lAdd").onclick = async () => {
      const l = await attempt(() => api.post(`/trackers/${t.id}/links`, { name: $("lName").value, slot: $("lSlot").value || null }), "Link erstellt");
      if (l) { list.push(l); render(); }
    };
    box.querySelectorAll("[data-la]").forEach((b) => (b.onclick = async () => {
      const l = list.find((x) => x.id === b.closest("[data-link]").dataset.link);
      const act = b.dataset.la;
      if (act === "copy") return attempt(() => navigator.clipboard.writeText(url(l)), "Link kopiert");
      if (act === "share") return navigator.share({ title: `${t.name} abhaken`, url: url(l) }).catch(() => {});
      if (act === "nfc") {
        toast("Halte jetzt den NFC-Tag ans Handy …");
        return attempt(() => new window.NDEFReader().write({ records: [{ recordType: "url", data: url(l) }] }), "Auf den NFC-Tag geschrieben");
      }
      if (act === "del" && confirm(`Link „${l.name}“ löschen? Ein NFC-Tag mit diesem Link funktioniert dann nicht mehr.`)
          && await attempt(() => api.del(`/trackers/${t.id}/links/${l.id}`), "Link gelöscht")) {
        list = list.filter((x) => x.id !== l.id);
        render();
      }
    }));
  };
  return {
    async draw() {
      render();
      if (list === null) {
        list = await api.get(`/trackers/${t.id}/links`).catch(() => []);
        render();
      }
    },
  };
}
