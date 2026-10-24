"""Sound: speech (the speech service, CPU); music, remakes, sound effects, stems and voice conversion (ComfyUI,
GPU). Each tool saves its audio files in the working folder, where the card under the answer plays them."""
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
CLIP_S = 30                           # a voice or style clip: the models hear at most this much of it
REMAKE_S = 300                        # a song to remake (ACE-Step, like music_generate)
LONG_S = 600                          # a song to take apart or to give another voice
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


def flac_seconds(data: bytes) -> float:
    """A FLAC file's length from its STREAMINFO block (0.0 when it is not one)."""
    if len(data) < 42 or data[:4] != b"fLaC" or data[4] & 0x7F != 0:
        return 0.0
    info = int.from_bytes(data[18:26], "big")              # 20 bits rate, 3 channels, 5 bits per sample, 36 samples
    rate, samples = info >> 44, info & ((1 << 36) - 1)
    return samples / rate if rate else 0.0


async def _sound_input(ctx: ToolContext, path: str, max_seconds: int, what: str) -> tuple[bytes, float, float]:
    """A sound file in the working folder for ComfyUI: (its first max_seconds as 44.1 kHz stereo FLAC, the seconds
    that holds, the whole file's seconds or 0 when unknown). ffmpeg converts it in the sandbox, confined in a
    person's chat - no media parser in the Hub or ComfyUI reads the user's file."""
    v = (path or "").strip()
    if not v:
        raise ValueError(f"{what} is required: the path of a sound file in the working folder")
    wd = ctx.workdir
    p = wd.resolve(v)
    if not p.is_file():
        raise FileNotFoundError(v)
    if p.stat().st_size > MAX_CLIP_BYTES:
        raise ValueError(f"{what} is over 200 MB")
    tmp = f".hub/sound-{uuid.uuid4().hex[:12]}.flac"
    wd.resolve(tmp).parent.mkdir(parents=True, exist_ok=True)
    src, dst = shlex.quote(f"{wd.sandbox_path}/{wd.rel(p)}"), shlex.quote(f"{wd.sandbox_path}/{tmp}")
    # local files only: a playlist inside a crafted file cannot make ffmpeg fetch anything
    cmd = (f"(ffprobe -v error -protocol_whitelist file -show_entries format=duration -of csv=p=0 {src} || true) && "
           f"ffmpeg -nostdin -hide_banner -loglevel error -protocol_whitelist file -y -i {src} -t {int(max_seconds)} "
           f"-vn -ac 2 -ar 44100 -sample_fmt s16 -c:a flac -f flac {dst}")
    try:
        res = await sandbox_exec(ctx, cmd, wd.sandbox_path, 180)
        out = wd.resolve(tmp)
        data = out.read_bytes() if out.is_file() else b""
        seconds = flac_seconds(data)
        if res.get("exit_code") != 0 or seconds <= 0:
            why = (res.get("stderr") or "").strip().splitlines()
            raise ValueError(f"{what} could not be read ({why[-1][:200] if why else 'not audio'})")
        try:
            total = float((res.get("stdout") or "").strip().splitlines()[0])
        except (ValueError, IndexError):
            total = 0.0
        return data, seconds, total
    except SandboxError as exc:
        raise ValueError(f"{what} could not be converted: {exc}")
    finally:
        try:
            wd.resolve(tmp).unlink()
        except (OSError, ValueError):
            pass


def _cut_note(seconds: float, total: float, what: str) -> str:
    return f" Only the first {_clock(seconds)} of {what} ({_clock(total)}) were used." if total > seconds + 1 else ""


def _clock(seconds: float) -> str:
    m, s_ = divmod(int(round(seconds)), 60)
    return f"{m}:{s_:02d}"


