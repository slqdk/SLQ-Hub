// A person's chats live on this device (IndexedDB), never on the Hub. One
// database per user name, so two people on one browser keep theirs apart.
//
//   chats   ChatMeta (title, dates, the composer's settings)
//   data    per chat: messages (stored blocks), runs (stats), artifacts (all versions)
//   files   per chat and path: the chat's files (attachments, what the assistant made)
//   blobs   pictures the messages show, by key (the user's photos, generated pictures)
import type { Block, ChatSettings, RunRecord } from "../lib/api";

export interface LocalMessage { id: number; role: "user" | "assistant" | "tool"; content: Block[]; run_id: string | null;
  created_at: string }
export interface LocalArtifact { id: string; title: string; type: string; version: number; content: string; at: string }
export interface ChatMeta { id: string; title: string; created: number; updated: number; settings: Partial<ChatSettings> }
export interface ChatData { messages: LocalMessage[]; runs: RunRecord[]; artifacts: Record<string, LocalArtifact[]> }
export interface LocalFile { chat: string; path: string; mime: string; size: number; blob: Blob; at: number }
export interface LocalBlob { key: string; mime: string; name: string; blob: Blob }

let dbp: Promise<IDBDatabase> | null = null;
let dbName = "";

export function selectDatabase(username: string): void {
  const name = `slq-hub-${username.toLowerCase()}`;
  if (name !== dbName) {
    dbName = name;
    dbp = null;
  }
}

function open(): Promise<IDBDatabase> {
  if (!dbName) return Promise.reject(new Error("no user"));
  if (!dbp) {
    dbp = new Promise((resolve, reject) => {
      let req: IDBOpenDBRequest;
      try {
        req = indexedDB.open(dbName, 1);
      } catch (e) {
        reject(e);
        return;
      }
      req.onupgradeneeded = () => {
        const db = req.result;
        db.createObjectStore("chats", { keyPath: "id" });
        db.createObjectStore("data");
        const files = db.createObjectStore("files", { keyPath: ["chat", "path"] });
        files.createIndex("chat", "chat");
        db.createObjectStore("blobs", { keyPath: "key" });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
      req.onblocked = () => reject(new Error("the chat store is blocked by another tab"));
    });
  }
  return dbp;
}

function result<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error("aborted"));
  });
}

/** False when this browser keeps nothing (private browsing in some browsers). */
export async function usable(): Promise<boolean> {
  try {
    await open();
    return true;
  } catch {
    return false;
  }
}

/** Ask the browser not to clear the chats when space runs short (granted on most phones once installed). */
export async function askToKeep(): Promise<boolean> {
  try {
    return (await navigator.storage?.persist?.()) ?? false;
  } catch {
    return false;
  }
}

export async function listChats(): Promise<ChatMeta[]> {
  const db = await open();
  const all = await result(db.transaction("chats").objectStore("chats").getAll() as IDBRequest<ChatMeta[]>);
  return all.sort((a, b) => b.updated - a.updated);
}

export async function getChat(id: string): Promise<{ meta: ChatMeta; data: ChatData } | null> {
  const db = await open();
  const tx = db.transaction(["chats", "data"]);
  const meta = await result(tx.objectStore("chats").get(id) as IDBRequest<ChatMeta | undefined>);
  if (!meta) return null;
  const data = await result(tx.objectStore("data").get(id) as IDBRequest<ChatData | undefined>);
  return { meta, data: data ?? { messages: [], runs: [], artifacts: {} } };
}

export async function saveChat(meta: ChatMeta, data: ChatData): Promise<void> {
  const db = await open();
  const tx = db.transaction(["chats", "data"], "readwrite");
  tx.objectStore("chats").put(meta);
  tx.objectStore("data").put(data, meta.id);
  await done(tx);
}

export async function saveMeta(meta: ChatMeta): Promise<void> {
  const db = await open();
  const tx = db.transaction("chats", "readwrite");
  tx.objectStore("chats").put(meta);
  await done(tx);
}

export async function deleteChat(id: string): Promise<void> {
  const db = await open();
  const data = (await getChat(id))?.data;
  const keys = new Set<string>();
  for (const m of data?.messages ?? []) for (const k of blobKeys(m.content)) keys.add(k);
  const files = await listFiles(id);
  const tx = db.transaction(["chats", "data", "files", "blobs"], "readwrite");
  tx.objectStore("chats").delete(id);
  tx.objectStore("data").delete(id);
  for (const f of files) tx.objectStore("files").delete([id, f.path]);
  for (const k of keys) tx.objectStore("blobs").delete(k);
  await done(tx);
}

