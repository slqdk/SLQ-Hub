// The Hub's UI API: one fetch wrapper and the shapes it returns.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Opts = { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal; query?: Record<string, unknown> };

// The Hub's version as this page first heard it (every answer to the UI carries X-Hub-Version). An answer with
// another one means the Hub was updated after the page loaded: the page runs the old code until it is reloaded -
// on a phone a tab can stay open for days.
let loadedVersion: string | null = null;
let newerVersion: string | null = null;

function noteVersion(v: string | null): void {
  if (!v) return;
  if (loadedVersion === null) loadedVersion = v;
  else if (v !== loadedVersion && v !== newerVersion) {
    newerVersion = v;
    window.dispatchEvent(new CustomEvent("hub:updated", { detail: v }));
  }
}

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  let url = path;
  if (opts.query) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.query)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    const s = q.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  const init: RequestInit = { method: opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET"),
    credentials: "same-origin", signal: opts.signal, headers: {} };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "The Hub does not answer. Check the connection.");
  }
  noteVersion(res.headers.get("x-hub-version"));
  if (res.status === 401 && !path.startsWith("/hub/api/auth/")) {
    window.dispatchEvent(new CustomEvent("hub:signed-out"));
  }
  const type = res.headers.get("content-type") ?? "";
  const data = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, errorText(data, res.status));
  return data as T;
}

function errorText(data: unknown, status: number): string {
  if (data && typeof data === "object" && "detail" in data) {
    const d = (data as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => (x as { msg?: string }).msg ?? String(x)).join("; ");
  }
  if (typeof data === "string" && data.trim()) return data.slice(0, 300);
  return `The Hub answered ${status}.`;
}

export async function upload(file: File | Blob, name?: string): Promise<Upload> {
  const fd = new FormData();
  fd.append("file", file, name ?? (file as File).name ?? "file");
  return api<Upload>("/hub/api/uploads", { form: fd });
}

// A person's chats keep their pictures on the device: a resolver gives those a local URL.
let localMedia: ((sha: string) => string | undefined) | null = null;
export function setLocalMedia(fn: ((sha: string) => string | undefined) | null): void {
  localMedia = fn;
}
export const mediaUrl = (sha: string) => localMedia?.(sha) ?? `/hub/media/blob/${sha}`;

// ------------------------------------------------------------ shapes

/** role "member": a person the owner added (Settings, People); their chats live on their device. */
export interface User { id: number; username: string; role?: "admin" | "member"; display_name?: string; away?: boolean;
  may_run_code?: boolean }
export interface Person { id: number; username: string; display_name: string; away: boolean; may_run_code: boolean;
  created_at: string; last_seen: string | null }
export interface AuthStatus { signedIn: boolean; user: User | null; version: string; setupNeeded: boolean;
  internetAsks?: boolean }                     // Settings -> Web: the internet asks first

export type Block =
  | { type: "text"; text: string; while_working?: boolean }   // while_working: sent while a turn worked
  | { type: "thinking"; text: string }
  | { type: "notice"; text: string }          // a note from the Hub in an answer (shown, never sent to the model)
  | { type: "image"; sha: string; mime?: string; name?: string; path?: string; size?: number; ref?: boolean }
  | { type: "file"; sha: string; mime?: string; name?: string; path?: string; size?: number }
  /** A file of the chat's working folder put in the message (its text then, when "inline"). */
  | { type: "file_ref"; path: string; name?: string; size?: number; mime?: string; sha?: string; lines?: number;
      inline?: boolean }
  | { type: "tool_call"; id: string; name: string; arguments: string }
  | { type: "tool_result"; tool_call_id: string; name: string; text: string; images?: ImageRef[]; is_error?: boolean;
      display?: Display | null }
  /** Continue under an unfinished turn (0.22.0): the model is told why it stopped. */
  | { type: "continue"; reason: "limit" | "stopped"; steps?: number }
  /** The first message of a chat that continues another (0.27.0, Continue in a new chat). */
  | { type: "handover"; chat_id: number; title: string }
  /** The independent review of a turn's work (0.30.0): stored as the Hub's message in the turn, shown in it.
   * wrapped/limit (0.32.0): it was told to finish near its limit of steps or time ("24 steps", "15 minutes"). */
  | { type: "review"; id: string; model: string; verdict: "ok" | "problems" | "failed"; report: string; files: string[];
      artifacts?: string[]; seconds?: number; changed?: string[]; error?: string; wrapped?: "steps" | "time";
      limit?: string };

