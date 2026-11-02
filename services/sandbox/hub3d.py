"""hub3d: 3D models to print, in the sandbox (0.67.1, after chat 255).

Build the solid with manifold3d - what it makes is always closed - then let hub3d save, check and draw it:

    import hub3d
    hub3d.save(solid, "out/part.stl", bed=180)    # STL or 3MF; prints what it checked, refuses what is not a solid
    hub3d.views(solid, "out/part-views.png")      # from the front, the side and above: look at it (view_image)

In bash:

    hub3d recipe                    manifold3d's API (3.5) and worked examples - read it before writing the code
    hub3d check FILE [--bed 180]    is FILE a closed solid; its size, volume and parts; does it fit the bed
    hub3d views FILE OUT.png        a picture of FILE from three sides

Chat 255 (0.66.0): a model guessed at manifold3d for an hour (Cylinder, union, to_stl, from_stl, a 4x4 transform
...), wrote STL files by hand - Bambu Studio found no geometry data in the one it called "validated" - and ended
with a mesh with 13 edges shared by four to eight triangles. Here the recipe's examples run when the sandbox image
is built (hub3d selftest), files are written by code that is tested, and a model is judged by counting: edges,
triangles, volume.

services/sandbox/Dockerfile puts this file in /opt/hub3d, on /opt/py's path through a .pth file; the Hub's tests
load it from the repo. Pictures are drawn as the Hub's app/model3d/mesh.py draws them - the same code, which a test
keeps the same."""
from __future__ import annotations

import argparse
import io
import math
import os
import re
import struct
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
from PIL import Image, ImageDraw

__all__ = ["NotPrintable", "Report", "check", "load", "save", "solid", "views", "RECIPE"]

MAX_TRIANGLES = 1_000_000                 # more makes slicers slow
STL_RECORD = np.dtype([("normal", "<f4", (3,)), ("corners", "<f4", (3, 3)), ("attr", "<u2")])     # 50 bytes
CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PRODUCTION = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
UNITS_MM = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}


class NotPrintable(ValueError):
    """Not a closed solid, or empty: what a slicer would refuse, fill wrongly or print with gaps."""


# ---------------------------------------------------------------- what is checked

class Report:
    """What check() and save() found. str() is the text to read; ok is True when it is a closed solid."""

    def __init__(self, name: str, kind: str = ""):
        self.name, self.kind = name, kind
        self.triangles = 0
        self.size = (0.0, 0.0, 0.0)
        self.lowest = (0.0, 0.0, 0.0)
        self.volume = 0.0                 # mm3; negative when the triangles face inwards
        self.parts = 0
        self.cavities = 0
        self.open_edges = self.shared_edges = self.flipped_edges = 0
        self.flat = 0                     # triangles without area
        self.bed: tuple[float, float, float] | None = None
        self.problems: list[str] = []     # it is not a closed solid
        self.warnings: list[str] = []     # it is one, but look
        self.notes: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def watertight(self) -> bool:
        return self.triangles > 0 and not self.open_edges and not self.shared_edges

    def __repr__(self) -> str:
        return f"<hub3d.Report {self.name}: {'ok' if self.ok else 'NOT printable'}, {self.triangles:,} triangles>"

    def __str__(self) -> str:
        head = f"{self.name}" + (f" ({self.kind})" if self.kind else "") + f": {self.triangles:,} triangles"
        lines = [head]
        if self.triangles:
            x, y, z = self.size
            lines.append(f"size {x:.1f} x {y:.1f} x {z:.1f} mm (X x Y x Z)")
        if self.ok:
            what = (f"volume {self.volume / 1000:.1f} cm3" if abs(self.volume) >= 100
                    else f"volume {self.volume:.1f} mm3")
            lines.append(f"closed solid: yes - every edge joins two triangles, all facing out; {what}")
            parts = f"{self.parts} part" + ("s" if self.parts != 1 else "")
            if self.cavities:
                parts += f", {self.cavities} sealed cavit{'y' if self.cavities == 1 else 'ies'} inside"
            lines.append(parts)
        else:
            lines.append("closed solid: NO")
            lines += [f"- {p}" for p in self.problems]
        if self.bed and self.ok and not any(w.startswith("too big") for w in self.warnings):
            lines.append(f"fits the {_dims(self.bed)} mm bed")
        lines += [f"! {w}" for w in self.warnings]
        lines += [f"({n})" for n in self.notes]
        if self.ok:
            lines.append("READY for the slicer." if not self.warnings else "A closed solid - read the ! lines.")
        else:
            lines.append("NOT READY: a slicer may refuse it, fill it wrongly or print it with gaps. If you made it, "
                         "build it from manifold3d solids (hub3d recipe) rather than triangles of your own.")
        return "\n".join(lines)


def _dims(v) -> str:
    return " x ".join(f"{float(a):g}" for a in v)


