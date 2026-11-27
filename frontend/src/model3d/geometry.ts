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
// Nothing here imports anything (manifold-3d and the fonts are handed in), so the browser and a test run with node
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
  "radius", "top", "text", "font", "bold", "italic", "align", "letter_spacing", "line_spacing", "points", "holes",
  "file", "repeat", "fillet", "chamfer", "on", "mates", "parts"];

/** A text's fonts (0.89.0; ten more 0.99.0), as fonts/index.json names them (tools/make_3d_font.py makes both): the
 *  first is the default. Other names a model may write for them, and the words in a name that ask for bold or
 *  italic. */
export const FONT_FAMILIES = ["sans", "condensed", "geometric", "rounded", "serif", "classic", "script", "handwriting",
  "marker", "stencil", "comic", "western", "blackletter", "futuristic", "pixel", "mono"];
export const FONT_ALIASES: Record<string, string> = {
  "barlow": "sans", "sans serif": "sans", "arial": "sans", "helvetica": "sans", "liberation sans": "sans",
  "dejavu sans": "sans", "roboto": "sans", "open sans": "sans", "inter": "sans",
  "oswald": "condensed", "bebas neue": "condensed", "bebas": "condensed", "impact": "condensed",
  "league gothic": "condensed", "arial narrow": "condensed", "narrow": "condensed",
  "montserrat": "geometric", "futura": "geometric", "century gothic": "geometric", "avenir": "geometric",
  "poppins": "geometric", "gotham": "geometric",
  "nunito": "rounded", "round": "rounded", "arial rounded": "rounded", "varela round": "rounded",
  "bitter": "serif", "slab": "serif", "times": "serif", "times new roman": "serif", "georgia": "serif",
  "liberation serif": "serif", "roboto slab": "serif",
  "cinzel": "classic", "trajan": "classic", "roman": "classic", "engraved": "classic",
  "pacifico": "script", "cursive": "script", "lobster": "script", "brush script": "script",
  "caveat": "handwriting", "handwritten": "handwriting", "hand": "handwriting",
  "knewave": "marker", "permanent marker": "marker", "sharpie": "marker",
  "saira stencil one": "stencil", "saira stencil": "stencil",
  "bangers": "comic", "comic book": "comic", "cartoon": "comic",
  "holtwood one sc": "western", "holtwood": "western", "rye": "western", "wild west": "western",
  "cowboy": "western", "saloon": "western", "wanted": "western",
  "grenze gotisch": "blackletter", "grenze": "blackletter", "unifrakturmaguntia": "blackletter",
  "unifraktur": "blackletter", "fraktur": "blackletter", "old english": "blackletter", "medieval": "blackletter",
  "oxanium": "futuristic", "audiowide": "futuristic", "orbitron": "futuristic", "sci fi": "futuristic",
  "scifi": "futuristic", "techno": "futuristic",
  "pixelify sans": "pixel", "pixelify": "pixel", "press start 2p": "pixel", "silkscreen": "pixel", "8 bit": "pixel",
  "8bit": "pixel", "arcade": "pixel",
  "jetbrains mono": "mono", "monospace": "mono", "courier": "mono", "courier new": "mono", "consolas": "mono",
  "liberation mono": "mono", "dejavu sans mono": "mono",
};
const BOLD_WORDS = ["bold", "extrabold", "black", "heavy"];
const ITALIC_WORDS = ["italic", "oblique"];
const PLAIN_WORDS = ["regular", "normal", "semibold", "medium", "book", "style"];
export type Align = "left" | "center" | "right";
/** How far an italic leans in a font that has none (tan 9°, the others' angle), and what letter_spacing and
 *  line_spacing may be: shares of the font's own spacing. */
export const SLANT = 0.15838444032453627;
export const SPACING: readonly [number, number] = [0.5, 4];

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
  text?: string;              // text: its letters, \n between lines
  font?: string;              // text: one of FONT_FAMILIES (left out: the first)
  bold?: boolean;             // text: the family's bold, where it has one
  italic?: boolean;           // text: its italic, or its letters slanted
  align?: Align;              // text: its lines (left out: centred)
  letter_spacing?: number;    // text: the space from one letter to the next, as a share of the font's (left out: 1)
  line_spacing?: number;      // text: the same from one line to the next
  points?: Point2[];          // outline: its shape, mm, around its own 0,0 (which `at` places)
  holes?: Point2[][];         // outline: shapes cut out of it
  file?: string;              // mesh: an STL, 3MF or OBJ in the working folder
  repeat?: Repeat;            // copies of it
  fillet?: EdgeSizes;         // its own edges rounded (0.91.0): box, wedge, cylinder, cone, an outline's corners
  chamfer?: EdgeSizes;        // ... or cut off: the same names, the size along each face
  on?: Anchor;                // kept on a face of a box (0.98.0): its at, rotate and size z follow from it - settle()
  mates?: Mate[];             // placed against other parts (0.101.0): its at and rotate follow from them - settle()
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
/** A file's own size (mm along X, Y and Z) when it is loaded: what a mesh part's 0s in size come from (0.104.0). */
export type OwnSize = (file: string) => Vec3 | null;

/** A design from its file's text, every part complete: missing or wrong values get defaults, said in `problems`.
 *  With `own`, a mesh part's 0s in size - all three when it has none - are worked out from its file (0.104.0, as
 *  hub3d's read_design with the design's folder); without, they stay 0 and the build works them out. */
