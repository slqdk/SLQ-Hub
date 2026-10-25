// SLQ Chat: a small chat page for phones and tablets on the home network.
// It uses the model API with a key (hub.ts), keeps its conversations on the
// device (store.ts) and wears the Hub's look (styles.css + chat.css).
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ArrowUp, Brain, ChevronDown, ChevronRight, Copy, Download, Image as ImageIcon, ImagePlus, KeyRound, Menu,
  RotateCcw, Square, SquarePen, Trash2, X } from "lucide-react";
import { Markdown } from "../components/Markdown";
import { ThinkingText } from "../components/ThinkingText";
import { copyText } from "../lib/clipboard";
import { saveText } from "../lib/download";
import { HubError, keyFrom, listModels, makePicture, saveKey, SIZES, storedKey, streamChat, takeKeyFromLink,
  type ApiMessage, type HubModel, type Part, type SizeName } from "./hub";
import { photoFromFile, pictureName } from "./photos";
import { deleteChat, listChats, loadMessages, newId, persistent, saveChat, titleFor, type ChatMeta,
  type Msg } from "./store";

type Conn =
  | { state: "checking" }
  | { state: "connect"; error?: string }
  | { state: "blocked"; error: HubError }
  | { state: "ready"; key: string | null; models: HubModel[] };

