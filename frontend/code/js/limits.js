// KI-Limits aller Accounts aus IDA (Claude: Sitzung/Woche, Antigravity: je Modellfamilie) als Dialog.
import { api } from "./api.js";
import { esc, openModal, sheetHead } from "./ui.js";

function resetText(iso) {
  if (!iso) return "";
  const ms = new Date(iso) - Date.now();
  if (ms <= 0) return "setzt sich gleich zurück";
  const min = Math.round(ms / 60000), h = Math.floor(min / 60), m = min % 60; // erst runden, sonst „23 Std 60 Min“
  if (h >= 24) return "Reset " + new Date(iso).toLocaleDateString("de-DE", { weekday: "long", hour: "2-digit", minute: "2-digit" });
  return `setzt sich in ${h ? h + " Std " : ""}${m} Min zurück`;
}

// kleine Werte mit einer Nachkommastelle (0,6 % statt „1 %“)
const pct = (v) => { const n = v ?? 0; return `${n > 0 && n < 10 ? n.toLocaleString("de-DE", { maximumFractionDigits: 1 }) : Math.round(n)} %`; };

function bar(label, w) {
  const p = Math.min(100, w.percent ?? 0);
  return `<div class="lim-row ${w.muted ? "muted" : ""}"><div class="lim-head"><b>${esc(label)}</b><span>${pct(w.percent)}</span></div>
    <div class="lim-bar ${p >= 80 ? "warn" : ""}"><div style="width:${p}%"></div></div>
    ${w.models?.length ? `<div class="lim-models">${w.models.map((m) => `<span>${esc(m)}</span>`).join("")}</div>` : ""}
    <small>${esc([resetText(w.resets_at), w.note].filter(Boolean).join(" · "))}</small></div>`;
}

function accountHtml(a) {
  const tag = `<span class="lim-tag">${a.provider === "antigravity" ? "Antigravity" : "Claude"}</span>`;
  const title = `<div class="set-gl">${esc(a.name)} ${tag}${a.stale ? ` · ${esc(a.note || "alte Werte")}` : ""}</div>`;
  if (a.error) return `${title}<div class="lim-card"><div class="ferr">${esc(a.error)}</div></div>`;
  const sess = a.windows.find((w) => w.id === "five_hour");
  const week = a.windows.find((w) => w.id === "seven_day");
  const rest = a.windows.filter((w) => w !== sess && w !== week);
  return `${title}<div class="lim-card">${sess ? bar("Aktuelle Sitzung", sess) : ""}${week ? bar("Diese Woche", week) : ""}${rest.map((w) => bar(w.label, w)).join("")}</div>`;
}

export function openLimits() {
  const m = openModal(`${sheetHead("KI-Limits", "data_usage")}<div class="sheet-scroll" id="limBody"><p class="sheet-hint">Lade …</p></div>
    <div class="sheet-actions"><button type="button" class="btn-tonal" id="limRefresh"><span class="ms">refresh</span>Aktualisieren</button></div>`);
  const body = m.el.querySelector("#limBody");
  const load = async (force) => {
    try {
      const list = await api.get(`/usage${force ? "?force=true" : ""}`);
      if (body.isConnected) body.innerHTML = list.map(accountHtml).join("") || `<p class="sheet-hint">Kein Account in IDA.</p>`;
    } catch (e) {
      if (body.isConnected) body.innerHTML = `<div class="ferr">${esc(e.message || "Limits nicht abrufbar")}</div>`;
    }
  };
  m.el.querySelector("#limRefresh").onclick = () => { body.innerHTML = `<p class="sheet-hint">Lade …</p>`; load(true); };
  load(false);
}
