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

/** Whether a part cuts (0.95.0): a hole, or a part in a group that is one - null when there is no such part. */
export function cuts(parts: Part[], id: string, under = false): boolean | null {
  for (const p of parts) {
    const c = under || !!p.hole;
    if (p.id === id) return c;
    const r = p.parts ? cuts(p.parts, id, c) : null;
    if (r !== null) return r;
  }
  return null;
}

/** The parts that cut and are built (0.95.0): holes, and the parts of hole groups - not a hidden one. */
export function cutters(parts: Part[], under = false, out: Part[] = []): Part[] {
  for (const p of parts) {
    if (p.hidden) continue;
    const c = under || !!p.hole;
    if (p.parts) cutters(p.parts, c, out);
    else if (c) out.push(p);
  }
  return out;
}

export function update(parts: Part[], id: string, fn: (p: Part) => Part): Part[] {
  return parts.map((p) => (p.id === id ? fn(p) : p.parts ? { ...p, parts: update(p.parts, id, fn) } : p));
}

export function remove(parts: Part[], ids: Set<string>): Part[] {
  return parts.filter((p) => !ids.has(p.id)).map((p) => (p.parts ? { ...p, parts: remove(p.parts, ids) } : p));
}

/** A copy with new ids everywhere (for Duplicate), taking ids from `seen` - each old id's new one put in `ids`. */
export function copy(p: Part, seen: Set<string>, ids?: Map<string, string>): Part {
  const id = freshId(seen);
  seen.add(id);
  ids?.set(p.id, id);
  return { ...p, id, parts: p.parts?.map((c) => copy(c, seen, ids)) };
}

/** Copies on faces of copies (0.98.0): each "on" that names a part copied with them names its copy - and each mate
 *  (0.101.0). */
export function onCopies(parts: Part[], ids: Map<string, string>): Part[] {
  return parts.map((p) => {
    let q = p.on && ids.has(p.on.part) ? { ...p, on: { ...p.on, part: ids.get(p.on.part)! } } : p;
    if (q.mates?.some((m) => ids.has(m.to.part))) {
      q = { ...q, mates: q.mates.map((m) => (ids.has(m.to.part) ? { ...m, to: { ...m.to, part: ids.get(m.to.part)! } } : m)) };
    }
    return q.parts ? { ...q, parts: onCopies(q.parts, ids) } : q;
  });
}

/** The parts on faces of these (0.98.0) - and on theirs - that are not among them: they go where a box goes. And
 *  those mated to them first (0.101.0), which go with them as well. */
export function riders(parts: Part[], ids: string[]): Part[] {
  const moving = new Set<string>();
  for (const id of ids) {
    const f = find(parts, id);
    if (f) allIds([f.part], moving);
  }
  const leaves: Part[] = [];
  const walk = (list: Part[]) => list.forEach((p) => (p.parts ? walk(p.parts) : leaves.push(p)));
  walk(parts);
  const out: Part[] = [];
  for (let grew = true; grew;) {
    grew = false;
    for (const p of leaves) {
      const by = p.on?.part ?? p.mates?.[0]?.to.part;
      if (by !== undefined && moving.has(by) && !moving.has(p.id)) {
        moving.add(p.id);
        out.push(p);
        grew = true;
      }
    }
  }
  return out;
}

/** The parts with `add` just after the part `after`, beside it - in its group if it is in one; at the end when there
 *  is no such part. */
export function insertAfter(parts: Part[], after: string, add: Part): Part[] {
  const into = (list: Part[]): Part[] | null => {
    const i = list.findIndex((p) => p.id === after);
    if (i >= 0) return [...list.slice(0, i + 1), add, ...list.slice(i + 1)];
    for (let j = 0; j < list.length; j++) {
      const inner = list[j].parts ? into(list[j].parts!) : null;
      if (inner) return list.map((p, k) => (k === j ? { ...p, parts: inner } : p));
    }
    return null;
  };
  return into(parts) ?? [...parts, add];
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
