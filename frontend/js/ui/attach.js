// Files and pictures for the next message: 📎 button, paste (Ctrl+V a screenshot) or drag & drop onto the input.
// Every file is uploaded right away; sending only passes the ids. Big photos are made smaller first
// (the model takes pictures up to 5 MB – and phones take much bigger ones).
import { api } from "../api.js";
import { $, esc, toast } from "./dom.js";

let items = []; // { key, name, kind, url, id?, state: "up" | "ok" }
let seq = 0;
const MAX = 10;
const MAX_SIDE = 2048;

export const hasFiles = () => items.length > 0;
export const uploading = () => items.some((x) => x.state === "up");

// the files for sending: [{ id, name, kind }] – the list is empty afterwards
export function takeFiles() {
  const out = items.filter((x) => x.state === "ok").map(({ id, name, kind }) => ({ id, name, kind }));
  items.forEach((x) => x.url && URL.revokeObjectURL(x.url));
  items = [];
  render();
  return out;
}

async function shrink(file) {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) return file;
  const bmp = await createImageBitmap(file).catch(() => null);
  if (!bmp) return file;
  const scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
  if (scale === 1 && file.size < 1.5 * 1024 * 1024) return file;
  const c = document.createElement("canvas");
  c.width = Math.round(bmp.width * scale);
  c.height = Math.round(bmp.height * scale);
  c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
  const blob = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.85));
  return blob ? new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : file;
}

export async function addFiles(list) {
  for (const raw of [...list]) {
    if (items.length >= MAX) { toast(`Höchstens ${MAX} Dateien pro Nachricht`); break; }
    const image = raw.type.startsWith("image/");
    const it = { key: ++seq, name: raw.name || (image ? "bild.png" : "datei"), kind: image ? "image" : /pdf/.test(raw.type) ? "pdf" : "text",
      url: image ? URL.createObjectURL(raw) : null, state: "up" };
    items.push(it);
    render();
    try {
      const file = await shrink(raw);
      const fd = new FormData();
      fd.append("file", file, file.name || it.name);
      const r = await api.post("/uploads", fd);
      if (!items.includes(it)) { api.del(`/uploads/${r.id}`).catch(() => {}); continue; } // removed meanwhile
      Object.assign(it, { id: r.id, name: r.name, kind: r.kind, state: "ok" });
    } catch (e) {
      toast(e.message || "Hochladen fehlgeschlagen");
      items = items.filter((x) => x !== it);
    }
    render();
  }
}

function remove(key) {
  const it = items.find((x) => x.key === key);
  if (!it) return;
  items = items.filter((x) => x !== it);
  if (it.id) api.del(`/uploads/${it.id}`).catch(() => {});
  if (it.url) URL.revokeObjectURL(it.url);
  render();
}

const ICON = { pdf: "picture_as_pdf", text: "description", image: "image" };

function render() {
  const box = $("attList");
  box.hidden = !items.length;
  document.body.classList.toggle("has-files", items.length > 0);
  box.innerHTML = items.map((x) => `<span class="att ${x.state === "up" ? "up" : ""} ${x.url ? "pic" : ""}" title="${esc(x.name)}">
    ${x.url ? `<img src="${x.url}" alt="">` : `<span class="ms">${ICON[x.kind] || "description"}</span><span class="att-name">${esc(x.name)}</span>`}
    ${x.state === "up" ? `<span class="att-spin"></span>` : ""}
    <button type="button" data-rm="${x.key}" aria-label="${esc(x.name)} entfernen"><span class="ms">close</span></button></span>`).join("");
  document.dispatchEvent(new CustomEvent("attach:change"));
}

export function initAttach() {
  $("attachBtn").onclick = () => $("fileInput").click();
  $("fileInput").onchange = () => { addFiles($("fileInput").files); $("fileInput").value = ""; };
  $("attList").onclick = (e) => { const b = e.target.closest("[data-rm]"); if (b) remove(+b.dataset.rm); };
  // a screenshot from the clipboard
  $("input").addEventListener("paste", (e) => {
    const fs = [...(e.clipboardData?.files || [])];
    if (fs.length) { e.preventDefault(); addFiles(fs); }
  });
  const comp = $("composer");
  comp.addEventListener("dragover", (e) => { if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); comp.classList.add("drop"); } });
  comp.addEventListener("dragleave", () => comp.classList.remove("drop"));
  comp.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) { e.preventDefault(); addFiles(e.dataTransfer.files); } comp.classList.remove("drop"); });
  // the open chat changes: files waiting for the old one are dropped
  document.addEventListener("chat:switch", () => { items.forEach((x) => x.id && api.del(`/uploads/${x.id}`).catch(() => {})); items = []; render(); });
}
