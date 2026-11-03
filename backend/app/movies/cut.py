"""Sound and the cut (0.73.0, Søren: "Continue with the rest" - 0.72.0 had left the sound and the final cut for later):
what is made from what, where it goes on the timeline, and ffmpeg's command. The jobs that make it are in sound.py.

Each line of a shot is spoken in its character's voice - one of the speech model's own voices, or a clip of a real
person (5-20 s) - by the speech service on the CPU. Each shot's sound text becomes an effect as long as the shot
(Stable Audio, on the card through the queue). The series has a theme (ACE-Step, through the queue), chosen among
versions like a sheet. The cut lays it all on a timeline: the clips one after the other, each line at its time in its
shot (by itself 0.3 s in, the next 0.25 s after the one before, or where the owner puts it), the effects at their
shots, the theme under everything - quieter while someone speaks - speech bubbles or captions on screen while a line
is heard, and an end card with the episode's title. ffmpeg renders it in the sandbox, after render_overlays.py has
drawn the bubbles and the end card there with the DejaVu fonts: the Hub's own image has no font with æ, ø and å."""
from __future__ import annotations

import shlex

from .common import _by_id, _stamp

DEFAULT_THEME = ("a playful theme tune for a cartoon family comedy, ukulele, glockenspiel, light percussion, "
                 "cheerful and bouncy, instrumental")
THEME_SECONDS = (10, 180)
THEME_DEFAULT_SECONDS = 40               # a 30 s episode and its end card; a longer one plays it again
CUT_DEFAULTS = {"music": True, "music_volume": 0.3, "effects_volume": 0.8, "speech_volume": 1.0,
                "text": "bubbles", "end_card": True}
TEXT_STYLES = ("bubbles", "captions", "none")
VOLUMES = ("music_volume", "effects_volume", "speech_volume")
LINE_START, LINE_GAP = 0.3, 0.25         # a shot's first line, and the pause between lines (seconds)
END_SECONDS = 2.5                        # the end card
FADE = 0.3                               # an effect's fade-out at its shot's end
LINGER = 0.4                             # a bubble stays a moment after its line - until the next one starts
EXPRESSIVENESS = (0.25, 2.0)
DEFAULT_EXPRESSIVENESS = 0.5


def lang_code(language: str) -> str:
    """The series' language ("Danish", or "da") as the speech models' code; "" when they do not know it."""
    from ..audio.pipeline import LANGUAGE_NAMES
    v = (language or "").strip().lower()
    if v in LANGUAGE_NAMES:
        return v
    return next((code for code, name in LANGUAGE_NAMES.items() if name.lower() == v), "")


def theme_of(series: dict) -> dict:
    """The series' theme: what it is made from, the versions made, the chosen one."""
    t = series.get("theme") or {}
    return {"prompt": t.get("prompt") or DEFAULT_THEME, "seconds": t.get("seconds") or THEME_DEFAULT_SECONDS,
            "file": t.get("file") or "", "file_stamp": t.get("file_stamp") or "",
            "variants": list(t.get("variants") or [])}


def cut_settings(episode: dict) -> dict:
    return {**CUT_DEFAULTS, **(episode.get("cut") or {})}


def voice_of(series: dict, who: str) -> tuple[str, str, float]:
    """A speaker's voice: ("clip", its file) for a character with a clip of a real voice, ("name", the speech model's
    voice) for one with a chosen voice - "" is the model's own - and the narrator's voice for anyone else; with how
    expressive they speak."""
    c = _by_id(series["cast"], who or "")
    if c is None:
        return "name", series["style"].get("narrator_voice") or "", DEFAULT_EXPRESSIVENESS
    ex = float(c.get("expressiveness") or DEFAULT_EXPRESSIVENESS)
    if c.get("voice") == "clip" and c.get("voice_clip"):
        return "clip", c["voice_clip"], ex
    return "name", c.get("voice") if c.get("voice") not in (None, "clip") else "", ex


def line_stamp(series: dict, line: dict, env: dict) -> str:
    return _stamp("line", line.get("text"), voice_of(series, line.get("who") or ""), env.get("speech"),
                  lang_code(series["style"].get("language") or ""))


def sfx_stamp(shot: dict, env: dict) -> str:
    return _stamp("sfx", (shot.get("sound") or "").strip(), shot.get("seconds"), env.get("sound"))


def theme_stamp(series: dict, env: dict) -> str:
    t = theme_of(series)
    return _stamp("theme", t["prompt"], t["seconds"], env.get("music"))


def line_state(series: dict, line: dict, env: dict) -> str:
    audio = line.get("audio")
    if not audio:
        return "none"
    return "fresh" if audio.get("stamp") == line_stamp(series, line, env) else "stale"


def sfx_state(shot: dict, env: dict) -> str:
    if not (shot.get("sound") or "").strip():
        return "unwanted"
    if not shot.get("sfx"):
        return "none"
    return "fresh" if shot["sfx"].get("stamp") == sfx_stamp(shot, env) else "stale"


def theme_state(series: dict, env: dict) -> str:
    t = theme_of(series)
    if not t["file"]:
        return "none"
    return "fresh" if t["file_stamp"] == theme_stamp(series, env) else "stale"


