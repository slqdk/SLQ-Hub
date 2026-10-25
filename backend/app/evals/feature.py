from __future__ import annotations

from ..state import Hub
from . import routes
from .runner import TestRuns


def install(hub: Hub) -> None:
    hub.extras["tests"] = TestRuns(hub)
    hub.extras["routers"].append(routes.router)

    async def interrupted() -> None:                  # a run the Hub's last stop cut short
        hub.extras["tests"].interrupted()

    hub.extras["startup_hooks"].append(interrupted)
