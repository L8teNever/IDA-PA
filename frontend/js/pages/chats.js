// "/chats" – all conversations, grouped by day, searchable (?q=).
import { api } from "../api.js";
import { setUrl } from "../router.js";
import { agentOf, colors, face, mainAgent } from "../store.js";
import { $, attempt, closeSheet, enableSwipe, esc, fmtRel, openSheet } from "../ui/dom.js";
import { setAgent, setView } from "../ui/shell.js";

export async function chatsPage(_, query) {
  setAgent(mainAgent());
  setView("page");
  $("page").innerHTML = `
    <div class="page-head"><h1>Chats</h1></div>
    <a class="big-new" href="/"><span class="ms">edit_square</span>Neuer Chat</a>
    <div class="search"><span class="ms">search</span><input id="chatSearch" placeholder="Chats durchsuchen" value="${esc(query.q || "")}"></div>
    <div id="chatPageList"></div>
    <p class="hint">Über ⋮ umbenennen oder anpinnen, zum Löschen eine Zeile zur Seite wischen.</p>`;
  let t;
  $("chatSearch").oninput = () => {
    clearTimeout(t);
    t = setTimeout(() => { const q = $("chatSearch").value.trim(); setUrl(q ? `/chats?q=${encodeURIComponent(q)}` : "/chats"); load(q); }, 250);
  };
  await load(query.q || "");
}

async function load(q) {
  const list = await api.get(`/chats?q=${encodeURIComponent(q)}`);
  const box = $("chatPageList");
  if (!box) return;
  if (!list.length) { box.innerHTML = `<div class="empty">${q ? "Nichts gefunden." : "Noch keine Chats."}</div>`; return; }
  const today = new Date().toDateString();
  const weekAgo = Date.now() - 7 * 864e5;
  const groups = [["Angepinnt", []], ["Heute", []], ["Diese Woche", []], ["Älter", []]];
  for (const c of list) {
    const d = new Date(c.updated_at);
    groups[c.pinned ? 0 : d.toDateString() === today ? 1 : d.getTime() > weekAgo ? 2 : 3][1].push(c);
  }
  box.innerHTML = groups.filter((g) => g[1].length).map(([name, items]) => `
    <div class="grp">${name}</div>
    <div class="clist">${items.map((c) => {
      const a = agentOf(c.agent_id), col = colors(a);
      const last = c.last ? (c.last.role === "user" ? "Du: " : a.name + ": ") + c.last.text.split("\n")[0] : "";
      return `<a class="crow" href="/chat/${c.id}" data-id="${c.id}">
        <span class="ag-dot" style="background:${col["--container"]};color:${col["--accent"]}">${face(a)}</span>
        <span class="cmain"><span class="ctop"><b>${c.pinned ? `<span class="ms c-pin" title="Angepinnt">keep</span>` : ""}${esc(c.title)}</b><span class="ctime">${fmtRel(c.updated_at)}</span></span>
        <small>${c.running ? `<span class="c-running"><span class="ms spin">progress_activity</span>${esc(a.name)} schreibt gerade …</span>` : esc(last)}</small></span>
        <button type="button" class="icon-btn c-more" data-more="${c.id}" aria-label="Umbenennen oder anpinnen"><span class="ms">more_vert</span></button></a>`;
    }).join("")}</div>`).join("");
  const byId = Object.fromEntries(list.map((c) => [c.id, c]));
  box.querySelectorAll("[data-more]").forEach((b) => {
    b.onpointerdown = (e) => e.stopPropagation(); // kein Wischen beim Tippen auf ⋮
    b.onclick = (e) => { e.preventDefault(); e.stopPropagation(); editChat(byId[b.dataset.more], () => load(q)); };
  });
  box.querySelectorAll(".crow").forEach((el) => enableSwipe(el, {
    onSwipe: async () => {
      if (!confirm("Chat löschen?")) { el.style.transform = ""; el.style.opacity = ""; return; }
      await attempt(() => api.del(`/chats/${el.dataset.id}`), "Chat gelöscht");
      load(q);
    },
  }));
}

// Umbenennen, anpinnen (steht dann oben) oder löschen
function editChat(c, done) {
  if (!c) return;
  openSheet(`<h2 class="sh-title">Chat</h2>
    <label class="fld"><span>Titel</span><input id="ceTitle" maxlength="120" value="${esc(c.title)}" autocomplete="off"></label>
    <label class="tool-item"><input type="checkbox" id="cePin" ${c.pinned ? "checked" : ""}><span>Angepinnt – steht oben in der Liste</span></label>
    <div id="ceErr" class="ferr"></div>
    <div class="wiz-nav"><button class="es-btn danger" id="ceDel"><span class="ms">delete</span>Löschen</button>
      <button class="es-btn primary" id="ceSave"><span class="ms">check</span>Speichern</button></div>`);
  const title = $("ceTitle");
  title.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("ceSave").click(); } };
  $("ceSave").onclick = async () => {
    const body = {};
    if (title.value.trim() !== c.title) body.title = title.value;
    if ($("cePin").checked !== !!c.pinned) body.pinned = $("cePin").checked;
    if (!Object.keys(body).length) return closeSheet();
    try {
      await api.patch(`/chats/${c.id}`, body);
      closeSheet();
      done();
    } catch (err) { $("ceErr").textContent = err.message; }
  };
  $("ceDel").onclick = async () => {
    if (!confirm("Chat löschen?")) return;
    if (await attempt(() => api.del(`/chats/${c.id}`), "Chat gelöscht")) { closeSheet(); done(); }
  };
}
