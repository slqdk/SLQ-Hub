"""How much one chat may use (0.26.0): Settings -> Models sets the context per chat next to the context; raising the
context alone gave a chat no more room (chat 74 ran with 128,000 and was cut at 65,536)."""
from __future__ import annotations

import json
import struct

from app import gguf

MODEL = "qwen3.8-27b"


def _s(text: str) -> bytes:
    b = text.encode()
    return struct.pack("<Q", len(b)) + b


def header(arch: str, ctx: int | None, wide: bool = False) -> bytes:
    kv = _s("general.architecture") + struct.pack("<I", 8) + _s(arch)
    kv += _s("general.tags") + struct.pack("<I", 9) + struct.pack("<I", 8) + struct.pack("<Q", 2) + _s("a") + _s("bb")
    kv += _s(f"{arch}.block_count") + struct.pack("<I", 4) + struct.pack("<I", 64)
    n = 3
    if ctx is not None:
        kv += _s(f"{arch}.context_length") + (struct.pack("<I", 10) + struct.pack("<Q", ctx) if wide
                                              else struct.pack("<I", 4) + struct.pack("<I", ctx))
        n += 1
    return b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, n) + kv + b"\0" * 64


def test_the_trained_context_comes_from_the_file_header(tmp_path):
    (tmp_path / "a.gguf").write_bytes(header("qwen35", 262144))
    (tmp_path / "b.gguf").write_bytes(header("llama", 131072, wide=True))
    (tmp_path / "c.gguf").write_bytes(header("qwen35", None))
    (tmp_path / "d.gguf").write_bytes(b"fake")
    assert gguf.context_length(tmp_path / "a.gguf") == 262144
    assert gguf.context_length(tmp_path / "b.gguf") == 131072
    assert gguf.context_length(tmp_path / "c.gguf") is None
    assert gguf.context_length(tmp_path / "d.gguf") is None
    assert gguf.context_length(tmp_path / "missing.gguf") is None


async def listed(client) -> dict:
    return {e["id"]: e for e in (await client.get("/hub/api/models")).json()["llm"]}[MODEL]


def flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


async def test_the_context_per_chat_is_set_next_to_the_context(signed_in, app, api_key):
    hub = app.state.hub
    m = await listed(signed_in)
    assert m["context_per_chat"] == 65536 and m["context_native"] is None        # the fake file has no header
    put = lambda server: signed_in.put(f"/hub/api/models/{MODEL}", json={"server": server})   # noqa: E731

    r = await put({"ctx": 262144})
    assert r.status_code == 200, r.text
    assert hub.catalog.context_per_request(MODEL) == 65536                       # the card's, as before
    args = hub.catalog.server_args(MODEL)
    assert flag(args, "-c") == "262144" and flag(args, "--kv-unified-per-slot") == "65536"

    r = await put({"per_request": 245760})
    assert r.status_code == 200, r.text
    assert hub.catalog.context_per_request(MODEL) == 245760
    assert flag(hub.catalog.server_args(MODEL), "--kv-unified-per-slot") == "245760"
    assert (await listed(signed_in))["context_per_chat"] == 245760
    show = await signed_in.post("/api/show", json={"model": MODEL}, headers={"Authorization": f"Bearer {api_key}"})
    assert show.json()["model_info"]["qwen35.context_length"] == 245760          # what apps are told

    r = await put({"per_request": 262144})                                       # the whole shared context
    assert r.status_code == 200 and "--kv-unified-per-slot" not in hub.catalog.server_args(MODEL)

    r = await put({"per_request": 300000})
    assert r.status_code == 400 and "up to the context (262144 tokens)" in r.json()["detail"]
    r = await put({"ctx": 131072})                                               # below the context per chat
    assert r.status_code == 400 and "up to the context (131072 tokens)" in r.json()["detail"]
    assert (await put({"ctx": "big"})).status_code == 400
    assert (await put({"ctx": 1024})).status_code == 400
    assert hub.catalog.context_per_request(MODEL) == 262144                      # nothing changed by those

    r = await put({"per_request": 0})                                            # empty: the card's again
    assert r.status_code == 200 and hub.catalog.context_per_request(MODEL) == 65536
    saved = json.loads((hub.env.data_dir / "models.override.json").read_text())
    assert saved["llm"][MODEL]["server"] == {"ctx": 262144}

    r = await put({"parallel": 0})                                               # an emptied Slots field
    assert r.status_code == 400 and "at least one slot" in r.json()["detail"]
    assert (await put({"parallel": 1})).status_code == 200 and hub.catalog.slots(MODEL) == 1
    r = await put({"parallel": None, "ctx": None})                               # null: the card's again
    assert r.status_code == 200 and hub.catalog.slots(MODEL) == 2
    assert hub.catalog.get_llm(MODEL)["server"]["ctx"] == 81920
    assert flag(hub.catalog.server_args(MODEL), "-np") == "2"


async def test_a_smaller_context_caps_what_a_chat_may_use(signed_in, app):
    """The card says 65,536 per chat; with a context of 32,768 one chat cannot have more than that."""
    hub = app.state.hub
    r = await signed_in.put(f"/hub/api/models/{MODEL}", json={"server": {"ctx": 32768}})
    assert r.status_code == 200, r.text
    assert hub.catalog.context_per_request(MODEL) == 32768
    assert "--kv-unified-per-slot" not in hub.catalog.server_args(MODEL)
    r = await signed_in.put(f"/hub/api/models/{MODEL}", json={"server": {"ctx": 65536, "kv_unified": False}})
    assert r.status_code == 200 and hub.catalog.context_per_request(MODEL) == 32768     # two slots, each half
