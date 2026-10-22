"""Chats, messages, runs (live over SSE), uploads and artifacts."""
from __future__ import annotations

import json
import mimetypes
import shutil

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..projects import store as projects
from ..state import get_hub
from . import store

router = APIRouter(prefix="/hub/api")
MAX_UPLOAD = 512 * 1024 * 1024


def _agent(request: Request):
    return get_hub(request).extras["agent"]


def _chat_or_404(chat_id: int) -> dict:
    c = store.get_chat(chat_id)
    if not c:
        raise HTTPException(404, "no such chat")
    return c


def _run_info(run) -> dict | None:
    if run is None:
        return None
    return {"id": run.id, "chat_id": run.chat_id, "status": run.status, "started": run.started,
            "events": len(run.events)}


# ---------------------------------------------------------------- chats

@router.get("/chats")
async def list_chats(request: Request, project_id: int | None = None, q: str = "", all: bool = False,
                     limit: int = 200):
    rows = store.list_chats(project_id, q.strip(), max(1, min(limit, 1000)), all_projects=all)
    agent = _agent(request)
    for r in rows:
        r["running"] = agent.runs.active_for_chat(r["id"]) is not None
    return rows


class ChatCreate(BaseModel):
    project_id: int | None = None
    title: str = "New chat"
    settings: dict | None = None


@router.post("/chats")
async def create_chat(body: ChatCreate):
    if body.project_id is not None and not projects.get_project(body.project_id):
        raise HTTPException(404, "no such project")
    return store.create_chat(body.project_id, body.title or "New chat", body.settings)


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: int, request: Request):
    chat = _chat_or_404(chat_id)
    agent = _agent(request)
    return {"chat": chat, "messages": store.messages(chat_id), "artifacts": store.list_artifacts(chat_id),
            "runs": store.runs_for_chat(chat_id), "run": _run_info(agent.runs.active_for_chat(chat_id)),
            "project": projects.get_project(chat["project_id"]) if chat.get("project_id") else None}


class ChatPatch(BaseModel):
    title: str | None = None
    settings: dict | None = None


@router.patch("/chats/{chat_id}")
async def patch_chat(chat_id: int, body: ChatPatch):
    _chat_or_404(chat_id)
    return store.update_chat(chat_id, title=body.title, settings=body.settings)


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: int, request: Request):
    chat = _chat_or_404(chat_id)
    agent = _agent(request)
    run = agent.runs.active_for_chat(chat_id)
    if run:
        agent.runs.cancel(run.id)
    store.delete_chat(chat_id)
    if not chat.get("project_id"):
        shutil.rmtree(get_hub(request).env.workspace_dir / "chats" / str(chat_id), ignore_errors=True)
    return {"ok": True}


# ---------------------------------------------------------------- messages -> runs

class MessageIn(BaseModel):
    text: str = ""
    attachments: list[str] = []          # upload sha256s
    refs: list[str] = []                 # files of the chat's working folder (the project's files)
    options: dict = {}                   # model, thinking, effort for this turn


def _start(request: Request, chat_id: int, options: dict) -> str:
    try:
        return _agent(request).start(chat_id, options).id
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


def _reset_summary_if(chat: dict, message_id: int) -> None:
    if chat.get("summary_upto") and int(chat["summary_upto"]) >= message_id:
        store.update_chat(chat["id"], summary="", summary_upto=0)


@router.post("/chats/{chat_id}/messages")
async def post_message(chat_id: int, body: MessageIn, request: Request):
    chat = _chat_or_404(chat_id)
    agent = _agent(request)
    if agent.runs.active_for_chat(chat_id):
        raise HTTPException(409, "this chat is already working on an answer")
    if not body.text.strip() and not body.attachments and not body.refs:
        raise HTTPException(400, "the message is empty")
    from ..agent.context import CHARS_PER_TOKEN
    from ..agent.loop import REF_SHARE, attachment_block, ref_blocks
    wd = agent.workdir(chat)
    blocks: list[dict] = []
    if body.text.strip():
        blocks.append({"type": "text", "text": body.text})
    for sha in body.attachments[:50]:
        up = store.get_upload(sha)
        if not up:
            raise HTTPException(404, f"no upload {sha[:12]}")
        blocks.append(attachment_block(wd, {"sha": sha, "name": up["filename"], "mime": up["mime"],
                                            "size": up["size"]}))
    if body.refs:
        # How much file text the message may carry depends on the model that answers it.
        project = projects.get_project(chat["project_id"]) if chat.get("project_id") else None
        try:
            entry, _, _ = agent.resolve(chat, project, body.options)
            ctx = get_hub(request).catalog.context_per_request(entry["id"])
        except RuntimeError:
            ctx = 32768
        try:
            blocks.extend(ref_blocks(wd, body.refs, int(ctx * REF_SHARE * CHARS_PER_TOKEN)))
        except FileNotFoundError as exc:
            raise HTTPException(404, f"No such file in the working folder: {exc}")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    msg = store.add_message(chat_id, "user", blocks)     # attachments are copied to .hub/ (not synced)
    return {"message": msg, "run_id": _start(request, chat_id, body.options)}


