// Hauptoberfläche des IDA Wiki (Android-17 / Material 3 Expressive):
// - 3 getrennte Bereiche (`privat/`, `memory/`, `ki/`) mit aufklappbarem Lebensordner-Baum & #Tags
// - Lese- & Split-Live-Editor mit `[[`-Autocomplete, Drag-&-Drop-/Clipboard-Upload, Backlinks & lokalem Mini-Graph
// - Vollbild-Wissensgraph (`?ansicht=graph`)
// - Lückenloser Wiki-Verlauf (`?ansicht=verlauf`) mit Diff-Vergleich & 1-Klick-Wiederherstellung.

import * as api from "./api.js";
import { getAllPages, getMeta, getPage, putPages } from "./db.js";
import { mountGraph } from "./graph.js";
import { buildPageIndex, esc, formatBytes, renderMarkdown, renderUnifiedDiff, toggleCheckboxAtLine } from "./markdown.js";
import * as outbox from "./outbox.js";
import {
  openAskIdaSheet,
  openAttachmentSheet,
  openMoveSheet,
  openNewPageSheet,
  openProposalsSheet,
  openSearchSheet,
  openSettingsSheet,
} from "./sheets.js";
import * as sync from "./sync.js";

const SPACE_META = {
  privat: { label: "Privat", icon: "home", desc: "Persönlicher Lebensspeicher" },
  memory: { label: "Gedächtnis", icon: "auto_awesome", desc: "Strukturiertes KI-Gedächtnis" },
  ki: { label: "KI & Chats", icon: "forum", desc: "IDA-Agenten, Chats & Code" },
};

