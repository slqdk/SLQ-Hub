"""hub3d: 3D models to print, in the sandbox (0.67.1, after chat 255).

Build the solid with manifold3d - what it makes is always closed - then let hub3d save, check and draw it:

    import hub3d
    hub3d.save(solid, "out/part.stl", bed=180)    # STL or 3MF; prints what it checked, refuses what is not a solid
    hub3d.views(solid, "out/part-views.png")      # from the front, the side and above: look at it (view_image)

In bash:

    hub3d recipe                    manifold3d's API (3.5) and worked examples - read it before writing the code
    hub3d check FILE [--bed 180]    is FILE a closed solid; its size, volume and parts; does it fit the bed
    hub3d views FILE OUT.png        a picture of FILE from three sides
    hub3d format                    the format of the Hub's 3D editor's designs (.3d.json), with an example
    hub3d design FILE.3d.json       a design from the 3D editor: what it makes, checked (--views, --stl, --3mf)

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

__all__ = ["NotPrintable", "Report", "check", "design", "load", "read_design", "save", "solid", "views", "RECIPE"]

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
        self.contact = self.footprint = 0.0   # mm2: its slice 0.1 mm above its lowest point, its shadow from above
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


CONTACT_SHARE = 0.4      # below this share of its shadow on the plate, it stands on something round or pointed


def _rests(vw: np.ndarray, fw: np.ndarray, r: Report) -> None:
    """What a closed solid stands on (0.88.0, chat 328: a snowflake of round rods lying down, touching the plate in
    lines): the area of its slice 0.1 mm above its lowest point - about the first layer - against its shadow."""
    try:
        import manifold3d as m3d
    except ImportError:
        return
    g = fw[(fw[:, 0] != fw[:, 1]) & (fw[:, 1] != fw[:, 2]) & (fw[:, 2] != fw[:, 0])]
    m = m3d.Manifold(m3d.Mesh(vert_properties=np.ascontiguousarray(vw, dtype=np.float32),
                              tri_verts=np.ascontiguousarray(g, dtype=np.uint32)))
    if not str(m.status()).endswith("NoError") or m.is_empty():
        return
    r.contact = float(m.slice(float(r.lowest[2]) + 0.1).area())
    r.footprint = float(m.project().area())
    if r.footprint > 1 and r.contact < CONTACT_SHARE * r.footprint:
        r.warnings.append(f"it stands on {r.contact:,.0f} mm2 of its {r.footprint:,.0f} mm2 shadow "
                          f"({100 * r.contact / r.footprint:.0f}%): round or pointed underneath - a cylinder or "
                          "sphere lying down, a tip - so it may come loose and print rough below. A flat bottom "
                          "holds: stand it on a flat face, cut it flat (trim_by_plane), make flat things of outlines "
                          "and boxes; for an organic shape, a brim")


def _analyse(v: np.ndarray, f: np.ndarray, name: str, kind: str = "", bed=None, rests: bool = True) -> Report:
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
    if r.ok and rests:
        _rests(vw, fw, r)
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
    before = _analyse(v, f, str(p), "", bed, rests=False)          # the file's own check below says it
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
    report = _analyse(vw, fw, "these triangles", rests=False)
    if not report.ok and report.watertight and not report.flipped_edges and report.volume < 0:
        fw = fw[:, ::-1]                                                   # all of it inside out: turned round
        report = _analyse(vw, fw, "these triangles", rests=False)
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
        sides = "straight down, from above and from the front" if _flat(ext) else "from the front, the side and above"
        print(f"Wrote {p}: {name} {sides} ({ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f} mm) - look at it with "
              "view_image.")
    return str(p)


# ---------------------------------------------------------------- the picture (as app/model3d/mesh.py)

BACK = (238, 238, 236)
CLAY = np.array([196, 160, 120], dtype=np.float64)
VIEWS = (("front", 0.0, 0.0), ("side", 90.0, 0.0), ("above", 35.0, 35.0))
# Something flat - a plate, a sign, a coaster: from the front and the side it is a line, so straight down first, its
# heights shaded and its edges drawn - letters on a plate read there (0.89.0, chat 330).
FLAT_VIEWS = (("top", 0.0, 90.0), ("above", 35.0, 35.0), ("front", 0.0, 0.0))
FLAT = 0.25              # flat: no taller than this share of its width or depth


def _rotation(yaw: float, pitch: float) -> np.ndarray:
    """Model (Z up) to view (x right, y up, z towards the eye): turned `yaw` about Z, tilted down by `pitch`."""
    y, p = math.radians(yaw), math.radians(pitch)
    look = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)   # front view: model -Y faces the eye
    rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    return rx @ look @ rz


def _view(v: np.ndarray, f: np.ndarray, rot: np.ndarray, size: int, relief: bool = False) -> Image.Image:
    """The model drawn as seen through `rot`. relief: higher faces lighter, and a dark line where the depth jumps
    (an edge) - for the view straight down, where the faces on top all face the light alike."""
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
    if relief:
        zf = z[f].mean(1)
        lo_z, hi_z = float(z.min()), float(z.max())
        shade = shade * (0.62 + 0.38 * (zf - lo_z) / ((hi_z - lo_z) or 1.0))
    colour = np.clip(CLAY[None, :] * shade[:, None], 0, 255)
    depth = np.full(size * size, -np.inf)
    img = np.empty((size * size, 3))
    img[:] = BACK
    _raster(tri, z[f], colour, size, depth, img)
    if relief:
        d = depth.reshape(size, size)
        jump = max(0.2, 2 / scale)          # mm: a step of more than two pixels' slope is an edge
        with np.errstate(invalid="ignore"):              # beside the background: -inf - -inf
            down = ~(np.abs(d[1:, :] - d[:-1, :]) <= jump)
            across = ~(np.abs(d[:, 1:] - d[:, :-1]) <= jump)
        edge = np.zeros((size, size), dtype=bool)
        edge[1:, :] |= down
        edge[:-1, :] |= down
        edge[:, 1:] |= across
        edge[:, :-1] |= across
        img = img.reshape(size, size, 3)
        img[edge & np.isfinite(d)] *= 0.45
        img = img.reshape(size * size, 3)
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


def _flat(ext) -> bool:
    return float(ext[2]) <= FLAT * max(float(ext[0]), float(ext[1]))


def _picture(v: np.ndarray, f: np.ndarray, size: int = 400, max_faces: int = 400_000) -> bytes:
    """A PNG: the model from the front, from the side and from above at an angle, with its size under it - something
    flat straight down, at an angle and from the front."""
    v = np.asarray(v, dtype=np.float64)
    f = np.asarray(f, dtype=np.int64)
    if len(f) > max_faces:                                 # a picture needs no more (every n-th triangle)
        f = f[np.linspace(0, len(f) - 1, max_faces).astype(np.int64)]
    pad = 28
    used = v[np.unique(f)]
    ext = used.max(0) - used.min(0)
    shown = FLAT_VIEWS if _flat(ext) else VIEWS
    sheet = Image.new("RGB", (size * len(shown), size + pad), BACK)
    draw = ImageDraw.Draw(sheet)
    for i, (name, yaw, pitch) in enumerate(shown):
        sheet.paste(_view(v, f, _rotation(yaw, pitch), size, relief=name == "top"), (i * size, 0))
        draw.text((i * size + 10, size + 8), name, fill=(90, 90, 90))
    draw.text((size * len(shown) - 220, size + 8), f"{ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f} mm",
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


# ---------------------------------------------------------------- designs from the Hub's 3D editor (0.87.0)

# A design (a .3d.json file) is a list of parts in mm, Z up, the plate at z = 0: each a shape as big as `size` (its
# box before turning), mirrored in its own X when `mirror`, turned by `rotate` (degrees, X then Y then Z - as
# OpenSCAD) and centred on `at`; holes are cut out of the solids beside them, in their group or the design. The
# editor builds it with manifold-3d in the browser (frontend/src/model3d/geometry.ts); this builds it the same way,
# step for step, so what the assistant checks is what the editor shows and exports. tests/test_design3d.py runs
# both on the same designs.
DESIGN_FORMAT = "slq-hub-3d"
DESIGN_SHAPES = ("box", "cylinder", "cone", "sphere", "wedge", "text", "outline", "mesh", "group")
PART_KEYS = ("id", "name", "shape", "size", "at", "rotate", "mirror", "hole", "hidden", "color", "sides", "radius",
             "top", "text", "font", "bold", "italic", "align", "letter_spacing", "line_spacing", "points", "holes",
             "file", "repeat", "fillet", "chamfer", "on", "mates", "parts")
MAX_COPIES = 100
ROUND_SHAPES = ("cylinder", "cone", "sphere")
DEFAULT_SIDES = 64
MIN_SIZE = 0.01
FONT_DIR = Path(__file__).with_name("fonts")        # the fonts as polygons (tools/make_3d_font.py): index.json, ...
_font_cache: dict = {}
# A text's fonts (0.89.0; ten more 0.99.0) as fonts/index.json names them, the first the default; other names a model
# may write for them, and the words in a name that ask for bold or italic - the editor's (geometry.ts), word for word.
FONT_FAMILIES = ("sans", "condensed", "geometric", "rounded", "serif", "classic", "script", "handwriting", "marker",
                 "stencil", "comic", "western", "blackletter", "futuristic", "pixel", "mono")
FONT_ALIASES = {
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
}
BOLD_WORDS = ("bold", "extrabold", "black", "heavy")
ITALIC_WORDS = ("italic", "oblique")
PLAIN_WORDS = ("regular", "normal", "semibold", "medium", "book", "style")
SLANT = 0.15838444032453627          # an italic in a font that has none: its letters lean tan 9 degrees
SPACING = (0.5, 4.0)                 # letter_spacing and line_spacing: shares of the font's own

DESIGN_EXAMPLE = """\
{
  "format": "slq-hub-3d",
  "version": 1,
  "printer": "bambu-p2s",
  "bed": [256, 256, 256],
  "parts": [
    {"id": "tag", "name": "Tag", "shape": "box", "size": [50, 22, 3], "at": [0, 0, 1.5], "fillet": {"vertical": 4},
     "chamfer": {"top": 0.6}, "color": "#2b7bd9"},
    {"id": "ring", "name": "Key ring hole", "shape": "cylinder", "size": [5, 5, 3.4], "at": [-19, 0, 1.5],
     "on": {"part": "tag", "face": "top", "from": {"left": 6}, "through": true}, "hole": true},
    {"id": "word", "name": "Name", "shape": "text", "text": "SLQ", "size": [0, 8, 1], "at": [-1, 0, 3.5],
     "mates": [{"type": "coincident", "face": "bottom", "to": {"part": "tag", "face": "top"}}], "color": "#d9822b"},
    {"id": "clip", "name": "Clip", "shape": "group", "parts": [
      {"id": "arm", "shape": "wedge", "size": [12, 6, 6], "at": [19, 0, 6], "rotate": [0, 0, 180]},
      {"id": "slot", "name": "Slot in the clip only", "shape": "box", "size": [8, 2, 8], "at": [19, 0, 7],
       "hole": true}
    ]}
  ]
}
"""

DESIGN_SNOWFLAKE = """\
{
  "format": "slq-hub-3d",
  "version": 1,
  "printer": "bambu-p2s",
  "bed": [256, 256, 256],
  "parts": [
    {"id": "core", "name": "Centre", "shape": "cylinder", "size": [26, 26, 3], "at": [0, 0, 1.5], "sides": 6},
    {"id": "window", "name": "Centre hole", "shape": "cylinder", "size": [10, 10, 5], "at": [0, 0, 1.5],
     "sides": 6, "rotate": [0, 0, 30], "hole": true},
    {"id": "arm", "name": "Arm", "shape": "group", "repeat": {"count": 6, "around": [0, 0]}, "parts": [
      {"id": "spine", "shape": "outline", "size": [0, 0, 3], "at": [0, 0, 1.5],
       "points": [[0, -2], [52, -2], [60, 0], [52, 2], [0, 2]]},
      {"id": "twig1", "shape": "box", "size": [16, 3, 3], "at": [26, 6.928, 1.5], "rotate": [0, 0, 60]},
      {"id": "twig2", "shape": "box", "size": [16, 3, 3], "at": [26, -6.928, 1.5], "rotate": [0, 0, -60]},
      {"id": "twig3", "shape": "box", "size": [12, 2.5, 3], "at": [41, 5.196, 1.5], "rotate": [0, 0, 60]},
      {"id": "twig4", "shape": "box", "size": [12, 2.5, 3], "at": [41, -5.196, 1.5], "rotate": [0, 0, -60]},
      {"id": "gem", "shape": "cylinder", "size": [10, 7, 3], "at": [50, 0, 1.5], "sides": 4},
      {"id": "eye", "name": "Hanging hole", "shape": "cylinder", "size": [3, 3, 5], "at": [50, 0, 1.5], "hole": true}
    ]}
  ]
}
"""

# 0.93.1: Gemma 4 31B's Christmas tree for a person - three cones that ran into one smooth cone, a star lying flat on
# its point, the lowest tier's flat underside over a thin trunk. Søren: "Er det her det bedste den kan lave?"
DESIGN_TREE = """\
{
  "format": "slq-hub-3d",
  "version": 1,
  "printer": "bambu-p2s",
  "bed": [256, 256, 256],
  "parts": [
    {"id": "foot", "name": "Foot", "shape": "cylinder", "size": [104, 104, 4], "at": [0, 0, 2],
     "chamfer": {"bottom": 0.6}, "fillet": {"top": 1.5}, "color": "#6d4c41"},
    {"id": "t1", "name": "Tier 1", "shape": "cone", "size": [100, 100, 54], "top": 0.3, "at": [0, 0, 30],
     "color": "#2e7d32"},
    {"id": "t2", "name": "Tier 2", "shape": "cone", "size": [84, 84, 48], "top": 0.3, "at": [0, 0, 66],
     "chamfer": {"bottom": 17}, "color": "#2e7d32"},
    {"id": "t3", "name": "Tier 3", "shape": "cone", "size": [66, 66, 44], "top": 0.28, "at": [0, 0, 100],
     "chamfer": {"bottom": 13}, "color": "#2e7d32"},
    {"id": "t4", "name": "Tier 4", "shape": "cone", "size": [48, 48, 40], "at": [0, 0, 132],
     "chamfer": {"bottom": 9}, "color": "#2e7d32"},
    {"id": "stem", "shape": "cylinder", "size": [6, 6, 14], "at": [0, 0, 155], "color": "#f9a825"},
    {"id": "star", "name": "Star", "shape": "outline", "size": [0, 0, 5], "at": [0, 0, 165], "rotate": [90, 0, 0],
     "points": [[0, 15], [-3.76, 5.18], [-14.27, 4.64], [-6.09, -1.98], [-8.82, -12.14], [0, -6.4],
                [8.82, -12.14], [6.09, -1.98], [14.27, 4.64], [3.76, 5.18]], "color": "#f9a825"},
    {"id": "b1", "name": "Baubles", "shape": "sphere", "size": [7, 7, 7], "at": [36.2, 0, 22],
     "repeat": {"count": 6, "around": [0, 0]}, "color": "#e53935"},
    {"id": "b2", "name": "Baubles", "shape": "sphere", "size": [7, 7, 7], "at": [22.9, 16.6, 62],
     "repeat": {"count": 5, "around": [0, 0]}, "color": "#fbc02d"},
    {"id": "b3", "name": "Baubles", "shape": "sphere", "size": [7, 7, 7], "at": [21, 7.6, 95],
     "repeat": {"count": 4, "around": [0, 0]}, "color": "#1e88e5"}
  ]
}
"""

DESIGN_GUIDE = f"""\
A design from the Hub's 3D editor: NAME.3d.json in the working folder. The editor beside the chat shows it and saves
the user's changes into it; you change it by editing the file - edit_file for a few values, write_file for a new
design - and the editor shows what you write within seconds. Keep each part's id: the user's messages name parts by
it ([3D point: part "Tag" (id tag) at x 3, y -11, z 1.5 mm, on a face facing -Y (front)] is a spot they pointed at).

    hub3d design NAME.3d.json --views out/NAME-views.png    what it makes, checked; then look: view_image
    hub3d design NAME.3d.json --3mf out/NAME.3mf             the file for the slicer (Bambu Studio); --stl too

