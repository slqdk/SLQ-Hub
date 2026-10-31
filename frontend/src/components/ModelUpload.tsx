// Model files from this computer (0.62.0, Søren: "så jeg selv kan uploade modeller fra min pc"): a language model
// (.gguf, with its vision projector), an SDXL picture model, the missing files of a model in the Hub's list, and a
// klein training's base model files. Each file goes in pieces (lib/upload.ts); what it is, is read from its own
// header as soon as that has arrived, so a wrong file is said after the first piece and not after 20 GB.
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Upload } from "lucide-react";
import { api, type ModelEntry, type SdxlChoices, type SdxlSettings } from "../lib/api";
import { bytes, seconds } from "../lib/format";
import { inspectUpload, uploadFile, type UploadFacts, type UploadInfo } from "../lib/upload";
import { useApp } from "../state";
import { Lamp, Modal, SwitchRow } from "./ui";
import { presetSettings, SdxlFields } from "./SdxlFields";

export interface Uploaded { up: UploadInfo | null; facts: UploadFacts | null; done: boolean; file: File | null }
const NONE: Uploaded = { up: null, facts: null, done: false, file: null };
const CONTEXTS = [8192, 16384, 32768, 65536, 131072, 262144];
const ID_OK = /^[a-z0-9][a-z0-9._-]{0,62}$/;

class Refused extends Error {}

/** One file: choose it, watch it go, see what it is. `refuse` names what makes it the wrong file once its facts are
 *  known - sending then stops, and what arrived is deleted. Closed before the end, what arrived stays on the Hub
 *  for three days: choosing the same file again goes on from there. */
export function UploadField({ label, accept, hint, onChange, refuse, inspect = true }: { label: string; accept: string;
  hint?: ReactNode; inspect?: boolean; onChange: (u: Uploaded) => void; refuse?: (facts: UploadFacts) => string }) {
  const [file, setFile] = useState<File | null>(null);
  const [received, setReceived] = useState(0);
  const [rate, setRate] = useState(0);
  const [status, setStatus] = useState<"idle" | "sending" | "stopped" | "done" | "failed">("idle");
  const [error, setError] = useState("");
  const [facts, setFacts] = useState<UploadFacts | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const ctrl = useRef<AbortController | null>(null);
  const last = useRef<{ t: number; n: number } | null>(null);
  useEffect(() => () => ctrl.current?.abort(), []);

  const start = async (f: File) => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setFile(f);
    setStatus("sending");
    setError("");
    setFacts(null);
    setReceived(0);
    setRate(0);
    last.current = null;
    let known: UploadFacts | null = null;
    let current: UploadInfo | null = null;
    onChange({ ...NONE, file: f });
    const look = async (up: UploadInfo) => {
      current = up;
      if (!inspect || (known && !known.pending && !up.done)) return;
      const got = await inspectUpload(up.id);
      if (got.pending) return;
      known = got;
      setFacts(got);
      const why = refuse?.(got) ?? "";
      if (why) throw new Refused(why);
      onChange({ up, facts: got, done: up.done, file: f });
    };
    try {
      const up = await uploadFile(f, { signal: c.signal, onPiece: look, onProgress: (n) => {
        const now = Date.now();
        if (last.current && now > last.current.t && n > last.current.n) {
          const r = ((n - last.current.n) * 1000) / (now - last.current.t);
          setRate((old) => (old ? old * 0.7 + r * 0.3 : r));
        }
        last.current = { t: now, n };
        setReceived(n);
      } });
      await look(up);                     // the whole header now (a GGUF's parameters come last)
      setStatus("done");
      onChange({ up, facts: known, done: true, file: f });
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        setStatus("stopped");
        return;
      }
      if (e instanceof Refused && current) {           // the wrong file: what arrived of it goes
        const gone = current as UploadInfo;
        await api(`/hub/api/model-uploads/${gone.id}`, { method: "DELETE" }).catch(() => undefined);
      }
      setStatus("failed");
      setError((e as Error).message);
      onChange({ ...NONE, file: f, facts: known });
    }
  };

  const stop = () => ctrl.current?.abort();
  const left = file && rate ? (file.size - received) / rate : null;
  return (
    <div className="field upload-field">
      <span>{label}</span>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <button type="button" className="btn small" disabled={status === "sending"} onClick={() => input.current?.click()}>
          <Upload size={14} /> {file ? "Choose another" : "Choose a file"}</button>
        {file && <span className="grow ellipsis" title={file.name}><b>{file.name}</b> <span className="meta num">
          {bytes(file.size)}</span></span>}
        {status === "sending" && <button type="button" className="btn quiet small" onClick={stop}>Stop</button>}
        <input ref={input} type="file" hidden accept={accept}
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) start(f); }} />
      </div>
      {file && status !== "idle" && <div style={{ marginTop: 6 }}>
        <div className={`bar ${status === "done" ? "" : "wait"}`}>
          <span style={{ width: `${Math.max(2, Math.min(100, (received / Math.max(1, file.size)) * 100))}%` }} /></div>
        <div className="meta num">{status === "done" ? "Uploaded"
          : status === "stopped" ? `Stopped at ${bytes(received)} of ${bytes(file.size)} - choose the same file to go on`
            : status === "failed" ? "" : `${bytes(received)} of ${bytes(file.size)}${rate ? `, ${bytes(rate)}/s` : ""}`
              + `${left != null && left > 0 ? ` · ${seconds(left)} left` : ""}`}</div></div>}
      {error && <small className="warn-text">{error}</small>}
      {!file && hint && <small>{hint}</small>}
      {facts && !error && <FactsLine facts={facts} />}
    </div>
  );
}

