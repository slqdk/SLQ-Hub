// The 3D editor's view (0.87.0): three.js with Z up, the printer's plate and build volume, the design's solid in
// its parts' colours, holes as ghosts, the selection's outline, a gizmo to move, turn and size it, and picking.
// No React here: Editor.tsx drives it.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { toCreasedNormals } from "three/addons/utils/BufferGeometryUtils.js";
import type { Box3, Built, MeshData, Vec3 } from "./geometry";

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

export interface Hit { part: string; point: Vec3; normal: Vec3 }
export type ViewName = "home" | "top" | "front" | "right";

const EDGE_LIMIT = 150_000;               // triangles above which the solid's edges are not drawn (slow to find)

function css(name: string, fallback: string): THREE.Color {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  try {
    return new THREE.Color(v || fallback);
  } catch {
    return new THREE.Color(fallback);
  }
}

function geometryOf(m: MeshData, colors?: Float32Array): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  const n = m.triangles.length;
  const pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const v = m.triangles[i];
    pos[i * 3] = m.positions[v * 3]; pos[i * 3 + 1] = m.positions[v * 3 + 1]; pos[i * 3 + 2] = m.positions[v * 3 + 2];
  }
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  if (colors) g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const out = toCreasedNormals(g, Math.PI / 7);
  if (out !== g) g.dispose();
  out.computeBoundingSphere();
  return out;
}

function disposeGroup(g: THREE.Object3D): void {
  for (const c of [...g.children]) {
    g.remove(c);
    c.traverse((o) => {
      const any = o as THREE.Mesh;
      any.geometry?.dispose();
      const mat = any.material as THREE.Material | THREE.Material[] | undefined;
      if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
      else mat?.dispose();
    });
  }
}

export class Viewport {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  readonly gizmo: TransformControls;
  readonly proxy = new THREE.Object3D();
  private plate = new THREE.Group();
  private result = new THREE.Group();
  private ghosts = new THREE.Group();
  private outline = new THREE.Group();
  readonly preview = new THREE.Group();
  private solid: THREE.Mesh | null = null;
  private triPart: string[] = [];
  private pickables: THREE.Object3D[] = [];
  private frame = 0;
  private ro: ResizeObserver;
  private bed: Vec3 = [256, 256, 256];
  private raycaster = new THREE.Raycaster();
  onChange: (() => void) | null = null;

