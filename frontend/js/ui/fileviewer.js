// File Explorer & Preview: browse project directories, inspect source code,
// and preview files (live interactive HTML preview with mobile/desktop device switcher, Markdown preview, images).
import { api } from "../api.js";
import { $, attempt, closeSheet, esc, fmtRel, md, openSheet, toast } from "./dom.js";

export function fmtBytes(bytes) {
  if (bytes == null || isNaN(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function fileIcon(name, isDir) {
  if (isDir) return ["folder", "ic-folder"];
  const ext = (name.split(".").pop() || "").toLowerCase();
  switch (ext) {
    case "html": case "htm": return ["html", "ic-html"];
    case "js": case "mjs": case "cjs": case "ts": case "tsx": case "jsx": return ["code", "ic-js"];
    case "py": return ["terminal", "ic-py"];
    case "css": case "scss": case "sass": case "less": return ["css", "ic-css"];
    case "json": case "toml": case "yaml": case "yml": case "ini": case "cfg": return ["data_object", "ic-data"];
    case "md": case "markdown": case "txt": case "rst": return ["description", "ic-doc"];
    case "png": case "jpg": case "jpeg": case "gif": case "svg": case "webp": case "ico": return ["image", "ic-img"];
    case "sh": case "bash": case "bat": case "ps1": return ["terminal", "ic-sh"];
    case "sql": return ["database", "ic-data"];
    default: return ["draft", "ic-file"];
  }
}

function codeWithLineNumbers(src) {
  if (!src) return `<span class="empty-file">(Leere Datei)</span>`;
  const lines = src.split("\n");
  return lines.map((line, idx) => `<span class="line"><span class="ln">${idx + 1}</span><span class="lc">${esc(line) || " "}</span></span>`).join("\n");
}

export function mountFileExplorer(container, projectId, { initialPath = "" } = {}) {
  let curPath = initialPath;
  let curFile = null;
  let curDirData = null;
  let q = "";
  let htmlTab = "render"; // "render" | "code"
  let mdTab = "render";   // "render" | "code"
  let device = "full";    // "full" | "tablet" | "mobile"

  const rawUrl = (p) => `/api/v1/code/projects/${projectId}/raw/${encodeURI(p.replace(/\\/g, "/"))}`;

  async function loadDir(path = "") {
    curFile = null;
    curPath = path;
    container.innerHTML = `<div class="fe-loading"><span class="ms spin">progress_activity</span>Lade Dateien …</div>`;
    try {
      curDirData = await api.get(`/code/projects/${projectId}/files?path=${encodeURIComponent(path)}`);
      drawDir();
    } catch (e) {
      container.innerHTML = `<div class="fe-box"><div class="ferr">${esc(e.message)}</div><button class="es-btn" id="feRetry"><span class="ms">refresh</span>Erneut versuchen</button></div>`;
      $("feRetry").onclick = () => loadDir(path);
    }
  }

  function drawDir() {
    if (!curDirData) return;
    const parts = curPath ? curPath.split("/").filter(Boolean) : [];
    let crumbHtml = `<button type="button" class="fe-crumb ${parts.length === 0 ? "active" : ""}" data-cd=""><span class="ms">folder</span>Root</button>`;
    let accum = "";
    for (let i = 0; i < parts.length; i++) {
      accum += (accum ? "/" : "") + parts[i];
      const isLast = i === parts.length - 1;
      crumbHtml += `<span class="fe-sep">/</span><button type="button" class="fe-crumb ${isLast ? "active" : ""}" data-cd="${esc(accum)}">${esc(parts[i])}</button>`;
    }

    const filterVal = q.toLowerCase().trim();
    const entries = curDirData.entries.filter((e) => !filterVal || e.name.toLowerCase().includes(filterVal));

    container.innerHTML = `<div class="fe-box">
      <div class="fe-bar">
        <div class="fe-crumbs">${crumbHtml}</div>
        <div class="fe-actions">
          ${curDirData.parent !== null ? `<button type="button" class="icon-btn" id="feUp" title="Ebene nach oben"><span class="ms">arrow_upward</span></button>` : ""}
          <button type="button" class="icon-btn" id="feRefresh" title="Aktualisieren"><span class="ms">refresh</span></button>
        </div>
      </div>
      <div class="fe-search-wrap">
        <span class="ms">search</span>
        <input type="search" id="feSearch" placeholder="Dateien in diesem Ordner filtern …" value="${esc(q)}">
      </div>
      <div class="fe-list">
        ${entries.length ? entries.map((e) => {
          const [ic, cls] = fileIcon(e.name, e.is_dir);
          const isHtml = !e.is_dir && (e.ext === ".html" || e.ext === ".htm");
          return `<div class="fe-row ${e.is_dir ? "is-dir" : "is-file"}" data-path="${esc(e.path)}" data-dir="${e.is_dir ? "1" : "0"}">
            <span class="fe-ic ${cls}"><span class="ms">${ic}</span></span>
            <span class="fe-main">
              <span class="fe-name">${esc(e.name)}</span>
              <small class="fe-meta">${e.is_dir ? "Ordner" : `${fmtBytes(e.size)}${e.modified_at ? ` · ${fmtRel(e.modified_at)}` : ""}`}</small>
            </span>
            ${isHtml ? `<span class="preview-badge" title="Direkte HTML-Vorschau verf\u00fcgbar"><span class="ms">visibility</span>Vorschau</span>` : ""}
            <span class="ms fe-arrow">${e.is_dir ? "chevron_right" : "visibility"}</span>
          </div>`;
        }).join("") : `<div class="fe-empty">${q ? "Keine Treffer f\u00fcr deine Suche." : "Dieser Ordner ist leer."}</div>`}
      </div>
    </div>`;

    container.querySelectorAll("[data-cd]").forEach((b) => (b.onclick = () => loadDir(b.dataset.cd)));
    if ($("feUp")) $("feUp").onclick = () => loadDir(curDirData.parent || "");
    if ($("feRefresh")) $("feRefresh").onclick = () => loadDir(curPath);

    const sInp = $("feSearch");
    if (sInp) {
      sInp.oninput = () => { q = sInp.value; drawDir(); };
    }

    container.querySelectorAll(".fe-row").forEach((row) => {
      row.onclick = () => {
        const isDir = row.dataset.dir === "1";
        const path = row.dataset.path;
        if (isDir) loadDir(path);
        else loadFile(path);
      };
    });
  }

  async function loadFile(filePath) {
    curFile = filePath;
    container.innerHTML = `<div class="fe-loading"><span class="ms spin">progress_activity</span>Lade ${esc(filePath.split("/").pop())} …</div>`;
    try {
      const data = await api.get(`/code/projects/${projectId}/file?path=${encodeURIComponent(filePath)}`);
      drawFile(data);
    } catch (e) {
      container.innerHTML = `<div class="fe-box"><div class="ferr">${esc(e.message)}</div><button class="es-btn" id="feBack"><span class="ms">arrow_back</span>Zur\u00fcck</button></div>`;
      $("feBack").onclick = () => loadDir(curPath);
    }
  }

  function drawFile(data) {
    const isHtml = data.ext === ".html" || data.ext === ".htm";
    const isMd = data.ext === ".md" || data.ext === ".markdown";
    const isImg = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"].includes(data.ext);
    const raw = rawUrl(data.path);
    if (isHtml) document.dispatchEvent(new CustomEvent("fe:html", { detail: { projectId, path: data.path } }));

    let topActions = "";
    if (isHtml) {
      topActions = `<div class="seg seg-mini" id="feHtmlSeg">
        <button type="button" class="${htmlTab === "render" ? "on" : ""}" data-ht="render"><span class="ms">visibility</span>Vorschau</button>
        <button type="button" class="${htmlTab === "code" ? "on" : ""}" data-ht="code"><span class="ms">code</span>Code</button>
      </div>
      ${htmlTab === "render" ? `<div class="device-toggles">
        <button type="button" class="icon-btn ib-sm ${device === "full" ? "on" : ""}" data-dev="full" title="Volle Breite (Desktop)"><span class="ms">laptop</span></button>
        <button type="button" class="icon-btn ib-sm ${device === "tablet" ? "on" : ""}" data-dev="tablet" title="Tablet (768px)"><span class="ms">tablet</span></button>
        <button type="button" class="icon-btn ib-sm ${device === "mobile" ? "on" : ""}" data-dev="mobile" title="Handy (375px)"><span class="ms">smartphone</span></button>
      </div>` : ""}
      <button type="button" class="icon-btn ib-sm" id="feReloadIframe" title="Vorschau neu laden"><span class="ms">refresh</span></button>`;
    } else if (isMd) {
      topActions = `<div class="seg seg-mini" id="feMdSeg">
        <button type="button" class="${mdTab === "render" ? "on" : ""}" data-md="render"><span class="ms">visibility</span>Vorschau</button>
        <button type="button" class="${mdTab === "code" ? "on" : ""}" data-md="code"><span class="ms">code</span>Quelltext</button>
      </div>`;
    }

    if (data.is_text) {
      topActions += `<button type="button" class="icon-btn ib-sm" id="feCopy" title="Inhalt kopieren"><span class="ms">content_copy</span></button>`;
    }
    topActions += `<a class="icon-btn ib-sm" href="${raw}" target="_blank" rel="noopener" title="In neuem Tab \u00f6ffnen"><span class="ms">open_in_new</span></a>`;

    let bodyHtml = "";
    if (isHtml && htmlTab === "render") {
      bodyHtml = `<div class="preview-frame-wrap device-${device}">
        <iframe class="preview-frame" id="feIframe" src="${raw}" sandbox="allow-scripts allow-forms allow-popups"></iframe>
      </div>`;
    } else if (isMd && mdTab === "render") {
      bodyHtml = `<div class="preview-md-box">${md(data.content || "")}</div>`;
    } else if (isImg) {
      bodyHtml = `<div class="preview-img-box"><img src="${raw}" alt="${esc(data.name)}"></div>`;
    } else if (data.is_text) {
      bodyHtml = `<div class="code-view">${data.truncated ? `<p class="hint">Datei ist sehr gro\u00df (\u00fcber 2 MB) \u2013 Vorschau wurde gek\u00fcrzt.</p>` : ""}<pre class="code-pre"><code>${codeWithLineNumbers(data.content)}</code></pre></div>`;
    } else {
      bodyHtml = `<div class="fe-binary">
        <span class="ms bin-ic">inventory_2</span>
        <b>${esc(data.name)}</b>
        <small>Bin\u00e4rdatei \u00b7 ${fmtBytes(data.size)} \u00b7 ${esc(data.mime || "application/octet-stream")}</small>
        <a class="es-btn primary" href="${raw}" download="${esc(data.name)}"><span class="ms">download</span>Datei herunterladen</a>
      </div>`;
    }

    container.innerHTML = `<div class="fe-box preview-mode">
      <div class="preview-header">
        <button type="button" class="icon-btn back" id="feBackToList" title="Zur\u00fcck zum Ordner"><span class="ms">arrow_back</span></button>
        <div class="pv-meta">
          <span class="pv-title" title="${esc(data.path)}">${esc(data.name)}</span>
          <small>${esc(data.path)} \u00b7 ${fmtBytes(data.size)}</small>
        </div>
        <div class="pv-actions">${topActions}</div>
      </div>
      <div class="preview-body">${bodyHtml}</div>
    </div>`;

    $("feBackToList").onclick = () => loadDir(curPath);

    if (isHtml) {
      container.querySelectorAll("[data-ht]").forEach((b) => (b.onclick = () => { htmlTab = b.dataset.ht; drawFile(data); }));
      container.querySelectorAll("[data-dev]").forEach((b) => (b.onclick = () => { device = b.dataset.dev; drawFile(data); }));
      const relBtn = $("feReloadIframe");
      if (relBtn) {
        relBtn.onclick = () => {
          const ifr = $("feIframe");
          if (ifr) ifr.src = raw + (raw.includes("?") ? "&" : "?") + `_r=${Date.now()}`;
        };
      }
    }

    if (isMd) {
      container.querySelectorAll("[data-md]").forEach((b) => (b.onclick = () => { mdTab = b.dataset.md; drawFile(data); }));
    }

    const copyBtn = $("feCopy");
    if (copyBtn && data.content) {
      copyBtn.onclick = async () => {
        try {
          await navigator.clipboard.writeText(data.content);
          toast("In die Zwischenablage kopiert");
        } catch {
          toast("Kopieren nicht m\u00f6glich");
        }
      };
    }
  }

  loadDir(initialPath);

  return {
    reload: () => (curFile ? loadFile(curFile) : loadDir(curPath)),
    openPath: (p) => loadDir(p),
    openFile: (f) => loadFile(f),
  };
}

export function openFileSheet(projectId, initialPath = "") {
  const s = openSheet(`<div class="handle"></div>
    <div class="cs-head">
      <h2 class="cs-title">Dateien & Vorschau</h2>
      <button class="icon-btn" data-x aria-label="Schlie\u00dfen"><span class="ms">close</span></button>
    </div>
    <div id="feSheetMount"></div>`, "code-sheet file-sheet");
  s.querySelector("[data-x]").onclick = closeSheet;
  mountFileExplorer($("feSheetMount"), projectId, { initialPath });
}
