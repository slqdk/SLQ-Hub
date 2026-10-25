"""Seeing, processing, generating and editing images."""
from __future__ import annotations

import io
import re
import shlex
import time
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from ...catalog import Catalog
from ...chats import store as chats
from ...projects import blobs
from ...projects.store import mime_of
from .base import Tool, ToolContext, ToolResult, gpu_failure, obj, register
from .shell import SandboxError, sandbox_exec

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def _image_bytes(ctx: ToolContext, ref: str) -> tuple[bytes, str]:
    """A path in the working folder, or an uploaded image's sha (in a person's chat: only one of its own)."""
    ref = (ref or "").strip()
    device = getattr(ctx.engine, "device", None)
    mine = device is None or ref in getattr(device, "shas", ())
    if re.fullmatch(r"[0-9a-f]{64}", ref) and blobs.exists(ref) and mine:
        return blobs.read(ref), ref[:12] + ".png"
    p = ctx.workdir.resolve(ref)
    if not p.is_file():
        raise FileNotFoundError(ref)
    return p.read_bytes(), p.name


def _shown(data: bytes, name: str) -> dict:
    up = chats.save_upload(data, name, mime_of(name) if Path(name).suffix else "image/png")
    return {"sha": up["sha"], "mime": up["mime"]}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "image"


async def view_image(ctx: ToolContext, args: dict) -> ToolResult:
    try:
        data, name = _image_bytes(ctx, args.get("path") or "")
        with Image.open(io.BytesIO(data)) as img:
            size = img.size
    except FileNotFoundError:
        return ToolResult(f"No such image: {args.get('path')}", is_error=True)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"Not an image the Hub can read: {exc}", is_error=True)
    return ToolResult(f"{args.get('path')} ({size[0]}x{size[1]} px) is shown below.", images=[_shown(data, name)])


async def image_transform(ctx: ToolContext, args: dict) -> ToolResult:
    src = args.get("path") or ""
    try:
        data, name = _image_bytes(ctx, src)
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except FileNotFoundError:
        return ToolResult(f"No such image: {src}", is_error=True)
    done = []
    for op in args.get("operations") or []:
        kind = op.get("op")
        try:
            if kind == "crop":
                if "box" in op:
                    l, t, r, b = [int(v) for v in op["box"]]
                else:
                    l, t = int(op.get("x", 0)), int(op.get("y", 0))
                    r, b = l + int(op["width"]), t + int(op["height"])
                img = img.crop((max(0, l), max(0, t), min(img.width, r), min(img.height, b)))
            elif kind == "resize":
                if op.get("max_side"):
                    img.thumbnail((int(op["max_side"]), int(op["max_side"])), Image.LANCZOS)
                else:
                    w = int(op.get("width") or img.width * int(op.get("height")) / img.height)
                    h = int(op.get("height") or img.height * w / img.width)
                    img = img.resize((max(1, w), max(1, h)), Image.LANCZOS)
            elif kind == "rotate":
                img = img.rotate(-float(op.get("degrees", 90)), expand=True)
            elif kind == "flip":
                img = ImageOps.mirror(img) if op.get("direction", "horizontal") == "horizontal" else ImageOps.flip(img)
            elif kind == "grayscale":
                img = ImageOps.grayscale(img)
            elif kind == "autocontrast":
                img = ImageOps.autocontrast(img.convert("RGB"))
            elif kind in ("brightness", "contrast", "color", "sharpness"):
                enh = {"brightness": ImageEnhance.Brightness, "contrast": ImageEnhance.Contrast,
                       "color": ImageEnhance.Color, "sharpness": ImageEnhance.Sharpness}[kind]
                img = enh(img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img).enhance(float(op.get("factor", 1.2)))
            elif kind == "blur":
                img = img.filter(ImageFilter.GaussianBlur(float(op.get("radius", 2))))
            elif kind == "sharpen":
                img = img.filter(ImageFilter.SHARPEN)
            elif kind == "pad":
                size = int(op.get("size", 1024))
                img = ImageOps.pad(img, (size, size), color=op.get("color", "white"))
            else:
                return ToolResult(f"unknown operation {kind!r}", is_error=True)
            done.append(kind)
        except (KeyError, ValueError, TypeError) as exc:
            return ToolResult(f"operation {kind}: {exc}", is_error=True)
    fmt = (args.get("format") or Path(args.get("output") or name).suffix.lstrip(".") or "png").lower()
    fmt = {"jpg": "jpeg"}.get(fmt, fmt)
    if fmt not in ("png", "jpeg", "webp", "gif", "bmp", "tiff"):
        return ToolResult(f"unsupported format {fmt}", is_error=True)
    out_rel = args.get("output") or f"{Path(name).stem}-edited.{'jpg' if fmt == 'jpeg' else fmt}"
    buf = io.BytesIO()
    save_img = img.convert("RGB") if fmt == "jpeg" and img.mode not in ("RGB", "L") else img
    save_img.save(buf, fmt.upper(), **({"quality": int(args.get("quality") or 90)} if fmt in ("jpeg", "webp") else {}))
    ctx.workdir.write(out_rel, buf.getvalue(), "agent", "hub", f"agent saved {out_rel}")
    return ToolResult(f"Saved {out_rel} ({img.width}x{img.height} px) after: {', '.join(done) or 'no changes'}.",
                      images=[_shown(buf.getvalue(), Path(out_rel).name)])


