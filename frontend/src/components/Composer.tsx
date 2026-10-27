// Where messages are written: text, attachments (pick, paste, drop), files
// of the chat's folder, the model, how hard it thinks, the libraries and the
// skills for this chat (0.23.0: picked by hand - the Skills button, or / and a
// skill's name in the message box).
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, BookOpen, Brain, Check, Cloud, Cpu, FileText, FolderOpen, Image as ImageIcon, Paperclip, Plus, Settings2,
  Sparkles, Square, Wand2, X } from "lucide-react";
import { upload, type ModelEntry, type Skill, type Upload } from "../lib/api";
import { clearDraft, NEW_SKILL_TEXT, peekDraft, SKILL_MAKER } from "../lib/draft";
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

/** The end of a path, enough to tell files apart: "POUs/MAIN.TcPOU". */
const shortPath = (p: string) => p.split("/").slice(-2).join("/");

export function Composer({ running, onSend, onStop, settings, onSettings, models, pictureModels = [], pictureDefault,
  libraries, skills, placeholder, autoFocus, refs = [], onRemoveRef, onOpenRef, onBrowseFiles, browseLabel,
  uploader = upload, showLibraries = true, onManageSkills }: {
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
  refs?: string[];                  // files of the chat's folder that go with the message
  onRemoveRef?: (path: string) => void;
  onOpenRef?: (path: string) => void;
  onBrowseFiles?: () => void;       // shows the folder's files beside the chat, to pick from
  browseLabel?: string;
  uploader?: (file: File, name: string) => Promise<Upload>;   // a person's chat keeps attachments on the device
  showLibraries?: boolean;
  onManageSkills?: () => void;      // the Skills page
}) {
  const [text, setText] = useState(peekDraft);      // an example from the help page, to change and send
  const [pending, setPending] = useState<Pending[]>([]);
  const [drag, setDrag] = useState(false);
  const [sending, setSending] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const seq = useRef(0);
  // "/" and a skill's name at the cursor: a list of skills to choose from (0.23.0).
  const [slash, setSlash] = useState<{ query: string; start: number; end: number } | null>(null);
  const [slashAt, setSlashAt] = useState(0);
  const chosen = useMemo(() => settings.skills.map((ref) => skills.find((k) => k.ref === ref)
    ?? { ref, title: ref.split("/").pop() ?? ref, description: "", mine: false, builtin: false } as Skill),
  [settings.skills, skills]);
  const matches = useMemo(() => {
    if (!slash) return [];
    const q = slash.query;
    return skills.filter((k) => !settings.skills.includes(k.ref)
      && (!q || `${k.title} ${k.name} ${k.description}`.toLowerCase().includes(q))).slice(0, 8);
  }, [slash, skills, settings.skills]);
  const findSlash = (value: string, caret: number) => {
    const m = value.slice(0, caret).match(/(?:^|\s)\/([\p{L}\p{N}._-]*)$/u);
    return m ? { query: m[1].toLowerCase(), start: caret - m[1].length - 1, end: caret } : null;
  };
  const pickSkill = (k: Skill) => {
    if (!slash) return;
    const next = text.slice(0, slash.start) + text.slice(slash.end);
    setText(next);
    setSlash(null);
    onSettings({ skills: [...settings.skills, k.ref] });
    requestAnimationFrame(() => {
      const el = ta.current;
      if (el) {
        el.focus();
        el.setSelectionRange(slash.start, slash.start);
      }
    });
  };
  const toggleSkill = (ref: string) => onSettings({ skills: settings.skills.includes(ref)
    ? settings.skills.filter((x) => x !== ref) : [...settings.skills, ref] });
  // Make a skill: the skill maker joins the chat, and an empty message box gets the start of the request.
  const makeSkill = () => {
    if (!settings.skills.includes(SKILL_MAKER)) onSettings({ skills: [...settings.skills, SKILL_MAKER] });
    const next = text.trim() ? text : NEW_SKILL_TEXT;
    setText(next);
    requestAnimationFrame(() => {
      const el = ta.current;
      if (el) {
        el.focus();
        el.setSelectionRange(next.length, next.length);
      }
    });
  };
  const canMake = skills.some((k) => k.ref === SKILL_MAKER);

  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, window.innerHeight * 0.4)}px`;
  }, [text]);

  useEffect(() => {
    if (autoFocus && window.matchMedia("(pointer: fine)").matches) ta.current?.focus();
  }, [autoFocus]);

  useEffect(() => {                                   // the help page's example is here now: put the cursor after it
    if (!peekDraft()) return;
    clearDraft();
    const el = ta.current;
    if (el) {
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  }, []);

  const add = (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      const key = ++seq.current;
      const preview = f.type.startsWith("image/") ? URL.createObjectURL(f) : null;
      setPending((p) => [...p, { key, name: f.name || "pasted image", size: f.size, preview, state: "uploading" }]);
      uploader(f, f.name || `pasted-${key}.png`).then(
        (u) => setPending((p) => p.map((x) => (x.key === key ? { ...x, state: "done", upload: u } : x))),
        (e) => setPending((p) => p.map((x) => (x.key === key ? { ...x, state: "failed", error: (e as Error).message } : x))),
      );
    }
  };

  const ready = pending.filter((p) => p.state === "done" && p.upload).map((p) => p.upload!);
  const uploading = pending.some((p) => p.state === "uploading");
  const hasContent = text.trim().length > 0 || ready.length > 0 || refs.length > 0;
  // While a turn works, a message waits for it: the model reads it after the step it is on.
  const canSend = !sending && !uploading && hasContent;

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
  const extras = settings.libraries.length;
  const groups: [string, Skill[]][] = [["Yours", skills.filter((k) => k.mine)],
    ["Shared by the family", skills.filter((k) => !k.mine && !k.builtin)], ["From SLQ Hub", skills.filter((k) => k.builtin)]];
  const pictureId = settings.image_model ?? pictureDefault;
  const picture = pictureModels.find((m) => m.id === pictureId) ?? pictureModels[0];
  const pictureDefaultName = pictureModels.find((m) => m.id === pictureDefault)?.name ?? pictureDefault;

  return (
    <div className={`composer ${drag ? "drag" : ""}`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDrag(true); } }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) add(e.dataTransfer.files); }}>
      {slash && matches.length > 0 && (
        <div className="slash-menu" role="listbox" aria-label="Skills">
          <div className="menu-head">Use a skill in this chat</div>
          {matches.map((k, i) => (
            <button key={k.ref} role="option" aria-selected={i === slashAt} className="menu-item"
              onMouseDown={(e) => { e.preventDefault(); pickSkill(k); }} onMouseEnter={() => setSlashAt(i)}>
              <Sparkles size={15} />
              <span>{k.title}<small className="clamp-2">{k.mine ? "" : `${k.by} · `}{k.description}</small></span>
            </button>
          ))}
        </div>
      )}
      {(pending.length > 0 || refs.length > 0 || chosen.length > 0) && (
        <div className="pending">
          {chosen.map((k) => (
            <div className="pending-item skill" key={`skill:${k.ref}`} title={k.description || k.title}>
              <Sparkles size={14} />
              <span className="ellipsis">{k.title}</span>
              <button className="icon-btn small x" aria-label={`Stop using the skill ${k.title} in this chat`}
                onClick={() => toggleSkill(k.ref)}><X size={13} /></button>
            </div>
          ))}
          {refs.map((path) => (
            <div className="pending-item ref" key={`ref:${path}`} title={path}>
              <FileText size={14} />
              <button className="open ellipsis" onClick={() => onOpenRef?.(path)} aria-label={`Show ${path}`}>{shortPath(path)}</button>
              <button className="icon-btn small x" aria-label={`Take ${path} out of the message`}
                onClick={() => onRemoveRef?.(path)}><X size={13} /></button>
            </div>
          ))}
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
        onChange={(e) => {
          setText(e.target.value);
          // (the list shows once there are skills that match: also when the list of skills arrives after the "/")
          const found = findSlash(e.target.value, e.target.selectionStart ?? e.target.value.length);
          setSlash(found);
          if (found?.query !== slash?.query) setSlashAt(0);
        }}
        onSelect={(e) => {
          const el = e.currentTarget;
          if (slash && (el.selectionStart !== slash.end || el.selectionEnd !== slash.end)) setSlash(findSlash(el.value, el.selectionStart));
        }}
        onBlur={() => setSlash(null)}
        onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); add(e.clipboardData.files); } }}
        onKeyDown={(e) => {
          if (slash && matches.length > 0) {                 // the skills list after "/"
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              setSlashAt((i) => (i + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length);
              return;
            }
            if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              pickSkill(matches[Math.min(slashAt, matches.length - 1)]);
              return;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setSlash(null);
              return;
            }
          }
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && window.matchMedia("(pointer: fine)").matches) {
            e.preventDefault();
            send();
          }
        }} aria-label="Message" />
      <div className="composer-bar">
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
        <MenuButton up label={<><Plus size={16} />{extras > 0 && <span className="num">{extras}</span>}</>}
          title={showLibraries ? "Attach and libraries" : "Attach files"}>
          {(close) => (
            <>
              <button className="menu-item" onClick={() => { close(); fileInput.current?.click(); }}>
                <Paperclip size={15} /> Attach files</button>
              {onBrowseFiles && (
                <button className="menu-item" onClick={() => { close(); onBrowseFiles(); }}>
                  <FolderOpen size={15} />
                  <span>{browseLabel ?? "Project files"}<small>Put files in the message, to change them or ask about them</small></span>
                </button>
              )}
              {showLibraries && <div className="menu-sep" />}
              {showLibraries && <div className="menu-head">Libraries to search</div>}
              {showLibraries && libraries.length === 0 && <div className="menu-item faint" style={{ cursor: "default" }}>No libraries yet</div>}
              {showLibraries && libraries.map((l) => {
                const on = settings.libraries.includes(l.id);
                return (
                  <button key={l.id} className="menu-item" role="menuitemcheckbox" aria-checked={on}
                    onClick={() => onSettings({ libraries: on ? settings.libraries.filter((x) => x !== l.id) : [...settings.libraries, l.id] })}>
                    {on ? <Check size={15} /> : <BookOpen size={15} />} {l.name}
                  </button>
                );
              })}
            </>
          )}
        </MenuButton>
        <MenuButton up className={`picker${chosen.length ? " on" : ""}`} menuClassName="wide"
          label={<><Sparkles size={15} /> <span className="hide-narrow">Skills</span>
            {chosen.length > 0 && <span className="num">{chosen.length}</span>}</>}
          title={chosen.length ? `Skills in this chat: ${chosen.map((k) => k.title).join(", ")}` : "Skills for this chat"}>
          {(close) => (
            <>
              <div className="menu-head">Skills for this chat</div>
              {skills.length === 0 && <div className="menu-item faint" style={{ cursor: "default" }}>No skills yet</div>}
              {groups.filter(([, list]) => list.length > 0).map(([label, list]) => (
                <div key={label}>
                  <div className="menu-sub">{label}</div>
                  {list.map((k) => {
                    const on = settings.skills.includes(k.ref);
                    return (
                      <button key={k.ref} className="menu-item" role="menuitemcheckbox" aria-checked={on}
                        onClick={() => toggleSkill(k.ref)}>
                        {on ? <Check size={15} /> : <Sparkles size={15} />}
                        <span>{k.title}<small className="clamp-2">{k.mine || k.builtin ? "" : `${k.by} · `}{k.description}</small></span>
                      </button>
                    );
                  })}
                </div>
              ))}
              {(canMake || onManageSkills) && <div className="menu-sep" />}
              {canMake && <button className="menu-item" onClick={() => { close(); makeSkill(); }}><Wand2 size={15} />
                <span>Make a skill<small>Attach your files and say what it is for; the assistant writes it</small></span></button>}
              {onManageSkills && <button className="menu-item" onClick={() => { close(); onManageSkills(); }}>
                <Settings2 size={15} /> All skills</button>}
              <div className="menu-foot faint small">Tip: type / in the message to pick a skill.</div>
            </>
          )}
        </MenuButton>
        <MenuButton up anchorClassName="shrink" label={<>{model?.kind === "cloud" ? <Cloud size={15} /> : <Cpu size={15} />} <span className="ellipsis chip-model">{model?.name ?? "No model"}</span></>}
          title={`Model: ${model?.name ?? "none"}`}>
          {(close) => (
            <>
              <div className="menu-head">Model for this chat</div>
              {models.map((m) => (
                <button key={m.id} className="menu-item" role="menuitemradio" aria-checked={m.id === model?.id}
                  onClick={() => { onSettings({ model: m.id }); close(); }}>
                  {m.id === model?.id ? <Check size={15} /> : m.kind === "cloud" ? <Cloud size={15} /> : <Cpu size={15} />}
                  <span>{m.name}<small>{m.kind === "cloud" ? "In the cloud: your card stays free, no review"
                    : m.loaded ? "Loaded now" : "Switching to it takes a moment"}</small></span>
                </button>
              ))}
            </>
          )}
        </MenuButton>
        {pictureModels.length > 1 && (
          <MenuButton up label={<><ImageIcon size={15} /> <span className="ellipsis chip-picture hide-narrow">{picture?.name ?? "Pictures"}</span></>}
            title={`Model for pictures: ${picture?.name ?? "none"}`}>
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
          <MenuButton up className={`picker${settings.thinking ? " on" : ""}`}
            label={<><Brain size={15} /> <span className="hide-narrow">{thinkLabel}</span></>} title={`Thinking: ${thinkLabel}`}>
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
        {running && <button className="send stop" onClick={onStop} aria-label="Stop" title="Stop"><Square size={14} fill="currentColor" /></button>}
        {(!running || hasContent) && (
          <button className="send" onClick={send} disabled={!canSend} aria-label={running ? "Send while it works" : "Send"}
            title={running ? "It reads this after the step it is on" : undefined}><ArrowUp size={18} /></button>
        )}
      </div>
    </div>
  );
}
