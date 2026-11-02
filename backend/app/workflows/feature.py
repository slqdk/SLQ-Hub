from __future__ import annotations

from ..state import Hub
from .store import Workflows


def install(hub: Hub) -> None:
    hub.extras["workflows"] = Workflows(hub.env.data_dir)          # 0.67.0: the owner's ComfyUI workflows
    from . import routes
    hub.extras["routers"].append(routes.router)
