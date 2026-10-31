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


class StreamingASGITransport(httpx.AsyncBaseTransport):
    """Like httpx.ASGITransport, but the response body arrives as the app sends it (ASGITransport waits for the
    whole body): the dev server's model streams for real, so the UI can be watched while text arrives."""

    def __init__(self, app):
        self.app = app

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import asyncio
        body = await request.aread()
        queue: asyncio.Queue = asyncio.Queue()
        started: asyncio.Future = asyncio.get_running_loop().create_future()
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": request.method,
                 "scheme": request.url.scheme, "path": request.url.path, "raw_path": request.url.raw_path.split(b"?")[0],
                 "query_string": request.url.query, "root_path": "",
                 "headers": [(k.lower(), v) for k, v in request.headers.raw],
                 "server": (request.url.host, request.url.port or 80), "client": ("127.0.0.1", 12345)}
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                await asyncio.Event().wait()                   # the client stays connected
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start" and not started.done():
                started.set_result((message["status"], message.get("headers") or []))
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    await queue.put(message["body"])
                if not message.get("more_body"):
                    await queue.put(None)

        async def run():
            try:
                await self.app(scope, receive, send)
            except Exception as exc:  # noqa: BLE001 - surfaces as the request's error
                if not started.done():
                    started.set_exception(exc)
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        status, headers = await started

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                while (chunk := await queue.get()) is not None:
                    yield chunk

            async def aclose(self):
                task.cancel()

        return httpx.Response(status, headers=headers, stream=Body(), request=request)