class RetryIn(BaseModel):
    message_id: int | None = None        # a user message; default: the last one
    options: dict = {}


@router.post("/chats/{chat_id}/retry")
async def retry(chat_id: int, body: RetryIn, request: Request):
    chat = _chat_or_404(chat_id)
    if _agent(request).runs.active_for_chat(chat_id):
        raise HTTPException(409, "this chat is already working on an answer")
    rows = store.messages(chat_id)
    users = [r for r in rows if r["role"] == "user"]
    target = next((r for r in users if r["id"] == body.message_id), None) if body.message_id else \
        (users[-1] if users else None)
    if target is None:
        raise HTTPException(404, "no user message to answer again")
    store.delete_messages_after(chat_id, target["id"])
    _reset_summary_if(chat, target["id"])
    return {"run_id": _start(request, chat_id, body.options)}


class EditIn(BaseModel):
    message_id: int
    text: str
    options: dict = {}


@router.post("/chats/{chat_id}/edit")
async def edit(chat_id: int, body: EditIn, request: Request):
    chat = _chat_or_404(chat_id)
    if _agent(request).runs.active_for_chat(chat_id):
        raise HTTPException(409, "this chat is already working on an answer")
    target = next((r for r in store.messages(chat_id) if r["id"] == body.message_id and r["role"] == "user"), None)
    if target is None:
        raise HTTPException(404, "no such user message")
    blocks = [b for b in target["content"] if b.get("type") != "text"]
    if body.text.strip():
        blocks.insert(0, {"type": "text", "text": body.text})
    if not blocks:
        raise HTTPException(400, "the message is empty")
    store.update_message(target["id"], blocks)
    store.delete_messages_after(chat_id, target["id"])
    _reset_summary_if(chat, target["id"])
    return {"run_id": _start(request, chat_id, body.options)}


# ---------------------------------------------------------------- runs

@router.get("/runs")
async def runs(request: Request):
    return [_run_info(r) for r in _agent(request).runs.runs.values() if not r.finished]


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int = 0,
                     last_event_id: str | None = Header(default=None)):
    run = _agent(request).runs.get(run_id)
    if run is None:
        raise HTTPException(404, "no such run (it may have finished more than an hour ago)")
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))

    async def gen():
        yield "retry: 2000\n\n"
        async for ev in run.stream(after):
            if await request.is_disconnected():
                return
            if ev["type"] == "ping":
                yield ": ping\n\n"
                continue
            yield f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/runs/{run_id}/stop")
async def stop(run_id: str, request: Request):
    if not _agent(request).runs.cancel(run_id):
        raise HTTPException(404, "no such running run")
    return {"ok": True}


class Approval(BaseModel):
    call_id: str
    decision: str                        # allow | always | deny


@router.post("/runs/{run_id}/approve")
async def approve(run_id: str, body: Approval, request: Request):
    run = _agent(request).runs.get(run_id)
    if run is None or body.decision not in ("allow", "always", "deny"):
        raise HTTPException(400, "no such run, or decision is not allow/always/deny")
    if not run.answer_approval(body.call_id, body.decision):
        raise HTTPException(404, "that tool call is not waiting for an answer")
    return {"ok": True}


# ---------------------------------------------------------------- uploads

@router.post("/uploads")
async def upload(file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "the file is larger than 512 MB")
    name = (file.filename or "file").replace("\\", "/").split("/")[-1][:200] or "file"
    mime = file.content_type if file.content_type and file.content_type != "application/octet-stream" else \
        (mimetypes.guess_type(name)[0] or "application/octet-stream")
    up = store.save_upload(data, name, mime)
    return {**up, "url": f"/hub/media/blob/{up['sha']}"}


# ---------------------------------------------------------------- artifacts

@router.get("/chats/{chat_id}/artifacts")
async def artifacts(chat_id: int):
    _chat_or_404(chat_id)
    return store.list_artifacts(chat_id)


@router.get("/chats/{chat_id}/artifacts/{artifact_id}")
async def artifact(chat_id: int, artifact_id: str, version: int | None = None):
    a = store.get_artifact(chat_id, artifact_id, version)
    if not a:
        raise HTTPException(404, "no such artifact")
    latest = store.get_artifact(chat_id, artifact_id)
    return {**a, "latest": latest["version"] if latest else a["version"]}


class SaveArtifact(BaseModel):
    path: str
    version: int | None = None


@router.post("/chats/{chat_id}/artifacts/{artifact_id}/save")
async def save_artifact(chat_id: int, artifact_id: str, body: SaveArtifact, request: Request):
    """Put an artifact into the chat's folder (the project's files for a
    project chat), e.g. to keep a generated page."""
    chat = _chat_or_404(chat_id)
    a = store.get_artifact(chat_id, artifact_id, body.version)
    if not a:
        raise HTTPException(404, "no such artifact")
    wd = _agent(request).workdir(chat)
    try:
        p = wd.write(body.path, a["content"], author="web", origin="artifact",
                     message=f"artifact {artifact_id} v{a['version']}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"path": wd.rel(p)}
