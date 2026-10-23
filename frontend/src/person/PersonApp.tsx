// The Hub for a person the owner added (Settings, People): the same chat as
// the owner's - models, pictures, thinking, tools, artifacts - with the chats
// and their files kept on this device. The Hub only answers.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download, FileText, FolderOpen, KeyRound, LogOut, Menu, MessageSquarePlus, MoreHorizontal, Pencil, Search,
  Trash2, Upload as UploadIcon, User as UserIcon, X } from "lucide-react";
import { api, setLocalMedia, type Artifact, type ArtifactRef, type Block, type ChatSettings, type Message, type ModelEntry,
  type Settings, type Skill, type Upload } from "../lib/api";
import { bytes, dateGroup } from "../lib/format";
import { saveText } from "../lib/download";
import { buildTurns, liveSegments, newLive, reduceLive, type AssistantTurn, type LiveRun } from "../lib/turns";
import { useApp } from "../state";
import { ArtifactPanel } from "../components/ArtifactPanel";
import { Composer, type ComposerSettings } from "../components/Composer";
import { GpuPlate } from "../components/Queue";
import { Items } from "../components/Turn";
import { Lamp, Logo, MenuButton, Modal, Toasts } from "../components/ui";
import { LiveState, TurnFoot, UserMessage } from "../views/ChatView";
import { photoFromFile } from "../chat/photos";
import * as S from "./store";
import * as T from "./turns";

const MAX_ATTACHMENT = 50 * 1024 * 1024;
const EMPTY: S.ChatData = { messages: [], runs: [], artifacts: {} };

interface Busy { chat: string; turn: string; started: number }
type Panel = { kind: "artifact"; id: string; version?: number } | { kind: "files" } | null;

