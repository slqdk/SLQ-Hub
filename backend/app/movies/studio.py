"""Movies (0.72.0, Søren: "Does it make sense to make a Movie workflow section that can do this? With checkpoints?"
- after asking how to make short family cartoon reels, at most 30 s each, with four characters from his LoRAs).

A series holds what every episode shares: its look (the picture model, a style LoRA and words, the format), its cast
(each character's LoRA, the words for their look, a character sheet) and its sets (a picture of each place). An
episode goes from a premise to a storyboard (the chat model writes the shots), to keyframes (pictures to choose
from for each shot), to clips (each chosen keyframe animated by the video model) and a silent preview of the clips
joined. Each stage is a checkpoint: the Hub stops for the owner - approve the storyboard, choose a keyframe, keep a
clip - and goes on from there whenever he comes back, because every result is stored with the series.

The Hub sets each shot's LoRAs itself - in a chat the picture menu is the owner's (0.59.0), so the assistant could not:
a shot with one character gets that character's LoRA and the style LoRA; a shot with two or more is combined from the
set's picture and the characters' sheets (FLUX.2 klein reads up to six pictures in a change) with the style LoRA only,
because LoRAs of several people in one picture blend their faces.

What a result was made from is stamped on it (a hash of the shot's words, the characters' and the set's, the look):
a change makes only what depends on it "made before a change" - a new keyframe for shot 3 makes shot 3's clip and the
preview out of date, nothing else. "Keep as it is" accepts a result for the change.

The work runs in the background through the queue (owner "movies", priority normal: chats go first), a job per
cast member, set, storyboard, the keyframes and clips of an episode (a queue of its shots) and its preview. Nothing
is half-stored: a job that stops - Stop, an error, a restart - leaves what it had finished. The files live in
data/movies/<series>, out of the sandbox's reach; the preview is joined by ffmpeg in the sandbox in a folder of its
own under the workspace, removed afterwards. The owner's alone, like LoRAs and training.

0.73.0 adds the sound and the cut (sound.py makes them, cut.py says what goes where): each character's voice - one of
the speech model's or a clip of a real one - the lines spoken, an effect for each shot, the series' theme, and the
episode cut into a film with the lines at their times, the theme under them, speech bubbles and an end card."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import secrets
import shlex
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from . import cut as C
from .common import (NARRATOR, PRIORITY, MovieError, _by_id, _given, _id, _now, _number, _para, _stamp, _text,
                     _whole)
from .sound import SoundJobs

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^[a-z0-9]{2,16}$")
# cast/ and sets/ pictures; voices/ clips (0.73.0); music/ themes; an episode's pictures, clips, lines and cuts
FILE_RE = re.compile(r"^(?:cast|sets|[a-z0-9]{2,16})/[a-z0-9][a-z0-9-]{0,80}\.(?:png|mp4|mp3|wav)$")
FORMATS = {"portrait": (720, 1280), "landscape": (1280, 720), "square": (1024, 1024)}
QUALITIES = ("fast", "full")
METHODS = ("auto", "new", "combine")      # combine: from the set's picture and the characters' sheets
NAME_MAX, WORDS_MAX, TEXT_MAX, LINE_MAX = 60, 600, 1500, 300
MAX_CAST, MAX_SETS, MAX_EPISODES, MAX_SHOTS, MAX_LINES = 8, 12, 300, 12, 12
MAX_VARIANTS = 12                          # pictures kept per shot, sheet or set (the chosen one always stays)
MAX_TAKES = 4                              # clips kept per shot: the current one and three before it
MAX_REFS = 6                               # FLUX.2 klein reads six pictures in a change: the set and five characters
SHOT_SECONDS = (1.0, 10.0)                 # what the video models make (5 s is what they learnt)
# an episode's jobs; those that work through a queue of its shots (or of its lines: "<shot>:<line>")
EPISODE_JOBS = ("storyboard", "keyframes", "clips", "preview", "voices", "sounds", "cut")
QUEUED_JOBS = ("keyframes", "clips", "voices", "sounds")

DEFAULT_RULES = (
    "One joke per episode: a setup in the first seconds, an escalation, a punchline near the end. Family-friendly. "
    "Mostly one or two characters in a shot, the whole family at most once. A character who speaks is off-screen or "
    "seen from behind, or only mumbles with a closed mouth - the video has no lip sync. Simple, small movements.")
DEFAULT_STYLE = {
    "model": "",                           # the picture model ("": Settings -> Images)
    "lora": "", "strength": 0.8,           # the style LoRA, on every picture
    "words": "2D cartoon, bold clean outlines, flat bright colours, simple shapes, expressive faces",
    "format": "portrait",
    "variants": 4,                         # pictures to choose from per shot, sheet and set
    "video_model": "",                     # "": Settings -> Assistant -> Video's model for a video from a picture
    "quality": "fast",
    "max_seconds": 30,
    "language": "Danish",                  # the spoken lines' language
    "rules": DEFAULT_RULES,
}

SHEET = ("Character design sheet of {name}: {look}. One figure, the whole body from head to feet, standing straight "
         "and facing the viewer, a friendly neutral expression, arms relaxed at the sides, on a plain light grey "
         "background with nothing else in the picture. Style: {words}.")
SET = ("{name}: {look}. The empty place, with no people and no animals, seen at eye level, with open floor in front "
       "where characters can stand. Style: {words}.")

STORYBOARD = """You write the storyboard of one episode of a short cartoon series, as JSON.

The series: {series}. Its look: {words}.
Rules for every episode:
{rules}

The cast - use these names exactly:
{cast}

The sets - use these names exactly, or "" for none:
{sets}

This episode: "{title}"
{premise}

Write the shots, together at most {max_seconds} seconds; each shot 2-10 seconds (5 is what the video model does best).
For each shot:
- seconds: its length.
- set: one of the sets' names, or "".
- characters: the cast names in the picture (most shots one or two).
- picture: the shot's first frame, for the picture model, in English: who is where, their pose and expression, what
  they hold, the framing (close-up, medium or wide shot) and the light. Concrete; no camera movement here.
- motion: what moves in the shot and how the camera moves, in English, simple and small - the video model starts
  from the picture.
- sound: the sound effects heard in the shot, in English, short and concrete (a fridge door opens, a cat meows) - no
  music: the series' theme plays under every episode. "" when nothing is heard but the lines.
- lines: what is said, in {language}: [{{"who": a cast name or "{narrator}", "text": ...}}] - empty when nobody speaks.
  Short lines: each is shown in a speech bubble while it is heard, and a 5-second shot holds about ten words.

