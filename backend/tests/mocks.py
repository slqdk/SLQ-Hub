"""Fake services for tests: the llama-server supervisor, llama-server itself,
ComfyUI, the sandbox and SearXNG - plus a transport that routes requests to
them by host name."""
from __future__ import annotations

import json
import re
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse


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

def _words(text: str) -> list[str]:
    import re
    return [w for w in re.findall(r"[a-zæøå0-9]+", text.lower()) if len(w) > 2 and w not in {
        "the", "and", "for", "with", "query", "instruct", "given", "search", "retrieve", "relevant", "passages",
        "that", "answer", "represent", "user", "input", "document", "pages", "system", "assistant", "im_start",
        "im_end"}]


def fake_embedding(x) -> list[float]:
    """A bag of words hashed into 32 dimensions; an image adds the colour of
    its first pixel - enough to make retrieval testable."""
    import zlib
    v = [0.0] * 32
    if isinstance(x, dict):
        text = " ".join(p.get("text", "") for p in x.get("content", []) if p.get("type") == "text")
        for p in x.get("content", []):
            if p.get("type") == "image_url":
                import base64
                import io
                from PIL import Image
                data = base64.b64decode(p["image_url"]["url"].split(",", 1)[1])
                r, g, b = Image.open(io.BytesIO(data)).convert("RGB").getpixel((0, 0))
                v[0] += r / 64
                v[1] += g / 64
    else:
        text = str(x)
    for w in _words(text):
        if w == "red":
            v[0] += 4
        elif w == "green":
            v[1] += 4
        else:
            v[2 + zlib.crc32(w.encode()) % 30] += 1.0
    if not any(v):
        v[31] = 1.0
    return v


def make_llama(script: dict) -> FastAPI:
    """script["replies"]: list of replies used in order (the last repeats).
    A reply is {"content": str, "reasoning": str, "tool_calls": [{"name","arguments"}], "finish": str}."""
    app = FastAPI()
    script.setdefault("requests", [])

    def next_reply(body: dict) -> dict:
        if script.get("responder"):
            return script["responder"](body)
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
        script.setdefault("embedded", []).extend(inp)
        return {"data": [{"index": i, "embedding": fake_embedding(x)} for i, x in enumerate(inp)]}

    @app.post("/v1/rerank")
    async def rerank(request: Request):
        body = await request.json()
        q = set(_words(body["query"]))
        scored = [{"index": i, "relevance_score": min(0.99, 0.5 + 0.2 * len(q & set(_words(d)))) if q & set(_words(d))
                   else 0.05} for i, d in enumerate(body["documents"])]
        return {"results": sorted(scored, key=lambda r: -r["relevance_score"])}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        script["requests"].append(body)
        reply = next_reply(body)
        if reply.get("delay"):
            import asyncio
            await asyncio.sleep(reply["delay"])
        if reply.get("error"):
            return JSONResponse({"error": {"message": reply["error"]}}, status_code=500)
        calls = [{"index": i, "id": f"call_{len(script['requests'])}_{i}", "type": "function",
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
            # as llama-server b11783 does: the finish chunk is empty, and usage and
            # timings come together in a last chunk without choices
            yield chunk({}, finish)
            last = {"choices": [], "id": "chatcmpl-1", "object": "chat.completion.chunk", "model": body.get("model"),
                    "usage": {**usage, "prompt_tokens_details": {"cached_tokens": timings["cache_n"]}},
                    "timings": timings}
            yield f"data: {json.dumps(last)}\n\n".encode()
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


def silent_mp3(seconds: float = 1.0) -> bytes:
    """A real MP3 of silence: MPEG-1 Layer III frames (128 kbit/s, 44.1 kHz, 1152 samples each) whose side
    information is all zero - browsers and decoders play it for `seconds`."""
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413                 # 417 bytes: 144 * 128000 / 44100
    return frame * max(1, round(seconds * 44100 / 1152))


def wav_tone(seconds: float = 1.0, hz: float = 330.0, rate: int = 24000) -> bytes:
    import io
    import math
    import struct
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / rate)))
                               for i in range(int(seconds * rate))))
    return buf.getvalue()


