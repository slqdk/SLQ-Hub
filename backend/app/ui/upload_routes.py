"""Settings -> Models -> Upload (0.62.0, Søren: "så jeg selv kan uploade modeller fra min pc"): model files from the
owner's computer, sent in pieces (app/uploads.py), made into

* a language model - a .gguf, with a vision projector (mmproj) beside it if it has one;
* a picture model - an SDXL checkpoint (.safetensors), with its kind's settings as from a link;
* the missing file of a model in the Hub's own list - klein 9B's, say, instead of a gated download.

What a file is, is read from its own header (inspect) as soon as that has arrived, so a wrong file is said after the
first piece, not after 20 GB. The owner's alone: people never reach /hub/api/model-uploads or these model routes
(gate.MEMBER_ROUTES)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import gguf, hf
from ..catalog import GROUPS
from ..images import sources
from ..images.loras import LoraError, is_lora, read_header
from ..state import get_hub
from ..uploads import UploadError, Uploads

router = APIRouter()


def _ups(request: Request) -> Uploads:
    return get_hub(request).extras["uploads"]


def _fail(exc: Exception):
    raise HTTPException(getattr(exc, "status", 400), str(exc))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._]+", "-", (text or "").lower()).strip("-._")[:60] or "model"


def _free_id(hub, want: str) -> str:
    taken = {e["id"] for g in GROUPS for e in hub.catalog.entries(g)}
    mid, n = want, 2
    while mid in taken:
        mid = f"{want}-{n}"
        n += 1
    return mid


def _check_id(hub, model_id: str) -> str:
    model_id = (model_id or "").strip()
    if not hf.ID_RE.match(model_id):
        raise HTTPException(400, "The name for apps may use a-z, 0-9, dots, dashes and underscores (up to 63).")
    if any(e["id"] == model_id for g in GROUPS for e in hub.catalog.entries(g)):
        raise HTTPException(400, f"There is already a model called {model_id}.")
    return model_id


# ---------------------------------------------------------------- the pieces

class UploadIn(BaseModel):
    name: str
    size: int
    modified: int | None = None          # the file's date on the computer (ms): the same file again is resumed


@router.get("/hub/api/model-uploads")
async def list_uploads(request: Request):
    return {"uploads": _ups(request).list()}


@router.post("/hub/api/model-uploads")
async def create_upload(body: UploadIn, request: Request):
    """A new upload, or the one of this file begun before (its `received` says where to go on)."""
    try:
        return _ups(request).create(body.name, body.size, body.modified)
    except UploadError as exc:
        _fail(exc)


@router.get("/hub/api/model-uploads/{uid}")
async def get_upload(uid: str, request: Request):
    try:
        return _ups(request).get(uid)
    except UploadError as exc:
        _fail(exc)


@router.put("/hub/api/model-uploads/{uid}")
async def put_piece(uid: str, offset: int, request: Request):
    """One piece of the file, from `offset` (where the Hub's copy ends now; 409 says where it does)."""
    try:
        return await _ups(request).append(uid, offset, request.stream())
    except UploadError as exc:
        _fail(exc)


@router.delete("/hub/api/model-uploads/{uid}")
async def delete_upload(uid: str, request: Request):
    try:
        _ups(request).delete(uid)
    except UploadError as exc:
        _fail(exc)
    return {"ok": True}


def _safetensors_head(ups: Uploads, uid: str) -> dict | None:
    """A .safetensors file's header from the bytes that have arrived - None until all of it has."""
    head = ups.head(uid, 8)
    if len(head) < 8:
        return None
    n = int.from_bytes(head, "little")
    if not 2 <= n <= sources.HEADER_MAX:
        raise UploadError("That is not a .safetensors file the Hub can read.")
    raw = ups.head(uid, 8 + n)
    if len(raw) < 8 + n:
        return None
    try:
        header = json.loads(raw[8:])
    except ValueError:
        raise UploadError("That is not a .safetensors file the Hub can read.")
    if not isinstance(header, dict):
        raise UploadError("That is not a .safetensors file the Hub can read.")
    return header


@router.get("/hub/api/model-uploads/{uid}/inspect")
async def inspect(uid: str, request: Request):
    """What the file is, from its own header - asked for while it arrives: {"pending": true} until enough has.
    A .gguf: a language model or a vision projector, its architecture, size and what its chat template supports.
    A .safetensors: an SDXL checkpoint the Hub can run (or why not), and its kind."""
    hub, ups = get_hub(request), _ups(request)
    try:
        m = ups.get(uid)
        name = m["name"]
        stem = re.sub(r"(?i)\.(gguf|safetensors)$", "", name)
        if name.lower().endswith(".gguf"):
            facts = await asyncio.to_thread(gguf.describe, ups.part(uid))
            if facts is None:
                if m["received"] < 24 and not m["done"]:
                    return {"pending": True}
                return {"kind": "unknown", "reason": "That is not a GGUF file the Hub can read."}
            if facts.get("partial"):
                return {"pending": True}
            if gguf.is_projector(facts):
                return {"kind": "mmproj", "architecture": facts.get("architecture"), "name": facts.get("name")}
            tpl = str(facts.get("chat_template") or "")
            label = facts.get("name") or stem
            return {"kind": "gguf", "architecture": facts.get("architecture"), "name": facts.get("name"),
                    "parameters": facts.get("parameters"), "context_length": facts.get("context_length"),
                    "quantization": hf.quant_label(stem), "tools": ("tools" in tpl) if tpl else None,
                    "thinking": ("enable_thinking" in tpl) if tpl else None,
                    "suggested": {"id": _free_id(hub, _slug(stem)), "name": str(label)[:80]}}
        if name.lower().endswith(".safetensors"):
            header = await asyncio.to_thread(_safetensors_head, ups, uid)
            if header is None:
                return {"pending": True}
            ok, note = sources.classify(header)
            meta = " ".join(str(v) for v in (header.get("__metadata__") or {}).values())[:2000]
            return {"kind": "sdxl" if ok else ("lora" if is_lora(header) else "safetensors"), "supported": ok,
                    "reason": "" if ok else note, "vpred": ok and note == "v-prediction",
                    "preset": sources.preset_for(f"{name} {meta}"),
                    "suggested": {"id": _free_id(hub, _slug(stem)), "name": stem.replace("_", " ")[:80]}}
        return {"kind": "other"}
    except UploadError as exc:
        _fail(exc)


# ---------------------------------------------------------------- into models

class LocalLlm(BaseModel):
    upload: str
    mmproj: str | None = None            # an upload with the vision projector
    id: str
    name: str = ""
    ctx: int = 32768
    tools: bool = True
    thinking: bool = False


@router.post("/hub/api/models/local")
async def add_language_model(body: LocalLlm, request: Request):
    """An uploaded .gguf (and its vision projector) as a language model, like one added from Hugging Face."""
    hub, ups = get_hub(request), _ups(request)
    model_id = _check_id(hub, body.id)
    try:
        m = ups.done(body.upload)
        if not m["name"].lower().endswith(".gguf"):
            raise UploadError("A language model is a .gguf file.")
        facts = await asyncio.to_thread(gguf.describe, ups.part(body.upload))
        if not facts or facts.get("partial") or not facts.get("architecture"):
            raise UploadError(f"{m['name']} is not a GGUF model the Hub can read.")
        if gguf.is_projector(facts):
            raise UploadError(f"{m['name']} is a vision projector (mmproj): give it under Pictures (vision), with the "
                              "model's own .gguf above.")
        mm = None
        if body.mmproj:
            mm = ups.done(body.mmproj)
            if not gguf.is_projector(await asyncio.to_thread(gguf.describe, ups.part(body.mmproj))):
                raise UploadError(f"{mm['name']} is not a vision projector (an mmproj .gguf).")
        limit = int(facts.get("context_length") or 262144)
        ctx = max(2048, min(int(body.ctx), limit))
        entry = hf.make_local_entry(model_id, body.name, m["name"], m["size"],
                                    (mm["name"], mm["size"]) if mm else None, ctx, body.tools, body.thinking, facts)
    except (UploadError, hf.HFError) as exc:
        _fail(exc)
    return _place(hub, ups, entry, "llm", [body.upload] + ([body.mmproj] if body.mmproj else []))


class LocalImage(BaseModel):
    upload: str
    id: str
    name: str = ""
    settings: dict = {}


@router.post("/hub/api/models/images/local")
async def add_picture_model(body: LocalImage, request: Request):
    """An uploaded SDXL checkpoint as a picture model, with its kind's settings - as one added from a link."""
    hub, ups = get_hub(request), _ups(request)
    model_id = _check_id(hub, body.id)
    try:
        m = ups.done(body.upload)
        if not m["name"].lower().endswith(".safetensors"):
            raise UploadError("A picture model is a .safetensors checkpoint.")
        header = await asyncio.to_thread(read_header, ups.part(body.upload))
        ok, note = sources.classify(header)
        if not ok:
            raise UploadError(note)
        entry = sources.make_local_entry(model_id, body.name, sources.clean_settings(body.settings), m["name"],
                                         m["size"])
    except (UploadError, LoraError, sources.SourceError) as exc:
        _fail(exc)
    return _place(hub, ups, entry, "comfy", [body.upload])


def _place(hub, ups: Uploads, entry: dict, group: str, uids: list[str]) -> dict:
    """The uploads into the entry's places (in its files' order), then the entry into the catalog - or, if that
    fails, the files back where they were, to try again."""
    placed: list[tuple[dict, Path]] = []
    try:
        for uid, f in zip(uids, entry["files"], strict=True):
            dest = hub.catalog.path(f)
            placed.append((ups.take(uid, dest), dest))
        added = hub.catalog.add_custom(entry, group)
    except (UploadError, ValueError) as exc:
        for m, dest in reversed(placed):
            ups.give_back(m, dest)
        _fail(exc)
    return {"model": added}


class CatalogFile(BaseModel):
    upload: str
    dest: str                            # which of the model's files it is


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e8 else f"{n / 1e6:.1f} MB"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(8 * 1024 * 1024):
            h.update(block)
    return h.hexdigest()


@router.post("/hub/api/models/{model_id}/files")
async def upload_model_file(model_id: str, body: CatalogFile, request: Request):
    """A file of a model in the Hub's list, from the computer instead of a download: checked against what the
    catalog says of it (its size - some are rounded there, so within 3% - its kind, and its SHA-256 when known),
    then put where a download would have put it. A file several models share serves them all."""
    hub, ups = get_hub(request), _ups(request)
    found = next(((e, g) for g in GROUPS for e in hub.catalog.entries(g) if e["id"] == model_id), None)
    if found is None:
        raise HTTPException(404, "No such model.")
    entry, _group = found
    f = next((x for x in entry.get("files") or [] if x.get("dest") == body.dest), None)
    if f is None:
        raise HTTPException(404, f"{model_id} has no file {body.dest}.")
    dest = hub.catalog.path(f)
    if not dest.resolve().is_relative_to(hub.env.models_dir.resolve()):
        raise HTTPException(400, "That file lies outside the models folder.")
    if dest.is_file():
        raise HTTPException(409, f"{dest.name} is there already.")
    st = (hub.extras["downloads"].state.get(body.dest) or {}).get("status")
    if st in ("downloading", "queued"):
        raise HTTPException(409, f"{dest.name} is downloading now: stop the download first.")
    try:
        m = ups.done(body.upload)
        want = int(f.get("size") or 0)
        if want and abs(m["size"] - want) > max(want * 0.03, 2_000_000):
            raise UploadError(f"{m['name']} is {_gb(m['size'])}, but {dest.name} is about {_gb(want)}. Is it the "
                              "right file?")
        part = ups.part(body.upload)
        if dest.suffix.lower() == ".safetensors":
            await asyncio.to_thread(read_header, part)
        elif dest.suffix.lower() == ".gguf" and ups.head(body.upload, 4) != b"GGUF":
            raise UploadError(f"{m['name']} is not a GGUF file.")
        if f.get("sha256") and (await asyncio.to_thread(_sha256, part)).lower() != str(f["sha256"]).lower():
            raise UploadError(f"{m['name']} is not the file the catalog names: its SHA-256 differs.")
        ups.take(body.upload, dest)
    except (UploadError, LoraError) as exc:
        _fail(exc)
    entry = hub.catalog.get(_group, model_id) or entry
    return {"installed": hub.catalog.installed(entry), "dest": body.dest}