// ------------------------------------------------------------ files

export async function listFiles(chat: string): Promise<LocalFile[]> {
  const db = await open();
  const idx = db.transaction("files").objectStore("files").index("chat");
  const all = await result(idx.getAll(chat) as IDBRequest<LocalFile[]>);
  return all.sort((a, b) => a.path.localeCompare(b.path));
}

export async function getFile(chat: string, path: string): Promise<LocalFile | undefined> {
  const db = await open();
  return result(db.transaction("files").objectStore("files").get([chat, path]) as IDBRequest<LocalFile | undefined>);
}

export async function putFile(f: LocalFile): Promise<void> {
  const db = await open();
  const tx = db.transaction("files", "readwrite");
  tx.objectStore("files").put(f);
  await done(tx);
}

export async function deleteFile(chat: string, path: string): Promise<void> {
  const db = await open();
  const tx = db.transaction("files", "readwrite");
  tx.objectStore("files").delete([chat, path]);
  await done(tx);
}

// ------------------------------------------------------------ pictures shown in messages

export async function putBlob(b: LocalBlob): Promise<void> {
  const db = await open();
  const tx = db.transaction("blobs", "readwrite");
  tx.objectStore("blobs").put(b);
  await done(tx);
}

export async function getBlob(key: string): Promise<LocalBlob | undefined> {
  const db = await open();
  return result(db.transaction("blobs").objectStore("blobs").get(key) as IDBRequest<LocalBlob | undefined>);
}

/** The picture keys a message's blocks point at. */
export function blobKeys(blocks: Block[]): string[] {
  const out: string[] = [];
  for (const b of blocks) {
    if ((b.type === "image" || b.type === "file") && b.sha) out.push(b.sha);
    if (b.type === "tool_result") for (const i of b.images ?? []) out.push(i.sha);
  }
  return out;
}

export function newKey(): string {
  const a = new Uint8Array(12);
  crypto.getRandomValues(a);
  return "l" + Array.from(a, (x) => x.toString(16).padStart(2, "0")).join("");
}

// ------------------------------------------------------------ export and import (one file with everything)

export interface Backup { app: "SLQ Hub"; kind: "chats"; format: 1; at: string; user: string;
  chats: { meta: ChatMeta; data: ChatData; files: { path: string; mime: string; data: string; at: number }[] }[];
  blobs: { key: string; mime: string; name: string; data: string }[] }

function toDataUrl(b: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(b);
  });
}

function fromDataUrl(url: string, mime: string): Blob {
  const data = url.includes(",") ? url.split(",", 2)[1] : url;
  const bin = atob(data);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

export async function exportAll(user: string): Promise<Backup> {
  const out: Backup = { app: "SLQ Hub", kind: "chats", format: 1, at: new Date().toISOString(), user, chats: [], blobs: [] };
  const keys = new Set<string>();
  for (const meta of await listChats()) {
    const c = await getChat(meta.id);
    if (!c) continue;
    const files = [];
    for (const f of await listFiles(meta.id)) files.push({ path: f.path, mime: f.mime, at: f.at, data: await toDataUrl(f.blob) });
    for (const m of c.data.messages) for (const k of blobKeys(m.content)) keys.add(k);
    out.chats.push({ meta, data: c.data, files });
  }
  for (const k of keys) {
    const b = await getBlob(k);
    if (b) out.blobs.push({ key: b.key, mime: b.mime, name: b.name, data: await toDataUrl(b.blob) });
  }
  return out;
}

/** Chats from an export file; a chat that is already here (same id) is replaced. Returns how many. */
export async function importAll(backup: Backup): Promise<number> {
  if (backup?.app !== "SLQ Hub" || backup.kind !== "chats" || !Array.isArray(backup.chats)) {
    throw new Error("This is not an export of SLQ Hub chats.");
  }
  for (const b of backup.blobs ?? []) await putBlob({ key: b.key, mime: b.mime, name: b.name, blob: fromDataUrl(b.data, b.mime) });
  for (const c of backup.chats) {
    await saveChat(c.meta, c.data);
    for (const f of c.files ?? []) {
      const blob = fromDataUrl(f.data, f.mime);
      await putFile({ chat: c.meta.id, path: f.path, mime: f.mime, size: blob.size, blob, at: f.at });
    }
  }
  return backup.chats.length;
}
