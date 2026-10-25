"""The Anthropic Messages API (/v1/messages), so Claude-format clients -
the Agent SDK, Claude Code, TwinCAT Coagent - can use the local models
through the GPU queue. Streaming, tools, images and thinking."""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import netfetch
from ..gpu.scheduler import SchedulerError
from ..llm import payload as P
from ..llm.client import LlmError
from ..llm.messages import anthropic_tool_choice, anthropic_tools, from_anthropic, normalise_openai
from ..llm.stream import Accumulator, parse_arguments
from ..usage import note_tokens
from .common import acquire_llm, caller, hub_of, resolve_model, sse

router = APIRouter()


def _err(status: int, message: str, kind: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse({"type": "error", "error": {"type": kind, "message": message}}, status_code=status)


def _thinking(body: dict) -> tuple[bool, str | None]:
    t = body.get("thinking")
    if not isinstance(t, dict):
        return False, None
    kind = t.get("type")
    if kind == "disabled":
        return False, None
    if kind == "adaptive":
        return True, "medium"
    budget = int(t.get("budget_tokens") or 0)
    return True, ("low" if budget and budget < 4096 else "medium" if budget < 16384 else "xhigh")


def _stop_reason(res: dict) -> str:
    if res.get("tool_calls"):
        return "tool_use"
    if res.get("finish_reason") == "length":
        return "max_tokens"
    return "end_turn"


def _usage(res: dict) -> dict:
    u, t = res.get("usage") or {}, res.get("timings") or {}
    out = {"input_tokens": u.get("prompt_tokens", t.get("prompt_n", 0)),
           "output_tokens": u.get("completion_tokens", t.get("predicted_n", 0))}
    if t.get("cache_n"):
        out["cache_read_input_tokens"] = t["cache_n"]
    return out


async def _prepare(request: Request):
    hub = hub_of(request)
    try:
        body = await request.json()
    except ValueError:
        return None, _err(400, "the body must be JSON")
    name = body.get("model") or ""
    entry = resolve_model(hub, name)
    if entry is None:
        return None, _err(404, f"model: {name} does not exist on this Hub", "not_found_error")
    try:
        messages = await normalise_openai(from_anthropic(body.get("system"), body.get("messages") or []),
                                          netfetch.blocked_networks(hub))
    except Exception as exc:  # noqa: BLE001
        return None, _err(400, f"could not read the messages: {exc}")
    return (hub, body, name, entry, messages), None


@router.post("/v1/messages")
async def messages(request: Request):
    prepared, error = await _prepare(request)
    if error:
        return error
    hub, body, name, entry, msgs = prepared
    thinking, effort = _thinking(body)
    sampling = {"max_tokens": body.get("max_tokens")}
    for k in ("temperature", "top_p", "top_k"):
        if body.get(k) is not None:
            sampling[k] = body[k]
    if body.get("stop_sequences"):
        sampling["stop"] = body["stop_sequences"]
    pl = P.build(entry, msgs, tools=anthropic_tools(body.get("tools")),
                 tool_choice=anthropic_tool_choice(body.get("tool_choice")), thinking=thinking, effort=effort,
                 sampling=sampling)
    who = caller(request)
    label = f"{who.owner}: /v1/messages"
    msg_id = "msg_" + secrets.token_hex(12)

    if not body.get("stream"):
        try:
            ticket = await acquire_llm(hub, who, entry["id"], label)
        except SchedulerError as exc:
            return _err(529, str(exc), "overloaded_error")
        acc = Accumulator()
        try:
            async for chunk in hub.llm.stream_chat(hub.main_url, pl):
                acc.feed(chunk)
            res = acc.result()
        except LlmError as exc:
            return _err(exc.status if exc.status >= 400 else 500, str(exc), "api_error")
        finally:
            note_tokens(ticket, acc.usage, acc.timings)       # Settings -> Usage (0.24.0)
            await ticket.release()
        content: list[dict] = []
        if res.get("reasoning"):
            content.append({"type": "thinking", "thinking": res["reasoning"], "signature": ""})
        if res.get("content"):
            content.append({"type": "text", "text": res["content"]})
        for c in res.get("tool_calls") or []:
            try:
                args = parse_arguments(c.get("arguments"))
            except ValueError:
                args = {"_raw": c.get("arguments")}
            content.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": args})
        return {"id": msg_id, "type": "message", "role": "assistant", "model": name, "content": content,
                "stop_reason": _stop_reason(res), "stop_sequence": None, "usage": _usage(res)}

    async def gen():
        yield sse({"type": "message_start", "message": {
            "id": msg_id, "type": "message", "role": "assistant", "model": name, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}},
            "message_start")
        try:
            ticket = await acquire_llm(hub, who, entry["id"], label)
        except SchedulerError as exc:
            yield sse({"type": "error", "error": {"type": "overloaded_error", "message": str(exc)}}, "error")
            return
        index = -1
        open_kind: str | None = None
        tool_index: dict[str, int] = {}
        acc = Accumulator()

        def close():
            nonlocal open_kind
            out = []
            if open_kind == "thinking":
                out.append(sse({"type": "content_block_delta", "index": index,
                                "delta": {"type": "signature_delta", "signature": ""}}, "content_block_delta"))
            if open_kind is not None:
                out.append(sse({"type": "content_block_stop", "index": index}, "content_block_stop"))
            open_kind = None
            return out

        def open_block(kind: str, block: dict):
            nonlocal index, open_kind
            out = close()
            index += 1
            open_kind = kind
            out.append(sse({"type": "content_block_start", "index": index, "content_block": block},
                           "content_block_start"))
            return out

        try:
            async for chunk in hub.llm.stream_chat(hub.main_url, pl):
                for ev, data in acc.feed(chunk):
                    if ev == "thinking":
                        if open_kind != "thinking":
                            for b in open_block("thinking", {"type": "thinking", "thinking": "", "signature": ""}):
                                yield b
                        yield sse({"type": "content_block_delta", "index": index,
                                   "delta": {"type": "thinking_delta", "thinking": data["text"]}}, "content_block_delta")
                    elif ev == "text":
                        if open_kind != "text":
                            for b in open_block("text", {"type": "text", "text": ""}):
                                yield b
                        yield sse({"type": "content_block_delta", "index": index,
                                   "delta": {"type": "text_delta", "text": data["text"]}}, "content_block_delta")
                    elif ev == "tool_start":
                        for b in open_block("tool", {"type": "tool_use", "id": data["id"], "name": data["name"],
                                                     "input": {}}):
                            yield b
                        tool_index[data["id"]] = index
                    elif ev == "tool_args":
                        yield sse({"type": "content_block_delta", "index": tool_index.get(data["id"], index),
                                   "delta": {"type": "input_json_delta", "partial_json": data["delta"]}},
                                  "content_block_delta")
            res = acc.result()
            # Tool calls recovered from text (not streamed as tool_calls).
            if res.get("tool_calls") and not tool_index:
                for c in res["tool_calls"]:
                    for b in open_block("tool", {"type": "tool_use", "id": c["id"], "name": c["name"], "input": {}}):
                        yield b
                    yield sse({"type": "content_block_delta", "index": index,
                               "delta": {"type": "input_json_delta", "partial_json": c["arguments"] or "{}"}},
                              "content_block_delta")
            for b in close():
                yield b
            yield sse({"type": "message_delta", "delta": {"stop_reason": _stop_reason(res), "stop_sequence": None},
                       "usage": _usage(res)}, "message_delta")
            yield sse({"type": "message_stop"}, "message_stop")
        except LlmError as exc:
            yield sse({"type": "error", "error": {"type": "api_error", "message": str(exc)}}, "error")
        finally:
            note_tokens(ticket, acc.usage, acc.timings)
            await ticket.release()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    prepared, error = await _prepare(request)
    if error:
        return error
    hub, body, _name, entry, msgs = prepared
    tools = anthropic_tools(body.get("tools"))
    if hub.loaded_model() == entry["id"]:
        try:
            return {"input_tokens": await hub.llm.count_tokens(hub.main_url, msgs, tools)}
        except LlmError:
            pass
    text = json.dumps(msgs, ensure_ascii=False) + json.dumps(tools or [], ensure_ascii=False)
    return {"input_tokens": max(1, int(len(text) / 3.5))}

