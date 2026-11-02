// The owner's ComfyUI workflows (0.67.0): Settings -> Models -> Workflows. Upload one, see what it needs, fetch its
// model files, choose what the assistant may set. The admin's alone - people's chats never see them.
import { useEffect, useRef, useState } from "react";
import { Download, Plus } from "lucide-react";
import { api, type Workflow, type WorkflowInput, type WorkflowList } from "../lib/api";
import { bytes } from "../lib/format";
import { useApp } from "../state";
import { ConfirmButton, Lamp, Modal, useInterval } from "./ui";

const KIND: Record<string, string> = { image: "Picture", audio: "Sound", video: "Video", text: "Text", seed: "Seed",
  number: "Number", boolean: "On/off", choice: "Choice" };

function shown(v: unknown): string {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 70 ? `${s.slice(0, 70)}...` : s;
}

export function WorkflowsSection() {
  const { toast } = useApp();
  const [data, setData] = useState<WorkflowList | null>(null);
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState<Workflow | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const load = () => api<WorkflowList>("/hub/api/workflows").then(setData, (e) => {
    toast((e as Error).message, "fault");
    setData({ workflows: [], comfy: false });
  });
  useEffect(() => { load(); }, []);
  const fetching = !!data?.workflows.some((w) => w.missing.some((m) => m.download
    && (m.download.status === "downloading" || m.download.status === "queued")));
  useInterval(load, fetching ? 2000 : null);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setBusy("");
      load();
    }
  };
  const add = (f: File) => run("upload", async () => {
    const fd = new FormData();
    fd.append("file", f, f.name);
    const w = await api<Workflow>("/hub/api/workflows", { form: fd });
    toast(`${w.name} added${w.ready ? "" : " - it needs files before it can run"}`, "run");
    setEditing(w);
  });
  const download = (w: Workflow) => run(`dl-${w.id}`, async () => {
    const r = await api<{ queued: string[]; bytes: number }>(`/hub/api/workflows/${w.id}/download`, { method: "POST" });
    toast(`Downloading ${r.queued.length} file${r.queued.length === 1 ? "" : "s"}${r.bytes ? ` (${bytes(r.bytes)})` : ""}`,
      "run");
  });
  const stop = (w: Workflow) => run(`stop-${w.id}`, async () => {
    await api(`/hub/api/workflows/${w.id}/cancel-download`, { method: "POST" });
  });
  const remove = (w: Workflow) => run(`rm-${w.id}`, async () => {
    await api(`/hub/api/workflows/${w.id}`, { method: "DELETE" });
    toast(`Deleted ${w.name}`, "run");
  });

  return (
    <section style={{ marginBottom: 26 }}>
      <div className="row" style={{ marginBottom: 8 }}>
        <h3 className="grow" style={{ margin: 0 }}>Workflows</h3>
        <button className="btn small" disabled={busy === "upload"} onClick={() => input.current?.click()}>
          {busy === "upload" ? "Reading..." : <><Plus size={14} /> Add workflow</>}</button>
        <input ref={input} type="file" hidden accept=".json,.png,.webp,application/json,image/png,image/webp"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) add(f); e.target.value = ""; }} />
      </div>
      <p className="muted small" style={{ marginTop: -4 }}>ComfyUI workflows from your computer: the .json a site
        shares, ComfyUI's API format, or a picture ComfyUI saved with its workflow inside. The Hub runs ComfyUI's own
        nodes only - a workflow that needs custom nodes is refused with their names. Choose what the assistant may
        set; then ask for it in a chat ("use the upscaler on this picture"). Only you can use them.</p>
      {data && !data.comfy && <div className="callout" style={{ marginBottom: 10 }}>ComfyUI is not answering, so what
        the workflows need cannot be checked now.</div>}
      <div className="list">
        {data === null ? <div className="faint">Loading</div> : data.workflows.length === 0
          ? <div className="list-row"><span className="muted">No workflows yet. ComfyUI's own templates are a good
            start: its template browser, or github.com/Comfy-Org/workflow_templates.</span></div>
          : data.workflows.map((w) => {
            const active = w.missing.filter((m) => m.download && (m.download.status === "downloading"
              || m.download.status === "queued"));
            const done = active.reduce((a, m) => a + (m.download?.done ?? 0), 0);
            const total = active.reduce((a, m) => a + (m.download?.total ?? 0), 0);
            const failed = w.missing.find((m) => m.download?.status === "failed");
            const open = w.inputs.filter((i) => i.on);
            return (
              <div className="list-row" key={w.id} style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
                <Lamp state={w.ready ? "run" : active.length ? "wait" : w.ready === null ? "off" : "act"}
                  pulse={!!active.length} title={w.ready ? "Ready" : w.ready === null ? "Not checked" : "Not ready"} />
                <div className="grow" style={{ minWidth: 220 }}>
                  <b>{w.name}</b> {w.ready === false && <span className="tag">
                    {w.unknown.length ? "Needs custom nodes" : `${w.missing.length} file${w.missing.length === 1 ? "" : "s"} missing`}</span>}
                  <div className="meta">{w.description || <i>No description - the assistant reads it to know when to use
                    it.</i>}</div>
                  <div className="meta">{open.length ? `The assistant sets: ${open.map((i) => i.label || i.key).join(", ")}`
                    : "The assistant runs it as it is."}{w.outputs.length ? ` · Makes: ${w.outputs.map((o) => o.title).join(", ")}` : ""}</div>
                  {active.length > 0 && <div style={{ marginTop: 6 }}>
                    <div className="bar wait"><span style={{ width: `${total ? (done / total) * 100 : 2}%` }} /></div>
                    <div className="meta num">{bytes(done)} of {bytes(total)}</div></div>}
                  {failed && !active.length && <div className="meta" style={{ color: "var(--fault)" }}>
                    Download failed: {failed.download?.error}</div>}
                </div>
                <div className="row">
                  {w.missing.some((m) => m.can_download) && (active.length
                    ? <button className="btn small" onClick={() => stop(w)}>Stop download</button>
                    : <button className="btn small" disabled={busy === `dl-${w.id}`} onClick={() => download(w)}>
                      <Download size={14} /> Download files</button>)}
                  <button className="btn quiet small" onClick={() => setEditing(w)}>Settings</button>
                  <ConfirmButton className="btn quiet small" confirmText="Delete this workflow? Its model files stay."
                    disabled={busy === `rm-${w.id}`} onConfirm={() => remove(w)}>Delete</ConfirmButton>
                </div>
              </div>
            );
          })}
      </div>
      {editing && <WorkflowSettings workflow={editing} onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); load(); }} />}
    </section>
  );
}

