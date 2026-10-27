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
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .. import db
from ..catalog import Catalog
from ..chats import store as chats
from ..gpu.scheduler import SchedulerError, Ticket
from ..llm import payload as P
from ..llm.client import LlmError
from ..llm.stream import Accumulator, parse_arguments, salvage_cut_write, valid_arguments
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
# Effort (0.38.0, Søren): one choice for how hard a turn works, in the chat (the composer), its project and Settings
# -> Assistant (plain chats / projects). quick: no thinking; think: thinking at the chosen level, half the thinking
# budget a step; extra: thinking at the model's highest level, the whole budget, and the independent review.
LEVELS = ("quick", "think", "extra")
EFFORT_RANK = ("minimal", "low", "medium", "high", "xhigh", "max")
# The cloud from a turn's start (0.37.0): "Continue in the cloud", or the cloud model chosen in the chat's model menu
# (its settings' model is CLOUD) - where a hand-over to the cloud is allowed: web chats in projects that let hard tasks
# go to the cloud, with Settings -> Cloud set up.
CLOUD = "cloud"
NO_CLOUD = ("The cloud model is only for chats in projects that let hard tasks go to the cloud (the project's "
            "settings), with a cloud model set up in Settings -> Cloud.")
CLOUD_OFF = ("This chat's model is the cloud model, but this chat cannot use the cloud now (the project does not let "
             "hard tasks go to the cloud, or Settings -> Cloud is not set up), so {model} answered.")
REVIEW_FIX_STEPS = 12               # the steps the work has at least after a review that found problems (0.30.0)
STEP_CALL, STEP_RESULT = 300, 200    # characters of a subagent's call and its result kept in the run's steps
# A call the end of the context cut off (chat 21: a whole game in one write_file, 13 minutes of output, lost).
CUT_OFF = "This call was cut off: your answer reached {where} after {chars:,} characters of its arguments, so it {what}."
IN_PARTS = ("Write a long file in parts of at most a few hundred lines: write_file with the first part, then "
            "write_file with append: true for each next part.")


def loop_notice(reason: str) -> str:
    return f"It was repeating itself ({reason}), so it was asked to answer with what it has."


def utf8(text: str) -> str:
    """Text that encodes as UTF-8: a lone surrogate (half an emoji in a call's arguments) becomes "?" - stored as it
    is, it would make the chat fail to load."""
    return text.encode("utf-8", "replace").decode("utf-8")


