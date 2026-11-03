// Movies (0.72.0): short films made shot by shot. A series keeps its look, its cast and its sets; an episode goes
// from what happens to a storyboard, keyframes, clips and a preview - each stage stops for you (approve, choose,
// keep) and goes on from there whenever you come back. The Hub sets each shot's LoRAs.
// 0.73.0: the sound - each character's voice, the lines spoken, an effect for each shot, the series' theme - and the
// cut: the film with the lines at their times, the theme under them, speech bubbles and an end card.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, Check, ChevronLeft, CircleStop, Clapperboard, Download, Menu, Mic, Music, Plus,
  RefreshCw, Scissors, Trash2, Upload, WandSparkles } from "lucide-react";
import { api, ApiError } from "../lib/api";
import { jobText, linesToText, movieFile, queuedFor, textToLines, type CastMember, type CutSettings, type Episode,
  type MovieJob, type MovieOptions, type MovieSet, type MovieStyle, type Series, type SeriesItem, type Shot,
  type Variant } from "../lib/movies";
import { linkProps, navigate } from "../lib/router";
import { useApp } from "../state";
import { ConfirmButton, Empty, Lamp, Tabs } from "../components/ui";
import { Draft, NumberDraft, PlayButton } from "../components/fields";

type Act = (call: () => Promise<unknown>, done?: string) => Promise<unknown>;
type Overview = { series: SeriesItem[]; options: MovieOptions };

