"""The card's energy (0.24.0): its own power reading while it works, split between the jobs that hold it - for
what each answer, chat, project and person used, and what that cost.

The reading is amdgpu's `power1_average` (the supervisor's /gpu; `power1_input` on cards without it): the
graphics card's own figure, not the whole PC from the wall. Settings -> Usage adds a fixed number of watts for
the rest of the PC while the card works (0 unless set).

Time is cut into intervals at every change of what runs or loads (the scheduler's on_change) and at every
reading (every SAMPLE_S seconds while something runs). An interval's energy - the latest reading times its
length - goes in equal shares to the jobs running in it; while the card loads for a job that waits (an idle card
switching to what the head of the queue needs), to that job. What the card uses while nobody uses it goes to
on_idle (Settings -> Usage shows it as "The card, idle"). A finished job's energy is added to its run
(meta["run_id"]): an agent turn may be several jobs (it gives the card up while it waits for an answer), and the
turn takes the sum when it ends (take_run).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from .scheduler import Job, Scheduler

log = logging.getLogger("hub.energy")

SAMPLE_S = 2.0          # a reading this often while the card works
IDLE_S = 60.0           # and this often while it is idle
MAX_GAP_S = 30.0        # a working interval longer than this (a reading that hung) counts as this long


class EnergyMeter:
    def __init__(self, sched: Scheduler, read: Callable[[], Awaitable[dict]],
                 clock: Callable[[], float] = time.monotonic):
        self.sched = sched
        self.read = read                       # -> the supervisor's /gpu ({"power_w": ..., "power_cap_w": ...})
        self.clock = clock
        self.power: float | None = None        # the latest reading, W (None: no reading - nothing is counted)
        self.cap: float | None = None
        self.source: str | None = None
        self.read_at: float | None = None
        self.other_w = 0.0                     # the rest of the PC while the card works (Settings -> Usage)
        self.last_t = clock()
        self.benef: list[Job] = []             # the jobs the current interval is for
        self.by_run: dict[str, list[float]] = {}   # run id -> [joules, seconds] of its finished jobs
        self.idle_j = 0.0                      # read while nobody used the card, not yet handed to on_idle
        self.on_idle: Callable[[float], None] | None = None
        self._wake: asyncio.Event | None = None

    # ------------------------------------------------------------ the intervals
    def _beneficiaries(self) -> list[Job]:
        running = list(self.sched.running.values())
        if running:
            return running
        if self.sched.switching:               # the card is made ready for a waiting job
            for j in self.sched.pending:
                if j.need == self.sched.switching:
                    return [j]
        return []

    def close(self, now: float | None = None) -> None:
        """The interval up to now, to the jobs it was for."""
        now = self.clock() if now is None else now
        dt = max(0.0, now - self.last_t)
        self.last_t = now
        if dt <= 0 or self.power is None:
            return
        if not self.benef:
            self.idle_j += self.power * dt
            return
        share = (self.power + self.other_w) * min(dt, MAX_GAP_S) / len(self.benef)
        for j in self.benef:
            j.energy_j += share

    def changed(self) -> None:
        """The scheduler's on_change: what runs or loads changed."""
        self.close()
        was_idle = not self.benef
        self.benef = self._beneficiaries()
        if was_idle and self.benef and self._wake is not None:
            self._wake.set()                   # a fresh reading now: the last one may be from long ago

    def finished(self, job: Job) -> None:
        """Before the scheduler's on_finish logs the job: its last interval, then its run's sum."""
        self.close()
        run_id = job.meta.get("run_id")
        if run_id:
            acc = self.by_run.setdefault(run_id, [0.0, 0.0])
            acc[0] += job.energy_j
            if job.started_at is not None and job.ended_at is not None:
                acc[1] += max(0.0, job.ended_at - job.started_at)

    def take_run(self, run_id: str) -> tuple[float, float]:
        """(Wh, seconds on the card) of a run's jobs, once - when the turn ends."""
        joules, secs = self.by_run.pop(run_id, (0.0, 0.0))
        return joules / 3600, secs

    # ------------------------------------------------------------ the readings
    async def sample(self) -> None:
        try:
            g = await self.read()
        except Exception:  # noqa: BLE001 - the supervisor may be restarting
            g = {}
        now = self.clock()
        p = g.get("power_w") if isinstance(g, dict) else None
        if isinstance(p, (int, float)) and p >= 0:
            self.power, self.read_at = float(p), now
            self.cap = g.get("power_cap_w") if isinstance(g.get("power_cap_w"), (int, float)) else None
            self.source = g.get("power_source")
        else:
            self.power = None                  # no reading: nothing is counted until there is one
        self.close(now)                        # the interval just ended, at the fresh reading
        if self.idle_j > 0 and self.on_idle is not None:
            joules, self.idle_j = self.idle_j, 0.0
            try:
                self.on_idle(joules)
            except Exception:  # noqa: BLE001
                log.exception("could not keep the idle energy")

    async def run(self) -> None:
        """Read the card every SAMPLE_S seconds while it works, every IDLE_S while it is idle."""
        self._wake = asyncio.Event()
        while True:
            try:
                try:
                    await asyncio.wait_for(self._wake.wait(), SAMPLE_S if self.benef else IDLE_S)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                await self.sample()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never stop reading
                log.exception("energy reading failed")
                await asyncio.sleep(SAMPLE_S)
