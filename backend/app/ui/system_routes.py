"""Queue, live events, settings, models, API keys and system status."""
from __future__ import annotations

import asyncio
import json
import os
import shutil

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import auth, hf
from ..catalog import GROUPS
from ..config import DEFAULTS
from ..gpu.scheduler import SchedulerError
from ..images import sources
from ..images import workflows as W
from ..state import get_hub

router = APIRouter()
SECRET_SETTINGS = {("cloud", "api_key")}


# ---------------------------------------------------------------- queue + events

def queue_for(snap: dict, user: dict | None) -> dict:
    """What a viewer may see of the queue. People see their own jobs, and that the card is busy - not with
    what or for whom. (People's jobs carry no chat title, so the owner sees only their name.)"""
    if not auth.is_member(user):
        return snap

    def one(j: dict) -> dict:
        meta = j.get("meta") or {}
        if meta.get("user_id") == user["id"]:
            return j
        return {**j, "owner": "", "label": "Another job", "meta": {k: v for k, v in meta.items() if k == "model"}}
    return {**snap, "last_error": None, "running": [one(j) for j in snap.get("running") or []],
            "pending": [one(j) for j in snap.get("pending") or []]}


@router.get("/hub/api/queue")
async def queue(request: Request):
    return queue_for(get_hub(request).scheduler.snapshot(), getattr(request.state, "user", None))


@router.post("/hub/api/queue/{job_id}/cancel")
async def cancel_job(job_id: int, request: Request):
    hub, user = get_hub(request), getattr(request.state, "user", None)
    job = hub.scheduler.job(job_id)
    if job is not None and auth.is_member(user) and job.meta.get("user_id") != user["id"]:
        raise HTTPException(403, "That is not your job.")
    if not hub.scheduler.cancel(job_id):
        raise HTTPException(404, "That job is not in the queue any more.")
    return {"ok": True}


@router.get("/hub/api/events")
async def events(request: Request):
    """Live queue snapshots for the header indicator and the queue drawer."""
    hub = get_hub(request)
    user = getattr(request.state, "user", None)

    async def gen():
        q = hub.scheduler.subscribe()
        try:
            yield f"event: queue\ndata: {json.dumps(queue_for(hub.scheduler.snapshot(), user))}\n\n".encode()
            while True:
                try:
                    snap = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: queue\ndata: {json.dumps(queue_for(snap, user))}\n\n".encode()
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


def _for_people(hub) -> dict:
    """The few settings a person's chat needs (defaults for the composer)."""
    s = hub.settings
    return {"models": {"default": s.get("models", "default")},
            "agent": {"thinking_in_chats": s.get("agent", "thinking_in_chats"), "effort": s.get("agent", "effort")},
            "images": {"model": s.get("images", "model")}}


@router.get("/hub/api/settings")
async def get_settings(request: Request):
    hub = get_hub(request)
    if auth.is_member(getattr(request.state, "user", None)):
        mine = _for_people(hub)
        return {"settings": mine, "defaults": mine}
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
    for group in GROUPS:
        out[group] = []
        for e in hub.catalog.entries(group):
            out[group].append({**e, "installed": hub.catalog.installed(e),
                               "downloads": by_model.get(e["id"], []),
                               "loaded": group == "llm" and hub.loaded_model() == e["id"]})
    out["current"] = hub.scheduler.current
    out["defaults"] = hub.settings.get("models")
    out["sdxl"] = {"presets": sources.PRESETS, "samplers": list(W.SDXL_SAMPLERS),
                   "schedulers": list(W.SDXL_SCHEDULERS)}
    return out


@router.get("/hub/api/models/hf")
async def hf_lookup(repo: str, request: Request):
    """The GGUF files of a Hugging Face repository, grouped into choices."""
    hub = get_hub(request)
    try:
        res = await hf.lookup(hf.parse_repo(repo), transport=hub.transports.web)
    except hf.HFError as exc:
        raise HTTPException(exc.status, str(exc))
    res["suggested"]["id"] = _unique_id(hub, res["suggested"]["id"])
    hub.env.models_dir.mkdir(parents=True, exist_ok=True)
    res["freeDisk"] = shutil.disk_usage(hub.env.models_dir).free
    gpu = await hub.runner.gpu()
    res["vram"] = gpu.get("vram_total") or 0
    return res


@router.get("/hub/api/models/hf/search")
async def hf_search(q: str, request: Request):
    """GGUF models on Hugging Face matching some words, for the add dialog."""
    try:
        return {"results": await hf.search(q, transport=get_hub(request).transports.web)}
    except hf.HFError as exc:
        raise HTTPException(exc.status, str(exc))


class HFAdd(BaseModel):
    repo: str
    file: str                      # the chosen quantisation: its first file
    mmproj: str | None = None      # a vision projector from the same repository
    id: str
    name: str = ""
    ctx: int = 32768
    tools: bool = True
    thinking: bool = False


