// A folder's files: the tree, a viewer/editor, upload, and - in a project -
// each file's history with restore. Beside a chat, files can also be put in
// the next message (picked/onPick).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, Download, File as FileIcon, FileArchive, FilePlus, Folder, FolderPlus,
  History, MessageSquarePlus, Pencil, Plus, RefreshCw, Search, Trash2, Upload as UploadIcon } from "lucide-react";
import { api, type FileData, type TreeData, type TreeEntry } from "../lib/api";
import { downloadZip } from "../lib/download";
import { ago, bytes } from "../lib/format";
import { useApp } from "../state";
import { ConfirmButton, Modal, useInterval } from "./ui";

export type Folder = { kind: "chat" | "project"; id: number };

interface Node { name: string; path: string; dir: boolean; size: number; modified: number; children: Node[] }

function buildTree(entries: TreeEntry[]): Node {
  const root: Node = { name: "", path: "", dir: true, size: 0, modified: 0, children: [] };
  const dirs = new Map<string, Node>([["", root]]);
  const sorted = [...entries].sort((a, b) => a.path.localeCompare(b.path));
  for (const e of sorted) {
    const parts = e.path.split("/");
    const name = parts[parts.length - 1];
    const parentPath = parts.slice(0, -1).join("/");
    let parent = dirs.get(parentPath);
    if (!parent) {                                   // a parent the listing skipped: make it
      parent = root;
      let acc = "";
      for (const p of parts.slice(0, -1)) {
        acc = acc ? `${acc}/${p}` : p;
        let d = dirs.get(acc);
        if (!d) {
          d = { name: p, path: acc, dir: true, size: 0, modified: 0, children: [] };
          parent.children.push(d);
          dirs.set(acc, d);
        }
        parent = d;
      }
    }
    const node: Node = { name, path: e.path, dir: e.dir, size: e.size, modified: e.modified, children: [] };
    parent.children.push(node);
    if (e.dir) dirs.set(e.path, node);
  }
  const order = (n: Node) => {
    n.children.sort((a, b) => (a.dir === b.dir ? a.name.localeCompare(b.name) : a.dir ? -1 : 1));
    n.children.forEach(order);
  };
  order(root);
  return root;
}

const base = (f: Folder) => `/hub/api/folders/${f.kind}/${f.id}`;

const MAX_MATCHES = 200;

