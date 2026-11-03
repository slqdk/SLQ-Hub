"""Movies' sound and cut (0.73.0, Søren: "Continue with the rest"): each character's voice - one of the speech
model's or a clip of a real one, converted in the sandbox - the lines spoken and kept through edits, an effect for
each shot and the series' theme through the queue, and the cut: the clips with the lines at their times, the theme
under them, speech bubbles and an end card, rendered by ffmpeg in the sandbox."""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys

import pytest

from app.movies import cut as C

from .mocks import _png, wav_tone
from .test_audio import install
from .test_loras import ORIGIN
from .test_movies import API, BLUE, RED, done, episode_with_storyboard, ok, series

SCRATCH = "/workspace/"


def strings(graph: dict) -> str:
    return " | ".join(str(v) for n in graph.values() for v in (n.get("inputs") or {}).values() if isinstance(v, str))


def answer(code: int = 0, stderr: str = "") -> dict:
    return {"exit_code": code, "stdout": "", "stderr": stderr, "timed_out": False, "duration_ms": 5, "truncated": False}


async def approved(client, app, fakes, x: dict) -> tuple[str, dict]:
    eid, _, _ = await episode_with_storyboard(client, app, fakes, x)
    s = await ok(await client.patch(f"{API}/{x['sid']}/episodes/{eid}", json={"approved": True}, headers=ORIGIN))
    return eid, s


