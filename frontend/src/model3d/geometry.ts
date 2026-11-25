// The 3D editor's designs (0.87.0): the .3d.json format, and the one solid a design makes, built with manifold-3d.
//
// A design is a list of parts in millimetres, Z up, the plate at z = 0 with its centre at x = y = 0. Each part is a
// shape (box, cylinder, cone, sphere, wedge, text, an outline, an imported mesh, or a group of parts) as big as
// `size` (its bounding box before it is turned), mirrored in its own X when `mirror`, turned by `rotate` (degrees:
// first about X, then Y, then Z - as OpenSCAD's rotate) and moved so its centre is at `at` - an outline (0.88.0) so
// its points' 0,0 is there, as OpenSCAD's translate(at) rotate(r) linear_extrude(center = true) polygon(points). A
// part or group can be repeated: copies turned about the up axis, or in a row. Parts that are holes are cut out of
// the solids beside them - in their group, or in the design itself - as in Tinkercad.
//
// Nothing here imports anything (manifold-3d and the font are handed in), so the browser and a test run with node
// (--experimental-strip-types) use the same code. services/sandbox/hub3d.py's design() builds designs the same way
// in the sandbox, for the assistant; tests/test_design3d.py runs both on the same designs and compares.

export const FORMAT = "slq-hub-3d";
export const VERSION = 1;
export const DEFAULT_SIDES = 64;
export const MIN_SIZE = 0.01;

export type Vec3 = [number, number, number];
export type Shape = "box" | "cylinder" | "cone" | "sphere" | "wedge" | "text" | "outline" | "mesh" | "group";
export const SHAPES: readonly Shape[] = ["box", "cylinder", "cone", "sphere", "wedge", "text", "outline", "mesh", "group"];
export const MAX_COPIES = 100;
/** The keys a part may have; others are said once (a model guessing the format: "type", "radius", "position"). */
export const PART_KEYS = ["id", "name", "shape", "size", "at", "rotate", "mirror", "hole", "hidden", "color", "sides",
  "radius", "top", "text", "points", "holes", "file", "repeat", "parts"];

export type Point2 = [number, number];

/** Copies of a part or a group, joined to it (0.88.0): `count` in all, turned about the up axis through `around` -
 *  evenly all round, or over `angle` degrees - or each moved on by `step`. */
export interface Repeat {
  count: number;
  around?: Point2;            // default: the part's at (a group's: 0, 0)
  angle?: number;             // default 360: all round
  step?: Vec3;                // a row instead of a circle
}
export const ROUND: readonly Shape[] = ["cylinder", "cone", "sphere"];

export interface Part {
  id: string;
  name?: string;
  shape: Shape;
  size: Vec3;                 // the bounding box before turning, mm (a group: worked out from its parts)
  at: Vec3;                   // the centre, mm
  rotate: Vec3;               // degrees: X, then Y, then Z
  mirror?: boolean;           // mirrored in its own X before it is turned
  hole?: boolean;             // cut out of the solids beside it
  hidden?: boolean;           // left out of everything
  color?: string;             // "#rrggbb", for the eye only
  sides?: number;             // cylinder, cone, sphere: segments around (and box corners when rounded)
  radius?: number;            // box: rounded edges, mm
  top?: number;               // cone: the top's diameter as a share of the bottom's (0: a point)
  text?: string;              // text
  points?: Point2[];          // outline: its shape, mm, around its own 0,0 (which `at` places)
  holes?: Point2[][];         // outline: shapes cut out of it
  file?: string;              // mesh: an STL, 3MF or OBJ in the working folder
  repeat?: Repeat;            // copies of it
  parts?: Part[];             // group
}

export interface Design {
  format: typeof FORMAT;
  version: number;
  printer?: string;           // the printer it is laid out for (printers.ts), or "custom"
  bed?: Vec3;                 // that printer's build volume, mm
  parts: Part[];
}

export const COLORS = ["#d9822b", "#2b7bd9", "#3aa55d", "#c94a8a", "#8a63d2", "#c9a227", "#2bb3b3", "#d94c3d"];

// ---------------------------------------------------------------- reading and writing the file

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function vec(v: unknown, fallback: Vec3): Vec3 {
  if (!Array.isArray(v) || v.length !== 3 || !v.every(num)) return [...fallback] as Vec3;
  return [v[0], v[1], v[2]];
}

/** A design from its file's text, every part complete: missing or wrong values get defaults, said in `problems`. */
export function readDesign(text: string): { design: Design; problems: string[] } {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (e) {
    throw new Error(`The design is not JSON: ${(e as Error).message}`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("The design is not a JSON object.");
  const d = data as Record<string, unknown>;
  const problems: string[] = [];
  const seen = new Set<string>();
  const parts = Array.isArray(d.parts) ? d.parts : [];
  if (!Array.isArray(d.parts)) problems.push('It has no "parts" list.');
  const odd: Odd = { parts: 0, keys: new Set<string>(), shapes: [] };
  const design: Design = { format: FORMAT, version: VERSION, parts: readParts(parts, seen, problems, odd) };
  // A model that guessed the format: what it did not know first, once - not a line for each of its parts.
  const first: string[] = [];
  if (odd.parts) {
    first.push(`${odd.parts} part${odd.parts > 1 ? "s have" : " has"} keys the format does not use (`
      + `${[...odd.keys].sort().join(", ")}) - run hub3d format: shape, size, at, rotate ...`);
  }
  if (odd.shapes.length > 3) {
    first.push(`${odd.shapes.length} parts have no shape the format knows (${odd.shapes.slice(0, 3).map(([id]) => id)
      .join(", ")} ...) - taken as boxes.`);
  } else odd.shapes.forEach(([id, shape]) => first.push(`${id}: no shape ${JSON.stringify(shape)} - taken as a box.`));
  problems.unshift(...first);
  if (typeof d.printer === "string" && d.printer) design.printer = d.printer;
  if (Array.isArray(d.bed) && d.bed.length === 3 && d.bed.every((x) => num(x) && x > 0)) design.bed = vec(d.bed, [1, 1, 1]);
  return { design, problems };
}

type Odd = { parts: number; keys: Set<string>; shapes: [string, unknown][] };

function readParts(list: unknown[], seen: Set<string>, problems: string[], odd: Odd): Part[] {
  const out: Part[] = [];
  for (const item of list) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      problems.push("A part that is not an object was left out.");
      continue;
    }
    out.push(readPart(item as Record<string, unknown>, seen, problems, out.length, odd));
  }
  return out;
}

