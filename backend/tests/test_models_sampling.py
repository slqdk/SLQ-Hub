"""Sampling for models added from Hugging Face: their family's
recommendation (read from the GGUF file when the entry has no architecture),
and flags set in Settings win over it."""
from __future__ import annotations

import json
import struct

from app import gguf
from app.catalog import Catalog, family_sampling
from app.llm import payload as P


def _s(text: str) -> bytes:
    b = text.encode()
    return struct.pack("<Q", len(b)) + b


def gguf_header(arch: str, before: bool = True) -> bytes:
    kv, n = b"", 0
    if before:      # a number and a list of strings before the architecture, which the reader steps over
        kv += _s("general.file_type") + struct.pack("<I", 4) + struct.pack("<I", 7)
        kv += _s("general.tags") + struct.pack("<I", 9) + struct.pack("<I", 8) + struct.pack("<Q", 2) + _s("a") + _s("bb")
        n += 2
    kv += _s("general.architecture") + struct.pack("<I", 8) + _s(arch)
    return b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, n + 1) + kv + b"\0" * 64


def test_the_architecture_comes_from_the_file_header(tmp_path):
    (tmp_path / "a.gguf").write_bytes(gguf_header("qwen35"))
    (tmp_path / "b.gguf").write_bytes(gguf_header("llama", before=False))
    (tmp_path / "c.gguf").write_bytes(b"not a model")
    assert gguf.architecture(tmp_path / "a.gguf") == "qwen35"
    assert gguf.architecture(tmp_path / "b.gguf") == "llama"
    assert gguf.architecture(tmp_path / "c.gguf") is None
    assert gguf.architecture(tmp_path / "missing.gguf") is None
    assert family_sampling("qwen35moe")["instruct"]["presence_penalty"] == 1.5
    assert family_sampling("gemma3") is None and family_sampling(None) is None


def test_a_qwen_fine_tune_gets_its_familys_sampling(tmp_path):
    models, data = tmp_path / "models", tmp_path / "data"
    data.mkdir()
    dest = "llm/hf/ornith-ai--Ornith-1.5-9B-GGUF/Ornith-1.5-9B-BF16.gguf"
    (models / dest).parent.mkdir(parents=True)
    (models / dest).write_bytes(gguf_header("qwen35"))
    base = {"files": [{"repo": "x/y", "path": "m.gguf", "dest": dest, "size": 10}],
            "server": {"ctx": 65536, "parallel": 2, "extra": []}, "context_per_request": 65536,
            "capabilities": ["chat", "tools", "thinking"], "thinking": {"default": False, "efforts": [], "default_effort": None}}
    (data / "models.custom.json").write_text(json.dumps({"llm": [
        {**base, "id": "ornith-1.5-9b", "name": "Ornith 1.5 9B"},                     # added before 0.9.1: no architecture
        {**base, "id": "some-llama", "name": "Some Llama", "architecture": "llama"},
        {**base, "id": "own", "name": "Own", "architecture": "qwen3", "sampling": {"instruct": {"temperature": 0.3}}}]}))
    cat = Catalog(models, data / "models.override.json")
    orn = cat.get_llm("ornith-1.5-9b")
    assert orn["architecture"] == "qwen35"
    body = P.build(orn, [{"role": "user", "content": "hej"}], thinking=False)
    assert (body["temperature"], body["top_p"], body["top_k"], body["presence_penalty"]) == (0.7, 0.8, 20, 1.5)
    body = P.build(orn, [{"role": "user", "content": "hej"}], thinking=True)
    assert (body["temperature"], body["top_p"], body["presence_penalty"]) == (1.0, 0.95, 1.5)
    assert "sampling" not in cat.get_llm("some-llama")                           # no family: llama-server decides
    assert cat.get_llm("own")["sampling"] == {"instruct": {"temperature": 0.3}}   # its own stays
    # Flags set under Settings -> Models win over the family's values.
    flagged = {**orn, "server": {**orn["server"], "extra": ["--temp", "0.6", "--presence-penalty=0"]}}
    body = P.build(flagged, [{"role": "user", "content": "hej"}], thinking=False)
    assert "temperature" not in body and "presence_penalty" not in body and body["top_k"] == 20


def test_idle_slots_stay_on_the_card_unless_a_model_asks_otherwise(app):
    """llama-server's default moves idle slots to its RAM cache and clears them whenever another request starts;
    a conversation too big for that cache was lost (chat 11). The Hub turns that off, unless a model's own extra
    flags say otherwise."""
    cat = app.state.hub.catalog
    assert "--no-cache-idle-slots" in cat.server_args("qwen3.8-27b")
    cat.save_override("qwen3.8-27b", server={"extra": ["--cache-idle-slots", "--cache-ram", "16384"]})
    args = cat.server_args("qwen3.8-27b")
    assert "--no-cache-idle-slots" not in args and args[-3:] == ["--cache-idle-slots", "--cache-ram", "16384"]
