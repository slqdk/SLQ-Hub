"""UI routes: projects, and the files of a project or a chat folder
(tree, read, write, upload, move, delete, history, restore), sync devices."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .. import auth, db
from ..state import Hub, get_hub
from . import store, syncstore

router = APIRouter()
MAX_UPLOAD = 200 * 1024 * 1024


class ProjectIn(BaseModel):
    name: str
    instructions: str = ""
    settings: dict | None = None


class ProjectPatch(BaseModel):
    name: str | None = None
    instructions: str | None = None
    settings: dict | None = None
    archived: bool | None = None


@router.get("/hub/api/projects")
async def list_projects(archived: bool = False):
    return store.list_projects(archived)


@router.post("/hub/api/projects")
async def create_project(body: ProjectIn, request: Request):
    p = store.create_project(body.name, body.instructions, body.settings)
    store.project_dir(get_hub(request).env.workspace_dir, p["id"])
    return p


@router.get("/hub/api/projects/{pid}")
async def get_project(pid: int):
    p = store.get_project(pid)
    if not p:
        raise HTTPException(404, "No such project.")
    return {**p, "head": syncstore.head(pid)}


@router.patch("/hub/api/projects/{pid}")
async def patch_project(pid: int, body: ProjectPatch):
    p = store.update_project(pid, body.name, body.instructions, body.settings, body.archived)
    if not p:
        raise HTTPException(404, "No such project.")
    return p


@router.delete("/hub/api/projects/{pid}")
async def delete_project(pid: int, request: Request):
    hub = get_hub(request)
    if not store.delete_project(pid, hub.env.workspace_dir, hub.env.data_dir / "trash"):
        raise HTTPException(404, "No such project.")
    return {"ok": True}


# ---------------------------------------------------------------- folders

def workdir(hub: Hub, kind: str, fid: int) -> store.Workdir:
    ws = hub.env.workspace_dir
    if kind == "project":
        if not store.get_project(fid):
            raise HTTPException(404, "No such project.")
        return store.Workdir(store.project_dir(ws, fid), fid, ws, hub.env.sandbox_workspace)
    if kind == "chat":
        with db.connect() as con:
            r = con.execute("SELECT project_id FROM chats WHERE id=?", (fid,)).fetchone()
        if not r:
            raise HTTPException(404, "No such chat.")
        if r["project_id"]:
            return workdir(hub, "project", r["project_id"])
        return store.Workdir(store.chat_dir(ws, fid), None, ws, hub.env.sandbox_workspace)
    raise HTTPException(404, "Unknown folder kind.")


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"No such file: {exc}")
    except (ValueError, syncstore.SyncError) as exc:
        raise HTTPException(400, str(exc))


@router.get("/hub/api/folders/{kind}/{fid}/tree")
async def tree(kind: str, fid: int, request: Request):
    wd = workdir(get_hub(request), kind, fid)
    return {"root": wd.sandbox_path, "projectId": wd.project_id, "entries": wd.tree()}


@router.get("/hub/api/folders/{kind}/{fid}/file")
async def read_file(kind: str, fid: int, path: str, request: Request):
    wd = workdir(get_hub(request), kind, fid)
    data = _safe(wd.read_bytes, path)
    text = data.decode("utf-8", "replace") if len(data) <= store.TEXT_LIMIT and store.is_text(data) else None
    return {"path": path, "size": len(data), "mime": store.mime_of(path), "text": text, "binary": text is None}


@router.get("/hub/api/folders/{kind}/{fid}/raw")
async def raw_file(kind: str, fid: int, path: str, request: Request, download: bool = False):
    wd = workdir(get_hub(request), kind, fid)
    p = _safe(wd.resolve, path)
    if not p.is_file():
        raise HTTPException(404, "No such file.")
    mime = store.mime_of(path)
    headers = {"X-Content-Type-Options": "nosniff"}
    if mime != "application/pdf":       # a page made in the folder must not run as the Hub; the PDF viewer needs it off
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(p, media_type=mime, filename=p.name if download else None, headers=headers)


class FileWrite(BaseModel):
    path: str
    text: str


@router.put("/hub/api/folders/{kind}/{fid}/file")
async def write_file(kind: str, fid: int, body: FileWrite, request: Request):
    wd = workdir(get_hub(request), kind, fid)
    _safe(wd.write, body.path, body.text, "user", "web", f"edited {body.path}")
    return {"ok": True, "head": syncstore.head(wd.project_id) if wd.project_id else None}


@router.delete("/hub/api/folders/{kind}/{fid}/file")
async def delete_file(kind: str, fid: int, path: str, request: Request):
    wd = workdir(get_hub(request), kind, fid)
    _safe(wd.delete, path, "user", "web")
    return {"ok": True}


class MoveIn(BaseModel):
    src: str
    dst: str


@router.post("/hub/api/folders/{kind}/{fid}/move")
async def move_file(kind: str, fid: int, body: MoveIn, request: Request):
    wd = workdir(get_hub(request), kind, fid)
    _safe(wd.move, body.src, body.dst, "user", "web")
    return {"ok": True}


class MkdirIn(BaseModel):
    path: str


@router.post("/hub/api/folders/{kind}/{fid}/mkdir")
async def mkdir(kind: str, fid: int, body: MkdirIn, request: Request):
    wd = workdir(get_hub(request), kind, fid)
    _safe(wd.mkdir, body.path)
    return {"ok": True}


@router.post("/hub/api/folders/{kind}/{fid}/upload")
async def upload(kind: str, fid: int, request: Request, files: list[UploadFile] = File(...), dir: str = Form("")):
    wd = workdir(get_hub(request), kind, fid)
    saved = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, f"{f.filename} is larger than {MAX_UPLOAD // (1024 * 1024)} MB.")
        name = Path(f.filename or "upload").name
        rel = f"{dir.strip('/')}/{name}" if dir.strip("/") else name
        p = _safe(wd.resolve, rel)
        _safe(store.safe_write, wd.root, p, data)
        saved.append(wd.rel(p))
    wd.checkin("user", "web", f"uploaded {', '.join(saved)[:200]}")
    return {"saved": saved}


# ---------------------------------------------------------------- history

@router.get("/hub/api/projects/{pid}/revisions")
async def revisions(pid: int, before: int = 0, limit: int = 50):
    return {"head": syncstore.head(pid), "revisions": syncstore.revisions(pid, limit, before or None)}


@router.get("/hub/api/projects/{pid}/history")
async def history(pid: int, path: str):
    return syncstore.file_history(pid, path)


@router.get("/hub/api/projects/{pid}/rev-file")
async def rev_file(pid: int, path: str, rev: int):
    sha = syncstore.manifest(pid, rev).get(path)
    if not sha:
        raise HTTPException(404, "The file did not exist at that revision.")
    data = _safe(syncstore.blob, pid, sha)
    if store.is_text(data):
        return {"path": path, "rev": rev, "text": data.decode("utf-8", "replace")}
    return Response(data, media_type=store.mime_of(path))


class RestoreIn(BaseModel):
    path: str
    rev: int


@router.post("/hub/api/projects/{pid}/restore")
async def restore(pid: int, body: RestoreIn, request: Request):
    hub = get_hub(request)
    wd = workdir(hub, "project", pid)
    sha = syncstore.manifest(pid, body.rev).get(body.path)
    if not sha:
        raise HTTPException(404, "The file did not exist at that revision.")
    data = _safe(syncstore.blob, pid, sha)
    _safe(wd.write, body.path, data, "user", "web", f"restored {body.path} from r{body.rev}")
    return {"ok": True, "head": syncstore.head(pid)}


# ---------------------------------------------------------------- sync devices

class TokenIn(BaseModel):
    name: str


@router.get("/hub/api/sync-tokens")
async def list_tokens(request: Request):
    return auth.list_sync_tokens(request.state.user["id"])


@router.post("/hub/api/sync-tokens")
async def create_token(body: TokenIn, request: Request):
    return auth.create_sync_token(request.state.user["id"], body.name)


@router.delete("/hub/api/sync-tokens/{token_id}")
async def revoke_token(token_id: int, request: Request):
    if not auth.revoke_sync_token(request.state.user["id"], token_id):
        raise HTTPException(404, "No such device.")
    return {"ok": True}


@router.get("/hub/api/sync-client/info")
async def client_info(request: Request):
    exe = get_hub(request).env.data_dir / "sync-client" / "SLQSync.exe"
    return {"exe": exe.is_file(), "exeSize": exe.stat().st_size if exe.is_file() else 0}


@router.get("/hub/api/sync-client/exe")
async def client_exe(request: Request):
    exe = get_hub(request).env.data_dir / "sync-client" / "SLQSync.exe"
    if not exe.is_file():
        raise HTTPException(404, "SLQSync.exe is not on this Hub - copy it to <data>/sync-client/.")
    return FileResponse(exe, media_type="application/vnd.microsoft.portable-executable", filename="SLQSync.exe")
