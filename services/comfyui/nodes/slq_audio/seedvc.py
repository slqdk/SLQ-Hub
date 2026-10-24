"""Voice conversion with Seed-VC v1 (Plachtaa/seed-vc, GPL-3.0): whisper-small hears what is said or sung, a
diffusion transformer redraws the mel spectrogram in the reference's voice, BigVGAN turns it back into sound. For
singing the F0-conditioned 44.1 kHz model keeps the melody, which RMVPE reads.

This is a port of Seed-VC's inference.py - its models, its chunking (30 s of context, the reference as the prompt
of every chunk, 16-frame crossfades) - with three changes: ComfyUI's resampler and the Kaldi filterbank below in
place of torchaudio (which ComfyUI's image no longer has), the reference trimmed to the parts with a voice in them
(a clip of a song's separated vocals is half silence), and an automatic shift by whole octaves only, so converted
singing stays in the key of the music it goes back into."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
import types
from pathlib import Path

import numpy as np
import torch
import yaml

import comfy.utils
from comfy_api.latest import IO

from .common import (SEEDVC_DIR, NodeError, as_audio, channels, device, done_with, inside, limit_peak,
                     load_weights, make_room, model_path, resample, voiced_level, voiced_only)

CONTENT_RATE = 16000                 # whisper hears 16 kHz
CAMPPLUS = "campplus_cn_common.bin"  # the speaker-style encoder's weights, in Seed-VC's repository
_STATE: dict = {}


# ---------------------------------------------------------------- Kaldi filterbank
# Adapted from TorchAudio's torchaudio/compliance/kaldi.py (fbank with Seed-VC's settings: 80 bins, no dither,
# 25 ms povey windows every 10 ms, pre-emphasis 0.97, power spectrum, log).
# Copyright (c) 2017 Facebook Inc. (Soumith Chintala). BSD 2-Clause License: redistribution and use in source and
# binary forms, with or without modification, are permitted provided that (1) redistributions of source code
# retain the above copyright notice, this list of conditions and the following disclaimer and (2) redistributions
# in binary form reproduce them in the documentation and/or other materials provided with the distribution.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DAMAGES
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE.

def _mel_scale(freq):
    return 1127.0 * (1.0 + freq / 700.0).log() if torch.is_tensor(freq) else 1127.0 * math.log(1.0 + freq / 700.0)


def _mel_banks(num_bins: int, padded: int, rate: float, low: float, high: float) -> torch.Tensor:
    nyquist = 0.5 * rate
    if high <= 0.0:
        high += nyquist
    width = rate / padded
    mel_low, mel_high = _mel_scale(low), _mel_scale(high)
    delta = (mel_high - mel_low) / (num_bins + 1)
    b = torch.arange(num_bins).unsqueeze(1)
    left, center, right = mel_low + b * delta, mel_low + (b + 1.0) * delta, mel_low + (b + 2.0) * delta
    mel = _mel_scale(width * torch.arange(padded // 2)).unsqueeze(0)
    up = (mel - left) / (center - left)
    down = (right - mel) / (right - center)
    return torch.max(torch.zeros(1), torch.min(up, down))


def kaldi_fbank(wave: torch.Tensor, num_mel_bins: int = 80, rate: float = 16000.0) -> torch.Tensor:
    """torchaudio.compliance.kaldi.fbank(wave[None], num_mel_bins=80, dither=0, sample_frequency=16000)."""
    x = wave.float()
    eps = torch.tensor(torch.finfo(torch.float).eps, device=x.device)
    shift, size = int(rate * 0.010), int(rate * 0.025)
    padded = 1 if size == 0 else 2 ** (size - 1).bit_length()
    if x.numel() < size:
        raise NodeError("the voice clip is too short")
    m = 1 + (x.numel() - size) // shift
    frames = x.contiguous().as_strided((m, size), (shift * x.stride(0), x.stride(0)))
    frames = frames - frames.mean(dim=1, keepdim=True)                       # remove the DC offset
    previous = torch.nn.functional.pad(frames.unsqueeze(0), (1, 0), mode="replicate").squeeze(0)[:, :-1]
    frames = frames - 0.97 * previous                                        # pre-emphasis
    window = torch.hann_window(size, periodic=False, device=x.device, dtype=x.dtype).pow(0.85)   # povey
    frames = torch.nn.functional.pad(frames * window, (0, padded - size))
    spectrum = torch.fft.rfft(frames).abs().pow(2.0)
    banks = torch.nn.functional.pad(_mel_banks(num_mel_bins, padded, rate, 20.0, 0.0), (0, 1)).to(x.device, x.dtype)
    return torch.max(torch.mm(spectrum, banks.T), eps).log()


# ---------------------------------------------------------------- Seed-VC's code

def _shim_dac() -> None:
    """Seed-VC's length regulator imports descript-audio-codec's VectorQuantize, which its v1 models never build
    (vector_quantize is off); a stand-in spares the image that package and its dependencies."""
    class VectorQuantize(torch.nn.Module):
        def __init__(self, *args, **kwargs):
            raise NodeError("this Seed-VC model needs descript-audio-codec (vector quantization)")
    dac, dac_nn, quantize = (types.ModuleType(n) for n in ("dac", "dac.nn", "dac.nn.quantize"))
    dac.__path__, dac_nn.__path__ = [], []
    quantize.VectorQuantize = VectorQuantize
    dac.nn, dac_nn.quantize = dac_nn, quantize
    sys.modules.update({"dac": dac, "dac.nn": dac_nn, "dac.nn.quantize": quantize})


def seedvc_code() -> None:
    """Seed-VC's `modules` package importable: its checkout on sys.path (last, so it shadows nothing)."""
    if _STATE.get("code"):
        return
    if not (SEEDVC_DIR / "modules" / "commons.py").is_file():
        raise NodeError(f"Seed-VC's code is missing from {SEEDVC_DIR}")
    if importlib.util.find_spec("dac") is None:
        _shim_dac()
    if str(SEEDVC_DIR) not in sys.path:
        sys.path.append(str(SEEDVC_DIR))
    import modules.commons as commons  # noqa: PLC0415
    if Path(commons.__file__).resolve().parents[1] != SEEDVC_DIR.resolve():
        raise NodeError(f"another package called 'modules' ({commons.__file__}) hides Seed-VC's")
    _STATE["code"] = True


