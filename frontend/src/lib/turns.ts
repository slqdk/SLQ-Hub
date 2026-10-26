// One view model for an assistant turn, built either from stored messages
// (history) or from a run's live events - so both render the same way.
import type { ArtifactRef, Block, Display, ImageRef, Message, RunEvent, RunRecord, SkillCardData, Todo } from "./api";

export type ToolState = "streaming" | "running" | "approval" | "done" | "error";

export interface ToolView {
  id: string;
  name: string;
  argsText: string;
  args?: Record<string, unknown>;
  state: ToolState;
  result?: string;
  images?: ImageRef[];
  display?: Display | null;
  seconds?: number;
  progress?: { value: number; max: number };   // a long GPU or speech job, while it runs
  children?: Item[];
  subagent?: { description: string; finished: boolean };
  ask?: AskInfo;                               // what the turn asks before this call (internet set to ask first)
  canChat?: boolean;                           // "Allow in this chat" is offered (the owner's chats)
}

/** Internet for the assistant, asking first: what a call wants and what "always" would allow. */
export interface AskInfo { kind: "internet"; what: "search" | "site" | "code"; key: string; detail?: string; host?: string;
  always: string; can_always: boolean; can_chat?: boolean; unblockable?: boolean }

/** An answer to a call that waits for the user: once, the rest of this chat, always, or no. */
export type Decision = "allow" | "chat" | "always" | "deny";

export type Item =
  | { kind: "thinking"; text: string }
  | { kind: "text"; text: string }
  | { kind: "tools"; tools: ToolView[] }
  | { kind: "artifact"; ref: ArtifactRef }
  | { kind: "skill"; card: SkillCardData }               // a skill the assistant made (skill_propose), to add
  | { kind: "todos"; items: Todo[] }
  | { kind: "notice"; text: string; tone: "info" | "fault" | "wait" }
  | ReviewItem
  | { kind: "user"; message: Message; id: string };      // written while the turn worked, now read by the model

/** The independent review of the turn's work (0.30.0): another model's check, its steps while it runs. */
export interface ReviewItem { kind: "review"; id: string; state: "running" | "ok" | "problems" | "failed"; model?: string;
  files?: string[]; artifacts?: string[]; report?: string; seconds?: number; changed?: string[]; error?: string;
  children?: Item[] }

export interface UserTurn { type: "user"; message: Message }
export interface AssistantTurn { type: "assistant"; key: string; runId: string | null; items: Item[]; run?: RunRecord;
  firstMessageId: number; text: string }
export type Turn = UserTurn | AssistantTurn;

function parseArgs(s: string): Record<string, unknown> | undefined {
  try {
    const v = JSON.parse(s || "{}");
    return v && typeof v === "object" ? v : undefined;
  } catch {
    return undefined;
  }
}

function lastTools(items: Item[]): ToolView[] {
  const last = items[items.length - 1];
  if (last && last.kind === "tools") return last.tools;
  const strip: Item = { kind: "tools", tools: [] };
  items.push(strip);
  return strip.tools;
}

/** Whether a call - here, inside a subagent or inside the review - waits for the user's answer. */
export function waitsForAnswer(items: Item[]): boolean {
  return items.some((it) => (it.kind === "tools" && it.tools.some((t) => t.state === "approval"
    || (t.children ? waitsForAnswer(t.children) : false)))
    || (it.kind === "review" && it.children ? waitsForAnswer(it.children) : false));
}

/** A review is a message of the Hub's in the turn, not the user's words. */
export function isReview(m: Message): boolean {
  return m.role === "user" && m.content.length > 0 && m.content.every((b) => b.type === "review");
}

function reviewItem(b: Extract<Block, { type: "review" }>): ReviewItem {
  return { kind: "review", id: b.id, state: b.verdict, model: b.model, files: b.files, artifacts: b.artifacts,
    report: b.report, seconds: b.seconds, changed: b.changed, error: b.error };
}

