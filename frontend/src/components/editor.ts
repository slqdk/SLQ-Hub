// CodeMirror for the file editor, loaded only when a file is opened.
import { EditorState, type Extension } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { indentWithTab } from "@codemirror/commands";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { sql } from "@codemirror/lang-sql";
import { xml } from "@codemirror/lang-xml";
import { yaml } from "@codemirror/lang-yaml";

function language(path: string): Extension[] {
  const ext = path.toLowerCase().split(".").pop() ?? "";
  if (["js", "mjs", "cjs", "jsx"].includes(ext)) return [javascript({ jsx: true })];
  if (["ts", "tsx"].includes(ext)) return [javascript({ jsx: ext === "tsx", typescript: true })];
  if (ext === "py") return [python()];
  if (["json", "jsonc"].includes(ext)) return [json()];
  if (["md", "markdown"].includes(ext)) return [markdown()];
  if (["html", "htm"].includes(ext)) return [html()];
  if (["css", "scss"].includes(ext)) return [css()];
  if (["xml", "svg", "xaml", "csproj", "props", "targets"].includes(ext)) return [xml()];
  if (ext === "sql") return [sql()];
  if (["yml", "yaml"].includes(ext)) return [yaml()];
  return [];
}

const theme = EditorView.theme({
  "&": { backgroundColor: "var(--sheet)", color: "var(--ink)", height: "100%" },
  ".cm-gutters": { backgroundColor: "var(--paper)", color: "var(--ink-3)", borderRight: "1px solid var(--rule-2)" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--act-wash) 55%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--act-wash)" },
  ".cm-cursor": { borderLeftColor: "var(--ink)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { backgroundColor: "var(--act-wash) !important" },
});

export function createEditor(parent: HTMLElement, doc: string, path: string, onChange: (text: string) => void,
  onSave: () => void): EditorView {
  return new EditorView({
    parent,
    state: EditorState.create({
      doc,
      extensions: [
        basicSetup,
        keymap.of([indentWithTab, { key: "Mod-s", preventDefault: true, run: () => (onSave(), true) }]),
        theme,
        EditorView.lineWrapping,
        ...language(path),
        EditorView.updateListener.of((u) => u.docChanged && onChange(u.state.doc.toString())),
      ],
    }),
  });
}
