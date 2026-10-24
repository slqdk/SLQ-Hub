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
from ..catalog import Catalog
from ..chats import store as chats
from ..gpu.scheduler import SchedulerError, Ticket
from ..llm import payload as P
from ..llm.client import LlmError
from ..llm.stream import Accumulator, parse_arguments
from ..projects import store as projects
from ..state import Hub
from . import cloud as cloudmod
from . import context as C
from . import internet
from .prompt import device_folder_text, folder_text, system_prompt
from .runs import Run, RunRegistry
from .tools import REGISTRY, Tool, ToolContext, ToolResult
from .tools.base import clip

log = logging.getLogger("hub.agent")
Persist = Callable[[str, list[dict]], Awaitable[None]] | None
STOPPED = "Stopped by the user before the tool finished."
DECLINED = "The user declined this tool call. Do not try it again; continue without it or ask the user."
# Small models can get stuck repeating the same step. A look-up repeated with nothing changed in between is
# refused; after LOOP_REPEATS refusals, or the same step LOOP_SAME_STEPS times, the model must answer.
REPEATED = ("You already called {name} with exactly these arguments in this turn, and nothing has changed since, "
            "so the result is the same as the earlier one above. Do not repeat a call: use what you have, try "
            "something different, or answer.")
MUST_ANSWER = ("(SLQ Hub: you are repeating the same steps and getting the same results. Stop using tools now. "
               "Answer the user with what you have found so far, and say plainly what you could not find.)")
NO_ANSWER = "(The model kept repeating the same tool calls and gave no answer. Ask again, or try another model.)"
LOOP_REPEATS = 3
LOOP_SAME_STEPS = 3


def loop_notice(reason: str) -> str:
    return f"It was repeating itself ({reason}), so it was asked to answer with what it has."


def _ensure_tools_loaded() -> None:
    from .tools import audio, files, images, knowledge, shell, web, windows  # noqa: F401 - registration
    from . import meta_tools  # noqa: F401


def vision(model: dict) -> bool:
    return "vision" in (model.get("capabilities") or [])


