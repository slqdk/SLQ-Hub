"""Libraries: indexing text and page images, fused search with re-ranking,
the agent's library_search tool."""
from __future__ import annotations

import asyncio
import json
import time

import pymupdf
import pytest

from .mocks import _png
from .test_agent import finish, new_chat, say


def _pdf() -> bytes:
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "The circulation pump runs at 1450 rpm.\nCheck the pump bearing yearly.")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "Valve maintenance: replace the seals every six months.")
    doc.new_page()                                    # a page with no text at all
    return doc.tobytes()


@pytest.fixture
def vl_installed(app):
    hub = app.state.hub
    for f in hub.catalog.get("aux", "embed-vl")["files"]:
        p = hub.env.models_dir / f["dest"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake")


async def _ready(client, lib_id: int, n: int, timeout: float = 15) -> list[dict]:
    t0 = time.monotonic()
    while True:
        docs = (await client.get(f"/hub/api/libraries/{lib_id}/docs")).json()
        if len(docs) == n and all(d["status"] in ("ready", "error") for d in docs):
            return docs
        assert time.monotonic() - t0 < timeout, docs
        await asyncio.sleep(0.02)


async def _library(client) -> int:
    r = await client.post("/hub/api/libraries", json={"name": "Manuals", "description": "Plant manuals"})
    assert r.status_code == 200, r.text
    lib = r.json()["id"]
    files = [("files", ("manual.pdf", _pdf(), "application/pdf")),
             ("files", ("notes.md", b"# Notes\nThe heat exchanger is cleaned in spring.", "text/markdown")),
             ("files", ("red.png", _png((40, 30), (230, 10, 10, 255)), "image/png")),
             ("files", ("green.png", _png((40, 30), (10, 230, 10, 255)), "image/png")),
             ("files", ("tool.exe", b"MZ...", "application/octet-stream"))]
    r = (await client.post(f"/hub/api/libraries/{lib}/docs", files=files)).json()
    assert [d["filename"] for d in r["added"]] == ["manual.pdf", "notes.md", "red.png", "green.png"]
    assert r["refused"][0]["filename"] == "tool.exe"
    return lib


async def test_index_and_search(signed_in, app, fakes, vl_installed):
    lib = await _library(signed_in)
    docs = {d["filename"]: d for d in await _ready(signed_in, lib, 4)}
    assert all(d["status"] == "ready" for d in docs.values()), docs
    assert docs["manual.pdf"]["pages"] == 3 and docs["manual.pdf"]["chunks"] == 2
    assert docs["red.png"]["chunks"] == 0 and docs["red.png"]["pages"] == 1
    listed = (await signed_in.get("/hub/api/libraries")).json()
    assert listed["available"] and listed["libraries"][0]["ready"] == 4

    hits = (await signed_in.get(f"/hub/api/libraries/{lib}/search", params={"q": "pump bearing rpm"})).json()
    assert hits[0]["filename"] == "manual.pdf" and hits[0]["page"] == 1 and "1450 rpm" in hits[0]["text"]
    assert hits[0]["image"]                           # its page image is there for the model

    hits = (await signed_in.get(f"/hub/api/libraries/{lib}/search", params={"q": "red"})).json()
    top = hits[0]
    assert top["filename"] == "red.png" and top["via"] == ["page"] and top["text"] == ""

    # The query went to both embedders in their own formats.
    embedded = fakes.llama["embedded"]
    assert any(isinstance(x, str) and x.startswith("Instruct: ") and x.endswith("Query:pump bearing rpm") for x in embedded)
    vl = [x for x in embedded if isinstance(x, dict)]
    assert any(p.get("type") == "image_url" for x in vl for p in x["content"])
    assert any("Retrieve document pages" in x["content"][0]["text"] for x in vl)
    starts = {s["name"] for s in fakes.sup["starts"]}
    assert {"embed-text", "embed-vl", "rerank"} <= starts and "embed-gpu" not in starts


async def test_documents_are_replaced_and_removed(signed_in, app, fakes, vl_installed):
    lib = await _library(signed_in)
    await _ready(signed_in, lib, 4)
    r = await signed_in.post(f"/hub/api/libraries/{lib}/docs",
                             files=[("files", ("notes.md", b"The boiler is serviced in autumn.", "text/markdown"))])
    assert r.json()["added"][0]["status"] == "queued"
    docs = {d["filename"]: d for d in await _ready(signed_in, lib, 4)}
    hits = (await signed_in.get(f"/hub/api/libraries/{lib}/search", params={"q": "boiler serviced autumn"})).json()
    assert hits[0]["filename"] == "notes.md" and "boiler" in hits[0]["text"]
    assert (await signed_in.delete(f"/hub/api/libraries/{lib}/docs/{docs['notes.md']['id']}")).status_code == 200
    hits = (await signed_in.get(f"/hub/api/libraries/{lib}/search", params={"q": "boiler serviced autumn"})).json()
    assert all(h["filename"] != "notes.md" for h in hits)


async def test_the_agent_searches_its_libraries(signed_in, app, fakes, vl_installed):
    lib = await _library(signed_in)
    await _ready(signed_in, lib, 4)
    c = await new_chat(signed_in)
    fakes.llama["replies"] = [{"content": "no tools yet"}, {"content": "T"}]
    await finish(app, await say(signed_in, c["id"], "hi"))
    assert "library_search" not in {t["function"]["name"] for t in fakes.llama["requests"][0]["tools"]}

    await signed_in.patch(f"/hub/api/chats/{c['id']}", json={"settings": {"libraries": [lib]}})
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": "library_search", "arguments": {"query": "red"}}]},
                              {"content": "The red picture (red.png, page 1)."}]
    run = await finish(app, await say(signed_in, c["id"], "find the red one"))
    assert run.status == "done", run.events[-1]
    result = next(e for e in run.events if e["type"] == "tool_result")["data"]
    assert result["text"].startswith("[1] red.png, page 1")
    assert result["images"] and result["display"]["library_hits"][0]["filename"] == "red.png"
    follow = fakes.llama["requests"][1]["messages"]
    assert follow[-1]["role"] == "user" and follow[-1]["content"][1]["type"] == "image_url"