/** An outline's points: three or more [x, y]; null otherwise. */
function pointsOf(v: unknown): Point2[] | null {
  if (!Array.isArray(v) || v.length < 3) return null;
  const out: Point2[] = [];
  for (const q of v) {
    if (!Array.isArray(q) || q.length !== 2 || !num(q[0]) || !num(q[1])) return null;
    out.push([q[0], q[1]]);
  }
  return out;
}

function readRepeat(v: unknown, id: string, problems: string[]): Repeat | undefined {
  if (v === undefined) return undefined;
  const r = v as Record<string, unknown>;
  if (!r || typeof r !== "object" || Array.isArray(r) || !num(r.count)) {
    problems.push(`${id}: "repeat" needs a "count" - left out.`);
    return undefined;
  }
  const count = Math.max(1, Math.min(MAX_COPIES, Math.round(r.count)));
  if (count < 2) return undefined;
  const out: Repeat = { count };
  if (Array.isArray(r.step) && r.step.length === 3 && r.step.every(num)) {
    out.step = [r.step[0], r.step[1], r.step[2]];
    return out;
  }
  if (Array.isArray(r.around) && r.around.length === 2 && r.around.every(num)) out.around = [r.around[0], r.around[1]];
  if (num(r.angle) && Math.abs(r.angle) < 360) out.angle = r.angle;         // all round is the default
  return out;
}

function readPart(p: Record<string, unknown>, seen: Set<string>, problems: string[], index: number, odd: Odd): Part {
  let id = typeof p.id === "string" && p.id.trim() ? p.id.trim() : "";
  if (!id || seen.has(id)) id = freshId(seen);
  seen.add(id);
  let shape = p.shape as Shape;
  if (!SHAPES.includes(shape)) {
    odd.shapes.push([id, p.shape]);
    shape = "box";
  }
  const unknown = Object.keys(p).filter((k) => !PART_KEYS.includes(k));
  if (unknown.length) {
    odd.parts += 1;
    unknown.forEach((k) => odd.keys.add(k));
  }
  const part: Part = { id, shape, size: [20, 20, 20], at: [0, 0, 10], rotate: [0, 0, 0] };
  if (typeof p.name === "string" && p.name) part.name = p.name;
  if (shape === "group") {
    part.parts = readParts(Array.isArray(p.parts) ? p.parts : [], seen, problems, odd);
  } else {
    // 0 keeps a text's width as its letters make it, and an outline's width and depth as its points draw them
    const auto = (i: number, v: number) => v <= 0 && ((shape === "text" && i === 0) || (shape === "outline" && i < 2));
    const size = vec(p.size, shape === "outline" ? [0, 0, 2] : [20, 20, shape === "text" ? 2 : 20]);
    part.size = size.map((v, i) => (auto(i, v) ? 0 : Math.max(MIN_SIZE, Math.abs(v)))) as Vec3;
    part.at = vec(p.at, [0, 0, part.size[2] / 2]);
  }
  if (shape === "group") part.size = [0, 0, 0];     // a group is where its parts are: nothing of its own
  else part.rotate = vec(p.rotate, [0, 0, 0]);
  if (shape === "group") part.at = [0, 0, 0];
  if (p.mirror === true) part.mirror = true;
  if (p.hole === true) part.hole = true;
  if (p.hidden === true) part.hidden = true;
  if (shape !== "group") {
    part.color = typeof p.color === "string" && /^#[0-9a-f]{6}$/i.test(p.color) ? p.color.toLowerCase()
      : COLORS[index % COLORS.length];
  }
  if (ROUND.includes(shape) || (shape === "box" && num(p.radius) && p.radius > 0)) {
    part.sides = num(p.sides) ? Math.max(3, Math.min(256, Math.round(p.sides))) : DEFAULT_SIDES;
  }
  if (shape === "box" && num(p.radius) && p.radius > 0) part.radius = p.radius;
  if (shape === "cone") part.top = num(p.top) ? Math.max(0, Math.min(1, p.top)) : 0;
  if (shape === "text") part.text = typeof p.text === "string" ? p.text : "Text";
  if (shape === "outline") {
    const pts = pointsOf(p.points);
    if (pts) part.points = pts;
    else problems.push(`${id}: an outline needs "points", three or more [x, y] - it makes nothing.`);
    if (Array.isArray(p.holes)) {
      const holes = p.holes.map(pointsOf);
      if (holes.some((h) => !h)) problems.push(`${id}: a hole that is not three or more [x, y] was left out.`);
      const kept = holes.filter((h): h is Point2[] => !!h);
      if (kept.length) part.holes = kept;
    }
  }
  if (shape === "mesh") {
    if (typeof p.file === "string" && p.file) part.file = p.file;
    else problems.push(`${id}: a mesh without a "file" makes nothing.`);
  }
  const repeat = readRepeat(p.repeat, id, problems);
  if (repeat) part.repeat = repeat;
  return part;
}

export function freshId(seen: Set<string>): string {
  let n = seen.size + 1;
  while (seen.has(`p${n}`)) n++;
  return `p${n}`;
}