async def image_ocr(ctx: ToolContext, args: dict) -> ToolResult:
    rel = args.get("path") or ""
    p = ctx.workdir.resolve(rel)
    if not p.is_file():
        return ToolResult(f"No such image: {rel}", is_error=True)
    lang = re.sub(r"[^a-z+_]", "", (args.get("languages") or "dan+eng").lower()) or "eng"
    sandbox_file = f"{ctx.workdir.sandbox_path}/{ctx.workdir.rel(p)}"
    try:
        res = await sandbox_exec(ctx, f"tesseract {shlex.quote(sandbox_file)} - -l {lang} 2>/dev/null",
                                 ctx.workdir.sandbox_path, 120)
    except SandboxError as exc:
        return ToolResult(str(exc), is_error=True)
    text = (res.get("stdout") or "").strip()
    return ToolResult(f"OCR text of {rel} ({lang}):\n{text}" if text else f"No text found in {rel}.")


async def _progress(ctx: ToolContext):
    async def cb(value: int, maximum: int, node):
        ctx.emit("image_progress", id=ctx.call_id, value=value, max=maximum)
    return cb


def _save_outputs(ctx: ToolContext, items: list[dict], save_as: str | None, prompt: str) -> list[str]:
    paths = []
    if not ctx.project and not save_as:
        return paths
    for i, it in enumerate(items):
        if save_as:
            stem = Path(save_as)
            rel = str(stem) if len(items) == 1 else f"{stem.with_suffix('')}-{i + 1}{stem.suffix or '.png'}"
        else:
            rel = f"images/{time.strftime('%Y%m%d-%H%M%S')}-{_slug(prompt)}{'-' + str(i + 1) if len(items) > 1 else ''}.png"
        ctx.workdir.write(rel, blobs.read(it["sha"]), "agent", "hub", f"generated {rel}")
        paths.append(rel)
    return paths


def _pipeline(ctx: ToolContext):
    return ctx.hub.extras.get("images")


def _picture_model(ctx: ToolContext, args: dict) -> str | None:
    """The model asked for in the call, else the chat's, else the project's;
    None leaves it to Settings -> Assistant -> Images."""
    if args.get("model"):
        return args["model"]
    for owner in (ctx.chat, ctx.project):
        chosen = ((owner or {}).get("settings") or {}).get("image_model")
        if chosen:
            return chosen
    return None


def _other_model_note(ctx: ToolContext, args: dict, used: str) -> str:
    """Said in the result when the call named another model than the chat's: the user sees it, and so does the
    model (and a troubleshooting export)."""
    if not args.get("model"):
        return ""
    pipe = _pipeline(ctx)
    mine = pipe.chosen(_picture_model(ctx, {}))
    if used == mine or args["model"] == mine:
        return ""
    entry = pipe.entry(mine) or {}
    return f" The call asked for {args['model']}; this chat's picture model is {entry.get('name') or mine}."


