"""ComfyUI workflows for sound (API format), built in code from the official templates
(Comfy-Org/workflow_templates, ComfyUI v0.42.3; fetched 2026-10-23).

ACE-Step 1.5 (audio_ace_step1_5_xl_turbo.json, audio_ace_step_1_5_split.json): UNETLoader ->
ModelSamplingAuraFlow (shift 3) -> KSampler (8 steps, cfg 1, euler/simple for the turbo models);
DualCLIPLoader (the 0.6B text encoder and the 1.7B or 4B planner, type "ace") -> TextEncodeAceStepAudio1.5,
whose planner writes the song's audio codes from the caption, the lyrics and the metadata (bpm, duration, time
signature, language, key); a zeroed negative; EmptyAceStep1.5LatentAudio; VAEDecodeAudio.

Stable Audio 3 (audio_stable_audio_3_medium.json, the subgraph without its Qwen prompt rewriter - the assistant
writes the prompt itself): CheckpointLoaderSimple (model and VAE), CLIPLoader (T5Gemma, type "stable_audio"),
the prompt and an empty negative, EmptyLatentAudio, KSampler (8 steps, cfg 1, lcm/simple), VAEDecodeAudio.

Both end in SaveAudioAdvanced: mp3 (V0), flac or opus.
"""
from __future__ import annotations

import re

ACE_LANGUAGES = ("ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es", "fa", "fi", "fr", "he", "hi",
                 "hr", "ht", "hu", "id", "is", "it", "ja", "ko", "la", "lt", "ms", "ne", "nl", "no", "pa", "pl",
                 "pt", "ro", "ru", "sa", "sk", "sr", "sv", "sw", "ta", "te", "th", "tl", "tr", "uk", "ur", "vi",
                 "yue", "zh")
ACE_KEYS = tuple(f"{root} {quality}" for quality in ("major", "minor")
                 for root in ("C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#",
                              "Bb", "B"))
ACE_TIME_SIGNATURES = ("2", "3", "4", "6")
SAVE_FORMATS = {"mp3": {"format": "mp3", "format.quality": "V0"}, "flac": {"format": "flac"},
                "ogg": {"format": "opus", "format.quality": "128k"}}
# What the official Stable Audio prompt rewriter adds for each kind of sound, as the dataset's own tags
# (Stability's prompt guide: "TrackType: SFX" and friends steer it).
SA3_TAGS = {"sfx": "TrackType: SFX", "ambience": "TrackType: SFX", "one-shot": "TrackType: SFX",
            "loop": "TrackType: Instrument", "instrument": "TrackType: Instrument",
            "music": "TrackType: Music, VocalType: Instrumental"}


def _save(audio_ref: list, fmt: str, prefix: str) -> dict:
    return {"class_type": "SaveAudioAdvanced",
            "inputs": {"audio": audio_ref, "filename_prefix": prefix, **SAVE_FORMATS[fmt]}}


def ace_key(key: str | None) -> str:
    """'a minor', 'F#m', 'Bb' -> one of ACE-Step's keys ('A minor', 'F# minor', 'Bb major'); C major when unknown."""
    raw = (key or "").strip().replace("♯", "#").replace("♭", "b")
    m = re.fullmatch(r"([A-Ga-g])\s*([#b]?)\s*(.*)", raw)
    if not m:
        return "C major"
    rest = m.group(3).strip().lower().rstrip(".")
    quality = "minor" if rest in ("m", "mi", "min", "minor", "moll", "mol") or rest.startswith("minor") else "major"
    name = f"{m.group(1).upper()}{m.group(2)} {quality}"
    return name if name in ACE_KEYS else "C major"


def ace_step(*, caption: str, lyrics: str, seconds: float, seed: int, bpm: int, key: str, time_signature: int,
             language: str, unet: str, text_encoder: str, planner: str, vae: str, steps: int = 8, cfg: float = 1.0,
             shift: float = 3.0, fmt: str = "mp3", prefix: str = "audio/hub") -> dict:
    seconds = float(max(5.0, min(600.0, seconds)))
    lang = language if language in ACE_LANGUAGES else "unknown"
    ts = str(time_signature) if str(time_signature) in ACE_TIME_SIGNATURES else "4"
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": float(shift)}},
        "3": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": text_encoder, "clip_name2": planner,
                                                         "type": "ace", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "5": {"class_type": "TextEncodeAceStepAudio1.5",
              "inputs": {"clip": ["3", 0], "tags": caption, "lyrics": lyrics, "seed": int(seed) % 2**63,
                         "bpm": int(max(10, min(300, bpm))), "duration": seconds, "timesignature": ts,
                         "language": lang, "keyscale": ace_key(key), "generate_audio_codes": True,
                         "cfg_scale": 2.0, "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0.0}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": seconds, "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["2", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
                         "seed": int(seed) % 2**63, "steps": int(steps), "cfg": float(cfg), "sampler_name": "euler",
                         "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["8", 0], "vae": ["4", 0]}},
        "10": _save(["9", 0], fmt, prefix),
    }


def sa3_prompt(prompt: str, kind: str, seconds: float, bpm: int | None = None) -> str:
    """The prompt as the official rewriter writes it: the dataset tag first, then the description, the BPM for
    music and the length ("... BPM: 120. Length: 8 seconds")."""
    text = prompt.strip().rstrip(".")
    tag = SA3_TAGS.get(kind, SA3_TAGS["sfx"])
    if not text.lower().startswith("tracktype:"):
        text = f"{tag}, {text}"
    if bpm and "bpm" not in text.lower():
        text += f". BPM: {int(bpm)}"
    if "length:" not in text.lower():
        text += f". Length: {max(1, round(seconds))} seconds"
    return text


def stable_audio3(*, prompt: str, seconds: float, seed: int, checkpoint: str, text_encoder: str, steps: int = 8,
                  cfg: float = 1.0, negative: str = "", fmt: str = "mp3", prefix: str = "audio/hub") -> dict:
    seconds = float(max(0.5, min(380.0, seconds)))
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder, "type": "stable_audio",
                                                     "device": "default"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "5": {"class_type": "EmptyLatentAudio", "inputs": {"seconds": seconds, "batch_size": 1}},
        "6": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0],
                         "seed": int(seed) % 2**63, "steps": int(steps), "cfg": float(cfg), "sampler_name": "lcm",
                         "scheduler": "simple", "denoise": 1.0}},
        "7": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": _save(["7", 0], fmt, prefix),
    }


def class_types(graph: dict) -> set[str]:
    return {n["class_type"] for n in graph.values()}
