from __future__ import annotations

from ..state import Hub
from .pipeline import ImagePipeline


def install(hub: Hub) -> None:
    hub.extras["images"] = ImagePipeline(hub)
    from . import lora_routes
    from .loras import Loras
    hub.extras["loras"] = Loras(hub.env.data_dir, hub.env.models_dir)        # 0.49.0: the admin's LoRAs
    hub.extras["routers"].append(lora_routes.router)