class HostRouter(httpx.AsyncBaseTransport):
    """Sends each request to the ASGI app registered for its host[:port]; `streaming` hosts answer as they go."""

    def __init__(self, apps: dict[str, FastAPI], streaming: tuple[str, ...] = ()):
        self.transports = {host: StreamingASGITransport(app) if host in streaming else httpx.ASGITransport(app=app)
                           for host, app in apps.items()}

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
        return {"processes": state["procs"], "gpu": {"vram_total": 24 * 1024**3,
                                                     "vram_used": state.get("vram_used", 1024**3)}}

    @app.get("/gpu")
    async def gpu():
        out = {"vram_total": 24 * 1024**3, "vram_used": state.get("vram_used", 1024**3)}
        if state.get("power_w", 280.0) is not None:            # the card's own reading (0.24.0); None: none
            out.update({"power_w": state.get("power_w", 280.0), "power_cap_w": 339.0, "power_source": "power1_average"})
        return out

    @app.post("/start")
    async def start(request: Request):
        body = await request.json()
        state["starts"].append(body)
        oom = state.get("vram_used", 0) > 20 * 1024**3        # the card is full: llama-server stops while loading
        state["procs"][body["name"]] = {"name": body["name"], "port": body["port"], "args": body["args"],
                                        "state": "exited" if oom else "running", "returncode": 1 if oom else None}
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
        if state.get("vram_used", 0) > 20 * 1024**3:
            return PlainTextResponse("0.01.462.660 E ggml_backend_cuda_buffer_type_alloc_buffer: allocating 16383.34 MiB "
                                     "on device 0: cudaMalloc failed: out of memory")
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
    A reply is {"content": str, "reasoning": str, "tool_calls": [{"name","arguments"}], "finish": str}; a tool
    call's "raw" is its arguments text as sent (cut off, say), a reply's "usage" its token counts. Like
    llama-server with --jinja, a request whose history has a tool call with arguments that are not a JSON object
    is refused with a 500 (it renders every earlier call into the chat template). Like b11783 (0.30.0): POST
    /v1/chat/completions/control ends the reasoning of a completion that streams with reasoning_control on - the
    request's reasoning_budget_message, then the answer - and answers success for any such completion while it
    streams, thinking or not (script["controls"] keeps each)."""
    app = FastAPI()
    script.setdefault("requests", [])
    script.setdefault("controls", [])
    streaming: dict[str, dict] = {}                     # completion id -> {"control", "stop"}, while it streams

    def next_reply(body: dict) -> dict:
        if script.get("responder"):
            return script["responder"](body)
        replies = script.get("replies") or [{"content": "Hello from the fake model.", "reasoning": "thinking..."}]
        idx = min(len(script["requests"]) - 1, len(replies) - 1)
        return replies[idx]

    @app.get("/health")
    async def health():
        if script.get("down"):                                # still loading, or never will
            return JSONResponse({"error": {"message": "Loading model"}}, status_code=503)
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
        for m in body.get("messages") or []:
            for tc in m.get("tool_calls") or []:
                try:
                    readable = isinstance(json.loads(tc["function"]["arguments"]), dict)
                except (KeyError, TypeError, ValueError):
                    readable = False
                if not readable:
                    script["refused"] = script.get("refused", 0) + 1
                    return JSONResponse({"error": {"code": 500, "message": "Failed to parse tool call arguments as "
                                                   "JSON: [json.exception.parse_error.101] parse error"}},
                                        status_code=500)
        reply = next_reply(body)
        cmpl_id = f"chatcmpl-{len(script['requests'])}"
        reasoning = reply.get("reasoning") or ""
        message = body.get("reasoning_budget_message") or ""
        if reply.get("delay"):
            import asyncio
            await asyncio.sleep(reply["delay"])
        if reply.get("error"):
            return JSONResponse({"error": {"message": reply["error"]}}, status_code=500)
        calls = [{"index": i, "id": f"call_{len(script['requests'])}_{i}", "type": "function",
                  "function": {"name": c["name"],
                               "arguments": c["raw"] if "raw" in c else json.dumps(c.get("arguments", {}))}}
                 for i, c in enumerate(reply.get("tool_calls") or [])]
        finish = reply.get("finish") or ("tool_calls" if calls else "stop")
        timings = {"prompt_n": 12, "prompt_ms": 50.0, "predicted_n": 7, "predicted_ms": 100.0, "cache_n": 3}
        usage = reply.get("usage") or {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}
        if not body.get("stream"):
            msg = {"role": "assistant", "content": reply.get("content", "")}
            if reasoning:
                msg["reasoning_content"] = reasoning
            if calls:
                msg["tool_calls"] = [{k: v for k, v in c.items() if k != "index"} for c in calls]
            return {"id": cmpl_id, "object": "chat.completion", "created": int(time.time()),
                    "model": body.get("model"), "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                    "usage": usage, "timings": timings}

        def chunk(delta: dict, finish_reason=None, **extra) -> bytes:
            obj = {"id": cmpl_id, "object": "chat.completion.chunk", "created": int(time.time()),
                   "model": body.get("model"), "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
            obj.update(extra)
            return f"data: {json.dumps(obj)}\n\n".encode()

        async def gen():
            streaming[cmpl_id] = {"control": bool(body.get("reasoning_control")), "stop": False}
            try:
                async for part in parts():
                    yield part
            finally:
                streaming.pop(cmpl_id, None)

        async def parts():
            import asyncio
            if reasoning and reply.get("pace"):                   # streamed a little at a time, to watch it arrive
                for i in range(0, len(reasoning), 9):
                    if streaming[cmpl_id]["stop"]:                # ended: the message, a piece at a time, then the end
                        for j in range(0, len(message), 9):
                            yield chunk({"reasoning_content": message[j:j + 9]})
                            await asyncio.sleep(reply["pace"])
                        break
                    yield chunk({"reasoning_content": reasoning[i:i + 9]})
                    await asyncio.sleep(reply["pace"])
            elif reasoning:
                for part in (reasoning[:5], reasoning[5:]):
                    if part:
                        yield chunk({"reasoning_content": part})
            content = reply.get("content", "")
            for i in range(0, len(content), 6):
                yield chunk({"content": content[i:i + 6]})
                if reply.get("content_pace"):                     # the answer written slowly too
                    await asyncio.sleep(reply["content_pace"])
            for c in calls:
                args = c["function"]["arguments"]
                yield chunk({"tool_calls": [{"index": c["index"], "id": c["id"], "type": "function",
                                             "function": {"name": c["function"]["name"], "arguments": ""}}]})
                for i in range(0, len(args), 8):
                    yield chunk({"tool_calls": [{"index": c["index"], "function": {"arguments": args[i:i + 8]}}]})
            # as llama-server b11783 does: the finish chunk is empty, and usage and
            # timings come together in a last chunk without choices
            yield chunk({}, finish)
            last = {"choices": [], "id": cmpl_id, "object": "chat.completion.chunk", "model": body.get("model"),
                    "usage": {**usage, "prompt_tokens_details": {"cached_tokens": timings["cache_n"]}},
                    "timings": timings}
            yield f"data: {json.dumps(last)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/v1/chat/completions/control")
    async def control(request: Request):
        body = await request.json()
        script["controls"].append(body)
        if body.get("action") != "reasoning_end":
            return JSONResponse({"error": {"message": "unknown control action"}}, status_code=400)
        live = streaming.get(body.get("id"))
        if live is None:
            return {"success": False, "message": "no active completion for this id"}
        if not live["control"]:
            return {"success": False, "message": "reasoning control not enabled for this completion"}
        live["stop"] = True                                   # (too late once the answer has started: no effect)
        return {"success": True}

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


def fake_flac(seconds: float, rate: int = 44100, channels: int = 2) -> bytes:
    """The start of a FLAC file - its marker and STREAMINFO block - for so many seconds: all the Hub reads of the
    files ffmpeg makes for ComfyUI (ComfyUI decodes them, the Hub never does)."""
    info = (rate << 44) | ((channels - 1) << 41) | (15 << 36) | int(seconds * rate)
    streaminfo = (4096).to_bytes(2, "big") * 2 + bytes(6) + info.to_bytes(8, "big") + bytes(16)
    return b"fLaC" + b"\x80" + len(streaminfo).to_bytes(3, "big") + streaminfo


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
    """(node id, file name) of the first node that saves sound."""
    every = _saved_audios(graph, pid)
    return every[0] if every else None


def _saved_audios(graph: dict, pid: str) -> list[tuple[str, str]]:
    """(node id, file name) for each node that saves sound, named as ComfyUI names them under output/audio/: the
    filename_prefix's last part, a counter, the format (hub-vocals_00001.mp3)."""
    out = []
    for node_id, node in graph.items():
        if node.get("class_type") in AUDIO_SAVERS:
            inputs = node.get("inputs") or {}
            fmt = inputs.get("format") or "flac"
            stem = str(inputs.get("filename_prefix") or pid).rsplit("/", 1)[-1]
            out.append((node_id, f"{stem}_{int(pid[1:]) if pid[1:].isdigit() else 1:05}.{fmt}"))
    return out


