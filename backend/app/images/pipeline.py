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


GENERATORS = ("qwen-image-2.1", "flux2-klein-4b", "ernie-image", "ernie-image-turbo")
EDITORS = ("qwen-image-2.1", "flux2-klein-4b")


def _png_ok(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


class ImagePipeline:
    def __init__(self, hub: Hub):
        self.hub = hub

    def available(self, model: str | None = None) -> bool:
        """Whether a model is downloaded; without one, whether any can make pictures."""
        if model is None:
            return any(self.available(m) for m in GENERATORS)
        e = self.hub.catalog.get("comfy", model)
        return bool(e) and self.hub.catalog.installed(e)

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
        model = model or s.get("model", "qwen-image-2.1")
        if model.startswith("ernie-image"):
            model = self._ernie(model, fast)
        elif fast and model == "qwen-image-2.1" and self.available("qwen-image-2.1-turbo"):
            model = "qwen-image-2.1-turbo"
        if not self.available(model):
            raise ImageError(f"the image model {model} is not downloaded (Settings -> Models)")
        results = []
        for i in range(max(1, min(int(count), 4))):
            sd = (int(seed) + i) if seed is not None else random.randint(1, 2**31 - 1)
            if model.startswith("ernie-image"):
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
            if model.startswith("flux2-klein"):
                graph = W.klein_t2i(prompt, width, height, sd, steps=steps or 4)
            else:
                turbo = model.endswith("turbo")
                graph = W.qwen_t2i(prompt, width, height, sd, steps=steps or int(s.get("steps", 25)),
                                   cfg=float(s.get("cfg", 1.0)), turbo=turbo, negative=negative)
            out = await self._run(graph, ticket, owner, priority, f"image: {prompt[:60]}", on_progress)
            for item in self._save(out, "generated"):
                results.append({**item, "seed": sd, "model": model})
        return results

    async def edit(self, prompt: str, images: list[bytes], seed: int | None = None, fast: bool = False,
                   model: str | None = None, steps: int | None = None, ticket: Ticket | None = None,
                   owner: str = "web", priority: str = "interactive", on_progress=None, count: int = 1) -> list[dict]:
        s = self.hub.settings.get("images")
        model = model or s.get("model", "qwen-image-2.1")
        if model.startswith("ernie-image"):           # ERNIE-Image only generates: edit with another model
            model = next((m for m in EDITORS if self.available(m)), "qwen-image-2.1")
        if fast and model == "qwen-image-2.1" and self.available("qwen-image-2.1-turbo"):
            model = "qwen-image-2.1-turbo"
        if not self.available(model):
            raise ImageError(f"the image model {model} is not downloaded (Settings -> Models)")
        results = []
        for i in range(max(1, min(int(count), 4))):
            names = await self._upload_inputs(images)
            sd = (int(seed) + i) if seed is not None else random.randint(1, 2**31 - 1)
            if model.startswith("flux2-klein"):
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
