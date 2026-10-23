"""Sound: speech (the speech service, CPU), music and sound effects (ComfyUI, GPU). Each tool saves one audio
file in the working folder, where the card under the answer plays it."""
from __future__ import annotations

import re
import shlex
import time
import unicodedata
import uuid
from pathlib import Path

from .base import Tool, ToolContext, ToolResult, obj, register
from .shell import SandboxError, sandbox_exec

AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".webm", ".mp4", ".mka", ".aiff", ".aif"}
SPEECH_SAVE = (".mp3", ".wav", ".flac", ".ogg")
MAX_CLIP_BYTES = 200 * 1024 * 1024
COMFY_SAVE = (".mp3", ".flac", ".ogg")
FLAC_DEVICE_S = 120                   # the longest FLAC that surely fits the 25 MB a device turn sends back
_TRANSLIT = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                           "Æ": "ae", "Ø": "oe", "Å": "aa", "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def _pipe(ctx: ToolContext):
    return ctx.hub.extras.get("audio")


def slug(text: str, limit: int = 48) -> str:
    """A file name from a text: 'Tredive sekunder!' -> 'tredive-sekunder'; æ, ø, å as ae, oe, aa."""
    t = unicodedata.normalize("NFKD", (text or "").translate(_TRANSLIT)).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    if len(words) > limit:
        words = words[:limit].rsplit("-", 1)[0] or words[:limit]
    return words or "audio"


def _free(ctx: ToolContext, base: str) -> str:
    rel, n = f"{base}.mp3", 2
    while ctx.workdir.resolve(rel).exists():
        rel, n = f"{base}-{n}.mp3", n + 1
    return rel


def _target(ctx: ToolContext, save_as: str | None, stem: str, allowed: tuple[str, ...]) -> tuple[str, str]:
    """(path in the working folder, format). save_as wins and may overwrite (a folder gets a free name from the
    text); otherwise a free name from the text - in a project under audio/, in a chat's own folder at the top."""
    raw = (save_as or "").strip()
    if not raw:
        return _free(ctx, f"audio/{stem}" if ctx.project else stem), "mp3"
    p = ctx.workdir.resolve(raw)                     # a path in the folder or a sandbox path; outside: ValueError
    rel = ctx.workdir.rel(p)
    if raw.endswith("/") or p.is_dir():
        return _free(ctx, stem if rel == "." else f"{rel}/{stem}"), "mp3"
    ext = p.suffix.lower()
    if not ext:
        rel, ext = rel + ".mp3", ".mp3"
    if ext == ".opus":
        rel, ext = str(Path(rel).with_suffix(".ogg")), ".ogg"
    if ext not in allowed:
        raise ValueError(f"save_as must end in {', '.join(allowed)} (convert with ffmpeg in bash for others)")
    return rel, ext[1:]


def _for_device(ctx: ToolContext, fmt: str, seconds: float) -> None:
    """In a person's chat the file goes to her device, which takes files of up to 25 MB: a long FLAC would not get
    there (48 kHz stereo FLAC is up to about 190 kB a second)."""
    if fmt == "flac" and seconds > FLAC_DEVICE_S and getattr(ctx.engine, "device", None) is not None:
        raise ValueError(f"a FLAC file of {seconds:.0f} s would be too big to reach the user's device in this chat "
                         f"(25 MB a file): save it as .mp3 or .ogg, or make it at most {FLAC_DEVICE_S} s")


def _reach(ctx: ToolContext, data: bytes) -> str:
    """How the user gets the file, for the result."""
    from ..device import MAX_OUT_FILE
    if getattr(ctx.engine, "device", None) is not None and len(data) > MAX_OUT_FILE:
        return (f"But it is {len(data) / 2**20:.1f} MB, more than the {MAX_OUT_FILE / 2**20:g} MB a file may have to "
                "reach the user's device in this chat, so it will not get there: make it again as .mp3 or .ogg, or "
                "shorter.")
    return "The user can play it under your answer."


