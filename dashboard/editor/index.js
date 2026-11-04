// Code-Editor des Tools „Dateien“: CodeMirror 6, lokal gebündelt (kein CDN). Neu bauen: siehe package.json, Skript „build“.
import { EditorView, basicSetup } from "codemirror";
import { Compartment, EditorState } from "@codemirror/state";
import { keymap } from "@codemirror/view";
import { indentWithTab } from "@codemirror/commands";
import { StreamLanguage } from "@codemirror/language";
import { openSearchPanel, search } from "@codemirror/search";
import { oneDark } from "@codemirror/theme-one-dark";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { sql } from "@codemirror/lang-sql";
import { xml } from "@codemirror/lang-xml";
import { yaml } from "@codemirror/lang-yaml";
import { dockerFile } from "@codemirror/legacy-modes/mode/dockerfile";
import { diff } from "@codemirror/legacy-modes/mode/diff";
import { nginx } from "@codemirror/legacy-modes/mode/nginx";
import { properties } from "@codemirror/legacy-modes/mode/properties";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import { toml } from "@codemirror/legacy-modes/mode/toml";

const stream = (m) => () => StreamLanguage.define(m);
const LANGS = [
  ["JavaScript", /\.(m|c)?jsx?$/i, () => javascript({ jsx: true })],
  ["TypeScript", /\.(m|c)?tsx?$/i, () => javascript({ jsx: true, typescript: true })],
  ["JSON", /\.(json|jsonc|webmanifest|map)$/i, json],
  ["Python", /\.pyw?$/i, python],
  ["HTML", /\.(html?|vue|svelte)$/i, html],
  ["CSS", /\.(css|scss|less)$/i, css],
  ["Markdown", /\.(md|markdown|mdx)$/i, markdown],
  ["YAML", /\.ya?ml$/i, yaml],
  ["XML", /\.(xml|svg|plist|xsd|xsl)$/i, xml],
  ["SQL", /\.sql$/i, sql],
  ["Dockerfile", /(^|\/)(Dockerfile|Containerfile)[^/]*$|\.dockerfile$/i, stream(dockerFile)],
  ["Shell", /\.(sh|bash|zsh|ksh)$|(^|\/)\.(bashrc|profile|bash_profile|bash_aliases|zshrc)$/i, stream(shell)],
  ["nginx", /nginx.*\.conf$|\/nginx\/.+$/i, stream(nginx)],
  ["TOML", /\.toml$/i, stream(toml)],
  ["Diff", /\.(diff|patch)$/i, stream(diff)],
  ["Konfiguration", /(^|\/)\.env[^/]*$|\.(env|ini|conf|cfg|properties|service|timer)$/i, stream(properties)],
];

const look = EditorView.theme({
  "&": { height: "100%", fontSize: "13.5px", backgroundColor: "transparent" },
  ".cm-scroller": { fontFamily: "var(--mono, ui-monospace, monospace)", lineHeight: "1.55" },
  ".cm-gutters": { backgroundColor: "transparent", border: "none", color: "#5f6470" },
  ".cm-activeLineGutter, .cm-activeLine": { backgroundColor: "rgba(255,255,255,.04)" },
  ".cm-panels": { backgroundColor: "#1d1f23", color: "#e3e2e6" },
  ".cm-panels.cm-panels-top": { borderBottom: "1px solid rgba(255,255,255,.08)" },
  ".cm-panel.cm-search": { padding: "8px 10px", fontFamily: "inherit" },
  ".cm-textfield": { borderRadius: "10px", border: "1px solid rgba(255,255,255,.14)", background: "#121316", color: "inherit", padding: "4px 8px" },
  ".cm-button": { borderRadius: "10px", border: "none", backgroundImage: "none", backgroundColor: "#282a2f", color: "inherit", padding: "4px 10px" },
}, { dark: true });

export function languageOf(name) {
  return LANGS.find(([, re]) => re.test(name)) || null;
}

export function createEditor({ parent, doc, filename, readOnly = false, onChange, onSave }) {
  const wrap = new Compartment();
  const lang = languageOf(filename);
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc,
      extensions: [
        basicSetup,
        oneDark,
        look,
        search({ top: true }),
        lang ? lang[2]() : [],
        wrap.of([]),
        EditorState.readOnly.of(readOnly),
        keymap.of([indentWithTab, { key: "Mod-s", preventDefault: true, run: () => { onSave?.(); return true; } }]),
        EditorView.updateListener.of((u) => { if (u.docChanged) onChange?.(); }),
      ],
    }),
  });
  return {
    view,
    language: lang ? lang[0] : "Text",
    value: () => view.state.doc.toString(),
    setValue: (text) => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } }),
    setWrap: (on) => view.dispatch({ effects: wrap.reconfigure(on ? EditorView.lineWrapping : []) }),
    find: () => { view.focus(); openSearchPanel(view); },
    focus: () => view.focus(),
    position: () => {
      const head = view.state.selection.main.head, line = view.state.doc.lineAt(head);
      return { line: line.number, col: head - line.from + 1, lines: view.state.doc.lines };
    },
    destroy: () => view.destroy(),
  };
}

