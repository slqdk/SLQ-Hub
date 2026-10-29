"""Settings pages for the agent: skills, memory, MCP servers, tool permissions."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from .. import auth, db
from ..state import get_hub
from .tools import REGISTRY

router = APIRouter(prefix="/hub/api")


# ---------------------------------------------------------------- skills
# Everyone's (0.23.0): the owner and each person has their own skills, shares some with the family and sees the
# shared ones and the Hub's. A skill is named by its ref ("<name>", "@<person id>/<name>", "hub/<name>"), given as
# ?ref= since it holds a slash.

def _who(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def _store(request: Request):
    return get_hub(request).extras["skills"]


def _owner_name(request: Request) -> str:
    hub = get_hub(request)
    return hub.settings.get("agent", "user_name") or (hub.env.admin_user or "the owner").title()


def _by(request: Request, s: dict) -> str:
    if s["builtin"]:
        return "SLQ Hub"
    if s["owner_id"] is None:
        return _owner_name(request)
    return auth.display_name(auth.get_person(s["owner_id"])) or "someone"


def _out(request: Request, s: dict) -> dict:
    return {**{k: v for k, v in s.items() if k != "body"}, "by": _by(request, s)}


def _fail(exc: Exception):
    if isinstance(exc, PermissionError):
        raise HTTPException(403, str(exc))
    if isinstance(exc, (FileNotFoundError, KeyError)):
        raise HTTPException(404, "no such skill")
    raise HTTPException(400, str(exc))


@router.get("/skills")
async def list_skills(request: Request):
    return [_out(request, s) for s in _store(request).visible(_who(request))]


@router.post("/skills")
async def install_skill(request: Request, file: UploadFile = File(...)):
    """A skill from Claude or elsewhere: a .zip or .skill bundle, or a SKILL.md - into your skills, not shared. A
    .zip with a set of skills side by side brings them all (0.48.0)."""
    data = await file.read()
    try:
        refs = _store(request).install_all(_who(request), data, file.filename or "SKILL.md")
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))
    return {"ref": refs[0], "name": refs[0].split("/")[-1], "refs": refs, "names": [r.split("/")[-1] for r in refs]}


@router.get("/skills/one")
async def get_skill(ref: str, request: Request):
    store = _store(request)
    try:
        return {**_out(request, store.entry(_who(request), ref)), "text": store.get(_who(request), ref)}
    except (FileNotFoundError, ValueError) as exc:
        _fail(exc)


class SkillText(BaseModel):
    text: str


@router.put("/skills/one")
async def put_skill(ref: str, body: SkillText, request: Request):
    try:
        _store(request).put_text(_who(request), ref, body.text)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        _fail(exc)
    return {"ok": True}


class SkillPatch(BaseModel):
    shared: bool | None = None
    category: str | None = None             # 0.41.0: "" takes it out of any


@router.patch("/skills/one")
async def patch_skill(ref: str, body: SkillPatch, request: Request):
    """Share one of your skills with the family, or take it back; put it into a category."""
    out: dict = {"ok": True}
    try:
        if body.shared is not None:
            _store(request).set_shared(_who(request), ref, body.shared)
        if body.category is not None:
            out["category"] = _store(request).set_category(_who(request), ref, body.category)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        _fail(exc)
    return out


class CategoryRename(BaseModel):
    old: str
    new: str = ""


@router.post("/skills/categories/rename")
async def rename_category(body: CategoryRename, request: Request):
    """A category of yours renamed - or emptied with new "" - for all your skills in it at once."""
    try:
        return {"moved": _store(request).rename_category(_who(request), body.old, body.new)}
    except ValueError as exc:
        _fail(exc)


@router.delete("/skills/one")
async def delete_skill(ref: str, request: Request):
    try:
        _store(request).delete(_who(request), ref)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        _fail(exc)
    return {"ok": True}


def _zip_response(name: str, data: bytes) -> Response:
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})


@router.get("/skills/zip")
async def skill_zip(ref: str, request: Request):
    """The skill as a .zip in the shape Claude takes (Settings -> Capabilities -> Skills there)."""
    try:
        return _zip_response(*_store(request).zip(_who(request), ref))
    except (FileNotFoundError, ValueError) as exc:
        _fail(exc)


@router.get("/skills/drafts/{draft_id}")
async def get_draft(draft_id: str, request: Request):
    """A skill the assistant made (skill_propose): what the card shows, and whether it was added."""
    try:
        return _store(request).draft(_who(request), draft_id)
    except FileNotFoundError:
        raise HTTPException(404, "This draft is gone - drafts are kept for two weeks.")


@router.post("/skills/drafts/{draft_id}/add")
async def add_draft(draft_id: str, request: Request):
    """The card's Add skill."""
    try:
        ref = _store(request).add_draft(_who(request), draft_id)
    except FileNotFoundError:
        raise HTTPException(404, "This draft is gone - drafts are kept for two weeks.")
    return {"ref": ref, "skill": _out(request, _store(request).entry(_who(request), ref))}