def _bed(bed) -> tuple[float, float, float] | None:
    """180, (180, 180, 180), "180x180x180" or "256,256,256" -> (x, y, z) in mm."""
    if bed is None or bed == "":
        return None
    if isinstance(bed, str):
        parts = [p for p in re.split(r"[x*, ]+", bed.strip().lower()) if p]
    elif isinstance(bed, (int, float)):
        parts = [bed]
    else:
        parts = list(bed)
    try:
        nums = [float(p) for p in parts]
    except (TypeError, ValueError):
        raise ValueError(f"bed: {bed!r} is not a size in mm (180, or 180x180x180)") from None
    if len(nums) == 1:
        nums *= 3
    if len(nums) != 3 or not all(0 < n < 10_000 for n in nums):
        raise ValueError(f"bed: {bed!r} is not a size in mm (180, or 180x180x180)")
    return nums[0], nums[1], nums[2]


def _welded(v: np.ndarray, f: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The triangles as a slicer reads them from a file: corners as 32-bit floats, and corners at the same point
    one vertex. STL keeps no shared vertices, so this is how its triangles are joined again."""
    v32 = np.ascontiguousarray(v, dtype=np.float32) + np.float32(0)            # -0.0 is 0.0
    key = v32.view(np.dtype((np.void, 12))).ravel()
    _, first, inverse = np.unique(key, return_index=True, return_inverse=True)
    return v32[first].astype(np.float64), inverse.reshape(-1)[f]


def _labels(faces: np.ndarray, n: int) -> np.ndarray:
    """Each vertex's part: the smallest vertex number of the triangles it is joined to through others (as
    app/model3d/mesh.py's bodies(), returning the labels)."""
    lab = np.arange(n, dtype=np.int64)
    if not len(faces):
        return lab
    flat = faces.reshape(-1)
    for _ in range(10_000):
        m = np.repeat(lab[faces].min(1), 3)
        new = lab.copy()
        np.minimum.at(new, flat, m)
        np.minimum.at(new, lab[flat], m)
        new = new[new]
        new = new[new]
        if np.array_equal(new, lab):
            break
        lab = new
    return lab


def _analyse(v: np.ndarray, f: np.ndarray, name: str, kind: str = "", bed=None) -> Report:
    r = Report(name, kind)
    r.bed = _bed(bed)
    r.triangles = int(len(f))
    if not len(f):
        r.problems.append("it has no triangles")
        return r
    if not np.isfinite(v[np.unique(f)]).all():
        r.problems.append("some corners are not numbers (NaN or infinite)")
        return r
    vw, fw = _welded(v, f)
    n = len(vw)
    used = np.unique(fw)
    lo, hi = vw[used].min(0), vw[used].max(0)
    r.size, r.lowest = tuple(float(a) for a in hi - lo), tuple(float(a) for a in lo)
    tri = vw[fw] - (lo + hi) / 2                                           # centred: less rounding in the sums
    same = (fw[:, 0] == fw[:, 1]) | (fw[:, 1] == fw[:, 2]) | (fw[:, 2] == fw[:, 0])
    area2 = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    r.flat = int((same | (area2 <= 1e-12)).sum())
    g = fw[~same]                                           # a triangle with a corner twice has no edges
    a, b = g.reshape(-1), g[:, [1, 2, 0]].reshape(-1)
    _, edge, count = np.unique(np.minimum(a, b) * n + np.maximum(a, b), return_inverse=True, return_counts=True)
    _, run = np.unique((a * n + b)[count[edge] == 2], return_counts=True)   # an edge of two triangles, run twice
    r.open_edges, r.shared_edges = int((count == 1).sum()), int((count > 2).sum())
    r.flipped_edges = int((run > 1).sum())
    six = np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2]))
    r.volume = float(six.sum() / 6)
    lab = _labels(g, n)
    ids, part = np.unique(lab[g[:, 0]], return_inverse=True)             # each triangle's part
    vol = np.bincount(part, weights=six[~same], minlength=len(ids)) / 6
    box_lo = np.full((len(ids), 3), np.inf)
    box_hi = np.full((len(ids), 3), -np.inf)
    np.minimum.at(box_lo, part, vw[g].min(1))
    np.maximum.at(box_hi, part, vw[g].max(1))
    out = vol > 0
    r.parts = int(out.sum())
    inward = 0
    for i in np.nonzero(~out)[0]:                  # a shell turned in inside another is a sealed cavity: fine
        if ((box_lo[out] <= box_lo[i]).all(1) & (box_hi[i] <= box_hi[out]).all(1)).any():
            r.cavities += 1
        else:
            inward += 1
    if r.open_edges:
        r.problems.append(f"{r.open_edges:,} edges belong to only one triangle: the surface has holes, it does not "
                          "close")
    if r.shared_edges:
        r.problems.append(f"{r.shared_edges:,} edges belong to more than two triangles: surfaces meet or cross "
                          "there")
    if r.flipped_edges:
        r.problems.append(f"{r.flipped_edges:,} edges are run the same way by both their triangles: some triangles "
                          "face inwards")
    if not r.problems and inward:
        r.problems.append("the triangles face inwards (its volume comes out negative or 0): reverse each triangle's "
                          "corners - faces[:, ::-1]" if inward == len(ids) else
                          f"{inward} of its {len(ids)} parts face inwards (volume negative or 0) - reverse their "
                          "triangles' corners")
    elif not r.problems and not r.parts:
        r.problems.append("it encloses no volume")
    if r.bed:
        over = [f"{ax} {s:.1f} > {b:g}" for ax, s, b in zip("XYZ", r.size, r.bed) if s > b + 1e-6]
        if over:
            turned = r.size[1] <= r.bed[0] + 1e-6 and r.size[0] <= r.bed[1] + 1e-6 and r.size[2] <= r.bed[2] + 1e-6
            r.warnings.append(f"too big for the {_dims(r.bed)} mm bed: {', '.join(over)}"
                              + (" - it fits turned 90 degrees" if turned else " - scale it down or split it"))
    biggest = max(r.size)
    if biggest < 1:
        r.warnings.append("under 1 mm across: slicers read STL in millimetres - made in metres (scale by 1000) or "
                          "centimetres (by 10)?")
    elif biggest > 1000:
        r.warnings.append(f"{biggest:,.0f} mm across: over a metre - a unit mistake?")
    if r.ok and r.parts > 1:
        r.warnings.append(f"{r.parts} separate parts: they print side by side as they lie; for one object join them "
                          "(a + b) so they overlap")
    if r.triangles > MAX_TRIANGLES:
        r.warnings.append(f"{r.triangles:,} triangles: slicers get slow over {MAX_TRIANGLES:,} - fewer circular "
                          "segments or n_divisions")
    if r.flat:
        r.notes.append(f"{r.flat:,} triangles without area - harmless, slicers drop them")
    return r


