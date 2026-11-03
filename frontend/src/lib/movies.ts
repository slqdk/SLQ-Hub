// Movies (0.72.0): short films made shot by shot - the shapes /hub/api/movies returns, and small helpers.

export type ResultState = "none" | "fresh" | "stale";

export interface MovieStyle {
  model: string; lora: string; strength: number; words: string; format: string; variants: number;
  video_model: string; quality: string; max_seconds: number; language: string; rules: string;
}
export interface Variant { file: string; stamp: string; seed?: number; made: number; method?: string; notes?: string[] }
export interface CastMember { id: string; name: string; look: string; lora: string; strength: number; sheet: string;
  sheet_state: ResultState; variants: Variant[] }
export interface MovieSet { id: string; name: string; look: string; picture: string; picture_state: ResultState;
  variants: Variant[] }
export interface Clip { file: string; seed: number; seconds: number; width: number; height: number; fps: number;
  model: string; kept: boolean; made: number }
export interface Line { who: string; name: string; text: string }
export interface Shot {
  id: string; seconds: number; set: string; characters: string[]; method: string; picture: string; motion: string;
  sound: string; lines: Line[]; variants: Variant[]; keyframe: string; keyframe_state: ResultState;
  clip: Clip | null; clip_state: ResultState; takes: Clip[];
  method_used: string; method_wanted: string; missing: string[];
  keyframe_error?: string; clip_error?: string;     // why the pictures or the clip failed (until they are made)
}
export interface Episode {
  id: string; title: string; premise: string; approved: boolean; notes: string[]; shots: Shot[];
  preview: { file: string; seconds: number; made: number } | null;
  progress: { shots: number; seconds: number; approved: boolean; keyframes: number; clips: number; kept: number;
    preview: ResultState };
}
export interface MovieJob { what?: string; name?: string; shot?: string; number?: number; done?: number; total?: number;
  value?: number; max?: number; chars?: number; model?: string; queued: string[] }
export interface Series {
  id: string; name: string; style: MovieStyle; cast: CastMember[]; sets: MovieSet[]; episodes: Episode[];
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
    default: return "Waiting for the graphics card";
  }
}