export function PhoneChat() {
  const [conn, setConn] = useState<Conn>({ state: "checking" });

  const check = useCallback(async (key: string | null) => {
    setConn({ state: "checking" });
    try {
      const models = await listModels(key);
      if (key) saveKey(key);
      setConn({ state: "ready", key, models });
    } catch (e) {
      const err = e instanceof HubError ? e : new HubError("other", (e as Error).message);
      // No key yet (and not the Hub's signed-in owner), or the key stopped working: ask for one.
      // Not at home, or the Hub is down: say so, and keep the key for later.
      if (err.problem === "key") {
        setConn({ state: "connect", error: key ? err.message : undefined });
      } else {
        if (key) saveKey(key);              // from a link: kept, so Try again (and the next visit) has it
        setConn({ state: "blocked", error: err });
      }
    }
  }, []);

  useEffect(() => {
    check(takeKeyFromLink() ?? storedKey());
    // A link opened while the page is already open only changes the #part.
    const onHash = () => {
      const key = takeKeyFromLink();
      if (key) check(key);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [check]);

  if (conn.state === "ready") {
    return <ChatApp apiKey={conn.key} models={conn.models}
      onUseKey={() => setConn({ state: "connect" })}
      onForget={() => { saveKey(null); setConn({ state: "connect" }); }} />;
  }
  return (
    <div className="pc-gate">
      <div className="pc-card">
        <h1><img src="/icons/icon.svg" alt="" /> SLQ Chat</h1>
        {conn.state === "checking" && <p className="notice"><Lamp state="wait" pulse /> Connecting to the Hub</p>}
        {conn.state === "connect" && <Connect error={conn.error} onKey={(k) => check(k)} />}
        {conn.state === "blocked" && (
          <>
            <p className="notice fault"><Lamp state="fault" /> {conn.error.message}</p>
            <div className="row wrap">
              <button className="btn primary" onClick={() => check(storedKey())}>Try again</button>
              <button className="btn quiet" onClick={() => setConn({ state: "connect" })}>Use another key</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Connect({ error, onKey }: { error?: string; onKey: (key: string) => void }) {
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const key = keyFrom(text);
    if (!key) {
      setProblem("That is not a key. A key starts with slqhub_ (the whole link works too).");
      return;
    }
    onKey(key);
  };
  return (
    <form onSubmit={submit} className="stack">
      <p className="muted pc-lead">Chat with the AI on the Hub at home. Paste the key you were given, or the whole
        link.</p>
      <label className="field"><span>Key or link</span>
        <input className="input" value={text} onChange={(e) => { setText(e.target.value); setProblem(null); }}
          placeholder="slqhub_..." autoCapitalize="off" autoCorrect="off" autoComplete="off" spellCheck={false} autoFocus />
      </label>
      {(problem || error) && <p className="notice fault"><Lamp state="fault" /> {problem || error}</p>}
      <button className="btn primary" type="submit" disabled={!text.trim()}>Connect</button>
      <p className="small faint" style={{ margin: 0 }}>The key stays on this device. Ask whoever runs the Hub for one
        (Settings, Apps and keys); it works on the home network, or anywhere if it is set to work away from home.</p>
    </form>
  );
}

// ------------------------------------------------------------ the chat

function usePref<T>(name: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const s = localStorage.getItem(name);
      if (s !== null) return JSON.parse(s) as T;
    } catch {
      /* no storage, or an old value */
    }
    return initial;
  });
  const set = useCallback((v: T) => {
    setValue(v);
    try {
      localStorage.setItem(name, JSON.stringify(v));
    } catch {
      /* kept for this visit */
    }
  }, [name]);
  return [value, set];
}

interface Busy { replyId: string; chatId: string; controller: AbortController }
interface Toast { text: string; fault?: boolean }

function ChatApp({ apiKey, models: firstModels, onUseKey, onForget }: { apiKey: string | null; models: HubModel[];
  onUseKey: () => void; onForget: () => void }) {
  const [models, setModels] = useState(firstModels);
  const chatModels = models.filter((m) => m.kind === "chat");
  const canPicture = models.some((m) => m.kind === "image" && (m.abilities ?? ["generate"]).includes("generate"));
  const [modelId, setModelId] = usePref<string>("slq-chat-model", "");
  const model = chatModels.find((m) => m.id === modelId) ?? chatModels.find((m) => m.default) ?? chatModels[0];
  // "off", "on" (a model without levels) or the model's level: low, medium, xhigh. Was a yes/no before 0.10.1.
  const [thinking, setThinking] = usePref<string>("slq-chat-thinking", legacyThink() ? "medium" : "off");
  const think = levelFor(model, thinking);
  const [size, setSize] = usePref<SizeName>("slq-chat-size", "square");
  const [pictureMode, setPictureMode] = useState(false);
  const [chats, setChats] = useState<ChatMeta[]>([]);
  const [chat, setChat] = useState<{ meta: ChatMeta | null; messages: Msg[] }>({ meta: null, messages: [] });
  const [busy, setBusy] = useState<Busy | null>(null);
  const live = useRef<{ meta: ChatMeta; messages: Msg[] } | null>(null);
  const deleted = useRef(new Set<string>());
  const [draft, setDraft] = useState("");
  const [photos, setPhotos] = useState<string[]>([]);
  const [drawer, setDrawer] = useState(false);
  const [viewer, setViewer] = useState<{ src: string; at: number } | null>(null);
  const [kept, setKept] = useState(true);
  const [toast, setToast] = useState<Toast | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refreshChats = useCallback(() => {
    listChats().then(setChats, () => setKept(false));
  }, []);
  const refreshModels = useCallback(() => {
    listModels(apiKey).then(setModels, () => undefined);
  }, [apiKey]);

  useEffect(() => {
    persistent().then((ok) => {
      setKept(ok);
      refreshChats();
    });
  }, [refreshChats]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(t);
  }, [toast]);

  // A phone may drop the page while it is in the background: keep what has come so far.
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden" && live.current && !deleted.current.has(live.current.meta.id)) {
        saveChat({ ...live.current.meta, updated: Date.now() }, live.current.messages).catch(() => undefined);
      }
    };
    document.addEventListener("visibilitychange", onHide);
    return () => document.removeEventListener("visibilitychange", onHide);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setViewer(null);
      setDrawer(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Follow the answer while the reader is at the bottom.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [chat]);
  const onScroll = () => {
    const el = scroller.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const newChat = () => {
    setDrawer(false);
    setChat({ meta: null, messages: [] });
    setPictureMode(false);
    stick.current = true;
    window.setTimeout(() => textarea.current?.focus(), 0);
  };

  const openChat = async (meta: ChatMeta) => {
    setDrawer(false);
    stick.current = true;
    if (live.current?.meta.id === meta.id) {
      setChat({ meta: live.current.meta, messages: live.current.messages });
      return;
    }
    let messages: Msg[] = [];
    try {
      messages = await loadMessages(meta.id);
    } catch {
      setToast({ text: "This chat could not be read.", fault: true });
    }
    setChat({ meta, messages });
  };

  // The open chat as a file, everything included (photos and pictures too), for showing someone what happened.
  const exportChat = () => {
    if (!chat.meta) return;
    const data = {
      exported: { app: "SLQ Chat", at: new Date().toISOString(), format: 1, page: location.origin + location.pathname,
        browser: navigator.userAgent },
      settings: { model: model?.id ?? null, thinking: think, pictureSize: size, keptOnDevice: kept,
        key: apiKey ? `${apiKey.slice(0, 14)}...` : "signed in to the Hub" },
      models: models.map(({ id, name, kind, vision, thinking: canThink, efforts, default: isDefault, loaded }) =>
        ({ id, name, kind, vision, thinking: canThink, efforts, default: isDefault, loaded })),
      systemPrompt: systemPrompt(),
      answering: busy?.chatId === chat.meta.id,
      chat: chat.meta,
      messages: chat.messages,
    };
    saveText(JSON.stringify(data, null, 1), `slq-chat-${fileSlug(chat.meta.title)}-${new Date().toISOString().slice(0, 10)}.json`);
    setDrawer(false);
  };

  const removeChat = async (meta: ChatMeta) => {
    if (busy?.chatId === meta.id) busy.controller.abort();
    deleted.current.add(meta.id);
    await deleteChat(meta.id).catch(() => undefined);
    setChat((c) => (c.meta?.id === meta.id ? { meta: null, messages: [] } : c));
    refreshChats();
  };

  const run = async (meta: ChatMeta, list: Msg[]) => {
    const ask = list[list.length - 1];
    const use = model;
    const reply: Msg = { id: newId(), role: "assistant", kind: ask.kind, text: "", at: Date.now(),
      ...(ask.kind === "picture" ? { size: ask.size } : { model: use?.name }) };
    let msgs = [...list, reply];
    const controller = new AbortController();
    const started = Date.now();
    live.current = { meta, messages: msgs };
    setChat({ meta, messages: msgs });
    setBusy({ replyId: reply.id, chatId: meta.id, controller });
    saveChat(meta, list).then(refreshChats, () => setKept(false));   // the question is kept even if the page is dropped
    const update = (patch: Partial<Msg>) => {
      msgs = msgs.map((m) => (m.id === reply.id ? { ...m, ...patch } : m));
      live.current = { meta, messages: msgs };
      setChat((c) => (c.meta?.id === meta.id ? { meta, messages: msgs } : c));
    };
    try {
      if (ask.kind === "picture") {
        const picture = await makePicture(apiKey, ask.text, (ask.size as SizeName) || "square", controller.signal);
        update({ picture, status: "done" });
      } else {
        if (!use) throw new HubError("other", "The Hub has no chat model downloaded.");
        let text = "";
        let reasoning = "";
        await streamChat(apiKey, { model: use.id, messages: toApi(list, !!use.vision), ...thinkFields(use, thinking) },
          (d) => {
            if (d.content) text += d.content;
            if (d.reasoning) reasoning += d.reasoning;
            update(reasoning ? { text, reasoning } : { text });
          }, controller.signal);
        update({ status: "done" });
      }
    } catch (e) {
      if (controller.signal.aborted) update({ status: "stopped" });
      else update({ status: "error", error: (e as Error).message || "Something went wrong." });
    } finally {
      update({ seconds: Math.round((Date.now() - started) / 1000) });
      live.current = null;
      setBusy(null);
      if (!deleted.current.has(meta.id)) {
        const saved = { ...meta, updated: Date.now() };
        try {
          await saveChat(saved, msgs);
        } catch {
          setKept(false);
        }
        setChat((c) => (c.meta?.id === meta.id ? { meta: saved, messages: msgs } : c));
        refreshChats();
      }
      refreshModels();
    }
  };

  const picture = pictureMode && canPicture;
  const canSend = !busy && (picture ? !!draft.trim() : !!(draft.trim() || photos.length));

  const send = () => {
    if (!canSend) return;
    const text = draft.trim();
    const now = Date.now();
    const ask: Msg = { id: newId(), role: "user", kind: picture ? "picture" : "chat", text, at: now,
      ...(picture ? { size } : photos.length ? { photos } : {}) };
    const meta = chat.meta ?? { id: newId(), title: titleFor(text), created: now, updated: now };
    setDraft("");
    if (!picture) setPhotos([]);            // photos wait in the box while a picture is made
    stick.current = true;
    run(meta, [...chat.messages, ask]);
  };

  const retry = (replyId: string) => {
    if (busy || !chat.meta) return;
    const i = chat.messages.findIndex((m) => m.id === replyId);
    if (i > 0) run(chat.meta, chat.messages.slice(0, i));
  };

  const addPhotos = async (files: FileList | null) => {
    const list = Array.from(files ?? []).slice(0, Math.max(0, 4 - photos.length));
    for (const f of list) {
      try {
        const url = await photoFromFile(f);
        setPhotos((p) => (p.length < 4 ? [...p, url] : p));
      } catch (e) {
        setToast({ text: (e as Error).message, fault: true });
      }
    }
    if (fileInput.current) fileInput.current.value = "";
  };

  // The text box grows with the text (up to a limit set in CSS).
  useLayoutEffect(() => {
    const ta = textarea.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
  }, [draft]);

  const coarse = typeof matchMedia === "function" && matchMedia("(pointer: coarse)").matches;
  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Phones: Enter is a new line (the send button is right there). Keyboards: Enter sends, Shift+Enter breaks.
    if (e.key === "Enter" && !e.shiftKey && !coarse && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  const lastId = chat.messages[chat.messages.length - 1]?.id;
  const elsewhere = busy && busy.chatId !== chat.meta?.id ? chats.find((c) => c.id === busy.chatId) : undefined;

  return (
    <div className="pc-app">
      <header className="topbar pc-top">
        <button className="icon-btn" onClick={() => { refreshChats(); setDrawer(true); }} aria-label="Chats">
          <Menu size={19} /></button>
        <h1 className="grow ellipsis">{chat.meta?.title ?? "SLQ Chat"}</h1>
        <button className="icon-btn" onClick={newChat} aria-label="New chat" title="New chat"
          disabled={!chat.messages.length}><SquarePen size={18} /></button>
      </header>

      <div className="scroll" ref={scroller} onScroll={onScroll}>
        <div className="column pc-column">
          {chat.messages.length === 0 ? (
            <div className="welcome pc-welcome">
              <h2>What can I help with?</h2>
              <p>The answers come from the AI on the Hub at home. Your chats stay on this device.</p>
              {canPicture && (picture
                ? <p className="small faint">Picture is on: the Hub draws what you write. Tap <b>Picture ×</b> to chat
                  again.</p>
                : <p className="small faint">Turn on <b>Picture</b> below to have the Hub draw something.</p>)}
              {!kept && <p className="small faint">This browser keeps nothing: the chat is gone when the page closes.</p>}
            </div>
          ) : (
            <div className="messages">
              {chat.messages.map((m) => (
                <MessageView key={m.id} msg={m} live={busy?.replyId === m.id} last={m.id === lastId}
                  onRetry={busy ? undefined : retry} onView={setViewer} onToast={setToast} />
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="composer-wrap">
        <div className="column pc-column">
          {elsewhere && (
            <p className="pc-note"><Lamp state="run" pulse /> Answering in "{elsewhere.title}"
              <button className="btn small quiet" onClick={() => openChat(elsewhere)}>Show</button></p>
          )}
          {!busy && !picture && model && !model.loaded && chatModels.length > 0 && (
            <p className="pc-note">{model.name} is not loaded right now: the first answer takes a little longer.</p>
          )}
          <div className="composer">
            {photos.length > 0 && !picture && (
              <div className="pending">
                {photos.map((p, i) => (
                  <div className="pending-item" key={i}>
                    <img src={p} alt="" /> Photo {i + 1}
                    <button className="icon-btn small x" onClick={() => setPhotos((all) => all.filter((_, j) => j !== i))}
                      aria-label={`Remove photo ${i + 1}`}><X size={14} /></button>
                  </div>
                ))}
              </div>
            )}
            <textarea ref={textarea} value={draft} rows={1} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKeyDown}
              placeholder={picture ? "Describe the picture" : "Write a message"} aria-label="Message" />
            <div className="composer-bar">
              {!picture && model?.vision && (
                <>
                  <button className="icon-btn" onClick={() => fileInput.current?.click()} aria-label="Add a photo"
                    title="Add a photo" disabled={photos.length >= 4}><ImagePlus size={18} /></button>
                  <input ref={fileInput} type="file" accept="image/*" multiple hidden
                    onChange={(e) => addPhotos(e.target.files)} />
                </>
              )}
              {canPicture && !picture && (
                <button className="picker" aria-pressed={false} onClick={() => setPictureMode(true)}
                  title="Make a picture instead of an answer"><ImageIcon size={15} /> <span className="pc-label">Picture</span></button>
              )}
              {picture && (                        /* the way back is on the chip itself */
                <button className="picker on pc-mode" aria-pressed={true} onClick={() => setPictureMode(false)}
                  title="Back to chat" aria-label="Picture is on. Back to chat">
                  <ImageIcon size={15} /> <span>Picture</span> <X size={14} /></button>
              )}
              {picture && (
                <label className="picker pc-select" title="Shape">
                  <span>{SIZES[size]?.label ?? "Square"}</span>
                  <select value={size} onChange={(e) => setSize(e.target.value as SizeName)} aria-label="Shape">
                    {(Object.keys(SIZES) as SizeName[]).map((s) => <option key={s} value={s}>{SIZES[s].label}</option>)}
                  </select>
                </label>
              )}
              {!picture && model?.thinking && !(model.efforts ?? []).length && (
                <button className="picker" aria-pressed={think !== "off"} onClick={() => setThinking(think === "off" ? "on" : "off")}
                  title={think !== "off" ? "Thinks before answering (slower, better at hard questions)" : "Answers straight away"}>
                  <Brain size={15} /> <span className="pc-label">Think</span></button>
              )}
              {!picture && model?.thinking && (model.efforts ?? []).length > 0 && (
                <label className={`picker pc-select${think !== "off" ? " on" : ""}`} title={`Thinking: ${THINK_LABEL[think] ?? think}`}>
                  <Brain size={15} /> <span className="pc-label">{THINK_LABEL[think] ?? think}</span>
                  <select value={think} onChange={(e) => setThinking(e.target.value)} aria-label="Thinking">
                    <option value="off">No thinking</option>
                    {(model.efforts ?? []).map((e) => <option key={e} value={e}>{THINK_LABEL[e] ?? e}</option>)}
                  </select>
                </label>
              )}
              <span className="spacer" />
              {!picture && chatModels.length > 1 && model && (
                <label className="picker pc-select pc-model" title="Model">
                  <span className="ellipsis">{model.name}</span>
                  <select value={model.id} onChange={(e) => setModelId(e.target.value)} aria-label="Model">
                    {chatModels.map((m) => <option key={m.id} value={m.id}>{m.name}{m.loaded ? " (loaded)" : ""}</option>)}
                  </select>
                </label>
              )}
              {busy ? (
                <button className="send stop" onClick={() => busy.controller.abort()} aria-label="Stop" title="Stop">
                  <Square size={14} fill="currentColor" /></button>
              ) : (
                <button className="send" onClick={send} disabled={!canSend} aria-label={picture ? "Make the picture" : "Send"}>
                  <ArrowUp size={18} /></button>
              )}
            </div>
          </div>
        </div>
      </div>

      {drawer && <div className="pc-scrim" onClick={() => setDrawer(false)} />}
      <aside className={`sidebar pc-drawer ${drawer ? "open" : ""}`} aria-hidden={!drawer}>
        <div className="sidebar-head">
          <span className="brand"><img src="/icons/icon.svg" alt="" /> SLQ Chat</span>
          <span className="grow" />
          <button className="icon-btn" onClick={() => setDrawer(false)} aria-label="Close"><X size={18} /></button>
        </div>
        <div className="pc-actions">
          <button className="btn" onClick={newChat}><SquarePen size={15} /> New chat</button>
        </div>
        {chat.meta && (
          <div className="pc-actions">
            <button className="btn quiet" onClick={exportChat} title="Save this chat as a file, to show someone what happened">
              <Download size={15} /> Export this chat</button>
          </div>
        )}
        <div className="sidebar-scroll">
          {chats.length === 0 ? <p className="pc-empty">No chats yet.</p> : groups(chats).map(([label, items]) => (
            <div className="side-group" key={label}>
              <div className="side-group-head">{label}</div>
              {items.map((c) => (
                <ChatRow key={c.id} meta={c} active={c.id === chat.meta?.id} working={busy?.chatId === c.id}
                  onOpen={() => openChat(c)} onDelete={() => removeChat(c)} />
              ))}
            </div>
          ))}
        </div>
        <div className="side-foot">
          <p className="small faint" style={{ margin: 0 }}>{kept ? "Chats are kept on this device only."
            : "This browser keeps nothing: chats are gone when the page closes."}</p>
          <div className="row">
            <KeyRound size={14} className="faint" />
            <span className="small grow ellipsis">{apiKey ? <span className="mono">{apiKey.slice(0, 14)}...</span>
              : "Signed in to the Hub"}</span>
            {apiKey ? <ConfirmTap onConfirm={onForget} label="Forget the key" armedLabel="Sure? Forget it" />
              : <button className="btn small" onClick={onUseKey}>Use a key</button>}
          </div>
        </div>
      </aside>

      {viewer && (
        <div className="pc-viewer" role="dialog" aria-label="Picture" onClick={() => setViewer(null)}>
          <img src={viewer.src} alt="" onClick={(e) => e.stopPropagation()} />
          <div className="pc-viewer-bar" onClick={(e) => e.stopPropagation()}>
            <a className="btn" href={viewer.src} download={pictureName(viewer.src, viewer.at)}><Download size={15} /> Save</a>
            <button className="btn" onClick={() => setViewer(null)}><X size={15} /> Close</button>
          </div>
          <p className="pc-viewer-tip">Or press and hold the picture.</p>
        </div>
      )}

      {toast && <div className="toasts"><div className="toast" role="status"><Lamp state={toast.fault ? "fault" : "run"} /> {toast.text}</div></div>}
    </div>
  );
}

function ChatRow({ meta, active, working, onOpen, onDelete }: { meta: ChatMeta; active: boolean; working: boolean;
  onOpen: () => void; onDelete: () => void }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 3000);
    return () => window.clearTimeout(t);
  }, [armed]);
  return (
    <div className="pc-chat-row">
      <button className="side-item" aria-current={active ? "page" : undefined} onClick={onOpen}>
        <span className="ellipsis">{meta.title}</span>
        {working && <Lamp state="run" pulse />}
      </button>
      <button className={`icon-btn small ${armed ? "armed" : ""}`} aria-label={armed ? "Delete the chat" : `Delete ${meta.title}`}
        onClick={() => (armed ? onDelete() : setArmed(true))}>{armed ? "Delete?" : <Trash2 size={14} />}</button>
    </div>
  );
}

function ConfirmTap({ onConfirm, label, armedLabel }: { onConfirm: () => void; label: string; armedLabel: string }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 3000);
    return () => window.clearTimeout(t);
  }, [armed]);
  return <button className={`btn small ${armed ? "danger" : ""}`} onClick={() => (armed ? onConfirm() : setArmed(true))}>
    {armed ? armedLabel : label}</button>;
}

