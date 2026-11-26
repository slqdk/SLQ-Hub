// The sketch (0.100.0). Søren: "Vi skal ha lavet en sketch sektion, ligesom i solid works, hvor man kan tegne en 2D
// tegning, og så boss extrude den ... Så kan man tegne lidt i frihånd med streger og cirkler før man extruder."
//
// Lines, rectangles and circles drawn on a plane - the plate (Top), the Front or Right plane, or a box's face - then
// made into outline parts: extruded out of the plane, or cut into what is there. The regions are read as SolidWorks
// reads a sketch: a closed shape inside another is a hole in it, one inside that hole an island again; shapes that
// cross or lie apart are each a part of their own (they join where they meet). On a box's face each part is kept on
// it (its "on", 0.98.0), so it goes with the box. The sketch itself is the editor's while it is drawn: the design gets
// the parts.

import { angles, faceFrame, MIN_SIZE, spotFrom, type FaceName, type Part, type Point2, type Vec3 } from "./geometry";

export type SketchPlane = { kind: "top" | "front" | "right" } | { kind: "face"; part: string; face: FaceName };
export type SketchShape =
  | { kind: "line"; pts: Point2[]; closed: boolean }   // clicked corners - closed when the last click met the first
  | { kind: "rect"; a: Point2; b: Point2 }               // two opposite corners
  | { kind: "circle"; c: Point2; r: number };

/** A plane in the design: its origin, which way is across it (U), up it (V) and out of it (N) - U x V = N; a box's
 *  face's middle and its frame (faceFrame), with its width and height. */
export interface PlaneFrame { O: Vec3; U: Vec3; V: Vec3; N: Vec3; W?: number; H?: number }

export const CIRCLE_SIDES = 64;

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const r3 = (v: number) => Math.round(v * 1000) / 1000 + 0;

export function planeFrame(plane: SketchPlane, box?: Part | null): PlaneFrame | null {
  switch (plane.kind) {
    case "top": return { O: [0, 0, 0], U: [1, 0, 0], V: [0, 1, 0], N: [0, 0, 1] };
    case "front": return { O: [0, 0, 0], U: [1, 0, 0], V: [0, 0, 1], N: [0, -1, 0] };
    case "right": return { O: [0, 0, 0], U: [0, 1, 0], V: [0, 0, 1], N: [1, 0, 0] };
    case "face": {
      if (!box || box.shape !== "box") return null;
      const F = faceFrame(box, plane.face);
      return { O: F.at(0, 0), U: F.U, V: F.V, N: F.N, W: F.W, H: F.H };
    }
  }
}

/** What a plane is called in the editor: "the plate", "the front of Lid". */
export function planeName(plane: SketchPlane, box?: Part | null): string {
  if (plane.kind !== "face") return { top: "the plate", front: "the front plane", right: "the right plane" }[plane.kind];
  return `the ${plane.face} of ${box?.name ? `"${box.name}"` : `box ${plane.part}`}`;
}

export const to3d = (f: PlaneFrame, [x, y]: Point2): Vec3 =>
  [f.O[0] + f.U[0] * x + f.V[0] * y, f.O[1] + f.U[1] * x + f.V[1] * y, f.O[2] + f.U[2] * x + f.V[2] * y];

export function to2d(f: PlaneFrame, w: Vec3): Point2 {
  const d: Vec3 = [w[0] - f.O[0], w[1] - f.O[1], w[2] - f.O[2]];
  return [dot(d, f.U), dot(d, f.V)];
}

/** Where a ray (the pointer's) meets the plane - null when it runs along it or away from it. */
export function rayOnPlane(f: PlaneFrame, origin: Vec3, dir: Vec3): Point2 | null {
  const den = dot(dir, f.N);
  if (Math.abs(den) < 1e-9) return null;
  const t = dot([f.O[0] - origin[0], f.O[1] - origin[1], f.O[2] - origin[2]], f.N) / den;
  if (t < 0) return null;
  return to2d(f, [origin[0] + dir[0] * t, origin[1] + dir[1] * t, origin[2] + dir[2] * t]);
}

/** A closed shape's outline as a loop of points - null for a line that is not closed, or one with no area. */
export function loopOf(s: SketchShape): Point2[] | null {
  let pts: Point2[];
  if (s.kind === "line") {
    if (!s.closed || s.pts.length < 3) return null;
    pts = s.pts;
  } else if (s.kind === "rect") {
    const [[x0, y0], [x1, y1]] = [s.a, s.b];
    pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  } else {
    if (s.r <= 0) return null;
    pts = [];
    for (let i = 0; i < CIRCLE_SIDES; i++) {
      const t = (2 * Math.PI * i) / CIRCLE_SIDES;
      pts.push([s.c[0] + s.r * Math.cos(t), s.c[1] + s.r * Math.sin(t)]);
    }
  }
  return Math.abs(area(pts)) > 0.01 ? pts : null;
}

/** The lines that draw a shape: round a closed one, as far as an open line goes. */
export function strokesOf(s: SketchShape): Point2[] {
  if (s.kind === "line") return s.closed && s.pts.length > 2 ? [...s.pts, s.pts[0]] : s.pts;
  const loop = loopOf(s);
  return loop ? [...loop, loop[0]] : [];
}

