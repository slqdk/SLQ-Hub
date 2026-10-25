"""0.22.1 - the card after the first test run on the real hardware (2026-10-25). A 720p video ran out of memory
while decoding, four times after ten minutes each; ComfyUI then stopped answering while it held the card, the Hub
gave up on the job, and every chat model load after it failed: seven test cases in a row. Now video is decoded in
tiles, a job the Hub gives up on is cancelled in ComfyUI, a busy ComfyUI is waited for, the card is cleared before a
chat model loads (and once more when the model did not fit), and a failure of the card does not hand the turn over."""
from __future__ import annotations

import pytest

from app.gpu import backend as B
from app.gpu.runner import RunnerError
from app.video import workflows as W

from .test_agent import finish, new_chat, say
from .test_audio import install

GB = 1024**3


class StubRunner:
    def __init__(self, vram: list[int], fail: str = "", fails: int = 0):
        self.vram, self.fail, self.fails, self.starts = list(vram), fail, fails, 0

    async def gpu(self) -> dict:
        return {"vram_used": self.vram.pop(0) if len(self.vram) > 1 else self.vram[0]}

    async def start(self, name, port, args) -> dict:
        self.starts += 1
        return {}

    async def wait_ready(self, name, port) -> None:
        if self.fails:
            self.fails -= 1
            raise RunnerError(f"llama-server 'main' stopped while loading (exit 1): {self.fail}")


class StubComfy:
    def __init__(self, runner: StubRunner | None = None, frees_to: int | None = None):
        self.calls: list[str] = []
        self.runner, self.frees_to = runner, frees_to

    async def interrupt(self, prompt_id=None):
        self.calls.append("interrupt")

    async def clear_queue(self, prompt_id=None):
        self.calls.append("clear")

    async def free(self):
        self.calls.append("free")
        if self.runner is not None and self.frees_to is not None:
            self.runner.vram = [self.frees_to]


class StubCatalog:
    def get_llm(self, ident):
        return {"id": ident}

    def installed(self, entry):
        return True

    def server_args(self, ident, models_dir):
        return ["-m", ident]


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(B, "FREE_WAIT_S", 0.2)
    monkeypatch.setattr(B, "CLEAR_WAIT_S", 0.2)


async def test_a_model_that_did_not_fit_loads_after_comfyui_frees_the_card(quick):
    runner = StubRunner([GB], fail="cudaMalloc failed: out of memory", fails=1)
    comfy = StubComfy()
    await B.GpuBackend(runner, comfy, StubCatalog()).load("llm:qwen3.6-35b-a3b")
    assert runner.starts == 2 and comfy.calls == ["interrupt", "clear", "free"]
    # another failure is not retried
    runner = StubRunner([GB], fail="unknown model architecture", fails=1)
    with pytest.raises(RunnerError):
        await B.GpuBackend(runner, StubComfy(), StubCatalog()).load("llm:x")
    assert runner.starts == 1


async def test_a_full_card_is_cleared_before_a_model_loads(quick):
    runner = StubRunner([16 * GB])
    comfy = StubComfy(runner, frees_to=GB)
    await B.GpuBackend(runner, comfy, StubCatalog()).load("llm:qwen3.6-35b-a3b")
    assert comfy.calls == ["interrupt", "clear", "free"] and runner.starts == 1
    # an empty card is left alone
    runner = StubRunner([GB])
    comfy = StubComfy()
    await B.GpuBackend(runner, comfy, StubCatalog()).load("llm:qwen3.6-35b-a3b")
    assert comfy.calls == []


async def test_comfyui_that_does_not_free_the_card_is_stopped(quick):
    runner = StubRunner([16 * GB])
    comfy = StubComfy(runner)                                  # /free does nothing: a job still runs
    await B.GpuBackend(runner, comfy, StubCatalog()).unload("comfy")
    assert comfy.calls == ["free", "interrupt", "clear", "free"]


