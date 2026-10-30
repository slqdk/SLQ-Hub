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
    """Each picture as a file in the working folder - images/<time>-<prompt>.png, or save_as - in a plain chat too
    (0.32.1): one with no file name could not be changed again, so the next "change it" edited the user's original.
    Not in a person's chat unless asked: its files go to the device and back with every message."""
    paths = []
    if getattr(ctx.engine, "device", None) is not None and not save_as:
        return paths
    for i, it in enumerate(items):
        if save_as:
            stem = Path(save_as)
            rel = str(stem) if len(items) == 1 else f"{stem.with_suffix('')}-{i + 1}{stem.suffix or '.png'}"
        else:
            rel = (f"{ctx.workdir.own}images/{time.strftime('%Y%m%d-%H%M%S')}-{_slug(prompt)}"
                   f"{'-' + str(i + 1) if len(items) > 1 else ''}.png")
        ctx.workdir.write(rel, blobs.read(it["sha"]), "agent", "hub", f"generated {rel}")
        paths.append(rel)
    return paths


def _saved_text(paths: list[str]) -> str:
    """Where the pictures went, and that a further change starts from them."""
    if not paths:
        return ""
    which = "that file" if len(paths) == 1 else "one of those files"
    return f" Saved as {', '.join(paths)} - to change a picture further, edit {which}."


def _pipeline(ctx: ToolContext):
    return ctx.hub.extras.get("images")


def _picture_model(ctx: ToolContext) -> str | None:
    """The chat's picture model (its picture menu), else the project's; None leaves it to Settings -> Assistant ->
    Images. A call cannot pick another (0.59.0): the model copied an earlier call's model and LoRAs, so the menu
    showed one thing and another was used."""
    for owner in (ctx.chat, ctx.project):
        chosen = ((owner or {}).get("settings") or {}).get("image_model")
        if chosen:
            return chosen
    return None


def _admins_turn(ctx: ToolContext) -> bool:
    """LoRAs are the admin's alone (0.49.0): never in a person's chat (their device's turn)."""
    return getattr(ctx.engine, "device", None) is None


