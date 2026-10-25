"""Sound: music and sound effects on the card (ComfyUI, through the scheduler like pictures), speech on the CPU
(the speech service). Each call returns the finished file's bytes; the tools put it in the working folder."""
from __future__ import annotations

import base64
import random
import time
from pathlib import Path

from ..gpu.comfy import ComfyError, input_images
from ..gpu.scheduler import SchedulerError, Ticket
from ..images.pipeline import EPHEMERAL
from ..state import Hub
from . import workflows as W
from .speech import SpeechClient, SpeechError, describe_voice


class AudioError(RuntimeError):
    pass


ABILITIES = ("music", "sound", "speech", "voice", "separate")
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


def models_path(entry: dict, role: str) -> str:
    """A file's path inside ComfyUI's models folder itself (separation/MelBandRoformer.ckpt) - how the Hub's own
    nodes take their files; ComfyUI's loaders take the name inside their folder type instead (comfy_name)."""
    f = role_files(entry, role)
    if not f:
        raise AudioError(f"{entry.get('name')} has no {role} file in the catalog")
    parts = Path(f[0]["dest"]).parts
    if len(parts) < 2 or parts[0] != "comfy":
        raise AudioError(f"{entry.get('name')}'s {role} file is not in ComfyUI's models folder")
    return "/".join(parts[1:])


def models_folder(entry: dict, role: str) -> str:
    """The folder of a model kept as several files (whisper-small, BigVGAN), inside ComfyUI's models folder."""
    return Path(models_path(entry, role)).parent.as_posix()


def output(files: list[tuple[str, bytes]], stem: str) -> bytes | None:
    """The file a SaveAudioAdvanced node with filename_prefix audio/<stem> wrote (ComfyUI: <stem>_00001.mp3)."""
    return next((data for name, data in files if Path(name).name.startswith(stem + "_")), None)


