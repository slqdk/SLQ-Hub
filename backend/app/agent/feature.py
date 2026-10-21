from __future__ import annotations

import asyncio
import logging

from ..state import Hub
from . import routes
from .loop import Agent
from .mcp import McpManager
from .memory import Memory
from .skills import Skills

log = logging.getLogger("hub.agent")


def install(hub: Hub) -> None:
    hub.extras["skills"] = Skills(hub.env.data_dir / "skills")
    hub.extras["memory"] = Memory(hub.env.data_dir / "memory")
    hub.extras["mcp"] = McpManager(hub)
    hub.extras["agent"] = Agent(hub)
    hub.extras["routers"].append(routes.router)

    async def start_mcp() -> None:
        async def run():
            try:
                await hub.extras["mcp"].ensure_started(force=True, wait=180)
            except Exception:  # noqa: BLE001
                log.exception("MCP start-up failed")
        if hub.extras["mcp"].configs():
            asyncio.get_running_loop().create_task(run())

    hub.extras["startup_hooks"].append(start_mcp)
