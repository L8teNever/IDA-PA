// Ergänzt angefangene Syntax: Der Editor zeigt den Rest grau an, Tab übernimmt ihn. Reine Textlogik, ohne Zugriff auf die Seite.
import { stripComment } from "./markdown.js";

const KINDS = ["center", "right", "left", "justify", "title", "entwurf"];
const COMMANDS = ["[TOC]", "[QUELLEN]"];
const PAIRS = { "[": "]", "(": ")", "{": "}", "„": "“" };
const CLOSERS = new Set(Object.values(PAIRS));

// Offene Klammern und Hervorhebungen der Zeile bis zum Cursor. Das zuletzt geöffnete steht oben.
function openTokens(b) {
  const st = [], top = () => st.at(-1)?.close;
  const toggle = (tok, i) => { if (top() === tok) st.pop(); else st.push({ close: tok, end: i + tok.length }); };
  for (let i = 0; i < b.length; i++) {
    const c = b[i], two = b.slice(i, i + 2);
    if (c === "`") { toggle("`", i); continue; }
    if (top() === "`") continue;
    if (two === "[[") { st.push({ close: "]]", end: i + 2 }); i++; }
    else if (two === "]]" && top() === "]]") { st.pop(); i++; }
    else if (two === "**" || two === "~~") { toggle(two, i); i++; }
    else if (two === "__") { if (top() === "__" || !/\w/.test(b[i - 1] || "")) toggle(two, i); i++; }
    else if (c === "*") {
      const space = /\s/.test(b[i + 1] || " ");
      if (top() === "*" ? !/\s/.test(b[i - 1] || " ") : !space || i === b.length - 1 && !/^\s*$/.test(b.slice(0, i))) toggle("*", i);
    }
    else if (PAIRS[c]) st.push({ close: PAIRS[c], end: i + 1 });
    else if (CLOSERS.has(c) && top() === c) st.pop();
  }
  return st;
}

/** @returns {{text:string, show:string, caret:number}|null} text = was eingefügt wird, caret = Cursor danach, relativ zum Einfügepunkt */
export function suggest(before, after, below) {
  if (stripComment(before) !== before) return null;
  const done = (text, caret = text.length, show = text) => ({ text, show, caret });
  let m;
  if ((m = before.match(/^:::[ \t]*([a-z]+)$/))) {
    if (KINDS.includes(m[1])) {
      if (after.trim()) return null;
      const next = below.split("\n").find((l) => /^:::/.test(l));   // schon geschlossen?
      return next && /^:::\s*$/.test(next) ? null : done("\n\n:::", 1, " ↵ :::");
    }
    const k = KINDS.find((x) => x.startsWith(m[1]));
    return k ? done(k.slice(m[1].length)) : null;
  }
  if (/^\[[a-z]+$/i.test(before)) {
    const c = COMMANDS.find((x) => x.startsWith(before.toUpperCase()));
    if (c) return after.trim() ? null : done(c.slice(before.length));
  }
  if (after && !/^[\s.,;:!?)\]}“]/.test(after)) return null;
  const t = openTokens(before).at(-1);
  if (!t || after.startsWith(t.close)) return null;
  const empty = t.end === before.length;
  if (t.close === "}" && /^#{1,4}\s/.test(before)) { if (empty) return done("-}"); }
  if (t.close === "]]" && empty) return done("Notiz: ]]", 7);
  return done(t.close, empty ? 0 : t.close.length);
}