async def image_generate(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipeline(ctx)
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult("prompt is required", is_error=True)
    from ...images.pipeline import ImageError
    try:
        items = await pipe.generate(prompt, int(args.get("width") or 1024), int(args.get("height") or 1024),
                                    count=int(args.get("count") or 1), seed=args.get("seed"),
                                    fast=bool(args.get("fast")), model=_picture_model(ctx, args),
                                    negative=args.get("negative") or "", ticket=ctx.ticket,
                                    on_progress=await _progress(ctx))
    except ImageError as exc:
        return gpu_failure("Image generation failed", exc)
    paths = _save_outputs(ctx, items, args.get("save_as"), prompt)
    desc = ", ".join(f"{it['width']}x{it['height']} seed {it['seed']}" for it in items)
    where = f" Saved as {', '.join(paths)}." if paths else ""
    used = items[0].get("prompt_used")
    enhanced = f"\nERNIE-Image's prompt enhancer turned the prompt into: {used[:2000]}" if used else ""
    other = _other_model_note(ctx, args, items[0]["model"])
    used_by = (pipe.entry(items[0]["model"]) or {}).get("name") or items[0]["model"]      # the owner's name for it
    return ToolResult(f"Generated {len(items)} image(s) with {used_by} ({desc}). They are shown to the "
                      f"user and below.{other}{where}{enhanced}",
                      images=[{"sha": it["sha"], "mime": "image/png"} for it in items],
                      display={"generated": [{"sha": it["sha"], "seed": it["seed"], "path": p}
                                             for it, p in zip(items, paths + [None] * len(items))]})


async def image_edit(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipeline(ctx)
    refs = args.get("images") or ([args["image"]] if args.get("image") else [])
    if not refs:
        return ToolResult("give at least one image (path in the working folder)", is_error=True)
    try:
        datas = [_image_bytes(ctx, r)[0] for r in refs]
    except FileNotFoundError as exc:
        return ToolResult(f"No such image: {exc}", is_error=True)
    from ...images.pipeline import ImageError
    try:
        items = await pipe.edit(args.get("prompt") or "", datas, seed=args.get("seed"), fast=bool(args.get("fast")),
                                model=_picture_model(ctx, args), ticket=ctx.ticket, on_progress=await _progress(ctx),
                                count=int(args.get("count") or 1))
    except ImageError as exc:
        return gpu_failure("Image editing failed", exc)
    paths = _save_outputs(ctx, items, args.get("save_as"), args.get("prompt") or "edit")
    where = f" Saved as {', '.join(paths)}." if paths else ""
    return ToolResult(f"Edited image(s) ready ({len(items)}, {_size_note(items[0])}).{where}",
                      images=[{"sha": it["sha"], "mime": "image/png"} for it in items],
                      display={"generated": [{"sha": it["sha"], "seed": it["seed"], "path": p}
                                             for it, p in zip(items, paths + [None] * len(items))]})


async def image_remove_background(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipeline(ctx)
    try:
        data, name = _image_bytes(ctx, args.get("path") or "")
    except FileNotFoundError:
        return ToolResult(f"No such image: {args.get('path')}", is_error=True)
    from ...images.pipeline import ImageError
    try:
        items = await pipe.remove_background(data, ticket=ctx.ticket, on_progress=await _progress(ctx))
    except ImageError as exc:
        return gpu_failure("Background removal failed", exc)
    save_as = args.get("save_as") or f"{Path(name).stem}-cutout.png"
    ctx.workdir.write(save_as, blobs.read(items[0]["sha"]), "agent", "hub", f"cut out {save_as}")
    return ToolResult(f"Background removed; saved as {save_as} (PNG with transparency, {_size_note(items[0])}).",
                      images=[{"sha": items[0]["sha"], "mime": "image/png"}],
                      display={"generated": [{"sha": items[0]["sha"], "path": save_as}]})


def _size_note(item: dict) -> str:
    """The size of an edit or a cut-out, against the original's (the pipeline scales it back up to 2048x2048)."""
    w, h = item.get("width"), item.get("height")
    src = tuple(item.get("source") or ())
    if not w or len(src) != 2 or (w, h) == src:
        return f"{w}x{h} px, the original's size" if w else "size unknown"
    return f"{w}x{h} px - the original is {src[0]}x{src[1]}; pictures that big stay at the model's size"


def _images_ready(ctx: ToolContext) -> bool:
    pipe = ctx.hub.extras.get("images")
    return bool(pipe and pipe.available())                  # any model that makes pictures


def _edit_ready(ctx: ToolContext) -> bool:
    pipe = ctx.hub.extras.get("images")
    return bool(pipe and pipe.installed("edit"))


def _cutout_ready(ctx: ToolContext) -> bool:
    pipe = ctx.hub.extras.get("images")
    return bool(pipe and pipe.available("qwen-image-2.1"))


register(Tool("view_image", "Look at an image from the working folder (or an attached image by its id).",
              obj({"path": {"type": "string"}}, ["path"]), view_image, group="images", read_only=True))
register(Tool("image_transform", "Process an image without AI: crop, resize, rotate, flip, grayscale, "
              "autocontrast, brightness/contrast/color/sharpness (factor), blur, sharpen, pad to a square; and "
              "convert/compress (format png/jpeg/webp, quality). Saves the result in the working folder.",
              obj({"path": {"type": "string"},
                   "operations": {"type": "array", "items": {"type": "object"},
                                  "description": "e.g. [{\"op\":\"crop\",\"x\":0,\"y\":0,\"width\":500,\"height\":400},"
                                                 " {\"op\":\"resize\",\"max_side\":1024}]"},
                   "output": {"type": "string", "description": "Output path (default <name>-edited.<ext>)"},
                   "format": {"type": "string"}, "quality": {"type": "integer"}}, ["path"]),
              image_transform, group="images"))
register(Tool("image_ocr", "Read the text in an image with Tesseract OCR (default languages dan+eng). For "
              "photos and screenshots your own vision is often better; use OCR for long printed text.",
              obj({"path": {"type": "string"}, "languages": {"type": "string"}}, ["path"]), image_ocr, group="images",
              read_only=True))
def _generate_params(models: list[str]) -> dict:
    model: dict = {"type": "string", "enum": models} if models else {"type": "string"}
    return obj({"prompt": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"},
                "count": {"type": "integer", "description": "1-4, default 1. More only when the user asks for "
                                                             "several pictures or for options."},
                "seed": {"type": "integer"},
                "fast": {"type": "boolean"}, "negative": {"type": "string"}, "model": model,
                "save_as": {"type": "string", "description": "Path in the working folder"}}, ["prompt"])


def _generate_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    """The downloaded picture models, the chat's own first, each with how to
    write its prompt (tags for SDXL anime models, sentences for the rest)."""
    from ...images.pipeline import prompt_hint
    pipe = _pipeline(ctx)
    gens = pipe.installed("generate")
    mine = pipe.chosen(_picture_model(ctx, {}))
    chosen = next((e for e in gens if e["id"] == mine), gens[0] if gens else None)
    if chosen is None:
        return GENERATE_TEXT, _generate_params([])
    lines = [f"- {e['id']}: {Catalog.label(e)}{' (this chat uses it)' if e is chosen else ''}. {prompt_hint(e)}"
             for e in gens]
    return (f"Generate images from a text prompt. Without model, this chat uses {chosen['name']}; pick another "
            "only when the user asks for it. Write the prompt the way the model wants it:\n" + "\n".join(lines) +
            "\nTakes 15-90 s per image (longer when the model has to load first).",
            _generate_params([e["id"] for e in gens]))


def _edit_params(models: list[str]) -> dict:
    model: dict = {"type": "string", "enum": models} if models else {"type": "string"}
    return obj({"prompt": {"type": "string"}, "images": {"type": "array", "items": {"type": "string"}},
                "seed": {"type": "integer"}, "fast": {"type": "boolean"}, "count": {"type": "integer"},
                "model": model, "save_as": {"type": "string"}}, ["prompt", "images"])


def _edit_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    eds = _pipeline(ctx).installed("edit")
    names = " or ".join(Catalog.label(e) for e in eds) or "Qwen-Image 2.1"
    return EDIT_TEXT.replace("(Qwen-Image 2.1)", f"({names})"), _edit_params([e["id"] for e in eds])


GENERATE_TEXT = ("Generate images from a text prompt with the model chosen for this chat (Settings -> Assistant -> "
                 "Images by default). Write a detailed English prompt: subject, style, composition, lighting, any "
                 "exact text in quotes. Takes 15-90 s per image.")
EDIT_TEXT = ("Edit images with an instruction (Qwen-Image 2.1): change, add or remove things, restyle, combine up to "
             "10 reference images ('put the person from image 1 into the scene of image 2'). The output keeps the "
             "first image's proportions.")

register(Tool("image_generate", GENERATE_TEXT, _generate_params(["qwen-image-2.1"]), image_generate,
              available=_images_ready, in_subagent=False, group="images", dynamic=_generate_dynamic))
register(Tool("image_edit", EDIT_TEXT, _edit_params(["qwen-image-2.1", "flux2-klein-4b"]), image_edit,
              available=_edit_ready, in_subagent=False, group="images", dynamic=_edit_dynamic))
register(Tool("image_remove_background", "Cut out the subject of an image: a PNG with a transparent background.",
              obj({"path": {"type": "string"}, "save_as": {"type": "string"}}, ["path"]),
              image_remove_background, available=_cutout_ready, in_subagent=False, group="images"))