def fake_mp4(size: int = 2048) -> bytes:
    """The start of an MP4 (an ftyp box and an mdat of `size` bytes): the Hub never decodes the videos ComfyUI makes,
    it only hands them on."""
    ftyp = b"ftypisom" + (512).to_bytes(4, "big") + b"isomiso2avc1mp41"
    return (8 + len(ftyp) - 4).to_bytes(4, "big") + ftyp + (8 + size).to_bytes(4, "big") + b"mdat" + bytes(size)


def _saved_videos(graph: dict, pid: str) -> list[tuple[str, str, str]]:
    """(node id, subfolder, file name) for each SaveVideo node, named as ComfyUI v0.42 names them: the
    filename_prefix's folders, its last part, a counter and a trailing underscore (video/hub -> video, hub_00001_.mp4)."""
    out = []
    for node_id, node in graph.items():
        if node.get("class_type") == "SaveVideo":
            inputs = node.get("inputs") or {}
            sub, _, stem = str(inputs.get("filename_prefix") or "ComfyUI").rpartition("/")
            n = int(pid[1:]) if pid[1:].isdigit() else 1
            ext = "mp4" if inputs.get("format") in (None, "auto", "mp4") else inputs["format"]
            out.append((node_id, sub, f"{stem}_{n:05}_.{ext}"))
    return out


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
        if state.get("object_info_down"):                     # too busy to list its nodes
            return JSONResponse({"error": "busy"}, status_code=503)
        names = ["UNETLoader", "QwenImage21Cache", "CLIPLoader", "VAELoader", "TextEncodeQwenImage21",
                 "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage", "SaveImageAdvanced", "LoadImage",
                 "KSamplerSelect", "RandomNoise", "CFGGuider", "SamplerCustomAdvanced", "ManualSigmas",
                 "CLIPTextEncode", "EmptyFlux2LatentImage", "Flux2Scheduler", "ConditioningZeroOut",
                 "ImageScaleToTotalPixels", "VAEEncode", "ReferenceLatent", "GetImageSize", "TextGenerate",
                 "PreviewAny", "ModelSamplingAuraFlow", "EmptySD3LatentImage", "T5TokenizerOptions", "BasicScheduler",
                 "CheckpointLoaderSimple", "CLIPSetLastLayer", "DualCLIPLoader", "TextEncodeAceStepAudio1.5",
                 "EmptyAceStep1.5LatentAudio", "VAEDecodeAudio", "SaveAudioAdvanced", "EmptyLatentAudio", "LoadAudio",
                 "VAEEncodeAudio", "AudioMerge", "SLQAceStepReference", "SLQAceStepEncode", "SLQSeparateVocals",
                 "SLQSeedVC", "SLQBeatCheck", "ModelSamplingSD3", "Wan22ImageToVideoLatent", "WanImageToVideo",
                 "WanFirstLastFrameToVideo", "EmptyHunyuanLatentVideo", "KSamplerAdvanced", "LoraLoaderModelOnly", "LoraLoader",
                 "CreateVideo", "SaveVideo", "TrimAudioDuration", "AudioConcat", "VAEDecodeTiled"]
        names = [n for n in names if n not in state.get("missing_nodes", ())]
        return {n: {"input": {}, "output": []} for n in names}

    @app.post("/free")
    async def free():
        state["frees"] += 1
        if state.get("on_free"):
            state["on_free"]()
        return {}

    @app.post("/interrupt")
    async def interrupt(request: Request):
        state.setdefault("interrupts", []).append(await request.json())
        return {}

    @app.post("/queue")
    async def queue_edit(request: Request):
        state.setdefault("queue_edits", []).append(await request.json())
        return {}

    @app.get("/queue")
    async def queue_list():
        """A job queued less than `running_s` ago is running; with `lost` ComfyUI has none (it restarted)."""
        running = [[i + 1, f"p{i + 1}", {}, {}, []] for i, at in enumerate(state.get("queued_at", []))
                   if not state.get("lost") and time.monotonic() - at < state.get("running_s", 0)]
        return {"queue_running": running, "queue_pending": []}

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
        state.setdefault("queued_at", []).append(time.monotonic())
        pid = f"p{len(state['prompts'])}"
        sounds = _saved_audios(body["prompt"], pid)
        videos = _saved_videos(body["prompt"], pid)
        if state.get("files"):                                # and the pictures (sounds, videos) into its output folder
            out = state["files"] / "output" / ("audio" if sounds else "")
            out.mkdir(parents=True, exist_ok=True)
            for _, name in sounds:
                (out / name).write_bytes(silent_mp3(1))
            for _, sub, name in videos:
                (out / sub).mkdir(parents=True, exist_ok=True)
                (out / sub / name).write_bytes(fake_mp4())
            if not sounds and not videos:
                (out / f"{pid}.png").write_bytes(PNG_1x1)
        return {"prompt_id": pid, "number": len(state["prompts"]), "node_errors": {}}

    @app.post("/history")
    async def history_change(request: Request):
        state.setdefault("history_deleted", []).extend((await request.json()).get("delete") or [])
        return {}

    @app.get("/history/{pid}")
    async def history(pid: str):
        n = int(pid[1:]) if pid[1:].isdigit() else 0
        if state.get("lost") or (0 < n <= len(state.get("queued_at", [])) and
                                 time.monotonic() - state["queued_at"][n - 1] < state.get("running_s", 0)):
            return {}                                         # not done yet - or gone, with ComfyUI restarted
        graph = state["prompts"][int(pid[1:]) - 1] if pid[1:].isdigit() and int(pid[1:]) <= len(state["prompts"]) else {}
        sounds = _saved_audios(graph, pid)
        videos = _saved_videos(graph, pid)
        if sounds:
            outputs = {node: {"audio": [{"filename": name, "subfolder": "audio", "type": "output"}]}
                       for node, name in sounds}
        elif videos:                                          # SaveVideo reports its file among "images", animated
            outputs = {} if state.get("no_video") else \
                {node: {"images": [{"filename": name, "subfolder": sub, "type": "output"}], "animated": [True]}
                 for node, sub, name in videos}
        else:
            outputs = {"9": {"images": [{"filename": f"{pid}.png", "subfolder": "", "type": "output"}]}}
        for node_id, node in graph.items():                   # PreviewAny shows the text it was given
            if node.get("class_type") == "PreviewAny":
                outputs[node_id] = {"text": [state.get("preview_text", "An enhanced, detailed prompt.")]}
            if node.get("class_type") == "SLQAceStepReference" and "source" in (node.get("inputs") or {}):
                outputs[node_id] = {"text": [state.get("ace_check", json.dumps(          # its check of the codes
                    {"timbre_frames": 750, "codes": 1295, "agree": 0.998, "cosine_mean": 0.9993,
                     "cosine_min": 0.97, "distinct": 811}))]}
            if node.get("class_type") == "SLQBeatCheck":      # whether a remake kept its song's time
                outputs[node_id] = {"text": [state.get("beat_check", json.dumps(
                    {"song_bpm": 97, "new_bpm": 97, "song_key": "F# minor", "new_key": "F# minor", "on_beat": 0.99,
                     "covered": 0.98, "chance": 0.228, "verdict": "follows"}))]}
            if node.get("class_type") == "SLQSeedVC":         # what the conversion did, as the node reports it
                outputs[node_id] = {"text": [state.get("vc_text", json.dumps(
                    {"semitones": 12, "reference_seconds": 18.5, "seconds": 61.0, "rate": 44100}))]}
        if state.get("fail_with"):
            return {pid: {"status": {"status_str": "error", "completed": False, "messages": [
                ["execution_error", {"node_type": state.get("fail_node", "SLQSeedVC"),
                                     "exception_message": state["fail_with"]}]]},
                "outputs": {}}}
        return {pid: {"status": {"status_str": "success", "completed": True, "messages": []}, "outputs": outputs}}

    @app.get("/view")
    async def view(filename: str = ""):
        if filename.rsplit(".", 1)[-1] in ("mp3", "flac", "opus"):
            return Response(state.get("audio_bytes") or silent_mp3(state.get("audio_seconds", 1.0)),
                            media_type="audio/mpeg")
        if filename.endswith(".mp4"):
            return Response(state.get("video_bytes") or fake_mp4(), media_type="video/mp4")
        return Response(state.get("picture_bytes") or PNG_1x1, media_type="image/png")

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
                              "threads": 10, "tokens": int(seconds * 25), "capped": int(state.get("capped", 0)),
                              "t3_seconds": 0.9, "decoder_seconds": 0.5,
                              "audio": base64.b64encode(data).decode()}) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    return app


