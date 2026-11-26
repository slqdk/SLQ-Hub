// The 3D editor (0.87.0): a design (.3d.json) in the chat's or the project's folder, edited Tinkercad-style beside
// the chat - shapes, holes, groups, moving, turning, sizing, an imported STL - on the chosen printer's plate. The
// assistant works on the same file: it reads it with every message while the editor is open, writes it with its
// file tools (and checks it with `hub3d design`), and what it writes shows here. "Send to chat" puts a spot on the model
// into the message. Loaded only when opened: three.js and manifold-3d (WebAssembly) are big.
import { createContext, Fragment, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type ReactNode } from "react";
import * as THREE from "three";
import Module from "manifold-3d/manifold";
import wasmUrl from "manifold-3d/manifold.wasm?url";
import { AlignCenter, AlignLeft, AlignRight, ArrowDownToLine, ArrowDownUp, Bold, Box, ChevronDown, CircleDashed, CircleDot,
  Columns3, Cone, Copy, Crosshair, Cylinder, Download, Eraser, Eye, EyeOff, FlipHorizontal2, FlipVertical2, Group, Italic,
  ListTree, Maximize2, PencilRuler, PenLine, RectangleHorizontal, Circle, ArrowUpFromLine,
  MessageSquareShare, Minimize2, MousePointer2, MousePointerClick, Move3d, PanelBottom, Printer as PrinterIcon,
  Redo2, Repeat2, Rotate3d, RotateCcw, RotateCw, Ruler, Scaling, Shapes, Split, SquareDashed, SquareMousePointer,
  SquareRoundCorner, Stamp, Trash2,
  TriangleAlert, Triangle, Type, Undo2, Ungroup, Unlink, Upload, X } from "lucide-react";
import fontIndex from "./fonts/index.json";
import { useApp } from "../state";
import { ConfirmButton, MenuButton } from "../components/ui";
import type { DesignFolder } from "./folder";
import { allIds, build, buildPart, COLORS, edgeLength, edgeLines, edgePlan, EDGES, edgesOf, extent, extentOf, FACE_FRAMES,
  faceAngle, faceAt, faceFrame, faceMarks, FONT_FAMILIES, fontFile, follows, freshId, holeEdges, joined, lettersApart,
  MAX_COPIES, MIN_SIZE, mirrorPart, nearestEdge, nearestToRay, newDesign, onFace, partSize, placeOn, readDesign, repeatAround, respot,
  retext, settle, SPACING, spotAt, stl, textGlyphs, textMeasure, threeMf, transformPart, treatEdges, turned90, withCapitals,
  withEdgeLength, writeDesign, type Align, type Anchor, type Box3, type Built, type Design, type EdgeKind, type FaceName,
  type FontData, type FontIndex, type Fonts, type Kit, type Mat3, type MeshData, type Part, type Point2, type Repeat,
  type Shape, type Vec3, type Wasm } from "./geometry";
import { baseName, dirOf, fmt, joinPath, MESH_FILE, pointMark, relativePath } from "./files";
import { parseMesh } from "./meshio";
import { lastPrinter, PRINTERS, printerOf, rememberPrinter, type Printer } from "./printers";
import { centre, copy, cuts, cutters, find, group, insertAfter, label, onCopies, pick, remove, replaceWith, riders, ungroup,
  update } from "./tree";
import { distanceTo, loopOf, planeFrame, planeName, rayOnPlane, sketchParts, sketchRegions, snapPoints, strokesOf, to3d,
  type PlaneFrame, type SketchPlane, type SketchShape } from "./sketch";
import { Viewport, type Hit, type ViewName } from "./Viewport";

// Select (0.96.0): a click only picks - no handles, nothing moves; Move, Turn and Size show their handles.
type Mode = "select" | "translate" | "rotate" | "scale";
type Status = "loading" | "new" | "saved" | "saving" | "unsaved" | "error";

// The fonts (0.89.0): the index now, each font's polygons when a design first uses it (they are 130-330 kB each).
const FONT_URLS = import.meta.glob<string>("./fonts/*.json", { query: "?url", import: "default", eager: true });
const FONTS: Fonts = { index: fontIndex as FontIndex, loaded: {} };
const fontLoads = new Map<string, Promise<void>>();
const fontFailed = new Set<string>();

function loadFont(file: string): Promise<void> {
  let p = fontLoads.get(file);
  if (!p) {
    const url = FONT_URLS[`./fonts/${file}.json`];
    p = (url ? fetch(url).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<FontData>;
    }) : Promise.reject(new Error("no such font"))).then((f) => { FONTS.loaded[file] = f; },
      (e) => { fontFailed.add(file); throw e; });
    fontLoads.set(file, p);
  }
  return p;
}

/** Every family's name in its own letters (0.99.0): the Font box's list from one small file, not every font's. */
let previews: Record<string, FontData> | null = null;
let previewLoad: Promise<void> | null = null;
function loadPreviews(): Promise<void> {
  previewLoad ??= (async () => {
    const url = FONT_URLS["./fonts/previews.json"];
    const r = url ? await fetch(url) : null;
    if (r?.ok) previews = await r.json() as Record<string, FontData>;
  })().catch(() => { previewLoad = null; });
  return previewLoad;
}

/** The font files a design's texts need that are not loaded (nor failed to load). */
function fontsWanted(parts: Part[], into = new Set<string>()): Set<string> {
  for (const p of parts) {
    if (p.shape === "text") {
      const { file } = fontFile(p, FONTS.index);
      if (!FONTS.loaded[file] && !fontFailed.has(file)) into.add(file);
    }
    if (p.parts) fontsWanted(p.parts, into);
  }
  return into;
}

let kitPromise: Promise<{ wasm: Wasm; fonts: Fonts }> | null = null;
function loadKit(): Promise<{ wasm: Wasm; fonts: Fonts }> {
  const first = fontFile({ id: "", shape: "text", size: [0, 10, 2], at: [0, 0, 0], rotate: [0, 0, 0] }, FONTS.index).file;
  kitPromise ??= Promise.all([
    Module({ locateFile: () => wasmUrl }).then((w) => {
      w.setup();
      return w as Wasm;
    }),
    loadFont(first),
  ]).then(([wasm]) => ({ wasm, fonts: FONTS }));
  kitPromise.catch(() => { kitPromise = null; });       // a failed load is tried again next time
  return kitPromise;
}

const r3 = (v: number) => Math.round(v * 1000) / 1000 + 0;
const r4 = (v: number) => Math.round(v * 1e4) / 1e4 + 0;
// Moving goes in whole mm and turning in 15° steps (0.96.0: no setting - Søren: "1 mm skal være standard").
const SNAP = 1;
const GREY = "#9aa0a3";

/** A rotation as the design keeps turns (row-major). */
function quatTurn(q: THREE.Quaternion): Mat3 {
  const e = new THREE.Matrix4().makeRotationFromQuaternion(q).elements;
  return [e[0], e[4], e[8], e[1], e[5], e[9], e[2], e[6], e[10]];
}

/** A part's size, with a text's width or height worked out when it follows its letters, an outline's from its points. */
const sized = (p: Part, fonts: Fonts): Vec3 => partSize(p, fonts);

/** A part that moves like a group: its copies go with it, and it is sized the same way in every direction. */
const asGroup = (p: Part) => p.shape === "group" || !!p.repeat;

function meshSize(m: MeshData): Vec3 {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < m.positions.length; i += 3) for (let k = 0; k < 3; k++) {
    lo[k] = Math.min(lo[k], m.positions[i + k]);
    hi[k] = Math.max(hi[k], m.positions[i + k]);
  }
  return [0, 1, 2].map((k) => r3(Math.max(MIN_SIZE, hi[k] - lo[k]))) as Vec3;
}

function union(boxes: (Box3 | null)[]): Box3 | null {
  let box: Box3 | null = null;
  for (const b of boxes) {
    if (!b) continue;
    box = box ? [[0, 1, 2].map((i) => Math.min(box![0][i], b[0][i])) as Vec3, [0, 1, 2].map((i) => Math.max(box![1][i], b[1][i])) as Vec3] : b;
  }
  return box;
}

/** Whether `inner` is `outer` or one of its parts. */
function within(parts: Part[], outer: string, inner: string): boolean {
  if (outer === inner) return true;
  const f = find(parts, outer);
  return !!f?.part.parts && !!find(f.part.parts, inner);
}

/** Numbers as the file keeps them. */
function tidy(p: Part): Part {
  const out: Part = { ...p, at: p.at.map(r3) as Vec3, rotate: p.rotate.map(r4) as Vec3 };
  if (p.shape !== "group") out.size = p.size.map((v, i) => (follows(p.shape, i, v) ? 0 : Math.max(MIN_SIZE, r3(v)))) as Vec3;
  if (p.radius) out.radius = r3(p.radius);
  if (p.repeat) {
    const r = { ...p.repeat };
    if (r.around) r.around = [r3(r.around[0]), r3(r.around[1])];
    if (r.step) r.step = r.step.map(r3) as Vec3;
    if (r.angle !== undefined) r.angle = r3(r.angle);
    out.repeat = r;
  }
  if (p.parts) out.parts = p.parts.map(tidy);
  return out;
}

/** A number typed freely, applied on Enter or when it loses focus; the arrow keys step it. */
function NumInput({ value, onSet, min, step = 1, label: name }: { value: number; onSet: (v: number) => void; min?: number;
  step?: number; label?: string }) {
  const [draft, setDraft] = useState(fmt(value));
  useEffect(() => setDraft(fmt(value)), [value]);
  const apply = (raw = draft) => {
    const v = Number(raw.replace(",", "."));
    if (!Number.isFinite(v) || (min !== undefined && v < min)) setDraft(fmt(value));
    else if (Math.abs(v - value) > 1e-9) onSet(v);
  };
  return (
    <input className="input mono" inputMode="decimal" value={draft} aria-label={name} onChange={(e) => setDraft(e.target.value)}
      onBlur={() => apply()}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          const v = r3(value + (e.key === "ArrowUp" ? 1 : -1) * step * (e.shiftKey ? 10 : 1));
          if (min === undefined || v >= min) onSet(v);
        }
      }} />
  );
}

/** A number field with its name over it. */
function Num({ value, onSet, min, label: name, unit = "mm", step = 1 }: { value: number; onSet: (v: number) => void;
  min?: number; label: string; unit?: string; step?: number }) {
  return (
    <label className="v3d-num" title={`${name} (${unit}) - the arrow keys step it, Shift: ten steps`}>
      <span>{name}</span>
      <NumInput value={value} onSet={onSet} min={min} step={step} />
    </label>
  );
}

function Tool({ icon, title, onClick, active, disabled, children }: { icon: ReactNode; title: string; onClick: () => void;
  active?: boolean; disabled?: boolean; children?: ReactNode }) {
  return (
    <button type="button" className={`btn small v3d-tool${active ? " on" : ""}`} onClick={onClick} disabled={disabled}
      title={title} aria-label={title} aria-pressed={active}>{icon}{children}</button>
  );
}

/** The STL, 3MF and OBJ files in the folder (a person's: in the chat's files), to put in the design. */
function FolderMeshes({ store, onPick, onUpload }: { store: DesignFolder; onPick: (path: string) => void;
  onUpload: (f: File) => void }) {
  const [files, setFiles] = useState<string[] | null>(null);
  useEffect(() => {
    store.list().then((all) => setFiles(all.filter((e) => MESH_FILE.test(e.path)).map((e) => e.path).sort()),
      () => setFiles([]));
  }, [store]);
  return (
    <>
      <label className="menu-item"><Upload size={15} /><span>Upload from this computer<small>STL, 3MF or OBJ, into {store.where}</small></span>
        <input type="file" accept=".stl,.3mf,.obj" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); }} />
      </label>
      <div className="menu-sep" />
      {files === null && <div className="menu-item faint">Looking in {store.where}</div>}
      {files?.length === 0 && <div className="menu-item faint">No STL, 3MF or OBJ in {store.where}</div>}
      {files?.map((f) => <button key={f} className="menu-item mono" onClick={() => onPick(f)}>{f}</button>)}
    </>
  );
}