def minutes_text(seconds: float, floor: bool = False) -> str:
    m = int(seconds // 60) if floor else round(seconds / 60)
    return f"{m} minute" + ("" if m == 1 else "s")


WARN_SHARE = 0.8                    # of a WrapUp's time: then it is told to finish


@dataclass
class WrapUp:
    """A subagent that must end with a report: the reviewer (0.32.0) - chat 126's review used all its 24 steps in 33
    minutes and gave none. Near its end - `left` steps before its step limit, or after WARN_SHARE of `seconds` of
    work - the Hub tells it to finish and report (`warn`, with {why}), and its last step - at the step limit, after
    `seconds`, or when it is stuck in a loop - has no tools, so it can only answer (`last`, with {why}). The time is
    checked between steps and leaves out waiting for the user's answer to a question (Engine.waited)."""
    warn: str
    last: str
    seconds: float = 0.0               # 0: no time limit
    left: int = 3
    clock: Callable[[], float] = time.monotonic
    start: float = 0.0                 # set when it is made
    worked: float = 0.0                # seconds of work at the latest look
    warned: str = ""                   # why it was told to finish: "steps" or "time"
    ended: str = ""                    # why its last step came: "steps" or "time"

    def __post_init__(self) -> None:
        self.start = self.clock()

    def due(self, step: int, max_steps: int, waited: float) -> tuple[str, str] | None:
        """("last", why) when this step must be the last, ("warn", why) when it is time to say so, else None."""
        self.worked = max(0.0, self.clock() - self.start - waited)
        if step >= max_steps:
            return "last", "steps"
        if self.seconds and self.worked >= self.seconds:
            return "last", "time"
        if not self.warned:
            if max_steps - step < self.left:
                return "warn", "steps"
            if self.seconds and self.worked >= self.seconds * WARN_SHARE:
                return "warn", "time"
        return None

    def limit(self, why: str, max_steps: int) -> str:
        return f"{max_steps} steps" if why == "steps" else minutes_text(self.seconds)

    def warning(self, why: str, left: int) -> str:
        reason = (f"you have {left} steps left" if why == "steps"
                  else f"{minutes_text(self.worked, floor=True)} of your {minutes_text(self.seconds)} have passed")
        return self.warn.format(why=reason)

    def last_text(self, why: str, max_steps: int) -> str:
        reason = {"steps": f"you have used your {max_steps} steps",
                  "time": f"your {minutes_text(self.seconds)} are up"}.get(why, "you are repeating the same steps")
        return self.last.format(why=reason)


def _ensure_tools_loaded() -> None:
    from .tools import audio, files, history, images, knowledge, shell, video, web, webapp, windows  # noqa: F401
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
        # The most thinking tokens a step may take before it must act (Settings -> Assistant; 0: no limit) - chat 78's
        # Qwen3.8 27B thought 12,808 and 13,354 tokens in single steps, nine minutes each (0.30.0).
        self.thinking_budget = thinking_budget(hub.settings)
        self.need = hub.model_need(model["id"])
        self.cloud: cloudmod.CloudClient | None = None
        self.steps: list[dict] = []
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
        self.state: dict = {}
        self.fail_streak = 0
        self.recent: list[str] = []
        self.last_prompt_tokens = 0
        self.last_step: dict = {}            # the latest model step's record (its tokens, why it stopped)
        self.kept: set[str] = set()          # calls the end of the context cut off whose file was kept
        self.ctx_limit = hub.catalog.context_per_request(model["id"])
        self.schema_tokens = 0               # the tool definitions take room in every request too
        self.cleared_any = False             # tool output of this turn was shortened (chat_history finds it whole)
        self._tools_stale = False            # condensed or cleared: which tools are offered may have changed
        # Real tokens per estimated token, as the last model step measured (at least 1): the estimate counts
        # characters, and hex dumps or binary text cost far more tokens a character than prose - chat 74's last
        # request was 45,004 tokens by the estimate and 62,286 real, so it was not condensed and ran out of room.
        self.token_ratio = 1.0
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
        self.only: set[str] | None = None   # the only tools it may have (the reviewer's: review.REVIEW_TOOLS)
        self.mcp_checks = False              # with `only`: also the MCP tools the review may use (mcp.tools)
        self.level = "quick"                 # the turn's effort (Agent.level_of): extra has the independent review
        self.top_model: str | None = None   # the turn's model, when this engine works for it with another (reviewer)
        self.wrap_up: WrapUp | None = None  # it must end with a report (the reviewer): told so near its end
        self.keeps_model = True              # a hand-over becomes the chat's model (not in a Settings -> Tests run)
        self.waited = 0.0                    # seconds spent waiting for the user's answer to a question (its clock)
        self.answer = ""                     # what the latest model step said (the loop returns the last non-empty)
        self.read_user_at = 0                # the step that read what the user wrote while the turn worked
        # NOTES.md in long work (0.31.0): whether to remind, the file as it was when last looked at, and when.
        self.remind_notes = False
        self.notes_at: tuple | None = None
        self.notes_reminded = 0

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
            if self.only is not None and t.name not in self.only:
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
            if self.only is None:
                out += mcp.tools()
            elif self.mcp_checks:            # the reviewer: a build or a test run on the user's server (0.36.0)
                out += mcp.tools(review=True)
        return out

    async def _inbox(self, messages: list[dict], persist: Persist) -> None:
        """Messages the user sent while this turn worked: stored where the model
        reads them (after the step it was on), shown, and added to the context."""
        if self.depth:
            return
        for item in self.run.take_inbox():
            self.read_user_at = self._step            # this step's answer may be a reply to it ("can you pause?")
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
                   compact: Callable[[list[dict]], Awaitable[list[dict]]] | None = None,
                   review: Callable[[list[dict]], Awaitable[bool]] | None = None) -> str:
        tools = self.tools()
        by_name = {t.name: t for t in tools}
        schemas = self.schemas(tools)
        self.schema_tokens = C.schema_tokens(schemas)
        self._note_prompt(messages, tools, "subagent" if self.depth else "start")
        final = ""
        step = 0
        while step < max_steps:
            step += 1
            self._step = step
            await self._inbox(messages, persist)
            messages = await self._fit(messages, compact)
            if self._tools_stale:                 # condensed or cleared: chat_history may be offered now
                self._tools_stale = False
                tools = self.tools()
                by_name = {t.name: t for t in tools}
                schemas = self.schemas(tools)
                self.schema_tokens = C.schema_tokens(schemas)
            forced_text = self._forced_text(messages, step, max_steps)
            forced = bool(forced_text)
            if forced:                            # stuck in a loop, or the reviewer's last step: only an answer
                messages.append({"role": "user", "content": forced_text})
            try:
                try:
                    res = await self._model_step(messages, [] if forced else schemas, step)
                except LlmError as exc:
                    refused = refused_size(exc)
                    if refused is None or self.cloud is not None:
                        raise
                    # Still too long for the model server (a new result costs more tokens a character than the
                    # last step measured - a hex dump, say): its own count sets the ratio, its own context the
                    # limit (it keeps the size it was started with until it is loaded again, whatever Settings
                    # say now), and the request is fitted again, once.
                    real, room = refused
                    raw = C.estimate_tokens(messages) + (0 if forced else self.schema_tokens)
                    self.token_ratio = max(self.token_ratio, token_ratio(real, raw))
                    if room:
                        self.ctx_limit = min(self.ctx_limit, room)
                    self.last_prompt_tokens = real
                    self.steps.append({"type": "refit", "step": step, "tokens": real, "context": room,
                                       "depth": self.depth})
                    messages = await self._fit(messages, compact)
                    if forced and (messages[-1].get("content") != forced_text):   # condensing rebuilt them
                        messages.append({"role": "user", "content": forced_text})
                    res = await self._model_step(messages, [] if forced else schemas, step)
            except asyncio.CancelledError:
                await self._save_partial(persist)
                raise
            if forced:
                res = {**res, "tool_calls": [], "content": res.get("content") or NO_ANSWER}
            self.answer = res.get("content") or ""
            msg: dict = {"role": "assistant", "content": res["content"]}
            calls = res.get("tool_calls") or []
            if calls:
                msg["tool_calls"] = [{"id": c["id"], "type": "function",       # what the model server can read back
                                      "function": {"name": c["name"], "arguments": valid_arguments(c["arguments"])}}
                                     for c in calls]
            if res.get("reasoning") and not self.cloud:
                msg["reasoning_content"] = res["reasoning"]
            messages.append(msg)
            notes = [self.pending_notice] if self.pending_notice else []
            self.pending_notice = ""
            if forced and self.must_answer:
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
                # The reviewer's last step is its last, cut off or not: its report is what it wrote.
                last = forced and self.wrap_up is not None
                if res.get("finish_reason") == "length" and step < max_steps and not last:
                    messages.append({"role": "user", "content": "(Your answer was cut off at the length limit. "
                                                                "Continue exactly where you stopped.)"})
                    continue
                # Not when this answer replies to what the user just wrote while it worked: chat 126's "kan du holde
                # en pause?" was answered with a status - a review would have sent the work back to go on.
                if review is not None and not forced and self.read_user_at != step and await review(messages):
                    # The independent review found problems: its report is the next message, and the fix has at
                    # least REVIEW_FIX_STEPS steps, however close to the limit the work ended. Checking each
                    # finding means reading again what was read before the answer (and the folder may have changed
                    # meanwhile): look-ups count again, and the loop guard starts over.
                    max_steps = max(max_steps, step + REVIEW_FIX_STEPS)
                    self.epoch += 1
                    self.repeats, self.same_steps = 0, {}
                    continue
                return final
            images: list[dict] = []
            cut = res.get("finish_reason") == "length"
            for c in calls:
                try:
                    result = await self._execute(c, by_name, cut=cut)
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
            self._remind_notes(messages, step)
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
        # The step limit (Settings -> Assistant): the turn may not be done. The UI marks it unfinished and offers
        # Continue (POST /chats/{id}/continue, a "continue" block that tells the model why it stopped).
        self.steps.append({"type": "limit", "steps": max_steps, "depth": self.depth})
        if self.depth:
            note = f"(Stopped after {max_steps} steps - ask me to continue if there is more to do.)"
        else:
            self.emit("step_limit", steps=max_steps)
            note = (f"(Stopped after {max_steps} steps, the most one turn may take. Press Continue, or ask me to "
                    "continue, if there is more to do.)")
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

    def _tokens(self, messages: list[dict], schemas: bool = True) -> int:
        """A request's size in tokens: the estimate from its characters, scaled by what the last step measured."""
        return int((C.estimate_tokens(messages) + (self.schema_tokens if schemas else 0)) * self.token_ratio)

    async def _fit(self, messages: list[dict], compact) -> list[dict]:
        if self.cloud is not None:
            return messages                       # cloud models have room; we do not summarise for them
        given = messages
        fitted = await self._fit_inner(messages, compact)
        if len(fitted) != len(given) or any(a is not b for a, b in zip(fitted, given)):
            # Something was cut or condensed: a look-up the model made earlier may be needed again (the loop guard
            # refuses a repeat within one epoch), and chat_history may be offered now.
            self.epoch += 1
            self.cleared_any = True
            self._tools_stale = True
        return fitted

    async def _fit_inner(self, messages: list[dict], compact) -> list[dict]:
        budget = int(self.ctx_limit * float(self.hub.settings.get("agent", "compact_at") or 0.75))

        def size(msgs: list[dict]) -> int:
            return self._tokens(msgs)

        now = size(messages)
        if now > self.ctx_limit * CLEAR_AT:
            # Old tool output and older steps' thinking, cleared in one go long before the context is full (0.27.0;
            # thinking since 0.28.4): a model answers worse in a long context well before it runs out. Only when it
            # frees a good share - every change to an older message makes llama-server read the request again from
            # there.
            cleared = C.trim_reasoning(C.trim_tool_results(messages, keep_last=KEEP_RESULTS, limit=C.OLD_RESULT_HEAD,
                                                           why="cleared to keep the conversation short"),
                                       keep_last=KEEP_THOUGHTS)
            after = size(cleared)
            if now - after >= self.ctx_limit * CLEAR_GAIN:
                self.steps.append({"type": "cleared", "before": now, "after": after, "depth": self.depth})
                self.emit("cleared", before=now, after=after)
                messages, self.last_prompt_tokens = cleared, 0     # the last real count was of the longer one
        if max(self.last_prompt_tokens, size(messages)) <= budget:
            return messages
        # Over the budget: room is made well below it, so the next steps only add to the request. Trimmed just
        # under it, the next step is over again and an older message changes at every step - and a model with
        # linear-attention layers (Qwen3.5 and later) can only resume its cache from a checkpoint near the start:
        # chat 78 read 36,000 tokens again at each of four steps in a row, about a minute each.
        target = int(self.ctx_limit * REFILL_TO)
        messages = C.trim_reasoning(messages, keep_last=KEEP_THOUGHTS)
        if size(messages) > target:
            messages = C.trim_tool_results(messages)
        if size(messages) > budget and compact is not None:
            before = size(messages)
            folded = await compact(messages)
            if folded is not messages:                # within one long turn there is nothing to fold
                self.emit("compacted", before=before, after=size(folded))
                self._note_prompt(folded, None, "compacted")
            messages = folded
        if size(messages) > target:
            messages = C.trim_tool_results(messages, keep_last=2, limit=400)
        if size(messages) > budget:               # a long turn: shorten its older answers and calls too
            messages = C.trim_assistant(messages, keep_last=4, limit=400)
        if size(messages) > budget:               # a small context: only the newest result and answer stay whole
            messages = C.trim_assistant(C.trim_tool_results(messages, keep_last=1, limit=400), keep_last=1, limit=400)
        if size(messages) > budget:               # still too long (a long turn of many steps): older results become a
            messages = C.trim_assistant(           # line each, older answers a sentence
                C.trim_tool_results(messages, keep_last=1, limit=0, why="left out to make room"), keep_last=1, limit=120)
        if size(messages) > budget:               # the last step's results do not fit together: all but its last
            messages = C.trim_tool_results(messages, keep_last=1, limit=400, why="left out to make room", unread=True)
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
            raw = C.estimate_tokens(messages) + (self.schema_tokens if schemas else 0)
            room = self.ctx_limit - int(raw * self.token_ratio)
            pl = P.build(self.model, messages, tools=schemas or None, thinking=self.thinking, effort=self.effort,
                         sampling={"max_tokens": max(1024, min(32768, room))}, control=True)
            sent = sent_summary(pl, len(messages), len(schemas), raw)
            if self.token_ratio > 1.0:
                sent["token_ratio"] = round(self.token_ratio, 3)
            # The thinking budget, counted here: llama-server streams the thinking about a token a piece (measured
            # on b11783), and at the budget the Hub ends it the way Stop thinking does - only while this step is
            # still thinking, so nothing is ever forced into an answer or a tool call (see P.thinking_fields).
            budget = self.thinking_budget if pl.get("reasoning_control") else 0
            if budget:
                sent["thinking_budget"] = budget
            acc = Accumulator()
            live = None                                   # this step, while its thinking can be ended
            thought, budget_hit = 0, False
            try:
                async for chunk in self.hub.llm.stream_chat(self.hub.main_url, pl):
                    if live is None and pl.get("reasoning_control") and chunk.get("id"):
                        live = self.run.thinking = {"url": self.hub.main_url, "id": chunk["id"]}
                    for kind, data in acc.feed(chunk):
                        if kind in ("text", "tool_start") and live is not None and self.run.thinking is live:
                            self.run.thinking = None      # it answers now: nothing left to stop
                        elif kind == "thinking":
                            thought += 1
                        self._forward(kind, data)
                    if (budget and not budget_hit and thought >= budget and live is not None
                            and self.run.thinking is live and live["id"] not in self.run.thinking_stopped):
                        budget_hit = True                 # once: the server forces the message, then the end tag
                        await self.hub.llm.end_reasoning(live["url"], live["id"])
            finally:
                if live is not None and self.run.thinking is live:
                    self.run.thinking = None
            res = acc.result()
            if live is not None and P.thinking_end(res.get("reasoning") or ""):
                if live["id"] in self.run.thinking_stopped:
                    sent["thinking_ended"] = "stopped"
                elif budget_hit:
                    sent["thinking_ended"] = "budget"
        u, t = res.get("usage") or {}, res.get("timings") or {}
        self.last_prompt_tokens = int(u.get("prompt_tokens") or 0)
        if self.cloud is None and sent.get("estimated_tokens") and self.last_prompt_tokens:
            self.token_ratio = token_ratio(self.last_prompt_tokens, sent["estimated_tokens"])
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
        self.last_step = rec
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

    async def _execute(self, call: dict, by_name: dict[str, Tool], cut: bool = False) -> ToolResult:
        """Run one call. `cut`: the answer it came in ended at the end of the context (finish "length")."""
        name, cid = call["name"], call["id"]
        t0 = time.monotonic()
        tool = by_name.get(name)
        args: dict = {}
        if tool is None:
            result = ToolResult(f"There is no tool called {name!r}. Available: {', '.join(sorted(by_name))}.",
                                is_error=True)
        else:
            args, bad = self._arguments(call, cut)
            if bad is not None:
                result = ToolResult(bad, is_error=True)
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
                    sends = named_files(args, tool.sends_files)      # their content goes too: the card says so
                    decision = await self._ask(cid, name, args, can_chat=chat_id is not None, **where,
                                               **({"sends": sends} if sends else {}))
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
                if cid in self.kept and not result.is_error:
                    result = ToolResult(self._kept_text(call, args, result.text), is_error=True)
        return self._finish(name, cid, args, result, t0)

    def _arguments(self, call: dict, cut: bool) -> tuple[dict, str | None]:
        """A call's arguments, or why it gets none. When the end of the context cut the call off, the model is
        told so; a write_file keeps what it had written (its id goes into self.kept)."""
        raw = call.get("arguments") or ""
        try:
            return parse_arguments(raw), None
        except ValueError as exc:
            if not cut:
                return {}, f"The arguments are not valid JSON ({exc}). Send them again as one JSON object."
        kept = salvage_cut_write(raw) if call["name"] == "write_file" else None
        if kept is None:
            return {}, self._cut_text(raw, "did not run") + " " + IN_PARTS
        self.kept.add(call["id"])
        return kept, None

    def _cut_text(self, raw: str, what: str) -> str:
        step = self.last_step
        prompt, written = int(step.get("prompt_tokens") or 0), int(step.get("completion_tokens") or 0)
        cap = int((step.get("sent") or {}).get("max_tokens") or 0)
        where = (f"the limit for one answer ({cap:,} tokens)" if cap and written >= cap else
                 f"the end of the context ({self.ctx_limit:,} tokens: {prompt:,} for the conversation, {written:,} "
                 "that you wrote)")
        return CUT_OFF.format(where=where, chars=len(raw), what=what)

    def _kept_text(self, call: dict, args: dict, done: str) -> str:
        content = args["content"]
        tail = content[-300:]
        fence = "`" * max(3, max((len(r) for r in re.findall(r"`+", tail)), default=0) + 1)
        part = (f"a part of {len(content):,} characters added to the end of the file, which now ends with"
                if args.get("append") else f"the first {len(content):,} characters of the file, ending with")
        return (self._cut_text(call.get("arguments") or "", "did not finish") + f" What it had is kept: {done} "
                f"That is {part}:\n{fence}\n{tail}\n{fence}\n"
                "The file is not complete. Add the rest with write_file and append: true, starting exactly after "
                "those characters. " + IN_PARTS)

    async def _ask(self, cid: str, name: str, args: dict, **extra) -> str:
        """The user's answer to a call that asks first. Meanwhile the card is free for others: the turn's lease is
        given up and taken again with the answer (the same ticket, so its subagents keep it too). If the wait is
        cancelled - the user pressed Stop - or fails, the turn is ending, so the card stays given up (the outer
        release() is then a no-op on it) rather than being taken back only to be let go again."""
        ticket = self.ticket
        clock = self.wrap_up.clock if self.wrap_up is not None else time.monotonic
        t0 = clock()
        try:
            if ticket is not None:
                await ticket.pause()
            decision = await self.run.ask_approval(cid, name, args, **extra)    # Stop while waiting propagates
            self.emit("approval_answered", id=cid, decision=decision)           # the buttons go, even if it queues
            if ticket is not None and ticket.paused:
                await self._resume(ticket)
        finally:
            self.waited += clock() - t0                 # the user's time, not the work's (WrapUp)
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
        if last is not None:                     # (the reviewer works for the turn: the turn's model stays named)
            self.run.emit("started", model=self.top_model or self.model["id"],
                          waited=round(time.monotonic() - t0, 2), thinking=self.thinking)

    def _finish(self, name: str, cid: str, args: dict, result: ToolResult, t0: float) -> ToolResult:
        result.text = self._store_long(cid, result.text or "(no output)")
        secs = round(time.monotonic() - t0, 2)
        rec = {"type": "tool", "name": name, "ok": not result.is_error, "seconds": secs, "depth": self.depth}
        if self.depth:     # a subagent's or the reviewer's calls are kept nowhere else: the export shows them (0.32.0)
            rec.update(parent=self.parent, call=utf8(json.dumps(args, ensure_ascii=False)[:STEP_CALL]),
                       result=utf8(" ".join((result.text or "").split())[:STEP_RESULT]))
        self.steps.append(rec)
        self.emit("tool_result", id=cid, name=name, ok=not result.is_error, text=result.text[:6000],
                  images=result.images, display=result.display, seconds=secs)
        self._watch(name, args, result)
        return result

    def _remind_notes(self, messages: list[dict], step: int) -> None:
        """Long work keeps its notes in NOTES.md (0.31.0): a turn that has made something for REMIND_AFTER steps
        without writing the file is reminded, and again every REMIND_EVERY steps while it is not written. Chat 126's
        model went 54 steps without one although its prompt asked for it."""
        from . import notes_file
        from .review import made_something
        if not self.remind_notes or self.depth:
            return
        due = self.notes_reminded + notes_file.REMIND_EVERY if self.notes_reminded else notes_file.REMIND_AFTER
        if step < due or not made_something(self.steps):
            return
        self.notes_reminded = step
        now = notes_file.stamp(self.workdir.root)
        if now is not None and now != self.notes_at:      # written since the last look: the next one counts from here
            self.notes_at = now
            return
        name = now[0] if now else notes_file.NAME
        messages.append({"role": "user", "content": notes_file.REMINDER.format(steps=step, name=name)})
        note = f"It was reminded to write what it found in {name} (step {step})."
        self.pending_notice = f"{self.pending_notice} {note}".strip()
        self.emit("notes_reminder", step=step, name=name)

    def _forced_text(self, messages: list[dict], step: int, max_steps: int) -> str:
        """The Hub's message for a step without tools, or "" when this step may use them: a model stuck in a loop
        must answer, and a subagent that must end with a report (wrap_up: the reviewer) is told near its end to
        finish - its last step only answers."""
        w = self.wrap_up
        if self.must_answer:
            return w.last_text("loop", max_steps) if w is not None else MUST_ANSWER
        if w is None:
            return ""
        due = w.due(step, max_steps, self.waited)
        if due is None:
            return ""
        kind, why = due
        rec = {"type": "wrap_up", "why": why, "step": step, "minutes": round(w.worked / 60, 1), "depth": self.depth,
               "parent": self.parent}
        shown = {"why": why, "step": step, "worked": round(w.worked), "limit": w.limit(why, max_steps)}
        if kind == "warn":
            w.warned = why
            messages.append({"role": "user", "content": w.warning(why, max_steps - step + 1)})
            self.steps.append(rec)
            self.emit("wrap_up", left=max_steps - step + 1, **shown)
            return ""
        w.ended = why
        self.steps.append({**rec, "last": True})
        self.emit("wrap_up", last=True, **shown)
        return w.last_text(why, max_steps)

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
        if result.is_error and (result.text.startswith(DECLINED) or result.hub_fault):
            return                           # the card or ComfyUI failed: a stronger model meets the same card
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

    def start_in_cloud(self, reason: str) -> None:
        """The whole turn on the cloud model (0.37.0: the user chose it): no card - nothing to give back, nobody to
        hand over to, and no independent review (it needs the card, as after a hand-over to the cloud)."""
        self.cloud = cloudmod.CloudClient(self.hub.settings, transport=self.hub.transports.web)
        self.steps.append({"type": "cloud", "model": self.cloud.label, "reason": reason})

    async def _escalate(self, messages: list[dict], reason: str) -> None:
        self.must_answer, self.repeats, self.same_steps, self.fail_streak, self.recent = "", 0, {}, 0, []
        self.cloud = cloudmod.CloudClient(self.hub.settings, transport=self.hub.transports.web)
        self.emit("escalated", model=self.cloud.label, reason=reason)
        self.steps.append({"type": "escalated", "model": self.cloud.label, "reason": reason})
        self.pending_notice = f"{self.pending_notice} Handed over to {self.cloud.label}: {reason}.".strip()
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


    def _keep_model(self, model_id: str) -> bool:
        """The chat's model from now on (its settings, as the model chip under the message box sets them)."""
        try:
            return chats.update_chat(self.chat["id"], settings={"model": model_id}) is not None
        except Exception:  # noqa: BLE001 - the turn goes on with it either way
            log.exception("could not keep the model for the chat")
            return False

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
        # The chat goes on with the model that took over (0.33.0, Søren): its next message would otherwise switch
        # the card back to the one that got stuck - a model load first, and the work back with the model that could
        # not do it. Not in a person's chat (her device keeps her choice).
        kept = self.device is None and self.keeps_model and self._keep_model(target["id"])
        self.emit("escalated", model=target["name"], model_id=target["id"], reason=reason, local=True,
                  **({"chat_model": target["id"]} if kept else {}))
        self.steps.append({"type": "escalated", "model": target["id"], "from": before["id"], "reason": reason,
                           "local": True})
        goes_on = f" The chat goes on with {target['name']}." if kept else ""
        self.pending_notice = (f"{self.pending_notice} Handed over from {before['name']} to {target['name']}: "
                               f"{reason}.{goes_on}").strip()
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