export function findTool(items: Item[], id: string): ToolView | undefined {
  for (const it of items) {
    if (it.kind === "review" && it.children) {
      const hit = findTool(it.children, id);
      if (hit) return hit;
    }
    if (it.kind !== "tools") continue;
    for (const t of it.tools) {
      if (t.id === id) return t;
      if (t.children) {
        const hit = findTool(t.children, id);
        if (hit) return hit;
      }
    }
  }
  return undefined;
}

/** Where a subagent's or the reviewer's steps go: the call that started it, or the review. */
function findHolder(items: Item[], id: string): { children?: Item[] } | undefined {
  const review = items.find((it): it is ReviewItem => it.kind === "review" && it.id === id);
  return review ?? findTool(items, id);
}

function afterResult(items: Item[], t: ToolView): void {
  const d = t.display;
  if (d?.artifact) items.push({ kind: "artifact", ref: d.artifact });
  if (t.name === "skill_propose" && d?.skill) items.push({ kind: "skill", card: d.skill as SkillCardData });
  if (t.name === "todo_write" && d?.todos) {
    const prev = items.findIndex((i) => i.kind === "todos");
    if (prev >= 0) items.splice(prev, 1);                // one list per turn: the latest
    items.push({ kind: "todos", items: d.todos });
  }
}

/** Why a turn is not done (0.22.0): it stopped at the step limit, or the user stopped it - null when it ended by
 *  itself. Continue goes on from there. */
export interface Unfinished { reason: "limit" | "stopped"; steps?: number }

export function unfinished(run: RunRecord | undefined): Unfinished | null {
  if (!run) return null;
  const limit = run.steps.find((s) => s.type === "limit" && !s.depth);
  if (limit) return { reason: "limit", steps: limit.steps };
  return run.status === "cancelled" ? { reason: "stopped" } : null;
}

/** A message that only says Continue (shown as a mark, not as the user's words). */
export function isContinue(m: Message): boolean {
  return m.content.length > 0 && m.content.every((b) => b.type === "continue");
}

/** Stored messages -> turns. */
export function buildTurns(messages: Message[], runs: RunRecord[], skipRun?: string | null): Turn[] {
  const byRun = new Map(runs.map((r) => [r.id, r]));
  const out: Turn[] = [];
  let cur: AssistantTurn | null = null;
  for (const m of messages) {
    if (skipRun && m.run_id === skipRun) continue;
    if (isReview(m)) {                                    // inside the turn it reviewed
      if (!cur || (m.run_id && cur.runId && m.run_id !== cur.runId)) {
        cur = { type: "assistant", key: `m${m.id}`, runId: m.run_id, items: [], firstMessageId: m.id, text: "",
          run: m.run_id ? byRun.get(m.run_id) : undefined };
        out.push(cur);
      }
      for (const b of m.content) if (b.type === "review") cur.items.push(reviewItem(b));
      continue;
    }
    if (m.role === "user") {
      cur = null;
      out.push({ type: "user", message: m });
      continue;
    }
    if (!cur || (m.run_id && cur.runId && m.run_id !== cur.runId)) {
      cur = { type: "assistant", key: `m${m.id}`, runId: m.run_id, items: [], firstMessageId: m.id, text: "",
        run: m.run_id ? byRun.get(m.run_id) : undefined };
      out.push(cur);
    }
    for (const b of m.content) {
      if (b.type === "notice" && b.text) cur.items.push({ kind: "notice", tone: "info", text: b.text });
      else if (b.type === "thinking" && b.text) cur.items.push({ kind: "thinking", text: b.text });
      else if (b.type === "text" && b.text) {
        cur.items.push({ kind: "text", text: b.text });
        cur.text += (cur.text ? "\n\n" : "") + b.text;
      } else if (b.type === "tool_call") {
        lastTools(cur.items).push({ id: b.id, name: b.name, argsText: b.arguments, args: parseArgs(b.arguments),
          state: "running" });
      } else if (b.type === "tool_result") {
        const t = findTool(cur.items, b.tool_call_id);
        if (!t) continue;
        t.result = b.text;
        t.images = b.images;
        t.display = b.display ?? null;
        t.state = b.is_error ? "error" : "done";
        afterResult(cur.items, t);
      }
    }
  }
  // A turn whose run was stopped can leave a call without a result.
  for (const t of out) if (t.type === "assistant") for (const it of t.items) if (it.kind === "tools")
    for (const tool of it.tools) if (tool.state === "running") tool.state = "error";
  return out;
}

