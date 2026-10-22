"""Adding language models from Hugging Face (Settings -> Models): repository
lookups, grouping GGUF files into choices, the catalog entry, the download
through the Hub's queue, the llama-server command line, and removal."""
from __future__ import annotations

import asyncio
import json
import shutil

import pytest

from app import hf
from app.catalog import Catalog

TEMPLATE = "{%- if tools %}<tools>{% endif %}{%- if enable_thinking is defined %}<think>{% endif %}"


def _f(path: str, size: int) -> dict:
    return {"type": "file", "oid": "x", "size": size, "path": path}


# The shape of unsloth/Qwen3.8-27B-GGUF's tree (sizes shortened).
REAL_TREE = [
    {"type": "directory", "oid": "a", "size": 0, "path": "BF16"},
    {"type": "directory", "oid": "b", "size": 0, "path": "MTP"},
    _f(".gitattributes", 4175),
    _f("BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf", 49986),
    _f("BF16/Qwen3.8-27B-BF16-00002-of-00002.gguf", 4671),
    _f("MTP/mtp-Qwen3.8-27B-Q4_0.gguf", 1369),
    _f("Qwen3.8-27B-Q8_0.gguf", 29047),
    _f("Qwen3.8-27B-UD-IQ1_M.gguf", 6729),
    _f("Qwen3.8-27B-UD-Q4_K_M.gguf", 16464),
    {"type": "file", "oid": "y", "size": 135, "path": "Qwen3.8-27B-UD-Q5_K_M.gguf",
     "lfs": {"oid": "z", "size": 19771, "pointerSize": 135}},
    _f("README.md", 7455),
    _f("imatrix_unsloth.gguf", 136),
    _f("mmproj-BF16.gguf", 931),
    _f("mmproj-F16.gguf", 927),
    _f("broken/Half-00001-of-00003.gguf", 10),         # an incomplete split
    _f("broken/Half-00003-of-00003.gguf", 10),
]


def test_repo_names_and_links():
    assert hf.parse_repo("unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF") == "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF"
    assert hf.parse_repo(" https://huggingface.co/bartowski/Q-GGUF/blob/main/q.gguf?x=1 ") == "bartowski/Q-GGUF"
    assert hf.parse_repo("hf.co/ggml-org/gemma-3-4b-it-GGUF") == "ggml-org/gemma-3-4b-it-GGUF"
    for bad in ("", "nope", "https://huggingface.co/datasets/a/b", "a/../b", "/x", "a b/c"):
        with pytest.raises(hf.HFError):
            hf.parse_repo(bad)
    assert hf.safe_path("BF16/x-00001-of-00002.gguf") and not hf.safe_path("../x.gguf")
    assert not hf.safe_path("a//b.gguf") and not hf.safe_path(".hidden/x.gguf")


def test_quant_labels():
    cases = {"Qwen3.8-27B-UD-Q4_K_M": "UD-Q4_K_M", "Qwen3.8-27B-Q8_0": "Q8_0", "Qwen3.8-27B-UD-IQ1_M": "UD-IQ1_M",
             "gpt-oss-20b-MXFP4": "MXFP4", "phi-4-mini-instruct.Q4_K_M": "Q4_K_M", "gemma-3-12b-it-qat-q4_0": "q4_0",
             "DeepSeek-R1-0528-Qwen3-8B-Q4_K_M": "Q4_K_M", "Qwen3.8-27B-BF16": "BF16", "plain-model": "plain-model"}
    for stem, label in cases.items():
        assert hf.quant_label(stem) == label, stem


def test_grouping_a_real_repository_tree():
    choices, mmproj = hf.group(REAL_TREE)
    labels = [c["label"] for c in choices]
    assert labels == ["UD-IQ1_M", "UD-Q4_K_M", "UD-Q5_K_M", "Q8_0", "BF16"]       # by size; no MTP, imatrix, mmproj
    bf16 = choices[-1]
    assert bf16["split"] and [f["path"] for f in bf16["files"]] == [
        "BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf", "BF16/Qwen3.8-27B-BF16-00002-of-00002.gguf"]
    assert bf16["size"] == 49986 + 4671 and bf16["key"] == bf16["files"][0]["path"]
    assert next(c for c in choices if c["label"] == "UD-Q5_K_M")["size"] == 19771      # the LFS size, not the pointer
    assert [(m["label"], m["size"]) for m in mmproj] == [("F16", 927), ("BF16", 931)]
    assert hf.suggest("unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF") == {
        "id": "qwen3-coder-30b-a3b-instruct", "name": "Qwen3 Coder 30B A3B Instruct"}