def thinking_budget(settings) -> int:
    """Settings -> Assistant's most thinking tokens in one step (0: no limit); the form saves it as text."""
    try:
        return max(0, int(float(settings.get("agent", "thinking_budget") or 0)))
    except (TypeError, ValueError):
        return 0


def first_text(rows: list[dict]) -> str:
    """The first thing the user wrote in a conversation."""
    return next((b.get("text", "") for r in rows if r["role"] == "user" for b in r["content"]
                 if b.get("type") == "text"), "")


SENT_SAMPLING = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty", "repeat_penalty")


TOO_LONG = re.compile(r"request \((\d+) tokens\) exceeds the available context size(?: \((\d+) tokens\))?")


def refused_size(exc: Exception) -> tuple[int, int | None] | None:
    """(the request's real size, the server's context) when the model server refused a request as too long."""
    m = TOO_LONG.search(str(exc))
    return (int(m.group(1)), int(m.group(2)) if m.group(2) else None) if m else None


MIN_SUMMARY_ROOM = 1000             # estimated tokens of old messages a summary request must at least take in
CLEAR_AT = 0.5                      # of the context: old tool output is cleared (all but the newest results) ...
CLEAR_GAIN = 0.15                   # ... when that frees at least this share of it
KEEP_RESULTS = 4                    # the newest tool results kept whole when clearing
KEEP_THOUGHTS = 2                   # the newest steps whose thinking stays (0.28.4)
REFILL_TO = 0.6                     # over the budget, the lighter cuts go down to this share of the context (0.28.4)
MIN_FOLD = 0.1                      # condensing waits until what it can fold is at least this share of the context