/** Every id in the design (groups' parts too). */
export function allIds(parts: Part[], into = new Set<string>()): Set<string> {
  for (const p of parts) {
    into.add(p.id);
    if (p.parts) allIds(p.parts, into);
  }
  return into;
}

const r3 = (v: number) => Math.round(v * 1000) / 1000 + 0;     // no -0, no 0.30000000000000004

function cleanPart(p: Part): Record<string, unknown> {
  const o: Record<string, unknown> = { id: p.id };
  if (p.name) o.name = p.name;
  o.shape = p.shape;
  if (p.shape !== "group") {
    o.size = p.size.map(r3);
    o.at = p.at.map(r3);
    if (p.rotate.some((v) => v !== 0)) o.rotate = p.rotate.map(r3);
  }
  if (p.mirror) o.mirror = true;
  if (p.hole) o.hole = true;
  if (p.hidden) o.hidden = true;
  if (p.color && p.shape !== "group") o.color = p.color;
  if (p.sides !== undefined && (ROUND.includes(p.shape) || p.radius)) o.sides = p.sides;
  if (p.radius) o.radius = r3(p.radius);
  if (p.shape === "cone") o.top = r3(p.top ?? 0);
  if (p.shape === "text") o.text = p.text ?? "";
  if (p.shape === "outline" && p.points) o.points = p.points.map((q) => q.map(r3));
  if (p.shape === "outline" && p.holes?.length) o.holes = p.holes.map((h) => h.map((q) => q.map(r3)));
  if (p.shape === "mesh" && p.file) o.file = p.file;
  if (p.repeat) {
    const r: Record<string, unknown> = { count: p.repeat.count };
    if (p.repeat.step) r.step = p.repeat.step.map(r3);
    else {
      if (p.repeat.around) r.around = p.repeat.around.map(r3);
      if (p.repeat.angle !== undefined && Math.abs(p.repeat.angle) < 360) r.angle = r3(p.repeat.angle);
    }
    o.repeat = r;
  }
  if (p.shape === "group") o.parts = (p.parts ?? []).map(cleanPart);
  return o;
}

/** The file's text: a fixed key order, numbers to a thousandth of a millimetre, one part a few lines. */
export function writeDesign(d: Design): string {
  const out: Record<string, unknown> = { format: FORMAT, version: VERSION };
  if (d.printer) out.printer = d.printer;
  if (d.bed) out.bed = d.bed.map(r3);
  out.parts = d.parts.map(cleanPart);
  const n = "-?[\\d.e+-]+";
  return JSON.stringify(out, null, 2)
    .replace(new RegExp(`\\[\\s+(${n}),\\s+(${n}),\\s+(${n})\\s+\\]`, "g"), "[$1, $2, $3]")
    .replace(new RegExp(`\\[\\s+(${n}),\\s+(${n})\\s+\\]`, "g"), "[$1, $2]")
    // an outline's points on one line
    .replace(new RegExp(`\\[\\s+(\\[${n}, ${n}\\](?:,\\s+\\[${n}, ${n}\\])*)\\s+\\]`, "g"),
      (_m, inner: string) => `[${inner.replace(/,\s+/g, ", ")}]`) + "\n";
}

export function newDesign(printer?: string, bed?: Vec3): Design {
  return { format: FORMAT, version: VERSION, printer, bed, parts: [] };
}

// ---------------------------------------------------------------- turning and moving, without three.js

export type Mat3 = [number, number, number, number, number, number, number, number, number];   // row-major

export function rotation(r: Vec3): Mat3 {
  const [x, y, z] = r.map((d) => (d * Math.PI) / 180);
  const cx = Math.cos(x), sx = Math.sin(x), cy = Math.cos(y), sy = Math.sin(y), cz = Math.cos(z), sz = Math.sin(z);
  // Rz * Ry * Rx: about X first, then Y, then Z
  return [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx,
    sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx,
    -sy, cy * sx, cy * cx];
}

export function mul(a: Mat3, b: Mat3): Mat3 {
  const o = new Array(9).fill(0) as Mat3;
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) for (let k = 0; k < 3; k++) o[i * 3 + j] += a[i * 3 + k] * b[k * 3 + j];
  return o;
}

export function apply(m: Mat3, v: Vec3): Vec3 {
  return [m[0] * v[0] + m[1] * v[1] + m[2] * v[2], m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
    m[6] * v[0] + m[7] * v[1] + m[8] * v[2]];
}

/** The angles (degrees, X then Y then Z) of a rotation matrix - rotation()'s inverse. */
export function angles(m: Mat3): Vec3 {
  const sy = Math.max(-1, Math.min(1, -m[6]));
  const y = Math.asin(sy);
  let x: number, z: number;
  if (Math.abs(sy) < 0.9999999) {
    x = Math.atan2(m[7], m[8]);
    z = Math.atan2(m[3], m[0]);
  } else {                                          // straight up or down: put it all in X
    x = Math.atan2(-m[5], m[4]);
    z = 0;
  }
  const deg = (v: number) => {
    const d = (v * 180) / Math.PI;
    const r = Math.round(d * 1e6) / 1e6;
    return Math.abs(r) < 1e-9 ? 0 : r;
  };
  return [deg(x), deg(y), deg(z)];
}

/** An outline's points scaled to its size (0: as drawn), its holes after them; null without points. */
export function outlinePolygons(p: Part): number[][][] | null {
  if (!p.points || p.points.length < 3) return null;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of p.points) {
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  const kx = p.size[0] > 0 && x1 - x0 > 1e-9 ? p.size[0] / (x1 - x0) : 1;
  const ky = p.size[1] > 0 && y1 - y0 > 1e-9 ? p.size[1] / (y1 - y0) : 1;
  return [p.points, ...(p.holes ?? [])].map((poly) => poly.map(([x, y]) => [x * kx, y * ky]));
}

