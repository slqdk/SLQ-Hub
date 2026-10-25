"""The Ollama API, so FamilyLog (and anything else that speaks Ollama) only
needs a new address. Every generation goes through the GPU queue.

Implemented: /api/version, /api/tags, /api/show, /api/ps, /api/chat,
/api/generate, /api/embed, /api/embeddings. Model management (pull, create,
delete, copy, push) belongs to the Hub and is refused.
"""
from __future__ import annotations

import hashlib
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..gpu.runner import RunnerError
from ..gpu.scheduler import SchedulerError
from ..llm import payload as P
from ..llm.client import LlmError
from ..llm.messages import from_ollama, image_part, ollama_format
from ..llm.stream import Accumulator, parse_arguments
from ..usage import note_tokens
from .common import Clock, acquire_llm, caller, hub_of, iso_now, ndjson, resolve_model

router = APIRouter()
OLLAMA_VERSION = "0.13.5"
CAPS = {"chat": "completion", "vision": "vision", "tools": "tools", "thinking": "thinking"}


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _size(hub, entry: dict) -> int:
    total = 0
    for f in entry.get("files", []):
        try:
            total += hub.catalog.path(f).stat().st_size
        except OSError:
            total += int(f.get("size") or 0)
    return total


def _details(entry: dict) -> dict:
    fam = entry.get("family") or "llama"
    return {"parent_model": "", "format": "gguf", "family": fam, "families": [fam],
            "parameter_size": entry.get("parameter_size", ""), "quantization_level": entry.get("quantization", "")}


def _modified(hub, entry: dict) -> str:
    try:
        ts = hub.catalog.path(entry["files"][0]).stat().st_mtime
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))
    except (OSError, IndexError, KeyError):
        return iso_now()


def _tag(hub, entry: dict) -> dict:
    return {"name": entry["id"], "model": entry["id"], "modified_at": _modified(hub, entry),
            "size": _size(hub, entry), "digest": hashlib.sha256(entry["id"].encode()).hexdigest(),
            "details": _details(entry)}


def _options(opts: dict) -> dict:
    out = {}
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("top_k", "top_k"), ("min_p", "min_p"),
                     ("seed", "seed"), ("stop", "stop"), ("repeat_penalty", "repeat_penalty"),
                     ("presence_penalty", "presence_penalty"), ("frequency_penalty", "frequency_penalty"),
                     ("num_predict", "max_tokens")):
        if opts.get(src) is not None:
            out[dst] = opts[src]
    if out.get("max_tokens") is not None and int(out["max_tokens"]) < 0:
        out.pop("max_tokens")
    return out


def _think(value) -> tuple[bool | None, str | None]:
    if isinstance(value, bool):
        return value, None
    if isinstance(value, str) and value:
        return (False, None) if value.lower() in ("none", "off", "false") else (True, value)
    return None, None


@router.get("/api/version")
async def version():
    return {"version": OLLAMA_VERSION}


@router.get("/api/tags")
async def tags(request: Request):
    hub = hub_of(request)
    models = [_tag(hub, e) for e in hub.catalog.llm_models() if hub.catalog.installed(e)]
    for a in hub.catalog.entries("aux"):
        if a.get("kind") == "embedding" and hub.catalog.installed(a):
            models.append({**_tag(hub, a), "details": {**_details(a), "family": "qwen3"}})
    return {"models": models}


@router.post("/api/show")
async def show(request: Request):
    hub = hub_of(request)
    body = await request.json()
    name = body.get("model") or body.get("name")
    entry = resolve_model(hub, name)
    if entry is None:
        aux = hub.catalog.get("aux", name or "")
        if aux and hub.catalog.installed(aux):
            return {"modelfile": "", "parameters": "", "template": "", "details": _details(aux),
                    "model_info": {"general.architecture": "qwen3", "qwen3.context_length": 8192},
                    "capabilities": ["embedding"], "modified_at": _modified(hub, aux)}
        return _err(404, f"model '{name}' not found")
    fam = entry.get("family") or "llama"
    ctx = hub.catalog.context_per_request(entry["id"])
    return {"license": "", "modelfile": f"# served by SLQ Hub ({entry['id']})", "parameters": "", "template": "",
            "details": _details(entry),
            "model_info": {"general.architecture": fam, "general.basename": entry["id"],
                           f"{fam}.context_length": ctx},
            "capabilities": [CAPS[c] for c in entry.get("capabilities", []) if c in CAPS],
            "modified_at": _modified(hub, entry)}


