"""Settings pages for the agent: skills, memory, MCP servers, tool permissions."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import db
from ..state import get_hub
from .tools import REGISTRY

router = APIRouter(prefix="/hub/api")


# ---------------------------------------------------------------- skills

@router.get("/skills")
async def list_skills(request: Request):
    return get_hub(request).extras["skills"].list()


@router.post("/skills")
async def install_skill(request: Request, file: UploadFile = File(...)):
    data = await file.read()
    try:
        name = get_hub(request).extras["skills"].install(data, file.filename or "SKILL.md")
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))
    return {"name": name}


@router.get("/skills/{name}")
async def get_skill(name: str, request: Request):
    store = get_hub(request).extras["skills"]
    try:
        text = store.get(name)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "no such skill")
    meta = next((s for s in store.list() if s["name"] == name), {})
    return {**meta, "text": text}


class SkillText(BaseModel):
    text: str


@router.put("/skills/{name}")
async def put_skill(name: str, body: SkillText, request: Request):
    store = get_hub(request).extras["skills"]
    try:
        store.get(name)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "no such skill")
    (store.root / name / "SKILL.md").write_text(body.text, encoding="utf-8")
    return {"ok": True}


class SkillPatch(BaseModel):
    enabled: bool


@router.patch("/skills/{name}")
async def patch_skill(name: str, body: SkillPatch, request: Request):
    try:
        get_hub(request).extras["skills"].set_enabled(name, body.enabled)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "no such skill")
    return {"ok": True}


@router.delete("/skills/{name}")
async def delete_skill(name: str, request: Request):
    try:
        if not get_hub(request).extras["skills"].delete(name):
            raise HTTPException(404, "no such skill")
    except ValueError:
        raise HTTPException(404, "no such skill")
    return {"ok": True}


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
