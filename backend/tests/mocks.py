"""Fake services for tests: the llama-server supervisor, llama-server itself,
ComfyUI, the sandbox and SearXNG - plus a transport that routes requests to
them by host name."""
from __future__ import annotations

import json
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse


class HostRouter(httpx.AsyncBaseTransport):
    """Sends each request to the ASGI app registered for its host[:port]."""

    def __init__(self, apps: dict[str, FastAPI]):
        self.transports = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        key = f"{host}:{request.url.port}" if request.url.port else host
        t = self.transports.get(key) or self.transports.get(host)
        if t is None:
            raise httpx.ConnectError(f"no fake service for {key}", request=request)
        return await t.handle_async_request(request)


# ---------------------------------------------------------------- supervisor

def make_supervisor(state: dict) -> FastAPI:
    app = FastAPI()
    state.setdefault("procs", {})
    state.setdefault("starts", [])

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/status")
    async def status():
        return {"processes": state["procs"], "gpu": {"vram_total": 24 * 1024**3, "vram_used": 1024**3}}

    @app.get("/gpu")
    async def gpu():
        return {"vram_total": 24 * 1024**3, "vram_used": 1024**3}

    @app.post("/start")
    async def start(request: Request):
        body = await request.json()
        state["starts"].append(body)
        state["procs"][body["name"]] = {"name": body["name"], "port": body["port"], "args": body["args"],
                                        "state": "running", "returncode": None}
        return state["procs"][body["name"]]

    @app.post("/stop")
    async def stop(request: Request):
        body = await request.json()
        names = list(state["procs"]) if body.get("all") else [body.get("name")]
        for n in names:
            state["procs"].pop(n, None)
        return {"stopped": names}

    @app.get("/logs")
    async def logs():
        return PlainTextResponse("log line")

    return app


# ---------------------------------------------------------------- llama-server

