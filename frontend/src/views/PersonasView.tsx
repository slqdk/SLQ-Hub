// Personas (0.74.0, Søren: "a virtual personality that i can chat with ... like a sports women, or a housewife" -
// "also with memory"): who someone is, how they talk, their picture and voice, and what they remember about you.
// A chat with a persona has the persona's own instructions instead of the assistant's.
import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronLeft, CircleStop, Drama, ImagePlus, Menu, MessageCircle, Plus, Square, Trash2, Upload, Volume2,
  WandSparkles } from "lucide-react";
import { api, ApiError } from "../lib/api";
import { ago } from "../lib/format";
import { personaFile, playSample, stopPlaying, unlockAudio, usePlaying, type Persona, type PersonaItem,
  type PersonaOptions } from "../lib/personas";
import { linkProps, navigate } from "../lib/router";
import { useApp } from "../state";
import { Avatar } from "../components/Avatar";
import { Draft, NumberDraft, PlayButton } from "../components/fields";
import { ConfirmButton, Empty, Lamp, SwitchRow, Tabs } from "../components/ui";
import { PostsTab, ReviewList, TasksTab } from "./PersonaTasks";

type Act = (call: () => Promise<unknown>, done?: string) => Promise<unknown>;
type Overview = { personas: PersonaItem[]; options: PersonaOptions; drafts: number };