/** A part's own box, before it is mirrored, turned and moved: centred on its 0,0,0 - an outline's where its points
 *  are drawn. */
export function ownBox(p: Part): Box3 {
  const [sx, sy, sz] = p.size;
  const polys = p.shape === "outline" ? outlinePolygons(p) : null;
  if (polys) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [x, y] of polys[0]) {
      x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
    return [[x0, y0, -sz / 2], [x1, y1, sz / 2]];
  }
  return [[-sx / 2, -sy / 2, -sz / 2], [sx / 2, sy / 2, sz / 2]];
}

/** A part's size with the 0s worked out: a text's width from its letters (given the font), an outline's from its
 *  points. */
export function partSize(p: Part, font?: FontData): Vec3 {
  if (p.shape === "text" && p.size[0] <= 0 && font) return [textWidth(p.text ?? "", p.size[1], font), p.size[1], p.size[2]];
  if (p.shape === "outline" && (p.size[0] <= 0 || p.size[1] <= 0)) {
    const [lo, hi] = ownBox(p);
    return [hi[0] - lo[0], hi[1] - lo[1], p.size[2]];
  }
  return p.size;
}

/** The corners of a part's box, turned and moved: where it is in the design. */
export function corners(p: Part): Vec3[] {
  const m = rotation(p.rotate);
  const [lo, hi] = ownBox(p);
  const out: Vec3[] = [];
  for (const x of [lo[0], hi[0]]) for (const y of [lo[1], hi[1]]) for (const z of [lo[2], hi[2]]) {
    const v = apply(m, [p.mirror ? -x : x, y, z]);
    out.push([v[0] + p.at[0], v[1] + p.at[1], v[2] + p.at[2]]);
  }
  return out;
}

export type Box3 = [Vec3, Vec3];

/** Where a repeat turns its copies about: `around`, or the part's own at (a group's: the plate's middle). */
export function repeatAround(p: Part): Point2 {
  return p.repeat?.around ?? (p.shape === "group" ? [0, 0] : [p.at[0], p.at[1]]);
}

/** The turn from one copy to the next, degrees: all round in even steps, or `angle` from the first to the last. */
export function repeatStep(r: Repeat): number {
  const a = r.angle ?? 360;
  return Math.abs(a) >= 360 - 1e-9 ? a / r.count : r.count > 1 ? a / (r.count - 1) : 0;
}

/** Where copy `i` (1 .. count - 1) of a repeated part puts the point `v` of the part. */
export function copyOf(p: Part, i: number, v: Vec3): Vec3 {
  const r = p.repeat!;
  if (r.step) return [v[0] + r.step[0] * i, v[1] + r.step[1] * i, v[2] + r.step[2] * i];
  const [ax, ay] = repeatAround(p);
  const t = (repeatStep(r) * i * Math.PI) / 180, c = Math.cos(t), s = Math.sin(t);
  const x = v[0] - ax, y = v[1] - ay;
  return [ax + c * x - s * y, ay + s * x + c * y, v[2]];
}

function boxOfPoints(points: Vec3[]): Box3 | null {
  if (!points.length) return null;
  const lo: Vec3 = [Infinity, Infinity, Infinity], hi: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (const c of points) for (let i = 0; i < 3; i++) {
    lo[i] = Math.min(lo[i], c[i]);
    hi[i] = Math.max(hi[i], c[i]);
  }
  return [lo, hi];
}

/** Where a part (a group: all its parts; and their copies) reaches in the design, from its turned box. */
export function extent(p: Part): Box3 | null {
  const box = p.shape === "group" ? extentOf(p.parts ?? []) : boxOfPoints(corners(p));
  if (!box || !p.repeat || p.repeat.count < 2) return box;
  const cs: Vec3[] = [];
  for (const x of [box[0][0], box[1][0]]) for (const y of [box[0][1], box[1][1]]) for (const z of [box[0][2], box[1][2]]) {
    cs.push([x, y, z]);
  }
  const all = [...cs];
  for (let i = 1; i < p.repeat.count; i++) for (const c of cs) all.push(copyOf(p, i, c));
  return boxOfPoints(all);
}

export function extentOf(parts: Part[]): Box3 | null {
  let box: Box3 | null = null;
  for (const p of parts) {
    if (p.hidden) continue;
    const e = extent(p);
    if (!e) continue;
    box = box ? [[0, 1, 2].map((i) => Math.min(box![0][i], e[0][i])) as Vec3, [0, 1, 2].map((i) => Math.max(box![1][i], e[1][i])) as Vec3] : e;
  }
  return box;
}

/** A part, or a group's parts, turned by `turn` (a rotation matrix) and scaled by `k` about `centre`, then moved by
 *  `move` - kept in the file's terms: each part's centre, size and angles, and where its copies go. */
