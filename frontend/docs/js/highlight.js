// Einfärbung des Markdown-Editors. Liefert pro Zeile ein <div>, das hinter dem (durchsichtigen) Textfeld liegt.
// Nur Farben und Hintergründe, nie Schriftgewicht oder -größe: Sonst laufen Einfärbung und Textfeld auseinander.
// Erkennt dieselben Muster wie render() in markdown.js – ändert sich dort die Syntax, muss sie hier mit.
import { stripComment, noteRanges } from "./markdown.js";

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const span = (c, s) => `<span class="${c}">${esc(s)}</span>`;
const REGIONS = { title: "Titelblatt", center: "Zentriert", right: "Rechtsbündig", left: "Linksbündig", justify: "Blocksatz", entwurf: "Entwurf" };
const OPEN = new RegExp(`^:::\\s*(${Object.keys(REGIONS).join("|")})\\s*$`);

const INLINE = /(`[^`]+`)|(\[\[[^\]]*\]\])|(\[@[^\]\n]+\])|(\[\^[^\]\s]+\])|(!?\[[^\]\n]*\]\([^)\s]+\))|(\*\*[^*]+?\*\*|__[^_]+?__)|((?<![*\w])\*[^*\s][^*]*?\*(?!\w)|(?<![_\w])_[^_\s][^_]*?_(?!\w))|(~~[^~]+~~)/g;
const KINDS = ["t-code", "t-note", "t-cite", "t-cite", "t-link", "t-b", "t-i", "t-s"];
function inl(s) {
  let out = "", last = 0;
  for (const m of s.matchAll(INLINE)) {
    out += esc(s.slice(last, m.index)) + span(KINDS[m.slice(1).findIndex((x) => x !== undefined)], m[0]);
    last = m.index + m[0].length;
  }
  return out + esc(s.slice(last));
}

const line = (cls, html, tag) => `<div${cls ? ` class="${cls}"` : ""}${tag ? ` data-tag="${tag}"` : ""}>${html || "<br>"}</div>`;
const COMMANDS = [[/^\\(newpage|pagebreak)\s*$/i, ""], [/^\[TOC\]\s*$/i, "Inhaltsverzeichnis"], [/^\[(QUELLEN|LITERATUR)\]\s*$/i, "Quellenverzeichnis"]];

/** @returns {string[]} eine HTML-Zeile je Textzeile */
export function highlightLines(text) {
  let fence = false, region = null;
  const lines = text.replace(/\r\n?/g, "\n").split("\n"), notes = noteRanges(lines);
  return lines.map((l, i) => {
    // mehrzeilige Notiz: Der Bereich ist grau hinterlegt, auch über leere Zeilen hinweg. Einzeilige erledigt inl().
    const multi = notes[i].some(([a, b]) => l.slice(a, a + 2) !== "[[" || l.slice(b - 2, b) !== "]]");
    if (multi) {
      let html = "", p = 0;
      for (const [a, b] of notes[i]) { html += inl(l.slice(p, a)) + (b > a ? span("t-note", l.slice(a, b)) : ""); p = b; }
      return line(`${region ? `l-reg r-${region} ` : ""}l-note`, html + inl(l.slice(p)), notes[i][0][0] > 0 || l.startsWith("[[") ? (l.includes("[[") ? "Notiz" : "") : "");
    }
    if (/^```/.test(l)) { fence = !fence; return line("l-code", span("t-mk", l)); }
    if (fence) return line("l-code", esc(l));
    const c = stripComment(l), rest = l.slice(c.length), com = rest ? span("t-com", rest) : "";
    let m;
    if (region && /^:::\s*$/.test(c)) { const r = region; region = null; return line(`l-reg r-${r}`, span("t-mk", c) + com); }
    if (!region && (m = c.match(OPEN))) { region = m[1]; return line(`l-reg r-${region}`, span("t-mk", c) + com, REGIONS[region]); }
    const reg = region ? `l-reg r-${region} ` : "";
    if (!c && rest) return line(reg.trim(), com);
    for (const [re, tag] of COMMANDS) if (re.test(c)) return line(`${reg}l-cmd`, esc(c) + com, tag);
    if ((m = c.match(/^(#{1,4})(\s+)(.*)$/))) return line(`${reg}l-h`, span("t-mk", m[1] + m[2]) + inl(m[3]) + com);
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(c)) return line(`${reg}l-dim`, esc(c) + com);
    if ((m = c.match(/^(>+\s?)(.*)$/))) return line(`${reg}l-q`, span("t-mk", m[1]) + inl(m[2]) + com);
    if ((m = c.match(/^(\[\^[^\]\s]+\]:)(.*)$/))) return line(reg.trim(), span("t-cite", m[1]) + inl(m[2]) + com);
    if (/^\s*\|/.test(c)) return line(`${reg}l-tb`, inl(c).replace(/\|/g, '<span class="t-mk">|</span>') + com);
    if ((m = c.match(/^(\s*(?:[-*+]|\d+[.)])\s+)(.*)$/))) return line(reg.trim(), span("t-mk", m[1]) + inl(m[2]) + com);
    return line(reg.trim(), inl(c) + com);
  });
}
