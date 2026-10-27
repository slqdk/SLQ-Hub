// A person's answer: the chat and its files go to the Hub with the message,
// the answer streams back, and its pictures and files come home to this
// device before the Hub is told to forget the turn.
import { api, type Block, type RunEvent, type RunRecord, type Step } from "../lib/api";
import * as S from "./store";

const MAX_SEND = 60 * 1024 * 1024;      // files and pictures sent with one message (before base64)
const RECENT = 12;                       // messages from the end whose pictures go along (as the model sees them)

export interface DeviceResult {
  status: "done" | "error" | "cancelled";
  error: string | null;
  title: string;
  messages: { role: "assistant" | "tool"; content: Block[] }[];
  files: { path: string; size: number; mime: string; skipped?: string }[];
  removed?: string[];                    // files of the chat the assistant deleted or moved away
  artifacts: { id: string; title: string; type: string; version: number; content: string }[];
  steps: Step[];
  usage: RunRecord["usage"];
  model: string | null;
}

function dataUrl(b: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(b);
  });
}

/** What the Hub needs for one answer: the conversation, the chat's files, recent pictures, the artifacts. */
export async function request(meta: S.ChatMeta, data: S.ChatData): Promise<Record<string, unknown>> {
  let total = 0;
  const files: { path: string; mime: string; data: string }[] = [];
  const left: { path: string; size: number }[] = [];       // too big to send each time: the model is told (0.38.0)
  for (const f of (await S.listFiles(meta.id)).sort((a, b) => b.at - a.at)) {    // the newest first
    if (total + f.size > MAX_SEND) {
      left.push({ path: f.path, size: f.size });
      continue;
    }
    total += f.size;
    files.push({ path: f.path, mime: f.mime, data: await dataUrl(f.blob) });
  }
  const paths = new Set(files.map((f) => f.path));
  const blobs: { sha: string; name: string; mime: string; data: string }[] = [];
  const seen = new Set<string>();
  const want = async (key: string) => {
    if (!key || seen.has(key)) return;
    seen.add(key);
    const b = await S.getBlob(key);
    if (!b || total + b.blob.size > MAX_SEND) return;
    total += b.blob.size;
    blobs.push({ sha: key, name: b.name, mime: b.mime, data: await dataUrl(b.blob) });
  };
  for (const m of data.messages.slice(-RECENT)) {
    for (const b of m.content) {
      if (b.type === "image" && !(b.path && paths.has(b.path))) await want(b.sha);
      if (b.type === "tool_result") for (const i of b.images ?? []) await want(i.sha);
    }
  }
  const artifacts = Object.values(data.artifacts).map((versions) => versions[versions.length - 1])
    .map(({ id, title, type, version, content }) => ({ id, title, type, version, content }));
  return { title: meta.title, settings: meta.settings, files, left, blobs, artifacts,
    messages: data.messages.map((m) => ({ role: m.role, content: m.content, created_at: m.created_at })) };
}

export async function start(body: Record<string, unknown>): Promise<string> {
  return (await api<{ turn_id: string }>("/hub/api/device/turns", { body })).turn_id;
}

export async function stop(turnId: string): Promise<void> {
  await api(`/hub/api/device/turns/${turnId}/stop`, { method: "POST" });
}

/** Follow the answer's events; resolves with the result when it is finished (null when the Hub lost it). */
export function follow(turnId: string, onEvent: (ev: RunEvent) => void): Promise<DeviceResult | null> {
  return new Promise((resolve) => {
    let result: DeviceResult | null = null;
    let ended = false;
    const es = new EventSource(`/hub/api/device/turns/${turnId}/events`);
    const finish = async () => {
      if (ended) return;
      ended = true;
      es.close();
      if (!result) {
        try {
          result = await api<DeviceResult>(`/hub/api/device/turns/${turnId}/result`);
        } catch {
          result = null;
        }
      }
      resolve(result);
    };
    es.onmessage = (m) => {
      let ev: RunEvent;
      try {
        ev = JSON.parse(m.data);
      } catch {
        return;
      }
      if (ev.type === "device_result") result = ev.data as DeviceResult;
      onEvent(ev);
    };
    es.addEventListener("end", () => void finish());
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) void finish();
    };
  });
}