def local_target(hub: Hub, current: dict) -> dict | None:
    """The stronger model on this server a stuck turn is handed over to
    (Settings -> Assistant), when it is downloaded and not the one already working."""
    want = str(hub.settings.get("agent", "escalate_to") or "").strip()
    if not want:
        return None
    entry = hub.catalog.get_llm(hub.catalog.resolve(want) or want)
    if not entry or entry["id"] == current["id"] or not hub.catalog.installed(entry):
        return None
    return entry


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
        self.schema_tokens = 0               # the tool definitions take room in every request too
        self._partial = {"text": "", "thinking": ""}
        # Loop guard (see REPEATED): +1 epoch after every call that may change something; read-only calls
        # remember the epoch they ran in; refused repeats and identical steps are counted.
        self.epoch = 0
        self.seen: dict[str, tuple[int, bool, int]] = {}   # look-up -> (epoch, it worked, tries)
        self.repeats = 0
        self.same_steps: dict[str, int] = {}
        self.must_answer = ""
        # Hand-over (a loop, three failed calls, or the escalate tool): first to the stronger local model,
        # then - in projects that allow it - to the cloud. A note goes with the next answer.
        self.handed_over_from = ""            # the model's name this turn was handed over from
        self.turn_options: dict = {}          # the turn's own choices (thinking, effort), for the next model
        self.pending_notice = ""
        self._step = 0
        self._tool_names: list[str] = []
        self._last_prompt: tuple | None = None
        # A person's chat, kept on their device (agent/device.py): no memory, libraries, MCP or cloud, code only
        # when allowed, artifacts and files go back to the device.
        self.device = None

    # ------------------------------------------------------------ helpers
    def emit(self, kind: str, **data) -> None:
        if self.depth:
            data.update(depth=self.depth, parent=self.parent)
        self.run.emit(kind, **data)

    def tool_context(self, call_id: str, online: bool = True, online_once: bool = False) -> ToolContext:
        return ToolContext(hub=self.hub, run=self.run, chat=self.chat, project=self.project, workdir=self.workdir,
                           ticket=self.ticket, model=self.model, depth=self.depth, call_id=call_id, state=self.state,
                           engine=self, online=online, online_once=online_once)

    def local_target(self) -> dict | None:
        if self.depth or self.cloud is not None or self.handed_over_from:
            return None
        return local_target(self.hub, self.model)

    def can_hand_over(self) -> bool:
        return self.local_target() is not None or (self.escalation and self.cloud is None)

    def handover_label(self) -> str:
        """Who takes over if this model gets stuck (for its prompt and the escalate tool); "" = nobody."""
        target = self.local_target()
        if target is not None:
            return f"{Catalog.label(target)}, a stronger model on this server"
        if self.escalation and self.cloud is None:
            return "a stronger cloud model"
        return ""

    def tools(self) -> list[Tool]:
        ctx = self.tool_context("")
        out = []
        for t in REGISTRY.values():
            if self.depth and not t.in_subagent:
                continue
            if t.name == "escalate" and not self.can_hand_over():
                continue
            if self.device is not None and (t.name in DEVICE_SKIP or (t.name == "bash" and not self.device.code)):
                continue
            try:
                if t.available(ctx):
                    out.append(t)
            except Exception:  # noqa: BLE001 - a broken check hides the tool, nothing more
                continue
        mcp = self.hub.extras.get("mcp")
        if mcp is not None and self.device is None:
            out += mcp.tools()
        return out

    async def _inbox(self, messages: list[dict], persist: Persist) -> None:
        """Messages the user sent while this turn worked: stored where the model
        reads them (after the step it was on), shown, and added to the context."""
        if self.depth:
            return
        for item in self.run.take_inbox():
            blocks = [{**b, "while_working": True} if b.get("type") == "text" else b for b in item["blocks"]]
            m = await persist("user", blocks) if persist else None
            row = m or {"id": 0, "role": "user", "content": blocks, "run_id": self.run.id}
            self.emit("user_message", id=item["id"], message=row)
            messages.extend(C.to_model_messages([row], vision=vision(self.model) or self.cloud is not None))

    def schemas(self, tools: list[Tool]) -> list[dict]:
        ctx = self.tool_context("")
        return [t.schema(ctx) for t in tools]

    # ------------------------------------------------------------ the loop
    async def loop(self, messages: list[dict], persist: Persist = None, max_steps: int = 60,
                   compact: Callable[[list[dict]], Awaitable[list[dict]]] | None = None) -> str:
        tools = self.tools()
        by_name = {t.name: t for t in tools}
        schemas = self.schemas(tools)
        self.schema_tokens = C.schema_tokens(schemas)
        self._note_prompt(messages, tools, "subagent" if self.depth else "start")
        final = ""
        for step in range(1, max_steps + 1):
            self._step = step
            await self._inbox(messages, persist)
            messages = await self._fit(messages, compact)
            forced = bool(self.must_answer)
            if forced:                            # stuck in a loop: no tools this time, only an answer
                messages.append({"role": "user", "content": MUST_ANSWER})
            try:
                res = await self._model_step(messages, [] if forced else schemas, step)
            except asyncio.CancelledError:
                await self._save_partial(persist)
                raise
            if forced:
                res = {**res, "tool_calls": [], "content": res.get("content") or NO_ANSWER}
            msg: dict = {"role": "assistant", "content": res["content"]}
            calls = res.get("tool_calls") or []
            if calls:
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for c in calls]
            if res.get("reasoning") and not self.cloud:
                msg["reasoning_content"] = res["reasoning"]
            messages.append(msg)
            notes = [self.pending_notice] if self.pending_notice else []
            self.pending_notice = ""
            if forced:
                notes.append(loop_notice(self.must_answer))
            blocks: list[dict] = [{"type": "notice", "text": n} for n in notes]   # shown, never sent to a model
            if res.get("reasoning"):
                blocks.append({"type": "thinking", "text": res["reasoning"]})
            if res.get("content"):
                blocks.append({"type": "text", "text": res["content"]})
            blocks += [{"type": "tool_call", "id": c["id"], "name": c["name"], "arguments": c["arguments"] or "{}"}
                       for c in calls]
            if persist and blocks:
                await persist("assistant", blocks)
            final = res.get("content") or final
            if calls:
                same = json.dumps([res.get("content") or "", [[c["name"], c.get("arguments") or ""] for c in calls]],
                                  ensure_ascii=False)
                self.same_steps[same] = self.same_steps.get(same, 0) + 1
                if self.same_steps[same] >= LOOP_SAME_STEPS:
                    names = ", ".join(dict.fromkeys(c["name"] for c in calls))
                    self._stuck(f"the same step ({names}) {self.same_steps[same]} times", loop=True)
            if not calls:
                if self.run.inbox and not self.depth and step < max_steps:
                    continue                      # the user wrote meanwhile: answer that too, in this turn
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
            if reason:
                if self.local_target() is not None:
                    await self._hand_over(messages, reason)
                elif self.escalation and self.cloud is None:
                    await self._escalate(messages, reason)
                tools = self.tools()
                by_name = {t.name: t for t in tools}
                schemas = self.schemas(tools)
                self.schema_tokens = C.schema_tokens(schemas)
                self._note_prompt(messages, tools, "escalated" if self.cloud is not None else "handed over")
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

        def size(msgs: list[dict]) -> int:
            return C.estimate_tokens(msgs) + self.schema_tokens

        if max(self.last_prompt_tokens, size(messages)) <= budget:
            return messages
        messages = C.trim_tool_results(messages)
        if size(messages) > budget and compact is not None:
            before = size(messages)
            folded = await compact(messages)
            if folded is not messages:                # within one long turn there is nothing to fold
                self.emit("compacted", before=before, after=size(folded))
                self._note_prompt(folded, None, "compacted")
            messages = folded
        if size(messages) > budget:
            messages = C.trim_tool_results(messages, keep_last=2, limit=400)
        if size(messages) > budget:               # a long turn: shorten its older answers and calls too
            messages = C.trim_assistant(messages, keep_last=4, limit=400)
        if size(messages) > budget:               # a small context: only the newest result and answer stay whole
            messages = C.trim_assistant(C.trim_tool_results(messages, keep_last=1, limit=400), keep_last=1, limit=400)
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
            sent = {"cloud": True, "tools": len(schemas), "messages": len(messages)}
        else:
            if self.ticket is not None:
                t_switch = time.monotonic()
                await self.ticket.switch(self.need)
                if time.monotonic() - t_switch > 1:
                    self.steps.append({"type": "load", "model": self.model["id"],
                                       "seconds": round(time.monotonic() - t_switch, 2)})
            room = self.ctx_limit - C.estimate_tokens(messages) - (self.schema_tokens if schemas else 0)
            pl = P.build(self.model, messages, tools=schemas or None, thinking=self.thinking, effort=self.effort,
                         sampling={"max_tokens": max(1024, min(32768, room))})
            sent = sent_summary(pl, len(messages), len(schemas), self.ctx_limit - room)
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
               "seconds": round(time.monotonic() - t0, 2),
               "sent": sent, "finish": res.get("finish_reason")}
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
                key = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
                epoch, ok, tries = self.seen.get(key, (-1, False, 0))
                if tool.read_only and epoch == self.epoch and (ok or tries >= 2):   # a failure may be retried once
                    self.repeats += 1
                    result = ToolResult(REPEATED.format(name=name), is_error=True)
                    if self.repeats >= LOOP_REPEATS:
                        self._stuck(f"{self.repeats} repeated tool calls", loop=True)
                    return self._finish(name, cid, args, result, t0)
                allowed, online, once = True, True, False
                where = {"depth": self.depth, "parent": self.parent} if self.depth else {}
                owner = self.device is None               # a person answers once each time
                chat_id = self.chat.get("id") if owner else None

                def ok_here(key: str) -> bool:            # always, or in this chat (the owner's answers)
                    return owner and (always_allowed(key) or (chat_id is not None and allowed_in_chat(chat_id, key)))

                def remember(decision: str, key: str) -> None:
                    if owner and decision == "always":
                        remember_allowed(key)
                    elif owner and decision == "chat" and chat_id is not None:
                        remember_in_chat(chat_id, key)
                if name in internet.TOOLS and internet.asking(self.hub.settings):
                    ask, online = internet.question(name, args, ok_here, await internet.code_can_go_offline(self.hub))
                    if ask is not None:
                        decision = await self._ask(
                            cid, name, args, ask={**ask, "can_always": owner, "can_chat": chat_id is not None}, **where)
                        remember(decision, ask["key"])
                        allowed = decision in ("allow", "always", "chat")
                        once = decision == "allow" or not owner
                if allowed and tool.needs_approval and not ok_here(name):
                    decision = await self._ask(cid, name, args, can_chat=chat_id is not None, **where)
                    remember(decision, name)
                    allowed = decision in ("allow", "always", "chat")
                if not allowed:
                    result = ToolResult(DECLINED, is_error=True)
                else:
                    try:
                        result = await tool.handler(self.tool_context(cid, online, online and once), args)
                    except asyncio.CancelledError:
                        raise
                    except FileNotFoundError as exc:
                        result = ToolResult(f"Not found: {exc}", is_error=True)
                    except ValueError as exc:
                        result = ToolResult(str(exc), is_error=True)
                    except Exception as exc:  # noqa: BLE001 - the model should see it
                        log.exception("tool %s failed", name)
                        result = ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
                if tool.read_only:
                    self.seen[key] = (self.epoch, not result.is_error, (tries if epoch == self.epoch else 0) + 1)
                else:
                    self.epoch += 1                   # it may have changed something: look-ups count again
        return self._finish(name, cid, args, result, t0)

    async def _ask(self, cid: str, name: str, args: dict, **extra) -> str:
        """The user's answer to a call that asks first. Meanwhile the card is free for others: the turn's lease is
        given up and taken again with the answer (the same ticket, so its subagents keep it too). If the wait is
        cancelled - the user pressed Stop - or fails, the turn is ending, so the card stays given up (the outer
        release() is then a no-op on it) rather than being taken back only to be let go again."""
        ticket = self.ticket
        if ticket is not None:
            await ticket.pause()
        decision = await self.run.ask_approval(cid, name, args, **extra)    # Stop while waiting propagates from here
        self.emit("approval_answered", id=cid, decision=decision)           # the buttons go, even while it queues
        if ticket is not None and ticket.paused:
            await self._resume(ticket)
        return decision

    async def _resume(self, ticket) -> None:
        """Queue for the card again after a question; the chat shows the place in the queue when it has to wait."""
        holder: dict = {}
        task = asyncio.get_running_loop().create_task(ticket.resume(on_job=lambda j: holder.setdefault("job", j)))
        t0 = time.monotonic()
        try:
            done, _ = await asyncio.wait({task}, timeout=0.5)
            last = None
            while not done:
                job = holder.get("job")
                if job is not None and job.state == "queued":
                    pos = self.hub.scheduler.position(job)
                    if pos != last:
                        last = pos
                        self.run.emit("queued", position=pos, current=self.hub.scheduler.current)
                done, _ = await asyncio.wait({task}, timeout=1)
            task.result()
        except BaseException:
            task.cancel()
            raise
        if last is not None:
            self.run.emit("started", model=self.model["id"], waited=round(time.monotonic() - t0, 2),
                          thinking=self.thinking)

    def _finish(self, name: str, cid: str, args: dict, result: ToolResult, t0: float) -> ToolResult:
        result.text = self._store_long(cid, result.text or "(no output)")
        secs = round(time.monotonic() - t0, 2)
        self.steps.append({"type": "tool", "name": name, "ok": not result.is_error, "seconds": secs,
                           "depth": self.depth})
        self.emit("tool_result", id=cid, name=name, ok=not result.is_error, text=result.text[:6000],
                  images=result.images, display=result.display, seconds=secs)
        self._watch(name, args, result)
        return result

    def _loop(self, reason: str) -> None:
        """The model is stuck: its next step must be an answer, without tools."""
        if self.must_answer:
            return
        self.must_answer = reason
        self.steps.append({"type": "loop", "reason": reason, "depth": self.depth})
        self.emit("loop", reason=reason)

    def _store_long(self, cid: str, text: str) -> str:
        limit = int(self.hub.settings.get("agent", "tool_output_chars") or 12000)
        if len(text) <= limit:
            return text
        rel = f".hub/outputs/{cid}.txt"
        try:
            p = self.workdir.resolve(rel)
            projects.safe_write(self.workdir.root, p, text.encode("utf-8"))
            short, _ = clip(text, limit)
            return short + f"\n\n(The full output is in {rel} - read it with read_file and offset/limit.)"
        except (OSError, ValueError):
            return clip(text, limit)[0]

    def _watch(self, name: str, args: dict, result: ToolResult) -> None:
        """Automatic hand-over: three failed tool calls in a row, or the same
        call three times in the last six. A call the user declined is neither:
        no means no, not "hand it to someone who will" (the cloud, perhaps)."""
        if result.is_error and result.text.startswith(DECLINED):
            return
        self.fail_streak = self.fail_streak + 1 if result.is_error else 0
        sig = name + json.dumps(args, sort_keys=True, ensure_ascii=False)[:500]
        self.recent = (self.recent + [sig])[-6:]
        if self.fail_streak >= 3:
            self._stuck("three tool calls in a row failed", loop=False)
        elif self.recent.count(sig) >= 3:
            self._stuck(f"the same {name} call was made three times", loop=False)

    def _note_prompt(self, messages: list[dict], tools: list[Tool] | None, reason: str) -> None:
        """Keep the system prompt and tool names the model works with, when they change."""
        if self.device is not None:          # a person's chat: the Hub keeps nothing of it
            return
        if tools is not None:
            self._tool_names = [t.name for t in tools]
        if not messages or messages[0].get("role") != "system":
            return
        system = str(messages[0].get("content") or "")
        key = (system, tuple(self._tool_names), self.model["id"])
        if key == self._last_prompt:
            return
        self._last_prompt = key
        try:
            chats.save_prompt(self.run.id, self.depth, self._step, self.model["id"], reason, system, self._tool_names)
        except Exception:  # noqa: BLE001 - a record for later, never a reason to fail the turn
            log.exception("could not store the prompt")

    def _stuck(self, reason: str, loop: bool) -> None:
        """Hand the turn over if someone can take it; otherwise a loop ends in an answer without tools."""
        if self.must_answer or "escalate" in self.state:
            return
        if self.can_hand_over():
            self.state["escalate"] = reason
        elif loop:
            self._loop(reason)

    async def _escalate(self, messages: list[dict], reason: str) -> None:
        self.must_answer, self.repeats, self.same_steps, self.fail_streak, self.recent = "", 0, {}, 0, []
        self.cloud = cloudmod.CloudClient(self.hub.settings, transport=self.hub.transports.web)
        self.emit("escalated", model=self.cloud.label, reason=reason)
        self.steps.append({"type": "escalated", "model": self.cloud.label, "reason": reason})
        self.pending_notice = f"Handed over to {self.cloud.label}: {reason}."
        agent: Agent | None = self.hub.extras.get("agent")
        if agent is not None and messages and messages[0].get("role") == "system":
            chat = chats.get_chat(self.chat["id"]) or self.chat
            messages[0] = {"role": "system", "content": agent.system_for(
                chat, self.project, self.workdir, self.model, subagent=self.depth > 0, cloud_model=self.cloud.label,
                device=self.device)}
        messages.append({"role": "user", "content": f"(SLQ Hub: the local model handed this turn over to you "
                                                    f"because {reason}. Continue the task.)"})
        if self.ticket is not None:          # the card is free while the cloud works
            await self.ticket.release()
            self.ticket = None


    async def _hand_over(self, messages: list[dict], reason: str) -> None:
        """The turn goes on with the stronger local model, inside the same
        lease (the card switches before the next step): its own system
        prompt, a note on why, and no thinking of the other model."""
        target = self.local_target()
        if target is None:
            return
        before = self.model
        agent: Agent | None = self.hub.extras.get("agent")
        chat = chats.get_chat(self.chat["id"]) or self.chat
        if agent is not None:
            try:
                _, self.thinking, self.effort = agent.resolve(chat, self.project, {**self.turn_options,
                                                                                   "model": target["id"]})
            except RuntimeError:
                pass
        self.model, self.need = target, self.hub.model_need(target["id"])
        self.ctx_limit = self.hub.catalog.context_per_request(target["id"])
        self.handed_over_from = Catalog.label(before)
        self.must_answer, self.repeats, self.same_steps, self.fail_streak, self.recent = "", 0, {}, 0, []
        self.emit("escalated", model=target["name"], model_id=target["id"], reason=reason, local=True)
        self.steps.append({"type": "escalated", "model": target["id"], "from": before["id"], "reason": reason,
                           "local": True})
        self.pending_notice = f"Handed over from {before['name']} to {target['name']}: {reason}."
        for m in messages:
            m.pop("reasoning_content", None)
        if not vision(target):
            messages[:] = C.without_images(messages)
        if agent is not None and messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": agent.system_for(    # a person's own prompt, not the owner's
                chat, self.project, self.workdir, target, handover_to=self.handover_label(),
                taken_over_from=Catalog.label(before), device=self.device)}
        messages.append({"role": "user", "content": f"(SLQ Hub: {Catalog.label(before)} handed this turn over to "
                                                    f"you because {reason}. The steps so far are above - check them "
                                                    "rather than trust them. Continue the task and answer.)"})
        job = getattr(self.ticket, "job", None)
        if job is not None:
            job.meta["model"] = target["id"]