def float_wav(samples, rate: int = 24000) -> bytes:
    """A mono 32-bit float WAV as libsndfile (torchaudio.save) writes it - fmt, fact and PEAK chunks, an 80-byte
    header - which is how the Røst voice samples on Hugging Face are stored."""
    import struct

    import numpy as np
    x = np.asarray(samples, dtype="<f4")
    data = x.tobytes()
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    fact = struct.pack("<I", len(x))
    peak = struct.pack("<IIfI", 1, 0, float(np.max(np.abs(x))) if x.size else 0.0, 0)
    chunks = b"".join(cid + struct.pack("<I", len(body)) + body
                      for cid, body in ((b"fmt ", fmt), (b"fact", fact), (b"PEAK", peak), (b"data", data)))
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


AUDIO_SAVERS = ("SaveAudio", "SaveAudioMP3", "SaveAudioOpus", "SaveAudioAdvanced")


def _saved_audio(graph: dict, pid: str) -> tuple[str, str] | None:
    """(node id, file name) when a graph saves sound, as ComfyUI names it under output/audio/."""
    for node_id, node in graph.items():
        if node.get("class_type") in AUDIO_SAVERS:
            fmt = (node.get("inputs") or {}).get("format") or "flac"
            return node_id, f"{pid}_00001_.{fmt}"
    return None


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
                 "ImageScaleToTotalPixels", "VAEEncode", "ReferenceLatent", "GetImageSize", "TextGenerate",
                 "PreviewAny", "ModelSamplingAuraFlow", "EmptySD3LatentImage", "T5TokenizerOptions", "BasicScheduler",
                 "CheckpointLoaderSimple", "CLIPSetLastLayer", "DualCLIPLoader", "TextEncodeAceStepAudio1.5",
                 "EmptyAceStep1.5LatentAudio", "VAEDecodeAudio", "SaveAudioAdvanced", "EmptyLatentAudio", "LoadAudio"]
        names = [n for n in names if n not in state.get("missing_nodes", ())]
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
        if state.get("files"):                                # like ComfyUI: into its input folder
            (state["files"] / "input").mkdir(parents=True, exist_ok=True)
            (state["files"] / "input" / f.filename).write_bytes(await f.read())
        return {"name": f.filename, "subfolder": "", "type": "input"}

    @app.post("/prompt")
    async def prompt(request: Request):
        body = await request.json()
        state["prompts"].append(body["prompt"])
        pid = f"p{len(state['prompts'])}"
        sound = _saved_audio(body["prompt"], pid)
        if state.get("files"):                                # and the picture (or sound) into its output folder
            out = state["files"] / "output" / ("audio" if sound else "")
            out.mkdir(parents=True, exist_ok=True)
            (out / (sound[1] if sound else f"{pid}.png")).write_bytes(silent_mp3(1) if sound else PNG_1x1)
        return {"prompt_id": pid, "number": len(state["prompts"]), "node_errors": {}}

    @app.post("/history")
    async def history_change(request: Request):
        state.setdefault("history_deleted", []).extend((await request.json()).get("delete") or [])
        return {}

    @app.get("/history/{pid}")
    async def history(pid: str):
        graph = state["prompts"][int(pid[1:]) - 1] if pid[1:].isdigit() and int(pid[1:]) <= len(state["prompts"]) else {}
        sound = _saved_audio(graph, pid)
        outputs = {sound[0]: {"audio": [{"filename": sound[1], "subfolder": "audio", "type": "output"}]}} if sound \
            else {"9": {"images": [{"filename": f"{pid}.png", "subfolder": "", "type": "output"}]}}
        for node_id, node in graph.items():                   # PreviewAny shows the text it was given
            if node.get("class_type") == "PreviewAny":
                outputs[node_id] = {"text": [state.get("preview_text", "An enhanced, detailed prompt.")]}
        return {pid: {"status": {"status_str": "success", "completed": True, "messages": []}, "outputs": outputs}}

    @app.get("/view")
    async def view(filename: str = ""):
        if filename.rsplit(".", 1)[-1] in ("mp3", "flac", "opus"):
            return Response(state.get("audio_bytes") or silent_mp3(state.get("audio_seconds", 1.0)),
                            media_type="audio/mpeg")
        return Response(PNG_1x1, media_type="image/png")

    return app


