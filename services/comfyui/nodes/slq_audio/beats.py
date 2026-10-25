"""SLQBeatCheck: whether a remake kept its song's tempo, key and beat (rhythm.py), reported as JSON text - the Hub
puts it in the remake's result, so the assistant can say so before the user has listened (chat 76: four of six
remakes came back in time, two at another tempo, and only the user's ears told)."""
from __future__ import annotations

import json
import logging

from comfy_api.latest import IO

from . import rhythm
from .common import channels


class BeatCheck(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="SLQBeatCheck",
            display_name="Beat check: does a remake keep its song's time? (SLQ)",
            category="SLQ Hub/audio",
            description="The song's and the new version's tempo and key, and the share of the new version's beats "
                        "within 70 ms of the song's (librosa's beat tracker), as JSON text.",
            inputs=[IO.Audio.Input("song"), IO.Audio.Input("result")],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, song, result) -> IO.NodeOutput:
        try:
            a = channels(song, rhythm.RATE, 1)[0].numpy()
            b = channels(result, rhythm.RATE, 1)[0].numpy()
            report = rhythm.report(a, b)
        except Exception as exc:  # noqa: BLE001 - a measure only: it never costs the user the remake itself
            logging.exception("SLQBeatCheck could not measure the beat")
            report = {"verdict": "unknown", "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}
        return IO.NodeOutput(ui={"text": [json.dumps(report)]})
