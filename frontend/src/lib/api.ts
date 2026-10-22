// The Hub's UI API: one fetch wrapper and the shapes it returns.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Opts = { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal; query?: Record<string, unknown> };

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

export const mediaUrl = (sha: string) => `/hub/media/blob/${sha}`;

// ------------------------------------------------------------ shapes

export interface User { id: number; username: string }
export interface AuthStatus { signedIn: boolean; user: User | null; version: string; setupNeeded: boolean }

export type Block =
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "image"; sha: string; mime?: string; name?: string; path?: string; size?: number; ref?: boolean }
  | { type: "file"; sha: string; mime?: string; name?: string; path?: string; size?: number }
  /** A file of the chat's working folder put in the message (its text then, when "inline"). */
  | { type: "file_ref"; path: string; name?: string; size?: number; mime?: string; sha?: string; lines?: number;
      inline?: boolean }
  | { type: "tool_call"; id: string; name: string; arguments: string }
  | { type: "tool_result"; tool_call_id: string; name: string; text: string; images?: ImageRef[]; is_error?: boolean;
      display?: Display | null };

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
  [k: string]: unknown;
}
export interface Todo { content: string; status: "pending" | "in_progress" | "completed" }

export interface Message { id: number; chat_id: number; role: "user" | "assistant" | "tool"; content: Block[];
  run_id: string | null; created_at: string }

export interface ChatSettings { model: string | null; thinking: boolean | null; effort: string | null; skills: string[];
  libraries: number[]; image_model: string | null }
export interface Chat { id: number; project_id: number | null; title: string; settings: ChatSettings; summary?: string;
  created_at: string; updated_at: string }
export interface ChatListItem { id: number; project_id: number | null; title: string; updated_at: string;
  created_at: string; project_name: string | null; running: boolean }

export interface Step { type: string; step?: number; depth?: number; model?: string; name?: string; ok?: boolean;
  seconds?: number; prompt_tokens?: number | null; completion_tokens?: number | null; cached_tokens?: number | null;
  tps?: number | null; prompt_tps?: number | null; reason?: string }
export interface RunRecord { id: string; chat_id: number; status: string; model: string; started_at: string;
  ended_at: string | null; steps: Step[]; usage: { prompt_tokens?: number; completion_tokens?: number; cached_tokens?: number };
  error: string | null }
export interface RunInfo { id: string; chat_id: number; status: string; started: number; events: number }
export interface Artifact { id: string; chat_id: number; version: number; title: string; type: string; content: string;
  created_at: string; latest: number }
export interface ArtifactListItem { id: string; title: string; type: string; version: number; updated: string }

export interface ChatData { chat: Chat; messages: Message[]; artifacts: ArtifactListItem[]; runs: RunRecord[];
  run: RunInfo | null; project: Project | null }

export interface ProjectSettings { model: string | null; cloud_escalation: boolean; skills: string[]; libraries: number[];
  thinking: boolean | null; image_model: string | null }
export interface Project { id: number; name: string; instructions: string; settings: ProjectSettings; created_at: string;
  updated_at: string; archived?: number; chats?: number; head?: number }

export interface RunEvent { seq: number; type: string; t: number; data: Record<string, any> }

export interface Upload { sha: string; name: string; mime: string; size: number; width: number | null;
  height: number | null; url: string }

export interface QueueJob { id: number; owner: string; kind: string; need: string; priority: string; label: string;
  lease: boolean; state: string; position: number | null; waited_s: number; running_s: number | null;
  switching_to: string | null; meta: { chat_id?: number; model?: string } }
export interface QueueSnapshot { current: string | null; switching: string | null; last_error: string | null;
  running: QueueJob[]; pending: QueueJob[] }

export interface DownloadState { model: string; dest: string; status: string; done: number; total: number; rate: number;
  error: string | null }
/** An SDXL picture model's own settings (added from a link). */
export interface SdxlSettings { preset: string; steps: number; cfg: number; sampler: string; scheduler: string;
  clip_skip: number; prefix: string; negative: string }
export interface SdxlChoices { presets: Record<string, SdxlSettings & { label: string }>; samplers: string[];
  schedulers: string[] }

export interface ModelEntry { id: string; name: string; description?: string; default?: boolean; installed: boolean;
  loaded?: boolean; downloads: DownloadState[]; capabilities?: string[]; kind?: string;
  files: { repo?: string; path?: string; url?: string; dest: string; size?: number; role?: string }[];
  server?: Record<string, unknown>; thinking?: { efforts?: string[]; default_effort?: string; default?: boolean };
  context_per_request?: number; parameter_size?: string; quantization?: string; aliases?: string[]; requires?: string[];
  custom?: boolean; family?: string; abilities?: string[]; sdxl?: SdxlSettings;
  source?: { repo?: string; url: string; file?: string; added?: string; site?: "huggingface" | "civitai"; nsfw?: boolean;
    base?: string; version_id?: number } }

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
export interface ModelsData { llm: ModelEntry[]; aux: ModelEntry[]; comfy: ModelEntry[]; current: string | null;
  defaults: { default: string; fallback_alias: string; preload: boolean }; sdxl?: SdxlChoices }

export interface ApiKey { id: number; name: string; prefix: string; priority: string; allowed_ips: string;
  created_at: string; last_used: string | null; enabled: number }

export interface Skill { name: string; title: string; description: string; files: string[]; enabled: boolean }
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