export function Editor({ store, path, create, own = "", onPath, onClose, max, onMax, onPin }: { store: DesignFolder;
  path: string; create?: boolean; own?: string; onPath: (path: string) => void; onClose: () => void; max: boolean;
  onMax: () => void; onPin: (path: string) => void }) {
  const { toast } = useApp();
  const folder = useRef(store);                 // where it reads and writes (0.93.0: the Hub's, or a person's device)
  folder.current = store;
  const host = useRef<HTMLDivElement>(null);
  const vp = useRef<Viewport | null>(null);
  const kit = useRef<Kit | null>(null);
  const [ready, setReady] = useState(false);
  const [design, setDesignState] = useState<Design | null>(null);
  const designRef = useRef<Design | null>(null);
  const [selected, setSelectedState] = useState<string[]>([]);
  const selectedRef = useRef<string[]>([]);
  const [mode, setMode] = useState<Mode>("select");
  const [built, setBuilt] = useState<Built | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [problems, setProblems] = useState<string[]>([]);
  const [outside, setOutside] = useState<string | null>(null);
  const [tool, setTool] = useState<null | "point" | "flat" | "edge" | "face">(null);
  const [edgeSize, setEdgeSize] = useState(2);
  const [edgeSel, setEdgeSel] = useState<Record<string, string[]>>({});       // picked edges, by part (0.92.0)
  const [hoverEdge, setHoverEdge] = useState<{ part: string; edge: string } | null>(null);
  const hoverFrame = useRef(0);
  const [several, setSeveral] = useState(false);                               // Select multiple: a click adds
  // A box's face clicked (0.98.0): the spot on it, and what the Face tab puts there - kept from one face to the next.
  const [faceSel, setFaceSel] = useState<FacePick | null>(null);
  const [feature, setFeature] = useState<Feature>({ round: true, size: 6, through: true, depth: 5, height: 5 });
  // A sketch being drawn (0.100.0): its plane, its shapes, the tool, what is half drawn - and the point a click would
  // put down, snapped; and what Extrude and Cut make of it, kept from one sketch to the next.
  const [sketch, setSketch] = useState<SketchState | null>(null);
  const sketchRef = useRef<SketchState | null>(null);
  sketchRef.current = sketch;
  const [hoverPt, setHoverPt] = useState<{ p: Point2; snapped: boolean } | null>(null);
  const hoverRef = useRef<{ p: Point2; snapped: boolean } | null>(null);
  hoverRef.current = hoverPt;
  const [sketchMake, setSketchMake] = useState<SketchMake>({ height: 5, through: true, depth: 5 });
  // The parts' tree over the view (0.96.1): open or folded, kept for next time - folded at first on a phone.
  const [treeOpen, setTreeOpenState] = useState(() => {
    try {
      const v = localStorage.getItem("slq3d.tree");
      if (v) return v === "open";
    } catch { /* no storage: the default */ }
    return window.innerWidth >= 700;
  });
  const setTreeOpen = (v: boolean) => {
    setTreeOpenState(v);
    try {
      localStorage.setItem("slq3d.tree", v ? "open" : "closed");
    } catch { /* kept for this time only */ }
  };
  const [failed, setFailed] = useState<string | null>(null);
  const [meshTick, setMeshTick] = useState(0);
  const [lock, setLock] = useState(false);
  const [tab, setTab] = useState<Tab>("insert");
  const meshes = useRef(new Map<string, MeshData | string>());
  const loading = useRef(new Set<string>());
  const past = useRef<Design[]>([]);
  const future = useRef<Design[]>([]);
  const saved = useRef("");                     // the file's text as this page last read or wrote it
  const inFlight = useRef<string | null>(null);  // a save on its way
  const dirty = useRef(false);                  // changes made here that are not saved yet
  const saveTimer = useRef(0);
  const dragging = useRef(false);
  const alive = useRef(true);
  const firstView = useRef(true);
  const skipOpen = useRef<string | null>(null);
  const shown3d = useRef<string[] | null>(null);      // parts just added: the view widens when they are out of it
  const designPath = useRef(path);              // where the design is saved (an STL's: <name>.3d.json beside it)
  const pathRef = useRef(path);
  pathRef.current = path;
  const shown = MESH_FILE.test(path) ? path.replace(MESH_FILE, ".3d.json") : path;
  const printer: Printer = useMemo(() => printerOf(design?.printer, design?.bed), [design?.printer, design?.bed]);

  const setDesign = useCallback((d: Design | null) => {
    designRef.current = d;
    setDesignState(d);
  }, []);
  const setSelected = useCallback((ids: string[]) => {
    selectedRef.current = ids;
    setSelectedState(ids);
  }, []);

  // ---------------------------------------------------------------- saving

  const save = useCallback(async () => {
    window.clearTimeout(saveTimer.current);
    const d = designRef.current, at = designPath.current;
    if (!d || !at) return;
    const text = writeDesign(d);
    if (text === saved.current) {
      dirty.current = false;
      setStatus("saved");
      return;
    }
    setStatus("saving");
    inFlight.current = text;
    try {
      await folder.current.writeText(at, text);
      if (!alive.current) return;                      // closed meanwhile: saved, nothing more to show
      saved.current = text;
      const same = !!designRef.current && writeDesign(designRef.current) === text;
      if (same) dirty.current = false;
      setStatus(same ? "saved" : "unsaved");
      if (at !== pathRef.current) {                    // an STL's new design, now a file: the one open
        skipOpen.current = at;
        onPath(at);
      }
      onPin(at);
    } catch (e) {
      if (alive.current) setStatus("error");
      toast(`The design was not saved: ${(e as Error).message}`, "fault");
    } finally {
      if (inFlight.current === text) inFlight.current = null;
    }
  }, [toast, onPin, onPath]);
  const saveRef = useRef(save);
  saveRef.current = save;

  const later = useCallback(() => {
    dirty.current = true;
    setStatus("unsaved");
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => void saveRef.current(), 1200);
  }, []);

  /** A change made here: kept for Undo, shown, and saved a moment later. Parts on faces (0.98.0) go where their boxes
   *  are now - and one moved, turned or sized itself keeps what was done to it on its face. */
  const commit = useCallback((next: Design) => {
    const cur = designRef.current;
    if (cur) {
      past.current.push(cur);
      if (past.current.length > 100) past.current.shift();
    }
    future.current = [];
    setDesign({ ...next, parts: settle(next.parts, undefined, cur?.parts) });
    later();
  }, [setDesign, later]);

  const undo = useCallback(() => {
    const prev = past.current.pop();
    if (!prev || !designRef.current) return;
    future.current.push(designRef.current);
    setDesign(prev);
    later();
  }, [setDesign, later]);
  const redo = useCallback(() => {
    const next = future.current.pop();
    if (!next || !designRef.current) return;
    past.current.push(designRef.current);
    setDesign(next);
    later();
  }, [setDesign, later]);

  useEffect(() => {
    alive.current = true;
    return () => {                                     // closing: what is not saved yet is saved now
      alive.current = false;
      if (dirty.current) void saveRef.current();
    };
  }, []);

  // A message is about to go: what is not saved yet is saved first, so it goes as it is now (0.93.0 - a person's
  // chat sends its files as they are at that moment).
  useEffect(() => {
    const flush = (e: Event) => {
      const waits = (e as CustomEvent<Promise<void>[]>).detail;
      if (dirty.current && Array.isArray(waits)) waits.push(saveRef.current());
    };
    window.addEventListener("slq:3d-save", flush);
    return () => window.removeEventListener("slq:3d-save", flush);
  }, []);

  // ---------------------------------------------------------------- loading

  const fetchMesh = useCallback(async (full: string) => parseMesh(full, await folder.current.readBytes(full)), []);

  const ensureMeshes = useCallback((d: Design) => {
    const want: string[] = [];
    const walk = (ps: Part[]) => ps.forEach((p) => {
      if (p.shape === "mesh" && p.file && !meshes.current.has(p.file) && !loading.current.has(p.file)) want.push(p.file);
      if (p.parts) walk(p.parts);
    });
    walk(d.parts);
    for (const file of want) {
      loading.current.add(file);
      fetchMesh(joinPath(dirOf(designPath.current), file))
        .then((m) => meshes.current.set(file, m), (e) => meshes.current.set(file, (e as Error).message))
        .finally(() => {
          loading.current.delete(file);
          setMeshTick((t) => t + 1);
        });
    }
  }, [fetchMesh]);

  const adopt = useCallback((text: string, fromOutside: boolean) => {
    const { design: d, problems: p } = readDesign(text);
    if (fromOutside && designRef.current) {            // what the assistant wrote can be undone here
      past.current.push(designRef.current);
      future.current = [];
    }
    saved.current = text;
    dirty.current = false;
    window.clearTimeout(saveTimer.current);
    setProblems(p);
    setDesign(d);
    setStatus("saved");
    const ids = allIds(d.parts);
    setSelected(selectedRef.current.filter((id) => ids.has(id)));
    ensureMeshes(d);
  }, [ensureMeshes, setDesign, setSelected]);

  useEffect(() => {
    let on = true;
    loadKit().then((k) => {
      if (!on) return;
      kit.current = { ...k, meshes: meshes.current };
      setReady(true);
    }, (e) => on && setFailed(`The 3D engine did not load: ${(e as Error).message}`));
    return () => { on = false; };
  }, []);

  useEffect(() => {
    if (path && path === skipOpen.current) {           // the editor itself moved here (an STL's design, saved)
      skipOpen.current = null;
      return;
    }
    if (path.startsWith("/")) {                        // the sandbox's own path, as the assistant may write it
      onPath(store.local(path));
      return;
    }
    let on = true;
    setStatus("loading");
    setFailed(null);
    setOutside(null);
    past.current = [];
    future.current = [];
    saved.current = "";
    dirty.current = false;
    firstView.current = true;
    designPath.current = path;
    const open = async () => {
      if (create && !path) {                           // New 3D design: a free name (hub/ in a synced project)
        let name = "";
        for (let n = 1; n < 100 && !name; n++) {
          const next = `${own}${n === 1 ? "design" : `design-${n}`}.3d.json`;
          if (!await store.has(next)) name = next;
        }
        if (!name) throw new Error("There are 99 designs called design-N already: rename some.");
        const pr = lastPrinter();
        await store.writeText(name, writeDesign(newDesign(pr.id, pr.bed)));
        if (on) onPath(name);
        return;
      }
      if (MESH_FILE.test(path)) {                      // an STL, 3MF or OBJ: a design with it in, beside it
        const designName = path.replace(MESH_FILE, ".3d.json");
        const there = await store.readText(designName).catch(() => null);
        if (!on) return;
        if (there != null) {                           // made before: that one
          onPath(designName);
          return;
        }
        const m = await fetchMesh(path);
        if (!on) return;
        const file = baseName(path);
        meshes.current.set(file, m);
        const size = meshSize(m);
        const pr = lastPrinter();
        const d = newDesign(pr.id, pr.bed);
        d.parts = [{ id: "p1", name: file, shape: "mesh", file, size, at: [0, 0, r3(size[2] / 2)], rotate: [0, 0, 0],
          color: COLORS[0] }];
        designPath.current = designName;
        setProblems([]);
        setDesign(d);
        setStatus("new");
        return;
      }
      const text = await store.readText(path);
      if (!on) return;
      if (text == null) throw new Error(`${path} is not in ${store.where}, or not a text file the editor can read`);
      adopt(text, false);
      onPin(path);
    };
    open().catch((e) => on && setFailed((e as Error).message));
    return () => { on = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.key, path, create]);

  // What the assistant (or anyone) writes to the file shows here; with changes of one's own not saved yet, ask.
  useEffect(() => {
    if (status === "loading" || status === "new" || !path) return;
    const t = window.setInterval(async () => {
      if (document.hidden || dragging.current || outside !== null) return;
      try {
        const text = await folder.current.readText(designPath.current);
        if (!alive.current || text == null || text === saved.current || text === inFlight.current) return;
        if (designRef.current && text === writeDesign(designRef.current)) {
          saved.current = text;
          return;
        }
        if (!dirty.current) adopt(text, true);
        else setOutside(text);
      } catch {
        /* gone or offline: the next look tells */
      }
    }, 2500);
    return () => window.clearInterval(t);
  }, [status, path, store.key, adopt, outside]);

  // ---------------------------------------------------------------- the view

  useEffect(() => {
    if (!host.current) return;
    const v = new Viewport(host.current);
    vp.current = v;
    v.view("home", null);
    const theme = new MutationObserver(() => v.theme());
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "style"] });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onMedia = () => v.theme();
    media.addEventListener("change", onMedia);
    return () => {
      theme.disconnect();
      media.removeEventListener("change", onMedia);
      v.dispose();
      vp.current = null;
    };
  }, []);

  useEffect(() => {
    vp.current?.setPlate(printer.bed);
  }, [printer.bed]);

  const colorOf = useCallback((id: string) => {
    const d = designRef.current;
    const f = d ? find(d.parts, id) : null;
    if (!f) return GREY;
    if (f.part.hole) {                                 // where a hole cut: the colour of the solid beside it
      const solid = f.siblings.find((s) => !s.hole && s.shape !== "group" && s.color);
      return solid?.color ?? GREY;
    }
    return f.part.color ?? GREY;
  }, []);

  // The solid the design makes, its holes as ghosts.
  useEffect(() => {
    const v = vp.current, k = kit.current;
    if (!design || !v || !k || !ready) return;
    const wanted = [...fontsWanted(design.parts)];
    if (wanted.length) {                               // a text in a font not loaded yet: built when it is
      Promise.allSettled(wanted.map(loadFont)).then((done) => {
        if (!alive.current) return;
        if (done.some((r) => r.status === "rejected")) toast(`A font did not load (${wanted.join(", ")}).`, "fault");
        setMeshTick((t) => t + 1);
      });
      return;
    }
    try {
      const b = build(design, k);
      setBuilt(b);
      v.setResult(b, colorOf);
      const holes: { part: string; built: Built }[] = [];
      const walk = (ps: Part[]) => ps.forEach((p) => {
        if (p.hidden) return;
        if (p.hole) holes.push({ part: p.id, built: buildPart(p, k) });
        else if (p.parts) walk(p.parts);
      });
      walk(design.parts);
      v.setGhosts(holes);
      if (firstView.current && (b.box || holes.length)) {
        firstView.current = false;
        shown3d.current = null;
        v.view("home", union([b.box, ...holes.map((h) => h.built.box)]));
      }
      const added = shown3d.current;
      shown3d.current = null;
      const where = added && union(added.map((id) => {
        const f = find(design.parts, id);
        return f ? buildPart(f.part, k).box ?? extent(f.part) : null;
      }));
      if (where && !v.inView(where)) v.view("home", union([b.box, where]));
    } catch (e) {
      setBuilt(null);
      toast(`The design could not be built: ${(e as Error).message}`, "fault");
    }
  }, [design, ready, meshTick, colorOf, toast]);

  // The selection: outlined, and the gizmo on it.
  useEffect(() => {
    const v = vp.current, k = kit.current;
    if (!v || !design || !k || !ready || dragging.current) return;
    const items = pick(design.parts, selected);
    const builts = items.map((p) => buildPart(p, k));
    v.setOutline(builts);
    if (!items.length || tool || mode === "select") {
      v.detach();
      return;
    }
    const one = items.length === 1 && !asGroup(items[0]) ? items[0] : null;
    const b = union(builts.map((x, i) => x.box ?? extent(items[i])));
    v.attach(one ? one.at : b ? centre(b) : [0, 0, 0], one ? one.rotate : null, mode, SNAP);
  }, [design, selected, mode, ready, tool, meshTick]);

  // The picked lines (0.92.0) drawn over the model, and the one a click would pick (0.94.0: near a line, on any tab);
  // in the Face tab (0.98.0) the face picked, what goes on it and how far it is from its edges - as SolidWorks draws a
  // sketch's dimensions.
  const faceBox = (() => {
    const f = faceSel && design ? find(design.parts, faceSel.part)?.part : null;
    return f && f.shape === "box" && !f.repeat ? f : null;
  })();
  const facing = tab === "face" && !!faceBox;
  useEffect(() => {
    const v = vp.current;
    if (!v || !ready) return;
    const list: { lines: Vec3[][]; hover?: boolean; face?: number }[] = [];
    const words: { at: Vec3; text: string; kind?: "dot" | "snap" | "typed" }[] = [];
    const at = sketch ? sketchFrame(sketch) : null;
    if (sketch && at) {                                // a sketch (0.100.0): its shapes in blue, an open line amber
      const f = at.f, w3 = (pts: Point2[]) => pts.map((q) => to3d(f, q));
      if (at.box && sketch.plane.kind === "face") {
        list.push({ lines: [faceMarks(at.box, sketch.plane.face, undefined, true, 0).outline], face: 1.5 });
      } else if (sketch.plane.kind !== "top") {        // the front or right plane: a frame to draw in, on the plate
        list.push({ lines: [w3([[-110, 0], [110, 0], [110, 110], [-110, 110], [-110, 0]])], face: 1 });
      }
      const closed: Vec3[][] = [], open: Vec3[][] = [], picked: Vec3[][] = [];
      sketch.shapes.forEach((sh, i) => (i === sketch.picked ? picked : loopOf(sh) ? closed : open).push(w3(strokesOf(sh))));
      if (closed.length) list.push({ lines: closed, face: 2.5 });
      if (open.length) list.push({ lines: open, hover: true });
      if (picked.length) list.push({ lines: picked });
      for (const q of snapPoints(sketch.shapes)) words.push({ at: to3d(f, q), text: "", kind: "dot" });
      const d = sketch.draft, to = hoverPt?.p;
      const mid = (a: Point2, b: Point2): Vec3 => to3d(f, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
      if (d) {
        const rubber: Point2[][] = [];
        if (d.kind === "line") {
          rubber.push(to ? [...d.pts, to] : d.pts);
          for (const q of d.pts) words.push({ at: to3d(f, q), text: "", kind: "dot" });
          const last = d.pts[d.pts.length - 1];
          if (to && (to[0] !== last[0] || to[1] !== last[1])) words.push({ at: mid(last, to), text: fmt(Math.hypot(to[0] - last[0], to[1] - last[1])) });
        } else if (d.kind === "rect" && to) {
          const [x0, y0] = d.a, [x1, y1] = to;
          rubber.push([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]);
          words.push({ at: mid(d.a, to), text: `${fmt(Math.abs(x1 - x0))} x ${fmt(Math.abs(y1 - y0))}` });
        } else if (d.kind === "circle" && to) {
          const r = Math.hypot(to[0] - d.c[0], to[1] - d.c[1]);
          if (r > 0) {
            rubber.push(strokesOf({ kind: "circle", c: d.c, r }));
            words.push({ at: to3d(f, d.c), text: `Ø${fmt(2 * r)}` });
          }
        }
        list.push({ lines: rubber.map(w3), face: 1.5 });
      }
      const sh = sketch.picked !== null ? sketch.shapes[sketch.picked] : null;
      if (sh?.kind === "rect") words.push({ at: mid(sh.a, sh.b), text: `${fmt(Math.abs(sh.b[0] - sh.a[0]))} x ${fmt(Math.abs(sh.b[1] - sh.a[1]))}` });
      if (sh?.kind === "circle") words.push({ at: to3d(f, sh.c), text: `Ø${fmt(2 * sh.r)}` });
      if (to) words.push({ at: to3d(f, to), text: "", kind: hoverPt?.snapped ? "snap" : "dot" });
      if (sketch.typed && to) words.push({ at: to3d(f, to), text: `${sketch.typed}|`, kind: "typed" });
    } else if (design) {
      for (const [id, edges] of Object.entries(edgeSel)) {
        const f = find(design.parts, id);
        if (f) list.push({ lines: edgeLines(f.part, edges) });
      }
      const f = hoverEdge ? find(design.parts, hoverEdge.part) : null;
      if (f && hoverEdge) list.push({ lines: edgeLines(f.part, [hoverEdge.edge]), hover: true });
      const dimension = (box: Part, face: FaceName, from: Record<string, number> | undefined, round: boolean | null,
        size: number) => {
        const m = faceMarks(box, face, from, !!round, size);
        list.push({ lines: [m.outline], face: 4 }, { lines: round === null ? m.shape.slice(1) : m.shape, face: 2.5 },
          { lines: m.dims.map((x) => x.line), face: 1.5 });
        for (const x of m.dims) {
          const [a, b] = x.line;
          words.push({ at: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2], text: fmt(x.mm) });
        }
        const ring = m.shape[0];
        if (round !== null) words.push({ at: ring[Math.round(ring.length / 8)], text: round ? `Ø${fmt(size)}` : `${fmt(size)} x ${fmt(size)}` });
      };
      if (facing && faceBox && faceSel) dimension(faceBox, faceSel.face, faceSel.from, feature.round, feature.size);
      // A part on a face, picked: where it is kept, as the Face tab showed it (0.98.0)
      const kept = !facing && tab === "part" && selected.length === 1 ? find(design.parts, selected[0])?.part : null;
      const on = kept?.on, box = on ? find(design.parts, on.part)?.part : null;
      if (kept && on && box) {
        dimension(box, on.face, on.from, kept.shape === "cylinder" ? true : kept.shape === "box" ? false : null,
          kept.shape === "cylinder" || kept.shape === "box" ? kept.size[0] : 0);
      }
    }
    v.setMarks(list);
    v.setLabels(words);
  }, [design, edgeSel, hoverEdge, ready, facing, faceBox, faceSel, feature.round, feature.size, tab, selected, sketch, hoverPt]);

  // What Extrude would make of the sketch (0.100.0), shown as it would stand - the rest faded while there is any.
  useEffect(() => {
    const v = vp.current, k = kit.current;
    if (!v || !ready || !k || !sketch) return;
    const at = sketchFrame(sketch);
    if (!at || !sketchRegions(sketch.shapes).length) {
      v.setPreview(null);
      return;
    }
    let n = 0;
    const made = sketchParts(sketch.plane, sketch.shapes, at.box, { cut: false, depth: sketchMake.height, through: false,
      reach: 0 }, () => `preview${++n}`, "Sketch");
    v.setPreview(made.map((p) => ({ built: buildPart(p.on && at.box ? placeOn(p, at.box) : p, k),
      color: at.box?.color ?? "#e8a33d" })));
  }, [sketch?.shapes, sketch?.plane, sketchMake.height, design, ready]);

  useEffect(() => {                                   // Pick lines belongs to the Lines tab
    if (tab !== "lines" && tool === "edge") setTool(null);
    if (tool && tool !== "edge") setHoverEdge(null);
  }, [tab, tool]);

  // ---------------------------------------------------------------- dragging: the gizmo, or the part itself

  const start = useRef<{ design: Design; ids: string[]; pos: THREE.Vector3; quat: THREE.Quaternion; matrix: THREE.Matrix4;
    body?: { z: number; from: Vec3 } } | null>(null);

  const beginDrag = useCallback((body?: { z: number; from: Vec3 }) => {
    const v = vp.current, d = designRef.current, k = kit.current;
    if (!v || !d || !k) return;
    dragging.current = true;
    const ids = selectedRef.current;
    v.proxy.updateMatrixWorld(true);
    start.current = { design: d, ids, pos: v.proxy.position.clone(), quat: v.proxy.quaternion.clone(),
      matrix: v.proxy.matrixWorld.clone(), body };
    // What is on their faces goes with them (0.98.0) - not when they are sized: what is on them keeps its own size.
    const going = [...pick(d.parts, ids), ...(v.gizmo.mode === "scale" && !body ? [] : riders(d.parts, ids))];
    v.setPreview(going.map((p) => ({ built: buildPart(p, k), color: p.hole ? GREY : p.color ?? GREY })));
  }, []);

  const finishDrag = useCallback(() => {
    const v = vp.current, s = start.current, k = kit.current;
    dragging.current = false;
    start.current = null;
    if (!v || !s || !k) return;
    v.setPreview(null);
    const pos = v.proxy.position, sc = v.proxy.scale;
    const moved = pos.distanceTo(s.pos) > 1e-6 || v.proxy.quaternion.angleTo(s.quat) > 1e-6
      || Math.abs(sc.x - 1) + Math.abs(sc.y - 1) + Math.abs(sc.z - 1) > 1e-6;
    if (!moved) {
      setSelected([...s.ids]);                         // puts the gizmo back where it was
      return;
    }
    let parts = s.design.parts;
    const items = pick(parts, s.ids);
    const one = items.length === 1 && !asGroup(items[0]) ? items[0] : null;
    if (one && !s.body) {
      const e = new THREE.Euler().setFromQuaternion(v.proxy.quaternion, "ZYX");
      const next: Part = { ...one, at: [pos.x, pos.y, pos.z],
        rotate: [(e.x * 180) / Math.PI, (e.y * 180) / Math.PI, (e.z * 180) / Math.PI] };
      if (v.gizmo.mode === "scale") {
        const was = sized(one, k.fonts), f = [Math.abs(sc.x), Math.abs(sc.y), Math.abs(sc.z)];
        next.size = was.map((x, i) => Math.max(MIN_SIZE, x * f[i])) as Vec3;
        const low = buildPart(one, k).box?.[0][2];
        if (low !== undefined && Math.abs(low) < 0.01) {            // it stood on the plate: it still does
          const now = buildPart(next, k).box?.[0][2] ?? 0;
          next.at = [next.at[0], next.at[1], next.at[2] - now];
        }
      }
      parts = update(parts, one.id, () => tidy(next));
    } else {
      const move: Vec3 = [pos.x - s.pos.x, pos.y - s.pos.y, pos.z - s.pos.z];
      const dq = v.proxy.quaternion.clone().multiply(s.quat.clone().invert());
      const turn = dq.angleTo(new THREE.Quaternion()) > 1e-9 ? quatTurn(dq) : null;
      const f = v.gizmo.mode === "scale" && !s.body ? sc.x : 1;
      const c: Vec3 = [s.pos.x, s.pos.y, s.pos.z];
      for (const id of s.ids) parts = update(parts, id, (p) => tidy(transformPart(p, c, move, turn, f)));
    }
    commit({ ...s.design, parts });
  }, [commit, setSelected]);

  useEffect(() => {
    const v = vp.current;
    if (!v || !ready) return;
    const down = () => beginDrag();
    const change = () => {
      const s = start.current;
      if (!s) return;
      if (v.gizmo.mode === "scale") {                  // several parts or a group: the same in every direction
        const items = pick(s.design.parts, s.ids);
        if (items.length !== 1 || asGroup(items[0])) {
          v.proxy.scale.setScalar(Math.max(0.01, (v.proxy.scale.x + v.proxy.scale.y + v.proxy.scale.z) / 3));
        }
      }
      v.proxy.updateMatrixWorld(true);
      v.movePreview(v.proxy.matrixWorld.clone().multiply(s.matrix.clone().invert()));
    };
    v.gizmo.addEventListener("mouseDown", down);
    v.gizmo.addEventListener("objectChange", change);
    v.gizmo.addEventListener("mouseUp", finishDrag);
    return () => {
      v.gizmo.removeEventListener("mouseDown", down);
      v.gizmo.removeEventListener("objectChange", change);
      v.gizmo.removeEventListener("mouseUp", finishDrag);
    };
  }, [ready, beginDrag, finishDrag]);

  // ---------------------------------------------------------------- the pointer on the model

  const pointer = useRef<{ x: number; y: number; hit: Hit | null; body: boolean } | null>(null);

  const topOf = (id: string) => {
    const d = designRef.current;
    return d ? find(d.parts, id)?.top.id ?? id : id;
  };

  const insertPoint = (h: Hit) => {
    const d = designRef.current;
    const p = d ? find(d.parts, h.part)?.part : null;
    const text = pointMark(p ? label(p) : h.part, h.part, !!p?.hole, h.point, h.normal);
    window.dispatchEvent(new CustomEvent("slq:compose-insert", { detail: { text } }));
    toast("The spot is in your message", "run");
  };

  const layFlat = (h: Hit) => {
    const d = designRef.current, k = kit.current;
    if (!d || !k) return;
    const id = selectedRef.current.find((s) => within(d.parts, s, h.part)) ?? topOf(h.part);
    const item = find(d.parts, id)?.part;
    if (!item) return;
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(...h.normal).normalize(), new THREE.Vector3(0, 0, -1));
    const b = buildPart(item, k).box ?? extent(item);
    const turned = transformPart(item, b ? centre(b) : item.at, [0, 0, 0], quatTurn(q));
    commit({ ...d, parts: update(d.parts, id, () => tidy(dropped(turned, k))) });
    setSelected([id]);
    setTool(null);
  };

  /** On a face (0.97.0): the one part picked put on the face clicked - a text's letters on it, upright on a wall. */
  const placeOnFace = (x: number, y: number) => {
    const d = designRef.current, v = vp.current, k = kit.current;
    const f = d && selectedRef.current.length === 1 ? find(d.parts, selectedRef.current[0]) : null;
    if (!d || !v || !k || !f) {
      toast("Pick one part, then click the face it should lie on.", "fault");
      setTool(null);
      return;
    }
    const { front, solid } = v.picks(x, y, allIds([f.part]));      // not on itself
    const h = solid ?? front;
    if (!h) return;                                    // nothing there: click a face
    const box = solidBox(h.part), face = box && f.part.shape !== "group" ? faceAt(box, h.point, h.normal) : null;
    if (box && face) {                                 // a box's face: kept on it where it was clicked (0.98.0)
      const p = f.part;
      const on: Anchor = { part: box.id, face, from: spotAt(box, face, h.point), depth: Math.max(MIN_SIZE, r3(p.size[2] - 0.2)) };
      const angle = p.shape === "text" ? faceAngle(box, face, v.cameraRight()) : 0;
      if (angle) on.angle = angle;
      if (!on.from) delete on.from;
      commit({ ...d, parts: update(d.parts, p.id, () => tidy({ ...p, on })) });
    } else {
      const next = onFace(f.part, h.point, h.normal, v.cameraRight(), buildPart(f.part, k).box);
      delete next.on;                                  // on another shape's face: free
      commit({ ...d, parts: update(d.parts, f.part.id, () => tidy(next)) });
    }
    setTool(null);
  };

  /** The solid box a click is on (0.98.0) - one a face can be picked on: not a hole, nor in a hole group, nor repeated. */
  const solidBox = (id: string): Part | null => {
    const d = designRef.current;
    const p = d ? find(d.parts, id)?.part : null;
    return d && p && p.shape === "box" && !p.repeat && !cuts(d.parts, p.id) ? p : null;
  };

  /** Cut hole, Add boss, Text here (0.98.0, the Face tab): a part kept on the picked face, at its spot - beside the box
   *  in the tree, so a hole cuts what the box is in - then picked, its tab open. */
  const onTheFace = (kind: "hole" | "boss" | "text") => {
    const d = designRef.current, v = vp.current;
    const box = faceSel && d ? find(d.parts, faceSel.part)?.part : null;
    if (!d || !faceSel || !box) return;
    const seen = allIds(d.parts);
    const id = freshId(seen);
    const base: Anchor = { part: box.id, face: faceSel.face };
    if (faceSel.from) base.from = { ...faceSel.from };
    const s = Math.max(MIN_SIZE, feature.size);
    let p: Part;
    if (kind === "text") {
      const F = faceFrame(box, faceSel.face);
      const angle = faceAngle(box, faceSel.face, v?.cameraRight() ?? [1, 0, 0]);
      p = { id, shape: "text", text: "Text", size: [0, r3(Math.min(10, Math.max(2, Math.min(F.W, F.H) * 0.5))), 1],
        at: [0, 0, 0], rotate: [0, 0, 0], color: COLORS[seen.size % COLORS.length], on: { ...base, depth: 1 } };
      if (angle) p.on!.angle = angle;
    } else {
      const round = feature.round, hole = kind === "hole";
      p = { id, name: hole ? (round ? "Round hole" : "Square hole") : round ? "Boss" : "Square boss",
        shape: round ? "cylinder" : "box", size: [s, s, 1], at: [0, 0, 0], rotate: [0, 0, 0] };
      if (round) p.sides = 64;
      if (hole) {
        p.hole = true;
        p.on = feature.through ? { ...base, through: true } : { ...base, depth: Math.max(MIN_SIZE, feature.depth) };
      } else {
        p.color = box.color;
        p.on = { ...base, depth: Math.max(MIN_SIZE, feature.height) };
      }
    }
    shown3d.current = [id];
    commit({ ...d, parts: insertAfter(d.parts, box.id, p) });
    setSelected([id]);
    setEdgeSel({});
    setFaceSel(null);
    setTab(kind === "text" ? "text" : "part");
    host.current?.focus({ preventScroll: true });     // its button is gone: Esc and the arrow keys work on
  };

  // ---------------------------------------------------------------- the sketch (0.100.0)

  /** A sketch's plane where it is now - a box's face goes where the box goes - or null, its box gone. */
  const sketchFrame = (s: SketchState | null): { f: PlaneFrame; box: Part | null } | null => {
    const d = designRef.current;
    if (!s || !d) return null;
    const box = s.plane.kind === "face" ? find(d.parts, s.plane.part)?.part ?? null : null;
    const f = planeFrame(s.plane, box);
    return f ? { f, box } : null;
  };

  /** Sketch on a plane: nothing picked, the view turned straight at it, Line ready to draw. */
  const startSketch = (plane: SketchPlane) => {
    const d = designRef.current, v = vp.current, k = kit.current;
    const box = plane.kind === "face" && d ? find(d.parts, plane.part)?.part ?? null : null;
    const f = d ? planeFrame(plane, box) : null;
    if (!d || !f) return;
    let n = 0;                                         // Sketch 1, 2 ... as SolidWorks numbers them
    const count = (list: Part[]) => list.forEach((p) => {
      const m = /^Sketch (\d+)/.exec(p.name ?? "");
      if (m) n = Math.max(n, Number(m[1]));
      if (p.parts) count(p.parts);
    });
    count(d.parts);
    setSketch({ plane, shapes: [], tool: "line", draft: null, picked: null, typed: "", past: [], n: n + 1 });
    setHoverPt(null);
    setSelected([]);
    setEdgeSel({});
    setFaceSel(null);
    setTool(null);
    setHoverEdge(null);
    setMode("select");
    setTab("sketch");
    if (v) {
      if (box && k) v.lookFrom(f.N, buildPart(box, k).box);
      else v.view(plane.kind === "face" ? "top" : plane.kind, built?.box ?? extentOf(d.parts));
    }
    host.current?.focus({ preventScroll: true });
  };

  /** The point on the sketch's plane under the pointer, as a click would put it down: a corner, an end or a middle
   *  within 10 px (a face's corners and middle too) - else the nearest whole millimetre (on a face, from its lower
   *  left corner, so its distances from the edges are whole). */
  const sketchAt = (x: number, y: number): { p: Point2; snapped: boolean } | null => {
    const v = vp.current, s = sketchRef.current, at = sketchFrame(s), r = host.current?.getBoundingClientRect();
    if (!v || !s || !at || !r) return null;
    const { origin, dir } = v.rayAt(x, y);
    const raw = rayOnPlane(at.f, origin, dir);
    if (!raw) return null;
    const near: Point2[] = [...snapPoints(s.shapes)];
    if (s.draft?.kind === "line") near.push(...s.draft.pts);
    else if (s.draft) near.push(s.draft.kind === "rect" ? s.draft.a : s.draft.c);
    const w = (at.f.W ?? 0) / 2, h = (at.f.H ?? 0) / 2;
    if (at.f.W) near.push([0, 0], [-w, -h], [w, -h], [w, h], [-w, h], [0, -h], [0, h], [-w, 0], [w, 0]);
    let best: Point2 | null = null, gap = 10;
    for (const c of near) {
      const sp = v.toScreen(to3d(at.f, c));
      const g = sp ? Math.hypot(sp[0] - (x - r.left), sp[1] - (y - r.top)) : Infinity;
      if (g < gap) {
        gap = g;
        best = c;
      }
    }
    if (best) return { p: best, snapped: true };
    return { p: [Math.round(raw[0] + w) - w + 0, Math.round(raw[1] + h) - h + 0], snapped: false };
  };

  /** A shape added: what it was kept for Undo, nothing half drawn any more. */
  const withShape = (s: SketchState, shape: SketchShape): SketchState =>
    ({ ...s, shapes: [...s.shapes, shape], past: [...s.past, s.shapes], draft: null, typed: "", picked: null });

  /** A click in the sketch: the tool's next point - or, with no tool, the shape there picked. */
  const sketchClick = (pt: Point2) => {
    const s = sketchRef.current, v = vp.current, at = sketchFrame(s);
    if (!s || !v || !at) return;
    const same = (a: Point2, b: Point2) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6;
    const d = s.draft;
    if (!s.tool) {                                     // within 8 px of a shape's lines: that one
      const tol = 8 * v.mmPerPx(to3d(at.f, pt));
      let picked: number | null = null, gap = tol;
      s.shapes.forEach((sh, i) => {
        const g = distanceTo(sh, pt);
        if (g < gap) {
          gap = g;
          picked = i;
        }
      });
      setSketch({ ...s, picked });
      return;
    }
    if (s.tool === "line") {
      if (d?.kind !== "line") return setSketch({ ...s, draft: { kind: "line", pts: [pt] }, typed: "", picked: null });
      const pts = d.pts;
      if (pts.length >= 3 && same(pt, pts[0])) return setSketch(withShape(s, { kind: "line", pts, closed: true }));
      if (pts.length === 2 && same(pt, pts[0])) return;          // back to the first of two: no shape yet
      if (same(pt, pts[pts.length - 1])) {           // the same spot again: the line ends there
        return setSketch(pts.length >= 2 ? withShape(s, { kind: "line", pts, closed: false }) : { ...s, draft: null });
      }
      return setSketch({ ...s, draft: { kind: "line", pts: [...pts, pt] }, typed: "" });
    }
    if (s.tool === "rect") {
      if (d?.kind !== "rect") return setSketch({ ...s, draft: { kind: "rect", a: pt }, typed: "", picked: null });
      if (d.a[0] === pt[0] || d.a[1] === pt[1]) return;
      return setSketch(withShape(s, { kind: "rect", a: d.a, b: pt }));
    }
    if (d?.kind !== "circle") return setSketch({ ...s, draft: { kind: "circle", c: pt }, typed: "", picked: null });
    const rad = Math.hypot(pt[0] - d.c[0], pt[1] - d.c[1]);
    if (rad >= 0.25) setSketch(withShape(s, { kind: "circle", c: d.c, r: r3(rad) }));
  };

  /** What is typed while drawing, put down (as SketchUp's Measurements box): a line's next length, toward the
   *  pointer (a whole 15 degrees when within 4); a rectangle's "width;height" (one number: a square); a circle's
   *  diameter. */
  const sketchTyped = (s: SketchState): SketchState | null => {
    const nums = s.typed.split(/[;x* ]+/i).filter(Boolean).map((t) => Number(t.replace(",", ".")));
    if (!nums.length || nums.some((x) => !Number.isFinite(x) || x <= 0) || !s.draft) return null;
    const to = hoverRef.current?.p;
    const d = s.draft;
    if (d.kind === "line") {
      const last = d.pts[d.pts.length - 1];
      let a = to ? Math.atan2(to[1] - last[1], to[0] - last[0]) : 0;
      if (!to || (to[0] === last[0] && to[1] === last[1])) a = 0;
      const deg = (a * 180) / Math.PI, step = Math.round(deg / 15) * 15;
      if (Math.abs(deg - step) < 4) a = (step * Math.PI) / 180;
      const pt: Point2 = [r3(last[0] + nums[0] * Math.cos(a)), r3(last[1] + nums[0] * Math.sin(a))];
      return { ...s, draft: { kind: "line", pts: [...d.pts, pt] }, typed: "" };
    }
    if (d.kind === "rect") {
      const sx = to && to[0] < d.a[0] ? -1 : 1, sy = to && to[1] < d.a[1] ? -1 : 1;
      const [wd, ht] = [nums[0], nums[1] ?? nums[0]];
      return withShape(s, { kind: "rect", a: d.a, b: [r3(d.a[0] + sx * wd), r3(d.a[1] + sy * ht)] });
    }
    return withShape(s, { kind: "circle", c: d.c, r: r3(nums[0] / 2) });
  };

  /** Esc in the sketch: what is half drawn ends (a line of two corners or more stays, open); then the tool is put
   *  down; then nothing is picked. The sketch stays - Extrude, Cut or Cancel leaves it. */
  const sketchEscape = (s: SketchState): SketchState => {
    if (s.draft?.kind === "line" && s.draft.pts.length >= 2) return withShape(s, { kind: "line", pts: s.draft.pts, closed: false });
    if (s.draft || s.typed) return { ...s, draft: null, typed: "" };
    if (s.tool) return { ...s, tool: null };
    return { ...s, picked: null };
  };

  /** The keys while sketching: L, R and C draw lines, rectangles and circles; numbers are typed for the next length;
   *  Enter puts that down (or ends a line); Backspace takes back what was typed, or a line's last corner, or the
   *  shape picked, as Delete does; Ctrl+Z undoes the last shape. The editor's other keys wait. */
  const sketchKey = (e: React.KeyboardEvent | KeyboardEvent, s: SketchState): void => {
    const k = e.key, ctrl = e.ctrlKey || e.metaKey;
    const set = (next: SketchState | null) => {
      e.preventDefault();
      if (next) setSketch(next);
    };
    if (ctrl && k.toLowerCase() === "z") {
      const before = s.past[s.past.length - 1];
      return set(before ? { ...s, shapes: before, past: s.past.slice(0, -1), draft: null, typed: "", picked: null } : null);
    }
    if (ctrl || e.altKey) return;
    if (s.draft && /^[0-9.,;x* ]$/i.test(k)) return set({ ...s, typed: s.typed + k });
    if (k === "Enter") {
      if (s.typed) return set(sketchTyped(s) ?? { ...s, typed: "" });
      return set(s.draft?.kind === "line" ? sketchEscape(s) : null);
    }
    if (k === "Escape") return set(sketchEscape(s));
    if (k === "Backspace" && s.typed) return set({ ...s, typed: s.typed.slice(0, -1) });
    if (k === "Backspace" && s.draft?.kind === "line") {
      return set({ ...s, draft: s.draft.pts.length > 1 ? { kind: "line", pts: s.draft.pts.slice(0, -1) } : null });
    }
    if ((k === "Delete" || k === "Backspace") && s.picked !== null) {
      return set({ ...s, shapes: s.shapes.filter((_, i) => i !== s.picked), past: [...s.past, s.shapes], picked: null });
    }
    const tools: Record<string, SketchTool> = { l: "line", r: "rect", c: "circle" };
    if (tools[k.toLowerCase()]) return set({ ...s, tool: tools[k.toLowerCase()], draft: null, typed: "", picked: null });
  };

  /** Extrude or Cut: the sketch's regions made into parts - on a box's face kept on it, beside it in the tree so a cut
   *  cuts what the box is in - picked; the sketch over. */
  const endSketch = (cut: boolean) => {
    const d = designRef.current, s = sketchRef.current, at = sketchFrame(s), v = vp.current;
    if (!d || !s || !at) return;
    const seen = allIds(d.parts);
    const id = () => {
      const x = freshId(seen);
      seen.add(x);
      return x;
    };
    const b = built?.box ?? extentOf(d.parts);
    const reach = b ? Math.max(...b[0].map(Math.abs), ...b[1].map(Math.abs)) + 10 : 100;
    const made = sketchParts(s.plane, s.shapes, at.box, { cut, depth: cut ? sketchMake.depth : sketchMake.height,
      through: cut && sketchMake.through, reach }, id, `Sketch ${s.n}${cut ? " cut" : ""}`,
    cut ? undefined : at.box?.color ?? COLORS[seen.size % COLORS.length]);
    if (!made.length) {
      toast("Nothing closed to make yet - a line back to its first corner, a rectangle or a circle.", "fault");
      return;
    }
    let ps = d.parts;
    if (at.box) for (const p of [...made].reverse()) ps = insertAfter(ps, at.box.id, p);
    else ps = [...ps, ...made];
    shown3d.current = made.map((p) => p.id);
    commit({ ...d, parts: ps });
    setSketch(null);
    setHoverPt(null);
    v?.setPreview(null);
    setSelected(made.map((p) => p.id));
    setTab("part");
    host.current?.focus({ preventScroll: true });
  };

  const cancelSketch = () => {
    setSketch(null);
    setHoverPt(null);
    vp.current?.setPreview(null);
    setTab("insert");
  };

  /** Turn (0.97.0): each part picked turned 90 degrees about X, Y or Z through its middle, as Office's Rotate. */
  const turn90 = (axis: 0 | 1 | 2, sign: 1 | -1) => {
    const d = designRef.current, k = kit.current;
    if (!d || !k || !selectedRef.current.length) return;
    let ps = d.parts;
    for (const id of selectedRef.current) ps = update(ps, id, (p) => tidy(turned90(p, axis, sign, buildPart(p, k).box)));
    commit({ ...d, parts: ps });
  };

  const onPointerDown = (e: React.PointerEvent) => {
    host.current?.focus({ preventScroll: true });
    const v = vp.current;
    if (!v || e.button !== 0 || v.overGizmo) return;
    if (sketchRef.current) {                           // sketching: a click draws, a drag turns the view
      pointer.current = { x: e.clientX, y: e.clientY, hit: null, body: false };
      return;
    }
    const hit = v.pick(e.clientX, e.clientY);
    const body = mode === "translate" && !tool && !!hit && !e.shiftKey   // dragging a picked part moves it: Move only
      && selectedRef.current.some((s) => within(designRef.current?.parts ?? [], s, hit.part));
    pointer.current = { x: e.clientX, y: e.clientY, hit, body };
    if (body) {
      v.controls.enabled = false;                      // the part moves, not the view
      (e.target as Element).setPointerCapture?.(e.pointerId);
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const v = vp.current, p = pointer.current;
    if (sketchRef.current) {                           // sketching: where a click would put its point
      if (p) return;
      const x = e.clientX, y = e.clientY;
      cancelAnimationFrame(hoverFrame.current);
      hoverFrame.current = requestAnimationFrame(() => {
        const at = sketchAt(x, y);
        setHoverPt((cur) => (at && cur && cur.snapped === at.snapped && cur.p[0] === at.p[0] && cur.p[1] === at.p[1] ? cur : at));
      });
      return;
    }
    if (v && !p && (!tool || tool === "edge")) {        // the line a click would pick, shown
      const x = e.clientX, y = e.clientY;
      cancelAnimationFrame(hoverFrame.current);
      hoverFrame.current = requestAnimationFrame(() => {
        const got = lineUnder(x, y, tool === "edge");
        setHoverEdge((cur) => (got && typeof got !== "string" ? (cur?.part === got.part.id && cur.edge === got.edge ? cur
          : { part: got.part.id, edge: got.edge }) : null));
      });
    }
    if (!v || !p?.body || !p.hit) return;
    if (!start.current) {
      if (Math.hypot(e.clientX - p.x, e.clientY - p.y) < 4) return;
      beginDrag({ z: p.hit.point[2], from: p.hit.point });
    }
    const s = start.current;
    const at = s?.body ? v.onPlane(e.clientX, e.clientY, s.body.z) : null;
    if (!s?.body || !at) return;
    const q = (x: number) => Math.round(x / SNAP) * SNAP;
    v.proxy.position.set(s.pos.x + q(at[0] - s.body.from[0]), s.pos.y + q(at[1] - s.body.from[1]), s.pos.z);
    v.proxy.updateMatrixWorld(true);
    v.movePreview(v.proxy.matrixWorld.clone().multiply(s.matrix.clone().invert()));
  };

  /** The part's own edge nearest a spot on the model - an outline's corners all at once - or why there is none. */
  const edgeOf = (hit: Hit): { part: Part; edge: string } | string => {
    const d = designRef.current;
    const part = d ? find(d.parts, hit.part)?.part : null;
    if (!part) return "";
    const why = edgeless(part);
    if (why) return why;
    if (part.shape === "outline") return { part, edge: "vertical" };
    const near = nearestEdge(part, hit.point);
    return near ? { part, edge: near.edge } : "";
  };

  /** The line under the pointer (0.94.0): one a few pixels from it on the screen (0.95.0: however the face there is
   *  turned - a pocket's wall seen edge-on from above too) and not behind the solid - of the part the pointer is on,
   *  or of a hole: the creases of what it cut, the pocket's inside seen through the hole's ghost. A rounded or cut
   *  line, its band as well. `any` (Pick lines): else the part's nearest line, however far - or why it has none. */
  const lineUnder = (x: number, y: number, any = false, px = 7): { part: Part; edge: string } | string | null => {
    const v = vp.current, d = designRef.current;
    if (!v || !d || v.overGizmo) return null;
    const { front, solid } = v.picks(x, y);
    if (!front) return null;
    const { origin, dir } = v.rayAt(x, y);
    const parts = new Set<Part>();
    for (const h of [front, solid]) {
      const p = h ? find(d.parts, h.part)?.part : null;
      if (p) parts.add(p);
    }
    for (const p of cutters(d.parts)) parts.add(p);
    const near: { part: Part; edge: string; off: number; at: Vec3; slack: number }[] = [];
    for (const part of parts) {
      if (edgeless(part)) continue;
      const plan = edgePlan(part);
      for (const edge of EDGES[part.shape] ?? []) {
        const n = nearestToRay(edgeLines(part, [edge]), origin, dir);
        if (!n) continue;
        const per = v.mmPerPx(n.point), size = plan[edge]?.size ?? 0;
        const off = Math.max(0, n.distance - size) / per;
        if (off <= px) near.push({ part, edge, off, at: n.point, slack: Math.min(2 * per, 1) + 1.5 * size });
      }
    }
    near.sort((a, b) => a.off - b.off);
    const got = near.find((n) => v.seen(n.at, n.slack));          // the nearest one not behind the solid
    if (got) return { part: got.part, edge: got.edge };
    const h = solid ?? front;
    return any ? edgeOf(h) : null;
  };

  /** A line picked (0.92.0, 0.94.0): alone - or, `add` (Shift, Select multiple, Pick lines), with the others, or unpicked.
   *  A line that has a fillet or chamfer already puts its size in the size field: changing that resizes it. Its part
   *  is the one picked, and the Lines tab shows. */
  const pickLine = ({ part, edge }: { part: Part; edge: string }, add: boolean) => {
    const had = !!edgeSel[part.id]?.includes(edge);
    setEdgeSel((cur) => {
      if (!add) return { [part.id]: [edge] };
      const list = cur[part.id] ?? [];
      const next = list.includes(edge) ? list.filter((e) => e !== edge) : [...list, edge];
      const out = { ...cur };
      if (next.length) out[part.id] = next;
      else delete out[part.id];
      return out;
    });
    const now = edgePlan(part)[edge];
    if (now && (!add || !had)) setEdgeSize(now.size);
    setSelected([part.id]);
    setFaceSel(null);
    setTab("lines");
  };

  /** Pick lines (0.92.0): a click picks the line nearest it - however far - or unpicks it. */
  const pickEdge = (x: number, y: number) => {
    const got = lineUnder(x, y, true);
    if (!got || typeof got === "string") {
      if (got) toast(got, "fault");
      return;
    }
    pickLine(got, true);
  };

  // Resize (0.94.0): the one picked line made as long as you type - a box over the view beside it; Enter sets it.
  const [resizing, setResizing] = useState<{ part: string; edge: string; length: number; what: string; x: number;
    y: number } | null>(null);
  const openResize = () => {
    const d = designRef.current, v = vp.current, el = host.current;
    const picked = Object.entries(edgeSel).flatMap(([id, es]) => es.map((e) => [id, e] as const));
    if (!d || picked.length !== 1) return;
    const [id, edge] = picked[0];
    const f = find(d.parts, id);
    const len = f ? edgeLength(f.part, edge) : null;
    if (!f || !len) {
      toast("That line has no length of its own to set.", "fault");
      return;
    }
    const line = edgeLines(f.part, [edge])[0] ?? [];
    const mid: Vec3 | undefined = line.length === 2 ? [(line[0][0] + line[1][0]) / 2, (line[0][1] + line[1][1]) / 2,
      (line[0][2] + line[1][2]) / 2] : line[Math.round((line.length - 1) * 0.75)];          // a rim: its front
    const w = el?.clientWidth ?? 400, h = el?.clientHeight ?? 300;
    const at = mid && v ? v.toScreen(mid) : null;
    const x = Math.max(8, Math.min(w - 196, (at?.[0] ?? w / 2) + 14)), y = Math.max(60, Math.min(h - 70, (at?.[1] ?? h / 2) - 20));
    setResizing({ part: id, edge, length: len.length, what: len.what, x, y });
  };
  const applyResize = (raw: string) => {
    const r = resizing;
    setResizing(null);
    const d = designRef.current;
    const f = r && d ? find(d.parts, r.part) : null;
    const v = Number(raw.trim().replace(",", "."));
    if (!r || !f || !Number.isFinite(v) || Math.abs(v - r.length) < 1e-9) return;
    const next = withEdgeLength(f.part, r.edge, v);
    if (typeof next === "string") toast(next, "fault");
    else change(r.part, () => next);
  };

  /** The picked edges given a fillet or chamfer of the size field's size, or none. A hole's (0.95.0): those inside
   *  the part it cuts - its inside corners - not those at an opening, where rounding the hole would leave a lip. */
  const treatPicked = (kind: EdgeKind | null, size = edgeSize) => {
    const d = designRef.current;
    if (!d || !Object.keys(edgeSel).length) return;
    let ps = d.parts;
    const left: string[] = [];
    for (const [id, edges] of Object.entries(edgeSel)) {
      const f = find(d.parts, id);
      if (!f || edgeless(f.part)) continue;                       // not a part rounded all round since
      let these = edges;
      if (kind && cuts(d.parts, id)) {
        if (!built) {
          toast("The design is not built yet - try again in a moment.", "fault");
          return;
        }
        const split = holeEdges(f.part, edges, built);
        these = split.inside;
        if (split.open.length) left.push(`${label(f.part)}: ${split.open.join(", ")}`);
      }
      if (these.length) ps = update(ps, id, (p) => tidy(treatEdges(p, these, kind ? { kind, size } : null)));
    }
    if (ps !== d.parts) commit({ ...d, parts: ps });
    if (left.length) {
      toast(`Left sharp - at an opening, where a ${kind} on the hole would leave a lip (that comes with fillets where`
        + ` parts meet): ${left.join("; ")}.`, "fault");
    }
  };

  /** A new size for the picked edges that have a fillet or chamfer: each keeps its kind. */
  const resizePicked = (size: number) => {
    setEdgeSize(size);
    const d = designRef.current;
    if (!d) return;
    let ps = d.parts, changed = false;
    for (const [id, edges] of Object.entries(edgeSel)) {
      const f = find(ps, id);
      if (!f || edgeless(f.part)) continue;
      const plan = edgePlan(f.part);
      for (const kind of ["fillet", "chamfer"] as const) {
        const these = edges.filter((e) => plan[e]?.kind === kind);
        if (!these.length) continue;
        ps = update(ps, id, (p) => tidy(treatEdges(p, these, { kind, size })));
        changed = true;
      }
    }
    if (changed) commit({ ...d, parts: ps });
  };

  /** The edges a name means picked - or, all of them picked already, unpicked. */
  const pickEdges = (p: Part, key: string) => setEdgeSel((cur) => {
    const es = edgesOf(p.shape, key), list = cur[p.id] ?? [];
    const next = es.every((e) => list.includes(e)) ? list.filter((e) => !es.includes(e)) : [...new Set([...list, ...es])];
    const out = { ...cur };
    if (next.length) out[p.id] = next;
    else delete out[p.id];
    return out;
  });

  /** Letters apart (0.92.0): the text as one part per letter, where they were - all picked. */
  const splitLetters = (id: string) => {
    const d = designRef.current, kk = kit.current;
    const f = d ? find(d.parts, id) : null;
    if (!d || !kk || !f || f.part.shape !== "text") return;
    let letters = lettersApart(f.part, kk.fonts, allIds(d.parts));
    if (!letters) {
      toast("Its font is not loaded yet - try again in a moment.", "fault");
      return;
    }
    const on = f.part.on, box = on ? find(d.parts, on.part)?.part : null;     // a text on a face: each letter on it
    if (on && box) letters = letters.map((l) => respot({ ...l, on: { ...on } }, box));
    commit({ ...d, parts: replaceWith(d.parts, id, letters) });
    setSelected(letters.map((l) => l.id));
    setTab("text");
    host.current?.focus({ preventScroll: true });     // its button is gone: Esc and the arrow keys work on
    toast(`${letters.length} letters, each a part of its own: click one to change it - Select multiple for more.`, "run");
  };

  /** One change for several parts at once (the Text tab for several texts). */
  const changeEach = (ids: string[], fn: (p: Part) => Part) => {
    const d = designRef.current;
    if (!d) return;
    let ps = d.parts;
    for (const id of ids) ps = update(ps, id, (p) => tidy(fn(p)));
    commit({ ...d, parts: ps });
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const v = vp.current, p = pointer.current;
    pointer.current = null;
    if (!v || !p) return;
    v.controls.enabled = true;
    if (start.current?.body) {
      finishDrag();
      return;
    }
    if (Math.hypot(e.clientX - p.x, e.clientY - p.y) > 4) return;          // the view was turned: not a click
    if (sketchRef.current) {
      const at = sketchAt(e.clientX, e.clientY);
      if (at) {
        setHoverPt(at);
        sketchClick(at.p);
      }
      return;
    }
    const hit = p.hit;
    if (tool === "point") {
      if (hit) insertPoint(hit);
      setTool(null);
      return;
    }
    if (tool === "flat") {
      if (hit) layFlat(hit);
      return;
    }
    if (tool === "face") {
      placeOnFace(e.clientX, e.clientY);
      return;
    }
    if (tool === "edge") {
      if (hit) pickEdge(e.clientX, e.clientY);
      return;
    }
    if (!hit) {
      if (!e.shiftKey && !several) {
        setSelected([]);
        setEdgeSel({});
        setFaceSel(null);
      }
      return;
    }
    const line = lineUnder(e.clientX, e.clientY);      // a line: it, and the Lines tab (0.94.0) - a hole's too (0.95.0)
    if (line && typeof line !== "string") {
      pickLine(line, e.shiftKey || several);
      return;
    }
    const id = topOf(hit.part);                        // a part: it, and the Part tab
    setFaceSel(null);
    if (e.shiftKey || several) {
      const cur = selectedRef.current;
      setSelected(cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]);
    } else {
      setSelected([id]);
      setEdgeSel({});
      // A box's face (0.98.0, Søren: "på Select, og jeg klikker på en flade (ikke en linie), så skal muligheden komme
      // frem oppe i menuen"): the spot clicked on it, and the Face tab - a hole, a boss or a text there.
      const box = mode === "select" ? solidBox(hit.part) : null, face = box ? faceAt(box, hit.point, hit.normal) : null;
      if (box && face) {
        setFaceSel({ part: box.id, face, from: spotAt(box, face, hit.point) });
        setTab("face");
        return;
      }
    }
    const text = designRef.current ? find(designRef.current.parts, id)?.part.shape === "text" : false;
    setTab(text ? "text" : "part");                    // a text: its font and size first (0.96.0), as in Office
  };

  /** A part picked in the tree (0.96.1): as a click on it on the model - alone, or one more with Shift or Select
   *  multiple - its tab opening (a text's: Text), but Lines stays: there a part is picked for its lines by name. */
  const pickFromTree = (id: string, add: boolean) => {
    const cur = selectedRef.current;
    setFaceSel(null);
    if (add || several) setSelected(cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]);
    else {
      setSelected([id]);
      setEdgeSel({});
    }
    const p = designRef.current ? find(designRef.current.parts, id)?.part : null;
    setTab((t) => (t === "lines" ? t : p?.shape === "text" ? "text" : "part"));
  };

  const onDoubleClick = (e: React.MouseEvent) => {        // the part itself, inside its group
    const v = vp.current;
    if (!v || tool || v.overGizmo) return;
    const hit = v.pick(e.clientX, e.clientY);
    if (hit) setSelected([hit.part]);
  };

  // ---------------------------------------------------------------- changes

  const parts = design?.parts ?? [];
  const items = pick(parts, selected);
  const one = items.length === 1 ? items[0] : null;
  const k = kit.current;

  /** The part moved up or down so its lowest point is on the plate - from what it makes, not its box. */
  function dropped(p: Part, kk: Kit): Part {
    const b = buildPart(p, kk).box;
    const low = b ? b[0][2] : extent(p)?.[0][2] ?? 0;
    return transformPart(p, [0, 0, 0], [0, 0, -low], null);
  }

  const boxOfItems = (ps: Part[]): Box3 | null => {
    const kk = kit.current;
    return union(ps.map((p) => (kk ? buildPart(p, kk).box : null) ?? extent(p)));
  };

  const change = (id: string, fn: (p: Part) => Part) => {
    const d = designRef.current;
    if (d) commit({ ...d, parts: update(d.parts, id, (p) => tidy(fn(p))) });
  };

  const add = (shape: Shape, hole = false) => {
    const d = designRef.current, kk = kit.current;
    if (!d || !kk) return;
    const seen = allIds(d.parts);
    const id = freshId(seen);
    const size: Vec3 = hole ? (shape === "cylinder" ? [6, 6, 22] : [10, 10, 22]) : shape === "text" ? [0, 10, 2] : [20, 20, 20];
    const p: Part = { id, name: hole ? (shape === "cylinder" ? "Round hole" : "Hole") : undefined, shape, size,
      at: [0, 0, 0], rotate: [0, 0, 0], hole: hole || undefined, color: hole ? undefined : COLORS[seen.size % COLORS.length] };
    if (shape === "cylinder" || shape === "cone" || shape === "sphere") p.sides = 64;
    if (shape === "cone") p.top = 0;
    if (shape === "text") p.text = "Text";
    // Where: a hole through the one part picked; anything else beside what is there, on the plate.
    const picked = pick(d.parts, selectedRef.current);
    const through = hole && picked.length === 1 ? boxOfItems(picked) : null;
    if (through) {
      const c = centre(through);
      size[2] = r3(through[1][2] - through[0][2] + 2);
      p.at = [r3(c[0]), r3(c[1]), r3(c[2])];
    } else {
      const all = built?.box ?? extentOf(d.parts);
      const w = sized(p, kk.fonts)[0];
      const x = all ? Math.ceil(all[1][0] + 5 + w / 2) : 0;
      const fits = Math.abs(x) + w / 2 <= printer.bed[0] / 2;
      p.at = [fits ? x : 0, all && fits ? Math.round((all[0][1] + all[1][1]) / 2) : 0, hole ? size[2] / 2 - 1 : size[2] / 2];
    }
    shown3d.current = [id];
    commit({ ...d, parts: [...d.parts, p] });
    setSelected([id]);
    setEdgeSel({});
    setFaceSel(null);
    if (shape === "text") {
      setTab("text");                              // its font and size, as Office shows them for a new text box (0.96.0)
      if (d.parts.some((x) => !x.hidden && !x.hole)) setTool("face");      // and the face it goes on (0.97.0)
    }
  };

  const addMesh = async (file: string) => {
    if (!designRef.current) return;
    const rel = relativePath(dirOf(designPath.current), file);
    try {
      let m = meshes.current.get(rel);
      if (!m || typeof m === "string") {
        m = await fetchMesh(file);
        meshes.current.set(rel, m);
      }
      const size = meshSize(m);
      const cur = designRef.current!;
      const id = freshId(allIds(cur.parts));
      shown3d.current = [id];
      commit({ ...cur, parts: [...cur.parts, { id, name: baseName(file), shape: "mesh", file: rel, size,
        at: [0, 0, r3(size[2] / 2)], rotate: [0, 0, 0], color: COLORS[cur.parts.length % COLORS.length] }] });
      setSelected([id]);
      setMeshTick((t) => t + 1);
    } catch (e) {
      toast(`Could not read ${file}: ${(e as Error).message}`, "fault");
    }
  };

  const upload = async (file: File) => {
    try {
      await addMesh(await store.put(file, file.name, dirOf(designPath.current)));
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  const del = () => {
    const d = designRef.current;
    if (!d || !selectedRef.current.length) return;
    commit({ ...d, parts: remove(d.parts, new Set(selectedRef.current)) });
    setSelected([]);
  };

  const duplicate = () => {
    const d = designRef.current;
    if (!d || !selectedRef.current.length) return;
    const seen = allIds(d.parts), ids = new Map<string, string>();
    const moved = onCopies(pick(d.parts, selectedRef.current).map((p) => copy(p, seen, ids)), ids)
      .map((p) => tidy(transformPart(p, [0, 0, 0], [5, 5, 0], null)));
    // A copy on a face (0.98.0) is where it was put - on its box's copy, or beside the part it copies on the same box.
    const all = [...d.parts, ...moved];
    const spot = (p: Part): Part => {
      if (p.parts) return { ...p, parts: p.parts.map(spot) };
      const box = p.on ? find(all, p.on.part)?.part : null;
      return box ? respot(p, box) : p;
    };
    const copies = moved.map(spot);
    shown3d.current = copies.map((c) => c.id);
    commit({ ...d, parts: [...d.parts, ...copies] });
    setSelected(copies.map((c) => c.id));
  };

  const doGroup = () => {
    const d = designRef.current;
    if (!d || selectedRef.current.length < 2) return;
    const g = group(d.parts, selectedRef.current);
    if (!g) {
      toast("Only parts side by side - at the top, or in the same group - can be grouped.", "fault");
      return;
    }
    commit({ ...d, parts: g.parts });
    setSelected([g.id]);
  };

  const doUngroup = () => {
    const d = designRef.current;
    const g = d && selectedRef.current.length === 1 ? find(d.parts, selectedRef.current[0])?.part : null;
    if (!d || !g || g.shape !== "group") return;
    commit({ ...d, parts: ungroup(d.parts, g.id) });
    setSelected((g.parts ?? []).map((p) => p.id));
  };

  const mirror = (axis: number) => {
    const d = designRef.current;
    const list = d ? pick(d.parts, selectedRef.current) : [];
    if (!d || !list.length) return;
    const b = boxOfItems(list);
    const c: Vec3 = b ? centre(b) : [0, 0, 0];
    let ps = d.parts;
    for (const it of list) ps = update(ps, it.id, (p) => tidy(mirrorPart(p, axis, c)));
    commit({ ...d, parts: ps });
  };

  const drop = () => {
    const d = designRef.current, kk = kit.current;
    const list = d ? pick(d.parts, selectedRef.current) : [];
    if (!d || !kk || !list.length) return;
    let ps = d.parts;
    for (const it of list) ps = update(ps, it.id, (p) => tidy(dropped(p, kk)));
    commit({ ...d, parts: ps });
  };

  const align = (axis: number, where: 0 | 1 | 2) => {
    const d = designRef.current;
    const list = d ? pick(d.parts, selectedRef.current) : [];
    const all = boxOfItems(list);
    if (!d || list.length < 2 || !all) return;
    const at = (b: Box3) => (where === 0 ? b[0][axis] : where === 2 ? b[1][axis] : (b[0][axis] + b[1][axis]) / 2);
    let ps = d.parts;
    for (const it of list) {
      const b = boxOfItems([it]);
      if (!b) continue;
      const move: Vec3 = [0, 0, 0];
      move[axis] = at(all) - at(b);
      ps = update(ps, it.id, (p) => tidy(transformPart(p, [0, 0, 0], move, null)));
    }
    commit({ ...d, parts: ps });
  };

  const setPrinter = (p: Printer) => {
    const d = designRef.current;
    if (!d) return;
    rememberPrinter(p);
    commit({ ...d, printer: p.id, bed: [...p.bed] as Vec3 });
  };

  // ---------------------------------------------------------------- the slicer's files

  const exportAs = async (kind: "stl" | "3mf", where: "download" | "folder") => {
    if (!built || (!built.triangles.length && !built.loose.length)) {
      toast("The design makes nothing to print yet.", "fault");
      return;
    }
    const mesh = joined(built);
    const stem = baseName(shown).replace(/\.3d\.json$/i, "") || "design";
    const bytes = kind === "stl" ? stl(mesh, stem) : threeMf(mesh, stem);
    const name = `${stem}.${kind}`;
    if (where === "download") {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([bytes as BlobPart], { type: kind === "stl" ? "model/stl" : "model/3mf" }));
      a.download = name;
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      return;
    }
    try {
      const saved = await store.put(new Blob([bytes as BlobPart], { type: kind === "stl" ? "model/stl" : "model/3mf" }), name,
        dirOf(designPath.current));
      toast(`Saved ${saved} in ${store.where}`, "run");
    } catch (e) {
      toast((e as Error).message, "fault");
    }
  };

  // ---------------------------------------------------------------- keys

  const onKey = (e: React.KeyboardEvent | KeyboardEvent) => {
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !designRef.current) return;
    if (sketchRef.current) {                           // sketching: its keys only (0.100.0)
      sketchKey(e, sketchRef.current);
      return;
    }
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();
    const step = SNAP * (e.shiftKey ? 10 : 1);
    const nudge = (m: Vec3) => {
      const d = designRef.current;
      if (!d || !selectedRef.current.length) return;
      let ps = d.parts;
      for (const id of selectedRef.current) ps = update(ps, id, (p) => tidy(transformPart(p, [0, 0, 0], m, null)));
      commit({ ...d, parts: ps });
    };
    const plain: Record<string, () => void> = {
      Delete: del, Backspace: del, Escape: () => {
        if (host.current?.closest(".panel-3d")?.querySelector(".menu")) return;   // it closes the menu, nothing more
        if (tool) setTool(null);
        else if (selectedRef.current.length || Object.keys(edgeSel).length || faceSel) {
          setSelected([]);
          setEdgeSel({});
          setFaceSel(null);
        } else setMode("select");                     // nothing picked: back to Select
      },
      ArrowLeft: () => nudge([-step, 0, 0]), ArrowRight: () => nudge([step, 0, 0]),
      ArrowUp: () => nudge([0, step, 0]), ArrowDown: () => nudge([0, -step, 0]),
      PageUp: () => nudge([0, 0, step]), PageDown: () => nudge([0, 0, -step]),
      g: () => setMode("translate"), r: () => setMode("rotate"), s: () => setMode("scale"), q: () => setMode("select"),
      h: () => { if (one) change(one.id, (p) => ({ ...p, hole: !p.hole || undefined })); },
    };
    let run: (() => void) | undefined;
    if (mod && key === "z") run = e.shiftKey ? redo : undo;
    else if (mod && key === "y") run = redo;
    else if (mod && key === "d") run = duplicate;
    else if (mod && key === "g") run = e.shiftKey ? doUngroup : doGroup;
    else if (mod && key === "a") run = () => setSelected((designRef.current?.parts ?? []).map((p) => p.id));
    else if (!mod && !e.altKey) run = plain[e.key] ?? plain[key];
    if (run) {
      e.preventDefault();
      run();
    }
  };

  // Keys with nothing focused (0.97.0): a button that went away under the pointer - Text in Insert when the Text tab
  // opens, a menu's item - leaves the page focused, not the editor; Esc, Delete and the arrows still work then - when
  // the last click was in the editor (not in the chat, where they are the page's).
  const keys = useRef(onKey);
  keys.current = onKey;
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    let inside = false;
    const down = (e: PointerEvent) => { inside = !!panel.current && e.target instanceof Node && panel.current.contains(e.target); };
    const on = (e: KeyboardEvent) => {
      if (inside && (!document.activeElement || document.activeElement === document.body)) keys.current(e);
    };
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("keydown", on);
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("keydown", on);
    };
  }, []);

  // ---------------------------------------------------------------- what is shown

  const box: Box3 | null = built?.box ?? null;
  const size = box ? box[1].map((v, i) => v - box[0][i]) : null;
  const bed = printer.bed;
  const tooBig = !!box && (box[0][0] < -bed[0] / 2 - 0.01 || box[1][0] > bed[0] / 2 + 0.01
    || box[0][1] < -bed[1] / 2 - 0.01 || box[1][1] > bed[1] / 2 + 0.01 || box[1][2] > bed[2] + 0.01);
  const below = !!box && box[0][2] < -0.01;
  const floating = !!box && box[0][2] > 0.01;
  const saying = { loading: "Loading", new: "Not saved yet", saved: "Saved", saving: "Saving", unsaved: "Not saved yet",
    error: "Not saved" }[status];
  const view = (name: ViewName) => vp.current?.view(name, built?.box ?? (design ? extentOf(design.parts) : null));
  const texts = items.length && items.every((p) => p.shape === "text") ? items : null;
  const shownTab: Tab = sketch ? "sketch" : (tab === "text" && !texts) || (tab === "face" && !faceBox) || tab === "sketch"
    ? "part" : tab;
  const pickedEdges = Object.entries(edgeSel).reduce((n, [id, es]) => n + (find(parts, id) ? es.length : 0), 0);
  const oneLine = (() => {                             // the one line picked: how long it is (Resize)
    const all = Object.entries(edgeSel).flatMap(([id, es]) => es.map((e) => [id, e] as const));
    const f = all.length === 1 ? find(parts, all[0][0]) : null;
    return f ? edgeLength(f.part, all[0][1]) : null;
  })();
  // What the band shows: a change folds its groups afresh to the room there is (0.94.0).
  const fitKey = [shownTab, faceSel?.face, feature.round, feature.through, one?.on ? `${one.on.face}${one.on.through ? "t" : ""}` : "",
    sketch ? `${sketch.picked !== null ? sketch.shapes[sketch.picked]?.kind : "-"}${sketchMake.through}${sketch.shapes.length > 0}` : "",
    selected.join(","), items.map((x) => `${x.shape}${x.radius ? "r" : ""}${x.repeat ? (x.repeat.step ? "s" : "a")
    : ""}${x.hole ? "h" : ""}`).join(","), pickedEdges, one ? (edgeSel[one.id] ?? []).length : 0, oneLine ? 1 : 0].join("|");
  const allProblems = [...problems, ...(built?.problems ?? [])];
  const hide = (id: string) => change(id, (p) => ({ ...p, hidden: !p.hidden || undefined }));
  const pickFirst = <p className="v3d-tip">Click a part on the model to change it - a box's face, to put a hole, a boss or
    a text on it - or a line, to round, cut or resize that line. Shift-click (or Select multiple) picks more, a double
    click picks a part inside a group; Move, Turn and Size put handles on what is picked.</p>;

  return (
    <aside ref={panel} className="panel panel-3d" aria-label="3D editor" onKeyDown={onKey}>
      <div className="panel-head v3d-head">
        <Box size={17} className="faint" />
        <h2 title={shown}>{baseName(shown) || "New design"}</h2>
        <span className={`tag${status === "error" ? " fault" : status === "saved" ? " ok" : ""}`} role="status">{saying}</span>
        <button className="btn small quiet" onClick={undo} disabled={!past.current.length} title="Undo (Ctrl+Z)"><Undo2 size={15} /> Undo</button>
        <button className="btn small quiet" onClick={redo} disabled={!future.current.length} title="Redo (Ctrl+Shift+Z)"><Redo2 size={15} /> Redo</button>
        <MenuButton right className="btn small" title="Files for the slicer" disabled={!built}
          label={<><Download size={15} /> Export <ChevronDown size={13} /></>}>
          {(close) => (
            <>
              <button className="menu-item" onClick={() => { close(); void exportAs("3mf", "download"); }}>
                <span>Download 3MF<small>Opens in Bambu Studio, in millimetres</small></span></button>
              <button className="menu-item" onClick={() => { close(); void exportAs("stl", "download"); }}>
                <span>Download STL</span></button>
              <div className="menu-sep" />
              <button className="menu-item" onClick={() => { close(); void exportAs("3mf", "folder"); }}>
                <span>Save 3MF in {store.where}<small>Beside the design, where the assistant sees it too</small></span></button>
              <button className="menu-item" onClick={() => { close(); void exportAs("stl", "folder"); }}>
                <span>Save STL in {store.where}</span></button>
            </>
          )}
        </MenuButton>
        <button className="btn small quiet" onClick={onMax} title={max ? "Show the chat beside the editor" : "Use the whole width"}>
          {max ? <><Minimize2 size={15} /> Show the chat</> : <><Maximize2 size={15} /> Full width</>}</button>
        <button className="btn small quiet" onClick={onClose} title="Close the 3D editor"><X size={15} /> Close</button>
      </div>
      {outside !== null && (
        <div className="v3d-bar" role="alert">
          <span className="grow">The design was changed in {store.where} - by the assistant? - while you had changes here.</span>
          <button className="btn small primary" onClick={() => { const t = outside; setOutside(null); adopt(t, true); }}>Load it</button>
          <button className="btn small" onClick={() => { saved.current = outside; setOutside(null); void save(); }}>Keep mine</button>
        </div>
      )}
      {status === "new" && (
        <div className="v3d-bar">
          <span className="grow">A new design with {baseName(path)} in it. The first change saves it as {baseName(shown)}.</span>
        </div>
      )}
      <div className="v3d-ribbon">
        <div className="v3d-tabs">
          <div className="v3d-tablist" role="tablist" aria-label="Tools">
            {!sketch && TABS.map(([id, name]) => (
              <button key={id} type="button" role="tab" aria-selected={shownTab === id} className={`v3d-tab${shownTab === id ? " on" : ""}`}
                onClick={() => setTab(id)}>{name}</button>
            ))}
            {sketch && (
              <button type="button" role="tab" aria-selected className="v3d-tab context on"
                title={`Sketching on ${planeName(sketch.plane, sketchFrame(sketch)?.box)} - Extrude, Cut or Cancel leaves it`}>Sketch</button>
            )}
            {texts && (
              <button type="button" role="tab" aria-selected={shownTab === "text"} className={`v3d-tab context${shownTab === "text" ? " on" : ""}`}
                onClick={() => setTab("text")} title={texts.length > 1 ? "The picked texts: their font and letters"
                  : "The picked text: its letters, font and lines"}>Text</button>
            )}
            {faceBox && faceSel && (
              <button type="button" role="tab" aria-selected={shownTab === "face"} className={`v3d-tab context${shownTab === "face" ? " on" : ""}`}
                onClick={() => setTab("face")} title={`The face picked - the ${faceSel.face} of ${label(faceBox)}: a hole, a boss or a text on it`}>Face</button>
            )}
          </div>
        </div>
        <Band label={shownTab === "text" ? "Text" : shownTab === "face" ? "Face" : shownTab === "sketch" ? "Sketch"
          : TABS.find(([id]) => id === shownTab)?.[1]} fit={fitKey}>
          {shownTab === "sketch" && sketch && (
            <SketchBand sketch={sketch} where={planeName(sketch.plane, sketchFrame(sketch)?.box)} make={sketchMake}
              onMake={(m) => setSketchMake((cur) => ({ ...cur, ...m }))} closed={sketchRegions(sketch.shapes).length}
              onTool={(t) => setSketch({ ...sketch, tool: t, draft: null, typed: "", picked: null })}
              onDelete={() => sketch.picked !== null && setSketch({ ...sketch, shapes: sketch.shapes.filter((_, i) => i !== sketch.picked),
                past: [...sketch.past, sketch.shapes], picked: null })}
              onUndo={() => sketch.past.length && setSketch({ ...sketch, shapes: sketch.past[sketch.past.length - 1],
                past: sketch.past.slice(0, -1), draft: null, typed: "", picked: null })}
              onShape={(sh) => sketch.picked !== null && setSketch({ ...sketch, shapes: sketch.shapes.map((x, i) => (i === sketch.picked ? sh : x)),
                past: [...sketch.past, sketch.shapes] })}
              onExtrude={() => endSketch(false)} onCut={() => endSketch(true)} onCancel={cancelSketch} />
          )}
          {shownTab === "insert" && (
            <>
              <Grp label="Solids">
                <Big tone="orange" icon={<Box size={20} />} label="Box" onClick={() => add("box")} />
                <Big tone="orange" icon={<Cylinder size={20} />} label="Cylinder" onClick={() => add("cylinder")} />
                <Big tone="orange" icon={<Cone size={20} />} label="Cone" onClick={() => add("cone")} />
                <Big tone="orange" icon={<CircleDot size={20} />} label="Sphere" onClick={() => add("sphere")} />
                <Big tone="orange" icon={<Triangle size={20} />} label="Wedge" title="A wedge: a ramp" onClick={() => add("wedge")} />
                <Big tone="violet" icon={<Type size={20} />} label="Text" title="A text - its font and size in the Text tab"
                  onClick={() => add("text")} />
              </Grp>
              <Grp label="Holes" fold={2} icon={<SquareDashed size={20} />}>
                <Big tone="grey" icon={<SquareDashed size={20} />} label="Hole" title="A box-shaped hole - through the part picked" onClick={() => add("box", true)} />
                <Big tone="grey" icon={<Cylinder size={20} strokeDasharray="3 2" />} label="Round hole" title="A round hole - through the part picked"
                  onClick={() => add("cylinder", true)} />
              </Grp>
              <Grp label="Sketch" fold={3} icon={<PencilRuler size={20} />}>
                <SketchButton onPick={startSketch} />
              </Grp>
              <Grp label="From a file" fold={1} icon={<Upload size={20} />}>
                <MenuButton fixed className="btn v3d-big tone-green" title="An STL, 3MF or OBJ into the design" menuClassName="wide"
                  label={<><Upload size={20} /><span>Import <ChevronDown size={11} /></span></>}>
                  {(close) => <FolderMeshes store={store} onPick={(f) => { close(); void addMesh(f); }}
                    onUpload={(f) => { close(); void upload(f); }} />}
                </MenuButton>
              </Grp>
            </>
          )}
          {shownTab === "part" && (
            <>
              {!items.length && pickFirst}
              {items.length > 1 && (
                <>
                  <Grp label={`${items.length} parts picked`}>
                    <Big icon={<Copy size={20} />} label="Duplicate" title="Duplicate them (Ctrl+D)" onClick={duplicate} />
                    <ConfirmButton className="btn v3d-big tone-red" confirmText="Delete?" label="Delete them" onConfirm={del}>
                      <Trash2 size={20} /><span>Delete</span></ConfirmButton>
                  </Grp>
                  <Grp label="Plate" fold={4} icon={<ArrowDownToLine size={20} />}>
                    <Big icon={<ArrowDownToLine size={20} />} label="Onto the plate" title="Each moved up or down so its lowest point is on the plate" onClick={drop} />
                  </Grp>
                  <Grp label="Arrange" fold={3} icon={<Group size={20} />}>
                    <Big icon={<Group size={20} />} label="Group" title="Group them (Ctrl+G): a hole in a group cuts only its parts" onClick={doGroup} />
                    <TurnButton onTurn={turn90} />
                    <MirrorButton onMirror={mirror} />
                  </Grp>
                  <Grp label="Align" fold={2} icon={<AlignCenter size={20} />}>
                    <div className="v3d-aligngrid">
                      {ALIGNS.map(([ax, names], i) => (
                        <Fragment key={ax}>
                          <span className={`v3d-ax ax${i}`}>{ax}</span>
                          {names.map((n, j) => (
                            <button key={n} type="button" className="v3d-small" title={`Line them up: ${n.toLowerCase()} (${ax})`}
                              onClick={() => align(i, j as 0 | 1 | 2)}><span>{n}</span></button>))}
                        </Fragment>
                      ))}
                    </div>
                  </Grp>
                </>
              )}
              {one && k && <PartBand part={one} fonts={k.fonts} lock={lock} setLock={setLock} change={(fn) => change(one.id, fn)}
                onDuplicate={duplicate} onDelete={del} onApart={() => splitLetters(one.id)} onDrop={drop}
                onFlat={() => setTool(tool === "flat" ? null : "flat")} flat={tool === "flat"} onUngroup={doUngroup} onMirror={mirror}
                onFace={() => setTool(tool === "face" ? null : "face")} face={tool === "face"} onTurn={turn90}
                box={one.on ? find(parts, one.on.part)?.part ?? null : null} depth={feature.depth} />}
            </>
          )}
          {shownTab === "text" && texts && k && <TextBand parts={texts} fonts={k.fonts} onApart={() => splitLetters(texts[0].id)}
            onFace={() => setTool(tool === "face" ? null : "face")} face={tool === "face"}
            change={(fn) => (texts.length > 1 ? changeEach(texts.map((t) => t.id), fn) : change(texts[0].id, fn))} />}
          {shownTab === "face" && faceBox && faceSel && (
            <FaceBand box={faceBox} spot={faceSel} feature={feature} onFeature={(f) => setFeature((cur) => ({ ...cur, ...f }))}
              onFrom={(from) => setFaceSel({ ...faceSel, from })} onMake={onTheFace}
              onSketch={() => startSketch({ kind: "face", part: faceBox.id, face: faceSel.face })} />
          )}
          {shownTab === "lines" && (
            <LinesBand part={one} many={items.length > 1} picked={pickedEdges} pickedOn={one ? edgeSel[one.id] ?? [] : []}
              size={edgeSize} picking={tool === "edge"} onPicking={() => setTool(tool === "edge" ? null : "edge")}
              onName={(key) => { if (one) pickEdges(one, key); }}
              onChip={(key, v) => { if (one) { pickEdges(one, key); setEdgeSize(v); } }}
              onTreat={(kind) => treatPicked(kind)} onSize={resizePicked} onClear={() => setEdgeSel({})}
              length={oneLine} onResize={openResize} cutter={!!one && !!design && !!cuts(design.parts, one.id)} />
          )}
        </Band>
      </div>
      <div className="v3d-stage">
        <div className={`v3d-view${tool ? " picking" : ""}`} ref={host} tabIndex={0} onPointerDown={onPointerDown}
          onPointerMove={onPointerMove} onPointerUp={onPointerUp} onDoubleClick={onDoubleClick}
          onPointerLeave={() => { cancelAnimationFrame(hoverFrame.current); setHoverEdge(null); }}
          aria-label="The design in 3D. Drag to turn the view, right-drag to pan, scroll to zoom. Click a part to pick it, a box's face to put something on it, a line to pick the line." />
        {resizing && (
          <div className="v3d-resize" style={{ left: resizing.x, top: resizing.y }} role="dialog" aria-label={`${resizing.what} of the line`}>
            <label>
              <span>{resizing.what}</span>
              <input className="input mono" autoFocus defaultValue={fmt(resizing.length)} inputMode="decimal"
                aria-label={`${resizing.what}, mm`} onFocus={(e) => e.target.select()} onBlur={() => setResizing(null)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    applyResize(e.currentTarget.value);
                    host.current?.focus({ preventScroll: true });
                  } else if (e.key === "Escape") {
                    e.preventDefault();
                    setResizing(null);
                    host.current?.focus({ preventScroll: true });
                  }
                }} />
              <span>mm</span>
            </label>
            <small>Enter sets it, Esc leaves it</small>
          </div>
        )}
        <div className="v3d-top">
          <div className="v3d-heads-row">
            <div className="v3d-left">
            {sketch && (
              <div className="v3d-heads" role="toolbar" aria-label="Sketch">
                <Tool icon={<MousePointer2 size={16} />} title="Pick a shape: Delete takes it away, its size in the Sketch tab (Esc)"
                  active={!sketch.tool} onClick={() => setSketch({ ...sketch, tool: null, draft: null, typed: "" })}><span>Pick</span></Tool>
                <Tool icon={<PenLine size={16} />} title="Line: click each corner (L)" active={sketch.tool === "line"}
                  onClick={() => setSketch({ ...sketch, tool: "line", draft: null, typed: "", picked: null })}><span>Line</span></Tool>
                <Tool icon={<RectangleHorizontal size={16} />} title="Rectangle: two corners across (R)" active={sketch.tool === "rect"}
                  onClick={() => setSketch({ ...sketch, tool: "rect", draft: null, typed: "", picked: null })}><span>Rectangle</span></Tool>
                <Tool icon={<Circle size={16} />} title="Circle: its middle, then its edge (C)" active={sketch.tool === "circle"}
                  onClick={() => setSketch({ ...sketch, tool: "circle", draft: null, typed: "", picked: null })}><span>Circle</span></Tool>
                <span className="v3d-sep" />
                <Tool icon={<Crosshair size={16} />} title="Look straight at the sketch again" onClick={() => {
                  const at = sketchFrame(sketch), v = vp.current, k = kit.current;
                  if (at && v) v.lookFrom(at.f.N, at.box && k ? buildPart(at.box, k).box : built?.box ?? null);
                }}><span>Look at it</span></Tool>
              </div>
            )}
            {!sketch && (
            <div className="v3d-heads" role="toolbar" aria-label="Pick and handles">
              <Tool icon={<MousePointer2 size={16} />} title="Select: a click picks a part or a line - no handles, nothing moves (Q, or Esc with nothing picked)"
                active={mode === "select"} onClick={() => setMode("select")}><span>Select</span></Tool>
              <Tool icon={<SquareMousePointer size={16} />} active={several} onClick={() => setSeveral(!several)}
                title="Select multiple: each click on the model picks one more part or line - or unpicks it - as Shift-click does"><span>Select multiple</span></Tool>
              <Tool icon={<MessageSquareShare size={16} />} title="Send to chat: click a spot on the model and it goes into your message to the assistant"
                active={tool === "point"} onClick={() => setTool(tool === "point" ? null : "point")}><span>Send to chat</span></Tool>
              <span className="v3d-sep" />
              <Tool icon={<Move3d size={16} />} title="Move: arrows on what is picked move it, 1 mm at a time - or drag it on the plate (G)"
                active={mode === "translate"} onClick={() => setMode("translate")}><span>Move</span></Tool>
              <Tool icon={<Rotate3d size={16} />} title="Turn: rings on what is picked turn it, 15° at a time (R)" active={mode === "rotate"}
                onClick={() => setMode("rotate")}><span>Turn</span></Tool>
              <Tool icon={<Scaling size={16} />} title="Size: handles on what is picked make it longer or shorter, 1 mm at a time (S)"
                active={mode === "scale"} onClick={() => setMode("scale")}><span>Size</span></Tool>
            </div>
            )}
            {parts.length > 0 && !sketch && (
              <PartTree parts={parts} selected={selected} open={treeOpen} onOpen={setTreeOpen} onHide={hide}
                onPickAll={() => { setSelected(parts.map((x) => x.id)); setEdgeSel({}); }} onPick={pickFromTree} />
            )}
            </div>
            <div className="v3d-heads" role="toolbar" aria-label="View">
              <Tool icon={<ViewIcon face="home" />} title="Fit: the whole design, seen from the front, right and above" onClick={() => view("home")}><span>Fit</span></Tool>
              <Tool icon={<ViewIcon face="top" />} title="From the top" onClick={() => view("top")}><span>Top</span></Tool>
              <Tool icon={<ViewIcon face="front" />} title="From the front" onClick={() => view("front")}><span>Front</span></Tool>
              <Tool icon={<ViewIcon face="right" />} title="From the right" onClick={() => view("right")}><span>Right</span></Tool>
            </div>
          </div>
          {sketch && <div className="v3d-hint">{sketchHint(sketch, planeName(sketch.plane, sketchFrame(sketch)?.box))}</div>}
          {tool && <div className="v3d-hint">{tool === "face" ? "Click the face it should lie on - a text's letters on it, upright on a wall. Esc leaves it where it is."
            : tool === "point" ? "Click the spot on the model to send it to the chat. Esc stops."
            : tool === "edge" ? "Click lines to pick them - again to unpick - then Fillet, Chamfer, Take off or Resize above. Esc stops."
              : "Click the face that should lie on the plate. Esc stops."}</div>}
        </div>
        {(failed || !ready || status === "loading") && <div className="v3d-overlay">{failed ?? "Loading the 3D editor"}</div>}
        {ready && !failed && design && !parts.length && (
          <div className="v3d-empty">Add a shape from Insert above, import an STL - or tell the assistant what to make.</div>)}
        <div className="v3d-status num">
          {size ? <span>{fmt(size[0])} x {fmt(size[1])} x {fmt(size[2])} mm</span> : <span>Nothing to print yet</span>}
          {built?.volume ? <span>{(built.volume / 1000).toFixed(1)} cm³</span> : null}
          {design && (
            <MenuButton up className="btn small quiet v3d-printer" title="The printer the design is laid out for"
              label={<><PrinterIcon size={14} /> {printer.name} <ChevronDown size={12} /></>}>
              {(close) => (
                <>
                  {PRINTERS.map((p) => (
                    <button key={p.id} className="menu-item" role="menuitemradio" aria-checked={printer.id === p.id}
                      onClick={() => { close(); setPrinter(p); }}><span>{p.name}<small>{p.bed.map(fmt).join(" x ")} mm</small></span></button>
                  ))}
                  <button className="menu-item" role="menuitemradio" aria-checked={printer.id === "custom"}
                    onClick={() => setPrinter({ id: "custom", name: "Custom size", bed: printer.bed })}>
                    <span>Custom size<small>Its build volume below</small></span></button>
                  {printer.id === "custom" && (
                    <div className="v3d-triple v3d-custom">
                      {["X", "Y", "Z"].map((ax, i) => <Num key={ax} label={ax} value={printer.bed[i]} min={10}
                        onSet={(v) => { const b = [...printer.bed] as Vec3; b[i] = v; setPrinter({ id: "custom", name: "Custom size", bed: b }); }} />)}
                    </div>
                  )}
                </>
              )}
            </MenuButton>
          )}
          {tooBig && <span className="v3d-warn">larger than the plate</span>}
          {below && <span className="v3d-warn">below the plate</span>}
          {floating && <span className="v3d-warn">not on the plate</span>}
          {built?.loose.length ? <span className="v3d-warn">{built.loose.length} not a closed solid</span> : null}
          {built?.rests && built.rests.footprint > 1 && built.rests.contact < 0.4 * built.rests.footprint && (
            <span className="v3d-warn" title="Its first layer is a small share of its shadow from above: round or pointed underneath (a cylinder lying down, a tip). It may come loose - a flat bottom holds.">
              stands on {Math.round((100 * built.rests.contact) / built.rests.footprint)}% of its shadow</span>)}
          {allProblems.length > 0 && (
            <MenuButton up right className="btn small quiet v3d-warn" title="What the design's file or its build says"
              label={<><TriangleAlert size={14} /> {allProblems.length} {allProblems.length > 1 ? "problems" : "problem"}</>}>
              {() => <div className="v3d-problems">{allProblems.map((p, i) => <p key={i}>{p}</p>)}</div>}
            </MenuButton>
          )}
        </div>
      </div>
    </aside>
  );
}