def _strength(value) -> float | None:
    """A chat's strength for a LoRA (its picture menu's slider), or None for the LoRA's own. The LoRA's own range
    (0-2, a slider -10 to 10) is applied when it is used."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if -10.0 <= v <= 10.0 else None


def _loras(ctx: ToolContext) -> list[dict]:
    """The LoRAs ticked in the chat's picture menu, each with the chat's strength (its slider) or None for the
    LoRA's own. A call cannot add one (0.59.0). None in a person's chat."""
    if not _admins_turn(ctx):
        return []
    settings = (ctx.chat or {}).get("settings") or {}
    strengths = settings.get("image_lora_strength") or {}
    if not isinstance(strengths, dict):
        strengths = {}
    return [{"id": x, "strength": _strength(strengths.get(x))} for x in (settings.get("image_loras") or [])
            if isinstance(x, str)]


def _lora_text(item: dict) -> str:
    used = f" with the LoRA{'s' if len(item.get('loras') or []) > 1 else ''} {', '.join(item['loras'])}" \
        if item.get("loras") else ""
    notes = f" ({'; '.join(item['lora_notes'])})" if item.get("lora_notes") else ""
    return used + notes


def _ignored(args: dict) -> str:
    """For the model, when its call named a model or LoRAs anyway (copied from an earlier call): not used."""
    named = [label for key, label in (("model", "model"), ("loras", "LoRAs")) if args.get(key)]
    if not named:
        return ""
    return (f" The call's {' and '.join(named)} {'were' if len(named) > 1 else 'was'} not used: the picture model and "
            "the LoRAs are chosen in this chat's picture menu, beside the message box - if the user wants others, "
            "tell them to pick them there.")


def _made_with(pipe, item: dict, ctx: ToolContext | None = None) -> dict:
    """The line under the picture (0.59.0): the model it was made with, the LoRAs used (with their strength) and
    any left out, so the user sees what was used instead of guessing. For an edit (ctx given) also why another
    model than the chat's made it: one that only makes new pictures (SDXL, Z-Image, Chroma, ERNIE) cannot change
    one."""
    name = (pipe.entry(item["model"]) or {}).get("name") or item["model"]
    out = {"model": name, "loras": item.get("lora_used") or [], "left_out": item.get("lora_notes") or []}
    if ctx is not None:
        from ...images.pipeline import family
        mine = pipe.entry(pipe.chosen(_picture_model(ctx))) or {}
        if mine and mine.get("id") != item["model"] and family(mine) != family(pipe.entry(item["model"])):
            out["note"] = (f"{mine.get('name') or mine['id']} only makes new pictures from a description, so {name} "
                           "made this change - ask for a new picture, without one attached, to use it")
    return out


async def image_generate(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipeline(ctx)
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult("prompt is required", is_error=True)
    from ...images.pipeline import ImageError
    try:
        items = await pipe.generate(prompt, int(args.get("width") or 1024), int(args.get("height") or 1024),
                                    count=int(args.get("count") or 1), seed=args.get("seed"),
                                    fast=bool(args.get("fast")), model=_picture_model(ctx),
                                    negative=args.get("negative") or "", ticket=ctx.ticket,
                                    on_progress=await _progress(ctx), loras=_loras(ctx))
    except ImageError as exc:
        return gpu_failure("Image generation failed", exc)
    paths = _save_outputs(ctx, items, args.get("save_as"), prompt)
    desc = ", ".join(f"{it['width']}x{it['height']} seed {it['seed']}" for it in items)
    where = _saved_text(paths)
    used = items[0].get("prompt_used")
    enhanced = f"\nERNIE-Image's prompt enhancer turned the prompt into: {used[:2000]}" if used else ""
    made = _made_with(pipe, items[0])                                       # the owner's name for the model
    return ToolResult(f"Generated {len(items)} image(s) with {made['model']}{_lora_text(items[0])} ({desc}). They "
                      f"are shown to the user and below, with a line saying what made them.{_ignored(args)}{where}"
                      f"{enhanced}",
                      images=[{"sha": it["sha"], "mime": "image/png"} for it in items],
                      display={"generated": [{"sha": it["sha"], "seed": it["seed"], "path": p}
                                             for it, p in zip(items, paths + [None] * len(items))],
                               "made_with": made})


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
                                model=_picture_model(ctx), ticket=ctx.ticket, on_progress=await _progress(ctx),
                                count=int(args.get("count") or 1), loras=_loras(ctx))
    except ImageError as exc:
        return gpu_failure("Image editing failed", exc)
    paths = _save_outputs(ctx, items, args.get("save_as"), args.get("prompt") or "edit")
    where = _saved_text(paths)
    made = _made_with(pipe, items[0], ctx)
    why = f" {made['note'][0].upper()}{made['note'][1:]}." if made.get("note") else ""
    return ToolResult(f"Edited image(s) ready with {made['model']} ({len(items)}, {_size_note(items[0])})"
                      f"{_lora_text(items[0])}.{why}{_ignored(args)}{where}",
                      images=[{"sha": it["sha"], "mime": "image/png"} for it in items],
                      display={"generated": [{"sha": it["sha"], "seed": it["seed"], "path": p}
                                             for it, p in zip(items, paths + [None] * len(items))],
                               "made_with": made})


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
def _generate_params() -> dict:
    """No model and no LoRAs (0.59.0): the chat's picture menu chooses them."""
    params = {"prompt": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"},
              "count": {"type": "integer", "description": "1-4, default 1. More only when the user asks for "
                                                           "several pictures or for options."},
              "seed": {"type": "integer"},
              "fast": {"type": "boolean"}, "negative": {"type": "string"},
              "save_as": {"type": "string", "description": "Path in the working folder"}}
    return obj(params, ["prompt"])


