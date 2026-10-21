from __future__ import annotations

from ..state import Hub
from .pipeline import ImagePipeline


def install(hub: Hub) -> None:
    hub.extras["images"] = ImagePipeline(hub)
