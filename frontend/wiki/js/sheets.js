// Dialoge & Bottom-Sheets der Wiki-Oberfläche:
// Volltextsuche, Neue Seite (inkl. Vorlagen & Kategorien), Verschieben, Anhang-Vorschau,
// „Ida fragen“, Freigabe-Vorschläge (`privat/`), Einstellungen (Externe MCP-Zugänge, Papierkorb, Backups & ZIP-Export).

import * as api from "./api.js";
import { getAllPages } from "./db.js";
import { esc, formatBytes, renderMarkdown, renderUnifiedDiff } from "./markdown.js";

function openModal(html, { wide = false, onClose } = {}) {
  const backdrop = document.createElement("div");
  backdrop.className = "wk-backdrop";
  backdrop.innerHTML = `<div class="wk-sheet ${wide ? "wide" : ""}" role="dialog" aria-modal="true">${html}</div>`;
  const close = () => {
    backdrop.remove();
    onClose?.();
  };
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop || e.target.closest("[data-close-sheet]")) close();
  });
  const onKey = (e) => {
    if (e.key === "Escape") {
      window.removeEventListener("keydown", onKey);
      close();
    }
  };
  window.addEventListener("keydown", onKey);
  document.body.appendChild(backdrop);
  return { el: backdrop.querySelector(".wk-sheet"), close };
}

export function openSearchSheet({ initialTag = "", onNavigate }) {
  const { el, close } = openModal(
    `
    <div class="sheet-head">
      <div class="search-bar-modal">
        <span class="ms">search</span>
        <input type="search" id="wkSearchInput" placeholder="Im gesamten Wiki suchen (Titel, Text, #Tags) …" autocomplete="off">
      </div>
      <button type="button" class="icon-btn" data-close-sheet title="Schließen"><span class="ms">close</span></button>
    </div>
    <div class="filter-row" id="wkSearchSpaces">
      <button type="button" class="chip active" data-sp="">Alle Bereiche</button>
      <button type="button" class="chip" data-sp="privat">Privat</button>
      <button type="button" class="chip" data-sp="memory">KI-Gedächtnis</button>
      <button type="button" class="chip" data-sp="ki">KI &amp; Chats</button>
    </div>
    <div class="sheet-scroll" id="wkSearchResults"></div>
  `,
    { wide: true },
  );

  const input = el.querySelector("#wkSearchInput");
  const listEl = el.querySelector("#wkSearchResults");
  let activeSpace = "";
  let activeTag = initialTag;
  if (activeTag) input.value = `#${activeTag} `;

  let timer = 0;
  async function run() {
    const raw = input.value.trim();
    const tagMatch = raw.match(/(?:^|\s)#([\w\-äöüÄÖÜß]+)/);
    const tag = tagMatch ? tagMatch[1].toLowerCase() : "";
    const q = raw.replace(/(?:^|\s)#[\w\-äöüÄÖÜß]+/g, "").trim();

    let results = [];
    try {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (activeSpace) params.set("space", activeSpace);
      if (tag) params.set("tag", tag);
      const r = await api.get(`/api/v1/search?${params.toString()}`);
      results = r.results || [];
    } catch {
      // Offline-Fallback direkt über IndexedDB
      const all = await getAllPages();
      const needle = q.toLowerCase();
      results = all
        .filter((p) => (!activeSpace || p.space === activeSpace))
        .filter((p) => (!tag || (p.tags || []).includes(tag)))
        .filter((p) => !needle || p.title.toLowerCase().includes(needle) || (p.body || "").toLowerCase().includes(needle) || p.path.toLowerCase().includes(needle))
        .slice(0, 35)
        .map((p) => ({ id: p.id, space: p.space, path: p.path, title: p.title, snippet: esc(p.summary || (p.body || "").slice(0, 140)) }));
    }

    if (!results.length) {
      listEl.innerHTML = `<div class="empty-hint">Keine passenden Seiten gefunden.</div>`;
      return;
    }
    listEl.innerHTML = results
      .map(
        (r) => `
      <button type="button" class="search-hit" data-open-path="${esc(r.path)}">
        <div class="search-hit-top">
          <span class="space-pill sp-${esc(r.space)}">${esc(r.space)}</span>
          <strong>${esc(r.title)}</strong>
          <span class="muted-path">${esc(r.path)}</span>
        </div>
        ${r.snippet ? `<div class="search-snip">${sanitizeSnippet(r.snippet)}</div>` : ""}
      </button>`,
      )
      .join("");
  }

  function sanitizeSnippet(s) {
    // Erlaubt ausschließlich <mark>…</mark> aus SQLite FTS5
    return esc(String(s || ""))
      .replace(/&lt;mark&gt;/g, "<mark>")
      .replace(/&lt;\/mark&gt;/g, "</mark>");
  }

  el.querySelector("#wkSearchSpaces").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-sp]");
    if (!btn) return;
    el.querySelectorAll("#wkSearchSpaces .chip").forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    activeSpace = btn.dataset.sp || "";
    run();
  });

  listEl.addEventListener("click", (e) => {
    const hit = e.target.closest("[data-open-path]");
    if (!hit) return;
    close();
    onNavigate(hit.dataset.openPath);
  });

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(run, 110);
  });

  setTimeout(() => input.focus(), 20);
  run();
}

