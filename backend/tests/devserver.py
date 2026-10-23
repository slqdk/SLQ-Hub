"""Run the Hub against the fake services, for UI work without a GPU.

    cd backend && python -m tests.devserver [--port 8080] [--data /tmp/hub-dev]

Signs in as soren / "correct horse battery". The fake model answers by
keyword: "files" (writes and runs a script), "jingle" (a command makes a
sound, a PDF and notes: the card under the answer), "announce" (speech),
"song" (music), "horn" (a sound effect), "game" (slow steps, to try
sending a message while it works), "page" (makes an artifact),
"think" (reasons first), "search" (web search), "picture" (an image),
"loop" (repeats the same searches until the Hub makes it answer), or
anything else (a short markdown answer). The built UI (frontend/dist) is
served when it exists; otherwise use `npm run dev` in frontend/.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import struct
import tempfile
import wave
from pathlib import Path

import uvicorn

from app import auth
from app.config import Env, _networks
from app.main import create_app
from app.state import Transports

from .conftest import _install_fake_models
from .mocks import (HostRouter, float_wav, make_civitai, make_civitai_delivery, make_comfy, make_hf, make_llama,
                    make_mcp_http, make_sandbox, make_searxng, make_speech, make_supervisor, silent_mp3)

ROOT = Path(__file__).resolve().parents[2]

ANSWER = """Here is a short overview.

| Model | Size | Context |
|---|---|---|
| Qwen3.8 27B | 17 GB | 64K per request |
| Qwen3.6 35B-A3B | 18 GB | 64K |

The energy is $E = mc^2$, and for a sum:

$$\\sum_{k=1}^{n} k = \\frac{n(n+1)}{2}$$

```python
def fib(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
```

That is all - ask for more detail if you need it."""

PAGE = """<div class="p-8 max-w-xl mx-auto">
  <h1 class="text-3xl font-semibold mb-3">Pump status</h1>
  <p class="text-slate-600 mb-6">Live values from the plant (demo).</p>
  <div class="grid grid-cols-2 gap-4">
    <div class="rounded-lg border p-4"><div class="text-sm text-slate-500">Speed</div><div class="text-2xl">1450 rpm</div></div>
    <div class="rounded-lg border p-4"><div class="text-sm text-slate-500">Pressure</div><div class="text-2xl">3.2 bar</div></div>
  </div>
