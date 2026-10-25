"""Video: from a description, from a picture, or from a first to a last picture (ComfyUI, GPU). The tool saves an
MP4 in the working folder, where the card under the answer plays it."""
from __future__ import annotations

from .audio import _progress, slug
from .base import Tool, ToolContext, ToolResult, gpu_failure, obj, register
from .images import _image_bytes

ORIENTATIONS = ("landscape", "portrait", "square")
MAX_PICTURE_BYTES = 50 * 1024 * 1024


def _pipe(ctx: ToolContext):
    return ctx.hub.extras.get("video")


def _free(ctx: ToolContext, base: str) -> str:
    rel, n = f"{base}.mp4", 2
    while ctx.workdir.resolve(rel).exists():
        rel, n = f"{base}-{n}.mp4", n + 1
    return rel


def _target(ctx: ToolContext, save_as: str | None, stem: str) -> str:
    """The MP4's path in the working folder. save_as wins and may overwrite (a folder gets a free name from the
    prompt); otherwise a free name from the prompt - in a project under video/, in a chat's own folder at the top."""
    raw = (save_as or "").strip()
    if not raw:
        return _free(ctx, f"video/{stem}" if ctx.project else stem)
    p = ctx.workdir.resolve(raw)                     # a path in the folder or a sandbox path; outside: ValueError
    rel = ctx.workdir.rel(p)
    if raw.endswith("/") or p.is_dir():
        return _free(ctx, stem if rel == "." else f"{rel}/{stem}")
    ext = p.suffix.lower()
    if ext not in ("", ".mp4"):
        raise ValueError("save_as must end in .mp4 (convert it with ffmpeg in bash afterwards for other formats)")
    return rel if ext else rel + ".mp4"


def _picture(ctx: ToolContext, ref) -> bytes | None:
    """A picture in the working folder (or an attached one's sha), at most 50 MB."""
    ref = str(ref or "").strip()
    if not ref:
        return None
    p = ctx.workdir.resolve(ref) if "/" in ref or "." in ref else None
    if p is not None and p.is_file() and p.stat().st_size > MAX_PICTURE_BYTES:
        raise ValueError(f"{ref} is over 50 MB")
    return _image_bytes(ctx, ref)[0]


def _reach(ctx: ToolContext, data: bytes) -> str:
    """How the user gets the file, for the result."""
    from ..device import MAX_OUT_FILE
    if getattr(ctx.engine, "device", None) is not None and len(data) > MAX_OUT_FILE:
        return (f"But it is {len(data) / 2**20:.1f} MB, more than the {MAX_OUT_FILE / 2**20:g} MB a file may have to "
                "reach the user's device in this chat, so it will not get there: make it shorter or smaller, or "
                "compress it with ffmpeg in bash.")
    return "The user can play it under your answer."


