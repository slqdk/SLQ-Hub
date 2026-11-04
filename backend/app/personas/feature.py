from __future__ import annotations

from ..state import Hub


def install(hub: Hub) -> None:
    from . import routes
    from .store import Personas
    hub.extras["personas"] = Personas(hub)             # 0.74.0: someone to chat with, the admin's alone
    hub.extras["routers"].append(routes.router)
    hub.extras["routers"].append(routes.media_router)  # 0.76.0: signed links Instagram fetches a post's files with
    hub.extras["startup_hooks"].append(hub.extras["personas"].sweep_after_start)
    hub.extras["startup_hooks"].append(hub.extras["personas"].start_schedule)      # 0.75.0: the tasks' times
    hub.extras["startup_hooks"].append(hub.extras["personas"].ig_after_start)      # 0.76.0: posts a restart cut off
