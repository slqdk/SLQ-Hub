"""A run is one agent turn. Its events are kept in memory so the browser
can follow it live over SSE and pick it up again after a reload."""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import AsyncIterator

FINAL = ("done", "error", "cancelled")
KEEP_FINISHED_S = 3600


class Run:
    def __init__(self, chat_id: int):
        self.id = "run_" + secrets.token_hex(8)
        self.chat_id = chat_id
        self.status = "queued"
        self.events: list[dict] = []
        self.started = time.time()
        self.ended: float | None = None
        self.task: asyncio.Task | None = None
        self.approvals: dict[str, asyncio.Future] = {}
        # What the user sent while the turn works ({"id", "blocks"}): the loop hands it to the model after the
        # step it is on; whatever is left when the turn ends starts the next turn.
        self.inbox: list[dict] = []
        self._cond = asyncio.Condition()
        self._seq = 0

    def take_inbox(self) -> list[dict]:
        items, self.inbox = self.inbox, []
        return items

    def emit(self, kind: str, **data) -> dict:
        self._seq += 1
        ev = {"seq": self._seq, "type": kind, "t": round(time.time() - self.started, 3), "data": data}
        self.events.append(ev)
        if kind in FINAL:
            self.status = kind
            self.ended = time.time()
        asyncio.get_running_loop().create_task(self._notify())
        return ev

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    @property
    def finished(self) -> bool:
        return self.status in FINAL

    async def stream(self, after: int = 0) -> AsyncIterator[dict]:
        """Every event after `after`, then live ones until the run ends."""
        sent = after
        while True:
            pending = [e for e in self.events if e["seq"] > sent]
            for e in pending:
                sent = e["seq"]
                yield e
            if self.finished and not [e for e in self.events if e["seq"] > sent]:
                return
            timed_out = False
            async with self._cond:
                if self.finished or any(e["seq"] > sent for e in self.events):
                    continue                     # something arrived while we were not looking
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=15)
                except asyncio.TimeoutError:
                    timed_out = True
            if timed_out:
                yield {"seq": sent, "type": "ping", "t": round(time.time() - self.started, 3), "data": {}}

    async def ask_approval(self, call_id: str, tool: str, arguments: dict, timeout: float = 600, **extra) -> str:
        """Wait for the user's answer to a tool call: allow, always or deny (no answer in `timeout`: deny).
        `extra` goes with the event: what is asked (ask) and, inside a subagent, where (depth, parent)."""
        fut = asyncio.get_running_loop().create_future()
        self.approvals[call_id] = fut
        self.emit("approval_needed", id=call_id, tool=tool, arguments=arguments, **extra)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return "deny"
        finally:
            self.approvals.pop(call_id, None)

    def answer_approval(self, call_id: str, decision: str) -> bool:
        fut = self.approvals.get(call_id)
        if fut and not fut.done():
            fut.set_result(decision)
            return True
        return False


class RunRegistry:
    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}

    def create(self, chat_id: int) -> Run:
        self._gc()
        run = Run(chat_id)
        self.runs[run.id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    def drop(self, run_id: str) -> None:
        """Forget a run now, events and all (a person's turn, once their device has it)."""
        self.runs.pop(run_id, None)

    def active_for_chat(self, chat_id: int) -> Run | None:
        for r in self.runs.values():
            if r.chat_id == chat_id and not r.finished:
                return r
        return None

    def cancel(self, run_id: str) -> bool:
        r = self.runs.get(run_id)
        if r and r.task and not r.task.done():
            r.task.cancel()
            return True
        return False

    def _gc(self) -> None:
        now = time.time()
        for rid in [rid for rid, r in self.runs.items() if r.finished and r.ended and now - r.ended > KEEP_FINISHED_S]:
            self.runs.pop(rid, None)
