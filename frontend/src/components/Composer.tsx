// Where messages are written: text, attachments (pick, paste, drop), the
// model, how hard it thinks, and the libraries and skills for this chat.
import { useEffect, useRef, useState } from "react";
import { ArrowUp, BookOpen, Brain, Check, Cpu, Image as ImageIcon, Paperclip, Plus, Sparkles, Square, X } from "lucide-react";
import { upload, type ModelEntry, type Skill, type Upload } from "../lib/api";
import { bytes } from "../lib/format";
import { Lamp, MenuButton } from "./ui";

export interface ComposerSettings {
  model: string;
  thinking: boolean;
  effort: string;
  skills: string[];
  libraries: number[];
  image_model: string | null;      // null: the project's or Settings' model for pictures
}

interface Pending { key: number; name: string; size: number; preview: string | null; state: "uploading" | "done" | "failed";
  upload?: Upload; error?: string }

const EFFORT_LABEL: Record<string, string> = { low: "Think a little", medium: "Think", xhigh: "Think hard", high: "Think hard" };

export function Composer({ running, onSend, onStop, settings, onSettings, models, pictureModels = [], pictureDefault,
  libraries, skills, placeholder, autoFocus }: {
  running: boolean;
  onSend: (text: string, uploads: Upload[]) => Promise<boolean>;
  onStop: () => void;
  settings: ComposerSettings;
  onSettings: (patch: Partial<ComposerSettings>) => void;
  models: ModelEntry[];
  pictureModels?: ModelEntry[];     // downloaded models that make pictures
  pictureDefault?: string;          // what this chat uses when it has no choice of its own
  libraries: { id: number; name: string }[];
  skills: Skill[];
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [drag, setDrag] = useState(false);
  const [sending, setSending] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, window.innerHeight * 0.4)}px`;
  }, [text]);

  useEffect(() => {
    if (autoFocus && window.matchMedia("(pointer: fine)").matches) ta.current?.focus();
  }, [autoFocus]);

  const add = (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      const key = ++seq.current;
      const preview = f.type.startsWith("image/") ? URL.createObjectURL(f) : null;
      setPending((p) => [...p, { key, name: f.name || "pasted image", size: f.size, preview, state: "uploading" }]);
      upload(f, f.name || `pasted-${key}.png`).then(
        (u) => setPending((p) => p.map((x) => (x.key === key ? { ...x, state: "done", upload: u } : x))),
        (e) => setPending((p) => p.map((x) => (x.key === key ? { ...x, state: "failed", error: (e as Error).message } : x))),
      );
    }
  };

  const ready = pending.filter((p) => p.state === "done" && p.upload).map((p) => p.upload!);
  const uploading = pending.some((p) => p.state === "uploading");
  const canSend = !running && !sending && !uploading && (text.trim().length > 0 || ready.length > 0);

  const send = async () => {
    if (!canSend) return;
    setSending(true);
    const ok = await onSend(text, ready);
    setSending(false);
    if (ok) {
      setText("");
      pending.forEach((p) => p.preview && URL.revokeObjectURL(p.preview));
      setPending([]);
    }
  };

  const model = models.find((m) => m.id === settings.model) ?? models[0];
  const efforts = model?.thinking?.efforts ?? [];
  const canThink = (model?.capabilities ?? []).includes("thinking");
  const thinkLabel = !canThink || !settings.thinking ? "No thinking" : EFFORT_LABEL[settings.effort] ?? "Think";
  const extras = settings.libraries.length + settings.skills.length;
  const pictureId = settings.image_model ?? pictureDefault;
  const picture = pictureModels.find((m) => m.id === pictureId) ?? pictureModels[0];
  const pictureDefaultName = pictureModels.find((m) => m.id === pictureDefault)?.name ?? pictureDefault;

  return (
    <div className={`composer ${drag ? "drag" : ""}`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDrag(true); } }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) add(e.dataTransfer.files); }}>
      {pending.length > 0 && (
        <div className="pending">
          {pending.map((p) => (
            <div className="pending-item" key={p.key} title={p.error ?? p.name}>
              {p.preview ? <img src={p.preview} alt="" /> : <Paperclip size={14} />}
              <span className="ellipsis">{p.name}</span>
              <span className="faint">{bytes(p.size)}</span>
              {p.state !== "done" && <Lamp size="sm" state={p.state === "failed" ? "fault" : "wait"} pulse={false} />}
              <button className="icon-btn small x" aria-label={`Remove ${p.name}`}
                onClick={() => setPending((all) => all.filter((x) => x.key !== p.key))}><X size={13} /></button>
            </div>
          ))}
        </div>
      )}
      <textarea ref={ta} value={text} rows={1} placeholder={placeholder ?? "Write to the assistant"}
        onChange={(e) => setText(e.target.value)}
        onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); add(e.clipboardData.files); } }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && window.matchMedia("(pointer: fine)").matches) {
            e.preventDefault();
            send();
          }
        }} aria-label="Message" />
      <div className="composer-bar">
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
        <MenuButton up label={<><Plus size={16} />{extras > 0 && <span className="num">{extras}</span>}</>}
          title="Attach, libraries and skills">
          {(close) => (
            <>
              <button className="menu-item" onClick={() => { close(); fileInput.current?.click(); }}>
                <Paperclip size={15} /> Attach files</button>
              <div className="menu-sep" />
              <div className="menu-head">Libraries to search</div>
              {libraries.length === 0 && <div className="menu-item faint" style={{ cursor: "default" }}>No libraries yet</div>}
              {libraries.map((l) => {
                const on = settings.libraries.includes(l.id);
                return (
                  <button key={l.id} className="menu-item" role="menuitemcheckbox" aria-checked={on}
                    onClick={() => onSettings({ libraries: on ? settings.libraries.filter((x) => x !== l.id) : [...settings.libraries, l.id] })}>
                    {on ? <Check size={15} /> : <BookOpen size={15} />} {l.name}
                  </button>
                );
              })}
              {skills.some((s) => !s.enabled) && <>
                <div className="menu-sep" />
                <div className="menu-head">Extra skills for this chat</div>
                {skills.filter((s) => !s.enabled).map((s) => {
                  const on = settings.skills.includes(s.name);
                  return (
                    <button key={s.name} className="menu-item" role="menuitemcheckbox" aria-checked={on}
                      onClick={() => onSettings({ skills: on ? settings.skills.filter((x) => x !== s.name) : [...settings.skills, s.name] })}>
                      {on ? <Check size={15} /> : <Sparkles size={15} />}
                      <span>{s.title}<small>{s.description.slice(0, 90)}</small></span>
                    </button>
                  );
                })}
              </>}
            </>
          )}
        </MenuButton>
        <MenuButton up label={<><Cpu size={15} /> <span className="ellipsis" style={{ maxWidth: 150 }}>{model?.name ?? "No model"}</span></>}
          title="Model">
          {(close) => (
            <>
              <div className="menu-head">Model for this chat</div>
              {models.map((m) => (
                <button key={m.id} className="menu-item" role="menuitemradio" aria-checked={m.id === model?.id}
                  onClick={() => { onSettings({ model: m.id }); close(); }}>
                  {m.id === model?.id ? <Check size={15} /> : <Cpu size={15} />}
                  <span>{m.name}<small>{m.loaded ? "Loaded now" : "Switching to it takes a moment"}</small></span>
                </button>
              ))}
            </>
          )}
        </MenuButton>
        {pictureModels.length > 1 && (
          <MenuButton up label={<><ImageIcon size={15} /> <span className="ellipsis" style={{ maxWidth: 140 }}>{picture?.name ?? "Pictures"}</span></>}
            title="Model for pictures">
            {(close) => (
              <>
                <div className="menu-head">Pictures in this chat</div>
                <button className="menu-item" role="menuitemradio" aria-checked={settings.image_model == null}
                  onClick={() => { onSettings({ image_model: null }); close(); }}>
                  {settings.image_model == null ? <Check size={15} /> : <span style={{ width: 15 }} />}
                  <span>The default<small>{pictureDefaultName}</small></span></button>
                {pictureModels.map((m) => (
                  <button key={m.id} className="menu-item" role="menuitemradio" aria-checked={settings.image_model === m.id}
                    onClick={() => { onSettings({ image_model: m.id }); close(); }}>
                    {settings.image_model === m.id ? <Check size={15} /> : <ImageIcon size={15} />}
                    <span>{m.name}<small>{m.description?.split(". ")[0]}</small></span>
                  </button>
                ))}
              </>
            )}
          </MenuButton>
        )}
        {canThink && (
          <MenuButton up label={<><Brain size={15} /> {thinkLabel}</>} title="Thinking">
            {(close) => (
              <>
                <button className="menu-item" role="menuitemradio" aria-checked={!settings.thinking}
                  onClick={() => { onSettings({ thinking: false }); close(); }}>
                  {!settings.thinking ? <Check size={15} /> : <span style={{ width: 15 }} />}
                  <span>No thinking<small>Fastest; fine for most questions</small></span></button>
                {efforts.length === 0 && (                  /* a model that thinks on/off, with no effort levels */
                  <button className="menu-item" role="menuitemradio" aria-checked={!!settings.thinking}
                    onClick={() => { onSettings({ thinking: true }); close(); }}>
                    {settings.thinking ? <Check size={15} /> : <span style={{ width: 15 }} />}
                    <span>Think<small>Reasons before it answers</small></span></button>
                )}
                {efforts.map((e) => (
                  <button key={e} className="menu-item" role="menuitemradio" aria-checked={settings.thinking && settings.effort === e}
                    onClick={() => { onSettings({ thinking: true, effort: e }); close(); }}>
                    {settings.thinking && settings.effort === e ? <Check size={15} /> : <span style={{ width: 15 }} />}
                    <span>{EFFORT_LABEL[e] ?? e}<small>{e === "low" ? "A short look first" : e === "xhigh" ? "For hard problems; slow" : "Reasons before it answers"}</small></span>
                  </button>
                ))}
              </>
            )}
          </MenuButton>
        )}
        <span className="spacer" />
        {running ? (
          <button className="send stop" onClick={onStop} aria-label="Stop"><Square size={14} fill="currentColor" /></button>
        ) : (
          <button className="send" onClick={send} disabled={!canSend} aria-label="Send"><ArrowUp size={18} /></button>
        )}
      </div>
    </div>
  );
}
