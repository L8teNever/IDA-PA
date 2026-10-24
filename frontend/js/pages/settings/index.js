// "/einstellungen[/:section[/:id]]" – list on the left, details on the right (one column on phones).
import { api } from "../../api.js";
import { colors, mainAgent, PALETTE, state } from "../../store.js";
import { $, esc } from "../../ui/dom.js";
import { setAgent, setView } from "../../ui/shell.js";
import * as accounts from "./accounts.js";
import * as agentsSec from "./agents.js";
import * as mcp from "./mcp.js";
import * as memory from "./memory.js";
import * as skills from "./skills.js";

// id, icon, title, subtitle(), palette, module
const SECTIONS = [
  ["KI", [
    ["agents", "groups", "Agents", () => state.boot.agents.map((a) => a.name).join(", "), "blau", agentsSec],
    ["mcp", "extension", "MCP-Server", () => "Verbindungen und Tools", "mint", mcp],
    ["skills", "auto_fix_high", "Skills", () => `${state.boot.skills.length} aktiv`, "gelb", skills],
    ["gedaechtnis", "psychology", "Gedächtnis", () => "Was die Agents sich merken", "lila", memory],
  ]],
  ["App", [
    ["accounts", "person", "Claude-Accounts", () => `${state.boot.accounts.length} verbunden`, "orange", accounts],
  ]],
];
const flat = SECTIONS.flatMap((g) => g[1]);
const isWide = () => matchMedia("(min-width: 900px)").matches;

export function subHead(title, back) {
  return `<div class="sub-head"><a class="icon-btn back" href="${back}" aria-label="Zurück"><span class="ms">arrow_back</span></a><h1>${esc(title)}</h1></div>`;
}

export async function settingsPage({ section, id } = {}) {
  setAgent(mainAgent());
  setView("page");
  $("page").classList.add("page-wide");
  if (!section && isWide()) section = "agents";
  $("page").innerHTML = `<div class="set-wrap ${section ? "insub" : ""}" id="setWrap">
      <div class="set-list"><div class="page-head"><h1>Einstellungen</h1></div><div id="setList"></div></div>
      <div class="set-detail" id="setDetail"></div></div>`;
  $("setList").innerHTML = SECTIONS.map(([g, rows]) => `<div class="sgrp">${g}</div><div class="slist">${rows.map(([sid, ic, t, sub, col]) => {
    const c = PALETTE[col];
    return `<a class="srow ${section === sid ? "sel" : ""}" href="/einstellungen/${sid}">
      <span class="sic" style="background:${c["--container"]};color:${c["--accent"]}"><span class="ms">${ic}</span></span>
      <span class="smain"><b>${t}</b><small>${esc(sub())}</small></span><span class="ms schev">chevron_right</span></a>`;
  }).join("")}</div>`).join("");
  const sec = flat.find((x) => x[0] === section);
  if (sec) await sec[5].render($("setDetail"), id);
}

