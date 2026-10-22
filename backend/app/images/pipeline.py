"""Running image jobs: on the card through the scheduler (inside an agent's
lease, or as a job of its own for API callers), outputs saved as uploads."""
from __future__ import annotations

import base64
import io
import random
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from PIL import Image

from ..api.common import caller
from ..chats import store as chats
from ..gpu.comfy import ComfyError
from ..gpu.scheduler import SchedulerError, Ticket
from ..media import sign
from ..state import Hub
from . import workflows as W


class ImageError(RuntimeError):
    pass


def family(entry: dict | None) -> str:
    """Which workflow a picture model runs with (catalog "family")."""
    return str((entry or {}).get("family") or "")


def abilities(entry: dict | None) -> list[str]:
    """generate, edit, cutout (catalog "abilities")."""
    return list((entry or {}).get("abilities") or [])


# How to write the prompt for each family; image_generate's description carries it.
HINTS = {
    "qwen-image": "Detailed English prompt (other languages work): subject, style, composition, lighting, exact text "
                  "in quotes. Good at text in images; fast=true uses its 8-step turbo.",
    "flux2-klein": "Fast (4 steps). Detailed natural-language prompt.",
    "ernie-image": "Photorealistic. Its own prompt enhancer expands the prompt, so a short clear description is enough.",
    "z-image": "Fast (8 steps), photorealistic. Detailed natural-language prompt in English or Chinese, exact text in "
               "quotes. No negative prompt.",
    "chroma": "Any subject and style, no content filter. A detailed natural-language description; negative says what "
              "to avoid (by default blur, sketchiness and flat colours). Slower (about 26 steps).",
}
SDXL_HINTS = {
    "pony": "SDXL, Pony: comma-separated Danbooru-style tags (e.g. 1girl, solo, smile, outdoors). The quality tags "
            "are added for you; rating_safe, rating_questionable or rating_explicit and source_anime, "
            "source_cartoon, source_furry or source_pony steer it.",
    "illustrious": "SDXL, Illustrious/NoobAI: comma-separated Danbooru-style tags (e.g. 1girl, solo, long hair, smile, "
                   "outdoors). The quality tags are added for you.",
    "sdxl": "SDXL: a short English description or comma-separated keywords; the first ~75 tokens count most.",
}


def prompt_hint(entry: dict) -> str:
    if family(entry) == "sdxl":
        preset = str((entry.get("sdxl") or {}).get("preset") or "sdxl")
        return SDXL_HINTS.get(preset, SDXL_HINTS["sdxl"]) + " negative is added to the model's own negative prompt."
    return HINTS.get(family(entry), "")