# ---------------------------------------------------------------- reading and writing files

def _read_stl(data: bytes) -> tuple[np.ndarray, np.ndarray, str]:
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if len(data) == 84 + 50 * n or (len(data) > 84 + 50 * n and not data[:5].lower() == b"solid"):
            rec = np.frombuffer(data, dtype=STL_RECORD, count=n, offset=84)
            v = rec["corners"].astype(np.float64).reshape(-1, 3)
            return v, np.arange(len(v), dtype=np.int64).reshape(-1, 3), "binary STL"
    text = data.decode("ascii", errors="replace")
    if "facet" in text and "vertex" in text:
        corners = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)
        try:
            v = np.array(corners, dtype=np.float64)
        except ValueError:
            raise ValueError("an ASCII STL with a corner that is not three numbers") from None
        if len(v) % 3:
            raise ValueError("an ASCII STL whose facets do not all have three corners")
        return v, np.arange(len(v), dtype=np.int64).reshape(-1, 3), "ASCII STL"
    raise ValueError("not an STL file: neither binary (84 bytes, then 50 a triangle) nor ASCII (solid, facet, "
                     "vertex)")


def _matrix(text: str | None) -> np.ndarray:
    """A 3MF transform ("m00 m01 m02 m10 ... m32") as 4 x 3: point @ m[:3] + m[3]."""
    if not text:
        return np.vstack([np.eye(3), np.zeros(3)])
    return np.array([float(x) for x in text.split()], dtype=np.float64).reshape(4, 3)


