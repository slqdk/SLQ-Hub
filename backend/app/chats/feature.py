from __future__ import annotations

from .. import media
from ..state import Hub
from . import routes


def install(hub: Hub) -> None:
    hub.extras["routers"] += [routes.router, media.router]
