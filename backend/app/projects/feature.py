from __future__ import annotations

from ..state import Hub
from . import blobs, repo, repo_routes, routes, store


def install(hub: Hub) -> None:
    blobs.configure(hub.env.data_dir / "blobs")
    (hub.env.workspace_dir / "projects").mkdir(parents=True, exist_ok=True)
    (hub.env.workspace_dir / "chats").mkdir(parents=True, exist_ok=True)
    repos = hub.extras["repos"] = repo.Repos(hub)               # a project's repository (0.84.0)
    hub.extras["routers"] += [routes.router, repo_routes.router]

    async def bind_repos() -> None:
        repos.bind()
        store.CHECKIN_HOOKS[:] = [h for h in store.CHECKIN_HOOKS               # (one Hub a process; tests make more)
                                  if not isinstance(getattr(h, "__self__", None), repo.Repos)] + [repos.changed]

    hub.extras["startup_hooks"].append(bind_repos)