async def test_each_line_spoken_in_its_characters_voice_and_kept_through_edits(signed_in, app, fakes):
    x = await series(signed_in, app)
    sid = x["sid"]
    eid, _, _ = await episode_with_storyboard(signed_in, app, fakes, x)
    base = f"{API}/{sid}/episodes/{eid}"
    r = await signed_in.post(f"{base}/voices", json={}, headers=ORIGIN)
    assert r.status_code == 400 and "Approve the storyboard first" in r.text      # the checkpoint
    await ok(await signed_in.patch(base, json={"approved": True}, headers=ORIGIN))
    r = await signed_in.post(f"{base}/voices", json={}, headers=ORIGIN)
    assert r.status_code == 400 and "No speech model is downloaded" in r.text
    install(app, "roest-v3")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert s["audio"]["language"] == "da" and s["audio"]["speech"] == "Røst v3"
    assert [v["id"] for v in s["audio"]["voices"]] == ["mic", "nic"]
    assert s["cast"][1]["voice"] == "" and s["style"]["narrator_voice"] == ""
    # a voice is one of the model's; a clip only when there is one
    r = await signed_in.patch(f"{API}/{sid}/cast/{x['mor']}", json={"voice": "bob"}, headers=ORIGIN)
    assert r.status_code == 400 and "its voices: mic, nic" in r.text
    r = await signed_in.patch(f"{API}/{sid}/cast/{x['mor']}", json={"voice": "clip"}, headers=ORIGIN)
    assert r.status_code == 400 and "Give a clip of the voice first" in r.text
    await ok(await signed_in.patch(f"{API}/{sid}/cast/{x['mor']}", json={"voice": "NIC", "expressiveness": 9},
                                   headers=ORIGIN))
    s = await ok(await signed_in.patch(f"{API}/{sid}", json={"style": {"narrator_voice": "mic"}}, headers=ORIGIN))
    assert (s["cast"][1]["voice"], s["cast"][1]["expressiveness"]) == ("nic", 2.0)
    # the lines: Mor's in shot 2 in her voice, the narrator's in shot 3 in his
    fakes.speech["requests"].clear()
    await ok(await signed_in.post(f"{base}/voices", json={}, headers=ORIGIN))
    await done(app, sid, f"voices:{eid}")
    reqs = fakes.speech["requests"]
    assert [(b["text"], b["language"], b["voice"]["path"].rsplit("/", 1)[-1], b["format"]) for b in reqs] == [
        ("Seriøst?", "da", "nic.wav", "mp3"), ("Og så var der ingen kage.", "da", "mic.wav", "mp3")]
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    e = s["episodes"][0]
    h1, h2, h3 = (sh["id"] for sh in e["shots"])
    mor, narrator = e["shots"][1]["lines"][0], e["shots"][2]["lines"][0]
    assert (mor["state"], mor["audio"]["voice"], mor["start"], mor["at"]) == ("fresh", "nic", 0.3, None)
    assert narrator["audio"]["seconds"] == 1.79 and (e["progress"]["spoken"], e["progress"]["lines"]) == (2, 2)
    r = await signed_in.get(f"{API}/{sid}/file", params={"path": mor["audio"]["file"]})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    r = await signed_in.post(f"{base}/voices", json={}, headers=ORIGIN)
    assert r.status_code == 400 and "Every line is spoken" in r.text
    # another voice makes Mor's line out of date - not the narrator's
    s = await ok(await signed_in.patch(f"{API}/{sid}/cast/{x['mor']}", json={"voice": "mic"}, headers=ORIGIN))
    e = s["episodes"][0]
    assert [sh["lines"][0]["state"] for sh in e["shots"][1:]] == ["stale", "fresh"]
    assert e["progress"]["spoken"] == 1
    # a line's time in its shot: kept within the shot, NaN refused, null is by itself again
    told = narrator["id"]
    s = await ok(await signed_in.post(f"{base}/shots/{h3}/line", json={"line": told, "at": 1.5}, headers=ORIGIN))
    assert s["episodes"][0]["shots"][2]["lines"][0]["start"] == 1.5
    # edited lines: one kept as it was keeps its id, take and time; a new one has none of them
    s = await ok(await signed_in.patch(f"{base}/shots/{h3}", json={"lines": [
        {"who": "Far", "text": "Hvem tog kagen?"}, {"who": "Narrator", "text": "Og så var der ingen kage."}]},
        headers=ORIGIN))
    lines = s["episodes"][0]["shots"][2]["lines"]
    assert [(ln["name"], ln["state"], ln["at"]) for ln in lines] == [("Far", "none", None), ("Narrator", "fresh", 1.5)]
    assert lines[1]["id"] == told and lines[0]["id"] not in ("", told)
    assert (lines[0]["start"], lines[0]["seconds"]) == (0.3, 1.2)     # not spoken: three words take 1.2 s
    far = lines[0]["id"]
    r = await signed_in.post(f"{base}/shots/{h3}/line", content=b'{"line": "' + told.encode() + b'", "at": NaN}',
                             headers={**ORIGIN, "Content-Type": "application/json"})
    assert r.status_code == 400 and "must be a number" in r.text
    assert (await signed_in.post(f"{base}/shots/{h3}/line", json={"line": "lnope", "at": 1},
                                 headers=ORIGIN)).status_code == 404
    s = await ok(await signed_in.post(f"{base}/shots/{h3}/line", json={"line": told, "at": 99}, headers=ORIGIN))
    assert s["episodes"][0]["shots"][2]["lines"][1]["at"] == 4.0                 # the shot's end
    s = await ok(await signed_in.post(f"{base}/shots/{h3}/line", json={"line": told, "at": None}, headers=ORIGIN))
    line = s["episodes"][0]["shots"][2]["lines"][1]
    assert (line["at"], line["start"]) == (None, 1.75)                            # after Far's 0.3 + 1.2 + 0.25
    # a typo fixed in its place keeps the line's id and time, not its take
    await ok(await signed_in.post(f"{base}/shots/{h3}/line", json={"line": told, "at": 2.5}, headers=ORIGIN))
    s = await ok(await signed_in.patch(f"{base}/shots/{h3}", json={"lines": [
        {"who": "Far", "text": "Hvem tog kagen?"}, {"who": "Narrator", "text": "Og så var der ingen kage!"}]},
        headers=ORIGIN))
    line = s["episodes"][0]["shots"][2]["lines"][1]
    assert (line["id"], line["at"], line["audio"], line["state"]) == (told, 2.5, None, "none")
    # one line again - Far has no voice of his own: the model's first
    fakes.speech["requests"].clear()
    await ok(await signed_in.post(f"{base}/voices", json={"shots": [h3], "line": far}, headers=ORIGIN))
    await done(app, sid, f"voices:{eid}")
    assert [(b["text"], b["voice"]["path"].rsplit("/", 1)[-1]) for b in fakes.speech["requests"]] == [
        ("Hvem tog kagen?", "mic.wav")]
    r = await signed_in.post(f"{base}/voices", json={"shots": [h1, h3], "line": far}, headers=ORIGIN)
    assert r.status_code == 400 and "which shot" in r.text
    # the lines moved while one waits for its take: the take goes to that line, wherever it is now
    fakes.speech["requests"].clear()
    fakes.speech["slow"] = 0.3
    await ok(await signed_in.post(f"{base}/voices", json={"shots": [h3], "line": far}, headers=ORIGIN))
    await ok(await signed_in.patch(f"{base}/shots/{h3}", json={"lines": [
        {"who": "Narrator", "text": "Og så var der ingen kage!"}, {"who": "Far", "text": "Hvem tog kagen?"}]},
        headers=ORIGIN))
    await done(app, sid, f"voices:{eid}")
    fakes.speech.pop("slow")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    lines = s["episodes"][0]["shots"][2]["lines"]
    assert [(ln["id"], ln["state"]) for ln in lines] == [(told, "none"), (far, "fresh")]
    # a failed line is said on the line itself, and the others go on
    fakes.speech["error"] = "the model ran out of memory"
    await ok(await signed_in.post(f"{base}/voices", json={"shots": [h2]}, headers=ORIGIN))
    await done(app, sid, f"voices:{eid}")
    fakes.speech.pop("error")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert s["episodes"][0]["shots"][1]["lines"][0]["error"] == "It could not be spoken: the model ran out of memory"
    # the series' folder keeps only what it points at
    folder = app.state.hub.env.data_dir / "movies" / sid / eid
    on_disk = sorted(p.name for p in folder.glob("*.mp3"))
    pointed = sorted(ln["audio"]["file"].split("/")[1] for sh in s["episodes"][0]["shots"] for ln in sh["lines"]
                     if ln["audio"])
    assert on_disk == pointed and len(pointed) == 2