Millimetres. X to the right, Y to the back, Z up; x 0, y 0 is the middle of the plate and z 0 its top.

The file: {{"format": "slq-hub-3d", "version": 1, "printer": ..., "bed": [x, y, z], "parts": [...]}} - printer and
bed are optional (the editor's printer list: bambu-p2s, bambu-p1s, bambu-x1, bambu-a1, bambu-a1-mini, bambu-h2s,
bambu-h2d, or "custom" with its bed).

A part:
  id        stays the part's for its life; a new part gets a new one (p1, p2 ... or a word)
  name      optional, shown in the editor
  shape     box | cylinder | cone | sphere | wedge | text | outline | mesh | group
  size      [x, y, z]: the part's own box before it is turned (cylinder, cone, sphere: the diameters and the height)
  at        [x, y, z]: where the middle of that box is (an outline: its points' 0,0) - a part standing on the
            plate has z = its height / 2
  rotate    [x, y, z] degrees about its middle: X first, then Y, then Z (as OpenSCAD's rotate); left out: [0, 0, 0]
  hole      true: cuts the solids beside it instead of adding to them
  hidden    true: left out of everything
  mirror    true: mirrored in its own X before it is turned
  color     "#rrggbb" - for the eye only: it prints in one filament
  repeat    copies of the part (or group), joined to it: {{"count": 6}} turns copies about the up axis through the
            part's at, evenly all round - "around": [x, y] another point (a group's default: 0, 0), "angle": 90
            spreads them over 90 degrees; {{"count": 4, "step": [25, 0, 0]}} puts them in a row. A hole's copies are
            holes too
  fillet    its own edges rounded, {{edge: mm}}: {{"top": 2}} rounds the edges round its top, {{"vertical": 3}} the
            upright ones, {{"all": 1}} every one, {{"top-front": 2}} the one between two faces. A box's faces: top,
            bottom, front, back, left, right; a wedge's: bottom, left, front, back, slope; a cylinder's or a cone's
            edges: top, bottom; an outline's: vertical (its corners). The name that means an edge most exactly wins.
            On a hole they shape what it cuts: a box's pocket gets round inside corners and floor edges from the
            hole's {{"vertical": 5, "bottom": 2}} - never its edges at the opening, which would leave a lip round it
  chamfer   the same names, the edges cut off instead (the size along each face): {{"bottom": 0.6}} - a bottom edge
            that does not bulge onto the plate
  on        kept on a face of a box, as a circle sketched on a face and dimensioned from two of its edges:
            {{"part": "tag", "face": "top", "from": {{"left": 6, "front": 4}}, "through": true}} is 6 mm from the
            box's left edge and 4 mm from its front one, through all of the box (a hole). face: the box's own top,
            bottom, front (its -Y side before it is turned), back, left or right; from: mm from an edge across the
            face and one up it, each named by the face beyond it - one left out: the middle; depth: how far a hole
            goes in or a solid stands out (left out: its size z); angle: degrees it is turned on the face. Its at,
            rotate and size z then follow from the box: it moves, turns and grows with it. To move it, change from;
            to place it yourself, leave on out
  mates     placed against other parts, as in SolidWorks' assemblies - a list, each one of: {{"type": "coincident",
            "face": "bottom", "to": {{"part": "tag", "face": "top"}}}} puts its bottom on the tag's top, the two
            faces against each other ("flip": true: flush instead, both looking the same way - two tops level);
            "distance" with "mm": 5 the same 5 mm apart (less than 0: into it); {{"type": "concentric", "to":
            {{"part": "hole"}}}} puts its axis - its own Z through its middle: a cylinder's, a round hole's - on the
            other's ("flip": pointing the other way). face: its own box's top, bottom, front, back, left or right
            before it is turned, whatever its shape (not a text's side whose size follows its letters). When the
            design is read it is turned and moved as little as its mates need, each in turn; what they leave free -
            sliding along a face, turning about an axis - stays as its at and rotate put it. So moving the part it
            is mated to does not take it along in the file (the editor does): move it too. Not with on

  box       radius: every edge and corner rounded (mm), sides: how round (default 64) - or fillet some edges
  cylinder  along Z; sides (default 64) - sides 6 makes a hexagon (a nut trap), 3 a triangle
  cone      top: the top's diameter as a share of the bottom's (0: a point, 1: a cylinder)
  sphere    sides; different sizes make an ellipsoid
  wedge     a ramp: its full height at its -X end, sloping down to nothing at +X
  text      text: the letters (æøå, digits, punctuation; \\n starts a new line), lying flat and reading along X.
            size [width, height, thickness] is the box all its lines fill, not one letter's height: two lines 40
            high have capitals about 13 mm tall (hub3d design says how tall they came out). A width or height of 0
            follows the letters: [70, 0, 2] is 70 mm wide - text on a plate fills most of its width. font: sans
            (Barlow, the default), condensed (Oswald: narrow - more letters in a width), geometric (Montserrat),
            rounded (Nunito), serif (Bitter), classic (Cinzel: Roman capitals, for a plaque), script (Pacifico),
            handwriting (Caveat), marker (Knewave), stencil (Saira Stencil: cut through a plate, its letters keep
            their middles), comic (Bangers), western (Holtwood One SC: a wanted poster's), blackletter (Grenze
            Gotisch: fraktur), futuristic (Oxanium), pixel (Pixelify Sans) or mono (JetBrains Mono); bold: true
            (none in script, marker, stencil, comic and western), italic: true (an italic in sans, geometric,
            rounded, serif and mono - the others lean); align: "left", "center" (the default)
            or "right" - the lines; letter_spacing, line_spacing: 1 is the font's own (1.3: wider apart).
            Standing up: rotate [90, 0, 0]
  outline   points: [[x, y], ...] - a flat shape of your own, drawn in mm around the part's 0,0 and size z thick, as
            OpenSCAD's translate(at) rotate(rotate) linear_extrude(size z, center=true) polygon(points). So here at
            is where the points' 0,0 goes, not the middle (left out: [0, 0, thickness / 2] - the points where they
            are drawn, on the plate). size [0, 0, 3]: as drawn, 3 mm thick; a size x or y scales the points to that
            width. holes: [[[x, y], ...], ...] cut out of it
  mesh      file: an STL, 3MF or OBJ, its path from the design's folder, centred on at. size left out: the file's own
            size; a 0 follows the others - [0, 0, 150] is 150 mm tall, its proportions kept; three sizes fill that box
            (stretched if they are not its proportions - hub3d design says each mesh's size). One that is not a
            closed solid is shown and exported as it is, but nothing joins it or cuts it
  group     parts: [...]; no size, at, rotate or color of its own

How it is built: in each list - the design's parts, or a group's - the solids are joined and then that list's holes
cut them; a group is built that way first and then counts as one solid (or one hole, with hole: true). So a hole at
the top cuts everything it meets; to keep a hole to some parts, put it in a group with them. Let parts that should
be one piece overlap a little (0.2 mm or more) - only touching at a face, they may print as two; a face mated
coincident on another is joined to it.

Flat things - ornaments, signs, snowflakes, stars, coasters - are outlines and boxes lying flat on the plate, a few
mm thick. Not cylinders lying down: they touch the plate in a line, come loose and print rough below (hub3d design
warns). Something that repeats round a centre - a snowflake's arms, a star's points, a gear's teeth, spokes, a ring
of holes - is one of it, repeated.

Things that stand - a tree, a figure, a vase, a tower - are built up from a wide, flat foot on the plate, and what
makes them look like themselves is made big: a Christmas tree is tiers that step out, each wider at its foot than
the tier below is there - not one smooth cone. A part that sticks out over the one below prints without supports
when its underside slopes 45 degrees or more: chamfer its bottom edge by about how far it sticks out ("chamfer":
{{"bottom": 17}} on a tier); a flat underside in the air needs supports. A star, a heart or a sign on top stands up -
an outline or a text turned rotate [90, 0, 0] - and overlaps what holds it by a few mm.

An example - a key tag with a hole, a name on top and a clip with its own slot:

{DESIGN_EXAMPLE}
Another - a snowflake, 120 mm: one arm (an outline, twigs and a gem with a hole) repeated six times round a centre:

{DESIGN_SNOWFLAKE}
And a Christmas tree, 18 cm: tiers stepping out with sloped undersides, a star standing on a stem, baubles repeated
round it:

{DESIGN_TREE}"""


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _vec(v, fallback) -> list[float]:
    if not isinstance(v, list) or len(v) != 3 or not all(_num(x) for x in v):
        return [float(x) for x in fallback]
    return [float(x) for x in v]


def read_design(text: str, folder=None, *, own=None) -> tuple[dict, list[str]]:
    """A design from its file's text, every part complete - the editor's readDesign: missing or wrong values get
    the same defaults, said in the list it returns. With `folder` (the design file's) or `own` (file -> its own size,
    _own_sizes), a mesh part's 0s in size - all three when it has none - are worked out from its file (0.104.0), as
    the editor does once the file is loaded; without, they stay 0 and the build works them out."""
    import json
    if own is None and folder is not None:
        own = _own_sizes(folder)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"the design is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("the design is not a JSON object")
    problems: list[str] = []
    seen: set[str] = set()
    parts = data.get("parts") if isinstance(data.get("parts"), list) else []
    if not isinstance(data.get("parts"), list):
        problems.append('it has no "parts" list')
    odd = {"parts": 0, "keys": set(), "shapes": []}
    design = {"parts": _settle(_read_parts(parts, seen, problems, odd, own), problems)}   # parts on faces placed (0.98.0)
    first = []                    # a model that guessed the format: what it did not know first, once - not per part
    if odd["parts"]:
        n = odd["parts"]
        first.append(f"{n} part{'s have' if n > 1 else ' has'} keys the format does not use "
                     f"({', '.join(sorted(odd['keys']))}) - run hub3d format: shape, size, at, rotate ...")
    if len(odd["shapes"]) > 3:
        first.append(f"{len(odd['shapes'])} parts have no shape the format knows "
                     f"({', '.join(pid for pid, _ in odd['shapes'][:3])} ...) - taken as boxes")
    else:
        first += [f"{pid}: no shape {shape!r} - taken as a box" for pid, shape in odd["shapes"]]
    problems[:0] = first
    if isinstance(data.get("printer"), str) and data["printer"]:
        design["printer"] = data["printer"]
    bed = data.get("bed")
    if isinstance(bed, list) and len(bed) == 3 and all(_num(x) and x > 0 for x in bed):
        design["bed"] = [float(x) for x in bed]
    return design, problems


def _read_parts(items: list, seen: set, problems: list, odd: dict, own=None) -> list[dict]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            problems.append("a part that is not an object was left out")
            continue
        out.append(_read_part(item, seen, problems, odd, own))
    return out


def _points(v) -> list | None:
    """An outline's points: three or more [x, y]; None otherwise."""
    if not isinstance(v, list) or len(v) < 3:
        return None
    out = []
    for q in v:
        if not isinstance(q, list) or len(q) != 2 or not _num(q[0]) or not _num(q[1]):
            return None
        out.append([float(q[0]), float(q[1])])
    return out


def _read_repeat(v, pid: str, problems: list) -> dict | None:
    if v is None:
        return None
    if not isinstance(v, dict) or not _num(v.get("count")):
        problems.append(f'{pid}: "repeat" needs a "count" - left out')
        return None
    count = max(1, min(MAX_COPIES, _round(v["count"])))
    if count < 2:
        return None
    out: dict = {"count": count}
    step = v.get("step")
    if isinstance(step, list) and len(step) == 3 and all(_num(x) for x in step):
        out["step"] = [float(x) for x in step]
        return out
    around = v.get("around")
    if isinstance(around, list) and len(around) == 2 and all(_num(x) for x in around):
        out["around"] = [float(x) for x in around]
    if _num(v.get("angle")) and abs(v["angle"]) < 360:                     # all round is the default
        out["angle"] = float(v["angle"])
    return out


def _round(v: float) -> int:
    return int(math.floor(v + 0.5))                 # as JavaScript's Math.round


def _follows(shape: str, i: int, v: float) -> bool:
    """Whether a 0 in a part's size follows what it draws: a text's width or height (from its letters - one of the
    two is given), an outline's width and depth (from its points), any of a mesh's three (from its file, 0.104.0)."""
    return v <= 0 and (shape == "mesh" or (i < 2 and shape in ("text", "outline")))


def mesh_scale(size, own) -> list[float]:
    """A mesh part's scale along X, Y and Z (0.104.0): a size given fills the file's own size there; a 0 follows the
    given ones - their scale, the mean when they differ - and with none given it is the file's own size. Chat 335: a
    mesh written without a size was squeezed into the 20 x 20 x 20 mm every other shape gets, and nothing said so."""
    given = [size[i] / own[i] for i in range(3) if size[i] > 0 and own[i] > 1e-9]
    k = sum(given) / len(given) if given else 1.0
    return [(size[i] / own[i] if size[i] > 0 else k) if own[i] > 1e-9 else 1.0 for i in range(3)]


def _fit_mesh(size, own) -> list[float]:
    """A mesh part's size with its 0s worked out from the file's own size."""
    k = mesh_scale(size, own)
    return [size[i] if size[i] > 0 else own[i] * k[i] for i in range(3)]


def _own_sizes(folder):
    """file -> the file's own size (mm along X, Y and Z, from its corners as 32-bit floats - as the build reads
    them), None when it cannot be read: a mesh part's 0s from its file (0.104.0). Each file read once."""
    base, sizes = Path(folder), {}

    def own(file: str):
        if file not in sizes:
            try:
                v, _, _ = _load(base / file)
                v32 = np.ascontiguousarray(v, dtype=np.float32)
                sizes[file] = ([float(x) for x in v32.max(0).astype(np.float64) - v32.min(0).astype(np.float64)]
                               if len(v32) else None)
            except (OSError, ValueError):
                sizes[file] = None
        return sizes[file]
    return own


def _font_name(raw: str) -> tuple[str, bool, bool]:
    """A font as a design may name it - "serif", "Pacifico", "Arial Bold", OpenSCAD's "Liberation Sans:style=Bold
    Italic" - as one of FONT_FAMILIES, and the bold and italic its words ask for; family "" when it is none."""
    words = [w for w in re.split(r"[\s:_,=-]+", raw.lower()) if w]
    bold = any(w in BOLD_WORDS for w in words)
    italic = any(w in ITALIC_WORDS for w in words)
    name = " ".join(w for w in words if w not in BOLD_WORDS and w not in ITALIC_WORDS and w not in PLAIN_WORDS)
    family = FONT_FAMILIES[0] if not name else name if name in FONT_FAMILIES else FONT_ALIASES.get(name, "")
    return family, bold, italic


def _read_text_style(p: dict, part: dict, problems: list) -> None:
    pid = part["id"]
    if "font" in p:
        family, bold, italic = _font_name(p["font"]) if isinstance(p["font"], str) else ("", False, False)
        if not family:
            problems.append(f'{pid}: no font "{p["font"]}" - the fonts are {", ".join(FONT_FAMILIES)}; drawn in '
                            f'{FONT_FAMILIES[0]}')
        elif family != FONT_FAMILIES[0]:
            part["font"] = family
        if bold:
            part["bold"] = True
        if italic:
            part["italic"] = True
    if p.get("bold") is True:
        part["bold"] = True
    if p.get("italic") is True:
        part["italic"] = True
    if "align" in p:
        a = p["align"].strip().lower() if isinstance(p["align"], str) else ""
        if a in ("left", "right"):
            part["align"] = a
        elif a not in ("center", "centre", "middle"):
            problems.append(f'{pid}: align is "left", "center" or "right" - centred')
    for key in ("letter_spacing", "line_spacing"):
        if key not in p:
            continue
        v = p[key]
        if not _num(v):
            problems.append(f"{pid}: {key} is a number, 1 the font's own - left out")
            continue
        c = max(SPACING[0], min(SPACING[1], float(v)))
        if c != v:
            problems.append(f"{pid}: {key} is {SPACING[0]:g} to {SPACING[1]:g} - made {c:g}")
        if c != 1:
            part[key] = c


def _read_part(p: dict, seen: set, problems: list, odd: dict, own=None) -> dict:
    pid = p["id"].strip() if isinstance(p.get("id"), str) and p["id"].strip() else ""
    if not pid or pid in seen:
        n = len(seen) + 1
        while f"p{n}" in seen:
            n += 1
        pid = f"p{n}"
    seen.add(pid)
    shape = p.get("shape")
    if shape not in DESIGN_SHAPES:
        odd["shapes"].append((pid, shape))
        shape = "box"
    unknown = [k for k in p if k not in PART_KEYS]
    if unknown:
        odd["parts"] += 1
        odd["keys"].update(unknown)
    part: dict = {"id": pid, "shape": shape, "size": [20.0, 20.0, 20.0], "at": [0.0, 0.0, 10.0],
                  "rotate": [0.0, 0.0, 0.0]}
    if shape == "group":
        part["parts"] = _read_parts(p.get("parts") if isinstance(p.get("parts"), list) else [], seen, problems, odd,
                                    own)
        part["size"], part["at"] = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    else:
        size = _vec(p.get("size"), [0, 0, 2] if shape == "outline" else [0, 10, 2] if shape == "text"
                    else [0, 0, 0] if shape == "mesh" else [20, 20, 20])     # a mesh: its file's own size (0.104.0)
        part["size"] = [0.0 if _follows(shape, i, v) else max(MIN_SIZE, abs(v)) for i, v in enumerate(size)]
        if shape == "text" and part["size"][0] == 0 and part["size"][1] == 0:
            part["size"][1] = 10.0
            problems.append(f"{pid}: a text needs a width or a height (size x or y) - made 10 mm tall")
        if shape == "mesh" and own and 0 in part["size"] and isinstance(p.get("file"), str) and p["file"]:
            mine = own(p["file"])
            if mine:
                part["size"] = _fit_mesh(part["size"], mine)
        part["at"] = _vec(p.get("at"), [0, 0, part["size"][2] / 2])
        part["rotate"] = _vec(p.get("rotate"), [0, 0, 0])
    for flag in ("mirror", "hole", "hidden"):
        if p.get(flag) is True:
            part[flag] = True
    radius = p.get("radius")
    rounded = shape == "box" and _num(radius) and radius > 0
    if shape in ROUND_SHAPES or rounded:
        sides = p.get("sides")
        part["sides"] = max(3, min(256, _round(sides))) if _num(sides) else DEFAULT_SIDES
    if rounded:
        part["radius"] = float(radius)
    if shape == "cone":
        top = p.get("top")
        part["top"] = max(0.0, min(1.0, float(top))) if _num(top) else 0.0
    if shape == "text":
        part["text"] = p["text"] if isinstance(p.get("text"), str) else "Text"
        _read_text_style(p, part, problems)
    if shape == "outline":
        pts = _points(p.get("points"))
        if pts:
            part["points"] = pts
        else:
            problems.append(f'{pid}: an outline needs "points", three or more [x, y] - it makes nothing')
        if isinstance(p.get("holes"), list):
            holes = [_points(h) for h in p["holes"]]
            if any(h is None for h in holes):
                problems.append(f"{pid}: a hole that is not three or more [x, y] was left out")
            kept = [h for h in holes if h is not None]
            if kept:
                part["holes"] = kept
    if shape == "mesh":
        if isinstance(p.get("file"), str) and p["file"]:
            part["file"] = p["file"]
        else:
            problems.append(f'{pid}: a mesh without a "file" makes nothing')
    repeat = _read_repeat(p.get("repeat"), pid, problems)
    if repeat:
        part["repeat"] = repeat
    fillet = _read_edge_sizes(p, "fillet", part, problems)
    chamfer = _read_edge_sizes(p, "chamfer", part, problems)
    if (fillet or chamfer) and part.get("radius"):
        problems.append(f"{pid}: a box with a radius is rounded all round already - its fillet and chamfer were left out")
    else:
        if fillet:
            part["fillet"] = fillet
        if chamfer:
            part["chamfer"] = chamfer
        if fillet and shape in ("box", "wedge", "outline") and _num(p.get("sides")):
            part["sides"] = max(3, min(256, _round(p["sides"])))         # how round its fillets are
    if "on" in p:
        if shape == "group":
            problems.append(f'{pid}: a group cannot go "on" a face (its parts can) - left out')
        else:
            on = _read_on(p["on"], part, problems)
            if on:
                part["on"] = on
    if "mates" in p:
        if shape == "group":
            problems.append(f'{pid}: a group cannot have "mates" (its parts can) - left out')
        elif part.get("on"):
            problems.append(f'{pid}: "on" places it already - its "mates" were left out')
        else:
            mates = _read_mates(p["mates"], part, problems)
            if mates:
                part["mates"] = mates
    return part


# ---------------------------------------------------------------- on a face of a box (0.98.0)
# A part kept on a face of a box - the editor's Anchor (geometry.ts): so far from two of the face's edges, its own Z
# out of the face, as deep (a hole) or as high (a solid) as it says, or through all of the box. Placed there whenever
# the design is read, so it goes with the box when the box moves, turns or grows.

FACES = ("top", "bottom", "front", "back", "left", "right")
_ON_KEYS = ("part", "face", "from", "depth", "through", "angle")
# Each face in the box's own frame: which way it looks (n), across it (u), up it (v) - u x v = n - and the faces
# beyond its edges, across and then up, each axis from its - end. The editor's FACE_FRAMES.
FACE_FRAMES = {
    "top": ((0, 0, 1), (1, 0, 0), (0, 1, 0), (("left", "right"), ("front", "back"))),
    "bottom": ((0, 0, -1), (1, 0, 0), (0, -1, 0), (("left", "right"), ("back", "front"))),
    "front": ((0, -1, 0), (1, 0, 0), (0, 0, 1), (("left", "right"), ("bottom", "top"))),
    "back": ((0, 1, 0), (-1, 0, 0), (0, 0, 1), (("right", "left"), ("bottom", "top"))),
    "left": ((-1, 0, 0), (0, -1, 0), (0, 0, 1), (("back", "front"), ("bottom", "top"))),
    "right": ((1, 0, 0), (0, 1, 0), (0, 0, 1), (("front", "back"), ("bottom", "top"))),
}


def _read_on(v, part: dict, problems: list) -> dict | None:
    """A part's "on": the box and its face, the distances from the face's edges, how deep, the turn on it - the
    editor's readOn. Neither a depth nor through: as deep (or as high) as the part is thick."""
    pid = part["id"]
    if not isinstance(v, dict) or not isinstance(v.get("part"), str) or not v["part"].strip():
        problems.append(f'{pid}: "on" names the box it is on: {{"part": id, "face": "front", "from": {{"left": 20, '
                        f'"top": 15}}}} - left out')
        return None
    face = v["face"].strip().lower() if isinstance(v.get("face"), str) else ""
    if face not in FACES:
        problems.append(f"{pid}: \"on\" needs the box's face: {', '.join(FACES)} - left out")
        return None
    odd = [k for k in v if k not in _ON_KEYS]
    if odd:
        problems.append(f'{pid}: "on" has no {", ".join(chr(34) + k + chr(34) for k in odd)} - its keys: '
                        f'{", ".join(_ON_KEYS)}')
    out: dict = {"part": v["part"].strip(), "face": face}
    ends = FACE_FRAMES[face][3]
    edges = ", ".join(e for pair in ends for e in pair)
    if "from" in v:
        f = v["from"]
        if not isinstance(f, dict):
            problems.append(f"{pid}: \"from\" is {{edge: mm}} - the {face}'s edges: {edges}; put in the middle")
        else:
            frm: dict = {}
            for raw, d in f.items():
                key = str(raw).strip().lower()
                axis = next((i for i, pair in enumerate(ends) if key in pair), -1)
                if axis < 0:
                    problems.append(f'{pid}: the {face}\'s edges are {edges}, so many mm from each - "{raw}" left out')
                elif not _num(d):
                    problems.append(f'{pid}: "from" is mm from an edge - "{raw}" is not a number; left out')
                elif any(e in frm for e in ends[axis]):
                    problems.append(f'{pid}: "from" has {" and ".join(ends[axis])} - one of the two; "{raw}" left out')
                else:
                    frm[key] = float(d)
            if frm:
                out["from"] = frm
    if v.get("through") is True:
        out["through"] = True
    if "depth" in v:
        if _num(v["depth"]) and v["depth"] > 0:
            out["depth"] = float(v["depth"])
        else:
            problems.append(f'{pid}: "depth" is mm, more than 0 - left out')
    if not out.get("through") and "depth" not in out:
        out["depth"] = part["size"][2]
    if "angle" in v:
        if not _num(v["angle"]):
            problems.append(f'{pid}: "angle" is degrees - left out')
        elif abs(math.fmod(v["angle"], 360)) > 1e-9:
            out["angle"] = float(v["angle"])
    return out


def _r3(v: float) -> float:
    return _round(v * 1000) / 1000 + 0.0           # the editor's r3: to a thousandth, no -0


def _rotation_rows(r) -> list[float]:
    """_rotation_matrix as the editor's rotation() writes it out, row by row - the same sums in the same order."""
    x, y, z = (d * math.pi / 180 for d in r)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx,
            sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx,
            -sy, cy * sx, cy * cx]


