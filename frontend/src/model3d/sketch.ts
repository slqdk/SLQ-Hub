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

// ---------------------------------------------------------------- distances (0.102.0)
//
// Søren, 06:52, a circle in a freehand shape with red lines from it to two of the shape's sides: "i need to be able to
// set distances in mm (the red lines)" - SolidWorks' Smart Dimension. A distance runs from a shape - the one that moves
// when it is set - square to a line: a side of another shape, or an edge of the face sketched on. Measured from the
// shape's nearest point (a circle's edge, as he drew them) or, `middle`, from its middle. Set, the shape moves square
// to the line - holding the latest other distance of it that runs another way, so two set fix it, as in SolidWorks.

/** A distance: from shape `shape` to a side of shape `to.shape` (its `seg`th, as strokesOf draws it) or to the face's
 *  edge `to.edge` (0 bottom, 1 right, 2 top, 3 left); `middle`: from the shape's middle, else its nearest point. */
export interface SketchDistance { shape: number; to: { shape: number; seg: number } | { edge: number }; middle?: boolean }
/** A size (0.103.0 - Søren: "Jeg burde kunne trykke på en linie, og så definere længden på linien i et inputfelt ved
 *  siden af linien"): the length of shape `shape`'s `side`th side - a line's or a rectangle's - or a circle's diameter
 *  (side 0). */
export interface SketchSize { shape: number; side: number }
export type SketchDim = SketchDistance | SketchSize;
export const isDistance = (d: SketchDim): d is SketchDistance => "to" in d;

/** The sides of a shape a distance can go to: a line's (open or closed), a rectangle's - a circle has none. */
export function sidesOf(s: SketchShape): [Point2, Point2][] {
  if (s.kind === "circle") return [];
  const pts = strokesOf(s), out: [Point2, Point2][] = [];
  for (let i = 0; i + 1 < pts.length; i++) out.push([pts[i], pts[i + 1]]);
  return out;
}

/** A face's edges in its plane (its middle at 0,0): bottom, right, top, left - none on the plate or a plane. */
export function faceEdges(f: PlaneFrame): [Point2, Point2][] {
  if (!f.W || !f.H) return [];
  const w = f.W / 2, h = f.H / 2;
  return [[[-w, -h], [w, -h]], [[w, -h], [w, h]], [[w, h], [-w, h]], [[-w, h], [-w, -h]]];
}

/** A shape's middle: a circle's, a rectangle's, the middle of the box round a line's corners. */
export function middleOf(s: SketchShape): Point2 {
  if (s.kind === "circle") return s.c;
  const pts = s.kind === "rect" ? [s.a, s.b] : s.pts;
  const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
}

/** A distance as it is now: how far (mm), from where on the shape to the foot on the line, the line's ends, and the
 *  unit direction from the line toward the shape - the one a value set moves it along. null: what it names is gone. */
export function measure(shapes: SketchShape[], f: PlaneFrame, d: SketchDim):
  { value: number; from: Point2; foot: Point2; n: Point2; a: Point2; b: Point2 } | null {
  if (!isDistance(d)) return null;                     // a size: dimLook, sizeOf
  const s = shapes[d.shape];
  const side = "edge" in d.to ? faceEdges(f)[d.to.edge] : shapes[d.to.shape] ? sidesOf(shapes[d.to.shape])[d.to.seg] : undefined;
  if (!s || !side) return null;
  const [a, b] = side, len = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (len < 1e-9) return null;
  let n: Point2 = [-(b[1] - a[1]) / len, (b[0] - a[0]) / len];
  const out = (q: Point2) => n[0] * (q[0] - a[0]) + n[1] * (q[1] - a[1]);
  const mid = middleOf(s);
  if (out(mid) < 0) n = [-n[0], -n[1]];
  let from = mid;
  if (!d.middle) {
    if (s.kind === "circle") from = [s.c[0] - n[0] * s.r, s.c[1] - n[1] * s.r];
    else {
      const pts = s.kind === "rect" ? [s.a, [s.b[0], s.a[1]], s.b, [s.a[0], s.b[1]]] as Point2[] : s.pts;
      for (const q of pts) if (out(q) < out(from) - 1e-9) from = q;
    }
  }
  const value = out(from);
  return { value, from, foot: [from[0] - n[0] * value, from[1] - n[1] * value], n, a, b };
}

/** A shape moved by `by`. */
export function moveShape(s: SketchShape, by: Point2): SketchShape {
  const m = (q: Point2): Point2 => [q[0] + by[0], q[1] + by[1]];
  if (s.kind === "circle") return { ...s, c: m(s.c) };
  if (s.kind === "rect") return { ...s, a: m(s.a), b: m(s.b) };
  return { ...s, pts: s.pts.map(m) };
}

