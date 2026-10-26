"""Chats, messages, runs (live over SSE), uploads and artifacts."""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from ..projects import store as projects
from ..state import get_hub
from . import store

log = logging.getLogger("hub.chats")

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
    more = store.runs_overview([r["id"] for r in rows]) if project_id is not None and not all else {}
    for r in rows:
        r["running"] = agent.runs.active_for_chat(r["id"]) is not None
        r.update(more.get(r["id"], {}))
    return rows


class ChatCreate(BaseModel):
    project_id: int | None = None
    title: str = "New chat"
    settings: dict | None = None


@router.post("/chats")
async def create_chat(body: ChatCreate):
    if body.project_id is not None and not projects.get_project(body.project_id):
        raise HTTPException(404, "no such project")
    settings = {k: v for k, v in (body.settings or {}).items() if k in store.CHAT_DEFAULTS}   # not continued_from
    return store.create_chat(body.project_id, body.title or "New chat", settings)


def context_use(hub, chat: dict, runs: list[dict]) -> dict:
    """How full the chat's last request was (0.27.0): a long chat is offered Continue in a new chat. The notes a
    continued chat starts with were written from the old one: their request says nothing about the new chat."""
    used, model = 0, None
    for r in reversed(runs):
        steps = [s for s in r.get("steps") or [] if s.get("type") == "model" and not s.get("depth")
                 and not s.get("handover")]
        if steps:
            used, model = int(steps[-1].get("prompt_tokens") or 0), steps[-1].get("model") or r.get("model")
            break
    try:
        entry = hub.catalog.get_llm(model or "") or hub.catalog.get_llm(chat["settings"].get("model") or "") or \
            hub.catalog.get_llm(hub.settings.get("models", "default") or "")
        limit = hub.catalog.context_per_request(entry["id"]) if entry else 0
    except Exception:  # noqa: BLE001 - only a hint
        limit = 0
    return {"used": used, "limit": limit, "condensed": bool(chat.get("summary_upto"))}


def lean_runs(runs: list[dict]) -> list[dict]:
    """The runs as the chat view uses them: the calls and results of subagents and the reviewer that the steps keep
    for the export (0.32.0) left out - each chat sends all its runs whenever it is opened."""
    return [{**r, "steps": [{k: v for k, v in s.items() if k not in ("call", "result")} for s in r.get("steps") or []]}
            for r in runs]


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: int, request: Request):
    chat = _chat_or_404(chat_id)
    agent = _agent(request)
    runs = store.runs_for_chat(chat_id)
    return {"chat": chat, "messages": store.messages(chat_id), "artifacts": store.list_artifacts(chat_id),
            "runs": lean_runs(runs), "run": _run_info(agent.runs.active_for_chat(chat_id)),
            "context": context_use(get_hub(request), chat, runs),
            "project": projects.get_project(chat["project_id"]) if chat.get("project_id") else None,
            "allowed": store.chat_permissions(chat_id)}


@router.delete("/chats/{chat_id}/allowed")
async def take_back(chat_id: int, tool: str | None = None):
    """Take back what was allowed in this chat ("Allow in this chat"): one (tool=...) or all of it."""
    _chat_or_404(chat_id)
    store.forget_chat_permissions(chat_id, tool)
    return {"allowed": store.chat_permissions(chat_id)}


class ChatPatch(BaseModel):
    title: str | None = None
    settings: dict | None = None


@router.patch("/chats/{chat_id}")
async def patch_chat(chat_id: int, body: ChatPatch):
    _chat_or_404(chat_id)
    return store.update_chat(chat_id, title=body.title, settings=body.settings)


class NotesIn(BaseModel):
    text: str


@router.put("/chats/{chat_id}/notes")
async def put_notes(chat_id: int, body: NotesIn, request: Request):
    """The notes the earlier part of the chat was condensed into (0.27.0): the user reads and corrects them in the
    chat's menu; the model gets them in its system prompt."""
    from ..agent.context import MAX_NOTES
    _chat_or_404(chat_id)
    _not_moving(request, chat_id)
    if _agent(request).runs.active_for_chat(chat_id):     # it may be condensing: the correction would be lost
        raise HTTPException(409, "The chat is working on an answer - correct the notes when it is done.")
    text = body.text.strip()
    if len(text) > MAX_NOTES:
        raise HTTPException(400, f"The notes can be at most {MAX_NOTES} characters.")
    chat = store.update_chat(chat_id, summary=text)
    agent = _agent(request)                               # NOTES.md too, while it is the Hub's copy (0.31.0)
    await agent.notes_to_file(chat, agent.workdir(chat), text, "corrected in the chat's menu")
    return chat


