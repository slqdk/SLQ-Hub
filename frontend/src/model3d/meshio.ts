// Mesh files for the 3D editor (0.87.0): STL with geometry.ts's own reader (as hub3d reads it), 3MF and OBJ with
// three.js's loaders - every object joined, its placement applied, in the file's millimetres.
import * as THREE from "three";
import { ThreeMFLoader } from "three/addons/loaders/3MFLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { readStl, type MeshData } from "./geometry";

function fromObject(root: THREE.Object3D): MeshData {
  root.updateMatrixWorld(true);
  const pos: number[] = [];
  const tri: number[] = [];
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh || !mesh.geometry) return;
    const g = mesh.geometry as THREE.BufferGeometry;
    const p = g.getAttribute("position");
    if (!p) return;
    const base = pos.length / 3;
    const v = new THREE.Vector3();
    for (let i = 0; i < p.count; i++) {
      v.fromBufferAttribute(p, i).applyMatrix4(mesh.matrixWorld);
      pos.push(v.x, v.y, v.z);
    }
    const index = g.getIndex();
    if (index) for (let i = 0; i < index.count; i++) tri.push(base + index.getX(i));
    else for (let i = 0; i < p.count; i++) tri.push(base + i);
  });
  if (!tri.length) throw new Error("no triangles in it");
  return { positions: new Float32Array(pos), triangles: new Uint32Array(tri) };
}

/** A mesh file's triangles. */
export function parseMesh(name: string, data: ArrayBuffer): MeshData {
  const ext = name.split(".").pop()?.toLowerCase();
  if (ext === "stl") return readStl(new Uint8Array(data));
  if (ext === "3mf") return fromObject(new ThreeMFLoader().parse(data));
  if (ext === "obj") return fromObject(new OBJLoader().parse(new TextDecoder().decode(data)));
  throw new Error(`${name}: the editor reads STL, 3MF and OBJ files`);
}
