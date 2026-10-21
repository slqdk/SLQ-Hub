"""Queue, live events, settings, models, API keys and system status."""
from __future__ import annotations

import asyncio
import json
import shutil

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import auth
from ..config import DEFAULTS
from ..gpu.scheduler import SchedulerError
from ..state import get_hub

router = APIRouter()
SECRET_SETTINGS = {("cloud", "api_key")}


# ---------------------------------------------------------------- queue + events

@router.get("/hub/api/queue")
async def queue(request: Request):
    return get_hub(request).scheduler.snapshot()


@router.post("/hub/api/queue/{job_id}/cancel")
async def cancel_job(job_id: int, request: Request):
    if not get_hub(request).scheduler.cancel(job_id):
        raise HTTPException(404, "That job is not in the queue any more.")
    return {"ok": True}


@router.get("/hub/api/events")
async def events(request: Request):
    """Live queue snapshots for the header indicator and the queue drawer."""
    hub = get_hub(request)

    async def gen():
        q = hub.scheduler.subscribe()
        try:
            yield f"event: queue\ndata: {json.dumps(hub.scheduler.snapshot())}\n\n".encode()
            while True:
                try:
                    snap = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: queue\ndata: {json.dumps(snap)}\n\n".encode()
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
        finally:
            hub.scheduler.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- settings

MASK = "********"


def _masked(data: dict) -> dict:
    out = json.loads(json.dumps(data))
    for section, key in SECRET_SETTINGS:
        if out.get(section, {}).get(key):
            out[section][key] = MASK
    for srv in (out.get("mcp") or {}).get("servers") or []:      # MCP headers and env hold API keys
        for field in ("headers", "env"):
            if isinstance(srv.get(field), dict):
                srv[field] = {k: (MASK if v else v) for k, v in srv[field].items()}
    return out


def _unmask_mcp(values: dict, current: dict) -> dict:
    old = {s.get("name"): s for s in current.get("servers") or [] if isinstance(s, dict)}
    for srv in values.get("servers") or []:
        if not isinstance(srv, dict):
            continue
        prev = old.get(srv.get("name")) or {}
        for field in ("headers", "env"):
            if isinstance(srv.get(field), dict):
                srv[field] = {k: ((prev.get(field) or {}).get(k, "") if v == MASK else v)
                              for k, v in srv[field].items()}
    return values


@router.get("/hub/api/settings")
async def get_settings(request: Request):
    hub = get_hub(request)
    return {"settings": _masked(hub.settings.all()), "defaults": _masked(DEFAULTS)}


class SettingsPatch(BaseModel):
    section: str
    values: dict


@router.put("/hub/api/settings")
async def put_settings(body: SettingsPatch, request: Request):
    hub = get_hub(request)
    values = dict(body.values)
    for section, key in SECRET_SETTINGS:
        if body.section == section and values.get(key) == MASK:
            values.pop(key)                      # unchanged secret
    if body.section == "mcp":
        values = _unmask_mcp(values, hub.settings.get("mcp"))
    try:
        hub.settings.update(body.section, values)
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    if body.section == "queue":
        q = hub.settings.get("queue")
        hub.scheduler.affinity_window = float(q.get("affinity_window_s", 20))
        hub.scheduler.aging_s = float(q.get("aging_s", 120))
    if body.section == "mcp" and "mcp" in hub.extras:
        asyncio.get_running_loop().create_task(hub.extras["mcp"].reload())
    return {"settings": _masked(hub.settings.all())}


# ---------------------------------------------------------------- models

@router.get("/hub/api/models")
async def models(request: Request):
    hub = get_hub(request)
    downloads = hub.extras["downloads"].snapshot()
    by_model: dict[str, list] = {}
    for f in downloads["files"]:
        by_model.setdefault(f["model"], []).append(f)
    out = {}
    for group in ("llm", "aux", "comfy"):
        out[group] = []
        for e in hub.catalog.entries(group):
            out[group].append({**e, "installed": hub.catalog.installed(e),
                               "downloads": by_model.get(e["id"], []),
                               "loaded": group == "llm" and hub.loaded_model() == e["id"]})
    out["current"] = hub.scheduler.current
    out["defaults"] = hub.settings.get("models")
    return out


@router.post("/hub/api/models/{model_id}/download")
async def download(model_id: str, request: Request):
    try:
        added = get_hub(request).extras["downloads"].enqueue(model_id)
    except KeyError:
        raise HTTPException(404, "No such model in the catalog.")
    return {"queued": added}


