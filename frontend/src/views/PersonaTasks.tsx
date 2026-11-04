// A persona's tasks and posts (0.75.0, Søren: "At 7PM every monday, prepare a social media post with a picture or
// video ... and then list the posts for me to review"): at its days and time a task makes a post - its text in the
// persona's voice and a picture or video - for the owner's review. Nothing is published without the owner: 0.76.0
// posts an approved one to the persona's Instagram account when it is connected ("Approve and post").
import { useCallback, useEffect, useState } from "react";
import { Check, Copy, Download, ExternalLink, MessageSquare, Pencil, Play, Plus, RotateCcw, Send,
  Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import { copyText } from "../lib/clipboard";
import { DAYS, personaFile, scheduleText, whenText, type InstagramApp, type Persona, type PersonaLora,
  type PersonaPost, type PersonaTask, type TaskLora, type TaskMedia } from "../lib/personas";
import { linkProps } from "../lib/router";
import { useApp } from "../state";
import { Avatar } from "../components/Avatar";
import { ConfirmButton, Empty, Lamp, Switch } from "../components/ui";

type Act = (call: () => Promise<unknown>, done?: string) => Promise<unknown>;

const MEDIA: { id: TaskMedia; label: string }[] = [
  { id: "picture", label: "A picture" }, { id: "video", label: "A video" },
  { id: "either", label: "A picture or a video" }, { id: "none", label: "Text only" }];
const mediaLabel = (m: TaskMedia) => MEDIA.find((x) => x.id === m)?.label ?? m;

type TaskDraft = Pick<PersonaTask, "title" | "instruction" | "days" | "time" | "media" | "web" | "loras">;
const NEW_TASK: TaskDraft = { title: "", instruction: "", days: [0], time: "19:00", media: "picture", web: false,
  loras: [] };
const TASK_LORAS = 3;

/** The LoRAs a task can add (0.79.0): not the persona's own, and of its kind when it has one (a klein 9B LoRA for
 *  pictures of her made with klein 9B) - one of another kind would be left out at the picture. */
function taskLoraChoices(p: Persona, loras: PersonaLora[]): PersonaLora[] {
  const own = loras.find((l) => l.id === p.lora);
  return loras.filter((l) => l.id !== p.lora && (!own || l.base === own.base));
}

// ---------------------------------------------------------------- tasks

export function TasksTab({ p, act, loras }: { p: Persona; act: Act; loras: PersonaLora[] }) {
  const [editing, setEditing] = useState<string | null>(null);        // a task's id, or "new"
  const base = `/hub/api/personas/${p.id}/tasks`;
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>At its days and time {p.name} does the task alone: writes the
        post in {p.name}'s voice, makes the picture or video, and hands it in for you to review under Posts. Nothing
        is published.</p>
      {p.tasks.length === 0 && editing !== "new" && <Empty>No tasks yet.</Empty>}
      {p.tasks.map((t) => (editing === t.id
        ? <TaskForm key={t.id} p={p} loras={loras} initial={t} onCancel={() => setEditing(null)} onSave={async (d) => {
          if (await act(() => api(`${base}/${t.id}`, { method: "PATCH", body: d }), "Saved")) setEditing(null);
        }} />
        : <TaskRow key={t.id} p={p} t={t} act={act} loras={loras} onEdit={() => setEditing(t.id)} />))}
      {editing === "new"
        ? <TaskForm p={p} loras={loras} initial={NEW_TASK} onCancel={() => setEditing(null)} onSave={async (d) => {
          if (await act(() => api(base, { body: d }), "The task is set")) setEditing(null);
        }} />
        : <div className="row"><button className="btn" onClick={() => setEditing("new")}><Plus size={15} /> New task</button></div>}
    </div>
  );
}