/** The shapes with distance `i` set to `value` mm: its shape moved square to its line - holding the latest other
 *  distance of that shape that runs another way. A size: setSize. null when it names what is gone. */
export function setDim(shapes: SketchShape[], f: PlaneFrame, dims: SketchDim[], i: number, value: number):
  SketchShape[] | null {
  if (dims[i] && !isDistance(dims[i])) return setSize(shapes, f, dims, i, value);
  const d = dims[i], m = d ? measure(shapes, f, d) : null;
  if (!d || !m) return null;
  const want = value - m.value;
  let by: Point2 = [m.n[0] * want, m.n[1] * want];
  for (let j = dims.length - 1; j >= 0; j--) {
    const o = j !== i && dims[j].shape === d.shape ? measure(shapes, f, dims[j]) : null;
    const det = o ? m.n[0] * o.n[1] - m.n[1] * o.n[0] : 0;
    if (!o || Math.abs(det) < 1e-6) continue;          // along the same line: both cannot be held
    by = [(want * o.n[1]) / det, (-want * o.n[0]) / det];
    break;
  }
  return shapes.map((s, k) => (k === d.shape ? moveShape(s, by) : s));
}

/** How long a size is: its side, or its circle's diameter - null when it is gone. */
export function sizeOf(shapes: SketchShape[], d: SketchSize): number | null {
  const s = shapes[d.shape];
  if (!s) return null;
  if (s.kind === "circle") return 2 * s.r;
  const e = sidesOf(s)[d.side];
  return e ? Math.hypot(e[1][0] - e[0][0], e[1][1] - e[0][1]) : null;
}

/** The shapes with size `i` set to `value` mm (more than 0): a circle's diameter; a rectangle's width or height, its
 *  first corner where it is; a line's side from the end of it no other size holds - its second end, unless only that
 *  one is held - the next side going with the end moved, as SolidWorks moves a line's free end. Then the shape's
 *  distances as they were: it moved back to them, as SolidWorks keeps a sketch's dimensions. */
function setSize(shapes: SketchShape[], f: PlaneFrame, dims: SketchDim[], i: number, value: number): SketchShape[] | null {
  const d = dims[i] as SketchSize, s = shapes[d.shape];
  if (!s || !(value > 0)) return null;
  let next: SketchShape;
  if (s.kind === "circle") next = { ...s, r: value / 2 };
  else if (s.kind === "rect") {
    const k = d.side % 2, b: Point2 = [s.b[0], s.b[1]];
    b[k] = s.a[k] + (s.b[k] < s.a[k] ? -1 : 1) * value;
    next = { ...s, b };
  } else {
    const n = s.pts.length, sides = sidesOf(s).length, A = d.side, B = (A + 1) % n;
    if (A >= sides) return null;
    const pa = s.pts[A], pb = s.pts[B], len = Math.hypot(pb[0] - pa[0], pb[1] - pa[1]);
    if (len < 1e-9) return null;
    const u: Point2 = [(pb[0] - pa[0]) / len, (pb[1] - pa[1]) / len];
    const held = (v: number) => dims.some((o, j) => j !== i && !isDistance(o) && o.shape === d.shape && o.side < sides
      && (o.side === v || (o.side + 1) % n === v));
    const pts = [...s.pts];
    if (held(B) && !held(A)) pts[A] = [pb[0] - u[0] * value, pb[1] - u[1] * value];
    else pts[B] = [pa[0] + u[0] * value, pa[1] + u[1] * value];
    next = { ...s, pts };
  }
  let out: SketchShape[] = shapes.map((x, k) => (k === d.shape ? next : x));
  dims.forEach((o, j) => {                             // its distances held: the latest last, so it wins
    const was = isDistance(o) && o.shape === d.shape ? measure(shapes, f, o)?.value : undefined;
    if (was !== undefined) out = setDim(out, f, dims, j, was) ?? out;
  });
  return out;
}

/** Whether two sizes are the same one: of one shape - a rectangle's width (or height) on either of its sides, a
 *  circle's diameter, a line's side. */
export function sameSize(shapes: SketchShape[], a: SketchSize, b: SketchSize): boolean {
  const s = shapes[a.shape];
  if (!s || a.shape !== b.shape) return false;
  return s.kind === "circle" || (s.kind === "rect" ? a.side % 2 === b.side % 2 : a.side === b.side);
}

/** The side of a shape nearest a point (a circle: 0, its diameter). */
export function sideOf(s: SketchShape, p: Point2): number {
  let best = 0, gap = Infinity;
  sidesOf(s).forEach(([a, b], i) => {
    const g = toSegment(p, a, b);
    if (g < gap) [gap, best] = [g, i];
  });
  return best;
}