def _apply(m: list[float], v) -> tuple[float, float, float]:
    return (m[0] * v[0] + m[1] * v[1] + m[2] * v[2], m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
            m[6] * v[0] + m[7] * v[1] + m[8] * v[2])


def _angles(m: list[float]) -> list[float]:
    """The angles (degrees, X then Y then Z) of a rotation matrix, row by row - _rotation_rows' inverse, the editor's
    angles()."""
    sy = max(-1.0, min(1.0, -m[6]))
    y = math.asin(sy)
    if abs(sy) < 0.9999999:
        x, z = math.atan2(m[7], m[8]), math.atan2(m[3], m[0])
    else:                                            # straight up or down: all of it in X
        x, z = math.atan2(-m[5], m[4]), 0.0

    def deg(a: float) -> float:
        r = _round(a * 180 / math.pi * 1e6) / 1e6
        return 0.0 if abs(r) < 1e-9 else r
    return [deg(x), deg(y), deg(z)]


def _face_frame(box: dict, face: str):
    """A face of a box where it is in the design - the editor's faceFrame: its width across, height up and the box's
    depth behind it; U, V, N (across, up, out: U = V x N); and where the spot x across, y up from its middle is."""
    n, u, v, _ = FACE_FRAMES[face]
    m = _rotation_rows(box["rotate"])

    def own(d):
        return _apply(m, (-d[0], d[1], d[2]) if box.get("mirror") else d)
    s = box["size"]

    def reach(d):
        return abs(d[0]) * s[0] + abs(d[1]) * s[1] + abs(d[2]) * s[2]
    W, H, T = reach(u), reach(v), reach(n)
    N, V = own(n), own(v)
    U = (V[1] * N[2] - V[2] * N[1], V[2] * N[0] - V[0] * N[2], V[0] * N[1] - V[1] * N[0])

    def at(x: float, y: float):
        w = own((n[0] * T / 2 + u[0] * x + v[0] * y, n[1] * T / 2 + u[1] * x + v[1] * y, n[2] * T / 2 + u[2] * x
                 + v[2] * y))
        return (w[0] + box["at"][0], w[1] + box["at"][1], w[2] + box["at"][2])
    return W, H, T, U, V, N, at


