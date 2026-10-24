"""Video on the card: ComfyUI through the scheduler, like pictures and sound - from a description, from a picture
(its first frame), or between a first and a last picture. Each call returns the finished MP4's bytes; the tool
puts it in the working folder."""
from __future__ import annotations

import asyncio
import io
import math
import random
import time
from pathlib import Path

from PIL import Image, ImageOps

from ..gpu.comfy import ComfyError, input_images
from ..gpu.scheduler import SchedulerError, Ticket
from ..images.pipeline import EPHEMERAL
from ..state import Hub
from . import workflows as W


class VideoError(RuntimeError):
    pass


ABILITIES = ("t2v", "i2v", "flf2v")            # from text, from a picture, from a first and a last picture
ORIENTATIONS = ("landscape", "portrait", "square")
QUALITIES = ("fast", "full")
MAX_SECONDS = 10.0
MAX_PICTURE_BYTES = 50 * 1024 * 1024
SETTING = {"t2v": "t2v_model", "i2v": "i2v_model", "flf2v": "i2v_model"}
WHAT = {"t2v": "video from a description", "i2v": "video from a picture",
        "flf2v": "video from a first and a last picture"}


def abilities(entry: dict | None) -> list[str]:
    return list((entry or {}).get("abilities") or [])


def comfy_name(entry: dict, role: str) -> str:
    """A file's name as ComfyUI's loaders list it: its path inside its folder type (diffusion_models/x -> x)."""
    f = [x for x in entry.get("files") or [] if x.get("role") == role]
    if not f:
        raise VideoError(f"{entry.get('name')} has no {role} file in the catalog")
    parts = Path(f[0]["dest"]).parts
    return "/".join(parts[2:]) if len(parts) > 2 and parts[0] == "comfy" else parts[-1]


