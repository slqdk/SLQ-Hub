"""The agent loop.

A web chat turn is one queue job holding a lease on the model (the user's
choice: an agent task keeps the card until it is done; a call from another
app for the same model may still use the second slot). Inside it:

    prompt -> model (streamed) -> tool calls -> results -> model -> ... -> answer

Everything is streamed to the browser as run events and stored as chat
messages step by step, so a reload or a crash loses nothing that was done.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from .. import db
from ..chats import store as chats
from ..gpu.scheduler import SchedulerError, Ticket
from ..llm import payload as P
from ..llm.client import LlmError
from ..llm.stream import Accumulator, parse_arguments
from ..projects import store as projects
from ..state import Hub
from . import cloud as cloudmod
from . import context as C
from .prompt import folder_text, system_prompt
from .runs import Run, RunRegistry
from .tools import REGISTRY, Tool, ToolContext, ToolResult
from .tools.base import clip

log = logging.getLogger("hub.agent")
Persist = Callable[[str, list[dict]], Awaitable[None]] | None
STOPPED = "Stopped by the user before the tool finished."


def _ensure_tools_loaded() -> None:
    from .tools import files, images, knowledge, shell, web  # noqa: F401 - registration
    from . import meta_tools  # noqa: F401


def vision(model: dict) -> bool:
    return "vision" in (model.get("capabilities") or [])


class Engine:
    """Runs model steps and tool calls for one conversation: the turn's main
    agent (depth 0) or a subagent (depth 1)."""

    def __init__(self, hub: Hub, run: Run, chat: dict, project: dict | None, workdir: projects.Workdir,
                 model: dict, ticket: Ticket | None, *, depth: int = 0, parent: str | None = None,
                 thinking: bool | None = None, effort: str | None = None, escalation: bool = False):
        self.hub, self.run, self.chat, self.project, self.workdir = hub, run, chat, project, workdir
        self.model, self.ticket, self.depth, self.parent = model, ticket, depth, parent
        self.thinking, self.effort, self.escalation = thinking, effort, escalation
        self.need = hub.model_need(model["id"])
        self.cloud: cloudmod.CloudClient | None = None
        self.steps: list[dict] = []
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
        self.state: dict = {}
        self.fail_streak = 0
        self.recent: list[str] = []
        self.last_prompt_tokens = 0
        self.ctx_limit = hub.catalog.context_per_request(model["id"])
        self._partial = {"text": "", "thinking": ""}

    # ------------------------------------------------------------ helpers
    def emit(self, kind: str, **data) -> None:
        if self.depth:
            data.update(depth=self.depth, parent=self.parent)
        self.run.emit(kind, **data)

    def tool_context(self, call_id: str) -> ToolContext:
        return ToolContext(hub=self.hub, run=self.run, chat=self.chat, project=self.project, workdir=self.workdir,
                           ticket=self.ticket, model=self.model, depth=self.depth, call_id=call_id, state=self.state,
                           engine=self)

    def tools(self) -> list[Tool]:
        ctx = self.tool_context("")
        out = []
        for t in REGISTRY.values():
            if self.depth and not t.in_subagent:
                continue
            if t.name == "escalate" and (not self.escalation or self.cloud is not None):
                continue
            try:
                if t.available(ctx):
                    out.append(t)
            except Exception:  # noqa: BLE001 - a broken check hides the tool, nothing more
                continue
        mcp = self.hub.extras.get("mcp")
        if mcp is not None:
            out += mcp.tools()
        return out

    # ------------------------------------------------------------ the loop
    async def loop(self, messages: list[dict], persist: Persist = None, max_steps: int = 60,
                   compact: Callable[[list[dict]], Awaitable[list[dict]]] | None = None) -> str:
        tools = self.tools()
        by_name = {t.name: t for t in tools}
        schemas = [t.schema() for t in tools]
        final = ""
        for step in range(1, max_steps + 1):
            messages = await self._fit(messages, compact)
            try:
                res = await self._model_step(messages, schemas, step)
            except asyncio.CancelledError:
                await self._save_partial(persist)
                raise
            msg: dict = {"role": "assistant", "content": res["content"]}
            calls = res.get("tool_calls") or []
            if calls:
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for c in calls]
            if res.get("reasoning") and not self.cloud:
                msg["reasoning_content"] = res["reasoning"]
            messages.append(msg)
            blocks: list[dict] = []
            if res.get("reasoning"):
                blocks.append({"type": "thinking", "text": res["reasoning"]})
            if res.get("content"):
                blocks.append({"type": "text", "text": res["content"]})
            blocks += [{"type": "tool_call", "id": c["id"], "name": c["name"], "arguments": c["arguments"] or "{}"}
                       for c in calls]
            if persist and blocks:
                await persist("assistant", blocks)
            final = res.get("content") or final
            if not calls:
                if res.get("finish_reason") == "length" and step < max_steps:
                    messages.append({"role": "user", "content": "(Your answer was cut off at the length limit. "
                                                                "Continue exactly where you stopped.)"})
                    continue
                return final
            images: list[dict] = []
            for c in calls:
                try:
                    result = await self._execute(c, by_name)
                except asyncio.CancelledError:
                    if persist:
                        await persist("tool", [{"type": "tool_result", "tool_call_id": c["id"], "name": c["name"],
                                                "text": STOPPED, "images": [], "is_error": True}])
                    raise
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result.text})
                images += result.images
                if persist:
                    await persist("tool", [{"type": "tool_result", "tool_call_id": c["id"], "name": c["name"],
                                            "text": result.text, "images": result.images,
                                            "is_error": result.is_error, "display": result.display}])
            parts = [p for p in (C._image(i) for i in images) if p] if vision(self.model) or self.cloud else []
            if parts:
                messages.append({"role": "user", "content": [{"type": "text", "text": "(Images returned by the "
                                                                                      "tools above.)"}, *parts]})
            reason = self.state.pop("escalate", None)
            if reason and self.cloud is None and self.escalation:
                await self._escalate(messages, reason)
                tools = self.tools()
                by_name = {t.name: t for t in tools}
                schemas = [t.schema() for t in tools]
        note = f"(Stopped after {max_steps} steps - ask me to continue if there is more to do.)"
        self.emit("text", text="\n\n" + note)
        if persist:
            await persist("assistant", [{"type": "text", "text": note}])
        return (final + "\n\n" + note).strip()

    async def _save_partial(self, persist: Persist) -> None:
        """A stopped turn keeps what the model had written so far."""
        if not persist:
            return
        blocks = []
        if self._partial["thinking"]:
            blocks.append({"type": "thinking", "text": self._partial["thinking"]})
        if self._partial["text"]:
            blocks.append({"type": "text", "text": self._partial["text"]})
        if blocks:
            await persist("assistant", blocks)

    async def _fit(self, messages: list[dict], compact) -> list[dict]:
        if self.cloud is not None:
            return messages                       # cloud models have room; we do not summarise for them
        budget = int(self.ctx_limit * float(self.hub.settings.get("agent", "compact_at") or 0.75))
        size = max(self.last_prompt_tokens, C.estimate_tokens(messages))
        if size <= budget:
            return messages
        messages = C.trim_tool_results(messages)
        if C.estimate_tokens(messages) > budget and compact is not None:
            before = C.estimate_tokens(messages)
            messages = await compact(messages)
            self.emit("compacted", before=before, after=C.estimate_tokens(messages))
        if C.estimate_tokens(messages) > budget:
            messages = C.trim_tool_results(messages, keep_last=2, limit=400)
        self.last_prompt_tokens = 0
        return messages

    async def _model_step(self, messages: list[dict], schemas: list[dict], step: int) -> dict:
        t0 = time.monotonic()
        self._partial = {"text": "", "thinking": ""}
        self.emit("step", step=step, model=self.cloud.label if self.cloud else self.model["id"])
        if self.cloud is not None:
            res = None
            clean = [{k: v for k, v in m.items() if k != "reasoning_content"} for m in messages]
            async for kind, data in self.cloud.stream(clean, schemas):
                if kind == "result":
                    res = data
                else:
                    self._forward(kind, data)
            if res is None:
                raise LlmError("the cloud model sent no answer")
        else:
            if self.ticket is not None:
                t_switch = time.monotonic()
                await self.ticket.switch(self.need)
                if time.monotonic() - t_switch > 1:
                    self.steps.append({"type": "load", "model": self.model["id"],
                                       "seconds": round(time.monotonic() - t_switch, 2)})
            room = self.ctx_limit - C.estimate_tokens(messages)
            pl = P.build(self.model, messages, tools=schemas or None, thinking=self.thinking, effort=self.effort,
                         sampling={"max_tokens": max(1024, min(32768, room))})
            acc = Accumulator()
            async for chunk in self.hub.llm.stream_chat(self.hub.main_url, pl):
                for kind, data in acc.feed(chunk):
                    self._forward(kind, data)
            res = acc.result()
        u, t = res.get("usage") or {}, res.get("timings") or {}
        self.last_prompt_tokens = int(u.get("prompt_tokens") or 0)
        self.usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        self.usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
        self.usage["cached_tokens"] += int(t.get("cache_n") or 0)
        rec = {"type": "model", "step": step, "depth": self.depth,
               "model": self.cloud.label if self.cloud else self.model["id"],
               "prompt_tokens": u.get("prompt_tokens"), "cached_tokens": t.get("cache_n"),
               "completion_tokens": u.get("completion_tokens"),
               "prompt_tps": _rate(t, "prompt"), "tps": _rate(t, "predicted"),
               "seconds": round(time.monotonic() - t0, 2)}
        self.steps.append(rec)
        self.emit("usage", **{k: v for k, v in rec.items() if k not in ("type", "depth")})
        return res

    def _forward(self, kind: str, data: dict) -> None:
        if kind == "text":
            self._partial["text"] += data["text"]
            self.emit("text", text=data["text"])
        elif kind == "thinking":
            self._partial["thinking"] += data["text"]
            self.emit("thinking", text=data["text"])
        elif kind == "tool_start":
            self.emit("tool_start", id=data["id"], name=data["name"])
        elif kind == "tool_args":
            self.emit("tool_args", id=data["id"], delta=data["delta"])

    async def _execute(self, call: dict, by_name: dict[str, Tool]) -> ToolResult:
        name, cid = call["name"], call["id"]
        t0 = time.monotonic()
        tool = by_name.get(name)
        args: dict = {}
        if tool is None:
            result = ToolResult(f"There is no tool called {name!r}. Available: {', '.join(sorted(by_name))}.",
                                is_error=True)
        else:
            try:
                args = parse_arguments(call.get("arguments"))
            except ValueError as exc:
                result = ToolResult(f"The arguments are not valid JSON ({exc}). Send them again as one JSON "
                                    "object.", is_error=True)
            else:
                self.emit("tool_call", id=cid, name=name, arguments=args)
                allowed = True
                if tool.needs_approval and not always_allowed(name):
                    decision = await self.run.ask_approval(cid, name, args)
                    if decision == "always":
                        remember_allowed(name)
                    allowed = decision in ("allow", "always")
                if not allowed:
                    result = ToolResult("The user declined this tool call. Do not try it again; continue without "
                                        "it or ask the user.", is_error=True)
                else:
                    try:
                        result = await tool.handler(self.tool_context(cid), args)
                    except asyncio.CancelledError:
                        raise
                    except FileNotFoundError as exc:
                        result = ToolResult(f"Not found: {exc}", is_error=True)
                    except ValueError as exc:
                        result = ToolResult(str(exc), is_error=True)
                    except Exception as exc:  # noqa: BLE001 - the model should see it
                        log.exception("tool %s failed", name)
                        result = ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
        result.text = self._store_long(cid, result.text or "(no output)")
        secs = round(time.monotonic() - t0, 2)
        self.steps.append({"type": "tool", "name": name, "ok": not result.is_error, "seconds": secs,
                           "depth": self.depth})
        self.emit("tool_result", id=cid, name=name, ok=not result.is_error, text=result.text[:6000],
                  images=result.images, display=result.display, seconds=secs)
        self._watch(name, args, result)
        return result

    def _store_long(self, cid: str, text: str) -> str:
        limit = int(self.hub.settings.get("agent", "tool_output_chars") or 12000)
        if len(text) <= limit:
            return text
        rel = f".hub/outputs/{cid}.txt"
        try:
            p = self.workdir.resolve(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            short, _ = clip(text, limit)
            return short + f"\n\n(The full output is in {rel} - read it with read_file and offset/limit.)"
        except OSError:
            return clip(text, limit)[0]

    def _watch(self, name: str, args: dict, result: ToolResult) -> None:
        """Automatic escalation: three failed tool calls in a row, or the same
        call three times in the last six."""
        self.fail_streak = self.fail_streak + 1 if result.is_error else 0
        sig = name + json.dumps(args, sort_keys=True, ensure_ascii=False)[:500]
        self.recent = (self.recent + [sig])[-6:]
        if not self.escalation or self.cloud is not None or "escalate" in self.state:
            return
        if self.fail_streak >= 3:
            self.state["escalate"] = "three tool calls in a row failed"
        elif self.recent.count(sig) >= 3:
            self.state["escalate"] = f"the same {name} call was made three times"

    async def _escalate(self, messages: list[dict], reason: str) -> None:
        self.cloud = cloudmod.CloudClient(self.hub.settings, transport=self.hub.transports.web)
        self.emit("escalated", model=self.cloud.label, reason=reason)
        self.steps.append({"type": "escalated", "model": self.cloud.label, "reason": reason})
        agent: Agent | None = self.hub.extras.get("agent")
        if agent is not None and messages and messages[0].get("role") == "system":
            chat = chats.get_chat(self.chat["id"]) or self.chat
            messages[0] = {"role": "system", "content": agent.system_for(
                chat, self.project, self.workdir, self.model, escalation=False, subagent=self.depth > 0,
                cloud_model=self.cloud.label)}
        messages.append({"role": "user", "content": f"(SLQ Hub: the local model handed this turn over to you "
                                                    f"because {reason}. Continue the task.)"})
        if self.ticket is not None:          # the card is free while the cloud works
            await self.ticket.release()
            self.ticket = None


def _rate(t: dict, kind: str) -> float | None:
    """Tokens per second from llama-server's timings."""
    if t.get(f"{kind}_per_second"):
        return round(float(t[f"{kind}_per_second"]), 1)
    n, ms = t.get(f"{kind}_n"), t.get(f"{kind}_ms")
    return round(n / ms * 1000, 1) if n and ms else None