def shot_seconds(shot: dict) -> float:
    """A shot's length in the cut: its clip's, else what it is written to be."""
    return float((shot.get("clip") or {}).get("seconds") or shot.get("seconds") or 5.0)


def estimate(text: str) -> float:
    """How long a line takes to say before it is spoken: about two and a half words a second."""
    return round(max(0.8, len((text or "").split()) / 2.5), 2)


def _at(line: dict) -> float | None:
    v = line.get("at")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def cut_fps(episode: dict) -> int:
    """The cut's frame rate: its first clip's (the video models make 16 or 24 frames a second)."""
    clip = next((sh["clip"] for sh in episode["shots"] if sh.get("clip")), None)
    return max(1, int((clip or {}).get("fps") or 24))


def timeline(series: dict, episode: dict) -> dict:
    """Where everything goes: each shot (as long as its clip, in whole frames of the cut's rate - so a clip of
    another rate does not move the lines after it), each line (by itself after the shot's start or the line before
    it, or where the owner put it), each effect, the end card. A line not spoken yet takes as long as it would to
    say."""
    cut = cut_settings(episode)
    fps = cut_fps(episode)
    shots, lines, effects = [], [], []
    frame = 0
    for sh in episode["shots"]:
        n = max(1, round(shot_seconds(sh) * fps))
        t, dur = frame / fps, n / fps
        sound, sfx = (sh.get("sound") or "").strip(), (sh.get("sfx") or {}).get("file") or ""
        shots.append({"id": sh["id"], "start": round(t, 3), "seconds": round(dur, 4), "frames": n,
                      "clip": (sh.get("clip") or {}).get("file"), "sound": sound, "sfx": sfx})
        at = LINE_START
        for i, ln in enumerate(sh.get("lines") or []):
            audio = ln.get("audio") or {}
            fixed = _at(ln)
            start = fixed if fixed is not None else at
            length = float(audio.get("seconds") or 0) or estimate(ln.get("text") or "")
            c = _by_id(series["cast"], ln.get("who") or "")
            lines.append({"shot": sh["id"], "i": i, "id": ln.get("id") or "", "start": round(t + start, 3),
                          "at": round(start, 2), "fixed": fixed is not None, "seconds": round(length, 2),
                          "file": audio.get("file") or "", "estimated": not audio.get("seconds"),
                          "text": ln.get("text") or "", "name": c["name"] if c else (ln.get("who") or ""),
                          "narrator": c is None, "over": round(max(0.0, start + length - dur), 2)})
            at = start + length + LINE_GAP
        if sfx and sound:
            effects.append({"shot": sh["id"], "start": round(t, 3), "seconds": round(dur, 4), "file": sfx})
        frame += n
    end = None
    if cut["end_card"]:
        k = round(END_SECONDS * fps)
        end = {"start": round(frame / fps, 3), "seconds": round(k / fps, 4), "frames": k}
        frame += k
    return {"shots": shots, "lines": lines, "effects": effects, "end": end, "fps": fps,
            "total": round(frame / fps, 3), "cut": cut}


def spoken(tl: dict) -> list[dict]:
    """The lines that are heard, in the order the cut numbers them (l1.mp3, b1.png ...)."""
    return [ln for ln in tl["lines"] if ln["file"]]


def cut_stamp(series: dict, episode: dict, env: dict) -> str:
    """What a cut is made of - every shot with its sound text and every line, spoken or not, so a line or an effect
    added after the cut makes it out of date too."""
    tl = timeline(series, episode)
    theme = theme_of(series)["file"] if tl["cut"]["music"] else ""
    # a shot's effect in the cut (made, with its sound text), or the sound text of one not made yet
    return _stamp("cut", [(s["clip"], s["frames"], s["sfx"] if s["sound"] else "", "" if s["sfx"] else s["sound"])
                          for s in tl["shots"]],
                  [(ln["file"], ln["start"], ln["text"], ln["name"]) for ln in tl["lines"]], theme, tl["cut"],
                  tl["fps"], episode.get("title"), series.get("name"), series["style"].get("format"))


def missing_for_cut(episode: dict) -> list[str]:
    """What keeps an episode from its cut: shots without a clip, lines not spoken."""
    out = []
    for n, sh in enumerate(episode["shots"]):
        if not sh.get("clip"):
            out.append(f"shot {n + 1} has no clip")
        if any(not (ln.get("audio") or {}).get("file") for ln in sh.get("lines") or []):
            out.append(f"shot {n + 1} has lines not spoken")
    return out


