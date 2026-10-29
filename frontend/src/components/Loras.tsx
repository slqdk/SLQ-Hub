// LoRAs for the picture models (0.49.0): Settings -> Models. The admin's alone - people never see them.
import { useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";
import { api, type Lora, type LoraList } from "../lib/api";
import { bytes } from "../lib/format";
import { useApp } from "../state";
import { TrainingSection } from "./Training";
import { ConfirmButton, Lamp, Modal } from "./ui";

export function LorasSection() {
  const { toast } = useApp();
  const [data, setData] = useState<LoraList | null>(null);
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState<Lora | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const load = () => api<LoraList>("/hub/api/loras").then(setData, () => setData({ loras: [], families: {} }));
  useEffect(() => { load(); }, []);

  const add = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f, f.name);
    setBusy("upload");
    try {
      const l = await api<Lora>("/hub/api/loras", { form: fd });
      toast(`${l.name} added${l.base ? ` - for ${l.base_label} models` : " - say which models it is for"}`, "run");
      await load();
      if (!l.base || !l.trigger) setEditing(l);       // its family or trigger word could not be read from the file
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setBusy("");
    }
  };
  const remove = async (l: Lora) => {
    setBusy(`rm-${l.id}`);
    try {
      await api(`/hub/api/loras/${l.id}`, { method: "DELETE" });
      toast(`Deleted ${l.name}`, "run");
      await load();
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setBusy("");
    }
  };

  return (
    <section style={{ marginBottom: 26 }}>
      <div className="row" style={{ marginBottom: 8 }}>
        <h3 className="grow" style={{ margin: 0 }}>LoRAs</h3>
        <button className="btn small" disabled={busy === "upload"} onClick={() => input.current?.click()}>
          {busy === "upload" ? "Uploading..." : <><Plus size={14} /> Add LoRA</>}</button>
        <input ref={input} type="file" hidden accept=".safetensors"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) add(f); e.target.value = ""; }} />
      </div>
      <p className="muted small" style={{ marginTop: -4 }}>Small add-ons that teach a picture model a person, a thing or
        a style. Only you can use them - people's chats and apps never see them. Choose one for a chat in its picture
        menu, or ask for it by name. A LoRA works only with models of its family.</p>
      <div className="list">
        {data === null ? <div className="faint">Loading</div> : data.loras.length === 0
          ? <div className="list-row"><span className="muted">No LoRAs yet. Add a .safetensors LoRA from your computer,
            or train one below.</span></div>
          : data.loras.map((l) => (
            <div className="list-row" key={l.id} style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
              <Lamp state={l.fits.length ? "off" : "act"} />
              <div className="grow" style={{ minWidth: 220 }}>
                <b>{l.name}</b> <span className="tag">{l.base ? l.base_label : "Which models?"}</span>
                {l.source?.kind === "trained" && <span className="tag">Trained here</span>}
                <div className="meta">
                  {l.trigger ? <>Trigger word <b>{l.trigger}</b> · </> : null}strength {l.strength} · {bytes(l.size)}
                  {l.fits.length ? ` · works with ${l.fits.map((m) => m.name).join(", ")}`
                    : l.base ? ` · no ${l.base_label} model is downloaded` : ""}
                </div>
                {l.note && <div className="meta">{l.note}</div>}
              </div>
              <div className="row">
                <button className="btn quiet small" onClick={() => setEditing(l)}>Settings</button>
                <ConfirmButton className="btn quiet small" confirmText="Delete it and its file?"
                  disabled={busy === `rm-${l.id}`} onConfirm={() => remove(l)}>Delete</ConfirmButton>
              </div>
            </div>
          ))}
      </div>
      <TrainingSection onAdded={load} />
      {editing && data && <LoraSettings lora={editing} families={data.families} onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); load(); }} />}
    </section>
  );
}

function LoraSettings({ lora, families, onClose, onSaved }: { lora: Lora; families: Record<string, string>;
  onClose: () => void; onSaved: () => void }) {
  const { toast } = useApp();
  const [f, setF] = useState({ name: lora.name, base: lora.base, trigger: lora.trigger ?? "",
    strength: String(lora.strength), note: lora.note ?? "" });
  const save = async () => {
    try {
      await api(`/hub/api/loras/${lora.id}`, { method: "PATCH", body: { ...f, strength: Number(f.strength) } });
      onSaved();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <Modal title={`LoRA: ${lora.name}`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={save}>Save</button></>}>
      <label className="field"><span>Name</span>
        <input className="input" value={f.name} maxLength={80} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label className="field"><span>For which picture models</span>
        <select className="input" value={f.base} onChange={(e) => setF({ ...f, base: e.target.value })}>
          <option value="">Not known</option>
          {Object.entries(families).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <small>The family it was trained on - read from the file when it can be. A LoRA for another family does
          nothing.</small></label>
      <label className="field"><span>Trigger word</span>
        <input className="input" value={f.trigger} maxLength={120} placeholder="e.g. cx9020x"
          onChange={(e) => setF({ ...f, trigger: e.target.value })} />
        <small>The word it was trained with. It is put in the prompt for you.</small></label>
      <label className="field"><span>Strength</span>
        <input className="input" type="number" min={0} max={2} step={0.05} value={f.strength}
          onChange={(e) => setF({ ...f, strength: e.target.value })} />
        <small>1 is as trained; 0.6-0.9 for a lighter touch. A chat or a request can ask for another.</small></label>
      <label className="field"><span>What it is for</span>
        <input className="input" value={f.note} maxLength={500} placeholder="e.g. Our CX9020 controller, for course pictures"
          onChange={(e) => setF({ ...f, note: e.target.value })} />
        <small>The assistant reads this to know when to use it.</small></label>
    </Modal>
  );
}
