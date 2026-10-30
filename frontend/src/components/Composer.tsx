// Where messages are written: text, attachments (pick, paste, drop), files
// of the chat's folder, the model, how hard it thinks, the libraries and the
// skills for this chat (0.23.0: picked by hand - the Skills button, or / and a
// skill's name in the message box).
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, BookOpen, Brain, Check, ChevronDown, ChevronRight, Cloud, Cpu, FileText, FolderOpen, Image as ImageIcon,
  Paperclip, Plus, Settings2, Sparkles, Square, Wand2, X } from "lucide-react";
import { upload, type AnswerItem, type AnswersBlock, type Lora, type ModelEntry, type Skill, type Upload } from "../lib/api";
import { byCategory, NO_CATEGORY, useFolders } from "../lib/skills";
import { LEVEL_LABEL, LEVELS, levelText, type Level } from "../lib/effort";
import { clearDraft, NEW_SKILL_TEXT, peekDraft, SKILL_MAKER } from "../lib/draft";
import { bytes } from "../lib/format";
import { Lamp, MenuButton } from "./ui";
import { allAnswered, QuestionList } from "./QuestionCard";

export interface ComposerSettings {
  model: string;
  thinking: boolean;
  effort: string;
  level: Level;                    // effort (0.38.0): quick | think | extra
  skills: string[];
  libraries: number[];
  image_model: string | null;      // null: the project's or Settings' model for pictures
  image_loras: string[];           // 0.49.0: the admin's LoRAs on every picture in this chat
  image_lora_strength: Record<string, number>;   // 0.54.0: their strength in this chat (else the LoRA's own)
}

/** A LoRA's strength in this chat (0.54.0, Søren: "a slider when I choose it"). Saved when let go, not on every
 *  move; at the LoRA's own strength nothing is saved, so a later change in its settings still applies. */
function LoraStrength({ lora, value, onCommit }: { lora: Lora; value: number | undefined;
  onCommit: (v: number | null) => void }) {
  const own = lora.strength;
  const [lo, hi, step] = lora.slider ? [-10, 10, 0.1] : [0, 2, 0.05];    // a slider LoRA goes both ways
  const [v, setV] = useState(value ?? own);
  useEffect(() => setV(value ?? own), [value, own]);
  const commit = () => {
    const next = Math.abs(v - own) < 0.001 ? null : v;
    if (next !== (value ?? null)) onCommit(next);
  };
  return (
    <div className="menu-slider" onClick={(e) => e.stopPropagation()} title="Strength in this chat">
      <input type="range" min={lo} max={hi} step={step} value={v} aria-label={`Strength of ${lora.name}`}
        onChange={(e) => setV(Number(e.target.value))} onPointerUp={commit} onKeyUp={commit} onBlur={commit} />
      <span className="menu-slider-value">{lora.slider ? `${v > 0 ? "+" : ""}${v.toFixed(1)}` : v.toFixed(2)}</span>
      {value !== undefined && <button type="button" className="link-btn" title={`Its own strength: ${own}`}
        onClick={() => onCommit(null)}>Reset</button>}
    </div>
  );
}

interface Pending { key: number; name: string; size: number; preview: string | null; state: "uploading" | "done" | "failed";
  upload?: Upload; error?: string }

const EFFORT_LABEL: Record<string, string> = { low: "Think a little", medium: "Think", xhigh: "Think hard", high: "Think hard" };

/** The end of a path, enough to tell files apart: "POUs/MAIN.TcPOU". */
const shortPath = (p: string) => p.split("/").slice(-2).join("/");