async def test_page_images_can_be_indexed_on_the_gpu(signed_in, app, fakes, vl_installed):
    app.state.hub.settings.update("libraries", {"page_images": "gpu"})
    r = await signed_in.post("/hub/api/libraries", json={"name": "Pictures"})
    lib = r.json()["id"]
    await signed_in.post(f"/hub/api/libraries/{lib}/docs",
                         files=[("files", ("red.png", _png((40, 30), (230, 10, 10, 255)), "image/png"))])
    docs = await _ready(signed_in, lib, 1)
    assert docs[0]["status"] == "ready", docs
    starts = [s["name"] for s in fakes.sup["starts"]]
    assert "embed-gpu" in starts and "embed-vl" not in starts
    gpu_args = next(s for s in fakes.sup["starts"] if s["name"] == "embed-gpu")["args"]
    assert "--device" not in gpu_args and gpu_args[-2:] == ["-ngl", "all"]
    sched = app.state.hub.scheduler
    await sched.drain()
    assert sched.current == "embedgpu:embed-vl" and not sched.running


async def test_a_broken_document_is_marked(signed_in, app, fakes):
    r = await signed_in.post("/hub/api/libraries", json={"name": "Bad"})
    lib = r.json()["id"]
    await signed_in.post(f"/hub/api/libraries/{lib}/docs", files=[("files", ("broken.pdf", b"%PDF-nope", "application/pdf"))])
    docs = await _ready(signed_in, lib, 1)
    assert docs[0]["status"] == "error" and "PDF" in docs[0]["error"]
    meta = json.dumps(docs[0])
    assert "broken.pdf" in meta
