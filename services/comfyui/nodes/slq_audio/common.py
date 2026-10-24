"""Shared pieces of the SLQ Hub audio nodes: where the models and the third-party code are, and small helpers on
ComfyUI's AUDIO values ({"waveform": [batch, channels, samples], "sample_rate": int})."""
from __future__ import annotations

import os
from pathlib import Path

import torch

import comfy.audio
import comfy.model_management
import folder_paths

# Third-party code the image clones at pinned commits (services/comfyui/Dockerfile).
SEEDVC_DIR = Path(os.environ.get("SLQ_SEEDVC_DIR", "/opt/seed-vc"))
MSST_DIR = Path(os.environ.get("SLQ_MSST_DIR", "/opt/msst"))


class NodeError(ValueError):
    pass


def inside(root: Path, rel: str, what: str) -> Path:
    """A file or folder under root, from its path there; never outside it."""
    base = root.resolve()
    p = (base / (rel or "").strip()).resolve()
    if not (rel or "").strip() or (p != base and base not in p.parents):
        raise NodeError(f"{what}: {rel!r} is not a path inside {base}")
    if not p.exists():
        raise NodeError(f"{what}: {p} is missing")
    return p


def model_path(rel: str, what: str) -> Path:
    """A file or folder under ComfyUI's models folder ('seed-vc/rmvpe.pt')."""
    return inside(Path(folder_paths.models_dir), rel, what)


def load_weights(path: Path) -> dict:
    """A state dict from a .safetensors file or a torch pickle - tensors only (weights_only: no code runs)."""
    if path.suffix == ".safetensors":
        import safetensors.torch
        return safetensors.torch.load_file(str(path), device="cpu")
    try:
        return torch.load(str(path), map_location="cpu", weights_only=True)
    except Exception as exc:  # noqa: BLE001 - unpickling refuses anything but tensors and plain data
        raise NodeError(f"{path.name} could not be read as weights ({exc.__class__.__name__}: {str(exc)[:200]})")


def device() -> torch.device:
    return comfy.model_management.get_torch_device()


def make_room() -> None:
    """Our models are not ComfyUI's: unload ComfyUI's from the card before loading ours."""
    comfy.model_management.unload_all_models()
    comfy.model_management.soft_empty_cache()


def done_with(*modules) -> None:
    for m in modules:
        if isinstance(m, torch.nn.Module):
            m.to("cpu")
    comfy.model_management.soft_empty_cache()


def resample(wave: torch.Tensor, rate: int, to: int) -> torch.Tensor:
    return wave if int(rate) == int(to) else comfy.audio.resample(wave, int(rate), int(to))


def channels(audio: dict, rate: int, count: int) -> torch.Tensor:
    """The first item of an AUDIO at `rate` with `count` channels (1: the mean; 2: mono doubled, else the first
    two): [count, samples], float32."""
    w = audio["waveform"]
    if w.dim() == 2:
        w = w[None]
    w = w[0].float()
    if count == 1:
        w = w.mean(0, keepdim=True)
    elif w.shape[0] == 1:
        w = w.repeat(count, 1)
    else:
        w = w[:count]
    return resample(w.contiguous(), int(audio["sample_rate"]), rate)


def as_audio(wave: torch.Tensor, rate: int) -> dict:
    """[channels, samples] or [samples] -> an AUDIO."""
    if wave.dim() == 1:
        wave = wave[None]
    return {"waveform": wave.float().cpu()[None].contiguous(), "sample_rate": int(rate)}


def frame_rms(wave: torch.Tensor, rate: int, frame_s: float = 0.05) -> tuple[torch.Tensor, int]:
    """RMS of 50 ms frames of a mono wave, and the frame length in samples."""
    frame = max(1, int(rate * frame_s))
    n = wave.shape[-1] // frame
    if n == 0:
        return wave.pow(2).mean().sqrt()[None], wave.shape[-1]
    return wave[: n * frame].reshape(n, frame).pow(2).mean(1).sqrt(), frame


def voiced_level(wave: torch.Tensor, rate: int, floor_db: float = -40.0) -> float:
    """The RMS of the frames with sound in them (within floor_db of the loudest frame): a level for vocals that
    are silent half the time."""
    rms, _ = frame_rms(wave, rate)
    peak = float(rms.max()) if rms.numel() else 0.0
    if peak <= 0:
        return 0.0
    loud = rms[rms > peak * 10 ** (floor_db / 20)]
    return float(loud.pow(2).mean().sqrt())


def voiced_only(wave: torch.Tensor, rate: int, limit_s: float, floor_db: float = -40.0) -> torch.Tensor:
    """The parts of a mono clip with a voice in them, joined with short crossfades, up to limit_s seconds. A clip
    of a song's separated vocals is half silence, and the models take only the first seconds of a reference."""
    rms, frame = frame_rms(wave, rate)
    peak = float(rms.max()) if rms.numel() else 0.0
    if peak <= 1e-4:
        raise NodeError("the voice clip is silent")
    keep = rms > peak * 10 ** (floor_db / 20)
    # 100 ms of margin around each voiced frame, so the starts and ends of words stay
    keep = torch.nn.functional.max_pool1d(keep.float()[None, None], 5, 1, 2)[0, 0] > 0
    runs, start = [], None
    for i, k in enumerate(keep.tolist() + [False]):
        if k and start is None:
            start = i
        elif not k and start is not None:
            runs.append(wave[start * frame:i * frame])
            start = None
    fade = max(1, int(rate * 0.005))
    out = runs[0] if runs else wave
    for run in runs[1:]:
        if out.shape[-1] > fade and run.shape[-1] > fade:
            ramp = torch.linspace(0, 1, fade, device=wave.device)
            joint = out[-fade:] * (1 - ramp) + run[:fade] * ramp
            out = torch.cat([out[:-fade], joint, run[fade:]])
        else:
            out = torch.cat([out, run])
        if out.shape[-1] >= limit_s * rate:
            break
    return out[: int(limit_s * rate)]


def limit_peak(wave: torch.Tensor, ceiling: float = 0.99) -> torch.Tensor:
    peak = float(wave.abs().max()) if wave.numel() else 0.0
    return wave * (ceiling / peak) if peak > ceiling else wave
