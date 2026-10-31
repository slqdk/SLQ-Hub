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


async def test_a_gated_file_says_what_to_do(tmp_path, monkeypatch):
    """0.61.0: FLUX.2 klein 9B's model file is gated on Hugging Face - 401 without HF_TOKEN, 403 when the token's
    account has not accepted the licence. The download fails with the steps to take, not a bare status code."""
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(401 if not request.headers.get("authorization") else 403)
    cat = Catalog(tmp_path)
    for token, wanted in ((None, "is gated: sign in on Hugging Face"), ("hf_x", "accept its licence at huggingface.co")):
        if token:
            monkeypatch.setenv("HF_TOKEN", token)
        else:
            monkeypatch.delenv("HF_TOKEN", raising=False)
        d = Downloads(cat, transport=httpx.MockTransport(handler))
        d.enqueue("big")
        for _ in range(300):
            if not d.snapshot()["active"]:
                break
            await asyncio.sleep(0.01)
        st = d.state["llm/big.gguf"]
        assert st["status"] == "failed" and wanted in st["error"] and "acme/big" in st["error"], st
        assert len(st["error"]) <= 300
    assert seen == [None, "Bearer hf_x"]
    from app.downloads import hf_login                       # a token Hugging Face does not take: 401 with a token
    assert "did not take the token" in hf_login("acme/big", 401) and len(hf_login("acme/big", 401)) <= 300


async def test_a_file_a_training_fetched_is_linked_not_downloaded(tmp_path):
    """0.63.0: FLUX.2 [klein] 9B uncensored's 16.4 GB text encoder is also what a training on klein 9B uncensored
    fetches into Hugging Face's cache. A copy there at exactly the file's size becomes the model's file by a hard link
    - one file on disk, no download; one of another size is not taken."""
    asked = []

    async def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, headers={"content-length": str(24 * CHUNK)}, stream=Slow(24))
    cat = Catalog(tmp_path)
    cached = tmp_path / "hf/hub/models--acme--big/snapshots/0a1b2c/big.gguf"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"g" * (24 * CHUNK))
    (tmp_path / "llm").mkdir()
    (tmp_path / "llm" / "big.gguf.part").write_bytes(b"g" * 10)             # a download begun before
    d = Downloads(cat, transport=httpx.MockTransport(handler))
    d.enqueue("big")
    for _ in range(300):
        if not d.snapshot()["active"]:
            break
        await asyncio.sleep(0.01)
    dest = cat.path(cat.entries["big"]["files"][0])
    assert d.state["llm/big.gguf"]["status"] == "done" and asked == []
    assert dest.stat().st_ino == cached.stat().st_ino and dest.stat().st_nlink == 2
    assert not dest.with_name("big.gguf.part").exists() and d.state["llm/big.gguf"]["done"] == 24 * CHUNK

    other = tmp_path / "hf/hub/models--acme--small/snapshots/0a1b2c/small.gguf"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"g" * 100)                                         # not the file: downloaded as before
    d.enqueue("small")
    for _ in range(500):
        if not d.snapshot()["active"]:
            break
        await asyncio.sleep(0.01)
    small = cat.path(cat.entries["small"]["files"][0])
    assert asked == ["/acme/small/resolve/main/small.gguf"] and small.stat().st_nlink == 1


async def test_a_cached_file_in_hugging_faces_own_layout_is_linked_to_its_data(tmp_path):
    """0.63.0 (found by a review): a cache filled by Hugging Face's own tools keeps the data in blobs/ and a relative
    symlink in the snapshot. Linux hard-links a symlink itself, which in another folder points nowhere - the data is
    linked instead."""
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("downloaded")
    cat = Catalog(tmp_path)
    repo = tmp_path / "hf/hub/models--acme--big"
    (repo / "blobs").mkdir(parents=True)
    (repo / "blobs" / "9f8e").write_bytes(b"g" * (24 * CHUNK))
    (repo / "snapshots/0a1b2c").mkdir(parents=True)
    (repo / "snapshots/0a1b2c/big.gguf").symlink_to("../../blobs/9f8e")
    d = Downloads(cat, transport=httpx.MockTransport(handler))
    d.enqueue("big")
    for _ in range(300):
        if not d.snapshot()["active"]:
            break
        await asyncio.sleep(0.01)
    dest = cat.path(cat.entries["big"]["files"][0])
    assert d.state["llm/big.gguf"]["status"] == "done" and not dest.is_symlink()
    assert dest.stat().st_ino == (repo / "blobs" / "9f8e").stat().st_ino
