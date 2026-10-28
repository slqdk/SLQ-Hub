"""A person's chat (an account the owner added, role "member"): the
conversation and its files live on the person's own device, not on the Hub.

Each answer is a device turn. The browser sends the conversation, the chat's
files, the pictures the model should see and the latest artifacts. The Hub
writes them into a temporary folder, runs the agent there (no memory, no
libraries, no MCP, no cloud; bash only when the owner allows it, confined to
the folder by Landlock), streams the events, and hands back the new messages,
the files the assistant made or changed, and the pictures. Once the device
has them - or an hour after the turn - the folder, the run and every file the
turn stored are deleted, and ComfyUI keeps no copy of its pictures: the Hub
keeps nothing of the chat.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import auth
from ..chats import store as chats
from ..gpu.scheduler import SchedulerError
from ..images import pipeline as images
from ..llm.client import LlmError
from ..projects import blobs
from ..projects import store as projects
from ..state import get_hub
from . import context as C
from .runs import Run

log = logging.getLogger("hub.device")

KEEP_S = 3600                          # a finished turn's folder and files wait this long for the device
MAX_INPUT = 120 * 1024 * 1024          # decoded files and pictures sent with one turn
MAX_FILES_IN = 500
MAX_MESSAGES = 2000
MAX_OUT_FILE = 100 * 1024 * 1024        # one file the device fetches (0.38.0: was 25 MB)
MAX_OUT_TOTAL = 250 * 1024 * 1024
MAX_OUT_FILES = 50
MAX_ACTIVE = 2                         # turns one person may have running at once (a phone and a PC)
MAX_WAITING = 4                        # finished turns of one person still waiting for their device; older go
BLOCK_TYPES = {"text", "image", "file", "thinking", "tool_call", "tool_result", "continue", "answers"}
SETTING_KEYS = {"model", "thinking", "effort", "image_model", "level"}
LEDGERS = "device-turns"               # data/device-turns/<turn>: the stored files a turn will delete, for a sweep
                                       # after a restart (not in the turn's folder: the person's code may write there)

router = APIRouter()
_lock = threading.Lock()               # the turns' sets of stored files (a file may be stored from a worker thread)
_registries: weakref.WeakSet = weakref.WeakSet()


@dataclass
class DeviceInfo:
    """What the engine needs to know about a person's turn (Engine.device)."""
    user_id: int
    name: str
    code: bool
    dropped: int = 0
    left: list = field(default_factory=list)          # files on the device too big to send each time: (path, size)
    history: list = field(default_factory=list)       # the conversation the device sent (chat_history searches it)
    artifacts: dict = field(default_factory=dict)     # id -> {"title", "type", "version", "content"}
    shas: set = field(default_factory=set)            # stored files of this turn (tools may use these only)

    def get_artifact(self, aid: str) -> dict | None:
        a = self.artifacts.get(aid)
        return dict(a) if a else None

    def save_artifact(self, aid: str, title: str, kind: str, content: str) -> int:
        version = int((self.artifacts.get(aid) or {}).get("version") or 0) + 1
        self.artifacts[aid] = {"title": title, "type": kind, "version": version, "content": content}
        return version


class DeviceTurn:
    def __init__(self, user: dict, run: Run, folder: Path, ledger: Path):
        self.user, self.run, self.folder, self.ledger = user, run, folder, ledger
        self.created: set[str] = set()                # stored files that were new: this turn deletes them at the end
        self.shas: set[str] = set()                   # stored files the device may fetch (and the tools may use)
        self.inputs: dict[str, str] = {}              # path -> sha256 of what the device sent
        self.collected: list[dict] = []               # the new messages
        self.outputs: list[dict] = []
        self.title = ""
        self.result: dict | None = None
        self.ended: float | None = None

    def listen(self, sha: str, new: bool) -> None:
        """Every file a tool stores during the turn (chats.save_upload)."""
        with _lock:
            self.shas.add(sha)
            if new:
                self.adopt(sha)

    def adopt(self, sha: str) -> None:
        """This turn deletes the file when it is done (call with _lock held)."""
        if sha not in self.created:
            self.created.add(sha)
            self._write_ledger()

    def release(self, sha: str) -> None:
        """Someone else keeps the file now (call with _lock held)."""
        if sha in self.created:
            self.created.discard(sha)
            self._write_ledger()

    def _write_ledger(self) -> None:
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.ledger.with_name(self.ledger.name + ".tmp")
            tmp.write_text("".join(f"{sha}\n" for sha in sorted(self.created)))
            os.replace(tmp, self.ledger)
        except OSError as exc:
            log.warning("could not write %s: %s", self.ledger, exc)


