"""Movies (0.72.0): series, their cast and sets, episodes and their shots, and the jobs that make the pictures,
storyboards, clips and previews - and since 0.73.0 the sound (voices, the lines spoken, effects, the theme) and the
cut. The admin's alone - a person's account gets 403 (the gate keeps people off these routes already)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import auth
from ..state import get_hub
from ..audio.clips import MAX_UPLOAD as MAX_VOICE_UPLOAD
from .studio import MovieError

router = APIRouter(prefix="/hub/api/movies")
KINDS = ("cast", "sets")


def _m(request: Request):
    if auth.is_member(getattr(request.state, "user", None)):
        raise HTTPException(403, "Movies are for the admin only.")
    return get_hub(request).extras["movies"]


def _fail(exc: MovieError):
    raise HTTPException(exc.status, str(exc))


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(404, "Not found.")
    return kind


@router.get("")
async def overview(request: Request):
    m = _m(request)
    return {"series": m.list(), "options": m.options()}


class NameIn(BaseModel):
    name: str


@router.post("")
async def create(body: NameIn, request: Request):
    try:
        return _m(request).create(body.name)
    except MovieError as exc:
        _fail(exc)


@router.get("/{sid}")
async def detail(sid: str, request: Request):
    try:
        return _m(request).detail(sid)
    except MovieError as exc:
        _fail(exc)


class SeriesPatch(BaseModel):
    name: str | None = None
    style: dict[str, Any] | None = None
    theme: dict[str, Any] | None = None     # its words and length (0.73.0)


@router.patch("/{sid}")
async def change(sid: str, body: SeriesPatch, request: Request):
    try:
        return await _m(request).update(sid, body.name, body.style, body.theme)
    except MovieError as exc:
        _fail(exc)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise HTTPException(400, f"Not a setting it can take: {exc}") from exc


@router.delete("/{sid}")
async def delete(sid: str, request: Request):
    try:
        _m(request).delete(sid)
    except MovieError as exc:
        _fail(exc)
    return {"ok": True}


class PickIn(BaseModel):
    file: str = ""
    keep: bool = False                  # keep the chosen one as it is, after a change


class EpisodeIn(BaseModel):
    title: str = ""
    premise: str = ""


@router.post("/{sid}/episodes")
async def add_episode(sid: str, body: EpisodeIn, request: Request):
    try:
        return await _m(request).add_episode(sid, body.title, body.premise)
    except MovieError as exc:
        _fail(exc)


class EpisodePatch(BaseModel):
    title: str | None = None
    premise: str | None = None
    approved: bool | None = None        # the storyboard's checkpoint
    cut: dict[str, Any] | None = None   # the cut's settings (0.73.0)


@router.patch("/{sid}/episodes/{eid}")
async def change_episode(sid: str, eid: str, body: EpisodePatch, request: Request):
    try:
        return await _m(request).change_episode(sid, eid, body.title, body.premise, body.approved, body.cut)
    except MovieError as exc:
        _fail(exc)


@router.delete("/{sid}/episodes/{eid}")
async def delete_episode(sid: str, eid: str, request: Request):
    try:
        return await _m(request).delete_episode(sid, eid)
    except MovieError as exc:
        _fail(exc)


class StoryboardIn(BaseModel):
    replace: bool = False               # the episode's shots go, with their pictures and clips


@router.post("/{sid}/episodes/{eid}/storyboard")
async def storyboard(sid: str, eid: str, body: StoryboardIn, request: Request):
    m = _m(request)
    try:
        m.write_storyboard(sid, eid, body.replace)
        return m.detail(sid)
    except MovieError as exc:
        _fail(exc)


class ShotAdd(BaseModel):
    after: str | None = None


@router.post("/{sid}/episodes/{eid}/shots")
async def add_shot(sid: str, eid: str, body: ShotAdd, request: Request):
    try:
        return await _m(request).add_shot(sid, eid, body.after)
    except MovieError as exc:
        _fail(exc)


class ShotPatch(BaseModel):
    seconds: float | None = None
    set: str | None = None
    characters: list[str] | None = None
    method: str | None = None
    picture: str | None = None
    motion: str | None = None
    sound: str | None = None
    lines: list[dict[str, str]] | None = None
    move: int | None = None             # -1: earlier, 1: later
    toggle_character: str | None = None   # one character in or out, whatever else changes meanwhile


@router.patch("/{sid}/episodes/{eid}/shots/{hid}")
async def change_shot(sid: str, eid: str, hid: str, body: ShotPatch, request: Request):
    try:
        return await _m(request).change_shot(sid, eid, hid, body.model_dump())
    except MovieError as exc:
        _fail(exc)


@router.delete("/{sid}/episodes/{eid}/shots/{hid}")
async def delete_shot(sid: str, eid: str, hid: str, request: Request):
    try:
        return await _m(request).delete_shot(sid, eid, hid)
    except MovieError as exc:
        _fail(exc)


@router.post("/{sid}/episodes/{eid}/shots/{hid}/pick")
async def pick_keyframe(sid: str, eid: str, hid: str, body: PickIn, request: Request):
    try:
        return await _m(request).pick_keyframe(sid, eid, hid, body.file, body.keep)
    except MovieError as exc:
        _fail(exc)


class ClipIn(BaseModel):
    kept: bool = True                   # the clip's checkpoint
    take: str = ""                      # go back to an earlier take
    accept: bool = False                # keep a clip made before a change as it is


@router.post("/{sid}/episodes/{eid}/shots/{hid}/clip")
async def keep_clip(sid: str, eid: str, hid: str, body: ClipIn, request: Request):
    try:
        return await _m(request).keep_clip(sid, eid, hid, body.kept, body.take, body.accept)
    except MovieError as exc:
        _fail(exc)


class ShotsIn(BaseModel):
    shots: list[str] | None = None      # these shots; none: each that needs it


@router.post("/{sid}/episodes/{eid}/keyframes")
async def keyframes(sid: str, eid: str, body: ShotsIn, request: Request):
    m = _m(request)
    try:
        m.make_keyframes(sid, eid, body.shots)
        return m.detail(sid)
    except MovieError as exc:
        _fail(exc)


@router.post("/{sid}/episodes/{eid}/clips")
async def clips(sid: str, eid: str, body: ShotsIn, request: Request):
    m = _m(request)
    try:
        m.make_clips(sid, eid, body.shots)
        return m.detail(sid)
    except MovieError as exc:
        _fail(exc)


@router.post("/{sid}/episodes/{eid}/preview")
async def preview(sid: str, eid: str, request: Request):
    m = _m(request)
    try:
        m.make_preview(sid, eid)
        return m.detail(sid)
    except MovieError as exc:
        _fail(exc)


# ---------------------------------------------------------------- sound and the cut (0.73.0)

class SpeakIn(BaseModel):
    shots: list[str] | None = None      # every line of these shots; none: each line not spoken or out of date
    line: str | None = None             # one line (its id) of the one shot named: another take


@router.post("/{sid}/episodes/{eid}/voices")
async def voices(sid: str, eid: str, body: SpeakIn, request: Request):
    m = _m(request)
    try:
        m.make_voices(sid, eid, body.shots, body.line)
        return m.detail(sid)
    except MovieError as exc:
        _fail(exc)


@router.post("/{sid}/episodes/{eid}/sounds")
async def sounds(sid: str, eid: str, body: ShotsIn, request: Request):
    m = _m(request)
    try:
        m.make_sounds(sid, eid, body.shots)
        return m.detail(sid)
    except MovieError as exc:
        _fail(exc)


@router.post("/{sid}/episodes/{eid}/cut")
async def cut(sid: str, eid: str, request: Request):
    m = _m(request)
    try:
        m.make_cut(sid, eid)
        return m.detail(sid)
    except MovieError as exc:
        _fail(exc)


class LineAt(BaseModel):
    line: str                           # its id
    at: float | None = None             # seconds into the shot; none: right after the line before it


@router.post("/{sid}/episodes/{eid}/shots/{hid}/line")
async def line_at(sid: str, eid: str, hid: str, body: LineAt, request: Request):
    try:
        return await _m(request).set_line_at(sid, eid, hid, body.line, body.at)
    except MovieError as exc:
        _fail(exc)


class KeepIn(BaseModel):
    what: str                           # "lines" or "effects"
    shot: str | None = None             # one shot's; none: each in the episode
    line: str | None = None             # one line (its id)


@router.post("/{sid}/episodes/{eid}/keep")
async def keep_sound(sid: str, eid: str, body: KeepIn, request: Request):
    """Keep lines or effects made before a change as they are."""
    try:
        return await _m(request).keep_sound(sid, eid, body.what, body.shot, body.line)
    except MovieError as exc:
        _fail(exc)


@router.post("/{sid}/theme/make")
async def make_theme(sid: str, request: Request):
    m = _m(request)
    try:
        m.make_theme(sid)
        return m.detail(sid)
    except MovieError as exc:
        _fail(exc)


@router.post("/{sid}/theme/pick")
async def pick_theme(sid: str, body: PickIn, request: Request):
    try:
        return await _m(request).pick_theme(sid, body.file, body.keep)
    except MovieError as exc:
        _fail(exc)


@router.post("/{sid}/cast/{cid}/voice")
async def voice_clip(sid: str, cid: str, request: Request, file: UploadFile = File(...)):
    """A clip of a real voice (5-20 s of one person speaking) for a character."""
    m = _m(request)
    data = await file.read(MAX_VOICE_UPLOAD + 1)
    try:
        return await m.set_voice_clip(sid, cid, data, file.filename or "")
    except MovieError as exc:
        _fail(exc)


class JobIn(BaseModel):
    key: str                            # "keyframes:<episode>", "cast:<character>", ...


@router.post("/{sid}/jobs/cancel")
async def cancel(sid: str, body: JobIn, request: Request):
    m = _m(request)
    try:
        m.load(sid)
        await m.cancel(sid, body.key)
        return m.detail(sid)
    except MovieError as exc:
        _fail(exc)


@router.get("/{sid}/file")
async def file(sid: str, path: str, request: Request):
    try:
        return FileResponse(_m(request).file(sid, path), headers={"Cache-Control": "private, max-age=31536000, immutable",
                                                                   "X-Content-Type-Options": "nosniff"})
    except MovieError as exc:
        _fail(exc)


# The cast's and the sets' routes last: "/{sid}/{kind}" would also take "/{sid}/episodes".

class MemberIn(BaseModel):
    name: str | None = None
    look: str | None = None
    lora: str | None = None
    strength: float | None = None
    voice: str | None = None            # the speech model's own voice ("" its default), or "clip" (0.73.0)
    expressiveness: float | None = None


@router.post("/{sid}/{kind}")
async def add_member(sid: str, kind: str, body: MemberIn, request: Request):
    try:
        return await _m(request).add_member(sid, _kind(kind), body.model_dump())
    except MovieError as exc:
        _fail(exc)


@router.patch("/{sid}/{kind}/{iid}")
async def change_member(sid: str, kind: str, iid: str, body: MemberIn, request: Request):
    try:
        return await _m(request).change_member(sid, _kind(kind), iid, body.model_dump())
    except MovieError as exc:
        _fail(exc)


@router.delete("/{sid}/{kind}/{iid}")
async def delete_member(sid: str, kind: str, iid: str, request: Request):
    try:
        return await _m(request).delete_member(sid, _kind(kind), iid)
    except MovieError as exc:
        _fail(exc)


@router.post("/{sid}/{kind}/{iid}/make")
async def make_member(sid: str, kind: str, iid: str, request: Request):
    m = _m(request)
    try:
        m.make_member(sid, _kind(kind), iid)
        return m.detail(sid)
    except MovieError as exc:
        _fail(exc)


@router.post("/{sid}/{kind}/{iid}/pick")
async def pick_member(sid: str, kind: str, iid: str, body: PickIn, request: Request):
    try:
        return await _m(request).pick_member(sid, _kind(kind), iid, body.file, body.keep)
    except MovieError as exc:
        _fail(exc)