def _read_3mf(data: bytes) -> tuple[np.ndarray, np.ndarray, str]:
    """The build's objects, placed: meshes, components (also in other model files of the package, as Bambu Studio
    and PrusaSlicer save them) and their transforms, in mm."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("not a 3MF file (a 3MF is a zip archive)") from None
    root = "3D/3dmodel.model"
    try:
        for rel in ElementTree.fromstring(z.read("_rels/.rels")):
            if rel.get("Type", "").endswith("/3dmodel") and rel.get("Target"):
                root = rel.get("Target", "").lstrip("/")
    except (KeyError, ElementTree.ParseError):
        pass
    models: dict = {}

    def model(path: str):
        if path not in models:
            try:
                el = ElementTree.fromstring(z.read(path))
            except KeyError:
                raise ValueError(f"the 3MF file has no {path}") from None
            models[path] = (el, {o.get("id"): o for o in el.iter(f"{CORE}object")})
        return models[path]

    verts, faces, count = [], [], [0]

    def place(path: str, oid, m: np.ndarray, depth: int) -> None:
        if depth > 16:
            raise ValueError("the 3MF file's components go round in a circle")
        obj = model(path)[1].get(oid)
        if obj is None:
            raise ValueError(f"the 3MF file has no object {oid} in {path}")
        mesh = obj.find(f"{CORE}mesh")
        if mesh is not None:
            v = np.array([[float(p.get("x")), float(p.get("y")), float(p.get("z"))]
                          for p in mesh.iter(f"{CORE}vertex")], dtype=np.float64).reshape(-1, 3)
            t = np.array([[int(p.get("v1")), int(p.get("v2")), int(p.get("v3"))]
                          for p in mesh.iter(f"{CORE}triangle")], dtype=np.int64).reshape(-1, 3)
            if len(t) and (t.min() < 0 or t.max() >= len(v)):
                raise ValueError(f"a triangle of object {oid} points at a corner it does not have")
            verts.append(v @ m[:3] + m[3])
            faces.append(t + count[0])
            count[0] += len(v)
        for c in obj.iter(f"{CORE}component"):
            cm = _matrix(c.get("transform"))
            whole = np.vstack([cm[:3] @ m[:3], cm[3] @ m[:3] + m[3]])
            place((c.get(f"{PRODUCTION}path") or "").lstrip("/") or path, c.get("objectid"), whole, depth + 1)

    el = model(root)[0]
    for item in el.iter(f"{CORE}item"):
        place((item.get(f"{PRODUCTION}path") or "").lstrip("/") or root, item.get("objectid"),
              _matrix(item.get("transform")), 0)
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), "3MF"
    unit = UNITS_MM.get(el.get("unit") or "millimeter", 1.0)
    return np.vstack(verts) * unit, np.vstack(faces), "3MF"


def load(path) -> tuple[np.ndarray, np.ndarray]:
    """(vertices, faces) of an STL, 3MF, OBJ, PLY, GLB or OFF file (several objects joined)."""
    return _load(path)[:2]


def _load(path) -> tuple[np.ndarray, np.ndarray, str]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no file {p}")
    data = p.read_bytes()
    ext = p.suffix.lower()
    if ext == ".stl":
        return _read_stl(data)
    if ext == ".3mf":
        return _read_3mf(data)
    import trimesh
    try:
        mesh = trimesh.load(io.BytesIO(data), file_type=ext.lstrip("."), force="mesh", process=False)
    except Exception as exc:  # noqa: BLE001 - trimesh raises many kinds
        raise ValueError(f"trimesh could not read {p.name}: {exc.__class__.__name__}: {str(exc)[:200]}") from None
    if not hasattr(mesh, "faces"):
        raise ValueError(f"{p.name} has no triangles")
    return np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int64), ext.lstrip(".").upper()


def _stl_bytes(v: np.ndarray, f: np.ndarray) -> bytes:
    tri = v[f]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normal, axis=1)
    normal[length > 0] /= length[length > 0, None]
    rec = np.zeros(len(f), dtype=STL_RECORD)
    rec["normal"], rec["corners"] = normal, tri
    return b"hub3d binary STL, millimetres".ljust(80) + struct.pack("<I", len(f)) + rec.tobytes()


def _3mf_bytes(v: np.ndarray, f: np.ndarray) -> bytes:
    vw, fw = _welded(v, f)
    fw = fw[(fw[:, 0] != fw[:, 1]) & (fw[:, 1] != fw[:, 2]) & (fw[:, 2] != fw[:, 0])]   # 3MF: three corners
    vertex = '<vertex x="{:.9g}" y="{:.9g}" z="{:.9g}"/>'.format
    triangle = '<triangle v1="{}" v2="{}" v3="{}"/>'.format
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
             '<resources><object id="1" type="model"><mesh>\n<vertices>\n'
             + "\n".join(vertex(*p) for p in vw.tolist()) + "\n</vertices>\n<triangles>\n"
             + "\n".join(triangle(*t) for t in fw.tolist())
             + "\n</triangles>\n</mesh></object></resources>\n<build><item objectid=\"1\"/></build>\n</model>\n")
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
             '</Types>\n')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>\n')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("3D/3dmodel.model", model)
    return buf.getvalue()


# ---------------------------------------------------------------- what can be given

def _is_model(x) -> bool:
    t = type(x)
    return t.__module__.split(".")[0] in ("manifold3d", "trimesh") or isinstance(x, (str, os.PathLike))


def _arrays(obj) -> tuple[np.ndarray, np.ndarray, str, str]:
    """(vertices, faces, name, kind) of a manifold3d Manifold, a trimesh mesh or scene, (vertices, faces), a file or
    a list of these."""
    if isinstance(obj, (str, os.PathLike)):
        v, f, kind = _load(obj)
        return v, f, str(obj), kind
    t = type(obj)
    lib = t.__module__.split(".")[0]
    if lib == "manifold3d" and t.__name__ == "Manifold":
        status = str(obj.status()).split(".")[-1]
        if status != "NoError":
            raise NotPrintable(f"manifold3d could not make this solid ({status}): the triangles it was made from "
                               "are not closed - hub3d.solid(vertices, faces) says where")
        if obj.is_empty():
            raise NotPrintable("the solid is empty - no triangles. Parts that do not overlap (a ^ b), a cut that takes "
                               "all of it (a - b) or a size of 0 make an empty solid, without an error; print the "
                               "parts' bounding_box() to see where they are.")
        mesh = obj.to_mesh()
        return (np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3], np.asarray(mesh.tri_verts, dtype=np.int64),
                "the solid", "manifold3d")
    if lib == "manifold3d" and t.__name__ == "Mesh":
        return (np.asarray(obj.vert_properties, dtype=np.float64)[:, :3], np.asarray(obj.tri_verts, dtype=np.int64),
                "the mesh", "manifold3d Mesh")
    if lib == "manifold3d" and t.__name__ == "CrossSection":
        raise TypeError("a CrossSection is a flat outline, not a solid: make one with m3d.Manifold.extrude(outline, "
                        "height) or m3d.Manifold.revolve(outline)")
    if lib == "trimesh" and t.__name__ == "Scene":
        obj = obj.to_mesh()
    if hasattr(obj, "vertices") and hasattr(obj, "faces"):
        return (np.asarray(obj.vertices, dtype=np.float64), np.asarray(obj.faces, dtype=np.int64), "the mesh",
                "trimesh")
    if isinstance(obj, (list, tuple)) and obj:
        if len(obj) == 2 and not any(_is_model(x) for x in obj):
            return (*_given(obj[0], obj[1]), "the triangles", "vertices and faces")
        got = [_arrays(x) for x in obj]
        offsets = np.cumsum([0] + [len(g[0]) for g in got[:-1]])
        return (np.vstack([g[0] for g in got]), np.vstack([g[1] + o for g, o in zip(got, offsets)]),
                f"{len(got)} models", "joined")
    raise TypeError(f"hub3d cannot use a {t.__name__}: give it a manifold3d Manifold, a trimesh mesh, "
                    "(vertices, faces) or a file")


def _given(vertices, faces) -> tuple[np.ndarray, np.ndarray]:
    v = np.asarray(vertices, dtype=np.float64)
    f = np.asarray(faces)
    if v.ndim != 2 or v.shape[1] != 3:
        raise ValueError(f"vertices must be N x 3 (x, y, z), not {v.shape}")
    if f.ndim != 2 or f.shape[1] != 3 or (len(f) and not np.issubdtype(f.dtype, np.integer)):
        raise ValueError(f"faces must be M x 3 whole numbers (three vertex numbers a triangle), not {f.shape} "
                         f"{f.dtype}")
    f = f.astype(np.int64)
    if len(f) and (f.min() < 0 or f.max() >= len(v)):
        raise ValueError(f"faces point at vertex {int(f.max())}, but there are {len(v)} vertices (numbered from 0)")
    return v, f


# ---------------------------------------------------------------- the API

def check(model, bed=None) -> Report:
    """Is it a closed solid (every edge joins exactly two triangles, all facing out, volume above 0)? Its size,
    volume, parts and whether it fits `bed` (180, or (x, y, z) in mm). Takes what save() takes, or a file."""
    v, f, name, kind = _arrays(model)
    return _analyse(v, f, name, kind, bed)


def save(model, path, bed=None, strict: bool = True, quiet: bool = False) -> Report:
    """Write `model` (a manifold3d Manifold, a trimesh mesh, (vertices, faces) or a list of these) as an STL or a
    3MF in millimetres, read the file back and check it; prints what it found. strict (the default) writes nothing
    and raises NotPrintable when it is not a closed solid."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in (".stl", ".3mf"):
        raise ValueError(f"save as .stl or .3mf, not {p.name}")
    v, f, _, _ = _arrays(model)
    before = _analyse(v, f, str(p), "", bed)
    if strict and not before.ok:
        raise NotPrintable(str(before) + "\nNothing was written. (save(..., strict=False) writes it anyway.)")
    if p.parent != Path(""):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_stl_bytes(v, f) if ext == ".stl" else _3mf_bytes(v, f))
    report = check(p, bed)
    report.notes.append(f"{p.stat().st_size:,} bytes, read back from the file")
    if not quiet:
        print(report)
    return report


