// The 3D editor (0.87.0): a design (.3d.json) in the chat's or the project's folder, edited Tinkercad-style beside
// the chat - shapes, holes, groups, moving, turning, sizing, an imported STL - on the chosen printer's plate. The
// assistant works on the same file: it reads it with every message while the editor is open, writes it with its
// file tools (and checks it with `hub3d design`), and what it writes shows here. "Point at" puts a spot on the model
// into the message. Loaded only when opened: three.js and manifold-3d (WebAssembly) are big.
import { createContext, Fragment, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type ReactNode } from "react";
import * as THREE from "three";
import Module from "manifold-3d/manifold";
import wasmUrl from "manifold-3d/manifold.wasm?url";
import { AlignCenter, AlignLeft, AlignRight, ArrowDownToLine, ArrowDownUp, Bold, Box, ChevronDown, CircleDot, Columns3, Cone,
  Copy, Crosshair, Cylinder, Download, Eraser, Eye, EyeOff, FlipHorizontal2, FlipVertical2, Group, Home, Italic, ListTree,
  Maximize2, Minimize2, MousePointerClick, Move3d, PanelBottom, Printer as PrinterIcon, Redo2, Repeat2, Rotate3d, Ruler,
  Scaling, Shapes, Split, SquareDashed, SquareMousePointer, SquareRoundCorner, Trash2, TriangleAlert, Triangle, Type, Undo2,
  Ungroup, Upload, X } from "lucide-react";
import fontIndex from "./fonts/index.json";
import { useApp } from "../state";
import { ConfirmButton, MenuButton } from "../components/ui";
import type { DesignFolder } from "./folder";
import { allIds, build, buildPart, COLORS, edgeLength, edgeLines, edgePlan, EDGES, edgesOf, extent, extentOf, FONT_FAMILIES, fontFile,
  follows, freshId, joined, lettersApart, MAX_COPIES, MIN_SIZE, mirrorPart, nearestEdge, newDesign, partSize, readDesign,
  repeatAround, retext, SPACING, stl, textMeasure, threeMf, transformPart, treatEdges, withCapitals, withEdgeLength, writeDesign,
  type Align, type Box3, type Built, type Design, type EdgeKind, type FontData, type FontIndex, type Fonts, type Kit,
  type Mat3, type MeshData, type Part, type Repeat, type Shape, type Vec3, type Wasm } from "./geometry";
import { baseName, dirOf, fmt, joinPath, MESH_FILE, pointMark, relativePath } from "./files";
import { parseMesh } from "./meshio";
import { lastPrinter, PRINTERS, printerOf, rememberPrinter, type Printer } from "./printers";
import { centre, copy, find, group, label, pick, remove, replaceWith, ungroup, update } from "./tree";
import { Viewport, type Hit, type ViewName } from "./Viewport";