@router.post("/hub/api/models/{model_id}/cancel-download")
async def cancel_download(model_id: str, request: Request):
    get_hub(request).extras["downloads"].cancel(model_id)
    return {"ok": True}


class ModelPatch(BaseModel):
    server: dict | None = None
    aliases: list[str] | None = None


@router.put("/hub/api/models/{model_id}")
async def update_model(model_id: str, body: ModelPatch, request: Request):
    hub = get_hub(request)
    try:
        entry = hub.catalog.save_override(model_id, body.server, body.aliases)
    except KeyError:
        raise HTTPException(404, "No such model.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"model": entry, "reloadNeeded": hub.loaded_model() == model_id}


@router.post("/hub/api/models/{model_id}/load")
async def load_model(model_id: str, request: Request):
    hub = get_hub(request)
    if not hub.catalog.get_llm(model_id):
        raise HTTPException(404, "No such model.")
    try:
        t = await hub.scheduler.acquire("web", "load", hub.model_need(model_id), "interactive",
                                        label=f"load {model_id}", max_wait=1800)
        await t.release()
    except SchedulerError as exc:
        raise HTTPException(500, str(exc))
    return {"current": hub.scheduler.current}


@router.post("/hub/api/models/unload")
async def unload_models(request: Request):
    hub = get_hub(request)
    try:
        t = await hub.scheduler.acquire("web", "unload", "idle", "interactive", label="free the GPU",
                                        max_wait=1800)
        await t.release()
    except SchedulerError as exc:
        raise HTTPException(500, str(exc))
    return {"current": hub.scheduler.current}


# ---------------------------------------------------------------- API keys

class KeyCreate(BaseModel):
    name: str
    priority: str = "normal"
    allowed_ips: str = ""


class KeyPatch(BaseModel):
    priority: str | None = None
    allowed_ips: str | None = None
    enabled: bool | None = None


@router.get("/hub/api/keys")
async def list_keys():
    return auth.list_api_keys()


@router.post("/hub/api/keys")
async def create_key(body: KeyCreate):
    try:
        return auth.create_api_key(body.name, body.priority, body.allowed_ips)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.patch("/hub/api/keys/{key_id}")
async def patch_key(key_id: int, body: KeyPatch):
    fields = {k: (int(v) if isinstance(v, bool) else v) for k, v in body.model_dump().items() if v is not None}
    try:
        if not auth.update_api_key(key_id, **fields):
            raise HTTPException(404, "No such key.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@router.delete("/hub/api/keys/{key_id}")
async def delete_key(key_id: int):
    if not auth.delete_api_key(key_id):
        raise HTTPException(404, "No such key.")
    return {"ok": True}


# ---------------------------------------------------------------- system

async def _probe(url: str, transport=None, timeout: float = 4) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as c:
            r = await c.get(url)
        return {"ok": r.status_code < 400, "status": r.status_code}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": exc.__class__.__name__}


@router.get("/hub/api/system")
async def system(request: Request):
    hub = get_hub(request)
    env = hub.env
    try:
        runner = await hub.runner.status()
    except Exception as exc:  # noqa: BLE001
        runner = {"error": str(exc)}
    try:
        comfy = await hub.comfy.system_stats()
    except Exception as exc:  # noqa: BLE001
        comfy = {"error": str(exc)}

    def disk(path):
        try:
            u = shutil.disk_usage(path)
            return {"total": u.total, "used": u.used, "free": u.free}
        except OSError:
            return {}

    return {
        "version": env.version,
        "services": {
            "llm": runner,
            "comfyui": comfy,
            "searxng": await _probe(env.searxng_url + "/healthz", hub.transports.web),
            "sandbox": await _probe(env.sandbox_url + "/health", hub.transports.sandbox),
        },
        "gpu": runner.get("gpu") if isinstance(runner, dict) else {},
        "disk": {"data": disk(env.data_dir), "models": disk(env.models_dir)},
        "queue": hub.scheduler.snapshot(),
        "lastLoadError": hub.scheduler.last_error,
    }


@router.get("/hub/api/connection")
async def connection(request: Request):
    """How other apps reach the Hub (for the Apps page)."""
    env = get_hub(request).env
    return {"lanHost": env.lan_host, "uiPort": env.ui_port, "ollamaPort": env.ollama_port,
            "publicUrl": env.public_url}


@router.get("/hub/api/system/logs/{name}")
async def logs(name: str, request: Request, tail: int = 300):
    return {"name": name, "text": await get_hub(request).runner.logs(name, tail)}
