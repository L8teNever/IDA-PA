// "/limits" – subscription limits of every account (Claude: session/week, Antigravity: per model family).
import { api } from "../api.js";
import { mainAgent } from "../store.js";
import { $, esc } from "../ui/dom.js";
import { setAgent, setView } from "../ui/shell.js";

function resetText(iso) {
  if (!iso) return "";
  const ms = new Date(iso) - Date.now();
  if (ms <= 0) return "setzt sich gleich zurück";
  const h = Math.floor(ms / 3600000), m = Math.round((ms % 3600000) / 60000);
  if (h >= 24) return "Reset " + new Date(iso).toLocaleDateString("de-DE", { weekday: "long", hour: "2-digit", minute: "2-digit" });
  return `setzt sich in ${h ? h + " Std " : ""}${m} Min zurück`;
}

// small values with one decimal (0.6 % instead of "1 %")
const pct = (v) => { const n = v ?? 0; return `${n > 0 && n < 10 ? n.toLocaleString("de-DE", { maximumFractionDigits: 1 }) : Math.round(n)} %`; };

export async function usagePage(_, query) {
  setAgent(mainAgent());
  setView("page");
  $("page").innerHTML = `<div class="page-head"><h1>KI-Limit</h1><button class="icon-btn" id="uRefresh" aria-label="Aktualisieren"><span class="ms">refresh</span></button></div><div id="uList"><div class="empty">Lade …</div></div>`;
  $("uRefresh").onclick = () => usagePage(_, { force: "1" });
  const list = await api.get(`/usage${query.force ? "?force=true" : ""}`);
  $("uList").innerHTML = list.map((a) => {
    const tag = `<span class="prov-tag ${a.provider || "claude"}">${a.provider === "antigravity" ? "Antigravity" : "Claude"}</span>`;
    if (a.error) return `<div class="pcard"><div class="pc-head"><b>${esc(a.name)} ${tag}</b></div><div class="pc-foot">⚠ ${esc(a.error)}</div></div>`;
    const week = a.windows.find((w) => w.id === "seven_day");
    const sess = a.windows.find((w) => w.id === "five_hour");
    const others = a.windows.filter((w) => w !== week && w !== sess);
    const p = Math.round(week?.percent ?? 0);
    return `<div class="acc-block"><div class="grp">${esc(a.name)} ${tag}${a.stale ? " · " + esc(a.note || "alte Werte") : ""}</div>
      ${week ? `<div class="pcard ring-card"><div class="big-ring" style="--p:${p}"><span><b>${p} %</b><small>diese Woche</small></span></div>
        <div class="ring-info"><div class="ri-row"><span class="ms">restart_alt</span>${esc(resetText(week.resets_at))}</div>
        <div class="ri-row ${p < 80 ? "ok" : ""}"><span class="ms">${p < 80 ? "check_circle" : "warning"}</span>${p < 80 ? "Noch genug übrig" : "Wird knapp"}</div></div></div>` : ""}
      ${sess ? `<div class="pcard"><div class="pc-head"><b>Aktuelle Sitzung</b><span>${Math.round(sess.percent ?? 0)} %</span></div>
        <div class="ubar big"><div style="width:${Math.min(100, sess.percent ?? 0)}%"></div></div><div class="pc-foot">${esc(resetText(sess.resets_at))}</div></div>` : ""}
      ${others.map((w) => `<div class="pcard ${w.muted ? "muted" : ""}"><div class="pc-head"><b>${esc(w.label)}</b><span>${pct(w.percent)}</span></div>
        <div class="ubar"><div style="width:${Math.min(100, w.percent ?? 0)}%"></div></div>
        ${w.models?.length ? `<div class="chipset model-chips">${w.models.map((m) => `<span>${esc(m)}</span>`).join("")}</div>` : ""}
        <div class="pc-foot">${esc([resetText(w.resets_at), w.note].filter(Boolean).join(" · "))}</div></div>`).join("")}
    </div>`;
  }).join("") || `<div class="empty">Kein Account.</div>`;
}