def always_allowed(tool: str) -> bool:
    with db.connect() as con:
        r = con.execute("SELECT decision FROM tool_permissions WHERE tool=?", (tool,)).fetchone()
    return bool(r and r["decision"] == "always")


def remember_allowed(tool: str) -> None:
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO tool_permissions (tool, decision, created_at) VALUES (?,?,?)",
                    (tool, "always", db.utcnow()))


class Agent:
    """Starts and tracks turns."""

    def __init__(self, hub: Hub):
        self.hub = hub
        self.runs = RunRegistry()
        _ensure_tools_loaded()

    def workdir(self, chat: dict) -> projects.Workdir:
        ws = self.hub.env.workspace_dir
        if chat.get("project_id"):
            return projects.Workdir(projects.project_dir(ws, chat["project_id"]), chat["project_id"], ws,
                                    self.hub.env.sandbox_workspace)
        return projects.Workdir(projects.chat_dir(ws, chat["id"]), None, ws, self.hub.env.sandbox_workspace)

    def start(self, chat_id: int, options: dict | None = None) -> Run:
        if self.runs.active_for_chat(chat_id):
            raise RuntimeError("this chat is already working on an answer")
        run = self.runs.create(chat_id)
        run.task = asyncio.get_running_loop().create_task(self._turn(run, options or {}), name=run.id)
        return run

    def resolve(self, chat: dict, project: dict | None, options: dict) -> tuple[dict, bool | None, str | None]:
        s = self.hub.settings
        cs, ps = chat.get("settings") or {}, (project or {}).get("settings") or {}
        model_id = options.get("model") or cs.get("model") or ps.get("model") or s.get("models", "default")
        model_id = self.hub.catalog.resolve(model_id) or model_id
        entry = self.hub.catalog.get_llm(model_id)
        if not entry or not self.hub.catalog.installed(entry):
            fallback = s.get("models", "default")
            entry = self.hub.catalog.get_llm(fallback)
            if not entry or not self.hub.catalog.installed(entry):
                raise RuntimeError(f"the model {model_id} is not downloaded (Settings -> Models)")
        thinking = options.get("thinking")
        if thinking is None:
            thinking = cs.get("thinking")
        if thinking is None:
            thinking = ps.get("thinking")
        if thinking is None:
            thinking = bool(s.get("agent", "thinking_in_projects" if project else "thinking_in_chats"))
        effort = options.get("effort") or cs.get("effort") or s.get("agent", "effort")
        return entry, thinking, effort

    def system_for(self, chat: dict, project: dict | None, wd: projects.Workdir, entry: dict, *,
                   escalation: bool, subagent: bool = False, cloud_model: str = "") -> str:
        skills = self.hub.extras["skills"].chosen(chat, project)
        mem = self.hub.extras["memory"].index(project["id"] if project else None)
        images = self.hub.extras.get("images")
        user = self.hub.settings.get("agent", "user_name") or (self.hub.env.admin_user or "the user").title()
        return system_prompt(user=user, model_name=entry["name"], folder_text=folder_text(project, wd.sandbox_path),
                             sandbox_path=wd.sandbox_path, memory_index=mem, skills=skills, project=project,
                             summary="" if subagent else chat.get("summary") or "",
                             images_available=bool(images and images.available()),
                             escalation=escalation, subagent=subagent, cloud_model=cloud_model)

    def build_messages(self, chat: dict, project: dict | None, wd: projects.Workdir, entry: dict, run: Run,
                       escalation: bool) -> list[dict]:
        sp = self.system_for(chat, project, wd, entry, escalation=escalation)
        rows = chats.messages(chat["id"], after_id=int(chat.get("summary_upto") or 0))
        keep_from = rows[-12]["id"] if len(rows) > 12 else 0
        return [{"role": "system", "content": sp},
                *C.to_model_messages(rows, run.id, keep_from, vision=vision(entry))]

    async def _compact(self, chat_id: int, engine: Engine, run: Run) -> list[dict]:
        """Fold everything before the latest user message into the chat's
        summary, then rebuild the messages."""
        chat = chats.get_chat(chat_id)
        rows = chats.messages(chat_id, after_id=int(chat.get("summary_upto") or 0))
        split = C.compaction_split(rows)
        if split is None or engine.ticket is None:
            return self.build_messages(chat, engine.project, engine.workdir, engine.model, run, engine.escalation)
        old = [r for r in rows if r["id"] <= split]
        msgs = [{"role": "system", "content": "You condense conversations into notes."},
                *C.to_model_messages(old, None, keep_images_from=10**12, vision=False),
                {"role": "user", "content": C.SUMMARY_PROMPT}]
        if chat.get("summary"):
            msgs.insert(1, {"role": "user", "content": "Summary of what came before:\n" + chat["summary"]})
            msgs.insert(2, {"role": "assistant", "content": "Noted."})
        await engine.ticket.switch(engine.need)
        pl = P.build(engine.model, msgs, thinking=False, sampling={"max_tokens": 2000})
        acc = Accumulator()
        async for chunk in self.hub.llm.stream_chat(self.hub.main_url, pl):
            acc.feed(chunk)
        summary = acc.result()["content"].strip()
        if summary:
            merged = (chat["summary"] + "\n\n" + summary).strip() if chat.get("summary") else summary
            chats.update_chat(chat_id, summary=merged, summary_upto=split)
        chat = chats.get_chat(chat_id)
        engine.chat = chat
        return self.build_messages(chat, engine.project, engine.workdir, engine.model, run, engine.escalation)

    async def _report_position(self, run: Run, holder: dict) -> None:
        last = None
        while True:
            job = holder.get("job")
            if job is not None and job.state == "queued":
                pos = self.hub.scheduler.position(job)
                if pos != last:
                    last = pos
                    run.emit("queued", position=pos, current=self.hub.scheduler.current)
            await asyncio.sleep(1)

    async def _title(self, chat: dict, engine: Engine) -> None:
        if (chat.get("title") or "New chat") != "New chat" or engine.ticket is None:
            return
        rows = chats.messages(chat["id"])
        first = next((b.get("text", "") for r in rows if r["role"] == "user" for b in r["content"]
                      if b.get("type") == "text"), "")
        if not first.strip():
            return
        prompt = ("Write a short title (3-6 words, no quotes, no full stop, in the same language) for a "
                  "conversation that starts with:\n\n" + first[:1500])
        try:
            pl = P.build(engine.model, [{"role": "user", "content": prompt}], thinking=False,
                         sampling={"max_tokens": 30, "temperature": 0.3})
            acc = Accumulator()
            async for chunk in self.hub.llm.stream_chat(self.hub.main_url, pl):
                acc.feed(chunk)
        except LlmError:
            return
        lines = acc.result()["content"].strip().strip('"').splitlines()
        title = lines[0].strip().strip('"*#').rstrip(".")[:80] if lines else ""
        if title:
            chats.update_chat(chat["id"], title=title)
            engine.run.emit("title", title=title)

    async def _turn(self, run: Run, options: dict) -> None:
        chat = chats.get_chat(run.chat_id)
        project = projects.get_project(chat["project_id"]) if chat and chat.get("project_id") else None
        status, error, engine, ticket, pos_task = "done", None, None, None, None
        try:
            if chat is None:
                raise RuntimeError("the chat was deleted")
            entry, thinking, effort = self.resolve(chat, project, options)
            chats.run_started(run.id, chat["id"], entry["id"])
            wd = self.workdir(chat)
            escalation = bool(project and project["settings"].get("cloud_escalation")
                              and cloudmod.configured(self.hub.settings))
            mcp = self.hub.extras.get("mcp")
            if mcp is not None:
                await mcp.ensure_started()
            holder: dict = {}
            run.emit("queued", position=None, current=self.hub.scheduler.current)
            pos_task = asyncio.get_running_loop().create_task(self._report_position(run, holder))
            t0 = time.monotonic()
            ticket = await self.hub.scheduler.acquire(
                "web", "agent", self.hub.model_need(entry["id"]), "interactive", label=chat["title"][:80],
                lease=True, meta={"chat_id": chat["id"], "model": entry["id"], "cancel": run.task.cancel},
                on_job=lambda j: holder.setdefault("job", j))
            pos_task.cancel()
            waited = round(time.monotonic() - t0, 2)
            run.status = "running"
            run.emit("started", model=entry["id"], waited=waited, thinking=thinking)
            engine = Engine(self.hub, run, chat, project, wd, entry, ticket, thinking=thinking, effort=effort,
                            escalation=escalation)
            engine.steps.append({"type": "queue", "seconds": waited})

            async def persist(role: str, blocks: list[dict]) -> None:
                m = chats.add_message(chat["id"], role, blocks, run.id)
                run.emit("message_saved", id=m["id"], role=role)

            messages = self.build_messages(chat, project, wd, entry, run, escalation)
            max_steps = int(self.hub.settings.get("agent", "max_steps") or 60)
            await engine.loop(messages, persist, max_steps=max_steps,
                              compact=lambda msgs: self._compact(chat["id"], engine, run))
            await self._title(chat, engine)
        except asyncio.CancelledError:
            status = "cancelled"
        except (SchedulerError, LlmError, RuntimeError) as exc:
            status, error = "error", str(exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed")
            status, error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            if pos_task is not None and not pos_task.done():
                pos_task.cancel()
            t = engine.ticket if engine is not None else ticket
            if t is not None:
                await t.release(error)
            if project:
                try:
                    projects.touch_project(project["id"])
                except Exception:  # noqa: BLE001
                    pass
            try:
                chats.run_finished(run.id, status, engine.steps if engine else [], engine.usage if engine else {},
                                   error)
            except Exception:  # noqa: BLE001
                log.exception("could not store the run")
            if status == "error":
                run.emit("error", message=error)
            elif status == "cancelled":
                run.emit("cancelled")
            else:
                run.emit("done", usage=engine.usage if engine else {})


def attachment_block(wd: projects.Workdir, up: dict) -> dict:
    """Copy an upload into the working folder's .hub/attachments and make the
    message block that points at it."""
    from ..projects import blobs
    name = Path(up.get("name") or up["sha"][:12]).name or up["sha"][:12]
    rel = f".hub/attachments/{name}"
    p = wd.resolve(rel)
    data = blobs.read(up["sha"])
    if p.exists() and p.read_bytes() != data:
        rel = f".hub/attachments/{up['sha'][:8]}-{name}"
        p = wd.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_bytes(data)
    kind = "image" if (up.get("mime") or "").startswith("image/") else "file"
    return {"type": kind, "sha": up["sha"], "mime": up.get("mime"), "name": name, "size": up.get("size"), "path": rel}