/** How a distance or a size is drawn: its lines, where its mm go, how many mm, what it is - and the line a click picks
 *  it by. A length runs beside its side, `off` mm out from the shape, with a line from each end (as SolidWorks draws
 *  one); a diameter from the circle's top up to its mm. */
export function dimLook(shapes: SketchShape[], f: PlaneFrame, d: SketchDim, off: number): { lines: Point2[][]; label: Point2;
  value: number; what: "distance" | "length" | "diameter"; pick: [Point2, Point2] } | null {
  if (isDistance(d)) {
    const m = measure(shapes, f, d);
    if (!m) return null;
    const lines: Point2[][] = [[m.from, m.foot]];
    const t = (m.foot[0] - m.a[0]) * (m.b[0] - m.a[0]) + (m.foot[1] - m.a[1]) * (m.b[1] - m.a[1]);
    if (t < 0) lines.push([m.a, m.foot]);
    else if (t > (m.b[0] - m.a[0]) ** 2 + (m.b[1] - m.a[1]) ** 2) lines.push([m.b, m.foot]);
    return { lines, label: [(m.from[0] + m.foot[0]) / 2, (m.from[1] + m.foot[1]) / 2], value: m.value, what: "distance",
      pick: [m.from, m.foot] };
  }
  const s = shapes[d.shape];
  if (!s) return null;
  if (s.kind === "circle") {
    const top: Point2 = [s.c[0], s.c[1] + s.r], at: Point2 = [s.c[0], s.c[1] + s.r + off];
    return { lines: [[top, at]], label: at, value: 2 * s.r, what: "diameter", pick: [top, at] };
  }
  const e = sidesOf(s)[d.side];
  if (!e) return null;
  const [a, b] = e, len = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (len < 1e-9) return null;
  let n: Point2 = [-(b[1] - a[1]) / len, (b[0] - a[0]) / len];
  const mid = middleOf(s);
  if (n[0] * (mid[0] - a[0]) + n[1] * (mid[1] - a[1]) > 1e-9) n = [-n[0], -n[1]];    // out, away from its middle
  const o = (q: Point2, k: number): Point2 => [q[0] + n[0] * k, q[1] + n[1] * k];
  return { lines: [[a, o(a, off * 1.3)], [b, o(b, off * 1.3)], [o(a, off), o(b, off)]],
    label: [(a[0] + b[0]) / 2 + n[0] * off, (a[1] + b[1]) / 2 + n[1] * off], value: len, what: "length",
    pick: [o(a, off), o(b, off)] };
}

/** The side nearest a point within `tol` mm - of a shape other than `not`, or the face's edge. */
export function sideAt(shapes: SketchShape[], f: PlaneFrame, p: Point2, tol: number, not: number): SketchDistance["to"] | null {
  let best: SketchDistance["to"] | null = null, gap = tol;
  shapes.forEach((s, k) => {
    if (k !== not) sidesOf(s).forEach(([a, b], seg) => {
      const g = toSegment(p, a, b);
      if (g < gap) [gap, best] = [g, { shape: k, seg }];
    });
  });
  faceEdges(f).forEach(([a, b], edge) => {
    const g = toSegment(p, a, b);
    if (g < gap) [gap, best] = [g, { edge }];
  });
  return best;
}

/** The distance or size whose line or mm are nearest a point within `tol` mm - null when none is. `off`: as dimLook. */
export function dimAt(shapes: SketchShape[], f: PlaneFrame, dims: SketchDim[], p: Point2, tol: number, off = 0): number | null {
  let best: number | null = null, gap = tol;
  dims.forEach((d, i) => {
    const look = dimLook(shapes, f, d, off);
    const g = look ? Math.min(toSegment(p, ...look.pick), Math.hypot(p[0] - look.label[0], p[1] - look.label[1])) : Infinity;
    if (g < gap) [gap, best] = [g, i];
  });
  return best;
}

/** The distances once shape `k` is taken away: those from it or to it gone, the shapes after it one back. */
export function dimsWithout(dims: SketchDim[], k: number): SketchDim[] {
  return dims.filter((d) => d.shape !== k && !(isDistance(d) && "shape" in d.to && d.to.shape === k)).map((d) => {
    const shape = d.shape > k ? d.shape - 1 : d.shape;
    if (!isDistance(d)) return { ...d, shape };
    return { ...d, shape, to: "shape" in d.to && d.to.shape > k ? { ...d.to, shape: d.to.shape - 1 } : d.to };
  });
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