# ---------------------------------------------------------------- speech service

def make_speech(state: dict) -> FastAPI:
    """The speech service's protocol (services/speech/server.py): NDJSON progress lines, then the audio."""
    import base64
    import re as _re
    app = FastAPI()
    state.setdefault("requests", [])

    @app.get("/health")
    async def health():
        if state.get("down"):
            return JSONResponse({"error": "down"}, status_code=503)
        return {"ok": True, "torch": "2.11.0+cpu", "threads": 10, "loaded": [], "voices": 0, "busy": False}

    @app.post("/free")
    async def free():
        return {"freed": 0}

    @app.post("/speak")
    async def speak(request: Request):
        from fastapi.responses import StreamingResponse
        body = await request.json()
        state["requests"].append(body)
        if state.get("refuse"):
            return JSONResponse({"error": state["refuse"]}, status_code=400)
        pieces = [x for x in _re.split(r"(?<=[.!?])\s+", body.get("text", "").strip()) if x] or [""]
        seconds = round(max(0.5, len(body.get("text", "")) / 14), 2)

        async def gen():
            yield json.dumps({"event": "queued", "busy": False}) + "\n"
            yield json.dumps({"event": "started", "pieces": len(pieces)}) + "\n"
            for i in range(len(pieces)):
                yield json.dumps({"event": "progress", "piece": i + 1, "pieces": len(pieces)}) + "\n"
            if state.get("error"):
                yield json.dumps({"event": "error", "message": state["error"]}) + "\n"
                return
            data = wav_tone(seconds) if body.get("format") == "wav" else silent_mp3(seconds)
            yield json.dumps({"event": "done", "format": body.get("format"), "sample_rate": 24000,
                              "seconds": seconds, "compute": 1.5, "seed": body.get("seed"), "pieces": len(pieces),
                              "threads": 10, "audio": base64.b64encode(data).decode()}) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    return app


# ---------------------------------------------------------------- sandbox + search

def make_sandbox(state: dict) -> FastAPI:
    app = FastAPI()
    state.setdefault("execs", [])

    @app.get("/health")
    async def health():
        return {"ok": True, "uid": 1000, "root": "/workspace", "landlock": state.get("landlock", 7)}

    @app.post("/exec")
    async def exec_(request: Request):
        body = await request.json()
        state["execs"].append(body)
        if state.get("delay"):
            import asyncio
            await asyncio.sleep(state["delay"])
        handler = state.get("handler")
        if handler:
            return handler(body)
        return {"exit_code": 0, "stdout": f"ran: {body['cmd']}\n", "stderr": "", "timed_out": False,
                "duration_ms": 5, "truncated": False}

    # A fake MCP stdio server: JSON-RPC lines in, lines out.
    state.setdefault("procs", {})

    @app.post("/proc/start")
    async def proc_start(request: Request):
        body = await request.json()
        pid = f"p{len(state['procs']) + 1}"
        state["procs"][pid] = {"cmd": body["cmd"], "out": "noise on stdout\n", "killed": False}
        return {"id": pid}

    @app.post("/proc/write")
    async def proc_write(request: Request):
        body = await request.json()
        proc = state["procs"][body["id"]]
        for line in body["data"].splitlines():
            msg = json.loads(line)
            reply = mcp_reply(msg, "stdio")
            if reply is not None:
                proc["out"] += json.dumps(reply) + "\n"
        return {"ok": True}

    @app.post("/proc/read")
    async def proc_read(request: Request):
        import asyncio
        body = await request.json()
        proc = state["procs"].get(body["id"])
        if proc is None:
            return JSONResponse({"error": "no such process"}, status_code=400)
        for _ in range(10):
            if proc["out"] or proc["killed"]:
                break
            await asyncio.sleep(0.02)
        data, proc["out"] = proc["out"], ""
        return {"data": data, "eof": proc["killed"] and not data, "exit_code": 0 if proc["killed"] else None}

    @app.post("/proc/kill")
    async def proc_kill(request: Request):
        body = await request.json()
        state["procs"][body["id"]]["killed"] = True
        return {"ok": True}

    return app