export function MoviesView({ id, episode }: { id?: string; episode?: string }) {
  const { setSidebarOpen, toast } = useApp();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [series, setSeries] = useState<Series | null>(null);
  const [gone, setGone] = useState(false);

  const loadOverview = useCallback(async () => {
    try {
      setOverview(await api<Overview>("/hub/api/movies"));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [toast]);
  const loadSeries = useCallback(async () => {
    if (!id) return;
    try {
      setSeries(await api<Series>(`/hub/api/movies/${id}`));
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
    setSeries(null);
    loadSeries();
  }, [loadSeries]);
  const working = !!series && Object.keys(series.jobs).length > 0;
  useEffect(() => {                                // while something is made: what it does, every two seconds
    if (!working) return;
    const t = window.setInterval(loadSeries, 2000);
    return () => window.clearInterval(t);
  }, [working, loadSeries]);

  const act: Act = useCallback(async (call, done) => {
    try {
      const r = await call();
      if (r && typeof r === "object" && "episodes" in r) setSeries(r as Series);
      if (done) toast(done, "run");
      return r;
    } catch (e) {
      toast((e as Error).message, "fault");
      return null;
    }
  }, [toast]);

  const ep = series && episode ? series.episodes.find((e) => e.id === episode) : undefined;
  const title = !id ? "Movies" : ep ? ep.title : series?.name ?? "Movies";
  return (
    <div className="main">
      <div className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open the menu"><Menu size={18} /></button>
        {id && <a className="icon-btn" {...linkProps(episode ? `/movies/${id}` : "/movies")}
          aria-label={episode ? "Back to the series" : "Back to Movies"}><ChevronLeft size={18} /></a>}
        <h1 className="grow ellipsis">{title}</h1>
      </div>
      <div className="scroll"><div className="page stack mv">
        {!id ? (overview ? <SeriesList overview={overview} /> : <div className="faint">Loading</div>)
          : gone ? <Empty>There is no such series.</Empty>
          : !series || !overview ? <div className="faint">Loading</div>
          : episode ? <EpisodePage series={series} eid={episode} act={act} />
          : <SeriesPage series={series} options={overview.options} act={act} />}
      </div></div>
    </div>
  );
}

// ---------------------------------------------------------------- small parts

/** Pictures to choose from; the chosen one is marked. */
function Pictures({ sid, variants, chosen, onPick }: { sid: string; variants: Variant[]; chosen: string;
  onPick: (file: string) => void }) {
  return (
    <div className="mv-pics">
      {variants.map((v) => (
        <button key={v.file} type="button" className={`mv-pic${v.file === chosen ? " chosen" : ""}`}
          onClick={() => v.file !== chosen && onPick(v.file)} aria-pressed={v.file === chosen}
          title={v.file === chosen ? "Chosen" : "Choose this one"}>
          <img src={movieFile(sid, v.file)} alt="" loading="lazy" />
          {v.file === chosen && <span className="mv-pic-mark"><Check size={13} /></span>}
        </button>
      ))}
    </div>
  );
}

function JobLine({ job, error, onStop }: { job?: MovieJob; error?: string; onStop?: () => void }) {
  if (job) {
    return (
      <div className="mv-job row small">
        <Lamp state="run" size="sm" pulse />
        <span className="grow">{jobText(job)}{job.queued.length > 0 ? ` - ${job.queued.length} more waiting` : ""}</span>
        {onStop && <button className="btn quiet small" onClick={onStop}><CircleStop size={14} /> Stop</button>}
      </div>
    );
  }
  return error ? <div className="mv-job small warn-text">{error}</div> : null;
}

function StateTag({ state, kept, fresh = "chosen" }: { state: string; kept?: boolean; fresh?: string }) {
  if (state === "stale") return <span className="tag mv-warn">before a change</span>;
  if (state === "fresh") return <span className="tag ok">{kept === false ? "to look at" : kept ? "kept" : fresh}</span>;
  return null;
}

function Stale({ onKeep }: { onKeep: () => void }) {
  return <div className="small warn-text">Made before a change. <button type="button" className="link-btn"
    onClick={onKeep}>Keep as it is</button></div>;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function family(series: Series, options: MovieOptions): string {
  return options.models.find((m) => m.id === series.style.model)?.family ?? options.default_model.family;
}

// ---------------------------------------------------------------- the list

function SeriesList({ overview }: { overview: Overview }) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const create = async () => {
    try {
      const s = await api<Series>("/hub/api/movies", { body: { name } });
      navigate(`/movies/${s.id}`);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <>
      <p className="lead">Short films made shot by shot. A series keeps its look, its cast (each character with
        their LoRA and voice), its sets and its theme. An episode goes from what happens to a storyboard, keyframes,
        clips, its sound and the cut - each stage stops for you: approve the storyboard, choose a keyframe for each
        shot, keep each clip, listen to the lines.</p>
      <div className="row mv-new">
        <input className="input grow" placeholder="Name of a new series" value={name} maxLength={60}
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && create()} />
        <button className="btn primary" disabled={!name.trim()} onClick={create}><Plus size={15} /> New series</button>
      </div>
      {!overview.options.models.length && <div className="callout">No picture model is downloaded yet (Settings,
        Models).</div>}
      {overview.series.length === 0 ? <Empty>No series yet.</Empty> : (
        <div className="mv-series-list">
          {overview.series.map((s) => (
            <a key={s.id} className="mv-series-card" {...linkProps(`/movies/${s.id}`)}>
              {s.cover ? <img src={movieFile(s.id, s.cover)} alt="" loading="lazy" />
                : <div className="mv-cover-empty"><Clapperboard size={28} /></div>}
              <div className="mv-series-meta">
                <b className="ellipsis">{s.name}</b>
                <span className="faint small">{s.cast} character{s.cast === 1 ? "" : "s"} · {s.episodes} episode{s.episodes === 1 ? "" : "s"}</span>
                {s.working && <span className="tag ok">working</span>}
              </div>
            </a>
          ))}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- a series

type SeriesTab = "episodes" | "cast" | "sets" | "look";

function SeriesPage({ series, options, act }: { series: Series; options: MovieOptions; act: Act }) {
  const [tab, setTab] = useState<SeriesTab>(series.cast.length ? "episodes" : "cast");
  const base = `/hub/api/movies/${series.id}`;
  return (
    <>
      <div className="row mv-head">
        <Draft className="input mv-title-input" value={series.name} maxLength={60}
          onSave={(v) => v.trim() && act(() => api(base, { method: "PATCH", body: { name: v } }))} />
        <ConfirmButton className="btn quiet small" confirmText="Delete the series with its pictures and clips?"
          disabled={Object.keys(series.jobs).length > 0}
          onConfirm={async () => {
            if (await act(() => api(base, { method: "DELETE" }))) navigate("/movies");
          }}><Trash2 size={14} /> Delete</ConfirmButton>
      </div>
      <Tabs<SeriesTab> value={tab} onChange={setTab} tabs={[
        { id: "episodes", label: `Episodes (${series.episodes.length})` },
        { id: "cast", label: `Cast (${series.cast.length})` },
        { id: "sets", label: `Sets (${series.sets.length})` },
        { id: "look", label: "Look" },
      ]} />
      {tab === "episodes" && <EpisodesTab series={series} act={act} />}
      {tab === "cast" && <Members series={series} options={options} kind="cast" act={act} />}
      {tab === "sets" && <Members series={series} options={options} kind="sets" act={act} />}
      {tab === "look" && <LookTab series={series} options={options} act={act} />}
    </>
  );
}

function stagesText(e: Episode): string {
  const p = e.progress;
  if (!p.shots) return "No storyboard yet";
  return `${p.shots} shot${p.shots === 1 ? "" : "s"}, ${p.seconds} s · storyboard ${p.approved ? "approved" : "to approve"}`
    + ` · keyframes ${p.keyframes}/${p.shots} · clips ${p.kept}/${p.shots} kept`
    + (p.lines ? ` · lines ${p.spoken}/${p.lines} spoken` : "")
    + (p.final === "fresh" ? " · cut" : p.preview === "fresh" ? " · preview" : "");
}

function EpisodesTab({ series, act }: { series: Series; act: Act }) {
  const [title, setTitle] = useState("");
  const [premise, setPremise] = useState("");
  const add = async () => {
    const r = await act(() => api<Series>(`/hub/api/movies/${series.id}/episodes`, { body: { title, premise } }));
    const id = (r as Series | null)?.new_episode;
    if (id) navigate(`/movies/${series.id}/${id}`);
  };
  return (
    <>
      {series.episodes.length > 0 && (
        <div className="list">
          {series.episodes.map((e) => (
            <a key={e.id} className="list-row mv-ep-row" {...linkProps(`/movies/${series.id}/${e.id}`)}>
              <div className="grow"><b>{e.title}</b><div className="meta">{stagesText(e)}</div></div>
              {Object.keys(series.jobs).some((k) => k.endsWith(`:${e.id}`)) && <span className="tag ok">working</span>}
            </a>
          ))}
        </div>
      )}
      <h3>New episode</h3>
      {!series.cast.length && <div className="callout">Add the cast first (Cast): the storyboard uses their names.</div>}
      <label className="field"><span>Title</span>
        <input className="input" value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} /></label>
      <label className="field"><span>What happens</span>
        <textarea className="textarea" rows={4} value={premise} onChange={(e) => setPremise(e.target.value)}
          placeholder="Far sneaks to the fridge at night for the last slice of cake - and the others have had the same idea." /></label>
      <div className="row"><button className="btn primary" disabled={!premise.trim()} onClick={add}><Plus size={15} /> Add the episode</button></div>
    </>
  );
}

function Members({ series, options, kind, act }: { series: Series; options: MovieOptions; kind: "cast" | "sets";
  act: Act }) {
  const [name, setName] = useState("");
  const items: (CastMember | MovieSet)[] = kind === "cast" ? series.cast : series.sets;
  const add = async () => {
    if (await act(() => api(`/hub/api/movies/${series.id}/${kind}`, { body: { name } }))) setName("");
  };
  return (
    <>
      <p className="small faint">{kind === "cast"
        ? "Each character's sheet - the whole figure, front on - is what shots with several characters are combined from. A character with a LoRA gets it in every shot of their own."
        : "A picture of each place, empty: shots with several characters are combined from it and the characters' sheets."}</p>
      {items.map((it) => <MemberCard key={it.id} series={series} options={options} kind={kind} item={it} act={act} />)}
      <div className="row mv-new">
        <input className="input grow" value={name} maxLength={60} onChange={(e) => setName(e.target.value)}
          placeholder={kind === "cast" ? "Name of a character" : "Name of a set (Kitchen, Car ...)"}
          onKeyDown={(e) => e.key === "Enter" && name.trim() && add()} />
        <button className="btn" disabled={!name.trim()} onClick={add}><Plus size={15} /> Add</button>
      </div>
    </>
  );
}

function MemberCard({ series, options, kind, item, act }: { series: Series; options: MovieOptions;
  kind: "cast" | "sets"; item: CastMember | MovieSet; act: Act }) {
  const sid = series.id;
  const key = `${kind}:${item.id}`;
  const base = `/hub/api/movies/${sid}/${kind}/${item.id}`;
  const c = kind === "cast" ? (item as CastMember) : null;
  const chosen = c ? c.sheet : (item as MovieSet).picture;
  const state = c ? c.sheet_state : (item as MovieSet).picture_state;
  const fam = family(series, options);
  const fitting = options.loras.filter((l) => l.base === fam);
  const notes = item.variants[0]?.notes ?? [];
  return (
    <div className="mv-member">
      <div className="mv-member-pic">
        {chosen ? <a href={movieFile(sid, chosen)} target="_blank" rel="noreferrer"><img src={movieFile(sid, chosen)} alt={item.name} /></a>
          : <div className="mv-pic-empty">{c ? "No sheet chosen" : "No picture chosen"}</div>}
        {state === "stale" && <Stale onKeep={() => act(() => api(`${base}/pick`, { body: { keep: true } }))} />}
      </div>
      <div className="stack mv-member-fields">
        <div className="row">
          <Draft className="input mv-name" value={item.name} maxLength={60}
            onSave={(v) => v.trim() && act(() => api(base, { method: "PATCH", body: { name: v } }))} />
          <ConfirmButton className="btn quiet small" confirmText={`Remove ${item.name}?`} label={`Remove ${item.name}`}
            onConfirm={() => act(() => api(base, { method: "DELETE" }))}><Trash2 size={14} /></ConfirmButton>
        </div>
        <label className="field"><span>{c ? "Looks" : "What it looks like"}</span>
          <Draft multiline rows={2} value={item.look} maxLength={600}
            placeholder={c ? "tall, short brown hair, glasses, green jumper" : "a small Danish kitchen, white cupboards, a big fridge"}
            onSave={(v) => act(() => api(base, { method: "PATCH", body: { look: v } }))} /></label>
        {c && (
          <div className="row mv-lora-row">
            <label className="field grow"><span>LoRA</span>
              <select className="select" value={c.lora}
                onChange={(e) => act(() => api(base, { method: "PATCH", body: { lora: e.target.value } }))}>
                <option value="">None - from the words</option>
                {fitting.map((l) => <option key={l.id} value={l.id}>{l.name}{l.trigger ? ` (${l.trigger})` : ""}</option>)}
                {c.lora && !fitting.some((l) => l.id === c.lora) && <option value={c.lora}>
                  {options.loras.find((l) => l.id === c.lora)?.name ?? c.lora} - not for this picture model</option>}
              </select></label>
            <label className="field mv-strength"><span>Strength</span>
              <NumberDraft value={c.strength} min={0} max={2} step={0.05}
                onSave={(v) => act(() => api(base, { method: "PATCH", body: { strength: v } }))} /></label>
          </div>
        )}
        {c && <VoiceRow series={series} c={c} base={base} act={act} />}
        {notes.map((n) => <div key={n} className="small warn-text">{cap(n)}.</div>)}
        <div className="row">
          <button className="btn small" disabled={!!series.jobs[key]}
            onClick={() => act(() => api(`${base}/make`, { method: "POST" }))}>
            <WandSparkles size={14} /> {item.variants.length ? "More pictures" : c ? "Make the sheet" : "Make the picture"}</button>
        </div>
        <JobLine job={series.jobs[key]} error={series.errors[key]}
          onStop={() => act(() => api(`/hub/api/movies/${sid}/jobs/cancel`, { body: { key } }))} />
        {item.variants.length > 0 && <Pictures sid={sid} variants={item.variants} chosen={chosen}
          onPick={(file) => act(() => api(`${base}/pick`, { body: { file } }))} />}
      </div>
    </div>
  );
}

/** A character's voice: one of the speech model's own, or a clip of a real voice (5-20 s of one person). */
function VoiceRow({ series, c, base, act }: { series: Series; c: CastMember; base: string; act: Act }) {
  const audio = series.audio;
  const input = useRef<HTMLInputElement>(null);
  const [sending, setSending] = useState(false);
  const upload = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    setSending(true);
    await act(() => api(`${base}/voice`, { form }), `${c.name} speaks with the clip's voice`);
    setSending(false);
  };
  const voices = audio.voices.filter((v) => v.id !== "default");
  return (
    <div className="row mv-lora-row">
      {audio.speech ? (
        <label className="field grow"><span>Voice</span>
          <select className="select" value={c.voice}
            onChange={(e) => act(() => api(base, { method: "PATCH", body: { voice: e.target.value } }))}>
            <option value="">{audio.speech}'s default</option>
            {voices.map((v) => <option key={v.id} value={v.id}>{cap(v.id)}{v.description ? ` - ${v.description}` : ""}</option>)}
            {c.voice && c.voice !== "clip" && !voices.some((v) => v.id === c.voice) && <option value={c.voice}>
              {cap(c.voice)} - not one of {audio.speech}'s</option>}
            {c.voice_clip && <option value="clip">A real voice - the clip ({c.voice_clip_seconds} s)</option>}
          </select></label>
      ) : <div className="grow small faint">{audio.note ? `${cap(audio.note)}.` : "No speech model is downloaded."}</div>}
      {audio.speech && audio.expressive && (
        <label className="field mv-strength"><span>Expression</span>
          <NumberDraft value={c.expressiveness} min={0.25} max={2} step={0.05}
            onSave={(v) => act(() => api(base, { method: "PATCH", body: { expressiveness: v } }))} /></label>)}
      {c.voice_clip && <PlayButton src={movieFile(series.id, c.voice_clip)} label={`${c.name}'s clip`} />}
      <button type="button" className="btn small" disabled={sending} onClick={() => input.current?.click()}
        title="5-20 seconds of one person speaking: a recording, a voice memo, a video">
        <Upload size={14} /> {sending ? "Reading the clip" : c.voice_clip ? "Another clip" : "A real voice"}</button>
      <input ref={input} type="file" accept="audio/*,video/*" hidden onChange={(e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (f) upload(f);
      }} />
    </div>
  );
}

function LookTab({ series, options, act }: { series: Series; options: MovieOptions; act: Act }) {
  const st = series.style;
  const save = (change: Partial<MovieStyle>) =>
    act(() => api(`/hub/api/movies/${series.id}`, { method: "PATCH", body: { style: change } }));
  const fam = family(series, options);
  const model = options.models.find((m) => m.id === (st.model || options.default_model.id));
  const fitting = options.loras.filter((l) => l.base === fam);
  return (
    <div className="stack">
      <div className="form-grid">
        <label className="field"><span>Picture model</span>
          <select className="select" value={st.model} onChange={(e) => save({ model: e.target.value })}>
            <option value="">Settings' ({options.default_model.name || "none"})</option>
            {options.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          {model && !model.combine && <small className="warn-text">It cannot combine pictures: shots with several
            characters become new pictures from the words.</small>}</label>
        <label className="field"><span>Style LoRA</span>
          <select className="select" value={st.lora} onChange={(e) => save({ lora: e.target.value })}>
            <option value="">None</option>
            {fitting.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            {st.lora && !fitting.some((l) => l.id === st.lora) && <option value={st.lora}>
              {options.loras.find((l) => l.id === st.lora)?.name ?? st.lora} - not for this picture model</option>}
          </select></label>
        <label className="field"><span>Its strength</span>
          <NumberDraft value={st.strength} min={0} max={2} step={0.05} onSave={(v) => save({ strength: v })} /></label>
        <label className="field"><span>Format</span>
          <select className="select" value={st.format} onChange={(e) => save({ format: e.target.value })}>
            <option value="portrait">Portrait 9:16 (reels)</option>
            <option value="landscape">Landscape 16:9</option>
            <option value="square">Square</option>
          </select></label>
        <label className="field"><span>Pictures to choose from</span>
          <select className="select" value={st.variants} onChange={(e) => save({ variants: Number(e.target.value) })}>
            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
          </select></label>
        <label className="field"><span>Video model</span>
          <select className="select" value={st.video_model} onChange={(e) => save({ video_model: e.target.value })}>
            <option value="">Settings' ({options.default_video || "none downloaded"})</option>
            {options.video_models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select></label>
        <label className="field"><span>Video quality</span>
          <select className="select" value={st.quality} onChange={(e) => save({ quality: e.target.value })}>
            <option value="fast">Fast</option>
            <option value="full">Full - several times slower</option>
          </select></label>
        <label className="field"><span>Longest episode (seconds)</span>
          <NumberDraft value={st.max_seconds} min={5} max={120} step={1} onSave={(v) => save({ max_seconds: v })} /></label>
        <label className="field"><span>Language of the lines</span>
          <select className="select" value={st.language} onChange={(e) => save({ language: e.target.value })}>
            {options.languages.map((l) => <option key={l} value={l}>{l}</option>)}
            {!options.languages.includes(st.language) && <option value={st.language}>{st.language}</option>}
          </select>
          {series.audio.note && <small className="warn-text">{cap(series.audio.note)}{/[.!?]$/.test(series.audio.note) ? "" : "."}</small>}</label>
        {series.audio.speech && (
          <label className="field"><span>The narrator's voice</span>
            <select className="select" value={st.narrator_voice} onChange={(e) => save({ narrator_voice: e.target.value })}>
              <option value="">{series.audio.speech}'s default</option>
              {series.audio.voices.filter((v) => v.id !== "default").map((v) => <option key={v.id} value={v.id}>
                {cap(v.id)}{v.description ? ` - ${v.description}` : ""}</option>)}
              {st.narrator_voice && !series.audio.voices.some((v) => v.id === st.narrator_voice) && (
                <option value={st.narrator_voice}>{cap(st.narrator_voice)} - not one of {series.audio.speech}'s</option>)}
            </select></label>)}
      </div>
      <label className="field"><span>Style words - on every picture and clip</span>
        <Draft multiline rows={2} value={st.words} maxLength={600} onSave={(v) => save({ words: v })} /></label>
      <label className="field"><span>Rules for the storyboards</span>
        <Draft multiline rows={4} value={st.rules} maxLength={1500} onSave={(v) => save({ rules: v })} />
        <small>{options.writer ? `${options.writer} writes the storyboards.` : "No chat model is downloaded to write storyboards."}</small></label>
      <p className="small faint">A change here makes the pictures made before it "before a change": make new ones, or keep
        them as they are.</p>
      <ThemeSection series={series} act={act} />
    </div>
  );
}

/** The series' theme: its words and length, the versions made, the chosen one under every episode. */
function ThemeSection({ series, act }: { series: Series; act: Act }) {
  const th = series.theme;
  const base = `/hub/api/movies/${series.id}`;
  const save = (theme: Record<string, unknown>) => act(() => api(base, { method: "PATCH", body: { theme } }));
  return (
    <div className="stack mv-theme">
      <h3>Theme music</h3>
      <p className="small faint">Plays under every episode, quieter while someone speaks.
        {series.audio.music ? ` ${series.audio.music} makes it.` : " No music model is downloaded (Settings, Models)."}</p>
      <div className="mv-shot-row">
        <label className="field grow"><span>What it sounds like</span>
          <Draft multiline rows={2} value={th.prompt} maxLength={600} onSave={(v) => save({ prompt: v })} /></label>
        <label className="field mv-seconds"><span>Seconds</span>
          <NumberDraft value={th.seconds} min={10} max={180} step={1} onSave={(v) => save({ seconds: v })} /></label>
      </div>
      {th.state === "stale" && <Stale onKeep={() => act(() => api(`${base}/theme/pick`, { body: { keep: true } }))} />}
      <div className="row">
        <button className="btn small" disabled={!!series.jobs.theme || !series.audio.music}
          onClick={() => act(() => api(`${base}/theme/make`, { method: "POST" }))}>
          <Music size={14} /> {th.variants.length ? "Another version" : "Make the theme"}</button>
      </div>
      <JobLine job={series.jobs.theme} error={series.errors.theme}
        onStop={() => act(() => api(`${base}/jobs/cancel`, { body: { key: "theme" } }))} />
      {th.variants.length > 0 && (
        <div className="list">
          {th.variants.map((v, i) => (
            <div key={v.file} className="list-row mv-version">
              <PlayButton src={movieFile(series.id, v.file)} label={`version ${th.variants.length - i}`} />
              <span className="grow small">Version {th.variants.length - i} · {v.seconds} s{v.bpm ? ` · ${v.bpm} bpm` : ""}</span>
              {v.file === th.file ? <span className="tag ok">chosen</span>
                : <button className="btn small quiet" onClick={() => act(() => api(`${base}/theme/pick`, { body: { file: v.file } }))}>Choose</button>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- an episode

function Stage({ n, title, done, detail, children }: { n: number; title: string; done: boolean; detail: string;
  children: ReactNode }) {
  return (
    <div className={`mv-stage${done ? " done" : ""}`}>
      <div className="mv-stage-head"><span className="mv-stage-n">{done ? <Check size={13} /> : n}</span><b>{title}</b></div>
      <div className="small faint">{detail}</div>
      <div className="row mv-stage-actions">{children}</div>
    </div>
  );
}

function EpisodePage({ series, eid, act }: { series: Series; eid: string; act: Act }) {
  const e = series.episodes.find((x) => x.id === eid);
  if (!e) return <Empty>There is no such episode.</Empty>;
  const sid = series.id;
  const base = `/hub/api/movies/${sid}/episodes/${eid}`;
  const p = e.progress;
  const key = (what: string) => `${what}:${eid}`;
  const job = (what: string) => series.jobs[key(what)];
  const stop = (what: string) => act(() => api(`/hub/api/movies/${sid}/jobs/cancel`, { body: { key: key(what) } }));
  const patch = (body: Record<string, unknown>) => act(() => api(base, { method: "PATCH", body }));
  const over = p.seconds > series.style.max_seconds;
  const writing = !!job("storyboard");
  const audio = series.audio;
  const soundDone = p.spoken === p.lines && p.effects_made === p.effects && (!e.cut.music || series.theme.state === "fresh");
  const staleLines = e.shots.some((sh) => sh.lines.some((ln) => ln.state === "stale"));
  const staleEffects = e.shots.some((sh) => sh.sfx_state === "stale");
  const keep = (what: "lines" | "effects") => act(() => api(`${base}/keep`, { body: { what } }));
  return (
    <>
      <div className="row mv-head">
        <Draft className="input mv-title-input" value={e.title} maxLength={80}
          onSave={(v) => v.trim() && patch({ title: v })} />
        <ConfirmButton className="btn quiet small" confirmText="Delete the episode with its pictures and clips?"
          onConfirm={async () => {
            if (await act(() => api(base, { method: "DELETE" }))) navigate(`/movies/${sid}`);
          }}><Trash2 size={14} /> Delete</ConfirmButton>
      </div>
      <label className="field"><span>What happens</span>
        <Draft multiline rows={3} value={e.premise} maxLength={1500} onSave={(v) => patch({ premise: v })} /></label>
      <div className="mv-stages">
        <Stage n={1} title="Storyboard" done={p.approved}
          detail={p.shots ? `${p.shots} shot${p.shots === 1 ? "" : "s"}, ${p.seconds} s${over ? ` - over ${series.style.max_seconds} s` : ""}` : "Not written yet"}>
          {!e.shots.length
            ? <button className="btn small primary" disabled={!!job("storyboard") || !e.premise.trim()}
              onClick={() => act(() => api(`${base}/storyboard`, { body: {} }))}><WandSparkles size={14} /> Write it</button>
            : <ConfirmButton className="btn small" disabled={!!job("storyboard")}
              confirmText="Replace the shots?"
              onConfirm={() => act(() => api(`${base}/storyboard`, { body: { replace: true } }))}>
              <RefreshCw size={14} /> Write it again</ConfirmButton>}
          {e.shots.length > 0 && (p.approved
            ? <button className="btn small quiet" onClick={() => patch({ approved: false })}>Unapprove</button>
            : <button className="btn small primary" onClick={() => patch({ approved: true })}><Check size={14} /> Approve</button>)}
        </Stage>
        <Stage n={2} title="Keyframes" done={p.shots > 0 && p.keyframes === p.shots}
          detail={`${p.keyframes} of ${p.shots} chosen`}>
          <button className="btn small" disabled={!p.approved || writing || p.keyframes === p.shots || !!job("keyframes")}
            title={p.approved ? undefined : "Approve the storyboard first"}
            onClick={() => act(() => api(`${base}/keyframes`, { body: {} }))}><WandSparkles size={14} /> Make the missing</button>
        </Stage>
        <Stage n={3} title="Clips" done={p.shots > 0 && p.kept === p.shots} detail={`${p.kept} of ${p.shots} kept`}>
          <button className="btn small" disabled={!p.keyframes || writing || p.clips >= p.keyframes || !!job("clips")}
            title={p.keyframes ? undefined : "Choose keyframes first"}
            onClick={() => act(() => api(`${base}/clips`, { body: {} }))}><Clapperboard size={14} /> Make the missing</button>
        </Stage>
        <Stage n={4} title="Preview" done={p.preview === "fresh"}
          detail={p.preview === "none" ? "The clips joined, no sound" : p.preview === "stale" ? "Made before a change" : `${e.preview?.seconds} s, no sound`}>
          <button className="btn small" disabled={!e.shots.some((s) => s.clip) || writing || !!job("preview")}
            onClick={() => act(() => api(`${base}/preview`, { method: "POST" }))}><Clapperboard size={14} /> Join the clips</button>
        </Stage>
        <Stage n={5} title="Sound" done={p.shots > 0 && soundDone}
          detail={!audio.speech && p.lines ? "No speech model is downloaded"
            : `Lines ${p.spoken} of ${p.lines} spoken · effects ${p.effects_made} of ${p.effects} · `
              + (series.theme.file ? (series.theme.state === "stale" ? "the theme is from before a change" : "theme chosen") : "no theme yet")}>
          <button className="btn small" disabled={!p.approved || writing || !audio.speech || p.spoken === p.lines || !!job("voices")}
            title={p.approved ? undefined : "Approve the storyboard first"}
            onClick={() => act(() => api(`${base}/voices`, { body: {} }))}><Mic size={14} /> Speak the lines</button>
          <button className="btn small" disabled={!p.approved || writing || !audio.sound || p.effects_made === p.effects || !!job("sounds")}
            title={p.approved ? undefined : "Approve the storyboard first"}
            onClick={() => act(() => api(`${base}/sounds`, { body: {} }))}><WandSparkles size={14} /> Make the effects</button>
          {!series.theme.file && <button className="btn small" disabled={!audio.music || !!series.jobs.theme}
            onClick={() => act(() => api(`/hub/api/movies/${sid}/theme/make`, { method: "POST" }))}><Music size={14} /> Make the theme</button>}
          {staleLines && <button type="button" className="link-btn small" onClick={() => keep("lines")}
            title="Lines spoken before a change of voice or speech model: keep them as they are">Keep the lines as they are</button>}
          {staleEffects && <button type="button" className="link-btn small" onClick={() => keep("effects")}
            title="Effects made before a change: keep them as they are">Keep the effects as they are</button>}
        </Stage>
        <Stage n={6} title="Cut" done={p.final === "fresh"}
          detail={p.final === "fresh" ? `${e.final?.seconds.toFixed(1)} s with sound` : p.final === "stale" ? `Made before a change: ${p.final_why}`
            : p.cut_missing.length ? `First: ${p.cut_missing.join(", ")}` : `${p.total.toFixed(1)} s with sound - ready`}>
          <button className="btn small primary" disabled={!p.shots || p.cut_missing.length > 0 || writing || !!job("cut")}
            onClick={() => act(() => api(`${base}/cut`, { method: "POST" }))}><Scissors size={14} /> {e.final ? "Cut it again" : "Cut it"}</button>
        </Stage>
      </div>
      {(["storyboard", "keyframes", "clips", "preview", "voices", "sounds", "cut"] as const).map((w) =>
        <JobLine key={w} job={job(w)} error={series.errors[key(w)]} onStop={() => stop(w)} />)}
      <JobLine job={series.jobs.theme} error={series.errors.theme}
        onStop={() => act(() => api(`/hub/api/movies/${sid}/jobs/cancel`, { body: { key: "theme" } }))} />
      {e.notes.length > 0 && <div className="callout small">{e.notes.map((n) => <div key={n}>{cap(n)}.</div>)}</div>}
      {e.final && (
        <div className="stack mv-final">
          <video className="mv-preview" src={movieFile(sid, e.final.file)} controls playsInline preload="metadata" />
          <div className="row">
            <a className="btn small" href={movieFile(sid, e.final.file)} download={`${e.title}.mp4`}><Download size={14} /> Download</a>
            <StateTag state={p.final} fresh="up to date" />
          </div>
        </div>
      )}
      {e.shots.length > 0 && <CutPanel e={e} onChange={(cut) => patch({ cut })} />}
      {e.preview && (e.final ? (
        <details className="mv-cut">
          <summary className="small"><b>The preview</b> - the clips joined, no sound</summary>
          <video className="mv-preview" src={movieFile(sid, e.preview.file)} controls playsInline preload="none" />
        </details>
      ) : <video className="mv-preview" src={movieFile(sid, e.preview.file)} controls playsInline preload="metadata" />)}
      {e.shots.map((sh, i) => <ShotCard key={sh.id} series={series} e={e} sh={sh} i={i} act={act} />)}
      <div className="row">
        <button className="btn small" onClick={() => act(() => api(`${base}/shots`, { body: { after: e.shots[e.shots.length - 1]?.id } }))}>
          <Plus size={14} /> Add a shot</button>
      </div>
    </>
  );
}

/** How the episode is cut: the text on screen, the end card, the theme under it, the volumes. */
function CutPanel({ e, onChange }: { e: Episode; onChange: (cut: Partial<CutSettings>) => void }) {
  const c = e.cut;
  return (
    <details className="mv-cut">
      <summary className="small"><b>How it is cut</b> - {c.text === "none" ? "no text" : c.text}
        {c.end_card ? ", an end card" : ""}{c.music ? ", the theme under it" : ", no theme"}</summary>
      <div className="form-grid">
        <label className="field"><span>On screen while a line is heard</span>
          <select className="select" value={c.text} onChange={(ev) => onChange({ text: ev.target.value as CutSettings["text"] })}>
            <option value="bubbles">Speech bubbles</option>
            <option value="captions">Captions at the bottom</option>
            <option value="none">Nothing</option>
          </select></label>
        <label className="field"><span>Speech volume</span>
          <NumberDraft value={c.speech_volume} min={0} max={2} step={0.05} onSave={(v) => onChange({ speech_volume: v })} /></label>
        <label className="field"><span>Effects volume</span>
          <NumberDraft value={c.effects_volume} min={0} max={2} step={0.05} onSave={(v) => onChange({ effects_volume: v })} /></label>
        <label className="field"><span>Theme volume</span>
          <NumberDraft value={c.music_volume} min={0} max={2} step={0.05} onSave={(v) => onChange({ music_volume: v })} /></label>
      </div>
      <div className="row mv-checks">
        <label className="check"><input type="checkbox" checked={c.music} onChange={(ev) => onChange({ music: ev.target.checked })} />
          The theme under it</label>
        <label className="check"><input type="checkbox" checked={c.end_card} onChange={(ev) => onChange({ end_card: ev.target.checked })} />
          An end card with the title</label>
      </div>
    </details>
  );
}

function ShotCard({ series, e, sh, i, act }: { series: Series; e: Episode; sh: Shot; i: number; act: Act }) {
  const sid = series.id;
  const writing = !!series.jobs[`storyboard:${e.id}`];
  const base = `/hub/api/movies/${sid}/episodes/${e.id}`;
  const sbase = `${base}/shots/${sh.id}`;
  const patch = (body: Record<string, unknown>) => act(() => api(sbase, { method: "PATCH", body }));
  const frames = series.jobs[`keyframes:${e.id}`];
  const clips = series.jobs[`clips:${e.id}`];
  const making = frames?.shot === sh.id;
  const framesWait = !!frames?.queued.includes(sh.id);
  const clipping = clips?.shot === sh.id;
  const clipWait = !!clips?.queued.includes(sh.id);
  const voices = series.jobs[`voices:${e.id}`];
  const sounds = series.jobs[`sounds:${e.id}`];
  const speaking = voices?.shot === sh.id;
  const speakWait = queuedFor(voices, sh.id);
  const sounding = sounds?.shot === sh.id;
  const soundWait = queuedFor(sounds, sh.id);
  const notes = sh.variants[0]?.notes ?? [];
  const stop = (what: string) =>
    act(() => api(`/hub/api/movies/${sid}/jobs/cancel`, { body: { key: `${what}:${e.id}` } }));
  return (
    <div className="mv-shot">
      <div className="mv-shot-head row">
        <b>Shot {i + 1}</b>
        <span className="faint small">{sh.seconds} s</span>
        <span className="grow" />
        <button className="icon-btn small" disabled={i === 0} onClick={() => patch({ move: -1 })} aria-label="Move it earlier"><ArrowUp size={14} /></button>
        <button className="icon-btn small" disabled={i === e.shots.length - 1} onClick={() => patch({ move: 1 })} aria-label="Move it later"><ArrowDown size={14} /></button>
        <ConfirmButton className="btn quiet small" confirmText="Delete the shot?" label="Delete the shot"
          disabled={making || clipping || speaking || sounding}
          onConfirm={() => act(() => api(sbase, { method: "DELETE" }))}><Trash2 size={14} /></ConfirmButton>
      </div>
      <div className="mv-shot-body">
        <div className="stack mv-shot-words">
          <div className="mv-shot-row">
            <label className="field mv-seconds"><span>Seconds</span>
              <NumberDraft value={sh.seconds} min={1} max={10} step={0.5} onSave={(v) => patch({ seconds: v })} /></label>
            <label className="field grow"><span>Set</span>
              <select className="select" value={sh.set} onChange={(ev) => patch({ set: ev.target.value })}>
                <option value="">None</option>
                {series.sets.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
              </select></label>
            <label className="field grow"><span>Keyframe from</span>
              <select className="select" value={sh.method} onChange={(ev) => patch({ method: ev.target.value })}>
                <option value="auto">Auto ({sh.characters.length >= 2 ? "combined" : "a new picture"})</option>
                <option value="new">A new picture</option>
                <option value="combine">The set and the sheets combined</option>
              </select></label>
          </div>
          <div className="field"><span>Who is in it</span>
            <div className="mv-chips">
              {series.cast.map((c) => {
                const on = sh.characters.includes(c.id);
                return <button key={c.id} type="button" className={`mv-chip${on ? " on" : ""}`} aria-pressed={on}
                  onClick={() => patch({ toggle_character: c.id })}>{c.name}</button>;
              })}
              {!series.cast.length && <span className="small faint">No cast yet</span>}
            </div>
          </div>
          <label className="field"><span>Picture - the first frame</span>
            <Draft multiline rows={3} value={sh.picture} maxLength={1500} onSave={(v) => patch({ picture: v })}
              placeholder="Who is where, doing what; close-up, medium or wide; the light" /></label>
          <label className="field"><span>Motion</span>
            <Draft multiline rows={2} value={sh.motion} maxLength={1500} onSave={(v) => patch({ motion: v })}
              placeholder="What moves, and how the camera moves - small and simple" /></label>
          <label className="field"><span>Sound</span>
            <Draft value={sh.sound} maxLength={1500} onSave={(v) => patch({ sound: v })}
              placeholder="What is heard: a door creaks, a cat meows - the theme plays by itself" /></label>
          <label className="field"><span>Lines</span>
            <Draft multiline rows={2} value={linesToText(sh.lines)} onSave={(v) => patch({ lines: textToLines(v) })}
              placeholder="Name: what is said - one a line; empty when nobody speaks" /></label>
        </div>
        <div className="stack mv-shot-media">
          <div className="mv-media">
            <div className="row small"><b>Keyframe</b><span className="grow" /><StateTag state={sh.keyframe_state} /></div>
            {sh.keyframe
              ? <a href={movieFile(sid, sh.keyframe)} target="_blank" rel="noreferrer"><img className="mv-keyframe" src={movieFile(sid, sh.keyframe)} alt={`Shot ${i + 1}`} /></a>
              : <div className="mv-pic-empty">{making ? "Making the pictures" : sh.variants.length ? "Choose one below" : "None yet"}</div>}
            {sh.keyframe_state === "stale" && <Stale onKeep={() => act(() => api(`${sbase}/pick`, { body: { keep: true } }))} />}
            {sh.method_wanted === "combine" && sh.missing.length > 0 && <div className="small faint">Combined once these
              have a picture: {sh.missing.join(", ")}. Until then a new picture from the words.</div>}
            {sh.method_wanted === "combine" && sh.method_used === "new" && !sh.missing.length && <div className="small faint">
              The series' picture model cannot combine pictures: a new picture from the words.</div>}
            {sh.keyframe_error && <div className="small warn-text">{sh.keyframe_error}</div>}
            {notes.map((n) => <div key={n} className="small warn-text">{cap(n)}.</div>)}
            <div className="row">
              <button className="btn small" disabled={!e.approved || writing || making || framesWait}
                title={e.approved ? undefined : "Approve the storyboard first"}
                onClick={() => act(() => api(`${base}/keyframes`, { body: { shots: [sh.id] } }))}>
                <WandSparkles size={14} /> {sh.variants.length ? "More pictures" : "Make pictures"}</button>
              {framesWait && <span className="small faint">Waiting its turn</span>}
            </div>
            {making && <JobLine job={frames} onStop={() => stop("keyframes")} />}
            {sh.variants.length > 0 && <Pictures sid={sid} variants={sh.variants} chosen={sh.keyframe}
              onPick={(file) => act(() => api(`${sbase}/pick`, { body: { file } }))} />}
          </div>
          <div className="mv-media">
            <div className="row small"><b>Clip</b><span className="grow" />
              <StateTag state={sh.clip_state} kept={sh.clip ? sh.clip.kept : undefined} /></div>
            {sh.clip
              ? <video className="mv-clip" src={movieFile(sid, sh.clip.file)} controls playsInline preload="metadata" loop />
              : <div className="mv-pic-empty">{clipping ? "Making the clip" : "None yet"}</div>}
            {sh.clip_error && <div className="small warn-text">{sh.clip_error}</div>}
            {sh.clip && sh.clip_state === "stale" && (sh.keyframe_state === "fresh"
              ? <Stale onKeep={() => act(() => api(`${sbase}/clip`, { body: { accept: true } }))} />
              : <div className="small faint">Its keyframe comes first: choose a new one, or keep it as it is.</div>)}
            <div className="row">
              {sh.clip && sh.clip_state === "fresh" && (sh.clip.kept
                ? <button className="btn small" onClick={() => act(() => api(`${sbase}/clip`, { body: { kept: false } }))}><Check size={14} /> Kept</button>
                : <button className="btn small primary" onClick={() => act(() => api(`${sbase}/clip`, { body: { kept: true } }))}>Keep</button>)}
              <button className="btn small" disabled={sh.keyframe_state !== "fresh" || writing || clipping || clipWait}
                title={sh.keyframe_state === "fresh" ? undefined : "Choose its keyframe first"}
                onClick={() => act(() => api(`${base}/clips`, { body: { shots: [sh.id] } }))}>
                <Clapperboard size={14} /> {sh.clip ? "Again" : "Make the clip"}</button>
              {clipWait && <span className="small faint">Waiting its turn</span>}
            </div>
            {clipping && <JobLine job={clips} onStop={() => stop("clips")} />}
            {sh.takes.length > 0 && <div className="small faint mv-takes">Earlier takes:{" "}
              {sh.takes.map((t, k) => <span key={t.file}>{k > 0 && ", "}<button type="button" className="link-btn"
                onClick={() => act(() => api(`${sbase}/clip`, { body: { take: t.file } }))}>{k === 0 ? "the one before" : `${k + 1} before`}</button></span>)}</div>}
          </div>
          <div className="mv-media">
            <div className="row small"><b>Sound</b></div>
            {sh.sfx_state === "unwanted" ? <div className="small faint">No effect: nothing written under Sound.</div> : (
              <div className="row mv-sound-row">
                {sh.sfx ? <PlayButton src={movieFile(sid, sh.sfx.file)} label={`shot ${i + 1}'s effect`} />
                  : <span className="small faint">{sounding ? "Making the effect" : "No effect yet"}</span>}
                <span className="grow small ellipsis">{sh.sfx ? `The effect, ${sh.sfx.seconds} s` : ""}</span>
                <StateTag state={sh.sfx_state} fresh="made" />
                {sh.sfx_state === "stale" && <button type="button" className="link-btn small"
                  onClick={() => act(() => api(`${base}/keep`, { body: { what: "effects", shot: sh.id } }))}>Keep</button>}
                <button className="btn small" disabled={!e.approved || writing || sounding || soundWait || !series.audio.sound}
                  title={series.audio.sound ? undefined : "No sound-effect model is downloaded"}
                  onClick={() => act(() => api(`${base}/sounds`, { body: { shots: [sh.id] } }))}>
                  <WandSparkles size={14} /> {sh.sfx ? "Again" : "Make it"}</button>
              </div>)}
            {sh.sfx_error && <div className="small warn-text">{sh.sfx_error}</div>}
            {sounding && <JobLine job={sounds} onStop={() => stop("sounds")} />}
            {sh.lines.map((ln, k) => (
              <div key={ln.id || k} className="mv-line">
                <div className="small"><b>{ln.name}:</b> {ln.text}</div>
                <div className="row mv-line-tools">
                  {ln.audio ? <PlayButton src={movieFile(sid, ln.audio.file)} label={`${ln.name}'s line`} />
                    : <Mic size={14} className="faint" aria-hidden />}
                  <span className="small faint">{ln.audio ? `${ln.audio.seconds} s` : "not spoken"}</span>
                  {ln.state === "stale" && <><span className="tag mv-warn">before a change</span>
                    <button type="button" className="link-btn small" title="Keep the take as it is"
                      onClick={() => act(() => api(`${base}/keep`, { body: { what: "lines", shot: sh.id, line: ln.id } }))}>Keep</button></>}
                  <span className="grow" />
                  <label className="mv-at small faint" title="Where it starts in the shot - by itself right after the line before">at
                    <NumberDraft value={ln.at ?? ln.start} min={0} max={sh.seconds_used} step={0.1}
                      onSave={(v) => act(() => api(`${sbase}/line`, { body: { line: ln.id, at: v } }))} /> s</label>
                  {ln.at !== null && <button type="button" className="link-btn small" title="Right after the line before"
                    onClick={() => act(() => api(`${sbase}/line`, { body: { line: ln.id, at: null } }))}>by itself</button>}
                  <button className="btn small quiet" disabled={!e.approved || writing || !series.audio.speech || speaking || speakWait}
                    onClick={() => act(() => api(`${base}/voices`, { body: { shots: [sh.id], line: ln.id } }))}>
                    {ln.audio ? "Again" : "Speak"}</button>
                </div>
                {ln.over > 0 && <div className="small warn-text">Runs {ln.over} s past the end of the shot.</div>}
                {ln.error && <div className="small warn-text">{ln.error}</div>}
              </div>
            ))}
            {speaking && <JobLine job={voices} onStop={() => stop("voices")} />}
            {speakWait && !speaking && <div className="small faint">Its lines are waiting their turn</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