export function Composer({ running, onSend, onStop, settings, onSettings, models, pictureModels = [], pictureDefault,
  libraries, skills, placeholder, autoFocus, refs = [], onRemoveRef, onOpenRef, onBrowseFiles, browseLabel,
  uploader = upload, showLibraries = true, onManageSkills, reviewOnExtra = true, answered = {}, loras = [] }: {
  loras?: Lora[];                  // 0.49.0: the admin's LoRAs (only the admin's chat passes them)
  running: boolean;
  /** answers: a chosen skill's questions answered on its card (0.43.0), to go with the message. */
  onSend: (text: string, uploads: Upload[], answers: AnswersBlock[]) => Promise<boolean>;
  answered?: Record<string, AnswerItem[]>;   // the latest answers to each skill's questions in this chat
  onStop: () => void;
  settings: ComposerSettings;
  onSettings: (patch: Partial<ComposerSettings>) => void;
  models: ModelEntry[];
  reviewOnExtra?: boolean;          // Extra also has the independent review (not in a person's chats)
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
  // A chosen skill's fixed questions (0.43.0): a card above the message box until they are answered and sent;
  // then a line with the answers, to change for the next message. Answers already in this chat start closed.
  const [qa, setQa] = useState<Record<string, { answers: string[][]; open: boolean }>>({});
  const asking = chosen.filter((k) => (k.questions ?? []).length > 0);
  const cardOf = (k: Skill) => {
    if (qa[k.ref]) return qa[k.ref];
    const qs = k.questions ?? [];
    const before = answered[k.ref];
    return before ? { answers: qs.map((q) => before.find((it) => it.q === q.text)?.a ?? q.chosen), open: false }
      : { answers: qs.map((q) => q.chosen), open: true };
  };
  const setCard = (k: Skill, patch: Partial<{ answers: string[][]; open: boolean }>) =>
    setQa((all) => ({ ...all, [k.ref]: { ...cardOf(k), ...patch } }));
  const openCards = asking.filter((k) => cardOf(k).open);
  const unanswered = openCards.filter((k) => !allAnswered(k.questions ?? [], cardOf(k).answers));
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
  // A LoRA's trigger word into the message, where the caret is (0.57.0: Søren wanted it at hand when a LoRA is on;
  // the Hub puts it in the picture prompt anyway, this is for writing with it)
  const insertWord = (word: string) => {
    const el = ta.current;
    const at = el && document.activeElement === el ? el.selectionStart ?? text.length : text.length;
    const before = text.slice(0, at);
    const after = text.slice(at);
    const lead = before && !/\s$/.test(before) ? " " : "";
    const trail = after && !/^\s/.test(after) ? " " : "";
    setText(before + lead + word + trail + after);
    const pos = (before + lead + word + trail).length;
    requestAnimationFrame(() => {
      const e = ta.current;
      if (e) {
        e.focus();
        e.setSelectionRange(pos, pos);
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
  const hasContent = text.trim().length > 0 || ready.length > 0 || refs.length > 0 || openCards.length > 0;
  // While a turn works, a message waits for it: the model reads it after the step it is on.
  const canSend = !sending && !uploading && hasContent && unanswered.length === 0;

  const send = async () => {
    if (!canSend) return;
    setSending(true);
    const answers: AnswersBlock[] = openCards.map((k) => ({ type: "answers", skill: k.ref, title: k.title,
      items: (k.questions ?? []).map((q, i) => ({ q: q.text, a: cardOf(k).answers[i] ?? [] })) }));
    const ok = await onSend(text, ready, answers);
    setSending(false);
    if (ok) {
      if (openCards.length) setQa((all) => ({ ...all, ...Object.fromEntries(openCards.map((k) =>
        [k.ref, { ...cardOf(k), open: false }])) }));
      setText("");
      pending.forEach((p) => p.preview && URL.revokeObjectURL(p.preview));
      setPending([]);
    }
  };

  const model = models.find((m) => m.id === settings.model) ?? models[0];
  const efforts = model?.thinking?.efforts ?? [];
  const canThink = (model?.capabilities ?? []).includes("thinking");
  const extras = settings.libraries.length;
  const groups = byCategory(skills);                 // by category (0.41.0), as on the Skills page
  const pictureId = settings.image_model ?? pictureDefault;
  const picture = pictureModels.find((m) => m.id === pictureId) ?? pictureModels[0];
  const pictureDefaultName = pictureModels.find((m) => m.id === pictureDefault)?.name ?? pictureDefault;
  const fits = (l: Lora) => l.fits.some((m) => m.id === picture?.id);                  // this chat's picture model
  const fitting = loras.filter(fits);
  const lorasOn = fitting.filter((l) => settings.image_loras.includes(l.id));
  // 0.59.0: on in this chat but for another family than its picture model - left out, so shown as such
  const unfitOn = loras.filter((l) => !fits(l) && settings.image_loras.includes(l.id));
  const unfitText = (l: Lora) => !l.base ? "No model family set - choose one in its settings (Settings, Models)"
    : l.fits.length ? `For ${l.base_label} models - choose ${l.fits.map((m) => m.name).join(" or ")} above`
      : `For ${l.base_label} models - none of those is downloaded`;
  const toggleLora = (id: string) => onSettings({ image_loras: settings.image_loras.includes(id)
    ? settings.image_loras.filter((x) => x !== id) : [...settings.image_loras, id] });
  const setStrength = (id: string, v: number | null) => {
    const next = { ...settings.image_lora_strength };
    if (v === null) delete next[id];
    else next[id] = Math.round(v * 100) / 100;
    onSettings({ image_lora_strength: next });
  };
  const strengthOf = (l: Lora) => settings.image_lora_strength[l.id] ?? l.strength;

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
      {asking.map((k) => {
        const card = cardOf(k);
        const qs = k.questions ?? [];
        const done = qs.filter((_, i) => (card.answers[i] ?? []).length > 0).length;
        if (!card.open) return (
          <div className="qcard compact" key={`q:${k.ref}`}>
            <Sparkles size={14} /><b>{k.title}</b>
            <span className="ellipsis">{done ? card.answers.map((a) => a.join(", ") || "-").join(" · ") : "Questions not answered"}</span>
            <button className="btn small" onClick={() => setCard(k, { open: true })}>{done ? "Change" : "Answer"}</button>
          </div>
        );
        return (
          <div className="qcard" key={`q:${k.ref}`} role="group" aria-label={`Questions of ${k.title}`}>
            <div className="qcard-head"><Sparkles size={14} /><b className="grow ellipsis">{k.title}</b>
              <span className="faint small">{done} of {qs.length} answered</span>
              <button className="icon-btn small" title="Not now - send without the answers" aria-label="Not now"
                onClick={() => setCard(k, { open: false })}><X size={13} /></button></div>
            <QuestionList questions={qs} answers={card.answers} onChange={(answers) => setCard(k, { answers })} />
          </div>
        );
      })}
      {(pending.length > 0 || refs.length > 0 || chosen.length > 0 || lorasOn.length > 0 || unfitOn.length > 0) && (
        <div className="pending">
          {lorasOn.map((l) => (
            <div className="pending-item lora" key={`lora:${l.id}`}
              title={l.trigger ? `${l.name}: trigger word ${l.trigger} - put in the picture's prompt for you. Click the `
                + "word to write it in your message." : l.name}>
              <ImageIcon size={14} />
              <span className="ellipsis">{l.name}</span>
              {l.trigger && <button className="open trigger" onClick={() => insertWord(l.trigger!)}
                aria-label={`Write ${l.trigger} in the message`}>{l.trigger}</button>}
              <span className="faint">{l.slider ? `${strengthOf(l) > 0 ? "+" : ""}${strengthOf(l)}` : `×${strengthOf(l)}`}</span>
              <button className="icon-btn small x" aria-label={`Stop using the LoRA ${l.name} in this chat`}
                onClick={() => toggleLora(l.id)}><X size={13} /></button>
            </div>
          ))}
          {unfitOn.map((l) => (
            <div className="pending-item lora unfit" key={`lora:${l.id}`}
              title={`${l.name} is not used with ${picture?.name ?? "this chat's picture model"}. ${unfitText(l)}, or turn it off.`}>
              <ImageIcon size={14} />
              <span className="ellipsis">{l.name}</span>
              <span className="unfit-note small">not with {picture?.name ?? "this model"}</span>
              <button className="icon-btn small x" aria-label={`Stop using the LoRA ${l.name} in this chat`}
                onClick={() => toggleLora(l.id)}><X size={13} /></button>
            </div>
          ))}
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
      <textarea ref={ta} value={text} rows={1} placeholder={openCards.length ? "Anything to add? (optional)" : placeholder ?? "Write to the assistant"}
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
              <SkillFolders groups={groups} chosen={settings.skills}
                onToggle={(ref) => { toggleSkill(ref); close(); }} />
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
        {(pictureModels.length > 1 || loras.length > 0) && (
          <MenuButton up label={<><ImageIcon size={15} /> <span className="ellipsis chip-picture hide-narrow">{picture?.name ?? "Pictures"}
            {lorasOn.length ? ` + ${lorasOn.length} LoRA${lorasOn.length > 1 ? "s" : ""}` : ""}</span></>}
            title={`Model for pictures: ${picture?.name ?? "none"}${lorasOn.length ? `, with ${lorasOn.map((l) => `${l.name} (${strengthOf(l)})`).join(", ")}` : ""}${unfitOn.length ? `; not used with it: ${unfitOn.map((l) => l.name).join(", ")}` : ""}`}>
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
                {loras.length > 0 && <>
                  <div className="menu-sep" />
                  <div className="menu-head">LoRAs on every picture</div>
                  {fitting.map((l) => (
                    <div key={l.id}>
                      <button className="menu-item" role="menuitemcheckbox"
                        aria-checked={settings.image_loras.includes(l.id)} onClick={() => toggleLora(l.id)}>
                        {settings.image_loras.includes(l.id) ? <Check size={15} /> : <span style={{ width: 15 }} />}
                        <span>{l.name}<small>{l.slider ? "Slider: plus for more, minus for the opposite"
                          : [l.trigger && `Trigger word ${l.trigger}`, l.note].filter(Boolean).join(" · ")
                            || `Strength ${l.strength}`}</small></span>
                      </button>
                      {settings.image_loras.includes(l.id) && <LoraStrength lora={l}
                        value={settings.image_lora_strength[l.id]} onCommit={(v) => setStrength(l.id, v)} />}
                    </div>
                  ))}
                  {loras.filter((l) => !fits(l)).map((l) => {
                    const on = settings.image_loras.includes(l.id);       // on: can be turned off; off: cannot be used
                    return (
                      <button key={l.id} className="menu-item unfit" role="menuitemcheckbox" aria-checked={on}
                        disabled={!on} onClick={() => toggleLora(l.id)}>
                        {on ? <Check size={15} /> : <span style={{ width: 15 }} />}
                        <span>{l.name}<small>{on ? "Not used with this model - click to turn it off" : unfitText(l)}</small></span>
                      </button>
                    );
                  })}
                </>}
              </>
            )}
          </MenuButton>
        )}
        <MenuButton up className={`picker${settings.level !== "quick" ? " on" : ""}`}
          label={<><Brain size={15} /> <span className="hide-narrow">{LEVEL_LABEL[settings.level]}</span></>}
          title={`Effort: ${LEVEL_LABEL[settings.level]}`}>
          {(close) => (
            <>
              <div className="menu-head">Effort</div>
              {LEVELS.map((lv) => (
                <button key={lv} className="menu-item" role="menuitemradio" aria-checked={settings.level === lv}
                  disabled={lv === "think" && !canThink} onClick={() => { onSettings({ level: lv }); close(); }}>
                  {settings.level === lv ? <Check size={15} /> : <span style={{ width: 15 }} />}
                  <span>{LEVEL_LABEL[lv]}<small>{levelText(lv, canThink, reviewOnExtra)}</small></span></button>
              ))}
              {canThink && efforts.length > 0 && (                  /* a model with thinking levels: they set Think */
                <>
                  <div className="menu-sub">How hard Think thinks</div>
                  {efforts.map((e) => (
                    <button key={e} className="menu-item" role="menuitemradio"
                      aria-checked={settings.level === "think" && settings.effort === e}
                      onClick={() => { onSettings({ level: "think", effort: e }); close(); }}>
                      {settings.level === "think" && settings.effort === e ? <Check size={15} /> : <span style={{ width: 15 }} />}
                      <span>{EFFORT_LABEL[e] ?? e}</span></button>
                  ))}
                </>
              )}
            </>
          )}
        </MenuButton>
        <span className="spacer" />
        {running && <button className="send stop" onClick={onStop} aria-label="Stop" title="Stop"><Square size={14} fill="currentColor" /></button>}
        {(!running || hasContent) && (
          <button className="send" onClick={send} disabled={!canSend} aria-label={running ? "Send while it works" : "Send"}
            title={unanswered.length ? `Answer the questions of ${unanswered[0].title} first`
              : running ? "It reads this after the step it is on" : undefined}><ArrowUp size={18} /></button>
        )}
      </div>
    </div>
  );
}

/** The Skills menu's list (0.45.0): skills in no category first, then each category as a folder that opens when its
 *  name is clicked ("internal" on three quick clicks, closed again when the menu closes). */
function SkillFolders({ groups, chosen, onToggle }: { groups: [string, Skill[]][]; chosen: string[];
  onToggle: (ref: string) => void }) {
  const folders = useFolders("hub:skill-menu-open");
  const item = (k: Skill) => {
    const on = chosen.includes(k.ref);
    return (
      <button key={k.ref} className="menu-item" role="menuitemcheckbox" aria-checked={on} onClick={() => onToggle(k.ref)}>
        {on ? <Check size={15} /> : <Sparkles size={15} />}
        <span>{k.title}<small className="clamp-2">{k.mine || k.builtin ? "" : `${k.by} · `}{k.description}</small></span>
      </button>
    );
  };
  return (
    <>
      {groups.map(([cat, list]) => {
        if (cat === NO_CATEGORY) return <div key={cat}>{list.map(item)}</div>;
        const open = folders.isOpen(cat);
        const on = list.filter((k) => chosen.includes(k.ref)).length;
        return (
          <div key={cat}>
            <button type="button" className="menu-sub menu-folder" aria-expanded={open} onClick={() => folders.click(cat)}>
              {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<span className="grow">{cat}</span>
              <span className="faint">{on ? `${on} on · ` : ""}{list.length}</span>
            </button>
            {open && list.map(item)}
          </div>
        );
      })}
    </>
  );
}