def solid(vertices, faces):
    """A manifold3d Manifold from triangles of your own (vertices N x 3, faces M x 3, each three vertex numbers).
    They must close: every edge in exactly two triangles. Facing inwards is turned round; anything else raises
    NotPrintable, saying what is open."""
    import manifold3d as m3d
    v, f = _given(vertices, faces)
    vw, fw = _welded(v, f)
    fw = fw[(fw[:, 0] != fw[:, 1]) & (fw[:, 1] != fw[:, 2]) & (fw[:, 2] != fw[:, 0])]
    report = _analyse(vw, fw, "these triangles")
    if not report.ok and report.watertight and not report.flipped_edges and report.volume < 0:
        fw = fw[:, ::-1]                                                   # all of it inside out: turned round
        report = _analyse(vw, fw, "these triangles")
    if not report.ok:
        raise NotPrintable("these triangles are not a closed solid:\n" + "\n".join(f"- {p}" for p in report.problems))
    m = m3d.Manifold(m3d.Mesh(vert_properties=np.ascontiguousarray(vw, dtype=np.float32),
                              tri_verts=np.ascontiguousarray(fw, dtype=np.uint32)))
    status = str(m.status()).split(".")[-1]
    if status != "NoError" or m.is_empty():
        raise NotPrintable(f"manifold3d did not take these triangles ({status})")
    return m


def views(model, path, size: int = 400, quiet: bool = False) -> str:
    """A PNG of `model` (or a file) from the front, the side and above at an angle, its size under it - to look at
    with view_image before saying how it turned out."""
    v, f, name, _ = _arrays(model)
    if not len(f):
        raise NotPrintable(f"{name} has no triangles to draw")
    size = max(100, min(1200, int(size)))
    p = Path(path)
    if p.suffix.lower() != ".png":
        raise ValueError(f"views are a PNG: {p.name}")
    if p.parent != Path(""):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_picture(v, f, size))
    ext = v[np.unique(f)].max(0) - v[np.unique(f)].min(0)
    if not quiet:
        print(f"Wrote {p}: {name} from the front, the side and above ({ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f} mm)"
              " - look at it with view_image.")
    return str(p)


# ---------------------------------------------------------------- the picture (as app/model3d/mesh.py)