Answer with JSON only: {{"shots": [{{"seconds": 5, "set": "...", "characters": ["..."], "picture": "...",
"motion": "...", "sound": "...", "lines": [{{"who": "...", "text": "..."}}]}}]}}"""

STORYBOARD_SCHEMA = {"type": "object", "properties": {"shots": {
    "type": "array", "minItems": 1, "maxItems": MAX_SHOTS, "items": {"type": "object", "properties": {
        "seconds": {"type": "number"}, "set": {"type": "string"},
        "characters": {"type": "array", "items": {"type": "string"}},
        "picture": {"type": "string"}, "motion": {"type": "string"}, "sound": {"type": "string"},
        "lines": {"type": "array", "items": {"type": "object", "properties": {
            "who": {"type": "string"}, "text": {"type": "string"}}, "required": ["who", "text"]}}},
        "required": ["seconds", "set", "characters", "picture", "motion", "sound", "lines"]}}},
    "required": ["shots"]}


def _seconds(value) -> float:
    return round(max(SHOT_SECONDS[0], min(SHOT_SECONDS[1], _number(value, 5.0))), 1)


def _strength(value, default: float = 1.0) -> float:
    return round(max(-10.0, min(10.0, _number(value, default))), 2)


# ---------------------------------------------------------------- what a result is made from

def env_of(series: dict, env: dict | None = None) -> dict:
    """What the look's defaults mean now - the picture model's id and whether it can combine pictures, the video
    model's id, the sound models' (Studio.env, from Settings and what is downloaded); without it, as the series
    says."""
    st = series["style"]
    return env or {"model": st.get("model") or "", "combine": True, "video": st.get("video_model") or "",
                   "speech": "", "sound": "", "music": ""}


def method_of(series: dict, shot: dict) -> str:
    """auto: combined from the pictures when two or more characters are in the shot, else a new picture."""
    m = shot.get("method") or "auto"
    if m == "auto":
        return "combine" if len(shot.get("characters") or []) >= 2 else "new"
    return m


def plan(series: dict, shot: dict, env: dict | None = None) -> dict:
    """How a shot's keyframes are made: the method, the prompt, the pictures it starts from (combine), the LoRAs and
    the stamp of what it is made from. A picture model that cannot change pictures cannot combine them: a new
    picture then (its edits would go to another model, without the series' LoRAs)."""
    st = series["style"]
    env = env_of(series, env)
    cast = [c for c in (_by_id(series["cast"], cid) for cid in shot.get("characters") or []) if c]
    place = _by_id(series["sets"], shot.get("set") or "")
    method = method_of(series, shot)
    if method == "combine" and not env["combine"]:
        method = "new"
    refs: list[tuple[str, str]] = []                      # (who, file)
    if method == "combine":
        if place and place.get("picture"):
            refs.append((place["name"], place["picture"]))
        refs += [(c["name"], c["sheet"]) for c in cast if c.get("sheet")]
        refs = refs[:MAX_REFS]
        if not refs:
            method = "new"                                # nothing to combine: a new picture from the words
    loras = []
    if len(cast) == 1 and cast[0].get("lora"):          # one character: their LoRA (never several: faces blend)
        loras.append({"id": cast[0]["lora"], "strength": cast[0].get("strength", 1.0)})
    if st.get("lora"):
        loras.append({"id": st["lora"], "strength": st.get("strength", 0.8)})
    picture = (shot.get("picture") or "").strip().rstrip(".")
    words = st.get("words") or ""
    if method == "combine":
        shown = {who for who, _ in refs}
        intro = " ".join(f"Picture {i + 1} {'shows the place, ' + who if place and i == 0 and who == place['name'] else 'is ' + who}."
                         for i, (who, _) in enumerate(refs))
        others = " ".join(f"{c['name']}: {c.get('look') or ''}." for c in cast if c["name"] not in shown)
        where = "" if (place and place["name"] in shown) or not place else f" The place: {place.get('look') or place['name']}."
        prompt = (f"{intro} Make one new picture{' of that place' if place and place['name'] in shown else ''} with "
                  f"them in it: {picture}.{where} {others} Keep each character exactly as in their picture - face, "
                  f"hair, clothes and colours. Style: {words}.")
    else:
        looks = " ".join(f"{c['name']}: {c.get('look') or ''}." for c in cast)
        where = f" The place: {place.get('look') or place['name']}." if place else ""
        prompt = f"{picture}. {looks}{where} Style: {words}."
    prompt = " ".join(prompt.split())
    stamp = _stamp("keyframe", env["model"], st.get("lora"), st.get("strength"), words, st.get("format"),
                   picture, method, [(c["name"], c.get("look"), c.get("lora"), c.get("strength"),
                                      c.get("sheet") if method == "combine" else None) for c in cast],
                   (place["name"], place.get("look"), place.get("picture") if method == "combine" else None)
                   if place else None)
    return {"method": method, "prompt": prompt, "refs": [f for _, f in refs], "loras": loras, "stamp": stamp}


def sheet_plan(series: dict, c: dict, env: dict | None = None) -> dict:
    st = series["style"]
    env = env_of(series, env)
    loras = ([{"id": c["lora"], "strength": c.get("strength", 1.0)}] if c.get("lora") else []) + \
        ([{"id": st["lora"], "strength": st.get("strength", 0.8)}] if st.get("lora") else [])
    prompt = SHEET.format(name=c["name"], look=(c.get("look") or "").rstrip("."), words=st.get("words") or "")
    return {"prompt": " ".join(prompt.split()), "loras": loras,
            "stamp": _stamp("sheet", env["model"], st.get("lora"), st.get("strength"), st.get("words"),
                            st.get("format"), c["name"], c.get("look"), c.get("lora"), c.get("strength"))}


def set_plan(series: dict, k: dict, env: dict | None = None) -> dict:
    st = series["style"]
    env = env_of(series, env)
    loras = [{"id": st["lora"], "strength": st.get("strength", 0.8)}] if st.get("lora") else []
    prompt = SET.format(name=k["name"], look=(k.get("look") or "").rstrip("."), words=st.get("words") or "")
    return {"prompt": " ".join(prompt.split()), "loras": loras,
            "stamp": _stamp("set", env["model"], st.get("lora"), st.get("strength"), st.get("words"),
                            st.get("format"), k["name"], k.get("look"))}


def clip_stamp(series: dict, shot: dict, env: dict | None = None) -> str:
    st = series["style"]
    return _stamp("clip", shot.get("keyframe"), shot.get("motion"), shot.get("seconds"), st.get("words"),
                  env_of(series, env)["video"], st.get("quality"))


def motion_prompt(series: dict, shot: dict) -> str:
    return " ".join(f"{(shot.get('motion') or '').strip().rstrip('.')}. {series['style'].get('words') or ''}".split())


def keyframe_state(series: dict, shot: dict, env: dict | None = None) -> str:
    if not shot.get("keyframe"):
        return "none"
    return "fresh" if shot.get("keyframe_stamp") == plan(series, shot, env)["stamp"] else "stale"


def clip_state(series: dict, shot: dict, env: dict | None = None) -> str:
    clip = shot.get("clip")
    if not clip:
        return "none"
    if keyframe_state(series, shot, env) != "fresh" or clip.get("stamp") != clip_stamp(series, shot, env):
        return "stale"
    return "fresh"


def preview_state(series: dict, episode: dict, env: dict | None = None) -> str:
    """Up to date while it joins the clips the shots have now, each of them up to date."""
    p = episode.get("preview")
    if not p:
        return "none"
    if p.get("stamp") != preview_stamp(episode) or any(clip_state(series, sh, env) != "fresh"
                                                        for sh in episode["shots"] if sh.get("clip")):
        return "stale"
    return "fresh"


def preview_stamp(episode: dict) -> str:
    return _stamp("preview", [(s["id"], (s.get("clip") or {}).get("file")) for s in episode["shots"]])


def missing(series: dict, shot: dict) -> list[str]:
    """What a combined shot has no picture of yet: its set, its characters."""
    place = _by_id(series["sets"], shot.get("set") or "")
    out = [place["name"]] if place and not place.get("picture") else []
    return out + [c["name"] for c in (_by_id(series["cast"], cid) for cid in shot.get("characters") or [])
                  if c and not c.get("sheet")]


def final_state(series: dict, episode: dict, env: dict | None = None) -> tuple[str, str]:
    """The cut is up to date while it is made of what the episode has now - its clips, lines, effects, theme and
    settings - each of them up to date. -> (state, why it is not)."""
    f = episode.get("final")
    if not f:
        return "none", ""
    env = env_of(series, env)
    for n, sh in enumerate(episode["shots"]):
        if sh.get("clip") and clip_state(series, sh, env) != "fresh":
            return "stale", f"shot {n + 1}'s clip is from before a change"
        if C.sfx_state(sh, env) == "stale":
            return "stale", f"shot {n + 1}'s effect is from before a change"
        if any(C.line_state(series, ln, env) == "stale" for ln in sh.get("lines") or []):
            return "stale", f"a line in shot {n + 1} was spoken before a change"
    if C.cut_settings(episode)["music"] and C.theme_state(series, env) == "stale":
        return "stale", "the theme is from before a change"
    if f.get("stamp") != C.cut_stamp(series, episode, env):
        return "stale", "the episode has changed since"
    return "fresh", ""


def lines_view(series: dict, shot: dict, placed: dict, env: dict | None = None) -> list[dict]:
    """A shot's lines as the page shows them: the speaker's name (a cast member's id is kept in the series), where
    the line starts in the shot and how long it is, whether it runs past the shot's end, its take and its state."""
    env = env_of(series, env)
    out = []
    for i, ln in enumerate(shot.get("lines") or []):
        c = _by_id(series["cast"], ln.get("who") or "")
        p = placed.get((shot["id"], i)) or {}
        audio = ln.get("audio")
        out.append({"id": ln.get("id") or "", "who": ln.get("who") or "",
                    "name": c["name"] if c else (ln.get("who") or NARRATOR), "text": ln.get("text") or "",
                    "at": ln.get("at"), "start": p.get("at", 0.0), "seconds": p.get("seconds", 0.0),
                    "over": p.get("over", 0.0),
                    "audio": {k: audio.get(k) for k in ("file", "seconds", "voice", "made")} if audio else None,
                    "state": C.line_state(series, ln, env), **({"error": ln["error"]} if ln.get("error") else {})})
    return out


def view(series: dict, env: dict | None = None) -> dict:
    """The series as the page shows it: each result's state (none, fresh or made before a change) and each
    episode's progress through its checkpoints."""
    s = copy.deepcopy(series)
    env = env_of(series, env)
    for c in s["cast"]:
        c["sheet_state"] = ("none" if not c.get("sheet") else
                            "fresh" if c.get("sheet_stamp") == sheet_plan(series, c, env)["stamp"] else "stale")
        c.setdefault("voice", "")
        c.setdefault("expressiveness", C.DEFAULT_EXPRESSIVENESS)
    for k in s["sets"]:
        k["picture_state"] = ("none" if not k.get("picture") else
                              "fresh" if k.get("picture_stamp") == set_plan(series, k, env)["stamp"] else "stale")
    s["style"].setdefault("narrator_voice", "")
    th = C.theme_of(series)
    s["theme"] = {**{k: v for k, v in th.items() if k != "file_stamp"}, "state": C.theme_state(series, env)}
    for e, orig in zip(s["episodes"], series["episodes"]):
        tl = C.timeline(series, orig)
        placed = {(x["shot"], x["i"]): x for x in tl["lines"]}
        fresh = kept = made = lines = spoken = wanted = effects = 0
        for sh, o in zip(e["shots"], orig["shots"]):
            sh["keyframe_state"] = keyframe_state(series, o, env)
            sh["clip_state"] = clip_state(series, o, env)
            sh["method_used"] = plan(series, o, env)["method"]
            sh["method_wanted"] = method_of(series, o)
            sh["missing"] = missing(series, o) if sh["method_wanted"] == "combine" else []
            sh["lines"] = lines_view(series, o, placed, env)
            sh["sfx_state"] = C.sfx_state(o, env)
            sh["seconds_used"] = C.shot_seconds(o)
            fresh += sh["keyframe_state"] == "fresh"
            made += sh["clip_state"] == "fresh"
            kept += sh["clip_state"] == "fresh" and bool((sh.get("clip") or {}).get("kept"))
            lines += len(sh["lines"])
            spoken += sum(ln["state"] == "fresh" for ln in sh["lines"])
            wanted += sh["sfx_state"] != "unwanted"
            effects += sh["sfx_state"] == "fresh"
        e["cut"] = C.cut_settings(orig)
        final, why = final_state(series, orig, env)
        e["progress"] = {"shots": len(e["shots"]), "seconds": round(sum(sh["seconds"] for sh in e["shots"]), 1),
                         "approved": bool(e.get("approved")), "keyframes": fresh, "clips": made, "kept": kept,
                         "preview": preview_state(series, orig, env), "lines": lines, "spoken": spoken,
                         "effects": wanted, "effects_made": effects, "final": final, "final_why": why,
                         "cut_missing": C.missing_for_cut(orig), "total": tl["total"]}
    return s


# ---------------------------------------------------------------- the storyboard

def storyboard_prompt(series: dict, episode: dict) -> str:
    st = series["style"]
    cast = "\n".join(f"- {c['name']}: {c.get('look') or '(no description)'}" for c in series["cast"]) or "- (none yet)"
    sets = "\n".join(f"- {k['name']}: {k.get('look') or '(no description)'}" for k in series["sets"]) or "- (none)"
    return STORYBOARD.format(series=series["name"], words=st.get("words") or "", rules=st.get("rules") or DEFAULT_RULES,
                             cast=cast, sets=sets, title=episode.get("title") or "", premise=episode.get("premise") or "",
                             max_seconds=st.get("max_seconds") or 30, language=st.get("language") or "English",
                             narrator=NARRATOR)


def parse_storyboard(text: str, series: dict) -> tuple[list[dict], list[str]]:
    """The chat model's shots, held to the series: names to the cast's and sets' ids (one not in the series is left
    out, and said), lengths of 1-10 s. -> (shots, notes)."""
    raw = text or ""
    start, end = raw.find("{"), raw.rfind("}")
    try:
        data = json.loads(raw[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list):
        return [], ["the chat model's answer was not a storyboard"]
    cast = {c["name"].lower(): c["id"] for c in series["cast"]}
    sets = {k["name"].lower(): k["id"] for k in series["sets"]}
    shots, notes = [], []
    for item in data["shots"][:MAX_SHOTS]:
        if not isinstance(item, dict):
            continue
        chars = []
        for name in item.get("characters") or []:
            cid = cast.get(_text(name, NAME_MAX).lower())
            if cid is None:
                notes.append(f"{_text(name, NAME_MAX)} is not in the cast: left out of a shot")
            elif cid not in chars:
                chars.append(cid)
        set_name = _text(item.get("set"), NAME_MAX)
        kid = sets.get(set_name.lower(), "")
        if set_name and not kid:
            notes.append(f"{set_name} is not one of the sets: a shot has none")
        lines = []
        for ln in item.get("lines") or []:
            if isinstance(ln, dict) and _text(ln.get("text"), LINE_MAX):
                who = _text(ln.get("who"), NAME_MAX)
                lines.append({"id": _id("l"), "who": cast.get(who.lower(), who or NARRATOR),
                              "text": _text(ln.get("text"), LINE_MAX)})
        shots.append(new_shot(seconds=item.get("seconds"), set=kid, characters=chars,
                              picture=item.get("picture"), motion=item.get("motion"), sound=item.get("sound"),
                              lines=lines[:MAX_LINES]))
    return shots, list(dict.fromkeys(notes))


def new_shot(**fields) -> dict:
    return {"id": _id("h"), "seconds": _seconds(fields.get("seconds", 5)), "set": fields.get("set") or "",
            "characters": list(fields.get("characters") or []), "method": "auto",
            "picture": _text(fields.get("picture"), TEXT_MAX), "motion": _text(fields.get("motion"), TEXT_MAX),
            "sound": _text(fields.get("sound"), TEXT_MAX), "lines": list(fields.get("lines") or []),
            "variants": [], "keyframe": "", "keyframe_stamp": "", "clip": None, "takes": []}


# ---------------------------------------------------------------- the studio

class Studio(SoundJobs):
    def __init__(self, hub):
        self.hub = hub
        self.root = Path(hub.env.data_dir) / "movies"
        self.locks: dict[str, asyncio.Lock] = {}
        self.tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.queues: dict[tuple[str, str], list[str]] = {}       # the shots a keyframes or clips job has left
        self.live: dict[tuple[str, str], dict] = {}              # what each job does now
        self.errors: dict[tuple[str, str], str] = {}             # why a job stopped (until it runs again)

    def env(self, series: dict) -> dict:
        """What the series' look means now: Settings' picture model when it names none (and whether that one can
        combine pictures), Settings' video model for a video from a picture when it names none, the speech model
        for the lines' language and the effect and music models (sound.py)."""
        from ..images.pipeline import abilities
        from ..video.pipeline import VideoError
        pipe, video = self.hub.extras.get("images"), self.hub.extras.get("video")
        st = series["style"]
        model = pipe.chosen(st.get("model") or None) if pipe else (st.get("model") or "")
        vid = st.get("video_model") or ""
        if video is not None:
            try:
                vid = video.chosen("i2v", vid or None)["id"]
            except VideoError:
                pass
        return {"model": model, "combine": bool(pipe) and "edit" in abilities(pipe.entry(model)), "video": vid,
                **self.audio_env(series)}

    def sweep(self) -> None:
        """At start-up: what a job that a restart cut off left - a preview's or a cut's folder in the workspace, a
        voice clip's, a file it was writing."""
        from .. import sandbox_job
        from ..audio import clips
        sandbox_job.sweep(self.hub, "movies-tmp")
        sandbox_job.sweep(self.hub, clips.TMP)
        if self.root.is_dir():
            for p in self.root.glob("*/**/.*.tmp"):
                if p.is_file() and not p.is_symlink():
                    p.unlink(missing_ok=True)

    async def sweep_after_start(self) -> None:
        self.sweep()

    # ------------------------------------------------------------ the sandbox
    def _tmp_folder(self) -> Path:
        """A folder of a job's own under the workspace, where the sandbox reaches it (removed by the job)."""
        from .. import sandbox_job
        return sandbox_job.tmp_folder(self.hub, "movies-tmp")

    async def _sandbox(self, folder: Path, cmd: str, timeout: int, out_name: str, failed: str) -> bytes:
        """Run a command in the sandbox in the job's folder - offline - and read the file it made (sandbox_job.py):
        an error when it failed, ran out of time or left nothing (or a link) behind."""
        from .. import sandbox_job
        try:
            return await sandbox_job.run(self.hub, folder, cmd, timeout, out_name, failed)
        except sandbox_job.SandboxJobError as exc:
            raise MovieError(str(exc)) from exc

    # ------------------------------------------------------------ storage
    def _dir(self, sid: str) -> Path:
        if not ID_RE.match(sid or ""):
            raise MovieError("No such series.", 404)
        return self.root / sid

    def load(self, sid: str) -> dict:
        try:
            data = json.loads((self._dir(sid) / "series.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise MovieError("No such series.", 404) from None
        for e in data.get("episodes") or []:      # lines from before 0.73.0: an id from what they are, kept when saved
            for sh in e.get("shots") or []:
                for i, ln in enumerate(sh.get("lines") or []):
                    if not ln.get("id"):
                        ln["id"] = "l" + _stamp(sh.get("id"), i, ln.get("who"), ln.get("text"))[:6]
        return data

    def _save(self, series: dict) -> None:
        d = self._dir(series["id"])
        d.mkdir(parents=True, exist_ok=True)
        series["updated"] = _now()
        tmp = d / f".series.{secrets.token_hex(4)}.tmp"
        tmp.write_text(json.dumps(series, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(d / "series.json")

    @asynccontextmanager
    async def edit(self, sid: str):
        """The series to change, saved afterwards - one change at a time, so a job's result and the owner's edit do
        not overwrite each other."""
        lock = self.locks.setdefault(sid, asyncio.Lock())
        async with lock:
            series = self.load(sid)
            yield series
            self._tidy(series)
            self._save(series)

    def path(self, sid: str, rel: str) -> Path:
        if not FILE_RE.match(rel or ""):
            raise MovieError("No such file.", 404)
        p = self._dir(sid) / rel
        if p.is_symlink() or not p.resolve().is_relative_to(self._dir(sid).resolve()):
            raise MovieError("No such file.", 404)
        return p

    def file(self, sid: str, rel: str) -> Path:
        p = self.path(sid, rel)
        if not p.is_file():
            raise MovieError("No such file.", 404)
        return p

    def _put(self, sid: str, rel: str, data: bytes) -> str:
        p = self.path(sid, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{secrets.token_hex(3)}.tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return rel

    @staticmethod
    def _files(series: dict) -> set[str]:
        """Every file the series points at."""
        out: set[str] = set()
        for c in series["cast"]:
            out |= {v["file"] for v in c.get("variants") or []} | ({c["sheet"]} if c.get("sheet") else set())
            if c.get("voice_clip"):
                out.add(c["voice_clip"])
        for k in series["sets"]:
            out |= {v["file"] for v in k.get("variants") or []} | ({k["picture"]} if k.get("picture") else set())
        theme = series.get("theme") or {}
        out |= {v["file"] for v in theme.get("variants") or []} | ({theme["file"]} if theme.get("file") else set())
        for e in series["episodes"]:
            out |= {x["file"] for x in (e.get("preview"), e.get("final")) if x}
            for sh in e["shots"]:
                out |= {v["file"] for v in sh.get("variants") or []}
                if sh.get("keyframe"):
                    out.add(sh["keyframe"])
                out |= {c["file"] for c in [sh.get("clip"), *(sh.get("takes") or []), sh.get("sfx")] if c}
                out |= {ln["audio"]["file"] for ln in sh.get("lines") or [] if (ln.get("audio") or {}).get("file")}
        return out

    def _tidy(self, series: dict) -> None:
        """Files nothing points at any more go (pictures left behind, an episode deleted)."""
        d = self._dir(series["id"])
        keep = self._files(series)
        for p in d.glob("*/*"):
            rel = f"{p.parent.name}/{p.name}"
            if p.is_file() and not p.name.startswith(".") and rel not in keep:
                p.unlink(missing_ok=True)
        for sub in d.iterdir():
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()

    # ------------------------------------------------------------ series
    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for d in self.root.iterdir():
                try:
                    s = self.load(d.name)
                except MovieError:
                    continue
                cover = next((c["sheet"] for c in s["cast"] if c.get("sheet")), "") or \
                    next((k["picture"] for k in s["sets"] if k.get("picture")), "")
                out.append({"id": s["id"], "name": s["name"], "episodes": len(s["episodes"]), "cast": len(s["cast"]),
                            "cover": cover, "updated": s.get("updated") or 0, "working": self.working(s["id"])})
        return sorted(out, key=lambda x: -x["updated"])

    def options(self) -> dict:
        """What the page offers: the picture models (and which can combine pictures), the LoRAs with their family,
        the video models that start from a picture, and the model that writes storyboards."""
        from ..images.pipeline import abilities, family
        pipe, video, store = (self.hub.extras.get(k) for k in ("images", "video", "loras"))
        models = [{"id": e["id"], "name": e["name"], "family": family(e), "combine": "edit" in abilities(e)}
                  for e in (pipe.installed("generate") if pipe else [])]
        default = pipe.chosen(None) if pipe else ""
        default_entry = pipe.entry(default) if pipe else None
        loras = [{k: x.get(k) for k in ("id", "name", "base", "base_label", "trigger", "strength", "slider",
                                        "category")} for x in (store.list() if store else [])]
        videos = [{"id": e["id"], "name": e["name"]} for e in (video.installed("i2v") if video else [])]
        from ..audio.pipeline import LANGUAGE_NAMES
        writer = self.writer()
        return {"models": models, "default_model": {"id": default, "name": (default_entry or {}).get("name") or default,
                                                    "family": family(default_entry)},
                "loras": loras, "video_models": videos,
                "default_video": video.chosen("i2v")["name"] if video and video.available("i2v") else "",
                "writer": (writer.get("name") or writer["id"]) if writer else "", "formats": list(FORMATS),
                "languages": list(LANGUAGE_NAMES.values()), "defaults": DEFAULT_STYLE}

    def create(self, name: str) -> dict:
        name = _text(name, NAME_MAX)
        if not name:
            raise MovieError("Give the series a name.")
        sid = secrets.token_hex(4)
        series = {"id": sid, "name": name, "created": _now(), "updated": _now(), "style": dict(DEFAULT_STYLE),
                  "cast": [], "sets": [], "episodes": []}
        self._save(series)
        return self.detail(sid)

    def detail(self, sid: str) -> dict:
        series = self.load(sid)
        out = view(series, self.env(series))
        out["audio"] = self.audio_info(series)
        out["jobs"] = {key: {**self.live.get((sid, key), {}), "queued": list(self.queues.get((sid, key), []))}
                       for (s, key), task in self.tasks.items() if s == sid and not task.done()}
        out["errors"] = {key: err for (s, key), err in self.errors.items() if s == sid}
        return out

    def working(self, sid: str, key: str | None = None) -> bool:
        return any(not t.done() for (s, k), t in self.tasks.items() if s == sid and (key is None or k == key))

    async def update(self, sid: str, name: str | None = None, style: dict | None = None,
                     theme: dict | None = None) -> dict:
        async with self.edit(sid) as s:
            if name is not None:
                if not _text(name, NAME_MAX):
                    raise MovieError("Give the series a name.")
                s["name"] = _text(name, NAME_MAX)
            if style:
                s["style"].update(self._clean_style(style))
                if "narrator_voice" in style:               # a voice of the speech model for the language set now
                    s["style"]["narrator_voice"] = self._voice_name(s, style["narrator_voice"])
            if theme:
                s.setdefault("theme", {}).update(self._clean_theme(theme))
        return self.detail(sid)

    def _voice_name(self, series: dict, value) -> str:
        """One of the speech model's own voices ("" is its default) - any name while none is downloaded."""
        name = _text(value, 40).lower()
        info = self.audio_info(series)
        names = [v["id"] for v in info["voices"]]
        if name and names and name not in names:
            raise MovieError(f"{info['speech']} has no voice called {name} - its voices: {', '.join(names)}.")
        return name

    @staticmethod
    def _clean_theme(theme: dict) -> dict:
        out: dict = {}
        if theme.get("prompt") is not None:
            out["prompt"] = _text(theme["prompt"], WORDS_MAX) or C.DEFAULT_THEME
        if theme.get("seconds") is not None:
            out["seconds"] = _whole(_given(theme["seconds"], "The theme's length"), *C.THEME_SECONDS,
                                    C.THEME_DEFAULT_SECONDS)
        return out

    def _clean_style(self, style: dict) -> dict:
        out: dict = {}
        for key, value in style.items():
            if key == "model":
                value = str(value or "")
                if value and self.hub.catalog.get("comfy", value) is None:
                    raise MovieError(f"There is no picture model called {value}.")
                out[key] = value
            elif key == "video_model":
                value = str(value or "")
                if value and self.hub.catalog.get("video", value) is None:
                    raise MovieError(f"There is no video model called {value}.")
                out[key] = value
            elif key == "lora":
                value = str(value or "")
                store = self.hub.extras.get("loras")
                if value and (store is None or store.get(value) is None):
                    raise MovieError("That LoRA is not there (Settings -> Models -> LoRAs).")
                out[key] = value
            elif key == "strength":
                out[key] = _strength(_given(value, "The strength"), 0.8)
            elif key == "words":
                out[key] = _text(value, WORDS_MAX)
            elif key == "format":
                if value not in FORMATS:
                    raise MovieError("The format is portrait, landscape or square.")
                out[key] = value
            elif key == "variants":
                out[key] = _whole(_given(value, "The number of pictures"), 1, 4, 4)
            elif key == "quality":
                out[key] = value if value in QUALITIES else "fast"
            elif key == "max_seconds":
                out[key] = _whole(_given(value, "The longest episode"), 5, 120, 30)
            elif key == "language":
                out[key] = _text(value, 40) or "English"
            elif key == "rules":
                out[key] = _para(value, TEXT_MAX) or DEFAULT_RULES
        return out

    def delete(self, sid: str) -> None:
        if self.working(sid):
            raise MovieError("It is working - stop it first.", 409)
        d = self._dir(sid)
        if not (d / "series.json").is_file():
            raise MovieError("No such series.", 404)
        shutil.rmtree(d, ignore_errors=True)
        for key in [k for k in self.errors if k[0] == sid]:
            self.errors.pop(key, None)

    # ------------------------------------------------------------ cast and sets
    async def add_member(self, sid: str, kind: str, fields: dict) -> dict:
        async with self.edit(sid) as s:
            items = s["cast"] if kind == "cast" else s["sets"]
            if len(items) >= (MAX_CAST if kind == "cast" else MAX_SETS):
                raise MovieError(f"A series has at most {MAX_CAST if kind == 'cast' else MAX_SETS} of these.")
            item = {"id": _id("c" if kind == "cast" else "k"), "name": "", "look": "", "variants": []}
            if kind == "cast":
                item.update(lora="", strength=1.0, sheet="", sheet_stamp="", voice="",
                            expressiveness=C.DEFAULT_EXPRESSIVENESS)
            else:
                item.update(picture="", picture_stamp="")
            item.update(self._clean_member(s, kind, fields, item["id"]))
            if not item["name"]:
                raise MovieError("Give it a name.")
            items.append(item)
        return self.detail(sid)

    def _clean_member(self, series: dict, kind: str, fields: dict, iid: str) -> dict:
        out: dict = {}
        items = series["cast"] if kind == "cast" else series["sets"]
        if "name" in fields and fields["name"] is not None:
            name = _text(fields["name"], NAME_MAX)
            if not name:
                raise MovieError("Give it a name.")
            if any(x["name"].lower() == name.lower() and x["id"] != iid for x in items):
                raise MovieError(f"There is already one called {name}.")
            out["name"] = name
        if fields.get("look") is not None:
            out["look"] = _text(fields["look"], WORDS_MAX)
        if kind == "cast":
            if fields.get("lora") is not None:
                lora = str(fields["lora"] or "")
                store = self.hub.extras.get("loras")
                if lora and (store is None or store.get(lora) is None):
                    raise MovieError("That LoRA is not there (Settings -> Models -> LoRAs).")
                out["lora"] = lora
            if fields.get("strength") is not None:
                out["strength"] = _strength(_given(fields["strength"], "The strength"), 1.0)
            if fields.get("voice") is not None:             # the speech model's own voice, or "clip": their clip
                if fields["voice"] == "clip":
                    if not (_by_id(items, iid) or {}).get("voice_clip"):
                        raise MovieError("Give a clip of the voice first.")
                    out["voice"] = "clip"
                else:
                    out["voice"] = self._voice_name(series, fields["voice"])
            if fields.get("expressiveness") is not None:
                lo, hi = C.EXPRESSIVENESS
                out["expressiveness"] = round(max(lo, min(hi, _given(fields["expressiveness"], "The expression"))), 2)
        return out

    async def change_member(self, sid: str, kind: str, iid: str, fields: dict) -> dict:
        async with self.edit(sid) as s:
            item = self._member(s, kind, iid)
            item.update(self._clean_member(s, kind, fields, iid))
        return self.detail(sid)

    async def delete_member(self, sid: str, kind: str, iid: str) -> dict:
        if self.working(sid, f"{kind}:{iid}"):
            raise MovieError("It is working - stop it first.", 409)
        async with self.edit(sid) as s:
            self._member(s, kind, iid)
            if kind == "cast":
                name = self._member(s, kind, iid)["name"]
                s["cast"] = [c for c in s["cast"] if c["id"] != iid]
                for e in s["episodes"]:
                    for sh in e["shots"]:
                        sh["characters"] = [c for c in sh["characters"] if c != iid]
                        for ln in sh.get("lines") or []:
                            if ln.get("who") == iid:
                                ln["who"] = name              # the line keeps who said it
            else:
                s["sets"] = [k for k in s["sets"] if k["id"] != iid]
                for e in s["episodes"]:
                    for sh in e["shots"]:
                        if sh["set"] == iid:
                            sh["set"] = ""
        return self.detail(sid)

    @staticmethod
    def _member(series: dict, kind: str, iid: str) -> dict:
        item = _by_id(series["cast"] if kind == "cast" else series["sets"], iid)
        if item is None:
            raise MovieError("No such character." if kind == "cast" else "No such set.", 404)
        return item

    async def pick_member(self, sid: str, kind: str, iid: str, file: str, keep: bool = False) -> dict:
        """Choose a sheet (or a set's picture) among those made - or keep the chosen one as it is after a change."""
        async with self.edit(sid) as s:
            item = self._member(s, kind, iid)
            field, stamp = ("sheet", "sheet_stamp") if kind == "cast" else ("picture", "picture_stamp")
            env = self.env(s)
            now = (sheet_plan(s, item, env) if kind == "cast" else set_plan(s, item, env))["stamp"]
            if keep:
                if not item.get(field):
                    raise MovieError("Nothing is chosen yet.")
                item[stamp] = now
            else:
                v = next((v for v in item.get("variants") or [] if v["file"] == file), None)
                if v is None:
                    raise MovieError("No such picture.", 404)
                item[field], item[stamp] = v["file"], v["stamp"]
        return self.detail(sid)

    def make_member(self, sid: str, kind: str, iid: str) -> None:
        s = self.load(sid)
        item = self._member(s, kind, iid)
        if kind == "cast" and not (item.get("look") or item.get("lora")):
            raise MovieError(f"Describe how {item['name']} looks, or give a LoRA, first.")
        self._pictures_ready()
        self._start(sid, f"{kind}:{iid}", self._make_member(sid, kind, iid))

    async def _pictures(self, call) -> list[tuple[dict, bytes]]:
        """The picture pipeline's pictures, with their bytes - and what it stored as uploads goes again: the
        series keeps its own copies (and deleting a series frees them)."""
        from ..chats import store as chats
        from ..projects import blobs
        made: set[str] = set()
        listening = chats.upload_listener.set(lambda sha, new: made.add(sha) if new else None)
        try:
            items = await call()
            return [(it, blobs.read(it["sha"])) for it in items]
        finally:
            chats.upload_listener.reset(listening)
            for sha in made:
                try:
                    chats.forget_upload(sha)
                except Exception:  # noqa: BLE001 - the rest still goes
                    log.exception("could not delete %s", sha[:12])

    async def _make_member(self, sid: str, kind: str, iid: str) -> None:
        key = (sid, f"{kind}:{iid}")
        s = self.load(sid)
        item = self._member(s, kind, iid)
        env = self.env(s)
        p = sheet_plan(s, item, env) if kind == "cast" else set_plan(s, item, env)
        w, h = FORMATS[s["style"]["format"]]
        self.live[key] = {"what": "sheet" if kind == "cast" else "set", "name": item["name"]}
        from ..images.pipeline import ImageError
        pipe = self.hub.extras["images"]
        try:
            pictures = await self._pictures(lambda: pipe.generate(
                p["prompt"], w, h, count=s["style"]["variants"], model=s["style"]["model"] or None, owner="movies",
                priority=PRIORITY, on_progress=self._progress(key), loras=p["loras"]))
        except ImageError as exc:
            raise MovieError(f"The pictures failed: {exc}") from exc
        async with self.edit(sid) as s:
            item = _by_id(s["cast"] if kind == "cast" else s["sets"], iid)
            if item is None:
                return                                    # deleted meanwhile
            tok = secrets.token_hex(3)
            folder = "cast" if kind == "cast" else "sets"
            new = [{"file": self._put(sid, f"{folder}/{iid}-{tok}-{i + 1}.png", data),
                    "stamp": p["stamp"], "seed": it.get("seed"), "made": _now(),
                    **({"notes": it["lora_notes"]} if it.get("lora_notes") else {})}
                   for i, (it, data) in enumerate(pictures)]
            item["variants"] = self._trim(new + (item.get("variants") or []),
                                          item.get("sheet") if kind == "cast" else item.get("picture"))

    @staticmethod
    def _trim(variants: list[dict], chosen: str) -> list[dict]:
        """The newest MAX_VARIANTS, and the chosen one always."""
        out = variants[:MAX_VARIANTS]
        if chosen and all(v["file"] != chosen for v in out):
            out += [v for v in variants if v["file"] == chosen]
        return out

    # ------------------------------------------------------------ episodes and shots
    async def add_episode(self, sid: str, title: str, premise: str = "") -> dict:
        async with self.edit(sid) as s:
            if len(s["episodes"]) >= MAX_EPISODES:
                raise MovieError(f"A series has at most {MAX_EPISODES} episodes.")
            title = _text(title, 80) or f"Episode {len(s['episodes']) + 1}"
            e = {"id": _id("e"), "title": title, "premise": _para(premise, TEXT_MAX), "approved": False,
                 "notes": [], "shots": [], "preview": None, "created": _now()}
            s["episodes"].append(e)
        out = self.detail(sid)
        out["new_episode"] = e["id"]
        return out

    @staticmethod
    def _episode(series: dict, eid: str) -> dict:
        e = _by_id(series["episodes"], eid)
        if e is None:
            raise MovieError("No such episode.", 404)
        return e

    @staticmethod
    def _shot(episode: dict, hid: str) -> dict:
        sh = _by_id(episode["shots"], hid)
        if sh is None:
            raise MovieError("No such shot.", 404)
        return sh

    async def change_episode(self, sid: str, eid: str, title: str | None = None, premise: str | None = None,
                             approved: bool | None = None, cut: dict | None = None) -> dict:
        async with self.edit(sid) as s:
            e = self._episode(s, eid)
            if title is not None:
                e["title"] = _text(title, 80) or e["title"]
            if premise is not None:
                e["premise"] = _para(premise, TEXT_MAX)
            if approved is not None:
                if approved and not e["shots"]:
                    raise MovieError("There are no shots to approve yet.")
                if approved and any(not sh["picture"] for sh in e["shots"]):
                    raise MovieError("Every shot needs a picture description first.")
                e["approved"] = bool(approved)
            if cut:
                e["cut"] = {**C.cut_settings(e), **self._clean_cut(cut)}
        return self.detail(sid)

    @staticmethod
    def _clean_cut(cut: dict) -> dict:
        """The cut's settings: the theme under it or not, the volumes (0-2), the text on screen, the end card."""
        out: dict = {}
        for key, value in cut.items():
            if key in ("music", "end_card"):
                out[key] = bool(value)
            elif key in C.VOLUMES:
                out[key] = round(max(0.0, min(2.0, _given(value, "The volume"))), 2)
            elif key == "text":
                if value not in C.TEXT_STYLES:
                    raise MovieError("The text on screen is bubbles, captions or none.")
                out[key] = value
        return out

    async def delete_episode(self, sid: str, eid: str) -> dict:
        if any(self.working(sid, f"{what}:{eid}") for what in EPISODE_JOBS):
            raise MovieError("It is working - stop it first.", 409)
        async with self.edit(sid) as s:
            self._episode(s, eid)
            s["episodes"] = [e for e in s["episodes"] if e["id"] != eid]
        return self.detail(sid)

    async def add_shot(self, sid: str, eid: str, after: str | None = None) -> dict:
        async with self.edit(sid) as s:
            e = self._episode(s, eid)
            if len(e["shots"]) >= MAX_SHOTS:
                raise MovieError(f"An episode has at most {MAX_SHOTS} shots.")
            at = next((i + 1 for i, sh in enumerate(e["shots"]) if sh["id"] == after), len(e["shots"]))
            e["shots"].insert(at, new_shot())
        return self.detail(sid)

    def _not_writing(self, sid: str, eid: str) -> None:
        if self.working(sid, f"storyboard:{eid}"):
            raise MovieError("It is writing the storyboard - wait for it.", 409)

    async def change_shot(self, sid: str, eid: str, hid: str, fields: dict) -> dict:
        async with self.edit(sid) as s:
            e = self._episode(s, eid)
            sh = self._shot(e, hid)
            for key in ("picture", "motion", "sound"):
                if fields.get(key) is not None:
                    sh[key] = _text(fields[key], TEXT_MAX)
            if fields.get("seconds") is not None:
                sh["seconds"] = _seconds(_given(fields["seconds"], "The seconds"))
            if fields.get("set") is not None:
                if fields["set"] and _by_id(s["sets"], fields["set"]) is None:
                    raise MovieError("No such set.")
                sh["set"] = fields["set"] or ""
            if fields.get("characters") is not None:          # (one no longer in the cast is left out)
                chars = [cid for cid in dict.fromkeys(fields["characters"]) if _by_id(s["cast"], cid) is not None]
                sh["characters"] = chars[:MAX_REFS]
            if fields.get("toggle_character"):                 # one tap on the page: in or out, whatever the rest
                cid = fields["toggle_character"]
                if cid in sh["characters"]:
                    sh["characters"].remove(cid)
                elif _by_id(s["cast"], cid) is None:
                    raise MovieError("No such character.")
                elif len(sh["characters"]) < MAX_REFS:
                    sh["characters"].append(cid)
            if fields.get("method") is not None:
                if fields["method"] not in METHODS:
                    raise MovieError("The method is auto, new or combine.")
                sh["method"] = fields["method"]
            if fields.get("lines") is not None:
                sh["lines"] = self._edited_lines(s, sh.get("lines") or [], fields["lines"])
            if fields.get("move") in (-1, 1):
                i = e["shots"].index(sh)
                j = i + fields["move"]
                if 0 <= j < len(e["shots"]):
                    e["shots"][i], e["shots"][j] = e["shots"][j], e["shots"][i]
        return self.detail(sid)

    @staticmethod
    def _edited_lines(series: dict, old: list[dict], given: list) -> list[dict]:
        """A shot's lines as the owner wrote them: a line kept word for word keeps its id, its take and its time; a
        changed one in the same place keeps its id and time (a typo fixed), not its take; a new one is new."""
        new: list[dict] = []
        for ln in given[:MAX_LINES]:
            text = _text((ln or {}).get("text"), LINE_MAX)
            if text:
                who = _text((ln or {}).get("who"), NAME_MAX) or NARRATOR
                c = _by_id(series["cast"], who) or next((c for c in series["cast"]
                                                         if c["name"].lower() == who.lower()), None)
                new.append({"who": c["id"] if c else who, "text": text})
        left = list(old)

        def take(line: dict) -> bool:
            for k, o in enumerate(left):
                if o is line:
                    del left[k]
                    return True
            return False
        for ln in new:
            same = next((o for o in left if (o.get("who"), o.get("text")) == (ln["who"], ln["text"])), None)
            if same is not None and take(same):
                ln.update({k: same[k] for k in ("id", "audio", "at") if k in same})
        for i, ln in enumerate(new):
            if "id" not in ln:
                if i < len(old) and take(old[i]):
                    ln.update({k: old[i][k] for k in ("id", "at") if k in old[i]})
                ln.setdefault("id", _id("l"))
        return new

    async def delete_shot(self, sid: str, eid: str, hid: str) -> dict:
        for what in QUEUED_JOBS:
            if self.live.get((sid, f"{what}:{eid}"), {}).get("shot") == hid:
                raise MovieError("It is making something for this shot right now - stop it first.", 409)
        async with self.edit(sid) as s:
            e = self._episode(s, eid)
            self._shot(e, hid)
            e["shots"] = [sh for sh in e["shots"] if sh["id"] != hid]
        for what in QUEUED_JOBS:
            q = self.queues.get((sid, f"{what}:{eid}"))
            if q:
                q[:] = [x for x in q if x.partition(":")[0] != hid]     # in place: the job reads this list
        return self.detail(sid)

    async def pick_keyframe(self, sid: str, eid: str, hid: str, file: str = "", keep: bool = False) -> dict:
        async with self.edit(sid) as s:
            sh = self._shot(self._episode(s, eid), hid)
            if keep:
                if not sh.get("keyframe"):
                    raise MovieError("No keyframe is chosen yet.")
                sh["keyframe_stamp"] = plan(s, sh, self.env(s))["stamp"]
            else:
                v = next((v for v in sh.get("variants") or [] if v["file"] == file), None)
                if v is None:
                    raise MovieError("No such picture.", 404)
                sh["keyframe"], sh["keyframe_stamp"] = v["file"], v["stamp"]
        return self.detail(sid)

    async def keep_clip(self, sid: str, eid: str, hid: str, kept: bool = True, take: str = "",
                        accept: bool = False) -> dict:
        """Keep a clip (the checkpoint before the preview), go back to an earlier take, or accept one made before a
        change as it is."""
        async with self.edit(sid) as s:
            sh = self._shot(self._episode(s, eid), hid)
            if take:
                old = next((c for c in sh.get("takes") or [] if c["file"] == take), None)
                if old is None:
                    raise MovieError("No such take.", 404)
                sh["takes"] = [c for c in sh["takes"] if c["file"] != take]
                if sh.get("clip"):
                    sh["takes"].insert(0, sh["clip"])
                sh["clip"] = old
            if not sh.get("clip"):
                raise MovieError("There is no clip yet.")
            if accept:
                env = self.env(s)
                if keyframe_state(s, sh, env) != "fresh":
                    raise MovieError("Its keyframe is from before a change - keep or remake that first.")
                sh["clip"]["stamp"] = clip_stamp(s, sh, env)
            if not take:
                sh["clip"]["kept"] = bool(kept)
        return self.detail(sid)

    # ------------------------------------------------------------ the storyboard
    def writer(self) -> dict | None:
        """The chat model when it is downloaded: it writes the storyboard."""
        entry = self.hub.catalog.get_llm(self.hub.settings.get("models", "default") or "")
        return entry if entry and self.hub.catalog.installed(entry) else None

    def write_storyboard(self, sid: str, eid: str, replace: bool = False) -> None:
        s = self.load(sid)
        e = self._episode(s, eid)
        if not e["premise"].strip():
            raise MovieError("Write what happens first (the premise).")
        if not s["cast"]:
            raise MovieError("Add the cast first.")
        if self.writer() is None:
            raise MovieError("No chat model is downloaded to write it (Settings -> Models).")
        if e["shots"] and not replace:
            raise MovieError("The episode has shots: writing the storyboard again replaces them, with their "
                             "pictures and clips.", 409)
        if any(self.working(sid, f"{what}:{eid}") for what in EPISODE_JOBS if what != "storyboard"):
            raise MovieError("It is working on this episode - stop that first.", 409)
        self._start(sid, f"storyboard:{eid}", self._storyboard(sid, eid, replace))

    async def _storyboard(self, sid: str, eid: str, replace: bool = False) -> None:
        from ..llm import payload as P
        from ..llm.stream import Accumulator
        key = (sid, f"storyboard:{eid}")
        s = self.load(sid)
        e = self._episode(s, eid)
        entry = self.writer()
        if entry is None:
            raise MovieError("No chat model is downloaded to write it (Settings -> Models).")
        self.live[key] = {"what": "storyboard", "model": entry.get("name") or entry["id"]}
        before = [sh["id"] for sh in e["shots"]]
        pl = P.build(entry, [{"role": "user", "content": storyboard_prompt(s, e)}], thinking=False,
                     sampling={"max_tokens": 4000},
                     response_format={"type": "json_schema", "json_schema": {"name": "storyboard",
                                                                             "schema": STORYBOARD_SCHEMA}})
        from ..gpu.scheduler import SchedulerError
        from ..llm.client import LlmError
        try:
            ticket = await self.hub.scheduler.acquire("movies", "storyboard", self.hub.model_need(entry["id"]),
                                                      "interactive", label=f"Storyboard: {e['title']}"[:80],
                                                      meta={"who": "owner"})
            try:
                acc = Accumulator()
                async for chunk in self.hub.llm.stream_chat(self.hub.main_url, pl):
                    acc.feed(chunk)
                    self.live[key]["chars"] = len(acc.content)
                text = acc.result().get("content") or ""
            finally:
                await ticket.release()
        except (LlmError, SchedulerError) as exc:
            raise MovieError(f"The chat model could not write it: {exc}") from exc
        async with self.edit(sid) as s:                   # the series as it is now: a character deleted meanwhile
            e = _by_id(s["episodes"], eid)
            if e is None:
                return
            if not replace and [sh["id"] for sh in e["shots"]] != before:
                raise MovieError("Shots were added while it wrote - write it again to replace them.")
            shots, notes = parse_storyboard(text, s)
            if not shots:
                raise MovieError("The chat model gave no storyboard - try again, or write the shots yourself.")
            e["shots"], e["notes"], e["approved"], e["preview"] = shots, notes, False, None

    # ------------------------------------------------------------ keyframes and clips
    def _pictures_ready(self) -> None:
        pipe = self.hub.extras.get("images")
        if pipe is None or not pipe.available():
            raise MovieError("No picture model is downloaded (Settings -> Models).")

    def make_keyframes(self, sid: str, eid: str, shots: list[str] | None = None) -> None:
        """The shots' keyframes - the given ones, or each without a chosen keyframe that is up to date. Shots added
        while it works join its queue."""
        s = self.load(sid)
        e = self._episode(s, eid)
        self._not_writing(sid, eid)
        if not e.get("approved"):
            raise MovieError("Approve the storyboard first.")
        env = self.env(s)
        todo = [sh["id"] for sh in e["shots"]
                if (sh["id"] in shots if shots else keyframe_state(s, sh, env) != "fresh")]
        for hid in todo:
            if not self._shot(e, hid)["picture"]:
                raise MovieError("Every shot needs a picture description first.")
        if not todo:
            raise MovieError("Every shot has an up-to-date keyframe.")
        self._pictures_ready()
        self._enqueue(sid, f"keyframes:{eid}", todo, lambda: self._keyframes(sid, eid))

    def make_clips(self, sid: str, eid: str, shots: list[str] | None = None) -> None:
        s = self.load(sid)
        e = self._episode(s, eid)
        self._not_writing(sid, eid)
        env = self.env(s)
        if shots:
            todo = [sh["id"] for sh in e["shots"] if sh["id"] in shots]
        else:
            todo = [sh["id"] for sh in e["shots"]
                    if keyframe_state(s, sh, env) == "fresh" and clip_state(s, sh, env) != "fresh"]
        if not todo:
            raise MovieError("No shot has a chosen, up-to-date keyframe without a clip.")
        for hid in todo:
            if keyframe_state(s, self._shot(e, hid), env) != "fresh":
                raise MovieError("A shot's keyframe is missing or from before a change - choose or keep it first.")
        video = self.hub.extras.get("video")
        if video is None or not video.available("i2v"):
            raise MovieError("No video model that makes a video from a picture is downloaded (Settings -> Models).")
        self._enqueue(sid, f"clips:{eid}", todo, lambda: self._clips(sid, eid))

    async def _keyframes(self, sid: str, eid: str) -> None:
        from ..images.pipeline import ImageError
        key = (sid, f"keyframes:{eid}")
        queue = self.queues.setdefault(key, [])
        done = 0
        while queue:
            hid = queue.pop(0)
            s = self.load(sid)
            e = _by_id(s["episodes"], eid)
            sh = _by_id(e["shots"], hid) if e else None
            if sh is None:
                continue
            p = plan(s, sh, self.env(s))
            done += 1
            self.live[key] = {"what": "keyframes", "shot": hid, "number": e["shots"].index(sh) + 1, "done": done - 1,
                              "total": done + len(queue)}
            st = s["style"]
            try:
                refs = [self.file(sid, f).read_bytes() for f in p["refs"]]
                pipe = self.hub.extras["images"]
                w, h = FORMATS[st["format"]]
                if p["method"] == "combine":
                    pictures = await self._pictures(lambda: pipe.edit(
                        p["prompt"], refs, model=st["model"] or None, owner="movies", priority=PRIORITY,
                        on_progress=self._progress(key), count=st["variants"], loras=p["loras"]))
                else:
                    pictures = await self._pictures(lambda: pipe.generate(
                        p["prompt"], w, h, count=st["variants"], model=st["model"] or None, owner="movies",
                        priority=PRIORITY, on_progress=self._progress(key), loras=p["loras"]))
            except (ImageError, MovieError, OSError) as exc:
                log.warning("keyframes for %s/%s: %s", sid, hid, exc)
                async with self.edit(sid) as s2:
                    e2 = _by_id(s2["episodes"], eid)
                    sh2 = _by_id(e2["shots"], hid) if e2 else None
                    if sh2 is not None:
                        sh2["keyframe_error"] = f"The pictures failed: {exc}"
                continue
            async with self.edit(sid) as s2:
                e2 = _by_id(s2["episodes"], eid)
                sh2 = _by_id(e2["shots"], hid) if e2 else None
                if sh2 is None:
                    continue                              # deleted meanwhile
                tok = secrets.token_hex(3)
                new = [{"file": self._put(sid, f"{eid}/{hid}-{tok}-{i + 1}.png", data),
                        "stamp": p["stamp"], "seed": it.get("seed"), "method": p["method"], "made": _now(),
                        **({"notes": it["lora_notes"]} if it.get("lora_notes") else {})}
                       for i, (it, data) in enumerate(pictures)]
                sh2["variants"] = self._trim(new + (sh2.get("variants") or []), sh2.get("keyframe") or "")
                sh2.pop("keyframe_error", None)

    async def _clips(self, sid: str, eid: str) -> None:
        from ..video.pipeline import VideoError
        key = (sid, f"clips:{eid}")
        queue = self.queues.setdefault(key, [])
        done = 0
        while queue:
            hid = queue.pop(0)
            s = self.load(sid)
            e = _by_id(s["episodes"], eid)
            sh = _by_id(e["shots"], hid) if e else None
            env = self.env(s)
            if sh is None or keyframe_state(s, sh, env) != "fresh":
                continue
            done += 1
            self.live[key] = {"what": "clips", "shot": hid, "number": e["shots"].index(sh) + 1, "done": done - 1,
                              "total": done + len(queue)}
            st = s["style"]
            stamp, made_from = clip_stamp(s, sh, env), sh["keyframe"]
            try:
                out = await self.hub.extras["video"].generate(
                    prompt=motion_prompt(s, sh), image=self.file(sid, sh["keyframe"]).read_bytes(),
                    seconds=sh["seconds"], quality=st["quality"], model=st["video_model"] or None, owner="movies",
                    priority=PRIORITY, on_progress=self._progress(key))
            except (VideoError, MovieError, OSError) as exc:
                log.warning("clip for %s/%s: %s", sid, hid, exc)
                async with self.edit(sid) as s2:
                    e2 = _by_id(s2["episodes"], eid)
                    sh2 = _by_id(e2["shots"], hid) if e2 else None
                    if sh2 is not None:
                        sh2["clip_error"] = f"The clip failed: {exc}"
                continue
            async with self.edit(sid) as s2:
                e2 = _by_id(s2["episodes"], eid)
                sh2 = _by_id(e2["shots"], hid) if e2 else None
                if sh2 is None:
                    continue
                clip = {"file": self._put(sid, f"{eid}/{hid}-{out['seed']}.mp4", out["data"]), "seed": out["seed"],
                        "seconds": out["seconds"], "width": out["width"], "height": out["height"], "fps": out["fps"],
                        "model": out["model"].get("name") or out["model"].get("id"), "stamp": stamp,
                        "keyframe": made_from, "kept": False, "made": _now()}
                if sh2.get("clip"):
                    sh2["takes"] = [sh2["clip"], *(sh2.get("takes") or [])][:MAX_TAKES - 1]
                sh2["clip"] = clip
                sh2.pop("clip_error", None)

    # ------------------------------------------------------------ the preview
    def make_preview(self, sid: str, eid: str) -> None:
        s = self.load(sid)
        e = self._episode(s, eid)
        self._not_writing(sid, eid)
        if not any(sh.get("clip") for sh in e["shots"]):
            raise MovieError("There are no clips to join yet.")
        self._start(sid, f"preview:{eid}", self._preview(sid, eid))

    async def _preview(self, sid: str, eid: str) -> None:
        """The clips joined in order, silent, in the first clip's size and frame rate - by ffmpeg in the sandbox, in a
        folder of its own that is removed afterwards (the series' own folder is out of the sandbox's reach)."""
        from ..projects import store as projects
        key = (sid, f"preview:{eid}")
        s = self.load(sid)
        e = self._episode(s, eid)
        clips = [sh["clip"] for sh in e["shots"] if sh.get("clip")]
        stamp = preview_stamp(e)
        self.live[key] = {"what": "preview", "total": len(clips)}
        folder = self._tmp_folder()
        try:
            names = []
            for i, c in enumerate(clips):
                name = f"c{i + 1}.mp4"
                projects.safe_write(folder, folder / name, self.file(sid, c["file"]).read_bytes())
                names.append(name)
            cmd = join_command(names, "preview.mp4", int(clips[0]["width"]), int(clips[0]["height"]),
                               int(clips[0]["fps"]))
            data = await self._sandbox(folder, cmd, 600, "preview.mp4", "The preview failed")
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        async with self.edit(sid) as s:
            e = _by_id(s["episodes"], eid)
            if e is not None:
                e["preview"] = {"file": self._put(sid, f"{eid}/preview-{secrets.token_hex(3)}.mp4", data),
                                "stamp": stamp, "made": _now(),
                                "seconds": round(sum(float(c.get("seconds") or 0) for c in clips), 2)}

    # ------------------------------------------------------------ jobs
    def _progress(self, key: tuple[str, str]):
        async def cb(value: int, maximum: int, node=None):
            live = self.live.get(key)
            if live is not None:
                live.update(value=value, max=maximum)
        return cb

    def _start(self, sid: str, key: str, coro) -> None:
        if self.working(sid, key):
            coro.close()
            raise MovieError("It is already working on that.", 409)
        self.errors.pop((sid, key), None)
        self.tasks[(sid, key)] = asyncio.get_running_loop().create_task(self._run(sid, key, coro))

    def _enqueue(self, sid: str, key: str, shots: list[str], factory) -> None:
        """Shots for a keyframes or clips job: into its queue when it runs, else a new job."""
        queue = self.queues.setdefault((sid, key), [])
        for hid in shots:
            if hid not in queue:
                queue.append(hid)
        if not self.working(sid, key):
            self._start(sid, key, factory())

    async def _run(self, sid: str, key: str, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            self.errors[(sid, key)] = "Stopped."
        except MovieError as exc:
            self.errors[(sid, key)] = str(exc)
        except Exception as exc:  # noqa: BLE001 - the page says what stopped it
            log.exception("movie job %s %s failed", sid, key)
            self.errors[(sid, key)] = f"It stopped: {exc}"
        finally:
            self.live.pop((sid, key), None)
            left = self.queues.get((sid, key))
            if left:                    # asked for while it stopped: a new job for them, once this one has ended
                asyncio.get_running_loop().call_soon(self._again, sid, key)
            else:
                self.queues.pop((sid, key), None)

    def _again(self, sid: str, key: str) -> None:
        what, _, eid = key.partition(":")
        if self.queues.get((sid, key)) and not self.working(sid, key) and what in QUEUED_JOBS:
            factory = {"keyframes": self._keyframes, "clips": self._clips, "voices": self._voices,
                       "sounds": self._sounds}[what]
            self._start(sid, key, factory(sid, eid))

    async def cancel(self, sid: str, key: str) -> None:
        task = self.tasks.get((sid, key))
        if task is not None and not task.done():
            q = self.queues.get((sid, key))
            if q is not None:
                q.clear()               # in place: a shot asked for while it stops starts a new job
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), 10)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass


def join_command(names: list[str], out: str, width: int, height: int, fps: int) -> str:
    """ffmpeg's command for clips joined in order: each scaled into the size (letterboxed if its shape differs) at one
    frame rate, without sound; H.264 for any player."""
    inputs = " ".join(f"-i {shlex.quote(n)}" for n in names)
    chains = ";".join(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                      f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]" for i in range(len(names)))
    joined = "".join(f"[v{i}]" for i in range(len(names)))
    graph = f"{chains};{joined}concat=n={len(names)}:v=1:a=0[out]"
    return (f"ffmpeg -nostdin -v error -y {inputs} -filter_complex {shlex.quote(graph)} -map '[out]' -an "
            f"-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -movflags +faststart {shlex.quote(out)}")

