from __future__ import annotations

from ..state import Hub
from . import blobs, routes, sync_routes


def install(hub: Hub) -> None:
    blobs.configure(hub.env.data_dir / "blobs")
    (hub.env.workspace_dir / "projects").mkdir(parents=True, exist_ok=True)
    (hub.env.workspace_dir / "chats").mkdir(parents=True, exist_ok=True)
    hub.extras["routers"] += [routes.router, sync_routes.router]
