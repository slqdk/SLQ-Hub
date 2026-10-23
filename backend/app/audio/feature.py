from __future__ import annotations

from ..state import Hub
from .pipeline import AudioPipeline


def install(hub: Hub) -> None:
    hub.extras["audio"] = AudioPipeline(hub)