def _lora_lines(ctx: ToolContext, entry: dict | None) -> str:
    """The admin's LoRAs for the tool's description (0.49.0) - nothing in a person's chat. Since 0.59.0 the user
    switches them on in the chat's picture menu; the model only learns which are on, and whether they fit the
    picture model (`entry`)."""
    from ...images.pipeline import family
    store = ctx.hub.extras.get("loras")
    if store is None or not _admins_turn(ctx):
        return ""
    items = store.list()
    if not items:
        return ""
    mine = set(((ctx.chat or {}).get("settings") or {}).get("image_loras") or [])
    fam = family(entry) if entry else ""
    on, off = [], []
    for x in items:
        bits = [f"for {x.get('base_label') or 'an unknown family of'} models"]
        if x.get("slider"):
            bits.append("a slider LoRA")
        if x.get("trigger"):
            bits.append(f'trigger "{x["trigger"]}" (put in the prompt for you)')
        if x["id"] in mine and x.get("base") != fam:
            bits.append("NOT used: it does not fit this chat's picture model")
        line = f"- {x['name']} ({'; '.join(bits)}).{(' ' + x['note']) if x.get('note') else ''}"
        (on if x["id"] in mine else off).append(line)
    text = ("\nLoRAs - add-ons that teach a model a person, a thing or a style; one works only with models of its "
            "family. The user switches them on, and sets their strength, in this chat's picture menu beside the "
            "message box; a call cannot add one.")
    if on:
        text += "\nOn in this chat, used on every picture:\n" + "\n".join(on)
    if off:
        text += "\nOff - if the user asks for one, tell them to tick it in the picture menu:\n" + "\n".join(off)
    return text


def _generate_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    """The downloaded picture models, the chat's own first, each with how to
    write its prompt (tags for SDXL anime models, sentences for the rest)."""
    from ...images.pipeline import prompt_hint
    pipe = _pipeline(ctx)
    gens = pipe.installed("generate")
    mine = pipe.chosen(_picture_model(ctx))
    chosen = next((e for e in gens if e["id"] == mine), gens[0] if gens else None)
    if chosen is None:
        return GENERATE_TEXT, _generate_params()
    return (f"Generate images from a text prompt with {Catalog.label(chosen)}, this chat's picture model. The user "
            "chooses it in the chat's picture menu beside the message box; a call cannot pick another, so if the "
            "user asks for another model, tell them to choose it there. Write the prompt the way this model wants "
            f"it: {prompt_hint(chosen)}\nTakes 15-90 s per image (longer when the model has to load first)."
            + _lora_lines(ctx, chosen), _generate_params())


def _edit_params() -> dict:
    params = {"prompt": {"type": "string"}, "images": {"type": "array", "items": {"type": "string"}},
              "seed": {"type": "integer"}, "fast": {"type": "boolean"}, "count": {"type": "integer"},
              "save_as": {"type": "string"}}
    return obj(params, ["prompt", "images"])


def _edit_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    """The chat's picture model edits when it can; one that only makes new pictures leaves it to the first edit
    model (as the pipeline does)."""
    from ...images.pipeline import abilities
    pipe = _pipeline(ctx)
    eds = pipe.installed("edit")
    mine = pipe.entry(pipe.chosen(_picture_model(ctx)))
    editor = mine if mine and "edit" in abilities(mine) else (eds[0] if eds else None)
    who = Catalog.label(editor) if editor else "Qwen-Image 2.1"
    if editor is not None and editor is not mine and mine:
        who += f" - this chat's {Catalog.label(mine)} only makes new pictures"
    return EDIT_TEXT.replace("(Qwen-Image 2.1)", f"({who})") + _lora_lines(ctx, editor), _edit_params()


GENERATE_TEXT = ("Generate images from a text prompt with the model chosen for this chat (Settings -> Assistant -> "
                 "Images by default). Write a detailed English prompt: subject, style, composition, lighting, any "
                 "exact text in quotes. Takes 15-90 s per image.")
EDIT_TEXT = ("Edit images with an instruction (Qwen-Image 2.1): change, add or remove things, restyle, combine "
             "several reference images (up to 10; FLUX.2 klein uses the first 6). The output keeps the first "
             "image's proportions, so put first the picture whose scene and framing should stay: images "
             "[\"sofa.jpg\", \"dog.jpg\"] with 'put the dog from image 2 on the sofa in image 1'. To change a picture "
             "you made or edited before, edit its newest file (the one its result named), not the original - unless "
             "the user asks to start again from the original.")

register(Tool("image_generate", GENERATE_TEXT, _generate_params(), image_generate,
              available=_images_ready, in_subagent=False, group="images", dynamic=_generate_dynamic))
register(Tool("image_edit", EDIT_TEXT, _edit_params(), image_edit,
              available=_edit_ready, in_subagent=False, group="images", dynamic=_edit_dynamic))
register(Tool("image_remove_background", "Cut out the subject of an image: a PNG with a transparent background.",
              obj({"path": {"type": "string"}, "save_as": {"type": "string"}}, ["path"]),
              image_remove_background, available=_cutout_ready, in_subagent=False, group="images"))