function FactsLine({ facts }: { facts: UploadFacts }) {
  if (facts.kind === "gguf") {
    const bits = [facts.architecture, facts.parameters ? `${(facts.parameters / 1e9).toFixed(1)}B parameters` : null,
      facts.context_length ? `${Math.round(facts.context_length / 1024)}K context` : null, facts.quantization || null]
      .filter(Boolean);
    return <small className="row" style={{ gap: 6 }}><Lamp state="run" size="sm" /> A language model{bits.length
      ? `: ${bits.join(" · ")}` : ""}</small>;
  }
  if (facts.kind === "mmproj") return <small className="row" style={{ gap: 6 }}><Lamp state="run" size="sm" /> A vision
    projector</small>;
  if (facts.kind === "sdxl") return <small className="row" style={{ gap: 6 }}><Lamp state="run" size="sm" /> An SDXL
    checkpoint the Hub can run{facts.vpred ? " (v-prediction: ComfyUI reads that from the file)" : ""}</small>;
  return null;
}

/** Settings -> Models -> Language models -> Upload: a .gguf from this computer, and its vision projector. */
export function UploadLanguageModel({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const { toast } = useApp();
  const [model, setModel] = useState<Uploaded>(NONE);
  const [mmproj, setMmproj] = useState<Uploaded>(NONE);
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const [named, setNamed] = useState(false);
  const [ctx, setCtx] = useState(32768);
  const [tools, setTools] = useState(true);
  const [thinking, setThinking] = useState(false);
  const [adding, setAdding] = useState(false);
  const facts = model.facts?.kind === "gguf" ? model.facts : null;
  const limit = facts?.context_length ?? 262144;

  const onModel = useCallback((u: Uploaded) => {
    setModel(u);
    const f = u.facts;
    if (f?.kind !== "gguf") return;
    if (!named && f.suggested) {
      setName(f.suggested.name);
      setId(f.suggested.id);
    }
    setCtx((c) => Math.min(c, CONTEXTS.filter((x) => x <= (f.context_length ?? 262144)).pop() ?? 8192));
    if (f.tools != null) setTools(f.tools);
    if (f.thinking != null) setThinking(f.thinking);
  }, [named]);

  const idOk = ID_OK.test(id);
  const ready = model.done && !!facts && (!mmproj.file || mmproj.done) && idOk;
  const add = async () => {
    if (!model.up) return;
    setAdding(true);
    try {
      await api("/hub/api/models/local", { body: { upload: model.up.id, mmproj: mmproj.done ? mmproj.up?.id : null, id,
        name, ctx, tools, thinking } });
      toast(`${name || id} is among your language models`, "run");
      onAdded();
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setAdding(false);
    }
  };

  return (
    <Modal wide title="Upload a language model" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!ready || adding} onClick={add}>{adding ? "Adding" : "Add"}</button></>}>
      <p className="muted small" style={{ margin: 0 }}>A GGUF file from this computer (the Hub runs language models
        with llama.cpp), one file - not one split into parts. It goes in pieces, so a lost connection or a closed
        page loses little: choose the same file again and it goes on. One language model is on the card at a time.</p>
      <UploadField label="Model (.gguf)" accept=".gguf" onChange={onModel}
        refuse={(f) => f.kind === "mmproj" ? "This is a vision projector (mmproj): choose it under Pictures, below."
          : f.kind !== "gguf" ? (f.reason || "That is not a GGUF model the Hub can read.") : ""} />
      <UploadField label="Pictures (vision) - optional" accept=".gguf" onChange={setMmproj}
        hint="The model's vision projector (mmproj-...gguf), if it can see pictures."
        refuse={(f) => f.kind !== "mmproj" ? "That is not a vision projector (an mmproj .gguf)." : ""} />
      {facts && (<>
        <div className="form-grid">
          <label className="field"><span>Name</span><input className="input" value={name}
            onChange={(e) => { setName(e.target.value); setNamed(true); }} /></label>
          <label className="field"><span>Name for apps</span><input className="input mono" value={id}
            onChange={(e) => { setId(e.target.value.toLowerCase()); setNamed(true); }} />
            <small style={idOk ? undefined : { color: "var(--fault)" }}>{idOk ? "Apps ask for the model by this name."
              : "a-z, 0-9, dots, dashes; starts with a letter or digit."}</small></label>
          <label className="field"><span>Context (tokens)</span><select className="select" value={ctx}
            onChange={(e) => setCtx(Number(e.target.value))}>
            {CONTEXTS.filter((c) => c <= limit).map((c) => <option key={c} value={c}>{c / 1024}K</option>)}</select>
            <small>More context needs more VRAM. Changeable later under Server settings.</small></label>
        </div>
        <SwitchRow checked={tools} onChange={setTools} title="Tool calls">
          {facts.tools == null ? "Unknown for this model." : facts.tools ? "Its chat template supports tools."
            : "Its chat template mentions no tools; the assistant may not manage files, web or code with it."}</SwitchRow>
        <SwitchRow checked={thinking} onChange={setThinking} title="Thinking">
          {facts.thinking ? "Its chat template can switch thinking on and off."
            : "Shows the Think button for this model. Only for models that support it."}</SwitchRow>
      </>)}
    </Modal>
  );
}