// 0.94.0: Insert (was Shapes); Part - with Size and place and Arrange in it; Lines (was Edges). Søren: "Shapes skal
// hedde Insert ... Parts menuen skal også inkludere Size, place, arrange (alle på en tab) ... Hvis man klikker på en
// streg, så skal menuen Lines komme frem".
type Tab = "insert" | "part" | "lines" | "text" | "face" | "sketch";
/** A box's face clicked (0.98.0): which, and the spot on it - so many mm from its edges (none: the middle). */
type FacePick = { part: string; face: FaceName; from?: Record<string, number> };
/** What the Face tab puts on a face: round or square, how wide; a hole through all or so deep; a boss so high. */
type Feature = { round: boolean; size: number; through: boolean; depth: number; height: number };
/** A sketch (0.100.0): its plane and shapes; the tool drawing (none: a click picks a shape); what is half drawn - a
 *  line's corners so far, a rectangle's first corner, a circle's middle; the shape picked; what is typed for the next
 *  length; what it was before each shape (Undo); its number, for its parts' names. */
type SketchTool = "line" | "rect" | "circle";
type Draft = { kind: "line"; pts: Point2[] } | { kind: "rect"; a: Point2 } | { kind: "circle"; c: Point2 };
type SketchState = { plane: SketchPlane; shapes: SketchShape[]; tool: SketchTool | null; draft: Draft | null;
  picked: number | null; typed: string; past: SketchShape[][]; n: number };
