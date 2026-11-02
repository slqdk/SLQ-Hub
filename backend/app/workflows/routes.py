"""Settings -> Models -> Workflows (0.67.0): the owner's ComfyUI workflows - upload one (the editor's JSON, the API
format, or a picture ComfyUI saved), see what it needs, fetch its model files, choose what the assistant may set.
The owner's alone, like LoRAs: the gate keeps people off these routes, and this says it again."""
from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path, PurePosixPath

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import auth, tokens
from ..catalog import GROUPS
from ..gpu.comfy import ComfyError
from ..state import get_hub
from . import convert as C
from .inspect import check, clean, folder_for, hf_source, model_sources
from .store import MAX_FILE, WorkflowStoreError

router = APIRouter(prefix="/hub/api")
SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ +\-()]*$")


def _hub(request: Request):
    if auth.is_member(getattr(request.state, "user", None)):
        raise HTTPException(403, "Workflows are for the admin only.")
    return get_hub(request)


async def _info(hub, max_age: float = 30) -> dict:
    try:
        return await hub.comfy.object_info(max_age=max_age)
    except ComfyError as exc:
        raise HTTPException(503, f"ComfyUI is not answering, so the workflow cannot be checked: {exc}")


def _dest(m: dict) -> str | None:
    """Where a missing model file goes: models/comfy/<folder>/<its name as the node names it>."""
    if not m.get("folder"):
        return None
    parts = PurePosixPath(m["file"]).parts
    if not parts or any(not SAFE_PART.match(p) or p in (".", "..") for p in parts):
        return None
    return "/".join(("comfy", m["folder"], *parts))


def _view(hub, wf: dict, info: dict | None) -> dict:
    """The workflow for the page: what it needs now (when ComfyUI answered), with each missing file's download. A
    file that is on the disk counts as there, even before ComfyUI lists it again (it lists its files every few
    minutes; a run asks it afresh)."""
    out = {k: wf.get(k) for k in ("id", "name", "description", "created", "updated", "source")}
    out["inputs"] = wf.get("inputs") or []
    if info is None:
        out.update(ready=None, unknown=[], missing=[], wrong=[], outputs=[])
        return out
    found = check(wf["prompt"], info, wf.get("sources"))
    states = {f["dest"]: f for f in hub.extras["downloads"].snapshot()["files"]}
    missing = []
    for m in found["missing"]:
        dest = _dest(m)
        if dest and hub.catalog.path({"dest": dest}).is_file():
            continue
        m["dest"] = dest
        m["download"] = states.get(dest) if dest else None
        m["can_download"] = bool(dest and m.get("url") and not _taken(hub, dest, m["url"]))
        missing.append(m)
    out.update(ready=not found["unknown"] and not missing, unknown=found["unknown"],
               missing=missing, wrong=found["wrong"], outputs=found["outputs"])
    return out


def _taken(hub, dest: str, url: str) -> bool:
    """A file of a model in Settings -> Models goes only from where that model gets it: a workflow's link may not
    put another file there (the model would count as downloaded, with the wrong file)."""
    src = hf_source(url) or {}
    for group in GROUPS:
        for e in hub.catalog.entries(group):
            for f in e.get("files") or []:
                if f.get("dest") == dest:
                    return not (f.get("repo") == src.get("repo") and f.get("path") == src.get("path"))
    return False


def _fail(exc: WorkflowStoreError):
    raise HTTPException(exc.status, str(exc))


def _store(hub):
    return hub.extras["workflows"]


@router.get("/workflows")
async def list_workflows(request: Request):
    hub = _hub(request)
    try:
        info = await hub.comfy.object_info()
    except ComfyError:
        info = None
    return {"workflows": [_view(hub, wf, info) for wf in _store(hub).list()], "comfy": info is not None}


def _name_of(filename: str) -> str:
    stem = Path(filename or "workflow").stem
    return clean(re.sub(r"[_\-]+", " ", stem), 80) or "Workflow"