export function transformPart(p: Part, centre: Vec3, move: Vec3, turn: Mat3 | null, k = 1): Part {
  let out: Part;
  if (p.shape === "group") out = { ...p, parts: (p.parts ?? []).map((c) => transformPart(c, centre, move, turn, k)) };
  else {
    const rel: Vec3 = [p.at[0] - centre[0], p.at[1] - centre[1], p.at[2] - centre[2]];
    const r = turn ? apply(turn, rel) : rel;
    const at: Vec3 = [centre[0] + r[0] * k + move[0], centre[1] + r[1] * k + move[1], centre[2] + r[2] * k + move[2]];
    out = { ...p, at };
    if (turn) out.rotate = angles(mul(turn, rotation(p.rotate)));
    if (k !== 1) {                         // a text's width 0 (it follows its letters) stays 0, an outline's 0s too
      const auto = (i: number, v: number) => v <= 0 && ((p.shape === "text" && i === 0) || (p.shape === "outline" && i < 2));
      out.size = p.size.map((v, i) => (auto(i, v) ? 0 : Math.max(MIN_SIZE, v * k))) as Vec3;
      if (p.shape === "outline" && (p.size[0] <= 0 || p.size[1] <= 0)) {
        const drawn = partSize(p);                    // scaled: its 0s become the drawn size times k
        out.size = [drawn[0] * k, drawn[1] * k, Math.max(MIN_SIZE, p.size[2] * k)];
      }
      if (p.radius) out.radius = p.radius * k;
    }
  }
  if (p.repeat) {
    const r: Repeat = { ...p.repeat };
    if (r.step) {
      const s = turn ? apply(turn, r.step) : r.step;
      r.step = [s[0] * k, s[1] * k, s[2] * k];
    } else if (r.around || p.shape === "group") {   // a part's own at is its centre of turning: that moves already
      const [ax, ay] = repeatAround(p);
      const rel: Vec3 = [ax - centre[0], ay - centre[1], 0];
      const v = turn ? apply(turn, rel) : rel;
      r.around = [centre[0] + v[0] * k + move[0], centre[1] + v[1] * k + move[1]];
    }
    out.repeat = r;
  }
  return out;
}

/** A part mirrored in the design's axis `axis` (0, 1, 2) through `centre`: its own X mirrored, its angles worked out
 *  (and its copies mirrored with it). */
export function mirrorPart(p: Part, axis: number, centre: Vec3): Part {
  const flip: Mat3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  flip[axis * 4] = -1;
  let out: Part;
  if (p.shape === "group") out = { ...p, parts: (p.parts ?? []).map((c) => mirrorPart(c, axis, centre)) };
  else {
    const at = [...p.at] as Vec3;
    at[axis] = 2 * centre[axis] - at[axis];
    // M * R = (M * R * Lx) * Lx, and M * R * Lx is a rotation: the part's own X flips, its angles become M R Lx's.
    const lx: Mat3 = [-1, 0, 0, 0, 1, 0, 0, 0, 1];
    out = { ...p, at, rotate: angles(mul(mul(flip, rotation(p.rotate)), lx)), mirror: !p.mirror || undefined };
  }
  if (p.repeat) {
    const r: Repeat = { ...p.repeat };
    if (r.step) {
      const s = [...r.step] as Vec3;
      s[axis] = -s[axis];
      r.step = s;
    } else if (axis < 2) {
      if (r.around || p.shape === "group") {
        const a = [...repeatAround(p)] as Point2;
        a[axis] = 2 * centre[axis] - a[axis];
        r.around = a;
      }
      if (r.angle !== undefined && Math.abs(r.angle) < 360) r.angle = -r.angle;   // all round: the same copies
    }
    out.repeat = r;
  }
  return out;
}

// ---------------------------------------------------------------- text

export interface FontData {
  unitsPerEm: number;
  ascender: number;
  descender: number;
  glyphs: Record<string, { a: number; c: number[][] }>;
}

/** The outlines of `text` in font units: one line after another, each glyph moved along by its advance. */
export function textContours(text: string, font: FontData): number[][][] {
  const out: number[][][] = [];
  const lines = text.split("\n");
  const lineHeight = 1.2 * font.unitsPerEm;
  lines.forEach((line, row) => {
    let x = 0;
    const y = -row * lineHeight;
    for (const ch of line) {
      const g = font.glyphs[ch] ?? font.glyphs["?"];
      if (!g) continue;
      for (const c of g.c) {
        const poly: number[][] = [];
        for (let i = 0; i < c.length; i += 2) poly.push([c[i] + x, c[i + 1] + y]);
        out.push(poly);
      }
      x += g.a;
    }
  });
  return out;
}

/** The outlines scaled into a part's size (width and height in mm, the box centred; width 0: as wide as the height
 *  makes it). null when the text draws nothing. */
export function textPolygons(text: string, size: Vec3, font: FontData): number[][][] | null {
  const contours = textContours(text, font);
  if (!contours.length) return null;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const c of contours) for (const [x, y] of c) {
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  const w = x1 - x0, h = y1 - y0;
  if (w <= 0 || h <= 0) return null;
  const ky = size[1] / h;
  const kx = size[0] > 0 ? size[0] / w : ky;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  return contours.map((c) => c.map(([x, y]) => [(x - cx) * kx, (y - cy) * ky]));
}

/** How wide text of a given height is, in mm (for a text part whose width follows its letters). */
export function textWidth(text: string, height: number, font: FontData): number {
  const contours = textContours(text, font);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const c of contours) for (const [x, y] of c) {
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  return y1 > y0 ? ((x1 - x0) * height) / (y1 - y0) : 0;
}

// ---------------------------------------------------------------- building the solid with manifold-3d

/** manifold-3d's module and its objects, typed loosely: the browser and node hand in the real one. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Wasm = any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Obj = any;

export interface MeshData { positions: Float32Array; triangles: Uint32Array }

export interface Built {
  /** The solid's triangles, and for each triangle the part it came from (a hole's id where it cut). */
  positions: Float32Array;
  triangles: Uint32Array;
  triPart: string[];
  /** Meshes that are not closed solids: shown and exported as they are, neither cut nor joined. */
  loose: { part: string; mesh: MeshData }[];
  problems: string[];
  volume: number;
  box: Box3 | null;
  /** What the solid stands on (0.88.0): the area of its slice 0.1 mm above its lowest point, and of its shadow from
   *  above, mm2 - round or pointed underneath when the first is a small share of the second. */
  rests: { contact: number; footprint: number } | null;
}

