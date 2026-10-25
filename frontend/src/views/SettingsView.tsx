// Settings: models, the queue, apps and keys, the agent, skills, memory, MCP, cloud, system.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp, Copy, Download, Menu, Plus, RefreshCw, Upload as UploadIcon, X } from "lucide-react";
import { api, type ApiKey, type Person, type HFLookup, type HFSearchHit, type ImageLookup, type McpServerConfig, type McpStatus,
  type MemoryFile, type ModelEntry, type Sampling, type SdxlChoices, type SdxlSettings, type Settings, type Skill } from "../lib/api";
import { ago, bytes, residencyName } from "../lib/format";
import { navigate } from "../lib/router";
import { useApp, type Theme } from "../state";
import { QueueModal, plateState } from "../components/Queue";
import { ConfirmButton, Empty, Lamp, Modal, Switch, SwitchRow, Tabs, copyText, useInterval, type LampState } from "../components/ui";
import { TestsTab } from "../components/TestsTab";
import { forgetSettings } from "./ChatView";

const TABS = [
  { id: "models", label: "Models" }, { id: "queue", label: "Queue" }, { id: "people", label: "People" },
  { id: "apps", label: "Apps and keys" },
  { id: "agent", label: "Assistant" }, { id: "skills", label: "Skills" }, { id: "memory", label: "Memory" },
  { id: "mcp", label: "MCP servers" }, { id: "cloud", label: "Cloud" }, { id: "tests", label: "Tests" },
  { id: "system", label: "System" },
];