export function openNewPageSheet({ defaultSpace = "privat", defaultFolder = "", suggestedPath = "", folders = [], memoryCategories = [], onSave }) {
  const spaceInit = suggestedPath ? suggestedPath.split("/")[0] : (defaultSpace === "ki" ? "privat" : defaultSpace);
  const privatFolders = folders.filter((f) => f.startsWith("privat/"));
  const { el, close } = openModal(`
    <div class="sheet-head">
      <h2>Neue Wiki-Seite</h2>
      <button type="button" class="icon-btn" data-close-sheet><span class="ms">close</span></button>
    </div>
    <form id="wkNewForm" class="sheet-form">
      <div class="field-row">
        <label>Bereich</label>
        <div class="seg-group" id="wkNewSpace">
          <button type="button" class="seg ${spaceInit === "privat" ? "active" : ""}" data-val="privat"><span class="ms">home</span> Privat</button>
          <button type="button" class="seg ${spaceInit === "memory" ? "active" : ""}" data-val="memory"><span class="ms">auto_awesome</span> KI-Gedächtnis</button>
        </div>
      </div>
      <div class="field-row">
        <label for="wkNewFolder">Ordner / Kategorie</label>
        <select id="wkNewFolder" class="wk-input"></select>
      </div>
      <div class="field-row">
        <label for="wkNewTitle">Titel der Seite</label>
        <input id="wkNewTitle" class="wk-input" required maxlength="180" placeholder="z. B. Hausratversicherung oder IDA Architektur">
      </div>
      <div class="field-row" id="wkNewSummaryWrap">
        <label for="wkNewSummary">Kurz-Zusammenfassung (1 Satz)</label>
        <input id="wkNewSummary" class="wk-input" maxlength="220" placeholder="Erscheint in Übersichten und im KI-Gedächtnis-Index">
      </div>
      <div class="field-row" id="wkNewTplWrap">
        <label for="wkNewTpl">Start-Vorlage</label>
        <select id="wkNewTpl" class="wk-input">
          <option value="blank">Leere Seite mit Überschrift</option>
          <option value="notiz">Strukturierte Notiz</option>
          <option value="projekt">Projekt mit Checkliste</option>
        </select>
      </div>
      <div class="sheet-actions">
        <button type="button" class="btn-tonal" data-close-sheet>Abbrechen</button>
        <button type="submit" class="btn-primary"><span class="ms">add</span> Anlegen &amp; bearbeiten</button>
      </div>
    </form>
  `);

  let curSpace = spaceInit;
  const folderSel = el.querySelector("#wkNewFolder");
  const titleIn = el.querySelector("#wkNewTitle");

  function fillFolders() {
    if (curSpace === "memory") {
      const cats = memoryCategories.length ? memoryCategories : ["kern", "personen", "projekte", "konzepte-wissen", "entscheidungen", "agenten", "journal"];
      folderSel.innerHTML = cats.map((c) => `<option value="memory/${esc(c)}">memory/${esc(c)}</option>`).join("");
      if (defaultFolder.startsWith("memory/")) folderSel.value = defaultFolder;
    } else {
      const opts = ["privat/00-inbox", ...privatFolders.filter((f) => f !== "privat/00-inbox")];
      folderSel.innerHTML = opts.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("");
      if (defaultFolder.startsWith("privat/") && opts.includes(defaultFolder)) folderSel.value = defaultFolder;
    }
  }
  fillFolders();

  if (suggestedPath) {
    const parts = suggestedPath.split("/");
    const leaf = parts.pop() || "";
    const fld = parts.join("/");
    if ([...folderSel.options].some((o) => o.value === fld)) folderSel.value = fld;
    titleIn.value = leaf.replace(/-/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
  }

  el.querySelector("#wkNewSpace").addEventListener("click", (e) => {
    const b = e.target.closest("[data-val]");
    if (!b) return;
    curSpace = b.dataset.val;
    el.querySelectorAll("#wkNewSpace .seg").forEach((s) => s.classList.toggle("active", s === b));
    fillFolders();
  });

  el.querySelector("#wkNewForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = titleIn.value.trim();
    if (!title) return;
    const slug = title
      .toLowerCase()
      .replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "notiz";
    const path = suggestedPath || `${folderSel.value}/${slug}`;
    const tpl = el.querySelector("#wkNewTpl").value;
    let body = `# ${title}\n\n`;
    if (tpl === "notiz") {
      body = `# ${title}\n\n## Kernpunkte\n\n- \n\n## Verweise\n\n- [[privat/_start|Startseite]]\n`;
    } else if (tpl === "projekt") {
      body = `# ${title}\n\n> [!info] Ziel & Kontext\n> Kurzbeschreibung des Vorhabens.\n\n## Aufgaben\n\n- [ ] Erster Schritt\n- [ ] Unterlagen verknüpfen\n`;
    }
    const summary = el.querySelector("#wkNewSummary").value.trim();
    close();
    await onSave({ path, title, body, frontmatter: summary ? { summary } : {} });
  });

  setTimeout(() => titleIn.focus(), 20);
}