/** What Extrude and Cut make of a sketch: so high out of its plane; a cut through all, or so deep. */
type SketchMake = { height: number; through: boolean; depth: number };
const TABS: [Tab, string][] = [["insert", "Insert"], ["part", "Part"], ["lines", "Lines"]];
const ALIGNS: [string, string[]][] = [["X", ["Left", "Middle", "Right"]], ["Y", ["Front", "Middle", "Back"]],
  ["Z", ["Bottom", "Middle", "Top"]]];

/** Which of the band's groups are folded, for want of room. */
const Folded = createContext<ReadonlySet<string>>(new Set());
const NONE: ReadonlySet<string> = new Set();

/** The ribbon's band (0.94.0): when its groups do not all fit, the ones marked to fold first become one button each,
 *  the group in its menu - as Office's ribbon does - until they fit. `fit` changes with what the band shows. */
function Band({ fit, label, children }: { fit: string; label?: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [folded, setFolded] = useState<ReadonlySet<string>>(NONE);
  const [measuring, setMeasuring] = useState(true);
  const width = useRef(0);
  useLayoutEffect(() => setMeasuring(true), [fit]);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (Math.abs(el.clientWidth - width.current) < 1) return;          // its width counts, not its height
      width.current = el.clientWidth;
      setMeasuring(true);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const over = () => el.scrollWidth > el.clientWidth + 1;
    const groups = () => [...el.querySelectorAll<HTMLElement>(":scope > .v3d-group[data-fold]")]
      .sort((a, b) => Number(a.dataset.fold) - Number(b.dataset.fold));
    if (measuring) {                                   // all open: fold the first to go, one by one, by their widths
      const cs = getComputedStyle(el);
      const room = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      let total = [...el.children].reduce((sum, c) => sum + (c as HTMLElement).getBoundingClientRect().width, 0);
      const next = new Set<string>();
      for (const g of groups()) {
        if (total <= room) break;
        next.add(g.dataset.key ?? "");
        total -= g.getBoundingClientRect().width - 66;   // a folded group: at least one button wide (too few: folded after)
      }
      setFolded(next);
      setMeasuring(false);
    } else if (over()) {                               // folded and still too wide: the next one too
      const g = groups().find((x) => !folded.has(x.dataset.key ?? ""));
      if (g) setFolded(new Set([...folded, g.dataset.key ?? ""]));
    }
  }, [measuring, folded]);
  return (
    <Folded.Provider value={measuring ? NONE : folded}>
      <div ref={ref} className="v3d-band" role="tabpanel" aria-label={label}>{children}</div>
    </Folded.Provider>
  );
}