async def video_generate(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipe(ctx)
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult("prompt is required: what happens in the video", is_error=True)
    from ...video.pipeline import VideoError
    first, last = str(args.get("image") or "").strip(), str(args.get("last_image") or "").strip()
    if last and not first:
        return ToolResult("last_image needs image too: the picture the video starts with", is_error=True)
    try:
        image, last_image = _picture(ctx, first), _picture(ctx, last)
    except FileNotFoundError as exc:
        return ToolResult(f"No such picture in the working folder: {exc}", is_error=True)
    except ValueError as exc:
        return ToolResult(f"Video generation failed: {exc}", is_error=True)
    try:
        raw = args.get("seconds")
        seconds = float(raw) if raw not in (None, "") else 5.0
    except (TypeError, ValueError):
        return ToolResult("seconds must be a number", is_error=True)
    orientation = str(args.get("orientation") or "landscape").lower()
    if orientation not in ORIENTATIONS:
        return ToolResult("orientation must be landscape, portrait or square", is_error=True)
    quality = str(args.get("quality") or "fast").lower()
    if quality not in ("fast", "full"):
        return ToolResult("quality must be fast or full", is_error=True)
    hd = args.get("hd") if isinstance(args.get("hd"), bool) else None
    try:
        rel = _target(ctx, args.get("save_as"), slug(prompt, fallback="video"))
        out = await pipe.generate(prompt=prompt, negative=str(args.get("negative") or ""), image=image,
                                  last_image=last_image, seconds=seconds, orientation=orientation, hd=hd,
                                  quality=quality, seed=args.get("seed"), model=args.get("model") or None,
                                  ticket=ctx.ticket, on_progress=await _progress(ctx, "video_progress"))
    except (ValueError, VideoError) as exc:
        return gpu_failure("Video generation failed", exc)
    ctx.workdir.write(rel, out["data"], "agent", "hub", f"made {rel}")
    source = {"t2v": "", "i2v": f" from {first}", "flf2v": f" from {first} to {last}"}[out["ability"]]
    quality_text = f", {out['quality']} quality" if out.get("quality") else ""
    cut = f" (seconds is at most {out['max_seconds']:g})" if seconds > out["max_seconds"] else ""
    return ToolResult(f"Saved {rel}{source}: {out['seconds']:g} s{cut}, {out['frames']} frames at {out['fps']} fps, "
                      f"{out['width']}x{out['height']}, {out['model']['name']}{quality_text}, seed {out['seed']} "
                      f"({out['compute']:.0f} s on the GPU). It has no sound. {_reach(ctx, out['data'])}",
                      display={"video": [{"path": rel, "seconds": out["seconds"], "model": out["model"]["name"]}]})


def _params(models: list[str]) -> dict:
    model: dict = {"type": "string", "enum": models} if models else {"type": "string"}
    return obj({"prompt": {"type": "string", "description": "What happens: the subject, what moves and how, the "
                                                            "camera, the light and the style."},
                "image": {"type": "string", "description": "Optional: a picture in the working folder that starts "
                                                           "the video (its first frame)."},
                "last_image": {"type": "string", "description": "Optional, with image: a picture to end on."},
                "seconds": {"type": "number", "description": "1-10, default 5 (what the models learnt)."},
                "orientation": {"type": "string", "enum": list(ORIENTATIONS),
                                "description": "Without a picture; a video from a picture keeps its shape."},
                "hd": {"type": "boolean", "description": "true: 720p, false: 480p (default: the model's own)."},
                "quality": {"type": "string", "enum": ["fast", "full"],
                            "description": "fast (default) or full - when the user asks for the best quality: "
                                           "several times longer. Wan 2.2 14B: 20 steps instead of 4; Wan 2.2 5B "
                                           "Turbo: Wan 2.2 5B's 20 steps instead of 4."},
                "negative": {"type": "string", "description": "What to keep out."},
                "model": model, "seed": {"type": "integer"},
                "save_as": {"type": "string", "description": "Path in the working folder (.mp4)."}}, ["prompt"])


TEXT = ("Make a short video - from a description, from a picture that starts it, or from a first to a last picture "
        "- as an MP4 in the working folder, which the user plays under your answer. It runs on the GPU and takes "
        "minutes.")


def _dynamic(ctx: ToolContext) -> tuple[str, dict]:
    pipe = _pipe(ctx)
    models = pipe.installed() if pipe else []
    if not models:
        return TEXT, _params([])
    from ...video.pipeline import WHAT, size_for
    lines = []
    for e in models:
        s = e.get("settings") or {}
        w, h = size_for(e)
        can = ", ".join(WHAT[a] for a in e.get("abilities") or [] if a in WHAT)
        lines.append(f"- {e['id']}: {e['name']} - {can}; {w}x{h} at {s.get('fps', 16)} fps by default")
    # The owner's choice stands unless the user names a model, as for pictures: the second test run's model
    # picked the 14B pair for a picture on its own, and that job stalled for minutes (HANDOFF item 51).
    picks = [f"from {words}: {pipe.chosen(a)['name']}" for a, words in (("t2v", "a description"),
                                                                         ("i2v", "a picture")) if pipe.available(a)]
    # 0.28.1: with the 14B picture to video deleted (Søren's disk), nothing makes a first-to-last video - say so here
    # instead of letting the assistant find out from a refusal.
    ends = ""
    if not pipe.available("flf2v"):
        could = [e["name"] for e in pipe.hub.catalog.entries("video") if "flf2v" in (e.get("abilities") or [])]
        ends = ("\n- A video from a first to a last picture: none of the downloaded models makes one"
                + (f" ({', '.join(could)} would - Settings -> Models -> Video)" if could else "")
                + ". Tell the user instead of trying.")
    text = (TEXT + "\n- prompt: one rich paragraph in English: the subject, what it does and how it moves, the "
            "camera (static, slow push-in, pan left, orbit, tracking shot, handheld), the light, the style and the "
            "mood. With a picture, describe the motion and the camera - the picture already shows what is in it. "
            "One shot per video; for a longer story make several and join them with ffmpeg.\n- seconds: 5 is what "
            "the models learnt; longer takes longer and can loop or drift.\n- For a particular look, make the "
            "first frame with image_generate (when offered) and start the video from it.\n- The video has no "
            "sound: make it with sound_generate or music_generate (when offered) and put it under the video with "
            "ffmpeg in bash: ffmpeg -i video.mp4 -i sound.mp3 -map 0:v -map 1:a -c:v copy -c:a aac -shortest "
            "out.mp4" + ends + "\nWithout model: " + "; ".join(picks) + " - pick another only when the user asks for "
            "it.\nModels:\n" + "\n".join(lines))
    return text, _params([e["id"] for e in models])


def _ready(ctx: ToolContext) -> bool:
    pipe = _pipe(ctx)
    return bool(pipe and pipe.available())


register(Tool("video_generate", TEXT, _params([]), video_generate, available=_ready, in_subagent=False,
              group="video", dynamic=_dynamic))