async def test_a_clip_of_a_real_voice_converted_in_the_sandbox(signed_in, app, fakes):
    x = await series(signed_in, app)
    sid = x["sid"]
    install(app, "roest-v3")
    ws = app.state.hub.env.workspace_dir
    seen: dict = {}
    clip = wav_tone(8.0)

    def convert(body):
        seen.update(body)
        (ws / body["cwd"].removeprefix(SCRATCH) / "voice.wav").write_bytes(clip)
        return answer()
    fakes.sandbox["handler"] = convert
    s = await ok(await signed_in.post(f"{API}/{sid}/cast/{x['far']}/voice",
                                      files={"file": ("far's voice.M4A", b"a phone recording", "audio/mp4")},
                                      headers=ORIGIN))
    assert seen["offline"] and seen["cwd"].startswith("/workspace/movies-tmp/")
    assert seen["cmd"] == ("ffmpeg -nostdin -v error -protocol_whitelist file -y -i in.m4a -t 30 -vn -ac 1 -ar 24000 "
                           "-c:a pcm_s16le -f wav voice.wav")
    assert not list((ws / "movies-tmp").iterdir())
    far = s["cast"][0]
    assert far["voice"] == "clip" and far["voice_clip"].startswith(f"voices/{x['far']}-")
    assert far["voice_clip_seconds"] == 8.0
    assert (await signed_in.get(f"{API}/{sid}/file", params={"path": far["voice_clip"]})).content == clip
    # too short, not audio, too big
    def short(body):
        (ws / body["cwd"].removeprefix(SCRATCH) / "voice.wav").write_bytes(wav_tone(1.0))
        return answer()
    fakes.sandbox["handler"] = short
    r = await signed_in.post(f"{API}/{sid}/cast/{x['far']}/voice", files={"file": ("a.wav", b"x", "audio/wav")},
                             headers=ORIGIN)
    assert r.status_code == 400 and "a voice needs 5-20 s" in r.text
    fakes.sandbox["handler"] = lambda body: answer(1, "in.bin: Invalid data found when processing input")
    r = await signed_in.post(f"{API}/{sid}/cast/{x['far']}/voice", files={"file": ("notes", b"x", "text/plain")},
                             headers=ORIGIN)
    assert r.status_code == 400 and "The clip could not be read: in.bin: Invalid data found" in r.text
    assert not list((ws / "movies-tmp").iterdir())
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert s["cast"][0]["voice_clip"] == far["voice_clip"]                      # the good one stays
    # Far's line spoken with his clip: the speech service gets the clip itself
    eid, _ = await approved(signed_in, app, fakes, x)
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    h1 = s["episodes"][0]["shots"][0]["id"]
    await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}/shots/{h1}",
                                   json={"lines": [{"who": "Far", "text": "Kagen!"}]}, headers=ORIGIN))
    fakes.speech["requests"].clear()
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/voices", json={"shots": [h1]}, headers=ORIGIN))
    await done(app, sid, f"voices:{eid}")
    assert fakes.speech["requests"][0]["voice"] == {"audio": base64.b64encode(clip).decode()}
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert s["episodes"][0]["shots"][0]["lines"][0]["state"] == "fresh"
    # back to one of the model's voices: the line is out of date, the clip kept to choose again
    s = await ok(await signed_in.patch(f"{API}/{sid}/cast/{x['far']}", json={"voice": "mic"}, headers=ORIGIN))
    assert s["episodes"][0]["shots"][0]["lines"][0]["state"] == "stale" and s["cast"][0]["voice_clip"]
    s = await ok(await signed_in.patch(f"{API}/{sid}/cast/{x['far']}", json={"voice": "clip"}, headers=ORIGIN))
    assert s["episodes"][0]["shots"][0]["lines"][0]["state"] == "fresh"


