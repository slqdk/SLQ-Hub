"""What 'loading' means for each kind of residency on the real card."""
from __future__ import annotations

import asyncio
import logging
import time

from ..catalog import Catalog
from .comfy import Comfy, ComfyError
from .runner import AUX_PORTS, MAIN, Runner, RunnerError

log = logging.getLogger("hub.gpu")

FREE_BELOW_GB = 2.5          # the card counts as empty below this (the VM's own use is a fraction of a GB)
FREE_WAIT_S = 30             # for ComfyUI's /free to take effect
CLEAR_WAIT_S = 120           # for ComfyUI to stop a job and free the card


class GpuBackend:
    def __init__(self, runner: Runner, comfy: Comfy, catalog: Catalog, container_models_dir: str = "/models"):
        self.runner = runner
        self.comfy = comfy
        self.catalog = catalog
        self.models_dir = container_models_dir

    # --------------------------------------------------------------- Backend protocol
    async def load(self, need: str) -> None:
        kind, _, ident = need.partition(":")
        if kind == "idle":                       # "unload everything"
            return
        if kind == "llm":
            entry = self.catalog.get_llm(ident)
            if not entry:
                raise ValueError(f"unknown model {ident!r}")
            if not self.catalog.installed(entry):
                raise ValueError(f"the files for {ident} are not downloaded yet (Settings -> Models)")
            name, port = MAIN
            args = self.catalog.server_args(ident, self.models_dir)
            await self._clear_card()
            try:
                await self.runner.start(name, port, args)
                await self.runner.wait_ready(name, port)
            except RunnerError as exc:
                if "out of memory" not in str(exc).lower():
                    raise
                log.warning("%s did not fit on the card (%s); clearing ComfyUI and trying once more", ident, exc)
                await self._clear_card(force=True)
                await self.runner.start(name, port, args)
                await self.runner.wait_ready(name, port)
        elif kind == "comfy":
            try:
                await self.comfy.system_stats()
            except ComfyError as exc:
                raise RuntimeError(f"ComfyUI is not available: {exc}") from exc
        elif kind == "embedgpu":
            args = self._gpu_args(self.catalog.aux_args(ident, self.models_dir))
            port = AUX_PORTS["embed-gpu"]
            await self.runner.start("embed-gpu", port, args)
            await self.runner.wait_ready("embed-gpu", port, timeout=300)
        else:
            raise ValueError(f"unknown residency {need!r}")

    async def unload(self, need: str) -> None:
        kind = need.partition(":")[0]
        if kind == "llm":
            await self.runner.stop(MAIN[0])
        elif kind == "comfy":
            await self.comfy.free()
            if not await self._wait_vram_free():
                await self._clear_card(force=True)
        elif kind == "embedgpu":
            await self.runner.stop("embed-gpu")

    async def alive(self, need: str) -> bool:
        """Is what the scheduler thinks is loaded still there?"""
        kind = need.partition(":")[0]
        if kind == "comfy":
            try:
                await self.comfy.system_stats()
                return True
            except ComfyError:
                return False
        name = {"llm": MAIN[0], "embedgpu": "embed-gpu"}.get(kind)
        if name is None:
            return True
        st = await self.runner.status()
        return ((st.get("processes") or {}).get(name) or {}).get("state") == "running"

    def slots(self, need: str) -> int:
        kind, _, ident = need.partition(":")
        if kind == "llm":
            return self.catalog.slots(ident)
        return 1

    # --------------------------------------------------------------- helpers
    async def reset(self) -> None:
        """At start-up: nothing of ours on the card."""
        for name in (MAIN[0], "embed-gpu"):
            try:
                await self.runner.stop(name)
            except Exception:
                pass
        await self.comfy.free()

    @staticmethod
    def _gpu_args(args: list[str]) -> list[str]:
        out, skip = [], False
        for a in args:
            if skip:
                skip = False
                continue
            if a == "--device":
                skip = True
                continue
            out.append(a)
        return out + ["-ngl", "all"]

    async def _wait_vram_free(self, below_gb: float = FREE_BELOW_GB, timeout: float | None = None) -> bool:
        """ComfyUI's /free is applied by its worker a moment later; wait until
        the card is really empty before an LLM tries to load. False: it was not."""
        deadline = time.monotonic() + (FREE_WAIT_S if timeout is None else timeout)
        while True:
            used = await self._vram_used()
            if used is None or used < below_gb * 1024**3:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.5)

    async def _vram_used(self) -> int | None:
        g = await self.runner.gpu()
        used = g.get("vram_used")
        return int(used) if isinstance(used, (int, float)) else None

    async def _clear_card(self, force: bool = False) -> None:
        """Before a model loads, nothing else may hold the card. The scheduler has unloaded what it knows of, but
        ComfyUI can still hold memory: a job the Hub stopped waiting for. In the test run of 2026-10-25 ComfyUI
        stopped answering during a video, kept its memory, and every model load after it failed (out of memory) -
        seven test cases in a row. Here ComfyUI is told to stop what it does, drop its queue and free the card,
        and the card is watched until it is empty (or two minutes pass). `force`: even if it looks empty."""
        used = await self._vram_used()
        if used is None or (used < FREE_BELOW_GB * 1024**3 and not force):
            return
        log.warning("%.1f GB in use on the card before a model loads: stopping ComfyUI's work and freeing it",
                    used / 1024**3)
        await self.comfy.interrupt()
        await self.comfy.clear_queue()
        await self.comfy.free()
        if not await self._wait_vram_free(timeout=CLEAR_WAIT_S):
            left = await self._vram_used()
            log.warning("the card still has %.1f GB in use after ComfyUI was asked to free it",
                        (left or 0) / 1024**3)