BACK = (238, 238, 236)
CLAY = np.array([196, 160, 120], dtype=np.float64)
VIEWS = (("front", 0.0, 0.0), ("side", 90.0, 0.0), ("above", 35.0, 35.0))


def _rotation(yaw: float, pitch: float) -> np.ndarray:
    """Model (Z up) to view (x right, y up, z towards the eye): turned `yaw` about Z, tilted down by `pitch`."""
    y, p = math.radians(yaw), math.radians(pitch)
    look = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)   # front view: model -Y faces the eye
    rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    return rx @ look @ rz


def _view(v: np.ndarray, f: np.ndarray, rot: np.ndarray, size: int) -> Image.Image:
    pts = v @ rot.T
    lo, hi = pts[:, :2].min(0), pts[:, :2].max(0)
    span = float(max(hi - lo)) or 1.0
    scale = (size * 0.86) / span
    off = (size - (hi - lo) * scale) / 2
    xy = (pts[:, :2] - lo) * scale + off
    xy[:, 1] = size - 1 - xy[:, 1]
    z = pts[:, 2]
    tri = xy[f]                                            # (M, 3, 2)
    n = np.cross(pts[f[:, 1]] - pts[f[:, 0]], pts[f[:, 2]] - pts[f[:, 0]])
    norm = np.linalg.norm(n, axis=1)
    keep = norm > 0
    n[keep] /= norm[keep, None]
    light = np.array([0.35, 0.55, 0.76])
    shade = 0.28 + 0.72 * np.abs(n @ light)               # both sides lit: open shells still read
    colour = np.clip(CLAY[None, :] * shade[:, None], 0, 255)
    depth = np.full(size * size, -np.inf)
    img = np.empty((size * size, 3))
    img[:] = BACK
    _raster(tri, z[f], colour, size, depth, img)
    return Image.fromarray(img.reshape(size, size, 3).astype(np.uint8))


def _raster(tri: np.ndarray, zt: np.ndarray, col: np.ndarray, size: int, depth: np.ndarray,
            img: np.ndarray) -> None:
    """Every pixel whose centre lies in a triangle takes its colour where it is the nearest so far (a depth
    buffer). Triangles go in groups by the size of their box, each group tested on a grid of that size at once."""
    lo = np.floor(tri.min(1) - 0.5).astype(np.int64)
    hi = np.floor(tri.max(1) - 0.5).astype(np.int64)
    lo = np.clip(lo, 0, size - 1)
    hi = np.clip(hi, -1, size - 1)
    side = np.maximum(hi[:, 0] - lo[:, 0], hi[:, 1] - lo[:, 1]) + 1
    x0, y0 = tri[:, 0, 0], tri[:, 0, 1]
    x1, y1 = tri[:, 1, 0], tri[:, 1, 1]
    x2, y2 = tri[:, 2, 0], tri[:, 2, 1]
    den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    usable = (np.abs(den) > 1e-12) & (side > 0)
    prev = 0
    for bucket in (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048):
        sel = np.nonzero(usable & (side > prev) & (side <= bucket))[0]
        prev = bucket
        if not len(sel):
            continue
        step = max(1, 4_000_000 // (bucket * bucket))
        grid = np.arange(bucket)
        for c in range(0, len(sel), step):
            t = sel[c:c + step]
            gx = lo[t, 0][:, None, None] + grid[None, None, :]          # (T, 1, B)
            gy = lo[t, 1][:, None, None] + grid[None, :, None]          # (T, B, 1)
            px, py = gx + 0.5, gy + 0.5
            d = den[t][:, None, None]
            w0 = ((y1[t] - y2[t])[:, None, None] * (px - x2[t][:, None, None])
                  + (x2[t] - x1[t])[:, None, None] * (py - y2[t][:, None, None])) / d
            w1 = ((y2[t] - y0[t])[:, None, None] * (px - x2[t][:, None, None])
                  + (x0[t] - x2[t])[:, None, None] * (py - y2[t][:, None, None])) / d
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9) & (gx < size) & (gy < size)
            if not inside.any():
                continue
            ti, yi, xi = np.nonzero(inside)
            pix = (lo[t, 1][ti] + yi) * size + (lo[t, 0][ti] + xi)
            zz = (w0[ti, yi, xi] * zt[t[ti], 0] + w1[ti, yi, xi] * zt[t[ti], 1] + w2[ti, yi, xi] * zt[t[ti], 2])
            _nearest(pix, zz, col[t[ti]], depth, img)


def _nearest(pix: np.ndarray, d: np.ndarray, col: np.ndarray, depth: np.ndarray, img: np.ndarray) -> None:
    order = np.lexsort((-d, pix))                          # per pixel, the nearest first
    pix, d, col = pix[order], d[order], col[order]
    first = np.ones(len(pix), dtype=bool)
    first[1:] = pix[1:] != pix[:-1]
    pix, d, col = pix[first], d[first], col[first]
    nearer = d > depth[pix]
    depth[pix[nearer]] = d[nearer]
    img[pix[nearer]] = col[nearer]


