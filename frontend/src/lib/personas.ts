// Personas (0.74.0): someone to chat with - who they are, how they talk, their picture, voice and memory.
import { useEffect, useState } from "react";
import { api } from "./api";

export interface PersonaItem { id: string; name: string; role: string; picture: string; updated: number }
/** What a chat with a persona shows (ChatData.persona). */
export interface PersonaBrief { id: string; name: string; role: string; picture: string; greeting: string;
  read_aloud: boolean; speech: boolean }
export interface PersonaVariant { file: string; made: number; own?: boolean }
export interface PersonaAudio { language: string; speech: string; voices: { id: string; description: string }[];
  note: string; expressive: boolean }
export interface PersonaChat { id: number; title: string; updated_at: string; created_at: string; archived: number }
/** 0.75.0: a task - at its days and time the persona makes a post for the owner's review. */
export type TaskMedia = "picture" | "video" | "either" | "none";
export interface PersonaTask {
  id: string; title: string; instruction: string; days: number[]; time: string; media: TaskMedia; web: boolean;
  enabled: boolean; chat_id: number | null; next_run: number | null; last_run: number | null; last_error: string;
  last_post: string; running: boolean;
}
export interface PostMedia { file: string; kind: "picture" | "video" }
/** 0.76.0: how its trip to Instagram went. */
export interface PostInstagram { state: "posting" | "posted" | "failed"; id?: string; permalink?: string; at?: number;
  error?: string }
export interface PersonaPost {
  id: string; task: string; title: string; text: string; media: PostMedia[]; status: "draft" | "approved";
  made: number; chat: number | null; instagram?: PostInstagram;
  /** instagram: the account the post can go to now ("" when none). */
  persona: { id: string; name: string; picture: string; instagram: string };
}
/** 0.76.0: the persona's Instagram account (never its token). */
export interface PersonaInstagram { username: string; account_type: string; expires: number | null;
  connected: number | null; problem: string; login: "unified" | "instagram"; others: number }
/** The owner's Meta app that posts the personas' posts (never its secret). 0.76.1: with a configuration ID it
 *  signs in with Unified login (apps made since October 2026), without one with Instagram login. */
export interface InstagramApp { app_id: string; secret_set: boolean; config_id: string; login: "unified" | "instagram";
  redirect_uri: string; public_url: string; ready: boolean }
export interface Persona {
  id: string; name: string; role: string; personality: string; speech: string; examples: string; greeting: string;
  language: string; model: string; level: "quick" | "think"; look: string; lora: string; strength: number;
  picture: string; variants: PersonaVariant[]; voice: string; voice_clip: string; voice_clip_seconds: number;
  expressiveness: number; web: boolean; pictures: boolean; check_pictures: boolean; memory: boolean;
  read_aloud: boolean;
  created: number; updated: number; tasks: PersonaTask[]; instagram: PersonaInstagram | null;
  notes: string; notes_max: number; chats: PersonaChat[]; audio: PersonaAudio;
  job: { what: string; value?: number; max?: number } | null; error: string;
}
export interface PersonaLora { id: string; name: string; base: string; base_label: string; trigger: string;
  category: string; strength: number }
export interface PersonaOptions { models: { id: string; name: string }[]; default_model: string; languages: string[];
  loras: PersonaLora[]; pictures: boolean; speech: boolean }

export const personaFile = (pid: string, path: string) =>
  `/hub/api/personas/${pid}/file?path=${encodeURIComponent(path)}`;

export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_NAMES = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"];

/** "Mondays at 19:00", "Every day at 07:30", "Mon, Wed, Fri at 18:00". */
export function scheduleText(t: { days: number[]; time: string }): string {
  const d = [...t.days].sort();
  const when = d.length === 7 ? "Every day" : d.length === 5 && d.every((x, i) => x === i) ? "Weekdays"
    : d.length === 2 && d[0] === 5 && d[1] === 6 ? "Weekends" : d.length === 1 ? DAY_NAMES[d[0]]
    : d.map((x) => DAYS[x]).join(", ");
  return `${when} at ${t.time}`;
}

