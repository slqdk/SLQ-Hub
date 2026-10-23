"""SLQ Hub's speech service: text to speech on the CPU with Chatterbox models.

It serves only the Hub (the `core` network). There is no GPU here on purpose: speech runs while the chat model
stays loaded on the card, so a short sentence costs seconds instead of a model swap.

    GET  /health   torch, threads, loaded models, voice prompts kept, whether it is busy
    POST /speak    one text -> one audio file, as NDJSON lines: {"event": "progress", ...} per piece of text,
                   then {"event": "done", "audio": <base64>, ...} or {"event": "error", "message": ...}
    POST /free     forget every loaded model

A /speak body:

    {"model": {"key": "roest-v3", "family": "chatterbox-mtl" | "chatterbox-turbo",
               "files": {"t3": path, "s3gen": path, "ve": path, "tokenizer": path (mtl: a .json file,
                         turbo: a folder), "conds": path (optional),
                         "decoder": path (optional, mtl: Turbo's two-step S3Gen instead of the ten-step one)},
               "int8": false (8-bit weights for T3's linear layers)},
     "text": "...", "language": "da",
     "voice": {"path": "/models/speech/.../mic.wav"} | {"audio": "<base64 of a WAV>"} | null,
     "cache_voice": false,
     "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "top_p": 0.95, "min_p": 0.05, "top_k": 1000,
     "repetition_penalty": 2.0, "seed": 1234, "format": "mp3" | "wav" | "flac" | "ogg",
     "t3_threads": 5 (optional: T3 on fewer threads, for the self-test's measurement)}

Every path must lie under SPEECH_MODELS (default /models/speech). The text is cut into sentences (the models
handle a sentence or two at a time), each is spoken with the same voice and seed, the pieces are trimmed and
joined with short pauses (longer between paragraphs), and the result is encoded once. The "done" event says
where the time went: T3's tokens and seconds, the decoder's seconds, and how many pieces ran to the token
ceiling instead of stopping by themselves (their end is probably noise). Nothing is written to
disk. Models are kept in memory and dropped after SPEECH_IDLE_MIN minutes without a request; so are the voice
prompts of the model's voice files, and of a clip sent as audio only when cache_voice is true (the Hub sends
false in a person's chat, so nothing of her voice stays here after the request).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import gc
import hashlib
import io
import json
import logging
import math
import os
import random
import re
import struct
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

log = logging.getLogger("speech")

MODELS_ROOT = Path(os.environ.get("SPEECH_MODELS", "/models/speech")).resolve()
PORT = int(os.environ.get("SPEECH_PORT", "7100"))
IDLE_MIN = float(os.environ.get("SPEECH_IDLE_MIN", "15"))
MAX_MODELS = int(os.environ.get("SPEECH_MAX_MODELS", "2"))
MAX_TEXT = 6000                     # characters per request (about seven minutes of speech)
MAX_VOICE_BYTES = 25 * 1024 * 1024
SR = 24000                          # what Chatterbox speaks at (S3GEN_SR)
TOKEN_SAMPLES = SR // 25            # the sound of one speech token (25 a second)
PIECE_CHARS = 260                   # text per model call: a sentence or two
PAUSE = 0.15                        # seconds between sentences
PARAGRAPH_PAUSE = 0.45              # ... and between paragraphs
FORMATS = {"mp3": ("mp3", "libmp3lame", 96_000), "ogg": ("ogg", "libopus", 48_000),
           "flac": ("flac", "flac", None), "wav": ("wav", "pcm_s16le", None)}


def cpu_threads() -> int:
    """SPEECH_THREADS, else the container's CPU quota (cgroup v2), else all CPUs but two."""
    if os.environ.get("SPEECH_THREADS"):
        return max(1, int(os.environ["SPEECH_THREADS"]))
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            return max(1, math.floor(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    return max(1, (os.cpu_count() or 4) - 2)


def cpu_name() -> str:
    """The CPU's model name and the vector instructions that matter for speed here (for /health)."""
    try:
        info = Path("/proc/cpuinfo").read_text(errors="replace")
    except OSError:
        return ""
    name = next((ln.split(":", 1)[1].strip() for ln in info.splitlines() if ln.startswith("model name")), "")
    flags = set(next((ln.split(":", 1)[1].split() for ln in info.splitlines() if ln.startswith("flags")), []))
    have = [f for f in ("avx2", "avx512f", "avx512_vnni", "avx_vnni", "avx512_bf16", "amx_bf16") if f in flags]
    return f"{name} ({', '.join(have)})" if have else name


# ---------------------------------------------------------------- text

_SENTENCE = re.compile(r"(?<=[.!?…])[\"'»”’)\]]*\s+")
_CLAUSE = re.compile(r"(?<=[,;:])\s+")


def _cut(sentence: str, limit: int) -> list[str]:
    """A sentence longer than `limit`: at commas, semicolons and colons, then between words."""
    if len(sentence) <= limit:
        return [sentence]
    out: list[str] = []
    cur = ""
    for part in _CLAUSE.split(sentence):
        if len(part) > limit:
            words = part.split()
            part = ""
            for w in words:
                if part and len(part) + 1 + len(w) > limit:
                    out.append(part)
                    part = w
                else:
                    part = f"{part} {w}".strip()
        if cur and len(cur) + 1 + len(part) > limit:
            out.append(cur)
            cur = part
        else:
            cur = f"{cur} {part}".strip()
    if cur:
        out.append(cur)
    return out


def split_text(text: str, limit: int = PIECE_CHARS) -> list[tuple[str, float]]:
    """Pieces of at most `limit` characters along sentence ends, each with the pause that follows it
    (none after the last). A line break starts a new paragraph."""
    pieces: list[tuple[str, float]] = []
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*", text.strip())]
    for para in (p for p in paragraphs if p):
        cur = ""
        for sentence in (s.strip() for s in _SENTENCE.split(para)):
            for part in _cut(sentence, limit) if sentence else []:
                if cur and len(cur) + 1 + len(part) > limit:
                    pieces.append((cur, PAUSE))
                    cur = part
                else:
                    cur = f"{cur} {part}".strip()
        if cur:
            pieces.append((cur, PAUSE))
        if pieces:
            pieces[-1] = (pieces[-1][0], PARAGRAPH_PAUSE)
    if pieces:
        pieces[-1] = (pieces[-1][0], 0.0)
    return pieces


def max_tokens(text: str) -> int:
    """A ceiling on speech tokens (25 per second) for a piece of text: about three per character, which is
    far more than speech needs, so a model that loses its place stops instead of babbling for 40 s."""
    return int(min(1000, 75 + 3 * len(text)))


# ---------------------------------------------------------------- audio

def trim(wav: np.ndarray, threshold_db: float = -45.0, keep: float = 0.04) -> np.ndarray:
    """Cut leading and trailing near-silence, keeping `keep` seconds on each side."""
    if wav.size == 0:
        return wav
    frame = int(0.01 * SR)
    n = len(wav) // frame
    if n < 3:
        return wav
    rms = np.sqrt(np.mean(wav[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    loud = np.nonzero(20 * np.log10(rms) > threshold_db)[0]
    if loud.size == 0:
        return wav[:0]
    pad = int(keep * SR)
    start = max(0, loud[0] * frame - pad)
    end = min(len(wav), (loud[-1] + 1) * frame + pad)
    return wav[start:end]


# (format tag, bits) -> sample type: integer PCM and IEEE float, the WAVs ffmpeg, libsndfile and torchaudio write
WAV_KINDS = {(1, 8): "u8", (1, 16): "<i2", (1, 24): "i24", (1, 32): "<i4", (3, 32): "<f4", (3, 64): "<f8"}
NOT_WAV = "the voice clip must be a WAV file (the Hub converts other formats)"


def parse_wav(data: bytes, max_seconds: float = 60.0) -> tuple[np.ndarray, int]:
    """A WAV -> (mono float32 samples, sample rate), at most max_seconds of it: integer PCM of 8-32 bits or 32/64-bit
    float, plain or WAVE_FORMAT_EXTENSIBLE (the Røst voice samples are 32-bit float). A reader of its own on
    purpose: the Hub converts what people give it to WAV with ffmpeg in the sandbox, so no media library parses
    their files here. (The Hub has the same reader in backend/app/audio/wavfile.py.)"""
    mv = memoryview(data)
    if len(mv) < 12 or bytes(mv[:4]) != b"RIFF" or bytes(mv[8:12]) != b"WAVE":
        raise ValueError(NOT_WAV)
    fmt = payload = None
    pos = 12
    while pos + 8 <= len(mv) and (fmt is None or payload is None):
        cid, size = bytes(mv[pos:pos + 4]), int.from_bytes(mv[pos + 4:pos + 8], "little")
        body = mv[pos + 8:pos + 8 + size]               # a data chunk may claim more than there is (a stream)
        if cid == b"fmt " and len(body) >= 16:
            tag, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", body)
            if tag == 0xFFFE and len(body) >= 26:         # extensible: the sub-format GUID starts with the tag
                tag = struct.unpack_from("<H", body, 24)[0]
            fmt = (tag, channels, rate, bits)
        elif cid == b"data":
            payload = body
        pos += 8 + size + (size & 1)
    if fmt is None or payload is None:
        raise ValueError(NOT_WAV)
    tag, channels, rate, bits = fmt
    kind = WAV_KINDS.get((tag, bits))
    if kind is None or not 1 <= channels <= 8 or not 4000 <= rate <= 192000:
        raise ValueError(NOT_WAV + f" (this one is format {tag}, {bits} bits, {channels} channels, {rate} Hz)")
    frame = bits // 8 * channels
    raw = payload[: min(len(payload) // frame, int(rate * max_seconds)) * frame]
    if kind == "u8":
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif kind == "i24":
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float32) / float(1 << 23)
    elif kind[1] == "f":
        x = np.clip(np.nan_to_num(np.frombuffer(raw, kind).astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0),
                    -1.0, 1.0)
    else:
        x = np.frombuffer(raw, kind).astype(np.float32) / float(1 << (bits - 1))
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x.astype(np.float32), rate


def read_wav(data: bytes) -> np.ndarray:
    """A voice clip (a WAV) -> mono float32 at 24 kHz."""
    x, rate = parse_wav(data)
    if rate != SR:
        import librosa
        x = librosa.resample(x, orig_sr=rate, target_sr=SR)
    return x.astype(np.float32)


def encode(wav: np.ndarray, fmt: str) -> bytes:
    import av
    container_fmt, codec, bit_rate = FORMATS[fmt]
    pcm = np.clip(wav, -1.0, 1.0)
    buf = io.BytesIO()
    with av.open(buf, "w", format=container_fmt) as out:
        stream = out.add_stream(codec, rate=SR)
        stream.layout = "mono"
        if bit_rate:
            stream.bit_rate = bit_rate
        if codec in ("pcm_s16le", "flac"):
            frame = av.AudioFrame.from_ndarray((pcm * 32767).astype(np.int16)[None, :], format="s16", layout="mono")
        else:
            frame = av.AudioFrame.from_ndarray(pcm.astype(np.float32)[None, :], format="fltp", layout="mono")
        frame.sample_rate = SR
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def give_back_memory() -> None:
    """Collect, then hand freed heap memory back to the system: glibc keeps it otherwise (a dropped 3 GB model
    left 1.9 GB resident; malloc_trim brought it to 0.8 GB)."""
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


@contextlib.contextmanager
def t3_threads(p: dict):
    """T3 on fewer threads when the request asks (p["t3_threads"]): one token at a time is limited by memory more
    than by cores, so fewer threads may be as fast. The decoder gets them all back."""
    n = int(p.get("t3_threads") or 0)
    if not n:
        yield
        return
    import torch
    before = torch.get_num_threads()
    torch.set_num_threads(n)
    try:
        yield
    finally:
        torch.set_num_threads(before)


def int8_weights(module) -> int:
    """A transformer's linear layers with 8-bit weights (dynamic quantization): T3 reads a quarter of the bytes
    per token, about 1.5x faster on the CPU. GPT-2's Conv1D layers (Turbo) are linear layers with the weight
    transposed; they become nn.Linear first. Returns how many layers changed."""
    import torch
    from transformers.pytorch_utils import Conv1D
    for parent in list(module.modules()):
        for name, child in list(parent.named_children()):
            if isinstance(child, Conv1D):
                nx, nf = child.weight.shape                 # x @ weight + bias, weight (in, out)
                lin = torch.nn.Linear(nx, nf)
                with torch.no_grad():
                    lin.weight.copy_(child.weight.t())
                    lin.bias.copy_(child.bias)
                setattr(parent, name, lin)
    before = sum(isinstance(m, torch.nn.Linear) for m in module.modules())
    torch.ao.quantization.quantize_dynamic(module, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)
    return before - sum(isinstance(m, torch.nn.Linear) for m in module.modules())


def inside_models(path: str) -> Path:
    p = Path(path).resolve()
    if MODELS_ROOT != p and MODELS_ROOT not in p.parents:
        raise ValueError(f"{path} is not under {MODELS_ROOT}")
    if not p.exists():
        raise ValueError(f"{path} is missing - download the model again (Settings -> Models)")
    return p


# ---------------------------------------------------------------- models

class Voice:
    """A model's voice prompt: T3's conditioning and S3Gen's reference."""

    def __init__(self, t3, gen: dict):
        self.t3, self.gen = t3, gen


class Model:
    """One loaded Chatterbox model (both families share the parts: T3 writes speech tokens, S3Gen turns them
    into sound, the voice encoder describes a voice)."""

    family = ""

    def __init__(self, files: dict, int8: bool = False):
        import perth
        import torch
        from chatterbox.models.voice_encoder import VoiceEncoder
        self.torch = torch
        self.files = files
        self.int8 = int8
        ve = VoiceEncoder()
        ve_path = inside_models(files["ve"])
        if ve_path.suffix == ".safetensors":
            from safetensors.torch import load_file
            ve.load_state_dict(load_file(ve_path))
        else:
            ve.load_state_dict(torch.load(ve_path, map_location="cpu", weights_only=True))
        self.ve = ve.eval()
        self.watermarker = perth.PerthImplicitWatermarker()
        self.voices: OrderedDict[str, Voice] = OrderedDict()
        self.default: Voice | None = None
        self.loaded_at = time.time()

    def _builtin(self):
        conds = self.files.get("conds")
        if not conds:
            return None
        if self.family == "chatterbox-turbo":
            from chatterbox.tts_turbo import Conditionals
        else:
            from chatterbox.mtl_tts import Conditionals
        c = Conditionals.load(inside_models(conds), map_location="cpu")
        return Voice(c.t3, c.gen)

    def voice(self, clip: np.ndarray | None, key: str, exaggeration: float, cache: bool = True) -> Voice:
        """The voice prompt for a clip (kept for the next request when `cache`), or the model's built-in voice."""
        if clip is None:
            if self.default is None:
                self.default = self._builtin()
            if self.default is None:
                raise ValueError("this model has no built-in voice: give a voice clip")
            return self._with_exaggeration(self.default, exaggeration)
        cache_key = f"{key}:{exaggeration:.3f}"
        if cache_key in self.voices:
            self.voices.move_to_end(cache_key)
            return self.voices[cache_key]
        v = self._prepare(clip, exaggeration)
        if cache:
            self.voices[cache_key] = v
            while len(self.voices) > 16:
                self.voices.popitem(last=False)
        return v

    def _with_exaggeration(self, v: Voice, exaggeration: float) -> Voice:
        return v

    def _prepare(self, clip: np.ndarray, exaggeration: float) -> Voice:
        import librosa
        torch = self.torch
        from chatterbox.models.s3tokenizer import S3_SR
        from chatterbox.models.t3.modules.cond_enc import T3Cond
        ref24 = clip.astype(np.float32)
        ref16 = librosa.resample(ref24, orig_sr=SR, target_sr=S3_SR)
        gen = self.s3gen.embed_ref(ref24[: 10 * SR], SR, device="cpu")
        tokens = None
        if plen := self.t3.hp.speech_cond_prompt_len:
            tokens, _ = self.s3gen.tokenizer.forward([ref16[: 6 * S3_SR]], max_len=plen)
            tokens = torch.atleast_2d(tokens)
        emb = torch.from_numpy(self.ve.embeds_from_wavs([ref16], sample_rate=S3_SR)).mean(axis=0, keepdim=True)
        return Voice(T3Cond(speaker_emb=emb, cond_prompt_speech_tokens=tokens,
                            emotion_adv=exaggeration * torch.ones(1, 1, 1)), gen)

    def speak(self, text: str, language: str, voice: Voice, p: dict) -> tuple[np.ndarray, dict]:
        """One piece of text -> (its sound, {"tokens", "stopped", "t3", "decoder"}): T3's speech tokens, whether
        it ended them itself (else it ran to the ceiling), and the seconds T3 and the decoder took."""
        raise NotImplementedError


class Multilingual(Model):
    """Chatterbox Multilingual (and its fine-tunes, like Røst v3): a 0.5B Llama T3 with classifier-free
    guidance and a 10-step S3Gen."""

    family = "chatterbox-mtl"

    def __init__(self, files: dict, int8: bool = False):
        super().__init__(files, int8)
        from safetensors.torch import load_file
        from chatterbox.models.s3gen import S3Gen
        from chatterbox.models.t3 import T3
        from chatterbox.models.t3.modules.t3_config import T3Config
        from chatterbox.models.tokenizers import MTLTokenizer
        t3 = T3(T3Config.multilingual())
        state = load_file(inside_models(files["t3"]))
        if "model" in state.keys():
            state = state["model"][0]
        t3.load_state_dict(state)
        if int8:
            int8_weights(t3.tfmr)
        self.t3 = t3.eval()
        if files.get("decoder"):
            # Turbo's distilled decoder: two steps without guidance instead of ten with it, the same speech tokens
            # and voice prompt - about six times less work for the part that costs the most on the CPU
            s3gen = S3Gen(meanflow=True)
            s3gen.load_state_dict(load_file(inside_models(files["decoder"])), strict=True)
            self.steps = 2
        else:
            s3gen = S3Gen()
            s3gen.load_state_dict(self.torch.load(inside_models(files["s3gen"]), map_location="cpu",
                                                  weights_only=True))
            self.steps = 0
        self.s3gen = s3gen.eval()
        self.tokenizer = MTLTokenizer(str(inside_models(files["tokenizer"])))

    def _with_exaggeration(self, v: Voice, exaggeration: float) -> Voice:
        from chatterbox.models.t3.modules.cond_enc import T3Cond
        if float(v.t3.emotion_adv[0, 0, 0]) == float(exaggeration):
            return v
        return Voice(T3Cond(speaker_emb=v.t3.speaker_emb, cond_prompt_speech_tokens=v.t3.cond_prompt_speech_tokens,
                            emotion_adv=exaggeration * self.torch.ones(1, 1, 1)), v.gen)

    def speak(self, text: str, language: str, voice: Voice, p: dict) -> tuple[np.ndarray, dict]:
        torch = self.torch
        import torch.nn.functional as F
        from chatterbox.mtl_tts import SUPPORTED_LANGUAGES, punc_norm
        from chatterbox.models.s3gen.const import S3GEN_SIL
        from chatterbox.models.s3tokenizer import drop_invalid_tokens
        lang = (language or "").lower()
        if lang not in SUPPORTED_LANGUAGES:
            raise ValueError(f"language {language!r} is not one this model speaks ({', '.join(SUPPORTED_LANGUAGES)})")
        tokens = self.tokenizer.text_to_tokens(punc_norm(text), language_id=lang)
        tokens = torch.cat([tokens, tokens], dim=0)                       # two rows for classifier-free guidance
        tokens = F.pad(tokens, (1, 0), value=self.t3.hp.start_text_token)
        tokens = F.pad(tokens, (0, 1), value=self.t3.hp.stop_text_token)
        t0 = time.time()
        with torch.inference_mode(), t3_threads(p):
            speech = self.t3.inference(t3_cond=voice.t3, text_tokens=tokens, max_new_tokens=max_tokens(text),
                                       temperature=p["temperature"], cfg_weight=p["cfg_weight"],
                                       repetition_penalty=p["repetition_penalty"], min_p=p["min_p"],
                                       top_p=p["top_p"])
        with torch.inference_mode():
            stats = {"tokens": int(speech.shape[-1]), "t3": time.time() - t0, "decoder": 0.0,
                     "stopped": bool(speech.numel()) and int(speech[0, -1]) == self.t3.hp.stop_speech_token}
            speech = drop_invalid_tokens(speech[0])
            speech = speech[speech < 6561]                                # only real speech tokens reach S3Gen
            if speech.numel() == 0:
                return np.zeros(0, np.float32), stats
            t1 = time.time()
            if self.steps:
                # as Turbo does it, after dropping T3's last token: emitted just before the end with weak
                # attention, it decodes to noise (upstream's own note); three silence tokens close the sound
                speech = torch.cat([speech[:-1] if speech.numel() > 1 else speech,
                                    torch.tensor([S3GEN_SIL] * 3, dtype=torch.long)])
                wav, _ = self.s3gen.inference(speech_tokens=speech, ref_dict=voice.gen, n_cfm_timesteps=self.steps)
                wav = wav.squeeze(0).detach().cpu().numpy()
            else:
                wav, _ = self.s3gen.inference(speech_tokens=speech, ref_dict=voice.gen)
                wav = wav.squeeze(0).detach().cpu().numpy()[: max(1, speech.numel() - 1) * TOKEN_SAMPLES]
            stats["decoder"] = time.time() - t1
        return self.watermarker.apply_watermark(wav, sample_rate=SR).astype(np.float32), stats


class Turbo(Model):
    """Chatterbox Turbo (and Røst v3 Turbo): a 350M GPT-2 T3 without guidance and a 2-step S3Gen - about three
    times faster. Voice clips must be longer than five seconds."""

    family = "chatterbox-turbo"

    def __init__(self, files: dict, int8: bool = False):
        super().__init__(files, int8)
        from safetensors.torch import load_file
        from transformers import AutoTokenizer
        from chatterbox.models.s3gen import S3Gen
        from chatterbox.models.t3 import T3
        from chatterbox.models.t3.modules.t3_config import T3Config
        hp = T3Config(text_tokens_dict_size=50276)
        hp.llama_config_name = "GPT2_medium"
        hp.speech_tokens_dict_size = 6563
        hp.input_pos_emb = None
        hp.speech_cond_prompt_len = 375
        hp.use_perceiver_resampler = False
        hp.emotion_adv = False
        t3 = T3(hp)
        state = load_file(inside_models(files["t3"]))
        if "model" in state.keys():
            state = state["model"][0]
        t3.load_state_dict(state)
        del t3.tfmr.wte
        if int8:
            int8_weights(t3.tfmr)
        self.t3 = t3.eval()
        s3gen = S3Gen(meanflow=True)
        s3gen.load_state_dict(load_file(inside_models(files["s3gen"])), strict=True)
        self.s3gen = s3gen.eval()
        tok = AutoTokenizer.from_pretrained(str(inside_models(files["tokenizer"])))
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        self.tokenizer = tok

    def _prepare(self, clip: np.ndarray, exaggeration: float) -> Voice:
        if len(clip) <= 5 * SR:
            raise ValueError("the voice clip must be longer than five seconds for this model")
        import pyloudnorm
        try:                                    # as Turbo does: the clip at -27 LUFS
            gain = 10.0 ** ((-27 - pyloudnorm.Meter(SR).integrated_loudness(clip)) / 20.0)
            if math.isfinite(gain) and gain > 0:
                clip = clip * gain
        except Exception:  # noqa: BLE001 - a clip it cannot measure is used as it is
            pass
        return super()._prepare(clip, 0.0)

    def speak(self, text: str, language: str, voice: Voice, p: dict) -> tuple[np.ndarray, dict]:
        torch = self.torch
        from chatterbox.tts_turbo import punc_norm
        from chatterbox.models.s3gen.const import S3GEN_SIL
        ids = self.tokenizer(punc_norm(text), return_tensors="pt", padding=True, truncation=True).input_ids
        limit = max_tokens(text)
        t0 = time.time()
        with torch.inference_mode(), t3_threads(p):
            speech = self.t3.inference_turbo(t3_cond=voice.t3, text_tokens=ids, temperature=p["temperature"],
                                             top_k=int(p["top_k"]), top_p=p["top_p"],
                                             repetition_penalty=p["repetition_penalty"], max_gen_len=limit)
        with torch.inference_mode():
            n = int(speech.shape[-1])         # one before its loop and up to `limit` in it, the stop token removed
            stats = {"tokens": n, "stopped": n <= limit, "t3": time.time() - t0, "decoder": 0.0}
            speech = speech[speech < 6561]
            if speech.numel() == 0:
                return np.zeros(0, np.float32), stats
            t1 = time.time()
            speech = torch.cat([speech, torch.tensor([S3GEN_SIL] * 3, dtype=torch.long)])
            wav, _ = self.s3gen.inference(speech_tokens=speech, ref_dict=voice.gen, n_cfm_timesteps=2)
            stats["decoder"] = time.time() - t1
        wav = wav.squeeze(0).detach().cpu().numpy()
        return self.watermarker.apply_watermark(wav, sample_rate=SR).astype(np.float32), stats


FAMILIES = {Multilingual.family: Multilingual, Turbo.family: Turbo}
DEFAULTS = {"exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "top_p": 0.95, "min_p": 0.05,
            "top_k": 1000, "repetition_penalty": 2.0}
LIMITS = {"exaggeration": (0.0, 2.0), "cfg_weight": (0.0, 1.0), "temperature": (0.05, 2.0), "top_p": (0.05, 1.0),
          "min_p": (0.0, 1.0), "top_k": (0, 5000), "repetition_penalty": (1.0, 3.0)}


class Speaker:
    """The loaded models (at most MAX_MODELS, least recently used goes first) and the one worker thread that
    runs them, so torch gets the CPU threads and /health stays responsive."""

    def __init__(self, threads: int):
        self.threads = threads
        self.models: OrderedDict[str, Model] = OrderedDict()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speak")
        self.lock = asyncio.Lock()
        self.busy = False
        self.last_used = time.time()
        self._mutex = threading.Lock()

    def model(self, spec: dict) -> Model:
        family = spec.get("family")
        if family not in FAMILIES:
            raise ValueError(f"unknown model family {family!r}")
        files = spec.get("files") or {}
        for role in ("t3", "s3gen", "ve", "tokenizer"):
            if not files.get(role):
                raise ValueError(f"the model's {role} file is not given")
        for path in files.values():              # every file there and inside the models, before anything is built
            inside_models(str(path))
        int8 = spec.get("int8") is True
        key = str(spec.get("key") or "") + "|" + "|".join(f"{k}={files[k]}" for k in sorted(files)) + f"|int8={int8}"
        with self._mutex:
            if key in self.models:
                self.models.move_to_end(key)
                return self.models[key]
            while len(self.models) >= MAX_MODELS:  # make room first: two models at once is what the memory holds
                gone = self.models.popitem(last=False)[0]          # (no name may keep the old model alive)
                log.info("dropped %s", gone.split("|", 1)[0])
        give_back_memory()
        t0 = time.time()
        m = FAMILIES[family](files, int8)
        extras = [x for x, on in (("Turbo's decoder", family == "chatterbox-mtl" and bool(files.get("decoder"))),
                                  ("8-bit T3", int8)) if on]
        m.label = str(spec.get("key") or family) + (f" ({', '.join(extras)})" if extras else "")
        log.info("loaded %s in %.1f s", m.label, time.time() - t0)
        with self._mutex:
            self.models[key] = m
        return m

    def free(self) -> int:
        with self._mutex:
            n = len(self.models)
            self.models.clear()
        give_back_memory()
        return n

    def loaded(self) -> list[str]:
        with self._mutex:
            return [getattr(m, "label", k.split("|", 1)[0]) for k, m in self.models.items()]

    def voices(self) -> int:
        """Voice prompts kept for the next request (the models' voice files, and clips sent with cache_voice)."""
        with self._mutex:
            return sum(len(m.voices) for m in self.models.values())

    async def run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)


def params(body: dict) -> dict:
    out = {}
    for k, default in DEFAULTS.items():
        v = body.get(k)
        v = default if v is None else v
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{k} must be a number")
        lo, hi = LIMITS[k]
        out[k] = min(hi, max(lo, float(v)))
    return out


def seed_all(seed: int) -> None:
    import torch
    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)


# ---------------------------------------------------------------- HTTP

def make_app(speaker: Speaker):
    from aiohttp import web
    cpu = cpu_name()

    async def health(_request):
        import torch
        return web.json_response({"ok": True, "torch": torch.__version__, "threads": speaker.threads, "cpu": cpu,
                                  "loaded": speaker.loaded(), "voices": speaker.voices(), "busy": speaker.busy})

    async def free(_request):
        async with speaker.lock:
            n = speaker.free()
        return web.json_response({"freed": n})

    async def speak(request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("the body must be a JSON object")
            text = str(body.get("text") or "").strip()
            if not text:
                raise ValueError("there is no text to speak")
            if len(text) > MAX_TEXT:
                raise ValueError(f"the text is {len(text)} characters; at most {MAX_TEXT} per request")
            fmt = str(body.get("format") or "mp3").lower()
            if fmt not in FORMATS:
                raise ValueError(f"format is one of {', '.join(FORMATS)}")
            p = params(body)
            if body.get("t3_threads") is not None:      # the self-test's question: is T3 faster on fewer threads?
                p["t3_threads"] = max(1, min(speaker.threads, int(body["t3_threads"])))
            seed = int(body.get("seed") if body.get("seed") is not None else random.randint(1, 2**31 - 1))
            voice_spec = body.get("voice") or None
            if voice_spec is not None and not isinstance(voice_spec, dict):
                raise ValueError("voice is {\"path\": ...}, {\"audio\": base64 WAV} or null")
            clip_data = None
            cache_voice = True                    # a model's own voice file; a clip sent as audio only when asked
            if voice_spec:
                if voice_spec.get("path"):
                    path = inside_models(str(voice_spec["path"]))
                    if path.suffix.lower() != ".wav" or not path.is_file():
                        raise ValueError(f"{voice_spec['path']} is not a .wav file")
                    if path.stat().st_size > MAX_VOICE_BYTES:
                        raise ValueError("the voice clip is over 25 MB; 10-20 seconds of speech is enough")
                    clip_data = path.read_bytes()
                elif voice_spec.get("audio"):
                    clip_data = base64.b64decode(voice_spec["audio"], validate=True)
                    cache_voice = body.get("cache_voice") is True
                if clip_data is not None and len(clip_data) > MAX_VOICE_BYTES:
                    raise ValueError("the voice clip is over 25 MB; 10-20 seconds of speech is enough")
            pieces = split_text(text)
            if not pieces:
                raise ValueError("there is no text to speak")
        except (ValueError, TypeError, OSError, json.JSONDecodeError, base64.binascii.Error) as exc:
            return web.json_response({"error": str(exc)}, status=400)

        resp = web.StreamResponse(headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store"})
        await resp.prepare(request)

        async def send(obj: dict) -> None:
            await resp.write((json.dumps(obj) + "\n").encode())

        t0 = time.time()
        speaker.last_used = time.time()
        try:
            await send({"event": "queued", "busy": speaker.busy})
            async with speaker.lock:
                speaker.busy = True
                try:
                    await send({"event": "started", "pieces": len(pieces)})
                    model = await speaker.run(speaker.model, body.get("model") or {})
                    clip = None
                    key = ""
                    if clip_data is not None:
                        key = hashlib.sha256(clip_data).hexdigest()
                        clip = await speaker.run(read_wav, clip_data)
                        if len(clip) < 2 * SR:
                            raise ValueError("the voice clip is shorter than two seconds; 10-20 seconds works best")
                    voice = await speaker.run(model.voice, clip, key, p["exaggeration"], cache_voice)
                    parts: list[np.ndarray] = []
                    totals = {"tokens": 0, "capped": 0, "t3": 0.0, "decoder": 0.0}
                    for i, (piece, pause) in enumerate(pieces):
                        def one(piece=piece, i=i):
                            seed_all(seed + i)
                            sound, st = model.speak(piece, str(body.get("language") or "en"), voice, p)
                            return trim(sound), st
                        wav, st = await speaker.run(one)
                        totals["tokens"] += st["tokens"]
                        totals["t3"] += st["t3"]
                        totals["decoder"] += st["decoder"]
                        if not st["stopped"]:
                            totals["capped"] += 1
                            log.warning("piece %d of %d ran to the ceiling of %d tokens: the model did not stop by "
                                        "itself", i + 1, len(pieces), max_tokens(piece))
                        parts.append(wav)
                        if pause and wav.size:
                            parts.append(np.zeros(int(pause * SR), np.float32))
                        await send({"event": "progress", "piece": i + 1, "pieces": len(pieces),
                                    "seconds": round(sum(len(x) for x in parts) / SR, 2)})
                    wav = np.concatenate(parts) if parts else np.zeros(0, np.float32)
                    if wav.size == 0:
                        raise ValueError("the model produced no sound for this text")
                    data = await speaker.run(encode, wav, fmt)
                finally:
                    speaker.busy = False
                    speaker.last_used = time.time()
            await send({"event": "done", "format": fmt, "sample_rate": SR, "seconds": round(len(wav) / SR, 2),
                        "compute": round(time.time() - t0, 2), "seed": seed, "pieces": len(pieces),
                        "threads": speaker.threads, "tokens": totals["tokens"], "capped": totals["capped"],
                        "t3_seconds": round(totals["t3"], 2), "decoder_seconds": round(totals["decoder"], 2),
                        "audio": base64.b64encode(data).decode()})
        except (ConnectionResetError, asyncio.CancelledError):
            log.info("the Hub went away; stopped after the piece in hand")
            raise
        except ValueError as exc:
            await send({"event": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - the Hub gets the reason, the log the trace
            log.exception("speak failed")
            await send({"event": "error", "message": f"{exc.__class__.__name__}: {exc}"[:500]})
        await resp.write_eof()
        return resp

    app = web.Application(client_max_size=40 * 1024 * 1024)
    app.add_routes([web.get("/health", health), web.post("/free", free), web.post("/speak", speak)])

    async def idle(_app):
        async def loop():
            while True:
                await asyncio.sleep(60)
                if speaker.loaded() and not speaker.busy and time.time() - speaker.last_used > IDLE_MIN * 60:
                    async with speaker.lock:
                        log.info("idle for %d minutes: freeing %d model(s)", IDLE_MIN, speaker.free())
        task = asyncio.get_running_loop().create_task(loop())
        yield
        task.cancel()

    app.cleanup_ctx.append(idle)
    return app


def warm_up() -> None:
    """librosa compiles its numba helpers on first use (seconds on a small CPU); do it before the first request."""
    try:
        import librosa
        x = np.sin(np.linspace(0, 2000, SR * 2)).astype(np.float32)
        librosa.resample(x, orig_sr=SR, target_sr=16000)
        librosa.effects.trim(x)
        np.abs(librosa.stft(x, n_fft=400, hop_length=160))
    except Exception:  # noqa: BLE001
        log.exception("warm-up failed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    threads = cpu_threads()
    import torch
    torch.set_num_threads(threads)
    log.info("torch %s on %d CPU threads, models under %s", torch.__version__, threads, MODELS_ROOT)
    threading.Thread(target=warm_up, daemon=True).start()
    from aiohttp import web
    web.run_app(make_app(Speaker(threads)), host="0.0.0.0", port=PORT, access_log=None)


if __name__ == "__main__":
    main()
