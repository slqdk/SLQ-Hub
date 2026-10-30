// LoRA training (0.50.0): Settings -> Models -> LoRAs -> Train a LoRA. The admin's alone - people never see it.
// A run: pictures in, captions (written by the chat model, or typed), then the whole card for a few hours, then a
// checkpoint - chosen by its sample pictures - becomes a LoRA.
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { CircleHelp, Copy, GraduationCap, ImagePlus, Sparkles, X } from "lucide-react";
import { api, type TrainingDetail, type TrainingOverview, type TrainingRun } from "../lib/api";
import { bytes, seconds } from "../lib/format";
import { useApp } from "../state";
import { ConfirmButton, Lamp, type LampState, Modal } from "./ui";

const ACTIVE = ["captioning", "preparing", "queued", "training"];
const KINDS: Record<string, { label: string; hint: string }> = {
  thing: { label: "A thing", hint: "An object or a product - a controller, a machine part, a logo on a box." },
  person: { label: "A person", hint: "Someone's face and look. Only with their consent." },
  style: { label: "A style", hint: "A look or a way of drawing, put on any subject." },
};
const UPLOAD_BATCH = 6;

const fileUrl = (rid: string, path: string, v?: number) =>
  `/hub/api/training/runs/${rid}/file?path=${encodeURIComponent(path)}${v ? `&v=${v}` : ""}`;

/** Words a picture model knows, found inside a trigger word: it reads them (Søren's "waterx55" turned the
 *  watercolour sample into a lake - 2026-10-30). Four letters or more, so a made-up word rarely hits one. */
const KNOWN_WORDS = ["water", "lora", "woman", "girl", "photo", "style", "paint", "color", "colour", "light", "fire",
  "snow", "rock", "gold", "blue", "pink", "black", "white", "green", "bird", "lake", "tree", "star", "moon", "rose",
  "love", "baby", "lady", "king", "queen", "face", "hair", "body", "art", "cat", "dog", "man", "car", "sun", "sea"];

export function triggerWarning(word: string): string {
  const w = word.toLowerCase();
  const known = KNOWN_WORDS.filter((k) => k.length >= 4 || w === k || w.startsWith(k) || w.endsWith(k))
    .find((k) => w.includes(k));
  if (known) return `It has the word "${known}" in it - the model reads it, and it can pull the pictures towards ` +
    `${known === "lora" ? "other things" : `"${known}"`}. A made-up word without real words works better.`;
  if (/\d/.test(w)) return "Digits can come out as text in the pictures (a signature, a label). Letters only work best.";
  return "";
}

/** One line on where a run is: "Step 750 of 2000 · 41 min left". */
export function progressText(run: TrainingRun): string {
  const p = run.progress;
  switch (run.state) {
    case "captioning":
      return p?.total ? `Writing captions: ${p.done ?? 0} of ${p.total}` : "Writing captions";
    case "preparing":
      return p?.what === "download" && p.total
        ? `Downloading the base model: ${bytes(p.done)} of ${bytes(p.total)}` : "Getting the base model ready";
    case "queued":
      return "Waiting for the card";
    case "training":
      if (p?.what === "stopping") return "Stopping - the card is free when it has";
      if (p?.what === "training" && p.total && p.step)
        return `Step ${p.step} of ${p.total}${p.eta_s ? ` · ${seconds(p.eta_s)} left` : ""}`;
      return "Starting - loading the model and caching the pictures";
    case "done": return "Done";
    case "failed": return "Stopped with an error";
    case "cancelled": return "Stopped";
    default: return "Not started";
  }
}

function lampOf(run: TrainingRun): LampState {
  if (run.state === "training") return "act";
  if (ACTIVE.includes(run.state)) return "wait";
  if (run.state === "failed") return "fault";
  return run.state === "done" ? "run" : "off";
}