def _spot_xy(a: dict, W: float, H: float) -> tuple[float, float]:
    ends, f = FACE_FRAMES[a["face"]][3], a.get("from") or {}

    def one(axis: int, L: float) -> float:
        lo, hi = ends[axis]
        return f[lo] - L / 2 if lo in f else L / 2 - f[hi] if hi in f else 0.0
    return one(0, W), one(1, H)


def _place_on(p: dict, box: dict) -> dict:
    """A part where its "on" puts it on its box - the editor's placeOn: its own Z out of the face, its X across it
    (turned by angle), unmirrored, its middle over the spot; a solid out of the face as far as its depth, 0.2 mm into
    it, a hole in as deep with its top 0.2 mm out - or through all of the box, 0.2 mm out on both sides."""
    a = p["on"]
    W, H, T, U, V, N, at = _face_frame(box, a["face"])
    x, y = _spot_xy(a, W, H)
    pt = at(x, y)
    t = a.get("angle", 0.0) * math.pi / 180
    c, s = math.cos(t), math.sin(t)
    X = (U[0] * c + V[0] * s, U[1] * c + V[1] * s, U[2] * c + V[2] * s)
    Y = (V[0] * c - U[0] * s, V[1] * c - U[1] * s, V[2] * c - U[2] * s)
    hole = bool(p.get("hole"))
    h = _r3(T + 0.4 if a.get("through") and hole else a.get("depth", T) + 0.2)
    lift = 0.2 - h / 2 if hole else h / 2 - 0.2
    out = dict(p)
    out["at"] = [_r3(pt[i] + N[i] * lift) for i in range(3)]
    out["rotate"] = _angles([X[0], Y[0], N[0], X[1], Y[1], N[1], X[2], Y[2], N[2]])
    out["size"] = [p["size"][0], p["size"][1], h]
    out.pop("mirror", None)
    return out


def _settle(parts: list, problems: list | None = None) -> list:
    """The parts, each one with "on" where its anchor puts it - after the box it is on, which may be on a face too;
    one whose box is not there, is not a box or leads round in a circle left where it is, free (said). One with mates
    (0.101.0) where they put it, after the parts they are to; a mate to a part that is not there, to a group or round
    in a circle left out (said). The editor's settle(), without its edits."""
    every: dict = {}

    def index(items: list) -> None:
        for q in items:
            every[q["id"]] = q
            index(q.get("parts") or [])
    index(parts)
    if not any(q.get("on") or q.get("mates") for q in every.values()):
        return parts
    done: dict = {}
    busy: set = set()

    def free(q: dict, why: str) -> dict:
        if problems is not None:
            problems.append(f"{q['id']}: {why} - left where it is")
        out = dict(q)
        out.pop("on", None)
        return out

    def resolve(q: dict) -> dict:
        if q["id"] in done:
            return done[q["id"]]
        out = q
        a = q.get("on")
        if a:
            b = every.get(a["part"])
            if q["id"] in busy:
                out = free(q, 'its "on" leads round in a circle')
            elif b is None:
                out = free(q, f'"on" names "{a["part"]}", which the design does not have')
            elif b["shape"] != "box":
                out = free(q, f"\"on\" is a box's face - {b['id']} is a {b['shape']}")
            else:
                busy.add(q["id"])
                box = resolve(b)
                busy.discard(q["id"])
                if q["id"] in done:                  # freed on the way round a circle
                    return done[q["id"]]
                out = _place_on(q, box)
        elif q.get("mates"):
            out = mated(q)
        done[q["id"]] = out
        return out

    def leads(frm: dict, to: str, seen: set) -> bool:
        """Whether a part is placed by another - on it, mated to it, or to one that is: those placed already as they
        are now (what was left out of them left out), the others as they are given."""
        if frm["id"] == to:
            return True
        if frm["id"] in seen:
            return False
        seen.add(frm["id"])
        f = done.get(frm["id"], frm)
        ids = [(f.get("on") or {}).get("part")] + [m["to"]["part"] for m in f.get("mates") or []]
        return any(i is not None and i in every and leads(every[i], to, seen) for i in ids)

    def mated(p: dict) -> dict:
        kept: list = []
        to: dict = {}
        for i, m in enumerate(p["mates"]):
            q = every.get(m["to"]["part"])
            face = m["to"].get("face")
            if q is None:
                why = f'names "{m["to"]["part"]}", which the design does not have'
            elif q["id"] == p["id"]:
                why = "is to the part itself"
            elif q["shape"] == "group":
                why = f"is to a group, {q['id']} (mate one of its parts)"
            elif leads(q, p["id"], set()):
                why = f"leads round in a circle: {q['id']} is placed by {p['id']}"
            elif face and _letters_side(q, face):
                why = f"is to the {face} of {q['id']}, a text whose size there follows its letters (mate another face)"
            else:
                why = ""
            if why:
                if problems is not None:
                    problems.append(f"{p['id']}: mate {i + 1} {why} - left out")
                continue
            to[q["id"]] = resolve(q)
            kept.append((m, i))
        out = dict(p, mates=[m for m, _ in kept])
        if not kept:
            out.pop("mates")
            return out
        return _solve_mates(out, kept, to, problems)

    def walk(items: list) -> list:
        return [dict(q, parts=walk(q.get("parts") or [])) if q["shape"] == "group" else resolve(q) for q in items]
    return walk(parts)


# ---------------------------------------------------------------- mates (0.101.0)
# Placed against other parts, as in SolidWorks' assemblies - the editor's mates (geometry.ts): a face of the part
# against a face of another (coincident - flush, looking the same way, with flip), so many mm out from it (distance),
# or its axis on the other's (concentric). The part the mates are on moves - as little as they need, after the parts
# they are to. A part's faces are its own box's, before it is turned; its axis is its own Z through its middle.

MATE_TYPES = ("coincident", "distance", "concentric")
_MATE_KEYS = ("type", "face", "to", "mm", "flip")
MATE_JOIN = 1e-5       # a coincident face of a solid this far into the solid it is on (mm): one piece - the editor's JOIN


def _face_axis(face: str) -> int:
    return next(i for i, x in enumerate(FACE_FRAMES[face][0]) if x != 0)


def _letters_side(p: dict, face: str) -> bool:
    """A text's side whose place follows its letters (its size there 0) - unknown until its font is: not to mate."""
    return p["shape"] == "text" and p["size"][_face_axis(face)] <= 0


def _face_in(v) -> str | None:
    f = v.strip().lower() if isinstance(v, str) else ""
    return f if f in FACES else None


def _read_mates(v, part: dict, problems: list) -> list | None:
    """A part's "mates" - the editor's readMates: each a type, its face and the part (and face) it is to; those that
    make no sense left out, said."""
    pid = part["id"]
    if not isinstance(v, list):
        problems.append(f'{pid}: "mates" is a list - [{{"type": "coincident", "face": "bottom", "to": {{"part": id, '
                        f'"face": "top"}}}}]; left out')
        return None
    out: list = []
    for i, m in enumerate(v):
        n = f"{pid}: mate {i + 1}"
        if not isinstance(m, dict):
            problems.append(f"{n} is not an object - left out")
            continue
        odd = [k for k in m if k not in _MATE_KEYS]
        if odd:
            problems.append(f'{n} has no {", ".join(chr(34) + k + chr(34) for k in odd)} - its keys: '
                            f'{", ".join(_MATE_KEYS)}')
        t = m.get("to")
        if isinstance(t, str):
            to = {"part": t.strip(), "face": None}
        elif isinstance(t, dict) and isinstance(t.get("part"), str):
            to = {"part": t["part"].strip(), "face": t.get("face")}
        else:
            to = None
        if not to or not to["part"]:
            problems.append(f'{n} names the part it is mated to: "to": {{"part": id, "face": "top"}} - left out')
            continue
        if isinstance(t, dict):
            odd_to = [k for k in t if k not in ("part", "face")]
            if odd_to:
                problems.append(f'{n}: "to" has no {", ".join(chr(34) + k + chr(34) for k in odd_to)} - its keys: '
                                f'part, face')
        given_face = "face" in m
        given_to_face = isinstance(t, dict) and "face" in t
        if "type" in m:
            kind = m["type"].strip().lower() if isinstance(m["type"], str) else ""
        elif given_face or given_to_face:
            kind = "distance" if _num(m.get("mm")) and m["mm"] != 0 else "coincident"
        else:
            kind = "concentric"
        if kind not in MATE_TYPES:
            said = f' "{m["type"]}"' if isinstance(m.get("type"), str) else ""
            problems.append(f"{n}: no type{said} - the types: {', '.join(MATE_TYPES)}; left out")
            continue
        mate: dict = {"type": kind, "to": {"part": to["part"]}}
        if kind == "concentric":
            if given_face or given_to_face:
                problems.append(f"{n}: concentric is axis on axis - its faces were left out")
            if "mm" in m:
                problems.append(f'{n}: concentric has no "mm" - left out')
        else:
            face, to_face = _face_in(m.get("face")), _face_in(to["face"])
            if not face or not to_face:
                problems.append(f'{n}: {kind} is face to face - "face" and the "to" part\'s "face" are each one of '
                                f'{", ".join(FACES)}; left out')
                continue
            if _letters_side(part, face):
                problems.append(f"{n}: its {face} follows its letters (a text, its size there 0) - mate another face; "
                                f"left out")
                continue
            mate["face"] = face
            mate["to"]["face"] = to_face
            if kind == "distance":
                mate["mm"] = float(m["mm"]) if _num(m.get("mm")) else 0.0
                if not _num(m.get("mm")):
                    problems.append(f'{n}: distance needs "mm" - made 0')
            elif "mm" in m and not (_num(m["mm"]) and m["mm"] == 0):
                problems.append(f'{n}: coincident has no "mm" (distance has) - left out')
        if m.get("flip") is True:
            mate["flip"] = True
        out.append(mate)
    return out or None


def _own_box(p: dict):
    """A part's own box before it is mirrored, turned and moved - the editor's ownBox: centred on its 0,0,0, an
    outline's where its points are drawn."""
    sx, sy, sz = p["size"]
    polys = _outline_polygons(p) if p["shape"] == "outline" else None
    if polys is not None:
        outer = polys[0]
        return ((float(outer[:, 0].min()), float(outer[:, 1].min()), -sz / 2),
                (float(outer[:, 0].max()), float(outer[:, 1].max()), sz / 2))
    return (-sx / 2, -sy / 2, -sz / 2), (sx / 2, sy / 2, sz / 2)


def _mul(a: list, b: list) -> list:
    return [sum(a[i * 3 + k] * b[k * 3 + j] for k in range(3)) for i in range(3) for j in range(3)]


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b) -> tuple:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit3(v) -> tuple:
    n = math.hypot(v[0], v[1], v[2])
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def _part_frame(p: dict):
    """A part's own frame in the design - the editor's partFrame: A (its turn and mirror), its box's middle and half
    sizes in its own frame, and that middle in the design."""
    lo, hi = _own_box(p)
    r = _rotation_rows(p["rotate"])
    a = [-r[0], r[1], r[2], -r[3], r[4], r[5], -r[6], r[7], r[8]] if p.get("mirror") else r
    mid = tuple((lo[i] + hi[i]) / 2 for i in range(3))
    half = tuple((hi[i] - lo[i]) / 2 for i in range(3))
    w = _apply(a, mid)
    return a, mid, half, (w[0] + p["at"][0], w[1] + p["at"][1], w[2] + p["at"][2])


def _mate_plane(p: dict, face: str):
    """A face of a part's own box in the design: its middle and which way it looks."""
    a, mid, half, _ = _part_frame(p)
    n = FACE_FRAMES[face][0]
    w = _apply(a, (mid[0] + n[0] * half[0], mid[1] + n[1] * half[1], mid[2] + n[2] * half[2]))
    return (w[0] + p["at"][0], w[1] + p["at"][1], w[2] + p["at"][2]), _apply(a, n)