export function PersonasView({ id }: { id?: string }) {
  const { setSidebarOpen, toast } = useApp();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [persona, setPersona] = useState<Persona | null>(null);
  const [gone, setGone] = useState(false);

  const loadOverview = useCallback(async () => {
    try {
      setOverview(await api<Overview>("/hub/api/personas"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  const loadPersona = useCallback(async () => {
    if (!id) return;
    try {
      setPersona(await api<Persona>(`/hub/api/personas/${id}`));
      setGone(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setGone(true);
      else toast((e as Error).message, "fault");
    }
  }, [id, toast]);
  useEffect(() => {
    loadOverview();
  }, [loadOverview, id]);
  useEffect(() => {
    setPersona(null);
    loadPersona();
  }, [loadPersona]);
  useEffect(() => () => stopPlaying(), []);
  const working = !!persona?.job || !!persona?.tasks.some((t) => t.running);
  useEffect(() => {                                 // while portraits or a post are made: how far, every few seconds
    if (!working) return;
    const t = window.setInterval(loadPersona, 2500);
    return () => window.clearInterval(t);
  }, [working, loadPersona]);

  const act: Act = useCallback(async (call, done) => {
    try {
      const r = await call();
      if (r && typeof r === "object" && "personality" in r) setPersona(r as Persona);
      if (done) toast(done, "run");
      return r;
    } catch (e) {
      toast((e as Error).message, "fault");
      return null;
    }
  }, [toast]);

  return (
    <div className="main">
      <div className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open the menu"><Menu size={18} /></button>
        {id && <a className="icon-btn" {...linkProps({ name: "personas" })} aria-label="Back to Personas"><ChevronLeft size={18} /></a>}
        <h1 className="grow ellipsis">{id ? persona?.name ?? "Persona" : "Personas"}</h1>
      </div>
      <div className="scroll"><div className="page stack mv pe">
        {!id ? (overview ? <PersonaList overview={overview} /> : <div className="faint">Loading</div>)
          : gone ? <Empty>There is no such persona.</Empty>
          : !persona || !overview ? <div className="faint">Loading</div>
          : <PersonaPage p={persona} options={overview.options} act={act} reload={loadPersona} />}
      </div></div>
    </div>
  );
}

async function startChat(pid: string, refreshLists: () => void, toast: (m: string, k?: "fault") => void) {
  try {
    const r = await api<{ chat: { id: number } }>(`/hub/api/personas/${pid}/chat`, { method: "POST" });
    refreshLists();
    navigate({ name: "chat", id: r.chat.id });
  } catch (e) {
    toast((e as Error).message, "fault");
  }
}

// ---------------------------------------------------------------- the list

function PersonaList({ overview }: { overview: Overview }) {
  const { toast, refreshLists } = useApp();
  const [name, setName] = useState("");
  const create = async () => {
    try {
      const p = await api<Persona>("/hub/api/personas", { body: { name } });
      navigate({ name: "personas", id: p.id });
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <>
      <p className="lead">Someone to chat with instead of the assistant: a runner who coaches you, a home cook, a
        grumpy old sailor. Say who they are and how they talk - they answer as themselves, in their own voice if you
        like, and remember what you tell them from one chat to the next.</p>
      <ReviewList />
      <div className="row mv-new">
        <input className="input grow" placeholder="Name of a new persona" value={name} maxLength={40}
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && create()} />
        <button className="btn primary" disabled={!name.trim()} onClick={create}><Plus size={15} /> New persona</button>
      </div>
      {overview.personas.length === 0 ? <Empty>No personas yet.</Empty> : (
        <div className="pe-list">
          {overview.personas.map((p) => (
            <div key={p.id} className="pe-card">
              <a className="pe-card-main" {...linkProps({ name: "personas", id: p.id })}>
                <Avatar persona={p} size={56} />
                <span className="pe-card-meta">
                  <b className="ellipsis">{p.name}</b>
                  <span className="faint small pe-role">{p.role || "Who they are is not written yet"}</span>
                </span>
              </a>
              <button className="btn small" onClick={() => startChat(p.id, refreshLists, toast)}>
                <MessageCircle size={14} /> Chat</button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- a persona

type Tab = "who" | "look" | "memory" | "tasks" | "posts" | "chats";

function PersonaPage({ p, options, act, reload }: { p: Persona; options: PersonaOptions; act: Act;
  reload: () => Promise<void> }) {
  const { toast, refreshLists } = useApp();
  const [tab, setTab] = useState<Tab>(() => {          // back from Instagram's sign-in (0.76.0): its Posts tab
    const q = new URLSearchParams(window.location.search);
    return q.has("instagram") || q.has("instagram_error") ? "posts" : "who";
  });
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const bad = q.get("instagram_error");
    if (!bad && !q.has("instagram")) return;
    toast(bad || `${p.name}'s Instagram is connected`, bad ? "fault" : "run");
    window.history.replaceState(window.history.state, "", window.location.pathname);
  }, [toast, p.name]);
  const shown = useRef(`${p.name}|${p.picture}`);
  useEffect(() => {                                 // the chat list shows the persona's name and picture too
    if (shown.current === `${p.name}|${p.picture}`) return;
    shown.current = `${p.name}|${p.picture}`;
    refreshLists();
  }, [p.name, p.picture, refreshLists]);
  const base = `/hub/api/personas/${p.id}`;
  const save = (change: Partial<Persona>) => act(() => api(base, { method: "PATCH", body: change }));
  return (
    <>
      <div className="row pe-head">
        <Avatar persona={p} size={64} />
        <div className="stack grow pe-head-names">
          <Draft className="input mv-title-input" value={p.name} maxLength={40}
            onSave={(v) => v.trim() && save({ name: v })} />
          <Draft value={p.role} maxLength={160} placeholder="Who they are, in a few words: a marathon runner from Aarhus"
            onSave={(v) => save({ role: v })} />
        </div>
        <div className="row pe-head-acts">
          <button className="btn primary" onClick={() => startChat(p.id, refreshLists, toast)}>
            <MessageCircle size={15} /> Chat with {p.name}</button>
          <ConfirmButton className="btn quiet small" confirmText={`Delete ${p.name}? The chats stay, as ordinary chats.`}
            disabled={!!p.job} onConfirm={async () => {
              if (await act(() => api(base, { method: "DELETE" }))) {
                refreshLists();
                navigate({ name: "personas" });
              }
            }}><Trash2 size={14} /> Delete</ConfirmButton>
        </div>
      </div>
      <Tabs<Tab> value={tab} onChange={setTab} tabs={[
        { id: "who", label: "Who" },
        { id: "look", label: "Picture and voice" },
        { id: "memory", label: "Memory" },
        { id: "tasks", label: `Tasks${p.tasks.length ? ` (${p.tasks.length})` : ""}` },
        { id: "posts", label: "Posts" },
        { id: "chats", label: `Chats (${p.chats.length})` },
      ]} />
      {tab === "who" && <WhoTab p={p} options={options} save={save} />}
      {tab === "look" && <LookTab p={p} options={options} act={act} save={save} />}
      {tab === "memory" && <MemoryTab p={p} act={act} save={save} reload={reload} />}
      {tab === "tasks" && <TasksTab p={p} act={act} loras={options.loras} />}
      {tab === "posts" && <PostsTab p={p} reload={reload} />}
      {tab === "chats" && <ChatsTab p={p} />}
    </>
  );
}

function WhoTab({ p, options, save }: { p: Persona; options: PersonaOptions; save: (c: Partial<Persona>) => unknown }) {
  const def = options.models.find((m) => m.id === options.default_model)?.name ?? options.default_model;
  return (
    <div className="stack">
      <label className="field"><span>Personality</span>
        <Draft multiline rows={6} value={p.personality} maxLength={4000}
          placeholder="Warm, direct and a little dry. 38, physiotherapist, runs every morning before work, two kids. Loves rye bread and long runs in Marselisborg, hates excuses. Knows a lot about training, injuries and food."
          onSave={(v) => save({ personality: v })} />
        <small>Their character, life and history, what they like and know about - the more, the more themselves.</small></label>
      <label className="field"><span>How they talk</span>
        <Draft multiline rows={3} value={p.speech} maxLength={2000}
          placeholder="Short sentences, Aarhus Danish, dry humour. Calls you 'makker'. Never lectures."
          onSave={(v) => save({ speech: v })} /></label>
      <label className="field"><span>Lines in their voice</span>
        <Draft multiline rows={4} value={p.examples} maxLength={3000}
          placeholder={"Nej, nu må du lige tage dig sammen.\nFem kilometer i dag - så kan du spise den kanelsnegl."}
          onSave={(v) => save({ examples: v })} />
        <small>A few things they would say, one a line: the model copies the tone better from examples than from words about it.</small></label>
      <label className="field"><span>Greeting</span>
        <Draft multiline rows={2} value={p.greeting} maxLength={600} placeholder="Hej makker! Har du løbet i dag?"
          onSave={(v) => save({ greeting: v })} />
        <small>How a new chat starts.</small></label>
      <div className="mv-shot-row">
        <label className="field grow"><span>Language</span>
          <select className="select" value={p.language} onChange={(e) => save({ language: e.target.value })}>
            <option value="">The language you write in</option>
            {options.languages.map((l) => <option key={l} value={l}>{l}</option>)}
          </select></label>
        <label className="field grow"><span>Model</span>
          <select className="select" value={p.model} onChange={(e) => save({ model: e.target.value })}>
            <option value="">The default{def ? ` - ${def}` : ""}</option>
            {options.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            {p.model && !options.models.some((m) => m.id === p.model) && <option value={p.model}>{p.model} - not downloaded</option>}
          </select></label>
        <label className="field grow"><span>Answers</span>
          <select className="select" value={p.level} onChange={(e) => save({ level: e.target.value as Persona["level"] })}>
            <option value="quick">At once</option>
            <option value="think">Thinks first (slower)</option>
          </select></label>
      </div>
      <h3>What they can do</h3>
      <SwitchRow checked={p.memory} onChange={(v) => save({ memory: v })} title="Memory">
        Keeps notes about you - your life, plans, what you agreed - and every chat starts from them (the Memory tab).</SwitchRow>
      <SwitchRow checked={p.web} onChange={(v) => save({ web: v })} title="The web">
        Looks things up: news, opening hours, prices.</SwitchRow>
      <SwitchRow checked={p.pictures} onChange={(v) => save({ pictures: v })} title="Pictures" disabled={!options.pictures && !p.pictures}>
        Makes pictures in the chat when you ask{options.pictures ? "" : " (no picture model is downloaded)"}.</SwitchRow>
      <SwitchRow checked={p.check_pictures} onChange={(v) => save({ check_pictures: v })} title="Check pictures">
        Before a picture reaches you - in a chat or a task's post - the chat model looks at it for extra, missing or
        grown-together arms, hands and legs and things that could not exist, then at each hand up close, and a picture
        with a serious mistake is made again with a new seed (Settings, Assistant says how often). What it found shows
        under the picture. A video is looked at, not made again. Slower; the chat model must see pictures.</SwitchRow>
      <SwitchRow checked={p.read_aloud} onChange={(v) => save({ read_aloud: v })} title="Read the answers aloud"
        disabled={!options.speech && !p.read_aloud}>
        Each answer is read aloud in their voice as it comes{options.speech ? "" : " (no speech model is downloaded)"};
        without this, the speaker under an answer reads it.</SwitchRow>
    </div>
  );
}

function LookTab({ p, options, act, save }: { p: Persona; options: PersonaOptions; act: Act;
  save: (c: Partial<Persona>) => unknown }) {
  const base = `/hub/api/personas/${p.id}`;
  const picInput = useRef<HTMLInputElement>(null);
  const uploadPicture = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    await act(() => api(`${base}/picture`, { form }));
  };
  const job = p.job;
  return (
    <div className="stack">
      <div className="mv-member">
        <div className="mv-member-pic">
          {p.picture ? <a href={personaFile(p.id, p.picture)} target="_blank" rel="noreferrer">
            <img src={personaFile(p.id, p.picture)} alt={p.name} /></a>
            : <div className="mv-pic-empty">No picture yet</div>}
        </div>
        <div className="stack mv-member-fields">
          <label className="field"><span>Looks</span>
            <Draft multiline rows={3} value={p.look} maxLength={600}
              placeholder="About 38, short blond hair, freckles, a red running jacket. A photo, natural light."
              onSave={(v) => save({ look: v })} />
            <small>Their age, hair, clothes - and the style: a photo, a painting, a cartoon.</small></label>
          {options.loras.length > 0 && (<>
            <div className="row mv-lora-row">
              <label className="field grow"><span>LoRA</span>
                <select className="select" value={p.lora} onChange={(e) => save({ lora: e.target.value })}>
                  <option value="">None - from the words</option>
                  {options.loras.map((l) => <option key={l.id} value={l.id}>
                    {l.name}{l.trigger ? ` (${l.trigger})` : ""} - {l.base_label}</option>)}
                  {p.lora && !options.loras.some((l) => l.id === p.lora) && <option value={p.lora}>{p.lora} - gone</option>}
                </select></label>
              <label className="field mv-strength"><span>Strength</span>
                <NumberDraft value={p.strength} min={0} max={2} step={0.05} onSave={(v) => save({ strength: v })} /></label>
            </div>
            {p.lora && <small className={p.strength > 1 ? "warn-text" : "muted"}>
              0.5-0.8 is usually enough for a person. Above 1, hands and bodies break more often.</small>}
          </>)}
          <div className="row" style={{ flexWrap: "wrap" }}>
            <button className="btn small" disabled={!!job || !options.pictures}
              title={options.pictures ? "Four portraits with the picture model" : "No picture model is downloaded (Settings, Models)"}
              onClick={() => act(() => api(`${base}/portraits`, { method: "POST" }))}>
              <WandSparkles size={14} /> {p.variants.length ? "More portraits" : "Make portraits"}</button>
            <button className="btn small" onClick={() => picInput.current?.click()}>
              <ImagePlus size={14} /> A picture of your own</button>
            <input ref={picInput} type="file" accept="image/*" hidden onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) uploadPicture(f);
            }} />
          </div>
          {job ? (
            <div className="mv-job row small">
              <Lamp state="run" size="sm" pulse />
              <span className="grow">Making portraits{job.max ? ` - step ${job.value ?? 0} of ${job.max}` : ""}</span>
              <button className="btn quiet small" onClick={() => act(() => api(`${base}/portraits/cancel`, { method: "POST" }))}>
                <CircleStop size={14} /> Stop</button>
            </div>
          ) : p.error ? <div className="mv-job small warn-text">{p.error}</div> : null}
          {p.variants.length > 0 && (
            <div className="mv-pics">
              {p.variants.map((v) => (
                <button key={v.file} type="button" className={`mv-pic${v.file === p.picture ? " chosen" : ""}`}
                  onClick={() => v.file !== p.picture && act(() => api(`${base}/pick`, { body: { file: v.file } }))}
                  aria-pressed={v.file === p.picture} title={v.file === p.picture ? "Chosen" : "Choose this one"}>
                  <img src={personaFile(p.id, v.file)} alt="" loading="lazy" />
                  {v.file === p.picture && <span className="mv-pic-mark"><Check size={13} /></span>}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      <h3>Voice</h3>
      <VoiceRow p={p} act={act} save={save} />
    </div>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The persona's voice: one of the speech model's own, or a clip of a real voice (5-20 s of one person). */
function VoiceRow({ p, act, save }: { p: Persona; act: Act; save: (c: Partial<Persona>) => unknown }) {
  const { toast } = useApp();
  const audio = p.audio;
  const input = useRef<HTMLInputElement>(null);
  const [sending, setSending] = useState(false);
  const playing = usePlaying();
  const sample = playing?.key === `sample:${p.id}` ? playing.state : null;
  const upload = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    setSending(true);
    await act(() => api(`/hub/api/personas/${p.id}/voice`, { form }), `${p.name} speaks with the clip's voice`);
    setSending(false);
  };
  if (!audio.speech) return <div className="small faint">{audio.note ? `${cap(audio.note)}.` : "No speech model is downloaded."}</div>;
  return (
    <div className="stack">
      <div className="row mv-lora-row">
        <label className="field grow"><span>Voice</span>
          <select className="select" value={p.voice} onChange={(e) => save({ voice: e.target.value })}>
            <option value="">{audio.speech}'s default</option>
            {audio.voices.map((v) => <option key={v.id} value={v.id}>{cap(v.id)}{v.description ? ` - ${v.description}` : ""}</option>)}
            {p.voice && p.voice !== "clip" && !audio.voices.some((v) => v.id === p.voice) && <option value={p.voice}>
              {cap(p.voice)} - not one of {audio.speech}'s</option>}
            {p.voice_clip && <option value="clip">A real voice - the clip ({p.voice_clip_seconds} s)</option>}
          </select></label>
        {audio.expressive && (
          <label className="field mv-strength"><span>Expression</span>
            <NumberDraft value={p.expressiveness} min={0.25} max={2} step={0.05}
              onSave={(v) => save({ expressiveness: v })} /></label>)}
        <button type="button" className="btn small" onClick={() => {
          if (sample) {
            stopPlaying();
            return;
          }
          unlockAudio();
          playSample(p.id).catch((e) => toast((e as Error).message, "fault"));
        }}
          title={p.greeting ? "The greeting, in this voice" : "A line in this voice"}>
          {sample === "making" ? <Lamp state="wait" size="sm" /> : sample ? <Square size={12} /> : <Volume2 size={14} />}
          {sample === "making" ? " Speaking" : sample ? " Stop" : " Hear it"}</button>
        {p.voice_clip && <PlayButton src={personaFile(p.id, p.voice_clip)} label={`${p.name}'s clip`} />}
        <button type="button" className="btn small" disabled={sending} onClick={() => input.current?.click()}
          title="5-20 seconds of one person speaking: a recording, a voice memo, a video">
          <Upload size={14} /> {sending ? "Reading the clip" : p.voice_clip ? "Another clip" : "A real voice"}</button>
        <input ref={input} type="file" accept="audio/*,video/*" hidden onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) upload(f);
        }} />
      </div>
      {audio.note && <div className="small faint">{cap(audio.note)}.</div>}
      <small className="faint">The speech model works on the processor: an answer takes a little while to read aloud
        the first time, then it plays at once.</small>
    </div>
  );
}

function MemoryTab({ p, act, save, reload }: { p: Persona; act: Act; save: (c: Partial<Persona>) => unknown;
  reload: () => Promise<void> }) {
  const [text, setText] = useState(p.notes);
  const [dirty, setDirty] = useState(false);
  const from = useRef(p.notes);                    // the notes the edit started from
  useEffect(() => {
    if (!dirty) {
      setText(p.notes);
      from.current = p.notes;
    }
  }, [p.notes, dirty]);
  useEffect(() => {                                 // the persona writes in them during a chat: the newest notes here
    reload();
    const again = () => { if (document.visibilityState === "visible") reload(); };
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    return () => {
      window.removeEventListener("focus", again);
      document.removeEventListener("visibilitychange", again);
    };
  }, [reload]);
  const base = `/hub/api/personas/${p.id}`;
  const store = async (value: string) => {
    // base: when the persona wrote in them meanwhile, nothing is saved - the page shows its notes (Undo) and keeps yours
    if (await act(() => api(`${base}/notes`, { method: "PUT", body: { text: value, base: from.current } }), "Saved")) {
      setDirty(false);
    } else {                                        // (changed meanwhile, say): Save again keeps yours over them
      try {
        from.current = (await api<Persona>(base)).notes;
      } catch {
        /* the next save says what is wrong */
      }
      await reload();
    }
  };
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>What {p.name} remembers about you, in {p.name}'s own notes - written
        during your chats, one thing a line. Every chat with {p.name} starts from them, with the titles of your last
        chats. Correct them as you like.</p>
      {!p.memory && <div className="callout">Memory is off: {p.name} neither reads nor writes these notes.
        <button className="link-btn" onClick={() => save({ memory: true })}>Turn it on</button></div>}
      <textarea className="textarea mono pe-notes" rows={12} value={text} aria-label={`${p.name}'s notes`}
        placeholder={`- Is training for a marathon in April\n- Has two kids, Ida and Karl`}
        onChange={(e) => { setText(e.target.value); setDirty(true); }} />
      <div className="row">
        <span className={`small grow ${text.length > p.notes_max ? "warn-text" : "faint"}`}>
          {text.length} of {p.notes_max} characters</span>
        <ConfirmButton className="btn quiet small" confirmText="Forget everything?" disabled={!p.notes && !text}
          onConfirm={() => store("")}><Trash2 size={14} /> Forget everything</ConfirmButton>
        <button className="btn small" disabled={!dirty} onClick={() => { setText(p.notes); setDirty(false); }}>Undo</button>
        <button className="btn primary small" disabled={!dirty || text.length > p.notes_max} onClick={() => store(text)}>Save</button>
      </div>
    </div>
  );
}

function ChatsTab({ p }: { p: Persona }) {
  const { toast, refreshLists } = useApp();
  return (
    <div className="stack">
      {p.chats.length === 0 ? <Empty>No chats with {p.name} yet.</Empty> : (
        <div className="list">
          {p.chats.map((c) => (
            <a key={c.id} className="list-row mv-ep-row" {...linkProps({ name: "chat", id: c.id })}>
              <Drama size={15} className="faint" />
              <div className="grow ellipsis">{c.title}</div>
              {!!c.archived && <span className="tag">archived</span>}
              <span className="faint small">{ago(c.updated_at)}</span>
            </a>
          ))}
        </div>
      )}
      <div className="row"><button className="btn" onClick={() => startChat(p.id, refreshLists, toast)}>
        <MessageCircle size={15} /> New chat with {p.name}</button></div>
    </div>
  );
}
