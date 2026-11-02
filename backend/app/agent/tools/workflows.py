"""The owner's ComfyUI workflows as a tool (0.67.0, Settings -> Models -> Workflows): the assistant runs one with
the inputs the owner opened to it, and its pictures, video, sound and 3D files land in the working folder. Only in
the owner's own chats - like LoRAs, workflows are the admin's."""
from __future__ import annotations

import time
from pathlib import Path

from ...chats import store as chats
from .audio import _progress, slug
from .base import Tool, ToolContext, ToolResult, gpu_failure, obj, register
from .images import _image_bytes

KIND_TEXT = {"image": "a picture: its path in the working folder", "audio": "sound: its path in the working folder",
             "video": "a video: its path in the working folder", "text": "text", "seed": "a whole number (random if "
             "left out)", "number": "a number", "boolean": "true or false", "choice": "one of the listed choices"}
MAX_FILE = 200 * 1024 * 1024


def _store(ctx: ToolContext):
    return ctx.hub.extras.get("workflows")


def _admins_turn(ctx: ToolContext) -> bool:
    return getattr(ctx.engine, "device", None) is None


def _open(ctx: ToolContext) -> list[dict]:
    store = _store(ctx)
    return store.list() if store is not None and _admins_turn(ctx) else []


def _line(item: dict) -> str:
    from ...workflows.inspect import clean
    what = clean(item.get("label") or item.get("title") or item["key"], 80)
    bits = [KIND_TEXT.get(item["kind"], item["kind"])]
    if item["kind"] == "number":
        lo, hi = item.get("min"), item.get("max")
        if lo is not None or hi is not None:
            bits.append(f"{lo if lo is not None else '...'}-{hi if hi is not None else '...'}")
    if item["kind"] == "choice" and item.get("options"):
        bits.append("choices: " + ", ".join(str(o) for o in item["options"][:12]))
    if item["kind"] not in ("image", "audio", "video", "seed") and item.get("default") not in (None, ""):
        d = clean(item["default"], 200)
        bits.append(f"default {d[:80]}{'...' if len(d) > 80 else ''}")
    desc = f" - {clean(item['description'], 300)}" if item.get("description") else ""
    return f"  {item['key']} ({what}; {'; '.join(bits)}){desc}"


def _ready(ctx: ToolContext) -> bool:
    return bool(_open(ctx))


def _params(ids: list[str]) -> dict:
    wf: dict = {"type": "string", "enum": ids} if ids else {"type": "string"}
    return obj({"workflow": {**wf, "description": "Which workflow (its id)."},
                "inputs": {"type": "object", "additionalProperties": True,
                           "description": "The workflow's inputs by key, as listed for it. Leave out what should keep "
                                          "the workflow's own value."},
                "save_as": {"type": "string", "description": "Optional folder in the working folder for the results."}},
               ["workflow"])


TEXT = ("Run one of the ComfyUI workflows the owner added (Settings -> Models -> Workflows) on the GPU: its "
        "pictures, video, sound or 3D files are saved in the working folder and shown to the user.")


def _dynamic(ctx: ToolContext) -> tuple[str, dict]:
    items = _open(ctx)
    if not items:
        return TEXT, _params([])
    lines = []
    for wf in items:
        open_inputs = [i for i in wf.get("inputs") or [] if i.get("on")]
        desc = f" - {wf['description']}" if wf.get("description") else ""
        lines.append(f"- {wf['id']}: {wf['name']}{desc}\n" + ("\n".join(_line(i) for i in open_inputs)
                                                               if open_inputs else "  (no inputs: it runs as it is)"))
    return (TEXT + " Only the inputs listed can be set; pictures, sound and video are files in the working folder "
            "(make one first with the other tools when the user has none). It takes from seconds to many minutes."
            "\nWorkflows:\n" + "\n".join(lines)), _params([w["id"] for w in items])


def _target(ctx: ToolContext, save_as: str | None, stem: str, n: int, ext: str) -> str:
    raw = (save_as or "").strip().rstrip("/")
    base = (ctx.workdir.rel(ctx.workdir.resolve(raw)) if raw else
            f"{ctx.workdir.own}workflows").strip("/")
    base = "" if base == "." else base
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}{'-' + str(n) if n > 1 else ''}{ext}"
    rel = f"{base}/{name}" if base else name
    k = 2
    while ctx.workdir.resolve(rel).exists():
        rel = f"{base}/{Path(name).stem}-{k}{ext}" if base else f"{Path(name).stem}-{k}{ext}"
        k += 1
    return rel


