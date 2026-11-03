// Fields that save when they are left, and a play button for a sound (Movies 0.72.0, Personas 0.74.0).
import { useEffect, useRef, useState } from "react";
import { Play, Square } from "lucide-react";

/** Text that is saved when the field is left - and follows the series while it is not being written in. */
export function Draft({ value, onSave, multiline, rows = 2, placeholder, className, maxLength }: { value: string;
  onSave: (v: string) => void; multiline?: boolean; rows?: number; placeholder?: string; className?: string;
  maxLength?: number }) {
  const [draft, setDraft] = useState(value);
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setDraft(value);
  }, [value]);
  const common = {
    value: draft, placeholder, maxLength,
    onFocus: () => { focused.current = true; },
    onBlur: () => {
      focused.current = false;
      if (draft !== value) onSave(draft);
    },
  };
  return multiline
    ? <textarea className={className ?? "textarea"} rows={rows} {...common} style={{ minHeight: 0 }}
      onChange={(e) => setDraft(e.target.value)} />
    : <input className={className ?? "input"} {...common} onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />;
}

export function NumberDraft({ value, onSave, min, max, step }: { value: number; onSave: (v: number) => void; min: number;
  max: number; step: number }) {
  const [draft, setDraft] = useState(String(value));
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setDraft(String(value));
  }, [value]);
  return <input className="input" type="number" inputMode="decimal" min={min} max={max} step={step} value={draft}
    onFocus={() => { focused.current = true; }} onChange={(e) => setDraft(e.target.value)}
    onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    onBlur={() => {
      focused.current = false;
      const n = Number(draft);
      if (draft.trim() !== "" && Number.isFinite(n) && n !== value) onSave(Math.min(max, Math.max(min, n)));
      else setDraft(String(value));
    }} />;
}

/** Plays a sound - one at a time on the page. */
export function PlayButton({ src, label }: { src: string; label: string }) {
  const [playing, setPlaying] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  useEffect(() => {
    const other = (ev: Event) => {
      if ((ev as CustomEvent<string>).detail !== src) audio.current?.pause();
    };
    window.addEventListener("mv:play", other);
    return () => {
      window.removeEventListener("mv:play", other);
      audio.current?.pause();
      audio.current = null;
      setPlaying(false);
    };
  }, [src]);
  const toggle = () => {
    if (playing) {
      audio.current?.pause();
      return;
    }
    window.dispatchEvent(new CustomEvent("mv:play", { detail: src }));
    const a = audio.current ?? new Audio(src);
    audio.current = a;
    a.onplay = () => setPlaying(true);
    a.onpause = () => setPlaying(false);
    a.onended = () => setPlaying(false);
    a.currentTime = 0;
    a.play().catch(() => setPlaying(false));
  };
  return (
    <button type="button" className={`icon-btn small mv-play${playing ? " on" : ""}`} onClick={toggle}
      aria-label={`${playing ? "Stop" : "Play"} ${label}`} title={`${playing ? "Stop" : "Play"} ${label}`}>
      {playing ? <Square size={12} /> : <Play size={13} />}</button>
  );
}