@router.post("/workflows")
async def add_workflow(request: Request, file: UploadFile = File(...), name: str = Form("")):
    """A workflow from the owner's computer: the editor's JSON (as sites share them), the API format, or a picture
    ComfyUI saved with its workflow inside. Custom nodes are refused with their names: the Hub's ComfyUI has
    only its own."""
    hub = _hub(request)
    data = await file.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise HTTPException(413, "That file is over 20 MB - not a workflow.")
    info = await _info(hub)

    def read() -> tuple[str, dict, dict, dict]:
        """In a thread: a large or nested workflow takes a while, and the Hub answers everyone meanwhile."""
        kind, obj = C.parse(data)
        controls: dict = {}
        prompt = C.to_api(obj, info, controls) if kind == "editor" else obj
        sources = model_sources(obj) if kind == "editor" else {}
        return kind, prompt, sources, check(prompt, info, sources, controls)

    try:
        kind, prompt, sources, found = await asyncio.to_thread(read)
    except C.UnknownNodes as exc:
        raise HTTPException(400, f"This workflow needs nodes the Hub's ComfyUI does not have: {', '.join(exc.types[:15])}"
                                 f"{' ...' if len(exc.types) > 15 else ''}. They are custom nodes (or newer than its "
                                 "ComfyUI); the Hub runs only ComfyUI's own.")
    except C.WorkflowError as exc:
        raise HTTPException(400, f"The workflow could not be read: {exc}")
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError) as exc:
        raise HTTPException(400, f"The workflow could not be read ({exc.__class__.__name__}).")
    if found["unknown"]:
        raise HTTPException(400, "This workflow needs nodes the Hub's ComfyUI does not have: "
                                 f"{', '.join(found['unknown'][:15])}. The Hub runs only ComfyUI's own.")
    if not found["outputs"]:
        raise HTTPException(400, "This workflow saves nothing (no node that saves or shows a result).")
    wf = _store(hub).add(name=clean(name, 80) or _name_of(file.filename or ""), prompt=prompt,
                         source={"file": (file.filename or "")[:200], "format": kind}, sources=sources,
                         inputs=found["inputs"])
    return _view(hub, wf, info)


@router.get("/workflows/{wid}")
async def get_workflow(wid: str, request: Request):
    hub = _hub(request)
    try:
        wf = _store(hub).get(wid)
    except WorkflowStoreError as exc:
        _fail(exc)
    try:
        info = await hub.comfy.object_info(max_age=10)
    except ComfyError:
        info = None
    return _view(hub, wf, info)


class WorkflowPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    inputs: dict[str, dict] | None = None


@router.put("/workflows/{wid}")
async def change_workflow(wid: str, body: WorkflowPatch, request: Request):
    hub = _hub(request)
    try:
        wf = _store(hub).update(wid, name=body.name, description=body.description, inputs=body.inputs)
    except WorkflowStoreError as exc:
        _fail(exc)
    try:
        info = await hub.comfy.object_info()
    except ComfyError:
        info = None
    return _view(hub, wf, info)


@router.delete("/workflows/{wid}")
async def delete_workflow(wid: str, request: Request):
    hub = _hub(request)
    try:
        _store(hub).delete(wid)
    except WorkflowStoreError as exc:
        _fail(exc)
    hub.extras["downloads"].cancel(f"workflow:{wid}")
    return {"ok": True}


async def _size(hub, src: dict) -> int:
    """A Hugging Face file's size, from its download link's answer (no download); 0 when it does not say."""
    url = src.get("url") or f"https://huggingface.co/{src['repo']}/resolve/main/{src['path']}"
    headers = {"User-Agent": "slq-hub/1"}
    if tokens.hf():
        headers["Authorization"] = f"Bearer {tokens.hf()}"
    try:
        async with httpx.AsyncClient(timeout=20, transport=hub.transports.web, follow_redirects=False) as c:
            r = await c.head(url, headers=headers)
    except httpx.HTTPError:
        return 0
    if r.status_code not in (200, 301, 302, 303, 307, 308):
        return 0
    for header in ("x-linked-size", "content-length"):
        try:
            if r.headers.get(header):
                return int(r.headers[header])
        except ValueError:
            continue
    return 0


@router.post("/workflows/{wid}/download")
async def download_files(wid: str, request: Request):
    """The missing model files the workflow says where to fetch (Hugging Face links in its file), into
    models/comfy/<folder> - the downloads of Settings -> Models, so they resume and show progress."""
    hub = _hub(request)
    try:
        wf = _store(hub).get(wid)
    except WorkflowStoreError as exc:
        _fail(exc)
    view = _view(hub, wf, await _info(hub, max_age=0))
    files = []
    for m in view["missing"]:
        if not m.get("can_download"):
            continue
        src = hf_source(m["url"])
        if src is None or not folder_for(m["folder"] or ""):
            continue
        files.append({**src, "dest": m["dest"], "size": await _size(hub, src)})
    if not files:
        raise HTTPException(400, "None of the missing files has a Hugging Face link in the workflow: put them in "
                                 "the folder named beside each (under models/comfy on the VM).")
    hub.env.models_dir.mkdir(parents=True, exist_ok=True)
    need = sum(f["size"] for f in files)
    free = shutil.disk_usage(hub.env.models_dir).free
    if need > free - 5 * 1024**3:
        raise HTTPException(400, f"Not enough disk space: it needs {need / 1e9:.1f} GB, {free / 1e9:.1f} GB is free "
                                 "and the Hub keeps 5 GB spare.")
    queued = hub.extras["downloads"].enqueue_files(f"workflow:{wid}", files)
    return {"queued": queued, "bytes": need}


@router.post("/workflows/{wid}/cancel-download")
async def cancel_download(wid: str, request: Request):
    hub = _hub(request)
    hub.extras["downloads"].cancel(f"workflow:{wid}")
    return {"ok": True}
