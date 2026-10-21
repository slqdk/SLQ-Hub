// The side panel for an artifact: a sandboxed live preview, the source,
// versions, and saving it into the chat's folder.
import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Copy, Download, Save, X } from "lucide-react";
import { api, type Artifact } from "../lib/api";
import { escapeHtml, highlight } from "../lib/markdown";
import { useApp } from "../state";
import { Markdown } from "./Markdown";
import { copyText, Modal } from "./ui";

const RUNNABLE = new Set(["html", "react", "svg", "mermaid"]);
const EXT: Record<string, string> = { html: "html", react: "jsx", svg: "svg", mermaid: "mmd", markdown: "md", code: "txt" };

export function artifactDoc(type: string, content: string): string {
  const head = `<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">`;
  const showErrors = `<script>window.addEventListener("error", function (e) {
    var p = document.createElement("pre");
    p.style.cssText = "color:#c22b2b;white-space:pre-wrap;padding:12px;margin:0;font:13px ui-monospace,monospace";
    p.textContent = "Error: " + (e.message || e.error);
    document.body.prepend(p);
  });</script>`;
  if (type === "html") {
    if (/<html[\s>]|<!doctype/i.test(content)) return content;
    return `<!doctype html><html><head>${head}${showErrors}<script src="/artifact-runtime/tailwind.js"></script>
<style>body{font-family:system-ui,sans-serif;margin:16px;color:#20262a}</style></head><body>${content}</body></html>`;
  }
  if (type === "svg") {
    return `<!doctype html><html><head>${head}<style>html,body{margin:0;height:100%}body{display:grid;place-items:center}
svg{max-width:100%;max-height:100vh;height:auto}</style></head><body>${content}</body></html>`;
  }
  if (type === "mermaid") {
    return `<!doctype html><html><head>${head}${showErrors}<script src="/artifact-runtime/mermaid.js"></script>
<style>body{margin:16px;font-family:system-ui,sans-serif;display:flex;justify-content:center}</style></head>
<body><pre class="mermaid">${escapeHtml(content)}</pre>
<script>mermaid.initialize({ startOnLoad: true, theme: "neutral", securityLevel: "strict" });</script></body></html>`;
  }
  // react: compile in the frame with Babel, imports from "react" only
  return `<!doctype html><html><head>${head}${showErrors}
<script src="/artifact-runtime/react.js"></script><script src="/artifact-runtime/react-dom.js"></script>
<script src="/artifact-runtime/babel.js"></script><script src="/artifact-runtime/tailwind.js"></script>
<style>body{margin:0;font-family:system-ui,sans-serif;color:#20262a}</style></head>
<body><div id="root"></div><script>
(function () {
  var src = ${JSON.stringify(content).replace(/</g, "\\u003c")};
  try {
    var out = Babel.transform(src, { filename: "artifact.tsx", presets: [["react", { runtime: "classic" }],
      ["typescript", { isTSX: true, allExtensions: true }]], plugins: ["transform-modules-commonjs"] }).code;
    var module = { exports: {} };
    var req = function (name) {
      if (name === "react") return React;
      if (name === "react-dom" || name === "react-dom/client") return ReactDOM;
      throw new Error("An artifact can only import from react (it tried " + name + ")");
    };
    new Function("module", "exports", "require", "React", out)(module, module.exports, req, React);
    var App = module.exports.default || Object.values(module.exports).find(function (v) { return typeof v === "function"; });
    if (!App) throw new Error("The component needs a default export");
    ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(App));
  } catch (e) {
    var p = document.createElement("pre");
    p.style.cssText = "color:#c22b2b;white-space:pre-wrap;padding:12px;margin:0;font:13px ui-monospace,monospace";
    p.textContent = String(e && e.message || e);
    document.body.prepend(p);
  }
})();
</script></body></html>`;
}