// ------------------------------------------------------------ live runs

export interface LiveRun {
  runId: string;
  seq: number;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  position: number | null;
  model: string | null;
  started: number;
  items: Item[];
  error?: string;
  title?: string;
  steps: number;
  tokens: number;
  tps: number | null;
  pending: { id: string; content: Block[] }[];         // sent while it works, read after the step it is on
}

export function newLive(runId: string): LiveRun {
  return { runId, seq: 0, status: "queued", position: null, model: null, started: Date.now(), items: [], steps: 0,
    tokens: 0, tps: null, pending: [] };
}

/** A live run's items split where the user wrote something: assistant parts and user messages in order. */
export function liveSegments(items: Item[]): ({ kind: "assistant"; items: Item[] } | { kind: "user"; message: Message;
  id: string })[] {
  const out: ({ kind: "assistant"; items: Item[] } | { kind: "user"; message: Message; id: string })[] = [];
  for (const it of items) {
    if (it.kind === "user") {
      out.push({ kind: "user", message: it.message, id: it.id });
      continue;
    }
    const last = out[out.length - 1];
    if (last && last.kind === "assistant") last.items.push(it);
    else out.push({ kind: "assistant", items: [it] });
  }
  return out;
}

function applyStream(items: Item[], ev: RunEvent): void {
  const d = ev.data;
  switch (ev.type) {
    case "thinking": {
      const last = items[items.length - 1];
      if (last && last.kind === "thinking") last.text += d.text;
      else items.push({ kind: "thinking", text: d.text });
      break;
    }
    case "text": {
      const last = items[items.length - 1];
      if (last && last.kind === "text") last.text += d.text;
      else items.push({ kind: "text", text: d.text });
      break;
    }
    case "tool_start":
      if (!findTool(items, d.id)) lastTools(items).push({ id: d.id, name: d.name, argsText: "", state: "streaming" });
      break;
    case "tool_args": {
      const t = findTool(items, d.id);
      if (t) t.argsText += d.delta;
      break;
    }
    case "tool_call": {
      let t = findTool(items, d.id);
      if (!t) {
        t = { id: d.id, name: d.name, argsText: JSON.stringify(d.arguments ?? {}), state: "running" };
        lastTools(items).push(t);
      }
      t.args = d.arguments ?? parseArgs(t.argsText);
      t.state = "running";
      break;
    }
    case "approval_needed": {
      const t = findTool(items, d.id);
      if (t) {
        t.state = "approval";
        t.ask = d.ask;
        t.canChat = !!(d.can_chat || d.ask?.can_chat);
      }
      break;
    }
    case "approval_answered": {            // the buttons go; a reload replays this too
      const t = findTool(items, d.id);
      if (t && t.state === "approval") t.state = "running";
      break;
    }
    case "image_progress":
    case "audio_progress":
    case "video_progress": {
      const t = findTool(items, d.id);
      if (t && Number(d.max) > 0) t.progress = { value: Number(d.value) || 0, max: Number(d.max) };
      break;
    }
    case "tool_result": {
      const t = findTool(items, d.id);
      if (!t) break;
      t.result = d.text;
      t.images = d.images;
      t.display = d.display;
      t.seconds = d.seconds;
      t.state = d.ok ? "done" : "error";
      afterResult(items, t);
      break;
    }
    case "escalated":
      items.push({ kind: "notice", tone: "info", text: `Handed over to ${d.model}: ${d.reason}.` });
      break;
    case "loop":
      items.push({ kind: "notice", tone: "info", text: `It was repeating itself (${d.reason}), so it was asked to answer with what it has.` });
      break;
    case "compacted":
      items.push({ kind: "notice", tone: "info", text: "The earlier part of the chat was condensed to make room." });
      break;
    case "notes_reminder":                   // NOTES.md in long work (0.31.0)
      items.push({ kind: "notice", tone: "info", text: `It was reminded to write what it found in ${d.name} (step ${d.step}).` });
      break;
    case "review": {
      if (d.state === "started") {
        items.push({ kind: "review", id: d.id, state: "running", model: d.model, files: d.files,
          artifacts: d.artifacts, children: [] });
      } else {
        const r = items.find((it): it is ReviewItem => it.kind === "review" && it.id === d.id);
        if (r) Object.assign(r, { state: d.verdict, report: d.report, seconds: d.seconds, changed: d.changed,
          error: d.error });
      }
      break;
    }
  }
}