function fraction(run: TrainingRun): number | null {
  const p = run.progress;
  if (!p || !p.total) return null;
  if (p.what === "training") return (p.step ?? 0) / p.total;
  if (p.what === "download" || p.what === "captions") return (p.done ?? 0) / p.total;
  return null;
}

function ProgressBar({ run }: { run: TrainingRun }) {
  const f = fraction(run);
  if (f === null) return null;
  return <div className={`bar ${run.state === "training" ? "" : "wait"}`} style={{ marginTop: 6 }}>
    <span style={{ width: `${Math.max(2, Math.min(100, f * 100))}%` }} /></div>;
}

export function TrainingSection({ onAdded }: { onAdded: () => void }) {
  const [data, setData] = useState<TrainingOverview | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [copying, setCopying] = useState<TrainingRun | null>(null);
  const load = useCallback(() => api<TrainingOverview>("/hub/api/training")
    .then((d) => { setData(d); setFailed(false); }, () => setFailed(true)), []);
  useEffect(() => { load(); }, [load]);
  const working = !!data?.runs.some((r) => ACTIVE.includes(r.state));
  useEffect(() => {
    if (!working || open) return;
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, [working, open, load]);
  if (failed || !data) return null;

  return (
    <div style={{ marginTop: 18 }}>
      <div className="row" style={{ marginBottom: 6 }}>
        <h4 className="grow" style={{ margin: 0 }}>Train a LoRA</h4>
        <button className="btn small" onClick={() => setCreating(true)}><GraduationCap size={14} /> New training</button>
      </div>
      <p className="muted small" style={{ marginTop: 0 }}>Teach {data.base.label} a thing, a person or a style from your
        own pictures, here on the card. A training takes the whole card for a few hours: chats and pictures wait
        until it is done.</p>
      {!data.service && (
        <div className="callout small" style={{ marginBottom: 8 }}>The trainer is not running. Pictures and captions
          can be prepared now; to train, add <code>COMPOSE_PROFILES=training</code> to <code>/srv/slq-hub/.env</code> on
          the VM and run <code>update.sh</code> - the first build takes a while.</div>
      )}
      {data.runs.length > 0 && (
        <div className="list">
          {data.runs.map((r) => (
            <div className="list-row" key={r.id} style={{ flexWrap: "wrap" }}>
              <Lamp state={lampOf(r)} />
              <div className="grow" style={{ minWidth: 200 }}>
                <b>{r.name}</b> <span className="tag">{KINDS[r.kind]?.label ?? r.kind}</span>
                <div className="meta">Trigger word <b>{r.trigger}</b> · {r.images ?? 0} pictures · {progressText(r)}
                  {r.size ? ` · ${bytes(r.size)}` : ""}</div>
                <ProgressBar run={r} />
              </div>
              <button className="btn quiet small" onClick={() => setOpen(r.id)}>Open</button>
            </div>
          ))}
        </div>
      )}
      {creating && <NewTraining data={data} onClose={() => setCreating(false)}
        onMade={(id) => { setCreating(false); load(); setOpen(id); }} />}
      {open && <TrainingEditor key={open} rid={open} data={data} onClose={() => { setOpen(null); load(); }}
        onAdded={onAdded} onDuplicate={(run) => { setOpen(null); setCopying(run); }} />}
      {copying && <DuplicateTraining run={copying} onClose={() => { setOpen(copying.id); setCopying(null); }}
        onMade={(id) => { setCopying(null); load(); setOpen(id); }} />}
    </div>
  );
}

function NewTraining({ data, onClose, onMade }: { data: TrainingOverview; onClose: () => void;
  onMade: (id: string) => void }) {
  const { toast } = useApp();
  const [f, setF] = useState({ name: "", trigger: "", kind: "thing", steps: "1500", rank: "16" });
  const [busy, setBusy] = useState(false);
  const make = async () => {
    setBusy(true);
    try {
      const run = await api<TrainingRun>("/hub/api/training/runs",
        { body: { ...f, steps: Number(f.steps), rank: Number(f.rank) } });
      onMade(run.id);
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="New training" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !f.name.trim() || f.trigger.length < 3} onClick={make}>
          Create</button></>}>
      <label className="field"><span>Name</span>
        <input className="input" value={f.name} maxLength={80} placeholder="e.g. CX9020 controller" autoFocus
          onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label className="field"><span>What it learns</span>
        <select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
          {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <small>{KINDS[f.kind].hint}</small></label>
      <label className="field"><span>Trigger word</span>
        <input className="input" value={f.trigger} maxLength={40} placeholder="e.g. rkxv"
          onChange={(e) => setF({ ...f, trigger: e.target.value.replace(/[^A-Za-z0-9_]/g, "") })} />
        <small>One made-up word the model does not know, 3-40 letters - no real word inside it. A prompt with it gets
          what the LoRA learned.</small>
        {triggerWarning(f.trigger) && <small className="warn-text">{triggerWarning(f.trigger)}</small>}</label>
      <div className="row" style={{ gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
        <label className="field grow" style={{ minWidth: 180 }}><span>Steps</span>
          <input className="input" type="number" min={250} max={6000} step={250} value={f.steps}
            onChange={(e) => setF({ ...f, steps: e.target.value })} />
          <small>1500 is a good start. A checkpoint with sample pictures is saved every 250 steps, so an earlier one
            can be chosen.</small></label>
        <label className="field" style={{ minWidth: 140 }}><span>Rank</span>
          <select className="input" value={f.rank} onChange={(e) => setF({ ...f, rank: e.target.value })}>
            {[8, 16, 32, 64].map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <small>16 suits most. Higher learns finer detail and makes a bigger file.</small></label>
      </div>
      <small className="muted">Trained on {data.base.label}'s base model, used with {data.base.label} in your chats.
        Only you can use it.</small>
    </Modal>
  );
}

type Guide = "" | "steps" | "rank" | "choose" | "better";

/** A small "?" that opens a guide below (a click, so it works on a phone). Inside a label it does not focus the
 *  field. */
function HelpDot({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return <button type="button" className={`help-dot ${on ? "on" : ""}`} aria-label={label} aria-expanded={on}
    title={label} onClick={(e) => { e.preventDefault(); onClick(); }}><CircleHelp size={14} /></button>;
}

/** Which pictures, by what it learns. A person LoRA learns everything the pictures share that the captions leave
 *  out - face, hair, build, and also an outfit or background seen again and again. */
const PICTURES: Record<string, ReactNode> = {
  person: <>For a person, a mix: about <b>half close-ups of the face</b> (front, three-quarter, profile, different
    expressions), <b>a third head and shoulders</b>, <b>the rest full body</b>. Without full-body pictures it can get
    the face right and give the body of a stranger, and a face further away looks less like them. Leave out other
    people (crop them away), beauty filters, the same outfit or background again and again, and sunglasses, hats or
    hands over the face.</>,
  thing: <>For a thing: from all sides and angles, close-ups of the details and the whole of it, on different
    backgrounds and in different light. It should be the main subject - crop away what is always around it.</>,
  style: <>For a style: many different subjects in that style - people, animals, places, things - so it learns the
    style and not a subject. Leave out pictures that are only a little like it.</>,
};
const CAPTIONS: Record<string, string> = {
  person: "describe clothes, place, pose, light, expression and framing (close-up, full body) - not the face or the " +
    "build, and the hair only when it changes (then it can be asked for).",
  thing: "describe where it is, the background, the light and the angle - not the thing itself, its colour or brand.",
  style: "describe what each picture shows - not the style, the colours or the technique.",
};
const PICTURE_HINT: Record<string, string> = {
  person: "15-40 sharp pictures, 1024 px or more: about half close-ups of the face, a third head and shoulders, the " +
    "rest full body - in different places, light and clothes. No other people, no beauty filters.",
  thing: "15-40 sharp pictures, 1024 px or more, from all sides, close and further away, on different backgrounds.",
  style: "15-40 pictures in the style, 1024 px or more, of many different subjects.",
};

/** The advice for a training (Søren: "write it into the interface, or I cannot remember it"). */
function GuideText({ guide, kind }: { guide: Guide; kind: string }) {
  const likeness = kind === "style" ? "has the style" : kind === "person" ? "looks like the person" : "looks like the thing";
  if (guide === "steps") return <>
    <b>Steps</b> are how long it trains. 1500 is a good start for 15-40 pictures; more pictures want more steps.
    A checkpoint with sample pictures is saved every 250 steps, so too many steps only cost time - you can choose an
    earlier one. While it trains, the page shows the time left.</>;
  if (guide === "rank") return <>
    <b>Rank</b> is how much the LoRA can hold. 16 suits a face or a thing. 32 for fine detail or a detailed style:
    it learns more, but copies your pictures sooner, and the file is twice the size.</>;
  if (guide === "choose") return <>
    <b>Which step to keep</b>
    <ul>
      <li>Every 250 steps a checkpoint is saved, with the same three sample pictures made with the trigger word.
        "Before training" is the model without the LoRA.</li>
      <li>Choose the step where it {likeness} <i>and</i> still does what the prompt says - the watercolour is still a
        watercolour, the café still a café.</li>
      <li><b>Too early:</b> it does not look like it yet.</li>
      <li><b>Too late:</b> stiff or plastic faces, the same pose, clothes or background as in your pictures, burnt
        colours, the watercolour turning into a photo.</li>
      <li><b>A sample that shows something else</b> at some steps - a lake or a field instead of them in the
        watercolour: it is not yet sure of them outside photos, and the prompt tips either way (the samples use the
        same seed at every step). Prefer a step where all three show them. A trigger word with a real word in it
        (water..., lora...) makes it worse - give a v2 a new one with Duplicate. Scribbles in a corner are the
        trigger word written as a signature.</li>
      <li>Add as LoRA on that step and try it in a chat. Weaker there than in the samples? Raise its strength to
        1.1-1.2 in the LoRA's settings: the samples use the base model, chats the fast 4-step one.</li>
    </ul></>;
  return <>
    <b>How to make it better</b>
    <ul>
      <li><b>Keep the best first:</b> Add as LoRA on the best step - Train again deletes this training's checkpoints.
        Or Duplicate it into a v2, which keeps them here.</li>
      <li><b>Done with it?</b> Once the LoRA you want is added, Free (at the bottom) deletes the checkpoints - a few
        GB per training. Pictures, captions, samples and the added LoRA stay.</li>
      <li><b>One change a round,</b> so you know what helped, and name the versions (v1, v2).</li>
      <li><b>Steps:</b> best step was the last one - give it 500-1000 more. Best step was early - give it fewer, or
        just keep that one.</li>
      <li><b>Pictures matter most:</b> sharp and large (1024 px or more); 20-30 good pictures beat 35 mixed ones.
        {" "}{PICTURES[kind] ?? PICTURES.thing}</li>
      <li><b>Captions:</b> {CAPTIONS[kind] ?? CAPTIONS.thing} What a caption leaves out becomes part of the trigger
        word. Correct wrong details in written captions.</li>
      {kind === "person" && <li><b>Check the mix:</b> in the café sample the person is further away than in the
        portrait. Looks like them there too - the mix is right. Only the portrait does - add more half and full-body
        pictures in a v2.</li>}
      <li><b>Rank:</b> 16 suits most; 32 when fine details are missing.</li>
      <li><b>Using it:</b> strength 1 is as trained, 0.7-0.9 a lighter touch. Set its default in the LoRA's
        settings.</li>
    </ul></>;
}

/** "Rikke" -> "Rikke v2", "Rikke v2" -> "Rikke v3" (as the Hub names a copy). */
function nextName(name: string): string {
  const m = /^(.*?)\s+v(\d+)$/.exec(name.trim());
  return m ? `${m[1]} v${Number(m[2]) + 1}` : `${name.trim().slice(0, 76)} v2`;
}

function DuplicateTraining({ run, onClose, onMade }: { run: TrainingRun; onClose: () => void;
  onMade: (id: string) => void }) {
  const { toast } = useApp();
  const [f, setF] = useState({ name: nextName(run.name), trigger: run.trigger, kind: run.kind as string });
  const [busy, setBusy] = useState(false);
  const make = async () => {
    setBusy(true);
    try {
      const copy = await api<TrainingRun>(`/hub/api/training/runs/${run.id}/duplicate`, { body: f });
      toast(`${copy.name}: ${run.images ?? "the"} pictures and their captions copied`, "run");
      onMade(copy.id);
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Duplicate ${run.name}`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !f.name.trim() || f.trigger.length < 3} onClick={make}>
          Duplicate</button></>}>
      <p className="muted small" style={{ margin: 0 }}>A new training with the same pictures, captions, steps and rank -
        not the checkpoints or samples, which stay with {run.name}. Change what you want to try in the copy.</p>
      <label className="field"><span>Name</span>
        <input className="input" value={f.name} maxLength={80} autoFocus
          onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label className="field"><span>Trigger word</span>
        <input className="input" value={f.trigger} maxLength={40}
          onChange={(e) => setF({ ...f, trigger: e.target.value.replace(/[^A-Za-z0-9_]/g, "") })} />
        <small>A new one replaces {run.trigger} in every caption.</small>
        {triggerWarning(f.trigger) && <small className="warn-text">{triggerWarning(f.trigger)}</small>}</label>
      <label className="field"><span>What it learns</span>
        <select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
          {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select></label>
    </Modal>
  );
}

function TrainingEditor({ rid, data, onClose, onAdded, onDuplicate }: { rid: string; data: TrainingOverview;
  onClose: () => void; onAdded: () => void; onDuplicate: (run: TrainingRun) => void }) {
  const { toast } = useApp();
  const [run, setRun] = useState<TrainingDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [settings, setSettings] = useState<{ name: string; steps: string } | null>(null);
  const [guide, setGuide] = useState<Guide>("");
  const guideBox = useRef<HTMLDivElement>(null);
  const toggle = (g: Guide) => setGuide(guide === g ? "" : g);
  const openGuide = (g: Guide) => {           // from a link further down: the guide is at the top
    setGuide(g);
    window.requestAnimationFrame(() => guideBox.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };
  const input = useRef<HTMLInputElement>(null);
  const load = useCallback(() => api<TrainingDetail>(`/hub/api/training/runs/${rid}`)
    .then((r) => { setRun(r); setError(""); }, (e) => setError((e as Error).message)), [rid]);
  useEffect(() => { load(); }, [load]);
  const working = !!run && ACTIVE.includes(run.state);
  useEffect(() => {
    if (!working) return;
    const t = window.setInterval(load, 3000);
    return () => window.clearInterval(t);
  }, [working, load]);

  const act = async (what: string, call: () => Promise<unknown>, done?: string) => {
    setBusy(what);
    try {
      await call();
      if (done) toast(done, "run");
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setBusy("");
      await load();
    }
  };
  const upload = async (files: File[]) => {
    let added = 0;
    setBusy("upload");
    try {
      for (let i = 0; i < files.length; i += UPLOAD_BATCH) {
        setBusy(`upload ${Math.min(i + UPLOAD_BATCH, files.length)} of ${files.length}`);
        const fd = new FormData();
        for (const f of files.slice(i, i + UPLOAD_BATCH)) fd.append("files", f, f.name);
        added += (await api<{ added: string[] }>(`/hub/api/training/runs/${rid}/images`, { form: fd })).added.length;
      }
      toast(`Added ${added} picture${added === 1 ? "" : "s"}`, "run");
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setBusy("");
      await load();
    }
  };
  const saveCaption = (name: string) => {
    const text = drafts[name];
    const was = run?.images_list.find((im) => im.name === name)?.caption ?? "";
    if (text === undefined || text.trim() === was) return;
    act(`cap-${name}`, () => api(`/hub/api/training/runs/${rid}/captions`, { method: "PUT",
      body: { captions: { [name]: text } } })).then(() => setDrafts((d) => { const n = { ...d }; delete n[name]; return n; }));
  };
  const saveSettings = (change: Record<string, unknown>) =>
    act("settings", () => api(`/hub/api/training/runs/${rid}`, { method: "PATCH", body: change }));

  if (!run) {
    return <Modal title="Training" onClose={onClose} wide>
      {error ? <div className="callout fault">{error}</div> : <div className="faint">Loading</div>}</Modal>;
  }
  const pics = run.images_list;
  const uncaptioned = pics.filter((im) => !im.caption).length;
  const editable = !working;
  const steps = [...new Set(run.samples.map((s) => s.step))].sort((a, b) => b - a);
  const checkpoint = (step: number) => run.checkpoints.find((c) => c.step === step);
  const ready = pics.length >= data.min_images && uncaptioned === 0;
  const s = settings ?? { name: run.name, steps: String(run.steps) };

  return (
    <Modal title={`Training: ${run.name}`} onClose={onClose} wide
      footer={<>
        <ConfirmButton className="btn quiet" confirmText="Delete the training, its pictures and checkpoints?"
          disabled={working} onConfirm={() => act("delete", () => api(`/hub/api/training/runs/${rid}`,
            { method: "DELETE" }), "Deleted").then(onClose)}>Delete training</ConfirmButton>
        <span className="grow" />
        {run.freeable > 0 && <ConfirmButton className="btn quiet" disabled={working || !!busy}
          confirmText="Delete its checkpoints? Pictures, captions, samples and added LoRAs stay."
          onConfirm={() => act("free", async () => {
            const r = await api<{ freed: number }>(`/hub/api/training/runs/${rid}/free`, { method: "POST" });
            toast(`Freed ${bytes(r.freed)}`, "run");
          })}>Free {bytes(run.freeable)}</ConfirmButton>}
        <button className="btn" disabled={!pics.length}
          onClick={() => onDuplicate({ ...run, images: pics.length })}><Copy size={14} /> Duplicate</button>
        <button className="btn" onClick={onClose}>Close</button>
      </>}>
      <div className="train-settings">
        <label className="field train-name"><span>Name</span>
          <input className="input" value={s.name} maxLength={80} disabled={!editable}
            onChange={(e) => setSettings({ ...s, name: e.target.value })}
            onBlur={() => s.name.trim() && s.name !== run.name && saveSettings({ name: s.name })} /></label>
        <label className="field"><span>Steps <HelpDot on={guide === "steps"} onClick={() => toggle("steps")}
          label="About steps" /></span>
          <input className="input" type="number" min={250} max={6000} step={250} value={s.steps} disabled={!editable}
            onChange={(e) => setSettings({ ...s, steps: e.target.value })}
            onBlur={() => Number(s.steps) !== run.steps && saveSettings({ steps: Number(s.steps) })} /></label>
        <label className="field"><span>Rank <HelpDot on={guide === "rank"} onClick={() => toggle("rank")}
          label="About rank" /></span>
          <select className="input" value={run.rank} disabled={!editable}
            onChange={(e) => saveSettings({ rank: Number(e.target.value) })}>
            {[8, 16, 32, 64].map((r) => <option key={r} value={r}>{r}</option>)}
          </select></label>
      </div>
      <div className="row train-info">
        <span className="grow muted small">
          {KINDS[run.kind]?.label ?? run.kind} · trigger word <b>{run.trigger}</b> · {data.base.label}</span>
        <button type="button" className={`btn quiet small ${guide === "choose" ? "on" : ""}`}
          aria-expanded={guide === "choose"} onClick={() => toggle("choose")}><CircleHelp size={14} /> Which step?</button>
        <button type="button" className={`btn quiet small ${guide === "better" ? "on" : ""}`}
          aria-expanded={guide === "better"} onClick={() => toggle("better")}><CircleHelp size={14} /> Make it better</button>
      </div>
      {guide && <div className="callout train-guide" ref={guideBox}><GuideText guide={guide} kind={run.kind} /></div>}

      {/* Where it is */}
      {working && (
        <div className="callout">
          <div className="row"><Lamp state={lampOf(run)} /> <b className="grow">{progressText(run)}</b>
            {run.state !== "captioning" && run.progress?.what !== "stopping" && (
              <ConfirmButton className="btn small" confirmText="Stop the training?"
                onConfirm={() => act("stop", () => api(`/hub/api/training/runs/${rid}/cancel`, { method: "POST" }))}>
                Stop</ConfirmButton>)}
          </div>
          <ProgressBar run={run} />
          {run.progress?.loss != null && <div className="meta small" style={{ marginTop: 6 }}>
            Loss {run.progress.loss.toFixed(3)} - it jumps about; the sample pictures say more.</div>}
          {!!run.progress?.log?.length && <details style={{ marginTop: 6 }}><summary className="small">The trainer's
            last lines</summary><pre className="code-line">{run.progress.log.join("\n")}</pre></details>}
        </div>
      )}
      {run.state === "failed" && (
        <div className="callout fault">{run.error || "The training stopped."}
          {!!run.log?.length && <details style={{ marginTop: 6 }}><summary className="small">The trainer's last
            lines</summary><pre className="code-line">{run.log.join("\n")}</pre></details>}
        </div>
      )}
      {run.state === "draft" && run.error && <div className="callout fault">{run.error}</div>}

      {/* Results */}
      {steps.length > 0 && (
        <section>
          <h4 style={{ margin: "0 0 4px" }}>Sample pictures</h4>
          <p className="muted small" style={{ marginTop: 0 }}>Made with the trigger word every 250 steps. Choose the
            step where it looks right before it starts to look stiff or copy the pictures - that is the one to
            keep. <button type="button" className="link-btn" onClick={() => openGuide("choose")}>How to tell</button></p>
          <div className="train-steps">
            {steps.map((st) => {
              const cp = checkpoint(st);
              return (
                <div className="train-step" key={st}>
                  <div className="train-step-head">
                    <b>{st === 0 ? "Before training" : st === run.steps ? `Step ${st} (the last)` : `Step ${st}`}</b>
                    {cp && <button className="btn small" disabled={!!busy}
                      onClick={() => act(`use-${st}`, async () => {
                        const l = await api<{ name: string }>(`/hub/api/training/runs/${rid}/use`, { body: { step: st } });
                        toast(`${l.name} is now among your LoRAs`, "run");
                        onAdded();
                      })}><Sparkles size={14} /> Add as LoRA</button>}
                  </div>
                  <div className="train-step-pics">
                    {run.samples.filter((x) => x.step === st).map((x) => (
                      <a key={x.path} href={fileUrl(rid, x.path)} target="_blank" rel="noreferrer">
                        <img src={fileUrl(rid, x.path)} alt={`Sample ${x.n + 1} at step ${st}`} loading="lazy" /></a>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Pictures and captions */}
      <section>
        <div className="row" style={{ marginBottom: 4, flexWrap: "wrap" }}>
          <h4 className="grow" style={{ margin: 0 }}>Pictures <span className="muted">({pics.length})</span></h4>
          <button className="btn small" disabled={!editable || !!busy || pics.length >= data.max_images}
            onClick={() => input.current?.click()}>
            {busy.startsWith("upload") ? `Uploading ${busy.slice(7)}` : <><ImagePlus size={14} /> Add pictures</>}</button>
          <input ref={input} type="file" multiple hidden accept="image/*,.heic,.heif"
            onChange={(e) => { const f = [...(e.target.files ?? [])]; if (f.length) upload(f); e.target.value = ""; }} />
        </div>
        <p className="muted small" style={{ marginTop: 0 }}>{PICTURE_HINT[run.kind] ?? PICTURE_HINT.thing} They are
          turned upright and scaled to at most 2048 px. <button type="button" className="link-btn"
            onClick={() => openGuide("better")}>More tips</button></p>
        {pics.length > 0 && (
          <>
            <p className="muted small" style={{ margin: "0 0 6px" }}>Captions describe what changes from picture to
              picture - the place, the light, the angle - not the {run.kind === "style" ? "style" : run.kind} itself:
              what they leave out is what the trigger word learns.</p>
            <div className="row" style={{ marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
              {data.can_caption ? <>
                <button className="btn small" disabled={!editable || !!busy || uncaptioned === 0}
                  onClick={() => act("captions", () => api(`/hub/api/training/runs/${rid}/captions/auto`,
                    { body: { overwrite: false } }))}>Write {uncaptioned || ""} caption{uncaptioned === 1 ? "" : "s"}</button>
                <ConfirmButton className="btn quiet small" confirmText="Replace every caption?"
                  disabled={!editable || !!busy} onConfirm={() => act("captions", () =>
                    api(`/hub/api/training/runs/${rid}/captions/auto`, { body: { overwrite: true } }))}>
                  Rewrite all</ConfirmButton>
              </> : <span className="faint small">The chat model cannot see pictures - type the captions.</span>}
            </div>
            <div className="train-pics">
              {pics.map((im) => (
                <div className="train-pic" key={im.name}>
                  <img src={fileUrl(rid, `images/${im.name}`, im.v)} alt={im.caption || im.name} loading="lazy" />
                  {editable && <button className="icon-btn train-pic-x" aria-label={`Remove ${im.name}`}
                    disabled={!!busy} onClick={() => act(`rm-${im.name}`, () =>
                      api(`/hub/api/training/runs/${rid}/images/${encodeURIComponent(im.name)}`, { method: "DELETE" }))}>
                    <X size={14} /></button>}
                  <textarea className="input" rows={3} maxLength={600} disabled={!editable}
                    placeholder={run.state === "captioning" ? "Being written..." : "What is in the picture"}
                    value={drafts[im.name] ?? im.caption}
                    onChange={(e) => setDrafts({ ...drafts, [im.name]: e.target.value })}
                    onBlur={() => saveCaption(im.name)} />
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      {/* Start */}
      {editable && (
        <div className="row" style={{ flexWrap: "wrap", gap: 10 }}>
          <span className="grow muted small">
            {pics.length < data.min_images ? `At least ${data.min_images} pictures (15-40 is best).`
              : uncaptioned ? `${uncaptioned} picture${uncaptioned === 1 ? " has" : "s have"} no caption yet.`
                : !data.service ? "The trainer is not running (see above)."
                  : `${run.steps} steps. The first time, about 16 GB of model files are downloaded first.`}</span>
          <ConfirmButton className="btn primary" disabled={!ready || !data.service || !!busy}
            confirmText={run.checkpoints.length ? "Busy for hours, and these checkpoints go (Duplicate keeps them) - start?"
              : "The card is busy for hours - start?"}
            onConfirm={() => act("start", () => api(`/hub/api/training/runs/${rid}/start`, { method: "POST" }))}>
            {run.state === "draft" ? "Start training" : "Train again"}</ConfirmButton>
        </div>
      )}
    </Modal>
  );
}