class DeviceTurns:
    """The device turns in progress or waiting for their device, by id."""

    def __init__(self, hub):
        self.hub = hub
        self.turns: dict[str, DeviceTurn] = {}
        self._confine: tuple[float, bool] = (float("-inf"), False)   # asked on first use (monotonic starts at boot)
        _registries.add(self)

    @property
    def root(self) -> Path:
        return self.hub.env.workspace_dir / "device"

    @property
    def ledgers(self) -> Path:
        return self.hub.env.data_dir / LEDGERS

    def new_turn(self, user: dict, run: Run) -> DeviceTurn:
        folder = self.root / run.id
        folder.mkdir(parents=True, exist_ok=True)
        turn = DeviceTurn(user, run, folder, self.ledgers / run.id)
        self.turns[run.id] = turn
        return turn

    def get(self, turn_id: str, user: dict | None) -> DeviceTurn | None:
        t = self.turns.get(turn_id)
        return t if t is not None and user and t.user["id"] == user["id"] else None

    def active(self, user_id: int) -> int:
        return sum(1 for t in self.turns.values() if t.user["id"] == user_id and t.ended is None)

    def trim(self, user_id: int) -> None:
        """A person's finished turns that still wait for a device beyond MAX_WAITING go, oldest first (a device
        that never says /done cannot pile them up)."""
        waiting = sorted((t for t in self.turns.values() if t.user["id"] == user_id and t.ended is not None),
                         key=lambda t: t.ended)
        for t in waiting[:max(0, len(waiting) - MAX_WAITING)]:
            self.finish(t.run.id)

    def owns_blob(self, user_id: int, sha: str) -> bool:
        return any(t.user["id"] == user_id and sha in t.shas for t in self.turns.values())

    def stored(self, sha: str) -> None:
        """A file was stored (blobs.store). Stored outside every person's turn - the owner's chat, a project, a
        library - it is kept: no turn deletes it. (A turn's own stores reach its listener; finish() hands a file
        that another turn uses on to that turn.)"""
        if not self.turns or isinstance(getattr(chats.upload_listener.get(), "__self__", None), DeviceTurn):
            return
        with _lock:
            for t in self.turns.values():
                t.release(sha)

    def finish(self, turn_id: str) -> None:
        """Delete what the turn left on the Hub: its run, its folder and the files it stored."""
        with _lock:
            t = self.turns.pop(turn_id, None)
            if t is None:
                return
            doomed = set()
            for sha in t.created:
                heir = next((o for o in self.turns.values() if sha in o.shas), None)
                if heir is not None:
                    heir.adopt(sha)                       # another answer shows it too: that one deletes it
                else:
                    doomed.add(sha)
        if t.run.task is not None and not t.run.task.done():
            t.run.task.cancel()
        agent = self.hub.extras.get("agent")
        if agent is not None:
            agent.runs.drop(turn_id)
        _forget(doomed)
        shutil.rmtree(t.folder, ignore_errors=True)
        for p in (t.ledger, t.ledger.with_name(t.ledger.name + ".tmp")):
            p.unlink(missing_ok=True)

    def sweep(self) -> None:
        """Turns whose device never came back, and what a restart left behind (folders and ledgers)."""
        now = time.time()
        for tid, t in list(self.turns.items()):
            if t.ended is not None and now - t.ended > KEEP_S:
                self.finish(tid)
        ids: set[str] = set()
        if self.root.is_dir():
            ids |= {d.name for d in self.root.iterdir()}
        if self.ledgers.is_dir():
            ids |= {p.name.removesuffix(".tmp") for p in self.ledgers.iterdir()}
        for tid in ids - set(self.turns):
            ledger = self.ledgers / tid
            try:
                _forget(set(ledger.read_text().split()))
            except OSError:
                pass
            shutil.rmtree(self.root / tid, ignore_errors=True)
            for p in (ledger, ledger.with_name(ledger.name + ".tmp")):
                p.unlink(missing_ok=True)

    async def can_confine(self) -> bool:
        """Whether the sandbox can keep a command inside its folder (Landlock); asked every few minutes."""
        at, ok = self._confine
        if time.monotonic() - at < 300:
            return ok
        env = self.hub.env
        try:
            async with httpx.AsyncClient(timeout=5, transport=self.hub.transports.sandbox) as c:
                r = await c.get(env.sandbox_url + "/health")
            ok = int(r.json().get("landlock") or 0) >= 1
        except (httpx.HTTPError, ValueError, TypeError):
            ok = False
        self._confine = (time.monotonic(), ok)
        return ok