def _repo(fakes, name="acme/Tiny-Coder-GGUF", gguf=None):
    fakes.hf.setdefault("repos", {})[name] = {
        "tree": [_f("Tiny-Coder-Q4_K_M.gguf", 1000), _f("Tiny-Coder-Q8_0.gguf", 2000),
                 _f("split/Tiny-Coder-BF16-00001-of-00002.gguf", 1500),
                 _f("split/Tiny-Coder-BF16-00002-of-00002.gguf", 1500),
                 _f("mmproj-F16.gguf", 300), _f("README.md", 50)],
        "gguf": gguf if gguf is not None else {"architecture": "qwen3", "context_length": 40960,
                                               "total": 1_540_000_000, "chat_template": TEMPLATE}}


async def _drain(hub, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if not hub.extras["downloads"].snapshot()["active"]:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("downloads did not finish")


async def test_lookup_add_download_and_remove(signed_in, app, fakes, api_key):
    hub = app.state.hub
    _repo(fakes)
    r = await signed_in.get("/hub/api/models/hf", params={"repo": "https://huggingface.co/acme/Tiny-Coder-GGUF"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert [c["label"] for c in res["choices"]] == ["Q4_K_M", "Q8_0", "BF16"]
    assert res["mmproj"][0]["path"] == "mmproj-F16.gguf"
    assert res["meta"]["tools"] is True and res["meta"]["thinking"] is True and res["meta"]["context_length"] == 40960
    assert res["suggested"] == {"id": "tiny-coder", "name": "Tiny Coder"} and res["freeDisk"] > 0

    body = {"repo": "acme/Tiny-Coder-GGUF", "file": "Tiny-Coder-Q4_K_M.gguf", "mmproj": "mmproj-F16.gguf",
            "id": "tiny-coder", "name": "Tiny Coder", "ctx": 999999, "tools": True, "thinking": True}
    r = await signed_in.post("/hub/api/models/hf", json=body)
    assert r.status_code == 200, r.text
    m = r.json()["model"]
    assert m["custom"] and m["server"]["ctx"] == 40960 and m["context_per_request"] == 40960   # capped
    assert set(m["capabilities"]) == {"chat", "vision", "tools", "thinking"} and m["parameter_size"] == "1.5B"
    assert all(f["dest"].startswith("llm/hf/acme--Tiny-Coder-GGUF/") for f in m["files"])
    await _drain(hub)
    assert hub.catalog.installed(hub.catalog.get_llm("tiny-coder"))
    assert sorted(fakes.hf["downloaded"]) == ["acme/Tiny-Coder-GGUF/Tiny-Coder-Q4_K_M.gguf",
                                              "acme/Tiny-Coder-GGUF/mmproj-F16.gguf"]
    listed = {e["id"]: e for e in (await signed_in.get("/hub/api/models")).json()["llm"]}
    assert listed["tiny-coder"]["installed"] and listed["tiny-coder"]["custom"]

    args = hub.catalog.server_args("tiny-coder")
    assert args[:4] == ["-m", "/models/llm/hf/acme--Tiny-Coder-GGUF/Tiny-Coder-Q4_K_M.gguf",
                        "--mmproj", "/models/llm/hf/acme--Tiny-Coder-GGUF/mmproj-F16.gguf"]
    assert args[args.index("-c") + 1] == "40960" and args[args.index("-np") + 1] == "2" and "-kvu" in args
    assert "--kv-unified-per-slot" not in args                     # one request may use the whole pool

    # It survives a restart, apps see it, and the name is now taken.
    again = Catalog(hub.env.models_dir, hub.env.data_dir / "models.override.json")
    assert again.get_llm("tiny-coder")["custom"]
    tags = (await signed_in.get("/api/tags", headers={"Authorization": f"Bearer {api_key}"})).json()
    assert "tiny-coder" in [t["model"] for t in tags["models"]]
    assert (await signed_in.post("/hub/api/models/hf", json=body)).status_code == 400
    assert (await signed_in.get("/hub/api/models/hf", params={"repo": "acme/Tiny-Coder-GGUF"})).json()[
        "suggested"]["id"] == "tiny-coder-2"

    # A split quantisation: the first part is what llama-server opens.
    r = await signed_in.post("/hub/api/models/hf", json={**body, "file": "split/Tiny-Coder-BF16-00001-of-00002.gguf",
                                                         "mmproj": None, "id": "tiny-coder-bf16", "thinking": False})
    assert r.status_code == 200, r.text
    m = r.json()["model"]
    assert [f.get("role") for f in m["files"]] == [None, "part"] and "vision" not in m["capabilities"]
    assert "thinking" not in m["capabilities"] and "thinking" not in m
    await _drain(hub)
    assert hub.catalog.server_args("tiny-coder-bf16")[1].endswith("split/Tiny-Coder-BF16-00001-of-00002.gguf")

    # Removing: not while it is the chat model; bundled models never; files and folders go.
    hub.settings.update("models", {"default": "tiny-coder"})
    assert (await signed_in.delete("/hub/api/models/tiny-coder")).status_code == 409
    hub.settings.update("models", {"default": "qwen3.8-27b", "fallback_alias": "tiny-coder"})
    assert (await signed_in.delete("/hub/api/models/qwen3.8-27b")).status_code == 404
    folder = hub.env.models_dir / "llm" / "hf" / "acme--Tiny-Coder-GGUF"
    assert (folder / "Tiny-Coder-Q4_K_M.gguf").is_file()
    assert (await signed_in.delete("/hub/api/models/tiny-coder")).status_code == 200
    assert hub.catalog.get_llm("tiny-coder") is None and hub.settings.get("models", "fallback_alias") == ""
    assert not (folder / "Tiny-Coder-Q4_K_M.gguf").exists() and not (folder / "mmproj-F16.gguf").exists()
    assert (folder / "split").is_dir()                              # the other model's files stay
    assert (await signed_in.delete("/hub/api/models/tiny-coder-bf16")).status_code == 200
    assert not folder.exists() and (hub.env.models_dir / "llm" / "hf").is_dir()
    assert json.loads((hub.env.data_dir / "models.custom.json").read_text())["llm"] == []


async def test_hugging_face_problems(signed_in, app, fakes, monkeypatch):
    fakes.hf["repos"] = {"acme/Gated-GGUF": {"status": 401},
                         "acme/Safetensors-only": {"tree": [_f("model.safetensors", 10), _f("config.json", 1)]}}
    _repo(fakes, gguf={})                                          # no metadata: still usable
    get = lambda repo: signed_in.get("/hub/api/models/hf", params={"repo": repo})  # noqa: E731
    r = await get("acme/Nothing-GGUF")
    assert r.status_code == 404 and "no model called" in r.json()["detail"]
    r = await get("acme/Gated-GGUF")
    assert r.status_code == 403 and "HF_TOKEN" in r.json()["detail"]
    r = await get("acme/Safetensors-only")
    assert r.status_code == 400 and "GGUF" in r.json()["detail"]
    assert (await get("not a repo")).status_code == 400
    r = await get("acme/Tiny-Coder-GGUF")
    assert r.status_code == 200 and r.json()["meta"] == {}

    base = {"repo": "acme/Tiny-Coder-GGUF", "file": "Tiny-Coder-Q8_0.gguf", "id": "tiny", "ctx": 8192}
    post = lambda **kw: signed_in.post("/hub/api/models/hf", json={**base, **kw})  # noqa: E731
    assert (await post(file="README.md")).status_code == 400
    assert (await post(mmproj="nope.gguf")).status_code == 400
    assert (await post(id="Bad Name!")).status_code == 400
    assert (await post(id="qwen3.8-27b")).status_code == 400        # a bundled model's name
    real = shutil.disk_usage
    monkeypatch.setattr(shutil, "disk_usage", lambda p: real(p)._replace(free=4 * 1024**3))
    r = await post()
    assert r.status_code == 400 and "Not enough disk space" in r.json()["detail"]
    assert app.state.hub.catalog.get_llm("tiny") is None