def load_dit(path: Path, cfg: dict):
    """Seed-VC's length regulator and diffusion model from a checkpoint ({"net": {"cfm": ..., ...}}). Buffers
    may be missing or of another size (Seed-VC skips those too); a missing weight is an error, never noise."""
    from modules.commons import build_model, recursive_munch  # noqa: PLC0415
    params = recursive_munch(cfg["model_params"])
    params.dit_type = "DiT"
    model = build_model(params, stage="DiT")
    state = load_weights(path)
    net = state.get("net", state)
    for key in model:
        if not isinstance(net.get(key), dict):
            raise NodeError(f"{path.name} has no {key} weights - is it a Seed-VC v1 model?")
        given = {k.removeprefix("module."): v for k, v in net[key].items()}
        own = model[key].state_dict()
        fits = {k: v for k, v in given.items() if k in own and own[k].shape == v.shape}
        weights = {n for n, _ in model[key].named_parameters()}
        missing = sorted(k for k in weights if k not in fits)
        if missing:
            raise NodeError(f"{path.name} does not fit its config: {len(missing)} weights of {key} missing or of "
                            f"another shape ({', '.join(missing[:3])})")
        model[key].load_state_dict(fits, strict=False)
        model[key].eval()
    return model


def load_vocoder(folder: Path, rate: int, mels: int) -> torch.nn.Module:
    from modules.bigvgan.bigvgan import BigVGAN, load_hparams_from_json  # noqa: PLC0415
    h = load_hparams_from_json(str(folder / "config.json"))
    if int(h.sampling_rate) != rate or int(h.num_mels) != mels:
        raise NodeError(f"the vocoder in {folder.name} makes {h.sampling_rate} Hz from {h.num_mels} mel bands; this "
                        f"model needs {rate} Hz from {mels}")
    vocoder = BigVGAN(h, use_cuda_kernel=False)
    state = load_weights(folder / "bigvgan_generator.pt")
    vocoder.load_state_dict(state.get("generator", state))
    vocoder.remove_weight_norm()
    return vocoder.eval()


def load_style_encoder() -> torch.nn.Module:
    from modules.campplus.DTDNN import CAMPPlus  # noqa: PLC0415
    encoder = CAMPPlus(feat_dim=80, embedding_size=192)
    encoder.load_state_dict(load_weights(inside(SEEDVC_DIR, CAMPPLUS, "speaker style encoder")))
    return encoder.eval()