export interface ImageRef { sha: string; mime?: string; caption?: string }
export interface ArtifactRef { id: string; version: number; title: string; type: string; language?: string | null }
export interface Display {
  artifact?: ArtifactRef;
  todos?: Todo[];
  images?: ImageRef[];
  subagent?: { description: string; tool_calls: number };
  library_hits?: { doc_id: number; filename: string; page: number; score: number; image: string | null; via: string[]; text: string }[];
  sources?: { title: string; url: string }[];
  mcp?: { server: string; tool: string };
  audio?: { path: string; seconds: number; model: string }[];
  video?: { path: string; seconds: number; model: string }[];
  skill?: SkillCardData;
  [k: string]: unknown;
}
export interface Todo { content: string; status: "pending" | "in_progress" | "completed" }

export interface Message { id: number; chat_id: number; role: "user" | "assistant" | "tool"; content: Block[];
  run_id: string | null; created_at: string }

export interface ChatSettings { model: string | null; thinking: boolean | null; effort: string | null; skills: string[];
  libraries: number[]; image_model: string | null }
export interface Chat { id: number; project_id: number | null; title: string; settings: ChatSettings; summary?: string;
  summary_upto?: number;
  created_at: string; updated_at: string; test_run?: string | null }
export interface ChatListItem { id: number; project_id: number | null; title: string; updated_at: string;
  created_at: string; project_name: string | null; running: boolean;
  energy_wh?: number | null; cost?: number | null;        // a project's chats: what each used of the card (0.24.0)
  // and for the project's overview (0.34.0): answers, tokens, the time its turns took, the models that worked (ids;
  // a cloud model by its name) and how the last turn ended
  turns?: number | null; tokens_in?: number | null; tokens_out?: number | null; seconds?: number | null;
  models?: string[];
  last?: { status?: string; unfinished?: "limit" | "stopped"; review?: "ok" | "problems" | "failed" } }

/** A file an answer made or changed in the working folder (a person's: on the device). */
export interface ProducedFile { path: string; size: number; mime: string; new?: boolean; skipped?: string }
export interface Step { type: string; step?: number; steps?: number; depth?: number; model?: string; name?: string; ok?: boolean;
  seconds?: number; prompt_tokens?: number | null; completion_tokens?: number | null; cached_tokens?: number | null;
  tps?: number | null; prompt_tps?: number | null; reason?: string; files?: ProducedFile[] }
export interface RunRecord { id: string; chat_id: number; status: string; model: string; started_at: string;
  ended_at: string | null; steps: Step[]; usage: RunUsage; error: string | null }
/** A turn's tokens and, since 0.24.0, the card's energy for it and what that cost (kr, at the price of the day). */
export interface RunUsage { prompt_tokens?: number; completion_tokens?: number; cached_tokens?: number; energy_wh?: number;
  cost?: number; gpu_seconds?: number }
export interface RunInfo { id: string; chat_id: number; status: string; started: number; events: number }
export interface Artifact { id: string; chat_id: number; version: number; title: string; type: string; content: string;
  created_at: string; latest: number }
export interface ArtifactListItem { id: string; title: string; type: string; version: number; updated: string }

export interface ChatData { chat: Chat; messages: Message[]; artifacts: ArtifactListItem[]; runs: RunRecord[];
  run: RunInfo | null; project: Project | null;
  /** How full the last request was, and whether the chat was condensed (0.27.0: the long-chat hint). */
  context?: { used: number; limit: number; condensed: boolean };
  allowed?: { tool: string; created_at: string }[] }      // "Allow in this chat" answers

