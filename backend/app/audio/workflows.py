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

SLQ Hub's own nodes (services/comfyui/nodes/slq_audio) add four graphs:
- a song with a reference sound (ace_step(timbre=...)): the clip, VAE-encoded, goes to SLQAceStepReference as
  timbre only - the planner still writes the structure;
- a remake (ace_remake): the song, VAE-encoded, is the cover source (its codes are the structure) and the latent
  the sampler starts from (denoise 1 - keep), with a style clip as timbre; SLQAceStepEncode leaves the planner out
  and unknown metadata as N/A;
- stems (separate): SLQSeparateVocals (Mel-Band RoFormer) -> vocals and accompaniment;
- another voice (seed_vc): the vocals separated when there is music, SLQSeedVC with the separated reference, the
  accompaniment added back with AudioMerge.
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
             shift: float = 3.0, fmt: str = "mp3", prefix: str = "audio/hub", timbre: str | None = None) -> dict:
    """timbre: a clip in ComfyUI's input folder whose sound the song takes on (SLQAceStepReference)."""
    seconds = float(max(5.0, min(600.0, seconds)))
    lang = language if language in ACE_LANGUAGES else "unknown"
    ts = str(time_signature) if str(time_signature) in ACE_TIME_SIGNATURES else "4"
    graph = {
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
    if timbre:
        graph.update(_encoded("11", timbre))
        graph["13"] = {"class_type": "SLQAceStepReference", "inputs": {"model": ["2", 0], "timbre": ["12", 0]}}
        graph["8"]["inputs"]["model"] = ["13", 0]
    return graph


def _encoded(first: str, audio: str) -> dict:
    """LoadAudio (a file in ComfyUI's input folder) -> VAEEncodeAudio with the graph's VAE (node 4)."""
    nxt = str(int(first) + 1)
    return {first: {"class_type": "LoadAudio", "inputs": {"audio": audio}},
            nxt: {"class_type": "VAEEncodeAudio", "inputs": {"audio": [first, 0], "vae": ["4", 0]}}}


def ace_remake(*, song: str, caption: str, lyrics: str, seconds: float, seed: int, bpm: int | None, key: str | None,
               time_signature: int | None, language: str, unet: str, text_encoder: str, planner: str, vae: str,
               keep: float = 0.0, reference: str | None = None, steps: int = 8, cfg: float = 1.0, shift: float = 3.0,
               fmt: str = "mp3", prefix: str = "audio/hub") -> dict:
    """A new version of a song (a file in ComfyUI's input folder): its codes are the structure, the sampler starts
    from it (keep 0: from noise; up to 0.9: from the song itself), a reference clip gives the sound."""
    seconds = float(max(5.0, min(600.0, seconds)))
    lang = language if language in ACE_LANGUAGES else "unknown"
    ts = str(time_signature) if str(time_signature or "") in ACE_TIME_SIGNATURES else ""
    keep = max(0.0, min(0.9, float(keep)))
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": float(shift)}},
        "3": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": text_encoder, "clip_name2": planner,
                                                         "type": "ace", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "5": {"class_type": "SLQAceStepEncode",
              "inputs": {"clip": ["3", 0], "tags": caption, "lyrics": lyrics, "language": lang, "duration": seconds,
                         "seed": int(seed) % 2**63, "bpm": int(max(10, min(300, bpm))) if bpm else 0,
                         "keyscale": ace_key(key) if key else "", "timesignature": ts}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        **_encoded("11", song),
        "15": {"class_type": "SLQAceStepReference", "inputs": {"model": ["2", 0], "source": ["12", 0]}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["15", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["12", 0],
                         "seed": int(seed) % 2**63, "steps": int(steps), "cfg": float(cfg), "sampler_name": "euler",
                         "scheduler": "simple", "denoise": round(1.0 - keep, 3)}},
        "9": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["8", 0], "vae": ["4", 0]}},
        "10": _save(["9", 0], fmt, prefix),
    }
    if reference:
        graph.update(_encoded("13", reference))
        graph["15"]["inputs"]["timbre"] = ["14", 0]
    return graph


def _separate(audio_ref: list, model: str, config: str) -> dict:
    return {"class_type": "SLQSeparateVocals",
            "inputs": {"audio": audio_ref, "model": model, "config": config, "overlap": 2, "batch": 2}}


def separate(*, audio: str, model: str, config: str, stems: tuple[str, ...] = ("vocals", "accompaniment"),
             fmt: str = "mp3", prefix: str = "audio/hub") -> dict:
    """Vocals and/or accompaniment of a file in ComfyUI's input folder; each saved under prefix-<stem>."""
    graph = {"1": {"class_type": "LoadAudio", "inputs": {"audio": audio}}, "2": _separate(["1", 0], model, config)}
    for i, stem in enumerate(("vocals", "accompaniment")):
        if stem in stems:
            graph[str(3 + i)] = _save(["2", i], fmt, f"{prefix}-{stem}")
    return graph


def seed_vc(*, audio: str, voice: str, dit: str, config: str, whisper: str, vocoder: str, rmvpe: str = "",
            separator: str | None = None, separator_config: str = "", music: bool = False, steps: int = 30,
            cfg: float = 0.7, pitch: int = 0, auto_octave: bool = True, vocals_only: bool = False,
            fmt: str = "mp3", prefix: str = "audio/hub") -> dict:
    """The voice in `audio` made to sound like the one in `voice` (files in ComfyUI's input folder). With a
    separator the voice clip is reduced to its vocals; with music the audio's vocals are converted and laid back
    on its accompaniment (saved as prefix-mix, the converted vocals as prefix-vocals), else saved as prefix-voice."""
    if music and not separator:
        raise ValueError("converting the voice in music needs the vocal separation model")
    graph: dict = {"1": {"class_type": "LoadAudio", "inputs": {"audio": audio}},
                   "2": {"class_type": "LoadAudio", "inputs": {"audio": voice}}}
    if separator:
        graph["4"] = _separate(["2", 0], separator, separator_config)
        if music:
            graph["3"] = _separate(["1", 0], separator, separator_config)
    graph["5"] = {"class_type": "SLQSeedVC",
                  "inputs": {"source": ["3", 0] if music else ["1", 0], "reference": ["4", 0] if separator else ["2", 0],
                             "dit": dit, "config": config, "whisper": whisper, "vocoder": vocoder, "rmvpe": rmvpe,
                             "steps": int(max(1, min(200, steps))), "cfg": float(max(0.0, min(1.0, cfg))),
                             "pitch": int(max(-24, min(24, pitch))), "auto_octave": bool(auto_octave),
                             "match_loudness": True, "reference_seconds": 25.0}}
    if music:
        graph["6"] = {"class_type": "AudioMerge",
                      "inputs": {"audio1": ["3", 1], "audio2": ["5", 0], "merge_method": "add"}}
        graph["7"] = _save(["6", 0], fmt, f"{prefix}-mix")
        if vocals_only:
            graph["8"] = _save(["5", 0], fmt, f"{prefix}-vocals")
    else:
        graph["7"] = _save(["5", 0], fmt, f"{prefix}-voice")
    return graph


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