function useSettings() {
  const { toast, refreshAuth } = useApp();
  const [data, setData] = useState<{ settings: Settings; defaults: Settings } | null>(null);
  const load = useCallback(async () => {
    try {
      setData(await api("/hub/api/settings"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load]);
  const save = useCallback(async (section: string, values: Record<string, unknown>) => {
    try {
      const r = await api<{ settings: Settings }>("/hub/api/settings", { method: "PUT", body: { section, values } });
      setData((d) => (d ? { ...d, settings: r.settings } : d));
      forgetSettings();
      if (section === "web") refreshAuth();           // whether the internet asks first shapes how answers show
      toast("Saved", "run");
      return true;
    } catch (e) {
      toast((e as Error).message, "fault");
      return false;
    }
  }, [toast, refreshAuth]);
  return { data, save, reload: load };
}

export function SettingsView({ tab }: { tab: string }) {
  const { setSidebarOpen } = useApp();
  const current = TABS.some((t) => t.id === tab) ? tab : "models";
  const s = useSettings();
  return (
    <div className="main">
      <div className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open the menu"><Menu size={18} /></button>
        <h1>Settings</h1>
      </div>
      <div className="scroll">
        <div className="page">
          <Tabs tabs={TABS} value={current} onChange={(tab) => navigate({ name: "settings", tab }, true)} />
          {current === "models" && <ModelsTab s={s} />}
          {current === "queue" && <QueueTab s={s} />}
          {current === "people" && <PeopleTab />}
          {current === "apps" && <AppsTab s={s} />}
          {current === "agent" && <AgentTab s={s} />}
          {current === "skills" && <SkillsTab />}
          {current === "memory" && <MemoryTab />}
          {current === "mcp" && <McpTab s={s} />}
          {current === "cloud" && <CloudTab s={s} />}
          {current === "tests" && <TestsTab />}
          {current === "system" && <SystemTab />}
        </div>
      </div>
    </div>
  );
}

type S = ReturnType<typeof useSettings>;

// ------------------------------------------------------------ a small form for one settings section

interface FieldSpec { key: string; label: string; type: "number" | "text" | "bool" | "select" | "password";
  help?: ReactNode; options?: { value: string; label: string }[]; min?: number; max?: number; step?: number }

function SectionForm({ s, section, fields, title, intro }: { s: S; section: string; fields: FieldSpec[]; title?: string;
  intro?: ReactNode }) {
  const saved = s.data?.settings[section] ?? {};
  const [values, setValues] = useState<Record<string, unknown>>(saved);
  const key = JSON.stringify(saved);
  useEffect(() => setValues(JSON.parse(key)), [key]);
  if (!s.data) return <div className="faint">Loading</div>;
  const dirty = JSON.stringify(values) !== key;
  return (
    <section className="stack" style={{ marginBottom: 28 }}>
      {title && <h3 style={{ margin: 0 }}>{title}</h3>}
      {intro && <p className="muted" style={{ margin: 0, maxWidth: "62ch" }}>{intro}</p>}
      <div className="form-grid">
        {fields.filter((f) => f.type !== "bool").map((f) => (
          <label className="field" key={f.key}><span>{f.label}</span>
            {f.type === "select" ? (
              <select className="select" value={String(values[f.key] ?? "")} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}>
                {f.options!.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            ) : (
              <input className="input" type={f.type === "number" ? "number" : f.type === "password" ? "password" : "text"}
                value={values[f.key] == null ? "" : String(values[f.key])} min={f.min} max={f.max} step={f.step}
                autoComplete="off"
                onChange={(e) => setValues({ ...values, [f.key]: f.type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value })} />
            )}
            {f.help && <small>{f.help}</small>}
          </label>
        ))}
      </div>
      {fields.filter((f) => f.type === "bool").map((f) => (
        <SwitchRow key={f.key} checked={!!values[f.key]} onChange={(v) => setValues({ ...values, [f.key]: v })} title={f.label}>{f.help}</SwitchRow>
      ))}
      <div><button className="btn primary" disabled={!dirty} onClick={() => s.save(section, values)}>Save changes</button></div>
    </section>
  );
}

// ------------------------------------------------------------ models

function ModelsTab({ s }: { s: S }) {
  const { models, refreshModels, toast, queue, modelNames } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState<ModelEntry | null>(null);
  const downloading = !!models && [...models.llm, ...models.aux, ...models.comfy, ...(models.audio ?? []),
    ...(models.video ?? [])]
    .some((m) => m.downloads.some((d) => d.status === "downloading" || d.status === "queued"));
  useInterval(refreshModels, downloading ? 2000 : 15000);

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setBusy(null);
      refreshModels();
    }
  };
  const download = (id: string) => act(`dl-${id}`, () => api(`/hub/api/models/${id}/download`, { method: "POST" }));
  const cancel = (id: string) => act(`cancel-${id}`, () => api(`/hub/api/models/${id}/cancel-download`, { method: "POST" }));
  const load = (id: string) => act(`load-${id}`, () => api(`/hub/api/models/${id}/load`, { method: "POST" }));
  const unload = () => act("unload", () => api("/hub/api/models/unload", { method: "POST" }));
  const remove = (m: ModelEntry) => act(`rm-${m.id}`, async () => {
    await api(`/hub/api/models/${m.id}`, { method: "DELETE" });
    toast(`Removed ${m.name} and its files`, "run");
  });
  // The owner's order (0.17.0): the chat's model choices, people's chats and the phone page list them so.
  const move = (group: "llm" | "comfy", items: ModelEntry[], i: number, by: -1 | 1) => act(`move-${group}`, async () => {
    const ids = items.map((m) => m.id);
    [ids[i], ids[i + by]] = [ids[i + by], ids[i]];
    await api("/hub/api/models/order", { method: "PUT", body: { group, ids } });
  });
  const [addingHF, setAddingHF] = useState(false);
  const [addingImage, setAddingImage] = useState(false);
  const [imageSettings, setImageSettings] = useState<ModelEntry | null>(null);
  const [renaming, setRenaming] = useState<ModelEntry | null>(null);
  const renameButton = (m: ModelEntry) => <button className="btn quiet small" onClick={() => setRenaming(m)}>Rename</button>;
  if (!models) return <div className="faint">Loading</div>;
  const installedLlms = models.llm.filter((m) => m.installed);

  return (
    <div>
      <div className="callout row wrap" style={{ marginBottom: 20 }}>
        <Lamp state={plateState(queue).lamp} pulse={plateState(queue).lamp === "run"} />
        <span className="grow">On the card now: <b>{residencyName(queue?.current, modelNames)}</b></span>
        <button className="btn small" disabled={busy === "unload" || !queue?.current} onClick={unload}>Free the GPU</button>
      </div>
      <SectionForm s={s} section="models" title="Defaults" fields={[
        { key: "default", label: "Model for chats", type: "select",
          options: installedLlms.length ? installedLlms.map((m) => ({ value: m.id, label: m.name })) : [{ value: String(s.data?.settings.models?.default ?? ""), label: "No model downloaded" }] },
        { key: "fallback_alias", label: "Unknown model names from apps go to", type: "select",
          options: [{ value: "", label: "Refuse them" }, ...models.llm.map((m) => ({ value: m.id, label: m.name }))],
          help: "An app asking for llama3 or gpt-4o gets this model instead of an error." },
        { key: "preload", label: "Load the chat model when the Hub starts", type: "bool" },
      ]} />
      <ModelGroup title="Language models" items={models.llm} busy={busy} onDownload={download} onCancel={cancel}
        onMove={(i, by) => move("llm", models.llm, i, by)}
        note="In this order in the chat's model choices. Move them with the arrows."
        action={<button className="btn small" onClick={() => setAddingHF(true)}><Plus size={14} /> Add from Hugging Face</button>}
        extra={(m) => <>
          {m.installed && (m.loaded ? <span className="tag"><Lamp state="run" size="sm" /> Loaded</span>
            : <button className="btn small" disabled={!!busy} onClick={() => load(m.id)}>{busy === `load-${m.id}` ? "Loading" : "Load"}</button>)}
          {renameButton(m)}
          {m.installed && <button className="btn quiet small" onClick={() => setAdvanced(m)}>Settings</button>}
          {m.custom && <ConfirmButton className="btn quiet small" confirmText="Remove it and its files?"
            disabled={!!busy || m.loaded} onConfirm={() => remove(m)}>Remove</ConfirmButton>}
        </>} />
      <ModelGroup title="Helpers for libraries" items={models.aux} busy={busy} onDownload={download} onCancel={cancel}
        note="Run on the CPU (the image embedder can use the GPU, see Assistant, Libraries)." />
      <ModelGroup title="Image models (ComfyUI)" items={models.comfy} busy={busy} onDownload={download} onCancel={cancel}
        onMove={(i, by) => move("comfy", models.comfy, i, by)}
        action={models.sdxl && <button className="btn small" onClick={() => setAddingImage(true)}><Plus size={14} /> Add image model</button>}
        extra={(m) => <>
          {renameButton(m)}
          {m.custom && m.sdxl && <button className="btn quiet small" onClick={() => setImageSettings(m)}>Settings</button>}
          {m.custom && <ConfirmButton className="btn quiet small" confirmText="Remove it and its file?" disabled={!!busy}
            onConfirm={() => remove(m)}>Remove</ConfirmButton>}
        </>} />
      {!!models.audio?.length && <ModelGroup title="Sound, music and speech" items={models.audio} busy={busy}
        onDownload={download} onCancel={cancel}
        note="Speech runs on the CPU, so the chat model stays loaded; music, sound effects, stems and other voices run on the card through ComfyUI. Once one is downloaded, ask for it in any chat - with a sound file attached for a remake, stems or another voice."
        extra={(m) => <span className="tag">{m.engine === "speech" ? "CPU" : "GPU"}</span>} />}
      {!!models.video?.length && <ModelGroup title="Video models" items={models.video} busy={busy}
        onDownload={download} onCancel={cancel}
        note="Run on the card through ComfyUI and take minutes per clip. Once one is downloaded, ask for a video in any chat - from a description, or with a picture attached to start from. Choose which one is used in Assistant, Video."
        extra={() => <span className="tag">GPU</span>} />}
      {advanced && <ModelSettings model={advanced} thinkingInChats={!!s.data?.settings.agent?.thinking_in_chats}
        onClose={() => setAdvanced(null)} onSaved={() => { setAdvanced(null); refreshModels(); }} />}
      {addingHF && <AddFromHF onClose={() => setAddingHF(false)} onAdded={() => { setAddingHF(false); refreshModels(); }} />}
      {addingImage && models.sdxl && <AddImageModel choices={models.sdxl} onClose={() => setAddingImage(false)}
        onAdded={() => { setAddingImage(false); refreshModels(); }} />}
      {imageSettings && models.sdxl && <ImageModelSettings model={imageSettings} choices={models.sdxl}
        onClose={() => setImageSettings(null)} onSaved={() => { setImageSettings(null); refreshModels(); }} />}
      {renaming && <RenameModel model={renaming} onClose={() => setRenaming(null)}
        onSaved={() => { setRenaming(null); refreshModels(); }} />}
    </div>
  );
}

function ModelGroup({ title, items, busy, onDownload, onCancel, extra, note, action, onMove }: { title: string;
  items: ModelEntry[]; busy: string | null; onDownload: (id: string) => void; onCancel: (id: string) => void;
  extra?: (m: ModelEntry) => ReactNode; note?: string; action?: ReactNode; onMove?: (index: number, by: -1 | 1) => void }) {
  return (
    <section style={{ marginBottom: 26 }}>
      <div className="row" style={{ marginBottom: 8 }}><h3 className="grow" style={{ margin: 0 }}>{title}</h3>{action}</div>
      {note && <p className="muted small" style={{ marginTop: -4 }}>{note}</p>}
      <div className="list">
        {items.map((m, i) => {
          const size = m.files.reduce((a, f) => a + (f.size ?? 0), 0);
          const active = m.downloads.filter((d) => d.status === "downloading" || d.status === "queued");
          const failed = m.downloads.find((d) => d.status === "failed");
          const done = active.reduce((a, d) => a + d.done, 0);
          const total = active.reduce((a, d) => a + (d.total || 0), 0);
          const rate = active.reduce((a, d) => a + (d.rate || 0), 0);
          return (
            <div className="list-row" key={m.id} style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
              <Lamp state={m.installed ? (m.loaded ? "run" : "off") : active.length ? "wait" : "off"} pulse={!!active.length} />
              <div className="grow" style={{ minWidth: 220 }}>
                <b>{m.name}</b>{m.name_card && <span className="muted small"> ({m.name_card})</span>} {m.custom && m.source
                  ? <><a className="tag" href={m.source.url} target="_blank" rel="noreferrer">
                      {m.source.site === "civitai" ? "Civitai" : "Hugging Face"}</a>
                    {m.source.nsfw && <span className="tag" title="Civitai marks this model NSFW">NSFW</span>}</>
                  : m.default ? null : <span className="tag">optional</span>}
                <div className="meta">{m.description}{size ? ` ${bytes(size)}.` : ""}</div>
                {active.length > 0 && <div style={{ marginTop: 6 }}>
                  <div className="bar wait"><span style={{ width: `${total ? (done / total) * 100 : 2}%` }} /></div>
                  <div className="meta num">{bytes(done)} of {bytes(total)}{rate ? `, ${bytes(rate)}/s` : ""}</div></div>}
                {failed && !active.length && <div className="meta" style={{ color: "var(--fault)" }}>Download failed: {failed.error}</div>}
              </div>
              <div className="row">
                {onMove && items.length > 1 && <span className="row model-order">
                  <button className="icon-btn small" disabled={!!busy || i === 0} onClick={() => onMove(i, -1)}
                    aria-label={`Move ${m.name} up`} title="Move up"><ChevronUp size={16} /></button>
                  <button className="icon-btn small" disabled={!!busy || i === items.length - 1} onClick={() => onMove(i, 1)}
                    aria-label={`Move ${m.name} down`} title="Move down"><ChevronDown size={16} /></button>
                </span>}
                {extra?.(m)}
                {!m.installed && (active.length
                  ? <button className="btn small" onClick={() => onCancel(m.id)}>Stop download</button>
                  : <button className="btn small" disabled={busy === `dl-${m.id}`} onClick={() => onDownload(m.id)}><Download size={14} /> Download</button>)}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

const SAMPLING_FIELDS: { key: keyof Sampling; label: string; step: number; min: number; max: number }[] = [
  { key: "temperature", label: "Temperature", step: 0.05, min: 0, max: 2 },
  { key: "top_p", label: "Top P", step: 0.05, min: 0, max: 1 },
  { key: "top_k", label: "Top K", step: 1, min: 0, max: 500 },
  { key: "min_p", label: "Min P", step: 0.01, min: 0, max: 1 },
  { key: "presence_penalty", label: "Presence penalty", step: 0.1, min: -2, max: 2 },
];
type SamplingForm = Record<"instruct" | "thinking", Record<string, string>>;

function samplingForm(m: ModelEntry): SamplingForm {
  const one = (mode: "instruct" | "thinking") => Object.fromEntries(SAMPLING_FIELDS.map((f) => {
    const v = m.sampling?.[mode]?.[f.key];
    return [f.key, v == null ? "" : String(v)];
  }));
  return { instruct: one("instruct"), thinking: one("thinking") };
}

/** One model's settings: how it answers (thinking, sampling - sent with every request) and how llama-server runs it. */
function ModelSettings({ model, thinkingInChats, onClose, onSaved }: { model: ModelEntry; thinkingInChats: boolean;
  onClose: () => void; onSaved: () => void }) {
  const { toast } = useApp();
  const server = (model.server ?? {}) as Record<string, unknown>;
  const canThink = (model.capabilities ?? []).includes("thinking");
  const [thinkingMode, setThinkingMode] = useState<string>(model.thinking_mode ?? "follow");
  const firstSampling = useMemo(() => samplingForm(model), [model]);
  const [sampling, setSampling] = useState<SamplingForm>(firstSampling);
  const [ctx, setCtx] = useState(String(server.ctx ?? ""));
  const [parallel, setParallel] = useState(String(server.parallel ?? ""));
  const [cache, setCache] = useState(String(server.cache_type ?? "q8_0"));
  const firstExtra = ((server.extra as string[] | undefined) ?? []).join(" ");
  const [extra, setExtra] = useState(firstExtra);
  const serverChanged = ctx !== String(server.ctx ?? "") || parallel !== String(server.parallel ?? "")
    || cache !== String(server.cache_type ?? "q8_0") || extra !== firstExtra;
  const samplingChanged = JSON.stringify(sampling) !== JSON.stringify(firstSampling);
  const setValue = (mode: "instruct" | "thinking", key: string, v: string) =>
    setSampling((s) => ({ ...s, [mode]: { ...s[mode], [key]: v } }));

  const put = async (body: Record<string, unknown>, done: string) => {
    try {
      const r = await api<{ reloadNeeded: boolean }>(`/hub/api/models/${model.id}`, { method: "PUT", body });
      toast(r.reloadNeeded ? "Saved. The model loads with the new server settings the next time it is loaded." : done, "run");
      onSaved();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const save = () => {
    const body: Record<string, unknown> = {};
    if (canThink && thinkingMode !== (model.thinking_mode ?? "follow")) body.thinking_mode = thinkingMode;
    if (samplingChanged) {
      const clean = (vals: Record<string, string>) => Object.fromEntries(Object.entries(vals).filter(([, v]) => v.trim() !== "")
        .map(([k, v]) => [k, Number(v)]));
      body.sampling = canThink ? { instruct: clean(sampling.instruct), thinking: clean(sampling.thinking) }
        : { instruct: clean(sampling.instruct) };
    }
    if (serverChanged) body.server = { ctx: Number(ctx), parallel: Number(parallel), cache_type: cache,
      extra: extra.trim() ? extra.trim().split(/\s+/) : [] };
    if (!Object.keys(body).length) {
      onClose();
      return;
    }
    put(body, "Saved");
  };
  const source = model.sampling_own ? "set here"
    : model.custom ? (model.sampling ? `the ${model.architecture ?? "model"} family's recommendation` : "none: llama-server decides")
    : "the model card";
  const modes: { mode: "instruct" | "thinking"; title: string }[] = canThink
    ? [{ mode: "instruct", title: "Without thinking" }, { mode: "thinking", title: "With thinking" }]
    : [{ mode: "instruct", title: "Sampling" }];

  return (
    <Modal wide title={`${model.name}: settings`} onClose={onClose} footer={<>
      {model.sampling_own && <button className="btn quiet" style={{ marginRight: "auto" }}
        onClick={() => put({ reset_sampling: true }, "Back to the model's own sampling")}>Use the model's own sampling</button>}
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" onClick={save}>Save</button></>}>
      <h3 style={{ margin: 0 }}>Answers</h3>
      {canThink && (
        <label className="field"><span>Thinking in chats</span>
          <select className="select" value={thinkingMode} onChange={(e) => setThinkingMode(e.target.value)} style={{ maxWidth: "28rem" }}>
            <option value="follow">Like the assistant setting (now: {thinkingInChats ? "think" : "answer straight away"})</option>
            <option value="on">Always think first</option>
            <option value="off">Answer straight away</option>
          </select>
          <small>For chats with no Think choice of their own. Reasoning models (they start with a think block) usually answer
            better thinking.</small></label>
      )}
      {modes.map(({ mode, title }) => (
        <fieldset key={mode} className="sampling-set">
          <legend>{title}</legend>
          <div className="sampling-grid">
            {SAMPLING_FIELDS.map((f) => (
              <label key={f.key} className="field"><span>{f.label}</span>
                <input className="input" type="number" inputMode="decimal" step={f.step} min={f.min} max={f.max}
                  value={sampling[mode][f.key]} placeholder="default"
                  onChange={(e) => setValue(mode, f.key, e.target.value)} aria-label={`${title}: ${f.label}`} /></label>
            ))}
          </div>
        </fieldset>
      ))}
      <p className="muted small" style={{ margin: 0 }}>Sampling now: {source}. Empty: llama-server's default. A flag such as
        <code className="mono">--temp</code> under Extra llama-server flags wins over these.</p>
      <h3 style={{ margin: "6px 0 0" }}>Server</h3>
      <p className="muted" style={{ margin: 0 }}>How llama-server runs this model. The context is shared by the slots
        (unified KV cache); more context or slots need more VRAM.</p>
      <div className="form-grid">
        <label className="field"><span>Context (tokens, all slots)</span><input className="input" type="number" value={ctx} onChange={(e) => setCtx(e.target.value)} /></label>
        <label className="field"><span>Slots (parallel requests)</span><input className="input" type="number" min={1} max={8} value={parallel} onChange={(e) => setParallel(e.target.value)} /></label>
        <label className="field"><span>KV cache type</span><select className="select" value={cache} onChange={(e) => setCache(e.target.value)}>
          {["f16", "q8_0", "q4_0"].map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
      </div>
      <label className="field"><span>Extra llama-server flags</span><input className="input mono" value={extra} onChange={(e) => setExtra(e.target.value)}
        placeholder="--temp 0.6" /><small>For experiments. The Hub sets <code className="mono">--host</code> and{" "}
        <code className="mono">--port</code>; <code className="mono">--api-key</code> is not allowed (the model server is
        reachable only on the Hub's internal network).</small></label>
    </Modal>
  );
}

/** The name the Hub shows for a chat or picture model: in chats, the model choices, Settings and on the phone page.
 * Its id - what apps, keys and settings use - stays. */
function RenameModel({ model, onClose, onSaved }: { model: ModelEntry; onClose: () => void; onSaved: () => void }) {
  const { toast } = useApp();
  const own = model.name_card ?? model.name;
  const [name, setName] = useState(model.name);
  const save = async (value: string) => {
    try {
      await api(`/hub/api/models/${encodeURIComponent(model.id)}/name`, { method: "PUT", body: { name: value } });
      toast(value.trim() && value.trim() !== own ? `Now called ${value.trim()}` : `Called ${own} again`, "run");
      onSaved();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <Modal title={`Rename ${model.name}`} onClose={onClose} footer={<>
      {model.name_card && <button className="btn quiet" style={{ marginRight: "auto" }} onClick={() => save("")}>
        Use {model.name_card}</button>}
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!name.trim()} onClick={() => save(name)}>Save</button></>}>
      <label className="field"><span>Name</span>
        <input className="input" value={name} maxLength={60} autoFocus placeholder={own}
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) save(name); }} />
        <small>Shown in chats, the model choices, Settings and on the phone page.{model.custom ? "" : ` Its own name is ${own};`}
          {" "}apps keep using its id, <code className="mono">{model.id}</code>.</small></label>
    </Modal>
  );
}

// ------------------------------------------------------------ add from Hugging Face

const GiB = 1024 ** 3;
const CONTEXTS = [8192, 16384, 32768, 65536, 131072, 262144];

/** Whether the weights leave room on the card for the context and compute buffers. */
function fitOf(size: number, vram: number): { lamp: LampState; text: string } {
  if (!vram) return { lamp: "off", text: "" };
  const left = vram - size;
  if (left > 6 * GiB) return { lamp: "run", text: "Fits, with room for a long context" };
  if (left > 2.5 * GiB) return { lamp: "wait", text: "Fits with a shorter context" };
  return { lamp: "fault", text: "Too big for the card" };
}

/** owner/name or a huggingface.co / hf.co link - anything else is searched for. */
function looksLikeRepo(text: string): boolean {
  const t = text.trim().replace(/^(https?:\/\/)?(www\.)?(huggingface\.co|hf\.co)\//i, "").split(/[?#]/)[0];
  return /^[A-Za-z0-9][\w.-]*\/[A-Za-z0-9][\w.-]*(\/.*)?$/.test(t) && !/\s/.test(text.trim());
}

// A model in the Hub's own list that matches the search: downloaded from its section, not from here.
interface InHub { id: string; name: string; group: string; section: string; installed: boolean }

function AddFromHF({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const { toast } = useApp();
  const [repo, setRepo] = useState("");
  const [looking, setLooking] = useState(false);
  const [error, setError] = useState("");
  const [found, setFound] = useState<HFLookup | null>(null);
  const [hits, setHits] = useState<HFSearchHit[] | null>(null);
  const [inHub, setInHub] = useState<InHub[]>([]);
  const [file, setFile] = useState("");
  const [mmproj, setMmproj] = useState("");
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const [ctx, setCtx] = useState(32768);
  const [tools, setTools] = useState(true);
  const [thinking, setThinking] = useState(false);
  const [adding, setAdding] = useState(false);

  const search = async () => {
    setLooking(true);
    setError("");
    setFound(null);
    try {
      const r = await api<{ results: HFSearchHit[]; in_hub?: InHub[] }>("/hub/api/models/hf/search", { query: { q: repo.trim() } });
      setHits(r.results);
      setInHub(r.in_hub ?? []);
      if (!r.results.length && !(r.in_hub ?? []).length) setError(`No GGUF model on Hugging Face matches "${repo.trim()}". Try fewer words.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLooking(false);
    }
  };

  const submit = () => {
    if (!repo.trim()) return;
    if (looksLikeRepo(repo)) lookUp(repo);
    else search();
  };

  const lookUp = async (which: string) => {
    setLooking(true);
    setError("");
    setFound(null);
    setInHub([]);
    try {
      const r = await api<HFLookup>("/hub/api/models/hf", { query: { repo: which.trim() } });
      setHits(null);
      // Q8_0 when it fits with room (nearly the answers of 16-bit at half the size and faster), else Q4_K_M.
      const roomy = r.choices.filter((c) => fitOf(c.size, r.vram).lamp === "run");
      const fitting = r.choices.filter((c) => fitOf(c.size, r.vram).lamp !== "fault");
      const pick = roomy.find((c) => /^Q8_0$/i.test(c.label)) ?? fitting.find((c) => /(^|-)Q4_K_M\b/i.test(c.label))
        ?? fitting[fitting.length - 1] ?? r.choices[0];
      setFound(r);
      setFile(pick?.key ?? "");
      setMmproj((r.mmproj.find((m) => /^F16$/i.test(m.label)) ?? r.mmproj[0])?.path ?? "");
      setName(r.suggested.name);
      setId(r.suggested.id);
      const limit = r.meta.context_length ?? 262144;
      setCtx(Math.min(32768, CONTEXTS.filter((c) => c <= limit).pop() ?? 8192));
      setTools(r.meta.tools ?? true);
      setThinking(r.meta.thinking ?? false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLooking(false);
    }
  };

  const choice = found?.choices.find((c) => c.key === file);
  const mm = found?.mmproj.find((m) => m.path === mmproj);
  const total = (choice?.size ?? 0) + (mm?.size ?? 0);
  const idOk = /^[a-z0-9][a-z0-9._-]{0,62}$/.test(id);
  const add = async () => {
    if (!found || !choice) return;
    setAdding(true);
    try {
      await api("/hub/api/models/hf", { method: "POST", body: { repo: found.repo, file: choice.key, mmproj: mm?.path ?? null,
        id, name, ctx, tools, thinking } });
      toast(`Downloading ${name || id}. It appears under Language models when it is ready.`, "run");
      onAdded();
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setAdding(false);
    }
  };

  const meta = found?.meta ?? {};
  const facts = found ? [meta.architecture, meta.parameters ? `${(meta.parameters / 1e9).toFixed(1)}B parameters` : null,
    meta.context_length ? `${Math.round(meta.context_length / 1024)}K context` : null].filter(Boolean) : [];
  return (
    <Modal wide title="Add a model from Hugging Face" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!choice || !idOk || adding} onClick={add}>
        <Download size={14} /> {adding ? "Adding" : `Add and download${total ? ` (${bytes(total)})` : ""}`}</button></>}>
      <form className="row" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <input className="input grow" autoFocus value={repo} onChange={(e) => setRepo(e.target.value)}
          placeholder="Search (Ornith 1.5 9B), or owner/name, or a link" aria-label="Hugging Face model" />
        <button className="btn" type="submit" disabled={looking || !repo.trim()}>
          {looking ? "Looking" : !repo.trim() || looksLikeRepo(repo) ? "Look up" : "Search"}</button>
      </form>
      {!found && !hits && !error && <p className="muted small" style={{ margin: 0 }}>Search for a model by name, or give a
        GGUF repository (the Hub runs models with llama.cpp); unsloth, bartowski and ggml-org publish most models that
        way. One language model is on the card at a time; the Hub switches when a chat or an app asks for another.</p>}
      {error && <div className="callout" style={{ color: "var(--fault)" }}>{error}</div>}
      {inHub.length > 0 && !found && (
        <div className="callout" aria-label="Already in the Hub">
          {inHub.map((m) => <div key={m.id}><b>{m.name}</b> is in the Hub's own list under <b>{m.section}</b>
            {m.installed ? " - it is downloaded already." : " - download it there, not here."}</div>)}
          {inHub.every((m) => m.group !== "llm") && <div className="muted small">This dialog adds chat models
            (GGUF files) only.</div>}
        </div>
      )}
      {hits && hits.length > 0 && !found && (
        <div className="list" aria-label="Models found" style={{ maxHeight: 320, overflow: "auto" }}>
          {hits.map((h) => (
            <button key={h.repo} type="button" className="list-row hf-hit" disabled={looking}
              onClick={() => { setRepo(h.repo); lookUp(h.repo); }}>
              <span className="grow" style={{ minWidth: 0 }}><b className="ellipsis" style={{ display: "block" }}>{h.repo}</b>
                <span className="meta num">{h.downloads.toLocaleString("en")} downloads · {h.likes} likes
                  {h.updated ? ` · ${h.updated.slice(0, 10)}` : ""}{h.gated ? " · needs a token (gated)" : ""}</span></span>
            </button>
          ))}
        </div>
      )}
      {found && (<>
        <div className="muted small"><a href={found.url} target="_blank" rel="noreferrer">{found.repo}</a>
          {facts.length > 0 && <> · {facts.join(" · ")}</>}</div>
        <div className="list" role="radiogroup" aria-label="Size" style={{ maxHeight: 260, overflow: "auto" }}>
          {found.choices.map((c) => {
            const fit = fitOf(c.size, found.vram);
            return (
              <label key={c.key} className="list-row choice-row">
                <input type="radio" name="hf-file" checked={file === c.key} onChange={() => setFile(c.key)} />
                <span className="choice-main"><b>{c.label}</b>{c.split && <span className="meta"> · in {c.files.length} parts</span>}
                  {/^(BF16|F16|F32)$/i.test(c.label) && <span className="meta"> · twice the size of Q8_0 and slower, for nearly the same answers</span>}</span>
                <span className="meta num">{bytes(c.size)}</span>
                <span className="row choice-state" style={{ minWidth: 210 }}><Lamp state={fit.lamp} size="sm" /><span className="meta">{fit.text}</span></span>
              </label>
            );
          })}
        </div>
        <div className="form-grid">
          <label className="field"><span>Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label className="field"><span>Name for apps</span><input className="input mono" value={id} onChange={(e) => setId(e.target.value.toLowerCase())} />
            <small style={idOk ? undefined : { color: "var(--fault)" }}>{idOk ? "Apps ask for the model by this name." : "a-z, 0-9, dots, dashes; starts with a letter or digit."}</small></label>
          <label className="field"><span>Context (tokens)</span><select className="select" value={ctx} onChange={(e) => setCtx(Number(e.target.value))}>
            {CONTEXTS.filter((c) => c <= (meta.context_length ?? 262144)).map((c) => <option key={c} value={c}>{c / 1024}K</option>)}</select>
            <small>More context needs more VRAM. Changeable later under Server settings.</small></label>
          {found.mmproj.length > 0 && <label className="field"><span>Pictures (vision)</span>
            <select className="select" value={mmproj} onChange={(e) => setMmproj(e.target.value)}>
              <option value="">No vision</option>
              {found.mmproj.map((m) => <option key={m.path} value={m.path}>{m.label} ({bytes(m.size)})</option>)}</select></label>}
        </div>
        <SwitchRow checked={tools} onChange={setTools} title="Tool calls">
          {meta.tools == null ? "Unknown for this model." : meta.tools ? "Its chat template supports tools." : "Its chat template mentions no tools; the assistant may not manage files, web or code with it."}</SwitchRow>
        <SwitchRow checked={thinking} onChange={setThinking} title="Thinking">
          {meta.thinking ? "Its chat template can switch thinking on and off." : "Shows the Think button for this model. Only for models that support it."}</SwitchRow>
        <div className="muted small">Download {bytes(total)} · {bytes(found.freeDisk)} free on the disk</div>
      </>)}
    </Modal>
  );
}

// ------------------------------------------------------------ picture models from a link

function presetSettings(choices: SdxlChoices, key: string): SdxlSettings {
  const { label: _label, ...rest } = choices.presets[key] ?? choices.presets.sdxl;
  return { ...rest, preset: choices.presets[key] ? key : "sdxl" };
}

/** Kind (preset), sampler settings, quality tags and negative prompt of an SDXL model. */
function SdxlFields({ value, onChange, choices }: { value: SdxlSettings; onChange: (v: SdxlSettings) => void;
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

function AddImageModel({ choices, onClose, onAdded }: { choices: SdxlChoices; onClose: () => void; onAdded: () => void }) {
  const { toast } = useApp();
  const [link, setLink] = useState("");
  const [looking, setLooking] = useState(false);
  const [error, setError] = useState("");
  const [found, setFound] = useState<ImageLookup | null>(null);
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const [named, setNamed] = useState(false);              // name or id typed by hand: keep them
  const [settings, setSettings] = useState<SdxlSettings | null>(null);
  const [tuned, setTuned] = useState(false);              // settings changed by hand: keep them
  const [adding, setAdding] = useState(false);
  const [checking, setChecking] = useState<string | null>(null);   // the file being checked, shown as chosen

  const lookUp = async (pick?: string) => {
    if (!link.trim()) return;
    setLooking(true);
    setChecking(pick ?? null);
    setError("");
    if (!pick) {
      setFound(null);
      setNamed(false);
      setTuned(false);
    }
    try {
      const r = await api<ImageLookup>("/hub/api/models/images/lookup", { query: { link: link.trim(), pick } });
      // Each lookup checks only the picked file: keep what earlier ones found out about the others.
      setFound((prev) => {
        if (!pick || !prev) return r;
        const known = new Map(prev.options.map((o) => [o.key, o]));
        return { ...r, options: r.options.map((o) => {
          const k = known.get(o.key);
          return o.supported === null && k && k.supported !== null ? { ...o, supported: k.supported, reason: k.reason, vpred: k.vpred } : o;
        }) };
      });
      if (!pick || !named) {
        setName(r.suggested.name);
        setId(r.suggested.id);
      }
      if (!pick || !tuned) setSettings(presetSettings(choices, r.suggested.preset));
    } catch (e) {
      if (pick) toast((e as Error).message, "fault");
      else setError((e as Error).message);
    } finally {
      setLooking(false);
      setChecking(null);
    }
  };

  const chosen = found?.options.find((o) => o.key === found.pick);
  const idOk = /^[a-z0-9][a-z0-9._-]{0,62}$/.test(id);
  const add = async () => {
    if (!found || !chosen || !settings) return;
    setAdding(true);
    try {
      await api("/hub/api/models/images", { method: "POST", body: { link: link.trim(), pick: chosen.key, id, name, settings } });
      toast(`Downloading ${name || id}. It appears under Image models when it is ready.`, "run");
      onAdded();
    } catch (e) {
      toast((e as Error).message, "fault");
    } finally {
      setAdding(false);
    }
  };

  return (
    <Modal wide title="Add an image model" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!chosen || chosen.supported !== true || !idOk || !settings || adding || looking}
        onClick={add}><Download size={14} /> {adding ? "Adding" : `Add and download${chosen?.size ? ` (${bytes(chosen.size)})` : ""}`}</button></>}>
      <form className="row" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); lookUp(); }}>
        <input className="input grow" autoFocus value={link} onChange={(e) => setLink(e.target.value)}
          placeholder="https://civitai.com/models/..., or a Hugging Face link" aria-label="Link to the model" />
        <button className="btn" type="submit" disabled={looking || !link.trim()}>{looking && !found ? "Looking" : "Look up"}</button>
      </form>
      {!found && !error && <p className="muted small" style={{ margin: 0 }}>An SDXL checkpoint: SDXL itself, Pony,
        Illustrious, NoobAI and the many models built on them. Paste its page on Civitai, or a Hugging Face repository
        or file with one .safetensors checkpoint (about 7 GB). The Hub checks that it can run it before anything is
        downloaded.</p>}
      {error && <div className="callout" style={{ color: "var(--fault)" }}>{error}</div>}
      {found && settings && (<>
        <div className="muted small"><a href={found.url} target="_blank" rel="noreferrer">{found.title}</a>
          {found.creator && <> · by {found.creator}</>}
          {found.nsfw && <> <span className="tag" title="Civitai marks this model NSFW">NSFW</span></>}</div>
        <div className="list" role="radiogroup" aria-label="Version" style={{ maxHeight: 240, overflow: "auto" }}>
          {found.options.map((o) => (
            <label key={o.key} className="list-row choice-row">
              <input type="radio" name="image-option" checked={(checking ?? found.pick) === o.key} disabled={looking}
                onChange={() => lookUp(o.key)} />
              <span className="choice-main"><b className="ellipsis" style={{ display: "block" }}>{o.label}</b>
                {o.base && <span className="meta">{o.base}</span>}
                {o.supported === false && <div className="meta" style={{ color: "var(--fault)" }}>{o.reason}</div>}</span>
              <span className="meta num">{o.size ? bytes(o.size) : ""}</span>
              <span className="row choice-state">
                <Lamp state={o.supported ? "run" : o.supported === false ? "fault" : "off"} size="sm" />
                <span className="meta">{checking === o.key ? "Checking" : o.supported ? "The Hub can run it"
                  : o.supported === false ? "Not usable" : "Pick it to check"}</span></span>
            </label>
          ))}
        </div>
        {chosen?.vpred && <p className="muted small" style={{ margin: 0 }}>A v-prediction model: ComfyUI reads that from the file.</p>}
        <div className="form-grid">
          <label className="field"><span>Name</span><input className="input" value={name}
            onChange={(e) => { setName(e.target.value); setNamed(true); }} /></label>
          <label className="field"><span>Name for apps</span><input className="input mono" value={id}
            onChange={(e) => { setId(e.target.value.toLowerCase()); setNamed(true); }} />
            <small style={idOk ? undefined : { color: "var(--fault)" }}>{idOk ? "Chats and apps pick the model by this name."
              : "a-z, 0-9, dots, dashes; starts with a letter or digit."}</small></label>
        </div>
        <SdxlFields value={settings} choices={choices} onChange={(v) => { setSettings(v); setTuned(true); }} />
        {found.site === "civitai" && !found.tokens.civitai && <p className="muted small" style={{ margin: 0 }}>Many
          Civitai models download only for signed-in users. If this one does, the download says so: create an API key
          at civitai.com (Account settings, API keys), put <code>CIVITAI_TOKEN=...</code> in /srv/slq-hub/.env and
          restart the Hub (<code>update.sh compose up -d</code>).</p>}
        <div className="muted small">Download {chosen?.size ? bytes(chosen.size) : "-"} · {bytes(found.freeDisk)} free on the disk</div>
      </>)}
    </Modal>
  );
}

function ImageModelSettings({ model, choices, onClose, onSaved }: { model: ModelEntry; choices: SdxlChoices;
  onClose: () => void; onSaved: () => void }) {
  const { toast } = useApp();
  const [settings, setSettings] = useState<SdxlSettings>(model.sdxl ?? presetSettings(choices, "sdxl"));
  const save = async () => {
    try {
      await api(`/hub/api/models/images/${model.id}`, { method: "PUT", body: { settings } });
      toast("Saved", "run");
      onSaved();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <Modal title={`${model.name}: settings`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" onClick={save}>Save</button></>}>
      <p className="muted" style={{ margin: 0 }}>How the Hub makes pictures with this model. The next picture uses the
        new settings. Rename it from the list.</p>
      <SdxlFields value={settings} choices={choices} onChange={setSettings} />
    </Modal>
  );
}

// ------------------------------------------------------------ queue

function QueueTab({ s }: { s: S }) {
  const { queue } = useApp();
  const [open, setOpen] = useState(false);
  return (
    <div>
      <div className="callout row wrap" style={{ marginBottom: 20 }}>
        <Lamp state={plateState(queue).lamp} />
        <span className="grow">{queue ? `${queue.running.length} working, ${queue.pending.length} waiting.` : "Connecting."}</span>
        <button className="btn small" onClick={() => setOpen(true)}>Show the queue</button>
      </div>
      <SectionForm s={s} section="queue" title="How the queue decides" intro={<>
        Web chats go first, then apps, then background work (library indexing). Requests for the model that is loaded
        share its slots; a request for another model waits until the card can switch.</>} fields={[
          { key: "affinity_window_s", label: "Let same-model work go first for (seconds)", type: "number", min: 0, max: 600,
            help: "While a request for another model waits less than this, requests for the loaded model keep the card. Saves model switches." },
          { key: "aging_s", label: "Raise a waiting request's priority every (seconds)", type: "number", min: 0, max: 3600,
            help: "So background work never waits forever. 0 turns it off." },
          { key: "max_wait_s", label: "An app's request gives up after (seconds)", type: "number", min: 30, max: 7200 },
        ]} />
      {open && <QueueModal onClose={() => setOpen(false)} />}
    </div>
  );
}

// ------------------------------------------------------------ people

function PeopleTab() {
  const { toast } = useApp();
  const [people, setPeople] = useState<Person[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [password, setPassword] = useState<Person | null>(null);
  const load = useCallback(async () => {
    try {
      setPeople(await api<Person[]>("/hub/api/people"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load]);
  const patch = async (p: Person, body: Partial<Person> & { password?: string }, done?: string) => {
    try {
      await api(`/hub/api/people/${p.id}`, { method: "PATCH", body });
      if (done) toast(done, "run");
      load();
      return true;
    } catch (e) {
      toast((e as Error).message, "fault");
      return false;
    }
  };
  const signOut = async (p: Person) => {
    try {
      const r = await api<{ sessions: number }>(`/hub/api/people/${p.id}/sign-out`, { method: "POST" });
      toast(r.sessions ? `${p.display_name} is signed out on ${r.sessions} device${r.sessions === 1 ? "" : "s"}` :
        `${p.display_name} was not signed in anywhere`, "run");
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const remove = async (p: Person) => {
    try {
      await api(`/hub/api/people/${p.id}`, { method: "DELETE" });
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <div>
      <p className="lead">Others in the household can use the Hub with their own login: the same chat as yours, with
        models, pictures, thinking and tools. Their chats and files are kept on their own device, not on the Hub, and
        they see nothing of yours: no Settings, libraries, MCP servers, projects or memory.</p>
      <div className="row" style={{ marginBottom: 8 }}><h3 style={{ margin: 0 }} className="grow">People</h3>
        <button className="btn small" onClick={() => setAdding(true)}><Plus size={14} /> Add a person</button></div>
      <div className="list" style={{ marginBottom: 24 }}>
        {people === null ? <Empty>Loading</Empty> : people.length === 0 ? <Empty>Nobody yet.</Empty> : people.map((p) => (
          <div className="list-row" key={p.id} style={{ flexWrap: "wrap" }}>
            <span className="grow" style={{ minWidth: 180 }}><b>{p.display_name}</b>
              <div className="meta"><span className="mono">{p.username}</span>, {p.last_seen ? `seen ${ago(p.last_seen)}` : "not signed in yet"}</div></span>
            <select className="select" style={{ width: 170 }} value={p.away ? "away" : "home"} aria-label={`Where ${p.display_name} can sign in`}
              onChange={(e) => patch(p, { away: e.target.value === "away" }, e.target.value === "away"
                ? `${p.display_name} can use the Hub away from home too` : `${p.display_name} can use the Hub only at home`)}>
              <option value="home">At home</option><option value="away">At home and away</option>
            </select>
            <label className="row small" style={{ gap: 6 }} title="Lets the assistant run code (bash, Python) in their chats, kept inside the chat's temporary folder">
              <Switch checked={p.may_run_code} onChange={(v) => patch(p, { may_run_code: v })} label={`${p.display_name} may run code`} />
              May run code</label>
            <button className="btn quiet small" onClick={() => setPassword(p)}>New password</button>
            <button className="btn quiet small" onClick={() => signOut(p)}>Sign out everywhere</button>
            <ConfirmButton onConfirm={() => remove(p)} confirmText="Remove the login?">Remove</ConfirmButton>
          </div>
        ))}
      </div>
      <div className="callout"><b>How it works for them</b>: they sign in at {location.host} with the user name and password
        you give them, and add the page to their phone's home screen. <b>At home</b> means the LAN and WireGuard;
        <b> At home and away</b> also lets them in through hub.slq.dk. A new password signs them out everywhere. Their chats
        stay in the browser they use, so they move between devices with Export and Import in their account menu.
        <div className="small muted" style={{ marginTop: 6 }}>Code runs inside the answer's temporary folder and cannot see
          your files (Linux Landlock, checked by the self-test); it can see the command lines of your own commands running
          at the same moment. Without Landlock, nobody's chat gets code.</div></div>
      {adding && <AddPerson onClose={() => setAdding(false)} onAdded={() => { setAdding(false); load(); }} />}
      {password && <NewPassword person={password} onClose={() => setPassword(null)}
        onSave={async (pw) => { if (await patch(password, { password: pw }, `New password for ${password.display_name}; signed out everywhere`)) setPassword(null); }} />}
    </div>
  );
}

function AddPerson({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [away, setAway] = useState(false);
  const [code, setCode] = useState(false);
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    try {
      await api("/hub/api/people", { body: { display_name: name, username, password, away, may_run_code: code } });
      toast(`${name || username} can sign in now`, "run");
      onAdded();
    } catch (e) {
      toast((e as Error).message, "fault");
      setBusy(false);
    }
  };
  return (
    <Modal title="Add a person" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={busy || !username.trim() || password.length < 10} onClick={add}>Add</button></>}>
      <label className="field"><span>Name</span><input className="input" autoFocus value={name}
        onChange={(e) => { setName(e.target.value); if (!username || username === slugName(name)) setUsername(slugName(e.target.value)); }}
        placeholder="Maria" /><small>What the assistant calls them.</small></label>
      <label className="field"><span>User name</span><input className="input mono" value={username} autoCapitalize="off"
        onChange={(e) => setUsername(e.target.value)} placeholder="maria" /></label>
      <label className="field"><span>Password</span><input className="input" value={password} type="text" autoComplete="off"
        onChange={(e) => setPassword(e.target.value)} /><small>At least 10 characters. They can change it themselves.</small></label>
      <label className="field"><span>Where they can sign in</span>
        <select className="select" value={away ? "away" : "home"} onChange={(e) => setAway(e.target.value === "away")}>
          <option value="home">At home: the LAN and WireGuard</option>
          <option value="away">At home and away: also through hub.slq.dk</option></select></label>
      <SwitchRow checked={code} onChange={setCode} title="May run code">The assistant may run bash and Python in their chats,
        inside each answer's temporary folder.</SwitchRow>
    </Modal>
  );
}

function slugName(name: string): string {
  return name.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/æ/g, "ae").replace(/ø/g, "oe")
    .replace(/å/g, "aa").replace(/[^a-z0-9._-]+/g, "").slice(0, 40);
}

function NewPassword({ person, onClose, onSave }: { person: Person; onClose: () => void; onSave: (pw: string) => void }) {
  const [pw, setPw] = useState("");
  return (
    <Modal title={`New password for ${person.display_name}`} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={pw.length < 10} onClick={() => onSave(pw)}>Save</button></>}>
      <label className="field"><span>New password</span><input className="input" autoFocus value={pw} type="text"
        autoComplete="off" onChange={(e) => setPw(e.target.value)} />
        <small>At least 10 characters. They are signed out on every device and sign in again with this.</small></label>
    </Modal>
  );
}

// ------------------------------------------------------------ apps and keys

function AppsTab({ s }: { s: S }) {
  const { toast } = useApp();
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [made, setMade] = useState<NewKeyMade | null>(null);
  const [origins, setOrigins] = useState("");
  const [conn, setConn] = useState<Connection | null>(null);
  const load = useCallback(async () => {
    try {
      setKeys(await api<ApiKey[]>("/hub/api/keys"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
    api<Connection>("/hub/api/connection").then(setConn, () => undefined);
  }, [load]);
  const cors = s.data?.settings.api?.cors_origins as string[] | undefined;
  useEffect(() => setOrigins((cors ?? []).join("\n")), [cors]);
  const patch = async (k: ApiKey, body: Record<string, unknown>): Promise<boolean> => {
    try {
      await api(`/hub/api/keys/${k.id}`, { method: "PATCH", body });
      load();
      return true;
    } catch (e) {
      toast((e as Error).message, "fault");
      return false;
    }
  };
  // Away from home = also through the proxy, from anywhere. A limit to the home network contradicts that, so it goes.
  const setWhere = async (k: ApiKey, away: boolean) => {
    const body: Record<string, unknown> = { away };
    let note = away ? `${k.name} works away from home too now, through ${publicHost || "the proxy"}.`
      : `${k.name} works only at home now.`;
    if (away && k.allowed_ips.trim()) {
      if (conn?.homeNetwork && k.allowed_ips.trim() === conn.homeNetwork) {
        body.allowed_ips = "";
        note += " Its limit to the home network is removed.";
      } else {
        note += ` It is still limited to ${k.allowed_ips}.`;
      }
    }
    if (await patch(k, body)) toast(note, "run");
  };
  const del = async (k: ApiKey) => {
    try {
      await api(`/hub/api/keys/${k.id}`, { method: "DELETE" });
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const lanHost = conn?.lanHost || location.hostname;
  const uiPort = conn?.uiPort ?? 8080;
  const ollamaPort = conn?.ollamaPort ?? 11434;
  const example = made ? made.key : "slqhub_...";
  const phonePage = `http://${lanHost}:${uiPort}/chat`;
  const publicUrl = (conn?.publicUrl || "").replace(/\/+$/, "");
  const publicHost = hostOf(publicUrl);
  const awayPage = publicUrl ? `${publicUrl}/chat` : "";
  return (
    <div>
      <p className="lead">Other apps use the models through the Hub, in the same queue as everything else. Each app gets
        its own key. The model API answers only on the LAN and WireGuard, unless a key is set to work away from home
        too (through {publicHost || "the proxy"}).</p>
      <div className="row" style={{ marginBottom: 8 }}><h3 style={{ margin: 0 }} className="grow">Keys</h3>
        <button className="btn small" onClick={() => { setAdding(true); setMade(null); }}><Plus size={14} /> New key</button></div>
      <div className="list" style={{ marginBottom: 24 }}>
        {keys === null ? <Empty>Loading</Empty> : keys.length === 0 ? <Empty>No keys yet. Make one per app.</Empty> : keys.map((k) => (
          <div className="list-row" key={k.id} style={{ flexWrap: "wrap" }}>
            <Switch checked={!!k.enabled} onChange={(v) => patch(k, { enabled: v })} label={`${k.name} enabled`} />
            <span className="grow" style={{ minWidth: 180 }}><b>{k.name}</b>
              <div className="meta"><span className="mono">{k.prefix}...</span>, {k.last_used ? `used ${ago(k.last_used)}` : "never used"}
                {k.allowed_ips ? `, only from ${k.allowed_ips}` : ""}</div></span>
            <select className="select" style={{ width: 170 }} value={k.away ? "away" : "home"}
              onChange={(e) => setWhere(k, e.target.value === "away")} aria-label={`Where ${k.name} works`}>
              <option value="home">At home</option><option value="away">At home and away</option>
            </select>
            <select className="select" style={{ width: 150 }} value={k.priority} onChange={(e) => patch(k, { priority: e.target.value })}
              aria-label="Priority">
              <option value="interactive">Interactive</option><option value="normal">Normal</option><option value="background">Background</option>
            </select>
            <ConfirmButton onConfirm={() => del(k)} confirmText="Delete the key?">Delete</ConfirmButton>
          </div>
        ))}
      </div>

      <h3>Connecting an app</h3>
      <div className="stack" style={{ maxWidth: "52rem" }}>
        <div className="callout"><b>Phones and tablets</b>: the Hub's chat page, made for a phone. Open it on the home Wi-Fi
          and add it to the home screen; it asks for a key once, and the chats stay on the phone.
          <div className="code-line">{phonePage}</div>
          <div className="small muted" style={{ marginTop: 6 }}>Make a key per person: the key dialog gives a link with the key
            in it, ready to send to the phone.</div>
          {awayPage && <div className="small muted" style={{ marginTop: 6 }}>A key set to <b>At home and away</b> also works
            at <span className="mono">{awayPage}</span>, from anywhere. That address is HTTPS, so a phone can install the
            page as an app. A phone keeps its chats per address: use one of the two.</div>}</div>
        <div className="callout"><b>Ollama apps</b> (FamilyLog, Enchanted and others). Apps without a key field take the key in
          the address:
          <div className="code-line">http://{lanHost}:{ollamaPort}/k/{example}</div>
          <div className="small muted" style={{ marginTop: 6 }}>Apps with a key field (Enchanted calls it Bearer Token) use
            http://{lanHost}:{ollamaPort} and the key there.</div>
          <div className="small muted" style={{ marginTop: 6 }}>Use the Hub's model names (qwen3.8-27b, qwen3.6-35b-a3b) or any
            name: unknown names go to the fallback model. Settings like "free ComfyUI first" in the app are not needed: the Hub's queue does that.</div></div>
        <div className="callout"><b>OpenAI-compatible apps</b>
          <div className="code-line">base URL  http://{lanHost}:{uiPort}/v1{"\n"}API key   {example}</div></div>
        <div className="callout"><b>Anthropic-compatible apps</b>
          <div className="code-line">base URL  http://{lanHost}:{uiPort}{"\n"}x-api-key {example}</div></div>
      </div>

      <h3>Browser apps</h3>
      <p className="muted" style={{ marginTop: 0, maxWidth: "62ch" }}>A web page on another address (a browser app calling the Hub
        directly) needs its origin listed here, one per line, like https://app.example.dk.</p>
      <textarea className="textarea mono" rows={3} value={origins} onChange={(e) => setOrigins(e.target.value)} style={{ maxWidth: "36rem" }} />
      <div className="row" style={{ marginTop: 8, marginBottom: 20 }}>
        <button className="btn primary" onClick={() => s.save("api", { cors_origins: origins.split(/\s+/).map((x) => x.trim()).filter(Boolean) })}>Save origins</button>
      </div>
      <SectionForm s={s} section="api" fields={[
        { key: "allow_external", label: "Allow every key from outside the LAN", type: "bool",
          help: `Through the proxy (${publicHost || "hub.slq.dk"}), for all keys. To let one person in from outside, set ` +
            "that key to At home and away instead. Off keeps the other keys on the LAN and WireGuard." }]} />

      {adding && <NewKey onClose={() => setAdding(false)} made={made} onMade={(m) => { setMade(m); load(); }}
        phonePage={phonePage} awayPage={awayPage} publicHost={publicHost} homeNetwork={conn?.homeNetwork} />}
    </div>
  );
}

interface Connection { lanHost: string; uiPort: number; ollamaPort: number; publicUrl?: string; homeNetwork?: string }
interface NewKeyMade { name: string; key: string; away?: boolean }

function hostOf(url: string): string {
  try {
    return url ? new URL(url).host : "";
  } catch {
    return "";
  }
}

function NewKey({ onClose, made, onMade, phonePage, awayPage, publicHost, homeNetwork }: { onClose: () => void;
  made: NewKeyMade | null; onMade: (m: NewKeyMade) => void; phonePage: string; awayPage: string; publicHost: string;
  homeNetwork?: string }) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const [priority, setPriority] = useState("normal");
  const [ips, setIps] = useState("");
  const [away, setAway] = useState(false);
  const create = async () => {
    try {
      const k = await api<NewKeyMade>("/hub/api/keys", { body: { name, priority, allowed_ips: ips, away } });
      onMade(k);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const copy = async (text: string) => toast((await copyText(text)) ? "Copied" : "Copying failed", "run");
  const anywhere = !!made?.away && !!awayPage;
  return (
    <Modal title="New key" onClose={onClose} footer={made ? <button className="btn primary" onClick={onClose}>Done</button>
      : <><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!name.trim()} onClick={create}>Make the key</button></>}>
      {made ? (
        <>
          <p style={{ margin: 0 }}>The key for <b>{made.name}</b> is shown only now.</p>
          <div className="code-line">{made.key}</div>
          <div className="row wrap">
            <button className="btn small" onClick={() => copy(made.key)}><Copy size={14} /> Copy the key</button>
            <button className="btn small" onClick={() => copy(`${anywhere ? awayPage : phonePage}#key=${made.key}`)}>
              <Copy size={14} /> Copy the phone link</button>
            {anywhere && <button className="btn small" onClick={() => copy(`${phonePage}#key=${made.key}`)}>
              <Copy size={14} /> Copy the home-only link</button>}
          </div>
          <p className="small muted" style={{ margin: 0 }}>The phone link opens the chat page with this key in it. Send it to the
            phone (Messages, mail) and open it {anywhere ? `anywhere: it goes through ${publicHost}` : "on the home Wi-Fi"}. On
            an iPhone, add the page to the home screen and open it from there; if it asks for the key, paste the link.</p>
        </>
      ) : (
        <>
          <label className="field"><span>App</span><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="FamilyLog" /></label>
          <label className="field"><span>Priority in the queue</span><select className="select" value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="interactive">Interactive: like a web chat</option><option value="normal">Normal: most apps</option>
            <option value="background">Background: batch work</option></select></label>
          <label className="field"><span>Where it works</span><select className="select" value={away ? "away" : "home"}
            onChange={(e) => setAway(e.target.value === "away")}>
            <option value="home">At home: the LAN and WireGuard</option>
            <option value="away">At home and away: also through {publicHost || "the proxy"}</option></select>
            <small>{away ? `From anywhere on the internet, with this key. For a person's phone; keep app keys at home.`
              : "The usual choice for apps and for phones that stay at home."}</small></label>
          <label className="field"><span>Only from these addresses</span><input className="input" value={ips} onChange={(e) => setIps(e.target.value)}
            placeholder="30.11.0.20, 10.8.0.0/24" /><small>Empty: no limit beyond the choice above.</small></label>
          {!away && homeNetwork && ips.trim() !== homeNetwork && (
            <button type="button" className="btn small" style={{ alignSelf: "flex-start" }} onClick={() => setIps(homeNetwork)}>
              Only the home network ({homeNetwork})</button>)}
        </>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------ assistant

function AgentTab({ s }: { s: S }) {
  const { models } = useApp();
  const current = String(s.data?.settings.images?.model ?? "qwen-image-2.1");
  // A turbo checkpoint that needs its base model (Qwen) is used for fast drafts, not chosen here.
  const imageModels = (models?.comfy ?? []).filter((m) => m.installed && !(m.requires ?? []).length);
  const imageOptions = imageModels.length ? imageModels.map((m) => ({ value: m.id, label: m.name }))
    : [{ value: current, label: "No image model downloaded" }];
  const soundModels = (models?.audio ?? []).filter((m) => m.installed);
  const soundOptions = (ability: string, language?: "da" | "other") => {
    const fit = soundModels.filter((m) => (m.abilities ?? []).includes(ability)
      && (language !== "other" || (m.languages ?? []).some((l) => l !== "da")));
    return [{ value: "", label: fit.length ? `The first downloaded (${fit[0].name})` : "None downloaded" },
      ...fit.map((m) => ({ value: m.id, label: m.name }))];
  };
  const videoModels = (models?.video ?? []).filter((m) => m.installed);
  const videoOptions = (ability: string) => {
    const fit = videoModels.filter((m) => (m.abilities ?? []).includes(ability));
    return [{ value: "", label: fit.length ? `The first downloaded (${fit[0].name})` : "None downloaded" },
      ...fit.map((m) => ({ value: m.id, label: m.name }))];
  };
  return (
    <div>
      <SectionForm s={s} section="agent" title="The assistant" fields={[
        { key: "user_name", label: "Your name, as the assistant uses it", type: "text" },
        { key: "effort", label: "Thinking effort when thinking is on", type: "select",
          options: [{ value: "low", label: "A little" }, { value: "medium", label: "Normal" }, { value: "xhigh", label: "Hard" }] },
        { key: "max_steps", label: "Most steps in one turn", type: "number", min: 5, max: 300,
          help: "A step is one answer from the model; tool calls each take one." },
        { key: "compact_at", label: "Condense the chat at this share of the context", type: "number", min: 0.3, max: 0.95, step: 0.05 },
        { key: "tool_output_chars", label: "Longest tool output shown to the model (characters)", type: "number", min: 2000, max: 100000,
          help: "Longer output is saved to a file the model can read in parts." },
        { key: "thinking_in_chats", label: "Think before answering in plain chats", type: "bool" },
        { key: "thinking_in_projects", label: "Think before answering in projects", type: "bool" },
        { key: "escalate_to", label: "When the model gets stuck, hand over to", type: "select",
          options: [{ value: "", label: "Nobody: it answers with what it has" },
            ...(models?.llm ?? []).filter((m) => m.installed).map((m) => ({ value: m.id, label: m.name }))],
          help: "Stuck: it repeats itself, three tool calls in a row fail, or it asks for help. The turn goes on with this " +
            "model (the card switches, which takes a moment) and the chat says so. Not when this model is the one working." },
      ]} />
      <SectionForm s={s} section="libraries" title="Libraries" fields={[
        { key: "page_images", label: "Page images for visual search", type: "select", options: [
          { value: "cpu", label: "On the CPU (slow, never disturbs chats)" }, { value: "gpu", label: "On the GPU (fast, unloads the chat model while indexing)" },
          { value: "off", label: "Off (text only)" }] },
        { key: "results", label: "Hits per search", type: "number", min: 1, max: 20 },
        { key: "images_in_results", label: "Page images shown to the model per search", type: "number", min: 0, max: 6 },
        { key: "rerank", label: "Re-rank the hits", type: "bool", help: "Better order, a little slower." },
      ]} />
      <SectionForm s={s} section="web" title="Web" fields={[
        { key: "access", label: "Internet for the assistant", type: "select", options: [
          { value: "allow", label: "Allowed" }, { value: "ask", label: "Ask first" }],
          help: "Ask first: a web search, a page from a site and code that goes online wait for your answer in the chat - " +
            "allow once, always, or deny. Code runs without the network unless it asks, and pictures from the web in " +
            "answers show as links. People are asked in their own chats, once each time." },
        { key: "search_results", label: "Results per web search", type: "number", min: 1, max: 20 },
        { key: "fetch_max_chars", label: "Longest page text read (characters)", type: "number", min: 2000, max: 200000 },
      ]} />
      <InternetAllowed asking={s.data?.settings.web?.access === "ask"} />
      <SectionForm s={s} section="sandbox" title="Sandbox" intro="Where the assistant runs code: no access to your LAN, internet allowed." fields={[
        { key: "timeout_s", label: "Default time limit for a command (seconds)", type: "number", min: 5, max: 3600 },
        { key: "max_timeout_s", label: "Longest a command may run (seconds)", type: "number", min: 10, max: 7200 },
      ]} />
      {soundModels.length > 0 && <SectionForm s={s} section="sound" title="Sound" fields={[
        { key: "music_model", label: "Model for music", type: "select", options: soundOptions("music"),
          help: "Songs, instrumentals and remakes (music_generate, music_remake). The assistant can still use another downloaded model if you name it." },
        { key: "sound_model", label: "Model for sound effects", type: "select", options: soundOptions("sound") },
        { key: "speech_model_da", label: "Voice model for Danish", type: "select", options: soundOptions("speech", "da"),
          help: "Røst v3 Turbo is the faster; in this Hub's listening test it also sounded best. Chatterbox speaks Danish " +
            "with an English accent. Listen to each under Speech samples." },
        { key: "speech_model", label: "Voice model for other languages", type: "select", options: soundOptions("speech", "other"),
          help: "Used when it speaks the language; otherwise a model that does." },
      ]} />}
      {soundModels.some((m) => (m.abilities ?? []).includes("speech")) && <SpeechSamples />}
      <SectionForm s={s} section="images" title="Images" fields={[
        { key: "model", label: "Model for pictures", type: "select", options: imageOptions,
          help: "Ask for a picture in any chat. The assistant can still use the other model if you name it." },
        { key: "steps", label: "Steps for a normal image", type: "number", min: 4, max: 80,
          help: "Qwen-Image, ERNIE-Image and Chroma1-HD. Models added from a link have their own (Settings, Models)." },
        { key: "turbo_steps", label: "Steps for a fast image", type: "number", min: 2, max: 20,
          help: "The turbo models: Qwen-Image Turbo, ERNIE-Image Turbo, Z-Image Turbo." },
        { key: "default_size", label: "Default size (pixels, the long side)", type: "number", min: 512, max: 2048, step: 64 },
        { key: "enhance_prompts", label: "ERNIE-Image: let its prompt enhancer expand the prompt", type: "bool",
          help: "A 3B model in the image workflow turns the prompt into a detailed description first. The chat shows what it wrote." },
      ]} />
      {videoModels.length > 0 && <SectionForm s={s} section="video" title="Video" fields={[
        { key: "t2v_model", label: "Model for a video from a description", type: "select", options: videoOptions("t2v") },
        { key: "i2v_model", label: "Model for a video from a picture", type: "select", options: videoOptions("i2v"),
          help: "A video from a first to a last picture uses it when it can (Wan 2.2 14B picture to video), otherwise a " +
            "model that can. The assistant can still use another downloaded model if you name it." },
      ]} />}
    </div>
  );
}

/** What "always" answers allow while the assistant asks before it goes online: web search, sites, code. */
function InternetAllowed({ asking }: { asking: boolean }) {
  const { toast } = useApp();
  const [perms, setPerms] = useState<{ tool: string; created_at: string }[] | null>(null);
  const load = useCallback(async () => {
    try {
      const all = await api<{ tool: string; created_at: string }[]>("/hub/api/tool-permissions");
      setPerms(all.filter((p) => p.tool.startsWith("internet:")));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load, asking]);
  if (!perms || (!asking && !perms.length)) return null;
  const label = (key: string) => key === "internet:search" ? "Web search" : key === "internet:code" ? "Internet for code"
    : key.replace(/^internet:site:/, "");
  const forget = async (tool: string) => {
    try {
      await api(`/hub/api/tool-permissions/${encodeURIComponent(tool)}`, { method: "DELETE" });
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <section className="stack" style={{ marginTop: -12, marginBottom: 28 }}>
      <h3 style={{ margin: 0 }}>Always allowed online</h3>
      <div className="list">
        {perms.length === 0 ? <Empty>Nothing yet. When the assistant asks and you choose Always, it appears here.</Empty>
          : perms.map((p) => (
            <div className="list-row" key={p.tool}><span className="grow">{label(p.tool)}
              {p.tool.startsWith("internet:site:") && <span className="muted small"> (site)</span>}</span>
              <span className="meta">{ago(p.created_at)}</span>
              <button className="btn quiet small" onClick={() => forget(p.tool)}>Ask again</button></div>
          ))}
      </div>
      {!asking && <p className="muted small" style={{ margin: 0 }}>Internet is allowed now, so these are not used.</p>}
    </section>
  );
}

// ------------------------------------------------------------ skills

function SkillsTab() {
  const { toast } = useApp();
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [edit, setEdit] = useState<{ name: string; text: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const load = useCallback(async () => {
    try {
      setSkills(await api<Skill[]>("/hub/api/skills"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load]);
  const install = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f, f.name);
    try {
      const r = await api<{ name: string }>("/hub/api/skills", { form: fd });
      toast(`Installed ${r.name}`, "run");
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const open = async (name: string) => {
    try {
      const r = await api<{ text: string }>(`/hub/api/skills/${name}`);
      setEdit({ name, text: r.text });
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const save = async () => {
    if (!edit) return;
    try {
      await api(`/hub/api/skills/${edit.name}`, { method: "PUT", body: { text: edit.text } });
      setEdit(null);
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <div>
      <p className="lead">A skill is a folder with a SKILL.md: instructions (and files) for one kind of task. The assistant
        sees each skill's name and description and loads the rest when a task matches. The same format as Claude's skills.</p>
      <div className="row" style={{ marginBottom: 10 }}>
        <button className="btn primary" onClick={() => input.current?.click()}><UploadIcon size={15} /> Install a skill</button>
        <span className="muted small">A .zip or .skill bundle, or a single SKILL.md.</span>
        <input ref={input} type="file" hidden accept=".zip,.skill,.md" onChange={(e) => { const f = e.target.files?.[0]; if (f) install(f); e.target.value = ""; }} />
      </div>
      <div className="list">
        {skills === null ? <Empty>Loading</Empty> : skills.length === 0 ? <Empty>No skills installed.</Empty> : skills.map((k) => (
          <div className="list-row" key={k.name} style={{ alignItems: "flex-start" }}>
            <Switch checked={k.enabled} label={`Offer ${k.title}`} onChange={async (v) => {
              try {
                await api(`/hub/api/skills/${k.name}`, { method: "PATCH", body: { enabled: v } });
                load();
              } catch (e) {
                toast((e as Error).message, "fault");
              }
            }} />
            <div className="grow"><b>{k.title}</b><div className="meta">{k.description}</div>
              {k.files.length > 0 && <div className="meta">{k.files.length} more file{k.files.length === 1 ? "" : "s"}</div>}</div>
            <button className="btn quiet small" onClick={() => open(k.name)}>Edit</button>
            <ConfirmButton onConfirm={async () => { await api(`/hub/api/skills/${k.name}`, { method: "DELETE" }); load(); }}
              confirmText="Delete?">Delete</ConfirmButton>
          </div>
        ))}
      </div>
      <p className="muted small">Switched off: not offered in every chat, but a project or a chat can still add it.</p>
      {edit && <Modal title={`Edit ${edit.name}`} onClose={() => setEdit(null)} wide footer={<>
        <button className="btn" onClick={() => setEdit(null)}>Cancel</button><button className="btn primary" onClick={save}>Save</button></>}>
        <textarea className="textarea mono" rows={22} value={edit.text} onChange={(e) => setEdit({ ...edit, text: e.target.value })} />
      </Modal>}
    </div>
  );
}

// ------------------------------------------------------------ memory

function MemoryTab() {
  const { toast } = useApp();
  const [files, setFiles] = useState<MemoryFile[] | null>(null);
  const [edit, setEdit] = useState<{ path: string; content: string; isNew?: boolean } | null>(null);
  const load = useCallback(async () => {
    try {
      setFiles(await api<MemoryFile[]>("/hub/api/memory"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load]);
  const open = async (path: string) => {
    try {
      const r = await api<{ content: string }>("/hub/api/memory/file", { query: { path } });
      setEdit({ path, content: r.content });
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const save = async () => {
    if (!edit) return;
    try {
      await api("/hub/api/memory/file", { method: "PUT", body: { path: edit.path, content: edit.content } });
      setEdit(null);
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const del = async (path: string) => {
    try {
      await api("/hub/api/memory/file", { method: "DELETE", query: { path } });
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <div>
      <p className="lead">What the assistant remembers across chats: short Markdown files it writes when you tell it
        something worth keeping. You can read, correct and delete them here. A project has its own memory as well.</p>
      <div className="row" style={{ marginBottom: 10 }}>
        <button className="btn" onClick={() => setEdit({ path: "/memories/", content: "", isNew: true })}><Plus size={15} /> New file</button></div>
      <div className="list">
        {files === null ? <Empty>Loading</Empty> : files.length === 0 ? <Empty>Nothing remembered yet.</Empty> : files.map((f) => (
          <div className="list-row" key={f.path}>
            <div className="grow"><b className="mono">{f.path}</b><div className="meta">{f.title}, {bytes(f.size)}</div></div>
            <button className="btn quiet small" onClick={() => open(f.path)}>Open</button>
            <ConfirmButton onConfirm={() => del(f.path)} confirmText="Forget it?">Delete</ConfirmButton>
          </div>
        ))}
      </div>
      {edit && <Modal title={edit.isNew ? "New memory file" : edit.path} onClose={() => setEdit(null)} wide footer={<>
        <button className="btn" onClick={() => setEdit(null)}>Cancel</button><button className="btn primary" onClick={save}>Save</button></>}>
        {edit.isNew && <label className="field"><span>Path</span><input className="input mono" value={edit.path}
          onChange={(e) => setEdit({ ...edit, path: e.target.value })} /><small>Under /memories/, ending in .md.</small></label>}
        <textarea className="textarea" rows={16} value={edit.content} onChange={(e) => setEdit({ ...edit, content: e.target.value })} />
      </Modal>}
    </div>
  );
}

// ------------------------------------------------------------ MCP

const NEW_SERVER: McpServerConfig = { name: "", transport: "http", url: "", headers: {}, command: "", env: {}, enabled: true, trusted: false };

function McpTab({ s }: { s: S }) {
  const { toast } = useApp();
  const [status, setStatus] = useState<McpStatus[]>([]);
  const [perms, setPerms] = useState<{ tool: string; created_at: string }[]>([]);
  const [edit, setEdit] = useState<{ cfg: McpServerConfig; index: number } | null>(null);
  const [reloading, setReloading] = useState(false);
  const servers = (s.data?.settings.mcp?.servers ?? []) as McpServerConfig[];
  const load = useCallback(async () => {
    try {
      setStatus(await api<McpStatus[]>("/hub/api/mcp"));
      setPerms((await api<{ tool: string; created_at: string }[]>("/hub/api/tool-permissions"))
        .filter((p) => !p.tool.startsWith("internet:")));         // those are listed under Assistant -> Web
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load]);
  useInterval(load, status.some((x) => x.status === "starting") ? 2000 : 15000);

  const write = async (list: McpServerConfig[]) => {
    if (await s.save("mcp", { servers: list })) {
      setReloading(true);
      try {
        setStatus(await api<McpStatus[]>("/hub/api/mcp/reload", { method: "POST" }));
      } catch (e) {
        toast((e as Error).message, "fault");
      } finally {
        setReloading(false);
      }
    }
  };
  const forget = async (tool: string) => {
    try {
      await api(`/hub/api/tool-permissions/${encodeURIComponent(tool)}`, { method: "DELETE" });
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <div>
      <p className="lead">MCP servers give the assistant more tools. An HTTP server is reached at its URL; a command server
        runs inside the sandbox (no LAN access), for example <span className="mono">npx -y @modelcontextprotocol/server-memory</span>.
        Their tools ask before they run, unless you trust the server.</p>
      <div className="row" style={{ marginBottom: 10 }}>
        <button className="btn primary" onClick={() => setEdit({ cfg: { ...NEW_SERVER }, index: -1 })}><Plus size={15} /> Add a server</button>
        <button className="btn" disabled={reloading} onClick={async () => { setReloading(true); try { setStatus(await api("/hub/api/mcp/reload", { method: "POST" })); } finally { setReloading(false); } }}>
          <RefreshCw size={14} /> {reloading ? "Connecting" : "Reconnect all"}</button>
      </div>
      <div className="list" style={{ marginBottom: 24 }}>
        {servers.length === 0 ? <Empty>No MCP servers.</Empty> : servers.map((cfg, i) => {
          const st = status.find((x) => x.name === cfg.name);
          return (
            <div className="list-row" key={cfg.name + i} style={{ alignItems: "flex-start" }}>
              <Lamp state={!cfg.enabled ? "off" : st?.status === "ready" ? "run" : st?.status === "error" ? "fault" : "wait"} />
              <div className="grow">
                <b>{cfg.name}</b> <span className="tag">{cfg.transport === "stdio" ? "command" : "HTTP"}</span>
                {cfg.trusted && <span className="tag">trusted</span>}
                <div className="meta mono ellipsis">{cfg.transport === "stdio" ? cfg.command : cfg.url}</div>
                {st?.error && <div className="meta" style={{ color: "var(--fault)" }}>{st.error}</div>}
                {st && st.tools.length > 0 && <div className="meta">{st.tools.length} tools: {st.tools.map((t) => t.name).join(", ")}</div>}
              </div>
              <Switch checked={cfg.enabled} label={`${cfg.name} enabled`} onChange={(v) => write(servers.map((x, j) => (j === i ? { ...x, enabled: v } : x)))} />
              <button className="btn quiet small" onClick={() => setEdit({ cfg: { ...cfg }, index: i })}>Edit</button>
            </div>
          );
        })}
      </div>
      <h3>Tools you always allow</h3>
      <div className="list">
        {perms.length === 0 ? <Empty>None. When a tool asks and you choose "Always allow this tool", it appears here.</Empty> : perms.map((p) => (
          <div className="list-row" key={p.tool}><span className="grow mono">{p.tool}</span><span className="meta">{ago(p.created_at)}</span>
            <button className="btn quiet small" onClick={() => forget(p.tool)}>Ask again</button></div>
        ))}
      </div>
      {edit && <McpEditor cfg={edit.cfg} existing={edit.index >= 0} onClose={() => setEdit(null)}
        onDelete={() => { write(servers.filter((_, j) => j !== edit.index)); setEdit(null); }}
        onSave={(cfg) => { write(edit.index >= 0 ? servers.map((x, j) => (j === edit.index ? cfg : x)) : [...servers, cfg]); setEdit(null); }} />}
    </div>
  );
}

function KeyValues({ value, onChange, keyLabel }: { value: Record<string, string>; onChange: (v: Record<string, string>) => void;
  keyLabel: string }) {
  const rows = Object.entries(value);
  return (
    <div className="stack" style={{ gap: 6 }}>
      {rows.map(([k, v], i) => (
        <div className="row" key={i}>
          <input className="input mono" value={k} placeholder={keyLabel} onChange={(e) => {
            const next = rows.map(([a, b], j) => (j === i ? [e.target.value, b] : [a, b]));
            onChange(Object.fromEntries(next));
          }} />
          <input className="input mono" value={v} placeholder="value" onChange={(e) => onChange({ ...value, [k]: e.target.value })} />
          <button className="icon-btn" aria-label="Remove" onClick={() => { const n = { ...value }; delete n[k]; onChange(n); }}><X size={15} /></button>
        </div>
      ))}
      <div><button className="btn small" onClick={() => onChange({ ...value, "": "" })}><Plus size={13} /> Add</button></div>
    </div>
  );
}

function McpEditor({ cfg, existing, onClose, onSave, onDelete }: { cfg: McpServerConfig; existing: boolean; onClose: () => void;
  onSave: (c: McpServerConfig) => void; onDelete: () => void }) {
  const [c, setC] = useState<McpServerConfig>(cfg);
  const valid = c.name.trim() && (c.transport === "stdio" ? (c.command ?? "").trim() : /^https?:\/\//.test(c.url ?? ""));
  return (
    <Modal title={existing ? `Edit ${cfg.name}` : "Add an MCP server"} onClose={onClose} footer={<>
      {existing && <ConfirmButton onConfirm={onDelete} confirmText="Remove the server?">Remove</ConfirmButton>}
      <span className="grow" />
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!valid} onClick={() => onSave({ ...c, name: c.name.trim() })}>Save and connect</button></>}>
      <div className="form-grid">
        <label className="field"><span>Name</span><input className="input" value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} /></label>
        <label className="field"><span>Kind</span><select className="select" value={c.transport} onChange={(e) => setC({ ...c, transport: e.target.value as "http" | "stdio" })}>
          <option value="http">HTTP (a URL)</option><option value="stdio">Command in the sandbox</option></select></label>
      </div>
      {c.transport === "http" ? (
        <>
          <label className="field"><span>URL</span><input className="input mono" value={c.url ?? ""} placeholder="https://example.com/mcp"
            onChange={(e) => setC({ ...c, url: e.target.value })} /></label>
          <div className="field"><span>Headers (an API key, for example)</span>
            <KeyValues value={c.headers ?? {}} keyLabel="Authorization" onChange={(headers) => setC({ ...c, headers })} /></div>
        </>
      ) : (
        <>
          <label className="field"><span>Command</span><input className="input mono" value={c.command ?? ""}
            placeholder="npx -y @modelcontextprotocol/server-memory" onChange={(e) => setC({ ...c, command: e.target.value })} /></label>
          <div className="field"><span>Environment variables</span>
            <KeyValues value={c.env ?? {}} keyLabel="NAME" onChange={(env) => setC({ ...c, env })} /></div>
        </>
      )}
      <SwitchRow checked={!!c.trusted} onChange={(v) => setC({ ...c, trusted: v })} title="Trust this server">
        Its tools run without asking first.</SwitchRow>
    </Modal>
  );
}

// ------------------------------------------------------------ cloud

function CloudTab({ s }: { s: S }) {
  const provider = String(s.data?.settings.cloud?.provider ?? "anthropic");
  return (
    <div>
      <p className="lead">A cloud model can take over a turn the local model cannot finish. It is used only in web chats,
        only in projects where you switch it on (project settings), and never for calls from other apps.</p>
      <SectionForm s={s} section="cloud" fields={[
        { key: "provider", label: "Provider", type: "select", options: [{ value: "anthropic", label: "Anthropic (Claude)" },
          { value: "openai", label: "OpenAI-compatible (DeepSeek, OpenRouter, ...)" }] },
        { key: "model", label: "Model", type: "text", help: provider === "anthropic" ? "For example claude-sonnet-5-5." : "As the provider names it." },
        { key: "api_key", label: "API key", type: "password", help: "Stored on the Hub; shown masked." },
        { key: "base_url", label: "Base URL (OpenAI-compatible only)", type: "text", help: "For example https://api.deepseek.com/v1" },
        { key: "max_tokens", label: "Longest answer (tokens)", type: "number", min: 1000, max: 128000 },
      ]} />
    </div>
  );
}

// ------------------------------------------------------------ system

interface SystemData { version: string; services: Record<string, any>; gpu: { vram_total?: number; vram_used?: number };
  disk: Record<string, { total?: number; used?: number; free?: number }>; lastLoadError: string | null }

interface SpeechSample { file: string; model?: string; name?: string; text?: string; seconds?: number; compute?: number;
  factor?: number; capped?: boolean }

/** The last speech self-test's samples: the same sentence and seed with each voice model, what each cost, and a
 * player - to choose the voice models above by ear. */
function SpeechSamples() {
  const [data, setData] = useState<{ at?: string; cpu?: string; threads?: number; samples: SpeechSample[] } | null>(null);
  useEffect(() => {
    api<{ at?: string; cpu?: string; threads?: number; samples: SpeechSample[] }>("/hub/api/sound/samples")
      .then(setData).catch(() => setData({ samples: [] }));
  }, []);
  if (!data) return null;
  return (
    <section className="stack" style={{ marginBottom: 28 }}>
      <h3 style={{ margin: 0 }}>Speech samples</h3>
      {!data.samples.length ? (
        <p className="muted" style={{ margin: 0, maxWidth: "62ch" }}>None yet. The self-test speaks the same sentence with each
          voice model and puts the samples here: on the VM,{" "}
          <code className="mono">sudo bash /opt/slq-hub/update.sh compose exec hub python -m app.cli selftest --speech</code></p>
      ) : (
        <>
          <p className="muted" style={{ margin: 0, maxWidth: "62ch" }}>From the self-test of {data.at}
            {data.threads ? ` (${data.threads} threads${data.cpu ? ` on ${data.cpu}` : ""})` : ""}: “{data.samples[0].text}”, the same
            seed in each. The real-time factor is the seconds of work for one second of speech.</p>
          <div className="list">
            {data.samples.map((s) => (
              <div className="list-row" key={s.file}>
                <span className="grow"><b>{s.name ?? s.model ?? s.file}</b>
                  <div className="meta">{s.seconds != null && s.compute != null
                    ? `${s.seconds.toFixed(1)} s of speech in ${s.compute.toFixed(1)} s, real-time factor ${s.factor?.toFixed(2)}` : s.file}
                    {s.capped ? " - it did not stop by itself, so the end is probably noise" : ""}</div>
                  <audio controls preload="none" src={`/hub/api/sound/samples/${encodeURIComponent(s.file)}`}
                    style={{ width: "100%", maxWidth: 420, marginTop: 6 }} />
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

/** The speech service's /health in a line: its CPU threads (and CPU), the voice models in memory, whether it is
 * speaking. */
function speechDetail(h?: { threads?: number; cpu?: string; loaded?: string[]; busy?: boolean }): string {
  if (!h) return "answering";
  const loaded = h.loaded?.length ? `loaded: ${h.loaded.join(", ")}` : "no voice model loaded";
  return `${h.threads ?? "?"} CPU threads${h.cpu ? ` on ${h.cpu}` : ""}, ${loaded}${h.busy ? ", speaking" : ""}`;
}

function SystemTab() {
  const { toast, theme, setTheme, auth, refreshAuth } = useApp();
  const [sys, setSys] = useState<SystemData | null>(null);
  const [log, setLog] = useState<{ name: string; text: string } | null>(null);
  const [pw, setPw] = useState({ current: "", next: "" });
  const load = useCallback(async () => {
    try {
      setSys(await api<SystemData>("/hub/api/system"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load]);
  useInterval(load, 10000);
  const showLog = async (name: string) => {
    try {
      const r = await api<{ text: string }>(`/hub/api/system/logs/${name}`, { query: { tail: 400 } });
      setLog({ name, text: r.text });
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const changePw = async () => {
    try {
      await api("/hub/api/auth/password", { body: { current: pw.current, new: pw.next } });
      setPw({ current: "", next: "" });
      toast("Password changed", "run");
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const signOut = async () => {
    await api("/hub/api/auth/logout", { method: "POST" }).catch(() => undefined);
    refreshAuth();
  };
  const procs = useMemo(() => Object.keys((sys?.services.llm?.processes ?? {}) as Record<string, unknown>), [sys]);
  const svc = (name: string, ok: boolean | undefined, detail: string) => (
    <div className="list-row" key={name}><Lamp state={ok ? "run" : ok === false ? "fault" : "off"} />
      <span className="grow"><b>{name}</b><div className="meta">{detail}</div></span></div>
  );
  const gpu = sys?.gpu ?? {};
  return (
    <div>
      <h3 style={{ marginTop: 0 }}>Services</h3>
      {!sys ? <div className="faint">Loading</div> : (
        <div className="list" style={{ marginBottom: 20 }}>
          {svc("Model server", !sys.services.llm?.error, sys.services.llm?.error ?? `${procs.length} process${procs.length === 1 ? "" : "es"}: ${procs.join(", ") || "none"}`)}
          {svc("ComfyUI", !sys.services.comfyui?.error, sys.services.comfyui?.error ?? `torch ${sys.services.comfyui?.system?.pytorch_version ?? "?"}`)}
          {svc("Speech (CPU)", sys.services.speech?.ok, sys.services.speech?.ok ? speechDetail(sys.services.speech.health) : sys.services.speech?.error ?? `HTTP ${sys.services.speech?.status}`)}
          {svc("Web search (SearXNG)", sys.services.searxng?.ok, sys.services.searxng?.ok ? "answering" : sys.services.searxng?.error ?? `HTTP ${sys.services.searxng?.status}`)}
          {svc("Sandbox", sys.services.sandbox?.ok, sys.services.sandbox?.ok ? "answering" : sys.services.sandbox?.error ?? `HTTP ${sys.services.sandbox?.status}`)}
          <div className="list-row"><Lamp state="off" /><span className="grow"><b>GPU memory</b>
            <div className="meta">{gpu.vram_total ? `${bytes(gpu.vram_used)} of ${bytes(gpu.vram_total)} in use` : "not reported"}</div>
            {gpu.vram_total ? <div className="bar" style={{ marginTop: 6, maxWidth: 360 }}><span style={{ width: `${((gpu.vram_used ?? 0) / gpu.vram_total) * 100}%` }} /></div> : null}</span></div>
          {Object.entries(sys.disk).map(([k, d]) => (
            <div className="list-row" key={k}><Lamp state={d.free && d.total && d.free / d.total < 0.08 ? "fault" : "off"} />
              <span className="grow"><b>Disk ({k})</b><div className="meta">{bytes(d.free)} free of {bytes(d.total)}</div></span></div>
          ))}
          {sys.lastLoadError && <div className="list-row"><Lamp state="fault" /><span className="grow"><b>Last load error</b><div className="meta">{sys.lastLoadError}</div></span></div>}
        </div>
      )}
      <h3>Logs</h3>
      <div className="row wrap" style={{ marginBottom: 20 }}>
        {(procs.length ? procs : ["main"]).map((p) => <button key={p} className="btn small" onClick={() => showLog(p)}>{p}</button>)}
      </div>
      <h3>Look</h3>
      <div className="row" style={{ marginBottom: 20 }}>
        <select className="select" style={{ maxWidth: 260 }} value={theme} onChange={(e) => setTheme(e.target.value as Theme)} aria-label="Colour theme">
          <option value="auto">Follow the device</option><option value="light">Light</option><option value="dark">Dark</option>
        </select>
      </div>
      <h3>Account</h3>
      <div className="form-grid" style={{ maxWidth: "40rem" }}>
        <label className="field"><span>Current password</span><input className="input" type="password" autoComplete="current-password"
          value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></label>
        <label className="field"><span>New password</span><input className="input" type="password" autoComplete="new-password"
          value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /><small>At least 10 characters.</small></label>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn" disabled={!pw.current || pw.next.length < 10} onClick={changePw}>Change password</button>
        <button className="btn quiet" onClick={signOut}>Sign out {auth?.user?.username}</button>
      </div>
      <p className="faint small" style={{ marginTop: 24 }}>SLQ Hub {sys?.version ?? auth?.version}</p>
      {log && <Modal title={`Log: ${log.name}`} onClose={() => setLog(null)} wide>
        <pre className="code-line" style={{ maxHeight: "65vh", whiteSpace: "pre-wrap" }}>{log.text || "(empty)"}</pre>
      </Modal>}
    </div>
  );
}