@router.get("/api/ps")
async def ps(request: Request):
    hub = hub_of(request)
    mid = hub.loaded_model()
    if not mid:
        return {"models": []}
    entry = hub.catalog.get_llm(mid) or {"id": mid, "files": []}
    gpu = await hub.runner.gpu()
    size = _size(hub, entry)
    return {"models": [{**_tag(hub, entry), "size": size, "size_vram": gpu.get("vram_used") or size,
                        "expires_at": "2099-01-01T00:00:00Z",
                        "context_length": hub.catalog.context_per_request(mid)}]}


def _final(name: str, res: dict, clock: Clock, key: str) -> dict:
    t = res.get("timings") or {}
    u = res.get("usage") or {}
    end = time.monotonic()
    done_reason = "length" if res.get("finish_reason") == "length" else "stop"
    out = {"model": name, "created_at": iso_now(), key: {"role": "assistant", "content": ""} if key == "message" else "",
           "done": True, "done_reason": done_reason,
           "total_duration": clock.ns(clock.t0, end), "load_duration": clock.ns(clock.t0, clock.admitted),
           "prompt_eval_count": u.get("prompt_tokens", t.get("prompt_n", 0)),
           "prompt_eval_duration": int(float(t.get("prompt_ms", 0)) * 1e6),
           "eval_count": u.get("completion_tokens", t.get("predicted_n", 0)),
           "eval_duration": int(float(t.get("predicted_ms", 0)) * 1e6)}
    if t.get("cache_n") is not None:
        out["prompt_eval_cached_count"] = t["cache_n"]
    return out


def _tool_calls(res: dict) -> list[dict]:
    out = []
    for c in res.get("tool_calls") or []:
        try:
            args = parse_arguments(c.get("arguments"))
        except ValueError:
            args = {"_raw": c.get("arguments")}
        out.append({"function": {"name": c.get("name"), "arguments": args}})
    return out


