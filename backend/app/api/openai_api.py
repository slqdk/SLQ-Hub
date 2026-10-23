"""The OpenAI API: /v1/models, /v1/chat/completions, /v1/embeddings and
(with the image pipeline) /v1/images/generations and /v1/images/edits.

llama-server already speaks this format, so chat is passed through - with
the model name resolved, images inlined, defaults from the model card, and
a turn on the GPU first."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..gpu.runner import RunnerError
from ..gpu.scheduler import SchedulerError
from ..llm import payload as P
from ..llm.client import LlmError
from ..llm.messages import normalise_openai
from .common import acquire_llm, caller, hub_of, resolve_model, sse

router = APIRouter()


def _err(status: int, message: str, kind: str = "invalid_request_error", code: str | None = None) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind, "code": code}}, status_code=status)


@router.get("/v1/models")
async def models(request: Request):
    """The standard list, plus a few fields other clients ignore and the
    phone chat page (/chat) uses: name, kind (chat, embedding, image),
    vision and thinking (with the effort levels the model's template takes:
    reasoning_effort), default (what the Hub picks itself) and loaded."""
    hub = hub_of(request)
    now = int(time.time())
    chat_default = hub.settings.get("models", "default")
    image_default = hub.settings.get("images", "model")
    loaded = hub.loaded_model()

    def item(e: dict, kind: str, **more) -> dict:
        return {"id": e["id"], "object": "model", "created": now, "owned_by": "slq-hub",
                "name": e.get("name") or e["id"], "kind": kind, **more}

    def efforts(e: dict) -> list[str]:
        if "thinking" not in (e.get("capabilities") or []):
            return []
        return list((e.get("thinking") or {}).get("efforts") or [])

    data = [item(e, "chat", vision="vision" in (e.get("capabilities") or []),
                 thinking="thinking" in (e.get("capabilities") or []), efforts=efforts(e),
                 default=e["id"] == chat_default, loaded=e["id"] == loaded)
            for e in hub.catalog.llm_models() if hub.catalog.installed(e)]
    data += [item(a, "embedding")
             for a in hub.catalog.entries("aux") if a.get("kind") == "embedding" and hub.catalog.installed(a)]
    data += [item(c, "image", abilities=list(c.get("abilities") or []), default=c["id"] == image_default)
             for c in hub.catalog.entries("comfy") if hub.catalog.installed(c)]
    return {"object": "list", "data": data}


def _thinking(body: dict, entry: dict) -> tuple[bool | None, str | None, dict]:
    extra: dict = {}
    if isinstance(body.get("chat_template_kwargs"), dict):
        extra["chat_template_kwargs"] = body["chat_template_kwargs"]
        return None, None, extra
    eff = body.get("reasoning_effort")
    if isinstance(body.get("reasoning"), dict):          # Responses-style {"effort": ...}
        eff = body["reasoning"].get("effort", eff)
    if eff is not None:
        if str(eff).lower() in ("none", "minimal", "off"):
            return False, None, extra
        return True, str(eff), extra
    if isinstance(body.get("thinking"), bool):
        return body["thinking"], None, extra
    return None, None, extra


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    hub = hub_of(request)
    try:
        body = await request.json()
    except ValueError:
        return _err(400, "the body must be JSON")
    name = body.get("model") or ""
    entry = resolve_model(hub, name)
    if entry is None:
        return _err(404, f"the model '{name}' does not exist on this Hub", code="model_not_found")
    try:
        messages = await normalise_openai(body.get("messages") or [])
    except Exception as exc:  # noqa: BLE001 - bad image, unreachable URL
        return _err(400, f"could not read the messages: {exc}")
    thinking, effort, extra = _thinking(body, entry)
    sampling = {k: body[k] for k in P.SAMPLING_KEYS if k in body}
    if body.get("max_completion_tokens") is not None:
        sampling["max_tokens"] = body["max_completion_tokens"]
    stream = bool(body.get("stream"))
    pl = P.build(entry, messages, tools=body.get("tools"), tool_choice=body.get("tool_choice"),
                 thinking=thinking, effort=effort, sampling=sampling,
                 response_format=body.get("response_format"), stream=stream, extra=extra)
    if body.get("stream_options"):
        pl["stream_options"] = body["stream_options"]
    who = caller(request)
    label = f"{who.owner}: /v1/chat/completions"

    if not stream:
        try:
            ticket = await acquire_llm(hub, who, entry["id"], label)
        except SchedulerError as exc:
            return _err(503, str(exc), "server_error", "queue")
        try:
            out = await hub.llm.chat(hub.main_url, pl)
        except LlmError as exc:
            return _err(exc.status if exc.status >= 400 else 500, str(exc), "server_error")
        finally:
            await ticket.release()
        out["model"] = name
        return out

    async def gen():
        try:
            ticket = await acquire_llm(hub, who, entry["id"], label)
        except SchedulerError as exc:
            yield sse({"error": {"message": str(exc), "type": "server_error", "code": "queue"}})
            return
        try:
            async for chunk in hub.llm.stream_chat(hub.main_url, pl):
                chunk["model"] = name
                yield sse(chunk)
            yield b"data: [DONE]\n\n"
        except LlmError as exc:
            yield sse({"error": {"message": str(exc), "type": "server_error"}})
        finally:
            await ticket.release()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/v1/embeddings")
async def embeddings(request: Request):
    hub = hub_of(request)
    body = await request.json()
    inp = body.get("input")
    texts = [inp] if isinstance(inp, str) else list(inp or [])
    if texts and not isinstance(texts[0], str):
        return _err(400, "token-id input is not supported; send text")
    aux_id = "embed-vl" if str(body.get("model", "")).startswith("embed-vl") else "embed-text"
    try:
        base = await hub.aux.ensure(aux_id)
        vecs = await hub.llm.embeddings(base, texts)
    except (RunnerError, LlmError) as exc:
        return _err(503, str(exc), "server_error")
    tokens = sum(max(1, len(t) // 4) for t in texts)
    return {"object": "list", "model": body.get("model") or aux_id,
            "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)],
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens}}


@router.post("/v1/images/generations")
async def images_generations(request: Request):
    hub = hub_of(request)
    pipeline = hub.extras.get("images")
    if pipeline is None:
        return _err(501, "image generation is not available on this Hub")
    return await pipeline.api_generate(request)


@router.post("/v1/images/edits")
async def images_edits(request: Request):
    hub = hub_of(request)
    pipeline = hub.extras.get("images")
    if pipeline is None:
        return _err(501, "image editing is not available on this Hub")
    return await pipeline.api_edit(request)

