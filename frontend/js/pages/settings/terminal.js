// Einstellungen → Claude-Accounts → /einstellungen/accounts/:id – a real terminal with the Claude CLI of the
// account (like logging in in T3 Code: `claude auth login`). xterm.js in the browser, a PTY on the server.
// The CLI keeps running on the server when the page/socket goes away (e.g. switching to Claude on the phone);
// coming back reconnects and shows what happened meanwhile.
import { api } from "../../api.js";
import { loadBoot } from "../../store.js";
import { $, esc, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

let loaded;
function loadXterm() {
  const add = (tag, attrs) => new Promise((ok, fail) => {
    const e = Object.assign(document.createElement(tag), attrs);
    e.onload = ok; e.onerror = fail;
    document.head.appendChild(e);
  });
  loaded ||= Promise.all([
    add("link", { rel: "stylesheet", href: new URL("../../../vendor/xterm/xterm.css", import.meta.url).href }),
    add("script", { src: new URL("../../../vendor/xterm/xterm.js", import.meta.url).href }),
  ]).then(() => Promise.all([
    add("script", { src: new URL("../../../vendor/xterm/addon-fit.js", import.meta.url).href }),
    add("script", { src: new URL("../../../vendor/xterm/addon-web-links.js", import.meta.url).href }),
  ]));
  return loaded;
}

let current = null;
export function closeTerminal() {
  if (!current) return;
  current.closed = true;
  current.ws?.close();
  current.term?.dispose();
  removeEventListener("resize", current.onResize);
  current = null;
}
// leaving the page ends only the view, never the CLI on the server
addEventListener("popstate", () => { if (current && !location.pathname.startsWith(current.path)) closeTerminal(); });

const KEYS = { enter: "\r", up: "\x1b[A", down: "\x1b[B", esc: "\x1b", ctrlc: "\x03" };
const dot = (ok, text) => `<span class="sdot" style="background:${ok ? "#7ee2b8" : "#ff8f8f"}"></span>${esc(text)}`;

export async function terminalView(el, id) {
  closeTerminal();
  const a = (await api.get("/accounts")).find((x) => x.id === id);
  if (!a) {
    el.innerHTML = subHead("Accounts", "/einstellungen/accounts") + `<div class="empty">Account nicht gefunden.</div>`;
    return;
  }
  const info = await api.get("/terminal");
  const agy = a.provider === "antigravity";
  el.innerHTML = subHead(`${a.name} – Terminal`, "/einstellungen/accounts") + `
    <div class="term-status" id="tStatus">${dot(a.logged_in, a.logged_in ? "angemeldet" : "nicht angemeldet")}</div>
    <div class="seg term-cmds" id="tCmds">
      <button data-cmd="login" class="on"><span class="ms">login</span>anmelden</button>
      <button data-cmd="status"><span class="ms">info</span>${agy ? "Modelle" : "Status"}</button>
      ${agy ? "" : `<button data-cmd="logout"><span class="ms">logout</span>abmelden</button>`}
    </div>
    <div class="term-box" id="tBox"></div>
    <div class="term-keys" id="tKeys">
      <button data-key="paste"><span class="ms">content_paste</span>Einfügen</button>
      <button data-key="enter">Enter</button>
      <button data-key="up">↑</button><button data-key="down">↓</button>
      <button data-key="esc">Esc</button><button data-key="ctrlc">Strg+C</button>
    </div>
    <p class="sub-intro" style="margin-top:12px">${agy ? `Hier läuft Googles <span class="mono">agy</span>.
      Link im Terminal antippen → mit Google anmelden → Code kopieren → „Einfügen“ → Enter. Danach zeigt agy seinen eigenen Chat – mit „Strg+C“ beenden, dann erscheinen die Modelle in IDA.` : `Wie in T3 Code läuft hier <span class="mono">claude auth login</span>.
      Link im Terminal antippen → bei Claude einloggen und „Autorisieren“ → Code kopieren → „Einfügen“ → Enter.`}
      Du kannst zwischendurch wechseln: der Login läuft auf dem Server weiter. Danach bleibt der Account dauerhaft angemeldet.</p>`;
  if (!info.available) { $("tBox").innerHTML = `<div class="empty">Das Terminal gibt es nur im Docker-Container.</div>`; return; }
  try { await loadXterm(); } catch { $("tBox").textContent = "Terminal konnte nicht geladen werden."; return; }

  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#7fd3ff";
  const term = new Terminal({ fontFamily: "ui-monospace, Menlo, Consolas, monospace", fontSize: 14, cursorBlink: true,
    theme: { background: "#121316", foreground: "#e3e2e6", cursor: accent } });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.loadAddon(new WebLinksAddon.WebLinksAddon((_e, uri) => window.open(uri, "_blank", "noopener")));
  term.open($("tBox"));
  fit.fit();

  const me = { path: `/einstellungen/accounts/${id}`, term, ws: null, closed: false, finished: false };
  current = me;
  const send = (m) => { if (me.ws?.readyState === 1) me.ws.send(JSON.stringify(m)); };
  me.onResize = () => { fit.fit(); send({ t: "resize", cols: term.cols, rows: term.rows }); };
  addEventListener("resize", me.onResize);
  term.onData((d) => send({ t: "in", d }));

  let cmd = "login", retry = 0;
  const connect = () => {
    if (me.closed) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/v1/accounts/${id}/terminal?cmd=${cmd}&cols=${term.cols}&rows=${term.rows}`);
    me.ws = ws;
    ws.onopen = () => { retry = 0; };
    ws.onmessage = async (ev) => {
      const m = JSON.parse(ev.data);
      if (m.t === "out") term.write(m.d);
      else if (m.t === "error") term.write(`\r\n\x1b[31m${m.d}\x1b[0m\r\n`);
      else if (m.t === "exit") {
        me.finished = true;
        term.write(`\r\n\x1b[90m[beendet${m.code ? " mit Code " + m.code : ""}]\x1b[0m\r\n`);
        $("tStatus").innerHTML = dot(m.logged_in, m.logged_in ? "angemeldet ✓ (dauerhaft gespeichert)" : "nicht angemeldet");
        if (m.logged_in && cmd === "login") { toast(`${a.name} ist angemeldet`); loadBoot(); }
      }
    };
    // phone switched apps / network blip: reconnect – the CLI kept running on the server
    ws.onclose = () => { if (!me.closed && !me.finished && retry < 30) setTimeout(connect, Math.min(500 * ++retry, 4000)); };
  };
  const run = (c) => { cmd = c; me.finished = false; me.ws?.close(); term.reset(); connect(); };

  $("tCmds").onclick = (e) => {
    const b = e.target.closest("[data-cmd]");
    if (!b) return;
    $("tCmds").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    run(b.dataset.cmd);
  };
  $("tKeys").onclick = async (e) => {
    const k = e.target.closest("[data-key]")?.dataset.key;
    if (!k) return;
    if (k === "paste") {
      try {
        const t = await navigator.clipboard.readText();
        if (t) send({ t: "in", d: t.trim() });
      } catch { toast("Kein Zugriff auf die Zwischenablage – lange ins Terminal tippen und einfügen"); }
    } else send({ t: "in", d: KEYS[k] });
    term.focus();
  };
  // the browser's own back/forward or a link click elsewhere ends the view
  document.addEventListener("click", function leave(e) {
    const link = e.target.closest("a[href^='/']");
    if (link && !link.getAttribute("href").startsWith(me.path)) { closeTerminal(); document.removeEventListener("click", leave, true); }
  }, true);
  connect();
  term.focus();
}