export async function startWikiApp(rootEl) {
  const state = {
    view: "page", // "page" | "graph" | "history"
    space: "privat",
    currentPath: "privat/_start",
    currentPage: null,
    editing: false,
    drawerOpen: false,
    pageRevsOpen: false,
    pages: [],
    folders: [],
    tags: [],
    meta: {},
    pendingProposals: 0,
    outboxSummary: { pending: 0, conflict: 0 },
    online: typeof navigator !== "undefined" ? navigator.onLine : true,
    expandedFolders: new Set(["privat/00-inbox", "privat/04-arbeit-projekte", "memory/kern", "memory/projekte"]),
    graphFilterSpace: "",
    graphDepth: 2,
    graphLocalCenter: "",
    historySpace: "",
    historyOnlyAi: false,
    selectedRevDetail: null,
    installPrompt: null,
  };

  let activeGraphCtrl = null;
  let activeMiniGraphCtrl = null;

  const toastEl = document.getElementById("toast");
  let toastTimer = 0;
  function toast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 3200);
  }

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    state.installPrompt = e;
  });

  function readRouteFromUrl() {
    const u = new URL(location.href);
    const ansicht = u.searchParams.get("ansicht");
    const seite = u.searchParams.get("seite");
    if (ansicht === "graph") state.view = "graph";
    else if (ansicht === "verlauf") state.view = "history";
    else state.view = "page";
    if (seite) {
      state.currentPath = seite;
      const sp = seite.split("/")[0];
      if (SPACE_META[sp]) state.space = sp;
    }
  }

  function pushUrl() {
    const u = new URL(location.href);
    u.searchParams.delete("neu");
    if (state.view === "graph") {
      u.searchParams.set("ansicht", "graph");
    } else if (state.view === "history") {
      u.searchParams.set("ansicht", "verlauf");
    } else {
      u.searchParams.delete("ansicht");
      if (state.currentPath) u.searchParams.set("seite", state.currentPath);
    }
    history.replaceState(null, "", u.pathname + u.search);
  }

  async function refreshLocalState() {
    state.pages = await getAllPages();
    state.folders = (await getMeta("folders", [])) || [];
    state.pendingProposals = (await getMeta("pending_proposals", 0)) || 0;
    state.outboxSummary = await outbox.countSummary();

    const tagCounts = new Map();
    for (const p of state.pages) {
      for (const t of p.tags || []) tagCounts.set(t, (tagCounts.get(t) || 0) + 1);
    }
    state.tags = [...tagCounts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 18)
      .map(([tag, count]) => ({ tag, count }));
  }

  async function loadPage(ref, { switchSpace = true } = {}) {
    state.view = "page";
    state.editing = false;
    state.pageRevsOpen = false;
    let pg = await getPage(ref);
    try {
      pg = await api.get(`/api/v1/page?ref=${encodeURIComponent(ref)}`);
      await putPages([pg]);
    } catch (err) {
      if (!pg) {
        toast(err.message);
        return;
      }
    }
    state.currentPage = pg;
    state.currentPath = pg.path;
    if (switchSpace && SPACE_META[pg.space]) state.space = pg.space;
    if (pg.folder) state.expandedFolders.add(pg.folder);
    pushUrl();
    await refreshLocalState();
    render();
  }

  async function savePageChanges({ path, title, body, frontmatter, note = "" }) {
    const isExisting = state.currentPage && state.currentPage.path.toLowerCase() === path.toLowerCase();
    const payload = {
      path,
      title,
      body,
      frontmatter,
      note,
      expected_revision: isExisting ? state.currentPage.revision_id : null,
    };

    try {
      const res = isExisting
        ? await api.put(`/api/v1/pages/${encodeURIComponent(state.currentPage.id)}`, payload)
        : await api.post("/api/v1/pages", payload);
      await putPages([res.page]);
      state.currentPage = res.page;
      state.currentPath = res.page.path;
      state.space = res.page.space;
      state.editing = false;
      if (res.merged) toast("Offline-Änderung automatisch zusammengeführt!");
      else toast("Gespeichert.");
      await sync.pull().catch(() => {});
      await refreshLocalState();
      pushUrl();
      render();
    } catch (err) {
      if (err instanceof api.ApiError) {
        toast(err.message);
        if (err.status === 409 && err.data?.server) {
          state.currentPage = err.data.server;
          render();
        }
        return;
      }
      // Echtes Offline-Speichern in lokaler IndexedDB + Outbox!
      const endpoint = isExisting ? `/api/v1/pages/${encodeURIComponent(state.currentPage.id)}` : "/api/v1/pages";
      const method = isExisting ? "PUT" : "POST";
      await outbox.enqueue(method, endpoint, payload, { path, title });
      const optimistic = {
        ...(state.currentPage || { id: `local-${Date.now().toString(36).slice(-8)}`, attachments: [], backlinks: [], outgoing_links: [] }),
        space: path.split("/")[0],
        path,
        folder: path.split("/").slice(0, -1).join("/"),
        slug: path.split("/").pop(),
        title: title || path.split("/").pop(),
        body,
        frontmatter: frontmatter || {},
        updated_at: new Date().toISOString(),
        updated_by: "user (offline)",
      };
      await putPages([optimistic]);
      state.currentPage = optimistic;
      state.currentPath = optimistic.path;
      state.editing = false;
      await refreshLocalState();
      toast("Offline gespeichert – wird automatisch synchronisiert.");
      render();
    }
  }

  // Schmale Leiste links neben der Seitenleiste (wie im Kalender): Neu, Bereiche, Graph/Verlauf und unten die Einstellungen
  function renderRail() {
    const btn = (attrs, active, icon, label) => `
      <button type="button" class="rail-btn ${active ? "active" : ""}" ${attrs}>
        <span class="rail-ic"><span class="ms">${icon}</span></span>
        <span class="rail-lbl">${label}</span>
      </button>`;
    return `
      <nav class="wk-rail" aria-label="Navigation">
        <img class="rail-logo" src="${esc(document.querySelector('link[rel="icon"]')?.href || "")}" alt="">
        <button type="button" class="rail-fab" data-act="new" title="Neue Seite" aria-label="Neue Seite"><span class="ms">add</span></button>
        <div class="rail-group">
          ${Object.entries(SPACE_META)
            .map(([key, info]) => btn(`data-switch-space="${key}"`, state.space === key && state.view === "page", info.icon, info.label === "KI & Chats" ? "KI &amp; Chats" : info.label))
            .join("")}
        </div>
        <div class="rail-group">
          ${btn(`data-switch-view="graph"`, state.view === "graph", "hub", "Wissensgraph")}
          ${btn(`data-switch-view="history"`, state.view === "history", "history", "Verlauf")}
        </div>
        <div class="rail-spacer"></div>
        ${btn(`data-act="settings" title="Einstellungen &amp; MCP"`, false, "settings", "Einstellungen")}
      </nav>
    `;
  }

  // Suche ganz rechts in jeder Kopfzeile
  function searchBtn() {
    return `<button type="button" class="icon-btn" data-act="search" title="Suchen (Strg+K)" aria-label="Suchen"><span class="ms">search</span></button>`;
  }

  function renderSidebar() {
    const spPages = state.pages.filter((p) => p.space === state.space);
    const spFolders = state.folders.filter((f) => f.startsWith(`${state.space}/`) && f !== state.space);

    // Top-Level-Seiten direkt unter `<space>/<datei>`
    const rootPages = spPages.filter((p) => p.folder === state.space);

    // Unterordner gruppieren
    const folderHtml = spFolders
      .map((fld) => {
        const depth = fld.split("/").length - 2;
        const label = fld.split("/").pop();
        const directPages = spPages.filter((p) => p.folder === fld);
        const open = state.expandedFolders.has(fld) || (state.currentPage?.folder || "").startsWith(fld);
        return `
        <div class="tree-folder" style="--depth:${depth}">
          <button type="button" class="tree-folder-btn ${open ? "open" : ""}" data-toggle-folder="${esc(fld)}">
            <span class="ms folder-ic">${open ? "folder_open" : (fld.includes("/code") ? "folder_code" : "folder")}</span>
            <span class="tree-label">${esc(label)}</span>
            <span class="tree-count">${directPages.length || ""}</span>
          </button>
          ${
            open
              ? `<div class="tree-children">
                  ${directPages
                    .map(
                      (p) => `
                    <button type="button" class="tree-page ${p.path === state.currentPath && state.view === "page" ? "active" : ""}" data-open-page="${esc(p.path)}">
                      <span class="ms page-ic">${p.pinned ? "star" : "description"}</span>
                      <span class="tree-label">${esc(p.title)}</span>
                    </button>`,
                    )
                    .join("")}
                </div>`
              : ""
          }
        </div>`;
      })
      .join("");

    return `
      <aside class="wk-sidebar ${state.drawerOpen ? "open" : ""}">
        <div class="sidebar-head">
        <div class="brand-row">
          <div class="brand-title">
            <span class="brand-dot"></span>
            <strong>IDA Wiki</strong>
          </div>
          <div class="brand-actions mobile-only">
            <button type="button" class="icon-btn" data-act="new" title="Neue Seite"><span class="ms">add</span></button>
            <button type="button" class="icon-btn" data-act="settings" title="Einstellungen &amp; MCP"><span class="ms">settings</span></button>
          </div>
        </div>

        ${
          state.pendingProposals > 0
            ? `<button type="button" class="proposal-banner" id="wkOpenProposals">
                <span class="ms">shield</span>
                <span>${state.pendingProposals} offene Freigabe${state.pendingProposals > 1 ? "n" : ""} für <code>privat/</code></span>
              </button>`
            : ""
        }
        </div>

        <div class="tree-scroll">
          <div class="space-caption">${esc(SPACE_META[state.space].desc)}</div>
          ${rootPages
            .map(
              (p) => `
            <button type="button" class="tree-page root-page ${p.path === state.currentPath && state.view === "page" ? "active" : ""}" data-open-page="${esc(p.path)}">
              <span class="ms page-ic">${p.pinned ? "star" : "notes"}</span>
              <span class="tree-label">${esc(p.title)}</span>
            </button>`,
            )
            .join("")}
          ${folderHtml}

          ${
            state.tags.length
              ? `<div class="tag-cloud-sec">
                  <div class="space-caption">Schlagworte</div>
                  <div class="tag-cloud">
                    ${state.tags.map((t) => `<button type="button" class="md-tag" data-tag="${esc(t.tag)}">#${esc(t.tag)} <small>${t.count}</small></button>`).join("")}
                  </div>
                </div>`
              : ""
          }
        </div>

        <div class="sidebar-foot">
          <span class="sync-indicator ${state.online ? "ok" : "off"}">
            <span class="ms">${state.online ? "cloud_sync" : "cloud_off"}</span>
            ${
              state.outboxSummary.pending > 0
                ? `${state.outboxSummary.pending} ausstehend`
                : state.online
                  ? "Synchron"
                  : "Offline-Modus"
            }
          </span>
          ${
            state.space !== "ki"
              ? `<button type="button" class="btn-tonal sm" id="wkNewFolderBtn"><span class="ms">folder</span> Ordner+</button>`
              : `<span class="readonly-hint">Auto-Spiegel</span>`
          }
        </div>
      </aside>
      ${state.drawerOpen ? `<div class="drawer-scrim" id="wkScrim"></div>` : ""}
    `;
  }

  function renderPageStage() {
    const p = state.currentPage;
    if (!p) {
      return `<main class="wk-stage"><div class="empty-stage">Wähle links eine Seite oder lege mit <strong>+</strong> eine neue Notiz an.</div></main>`;
    }
    const isReadOnlyKi = p.space === "ki";
    const pageIndex = buildPageIndex(state.pages);
    const renderedHtml = renderMarkdown(p.body || "", {
      space: p.space,
      pageIndex,
      attachments: p.attachments || [],
      prefixUrl: api.apiUrl,
      readonlyTasks: isReadOnlyKi,
    });

    const crumbs = p.path.split("/");
    const idaLink = p.frontmatter?.ida_url;

    return `
      <main class="wk-stage" id="wkDropZone">
        <header class="stage-topbar">
          <div class="topbar-left">
            <button type="button" class="icon-btn mobile-only" id="wkOpenDrawer"><span class="ms">menu</span></button>
            <div class="breadcrumbs">
              <span class="space-pill sp-${esc(p.space)}">${esc(SPACE_META[p.space]?.label || p.space)}</span>
              ${crumbs.slice(1).map((c) => `<span class="crumb-sep">/</span><span class="crumb">${esc(c)}</span>`).join("")}
            </div>
          </div>
          <div class="topbar-actions">
            ${idaLink ? `<a class="btn-tonal sm" href="${esc(idaLink)}" target="_blank" rel="noopener"><span class="ms">open_in_new</span> In IDA</a>` : ""}
            <button type="button" class="btn-tonal sm" id="wkAskIdaBtn" title="Ida zu dieser Seite fragen"><span class="ms">auto_awesome</span> <span class="hide-xs">Ida fragen</span></button>
            ${
              !isReadOnlyKi && state.editing
                ? `
                  <label class="icon-btn" title="Datei oder Bild anhängen">
                    <span class="ms">attach_file</span>
                    <input type="file" id="wkFileInput" multiple hidden>
                  </label>
                  <button type="button" class="icon-btn" id="wkMoveBtn" title="Verschieben / Umbenennen"><span class="ms">alt_route</span></button>
                `
                : ""
            }
            ${
              !isReadOnlyKi
                ? `
                  <button type="button" class="btn-primary sm" id="wkToggleEdit">
                    <span class="ms">${state.editing ? "visibility" : "edit"}</span>
                    <span>${state.editing ? "Fertig" : "Bearbeiten"}</span>
                  </button>
                `
                : ""
            }
            ${!isReadOnlyKi && state.editing ? `<button type="button" class="icon-btn" id="wkDeletePageBtn" title="In den Papierkorb"><span class="ms">delete</span></button>` : ""}
            ${searchBtn()}
          </div>
        </header>

        ${
          state.editing
            ? `
            <div class="editor-workspace">
              <div class="editor-pane">
                <div class="editor-toolbar">
                  <input id="wkEditTitle" class="wk-title-input" value="${esc(p.title)}" placeholder="Seitentitel">
                  <div class="fmt-btns">
                    <button type="button" class="icon-btn sm" data-ins="[[|]]" title="[[Wikilink]] einfügen"><span class="ms">add_link</span></button>
                    <button type="button" class="icon-btn sm" data-ins="\n- [ ] " title="Checkliste"><span class="ms">checklist</span></button>
                    <button type="button" class="icon-btn sm" data-ins="\n> [!note] Hinweis\n> " title="Callout-Box"><span class="ms">lightbulb</span></button>
                    <button type="button" class="btn-primary sm" id="wkSaveBtn"><span class="ms">save</span> Speichern</button>
                  </div>
                </div>
                <div class="editor-textarea-wrap">
                  <textarea id="wkEditorArea" class="wk-editor-textarea" spellcheck="true" placeholder="Schreibe in Markdown … Tippe [[ für Seitenverweise oder füge Bilder direkt aus der Zwischenablage ein.">${esc(p.body || "")}</textarea>
                  <div id="wkLinkSuggest" class="link-suggest-pop" hidden></div>
                </div>
              </div>
              <div class="preview-pane hide-mobile">
                <div class="preview-head">Live-Vorschau</div>
                <article class="md-prose" id="wkLivePreview">${renderedHtml}</article>
              </div>
            </div>
          `
            : `
            <div class="page-scroll">
              <div class="page-content-grid">
                <article class="page-article">
                  <div class="page-meta-bar">
                    ${(p.tags || []).map((t) => `<button type="button" class="md-tag" data-tag="${esc(t)}">#${esc(t)}</button>`).join("")}
                    <span class="meta-stamp">Zuletzt geändert von <strong>${esc(p.updated_by || "user")}</strong>${p.updated_at ? ` · ${formatDate(p.updated_at)}` : ""}</span>
                  </div>
                  <div class="md-prose" id="wkReadProse">${renderedHtml}</div>

                  ${
                    (p.attachments || []).length
                      ? `
                      <section class="page-sec">
                        <h3><span class="ms">attach_file</span> Dateianhänge (${p.attachments.length})</h3>
                        <div class="att-strip">
                          ${p.attachments
                            .map(
                              (a) => `
                            <button type="button" class="att-card" data-open-att="${esc(a.name)}">
                              ${
                                a.kind === "image"
                                  ? `<img src="${esc(api.apiUrl(a.url))}" alt="${esc(a.name)}" loading="lazy">`
                                  : `<span class="ms att-big-ic">${a.kind === "pdf" ? "description" : "attach_file"}</span>`
                              }
                              <div class="att-card-meta">
                                <strong>${esc(a.name)}</strong>
                                <small>${formatBytes(a.size)}</small>
                              </div>
                            </button>`,
                            )
                            .join("")}
                        </div>
                      </section>`
                      : ""
                  }

                  <section class="page-sec backlinks-sec">
                    <h3><span class="ms">link</span> Verweise auf diese Seite (${(p.backlinks || []).length})</h3>
                    ${
                      (p.backlinks || []).length
                        ? `<div class="backlink-grid">
                            ${p.backlinks
                              .map(
                                (b) => `
                              <button type="button" class="backlink-card" data-open-page="${esc(b.source_path)}">
                                <div class="search-hit-top">
                                  <span class="space-pill sp-${esc(b.source_space)}">${esc(b.source_space)}</span>
                                  <strong>${esc(b.source_title)}</strong>
                                </div>
                                ${b.snippet ? `<p>${esc(b.snippet)}</p>` : ""}
                              </button>`,
                              )
                              .join("")}
                          </div>`
                        : `<p class="muted-path">Noch keine eingehenden <code>[[Wikilinks]]</code> auf diese Seite.</p>`
                    }
                  </section>
                </article>

                <aside class="page-context-rail">
                  <div class="rail-card">
                    <div class="rail-card-head">
                      <span><span class="ms">hub</span> Lokaler Wissensgraph</span>
                      <button type="button" class="icon-btn sm" id="wkExpandGraphBtn" title="Im großen Graphen öffnen"><span class="ms">open_in_new</span></button>
                    </div>
                    <canvas id="wkMiniGraph" class="mini-graph-canvas"></canvas>
                  </div>
                  ${
                    (p.headings || []).length > 1
                      ? `<div class="rail-card">
                          <div class="rail-card-head"><span>Gliederung</span></div>
                          <nav class="toc-list">
                            ${p.headings.map((h) => `<a href="#h-${esc(h.slug)}" class="toc-link lvl-${h.level}">${esc(h.text)}</a>`).join("")}
                          </nav>
                        </div>`
                      : ""
                  }
                  <div class="rail-card" id="wkPageRevDrawer" ${state.pageRevsOpen ? "" : "hidden"}>
                    <div class="rail-card-head"><span><span class="ms">history</span> Seitenversionen</span></div>
                    <div id="wkPageRevList" class="rev-mini-list">Lade …</div>
                  </div>
                </aside>
              </div>
            </div>
          `
        }
      </main>
    `;
  }

  function renderGraphStage() {
    return `
      <main class="wk-stage graph-stage">
        <header class="stage-topbar">
          <div class="topbar-left">
            <button type="button" class="icon-btn mobile-only" id="wkOpenDrawer"><span class="ms">menu</span></button>
            <h2><span class="ms">hub</span> <span class="tb-title">Wissensgraph</span></h2>
          </div>
          <div class="topbar-sub">
            <div class="filter-row inline-filters" id="wkGraphSpaceChips">
              <button type="button" class="chip ${!state.graphFilterSpace ? "active" : ""}" data-gsp="">Alle</button>
              <button type="button" class="chip ${state.graphFilterSpace === "privat" ? "active" : ""}" data-gsp="privat">Privat</button>
              <button type="button" class="chip ${state.graphFilterSpace === "memory" ? "active" : ""}" data-gsp="memory">Gedächtnis</button>
              <button type="button" class="chip ${state.graphFilterSpace === "ki" ? "active" : ""}" data-gsp="ki">KI &amp; Chats</button>
            </div>
          </div>
          <div class="topbar-actions">
            ${
              state.graphLocalCenter
                ? `<button type="button" class="chip active tb-focus" id="wkClearLocalGraph">Fokus: ${esc(state.graphLocalCenter.split("/").pop())} ×</button>`
                : ""
            }
            <input type="search" id="wkGraphSearch" class="wk-input sm-input graph-find" placeholder="Knoten hervorheben …">
            <button type="button" class="icon-btn tb-reset" id="wkGraphReset" title="Ansicht zentrieren"><span class="ms">restart_alt</span></button>
            ${searchBtn()}
          </div>
        </header>
        <div class="graph-canvas-wrap">
          <canvas id="wkMainGraph" class="main-graph-canvas"></canvas>
          <div class="graph-legend">
            <span><i style="background:#7fd3ff"></i> Privat</span>
            <span><i style="background:#c3b9f5"></i> KI-Gedächtnis</span>
            <span><i style="background:#81c995"></i> KI &amp; Chats</span>
            <span><i class="ghost-dot"></i> Offener [[Link]] (Klick legt Seite an)</span>
          </div>
        </div>
      </main>
    `;
  }

  function renderHistoryStage() {
    return `
      <main class="wk-stage history-stage">
        <header class="stage-topbar">
          <div class="topbar-left">
            <button type="button" class="icon-btn mobile-only" id="wkOpenDrawer"><span class="ms">menu</span></button>
            <h2><span class="ms">history</span> <span class="tb-title">Wiki-Verlauf &amp; Wiederherstellung</span></h2>
          </div>
          <div class="topbar-sub">
            <div class="filter-row inline-filters" id="wkHistFilters">
              <button type="button" class="chip ${!state.historySpace && !state.historyOnlyAi ? "active" : ""}" data-hsp="">Alle</button>
              <button type="button" class="chip ${state.historySpace === "privat" ? "active" : ""}" data-hsp="privat">Privat</button>
              <button type="button" class="chip ${state.historySpace === "memory" ? "active" : ""}" data-hsp="memory">Gedächtnis</button>
              <button type="button" class="chip ${state.historySpace === "ki" ? "active" : ""}" data-hsp="ki">KI &amp; Chats</button>
              <button type="button" class="chip ${state.historyOnlyAi ? "active" : ""}" id="wkHistOnlyAi"><span class="ms">auto_awesome</span> Nur KI</button>
            </div>
          </div>
          <div class="topbar-actions">${searchBtn()}</div>
        </header>
        <div class="history-split">
          <div class="history-list" id="wkHistTimeline"><div class="empty-hint">Lade Versionsverlauf …</div></div>
          <div class="history-inspector" id="wkHistDetail">
            <div class="empty-hint">Wähle links eine Änderung aus, um das genaue Diff zu prüfen oder den vorherigen Stand mit einem Klick wiederherzustellen.</div>
          </div>
        </div>
      </main>
    `;
  }

  function renderBottomBar() {
    return `
      <nav class="wk-bottom-nav mobile-only">
        <button type="button" class="bnav-btn ${state.view === "page" && state.space === "privat" ? "active" : ""}" data-switch-space="privat"><span class="ms">home</span><small>Privat</small></button>
        <button type="button" class="bnav-btn ${state.view === "page" && state.space === "memory" ? "active" : ""}" data-switch-space="memory"><span class="ms">auto_awesome</span><small>Gedächtnis</small></button>
        <button type="button" class="bnav-btn ${state.view === "page" && state.space === "ki" ? "active" : ""}" data-switch-space="ki"><span class="ms">forum</span><small>Chats</small></button>
        <button type="button" class="bnav-btn ${state.view === "graph" ? "active" : ""}" data-switch-view="graph"><span class="ms">hub</span><small>Graph</small></button>
        <button type="button" class="bnav-btn ${state.view === "history" ? "active" : ""}" data-switch-view="history"><span class="ms">history</span><small>Verlauf</small></button>
      </nav>
    `;
  }

  // Nach Hintergrund-Abgleich: im Wissensgraph nur die Seitenleiste auffrischen – ein komplettes Neuzeichnen würde
  // den Graphen neu aufbauen und Zoom/Ausschnitt zurücksetzen
  function refreshAfterSync() {
    if (state.view !== "graph" || !rootEl.querySelector("#wkMainGraph")) {
      render();
      return;
    }
    const old = rootEl.querySelector(".wk-sidebar");
    if (!old) return;
    const scrollTop = old.querySelector(".tree-scroll")?.scrollTop || 0;
    const tpl = document.createElement("template");
    tpl.innerHTML = renderSidebar();
    const fresh = tpl.content.querySelector(".wk-sidebar");
    old.replaceWith(fresh);
    const tree = fresh.querySelector(".tree-scroll");
    if (tree) tree.scrollTop = scrollTop;
    watchFloatingBars();
  }

  function render() {
    activeGraphCtrl?.destroy();
    activeGraphCtrl = null;
    activeMiniGraphCtrl?.destroy();
    activeMiniGraphCtrl = null;

    const stage =
      state.view === "graph"
        ? renderGraphStage()
        : state.view === "history"
          ? renderHistoryStage()
          : renderPageStage();

    rootEl.innerHTML = `<div class="wk-shell view-${state.view}">${renderRail()}${renderSidebar()}${stage}${renderBottomBar()}</div>`;
    watchFloatingBars();
    bindStageHydration();
  }

  // Kopfzeile und Seitenleisten-Kopf schweben über dem Inhalt; der Inhalt bekommt oben genau so viel Luft, wie sie hoch sind,
  // und scrollt dann darunter durch (wie im Kalender)
  let barObserver = null;
  function watchFloatingBars() {
    barObserver?.disconnect();
    const pairs = [
      [rootEl.querySelector(".stage-topbar"), rootEl.querySelector(".wk-stage"), "--tb"],
      [rootEl.querySelector(".sidebar-head"), rootEl.querySelector(".wk-sidebar"), "--sh"],
      [rootEl.querySelector(".sidebar-foot"), rootEl.querySelector(".wk-sidebar"), "--sf"],
    ].filter(([bar, host]) => bar && host);
    const apply = () => pairs.forEach(([bar, host, prop]) => host.style.setProperty(prop, `${bar.offsetHeight}px`));
    apply();
    barObserver = new ResizeObserver(apply);
    pairs.forEach(([bar]) => barObserver.observe(bar));
  }

  async function bindStageHydration() {
    if (state.view === "page" && state.currentPage) {
      if (state.editing) {
        bindLiveEditor();
      } else {
        const miniCanvas = rootEl.querySelector("#wkMiniGraph");
        if (miniCanvas) {
          api
            .get(`/api/v1/graph?center=${encodeURIComponent(state.currentPage.id)}&depth=1`)
            .then((g) => {
              if (!miniCanvas.isConnected) return;
              activeMiniGraphCtrl = mountGraph(miniCanvas, g, {
                onSelectNode: (node) => handleGraphNodeClick(node),
              });
            })
            .catch(() => {});
        }
        if (state.pageRevsOpen) loadPageRevisionsList();
      }
    } else if (state.view === "graph") {
      const canvas = rootEl.querySelector("#wkMainGraph");
      if (canvas) {
        const params = new URLSearchParams();
        if (state.graphFilterSpace) params.set("space", state.graphFilterSpace);
        if (state.graphLocalCenter) {
          params.set("center", state.graphLocalCenter);
          params.set("depth", String(state.graphDepth));
        }
        try {
          const g = await api.get(`/api/v1/graph?${params.toString()}`);
          if (!canvas.isConnected) return;
          activeGraphCtrl = mountGraph(canvas, g, {
            onSelectNode: (node) => handleGraphNodeClick(node),
            inset: graphInset,
          });
        } catch (err) {
          toast(err.message);
        }
      }
    } else if (state.view === "history") {
      await loadGlobalHistory();
    }
  }

  // Der große Graph liegt hinter Leiste, Seitenleiste und Kopfzeile – eingepasst wird in den frei sichtbaren Bereich
  function graphInset() {
    const desktop = matchMedia("(min-width: 961px)").matches;
    const side = rootEl.querySelector(".wk-sidebar");
    const top = rootEl.querySelector(".graph-stage .stage-topbar");
    const bnav = rootEl.querySelector(".wk-bottom-nav");
    const legend = rootEl.querySelector(".graph-legend");
    const bottomEl = !desktop && bnav ? bnav : legend;
    return {
      left: desktop && side ? side.getBoundingClientRect().right : 0,
      top: top ? top.getBoundingClientRect().bottom : 0,
      bottom: bottomEl ? Math.max(0, innerHeight - bottomEl.getBoundingClientRect().top) : 0,
    };
  }

  function handleGraphNodeClick(node) {
    if (node.kind === "ghost") {
      openNewPageSheet({
        defaultSpace: state.space,
        suggestedPath: node.path.includes("/") ? node.path : `privat/00-inbox/${node.path}`,
        folders: state.folders,
        memoryCategories: state.meta.memory_categories || [],
        onSave: savePageChanges,
      });
      return;
    }
    if (node.kind === "attachment" && node.page_id) {
      loadPage(node.page_id);
      return;
    }
    loadPage(node.path || node.id);
  }

  async function loadPageRevisionsList() {
    const box = rootEl.querySelector("#wkPageRevList");
    if (!box || !state.currentPage) return;
    try {
      const r = await api.get(`/api/v1/pages/${encodeURIComponent(state.currentPage.id)}/revisions`);
      box.innerHTML = (r.revisions || [])
        .map(
          (rev) => `
        <button type="button" class="rev-mini-row" data-inspect-rev="${rev.id}">
          <div><strong>#${rev.rev_number}</strong> · ${esc(rev.by)} <span class="diff-stat">+${rev.added_lines} -${rev.deleted_lines}</span></div>
          <small class="muted-path">${formatDate(rev.at)} ${rev.note ? `· ${esc(rev.note)}` : ""}</small>
        </button>`,
        )
        .join("");
    } catch (err) {
      box.innerHTML = `<div class="muted-path">${esc(err.message)}</div>`;
    }
  }

  async function loadGlobalHistory() {
    const listEl = rootEl.querySelector("#wkHistTimeline");
    if (!listEl) return;
    const params = new URLSearchParams({ limit: "100" });
    if (state.historySpace) params.set("space", state.historySpace);
    if (state.historyOnlyAi) params.set("by", "_ai_");
    try {
      const r = await api.get(`/api/v1/history?${params.toString()}`);
      const revs = r.revisions || [];
      if (!revs.length) {
        listEl.innerHTML = `<div class="empty-hint">Noch keine Einträge für diesen Filter.</div>`;
        return;
      }
      listEl.innerHTML = revs
        .map(
          (rev) => `
        <button type="button" class="hist-card ${state.selectedRevDetail?.id === rev.id ? "active" : ""}" data-select-rev="${rev.id}">
          <div class="hist-card-top">
            <span class="space-pill sp-${esc(rev.space)}">${esc(rev.space)}</span>
            <span class="author-badge ${rev.by === "user" ? "by-user" : "by-ai"}">${esc(rev.by)}${rev.approved_by ? " ✓ freigegeben" : ""}</span>
            <span class="diff-stat">+${rev.added_lines} / -${rev.deleted_lines}</span>
          </div>
          <div class="hist-title">${esc(rev.title)} <small>#${rev.rev_number} (${esc(rev.action)})</small></div>
          <div class="muted-path">${esc(rev.path)} · ${formatDate(rev.at)}${rev.note ? ` · ${esc(rev.note)}` : ""}</div>
        </button>`,
        )
        .join("");
      if (!state.selectedRevDetail && revs[0]) {
        await inspectRevision(revs[0].id);
      }
    } catch (err) {
      listEl.innerHTML = `<div class="empty-hint">${esc(err.message)}</div>`;
    }
  }

  async function inspectRevision(revId) {
    const detailEl = rootEl.querySelector("#wkHistDetail");
    if (!detailEl) return;
    const d = await api.get(`/api/v1/revisions/${revId}`);
    state.selectedRevDetail = d;
    rootEl.querySelectorAll(".hist-card").forEach((c) => c.classList.toggle("active", Number(c.dataset.selectRev) === revId));
    const canRestore = d.space !== "ki";
    detailEl.innerHTML = `
      <div class="inspector-head">
        <div>
          <h3>${esc(d.title)} <small>Version #${d.rev_number}</small></h3>
          <div class="muted-path"><code>${esc(d.path)}</code> · von <strong>${esc(d.by)}</strong> über ${esc(d.via)} am ${formatDate(d.at)}</div>
        </div>
        <div class="prop-actions">
          <button type="button" class="btn-tonal sm" data-open-page="${esc(d.path)}"><span class="ms">description</span> Zur Seite</button>
          ${
            canRestore
              ? `
            <button type="button" class="btn-tonal sm" data-undo-rev="${d.id}"><span class="ms">undo</span> Änderung rückgängig</button>
            <button type="button" class="btn-primary sm" data-restore-rev="${d.id}" data-restore-page="${esc(d.page_id)}"><span class="ms">restart_alt</span> Auf #${d.rev_number} zurücksetzen</button>
          `
              : ""
          }
        </div>
      </div>
      ${renderUnifiedDiff(d.diff)}
    `;
  }

  function bindLiveEditor() {
    const ta = rootEl.querySelector("#wkEditorArea");
    const prev = rootEl.querySelector("#wkLivePreview");
    const pop = rootEl.querySelector("#wkLinkSuggest");
    if (!ta) return;

    const pageIndex = buildPageIndex(state.pages);

    ta.addEventListener("input", async () => {
      if (prev) {
        prev.innerHTML = renderMarkdown(ta.value, {
          space: state.currentPage.space,
          pageIndex,
          attachments: state.currentPage.attachments || [],
          prefixUrl: api.apiUrl,
        });
      }
      // `[[`-Autocomplete prüfen
      const before = ta.value.slice(0, ta.selectionStart);
      const m = before.match(/\[\[([^\[\]\n|]{0,50})$/);
      if (m && pop) {
        const q = m[1].toLowerCase();
        const hits = state.pages
          .filter((p) => !q || p.title.toLowerCase().includes(q) || p.path.toLowerCase().includes(q))
          .slice(0, 8);
        if (hits.length) {
          pop.hidden = false;
          pop.innerHTML = hits
            .map(
              (h) => `
            <button type="button" class="suggest-item" data-pick-link="${esc(h.path)}" data-pick-title="${esc(h.title)}">
              <span class="space-pill sp-${esc(h.space)}">${esc(h.space)}</span>
              <strong>${esc(h.title)}</strong>
              <small>${esc(h.path)}</small>
            </button>`,
            )
            .join("");
          return;
        }
      }
      if (pop) pop.hidden = true;
    });

    pop?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-pick-link]");
      if (!btn) return;
      const before = ta.value.slice(0, ta.selectionStart);
      const after = ta.value.slice(ta.selectionStart);
      const replaced = before.replace(/\[\[[^\[\]\n|]{0,50}$/, `[[${btn.dataset.pickLink}|${btn.dataset.pickTitle}]]`);
      ta.value = replaced + after;
      pop.hidden = true;
      ta.focus();
      ta.dispatchEvent(new Event("input"));
    });

    // Bilder direkt per Strg+V einfügen -> automatischer Anhang-Upload + ![[name]] im Markdown!
    ta.addEventListener("paste", async (e) => {
      const files = [...(e.clipboardData?.files || [])];
      if (!files.length || !state.currentPage) return;
      e.preventDefault();
      await uploadAndEmbedFiles(files, ta);
    });
  }

  async function uploadAndEmbedFiles(files, editorTextarea = null) {
    if (!state.currentPage || state.currentPage.space === "ki") return;
    for (const f of files) {
      try {
        const res = await api.uploadFile(state.currentPage.id, f);
        const att = res.attachment;
        toast(`Angehängt: ${att.name}`);
        if (editorTextarea) {
          const ins = `\n![[${att.name}]]\n`;
          const pos = editorTextarea.selectionStart;
          editorTextarea.value = editorTextarea.value.slice(0, pos) + ins + editorTextarea.value.slice(pos);
        }
      } catch (err) {
        toast(err.message);
      }
    }
    if (!editorTextarea) {
      await loadPage(state.currentPage.id, { switchSpace: false });
    } else {
      const refreshed = await api.get(`/api/v1/pages/${encodeURIComponent(state.currentPage.id)}`);
      state.currentPage = refreshed;
      editorTextarea.dispatchEvent(new Event("input"));
    }
  }

  // Zentrale Event-Delegation
  rootEl.addEventListener("click", async (e) => {
    const openDrawer = e.target.closest("#wkOpenDrawer");
    if (openDrawer) {
      state.drawerOpen = true;
      render();
      return;
    }
    if (e.target.closest("#wkScrim")) {
      state.drawerOpen = false;
      render();
      return;
    }

    const spBtn = e.target.closest("[data-switch-space]");
    if (spBtn) {
      const sp = spBtn.dataset.switchSpace;
      state.space = sp;
      state.view = "page";
      state.drawerOpen = false;
      const defaults = { privat: "privat/_start", memory: "memory/_index", ki: "ki/_uebersicht" };
      if (!state.currentPage || state.currentPage.space !== sp) {
        const firstInSpace = state.pages.find((p) => p.path === defaults[sp]) || state.pages.find((p) => p.space === sp);
        if (firstInSpace) await loadPage(firstInSpace.path, { switchSpace: false });
        else render();
      } else {
        pushUrl();
        render();
      }
      return;
    }

    const viewBtn = e.target.closest("[data-switch-view]");
    if (viewBtn) {
      state.view = viewBtn.dataset.switchView;
      state.drawerOpen = false;
      state.graphLocalCenter = "";
      pushUrl();
      render();
      return;
    }

    const fldBtn = e.target.closest("[data-toggle-folder]");
    if (fldBtn) {
      const f = fldBtn.dataset.toggleFolder;
      if (state.expandedFolders.has(f)) state.expandedFolders.delete(f);
      else state.expandedFolders.add(f);
      render();
      return;
    }

    const pgBtn = e.target.closest("[data-open-page]");
    if (pgBtn) {
      state.drawerOpen = false;
      await loadPage(pgBtn.dataset.openPage);
      return;
    }

    const wl = e.target.closest("[data-wikilink]");
    if (wl) {
      e.preventDefault();
      if (wl.dataset.ghost === "1") {
        openNewPageSheet({
          defaultSpace: state.space,
          suggestedPath: wl.dataset.wikilink,
          folders: state.folders,
          memoryCategories: state.meta.memory_categories || [],
          onSave: savePageChanges,
        });
      } else {
        await loadPage(wl.dataset.wikilink);
        if (wl.dataset.heading) {
          document.getElementById(`h-${wl.dataset.heading}`)?.scrollIntoView({ behavior: "smooth" });
        }
      }
      return;
    }

    const tagBtn = e.target.closest("[data-tag]");
    if (tagBtn) {
      openSearchSheet({ initialTag: tagBtn.dataset.tag, onNavigate: (p) => loadPage(p) });
      return;
    }

    if (e.target.closest('[data-act="search"]')) {
      openSearchSheet({ onNavigate: (p) => loadPage(p) });
      return;
    }

    if (e.target.closest('[data-act="new"]')) {
      openNewPageSheet({
        defaultSpace: state.space,
        defaultFolder: state.currentPage?.folder || "",
        folders: state.folders,
        memoryCategories: state.meta.memory_categories || [],
        onSave: savePageChanges,
      });
      return;
    }

    if (e.target.closest("#wkNewFolderBtn")) {
      const name = prompt(`Neuer Unterordner unter ${state.space}/ (z. B. 04-arbeit-projekte/hausbau):`);
      if (name?.trim()) {
        const full = name.trim().startsWith(`${state.space}/`) ? name.trim() : `${state.space}/${name.trim()}`;
        try {
          await api.post("/api/v1/folders", { folder: full });
          await sync.pull();
          await refreshLocalState();
          state.expandedFolders.add(full);
          toast(`Ordner „${full}“ angelegt.`);
          render();
        } catch (err) {
          toast(err.message);
        }
      }
      return;
    }

    if (e.target.closest('[data-act="settings"]')) {
      await openSettingsSheet({
        meta: state.meta,
        installPrompt: state.installPrompt,
        toast,
        onRefresh: async () => {
          await sync.pull();
          await refreshLocalState();
          render();
        },
      });
      return;
    }

    if (e.target.closest("#wkOpenProposals")) {
      await openProposalsSheet({
        toast,
        onResolved: async () => {
          await sync.pull();
          await refreshLocalState();
          if (state.currentPage) await loadPage(state.currentPage.path, { switchSpace: false });
          else render();
        },
      });
      return;
    }

    if (e.target.closest("#wkAskIdaBtn")) {
      openAskIdaSheet({ page: state.currentPage, toast });
      return;
    }

    if (e.target.closest("#wkToggleEdit")) {
      if (state.editing) {
        const ta = rootEl.querySelector("#wkEditorArea");
        const ti = rootEl.querySelector("#wkEditTitle");
        if (ta && state.currentPage) {
          await savePageChanges({
            path: state.currentPage.path,
            title: ti?.value.trim() || state.currentPage.title,
            body: ta.value,
            frontmatter: state.currentPage.frontmatter,
          });
          return;
        }
      }
      state.editing = !state.editing;
      render();
      return;
    }

    if (e.target.closest("#wkSaveBtn")) {
      const ta = rootEl.querySelector("#wkEditorArea");
      const ti = rootEl.querySelector("#wkEditTitle");
      if (ta && state.currentPage) {
        await savePageChanges({
          path: state.currentPage.path,
          title: ti?.value.trim() || state.currentPage.title,
          body: ta.value,
          frontmatter: state.currentPage.frontmatter,
        });
      }
      return;
    }

    const insBtn = e.target.closest("[data-ins]");
    if (insBtn) {
      const ta = rootEl.querySelector("#wkEditorArea");
      if (!ta) return;
      const tpl = insBtn.dataset.ins;
      const pos = ta.selectionStart;
      if (tpl === "[[|]]") {
        ta.value = `${ta.value.slice(0, pos)}[[${ta.value.slice(pos)}`;
        ta.selectionStart = ta.selectionEnd = pos + 2;
      } else {
        ta.value = ta.value.slice(0, pos) + tpl + ta.value.slice(pos);
        ta.selectionStart = ta.selectionEnd = pos + tpl.length;
      }
      ta.focus();
      ta.dispatchEvent(new Event("input"));
      return;
    }

    if (e.target.closest("#wkMoveBtn") && state.currentPage) {
      openMoveSheet({
        page: state.currentPage,
        onMove: async (toPath, newTitle) => {
          try {
            const res = await api.post(`/api/v1/pages/${encodeURIComponent(state.currentPage.id)}/move`, {
              to_path: toPath,
              title: newTitle,
            });
            toast(`Verschoben (${res.rewritten_backlinks || 0} eingehende Links automatisch angepasst).`);
            await sync.pull();
            await loadPage(res.page.path);
          } catch (err) {
            toast(err.message);
          }
        },
      });
      return;
    }

    if (e.target.closest("#wkDeletePageBtn") && state.currentPage) {
      if (!confirm(`Seite „${state.currentPage.title}“ in den 30-Tage-Papierkorb verschieben?`)) return;
      try {
        await api.del(`/api/v1/pages/${encodeURIComponent(state.currentPage.id)}`);
        toast("In den Papierkorb verschoben.");
        await sync.pull();
        await refreshLocalState();
        const fallback = state.space === "memory" ? "memory/_index" : "privat/_start";
        await loadPage(fallback, { switchSpace: false });
      } catch (err) {
        toast(err.message);
      }
      return;
    }

    if (e.target.closest("#wkPageHistBtn")) {
      state.pageRevsOpen = !state.pageRevsOpen;
      render();
      return;
    }

    const inspectMini = e.target.closest("[data-inspect-rev]");
    if (inspectMini) {
      const rid = Number(inspectMini.dataset.inspectRev);
      state.view = "history";
      state.selectedRevDetail = null;
      pushUrl();
      render();
      await inspectRevision(rid);
      return;
    }

    const openAtt = e.target.closest("[data-open-att]");
    if (openAtt && state.currentPage) {
      const att = (state.currentPage.attachments || []).find((a) => a.name === openAtt.dataset.openAtt);
      if (att) {
        openAttachmentSheet({
          pageId: state.currentPage.id,
          attachment: att,
          canEdit: state.currentPage.space !== "ki",
          onDelete: async (pid, name) => {
            await api.del(`/api/v1/files/${encodeURIComponent(pid)}/${encodeURIComponent(name)}`);
            toast("Anhang entfernt.");
            await loadPage(pid, { switchSpace: false });
          },
        });
      }
      return;
    }

    if (e.target.closest("#wkExpandGraphBtn") && state.currentPage) {
      state.view = "graph";
      state.graphLocalCenter = state.currentPage.path;
      pushUrl();
      render();
      return;
    }

    if (e.target.closest("#wkClearLocalGraph")) {
      state.graphLocalCenter = "";
      render();
      return;
    }

    const gsp = e.target.closest("[data-gsp]");
    if (gsp) {
      state.graphFilterSpace = gsp.dataset.gsp || "";
      render();
      return;
    }

    if (e.target.closest("#wkGraphReset")) {
      activeGraphCtrl?.resetView();
      return;
    }

    const hsp = e.target.closest("[data-hsp]");
    if (hsp) {
      state.historySpace = hsp.dataset.hsp || "";
      state.historyOnlyAi = false;
      state.selectedRevDetail = null;
      render();
      return;
    }

    if (e.target.closest("#wkHistOnlyAi")) {
      state.historyOnlyAi = !state.historyOnlyAi;
      state.selectedRevDetail = null;
      render();
      return;
    }

    const selRev = e.target.closest("[data-select-rev]");
    if (selRev) {
      await inspectRevision(Number(selRev.dataset.selectRev));
      return;
    }

    const restRev = e.target.closest("[data-restore-rev]");
    if (restRev) {
      const rid = Number(restRev.dataset.restoreRev);
      const pid = restRev.dataset.restorePage;
      const res = await api.post(`/api/v1/pages/${encodeURIComponent(pid)}/restore/${rid}`, {});
      toast("Version wiederhergestellt.");
      await sync.pull();
      await loadPage(res.page.path);
      return;
    }

    const undoRev = e.target.closest("[data-undo-rev]");
    if (undoRev) {
      const rid = Number(undoRev.dataset.undoRev);
      await api.post(`/api/v1/revisions/${rid}/undo`, {});
      toast("Änderung rückgängig gemacht.");
      await sync.pull();
      await refreshLocalState();
      await loadGlobalHistory();
      return;
    }

    const copyBtn = e.target.closest("[data-copy-code]");
    if (copyBtn) {
      const code = copyBtn.closest(".md-codeblock")?.querySelector("code")?.textContent || "";
      navigator.clipboard?.writeText(code);
      toast("Code kopiert.");
    }
  });

  // Interaktive Checklisten direkt im Lese-Modus abhaken!
  rootEl.addEventListener("change", async (e) => {
    if (e.target.matches("[data-task-line]") && state.currentPage && !state.editing) {
      const lineIdx = Number(e.target.dataset.taskLine);
      const updatedBody = toggleCheckboxAtLine(state.currentPage.body || "", lineIdx);
      await savePageChanges({
        path: state.currentPage.path,
        title: state.currentPage.title,
        body: updatedBody,
        frontmatter: state.currentPage.frontmatter,
        note: "Checkliste aktualisiert",
      });
    }
    if (e.target.id === "wkFileInput" && e.target.files?.length) {
      await uploadAndEmbedFiles([...e.target.files]);
    }
  });

  rootEl.addEventListener("input", (e) => {
    if (e.target.id === "wkGraphSearch") {
      activeGraphCtrl?.setQuery(e.target.value);
    }
  });

  // Drag-&-Drop von Dateien auf die Seite
  rootEl.addEventListener("dragover", (e) => {
    if (state.view === "page" && state.currentPage && state.currentPage.space !== "ki") {
      e.preventDefault();
    }
  });
  rootEl.addEventListener("drop", async (e) => {
    if (state.view === "page" && state.currentPage && state.currentPage.space !== "ki" && e.dataTransfer?.files?.length) {
      e.preventDefault();
      const ta = rootEl.querySelector("#wkEditorArea");
      await uploadAndEmbedFiles([...e.dataTransfer.files], ta);
    }
  });

  // Tastenkürzel (Strg+K für globale Suche, Strg+S zum Speichern im Editor)
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openSearchSheet({ onNavigate: (p) => loadPage(p) });
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && state.editing) {
      e.preventDefault();
      rootEl.querySelector("#wkSaveBtn")?.click();
    }
  });

  window.addEventListener("online", () => {
    state.online = true;
    sync.syncAll().then(refreshLocalState).then(refreshAfterSync).catch(() => {});
  });
  window.addEventListener("offline", () => {
    state.online = false;
    refreshAfterSync();
  });

  sync.onChange(async (evt) => {
    if (evt.type === "online") state.online = evt.online;
    await refreshLocalState();
    if (evt.type === "changed" && state.currentPage && evt.page_id === state.currentPage.id && !state.editing) {
      state.currentPage = (await getPage(state.currentPage.id)) || state.currentPage;
    }
    if (!state.editing) refreshAfterSync();
  });

  // Start: zuerst sofort aus lokalem IndexedDB-Cache zeichnen, dann Server-Sync + Live-SSE starten
  readRouteFromUrl();
  await refreshLocalState();
  if (state.pages.length) {
    state.currentPage = (await getPage(state.currentPath)) || state.pages[0];
    render();
  }

  try {
    state.meta = await api.get("/api/v1/meta");
    await sync.syncAll();
    await refreshLocalState();
  } catch {
    state.online = false;
  }

  if (state.view === "page") {
    await loadPage(state.currentPath, { switchSpace: false });
  } else {
    render();
  }

  const u = new URL(location.href);
  if (u.searchParams.get("neu") === "1") {
    openNewPageSheet({
      defaultSpace: "privat",
      defaultFolder: "privat/00-inbox",
      folders: state.folders,
      memoryCategories: state.meta.memory_categories || [],
      onSave: savePageChanges,
    });
  }

  sync.startStream();
}

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("de-DE", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
