// Einstellungen → Agents (list, wizard for a new agent, edit page per agent with tools/skills/runs).
import { api } from "../../api.js";
import { navigate } from "../../router.js";
import { colors, face, loadBoot, modelInfo, PALETTE, state } from "../../store.js";
import { $, attempt, closeSheet, esc, fmtDateTime, md, openSheet, sw, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

const BASE = "/einstellungen/agents";

export async function render(el, id) {
  if (id) return edit(el, id);
  const list = state.boot.agents;
  el.innerHTML = subHead("Agents", "/einstellungen") + `
    <p class="sub-intro">Jeder Agent hat eigene Farbe, Aufgabe und Persönlichkeit und darf nur die MCP-Tools benutzen, die du ihm gibst.</p>
    <button class="add-btn" id="newAgent"><span class="ms">add</span>Neuer Agent</button>
    <div class="slist">${list.map((a) => {
      const c = colors(a);
      return `<a class="srow ${a.enabled ? "" : "off-row"}" href="${BASE}/${a.id}">
        <span class="ag-dot" style="width:42px;height:42px;border-radius:14px;background:${c["--container"]};color:${c["--accent"]}">${face(a)}</span>
        <span class="smain"><b>${esc(a.name)}${a.is_main ? " · Haupt-Agent" : ""}</b><small>${esc(a.description)} · ${esc(modelInfo(a.model).name)}</small></span>
        <span class="ms schev">chevron_right</span></a>`;
    }).join("")}</div>`;
  $("newAgent").onclick = wizard;
}

// 3-step wizard from the design
async function wizard() {
  const connectors = await api.get("/connectors");
  const w = { step: 1, name: "", task: "", palette: "gelb", connectors: [], home: "simple" };
  const draw = () => {
    const c = PALETTE[w.palette];
    let body = "";
    if (w.step === 1) body = `
      <div class="wiz-prev"><div class="face-p" style="background:radial-gradient(120% 100% at 50% 25%, ${c["--face-top"]} 30%, ${c["--face-bottom"]} 100%);box-shadow:0 0 30px ${c["--glow"]}"><i></i><i></i></div></div>
      <label class="fld"><span>Name</span><input id="wName" value="${esc(w.name)}" placeholder="z. B. Mia" maxlength="40"></label>
      <label class="fld"><span>Aufgabe</span><input id="wTask" value="${esc(w.task)}" placeholder="Kümmert sich um Schule und Hausaufgaben" maxlength="120"></label>
      <div class="flabel">Farbe</div>
      <div class="colors" id="wCols">${Object.entries(PALETTE).map(([k, p]) => `<button data-c="${k}" class="${w.palette === k ? "on" : ""}" style="background:${p["--accent"]}" aria-label="${k}"></button>`).join("")}</div>`;
    if (w.step === 2) body = `
      <div class="flabel" style="margin-top:0">Welche MCP-Server darf ${esc(w.name)} benutzen?</div>
      ${connectors.length ? `<div class="chipset" id="wMcps">${connectors.map((m) => `<button data-m="${m.id}" class="${w.connectors.includes(m.id) ? "on" : ""}">${esc(m.name)}</button>`).join("")}</div>`
        : `<p class="sub-intro">Noch keine MCP-Server verbunden – geht auch später.</p>`}`;
    if (w.step === 3) body = `
      <div class="flabel" style="margin-top:0">Startseite von ${esc(w.name)}</div>
      <button class="opt-card ${w.home === "simple" ? "on" : ""}" data-h="simple"><span class="ms">face</span><span><b>Schlicht</b><small>Maskottchen groß in der Mitte, dazu Schnellstart-Buttons</small></span></button>
      <button class="opt-card ${w.home === "timeline" ? "on" : ""}" data-h="timeline"><span class="ms">view_timeline</span><span><b>Mit Timeline</b><small>Infos und Tagesablauf wie bei Ida</small></span></button>`;
    const sheet = openSheet(`
      <div class="sub-head" style="padding-top:0"><h1 style="font-size:22px">Neuer Agent</h1><span style="color:var(--muted);font-size:13px">${w.step} von 3</span></div>
      <div class="wiz-steps" style="--wiz:${c["--accent"]}">${[1, 2, 3].map((i) => `<span class="${i <= w.step ? "on" : ""}"></span>`).join("")}</div>
      ${body}<div id="wErr" class="ferr"></div>
      <div class="wiz-nav"><button class="es-btn" id="wBack">${w.step === 1 ? "Abbrechen" : "Zurück"}</button>
        <button class="es-btn primary" id="wNext" style="background:${c["--accent"]};color:${c["--on-accent"]}">${w.step === 3 ? "Agent anlegen" : "Weiter"}</button></div>`);
    sheet.style.setProperty("--wiz", c["--accent"]);
    const keep = () => { if ($("wName")) w.name = $("wName").value.trim(); if ($("wTask")) w.task = $("wTask").value.trim(); };
    if ($("wCols")) $("wCols").onclick = (e) => { const b = e.target.closest("[data-c]"); if (!b) return; keep(); w.palette = b.dataset.c; draw(); };
    if ($("wMcps")) $("wMcps").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; const i = b.dataset.m; w.connectors = w.connectors.includes(i) ? w.connectors.filter((x) => x !== i) : [...w.connectors, i]; draw(); };
    sheet.querySelectorAll("[data-h]").forEach((b) => (b.onclick = () => { w.home = b.dataset.h; draw(); }));
    $("wBack").onclick = () => { keep(); if (w.step === 1) closeSheet(); else { w.step--; draw(); } };
    $("wNext").onclick = async () => {
      keep();
      if (w.step === 1 && !w.name) { $("wErr").textContent = "Gib dem Agent einen Namen."; return; }
      if (w.step < 3) { w.step++; draw(); return; }
      try {
        const a = await api.post("/agents", { name: w.name, description: w.task, intro: w.task || `Hi, ich bin ${w.name}.`, role: w.task,
          palette: w.palette, home: w.home, connectors: w.connectors, chips: [["chat", "Was kannst du?"]] });
        closeSheet(); await loadBoot(); toast(`${a.name} ist da`); navigate(`${BASE}/${a.id}`);
      } catch (err) { $("wErr").textContent = err.message; }
    };
  };
  draw();
}