@router.post("/hub/api/models/hf")
async def hf_add(body: HFAdd, request: Request):
    """Add a model from Hugging Face to the catalog and start downloading it."""
    hub = get_hub(request)
    try:
        repo = hf.parse_repo(body.repo)
        res = await hf.lookup(repo, transport=hub.transports.web)      # sizes from the source, not the browser
        choice = next((c for c in res["choices"] if c["key"] == body.file), None)
        if choice is None:
            raise hf.HFError(f"{body.file} is not a model file in {repo}.")
        mm = None
        if body.mmproj:
            mm = next((m for m in res["mmproj"] if m["path"] == body.mmproj), None)
            if mm is None:
                raise hf.HFError(f"{body.mmproj} is not a vision projector in {repo}.")
        limit = int(res["meta"].get("context_length") or 262144)
        ctx = max(2048, min(int(body.ctx), limit))
        entry = hf.make_entry(repo, choice, mm, body.id.strip(), body.name, ctx, body.tools, body.thinking,
                              res["meta"])
    except hf.HFError as exc:
        raise HTTPException(exc.status, str(exc))
    _disk_check(hub, entry)
    try:
        added = hub.catalog.add_custom(entry)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    queued = hub.extras["downloads"].enqueue(entry["id"])
    return {"model": added, "queued": queued}


def _unique_id(hub, want: str) -> str:
    taken = {e["id"] for g in GROUPS for e in hub.catalog.entries(g)}
    mid, n = want, 2
    while mid in taken:
        mid = f"{want}-{n}"
        n += 1
    return mid


def _disk_check(hub, entry: dict) -> None:
    hub.env.models_dir.mkdir(parents=True, exist_ok=True)
    need = sum(int(f.get("size") or 0) for f in entry["files"] if not hub.catalog.path(f).is_file())
    free = shutil.disk_usage(hub.env.models_dir).free
    if need > free - 5 * 1024**3:                          # keep 5 GB for chats, images and updates
        raise HTTPException(400, f"Not enough disk space: it needs {need / 1e9:.1f} GB, {free / 1e9:.1f} GB is free "
                                 "and the Hub keeps 5 GB spare.")


@router.get("/hub/api/models/images/lookup")
async def image_lookup(link: str, request: Request, pick: str | None = None):
    """What an SDXL link points at (Hugging Face files or Civitai versions),
    which of them the Hub can run, and settings to start from."""
    hub = get_hub(request)
    try:
        res = await sources.lookup(link, pick, transport=hub.transports.web)
    except sources.SourceError as exc:
        raise HTTPException(exc.status, str(exc))
    res["suggested"]["id"] = _unique_id(hub, res["suggested"]["id"])
    hub.env.models_dir.mkdir(parents=True, exist_ok=True)
    res["freeDisk"] = shutil.disk_usage(hub.env.models_dir).free
    res["presets"] = sources.PRESETS
    res["samplers"] = list(W.SDXL_SAMPLERS)
    res["schedulers"] = list(W.SDXL_SCHEDULERS)
    res["tokens"] = {"hf": bool(os.environ.get("HF_TOKEN")), "civitai": bool(os.environ.get("CIVITAI_TOKEN"))}
    return res


class ImageAdd(BaseModel):
    link: str
    pick: str                      # the Hugging Face file or the Civitai version
    id: str
    name: str = ""
    settings: dict = {}


@router.post("/hub/api/models/images")
async def image_add(body: ImageAdd, request: Request):
    """Add an SDXL checkpoint from a link to the catalog and start downloading it."""
    hub = get_hub(request)
    try:
        res = await sources.lookup(body.link, body.pick, transport=hub.transports.web)   # checked at the source
        entry = sources.make_entry(res, body.id.strip(), body.name, sources.clean_settings(body.settings))
    except sources.SourceError as exc:
        raise HTTPException(exc.status, str(exc))
    _disk_check(hub, entry)
    try:
        added = hub.catalog.add_custom(entry, "comfy")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    queued = hub.extras["downloads"].enqueue(entry["id"])
    return {"model": added, "queued": queued}


class ImagePatch(BaseModel):
    name: str | None = None
    settings: dict | None = None


@router.put("/hub/api/models/images/{model_id}")
async def image_update(model_id: str, body: ImagePatch, request: Request):
    """An added picture model's name and settings (steps, CFG, sampler, tags...)."""
    hub = get_hub(request)
    entry = hub.catalog.get("comfy", model_id)
    if not entry or not entry.get("custom"):
        raise HTTPException(404, "Only picture models added from a link have these settings.")
    changes: dict = {}
    if body.name is not None:
        changes["name"] = body.name.strip()[:80] or entry["name"]
    if body.settings is not None:
        try:
            changes["sdxl"] = sources.clean_settings(body.settings)
        except sources.SourceError as exc:
            raise HTTPException(exc.status, str(exc))
    return {"model": hub.catalog.update_custom(model_id, changes)}