  constructor(private host: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.domElement.className = "v3d-canvas";
    host.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.5, 20000);
    this.camera.up.set(0, 0, 1);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.screenSpacePanning = true;
    this.controls.addEventListener("change", () => this.render());
    this.gizmo = new TransformControls(this.camera, this.renderer.domElement);
    this.gizmo.addEventListener("change", () => this.render());
    this.gizmo.addEventListener("dragging-changed", (e) => { this.controls.enabled = !(e as unknown as { value: boolean }).value; });
    this.gizmo.size = 0.8;
    this.scene.add(this.gizmo.getHelper());
    this.scene.add(this.proxy, this.plate, this.result, this.ghosts, this.outline, this.preview);
    this.preview.matrixAutoUpdate = false;
    const hemi = new THREE.HemisphereLight(0xffffff, 0x6b6f72, 1.6);
    this.scene.add(hemi);
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(0.5, -0.4, 1);
    this.camera.add(key);
    key.target.position.set(0, 0, -1);
    this.camera.add(key.target);
    this.scene.add(this.camera);
    this.theme();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    this.resize();
  }

  /** The page's light or dark colours. */
  theme(): void {
    this.scene.background = css("--paper", "#f4f5f3");
    this.setPlate(this.bed);
  }

  dispose(): void {
    cancelAnimationFrame(this.frame);
    this.ro.disconnect();
    this.gizmo.detach();
    this.gizmo.dispose();
    this.controls.dispose();
    for (const g of [this.plate, this.result, this.ghosts, this.outline, this.preview]) disposeGroup(g);
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  private resize(): void {
    const w = Math.max(1, this.host.clientWidth), h = Math.max(1, this.host.clientHeight);
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.render();
  }

  render(): void {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.renderer.render(this.scene, this.camera);
    });
  }

  // ---------------------------------------------------------------- the plate

  setPlate(bed: Vec3): void {
    this.bed = bed;
    disposeGroup(this.plate);
    const [w, d, h] = bed;
    const plate = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshStandardMaterial({
      color: css("--sheet", "#ffffff"), roughness: 1, metalness: 0, polygonOffset: true, polygonOffsetFactor: 2,
      polygonOffsetUnits: 2, side: THREE.DoubleSide }));
    plate.position.z = -0.02;
    plate.renderOrder = -1;
    this.plate.add(plate);
    const minor: number[] = [], major: number[] = [];
    for (let x = 0; x <= w / 2; x += 10) for (const s of x ? [-1, 1] : [1]) (x % 50 === 0 ? major : minor).push(s * x, -d / 2, 0, s * x, d / 2, 0);
    for (let y = 0; y <= d / 2; y += 10) for (const s of y ? [-1, 1] : [1]) (y % 50 === 0 ? major : minor).push(-w / 2, s * y, 0, w / 2, s * y, 0);
    const line = (pts: number[], color: THREE.Color, opacity: number) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
    };
    this.plate.add(line(minor, css("--rule-2", "#e1e5e2"), 1), line(major, css("--rule", "#b7beba"), 1));
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(w, d, h)),
      new THREE.LineBasicMaterial({ color: css("--ink-3", "#7a8388"), transparent: true, opacity: 0.35 }));
    edges.position.z = h / 2;
    this.plate.add(edges);
    const axis = (to: Vec3, color: number) => {
      const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-w / 2, -d / 2, 0.05),
        new THREE.Vector3(-w / 2 + to[0], -d / 2 + to[1], 0.05 + to[2])]);
      return new THREE.Line(g, new THREE.LineBasicMaterial({ color }));
    };
    this.plate.add(axis([20, 0, 0], 0xd23c3c), axis([0, 20, 0], 0x2f9e44), axis([0, 0, 20], 0x1c64b4));
    this.render();
  }

  // ---------------------------------------------------------------- what the design makes

  /** The solid, each triangle in its part's colour; loose meshes in amber. */
  setResult(b: Built | null, colorOf: (part: string) => string): void {
    disposeGroup(this.result);
    this.solid = null;
    this.triPart = [];
    this.pickables = this.pickables.filter((o) => o.parent === this.ghosts);
    if (b && b.triangles.length) {
      const n = b.triangles.length;
      const colors = new Float32Array(n * 3);
      const cache = new Map<string, THREE.Color>();
      for (let t = 0; t < n / 3; t++) {
        const id = b.triPart[t] ?? "";
        let c = cache.get(id);
        if (!c) {
          c = new THREE.Color(colorOf(id));
          cache.set(id, c);
        }
        for (let k = 0; k < 3; k++) {
          colors[(t * 3 + k) * 3] = c.r; colors[(t * 3 + k) * 3 + 1] = c.g; colors[(t * 3 + k) * 3 + 2] = c.b;
        }
      }
      const g = geometryOf(b, colors);
      const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.72, metalness: 0.04,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
      this.solid = mesh;
      this.triPart = b.triPart;
      this.result.add(mesh);
      this.pickables.push(mesh);
      if (n / 3 <= EDGE_LIMIT) {
        this.result.add(new THREE.LineSegments(new THREE.EdgesGeometry(g, 28),
          new THREE.LineBasicMaterial({ color: 0x1b1f22, transparent: true, opacity: 0.28 })));
      }
    }
    for (const l of b?.loose ?? []) {
      const mesh = new THREE.Mesh(geometryOf(l.mesh), new THREE.MeshStandardMaterial({ color: 0xd99a2b, roughness: 0.8,
        side: THREE.DoubleSide }));
      mesh.userData.part = l.part;
      this.result.add(mesh);
      this.pickables.push(mesh);
    }
    this.render();
  }

  /** Holes, each drawn alone, see-through: they cut, so they are not in the solid. */
  setGhosts(list: { part: string; built: Built }[]): void {
    disposeGroup(this.ghosts);
    this.pickables = this.pickables.filter((o) => o.parent !== this.ghosts && o.parent);
    const color = css("--ink-3", "#7a8388");
    for (const { part, built } of list) {
      if (!built.triangles.length) continue;
      const g = geometryOf(built);
      const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color, transparent: true, opacity: 0.22,
        depthWrite: false, roughness: 1 }));
      mesh.userData.part = part;
      mesh.renderOrder = 2;
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(g, 28), new THREE.LineBasicMaterial({ color,
        transparent: true, opacity: 0.6, depthWrite: false }));
      edges.renderOrder = 2;
      this.ghosts.add(mesh, edges);
      this.pickables.push(mesh);
    }
    this.render();
  }

  /** The selected parts' edges in the accent colour, over everything. */
  setOutline(list: Built[]): void {
    disposeGroup(this.outline);
    const color = css("--act", "#1c64b4");
    for (const b of list) {
      if (!b.triangles.length) continue;
      const g = geometryOf(b);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(g, 28), new THREE.LineBasicMaterial({ color,
        depthTest: false, transparent: true, opacity: 0.9 }));
      edges.renderOrder = 5;
      const wash = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.1,
        depthWrite: false }));
      wash.renderOrder = 4;
      this.outline.add(wash, edges);
    }
    this.render();
  }

  /** While something is dragged: it alone, as it was when the drag began (moved by `matrix`), the rest faded. */
  setPreview(list: { built: Built; color: string }[] | null): void {
    disposeGroup(this.preview);
    this.preview.matrix.identity();
    const fade = !!list;
    this.result.traverse((o) => {
      const m = (o as THREE.Mesh).material as THREE.Material & { opacity: number } | undefined;
      if (!m) return;
      if (m.userData.opacity === undefined) {           // as it was made: what to go back to
        m.userData.opacity = m.opacity;
        m.userData.wasTransparent = m.transparent;
      }
      m.transparent = fade || m.userData.wasTransparent === true;
      m.opacity = fade ? Math.min(m.userData.opacity, 0.3) : m.userData.opacity;
      m.needsUpdate = true;
    });
    this.outline.visible = !fade;
    for (const { built, color } of list ?? []) {
      if (!built.triangles.length) continue;
      this.preview.add(new THREE.Mesh(geometryOf(built), new THREE.MeshStandardMaterial({ color, roughness: 0.7,
        transparent: true, opacity: 0.85 })));
    }
    this.render();
  }

  movePreview(m: THREE.Matrix4): void {
    this.preview.matrix.copy(m);
    this.preview.matrixWorldNeedsUpdate = true;
    this.render();
  }

  // ---------------------------------------------------------------- picking

  private ray(clientX: number, clientY: number): void {
    const r = this.renderer.domElement.getBoundingClientRect();
    const p = new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(p, this.camera);
  }

  /** The part under the pointer: where, and which way that face looks. */
  pick(clientX: number, clientY: number): Hit | null {
    this.ray(clientX, clientY);
    const hits = this.raycaster.intersectObjects(this.pickables, false);
    for (const h of hits) {
      const part = h.object === this.solid ? this.triPart[h.faceIndex ?? -1] : h.object.userData.part;
      if (!part) continue;
      const n = h.face?.normal ?? new THREE.Vector3(0, 0, 1);
      return { part, point: [h.point.x, h.point.y, h.point.z], normal: [n.x, n.y, n.z] };
    }
    return null;
  }

  /** Where the pointer meets the level plane at height z. */
  onPlane(clientX: number, clientY: number, z: number): Vec3 | null {
    this.ray(clientX, clientY);
    const p = new THREE.Vector3();
    return this.raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), -z), p) ? [p.x, p.y, p.z] : null;
  }

  // ---------------------------------------------------------------- the gizmo

  attach(at: Vec3, rotate: Vec3 | null, mode: "translate" | "rotate" | "scale", snap: number): void {
    this.proxy.position.set(...at);
    this.proxy.rotation.set(...((rotate ?? [0, 0, 0]).map((d) => (d * Math.PI) / 180) as Vec3), "ZYX");
    this.proxy.scale.set(1, 1, 1);
    this.proxy.updateMatrixWorld(true);
    this.gizmo.setMode(mode);
    this.gizmo.setSpace(mode === "translate" ? "world" : "local");
    this.gizmo.setTranslationSnap(snap > 0 ? snap : null);
    this.gizmo.setRotationSnap(snap > 0 ? THREE.MathUtils.degToRad(15) : null);
    this.gizmo.attach(this.proxy);
    this.render();
  }

  detach(): void {
    this.gizmo.detach();
    this.render();
  }

  get overGizmo(): boolean {
    return !!this.gizmo.object && this.gizmo.axis !== null;
  }

  // ---------------------------------------------------------------- the camera

  /** Whether all of `box` is on the screen now. */
  inView(box: Box3): boolean {
    this.camera.updateMatrixWorld();
    const v = new THREE.Vector3();
    for (const x of [box[0][0], box[1][0]]) for (const y of [box[0][1], box[1][1]]) for (const z of [box[0][2], box[1][2]]) {
      v.set(x, y, z).project(this.camera);
      if (Math.abs(v.x) > 0.96 || Math.abs(v.y) > 0.96 || v.z > 1) return false;
    }
    return true;
  }

  /** Look at `box` (the design, or the plate when it is empty) from a standard side. */
  view(name: ViewName, box: Box3 | null): void {
    const [w, d, h] = this.bed;
    const lo = box ? box[0] : [-w / 4, -d / 4, 0], hi = box ? box[1] : [w / 4, d / 4, h / 6];
    const c = new THREE.Vector3((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2);
    const r = Math.max(10, new THREE.Vector3(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]).length() / 2);
    const half = THREE.MathUtils.degToRad(this.camera.fov / 2);          // a tall view is narrower than it is high
    const dist = (r / Math.sin(Math.min(half, Math.atan(Math.tan(half) * this.camera.aspect)))) * 1.15;
    const dir = { home: new THREE.Vector3(0.55, -1, 0.75), top: new THREE.Vector3(0, -0.0001, 1),
      front: new THREE.Vector3(0, -1, 0.0001), right: new THREE.Vector3(1, 0, 0.0001) }[name].normalize();
    this.camera.position.copy(c).addScaledVector(dir, dist);
    this.controls.target.copy(c);
    this.camera.near = Math.max(0.1, dist / 200);
    this.camera.far = dist * 50 + Math.max(w, d, h) * 4;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.render();
  }
}