/** A group of the ribbon's tools, its name under it - as in SolidWorks' and Office's ribbons. `fold`: short of room it
 *  becomes one button (`icon`, its name) with the group in its menu - the lowest number first. */
function Grp({ label, children, fold, icon, id, short }: { label: ReactNode; children: ReactNode; fold?: number;
  icon?: ReactNode; id?: string; short?: string }) {
  const folded = useContext(Folded);
  const name = typeof label === "string" ? label : undefined;
  const key = id ?? name ?? String(fold ?? "");
  if (fold !== undefined && folded.has(key)) {
    return (
      <div className="v3d-group folded" data-fold={fold} data-key={key}>
        <div className="v3d-group-body">
          <MenuButton fixed className="btn v3d-big" title={`${name ?? short ?? "More"}: its tools`} menuClassName="v3d-foldmenu"
            label={<>{icon ?? <ChevronDown size={20} />}<span>{short ?? name ?? "More"} <ChevronDown size={11} /></span></>}>
            {() => <div className="v3d-foldbody">{children}</div>}
          </MenuButton>
        </div>
        <div className="v3d-group-label">&nbsp;</div>
      </div>
    );
  }
  return (
    <div className="v3d-group" role="group" aria-label={name} data-fold={fold} data-key={key}>
      <div className="v3d-group-body">{children}</div>
      <div className="v3d-group-label">{label}</div>
    </div>
  );
}