def overlay_items(series: dict, episode: dict, tl: dict, width: int, height: int,
                  background: str | None) -> list[dict]:
    """What render_overlays.py draws: a bubble (a narration box for the narrator) or a caption for each line heard,
    on screen while it is heard and a moment after - until the next one starts - and the end card."""
    style = tl["cut"]["text"]
    items = []
    if style != "none":
        heard = spoken(tl)
        for n, ln in enumerate(heard):
            end = ln["start"] + ln["seconds"] + LINGER
            later = [x["start"] for x in heard if x["start"] > ln["start"]]
            if later:
                end = min(end, min(later))
            kind = "caption" if style == "captions" else "narration" if ln["narrator"] else "bubble"
            items.append({"kind": kind, "name": "" if ln["narrator"] else ln["name"], "text": ln["text"],
                          "out": f"b{n + 1}.png",
                          "start": ln["start"], "end": round(min(end, tl["total"]), 3)})
    if tl["end"]:
        items.append({"kind": "end", "title": episode.get("title") or "", "series": series.get("name") or "",
                      "background": background, "out": "end.png"})
    return [{**it, "width": width, "height": height} for it in items]


def cut_command(tl: dict, width: int, height: int, overlays: list[dict], music: bool) -> str:
    """ffmpeg's command for the cut, its inputs named as the job copies them: c<n>.mp4 for the clips, l<n>.mp3 for
    the lines heard, s<n>.mp3 for the effects, m.mp3 for the theme, b<n>.png and end.png from render_overlays.py.
    Each clip is held to its length (its last frame kept if it is short); the theme plays again if the
    episode is longer, fades in and out, and gives way while someone speaks (sidechain compression)."""
    cut = tl["cut"]
    total, fps = tl["total"], tl["fps"]
    inputs: list[str] = []
    graph: list[str] = []

    def add(arg: str) -> int:
        inputs.append(arg)
        return len(inputs) - 1

    shape = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
             f"setsar=1,fps={fps},format=yuv420p")
    video = []
    for n, s in enumerate(tl["shots"]):
        i = add(f"-i c{n + 1}.mp4")
        graph.append(f"[{i}:v]{shape},tpad=stop_mode=clone:stop_duration={s['seconds']:.3f},"
                     f"trim=end_frame={s['frames']},setpts=PTS-STARTPTS[v{n}]")
        video.append(f"[v{n}]")
    if tl["end"]:
        i = add(f"-loop 1 -framerate {fps} -t {tl['end']['seconds'] + 0.5:.3f} -i end.png")
        graph.append(f"[{i}:v]{shape},trim=end_frame={tl['end']['frames']},setpts=PTS-STARTPTS[vend]")
        video.append("[vend]")
    graph.append(f"{''.join(video)}concat=n={len(video)}:v=1:a=0[vc]")
    last = "vc"
    for n, ov in enumerate(o for o in overlays if o["kind"] != "end"):
        i = add(f"-i {shlex.quote(ov['out'])}")
        graph.append(f"[{last}][{i}:v]overlay=0:0:enable='between(t,{ov['start']:.3f},{ov['end']:.3f})'[vo{n + 1}]")
        last = f"vo{n + 1}"
    stereo = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    voices: list[str] = []
    for n, ln in enumerate(spoken(tl)):
        i = add(f"-i l{n + 1}.mp3")
        graph.append(f"[{i}:a]{stereo},volume={cut['speech_volume']:.2f},"
                     f"adelay=delays={round(ln['start'] * 1000)}:all=1[l{n + 1}]")
        voices.append(f"[l{n + 1}]")
    effects: list[str] = []
    for n, ef in enumerate(tl["effects"]):
        i = add(f"-i s{n + 1}.mp3")
        d = ef["seconds"]
        graph.append(f"[{i}:a]{stereo},atrim=duration={d:.3f},afade=t=out:st={max(0.0, d - FADE):.3f}:d={FADE},"
                     f"volume={cut['effects_volume']:.2f},adelay=delays={round(ef['start'] * 1000)}:all=1[s{n + 1}]")
        effects.append(f"[s{n + 1}]")
    silence = add(f"-f lavfi -t {total:.3f} -i anullsrc=r=48000:cl=stereo")
    mix = [f"[{silence}:a]"]             # first: the mix is as long as the film
    if voices:
        graph.append(f"{''.join(voices)}amix=inputs={len(voices)}:duration=longest:normalize=0[speech]"
                     if len(voices) > 1 else f"{voices[0]}anull[speech]")
    if music:
        i = add("-stream_loop -1 -i m.mp3")
        graph.append(f"[{i}:a]{stereo},atrim=duration={total:.3f},afade=t=in:d=0.5,"
                     f"afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5,volume={cut['music_volume']:.2f}[music]")
        if voices:                       # the speech, padded to the end, keys the theme down
            graph.append("[speech]asplit=2[speech1][speech2]")
            graph.append(f"[speech2]apad=whole_dur={total:.3f}[key]")
            graph.append("[music][key]sidechaincompress=threshold=0.05:ratio=4:attack=20:release=400[ducked]")
            mix += ["[speech1]", "[ducked]"]
        else:
            mix.append("[music]")
    elif voices:
        mix.append("[speech]")
    mix += effects
    graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95:level=0[aout]"
                 if len(mix) > 1 else f"{mix[0]}anull[aout]")
    return (f"ffmpeg -nostdin -v error -y {' '.join(inputs)} -filter_complex {shlex.quote(';'.join(graph))} "
            f"-map '[{last}]' -map '[aout]' -t {total:.3f} -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p "
            f"-r {fps} -c:a aac -b:a 192k -ar 48000 -movflags +faststart final.mp4")
