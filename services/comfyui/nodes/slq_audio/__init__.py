"""SLQ Hub's own ComfyUI nodes for sound (see README.md). Only the Hub queues them, always through its GPU
scheduler. The heavy third-party code (Seed-VC, MSST's separation model) loads when a node runs, never at start:
a problem there fails that job with its reason instead of keeping ComfyUI from starting."""
from __future__ import annotations

from typing_extensions import override

from comfy_api.latest import ComfyExtension, IO

from .ace import AceStepEncode, AceStepReference
from .seedvc import SeedVC
from .separate import SeparateVocals


class SlqAudio(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        return [AceStepReference, AceStepEncode, SeparateVocals, SeedVC]


async def comfy_entrypoint() -> SlqAudio:
    return SlqAudio()
