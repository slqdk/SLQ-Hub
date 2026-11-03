// Movies (0.72.0): short films made shot by shot - the shapes /hub/api/movies returns, and small helpers.
// 0.73.0: the sound (voices, the lines spoken, effects, the theme) and the cut.

export type ResultState = "none" | "fresh" | "stale";

export interface MovieStyle {
  model: string; lora: string; strength: number; words: string; format: string; variants: number;
  video_model: string; quality: string; max_seconds: number; language: string; rules: string;
  narrator_voice: string;
}
export interface Variant { file: string; stamp: string; seed?: number; made: number; method?: string; notes?: string[] }
export interface CastMember { id: string; name: string; look: string; lora: string; strength: number; sheet: string;
  sheet_state: ResultState; variants: Variant[];
  voice: string; voice_clip?: string; voice_clip_seconds?: number; expressiveness: number }
export interface MovieSet { id: string; name: string; look: string; picture: string; picture_state: ResultState;
  variants: Variant[] }
export interface Clip { file: string; seed: number; seconds: number; width: number; height: number; fps: number;
  model: string; kept: boolean; made: number }
export interface LineAudio { file: string; seconds: number; voice: string; made: number }
export interface Line {
  id: string; who: string; name: string; text: string;
  at: number | null;        // where the owner put it in its shot; null: right after the line before
  start: number;            // where it starts in its shot
  seconds: number;          // its take's length, or how long it would take to say
  over: number;             // seconds past the shot's end
  audio: LineAudio | null; state: ResultState;
  error?: string;           // why its last take failed
}
export interface Shot {
  id: string; seconds: number; set: string; characters: string[]; method: string; picture: string; motion: string;
  sound: string; lines: Line[]; variants: Variant[]; keyframe: string; keyframe_state: ResultState;
  clip: Clip | null; clip_state: ResultState; takes: Clip[];
  method_used: string; method_wanted: string; missing: string[];
  keyframe_error?: string; clip_error?: string;     // why the pictures or the clip failed (until they are made)
  sfx: { file: string; seconds: number; made: number } | null;
  sfx_state: ResultState | "unwanted";                // unwanted: no sound written
  sfx_error?: string;
  seconds_used: number;                               // its clip's length, else its own
}
export type TextStyle = "bubbles" | "captions" | "none";
export interface CutSettings { music: boolean; music_volume: number; effects_volume: number; speech_volume: number;
  text: TextStyle; end_card: boolean }
export interface Episode {
  id: string; title: string; premise: string; approved: boolean; notes: string[]; shots: Shot[];
  preview: { file: string; seconds: number; made: number } | null;
  final: { file: string; seconds: number; made: number; width: number; height: number } | null;
  cut: CutSettings;
  progress: { shots: number; seconds: number; approved: boolean; keyframes: number; clips: number; kept: number;
    preview: ResultState; lines: number; spoken: number; effects: number; effects_made: number; final: ResultState;
    final_why: string; cut_missing: string[]; total: number };
}
export interface ThemeVersion { file: string; seconds: number; seed: number; made: number; bpm?: number; key?: string }
export interface Theme { prompt: string; seconds: number; file: string; state: ResultState; variants: ThemeVersion[] }
export interface AudioInfo {
  language: string;         // the speech models' code for the lines' language; "" when they do not know it
  speech: string; voices: { id: string; description: string }[]; note: string; expressive: boolean;
  sound: string; music: string;
}
export interface MovieJob { what?: string; name?: string; shot?: string; number?: number; line?: number; done?: number;
  total?: number; value?: number; max?: number; chars?: number; model?: string; seconds?: number; queued: string[] }
export interface Series {
  id: string; name: string; style: MovieStyle; cast: CastMember[]; sets: MovieSet[]; episodes: Episode[];
  theme: Theme; audio: AudioInfo;
  jobs: Record<string, MovieJob>; errors: Record<string, string>; new_episode?: string;
}
export interface SeriesItem { id: string; name: string; episodes: number; cast: number; cover: string; updated: number;
  working: boolean }
export interface MovieOptions {
  models: { id: string; name: string; family: string; combine: boolean }[];
  default_model: { id: string; name: string; family: string };
  loras: { id: string; name: string; base: string; base_label: string; trigger: string; strength: number;
    slider: boolean; category: string }[];
  video_models: { id: string; name: string }[];
  default_video: string;
  writer: string;
  formats: string[];
  languages: string[];
  defaults: MovieStyle;
}

export const movieFile = (sid: string, path: string) =>
  `/hub/api/movies/${sid}/file?path=${encodeURIComponent(path)}`;

/** A shot's lines as the text field shows them: "Name: what is said", one a line. */
export function linesToText(lines: Line[]): string {
  return lines.map((l) => `${l.name}: ${l.text}`).join("\n");
}

/** And back: a line without "Name:" is the narrator's. */
export function textToLines(text: string): { who: string; text: string }[] {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const m = l.match(/^([^:]{1,60}):\s*(.+)$/);
    return m ? { who: m[1].trim(), text: m[2].trim() } : { who: "Narrator", text: l };
  });
}

/** Whether a shot's queued work includes it: a shot's id, or one of its lines ("<shot>:<line>"). */
export function queuedFor(job: MovieJob | undefined, shot: string): boolean {
  return !!job?.queued.some((q) => q === shot || q.startsWith(`${shot}:`));
}

/** What a job does now, for the line under what it works on. */
export function jobText(job: MovieJob): string {
  const steps = job.max ? ` - step ${job.value ?? 0} of ${job.max}` : "";
  const of = job.total && job.total > 1 ? ` (${(job.done ?? 0) + 1} of ${job.total})` : "";
  switch (job.what) {
    case "sheet": return `Making ${job.name}'s sheet${steps}`;
    case "set": return `Making the picture of ${job.name}${steps}`;
    case "storyboard": return `${job.model ?? "The chat model"} is writing the storyboard${job.chars ? ` (${job.chars.toLocaleString()} characters)` : ""}`;
    case "keyframes": return `Keyframes for shot ${job.number}${of}${steps}`;
    case "clips": return `The clip for shot ${job.number}${of}${steps}`;
    case "preview": return `Joining ${job.total} clip${job.total === 1 ? "" : "s"}`;
    case "voices": return `Speaking line ${job.line} of shot ${job.number}${of}`;
    case "sounds": return `The effect for shot ${job.number}${of}${steps}`;
    case "theme": return `Making a version of the theme${steps}`;
    case "cut": return `Cutting the episode - ${job.seconds} s of film`;
    default: return "Waiting for the graphics card";
  }
}