/** Bring the answer's pictures and files to this device, and drop the files it deleted (the Hub forgets the
 * turn only with forget()). */
export async function collect(turnId: string, chat: string, result: DeviceResult): Promise<string[]> {
  const problems: string[] = [];
  for (const m of result.messages) {
    for (const b of m.content) {
      if (b.type !== "tool_result") continue;
      for (const i of b.images ?? []) {
        if (await S.getBlob(i.sha)) continue;
        try {
          const r = await fetch(`/hub/api/device/turns/${turnId}/blob/${i.sha}`, { credentials: "same-origin" });
          if (!r.ok) throw new Error(String(r.status));
          await S.putBlob({ key: i.sha, mime: i.mime || r.headers.get("content-type") || "image/png", name: `${i.sha.slice(0, 12)}.png`,
            blob: await r.blob() });
        } catch {
          problems.push("a picture");
        }
      }
    }
  }
  for (const f of result.files) {
    if (f.skipped) {
      problems.push(`${f.path} (${f.skipped})`);
      continue;
    }
    if (!(await fetchFile(turnId, chat, f.path, f.mime))) problems.push(f.path);
  }
  for (const path of result.removed ?? []) await S.deleteFile(chat, path);
  return problems;
}

/** One file of an answer from the Hub into this device's store, tried three times (a phone on the move drops
 * connections). The Hub keeps an answer's files until the device says it has them (forget), an hour at most. */
export async function fetchFile(turnId: string, chat: string, path: string, mime?: string): Promise<boolean> {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch(`/hub/api/device/turns/${turnId}/file?path=${encodeURIComponent(path)}`,
        { credentials: "same-origin" });
      if (r.status === 404) return false;                       // the Hub has let it go
      if (!r.ok) throw new Error(String(r.status));
      const blob = await r.blob();
      await S.putFile({ chat, path, mime: mime || blob.type, size: blob.size, blob, at: Date.now() });
      return true;
    } catch {
      await new Promise((ok) => setTimeout(ok, 700 * 3 ** attempt));
    }
  }
  return false;
}

/** The device has everything: the Hub deletes the turn's folder and files. */
export async function forget(turnId: string): Promise<void> {
  try {
    await api(`/hub/api/device/turns/${turnId}/done`, { method: "POST" });
  } catch {
    /* the Hub forgets it within the hour anyway */
  }
}

/** The answer's messages and stats into the chat's stored data (again after a reload: replaced, not doubled). */
export function merge(data: S.ChatData, turnId: string, result: DeviceResult, started: number): S.ChatData {
  const before = data.messages.filter((m) => m.run_id !== turnId);
  let id = before.reduce((n, m) => Math.max(n, m.id), 0);
  const now = new Date().toISOString();
  const messages = [...before, ...result.messages.map((m) => ({ id: ++id, role: m.role, content: m.content,
    run_id: turnId, created_at: now }))];
  const run: RunRecord = { id: turnId, chat_id: 0, status: result.status, model: result.model ?? "",
    started_at: new Date(started).toISOString(), ended_at: now, steps: result.steps ?? [], usage: result.usage ?? {},
    error: result.error };
  const artifacts = { ...data.artifacts };
  for (const a of result.artifacts ?? []) {
    const versions = artifacts[a.id] ?? [];
    if (!versions.length || versions[versions.length - 1].version < a.version) {
      artifacts[a.id] = [...versions, { ...a, at: now }];
    }
  }
  return { messages, runs: [...data.runs.filter((r) => r.id !== turnId), run], artifacts };
}
