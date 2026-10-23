"""The SLQ Hub app: one FastAPI application serving the web UI, its API,
the model API for other apps and the sync API."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .config import Env, load_env
from .downloads import Downloads
from .gate import Gate
from .state import Hub, Transports, build_hub

log = logging.getLogger("hub")


async def _startup(hub: Hub) -> None:
    """Tidy the card, then load the default model so the first chat is quick."""
    try:
        await hub.backend.reset()
    except Exception as exc:  # noqa: BLE001 - services may still be starting
        log.warning("could not reset the GPU services: %s", exc)
    models = hub.settings.get("models")
    default = models.get("default")
    entry = hub.catalog.get_llm(default) if default else None
    if models.get("preload") and entry and hub.catalog.installed(entry):
        for attempt in range(30):              # the llm container may start after us
            try:
                await hub.runner.status()
                break
            except Exception:  # noqa: BLE001
                await asyncio.sleep(2)
        try:
            await hub.scheduler.preload(hub.model_need(default))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not preload %s: %s", default, exc)


def create_app(env: Env | None = None, transports: Transports | None = None, startup: bool = True) -> FastAPI:
    env = env or load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    hub = build_hub(env, transports)
    auth.ensure_admin(env.admin_user, env.admin_password_hash)
    hub.extras["downloads"] = Downloads(hub.catalog, transport=hub.transports.web)
    _install_features(hub)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(_startup(hub)) if startup else None
        for hook in hub.extras.get("startup_hooks", []):
            try:
                await hook()
            except Exception:  # noqa: BLE001
                log.exception("startup hook failed")
        yield
        if task and not task.done():
            task.cancel()

    app = FastAPI(title="SLQ Hub", version=env.version, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.hub = hub

    from .ui import auth_routes, people_routes, system_routes
    app.include_router(auth_routes.router)
    app.include_router(people_routes.router)
    app.include_router(system_routes.router)
    for router in hub.extras.get("routers", []):
        app.include_router(router)

    from .api import anthropic_api, ollama_api, openai_api
    app.include_router(anthropic_api.router)
    app.include_router(openai_api.router)
    app.include_router(ollama_api.router)

    _static(app, env.static_dir)
    app.add_middleware(Gate, hub_getter=lambda: app.state.hub)
    return app


def _install_features(hub: Hub) -> None:
    """Chats, projects, sync, the agent, images, libraries - each module
    registers its routers and hooks on the hub."""
    from . import features
    features.install(hub)


def _static(app: FastAPI, static_dir: Path | None) -> None:
    # HEAD too: some Ollama apps (Enchanted) probe the server with HEAD / and
    # call it unreachable unless that answers 200, as Ollama's own server does.
    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def root(request: Request):
        accept = request.headers.get("accept", "")
        if "text/html" not in accept:
            return PlainTextResponse("Ollama is running (SLQ Hub)")      # what Ollama clients probe
        if static_dir:
            return FileResponse(static_dir / "index.html")
        return PlainTextResponse("SLQ Hub: the web UI is not built (frontend/dist missing).")

    if not static_dir:
        return
    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith(("hub/api/", "api/", "v1/")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        f = (static_dir / path).resolve()
        if path and f.is_file() and static_dir.resolve() in f.parents:
            return FileResponse(f)
        # The phone chat page (/chat) is a small app of its own on the model API.
        if (path == "chat" or path.startswith("chat/")) and (static_dir / "chat.html").is_file():
            return FileResponse(static_dir / "chat.html")
        return FileResponse(static_dir / "index.html")