/** Settings -> Models -> Image models -> Upload: an SDXL checkpoint from this computer. */
export function UploadImageModel({ choices, onClose, onAdded }: { choices: SdxlChoices; onClose: () => void;
  onAdded: () => void }) {
  const { toast } = useApp();
  const [file, setFile] = useState<Uploaded>(NONE);
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const [named, setNamed] = useState(false);
  const [settings, setSettings] = useState<SdxlSettings | null>(null);
  const [tuned, setTuned] = useState(false);
  const [adding, setAdding] = useState(false);
  const onFile = useCallback((u: Uploaded) => {
    setFile(u);
    const f = u.facts;
    if (f?.kind !== "sdxl") return;
    if (!named && f.suggested) {
      setName(f.suggested.name);
      setId(f.suggested.id);
    }
    if (!tuned) setSettings(presetSettings(choices, f.preset ?? "sdxl"));
  }, [named, tuned, choices]);
  const idOk = ID_OK.test(id);
  const add = async () => {
    if (!file.up || !settings) return;
    setAdding(true);
    try {
      await api("/hub/api/models/images/local", { body: { upload: file.up.id, id, name, settings } });
      toast(`${name || id} is among your image models - and a base to train LoRAs on`, "run");
      onAdded();
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setAdding(false);
    }
  };
  return (
    <Modal wide title="Upload an image model" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!file.done || file.facts?.kind !== "sdxl" || !idOk || !settings || adding}
        onClick={add}>{adding ? "Adding" : "Add"}</button></>}>
      <p className="muted small" style={{ margin: 0 }}>An SDXL checkpoint from this computer: SDXL itself, Pony,
        Illustrious, NoobAI or a model built on them - one .safetensors with the image model, both text encoders and
        the VAE (about 7 GB). The Hub reads what it is as soon as the start of the file has arrived. It can also be
        the base of a LoRA training (Train a LoRA, Train on).</p>
      <UploadField label="Checkpoint (.safetensors)" accept=".safetensors" onChange={onFile}
        refuse={(f) => f.kind === "sdxl" ? "" : `${f.reason || "That is not an SDXL checkpoint."}${f.kind === "lora"
          ? " Add a LoRA under LoRAs, below." : ""}`} />
      {file.facts?.kind === "sdxl" && settings && (<>
        <div className="form-grid">
          <label className="field"><span>Name</span><input className="input" value={name}
            onChange={(e) => { setName(e.target.value); setNamed(true); }} /></label>
          <label className="field"><span>Name for apps</span><input className="input mono" value={id}
            onChange={(e) => { setId(e.target.value.toLowerCase()); setNamed(true); }} />
            <small style={idOk ? undefined : { color: "var(--fault)" }}>{idOk ? "Chats and apps pick the model by this name."
              : "a-z, 0-9, dots, dashes; starts with a letter or digit."}</small></label>
        </div>
        <SdxlFields value={settings} choices={choices} onChange={(v) => { setSettings(v); setTuned(true); }} />
      </>)}
    </Modal>
  );
}

const baseName = (path: string) => path.split("/").pop() ?? path;
const accepting = (path: string) => { const m = /\.[A-Za-z0-9]+$/.exec(path); return m ? m[0] : ""; };

