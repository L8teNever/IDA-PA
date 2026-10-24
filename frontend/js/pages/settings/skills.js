// Einstellungen → Skills (list, edit page /einstellungen/skills/:id, new: /einstellungen/skills/neu).
import { api } from "../../api.js";
import { navigate } from "../../router.js";
import { colors, loadBoot, PALETTE, state } from "../../store.js";
import { $, attempt, esc, sw, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

const BASE = "/einstellungen/skills";
const ICONS = ["auto_fix_high", "summarize", "translate", "spellcheck", "psychology", "wb_sunny", "mail", "inbox", "work", "menu_book", "sick", "event", "call", "home_iot_device", "code", "shopping_cart"];

export async function render(el, id) {
  if (id) return edit(el, id === "neu" ? null : id);
  const list = await api.get("/skills");
  const y = PALETTE.gelb;
  el.innerHTML = subHead("Skills", "/einstellungen") + `
    <p class="sub-intro">Anleitungen, nach denen deine Agents bestimmte Aufgaben erledigen. Agents laden sie selbst, wenn sie passen. Im Chat startest du einen Skill mit <b>/name</b>.</p>
    <a class="add-btn" href="${BASE}/neu"><span class="ms">add</span>Skill hinzufügen</a>
    <div class="slist">${list.map((k) => {
      const who = k.agents.map((i) => state.boot.agents.find((a) => a.id === i)).filter(Boolean).map((a) => `<span style="color:${colors(a)["--accent"]}">${esc(a.name)}</span>`).join(" · ");
      return `<div class="srow ${k.enabled ? "" : "off-row"}" data-href="${BASE}/${k.id}">
        <span class="sic" style="background:${y["--container"]};color:${y["--accent"]}"><span class="ms">${esc(k.icon)}</span></span>
        <span class="smain"><b>${esc(k.name)} <span class="mono dim">/${esc(k.id)}</span></b><small>${esc(k.description)}</small><small>${who || "Kein Agent"}</small></span>
        ${sw(k.enabled, `data-toggle="${k.id}" aria-label="${esc(k.name)} an/aus"`)}</div>`;
    }).join("") || `<div class="empty">Noch keine Skills.</div>`}</div>`;
  el.querySelectorAll("[data-toggle]").forEach((b) => (b.onclick = async (e) => {
    e.stopPropagation();
    const k = list.find((x) => x.id === b.dataset.toggle);
    if (await attempt(() => api.patch(`/skills/${k.id}`, { enabled: !k.enabled }))) { await loadBoot(); render(el); }
  }));
}

async function edit(el, id) {
  const k = id ? await api.get(`/skills/${id}`) : { id: "", icon: "auto_fix_high", name: "", description: "", body: "", enabled: true, agents: [state.boot.agents.find((a) => a.is_main)?.id] };
  const draw = () => {
    el.innerHTML = subHead(id ? "Skill bearbeiten" : "Neuer Skill", BASE) + `
      ${id ? "" : `<div class="seg" id="kMode"><button class="on">Selbst schreiben</button><button data-file>SKILL.md importieren</button></div>`}
      <div class="flabel" style="margin-top:0">Icon</div>
      <div class="chipset icons" id="kIcons">${ICONS.map((ic) => `<button data-ic="${ic}" class="${k.icon === ic ? "on" : ""}"><span class="ms">${ic}</span></button>`).join("")}</div>
      <label class="fld" style="margin-top:12px"><span>Name</span><input id="kName" value="${esc(k.name)}" maxlength="60" placeholder="z. B. Einkaufsliste"></label>
      ${id ? `<div class="kv small"><span>Aufruf im Chat</span><b class="mono">/${esc(k.id)}</b></div>`
           : `<label class="fld"><span>Kürzel für den Chat (optional, a–z 0–9 -)</span><input id="kId" value="${esc(k.id)}" maxlength="32" placeholder="einkauf"></label>`}
      <label class="fld"><span>Wann benutzen?</span><input id="kDesc" value="${esc(k.description)}" maxlength="160" placeholder="Kurz, wofür der Skill da ist"></label>
      <label class="fld"><span>Anleitung</span><textarea id="kBody" rows="8" placeholder="Schritt für Schritt, was der Agent tun soll">${esc(k.body)}</textarea></label>
      <div class="flabel">Welche Agents dürfen ihn benutzen?</div>
      <div class="chipset" id="kAgents">${state.boot.agents.map((a) => `<button data-a="${a.id}" class="${k.agents.includes(a.id) ? "on" : ""}">${esc(a.name)}</button>`).join("")}</div>
      <div id="kErr" class="ferr"></div>
      <div class="wiz-nav">${id ? `<button class="es-btn danger" id="kDel"><span class="ms">delete</span>Löschen</button>` : `<a class="es-btn" href="${BASE}">Abbrechen</a>`}
        <button class="es-btn primary" id="kSave"><span class="ms">${id ? "check" : "add"}</span>${id ? "Speichern" : "Anlegen"}</button></div>
      <input type="file" id="kFile" accept=".md,.txt,text/markdown,text/plain" hidden>`;
    bind();
  };
  const keep = () => { k.name = $("kName").value; k.description = $("kDesc").value; k.body = $("kBody").value; if ($("kId")) k.id = $("kId").value; };
  const bind = () => {
    $("kIcons").onclick = (e) => { const b = e.target.closest("[data-ic]"); if (!b) return; keep(); k.icon = b.dataset.ic; draw(); };
    $("kAgents").onclick = (e) => { const b = e.target.closest("[data-a]"); if (!b) return; keep(); const a = b.dataset.a; k.agents = k.agents.includes(a) ? k.agents.filter((x) => x !== a) : [...k.agents, a]; draw(); };
    if ($("kMode")) $("kMode").onclick = (e) => { if (e.target.closest("[data-file]")) { keep(); $("kFile").click(); } };
    $("kFile").onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const text = await f.text();
      const fm = text.match(/^---\s*([\s\S]*?)\s*---\s*([\s\S]*)$/); // SKILL.md with name/description front matter
      if (fm) {
        const get = (key) => (fm[1].match(new RegExp("^" + key + ":\\s*(.+)$", "m")) || [])[1];
        k.name = (get("name") || k.name || f.name.replace(/\.\w+$/, "")).trim();
        k.description = (get("description") || k.description || "").trim();
        k.body = fm[2].trim();
      } else { k.name = k.name || f.name.replace(/\.\w+$/, ""); k.body = text.trim(); }
      draw(); toast("Datei übernommen, kurz prüfen und anlegen");
    };
    if ($("kDel")) $("kDel").onclick = async () => {
      if (confirm(`Skill „${k.name}“ löschen?`) && (await attempt(() => api.del(`/skills/${id}`), "Gelöscht"))) { await loadBoot(); navigate(BASE); }
    };
    $("kSave").onclick = async () => {
      keep();
      try {
        const body = { name: k.name, icon: k.icon, description: k.description, body: k.body, agents: k.agents };
        const s = id ? await api.patch(`/skills/${id}`, body) : await api.post("/skills", { ...body, id: k.id || null });
        await loadBoot(); toast(id ? "Gespeichert" : `/${s.id} angelegt`); navigate(`${BASE}/${s.id}`);
      } catch (err) { $("kErr").textContent = err.message; }
    };
  };
  draw();
}
