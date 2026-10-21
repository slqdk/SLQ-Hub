"""The CPU-only helper servers (embeddings, reranker): started when first
needed, stopped after a while unused. They never touch the GPU queue."""
from __future__ import annotations

import asyncio
import time

from ..catalog import Catalog
from .runner import AUX_PORTS, Runner, RunnerError

IDLE_STOP_S = 15 * 60


class AuxManager:
    def __init__(self, runner: Runner, catalog: Catalog, container_models_dir: str = "/models"):
        self.runner = runner
        self.catalog = catalog
        self.models_dir = container_models_dir
        self._last_used: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._ready: set[str] = set()
        self._reaper: asyncio.Task | None = None

    def available(self, aux_id: str) -> bool:
        entry = self.catalog.get("aux", aux_id)
        return bool(entry) and self.catalog.installed(entry)

    async def ensure(self, aux_id: str) -> str:
        """Base URL of a running, ready server for `aux_id`."""
        entry = self.catalog.get("aux", aux_id)
        if not entry:
            raise RunnerError(f"unknown helper model {aux_id!r}")
        if not self.catalog.installed(entry):
            raise RunnerError(f"the files for {aux_id} are not downloaded (Settings -> Models)")
        port = AUX_PORTS[aux_id]
        lock = self._locks.setdefault(aux_id, asyncio.Lock())
        async with lock:
            self._last_used[aux_id] = time.monotonic()
            base = self.runner.base_url(port)
            if aux_id in self._ready:
                st = await self.runner.status()
                if ((st.get("processes") or {}).get(aux_id) or {}).get("state") == "running":
                    return base
                self._ready.discard(aux_id)
            await self.runner.start(aux_id, port, self.catalog.aux_args(aux_id, self.models_dir))
            await self.runner.wait_ready(aux_id, port, timeout=300)
            self._ready.add(aux_id)
            self._ensure_reaper()
            return base

    def touch(self, aux_id: str) -> None:
        self._last_used[aux_id] = time.monotonic()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap())

    async def _reap(self) -> None:
        while self._ready:
            await asyncio.sleep(60)
            now = time.monotonic()
            for aux_id in list(self._ready):
                if now - self._last_used.get(aux_id, now) > IDLE_STOP_S:
                    try:
                        await self.runner.stop(aux_id)
                    except RunnerError:
                        pass
                    self._ready.discard(aux_id)