def picture_size(data: bytes) -> tuple[int, int]:
    """A picture's upright size; refuses what is not a picture."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            return img.size
    except Exception as exc:  # noqa: BLE001 - PIL raises many kinds
        raise VideoError(f"the picture could not be read ({exc.__class__.__name__})")


def as_png(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()


def size_for(entry: dict, orientation: str = "landscape", hd: bool | None = None,
             picture: tuple[int, int] | None = None) -> tuple[int, int]:
    """Width and height for a video: the model's pixel count for 480p or 720p (its own default when hd is None),
    in the picture's shape when there is one (between 1:2.5 and 2.5:1), else landscape, portrait or square;
    multiples of what the model needs (16, or 32 for Wan 2.2 5B)."""
    s = entry.get("settings") or {}
    sizes = s.get("sizes") or {"480p": [832, 480]}
    key = s.get("size") or next(iter(sizes))
    if hd is not None:
        key = "720p" if hd else "480p"
        if key not in sizes:
            key = s.get("size") or next(iter(sizes))
    base_w, base_h = sizes[key]
    mult = int(s.get("multiple") or 16)
    if picture:
        aspect = max(0.4, min(2.5, picture[0] / max(1, picture[1])))
    else:
        aspect = {"portrait": base_h / base_w, "square": 1.0}.get(orientation, base_w / base_h)
    w = math.sqrt(base_w * base_h * aspect)
    h = w / aspect
    return max(mult * 4, int(round(w / mult)) * mult), max(mult * 4, int(round(h / mult)) * mult)


def one_bar(on_progress, split: int, steps: int):
    """The 14B pair's two samplers as one progress bar: the high-noise expert (node 14) reports steps 1..split of
    split, the low-noise one (node 15) 1..steps-split of steps-split."""
    if on_progress is None:
        return None

    async def cb(value: int, maximum: int, node=None):
        if str(node) == "15":
            value, maximum = split + value, steps
        elif str(node) == "14":
            maximum = steps
        res = on_progress(value, maximum, node)
        if asyncio.iscoroutine(res):
            await res
    return cb


class VideoPipeline:
    def __init__(self, hub: Hub):
        self.hub = hub

    # ------------------------------------------------------------ models
    def entry(self, model: str | None) -> dict | None:
        return self.hub.catalog.get("video", model) if model else None

    def installed(self, ability: str | None = None) -> list[dict]:
        """Downloaded video models (that can do `ability`), in catalog order."""
        return [e for e in self.hub.catalog.entries("video")
                if (ability is None or ability in abilities(e)) and self.hub.catalog.installed(e)]

    def available(self, ability: str | None = None) -> bool:
        return bool(self.installed(ability))

    def chosen(self, ability: str, wanted: str | None = None) -> dict:
        """The model asked for (when it is downloaded and can do this), else Settings -> Assistant -> Video, else
        the first downloaded one that can."""
        models = self.installed(ability)
        if not models:
            others = self.installed()
            if others:
                raise VideoError(f"no downloaded video model makes {WHAT[ability]}: "
                                 f"{', '.join(e['name'] for e in others)} cannot")
            raise VideoError("no video model is downloaded (Settings -> Models -> Video)")
        by_id = {e["id"]: e for e in models}
        if wanted:
            if wanted in by_id:
                return by_id[wanted]
            other = self.entry(wanted)
            if other is None:
                raise VideoError(f"there is no video model called {wanted}; downloaded: {', '.join(by_id)}")
            if ability not in abilities(other):
                raise VideoError(f"{other['name']} does not make {WHAT[ability]}; use one of {', '.join(by_id)}")
            raise VideoError(f"{other['name']} is not downloaded (Settings -> Models); downloaded: {', '.join(by_id)}")
        setting = str(self.hub.settings.get("video", SETTING[ability]) or "")
        return by_id.get(setting) or models[0]

    # ------------------------------------------------------------ ComfyUI
    async def _upload(self, pictures: dict[str, bytes]) -> dict[str, str]:
        names: dict[str, str] = {}
        stamp = int(time.time() * 1000)
        try:
            for key, data in pictures.items():
                names[key] = await self.hub.comfy.upload_image(as_png(data), f"hub-{stamp}-{key}.png")
        except ComfyError as exc:
            self.hub.comfy.delete_files([("input", "", n) for n in names.values()])
            raise VideoError(str(exc))
        return names

    async def _run(self, graph: dict, ticket: Ticket | None, owner: str, priority: str, label: str,
                   on_progress=None) -> tuple[bytes, float]:
        """On the card through the scheduler; the pictures it loaded leave ComfyUI's input folder afterwards."""
        own = None
        t0 = time.monotonic()
        try:
            if ticket is not None:
                await ticket.switch("comfy")
            else:
                own = await self.hub.scheduler.acquire(owner, "video", "comfy", priority, label=label,
                                                       max_wait=float(self.hub.settings.get("queue", "max_wait_s")))
            info = await self.hub.comfy.object_info()
            missing = sorted(W.class_types(graph) - set(info))
            if missing:
                raise VideoError(f"ComfyUI does not know these nodes: {', '.join(missing)} - update ComfyUI "
                                 "(COMFYUI_REF in .env)")
            t0 = time.monotonic()
            files, _ = await self.hub.comfy.run_job(graph, on_progress=on_progress, keep=not EPHEMERAL.get(),
                                                    kind="video", timeout=7200)
            return files[0][1], time.monotonic() - t0
        except (ComfyError, SchedulerError) as exc:
            raise VideoError(str(exc))
        finally:
            if own is not None:
                await own.release()
            self.hub.comfy.delete_files([("input", "", n) for n in input_images(graph)])

    async def generate(self, *, prompt: str, negative: str = "", image: bytes | None = None,
                       last_image: bytes | None = None, seconds: float = 5.0, orientation: str = "landscape",
                       hd: bool | None = None, quality: str = "fast", seed: int | None = None,
                       model: str | None = None, ticket: Ticket | None = None, owner: str = "web",
                       priority: str = "interactive", on_progress=None) -> dict:
        if last_image is not None and image is None:
            raise VideoError("a last picture needs a first one")
        ability = "flf2v" if last_image is not None else "i2v" if image is not None else "t2v"
        entry = self.chosen(ability, model)
        s = entry.get("settings") or {}
        fps = int(s.get("fps") or 16)
        seconds = max(1.0, min(MAX_SECONDS, float(seconds)))
        quality = quality if quality in QUALITIES else "fast"
        picture = None
        for data in (image, last_image):
            if data is not None:
                if len(data) > MAX_PICTURE_BYTES:
                    raise VideoError("the picture is over 50 MB")
                size = picture_size(data)
                picture = picture or size
        width, height = size_for(entry, orientation if orientation in ORIENTATIONS else "landscape", hd, picture)
        sd = int(seed) if seed is not None else random.randint(1, 2**31 - 1)
        family = str(entry.get("family") or "")
        if family == "wan2.2-5b":
            files = {"unet": comfy_name(entry, "unet"), "text_encoder": comfy_name(entry, "text_encoder"),
                     "vae": comfy_name(entry, "vae")}
        elif family == "wan2.2-14b":
            fast = quality == "fast"
            files = {"high": comfy_name(entry, "unet_high"), "low": comfy_name(entry, "unet_low"),
                     "text_encoder": comfy_name(entry, "text_encoder"), "vae": comfy_name(entry, "vae"),
                     "lora_high": comfy_name(entry, "lora_high") if fast else None,
                     "lora_low": comfy_name(entry, "lora_low") if fast else None}
        else:
            raise VideoError(f"{entry['name']} has no workflow in this Hub (family {family!r})")
        names = await self._upload({k: v for k, v in (("start", image), ("end", last_image)) if v is not None})
        common = dict(prompt=prompt, negative_prompt=negative, seconds=seconds, width=width, height=height, seed=sd,
                      fps=fps, start_image=names.get("start"), sampler=str(s.get("sampler") or "euler"),
                      scheduler=str(s.get("scheduler") or "simple"), shift=float(s.get("shift") or 5.0))
        if family == "wan2.2-5b":
            graph = W.wan_5b(steps=int(s.get("steps") or 20), cfg=float(s.get("cfg") or 5.0), **common, **files)
        else:
            # the template's steps, split and cfg for fast or full - and its own for an ability that differs (the
            # first-to-last-frame template runs full quality at shift 8 and cfg 4)
            mode = s.get(f"{ability}_{quality}") or s.get(quality) or {}
            common["shift"] = float(mode.get("shift") or common["shift"])
            steps, split = int(mode.get("steps") or 4), int(mode.get("split") or 2)
            graph = W.wan_14b(steps=steps, split=split, cfg=float(mode.get("cfg") or 1.0), end_image=names.get("end"),
                              **common, **files)
            on_progress = one_bar(on_progress, split, steps)
        n = W.frames(seconds, fps)
        data, took = await self._run(graph, ticket, owner, priority, f"video: {prompt[:60]}", on_progress)
        return {"data": data, "width": width, "height": height, "frames": n, "fps": fps,
                "seconds": round(n / fps, 2), "seed": sd, "model": entry, "compute": round(took, 1),
                "quality": quality if family == "wan2.2-14b" else None, "ability": ability,
                "max_seconds": MAX_SECONDS}
