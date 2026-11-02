// Einstellungen → Auto: which models "Auto" may pick, their profiles (strength + skills) and whether it may change provider.
import { api } from "../../api.js";
import { $, attempt, esc } from "../../ui/dom.js";
import { subHead } from "./index.js";

const MODES = [
  ["per_chat", "Pro Chat", "Der Anbieter wird bei der ersten Nachricht eines Chats gewählt und bleibt dann – die Session bleibt erhalten."],
  ["free", "Frei", "Auto darf bei jeder Nachricht zwischen Claude und Antigravity/Gemini wechseln (der Verlauf wird dann mitgegeben)."],
  ["claude", "Nur Claude", "Auto wechselt nur Modell und Stärke innerhalb von Claude."],
  ["antigravity", "Nur Antigravity", "Auto wechselt nur Modell und Stärke innerhalb von Antigravity (Gemini & Co.)."],
];
const TIERS = { 1: "leicht", 2: "mittel", 3: "stark" };
const baseName = (n) => String(n).replace(/\s*\((low|medium|high)\)\s*$/i, "");

export async function render(el) {
  const s = await api.get("/autoroute");
  let mode = s.provider_mode;
  const allowed = new Set(s.allowed);
  const own = JSON.parse(JSON.stringify(s.profiles || {})); // model id -> { tier, code, writing, research, speed }
  const open = new Set();

  // Antigravity level variants (Low/Medium/High) are one row: the checkbox and the profile apply to all of them
  const rows = () => {
    const by = new Map();
    for (const m of s.available) {
      if (!by.has(m.base)) by.set(m.base, { ...m, name: baseName(m.name), ids: [] });
      by.get(m.base).ids.push(m.id);
    }
    return [...by.values()];
  };
  const skillBar = (v) => `<span class="ar-bar"><i style="width:${(v || 0) * 10}%"></i></span>`;
  const rowHtml = (m) => {
    const on = m.ids.some((id) => allowed.has(id));
    const o = own[m.ids[0]] || {};
    const tier = o.tier || m.tier, sk = { ...(m.skills || {}), ...o };
    const edit = open.has(m.base);
    return `<div class="ar-row ${on ? "" : "off"}" data-base="${esc(m.base)}">
      <label class="ar-main"><input type="checkbox" data-ids="${esc(m.ids.join(","))}" ${on ? "checked" : ""} ${m.chat ? "" : "disabled"}>
        <span class="ar-txt"><b>${esc(m.name)}</b>${m.ids.length > 1 ? ` <small class="muted">· ${m.ids.length} Stufen</small>` : ""}
          <small>${tier ? TIERS[tier] : "Stärke unbekannt"}${m.note ? " · " + esc(m.note) : ""}${m.changed ? " · <b>angepasst</b>" : ""}</small></span></label>
      ${m.chat && m.skills ? `<span class="ar-skills" title="${Object.entries(s.skills).map(([k, n]) => `${n}: ${sk[k]}/10`).join(" · ")}">${Object.keys(s.skills).map((k) => skillBar(sk[k])).join("")}</span>` : ""}
      ${m.chat ? `<button type="button" class="icon-btn ar-edit" data-edit="${esc(m.base)}" title="Stärke und Fähigkeiten anpassen"><span class="ms">${edit ? "expand_less" : "tune"}</span></button>` : ""}
      ${edit ? `<div class="ar-form">
        <label class="fld"><span>Stärke</span><select data-k="tier">${[1, 2, 3].map((t) => `<option value="${t}" ${tier === t ? "selected" : ""}>${TIERS[t]}</option>`).join("")}</select></label>
        ${Object.entries(s.skills).map(([k, n]) => `<label class="fld"><span>${esc(n)} <b>${sk[k] ?? 5}</b>/10</span><input type="range" min="1" max="10" data-k="${k}" value="${sk[k] ?? 5}"></label>`).join("")}
        ${m.changed || own[m.ids[0]] ? `<button type="button" class="es-btn" data-reset="${esc(m.base)}"><span class="ms">undo</span>Standard</button>` : ""}
      </div>` : ""}</div>`;
  };

  const draw = () => {
    const all = rows();
    const groups = {};
    for (const m of all) ((groups[m.provider_name] ||= { main: [], more: [] })[m.recommended || m.ids.some((id) => allowed.has(id)) ? "main" : "more"]).push(m);
    el.innerHTML = subHead("Auto", "/einstellungen") + `
      <p class="sub-intro">Mit <b>Auto</b> in der Modell-Auswahl sucht IDA für jede Nachricht selbst Anbieter, Modell und Stärke aus: Unter den Modellen, die zur Schwierigkeit passen, nimmt es das, das für diese Art Anfrage (Code, Schreiben, Recherche …) am besten ist – bei Gleichstand das sparsamere. Unter jeder Antwort steht, was gewählt wurde. Die Entscheidung läuft lokal und kostet kein Limit.</p>
      <div class="rf-h" style="margin:14px 0 6px">Anbieter wechseln?</div>
      <div class="seg" id="aMode">${MODES.map(([id, label]) => `<button type="button" data-m="${id}" class="${mode === id ? "on" : ""}">${label}</button>`).join("")}</div>
      <div class="rf-sum"><span class="ms">info</span><span>${esc(MODES.find((x) => x[0] === mode)?.[2] || "")}</span></div>
      <div class="rf-h" style="margin:16px 0 6px">Modelle &amp; Profile <small class="muted">· Balken: ${Object.values(s.skills).map(esc).join(" · ")}</small></div>
      <p class="rf-note">Die Werte stammen aus öffentlichen Benchmarks (Stand Nov. 2026, grob gerundet) – mit <span class="ms" style="font-size:14px;vertical-align:-2px">tune</span> passt du sie an.</p>
      ${Object.entries(groups).map(([prov, g]) => `<div class="sgrp">${esc(prov)}</div><div class="ar-list">${g.main.map(rowHtml).join("")}</div>
        ${g.more.length ? `<details class="ar-more"><summary>Ältere und nicht empfohlene (${g.more.length})</summary><div class="ar-list">${g.more.map(rowHtml).join("")}</div></details>` : ""}`).join("")
        || `<div class="soon-box"><span class="ms">person</span>Noch keine Modelle – erst einen Account einrichten (Einstellungen → Accounts).</div>`}
      <div class="rf-h" style="margin:16px 0 6px">Gelernt aus deinen Bewertungen</div>
      ${s.stats.length ? `<div class="tool-list">${s.stats.map((x) => `<div class="tool-item"><span><b>${esc(x.name)}</b> · ${x.total} Bewertungen ·
        ${Math.round((x.ok / x.total) * 100)} % passte · ${x.weak}× zu schwach · ${x.strong}× zu stark
        ${x.shift > 0 ? ` · <b>nimmt jetzt stärkere Modelle</b>` : x.shift < 0 ? ` · <b>nimmt jetzt kleinere Modelle</b>` : ""}</span></div>`).join("")}</div>
        <button type="button" class="es-btn" id="aReset" style="margin-top:8px"><span class="ms">restart_alt</span>Gelerntes zurücksetzen</button>`
      : `<div class="rf-sum"><span class="ms">thumb_up</span><span>Noch keine Bewertungen. Unter jeder Auto-Antwort kannst du angeben, ob das Modell passte, zu schwach oder zu stark war – Auto passt sich daran an.</span></div>`}
      <div class="rf-sum" style="margin-top:8px"><span class="ms">${s.embed_model ? "hub" : "info"}</span><span>${s.embed_model
        ? `Ähnliche Anfragen: <b>an</b> (über <span class="mono">${esc(s.embed_model)}</span> in Ollama) – ähnelt eine neue Frage bewerteten, nimmt Auto die Stärke, die dort richtig war.`
        : "Ähnliche Anfragen: <b>aus</b> – dafür braucht es ein Embedding-Modell in Ollama (z. B. <span class=\"mono\">ollama pull nomic-embed-text</span>). Bis dahin lernt Auto pro Art der Anfrage."}</span></div>
      <div class="wiz-nav"><button class="es-btn primary" id="aSave"><span class="ms">check</span>Speichern</button></div>`;
    bind(all);
  };

  const bind = (all) => {
    $("aMode").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; mode = b.dataset.m; draw(); };
    el.querySelectorAll(".ar-main input[type=checkbox]").forEach((i) => {
      i.onchange = () => { i.dataset.ids.split(",").forEach((id) => (i.checked ? allowed.add(id) : allowed.delete(id))); i.closest(".ar-row").classList.toggle("off", !i.checked); };
    });
    el.querySelectorAll("[data-edit]").forEach((b) => { b.onclick = () => { open.has(b.dataset.edit) ? open.delete(b.dataset.edit) : open.add(b.dataset.edit); draw(); }; });
    el.querySelectorAll(".ar-form [data-k]").forEach((inp) => {
      inp.oninput = () => {
        const m = all.find((x) => x.base === inp.closest(".ar-row").dataset.base);
        for (const id of m.ids) own[id] = { ...(own[id] || {}), [inp.dataset.k]: Number(inp.value) };
        const lbl = inp.closest(".fld").querySelector("b");
        if (lbl) lbl.textContent = inp.value;
      };
      inp.onchange = draw;
    });
    el.querySelectorAll("[data-reset]").forEach((b) => {
      b.onclick = () => { all.find((x) => x.base === b.dataset.reset).ids.forEach((id) => delete own[id]); draw(); };
    });
    if ($("aReset")) $("aReset").onclick = async () => {
      if (!confirm("Alle Bewertungen vergessen? Auto entscheidet dann wieder nur nach seinen Regeln.")) return;
      const r = await attempt(() => api.del("/autoroute/learned"), "Zurückgesetzt");
      if (r) { Object.assign(s, r); draw(); }
    };
    $("aSave").onclick = async () => {
      if (!allowed.size) return attempt(() => Promise.reject(new Error("Mindestens ein Modell auswählen")));
      const r = await attempt(() => api.put("/autoroute", { provider_mode: mode, models: [...allowed], profiles: own }), "Gespeichert");
      if (r) { Object.assign(s, r); draw(); }
    };
  };
  draw();
}