def _mate_axis(p: dict):
    """A part's axis in the design: its own Z through its middle."""
    a, _, _, c = _part_frame(p)
    return c, (a[2], a[5], a[8])


def _turn_about(k, angle: float) -> list:
    c, s = math.cos(angle), math.sin(angle)
    t = 1 - c
    x, y, z = k
    return [t * x * x + c, t * x * y - s * z, t * x * z + s * y,
            t * x * y + s * z, t * y * y + c, t * y * z - s * x,
            t * x * z - s * y, t * y * z + s * x, t * z * z + c]


def _turn_onto(v, d, a: list) -> list | None:
    """The least turn taking the unit v onto the unit d - None when it is there; the other way round, half a turn
    about the part's own axis (a's columns) most across v. The editor's turnOnto."""
    k = _cross(v, d)
    s, c = math.hypot(k[0], k[1], k[2]), _dot(v, d)
    if s < 1e-9:
        if c > 0:
            return None
        e = (a[0], a[3], a[6])
        least = abs(_dot(e, v))
        for j in (1, 2):
            f = (a[j], a[3 + j], a[6 + j])
            g = abs(_dot(f, v))
            if g < least - 1e-12:
                e, least = f, g
        x = _dot(e, v)
        return _turn_about(_unit3((e[0] - x * v[0], e[1] - x * v[1], e[2] - x * v[2])), math.pi)
    angle = math.atan2(s, c)
    return None if angle < 1e-7 else _turn_about((k[0] / s, k[1] / s, k[2] / s), angle)


def _across(d) -> tuple:
    """Two unit directions square to the unit d and to each other - the editor's across."""
    a, b, c = abs(d[0]), abs(d[1]), abs(d[2])
    e = (1, 0, 0) if a <= b and a <= c else (0, 1, 0) if b <= c else (0, 0, 1)
    u = _unit3(_cross(d, e))
    return u, _cross(d, u)


def _moved(p: dict, centre, move, turn: list | None) -> dict:
    """A part turned by turn about centre and moved by move: its at, rotate and where its copies go - the editor's
    moved (transformPart's sums for one part)."""
    rel = (p["at"][0] - centre[0], p["at"][1] - centre[1], p["at"][2] - centre[2])
    r = _apply(turn, rel) if turn else rel
    out = dict(p, at=[centre[i] + r[i] + move[i] for i in range(3)])
    if turn:
        out["rotate"] = _angles(_mul(turn, _rotation_rows(p["rotate"])))
    if p.get("repeat"):
        rp = dict(p["repeat"])
        if rp.get("step"):
            rp["step"] = list(_apply(turn, rp["step"])) if turn else rp["step"]
        elif rp.get("around"):
            a = (rp["around"][0] - centre[0], rp["around"][1] - centre[1], 0.0)
            w = _apply(turn, a) if turn else a
            rp["around"] = [centre[0] + w[0] + move[0], centre[1] + w[1] + move[1]]
        out["repeat"] = rp
    return out


def _mm(v: float) -> str:
    """mm as the editor writes them in what it says (fmtMm): to a hundredth, no .0."""
    r = _round(v * 100) / 100
    return str(int(r)) if r == int(r) else repr(r)


def _solve_mates(p: dict, mates: list, to: dict, problems: list | None) -> dict:
    """Where a part's mates put it, the parts they are to placed already - the editor's solveMates, step for step:
    turned first, each mate's directions in turn, as little as the turns left allow, about its middle; then moved as
    little as puts each face on the other's plane and its axis on the other's line. A mate the ones before it leave no
    room for is said and skipped (kept). mates: (mate, its number in the part's list)."""
    a, mid, half, c = _part_frame(p)
    r = _rotation_rows(p["rotate"])
    turn = None
    free = "all"                                     # the turns left: "all", an axis, "none"
    skipped: set = set()

    def say(m: dict, i: int, why: str) -> None:
        skipped.add(i)
        if problems is not None:
            problems.append(f"{p['id']}: mate {i + 1} ({m['type']} to {m['to']['part']}) cannot be met as well as the "
                            f"ones before it - {why}; skipped")

    for m, i in mates:
        q = to[m["to"]["part"]]
        if m["type"] == "concentric":
            z = _mate_axis(q)[1]
            v = (a[2], a[5], a[8])
            d = (-z[0], -z[1], -z[2]) if m.get("flip") else z
        else:
            nq = _mate_plane(q, m["to"]["face"])[1]
            v = _apply(a, FACE_FRAMES[m["face"]][0])
            d = nq if m.get("flip") else (-nq[0], -nq[1], -nq[2])
        t = None
        if free == "all":
            t = _turn_onto(v, d, a)
            free = d
        elif free != "none":
            ax = free
            cv, cd = _dot(v, ax), _dot(d, ax)
            if abs(math.acos(max(-1.0, min(1.0, cv))) - math.acos(max(-1.0, min(1.0, cd)))) > 1e-6:
                say(m, i, "it would have to turn")
                continue
            pv = (v[0] - cv * ax[0], v[1] - cv * ax[1], v[2] - cv * ax[2])
            pd = (d[0] - cd * ax[0], d[1] - cd * ax[1], d[2] - cd * ax[2])
            if math.hypot(pv[0], pv[1], pv[2]) > 1e-6:       # across the axis: turned about it, no turn left after
                angle = math.atan2(_dot(ax, _cross(pv, pd)), _dot(pv, pd))
                if abs(angle) > 1e-7:
                    t = _turn_about(ax, angle)
                free = "none"
        else:
            k = _cross(v, d)
            if math.atan2(math.hypot(k[0], k[1], k[2]), _dot(v, d)) > 1e-6:
                say(m, i, "it would have to turn")
                continue
        if t:
            a, r = _mul(t, a), _mul(t, r)
            turn = _mul(t, turn) if turn else t
    w0 = _apply(a, mid)
    at = [c[0] - w0[0], c[1] - w0[1], c[2] - w0[2]] if turn else list(p["at"])
    basis = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    shift = False

    def push(m: dict, i: int, j, rr: float) -> bool:
        nonlocal at, basis, shift
        w = [0.0, 0.0, 0.0]
        for b in basis:
            x = _dot(j, b)
            w[0] += x * b[0]
            w[1] += x * b[1]
            w[2] += x * b[2]
        ww = _dot(w, w)
        if ww < 1e-12:
            if abs(rr) <= 2e-3:
                return True
            say(m, i, f"{_mm(abs(rr))} mm off")
            return False
        if abs(rr) > 1e-9:
            at = [at[0] + rr * w[0] / ww, at[1] + rr * w[1] / ww, at[2] + rr * w[2] / ww]
            shift = True
        u = _unit3(w)
        nxt: list = []
        for b in basis:
            x = (b[0] - _dot(b, u) * u[0], b[1] - _dot(b, u) * u[1], b[2] - _dot(b, u) * u[2])
            for o in nxt:
                x = (x[0] - _dot(x, o) * o[0], x[1] - _dot(x, o) * o[1], x[2] - _dot(x, o) * o[2])
            ln = math.hypot(x[0], x[1], x[2])
            if ln > 1e-6:
                nxt.append((x[0] / ln, x[1] / ln, x[2] / ln))
        basis = nxt
        return True

    def place(lp):
        w = _apply(a, lp)
        return (w[0] + at[0], w[1] + at[1], w[2] + at[2])

    for m, i in mates:
        if i in skipped:
            continue
        q = to[m["to"]["part"]]
        if m["type"] == "concentric":
            pt, dr = _mate_axis(q)
            for u in _across(dr):
                e = place(mid)
                if not push(m, i, u, -_dot((e[0] - pt[0], e[1] - pt[1], e[2] - pt[2]), u)):
                    break
        else:
            pt, nq = _mate_plane(q, m["to"]["face"])
            n = FACE_FRAMES[m["face"]][0]
            e = place((mid[0] + n[0] * half[0], mid[1] + n[1] * half[1], mid[2] + n[2] * half[2]))
            # a solid's face on another solid's (or flush with a hole's) a hair into it: one piece, however turned
            if m["type"] == "distance":
                want = m.get("mm", 0.0)
            elif p.get("hole"):
                want = 0.0
            elif q.get("hole"):
                want = MATE_JOIN if m.get("flip") else 0.0
            else:
                want = 0.0 if m.get("flip") else -MATE_JOIN
            push(m, i, nq, want - _dot((e[0] - pt[0], e[1] - pt[1], e[2] - pt[2]), nq))
    if not turn and not shift:
        return p
    w1 = _apply(a, mid)
    c1 = (w1[0] + at[0], w1[1] + at[1], w1[2] + at[2])
    out = _moved(p, c, (c1[0] - c[0], c1[1] - c[1], c1[2] - c[2]), turn)
    if turn:
        out["rotate"] = _angles(r)
    out["at"] = list(at)                 # not rounded: faces on parts turned askew meet exactly, built as one piece
    return out


# ---------------------------------------------------------------- edges: fillets and chamfers (0.91.0)

# A part's own edges - the editor's EDGE_FACES and EDGES (geometry.ts), name for name: its faces (each names the
# edges round it) and each edge as the two faces it lies between, in the faces' order.
EDGE_FACES = {"box": ("top", "bottom", "front", "back", "left", "right"),
              "wedge": ("bottom", "left", "front", "back", "slope"),
              "cylinder": ("top", "bottom"), "cone": ("top", "bottom")}
EDGES = {"box": ("top-front", "top-back", "top-left", "top-right", "bottom-front", "bottom-back", "bottom-left",
                 "bottom-right", "front-left", "front-right", "back-left", "back-right"),
         "wedge": ("bottom-left", "bottom-front", "bottom-back", "bottom-slope", "left-front", "left-back", "left-slope",
                   "front-slope", "back-slope"),
         "cylinder": ("top", "bottom"), "cone": ("top", "bottom"), "outline": ("vertical",)}
_VERTICAL = {"box": ("front-left", "front-right", "back-left", "back-right"), "wedge": ("left-front", "left-back"),
             "outline": ("vertical",)}


def _edge_name(shape: str, raw: str) -> str | None:
    """An edge name as the file keeps it ("Front_Top" -> "top-front"), or None - the editor's edgeName."""
    edges = EDGES.get(shape)
    if not edges:
        return None
    words = [w for w in re.split(r"[\s_-]+", raw.strip().lower()) if w]
    key = "-".join(words)
    if key == "all" or (key == "vertical" and shape in _VERTICAL):
        return key
    if shape == "outline":
        return "vertical" if key == "corners" else None
    faces = EDGE_FACES.get(shape, ())
    if len(words) == 1 and key in faces:
        return key
    if len(words) == 2:
        pair = "-".join(sorted(words, key=lambda w: faces.index(w) if w in faces else -1))
        if pair in edges:
            return pair
    return None


def _covers(shape: str, key: str, edge: str) -> int:
    if key == "all":
        return 0
    if key == "vertical":
        return 1 if edge in _VERTICAL.get(shape, ()) else -1
    if key == edge:
        return 2
    return 1 if key in edge.split("-") else -1


def _edge_plan(p: dict) -> dict:
    """Each of a part's edges that is rounded or cut: {edge: (kind, size)} - the editor's edgePlan."""
    out = {}
    for edge in EDGES.get(p["shape"], ()):
        best = None
        for kind in ("chamfer", "fillet"):
            for key, size in (p.get(kind) or {}).items():
                spec = _covers(p["shape"], key, edge)
                if spec < 0:
                    continue
                rank, was = (1 if kind == "fillet" else 0), (1 if best and best[0] == "fillet" else 0)
                if best is None or spec > best[2] or (spec == best[2] and (rank > was or (rank == was
                                                                                           and size > best[1]))):
                    best = (kind, size, spec)
        if best:
            out[edge] = (best[0], best[1])
    return out


def _edge_quarter(p: dict) -> int:
    return max(1, _round(p.get("sides", 32) / 4))


def _round_corners(poly: list, corners: list, quarter: int):
    """A polygon with its corners rounded or cut - the editor's roundCorners, step for step: (points, clamped,
    the size each corner got)."""
    n = len(poly)
    out: list = []
    sizes = [0.0] * n
    clamped = False
    for i in range(n):
        c, spec = poly[i], corners[i]
        if not spec:
            out.append(c)
            continue
        a, b = poly[(i + n - 1) % n], poly[(i + 1) % n]
        lin = math.sqrt((c[0] - a[0]) * (c[0] - a[0]) + (c[1] - a[1]) * (c[1] - a[1]))
        lout = math.sqrt((b[0] - c[0]) * (b[0] - c[0]) + (b[1] - c[1]) * (b[1] - c[1]))
        din = ((c[0] - a[0]) / lin, (c[1] - a[1]) / lin)
        dout = ((b[0] - c[0]) / lout, (b[1] - c[1]) / lout)
        turn = math.atan2(din[0] * dout[1] - din[1] * dout[0], din[0] * dout[0] + din[1] * dout[1])
        if abs(turn) < 1e-9:
            out.append(c)
            continue
        k = math.tan(abs(turn) / 2)
        t = spec[1] * k if spec[0] == "fillet" else spec[1]
        room = min(lin * (0.5 if corners[(i + n - 1) % n] else 0.999), lout * (0.5 if corners[(i + 1) % n] else 0.999))
        if t > room:
            t = room
            clamped = True
        p1 = (c[0] - t * din[0], c[1] - t * din[1])
        p2 = (c[0] + t * dout[0], c[1] + t * dout[1])
        if spec[0] == "chamfer":
            sizes[i] = t
            out += [p1, p2]
            continue
        r = t / k
        sizes[i] = r
        side = 1 if turn > 0 else -1
        o = (p1[0] - side * r * din[1], p1[1] + side * r * din[0])
        a0 = math.atan2(p1[1] - o[1], p1[0] - o[0])
        m = max(1, _round(quarter * abs(turn) / (math.pi / 2)))
        for j in range(m + 1):
            ang = a0 + turn * j / m
            out.append((o[0] + r * math.cos(ang), o[1] + r * math.sin(ang)))
    return out, clamped, sizes