@router.get("/chats/{chat_id}/export")
async def export_chat(chat_id: int, request: Request, format: str = "zip"):
    """The chat as Markdown (format=md) or as a troubleshooting bundle (format=zip)."""
    from . import export
    chat = _chat_or_404(chat_id)
    hub = get_hub(request)
    if format == "md":
        return Response(export.markdown(chat, hub.env.version), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{export.filename(chat, "md")}"'})
    if format != "zip":
        raise HTTPException(400, "format is md or zip")
    data = await export.bundle(hub, _agent(request), chat)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{export.filename(chat, "zip")}"'})


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
    options = {k: v for k, v in (options or {}).items() if k != "handover_from"}     # only the handover route's
    try:
        return _agent(request).start(chat_id, options).id
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


def _not_moving(request: Request, chat_id: int) -> None:
    """While a chat's files are moved into a project (0.31.0) nothing may change it or write into its old folder."""
    if chat_id in _agent(request).moving:
        raise HTTPException(409, "This chat is being moved into a project - try again in a moment.")


def _reset_summary_if(chat: dict, message_id: int) -> None:
    if chat.get("summary_upto") and int(chat["summary_upto"]) >= message_id:
        store.update_chat(chat["id"], summary="", summary_upto=0)


@router.post("/chats/{chat_id}/messages")
async def post_message(chat_id: int, body: MessageIn, request: Request):
    """A new message starts a turn. While a turn works, the message waits in
    the turn's inbox and the model reads it after the step it is on."""
    chat = _chat_or_404(chat_id)
    agent = _agent(request)
    _not_moving(request, chat_id)                        # before anything is stored or copied into the old folder
    active = agent.runs.active_for_chat(chat_id)
    if active and len(active.inbox) >= 10:
        raise HTTPException(409, "Ten messages are already waiting for this turn")
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
    active = agent.runs.active_for_chat(chat_id)
    if active is not None:
        item = {"id": "note_" + secrets.token_hex(4), "blocks": blocks}
        active.inbox.append(item)
        active.emit("user_queued", id=item["id"], content=blocks)
        return {"message": None, "run_id": active.id, "queued": item["id"]}
    msg = store.add_message(chat_id, "user", blocks)     # attachments are copied to .hub/ (not synced)
    return {"message": msg, "run_id": _start(request, chat_id, body.options)}


class RetryIn(BaseModel):
    message_id: int | None = None        # a user message; default: the last one
    options: dict = {}


@router.post("/chats/{chat_id}/retry")
async def retry(chat_id: int, body: RetryIn, request: Request):
    chat = _chat_or_404(chat_id)
    _not_moving(request, chat_id)
    if _agent(request).runs.active_for_chat(chat_id):
        raise HTTPException(409, "this chat is already working on an answer")
    rows = store.messages(chat_id)
    from ..agent.review import is_review_row             # the Hub's review is not a message to answer again
    users = [r for r in rows if r["role"] == "user" and not is_review_row(r)]
    target = next((r for r in users if r["id"] == body.message_id), None) if body.message_id else \
        (users[-1] if users else None)
    if target is None:
        raise HTTPException(404, "no user message to answer again")
    store.delete_messages_after(chat_id, target["id"])
    _reset_summary_if(chat, target["id"])
    hand = next((b for b in target["content"] if b.get("type") == "handover"), None)
    if hand is not None:                     # the notes of a continued chat, written again
        try:
            return {"run_id": _agent(request).start(chat_id, {"handover_from": int(hand["chat_id"])}).id}
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
    return {"run_id": _start(request, chat_id, body.options)}


def unfinished(run: dict | None) -> dict | None:
    """Why a turn is not done (0.22.0): it stopped at the step limit, or the user stopped it. None: it ended by
    itself (with an answer, or an error - that one is answered again, not continued)."""
    if not run:
        return None
    limit = next((s for s in run.get("steps") or [] if s.get("type") == "limit" and not s.get("depth")), None)
    if limit:
        return {"reason": "limit", "steps": int(limit.get("steps") or 0)}
    if run.get("status") == "cancelled":
        return {"reason": "stopped"}
    return None


class ContinueIn(BaseModel):
    options: dict = {}                   # model, thinking, effort for this turn


@router.post("/chats/{chat_id}/continue")
async def continue_turn(chat_id: int, body: ContinueIn, request: Request):
    """Continue under an unfinished turn: a message that tells the model why it stopped, then a new turn."""
    _chat_or_404(chat_id)
    _not_moving(request, chat_id)
    if _agent(request).runs.active_for_chat(chat_id):
        raise HTTPException(409, "this chat is already working on an answer")
    runs = store.runs_for_chat(chat_id)
    why = unfinished(runs[-1] if runs else None)
    if why is None:
        raise HTTPException(409, "The last answer is finished - write what you want next")
    msg = store.add_message(chat_id, "user", [{"type": "continue", **why}])
    return {"message": msg, "run_id": _start(request, chat_id, body.options)}


HANDOVER_SKIP = {"skills", "dotnet", "tmp", "home"}      # under .hub: made again, or not needed
HANDOVER_MAX = 2 * 1024 ** 3


def _copy_into_new(src: Path, workspace: Path, chat_id: int) -> None:
    """A chat's own folder into the chat that continues it (a project's chats share the project's folder): into a
    fresh folder first, then renamed into place - never into whatever is at the new chat's path already (code in
    the sandbox could have planted chats/<id>/.hub -> /data there). Gone again when the copy fails."""
    chats_dir = projects.real_dir(workspace, "chats")
    dst = chats_dir / str(chat_id)
    tmp = Path(tempfile.mkdtemp(prefix=f".handover-{chat_id}-", dir=chats_dir))

    def ignore(d, names):
        return [n for n in names if Path(d) == src / ".hub" and n in HANDOVER_SKIP]
    try:
        shutil.copytree(src, tmp, ignore=ignore, dirs_exist_ok=True, symlinks=True)
        if dst.is_symlink() or (dst.exists() and not dst.is_dir()):
            dst.unlink()
        elif dst.exists():
            shutil.rmtree(dst)                     # (rmtree removes links inside, it never follows them)
        os.rename(tmp, dst)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _folder_size(root: Path) -> int:
    total = 0
    for p in Path(root).rglob("*"):
        rel = p.relative_to(root).parts
        if len(rel) > 1 and rel[0] == ".hub" and rel[1] in HANDOVER_SKIP:
            continue
        if p.is_file() and not p.is_symlink():
            total += p.stat().st_size
    return total


def next_title(title: str) -> str:
    m = re.match(r"^(.*) \((\d+)\)$", title)
    return f"{m.group(1)} ({int(m.group(2)) + 1})" if m else f"{title} (2)"


@router.post("/chats/{chat_id}/handover")
async def handover(chat_id: int, request: Request):
    """Continue in a new chat (0.27.0): a new chat with the same project, settings and files, whose first answer is
    the notes the work goes on from - a fresh context answers better than a very long one."""
    old = _chat_or_404(chat_id)
    agent = _agent(request)
    _not_moving(request, chat_id)
    if agent.runs.active_for_chat(chat_id):
        raise HTTPException(409, "This chat is working on an answer - wait for it, or stop it.")
    if not store.count_messages(chat_id):
        raise HTTPException(409, "There is nothing to carry over yet.")
    old_dir = None if old.get("project_id") else agent.workdir(old).root
    if old_dir is not None:
        size = await asyncio.to_thread(_folder_size, old_dir)
        if size > HANDOVER_MAX:
            raise HTTPException(413, f"This chat's files take {size / 1024 ** 3:.1f} GB - too much to copy to a new "
                                     "chat. Delete what you no longer need from its Files first.")
    settings = {k: v for k, v in old["settings"].items() if k in store.CHAT_DEFAULTS}
    new = store.create_chat(old.get("project_id"), next_title(old["title"]), {**settings, "continued_from": chat_id})
    if old_dir is not None:
        try:
            await asyncio.to_thread(_copy_into_new, old_dir, agent.hub.env.workspace_dir, new["id"])
        except OSError as exc:
            store.delete_chat(new["id"])
            raise HTTPException(507, f"Could not copy the chat's files: {exc}")
    store.add_message(new["id"], "user", [{"type": "handover", "chat_id": chat_id, "title": old["title"]}])
    run = agent.start(new["id"], {"handover_from": chat_id})
    return {"chat": store.get_chat(new["id"]), "run_id": run.id}


class MoveIn(BaseModel):
    project_id: int | None = None
    new_project: str | None = None      # a name: the project is made first


@router.post("/chats/{chat_id}/move")
async def move_to_project(chat_id: int, body: MoveIn, request: Request):
    """Move a chat into a project (0.31.0): its files go into the project's folder and it becomes one of the
    project's chats - the project's instructions, files, skills, libraries and sync from its next answer on. Only a
    chat outside a project; nothing in the project is overwritten (409, and nothing moves); the chat's old folder
    goes to the trash (projects/move.py has how it is done safely)."""
    from ..projects import move
    chat = _chat_or_404(chat_id)
    agent = _agent(request)
    hub = get_hub(request)
    if chat_id in agent.moving:
        raise HTTPException(409, "This chat is being moved already.")
    agent.moving.add(chat_id)                              # first: no turn starts, no message is stored meanwhile
    try:
        if chat.get("project_id"):
            raise HTTPException(409, "This chat is in a project already.")
        if chat.get("test_run"):
            raise HTTPException(409, "A test case's chat cannot be moved.")
        if agent.runs.active_for_chat(chat_id):
            raise HTTPException(409, "This chat is working on an answer - wait for it, or stop it.")
        name = " ".join((body.new_project or "").split())
        if body.project_id is None and not name:
            raise HTTPException(400, "Choose a project, or give the new project a name.")
        project = None
        if body.project_id is not None:
            project = projects.get_project(body.project_id)
            if project is None:
                raise HTTPException(404, "No such project.")
            if project.get("archived"):
                raise HTTPException(409, "That project is archived.")
        src = agent.workdir(chat)
        size = await asyncio.to_thread(_folder_size, src.root)
        if size > HANDOVER_MAX:
            raise HTTPException(413, f"This chat's files take {size / 1024 ** 3:.1f} GB - too much to move. Delete "
                                     "what you no longer need from its Files first.")
        try:
            return await _move(hub, chat, project, name, src.sandbox_path)
        except move.Refused as exc:
            raise HTTPException(exc.status, exc.detail)
    finally:
        agent.moving.discard(chat_id)


async def _move(hub, chat: dict, project: dict | None, name: str, moved_from: str) -> dict:
    """The move itself: the chat's folder taken away, planned (a refusal before a new project is made), merged into
    the project under its sync lock, the chat's rows, the old folder into the trash. On a failure everything goes
    back."""
    from ..projects import move
    ws, chat_id = hub.env.workspace_dir, chat["id"]
    private = await asyncio.to_thread(move.take_away, ws, chat_id)
    made = None
    try:
        if private is not None:
            dst = projects.project_dir(ws, project["id"]) if project else None
            move.refuse(await asyncio.to_thread(move.plan, private, dst))      # a quick answer before anything
        if project is None:
            project = made = projects.create_project(name)
        dst = projects.project_dir(ws, project["id"])
        result = move.Plan()
        if private is not None:
            result = await asyncio.to_thread(move.merge_locked, private, dst, project["id"],
                                             f"moved in from the chat \"{chat['title']}\""[:200])
        if result.renamed:                                   # attachments the project had by the same names
            store.repoint_paths(chat_id, result.renamed)
        moved = store.move_to_project(chat_id, project["id"], moved_from, result.renamed)
    except BaseException as exc:
        back = await asyncio.to_thread(move.put_back, ws, chat_id, private)
        if made is not None:
            projects.delete_project(made["id"], ws, hub.env.data_dir / "trash")
        if isinstance(exc, (move.Refused, HTTPException)) or not isinstance(exc, Exception):
            raise
        log.exception("moving chat %s into a project failed", chat_id)
        where = "" if back else f" Its files are kept in {private}."
        raise HTTPException(507, f"Could not move the chat's files ({exc}); the chat stayed where it was.{where}")
    trashed = await asyncio.to_thread(move.to_trash, private, hub.env.data_dir / "trash", chat_id)
    return {"chat": moved, "project": projects.get_project(project["id"]), "copied": result.files,
            "same": len(result.same), "left": result.left, "renamed": result.renamed,
            "old_folder": str(trashed) if trashed else None}


class EditIn(BaseModel):
    message_id: int
    text: str
    options: dict = {}


@router.post("/chats/{chat_id}/edit")
async def edit(chat_id: int, body: EditIn, request: Request):
    chat = _chat_or_404(chat_id)
    _not_moving(request, chat_id)
    if _agent(request).runs.active_for_chat(chat_id):
        raise HTTPException(409, "this chat is already working on an answer")
    from ..agent.review import is_review_row
    target = next((r for r in store.messages(chat_id) if r["id"] == body.message_id and r["role"] == "user"
                   and not is_review_row(r)), None)
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
    return [_run_info(r) for r in _agent(request).runs.runs.values() if not r.finished and r.chat_id is not None]


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
            # (as bytes: a lone surrogate - half an emoji in a call's arguments - as "?", not a broken stream)
            yield f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8", "replace")
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/runs/{run_id}/stop")
async def stop(run_id: str, request: Request):
    if not _agent(request).runs.cancel(run_id):
        raise HTTPException(404, "no such running run")
    return {"ok": True}


@router.post("/runs/{run_id}/stop-thinking")
async def stop_thinking(run_id: str, request: Request):
    """End the thinking of the step that is thinking now (0.30.0): it goes on with what it has. ok false: no step
    was thinking (it had just ended, say)."""
    run = _agent(request).runs.get(run_id)
    if run is None or run.finished:
        raise HTTPException(404, "no such running run")
    return {"ok": await run.stop_thinking(get_hub(request).llm)}


class Approval(BaseModel):
    call_id: str
    decision: str                        # allow (once) | chat (the rest of this chat) | always | deny


@router.post("/runs/{run_id}/approve")
async def approve(run_id: str, body: Approval, request: Request):
    run = _agent(request).runs.get(run_id)
    if run is None or body.decision not in ("allow", "chat", "always", "deny"):
        raise HTTPException(400, "no such run, or decision is not allow/chat/always/deny")
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
