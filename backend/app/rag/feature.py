from __future__ import annotations

from ..state import Hub
from . import routes, tools  # noqa: F401 - tools registers library_search
from .library import Libraries


def install(hub: Hub) -> None:
    libs = Libraries(hub)
    hub.extras["libraries"] = libs
    hub.extras["routers"].append(routes.router)

    async def start() -> None:
        libs.start()

    hub.extras["startup_hooks"].append(start)