def _round_all(p: dict) -> float:
    """A box whose 12 edges are all fillets of one size: that size (built as a box with a radius), else 0."""
    if p["shape"] != "box":
        return 0.0
    plan = _edge_plan(p)
    r = plan.get(EDGES["box"][0], ("", 0.0))[1]
    return r if all(plan.get(e) == ("fillet", r) for e in EDGES["box"]) else 0.0


def _edge_prisms(p: dict):
    """The prisms a box or wedge with treated edges is the intersection of, and a box's corners where three fillets of
    one size meet (a ball's piece goes there) - the editor's edgePrisms: ([(polygon, u, v, length)], clamped,
    [(centre, corner, r)])."""
    plan = _edge_plan(p)
    q = _edge_quarter(p)
    sx, sy, sz = p["size"]
    x, y, z = sx / 2, sy / 2, sz / 2
    X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    out: list = []
    got: dict = {}
    clamped = False

    def add(poly, names, u, v, length, always=False):
        nonlocal clamped
        corners = [plan.get(nm) if nm else None for nm in names]
        if not always and not any(corners):
            return
        pts, c, sizes = _round_corners(poly, corners, q)
        clamped = clamped or c
        for i, nm in enumerate(names):
            if nm and plan.get(nm, ("",))[0] == "fillet":
                got[nm] = sizes[i]
        out.append((pts, u, v, length))

    balls: list = []
    if p["shape"] == "box":
        add([(-x, -y), (x, -y), (x, y), (-x, y)], ["front-left", "front-right", "back-right", "back-left"], X, Y, sz)
        add([(-y, -z), (y, -z), (y, z), (-y, z)], ["bottom-front", "bottom-back", "top-back", "top-front"], Y, Z, sx)
        add([(-z, -x), (z, -x), (z, x), (-z, x)], ["bottom-left", "top-left", "top-right", "bottom-right"], Z, X, sy)
        for a in (-1, 1):
            for b in (-1, 1):
                for c in (-1, 1):
                    ud, fb, lr = ("top" if c > 0 else "bottom"), ("front" if b < 0 else "back"), \
                        ("left" if a < 0 else "right")
                    r = got.get(f"{ud}-{fb}")
                    if not r or got.get(f"{ud}-{lr}") != r or got.get(f"{fb}-{lr}") != r:
                        continue
                    balls.append(((a * (x - r), b * (y - r), c * (z - r)), (a * x, b * y, c * z), r))
    elif p["shape"] == "wedge":
        length, deep = math.sqrt(sx * sx + sz * sz), sx + sz
        n = (sz / length, 0.0, sx / length)
        add([(-z, -x), (z, -x), (-z, x)], ["bottom-left", "left-slope", "bottom-slope"], Z, X, sy, True)
        add([(-y, -z), (y, -z), (y, -z + deep), (-y, -z + deep)], ["bottom-front", "bottom-back", None, None], Y, Z, sx)
        add([(-x, -y), (-x + deep, -y), (-x + deep, y), (-x, y)], ["left-front", None, None, "left-back"], X, Y, sz)
        add([(-y, -deep), (y, -deep), (y, 0.0), (-y, 0.0)], [None, None, "back-slope", "front-slope"], Y, n, length)
    return out, clamped, balls


def _edge_profile(p: dict):
    """A cylinder's or cone's half outline with its rims treated - the editor's edgeProfile."""
    plan = _edge_plan(p)
    r, h = p["size"][0] / 2, p["size"][2] / 2
    top = r * p.get("top", 0.0) if p["shape"] == "cone" else r
    if top > 1e-9:
        return _round_corners([(0.0, -h), (r, -h), (top, h), (0.0, h)], [None, plan.get("bottom"), plan.get("top"), None],
                              _edge_quarter(p))
    return _round_corners([(0.0, -h), (r, -h), (0.0, h)], [None, plan.get("bottom"), None], _edge_quarter(p))


def _read_edge_sizes(p: dict, kind: str, part: dict, problems: list):
    if kind not in p:
        return None
    v = p[kind]
    if not isinstance(v, dict):
        problems.append(f'{part["id"]}: "{kind}" is {{ edge: mm }} - left out')
        return None
    if part["shape"] not in EDGES:
        problems.append(f'{part["id"]}: a {part["shape"]} has no edges of its own to {kind} - left out')
        return None
    out: dict = {}
    for key, size in v.items():
        name = _edge_name(part["shape"], key)
        if not name or not _num(size) or size <= 0:
            problems.append(f'{part["id"]}: no {kind} "{key}" - {_edge_choices(part["shape"])}, each with its size in mm')
            continue
        out[name] = max(out.get(name, 0), float(size))
    return out or None


def _edge_choices(shape: str) -> str:
    if shape == "outline":
        return "an outline's edges: vertical (its corners), all"
    if shape in ("cylinder", "cone"):
        return f"a {shape}'s edges: top, bottom, all"
    faces = EDGE_FACES.get(shape, ())
    return (f"a {shape}'s edges: {', '.join(faces)} (the edges round that face), vertical, all, or two faces: "
            + ("top-front" if shape == "box" else "left-slope"))


def _outline_polygons(p: dict):
    """An outline's points scaled to its size (0: as drawn), its holes after them - the editor's outlinePolygons."""
    pts = p.get("points")
    if not pts or len(pts) < 3:
        return None
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    kx = p["size"][0] / (x1 - x0) if p["size"][0] > 0 and x1 - x0 > 1e-9 else 1.0
    ky = p["size"][1] / (y1 - y0) if p["size"][1] > 0 and y1 - y0 > 1e-9 else 1.0
    return [np.array([(x * kx, y * ky) for x, y in poly], dtype=np.float64) for poly in [pts] + p.get("holes", [])]


def _repeat_around(p: dict) -> tuple[float, float]:
    """Where a repeat turns its copies about: around, or the part's own at (a group's: the plate's middle)."""
    r = p["repeat"]
    if "around" in r:
        return r["around"][0], r["around"][1]
    return (0.0, 0.0) if p["shape"] == "group" else (p["at"][0], p["at"][1])


def _repeat_step(r: dict) -> float:
    """The turn from one copy to the next, degrees - the editor's repeatStep."""
    a = r.get("angle", 360.0)
    if abs(a) >= 360 - 1e-9:
        return a / r["count"]
    return a / (r["count"] - 1) if r["count"] > 1 else 0.0


def _font_index() -> dict:
    if "index" not in _font_cache:
        import json
        _font_cache["index"] = json.loads((FONT_DIR / "index.json").read_text(encoding="utf-8"))
    return _font_cache["index"]


def _font(file: str) -> dict:
    if file not in _font_cache:
        import json
        _font_cache[file] = json.loads((FONT_DIR / f"{file}.json").read_text(encoding="utf-8"))
    return _font_cache[file]


def _font_file(p: dict) -> tuple[str, float]:
    """The font file a text part is drawn in, and how far its letters lean - the editor's fontFile: a family without
    an italic slants its upright; one without a bold draws its regular."""
    index = _font_index()
    fam = index["families"].get(p.get("font") or "") or index["families"][index["default"]]
    style = ("bold-italic" if p.get("bold") and p.get("italic") else "bold" if p.get("bold")
             else "italic" if p.get("italic") else "regular")
    if fam.get(style):
        return fam[style], 0.0
    return (p.get("bold") and fam.get("bold")) or fam["regular"], SLANT if p.get("italic") else 0.0


def _text_contours(p: dict, font: dict, slant: float = 0.0) -> list[list[tuple[float, float]]]:
    """A text's outlines in font units, laid out as the editor's textContours: letter after letter by their advances
    (times letter_spacing between them), line under line by the font's line height (times line_spacing), the lines
    centred on x 0 or starting or ending there (align), slanted by `slant`."""
    out = []
    ls = p.get("letter_spacing", 1.0)
    step = (font["ascender"] - font["descender"]) * p.get("line_spacing", 1.0)
    align = p.get("align")
    for row, line in enumerate(p.get("text", "").split("\n")):
        placed, x, last = [], 0.0, 0.0
        for ch in line:
            g = font["glyphs"].get(ch) or font["glyphs"].get("?")
            if not g:
                continue
            placed.append((g["c"], x))
            last = g["a"]
            x += g["a"] * ls
        width = x - last * ls + last if placed else 0.0
        off = 0.0 if align == "left" else -width if align == "right" else -width / 2
        y = -row * step
        for cs, gx in placed:
            for c in cs:
                out.append([(c[i] + gx + off + slant * c[i + 1], c[i + 1] + y) for i in range(0, len(c), 2)])
    return out


def _ink_box(contours: list) -> tuple[float, float, float, float] | None:
    xs = [x for c in contours for x, _ in c]
    ys = [y for c in contours for _, y in c]
    if not xs:
        return None
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _text_scale(size, w: float, h: float) -> tuple[float, float]:
    """mm per font unit across and up, for letters w x h font units in a part of `size`: a 0 follows the other."""
    ky = size[1] / h if size[1] > 0 else size[0] / w
    return (size[0] / w if size[0] > 0 else ky), ky