# ---------------------------------------------------------------- sandbox + search

def make_sandbox(state: dict) -> FastAPI:
    app = FastAPI()
    state.setdefault("execs", [])

    @app.get("/health")
    async def health():
        abi = state.get("landlock", 7)
        return {"ok": True, "uid": 1000, "root": "/workspace", "landlock": abi, "offline": state.get("offline", abi >= 4),
                "windows": state.get("windows", False), "browser": state.get("browser", False)}

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


def mcp_reply(msg: dict, flavour: str, state: dict | None = None) -> dict | None:
    """The fake MCP server's answers (both transports); state["tools"] replaces its tool list."""
    if "id" not in msg:
        return None                                       # a notification
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": f"fake-{flavour}", "version": "1.0"}}
    elif method == "tools/list" and (state or {}).get("tools") is not None:
        result = {"tools": state["tools"]}
    elif method == "tools/list":
        result = {"tools": [
            {"name": "add", "description": "Add two numbers",
             "inputSchema": {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                             "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}},
            {"name": "picture", "description": "Return a small picture", "inputSchema": {"type": "object"}},
            {"name": "build", "description": "Build the files", "annotations": {"readOnlyHint": True},
             "inputSchema": {"type": "object", "properties": {
                 "files": {"type": "array", "items": {"type": "string", "format": "slq-hub-file"}},
                 "target": {"type": "string"}}, "required": ["files"]}}]}
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments") or {}
        if name == "add":
            result = {"content": [{"type": "text", "text": str(args.get("a", 0) + args.get("b", 0))}]}
        elif name == "build":
            import base64
            got = (msg["params"].get("_meta") or {}).get("slq-hub/files") or []
            result = {"content": [{"type": "text", "text": "; ".join(
                f"{f['path']} {f['size']} {base64.b64decode(f['data']).decode()[:19]}" for f in got) or "no files"}]}
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
        reply = mcp_reply(msg, "http", state)
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
    the tree entry has a "header"; Range requests get their part. A repo with
    "gated" (0.61.0: FLUX.2 klein 9B's) is listed to anyone, as the real one is,
    but its files are served only with "Authorization: Bearer <its token>" -
    401 without one, 403 with another."""
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
        return {"_id": "x", "id": f"{owner}/{name}", "gguf": r.get("gguf") or {}, "gated": r.get("gated") or False,
                **({"sha": r["sha"]} if r.get("sha") else {})}

    @app.get("/{owner}/{name}/resolve/main/{path:path}")
    async def resolve(owner: str, name: str, path: str, request: Request):
        r = repo_of(owner, name) or {}
        f = next((x for x in r.get("tree", []) if x.get("path") == path), None)
        if f is None:
            return PlainTextResponse("Entry not found", status_code=404)
        if r.get("gated"):
            auth = request.headers.get("authorization") or ""
            if not auth:
                return PlainTextResponse(f"Access to model {owner}/{name} is restricted. Please log in.",
                                         status_code=401)
            if auth != f"Bearer {r.get('token')}":
                return PlainTextResponse(f"Access to model {owner}/{name} is restricted and you are not in the "
                                         "authorized list.", status_code=403)
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


def make_trainer(state: dict) -> FastAPI:
    """The LoRA trainer (services/trainer/traind.py, 0.50.0): a job writes two checkpoints
    and a sample picture into the run's folder (state["root"] is data/training, which the trainer sees as
    /training) and is done on the second look."""
    import json as _json
    import struct as _struct
    from pathlib import Path as _Path

    app = FastAPI()
    state.setdefault("jobs", {})

    def lora_bytes() -> bytes:
        raw = _json.dumps({"diffusion_model.single_blocks.0.linear1.lora_A.weight":
                           {"dtype": "F16", "shape": [16, 3072], "data_offsets": [0, 0]}}).encode()
        return _struct.pack("<Q", len(raw)) + raw

    @app.get("/health")
    async def health():
        if state.get("down"):
            return JSONResponse({"error": "down"}, status_code=503)
        if state.get("busy_looks"):               # a job from before a restart, stopping
            state["busy_looks"] -= 1
            return {"ok": True, "busy": True, "job": state.get("busy_job"), "toolkit": True, "ref": "test"}
        return {"ok": True, "busy": False, "job": None, "toolkit": True, "ref": "test"}

    @app.post("/jobs")
    async def job(request: Request):
        """Like ai-toolkit: it goes on from the newest numbered checkpoint in the folder (and its optimizer.pt), saves
        one every 250 steps, and the last as <id>.safetensors when it is done."""
        body = await request.json()
        rid = body["id"]
        steps = body["config"]["config"]["process"][0]["train"]["steps"]
        out = _Path(state["root"]) / rid / "output" / rid
        (out / "samples").mkdir(parents=True, exist_ok=True)
        done = [int(f.stem.rsplit("_", 1)[1]) for f in out.glob(f"{rid}_*.safetensors")]
        start = max(done, default=0)
        state["jobs"][rid] = {"config": body["config"], "dir": body["dir"], "looks": 0, "cancelled": False,
                              "start": start, "optimizer": (out / "optimizer.pt").is_file(), "steps": steps}
        for step in range(start + 250 - start % 250, steps, 250):
            (out / f"{rid}_{step:09d}.safetensors").write_bytes(lora_bytes())
            (out / "samples" / f"{1761700000000 + step}__{step:09d}_0.jpg").write_bytes(_png((64, 64)))
        (out / "optimizer.pt").write_bytes(b"o" * 1000)             # ai-toolkit's state for resuming
        return {"ok": True}

    @app.get("/jobs/{rid}")
    async def look(rid: str):
        if state.get("forget"):                  # the trainer restarted: its jobs are gone
            return JSONResponse({"error": "no such job"}, status_code=404)
        j = state["jobs"][rid]
        j["looks"] += 1
        step, total = min(j["start"] + 250, j["steps"]), j["steps"]
        if j["cancelled"]:                    # like ai-toolkit: a while to stop and let go of the card
            j["stopping"] = j.get("stopping", 0) + 1
            return {"state": "stopping" if j["stopping"] < 3 else "cancelled", "step": step, "total": total, "log": []}
        if j["looks"] < state.get("looks_to_finish", 2) or state.get("hold"):
            return {"state": "running", "step": step, "total": total, "loss": 0.31, "eta_s": 600, "log": ["caching"]}
        out = _Path(state["root"]) / rid / "output" / rid
        if out.is_dir():
            (out / f"{rid}.safetensors").write_bytes(lora_bytes())
        return {"state": "done", "step": total, "total": total, "loss": 0.29, "log": ["Done"]}

    @app.post("/jobs/{rid}/cancel")
    async def cancel(rid: str):
        if rid in state["jobs"]:
            state["jobs"][rid]["cancelled"] = True
        state.setdefault("cancels", []).append(rid)
        return {"ok": True}

    return app