function fileSlug(title: string): string {
  const t = title.toLowerCase().replace(/æ/g, "ae").replace(/ø/g, "oe").replace(/å/g, "aa");
  return t.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "") || "chat";
}

function groups(chats: ChatMeta[]): [string, ChatMeta[]][] {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const today = start.getTime();
  const yesterday = today - 86400000;
  const week = today - 6 * 86400000;
  const out: [string, ChatMeta[]][] = [["Today", []], ["Yesterday", []], ["This week", []], ["Earlier", []]];
  for (const c of chats) {
    const i = c.updated >= today ? 0 : c.updated >= yesterday ? 1 : c.updated >= week ? 2 : 3;
    out[i][1].push(c);
  }
  return out.filter(([, items]) => items.length > 0);
}

// ------------------------------------------------------------ one message

const MessageView = memo(function MessageView({ msg, live, last, onRetry, onView, onToast }: { msg: Msg; live: boolean;
  last: boolean; onRetry?: (id: string) => void; onView: (v: { src: string; at: number }) => void;
  onToast: (t: Toast) => void }) {
  if (msg.role === "user") {
    return (
      <div className="msg-user">
        {msg.kind === "picture" && <span className="msg-note"><ImageIcon size={13} /> Picture{msg.size && msg.size !== "square"
          ? `, ${SIZES[msg.size as SizeName]?.label.toLowerCase() ?? msg.size}` : ""}</span>}
        {msg.photos && msg.photos.length > 0 && (
          <div className="attachments">
            {msg.photos.map((p, i) => (
              <button key={i} className="thumb pc-thumb" onClick={() => onView({ src: p, at: msg.at })} aria-label={`Photo ${i + 1}`}>
                <img src={p} alt="" /></button>
            ))}
          </div>
        )}
        {msg.text && <div className="bubble">{msg.text}</div>}
      </div>
    );
  }
  const interrupted = !live && !msg.status;
  const canRetry = last && !!onRetry && (msg.status === "error" || msg.status === "stopped" || interrupted);
  const retry = canRetry ? <button className="btn small" onClick={() => onRetry!(msg.id)}><RotateCcw size={14} /> Try again</button> : null;

  if (msg.kind === "picture") {
    const ratio = msg.size === "portrait" ? "832 / 1216" : msg.size === "landscape" ? "1216 / 832" : "1 / 1";
    return (
      <div className="msg-assistant">
        {msg.picture ? (
          <button className="pc-picture" onClick={() => onView({ src: msg.picture!, at: msg.at })} aria-label="Open the picture">
            <img src={msg.picture} alt="The picture the Hub made" /></button>
        ) : live ? (
          <div className="pc-canvas" style={{ aspectRatio: ratio }}>
            <span className="notice"><Lamp state="run" pulse /> Painting <Seconds since={msg.at} /></span>
          </div>
        ) : null}
        <Status msg={msg} interrupted={interrupted}>{retry}</Status>
        {msg.picture && msg.seconds != null && <div className="msg-foot"><span className="stats">{msg.seconds} s</span></div>}
      </div>
    );
  }

  const waiting = live && !msg.text && !msg.reasoning;
  return (
    <div className="msg-assistant">
      {msg.reasoning && <Thinking text={msg.reasoning} live={live && !msg.text} />}
      {waiting && <p className="notice"><Lamp state="wait" pulse /> Waiting for the model <Seconds since={msg.at} /></p>}
      {msg.text && <Markdown text={msg.text} live={live} />}
      <Status msg={msg} interrupted={interrupted}>{retry}</Status>
      {!live && msg.text && (
        <div className="msg-foot">
          <button className="icon-btn small" aria-label="Copy the answer" title="Copy"
            onClick={async () => onToast((await copyText(msg.text)) ? { text: "Copied" } : { text: "Copying failed", fault: true })}><Copy size={14} /></button>
          <span className="stats">{[msg.model, msg.seconds != null ? `${msg.seconds} s` : ""].filter(Boolean).join(", ")}</span>
        </div>
      )}
    </div>
  );
});