def _text_polygons(p: dict, font: dict, slant: float = 0.0):
    contours = _text_contours(p, font, slant)
    b = _ink_box(contours)
    if b is None:
        return None
    x0, y0, x1, y1 = b
    kx, ky = _text_scale(p["size"], x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return [np.array([((x - cx) * kx, (y - cy) * ky) for x, y in c], dtype=np.float64) for c in contours]


def _text_measure(p: dict) -> dict | None:
    """What a text part comes out as - the editor's textMeasure: width and height (mm), lines, its capitals' height
    and the font's name."""
    file, slant = _font_file(p)
    font = _font(file)
    b = _ink_box(_text_contours(p, font, slant))
    if b is None:
        return None
    w, h = b[2] - b[0], b[3] - b[1]
    kx, ky = _text_scale(p["size"], w, h)
    return {"width": w * kx, "height": h * ky, "lines": len(p.get("text", "").split("\n")),
            "capitals": font.get("capHeight", 0.7 * font["unitsPerEm"]) * ky, "font": font.get("name", file)}


def _rotation_matrix(r) -> np.ndarray:
    x, y, z = (math.radians(a) for a in r)
    rx = np.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return rz @ ry @ rx


def _place_mesh(v: np.ndarray, f: np.ndarray, part: dict) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = v.min(0), v.max(0)
    c = (lo + hi) / 2
    k = np.array(mesh_scale(part["size"], [float(hi[i] - lo[i]) for i in range(3)]))
    w = (v - c) * k
    if part.get("mirror"):
        w[:, 0] = -w[:, 0]
        f = f[:, [0, 2, 1]]
    w = w @ _rotation_matrix(part["rotate"]).T + np.array(part["at"])
    return w, f


# ---------------------------------------------------------------- a hole's lines (0.95.0)
# A fillet or chamfer on a hole shapes what it cuts. On its edges inside the part - a pocket's upright corners and
# floor edges - it rounds the pocket's inside; on its edges at an opening the cutter rounded there cuts less, so a
# lip is left round the opening. The editor rounds only the inside ones; `hub3d design` warns of the others.

_FACE_OUT = {"top": (0, 0, 1), "bottom": (0, 0, -1), "front": (0, -1, 0), "back": (0, 1, 0), "left": (-1, 0, 0),
             "right": (1, 0, 0)}
_FACE_AT = {"top": (2, 1), "bottom": (2, -1), "front": (1, -1), "back": (1, 1), "left": (0, -1), "right": (0, 1)}


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else np.zeros(3)


def _edge_ends(p: dict, edge: str):
    """A box's or wedge's edge, its two ends in its own frame - the editor's edgeSegments."""
    x, y, z = (s / 2 for s in p["size"])
    if p["shape"] == "box":
        lo, hi = np.array([-x, -y, -z]), np.array([x, y, z])
        a, b = lo.copy(), hi.copy()
        for face in edge.split("-"):
            axis, sign = _FACE_AT[face]
            a[axis] = b[axis] = hi[axis] if sign > 0 else lo[axis]
        return a, b
    pt = {"A": (-x, -y, -z), "B": (x, -y, -z), "C": (x, y, -z), "D": (-x, y, -z), "E": (-x, -y, z), "F": (-x, y, z)}
    ends = {"bottom-left": "AD", "bottom-front": "AB", "bottom-back": "DC", "bottom-slope": "BC", "left-front": "AE",
            "left-back": "DF", "left-slope": "EF", "front-slope": "EB", "back-slope": "FC"}[edge]
    return np.array(pt[ends[0]], dtype=np.float64), np.array(pt[ends[1]], dtype=np.float64)


def _in_outlines(polys, x: float, y: float) -> bool:
    """Inside outlines read EvenOdd: inside an odd number of them."""
    inside = False
    for poly in polys:
        n = len(poly)
        for i in range(n):
            (xi, yi), (xj, yj) = poly[i], poly[i - 1]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
    return inside


def _edge_outside(p: dict, edge: str, gap: float) -> list[np.ndarray]:
    """Points `gap` mm off a part's edge, out between the two faces it lies between: along each line of it, on the
    part and each copy, in the design - the editor's edgeOutside. One array a line."""
    sx, sy, sz = p["size"]
    local = []

    def along(a, b, out):
        d = _unit(out)
        return [a + (k / 8) * (b - a) + gap * d for k in range(1, 8)]

    if p["shape"] in ("box", "wedge"):
        ln = math.hypot(sx, sz) or 1.0
        f, g = (np.array((sz / ln, 0, sx / ln) if face == "slope" else _FACE_OUT[face], dtype=np.float64)
                for face in edge.split("-"))
        a, b = _edge_ends(p, edge)
        local.append(along(a, b, f + g))
    elif p["shape"] in ("cylinder", "cone"):
        k = 1.0 if edge == "bottom" else (p.get("top", 0.0) if p["shape"] == "cone" else 1.0)
        if k > 0:
            up = 1 if edge == "top" else -1
            ring = []
            for i in range(8):
                t = 2 * math.pi * (i + 0.5) / 8
                n = _unit((math.cos(t) / max(sx, 1e-9), math.sin(t) / max(sy, 1e-9), 0))
                d = _unit((n[0], n[1], up))
                ring.append(np.array([sx / 2 * k * math.cos(t), sy / 2 * k * math.sin(t), up * sz / 2]) + gap * d)
            local.append(ring)
    elif p["shape"] == "outline" and edge == "vertical":
        polys = _outline_polygons(p) or []
        for poly in polys:
            for i in range(len(poly)):
                c, a, b = poly[i], poly[i - 1], poly[(i + 1) % len(poly)]
                u, w = _unit((a[0] - c[0], a[1] - c[1], 0)), _unit((b[0] - c[0], b[1] - c[1], 0))
                d = _unit(u + w)                                  # into the corner's angle ...
                if not d[0] and not d[1]:
                    d = np.array([-u[1], u[0], 0.0])
                if _in_outlines(polys, c[0] + gap / 2 * d[0], c[1] + gap / 2 * d[1]):
                    d = -d                                        # ... or out of it
                local.append(along(np.array([c[0], c[1], -sz / 2]), np.array([c[0], c[1], sz / 2]), d))
    m = _rotation_matrix(p["rotate"])

    def place(v):
        w = np.array(v, dtype=np.float64)
        if p.get("mirror"):
            w[0] = -w[0]
        return m @ w + np.array(p["at"], dtype=np.float64)

    out = [np.array([place(v) for v in line]) for line in local]
    r = p.get("repeat")
    for i in range(1, r["count"] if r and r["count"] > 1 else 1):
        for line in local:
            pts = np.array([place(v) for v in line])
            if "step" in r:
                pts = pts + np.array(r["step"], dtype=np.float64) * i
            else:
                ax, ay = _repeat_around(p)
                t = math.radians(_repeat_step(r) * i)
                x, y = pts[:, 0] - ax, pts[:, 1] - ay
                pts = np.column_stack([ax + math.cos(t) * x - math.sin(t) * y, ay + math.sin(t) * x + math.cos(t) * y,
                                       pts[:, 2]])
            out.append(pts)
    return out


def _inside_solid(tri: np.ndarray, v) -> bool:
    """Whether a point is inside a closed mesh (its triangles, N x 3 x 3): rays along +X, +Y and +Z each cross it an
    odd number of times when it is - and the three vote, as the editor's insideMesh."""
    q = np.asarray(v, dtype=np.float64) + np.array([1.37e-5, 2.91e-5, 4.73e-5])
    votes = 0
    for axis in range(3):
        i, j = (axis + 1) % 3, (axis + 2) % 3
        ti, tj = tri[:, :, i] - q[i], tri[:, :, j] - q[j]
        near = ~((ti > 0).all(1) | (ti < 0).all(1) | (tj > 0).all(1) | (tj < 0).all(1))
        ti, tj, ta = ti[near], tj[near], tri[near][:, :, axis]
        wa = ti[:, 1] * tj[:, 2] - tj[:, 1] * ti[:, 2]
        wb = ti[:, 2] * tj[:, 0] - tj[:, 2] * ti[:, 0]
        wc = ti[:, 0] * tj[:, 1] - tj[:, 0] * ti[:, 1]
        hit = ((wa > 0) & (wb > 0) & (wc > 0)) | ((wa < 0) & (wb < 0) & (wc < 0))
        at = (wa * ta[:, 0] + wb * ta[:, 1] + wc * ta[:, 2])[hit] / (wa + wb + wc)[hit]
        votes += int((at > q[axis]).sum()) % 2
    return votes >= 2


def hole_edges(p: dict, edges, tri: np.ndarray) -> tuple[list[str], list[str]]:
    """A cutter's edges split: those inside the solid it cuts (solid just off the edge somewhere along each of its
    lines) and the open ones - the editor's holeEdges. `tri`: the built solid's triangles."""
    gap = min(0.2, 0.1 * min([2.0] + [s for s in p["size"] if s > 0]))
    inside, open_ = [], []
    for e in edges:
        ok = all(any(_inside_solid(tri, v) for v in line) for line in _edge_outside(p, e, gap))
        (inside if ok else open_).append(e)
    return inside, open_


def _hole_lips(parts: list, solid, under: bool = False, mesh: list | None = None) -> list[str]:
    """Holes (and parts of hole groups) with a fillet or chamfer on an edge at an opening: a lip round it."""
    out, mesh = [], mesh if mesh is not None else []
    for p in parts:
        if p.get("hidden"):
            continue
        cuts = under or bool(p.get("hole"))
        if p["shape"] == "group":
            out += _hole_lips(p.get("parts", []), solid, cuts, mesh)
            continue
        plan = _edge_plan(p) if cuts and (p.get("fillet") or p.get("chamfer")) and not p.get("radius") else {}
        if not plan:
            continue
        if not mesh:                                    # the solid's triangles, once
            m = solid.to_mesh()
            mesh.append(np.asarray(m.vert_properties, dtype=np.float64)[:, :3][np.asarray(m.tri_verts)])
        _, lips = hole_edges(p, list(plan), mesh[0])
        for kind in ("fillet", "chamfer"):
            these = [e for e in lips if plan[e][0] == kind]
            if these:
                out.append(f"hole {p['id']}: its {kind} on {', '.join(these)} is at an opening, where it leaves a lip "
                           f"round the opening - a hole's {kind} belongs on its edges inside the part (a pocket's "
                           "vertical and bottom)")
    return out


class DesignBuild:
    """What a design makes: `solid` (a Manifold, or None), `loose` meshes that are not closed solids [(part id,
    vertices, faces)], `problems`, and the design itself (`design`, with its `bed`). `cuts`: each list's solids and
    holes [(id, Manifold)] and what the holes left of them, for cut_warnings (0.104.0)."""

    def __init__(self, design: dict, solid, loose: list, problems: list[str], cuts: list | None = None):
        self.design, self.solid, self.loose, self.problems = design, solid, loose, problems
        self.cuts = cuts or []


CUTS_SAID = 3            # cut_warnings says at most this many


def _mm1(v: float) -> str:
    r = _round(v * 10) / 10
    return str(int(r)) if r == int(r) else repr(r)


def _box_of(m):
    return np.asarray(m.bounding_box(), dtype=np.float64).reshape(2, 3)


def cut_warnings(b: DesignBuild) -> list[str]:
    """Holes that cut a solid of their list wrongly (0.104.0): one that covers a solid right across - in two of X, Y
    and Z, or all three - and holes that leave a solid in more pieces than it was. Chat 335: a 40 mm hole on a mesh
    squeezed to 20 mm cut it right across, seven times, while the model moved the hole up and down - and at the
    mesh's real size, the same hole would have cut its wall through: two pieces."""
    out: list[str] = []
    axes = "XYZ"
    for solids, holes, cut in b.cuts:
        boxes = {sid: _box_of(s) for sid, s in solids}
        hit: dict[str, list[str]] = {}
        for hid, h in holes:
            hb = _box_of(h)
            for sid, s in solids:
                sb = boxes[sid]
                if np.any(hb[0] >= sb[1]) or np.any(hb[1] <= sb[0]):
                    continue                                    # their boxes do not meet
                hit.setdefault(sid, []).append(hid)
                covered = [i for i in range(3) if hb[0][i] <= sb[0][i] + 1e-3 and hb[1][i] >= sb[1][i] - 1e-3]
                if len(covered) < 2 or (h ^ s).volume() < 1e-6:
                    continue
                hs, ss = hb[1] - hb[0], sb[1] - sb[0]
                if len(covered) == 3:
                    out.append(f"hole {hid} is bigger than {sid} every way ({' x '.join(_mm1(x) for x in hs)} mm over "
                               f"its {' x '.join(_mm1(x) for x in ss)}): it cuts all of {sid} away - is one of their "
                               "sizes wrong?")
                    continue
                i = ({0, 1, 2} - set(covered)).pop()
                a, z = max(hb[0][i], sb[0][i]), min(hb[1][i], sb[1][i])
                out.append(f"hole {hid} is wider than {sid} in {' and '.join(axes[j] for j in covered)} "
                           f"({' x '.join(_mm1(hs[j]) for j in covered)} mm over its "
                           f"{' x '.join(_mm1(ss[j]) for j in covered)}): it cuts all of {sid} from "
                           f"{axes[i].lower()} {_mm1(a)} to {_mm1(z)} - right to cut it flat, but for a hole in it one "
                           "of their sizes is wrong")
        for sid, s in solids:                                   # one piece before, more after
            if sid not in hit:
                continue
            if len(solids) == 1:
                rest = cut                                      # the list's own cut: what its holes left of it
            else:
                rest = s
                for hid, h in holes:
                    if hid in hit[sid]:
                        rest = rest - h
            before, after = len(s.decompose()), rest.decompose()
            if len(after) > before:
                spans = sorted((_box_of(m)[:, 2] for m in after), key=lambda zz: zz[0])
                where = ", ".join(f"z {_mm1(lo)} to {_mm1(hi)}" for lo, hi in spans[:4]) + (" ..." if len(spans) > 4
                                                                                             else "")
                out.append(f"{' and '.join(f'hole {h}' for h in hit[sid])} cut{'s' if len(hit[sid]) == 1 else ''} "
                           f"{sid} into {len(after)} pieces ({where}) - if it should stay one piece, a hole goes too "
                           "far or is too wide")
    return out[:CUTS_SAID] + ([f"... and {len(out) - CUTS_SAID} more like it"] if len(out) > CUTS_SAID else [])


def build_design(design: dict, folder=".") -> DesignBuild:
    """The design's solid, as the editor builds it. Mesh parts' files are found from `folder` (the design file's)."""
    import manifold3d as m3d
    M = m3d.Manifold
    problems: list[str] = []
    loose: list = []
    base = Path(folder)

    def too_big(p: dict) -> None:
        problems.append(f"{p['id']}: a fillet or chamfer larger than its edges have room for was made smaller.")

    def prism(poly, u, v, length):
        """The polygon (u, v) extruded `length` along u x v, centred on 0,0,0 - the editor's prism."""
        e = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        m = m3d.CrossSection([np.array(poly, dtype=np.float64)], m3d.FillRule.NonZero).extrude(length)
        return m.transform(np.array([[u[0], v[0], e[0], -e[0] * length / 2], [u[1], v[1], e[1], -e[1] * length / 2],
                                     [u[2], v[2], e[2], -e[2] * length / 2]], dtype=np.float64))

    def edged(p: dict):
        """A part with its own edges rounded or cut (0.91.0) - the editor's edged, in its order."""
        all_round = _round_all(p)
        if all_round:
            sx, sy, sz = p["size"]
            r = min(all_round, min(sx, sy, sz) / 2 * 0.999)
            if r < all_round:
                too_big(p)
            balls = [M.sphere(r, 4 * _edge_quarter(p)).translate((a * (sx / 2 - r), b * (sy / 2 - r), c * (sz / 2 - r)))
                     for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)]
            return M.batch_hull(balls)
        if p["shape"] in ("cylinder", "cone"):
            poly, clamped, _ = _edge_profile(p)
            if clamped:
                too_big(p)
            m = m3d.CrossSection([np.array(poly, dtype=np.float64)], m3d.FillRule.NonZero).revolve(
                p.get("sides", DEFAULT_SIDES), 360.0)
            if abs(p["size"][1] - p["size"][0]) > 1e-9:
                m = m.scale((1.0, p["size"][1] / p["size"][0], 1.0))
            return m
        prisms, clamped, balls = _edge_prisms(p)
        if clamped:
            too_big(p)
        m = None
        for poly, u, v, length in prisms:
            one = prism(poly, u, v, length)
            m = done(m ^ one) if m is not None else one
        for centre, corner, r in balls:
            if m is None:
                break
            lo = [min(a, b) for a, b in zip(centre, corner)]
            hi = [max(a, b) for a, b in zip(centre, corner)]
            cube = M.cube((hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]), False).translate(tuple(lo))
            ball = M.sphere(r, 4 * _edge_quarter(p)).translate(tuple(centre))
            cut = done(m - cube)
            m = done(cut + done(ball ^ cube))
        return m

    def shape(p: dict):
        sx, sy, sz = p["size"]
        sides = p.get("sides", DEFAULT_SIDES)
        kind = p["shape"]
        if (p.get("fillet") or p.get("chamfer")) and not p.get("radius") and kind != "outline" and _edge_plan(p):
            return edged(p)
        if kind == "box":
            r = min(p.get("radius", 0.0), min(sx, sy, sz) / 2 * 0.999)
            if r <= 0:
                return M.cube((sx, sy, sz), True)
            balls = [M.sphere(r, sides).translate((a * (sx / 2 - r), b * (sy / 2 - r), c * (sz / 2 - r)))
                     for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)]
            return M.batch_hull(balls)
        if kind in ("cylinder", "cone"):
            top = 0.5 * p.get("top", 0.0) if kind == "cone" else 0.5
            return M.cylinder(1, 0.5, top, sides, True).scale((sx, sy, sz))
        if kind == "sphere":
            return M.sphere(0.5, sides).scale((sx, sy, sz))
        if kind == "wedge":
            x, y, z = sx / 2, sy / 2, sz / 2
            return M.hull_points(np.array([[-x, -y, -z], [x, -y, -z], [-x, y, -z], [x, y, -z], [-x, -y, z],
                                           [-x, y, z]], dtype=np.float64))
        if kind == "text":
            file, slant = _font_file(p)
            polys = _text_polygons(p, _font(file), slant)
            if polys is None:
                return None
            return m3d.CrossSection(polys, m3d.FillRule.NonZero).extrude(sz).translate((0, 0, -sz / 2))
        if kind == "outline":                       # EvenOdd: a hole is a hole whichever way round its points run
            polys = _outline_polygons(p)
            if polys is None:
                return None
            spec = _edge_plan(p).get("vertical")
            if spec:
                q, clamped, rounded = _edge_quarter(p), False, []
                for poly in polys:
                    pts, c, _ = _round_corners([(float(a), float(b)) for a, b in poly], [spec] * len(poly), q)
                    clamped = clamped or c
                    rounded.append(np.array(pts, dtype=np.float64))
                polys = rounded
                if clamped:
                    too_big(p)
            cs = m3d.CrossSection(polys, m3d.FillRule.EvenOdd)
            if cs.is_empty():
                return None
            return cs.extrude(sz).translate((0, 0, -sz / 2))
        if kind == "mesh":
            if not p.get("file"):
                problems.append(f"{p['id']}: no file")
                return None
            try:
                v, f, _ = _load(base / p["file"])
            except (OSError, ValueError) as exc:
                problems.append(f"{p['id']}: {exc}")
                return None
            vw, fw = _welded(v, f)
            fw = fw[(fw[:, 0] != fw[:, 1]) & (fw[:, 1] != fw[:, 2]) & (fw[:, 2] != fw[:, 0])]
            m = M(m3d.Mesh(vert_properties=np.ascontiguousarray(vw, dtype=np.float32),
                           tri_verts=np.ascontiguousarray(fw, dtype=np.uint32)))
            status = str(m.status()).split(".")[-1]
            if status != "NoError" or m.is_empty():
                problems.append(f"{p['id']}: {p['file']} is not a closed solid ({status}) - shown and exported as it "
                                "is; holes do not cut it.")
                loose.append((p["id"], *_place_mesh(vw, fw, p)))
                return None
            lo, hi = vw.min(0), vw.max(0)
            c = (lo + hi) / 2
            k = mesh_scale(p["size"], [float(hi[i] - lo[i]) for i in range(3)])     # its 0s from the file (0.104.0)
            return m.translate(tuple(float(x) for x in -c)).scale(tuple(k))
        return None

    def place(p: dict, m):
        if p.get("mirror"):
            m = m.mirror((1, 0, 0))
        if any(v != 0 for v in p["rotate"]):
            m = m.rotate(tuple(p["rotate"]))
        return m.translate(tuple(p["at"])).as_original()

    def done(m):
        """Every join and cut is worked out where it is made, as the editor does (0.88.0): manifold3d otherwise
        merges a join into the one that takes it in when nothing else holds it - and Python frees objects at once,
        the browser keeps them - so the same design came out of different triangles."""
        m.status()
        return m

    def repeated(p: dict, m):
        """The part and its copies, joined - in the editor's order, so the two build the same solid."""
        r = p["repeat"]
        copies = [m]
        for i in range(1, r["count"]):
            if "step" in r:
                copies.append(m.translate(tuple(s * i for s in r["step"])))
            else:
                ax, ay = _repeat_around(p)
                copies.append(m.translate((-ax, -ay, 0)).rotate((0, 0, _repeat_step(r) * i)).translate((ax, ay, 0)))
        return done(M.batch_boolean(copies, m3d.OpType.Add))

    cuts: list = []

    def level(parts: list):
        solids, holes, solid_ids, hole_ids = [], [], [], []
        for p in parts:
            if p.get("hidden"):
                continue
            if p["shape"] == "group":
                m = level(p.get("parts", []))
            else:
                s = shape(p)
                m = place(p, s) if s is not None else None
            if m is not None and p.get("repeat") and p["repeat"]["count"] > 1:
                m = repeated(p, m)
            if m is not None:
                (holes if p.get("hole") else solids).append(m)
                (hole_ids if p.get("hole") else solid_ids).append(p["id"])
        if not solids:
            return None
        out = solids[0] if len(solids) == 1 else done(M.batch_boolean(solids, m3d.OpType.Add))
        if holes:
            out = done(out - (holes[0] if len(holes) == 1 else done(M.batch_boolean(holes, m3d.OpType.Add))))
            cuts.append((list(zip(solid_ids, solids)), list(zip(hole_ids, holes)), out))   # looked at by cut_warnings
        return out

    solid = level(design["parts"])
    if solid is not None:
        status = str(solid.status()).split(".")[-1]
        if status != "NoError":
            problems.append(f"manifold3d: {status}")
    return DesignBuild(design, solid, loose, problems, cuts)