async def test_the_card_is_cleared_for_a_chat_turn(signed_in, app, fakes):
    """Through the scheduler, as after the video case: ComfyUI holds 22 GB, a chat turn needs its model."""
    fakes.sup["vram_used"] = 22 * GB
    fakes.comfy["on_free"] = lambda: fakes.sup.update(vram_used=GB)
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Hej"))
    assert run.status == "done", run.events[-1]
    assert fakes.comfy["interrupts"] == [{}] and {"clear": True} in fakes.comfy["queue_edits"]
    assert fakes.comfy["frees"] >= 1 and len(fakes.sup["starts"]) == 1


async def test_a_card_that_stays_full_says_why(signed_in, app, fakes, monkeypatch):
    monkeypatch.setattr(B, "CLEAR_WAIT_S", 0.2)
    fakes.sup["vram_used"] = 22 * GB                           # ComfyUI does not let go
    fakes.llama["down"] = True                                 # so llama-server never comes up
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Hej"))
    assert run.status == "error" and "out of memory" in run.events[-1]["data"]["message"]
    assert len(fakes.sup["starts"]) == 2                       # tried, cleared, tried again


def test_video_is_decoded_in_tiles():
    g = W.wan_5b(prompt="a kite", seconds=5, width=1280, height=704, seed=1, unet="u", text_encoder="t", vae="v")
    assert g["10"] == {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["9", 0], "vae": ["4", 0], "tile_size": 512,
                                                                   "overlap": 64, "temporal_size": 4096,
                                                                   "temporal_overlap": 8}}
    assert "VAEDecode" not in W.class_types(g)
    g = W.wan_14b(prompt="a kite", seconds=5, width=832, height=480, seed=1, high="h", low="l", text_encoder="t",
                  vae="v", lora_high=None, lora_low=None)
    assert g["16"]["class_type"] == "VAEDecodeTiled" and g["16"]["inputs"]["samples"] == ["15", 0]


async def test_a_video_out_of_memory_is_the_cards_fault_and_its_job_is_cancelled(signed_in, app, fakes):
    """Three failures of the card in a row do not hand the turn over (the stronger model would meet the same card),
    the result says not to try the same again, and ComfyUI is told to drop each job."""
    install(app, "wan2.2-5b")
    fakes.comfy["fail_with"] = "CUDA out of memory. Tried to allocate 18.56 GiB."
    fakes.comfy["fail_node"] = "VAEDecodeTiled"
    calls = [{"name": "video_generate", "arguments": {"prompt": f"a red kite over a green hill, take {i}"}}
             for i in range(3)]                                # not the same step three times: that is a loop
    fakes.llama["replies"] = [{"tool_calls": [calls[0]]}, {"tool_calls": [calls[1]]}, {"tool_calls": [calls[2]]},
                              {"content": "The card ran out of memory; I could not make the video."}]
    c = await new_chat(signed_in, settings={"model": "qwen3.6-35b-a3b"})
    run = await finish(app, await say(signed_in, c["id"], "Make a video of a kite"), timeout=30)
    assert run.status == "done", run.events[-1]
    results = [e["data"] for e in run.events if e["type"] == "tool_result"]
    assert len(results) == 3 and all(not r["ok"] for r in results)
    assert results[0]["text"].startswith("Video generation failed: ComfyUI reported an error: VAEDecodeTiled: CUDA out "
                                         "of memory") and "do not repeat it" in results[0]["text"]
    assert not any(e["type"] == "escalated" for e in run.events)
    pids = [f"p{i}" for i in range(1, 4)]
    assert [i.get("prompt_id") for i in fakes.comfy["interrupts"]] == pids
    assert [q.get("delete") for q in fakes.comfy["queue_edits"]] == [[p] for p in pids]


async def test_a_busy_comfyui_lends_its_last_node_list(app, fakes):
    comfy = app.state.hub.comfy
    first = await comfy.object_info()
    fakes.comfy["object_info_down"] = True
    comfy._object_info_at = 0                                  # stale: asked again
    assert await comfy.object_info() is first
    comfy._object_info = None
    with pytest.raises(Exception):
        await comfy.object_info()