function TaskRow({ p, t, act, loras, onEdit }: { p: Persona; t: PersonaTask; act: Act; loras: PersonaLora[];
  onEdit: () => void }) {
  const base = `/hub/api/personas/${p.id}/tasks/${t.id}`;
  return (
    <div className="pe-task">
      <div className="row pe-task-head">
        <Switch checked={t.enabled} label={t.enabled ? "On" : "Off"}
          onChange={(v) => act(() => api(base, { method: "PATCH", body: { enabled: v } }))} />
        <b className="grow ellipsis">{t.title}</b>
      </div>
      <div className="small pe-task-text">{t.instruction}</div>
      <div className="row small pe-task-meta">
        <span className="tag">{scheduleText(t)}</span>
        {t.running ? <span className="row" style={{ gap: 6 }}><Lamp state="run" size="sm" pulse /> Making a post now</span>
          : <span className="faint">{t.enabled && t.next_run ? `Next: ${whenText(t.next_run)}` : "Off"}</span>}
        <span className="faint">{mediaLabel(t.media)}{t.web ? " · looks things up" : ""}</span>
        {(t.loras ?? []).map((l) => {
          const meta = loras.find((x) => x.id === l.id);
          return <span key={l.id} className="tag" title="A LoRA of this task's own, on its pictures">
            {meta?.name ?? `${l.id} (gone)`} ×{l.strength ?? meta?.strength ?? 1}</span>;
        })}
        {t.last_error && !t.running && <span className="warn-text">{t.last_error}</span>}
      </div>
      <div className="row pe-task-acts">
        <button className="btn small" disabled={t.running}
          onClick={() => act(() => api(`${base}/run`, { method: "POST" }), "A post is being made - it comes under Posts")}>
          <Play size={13} /> Make one now</button>
        <button className="btn quiet small" onClick={onEdit}><Pencil size={13} /> Change</button>
        {t.chat_id && <a className="btn quiet small" {...linkProps({ name: "chat", id: t.chat_id })}
          title="How the posts were made"><MessageSquare size={13} /> Its chat</a>}
        <span className="grow" />
        <ConfirmButton className="btn quiet small" confirmText={`Delete "${t.title}"? Its posts stay.`}
          disabled={t.running} onConfirm={() => act(() => api(base, { method: "DELETE" }))}>
          <Trash2 size={13} /> Delete</ConfirmButton>
      </div>
    </div>
  );
}

