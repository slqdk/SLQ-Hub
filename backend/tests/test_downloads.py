"""The download queue (Settings -> Models): Cancel stops one model's files and the queue goes on (0.26.0: before,
cancelling the file being downloaded ended the queue, and the next model's files waited for ever - and an update
waiting for the Hub to be free with them)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.downloads import Downloads

CHUNK = 512 * 1024          # the queue reads 4 MiB at a time: a file of three of those


class Slow(httpx.AsyncByteStream):
    def __init__(self, chunks: int):
        self.chunks = chunks

    async def __aiter__(self):
        for _ in range(self.chunks):
            await asyncio.sleep(0.01)
            yield b"g" * CHUNK


class Catalog:
    def __init__(self, root: Path):
        self.root = root
        self.entries = {m: {"id": m, "files": [{"repo": f"acme/{m}", "path": f"{m}.gguf", "dest": f"llm/{m}.gguf",
                                                "size": 24 * CHUNK}]} for m in ("big", "small")}

    def get(self, group: str, entry_id: str):
        return self.entries.get(entry_id) if group == "llm" else None

    def path(self, f: dict) -> Path:
        return self.root / f["dest"]


async def test_cancel_stops_one_model_and_the_queue_goes_on(tmp_path):
    asked = []

    async def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, headers={"content-length": str(24 * CHUNK)}, stream=Slow(24))
    cat = Catalog(tmp_path)
    d = Downloads(cat, transport=httpx.MockTransport(handler))
    d.enqueue("big")
    d.enqueue("small")
    for _ in range(200):
        if d.state["llm/big.gguf"]["done"]:
            break
        await asyncio.sleep(0.01)
    d.cancel("big")
    for _ in range(500):
        if not d.snapshot()["active"]:
            break
        await asyncio.sleep(0.01)
    assert d.state["llm/big.gguf"]["status"] == "cancelled"
    assert d.state["llm/small.gguf"]["status"] == "done" and cat.path(cat.entries["small"]["files"][0]).is_file()
    # asked for again, it downloads (an earlier Cancel does not linger)
    d.enqueue("big")
    for _ in range(500):
        if not d.snapshot()["active"]:
            break
        await asyncio.sleep(0.01)
    assert d.state["llm/big.gguf"]["status"] == "done" and cat.path(cat.entries["big"]["files"][0]).is_file()