def _forget(shas: set[str]) -> None:
    for sha in shas:
        if blobs.SHA.match(sha):
            try:
                chats.forget_upload(sha)
            except Exception:  # noqa: BLE001 - the rest still goes
                log.exception("could not delete %s", sha[:12])


def _blob_stored(sha: str) -> None:
    for turns in list(_registries):
        turns.stored(sha)


blobs.on_store(_blob_stored)


# ---------------------------------------------------------------- the device's request

def _decode(data: str) -> bytes:
    if "," in data[:100] and data.startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, f"a file is not valid base64 ({exc})")


def _settings(raw) -> dict:
    s = {k: v for k, v in (raw or {}).items() if k in SETTING_KEYS} if isinstance(raw, dict) else {}
    # the skills she chose for the chat: her own, the family's shared ones and the Hub's (turn_skills drops others)
    s["skills"] = [r for r in (raw or {}).get("skills") or [] if isinstance(r, str)][:20] if isinstance(raw, dict) \
        else []
    s["libraries"] = []
    return s


def prepare(turn: DeviceTurn, body: dict, wd: projects.Workdir) -> list[dict]:
    """The device's files into the folder, its pictures into the store, and its messages as rows the agent's
    history builder takes (ids in order, picture keys replaced by the stored ones)."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages are required")
    if len(messages) > MAX_MESSAGES:
        raise HTTPException(400, f"at most {MAX_MESSAGES} messages")
    total = 0
    keys: dict[str, str] = {}                         # the device's picture key -> the stored sha
    by_path: dict[str, str] = {}                      # a file's path -> its stored sha (pictures only)
    files = body.get("files") or []
    if not isinstance(files, list) or len(files) > MAX_FILES_IN:
        raise HTTPException(400, f"at most {MAX_FILES_IN} files")
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("path"), str) or not isinstance(f.get("data"), str):
            raise HTTPException(400, "a file needs a path and data")
        data = _decode(f["data"])
        total += len(data)
        if total > MAX_INPUT:
            raise HTTPException(413, "the chat's files are too big to send with one message")
        try:
            p = wd.resolve(f["path"])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        rel = wd.rel(p)
        if rel == "." or rel == ".hub" or rel.startswith((".hub/home", ".hub/tmp")):
            raise HTTPException(400, f"{f['path']}: not a file path")
        projects.safe_write(wd.root, p, data)
        turn.inputs[rel] = hashlib.sha256(data).hexdigest()
        mime = f.get("mime") or mimetypes.guess_type(p.name)[0] or ""
        if mime.startswith("image/"):
            by_path[rel] = chats.save_upload(data, p.name, mime)["sha"]
    for b in body.get("blobs") or []:
        if not isinstance(b, dict) or not isinstance(b.get("sha"), str) or not isinstance(b.get("data"), str):
            raise HTTPException(400, "a picture needs a sha and data")
        data = _decode(b["data"])
        total += len(data)
        if total > MAX_INPUT:
            raise HTTPException(413, "the chat's pictures are too big to send with one message")
        keys[b["sha"]] = chats.save_upload(data, b.get("name") or "picture", b.get("mime") or "image/png")["sha"]

    def picture(block: dict) -> dict:
        out = {k: v for k, v in block.items() if k != "data"}
        sha = keys.get(block.get("sha") or "") or by_path.get(block.get("path") or "")
        if sha:
            out["sha"] = sha
        elif block.get("sha") not in turn.shas:
            out.pop("sha", None)                       # not sent: the model reads it with a tool, if at all
        return out

    rows: list[dict] = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant", "tool"):
            raise HTTPException(400, "a message needs a role: user, assistant or tool")
        content = m.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            raise HTTPException(400, "a message's content is a list of blocks")
        blocks = []
        for b in content:
            if not isinstance(b, dict) or b.get("type") not in BLOCK_TYPES:
                continue
            if b["type"] == "image":
                b = picture(b)
            elif b["type"] == "tool_result":
                b = {**b, "images": [picture(i) for i in b.get("images") or [] if isinstance(i, dict)]}
                b["images"] = [i for i in b["images"] if i.get("sha")]
            elif b["type"] == "file":
                b = {k: v for k, v in b.items() if k != "data"}
            elif b["type"] == "answers":                        # a skill's questions, answered on its card
                from .context import answers_block
                b = answers_block(b)
                if b is None:
                    continue
            elif b["type"] == "continue":                       # Continue under an unfinished turn: only why
                steps = b.get("steps")
                b = {"type": "continue", "reason": "limit" if b.get("reason") == "limit" else "stopped",
                     "steps": steps if isinstance(steps, int) and 0 < steps < 10_000 else 0}
            blocks.append(b)
        row = {"id": len(rows) + 1, "role": m["role"], "content": blocks}
        if isinstance(m.get("created_at"), str):
            row["created_at"] = m["created_at"][:40]             # when it was sent (the model sees it)
        rows.append(row)
    last = rows[-1]
    if last["role"] != "user" or not last["content"]:
        raise HTTPException(400, "the last message must be the user's, and not empty")
    turn.shas |= set(keys.values()) | set(by_path.values())
    return rows


def outputs(turn: DeviceTurn) -> list[dict]:
    """The files the assistant made or changed (not .hub/): they go to the device."""
    out: list[dict] = []
    total = 0
    root = turn.folder.resolve()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".hub/"):
            continue
        try:
            data_sha = hashlib.sha256(p.read_bytes()).hexdigest() if p.stat().st_size <= MAX_OUT_FILE else ""
        except OSError:
            continue
        if turn.inputs.get(rel) == data_sha:
            continue
        size = p.stat().st_size
        item = {"path": rel, "size": size, "mime": mimetypes.guess_type(p.name)[0] or "application/octet-stream"}
        if size > MAX_OUT_FILE or total + size > MAX_OUT_TOTAL or len(out) >= MAX_OUT_FILES:
            item["skipped"] = "too big to send to the device"
        else:
            total += size
        out.append(item)
    return out


def left_on_device(body: dict) -> list[tuple[str, int]]:
    """The chat's files the device kept back - over what one message may carry (0.38.0): the model is told of them."""
    out = []
    for f in (body.get("left") or [])[:50] if isinstance(body.get("left"), list) else []:
        if isinstance(f, dict) and isinstance(f.get("path"), str) and f["path"].strip():
            try:
                out.append((f["path"][:300], max(0, int(f.get("size") or 0))))
            except (TypeError, ValueError):
                continue
    return out


