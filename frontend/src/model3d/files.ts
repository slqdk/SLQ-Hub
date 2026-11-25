// Which files the 3D editor opens, and paths inside a working folder (0.87.0). Kept apart from three.js so the chat
// and the Files panel can ask without loading it.
export const MESH_FILE = /\.(stl|3mf|obj)$/i;
export const DESIGN_FILE = /\.3d\.json$/i;
export const OPENS_IN_3D = /\.(stl|3mf|obj)$|\.3d\.json$/i;

/** A path inside the working folder from a design's folder and a path written in the design ("../x.stl" too). */
export function joinPath(dir: string, rel: string): string {
  const out: string[] = [];
  for (const piece of [...dir.split("/"), ...rel.split("/")]) {
    if (!piece || piece === ".") continue;
    if (piece === "..") out.pop();
    else out.push(piece);
  }
  return out.join("/");
}

/** `path` as the design in `dir` writes it. */
export function relativePath(dir: string, path: string): string {
  const from = dir.split("/").filter(Boolean), to = path.split("/").filter(Boolean);
  let i = 0;
  while (i < from.length && i < to.length - 1 && from[i] === to[i]) i++;
  return [...from.slice(i).map(() => ".."), ...to.slice(i)].join("/");
}

export function dirOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? "" : path.slice(0, cut);
}

export function baseName(path: string): string {
  return path.split("/").pop() ?? path;
}

/** A path as the assistant may write it - relative, or the sandbox's own (/workspace/projects/3/...) - as a path in
 *  the folder (the Hub reads both the same way). */
export function inFolder(path: string, folder: { kind: "chat" | "project"; id: number }): string {
  const p = path.replace(/\\/g, "/").trim();
  if (!p.startsWith("/")) return p.replace(/^(\.\/)+/, "");
  const m = p.match(new RegExp(`^/(?:[^/]+/)*?${folder.kind === "project" ? "projects" : "chats"}/${folder.id}(?:/|$)`));
  return (m ? p.slice(m[0].length) : p).replace(/^\/+/, "");
}

/** A length as the editor shows it: at most two decimals (one from 100 mm), no trailing zeros. */
export const fmt = (v: number): string => (Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2)).replace(/\.?0+$/, "") || "0";

/** Which way a face looks, in words. */
export function facing(n: [number, number, number]): string {
  const [x, y, z] = n;
  if (z > 0.9) return "up";
  if (z < -0.9) return "down";
  const side = Math.abs(x) >= Math.abs(y) ? (x > 0 ? "+X (right)" : "-X (left)") : (y > 0 ? "+Y (back)" : "-Y (front)");
  return Math.abs(z) > 0.35 ? `${side}, ${z > 0 ? "upwards" : "downwards"}` : side;
}

/** A spot on the model as "Point at" puts it in the message - in the words the assistant's prompt shows it. */
export function pointMark(name: string, id: string, hole: boolean, at: [number, number, number],
  normal: [number, number, number]): string {
  return `[3D point: part ${JSON.stringify(name)} (id ${id}${hole ? ", a hole" : ""}) at x ${fmt(at[0])}, y ${fmt(at[1])}, `
    + `z ${fmt(at[2])} mm, on a face facing ${facing(normal)}]`;
}
