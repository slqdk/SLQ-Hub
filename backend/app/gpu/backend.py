"""What 'loading' means for each kind of residency on the real card."""
from __future__ import annotations

import asyncio
import time

from ..catalog import Catalog
from .comfy import Comfy, ComfyError
from .runner import AUX_PORTS, MAIN, Runner


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
            await self.runner.start(name, port, self.catalog.server_args(ident, self.models_dir))
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
            await self._wait_vram_free()
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

    async def _wait_vram_free(self, below_gb: float = 2.5, timeout: float = 30) -> None:
        """ComfyUI's /free is applied by its worker a moment later; wait until
        the card is really empty before an LLM tries to load."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            g = await self.runner.gpu()
            used = g.get("vram_used")
            if used is None or used < below_gb * 1024**3:
                return
            await asyncio.sleep(0.5)
