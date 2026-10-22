"""Run the Hub against the fake services, for UI work without a GPU.

    cd backend && python -m tests.devserver [--port 8080] [--data /tmp/hub-dev]

Signs in as soren / "correct horse battery". The fake model answers by
keyword: "files" (writes and runs a script), "page" (makes an artifact),
"think" (reasons first), "search" (web search), "picture" (an image), or
anything else (a short markdown answer). The built UI (frontend/dist) is
served when it exists; otherwise use `npm run dev` in frontend/.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import uvicorn

from app import auth
from app.config import Env, _networks
from app.main import create_app
from app.state import Transports

from .conftest import _install_fake_models
from .mocks import HostRouter, make_comfy, make_hf, make_llama, make_mcp_http, make_sandbox, make_searxng, make_supervisor

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data", default="")
    a = ap.parse_args()
    base = Path(a.data or tempfile.mkdtemp(prefix="hub-dev-"))
    data, models = base / "data", base / "models"
    _install_fake_models(models, {"qwen3.8-27b", "qwen3.6-35b-a3b", "embed-text", "rerank", "embed-vl",
                                  "qwen-image-2.1", "qwen-image-2.1-turbo", "ernie-image-turbo"})
    dist = ROOT / "frontend" / "dist"
    env = Env(data_dir=data, models_dir=models, llm_url="http://supervisor", llm_host="llm", comfy_url="http://comfy",
              searxng_url="http://searxng", sandbox_url="http://sandbox:7000", sandbox_ip="172.31.250.20",
              internal_token="tok", secret_key="dev", public_url="", trusted_proxies=(),
              lan_networks=tuple(_networks(["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"])),
              admin_user="soren", admin_password_hash=auth.hash_password("correct horse battery", n=2**10),
              static_dir=dist if (dist / "index.html").is_file() else None)
    fakes = {"sup": {}, "llama": {"responder": responder}, "comfy": {}, "sandbox": {}, "mcp": {}}
    router = HostRouter({"supervisor": make_supervisor(fakes["sup"]), "llm": make_llama(fakes["llama"]),
                         "comfy": make_comfy(fakes["comfy"]), "sandbox:7000": make_sandbox(fakes["sandbox"]),
                         "searxng": make_searxng(), "mcp.test": make_mcp_http(fakes["mcp"]),
                         "huggingface.co": make_hf(HF_DEMO)})
    t = Transports(llm=router, runner=router, comfy=router, sandbox=router, web=router)
    app = create_app(env, t, startup=False)
    print(f"SLQ Hub (fake services) on http://127.0.0.1:{a.port}  data: {base}")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
