from __future__ import annotations

from ..state import Hub


def install(hub: Hub) -> None:
    from . import routes
    from .runs import Training
    hub.extras["training"] = Training(hub)           # 0.50.0: LoRA training, the admin's alone
    hub.extras["routers"].append(routes.router)
    hub.extras["startup_hooks"].append(hub.extras["training"].resume_after_restart)
    hub.extras["startup_hooks"].append(hub.extras["training"].categorize_after_start)   # 0.70.0