/** Same triangles with each place in space once: STL repeats a corner for every triangle that has it. */
export function weld(m: MeshData): MeshData {
  const index = new Map<string, number>();
  const pos: number[] = [];
  const remap = new Uint32Array(m.positions.length / 3);
  for (let i = 0; i < remap.length; i++) {
    const key = `${m.positions[i * 3]},${m.positions[i * 3 + 1]},${m.positions[i * 3 + 2]}`;
    let j = index.get(key);
    if (j === undefined) {
      j = pos.length / 3;
      index.set(key, j);
      pos.push(m.positions[i * 3], m.positions[i * 3 + 1], m.positions[i * 3 + 2]);
    }
    remap[i] = j;
  }
  const tri: number[] = [];
  for (let t = 0; t < m.triangles.length; t += 3) {
    const a = remap[m.triangles[t]], b = remap[m.triangles[t + 1]], c = remap[m.triangles[t + 2]];
    if (a !== b && b !== c && a !== c) tri.push(a, b, c);
  }
  return { positions: new Float32Array(pos), triangles: new Uint32Array(tri) };
}

function meshBox(m: MeshData): Box3 {
  const lo: Vec3 = [Infinity, Infinity, Infinity], hi: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < m.positions.length; i += 3) for (let k = 0; k < 3; k++) {
    lo[k] = Math.min(lo[k], m.positions[i + k]);
    hi[k] = Math.max(hi[k], m.positions[i + k]);
  }
  return [lo, hi];
}

/** A mesh as a part places it: centred, scaled to its size, mirrored, turned and moved (for loose meshes). */
function placeMesh(m: MeshData, p: Part): MeshData {
  const [lo, hi] = meshBox(m);
  const c = [0, 1, 2].map((i) => (lo[i] + hi[i]) / 2);
  const k = [0, 1, 2].map((i) => (hi[i] - lo[i] > 1e-9 ? p.size[i] / (hi[i] - lo[i]) : 1));
  const rot = rotation(p.rotate);
  const out = new Float32Array(m.positions.length);
  for (let i = 0; i < m.positions.length; i += 3) {
    let v: Vec3 = [(m.positions[i] - c[0]) * k[0], (m.positions[i + 1] - c[1]) * k[1], (m.positions[i + 2] - c[2]) * k[2]];
    if (p.mirror) v[0] = -v[0];
    v = apply(rot, v);
    out[i] = v[0] + p.at[0]; out[i + 1] = v[1] + p.at[1]; out[i + 2] = v[2] + p.at[2];
  }
  let tri = m.triangles;
  if (p.mirror) {                                   // a mirror turns triangles inside out: turn them back
    tri = new Uint32Array(m.triangles);
    for (let t = 0; t < tri.length; t += 3) [tri[t + 1], tri[t + 2]] = [tri[t + 2], tri[t + 1]];
  }
  return { positions: out, triangles: tri };
}

export interface Kit {
  wasm: Wasm;
  font: FontData;
  meshes: Map<string, MeshData | string>;      // file -> its triangles, or why it could not be read
}

/** The design's solid: holes cut out of the solids beside them, groups first. Everything manifold-3d makes here is
 *  freed before it returns. */
