from __future__ import annotations

from ..state import Hub
from .pipeline import VideoPipeline


def install(hub: Hub) -> None:
    hub.extras["video"] = VideoPipeline(hub)
