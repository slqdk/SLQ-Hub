"""Running one of the owner's workflows (0.67.0): its exposed inputs set from the call - text, numbers, a seed
(random unless given), pictures, sound and video uploaded to ComfyUI - then on the card through the scheduler, like
every picture job, and back come the files it saved."""
from __future__ import annotations

import copy
import math
import random
import time
from pathlib import Path

from ..gpu.comfy import ComfyError
from ..gpu.scheduler import SchedulerError, Ticket
from ..state import Hub
from .inspect import check

FILE_KINDS = ("image", "audio", "video")
MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
         ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
         ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}


class WorkflowRunError(RuntimeError):
    def __init__(self, message: str = "", hub: bool = False):
        super().__init__(message)
        self.hub = hub                       # the card or ComfyUI failed - not the request


def problems(wf: dict, info: dict) -> str:
    """Why the workflow cannot run in this ComfyUI now ("" when it can)."""
    found = check(wf["prompt"], info, wf.get("sources"))
    if found["unknown"]:
        return ("it needs nodes this ComfyUI does not have: " + ", ".join(found["unknown"][:10])
                + " (custom nodes are not installed in the Hub's ComfyUI)")
    if found["missing"]:
        names = ", ".join(m["file"] for m in found["missing"][:6])
        return f"model files are missing: {names} (Settings -> Models -> Workflows can download those it knows)"
    return ""


def _number(value, item: dict):
    """A number for an input: an integer read as one (seeds go up to 2^64), held to the node's own limits and steps;
    infinity and the like refused."""
    try:
        if item.get("integer"):
            v = int(value) if isinstance(value, (int, str)) and not isinstance(value, bool) else int(float(value))
        else:
            v = float(value)
            if not math.isfinite(v):
                raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise WorkflowRunError(f"{item['key']} must be {'a whole number' if item.get('integer') else 'a number'}")
    lo, hi, step = item.get("min"), item.get("max"), item.get("step")
    if item.get("integer"):
        if isinstance(step, (int, float)) and step > 1 and float(step).is_integer():
            base = int(lo) if isinstance(lo, (int, float)) and float(lo).is_integer() else 0
            v = base + round((v - base) / int(step)) * int(step)
        if isinstance(lo, (int, float)):
            v = max(int(math.ceil(lo)), v)
        if isinstance(hi, (int, float)):
            v = min(int(math.floor(hi)), v)
        return v
    if isinstance(lo, (int, float)):
        v = max(float(lo), v)
    if isinstance(hi, (int, float)):
        v = min(float(hi), v)
    return v


def apply(wf: dict, values: dict, uploaded: dict[str, str]) -> tuple[dict, dict]:
    """The workflow's graph with the call's values in its exposed inputs; seeds random unless given. Returns
    (graph, what was used: key -> value)."""
    graph = copy.deepcopy(wf["prompt"])
    used: dict = {}
    for item in wf.get("inputs") or []:
        if not item.get("on"):
            continue
        key, node = item["key"], graph.get(item["node"])
        if node is None:
            continue
        given = values.get(key)
        kind = item["kind"]
        if kind in FILE_KINDS:
            if key in uploaded:
                node["inputs"][item["input"]] = uploaded[key]
                used[key] = uploaded[key]
            continue
        if given is None or given == "":
            if kind != "seed" or item.get("control") == "fixed":   # the workflow's author fixed this seed
                continue
            given = random.randint(1, 2**48)
        if kind == "text":
            value = str(given)
        elif kind in ("number", "seed"):
            value = _number(given, item if kind == "number" else {**item, "integer": True})
        elif kind == "boolean":
            value = given if isinstance(given, bool) else str(given).strip().lower() in ("1", "true", "yes", "on")
        elif kind == "choice":
            options = item.get("options") or []
            if given not in options:
                raise WorkflowRunError(f"{key} must be one of: {', '.join(str(o) for o in options[:20])}")
            value = given
        else:
            continue
        node["inputs"][item["input"]] = value
        used[key] = value
    return graph, used


async def run(hub: Hub, wf: dict, values: dict, files: dict[str, tuple[bytes, str]], *, ticket: Ticket | None,
              owner: str = "web", priority: str = "interactive", on_progress=None, keep: bool = True) -> dict:
    """files: key -> (bytes, file name) for the exposed picture, sound and video inputs."""
    own = None
    names: list[str] = []
    t0 = time.monotonic()
    try:
        info = await hub.comfy.object_info()
        why = problems(wf, info)
        if why:                                # files downloaded since ComfyUI last listed its nodes: ask again
            why = problems(wf, await hub.comfy.object_info(max_age=0))
        if why:
            raise WorkflowRunError(f"{wf['name']} cannot run: {why}")
        uploaded: dict[str, str] = {}
        stamp = int(time.time() * 1000)
        for key, (data, name) in files.items():
            ext = Path(name).suffix.lower() or ".png"
            uploaded[key] = await hub.comfy.upload_image(data, f"hub-{stamp}-{key}{ext}",
                                                         MIMES.get(ext, "application/octet-stream"))
            names.append(uploaded[key])
        graph, used = apply(wf, values, uploaded)
        if ticket is not None:
            await ticket.switch("comfy")
        else:
            own = await hub.scheduler.acquire(owner, "workflow", "comfy", priority, label=f"workflow: {wf['name']}"[:60],
                                              max_wait=float(hub.settings.get("queue", "max_wait_s")))
        t0 = time.monotonic()
        items, texts = await hub.comfy.run_any(graph, on_progress=on_progress, timeout=7200, keep=keep)
        return {"items": items, "texts": texts, "used": used, "compute": round(time.monotonic() - t0, 1)}
    except (ComfyError, SchedulerError) as exc:
        raise WorkflowRunError(str(exc), hub=True) from exc
    finally:
        if own is not None:
            await own.release()
        hub.comfy.delete_files([("input", "", n) for n in names])     # the call's files leave ComfyUI's input
