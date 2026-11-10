// Smart Boards: Thema/Prompt + KI-Modell eingeben, Ida holt die Infos aus Chats, Wiki & Co. und zeigt sie geordnet an.
// „Neu laden“ lässt Ida nach Neuem und Geändertem suchen und das Board anpassen.

import * as api from "./api.js";
import { esc, renderMarkdown } from "./markdown.js";
import { openModal } from "./sheets.js";

const bs = { list: [], current: null, sel: localStorage.getItem("wiki.board") || "", models: null, connected: true, timer: 0 };

const ago = (iso) => {
  if (!iso) return "noch nie";
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.floor(s / 60)} Min.`;
  if (s < 86400) return `vor ${Math.floor(s / 3600)} Std.`;
  return new Date(iso).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
};
const modelName = (id) => (id ? bs.models?.model_names?.[id] || id : "Standard");

export function renderBoardsStage() {
  return `
    <main class="wk-stage boards-stage">
      <header class="stage-topbar">
        <div class="topbar-left">
          <button type="button" class="icon-btn mobile-only" id="wkOpenDrawer"><span class="ms">menu</span></button>
          <h2><span class="ms">dashboard</span> <span class="tb-title">Smart Boards</span></h2>
        </div>
        <div class="topbar-actions">
          <button type="button" class="btn-primary sm" id="wkBoardNew" title="Neues Board erstellen"><span class="ms">add</span> Neues Board</button>
        </div>
      </header>
      <div class="boards-split" id="wkBoards"><div class="empty-hint">Lade Boards …</div></div>
    </main>`;
}

export async function mountBoards(root) {
  const host = root.querySelector("#wkBoards");
  if (!host) return;
  clearTimeout(bs.timer);
  root.querySelector("#wkBoardNew").onclick = () => openForm(root);
  host.addEventListener("click", (e) => onClick(e, root));
  try {
    const r = await api.get("/api/v1/boards");
    bs.list = r.boards;
    bs.connected = r.connected;
  } catch (e) {
    host.innerHTML = `<div class="empty-hint">${esc(e.message)}</div>`;
    return;
  }
  if (!bs.list.some((b) => b.id === bs.sel)) bs.sel = bs.list[0]?.id || "";
  await select(root, bs.sel);
}

async function select(root, id) {
  bs.sel = id;
  localStorage.setItem("wiki.board", id);
  bs.current = null;
  if (id) {
    try {
      bs.current = await api.get(`/api/v1/boards/${id}`);
    } catch {
      bs.sel = "";
    }
  }
  draw(root);
  poll(root);
}

// Läuft gerade ein Lauf, fragt die Ansicht alle paar Sekunden nach, bis er fertig ist
function poll(root) {
  clearTimeout(bs.timer);
  if (bs.current?.status !== "running") return;
  bs.timer = setTimeout(async () => {
    if (!root.querySelector("#wkBoards")?.isConnected) return;
    try {
      const b = await api.get(`/api/v1/boards/${bs.sel}`);
      if (b.id !== bs.sel) return;
      bs.current = b;
      const row = bs.list.find((x) => x.id === b.id);
      if (row) Object.assign(row, b);
      draw(root);
    } catch { /* nächster Versuch */ }
    poll(root);
  }, 2500);
}

function draw(root) {
  const host = root.querySelector("#wkBoards");
  if (!host) return;
  const b = bs.current;
  host.classList.toggle("has-sel", !!b);
  const list = bs.list.length
    ? bs.list.map((x) => `
        <button type="button" class="hist-card board-card ${x.id === bs.sel ? "active" : ""}" data-board="${x.id}">
          <div class="hist-card-top"><span class="ms board-st ${x.status}">${x.status === "running" ? "progress_activity" : x.status === "error" ? "error" : "dashboard"}</span>
            <span class="hist-title">${esc(x.title)}</span></div>
          <div class="board-prev">${esc(x.preview || (x.status === "running" ? "Ida sammelt gerade …" : "Noch leer"))}</div>
        </button>`).join("")
    : `<div class="empty-hint">Noch kein Board.<br>Lege mit <strong>Neues Board</strong> eins an: Thema eingeben, Modell wählen, Ida erledigt den Rest.</div>`;
  host.innerHTML = `<div class="history-list board-list">${list}</div><div class="history-inspector board-view">${b ? view(b) : `<div class="empty-hint">Wähle links ein Board.</div>`}</div>`;
}

function view(b) {
  const running = b.status === "running";
  const body = b.content.trim()
    ? `<div class="md-prose board-prose">${renderMarkdown(b.content, { space: "memory", pageIndex: null, attachments: [], prefixUrl: api.apiUrl, readonlyTasks: true })}</div>`
    : `<div class="empty-hint">${running ? "Ida sucht gerade alles dazu zusammen. Das kann eine Weile dauern." : "Noch kein Inhalt. Mit „Neu laden“ startest du die Suche."}</div>`;
  return `
    <div class="board-head">
      <button type="button" class="icon-btn mobile-only" data-board-back title="Zur Liste"><span class="ms">arrow_back</span></button>
      <div class="board-title"><h3>${esc(b.title)}</h3>
        <small>${esc(modelName(b.model))} · aktualisiert ${esc(ago(b.last_run_at))}${running ? " · läuft …" : ""}</small></div>
      <div class="board-actions">
        <button type="button" class="btn-primary sm" data-board-refresh ${running ? "disabled" : ""} title="Ida nach Neuem und Geändertem suchen lassen"><span class="ms ${running ? "spin" : ""}">${running ? "progress_activity" : "refresh"}</span> Neu laden</button>
        <button type="button" class="icon-btn" data-board-edit title="Thema oder Modell ändern"><span class="ms">edit</span></button>
        <button type="button" class="icon-btn" data-board-del title="Board löschen"><span class="ms">delete</span></button>
      </div>
    </div>
    ${b.status === "error" ? `<div class="board-err"><span class="ms">error</span> ${esc(b.error || "Das hat nicht geklappt")}</div>` : ""}
    <details class="board-prompt"><summary>Auftrag an Ida</summary><p>${esc(b.prompt)}</p></details>
    ${body}`;
}

async function onClick(e, root) {
  const card = e.target.closest("[data-board]");
  if (card) return select(root, card.dataset.board);
  if (e.target.closest("[data-board-back]")) {
    bs.current = null;
    return draw(root);
  }
  const b = bs.current;
  if (!b) return;
  if (e.target.closest("[data-board-refresh]")) {
    try {
      await api.post(`/api/v1/boards/${b.id}/refresh`);
      b.status = "running";
      b.error = "";
      const row = bs.list.find((x) => x.id === b.id);
      if (row) row.status = "running";
      draw(root);
      poll(root);
    } catch (err) {
      alert(err.message);
    }
  } else if (e.target.closest("[data-board-edit]")) openForm(root, b);
  else if (e.target.closest("[data-board-del]")) {
    if (!confirm(`Board „${b.title}“ wirklich löschen?`)) return;
    await api.del(`/api/v1/boards/${b.id}`).catch((err) => alert(err.message));
    bs.list = bs.list.filter((x) => x.id !== b.id);
    await select(root, bs.list[0]?.id || "");
  }
}

async function openForm(root, edit = null) {
  if (!bs.models) {
    try {
      bs.models = await api.get("/api/v1/boards/models");
    } catch {
      bs.models = { models: [], model_names: {} };
    }
  }
  const ids = bs.models.models || [];
  const cur = edit ? edit.model || "" : localStorage.getItem("wiki.board.model") || "";
  const { el, close } = openModal(`
    <div class="sheet-head"><h2><span class="ms">dashboard</span> ${edit ? "Board ändern" : "Neues Smart Board"}</h2>
      <button type="button" class="icon-btn" data-close-sheet><span class="ms">close</span></button></div>
    <form class="sheet-form" id="wkBoardForm">
      <label class="field-row">Titel (optional)<input class="wk-input" id="wkBoardTitle" maxlength="120" placeholder="z. B. Projektarbeit" value="${esc(edit?.title || "")}"></label>
      <label class="field-row">Thema oder Prompt
        <textarea class="wk-input" id="wkBoardPrompt" rows="5" required maxlength="4000" placeholder="z. B. Alles zur Projektarbeit: Termine, Absprachen mit Lehrern, offene Aufgaben">${esc(edit?.prompt || "")}</textarea></label>
      <label class="field-row">KI-Modell
        <select class="wk-input" id="wkBoardModel"><option value="">Standard</option>${ids.map((m) => `<option value="${esc(m)}" ${m === cur ? "selected" : ""}>${esc(modelName(m))}</option>`).join("")}</select></label>
      ${!bs.connected ? `<p class="sheet-hint">Die Verbindung zu Ida ist hier noch nicht eingerichtet (IDA_CHAT_URL).</p>` : ""}
      <div class="sheet-actions"><button type="submit" class="btn-primary"><span class="ms">${edit ? "save" : "auto_awesome"}</span> ${edit ? "Speichern" : "Erstellen & befüllen"}</button></div>
    </form>`, { wide: true });
  el.querySelector("#wkBoardPrompt").focus();
  el.querySelector("#wkBoardForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const body = { title: el.querySelector("#wkBoardTitle").value, prompt: el.querySelector("#wkBoardPrompt").value, model: el.querySelector("#wkBoardModel").value || null };
    try {
      if (edit) {
        const r = await api.patch(`/api/v1/boards/${edit.id}`, body);
        Object.assign(bs.list.find((x) => x.id === edit.id) || {}, r);
        Object.assign(bs.current, r);
        draw(root);
      } else {
        localStorage.setItem("wiki.board.model", body.model || "");
        const r = await api.post("/api/v1/boards", body);
        bs.list.unshift(r);
        await select(root, r.id);
      }
      close();
    } catch (err) {
      alert(err.message);
    }
  };
}
