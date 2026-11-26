// Parts in a design's tree (0.87.0): finding, changing, adding and taking them out without touching the rest.
import { allIds, extent, extentOf, freshId, type Box3, type Part, type Vec3 } from "./geometry";

export interface Found { part: Part; siblings: Part[]; index: number; top: Part; parent: Part | null }

export function find(parts: Part[], id: string, top: Part | null = null, parent: Part | null = null): Found | null {
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (p.id === id) return { part: p, siblings: parts, index: i, top: top ?? p, parent };
    if (p.parts) {
      const f = find(p.parts, id, top ?? p, p);
      if (f) return f;
    }
  }
  return null;
}

export function update(parts: Part[], id: string, fn: (p: Part) => Part): Part[] {
  return parts.map((p) => (p.id === id ? fn(p) : p.parts ? { ...p, parts: update(p.parts, id, fn) } : p));
}

export function remove(parts: Part[], ids: Set<string>): Part[] {
  return parts.filter((p) => !ids.has(p.id)).map((p) => (p.parts ? { ...p, parts: remove(p.parts, ids) } : p));
}

/** A copy with new ids everywhere (for Duplicate), taking ids from `seen`. */
export function copy(p: Part, seen: Set<string>): Part {
  const id = freshId(seen);
  seen.add(id);
  return { ...p, id, parts: p.parts?.map((c) => copy(c, seen)) };
}

/** The parts with these ids, in the tree's order. */
export function pick(parts: Part[], ids: string[]): Part[] {
  return ids.map((id) => find(parts, id)?.part).filter((p): p is Part => !!p);
}

export function boxOf(parts: Part[], ids: string[]): Box3 | null {
  return extentOf(pick(parts, ids));
}

export function centre(b: Box3): Vec3 {
  return [(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2, (b[0][2] + b[1][2]) / 2];
}

/** Join sibling parts into a group where the first of them was. null when they are not siblings. */
export function group(parts: Part[], ids: string[]): { parts: Part[]; id: string } | null {
  const found = ids.map((id) => find(parts, id));
  if (found.some((f) => !f) || new Set(found.map((f) => f!.siblings)).size !== 1) return null;
  const seen = allIds(parts);
  const id = freshId(seen);
  const members = new Set(ids);
  const siblings = found[0]!.siblings;
  const first = Math.min(...found.map((f) => f!.index));
  const gathered = siblings.filter((p) => members.has(p.id));
  const g: Part = { id, shape: "group", size: [0, 0, 0], at: [0, 0, 0], rotate: [0, 0, 0], parts: gathered };
  const replace = (list: Part[]): Part[] => {
    if (list === siblings) {
      const out: Part[] = [];
      list.forEach((p, i) => {
        if (i === first) out.push(g);
        if (!members.has(p.id)) out.push(p);
      });
      return out;
    }
    return list.map((p) => (p.parts ? { ...p, parts: replace(p.parts) } : p));
  };
  return { parts: replace(parts), id };
}

/** The tree with one part replaced by others, where it was (Letters apart). */
export function replaceWith(parts: Part[], id: string, by: Part[]): Part[] {
  return parts.flatMap((p) => (p.id === id ? by : p.parts ? [{ ...p, parts: replaceWith(p.parts, id, by) }] : [p]));
}

/** A group's parts in its place. */
export function ungroup(parts: Part[], id: string): Part[] {
  return parts.flatMap((p) => (p.id === id && p.shape === "group" ? p.parts ?? []
    : p.parts ? [{ ...p, parts: ungroup(p.parts, id) }] : [p]));
}

export function label(p: Part): string {
  return p.name || (p.shape === "text" ? `“${(p.text ?? "").split("\n")[0].slice(0, 20)}”` : `${p.shape} ${p.id}`);
}

export function bottom(p: Part): number {
  return extent(p)?.[0][2] ?? 0;
}