async function edit(el, id) {
  const [a, connectors, skills] = await Promise.all([api.get(`/agents/${id}`), api.get("/connectors"), api.get("/skills")]);
  const c = colors(a);
  const opt = (list, cur, label = (x) => x) => list.map((x) => `<option value="${esc(x.id ?? x)}" ${cur === (x.id ?? x) ? "selected" : ""}>${esc(label(x))}</option>`).join("");
  el.innerHTML = subHead(a.name, BASE) + `
    <div class="pcard agent-head" style="--n-acc:${c["--accent"]}">
      <div class="wiz-prev"><div class="face-p" style="background:radial-gradient(120% 100% at 50% 25%, ${c["--face-top"]} 30%, ${c["--face-bottom"]} 100%);box-shadow:0 0 30px ${c["--glow"]}"><i></i><i></i></div></div>
      <div class="avatar-edit">
        <span class="ag-dot av-big" style="background:${c["--container"]};color:${c["--accent"]}">${face(a)}</span>
        <span class="smain"><b>Profilbild</b><small>Erscheint statt des Buchstabens bei Chats und im Agent-Menü</small></span>
        <input type="file" id="aPicFile" accept="image/png,image/jpeg,image/gif,image/webp" hidden>
        <button class="icon-btn small" id="aPicPick" aria-label="Bild wählen"><span class="ms">add_photo_alternate</span></button>
        ${a.avatar ? `<button class="icon-btn small" id="aPicDel" aria-label="Bild entfernen"><span class="ms">delete</span></button>` : ""}
      </div>
      <div class="colors" id="aCols">${Object.entries(PALETTE).map(([k, p]) => `<button data-c="${k}" class="${a.palette === k ? "on" : ""}" style="background:${p["--accent"]}" aria-label="${k}"></button>`).join("")}</div>
    </div>
    <label class="fld"><span>Name</span><input id="aName" maxlength="40" value="${esc(a.name)}"></label>
    <label class="fld"><span>Kurzbeschreibung</span><input id="aDesc" maxlength="120" value="${esc(a.description)}"></label>
    <label class="fld"><span>Begrüßung auf der Startseite</span><input id="aIntro" maxlength="300" value="${esc(a.intro)}"></label>
    <label class="fld"><span>Rolle / Aufgabe (Prompt)</span><textarea id="aRole" rows="4">${esc(a.role)}</textarea></label>
    <label class="fld"><span>Persönlichkeit & Stil</span><textarea id="aPers" rows="2" placeholder="z. B. locker, mit Humor, duzt mich">${esc(a.personality)}</textarea></label>
    <label class="fld"><span>Eigener Prompt / Zusatz-Anweisungen</span><textarea id="aInstr" rows="3">${esc(a.instructions)}</textarea></label>
    <div class="fld-row">
      <label class="fld"><span>Modell</span><select id="aModel">${opt(state.boot.models, a.model, (m) => modelInfo(m).name)}</select></label>
      <label class="fld"><span>Account</span><select id="aAcc"><option value="">erster Account</option>${opt(state.boot.accounts, a.account_id, (x) => x.name)}</select></label>
    </div>
    <div class="fld-row">
      <label class="fld"><span>Startseite</span><select id="aHome"><option value="simple" ${a.home === "simple" ? "selected" : ""}>Schlicht</option><option value="timeline" ${a.home === "timeline" ? "selected" : ""}>Mit Timeline</option></select></label>
    </div>
    <div class="slist toggles">
      <div class="srow"><span class="smain"><b>Bei Limit anderen Account nehmen</b><small>Gleiches Modell auf dem nächsten Account</small></span>${sw(a.fallback, 'data-t="fallback"')}</div>
      <div class="srow"><span class="smain"><b>Langzeit-Gedächtnis</b><small>Eigenes + gemeinsames Gedächtnis</small></span>${sw(a.memory_enabled, 'data-t="memory_enabled"')}</div>
      ${a.is_main ? "" : `<div class="srow"><span class="smain"><b>Aktiv</b><small>Pausiert = keine Läufe, keine Wake-ups</small></span>${sw(a.enabled, 'data-t="enabled"')}</div>`}
    </div>
    <div id="aErr" class="ferr"></div>
    <div class="btn-row"><button class="es-btn primary" id="aSave"><span class="ms">check</span>Speichern</button>
      ${a.is_main ? "" : `<button class="es-btn danger" id="aDel"><span class="ms">delete</span>Löschen</button>`}</div>

    <div class="grp">Schnellstart-Buttons</div>
    <label class="fld"><span>Eine Zeile pro Button: Icon | Text (Icons: Material Symbols)</span><textarea id="aChips" rows="3">${esc((a.chips || []).map((x) => x.join(" | ")).join("\n"))}</textarea></label>

    <div class="grp">MCP-Server & Tools</div>
    <p class="sub-intro">Nur du vergibst Tool-Zugriff. Ein Agent sieht nur die Server und Tools, die hier an sind.</p>
    <div class="slist" id="aConns">${connectors.length ? connectors.map((m) => {
      const granted = Object.prototype.hasOwnProperty.call(a.connectors, m.id);
      const tools = a.connectors[m.id];
      const n = granted ? (tools === null ? m.tools.filter((t) => !m.disabled_tools.includes(t)).length : tools.length) : 0;
      return `<div class="srow col">
        <div class="srow-top"><span class="sdot" style="background:${m.status === "error" ? "#ff8f8f" : m.enabled ? "#7ee2b8" : "#45464f"}"></span>
          <span class="smain"><b>${esc(m.name)}</b><small>${granted ? `${n} Tools${tools === null ? " (alle)" : ""}` : "kein Zugriff"}</small></span>
          ${granted && m.tools.length ? `<button class="icon-btn small" data-tools="${m.id}" aria-label="Tools wählen"><span class="ms">tune</span></button>` : ""}
          ${sw(granted, `data-conn="${m.id}"`)}</div></div>`;
    }).join("") : `<div class="empty">Noch keine MCP-Server (Einstellungen → MCP-Server).</div>`}</div>

    <div class="grp">Skills</div>
    <div class="chipset" id="aSkills">${skills.map((s) => `<button data-s="${s.id}" class="${a.skills.includes(s.id) ? "on" : ""}">${esc(s.name)}</button>`).join("") || `<span class="sub-intro">Keine Skills.</span>`}</div>

    <div class="grp">Notizen (Arbeitszettel des Agenten)</div>
    <label class="fld"><span>Der Agent pflegt sie selbst, du kannst sie ändern</span><textarea id="aNotes" rows="3">${esc(a.notes)}</textarea></label>
    <button class="es-btn" id="aNotesSave"><span class="ms">save</span>Notizen speichern</button>

    <div class="grp">Letzte Läufe</div>
    <div class="slist">${a.runs.length ? a.runs.map((r) => `<details class="srow run"><summary><span class="sic" style="color:${r.ok ? "#7ee2b8" : "#ff8f8f"}"><span class="ms">${r.ok ? "check_circle" : "error"}</span></span>
      <span class="smain"><b>${esc(r.trigger)}</b><small>${fmtDateTime(r.at)}${r.account ? " · " + esc(r.account) : ""}${r.tools.length ? " · 🔧 " + esc(r.tools.join(", ")) : ""}</small></span></summary>
      <div class="md run-text">${md(r.text)}</div></details>`).join("") : `<div class="empty">Noch keine Läufe.</div>`}</div>`;

  let palette = a.palette;
  const flags = { fallback: a.fallback, memory_enabled: a.memory_enabled, enabled: a.enabled };
  $("aCols").onclick = (e) => { const b = e.target.closest("[data-c]"); if (!b) return; palette = b.dataset.c; $("aCols").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); };
  el.querySelectorAll("[data-t]").forEach((b) => (b.onclick = () => { flags[b.dataset.t] = !flags[b.dataset.t]; b.classList.toggle("on", flags[b.dataset.t]); }));
  $("aSave").onclick = async () => {
    const chips = $("aChips").value.split("\n").map((l) => l.split("|").map((x) => x.trim())).filter((x) => x.length >= 2 && x[1]);
    try {
      await api.patch(`/agents/${id}`, { name: $("aName").value, description: $("aDesc").value, intro: $("aIntro").value, role: $("aRole").value,
        personality: $("aPers").value, instructions: $("aInstr").value, model: $("aModel").value, account_id: $("aAcc").value || null,
        home: $("aHome").value, palette, chips, ...flags });
      await loadBoot(); toast("Gespeichert"); render(el, id);
    } catch (err) { $("aErr").textContent = err.message; }
  };
  $("aPicPick").onclick = () => $("aPicFile").click();
  $("aPicFile").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const body = new FormData();
      body.append("file", await squarePicture(f), "avatar.jpg");
      await api.post(`/agents/${id}/avatar`, body);
      await loadBoot(); toast("Profilbild gespeichert"); render(el, id);
    } catch (err) { $("aErr").textContent = err.message; }
  };
  if ($("aPicDel")) $("aPicDel").onclick = async () => {
    if (await attempt(() => api.del(`/agents/${id}/avatar`), "Profilbild entfernt")) { await loadBoot(); render(el, id); }
  };
  if ($("aDel")) $("aDel").onclick = async () => {
    if (!confirm(`${a.name} wirklich löschen? Chats und Routinen gehen an den Haupt-Agenten, sein Gedächtnis wird gelöscht.`)) return;
    if (await attempt(() => api.del(`/agents/${id}`), `${a.name} gelöscht`)) { await loadBoot(); navigate(BASE); }
  };
  $("aConns").onclick = async (e) => {
    const t = e.target.closest("[data-tools]");
    if (t) return toolSheet(a, connectors.find((m) => m.id === t.dataset.tools), () => render(el, id));
    const b = e.target.closest("[data-conn]");
    if (!b) return;
    const on = !b.classList.contains("on");
    if (await attempt(() => api.put(`/agents/${id}/connectors/${b.dataset.conn}`, { allowed: on, tools: null }))) render(el, id);
  };
  $("aSkills").onclick = async (e) => {
    const b = e.target.closest("[data-s]");
    if (!b) return;
    const s = skills.find((x) => x.id === b.dataset.s);
    const agents = s.agents.includes(id) ? s.agents.filter((x) => x !== id) : [...s.agents, id];
    if (await attempt(() => api.patch(`/skills/${s.id}`, { agents }))) { s.agents = agents; b.classList.toggle("on"); }
  };
  $("aNotesSave").onclick = () => attempt(() => api.put(`/agents/${id}/notes`, { text: $("aNotes").value }), "Notizen gespeichert");
}