function WorkflowSettings({ workflow, onClose, onSaved }: { workflow: Workflow; onClose: () => void;
  onSaved: () => void }) {
  const { toast } = useApp();
  const [name, setName] = useState(workflow.name);
  const [description, setDescription] = useState(workflow.description);
  const [inputs, setInputs] = useState<Record<string, Pick<WorkflowInput, "on" | "label" | "description">>>(
    Object.fromEntries(workflow.inputs.map((i) => [i.key, { on: i.on, label: i.label, description: i.description }])));
  const [all, setAll] = useState(false);
  const set = (key: string, change: Partial<Pick<WorkflowInput, "on" | "label" | "description">>) =>
    setInputs({ ...inputs, [key]: { ...inputs[key], ...change } });
  const save = async () => {
    try {
      await api(`/hub/api/workflows/${workflow.id}`, { method: "PUT", body: { name, description, inputs } });
      onSaved();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  // the usual ones first; the rest behind "Show all"
  const listed = workflow.inputs.filter((i) => all || i.on || ["image", "audio", "video", "text", "seed"].includes(i.kind));
  return (
    <Modal wide title={`Workflow: ${workflow.name}`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={save}>Save</button></>}>
      {workflow.unknown.length > 0 && <div className="callout" style={{ marginBottom: 10 }}>It needs nodes this ComfyUI
        does not have: {workflow.unknown.join(", ")}.</div>}
      {workflow.missing.length > 0 && <div className="callout" style={{ marginBottom: 10 }}>
        <b>Missing model files</b>
        {workflow.missing.map((m) => <div key={`${m.node}-${m.input}`} className="small">
          {m.file} <span className="muted">- for {m.class_type}{m.folder ? `, into models/comfy/${m.folder}` : ""}
            {m.can_download ? " (Download files fetches it)" : m.folder ? " (no link in the workflow: put it there on the VM)"
              : " (the workflow does not say where it goes)"}</span></div>)}
      </div>}
      {workflow.wrong.length > 0 && <div className="callout" style={{ marginBottom: 10 }}>
        {workflow.wrong.map((x) => <div key={`${x.node}-${x.input}`} className="small">{x.class_type} {x.input}: "
          {shown(x.value)}" is not one of its choices here - it may fail.</div>)}</div>}
      <label className="field"><span>Name</span>
        <input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field"><span>What it is for</span>
        <textarea className="input" rows={2} value={description} maxLength={600}
          placeholder="e.g. Makes a picture four times bigger and sharper"
          onChange={(e) => setDescription(e.target.value)} />
        <small>The assistant reads this to know when to use it.</small></label>
      <div className="row" style={{ margin: "12px 0 6px" }}>
        <b className="grow">What the assistant may set</b>
        {workflow.inputs.length > listed.length || all ? <button className="btn quiet small" onClick={() => setAll(!all)}>
          {all ? "Show the usual" : `Show all (${workflow.inputs.length})`}</button> : null}
      </div>
      <p className="muted small" style={{ marginTop: 0 }}>The rest keep the workflow's own values. A seed it may set is
        random on each run unless the assistant gives one.</p>
      <div className="list">
        {listed.length === 0 && <div className="list-row muted">Nothing to set - it runs as it is.</div>}
        {listed.map((i) => (
          <div className="list-row" key={i.key} style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 8 }}>
            <input type="checkbox" checked={inputs[i.key]?.on ?? false} style={{ marginTop: 4 }}
              aria-label={`The assistant may set ${i.key}`} onChange={(e) => set(i.key, { on: e.target.checked })} />
            <div className="grow" style={{ minWidth: 200 }}>
              <b>{i.key}</b> <span className="tag">{KIND[i.kind] ?? i.kind}</span>
              <div className="meta">{i.title} · {i.input}{i.kind !== "image" && i.kind !== "audio" && i.kind !== "video"
                && i.default !== "" ? ` · now ${shown(i.default)}` : ""}{i.min !== undefined || i.max !== undefined
                ? ` · ${i.min ?? "..."} to ${i.max ?? "..."}` : ""}</div>
              {inputs[i.key]?.on && <input className="input" style={{ marginTop: 6 }} maxLength={200}
                value={inputs[i.key]?.description ?? ""} placeholder="A line for the assistant (optional)"
                onChange={(e) => set(i.key, { description: e.target.value })} />}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}