@router.get("/skills/drafts/{draft_id}/zip")
async def draft_zip(draft_id: str, request: Request):
    try:
        return _zip_response(*_store(request).draft_zip(_who(request), draft_id))
    except FileNotFoundError:
        raise HTTPException(404, "This draft is gone - drafts are kept for two weeks.")


# ---------------------------------------------------------------- memory

def _mem(request: Request):
    return get_hub(request).extras["memory"]


@router.get("/memory")
async def memory_files(request: Request, project_id: int | None = None):
    m = _mem(request)
    out = []
    for path, first in m.index(project_id, limit=1000):
        p = m.resolve(path, project_id)
        out.append({"path": path, "title": first, "size": p.stat().st_size if p.exists() else 0})
    return out


@router.get("/memory/file")
async def memory_file(path: str, request: Request, project_id: int | None = None):
    try:
        p = _mem(request).resolve(path, project_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not p.is_file():
        raise HTTPException(404, "no such file")
    return {"path": path, "content": p.read_text(encoding="utf-8", errors="replace")}


class MemoryWrite(BaseModel):
    path: str
    content: str
    project_id: int | None = None


@router.put("/memory/file")
async def memory_write(body: MemoryWrite, request: Request):
    try:
        msg = _mem(request).create(body.path, body.content, body.project_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "message": msg}


@router.delete("/memory/file")
async def memory_delete(path: str, request: Request, project_id: int | None = None):
    try:
        _mem(request).delete(path, project_id)
    except FileNotFoundError:
        raise HTTPException(404, "no such file")
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


# ---------------------------------------------------------------- MCP and tools

@router.get("/mcp")
async def mcp_status(request: Request):
    return get_hub(request).extras["mcp"].status()


@router.post("/mcp/reload")
async def mcp_reload(request: Request):
    mcp = get_hub(request).extras["mcp"]
    try:
        await asyncio.wait_for(mcp.reload(), 200)
    except asyncio.TimeoutError:
        pass
    return mcp.status()


@router.get("/tools")
async def tools(request: Request):
    from .loop import _ensure_tools_loaded
    _ensure_tools_loaded()
    builtin = [{"name": t.name, "description": t.description, "group": t.group, "needs_approval": t.needs_approval,
                "subagent": t.in_subagent} for t in REGISTRY.values()]
    return {"builtin": builtin, "mcp": get_hub(request).extras["mcp"].status()}


@router.get("/tool-permissions")
async def permissions():
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT tool, decision, created_at FROM tool_permissions ORDER BY tool")]


@router.delete("/tool-permissions/{tool}")
async def forget_permission(tool: str):
    with db.connect() as con:
        n = con.execute("DELETE FROM tool_permissions WHERE tool=?", (tool,)).rowcount
    if not n:
        raise HTTPException(404, "no such permission")
    return {"ok": True}
