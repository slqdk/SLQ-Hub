"""A generated 3D model made ready to print (0.67.0): the GLB ComfyUI saved (glTF: Y up, no unit) turned Z up as
slicers have it, scaled to millimetres, standing on the bed, saved as STL - and checked: closed (watertight), its
size, volume and how many separate parts. A picture of it from three sides goes with it, drawn here (numpy and
Pillow), so the assistant and the user see what came out before printing."""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image, ImageDraw

AXES = ("longest", "height", "width", "depth")


class MeshError(ValueError):
    pass


def load(data: bytes, file_type: str = "glb"):
    """One mesh from a GLB/STL/OBJ/PLY file (a scene's meshes joined)."""
    import trimesh
    try:
        loaded = trimesh.load(io.BytesIO(data), file_type=file_type, force="mesh", process=False)
    except Exception as exc:  # noqa: BLE001 - trimesh raises many kinds
        raise MeshError(f"the 3D file could not be read ({exc.__class__.__name__}: {str(exc)[:200]})")
    if not hasattr(loaded, "faces") or len(loaded.faces) == 0:
        raise MeshError("the 3D file has no triangles")
    return loaded


def bodies(faces: np.ndarray, n_vertices: int) -> int:
    """How many separate parts: groups of triangles that share vertices. Each vertex takes the smallest label of
    its triangles, labels follow their labels (pointer jumping) until nothing changes - numpy only."""
    faces = np.asarray(faces, dtype=np.int64)
    if not len(faces):
        return 0
    lab = np.arange(n_vertices, dtype=np.int64)
    flat = faces.reshape(-1)
    for _ in range(10_000):
        m = np.repeat(lab[faces].min(1), 3)
        new = lab.copy()
        np.minimum.at(new, flat, m)
        np.minimum.at(new, lab[flat], m)                  # the old label's owner too, so groups merge
        new = new[new]
        new = new[new]
        if np.array_equal(new, lab):
            break
        lab = new
    return int(len(np.unique(lab[np.unique(flat)])))


def for_print(mesh, size_mm: float, along: str = "longest", y_up: bool = True) -> tuple[bytes, dict]:
    """(binary STL, facts): Z up, `size_mm` along the axis asked (the longest side by default), centred on the bed
    with its lowest point at 0."""
    import trimesh
    if not (0.5 <= float(size_mm) <= 2000):
        raise MeshError("size_mm must be between 0.5 and 2000")
    m = trimesh.Trimesh(vertices=np.asarray(mesh.vertices, dtype=np.float64), faces=np.asarray(mesh.faces),
                        process=True)                     # joins duplicate vertices, drops degenerate triangles
    if y_up:                                              # glTF's Y up -> Z up, front (+Z) -> towards -Y
        m.apply_transform(np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.float64))
    ext = m.extents
    axis = {"width": 0, "depth": 1, "height": 2}.get(along)
    span = float(ext[axis]) if axis is not None else float(max(ext))
    if span <= 0:
        raise MeshError("the model is flat or empty")
    m.apply_scale(float(size_mm) / span)
    lo, hi = m.bounds
    m.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    watertight = bool(m.is_watertight)
    facts = {"triangles": int(len(m.faces)), "watertight": watertight,
             "winding": bool(m.is_winding_consistent),
             "size_mm": [round(float(x), 1) for x in m.extents],
             "volume_cm3": round(abs(float(m.volume)) / 1000, 1) if watertight else None,
             "bodies": bodies(np.asarray(m.faces), len(m.vertices))}
    return m.export(file_type="stl"), {**facts, "mesh": m}


# ------------------------------------------------------------------ the picture: three views, shaded like clay
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


def views(mesh, size: int = 400, max_faces: int = 400_000) -> bytes:
    """A PNG: the model from the front, from the side and from above at an angle, with its size under it."""
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces, dtype=np.int64)
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