async def _progress(ctx: ToolContext):
    """Progress for the tool row (a sampler's steps, the planner's tokens, the speech service's sentences), at
    most twice a second - a song's planner reports every token."""
    last = {"at": 0.0, "max": 0}

    async def cb(value: int, maximum: int, node=None):
        now = time.monotonic()
        if value < maximum and maximum == last["max"] and now - last["at"] < 0.5:
            return
        last.update(at=now, max=maximum)
        ctx.emit("audio_progress", id=ctx.call_id, value=value, max=maximum)
    return cb


def _save(ctx: ToolContext, rel: str, data: bytes, what: str) -> None:
    ctx.workdir.write(rel, data, "agent", "hub", f"{what} {rel}")


def _number(args: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(args.get(key) if args.get(key) not in (None, "") else default)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number")
    return max(lo, min(hi, v))


async def _clip(ctx: ToolContext, voice: str) -> bytes | None:
    """A voice that names a file in the working folder: its first 30 s as a 24 kHz mono WAV. ffmpeg converts it in
    the sandbox (confined in a person's chat), the place for files from users, so no media parser reads them
    in the Hub or the speech service."""
    v = (voice or "").strip()
    if not v or ("/" not in v and Path(v).suffix.lower() not in AUDIO_EXT):
        return None
    wd = ctx.workdir
    p = wd.resolve(v)
    if not p.is_file():
        raise FileNotFoundError(v)
    if p.stat().st_size > MAX_CLIP_BYTES:
        raise ValueError("the voice clip is over 200 MB; 10-20 seconds of speech is enough")
    tmp = f".hub/voice-{uuid.uuid4().hex[:12]}.wav"
    wd.resolve(tmp).parent.mkdir(parents=True, exist_ok=True)
    src, dst = f"{wd.sandbox_path}/{wd.rel(p)}", f"{wd.sandbox_path}/{tmp}"
    # local files only: a playlist inside a crafted file cannot make ffmpeg fetch anything
    cmd = (f"ffmpeg -nostdin -hide_banner -loglevel error -protocol_whitelist file -y -i {shlex.quote(src)} -t 30 "
           f"-vn -ac 1 -ar 24000 -c:a pcm_s16le -f wav {shlex.quote(dst)}")
    try:
        res = await sandbox_exec(ctx, cmd, wd.sandbox_path, 90)
        out = wd.resolve(tmp)
        if res.get("exit_code") != 0 or not out.is_file() or out.stat().st_size <= 44:
            why = (res.get("stderr") or "").strip().splitlines()
            raise ValueError(f"the voice clip could not be read ({why[-1][:200] if why else 'not audio'})")
        return out.read_bytes()
    except SandboxError as exc:
        raise ValueError(f"the voice clip could not be converted: {exc}")
    finally:
        try:
            wd.resolve(tmp).unlink()
        except (OSError, ValueError):
            pass


# ---------------------------------------------------------------- speech

async def speech_generate(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipe(ctx)
    text = str(args.get("text") or "").strip()
    language = str(args.get("language") or "").strip().lower()
    if not text:
        return ToolResult("text is required", is_error=True)
    if not language:
        return ToolResult("language is required (an ISO code such as da or en)", is_error=True)
    from ...audio.pipeline import LANGUAGE_NAMES, AudioError
    try:
        voice: str | bytes | None = args.get("voice") or None
        clip = await _clip(ctx, voice) if isinstance(voice, str) else None
        if clip is not None:
            voice = clip
        rel, fmt = _target(ctx, args.get("save_as"), slug(text), SPEECH_SAVE)
        ex = _number(args, "expressiveness", 0.5, 0.25, 2.0) if args.get("expressiveness") is not None else None
        out = await pipe.speak(text=text, language=language, voice=voice, expressiveness=ex, seed=args.get("seed"),
                               model=args.get("model") or None, fmt=fmt, on_progress=await _progress(ctx))
    except FileNotFoundError as exc:
        return ToolResult(f"No such voice clip in the working folder: {exc}", is_error=True)
    except (ValueError, AudioError) as exc:
        return ToolResult(f"Speech failed: {exc}", is_error=True)
    _save(ctx, rel, out["data"], "spoke")
    lang = LANGUAGE_NAMES.get(language, language)
    note = f" Note: {out['note']}" if out.get("note") else ""
    return ToolResult(f"Saved {rel}: {out['seconds']:.1f} s of {lang} speech, {out['model']['name']}, voice "
                      f"{out['voice']}, seed {out['seed']} (made on the CPU in {out['compute']:.0f} s). "
                      f"{_reach(ctx, out['data'])}{note}",
                      display={"audio": [{"path": rel, "seconds": out["seconds"], "model": out["model"]["name"]}]})


def _speech_params(models: list[str], languages: list[str]) -> dict:
    model: dict = {"type": "string", "enum": models} if models else {"type": "string"}
    lang: dict = {"type": "string", "enum": languages} if languages else {"type": "string"}
    lang["description"] = "The language of the text (ISO code)."
    return obj({"text": {"type": "string", "description": "Exactly what to say, written as it should sound."},
                "language": lang,
                "voice": {"type": "string", "description": "One of the model's voices, or the path of a voice clip "
                                                           "in the working folder."},
                "expressiveness": {"type": "number", "description": "0.25-2, default 0.5 (models that support it)."},
                "model": model, "seed": {"type": "integer"},
                "save_as": {"type": "string", "description": "Path in the working folder: .mp3 (default), .wav, "
                                                             ".flac or .ogg"}}, ["text", "language"])


SPEECH_TEXT = ("Speak text aloud: an audio file in the working folder that the user can play under your answer. "
               "Runs on the server's CPU, so the chat model stays loaded. Write text exactly as it should sound: "
               "numbers, dates, times, units and abbreviations in words, no markdown or emoji.")


def _speech_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    pipe = _pipe(ctx)
    models = pipe.installed("speech")
    if not models:
        return SPEECH_TEXT, _speech_params([], [])
    lines, languages = [], []
    for e in models:
        langs = list(e.get("languages") or [])
        languages += [x for x in langs if x not in languages]
        voices = ", ".join(f"{n} ({d})" if d else n for n, d in pipe.voices(e).items())
        speaks = "Danish (English only with a heavy Danish accent)" if langs == ["da"] else \
            f"{len(langs)} languages: {', '.join(langs)}"
        extra = " Expressiveness works." if e.get("expressive") else ""
        faster = " About three times faster, a little less natural: for long texts." if \
            e.get("family") == "chatterbox-turbo" else ""
        lines.append(f"- {e['id']}: {e['name']}, {speaks}. Voices: {voices}.{faster}{extra}")
    if "en" not in languages and any(e.get("family") == "chatterbox-mtl" for e in models):
        languages.append("en")                          # Røst reads English too, with its accent
    da = pipe.speech_model("da")[0]["name"]
    other = next((e["name"] for e in models if "en" in (e.get("languages") or [])), None)
    route = f"Without model, Danish goes to {da}" + (f" and other languages to {other}." if other else ".")
    expressive = ("expressiveness (where it works): 0.5 normal, 0.7-1.0 lively or dramatic (an announcer), 0.3 calm. "
                  if any(e.get("expressive") for e in models) else "")
    text = (SPEECH_TEXT + " Punctuation gives the pauses, a new line a longer one; up to 6,000 characters per call. "
            "A few seconds per sentence, more the first time a model loads.\n" + "\n".join(lines) +
            f"\n{route}\nvoice: one of the model's voices, or the path of a clip in the working folder (5-20 s of one "
            "person speaking clearly, any common format) to speak in that voice - only a voice the user has the "
            f"right to use. {expressive}To mix speech with music or add effects such as reverb, use ffmpeg in bash "
            "afterwards.")
    return text, _speech_params([e["id"] for e in models], languages)


# ---------------------------------------------------------------- music

async def music_generate(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipe(ctx)
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult("prompt is required: the style of the music", is_error=True)
    from ...audio.pipeline import AudioError
    lyrics = str(args.get("lyrics") or "")
    try:
        seconds = _number(args, "duration", 60, 10, 300)
        bpm = int(_number(args, "bpm", 0, 0, 300)) or None
        ts = int(_number(args, "time_signature", 4, 2, 6))
        rel, fmt = _target(ctx, args.get("save_as"), slug(prompt), COMFY_SAVE)
        _for_device(ctx, fmt, seconds)
        out = await pipe.music(prompt=prompt, lyrics=lyrics, seconds=seconds, bpm=bpm, key=args.get("key"),
                               time_signature=ts, language=str(args.get("language") or "en"), seed=args.get("seed"),
                               model=args.get("model") or None, fmt=fmt, ticket=ctx.ticket,
                               on_progress=await _progress(ctx))
    except (ValueError, AudioError) as exc:
        return ToolResult(f"Music generation failed: {exc}", is_error=True)
    _save(ctx, rel, out["data"], "made")
    words = "instrumental" if not lyrics.strip() or lyrics.strip().lower() in ("[instrumental]", "[inst]") else \
        f"lyrics in {out['language']}"
    return ToolResult(f"Saved {rel}: {out['seconds']:.0f} s, {out['model']['name']} - {out['bpm']} BPM, {out['key']}, "
                      f"{out['time_signature']}/4, {words}, seed {out['seed']} ({out['compute']:.0f} s on the card). "
                      f"{_reach(ctx, out['data'])}",
                      display={"audio": [{"path": rel, "seconds": out["seconds"], "model": out["model"]["name"]}]})


def _music_params(models: list[str]) -> dict:
    model: dict = {"type": "string", "enum": models} if models else {"type": "string"}
    return obj({"prompt": {"type": "string", "description": "The style: genre, mood, instruments, singer, production."},
                "lyrics": {"type": "string", "description": "Lyrics with [verse]/[chorus]/... tags; empty for an "
                                                            "instrumental."},
                "duration": {"type": "number", "description": "Seconds, 10-300 (default 60)."},
                "bpm": {"type": "integer"}, "key": {"type": "string", "description": "e.g. A minor, F# major"},
                "time_signature": {"type": "integer", "description": "2, 3, 4 or 6 (default 4)."},
                "language": {"type": "string", "description": "Language of the lyrics (ISO code, default en)."},
                "model": model, "seed": {"type": "integer"},
                "save_as": {"type": "string", "description": "Path in the working folder: .mp3 (default), .flac or "
                                                             ".ogg"}}, ["prompt"])


MUSIC_TEXT = ("Make music - a song with vocals from lyrics, or an instrumental - as an audio file in the working "
              "folder that the user can play under your answer. Runs on the graphics card.")


def _music_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    pipe = _pipe(ctx)
    models = pipe.installed("music")
    if not models:
        return MUSIC_TEXT, _music_params([])
    mine = pipe.chosen("music")["id"]
    lines = "\n".join(f"- {e['id']}: {e['name']}{' (used without model)' if e['id'] == mine else ''}" for e in models)
    text = (MUSIC_TEXT + " Roughly 1-3 minutes for a 2-minute song, plus a minute or two when the model has to "
            "load.\n- prompt: the style as a rich description or comma-separated tags - genre, era, mood, "
            "instruments, tempo feel, the singer (gender, timbre), production. E.g. \"upbeat Danish indie pop, warm "
            "female vocals, jangly guitars, handclaps, bright 2010s production\".\n- lyrics: one short line per sung "
            "phrase, section tags on their own lines: [verse], [chorus], [bridge], [intro], [outro], [instrumental]; "
            "in the language given as language. Empty for an instrumental.\n- duration: seconds, 10-300 - give the "
            "lyrics room: a verse and a chorus need 45-60 s, a whole song 2-3 minutes.\n- bpm, key (\"A minor\") and "
            "time_signature (3 for a waltz, else 4): choose what fits the style.\n- language of the lyrics: English "
            "is strongest; Danish and the other languages work but are less polished.\nModels:\n" + lines)
    return text, _music_params([e["id"] for e in models])


# ---------------------------------------------------------------- sound effects

async def sound_generate(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipe(ctx)
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult("prompt is required: a description of the sound", is_error=True)
    from ...audio.pipeline import AudioError
    try:
        seconds = _number(args, "duration", 5, 0.5, 380)
        bpm = int(_number(args, "bpm", 0, 0, 300)) or None
        rel, fmt = _target(ctx, args.get("save_as"), slug(prompt), COMFY_SAVE)
        _for_device(ctx, fmt, seconds)
        out = await pipe.sound(prompt=prompt, kind=str(args.get("kind") or "sfx").lower(), seconds=seconds, bpm=bpm,
                               seed=args.get("seed"), model=args.get("model") or None, fmt=fmt,
                               negative=str(args.get("negative") or ""), ticket=ctx.ticket,
                               on_progress=await _progress(ctx))
    except (ValueError, AudioError) as exc:
        return ToolResult(f"Sound generation failed: {exc}", is_error=True)
    _save(ctx, rel, out["data"], "made")
    return ToolResult(f"Saved {rel}: {out['seconds']:g} s, {out['model']['name']}, seed {out['seed']} "
                      f"({out['compute']:.0f} s on the card). {_reach(ctx, out['data'])} The model got: "
                      f"{out['prompt_used']}",
                      display={"audio": [{"path": rel, "seconds": out["seconds"], "model": out["model"]["name"]}]})


def _sound_params(models: list[str]) -> dict:
    model: dict = {"type": "string", "enum": models} if models else {"type": "string"}
    return obj({"prompt": {"type": "string", "description": "A precise English description of the sound."},
                "kind": {"type": "string", "enum": ["sfx", "ambience", "one-shot", "loop", "instrument", "music"]},
                "duration": {"type": "number", "description": "Seconds (default 5)."},
                "bpm": {"type": "integer", "description": "For loops and music."},
                "negative": {"type": "string", "description": "What to keep out, e.g. talking, music."},
                "model": model, "seed": {"type": "integer"},
                "save_as": {"type": "string", "description": "Path in the working folder: .mp3 (default), .flac or "
                                                             ".ogg"}}, ["prompt"])


SOUND_TEXT = ("Make sound effects, ambience, loops, one-shots, single-instrument parts or instrumental music (no "
              "speech or singing) as an audio file in the working folder that the user can play under your answer. "
              "Runs on the graphics card.")


def _sound_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    pipe = _pipe(ctx)
    models = pipe.installed("sound")
    if not models:
        return SOUND_TEXT, _sound_params([])
    text = (SOUND_TEXT[:-1] + " in seconds, plus loading the model.\nprompt: describe the sound precisely in English - "
            "the source and "
            "its material, the action and how it starts, lasts and fades, the space (dry room, hall, arena, outdoors; "
            "close or distant) and any processing; for music the genre, instruments, mood and BPM. E.g. \"Glass "
            "bottle shattering on concrete, sharp impact, scattered fragments\", \"Ice hockey arena horn at the end "
            "of a period, loud and slightly distorted, huge echoing hall\", \"Lo-fi hip-hop drum loop, dusty snare, "
            "swung hi-hats\".\nkind: sfx (default), ambience, one-shot, loop, instrument or music - it adds the tag "
            "the model was trained with.\nduration: seconds - 1-3 for impacts and one-shots, 3-6 for short actions, "
            "6-30 for ambience and loops, up to 380 for music.\nModels: " +
            ", ".join(f"{e['id']} ({e['name']})" for e in models))
    return text, _sound_params([e["id"] for e in models])


def _ready(ability: str):
    def check(ctx: ToolContext) -> bool:
        pipe = _pipe(ctx)
        return bool(pipe and pipe.available(ability))
    return check


register(Tool("speech_generate", SPEECH_TEXT, _speech_params([], []), speech_generate, available=_ready("speech"),
              in_subagent=False, group="audio", dynamic=_speech_dynamic))
register(Tool("music_generate", MUSIC_TEXT, _music_params([]), music_generate, available=_ready("music"),
              in_subagent=False, group="audio", dynamic=_music_dynamic))
register(Tool("sound_generate", SOUND_TEXT, _sound_params([]), sound_generate, available=_ready("sound"),
              in_subagent=False, group="audio", dynamic=_sound_dynamic))