def node_text(texts: dict, node: str) -> dict:
    """A node's JSON text output (SLQSeedVC reports the pitch shift it made), {} when there is none."""
    import json
    try:
        return json.loads((texts.get(node) or ["{}"])[0])
    except (TypeError, ValueError):
        return {}


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
        if missing and all(n.startswith("SLQ") for n in missing):
            raise AudioError(f"ComfyUI does not have the Hub's own nodes ({', '.join(missing)}): its image is older "
                             "than this Hub - update.sh rebuilds it")
        if missing:
            raise AudioError(f"ComfyUI does not know these nodes: {', '.join(missing)} - update ComfyUI "
                             "(COMFYUI_REF in .env) or the workflow")

    async def _run(self, graph: dict, ticket: Ticket | None, owner: str, priority: str, label: str,
                   on_progress=None) -> tuple[bytes, float]:
        files, _, took = await self._run_all(graph, ticket, owner, priority, label, on_progress)
        return files[0][1], took

    async def _run_all(self, graph: dict, ticket: Ticket | None, owner: str, priority: str, label: str,
                       on_progress=None) -> tuple[list[tuple[str, bytes]], dict, float]:
        """Run a graph on the card through the scheduler: every file its saving nodes wrote, the text outputs by
        node, the seconds it took. The sound files it loaded are copies of the user's (the originals stay in the
        working folder), so they leave ComfyUI's input folder afterwards in every chat - a song is tens of MB."""
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
            files, texts = await self.hub.comfy.run_job(graph, on_progress=on_progress, keep=not EPHEMERAL.get(),
                                                        kind="audio", timeout=3600)
            return files, texts, time.monotonic() - t0
        except (ComfyError, SchedulerError) as exc:
            raise AudioError(str(exc))
        finally:
            if own is not None:
                await own.release()
            self.hub.comfy.delete_files([("input", "", n) for n in input_images(graph)])

    async def _upload(self, sounds: dict[str, bytes]) -> dict[str, str]:
        """Sound files (FLAC, made from the user's in the sandbox) into ComfyUI's input folder: key -> the name
        LoadAudio takes."""
        names: dict[str, str] = {}
        stamp = int(time.time() * 1000)
        try:
            for key, data in sounds.items():
                names[key] = await self.hub.comfy.upload_image(data, f"hub-{stamp}-{key}.flac", "audio/flac")
        except ComfyError as exc:
            self.hub.comfy.delete_files([("input", "", n) for n in names.values()])
            raise AudioError(str(exc))
        return names

    def _ace_files(self, entry: dict) -> dict:
        s = entry.get("settings") or {}
        return {"unet": comfy_name(entry, "unet"), "text_encoder": comfy_name(entry, "text_encoder"),
                "planner": comfy_name(entry, "planner"), "vae": comfy_name(entry, "vae"),
                "steps": int(s.get("steps", 8)), "cfg": float(s.get("cfg", 1.0)), "shift": float(s.get("shift", 3.0))}

    async def music(self, *, prompt: str, lyrics: str = "", seconds: float = 60, bpm: int | None = None,
                    key: str | None = None, time_signature: int = 4, language: str = "en", seed: int | None = None,
                    model: str | None = None, fmt: str = "mp3", reference: bytes | None = None,
                    ticket: Ticket | None = None, owner: str = "web", priority: str = "interactive",
                    on_progress=None) -> dict:
        """reference: a clip (FLAC) whose sound the song takes on - its timbre, not its structure."""
        entry = self.chosen("music", model)
        if fmt not in COMFY_FORMATS:
            raise AudioError(f"music can be saved as {', '.join(COMFY_FORMATS)}")
        sd = int(seed) if seed is not None else random.randint(1, 2**31 - 1)
        bpm_used = int(bpm) if bpm else 110
        files = self._ace_files(entry)
        names = await self._upload({"reference": reference}) if reference else {}
        graph = W.ace_step(caption=prompt, lyrics=lyrics.strip() or "[Instrumental]", seconds=seconds, seed=sd,
                           bpm=bpm_used, key=key or "C major", time_signature=int(time_signature or 4),
                           language=(language or "en").lower(), fmt=fmt, timbre=names.get("reference"), **files)
        data, took = await self._run(graph, ticket, owner, priority, f"music: {prompt[:60]}", on_progress)
        node = graph["5"]["inputs"]
        return {"data": data, "seconds": round(float(node["duration"]), 1), "compute": round(took, 1),
                "model": entry, "seed": sd, "bpm": node["bpm"], "key": node["keyscale"],
                "time_signature": node["timesignature"], "language": node["language"], "format": fmt}

    async def remake(self, *, song: bytes, seconds: float, prompt: str, lyrics: str = "",
                     reference: bytes | None = None, keep: float = 0.5, bpm: int | None = None, key: str | None = None,
                     time_signature: int | None = None, language: str = "en", seed: int | None = None,
                     model: str | None = None, fmt: str = "mp3", ticket: Ticket | None = None, owner: str = "web",
                     priority: str = "interactive", on_progress=None) -> dict:
        """A new version of a song (FLAC): its melody, rhythm and structure (ACE-Step's codes of it), the sound
        from the prompt and a reference clip - or the song's own; keep > 0 starts from the song itself instead of
        noise. "codes" is SLQAceStepReference's check of the song's codes against ACE-Step's own tokenizer."""
        entry = self.chosen("music", model)
        if fmt not in COMFY_FORMATS:
            raise AudioError(f"music can be saved as {', '.join(COMFY_FORMATS)}")
        sd = int(seed) if seed is not None else random.randint(1, 2**31 - 1)
        files = self._ace_files(entry)
        names = await self._upload({"song": song, **({"reference": reference} if reference else {})})
        graph = W.ace_remake(song=names["song"], reference=names.get("reference"), caption=prompt, lyrics=lyrics,
                             seconds=seconds, seed=sd, bpm=bpm, key=key, time_signature=time_signature,
                             language=(language or "en").lower(), keep=keep, fmt=fmt, **files)
        files, texts, took = await self._run_all(graph, ticket, owner, priority, f"remake: {prompt[:60]}",
                                                 on_progress)
        enc = graph["5"]["inputs"]
        return {"data": files[0][1], "seconds": round(float(enc["duration"]), 1), "compute": round(took, 1),
                "model": entry, "seed": sd, "bpm": enc["bpm"] or None, "key": enc["keyscale"] or None,
                "time_signature": int(enc["timesignature"]) if enc["timesignature"] else None,
                "language": enc["language"], "keep": 1.0 - float(graph["8"]["inputs"]["denoise"]), "format": fmt,
                "own_sound": not reference, "codes": node_text(texts, "15")}

    async def separate(self, *, audio: bytes, stems: tuple[str, ...] = ("vocals", "accompaniment"),
                       model: str | None = None, fmt: str = "mp3", ticket: Ticket | None = None, owner: str = "web",
                       priority: str = "interactive", on_progress=None) -> dict:
        """A song's vocals and/or accompaniment: {stem: bytes, ..., "compute": s, "model": entry}."""
        entry = self.chosen("separate", model)
        if fmt not in COMFY_FORMATS:
            raise AudioError(f"stems can be saved as {', '.join(COMFY_FORMATS)}")
        weights = models_path(entry, "separator")
        names = await self._upload({"song": audio})
        graph = W.separate(audio=names["song"], model=weights,
                           config=str((entry.get("settings") or {}).get("config") or ""), stems=stems, fmt=fmt)
        files, _, took = await self._run_all(graph, ticket, owner, priority, "stems", on_progress)
        out: dict = {"compute": round(took, 1), "model": entry, "format": fmt}
        for stem in stems:
            data = output(files, f"hub-{stem}")
            if data is None:
                raise AudioError(f"the separation finished without the {stem}")
            out[stem] = data
        return out

    async def convert_voice(self, *, audio: bytes, voice: bytes, singing: bool = True, music: bool = True,
                            pitch: int = 0, auto_octave: bool = True, steps: int | None = None,
                            vocals_only: bool = False, model: str | None = None, fmt: str = "mp3",
                            ticket: Ticket | None = None, owner: str = "web", priority: str = "interactive",
                            on_progress=None) -> dict:
        """The voice in `audio` (FLAC) made to sound like the one in `voice` (FLAC): Seed-VC, the singing model
        (melody kept) or the speech one; with music the vocals are separated, converted and mixed back."""
        entry = self.chosen("voice", model)
        if fmt not in COMFY_FORMATS:
            raise AudioError(f"converted voices can be saved as {', '.join(COMFY_FORMATS)}")
        sep = self.installed("separate")
        if music and not sep:
            raise AudioError("a voice in music has to be separated from it first: download Mel-Band RoFormer vocals "
                             "(Settings -> Models -> Sound, music and speech)")
        s = entry.get("settings") or {}
        kind = "singing" if singing else "speech"
        files = {"dit": models_path(entry, f"dit_{kind}"), "config": str(s.get(f"config_{kind}") or ""),
                 "whisper": models_folder(entry, "whisper"), "vocoder": models_folder(entry, f"vocoder_{kind}"),
                 "rmvpe": models_path(entry, "rmvpe") if singing else "",
                 "separator": models_path(sep[0], "separator") if sep else None,
                 "separator_config": str((sep[0].get("settings") or {}).get("config") or "") if sep else ""}
        names = await self._upload({"audio": audio, "voice": voice})
        graph = W.seed_vc(audio=names["audio"], voice=names["voice"], music=music,
                          steps=int(steps or s.get(f"steps_{kind}") or 30), cfg=float(s.get("cfg", 0.7)),
                          pitch=int(pitch) if singing else 0, auto_octave=bool(auto_octave) and singing,
                          vocals_only=vocals_only and music, fmt=fmt, **files)
        files, texts, took = await self._run_all(graph, ticket, owner, priority, "voice conversion", on_progress)
        data = output(files, "hub-mix" if music else "hub-voice")
        if data is None:
            raise AudioError("the voice conversion finished without its file")
        info = node_text(texts, "5")
        return {"data": data, "vocals": output(files, "hub-vocals") if vocals_only and music else None,
                "compute": round(took, 1), "model": entry, "separated": bool(sep), "singing": singing,
                "music": music, "semitones": int(info.get("semitones") or 0),
                "reference_seconds": info.get("reference_seconds"), "seconds": info.get("seconds"),
                "steps": graph["5"]["inputs"]["steps"], "format": fmt}

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
        notes: list[str] = []
        body = {"model": {"key": entry["id"], "family": entry["family"], "files": self._speech_files(entry)},
                "text": text, "language": (language or "da").lower(), "voice": voice_spec, "seed": sd,
                "format": fmt, **p,
                # a clip's voice prompt may wait in the service for the next request - not in a person's chat
                "cache_voice": not EPHEMERAL.get()}
        try:
            done = await self.speech.speak(body, on_progress=on_progress)
        except SpeechError as exc:
            raise AudioError(str(exc))
        pieces, capped = int(done.get("pieces") or 1), int(done.get("capped") or 0)
        if capped:
            notes.append(f"The model did not stop by itself on {capped} of {pieces} piece(s): it ran to its length "
                         "limit, so the end of that piece may be noise - another seed usually fixes it.")
        return {"data": done["audio"], "seconds": float(done.get("seconds") or 0), "compute":
                float(done.get("compute") or 0), "model": entry, "voice": voice_name, "seed": sd, "format": fmt,
                "note": " ".join(x for x in (note, *notes) if x), "pieces": pieces, "capped": capped,
                "threads": done.get("threads"), "tokens": done.get("tokens"), "t3_seconds": done.get("t3_seconds"),
                "decoder_seconds": done.get("decoder_seconds")}