type Mode = "translate" | "rotate" | "scale";
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
const SNAPS = [0, 0.1, 0.5, 1, 2, 5, 10];
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
  const [mode, setMode] = useState<Mode>("translate");
  const [snap, setSnap] = useState(1);
  const [built, setBuilt] = useState<Built | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [problems, setProblems] = useState<string[]>([]);
  const [outside, setOutside] = useState<string | null>(null);
  const [tool, setTool] = useState<null | "point" | "flat" | "edge">(null);
  const [edgeSize, setEdgeSize] = useState(2);
  const [edgeSel, setEdgeSel] = useState<Record<string, string[]>>({});       // picked edges, by part (0.92.0)
  const [hoverEdge, setHoverEdge] = useState<{ part: string; edge: string } | null>(null);
  const hoverFrame = useRef(0);
  const [several, setSeveral] = useState(false);                               // Pick several: a click adds
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

  /** A change made here: kept for Undo, shown, and saved a moment later. */
  const commit = useCallback((next: Design) => {
    const cur = designRef.current;
    if (cur) {
      past.current.push(cur);
      if (past.current.length > 100) past.current.shift();
    }
    future.current = [];
    setDesign(next);
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
    if (!items.length || tool) {
      v.detach();
      return;
    }
    const one = items.length === 1 && !asGroup(items[0]) ? items[0] : null;
    const b = union(builts.map((x, i) => x.box ?? extent(items[i])));
    v.attach(one ? one.at : b ? centre(b) : [0, 0, 0], one ? one.rotate : null, mode, snap);
  }, [design, selected, mode, snap, ready, tool, meshTick]);

  // The picked lines (0.92.0) drawn over the model, and the one a click would pick (0.94.0: near a line, on any tab).
  useEffect(() => {
    const v = vp.current;
    if (!v || !ready) return;
    const list: { lines: Vec3[][]; hover?: boolean }[] = [];
    if (design) {
      for (const [id, edges] of Object.entries(edgeSel)) {
        const f = find(design.parts, id);
        if (f) list.push({ lines: edgeLines(f.part, edges) });
      }
      const f = hoverEdge ? find(design.parts, hoverEdge.part) : null;
      if (f && hoverEdge) list.push({ lines: edgeLines(f.part, [hoverEdge.edge]), hover: true });
    }
    v.setMarks(list);
  }, [design, edgeSel, hoverEdge, ready]);

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
    v.setPreview(pick(d.parts, ids).map((p) => ({ built: buildPart(p, k), color: p.hole ? GREY : p.color ?? GREY })));
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

  const onPointerDown = (e: React.PointerEvent) => {
    host.current?.focus({ preventScroll: true });
    const v = vp.current;
    if (!v || e.button !== 0 || v.overGizmo) return;
    const hit = v.pick(e.clientX, e.clientY);
    const body = !tool && !!hit && !e.shiftKey && selectedRef.current.some((s) => within(designRef.current?.parts ?? [], s, hit.part));
    pointer.current = { x: e.clientX, y: e.clientY, hit, body };
    if (body) {
      v.controls.enabled = false;                      // the part moves, not the view
      (e.target as Element).setPointerCapture?.(e.pointerId);
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const v = vp.current, p = pointer.current;
    if (v && !p && (!tool || tool === "edge")) {        // the line a click would pick, shown
      const x = e.clientX, y = e.clientY;
      cancelAnimationFrame(hoverFrame.current);
      hoverFrame.current = requestAnimationFrame(() => {
        const vv = vp.current;
        const hit = vv && !vv.overGizmo ? vv.pick(x, y) : null;
        const got = !hit ? null : tool === "edge" ? edgeOf(hit) : lineAt(hit);
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
    const q = (x: number) => (snap > 0 ? Math.round(x / snap) * snap : x);
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

  /** A line within a few pixels of a spot on the model (0.94.0): a click there picks the line, not the part. */
  const lineAt = (hit: Hit, px = 7): { part: Part; edge: string } | null => {
    const v = vp.current, d = designRef.current;
    const part = d ? find(d.parts, hit.part)?.part : null;
    if (!v || !part || edgeless(part)) return null;
    const near = nearestEdge(part, hit.point);
    return near && near.distance <= px * v.mmPerPx(hit.point) ? { part, edge: near.edge } : null;
  };

  /** A line picked (0.92.0, 0.94.0): alone - or, `add` (Shift, Pick several, Pick lines), with the others, or unpicked.
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
    setTab("lines");
  };

  /** Pick lines (0.92.0): a click picks the line nearest it - however far - or unpicks it. */
  const pickEdge = (hit: Hit) => {
    const got = edgeOf(hit);
    if (typeof got === "string") {
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

  /** The picked edges given a fillet or chamfer of the size field's size, or none. */
  const treatPicked = (kind: EdgeKind | null, size = edgeSize) => {
    const d = designRef.current;
    if (!d || !Object.keys(edgeSel).length) return;
    let ps = d.parts;
    for (const [id, edges] of Object.entries(edgeSel)) {          // not a part rounded all round since, nor a hole
      ps = update(ps, id, (p) => (edgeless(p) ? p : tidy(treatEdges(p, edges, kind ? { kind, size } : null))));
    }
    commit({ ...d, parts: ps });
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
    const letters = lettersApart(f.part, kk.fonts, allIds(d.parts));
    if (!letters) {
      toast("Its font is not loaded yet - try again in a moment.", "fault");
      return;
    }
    commit({ ...d, parts: replaceWith(d.parts, id, letters) });
    setSelected(letters.map((l) => l.id));
    host.current?.focus({ preventScroll: true });     // its button is gone: Esc and the arrow keys work on
    toast(`${letters.length} letters, each a part of its own: click one to move, size or turn it - Pick several for more.`, "run");
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
    if (tool === "edge") {
      if (hit) pickEdge(hit);
      return;
    }
    if (!hit) {
      if (!e.shiftKey && !several) {
        setSelected([]);
        setEdgeSel({});
      }
      return;
    }
    const line = lineAt(hit);                          // a line: it, and the Lines tab (0.94.0)
    if (line) {
      pickLine(line, e.shiftKey || several);
      return;
    }
    const id = topOf(hit.part);                        // a part: it, and the Part tab
    if (e.shiftKey || several) {
      const cur = selectedRef.current;
      setSelected(cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]);
    } else {
      setSelected([id]);
      setEdgeSel({});
    }
    const text = designRef.current ? find(designRef.current.parts, id)?.part.shape === "text" : false;
    setTab((t) => (t === "text" && text ? t : "part"));
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
    setMode("translate");
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
    const seen = allIds(d.parts);
    const copies = pick(d.parts, selectedRef.current).map((p) => tidy(transformPart(copy(p, seen), [0, 0, 0], [5, 5, 0], null)));
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

  const onKey = (e: React.KeyboardEvent) => {
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !designRef.current) return;
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();
    const step = (snap || 1) * (e.shiftKey ? 10 : 1);
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
        else {
          setSelected([]);
          setEdgeSel({});
        }
      },
      ArrowLeft: () => nudge([-step, 0, 0]), ArrowRight: () => nudge([step, 0, 0]),
      ArrowUp: () => nudge([0, step, 0]), ArrowDown: () => nudge([0, -step, 0]),
      PageUp: () => nudge([0, 0, step]), PageDown: () => nudge([0, 0, -step]),
      g: () => setMode("translate"), r: () => setMode("rotate"), s: () => setMode("scale"),
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
  const shownTab: Tab = tab === "text" && !texts ? "part" : tab;
  const pickedEdges = Object.entries(edgeSel).reduce((n, [id, es]) => n + (find(parts, id) ? es.length : 0), 0);
  const oneLine = (() => {                             // the one line picked: how long it is (Resize)
    const all = Object.entries(edgeSel).flatMap(([id, es]) => es.map((e) => [id, e] as const));
    const f = all.length === 1 ? find(parts, all[0][0]) : null;
    return f ? edgeLength(f.part, all[0][1]) : null;
  })();
  // What the band shows: a change folds its groups afresh to the room there is (0.94.0).
  const fitKey = [shownTab, selected.join(","), items.map((x) => `${x.shape}${x.radius ? "r" : ""}${x.repeat ? (x.repeat.step ? "s" : "a")
    : ""}${x.hole ? "h" : ""}`).join(","), pickedEdges, one ? (edgeSel[one.id] ?? []).length : 0, oneLine ? 1 : 0].join("|");
  const allProblems = [...problems, ...(built?.problems ?? [])];
  const hide = (id: string) => change(id, (p) => ({ ...p, hidden: !p.hidden || undefined }));
  const pickFirst = <p className="v3d-tip">Click a part on the model to change it - or a line on it, to round, cut or
    resize that line. Shift-click (or Pick several) picks more, a double click picks a part inside a group, and dragging a
    picked part moves it on the plate.</p>;

  return (
    <aside className="panel panel-3d" aria-label="3D editor" onKeyDown={onKey}>
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
            {TABS.map(([id, name]) => (
              <button key={id} type="button" role="tab" aria-selected={shownTab === id} className={`v3d-tab${shownTab === id ? " on" : ""}`}
                onClick={() => setTab(id)}>{name}</button>
            ))}
            {texts && (
              <button type="button" role="tab" aria-selected={shownTab === "text"} className={`v3d-tab context${shownTab === "text" ? " on" : ""}`}
                onClick={() => setTab("text")} title={texts.length > 1 ? "The picked texts: their font and letters"
                  : "The picked text: its letters, font and lines"}>Text</button>
            )}
          </div>
          <div className="v3d-quick">
            <MenuButton right className="btn small quiet" title="The design's parts: pick one, hide or show it" menuClassName="wide v3d-partsmenu"
              label={<><ListTree size={15} /> Parts{parts.length ? ` (${allIds(parts).size})` : ""} <ChevronDown size={13} /></>}>
              {(close) => (
                <>
                  {parts.length > 1 && (
                    <button type="button" className="menu-item" onClick={() => { close(); setSelected(parts.map((x) => x.id)); }}>
                      <span>Pick all<small>Every part, Ctrl+A</small></span></button>
                  )}
                  <PartList parts={parts} onHide={hide} onPick={(id) => {
                    if (!several) close();
                    setSelected(several ? (selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]) : [id]);
                  }} />
                </>
              )}
            </MenuButton>
          </div>
        </div>
        <Band label={shownTab === "text" ? "Text" : TABS.find(([id]) => id === shownTab)?.[1]} fit={fitKey}>
          {shownTab === "insert" && (
            <>
              <Grp label="Solids">
                <Big icon={<Box size={20} />} label="Box" onClick={() => add("box")} />
                <Big icon={<Cylinder size={20} />} label="Cylinder" onClick={() => add("cylinder")} />
                <Big icon={<Cone size={20} />} label="Cone" onClick={() => add("cone")} />
                <Big icon={<CircleDot size={20} />} label="Sphere" onClick={() => add("sphere")} />
                <Big icon={<Triangle size={20} />} label="Wedge" title="A wedge: a ramp" onClick={() => add("wedge")} />
                <Big icon={<Type size={20} />} label="Text" onClick={() => add("text")} />
              </Grp>
              <Grp label="Holes" fold={2} icon={<SquareDashed size={20} />}>
                <Big icon={<SquareDashed size={20} />} label="Hole" title="A box-shaped hole - through the part picked" onClick={() => add("box", true)} />
                <Big icon={<Cylinder size={20} strokeDasharray="3 2" />} label="Round hole" title="A round hole - through the part picked"
                  onClick={() => add("cylinder", true)} />
              </Grp>
              <Grp label="From a file" fold={1} icon={<Upload size={20} />}>
                <MenuButton fixed className="btn v3d-big" title="An STL, 3MF or OBJ into the design" menuClassName="wide"
                  label={<><Upload size={20} /><span>Import</span></>}>
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
                    <ConfirmButton className="btn v3d-big" confirmText="Delete?" label="Delete them" onConfirm={del}>
                      <Trash2 size={20} /><span>Delete</span></ConfirmButton>
                  </Grp>
                  <Grp label="Plate" fold={4} icon={<ArrowDownToLine size={20} />}>
                    <Big icon={<ArrowDownToLine size={20} />} label="Onto the plate" title="Each moved up or down so its lowest point is on the plate" onClick={drop} />
                  </Grp>
                  <Grp label="Arrange" fold={3} icon={<Group size={20} />}>
                    <Big icon={<Group size={20} />} label="Group" title="Group them (Ctrl+G): a hole in a group cuts only its parts" onClick={doGroup} />
                    <MirrorButton onMirror={mirror} />
                  </Grp>
                  <Grp label="Align" fold={2} icon={<AlignCenter size={20} />}>
                    <div className="v3d-aligngrid">
                      {ALIGNS.map(([ax, names], i) => (
                        <Fragment key={ax}>
                          <span className="faint">{ax}</span>
                          {names.map((n, j) => (
                            <button key={n} type="button" className="btn small" title={`Line them up: ${n.toLowerCase()} (${ax})`}
                              onClick={() => align(i, j as 0 | 1 | 2)}>{n}</button>))}
                        </Fragment>
                      ))}
                    </div>
                  </Grp>
                </>
              )}
              {one && k && <PartBand part={one} fonts={k.fonts} lock={lock} setLock={setLock} change={(fn) => change(one.id, fn)}
                onDuplicate={duplicate} onDelete={del} onApart={() => splitLetters(one.id)} onDrop={drop}
                onFlat={() => setTool(tool === "flat" ? null : "flat")} flat={tool === "flat"} onUngroup={doUngroup} onMirror={mirror} />}
            </>
          )}
          {shownTab === "text" && texts && k && <TextBand parts={texts} fonts={k.fonts} onApart={() => splitLetters(texts[0].id)}
            change={(fn) => (texts.length > 1 ? changeEach(texts.map((t) => t.id), fn) : change(texts[0].id, fn))} />}
          {shownTab === "lines" && (
            <LinesBand part={one} many={items.length > 1} picked={pickedEdges} pickedOn={one ? edgeSel[one.id] ?? [] : []}
              size={edgeSize} picking={tool === "edge"} onPicking={() => setTool(tool === "edge" ? null : "edge")}
              onName={(key) => { if (one) pickEdges(one, key); }}
              onChip={(key, v) => { if (one) { pickEdges(one, key); setEdgeSize(v); } }}
              onTreat={(kind) => treatPicked(kind)} onSize={resizePicked} onClear={() => setEdgeSel({})}
              length={oneLine} onResize={openResize} />
          )}
        </Band>
      </div>
      <div className="v3d-stage">
        <div className={`v3d-view${tool ? " picking" : ""}`} ref={host} tabIndex={0} onPointerDown={onPointerDown}
          onPointerMove={onPointerMove} onPointerUp={onPointerUp} onDoubleClick={onDoubleClick}
          onPointerLeave={() => { cancelAnimationFrame(hoverFrame.current); setHoverEdge(null); }}
          aria-label="The design in 3D. Drag to turn the view, right-drag to pan, scroll to zoom. Click a part to pick it, a line to pick the line." />
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
          <div className="v3d-heads" role="toolbar" aria-label="Handles and view">
            <Tool icon={<Move3d size={15} />} title="Move: the handles move what is picked (G)" active={mode === "translate"}
              onClick={() => setMode("translate")}><span>Move</span></Tool>
            <Tool icon={<Rotate3d size={15} />} title="Turn: the handles turn what is picked (R)" active={mode === "rotate"}
              onClick={() => setMode("rotate")}><span>Turn</span></Tool>
            <Tool icon={<Scaling size={15} />} title="Size: the handles size what is picked (S)" active={mode === "scale"}
              onClick={() => setMode("scale")}><span>Size</span></Tool>
            <select className="select" value={snap} onChange={(e) => setSnap(Number(e.target.value))} aria-label="Snap"
              title="Moving snaps to this grid, turning to 15°; the arrow keys move a step">
              {SNAPS.map((s) => <option key={s} value={s}>{s ? `Snap ${s} mm` : "No snap"}</option>)}
            </select>
            <span className="v3d-sep" />
            <Tool icon={<Home size={15} />} title="Fit the design in view" onClick={() => view("home")}><span>Fit</span></Tool>
            <Tool icon={<span className="v3d-letter">T</span>} title="From the top" onClick={() => view("top")}><span>Top</span></Tool>
            <Tool icon={<span className="v3d-letter">F</span>} title="From the front" onClick={() => view("front")}><span>Front</span></Tool>
            <Tool icon={<span className="v3d-letter">R</span>} title="From the right" onClick={() => view("right")}><span>Right</span></Tool>
            <span className="v3d-sep" />
            <Tool icon={<SquareMousePointer size={15} />} active={several} onClick={() => setSeveral(!several)}
              title="Pick several: each click on the model picks one more part - or unpicks it - as Shift-click does"><span>Pick several</span></Tool>
            <Tool icon={<Crosshair size={15} />} title="Point at a spot on the model: it goes into your message to the assistant"
              active={tool === "point"} onClick={() => setTool(tool === "point" ? null : "point")}><span>Point at</span></Tool>
          </div>
          {tool && <div className="v3d-hint">{tool === "point" ? "Click the spot on the model. Esc stops."
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
type Tab = "insert" | "part" | "lines" | "text";
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

/** A ribbon button: its icon over its name. */
function Big({ icon, label, title, onClick, active, disabled }: { icon: ReactNode; label: string; title?: string;
  onClick: () => void; active?: boolean; disabled?: boolean }) {
  return (
    <button type="button" className={`btn v3d-big${active ? " on" : ""}`} onClick={onClick} disabled={disabled}
      title={title ?? label} aria-pressed={active}>{icon}<span>{label}</span></button>
  );
}

function PartList({ parts, onPick, onHide, depth = 0 }: { parts: Part[]; onPick: (id: string) => void;
  onHide: (id: string) => void; depth?: number }) {
  if (!parts.length && !depth) return <p className="v3d-note faint">No parts yet.</p>;
  return (
    <ul className="v3d-parts" style={depth ? { paddingLeft: 14 } : undefined}>
      {parts.map((p) => (
        <li key={p.id}>
          <div className="row">
            <span className={`v3d-swatch${p.hole ? " hole" : ""}`} style={p.hole || p.shape === "group" ? undefined : { background: p.color ?? GREY }} />
            <button className="link-btn grow ellipsis" onClick={() => onPick(p.id)}>{label(p)}</button>
            {p.hole && <span className="tag">hole</span>}
            {p.shape === "group" && <span className="tag">group</span>}
            <button className="btn small quiet" onClick={() => onHide(p.id)} aria-label={p.hidden ? `Show ${label(p)}` : `Hide ${label(p)}`}
              title={p.hidden ? "Hidden: left out of the solid. Show it" : "Hide it: left out of the solid"}>
              {p.hidden ? <><EyeOff size={14} /> Show</> : <><Eye size={14} /> Hide</>}</button>
          </div>
          {p.parts && <PartList parts={p.parts} onPick={onPick} onHide={onHide} depth={depth + 1} />}
        </li>
      ))}
    </ul>
  );
}

const kindOf = (p: Part) => (p.shape === "group" ? "Group" : p.shape === "mesh" ? "Imported mesh"
  : p.shape[0].toUpperCase() + p.shape.slice(1));

/** The Part tab for one part (0.94.0: with what Size and place and Arrange had): its name, solid or hole and colour;
 *  its size, centre and turn; what its shape has of its own; the plate; mirror and pattern; duplicate, hide, delete. */
function PartBand({ part, fonts, lock, setLock, change, onDuplicate, onDelete, onApart, onDrop, onFlat, flat, onUngroup,
  onMirror }: { part: Part; fonts: Fonts; lock: boolean; setLock: (v: boolean) => void; change: (fn: (p: Part) => Part) => void;
  onDuplicate: () => void; onDelete: () => void; onApart: () => void; onDrop: () => void; onFlat: () => void; flat: boolean;
  onUngroup: () => void; onMirror: (axis: 0 | 1 | 2) => void }) {
  const g = part.shape === "group";
  const box = extent(part);
  return (
    <>
      <Grp label={<>{kindOf(part)} <span className="mono">{part.id}</span></>}>
        <div className="v3d-stack">
          <input className="input v3d-name" defaultValue={part.name ?? ""} key={`${part.id}:${part.name ?? ""}`} placeholder={label(part)}
            aria-label="Name" title="Its name in Parts and in the assistant's messages"
            onBlur={(e) => { if (e.target.value !== (part.name ?? "")) change((p) => ({ ...p, name: e.target.value || undefined })); }}
            onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
          <div className="v3d-seg" role="group" aria-label="Solid or hole">
            <button className={`btn small${!part.hole ? " on" : ""}`} aria-pressed={!part.hole} onClick={() => change((p) => ({ ...p, hole: undefined }))}>Solid</button>
            <button className={`btn small${part.hole ? " on" : ""}`} aria-pressed={!!part.hole} title="A hole cuts the solids beside it (H)"
              onClick={() => change((p) => ({ ...p, hole: true }))}>Hole</button>
            {!g && !part.hole && <input type="color" className="v3d-color" value={part.color ?? GREY} aria-label="Colour" title="Colour (for the eye only: one colour prints)"
              onChange={(e) => change((p) => ({ ...p, color: e.target.value }))} />}
          </div>
        </div>
      </Grp>
      {!g && (
        <Grp id="place" short="Size" label={part.shape === "outline" ? "Size, its points' 0,0 and turn" : "Size, centre and turn"} fold={6}
          icon={<Scaling size={20} />}>
          <PlaceTable part={part} fonts={fonts} lock={lock} setLock={setLock} change={change} bottom={box?.[0][2] ?? 0} />
        </Grp>
      )}
      {g && (
        <Grp label="Group" fold={3} icon={<Group size={20} />}>
          <p className="v3d-note num">{box ? `${fmt(box[1][0] - box[0][0])} x ${fmt(box[1][1] - box[0][1])} x ${fmt(box[1][2] - box[0][2])} mm, ` : ""}
            {part.parts?.length ?? 0} parts. A hole in it cuts only its own parts; double-click one on the model to change it.
            Move, turn and size it with the handles.</p>
        </Grp>
      )}
      {part.shape === "box" && (
        <Grp label="Box" fold={3} icon={<Box size={20} />}>
          <Num label="All rounded" unit="mm: every edge and corner - or round some lines in Lines" value={part.radius ?? 0} min={0} step={0.5}
            onSet={(v) => change((p) => (v > 0 ? { ...p, radius: v, sides: p.sides ?? 32, fillet: undefined, chamfer: undefined }
              : { ...p, radius: undefined, sides: p.fillet ? p.sides : undefined }))} />
          {!!part.radius && <Num label="Sides" unit="segments round a corner" value={part.sides ?? 64} min={3} step={8}
            onSet={(v) => change((p) => ({ ...p, sides: Math.round(Math.min(256, Math.max(3, v))) }))} />}
        </Grp>
      )}
      {(part.shape === "cylinder" || part.shape === "cone" || part.shape === "sphere") && (
        <Grp label={kindOf(part)} fold={3} icon={part.shape === "cone" ? <Cone size={20} /> : part.shape === "sphere" ? <CircleDot size={20} /> : <Cylinder size={20} />}>
          <Num label="Sides" unit="segments around" value={part.sides ?? 64} min={3} step={8}
            onSet={(v) => change((p) => ({ ...p, sides: Math.round(Math.min(256, Math.max(3, v))) }))} />
          {part.shape === "cone" && <Num label="Top, %" unit="% of the base" value={(part.top ?? 0) * 100} min={0} step={10}
            onSet={(v) => change((p) => ({ ...p, top: Math.min(1, v / 100) || undefined }))} />}
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
        <MirrorButton onMirror={onMirror} />
        <PatternButton part={part} change={change} />
        {g && <Big icon={<Ungroup size={20} />} label="Ungroup" title="Ungroup (Ctrl+Shift+G)" onClick={onUngroup} />}
      </Grp>
      <Grp label="Part" fold={5} icon={<Copy size={20} />}>
        <Big icon={<Copy size={20} />} label="Duplicate" title="Duplicate (Ctrl+D)" onClick={onDuplicate} />
        <Big icon={part.hidden ? <EyeOff size={20} /> : <Eye size={20} />} label={part.hidden ? "Show" : "Hide"}
          title={part.hidden ? "Hidden: left out of the solid. Show it" : "Hide it: left out of the solid"}
          onClick={() => change((p) => ({ ...p, hidden: !p.hidden || undefined }))} />
        <ConfirmButton className="btn v3d-big" confirmText="Delete?" label="Delete (Del)" onConfirm={onDelete}>
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
function TextBand({ parts, fonts, change, onApart }: { parts: Part[]; fonts: Fonts; change: (fn: (p: Part) => Part) => void;
  onApart: () => void }) {
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
  const LINES: [Align, ReactNode, string][] = [["left", <AlignLeft size={15} />, "Left"], ["center", <AlignCenter size={15} />, "Centre"],
    ["right", <AlignRight size={15} />, "Right"]];
  return (
    <>
      {many ? (
        <Grp label="Texts"><p className="v3d-note">{parts.length} texts picked: {parts.map((p) => JSON.stringify(p.text ?? "")).join(", ")}.
          A change here is made to each.</p></Grp>
      ) : (
        <Grp label="Text">
          <textarea className="textarea v3d-textarea" rows={2} defaultValue={part.text ?? ""} key={`${part.id}:${part.text ?? ""}`}
            aria-label="Text" title="Its letters; a new line is a new line of text"
            onBlur={(e) => { if (e.target.value !== part.text) restyle({ text: e.target.value }); }} />
          <ApartButton part={part} onApart={onApart} />
        </Grp>
      )}
      <Grp id="font" short="Font" fold={2} icon={<Type size={20} />} label={many ? (oneFont ? fam.name : "Fonts") : t ? t.font : "Font"}>
        <div className="v3d-stack">
          <select className="select" value={oneFont ? fontOf(part) : ""} aria-label="Font"
            onChange={(e) => { if (e.target.value) restyle({ font: e.target.value === fonts.index.default ? undefined : e.target.value }); }}>
            {!oneFont && <option value="">Several fonts</option>}
            {FONT_FAMILIES.map((f) => <option key={f} value={f}>{f[0].toUpperCase() + f.slice(1)} - {fonts.index.families[f]?.name}</option>)}
          </select>
          <div className="v3d-seg">
            <button className={`btn small${bold && canBold ? " on" : ""}`} aria-pressed={bold} disabled={!canBold}
              title={canBold ? "Bold" : `${fam.name} has no bold`} onClick={() => restyle({ bold: !bold || undefined })}><Bold size={14} /> Bold</button>
            <button className={`btn small${italic ? " on" : ""}`} aria-pressed={italic}
              title={fam.italic || many ? "Italic" : `Slanted (${fam.name} has no italic)`}
              onClick={() => restyle({ italic: !italic || undefined })}><Italic size={14} /> Italic</button>
          </div>
        </div>
      </Grp>
      <Grp id="letters" short="Letters" fold={3} icon={<Scaling size={20} />}
        label={many ? "Letters" : t ? `Letters: ${fmt(t.width)} x ${fmt(t.height)} mm` : "Letters"}>
        <div className="v3d-stack">
          <div className="v3d-pair">
            <Num label="Capitals, mm" value={r3(caps[0])} min={0.1}
              unit={many && caps.some((c) => Math.abs(c - caps[0]) > 1e-6) ? "mm: how tall each one's capitals are - now not the same"
                : "mm: how tall its capitals are - it sizes the text"}
              onSet={(v) => change((p) => withCapitals(p, v, fonts))} />
            <Num label="Spacing" unit="from letter to letter: 1 is the font's own" step={0.05} value={part.letter_spacing ?? 1}
              min={SPACING[0]} onSet={spacing("letter_spacing")} />
          </div>
          {!many && (
            <small className="faint" title={t ? `${t.lines > 1 ? `${t.lines} lines in ` : ""}${t.font}` : undefined}>
              {part.size[0] <= 0 ? "Its width follows the letters" : part.size[1] <= 0 ? "Its height follows the letters"
                : stretched ? <>Stretched to its size: <button className="link-btn small" onClick={() => change((p) => ({ ...p, size: [0, p.size[1], p.size[2]] }))}>undo</button></>
                  : "Its width follows the letters"}</small>
          )}
        </div>
      </Grp>
      <Grp id="lines" short="Lines" fold={1} icon={<AlignCenter size={20} />} label="Lines">
        <div className="v3d-stack">
          <div className="v3d-seg" role="group" aria-label="The lines">
            {LINES.map(([a, icon, name]) => (
              <button key={a} className={`btn small${align === a ? " on" : ""}`} aria-pressed={align === a}
                title={`Lines ${a === "center" ? "centred" : `to the ${a}`}`} onClick={() => restyle({ align: a === "center" ? undefined : a })}>{icon} {name}</button>
            ))}
          </div>
          <label className="v3d-inline" title="From line to line: 1 is the font's own">
            <span>Spacing</span>
            <NumInput value={part.line_spacing ?? 1} min={SPACING[0]} step={0.05} onSet={spacing("line_spacing")} />
          </label>
        </div>
      </Grp>
    </>
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
  if (p.hole) return "A hole's rim where it meets a part cannot be rounded yet.";
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
  length, onResize }: {
  part: Part | null; many: boolean; picked: number; pickedOn: string[]; size: number; picking: boolean;
  onPicking: () => void; onName: (key: string) => void; onChip: (key: string, size: number) => void;
  onTreat: (kind: EdgeKind | null) => void; onSize: (v: number) => void; onClear: () => void;
  length: { length: number; what: string } | null; onResize: () => void }) {
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
  return (
    <>
      <Grp label={part && !why ? `Pick: ${kindOf(part).toLowerCase()} ${part.id}` : "Pick"}>
        <Big icon={<MousePointerClick size={20} />} label="Pick lines" active={picking} onClick={onPicking}
          title="Click lines on the model to pick several - again to unpick (a plain click near a line picks it alone). Esc stops." />
        {names.length > 0 && (
          <div className="v3d-edgenames" role="group" aria-label="Pick lines by name">
            {names.map(([key, name]) => (
              <button key={key} type="button" className={`btn small${has(key) ? " on" : ""}`} aria-pressed={has(key)}
                onClick={() => onName(key)} title={has(key) ? `Unpick ${where(key)}` : `Pick ${where(key)}`}>
                <EdgeIcon which={key} size={16} /> {name}</button>
            ))}
          </div>
        )}
      </Grp>
      <Grp id="resize" short="Resize" label={length ? `${length.what}: ${fmt(length.length)} mm` : "One line"} fold={4} icon={<Ruler size={20} />}>
        <Big icon={<Ruler size={20} />} label="Resize" disabled={!length} onClick={onResize}
          title={length ? `Make it another ${length.what.toLowerCase()} - type it in the box by the line, Enter sets it`
            : picked > 1 ? "Pick one line to resize it" : "Pick a line to resize it"} />
      </Grp>
      <Grp id="treat" short="Fillet" label={picked ? `${picked} line${picked > 1 ? "s" : ""} picked` : "Nothing picked"} fold={3} icon={<SquareRoundCorner size={20} />}>
        <Big icon={<SquareRoundCorner size={20} />} label="Fillet" disabled={!picked} onClick={() => onTreat("fillet")}
          title={`The picked lines rounded: radius ${fmt(size)} mm`} />
        <Big icon={<ChamferIcon />} label="Chamfer" disabled={!picked} onClick={() => onTreat("chamfer")}
          title={`The picked lines cut off: ${fmt(size)} mm along each face`} />
        <Big icon={<Eraser size={20} />} label="Take off" disabled={!picked} onClick={() => onTreat(null)}
          title="The picked lines sharp again" />
        <div className="v3d-stack">
          <label className="v3d-inline" title="The fillet's radius or the chamfer's reach. Changed: the picked lines that have one get it">
            <span>Size</span><NumInput value={size} min={0.1} step={0.5} onSet={onSize} label="Size" /><span>mm</span>
          </label>
          <button type="button" className="btn small quiet" disabled={!picked} onClick={onClear} title="No line picked (Esc)">
            <X size={14} /> Unpick all</button>
        </div>
      </Grp>
      {why && <p className="v3d-tip">{why}</p>}
      {!part && !many && <p className="v3d-tip">Click a line on the model to pick it - or pick a part and its lines by name.</p>}
      {many && <p className="v3d-tip">Pick one part to pick its lines by name - or click lines on the model.</p>}
      {part && !why && (
        <Grp label="Now - click to pick" short="Now" fold={1} icon={<SquareRoundCorner size={20} />}>
          <div className="v3d-edgelist">
            {now.length ? now.map(({ k, key, v }) => (
              <button key={k + key} type="button" className={`btn small v3d-chip${has(key) ? " on" : ""}`} aria-pressed={has(key)}
                title={has(key) ? "Unpick these lines" : "Pick these lines - then change the size, or Take off"}
                onClick={() => onChip(key, v)}>{key} · {k} {fmt(v)}</button>
            )) : <span className="faint">No line rounded or cut</span>}
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