/** The points a click snaps to: corners, ends, a rectangle's and a circle's middle. */
export function snapPoints(shapes: SketchShape[]): Point2[] {
  const out: Point2[] = [];
  for (const s of shapes) {
    if (s.kind === "line") out.push(...s.pts);
    else if (s.kind === "rect") {
      const [[x0, y0], [x1, y1]] = [s.a, s.b];
      out.push([x0, y0], [x1, y0], [x1, y1], [x0, y1], [(x0 + x1) / 2, (y0 + y1) / 2]);
    } else out.push(s.c);
  }
  return out;
}

/** How far a point is from a shape's lines (mm) - a click near one picks it. */
export function distanceTo(s: SketchShape, p: Point2): number {
  if (s.kind === "circle") return Math.abs(Math.hypot(p[0] - s.c[0], p[1] - s.c[1]) - s.r);
  const pts = strokesOf(s);
  let best = Infinity;
  for (let i = 0; i + 1 < pts.length; i++) best = Math.min(best, toSegment(p, pts[i], pts[i + 1]));
  return pts.length === 1 ? Math.hypot(p[0] - pts[0][0], p[1] - pts[0][1]) : best;
}

function toSegment(p: Point2, a: Point2, b: Point2): number {
  const dx = b[0] - a[0], dy = b[1] - a[1], len = dx * dx + dy * dy;
  const t = len > 0 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

export function area(pts: Point2[]): number {
  let s = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[(i + 1) % pts.length];
    s += x0 * y1 - x1 * y0;
  }
  return s / 2;
}

function inside([x, y]: Point2, poly: Point2[]): boolean {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

/** The regions the closed shapes make: each one's outline and the holes in it. A shape with every corner inside a
 *  bigger one lies in it: inside an outline it is a hole, inside a hole an island - so a circle in a rectangle is a
 *  hole through the plate it makes; shapes that cross or lie apart are a region each. */
export function sketchRegions(shapes: SketchShape[]): { outer: Point2[]; holes: Point2[][] }[] {
  const loops = shapes.map(loopOf).filter((l): l is Point2[] => !!l);
  const size = loops.map((l) => Math.abs(area(l)));
  const holds = (i: number, j: number) => i !== j && size[i] > size[j] && loops[j].every((p) => inside(p, loops[i]));
  const parent = loops.map((_, j) => {
    let best = -1;
    for (let i = 0; i < loops.length; i++) if (holds(i, j) && (best < 0 || size[i] < size[best])) best = i;
    return best;
  });
  const depth = (j: number) => {
    let d = 0;
    for (let k = parent[j]; k >= 0 && d < loops.length; k = parent[k]) d++;
    return d;
  };
  return loops.flatMap((outer, i) => (depth(i) % 2 ? [] : [{ outer, holes: loops.filter((_, j) => parent[j] === i) }]));
}

/** What the sketch makes (`how`): extruded so many mm out of the plane, or cut - through all of it (a box's, or on
 *  another plane as far as `reach` mm both ways) or so deep. */
export interface Make { cut: boolean; depth: number; through: boolean; reach: number }

/** The parts a sketch makes: an outline for each region, its points about the region's middle. On a box's face each
 *  is kept on it ("on", 0.98.0: so many mm from the face's edges, as deep or as high as `how` says, placed by settle);
 *  on the plate it stands on it, on the front or right plane it stands out of it toward you - or, cut, goes into what
 *  is there. Named `name`, then "name (2)", "name (3)" ... for more than one; `id` gives each its id. */
export function sketchParts(plane: SketchPlane, shapes: SketchShape[], box: Part | null, how: Make, id: () => string,
  name: string, color?: string): Part[] {
  const f = planeFrame(plane, box);
  if (!f) return [];
  const regions = sketchRegions(shapes);
  const depth = Math.max(MIN_SIZE, how.depth);
  return regions.map(({ outer, holes }, i) => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [x, y] of outer) {
      x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
    const c: Point2 = [r3((x0 + x1) / 2), r3((y0 + y1) / 2)];
    const about = (loop: Point2[]): Point2[] => loop.map(([x, y]) => [r3(x - c[0]), r3(y - c[1])]);
    const p: Part = { id: id(), name: i ? `${name} (${i + 1})` : name, shape: "outline", size: [0, 0, depth],
      at: [0, 0, 0], rotate: [0, 0, 0], points: about(outer) };
    if (holes.length) p.holes = holes.map(about);
    if (how.cut) p.hole = true;
    else if (color) p.color = color;
    if (plane.kind === "face" && box) {
      // its spot in the face's own terms - across a mirrored box's face its own x runs the other way (U = V x N); one
      // in the middle stays there when the box grows
      const from = spotFrom(plane.face, box.mirror ? -c[0] : c[0], c[1], f.W ?? 0, f.H ?? 0, {}, 0);
      p.on = { part: box.id, face: plane.face, ...(from ? { from } : {}),
        ...(how.cut && how.through ? { through: true } : { depth }) };
      return p;
    }
    // on a plane of the design: out of it toward you; a cut from 0.2 mm behind it - or through all both ways
    const h = how.cut ? (how.through ? 2 * how.reach + 0.4 : depth + 0.2) : depth;
    const off = how.cut ? (how.through ? 0 : (depth - 0.2) / 2) : depth / 2;
    const o = to3d(f, c);
    p.size = [0, 0, r3(h)];
    p.at = [r3(o[0] + f.N[0] * off), r3(o[1] + f.N[1] * off), r3(o[2] + f.N[2] * off)];
    p.rotate = angles([f.U[0], f.V[0], f.N[0], f.U[1], f.V[1], f.N[1], f.U[2], f.V[2], f.N[2]]);
    return p;
  });
}
