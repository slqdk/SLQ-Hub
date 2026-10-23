"""Sound: music and sound effects on the card (ComfyUI, through the scheduler like pictures), speech on the CPU
(the speech service). Each call returns the finished file's bytes; the tools put it in the working folder."""
from __future__ import annotations

import base64
import random
import time
from pathlib import Path

from ..gpu.comfy import ComfyError
from ..gpu.scheduler import SchedulerError, Ticket
from ..images.pipeline import EPHEMERAL
from ..state import Hub
from . import workflows as W
from .speech import SpeechClient, SpeechError, describe_voice


class AudioError(RuntimeError):
    pass


ABILITIES = ("music", "sound", "speech")
COMFY_FORMATS = ("mp3", "flac", "ogg")
SPEECH_FORMATS = ("mp3", "wav", "flac", "ogg")
MAX_VOICE_BYTES = 25 * 1024 * 1024
# What a model does with a language it was not made for.
ACCENT_NOTE = ("{model} speaks {language} with a heavy Danish accent; Chatterbox Multilingual V3 (Settings -> "
               "Models) speaks it natively.")
LANGUAGE_NAMES = {"da": "Danish", "en": "English", "de": "German", "sv": "Swedish", "no": "Norwegian",
                  "fr": "French", "es": "Spanish", "it": "Italian", "nl": "Dutch", "fi": "Finnish", "pl": "Polish",
                  "pt": "Portuguese", "ru": "Russian", "tr": "Turkish", "el": "Greek", "ar": "Arabic",
                  "he": "Hebrew", "hi": "Hindi", "ja": "Japanese", "ko": "Korean", "ms": "Malay", "sw": "Swahili",
                  "zh": "Chinese"}
# Chatterbox's multilingual tokenizer has these language tokens (Røst v3 kept them).
MULTILINGUAL = ("ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl", "no", "pl",
                "pt", "ru", "sv", "sw", "tr", "zh")


def abilities(entry: dict | None) -> list[str]:
    return list((entry or {}).get("abilities") or [])


def role_files(entry: dict, role: str) -> list[dict]:
    return [f for f in entry.get("files") or [] if f.get("role") == role]


def comfy_name(entry: dict, role: str) -> str:
    """A file's name as ComfyUI lists it: its path inside its models folder (comfy/<folder>/<name>)."""
    f = role_files(entry, role)
    if not f:
        raise AudioError(f"{entry.get('name')} has no {role} file in the catalog")
    parts = Path(f[0]["dest"]).parts
    return "/".join(parts[2:]) if len(parts) > 2 and parts[0] == "comfy" else parts[-1]


def speech_files(entry: dict, root: str) -> dict:
    """A speech model's files as the speech service sees them (root: the models folder there): the tokenizer is
    a file for the multilingual family and a folder for Turbo."""
    files: dict = {}
    for role in ("t3", "s3gen", "ve", "conds"):
        f = role_files(entry, role)
        if f:
            files[role] = f"{root}/{f[0]['dest']}"
    tok = role_files(entry, "tokenizer")
    if tok:
        dest = tok[0]["dest"]
        files["tokenizer"] = f"{root}/{dest}" if entry.get("family") == "chatterbox-mtl" else \
            f"{root}/{Path(dest).parent.as_posix()}"
    return files