# Tools a person's chat does not get (agent/device.py): their memory and the owner's libraries stay out.
DEVICE_SKIP = {"memory", "library_search"}


def first_text(rows: list[dict]) -> str:
    """The first thing the user wrote in a conversation."""
    return next((b.get("text", "") for r in rows if r["role"] == "user" for b in r["content"]
                 if b.get("type") == "text"), "")


SENT_SAMPLING = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty", "repeat_penalty")


def sent_summary(pl: dict, messages: int, tools: int, estimated: int) -> dict:
    """What a model step asked llama-server for (stored with the step, for later)."""
    out = {"sampling": {k: pl[k] for k in SENT_SAMPLING if k in pl},
           "thinking": (pl.get("chat_template_kwargs") or {}).get("enable_thinking"),
           "max_tokens": pl.get("max_tokens"), "tools": tools, "messages": messages, "estimated_tokens": estimated}
    if pl.get("reasoning_effort"):
        out["effort"] = pl["reasoning_effort"]
    return out


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


def allowed_in_chat(chat_id: int, tool: str) -> bool:
    """Allowed for the rest of this chat ("Allow in this chat")."""
    with db.connect() as con:
        return con.execute("SELECT 1 FROM chat_permissions WHERE chat_id=? AND tool=?", (chat_id, tool)).fetchone() \
            is not None


