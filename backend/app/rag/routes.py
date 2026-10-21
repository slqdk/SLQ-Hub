"""/hub/api/libraries - documents to search."""
from __future__ import annotations

import mimetypes

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..state import get_hub
from .extract import Unsupported
from .library import LibraryError

router = APIRouter(prefix="/hub/api/libraries")
MAX_DOC = 300 * 1024 * 1024


def _libs(request: Request):
    return get_hub(request).extras["libraries"]


@router.get("")
async def list_libraries(request: Request):
    libs = _libs(request)
    return {"libraries": libs.list(), "available": libs.available(), "indexing": libs.current and
            {"doc_id": libs.current["id"], "filename": libs.current["filename"]}}


class LibraryIn(BaseModel):
    name: str
    description: str = ""


@router.post("")
async def create_library(body: LibraryIn, request: Request):
    try:
        return _libs(request).create(body.name, body.description)
    except LibraryError as exc:
        raise HTTPException(400, str(exc))


class LibraryPatch(BaseModel):
    name: str | None = None
    description: str | None = None


@router.patch("/{lib_id}")
async def patch_library(lib_id: int, body: LibraryPatch, request: Request):
    lib = _libs(request).update(lib_id, body.name, body.description)
    if not lib:
        raise HTTPException(404, "no such library")
    return lib


@router.delete("/{lib_id}")
async def delete_library(lib_id: int, request: Request):
    if not _libs(request).delete(lib_id):
        raise HTTPException(404, "no such library")
    return {"ok": True}


@router.get("/{lib_id}/docs")
async def docs(lib_id: int, request: Request):
    libs = _libs(request)
    if not libs.get(lib_id):
        raise HTTPException(404, "no such library")
    return libs.docs(lib_id)


@router.post("/{lib_id}/docs")
async def add_docs(lib_id: int, request: Request, files: list[UploadFile] = File(...)):
    libs = _libs(request)
    if not libs.get(lib_id):
        raise HTTPException(404, "no such library")
    added, refused = [], []
    for f in files:
        data = await f.read(MAX_DOC + 1)
        name = (f.filename or "document").replace("\\", "/").split("/")[-1][:200]
        if len(data) > MAX_DOC:
            refused.append({"filename": name, "error": "larger than 300 MB"})
            continue
        mime = f.content_type if f.content_type and f.content_type != "application/octet-stream" else \
            (mimetypes.guess_type(name)[0] or "application/octet-stream")
        try:
            added.append(libs.add(lib_id, data, name, mime))
        except (Unsupported, LibraryError) as exc:
            refused.append({"filename": name, "error": str(exc)})
    return {"added": added, "refused": refused}


@router.delete("/{lib_id}/docs/{doc_id}")
async def remove_doc(lib_id: int, doc_id: int, request: Request):
    libs = _libs(request)
    d = libs.doc(doc_id)
    if not d or d["library_id"] != lib_id:
        raise HTTPException(404, "no such document")
    libs.remove(doc_id)
    return {"ok": True}


@router.post("/{lib_id}/docs/{doc_id}/reindex")
async def reindex(lib_id: int, doc_id: int, request: Request):
    libs = _libs(request)
    d = libs.doc(doc_id)
    if not d or d["library_id"] != lib_id:
        raise HTTPException(404, "no such document")
    libs.reindex(doc_id)
    return {"ok": True}


@router.get("/{lib_id}/search")
async def search(lib_id: int, q: str, request: Request, k: int = 8):
    libs = _libs(request)
    if not libs.get(lib_id):
        raise HTTPException(404, "no such library")
    try:
        hits = await libs.search([lib_id], q, k)
    except LibraryError as exc:
        raise HTTPException(400, str(exc))
    return [{"doc_id": h.doc_id, "filename": h.filename, "page": h.page, "text": h.text, "score": h.score,
             "image": h.image, "via": h.via} for h in hits]