/** "Mon 9 Nov, 19:00" (local time). */
export function whenText(epoch: number): string {
  const t = new Date(epoch * 1000);
  return `${t.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}, `
    + t.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

// ---------------------------------------------------------------- one voice at a time

/** What plays now: an answer ("<chat>:<message>") or a persona's sample ("sample:<id>"), being made or playing. */
export type Playing = { key: string; state: "making" | "playing" } | null;
let playing: Playing = null;
let seq = 0;                                      // each play and each stop: the newest one wins
let el: HTMLAudioElement | null = null;           // the page's one element (see unlockAudio)
let quiet = "";
const OWN = "persona-voice";                      // the "mv:play" event of Movies' play buttons, from here
const listeners = new Set<() => void>();

function set(p: Playing): void {
  playing = p;
  listeners.forEach((f) => f());
}

export function usePlaying(): Playing {
  const [, tick] = useState(0);
  useEffect(() => {
    const f = () => tick((x) => x + 1);
    listeners.add(f);
    return () => { listeners.delete(f); };
  }, []);
  return playing;
}

function element(): HTMLAudioElement {
  if (!el) el = new Audio();
  return el;
}

/** A tenth of a second of silence, as a WAV. */
function silence(): string {
  const n = 800;
  const buf = new Uint8Array(44 + n);
  const v = new DataView(buf.buffer);
  const w = (at: number, text: string) => { for (let i = 0; i < text.length; i++) buf[at + i] = text.charCodeAt(i); };
  w(0, "RIFF"); v.setUint32(4, 36 + n, true); w(8, "WAVEfmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, 1, true); v.setUint32(24, 8000, true); v.setUint32(28, 8000, true); v.setUint16(32, 1, true);
  v.setUint16(34, 8, true); w(36, "data"); v.setUint32(40, n, true);
  buf.fill(128, 44);
  return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
}

/** From a tap or a key press: the page's audio element may then play later without one. Safari on a phone lets sound
 *  start only from the user's own action - and an answer is read aloud only once the Hub has made it. */
export function unlockAudio(): void {
  if (playing) return;
  try {
    const a = element();
    quiet ||= silence();
    a.src = quiet;
    a.play().catch(() => undefined);
  } catch {
    /* nothing to unlock */
  }
}

export function stopPlaying(): void {
  seq++;
  el?.pause();
  if (playing) set(null);
}

if (typeof window !== "undefined") {             // a Movies play button started: this one stops
  window.addEventListener("mv:play", (e) => { if ((e as CustomEvent<string>).detail !== OWN) stopPlaying(); });
}

type Made = { path?: string; making?: boolean };

/** Ask the Hub for the sound until it is made (a long answer takes minutes on the CPU), then play it - anything
 *  playing stops; a stop or a newer play while it is made wins. */
async function play(key: string, pid: string, make: () => Promise<Made>): Promise<void> {
  stopPlaying();
  const mine = ++seq;
  window.dispatchEvent(new CustomEvent("mv:play", { detail: OWN }));
  set({ key, state: "making" });
  const over = () => {
    if (mine === seq) {
      seq++;
      set(null);
    }
  };
  try {
    let r = await make();
    while (!r.path) {
      await new Promise((ok) => window.setTimeout(ok, 1500));
      if (mine !== seq) return;
      r = await make();
    }
    if (mine !== seq) return;
    const a = element();
    a.onended = over;
    a.onerror = over;
    a.src = personaFile(pid, r.path);
    set({ key, state: "playing" });
    await a.play();
  } catch (e) {
    over();
    throw e;
  }
}

export const answerKey = (chatId: number, messageId: number) => `${chatId}:${messageId}`;

export function playAnswer(pid: string, chatId: number, messageId: number): Promise<void> {
  return play(answerKey(chatId, messageId), pid, () => api<Made>(`/hub/api/personas/${pid}/say`,
    { body: { chat_id: chatId, message_id: messageId } }));
}

export function playSample(pid: string): Promise<void> {
  return play(`sample:${pid}`, pid, () => api<Made>(`/hub/api/personas/${pid}/sample`, { method: "POST" }));
}
