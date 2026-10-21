"""Optional feature modules, installed on the hub at start-up. Each adds its
routers to hub.extras["routers"] and any start-up hooks to
hub.extras["startup_hooks"]. Kept apart so a broken feature is easy to see
in the log and the model API keeps working."""
from __future__ import annotations

import importlib
import logging

from .state import Hub

log = logging.getLogger("hub.features")

MODULES = [
    "app.projects.feature",
    "app.chats.feature",
    "app.agent.feature",
    "app.images.feature",
    "app.rag.feature",
]


def install(hub: Hub) -> None:
    hub.extras.setdefault("routers", [])
    hub.extras.setdefault("startup_hooks", [])
    hub.extras.setdefault("features", {})
    for name in MODULES:
        try:
            mod = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name and name.startswith(exc.name):
                continue                     # not built yet
            log.exception("feature %s could not be imported", name)
            hub.extras["features"][name] = f"failed: {exc}"
            continue
        try:
            mod.install(hub)
            hub.extras["features"][name] = "ok"
        except Exception as exc:  # noqa: BLE001
            log.exception("feature %s failed to install", name)
            hub.extras["features"][name] = f"failed: {exc}"