export interface ProjectSettings { model: string | null; cloud_escalation: boolean; skills: string[]; libraries: number[];
  thinking: boolean | null; image_model: string | null }
export interface Project { id: number; name: string; instructions: string; settings: ProjectSettings; created_at: string;
  updated_at: string; archived?: number; chats?: number; head?: number; usage?: UsageTotals }
/** What was used of the card (0.24.0): answers, other jobs, tokens read / written / from the cache, energy (Wh), cost
 *  (kr), seconds on the card; since: the first day counted. */
export interface UsageTotals { turns: number; jobs: number; tokens_in: number; tokens_out: number; tokens_cached: number;
  energy_wh: number; cost: number; seconds: number; since: string | null }
export interface UsageRow extends UsageTotals { who: string; label: string;
  kind: "owner" | "person" | "app" | "tests" | "library" | "system" | "idle" | "other" }
export interface UsageData { period: "today" | "month" | "all"; since: string | null; price_per_kwh: number;
  other_watts: number; power: { now_w: number; cap_w: number | null; source: string | null } | null; rows: UsageRow[] }

export interface RunEvent { seq: number; type: string; t: number; data: Record<string, any> }

export interface Upload { sha: string; name: string; mime: string; size: number; width: number | null;
  height: number | null; url: string }

export interface QueueJob { id: number; owner: string; kind: string; need: string; priority: string; label: string;
  lease: boolean; state: string; position: number | null; waited_s: number; running_s: number | null;
  switching_to: string | null; meta: { chat_id?: number; model?: string; user_id?: number } }
export interface QueueSnapshot { current: string | null; switching: string | null; last_error: string | null;
  running: QueueJob[]; pending: QueueJob[];
  received?: number }          // when the page got it (Date.now()): the times in it count on from there

export interface DownloadState { model: string; dest: string; status: string; done: number; total: number; rate: number;
  error: string | null }
/** An SDXL picture model's own settings (added from a link). */
export interface SdxlSettings { preset: string; steps: number; cfg: number; sampler: string; scheduler: string;
  clip_skip: number; prefix: string; negative: string }
export interface SdxlChoices { presets: Record<string, SdxlSettings & { label: string }>; samplers: string[];
  schedulers: string[] }

/** name: what the Hub shows (the owner may rename chat and picture models); name_card: the model's own name, when
 * a bundled model was renamed. */
export interface ModelEntry { id: string; name: string; name_card?: string; description?: string; default?: boolean; installed: boolean;
  loaded?: boolean; downloads: DownloadState[]; capabilities?: string[]; kind?: string;
  files: { repo?: string; path?: string; url?: string; dest: string; size?: number; role?: string }[];
  server?: Record<string, unknown>; thinking?: { efforts?: string[]; default_effort?: string; default?: boolean };
  context_per_request?: number;
  /** Language models: what one chat may use now (Settings or the card), and what the model was trained for. */
  context_per_chat?: number; context_native?: number | null; parameter_size?: string;
  /** A downloaded model from the Hub's own list: the bytes Delete files gives back (files no other model uses). */
  freeable?: number; quantization?: string; aliases?: string[]; requires?: string[];
  custom?: boolean; family?: string; abilities?: string[]; sdxl?: SdxlSettings; architecture?: string;
  /** Sound models: where they run (the card through ComfyUI, or the CPU speech service) and what they speak. */
  engine?: "comfy" | "speech"; languages?: string[];
  /** Sampling per mode (instruct = without thinking): the card's, the family's, or set in Settings (sampling_own). */
  sampling?: Partial<Record<"instruct" | "thinking", Sampling>>; sampling_card?: Partial<Record<"instruct" | "thinking", Sampling>>;
  sampling_own?: boolean; thinking_mode?: "on" | "off";
  source?: { repo?: string; url: string; file?: string; added?: string; site?: "huggingface" | "civitai"; nsfw?: boolean;
    base?: string; version_id?: number } }