async def test_effects_and_the_theme_through_the_queue(signed_in, app, fakes):
    x = await series(signed_in, app)
    sid = x["sid"]
    eid, _ = await approved(signed_in, app, fakes, x)
    base = f"{API}/{sid}/episodes/{eid}"
    r = await signed_in.post(f"{base}/sounds", json={}, headers=ORIGIN)
    assert r.status_code == 400 and "No sound-effect model is downloaded" in r.text
    r = await signed_in.post(f"{API}/{sid}/theme/make", headers=ORIGIN)
    assert r.status_code == 400 and "No music model is downloaded" in r.text
    install(app, "stable-audio-3-medium", "ace-step-1.5-turbo")
    fakes.comfy["prompts"].clear()
    await ok(await signed_in.post(f"{base}/sounds", json={}, headers=ORIGIN))
    await done(app, sid, f"sounds:{eid}")
    graphs = fakes.comfy["prompts"]
    assert len(graphs) == 2                                    # shot 3 has no sound written
    assert "creaking floor" in strings(graphs[0]) and "western whistle" in strings(graphs[1])
    assert [g["5"]["inputs"]["seconds"] for g in graphs] == [5.0, 10.0]          # as long as the shots
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    e = s["episodes"][0]
    assert [sh["sfx_state"] for sh in e["shots"]] == ["fresh", "fresh", "unwanted"]
    assert (e["progress"]["effects"], e["progress"]["effects_made"]) == (2, 2)
    h1 = e["shots"][0]["id"]
    assert (await signed_in.get(f"{API}/{sid}/file", params={"path": e["shots"][0]["sfx"]["file"]})).status_code == 200
    # a new sound text makes the effect out of date; one shot again
    s = await ok(await signed_in.patch(f"{base}/shots/{h1}", json={"sound": "creaking floor, a cat meows"},
                                       headers=ORIGIN))
    assert s["episodes"][0]["shots"][0]["sfx_state"] == "stale"
    await ok(await signed_in.post(f"{base}/sounds", json={"shots": [h1]}, headers=ORIGIN))
    await done(app, sid, f"sounds:{eid}")
    assert "a cat meows" in strings(fakes.comfy["prompts"][-1])
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert s["episodes"][0]["shots"][0]["sfx_state"] == "fresh"
    r = await signed_in.post(f"{base}/sounds", json={"shots": [s["episodes"][0]["shots"][2]["id"]]}, headers=ORIGIN)
    assert r.status_code == 400 and "Write what is heard" in r.text
    # the theme: its words and length, the first version chosen by itself, another one chosen by hand
    assert s["theme"] == {"prompt": C.DEFAULT_THEME, "seconds": 40, "file": "", "variants": [], "state": "none"}
    s = await ok(await signed_in.patch(f"{API}/{sid}", json={"theme": {"prompt": "a jolly ukulele theme",
                                                                       "seconds": 35}}, headers=ORIGIN))
    assert (s["theme"]["prompt"], s["theme"]["seconds"]) == ("a jolly ukulele theme", 35)
    r = await signed_in.patch(f"{API}/{sid}", content=b'{"theme": {"seconds": NaN}}',
                              headers={**ORIGIN, "Content-Type": "application/json"})
    assert r.status_code == 400
    for _ in range(2):
        await ok(await signed_in.post(f"{API}/{sid}/theme/make", headers=ORIGIN))
        await done(app, sid, "theme")
    g = fakes.comfy["prompts"][-1]
    assert "a jolly ukulele theme" in strings(g) and g["5"]["inputs"]["duration"] == 35
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    first, second = s["theme"]["variants"][1]["file"], s["theme"]["variants"][0]["file"]
    assert s["theme"]["file"] == first and s["theme"]["state"] == "fresh" and first.startswith("music/theme-")
    s = await ok(await signed_in.post(f"{API}/{sid}/theme/pick", json={"file": second}, headers=ORIGIN))
    assert s["theme"]["file"] == second
    s = await ok(await signed_in.patch(f"{API}/{sid}", json={"theme": {"prompt": "a sad tuba"}}, headers=ORIGIN))
    assert s["theme"]["state"] == "stale"
    s = await ok(await signed_in.post(f"{API}/{sid}/theme/pick", json={"keep": True}, headers=ORIGIN))
    assert s["theme"]["state"] == "fresh"
    assert (await signed_in.post(f"{API}/{sid}/theme/pick", json={"file": "music/x.mp3"},
                                 headers=ORIGIN)).status_code == 404