function Status({ msg, interrupted, children }: { msg: Msg; interrupted: boolean; children?: ReactNode }) {
  if (msg.status === "error") {
    return <div className="pc-status"><p className="notice fault"><Lamp state="fault" /> {msg.error}</p>{children}</div>;
  }
  if (msg.status === "stopped" || interrupted) {
    return <div className="pc-status"><p className="notice">{interrupted ? "Interrupted." : "Stopped."}</p>{children}</div>;
  }
  return null;
}

function Thinking({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false);
  const words = text.trim().split(/\s+/).length;
  return (
    <div className="thinking">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open || live}>
        <Brain size={15} /> {live ? "Thinking" : `Thought for ${words} words`}
        {open || live ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {(open || live) && <ThinkingText text={text} live={live} />}
    </div>
  );
}

function Seconds({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  return <span className="num faint">{Math.max(0, Math.round((now - since) / 1000))} s</span>;
}

function Lamp({ state, pulse }: { state: "run" | "wait" | "fault" | "off"; pulse?: boolean }) {
  return <span className={`lamp ${pulse ? "pulse" : ""}`} data-state={state} aria-hidden="true" />;
}

// ------------------------------------------------------------ what the model is sent

const BUDGET = 60000;          // characters of history (about 15-20k tokens)
const PHOTO_TURNS = 2;         // photos go along from the last two messages that have some

function systemPrompt(): string {
  const date = new Date().toLocaleDateString("en-GB", { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  return `You are a helpful, friendly assistant on a private AI hub in the user's home. Today is ${date}. ` +
    "Answer in the language the user writes in. Use Markdown (lists, tables, code blocks) when it helps. " +
    "You cannot look things up on the internet; say so when a question needs current information.";
}

const THINK_LABEL: Record<string, string> = { off: "No thinking", on: "Think", low: "Think a little", medium: "Think",
  xhigh: "Think hard", high: "Think hard" };

/** The thinking choice as this model can take it: off, on (no levels), or one of its own levels. */
export function levelFor(m: HubModel | undefined, choice: string): string {
  if (!m?.thinking || choice === "off") return "off";
  const levels = m.efforts ?? [];
  if (!levels.length) return "on";
  if (levels.includes(choice)) return choice;
  if (choice === "high" && levels.includes("xhigh")) return "xhigh";
  return levels.includes("medium") ? "medium" : levels[Math.min(1, levels.length - 1)];
}

function thinkFields(m: HubModel, choice: string): Record<string, unknown> {
  if (!m.thinking) return {};
  const level = levelFor(m, choice);
  if (level === "off") return { thinking: false };
  return { reasoning_effort: level === "on" ? "medium" : level };
}

/** Before 0.10.1 the page kept only "think: yes/no". */
function legacyThink(): boolean {
  try {
    return localStorage.getItem("slq-chat-think") === "true";
  } catch {
    return false;
  }
}

/** The conversation as the model API wants it: newest first until the budget is spent. */
export function toApi(list: Msg[], vision: boolean): ApiMessage[] {
  const picked: ApiMessage[] = [];
  let chars = 0;
  let photoTurns = 0;
  for (let i = list.length - 1; i >= 0; i--) {
    const m = list[i];
    let msg: ApiMessage | null = null;
    if (m.role === "user") {
      const text = m.kind === "picture" ? `(I asked the picture model for: ${m.text})` : m.text;
      if (m.photos?.length && vision && photoTurns < PHOTO_TURNS) {
        photoTurns++;
        const parts: Part[] = m.photos.map((url) => ({ type: "image_url", image_url: { url } }));
        msg = { role: "user", content: [...parts, { type: "text", text: text || "(See the photo.)" }] };
      } else {
        const note = m.photos?.length ? `(${m.photos.length === 1 ? "A photo was" : `${m.photos.length} photos were`} shared here.)` : "";
        msg = { role: "user", content: [note, text].filter(Boolean).join("\n") };
      }
    } else if (m.kind === "picture") {
      if (m.picture) msg = { role: "assistant", content: "(The picture was made and shown.)" };
    } else if (m.text.trim()) {
      msg = { role: "assistant", content: m.text };
    }
    if (!msg) continue;
    const size = typeof msg.content === "string" ? msg.content.length
      : msg.content.reduce((n, p) => n + (p.type === "text" ? p.text.length : 1500), 0);
    if (picked.length && chars + size > BUDGET) break;
    chars += size;
    picked.unshift(msg);
  }
  // Neighbours with the same role are joined (a failed answer leaves two questions in a row),
  // and the conversation starts with the user.
  const merged: ApiMessage[] = [];
  for (const m of picked) {
    const prev = merged[merged.length - 1];
    if (prev && prev.role === m.role) prev.content = join(prev.content, m.content);
    else merged.push({ ...m });
  }
  while (merged.length && merged[0].role === "assistant") merged.shift();
  return [{ role: "system", content: systemPrompt() }, ...merged];
}

function join(a: string | Part[], b: string | Part[]): string | Part[] {
  if (typeof a === "string" && typeof b === "string") return `${a}\n\n${b}`;
  const parts = (x: string | Part[]): Part[] => (typeof x === "string" ? (x ? [{ type: "text", text: x }] : []) : x);
  return [...parts(a), ...parts(b)];
}
