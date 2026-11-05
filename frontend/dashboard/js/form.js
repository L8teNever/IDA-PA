// Formular-Dialog im IDA-Stil (wie in IDA Code): Felder = [{ name, label, type, value, hint, placeholder, options, show }]
//   type: text (Standard) | textarea | switch | select (options: [{value, label}]) | seg (Knöpfe nebeneinander, options wie select)
//   show(values) -> bool: Feld nur zeigen, wenn das zutrifft (z. B. Repo nur bei Quelle GitHub)
// Gibt die Werte zurück oder null (abgebrochen). submit(values) darf einen Fehlertext liefern – dann bleibt der Dialog offen.
import { esc, openModal, sheetHead } from "./ui.js";

export function formDialog(title, fields, { ok = "Speichern", icon = "", submit, wide = true } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const vals = Object.fromEntries(fields.filter((f) => f.name).map((f) => [f.name, f.value ?? (f.type === "switch" ? false : f.options?.[0]?.value ?? "")]));
    const field = (f) => {
      const id = `ff_${f.name}`;
      const hint = f.hint ? `<small class="ff-hint">${f.hint}</small>` : "";
      if (f.type === "head") return `<div class="ff-head" data-ff="${f.key || ""}">${esc(f.label)}</div>`;
      if (f.type === "switch") {
        return `<div class="ff ff-switch" data-ff="${f.name}"><div><span>${esc(f.label)}</span>${hint}</div>
          <button type="button" class="sw ${vals[f.name] ? "on" : ""}" data-sw="${f.name}" aria-label="${esc(f.label)}"></button></div>`;
      }
      if (f.type === "seg") {
        return `<div class="ff" data-ff="${f.name}"><span>${esc(f.label)}</span><div class="seg-mini big">${f.options.map((o) =>
          `<button type="button" class="${o.value === vals[f.name] ? "on" : ""}" data-seg="${f.name}" data-v="${esc(o.value)}">${o.icon ? `<span class="ms">${o.icon}</span>` : ""}${esc(o.label)}</button>`).join("")}</div>${hint}</div>`;
      }
      const input = f.type === "textarea"
        ? `<textarea class="cd-input" id="${id}" rows="${f.rows || 3}" placeholder="${esc(f.placeholder || "")}" spellcheck="false">${esc(vals[f.name])}</textarea>`
        : f.type === "select"
          ? `<select class="cd-input" id="${id}">${f.options.map((o) => `<option value="${esc(o.value)}" ${o.value === vals[f.name] ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`
          : `<input class="cd-input" id="${id}" type="${f.type === "password" ? "password" : f.type === "number" ? "number" : "text"}" value="${esc(vals[f.name])}"
              placeholder="${esc(f.placeholder || "")}" autocomplete="off" spellcheck="false" ${f.type === "number" ? `min="${f.min ?? 0}"` : ""}>`;
      return `<label class="ff" data-ff="${f.name}"><span>${esc(f.label)}</span>${input}${hint}</label>`;
    };
    const m = openModal(`${sheetHead(title, icon)}<form class="ff-form" novalidate>${fields.map(field).join("")}
      <div class="ff-error" id="ffErr" hidden></div>
      <div class="sheet-actions"><button type="button" class="btn-tonal" data-close>Abbrechen</button><button class="btn-primary" type="submit" id="ffOk">${esc(ok)}</button></div></form>`,
    { wide, onClose: () => { if (!done) resolve(null); } });
    const form = m.el.querySelector("form");
    const read = () => {
      for (const f of fields) {
        if (!f.name || f.type === "switch" || f.type === "seg") continue;
        const el = form.querySelector(`#ff_${f.name}`);
        if (el) vals[f.name] = f.type === "number" ? Number(el.value || 0) : el.value;
      }
      return vals;
    };
    const visible = () => {
      read();
      for (const f of fields) {
        const box = form.querySelector(`[data-ff="${f.name || f.key || ""}"]`);
        if (box && f.show) box.hidden = !f.show(vals);
      }
    };
    form.addEventListener("click", (e) => {
      const sw = e.target.closest("[data-sw]");
      if (sw) { vals[sw.dataset.sw] = !vals[sw.dataset.sw]; sw.classList.toggle("on", vals[sw.dataset.sw]); visible(); }
      const seg = e.target.closest("[data-seg]");
      if (seg) {
        vals[seg.dataset.seg] = seg.dataset.v;
        form.querySelectorAll(`[data-seg="${seg.dataset.seg}"]`).forEach((b) => b.classList.toggle("on", b === seg));
        visible();
      }
    });
    form.addEventListener("input", visible);
    form.addEventListener("change", visible);
    form.onsubmit = async (e) => {
      e.preventDefault();
      const v = { ...read() };
      const err = form.querySelector("#ffErr");
      if (submit) {
        const btn = form.querySelector("#ffOk");
        btn.disabled = true;
        const msg = await submit(v).catch((x) => x.message || "Fehler");
        btn.disabled = false;
        if (msg) { err.textContent = msg; err.hidden = false; return; }
      }
      done = true;
      resolve(v);
      m.close();
    };
    visible();
    form.querySelector("input, textarea, select")?.focus();
  });
}

export const lines = (text) => String(text || "").split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean);