export function readDesign(text: string, own?: OwnSize): { design: Design; problems: string[] } {
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
  const design: Design = { format: FORMAT, version: VERSION, parts: readParts(parts, seen, problems, odd, own) };
  design.parts = settle(design.parts, problems);         // parts on faces where their anchors put them (0.98.0)
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

function readParts(list: unknown[], seen: Set<string>, problems: string[], odd: Odd, own?: OwnSize): Part[] {
  const out: Part[] = [];
  for (const item of list) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      problems.push("A part that is not an object was left out.");
      continue;
    }
    out.push(readPart(item as Record<string, unknown>, seen, problems, out.length, odd, own));
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

function readPart(p: Record<string, unknown>, seen: Set<string>, problems: string[], index: number, odd: Odd,
  own?: OwnSize): Part {
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
    part.parts = readParts(Array.isArray(p.parts) ? p.parts : [], seen, problems, odd, own);
  } else {
    const size = vec(p.size, shape === "outline" ? [0, 0, 2] : shape === "text" ? [0, 10, 2]
      : shape === "mesh" ? [0, 0, 0] : [20, 20, 20]);                     // a mesh: its file's own size (0.104.0)
    part.size = size.map((v, i) => (follows(shape, i, v) ? 0 : Math.max(MIN_SIZE, Math.abs(v)))) as Vec3;
    if (shape === "text" && part.size[0] === 0 && part.size[1] === 0) {
      part.size[1] = 10;
      problems.push(`${id}: a text needs a width or a height (size x or y) - made 10 mm tall.`);
    }
    if (shape === "mesh" && own && part.size.includes(0) && typeof p.file === "string" && p.file) {
      const mine = own(p.file);
      if (mine) part.size = fitMesh(part.size, mine);
    }
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
  if (shape === "text") {
    part.text = typeof p.text === "string" ? p.text : "Text";
    readTextStyle(p, part, problems);
  }
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
  const fillet = readEdgeSizes(p.fillet, part, "fillet", problems);
  const chamfer = readEdgeSizes(p.chamfer, part, "chamfer", problems);
  if ((fillet || chamfer) && part.radius) {
    problems.push(`${id}: a box with a radius is rounded all round already - its fillet and chamfer were left out.`);
  } else {
    if (fillet) part.fillet = fillet;
    if (chamfer) part.chamfer = chamfer;
    if (fillet && (shape === "box" || shape === "wedge" || shape === "outline") && num(p.sides)) {
      part.sides = Math.max(3, Math.min(256, Math.round(p.sides)));        // how round its fillets are
    }
  }
  if (p.on !== undefined) {
    if (shape === "group") problems.push(`${id}: a group cannot go "on" a face (its parts can) - left out.`);
    else {
      const on = readOn(p.on, part, problems);
      if (on) part.on = on;
    }
  }
  if (p.mates !== undefined) {
    if (shape === "group") problems.push(`${id}: a group cannot have "mates" (its parts can) - left out.`);
    else if (part.on) problems.push(`${id}: "on" places it already - its "mates" were left out.`);
    else {
      const mates = readMates(p.mates, part, problems);
      if (mates) part.mates = mates;
    }
  }
  return part;
}

/** A part's "on" (0.98.0): the box and its face, the distances from the face's edges, how deep, the turn on it - or
 *  undefined, said. Neither a depth nor through: as deep (or as high) as the part is thick. */
function readOn(v: unknown, part: Part, problems: string[]): Anchor | undefined {
  const id = part.id;
  const o = v as Record<string, unknown>;
  if (!o || typeof o !== "object" || Array.isArray(o) || typeof o.part !== "string" || !o.part.trim()) {
    problems.push(`${id}: "on" names the box it is on: {"part": id, "face": "front", "from": {"left": 20, "top": 15}}`
      + " - left out.");
    return undefined;
  }
  const face = (typeof o.face === "string" ? o.face.trim().toLowerCase() : "") as FaceName;
  if (!FACES.includes(face)) {
    problems.push(`${id}: "on" needs the box's face: ${FACES.join(", ")} - left out.`);
    return undefined;
  }
  const odd = Object.keys(o).filter((k) => !ON_KEYS.includes(k));
  if (odd.length) problems.push(`${id}: "on" has no ${odd.map((k) => `"${k}"`).join(", ")} - its keys: ${ON_KEYS.join(", ")}.`);
  const out: Anchor = { part: o.part.trim(), face };
  const ends = FACE_FRAMES[face].ends;
  if (o.from !== undefined) {
    const f = o.from as Record<string, unknown>;
    if (!f || typeof f !== "object" || Array.isArray(f)) {
      problems.push(`${id}: "from" is {edge: mm} - the ${face}'s edges: ${ends.flat().join(", ")}; put in the middle.`);
    } else {
      const from: Record<string, number> = {};
      for (const [raw, d] of Object.entries(f)) {
        const key = raw.trim().toLowerCase();
        const axis = ends.findIndex((e) => e.includes(key));
        if (axis < 0) {
          problems.push(`${id}: the ${face}'s edges are ${ends.flat().join(", ")}, so many mm from each - "${raw}" left out.`);
        } else if (!num(d)) {
          problems.push(`${id}: "from" is mm from an edge - "${raw}" is not a number; left out.`);
        } else if (ends[axis].some((e) => e in from)) {
          problems.push(`${id}: "from" has ${ends[axis].join(" and ")} - one of the two; "${raw}" left out.`);
        } else from[key] = d;
      }
      if (Object.keys(from).length) out.from = from;
    }
  }
  if (o.through === true) out.through = true;
  if (o.depth !== undefined) {
    if (num(o.depth) && o.depth > 0) out.depth = o.depth;
    else problems.push(`${id}: "depth" is mm, more than 0 - left out.`);
  }
  if (!out.through && out.depth === undefined) out.depth = part.size[2];
  if (o.angle !== undefined) {
    if (!num(o.angle)) problems.push(`${id}: "angle" is degrees - left out.`);
    else if (Math.abs(o.angle % 360) > 1e-9) out.angle = o.angle;
  }
  return out;
}

/** Whether a 0 in a part's size follows what it draws: a text's width or height (from its letters - one of the two
 *  is given), an outline's width and depth (from its points), any of a mesh's three (from its file, 0.104.0). */
export function follows(shape: Shape, i: number, v: number): boolean {
  return v <= 0 && (shape === "mesh" || (i < 2 && (shape === "text" || shape === "outline")));
}

/** A mesh part's scale along X, Y and Z (0.104.0, hub3d's mesh_scale): a size given fills the file's own size there;
 *  a 0 follows the given ones - their scale, the mean when they differ - and with none given it is the file's own
 *  size. Chat 335: a mesh written without a size was squeezed into the 20 x 20 x 20 mm every other shape gets. */
export function meshScale(size: Vec3, own: Vec3): Vec3 {
  const given: number[] = [];
  for (let i = 0; i < 3; i++) if (size[i] > 0 && own[i] > 1e-9) given.push(size[i] / own[i]);
  const k = given.length ? given.reduce((a, b) => a + b, 0) / given.length : 1;
  return [0, 1, 2].map((i) => (own[i] > 1e-9 ? (size[i] > 0 ? size[i] / own[i] : k) : 1)) as Vec3;
}

/** A mesh part's size with its 0s worked out from the file's own size. */
export function fitMesh(size: Vec3, own: Vec3): Vec3 {
  const k = meshScale(size, own);
  return [0, 1, 2].map((i) => (size[i] > 0 ? size[i] : own[i] * k[i])) as Vec3;
}

/** A mesh file's own size: mm along X, Y and Z from its corners (as the build places it). */
export function meshExtent(m: MeshData): Vec3 {
  const [lo, hi] = meshBox(m);
  return [0, 1, 2].map((i) => (hi[i] >= lo[i] ? hi[i] - lo[i] : 0)) as Vec3;
}

/** The mesh parts whose sizes still have 0s that `own` can now work out, worked out - the rest as they were; null when
 *  there are none (0.104.0: the editor, once a file is loaded, for a design changed since it was read). */
export function fitMeshes(parts: Part[], own: (file: string) => Vec3 | null): Part[] | null {
  let changed = false;
  const walk = (list: Part[]): Part[] => list.map((p) => {
    if (p.parts) {
      const inner = walk(p.parts);
      return inner === p.parts ? p : { ...p, parts: inner };
    }
    if (p.shape !== "mesh" || !p.file || !p.size.includes(0)) return p;
    const mine = own(p.file);
    if (!mine) return p;
    changed = true;
    return { ...p, size: fitMesh(p.size, mine) };
  });
  const out = walk(parts);
  return changed ? out : null;
}

/** A font as a design may name it - "serif", "Pacifico", "Arial Bold", OpenSCAD's "Liberation Sans:style=Bold
 *  Italic" - as one of FONT_FAMILIES, and the bold or italic its words ask for. family "" when it is none of them. */
export function fontName(raw: string): { family: string; bold: boolean; italic: boolean } {
  const words = raw.toLowerCase().split(/[\s:_,=-]+/).filter(Boolean);
  const bold = words.some((w) => BOLD_WORDS.includes(w));
  const italic = words.some((w) => ITALIC_WORDS.includes(w));
  const name = words.filter((w) => !BOLD_WORDS.includes(w) && !ITALIC_WORDS.includes(w) && !PLAIN_WORDS.includes(w))
    .join(" ");
  const family = !name ? FONT_FAMILIES[0] : FONT_FAMILIES.includes(name) ? name : FONT_ALIASES[name] ?? "";
  return { family, bold, italic };
}

function readTextStyle(p: Record<string, unknown>, part: Part, problems: string[]): void {
  const id = part.id;
  if (p.font !== undefined) {
    const f = typeof p.font === "string" ? fontName(p.font) : { family: "", bold: false, italic: false };
    if (!f.family) {
      problems.push(`${id}: no font "${String(p.font)}" - the fonts are ${FONT_FAMILIES.join(", ")}; drawn in `
        + `${FONT_FAMILIES[0]}.`);
    } else if (f.family !== FONT_FAMILIES[0]) part.font = f.family;
    if (f.bold) part.bold = true;
    if (f.italic) part.italic = true;
  }
  if (p.bold === true) part.bold = true;
  if (p.italic === true) part.italic = true;
  if (p.align !== undefined) {
    const a = typeof p.align === "string" ? p.align.trim().toLowerCase() : "";
    if (a === "left" || a === "right") part.align = a;
    else if (!["center", "centre", "middle"].includes(a)) {
      problems.push(`${id}: align is "left", "center" or "right" - centred.`);
    }
  }
  for (const key of ["letter_spacing", "line_spacing"] as const) {
    const v = p[key];
    if (v === undefined) continue;
    if (!num(v)) {
      problems.push(`${id}: ${key} is a number, 1 the font's own - left out.`);
      continue;
    }
    const c = Math.max(SPACING[0], Math.min(SPACING[1], v));
    if (c !== v) problems.push(`${id}: ${key} is ${SPACING[0]} to ${SPACING[1]} - made ${c}.`);
    if (c !== 1) part[key] = c;
  }
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
  if (p.on) {                                        // after at and rotate: they follow from it
    const a: Record<string, unknown> = { part: p.on.part, face: p.on.face };
    if (p.on.from && Object.keys(p.on.from).length) {
      a.from = Object.fromEntries(Object.entries(p.on.from).map(([key, v]) => [key, r3(v)]));
    }
    if (p.on.depth !== undefined) a.depth = r3(p.on.depth);
    if (p.on.through) a.through = true;
    if (p.on.angle) a.angle = r3(p.on.angle);
    o.on = a;
  }
  if (p.mates?.length) {                             // after at and rotate too (0.101.0)
    o.mates = p.mates.map((m) => {
      const w: Record<string, unknown> = { type: m.type };
      if (m.face) w.face = m.face;
      w.to = m.to.face ? { part: m.to.part, face: m.to.face } : { part: m.to.part };
      if (m.type === "distance") w.mm = r3(m.mm ?? 0);
      if (m.flip) w.flip = true;
      return w;
    });
  }
  if (p.mirror) o.mirror = true;
  if (p.hole) o.hole = true;
  if (p.hidden) o.hidden = true;
  if (p.color && p.shape !== "group") o.color = p.color;
  if (p.sides !== undefined && (ROUND.includes(p.shape) || p.radius || p.fillet)) o.sides = p.sides;
  if (p.radius) o.radius = r3(p.radius);
  if (p.shape === "cone") o.top = r3(p.top ?? 0);
  if (p.shape === "text") {
    o.text = p.text ?? "";
    if (p.font && p.font !== FONT_FAMILIES[0]) o.font = p.font;
    if (p.bold) o.bold = true;
    if (p.italic) o.italic = true;
    if (p.align && p.align !== "center") o.align = p.align;
    if (p.letter_spacing !== undefined && p.letter_spacing !== 1) o.letter_spacing = r3(p.letter_spacing);
    if (p.line_spacing !== undefined && p.line_spacing !== 1) o.line_spacing = r3(p.line_spacing);
  }
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
  for (const kind of ["fillet", "chamfer"] as const) {
    const m = p[kind];
    if (m && Object.keys(m).length) o[kind] = Object.fromEntries(Object.entries(m).map(([key, v]) => [key, r3(v)]));
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
      (_m, inner: string) => `[${inner.replace(/,\s+/g, ", ")}]`)
    // a part's "on" on one line too (0.98.0), and each of its mates (0.101.0)
    .replace(/"on": \{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}/g, oneLine)
    .replace(/\{\s*"type": "(?:coincident|distance|concentric)"[^{}]*\{[^{}]*\}[^{}]*\}/g, oneLine) + "\n";
}

function oneLine(m: string): string {
  return m.replace(/\s*\n\s*/g, " ").replace(/\{ /g, "{").replace(/ \}/g, "}");
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

/** A part's size with the 0s worked out: a text's width or height from its letters (given its font), an outline's
 *  from its points. */
export function partSize(p: Part, fonts?: Fonts): Vec3 {
  if (p.shape === "text" && (p.size[0] <= 0 || p.size[1] <= 0)) {
    const t = fonts && textMeasure(p, fonts);
    return t ? [t.width, t.height, p.size[2]] : p.size;
  }
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
    if (k !== 1) {                         // a text's 0 (it follows its letters) stays 0, an outline's 0s too
      out.size = p.size.map((v, i) => (follows(p.shape, i, v) ? 0 : Math.max(MIN_SIZE, v * k))) as Vec3;
      if (p.shape === "outline" && (p.size[0] <= 0 || p.size[1] <= 0)) {
        const drawn = partSize(p);                    // scaled: its 0s become the drawn size times k
        out.size = [drawn[0] * k, drawn[1] * k, Math.max(MIN_SIZE, p.size[2] * k)];
      }
      if (p.radius) out.radius = p.radius * k;
      for (const kind of ["fillet", "chamfer"] as const) {
        const m = p[kind];
        if (m) out[kind] = Object.fromEntries(Object.entries(m).map(([key, v]) => [key, v * k]));
      }
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

/** A part put on a face of the design (0.97.0, "On a face"): turned so its own Z looks out of the face - a text's
 *  letters lie on it - reading across it: upright on a wall, on a top or an underside the way `right` points (the
 *  view's right). Its underside lies on the face at `point`, 0.2 mm into it so the two join; a hole is sunk into the
 *  face as deep as it is thick, its top 0.2 mm out, so it cuts in. A group turns as one, its box's bottom on the face
 *  (`box`: what it builds to). A mirrored part is put on unmirrored: a text would read backwards. */
export function onFace(p: Part, point: Vec3, normal: Vec3, right: Vec3, box?: Box3 | null): Part {
  const n = unit(normal);
  let u: Vec3 = Math.abs(n[2]) < 0.9 ? unit([-n[1], n[0], 0]) : [0, 0, 0];      // up x n: across a wall, as one faces it
  if (!u[0] && !u[1] && !u[2]) {
    const d = right[0] * n[0] + right[1] * n[1] + right[2] * n[2];
    u = unit([right[0] - d * n[0], right[1] - d * n[1], right[2] - d * n[2]]);
    if (!u[0] && !u[1] && !u[2]) u = [1, 0, 0];
  }
  const v: Vec3 = [n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0]];   // up the face
  const m: Mat3 = [u[0], v[0], n[0], u[1], v[1], n[1], u[2], v[2], n[2]];                           // columns u v n
  const group = p.shape === "group";
  const q: Part = group ? p : { ...p, mirror: undefined };
  const b = box ?? extent(q);
  const c: Vec3 = group && b ? [(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2, (b[0][2] + b[1][2]) / 2] : q.at;
  const h = group ? (b ? b[1][2] - b[0][2] : 0) : q.size[2];
  const r = group ? [1, 0, 0, 0, 1, 0, 0, 0, 1] as Mat3 : rotation(q.rotate);
  const rt: Mat3 = [r[0], r[3], r[6], r[1], r[4], r[7], r[2], r[5], r[8]];                         // its turn undone
  const lift = p.hole ? 0.2 - h / 2 : h / 2 - 0.2;
  const to: Vec3 = [point[0] + n[0] * lift, point[1] + n[1] * lift, point[2] + n[2] * lift];
  return transformPart(q, c, [to[0] - c[0], to[1] - c[1], to[2] - c[2]], mul(m, rt));
}

/** A part turned 90 degrees about the design's X, Y or Z through its middle (0.97.0, Arrange's Turn - Office's Rotate):
 *  `sign` +1 one way, -1 the other. One that stood on the plate still does; else its middle stays. */
export function turned90(p: Part, axis: 0 | 1 | 2, sign: 1 | -1, box?: Box3 | null): Part {
  const s = sign, t: Mat3 = axis === 0 ? [1, 0, 0, 0, 0, -s, 0, s, 0] : axis === 1 ? [0, 0, s, 0, 1, 0, -s, 0, 0]
    : [0, -s, 0, s, 0, 0, 0, 0, 1];
  const b = box ?? extent(p);
  const c: Vec3 = b ? [(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2, (b[0][2] + b[1][2]) / 2] : p.at;
  let out = transformPart(p, c, [0, 0, 0], t);
  if (b && Math.abs(b[0][2]) < 0.01) {                                  // on the plate: its new bottom there too
    const half = [b[1][0] - b[0][0], b[1][1] - b[0][1], b[1][2] - b[0][2]].map((x) => x / 2);
    const low = c[2] - Math.abs(t[6]) * half[0] - Math.abs(t[7]) * half[1] - Math.abs(t[8]) * half[2];
    out = transformPart(out, [0, 0, 0], [0, 0, -low], null);
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

// ---------------------------------------------------------------- on a face of a box (0.98.0)
//
// Søren: "Hvad gør jeg hvis jeg gerne vil lave en 2D cirkel et sted på kassen, og derefter skære hullet ud hele vejen
// igennem kassen? Ligesom i SolidWorks? Hvor jeg tegner en cirkel på den side jeg ønsker, og så låser afstandene til
// kanterne? Og så Boss Extrude?" A part may be kept on a face of a box: so far from two of the face's edges, its own Z
// out of the face, as deep (a hole) or as high (a solid) as it says - or through all of the box. It is placed there
// whenever the design is read or changed, so it goes with the box when the box moves, turns or grows: the distances
// stay, as a sketch's dimensions do.

export type FaceName = "top" | "bottom" | "front" | "back" | "left" | "right";
export const FACES: readonly FaceName[] = ["top", "bottom", "front", "back", "left", "right"];
const ON_KEYS = ["part", "face", "from", "depth", "through", "angle"];

/** Where a part is kept on a box (the part's "on"). */
export interface Anchor {
  part: string;                    // the box
  face: FaceName;                  // the box's own face - front is its -Y side before it is turned
  from?: Record<string, number>;   // mm from an edge across the face and from one up it, each named by the face beyond
                                   // it: {"left": 20, "top": 15}; an axis with neither: the middle
  depth?: number;                  // how far a hole goes in, or a solid stands out, mm
  through?: boolean;               // a hole: through all of the box
  angle?: number;                  // degrees about the face's normal, from across the face toward up it
}

/** A box's faces in its own frame: which way each looks (n), across it (u) and up it (v) - u x v = n, so a text on it
 *  reads across it, upright on a wall - and the faces beyond its edges: across, then up, each axis from its - end. */
export const FACE_FRAMES: Record<FaceName, { n: Vec3; u: Vec3; v: Vec3; ends: [[string, string], [string, string]] }> = {
  top: { n: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0], ends: [["left", "right"], ["front", "back"]] },
  bottom: { n: [0, 0, -1], u: [1, 0, 0], v: [0, -1, 0], ends: [["left", "right"], ["back", "front"]] },
  front: { n: [0, -1, 0], u: [1, 0, 0], v: [0, 0, 1], ends: [["left", "right"], ["bottom", "top"]] },
  back: { n: [0, 1, 0], u: [-1, 0, 0], v: [0, 0, 1], ends: [["right", "left"], ["bottom", "top"]] },
  left: { n: [-1, 0, 0], u: [0, -1, 0], v: [0, 0, 1], ends: [["back", "front"], ["bottom", "top"]] },
  right: { n: [1, 0, 0], u: [0, 1, 0], v: [0, 0, 1], ends: [["front", "back"], ["bottom", "top"]] },
};

const dot3 = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross3 = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const reach = (d: Vec3, s: Vec3) => Math.abs(d[0]) * s[0] + Math.abs(d[1]) * s[1] + Math.abs(d[2]) * s[2];

/** A face of a box where it is in the design: its width across, height up and the box's depth behind it (mm); which
 *  way it looks, up it and across it (N, V, U = V x N: a turn's columns, a mirrored box's too) - and `at`, where the
 *  spot x across and y up from its middle is. */
export function faceFrame(box: Part, face: FaceName): { W: number; H: number; T: number; U: Vec3; V: Vec3; N: Vec3;
  at: (x: number, y: number) => Vec3 } {
  const f = FACE_FRAMES[face];
  const m = rotation(box.rotate);
  const own = (d: Vec3): Vec3 => apply(m, box.mirror ? [-d[0], d[1], d[2]] : d);
  const W = reach(f.u, box.size), H = reach(f.v, box.size), T = reach(f.n, box.size);
  const N = own(f.n), V = own(f.v), U = cross3(V, N);
  const at = (x: number, y: number): Vec3 => {
    const w = own([f.n[0] * T / 2 + f.u[0] * x + f.v[0] * y, f.n[1] * T / 2 + f.u[1] * x + f.v[1] * y,
      f.n[2] * T / 2 + f.u[2] * x + f.v[2] * y]);
    return [w[0] + box.at[0], w[1] + box.at[1], w[2] + box.at[2]];
  };
  return { W, H, T, U, V, N, at };
}

/** Where an anchor's spot is: x across and y up from its face's middle, mm. */
export function spotXY(a: Anchor, W: number, H: number): [number, number] {
  const ends = FACE_FRAMES[a.face].ends, f = a.from ?? {};
  const one = (axis: 0 | 1, L: number) => {
    const [lo, hi] = ends[axis];
    return f[lo] !== undefined ? f[lo] - L / 2 : f[hi] !== undefined ? L / 2 - f[hi] : 0;
  };
  return [one(0, W), one(1, H)];
}

/** The distances for a spot x across and y up from a face's middle: from the edges `like` names - an axis it names
 *  neither of stays in the middle while the spot is there - else from the nearer edge; `step` rounds them (a click:
 *  1 mm). undefined: the middle both ways. */
export function spotFrom(face: FaceName, x: number, y: number, W: number, H: number, like?: Record<string, number>,
  step = 0): Record<string, number> | undefined {
  const ends = FACE_FRAMES[face].ends;
  const out: Record<string, number> = {};
  ([[x, W], [y, H]] as const).forEach(([c, L], axis) => {
    const [lo, hi] = ends[axis];
    const key = like?.[lo] !== undefined ? lo : like?.[hi] !== undefined ? hi : like && Math.abs(c) < 1e-3 ? ""
      : c < 0 ? lo : hi;
    if (!key) return;
    const d = key === lo ? c + L / 2 : L / 2 - c;
    out[key] = step ? Math.round(d / step) * step + 0 : r3(d);
  });
  return Object.keys(out).length ? out : undefined;
}

/** A part where its anchor puts it on its box: its own Z out of the face and its X across it (turned by `angle`),
 *  unmirrored, its middle over the spot. A solid stands out of the face as far as its depth, 0.2 mm into it so the two
 *  join; a hole goes in as deep, its top 0.2 mm out so it cuts cleanly - or through all of the box, 0.2 mm out on both
 *  sides. Its size z is what that takes. */
export function placeOn(p: Part, box: Part): Part {
  const a = p.on!;
  const F = faceFrame(box, a.face);
  const [x, y] = spotXY(a, F.W, F.H);
  const at = F.at(x, y);
  const t = ((a.angle ?? 0) * Math.PI) / 180, c = Math.cos(t), s = Math.sin(t);
  const X: Vec3 = [F.U[0] * c + F.V[0] * s, F.U[1] * c + F.V[1] * s, F.U[2] * c + F.V[2] * s];
  const Y: Vec3 = [F.V[0] * c - F.U[0] * s, F.V[1] * c - F.U[1] * s, F.V[2] * c - F.U[2] * s];
  const h = r3(a.through && p.hole ? F.T + 0.4 : (a.depth ?? F.T) + 0.2);
  const lift = p.hole ? 0.2 - h / 2 : h / 2 - 0.2;
  const out: Part = { ...p, at: [r3(at[0] + F.N[0] * lift), r3(at[1] + F.N[1] * lift), r3(at[2] + F.N[2] * lift)],
    rotate: angles([X[0], Y[0], F.N[0], X[1], Y[1], F.N[1], X[2], Y[2], F.N[2]]), size: [p.size[0], p.size[1], h] };
  delete out.mirror;
  return out;
}

export function sameAnchor(a: Anchor, b: Anchor): boolean {
  const fa = a.from ?? {}, fb = b.from ?? {};
  const ka = Object.keys(fa);
  return a.part === b.part && a.face === b.face && !!a.through === !!b.through && a.depth === b.depth
    && (a.angle ?? 0) === (b.angle ?? 0) && ka.length === Object.keys(fb).length && ka.every((k) => fa[k] === fb[k]);
}

/** A part on a face with its anchor's distances for where its middle is now: from the same edges - one in the middle
 *  stays there while it is - to the spot under it on the face. */
export function respot(p: Part, box: Part): Part {
  const a = p.on!;
  const f = FACE_FRAMES[a.face], F = faceFrame(box, a.face);
  const l = toPart(box, p.at);
  const from = spotFrom(a.face, dot3(l, f.u), dot3(l, f.v), F.W, F.H, a.from ?? {});
  const on: Anchor = { ...a, from };
  if (!from) delete on.from;
  return { ...p, on };
}

/** A part on a face as an edit in the editor left it (`was`: before it): moved, its spot is where its middle is now
 *  (respot) - moved off the face, it goes back onto it; turned about the face's normal, its angle follows - turned off
 *  the face, it is free; a new size z is a new depth. Its anchor changed, or nothing: placed by its anchor. */
function edited(p: Part, was: Part | undefined, box: Part): Part {
  const a = p.on;
  if (!a || !was?.on || !sameAnchor(a, was.on)) return p;
  let q = p;
  if (!!p.mirror !== !!was.mirror || p.rotate.some((v, i) => Math.abs(v - was.rotate[i]) > 1e-3)) {
    const F = faceFrame(box, a.face), m = rotation(p.rotate);
    const z: Vec3 = [m[2], m[5], m[8]], x: Vec3 = p.mirror ? [-m[0], -m[3], -m[6]] : [m[0], m[3], m[6]];
    if (dot3(z, F.N) < 0.9999) {
      q = { ...p };
      delete q.on;
      return q;
    }
    const angle = r3((Math.atan2(dot3(x, F.V), dot3(x, F.U)) * 180) / Math.PI);
    const on: Anchor = { ...a, angle };
    if (!angle) delete on.angle;
    q = { ...q, on };
  }
  if (p.at.some((v, i) => Math.abs(v - was.at[i]) > 1e-4)) q = respot(q, box);
  if (Math.abs(p.size[2] - was.size[2]) > 1e-6 && !(a.through && p.hole)) {
    q = { ...q, on: { ...q.on!, depth: Math.max(MIN_SIZE, r3(p.size[2] - 0.2)) } };
  }
  return q;
}

/** The parts, each one with "on" where its anchor puts it - after the box it is on, which may be on a face too. One
 *  whose box is not there, is not a box or leads round in a circle is left where it is, free (said in `problems`).
 *  One with mates (0.101.0) where they put it, after the parts they are to - a mate to a part that is not there, to
 *  a group or round in a circle left out (said). `before` (the editor's): the parts as they were before an edit - one
 *  moved, turned or sized by it gets its anchor for what was done (edited, above) before it is placed; one it did not
 *  touch goes with the part of its first mate, moved or turned, before it is mated (followed, below). */
export function settle(parts: Part[], problems?: string[], before?: Part[]): Part[] {
  const index = (list: Part[], into: Map<string, Part>) => {
    for (const p of list) {
      into.set(p.id, p);
      if (p.parts) index(p.parts, into);
    }
    return into;
  };
  const all = index(parts, new Map());
  if (![...all.values()].some((p) => p.on || p.mates)) return parts;
  const was = before ? index(before, new Map()) : null;
  const done = new Map<string, Part>(), busy = new Set<string>();
  const free = (p: Part, why: string): Part => {
    problems?.push(`${p.id}: ${why} - left where it is.`);
    const q = { ...p };
    delete q.on;
    return q;
  };
  const resolve = (p: Part): Part => {
    const had = done.get(p.id);
    if (had) return had;
    let out = p;
    const a = p.on;
    if (a) {
      const b = all.get(a.part);
      if (busy.has(p.id)) out = free(p, `its "on" leads round in a circle`);
      else if (!b) out = free(p, `"on" names "${a.part}", which the design does not have`);
      else if (b.shape !== "box") out = free(p, `"on" is a box's face - ${b.id} is a ${b.shape}`);
      else {
        busy.add(p.id);
        const box = resolve(b);
        busy.delete(p.id);
        const freed = done.get(p.id);              // on the way round a circle
        if (freed) return freed;
        const q = was ? edited(p, was.get(p.id), box) : p;
        out = q.on ? placeOn(q, box) : q;
      }
    } else if (p.mates) out = mated(p);
    done.set(p.id, out);
    return out;
  };
  // Whether a part is placed by another - on it, mated to it, or to one that is: those placed already as they are
  // now (what was left out of them left out), the others as they are given.
  const leads = (from: Part, to: string, seen = new Set<string>()): boolean => {
    if (from.id === to) return true;
    if (seen.has(from.id)) return false;
    seen.add(from.id);
    const f = done.get(from.id) ?? from;
    return [f.on?.part, ...(f.mates ?? []).map((m) => m.to.part)].some((id) => {
      const q = id === undefined ? undefined : all.get(id);
      return !!q && leads(q, to, seen);
    });
  };
  const mated = (p: Part): Part => {
    const kept: [Mate, number][] = [], to = new Map<string, Part>();
    p.mates!.forEach((m, i) => {
      const q = all.get(m.to.part);
      const why = !q ? `names "${m.to.part}", which the design does not have`
        : q.id === p.id ? "is to the part itself"
          : q.shape === "group" ? `is to a group, ${q.id} (mate one of its parts)`
            : leads(q, p.id) ? `leads round in a circle: ${q.id} is placed by ${p.id}`
              : m.to.face && lettersSide(q, m.to.face) ? `is to the ${m.to.face} of ${q.id}, a text whose size there `
                + "follows its letters (mate another face)" : "";
      if (why) {
        problems?.push(`${p.id}: mate ${i + 1} ${why} - left out.`);
        return;
      }
      to.set(q!.id, resolve(q!));
      kept.push([m, i]);
    });
    let q: Part = { ...p, mates: kept.map(([m]) => m) };
    if (!kept.length) {
      delete q.mates;
      return q;
    }
    if (was) q = followed(q, was.get(p.id), was.get(kept[0][0].to.part), to.get(kept[0][0].to.part)!);
    return solveMates(q, kept, to, problems);
  };
  const walk = (list: Part[]): Part[] => list.map((p) => (p.shape === "group" ? { ...p, parts: walk(p.parts ?? []) }
    : resolve(p)));
  return walk(parts);
}

/** Which face of a box a spot on it is on, from which way the model's face there looks (0.98.0: a click picks it) -
 *  null on a rounded or cut edge, or off its outside. */
export function faceAt(box: Part, point: Vec3, normal: Vec3): FaceName | null {
  if (box.shape !== "box") return null;
  const m = rotation(box.rotate);
  const l: Vec3 = [m[0] * normal[0] + m[3] * normal[1] + m[6] * normal[2], m[1] * normal[0] + m[4] * normal[1]
    + m[7] * normal[2], m[2] * normal[0] + m[5] * normal[1] + m[8] * normal[2]];
  const n = unit(box.mirror ? [-l[0], l[1], l[2]] : l);
  const face = FACES.find((x) => dot3(n, FACE_FRAMES[x].n) > 0.999);
  if (!face) return null;
  const f = FACE_FRAMES[face];
  return Math.abs(dot3(toPart(box, point), f.n) - reach(f.n, box.size) / 2) < 0.05 ? face : null;
}

/** The spot on a box's face a point on it is at (a click), as distances from the nearer edges - to `step` mm. */
export function spotAt(box: Part, face: FaceName, point: Vec3, step = 1): Record<string, number> | undefined {
  const f = FACE_FRAMES[face], F = faceFrame(box, face), l = toPart(box, point);
  return spotFrom(face, dot3(l, f.u), dot3(l, f.v), F.W, F.H, undefined, step);
}

/** The angle that sets a text upright on a face: on a wall its letters' up as near the design's up as a quarter turn
 *  gets; on a top or an underside its lines along `right` - the view's right, as On a face reads (0.97.0). */
export function faceAngle(box: Part, face: FaceName, right: Vec3): number {
  const F = faceFrame(box, face);
  let best = 0, score = -Infinity;
  for (const angle of [0, 90, 180, -90]) {
    const t = (angle * Math.PI) / 180, c = Math.cos(t), s = Math.sin(t);
    const X: Vec3 = [F.U[0] * c + F.V[0] * s, F.U[1] * c + F.V[1] * s, F.U[2] * c + F.V[2] * s];
    const v = Math.abs(F.N[2]) < 0.9 ? F.V[2] * c - F.U[2] * s : dot3(X, right);
    if (v > score + 1e-9) {
      best = angle;
      score = v;
    }
  }
  return best;
}

/** What shows a spot on a face (0.98.0, the editor's Face tab): the face's outline; a circle or a square `size` across
 *  there, a cross at its middle; and a line to its middle from each edge it is measured from, with how far. */
export function faceMarks(box: Part, face: FaceName, from: Record<string, number> | undefined, round: boolean,
  size: number): { outline: Vec3[]; shape: Vec3[][]; dims: { line: Vec3[]; mm: number }[] } {
  const F = faceFrame(box, face), ends = FACE_FRAMES[face].ends, f = from ?? {};
  const [x, y] = spotXY({ part: box.id, face, from }, F.W, F.H);
  const w = F.W / 2, h = F.H / 2, r = size / 2;
  const shape: Vec3[][] = [];
  if (round) {
    const ring: Vec3[] = [];
    for (let i = 0; i <= 48; i++) ring.push(F.at(x + r * Math.cos((Math.PI * i) / 24), y + r * Math.sin((Math.PI * i) / 24)));
    shape.push(ring);
  } else shape.push([F.at(x - r, y - r), F.at(x + r, y - r), F.at(x + r, y + r), F.at(x - r, y + r), F.at(x - r, y - r)]);
  const k = Math.max(0.5, Math.min(2, r / 2));
  shape.push([F.at(x - k, y), F.at(x + k, y)], [F.at(x, y - k), F.at(x, y + k)]);
  const dims: { line: Vec3[]; mm: number }[] = [];
  const [[lo0, hi0], [lo1, hi1]] = ends;
  if (f[lo0] !== undefined) dims.push({ line: [F.at(-w, y), F.at(x, y)], mm: f[lo0] });
  else if (f[hi0] !== undefined) dims.push({ line: [F.at(x, y), F.at(w, y)], mm: f[hi0] });
  if (f[lo1] !== undefined) dims.push({ line: [F.at(x, -h), F.at(x, y)], mm: f[lo1] });
  else if (f[hi1] !== undefined) dims.push({ line: [F.at(x, y), F.at(x, h)], mm: f[hi1] });
  return { outline: [F.at(-w, -h), F.at(w, -h), F.at(w, h), F.at(-w, h), F.at(-w, -h)], shape, dims };
}

// ---------------------------------------------------------------- mates (0.101.0)
//
// Søren: "Og så skal man også kunne mate i 3D". As in SolidWorks' assemblies: a face of a part against a face of
// another (coincident - or flush, the two looking the same way, with flip), so many mm out from it (distance), or its
// axis on the other's (concentric). The part the mates are on is the one that moves: whenever the design is read or
// changed it is turned and moved, as little as they need, to where they put it - after the parts they are to - and in
// the editor it goes with the part of its first mate when that one is moved. A part's faces are its own box's, before
// it is turned (as a box's are for "on"), whatever its shape - a cylinder's top, a sphere's front; its axis is its own
// Z through its middle - a cylinder's, a cone's, a round hole's.

export type MateType = "coincident" | "distance" | "concentric";
export const MATE_TYPES: readonly MateType[] = ["coincident", "distance", "concentric"];
const MATE_KEYS = ["type", "face", "to", "mm", "flip"];
/** How far a coincident face of a solid goes into the solid it is on, mm: nothing to see - not in the file, which keeps
 *  a thousandth - but the two are one piece (measured: exactly flat on each other, turned askew, half are two). */
export const JOIN = 1e-5;

/** One of a part's mates. */
export interface Mate {
  type: MateType;
  face?: FaceName;                        // coincident, distance: the part's own face
  to: { part: string; face?: FaceName };  // the part it is mated to - and, face to face, that part's face
  mm?: number;                            // distance: how far out of the other's face this one is (less than 0: into it)
  flip?: boolean;                         // faces flush - looking the same way - not against each other; axes pointing
                                          // opposite ways
}

/** Which axis of a part's own box a face is across: 0 X, 1 Y, 2 Z. */
export const faceAxis = (face: FaceName): number => FACE_FRAMES[face].n.findIndex((x) => x !== 0);

/** Whether a face of a part is a text's side whose place follows its letters (its size there 0): unknown until its
 *  font is - not one to mate. */
export const lettersSide = (p: Part, face: FaceName): boolean => p.shape === "text" && p.size[faceAxis(face)] <= 0;

function faceIn(v: unknown): FaceName | null {
  const f = typeof v === "string" ? v.trim().toLowerCase() : "";
  return FACES.includes(f as FaceName) ? f as FaceName : null;
}

/** A part's "mates": each a type, its face and the part (and face) it is to - those that make no sense left out, said. */
function readMates(v: unknown, part: Part, problems: string[]): Mate[] | undefined {
  const id = part.id;
  if (!Array.isArray(v)) {
    problems.push(`${id}: "mates" is a list - [{"type": "coincident", "face": "bottom", "to": {"part": id, "face": "top"}}];`
      + " left out.");
    return undefined;
  }
  const out: Mate[] = [];
  v.forEach((raw, i) => {
    const n = `${id}: mate ${i + 1}`;
    const m = raw as Record<string, unknown>;
    if (!m || typeof m !== "object" || Array.isArray(m)) {
      problems.push(`${n} is not an object - left out.`);
      return;
    }
    const odd = Object.keys(m).filter((k) => !MATE_KEYS.includes(k));
    if (odd.length) problems.push(`${n} has no ${odd.map((k) => `"${k}"`).join(", ")} - its keys: ${MATE_KEYS.join(", ")}.`);
    const t = m.to as Record<string, unknown> | string | undefined;
    const to = typeof t === "string" ? { part: t.trim(), face: undefined as unknown }
      : t && typeof t === "object" && !Array.isArray(t) && typeof t.part === "string" ? { part: t.part.trim(), face: t.face }
        : null;
    if (!to || !to.part) {
      problems.push(`${n} names the part it is mated to: "to": {"part": id, "face": "top"} - left out.`);
      return;
    }
    if (t && typeof t === "object") {
      const oddTo = Object.keys(t).filter((k) => k !== "part" && k !== "face");
      if (oddTo.length) problems.push(`${n}: "to" has no ${oddTo.map((k) => `"${k}"`).join(", ")} - its keys: part, face.`);
    }
    const type = (m.type !== undefined ? (typeof m.type === "string" ? m.type.trim().toLowerCase() : "")
      : m.face !== undefined || to.face !== undefined ? (num(m.mm) && m.mm !== 0 ? "distance" : "coincident")
        : "concentric") as MateType;
    if (!MATE_TYPES.includes(type)) {
      problems.push(`${n}: no type${typeof m.type === "string" ? ` "${m.type}"` : ""} - the types: ${MATE_TYPES.join(", ")};`
        + " left out.");
      return;
    }
    const mate: Mate = { type, to: { part: to.part } };
    if (type === "concentric") {
      if (m.face !== undefined || to.face !== undefined) problems.push(`${n}: concentric is axis on axis - its faces were left out.`);
      if (m.mm !== undefined) problems.push(`${n}: concentric has no "mm" - left out.`);
    } else {
      const face = faceIn(m.face), toFace = faceIn(to.face);
      if (!face || !toFace) {
        problems.push(`${n}: ${type} is face to face - "face" and the "to" part's "face" are each one of ${FACES.join(", ")};`
          + " left out.");
        return;
      }
      if (lettersSide(part, face)) {
        problems.push(`${n}: its ${face} follows its letters (a text, its size there 0) - mate another face; left out.`);
        return;
      }
      mate.face = face;
      mate.to.face = toFace;
      if (type === "distance") {
        mate.mm = num(m.mm) ? m.mm : 0;
        if (!num(m.mm)) problems.push(`${n}: distance needs "mm" - made 0.`);
      } else if (m.mm !== undefined && !(num(m.mm) && m.mm === 0)) {
        problems.push(`${n}: coincident has no "mm" (distance has) - left out.`);
      }
    }
    if (m.flip === true) mate.flip = true;
    out.push(mate);
  });
  return out.length ? out : undefined;
}

/** A part's own frame in the design: A, its turn and mirror (own -> design), its own box's middle and half sizes in
 *  its own frame, and that middle in the design. */
function partFrame(p: Part): { A: Mat3; mid: Vec3; half: Vec3; c: Vec3 } {
  const [lo, hi] = ownBox(p);
  const R = rotation(p.rotate);
  const A: Mat3 = p.mirror ? [-R[0], R[1], R[2], -R[3], R[4], R[5], -R[6], R[7], R[8]] : R;   // R times mirror X
  const mid: Vec3 = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
  const half: Vec3 = [(hi[0] - lo[0]) / 2, (hi[1] - lo[1]) / 2, (hi[2] - lo[2]) / 2];
  const w = apply(A, mid);
  return { A, mid, half, c: [w[0] + p.at[0], w[1] + p.at[1], w[2] + p.at[2]] };
}

/** A face of a part's own box in the design: its middle, and which way it looks. */
export function matePlane(p: Part, face: FaceName): { point: Vec3; normal: Vec3 } {
  const { A, mid, half } = partFrame(p), n = FACE_FRAMES[face].n;
  const w = apply(A, [mid[0] + n[0] * half[0], mid[1] + n[1] * half[1], mid[2] + n[2] * half[2]]);
  return { point: [w[0] + p.at[0], w[1] + p.at[1], w[2] + p.at[2]], normal: apply(A, n) };
}

/** A part's axis in the design: its own Z, through its middle. */
export function mateAxis(p: Part): { point: Vec3; dir: Vec3 } {
  const { A, c } = partFrame(p);
  return { point: c, dir: [A[2], A[5], A[8]] };
}

/** The turn by `angle` radians about the unit axis k. */
function turnAbout(k: Vec3, angle: number): Mat3 {
  const c = Math.cos(angle), s = Math.sin(angle), t = 1 - c, [x, y, z] = k;
  return [t * x * x + c, t * x * y - s * z, t * x * z + s * y,
    t * x * y + s * z, t * y * y + c, t * y * z - s * x,
    t * x * z - s * y, t * y * z + s * x, t * z * z + c];
}

/** The least turn that takes the unit v onto the unit d - null when it is there; the other way round, half a turn
 *  about the part's own axis (A's columns) most across v. */
function turnOnto(v: Vec3, d: Vec3, A: Mat3): Mat3 | null {
  const k = cross3(v, d), s = Math.hypot(k[0], k[1], k[2]), c = dot3(v, d);
  if (s < 1e-9) {
    if (c > 0) return null;
    let e: Vec3 = [A[0], A[3], A[6]], least = Math.abs(dot3(e, v));
    for (const j of [1, 2]) {
      const f: Vec3 = [A[j], A[3 + j], A[6 + j]], g = Math.abs(dot3(f, v));
      if (g < least - 1e-12) {
        e = f;
        least = g;
      }
    }
    const x = dot3(e, v);
    return turnAbout(unit([e[0] - x * v[0], e[1] - x * v[1], e[2] - x * v[2]]), Math.PI);
  }
  const angle = Math.atan2(s, c);
  return angle < 1e-7 ? null : turnAbout([k[0] / s, k[1] / s, k[2] / s], angle);
}

/** Two unit directions across the unit d, square to it and to each other. */
function across(d: Vec3): [Vec3, Vec3] {
  const a = Math.abs(d[0]), b = Math.abs(d[1]), c = Math.abs(d[2]);
  const e: Vec3 = a <= b && a <= c ? [1, 0, 0] : b <= c ? [0, 1, 0] : [0, 0, 1];
  const u = unit(cross3(d, e));
  return [u, cross3(d, u)];
}

/** A part turned by `turn` about `centre` and moved by `move` (transformPart's sums, for one part): its at, rotate,
 *  and where its copies go - as hub3d's _moved. */
function moved(p: Part, centre: Vec3, move: Vec3, turn: Mat3 | null): Part {
  const rel: Vec3 = [p.at[0] - centre[0], p.at[1] - centre[1], p.at[2] - centre[2]];
  const r = turn ? apply(turn, rel) : rel;
  const out: Part = { ...p, at: [centre[0] + r[0] + move[0], centre[1] + r[1] + move[1], centre[2] + r[2] + move[2]] };
  if (turn) out.rotate = angles(mul(turn, rotation(p.rotate)));
  if (p.repeat) {
    const rp: Repeat = { ...p.repeat };
    if (rp.step) rp.step = turn ? apply(turn, rp.step) : rp.step;
    else if (rp.around) {
      const a: Vec3 = [rp.around[0] - centre[0], rp.around[1] - centre[1], 0];
      const w = turn ? apply(turn, a) : a;
      rp.around = [centre[0] + w[0] + move[0], centre[1] + w[1] + move[1]];
    }
    out.repeat = rp;
  }
  return out;
}

/** Where a part's mates put it, the parts they are to (`to`) placed already. Turned first - each mate's directions in
 *  turn: its face to look against the other's (or the same way), its axis along the other's - as little as the turns
 *  the mates before it leave allow, about its middle; then moved as little as puts each face on the other's plane (so
 *  many mm out) and its axis on the other's line. In what they leave free it stays. A mate the ones before it leave no
 *  room for is said in `problems` and skipped, kept: it may be met again when they change. `mates` carry their
 *  numbers in the part's list. */
function solveMates(p: Part, mates: [Mate, number][], to: Map<string, Part>, problems?: string[]): Part {
  const f0 = partFrame(p), { mid, half } = f0;
  let A = f0.A, R = rotation(p.rotate), turn: Mat3 | null = null;
  let free: Vec3 | "all" | "none" = "all";              // the turns left: all, about one axis, none
  const skipped = new Set<number>();
  const say = (m: Mate, i: number, why: string) => {
    skipped.add(i);
    problems?.push(`${p.id}: mate ${i + 1} (${m.type} to ${m.to.part}) cannot be met as well as the ones before it - ${why};`
      + " skipped.");
  };
  for (const [m, i] of mates) {
    const q = to.get(m.to.part)!;
    let v: Vec3, d: Vec3;
    if (m.type === "concentric") {
      const z = mateAxis(q).dir;
      v = [A[2], A[5], A[8]];
      d = m.flip ? [-z[0], -z[1], -z[2]] : z;
    } else {
      const n = matePlane(q, m.to.face!).normal;
      v = apply(A, FACE_FRAMES[m.face!].n);
      d = m.flip ? n : [-n[0], -n[1], -n[2]];
    }
    let t: Mat3 | null = null;
    if (free === "all") {
      t = turnOnto(v, d, A);
      free = d;
    } else if (free !== "none") {
      const a = free, cv = dot3(v, a), cd = dot3(d, a);
      if (Math.abs(Math.acos(Math.max(-1, Math.min(1, cv))) - Math.acos(Math.max(-1, Math.min(1, cd)))) > 1e-6) {
        say(m, i, "it would have to turn");
        continue;
      }
      const pv: Vec3 = [v[0] - cv * a[0], v[1] - cv * a[1], v[2] - cv * a[2]];
      const pd: Vec3 = [d[0] - cd * a[0], d[1] - cd * a[1], d[2] - cd * a[2]];
      if (Math.hypot(pv[0], pv[1], pv[2]) > 1e-6) {    // across the axis: turned about it, and then no turn is left
        const angle = Math.atan2(dot3(a, cross3(pv, pd)), dot3(pv, pd));
        if (Math.abs(angle) > 1e-7) t = turnAbout(a, angle);
        free = "none";
      }
    } else {
      const k = cross3(v, d);
      if (Math.atan2(Math.hypot(k[0], k[1], k[2]), dot3(v, d)) > 1e-6) {
        say(m, i, "it would have to turn");
        continue;
      }
    }
    if (t) {
      A = mul(t, A);
      R = mul(t, R);
      turn = turn ? mul(t, turn) : t;
    }
  }
  // Its middle stays while it turns: there, at is ...
  const c = f0.c, w0 = apply(A, mid);
  let at: Vec3 = turn ? [c[0] - w0[0], c[1] - w0[1], c[2] - w0[2]] : [...p.at];
  let basis: Vec3[] = [[1, 0, 0], [0, 1, 0], [0, 0, 1]], shift = false;
  // ... then each move: along `j` by `r` - in what is still free, as little as does it; what it fixes is not free after.
  const push = (m: Mate, i: number, j: Vec3, r: number): boolean => {
    const w: Vec3 = [0, 0, 0];
    for (const b of basis) {
      const x = dot3(j, b);
      w[0] += x * b[0];
      w[1] += x * b[1];
      w[2] += x * b[2];
    }
    const ww = dot3(w, w);
    if (ww < 1e-12) {
      if (Math.abs(r) <= 2e-3) return true;
      say(m, i, `${fmtMm(Math.abs(r))} mm off`);
      return false;
    }
    if (Math.abs(r) > 1e-9) {
      at = [at[0] + (r * w[0]) / ww, at[1] + (r * w[1]) / ww, at[2] + (r * w[2]) / ww];
      shift = true;
    }
    const u = unit(w), next: Vec3[] = [];
    for (const b of basis) {
      let x: Vec3 = [b[0] - dot3(b, u) * u[0], b[1] - dot3(b, u) * u[1], b[2] - dot3(b, u) * u[2]];
      for (const o of next) x = [x[0] - dot3(x, o) * o[0], x[1] - dot3(x, o) * o[1], x[2] - dot3(x, o) * o[2]];
      const l = Math.hypot(x[0], x[1], x[2]);
      if (l > 1e-6) next.push([x[0] / l, x[1] / l, x[2] / l]);
    }
    basis = next;
    return true;
  };
  const place = (l: Vec3): Vec3 => {
    const w = apply(A, l);
    return [w[0] + at[0], w[1] + at[1], w[2] + at[2]];
  };
  for (const [m, i] of mates) {
    if (skipped.has(i)) continue;
    const q = to.get(m.to.part)!;
    if (m.type === "concentric") {
      const L = mateAxis(q);
      for (const u of across(L.dir)) {
        const e = place(mid);
        if (!push(m, i, u, -dot3([e[0] - L.point[0], e[1] - L.point[1], e[2] - L.point[2]], u))) break;
      }
    } else {
      const P = matePlane(q, m.to.face!), n = FACE_FRAMES[m.face!].n;
      const e = place([mid[0] + n[0] * half[0], mid[1] + n[1] * half[1], mid[2] + n[2] * half[2]]);
      // A solid's face on another solid's - or flush with a hole's, where the solid round the hole is - a hair into
      // it: built as one piece however the two are turned (only touching, a face apart by a rounding, they are two).
      const want = m.type === "distance" ? m.mm ?? 0 : p.hole ? 0 : q.hole ? (m.flip ? JOIN : 0) : m.flip ? 0 : -JOIN;
      push(m, i, P.normal, want - dot3([e[0] - P.point[0], e[1] - P.point[1], e[2] - P.point[2]], P.normal));
    }
  }
  if (!turn && !shift) return p;
  const w1 = apply(A, mid), c1: Vec3 = [w1[0] + at[0], w1[1] + at[1], w1[2] + at[2]];
  const out = moved(p, c, [c1[0] - c[0], c1[1] - c[1], c1[2] - c[2]], turn);
  if (turn) out.rotate = angles(R);
  // Not rounded: faces put together on parts turned askew then meet exactly and are built as one piece - to a
  // thousandth of a mm, half of them are a hair apart, two (measured). The file keeps a thousandth; read, it is
  // put back here.
  out.at = at;
  return out;
}

/** In an edit (the editor's `before`): a part the edit did not touch - as it was - whose first mate's part it moved or
 *  turned (not sized or mirrored), goes with that part as one first: as a part on a face goes with its box. */
function followed(p: Part, was: Part | undefined, q0: Part | undefined, q1: Part): Part {
  const same = (a: Part, b: Part) => !!a.mirror === !!b.mirror && [0, 1, 2].every((i) => Math.abs(a.at[i] - b.at[i]) < 1e-9
    && Math.abs(a.rotate[i] - b.rotate[i]) < 1e-9 && Math.abs(a.size[i] - b.size[i]) < 1e-9);
  if (!was || !q0 || !same(p, was) || same(q0, q1) || !!q0.mirror !== !!q1.mirror
    || q0.size.some((v, i) => Math.abs(v - q1.size[i]) > 1e-9)) return p;
  const r0 = rotation(q0.rotate);
  const t = mul(rotation(q1.rotate), [r0[0], r0[3], r0[6], r0[1], r0[4], r0[7], r0[2], r0[5], r0[8]]);
  return moved(p, q0.at, [q1.at[0] - q0.at[0], q1.at[1] - q0.at[1], q1.at[2] - q0.at[2]],
    Math.abs(t[0] + t[4] + t[8] - 3) < 1e-12 ? null : t);
}

/** What of a part a click picks to mate (0.101.0): the face of its own box the spot is on, where the model is flat and
 *  square to it there - a box's side, a cylinder's top, a text's top - or, on a round part's side (a cylinder's, a
 *  cone's, a sphere), its axis ({}). null elsewhere: a wedge's slope, a rounded edge, a letter's side. */
export function mateAt(p: Part, point: Vec3, normal: Vec3): { face?: FaceName } | null {
  if (p.shape === "group") return null;
  const m = rotation(p.rotate);
  const ln: Vec3 = [m[0] * normal[0] + m[3] * normal[1] + m[6] * normal[2], m[1] * normal[0] + m[4] * normal[1]
    + m[7] * normal[2], m[2] * normal[0] + m[5] * normal[1] + m[8] * normal[2]];
  const n = unit(p.mirror ? [-ln[0], ln[1], ln[2]] : ln), l = toPart(p, point), [lo, hi] = ownBox(p);
  const round = ROUND.includes(p.shape);
  for (const face of FACES) {
    const i = faceAxis(face);
    if (p.shape === "sphere" || (round && i < 2) || lettersSide(p, face)) continue;
    const at = FACE_FRAMES[face].n[i] > 0 ? hi[i] : lo[i];
    if (Math.abs(n[i]) > 0.999 && Math.abs(l[i] - at) < 0.05) return { face };
  }
  return round ? {} : null;
}

/** What shows a part's face for a mate in the editor: the outline of its box's face - or, no face, its axis, a little
 *  longer than the part. */
export function mateMark(p: Part, face?: FaceName): Vec3[] {
  const { A, mid, half } = partFrame(p);
  const w = (x: number, y: number, z: number): Vec3 => {
    const v = apply(A, [mid[0] + x, mid[1] + y, mid[2] + z]);
    return [v[0] + p.at[0], v[1] + p.at[1], v[2] + p.at[2]];
  };
  if (!face) {
    const h = half[2] * 1.25 + 3;
    return [w(0, 0, -h), w(0, 0, h)];
  }
  const f = FACE_FRAMES[face], o = f.n.map((x, i) => x * half[i]), u = f.u.map((x, i) => x * half[i]);
  const v = f.v.map((x, i) => x * half[i]);
  const at = (a: number, b: number) => w(o[0] + a * u[0] + b * v[0], o[1] + a * u[1] + b * v[1], o[2] + a * u[2] + b * v[2]);
  return [at(-1, -1), at(1, -1), at(1, 1), at(-1, 1), at(-1, -1)];
}

/** Whether `from` is placed by the part `to` - on it, mated to it, or to one that is (the editor: not a mate the
 *  other way round). */
export function placedBy(parts: Part[], from: string, to: string): boolean {
  const all = new Map<string, Part>();
  const index = (list: Part[]) => list.forEach((p) => {
    all.set(p.id, p);
    if (p.parts) index(p.parts);
  });
  index(parts);
  const seen = new Set<string>();
  const walk = (id: string): boolean => {
    if (id === to) return true;
    if (seen.has(id)) return false;
    seen.add(id);
    const p = all.get(id);
    return !!p && [p.on?.part, ...(p.mates ?? []).map((m) => m.to.part)].some((x) => x !== undefined && walk(x));
  };
  return walk(from);
}

// ---------------------------------------------------------------- edges: fillets and chamfers (0.91.0)

/** A part's own edges: its faces (each names the edges round it) and each edge as the two faces it lies between, in
 *  the faces' order here - "top-front", never "front-top". */
export const EDGE_FACES: Partial<Record<Shape, string[]>> = {
  box: ["top", "bottom", "front", "back", "left", "right"],
  wedge: ["bottom", "left", "front", "back", "slope"],
  cylinder: ["top", "bottom"],
  cone: ["top", "bottom"],
};
export const EDGES: Partial<Record<Shape, string[]>> = {
  box: ["top-front", "top-back", "top-left", "top-right", "bottom-front", "bottom-back", "bottom-left", "bottom-right",
    "front-left", "front-right", "back-left", "back-right"],
  wedge: ["bottom-left", "bottom-front", "bottom-back", "bottom-slope", "left-front", "left-back", "left-slope",
    "front-slope", "back-slope"],
  cylinder: ["top", "bottom"],
  cone: ["top", "bottom"],
  outline: ["vertical"],
};
const VERTICAL: Partial<Record<Shape, string[]>> = {
  box: ["front-left", "front-right", "back-left", "back-right"], wedge: ["left-front", "left-back"], outline: ["vertical"],
};
export type EdgeKind = "fillet" | "chamfer";
/** Edge name -> mm: a face ("top": the edges round it), two faces ("top-front": the edge between them), "vertical",
 *  "all" - an outline's "vertical" (or "corners") are the edges at its points. */
export type EdgeSizes = Record<string, number>;
export interface EdgeSpec { kind: EdgeKind; size: number }

/** An edge name as the file keeps it ("Front_Top" -> "top-front"), or null when the shape has no such edge. */
export function edgeName(shape: Shape, raw: string): string | null {
  const edges = EDGES[shape];
  if (!edges) return null;
  const words = raw.trim().toLowerCase().split(/[\s_-]+/).filter(Boolean);
  const key = words.join("-");
  if (key === "all" || (key === "vertical" && VERTICAL[shape])) return key;
  if (shape === "outline") return key === "corners" ? "vertical" : null;
  const faces = EDGE_FACES[shape] ?? [];
  if (words.length === 1 && faces.includes(key)) return key;
  if (words.length === 2) {
    const pair = [...words].sort((a, b) => faces.indexOf(a) - faces.indexOf(b)).join("-");
    if (edges.includes(pair)) return pair;
  }
  return null;
}

/** The edges a name stands for. */
export function edgesOf(shape: Shape, key: string): string[] {
  return (EDGES[shape] ?? []).filter((e) => covers(shape, key, e) >= 0);
}

/** How exactly a name means an edge: "all" 0, "vertical" or a face 1, the edge itself 2; -1 when it does not. */
function covers(shape: Shape, key: string, edge: string): number {
  if (key === "all") return 0;
  if (key === "vertical") return VERTICAL[shape]?.includes(edge) ? 1 : -1;
  if (key === edge) return 2;
  return edge.split("-").includes(key) ? 1 : -1;
}

/** Each of a part's edges that is rounded or cut, and how: the name that means it most exactly wins ("top-front"
 *  over "top" over "all"), a fillet over a chamfer, then the larger size. */
export function edgePlan(p: Part): Record<string, EdgeSpec> {
  const out: Record<string, EdgeSpec> = {};
  for (const edge of EDGES[p.shape] ?? []) {
    let best: (EdgeSpec & { spec: number }) | null = null;
    for (const kind of ["chamfer", "fillet"] as const) {
      for (const [key, size] of Object.entries(p[kind] ?? {})) {
        const spec = covers(p.shape, key, edge);
        if (spec < 0) continue;
        const rank = kind === "fillet" ? 1 : 0, was = best?.kind === "fillet" ? 1 : 0;
        if (!best || spec > best.spec || (spec === best.spec && (rank > was || (rank === was && size > best.size)))) {
          best = { kind, size, spec };
        }
      }
    }
    if (best) out[edge] = { kind: best.kind, size: best.size };
  }
  return out;
}

/** A polygon with corner i rounded (fillet: an arc of radius size, `quarter` straight pieces to a right angle) or cut
 *  (chamfer: size along each side) by corners[i]. A size that does not fit - half a side where the corner at its
 *  other end is treated too, nearly all of it where not - is made smaller, and `clamped` says so. */
export function roundCorners(poly: Point2[], corners: (EdgeSpec | undefined)[], quarter: number): { poly: Point2[];
  clamped: boolean; sizes: number[] } {
  const n = poly.length;
  const out: Point2[] = [];
  const sizes: number[] = poly.map(() => 0);
  let clamped = false;
  for (let i = 0; i < n; i++) {
    const c = poly[i], spec = corners[i];
    if (!spec) {
      out.push(c);
      continue;
    }
    const a = poly[(i + n - 1) % n], b = poly[(i + 1) % n];
    const lin = Math.sqrt((c[0] - a[0]) * (c[0] - a[0]) + (c[1] - a[1]) * (c[1] - a[1]));
    const lout = Math.sqrt((b[0] - c[0]) * (b[0] - c[0]) + (b[1] - c[1]) * (b[1] - c[1]));
    const din: Point2 = [(c[0] - a[0]) / lin, (c[1] - a[1]) / lin], dout: Point2 = [(b[0] - c[0]) / lout, (b[1] - c[1]) / lout];
    const turn = Math.atan2(din[0] * dout[1] - din[1] * dout[0], din[0] * dout[0] + din[1] * dout[1]);
    if (Math.abs(turn) < 1e-9) {
      out.push(c);
      continue;
    }
    const k = Math.tan(Math.abs(turn) / 2);
    let t = spec.kind === "fillet" ? spec.size * k : spec.size;
    const room = Math.min(lin * (corners[(i + n - 1) % n] ? 0.5 : 0.999), lout * (corners[(i + 1) % n] ? 0.5 : 0.999));
    if (t > room) {
      t = room;
      clamped = true;
    }
    const p1: Point2 = [c[0] - t * din[0], c[1] - t * din[1]], p2: Point2 = [c[0] + t * dout[0], c[1] + t * dout[1]];
    if (spec.kind === "chamfer") {
      sizes[i] = t;
      out.push(p1, p2);
      continue;
    }
    const r = t / k;
    sizes[i] = r;
    const side = turn > 0 ? 1 : -1;                                  // the centre is on the inside of the turn
    const o: Point2 = [p1[0] - side * r * din[1], p1[1] + side * r * din[0]];
    const a0 = Math.atan2(p1[1] - o[1], p1[0] - o[0]);
    const m = Math.max(1, Math.round((quarter * Math.abs(turn)) / (Math.PI / 2)));
    for (let j = 0; j <= m; j++) {
      const ang = a0 + (turn * j) / m;
      out.push([o[0] + r * Math.cos(ang), o[1] + r * Math.sin(ang)]);
    }
  }
  return { poly: out, clamped, sizes };
}

/** Whether a part has an edge rounded or cut. */
export const hasEdges = (p: Part) => Object.keys(edgePlan(p)).length > 0;

/** Straight pieces in a quarter circle of a part's fillets: a quarter of its sides (default 32 for a box). */
export const edgeQuarter = (p: Part) => Math.max(1, Math.round((p.sides ?? 32) / 4));

/** The prisms a box or wedge with treated edges is the intersection of: each a polygon in a plane (axes u and v),
 *  extruded `length` along u x v and centred on the part's middle. A box's: its outline seen along each axis with
 *  the corners of the edges along that axis treated. A wedge's: its triangle seen from the side, and three slabs as
 *  deep as the wedge under its bottom, left end and slope, their front and back edges treated. Only those with an
 *  edge treated - the first is always there. */
export function edgePrisms(p: Part): { prisms: { poly: Point2[]; u: Vec3; v: Vec3; length: number }[]; clamped: boolean;
  balls: { centre: Vec3; corner: Vec3; r: number }[] } {
  const plan = edgePlan(p);
  const q = edgeQuarter(p);
  const [sx, sy, sz] = p.size;
  const x = sx / 2, y = sy / 2, z = sz / 2;
  const X: Vec3 = [1, 0, 0], Y: Vec3 = [0, 1, 0], Z: Vec3 = [0, 0, 1];
  const out: { poly: Point2[]; u: Vec3; v: Vec3; length: number }[] = [];
  const got: Record<string, number> = {};                     // the fillet each edge got, after making room
  let clamped = false;
  const add = (poly: Point2[], names: (string | null)[], u: Vec3, v: Vec3, length: number, always = false) => {
    const corners = names.map((nm) => (nm ? plan[nm] : undefined));
    if (!always && !corners.some(Boolean)) return;
    const r = roundCorners(poly, corners, q);
    clamped ||= r.clamped;
    names.forEach((nm, i) => { if (nm && plan[nm]?.kind === "fillet") got[nm] = r.sizes[i]; });
    out.push({ poly: r.poly, u, v, length });
  };
  // A box's corner where three fillets of one size meet is a ball's eighth (as CAD makes it), not where the three
  // rounded prisms cross: the build cuts the cube of that size at the corner and puts the ball's piece there.
  const balls: { centre: Vec3; corner: Vec3; r: number }[] = [];
  const corners = () => {
    for (const a of [-1, 1]) for (const b of [-1, 1]) for (const c of [-1, 1]) {
      const ud = c > 0 ? "top" : "bottom", fb = b < 0 ? "front" : "back", lr = a < 0 ? "left" : "right";
      const r = got[`${ud}-${fb}`];
      if (!r || got[`${ud}-${lr}`] !== r || got[`${fb}-${lr}`] !== r) continue;
      balls.push({ centre: [a * (x - r), b * (y - r), c * (z - r)], corner: [a * x, b * y, c * z], r });
    }
  };
  if (p.shape === "box") {
    add([[-x, -y], [x, -y], [x, y], [-x, y]], ["front-left", "front-right", "back-right", "back-left"], X, Y, sz);
    add([[-y, -z], [y, -z], [y, z], [-y, z]], ["bottom-front", "bottom-back", "top-back", "top-front"], Y, Z, sx);
    add([[-z, -x], [z, -x], [z, x], [-z, x]], ["bottom-left", "top-left", "top-right", "bottom-right"], Z, X, sy);
    corners();
  } else if (p.shape === "wedge") {
    const len = Math.sqrt(sx * sx + sz * sz), deep = sx + sz;
    const n: Vec3 = [sz / len, 0, sx / len];                       // the slope's outward normal; it runs through 0,0,0
    add([[-z, -x], [z, -x], [-z, x]], ["bottom-left", "left-slope", "bottom-slope"], Z, X, sy, true);
    add([[-y, -z], [y, -z], [y, -z + deep], [-y, -z + deep]], ["bottom-front", "bottom-back", null, null], Y, Z, sx);
    add([[-x, -y], [-x + deep, -y], [-x + deep, y], [-x, y]], ["left-front", null, null, "left-back"], X, Y, sz);
    add([[-y, -deep], [y, -deep], [y, 0], [-y, 0]], [null, null, "back-slope", "front-slope"], Y, n, len);
  }
  return { prisms: out, clamped, balls };
}

/** A box whose 12 edges are all fillets of one size: rounded all round, as a box with a radius (the hull of eight
 *  balls) - that size, or 0. */
export function roundAll(p: Part): number {
  if (p.shape !== "box") return 0;
  const plan = edgePlan(p), edges = EDGES.box ?? [];
  const r = plan[edges[0]]?.size ?? 0;
  return edges.every((e) => plan[e]?.kind === "fillet" && plan[e].size === r) ? r : 0;
}

/** A cylinder's or cone's half outline (x: the radius, y: up) with its rims treated - turned round to make it. */
export function edgeProfile(p: Part): { poly: Point2[]; clamped: boolean } {
  const plan = edgePlan(p);
  const r = p.size[0] / 2, h = p.size[2] / 2, top = p.shape === "cone" ? r * (p.top ?? 0) : r;
  if (top > 1e-9) return roundCorners([[0, -h], [r, -h], [top, h], [0, h]], [undefined, plan.bottom, plan.top, undefined], edgeQuarter(p));
  return roundCorners([[0, -h], [r, -h], [0, h]], [undefined, plan.bottom, undefined], edgeQuarter(p));
}

/** Where a point in the design is on a part (its copy `copy`): in the part's own frame, before it is mirrored, turned
 *  and moved. */
export function toPart(p: Part, v: Vec3, copy = 0): Vec3 {
  let w: Vec3 = v;
  if (copy && p.repeat) {
    const r = p.repeat;
    if (r.step) w = [v[0] - r.step[0] * copy, v[1] - r.step[1] * copy, v[2] - r.step[2] * copy];
    else {
      const [ax, ay] = repeatAround(p);
      const t = (-repeatStep(r) * copy * Math.PI) / 180, c = Math.cos(t), s = Math.sin(t);
      w = [ax + c * (v[0] - ax) - s * (v[1] - ay), ay + s * (v[0] - ax) + c * (v[1] - ay), v[2]];
    }
  }
  const m = rotation(p.rotate);
  const d: Vec3 = [w[0] - p.at[0], w[1] - p.at[1], w[2] - p.at[2]];
  const l: Vec3 = [m[0] * d[0] + m[3] * d[1] + m[6] * d[2], m[1] * d[0] + m[4] * d[1] + m[7] * d[2], m[2] * d[0] + m[5] * d[1] + m[8] * d[2]];
  return p.mirror ? [-l[0], l[1], l[2]] : l;
}

/** Where a point of a part's own frame is in the design - toPart's other way (0.101.0). */
export function fromPart(p: Part, l: Vec3): Vec3 {
  const w = apply(rotation(p.rotate), p.mirror ? [-l[0], l[1], l[2]] : l);
  return [w[0] + p.at[0], w[1] + p.at[1], w[2] + p.at[2]];
}

/** A box's or wedge's edges as straight lines in its own frame. */
function edgeSegments(p: Part): [string, Vec3, Vec3][] {
  const [sx, sy, sz] = p.size;
  const x = sx / 2, y = sy / 2, z = sz / 2;
  if (p.shape === "box") {
    const c = (a: number, b: number, d: number): Vec3 => [a * x, b * y, d * z];
    return [["top-front", c(-1, -1, 1), c(1, -1, 1)], ["top-back", c(-1, 1, 1), c(1, 1, 1)],
      ["top-left", c(-1, -1, 1), c(-1, 1, 1)], ["top-right", c(1, -1, 1), c(1, 1, 1)],
      ["bottom-front", c(-1, -1, -1), c(1, -1, -1)], ["bottom-back", c(-1, 1, -1), c(1, 1, -1)],
      ["bottom-left", c(-1, -1, -1), c(-1, 1, -1)], ["bottom-right", c(1, -1, -1), c(1, 1, -1)],
      ["front-left", c(-1, -1, -1), c(-1, -1, 1)], ["front-right", c(1, -1, -1), c(1, -1, 1)],
      ["back-left", c(-1, 1, -1), c(-1, 1, 1)], ["back-right", c(1, 1, -1), c(1, 1, 1)]];
  }
  if (p.shape === "wedge") {
    const A: Vec3 = [-x, -y, -z], B: Vec3 = [x, -y, -z], C: Vec3 = [x, y, -z], D: Vec3 = [-x, y, -z], E: Vec3 = [-x, -y, z],
      F: Vec3 = [-x, y, z];
    return [["bottom-left", A, D], ["bottom-front", A, B], ["bottom-back", D, C], ["bottom-slope", B, C],
      ["left-front", A, E], ["left-back", D, F], ["left-slope", E, F], ["front-slope", E, B], ["back-slope", F, C]];
  }
  return [];
}

/** Where edges are in the design (0.92.0, to draw them picked): lines through each - a box's or wedge's straight,
 *  a cylinder's or cone's rims round, an outline's corners upright - on the part and on each of its copies. */
export function edgeLines(p: Part, names: string[]): Vec3[][] {
  const [sx, sy, sz] = p.size;
  const local: Vec3[][] = [];
  if (p.shape === "box" || p.shape === "wedge") {
    for (const [name, a, b] of edgeSegments(p)) if (names.includes(name)) local.push([a, b]);
  } else if (p.shape === "cylinder" || p.shape === "cone") {
    const n = p.sides ?? DEFAULT_SIDES;
    for (const [name, z, k] of [["bottom", -sz / 2, 1], ["top", sz / 2, p.shape === "cone" ? p.top ?? 0 : 1]] as const) {
      if (!names.includes(name) || k <= 0) continue;
      const ring: Vec3[] = [];
      for (let i = 0; i <= n; i++) {
        const t = (2 * Math.PI * i) / n;
        ring.push([(sx / 2) * k * Math.cos(t), (sy / 2) * k * Math.sin(t), z]);
      }
      local.push(ring);
    }
  } else if (p.shape === "outline" && names.includes("vertical")) {
    for (const poly of outlinePolygons(p) ?? []) for (const [x, y] of poly) local.push([[x, y, -sz / 2], [x, y, sz / 2]]);
  }
  return placed(p, local);
}

/** Points in a part's own frame where they are in the design: mirrored, turned and moved as the part is - each list
 *  once on the part and once on each of its copies. */
function placed(p: Part, local: Vec3[][]): Vec3[][] {
  const m = rotation(p.rotate);
  const place = (v: Vec3): Vec3 => {
    const w = apply(m, [p.mirror ? -v[0] : v[0], v[1], v[2]]);
    return [w[0] + p.at[0], w[1] + p.at[1], w[2] + p.at[2]];
  };
  const out = local.map((line) => line.map(place));
  const copies = p.repeat && p.repeat.count > 1 ? p.repeat.count : 1;
  for (let i = 1; i < copies; i++) for (const line of local) out.push(line.map((v) => copyOf(p, i, place(v))));
  return out;
}

/** The fewest names for what each edge is (0.92.0): the edges with one fillet or chamfer of one size named together -
 *  "all", "vertical", a face - where every edge a name means has it, the rest one by one. No two names of different
 *  treatments mean one edge, so it is what `plan` says, whatever wins. */
export function compactEdges(shape: Shape, plan: Record<string, EdgeSpec>): { fillet?: EdgeSizes; chamfer?: EdgeSizes } {
  const classes = new Map<string, { spec: EdgeSpec; edges: Set<string> }>();
  for (const [edge, spec] of Object.entries(plan)) {
    const key = `${spec.kind}:${spec.size}`;
    if (!classes.has(key)) classes.set(key, { spec, edges: new Set() });
    classes.get(key)!.edges.add(edge);
  }
  const names = shape === "outline" ? ["vertical"] : ["all", "vertical", ...(EDGE_FACES[shape] ?? [])];
  const out: Record<EdgeKind, EdgeSizes> = { fillet: {}, chamfer: {} };
  for (const { spec, edges } of classes.values()) {
    const covered = new Set<string>();
    for (const name of names) {
      const es = edgesOf(shape, name);
      if (!es.length || !es.every((e) => edges.has(e)) || es.every((e) => covered.has(e))) continue;
      out[spec.kind][name] = spec.size;
      es.forEach((e) => covered.add(e));
    }
    for (const e of (EDGES[shape] ?? []).filter((x) => edges.has(x) && !covered.has(x))) out[spec.kind][e] = spec.size;
  }
  const some = (m: EdgeSizes) => (Object.keys(m).length ? m : undefined);
  return { fillet: some(out.fillet), chamfer: some(out.chamfer) };
}

/** A part with some of its edges given a fillet or chamfer - or none (spec null) - the rest as they were. */
export function treatEdges(p: Part, edges: string[], spec: EdgeSpec | null): Part {
  const plan = edgePlan(p);
  for (const e of edges) {
    if (spec) plan[e] = spec;
    else delete plan[e];
  }
  const { fillet, chamfer } = compactEdges(p.shape, plan);
  return { ...p, fillet, chamfer };
}

/** The part's own edge nearest a point (the editor's lines), with how far it is (mm) - null for a shape without
 *  named edges. Measured to the lines `edgeLines` draws - a rim round, an outline's corners, every copy - so a click
 *  on a cylinder's top far from its rim is far from the edge (0.94.0: a click near a line picks it) - and the point
 *  on it nearest. */
export function nearestEdge(p: Part, v: Vec3): { edge: string; distance: number; point: Vec3 } | null {
  const names = EDGES[p.shape];
  if (!names) return null;
  let best: { edge: string; distance: number; point: Vec3 } | null = null;
  for (const name of names) {
    for (const line of edgeLines(p, [name])) {
      for (let i = 1; i < line.length; i++) {
        const a = line[i - 1], b = line[i];
        const ab: Vec3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
        const len2 = ab[0] * ab[0] + ab[1] * ab[1] + ab[2] * ab[2];
        const t = len2 > 0 ? Math.max(0, Math.min(1, ((v[0] - a[0]) * ab[0] + (v[1] - a[1]) * ab[1] + (v[2] - a[2]) * ab[2]) / len2)) : 0;
        const point: Vec3 = [a[0] + t * ab[0], a[1] + t * ab[1], a[2] + t * ab[2]];
        const d = Math.hypot(v[0] - point[0], v[1] - point[1], v[2] - point[2]);
        if (!best || d < best.distance) best = { edge: name, distance: d, point };
      }
    }
  }
  return best;
}

/** The point of some lines (each a row of points, straight between them) nearest a ray - from `o` along the unit
 *  `dir` - and how far it is from the ray (0.95.0): seen from a camera at `o`, what is a few pixels from the pointer
 *  on the screen, whichever way the face under it is turned. */
export function nearestToRay(lines: Vec3[][], o: Vec3, dir: Vec3): { distance: number; point: Vec3 } | null {
  const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const clamp = (x: number) => Math.max(0, Math.min(1, x));
  let best: { distance: number; point: Vec3 } | null = null;
  for (const line of lines) {
    for (let i = 1; i < line.length; i++) {
      const a = line[i - 1], b = line[i];
      const e: Vec3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], w: Vec3 = [o[0] - a[0], o[1] - a[1], o[2] - a[2]];
      const B = dot(dir, e), C = dot(e, e), D = dot(dir, w), E = dot(e, w);
      const den = C - B * B;                                             // 0: the piece lies along the ray
      let t = C > 1e-12 && den > 1e-12 * C ? clamp((E - B * D) / den) : 0;
      let u = t * B - D;                                                 // how far along the ray
      if (u < 0) {
        u = 0;
        t = C > 1e-12 ? clamp(E / C) : 0;
      }
      const point: Vec3 = [a[0] + t * e[0], a[1] + t * e[1], a[2] + t * e[2]];
      const d = Math.hypot(point[0] - o[0] - u * dir[0], point[1] - o[1] - u * dir[1], point[2] - o[2] - u * dir[2]);
      if (!best || d < best.distance) best = { distance: d, point };
    }
  }
  return best;
}

/** How long a part's edge is (0.94.0, the Lines tab's Resize): a box's or wedge's straight edge, its length; a
 *  cylinder's or cone's rim, its diameter (across X); an outline's corners, its thickness. Null: nothing to size. */
export function edgeLength(p: Part, edge: string): { length: number; what: "Length" | "Diameter" } | null {
  const [sx, , sz] = p.size;
  if (p.shape === "box" || p.shape === "wedge") {
    const seg = edgeSegments(p).find(([name]) => name === edge);
    if (!seg) return null;
    const [, a, b] = seg;
    return { length: Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]), what: "Length" };
  }
  if (p.shape === "cylinder" || p.shape === "cone") {
    const k = edge === "bottom" ? 1 : edge === "top" ? (p.shape === "cone" ? p.top ?? 0 : 1) : 0;
    return k > 0 ? { length: sx * k, what: "Diameter" } : null;
  }
  if (p.shape === "outline" && edge === "vertical") return { length: sz, what: "Length" };
  return null;
}

/** The part with one edge made `length` long (0.94.0): the size along it - a slope keeps its angle, a rim its round
 *  (a cone's other rim keeps its own diameter), an outline's corners are its thickness - its bottom staying where it
 *  was, its middle too across. A string: why not. */
export function withEdgeLength(p: Part, edge: string, length: number): Part | string {
  const now = edgeLength(p, edge);
  if (!now) return `${edge} has no length to set.`;
  if (!(length >= MIN_SIZE)) return `${fmtMm(length)} mm is too short.`;
  const size = [...p.size] as Vec3;
  let top = p.top;
  if (p.shape === "box" || p.shape === "wedge") {
    const [, a, b] = edgeSegments(p).find(([name]) => name === edge)!;
    const d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]].map(Math.abs);
    const along = d.filter((x) => x > 1e-9).length;
    if (along === 1) size[d.findIndex((x) => x > 1e-9)] = length;        // straight along X, Y or Z
    else {                                                                // a slope: its run and rise alike
      const f = length / now.length;
      for (let i = 0; i < 3; i++) if (d[i] > 1e-9) size[i] = p.size[i] * f;
    }
  } else if (p.shape === "cylinder" || p.shape === "cone") {
    if (p.shape === "cone" && edge === "top") {
      if (length > p.size[0] + 1e-9) return `The top cannot be wider than the bottom (${fmtMm(p.size[0])} mm) - turn the cone over.`;
      top = Math.min(1, length / p.size[0]);
    } else {
      const f = length / p.size[0];
      if (p.shape === "cone") top = p.top ? Math.min(1, (p.top * p.size[0]) / length) : p.top;   // the top's own diameter
      size[0] = length;
      size[1] = p.size[1] * f;
    }
  } else size[2] = length;                                                // an outline's corners: its thickness
  const next: Part = { ...p, size, top };
  const was = extent(p), then = extent(next);
  if (was && then) next.at = [p.at[0], p.at[1], p.at[2] + was[0][2] - then[0][2]];   // its bottom stays
  return next;
}

const fmtMm = (v: number) => String(Math.round(v * 100) / 100);

// ---------------------------------------------------------------- a hole's lines (0.95.0)

const FACE_OUT: Record<string, Vec3> = { top: [0, 0, 1], bottom: [0, 0, -1], front: [0, -1, 0], back: [0, 1, 0],
  left: [-1, 0, 0], right: [1, 0, 0] };

const unit = (v: Vec3): Vec3 => {
  const l = Math.hypot(v[0], v[1], v[2]);
  return l > 1e-12 ? [v[0] / l, v[1] / l, v[2] / l] : [0, 0, 0];
};

/** Whether a point is inside outlines read EvenOdd - inside an odd number of them, as an outline part is built. */
function inOutlines(polys: number[][][], x: number, y: number): boolean {
  let inside = false;
  for (const poly of polys) {
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}

/** Points a little way (`gap` mm) off a part's edge, out between the two faces it lies between: along each line of it
 *  - a box's or wedge's edge at its eighths, a rim at eight places round it, each of an outline's corners up its
 *  height - on the part and on each copy, in the design. One list a line. */
export function edgeOutside(p: Part, edge: string, gap: number): Vec3[][] {
  const [sx, sy, sz] = p.size;
  const local: Vec3[][] = [];
  const along = (a: Vec3, b: Vec3, out: Vec3): Vec3[] => {
    const d = unit(out), pts: Vec3[] = [];
    for (let k = 1; k < 8; k++) {
      const t = k / 8;
      pts.push([a[0] + t * (b[0] - a[0]) + gap * d[0], a[1] + t * (b[1] - a[1]) + gap * d[1],
        a[2] + t * (b[2] - a[2]) + gap * d[2]]);
    }
    return pts;
  };
  if (p.shape === "box" || p.shape === "wedge") {
    const seg = edgeSegments(p).find(([name]) => name === edge);
    const len = Math.hypot(sx, sz) || 1;
    const out = (face: string): Vec3 => (face === "slope" ? [sz / len, 0, sx / len] : FACE_OUT[face]);
    if (seg) {
      const [f, g] = edge.split("-").map(out);
      local.push(along(seg[1], seg[2], [f[0] + g[0], f[1] + g[1], f[2] + g[2]]));
    }
  } else if (p.shape === "cylinder" || p.shape === "cone") {
    const k = edge === "bottom" ? 1 : edge === "top" ? (p.shape === "cone" ? p.top ?? 0 : 1) : 0;
    if (k > 0) {
      const up = edge === "top" ? 1 : -1;
      const ring: Vec3[] = [];
      for (let i = 0; i < 8; i++) {
        const t = (2 * Math.PI * (i + 0.5)) / 8;                         // between a few-sided one's corners
        const n = unit([Math.cos(t) / Math.max(sx, 1e-9), Math.sin(t) / Math.max(sy, 1e-9), 0]);   // out of its side
        const d = unit([n[0], n[1], up]);
        ring.push([(sx / 2) * k * Math.cos(t) + gap * d[0], (sy / 2) * k * Math.sin(t) + gap * d[1], (up * sz) / 2 + gap * d[2]]);
      }
      local.push(ring);
    }
  } else if (p.shape === "outline" && edge === "vertical") {
    const polys = outlinePolygons(p) ?? [];
    for (const poly of polys) {
      for (let i = 0; i < poly.length; i++) {
        const [cx, cy] = poly[i], a = poly[(i + poly.length - 1) % poly.length], b = poly[(i + 1) % poly.length];
        const u = unit([a[0] - cx, a[1] - cy, 0]), w = unit([b[0] - cx, b[1] - cy, 0]);
        let d = unit([u[0] + w[0], u[1] + w[1], 0]);                     // into the corner's angle ...
        if (!d[0] && !d[1]) d = [-u[1], u[0], 0];                         // (a straight one: off its side)
        if (inOutlines(polys, cx + (gap / 2) * d[0], cy + (gap / 2) * d[1])) d = [-d[0], -d[1], 0];   // ... or out of it
        local.push(along([cx, cy, -sz / 2], [cx, cy, sz / 2], d));
      }
    }
  }
  return placed(p, local);
}

/** How far off its edges `holeEdges` looks: 0.2 mm, less on a small part. */
function edgeGap(p: Part): number {
  return Math.min(0.2, 0.1 * Math.min(2, ...p.size.filter((x) => x > 0)));
}

/** Whether a point is inside a closed mesh: rays from it along +X, +Y and +Z each cross the mesh an odd number of times
 *  when it is. One through a line or a corner between two triangles can count wrong, so the three vote. */
export function insideMesh(m: MeshData, v: Vec3): boolean {
  const P = m.positions, T = m.triangles;
  const q = [v[0] + 1.37e-5, v[1] + 2.91e-5, v[2] + 4.73e-5];          // off the round numbers corners are at
  let votes = 0;
  for (let axis = 0; axis < 3; axis++) {
    const i = (axis + 1) % 3, j = (axis + 2) % 3, qi = q[i], qj = q[j], qa = q[axis];
    let n = 0;
    for (let t = 0; t < T.length; t += 3) {
      const a = T[t] * 3, b = T[t + 1] * 3, c = T[t + 2] * 3;
      const ai = P[a + i] - qi, bi = P[b + i] - qi, ci = P[c + i] - qi;
      if ((ai > 0 && bi > 0 && ci > 0) || (ai < 0 && bi < 0 && ci < 0)) continue;
      const aj = P[a + j] - qj, bj = P[b + j] - qj, cj = P[c + j] - qj;
      if ((aj > 0 && bj > 0 && cj > 0) || (aj < 0 && bj < 0 && cj < 0)) continue;
      const wa = bi * cj - bj * ci, wb = ci * aj - cj * ai, wc = ai * bj - aj * bi;   // the ray's side of each edge
      if (!((wa > 0 && wb > 0 && wc > 0) || (wa < 0 && wb < 0 && wc < 0))) continue;
      if ((wa * P[a + axis] + wb * P[b + axis] + wc * P[c + axis]) / (wa + wb + wc) > qa) n++;
    }
    if (n % 2) votes++;
    if (votes === 2 || votes + 2 - axis < 2) break;                       // decided
  }
  return votes >= 2;
}

/** A cutter's edges (a hole's, or a part's in a hole group) split: those inside the solid it cuts - solid just off the
 *  edge somewhere along each of its lines: the inside corners of what it cut, which a fillet or chamfer on it rounds
 *  or cuts as it should - and the open ones, at an opening or out in the air, where rounding the cutter would leave a
 *  lip round the opening (or do nothing). */
export function holeEdges(p: Part, edges: string[], solid: MeshData): { inside: string[]; open: string[] } {
  const gap = edgeGap(p), inside: string[] = [], open: string[] = [];
  for (const e of edges) {
    const lines = edgeOutside(p, e, gap);
    (lines.every((pts) => pts.some((v) => insideMesh(solid, v))) ? inside : open).push(e);
  }
  return { inside, open };
}

function readEdgeSizes(v: unknown, part: Part, kind: EdgeKind, problems: string[]): EdgeSizes | undefined {
  if (v === undefined) return undefined;
  if (!v || typeof v !== "object" || Array.isArray(v)) {
    problems.push(`${part.id}: "${kind}" is { edge: mm } - left out.`);
    return undefined;
  }
  if (!EDGES[part.shape]) {
    problems.push(`${part.id}: a ${part.shape} has no edges of its own to ${kind} - left out.`);
    return undefined;
  }
  const out: EdgeSizes = {};
  for (const [key, size] of Object.entries(v as Record<string, unknown>)) {
    const name = edgeName(part.shape, key);
    if (!name || !num(size) || size <= 0) {
      problems.push(`${part.id}: no ${kind} "${key}" - ${edgeChoices(part.shape)}, each with its size in mm.`);
      continue;
    }
    out[name] = Math.max(out[name] ?? 0, size);
  }
  return Object.keys(out).length ? out : undefined;
}

/** What the names of a shape's edges are, said for a problem and in hub3d format. */
export function edgeChoices(shape: Shape): string {
  if (shape === "outline") return "an outline's edges: vertical (its corners), all";
  if (shape === "cylinder" || shape === "cone") return `a ${shape}'s edges: top, bottom, all`;
  const faces = EDGE_FACES[shape] ?? [];
  return `a ${shape}'s edges: ${faces.join(", ")} (the edges round that face), vertical, all, or two faces: `
    + (shape === "box" ? "top-front" : "left-slope");
}

// ---------------------------------------------------------------- text

/** A font as tools/make_3d_font.py flattens it: per character its advance and its outlines, in font units. */
export interface FontData {
  name?: string;
  unitsPerEm: number;
  ascender: number;
  descender: number;
  capHeight?: number;
  glyphs: Record<string, { a: number; c: number[][] }>;
}

/** fonts/index.json: each family's font files by style (a family may have only "regular"). */
export interface FontIndex {
  default: string;
  families: Record<string, { name: string; regular: string; bold?: string; italic?: string; "bold-italic"?: string }>;
}

/** The fonts a build draws text with: the index, and the font files loaded so far (by their names in it). */
export interface Fonts { index: FontIndex; loaded: Record<string, FontData> }

/** The font file a text part is drawn in, and how far its letters lean (a family without an italic: SLANT). A
 *  family without a bold draws its regular. */
export function fontFile(p: Part, index: FontIndex): { file: string; slant: number } {
  const fam = index.families[p.font ?? ""] ?? index.families[index.default];
  const style = p.bold && p.italic ? "bold-italic" : p.bold ? "bold" : p.italic ? "italic" : "regular";
  const file = fam[style];
  if (file) return { file, slant: 0 };
  return { file: (p.bold && fam.bold) || fam.regular, slant: p.italic ? SLANT : 0 };
}

/** A text part's font and slant - null while its file is not loaded. */
export function textFont(p: Part, fonts: Fonts): { font: FontData; slant: number } | null {
  const { file, slant } = fontFile(p, fonts.index);
  const font = fonts.loaded[file];
  return font ? { font, slant } : null;
}

/** A text's letters in font units, laid out: each letter after the last by its advance (times letter_spacing between
 *  letters), each line below the last by the font's line height (ascender to descender, times line_spacing), the
 *  lines centred on x 0 - or starting or ending there (align) - and slanted by `slant`. Each letter's character
 *  and outlines (a space has none). */
export function textGlyphs(p: Part, font: FontData, slant = 0): { ch: string; contours: number[][][] }[] {
  const out: { ch: string; contours: number[][][] }[] = [];
  const ls = p.letter_spacing ?? 1;
  const step = (font.ascender - font.descender) * (p.line_spacing ?? 1);
  (p.text ?? "").split("\n").forEach((line, row) => {
    const placed: [string, number[][], number][] = [];
    let x = 0, last = 0;
    for (const ch of line) {
      const g = font.glyphs[ch] ?? font.glyphs["?"];
      if (!g) continue;
      placed.push([ch, g.c, x]);
      last = g.a;
      x += g.a * ls;
    }
    const width = placed.length ? x - last * ls + last : 0;
    const off = p.align === "left" ? 0 : p.align === "right" ? -width : -width / 2;
    const y = -row * step;
    for (const [ch, cs, gx] of placed) {
      const contours: number[][][] = [];
      for (const c of cs) {
        const poly: number[][] = [];
        for (let i = 0; i < c.length; i += 2) poly.push([c[i] + gx + off + slant * c[i + 1], c[i + 1] + y]);
        contours.push(poly);
      }
      out.push({ ch, contours });
    }
  });
  return out;
}

/** A text's outlines in font units, laid out (textGlyphs, all its letters'). */
export function textContours(p: Part, font: FontData, slant = 0): number[][][] {
  return textGlyphs(p, font, slant).flatMap((g) => g.contours);
}

function inkBox(contours: number[][][]): [number, number, number, number] | null {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const c of contours) for (const [x, y] of c) {
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  return x1 > x0 && y1 > y0 ? [x0, y0, x1, y1] : null;
}

/** mm per font unit across and up, for letters `w` x `h` font units in a part of `size`: a 0 follows the other. */
function textScale(size: Vec3, w: number, h: number): [number, number] {
  const ky = size[1] > 0 ? size[1] / h : size[0] / w;
  return [size[0] > 0 ? size[0] / w : ky, ky];
}

/** The outlines in mm, scaled into the part's size and centred on its 0,0. null when the text draws nothing. */
export function textPolygons(p: Part, font: FontData, slant = 0): number[][][] | null {
  const contours = textContours(p, font, slant);
  const b = inkBox(contours);
  if (!b) return null;
  const [x0, y0, x1, y1] = b;
  const [kx, ky] = textScale(p.size, x1 - x0, y1 - y0);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  return contours.map((c) => c.map(([x, y]) => [(x - cx) * kx, (y - cy) * ky]));
}

/** What a text part comes out as: its width and height in mm (its 0s worked out), its lines, how tall its capitals
 *  are and the font's name. null while its font is not loaded, or when it draws nothing. */
export function textMeasure(p: Part, fonts: Fonts): { width: number; height: number; lines: number; capitals: number;
  font: string } | null {
  const t = textFont(p, fonts);
  const b = t && inkBox(textContours(p, t.font, t.slant));
  if (!t || !b) return null;
  const w = b[2] - b[0], h = b[3] - b[1];
  const [kx, ky] = textScale(p.size, w, h);
  return { width: w * kx, height: h * ky, lines: (p.text ?? "").split("\n").length,
    capitals: (t.font.capHeight ?? 0.7 * t.font.unitsPerEm) * ky, font: t.font.name ?? fontFile(p, fonts.index).file };
}

/** A text part sized so its capitals are `mm` tall (the editor's "Letters"): its size scaled, its 0s kept. */
export function withCapitals(p: Part, mm: number, fonts: Fonts): Part {
  const t = textMeasure(p, fonts);
  if (!t || t.capitals <= 0 || mm <= 0) return p;
  const f = mm / t.capitals;
  return { ...p, size: [p.size[0] * f, p.size[1] * f, p.size[2]] };
}

/** A text part as one text part per letter, each just where it was - as SolidWorks dissolves sketch text (0.92.0, the
 *  editor's Letters apart): the font, style, height, thickness, turn, mirror and colour kept, each letter's width
 *  following it (unless the text was stretched: then its own), a repeat's centre kept. null while its font is not
 *  loaded or when it draws nothing. */
export function lettersApart(p: Part, fonts: Fonts, seen: Set<string>): Part[] | null {
  const t = textFont(p, fonts);
  if (!t) return null;
  const glyphs = textGlyphs(p, t.font, t.slant);
  const all = inkBox(glyphs.flatMap((g) => g.contours));
  if (!all) return null;
  const [kx, ky] = textScale(p.size, all[2] - all[0], all[3] - all[1]);
  const cx = (all[0] + all[2]) / 2, cy = (all[1] + all[3]) / 2;
  const m = rotation(p.rotate);
  const stretched = Math.abs(kx - ky) > 1e-9 * Math.max(kx, ky);
  const out: Part[] = [];
  for (const g of glyphs) {
    const b = inkBox(g.contours);
    if (!b) continue;
    const lx = ((b[0] + b[2]) / 2 - cx) * kx, ly = ((b[1] + b[3]) / 2 - cy) * ky;
    const w = apply(m, [p.mirror ? -lx : lx, ly, 0]);
    const id = freshId(seen);
    seen.add(id);
    const letter: Part = { id, name: g.ch, shape: "text", text: g.ch,
      size: [stretched ? (b[2] - b[0]) * kx : 0, (b[3] - b[1]) * ky, p.size[2]],
      at: [p.at[0] + w[0], p.at[1] + w[1], p.at[2] + w[2]], rotate: [...p.rotate] as Vec3 };
    if (p.font) letter.font = p.font;
    if (p.bold) letter.bold = true;
    if (p.italic) letter.italic = true;
    if (p.mirror) letter.mirror = true;
    if (p.hole) letter.hole = true;
    if (p.hidden) letter.hidden = true;
    if (p.color) letter.color = p.color;
    if (p.repeat) letter.repeat = p.repeat.step ? { ...p.repeat } : { ...p.repeat, around: repeatAround(p) };
    out.push(letter);
  }
  return out.length ? out : null;
}

/** A text part with `patch` (its letters, font, style or spacing) whose capitals stay as tall as they were - the
 *  text grows or shrinks around them, as in any text tool - unless it was stretched to a box of its own (a width
 *  and a height that are not the letters' own, to 0.05 mm): then the new letters fill that box. */
export function retext(p: Part, patch: Partial<Part>, fonts: Fonts): Part {
  const next: Part = { ...p, ...patch };
  const was = textMeasure(p, fonts);
  if (!was) return next;
  const own = (q: Part) => textMeasure({ ...q, size: [0, q.size[1], q.size[2]] }, fonts)?.width;
  const boxed = p.size[0] > 0 && p.size[1] > 0;
  if (boxed && Math.abs(p.size[0] - (own(p) ?? p.size[0])) >= 0.05) return next;
  const out = withCapitals(next, was.capitals, fonts);
  const w = boxed ? own(out) : undefined;
  return w !== undefined ? { ...out, size: [w, out.size[1], out.size[2]] } : out;
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
  const k = meshScale(p.size, [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]]);
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
  fonts: Fonts;
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

  // A part with its own edges rounded or cut (0.91.0): a box or a wedge the intersection of its prisms, a cylinder or
  // a cone its treated half outline turned round, an outline its corners treated (in "outline" below).
  const tooBig = (p: Part) => problems.push(`${p.id}: a fillet or chamfer larger than its edges have room for was made smaller.`);
  const prism = (poly: Point2[], u: Vec3, v: Vec3, length: number): Obj => {
    const e: Vec3 = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    const m = keep(keep(new CrossSection([poly], "NonZero")).extrude(length));
    return keep(m.transform([u[0], u[1], u[2], 0, v[0], v[1], v[2], 0, e[0], e[1], e[2], 0,
      (-e[0] * length) / 2, (-e[1] * length) / 2, (-e[2] * length) / 2, 1]));
  };
  const edged = (p: Part): Obj | null => {
    const all = roundAll(p);
    if (all) {
      const [sx, sy, sz] = p.size;
      const r = Math.min(all, (Math.min(sx, sy, sz) / 2) * 0.999);
      if (r < all) tooBig(p);
      const balls: Obj[] = [];
      for (const a of [-1, 1]) for (const b of [-1, 1]) for (const c of [-1, 1]) {
        balls.push(keep(keep(Manifold.sphere(r, 4 * edgeQuarter(p))).translate([a * (sx / 2 - r), b * (sy / 2 - r), c * (sz / 2 - r)])));
      }
      return keep(Manifold.hull(balls));
    }
    if (p.shape === "cylinder" || p.shape === "cone") {
      const { poly, clamped } = edgeProfile(p);
      if (clamped) tooBig(p);
      const m = keep(keep(new CrossSection([poly], "NonZero")).revolve(p.sides ?? DEFAULT_SIDES, 360));
      return Math.abs(p.size[1] - p.size[0]) > 1e-9 ? keep(m.scale([1, p.size[1] / p.size[0], 1])) : m;
    }
    const { prisms, clamped, balls } = edgePrisms(p);
    if (clamped) tooBig(p);
    let m: Obj | null = null;
    for (const pr of prisms) {
      const one = prism(pr.poly, pr.u, pr.v, pr.length);
      m = m ? done(keep(m.intersect(one))) : one;
    }
    for (const b of balls) {
      if (!m) break;
      const lo = b.centre.map((v, i) => Math.min(v, b.corner[i])), hi = b.centre.map((v, i) => Math.max(v, b.corner[i]));
      const cube = keep(keep(Manifold.cube([hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]], false)).translate(lo));
      const ball = keep(keep(Manifold.sphere(b.r, 4 * edgeQuarter(p))).translate(b.centre));
      m = done(keep(keep(m.subtract(cube)).add(done(keep(ball.intersect(cube))))));
    }
    return m;
  };

  const shape = (p: Part): Obj | null => {
    const [sx, sy, sz] = p.size;
    const sides = p.sides ?? DEFAULT_SIDES;
    if ((p.fillet || p.chamfer) && !p.radius && p.shape !== "outline" && hasEdges(p)) return edged(p);
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
        const t = textFont(p, kit.fonts);
        if (!t) {
          problems.push(`${p.id}: its font (${fontFile(p, kit.fonts.index).file}) is not loaded yet.`);
          return null;
        }
        const polys = textPolygons(p, t.font, t.slant);
        if (!polys) return null;
        return keep(keep(keep(new CrossSection(polys, "NonZero")).extrude(sz)).translate([0, 0, -sz / 2]));
      }
      case "outline": {                    // EvenOdd: a hole is a hole whichever way round its points run
        let polys = outlinePolygons(p);
        if (!polys) return null;
        const spec = edgePlan(p).vertical;
        if (spec) {
          const q = edgeQuarter(p);
          let clamped = false;
          polys = polys.map((poly) => {
            const r = roundCorners(poly as Point2[], poly.map(() => spec), q);
            clamped ||= r.clamped;
            return r.poly;
          });
          if (clamped) tooBig(p);
        }
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
        const k = meshScale(p.size, [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]]);   // its 0s from the file (0.104.0)
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