def removed(turn: DeviceTurn) -> list[str]:
    """Files the device sent that are gone now (the assistant deleted or moved them): the device deletes them too."""
    return sorted(rel for rel in turn.inputs if not os.path.lexists(turn.folder / rel))


# ---------------------------------------------------------------- the turn

async def run_turn(hub, turn: DeviceTurn, body: dict, rows: list[dict], wd: projects.Workdir) -> None:
    from .loop import Engine, first_text, vision
    agent = hub.extras["agent"]
    run = turn.run
    listening = chats.upload_listener.set(turn.listen)
    ephemeral = images.EPHEMERAL.set(True)               # ComfyUI keeps no copy of this chat's pictures
    status, error, engine, ticket, pos_task = "done", None, None, None, None
    device = DeviceInfo(user_id=turn.user["id"], name=auth.display_name(turn.user), code=False, shas=turn.shas,
                        history=rows, left=left_on_device(body))
    for a in body.get("artifacts") or []:
        if isinstance(a, dict) and isinstance(a.get("id"), str) and isinstance(a.get("content"), str):
            device.artifacts[a["id"]] = {"title": str(a.get("title") or a["id"]), "type": str(a.get("type") or "html"),
                                         "version": int(a.get("version") or 1), "content": a["content"]}
    try:
        chat = {"id": None, "project_id": None, "title": str(body.get("title") or "New chat")[:200],
                "settings": _settings(body.get("settings")), "summary": ""}
        entry, thinking, effort = agent.resolve(chat, None, {})
        device.code = bool(turn.user.get("may_run_code")) and await hub.extras["device"].can_confine()
        from .skills import materialize                    # her chosen skills' files, for code (.hub/skills/)
        await asyncio.to_thread(materialize, hub.extras["skills"], agent.skills_for(chat, None, device), wd.root,
                                device)
        holder: dict = {}
        run.emit("queued", position=None, current=hub.scheduler.current)
        pos_task = asyncio.get_running_loop().create_task(agent._report_position(run, holder))
        t0 = time.monotonic()
        ticket = await hub.scheduler.acquire(
            device.name or "a person", "agent", hub.model_need(entry["id"]), "interactive", label="Chat",
            lease=True, meta={"user_id": device.user_id, "model": entry["id"], "cancel": run.task.cancel,
                              "run_id": run.id},
            on_job=lambda j: holder.setdefault("job", j))
        pos_task.cancel()
        waited = round(time.monotonic() - t0, 2)
        run.status = "running"
        run.emit("started", model=entry["id"], waited=waited, thinking=thinking)
        engine = Engine(hub, run, chat, None, wd, entry, ticket, thinking=thinking, effort=effort)
        engine.level = agent.level_of(chat, None, {})[0]
        if engine.level != "extra" and engine.thinking_budget:          # as in the owner's turns (0.38.0)
            engine.thinking_budget = max(1, engine.thinking_budget // 2)
        engine.device = device
        engine.steps.append({"type": "queue", "seconds": waited})

        async def persist(role: str, blocks: list[dict]) -> dict:
            row = {"id": len(rows) + len(turn.collected) + 1, "role": role, "content": blocks, "run_id": run.id}
            turn.collected.append(row)
            return row

        messages = _fit(agent, chat, wd, entry, engine, device, rows, vision(entry))
        max_steps = int(hub.settings.get("agent", "max_steps") or 60)
        await engine.loop(messages, persist, max_steps=max_steps)
        if (chat["title"] or "New chat") == "New chat":
            turn.title = await agent.title_for(engine, first_text(rows))
            if turn.title:
                run.emit("title", title=turn.title)
    except asyncio.CancelledError:
        status = "cancelled"
    except (SchedulerError, LlmError, RuntimeError) as exc:
        status, error = "error", str(exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("device turn failed")
        status, error = "error", f"{type(exc).__name__}: {exc}"
    finally:
        if pos_task is not None and not pos_task.done():
            pos_task.cancel()
        t = engine.ticket if engine is not None else ticket
        if t is not None:
            await t.release(error)
        chats.upload_listener.reset(listening)
        images.EPHEMERAL.reset(ephemeral)
        gone: list[str] = []
        try:
            turn.outputs = outputs(turn)
            gone = removed(turn)
        except Exception:  # noqa: BLE001
            log.exception("could not list the turn's files")
            turn.outputs = []
        if engine is not None and turn.outputs:                  # shown under the answer, like the owner's
            engine.steps.append({"type": "files", "files": [
                {**f, "new": f["path"] not in turn.inputs} for f in turn.outputs][:projects.PRODUCED_LIMIT]})
        usage_now = engine.usage if engine else {}
        try:                                                     # the card's energy and its cost (0.24.0)
            from .. import usage
            usage.finish_turn(hub, run.id, usage_now, f"user:{turn.user['id']}")
        except Exception:  # noqa: BLE001
            log.exception("could not count the turn's usage")
        turn.result = {
            "status": status, "error": error, "title": turn.title,
            "messages": [{"role": r["role"], "content": r["content"]} for r in turn.collected],
            "files": turn.outputs, "removed": gone,
            "artifacts": [{"id": k, **v} for k, v in device.artifacts.items()],
            "steps": engine.steps if engine else [], "usage": usage_now,
            "model": engine.model["id"] if engine else None,
        }
        run.emit("device_result", **turn.result)
        turn.ended = time.time()
        if status == "error":
            run.emit("error", message=error)
        elif status == "cancelled":
            run.emit("cancelled")
        else:
            run.emit("done", usage=engine.usage if engine else {})


def _fit(agent, chat: dict, wd, entry: dict, engine, device: DeviceInfo, rows: list[dict], sees: bool) -> list[dict]:
    """The model messages for the turn: the oldest messages are left out until they fit the context (a
    person's chat has no stored summary to fold them into)."""
    schema = C.schema_tokens(engine.schemas(engine.tools()))
    budget = int(engine.ctx_limit * float(agent.hub.settings.get("agent", "compact_at") or 0.75))
    keep = list(rows)
    while True:
        device.dropped = len(rows) - len(keep)
        keep_from = keep[-12]["id"] if len(keep) > 12 else 0
        system = agent.system_for(chat, None, wd, entry, handover_to=engine.handover_label(), device=device)
        msgs = [{"role": "system", "content": system},
                *C.to_model_messages(keep, None, keep_from, vision=sees, keep_results_from=C.results_from(keep, None))]
        if C.estimate_tokens(msgs) + schema <= budget or len(keep) <= 1:
            return msgs
        keep = keep[1:]
        while len(keep) > 1 and keep[0]["role"] != "user":    # start with something the user wrote
            keep = keep[1:]


# ---------------------------------------------------------------- routes

def _person(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "not signed in")
    return user


def _turns(request: Request) -> DeviceTurns:
    return get_hub(request).extras["device"]


@router.post("/hub/api/device/turns")
async def start_turn(request: Request):
    """A person's message: the conversation and its files from the device; the answer streams from
    /events, and the device fetches the result's pictures and files before it calls /done."""
    hub, user, turns = get_hub(request), _person(request), _turns(request)
    turns.sweep()
    turns.trim(user["id"])
    if turns.active(user["id"]) >= MAX_ACTIVE:
        raise HTTPException(409, "Two answers are already being written for you. Wait for one, or stop it.")
    try:
        body = json.loads(await request.body())
    except ValueError:
        raise HTTPException(400, "the body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "the body must be a JSON object")
    agent = hub.extras["agent"]
    run = agent.runs.create(None)
    turn = turns.new_turn(user, run)
    wd = projects.Workdir(turn.folder, None, hub.env.workspace_dir, hub.env.sandbox_workspace)
    listening = chats.upload_listener.set(turn.listen)
    try:
        rows = prepare(turn, body, wd)
    except BaseException:
        turns.finish(run.id)
        raise
    finally:
        chats.upload_listener.reset(listening)
    run.task = asyncio.get_running_loop().create_task(run_turn(hub, turn, body, rows, wd), name=run.id)
    return {"turn_id": run.id}


@router.get("/hub/api/device/turns/{turn_id}/events")
async def turn_events(turn_id: str, request: Request, after: int = 0,
                      last_event_id: str | None = Header(default=None)):
    turn = _turns(request).get(turn_id, _person(request))
    if turn is None:
        raise HTTPException(404, "no such answer (it may have been cleaned up)")
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))

    async def gen():
        yield "retry: 2000\n\n"
        async for ev in turn.run.stream(after):
            if await request.is_disconnected():
                return
            if ev["type"] == "ping":
                yield ": ping\n\n"
                continue
            yield f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/hub/api/device/turns/{turn_id}/result")
async def turn_result(turn_id: str, request: Request):
    turn = _turns(request).get(turn_id, _person(request))
    if turn is None:
        raise HTTPException(404, "no such answer (it may have been cleaned up)")
    if turn.result is None:
        raise HTTPException(409, "the answer is not finished yet")
    return turn.result


@router.post("/hub/api/device/turns/{turn_id}/stop")
async def stop_turn(turn_id: str, request: Request):
    turn = _turns(request).get(turn_id, _person(request))
    if turn is None or turn.run.task is None or turn.run.task.done():
        raise HTTPException(404, "no such answer being written")
    turn.run.task.cancel()
    return {"ok": True}


@router.post("/hub/api/device/turns/{turn_id}/stop-thinking")
async def stop_thinking(turn_id: str, request: Request):
    """Stop thinking in a person's chat (0.30.0), as in the owner's."""
    turn = _turns(request).get(turn_id, _person(request))
    if turn is None or turn.run.finished:
        raise HTTPException(404, "no such answer being written")
    return {"ok": await turn.run.stop_thinking(get_hub(request).llm)}


@router.post("/hub/api/device/turns/{turn_id}/approve")
async def approve(turn_id: str, request: Request):
    """The person's answer when the turn asks first (internet for the assistant set to ask): allow once or deny -
    "always" is the owner's."""
    turn = _turns(request).get(turn_id, _person(request))
    if turn is None:
        raise HTTPException(404, "no such answer being written")
    try:
        body = json.loads(await request.body())
    except ValueError:
        raise HTTPException(400, "the body must be JSON")
    if not isinstance(body, dict) or body.get("decision") not in ("allow", "deny") or not body.get("call_id"):
        raise HTTPException(400, "call_id and a decision of allow or deny")
    if not turn.run.answer_approval(str(body["call_id"]), body["decision"]):
        raise HTTPException(404, "that tool call is not waiting for an answer")
    return {"ok": True}


@router.post("/hub/api/device/turns/{turn_id}/answer")
async def answer(turn_id: str, request: Request):
    """The person's answers to an ask_user card (0.43.0)."""
    turn = _turns(request).get(turn_id, _person(request))
    if turn is None:
        raise HTTPException(404, "no such answer being written")
    try:
        body = json.loads(await request.body())
    except ValueError:
        raise HTTPException(400, "the body must be JSON")
    from ..chats.routes import clean_answers
    if not isinstance(body, dict) or not body.get("call_id") or not isinstance(body.get("answers"), list):
        raise HTTPException(400, "call_id and answers")
    if not turn.run.answer_questions(str(body["call_id"]), clean_answers(body["answers"])):
        raise HTTPException(404, "that card is not waiting for an answer")
    return {"ok": True}


@router.post("/hub/api/device/turns/{turn_id}/done")
async def turn_done(turn_id: str, request: Request):
    """The device has what it needs: the Hub forgets the turn."""
    turns = _turns(request)
    if turns.get(turn_id, _person(request)) is None:
        raise HTTPException(404, "no such answer")
    turns.finish(turn_id)
    return {"ok": True}


@router.get("/hub/api/device/turns/{turn_id}/blob/{sha}")
async def turn_blob(turn_id: str, sha: str, request: Request):
    turn = _turns(request).get(turn_id, _person(request))
    if turn is None or sha not in turn.shas or not blobs.exists(sha):
        raise HTTPException(404, "no such picture in this answer")
    meta = chats.get_upload(sha) or {}
    return Response(blobs.read(sha), media_type=meta.get("mime") or "application/octet-stream",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "sandbox"})


@router.get("/hub/api/device/turns/{turn_id}/file")
async def turn_file(turn_id: str, path: str, request: Request):
    turn = _turns(request).get(turn_id, _person(request))
    if turn is None:
        raise HTTPException(404, "no such answer")
    item = next((f for f in turn.outputs if f["path"] == path and not f.get("skipped")), None)
    if item is None:
        raise HTTPException(404, "no such file in this answer")
    p = (turn.folder / path).resolve()
    if turn.folder.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "no such file in this answer")
    return FileResponse(p, media_type=item["mime"], headers={"Cache-Control": "no-store",
                                                              "X-Content-Type-Options": "nosniff",
                                                              "Content-Security-Policy": "sandbox"})
