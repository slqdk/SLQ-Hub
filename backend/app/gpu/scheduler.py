"""The GPU scheduler - the queue every model call goes through.

There is one card. A web chat turn, an API call from FamilyLog, an image
job and library indexing all compete for it, so all of them ask here first.
The queue lives in memory (as in SLQ Companion): a request that was waiting
when the backend restarted cannot be resumed anyway - its connection is gone.

Concepts (docs/DESIGN.md section 3):

* residency - what is loaded on the card: "llm:<model id>", "comfy",
  "embedgpu:<id>". One at a time.
* slots     - how many jobs one residency serves at once (an LLM: its
  llama-server -np; ComfyUI: 1). Never more requests than slots are sent, so
  the queue the UI shows is the real one.
* job       - one unit of work: owner, priority, the residency it needs.
* priority  - interactive (web UI) < normal (apps) < background. A job gains
  one level per `aging_s` seconds of waiting, so nothing starves.
* lease     - an agent turn holds its slot for the whole turn, pauses between
  tool calls included, and may switch residency inside it (to ComfyUI for an
  image and back).

Admission (re-run whenever something changes):

1. Leases asking to switch go first: nothing new starts. A lease whose
   target is loaded continues as soon as a slot of it is free. Otherwise the
   card switches once every running job is either finished or itself
   waiting for a switch (so two leases that both want ComfyUI cannot wait on
   each other) - to the target of the lease that asked first.
2. The head is the waiting job with the best (effective priority, arrival).
   If it needs what is loaded and a slot is free, it starts.
3. If it needs something else: while a lease holds the card (it cannot
   switch anyway) or while the head has waited less than `affinity_window`,
   waiting jobs for the loaded residency with the same or better priority
   may use free slots. Otherwise nothing new starts; when the running jobs
   are done the card switches and the head starts.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

log = logging.getLogger("hub.scheduler")

PRIORITY = {"interactive": 0, "normal": 1, "background": 2}
PRIORITY_NAME = {v: k for k, v in PRIORITY.items()}


class SchedulerError(RuntimeError):
    """A job could not get the card (the model would not load, ...)."""


class QueueTimeout(SchedulerError):
    """A job waited longer than it was willing to."""


class Backend(Protocol):
    async def load(self, need: str) -> None: ...
    async def unload(self, need: str) -> None: ...
    def slots(self, need: str) -> int: ...
    # optional: async def alive(self, need: str) -> bool  (is what is loaded still there?)


@dataclass(eq=False)
class Job:
    id: int
    owner: str
    kind: str
    need: str
    priority: int
    label: str
    lease: bool
    enqueued_at: float
    meta: dict = field(default_factory=dict)
    state: str = "queued"
    started_at: float | None = None
    ended_at: float | None = None
    error: str | None = None
    admitted: asyncio.Future | None = None
    switch_to: str | None = None
    switch_fut: asyncio.Future | None = None
    switch_at: float = 0.0

    def info(self, now: float, position: int | None = None) -> dict:
        return {
            "id": self.id, "owner": self.owner, "kind": self.kind, "need": self.need,
            "priority": PRIORITY_NAME.get(self.priority, str(self.priority)), "label": self.label,
            "lease": self.lease, "state": self.state, "position": position,
            "waited_s": round((self.started_at or now) - self.enqueued_at, 1),
            "running_s": round(now - self.started_at, 1) if self.started_at and self.state == "running" else None,
            "switching_to": self.switch_to,
            "meta": {k: v for k, v in self.meta.items() if k in ("chat_id", "model", "user_id")},
        }


class Ticket:
    """What acquire() gives back: the right to use the card until released."""

    def __init__(self, sched: "Scheduler", job: Job):
        self._sched = sched
        self.job = job
        self._released = False

    @property
    def need(self) -> str:
        return self.job.need

    async def switch(self, need: str) -> None:
        """Change residency inside a lease (e.g. to ComfyUI and back)."""
        await self._sched._switch_lease(self.job, need)

    async def release(self, error: str | None = None) -> None:
        if not self._released:
            self._released = True
            self._sched._release(self.job, error)

    async def __aenter__(self) -> "Ticket":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.release(None if exc is None else f"{type(exc).__name__}: {exc}"[:300])


class Scheduler:
    def __init__(self, backend: Backend, *, affinity_window: float = 20, aging_s: float = 120,
                 clock: Callable[[], float] = time.monotonic, on_finish: Callable[[Job], None] | None = None):
        self.backend = backend
        self.affinity_window = affinity_window
        self.aging_s = aging_s
        self.clock = clock
        self.on_finish = on_finish
        self.current: str | None = None
        self.switching: str | None = None
        self.running: dict[int, Job] = {}
        self.pending: list[Job] = []
        self.last_error: str | None = None
        self._ids = itertools.count(1)
        self._wake: asyncio.Event | None = None
        self._pump_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listeners: set[asyncio.Queue] = set()
        self._timer: asyncio.TimerHandle | None = None
        self._suspect = False

    # ------------------------------------------------------------ public
    async def acquire(self, owner: str, kind: str, need: str, priority: str | int = "normal",
                      label: str = "", lease: bool = False, meta: dict | None = None,
                      max_wait: float | None = None, on_job: Callable[[Job], None] | None = None) -> Ticket:
        """Wait for a turn on the card. Cancelling the awaiting task gives the
        place up; a job that already started is released."""
        self._ensure_pump()
        prio = PRIORITY[priority] if isinstance(priority, str) else int(priority)
        loop = asyncio.get_running_loop()
        job = Job(id=next(self._ids), owner=owner, kind=kind, need=need, priority=prio, label=label,
                  lease=lease, enqueued_at=self.clock(), meta=dict(meta or {}), admitted=loop.create_future())
        self.pending.append(job)
        if on_job is not None:
            on_job(job)
        self._notify()
        try:
            if max_wait is None:
                await asyncio.shield(job.admitted)
            else:
                await asyncio.wait_for(asyncio.shield(job.admitted), timeout=max_wait)
        except asyncio.TimeoutError:
            self._abandon(job, "timed out waiting")
            raise QueueTimeout(f"waited more than {int(max_wait or 0)} s for the GPU")
        except asyncio.CancelledError:
            self._abandon(job, "cancelled")
            raise
        return Ticket(self, job)

    def snapshot(self) -> dict:
        now = self.clock()
        order = self._ordered(now)
        return {
            "current": self.current, "switching": self.switching, "last_error": self.last_error,
            "running": [j.info(now) for j in self.running.values()],
            "pending": [j.info(now, i + 1) for i, j in enumerate(order)],
        }

    def job(self, job_id: int) -> Job | None:
        """A waiting or running job."""
        for j in self.pending:
            if j.id == job_id:
                return j
        return self.running.get(job_id)

    def cancel(self, job_id: int) -> bool:
        """Cancel from the UI: a waiting job leaves the queue (its waiter gets
        an error); a running job is stopped through its meta["cancel"]."""
        for j in list(self.pending):
            if j.id == job_id:
                self.pending.remove(j)
                j.state, j.error, j.ended_at = "cancelled", "cancelled by the user", self.clock()
                if j.admitted and not j.admitted.done():
                    j.admitted.set_exception(SchedulerError("cancelled by the user"))
                self._finished(j)
                self._notify()
                return True
        j = self.running.get(job_id)
        if j is not None and callable(j.meta.get("cancel")):
            j.meta["cancel"]()
            return True
        return False

    def position(self, job: Job) -> int:
        """Jobs ahead of this one (0 = running)."""
        if job.state == "running":
            return 0
        order = self._ordered(self.clock())
        try:
            return order.index(job) + len(self.running)
        except ValueError:
            return -1

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    async def drain(self) -> None:
        """Test helper: until nothing waits or runs."""
        while self.pending or self.running or self.switching:
            await asyncio.sleep(0.005)

    def suspect(self) -> None:
        """Something could not reach what is loaded (a crashed llama-server):
        check it before the next job is sent there."""
        self._suspect = True
        self._notify()

    async def preload(self, need: str) -> None:
        """Load something without a job (start-up): only when the card is idle."""
        t = await self.acquire("system", "preload", need, "background", label=f"preload {need}")
        await t.release()

    # ------------------------------------------------------------ internals
    def _eff(self, job: Job, now: float) -> int:
        if self.aging_s <= 0:
            return job.priority
        return max(0, job.priority - int((now - job.enqueued_at) // self.aging_s))

    def _ordered(self, now: float) -> list[Job]:
        return sorted(self.pending, key=lambda j: (self._eff(j, now), j.enqueued_at, j.id))

    def _ensure_pump(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:          # a new event loop (tests): start fresh
            self._loop = loop
            self._wake = asyncio.Event()
            self._pump_task = None
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = loop.create_task(self._pump(), name="gpu-scheduler")

    def _notify(self) -> None:
        if self._wake is not None:
            self._wake.set()
        self._broadcast()

    def _broadcast(self) -> None:
        if not self._listeners:
            return
        snap = self.snapshot()
        for q in list(self._listeners):
            try:
                if q.full():
                    q.get_nowait()
                q.put_nowait(snap)
            except (asyncio.QueueFull, asyncio.QueueEmpty):
                pass

    def _wake_later(self, delay: float) -> None:
        if self._loop is None:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = self._loop.call_later(max(0.05, delay), self._notify)

    async def _pump(self) -> None:
        assert self._wake is not None
        while True:
            self._wake.clear()
            try:
                await self._admit()
            except Exception:                 # never let the queue die
                log.exception("scheduler admission failed")
            await self._wake.wait()

    def _start(self, job: Job) -> None:
        self.pending.remove(job)
        job.state = "running"
        job.started_at = self.clock()
        self.running[job.id] = job
        if job.admitted and not job.admitted.done():
            job.admitted.set_result(True)
        self._broadcast()

    def _fail(self, job: Job, err: Exception) -> None:
        if job in self.pending:
            self.pending.remove(job)
        job.state = "failed"
        job.error = str(err)
        job.ended_at = self.clock()
        if job.admitted and not job.admitted.done():
            job.admitted.set_exception(err)
        self._finished(job)

    def _finished(self, job: Job) -> None:
        if self.on_finish:
            try:
                self.on_finish(job)
            except Exception:
                log.exception("on_finish failed")
        self._broadcast()

    def _release(self, job: Job, error: str | None) -> None:
        if self.running.pop(job.id, None) is None:
            return
        job.state = "failed" if error else "done"
        job.error = error
        job.ended_at = self.clock()
        if job.switch_fut and not job.switch_fut.done():
            job.switch_fut.cancel()
        job.switch_to = None
        self._finished(job)
        self._notify()

    def _abandon(self, job: Job, why: str) -> None:
        """The waiter gave up (cancelled or timed out)."""
        if job in self.pending:
            self.pending.remove(job)
            job.state = "cancelled"
            job.error = why
            job.ended_at = self.clock()
            self._finished(job)
            self._notify()
        elif job.state == "running":                  # admitted just as it gave up
            self._release(job, why)

    def _users(self, need: str | None, but: Job | None = None) -> int:
        """Running jobs working on `need` (not waiting to switch away)."""
        return sum(1 for j in self.running.values()
                   if j is not but and j.switch_to is None and j.need == need)

    async def _switch_lease(self, job: Job, need: str) -> None:
        if job.state != "running":
            raise SchedulerError("only a running job can switch")
        if not job.lease:
            raise SchedulerError("only a lease can switch residency")
        if job.need == need and need == self.current and self.switching is None:
            return
        if (need == self.current and self.switching is None and not self._suspect
                and not any(j.switch_to for j in self.running.values())
                and self._users(need, but=job) < self.backend.slots(need)):
            job.need = need
            return
        loop = asyncio.get_running_loop()
        job.switch_to = need
        job.switch_fut = loop.create_future()
        job.switch_at = self.clock()
        self._notify()
        await job.switch_fut

    async def _switch(self, target: str) -> Exception | None:
        """Unload what is there and load `target`. None on success."""
        self.switching = target
        self._broadcast()
        try:
            if self.current is not None:
                old, self.current = self.current, None
                await self.backend.unload(old)
            await self.backend.load(target)
            self.current = target
            self.last_error = None
            return None
        except Exception as exc:              # noqa: BLE001 - reported to the job
            self.current = None
            self.last_error = f"{target}: {exc}"
            log.warning("could not load %s: %s", target, exc)
            try:
                await self.backend.unload(target)
            except Exception:
                pass
            return SchedulerError(f"could not load {target}: {exc}")
        finally:
            self.switching = None
            self._broadcast()

    async def _check_alive(self) -> None:
        self._suspect = False
        alive = getattr(self.backend, "alive", None)
        if self.current is None or alive is None:
            return
        try:
            ok = await alive(self.current)
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            log.warning("%s is no longer loaded (crashed?) - it is loaded again when needed", self.current)
            self.last_error = f"{self.current} stopped unexpectedly; it is loaded again"
            self.current = None
            self._broadcast()

    async def _admit(self) -> None:
        while True:
            now = self.clock()
            if self._suspect and self.switching is None:
                await self._check_alive()

            # 1. Leases asking to switch go first.
            waiters = sorted((j for j in self.running.values() if j.switch_to is not None),
                             key=lambda j: (j.switch_at, j.id))
            if waiters:
                progressed = False
                for w in waiters:                   # targets that are loaded: continue while slots allow
                    if (w.switch_to == self.current and self.current is not None
                            and self._users(self.current) < self.backend.slots(self.current)):
                        fut = w.switch_fut
                        w.need, w.switch_to, w.switch_fut = w.switch_to, None, None
                        if fut and not fut.done():
                            fut.set_result(True)
                        progressed = True
                if progressed:
                    self._broadcast()
                    continue
                if any(j.switch_to is None for j in self.running.values()):
                    return                          # jobs still working on the loaded model finish first
                first = waiters[0]
                target = first.switch_to
                if target == self.current:
                    return                          # no slot of it is free; a release wakes us
                err = await self._switch(target)
                if err is not None:                 # the load failed: tell everyone who wanted it
                    for w in waiters:
                        if w.switch_to == target and w.switch_fut and not w.switch_fut.done():
                            fut = w.switch_fut
                            w.switch_to, w.switch_fut = None, None
                            fut.set_exception(err)
                continue

            waiting = self._ordered(now)
            if not waiting:
                return
            head = waiting[0]

            # 2. Idle card: serve the head, switching if needed.
            if not self.running:
                if head.need != self.current:
                    err = await self._switch(head.need)
                    if err is not None and head in self.pending:
                        self._fail(head, err)
                    continue                         # re-evaluate: things may have changed
                self._start(head)
                continue

            free = self.backend.slots(self.current) - len(self.running) if self.current else 0
            if head.need == self.current:
                if free > 0:
                    self._start(head)
                    continue
                return

            # 3. The head needs something else.
            lease_running = any(j.lease for j in self.running.values())
            in_window = (now - head.enqueued_at) < self.affinity_window
            if free > 0 and (lease_running or in_window):
                head_p = self._eff(head, now)
                cand = next((j for j in waiting[1:]
                             if j.need == self.current and self._eff(j, now) <= head_p), None)
                if cand is not None:
                    self._start(cand)
                    continue
            if in_window and not lease_running:
                self._wake_later(head.enqueued_at + self.affinity_window - now)
            return
