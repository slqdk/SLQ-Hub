// Where the 3D editor keeps its designs (0.93.0): the Hub's chat or project folder - the owner's - or a person's chat
// on her own device (person/designs.ts). The editor, its 3D menu and its import list read and write through this, the
// same either way.
import { api, ApiError, type FileData, type TreeData } from "../lib/api";
import type { Folder } from "../components/Files";
import { inFolder } from "./files";

export interface DesignFolder {
  /** Which folder: another key opens the editor afresh. */
  key: string;
  /** Where its files are, in words: "the folder", "this chat's files". */
  where: string;
  /** A text file's text - null when there is no such file, or it is not text. */
  readText(path: string): Promise<string | null>;
  /** Whether there is a file there (of any kind). */
  has(path: string): Promise<boolean>;
  writeText(path: string, text: string): Promise<void>;
  /** A file's bytes (a mesh); it throws, saying so, when there is none. */
  readBytes(path: string): Promise<ArrayBuffer>;
  /** Every file, with when it last changed (only the order counts). */
  list(): Promise<{ path: string; modified: number }[]>;
  /** A file into `dir` under its name - one of that name is replaced: the path it got. */
  put(file: Blob, name: string, dir: string): Promise<string>;
  /** A path as the assistant may write it in the sandbox ("/workspace/chats/7/tag.3d.json"), as the folder's. */
  local(path: string): string;
}

const made = new Map<string, DesignFolder>();       // one each, so the editor's menus see the same one

/** The owner's: a chat's or a project's folder on the Hub. */
export function hubFolder(folder: Folder): DesignFolder {
  const key = `${folder.kind}/${folder.id}`;
  let f = made.get(key);
  if (!f) made.set(key, f = makeHubFolder(folder));
  return f;
}

function makeHubFolder(folder: Folder): DesignFolder {
  const base = `/hub/api/folders/${folder.kind}/${folder.id}`;
  const read = async (path: string): Promise<FileData | null> => {
    try {
      return await api<FileData>(`${base}/file`, { query: { path } });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  };
  return {
    key: `${folder.kind}/${folder.id}`,
    where: "the folder",
    readText: async (path) => (await read(path))?.text ?? null,
    has: async (path) => (await read(path)) !== null,
    writeText: async (path, text) => { await api(`${base}/file`, { method: "PUT", body: { path, text } }); },
    readBytes: async (path) => {
      const r = await fetch(`${base}/raw?path=${encodeURIComponent(path)}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error(r.status === 404 ? `there is no ${path} in the folder` : `${path}: HTTP ${r.status}`);
      return r.arrayBuffer();
    },
    list: async () => (await api<TreeData>(`${base}/tree`)).entries.filter((e) => !e.dir)
      .map((e) => ({ path: e.path, modified: e.modified })),
    put: async (file, name, dir) => {
      const fd = new FormData();
      fd.append("files", new File([file], name));
      fd.append("dir", dir);
      const r = await api<{ saved: string[] }>(`${base}/upload`, { form: fd });
      return r.saved[0] ?? (dir ? `${dir}/${name}` : name);
    },
    local: (path) => inFolder(path, folder),
  };
}

/** A message is about to go: the 3D editor, if one is open, saves what it has not yet (0.93.0) - a person's chat sends
 *  its files as they are at that moment, and the owner's assistant may read the design at once. */
export async function saveOpenDesign(): Promise<void> {
  const waits: Promise<void>[] = [];
  window.dispatchEvent(new CustomEvent("slq:3d-save", { detail: waits }));
  await Promise.allSettled(waits);
}