export function build(design: Design, kit: Kit): Built {
  const { Manifold, CrossSection, Mesh } = kit.wasm;
  const made: Obj[] = [];
  const keep = (x: Obj): Obj => {
    made.push(x);
    return x;
  };
  const owner = new Map<number, string>();
  const problems: string[] = [];
  const loose: { part: string; mesh: MeshData }[] = [];

  const shape = (p: Part): Obj | null => {
    const [sx, sy, sz] = p.size;
    const sides = p.sides ?? DEFAULT_SIDES;
    switch (p.shape) {
      case "box": {
        const r = Math.min(p.radius ?? 0, (Math.min(sx, sy, sz) / 2) * 0.999);
        if (r <= 0) return keep(Manifold.cube([sx, sy, sz], true));
        const balls: Obj[] = [];
        for (const a of [-1, 1]) for (const b of [-1, 1]) for (const c of [-1, 1]) {
          balls.push(keep(keep(Manifold.sphere(r, sides)).translate([a * (sx / 2 - r), b * (sy / 2 - r), c * (sz / 2 - r)])));
        }
        return keep(Manifold.hull(balls));
      }
      case "cylinder":
      case "cone": {
        const top = p.shape === "cone" ? 0.5 * (p.top ?? 0) : 0.5;
        return keep(keep(Manifold.cylinder(1, 0.5, top, sides, true)).scale([sx, sy, sz]));
      }
      case "sphere":
        return keep(keep(Manifold.sphere(0.5, sides)).scale([sx, sy, sz]));
      case "wedge": {
        const x = sx / 2, y = sy / 2, z = sz / 2;
        return keep(Manifold.hull([[-x, -y, -z], [x, -y, -z], [-x, y, -z], [x, y, -z], [-x, -y, z], [-x, y, z]]));
      }
      case "text": {
        const polys = textPolygons(p.text ?? "", p.size, kit.font);
        if (!polys) return null;
        return keep(keep(keep(new CrossSection(polys, "NonZero")).extrude(sz)).translate([0, 0, -sz / 2]));
      }
      case "outline": {                    // EvenOdd: a hole is a hole whichever way round its points run
        const polys = outlinePolygons(p);
        if (!polys) return null;
        const cs = keep(new CrossSection(polys, "EvenOdd"));
        if (cs.isEmpty()) return null;
        return keep(keep(cs.extrude(sz)).translate([0, 0, -sz / 2]));
      }
      case "mesh": {
        const got = p.file ? kit.meshes.get(p.file) : undefined;
        if (got === undefined) {
          problems.push(`${p.id}: ${p.file ? `${p.file} is not loaded` : "no file"}.`);
          return null;
        }
        if (typeof got === "string") {
          problems.push(`${p.id}: ${got}`);
          return null;
        }
        const w = weld(got);
        let m: Obj = null, status = "NoError";
        try {          // manifold-3d throws on triangles that do not close (the Python one says it in status())
          m = keep(new Manifold(new Mesh({ numProp: 3, vertProperties: w.positions, triVerts: w.triangles })));
          status = m.status();
        } catch (e) {
          status = (e as { code?: string }).code ?? "NotManifold";
        }
        if (!m || status !== "NoError" || m.isEmpty()) {
          problems.push(`${p.id}: ${p.file} is not a closed solid (${status}) - shown and exported as it is; holes`
            + " do not cut it.");
          loose.push({ part: p.id, mesh: placeMesh(w, p) });
          return null;
        }
        const [lo, hi] = meshBox(w);
        const c: Vec3 = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
        const k = [0, 1, 2].map((i) => (hi[i] - lo[i] > 1e-9 ? p.size[i] / (hi[i] - lo[i]) : 1));
        return keep(keep(m.translate([-c[0], -c[1], -c[2]])).scale(k));
      }
      default:
        return null;
    }
  };

  const place = (p: Part, m: Obj): Obj => {
    let x = m;
    if (p.mirror) x = keep(x.mirror([1, 0, 0]));
    if (p.rotate.some((v) => v !== 0)) x = keep(x.rotate(p.rotate));
    return keep(keep(x.translate(p.at)).asOriginal());
  };

  // Every join and cut is worked out where it is made (0.88.0): manifold-3d otherwise merges a join into the one
  // that takes it in, when nothing else holds it - which depends on when objects are freed, so the browser and
  // hub3d (Python frees them at once) would build the same solid out of different triangles.
  const done = (m: Obj): Obj => {
    m.status();
    return m;
  };

  // A repeated part's copies keep its original id: each of their triangles is the part's.
  const repeated = (p: Part, m: Obj): Obj => {
    const r = p.repeat!;
    const all: Obj[] = [m];
    for (let i = 1; i < r.count; i++) {
      if (r.step) all.push(keep(m.translate([r.step[0] * i, r.step[1] * i, r.step[2] * i])));
      else {
        const [ax, ay] = repeatAround(p);
        all.push(keep(keep(keep(m.translate([-ax, -ay, 0])).rotate([0, 0, repeatStep(r) * i])).translate([ax, ay, 0])));
      }
    }
    return done(keep(Manifold.union(all)));
  };

  const level = (parts: Part[]): Obj | null => {
    const solids: Obj[] = [], holes: Obj[] = [];
    for (const p of parts) {
      if (p.hidden) continue;
      let m: Obj | null = null;
      if (p.shape === "group") {
        m = level(p.parts ?? []);
      } else {
        const s = shape(p);
        if (s) {
          m = place(p, s);
          owner.set(m.originalID(), p.id);
        }
      }
      if (m && p.repeat && p.repeat.count > 1) m = repeated(p, m);
      if (m) (p.hole ? holes : solids).push(m);
    }
    if (!solids.length) return null;
    let out = solids.length === 1 ? solids[0] : done(keep(Manifold.union(solids)));
    if (holes.length) out = done(keep(out.subtract(holes.length === 1 ? holes[0] : done(keep(Manifold.union(holes))))));
    return out;
  };

  try {
    const result = level(design.parts);
    let positions = new Float32Array(0), triangles = new Uint32Array(0), triPart: string[] = [], volume = 0;
    if (result) {
      if (result.status() !== "NoError") problems.push(`manifold-3d: ${result.status()}`);
      const mesh = result.getMesh();
      const n = mesh.vertProperties.length / mesh.numProp;
      positions = new Float32Array(n * 3);
      for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) positions[i * 3 + k] = mesh.vertProperties[i * mesh.numProp + k];
      triangles = new Uint32Array(mesh.triVerts);
      triPart = new Array(triangles.length / 3);
      for (let r = 0; r < mesh.runOriginalID.length; r++) {
        const id = owner.get(mesh.runOriginalID[r]) ?? "";
        for (let t = mesh.runIndex[r] / 3; t < mesh.runIndex[r + 1] / 3; t++) triPart[t] = id;
      }
      volume = result.volume();
    }
    let box: Box3 | null = triangles.length ? meshBox({ positions, triangles }) : null;
    let rests: Built["rests"] = null;
    if (result && box) {
      rests = { contact: keep(result.slice(box[0][2] + 0.1)).area(), footprint: keep(result.project()).area() };
    }
    for (const l of loose) {
      const b = meshBox(l.mesh);
      box = box ? [[0, 1, 2].map((i) => Math.min(box![0][i], b[0][i])) as Vec3,
        [0, 1, 2].map((i) => Math.max(box![1][i], b[1][i])) as Vec3] : b;
    }
    return { positions, triangles, triPart, loose, problems, volume, box, rests };
  } finally {
    for (const m of made) {
      try {
        m.delete();
      } catch {
        /* freed already */
      }
    }
  }
}

/** One part alone, placed (for showing a part while it is dragged, and holes as ghosts). */
export function buildPart(p: Part, kit: Kit): Built {
  return build({ format: FORMAT, version: VERSION, parts: [{ ...p, hole: false, hidden: false }] }, kit);
}

// ---------------------------------------------------------------- files for the slicer

function normal(p: Float32Array, a: number, b: number, c: number): Vec3 {
  const ux = p[b * 3] - p[a * 3], uy = p[b * 3 + 1] - p[a * 3 + 1], uz = p[b * 3 + 2] - p[a * 3 + 2];
  const vx = p[c * 3] - p[a * 3], vy = p[c * 3 + 1] - p[a * 3 + 1], vz = p[c * 3 + 2] - p[a * 3 + 2];
  const n: Vec3 = [uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx];
  const l = Math.hypot(...n) || 1;
  return [n[0] / l, n[1] / l, n[2] / l];
}

