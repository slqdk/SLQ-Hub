// A chat: history, the live turn, and the composer.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { Copy, Download, FileText, FolderOpen, Menu, MoreHorizontal, Pencil, RotateCcw, Trash2 } from "lucide-react";
import { api, mediaUrl, type ArtifactRef, type Block, type ChatData, type ChatSettings, type Library, type Message,
  type RunEvent, type RunRecord, type Settings, type Skill, type Upload } from "../lib/api";
import { seconds, tokens } from "../lib/format";
import { linkProps, navigate } from "../lib/router";
import { buildTurns, liveSegments, newLive, reduceLive, type AssistantTurn, type LiveRun } from "../lib/turns";
import { useApp } from "../state";
import { Composer, type ComposerSettings } from "../components/Composer";
import { Items } from "../components/Turn";
import { copyText, Lamp, MenuButton } from "../components/ui";
import { download } from "../lib/download";
import type { PanelState } from "../App";
import type { Folder } from "../components/Files";

let settingsCache: Settings | null = null;
async function loadSettings(): Promise<Settings> {
  if (!settingsCache) settingsCache = (await api<{ settings: Settings }>("/hub/api/settings")).settings;
  return settingsCache;
}
export function forgetSettings() {
  settingsCache = null;
}

export function ChatView({ chatId, projectId, openPanel, refs, onRefs, keepPanel }: { chatId?: number; projectId?: number;
  openPanel: (p: PanelState) => void;
  refs: string[];                                   // files of the folder picked for the next message
  onRefs: Dispatch<SetStateAction<string[]>>;
  keepPanel: () => void;                            // a new chat was saved: keep the panel on its page
}) {
  const { models, queue, refreshLists, toast, setSidebarOpen, modelNames, projects } = useApp();
  const [data, setData] = useState<ChatData | null>(null);
  const [missing, setMissing] = useState(false);
  const [live, setLive] = useState<LiveRun | null>(null);
  const liveRef = useRef<LiveRun | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const [settings, setSettings] = useState<Settings | null>(settingsCache);
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [draft, setDraft] = useState<Partial<ChatSettings>>({});
  const [editing, setEditing] = useState<number | null>(null);
  const [renaming, setRenaming] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const seenArtifacts = useRef(0);

  const project = data?.project ?? (projectId ? projects.find((p) => p.id === projectId) ?? null : null);

  useEffect(() => {
    loadSettings().then(setSettings, () => undefined);
    api<{ libraries: Library[] }>("/hub/api/libraries").then((r) => setLibraries(r.libraries), () => undefined);
    api<Skill[]>("/hub/api/skills").then(setSkills, () => undefined);
  }, []);

  const detach = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  const load = useCallback(async () => {
    if (!chatId) return null;
    try {
      const d = await api<ChatData>(`/hub/api/chats/${chatId}`);
      setData(d);
      return d;
    } catch (e) {
      if ((e as { status?: number }).status === 404) setMissing(true);
      else toast((e as Error).message, "fault");
      return null;
    }
  }, [chatId, toast]);

  const attach = useCallback((runId: string) => {
    if (esRef.current && liveRef.current?.runId === runId) return;
    detach();
    liveRef.current = newLive(runId);
    seenArtifacts.current = 0;
    setLive(liveRef.current);
    stick.current = true;
    const es = new EventSource(`/hub/api/runs/${runId}/events`);
    esRef.current = es;
    let ended = false;
    es.onmessage = (m) => {
      let ev: RunEvent;
      try {
        ev = JSON.parse(m.data);
      } catch {
        return;
      }
      if (!liveRef.current || liveRef.current.runId !== runId) return;
      liveRef.current = reduceLive(liveRef.current, ev);
      setLive(liveRef.current);
      if (ev.type === "title") refreshLists();
      if (ev.type === "done" || ev.type === "error" || ev.type === "cancelled") ended = true;
    };
    const finish = async () => {
      es.close();
      if (esRef.current === es) esRef.current = null;
      const err = liveRef.current?.status === "error" ? liveRef.current.error : null;
      const d = await load();
      if (liveRef.current?.runId === runId) {
        liveRef.current = null;
        setLive(null);
      }
      refreshLists();
      if (err) toast(err, "fault");
      // A message that came after the turn's last step (or with Stop) has started the next turn.
      if (d?.run && d.run.id !== runId) attachRef.current?.(d.run.id);
    };
    es.addEventListener("end", () => finish());
    es.onerror = () => {
      // A finished run's stream closes; a lost connection is retried by the browser.
      if (ended || es.readyState === EventSource.CLOSED) finish();
    };
  }, [detach, load, refreshLists, toast]);
  const attachRef = useRef(attach);
  attachRef.current = attach;

  useEffect(() => {
    setData(null);
    setMissing(false);
    setLive(null);
    liveRef.current = null;
    detach();
    if (!chatId) return;
    load().then((d) => d?.run && attach(d.run.id));
    return detach;
  }, [chatId, load, attach, detach]);

  // New artifacts made during a live turn open in the panel.
  useEffect(() => {
    if (!live || !chatId) return;
    const arts = live.items.filter((i) => i.kind === "artifact");
    if (arts.length > seenArtifacts.current) {
      seenArtifacts.current = arts.length;
      const last = arts[arts.length - 1];
      if (last.kind === "artifact") openPanel({ kind: "artifact", chatId, id: last.ref.id, version: last.ref.version });
    }
  }, [live, chatId, openPanel]);

  // Keep the newest text in view unless the reader scrolled up.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });

  const llms = useMemo(() => (models?.llm ?? []).filter((m) => m.installed), [models]);
  // Picture models: downloaded, and not a turbo that needs its base (that one is for fast drafts).
  const pictureModels = useMemo(() => (models?.comfy ?? []).filter((m) => m.installed && !(m.requires ?? []).length), [models]);
  const pictureDefault = project?.settings.image_model ?? (settings?.images?.model as string | undefined) ?? "qwen-image-2.1";

  const effective: ComposerSettings = useMemo(() => {
    const cs = { ...(data?.chat.settings ?? {}), ...draft } as Partial<ChatSettings>;
    const ps = project?.settings;
    const agent = settings?.agent ?? {};
    const def = settings?.models?.default ?? models?.defaults?.default ?? llms[0]?.id ?? "";
    const thinking = cs.thinking ?? ps?.thinking ?? (project ? agent.thinking_in_projects : agent.thinking_in_chats) ?? false;
    return { model: cs.model ?? ps?.model ?? def, thinking: !!thinking, effort: cs.effort ?? agent.effort ?? "medium",
      skills: cs.skills ?? [], libraries: cs.libraries ?? [], image_model: cs.image_model ?? null };
  }, [data, draft, project, settings, models, llms]);

  const changeSettings = async (patch: Partial<ComposerSettings>) => {
    const p: Partial<ChatSettings> = { ...patch };
    if (!chatId) {
      setDraft((d) => ({ ...d, ...p }));
      return;
    }
    try {
      const chat = await api<ChatData["chat"]>(`/hub/api/chats/${chatId}`, { method: "PATCH", body: { settings: p } });
      setData((d) => (d ? { ...d, chat } : d));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const send = async (text: string, uploads: Upload[]): Promise<boolean> => {
    try {
      let id = chatId;
      if (!id) {
        const created = await api<{ id: number }>("/hub/api/chats", { body: { project_id: projectId ?? null,
          settings: draft } });
        id = created.id;
      }
      const r = await api<{ message: Message | null; run_id: string; queued?: string }>(`/hub/api/chats/${id}/messages`, {
        body: { text, attachments: uploads.map((u) => u.sha), refs } });
      onRefs([]);
      if (r.queued || !r.message) {                    // the turn at work reads it after the step it is on
        stick.current = true;
        return true;
      }
      if (!chatId) {
        refreshLists();
        keepPanel();
        navigate({ name: "chat", id }, true);
        return true;
      }
      const message = r.message;
      setData((d) => (d ? { ...d, messages: [...d.messages, message] } : d));
      attach(r.run_id);
      return true;
    } catch (e) {
      toast((e as Error).message, "fault");
      return false;
    }
  };

  const stop = async () => {
    if (!live) return;
    try {
      await api(`/hub/api/runs/${live.runId}/stop`, { method: "POST" });
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const retry = async (messageId?: number) => {
    try {
      const r = await api<{ run_id: string }>(`/hub/api/chats/${chatId}/retry`, { body: { message_id: messageId ?? null } });
      await load();
      attach(r.run_id);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const saveEdit = async (messageId: number, text: string) => {
    try {
      const r = await api<{ run_id: string }>(`/hub/api/chats/${chatId}/edit`, { body: { message_id: messageId, text } });
      setEditing(null);
      await load();
      attach(r.run_id);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const rename = async (title: string) => {
    setRenaming(false);
    if (!chatId || !title.trim() || title === data?.chat.title) return;
    try {
      const chat = await api<ChatData["chat"]>(`/hub/api/chats/${chatId}`, { method: "PATCH", body: { title } });
      setData((d) => (d ? { ...d, chat } : d));
      refreshLists();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const remove = async () => {
    if (!chatId) return;
    try {
      await api(`/hub/api/chats/${chatId}`, { method: "DELETE" });
      refreshLists();
      navigate(project ? { name: "project", id: project.id } : "/", true);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const openArtifact = (ref: ArtifactRef) => chatId && openPanel({ kind: "artifact", chatId, id: ref.id, version: ref.version });
  const turns = useMemo(() => (data ? buildTurns(data.messages, data.runs, live?.runId) : []), [data, live?.runId]);
  // A note written while a turn worked splits it in parts: the run's summary goes under the last part.
  const lastPart = useMemo(() => {
    const m = new Map<string, number>();
    turns.forEach((t, i) => { if (t.type === "assistant" && t.runId) m.set(t.runId, i); });
    return m;
  }, [turns]);
  const lastAssistant = [...turns].reverse().find((t) => t.type === "assistant") as AssistantTurn | undefined;
  const running = !!live && live.status !== "done" && live.status !== "error" && live.status !== "cancelled";

  if (missing) {
    return (
      <div className="main">
        <div className="topbar"><h1>Chat not found</h1></div>
        <div className="page"><p className="lead">This chat was deleted, or the link is wrong.</p>
          <a className="btn" {...linkProps("/")}>Start a new chat</a></div>
      </div>
    );
  }

  const title = data?.chat.title ?? (project ? `New chat in ${project.name}` : "New chat");
  // The folder the assistant works in: the project's, or this chat's own (once the chat exists).
  const folder: Folder | null = project ? { kind: "project", id: project.id } : chatId ? { kind: "chat", id: chatId } : null;
  const showFiles = (select?: string) => folder && openPanel({ kind: "files", folder, select, at: Date.now() });

  return (
    <div className="main">
      <div className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open the menu"><Menu size={18} /></button>
        {renaming ? (
          <input className="input" autoFocus defaultValue={data?.chat.title} style={{ maxWidth: 420 }}
            onBlur={(e) => rename(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") rename((e.target as HTMLInputElement).value); if (e.key === "Escape") setRenaming(false); }} />
        ) : (
          <h1 className="ellipsis" onDoubleClick={() => chatId && setRenaming(true)}>{title}</h1>
        )}
        {project && <a className="tag" {...linkProps({ name: "project", id: project.id })}>{project.name}</a>}
        <span className="grow" />
        {folder && <button className="btn quiet small" onClick={() => showFiles()}>
          <FolderOpen size={15} /> Files</button>}
        {chatId && (
          <MenuButton right className="icon-btn" label={<MoreHorizontal size={18} />} title="More">
            {(close) => (
              <>
                <button className="menu-item" onClick={() => { close(); setRenaming(true); }}><Pencil size={15} /> Rename</button>
                {data && data.artifacts.length > 0 && data.artifacts.map((a) => (
                  <button key={a.id} className="menu-item" onClick={() => { close(); openPanel({ kind: "artifact", chatId, id: a.id }); }}>
                    <span className="tag">artifact</span> {a.title}</button>
                ))}
                <div className="menu-sep" />
                <button className="menu-item" onClick={() => { close(); download(`/hub/api/chats/${chatId}/export?format=md`); }}>
                  <FileText size={15} /> Export as Markdown</button>
                <button className="menu-item" onClick={() => { close(); download(`/hub/api/chats/${chatId}/export?format=zip`); }}>
                  <Download size={15} /><span>Export for troubleshooting<small>Everything about the chat, as a ZIP</small></span></button>
                <div className="menu-sep" />
                <button className="menu-item" style={{ color: "var(--fault)" }} onClick={() => { close(); remove(); }}>
                  <Trash2 size={15} /> Delete chat</button>
              </>
            )}
          </MenuButton>
        )}
      </div>

      <div className="scroll" ref={scroller} onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90;
      }}>
        <div className="column">
          {!chatId && (
            <div className="welcome">
              <h2>{project ? project.name : "What are we working on?"}</h2>
              <p>{project
                ? "This chat works in the project's files. Everything the assistant changes is kept in the project's history."
                : "Ask, attach files or pictures, ask for a picture, or have it build something. It runs on your own card."}</p>
            </div>
          )}
          <div className="messages">
            {turns.map((t, i) => t.type === "user" ? (
              <UserMessage key={`u${t.message.id}`} message={t.message} editing={editing === t.message.id}
                canEdit={!running} onEdit={() => setEditing(t.message.id)} onCancel={() => setEditing(null)}
                onSave={(text) => saveEdit(t.message.id, text)} onOpenFile={folder ? showFiles : undefined} />
            ) : (
              <div className="msg-assistant" key={t.key}>
                <Items items={t.items} actions={{ openArtifact }} />
                <TurnFoot turn={t} names={modelNames} canRetry={!running && t === lastAssistant}
                  stats={!t.runId || lastPart.get(t.runId) === i} onRetry={() => retry()} />
              </div>
            ))}
            {live && (() => {
              const segs = liveSegments(live.items);
              const tail = segs.length && segs[segs.length - 1].kind === "assistant" ? segs.length - 1 : -1;
              const state = <LiveState live={live} queueCurrent={queue?.current ?? null} names={modelNames} />;
              return <>
                {segs.map((seg, i) => seg.kind === "user" ? (
                  <UserMessage key={`lu${seg.id}`} message={seg.message} editing={false} canEdit={false}
                    onEdit={() => undefined} onCancel={() => undefined} onSave={() => undefined}
                    onOpenFile={folder ? showFiles : undefined} />
                ) : (
                  <div className="msg-assistant" key={`la${i}`}>
                    <Items items={seg.items} actions={{ openArtifact, runId: live.runId, live: true }} />
                    {i === tail && state}
                  </div>
                ))}
                {tail < 0 && <div className="msg-assistant">{state}</div>}
                {live.pending.map((n) => <PendingNote key={n.id} content={n.content} />)}
              </>;
            })()}
          </div>
        </div>
      </div>

      <div className="composer-wrap">
        <div className="column">
          <Composer running={running} onSend={send} onStop={stop} settings={effective} onSettings={changeSettings}
            models={llms} pictureModels={pictureModels} pictureDefault={pictureDefault} libraries={libraries} skills={skills} autoFocus
            placeholder={running ? "Add something while it works" : project ? `Ask about ${project.name}, or give it a task` : undefined}
            refs={folder ? refs : []} onRemoveRef={(p) => onRefs((r) => r.filter((x) => x !== p))} onOpenRef={(p) => showFiles(p)}
            onBrowseFiles={folder ? () => showFiles() : undefined} browseLabel={project ? "Project files" : "This chat's files"} />
          <div className="hint">{running ? "What you send now is read after the step it is working on. The square stops it."
            : <>Enter sends, Shift+Enter makes a new line. Paste or drop files to attach them
            {folder ? <>, or put {project ? "project" : "the chat's"} files in the message from <b>Files</b>.</> : "."}</>}</div>
        </div>
      </div>
    </div>
  );
}

function UserMessage({ message, editing, canEdit, onEdit, onCancel, onSave, onOpenFile }: { message: Message;
  editing: boolean; canEdit: boolean; onEdit: () => void; onCancel: () => void; onSave: (text: string) => void;
  onOpenFile?: (path: string) => void }) {
  const text = message.content.filter((b) => b.type === "text").map((b) => (b as { text: string }).text).join("\n\n");
  const files = message.content.filter((b) => b.type === "image" || b.type === "file" || b.type === "file_ref");
  const whileWorking = message.content.some((b) => b.type === "text" && b.while_working);
  const [value, setValue] = useState(text);
  return (
    <div className="msg-user">
      {whileWorking && <div className="msg-note">Sent while it worked</div>}
      {files.length > 0 && (
        <div className="attachments">
          {files.map((b, i) => b.type === "image" ? (
            <a key={i} className="thumb" href={mediaUrl(b.sha)} target="_blank" rel="noreferrer" title={b.ref ? b.path : b.name}>
              <img src={mediaUrl(b.sha)} alt={b.name ?? "attached image"} loading="lazy" /></a>
          ) : b.type === "file" ? (
            <a key={i} className="file-chip" href={`${mediaUrl(b.sha)}?download=1`}>{b.name}</a>
          ) : b.type === "file_ref" ? (
            <button key={i} className="file-chip" disabled={!onOpenFile} onClick={() => onOpenFile?.(b.path)}
              title={`Show ${b.path}`}>
              <FileText size={14} /><span className="ellipsis">{b.path}</span></button>
          ) : null)}
        </div>
      )}
      {editing ? (
        <div className="stack" style={{ width: "min(640px, 80vw)" }}>
          <textarea className="textarea" value={value} autoFocus onChange={(e) => setValue(e.target.value)} rows={4} />
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn small" onClick={onCancel}>Cancel</button>
            <button className="btn primary small" onClick={() => onSave(value)} disabled={!value.trim()}>Send again</button>
          </div>
        </div>
      ) : (
        text && <div className="bubble">{text}</div>
      )}
      {!editing && (
        <div className="tools">
          <button className="icon-btn small" onClick={() => copyText(text)} aria-label="Copy"><Copy size={14} /></button>
          {canEdit && <button className="icon-btn small" onClick={onEdit} aria-label="Edit and send again"><Pencil size={14} /></button>}
        </div>
      )}
    </div>
  );
}

/** A message sent while the turn works, before the model has read it. */
function PendingNote({ content }: { content: Block[] }) {
  const text = content.filter((b) => b.type === "text").map((b) => (b as { text: string }).text).join("\n\n");
  const files = content.filter((b) => b.type === "image" || b.type === "file" || b.type === "file_ref");
  return (
    <div className="msg-user pending-note">
      <div className="msg-note"><Lamp state="wait" size="sm" /> Waiting: it reads this after the step it is on</div>
      {files.length > 0 && (
        <div className="attachments">
          {files.map((b, i) => b.type === "image" ? (
            <span key={i} className="thumb"><img src={mediaUrl(b.sha)} alt={b.name ?? "attached image"} /></span>
          ) : <span key={i} className="file-chip">{b.type === "file_ref" ? b.path : b.name}</span>)}
        </div>
      )}
      {text && <div className="bubble">{text}</div>}
    </div>
  );
}

function runStats(run: RunRecord | undefined, names: Record<string, string>): string {
  if (!run) return "";
  const parts: string[] = [];
  const models = Array.from(new Set(run.steps.filter((s) => s.type === "model").map((s) => s.model ?? "")));
  if (models.length) parts.push(models.map((m) => names[m] ?? m).join(" and "));
  const secs = run.steps.reduce((a, s) => a + (s.depth ? 0 : Number(s.seconds ?? 0)), 0);
  if (secs >= 0.05) parts.push(seconds(secs));
  const out = run.usage?.completion_tokens;
  if (out) parts.push(`${tokens(out)} tokens`);
  const tps = run.steps.filter((s) => s.type === "model" && s.tps).map((s) => s.tps!);
  if (tps.length) parts.push(`${Math.round(tps.reduce((a, b) => a + b, 0) / tps.length)} tokens/s`);
  if (run.status === "cancelled") parts.push("stopped");
  if (run.status === "error") parts.push("failed");
  return parts.join(", ");
}

function TurnFoot({ turn, names, canRetry, onRetry, stats = true }: { turn: AssistantTurn; names: Record<string, string>;
  canRetry: boolean; onRetry: () => void; stats?: boolean }) {
  const { toast } = useApp();
  return (
    <div className="msg-foot">
      {turn.text && <button className="icon-btn small" aria-label="Copy the answer"
        onClick={async () => toast((await copyText(turn.text)) ? "Copied" : "Copying failed", "run")}><Copy size={14} /></button>}
      {canRetry && <button className="icon-btn small" aria-label="Answer again" onClick={onRetry}><RotateCcw size={14} /></button>}
      <span className="stats">{stats ? runStats(turn.run, names) : ""}</span>
      {stats && turn.run?.status === "error" && turn.run.error && <span className="notice fault" style={{ fontSize: 13 }}>{turn.run.error}</span>}
    </div>
  );
}

function LiveState({ live, queueCurrent, names }: { live: LiveRun; queueCurrent: string | null; names: Record<string, string> }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((x) => x + 1), 1000);
    return () => window.clearInterval(t);
  }, []);
  const secs = (Date.now() - live.started) / 1000;
  if (live.status === "queued") {
    const loading = live.position === 0 || (live.position == null && queueCurrent !== null);
    return (
      <div className="run-state"><Lamp state="wait" />
        {live.position && live.position > 0
          ? `Waiting for the card, ${live.position} job${live.position === 1 ? "" : "s"} ahead`
          : loading ? "Getting the card ready" : "Waiting for the card"}
        <span className="faint num">{seconds(secs)}</span>
      </div>
    );
  }
  if (live.status === "running") {
    const waiting = live.items.some((i) => i.kind === "tools" && i.tools.some((t) => t.state === "approval"));
    return (
      <div className="run-state">
        <Lamp state={waiting ? "act" : "run"} pulse={!waiting} />
        {waiting ? "Waiting for your answer" : `Working${live.model ? ` with ${names[live.model] ?? live.model}` : ""}`}
        <span className="faint num">{seconds(secs)}{live.steps ? `, ${live.steps} step${live.steps === 1 ? "" : "s"}` : ""}{live.tps ? `, ${Math.round(live.tps)} tokens/s` : ""}</span>
      </div>
    );
  }
  if (live.status === "error") return <div className="run-state"><Lamp state="fault" />{live.error ?? "The turn failed"}</div>;
  return null;
}
