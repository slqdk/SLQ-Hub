"""Vocals and accompaniment apart: a Mel-Band RoFormer vocal model (Kimberley Jensen's weights, trained with
ZFTurbo's Music-Source-Separation-Training) run the way that project runs it - 8-second chunks at 44.1 kHz,
overlapping, faded and averaged - with the accompaniment as the mix minus the vocals.

The model code is MSST's own (MIT), from the commit the image clones; it is loaded under a private module name
because MSST imports it as `models.bs_roformer`, and ComfyUI's models folder would answer to `models`."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import torch
import torch.nn.functional as F
import yaml

import comfy.utils
from comfy_api.latest import IO

from .common import (MSST_DIR, NodeError, as_audio, channels, device, done_with, inside, load_weights, make_room,
                     model_path)

RATE = 44100
_CODE: dict = {}


class _Loader(yaml.SafeLoader):
    """MSST's configs write tuples as !!python/tuple; nothing else beyond plain YAML."""


_Loader.add_constructor("tag:yaml.org,2002:python/tuple", lambda loader, node: tuple(loader.construct_sequence(node)))


def read_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.load(f, Loader=_Loader)  # noqa: S506 - a SafeLoader with one extra tag
    if not isinstance(cfg, dict) or not isinstance(cfg.get("model"), dict):
        raise NodeError(f"{path.name} is not a separation model config (no model section)")
    return cfg


def roformer_class():
    """MSST's MelBandRoformer, loaded from its files under a private name (its own import of attend.py is
    pointed at the private copy)."""
    if "cls" in _CODE:
        return _CODE["cls"]
    folder = MSST_DIR / "models" / "bs_roformer"
    spec = importlib.util.spec_from_file_location("slq_msst_attend", folder / "attend.py")
    if spec is None or spec.loader is None or not (folder / "mel_band_roformer.py").is_file():
        raise NodeError(f"the separation code is missing from {folder}")
    attend = importlib.util.module_from_spec(spec)
    sys.modules["slq_msst_attend"] = attend
    spec.loader.exec_module(attend)
    source = (folder / "mel_band_roformer.py").read_text(encoding="utf-8")
    needle = "from models.bs_roformer.attend import Attend"
    if source.count(needle) != 1:
        raise NodeError("MSST's mel_band_roformer.py changed (its import of Attend); pin MSST_REF in the image")
    mod = types.ModuleType("slq_msst_mel_band_roformer")
    mod.__file__ = str(folder / "mel_band_roformer.py")
    sys.modules[mod.__name__] = mod
    exec(compile(source.replace(needle, "from slq_msst_attend import Attend"), mod.__file__, "exec"),  # noqa: S102
         mod.__dict__)
    _CODE["cls"] = mod.MelBandRoformer
    return _CODE["cls"]


def load_model(weights: Path, cfg: dict) -> torch.nn.Module:
    params = dict(cfg["model"])
    params["match_input_audio_length"] = True
    model = roformer_class()(**params)
    state = load_weights(weights)
    for key in ("state_dict", "state", "model"):
        if isinstance(state.get(key), dict):
            state = state[key]
    state = {k.removeprefix("module."): v for k, v in state.items()}
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing or unexpected:
        raise NodeError(f"{weights.name} does not fit the config: {len(missing)} weights missing "
                        f"({', '.join(list(missing)[:3])}), {len(unexpected)} unknown ({', '.join(list(unexpected)[:3])})")
    return model.eval()


def fade_window(size: int, fade: int) -> torch.Tensor:
    w = torch.ones(size)
    if fade > 0:
        w[:fade] = torch.linspace(0, 1, fade)
        w[-fade:] = torch.linspace(1, 0, fade)
    return w


