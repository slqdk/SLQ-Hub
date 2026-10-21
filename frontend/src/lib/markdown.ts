// Markdown for answers: GitHub-flavoured, highlighted code, KaTeX maths,
// everything sanitised (the model's output is never trusted as HTML).
import { Marked, type TokenizerAndRendererExtension } from "marked";
import DOMPurify from "dompurify";
import katex from "katex";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import csharp from "highlight.js/lib/languages/csharp";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import pascal from "highlight.js/lib/languages/delphi";
import plaintext from "highlight.js/lib/languages/plaintext";
import powershell from "highlight.js/lib/languages/powershell";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

const LANGS = { bash, c, cpp, csharp, css, diff, dockerfile, go, ini, java, javascript, json, markdown, pascal,
  plaintext, powershell, python, rust, sql, typescript, xml, yaml };
for (const [name, lang] of Object.entries(LANGS)) hljs.registerLanguage(name, lang);
hljs.registerAliases(["sh", "shell", "zsh", "console"], { languageName: "bash" });
hljs.registerAliases(["html", "svg", "xaml"], { languageName: "xml" });
hljs.registerAliases(["js", "jsx", "mjs"], { languageName: "javascript" });
hljs.registerAliases(["ts", "tsx"], { languageName: "typescript" });
hljs.registerAliases(["py"], { languageName: "python" });
hljs.registerAliases(["yml"], { languageName: "yaml" });
hljs.registerAliases(["st", "iecst", "structured-text", "delphi"], { languageName: "pascal" });
hljs.registerAliases(["cs"], { languageName: "csharp" });
hljs.registerAliases(["ps1"], { languageName: "powershell" });
hljs.registerAliases(["text", "txt", "output"], { languageName: "plaintext" });

export function highlight(code: string, lang?: string | null): string {
  const l = (lang ?? "").trim().split(/\s+/)[0].toLowerCase();
  try {
    if (l && hljs.getLanguage(l)) return hljs.highlight(code, { language: l, ignoreIllegals: true }).value;
    if (code.length < 20000) return hljs.highlightAuto(code).value;
  } catch {
    /* plain below */
  }
  return escapeHtml(code);
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]!);
}

function tex(src: string, display: boolean): string {
  try {
    return katex.renderToString(src, { displayMode: display, throwOnError: false, output: "htmlAndMathml" });
  } catch {
    return escapeHtml(src);
  }
}

const mathBlock: TokenizerAndRendererExtension = {
  name: "mathBlock",
  level: "block",
  start: (src) => {
    const i = src.search(/\$\$|\\\[/);
    return i < 0 ? undefined : i;
  },
  tokenizer(src) {
    const m = /^\$\$([\s\S]+?)\$\$[ \t]*(?:\n|$)/.exec(src) ?? /^\\\[([\s\S]+?)\\\][ \t]*(?:\n|$)/.exec(src);
    if (m) return { type: "mathBlock", raw: m[0], text: m[1].trim() };
    return undefined;
  },
  renderer: (t) => `<div class="math-block">${tex(t.text, true)}</div>`,
};

const mathInline: TokenizerAndRendererExtension = {
  name: "mathInline",
  level: "inline",
  start: (src) => {
    const i = src.search(/\$|\\\(/);
    return i < 0 ? undefined : i;
  },
  tokenizer(src) {
    // $x$ but not prices ("$5 and $10"): no space just inside the dollars, no digit right after.
    const m = /^\$(?!\s)((?:\\\$|[^$\n])+?)(?<!\s)\$(?!\d)/.exec(src) ?? /^\\\((.+?)\\\)/.exec(src);
    if (m) return { type: "mathInline", raw: m[0], text: m[1] };
    return undefined;
  },
  renderer: (t) => tex(t.text, false),
};

const md = new Marked({ gfm: true, breaks: false });
md.use({
  extensions: [mathBlock, mathInline],
  renderer: {
    code({ text, lang }) {
      const l = (lang ?? "").trim().split(/\s+/)[0];
      return `<pre data-lang="${escapeHtml(l)}"><code class="hljs">${highlight(text, l)}</code></pre>`;
    },
  },
});

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

export function renderMarkdown(text: string): string {
  const html = md.parse(text, { async: false }) as string;
  return DOMPurify.sanitize(html, { ADD_ATTR: ["data-lang", "target"], FORBID_TAGS: ["style", "form", "input"] });
}