def _paths(ctx: ToolContext, save_as: str | None, stem: str, parts: tuple[str, ...]) -> tuple[dict[str, str], str]:
    """{part: path} for the files of one call, side by side as <base>-<part>.<format>. save_as: a name the parts
    go after (song.flac -> song-vocals.flac, song-accompaniment.flac; may overwrite) or a folder; else a free name
    from stem, in a project under audio/ - with one number for all the parts when a name is taken."""
    raw = (save_as or "").strip()
    if raw and not raw.endswith("/") and not ctx.workdir.resolve(raw).is_dir():
        rel, fmt = _target(ctx, raw, stem, COMFY_SAVE)
        base = rel[: -len(Path(rel).suffix)]
        return {p: f"{base}-{p}.{fmt}" for p in parts}, fmt
    if raw:
        folder = ctx.workdir.rel(ctx.workdir.resolve(raw))
        base = stem if folder == "." else f"{folder}/{stem}"
    else:
        base = f"audio/{stem}" if ctx.project else stem
    n = 1
    while True:
        out = {p: f"{base}-{p}{'' if n == 1 else f'-{n}'}.mp3" for p in parts}
        if not any(ctx.workdir.resolve(x).exists() for x in out.values()):
            return out, "mp3"
        n += 1


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
            f"{len(langs)} languages: {', '.join(langs)}" + (" (its Danish has an English accent)" if "da" in langs
                                                             else "")
        extra = " Expressiveness works." if e.get("expressive") else ""
        faster = " About 1.5 times faster than Røst v3." if e.get("family") == "chatterbox-turbo" else ""
        lines.append(f"- {e['id']}: {e['name']}, {speaks}. Voices: {voices}.{faster}{extra}")
    if "en" not in languages and any(e.get("family") == "chatterbox-mtl" for e in models):
        languages.append("en")                          # Røst reads English too, with its accent
    da = pipe.speech_model("da")[0]["name"]
    other = next((e["name"] for e in models if "en" in (e.get("languages") or [])), None)
    route = f"Without model, Danish goes to {da}" + (f" and other languages to {other}." if other else ".")
    expressive = ("expressiveness (where it works): 0.5 normal, 0.7-1.0 lively or dramatic (an announcer), 0.3 calm. "
                  if any(e.get("expressive") for e in models) else "")
    text = (SPEECH_TEXT + " Punctuation gives the pauses, a new line a longer one; up to 6,000 characters per call. "
            "Slower than real time: several seconds of work per second of speech, more the first time a model "
            "loads.\n" + "\n".join(lines) +
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
    note = ""
    try:
        seconds = _number(args, "duration", 60, 10, 300)
        bpm = int(_number(args, "bpm", 0, 0, 300)) or None
        ts = int(_number(args, "time_signature", 4, 2, 6))
        rel, fmt = _target(ctx, args.get("save_as"), slug(prompt), COMFY_SAVE)
        _for_device(ctx, fmt, seconds)
        reference = None
        if str(args.get("reference") or "").strip():
            reference, used, total = await _sound_input(ctx, str(args["reference"]), CLIP_S, "the reference clip")
            note = _cut_note(used, total, "the reference clip")
        out = await pipe.music(prompt=prompt, lyrics=lyrics, seconds=seconds, bpm=bpm, key=args.get("key"),
                               time_signature=ts, language=str(args.get("language") or "en"), seed=args.get("seed"),
                               model=args.get("model") or None, fmt=fmt, reference=reference, ticket=ctx.ticket,
                               on_progress=await _progress(ctx))
    except FileNotFoundError as exc:
        return ToolResult(f"No such file in the working folder: {exc}", is_error=True)
    except (ValueError, AudioError) as exc:
        return ToolResult(f"Music generation failed: {exc}", is_error=True)
    _save(ctx, rel, out["data"], "made")
    words = "instrumental" if not lyrics.strip() or lyrics.strip().lower() in ("[instrumental]", "[inst]") else \
        f"lyrics in {out['language']}"
    sound = f", with the sound of {str(args['reference']).strip()}" if reference else ""
    return ToolResult(f"Saved {rel}: {out['seconds']:.0f} s, {out['model']['name']} - {out['bpm']} BPM, {out['key']}, "
                      f"{out['time_signature']}/4, {words}{sound}, seed {out['seed']} ({out['compute']:.0f} s on the "
                      f"card). {_reach(ctx, out['data'])}{note}",
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
                "reference": {"type": "string", "description": "Optional: a clip in the working folder whose "
                                                               "sound the song takes on (5-30 s)."},
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
            "female vocals, jangly guitars, handclaps, bright 2010s production\". A solo or lead instrument goes in "
            "the same prompt (\"..., wild fast electric guitar solo\"): one call makes the whole arrangement, and "
            "music made separately never fits it.\n- lyrics: one short line per sung "
            "phrase, section tags on their own lines: [verse], [chorus], [bridge], [intro], [outro], [instrumental]; "
            "in the language given as language. Empty for an instrumental.\n- duration: seconds, 10-300 - give the "
            "lyrics room: a verse and a chorus need 45-60 s, a whole song 2-3 minutes.\n- bpm, key (\"A minor\") and "
            "time_signature (3 for a waltz, else 4): choose what fits the style.\n- language of the lyrics: English "
            "is strongest; Danish and the other languages work but are less polished.\n- reference (optional): a "
            "clip in the working folder (5-30 s, any common format) whose sound the song takes on - a singer's "
            "timbre, another song's instruments, production and room. It steers the sound, it does not copy a "
            "particular person's voice; for a song sung in someone's voice, make the song, then voice_convert it "
            "(when that tool is offered). Only a voice or recording the user has the right to use.\nModels:\n" +
            lines)
    return text, _music_params([e["id"] for e in models])


# ---------------------------------------------------------------- remake

async def music_remake(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipe(ctx)
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult("prompt is required: the sound of the new version", is_error=True)
    from ...audio.pipeline import AudioError
    lyrics = str(args.get("lyrics") or "")
    try:
        song, seconds, total = await _sound_input(ctx, str(args.get("audio") or ""), REMAKE_S, "audio")
        note = _cut_note(seconds, total, "the song")
        reference = None
        if str(args.get("reference") or "").strip():
            reference, used, rtotal = await _sound_input(ctx, str(args["reference"]), CLIP_S, "the reference clip")
            note += _cut_note(used, rtotal, "the reference clip")
        stem = slug(Path(str(args.get("audio"))).stem + "-remake")
        rel, fmt = _target(ctx, args.get("save_as"), stem, COMFY_SAVE)
        _for_device(ctx, fmt, seconds)
        bpm = int(_number(args, "bpm", 0, 0, 300)) or None
        ts = int(_number(args, "time_signature", 0, 0, 6)) or None
        out = await pipe.remake(song=song, seconds=seconds, prompt=prompt, lyrics=lyrics, reference=reference,
                                keep=_number(args, "keep", 0.0, 0.0, 0.9), bpm=bpm, key=args.get("key") or None,
                                time_signature=ts, language=str(args.get("language") or "en"), seed=args.get("seed"),
                                model=args.get("model") or None, fmt=fmt, ticket=ctx.ticket,
                                on_progress=await _progress(ctx))
    except FileNotFoundError as exc:
        return ToolResult(f"No such file in the working folder: {exc}", is_error=True)
    except (ValueError, AudioError) as exc:
        return ToolResult(f"The remake failed: {exc}", is_error=True)
    _save(ctx, rel, out["data"], "made")
    sound = f" with the sound of {str(args['reference']).strip()}" if reference else ""
    words = "no lyrics given, so the words are not kept" if not lyrics.strip() else f"lyrics in {out['language']}"
    meta = ", ".join(x for x in (f"{out['bpm']} BPM" if out["bpm"] else "", out["key"] or "",
                                  f"{out['time_signature']}/4" if out["time_signature"] else "") if x)
    return ToolResult(f"Saved {rel}: a new version of {str(args.get('audio')).strip()}{sound}, {out['seconds']:.0f} s, "
                      f"{out['model']['name']}, keep {out['keep']:.1f}, {words}"
                      f"{', ' + meta if meta else ''}, seed {out['seed']} ({out['compute']:.0f} s on the card). "
                      f"{_reach(ctx, out['data'])}{note}",
                      display={"audio": [{"path": rel, "seconds": out["seconds"], "model": out["model"]["name"]}]})


def _remake_params(models: list[str]) -> dict:
    model: dict = {"type": "string", "enum": models} if models else {"type": "string"}
    return obj({"audio": {"type": "string", "description": "The song: a sound file in the working folder."},
                "prompt": {"type": "string", "description": "The sound of the new version: genre, instruments, "
                                                            "singer, production, room, live or studio."},
                "lyrics": {"type": "string", "description": "The song's words with [verse]/[chorus] tags, as sung; "
                                                            "empty: the words are not kept."},
                "reference": {"type": "string", "description": "Optional: a clip in the working folder whose "
                                                               "sound to take (5-30 s)."},
                "keep": {"type": "number", "description": "0-0.9, default 0: how much of the recording itself stays."},
                "bpm": {"type": "integer"}, "key": {"type": "string"},
                "time_signature": {"type": "integer", "description": "2, 3, 4 or 6."},
                "language": {"type": "string", "description": "Language of the lyrics (ISO code, default en)."},
                "model": model, "seed": {"type": "integer"},
                "save_as": {"type": "string", "description": "Path in the working folder: .mp3 (default), .flac or "
                                                             ".ogg"}}, ["audio", "prompt"])


REMAKE_TEXT = ("Make a new version of a song in the working folder: its melody, rhythm and structure re-performed "
               "with another sound - a style described in the prompt and/or the sound of a reference clip (another "
               "song, a live recording with its audience, room and reverb). Runs on the graphics card.")


def _remake_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    pipe = _pipe(ctx)
    models = pipe.installed("music")
    if not models:
        return REMAKE_TEXT, _remake_params([])
    text = (REMAKE_TEXT + f" Songs up to {REMAKE_S // 60} minutes (longer ones are cut).\n- prompt: the new sound "
            "as rich tags - genre, instruments, singer, "
            "production, \"live concert recording, cheering audience, big arena reverb\". The model hears the "
            "reference, you do not: describe what the user says it sounds like.\n- reference: a clip of the sound to "
            "take (5-30 s; cut the part you want with ffmpeg first - the first 30 s are used).\n- lyrics: the song's "
            "words as they are sung, with section tags, in language. Ask the user when you do not know them; "
            "without them the singing loses its words.\n- keep: 0 (default) a new performance that follows the "
            "song; 0.3-0.6 stays closer to the recording itself (more of its voice and sound survive, less of the new "
            "style).\n- bpm, key and time_signature: give them when you know them, else leave them out.\n"
            "Only recordings the user has the right to use. Models: " +
            ", ".join(f"{e['id']} ({e['name']})" for e in models))
    return text, _remake_params([e["id"] for e in models])


# ---------------------------------------------------------------- stems

STEMS = ("vocals", "accompaniment")


async def audio_separate(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipe(ctx)
    from ...audio.pipeline import AudioError
    keep = str(args.get("keep") or "both").strip().lower()
    stems = STEMS if keep == "both" else (keep,)
    if any(x not in STEMS for x in stems):
        return ToolResult("keep must be both, vocals or accompaniment", is_error=True)
    try:
        audio, seconds, total = await _sound_input(ctx, str(args.get("audio") or ""), LONG_S, "audio")
        paths, fmt = _paths(ctx, args.get("save_as"), slug(Path(str(args.get("audio"))).stem), stems)
        _for_device(ctx, fmt, seconds)
        out = await pipe.separate(audio=audio, stems=stems, model=args.get("model") or None, fmt=fmt,
                                  ticket=ctx.ticket, on_progress=await _progress(ctx))
    except FileNotFoundError as exc:
        return ToolResult(f"No such file in the working folder: {exc}", is_error=True)
    except (ValueError, AudioError) as exc:
        return ToolResult(f"Separating failed: {exc}", is_error=True)
    for stem in stems:
        _save(ctx, paths[stem], out[stem], "separated")
    listed = " and ".join(f"{paths[x]} ({x})" for x in stems)
    reach = [_reach(ctx, out[x]) for x in stems]
    return ToolResult(f"Saved {listed}: {_clock(seconds)}, {out['model']['name']} ({out['compute']:.0f} s on the card). "
                      f"{next((r for r in reach if 'will not get there' in r), reach[0])}"
                      f"{_cut_note(seconds, total, 'the song')}",
                      display={"audio": [{"path": paths[x], "seconds": round(seconds, 1),
                                          "model": out["model"]["name"]} for x in stems]})


def _separate_params() -> dict:
    return obj({"audio": {"type": "string", "description": "The song: a sound file in the working folder."},
                "keep": {"type": "string", "enum": ["both", "vocals", "accompaniment"]},
                "save_as": {"type": "string", "description": "A folder, or a name the stems go after (song.flac -> "
                                                             "song-vocals.flac ...): .mp3 (default), .flac or .ogg"}},
               ["audio"])


SEPARATE_TEXT = ("Take a song in the working folder apart into its vocals and the accompaniment (everything else) "
                 "as audio files: karaoke, remixes, an a cappella, or one part to listen to alone. Runs on the "
                 f"graphics card; songs up to {LONG_S // 60} minutes (longer ones are cut).")


# ---------------------------------------------------------------- voice conversion

async def voice_convert(ctx: ToolContext, args: dict) -> ToolResult:
    pipe = _pipe(ctx)
    from ...audio.pipeline import AudioError
    singing = args.get("singing") is not False
    music = bool(args["music"]) if args.get("music") is not None else singing
    vocals_only = bool(args.get("vocals_only")) and music
    try:
        audio, seconds, total = await _sound_input(ctx, str(args.get("audio") or ""), LONG_S, "audio")
        voice, used, vtotal = await _sound_input(ctx, str(args.get("voice") or ""), CLIP_S, "voice")
        stem = slug(f"{Path(str(args.get('audio'))).stem}-as-{Path(str(args.get('voice'))).stem}")
        if vocals_only:
            paths, fmt = _paths(ctx, args.get("save_as"), stem, ("mix", "vocals"))
        else:
            rel, fmt = _target(ctx, args.get("save_as"), stem, COMFY_SAVE)
            paths = {"mix": rel}
        _for_device(ctx, fmt, seconds)
        steps = int(_number(args, "steps", 0, 0, 100)) or None
        out = await pipe.convert_voice(audio=audio, voice=voice, singing=singing, music=music,
                                       pitch=int(_number(args, "pitch", 0, -24, 24)),
                                       auto_octave=args.get("auto_octave") is not False, steps=steps,
                                       vocals_only=vocals_only, model=args.get("model") or None, fmt=fmt,
                                       ticket=ctx.ticket, on_progress=await _progress(ctx))
    except FileNotFoundError as exc:
        return ToolResult(f"No such file in the working folder: {exc}", is_error=True)
    except (ValueError, AudioError) as exc:
        return ToolResult(f"Voice conversion failed: {exc}", is_error=True)
    _save(ctx, paths["mix"], out["data"], "made")
    shown = [{"path": paths["mix"], "seconds": round(seconds, 1), "model": out["model"]["name"]}]
    extra = ""
    if vocals_only and out.get("vocals"):
        _save(ctx, paths["vocals"], out["vocals"], "made")
        shown.append({"path": paths["vocals"], "seconds": round(seconds, 1), "model": out["model"]["name"]})
        extra = f" The converted vocals alone: {paths['vocals']}."
    how = ("singing, the vocals separated from the music, converted and mixed back" if music and singing else
           "singing" if singing else "speech, separated from the music and mixed back" if music else "speech")
    shift = f", {_shift_text(out['semitones'])}" if singing else ""
    ref = out.get("reference_seconds")
    heard = f"; {ref:g} s of the clip had voice in it" if isinstance(ref, (int, float)) else ""
    return ToolResult(f"Saved {paths['mix']}: {str(args.get('audio')).strip()} in the voice of "
                      f"{str(args.get('voice')).strip()} - {how}{shift}{heard}. {out['model']['name']}, "
                      f"{out['steps']} steps ({out['compute']:.0f} s on the card). {_reach(ctx, out['data'])}{extra}"
                      f"{_cut_note(seconds, total, 'the recording')}{_cut_note(used, vtotal, 'the voice clip')}",
                      display={"audio": shown})


def _shift_text(n: int) -> str:
    if not n:
        return "the melody at its own pitch"
    octaves = f" ({abs(n) // 12} octave{'s' if abs(n) >= 24 else ''})" if n % 12 == 0 else ""
    return f"the melody moved {'up' if n > 0 else 'down'} {abs(n)} semitones{octaves}"


def _voice_params() -> dict:
    return obj({"audio": {"type": "string", "description": "The recording whose voice to change: a sound file in "
                                                          "the working folder."},
                "voice": {"type": "string", "description": "A clip of the voice to take on (5-30 s), in the working "
                                                          "folder."},
                "singing": {"type": "boolean", "description": "true (default): singing - the melody is kept; false: "
                                                             "speech."},
                "music": {"type": "boolean", "description": "The recording has music under the voice (default: the "
                                                           "same as singing)."},
                "pitch": {"type": "integer", "description": "Singing: semitones to move the melody, -24..24 "
                                                           "(default 0)."},
                "auto_octave": {"type": "boolean", "description": "Singing: move it by whole octaves towards the "
                                                                 "voice's range (default true)."},
                "steps": {"type": "integer", "description": "Diffusion steps, 10-100 (default 40 singing, 25 "
                                                           "speech)."},
                "vocals_only": {"type": "boolean", "description": "Also save the converted vocals alone."},
                "save_as": {"type": "string", "description": "Path in the working folder: .mp3 (default), .flac or "
                                                             ".ogg"}}, ["audio", "voice"])


VOICE_TEXT = ("Make a recording sound as if another person sang or spoke it: the words, timing and melody stay, the "
              "voice becomes the one in a clip. Runs on the graphics card.")


def _voice_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    pipe = _pipe(ctx)
    if not pipe.available("voice"):
        return VOICE_TEXT, _voice_params()
    separates = pipe.available("separate")
    music = ("Songs with music are fine: the vocals are separated, converted and mixed back (music: true)."
             if separates else "Songs with music need Mel-Band RoFormer vocals, which is not downloaded: only clean "
             "vocals or speech (music: false).")
    text = (VOICE_TEXT + f" Recordings up to {LONG_S // 60} minutes (longer ones are cut).\n- voice: a "
            f"clip of the voice to take on, 5-30 s of one person (any common format; the first {CLIP_S} s are used"
            f"{', and a clip from a song works: its vocals are separated first' if separates else ''}; pauses are "
            "skipped). Cut a longer recording with ffmpeg first. Only a voice the user has the right to use.\n- "
            "singing: true keeps the melody exactly (the 44.1 kHz singing model); false for speech.\n- " + music +
            "\n- pitch: semitones for the melody; with auto_octave (default) it also moves by whole octaves towards "
            "the voice's range - a man's song in a woman's voice goes up an octave and stays in key.\n- For a new "
            "song in someone's voice: make it with music_generate, then convert its singing here.")
    return text, _voice_params()


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
    # its music and instrument parts never line up with other music: no shared key, tempo or beat (chat 15)
    alone = (" Its music and instrument parts stand alone: they do not follow a song from music_generate (no "
             "shared key, tempo or beat), so a solo for a song belongs in music_generate's prompt."
             if pipe.available("music") else " Pieces of music made separately do not fit together (no shared key, "
             "tempo or beat), so do not layer two of them.")
    text = (SOUND_TEXT[:-1] + " in seconds, plus loading the model.\nprompt: describe the sound precisely in English - "
            "the source and "
            "its material, the action and how it starts, lasts and fades, the space (dry room, hall, arena, outdoors; "
            "close or distant) and any processing; for music the genre, instruments, mood and BPM. E.g. \"Glass "
            "bottle shattering on concrete, sharp impact, scattered fragments\", \"Ice hockey arena horn at the end "
            "of a period, loud and slightly distorted, huge echoing hall\", \"Lo-fi hip-hop drum loop, dusty snare, "
            "swung hi-hats\".\nkind: sfx (default), ambience, one-shot, loop, instrument or music - it adds the tag "
            "the model was trained with." + alone + "\nduration: seconds - 1-3 for impacts and one-shots, 3-6 for "
            "short actions, 6-30 for ambience and loops, up to 380 for music.\nModels: " +
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
register(Tool("music_remake", REMAKE_TEXT, _remake_params([]), music_remake, available=_ready("music"),
              in_subagent=False, group="audio", dynamic=_remake_dynamic))
register(Tool("audio_separate", SEPARATE_TEXT, _separate_params(), audio_separate, available=_ready("separate"),
              in_subagent=False, group="audio"))
register(Tool("voice_convert", VOICE_TEXT, _voice_params(), voice_convert, available=_ready("voice"),
              in_subagent=False, group="audio", dynamic=_voice_dynamic))
