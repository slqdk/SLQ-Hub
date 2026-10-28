"""/api/sync/... - what SLQSync.exe and slq_sync.py talk to.

Identical to SLQ Companion's protocol (2.64.0): the Windows client signs in
with a device token (Authorization: Bearer slqsync_...) and syncs a local
folder with a project's revision history.

A project made since 0.40.0 syncs one folder of it (its synced folder; hub/
beside it is the assistant's): the PC's folder is that folder. The history
keeps the whole project, paths from its top; here they are turned into the
PC's - the prefix taken off on the way out, put on on the way in - and what
lies outside is left out. The PC client is the same."""
from __future__ import annotations

import base64
import socket
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..state import get_hub
from . import store, syncstore

router = APIRouter()
MAX_CHECKIN_FILES = 2000


def _workdir(request: Request, pid: int) -> Path:
    if not store.get_project(pid):
        raise HTTPException(404, "No such project.")
    return store.project_dir(get_hub(request).env.workspace_dir, pid)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except syncstore.SyncError as exc:
        raise HTTPException(400, str(exc))


def _prefix(pid: int) -> str:
    """The synced folder and "/" (0.40.0), or "" for a project that syncs as a whole."""
    folder = store.sync_folder(store.get_project(pid))
    return f"{folder}/" if folder else ""


@router.get("/api/sync/whoami")
async def whoami(request: Request):
    return {"user": request.state.user["username"], "role": "admin", "server": socket.gethostname()}


@router.get("/api/sync/projects")
async def projects(request: Request):
    ws = get_hub(request).env.workspace_dir
    return [{"id": p["id"], "name": p["name"], "head": syncstore.head(p["id"]),
             "folderExists": (ws / "projects" / str(p["id"])).is_dir()} for p in store.list_projects()]


@router.get("/api/sync/projects/{pid}/head")
async def head(pid: int, request: Request):
    wd = _workdir(request, pid)
    syncstore.snapshot(pid, wd, author="hub", origin="hub", message="Changes on the server")
    h = syncstore.head(pid)
    return {"head": h, "files": syncstore.in_view(syncstore.manifest(pid, h), _prefix(pid))}


@router.get("/api/sync/projects/{pid}/changes")
async def changes(pid: int, since: int, request: Request):
    wd = _workdir(request, pid)
    syncstore.snapshot(pid, wd, author="hub", origin="hub", message="Changes on the server")
    out = _call(syncstore.changes_since, pid, max(0, since))
    return {**out, "changes": syncstore.in_view(out["changes"], _prefix(pid))}


@router.get("/api/sync/projects/{pid}/blob/{sha}")
async def blob(pid: int, sha: str):
    data = _call(syncstore.blob, pid, sha)
    return Response(data, media_type="application/octet-stream",
                    headers={"Cache-Control": "private, max-age=31536000, immutable"})


class CheckinFile(BaseModel):
    path: str
    baseSha: str | None = None
    sha: str | None = None
    content: str | None = None


class CheckinBody(BaseModel):
    origin: str = ""
    message: str = ""
    files: list[CheckinFile]


@router.post("/api/sync/projects/{pid}/checkin")
async def checkin(pid: int, body: CheckinBody, request: Request):
    if len(body.files) > MAX_CHECKIN_FILES:
        raise HTTPException(400, f"At most {MAX_CHECKIN_FILES} files per check-in.")
    wd = _workdir(request, pid)
    pre = _prefix(pid)
    files = []
    for f in body.files:
        content = None
        if f.sha is not None:
            try:
                content = base64.b64decode(f.content or "", validate=True)
            except ValueError:
                raise HTTPException(400, f"{f.path}: content is not base64.")
        path = pre + _call(syncstore.clean_path, f.path) if pre else f.path       # the PC's path, in the project
        files.append({"path": path, "baseSha": f.baseSha, "sha": f.sha, "content": content})
    origin = " ".join((body.origin or "").split())[:60] or "client"
    result = _call(syncstore.checkin, pid, wd, files, author=request.state.user["username"], origin=origin,
                   message=body.message)
    if result.get("rev"):
        store.touch_project(pid)
    if pre:
        result = {**result, "accepted": [p[len(pre):] for p in result["accepted"]],
                  "conflicts": [{**c, "path": c["path"][len(pre):]} for c in result["conflicts"]]}
    return result


@router.get("/api/sync/projects/{pid}/revisions")
async def revisions(pid: int, limit: int = 50, before: int = 0):
    return {"head": syncstore.head(pid), "revisions": syncstore.revisions(pid, limit=limit, before=before or None,
                                                                          prefix=_prefix(pid))}