function TaskForm({ p, loras, initial, onSave, onCancel }: { p: Persona; loras: PersonaLora[]; initial: TaskDraft;
  onSave: (d: TaskDraft) => unknown; onCancel: () => void }) {
  const [d, setD] = useState<TaskDraft>({ title: initial.title, instruction: initial.instruction,
    days: [...initial.days], time: initial.time, media: initial.media, web: initial.web,
    loras: (initial.loras ?? []).map((l) => ({ ...l })) });
  const set = (change: Partial<TaskDraft>) => setD((x) => ({ ...x, ...change }));
  const toggle = (day: number) => set({ days: d.days.includes(day) ? d.days.filter((x) => x !== day)
    : [...d.days, day].sort() });
  const ready = d.title.trim() && d.instruction.trim() && d.days.length > 0 && /^\d\d:\d\d$/.test(d.time);
  return (
    <div className="pe-task editing stack">
      <label className="field"><span>Title</span>
        <input className="input" value={d.title} maxLength={80} placeholder="Monday post"
          onChange={(e) => set({ title: e.target.value })} /></label>
      <label className="field"><span>What to do</span>
        <textarea className="textarea" rows={4} value={d.instruction} maxLength={2000}
          placeholder="A social media post: a comment on this week's news in your own opinion, or how your training went, or your breakfast with a quote. A picture of you, or of what you write about. Short, hashtags at the end."
          onChange={(e) => set({ instruction: e.target.value })} />
        <small>In your words - the persona decides the rest in character.</small></label>
      <div className="field"><span>Days</span>
        <div className="row pe-days" role="group" aria-label="Days">
          {DAYS.map((name, i) => (
            <button key={name} type="button" className={`pe-day${d.days.includes(i) ? " on" : ""}`}
              aria-pressed={d.days.includes(i)} onClick={() => toggle(i)}>{name}</button>
          ))}
        </div></div>
      <div className="mv-shot-row">
        <label className="field"><span>At</span>
          <input className="input" type="time" value={d.time} onChange={(e) => set({ time: e.target.value })} /></label>
        <label className="field grow"><span>The post has</span>
          <select className="select" value={d.media} onChange={(e) => set({ media: e.target.value as TaskMedia })}>
            {MEDIA.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select></label>
      </div>
      <div className="row" style={{ gap: 10 }}>
        <Switch checked={d.web} onChange={(v) => set({ web: v })} label="Looks things up" />
        <span className="small">Looks things up on the web - for the news, a date, a place</span>
      </div>
      {d.media !== "none" && <TaskLoras p={p} loras={loras} value={d.loras} onChange={(v) => set({ loras: v })} />}
      <div className="row">
        <span className="grow" />
        <button className="btn quiet small" onClick={onCancel}>Cancel</button>
        <button className="btn primary small" disabled={!ready} onClick={() => onSave(d)}>Save</button>
      </div>
    </div>
  );
}

/** A task's own LoRAs (0.79.0, Søren: "can we add a second lora to the tasks ? If i want to add filter or something
 *  like that"): up to three, each with its strength, on every new picture the task makes - after the persona's. */
function TaskLoras({ p, loras, value, onChange }: { p: Persona; loras: PersonaLora[]; value: TaskLora[];
  onChange: (v: TaskLora[]) => void }) {
  const choices = taskLoraChoices(p, loras);
  const meta = (id: string) => loras.find((l) => l.id === id);
  const free = choices.filter((c) => !value.some((v) => v.id === c.id));
  const change = (i: number, x: Partial<TaskLora>) => onChange(value.map((v, j) => (j === i ? { ...v, ...x } : v)));
  return (
    <div className="field"><span>LoRAs on its pictures</span>
      {value.map((v, i) => {
        const m = meta(v.id);
        const [lo, hi] = m?.slider ? [-10, 10] : [0, 2];
        return (
          <div key={i} className="row" style={{ gap: 8, marginBottom: 6 }}>
            <select className="select grow" value={v.id} aria-label="LoRA"
              onChange={(e) => change(i, { id: e.target.value, strength: meta(e.target.value)?.strength ?? 1 })}>
              {!m && <option value={v.id}>{v.id} - no longer there</option>}
              {choices.filter((c) => c.id === v.id || !value.some((x) => x.id === c.id))
                .map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <input className="input" type="number" step={0.05} min={lo} max={hi} style={{ width: 84 }}
              aria-label="Strength" value={v.strength ?? m?.strength ?? 1}
              onChange={(e) => change(i, { strength: e.target.value === "" ? null : Number(e.target.value) })} />
            <button type="button" className="icon-btn" aria-label="Remove the LoRA"
              onClick={() => onChange(value.filter((_, j) => j !== i))}><X size={15} /></button>
          </div>
        );
      })}
      {value.length < TASK_LORAS && free.length > 0 && (
        <div className="row"><button type="button" className="btn quiet small"
          onClick={() => onChange([...value, { id: free[0].id, strength: free[0].strength ?? 1 }])}>
          <Plus size={13} /> Add a LoRA</button></div>)}
      <small>{choices.length === 0
        ? `No other LoRA ${p.lora ? `of the same kind as ${p.name}'s ` : ""}is there yet (Settings, Models, LoRAs).`
        : `A style or a filter on every new picture this task makes, after ${p.name}'s own LoRA. Strength 1 is the LoRA as trained.`}</small>
    </div>
  );
}

// ---------------------------------------------------------------- posts

/** Posts from the Hub: one persona's, or the drafts of all of them - reloaded every 15 s while one is being made,
 *  every 3 s while one is on its way to Instagram. */
export function usePosts(url: string, live: boolean) {
  const { toast } = useApp();
  const [posts, setPosts] = useState<PersonaPost[] | null>(null);
  const load = useCallback(async () => {
    try {
      setPosts((await api<{ posts: PersonaPost[] }>(url)).posts);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  }, [url, toast]);
  useEffect(() => {
    load();
  }, [load]);
  const posting = (posts ?? []).some((s) => s.instagram?.state === "posting");
  useEffect(() => {
    if (!live && !posting) return;
    const t = window.setInterval(load, posting ? 3000 : 15000);
    return () => window.clearInterval(t);
  }, [live, posting, load]);
  return { posts, load, setPosts };
}

export function PostsTab({ p, reload }: { p: Persona; reload: () => Promise<void> }) {
  const running = p.tasks.some((t) => t.running);
  const { posts, load } = usePosts(`/hub/api/personas/${p.id}/posts`, running);
  useEffect(() => {                                     // a post handed in: here at once
    if (!running) load();
  }, [running, load]);
  if (!posts) return <div className="faint">Loading</div>;
  return (
    <div className="stack">
      <InstagramBox p={p} onChange={async () => { await reload(); await load(); }} />
      {running && <div className="row small" style={{ gap: 6 }}><Lamp state="run" size="sm" pulse />
        {p.name} is making a post</div>}
      {posts.length === 0 ? <Empty>No posts yet - give {p.name} a task (Tasks).</Empty>
        : <div className="pe-posts">{posts.map((s) => <PostCard key={s.id} post={s} onChange={load} />)}</div>}
    </div>
  );
}

/** 0.76.0: where the persona's approved posts can go - the owner's Meta app (once for all personas), the persona's
 *  account signed in at Instagram. */
function InstagramBox({ p, onChange }: { p: Persona; onChange: () => Promise<void> }) {
  const { toast } = useApp();
  const [app, setApp] = useState<InstagramApp | null>(null);
  const [editing, setEditing] = useState(false);
  const [appId, setAppId] = useState("");
  const [secret, setSecret] = useState("");
  const [config, setConfig] = useState("");
  useEffect(() => {
    api<InstagramApp>("/hub/api/personas/instagram").then((a) => {
      setApp(a);
      setAppId(a.app_id);
      setConfig(a.config_id);
    }).catch((e) => toast((e as Error).message, "fault"));
  }, [toast]);
  if (!app) return null;
  const ig = p.instagram;
  const fail = (e: unknown) => toast((e as Error).message, "fault");
  const saveApp = async () => {
    try {
      const a = await api<InstagramApp>("/hub/api/personas/instagram", { method: "PUT",
        body: { app_id: appId.trim(), app_secret: secret.trim(), config_id: config.trim() } });
      setApp(a);
      setSecret("");
      setEditing(false);
      toast("Saved", "run");
    } catch (e) {
      fail(e);
    }
  };
  const connect = async () => {
    try {                                               // to Instagram's sign-in, and back here
      window.location.href = (await api<{ url: string }>(`/hub/api/personas/${p.id}/instagram/connect`,
        { method: "POST" })).url;
    } catch (e) {
      fail(e);
    }
  };
  const disconnect = async () => {
    try {
      await api(`/hub/api/personas/${p.id}/instagram`, { method: "DELETE" });
      await onChange();
    } catch (e) {
      fail(e);
    }
  };
  const noAddress = !app.public_url.startsWith("https://");
  return (
    <section className="pe-ig stack">
      <div className="row pe-ig-head">
        <Send size={15} /><b className="grow">Instagram</b>
        {app.ready && !editing && <button className="btn quiet small" onClick={() => setEditing(true)}>Meta app</button>}
      </div>
      {!app.ready || editing ? (<>
        <p className="muted small" style={{ margin: 0 }}>Approved posts can go to {p.name}'s Instagram account - a
          professional one - straight from the Hub. Once, at developers.facebook.com: an app with the use case Manage
          messaging &amp; content on Instagram. An app made since October 2026 has <b>Unified login</b>: it needs a
          business portfolio, and under API setup with Unified login you create a configuration with this return
          address as its Redirect URL - then give the Hub the app's ID and secret (App settings, Basic) and the
          configuration's ID. An older app with Instagram login: its Instagram app ID and secret, this return address
          under Business login settings, and no configuration ID.</p>
        {noAddress ? <div className="pe-ig-problem small">Instagram needs the Hub's address on the internet: set
          HUB_PUBLIC_URL (https://...) in /srv/slq-hub/.env.</div>
          : <div className="row pe-ig-uri"><code className="mono grow">{app.redirect_uri}</code>
            <button className="btn quiet small" onClick={async () => toast((await copyText(app.redirect_uri))
              ? "Copied" : "Copying failed", "run")}><Copy size={13} /> Copy</button></div>}
        <div className="form-grid">
          <label className="field"><span>App ID</span><input className="input mono" value={appId}
            inputMode="numeric" autoComplete="off" onChange={(e) => setAppId(e.target.value)} /></label>
          <label className="field"><span>App secret</span><input className="input mono" type="password"
            value={secret} autoComplete="off" placeholder={app.secret_set ? "Kept - type a new one to change it" : ""}
            onChange={(e) => setSecret(e.target.value)} /></label>
          <label className="field"><span>Configuration ID (Unified login)</span><input className="input mono"
            value={config} inputMode="numeric" autoComplete="off" placeholder="Empty: Instagram login"
            onChange={(e) => setConfig(e.target.value)} /></label>
        </div>
        <div className="row">
          <span className="grow" />
          {editing && <button className="btn quiet small" onClick={() => { setEditing(false); setSecret("");
            setAppId(app.app_id); setConfig(app.config_id); }}>Cancel</button>}
          <button className="btn primary small" disabled={!appId.trim() || (!secret.trim() && !app.secret_set)}
            onClick={saveApp}>Save</button>
        </div>
      </>) : ig ? (
        <div className="row pe-ig-acct">
          <Lamp state={ig.problem ? "fault" : "run"} size="sm" />
          {ig.problem ? <span className="grow pe-ig-problem small">{ig.problem}</span>
            : <span className="grow small">Approve and post puts a post on{" "}
              <a href={`https://www.instagram.com/${ig.username}/`} target="_blank" rel="noreferrer">@{ig.username}</a>
              {ig.login === "instagram" && <span className="faint"> - the sign-in is renewed by itself.</span>}
              {ig.others > 0 && <span className="faint"> (Meta gave the Hub {ig.others + 1} accounts; to use another,
                connect again and choose only that one.)</span>}</span>}
          {ig.problem && <button className="btn primary small" onClick={connect}>Connect again</button>}
          <ConfirmButton className="btn quiet small" confirmText="Disconnect?" onConfirm={disconnect}>
            Disconnect</ConfirmButton>
        </div>
      ) : (
        <div className="row pe-ig-acct">
          <span className="grow muted small">Connect {p.name}'s account, and approved posts can go there. In Meta's
            window, sign in and choose {p.name}'s Instagram account - not your own.</span>
          <button className="btn primary small" onClick={connect}><Send size={13} /> Connect Instagram</button>
        </div>
      )}
    </section>
  );
}

/** The drafts of all personas, for the Personas page - and the posts on their way to Instagram or that did not get
 *  there (0.76.0: "Approve and post" here, and how it went is seen here). */
export function ReviewList() {
  const { posts, load } = usePosts("/hub/api/personas/posts?status=review", true);
  if (!posts || posts.length === 0) return null;
  return (
    <section className="stack">
      <h3 style={{ margin: 0 }}>Posts to review ({posts.length})</h3>
      <div className="pe-posts">{posts.map((s) => <PostCard key={s.id} post={s} onChange={load} showWho />)}</div>
    </section>
  );
}

function PostCard({ post, onChange, showWho = false }: { post: PersonaPost; onChange: () => void;
  showWho?: boolean }) {
  const { toast } = useApp();
  const [text, setText] = useState(post.text);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    if (!editing) setText(post.text);
  }, [post.text, editing]);
  const base = `/hub/api/personas/${post.persona.id}/posts/${post.id}`;
  const act = async (call: () => Promise<unknown>, done?: string) => {
    try {
      await call();
      if (done) toast(done, "run");
      onChange();
      return true;
    } catch (e) {
      toast((e as Error).message, "fault");
      return false;
    }
  };
  const run = () => act(() => api(`/hub/api/personas/${post.persona.id}/tasks/${post.task}/run`, { method: "POST" }),
    "Another one is being made - it comes here");
  const ig = post.instagram;
  const account = post.persona.instagram;                // where it can go now ("" when nowhere)
  const posted = ig?.state === "posted";
  const posting = ig?.state === "posting";
  const publish = () => act(() => api(`${base}/instagram`, { method: "POST" }), `Posting to @${account}`);
  const tag = posted ? ["On Instagram", " ok"] : posting ? ["Posting", ""] : ig?.state === "failed"
    ? ["Not posted", " fault"] : post.status === "approved" ? ["Approved", " ok"] : ["To review", ""];
  return (
    <article className={`pe-post${post.status === "approved" ? " approved" : ""}`}>
      <div className="pe-post-media">
        {post.media.length === 0 ? <div className="pe-post-textonly">Text only</div>
          : post.media.map((m) => m.kind === "video"
            ? <video key={m.file} src={personaFile(post.persona.id, m.file)} controls playsInline preload="metadata" />
            : <a key={m.file} href={personaFile(post.persona.id, m.file)} target="_blank" rel="noreferrer">
              <img src={personaFile(post.persona.id, m.file)} alt="" loading="lazy" /></a>)}
      </div>
      <div className="pe-post-body stack">
        <div className="row pe-post-head">
          {showWho && <Avatar persona={post.persona} size={22} />}
          <b className="ellipsis grow">{showWho ? `${post.persona.name} · ` : ""}{post.title}</b>
          <span className={`tag${tag[1]}`}>{tag[0]}</span>
        </div>
        <div className="faint small">{whenText(post.made)}{posted && ig?.at ? ` · on Instagram ${whenText(ig.at)}` : ""}
        </div>
        {posting && <div className="row small" style={{ gap: 6 }}><Lamp state="run" size="sm" pulse />
          Posting to Instagram</div>}
        {ig?.state === "failed" && ig.error && <div className="pe-post-error small">{ig.error}</div>}
        {editing ? (
          <>
            <textarea className="textarea" rows={6} value={text} maxLength={5000} onChange={(e) => setText(e.target.value)} />
            <div className="row">
              <span className="grow" />
              <button className="btn quiet small" onClick={() => { setText(post.text); setEditing(false); }}>Cancel</button>
              <button className="btn primary small" disabled={!text.trim()} onClick={async () => {
                if (await act(() => api(base, { method: "PATCH", body: { text } }), "Saved")) setEditing(false);
              }}>Save</button>
            </div>
          </>
        ) : <div className="pe-post-text">{post.text}</div>}
        <div className="row pe-post-acts">
          {post.status === "draft" && account && <ConfirmButton className="btn primary small"
            confirmText={`Post to @${account}?`} onConfirm={publish}><Send size={13} /> Approve and post</ConfirmButton>}
          {post.status === "draft"
            ? <button className={`btn small ${account ? "quiet" : "primary"}`} onClick={() => act(() => api(base,
              { method: "PATCH", body: { status: "approved" } }), "Approved")}><Check size={13} /> Approve</button>
            : !posted && !posting && <>
              {account && <ConfirmButton className="btn primary small" confirmText={`Post to @${account}?`}
                onConfirm={publish}><Send size={13} /> {ig?.state === "failed" ? "Post again" : "Post to Instagram"}
              </ConfirmButton>}
              <button className="btn quiet small" onClick={() => act(() => api(base, { method: "PATCH",
                body: { status: "draft" } }))}><RotateCcw size={13} /> Back to review</button></>}
          {posted && ig?.permalink && <a className="btn quiet small" href={ig.permalink} target="_blank"
            rel="noreferrer"><ExternalLink size={13} /> On Instagram</a>}
          {!editing && !posted && !posting && <button className="btn quiet small" onClick={() => setEditing(true)}>
            <Pencil size={13} /> Text</button>}
          <button className="btn quiet small" onClick={async () => toast((await copyText(post.text)) ? "Copied"
            : "Copying failed", "run")}><Copy size={13} /> Copy</button>
          {post.media.map((m, i) => <a key={m.file} className="btn quiet small" href={personaFile(post.persona.id, m.file)}
            download><Download size={13} /> {m.kind === "video" ? "Video" : post.media.length > 1 ? `Picture ${i + 1}` : "Picture"}</a>)}
          <span className="grow" />
          <button className="btn quiet small" onClick={run} title="The task once more, now"><Play size={13} /> Another</button>
          {post.chat && <a className="btn quiet small" {...linkProps({ name: "chat", id: post.chat })}
            title="How it was made"><MessageSquare size={13} /></a>}
          <ConfirmButton className="btn quiet small" disabled={posting}
            confirmText={posted ? "Discard it here? It stays on Instagram." : "Discard this post?"}
            onConfirm={() => act(() => api(base, { method: "DELETE" }), "Discarded")}><Trash2 size={13} /> Discard</ConfirmButton>
        </div>
      </div>
    </article>
  );
}