export interface Sampling { temperature?: number; top_p?: number; top_k?: number; min_p?: number; presence_penalty?: number;
  repeat_penalty?: number }

/** GET /hub/api/models/hf/search: GGUF repositories matching some words. */
export interface HFSearchHit { repo: string; downloads: number; likes: number; updated: string | null; gated: boolean }

/** GET /hub/api/models/images/lookup: what an SDXL link points at. */
export interface ImageOption { key: string; label: string; size: number; supported: boolean | null; reason: string;
  base: string; preset: string; vpred?: boolean; file?: { name: string; url: string; sha256: string } }
export interface ImageLookup { site: "hf" | "civitai"; url: string; title: string; nsfw: boolean | null;
  creator: string | null; options: ImageOption[]; pick: string | null;
  suggested: { id: string; name: string; preset: string }; freeDisk: number; tokens: { hf: boolean; civitai: boolean } }

/** GET /hub/api/models/hf: a Hugging Face repository's GGUF files, grouped. */
export interface HFChoice { key: string; label: string; split: boolean; size: number; files: { path: string; size: number }[] }
export interface HFLookup { repo: string; url: string; choices: HFChoice[];
  mmproj: { path: string; size: number; label: string }[];
  meta: { architecture?: string | null; context_length?: number | null; parameters?: number | null;
    tools?: boolean | null; thinking?: boolean | null };
  suggested: { id: string; name: string }; freeDisk: number; vram: number }
export interface ModelsData { llm: ModelEntry[]; aux: ModelEntry[]; comfy: ModelEntry[]; audio?: ModelEntry[];
  video?: ModelEntry[]; current: string | null;
  defaults: { default: string; fallback_alias: string; preload: boolean }; sdxl?: SdxlChoices }

export interface ApiKey { id: number; name: string; prefix: string; priority: string; allowed_ips: string;
  away: number; created_at: string; last_used: string | null; enabled: number }

/** A skill (0.23.0): yours, one the family shared, or the Hub's own; named by its ref ("<name>", "@<id>/<name>",
 *  "hub/<name>"). */
export interface Skill { ref: string; name: string; title: string; description: string; files: string[]; size: number;
  kind: "owner" | "person" | "hub"; owner_id: number | null; mine: boolean; shared: boolean; builtin: boolean;
  updated: string; by: string }
/** What skill_propose shows in the chat: a skill the assistant made, to add with Add skill. */
export interface SkillCardData { draft: string; name: string; title: string; description: string; files: string[];
  size: number; replaces: boolean; ref: string }
export interface SkillDraft extends SkillCardData { text: string; added: number | null; expires: number }
export interface MemoryFile { path: string; title: string; size: number }
export interface McpStatus { name: string; transport: string; enabled: boolean; trusted: boolean; status: string;
  error: string; server: { name?: string; version?: string };
  tools: { name: string; agent_name: string; description: string }[] }
export interface McpServerConfig { name: string; transport: "http" | "stdio"; url?: string; headers?: Record<string, string>;
  command?: string; env?: Record<string, string>; enabled: boolean; trusted?: boolean; timeout_s?: number }

export interface Library { id: number; name: string; description: string; docs: number; ready: number; busy: number;
  errors: number; bytes: number; pages: number; created_at: string }
export interface LibraryDoc { id: number; library_id: number; filename: string; sha: string; mime: string; size: number;
  pages: number; chunks: number; status: string; progress: number; error: string | null; created_at: string;
  indexed_at: string | null }
export interface LibraryHit { doc_id: number; filename: string; page: number; text: string; score: number;
  image: string | null; via: string[] }

export interface TreeEntry { path: string; dir: boolean; size: number; modified: number }
export interface TreeData { root: string; projectId: number | null; entries: TreeEntry[] }
export interface FileData { path: string; size: number; mime: string; text: string | null; binary: boolean }
export interface Revision { rev: number; author: string; origin: string; message: string; createdAt: string;
  fileCount: number; files: { path: string; deleted: boolean }[] }

export type Settings = Record<string, Record<string, any>>;
