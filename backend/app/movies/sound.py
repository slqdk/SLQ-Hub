"""Sound and the cut's jobs (0.73.0) - the part of the Studio that takes a clip of a real voice, speaks the lines,
makes the effects and the theme, and cuts the episode. What is made from what, and where it goes: cut.py.

Speech runs on the CPU (the speech service), a line at a time; the effects and the theme on the card, through the
queue like the pictures (owner "movies", priority normal: chats go first). A voice clip's conversion and the cut run
in the sandbox, in a folder of their own under the workspace that is removed afterwards: the series' own folder is out
of the sandbox's reach, and a file from outside - a voice clip - is opened only there."""
from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
from pathlib import Path

from . import cut as C
from .common import PRIORITY, MovieError, _by_id, _given, _now

log = logging.getLogger(__name__)

SCRIPT = Path(__file__).with_name("render_overlays.py")
MAX_VOICE_UPLOAD = 50 * 1024 * 1024
SUFFIX_RE = re.compile(r"^\.[a-z0-9]{1,8}$")
NO_SOUND_MODELS = "(Settings -> Models -> Sound, music and speech)"


class SoundJobs:
    """Mixed into Studio (studio.py), whose storage, jobs and sandbox helpers it uses."""

    # ------------------------------------------------------------ what the series' sound means now
    def audio_env(self, series: dict) -> dict:
        """The speech model for the lines' language, the effect and music models: Settings' choices among those
        downloaded ("" when there is none) - part of what a line, an effect or the theme is stamped with."""
        from ..audio.pipeline import AudioError
        audio = self.hub.extras.get("audio")
        out = {"speech": "", "sound": "", "music": ""}
        if audio is None:
            return out
        code = C.lang_code(series["style"].get("language") or "")
        if code:
            try:
                out["speech"] = audio.speech_model(code)[0]["id"]
            except AudioError:
                pass
        for ability in ("sound", "music"):
            try:
                out[ability] = audio.chosen(ability)["id"]
            except AudioError:
                pass
        return out

    def audio_info(self, series: dict) -> dict:
        """What the page says about the sound: the speech model for the lines' language and its own voices (with a
        note when it speaks the language with an accent), the effect and music models."""
        from ..audio.pipeline import AudioError
        audio = self.hub.extras.get("audio")
        language = series["style"].get("language") or ""
        code = C.lang_code(language)
        info = {"language": code, "speech": "", "voices": [], "note": "", "sound": "", "music": "",
                "expressive": False}
        if audio is None:
            return info
        if not code:
            info["note"] = f"The speech models do not know {language or 'the language'} - choose one of theirs."
        else:
            try:
                entry, note = audio.speech_model(code)
                info.update(speech=entry["name"], note=note, expressive=bool(entry.get("expressive")),
                            voices=[{"id": k, "description": v} for k, v in audio.voices(entry).items()])
            except AudioError as exc:
                info["note"] = str(exc)
        for ability in ("sound", "music"):
            try:
                info[ability] = audio.chosen(ability)["name"]
            except AudioError:
                pass
        return info

    def _sound_ready(self, ability: str, what: str) -> None:
        audio = self.hub.extras.get("audio")
        if audio is None or not audio.available(ability):
            raise MovieError(f"No {what} model is downloaded {NO_SOUND_MODELS}.")

    @staticmethod
    def _shot_in(series: dict, eid: str, hid: str) -> dict | None:
        e = _by_id(series["episodes"], eid)
        return _by_id(e["shots"], hid) if e else None

    # ------------------------------------------------------------ a voice of one's own
    async def set_voice_clip(self, sid: str, cid: str, data: bytes, filename: str = "") -> dict:
        """A clip of a real voice for a character: its first 30 s as the speech models take it (24 kHz mono WAV),
        converted by ffmpeg in the sandbox - the place for files from outside - and kept with the series."""
        from ..projects import store as projects
        self._member(self.load(sid), "cast", cid)
        if not data:
            raise MovieError("The file is empty.")
        if len(data) > MAX_VOICE_UPLOAD:
            raise MovieError("The file is over 50 MB - 10-20 seconds of one person speaking is enough.", 413)
        suffix = Path(filename or "").suffix.lower()
        name = f"in{suffix if SUFFIX_RE.match(suffix) else '.bin'}"
        folder = self._tmp_folder()
        try:
            projects.safe_write(folder, folder / name, data)
            out = await self._sandbox(folder, C.voice_command(name, "voice.wav"), 120, "voice.wav",
                                      "The clip could not be read")
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        seconds = C.wav_seconds(out)
        if seconds < C.VOICE_MIN:
            raise MovieError(f"The clip has {seconds:.1f} s of sound - a voice needs 5-20 s of one person speaking.")
        async with self.edit(sid) as s:
            c = self._member(s, "cast", cid)
            c["voice_clip"] = self._put(sid, f"voices/{cid}-{secrets.token_hex(3)}.wav", out)
            c["voice_clip_seconds"] = round(seconds, 1)
            c["voice"] = "clip"
        return self.detail(sid)

    # ------------------------------------------------------------ the lines
    def make_voices(self, sid: str, eid: str, shots: list[str] | None = None, line: str | None = None) -> None:
        """The lines spoken in their characters' voices: each not spoken yet or spoken before a change - or every
        line of the given shots, or one line (its id) of one shot: another take. The job's queue holds
        "<shot>:<line>" by the lines' ids, so an edit that moves the lines meanwhile cannot give a take to the wrong
        one."""
        s = self.load(sid)
        e = self._episode(s, eid)
        self._not_writing(sid, eid)
        if not e.get("approved"):
            raise MovieError("Approve the storyboard first.")
        self._sound_ready("speech", "speech")
        language = s["style"].get("language") or ""
        if not C.lang_code(language):
            raise MovieError(f"The speech models do not know {language or 'the lines’ language'} - choose another "
                             "on the series' Look tab.")
        env = self.env(s)
        if line is not None:
            if not shots or len(shots) != 1:
                raise MovieError("Say which shot the line is in.")
            sh = self._shot(e, shots[0])
            if _by_id(sh.get("lines") or [], line) is None:
                raise MovieError("No such line.", 404)
            todo = [f"{sh['id']}:{line}"]
        elif shots:
            todo = [f"{sh['id']}:{ln['id']}" for sh in e["shots"] if sh["id"] in shots for ln in sh.get("lines") or []]
        else:
            todo = [f"{sh['id']}:{ln['id']}" for sh in e["shots"] for ln in sh.get("lines") or []
                    if C.line_state(s, ln, env) != "fresh"]
        if not todo:
            raise MovieError("No lines there." if shots else "Every line is spoken.")
        self._enqueue(sid, f"voices:{eid}", todo, lambda: self._voices(sid, eid))

    async def _voices(self, sid: str, eid: str) -> None:
        from ..audio.pipeline import AudioError
        key = (sid, f"voices:{eid}")
        queue = self.queues.setdefault(key, [])
        done = 0
        while queue:
            hid, _, lid = queue.pop(0).partition(":")
            s = self.load(sid)
            e = _by_id(s["episodes"], eid)
            sh = _by_id(e["shots"], hid) if e else None
            ln = _by_id((sh or {}).get("lines") or [], lid)
            if ln is None:
                continue                                  # its shot or the line itself is gone
            i = sh["lines"].index(ln)
            env = self.env(s)
            done += 1
            self.live[key] = {"what": "voices", "shot": hid, "number": e["shots"].index(sh) + 1, "line": i + 1,
                              "done": done - 1, "total": done + len(queue)}
            kind, voice, expressiveness = C.voice_of(s, ln.get("who") or "")
            stamp = C.line_stamp(s, ln, env)
            try:
                heard = self.file(sid, voice).read_bytes() if kind == "clip" else (voice or None)
                out = await self.hub.extras["audio"].speak(
                    text=ln["text"], language=C.lang_code(s["style"].get("language") or ""), voice=heard,
                    expressiveness=expressiveness, model=env["speech"] or None, fmt="mp3",
                    on_progress=self._progress(key))
            except (AudioError, MovieError, OSError) as exc:
                log.warning("line %s of %s/%s: %s", lid, sid, hid, exc)
                async with self.edit(sid) as s2:
                    ln2 = _by_id((self._shot_in(s2, eid, hid) or {}).get("lines") or [], lid)
                    if ln2 is not None:
                        ln2["error"] = f"It could not be spoken: {exc}"
                continue
            async with self.edit(sid) as s2:
                ln2 = _by_id((self._shot_in(s2, eid, hid) or {}).get("lines") or [], lid)
                if ln2 is None or (ln2.get("who"), ln2.get("text")) != (ln.get("who"), ln.get("text")):
                    continue                                  # changed meanwhile: this take is not its
                ln2["audio"] = {"file": self._put(sid, f"{eid}/{hid}-{lid}-{secrets.token_hex(3)}.mp3", out["data"]),
                                "seconds": round(float(out["seconds"]), 2), "stamp": stamp,
                                "voice": out.get("voice") or "", "seed": out.get("seed"), "made": _now()}
                ln2.pop("error", None)

    async def set_line_at(self, sid: str, eid: str, hid: str, line: str, at: float | None) -> dict:
        """Where a line starts in its shot (seconds), or None: by itself, after the one before it."""
        async with self.edit(sid) as s:
            sh = self._shot(self._episode(s, eid), hid)
            ln = _by_id(sh.get("lines") or [], line)
            if ln is None:
                raise MovieError("No such line.", 404)
            if at is None:
                ln.pop("at", None)
            else:
                ln["at"] = round(max(0.0, min(C.shot_seconds(sh), _given(at, "The time"))), 2)
        return self.detail(sid)

    async def keep_sound(self, sid: str, eid: str, what: str, shot: str | None = None,
                         line: str | None = None) -> dict:
        """Keep lines or effects made before a change as they are - one, or each in the episode: after a new speech
        or effect model is downloaded, say, the takes may be just as good."""
        if what not in ("lines", "effects"):
            raise MovieError("Keep lines or effects.")
        async with self.edit(sid) as s:
            e = self._episode(s, eid)
            env = self.env(s)
            shots = [self._shot(e, shot)] if shot else e["shots"]
            kept = 0
            for sh in shots:
                if what == "effects":
                    if C.sfx_state(sh, env) == "stale":
                        sh["sfx"]["stamp"] = C.sfx_stamp(sh, env)
                        kept += 1
                    continue
                for ln in sh.get("lines") or []:
                    if (line is None or ln.get("id") == line) and C.line_state(s, ln, env) == "stale":
                        ln["audio"]["stamp"] = C.line_stamp(s, ln, env)
                        kept += 1
            if not kept:
                raise MovieError("Nothing there is from before a change.")
        return self.detail(sid)

    # ------------------------------------------------------------ the effects
    def make_sounds(self, sid: str, eid: str, shots: list[str] | None = None) -> None:
        """Each shot's effect from its sound text, as long as the shot: each missing or made before a change, or
        the given shots' again."""
        s = self.load(sid)
        e = self._episode(s, eid)
        self._not_writing(sid, eid)
        if not e.get("approved"):
            raise MovieError("Approve the storyboard first.")
        self._sound_ready("sound", "sound-effect")
        env = self.env(s)
        if shots:
            todo = [sh["id"] for sh in e["shots"] if sh["id"] in shots and (sh.get("sound") or "").strip()]
            if not todo:
                raise MovieError("Write what is heard in the shot first (Sound).")
        else:
            todo = [sh["id"] for sh in e["shots"] if C.sfx_state(sh, env) in ("none", "stale")]
            if not todo:
                raise MovieError("Every effect is made." if any((sh.get("sound") or "").strip() for sh in e["shots"])
                                 else "No shot says what is heard (Sound).")
        self._enqueue(sid, f"sounds:{eid}", todo, lambda: self._sounds(sid, eid))

    async def _sounds(self, sid: str, eid: str) -> None:
        from ..audio.pipeline import AudioError
        key = (sid, f"sounds:{eid}")
        queue = self.queues.setdefault(key, [])
        done = 0
        while queue:
            hid = queue.pop(0)
            s = self.load(sid)
            e = _by_id(s["episodes"], eid)
            sh = _by_id(e["shots"], hid) if e else None
            text = ((sh or {}).get("sound") or "").strip()
            if not text:
                continue
            env = self.env(s)
            done += 1
            self.live[key] = {"what": "sounds", "shot": hid, "number": e["shots"].index(sh) + 1, "done": done - 1,
                              "total": done + len(queue)}
            stamp = C.sfx_stamp(sh, env)
            try:
                out = await self.hub.extras["audio"].sound(
                    prompt=text, kind="sfx", seconds=float(sh["seconds"]), model=env["sound"] or None, fmt="mp3",
                    owner="movies", priority=PRIORITY, on_progress=self._progress(key))
            except AudioError as exc:
                log.warning("effect for %s/%s: %s", sid, hid, exc)
                async with self.edit(sid) as s2:
                    sh2 = self._shot_in(s2, eid, hid)
                    if sh2 is not None:
                        sh2["sfx_error"] = f"The effect failed: {exc}"
                continue
            async with self.edit(sid) as s2:
                sh2 = self._shot_in(s2, eid, hid)
                if sh2 is None:
                    continue                                  # deleted meanwhile
                sh2["sfx"] = {"file": self._put(sid, f"{eid}/{hid}-fx-{secrets.token_hex(3)}.mp3", out["data"]),
                              "seconds": out["seconds"], "stamp": stamp, "seed": out.get("seed"), "made": _now()}
                sh2.pop("sfx_error", None)

    # ------------------------------------------------------------ the theme
    def make_theme(self, sid: str) -> None:
        self.load(sid)
        self._sound_ready("music", "music")
        self._start(sid, "theme", self._theme(sid))

    async def _theme(self, sid: str) -> None:
        """A version of the series' theme; the first one made is chosen until another is."""
        from ..audio.pipeline import AudioError
        key = (sid, "theme")
        s = self.load(sid)
        env = self.env(s)
        th = C.theme_of(s)
        stamp = C.theme_stamp(s, env)
        self.live[key] = {"what": "theme"}
        try:
            out = await self.hub.extras["audio"].music(
                prompt=th["prompt"], seconds=th["seconds"], model=env["music"] or None, fmt="mp3", owner="movies",
                priority=PRIORITY, on_progress=self._progress(key))
        except AudioError as exc:
            raise MovieError(f"The theme failed: {exc}") from exc
        async with self.edit(sid) as s:
            t = s.setdefault("theme", {})
            t.setdefault("prompt", th["prompt"])
            t.setdefault("seconds", th["seconds"])
            v = {"file": self._put(sid, f"music/theme-{secrets.token_hex(3)}.mp3", out["data"]), "stamp": stamp,
                 "seed": out.get("seed"), "seconds": out.get("seconds"), "bpm": out.get("bpm"), "key": out.get("key"),
                 "made": _now()}
            t["variants"] = self._trim([v, *(t.get("variants") or [])], t.get("file") or "")
            if not t.get("file"):
                t["file"], t["file_stamp"] = v["file"], v["stamp"]

    async def pick_theme(self, sid: str, file: str = "", keep: bool = False) -> dict:
        """Choose a version of the theme - or keep the chosen one as it is after a change."""
        async with self.edit(sid) as s:
            t = s.setdefault("theme", {})
            if keep:
                if not t.get("file"):
                    raise MovieError("No theme is chosen yet.")
                t["file_stamp"] = C.theme_stamp(s, self.env(s))
            else:
                v = next((v for v in t.get("variants") or [] if v["file"] == file), None)
                if v is None:
                    raise MovieError("No such version of the theme.", 404)
                t["file"], t["file_stamp"] = v["file"], v["stamp"]
        return self.detail(sid)

    # ------------------------------------------------------------ the cut
    def make_cut(self, sid: str, eid: str) -> None:
        s = self.load(sid)
        e = self._episode(s, eid)
        self._not_writing(sid, eid)
        if not e["shots"]:
            raise MovieError("There are no shots yet.")
        gaps = C.missing_for_cut(e)
        if gaps:
            raise MovieError(f"Not yet: {'; '.join(gaps)}.")
        self._start(sid, f"cut:{eid}", self._cut(sid, eid))

    async def _cut(self, sid: str, eid: str) -> None:
        """The episode as a film: the clips, the lines at their times with bubbles or captions, the effects, the
        theme under it and the end card - drawn and rendered in the sandbox, in the first clip's size and rate."""
        from ..projects import store as projects
        key = (sid, f"cut:{eid}")
        s = self.load(sid)
        e = self._episode(s, eid)
        gaps = C.missing_for_cut(e)
        if gaps or not e["shots"]:
            raise MovieError(f"Not yet: {'; '.join(gaps) or 'there are no shots'}.")
        env = self.env(s)
        tl = C.timeline(s, e)
        stamp = C.cut_stamp(s, e, env)
        first = e["shots"][0]["clip"]
        w, h = int(first["width"]), int(first["height"])
        theme = C.theme_of(s)["file"] if tl["cut"]["music"] else ""
        still = next((sh["keyframe"] for sh in reversed(e["shots"]) if sh.get("keyframe")), "") if tl["end"] else ""
        self.live[key] = {"what": "cut", "total": len(tl["shots"]), "seconds": tl["total"]}
        folder = self._tmp_folder()
        try:
            def put(name: str, rel: str) -> None:
                projects.safe_write(folder, folder / name, self.file(sid, rel).read_bytes())
            for n, shot in enumerate(tl["shots"]):
                put(f"c{n + 1}.mp4", shot["clip"])
            for n, ln in enumerate(C.spoken(tl)):
                put(f"l{n + 1}.mp3", ln["file"])
            for n, ef in enumerate(tl["effects"]):
                put(f"s{n + 1}.mp3", ef["file"])
            if theme:
                put("m.mp3", theme)
            if still:
                put("bg.png", still)
            overlays = C.overlay_items(s, e, tl, w, h, "bg.png" if still else None)
            cmd = C.cut_command(tl, w, h, overlays, bool(theme))
            if overlays:
                projects.safe_write(folder, folder / "overlays.json",
                                    json.dumps(overlays, ensure_ascii=False).encode("utf-8"))
                projects.safe_write(folder, folder / "render_overlays.py", SCRIPT.read_bytes())
                cmd = f"python3 render_overlays.py overlays.json && {cmd}"
            data = await self._sandbox(folder, cmd, 900, "final.mp4", "The cut failed")
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        async with self.edit(sid) as s2:
            e2 = _by_id(s2["episodes"], eid)
            if e2 is not None:
                e2["final"] = {"file": self._put(sid, f"{eid}/final-{secrets.token_hex(3)}.mp4", data),
                               "stamp": stamp, "seconds": tl["total"], "width": w, "height": h, "made": _now()}