export function openMoveSheet({ page, onMove }) {
  const { el, close } = openModal(`
    <div class="sheet-head">
      <h2>Seite verschieben oder umbenennen</h2>
      <button type="button" class="icon-btn" data-close-sheet><span class="ms">close</span></button>
    </div>
    <form id="wkMoveForm" class="sheet-form">
      <p class="sheet-hint"><span class="ms">link</span> Alle eingehenden <code>[[Wikilinks]]</code> in anderen Seiten werden automatisch auf den neuen Pfad aktualisiert.</p>
      <div class="field-row">
        <label for="wkMoveTitle">Neuer Titel</label>
        <input id="wkMoveTitle" class="wk-input" value="${esc(page.title)}" required>
      </div>
      <div class="field-row">
        <label for="wkMovePath">Neuer Pfad (ohne <code>.md</code>)</label>
        <input id="wkMovePath" class="wk-input" value="${esc(page.path)}" required>
      </div>
      <div class="sheet-actions">
        <button type="button" class="btn-tonal" data-close-sheet>Abbrechen</button>
        <button type="submit" class="btn-primary"><span class="ms">save</span> Verschieben</button>
      </div>
    </form>
  `);
  el.querySelector("#wkMoveForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const toPath = el.querySelector("#wkMovePath").value.trim();
    const title = el.querySelector("#wkMoveTitle").value.trim();
    close();
    await onMove(toPath, title);
  });
}

