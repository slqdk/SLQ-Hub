// Libraries: documents the assistant can search, by text and by what the pages show.
import { useCallback, useEffect, useRef, useState } from "react";
import { Menu, Plus, RefreshCw, Search, Trash2, Upload as UploadIcon } from "lucide-react";
import { api, mediaUrl, type Library, type LibraryDoc, type LibraryHit } from "../lib/api";
import { ago, bytes } from "../lib/format";
import { linkProps, navigate } from "../lib/router";
import { useApp } from "../state";
import { ConfirmButton, Empty, Lamp, Modal, useInterval } from "../components/ui";

export function LibrariesView({ id }: { id?: number }) {
  const { setSidebarOpen, toast } = useApp();
  const [libs, setLibs] = useState<Library[] | null>(null);
  const [available, setAvailable] = useState(true);
  const [creating, setCreating] = useState(false);
  const load = useCallback(async () => {
    try {
      const r = await api<{ libraries: Library[]; available: boolean }>("/hub/api/libraries");
      setLibs(r.libraries);
      setAvailable(r.available);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load]);
  useInterval(load, libs?.some((l) => l.busy > 0) ? 3000 : null);

  const lib = libs?.find((l) => l.id === id);
  return (
    <div className="main">
      <div className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open the menu"><Menu size={18} /></button>
        <h1>{lib ? <><a {...linkProps("/libraries")} style={{ color: "inherit" }}>Libraries</a> / {lib.name}</> : "Libraries"}</h1>
        <span className="grow" />
        {!lib && <button className="btn primary small" onClick={() => setCreating(true)}><Plus size={15} /> New library</button>}
      </div>
      <div className="scroll">
        <div className="page">
          {!available && <div className="callout fault" style={{ marginBottom: 16 }}>
            The embedding model is not downloaded, so documents cannot be indexed. Get it under Settings, Models
            (Qwen3 Embedding).</div>}
          {lib ? <LibraryDetail lib={lib} onChanged={load} /> : (
            <>
              <p className="lead">Put manuals, papers and notes in a library, then connect it to a chat (the + in the
                message box) or a project. The assistant searches the text and the page images, so diagrams and scanned
                pages are found too.</p>
              <div className="list">
                {libs === null ? <Empty>Loading</Empty> : libs.length === 0 ? <Empty>No libraries yet.</Empty> : libs.map((l) => (
                  <a key={l.id} className="list-row" style={{ color: "inherit", textDecoration: "none" }} {...linkProps({ name: "libraries", id: l.id })}>
                    <span className="grow"><b>{l.name}</b>
                      <div className="meta">{l.docs} document{l.docs === 1 ? "" : "s"}, {l.pages} pages, {bytes(l.bytes)}
                        {l.description ? `, ${l.description}` : ""}</div></span>
                    {l.busy > 0 && <span className="row small muted"><Lamp state="wait" /> indexing {l.busy}</span>}
                    {l.errors > 0 && <span className="row small" style={{ color: "var(--fault)" }}><Lamp state="fault" /> {l.errors} failed</span>}
                  </a>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
      {creating && <NewLibrary onClose={() => setCreating(false)} onCreated={(nid) => { setCreating(false); load(); navigate({ name: "libraries", id: nid }); }} />}
    </div>
  );
}

function NewLibrary({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = async () => {
    try {
      const l = await api<Library>("/hub/api/libraries", { body: { name, description } });
      onCreated(l.id);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <Modal title="New library" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!name.trim()} onClick={create}>Create library</button></>}>
      <label className="field"><span>Name</span><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field"><span>What is in it</span><input className="input" value={description}
        onChange={(e) => setDescription(e.target.value)} placeholder="Optional" /></label>
    </Modal>
  );
}

function LibraryDetail({ lib, onChanged }: { lib: Library; onChanged: () => void }) {
  const { toast } = useApp();
  const [docs, setDocs] = useState<LibraryDoc[] | null>(null);
  const [drag, setDrag] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<LibraryHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setDocs(await api<LibraryDoc[]>(`/hub/api/libraries/${lib.id}/docs`));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [lib.id, toast]);
  useEffect(() => {
    load();
  }, [load]);
  const busy = docs?.some((d) => d.status === "queued" || d.status === "indexing");
  useInterval(() => { load(); onChanged(); }, busy ? 2000 : null);

  const add = async (files: FileList) => {
    const fd = new FormData();
    for (const f of Array.from(files)) fd.append("files", f, f.name);
    try {
      const r = await api<{ added: LibraryDoc[]; refused: { filename: string; error: string }[] }>(
        `/hub/api/libraries/${lib.id}/docs`, { form: fd });
      if (r.added.length) toast(`Added ${r.added.length} document${r.added.length === 1 ? "" : "s"}; indexing starts now`, "run");
      for (const x of r.refused) toast(`${x.filename}: ${x.error}`, "fault");
      load();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const search = async () => {
    if (!q.trim()) return;
    setSearching(true);
    try {
      setHits(await api<LibraryHit[]>(`/hub/api/libraries/${lib.id}/search`, { query: { q, k: 8 } }));
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setSearching(false);
    }
  };

  const del = async (doc: LibraryDoc) => {
    try {
      await api(`/hub/api/libraries/${lib.id}/docs/${doc.id}`, { method: "DELETE" });
      load();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const reindex = async (doc: LibraryDoc) => {
    try {
      await api(`/hub/api/libraries/${lib.id}/docs/${doc.id}/reindex`, { method: "POST" });
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const removeLib = async () => {
    try {
      await api(`/hub/api/libraries/${lib.id}`, { method: "DELETE" });
      onChanged();
      navigate("/libraries", true);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  return (
    <div className="stack" style={{ gap: 18 }}
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDrag(true); } }}
      onDragLeave={() => setDrag(false)} onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) add(e.dataTransfer.files); }}>
      {lib.description && <p className="lead">{lib.description}</p>}
      <div className={`callout`} style={drag ? { borderColor: "var(--act)", background: "var(--act-wash)" } : undefined}>
        <div className="row wrap">
          <button className="btn primary" onClick={() => input.current?.click()}><UploadIcon size={16} /> Add documents</button>
          <span className="muted small grow">PDF, pictures, Word (.docx), HTML and text files. Drop them here.
            A file with the same name replaces the old one.</span>
        </div>
        <input ref={input} type="file" multiple hidden accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,.docx,.html,.htm,.txt,.md,.csv,.json,.xml,.yaml,.yml"
          onChange={(e) => { if (e.target.files?.length) add(e.target.files); e.target.value = ""; }} />
      </div>

      <div>
        <div className="label" style={{ marginBottom: 6 }}>Try a search</div>
        <div className="row">
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="What would you ask?"
            onKeyDown={(e) => e.key === "Enter" && search()} />
          <button className="btn" onClick={search} disabled={searching || !q.trim()}><Search size={15} /> Search</button>
        </div>
        {hits && (
          <div className="list" style={{ marginTop: 10 }}>
            {hits.length === 0 ? <Empty>Nothing found.</Empty> : hits.map((h, i) => (
              <div className="list-row" key={i} style={{ alignItems: "flex-start" }}>
                {h.image && <a href={mediaUrl(h.image)} target="_blank" rel="noreferrer" className="thumb" style={{ flex: "none" }}>
                  <img src={mediaUrl(h.image)} alt={`${h.filename} page ${h.page}`} style={{ maxWidth: 90, maxHeight: 110 }} /></a>}
                <div className="grow">
                  <b>{h.filename}{h.page ? `, page ${h.page}` : ""}</b>
                  <span className="meta"> score {h.score}, found by {h.via.join(" and ")}</span>
                  {h.text && <div className="small muted" style={{ whiteSpace: "pre-wrap", maxHeight: 120, overflow: "hidden" }}>{h.text}</div>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <div className="label" style={{ marginBottom: 6 }}>Documents</div>
        <div className="list">
          {docs === null ? <Empty>Loading</Empty> : docs.length === 0 ? <Empty>No documents yet.</Empty> : docs.map((d) => (
            <div className="list-row" key={d.id}>
              <Lamp state={d.status === "ready" ? "run" : d.status === "error" ? "fault" : "wait"} pulse={d.status === "indexing"} />
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="ellipsis"><b>{d.filename}</b></div>
                <div className="meta">
                  {d.status === "ready" && `${d.pages} page${d.pages === 1 ? "" : "s"}, ${d.chunks} passages, ${bytes(d.size)}, indexed ${ago(d.indexed_at)}`}
                  {d.status === "queued" && "Waiting to be indexed"}
                  {d.status === "indexing" && `Indexing, ${Math.round(d.progress * 100)} %`}
                  {d.status === "error" && <span style={{ color: "var(--fault)" }}>{d.error}</span>}
                </div>
                {d.status === "indexing" && <div className="bar wait" style={{ marginTop: 6 }}><span style={{ width: `${Math.round(d.progress * 100)}%` }} /></div>}
              </div>
              <button className="icon-btn small" onClick={() => reindex(d)} aria-label="Index again" title="Index again"><RefreshCw size={14} /></button>
              <ConfirmButton className="icon-btn small" confirmText="Delete?" label={`Delete ${d.filename}`} onConfirm={() => del(d)}><Trash2 size={14} /></ConfirmButton>
            </div>
          ))}
        </div>
      </div>
      <div className="row"><span className="grow" />
        <ConfirmButton onConfirm={removeLib} confirmText="Delete the library and its index?">Delete library</ConfirmButton></div>
    </div>
  );
}
