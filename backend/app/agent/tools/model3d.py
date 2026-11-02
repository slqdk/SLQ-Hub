"""A 3D model from a picture (0.67.0): TRELLIS.2 on the card makes the shape, the Hub makes it printable - an STL in
millimetres, Z up, standing on the bed - checks it, and draws it from three sides, so the assistant looks before it
says how it turned out."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ...chats import store as chats
from .audio import _progress, slug
from .base import Tool, ToolContext, ToolResult, gpu_failure, obj, register
from .images import _image_bytes

TEXT = ("Make a 3D model to print from one picture of a thing (TRELLIS.2 on the GPU, a few minutes): an STL in "
        "millimetres, Z up, standing on the bed, plus the GLB as made and a picture of it from three sides. A "
        "picture has no scale: give size_mm (and along which side) from what the user said, or ask. One object on "
        "a plain background works best; the background is cut away. The shape is guessed where the picture does not "
        "show it (the back). Look at the views (view_image) before you say how it turned out.")


def _pipe(ctx: ToolContext):
    return ctx.hub.extras.get("model3d")


def _ready(ctx: ToolContext) -> bool:
    pipe = _pipe(ctx)
    return bool(pipe and pipe.available())


def _base(ctx: ToolContext, save_as: str | None, stem: str) -> str:
    """The files' path without extension: save_as (a folder gets a name from the picture), else in a project
    under models3d/, in a chat's own folder at the top; a free name either way."""
    raw = (save_as or "").strip()
    if raw:
        p = ctx.workdir.resolve(raw)
        rel = ctx.workdir.rel(p)
        if raw.endswith("/") or p.is_dir():
            base = stem if rel == "." else f"{rel}/{stem}"
        else:
            return str(Path(rel).with_suffix(""))
    else:
        base = f"{ctx.workdir.own}models3d/{stem}" if ctx.project else stem
    out, n = base, 2
    while any(ctx.workdir.resolve(f"{out}{ext}").exists() for ext in (".stl", ".glb")):
        out, n = f"{base}-{n}", n + 1
    return out


async def model3d_generate(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipe(ctx)
    ref = str(args.get("image") or "").strip()
    if not ref:
        return ToolResult("image is required: the picture of the thing, in the working folder", is_error=True)
    try:
        size = float(args.get("size_mm") if args.get("size_mm") not in (None, "") else 100)
    except (TypeError, ValueError):
        return ToolResult("size_mm must be a number (millimetres)", is_error=True)
    along = str(args.get("along") or "longest").lower()
    from ...model3d.mesh import AXES, MeshError, for_print, load, views
    from ...model3d.pipeline import Model3DError
    if along not in AXES:
        return ToolResult(f"along must be one of {', '.join(AXES)}", is_error=True)
    detail = str(args.get("detail") or "standard").lower()
    try:
        data, name = _image_bytes(ctx, ref)
    except FileNotFoundError:
        return ToolResult(f"No such picture: {ref}", is_error=True)
    from ...images.pipeline import EPHEMERAL
    try:
        out = await pipe.make(data, detail=detail, seed=args.get("seed"), ticket=ctx.ticket,
                              on_progress=await _progress(ctx, "image_progress"), keep=not EPHEMERAL.get())
    except Model3DError as exc:
        return gpu_failure("The 3D model failed", exc)
    try:
        stl, facts = await asyncio.to_thread(lambda: for_print(load(out["glb"]), size, along))
        picture = await asyncio.to_thread(views, facts["mesh"])
    except MeshError as exc:
        return ToolResult(f"TRELLIS.2 made a model, but it could not be made printable: {exc}", is_error=True)
    try:
        base = _base(ctx, args.get("save_as"), slug(Path(name).stem, fallback="model"))
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    for ext, blob in ((".stl", stl), (".glb", out["glb"]), ("-views.png", picture)):
        ctx.workdir.write(f"{base}{ext}", blob, "agent", "hub", f"made {base}{ext}")
    up = chats.save_upload(picture, Path(f"{base}-views.png").name, "image/png")
    w, d, h = facts["size_mm"]
    checks = ["closed (watertight)" if facts["watertight"] else "NOT closed: it has holes - a slicer may repair it, "
              "or make it again with another seed"]
    if facts["bodies"] > 1:
        checks.append(f"{facts['bodies']} separate parts")
    volume = f", {facts['volume_cm3']:g} cm3" if facts.get("volume_cm3") is not None else ""
    given = "" if args.get("size_mm") not in (None, "") else (" No size was given, so it is 100 mm on its longest "
                                                               "side - ask the user for the real size.")
    return ToolResult(
        f"Made a 3D model of {ref} with {out['model']['name']} ({out['detail']} detail, seed {out['seed']}, "
        f"{out['compute']:.0f} s on the GPU): {base}.stl - {w:g} x {d:g} x {h:g} mm (width x depth x height), Z up, "
        f"standing on the bed; {facts['triangles']:,} triangles, {', '.join(checks)}{volume}. {base}.glb is the model "
        f"as made (glTF, Y up) for 3D viewers; {base}-views.png shows it from the front, the side and above - look "
        f"at it before you say how it turned out.{given}",
        images=[{"sha": up["sha"], "mime": "image/png"}],
        display={"generated": [{"sha": up["sha"], "path": f"{base}-views.png"}],
                 "made_with": {"model": out["model"]["name"], "loras": [], "left_out": []}})


def _params() -> dict:
    return obj({"image": {"type": "string", "description": "The picture of the thing (path in the working folder)."},
                "size_mm": {"type": "number", "description": "Its real size in millimetres along `along` (default "
                                                             "100 on the longest side)."},
                "along": {"type": "string", "enum": ["longest", "height", "width", "depth"]},
                "detail": {"type": "string", "enum": ["standard", "high"],
                           "description": "high: a finer shape, more GPU memory and time."},
                "seed": {"type": "integer"},
                "save_as": {"type": "string", "description": "Path in the working folder, without extension."}},
               ["image"])


register(Tool("model3d_generate", TEXT, _params(), model3d_generate, available=_ready, in_subagent=False,
              group="images"))