def load_whisper(folder: Path):
    from transformers import WhisperFeatureExtractor, WhisperModel  # noqa: PLC0415
    full = WhisperModel.from_pretrained(str(folder), local_files_only=True)
    encoder = full.encoder.eval()
    del full
    return encoder, WhisperFeatureExtractor.from_pretrained(str(folder), local_files_only=True)


class Content:
    """whisper-small's encoder states for 16 kHz audio, 50 a second (Seed-VC's semantic_fn)."""

    def __init__(self, encoder, extractor, dev: torch.device):
        self.encoder, self.extractor, self.dev = encoder, extractor, dev
        self.dtype = torch.float16 if dev.type == "cuda" else torch.float32
        self.encoder.to(dev, self.dtype)

    def __call__(self, wave16k: torch.Tensor) -> torch.Tensor:
        inputs = self.extractor([wave16k.float().cpu().numpy()], return_tensors="pt", return_attention_mask=True,
                                sampling_rate=CONTENT_RATE)
        out = self.encoder(inputs.input_features.to(self.dev, self.dtype)).last_hidden_state.float()
        return out[:, : wave16k.shape[-1] // 320 + 1]

    def long(self, wave16k: torch.Tensor) -> torch.Tensor:
        """Longer than 30 s: 30-s windows overlapping by 5 s, the overlap dropped from each next one."""
        if wave16k.shape[-1] <= CONTENT_RATE * 30:
            return self(wave16k)
        parts, buffer, done, overlap = [], None, 0, 5
        while done < wave16k.shape[-1]:
            if buffer is None:
                chunk = wave16k[done:done + CONTENT_RATE * 30]
            else:
                chunk = torch.cat([buffer, wave16k[done:done + CONTENT_RATE * (30 - overlap)]])
            states = self(chunk)
            parts.append(states if done == 0 else states[:, 50 * overlap:])
            buffer = chunk[-CONTENT_RATE * overlap:]
            done += CONTENT_RATE * 30 if done == 0 else chunk.shape[-1] - CONTENT_RATE * overlap
        return torch.cat(parts, dim=1)


def f0_track(rmvpe, wave16k: torch.Tensor, chunk_s: int = 60, context_s: int = 1) -> np.ndarray:
    """RMVPE's pitch, 100 frames a second; long audio in 60-s pieces with a second of context on each side."""
    n = wave16k.shape[-1]
    frames = n // 160 + 1
    if n <= CONTENT_RATE * chunk_s:
        return rmvpe.infer_from_audio(wave16k, thred=0.03)[:frames]
    out, step, ctx = [], CONTENT_RATE * chunk_s, CONTENT_RATE * context_s
    for start in range(0, n, step):
        lo, hi = max(0, start - ctx), min(n, start + step + ctx)
        f0 = rmvpe.infer_from_audio(wave16k[lo:hi], thred=0.03)
        first = (start - lo) // 160
        out.append(f0[first:first + step // 160])
    f0 = np.concatenate(out)
    return np.pad(f0, (0, max(0, frames - f0.shape[0])))[:frames]


def _crossfade(a: np.ndarray, b: np.ndarray, overlap: int) -> np.ndarray:
    fade_out = np.cos(np.linspace(0, np.pi / 2, overlap)) ** 2
    fade_in = np.cos(np.linspace(np.pi / 2, 0, overlap)) ** 2
    n = min(overlap, len(b))
    b = b.copy()
    b[:n] = b[:n] * fade_in[:n] + (a[-overlap:] * fade_out)[:n]
    return b


# ---------------------------------------------------------------- the node

class SeedVC(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="SLQSeedVC",
            display_name="Seed-VC voice conversion (SLQ)",
            category="SLQ Hub/audio",
            description="Make a voice (speech, or singing with the F0 model) sound like the reference voice.",
            inputs=[
                IO.Audio.Input("source", tooltip="The voice to convert - clean vocals or speech"),
                IO.Audio.Input("reference", tooltip="The voice to take on: 1-30 s of it (silence is skipped)"),
                IO.String.Input("dit", default="seed-vc/DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth",
                                tooltip="The diffusion model, a path inside ComfyUI's models folder"),
                IO.String.Input("config", default="configs/presets/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml",
                                tooltip="Its config, a path inside the Seed-VC checkout"),
                IO.String.Input("whisper", default="seed-vc/whisper-small", tooltip="whisper-small's folder"),
                IO.String.Input("vocoder", default="seed-vc/bigvgan_v2_44khz_128band_512x",
                                tooltip="BigVGAN's folder (config.json, bigvgan_generator.pt)"),
                IO.String.Input("rmvpe", default="seed-vc/rmvpe.pt", tooltip="RMVPE, for the F0 models"),
                IO.Int.Input("steps", default=30, min=1, max=200, tooltip="Diffusion steps; 30-50 for singing"),
                IO.Float.Input("cfg", default=0.7, min=0.0, max=1.0, step=0.05),
                IO.Int.Input("pitch", default=0, min=-24, max=24, tooltip="Semitones to move the melody (F0 models)"),
                IO.Boolean.Input("auto_octave", default=True,
                                 tooltip="Also move it by whole octaves towards the reference's range (stays in key)"),
                IO.Boolean.Input("match_loudness", default=True, tooltip="As loud as the source"),
                IO.Float.Input("reference_seconds", default=25.0, min=1.0, max=30.0, step=0.5),
            ],
            outputs=[IO.Audio.Output()],
        )

    @classmethod
    def execute(cls, source, reference, dit, config, whisper, vocoder, rmvpe, steps, cfg, pitch, auto_octave,
                match_loudness, reference_seconds) -> IO.NodeOutput:
        seedvc_code()
        conf = yaml.safe_load(inside(SEEDVC_DIR, config, "Seed-VC config").read_text(encoding="utf-8"))
        spect = conf["preprocess_params"]["spect_params"]
        rate, hop = int(conf["preprocess_params"]["sr"]), int(spect["hop_length"])
        uses_f0 = bool(conf["model_params"]["DiT"].get("f0_condition"))
        paths = {"dit": model_path(dit, "Seed-VC model"), "whisper": model_path(whisper, "whisper-small"),
                 "vocoder": model_path(vocoder, "BigVGAN vocoder")}
        if uses_f0:
            paths["rmvpe"] = model_path(rmvpe, "RMVPE pitch model")
        stereo = int(source["waveform"].shape[-2]) >= 2 if source["waveform"].dim() >= 2 else False
        src = limit_peak(channels(source, rate, 1)[0])
        ref = voiced_only(limit_peak(channels(reference, rate, 1)[0]), rate, float(reference_seconds))
        if ref.shape[-1] < rate:
            raise NodeError("the voice clip has less than a second of voice in it")
        if src.shape[-1] < rate // 4:
            raise NodeError("the audio to convert is too short")

        make_room()
        dev = device()
        model = load_dit(paths["dit"], conf)
        voc = load_vocoder(paths["vocoder"], rate, int(spect["n_mels"]))
        style_encoder = load_style_encoder()
        encoder, extractor = load_whisper(paths["whisper"])
        f0_model = None
        loaded = [*model.values(), voc, style_encoder, encoder]
        try:
            for m in model.values():
                m.to(dev)
            model.cfm.estimator.setup_caches(max_batch_size=1, max_seq_length=8192)
            voc.to(dev)
            style_encoder.to(dev)
            content = Content(encoder, extractor, dev)
            if uses_f0:
                from modules.rmvpe import RMVPE  # noqa: PLC0415
                f0_model = RMVPE(str(paths["rmvpe"]), is_half=False, device=dev)
                loaded.append(f0_model.model)
            from modules.audio import mel_spectrogram  # noqa: PLC0415
            mel_args = {"n_fft": int(spect["n_fft"]), "win_size": int(spect["win_length"]), "hop_size": hop,
                        "num_mels": int(spect["n_mels"]), "sampling_rate": rate, "fmin": spect.get("fmin", 0),
                        "fmax": None if spect.get("fmax", "None") == "None" else 8000, "center": False}
            with torch.no_grad():
                out, semis = convert(model, content, f0_model, voc, style_encoder,
                                     lambda w: mel_spectrogram(w, **mel_args), src.to(dev), ref.to(dev), rate, hop,
                                     int(steps), float(cfg), int(pitch), bool(auto_octave))
        finally:
            done_with(*loaded)
            del model, voc, style_encoder, encoder, f0_model, loaded
        out = out.cpu()
        if match_loudness:
            have, want = voiced_level(out, rate), voiced_level(src.cpu(), rate)
            if have > 0 and want > 0:
                out = out * max(0.1, min(10.0, want / have))
        out = limit_peak(out)
        wave = out.repeat(2, 1) if stereo else out[None]
        info = {"semitones": semis, "reference_seconds": round(ref.shape[-1] / rate, 1),
                "seconds": round(out.shape[-1] / rate, 1), "rate": rate}
        return IO.NodeOutput(as_audio(wave, rate), ui={"text": [json.dumps(info)]})


def convert(model, content: Content, f0_model, vocoder, style_encoder, to_mel, source: torch.Tensor,
            ref: torch.Tensor, rate: int, hop: int, steps: int, cfg: float, pitch: int,
            auto_octave: bool) -> tuple[torch.Tensor, int]:
    """Seed-VC's inference loop for one source and one reference (mono waves at the model's rate)."""
    dev = source.device
    src16, ref16 = resample(source, rate, CONTENT_RATE), resample(ref, rate, CONTENT_RATE)
    s_alt = content.long(src16)
    s_ori = content(ref16)
    mel, mel2 = to_mel(source[None].float()), to_mel(ref[None].float())
    lengths = torch.LongTensor([mel.size(2)]).to(dev)
    lengths2 = torch.LongTensor([mel2.size(2)]).to(dev)
    feat = kaldi_fbank(ref16)
    style = style_encoder((feat - feat.mean(dim=0, keepdim=True)).unsqueeze(0))
    semis = 0
    f0_ori = shifted = None
    if f0_model is not None:
        f0_ori = torch.from_numpy(f0_track(f0_model, ref16)).to(dev)[None].float()
        f0_alt = torch.from_numpy(f0_track(f0_model, src16)).to(dev)[None].float()
        voiced_ori, voiced_alt = f0_ori[f0_ori > 1], f0_alt[f0_alt > 1]
        if auto_octave and voiced_ori.numel() and voiced_alt.numel():
            gap = float(torch.median(torch.log(voiced_ori)) - torch.median(torch.log(voiced_alt)))
            semis = 12 * int(round(gap / math.log(2)))
        semis += pitch
        shifted = f0_alt.clone()
        if semis:
            shifted[f0_alt > 1] = f0_alt[f0_alt > 1] * 2 ** (semis / 12)
    cond = model.length_regulator(s_alt, ylens=lengths, n_quantizers=3, f0=shifted)[0]
    prompt = model.length_regulator(s_ori, ylens=lengths2, n_quantizers=3, f0=f0_ori)[0]

    window = rate // hop * 30
    overlap = 16
    overlap_wave = overlap * hop
    room = window - mel2.size(2)
    if room <= 4 * overlap:
        raise NodeError("the voice clip is too long for the model's 30-s context")
    total = max(1, math.ceil(max(1, cond.size(1) - overlap) / (room - overlap)))
    progress = comfy.utils.ProgressBar(total)
    use_amp = dev.type == "cuda"
    pieces, previous, done, n = [], None, 0, 0
    while done < cond.size(1):
        chunk = cond[:, done:done + room]
        last = done + room >= cond.size(1)
        both = torch.cat([prompt, chunk], dim=1)
        with torch.autocast(device_type=dev.type, dtype=torch.float16, enabled=use_amp):
            target = model.cfm.inference(both, torch.LongTensor([both.size(1)]).to(dev), mel2, style, None, steps,
                                         inference_cfg_rate=cfg)
            target = target[:, :, mel2.size(-1):]
        wave = vocoder(target.float()).squeeze().float().cpu().numpy()
        if done == 0:
            if last:
                pieces.append(wave)
                break
            pieces.append(wave[:-overlap_wave])
            previous = wave[-overlap_wave:]
        elif last:
            pieces.append(_crossfade(previous, wave, overlap_wave))
            break
        else:
            pieces.append(_crossfade(previous, wave[:-overlap_wave], overlap_wave))
            previous = wave[-overlap_wave:]
        done += target.size(2) - overlap
        n += 1
        progress.update_absolute(min(n, total), total)
    progress.update_absolute(total, total)
    return torch.from_numpy(np.concatenate(pieces)).float(), semis