def demix(model: torch.nn.Module, mix: torch.Tensor, chunk: int, overlap: int, batch: int, dev: torch.device,
          progress=None) -> torch.Tensor:
    """The model's stem for a [channels, samples] mix: overlapping chunks (step chunk/overlap), each faded in and
    out except at the ends, summed and divided by the summed windows (MSST's demix, generic mode)."""
    length = mix.shape[-1]
    step = max(1, chunk // max(2, overlap))        # with no overlap the faded chunk edges would be lost
    fade = chunk // 10
    border = chunk - step
    padded = length > 2 * border and border > 0
    if padded:
        mix = F.pad(mix[None], (border, border), mode="reflect")[0]
    total = mix.shape[-1]
    window = fade_window(chunk, fade)
    result = torch.zeros_like(mix)
    counter = torch.zeros(total)
    starts = list(range(0, total, step))
    use_amp = dev.type == "cuda"
    with torch.inference_mode():
        for b in range(0, len(starts), batch):
            group = starts[b:b + batch]
            parts = []
            for s in group:
                part = mix[:, s:s + chunk]
                n = part.shape[-1]
                mode = "reflect" if n > chunk // 2 else "constant"
                if n < chunk:
                    part = F.pad(part[None], (0, chunk - n), mode=mode)[0] if mode == "reflect" else \
                        F.pad(part, (0, chunk - n))
                parts.append(part)
            with torch.autocast(dev.type, dtype=torch.float16, enabled=use_amp):
                out = model(torch.stack(parts).to(dev)).float().cpu()
            if out.dim() == 4:                                   # several stems: the first is the target
                out = out[:, 0]
            for j, s in enumerate(group):
                n = min(chunk, total - s)
                w = window.clone()
                if s == 0:
                    w[:fade] = 1
                if s + step >= total:
                    w[-fade:] = 1
                result[:, s:s + n] += out[j, :, :n] * w[:n]
                counter[s:s + n] += w[:n]
            if progress is not None:
                progress.update_absolute(min(b + batch, len(starts)), len(starts))
    est = result / counter.clamp(min=1e-8)
    if padded:
        est = est[:, border:-border]
    return torch.nan_to_num(est)


class SeparateVocals(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="SLQSeparateVocals",
            display_name="Separate vocals (SLQ)",
            category="SLQ Hub/audio",
            description="Vocals and the accompaniment (the mix minus the vocals) with a Mel-Band RoFormer vocal "
                        "model, at 44.1 kHz stereo.",
            inputs=[
                IO.Audio.Input("audio"),
                IO.String.Input("model", default="separation/MelBandRoformer.ckpt",
                                tooltip="Weights, a path inside ComfyUI's models folder"),
                IO.String.Input("config", default="configs/KimberleyJensen/config_vocals_mel_band_roformer_kj.yaml",
                                tooltip="Its config, a path inside the MSST checkout"),
                IO.Int.Input("overlap", default=2, min=2, max=8,
                             tooltip="Chunks per chunk length: more is smoother at the joins and slower"),
                IO.Int.Input("batch", default=2, min=1, max=16),
            ],
            outputs=[IO.Audio.Output(display_name="vocals"), IO.Audio.Output(display_name="accompaniment")],
        )

    @classmethod
    def execute(cls, audio, model, config, overlap, batch) -> IO.NodeOutput:
        cfg = read_config(inside(MSST_DIR, config, "separation config"))
        weights = model_path(model, "separation model")
        rate = int(cfg.get("audio", {}).get("sample_rate") or cfg["model"].get("sample_rate") or RATE)
        chunk = int(cfg.get("audio", {}).get("chunk_size") or 352800)
        stereo = bool(cfg["model"].get("stereo", True))
        mix = channels(audio, rate, 2 if stereo else 1)
        if mix.shape[-1] < rate // 10:
            raise NodeError("the audio is shorter than a tenth of a second")
        make_room()
        dev = device()
        net = load_model(weights, cfg).to(dev)
        try:
            progress = comfy.utils.ProgressBar(1)
            vocals = demix(net, mix, chunk, int(overlap), int(batch), dev, progress)
        finally:
            done_with(net)
            del net
        accompaniment = mix - vocals
        return IO.NodeOutput(as_audio(vocals, rate), as_audio(accompaniment, rate))
