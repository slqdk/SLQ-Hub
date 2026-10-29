"""Settings -> Models -> LoRAs (0.49.0): the admin's LoRAs for the picture models. Only the admin: a person's account
gets 403 here (the gate already keeps people off these routes; this says it again)."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import auth
from ..state import get_hub
from .loras import FAMILIES, ID_RE, MAX_BYTES, LoraError

router = APIRouter(prefix="/hub/api")


def _store(request: Request):
    if auth.is_member(getattr(request.state, "user", None)):
        raise HTTPException(403, "LoRAs are for the admin only.")
    return get_hub(request).extras["loras"]


def _fail(exc: LoraError):
    raise HTTPException(exc.status, str(exc))


def _with_fits(request: Request, items: list[dict]) -> list[dict]:
    """Each LoRA with the downloaded picture models it fits (by family)."""
    hub = get_hub(request)
    pipe = hub.extras.get("images")
    models = [e for e in hub.catalog.entries("comfy") if hub.catalog.installed(e)] if pipe else []
    return [{**x, "fits": [{"id": e["id"], "name": e.get("name") or e["id"]} for e in models
                           if e.get("family") == x.get("base") and not e.get("requires")]} for x in items]


@router.get("/loras")
async def list_loras(request: Request):
    store = _store(request)
    return {"loras": _with_fits(request, store.list()), "families": FAMILIES}


@router.post("/loras")
async def add_lora(request: Request, file: UploadFile = File(...), name: str = Form(""), base: str = Form(""),
                   trigger: str = Form(""), strength: float = Form(1.0), note: str = Form("")):
    """A .safetensors LoRA from the admin's computer. Its family and trigger word are read from the file when they
    are not given."""
    store = _store(request)
    filename = file.filename or "lora.safetensors"
    if not filename.lower().endswith(".safetensors"):
        raise HTTPException(400, "A LoRA is a .safetensors file.")
    store.dir.mkdir(parents=True, exist_ok=True)
    tmp = store.dir / f".upload-{secrets.token_hex(6)}.part"          # on the same disk, so it is moved, not copied
    size = 0
    try:
        with open(tmp, "wb") as out:
            while chunk := await file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, f"The file is larger than {MAX_BYTES // 1024**3} GB - not a LoRA.")
                out.write(chunk)
        try:
            entry = store.add_file(tmp, filename, name=name, base=base, trigger=trigger, strength=strength, note=note)
        except LoraError as exc:
            _fail(exc)
    finally:
        tmp.unlink(missing_ok=True)
    return _with_fits(request, [entry])[0]


class LoraPatch(BaseModel):
    name: str | None = None
    base: str | None = None
    trigger: str | None = None
    strength: float | None = None
    note: str | None = None


@router.patch("/loras/{lid}")
async def change_lora(lid: str, body: LoraPatch, request: Request):
    store = _store(request)
    if not ID_RE.match(lid):
        raise HTTPException(404, "No such LoRA.")
    try:
        entry = store.update(lid, **body.model_dump())
    except LoraError as exc:
        _fail(exc)
    return _with_fits(request, [entry])[0]


@router.delete("/loras/{lid}")
async def delete_lora(lid: str, request: Request):
    store = _store(request)
    if not ID_RE.match(lid):
        raise HTTPException(404, "No such LoRA.")
    try:
        store.delete(lid)
    except LoraError as exc:
        _fail(exc)
    return {"ok": True}
