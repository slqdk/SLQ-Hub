from __future__ import annotations

from ..state import Hub


def install(hub: Hub) -> None:
    from . import routes
    from .studio import Studio
    hub.extras["movies"] = Studio(hub)                 # 0.72.0: short films shot by shot, the admin's alone
    hub.extras["routers"].append(routes.router)
    hub.extras["startup_hooks"].append(hub.extras["movies"].sweep_after_start)
