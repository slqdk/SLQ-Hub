"""Personas (0.74.0): someone to chat with - who they are, how they talk, their picture, voice and memory. The
owner's alone - a person's account gets 403 (the gate keeps people off these routes already)."""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import auth
from ..audio.clips import MAX_UPLOAD as MAX_VOICE_UPLOAD
from ..state import get_hub
from .store import MAX_PICTURE, PersonaError

router = APIRouter(prefix="/hub/api/personas")


def _p(request: Request):
    if auth.is_member(getattr(request.state, "user", None)):
        raise HTTPException(403, "Personas are for the admin only.")
    return get_hub(request).extras["personas"]


def _fail(exc: PersonaError):
    raise HTTPException(exc.status, str(exc))


class PersonaIn(BaseModel):
    name: str | None = None
    role: str | None = None
    personality: str | None = None
    speech: str | None = None
    examples: str | None = None
    greeting: str | None = None
    language: str | None = None
    model: str | None = None
    level: str | None = None
    look: str | None = None
    lora: str | None = None
    strength: float | None = None
    voice: str | None = None
    expressiveness: float | None = None
    web: bool | None = None
    pictures: bool | None = None
    memory: bool | None = None
    read_aloud: bool | None = None


@router.get("")
async def overview(request: Request):
    p = _p(request)
    return {"personas": p.list(), "options": p.options()}


@router.post("")
async def create(body: PersonaIn, request: Request):
    try:
        return _p(request).create(body.model_dump(exclude_none=True))
    except PersonaError as exc:
        _fail(exc)


@router.get("/{pid}")
async def detail(pid: str, request: Request):
    try:
        return _p(request).detail(pid)
    except PersonaError as exc:
        _fail(exc)


@router.patch("/{pid}")
async def change(pid: str, body: PersonaIn, request: Request):
    try:
        return await _p(request).update(pid, body.model_dump(exclude_none=True))
    except PersonaError as exc:
        _fail(exc)


@router.delete("/{pid}")
async def delete(pid: str, request: Request):
    try:
        _p(request).delete(pid)
    except PersonaError as exc:
        _fail(exc)
    return {"ok": True}


@router.post("/{pid}/chat")
async def chat(pid: str, request: Request):
    """A new chat with the persona."""
    try:
        return {"chat": _p(request).start_chat(pid)}
    except PersonaError as exc:
        _fail(exc)


@router.post("/{pid}/picture")
async def picture(pid: str, request: Request, file: UploadFile = File(...)):
    """A picture of the owner's as the persona's."""
    p = _p(request)
    data = await file.read(MAX_PICTURE + 1)
    try:
        return await p.set_picture(pid, data)
    except PersonaError as exc:
        _fail(exc)


@router.post("/{pid}/portraits")
async def portraits(pid: str, request: Request):
    """Four portraits from how the persona looks (and its LoRA), with the picture model."""
    p = _p(request)
    try:
        p.make_portrait(pid)
        return p.detail(pid)
    except PersonaError as exc:
        _fail(exc)


@router.post("/{pid}/portraits/cancel")
async def cancel(pid: str, request: Request):
    p = _p(request)
    try:
        p.load(pid)
        await p.cancel(pid)
        return p.detail(pid)
    except PersonaError as exc:
        _fail(exc)


class PickIn(BaseModel):
    file: str


@router.post("/{pid}/pick")
async def pick(pid: str, body: PickIn, request: Request):
    try:
        return await _p(request).pick(pid, body.file)
    except PersonaError as exc:
        _fail(exc)


@router.post("/{pid}/voice")
async def voice_clip(pid: str, request: Request, file: UploadFile = File(...)):
    """A clip of a real voice (5-20 s of one person speaking) as the persona's."""
    p = _p(request)
    data = await file.read(MAX_VOICE_UPLOAD + 1)
    try:
        return await p.set_voice_clip(pid, data, file.filename or "")
    except PersonaError as exc:
        _fail(exc)


class NotesIn(BaseModel):
    text: str
    base: str | None = None              # the notes the edit started from: changed meanwhile -> 409


@router.put("/{pid}/notes")
async def notes(pid: str, body: NotesIn, request: Request):
    """What the persona remembers about the owner, as the owner corrects it."""
    try:
        return await _p(request).set_notes(pid, body.text, body.base)
    except PersonaError as exc:
        _fail(exc)


class SayIn(BaseModel):
    chat_id: int
    message_id: int


@router.post("/{pid}/say")
async def say(pid: str, body: SayIn, request: Request):
    """One of the persona's answers read aloud -> {"path"} to play, or {"making": true}: ask again in a moment."""
    try:
        return _p(request).say(pid, body.chat_id, body.message_id)
    except PersonaError as exc:
        _fail(exc)


@router.post("/{pid}/sample")
async def sample(pid: str, request: Request):
    """How the persona sounds -> {"path"} to play, or {"making": true}."""
    try:
        return _p(request).sample(pid)
    except PersonaError as exc:
        _fail(exc)


@router.get("/{pid}/file")
async def file(pid: str, path: str, request: Request):
    try:
        return FileResponse(_p(request).file(pid, path), headers={"Cache-Control": "private, max-age=31536000, immutable",
                                                                   "X-Content-Type-Options": "nosniff"})
    except PersonaError as exc:
        _fail(exc)