def mcp_reply(msg: dict, flavour: str) -> dict | None:
    """The fake MCP server's answers (both transports)."""
    if "id" not in msg:
        return None                                       # a notification
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": f"fake-{flavour}", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": [
            {"name": "add", "description": "Add two numbers",
             "inputSchema": {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                             "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}},
            {"name": "picture", "description": "Return a small picture", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments") or {}
        if name == "add":
            result = {"content": [{"type": "text", "text": str(args.get("a", 0) + args.get("b", 0))}]}
        elif name == "picture":
            import base64
            result = {"content": [{"type": "text", "text": "here"},
                                  {"type": "image", "data": base64.b64encode(_png()).decode(), "mimeType": "image/png"}]}
        else:
            return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32602, "message": f"unknown tool {name}"}}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


def make_mcp_http(state: dict) -> FastAPI:
    """A Streamable HTTP MCP server: JSON for most answers, SSE for tools/call."""
    app = FastAPI()
    state.setdefault("calls", [])

    @app.post("/mcp")
    async def mcp(request: Request):
        if state.get("auth") and request.headers.get("authorization") != state["auth"]:
            return JSONResponse({"error": "unauthorised"}, status_code=401)
        msg = await request.json()
        state["calls"].append(msg)
        reply = mcp_reply(msg, "http")
        if reply is None:
            return Response(status_code=202)
        headers = {"Mcp-Session-Id": "sess-1"} if msg.get("method") == "initialize" else {}
        if msg.get("method") == "tools/call":
            async def gen():
                yield b": keep-alive\n\n"
                yield f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'method': 'notifications/progress', 'params': {}})}\n\n".encode()
                yield f"event: message\ndata: {json.dumps(reply)}\n\n".encode()
            return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
        return JSONResponse(reply, headers=headers)

    @app.delete("/mcp")
    async def end():
        return Response(status_code=204)

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


# ---------------------------------------------------------------- Hugging Face

def _ranged(data: bytes, request: Request) -> Response:
    """The whole body, or the part a Range header asks for (206)."""
    m = re.match(r"bytes=(\d+)-(\d*)$", request.headers.get("range") or "")
    if not m:
        return Response(data, media_type="application/octet-stream")
    start = int(m[1])
    end = min(int(m[2]) if m[2] else len(data) - 1, len(data) - 1)
    if start >= len(data):
        return Response(status_code=416)
    return Response(data[start:end + 1], status_code=206, media_type="application/octet-stream",
                    headers={"Content-Range": f"bytes {start}-{end}/{len(data)}"})


def safetensors_bytes(header: dict, pad: int = 1024) -> bytes:
    raw = json.dumps(header).encode()
    return len(raw).to_bytes(8, "little") + raw + b"\0" * pad