export function openAttachmentSheet({ pageId, attachment, canEdit, onDelete }) {
  const url = api.apiUrl(attachment.url);
  const dlUrl = `${url}?download=1`;
  let preview = `<div class="att-fallback"><span class="ms">attach_file</span><p>${esc(attachment.name)} (${formatBytes(attachment.size)})</p></div>`;
  if (attachment.kind === "image") {
    preview = `<div class="att-img-box"><img src="${esc(url)}" alt="${esc(attachment.name)}"></div>`;
  } else if (attachment.kind === "pdf") {
    preview = `<iframe class="att-pdf-frame" src="${esc(url)}" title="${esc(attachment.name)}"></iframe>`;
  } else if (attachment.kind === "audio") {
    preview = `<div class="att-fallback"><audio controls src="${esc(url)}"></audio></div>`;
  } else if (attachment.kind === "video") {
    preview = `<div class="att-img-box"><video controls src="${esc(url)}"></video></div>`;
  }

  const { el, close } = openModal(
    `
    <div class="sheet-head">
      <div>
        <h2>${esc(attachment.name)}</h2>
        <small class="muted-path">${esc(attachment.mime)} · ${formatBytes(attachment.size)}</small>
      </div>
      <button type="button" class="icon-btn" data-close-sheet><span class="ms">close</span></button>
    </div>
    <div class="sheet-scroll">${preview}</div>
    <div class="sheet-actions">
      ${canEdit ? `<button type="button" class="btn-danger" id="wkDelAtt"><span class="ms">delete</span> Entfernen</button>` : ""}
      <a class="btn-primary" href="${esc(dlUrl)}" download="${esc(attachment.name)}"><span class="ms">download</span> Herunterladen</a>
    </div>
  `,
    { wide: true },
  );

  el.querySelector("#wkDelAtt")?.addEventListener("click", async () => {
    close();
    await onDelete(pageId, attachment.name);
  });
}

export function openAskIdaSheet({ page, toast }) {
  const { el } = openModal(
    `
    <div class="sheet-head">
      <h2><span class="ms">auto_awesome</span> Ida zu dieser Seite fragen</h2>
      <button type="button" class="icon-btn" data-close-sheet><span class="ms">close</span></button>
    </div>
    <form id="wkAskForm" class="sheet-form">
      ${page ? `<div class="context-pill"><span class="ms">description</span> Kontext: <strong>${esc(page.title)}</strong> (<code>${esc(page.path)}</code>)</div>` : ""}
      <div class="field-row">
        <textarea id="wkAskInput" class="wk-input" rows="3" required placeholder="z. B. Fasse die wichtigsten Punkte zusammen oder ergänze fehlende Fragen …"></textarea>
      </div>
      <div class="sheet-actions">
        <button type="submit" class="btn-primary" id="wkAskBtn"><span class="ms">send</span> Ida fragen</button>
      </div>
    </form>
    <div id="wkAskAnswer" class="ask-answer" hidden></div>
  `,
    { wide: true },
  );

  const form = el.querySelector("#wkAskForm");
  const ansBox = el.querySelector("#wkAskAnswer");
  const btn = el.querySelector("#wkAskBtn");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = el.querySelector("#wkAskInput").value.trim();
    if (!q) return;
    btn.disabled = true;
    ansBox.hidden = false;
    ansBox.innerHTML = `<div class="loading-row"><span class="ms spin">progress_activity</span> Ida denkt nach …</div>`;
    try {
      const res = await api.post("/api/v1/ask", {
        question: q,
        page_path: page?.path || null,
        page_title: page?.title || null,
        page_excerpt: (page?.body || "").slice(0, 3800),
      });
      ansBox.innerHTML = `<div class="md-prose">${renderMarkdown(res.answer || "")}</div>`;
    } catch (err) {
      ansBox.innerHTML = `<div class="err-box">${esc(err.message)}</div>`;
      toast?.(err.message);
    } finally {
      btn.disabled = false;
    }
  });
  setTimeout(() => el.querySelector("#wkAskInput").focus(), 20);
}