def _clip(path, seconds: float) -> bytes:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=180x320:r=24:d={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path.read_bytes()


async def test_the_cut_with_the_lines_at_their_times_the_theme_bubbles_and_an_end_card(signed_in, app, fakes, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed here")
    x = await series(signed_in, app)
    sid = x["sid"]
    install(app, "wan2.2-5b", "roest-v3", "stable-audio-3-medium", "ace-step-1.5-turbo")
    eid, s = await approved(signed_in, app, fakes, x)
    base = f"{API}/{sid}/episodes/{eid}"
    shots = s["episodes"][0]["shots"]
    fakes.comfy["pictures"] = [_png((90, 160), RED), _png((90, 160), BLUE)]
    await ok(await signed_in.post(f"{base}/keyframes", json={}, headers=ORIGIN))
    await done(app, sid, f"keyframes:{eid}")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    for sh in s["episodes"][0]["shots"]:
        await ok(await signed_in.post(f"{base}/shots/{sh['id']}/pick", json={"file": sh["variants"][0]["file"]},
                                      headers=ORIGIN))
    fakes.comfy["video_bytes"] = _clip(tmp_path / "clip.mp4", 3.0)     # shorter than the shots: its last frame held
    r = await signed_in.post(f"{base}/cut", headers=ORIGIN)
    assert r.status_code == 400 and "shot 1 has no clip" in r.text and "shot 2 has lines not spoken" in r.text
    await ok(await signed_in.post(f"{base}/clips", json={}, headers=ORIGIN))
    await done(app, sid, f"clips:{eid}")
    await ok(await signed_in.post(f"{base}/voices", json={}, headers=ORIGIN))
    await done(app, sid, f"voices:{eid}")
    await ok(await signed_in.post(f"{base}/sounds", json={}, headers=ORIGIN))
    await done(app, sid, f"sounds:{eid}")
    await ok(await signed_in.post(f"{API}/{sid}/theme/make", headers=ORIGIN))
    await done(app, sid, "theme")
    ws = app.state.hub.env.workspace_dir
    ran: list[dict] = []

    def sandbox(body):                                    # the sandbox's python3 and ffmpeg, for real
        ran.append(body)
        folder = ws / body["cwd"].removeprefix(SCRATCH)
        items = json.loads((folder / "overlays.json").read_text(encoding="utf-8"))
        ran[-1]["items"] = items
        cmd = body["cmd"].replace("python3 render_overlays.py", f"{sys.executable} render_overlays.py")
        run = subprocess.run(["sh", "-c", cmd], cwd=folder, capture_output=True, text=True, timeout=300)
        ran[-1]["files"] = sorted(p.name for p in folder.iterdir())
        return answer(run.returncode, run.stderr)
    fakes.sandbox["handler"] = sandbox
    s = await ok(await signed_in.post(f"{base}/cut", headers=ORIGIN))
    await done(app, sid, f"cut:{eid}")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert f"cut:{eid}" not in s["errors"], s["errors"]
    e = s["episodes"][0]
    body = ran[0]
    assert body["offline"] and body["cmd"].startswith("python3 render_overlays.py overlays.json && ffmpeg ")
    assert {"c1.mp4", "c2.mp4", "c3.mp4", "l1.mp3", "l2.mp3", "s1.mp3", "s2.mp3", "m.mp3", "bg.png", "b1.png",
            "b2.png", "end.png", "final.mp4"} <= set(body["files"])
    assert [(it["kind"], it.get("name"), it.get("text")) for it in body["items"]] == [
        ("bubble", "Mor", "Seriøst?"), ("narration", "", "Og så var der ingen kage."), ("end", None, None)]
    assert (body["items"][-1]["title"], body["items"][-1]["series"], body["items"][-1]["background"]) == \
        ("Det sidste stykke", "Hjemme hos os", "bg.png")
    total = e["progress"]["total"]
    assert total == 21.625                       # 121 + 241 + 97 frames of 24 a second, and the end card's 60
    starts = [round(121 / 24 + 0.3, 3), round(362 / 24 + 0.3, 3)]
    assert f"adelay=delays={round(starts[0] * 1000)}:all=1" in body["cmd"]
    assert f"between(t,{starts[1]:.3f}," in body["cmd"]
    assert "sidechaincompress" in body["cmd"] and "-stream_loop -1 -i m.mp3" in body["cmd"]
    assert not list((ws / "movies-tmp").iterdir())
    final = e["final"]
    assert e["progress"]["final"] == "fresh" and final["seconds"] == total and (final["width"], final["height"]) == \
        (shots and e["shots"][0]["clip"]["width"], e["shots"][0]["clip"]["height"])
    out = tmp_path / "final.mp4"
    out.write_bytes((await signed_in.get(f"{API}/{sid}/file", params={"path": final["file"]})).content)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                                       "-of", "json", str(out)], capture_output=True, text=True, check=True).stdout)
    assert sorted(st["codec_type"] for st in probe["streams"]) == ["audio", "video"]
    assert abs(float(probe["format"]["duration"]) - total) < 0.1
    # the cut's settings, a line spoken again, another theme: out of date
    s = await ok(await signed_in.patch(base, json={"cut": {"text": "captions", "music_volume": 0.5}}, headers=ORIGIN))
    e = s["episodes"][0]
    assert e["progress"]["final"] == "stale" and (e["cut"]["text"], e["cut"]["music_volume"]) == ("captions", 0.5)
    r = await signed_in.patch(base, json={"cut": {"text": "subtitles"}}, headers=ORIGIN)
    assert r.status_code == 400 and "bubbles, captions or none" in r.text
    await ok(await signed_in.post(f"{base}/cut", headers=ORIGIN))
    await done(app, sid, f"cut:{eid}")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert s["episodes"][0]["progress"]["final"] == "fresh"
    assert [it["kind"] for it in ran[-1]["items"]][:2] == ["caption", "caption"]
    # takes from before a change kept as they are: the cut is up to date again, and says what dated it
    s = await ok(await signed_in.patch(f"{API}/{sid}/cast/{x['mor']}", json={"voice": "nic"}, headers=ORIGIN))
    p = s["episodes"][0]["progress"]
    assert (p["final"], p["final_why"]) == ("stale", "a line in shot 2 was spoken before a change")
    s = await ok(await signed_in.post(f"{base}/keep", json={"what": "lines"}, headers=ORIGIN))
    assert s["episodes"][0]["progress"]["final"] == "fresh"
    h1 = s["episodes"][0]["shots"][0]["id"]
    s = await ok(await signed_in.patch(f"{base}/shots/{h1}", json={"sound": "creaking floor, an owl"}, headers=ORIGIN))
    assert s["episodes"][0]["progress"]["final_why"] == "shot 1's effect is from before a change"
    s = await ok(await signed_in.post(f"{base}/keep", json={"what": "effects", "shot": h1}, headers=ORIGIN))
    assert s["episodes"][0]["shots"][0]["sfx_state"] == "fresh" and s["episodes"][0]["progress"]["final"] == "fresh"
    r = await signed_in.post(f"{base}/keep", json={"what": "effects"}, headers=ORIGIN)
    assert r.status_code == 400 and "Nothing there is from before a change" in r.text
    await ok(await signed_in.post(f"{API}/{sid}/theme/make", headers=ORIGIN))
    await done(app, sid, "theme")
    s = await ok(await signed_in.post(f"{API}/{sid}/theme/pick", json={"file": (await ok(await signed_in.get(
        f"{API}/{sid}")))["theme"]["variants"][0]["file"]}, headers=ORIGIN))
    assert s["episodes"][0]["progress"]["final"] == "stale"
    # ffmpeg that fails leaves the cut as it was, and says why
    before = s["episodes"][0]["final"]["file"]
    fakes.sandbox["handler"] = lambda body: answer(1, "Error while filtering: Cannot allocate memory")
    await ok(await signed_in.post(f"{base}/cut", headers=ORIGIN))
    await done(app, sid, f"cut:{eid}")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert s["episodes"][0]["final"]["file"] == before
    assert "The cut failed: Error while filtering: Cannot allocate memory" in s["errors"][f"cut:{eid}"]
    assert not list((ws / "movies-tmp").iterdir())
    # a line added after the cut dates it, and the next cut waits for it to be spoken
    await ok(await signed_in.post(f"{API}/{sid}/theme/pick", json={"file": s["theme"]["variants"][1]["file"]},
                                  headers=ORIGIN))
    s = await ok(await signed_in.patch(f"{base}/shots/{h1}", json={"lines": [{"who": "Far", "text": "Kage!"}]},
                                       headers=ORIGIN))
    p = s["episodes"][0]["progress"]
    assert (p["final"], p["final_why"], p["cut_missing"]) == ("stale", "the episode has changed since",
                                                              ["shot 1 has lines not spoken"])


