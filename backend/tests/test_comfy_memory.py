"""The self-test's look at ComfyUI's memory flags on AMD (0.73.1): with ComfyUI's "dynamic VRAM" on, Wan 2.2's videos
from a picture came out as flat colours or black on the RX 7900 XTX (2026-11-03, HANDOFF item 57)."""
from __future__ import annotations

from app.cli import comfy_memory

AMD = {"name": "cuda:0 AMD Radeon RX 7900 XTX : native"}
BASE = ["main.py", "--listen", "0.0.0.0", "--port", "8188", "--disable-metadata"]


def stats(argv: list[str], version: str = "2.14.1+rocm7.14", device: dict = AMD) -> dict:
    return {"system": {"pytorch_version": version, "argv": argv}, "devices": [device]}


def test_dynamic_vram_on_an_amd_card_is_warned_with_the_fix():
    state, what, fix = comfy_memory(stats(BASE))
    assert state == "WARN" and "flat colours or black" in what
    assert "COMFY_ARGS=--disable-dynamic-vram --reserve-vram 6" in fix and "compose up -d comfyui" in fix
    state, what, _ = comfy_memory(stats(BASE + ["--disable-dynamic-vram"]))
    assert state == "WARN" and "runs out of memory" in what
    assert comfy_memory(stats(BASE + ["--disable-dynamic-vram", "--reserve-vram", "6"])) == (
        "PASS", "dynamic VRAM off, 6 GB kept free for decoding videos", "")
    assert comfy_memory(stats(BASE + ["--disable-dynamic-vram", "--reserve-vram"]))[0] == "PASS"


def test_other_cards_and_a_comfyui_that_says_nothing():
    assert comfy_memory(stats(BASE, "2.14.1+cu130", {"name": "cuda:0 NVIDIA GeForce RTX 4090 : cudaMallocAsync"})) \
        is None
    assert comfy_memory({}) is None
    assert comfy_memory({"system": {"pytorch_version": "2.14.1+rocm7.14"}})[0] == "WARN"     # no argv: as given
