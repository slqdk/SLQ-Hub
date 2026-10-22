// Settings: models, the queue, apps and keys, the agent, skills, memory, MCP, cloud, system.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Copy, Download, Menu, Plus, RefreshCw, Upload as UploadIcon, X } from "lucide-react";
import { api, type ApiKey, type McpServerConfig, type McpStatus, type MemoryFile, type ModelEntry,
  type Settings, type Skill } from "../lib/api";
import { ago, bytes, residencyName } from "../lib/format";
import { navigate } from "../lib/router";
import { useApp, type Theme } from "../state";
import { QueueModal, plateState } from "../components/Queue";
import { ConfirmButton, Empty, Lamp, Modal, Switch, SwitchRow, copyText, useInterval } from "../components/ui";
import { forgetSettings } from "./ChatView";

const TABS = [
  { id: "models", label: "Models" }, { id: "queue", label: "Queue" }, { id: "apps", label: "Apps and keys" },
  { id: "agent", label: "Assistant" }, { id: "skills", label: "Skills" }, { id: "memory", label: "Memory" },
  { id: "mcp", label: "MCP servers" }, { id: "cloud", label: "Cloud" }, { id: "system", label: "System" },
];

function useSettings() {
  const { toast } = useApp();
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
      toast("Saved", "run");
      return true;
    } catch (e) {
      toast((e as Error).message, "fault");
      return false;
    }
  }, [toast]);
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
          <div className="tabs" role="tablist">
            {TABS.map((t) => <button key={t.id} className="tab" role="tab" aria-selected={t.id === current}
              onClick={() => navigate({ name: "settings", tab: t.id }, true)}>{t.label}</button>)}
          </div>
          {current === "models" && <ModelsTab s={s} />}
          {current === "queue" && <QueueTab s={s} />}
          {current === "apps" && <AppsTab s={s} />}
          {current === "agent" && <AgentTab s={s} />}
          {current === "skills" && <SkillsTab />}
          {current === "memory" && <MemoryTab />}
          {current === "mcp" && <McpTab s={s} />}
          {current === "cloud" && <CloudTab s={s} />}
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
  const downloading = !!models && [...models.llm, ...models.aux, ...models.comfy].some((m) => m.downloads.some((d) => d.status === "downloading" || d.status === "queued"));
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
        extra={(m) => m.installed && <>
          {m.loaded ? <span className="tag"><Lamp state="run" size="sm" /> Loaded</span>
            : <button className="btn small" disabled={!!busy} onClick={() => load(m.id)}>{busy === `load-${m.id}` ? "Loading" : "Load"}</button>}
          <button className="btn quiet small" onClick={() => setAdvanced(m)}>Server settings</button>
        </>} />
      <ModelGroup title="Helpers for libraries" items={models.aux} busy={busy} onDownload={download} onCancel={cancel}
        note="Run on the CPU (the image embedder can use the GPU, see Assistant, Libraries)." />
      <ModelGroup title="Image models (ComfyUI)" items={models.comfy} busy={busy} onDownload={download} onCancel={cancel} />
      {advanced && <ServerSettings model={advanced} onClose={() => setAdvanced(null)} onSaved={() => { setAdvanced(null); refreshModels(); }} />}
    </div>
  );
}

