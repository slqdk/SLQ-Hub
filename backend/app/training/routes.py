"""Settings -> Models -> LoRAs -> Train (0.50.0): LoRA training runs. The admin's alone - a person's account gets 403
(the gate keeps people off these routes already)."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import auth
from ..state import get_hub
from .runs import BASE, KINDS, MAX_IMAGES, MIN_IMAGES, TrainingError

router = APIRouter(prefix="/hub/api/training")
MAX_UPLOAD = 60 * 1024 * 1024            # per picture


def _t(request: Request):
    if auth.is_member(getattr(request.state, "user", None)):
        raise HTTPException(403, "Training is for the admin only.")
    return get_hub(request).extras["training"]


def _fail(exc: TrainingError):
    raise HTTPException(exc.status, str(exc))


@router.get("")
async def overview(request: Request):
    t = _t(request)
    # hf_token (0.61.0): FLUX.2 klein 9B is gated on Hugging Face - the page says so before a start fails on it
    return {"service": await t.service(), "runs": t.list(), "base": BASE, "bases": t.bases(), "kinds": list(KINDS),
            "min_images": MIN_IMAGES, "max_images": MAX_IMAGES, "can_caption": t.captioner() is not None,
            "hf_token": bool(os.environ.get("HF_TOKEN"))}


class RunIn(BaseModel):
    name: str
    trigger: str
    kind: str = "thing"
    steps: int = 1500
    rank: int = 16
    base: str | None = None          # "flux2-klein-4b", "flux2-klein-9b" (0.61.0) or "sdxl:<an SDXL model's id>"


@router.post("/runs")
async def create_run(body: RunIn, request: Request):
    try:
        return _t(request).create(body.name, body.trigger, body.kind, body.steps, body.rank, body.base)
    except TrainingError as exc:
        _fail(exc)


@router.get("/runs/{rid}")
async def get_run(rid: str, request: Request):
    try:
        return _t(request).detail(rid)
    except TrainingError as exc:
        _fail(exc)


class RunPatch(BaseModel):
    name: str | None = None
    steps: int | None = None
    rank: int | None = None


@router.patch("/runs/{rid}")
async def change_run(rid: str, body: RunPatch, request: Request):
    try:
        return _t(request).update(rid, **body.model_dump())
    except TrainingError as exc:
        _fail(exc)


class DuplicateIn(BaseModel):
    name: str | None = None
    trigger: str | None = None
    kind: str | None = None
    base: str | None = None       # 0.60.0: train the copy on another model (a klein size or "sdxl:<id>")


@router.post("/runs/{rid}/duplicate")
async def duplicate_run(rid: str, body: DuplicateIn, request: Request):
    try:
        return _t(request).duplicate(rid, body.name or None, body.trigger or None, body.kind or None,
                                     body.base or None)
    except TrainingError as exc:
        _fail(exc)


@router.delete("/runs/{rid}")
async def delete_run(rid: str, request: Request):
    try:
        _t(request).delete(rid)
    except TrainingError as exc:
        _fail(exc)
    return {"ok": True}


@router.post("/runs/{rid}/images")
async def add_images(rid: str, request: Request):
    t = _t(request)
    form = await request.form()
    files: list[tuple[str, bytes]] = []
    for f in form.getlist("files"):
        if isinstance(f, UploadFile) or hasattr(f, "read"):
            data = await f.read()
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, f"{f.filename} is larger than {MAX_UPLOAD // 1024**2} MB.")
            files.append((f.filename or "picture.jpg", data))
    if not files:
        raise HTTPException(400, "No pictures.")
    try:
        return {"added": t.add_images(rid, files)}
    except TrainingError as exc:
        _fail(exc)


@router.delete("/runs/{rid}/images/{name}")
async def remove_image(rid: str, name: str, request: Request):
    try:
        _t(request).remove_image(rid, name)
    except TrainingError as exc:
        _fail(exc)
    return {"ok": True}


class CaptionsIn(BaseModel):
    captions: dict[str, str]


@router.put("/runs/{rid}/captions")
async def set_captions(rid: str, body: CaptionsIn, request: Request):
    try:
        _t(request).set_captions(rid, body.captions)
    except TrainingError as exc:
        _fail(exc)
    return {"ok": True}


class AutoIn(BaseModel):
    overwrite: bool = False


@router.post("/runs/{rid}/captions/auto")
async def auto_captions(rid: str, body: AutoIn, request: Request):
    try:
        await _t(request).write_captions(rid, body.overwrite)
    except TrainingError as exc:
        _fail(exc)
    return {"ok": True}


@router.post("/runs/{rid}/start")
async def start(rid: str, request: Request):
    try:
        return await _t(request).start(rid)
    except TrainingError as exc:
        _fail(exc)


@router.post("/runs/{rid}/continue")
async def resume(rid: str, request: Request):
    """Go on from the newest checkpoint: a stopped run, or a finished one given more steps."""
    try:
        return await _t(request).start(rid, resume=True)
    except TrainingError as exc:
        _fail(exc)


@router.post("/runs/{rid}/cancel")
async def cancel(rid: str, request: Request):
    try:
        await _t(request).cancel(rid)
    except TrainingError as exc:
        _fail(exc)
    return {"ok": True}


class UseIn(BaseModel):
    step: int | None = None


@router.post("/runs/{rid}/use")
async def use(rid: str, body: UseIn, request: Request):
    try:
        return _t(request).use(rid, body.step)
    except TrainingError as exc:
        _fail(exc)


@router.post("/runs/{rid}/free")
async def free_space(rid: str, request: Request):
    try:
        return {"freed": _t(request).free_space(rid)}
    except TrainingError as exc:
        _fail(exc)


@router.get("/bases/{base}/files")
async def base_files(base: str, request: Request):
    """The files a klein base's training reads, and which are here (0.62.0: they can be uploaded instead)."""
    try:
        return {"files": await _t(request).base_files(base)}
    except TrainingError as exc:
        _fail(exc)


class BaseFileIn(BaseModel):
    upload: str                          # a finished upload (app/uploads.py)
    repo: str
    path: str


@router.post("/bases/{base}/files")
async def put_base_file(base: str, body: BaseFileIn, request: Request):
    """An uploaded file as one of a klein base's training files, where the download would have put it."""
    t = _t(request)
    try:
        return await t.place_base_file(base, body.repo, body.path, get_hub(request).extras["uploads"], body.upload)
    except TrainingError as exc:
        _fail(exc)


@router.get("/runs/{rid}/file")
async def file(rid: str, path: str, request: Request):
    try:
        return FileResponse(_t(request).file(rid, path), headers={"Cache-Control": "private, max-age=3600"})
    except TrainingError as exc:
        _fail(exc)