def make_hf(state: dict) -> FastAPI:
    """The bits of huggingface.co the Hub uses: the tree and model-info APIs and
    file downloads. state["repos"][repo] = {"tree": [...], "gguf": {...}} or
    {"status": 401}; downloads serve b"g" * size, or a .safetensors file when
    the tree entry has a "header"; Range requests get their part."""
    app = FastAPI()

    def repo_of(owner: str, name: str) -> dict | None:
        return (state.get("repos") or {}).get(f"{owner}/{name}")

    @app.get("/api/models")
    async def search(request: Request):
        """The model search: state["listing"] holds the records it matches against
        (fields as the real API sends them without expand: id, downloads, likes, ...)."""
        q = request.query_params
        state.setdefault("searches", []).append(dict(q))
        term = (q.get("search") or "").lower()
        hits = [m for m in state.get("listing") or [] if term in m["id"].lower()
                and (q.get("filter") != "gguf" or "gguf" in m.get("tags", []))]
        hits.sort(key=lambda m: -m.get("downloads", 0))
        return hits[:int(q.get("limit") or 50)]

    @app.get("/api/models/{owner}/{name}/tree/main")
    async def tree(owner: str, name: str, recursive: bool = False):
        r = repo_of(owner, name)
        if r is None:
            return JSONResponse({"error": "Repository not found"}, status_code=404)
        if r.get("status"):
            return JSONResponse({"error": "Access denied"}, status_code=r["status"])
        return r["tree"]

    @app.get("/api/models/{owner}/{name}")
    async def info(owner: str, name: str):
        r = repo_of(owner, name)
        if r is None or r.get("status"):
            return JSONResponse({"error": "no"}, status_code=404)
        return {"_id": "x", "id": f"{owner}/{name}", "gguf": r.get("gguf") or {}}

    @app.get("/{owner}/{name}/resolve/main/{path:path}")
    async def resolve(owner: str, name: str, path: str, request: Request):
        r = repo_of(owner, name) or {}
        f = next((x for x in r.get("tree", []) if x.get("path") == path), None)
        if f is None:
            return PlainTextResponse("Entry not found", status_code=404)
        rng = request.headers.get("range") or ""
        if not rng or rng.endswith("-"):                    # a download, not a header read
            state.setdefault("downloaded", []).append(f"{owner}/{name}/{path}")
        if f.get("header") is not None:
            return _ranged(safetensors_bytes(f["header"]), request)
        size = 1024 if state.get("tiny_downloads") else int(f.get("size") or 0)
        return _ranged(b"g" * size, request)

    return app


def make_civitai(state: dict) -> FastAPI:
    """civitai.com: the model and model-version APIs, and downloads that
    redirect to its storage (delivery.civitai.test). state["models"][id] is a
    model as the API returns it; with state["token"], downloads need
    "Authorization: Bearer <token>"."""
    app = FastAPI()

    def models() -> dict:
        return state.get("models") or {}

    @app.get("/api/v1/models/{mid}")
    async def model(mid: int):
        m = models().get(mid)
        if m is None:
            return JSONResponse({"error": f"No model with id {mid}"}, status_code=404)
        return m

    @app.get("/api/v1/model-versions/{vid}")
    async def version(vid: int):
        for m in models().values():
            for v in m.get("modelVersions", []):
                if v["id"] == vid:
                    return {**v, "modelId": m["id"], "model": {"name": m["name"], "type": m["type"],
                                                                "nsfw": m.get("nsfw")}}
        return JSONResponse({"error": f"No version with id {vid}"}, status_code=404)

    @app.get("/api/download/models/{vid}")
    async def download(vid: int, request: Request):
        state.setdefault("download_auth", []).append(request.headers.get("authorization"))
        if state.get("token") and request.headers.get("authorization") != f"Bearer {state['token']}":
            return JSONResponse({"error": "Unauthorized", "message": "The creator of this asset requires you to be "
                                 "logged in to download it"}, status_code=401)
        return RedirectResponse(f"https://delivery.civitai.test/file/{vid}.safetensors", status_code=307)

    return app


def make_civitai_delivery(state: dict) -> FastAPI:
    """Civitai's file storage behind the signed redirect."""
    app = FastAPI()

    @app.get("/file/{name}")
    async def file(name: str, request: Request):
        state.setdefault("delivery_auth", []).append(request.headers.get("authorization"))
        state.setdefault("delivered", []).append(name)
        return _ranged(b"c" * 2048, request)

    return app