function chatFromHash(): string | null {
  const m = location.hash.match(/^#c=([\w-]+)$/);
  return m ? m[1] : null;
}

function dataUrlToBlob(url: string): Blob {
  const [head, data] = url.split(",", 2);
  const mime = head.match(/data:([^;]+)/)?.[1] ?? "application/octet-stream";
  const bin = atob(data);
  const bytesArr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytesArr[i] = bin.charCodeAt(i);
  return new Blob([bytesArr], { type: mime });
}

export function PersonApp() {
  const { auth, toast, models, queue, modelNames, sidebarOpen, setSidebarOpen, refreshAuth } = useApp();
  const user = auth!.user!;
  const pendingKey = `slq-hub-pending-${user.username}`;
  const [ok, setOk] = useState<boolean | null>(null);
  const [chats, setChats] = useState<S.ChatMeta[]>([]);
  const [chatId, setChatId] = useState<string | null>(chatFromHash);
  const [chat, setChat] = useState<{ meta: S.ChatMeta; data: S.ChatData } | null>(null);
  const [live, setLive] = useState<LiveRun | null>(null);
  const liveRef = useRef<LiveRun | null>(null);
  const [busy, setBusy] = useState<Busy | null>(null);
  const [saving, setSaving] = useState(false);          // the answer is done; its pictures and files come home
  const liveArtifacts = useRef(new Map<string, Artifact>());
  const [panel, setPanel] = useState<Panel>(null);
  const [draft, setDraft] = useState<Partial<ChatSettings>>({});
  const [settings, setSettings] = useState<Settings | null>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const urls = useRef(new Map<string, string>());
  const [, setMediaTick] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [editing, setEditing] = useState<number | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [account, setAccount] = useState(false);
  const [q, setQ] = useState("");
  const importInput = useRef<HTMLInputElement>(null);

  // ---- the device's store, and pictures shown from it
  useEffect(() => {
    S.selectDatabase(user.username);
    S.usable().then(setOk);
    setLocalMedia((sha) => urls.current.get(sha));
    return () => setLocalMedia(null);
  }, [user.username]);

  const loadMedia = useCallback(async (data: S.ChatData) => {
    let added = 0;
    for (const m of data.messages) {
      for (const k of S.blobKeys(m.content)) {
        if (urls.current.has(k)) continue;
        const b = await S.getBlob(k);
        if (b) {
          urls.current.set(k, URL.createObjectURL(b.blob));
          added++;
        }
      }
    }
    if (added) setMediaTick((t) => t + 1);
  }, []);

  const refreshChats = useCallback(async () => {
    try {
      setChats(await S.listChats());
    } catch {
      /* the store says so elsewhere */
    }
  }, []);

  useEffect(() => {
    if (!ok) return;
    refreshChats();
    api<{ settings: Settings }>("/hub/api/settings").then((r) => setSettings(r.settings), () => undefined);
    api<Skill[]>("/hub/api/skills").then(setSkills, () => undefined);
  }, [ok, refreshChats]);

  // ---- which chat is open (#c=<id>)
  useEffect(() => {
    const on = () => setChatId(chatFromHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const openChat = (id: string | null) => {
    setSidebarOpen(false);
    setPanel(null);
    setEditing(null);
    if (id) location.hash = `c=${id}`;
    else history.replaceState(null, "", location.pathname);
    setChatId(id);
  };

  useEffect(() => {
    if (!ok) return;
    let alive = true;
    if (!chatId) {
      setChat(null);
      setDraft({});
      return;
    }
    S.getChat(chatId).then(async (c) => {
      if (!alive) return;
      if (!c) {
        openChat(null);
        return;
      }
      setChat(c);
      stick.current = true;
      await loadMedia(c.data);
    }, () => toast("This chat could not be read.", "fault"));
    return () => {
      alive = false;
    };
  }, [chatId, ok, loadMedia, toast]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });

  // ---- an answer: send the chat, follow the events, bring the result home
  const runTurn = useCallback(async (meta: S.ChatMeta, data: S.ChatData, resumeTurn?: string, resumeStarted?: number) => {
    let turnId = resumeTurn;
    const started = resumeStarted ?? Date.now();
    if (!turnId) {
      try {
        turnId = await T.start(await T.request(meta, data));
      } catch (e) {
        toast((e as Error).message, "fault");
        return;
      }
    }
    try {
      localStorage.setItem(pendingKey, JSON.stringify({ chat: meta.id, turn: turnId, started }));
    } catch {
      /* no storage: a reload loses the answer */
    }
    liveArtifacts.current.clear();
    liveRef.current = newLive(turnId);
    setLive(liveRef.current);
    setBusy({ chat: meta.id, turn: turnId, started });
    stick.current = true;
    const result = await T.follow(turnId, (ev) => {
      if (!liveRef.current || liveRef.current.runId !== turnId) return;
      liveRef.current = reduceLive(liveRef.current, ev);
      setLive(liveRef.current);
      if (ev.type === "artifact" && ev.data.content != null) {
        const a = ev.data as Artifact & { content: string };
        liveArtifacts.current.set(a.id, { ...a, chat_id: 0, created_at: new Date().toISOString(), latest: a.version });
        setPanel({ kind: "artifact", id: a.id, version: a.version });
      }
    });
    const forgetPending = () => {
      try {
        localStorage.removeItem(pendingKey);
      } catch {
        /* fine */
      }
    };
    if (!result) {
      forgetPending();
      toast("The Hub lost this answer (it may have restarted). Ask again.", "fault");
      liveRef.current = null;
      setLive(null);
      setBusy(null);
      return;
    }
    // Pictures and files first, then the messages; only then may the Hub forget the turn (a reload before
    // that picks the answer up again).
    setSaving(true);
    const problems = await T.collect(turnId, meta.id, result);
    const current = (await S.getChat(meta.id)) ?? { meta, data };
    const merged = T.merge(current.data, turnId, result, started);
    const newMeta = { ...current.meta, updated: Date.now(),
      title: current.meta.title === "New chat" && result.title ? result.title : current.meta.title };
    await S.saveChat(newMeta, merged);
    await T.forget(turnId);
    forgetPending();
    await loadMedia(merged);
    setChat((c) => (c && c.meta.id === meta.id ? { meta: newMeta, data: merged } : c));
    liveRef.current = null;
    setLive(null);
    setBusy(null);
    setSaving(false);
    liveArtifacts.current.clear();
    refreshChats();
    if (result.status === "error" && result.error) toast(result.error, "fault");
    if (problems.length) toast(`Could not keep: ${problems.join(", ")}`, "fault");
  }, [loadMedia, pendingKey, refreshChats, toast]);

  // An answer that was being written when the page was closed or reloaded.
  useEffect(() => {
    if (!ok) return;
    let p: Busy | null = null;
    try {
      p = JSON.parse(localStorage.getItem(pendingKey) || "null");
    } catch {
      p = null;
    }
    if (!p?.turn || !p.chat) return;
    S.getChat(p.chat).then((c) => {
      if (!c || c.data.runs.some((r) => r.id === p!.turn)) {          // already kept: only the note was left
        try {
          localStorage.removeItem(pendingKey);
        } catch {
          /* fine */
        }
        return;
      }
      void runTurn(c.meta, c.data, p!.turn, p!.started);
    }, () => undefined);
  }, [ok]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- the composer's settings: the chat's, else the defaults the owner set
  const llms = useMemo(() => (models?.llm ?? []).filter((m) => m.installed), [models]);
  const pictureModels = useMemo(() => (models?.comfy ?? []).filter((m) => m.installed && !(m.requires ?? []).length), [models]);
  const pictureDefault = (settings?.images?.model as string | undefined) ?? "qwen-image-2.1";
  const effective: ComposerSettings = useMemo(() => {
    const cs = { ...(chat?.meta.settings ?? {}), ...(chat ? {} : draft) } as Partial<ChatSettings>;
    const def = (settings?.models?.default as string | undefined) ?? llms[0]?.id ?? "";
    const model = cs.model ?? def;
    const mode = llms.find((m: ModelEntry) => m.id === model)?.thinking_mode;
    const thinking = cs.thinking ?? (mode ? mode === "on" : undefined) ?? !!settings?.agent?.thinking_in_chats;
    return { model, thinking: !!thinking, effort: cs.effort ?? (settings?.agent?.effort as string | undefined) ?? "medium",
      skills: cs.skills ?? [], libraries: [], image_model: cs.image_model ?? null };
  }, [chat, draft, settings, llms]);

  const changeSettings = async (patch: Partial<ComposerSettings>) => {
    const p = { ...patch } as Partial<ChatSettings>;
    delete p.libraries;
    if (!chat) {
      setDraft((d) => ({ ...d, ...p }));
      return;
    }
    const meta = { ...chat.meta, settings: { ...chat.meta.settings, ...p } };
    setChat({ ...chat, meta });
    await S.saveMeta(meta);
  };

  // ---- attachments stay on the device; photos are made smaller first
  const uploader = async (file: File, name: string): Promise<Upload> => {
    if (file.size > MAX_ATTACHMENT) throw new Error(`${name} is larger than ${bytes(MAX_ATTACHMENT)}`);
    let blob: Blob = file;
    let mime = file.type || "application/octet-stream";
    let fname = name;
    if (/^image\/(jpeg|png|webp|heic|heif|bmp)$/.test(mime)) {
      try {
        blob = dataUrlToBlob(await photoFromFile(file));
        mime = "image/jpeg";
        fname = name.replace(/\.[^.]+$/, "") + ".jpg";
      } catch {
        /* sent as it is */
      }
    }
    const key = S.newKey();
    await S.putBlob({ key, mime, name: fname, blob });
    const url = URL.createObjectURL(blob);
    urls.current.set(key, url);
    return { sha: key, name: fname, mime, size: blob.size, width: null, height: null, url };
  };

  const send = async (text: string, uploads: Upload[]): Promise<boolean> => {
    if (busy) {
      toast("Wait for the answer, or stop it, before you write again.", "info");
      return false;
    }
    try {
      const now = Date.now();
      const meta: S.ChatMeta = chat?.meta ?? { id: S.newKey(), title: "New chat", created: now, updated: now, settings: draft };
      const data = chat?.data ?? EMPTY;
      const taken = new Set((await S.listFiles(meta.id)).map((f) => f.path));
      const blocks: Block[] = [];
      if (text.trim()) blocks.push({ type: "text", text });
      for (const u of uploads) {
        const b = await S.getBlob(u.sha);
        if (!b) continue;
        let path = `.hub/attachments/${u.name}`;
        if (taken.has(path)) path = `.hub/attachments/${u.sha.slice(1, 9)}-${u.name}`;
        taken.add(path);
        await S.putFile({ chat: meta.id, path, mime: u.mime, size: b.blob.size, blob: b.blob, at: now });
        blocks.push(u.mime.startsWith("image/") ? { type: "image", sha: u.sha, name: u.name, mime: u.mime, path }
          : { type: "file", sha: u.sha, name: u.name, mime: u.mime, size: u.size, path });
      }
      const id = data.messages.reduce((n, m) => Math.max(n, m.id), 0) + 1;
      const next: S.ChatData = { ...data, messages: [...data.messages, { id, role: "user", content: blocks, run_id: null,
        created_at: new Date(now).toISOString() }] };
      const newMeta = { ...meta, updated: now };
      await S.saveChat(newMeta, next);
      if (!chat) {
        S.askToKeep();
        setDraft({});
        location.hash = `c=${meta.id}`;
        setChatId(meta.id);
      }
      setChat({ meta: newMeta, data: next });
      refreshChats();
      void runTurn(newMeta, next);
      return true;
    } catch (e) {
      toast((e as Error).message || "The chat could not be kept on this device.", "fault");
      return false;
    }
  };

  const again = async (data: S.ChatData, cut: number) => {
    if (!chat || busy) return;
    const kept = data.messages.slice(0, cut + 1);
    const runIds = new Set(kept.map((m) => m.run_id).filter(Boolean));
    const next = { ...data, messages: kept, runs: data.runs.filter((r) => runIds.has(r.id)) };
    const meta = { ...chat.meta, updated: Date.now() };
    await S.saveChat(meta, next);
    setChat({ meta, data: next });
    void runTurn(meta, next);
  };
  const retry = () => {
    if (!chat) return;
    const msgs = chat.data.messages;
    let i = msgs.length - 1;
    while (i >= 0 && msgs[i].role !== "user") i--;
    if (i >= 0) void again(chat.data, i);
  };
  const saveEdit = (messageId: number, text: string) => {
    if (!chat) return;
    const i = chat.data.messages.findIndex((m) => m.id === messageId);
    if (i < 0) return;
    const content = [...(text.trim() ? [{ type: "text", text } as Block] : []),
      ...chat.data.messages[i].content.filter((b) => b.type !== "text")];
    const messages = chat.data.messages.map((m, j) => (j === i ? { ...m, content } : m));
    setEditing(null);
    void again({ ...chat.data, messages }, i);
  };

  const stop = async () => {
    if (!busy) return;
    try {
      await T.stop(busy.turn);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const remove = async (id: string) => {
    if (busy?.chat === id) await stop();
    await S.deleteChat(id);
    if (chatId === id) openChat(null);
    refreshChats();
  };

  const rename = async (title: string) => {
    setRenaming(false);
    if (!chat || !title.trim() || title === chat.meta.title) return;
    const meta = { ...chat.meta, title: title.trim().slice(0, 120) };
    setChat({ ...chat, meta });
    await S.saveMeta(meta);
    refreshChats();
  };

  const loadArtifact = useCallback(async (id: string, version?: number): Promise<Artifact> => {
    const liveOne = liveArtifacts.current.get(id);
    const versions = chat?.data.artifacts[id] ?? [];
    const pick = version ? versions.find((a) => a.version === version) : versions[versions.length - 1];
    if (liveOne && (!pick || liveOne.version >= (version ?? 0))) return liveOne;
    if (!pick) throw new Error("This artifact is not on this device.");
    return { ...pick, chat_id: 0, created_at: pick.at, latest: versions[versions.length - 1].version };
  }, [chat]);

  const exportAll = async () => {
    try {
      const b = await S.exportAll(user.username);
      saveText(JSON.stringify(b), `slq-hub-chats-${new Date().toISOString().slice(0, 10)}.json`);
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  const importAll = async (f: File | undefined) => {
    if (!f) return;
    try {
      const n = await S.importAll(JSON.parse(await f.text()));
      toast(`${n} chat${n === 1 ? "" : "s"} added to this device`, "run");
      refreshChats();
    } catch (e) {
      toast((e as Error).message || "That file could not be read.", "fault");
    }
  };
  const signOut = async () => {
    await api("/hub/api/auth/logout", { method: "POST" }).catch(() => undefined);
    refreshAuth();
  };

  if (ok === null) return null;
  if (!ok) {
    return (
      <div className="pc-gate"><div className="pc-card">
        <h1><Logo /> SLQ Hub</h1>
        <p className="notice fault"><Lamp state="fault" /> This browser does not let the page keep chats (private browsing?).
          Your chats are kept on this device, so open the Hub in a normal window.</p>
        <button className="btn" onClick={signOut}>Sign out</button>
      </div><Toasts /></div>
    );
  }

  // ---- the page
  const messages: Message[] = (chat?.data.messages ?? []).map((m) => ({ ...m, chat_id: 0 }));
  const liveHere = busy && chat && busy.chat === chat.meta.id ? live : null;
  const turns = buildTurns(messages, chat?.data.runs ?? [], liveHere?.runId);
  const lastPart = new Map<string, number>();
  turns.forEach((t, i) => { if (t.type === "assistant" && t.runId) lastPart.set(t.runId, i); });
  const lastAssistant = [...turns].reverse().find((t) => t.type === "assistant") as AssistantTurn | undefined;
  const running = !!liveHere && liveHere.status !== "done" && liveHere.status !== "error" && liveHere.status !== "cancelled";
  const openArtifact = (ref: ArtifactRef) => setPanel({ kind: "artifact", id: ref.id, version: ref.version });
  const shown = q.trim() ? chats.filter((c) => c.title.toLowerCase().includes(q.trim().toLowerCase())) : chats;
  const groups: { label: string; items: S.ChatMeta[] }[] = [];
  for (const c of shown) {
    const g = dateGroup(new Date(c.updated).toISOString());
    if (!groups.length || groups[groups.length - 1].label !== g) groups.push({ label: g, items: [] });
    groups[groups.length - 1].items.push(c);
  }

  return (
    <div className={`app ${panel ? "with-panel" : ""}`}>
      {sidebarOpen && <div className="scrim" onClick={() => setSidebarOpen(false)} />}
      <nav className={`sidebar ${sidebarOpen ? "open" : ""}`} aria-label="Chats">
        <div className="sidebar-head">
          <button className="brand" onClick={() => openChat(null)}><Logo /> SLQ Hub</button>
          <span className="grow" />
          <button className="icon-btn menu-close" onClick={() => setSidebarOpen(false)} aria-label="Close the menu"
            style={{ display: sidebarOpen ? undefined : "none" }}><X size={18} /></button>
        </div>
        <div className="sidebar-actions">
          <button className="btn" onClick={() => openChat(null)}><MessageSquarePlus size={16} /> New chat</button>
        </div>
        <div style={{ padding: "0 12px 8px" }}>
          <label className="row" style={{ gap: 6 }}><Search size={15} className="faint" />
            <input className="input" placeholder="Search your chats" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        </div>
        <div className="sidebar-scroll">
          {chats.length === 0 && <div className="side-more" style={{ padding: "2px 16px" }}>Your chats are kept on this device.</div>}
          {groups.map((g) => (
            <div className="side-group" key={g.label}>
              <div className="side-group-head">{g.label}</div>
              {g.items.map((c) => (
                <a key={c.id} className="side-item" href={`#c=${c.id}`} onClick={(e) => { e.preventDefault(); openChat(c.id); }}
                  aria-current={chatId === c.id ? "page" : undefined} title={c.title}>
                  <span className="ellipsis">{c.title}</span>
                  {busy?.chat === c.id && <Lamp state="run" size="sm" pulse title="Working" />}
                </a>
              ))}
            </div>
          ))}
        </div>
        <div className="side-foot">
          <div className="side-links">
            <MenuButton up className="side-item" label={<><UserIcon size={15} /> <span className="ellipsis">{user.display_name || user.username}</span></>}
              title="Your account">
              {(close) => (
                <>
                  <div className="menu-head">Chats on this device</div>
                  <button className="menu-item" onClick={() => { close(); exportAll(); }}><Download size={15} />
                    <span>Export all chats<small>One file with every chat, its pictures and files</small></span></button>
                  <button className="menu-item" onClick={() => { close(); importInput.current?.click(); }}><UploadIcon size={15} />
                    <span>Import chats<small>From an export file, e.g. from your other device</small></span></button>
                  <div className="menu-sep" />
                  <button className="menu-item" onClick={() => { close(); setAccount(true); }}><KeyRound size={15} /> Change password</button>
                  <button className="menu-item" onClick={() => { close(); signOut(); }}><LogOut size={15} /> Sign out</button>
                </>
              )}
            </MenuButton>
            <input ref={importInput} type="file" accept="application/json,.json" hidden
              onChange={(e) => { importAll(e.target.files?.[0]); e.target.value = ""; }} />
          </div>
          <GpuPlate />
        </div>
      </nav>

      <div className="main">
        <div className="topbar">
          <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open the menu"><Menu size={18} /></button>
          {renaming && chat ? (
            <input className="input" autoFocus defaultValue={chat.meta.title} style={{ maxWidth: 420 }}
              onBlur={(e) => rename(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") rename((e.target as HTMLInputElement).value); if (e.key === "Escape") setRenaming(false); }} />
          ) : (
            <h1 className="ellipsis" onDoubleClick={() => chat && setRenaming(true)}>{chat?.meta.title ?? "New chat"}</h1>
          )}
          <span className="grow" />
          {chat && <button className="btn quiet small" onClick={() => setPanel({ kind: "files" })}><FolderOpen size={15} /> Files</button>}
          {chat && (
            <MenuButton right className="icon-btn" label={<MoreHorizontal size={18} />} title="More">
              {(close) => (
                <>
                  <button className="menu-item" onClick={() => { close(); setRenaming(true); }}><Pencil size={15} /> Rename</button>
                  {Object.values(chat.data.artifacts).map((v) => v[v.length - 1]).map((a) => (
                    <button key={a.id} className="menu-item" onClick={() => { close(); setPanel({ kind: "artifact", id: a.id }); }}>
                      <span className="tag">artifact</span> {a.title}</button>
                  ))}
                  <div className="menu-sep" />
                  <button className="menu-item" style={{ color: "var(--fault)" }} onClick={() => { close(); remove(chat.meta.id); }}>
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
            {!chat && (
              <div className="welcome">
                <h2>What are we working on?</h2>
                <p>Ask, attach files or pictures, ask for a picture, or have it build something. Your chats are kept on this
                  device; the Hub only answers.</p>
              </div>
            )}
            <div className="messages">
              {turns.map((t, i) => t.type === "user" ? (
                <UserMessage key={`u${t.message.id}`} message={t.message} editing={editing === t.message.id}
                  canEdit={!busy} onEdit={() => setEditing(t.message.id)} onCancel={() => setEditing(null)}
                  onSave={(text) => saveEdit(t.message.id, text)} />
              ) : (
                <div className="msg-assistant" key={t.key}>
                  <Items items={t.items} actions={{ openArtifact }} />
                  <TurnFoot turn={t} names={modelNames} canRetry={!busy && t === lastAssistant}
                    stats={!t.runId || lastPart.get(t.runId) === i} onRetry={retry} />
                </div>
              ))}
              {liveHere && (() => {
                const segs = liveSegments(liveHere.items);
                const state = <LiveState live={liveHere} queueCurrent={queue?.current ?? null} names={modelNames} />;
                return <>
                  {segs.map((seg, i) => seg.kind === "assistant" ? (
                    <div className="msg-assistant" key={`la${i}`}>
                      <Items items={seg.items} actions={{ openArtifact, live: true }} />
                      {i === segs.length - 1 && state}
                    </div>
                  ) : null)}
                  {!segs.length && <div className="msg-assistant">{state}</div>}
                  {saving && <div className="run-state saving-note"><Lamp state="wait" pulse /> Keeping the answer on this device</div>}
                </>;
              })()}
            </div>
          </div>
        </div>

        <div className="composer-wrap">
          <div className="column">
            <Composer running={running} onSend={send} onStop={stop} settings={effective} onSettings={changeSettings}
              models={llms} pictureModels={pictureModels} pictureDefault={pictureDefault} libraries={[]} skills={skills}
              autoFocus showLibraries={false} uploader={uploader}
              placeholder={busy ? "Wait for the answer, or stop it" : undefined} />
            <div className="hint">{running ? "The square stops it."
              : "Enter sends, Shift+Enter makes a new line. Paste or drop files to attach them."}</div>
          </div>
        </div>
      </div>

      {panel?.kind === "artifact" && (
        <ArtifactPanel chatId={0} id={panel.id} version={panel.version} loader={loadArtifact} onClose={() => setPanel(null)} />
      )}
      {panel?.kind === "files" && chat && <LocalFiles chat={chat.meta.id} version={chat.meta.updated}
        onClose={() => setPanel(null)} />}
      {account && <PasswordModal onClose={() => setAccount(false)} />}
      <Toasts />
    </div>
  );
}

function LocalFiles({ chat, version, onClose }: { chat: string; version: number; onClose: () => void }) {
  const { toast } = useApp();
  const [files, setFiles] = useState<S.LocalFile[] | null>(null);
  const load = useCallback(() => {
    S.listFiles(chat).then(setFiles, () => setFiles([]));
  }, [chat, version]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(load, [load]);
  const save = (f: S.LocalFile) => {
    const url = URL.createObjectURL(f.blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = f.path.split("/").pop() || "file";
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 30000);
  };
  const del = async (f: S.LocalFile) => {
    try {
      await S.deleteFile(chat, f.path);
      load();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <aside className="panel" aria-label="This chat's files">
      <div className="panel-head">
        <h2>This chat's files</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button>
      </div>
      <div className="panel-body" style={{ padding: 12 }}>
        <p className="small muted" style={{ marginTop: 0 }}>Kept on this device and sent along with each message, so the
          assistant can read and change them. What it makes comes back here.</p>
        {files === null ? null : files.length === 0 ? <p className="muted">No files yet.</p> : (
          <div className="list">
            {files.map((f) => (
              <div className="list-row" key={f.path}>
                <FileText size={15} />
                <span className="grow ellipsis" title={f.path}>{f.path.replace(/^\.hub\/attachments\//, "")}
                  <div className="meta">{bytes(f.size)}{f.path.startsWith(".hub/attachments/") ? ", attached" : ""}</div></span>
                <button className="icon-btn small" onClick={() => save(f)} aria-label={`Download ${f.path}`}><Download size={15} /></button>
                <button className="icon-btn small" onClick={() => del(f)} aria-label={`Delete ${f.path}`}><Trash2 size={15} /></button>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

function PasswordModal({ onClose }: { onClose: () => void }) {
  const { toast } = useApp();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const save = async () => {
    try {
      await api("/hub/api/auth/password", { body: { current, new: next } });
      toast("Password changed", "run");
      onClose();
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };
  return (
    <Modal title="Change password" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" onClick={save} disabled={!current || next.length < 10}>Change</button></>}>
      <label className="field"><span>Current password</span>
        <input className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></label>
      <label className="field"><span>New password</span>
        <input className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        <small>At least 10 characters.</small></label>
    </Modal>
  );
}