/** Turn, as one button (0.97.0, Office's Rotate): 90 degrees about Z, X or Y - each part picked through its middle. */
function TurnButton({ onTurn }: { onTurn: (axis: 0 | 1 | 2, sign: 1 | -1) => void }) {
  const items: [0 | 1 | 2, 1 | -1, ReactNode, string, string][] = [
    [2, 1, <RotateCcw size={15} />, "Turn left 90°", "About Z, seen from above"],
    [2, -1, <RotateCw size={15} />, "Turn right 90°", "About Z, seen from above"],
    [0, 1, <RotateCcw size={15} />, "Tip forward 90°", "About X: its top to the front - a flat text stands up, facing you"],
    [0, -1, <RotateCw size={15} />, "Tip back 90°", "About X: its top to the back"],
    [1, -1, <RotateCcw size={15} />, "Tip left 90°", "About Y: its top to the left"],
    [1, 1, <RotateCw size={15} />, "Tip right 90°", "About Y: its top to the right"],
  ];
  return (
    <MenuButton fixed className="btn v3d-big" title="Turn it 90° - one that stood on the plate still does"
      label={<><RotateCw size={20} /><span>Turn <ChevronDown size={11} /></span></>}>
      {(close) => items.map(([axis, sign, icon, name, note]) => (
        <button key={`${axis}${sign}`} className="menu-item" onClick={() => { close(); onTurn(axis, sign); }}>
          {icon}<span>{name}<small>{note}</small></span></button>
      ))}
    </MenuButton>
  );
}

/** Mirror, as one button: left-right, front-back or up-down. */
function MirrorButton({ onMirror }: { onMirror: (axis: 0 | 1 | 2) => void }) {
  return (
    <MenuButton fixed className="btn v3d-big" title="Mirror it - left-right, front-back or up-down"
      label={<><FlipHorizontal2 size={20} /><span>Mirror <ChevronDown size={11} /></span></>}>
      {(close) => (
        <>
          <button className="menu-item" onClick={() => { close(); onMirror(0); }}><FlipHorizontal2 size={15} /><span>Left-right<small>In X</small></span></button>
          <button className="menu-item" onClick={() => { close(); onMirror(1); }}><FlipVertical2 size={15} /><span>Front-back<small>In Y</small></span></button>
          <button className="menu-item" onClick={() => { close(); onMirror(2); }}><ArrowDownUp size={15} /><span>Up-down<small>In Z</small></span></button>
        </>
      )}
    </MenuButton>
  );
}

/** A big ribbon button, as Office 2007 has them (0.96.0): its icon over its name - `tone` colours the icon. */
function Big({ icon, label, title, onClick, active, disabled, tone }: { icon: ReactNode; label: string; title?: string;
  onClick: () => void; active?: boolean; disabled?: boolean; tone?: Tone }) {
  return (
    <button type="button" className={`btn v3d-big${tone ? ` tone-${tone}` : ""}${active ? " on" : ""}`} onClick={onClick}
      disabled={disabled} title={title ?? label} aria-pressed={active}>{icon}<span>{label}</span></button>
  );
}

type Tone = "orange" | "grey" | "green" | "red" | "violet";

/** A small ribbon button: its icon before its name, one of up to three in a column (`Stack`). */
function Small({ icon, label, title, onClick, active, disabled, tone }: { icon: ReactNode; label: string; title?: string;
  onClick: () => void; active?: boolean; disabled?: boolean; tone?: Tone }) {
  return (
    <button type="button" className={`v3d-small${tone ? ` tone-${tone}` : ""}${active ? " on" : ""}`} onClick={onClick}
      disabled={disabled} title={title ?? label} aria-pressed={active}>{icon}<span>{label}</span></button>
  );
}

/** Up to three rows of small controls, one over the other - an Office ribbon group's column. */
function Stack({ children }: { children: ReactNode }) {
  return <div className="v3d-col">{children}</div>;
}

/** A number in a row of a column: its name, the field, its unit. */
function Field({ value, onSet, min, label: name, unit, step = 1, title, width }: { value: number; onSet: (v: number) => void;
  min?: number; label: string; unit?: string; step?: number; title?: string; width?: number }) {
  return (
    <label className="v3d-field" title={title ?? `${name}${unit ? ` (${unit})` : ""} - the arrow keys step it, Shift: ten steps`}>
      <span>{name}</span>
      <span className="v3d-field-in" style={width ? { width } : undefined}><NumInput value={value} onSet={onSet} min={min} step={step} label={name} /></span>
      {unit && <small>{unit}</small>}
    </label>
  );
}

/** The view buttons' pictures, as SolidWorks draws its view orientations (0.96.0): a cube seen from the front, the
 *  right and above, the face looked at filled - Fit: all three, shaded. */
function ViewIcon({ face }: { face: "home" | "top" | "front" | "right" }) {
  const fill = (f: "top" | "front" | "right") => (face === "home" ? `var(--vi-${f})` : face === f ? "var(--vi-on)" : "var(--vi-off)");
  return (
    <svg className="v3d-viewicon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <polygon points="8,1.5 14.5,5 8,8.5 1.5,5" fill={fill("top")} />
      <polygon points="1.5,5 8,8.5 8,15 1.5,11.5" fill={fill("front")} />
      <polygon points="8,8.5 14.5,5 14.5,11.5 8,15" fill={fill("right")} />
      <path d="M8 1.5 14.5 5v6.5L8 15l-6.5-3.5V5ZM1.5 5 8 8.5 14.5 5M8 8.5V15" fill="none" stroke="currentColor"
        strokeWidth="1.1" strokeLinejoin="round" />
    </svg>
  );
}

/** A text's name in its own font (the Font box's list, as Office shows its fonts): the letters' outlines as a path. */
function FontFace({ text, font, height = 15 }: { text: string; font: FontData | undefined; height?: number }) {
  const d = useMemo(() => {
    if (!font) return null;
    const glyphs = textGlyphs({ id: "", shape: "text", text, size: [0, 0, 0], at: [0, 0, 0], rotate: [0, 0, 0], align: "left" }, font);
    let x1 = 0, path = "";
    for (const g of glyphs) {
      for (const c of g.contours) {
        path += `M${c.map(([x, y]) => `${Math.round(x)} ${Math.round(-y)}`).join("L")}Z`;
        for (const [x] of c) x1 = Math.max(x1, x);
      }
    }
    return { path, w: Math.max(1, x1) };
  }, [text, font]);
  if (!font || !d) return <span className="v3d-face-plain">{text}</span>;
  const cap = font.capHeight ?? font.ascender * 0.7;                 // its capitals the same height in every font
  const top = cap * 1.3, h = top - Math.max(font.descender, -cap * 0.45);
  return (
    <svg className="v3d-face" height={height} width={(height * d.w) / h} viewBox={`0 ${-top} ${d.w} ${h}`} aria-label={text} role="img">
      <path d={d.path} fill="currentColor" />
    </svg>
  );
}

/** The Font box (0.96.0, Office's): the font's name in its own letters; its list, each font so - from previews.json
 *  (0.99.0: sixteen fonts), fetched when the pointer comes to the box; a font's own file, where it is loaded. */
function FontCombo({ value, fonts, onPick }: { value: string; fonts: Fonts; onPick: (family: string) => void }) {
  const [, setTick] = useState(0);
  const faceOf = (f: string) => fonts.loaded[fonts.index.families[f]?.regular ?? ""] ?? previews?.[f];
  const loadAll = () => {
    if (!previews) void loadPreviews().then(() => setTick((t) => t + 1));
  };
  const fam = value ? fonts.index.families[value] : undefined;
  return (
    <div className="v3d-combo font" onPointerEnter={loadAll}>
      <MenuButton fixed className="v3d-combo-btn" title="Font" menuClassName="v3d-combomenu"
        label={<><span className="v3d-combo-text">{fam ? <FontFace text={fam.name} font={faceOf(value)} height={14} /> : "Several fonts"}</span>
          <span className="v3d-combo-arrow"><ChevronDown size={12} /></span></>}>
        {(close) => (
          <>
            {FONT_FAMILIES.map((f) => (
              <button key={f} type="button" role="menuitemradio" aria-checked={f === value} className="menu-item v3d-fontitem"
                onClick={() => { close(); onPick(f); }}>
                <FontFace text={fonts.index.families[f]?.name ?? f} font={faceOf(f)} height={19} />
                <small>{f[0].toUpperCase() + f.slice(1)}</small>
              </button>
            ))}
          </>
        )}
      </MenuButton>
    </div>
  );
}

/** How tall its capitals come out, mm - Office's font size box: typed, or one of its list. */
const TEXT_SIZES = [3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 80];
function SizeCombo({ value, mixed, onSet }: { value: number; mixed: boolean; onSet: (v: number) => void }) {
  return (
    <div className="v3d-combo size" title="Size: how tall its capitals come out, mm">
      <span className="v3d-combo-in">{mixed ? <input className="input mono" placeholder="-" aria-label="Size, mm"
        onKeyDown={(e) => { if (e.key === "Enter") { const v = Number(e.currentTarget.value.replace(",", ".")); if (v >= 0.1) onSet(v); } }} />
        : <NumInput value={r3(value)} min={0.1} onSet={onSet} label="Size, mm" />}</span>
      <MenuButton fixed className="v3d-combo-btn arrow" title="Sizes" menuClassName="v3d-combomenu narrow"
        label={<span className="v3d-combo-arrow"><ChevronDown size={12} /></span>}>
        {(close) => TEXT_SIZES.map((v) => (
          <button key={v} type="button" role="menuitemradio" aria-checked={!mixed && Math.abs(v - value) < 1e-6}
            className="menu-item num" onClick={() => { close(); onSet(v); }}>{v}</button>
        ))}
      </MenuButton>
      <small>mm</small>
    </div>
  );
}

/** The design's parts as a tree over the view, at the left under the bar (0.96.1, Søren: "Move parts" - where
 *  SolidWorks has its FeatureManager): a click picks one (Shift: one more), Hide or Show on the row leaves it out or
 *  in; the title folds it. */
function PartTree({ parts, selected, open, onOpen, onPick, onHide, onPickAll }: { parts: Part[]; selected: string[];
  open: boolean; onOpen: (v: boolean) => void; onPick: (id: string, add: boolean) => void; onHide: (id: string) => void;
  onPickAll: () => void }) {
  const rows = (list: Part[], depth: number): ReactNode => list.map((p) => {
    const on = selected.includes(p.id);
    return (
      <li key={p.id} role="treeitem" aria-selected={on}>
        <div className={`v3d-tree-row${on ? " on" : ""}${p.hidden ? " hidden" : ""}`} style={{ paddingLeft: 2 + depth * 14 }}>
          <button type="button" className="v3d-tree-name" onClick={(e) => onPick(p.id, e.shiftKey)}
            title={`${label(p)} - ${p.shape === "group" ? "a group" : p.hole ? `a ${p.shape} hole` : `a ${p.shape}`}${p.hidden ? ", hidden" : ""}. Click to pick it, Shift-click: one more`}>
            <ShapeIcon part={p} /><span>{label(p)}</span>
          </button>
          <button type="button" className="v3d-tree-eye" onClick={() => onHide(p.id)}
            title={p.hidden ? "Hidden: left out of the solid. Show it" : "Hide it: left out of the solid"}
            aria-label={p.hidden ? `Show ${label(p)}` : `Hide ${label(p)}`}>
            {p.hidden ? <EyeOff size={13} /> : <Eye size={13} />}<span>{p.hidden ? "Show" : "Hide"}</span>
          </button>
        </div>
        {!!p.parts?.length && <ul role="group">{rows(p.parts, depth + 1)}</ul>}
      </li>
    );
  });
  return (
    <div className="v3d-tree">
      <div className="v3d-tree-head">
        <button type="button" className="v3d-tree-toggle" aria-expanded={open} onClick={() => onOpen(!open)}
          title={open ? "Fold the list of parts" : "The design's parts: pick one, hide or show it"}>
          <ChevronDown size={13} className={open ? undefined : "shut"} /><ListTree size={14} /><span>Parts ({allIds(parts).size})</span>
        </button>
        {open && parts.length > 1 && <button type="button" className="v3d-tree-all" onClick={onPickAll} title="Every part (Ctrl+A)">Pick all</button>}
      </div>
      {open && <ul className="v3d-tree-list" role="tree" aria-label="The design's parts">{rows(parts, 0)}</ul>}
    </div>
  );
}

/** A part's shape as a small picture in the tree: a hole's grey and dashed, a text's violet, a group's blue. */
function ShapeIcon({ part: p }: { part: Part }) {
  const props = { size: 14, ...(p.hole ? { strokeDasharray: "3 2" } : {}) };
  const icon = p.shape === "box" ? <Box {...props} /> : p.shape === "cylinder" ? <Cylinder {...props} /> : p.shape === "cone"
    ? <Cone {...props} /> : p.shape === "sphere" ? <CircleDot {...props} /> : p.shape === "wedge" ? <Triangle {...props} />
      : p.shape === "text" ? <Type {...props} /> : p.shape === "outline" ? <Shapes {...props} /> : p.shape === "mesh"
        ? <Upload {...props} /> : <Group {...props} />;
  const tone = p.hole ? "grey" : p.shape === "text" ? "violet" : p.shape === "group" ? "" : "orange";
  return <span className={`v3d-tree-icon${tone ? ` tone-${tone}` : ""}`}>{icon}</span>;
}

const kindOf = (p: Part) => (p.shape === "group" ? "Group" : p.shape === "mesh" ? "Imported mesh"
  : p.shape[0].toUpperCase() + p.shape.slice(1));

/** The Part tab for one part (0.94.0: with what Size and place and Arrange had): its name, solid or hole and colour;
 *  its size, centre and turn; what its shape has of its own; the plate; mirror and pattern; duplicate, hide, delete. */