export async function openProposalsSheet({ onResolved, toast }) {
  const { el } = openModal(
    `
    <div class="sheet-head">
      <h2><span class="ms">shield</span> Offene Freigaben für <code>privat/</code></h2>
      <button type="button" class="icon-btn" data-close-sheet><span class="ms">close</span></button>
    </div>
    <p class="sheet-hint">Änderungsvorschläge von IDA-Agenten an deinem persönlichen Bereich. Nichts wird ohne deine Zustimmung übernommen.</p>
    <div class="sheet-scroll" id="wkPropsList"><div class="empty-hint">Lade Vorschläge …</div></div>
  `,
    { wide: true },
  );

  const box = el.querySelector("#wkPropsList");
  async function load() {
    const r = await api.get("/api/v1/proposals?status=pending");
    const list = r.proposals || [];
    if (!list.length) {
      box.innerHTML = `<div class="empty-hint">Keine offenen Freigabe-Anfragen vorhanden.</div>`;
      return;
    }
    box.innerHTML = list
      .map(
        (p) => `
      <div class="prop-card" data-prop-id="${esc(p.id)}">
        <div class="prop-head">
          <span class="space-pill sp-memory">Agent: ${esc(p.agent_id)}</span>
          <strong>${esc(p.summary)}</strong>
          <code>${esc(p.target_path)}</code>
        </div>
        ${renderUnifiedDiff(p.diff_preview)}
        <div class="prop-actions">
          <button type="button" class="btn-tonal" data-reject="${esc(p.id)}">Ablehnen</button>
          <button type="button" class="btn-primary" data-approve="${esc(p.id)}"><span class="ms">check</span> Erlauben &amp; speichern</button>
        </div>
      </div>`,
      )
      .join("");
  }

  box.addEventListener("click", async (e) => {
    const appBtn = e.target.closest("[data-approve]");
    const rejBtn = e.target.closest("[data-reject]");
    try {
      if (appBtn) {
        await api.post(`/api/v1/proposals/${encodeURIComponent(appBtn.dataset.approve)}/approve`, {});
        toast?.("Änderung freigegeben und im Wiki gespeichert.");
      } else if (rejBtn) {
        await api.post(`/api/v1/proposals/${encodeURIComponent(rejBtn.dataset.reject)}/reject`, {});
        toast?.("Vorschlag abgelehnt.");
      } else {
        return;
      }
      await load();
      onResolved?.();
    } catch (err) {
      toast?.(err.message);
    }
  });

  await load();
}