def _picture(v: np.ndarray, f: np.ndarray, size: int = 400, max_faces: int = 400_000) -> bytes:
    """A PNG: the model from the front, from the side and from above at an angle, with its size under it."""
    v = np.asarray(v, dtype=np.float64)
    f = np.asarray(f, dtype=np.int64)
    if len(f) > max_faces:                                 # a picture needs no more (every n-th triangle)
        f = f[np.linspace(0, len(f) - 1, max_faces).astype(np.int64)]
    pad = 28
    sheet = Image.new("RGB", (size * len(VIEWS), size + pad), BACK)
    draw = ImageDraw.Draw(sheet)
    for i, (name, yaw, pitch) in enumerate(VIEWS):
        sheet.paste(_view(v, f, _rotation(yaw, pitch), size), (i * size, 0))
        draw.text((i * size + 10, size + 8), name, fill=(90, 90, 90))
    ext = v.max(0) - v.min(0)
    draw.text((size * len(VIEWS) - 220, size + 8), f"{ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f} mm",
              fill=(90, 90, 90))
    buf = io.BytesIO()
    sheet.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- the recipe

def _indent(code: str) -> str:
    return "\n".join(("    " + line) if line else "" for line in code.splitlines()) + "\n"


EXAMPLE = '''\
import manifold3d as m3d, numpy as np, hub3d
M, C = m3d.Manifold, m3d.CrossSection
m3d.set_min_circular_angle(3); m3d.set_min_circular_edge_length(0.5)   # round enough to print

# A pen cup: 12 ribs round a circle, turned 60 degrees on the way up, 2 mm wall, 3 mm floor, wider in the middle
H = 100
outline = C.circle(30)
for i in range(12):
    outline = outline + C.circle(5).translate((30, 0)).rotate(i * 30)
outer = M.extrude(outline, H, n_divisions=40, twist_degrees=60)
inner = M.extrude(outline.offset(-2), H, n_divisions=40, twist_degrees=60).trim_by_plane((0, 0, 1), 3)
cup = outer - inner

def wider(p):                       # p: every corner, N x 3 - return them moved
    s = 1 + 0.15 * np.sin(np.pi * p[:, 2] / H)
    p[:, 0] *= s
    p[:, 1] *= s
    return p

cup = cup.warp_batch(wider)
hub3d.save(cup, "out/pen-cup.stl", bed=180)       # prints what it checked
hub3d.views(cup, "out/pen-cup-views.png")         # then: view_image out/pen-cup-views.png
'''

LETTERS = '''\
import manifold3d as m3d
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties
M, C = m3d.Manifold, m3d.CrossSection

path = TextPath((0, 0), "Hub 8", size=12, prop=FontProperties(family="DejaVu Sans", weight="bold"))
letters = C([p for p in path.to_polygons() if len(p) > 2], m3d.FillRule.EvenOdd)   # holes in b and 8 stay holes
x0, y0, x1, y1 = letters.bounds()
sign = M.cube((x1 - x0 + 8, y1 - y0 + 8, 2)) + M.extrude(letters, 1).translate((4 - x0, 4 - y0, 2))
'''

RECIPE = f"""\
manifold3d 3.5: solids that are always closed. Millimetres, Z up, angles in degrees. Every call returns a new object.

2D outlines - m3d.CrossSection (C):
  C.square((w, d), center=False)   C.circle(r)   C.hull_points(points)
  C([outer, hole, ...], m3d.FillRule.EvenOdd)   your own outlines: lists or N x 2 arrays of (x, y); with EvenOdd
      an outline inside another is a hole, whichever way round it runs (the default rule drops such holes)
  a + b  joined   a - b  cut   a ^ b  only where both are
  .offset(d)  grown (d > 0) or shrunk (d < 0) all round   .translate((x, y))  .rotate(degrees)  .scale((sx, sy))
  .area()  .bounds() -> (xmin, ymin, xmax, ymax)

Solids - m3d.Manifold (M):
  M.cube((w, d, h), center=False)          always three sizes
  M.cylinder(h, radius_low, radius_high=-1, center=False)   radius_high -1: as radius_low; 0: a cone
  M.sphere(r)
  M.extrude(outline, h, n_divisions=0, twist_degrees=0, scale_top=(1, 1))   up Z from 0; n_divisions: layers
  M.revolve(outline, revolve_degrees=360)   the outline's x is the radius, its y the height: turned about Z
  M.batch_hull([a, b, ...])   M.hull_points(points)   .hull()   the skin stretched round them
Placing:  .translate((x, y, z))  .rotate((rx, ry, rz))  .scale((sx, sy, sz))  .mirror((nx, ny, nz))
  .transform(m)   m: 3 rows x 4 columns (not 4 x 4)
Joining and cutting:  a + b   a - b   a ^ b   M.batch_boolean([a, b, c], m3d.OpType.Add)  (or Subtract, Intersect)
  .trim_by_plane((0, 0, 1), z)   keeps what is above z     .split_by_plane(normal, offset) -> (that side, the other)
  .decompose() -> [the separate parts]    .slice(z) -> the outline at height z    M.compose([a, b]) without joining
Bending any shape:  .refine(n) cuts each edge in n first, then .warp_batch(f): f gets the corners (N x 3 numpy
  array) and returns them moved - for profiles that swell or narrow with height, bends, waves
Asking:  .volume()  .bounding_box() -> (xmin, ymin, zmin, xmax, ymax, zmax)  .num_tri()  .is_empty()  .genus()
  An empty result raises no error (parts that do not overlap, a cut that takes all): hub3d.save says so.
Round enough: m3d.set_min_circular_angle(3); m3d.set_min_circular_edge_length(0.5) before making circles - the
  default gives a 60 mm circle 36 flat sides.
Not in manifold3d - do not guess: Cylinder, union(), intersect(), to_stl(), from_stl(), a module-level
  batch_boolean. Save with hub3d.save; read a file with hub3d.load(path) -> (vertices, faces), and make your own
  triangles a solid with hub3d.solid(vertices, faces).

For printing (FDM, 0.4 mm nozzle): walls 1.2 mm or more; overhangs up to about 45 degrees need no supports; a flat
face on the bed; 0.2-0.3 mm of play for parts that fit together; nothing finer than about 0.4 mm.

Example - a pen cup (runs as it stands):

{_indent(EXAMPLE)}
Example - raised letters on a plate (matplotlib draws the letters):

{_indent(LETTERS)}
Before you say it is done: hub3d.save printed READY (or says what is wrong - fix the model, not the check), and you
have looked at hub3d.views' picture with view_image and compared it with what was asked. Tell the user its size.
In bash:  hub3d check FILE [--bed 180]    hub3d views FILE OUT.png
"""