function PartBand({ part, fonts, lock, setLock, change, onDuplicate, onDelete, onApart, onDrop, onFlat, flat, onUngroup,
  onMirror, onFace, face, onTurn, box: onBox, depth }: { part: Part; fonts: Fonts; lock: boolean; setLock: (v: boolean) => void;
  change: (fn: (p: Part) => Part) => void; onDuplicate: () => void; onDelete: () => void; onApart: () => void;
  onDrop: () => void; onFlat: () => void; flat: boolean; onUngroup: () => void; onMirror: (axis: 0 | 1 | 2) => void;
  onFace: () => void; face: boolean; onTurn: (axis: 0 | 1 | 2, sign: 1 | -1) => void; box: Part | null; depth: number }) {
  const g = part.shape === "group";
  const box = extent(part);
  return (
    <>
      <Grp label={<>{kindOf(part)} <span className="mono">{part.id}</span></>}>
        <Stack>
          <label className="v3d-field" title="Its name in Parts and in the assistant's messages">
            <span>Name</span>
            <input className="input v3d-name" defaultValue={part.name ?? ""} key={`${part.id}:${part.name ?? ""}`} placeholder={label(part)}
              aria-label="Name" onBlur={(e) => { if (e.target.value !== (part.name ?? "")) change((p) => ({ ...p, name: e.target.value || undefined })); }}
              onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
          </label>
          <div className="v3d-row" role="group" aria-label="Solid or hole">
            <Small icon={<Box size={16} />} tone="orange" label="Solid" active={!part.hole} title="A solid: joined to the others"
              onClick={() => change((p) => ({ ...p, hole: undefined }))} />
            <Small icon={<SquareDashed size={16} />} tone="grey" label="Hole" active={!!part.hole} title="A hole cuts the solids beside it (H)"
              onClick={() => change((p) => ({ ...p, hole: true }))} />
          </div>
          {!g && !part.hole && (
            <label className="v3d-field" title="Colour (for the eye only: one colour prints)">
              <span>Colour</span>
              <input type="color" className="v3d-color" value={part.color ?? GREY} aria-label="Colour"
                onChange={(e) => change((p) => ({ ...p, color: e.target.value }))} />
            </label>
          )}
        </Stack>
      </Grp>
      {!g && (
        <Grp id="place" short="Size" label={part.shape === "outline" ? "Size, its points' 0,0 and turn" : "Size, centre and turn"}
          fold={part.on ? 2 : 6}
          icon={<Scaling size={20} />}>
          <PlaceTable part={part} fonts={fonts} lock={lock} setLock={setLock} change={change} bottom={box?.[0][2] ?? 0} />
        </Grp>
      )}
      {part.on && onBox && <OnFaceGroup part={part} box={onBox} change={change} depth={depth} />}
      {g && (
        <Grp label="Group" fold={3} icon={<Group size={20} />}>
          <p className="v3d-note num">{box ? `${fmt(box[1][0] - box[0][0])} x ${fmt(box[1][1] - box[0][1])} x ${fmt(box[1][2] - box[0][2])} mm, ` : ""}
            {part.parts?.length ?? 0} parts. A hole in it cuts only its own parts; double-click one on the model to change it.
            Move, turn and size it with the handles.</p>
        </Grp>
      )}
      {part.shape === "box" && (
        <Grp label="Box" fold={3} icon={<Box size={20} />}>
          <Stack>
            <Field label="All rounded" unit="mm" value={part.radius ?? 0} min={0} step={0.5}
              title="Every edge and corner rounded, mm - or round some lines in Lines"
              onSet={(v) => change((p) => (v > 0 ? { ...p, radius: v, sides: p.sides ?? 32, fillet: undefined, chamfer: undefined }
                : { ...p, radius: undefined, sides: p.fillet ? p.sides : undefined }))} />
            {!!part.radius && <Field label="Sides" value={part.sides ?? 64} min={3} step={8} title="Segments round a corner"
              onSet={(v) => change((p) => ({ ...p, sides: Math.round(Math.min(256, Math.max(3, v))) }))} />}
          </Stack>
        </Grp>
      )}
      {(part.shape === "cylinder" || part.shape === "cone" || part.shape === "sphere") && (
        <Grp label={kindOf(part)} fold={3} icon={part.shape === "cone" ? <Cone size={20} /> : part.shape === "sphere" ? <CircleDot size={20} /> : <Cylinder size={20} />}>
          <Stack>
            <Field label="Sides" value={part.sides ?? 64} min={3} step={8} title="Segments around"
              onSet={(v) => change((p) => ({ ...p, sides: Math.round(Math.min(256, Math.max(3, v))) }))} />
            {part.shape === "cone" && <Field label="Top" unit="% of the base" value={(part.top ?? 0) * 100} min={0} step={10}
              onSet={(v) => change((p) => ({ ...p, top: Math.min(1, v / 100) || undefined }))} />}
          </Stack>
        </Grp>
      )}
      {part.shape === "outline" && (
        <Grp label="Outline" fold={3} icon={<Shapes size={20} />}><p className="v3d-note">{part.points?.length ?? 0} points{part.holes?.length
          ? `, ${part.holes.length} hole${part.holes.length > 1 ? "s" : ""} in it` : ""}. Its size scales them
          {part.size[0] <= 0 && part.size[1] <= 0 ? " (now as drawn)" : ""}.</p></Grp>
      )}
      {part.shape === "mesh" && <Grp label="Imported mesh" fold={3} icon={<Upload size={20} />}><p className="v3d-note mono" title="The file, from the design's folder">{part.file}</p></Grp>}
      {part.shape === "text" && (
        <Grp label="Text" fold={3} icon={<Type size={20} />}>
          <p className="v3d-note">Its letters, font and lines: the Text tab.</p>
          <ApartButton part={part} onApart={onApart} />
        </Grp>
      )}
      <Grp label="Arrange" fold={4} icon={<ArrowDownToLine size={20} />}>
        <Big icon={<ArrowDownToLine size={20} />} label="Onto the plate" title="Moved up or down so its lowest point is on the plate" onClick={onDrop} />
        <Big icon={<PanelBottom size={20} />} label="Lay flat" title="Lay a face flat: click the face that should lie on the plate" onClick={onFlat} active={flat} />
        <Big icon={<Stamp size={20} />} label="On a face" active={face} onClick={onFace}
          title="Put it on a face of the model: click the face - a text's letters on it, upright on a wall (a hole sinks in)" />
        <TurnButton onTurn={onTurn} />
        <MirrorButton onMirror={onMirror} />
        <PatternButton part={part} change={change} />
        {g && <Big icon={<Ungroup size={20} />} label="Ungroup" title="Ungroup (Ctrl+Shift+G)" onClick={onUngroup} />}
      </Grp>
      <Grp label="Part" fold={5} icon={<Copy size={20} />}>
        <Big icon={<Copy size={20} />} label="Duplicate" title="Duplicate (Ctrl+D)" onClick={onDuplicate} />
        <Big icon={part.hidden ? <EyeOff size={20} /> : <Eye size={20} />} label={part.hidden ? "Show" : "Hide"}
          title={part.hidden ? "Hidden: left out of the solid. Show it" : "Hide it: left out of the solid"}
          onClick={() => change((p) => ({ ...p, hidden: !p.hidden || undefined }))} />
        <ConfirmButton className="btn v3d-big tone-red" confirmText="Delete?" label="Delete (Del)" onConfirm={onDelete}>
          <Trash2 size={20} /><span>Delete</span></ConfirmButton>
      </Grp>
    </>
  );
}

/** Size, centre (an outline: its points' 0,0) and turn by the numbers (0.94.0: one small table, X Y Z across). */
function PlaceTable({ part, fonts, lock, setLock, change, bottom }: { part: Part; fonts: Fonts; lock: boolean;
  setLock: (v: boolean) => void; change: (fn: (p: Part) => Part) => void; bottom: number }) {
  const now = sized(part, fonts);
  const sizeOf = (i: number, v: number) => change((p) => {
    if (!lock) {                                       // a text's other 0 stays: its letters decide it
      const s = [...p.size] as Vec3;
      s[i] = v;
      return { ...p, size: s };
    }
    const f = v / (sized(p, fonts)[i] || 1);        // an outline's 0s become sizes; a text's 0 stays
    const size = (p.shape === "outline" ? sized(p, fonts) : p.size).map((x) => x * f) as Vec3;
    return { ...p, size, radius: p.radius ? p.radius * f : p.radius };
  });
  const rows: [string, string, (i: number) => number, (i: number, v: number) => void, number | undefined, number, ReactNode][] = [
    ["Size", "mm", (i) => r3(now[i]), sizeOf, MIN_SIZE, 1,
      <label key="lock" className="v3d-check" title="All three change together"><input type="checkbox" checked={lock}
        onChange={(e) => setLock(e.target.checked)} /> keep ratio</label>],
    [part.shape === "outline" ? "0,0" : "Centre", "mm", (i) => part.at[i],
      (i, v) => change((p) => { const at = [...p.at] as Vec3; at[i] = v; return { ...p, at }; }), undefined, 1,
      <small key="z" className="faint num" title="Its lowest point">bottom z {fmt(bottom)}</small>],
    ["Turned", "°: X first, then Y, then Z", (i) => part.rotate[i],
      (i, v) => change((p) => { const r = [...p.rotate] as Vec3; r[i] = v; return { ...p, rotate: r }; }), undefined, 15,
      <span key="order" />],
  ];
  return (
    <div className="v3d-place">
      {rows.map(([name, unit, get, set, min, step, after]) => (
        <Fragment key={name}>
          <span className="v3d-place-name">{name}</span>
          {[0, 1, 2].map((i) => (
            <label key={i} className="v3d-axis" title={`${name} ${"XYZ"[i]} (${unit}) - the arrow keys step it, Shift: ten steps`}>
              <span className={`v3d-ax ax${i}`}>{"XYZ"[i]}</span>
              <NumInput value={get(i)} min={min} step={step} onSet={(v) => set(i, v)} label={`${name} ${"XYZ"[i]}, ${unit.split(":")[0]}`} />
            </label>
          ))}
          {after}
        </Fragment>
      ))}
    </div>
  );
}

/** The Text tab (0.89.0): its letters, font, style, lines and spacing. "Capitals" is how tall they come out: it sizes
 *  the text. Several texts picked - letters taken apart, say - get one font, style and capitals each (0.92.0). */
function TextBand({ parts, fonts, change, onApart, onFace, face }: { parts: Part[]; fonts: Fonts;
  change: (fn: (p: Part) => Part) => void; onApart: () => void; onFace: () => void; face: boolean }) {
  const part = parts[0], many = parts.length > 1;
  const t = textMeasure(part, fonts);
  const fontOf = (p: Part) => (fonts.index.families[p.font ?? ""] ? p.font! : fonts.index.default);
  const fam = fonts.index.families[fontOf(part)];
  const oneFont = parts.every((p) => fontOf(p) === fontOf(part));
  const canBold = parts.some((p) => fonts.index.families[fontOf(p)]?.bold);
  const bold = parts.every((p) => p.bold), italic = parts.every((p) => p.italic);
  const restyle = (patch: Partial<Part>) => {          // its capitals kept: measured in the new font, loaded first
    const missing = [...new Set(parts.map((p) => fontFile({ ...p, ...patch }, fonts.index).file))].filter((f) => !fonts.loaded[f]);
    if (!missing.length) change((p) => retext(p, patch, fonts));
    else void Promise.allSettled(missing.map(loadFont)).then(() => change((p) => retext(p, patch, fonts)));
  };
  const align = part.align ?? "center";
  const spacing = (key: "letter_spacing" | "line_spacing") => (v: number) => {
    const c = Math.max(SPACING[0], Math.min(SPACING[1], v));
    restyle({ [key]: Math.abs(c - 1) < 1e-9 ? undefined : c });
  };
  const fixed = part.size[0] > 0 && part.size[1] > 0;
  const own = fixed ? textMeasure({ ...part, size: [0, part.size[1], part.size[2]] }, fonts)?.width : undefined;
  const stretched = own !== undefined && Math.abs(own - part.size[0]) >= 0.05;
  const caps = parts.map((p) => textMeasure(p, fonts)?.capitals ?? 0);
  const LINES: [Align, ReactNode, string][] = [["left", <AlignLeft size={16} />, "Left"], ["center", <AlignCenter size={16} />, "Centre"],
    ["right", <AlignRight size={16} />, "Right"]];
  const mixed = many && caps.some((c) => Math.abs(c - caps[0]) > 1e-6);
  return (
    <>
      {many ? (
        <Grp label="Texts"><p className="v3d-note">{parts.length} texts picked: {parts.map((p) => JSON.stringify(p.text ?? "")).join(", ")}.
          A change here is made to each.</p></Grp>
      ) : (
        <Grp label="Text">
          <textarea className="textarea v3d-textarea" rows={3} defaultValue={part.text ?? ""} key={`${part.id}:${part.text ?? ""}`}
            aria-label="Text" title="Its letters; a new line is a new line of text"
            onBlur={(e) => { if (e.target.value !== part.text) restyle({ text: e.target.value }); }} />
          <Big icon={<Stamp size={20} />} label="On a face" active={face} onClick={onFace}
            title="Put the text on a face of the model: click the face - its letters on it, upright on a wall (a hole sinks in)" />
          <ApartButton part={part} onApart={onApart} />
        </Grp>
      )}
      <Grp id="font" short="Font" fold={3} icon={<Type size={20} />} label="Font">
        <Stack>
          <div className="v3d-row">
            <FontCombo value={oneFont ? fontOf(part) : ""} fonts={fonts}
              onPick={(f) => restyle({ font: f === fonts.index.default ? undefined : f })} />
            <SizeCombo value={caps[0]} mixed={mixed} onSet={(v) => change((p) => withCapitals(p, v, fonts))} />
          </div>
          <div className="v3d-row">
            <Small icon={<Bold size={16} />} label="Bold" active={bold && canBold} disabled={!canBold}
              title={canBold ? "Bold" : `${fam.name} has no bold`} onClick={() => restyle({ bold: !bold || undefined })} />
            <Small icon={<Italic size={16} />} label="Italic" active={italic}
              title={fam.italic || many ? "Italic" : `Slanted (${fam.name} has no italic)`} onClick={() => restyle({ italic: !italic || undefined })} />
          </div>
          <Field label="Spacing" value={part.letter_spacing ?? 1} min={SPACING[0]} step={0.05} width={52}
            title="From letter to letter: 1 is the font's own" onSet={spacing("letter_spacing")} />
        </Stack>
      </Grp>
      <Grp id="lines" short="Lines" fold={2} icon={<AlignCenter size={20} />} label="Lines">
        <Stack>
          <div className="v3d-row" role="group" aria-label="The lines">
            {LINES.map(([a, icon, name]) => (
              <Small key={a} icon={icon} label={name} active={align === a} title={`Lines ${a === "center" ? "centred" : `to the ${a}`}`}
                onClick={() => restyle({ align: a === "center" ? undefined : a })} />
            ))}
          </div>
          <Field label="Spacing" value={part.line_spacing ?? 1} min={SPACING[0]} step={0.05} width={52}
            title="From line to line: 1 is the font's own" onSet={spacing("line_spacing")} />
          {!many && t && <small className="v3d-col-note">{t.lines > 1 ? `${t.lines} lines` : "1 line"}</small>}
        </Stack>
      </Grp>
      <Grp id="letters" short="Size" fold={1} icon={<Scaling size={20} />} label="Size">
        <Stack>
          {!many && t && <span className="v3d-col-strong num">{fmt(t.width)} x {fmt(t.height)} mm</span>}
          {!many && (
            <small className="v3d-col-note" title={t ? `${t.lines > 1 ? `${t.lines} lines in ` : ""}${t.font}` : undefined}>
              {part.size[0] <= 0 ? "Its width follows the letters" : part.size[1] <= 0 ? "Its height follows the letters"
                : stretched ? <>Stretched to its size - <button className="link-btn small" onClick={() => change((p) => ({ ...p, size: [0, p.size[1], p.size[2]] }))}>undo</button></>
                  : "Its width follows the letters"}</small>
          )}
          {many && <small className="v3d-col-note">{mixed ? "Not the same size: a size typed is given to each" : `Capitals ${fmt(caps[0])} mm each`}</small>}
        </Stack>
      </Grp>
    </>
  );
}

/** Where on a face, one way across it (0.98.0): from which edge - or in the middle - and how far, mm. */
function FromRow({ face, axis, size, from, onSet }: { face: FaceName; axis: 0 | 1; size: number;
  from?: Record<string, number>; onSet: (from: Record<string, number> | undefined) => void }) {
  const [lo, hi] = FACE_FRAMES[face].ends[axis];
  const f = from ?? {};
  const key = f[lo] !== undefined ? lo : f[hi] !== undefined ? hi : "";
  const fromLo = key === lo ? f[lo] : key === hi ? size - f[hi] : size / 2;          // the spot, from the first edge
  const set = (k: string, mm: number) => {
    const next = { ...f };
    delete next[lo];
    delete next[hi];
    if (k) next[k] = r3(mm);
    onSet(Object.keys(next).length ? next : undefined);
  };
  const way = axis ? "Up the face" : "Across the face";
  return (
    <div className="v3d-field v3d-from">
      <select className="select" value={key} aria-label={`${way}: measured from`}
        title={`${way}: which edge the distance is from - or in the middle`}
        onChange={(e) => set(e.target.value, e.target.value === lo ? fromLo : e.target.value === hi ? size - fromLo : 0)}>
        <option value={lo}>From {lo}</option>
        <option value={hi}>From {hi}</option>
        <option value="">Middle</option>
      </select>
      <span className="v3d-field-in"><NumInput value={key ? f[key] : r3(size / 2)} step={1}
        label={`${way}, mm from the ${key || lo} edge`} onSet={(v) => set(key || lo, v)} /></span>
      <small>mm</small>
    </div>
  );
}

/** The Face tab (0.98.0): the face picked on a box and the spot on it - so many mm from two of its edges, as a sketch
 *  on a face is dimensioned in SolidWorks - and what goes there: a round or a square hole through all of the box or
 *  so deep (an extruded cut), a boss so high (a boss extrude), or a text. Each is kept there: it goes with the box. */
function FaceBand({ box, spot, feature, onFeature, onFrom, onMake, onSketch }: { box: Part; spot: FacePick; feature: Feature;
  onFeature: (f: Partial<Feature>) => void; onFrom: (from: Record<string, number> | undefined) => void;
  onMake: (kind: "hole" | "boss" | "text") => void; onSketch: () => void }) {
  const F = faceFrame(box, spot.face);
  const name = spot.face[0].toUpperCase() + spot.face.slice(1);
  const across = feature.round ? `round, ${fmt(feature.size)} mm across` : `square, ${fmt(feature.size)} mm wide`;
  const keeps = "it stays here when the box moves, turns or grows";
  return (
    <>
      <Grp label="Face" fold={1} icon={<Box size={20} />}>
        <Stack>
          <span className="v3d-col-strong v3d-clip">{name}</span>
          <small className="v3d-col-note v3d-clip" title={label(box)}>of {label(box)}</small>
          <small className="v3d-col-note num">{fmt(F.W)} x {fmt(F.H)} mm</small>
        </Stack>
      </Grp>
      <Grp label="Where" fold={3} icon={<Crosshair size={20} />}>
        <Stack>
          <FromRow face={spot.face} axis={0} size={F.W} from={spot.from} onSet={onFrom} />
          <FromRow face={spot.face} axis={1} size={F.H} from={spot.from} onSet={onFrom} />
          <Small icon={<Crosshair size={16} />} label="In the middle" onClick={() => onFrom(undefined)}
            title="The middle of the face, both ways" />
        </Stack>
      </Grp>
      <Grp label="Shape" fold={2} icon={<CircleDashed size={20} />}>
        <Stack>
          <div className="v3d-row" role="group" aria-label="Round or square">
            <Small icon={<CircleDashed size={16} />} label="Round" active={feature.round} onClick={() => onFeature({ round: true })} />
            <Small icon={<SquareDashed size={16} />} label="Square" active={!feature.round} onClick={() => onFeature({ round: false })} />
          </div>
          <Field label={feature.round ? "Diameter" : "Side"} unit="mm" value={feature.size} min={0.1} step={1}
            onSet={(v) => onFeature({ size: Math.max(0.1, v) })} />
        </Stack>
      </Grp>
      <Grp label="Cut or add">
        <Big tone="violet" icon={<PencilRuler size={20} />} label="Sketch" onClick={onSketch}
          title="Draw on this face - lines, rectangles, circles - then Extrude or Cut it, kept on the face" />
        <Big tone="grey" icon={feature.round ? <CircleDashed size={20} /> : <SquareDashed size={20} />} label="Cut hole"
          onClick={() => onMake("hole")} title={`A hole here, ${across}, ${feature.through ? "through all of the box"
            : `${fmt(feature.depth)} mm deep`} - ${keeps}`} />
        <Big tone="orange" icon={feature.round ? <Cylinder size={20} /> : <Box size={20} />} label="Add boss"
          onClick={() => onMake("boss")} title={`A boss here, ${across}, ${fmt(feature.height)} mm out of the face - ${keeps}`} />
        <Big tone="violet" icon={<Type size={20} />} label="Text here" onClick={() => onMake("text")}
          title={`A text here, 1 mm out of the face - its letters, font and size in the Text tab; ${keeps}`} />
        <Stack>
          <label className="v3d-check" title="The hole through all of the box - however thick it gets">
            <input type="checkbox" checked={feature.through} onChange={(e) => onFeature({ through: e.target.checked })} /> Through all</label>
          {!feature.through && <Field label="Depth" unit="mm" value={feature.depth} min={0.1} step={1} width={44}
            title="How far the hole goes into the box, mm" onSet={(v) => onFeature({ depth: Math.max(0.1, v) })} />}
          <Field label="Height" unit="mm" value={feature.height} min={0.1} step={1} width={44}
            title="How far the boss stands out of the face, mm" onSet={(v) => onFeature({ height: Math.max(0.1, v) })} />
        </Stack>
      </Grp>
    </>
  );
}

/** Insert's Sketch (0.100.0): on the plate, the front plane or the right plane - on a box's face from its Face tab. */
function SketchButton({ onPick }: { onPick: (plane: SketchPlane) => void }) {
  return (
    <MenuButton fixed className="btn v3d-big tone-violet" title="Sketch: draw lines, rectangles and circles on a plane, then Extrude or Cut them"
      label={<><PencilRuler size={20} /><span>Sketch <ChevronDown size={11} /></span></>}>
      {(close) => (
        <>
          <button className="menu-item" onClick={() => { close(); onPick({ kind: "top" }); }}><ViewIcon face="top" />
            <span>On the plate<small>The top plane - what you draw stands on the plate</small></span></button>
          <button className="menu-item" onClick={() => { close(); onPick({ kind: "front" }); }}><ViewIcon face="front" />
            <span>On the front plane<small>Seen from the front - it stands up, out toward you</small></span></button>
          <button className="menu-item" onClick={() => { close(); onPick({ kind: "right" }); }}><ViewIcon face="right" />
            <span>On the right plane<small>Seen from the right - out toward you</small></span></button>
          <div className="menu-item faint"><Box size={15} /><span>On a box's face<small>Click the face in Select, then Sketch in
            its Face tab</small></span></div>
        </>
      )}
    </MenuButton>
  );
}