def remember_in_chat(chat_id: int, tool: str) -> None:
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO chat_permissions (chat_id, tool, created_at) VALUES (?,?,?)",
                    (chat_id, tool, db.utcnow()))


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
        if thinking is None and entry.get("thinking_mode") in ("on", "off"):     # Settings -> Models
            thinking = entry["thinking_mode"] == "on"
        if thinking is None:
            thinking = bool(s.get("agent", "thinking_in_projects" if project else "thinking_in_chats"))
        effort = options.get("effort") or cs.get("effort") or s.get("agent", "effort")
        return entry, thinking, effort

    def system_for(self, chat: dict, project: dict | None, wd: projects.Workdir, entry: dict, *,
                   handover_to: str = "", subagent: bool = False, cloud_model: str = "",
                   taken_over_from: str = "", device=None) -> str:
        skills = self.hub.extras["skills"].chosen(chat, project)
        images = self.hub.extras.get("images")
        audio = self.hub.extras.get("audio")
        sound = [a for a in ("speech", "music", "sound") if audio and audio.available(a)] if not subagent else []
        if device is not None:                   # a person's chat: their name, no memory, code if allowed
            return system_prompt(user=device.name, model_name=Catalog.label(entry),
                                 folder_text=device_folder_text(wd.sandbox_path, device.code),
                                 sandbox_path=wd.sandbox_path, memory_index=[], skills=skills, project=None,
                                 images_available=bool(images and images.available()), handover_to=handover_to,
                                 subagent=subagent, taken_over_from=taken_over_from, memory=False,
                                 code=device.code, dropped=device.dropped, sound=sound,
                                 internet_asks=internet.asking(self.hub.settings))
        from .tools import windows as windows_tool
        mem = self.hub.extras["memory"].index(project["id"] if project else None)
        user = self.hub.settings.get("agent", "user_name") or (self.hub.env.admin_user or "the user").title()
        return system_prompt(user=user, model_name=Catalog.label(entry),
                             folder_text=folder_text(project, wd.sandbox_path),
                             sandbox_path=wd.sandbox_path, memory_index=mem, skills=skills, project=project,
                             summary="" if subagent else chat.get("summary") or "",
                             images_available=bool(images and images.available()),
                             handover_to=handover_to, subagent=subagent, cloud_model=cloud_model,
                             taken_over_from=taken_over_from, sound=sound,
                             internet_asks=internet.asking(self.hub.settings), windows=windows_tool.ready(self.hub))

    def build_messages(self, chat: dict, project: dict | None, wd: projects.Workdir, entry: dict, run: Run,
                       handover_to: str = "", taken_over_from: str = "") -> list[dict]:
        sp = self.system_for(chat, project, wd, entry, handover_to=handover_to, taken_over_from=taken_over_from)
        rows = chats.messages(chat["id"], after_id=int(chat.get("summary_upto") or 0))
        keep_from = rows[-12]["id"] if len(rows) > 12 else 0
        return [{"role": "system", "content": sp},
                *C.to_model_messages(rows, run.id, keep_from, vision=vision(entry))]

    async def _compact(self, chat_id: int, engine: Engine, run: Run, messages: list[dict]) -> list[dict]:
        """Fold everything before the latest user message into the chat's
        summary, then rebuild the messages. Within one long turn there is
        nothing before it: the messages stay as they are (the caller trims)."""
        chat = chats.get_chat(chat_id)
        rows = chats.messages(chat_id, after_id=int(chat.get("summary_upto") or 0))
        split = C.compaction_split(rows)
        if split is None or engine.ticket is None:
            return messages
        old = [r for r in rows if r["id"] <= split]
        head = [{"role": "system", "content": "You condense conversations into notes."}]
        if chat.get("summary"):
            head += [{"role": "user", "content": "Summary of what came before:\n" + chat["summary"]},
                     {"role": "assistant", "content": "Noted."}]
        tail = [{"role": "user", "content": C.SUMMARY_PROMPT}]
        # The request must fit the context: room for the notes it writes, a margin for the tokenizer.
        notes = min(2000, engine.ctx_limit // 4)
        room = int(engine.ctx_limit * 0.8) - notes - C.estimate_tokens(head + tail)
        body = C.shrink_for_summary(C.to_model_messages(old, None, keep_images_from=10**12, vision=False), room)
        msgs = [*head, *body, *tail]
        await engine.ticket.switch(engine.need)
        pl = P.build(engine.model, msgs, thinking=False, sampling={"max_tokens": notes})
        acc = Accumulator()
        async for chunk in self.hub.llm.stream_chat(self.hub.main_url, pl):
            acc.feed(chunk)
        summary = acc.result()["content"].strip()
        if summary:
            merged = (chat["summary"] + "\n\n" + summary).strip() if chat.get("summary") else summary
            chats.update_chat(chat_id, summary=merged, summary_upto=split)
        chat = chats.get_chat(chat_id)
        engine.chat = chat
        return self.build_messages(chat, engine.project, engine.workdir, engine.model, run, engine.handover_label(),
                                   engine.handed_over_from)

    async def _note_files(self, engine: Engine, wd: projects.Workdir, before: dict) -> None:
        """The files this turn made or changed in the working folder: a step of the run, shown under the answer."""
        try:
            after = await asyncio.to_thread(projects.snapshot, wd.root)
        except Exception:  # noqa: BLE001
            log.exception("could not list the folder after the turn")
            return
        files = projects.produced(before, after) if after is not None else []
        if files:
            engine.steps.append({"type": "files", "files": files})
            engine.run.emit("files", files=files)

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
        title = await self.title_for(engine, first_text(rows))
        if title:
            chats.update_chat(chat["id"], title=title)
            engine.run.emit("title", title=title)

    async def title_for(self, engine: Engine, first: str) -> str:
        """A short title for a conversation that starts with `first` ("" when it cannot be made)."""
        if not first.strip():
            return ""
        prompt = ("Write a short title (3-6 words, no quotes, no full stop, in the same language) for a "
                  "conversation that starts with:\n\n" + first[:1500])
        try:
            pl = P.build(engine.model, [{"role": "user", "content": prompt}], thinking=False,
                         sampling={"max_tokens": 30, "temperature": 0.3})
            acc = Accumulator()
            async for chunk in self.hub.llm.stream_chat(self.hub.main_url, pl):
                acc.feed(chunk)
        except LlmError:
            return ""
        lines = acc.result()["content"].strip().strip('"').splitlines()
        return lines[0].strip().strip('"*#').rstrip(".")[:80] if lines else ""

    async def _turn(self, run: Run, options: dict) -> None:
        chat = chats.get_chat(run.chat_id)
        project = projects.get_project(chat["project_id"]) if chat and chat.get("project_id") else None
        status, error, engine, ticket, pos_task = "done", None, None, None, None
        wd, before = None, None
        try:
            if chat is None:
                raise RuntimeError("the chat was deleted")
            entry, thinking, effort = self.resolve(chat, project, options)
            chats.run_started(run.id, chat["id"], entry["id"])
            wd = self.workdir(chat)
            before = await asyncio.to_thread(projects.snapshot, wd.root)     # what the turn makes is shown after it
            escalation = bool(project and project["settings"].get("cloud_escalation")
                              and cloudmod.configured(self.hub.settings))
            mcp = self.hub.extras.get("mcp")
            if mcp is not None:
                await mcp.ensure_started()
            from .tools import windows as windows_tool    # (registered with the others, in their order)
            await windows_tool.refresh(self.hub)          # whether windows_app is there, before the prompt says so
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
            engine.turn_options = {k: options[k] for k in ("thinking", "effort") if options.get(k) is not None}
            engine.steps.append({"type": "queue", "seconds": waited})

            async def persist(role: str, blocks: list[dict]) -> dict:
                m = chats.add_message(chat["id"], role, blocks, run.id)
                run.emit("message_saved", id=m["id"], role=role)
                return m

            messages = self.build_messages(chat, project, wd, entry, run, engine.handover_label())
            max_steps = int(self.hub.settings.get("agent", "max_steps") or 60)
            await engine.loop(messages, persist, max_steps=max_steps,
                              compact=lambda msgs: self._compact(chat["id"], engine, run, msgs))
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
            if engine is not None and wd is not None and before is not None:
                await self._note_files(engine, wd, before)
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
            # Written after the last step it could be read in (or with Stop): it becomes the next turn.
            leftover = run.take_inbox()
            if leftover and chats.get_chat(run.chat_id):
                for item in leftover:
                    chats.add_message(run.chat_id, "user", [{k: v for k, v in b.items() if k != "while_working"}
                                                            for b in item["blocks"]])
                if status != "error":
                    try:
                        self.start(run.chat_id, options)
                    except RuntimeError:
                        pass


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
    if not p.exists():
        projects.safe_write(wd.root, p, data)
    kind = "image" if (up.get("mime") or "").startswith("image/") else "file"
    return {"type": kind, "sha": up["sha"], "mime": up.get("mime"), "name": name, "size": up.get("size"), "path": rel}


REF_MAX = 20                     # files of the working folder in one message
REF_SHARE = 0.3                  # of the model's context, for the text of those files
REF_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
REF_IMAGE_MAX = 20_000_000


def ref_blocks(wd: projects.Workdir, paths: list[str], budget: int) -> list[dict]:
    """Blocks for files of the working folder that the user put in a message.
    Text files are copied into the message as they are now, while they fit in
    `budget` characters, so the model has them without a tool call; pictures
    are shown to it; anything else is named, for read_file. Raises ValueError
    for a path outside the folder and FileNotFoundError for one that is not a
    file."""
    from ..projects import blobs
    out: list[dict] = []
    seen: set[str] = set()
    for raw in paths[:REF_MAX]:
        p = wd.resolve(raw)
        if not p.is_file():
            raise FileNotFoundError(raw)
        rel = wd.rel(p)
        if rel in seen:
            continue
        seen.add(rel)
        size = p.stat().st_size
        meta = {"path": rel, "name": p.name, "size": size, "mime": projects.mime_of(p.name)}
        if p.suffix.lower() in REF_IMAGE_EXT and size <= REF_IMAGE_MAX:
            up = chats.save_upload(p.read_bytes(), p.name, meta["mime"])
            out.append({"type": "image", "sha": up["sha"], **meta, "ref": True})
            continue
        block = {"type": "file_ref", **meta}
        if size <= budget * 4:                     # UTF-8: at most 4 bytes a character
            data = p.read_bytes()
            if projects.is_text(data):
                text = data.decode("utf-8", "replace")
                if len(text) <= budget:
                    block.update(sha=blobs.store(data), lines=len(text.splitlines()), inline=True)
                    budget -= len(text)
        out.append(block)
    return out