export function Files({ folder, projectId, select, selectAt, picked, onPick }: { folder: Folder;
  projectId?: number | null;
  select?: string;                   // a file to show (again, when selectAt changes)
  selectAt?: number;
  picked?: string[];                 // files in the next message
  onPick?: (path: string) => void;   // puts a file in the message, or takes it out
}) {
  const { toast } = useApp();
  const [tree, setTree] = useState<TreeData | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [sel, setSel] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [prompt, setPrompt] = useState<null | { title: string; label: string; value: string; action: (v: string) => Promise<void> }>(null);
  const upInput = useRef<HTMLInputElement>(null);
  const url = base(folder);

  const refresh = useCallback(async () => {
    try {
      setTree(await api<TreeData>(`${url}/tree`));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [url, toast]);

  useEffect(() => {
    setTree(null);
    setSel(null);
    refresh();
  }, [refresh]);
  useInterval(refresh, 6000);

  // Select a file and open the folders above it.
  const reveal = useCallback((path: string) => {
    setSel(path);
    const parts = path.split("/");
    setOpen((o) => {
      const n = new Set(o);
      for (let i = 1; i < parts.length; i++) n.add(parts.slice(0, i).join("/"));
      return n;
    });
  }, []);

  // Asked to show a file (one named in a message).
  useEffect(() => {
    if (!select) return;
    reveal(select);
    setQ("");
  }, [select, selectAt, url, reveal]);

  const root = useMemo(() => buildTree(tree?.entries ?? []), [tree]);
  const pickedSet = useMemo(() => new Set(picked ?? []), [picked]);
  const matches = useMemo(() => {
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return null;
    return (tree?.entries ?? []).filter((e) => !e.dir && terms.every((t) => e.path.toLowerCase().includes(t)));
  }, [q, tree]);
  const selected = tree?.entries.find((e) => e.path === sel) ?? null;
  const currentDir = selected ? (selected.dir ? selected.path : selected.path.split("/").slice(0, -1).join("/")) : "";

  const uploadFiles = async (files: FileList) => {
    const fd = new FormData();
    for (const f of Array.from(files)) fd.append("files", f, f.name);
    fd.append("dir", currentDir);
    try {
      const r = await api<{ saved: string[] }>(`${base(folder)}/upload`, { form: fd });
      toast(`Uploaded ${r.saved.length} file${r.saved.length === 1 ? "" : "s"}`, "run");
      refresh();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const zip = () => downloadZip(`${url}/zip`).catch((e) => toast((e as Error).message, "fault"));

  const ask = (title: string, label: string, value: string, action: (v: string) => Promise<void>) =>
    setPrompt({ title, label, value, action });

  const newFile = () => ask("New file", "Path", currentDir ? `${currentDir}/` : "", async (p) => {
    await api(`${base(folder)}/file`, { method: "PUT", body: { path: p, text: "" } });
    await refresh();
    setSel(p);
  });
  const newFolder = () => ask("New folder", "Path", currentDir ? `${currentDir}/` : "", async (p) => {
    await api(`${base(folder)}/mkdir`, { body: { path: p } });
    await refresh();
    setOpen((o) => new Set(o).add(p));
  });

  return (
    <div className={`files ${tree && tree.entries.length === 0 ? "single" : ""}`}>
      {tree && tree.entries.length === 0 ? (
        <div className="page" style={{ maxWidth: "40rem" }}>
          <p className="lead">The folder is empty. Upload files, or ask the assistant to make some.</p>
          <div className="row wrap">
            <button className="btn primary" onClick={() => upInput.current?.click()}><UploadIcon size={16} /> Upload files</button>
            <button className="btn" onClick={newFile}><FilePlus size={16} /> New file</button>
          </div>
        </div>
      ) : (
        <>
          <div className={`tree ${onPick ? "picking" : ""}`} aria-label="Files">
            <div className="row" style={{ padding: "0 4px 6px", gap: 2 }}>
              <button className="icon-btn small" onClick={() => upInput.current?.click()} aria-label="Upload files" title="Upload files"><UploadIcon size={15} /></button>
              <button className="icon-btn small" onClick={newFile} aria-label="New file" title="New file"><FilePlus size={15} /></button>
              <button className="icon-btn small" onClick={newFolder} aria-label="New folder" title="New folder"><FolderPlus size={15} /></button>
              <span className="grow" />
              <button className="icon-btn small" onClick={zip} aria-label="Download the folder as a ZIP"
                title="Download the folder as a ZIP"><FileArchive size={15} /></button>
              <button className="icon-btn small" onClick={refresh} aria-label="Refresh" title="Refresh"><RefreshCw size={14} /></button>
            </div>
            <label className="tree-find">
              <Search size={14} aria-hidden />
              <input value={q} placeholder="Find a file" aria-label="Find a file" onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Escape") setQ(""); }} />
            </label>
            {matches ? (
              <>
                {matches.slice(0, MAX_MATCHES).map((e) => {
                  const cut = e.path.lastIndexOf("/");
                  return (
                    <div className="tree-row" key={e.path}>
                      <button className="tree-item" aria-current={sel === e.path} title={e.path} onClick={() => reveal(e.path)}>
                        <FileIcon size={14} />
                        <span className="ellipsis">{e.path.slice(cut + 1)}
                          {cut > 0 && <span className="faint"> {e.path.slice(0, cut)}</span>}</span>
                      </button>
                      {onPick && <PickButton path={e.path} on={pickedSet.has(e.path)} onPick={onPick} />}
                    </div>
                  );
                })}
                <div className="faint small" style={{ padding: "6px 10px" }}>
                  {matches.length === 0 ? "No file matches." : matches.length > MAX_MATCHES
                    ? `${MAX_MATCHES} of ${matches.length} files. Type more to narrow it down.` : null}
                </div>
              </>
            ) : (
              <TreeNodes nodes={root.children} depth={0} open={open} sel={sel}
                onToggle={(p) => setOpen((o) => { const n = new Set(o); n.has(p) ? n.delete(p) : n.add(p); return n; })}
                onSelect={setSel} picked={pickedSet} onPick={onPick} />
            )}
          </div>
          <div className="file-view">
            {selected && !selected.dir ? (
              <FileView key={selected.path + selected.modified} folder={folder} entry={selected} projectId={projectId}
                picked={pickedSet.has(selected.path)} onPick={onPick}
                onChanged={refresh} onGone={() => { setSel(null); refresh(); }}
                onRename={() => ask("Rename or move", "New path", selected.path, async (p) => {
                  await api(`${base(folder)}/move`, { body: { src: selected.path, dst: p } });
                  await refresh();
                  setSel(p);
                })} />
            ) : (
              <div className="page faint" style={{ maxWidth: "36rem" }}>
                {tree && sel && !selected && <p className="notice">{sel} is not in the folder any more.</p>}
                {tree ? `${tree.entries.filter((e) => !e.dir).length} files. Pick one to look at it or edit it.` : "Loading"}
                {tree && <p><button className="btn small" onClick={zip}>
                  <FileArchive size={14} /> Download the folder as a ZIP</button></p>}
                {tree && onPick && <p>The + beside a file puts it in your next message: the assistant then has the file
                  as it is now, to change it or answer about it.</p>}
                {tree?.root && <div className="code-line">{tree.root}</div>}
              </div>
            )}
          </div>
        </>
      )}
      <input ref={upInput} type="file" multiple hidden onChange={(e) => { if (e.target.files?.length) uploadFiles(e.target.files); e.target.value = ""; }} />
      {prompt && <PromptModal {...prompt} onClose={() => setPrompt(null)} />}
    </div>
  );
}

function TreeNodes({ nodes, depth, open, sel, onToggle, onSelect, picked, onPick }: { nodes: Node[]; depth: number;
  open: Set<string>; sel: string | null; onToggle: (p: string) => void; onSelect: (p: string) => void;
  picked: Set<string>; onPick?: (p: string) => void }) {
  return (
    <>
      {nodes.map((n) => (
        <div key={n.path}>
          <div className="tree-row">
            <button className="tree-item" style={{ paddingLeft: 8 + depth * 14 }} aria-current={sel === n.path}
              onClick={() => (n.dir ? (onToggle(n.path), onSelect(n.path)) : onSelect(n.path))}>
              {n.dir ? (open.has(n.path) ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : <span style={{ width: 13 }} />}
              {n.dir ? <Folder size={14} /> : <FileIcon size={14} />}
              <span className="ellipsis">{n.name}</span>
            </button>
            {!n.dir && onPick && <PickButton path={n.path} on={picked.has(n.path)} onPick={onPick} />}
          </div>
          {n.dir && open.has(n.path) && <TreeNodes nodes={n.children} depth={depth + 1} open={open} sel={sel}
            onToggle={onToggle} onSelect={onSelect} picked={picked} onPick={onPick} />}
        </div>
      ))}
    </>
  );
}

function PickButton({ path, on, onPick }: { path: string; on: boolean; onPick: (p: string) => void }) {
  const name = path.split("/").pop();
  return (
    <button className="icon-btn small tree-pick" aria-pressed={on} onClick={() => onPick(path)}
      aria-label={on ? `Take ${name} out of the message` : `Put ${name} in the message`}
      title={on ? "In your next message. Click to take it out." : "Put it in your next message"}>
      {on ? <Check size={14} /> : <Plus size={14} />}
    </button>
  );
}

function FileView({ folder, entry, projectId, picked, onPick, onChanged, onGone, onRename }: { folder: Folder;
  entry: TreeEntry; projectId?: number | null; picked: boolean; onPick?: (path: string) => void; onChanged: () => void;
  onGone: () => void; onRename: () => void }) {
  const { toast } = useApp();
  const [file, setFile] = useState<FileData | null>(null);
  const [dirty, setDirty] = useState(false);
  const [history, setHistory] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const text = useRef("");
  const raw = `${base(folder)}/raw?path=${encodeURIComponent(entry.path)}`;
  const cut = entry.path.lastIndexOf("/");
  const isImage = /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(entry.path);
  const isAudio = /\.(mp3|wav|ogg|oga|opus|m4a|aac|flac|weba)$/i.test(entry.path);
  const isVideo = /\.(mp4|webm|mov|m4v|ogv)$/i.test(entry.path);
  const isPdf = /\.pdf$/i.test(entry.path);
  const media = isImage || isAudio || isVideo || isPdf;          // shown from the file itself, not read as text

  const save = useCallback(async () => {
    try {
      await api(`${base(folder)}/file`, { method: "PUT", body: { path: entry.path, text: text.current } });
      setDirty(false);
      toast("Saved", "run");
      onChanged();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [folder, entry.path, toast, onChanged]);
  const saveRef = useRef(save);
  saveRef.current = save;

  useEffect(() => {
    let alive = true;
    if (media) return;
    api<FileData>(`${base(folder)}/file`, { query: { path: entry.path } }).then((f) => alive && setFile(f),
      (e) => toast((e as Error).message, "fault"));
    return () => {
      alive = false;
    };
  }, [folder, entry.path, media, toast]);

  useEffect(() => {
    if (!file || file.text == null || !host.current) return;
    text.current = file.text;
    let view: { destroy: () => void } | null = null;
    let cancelled = false;
    import("./editor").then(({ createEditor }) => {
      if (cancelled || !host.current) return;
      view = createEditor(host.current, file.text ?? "", entry.path, (t) => {
        text.current = t;
        setDirty(true);
      }, () => saveRef.current());
    });
    return () => {
      cancelled = true;
      view?.destroy();
    };
  }, [file, entry.path]);

  const remove = async () => {
    try {
      await api(`${base(folder)}/file`, { method: "DELETE", query: { path: entry.path } });
      toast(`Deleted ${entry.path}`, "run");
      onGone();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  return (
    <>
      <div className="file-bar">
        <span className="path" title={entry.path}>
          {cut > 0 && <span className="dir">{entry.path.slice(0, cut + 1)}</span>}
          <span className="name">{entry.path.slice(cut + 1)}</span>
        </span>
        <span className="faint small num meta">{bytes(entry.size)}, {ago(entry.modified)}</span>
        <span className="file-actions">
        {onPick && (
          <button className={`btn small ${picked ? "picked" : ""}`} aria-pressed={picked}
            onClick={async () => { if (dirty && !picked) await save(); onPick(entry.path); }}
            title={picked ? "In your next message. Click to take it out." : "Put this file in your next message"}>
            {picked ? <><Check size={14} /> In the message</> : <><MessageSquarePlus size={14} /> Add to chat</>}
          </button>
        )}
        {file?.text != null && <button className="btn primary small" disabled={!dirty} onClick={save}>Save</button>}
        {projectId && <button className="icon-btn small" onClick={() => setHistory(true)} aria-label="History" title="History"><History size={15} /></button>}
        <button className="icon-btn small" onClick={onRename} aria-label="Rename or move" title="Rename or move"><Pencil size={15} /></button>
        <a className="icon-btn small" href={`${raw}&download=true`} aria-label="Download" title="Download"><Download size={15} /></a>
        <ConfirmButton className="icon-btn small" confirmText="Delete?" label="Delete" onConfirm={remove}><Trash2 size={15} /></ConfirmButton>
        </span>
      </div>
      {isImage ? (
        <div className="editor"><img className="preview-img" src={raw} alt={entry.path} /></div>
      ) : isAudio ? (
        <div className="editor media-view"><audio controls preload="metadata" src={raw} aria-label={`Play ${entry.path}`} /></div>
      ) : isVideo ? (
        <div className="editor media-view"><video className="preview-img" controls preload="metadata" src={raw} /></div>
      ) : isPdf ? (
        <div className="editor media-view pdf-view">
          <iframe className="preview-pdf" src={raw} title={entry.path} />
          <a className="btn small" href={raw} target="_blank" rel="noreferrer">Open in a new tab</a></div>
      ) : !file ? (
        <div className="page faint">Loading</div>
      ) : file.text == null ? (
        <div className="page"><p className="lead">A binary file ({file.mime}, {bytes(file.size)}).</p>
          <a className="btn" href={raw} target="_blank" rel="noreferrer">Open</a></div>
      ) : (
        <div className="editor" ref={host} />
      )}
      {history && projectId && <FileHistory projectId={projectId} path={entry.path} onClose={() => setHistory(false)}
        onRestored={() => { setHistory(false); onChanged(); }} />}
    </>
  );
}

function FileHistory({ projectId, path, onClose, onRestored }: { projectId: number; path: string; onClose: () => void;
  onRestored: () => void }) {
  const { toast } = useApp();
  const [items, setItems] = useState<{ rev: number; author: string; origin: string; message: string; createdAt: string;
    deleted: boolean; size: number }[] | null>(null);
  const [preview, setPreview] = useState<{ rev: number; text: string } | null>(null);
  useEffect(() => {
    api<typeof items>(`/hub/api/projects/${projectId}/history`, { query: { path } }).then(setItems,
      (e) => toast((e as Error).message, "fault"));
  }, [projectId, path, toast]);
  const view = async (rev: number) => {
    try {
      const r = await api<{ text?: string } | string>(`/hub/api/projects/${projectId}/rev-file`, { query: { path, rev } });
      setPreview({ rev, text: typeof r === "string" ? r : r.text ?? "(binary)" });
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const restore = async (rev: number) => {
    try {
      await api(`/hub/api/projects/${projectId}/restore`, { body: { path, rev } });
      toast(`Restored ${path} from revision ${rev}`, "run");
      onRestored();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <Modal title={`History of ${path}`} onClose={onClose} wide>
      {!items ? <div className="faint">Loading</div> : (
        <ul className="history" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {items.map((h, i) => (
            <li key={h.rev}>
              <span className="tag num">r{h.rev}</span>
              <span className="grow">{h.deleted ? "Deleted" : h.message || "Changed"}
                <span className="faint small">, {h.author} via {h.origin}, {ago(h.createdAt)}</span></span>
              {!h.deleted && <button className="btn quiet small" onClick={() => view(h.rev)}>View</button>}
              {!h.deleted && i > 0 && <button className="btn small" onClick={() => restore(h.rev)}>Restore</button>}
            </li>
          ))}
        </ul>
      )}
      {preview && <><div className="label">Revision {preview.rev}</div><pre className="code-line" style={{ maxHeight: 360, whiteSpace: "pre-wrap" }}>{preview.text}</pre></>}
    </Modal>
  );
}

export function PromptModal({ title, label, value, action, onClose }: { title: string; label: string; value: string;
  action: (v: string) => Promise<void>; onClose: () => void }) {
  const { toast } = useApp();
  const [v, setV] = useState(value);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (!v.trim()) return;
    setBusy(true);
    try {
      await action(v.trim());
      onClose();
    } catch (e) {
      toast((e as Error).message, "fault");
      setBusy(false);
    }
  };
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={busy || !v.trim()} onClick={go}>{title}</button></>}>
      <label className="field"><span>{label}</span>
        <input className="input" value={v} autoFocus onChange={(e) => setV(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && go()} /></label>
    </Modal>
  );
}