def token_ratio(real: int, estimated: int) -> float:
    """Real tokens per estimated token, between 1 (the estimate is not trusted lower) and 3."""
    return min(3.0, max(1.0, real / estimated)) if estimated > 0 and real > 0 else 1.0


def last_token_ratio(runs: list[dict]) -> float:
    """The ratio of a chat's latest measured model step, for its next turn's first request (1 when none). A
    subagent's or reviewer's step (depth) measured another conversation, maybe with another model: not that one."""
    for r in reversed(runs):
        for s in reversed(r.get("steps") or []):
            est = (s.get("sent") or {}).get("estimated_tokens") if isinstance(s.get("sent"), dict) else None
            if s.get("type") == "model" and not s.get("depth") and s.get("prompt_tokens") and est:
                return token_ratio(int(s["prompt_tokens"]), int(est))
    return 1.0


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


def named_files(args: dict, params: tuple[str, ...]) -> list[str]:
    """The paths a call's file arguments name (Tool.sends_files), each once: what the approval card lists."""
    out: list[str] = []
    for p in params:
        value = args.get(p)
        for path in value if isinstance(value, list) else [value]:
            if isinstance(path, str) and path.strip() and path not in out:
                out.append(path)
    return out


class Agent:
    """Starts and tracks turns."""

    def __init__(self, hub: Hub):
        self.hub = hub
        self.runs = RunRegistry()
        self.moving: set[int] = set()        # chats whose files are being moved into a project (0.31.0)
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
        if chat_id in self.moving:
            raise RuntimeError("this chat is being moved into a project - send it again in a moment")
        run = self.runs.create(chat_id)
        run.task = asyncio.get_running_loop().create_task(self._turn(run, options or {}), name=run.id)
        return run

    def level_of(self, chat: dict, project: dict | None, options: dict) -> tuple[str, bool]:
        """The turn's effort level, and whether the user chose it for the chat or the message (not a default - a
        model's own thinking mode, Settings -> Models, still decides those). A `thinking` choice from before 0.38.0
        reads as quick or think."""
        cs, ps = chat.get("settings") or {}, (project or {}).get("settings") or {}
        for src, chosen in ((options, True), (cs, True), (ps, False)):
            if src.get("level") in LEVELS:
                return src["level"], chosen
            if isinstance(src.get("thinking"), bool):
                return ("think" if src["thinking"] else "quick"), chosen
        default = self.hub.settings.get("agent", "level_projects" if project else "level_chats")
        return (default if default in LEVELS else "quick"), False

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
        level, chosen = self.level_of(chat, project, options)
        thinking = level != "quick"
        if not chosen and entry.get("thinking_mode") in ("on", "off"):          # Settings -> Models
            thinking = entry["thinking_mode"] == "on"
        known = (entry.get("thinking") or {})
        levels = [e for e in known.get("efforts") or [] if isinstance(e, str)]
        if level == "extra" and levels:                   # the model's highest level
            effort = max(levels, key=lambda e: EFFORT_RANK.index(e) if e in EFFORT_RANK else 2)
        else:
            effort = (options.get("effort") or cs.get("effort") or known.get("default_effort")
                      or s.get("agent", "effort"))
        return entry, thinking, effort

    def skills_for(self, chat: dict | None, project: dict | None, device=None) -> list[dict]:
        """The skills the user chose for the chat and its project (0.23.0: picked by hand)."""
        from .skills import turn_skills
        return turn_skills(self.hub.extras["skills"], chat, project, device)

    def system_for(self, chat: dict, project: dict | None, wd: projects.Workdir, entry: dict, *,
                   handover_to: str = "", subagent: bool = False, cloud_model: str = "",
                   taken_over_from: str = "", device=None, cloud_chosen: bool = False) -> str:
        skills = self.skills_for(chat, project, device)
        images = self.hub.extras.get("images")
        audio = self.hub.extras.get("audio")
        sound = [a for a in ("speech", "music", "sound", "voice", "separate") if audio and audio.available(a)] \
            if not subagent else []
        video_pipe = self.hub.extras.get("video")
        video = bool(video_pipe and video_pipe.available()) and not subagent
        if device is not None:                   # a person's chat: their name, no memory, code if allowed
            return system_prompt(user=device.name, model_name=Catalog.label(entry),
                                 folder_text=device_folder_text(wd.sandbox_path, device.code, device.left),
                                 sandbox_path=wd.sandbox_path, memory_index=[], skills=skills, project=None,
                                 images_available=bool(images and images.available()), handover_to=handover_to,
                                 subagent=subagent, taken_over_from=taken_over_from, memory=False,
                                 code=device.code, dropped=device.dropped, sound=sound, video=video,
                                 internet_asks=internet.asking(self.hub.settings))
        from .tools import webapp as webapp_tool
        from .tools import windows as windows_tool
        mem = self.hub.extras["memory"].index(project["id"] if project else None)
        user = self.hub.settings.get("agent", "user_name") or (self.hub.env.admin_user or "the user").title()
        from . import notes_file
        notes = notes_file.find(wd.root) if notes_file.wanted(self.hub.settings) and not subagent else None
        moved = str((chat.get("settings") or {}).get("moved_from") or "") if project else ""
        renamed = (chat.get("settings") or {}).get("moved_renamed") if moved else None
        return system_prompt(user=user, model_name=Catalog.label(entry),
                             folder_text=folder_text(project, wd.sandbox_path, notes.name if notes else "", moved,
                                                     renamed if isinstance(renamed, dict) else None),
                             sandbox_path=wd.sandbox_path, memory_index=mem, skills=skills, project=project,
                             summary="" if subagent else chat.get("summary") or "",
                             images_available=bool(images and images.available()),
                             handover_to=handover_to, subagent=subagent, cloud_model=cloud_model,
                             cloud_chosen=cloud_chosen,
                             taken_over_from=taken_over_from, sound=sound, video=video,
                             internet_asks=internet.asking(self.hub.settings), windows=windows_tool.ready(self.hub),
                             web=webapp_tool.ready(self.hub))

    def build_messages(self, chat: dict, project: dict | None, wd: projects.Workdir, entry: dict, run: Run,
                       handover_to: str = "", taken_over_from: str = "", cloud_model: str = "",
                       cloud_chosen: bool = False) -> list[dict]:
        sp = self.system_for(chat, project, wd, entry, handover_to=handover_to, taken_over_from=taken_over_from,
                             cloud_model=cloud_model, cloud_chosen=cloud_chosen)
        rows = chats.messages(chat["id"], after_id=int(chat.get("summary_upto") or 0))
        keep_from = rows[-12]["id"] if len(rows) > 12 else 0
        return [{"role": "system", "content": sp},
                *C.to_model_messages(rows, run.id, keep_from, vision=vision(entry) or bool(cloud_model),
                                     keep_results_from=C.results_from(rows, run.id))]

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
        foldable = C.estimate_tokens(C.to_model_messages(old, None, keep_images_from=10**12, vision=False))
        if foldable * engine.token_ratio < engine.ctx_limit * MIN_FOLD:
            # Too little before this turn to be worth a summary (chat 78: the new chat's notes, 1,200 tokens, were
            # condensed into notes as long, and the changed system prompt made the server read 48,000 tokens again).
            return messages
        head = [{"role": "system", "content": "You condense conversations into notes."}]
        if chat.get("summary"):
            head += [{"role": "user", "content": "The notes so far, from earlier in the conversation:\n"
                                                 + chat["summary"]},
                     {"role": "assistant", "content": "Noted."}]
        tail = [{"role": "user", "content": C.SUMMARY_PROMPT}]
        # The request must fit the context: room for the notes it writes, a margin for the tokenizer.
        notes = min(2000, engine.ctx_limit // 4)
        room = int((engine.ctx_limit * 0.8 - notes) / engine.token_ratio) - C.estimate_tokens(head + tail)
        if room < MIN_SUMMARY_ROOM:
            # The summary so far leaves no room for what it should take in: folding now would drop those
            # messages unread (summary_upto moves past them). The caller shortens the request instead.
            log.warning("chat %s: no room to condense (%s tokens)", chat_id, room)
            return messages
        body = C.shrink_for_summary(C.to_model_messages(old, None, keep_images_from=10**12, vision=False), room)
        msgs = [*head, *body, *tail]
        await engine.ticket.switch(engine.need)
        pl = P.build(engine.model, msgs, thinking=False, sampling={"max_tokens": notes})
        acc = Accumulator()
        async for chunk in self.hub.llm.stream_chat(self.hub.main_url, pl):
            acc.feed(chunk)
        summary = acc.result()["content"].strip()
        if summary:                                   # the new notes replace the old ones (which they took in)
            chats.update_chat(chat_id, summary=summary, summary_upto=split)
            if await self.notes_to_file(chat, engine.workdir, summary, "the chat was condensed", engine.device):
                from . import notes_file                   # the Hub's write is not the model keeping its notes
                engine.notes_at = await asyncio.to_thread(notes_file.stamp, engine.workdir.root)
        chat = chats.get_chat(chat_id)
        engine.chat = chat
        return self.build_messages(chat, engine.project, engine.workdir, engine.model, run, engine.handover_label(),
                                   engine.handed_over_from)

    async def _write_handover(self, engine: Engine, old_id: int, persist) -> None:
        """The first answer of a chat that continues another: the notes the work goes on from, written from the
        old chat's notes and what came after them - streamed like any answer, kept as the new chat's first one."""
        old = chats.get_chat(old_id)
        if old is None:
            raise RuntimeError("the chat to continue from was deleted")
        rows = chats.messages(old_id, after_id=int(old.get("summary_upto") or 0))
        engine.thinking = False
        engine.token_ratio = last_token_ratio(chats.runs_for_chat(old_id))
        head = [{"role": "system", "content": "You write the notes a conversation continues from in a new chat."}]
        if old.get("summary"):
            head += [{"role": "user", "content": "The notes so far, from earlier in the conversation:\n"
                                                 + old["summary"]},
                     {"role": "assistant", "content": "Noted."}]
        tail = [{"role": "user", "content": C.HANDOVER_PROMPT}]
        notes = min(2000, engine.ctx_limit // 4)
        room = int((engine.ctx_limit * 0.8 - notes) / engine.token_ratio) - C.estimate_tokens(head + tail)
        body = C.shrink_for_summary(C.to_model_messages(rows, None, keep_images_from=10**12, vision=False,
                                                        keep_results_from=C.results_from(rows, None)),
                                    room) if room >= MIN_SUMMARY_ROOM else []
        res = await engine._model_step([*head, *body, *tail], [], 1)
        engine.last_step["handover"] = True            # not a measure of how full the new chat is
        text = (res.get("content") or "").strip()
        if not text:
            raise RuntimeError("the model wrote no notes - try again")
        await persist("assistant", [{"type": "text", "text": text}])
        await self.notes_to_file(engine.chat, engine.workdir, text, f"continued from the chat \"{old['title']}\"",
                                 engine.device)

    async def notes_to_file(self, chat: dict, wd, notes: str, why: str, device=None) -> str | None:
        """The chat's notes into NOTES.md in its working folder (0.31.0), unless someone else keeps that file -
        best effort: the notes are the chat's in any case."""
        from . import notes_file
        if device is not None or wd is None or not notes_file.wanted(self.hub.settings):
            return None
        before = (chat.get("settings") or {}).get("continued_from")
        try:
            return await asyncio.to_thread(notes_file.write, wd, chat["id"], chat.get("title") or "this chat", notes,
                                           why, (before,) if before else ())
        except Exception:  # noqa: BLE001 - a folder or a sync history that will not take it: the chat keeps its notes
            log.exception("could not write the notes file of chat %s", chat.get("id"))
            return None

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
            from .skills import materialize                # the chosen skills' files, for code (.hub/skills/)
            await asyncio.to_thread(materialize, self.hub.extras["skills"], self.skills_for(chat, project), wd.root)
            escalation = bool(project and project["settings"].get("cloud_escalation")
                              and cloudmod.configured(self.hub.settings))
            chose_cloud = (chat.get("settings") or {}).get("model") == CLOUD
            if options.get("cloud") and not escalation:
                raise RuntimeError(NO_CLOUD)
            in_cloud = escalation and (bool(options.get("cloud")) or chose_cloud)
            cloud_label = cloudmod.CloudClient(self.hub.settings).label if in_cloud else ""
            mcp = self.hub.extras.get("mcp")
            if mcp is not None:
                await mcp.ensure_started()
            from .tools import webapp as webapp_tool      # (registered with the others, in their order)
            from .tools import windows as windows_tool
            await windows_tool.refresh(self.hub)          # whether windows_app is there, before the prompt says so
            await webapp_tool.refresh(self.hub)           # and web_app
            holder: dict = {}
            t0 = time.monotonic()
            test = bool(options.get("test"))              # a test run's turn (Settings -> Tests) lets chats go first
            if not in_cloud:                              # the cloud needs no card: it is not queued for
                run.emit("queued", position=None, current=self.hub.scheduler.current)
                pos_task = asyncio.get_running_loop().create_task(self._report_position(run, holder))
                ticket = await self.hub.scheduler.acquire(
                    "tests" if test else "web", "agent", self.hub.model_need(entry["id"]),
                    "background" if test else "interactive", label=chat["title"][:80],
                    lease=True, meta={"chat_id": chat["id"], "model": entry["id"], "cancel": run.task.cancel,
                                      "run_id": run.id},
                    on_job=lambda j: holder.setdefault("job", j))
                pos_task.cancel()
            waited = round(time.monotonic() - t0, 2)
            run.status = "running"
            run.emit("started", model=cloud_label or entry["id"], waited=waited, thinking=thinking)
            engine = Engine(self.hub, run, chat, project, wd, entry, ticket, thinking=thinking, effort=effort,
                            escalation=escalation)
            engine.level = self.level_of(chat, project, options)[0]
            if engine.level != "extra" and engine.thinking_budget:      # Think: half the budget a step (0.38.0)
                engine.thinking_budget = max(1, engine.thinking_budget // 2)
            if in_cloud:
                engine.start_in_cloud("the user continued in the cloud" if options.get("cloud")
                                      else "the chat's model is the cloud model")
            elif chose_cloud:                             # the project's switch went off since it was chosen
                engine.pending_notice = CLOUD_OFF.format(model=Catalog.label(entry))
            engine.token_ratio = last_token_ratio(chats.runs_for_chat(chat["id"]))   # the last turn's measure
            from . import notes_file                      # NOTES.md as it was when the turn started (0.31.0)
            engine.remind_notes = notes_file.wanted(self.hub.settings) and not test
            engine.keeps_model = not test                 # a test run measures its model, and keeps the chat's
            engine.notes_at = await asyncio.to_thread(notes_file.stamp, wd.root)
            engine.turn_options = {k: options[k] for k in ("thinking", "effort") if options.get(k) is not None}
            engine.steps.append({"type": "queue", "seconds": waited})

            async def persist(role: str, blocks: list[dict]) -> dict:
                m = chats.add_message(chat["id"], role, blocks, run.id)
                run.emit("message_saved", id=m["id"], role=role)
                return m

            if options.get("handover_from"):              # Continue in a new chat (0.27.0): the notes, nothing else
                await self._write_handover(engine, int(options["handover_from"]), persist)
            else:
                messages = self.build_messages(chat, project, wd, entry, run, engine.handover_label(),
                                               cloud_model=cloud_label,
                                               cloud_chosen=in_cloud and not options.get("cloud"))
                max_steps = int(self.hub.settings.get("agent", "max_steps") or 60)
                from .review import review as review_turn
                # A test run (Settings -> Tests) measures the model it tests: no other model's review in its steps.
                await engine.loop(messages, persist, max_steps=max_steps,
                                  compact=lambda msgs: self._compact(chat["id"], engine, run, msgs),
                                  review=None if test else (lambda msgs: review_turn(self, engine, chat, project, wd,
                                                                                     before, msgs, persist)))
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
            usage_now = engine.usage if engine else {}
            try:                                          # the card's energy and its cost (0.24.0)
                from .. import usage
                usage.finish_turn(self.hub, run.id, usage_now, "tests" if options.get("test") else "owner",
                                  project["id"] if project else None)
            except Exception:  # noqa: BLE001
                log.exception("could not count the turn's usage")
            try:
                chats.run_finished(run.id, status, engine.steps if engine else [], usage_now, error)
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
                    try:                                  # (never another handover: the notes were this run's)
                        self.start(run.chat_id, {k: v for k, v in options.items() if k != "handover_from"})
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
