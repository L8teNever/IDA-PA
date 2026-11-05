// Formular-Dialog im IDA-Stil (wie in IDA Code): Felder = [{ name, label, type, value, hint, placeholder, options, show }]
//   type: text (Standard) | textarea | number | datetime | time | switch | select (options: [{value, label}]) | seg (Knöpfe nebeneinander, options wie select)
//         multi (Liste mit Häkchen und Suche, Wert = Array; options: [{value, label, parent?}] – ist parent gewählt, gilt der Eintrag automatisch mit)
//         live (kein Eingabefeld: render(values) -> HTML, wird bei jeder Änderung neu gezeichnet; braucht key)
//   show(values) -> bool: Feld nur zeigen, wenn das zutrifft (z. B. Repo nur bei Quelle GitHub)
// Gibt die Werte zurück oder null (abgebrochen). submit(values) darf einen Fehlertext liefern – dann bleibt der Dialog offen.
import { esc, openModal, popMenu, sheetHead } from "./ui.js";

export function formDialog(title, fields, { ok = "Speichern", icon = "", submit, wide = true } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const vals = Object.fromEntries(fields.filter((f) => f.name).map((f) => [f.name, f.value ?? (f.type === "switch" ? false : f.options?.[0]?.value ?? "")]));
    const field = (f) => {
      const id = `ff_${f.name}`;
      const hint = f.hint ? `<small class="ff-hint">${f.hint}</small>` : "";
      if (f.type === "head") return `<div class="ff-head" data-ff="${f.key || ""}">${esc(f.label)}</div>`;
      if (f.type === "live") return `<div class="ff-live" data-ff="${f.key}"></div>`;
      if (f.type === "switch") {
        return `<div class="ff ff-switch" data-ff="${f.name}"><div><span>${esc(f.label)}</span>${hint}</div>
          <button type="button" class="sw ${vals[f.name] ? "on" : ""}" data-sw="${f.name}" aria-label="${esc(f.label)}"></button></div>`;
      }
      if (f.type === "seg") {
        return `<div class="ff" data-ff="${f.name}"><span>${esc(f.label)}</span><div class="seg-mini big">${f.options.map((o) =>
          `<button type="button" class="${o.value === vals[f.name] ? "on" : ""}" data-seg="${f.name}" data-v="${esc(o.value)}">${o.icon ? `<span class="ms">${o.icon}</span>` : ""}${esc(o.label)}</button>`).join("")}</div>${hint}</div>`;
      }
      if (f.type === "multi") {
        return `<div class="ff" data-ff="${f.name}"><span>${esc(f.label)}</span>
          <label class="side-search"><span class="ms">search</span><input type="search" data-mfind="${f.name}" placeholder="Suchen" autocomplete="off"></label>
          <div class="ff-multi" data-mlist="${f.name}">${f.options.map((o) => `<button type="button" class="ff-check ${o.parent ? "sub" : ""}" data-multi="${f.name}" data-v="${esc(o.value)}">
            <span class="ms"></span><span class="ellip">${esc(o.label)}</span></button>`).join("")}</div>${hint}</div>`;
      }
      const input = f.type === "textarea"
        ? `<textarea class="cd-input" id="${id}" rows="${f.rows || 3}" placeholder="${esc(f.placeholder || "")}" spellcheck="false">${esc(vals[f.name])}</textarea>`
        : f.type === "select"
          ? `<button type="button" class="cd-input ff-pick" data-pick="${f.name}"><span class="ellip">${esc(optLabel(f, vals[f.name]))}</span><span class="ms">expand_more</span></button>`
          : `<input class="cd-input" id="${id}" type="${f.type === "password" ? "password" : f.type === "number" ? "number" : f.type === "datetime" ? "datetime-local" : f.type === "time" ? "time" : "text"}" value="${esc(vals[f.name])}"
              placeholder="${esc(f.placeholder || "")}" autocomplete="off" spellcheck="false" ${f.type === "number" ? `min="${f.min ?? 0}"` : ""}>`;
      if (f.type === "select") return `<div class="ff" data-ff="${f.name}"><span>${esc(f.label)}</span>${input}${hint}</div>`;
      return `<label class="ff" data-ff="${f.name}"><span>${esc(f.label)}</span>${input}${hint}</label>`;
    };
    const m = openModal(`${sheetHead(title, icon)}<form class="ff-form" novalidate><div class="ff-scroll">${fields.map(field).join("")}
      <div class="ff-error" id="ffErr" hidden></div></div>
      <div class="sheet-actions"><button type="button" class="btn-tonal" data-close>Abbrechen</button><button class="btn-primary" type="submit" id="ffOk">${esc(ok)}</button></div></form>`,
    { wide, onClose: () => { if (!done) resolve(null); } });
    const form = m.el.querySelector("form");
    const read = () => {
      for (const f of fields) {
        if (!f.name || f.type === "switch" || f.type === "seg" || f.type === "select" || f.type === "multi") continue;
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
        if (box && f.type === "live") box.innerHTML = f.render(vals);
      }
    };
    const ticks = (f) => {
      for (const b of form.querySelectorAll(`[data-multi="${f.name}"]`)) {
        const parent = f.options.find((o) => o.value === b.dataset.v)?.parent;
        const on = vals[f.name].includes(b.dataset.v), inherited = !!parent && vals[f.name].includes(parent);
        b.classList.toggle("on", on || inherited);
        b.disabled = inherited;
        b.firstElementChild.textContent = on || inherited ? "check_box" : "check_box_outline_blank";
      }
    };
    fields.filter((f) => f.type === "multi").forEach(ticks);
    form.addEventListener("click", (e) => {
      const mu = e.target.closest("[data-multi]");
      if (mu) {
        const f = fields.find((x) => x.name === mu.dataset.multi), v = mu.dataset.v;
        vals[f.name] = vals[f.name].includes(v) ? vals[f.name].filter((x) => x !== v)
          : [...vals[f.name].filter((x) => f.options.find((o) => o.value === x)?.parent !== v), v];  // ganzer Stack ersetzt einzelne Dienste
        ticks(f);
        visible();
      }
      const sw = e.target.closest("[data-sw]");
      if (sw) { vals[sw.dataset.sw] = !vals[sw.dataset.sw]; sw.classList.toggle("on", vals[sw.dataset.sw]); visible(); }
      const pick = e.target.closest("[data-pick]");
      if (pick) {
        const f = fields.find((x) => x.name === pick.dataset.pick);
        popMenu(pick, f.options.map((o) => ({ value: o.value, label: o.label, icon: o.icon, sub: o.sub, on: o.value === vals[f.name] })), (v) => {
          vals[f.name] = v;
          pick.querySelector("span").textContent = optLabel(f, v);
          visible();
        });
      }
      const seg = e.target.closest("[data-seg]");
      if (seg) {
        vals[seg.dataset.seg] = seg.dataset.v;
        form.querySelectorAll(`[data-seg="${seg.dataset.seg}"]`).forEach((b) => b.classList.toggle("on", b === seg));
        visible();
      }
    });
    form.addEventListener("input", (e) => {
      const name = e.target.dataset?.mfind;
      if (name) {
        const q = e.target.value.toLowerCase();
        for (const b of form.querySelectorAll(`[data-multi="${name}"]`)) b.hidden = !!q && !b.textContent.toLowerCase().includes(q);
      }
      visible();
    });
    form.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.dataset?.mfind != null) e.preventDefault(); });
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
    if (!matchMedia("(pointer: coarse)").matches) form.querySelector("input, textarea")?.focus(); // am Handy keine Tastatur sofort
  });
}

const optLabel = (f, v) => f.options.find((o) => o.value === v)?.label ?? "Auswählen";

export const lines = (text) => String(text || "").split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean);