async def _generate(request: Request, body: dict, messages: list[dict], kind: str):
    """kind 'chat' -> /api/chat shape (message), 'generate' -> /api/generate (response)."""
    hub = hub_of(request)
    name = body.get("model") or ""
    entry = resolve_model(hub, name)
    if entry is None:
        return _err(404, f"model '{name}' not found")
    thinking, effort = _think(body.get("think"))
    pl = P.build(entry, messages, tools=body.get("tools") if kind == "chat" else None, thinking=thinking,
                 effort=effort, sampling=_options(body.get("options") or {}),
                 response_format=ollama_format(body.get("format")))
    who = caller(request)
    stream = body.get("stream", True)
    key = "message" if kind == "chat" else "response"

    def piece(text: str = "", thinking_text: str | None = None) -> dict:
        if key == "message":
            msg = {"role": "assistant", "content": text}
            if thinking_text:
                msg["thinking"] = thinking_text
            return {"model": name, "created_at": iso_now(), "message": msg, "done": False}
        out = {"model": name, "created_at": iso_now(), "response": text, "done": False}
        if thinking_text:
            out["thinking"] = thinking_text
        return out

    async def run(emit):
        clock = Clock()
        try:
            ticket = await acquire_llm(hub, who, entry["id"], f"{who.owner}: /api/{kind}")
        except SchedulerError as exc:
            raise LlmError(str(exc), 503)
        clock.admit()
        acc = Accumulator()
        try:
            async for chunk in hub.llm.stream_chat(hub.main_url, pl):
                for ev, data in acc.feed(chunk):
                    if ev == "text":
                        await emit(piece(data["text"]))
                    elif ev == "thinking":
                        await emit(piece("", data["text"]))
            res = acc.result()
        finally:
            note_tokens(ticket, acc.usage, acc.timings)       # Settings -> Usage (0.24.0)
            await ticket.release()
        final = _final(name, res, clock, key)
        if kind == "chat" and res.get("tool_calls"):
            await emit({"model": name, "created_at": iso_now(),
                        "message": {"role": "assistant", "content": "", "tool_calls": _tool_calls(res)},
                        "done": False})
        if kind == "generate":
            final["context"] = []
        return res, final

    if stream:
        async def gen():
            import asyncio
            q: asyncio.Queue = asyncio.Queue()

            async def emit(obj):
                await q.put(obj)

            async def worker():
                try:
                    _res, final = await run(emit)
                    await q.put(final)
                except LlmError as exc:
                    await q.put({"error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    await q.put({"error": f"{type(exc).__name__}: {exc}"})
                finally:
                    await q.put(None)

            task = asyncio.create_task(worker())
            try:
                while True:
                    item = await q.get()
                    if item is None:
                        break
                    yield ndjson(item)
            finally:
                if not task.done():
                    task.cancel()
        return StreamingResponse(gen(), media_type="application/x-ndjson")

    parts: list[str] = []
    thoughts: list[str] = []

    async def collect(obj):
        if key == "message":
            parts.append(obj["message"].get("content", ""))
            if obj["message"].get("thinking"):
                thoughts.append(obj["message"]["thinking"])
        else:
            parts.append(obj.get("response", ""))
            if obj.get("thinking"):
                thoughts.append(obj["thinking"])

    try:
        res, final = await run(collect)
    except LlmError as exc:
        return _err(exc.status if exc.status >= 400 else 500, str(exc))
    if key == "message":
        msg = {"role": "assistant", "content": res["content"]}
        if res.get("reasoning"):
            msg["thinking"] = res["reasoning"]
        if res.get("tool_calls"):
            msg["tool_calls"] = _tool_calls(res)
        final["message"] = msg
    else:
        final["response"] = res["content"]
        if res.get("reasoning"):
            final["thinking"] = res["reasoning"]
    return final


@router.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    try:
        messages = from_ollama(body.get("messages") or [])
    except ValueError as exc:
        return _err(400, str(exc))
    return await _generate(request, body, messages, "chat")


@router.post("/api/generate")
async def generate(request: Request):
    body = await request.json()
    prompt = body.get("prompt") or ""
    if not prompt and not body.get("images"):
        # Ollama's "load / unload this model" request: the Hub decides that.
        return {"model": body.get("model", ""), "created_at": iso_now(), "response": "", "done": True,
                "done_reason": "load"}
    messages: list[dict] = []
    if body.get("system"):
        messages.append({"role": "system", "content": body["system"]})
    try:
        import base64
        parts = [image_part(base64.b64decode(i.split(",", 1)[-1])) for i in body.get("images") or []]
    except ValueError as exc:
        return _err(400, str(exc))
    if parts:
        messages.append({"role": "user", "content": [*parts, {"type": "text", "text": prompt}]})
    else:
        messages.append({"role": "user", "content": prompt})
    return await _generate(request, body, messages, "generate")


async def _embed(request: Request, texts: list[str], name: str):
    hub = hub_of(request)
    aux_id = "embed-vl" if (name or "").startswith("embed-vl") else "embed-text"
    t0 = time.monotonic()
    try:
        base = await hub.aux.ensure(aux_id)
        vecs = await hub.llm.embeddings(base, texts)
    except (RunnerError, LlmError) as exc:
        raise LlmError(str(exc), 503)
    return vecs, int((time.monotonic() - t0) * 1e9)


@router.post("/api/embed")
async def embed(request: Request):
    body = await request.json()
    inp = body.get("input")
    texts = [inp] if isinstance(inp, str) else list(inp or [])
    try:
        vecs, dur = await _embed(request, texts, body.get("model", ""))
    except LlmError as exc:
        return _err(exc.status, str(exc))
    return {"model": body.get("model", ""), "embeddings": vecs, "total_duration": dur, "load_duration": 0,
            "prompt_eval_count": sum(max(1, len(t) // 4) for t in texts)}


@router.post("/api/embeddings")
async def embeddings_legacy(request: Request):
    body = await request.json()
    try:
        vecs, _ = await _embed(request, [body.get("prompt") or ""], body.get("model", ""))
    except LlmError as exc:
        return _err(exc.status, str(exc))
    return {"embedding": vecs[0] if vecs else []}


@router.api_route("/api/{action}", methods=["GET", "POST", "DELETE", "HEAD"])
async def refused(action: str):
    if action in ("pull", "push", "create", "copy", "delete", "blobs"):
        return _err(403, "models are managed in SLQ Hub (Settings -> Models)")
    return _err(404, f"/api/{action} is not part of SLQ Hub's Ollama API")