# ---------------------------------------------------------------- self-test (when the sandbox image is built)

def selftest() -> int:
    import manifold3d as m3d
    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        if not ok:
            failures.append(what)

    here = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ.setdefault("MPLCONFIGDIR", os.path.join(tmp, "mpl"))
        os.chdir(tmp)
        try:
            out = io.StringIO()
            old, sys.stdout = sys.stdout, out
            try:
                exec(compile(EXAMPLE, "<recipe: pen cup>", "exec"), {"__name__": "recipe"})
                letters: dict = {}
                exec(compile(LETTERS, "<recipe: letters>", "exec"), letters)
            finally:
                sys.stdout = old
            cup = check("out/pen-cup.stl", bed=180)
            expect(cup.ok and cup.parts == 1 and not cup.warnings, f"the pen cup is not ready:\n{cup}")
            expect("READY" in out.getvalue(), "save() did not print READY for the pen cup")
            expect(Path("out/pen-cup-views.png").stat().st_size > 10_000, "the pen cup's picture is missing")
            sign = letters["sign"]
            expect(str(sign.status()).endswith("NoError") and len(sign.decompose()) == 1,
                   "the letters are not one solid with their plate")
            cube = m3d.Manifold.cube((10, 10, 10))
            for name in ("cube.stl", "cube.3mf"):
                r = save(cube, name, quiet=True)
                expect(r.ok and abs(r.volume - 1000) < 1e-3 and r.triangles == 12, f"{name} read back wrong:\n{r}")
            try:
                save(cube ^ cube.translate((20, 0, 0)), "empty.stl", quiet=True)
                failures.append("an empty solid was saved")
            except NotPrintable:
                pass
            mesh = cube.to_mesh()
            inside_out = np.asarray(mesh.tri_verts)[:, ::-1]
            again = solid(np.asarray(mesh.vert_properties)[:, :3], inside_out)
            expect(abs(again.volume() - 1000) < 1e-3, "solid() did not turn an inside-out cube round")
            try:
                solid(np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts)[1:])
                failures.append("solid() took a cube with a triangle missing")
            except NotPrintable:
                pass
        finally:
            os.chdir(here)
    import trimesh
    versions = f"manifold3d {_version('manifold3d')}, trimesh {trimesh.__version__}, numpy {np.__version__}"
    if failures:
        print("hub3d selftest FAILED (" + versions + "):\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print(f"hub3d selftest: ok ({versions})")
    return 0


def _version(dist: str) -> str:
    from importlib import metadata
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "?"


# ---------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hub3d", description="3D models to print: check and draw STL/3MF files, and "
                                 "manifold3d's API with examples (hub3d recipe).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("recipe", help="manifold3d's API and worked examples - read it before writing the code")
    c = sub.add_parser("check", help="is the file a closed solid; its size, volume and parts")
    c.add_argument("file")
    c.add_argument("--bed", help="the printer's bed in mm: 180 or 180x180x180")
    v = sub.add_parser("views", help="a picture of the file from the front, the side and above")
    v.add_argument("file")
    v.add_argument("png")
    v.add_argument("--size", type=int, default=400, help="pixels a view (default 400)")
    sub.add_parser("selftest", help="run the recipe's examples and check the results")
    args = ap.parse_args(argv)
    if args.cmd == "recipe":
        print(RECIPE, end="")
        return 0
    if args.cmd == "selftest":
        return selftest()
    try:
        if args.cmd == "check":
            report = check(args.file, args.bed)
            print(report)
            return 0 if report.ok else 1
        views(args.file, args.png, args.size)
        return 0
    except (OSError, ValueError, TypeError) as exc:              # NotPrintable is a ValueError
        print(f"hub3d {args.cmd}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