@router.delete("/hub/api/models/{model_id}")
async def remove_model(model_id: str, request: Request, files: bool = True):
    """Remove a model that was added from Hugging Face or a link (and its files)."""
    hub = get_hub(request)
    entry, group = hub.catalog.get_llm(model_id), "llm"
    if entry is None:
        entry, group = hub.catalog.get("comfy", model_id), "comfy"
    if not entry or not entry.get("custom"):
        raise HTTPException(404, "Only models you added yourself can be removed.")
    if group == "llm":
        if hub.loaded_model() == model_id:
            raise HTTPException(409, "It is on the card now. Free the GPU first.")
        if hub.settings.get("models", "default") == model_id:
            raise HTTPException(409, "It is the model for chats. Choose another one first.")
    elif hub.settings.get("images", "model") == model_id:
        raise HTTPException(409, "It is the model for pictures (Settings -> Assistant -> Images). Choose another "
                                 "one first.")
    hub.extras["downloads"].cancel(model_id)
    hub.catalog.remove_custom(model_id)
    if hub.settings.get("models", "fallback_alias") == model_id:
        hub.settings.update("models", {"fallback_alias": ""})
    if files:
        root = (hub.env.models_dir / ("llm/hf" if group == "llm" else "comfy/checkpoints/hub")).resolve()
        for f in entry.get("files", []):
            p = hub.catalog.path(f)
            for q in (p, p.with_name(p.name + ".part")):
                try:
                    if q.resolve().is_relative_to(root) and q.is_file():
                        q.unlink()
                except OSError:
                    pass
            d = p.parent                                   # empty folders up to llm/hf (or checkpoints/hub)
            while d.resolve() != root and d.resolve().is_relative_to(root):
                try:
                    d.rmdir()
                except OSError:
                    break
                d = d.parent
    return {"ok": True}


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
    sampling: dict | None = None         # {"instruct": {...}, "thinking": {...}} - replaces the card's values
    reset_sampling: bool = False         # back to the card's (or the family's) values
    thinking_mode: str | None = None     # follow | on | off


@router.put("/hub/api/models/{model_id}")
async def update_model(model_id: str, body: ModelPatch, request: Request):
    hub = get_hub(request)
    try:
        entry = hub.catalog.save_override(model_id, body.server, body.aliases, body.sampling, body.reset_sampling,
                                          body.thinking_mode)
    except KeyError:
        raise HTTPException(404, "No such model.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # Sampling and thinking go with each request; only server settings need the model loaded again.
    return {"model": entry, "reloadNeeded": body.server is not None and hub.loaded_model() == model_id}


class ModelName(BaseModel):
    name: str = ""                       # "" = the model's own name again


@router.put("/hub/api/models/{model_id}/name")
async def rename_model(model_id: str, body: ModelName, request: Request):
    """The name the Hub shows for a chat or picture model; its id - what apps and settings use - stays."""
    try:
        return {"model": get_hub(request).catalog.rename(model_id, body.name)}
    except KeyError:
        raise HTTPException(404, "Only chat and picture models can be renamed.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


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
    away: bool = False                      # also through the proxy (hub.slq.dk), from outside the home network


class KeyPatch(BaseModel):
    priority: str | None = None
    allowed_ips: str | None = None
    enabled: bool | None = None
    away: bool | None = None


@router.get("/hub/api/keys")
async def list_keys():
    return auth.list_api_keys()


@router.post("/hub/api/keys")
async def create_key(body: KeyCreate):
    try:
        return auth.create_api_key(body.name, body.priority, body.allowed_ips, away=body.away)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.patch("/hub/api/keys/{key_id}")
async def patch_key(key_id: int, body: KeyPatch):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
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

async def _probe(url: str, transport=None, timeout: float = 4, details: bool = False) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as c:
            r = await c.get(url)
        out = {"ok": r.status_code < 400, "status": r.status_code}
        if details and r.status_code < 400:
            try:
                body = r.json()
                if isinstance(body, dict):
                    out["health"] = body
            except ValueError:
                pass
        return out
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
            "speech": await _probe(env.speech_url + "/health", hub.transports.speech, details=True),
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
    """How other apps reach the Hub (for the Apps page). homeNetwork is the
    LAN range the Hub itself sits in, for keys that should work only at home
    (the other LAN ranges may be WireGuard or a VPN)."""
    env = get_hub(request).env
    return {"lanHost": env.lan_host, "uiPort": env.ui_port, "ollamaPort": env.ollama_port,
            "publicUrl": env.public_url, "homeNetwork": _home_network(env)}


def _home_network(env) -> str:
    import ipaddress
    try:
        addr = ipaddress.ip_address((env.lan_host or "").strip())
    except ValueError:
        return ""
    nets = [n for n in env.lan_networks if n.version == addr.version and addr in n and not n.is_loopback]
    return str(min(nets, key=lambda n: n.num_addresses)) if nets else ""


@router.get("/hub/api/system/logs/{name}")
async def logs(name: str, request: Request, tail: int = 300):
    return {"name": name, "text": await get_hub(request).runner.logs(name, tail)}