/** Apply one event; returns a new object so React re-renders. */
export function reduceLive(live: LiveRun, ev: RunEvent): LiveRun {
  if (ev.seq <= live.seq || ev.type === "ping") return live;
  const next: LiveRun = { ...live, seq: ev.seq, items: live.items };
  const d = ev.data;
  if (d.parent && d.depth) {
    const parent = findHolder(next.items, d.parent);
    if (parent) {
      parent.children ??= [];
      applyStream(parent.children, ev);
      next.items = [...next.items];
      return next;
    }
  }
  switch (ev.type) {
    case "queued":
      next.status = "queued";
      next.position = d.position ?? null;
      break;
    case "started":
      next.status = "running";
      next.model = d.model;
      break;
    case "escalated":
      if (d.model_id) next.model = d.model_id;          // handed over to another local model
      applyStream(next.items, ev);                      // and the notice
      break;
    case "subagent": {
      const t = findTool(next.items, d.id);
      if (t) t.subagent = { description: d.description, finished: d.state === "finished" };
      break;
    }
    case "usage":
      next.steps += 1;
      next.tokens += Number(d.completion_tokens ?? 0);
      if (d.tps) next.tps = d.tps;
      break;
    case "title":
      next.title = d.title;
      break;
    case "user_queued":
      if (!next.pending.some((p) => p.id === d.id)) next.pending = [...next.pending, { id: d.id, content: d.content ?? [] }];
      break;
    case "user_message":
      next.pending = next.pending.filter((p) => p.id !== d.id);
      next.items.push({ kind: "user", message: d.message, id: d.id });
      break;
    case "done":
      next.status = "done";
      break;
    case "cancelled":
      next.status = "cancelled";
      break;
    case "error":
      next.status = "error";
      next.error = d.message;
      break;
    default:
      applyStream(next.items, ev);
  }
  next.items = [...next.items];
  return next;
}

/** A short line for a tool row: what it did, in its own terms. */
export function toolSummary(t: ToolView): string {
  const a = t.args ?? {};
  const s = (k: string) => (typeof a[k] === "string" ? (a[k] as string) : "");
  switch (t.name) {
    case "bash": return s("command").split("\n")[0];
    case "windows_app": case "web_app": {
      const n = Array.isArray(a.steps) ? a.steps.length : 0;
      return s("target") + (n ? ` (${n} step${n === 1 ? "" : "s"})` : "");
    }
    case "read_file": case "write_file": case "edit_file": case "delete_file": case "view_image": return s("path");
    case "list_files": return s("path") || "the folder";
    case "move_file": return `${s("src") || s("source")} to ${s("dst") || s("destination")}`;
    case "search_files": return s("pattern");
    case "web_search": return s("query");
    case "web_fetch": return s("url");
    case "library_search": return s("query");
    case "image_generate": case "image_edit": case "music_generate": case "sound_generate": return s("prompt");
    case "video_generate": return (s("image") ? `${s("image")}: ` : "") + s("prompt");
    case "speech_generate": return s("text");
    case "image_transform": case "image_ocr": case "image_remove_background": return s("path") || s("image");
    case "memory": return `${s("command")} ${s("path")}`.trim();
    case "skill": return s("name");
    case "artifact": return s("title") || s("id");
    case "task": return s("description");
    case "escalate": return s("reason");
    default: {
      const first = Object.values(a).find((v) => typeof v === "string");
      return typeof first === "string" ? first : "";
    }
  }
}

export function toolLabel(name: string): string {
  const m = name.match(/^mcp__(.+?)__(.+)$/);
  if (m) return `${m[1]}: ${m[2]}`;
  return name.replace(/_/g, " ");
}
