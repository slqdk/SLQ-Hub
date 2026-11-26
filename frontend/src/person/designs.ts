// The 3D editor in a person's chat (0.93.0): its designs and meshes are the chat's files on this device - sent with
// every message like the rest, so the assistant reads and changes them, and what it changes comes back here, where
// the editor sees it.
import type { DesignFolder } from "../model3d/folder";
import * as S from "./store";

/** A device turn's folder as the sandbox sees it ("/workspace/device/run_…/"): what follows is the chat's path. */
const TURN_FOLDER = /^\/(?:[^/]+\/)*?device\/run_[0-9a-f]+(?:\/|$)/;

const made = new Map<string, DesignFolder>();       // one each, so the editor's menus see the same one

export function deviceFolder(chat: string): DesignFolder {
  let f = made.get(chat);
  if (!f) made.set(chat, f = makeDeviceFolder(chat));
  return f;
}

function makeDeviceFolder(chat: string): DesignFolder {
  const store = (path: string, blob: Blob, mime: string) =>
    S.putFile({ chat, path, mime, size: blob.size, blob, at: Date.now() });
  return {
    key: `device/${chat}`,
    where: "this chat's files",
    readText: async (path) => {
      const f = await S.getFile(chat, path);
      return f ? f.blob.text() : null;
    },
    has: async (path) => !!(await S.getFile(chat, path)),
    writeText: async (path, text) => store(path, new Blob([text], { type: "application/json" }), "application/json"),
    readBytes: async (path) => {
      const f = await S.getFile(chat, path);
      if (!f) throw new Error(`there is no ${path} in this chat's files`);
      return f.blob.arrayBuffer();
    },
    list: async () => (await S.listFiles(chat)).map((f) => ({ path: f.path, modified: f.at })),
    put: async (file, name, dir) => {
      const path = dir ? `${dir}/${name}` : name;
      await store(path, file, file.type || "application/octet-stream");
      return path;
    },
    local: (path) => {
      const p = path.replace(/\\/g, "/").trim();
      const m = p.match(TURN_FOLDER);
      return (m ? p.slice(m[0].length) : p).replace(/^(\.\/)+/, "").replace(/^\/+/, "");
    },
  };
}