// Cuts the middle square out of the picture and shrinks it to 256 px (small upload, sharp enough for every place).
async function squarePicture(file, size = 256) {
  const img = await createImageBitmap(file).catch(() => { throw new Error("Das Bild lässt sich nicht lesen"); });
  const side = Math.min(img.width, img.height);
  const cv = document.createElement("canvas");
  cv.width = cv.height = size;
  cv.getContext("2d").drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, size, size);
  return new Promise((ok, no) => cv.toBlob((b) => (b ? ok(b) : no(new Error("Das Bild lässt sich nicht verkleinern"))), "image/jpeg", 0.88));
}

function toolSheet(a, m, done) {
  const granted = a.connectors[m.id];
  const enabled = m.tools.filter((t) => !m.disabled_tools.includes(t));
  let chosen = new Set(granted === null ? enabled : granted);
  const draw = () => {
    openSheet(`<div class="sub-head" style="padding-top:0"><h1 style="font-size:22px">${esc(m.name)}</h1><button class="icon-btn es-x" id="tClose"><span class="ms">close</span></button></div>
      <p class="sub-intro" style="margin:-6px 0 12px">Welche Tools darf ${esc(a.name)} benutzen? Am besten nur lesende, wenn er allein im Hintergrund läuft.</p>
      <div class="btn-row"><button class="es-btn" id="tAll">Alle</button><button class="es-btn" id="tNone">Keine</button></div>
      <div class="tool-list">${enabled.map((t) => `<label class="tool-item"><input type="checkbox" value="${esc(t)}" ${chosen.has(t) ? "checked" : ""}><span>${esc(t)}</span></label>`).join("")}</div>
      <div class="wiz-nav"><button class="es-btn" id="tCancel">Abbrechen</button><button class="es-btn primary" id="tSave"><span class="ms">check</span>Speichern</button></div>`);
    $("tClose").onclick = $("tCancel").onclick = closeSheet;
    $("tAll").onclick = () => { chosen = new Set(enabled); draw(); };
    $("tNone").onclick = () => { chosen = new Set(); draw(); };
    document.querySelectorAll(".tool-item input").forEach((x) => (x.onchange = () => (x.checked ? chosen.add(x.value) : chosen.delete(x.value))));
    $("tSave").onclick = async () => {
      const tools = chosen.size === enabled.length ? null : [...chosen];
      if (await attempt(() => api.put(`/agents/${a.id}/connectors/${m.id}`, { allowed: true, tools }), "Gespeichert")) { closeSheet(); done(); }
    };
  };
  draw();
}