</div>"""


def responder(body: dict) -> dict:
    msgs = body["messages"]
    last_user = next((m for m in reversed(msgs) if m["role"] == "user"), {"content": ""})
    text = last_user["content"] if isinstance(last_user["content"], str) else " ".join(
        p.get("text", "") for p in last_user["content"])
    text = re.sub(r"^\[[^\]\n]{8,40}\]\n", "", text)          # the time it was sent
    after_tool = msgs[-1]["role"] == "tool" or (msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list)
                                                and "Images returned" in json.dumps(msgs[-1]["content"]))
    tools_done = sum(1 for m in msgs if m["role"] == "tool")
    low = text.lower()
    if "short title" in low:
        return {"content": text.split("starts with:")[-1].strip()[:40].title() or "Chat"}
    if "condense the conversation" in low:
        return {"content": "Summary of the chat so far."}
    if "files" in low:
        if tools_done == 0:
            return {"content": "I will write the script first.", "tool_calls": [{"name": "write_file", "arguments": {
                "path": "fib.py", "content": "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n"
                                             "    return a\n\nprint([fib(i) for i in range(10)])\n"}}]}
        if tools_done == 1:
            return {"tool_calls": [{"name": "bash", "arguments": {"command": "python fib.py"}}]}
        return {"content": "The script `fib.py` is in the chat's folder and prints the first ten Fibonacci numbers."}
    if "jingle" in low:
        if tools_done == 0:
            return {"content": "Making the sound with ffmpeg.", "tool_calls": [{"name": "bash", "arguments": {
                "command": "ffmpeg -f lavfi -i sine=440:d=1.2 jingle.wav && make-guide guide.pdf"}}]}
        if tools_done == 1:
            return {"tool_calls": [{"name": "write_file", "arguments": {
                "path": "notes/jingle.md", "content": "# Jingle\n\nA 440 Hz tone, 1.2 s.\n"}}]}
        return {"content": "Done: **jingle.wav** (1.2 s), a one-page **guide.pdf** and notes in `notes/jingle.md`."}
    if "announce" in low:
        if tools_done == 0:
            return {"content": "Making the announcement.", "tool_calls": [{"name": "speech_generate", "arguments": {
                "text": "Tredive sekunder tilbage af tredje periode!", "language": "da", "voice": "mic"}}]}
        return {"content": "Here it is: **tredive-sekunder-tilbage-af-tredje-periode.mp3**, read by Mic (Røst v3)."}
    if "song" in low:
        if tools_done == 0:
            return {"tool_calls": [{"name": "music_generate", "arguments": {
                "prompt": "upbeat Danish stadium rock, male vocals, crowd chants", "duration": 45, "bpm": 132,
                "key": "A minor", "language": "da", "save_as": "club-song.mp3",
                "lyrics": "[verse]\nVi spiller på is\n[chorus]\nJegerup, Jegerup!"}}]}
        return {"content": "The song is ready: **club-song.mp3** (45 s, 132 BPM)."}
    if "horn" in low:
        if tools_done == 0:
            return {"tool_calls": [{"name": "sound_generate", "arguments": {
                "prompt": "Ice hockey arena horn at the end of a period, loud, huge echoing hall", "duration": 3}}]}
        return {"content": "The horn: **ice-hockey-arena-horn-at-the-end-of-a-period.mp3** (3 s)."}
    if "page" in low:
        if tools_done == 0:
            return {"tool_calls": [{"name": "todo_write", "arguments": {"todos": [
                {"content": "Lay out the status page", "status": "completed"},
                {"content": "Add the values", "status": "in_progress"}]}}]}
        if tools_done == 1:
            return {"tool_calls": [{"name": "artifact", "arguments": {"action": "create", "id": "pump-status",
                                                                      "title": "Pump status", "type": "html",
                                                                      "content": PAGE}}]}
        return {"content": "The page is open on the right. Tell me what to change."}
    if "picture" in low:
        if tools_done == 0:
            return {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a red fox in the snow", "fast": True}}]}
        return {"content": "Here is the fox."}
    if low.startswith("(the user wrote this while you were working"):
        note = text.split("\n", 1)[-1].strip()
        return {"content": f'Noted: "{note[:80]}". I take that into account from here.', "delay": 1}
    if "game" in low:                                   # slow steps: try sending something while it works
        if tools_done == 0:
            return {"content": "I will write the game first.", "delay": 3, "tool_calls": [
                {"name": "write_file", "arguments": {"path": "game.js", "content": "console.log('jump')\n"}}]}
        if tools_done == 1:
            return {"delay": 3, "tool_calls": [{"name": "bash", "arguments": {"command": "node game.js"}}]}
        return {"content": "The game runs.", "delay": 2}
    asked = " ".join(m["content"] for m in msgs if m["role"] == "user" and isinstance(m["content"], str)).lower()
    if "loop" in asked:                                 # a small model stuck repeating itself (the Hub stops it)
        if "handed this turn over" in json.dumps(msgs[-1]):   # the stronger model it was handed over to
            return {"content": "Taking over: the XC60 diesel emits about 170 g CO2/km on the road; the Tesla's "
                               "share depends on the Danish grid. Sources follow."}
        if not body.get("tools"):
            return {"content": "I could not find the exact figure; here is what the two pages say."}
        return {"content": "I have the numbers now. Let me fetch the exact figures to be precise.",
                "tool_calls": [{"name": "web_search", "arguments": {"query": "volvo xc60 2.0 diesel co2"}},
                               {"name": "web_search", "arguments": {"query": "tesla model 3 kwh per 100 km"}}]}
    if "slow" in low:
        return {"content": "That took a while, but here it is.", "delay": 5}
    if "add numbers" in low:
        if tools_done == 0:
            return {"tool_calls": [{"name": "mcp__calc__add", "arguments": {"a": 2, "b": 3}}]}
        return {"content": "2 + 3 = 5, says the calculator."}
    if "You are a subagent" in msgs[0]["content"]:
        if tools_done == 0:
            return {"tool_calls": [{"name": "web_search", "arguments": {"query": "pump maintenance intervals"}}]}
        return {"content": "Report: pumps are serviced yearly; seals every six months."}
    if "research" in low:
        if tools_done == 0:
            return {"tool_calls": [{"name": "task", "arguments": {"description": "Find maintenance intervals",
                                                                   "prompt": "Find pump maintenance intervals."}}]}
        return {"content": "The subagent found it: yearly service, seals every six months."}
    if "search" in low:
        if tools_done == 0:
            return {"tool_calls": [{"name": "web_search", "arguments": {"query": text[:60]}}]}
        return {"content": "Two results came back; the first one looks most relevant.\n\nSources:\n- [Result](https://example.com/a)"}
    if "think" in low:
        return {"reasoning": "The user wants me to think. First I consider the question, then I check the numbers, "
                             "and then I write a short answer.", "content": "After thinking it over: **42**."}
    if after_tool:
        return {"content": "Done."}
    return {"content": ANSWER, "reasoning": "A quick look at what was asked."}


# A fake Hugging Face repository for Settings -> Models -> Add from Hugging Face
# (real-looking sizes; the "downloads" are 1 KB).
def _hf_file(path: str, gb: float) -> dict:
    return {"type": "file", "oid": "x", "size": int(gb * 1024**3), "path": path}


HF_DEMO = {"tiny_downloads": True, "repos": {"unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF": {
    "tree": [_hf_file(f"Qwen3-Coder-30B-A3B-Instruct-{q}.gguf", gb) for q, gb in
             (("UD-IQ2_XXS", 8.0), ("Q3_K_M", 13.7), ("Q4_K_M", 17.3), ("Q5_K_M", 20.2), ("Q6_K", 23.4), ("Q8_0", 30.3))]
            + [_hf_file("BF16/Qwen3-Coder-30B-A3B-Instruct-BF16-00001-of-00002.gguf", 46.5),
               _hf_file("BF16/Qwen3-Coder-30B-A3B-Instruct-BF16-00002-of-00002.gguf", 10.4),
               _hf_file("README.md", 0.00001)],
    "gguf": {"architecture": "qwen3moe", "context_length": 262144, "total": 30532122624,
             "chat_template": "{% if tools %}<tools>{% endif %}"}}}}


# Search ("Ornith 1.5 9B" in the dialog): the records the search API returns, and Ornith's own repository.
HF_DEMO["repos"]["ornith-ai/Ornith-1.5-9B-GGUF"] = {
    "tree": [_hf_file(f"Ornith-1.5-9B-{q}.gguf", gb) for q, gb in
             (("Q4_K_M", 5.38), ("Q5_K_M", 6.18), ("Q6_K", 7.04), ("Q8_0", 9.12), ("BF16", 17.14))]
            + [_hf_file("README.md", 0.00001)],
    "gguf": {"architecture": "qwen35", "context_length": 262144, "total": 9_200_000_000,
             "chat_template": "{% if tools %}<tools>{% endif %}{% if enable_thinking %}<think>{% endif %}"}}
HF_DEMO["listing"] = [
    {"id": "ornith-ai/Ornith-1.5-9B-GGUF", "downloads": 48210, "likes": 312, "tags": ["gguf"],
     "createdAt": "2026-09-30T10:00:00.000Z"},
    {"id": "bartowski/ornith-ai_Ornith-1.5-9B-GGUF", "downloads": 9120, "likes": 41, "tags": ["gguf"],
     "createdAt": "2026-10-01T08:00:00.000Z"},
    {"id": "ornith-ai/Ornith-1.5-27B-GGUF", "downloads": 20511, "likes": 140, "tags": ["gguf"],
     "createdAt": "2026-09-30T10:00:00.000Z"},
    {"id": "ornith-ai/Ornith-1.0-9B-GGUF", "downloads": 30122, "likes": 201, "tags": ["gguf"],
     "createdAt": "2026-07-02T10:00:00.000Z"},
    {"id": "ornith-ai/Ornith-1.5-9B", "downloads": 15000, "likes": 260, "tags": ["safetensors"],
     "createdAt": "2026-09-30T10:00:00.000Z"},
    {"id": "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", "downloads": 523000, "likes": 900, "tags": ["gguf"],
     "createdAt": "2025-08-01T10:00:00.000Z"},
]


# Picture models from a link: a Hugging Face repository with an SDXL and an SD 1.5 checkpoint, and a Civitai
# model with a Pony version and a Flux one (try https://civitai.com/models/257749 in Add image model).
_T = {"dtype": "F16", "shape": [4], "data_offsets": [0, 8]}
SDXL_HEADER = {"model.diffusion_model.input_blocks.0.0.weight": {**_T, "shape": [320, 4, 3, 3]},
               "model.diffusion_model.label_emb.0.0.weight": {**_T, "shape": [1280, 2816]},
               "conditioner.embedders.0.transformer.text_model.final_layer_norm.weight": _T,
               "conditioner.embedders.1.model.ln_final.weight": _T, "first_stage_model.decoder.conv_in.weight": _T}
SD15_HEADER = {"cond_stage_model.transformer.text_model.final_layer_norm.weight": _T,
               "model.diffusion_model.input_blocks.0.0.weight": {**_T, "shape": [320, 4, 3, 3]},
               "first_stage_model.decoder.conv_in.weight": _T}
HF_DEMO["repos"]["someone/sdxl-mirror"] = {"tree": [
    {**_hf_file("juggernautXL_v10.safetensors", 6.6), "header": SDXL_HEADER},
    {**_hf_file("sd15/realisticVision.safetensors", 2.0), "header": SD15_HEADER}, _hf_file("README.md", 0.00001)]}


def _civitai_version(vid: int, name: str, base: str) -> dict:
    return {"id": vid, "name": name, "baseModel": base, "files": [
        {"id": vid * 10, "name": f"{name}.safetensors", "sizeKB": 6_775_430.0, "type": "Model", "primary": True,
         "metadata": {"format": "SafeTensor", "size": "pruned", "fp": "fp16"}, "hashes": {"SHA256": "AB12"},
         "downloadUrl": f"https://civitai.com/api/download/models/{vid}?fileId={vid * 10}"}]}


CIVITAI_DEMO = {"models": {257749: {"id": 257749, "name": "Pony Diffusion V6 XL", "type": "Checkpoint", "nsfw": True,
                                    "creator": {"username": "PurpleSmartAI"},
                                    "modelVersions": [_civitai_version(290640, "V6 (start with this one)", "Pony"),
                                                      _civitai_version(290641, "Flux port", "Flux.1 D")]}}}


def _tone(seconds: float = 1.2, hz: float = 440.0, rate: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * hz * i / rate)))
                               for i in range(int(seconds * rate))))
    return buf.getvalue()


def _voice(hz: float, seconds: float = 6.0, rate: int = 24000) -> bytes:
    """A voiced tone with harmonics, as 32-bit float like the Røst samples: the Hub reads its pitch to call the
    voice male or female."""
    return float_wav([0.09 * sum(math.sin(2 * math.pi * k * hz * i / rate) / k for k in range(1, 8))
                      for i in range(int(seconds * rate))], rate)


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>"
       b"endobj\n4 0 obj<</Length 44>>stream\nBT /F1 24 Tf 40 70 Td (Jingle guide) Tj ET\nendstream endobj\n"
       b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def sandbox_handler(workspace: Path):
    """The fake sandbox's commands: "jingle" makes real files in the folder, like ffmpeg would."""
    def handle(body: dict) -> dict:
        cmd = body.get("cmd") or ""
        folder = workspace / str(body.get("cwd") or "/workspace").removeprefix("/workspace").lstrip("/")
        if "jingle.wav" in cmd:
            (folder / "jingle.wav").write_bytes(_tone())
            (folder / "guide.pdf").write_bytes(PDF)
        return {"exit_code": 0, "stdout": f"ran: {cmd}\n", "stderr": "", "timed_out": False, "duration_ms": 5,
                "truncated": False}
    return handle


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data", default="")
    ap.add_argument("--public-url", default="", help="the address through the proxy, e.g. https://hub.example.test")
    ap.add_argument("--proxy", default="", help="treat this peer as the trusted proxy (X-Forwarded-For is believed)")
    ap.add_argument("--lan-host", default="", help="the Hub's LAN address, for the Apps page")
    a = ap.parse_args()
    base = Path(a.data or tempfile.mkdtemp(prefix="hub-dev-"))
    data, models = base / "data", base / "models"
    _install_fake_models(models, {"qwen3.8-27b", "qwen3.6-35b-a3b", "embed-text", "rerank", "embed-vl",
                                  "qwen-image-2.1", "qwen-image-2.1-turbo", "ernie-image-turbo", "z-image-turbo",
                                  "roest-v3", "ace-step-1.5-xl-turbo", "stable-audio-3-medium"})
    for name, hz in (("mic", 118), ("nic", 205)):              # the voices: a low one and a high one, as WAV
        (models / "speech" / "roest-v3" / "voices" / f"{name}.wav").write_bytes(_voice(hz))
    samples = data / "selftest"                                 # what selftest --speech leaves: Settings -> Sound
    samples.mkdir(parents=True, exist_ok=True)
    runs = (("best", 3.4, 19.6), ("faster", 3.3, 10.4), ("fastest", 3.2, 7.4))
    for setting, secs, _ in runs:
        (samples / f"speech-roest-v3-{setting}.mp3").write_bytes(silent_mp3(secs))
    (samples / "speech.json").write_text(json.dumps({
        "at": "2026-10-23 21:30", "cpu": "AMD Ryzen 9 5950X 16-Core Processor (avx2)", "threads": 10,
        "samples": [{"file": f"speech-roest-v3-{st}.mp3", "model": "roest-v3", "name": "Røst v3", "setting": st,
                     "text": "Og her kommer en sætning mere, så farten kan måles.", "seconds": secs, "compute": took,
                     "factor": round(took / secs, 2), "capped": False} for st, secs, took in runs]}))
    dist = ROOT / "frontend" / "dist"
    env = Env(data_dir=data, models_dir=models, llm_url="http://supervisor", llm_host="llm", comfy_url="http://comfy",
              searxng_url="http://searxng", sandbox_url="http://sandbox:7000", sandbox_ip="172.31.250.20",
              internal_token="tok", secret_key="dev", public_url=a.public_url,
              trusted_proxies=(a.proxy,) if a.proxy else (), lan_host=a.lan_host,
              lan_networks=tuple(_networks(["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"])),
              admin_user="soren", admin_password_hash=auth.hash_password("correct horse battery", n=2**10),
              static_dir=dist if (dist / "index.html").is_file() else None, speech_url="http://speech:7100")
    fakes = {"sup": {}, "llama": {"responder": responder}, "comfy": {"audio_seconds": 3.0},
             "sandbox": {"handler": sandbox_handler(data / "workspace")}, "mcp": {}, "speech": {}}
    router = HostRouter({"supervisor": make_supervisor(fakes["sup"]), "llm": make_llama(fakes["llama"]),
                         "comfy": make_comfy(fakes["comfy"]), "sandbox:7000": make_sandbox(fakes["sandbox"]),
                         "searxng": make_searxng(), "mcp.test": make_mcp_http(fakes["mcp"]),
                         "huggingface.co": make_hf(HF_DEMO), "civitai.com": make_civitai(CIVITAI_DEMO),
                         "delivery.civitai.test": make_civitai_delivery(CIVITAI_DEMO),
                         "speech:7100": make_speech(fakes["speech"])})
    t = Transports(llm=router, runner=router, comfy=router, sandbox=router, web=router, speech=router)
    app = create_app(env, t, startup=False)
    print(f"SLQ Hub (fake services) on http://127.0.0.1:{a.port}  data: {base}")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning", proxy_headers=False)   # as in production


if __name__ == "__main__":
    main()