def make_llama(script: dict) -> FastAPI:
    """script["replies"]: list of replies used in order (the last repeats).
    A reply is {"content": str, "reasoning": str, "tool_calls": [{"name","arguments"}], "finish": str}."""
    app = FastAPI()
    script.setdefault("requests", [])

    def next_reply() -> dict:
        replies = script.get("replies") or [{"content": "Hello from the fake model.", "reasoning": "thinking..."}]
        idx = min(len(script["requests"]) - 1, len(replies) - 1)
        return replies[idx]

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/tokenize")
    async def tokenize(request: Request):
        body = await request.json()
        return {"tokens": list(range(max(1, len(body.get("content", "")) // 4)))}

    @app.post("/apply-template")
    async def apply_template(request: Request):
        body = await request.json()
        return {"prompt": json.dumps(body.get("messages"))}

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        body = await request.json()
        inp = body["input"] if isinstance(body["input"], list) else [body["input"]]
        return {"data": [{"index": i, "embedding": [float(len(str(x)) % 7), 1.0, 0.5]} for i, x in enumerate(inp)]}

    @app.post("/v1/rerank")
    async def rerank(request: Request):
        body = await request.json()
        return {"results": [{"index": i, "relevance_score": 1.0 / (i + 1)} for i, _ in enumerate(body["documents"])]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        script["requests"].append(body)
        reply = next_reply()
        if reply.get("error"):
            return JSONResponse({"error": {"message": reply["error"]}}, status_code=500)
        calls = [{"index": i, "id": f"call_{i}", "type": "function",
                  "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}))}}
                 for i, c in enumerate(reply.get("tool_calls") or [])]
        finish = reply.get("finish") or ("tool_calls" if calls else "stop")
        timings = {"prompt_n": 12, "prompt_ms": 50.0, "predicted_n": 7, "predicted_ms": 100.0, "cache_n": 3}
        usage = {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}
        if not body.get("stream"):
            msg = {"role": "assistant", "content": reply.get("content", "")}
            if reply.get("reasoning"):
                msg["reasoning_content"] = reply["reasoning"]
            if calls:
                msg["tool_calls"] = [{k: v for k, v in c.items() if k != "index"} for c in calls]
            return {"id": "chatcmpl-1", "object": "chat.completion", "created": int(time.time()),
                    "model": body.get("model"), "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                    "usage": usage, "timings": timings}

        def chunk(delta: dict, finish_reason=None, **extra) -> bytes:
            obj = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": int(time.time()),
                   "model": body.get("model"), "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
            obj.update(extra)
            return f"data: {json.dumps(obj)}\n\n".encode()

        async def gen():
            if reply.get("reasoning"):
                for part in (reply["reasoning"][:5], reply["reasoning"][5:]):
                    if part:
                        yield chunk({"reasoning_content": part})
            content = reply.get("content", "")
            for i in range(0, len(content), 6):
                yield chunk({"content": content[i:i + 6]})
            for c in calls:
                args = c["function"]["arguments"]
                yield chunk({"tool_calls": [{"index": c["index"], "id": c["id"], "type": "function",
                                             "function": {"name": c["function"]["name"], "arguments": ""}}]})
                for i in range(0, len(args), 8):
                    yield chunk({"tool_calls": [{"index": c["index"], "function": {"arguments": args[i:i + 8]}}]})
            yield chunk({}, finish, timings=timings)
            yield f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


# ---------------------------------------------------------------- ComfyUI

def _png(size=(8, 8), color=(200, 30, 30, 255)) -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


PNG_1x1 = _png()


def make_comfy(state: dict) -> FastAPI:
    app = FastAPI()
    state.setdefault("prompts", [])
    state.setdefault("uploads", [])
    state.setdefault("frees", 0)

    @app.get("/system_stats")
    async def stats():
        return {"system": {"pytorch_version": "2.14.1+rocm7.14"},
                "devices": [{"name": "cuda:0 AMD Radeon RX 7900 XTX", "type": "cuda", "vram_total": 24 * 1024**3,
                             "vram_free": 23 * 1024**3}]}

    @app.get("/object_info")
    async def object_info():
        names = ["UNETLoader", "QwenImage21Cache", "CLIPLoader", "VAELoader", "TextEncodeQwenImage21",
                 "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage", "SaveImageAdvanced", "LoadImage",
                 "KSamplerSelect", "RandomNoise", "CFGGuider", "SamplerCustomAdvanced", "ManualSigmas",
                 "CLIPTextEncode", "EmptyFlux2LatentImage", "Flux2Scheduler", "ConditioningZeroOut",
                 "ImageScaleToTotalPixels", "VAEEncode", "ReferenceLatent", "GetImageSize"]
        return {n: {"input": {}, "output": []} for n in names}

    @app.post("/free")
    async def free():
        state["frees"] += 1
        return {}

    @app.post("/interrupt")
    async def interrupt():
        return {}

    @app.post("/upload/image")
    async def upload(request: Request):
        form = await request.form()
        f = form["image"]
        state["uploads"].append(f.filename)
        return {"name": f.filename, "subfolder": "", "type": "input"}

    @app.post("/prompt")
    async def prompt(request: Request):
        body = await request.json()
        state["prompts"].append(body["prompt"])
        return {"prompt_id": f"p{len(state['prompts'])}", "number": len(state["prompts"]), "node_errors": {}}

    @app.get("/history/{pid}")
    async def history(pid: str):
        return {pid: {"status": {"status_str": "success", "completed": True, "messages": []},
                      "outputs": {"9": {"images": [{"filename": f"{pid}.png", "subfolder": "", "type": "output"}]}}}}

    @app.get("/view")
    async def view():
        return Response(PNG_1x1, media_type="image/png")

    return app


# ---------------------------------------------------------------- sandbox + search

def make_sandbox(state: dict) -> FastAPI:
    app = FastAPI()
    state.setdefault("execs", [])

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/exec")
    async def exec_(request: Request):
        body = await request.json()
        state["execs"].append(body)
        handler = state.get("handler")
        if handler:
            return handler(body)
        return {"exit_code": 0, "stdout": f"ran: {body['cmd']}\n", "stderr": "", "timed_out": False,
                "duration_ms": 5, "truncated": False}

    return app


def make_searxng() -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz():
        return PlainTextResponse("OK")

    @app.get("/search")
    async def search(q: str):
        return {"results": [{"title": f"Result for {q}", "url": "https://example.com/a", "content": "A snippet."},
                            {"title": "Second", "url": "https://example.org/b", "content": "Another."}]}

    return app