def test_the_timeline_the_overlays_and_the_command():
    series = {"name": "Hjemme hos os", "style": {"language": "Danish", "format": "portrait", "narrator_voice": "mic"},
              "cast": [{"id": "c1", "name": "Far", "voice": "nic"},
                       {"id": "c2", "name": "Mor", "voice": "clip", "voice_clip": "voices/c2-a.wav",
                        "expressiveness": 1.2}]}
    episode = {"title": "Kage", "shots": [
        {"id": "h1", "seconds": 5.0, "clip": {"file": "e1/h1.mp4", "seconds": 5.04}, "sound": "a door creaks",
         "sfx": {"file": "e1/h1-fx.mp3"}, "lines": [
            {"who": "c1", "text": "Hvor er kagen?", "audio": {"file": "e1/l1.mp3", "seconds": 1.5}},
            {"who": "c2", "text": "Væk!", "audio": {"file": "e1/l2.mp3", "seconds": 0.8}},
            {"who": "c1", "text": "Nej!", "at": 4.8, "audio": {"file": "e1/l3.mp3", "seconds": 1.0}}]},
        {"id": "h2", "seconds": 4.0, "sound": "", "sfx": {"file": "e1/old.mp3"}, "lines": [
            {"who": "Narrator", "text": "Og så var der ingen kage tilbage i hele huset."}]}]}
    tl = C.timeline(series, episode)
    assert [(ln["start"], ln["seconds"], ln["over"]) for ln in tl["lines"]] == [
        (0.3, 1.5, 0.0), (2.05, 0.8, 0.0), (4.8, 1.0, 0.76), (5.342, 4.0, 0.3)]     # ten words: 4 s
    assert tl["lines"][3]["estimated"] and not tl["lines"][3]["file"] and tl["lines"][3]["narrator"]
    assert [ef["shot"] for ef in tl["effects"]] == ["h1"]                    # an effect without a sound text: none
    assert [(sh["frames"], sh["seconds"]) for sh in tl["shots"]] == [(121, 5.0417), (96, 4.0)]   # 24 a second
    assert (tl["end"], tl["total"]) == ({"start": 9.042, "seconds": 2.5, "frames": 60}, 11.542)
    assert C.missing_for_cut(episode) == ["shot 2 has no clip", "shot 2 has lines not spoken"]
    items = C.overlay_items(series, episode, tl, 720, 1280, None)
    assert [(it["kind"], it.get("name"), it.get("start"), it.get("end")) for it in items] == [
        ("bubble", "Far", 0.3, 2.05), ("bubble", "Mor", 2.05, 3.25), ("bubble", "Far", 4.8, 6.2),
        ("end", None, None, None)]                       # Far's first bubble gives way to Mor's; the last lingers
    assert all((it["width"], it["height"]) == (720, 1280) for it in items)
    cmd = C.cut_command(tl, 720, 1280, items, music=True)
    assert cmd.startswith("ffmpeg -nostdin -v error -y -i c1.mp4 -i c2.mp4 -loop 1 -framerate 24 -t 3.000 -i end.png "
                          "-i b1.png -i b2.png -i b3.png -i l1.mp3 -i l2.mp3 -i l3.mp3 -i s1.mp3 -f lavfi -t 11.542 "
                          "-i anullsrc=r=48000:cl=stereo -stream_loop -1 -i m.mp3 -filter_complex ")
    assert "trim=end_frame=121," in cmd and "trim=end_frame=96," in cmd and "trim=end_frame=60," in cmd
    assert "adelay=delays=300:all=1" in cmd and "adelay=delays=2050:all=1" in cmd and "adelay=delays=4800:all=1" in cmd
    assert "enable='\"'\"'between(t,4.800,6.200)'\"'\"'" in cmd
    assert "[music][key]sidechaincompress" in cmd and "apad=whole_dur=11.542" in cmd
    assert "-map '[vo3]' -map '[aout]' -t 11.542" in cmd and cmd.endswith("final.mp4")
    quiet = C.cut_command({**tl, "lines": [], "effects": []}, 720, 1280, [], music=False)
    assert "amix" not in quiet and "[0:a]anull[aout]" not in quiet and "anull[aout]" in quiet
    assert "-map '[vc]'" in quiet
    captions = C.overlay_items(series, {**episode, "cut": {"text": "captions", "end_card": False}},
                               C.timeline(series, {**episode, "cut": {"text": "captions", "end_card": False}}), 720,
                               1280, None)
    assert [it["kind"] for it in captions] == ["caption", "caption", "caption"]
    # what a line is spoken with: the character's voice and expression, the narrator's, a clip
    assert C.voice_of(series, "c1") == ("name", "nic", 0.5)
    assert C.voice_of(series, "c2") == ("clip", "voices/c2-a.wav", 1.2)
    assert C.voice_of(series, "Narrator") == ("name", "mic", 0.5)
    env = {"speech": "roest-v3"}
    line = episode["shots"][0]["lines"][0]
    stamp = C.line_stamp(series, line, env)
    assert stamp == C.line_stamp(series, dict(line), env)
    assert stamp != C.line_stamp(series, line, {"speech": "chatterbox-multilingual-v3"})
    assert stamp != C.line_stamp({**series, "style": {**series["style"], "language": "English"}}, line, env)
    assert stamp != C.line_stamp(series, {**line, "text": "Hvor er kagen nu?"}, env)
    assert (C.lang_code("Danish"), C.lang_code("da"), C.lang_code("DANISH"), C.lang_code("Klingon")) == \
        ("da", "da", "da", "")
    assert C.wav_seconds(wav_tone(2.0)) == 2.0 and C.wav_seconds(b"not a wav") == 0.0