/** One missing file of a model, uploaded and put in its place at once. */
function FileRow({ name, size, here, onUpload, uploadKey, from }: { name: string; size?: number; here: boolean;
  uploadKey: string; onUpload: (up: UploadInfo) => Promise<void>; from?: string }) {
  const [placing, setPlacing] = useState(false);
  const [error, setError] = useState("");
  const [chosen, setChosen] = useState("");
  return (
    <div className="list-row" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
      <Lamp state={here ? "run" : "off"} />
      <div className="grow" style={{ minWidth: 220 }}>
        <b className="ellipsis" style={{ display: "block" }}>{name}</b>
        <span className="meta num">{[size ? bytes(size) : "", from ? `from ${from}` : "", here ? "here" : ""]
          .filter(Boolean).join(" · ")}</span>
        {!here && <UploadField key={uploadKey} label="" accept={accepting(name)} inspect={false} onChange={(u) => {
          if (u.file) setChosen(u.file.name);
          if (!u.done || !u.up || placing) return;
          setPlacing(true);
          setError("");
          onUpload(u.up).catch((e) => setError((e as Error).message)).finally(() => setPlacing(false));
        }} />}
        {!here && chosen && chosen !== name && <small className="muted">Named {chosen} on this computer - make sure it
          is {name}.</small>}
        {placing && <small className="muted">Checking it and putting it in place</small>}
        {error && <small className="warn-text">{error}</small>}
      </div>
    </div>
  );
}

/** A model in the Hub's list whose files are not all here: upload them instead of downloading them (a gated
 *  model, say, or one already on this computer). */
export function UploadModelFiles({ model, onClose, onDone }: { model: ModelEntry; onClose: () => void;
  onDone: () => void }) {
  const { toast } = useApp();
  const [here, setHere] = useState<Record<string, boolean>>(
    Object.fromEntries(model.files.map((f) => [f.dest, !!f.here])));
  const uploaded = model.source?.site === "upload";
  return (
    <Modal wide title={`Upload ${model.name}'s files`} onClose={onClose}
      footer={<button className="btn" onClick={() => { onDone(); onClose(); }}>Close</button>}>
      <p className="muted small" style={{ margin: 0 }}>{uploaded ? "This model was uploaded from a computer, and a file "
        + "of it is gone from the Hub: upload it again." : "Instead of downloading them: the files of this model from "
        + "this computer - the same files the download would fetch. Each is checked against the Hub's list (its size, "
        + "its kind) before it is put in place. Files that are here already are not needed again."}</p>
      <div className="list">
        {model.files.map((f) => (
          <FileRow key={f.dest} uploadKey={f.dest} name={baseName(f.path ?? f.dest)} size={f.size} here={here[f.dest]}
            onUpload={async (up) => {
              const r = await api<{ installed: boolean }>(`/hub/api/models/${encodeURIComponent(model.id)}/files`,
                { body: { upload: up.id, dest: f.dest } });
              setHere((h) => ({ ...h, [f.dest]: true }));
              if (r.installed) toast(`${model.name} is ready`, "run");
            }} />
        ))}
      </div>
    </Modal>
  );
}

interface BaseFile { repo: string; path: string; size: number; here: boolean; gated: boolean }

/** A FLUX.2 klein training's base model files (Hugging Face's): upload them instead of downloading them - the 9B
 *  base is gated, and a browser that has accepted its licence can fetch it. */
export function TrainingBaseFiles({ base, label, onClose }: { base: string; label: string; onClose: () => void }) {
  const [files, setFiles] = useState<BaseFile[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => api<{ files: BaseFile[] }>(`/hub/api/training/bases/${base}/files`)
    .then((r) => setFiles(r.files), (e) => setError((e as Error).message)), [base]);
  useEffect(() => { load(); }, [load]);
  const gated = files?.find((f) => f.gated && !f.here);
  return (
    <Modal wide title={`${label}: base model files`} onClose={onClose}
      footer={<button className="btn" onClick={onClose}>Close</button>}>
      <p className="muted small" style={{ margin: 0 }}>What a training on {label} reads. Missing files are downloaded
        when it starts{gated ? " - the gated one only with HF_TOKEN set" : ""}; or upload any of them from this
        computer instead, exactly as Hugging Face has them.{gated && <> {baseName(gated.path)} is on <a
          href={`https://huggingface.co/${gated.repo}`} target="_blank" rel="noreferrer">its Hugging Face page</a>:
          signed in there with its licence accepted, a browser can download it.</>}</p>
      {error && <div className="callout fault">{error}</div>}
      {!files && !error && <div className="faint">Asking Hugging Face what it needs</div>}
      {files && <div className="list">
        {files.map((f) => (
          <FileRow key={`${f.repo}/${f.path}`} uploadKey={`${f.repo}/${f.path}`} name={baseName(f.path)} size={f.size}
            from={f.repo} here={f.here} onUpload={async (up) => {
              await api(`/hub/api/training/bases/${base}/files`, { body: { upload: up.id, repo: f.repo, path: f.path } });
              await load();
            }} />
        ))}
      </div>}
    </Modal>
  );
}