function ModelGroup({ title, items, busy, onDownload, onCancel, extra, note }: { title: string; items: ModelEntry[];
  busy: string | null; onDownload: (id: string) => void; onCancel: (id: string) => void;
  extra?: (m: ModelEntry) => ReactNode; note?: string }) {
  return (
    <section style={{ marginBottom: 26 }}>
      <h3>{title}</h3>
      {note && <p className="muted small" style={{ marginTop: -4 }}>{note}</p>}
      <div className="list">
        {items.map((m) => {
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
                <b>{m.name}</b> {m.default ? null : <span className="tag">optional</span>}
                <div className="meta">{m.description}{size ? ` ${bytes(size)}.` : ""}</div>
                {active.length > 0 && <div style={{ marginTop: 6 }}>
                  <div className="bar wait"><span style={{ width: `${total ? (done / total) * 100 : 2}%` }} /></div>
                  <div className="meta num">{bytes(done)} of {bytes(total)}{rate ? `, ${bytes(rate)}/s` : ""}</div></div>}
                {failed && !active.length && <div className="meta" style={{ color: "var(--fault)" }}>Download failed: {failed.error}</div>}
              </div>
              <div className="row">
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

function ServerSettings({ model, onClose, onSaved }: { model: ModelEntry; onClose: () => void; onSaved: () => void }) {
  const { toast } = useApp();
  const server = (model.server ?? {}) as Record<string, unknown>;
  const [ctx, setCtx] = useState(String(server.ctx ?? ""));
  const [parallel, setParallel] = useState(String(server.parallel ?? ""));
  const [cache, setCache] = useState(String(server.cache_type ?? "q8_0"));
  const [extra, setExtra] = useState(((server.extra as string[] | undefined) ?? []).join(" "));
  const save = async () => {
    try {
      const r = await api<{ reloadNeeded: boolean }>(`/hub/api/models/${model.id}`, { method: "PUT", body: { server: {
        ctx: Number(ctx), parallel: Number(parallel), cache_type: cache, extra: extra.trim() ? extra.trim().split(/\s+/) : [] } } });
      toast(r.reloadNeeded ? "Saved. The model loads with these settings the next time it is loaded." : "Saved", "run");
      onSaved();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <Modal title={`${model.name}: server settings`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" onClick={save}>Save</button></>}>
      <p className="muted" style={{ margin: 0 }}>How llama-server runs this model. The context is shared by the slots
        (unified KV cache); more context or slots need more VRAM.</p>
      <div className="form-grid">
        <label className="field"><span>Context (tokens, all slots)</span><input className="input" type="number" value={ctx} onChange={(e) => setCtx(e.target.value)} /></label>
        <label className="field"><span>Slots (parallel requests)</span><input className="input" type="number" min={1} max={8} value={parallel} onChange={(e) => setParallel(e.target.value)} /></label>
        <label className="field"><span>KV cache type</span><select className="select" value={cache} onChange={(e) => setCache(e.target.value)}>
          {["f16", "q8_0", "q4_0"].map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
      </div>
      <label className="field"><span>Extra llama-server flags</span><input className="input mono" value={extra} onChange={(e) => setExtra(e.target.value)}
        placeholder="--temp 0.6" /><small>For experiments. --host, --port and --api-key are set by the Hub.</small></label>
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

// ------------------------------------------------------------ apps and keys

function AppsTab({ s }: { s: S }) {
  const { toast } = useApp();
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [made, setMade] = useState<{ name: string; key: string } | null>(null);
  const [origins, setOrigins] = useState("");
  const [conn, setConn] = useState<{ lanHost: string; uiPort: number; ollamaPort: number } | null>(null);
  const load = useCallback(async () => {
    try {
      setKeys(await api<ApiKey[]>("/hub/api/keys"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  useEffect(() => {
    load();
    api<{ lanHost: string; uiPort: number; ollamaPort: number }>("/hub/api/connection").then(setConn, () => undefined);
  }, [load]);
  const cors = s.data?.settings.api?.cors_origins as string[] | undefined;
  useEffect(() => setOrigins((cors ?? []).join("\n")), [cors]);
  const patch = async (k: ApiKey, body: Record<string, unknown>) => {
    try {
      await api(`/hub/api/keys/${k.id}`, { method: "PATCH", body });
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
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
  return (
    <div>
      <p className="lead">Other apps use the models through the Hub, in the same queue as everything else. Each app gets
        its own key; the model API answers only on the LAN and WireGuard.</p>
      <div className="row" style={{ marginBottom: 8 }}><h3 style={{ margin: 0 }} className="grow">Keys</h3>
        <button className="btn small" onClick={() => { setAdding(true); setMade(null); }}><Plus size={14} /> New key</button></div>
      <div className="list" style={{ marginBottom: 24 }}>
        {keys === null ? <Empty>Loading</Empty> : keys.length === 0 ? <Empty>No keys yet. Make one per app.</Empty> : keys.map((k) => (
          <div className="list-row" key={k.id} style={{ flexWrap: "wrap" }}>
            <Switch checked={!!k.enabled} onChange={(v) => patch(k, { enabled: v })} label={`${k.name} enabled`} />
            <span className="grow" style={{ minWidth: 180 }}><b>{k.name}</b>
              <div className="meta"><span className="mono">{k.prefix}...</span>, {k.last_used ? `used ${ago(k.last_used)}` : "never used"}
                {k.allowed_ips ? `, only from ${k.allowed_ips}` : ""}</div></span>
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
        <div className="callout"><b>Ollama apps</b> (FamilyLog and others). Ollama clients send no key, so it goes in the address:
          <div className="code-line">http://{lanHost}:{ollamaPort}/k/{example}</div>
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
        { key: "allow_external", label: "Allow the model API from outside the LAN", type: "bool",
          help: "Through the proxy (hub.slq.dk) with a key. Off keeps the models on the LAN and WireGuard only." }]} />

      {adding && <NewKey onClose={() => setAdding(false)} made={made} onMade={(m) => { setMade(m); load(); }} />}
    </div>
  );
}

function NewKey({ onClose, made, onMade }: { onClose: () => void; made: { name: string; key: string } | null;
  onMade: (m: { name: string; key: string }) => void }) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const [priority, setPriority] = useState("normal");
  const [ips, setIps] = useState("");
  const create = async () => {
    try {
      const k = await api<{ name: string; key: string }>("/hub/api/keys", { body: { name, priority, allowed_ips: ips } });
      onMade(k);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <Modal title="New key" onClose={onClose} footer={made ? <button className="btn primary" onClick={onClose}>Done</button>
      : <><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!name.trim()} onClick={create}>Make the key</button></>}>
      {made ? (
        <>
          <p style={{ margin: 0 }}>The key for <b>{made.name}</b> is shown only now.</p>
          <div className="code-line">{made.key}</div>
          <button className="btn small" onClick={async () => toast((await copyText(made.key)) ? "Copied" : "Copying failed", "run")}><Copy size={14} /> Copy the key</button>
        </>
      ) : (
        <>
          <label className="field"><span>App</span><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="FamilyLog" /></label>
          <label className="field"><span>Priority in the queue</span><select className="select" value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="interactive">Interactive: like a web chat</option><option value="normal">Normal: most apps</option>
            <option value="background">Background: batch work</option></select></label>
          <label className="field"><span>Only from these addresses</span><input className="input" value={ips} onChange={(e) => setIps(e.target.value)}
            placeholder="30.11.0.20, 10.8.0.0/24" /><small>Empty: any address on the LAN.</small></label>
        </>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------ assistant

function AgentTab({ s }: { s: S }) {
  const { models } = useApp();
  const current = String(s.data?.settings.images?.model ?? "qwen-image-2.1");
  const imageModels = (models?.comfy ?? []).filter((m) => m.installed && !m.id.endsWith("-turbo"));
  const imageOptions = imageModels.length ? imageModels.map((m) => ({ value: m.id, label: m.name }))
    : [{ value: current, label: "No image model downloaded" }];
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
        { key: "search_results", label: "Results per web search", type: "number", min: 1, max: 20 },
        { key: "fetch_max_chars", label: "Longest page text read (characters)", type: "number", min: 2000, max: 200000 },
      ]} />
      <SectionForm s={s} section="sandbox" title="Sandbox" intro="Where the assistant runs code: no access to your LAN, internet allowed." fields={[
        { key: "timeout_s", label: "Default time limit for a command (seconds)", type: "number", min: 5, max: 3600 },
        { key: "max_timeout_s", label: "Longest a command may run (seconds)", type: "number", min: 10, max: 7200 },
      ]} />
      <SectionForm s={s} section="images" title="Images" fields={[
        { key: "model", label: "Model for pictures", type: "select", options: imageOptions,
          help: "Ask for a picture in any chat. The assistant can still use the other model if you name it." },
        { key: "steps", label: "Steps for a normal image", type: "number", min: 4, max: 80 },
        { key: "turbo_steps", label: "Steps for a fast image", type: "number", min: 2, max: 20 },
        { key: "default_size", label: "Default size (pixels, the long side)", type: "number", min: 512, max: 2048, step: 64 },
      ]} />
    </div>
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
      setPerms(await api("/hub/api/tool-permissions"));
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

