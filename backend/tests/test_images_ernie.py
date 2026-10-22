"""ERNIE-Image: the workflow built from the official templates (its graphs
also pass a real ComfyUI v0.42.3's validation, see docs/HANDOFF.md)."""
from __future__ import annotations

import json

from app.images import workflows as W


def _node(g: dict, cls: str) -> list[dict]:
    return [n for n in g.values() if n["class_type"] == cls]


def test_base_model_with_the_prompt_enhancer():
    g = W.ernie_t2i('a "red" fox\nin snow, æøå', 1000, 760, seed=42)
    assert _node(g, "UNETLoader")[0]["inputs"]["unet_name"] == "ernie-image.safetensors"
    loaders = {n["inputs"]["clip_name"]: n["inputs"]["type"] for n in _node(g, "CLIPLoader")}
    assert loaders == {"ministral-3-3b.safetensors": "flux2", "ernie-image-prompt-enhancer.safetensors": "flux2"}
    tg = _node(g, "TextGenerate")[0]["inputs"]
    assert tg["clip"] == ["30", 0] and tg["max_length"] == 2048 and tg["sampling_mode"] == "on"
    assert tg["sampling_mode.temperature"] == 0.6 and tg["sampling_mode.top_k"] == 64 and tg["sampling_mode.seed"] == 42
    pe = tg["prompt"]
    assert pe.startswith("<s>[SYSTEM_PROMPT]你是一个专业的文生图 Prompt 增强助手") and pe.endswith("[/INST]")
    request = json.loads(pe[pe.index("[INST]") + 6:-len("[/INST]")])
    assert request == {"prompt": 'a "red" fox\nin snow, æøå', "width": 992, "height": 768}   # snapped to 16
    assert "æøå" in pe                                              # not escaped to \\u00e6
    enc = [n for n in _node(g, "CLIPTextEncode")]
    assert enc[0]["inputs"]["text"] == ["32", 0] and enc[1]["inputs"]["text"] == ""         # positive, empty negative
    assert g[W.ERNIE_PROMPT_NODE] == {"class_type": "PreviewAny", "inputs": {"source": ["32", 0]}}
    ks = _node(g, "KSampler")[0]["inputs"]
    assert (ks["steps"], ks["cfg"], ks["sampler_name"], ks["scheduler"]) == (25, 4.0, "euler", "simple")
    assert _node(g, "EmptyFlux2LatentImage")[0]["inputs"] == {"width": 992, "height": 768, "batch_size": 1}


def test_turbo_without_the_enhancer():
    g = W.ernie_t2i("a red fox", 1344, 768, seed=7, turbo=True, enhance=False)
    assert _node(g, "UNETLoader")[0]["inputs"]["unet_name"] == "ernie-image-turbo.safetensors"
    assert not _node(g, "TextGenerate") and not _node(g, "PreviewAny") and len(_node(g, "CLIPLoader")) == 1
    assert _node(g, "CLIPTextEncode")[0]["inputs"]["text"] == "a red fox"
    assert _node(g, "ConditioningZeroOut")[0]["inputs"]["conditioning"] == ["5", 0]
    ks = _node(g, "KSampler")[0]["inputs"]
    assert (ks["steps"], ks["cfg"], ks["negative"]) == (8, 1.0, ["6", 0])
