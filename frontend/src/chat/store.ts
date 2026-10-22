// Conversations live on this device only (IndexedDB): the Hub keeps nothing
// of them, and no one else on the Hub can read them. Two stores: "chats"
// holds the list (title, dates), "bodies" the messages with their pictures.

export interface Msg {
  id: string;
  role: "user" | "assistant";
  kind: "chat" | "picture";
  text: string;
  photos?: string[];          // the user's photos, as data URLs
  picture?: string;           // a picture the Hub made, as a data URL
  size?: string;              // the picture's shape (square, portrait, landscape)
  reasoning?: string;
  model?: string;
  status?: "done" | "stopped" | "error";
  error?: string;
  at: number;
  seconds?: number;
}

export interface ChatMeta { id: string; title: string; created: number; updated: number }

const DB_NAME = "slq-chat";
let dbp: Promise<IDBDatabase> | null = null;
let memory: { metas: Map<string, ChatMeta>; bodies: Map<string, Msg[]> } | null = null;

function open(): Promise<IDBDatabase> {
  if (!dbp) {
    dbp = new Promise((resolve, reject) => {
      let req: IDBOpenDBRequest;
      try {
        req = indexedDB.open(DB_NAME, 1);
      } catch (e) {
        reject(e);
        return;
      }
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("chats")) db.createObjectStore("chats", { keyPath: "id" });
        if (!db.objectStoreNames.contains("bodies")) db.createObjectStore("bodies");
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
      req.onblocked = () => reject(new Error("the chat store is blocked"));
    });
  }
  return dbp;
}

/** False when this browser keeps nothing (private browsing in some browsers): chats last until the page closes. */
export async function persistent(): Promise<boolean> {
  try {
    await open();
    return true;
  } catch {
    memory ??= { metas: new Map(), bodies: new Map() };
    return false;
  }
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error("aborted"));
  });
}

function result<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function listChats(): Promise<ChatMeta[]> {
  if (memory) return [...memory.metas.values()].sort((a, b) => b.updated - a.updated);
  const db = await open();
  const all = await result(db.transaction("chats").objectStore("chats").getAll() as IDBRequest<ChatMeta[]>);
  return all.sort((a, b) => b.updated - a.updated);
}

export async function loadMessages(id: string): Promise<Msg[]> {
  if (memory) return memory.bodies.get(id) ?? [];
  const db = await open();
  return (await result(db.transaction("bodies").objectStore("bodies").get(id) as IDBRequest<Msg[] | undefined>)) ?? [];
}

export async function saveChat(meta: ChatMeta, messages: Msg[]): Promise<void> {
  if (memory) {
    memory.metas.set(meta.id, meta);
    memory.bodies.set(meta.id, messages);
    return;
  }
  const db = await open();
  const tx = db.transaction(["chats", "bodies"], "readwrite");
  tx.objectStore("chats").put(meta);
  tx.objectStore("bodies").put(messages, meta.id);
  await done(tx);
}

export async function deleteChat(id: string): Promise<void> {
  if (memory) {
    memory.metas.delete(id);
    memory.bodies.delete(id);
    return;
  }
  const db = await open();
  const tx = db.transaction(["chats", "bodies"], "readwrite");
  tx.objectStore("chats").delete(id);
  tx.objectStore("bodies").delete(id);
  await done(tx);
}

export function newId(): string {
  const a = new Uint8Array(8);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** A chat's name: the start of its first message. */
export function titleFor(text: string): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (!t) return "Photo";
  return t.length > 48 ? t.slice(0, 46).trimEnd() + "..." : t;
}