def design(path, quiet: bool = False):
    """The solid a design from the Hub's 3D editor (a .3d.json file) makes, as the editor shows and exports it. Mesh
    parts that are not closed solids are left out of it (said); save it, check it or draw it like any solid."""
    p = Path(path)
    d, problems = read_design(p.read_text(encoding="utf-8"), p.parent)
    b = build_design(d, p.parent)
    for msg in problems + b.problems:
        if not quiet:
            print(f"{p.name}: {msg}")
    if b.solid is None or b.solid.is_empty():
        raise NotPrintable(f"{p.name} makes nothing to print: no solid parts that are shown"
                           + (" (only meshes that are not closed solids)" if b.loose else ""))
    return b.solid


def _count_shapes(parts: list, into: dict) -> dict:
    for p in parts:
        if p.get("hidden"):
            continue
        if p["shape"] == "group":
            into["group"] = into.get("group", 0) + 1
            _count_shapes(p.get("parts", []), into)
            continue
        key = p["shape"] + (" hole" if p.get("hole") else "")
        into[key] = into.get(key, 0) + 1
    return into


def _text_lines(parts: list) -> list[str]:
    """What each shown text comes out as (0.89.0, chat 330: "letter height 20" was the height of two lines, and
    capitals 6.6 mm tall were too small for the plate - nothing said so)."""
    out = []
    for p in parts:
        if p.get("hidden"):
            continue
        if p["shape"] == "group":
            out += _text_lines(p.get("parts", []))
        elif p["shape"] == "text":
            m = _text_measure(p)
            if m:
                lines = f"{m['lines']} lines" if m["lines"] > 1 else "1 line"
                out.append(f"text {p['id']}: {lines} in {m['font']}, {m['width']:.1f} x {m['height']:.1f} x "
                           f"{p['size'][2]:.1f} mm - its capitals {m['capitals']:.1f} mm tall")
    return out


def _mesh_lines(parts: list, own) -> tuple[list[str], list[str]]:
    """What each shown mesh part comes out as against its file's own size, and a warning for one stretched (0.104.0,
    chat 335: a mesh without a size came out 20 x 20 x 20 mm and nothing said so)."""
    lines, warnings = [], []
    for p in parts:
        if p.get("hidden"):
            continue
        if p["shape"] == "group":
            more, warned = _mesh_lines(p.get("parts", []), own)
            lines += more
            warnings += warned
            continue
        mine = own(p["file"]) if p["shape"] == "mesh" and p.get("file") else None
        if not mine:
            continue
        k = mesh_scale(p["size"], mine)
        size = [mine[i] * k[i] for i in range(3)]
        said = f"mesh {p['id']}: {p['file']}, {size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} mm"
        own_box = " x ".join(_mm1(x) for x in mine)
        if max(k) > min(k) * 1.01:
            lines.append(f"{said} - stretched: {k[0]:.2f} x {k[1]:.2f} x {k[2]:.2f} of its own {own_box}")
            warnings.append(f"mesh {p['id']} is stretched to {k[0]:.2f} x {k[1]:.2f} x {k[2]:.2f} of {p['file']}'s own "
                            f"{own_box} mm - to keep its proportions give one size and 0s ([0, 0, {_mm1(size[2])}]), "
                            "or leave size out for its own size")
        elif abs(max(k) - 1) <= 1e-3 and abs(min(k) - 1) <= 1e-3:
            lines.append(f"{said} - its own size")
        else:
            lines.append(f"{said} - {sum(k) / 3:.3g} x its own size")
    return lines, warnings


def design_report(path, bed=None, stl=None, three_mf=None, png=None) -> int:
    """`hub3d design`: what a design makes, checked against its printer's bed; writes a picture, an STL or a 3MF."""
    p = Path(path)
    own = _own_sizes(p.parent)
    d, problems = read_design(p.read_text(encoding="utf-8"), own=own)
    b = build_design(d, p.parent)
    bed = bed or d.get("bed")
    counts = _count_shapes(d["parts"], {})
    said = ", ".join(f"{n} {k}" for k, n in counts.items()) or "no parts"
    where = ""
    if d.get("printer") or bed:
        where = "; laid out for " + (d.get("printer") or "a bed") + (f" ({_dims(_bed(bed))} mm)" if bed else "")
    print(f"{p.name}: {said}{where}")
    meshes, stretched = _mesh_lines(d["parts"], own)
    for line in _text_lines(d["parts"]) + meshes:
        print(line)
    for msg in problems + b.problems:
        print(f"- {msg}")
    cut = cut_warnings(b)
    models = ([b.solid] if b.solid is not None and not b.solid.is_empty() else []) + [(v, f) for _, v, f in b.loose]
    if not models:
        for w in stretched + cut:                       # a hole bigger than all it cuts leaves nothing: say why
            print(f"! {w}")
        print("It makes nothing to print.")
        return 1
    model = models[0] if len(models) == 1 else models
    report = check(model, bed)
    report.warnings += stretched
    if b.solid is not None and not b.solid.is_empty():
        report.warnings += _hole_lips(d["parts"], b.solid)
    report.warnings += cut
    low = report.lowest[2]
    if low < -0.01:              # a design is placed on its plate: the slicer moves it, so what lies there changes
        report.warnings.append(f"its lowest point is at z {low:.2f} mm, below the plate - the slicer lifts it, so "
                               "the bottom is not where the design has it: a part on the plate has at z = its "
                               "height / 2")
    elif low > 0.01:
        report.warnings.append(f"its lowest point is at z {low:.2f} mm, above the plate - the slicer drops it onto "
                               "the plate: let a part stand at z 0")
    print(report)
    if png:
        views(model, png)
    for out in (stl, three_mf):
        if out:
            save(model, out, bed=bed, strict=False)
    return 0 if report.ok and not b.loose else 1


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
            import json                                     # a design from the 3D editor, its text included
            Path("plate.3d.json").write_text(json.dumps({"parts": [
                {"id": "b", "shape": "box", "size": [30, 20, 4], "radius": 1},
                {"id": "t", "shape": "text", "text": "Hub æøå", "size": [0, 8, 1], "at": [0, 0, 4.4]},
                {"id": "h", "shape": "cylinder", "size": [5, 5, 10], "at": [10, 0, 2], "hole": True}]}))
            plate = design("plate.3d.json", quiet=True)
            expect(str(plate.status()).endswith("NoError") and len(plate.decompose()) == 1
                   and 2000 < plate.volume() < 2500, f"the design is not one plate with its letters: {plate.volume()}")
            index = _font_index()                                    # every font is in the image, and draws
            for fid in FONT_FAMILIES:
                for style in ("regular", "bold", "italic", "bold-italic"):
                    if style == "regular" or style in index["families"][fid]:
                        t = {"id": "t", "shape": "text", "text": "Aæ", "size": [0, 8, 1], "font": fid,
                             "bold": "bold" in style, "italic": "italic" in style}
                        m = _text_measure(t)
                        expect(m is not None and 2 < m["capitals"] < 8.01,          # 8: no overshoot (Oxanium)
                               f"the font {fid} {style} draws nothing")
            Path("tag.3d.json").write_text(DESIGN_EXAMPLE)          # hub3d format's example
            tag = check(design("tag.3d.json", quiet=True), bed=256)
            expect(tag.ok and tag.parts == 1 and not tag.warnings, f"hub3d format's example is not ready:\n{tag}")
            Path("snow.3d.json").write_text(DESIGN_SNOWFLAKE)              # ... and its snowflake (outlines, repeats)
            snow = check(design("snow.3d.json", quiet=True), bed=256)
            expect(snow.ok and snow.parts == 1 and not snow.warnings, f"hub3d format's snowflake is not ready:\n{snow}")
            Path("tree.3d.json").write_text(DESIGN_TREE)                   # ... and its tree (standing, sloped tiers)
            tree = check(design("tree.3d.json", quiet=True), bed=256)
            expect(tree.ok and tree.parts == 1 and not tree.warnings, f"hub3d format's tree is not ready:\n{tree}")
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
    dz = sub.add_parser("design", help="a design from the Hub's 3D editor (.3d.json): what it makes, checked; "
                        "--views, --stl and --3mf write it")
    dz.add_argument("file")
    dz.add_argument("--bed", help="the printer's bed in mm (default: the one in the design)")
    dz.add_argument("--views", metavar="OUT.png", help="a picture of it from three sides")
    dz.add_argument("--stl", metavar="OUT.stl")
    dz.add_argument("--3mf", dest="three_mf", metavar="OUT.3mf")
    sub.add_parser("format", help="the format of the 3D editor's designs (.3d.json), with an example")
    sub.add_parser("selftest", help="run the recipe's examples and check the results")
    args = ap.parse_args(argv)
    if args.cmd == "recipe":
        print(RECIPE, end="")
        return 0
    if args.cmd == "format":
        print(DESIGN_GUIDE, end="")
        return 0
    if args.cmd == "selftest":
        return selftest()
    try:
        if args.cmd == "check":
            report = check(args.file, args.bed)
            print(report)
            return 0 if report.ok else 1
        if args.cmd == "design":
            return design_report(args.file, args.bed, args.stl, args.three_mf, args.views)
        views(args.file, args.png, args.size)
        return 0
    except (OSError, ValueError, TypeError) as exc:              # NotPrintable is a ValueError
        print(f"hub3d {args.cmd}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
