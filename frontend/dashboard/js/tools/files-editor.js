// Vollbild-Editor des Tools „Dateien“. CodeMirror lädt erst, wenn wirklich eine Datei geöffnet wird.
import { api, ApiError } from "../api.js";
import { confirmDialog, esc, fmtBytes, toast } from "../ui.js";

let wrapOn = localStorage.getItem("fs-wrap") === "1";

export async function openEditor(path, { onSaved } = {}) {
  const name = path.split("/").pop();
  const box = document.createElement("div");
  box.className = "fe";
  box.innerHTML = `<div class="fe-bar">
      <button type="button" class="icon-btn" data-a="close" aria-label="Schließen"><span class="ms">arrow_back</span></button>
      <div class="fe-title"><b><i class="fe-dot" hidden></i><span>${esc(name)}</span></b><small class="mono">${esc(path)}</small></div>
      <button type="button" class="icon-btn" data-a="find" title="Suchen und ersetzen (Strg+F)" aria-label="Suchen"><span class="ms">search</span></button>
      <button type="button" class="icon-btn ${wrapOn ? "on" : ""}" data-a="wrap" title="Zeilen umbrechen" aria-label="Zeilen umbrechen"><span class="ms">wrap_text</span></button>
      <button type="button" class="btn-primary act-btn" data-a="save" disabled title="Speichern (Strg+S)"><span class="ms">save</span><span class="lbl">Speichern</span></button>
    </div>
    <div class="fe-body"><div class="fe-load"><span class="ms spin">progress_activity</span>Öffne Datei …</div></div>
    <div class="fe-status"><span id="feLang"></span><span id="fePos"></span><span id="feSize"></span></div>`;
  document.body.appendChild(box);
  document.body.classList.add("fe-open");
  requestAnimationFrame(() => box.classList.add("show"));

  let ed = null, version = null, saved = "", saving = false, dirty = false;
  const $ = (s) => box.querySelector(s);

  const closeNow = () => {
    removeEventListener("beforeunload", guard);
    removeEventListener("keydown", onKey, true);
    document.body.classList.remove("fe-open");
    box.classList.remove("show");
    setTimeout(() => { ed?.destroy(); box.remove(); }, 200);
  };
  const guard = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  addEventListener("beforeunload", guard);
  async function close() {
    if (dirty && !(await confirmDialog("Änderungen verwerfen?", `${name} hat ungespeicherte Änderungen.`, { ok: "Verwerfen", danger: true }))) return;
    closeNow();
  }
  const onKey = (e) => {
    if (e.key === "Escape" && !box.querySelector(".cm-search") && !document.querySelector(".cd-backdrop")) { e.preventDefault(); close(); }
  };
  addEventListener("keydown", onKey, true);

  const mark = () => {
    dirty = !!ed && ed.value() !== saved;
    $(".fe-dot").hidden = !dirty;
    $('[data-a="save"]').disabled = !dirty || saving;
  };
  const status = () => {
    if (!ed) return;
    const p = ed.position();
    $("#fePos").textContent = `Zeile ${p.line}, Spalte ${p.col} · ${p.lines} Zeilen`;
    $("#feSize").textContent = fmtBytes(new Blob([ed.value()]).size);
  };

  async function save(force = false) {
    if (!ed || saving || !dirty) return;
    saving = true;
    mark();
    const text = ed.value();
    try {
      const r = await api.put("/fs/text", { path, content: text, version: force ? null : version });
      version = r.version;
      saved = text;
      toast("Gespeichert");
      onSaved?.();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        saving = false;
        if (await confirmDialog("Datei wurde geändert", "Jemand anderes hat die Datei inzwischen geändert. Mit deiner Fassung überschreiben?", { ok: "Überschreiben", danger: true })) return save(true);
        return mark();
      }
      toast(e.message || "Speichern ging nicht");
    }
    saving = false;
    mark();
    status();
  }

  box.addEventListener("click", (e) => {
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "close") close();
    if (a === "save") save();
    if (a === "find") ed?.find();
    if (a === "wrap") {
      wrapOn = !wrapOn;
      localStorage.setItem("fs-wrap", wrapOn ? "1" : "0");
      e.target.closest("[data-a]").classList.toggle("on", wrapOn);
      ed?.setWrap(wrapOn);
    }
  });

  try {
    const [d, cm] = await Promise.all([api.get("/fs/text", { path }), import("../../vendor/codemirror.js")]);
    version = d.version;
    saved = d.content;
    const body = $(".fe-body");
    body.innerHTML = "";
    ed = cm.createEditor({ parent: body, doc: d.content, filename: path, readOnly: d.readonly, onChange: () => { mark(); status(); }, onSave: () => save() });
    ed.setWrap(wrapOn);
    ed.view.dom.addEventListener("keyup", status);
    ed.view.dom.addEventListener("click", status);
    $("#feLang").textContent = d.readonly ? `${ed.language} · nur lesen` : ed.language;
    status();
    ed.focus();
  } catch (e) {
    closeNow();
    throw e;
  }
}