/** All of a build's triangles - the solid and the loose meshes - as one mesh. */
export function joined(b: Built): MeshData {
  const meshes = [{ positions: b.positions, triangles: b.triangles }, ...b.loose.map((l) => l.mesh)];
  const np = meshes.reduce((s, m) => s + m.positions.length, 0), nt = meshes.reduce((s, m) => s + m.triangles.length, 0);
  const positions = new Float32Array(np), triangles = new Uint32Array(nt);
  let po = 0, to = 0;
  for (const m of meshes) {
    positions.set(m.positions, po);
    for (let i = 0; i < m.triangles.length; i++) triangles[to + i] = m.triangles[i] + po / 3;
    po += m.positions.length;
    to += m.triangles.length;
  }
  return { positions, triangles };
}

/** A binary STL in millimetres. */
export function stl(m: MeshData, title = "SLQ Hub 3D editor"): Uint8Array {
  const n = m.triangles.length / 3;
  const buf = new ArrayBuffer(84 + n * 50);
  const bytes = new Uint8Array(buf);
  const head = new TextEncoder().encode(title.slice(0, 79));
  bytes.set(head, 0);
  const dv = new DataView(buf);
  dv.setUint32(80, n, true);
  let o = 84;
  for (let t = 0; t < n; t++) {
    const a = m.triangles[t * 3], b = m.triangles[t * 3 + 1], c = m.triangles[t * 3 + 2];
    const nn = normal(m.positions, a, b, c);
    for (const v of [...nn, ...[a, b, c].flatMap((i) => [m.positions[i * 3], m.positions[i * 3 + 1], m.positions[i * 3 + 2]])]) {
      dv.setFloat32(o, v, true);
      o += 4;
    }
    dv.setUint16(o, 0, true);
    o += 2;
  }
  return bytes;
}

/** An STL file's triangles (binary or ASCII). */
export function readStl(data: Uint8Array): MeshData {
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const n = data.byteLength >= 84 ? dv.getUint32(80, true) : -1;
  const pos: number[] = [];
  if (n >= 0 && data.byteLength === 84 + n * 50) {
    for (let t = 0; t < n; t++) for (let k = 0; k < 9; k++) pos.push(dv.getFloat32(84 + t * 50 + 12 + k * 4, true));
  } else {
    const text = new TextDecoder().decode(data);
    const re = /vertex\s+([-+\d.eE]+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) pos.push(+m[1], +m[2], +m[3]);
    if (!pos.length) throw new Error("not an STL file: no triangles in it");
  }
  const triangles = new Uint32Array(pos.length / 3);
  for (let i = 0; i < triangles.length; i++) triangles[i] = i;
  return { positions: new Float32Array(pos), triangles };
}

let crcTable: Uint32Array | null = null;
function crc32(data: Uint8Array): number {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
      let c = i;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[i] = c >>> 0;
    }
  }
  let c = 0xffffffff;
  for (let i = 0; i < data.length; i++) c = crcTable[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** A ZIP of `files`, stored (not packed): what a 3MF is. */
export function zip(files: [string, Uint8Array][]): Uint8Array {
  const enc = new TextEncoder();
  const chunks: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const [name, data] of files) {
    const nb = enc.encode(name);
    const crc = crc32(data);
    const local = new Uint8Array(30 + nb.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true); lv.setUint16(4, 20, true); lv.setUint16(8, 0, true);
    lv.setUint32(14, crc, true); lv.setUint32(18, data.length, true); lv.setUint32(22, data.length, true);
    lv.setUint16(26, nb.length, true);
    local.set(nb, 30);
    const cen = new Uint8Array(46 + nb.length);
    const cv = new DataView(cen.buffer);
    cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true); cv.setUint16(6, 20, true);
    cv.setUint32(16, crc, true); cv.setUint32(20, data.length, true); cv.setUint32(24, data.length, true);
    cv.setUint16(28, nb.length, true); cv.setUint32(42, offset, true);
    cen.set(nb, 46);
    chunks.push(local, data);
    central.push(cen);
    offset += local.length + data.length;
  }
  const size = central.reduce((s, c) => s + c.length, 0);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, files.length, true); ev.setUint16(10, files.length, true);
  ev.setUint32(12, size, true); ev.setUint32(16, offset, true);
  const all = [...chunks, ...central, end];
  const out = new Uint8Array(all.reduce((s, c) => s + c.length, 0));
  let o = 0;
  for (const c of all) {
    out.set(c, o);
    o += c.length;
  }
  return out;
}

const xmlEscape = (s: string) => s.replace(/[<>&"']/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" })[c]!);

/** A 3MF (core specification, millimetres): one object, the way Bambu Studio and other slicers read them. */
export function threeMf(m: MeshData, name = "design"): Uint8Array {
  const enc = new TextEncoder();
  const verts: string[] = [], tris: string[] = [];
  for (let i = 0; i < m.positions.length; i += 3) {
    verts.push(`<vertex x="${+m.positions[i].toFixed(5)}" y="${+m.positions[i + 1].toFixed(5)}" z="${+m.positions[i + 2].toFixed(5)}"/>`);
  }
  for (let t = 0; t < m.triangles.length; t += 3) tris.push(`<triangle v1="${m.triangles[t]}" v2="${m.triangles[t + 1]}" v3="${m.triangles[t + 2]}"/>`);
  const model = `<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" `
    + `xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n<metadata name="Application">SLQ Hub</metadata>\n`
    + `<resources><object id="1" name="${xmlEscape(name)}" type="model"><mesh><vertices>${verts.join("")}</vertices>`
    + `<triangles>${tris.join("")}</triangles></mesh></object></resources>\n<build><item objectid="1"/></build>\n</model>\n`;
  const types = `<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">`
    + `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>`
    + `<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>\n`;
  const rels = `<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`
    + `<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>`
    + `</Relationships>\n`;
  return zip([["[Content_Types].xml", enc.encode(types)], ["_rels/.rels", enc.encode(rels)], ["3D/3dmodel.model", enc.encode(model)]]);
}