def test_the_overlays_are_drawn_with_danish_letters(tmp_path):
    from PIL import Image
    script = tmp_path / "render_overlays.py"
    shutil.copy(C.__file__.replace("cut.py", "render_overlays.py"), script)
    (tmp_path / "bg.png").write_bytes(_png((90, 160), RED))
    items = [{"kind": "bubble", "name": "Mor", "text": "Æblekagen? Den spiste jeg i går, skat. Øv.", "out": "b1.png",
              "width": 360, "height": 640},
             {"kind": "narration", "name": "", "text": "Og så blev der stille i køkkenet.", "out": "b2.png",
              "width": 360, "height": 640},
             {"kind": "caption", "name": "Far", "text": "Hvor er kagen?", "out": "b3.png", "width": 360,
              "height": 640},
             {"kind": "end", "title": "Det sidste stykke kage", "series": "Hjemme hos os", "background": "bg.png",
              "out": "end.png", "width": 360, "height": 640},
             {"kind": "end", "title": "Uden baggrund", "series": "", "background": None, "out": "plain.png",
              "width": 360, "height": 640}]
    (tmp_path / "items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    subprocess.run([sys.executable, str(script), "items.json"], cwd=tmp_path, check=True)
    for name, mode in (("b1.png", "RGBA"), ("b2.png", "RGBA"), ("b3.png", "RGBA"), ("end.png", "RGB"),
                       ("plain.png", "RGB")):
        with Image.open(tmp_path / name) as img:
            assert (img.size, img.mode) == ((360, 640), mode)
            if mode == "RGBA":
                alpha = img.getchannel("A")
                top = alpha.crop((0, 0, 360, 320)).getbbox()
                bottom = alpha.crop((0, 320, 360, 640)).getbbox()
                assert (top is not None) == (name != "b3.png") and (bottom is not None) == (name == "b3.png")
    with Image.open(tmp_path / "end.png") as img:
        assert img.getpixel((180, 600))[0] < 200                       # the keyframe, darkened