// Einstellungen wie im Kalender: eine übersichtliche Liste, jeder Eintrag öffnet eine eigene Unterseite
export async function openSettingsSheet({ meta, installPrompt, onRefresh, toast }) {
  const mcpUrl = `${(meta?.public_url || location.origin).replace(/\/+$/, "")}/mcp`;
  const { el } = openModal(`<div class="set-view" id="wkSetView"></div>`);
  const view = el.querySelector("#wkSetView");
  let data = { tokens: [], trash: [], backups: [] };
  let createdTok = "";

  async function loadData() {
    const [tokRes, trashRes, bkpRes] = await Promise.all([
      api.get("/api/v1/mcp-tokens").catch(() => ({ tokens: [] })),
      api.get("/api/v1/trash").catch(() => ({ items: [] })),
      api.get("/api/v1/backups").catch(() => ({ backups: [] })),
    ]);
    data = { tokens: tokRes.tokens || [], trash: trashRes.items || [], backups: bkpRes.backups || [] };
  }

  const head = (title, back) => `
    <div class="sheet-head set-head">
      ${back ? `<button type="button" class="icon-btn" data-set-back title="Zurück"><span class="ms">arrow_back</span></button>` : ""}
      <h2>${title}</h2>
      <button type="button" class="icon-btn" data-close-sheet title="Schließen"><span class="ms">close</span></button>
    </div>`;

  const item = (go, icon, title, sub = "") => `
    <button type="button" class="set-li" data-set-go="${go}">
      <span class="ms">${icon}</span>
      <div class="ct"><span>${title}</span>${sub ? `<small>${sub}</small>` : ""}</div>
      <span class="ms">chevron_right</span>
    </button>`;

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const empty = (text) => `<div class="muted-path set-empty">${text}</div>`;

  const pages = {
    home() {
      const lastBackup = data.backups[0];
      return `
        ${head("Einstellungen")}
        <div class="sheet-scroll">
          <div class="set-list">
            ${item("mcp", "key", "Externe KI-Anbindung (MCP)", data.tokens.length ? plural(data.tokens.length, "Zugang", "Zugänge") : "Noch kein Zugang angelegt")}
          </div>
          <div class="set-gl">Daten</div>
          <div class="set-list">
            ${item("trash", "delete", "Papierkorb", data.trash.length ? plural(data.trash.length, "Seite", "Seiten") : "Leer")}
            ${item("backup", "backup", "Sicherungen und Export", lastBackup ? `Letzte: ${esc(lastBackup.name)}` : "Noch keine Sicherung")}
          </div>
          ${
            installPrompt
              ? `<div class="set-gl">App</div>
                 <div class="set-list">
                   <button type="button" class="set-li" id="wkInstallPwa"><span class="ms">install_mobile</span><div class="ct"><span>App installieren</span></div></button>
                 </div>`
              : ""
          }
        </div>`;
    },

    mcp() {
      return `
        ${head("Externe KI-Anbindung", true)}
        <div class="sheet-scroll">
          <p class="sheet-hint">Verbinde Claude Code, Cursor oder Desktop-KIs direkt mit deinem gemeinsamen <code>memory/</code>-Gedächtnis. Der Bereich <code>privat/</code> ist über MCP technisch <strong>strikt schreibgeschützt</strong>.</p>
          <div class="set-gl">Adresse</div>
          <div class="mcp-endpoint-box">
            <code>${esc(mcpUrl)}</code>
            <button type="button" class="btn-tonal sm" id="wkCopyMcpUrl"><span class="ms">content_copy</span> Kopieren</button>
          </div>
          <div class="set-gl">Neuer Zugang</div>
          <form id="wkNewTokForm" class="tok-form">
            <input id="wkTokName" class="wk-input" placeholder="Name des Geräts / Clients (z. B. Claude Code Laptop)" required maxlength="50">
            <div class="tok-checks">
              <label><input type="checkbox" id="wkTokPriv" checked> <code>privat/</code> lesbar</label>
              <label><input type="checkbox" id="wkTokKi" checked> <code>ki/</code> Chats lesbar</label>
            </div>
            <button type="submit" class="btn-primary sm"><span class="ms">add</span> Token erzeugen</button>
          </form>
          ${createdTok}
          <div class="set-gl">Angelegte Zugänge</div>
          ${
            data.tokens.length
              ? `<div class="set-list">${data.tokens
                  .map(
                    (t) => `
                <div class="set-li static">
                  <span class="ms">vpn_key</span>
                  <div class="ct"><span>${esc(t.name)} <code>${esc(t.prefix)}…</code></span>
                  <small>memory: Lesen + Schreiben · privat: ${t.allow_read_private ? "Lesen" : "Aus"} · ki: ${t.allow_read_chats ? "Lesen" : "Aus"}</small></div>
                  <button type="button" class="icon-btn" data-del-tok="${esc(t.id)}" title="Widerrufen"><span class="ms">delete</span></button>
                </div>`,
                  )
                  .join("")}</div>`
              : empty("Noch keine benannten MCP-Tokens angelegt.")
          }
        </div>`;
    },

    trash() {
      return `
        ${head("Papierkorb", true)}
        <div class="sheet-scroll">
          <p class="sheet-hint">Gelöschte Seiten bleiben 30 Tage hier und lassen sich mit einem Klick zurückholen.</p>
          ${
            data.trash.length
              ? `<div class="set-list">${data.trash
                  .map(
                    (p) => `
                <div class="set-li static">
                  <span class="ms">description</span>
                  <div class="ct"><span>${esc(p.title)}</span><small>${esc(p.path)}</small></div>
                  <button type="button" class="btn-tonal sm" data-restore-trash="${esc(p.id)}"><span class="ms">undo</span> <span class="hide-xs">Wiederherstellen</span></button>
                </div>`,
                  )
                  .join("")}</div>`
              : empty("Der Papierkorb ist leer.")
          }
        </div>`;
    },

    backup() {
      return `
        ${head("Sicherungen und Export", true)}
        <div class="sheet-scroll">
          <p class="sheet-hint">Exportiert alle echten Markdown-Dateien (<code>vault/</code>) und Anhänge (<code>files/</code>) als portables ZIP-Archiv – direkt in Obsidian öffnbar.</p>
          <div class="set-list">
            <a class="set-li" href="${esc(api.apiUrl("/api/v1/export.zip"))}"><span class="ms">download</span><div class="ct"><span>Vault als ZIP herunterladen</span><small>Obsidian-Export</small></div></a>
            <button type="button" class="set-li" id="wkRunBackup"><span class="ms">backup</span><div class="ct"><span>Server-Backup jetzt anlegen</span></div></button>
          </div>
          <div class="set-gl">Letzte Sicherungen</div>
          ${
            data.backups.length
              ? `<div class="set-list">${data.backups
                  .slice(0, 5)
                  .map(
                    (b) => `
                <div class="set-li static">
                  <span class="ms">inventory_2</span>
                  <div class="ct"><span><code>${esc(b.name)}</code></span><small>${formatBytes(b.size)}</small></div>
                </div>`,
                  )
                  .join("")}</div>`
              : empty("Noch keine Sicherung vorhanden.")
          }
        </div>`;
    },
  };

  let current = "home";
  function show(name, dir = "") {
    current = name;
    view.innerHTML = pages[name]();
    view.classList.remove("fwd", "back");
    if (dir) {
      void view.offsetWidth; // Animation neu starten
      view.classList.add(dir);
    }
  }

  async function reload() {
    await loadData();
    if (view.isConnected) show(current);
  }

  el.addEventListener("click", async (e) => {
    const go = e.target.closest("[data-set-go]");
    if (go) {
      createdTok = "";
      show(go.dataset.setGo, "fwd");
      return;
    }
    if (e.target.closest("[data-set-back]")) {
      show("home", "back");
      return;
    }
    if (e.target.closest("#wkCopyMcpUrl")) {
      navigator.clipboard?.writeText(mcpUrl);
      toast?.("MCP-URL kopiert.");
      return;
    }
    if (e.target.closest("#wkInstallPwa")) {
      installPrompt?.prompt?.();
      return;
    }
    const delTok = e.target.closest("[data-del-tok]");
    if (delTok) {
      try {
        await api.del(`/api/v1/mcp-tokens/${encodeURIComponent(delTok.dataset.delTok)}`);
        toast?.("MCP-Token widerrufen.");
      } catch (err) {
        toast?.(err.message);
      }
      await reload();
      return;
    }
    const rest = e.target.closest("[data-restore-trash]");
    if (rest) {
      try {
        await api.post(`/api/v1/trash/${encodeURIComponent(rest.dataset.restoreTrash)}/restore`, {});
        toast?.("Seite wiederhergestellt.");
        onRefresh?.();
      } catch (err) {
        toast?.(err.message);
      }
      await reload();
      return;
    }
    if (e.target.closest("#wkRunBackup")) {
      try {
        await api.post("/api/v1/backups", {});
        toast?.("Vollständige Sicherung erstellt.");
      } catch (err) {
        toast?.(err.message);
      }
      await reload();
    }
  });

  el.addEventListener("submit", async (e) => {
    if (e.target.id !== "wkNewTokForm") return;
    e.preventDefault();
    const name = el.querySelector("#wkTokName").value.trim();
    if (!name) return;
    try {
      const created = await api.post("/api/v1/mcp-tokens", {
        name,
        allow_read_private: el.querySelector("#wkTokPriv").checked,
        allow_read_chats: el.querySelector("#wkTokKi").checked,
      });
      const snippet = JSON.stringify(
        { mcpServers: { "ida-wiki": { type: "http", url: mcpUrl, headers: { Authorization: `Bearer ${created.token}` } } } },
        null,
        2,
      );
      createdTok = `
        <div class="created-tok">
          <div><strong>Einmalig angezeigtes Token für „${esc(created.name)}“:</strong></div>
          <code>${esc(created.token)}</code>
          <pre>${esc(snippet)}</pre>
        </div>`;
    } catch (err) {
      toast?.(err.message);
    }
    await reload();
  });

  show("home");
  await reload();
}
