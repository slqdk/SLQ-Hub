// A persona's tasks and posts (0.75.0, Søren: "At 7PM every monday, prepare a social media post with a picture or
// video ... and then list the posts for me to review"): at its days and time a task makes a post - its text in the
// persona's voice and a picture or video - for the owner's review. Nothing is published.
import { useCallback, useEffect, useState } from "react";
import { Check, Copy, Download, MessageSquare, Pencil, Play, Plus, RotateCcw, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { copyText } from "../lib/clipboard";
import { DAYS, personaFile, scheduleText, whenText, type Persona, type PersonaPost, type PersonaTask,
  type TaskMedia } from "../lib/personas";
import { linkProps } from "../lib/router";
import { useApp } from "../state";
import { Avatar } from "../components/Avatar";
import { ConfirmButton, Empty, Lamp, Switch } from "../components/ui";

type Act = (call: () => Promise<unknown>, done?: string) => Promise<unknown>;

const MEDIA: { id: TaskMedia; label: string }[] = [
  { id: "picture", label: "A picture" }, { id: "video", label: "A video" },
  { id: "either", label: "A picture or a video" }, { id: "none", label: "Text only" }];
const mediaLabel = (m: TaskMedia) => MEDIA.find((x) => x.id === m)?.label ?? m;

type TaskDraft = Pick<PersonaTask, "title" | "instruction" | "days" | "time" | "media" | "web">;
const NEW_TASK: TaskDraft = { title: "", instruction: "", days: [0], time: "19:00", media: "picture", web: false };

// ---------------------------------------------------------------- tasks

export function TasksTab({ p, act }: { p: Persona; act: Act }) {
  const [editing, setEditing] = useState<string | null>(null);        // a task's id, or "new"
  const base = `/hub/api/personas/${p.id}/tasks`;
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>At its days and time {p.name} does the task alone: writes the
        post in {p.name}'s voice, makes the picture or video, and hands it in for you to review under Posts. Nothing
        is published.</p>
      {p.tasks.length === 0 && editing !== "new" && <Empty>No tasks yet.</Empty>}
      {p.tasks.map((t) => (editing === t.id
        ? <TaskForm key={t.id} initial={t} onCancel={() => setEditing(null)} onSave={async (d) => {
          if (await act(() => api(`${base}/${t.id}`, { method: "PATCH", body: d }), "Saved")) setEditing(null);
        }} />
        : <TaskRow key={t.id} p={p} t={t} act={act} onEdit={() => setEditing(t.id)} />))}
      {editing === "new"
        ? <TaskForm initial={NEW_TASK} onCancel={() => setEditing(null)} onSave={async (d) => {
          if (await act(() => api(base, { body: d }), "The task is set")) setEditing(null);
        }} />
        : <div className="row"><button className="btn" onClick={() => setEditing("new")}><Plus size={15} /> New task</button></div>}
    </div>
  );
}

function TaskRow({ p, t, act, onEdit }: { p: Persona; t: PersonaTask; act: Act; onEdit: () => void }) {
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

function TaskForm({ initial, onSave, onCancel }: { initial: TaskDraft; onSave: (d: TaskDraft) => unknown;
  onCancel: () => void }) {
  const [d, setD] = useState<TaskDraft>({ title: initial.title, instruction: initial.instruction,
    days: [...initial.days], time: initial.time, media: initial.media, web: initial.web });
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
      <div className="row">
        <span className="grow" />
        <button className="btn quiet small" onClick={onCancel}>Cancel</button>
        <button className="btn primary small" disabled={!ready} onClick={() => onSave(d)}>Save</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- posts

/** Posts from the Hub: one persona's, or the drafts of all of them - reloaded every 15 s while one is being made. */
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
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(load, 15000);
    return () => window.clearInterval(t);
  }, [live, load]);
  return { posts, load, setPosts };
}

export function PostsTab({ p }: { p: Persona }) {
  const running = p.tasks.some((t) => t.running);
  const { posts, load } = usePosts(`/hub/api/personas/${p.id}/posts`, running);
  useEffect(() => {                                     // a post handed in: here at once
    if (!running) load();
  }, [running, load]);
  if (!posts) return <div className="faint">Loading</div>;
  return (
    <div className="stack">
      {running && <div className="row small" style={{ gap: 6 }}><Lamp state="run" size="sm" pulse />
        {p.name} is making a post</div>}
      {posts.length === 0 ? <Empty>No posts yet - give {p.name} a task (Tasks).</Empty>
        : <div className="pe-posts">{posts.map((s) => <PostCard key={s.id} post={s} onChange={load} />)}</div>}
    </div>
  );
}

/** The drafts of all personas, for the Personas page. */
export function ReviewList() {
  const { posts, load } = usePosts("/hub/api/personas/posts?status=draft", true);
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
          <span className={`tag${post.status === "approved" ? " ok" : ""}`}>
            {post.status === "approved" ? "Approved" : "To review"}</span>
        </div>
        <div className="faint small">{whenText(post.made)}</div>
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
          {post.status === "draft"
            ? <button className="btn primary small" onClick={() => act(() => api(base, { method: "PATCH",
              body: { status: "approved" } }), "Approved")}><Check size={13} /> Approve</button>
            : <button className="btn quiet small" onClick={() => act(() => api(base, { method: "PATCH",
              body: { status: "draft" } }))}><RotateCcw size={13} /> Back to review</button>}
          {!editing && <button className="btn quiet small" onClick={() => setEditing(true)}><Pencil size={13} /> Text</button>}
          <button className="btn quiet small" onClick={async () => toast((await copyText(post.text)) ? "Copied"
            : "Copying failed", "run")}><Copy size={13} /> Copy</button>
          {post.media.map((m, i) => <a key={m.file} className="btn quiet small" href={personaFile(post.persona.id, m.file)}
            download><Download size={13} /> {m.kind === "video" ? "Video" : post.media.length > 1 ? `Picture ${i + 1}` : "Picture"}</a>)}
          <span className="grow" />
          <button className="btn quiet small" onClick={run} title="The task once more, now"><Play size={13} /> Another</button>
          {post.chat && <a className="btn quiet small" {...linkProps({ name: "chat", id: post.chat })}
            title="How it was made"><MessageSquare size={13} /></a>}
          <ConfirmButton className="btn quiet small" confirmText="Discard this post?"
            onConfirm={() => act(() => api(base, { method: "DELETE" }), "Discarded")}><Trash2 size={13} /> Discard</ConfirmButton>
        </div>
      </div>
    </article>
  );
}
