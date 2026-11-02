// Einstellungen → Auto: which models "Auto" may pick and whether it may change provider.
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

export async function render(el) {
  const s = await api.get("/autoroute");
  let mode = s.provider_mode;
  const allowed = new Set(s.allowed);

  const draw = () => {
    const groups = {};
    for (const m of s.available) (groups[m.provider_name] ||= []).push(m);
    el.innerHTML = subHead("Auto", "/einstellungen") + `
      <p class="sub-intro">Mit <b>Auto</b> in der Modell-Auswahl sucht IDA für jede Nachricht selbst Anbieter, Modell und Stärke aus – leichte Fragen gehen an kleine Modelle, schwere an starke. Unter jeder Antwort steht, was gewählt wurde. Die Entscheidung läuft lokal über Regeln und kostet kein Limit.</p>
      <div class="rf-h" style="margin:14px 0 6px">Anbieter wechseln?</div>
      <div class="seg" id="aMode">${MODES.map(([id, label]) => `<button type="button" data-m="${id}" class="${mode === id ? "on" : ""}">${label}</button>`).join("")}</div>
      <div class="rf-sum"><span class="ms">info</span><span>${esc(MODES.find((x) => x[0] === mode)?.[2] || "")}</span></div>
      <div class="rf-h" style="margin:16px 0 6px">Diese Modelle darf Auto nehmen</div>
      ${Object.entries(groups).map(([prov, ms]) => `<div class="sgrp">${esc(prov)}</div><div class="tool-list">${ms.map((m) => `
        <label class="tool-item"><input type="checkbox" value="${esc(m.id)}" ${allowed.has(m.id) ? "checked" : ""}>
          <span>${esc(m.name)}${m.tier ? ` <small class="muted">· ${TIERS[m.tier]}</small>` : ` <small class="muted">· unbekannt</small>`}</span></label>`).join("")}</div>`).join("")
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
    $("aMode").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; keep(); mode = b.dataset.m; draw(); };
    if ($("aReset")) $("aReset").onclick = async () => {
      if (!confirm("Alle Bewertungen vergessen? Auto entscheidet dann wieder nur nach seinen Regeln.")) return;
      const r = await attempt(() => api.del("/autoroute/learned"), "Zurückgesetzt");
      if (r) { Object.assign(s, r); draw(); }
    };
    $("aSave").onclick = async () => {
      keep();
      if (!allowed.size) return attempt(() => Promise.reject(new Error("Mindestens ein Modell auswählen")));
      const r = await attempt(() => api.put("/autoroute", { provider_mode: mode, models: [...allowed] }), "Gespeichert");
      if (r) Object.assign(s, r);
    };
  };
  const keep = () => el.querySelectorAll(".tool-item input").forEach((i) => (i.checked ? allowed.add(i.value) : allowed.delete(i.value)));
  draw();
}