/** What to do next in a sketch, said under the bar. */
function sketchHint(s: SketchState, where: string): string {
  const typed = s.typed ? ` Typed: ${s.typed} - Enter puts it down.` : "";
  if (s.tool === "line") {
    return s.draft ? `Line: click the next corner - the first one again closes it, the last one again (or Esc) ends it. `
      + `Type a length and Enter for an exact one.${typed}` : `Line on ${where}: click its first corner. Points snap to whole `
      + "millimetres, and to the corners and middles near the pointer.";
  }
  if (s.tool === "rect") return s.draft ? `Rectangle: click the corner across - or type width;height and Enter.${typed}` : `Rectangle on ${where}: click a corner.`;
  if (s.tool === "circle") return s.draft ? `Circle: click its edge - or type its diameter and Enter.${typed}` : `Circle on ${where}: click its middle.`;
  return "Click a shape to pick it - Delete takes it away; L, R and C draw. Extrude or Cut makes the closed shapes into parts.";
}

/** The Sketch tab (0.100.0): what draws - Line, Rectangle, Circle; Pick, Delete, Undo; the picked shape's size; and
 *  what leaves the sketch - Extrude (so high), Cut (through all or so deep), Cancel. */
function SketchBand({ sketch, where, make, onMake, closed, onTool, onDelete, onUndo, onShape, onExtrude, onCut, onCancel }: {
  sketch: SketchState; where: string; make: SketchMake; onMake: (m: Partial<SketchMake>) => void; closed: number;
  onTool: (t: SketchTool | null) => void; onDelete: () => void; onUndo: () => void; onShape: (s: SketchShape) => void;
  onExtrude: () => void; onCut: () => void; onCancel: () => void }) {
  const sh = sketch.picked !== null ? sketch.shapes[sketch.picked] : null;
  const rect = sh?.kind === "rect" ? { c: [(sh.a[0] + sh.b[0]) / 2, (sh.a[1] + sh.b[1]) / 2], w: Math.abs(sh.b[0] - sh.a[0]),
    h: Math.abs(sh.b[1] - sh.a[1]) } : null;
  const box = (c: number[], w: number, h: number): SketchShape => ({ kind: "rect", a: [r3(c[0] - w / 2), r3(c[1] - h / 2)],
    b: [r3(c[0] + w / 2), r3(c[1] + h / 2)] });
  const xy = "from the plane's 0,0 - a face's middle";
  return (
    <>
      <Grp label="Draw">
        <Big icon={<PenLine size={20} />} label="Line" active={sketch.tool === "line"} onClick={() => onTool("line")}
          title="Line: click each corner - the first again closes it (L)" />
        <Big icon={<RectangleHorizontal size={20} />} label="Rectangle" active={sketch.tool === "rect"} onClick={() => onTool("rect")}
          title="Rectangle: two corners across (R)" />
        <Big icon={<Circle size={20} />} label="Circle" active={sketch.tool === "circle"} onClick={() => onTool("circle")}
          title="Circle: its middle, then its edge (C)" />
      </Grp>
      <Grp label="Change" fold={1} icon={<Undo2 size={20} />}>
        <Stack>
          <Small icon={<MousePointer2 size={16} />} label="Pick" active={!sketch.tool} onClick={() => onTool(null)}
            title="Click a shape to pick it (Esc)" />
          <Small icon={<Trash2 size={16} />} label="Delete" disabled={!sh} onClick={onDelete} title="Take the picked shape away (Delete)" />
          <Small icon={<Undo2 size={16} />} label="Undo" disabled={!sketch.past.length} onClick={onUndo} title="Undo the last shape (Ctrl+Z)" />
        </Stack>
      </Grp>
      {sh && (
        <Grp label="Picked" fold={2} icon={sh.kind === "circle" ? <Circle size={20} /> : sh.kind === "rect" ? <RectangleHorizontal size={20} /> : <PenLine size={20} />}>
          {rect && (
            <>
              <Stack>
                <Field label="Width" unit="mm" value={r3(rect.w)} min={0.1} step={1} width={44} onSet={(v) => onShape(box(rect.c, Math.max(0.1, v), rect.h))} />
                <Field label="Height" unit="mm" value={r3(rect.h)} min={0.1} step={1} width={44} onSet={(v) => onShape(box(rect.c, rect.w, Math.max(0.1, v)))} />
              </Stack>
              <Stack>
                <Field label="X" unit="mm" value={r3(rect.c[0])} step={1} width={44} title={`Its middle, ${xy}`} onSet={(v) => onShape(box([v, rect.c[1]], rect.w, rect.h))} />
                <Field label="Y" unit="mm" value={r3(rect.c[1])} step={1} width={44} title={`Its middle, ${xy}`} onSet={(v) => onShape(box([rect.c[0], v], rect.w, rect.h))} />
              </Stack>
            </>
          )}
          {sh.kind === "circle" && (
            <>
              <Stack>
                <Field label="Diameter" unit="mm" value={r3(2 * sh.r)} min={0.1} step={1} width={44} onSet={(v) => onShape({ ...sh, r: r3(Math.max(0.1, v) / 2) })} />
              </Stack>
              <Stack>
                <Field label="X" unit="mm" value={r3(sh.c[0])} step={1} width={44} title={`Its middle, ${xy}`} onSet={(v) => onShape({ ...sh, c: [v, sh.c[1]] })} />
                <Field label="Y" unit="mm" value={r3(sh.c[1])} step={1} width={44} title={`Its middle, ${xy}`} onSet={(v) => onShape({ ...sh, c: [sh.c[0], v] })} />
              </Stack>
            </>
          )}
          {sh.kind === "line" && (
            <Stack>
              <span className="v3d-col-strong">A line, {sh.pts.length} corners</span>
              <small className="v3d-col-note">{sh.closed ? "Closed: it makes a part" : "Open: close it to make a part"}</small>
            </Stack>
          )}
        </Grp>
      )}
      <Grp label={closed ? `Make - ${closed} ${closed > 1 ? "shapes" : "shape"} closed` : "Make - nothing closed yet"}>
        <Big tone="orange" icon={<ArrowUpFromLine size={20} />} label="Extrude" disabled={!closed} onClick={onExtrude}
          title={`Boss extrude: what is closed, ${fmt(make.height)} mm thick out of ${where} - a part`} />
        <Big tone="grey" icon={<SquareDashed size={20} />} label="Cut" disabled={!closed} onClick={onCut}
          title={`Extruded cut: what is closed, cut into what is there - ${make.through ? "through all" : `${fmt(make.depth)} mm deep`}`} />
        <Stack>
          <Field label="Thickness" unit="mm" value={make.height} min={0.1} step={1} width={44}
            title="Extrude: how thick the part is - how far out of the plane, mm" onSet={(v) => onMake({ height: Math.max(0.1, v) })} />
          <label className="v3d-check" title="Cut through all of it - a box's face: the box; another plane: everything, both ways">
            <input type="checkbox" checked={make.through} onChange={(e) => onMake({ through: e.target.checked })} /> Cut through all</label>
          {!make.through && <Field label="Depth" unit="mm" value={make.depth} min={0.1} step={1} width={44} title="Cut: how far in, mm"
            onSet={(v) => onMake({ depth: Math.max(0.1, v) })} />}
        </Stack>
        {sketch.shapes.length || sketch.draft ? (
          <ConfirmButton className="btn v3d-big tone-red" confirmText="Throw away?" label="Leave the sketch - what is drawn is thrown away"
            onConfirm={onCancel}><X size={20} /><span>Cancel</span></ConfirmButton>
        ) : <Big tone="red" icon={<X size={20} />} label="Cancel" onClick={onCancel} title="Leave the sketch" />}
      </Grp>
    </>
  );
}

/** The Part tab's On the face (0.98.0): where the part is kept on its box's face - from which edges, how far - how
 *  deep a hole goes or how high a solid stands, and Free it: its own numbers place it again, where it is now. */
function OnFaceGroup({ part, box, change, depth }: { part: Part; box: Part; change: (fn: (p: Part) => Part) => void;
  depth: number }) {
  const a = part.on!;
  const F = faceFrame(box, a.face);
  const setOn = (patch: Partial<Anchor>) => change((p) => {
    if (!p.on) return p;
    const on: Anchor = { ...p.on, ...patch };
    for (const k of Object.keys(patch) as (keyof Anchor)[]) if (patch[k] === undefined) delete on[k];
    return { ...p, on };
  });
  return (
    <Grp id="onface" short="On the face" label="On the face" fold={7} icon={<Crosshair size={20} />}>
      <Stack>
        <FromRow face={a.face} axis={0} size={F.W} from={a.from} onSet={(from) => setOn({ from })} />
        <FromRow face={a.face} axis={1} size={F.H} from={a.from} onSet={(from) => setOn({ from })} />
        <small className="v3d-col-note v3d-clip" title={`The ${a.face} of ${label(box)}: ${fmt(F.W)} x ${fmt(F.H)} mm`}>
          {a.face[0].toUpperCase() + a.face.slice(1)} of {label(box)}</small>
      </Stack>
      <Stack>
        {part.hole ? (
          <>
            <label className="v3d-check" title="Through all of the box - however thick it gets">
              <input type="checkbox" checked={!!a.through} onChange={(e) => setOn(e.target.checked ? { through: true, depth: undefined }
                : { through: undefined, depth: a.depth ?? depth })} /> Through all</label>
            {!a.through && <Field label="Depth" unit="mm" value={a.depth ?? depth} min={MIN_SIZE} step={1}
              title="How far into the box, mm" onSet={(v) => setOn({ depth: Math.max(MIN_SIZE, v) })} />}
          </>
        ) : (
          <Field label="Height" unit="mm" value={a.depth ?? F.T} min={MIN_SIZE} step={1} title="How far out of the face, mm"
            onSet={(v) => setOn({ depth: Math.max(MIN_SIZE, v) })} />
        )}
        <Small icon={<Unlink size={16} />} label="Free it" onClick={() => change((p) => { const q = { ...p }; delete q.on; return q; })}
          title="Not kept on the face any more: it stays where it is now, and stays there when the box moves" />
      </Stack>
    </Grp>
  );
}

/** Letters apart (0.92.0): a text with more than one letter as a part per letter, each where it was. */
function ApartButton({ part, onApart }: { part: Part; onApart: () => void }) {
  const letters = [...(part.text ?? "")].filter((c) => c.trim()).length;
  return (
    <Big icon={<Split size={20} />} label="Letters apart" disabled={letters < 2} onClick={onApart}
      title={letters < 2 ? "One letter: nothing to take apart"
        : `Each of its ${letters} letters a text part of its own, just where it is: click one to size, move or turn it`} />
  );
}

/** Why a part has no lines of its own to round, cut or resize in the Lines tab - or null. */
function edgeless(p: Part): string | null {
  if (p.radius) return "That box is rounded all round: set All rounded (Part) to 0 to round single edges.";
  if (EDGES[p.shape]) return null;
  return p.shape === "group" ? "A group's lines are its parts': click one on the model to pick it."
    : p.shape === "mesh" ? "An imported mesh's lines cannot be rounded or resized yet."
      : `A ${kindOf(p).toLowerCase()} has no lines of its own to round or resize.`;
}

/** The Lines tab (0.92.0 as Edges): lines picked first - clicked on the model, or by name - then what they get: a
 *  fillet, a chamfer or nothing; a new size resizes the picked lines that have one. One line picked: Resize sets how
 *  long it is (0.94.0) - a box over the view, Enter sets it. */
function LinesBand({ part, many, picked, pickedOn, size, picking, onPicking, onName, onChip, onTreat, onSize, onClear,
  length, onResize, cutter }: {
  part: Part | null; many: boolean; picked: number; pickedOn: string[]; size: number; picking: boolean;
  onPicking: () => void; onName: (key: string) => void; onChip: (key: string, size: number) => void;
  onTreat: (kind: EdgeKind | null) => void; onSize: (v: number) => void; onClear: () => void;
  length: { length: number; what: string } | null; onResize: () => void; cutter: boolean }) {
  const why = part ? edgeless(part) : null;
  const shape = part?.shape;
  const names: [string, string][] = !part || why ? [] : shape === "outline" ? [["vertical", "Corners"]]
    : shape === "cylinder" || shape === "cone" ? [["top", "Top"], ["bottom", "Bottom"], ["all", "Both"]]
      : shape === "wedge" ? [["slope", "Slope"], ["bottom", "Bottom"], ["vertical", "Vertical"], ["all", "All"]]
        : [["top", "Top"], ["bottom", "Bottom"], ["vertical", "Vertical"], ["all", "All"]];
  const has = (key: string) => {
    const es = part ? edgesOf(part.shape, key) : [];
    return es.length > 0 && es.every((e) => pickedOn.includes(e));
  };
  const now = part && !why ? (["fillet", "chamfer"] as const).flatMap((k) => Object.entries(part[k] ?? {}).map(([key, v]) => ({ k, key, v }))) : [];
  const where = (key: string) => (key === "all" ? "every edge" : key === "vertical" ? (shape === "outline" ? "its corners"
    : "the upright edges") : `the edges round its ${key}`);
  const inside = cutter ? " - those inside the part the hole cuts (its inside corners), not those at an opening" : "";
  const first = names.slice(0, 3), rest = names.slice(3);
  const byName = ([key, name]: [string, string]) => (
    <Small key={key} icon={<EdgeIcon which={key} size={16} />} label={name} active={has(key)} onClick={() => onName(key)}
      title={has(key) ? `Unpick ${where(key)}` : `Pick ${where(key)}`} />
  );
  return (
    <>
      <Grp label={part && !why ? `Pick: ${kindOf(part).toLowerCase()} ${part.id}${cutter ? " (hole)" : ""}` : "Pick"}>
        <Big icon={<MousePointerClick size={20} />} label="Pick lines" active={picking} onClick={onPicking}
          title="Click lines on the model to pick several - again to unpick (a plain click near a line picks it alone). Esc stops." />
        {first.length > 0 && <Stack>{first.map(byName)}</Stack>}
        <Stack>
          {rest.map(byName)}
          <Small icon={<X size={16} />} label="Unpick all" disabled={!picked} onClick={onClear} title="No line picked (Esc)" />
        </Stack>
      </Grp>
      <Grp id="resize" short="Resize" label={length ? `${length.what}: ${fmt(length.length)} mm` : "One line"} fold={4} icon={<Ruler size={20} />}>
        <Big icon={<Ruler size={20} />} label="Resize" disabled={!length} onClick={onResize}
          title={length ? `Make it another ${length.what.toLowerCase()} - type it in the box by the line, Enter sets it`
            : picked > 1 ? "Pick one line to resize it" : "Pick a line to resize it"} />
      </Grp>
      <Grp id="treat" short="Fillet" label={picked ? `${picked} line${picked > 1 ? "s" : ""} picked` : "Round or cut"} fold={3}
        icon={<SquareRoundCorner size={20} />}>
        <Big icon={<SquareRoundCorner size={20} />} label="Fillet" disabled={!picked} onClick={() => onTreat("fillet")}
          title={`The picked lines rounded: radius ${fmt(size)} mm${inside}`} />
        <Big icon={<ChamferIcon />} label="Chamfer" disabled={!picked} onClick={() => onTreat("chamfer")}
          title={`The picked lines cut off: ${fmt(size)} mm along each face${inside}`} />
        <Stack>
          <Field label="Size" unit="mm" value={size} min={0.1} step={0.5} onSet={onSize} width={46}
            title="The fillet's radius or the chamfer's reach, mm. Changed: the picked lines that have one get it" />
          <Small icon={<Eraser size={16} />} label="Take off" disabled={!picked} onClick={() => onTreat(null)}
            title="The picked lines sharp again" />
        </Stack>
      </Grp>
      {why && <p className="v3d-tip">{why}</p>}
      {!part && !many && <p className="v3d-tip">Click a line on the model to pick it - or pick a part and its lines by name.</p>}
      {many && <p className="v3d-tip">Pick one part to pick its lines by name - or click lines on the model.</p>}
      {part && !why && (
        <Grp label="Rounded or cut - click to pick" short="On it" fold={1} icon={<SquareRoundCorner size={20} />}>
          <div className={`v3d-gallery${now.length ? "" : " empty"}`} role="group" aria-label="Its lines rounded or cut">
            {now.length ? now.map(({ k, key, v }) => (
              <button key={k + key} type="button" className={`v3d-small${has(key) ? " on" : ""}`} aria-pressed={has(key)}
                title={has(key) ? "Unpick these lines" : "Pick these lines - then change the size, or Take off"}
                onClick={() => onChip(key, v)}><EdgeIcon which={key} size={16} /><span>{key} · {k} {fmt(v)} mm</span></button>
            )) : <span>No line rounded or cut</span>}
          </div>
        </Grp>
      )}
    </>
  );
}

/** A square with a corner cut off - Chamfer's icon, beside Fillet's rounded one. */
function ChamferIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M21 11 13 3" />
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
    </svg>
  );
}

/** A small picture of which edges a button rounds. */
function EdgeIcon({ which, size = 20 }: { which: string; size?: number }) {
  const on = (x: string) => (which === "all" || which === x ? "currentColor" : "none");
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <rect x="3" y="5" width="14" height="11" rx="1" opacity="0.45" />
      <path d="M3 5h14" stroke={which === "top" || which === "slope" || which === "all" ? "currentColor" : "none"} strokeWidth="3" />
      <path d="M3 16h14" stroke={on("bottom")} strokeWidth="3" />
      <path d="M3 5v11M17 5v11" stroke={which === "vertical" || which === "all" ? "currentColor" : "none"} strokeWidth="3" />
      {which === "top" || which === "bottom" || which === "vertical" || which === "all" || which === "slope" ? null
        : <circle cx="10" cy="10.5" r="3" stroke="currentColor" />}
    </svg>
  );
}

/** Copies of the part or group (0.88.0): turned round a point - a snowflake's arms, a star's points - or in a row. */
/** Pattern (0.88.0): copies of the part round a point or in a row - one button (0.94.0), its settings in its menu. */
function PatternButton({ part, change }: { part: Part; change: (fn: (p: Part) => Part) => void }) {
  const r = part.repeat;
  const set = (fn: (x: Repeat) => Repeat) => change((p) => (p.repeat ? { ...p, repeat: fn({ ...p.repeat }) } : p));
  const b = extent(part);
  const w = b ? b[1][0] - b[0][0] : 20;
  const around = repeatAround(part);
  return (
    <MenuButton fixed className={`btn v3d-big${r ? " on" : ""}`} menuClassName="v3d-foldmenu"
      title={r ? `${r.count} of it, ${r.step ? "in a row" : "turned round a point"} - change it` : "Copies of it round a point or in a row"}
      label={<><Repeat2 size={20} /><span>{r ? `Pattern ×${r.count}` : "Pattern"} <ChevronDown size={11} /></span></>}>
      {(close) => (
        <div className="v3d-foldbody">
          {!r ? (
            <>
              <Big icon={<Repeat2 size={20} />} label="Around" onClick={() => { close(); change((p) => ({ ...p, repeat: { count: 6 } })); }}
                title={part.shape === "group" ? "Six copies turned round the plate's middle - set Around for another point"
                  : "Six copies turned round its own position - set Around for another point"} />
              <Big icon={<Columns3 size={20} />} label="In a row" title="Three copies in a row"
                onClick={() => { close(); change((p) => ({ ...p, repeat: { count: 3, step: [r3(w + 5), 0, 0] } })); }} />
            </>
          ) : (
            <>
              <span className="v3d-foldhead">{r.step ? "In a row" : r.around ? "Turned round a point" : part.shape === "group"
                ? "Round the plate's middle" : "Round its own position"}</span>
              <Num label="Copies" unit="in all" value={r.count} min={2}
                onSet={(v) => set((x) => ({ ...x, count: Math.max(2, Math.min(MAX_COPIES, Math.round(v))) }))} />
              {r.step ? ["X", "Y", "Z"].map((ax, i) => <Num key={ax} label={`Step ${ax}`} value={r.step![i]}
                onSet={(v) => set((x) => { const st = [...(x.step ?? [0, 0, 0])] as Vec3; st[i] = v; return { ...x, step: st }; })} />) : (
                <>
                  <Num label="Angle" unit="° from the first to the last (360: all round)" step={15} value={r.angle ?? 360}
                    onSet={(v) => set((x) => ({ ...x, angle: Math.max(-360, Math.min(360, v)) }))} />
                  {["X", "Y"].map((ax, i) => <Num key={ax} label={`Around ${ax}`} value={around[i]}
                    onSet={(v) => set((x) => { const a = [...around] as [number, number]; a[i] = v; return { ...x, around: a }; })} />)}
                </>
              )}
              <Big icon={<X size={20} />} label="Remove" title="No copies" onClick={() => { close(); change((p) => ({ ...p, repeat: undefined })); }} />
            </>
          )}
        </div>
      )}
    </MenuButton>
  );
}
