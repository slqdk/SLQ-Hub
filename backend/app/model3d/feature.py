from __future__ import annotations

from ..state import Hub
from .pipeline import Model3DPipeline


def install(hub: Hub) -> None:
    hub.extras["model3d"] = Model3DPipeline(hub)            # 0.67.0: a 3D model from a picture
