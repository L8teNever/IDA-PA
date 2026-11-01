// Tab "Actions": Workflow-Läufe des GitHub-Repos, ihre Jobs und Schritte, Logs zum Ansehen, erneut starten / abbrechen.
import { api } from "./api.js";
import { esc, fmtDur, fmtRel, toast } from "./ui.js";

// [Icon, Farbklasse, Text] je Lauf / Job / Schritt
export function runState(x) {
  if (x.status === "in_progress") return ["progress_activity", "run", "Läuft"];
  if (x.status && x.status !== "completed") return ["schedule", "wait", "Wartet"];
  switch (x.conclusion) {
    case "success": return ["check_circle", "ok", "Erfolgreich"];
    case "failure": case "timed_out": case "startup_failure": return ["cancel", "err", "Fehlgeschlagen"];
    case "cancelled": return ["block", "muted", "Abgebrochen"];
    case "skipped": return ["skip_next", "muted", "Übersprungen"];
    case "action_required": return ["pending_actions", "wait", "Wartet auf Freigabe"];
    default: return ["help", "muted", x.conclusion || "Unbekannt"];
  }
}
const icon = (x) => { const [ic, cls, t] = runState(x); return `<span class="ms ac-st ${cls} ${cls === "run" ? "spin" : ""}" title="${esc(t)}">${ic}</span>`; };
const dur = (a, b) => (a && (b || true) ? fmtDur(Math.max(0, (b ? new Date(b) : new Date()) - new Date(a))) : "");

// Zeitstempel vorn wegnehmen, Markierungen von GitHub in Klassen umsetzen
function logHtml(text) {
  return text.split("\n").map((raw) => {
    const l = raw.replace(/^﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ?/, "");
    let c = "";
    if (l.startsWith("##[error]")) c = "del";
    else if (l.startsWith("##[warning]")) c = "warn";
    else if (l.startsWith("##[group]")) c = "file";
    else if (l.startsWith("##[endgroup]")) return "";
    else if (l.startsWith("##[")) c = "meta";
    return `<i class="${c}">${esc(l.replace(/^##\[(group|error|warning|command|debug|notice)\]/, "") || " ")}</i>`;
  }).join("");
}

