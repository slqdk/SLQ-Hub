// An SDXL model's settings (kind, sampler, quality tags, negative prompt) - for a model added from a link or
// uploaded from this computer (0.62.0), and its settings later.
import { type SdxlChoices, type SdxlSettings } from "../lib/api";

export function presetSettings(choices: SdxlChoices, key: string): SdxlSettings {
  const { label: _label, ...rest } = choices.presets[key] ?? choices.presets.sdxl;
  return { ...rest, preset: choices.presets[key] ? key : "sdxl" };
}

/** Kind (preset), sampler settings, quality tags and negative prompt of an SDXL model. */
export function SdxlFields({ value, onChange, choices }: { value: SdxlSettings; onChange: (v: SdxlSettings) => void;
  choices: SdxlChoices }) {
  const set = (patch: Partial<SdxlSettings>) => onChange({ ...value, ...patch });
  return (<>
    <label className="field"><span>Kind</span>
      <select className="select" value={value.preset} onChange={(e) => onChange(presetSettings(choices, e.target.value))}>
        {Object.entries(choices.presets).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}</select>
      <small>Sets the quality tags, sampler and clip skip that kind of model is made for.</small></label>
    <details className="sdxl-more">
      <summary>Sampler, tags and negative prompt</summary>
      <div className="stack" style={{ marginTop: 10 }}>
        <div className="form-grid">
          <label className="field"><span>Steps</span><input className="input" type="number" min={1} max={100}
            value={value.steps} onChange={(e) => set({ steps: Number(e.target.value) })} /></label>
          <label className="field"><span>CFG</span><input className="input" type="number" min={0} max={30} step={0.5}
            value={value.cfg} onChange={(e) => set({ cfg: Number(e.target.value) })} /></label>
          <label className="field"><span>Sampler</span><select className="select" value={value.sampler}
            onChange={(e) => set({ sampler: e.target.value })}>{choices.samplers.map((x) => <option key={x} value={x}>{x}</option>)}</select></label>
          <label className="field"><span>Scheduler</span><select className="select" value={value.scheduler}
            onChange={(e) => set({ scheduler: e.target.value })}>{choices.schedulers.map((x) => <option key={x} value={x}>{x}</option>)}</select></label>
          <label className="field"><span>Clip skip</span><select className="select" value={value.clip_skip}
            onChange={(e) => set({ clip_skip: Number(e.target.value) })}>
            <option value={1}>1 (most models)</option><option value={2}>2 (Pony and its finetunes)</option></select></label>
        </div>
        <label className="field"><span>Quality tags, put before every prompt</span>
          <textarea className="textarea" rows={2} style={{ minHeight: 56 }} value={value.prefix} onChange={(e) => set({ prefix: e.target.value })} /></label>
        <label className="field"><span>Negative prompt</span>
          <textarea className="textarea" rows={2} style={{ minHeight: 56 }} value={value.negative} onChange={(e) => set({ negative: e.target.value })} />
          <small>What the assistant asks to avoid is added to this.</small></label>
      </div>
    </details>
  </>);
}
