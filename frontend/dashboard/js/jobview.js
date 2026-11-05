// Vorgang mit Live-Ausgabe in einem Dialog (Websites, Veröffentlichen): start() liefert {job}, danach folgt der Dialog der Ausgabe.
import { followJob } from "./api.js";
import { attempt, esc, openModal, sheetHead, toast } from "./ui.js";

export async function jobDialog(title, start, { onDone } = {}) {
  const r = await attempt(start);
  if (!r?.job) return null;
  const m = openModal(`${sheetHead(title, "terminal")}
    <div class="job-state"><span class="ms spin" id="jvIc">progress_activity</span><span id="jvTxt">Läuft …</span></div>
    <pre class="log job-log" id="jvLog"></pre>
    <div class="sheet-actions"><button type="button" class="btn-tonal" data-close>Schließen</button></div>`, { wide: true });
  const log = m.el.querySelector("#jvLog");
  let result = null;
  await followJob(r.job.id, (ev) => {
    if (ev.line != null) {
      const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
      log.textContent += ev.line + "\n";
      if (stick) log.scrollTop = log.scrollHeight;
    }
    if (ev.done) result = ev.done;
  }).catch(() => {});
  const ok = !!result?.ok;
  const ic = m.el.querySelector("#jvIc");
  ic.classList.remove("spin");
  ic.textContent = ok ? "check_circle" : "error";
  ic.className = `ms st-${ok ? "ok" : "err"}`;
  m.el.querySelector("#jvTxt").textContent = ok ? "Fertig" : "Fehlgeschlagen – siehe Ausgabe";
  toast(ok ? `${title} – fertig` : `${title} – fehlgeschlagen`);
  onDone?.(ok);
  return ok;
}

// Ergebnis mehrerer Schritte ({steps:[{step, ok, detail}]}) als Liste
export const stepsHtml = (steps) => `<div class="steps">${(steps || []).map((s) => `<div class="step ${s.ok ? "ok" : "err"}">
  <span class="ms">${s.ok ? "check_circle" : "error"}</span><b>${esc(s.step)}</b><span class="muted">${esc(s.detail || "")}</span></div>`).join("")}</div>`;