export function ArtifactPanel({ chatId, id, version, onClose }: { chatId: number; id: string; version?: number;
  onClose: () => void }) {
  const { toast } = useApp();
  const [art, setArt] = useState<Artifact | null>(null);
  const [v, setV] = useState<number | undefined>(version);
  const [view, setView] = useState<"preview" | "code">("preview");
  const [saving, setSaving] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => setV(version), [version, id]);
  useEffect(() => {
    let alive = true;
    api<Artifact>(`/hub/api/chats/${chatId}/artifacts/${encodeURIComponent(id)}`, { query: { version: v } })
      .then((a) => alive && setArt(a), (e) => toast((e as Error).message, "fault"));
    return () => {
      alive = false;
    };
  }, [chatId, id, v, toast]);

  const doc = useMemo(() => (art && RUNNABLE.has(art.type) ? artifactDoc(art.type, art.content) : ""), [art]);
  if (!art) return <aside className="panel"><div className="panel-head"><h2>Loading</h2>
    <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button></div></aside>;

  const runnable = RUNNABLE.has(art.type);
  const fileName = `${art.id}.${EXT[art.type] ?? "txt"}`;
  const download = () => {
    const blob = new Blob([art.content], { type: art.type === "html" ? "text/html" : "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fileName;
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };

  return (
    <aside className="panel" aria-label="Artifact">
      <div className="panel-head">
        <h2 title={art.title}>{art.title}</h2>
        {art.latest > 1 && (
          <span className="row" style={{ gap: 0 }}>
            <button className="icon-btn small" disabled={art.version <= 1} onClick={() => setV(art.version - 1)}
              aria-label="Earlier version"><ChevronLeft size={16} /></button>
            <span className="small muted num">v{art.version} of {art.latest}</span>
            <button className="icon-btn small" disabled={art.version >= art.latest} onClick={() => setV(art.version + 1)}
              aria-label="Later version"><ChevronRight size={16} /></button>
          </span>
        )}
        <button className="icon-btn" onClick={async () => toast((await copyText(art.content)) ? "Copied" : "Copying failed", "run")}
          aria-label="Copy the source"><Copy size={16} /></button>
        <button className="icon-btn" onClick={download} aria-label="Download"><Download size={16} /></button>
        <button className="icon-btn" onClick={() => setSaving(true)} aria-label="Save to the folder"><Save size={16} /></button>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button>
      </div>
      {runnable && (
        <div className="panel-tabs" role="tablist">
          <button className="tab" role="tab" aria-selected={view === "preview"} onClick={() => setView("preview")}>Preview</button>
          <button className="tab" role="tab" aria-selected={view === "code"} onClick={() => setView("code")}>Source</button>
          <span className="grow" />
          {view === "preview" && <button className="btn quiet small" onClick={() => setReloadKey((k) => k + 1)}>Reload</button>}
        </div>
      )}
      <div className="panel-body">
        {runnable && view === "preview" ? (
          <iframe key={`${art.version}-${reloadKey}`} className="artifact-frame" title={art.title} srcDoc={doc}
            sandbox="allow-scripts allow-popups allow-forms allow-modals allow-downloads" />
        ) : art.type === "markdown" ? (
          <div style={{ padding: "18px 22px" }}><Markdown text={art.content} /></div>
        ) : (
          <pre className="code-view"><code className="hljs" dangerouslySetInnerHTML={{
            __html: highlight(art.content, art.type === "react" ? "tsx" : art.type === "code" ? null : art.type) }} /></pre>
        )}
      </div>
      {saving && <SaveArtifact chatId={chatId} id={art.id} version={art.version} suggested={fileName}
        onClose={() => setSaving(false)} />}
    </aside>
  );
}

function SaveArtifact({ chatId, id, version, suggested, onClose }: { chatId: number; id: string; version: number;
  suggested: string; onClose: () => void }) {
  const { toast } = useApp();
  const [path, setPath] = useState(suggested);
  const save = async () => {
    try {
      const r = await api<{ path: string }>(`/hub/api/chats/${chatId}/artifacts/${encodeURIComponent(id)}/save`,
        { body: { path, version } });
      toast(`Saved as ${r.path}`, "run");
      onClose();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <Modal title="Save to the folder" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" onClick={save} disabled={!path.trim()}>Save</button></>}>
      <label className="field"><span>Path in the chat's folder (the project's files in a project)</span>
        <input className="input" value={path} autoFocus onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()} /></label>
    </Modal>
  );
}