export function mountActions(el, projectId, isCurrent) {
  const base = `/code/projects/${projectId}/actions`;
  let timer = null, view = { kind: "list" }, onlyBranch = false, st = null;
  const alive = () => isCurrent();
  const stop = () => { clearTimeout(timer); timer = null; };
  const later = (fn, ms) => { stop(); timer = setTimeout(() => { if (alive()) fn(); }, ms); };
  const fail = (e) => {
    if (!alive()) return;
    const local = /GitHub-Repo/.test(e.message);
    el.innerHTML = `<div class="pane-empty clean"><span class="empty-ic"><span class="ms big">${local ? "cloud_off" : "error"}</span></span>
      <b>${local ? "Kein GitHub-Repo" : "Actions nicht verfügbar"}</b><small>${esc(e.message)}</small></div>`;
  };

  // ---- Liste der Läufe ----
  const showList = async (quiet = false) => {
    view = { kind: "list" };
    if (!quiet) el.innerHTML = `<div class="pane-loading"><span class="ms spin">progress_activity</span>Lade Actions …</div>`;
    let d;
    try {
      st = st || await api.get(`/code/projects/${projectId}/git`);
      d = await api.get(`${base}/runs${onlyBranch ? `?branch=${encodeURIComponent(st.branch)}` : ""}`);
    } catch (e) { return fail(e); }
    if (!alive() || view.kind !== "list") return;
    const open = d.runs.some((r) => r.status !== "completed");
    el.innerHTML = `<div class="pane-box"><div class="ac-bar">
        <span class="ac-count">${d.total} Läufe</span>
        <button type="button" class="chip ${onlyBranch ? "has" : ""}" data-branch title="Nur Läufe dieses Branches"><span class="ms">alt_route</span>${esc(st.branch)}</button>
        <button type="button" class="icon-btn sm" data-reload title="Neu laden"><span class="ms">refresh</span></button></div>
      <div class="ac-list">${d.runs.length ? d.runs.map((r) => `<button type="button" class="ac-run" data-run="${r.id}">
        ${icon(r)}<span class="ac-main"><b>${esc(r.name || r.workflow)}</b>
        <small>${esc(r.workflow || "")} #${r.number} · ${esc(r.sha)} · ${esc(r.branch || "")}${r.actor ? ` · ${esc(r.actor)}` : ""}</small></span>
        <span class="ac-time"><small>${r.created_at ? fmtRel(r.created_at) : ""}</small><small>${r.status === "completed" ? dur(r.started_at || r.created_at, r.updated_at) : ""}</small></span></button>`).join("")
        : `<div class="pane-empty clean"><span class="empty-ic"><span class="ms big">play_circle</span></span><b>Noch keine Läufe</b><small>${onlyBranch ? "Auf diesem Branch ist noch nichts gelaufen." : "In diesem Repo ist noch kein Workflow gelaufen."}</small></div>`}</div></div>`;
    el.querySelector("[data-reload]").onclick = () => showList();
    el.querySelector("[data-branch]").onclick = () => { onlyBranch = !onlyBranch; showList(); };
    el.querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => showRun(+b.dataset.run)));
    if (open) later(() => showList(true), 8000);
  };

  // ---- ein Lauf mit Jobs und Schritten ----
  const showRun = async (id, quiet = false) => {
    view = { kind: "run", id };
    if (!quiet) el.innerHTML = `<div class="pane-loading"><span class="ms spin">progress_activity</span>Lade Lauf …</div>`;
    let r;
    try { r = await api.get(`${base}/runs/${id}`); } catch (e) { return fail(e); }
    if (!alive() || view.kind !== "run" || view.id !== id) return;
    const [, , label] = runState(r);
    const running = r.status !== "completed";
    const failed = r.jobs.some((j) => ["failure", "timed_out"].includes(j.conclusion));
    el.innerHTML = `<div class="pane-box"><div class="fv-head"><button type="button" class="icon-btn sm" data-back title="Zurück"><span class="ms">arrow_back</span></button>
        <div class="fv-meta"><b>${esc(r.name || r.workflow)}</b><small>${esc(r.workflow || "")} #${r.number} · ${esc(r.sha)} · ${esc(r.branch || "")}</small></div></div>
      <div class="ac-head">${icon(r)}<b>${esc(label)}</b><small>${r.created_at ? fmtRel(r.created_at) : ""}${r.status === "completed" ? ` · ${dur(r.started_at || r.created_at, r.updated_at)}` : ""}</small></div>
      <div class="act-row">${running ? `<button type="button" class="btn-tonal" data-do="cancel"><span class="ms">stop_circle</span><span class="lbl">Abbrechen</span></button>`
        : `${failed ? `<button type="button" class="btn-primary" data-do="rerun-failed"><span class="ms">replay</span><span class="lbl">Fehlgeschlagene neu starten</span></button>` : ""}
           <button type="button" class="btn-tonal" data-do="rerun"><span class="ms">restart_alt</span><span class="lbl">Alles neu starten</span></button>`}
        ${r.url ? `<a class="btn-tonal" href="${esc(r.url)}" target="_blank" rel="noopener" title="Auf GitHub öffnen"><span class="ms">open_in_new</span><span class="lbl">GitHub</span></a>` : ""}</div>
      <div class="ac-jobs">${r.jobs.map((j) => `<details class="ac-job" ${j.conclusion === "failure" || j.status === "in_progress" ? "open" : ""}>
        <summary>${icon(j)}<span class="ac-main"><b>${esc(j.name)}</b></span><small>${dur(j.started_at, j.completed_at)}</small>
          <button type="button" class="btn-tonal sm" data-log="${j.id}" data-name="${esc(j.name)}"><span class="ms">terminal</span>Log</button></summary>
        <div class="ac-steps">${j.steps.map((s) => `<div class="ac-step">${icon(s)}<span>${esc(s.name)}</span></div>`).join("")}</div></details>`).join("") || `<div class="pane-empty">Noch keine Jobs.</div>`}</div>
      <div class="ferr" data-err></div></div>`;
    el.querySelector("[data-back]").onclick = () => showList();
    el.querySelectorAll("[data-log]").forEach((b) => (b.onclick = (e) => { e.preventDefault(); showLog(r, +b.dataset.log, b.dataset.name); }));
    el.querySelectorAll("[data-do]").forEach((b) => (b.onclick = async () => {
      b.disabled = true;
      try {
        await api.post(`${base}/runs/${id}/${b.dataset.do}`);
        toast(b.dataset.do === "cancel" ? "Wird abgebrochen …" : "Wird neu gestartet …");
        setTimeout(() => alive() && showRun(id, true), 2500);
      } catch (e) { el.querySelector("[data-err]").textContent = e.message; b.disabled = false; }
    }));
    if (running) later(() => showRun(id, true), 6000);
  };

  // ---- Log eines Jobs ----
  const showLog = async (run, jobId, name) => {
    stop();
    view = { kind: "log", id: jobId };
    el.innerHTML = `<div class="pane-box file-mode"><div class="fv-head"><button type="button" class="icon-btn sm" data-back title="Zurück zum Lauf"><span class="ms">arrow_back</span></button>
        <div class="fv-meta"><b>${esc(name)}</b><small>${esc(run.workflow || "")} #${run.number}</small></div>
        <button type="button" class="icon-btn sm" data-copy title="Log kopieren"><span class="ms">content_copy</span></button>
        <button type="button" class="icon-btn sm" data-reload title="Neu laden"><span class="ms">refresh</span></button></div>
      <div class="fv-body" data-body><div class="pane-loading"><span class="ms spin">progress_activity</span>Lade Log …</div></div></div>`;
    el.querySelector("[data-back]").onclick = () => showRun(run.id);
    let raw = "";
    const load = async () => {
      const body = el.querySelector("[data-body]");
      try {
        const d = await api.get(`${base}/jobs/${jobId}/log`);
        if (!alive() || view.kind !== "log" || view.id !== jobId) return;
        raw = d.log;
        body.innerHTML = `${d.truncated ? `<p class="sheet-hint">Das Log ist lang – gezeigt wird das Ende.</p>` : ""}<pre class="diff ac-log">${logHtml(d.log)}</pre>`;
        const pre = body.querySelector("pre");
        const firstErr = pre.querySelector("i.del");
        (firstErr || pre.lastElementChild)?.scrollIntoView({ block: firstErr ? "center" : "end" });
      } catch (e) { if (alive()) body.innerHTML = `<div class="pane-empty clean"><span class="empty-ic"><span class="ms big">description</span></span><b>Kein Log</b><small>${esc(e.message)}</small></div>`; }
    };
    el.querySelector("[data-reload]").onclick = load;
    el.querySelector("[data-copy]").onclick = () => navigator.clipboard?.writeText(raw).then(() => toast("Kopiert"), () => toast("Kopieren nicht möglich"));
    load();
  };

  showList();
}