def _file(ctx: ToolContext, ref: str, kind: str) -> tuple[bytes, str]:
    if kind == "image":
        return _image_bytes(ctx, ref)
    p = ctx.workdir.resolve(ref)
    if not p.is_file():
        raise FileNotFoundError(ref)
    if p.stat().st_size > MAX_FILE:
        raise ValueError(f"{ref} is over 200 MB")
    return p.read_bytes(), p.name


async def workflow_run(ctx: ToolContext, args: dict) -> ToolResult:
    from ...workflows.runner import FILE_KINDS, WorkflowRunError, run
    from ...workflows.store import WorkflowStoreError
    store = _store(ctx)
    if store is None or not _admins_turn(ctx):
        return ToolResult("There are no workflows in this chat.", is_error=True)
    wid = str(args.get("workflow") or "").strip()
    try:
        wf = store.get(wid)
    except WorkflowStoreError:
        names = ", ".join(f"{w['id']} ({w['name']})" for w in store.list())
        return ToolResult(f"No workflow {wid!r}. The workflows: {names or 'none'}.", is_error=True)
    given = args.get("inputs") if isinstance(args.get("inputs"), dict) else {}
    open_inputs = {i["key"]: i for i in wf.get("inputs") or [] if i.get("on")}
    extra = sorted(set(given) - set(open_inputs))
    if extra:
        return ToolResult(f"{wf['name']} has no input {', '.join(extra)}; it takes: "
                          f"{', '.join(open_inputs) or 'nothing'}.", is_error=True)
    need = [k for k, i in open_inputs.items() if i["kind"] in FILE_KINDS and not given.get(k)]
    if need:                                  # its example file is not in ComfyUI's input folder
        return ToolResult(f"{wf['name']} needs {', '.join(need)}: "
                          + "; ".join(f"{k} - {KIND_TEXT[open_inputs[k]['kind']]}" for k in need), is_error=True)
    files: dict[str, tuple[bytes, str]] = {}
    for key, item in open_inputs.items():
        if item["kind"] in FILE_KINDS and given.get(key):
            try:
                files[key] = _file(ctx, str(given[key]), item["kind"])
            except FileNotFoundError:
                return ToolResult(f"No such file in the working folder: {given[key]}", is_error=True)
            except ValueError as exc:
                return ToolResult(str(exc), is_error=True)
    try:
        ctx.workdir.resolve(str(args.get("save_as") or "."))
    except ValueError as exc:
        return ToolResult(f"save_as: {exc}", is_error=True)
    from ...images.pipeline import EPHEMERAL
    try:
        out = await run(ctx.hub, wf, {k: v for k, v in given.items() if k not in files}, files, ticket=ctx.ticket,
                        on_progress=await _progress(ctx, "image_progress"), keep=not EPHEMERAL.get())
    except (ValueError, WorkflowRunError) as exc:
        return gpu_failure(f"The workflow {wf['name']} failed", exc)
    if not out["items"] and not out["texts"]:
        return ToolResult(f"{wf['name']} ran ({out['compute']:.0f} s) but saved nothing.", is_error=True)
    stem = slug(wf["name"], fallback="workflow")
    shown, images, paths, video, audio = [], [], [], [], []
    for n, item in enumerate(out["items"], 1):
        ext = Path(item["name"]).suffix.lower() or ".bin"
        rel = _target(ctx, args.get("save_as"), stem, n if len(out["items"]) > 1 else 1, ext)
        ctx.workdir.write(rel, item["data"], "agent", "hub", f"made {rel} with {wf['name']}")
        paths.append(rel)
        if item["kind"] == "image" and len(images) < 4:
            up = chats.save_upload(item["data"], Path(rel).name, "image/png" if ext == ".png" else
                                   {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext,
                                                                                                   "image/png"))
            images.append({"sha": up["sha"], "mime": up["mime"]})
            shown.append({"sha": up["sha"], "path": rel})
        elif item["kind"] == "video":
            video.append({"path": rel, "model": wf["name"]})
        elif item["kind"] == "audio":
            audio.append({"path": rel, "model": wf["name"]})
    used = ", ".join(f"{k}={str(v)[:60]}" for k, v in out["used"].items() if k not in files)
    text = (f"Ran {wf['name']} ({out['compute']:.0f} s on the GPU){': ' + used if used else ''}. "
            f"Saved {', '.join(paths) if paths else 'no files'}.")
    for node, lines in out["texts"].items():
        text += f"\nText from node {node}: " + " ".join(lines)[:2000]
    display: dict = {}
    if shown:
        display["generated"] = shown
        display["made_with"] = {"model": wf["name"], "loras": [], "left_out": []}
    if video:
        display["video"] = video
    if audio:
        display["audio"] = audio
    return ToolResult(text, images=images, display=display or None)


register(Tool("workflow_run", TEXT, _params([]), workflow_run, available=_ready, in_subagent=False,
              group="workflows", dynamic=_dynamic))