def _png_ok(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


class ImagePipeline:
    def __init__(self, hub: Hub):
        self.hub = hub

    def entry(self, model: str | None) -> dict | None:
        return self.hub.catalog.get("comfy", model) if model else None

    def installed(self, ability: str) -> list[dict]:
        """Downloaded models that can do something (generate, edit, cutout), in
        catalog order. A turbo that needs its base model is left out: fast=true
        reaches it."""
        return [e for e in self.hub.catalog.entries("comfy")
                if ability in abilities(e) and not e.get("requires") and self.hub.catalog.installed(e)]

    def available(self, model: str | None = None) -> bool:
        """Whether a model is downloaded; without one, whether any can make pictures."""
        if model is None:
            return bool(self.installed("generate"))
        e = self.entry(model)
        return bool(e) and self.hub.catalog.installed(e)

    def chosen(self, model: str | None) -> str:
        """The model asked for, or Settings -> Assistant -> Images when none was
        (or when a chat still names a model that has been removed)."""
        default = str(self.hub.settings.get("images", "model") or "qwen-image-2.1")
        return model if model and self.entry(model) else default

    def _ernie(self, model: str, fast: bool) -> str:
        """ERNIE-Image or its Turbo: what was asked for, else whichever is downloaded."""
        base, turbo = "ernie-image", "ernie-image-turbo"
        want = turbo if (model == turbo or fast) else base
        other = base if want == turbo else turbo
        return want if self.available(want) else (other if self.available(other) else model)

    async def _validate(self, graph: dict) -> None:
        try:
            info = await self.hub.comfy.object_info()
        except ComfyError as exc:
            raise ImageError(str(exc))
        missing = sorted(W.class_types(graph) - set(info))
        if missing:
            raise ImageError(f"ComfyUI does not know these nodes: {', '.join(missing)} - update ComfyUI "
                             "(COMFYUI_REF in .env) or the workflow")

    async def _run(self, graph: dict, ticket: Ticket | None, owner: str, priority: str, label: str,
                   on_progress=None, texts: dict | None = None) -> list[tuple[str, bytes]]:
        own = None
        try:
            if ticket is not None:
                await ticket.switch("comfy")
            else:
                own = await self.hub.scheduler.acquire(owner, "image", "comfy", priority, label=label,
                                                       max_wait=float(self.hub.settings.get("queue", "max_wait_s")))
            await self._validate(graph)
            images, out_texts = await self.hub.comfy.run_outputs(graph, on_progress=on_progress)
            if texts is not None:
                texts.update(out_texts)
            return images
        except (ComfyError, SchedulerError) as exc:
            raise ImageError(str(exc))
        finally:
            if own is not None:
                await own.release()

    async def _upload_inputs(self, images: list[bytes]) -> list[str]:
        names = []
        for i, data in enumerate(images):
            buf = io.BytesIO()
            with Image.open(io.BytesIO(data)) as img:
                img = img.convert("RGBA") if img.mode in ("RGBA", "LA", "P") else img.convert("RGB")
                img.save(buf, "PNG")
            names.append(await self.hub.comfy.upload_image(buf.getvalue(), f"hub-{int(time.time() * 1000)}-{i}.png"))
        return names

    def _save(self, outputs: list[tuple[str, bytes]], prefix: str) -> list[dict]:
        saved = []
        for fname, data in outputs:
            w, h = _png_ok(data)
            up = chats.save_upload(data, f"{prefix}-{fname}", "image/png")
            saved.append({**up, "width": w, "height": h})
        return saved

    async def generate(self, prompt: str, width: int = 1024, height: int = 1024, count: int = 1,
                       seed: int | None = None, fast: bool = False, model: str | None = None, negative: str = "",
                       steps: int | None = None, ticket: Ticket | None = None, owner: str = "web",
                       priority: str = "interactive", on_progress=None) -> list[dict]:
        s = self.hub.settings.get("images")
        model = self.chosen(model)
        if family(self.entry(model)) == "ernie-image":
            model = self._ernie(model, fast)
        elif fast and model == "qwen-image-2.1" and self.available("qwen-image-2.1-turbo"):
            model = "qwen-image-2.1-turbo"
        if not self.available(model):
            raise ImageError(f"the image model {model} is not downloaded (Settings -> Models)")
        entry = self.entry(model) or {}
        fam = family(entry)
        results = []
        for i in range(max(1, min(int(count), 4))):
            sd = (int(seed) + i) if seed is not None else random.randint(1, 2**31 - 1)
            if fam == "ernie-image":
                turbo = model.endswith("turbo")
                graph = W.ernie_t2i(prompt, width, height, sd, turbo=turbo,
                                    steps=steps or int(s.get("turbo_steps" if turbo else "steps", 8 if turbo else 25)),
                                    enhance=bool(s.get("enhance_prompts", True)))
                texts: dict = {}
                out = await self._run(graph, ticket, owner, priority, f"image: {prompt[:60]}", on_progress, texts)
                used = (texts.get(W.ERNIE_PROMPT_NODE) or [""])[0].strip()
                for item in self._save(out, "generated"):
                    results.append({**item, "seed": sd, "model": model, **({"prompt_used": used} if used else {})})
                continue
            if fam == "flux2-klein":
                graph = W.klein_t2i(prompt, width, height, sd, steps=steps or 4)
            elif fam == "z-image":
                graph = W.zimage_t2i(prompt, width, height, sd, steps=steps or int(s.get("turbo_steps", 8)))
            elif fam == "chroma":
                graph = W.chroma_t2i(prompt, width, height, sd, steps=steps or int(s.get("steps", 25)),
                                     negative=negative.strip() or None)
            elif fam == "sdxl":
                graph = self._sdxl(entry, prompt, width, height, sd, steps, negative)
            else:
                turbo = model.endswith("turbo")
                graph = W.qwen_t2i(prompt, width, height, sd, steps=steps or int(s.get("steps", 25)),
                                   cfg=float(s.get("cfg", 1.0)), turbo=turbo, negative=negative)
            out = await self._run(graph, ticket, owner, priority, f"image: {prompt[:60]}", on_progress)
            for item in self._save(out, "generated"):
                results.append({**item, "seed": sd, "model": model})
        return results

    @staticmethod
    def _sdxl(entry: dict, prompt: str, width: int, height: int, seed: int, steps: int | None, negative: str) -> dict:
        """An SDXL checkpoint with its own settings (Settings -> Models)."""
        p = entry.get("sdxl") or {}
        ckpt = entry["files"][0]["dest"].split("comfy/checkpoints/", 1)[-1]
        neg = ", ".join(x for x in (str(p.get("negative") or "").strip(), negative.strip()) if x)
        return W.sdxl_t2i(ckpt, prompt, width, height, seed, steps=int(steps or p.get("steps") or 25),
                          cfg=float(p.get("cfg") or 7.0), sampler=str(p.get("sampler") or "dpmpp_2m"),
                          scheduler=str(p.get("scheduler") or "karras"), clip_skip=int(p.get("clip_skip") or 1),
                          prompt_prefix=str(p.get("prefix") or ""), negative=neg)

    async def edit(self, prompt: str, images: list[bytes], seed: int | None = None, fast: bool = False,
                   model: str | None = None, steps: int | None = None, ticket: Ticket | None = None,
                   owner: str = "web", priority: str = "interactive", on_progress=None, count: int = 1) -> list[dict]:
        s = self.hub.settings.get("images")
        model = self.chosen(model)
        if "edit" not in abilities(self.entry(model)):   # ERNIE, Z-Image, Chroma, SDXL only generate
            model = next((e["id"] for e in self.installed("edit")), "qwen-image-2.1")
        if fast and model == "qwen-image-2.1" and self.available("qwen-image-2.1-turbo"):
            model = "qwen-image-2.1-turbo"
        if not self.available(model):
            raise ImageError(f"the image model {model} is not downloaded (Settings -> Models)")
        results = []
        for i in range(max(1, min(int(count), 4))):
            names = await self._upload_inputs(images)
            sd = (int(seed) + i) if seed is not None else random.randint(1, 2**31 - 1)
            if family(self.entry(model)) == "flux2-klein":
                graph = W.klein_edit(prompt, names, sd, steps=steps or 4)
            else:
                graph = W.qwen_edit(prompt, names, sd, steps=steps or int(s.get("steps", 25)),
                                    cfg=float(s.get("cfg", 1.0)), turbo=model.endswith("turbo"))
            out = await self._run(graph, ticket, owner, priority, f"edit: {prompt[:60]}", on_progress)
            for item in self._save(out, "edited"):
                results.append({**item, "seed": sd, "model": model})
        return results

    async def remove_background(self, image: bytes, ticket: Ticket | None = None, owner: str = "web",
                                priority: str = "interactive", on_progress=None) -> list[dict]:
        if not self.available("qwen-image-2.1"):
            raise ImageError("Qwen-Image 2.1 is not downloaded (Settings -> Models)")
        names = await self._upload_inputs([image])
        graph = W.qwen_remove_background(names[0], random.randint(1, 2**31 - 1),
                                         steps=int(self.hub.settings.get("images", "steps") or 25))
        out = await self._run(graph, ticket, owner, priority, "remove background", on_progress)
        return self._save(out, "cutout")

    # ------------------------------------------------------------ OpenAI images API
    def _response(self, request: Request, items: list[dict], fmt: str) -> dict:
        data = []
        for it in items:
            if fmt == "url":
                base = str(request.base_url).rstrip("/")
                data.append({"url": base + sign(self.hub.env.secret_key, it["sha"]),
                             "revised_prompt": None})
            else:
                from ..projects import blobs
                data.append({"b64_json": base64.b64encode(blobs.read(it["sha"])).decode()})
        return {"created": int(time.time()), "data": data}

    @staticmethod
    def _size(size: str | None) -> tuple[int, int]:
        if not size or size == "auto" or "x" not in size:
            return 1024, 1024
        w, _, h = size.partition("x")
        return int(w), int(h)

    async def api_generate(self, request: Request):
        body = await request.json()
        who = caller(request)
        w, h = self._size(body.get("size"))
        model = body.get("model") if self.hub.catalog.get("comfy", body.get("model") or "") else None
        try:
            items = await self.generate(body.get("prompt") or "", w, h, count=int(body.get("n") or 1),
                                        fast=body.get("quality") in ("low", "fast"), model=model,
                                        owner=who.owner, priority=who.priority)
        except ImageError as exc:
            return JSONResponse({"error": {"message": str(exc), "type": "server_error"}}, status_code=503)
        return self._response(request, items, body.get("response_format") or "b64_json")

    async def api_edit(self, request: Request):
        form = await request.form()
        who = caller(request)
        images = []
        for key in ("image", "image[]"):
            for f in form.getlist(key):
                if hasattr(f, "read"):
                    images.append(await f.read())
        if not images:
            return JSONResponse({"error": {"message": "send at least one image", "type": "invalid_request_error"}},
                                status_code=400)
        model = form.get("model") if self.hub.catalog.get("comfy", str(form.get("model") or "")) else None
        try:
            items = await self.edit(str(form.get("prompt") or ""), images, count=int(form.get("n") or 1),
                                    fast=str(form.get("quality") or "") in ("low", "fast"), model=model,
                                    owner=who.owner, priority=who.priority)
        except ImageError as exc:
            return JSONResponse({"error": {"message": str(exc), "type": "server_error"}}, status_code=503)
        return self._response(request, items, str(form.get("response_format") or "b64_json"))