class AudioPipeline:
    def __init__(self, hub: Hub):
        self.hub = hub
        self.speech = SpeechClient(hub.env.speech_url, transport=hub.transports.speech)

    # ------------------------------------------------------------ models
    def entry(self, model: str | None) -> dict | None:
        return self.hub.catalog.get("audio", model) if model else None

    def installed(self, ability: str) -> list[dict]:
        """Downloaded models that can do something (music, sound, speech), in catalog order."""
        return [e for e in self.hub.catalog.entries("audio")
                if ability in abilities(e) and self.hub.catalog.installed(e)]

    def available(self, ability: str) -> bool:
        return bool(self.installed(ability))

    def chosen(self, ability: str, wanted: str | None = None) -> dict:
        """The model asked for (when it is downloaded and can do this), else Settings -> Assistant -> Sound, else
        the first downloaded one."""
        models = self.installed(ability)
        if not models:
            raise AudioError(f"no {ability} model is downloaded (Settings -> Models -> Sound, music and speech)")
        by_id = {e["id"]: e for e in models}
        if wanted:
            if wanted in by_id:
                return by_id[wanted]
            other = self.entry(wanted)
            if other is None:
                raise AudioError(f"there is no sound model called {wanted}; downloaded: {', '.join(by_id)}")
            if ability not in abilities(other):
                raise AudioError(f"{other['name']} does not make {ability}; use one of {', '.join(by_id)}")
            raise AudioError(f"{other['name']} is not downloaded (Settings -> Models); downloaded: "
                             f"{', '.join(by_id)}")
        setting = str(self.hub.settings.get("sound", f"{ability}_model") or "")
        return by_id.get(setting) or models[0]

    def speech_model(self, language: str, wanted: str | None = None) -> tuple[dict, str]:
        """The speech model for a language, and a note when it is a poor fit (Røst speaking English)."""
        lang = (language or "").lower()
        models = self.installed("speech")
        if not models:
            raise AudioError("no speech model is downloaded (Settings -> Models -> Sound, music and speech)")
        if wanted:
            entry = self.chosen("speech", wanted)
        else:
            # Settings -> Assistant -> Sound, unless that model does not speak the language and another one does
            setting = str(self.hub.settings.get("sound", "speech_model_da" if lang == "da" else "speech_model") or "")
            fits = [e for e in models if lang in (e.get("languages") or [])]
            entry = next((e for e in models if e["id"] == setting), None)
            if entry is None or (entry not in fits and fits):
                entry = fits[0] if fits else models[0]
        speaks = entry.get("languages") or []
        if lang in speaks:
            return entry, ""
        if entry.get("family") == "chatterbox-mtl" and lang not in MULTILINGUAL:
            raise AudioError(f"{entry['name']} cannot speak {language!r}; it knows "
                             f"{', '.join(sorted(set(MULTILINGUAL)))}")
        return entry, ACCENT_NOTE.format(model=entry["name"], language=LANGUAGE_NAMES.get(lang, language))

    def voices(self, entry: dict) -> dict[str, str]:
        """A speech model's own voices: name -> what it sounds like ('male voice', or '' when unknown)."""
        out = {}
        if role_files(entry, "conds"):
            out["default"] = ""
        for f in role_files(entry, "voice"):
            out[f["voice"]] = describe_voice(self.hub.catalog.path(f))
        return out

    # ------------------------------------------------------------ ComfyUI
    async def _validate(self, graph: dict) -> None:
        try:
            info = await self.hub.comfy.object_info()
        except ComfyError as exc:
            raise AudioError(str(exc))
        missing = sorted(W.class_types(graph) - set(info))
        if missing:
            raise AudioError(f"ComfyUI does not know these nodes: {', '.join(missing)} - update ComfyUI "
                             "(COMFYUI_REF in .env) or the workflow")

    async def _run(self, graph: dict, ticket: Ticket | None, owner: str, priority: str, label: str,
                   on_progress=None) -> tuple[bytes, float]:
        own = None
        t0 = time.monotonic()
        try:
            if ticket is not None:
                await ticket.switch("comfy")
            else:
                own = await self.hub.scheduler.acquire(owner, "audio", "comfy", priority, label=label,
                                                       max_wait=float(self.hub.settings.get("queue", "max_wait_s")))
            await self._validate(graph)
            t0 = time.monotonic()
            files, _ = await self.hub.comfy.run_job(graph, on_progress=on_progress, keep=not EPHEMERAL.get(),
                                                    kind="audio", timeout=3600)
            return files[0][1], time.monotonic() - t0
        except (ComfyError, SchedulerError) as exc:
            raise AudioError(str(exc))
        finally:
            if own is not None:
                await own.release()

    async def music(self, *, prompt: str, lyrics: str = "", seconds: float = 60, bpm: int | None = None,
                    key: str | None = None, time_signature: int = 4, language: str = "en", seed: int | None = None,
                    model: str | None = None, fmt: str = "mp3", ticket: Ticket | None = None, owner: str = "web",
                    priority: str = "interactive", on_progress=None) -> dict:
        entry = self.chosen("music", model)
        if fmt not in COMFY_FORMATS:
            raise AudioError(f"music can be saved as {', '.join(COMFY_FORMATS)}")
        s = entry.get("settings") or {}
        sd = int(seed) if seed is not None else random.randint(1, 2**31 - 1)
        bpm_used = int(bpm) if bpm else 110
        graph = W.ace_step(caption=prompt, lyrics=lyrics.strip() or "[Instrumental]", seconds=seconds, seed=sd,
                           bpm=bpm_used, key=key or "C major", time_signature=int(time_signature or 4),
                           language=(language or "en").lower(), unet=comfy_name(entry, "unet"),
                           text_encoder=comfy_name(entry, "text_encoder"), planner=comfy_name(entry, "planner"),
                           vae=comfy_name(entry, "vae"), steps=int(s.get("steps", 8)), cfg=float(s.get("cfg", 1.0)),
                           shift=float(s.get("shift", 3.0)), fmt=fmt)
        data, took = await self._run(graph, ticket, owner, priority, f"music: {prompt[:60]}", on_progress)
        node = graph["5"]["inputs"]
        return {"data": data, "seconds": round(float(node["duration"]), 1), "compute": round(took, 1),
                "model": entry, "seed": sd, "bpm": node["bpm"], "key": node["keyscale"],
                "time_signature": node["timesignature"], "language": node["language"], "format": fmt}

    async def sound(self, *, prompt: str, kind: str = "sfx", seconds: float = 5, bpm: int | None = None,
                    seed: int | None = None, model: str | None = None, fmt: str = "mp3", negative: str = "",
                    ticket: Ticket | None = None, owner: str = "web", priority: str = "interactive",
                    on_progress=None) -> dict:
        entry = self.chosen("sound", model)
        if fmt not in COMFY_FORMATS:
            raise AudioError(f"sound can be saved as {', '.join(COMFY_FORMATS)}")
        s = entry.get("settings") or {}
        sd = int(seed) if seed is not None else random.randint(1, 2**31 - 1)
        kind = kind if kind in W.SA3_TAGS else "sfx"
        text = W.sa3_prompt(prompt, kind, seconds, bpm if kind in ("music", "loop", "instrument") else None)
        graph = W.stable_audio3(prompt=text, seconds=seconds, seed=sd, checkpoint=comfy_name(entry, "checkpoint"),
                                text_encoder=comfy_name(entry, "text_encoder"), steps=int(s.get("steps", 8)),
                                cfg=float(s.get("cfg", 1.0)), negative=negative, fmt=fmt)
        data, took = await self._run(graph, ticket, owner, priority, f"sound: {prompt[:60]}", on_progress)
        return {"data": data, "seconds": round(float(graph["5"]["inputs"]["seconds"]), 1), "compute": round(took, 1),
                "model": entry, "seed": sd, "prompt_used": text, "format": fmt}

    # ------------------------------------------------------------ speech
    def _speech_files(self, entry: dict) -> dict:
        return speech_files(entry, self.hub.env.speech_models)

    def _voice(self, entry: dict, voice: str | bytes | None) -> tuple[dict | None, str]:
        """(the request's voice, its name for the result). Bytes are a clip the caller read; a name is one of the
        model's voices; nothing is the model's default (its built-in voice, else its first voice)."""
        if isinstance(voice, (bytes, bytearray)):
            if len(voice) > MAX_VOICE_BYTES:
                raise AudioError("the voice clip is over 25 MB; 10-20 seconds of speech is enough")
            return {"audio": base64.b64encode(bytes(voice)).decode()}, "the given clip"
        own = {f["voice"]: f for f in role_files(entry, "voice")}
        name = (voice or "").strip().lower()
        if not name:
            name = "default" if role_files(entry, "conds") else next(iter(own), "")
        if name == "default" and role_files(entry, "conds"):
            return None, "default"
        if name in own:
            return {"path": f"{self.hub.env.speech_models}/{own[name]['dest']}"}, name
        names = ", ".join(self.voices(entry)) or "none"
        raise AudioError(f"{entry['name']} has no voice called {voice!r} (its voices: {names}); a voice can also be "
                         "the path of a clip in the working folder")

    async def speak(self, *, text: str, language: str, voice: str | bytes | None = None,
                    expressiveness: float | None = None, seed: int | None = None, model: str | None = None,
                    fmt: str = "mp3", on_progress=None) -> dict:
        if fmt not in SPEECH_FORMATS:
            raise AudioError(f"speech can be saved as {', '.join(SPEECH_FORMATS)}")
        entry, note = self.speech_model(language, model)
        voice_spec, voice_name = self._voice(entry, voice)
        p = dict(entry.get("sampling") or {})
        if entry.get("expressive") and expressiveness is not None:
            ex = max(0.25, min(2.0, float(expressiveness)))
            p["exaggeration"] = ex
            if ex >= 0.65:                    # Chatterbox's own advice: more emotion, slower guidance
                p["cfg_weight"] = min(float(p.get("cfg_weight", 0.5)), 0.3)
        sd = int(seed) if seed is not None else random.randint(1, 2**31 - 1)
        body = {"model": {"key": entry["id"], "family": entry["family"], "files": self._speech_files(entry)},
                "text": text, "language": (language or "da").lower(), "voice": voice_spec, "seed": sd,
                "format": fmt, **p,
                # a clip's voice prompt may wait in the service for the next request - not in a person's chat
                "cache_voice": not EPHEMERAL.get()}
        try:
            done = await self.speech.speak(body, on_progress=on_progress)
        except SpeechError as exc:
            raise AudioError(str(exc))
        return {"data": done["audio"], "seconds": float(done.get("seconds") or 0), "compute":
                float(done.get("compute") or 0), "model": entry, "voice": voice_name, "seed": sd, "format": fmt,
                "note": note, "pieces": int(done.get("pieces") or 1), "threads": done.get("threads")}
