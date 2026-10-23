"""Sound: speech through the speech service (CPU), music and sound effects through ComfyUI (the card) - the
catalog, the tools, the graphs, what is sent, what is saved, people's turns - against the fake services."""
from __future__ import annotations

import base64
import importlib.util
import json
import shlex
from pathlib import Path

from app.agent.tools.audio import slug
from app.audio import workflows as W
from app.audio.speech import describe_voice, median_pitch

from .conftest import _install_fake_models
from .mocks import float_wav, wav_tone
from .test_agent import finish, new_chat, say
from .test_people import add_person, client_from, sign_in, turn, user_msg

ROOT = Path(__file__).resolve().parents[2]


def install(app, *ids: str) -> None:
    """Fake model files on disk; the voices are real WAVs, a low one (mic) and a high one (nic), stored as 32-bit
    float like the Røst samples on Hugging Face."""
    hub = app.state.hub
    _install_fake_models(hub.env.models_dir, set(ids))
    for e in hub.catalog.entries("audio"):
        if e["id"] in ids:
            for f in e["files"]:
                if f.get("role") == "voice":
                    hz = 118 if f["voice"] == "mic" else 205
                    (hub.env.models_dir / f["dest"]).write_bytes(_voiced(hz, encoding="float"))


def _voiced(hz: float, seconds: float = 6.0, rate: int = 24000, encoding: str = "pcm16") -> bytes:
    """A voice-like tone (harmonics, with pauses) as a WAV: 16-bit PCM, or 32-bit float."""
    import io
    import wave

    import numpy as np
    t = np.arange(int(seconds * rate)) / rate
    x = sum(np.sin(2 * np.pi * k * hz * t) / k for k in range(1, 9)) * 0.12 * (np.sin(2 * np.pi * 3 * t) > -0.4)
    if encoding == "float":
        return float_wav(x, rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def tools_offered(fakes, i: int = -1) -> dict:
    return {t["function"]["name"]: t["function"] for t in fakes.llama["requests"][i].get("tools") or []}


def system(fakes, i: int = -1) -> str:
    return fakes.llama["requests"][i]["messages"][0]["content"]


def tool_result(run) -> dict:
    return next(e["data"] for e in run.events if e["type"] == "tool_result")


def chat_dir(app, chat_id: int) -> Path:
    return app.state.hub.env.workspace_dir / "chats" / str(chat_id)


# ---------------------------------------------------------------- catalog

async def test_the_sound_models_are_in_the_catalog_and_download(signed_in, app):
    r = await signed_in.get("/hub/api/models")
    audio = {m["id"]: m for m in r.json()["audio"]}
    assert list(audio) == ["roest-v3", "roest-v3-turbo", "chatterbox-multilingual-v3", "ace-step-1.5-xl-turbo",
                           "ace-step-1.5-turbo", "stable-audio-3-medium"]
    assert not any(m["installed"] for m in audio.values())
    assert {m["engine"] for m in audio.values()} == {"speech", "comfy"}
    r = await signed_in.post("/hub/api/models/roest-v3/download")
    assert r.status_code == 200
    assert set(r.json()["queued"]) == {"speech/roest-v3/t3_mtl23ls_v2.safetensors", "speech/chatterbox/s3gen.pt",
                                       "speech/chatterbox/ve.pt",
                                       "speech/roest-v3/grapheme_mtl_merged_expanded_v1.json",
                                       "speech/roest-v3/voices/mic.wav", "speech/roest-v3/voices/nic.wav"}
    app.state.hub.extras["downloads"].cancel("roest-v3")
    # every file of a speech model sits under speech/, every ComfyUI file in a ComfyUI models folder
    for m in audio.values():
        for f in m["files"]:
            assert f["dest"].startswith("speech/" if m["engine"] == "speech" else "comfy/"), f
            assert f["size"] > 0 and f["repo"] and f["path"] and f["role"]


async def test_no_sound_tools_until_a_model_is_downloaded(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "Hi."}, {"content": "Hi"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hello"))
    offered = tools_offered(fakes, 0)
    assert not {"speech_generate", "music_generate", "sound_generate"} & set(offered)
    assert "# Sound" not in system(fakes, 0)
    install(app, "roest-v3", "stable-audio-3-medium")
    fakes.llama["requests"].clear()
    await finish(app, await say(signed_in, c["id"], "Hello again"))
    offered = tools_offered(fakes, 0)
    assert {"speech_generate", "sound_generate"} <= set(offered) and "music_generate" not in offered
    speech = offered["speech_generate"]
    # the voices with what they sound like, read from the clips themselves
    assert "Voices: mic (male voice), nic (female voice)" in speech["description"]
    assert speech["parameters"]["properties"]["model"]["enum"] == ["roest-v3"]
    assert "en" in speech["parameters"]["properties"]["language"]["enum"]          # Røst reads English too
    assert "speech_generate and sound_generate make audio files" in system(fakes, 0)


# ---------------------------------------------------------------- speech

async def test_a_danish_sentence_is_spoken_and_saved(signed_in, app, fakes):
    install(app, "roest-v3")
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "speech_generate", "arguments": {"text": "Tredive sekunder tilbage!",
                                                                  "language": "da", "voice": "nic", "seed": 7}}]},
        {"content": "Her er den: **tredive-sekunder-tilbage.mp3**."}, {"content": "Nedtælling"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Lav en speaker der siger tredive sekunder tilbage"))
    assert run.status == "done", run.events[-1]
    body = fakes.speech["requests"][0]
    assert body["model"] == {"key": "roest-v3", "family": "chatterbox-mtl", "int8": False, "files": {
        "t3": "/models/speech/roest-v3/t3_mtl23ls_v2.safetensors", "s3gen": "/models/speech/chatterbox/s3gen.pt",
        "ve": "/models/speech/chatterbox/ve.pt",
        "tokenizer": "/models/speech/roest-v3/grapheme_mtl_merged_expanded_v1.json"}}
    assert body["voice"] == {"path": "/models/speech/roest-v3/voices/nic.wav"}
    assert (body["text"], body["language"], body["seed"], body["format"]) == \
        ("Tredive sekunder tilbage!", "da", 7, "mp3")
    assert body["temperature"] == 0.8 and body["cfg_weight"] == 0.5 and "exaggeration" not in body
    res = tool_result(run)
    assert res["ok"], res
    assert "Saved tredive-sekunder-tilbage.mp3: 1.8 s of Danish speech, Røst v3, voice nic, seed 7" in res["text"]
    assert res["display"] == {"audio": [{"path": "tredive-sekunder-tilbage.mp3", "seconds": 1.79, "model": "Røst v3"}]}
    saved = chat_dir(app, c["id"]) / "tredive-sekunder-tilbage.mp3"
    assert saved.read_bytes()[:2] == b"\xff\xfb"
    data = (await signed_in.get(f"/hub/api/chats/{c['id']}")).json()                # the card under the answer
    card = next(s for s in data["runs"][-1]["steps"] if s["type"] == "files")["files"]
    assert [(f["path"], f["mime"], f["new"]) for f in card] == [("tredive-sekunder-tilbage.mp3", "audio/mpeg", True)]
    # the same text again keeps the first file and takes the next free name
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [fakes.llama["replies"][0], {"content": "Igen."}]
    run = await finish(app, await say(signed_in, c["id"], "En gang til"))
    assert "Saved tredive-sekunder-tilbage-2.mp3" in tool_result(run)["text"]


async def test_english_goes_to_chatterbox_with_expression_and_a_cloned_voice(signed_in, app, fakes):
    install(app, "roest-v3", "chatterbox-multilingual-v3")
    c = await new_chat(signed_in)
    folder = chat_dir(app, c["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "me.m4a").write_bytes(b"\x00\x00\x00\x20ftypM4A " + bytes(64))       # an iPhone voice memo
    clip = wav_tone(8, hz=130)
    seen: list[dict] = []

    def ffmpeg(body):                                   # the sandbox converts the clip to a 24 kHz mono WAV
        seen.append(body)
        dst = shlex.split(body["cmd"])[-1]
        out = app.state.hub.env.workspace_dir / dst.removeprefix("/workspace/")
        out.write_bytes(clip)
        return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "duration_ms": 5, "truncated": False}

    fakes.sandbox["handler"] = ffmpeg
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "speech_generate", "arguments": {
            "text": "Thirty seconds!", "language": "en", "expressiveness": 0.9, "voice": "me.m4a",
            "save_as": "announcer.wav"}}]},
        {"content": "Done."}, {"content": "Announcer"}]
    run = await finish(app, await say(signed_in, c["id"], "A stadium announcer saying thirty seconds, in my voice"))
    body = fakes.speech["requests"][0]
    assert body["model"]["key"] == "chatterbox-multilingual-v3" and body["format"] == "wav"
    assert body["model"]["files"]["conds"] == "/models/speech/chatterbox/conds.pt"
    assert body["model"]["files"]["t3"] == "/models/speech/chatterbox/t3_mtl23ls_v3.safetensors"
    assert body["exaggeration"] == 0.9 and body["cfg_weight"] == 0.3                 # lively: slower guidance
    assert base64.b64decode(body["voice"]["audio"]) == clip and body["cache_voice"] is True   # the owner's: kept
    cmd = shlex.split(seen[0]["cmd"])
    assert cmd[:2] == ["ffmpeg", "-nostdin"] and cmd[cmd.index("-i") + 1] == f"/workspace/chats/{c['id']}/me.m4a"
    assert cmd[cmd.index("-t") + 1] == "30" and cmd[cmd.index("-ar") + 1] == "24000" and "confine" not in seen[0]
    assert not list((folder / ".hub").glob("voice-*.wav"))                         # the converted copy is gone
    res = tool_result(run)
    assert res["ok"] and "of English speech, Chatterbox Multilingual V3, voice the given clip" in res["text"], res
    assert (folder / "announcer.wav").read_bytes()[:4] == b"RIFF"
    # Danish still goes to Røst, and no voice means the model's own default
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": "speech_generate", "arguments": {"text": "Hej.",
                                                                                        "language": "da"}}]},
                              {"content": "Hej."}]
    await finish(app, await say(signed_in, c["id"], "Sig hej"))
    body = fakes.speech["requests"][1]
    assert body["model"]["key"] == "roest-v3" and body["voice"] == {"path": "/models/speech/roest-v3/voices/mic.wav"}


async def test_rost_turbo_reads_english_with_a_note(signed_in, app, fakes):
    install(app, "roest-v3-turbo")
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "speech_generate", "arguments": {"text": "Time out!", "language": "en"}}]},
        {"content": "Done."}, {"content": "Time out"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Say time out"))
    body = fakes.speech["requests"][0]
    assert body["model"]["family"] == "chatterbox-turbo"
    assert body["model"]["files"]["tokenizer"] == "/models/speech/roest-v3-turbo/tokenizer"      # a folder
    assert body["voice"] == {"path": "/models/speech/roest-v3-turbo/voices/mic.wav"}
    assert (body["top_k"], body["repetition_penalty"]) == (800, 1.2)
    res = tool_result(run)
    assert res["ok"] and "Røst v3 Turbo speaks English with a heavy Danish accent" in res["text"], res


async def test_the_speech_speed_setting(signed_in, app, fakes):
    """Faster: 8-bit T3, every model; fastest: also Turbo's two-step decoder (from Røst v3 Turbo) for the 500M models.
    Without Røst v3 Turbo the result says why the decoder stayed; for a Turbo model fastest is faster."""
    install(app, "roest-v3")
    c = await new_chat(signed_in)

    async def speak(text: str, speed: str, language: str = "da") -> tuple[dict, dict]:
        r = await signed_in.put("/hub/api/settings", json={"section": "sound", "values": {"speech_speed": speed}})
        assert r.status_code == 200, r.text
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = [{"tool_calls": [{"name": "speech_generate", "arguments": {
            "text": text, "language": language}}]}, {"content": "Ok."}]
        res = tool_result(await finish(app, await say(signed_in, c["id"], "Speak")))
        return fakes.speech["requests"][-1]["model"], res

    model, res = await speak("Hej.", "best")
    assert model["int8"] is False and "decoder" not in model["files"] and "Note" not in res["text"]
    model, res = await speak("Hej.", "faster")                         # 8-bit, its own ten-step decoder
    assert model["int8"] is True and "decoder" not in model["files"] and "Note" not in res["text"]
    model, res = await speak("Hej.", "fastest")                        # no Røst v3 Turbo yet: the ten-step decoder
    assert model["int8"] is True and "decoder" not in model["files"]
    assert "The fastest speech setting needs Røst v3 Turbo downloaded" in res["text"]
    install(app, "roest-v3", "roest-v3-turbo")
    model, res = await speak("Hej.", "fastest")
    assert model["files"]["decoder"] == "/models/speech/roest-v3-turbo/s3gen_meanflow.safetensors"
    assert model["files"]["s3gen"] == "/models/speech/chatterbox/s3gen.pt" and model["int8"] is True
    assert "Note" not in res["text"]
    model, res = await speak("Hej.", "faster")
    assert model["int8"] is True and "decoder" not in model["files"]
    r = await signed_in.put("/hub/api/settings", json={"section": "sound", "values": {"speech_model_da":
                                                                                      "roest-v3-turbo"}})
    for speed in ("faster", "fastest"):                                # Turbo has that decoder: 8-bit either way
        model, res = await speak("Hej.", speed)
        assert model["key"] == "roest-v3-turbo" and model["int8"] is True and "decoder" not in model["files"]
        assert "Note" not in res["text"]
    # a piece that ran to the token ceiling is said so
    fakes.speech["capped"] = 1
    model, res = await speak("Hej. Farvel.", "best")
    assert "did not stop by itself on 1 of 2 piece(s)" in res["text"] and "another seed" in res["text"]


async def test_the_self_tests_speech_samples_play_in_settings(signed_in, app):
    """selftest --speech leaves data/selftest/speech-*.mp3 and speech.json; the Sound settings list and play them -
    for the owner only."""
    from .mocks import silent_mp3
    folder = app.state.hub.env.data_dir / "selftest"
    folder.mkdir(parents=True, exist_ok=True)
    runs = [("roest-v3", "Røst v3", "best", 5.75), ("roest-v3", "Røst v3", "faster", 3.1),
            ("roest-v3", "Røst v3", "fastest", 2.28), ("roest-v3-turbo", "Røst v3 Turbo", "best", 3.75)]
    for model, _, setting, _ in runs:
        (folder / f"speech-{model}-{setting}.mp3").write_bytes(silent_mp3(1.0))
    (folder / "notes.txt").write_text("not a sample")
    assert (await signed_in.get("/hub/api/sound/samples")).json()["samples"] == []    # no list (0.13.1): none
    (folder / "speech.json").write_text(json.dumps({"at": "2026-10-23 21:30", "cpu": "AMD Ryzen 9 5950X (avx2)",
                                                    "threads": 10, "samples": [
        {"file": f"speech-{m}-{st}.mp3", "model": m, "name": n, "setting": st, "text": "Og her kommer en sætning mere.",
         "seconds": 3.4, "compute": round(3.4 * f, 1), "factor": f, "capped": st == "fastest"}
        for m, n, st, f in runs]}))
    r = await signed_in.get("/hub/api/sound/samples")
    assert r.status_code == 200, r.text
    data = r.json()
    assert (data["at"], data["threads"]) == ("2026-10-23 21:30", 10)
    assert [(s["model"], s["setting"]) for s in data["samples"]] == [(m, st) for m, _, st, _ in runs]
    assert data["samples"][2]["capped"] is True and data["samples"][0]["factor"] == 5.75
    r = await signed_in.get("/hub/api/sound/samples/speech-roest-v3-faster.mp3")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content[:2] == b"\xff\xfb"
    for bad in ("notes.txt", "..%2Fhub.db", "speech-x.mp3", "speech-..%2F..%2Fhub.db.mp3"):
        assert (await signed_in.get(f"/hub/api/sound/samples/{bad}")).status_code == 404, bad
    await add_person(signed_in)
    async with client_from(app) as phone:
        await sign_in(phone)
        assert (await phone.get("/hub/api/sound/samples")).status_code == 403


async def test_speech_errors_are_reported(signed_in, app, fakes):
    install(app, "roest-v3")
    c = await new_chat(signed_in)

    async def call(args: dict) -> dict:
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = [{"tool_calls": [{"name": "speech_generate", "arguments": args}]}, {"content": "?"}]
        return tool_result(await finish(app, await say(signed_in, c["id"], "Speak")))

    res = await call({"text": "Hej", "language": "da", "voice": "bob"})
    assert not res["ok"] and "no voice called 'bob' (its voices: mic, nic)" in res["text"]
    res = await call({"text": "Hej", "language": "da", "voice": "clips/missing.wav"})
    assert not res["ok"] and "No such voice clip" in res["text"]
    folder = chat_dir(app, c["id"])
    (folder / "notes.mp3").write_bytes(b"not audio")
    fakes.sandbox["handler"] = lambda body: {"exit_code": 1, "stdout": "", "timed_out": False, "duration_ms": 5,
                                             "stderr": "notes.mp3: Invalid data found when processing input\n"}
    res = await call({"text": "Hej", "language": "da", "voice": "notes.mp3"})
    assert not res["ok"] and "could not be read (notes.mp3: Invalid data found when processing input)" in res["text"]
    fakes.sandbox.pop("handler")
    res = await call({"text": "Hej", "language": "da", "save_as": "hej.aiff"})
    assert not res["ok"] and "save_as must end in .mp3, .wav, .flac, .ogg" in res["text"]
    res = await call({"text": "Hej", "language": "da", "model": "chatterbox-multilingual-v3"})
    assert not res["ok"] and "Chatterbox Multilingual V3 is not downloaded" in res["text"]
    fakes.speech["error"] = "the voice clip is shorter than two seconds"
    res = await call({"text": "Hej", "language": "da"})
    assert not res["ok"] and "Speech failed: the voice clip is shorter than two seconds" in res["text"]
    fakes.speech.pop("error")
    fakes.speech["refuse"] = "there is no text to speak"
    res = await call({"text": "Hej", "language": "da"})
    assert not res["ok"] and "there is no text to speak" in res["text"]
    fakes.speech.pop("refuse")
    app.state.router.transports.pop("speech:7100")                                 # the service is down
    res = await call({"text": "Hej", "language": "da"})
    assert not res["ok"] and "the speech service is not answering" in res["text"]


# ---------------------------------------------------------------- music and sound effects

async def test_a_song_is_made_on_the_card_and_saved(signed_in, app, fakes):
    install(app, "ace-step-1.5-xl-turbo")
    lyrics = "[verse]\nVi spiller på is\n[chorus]\nJegerup, Jegerup!"
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "music_generate", "arguments": {
            "prompt": "upbeat Danish stadium rock, male vocals, crowd chants", "lyrics": lyrics, "duration": 45,
            "bpm": 132, "key": "a minor", "language": "da", "seed": 3, "save_as": "jegerup.mp3"}}]},
        {"content": "Here: **jegerup.mp3**."}, {"content": "Song"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Make a club song"))
    assert run.status == "done", run.events[-1]
    g = fakes.comfy["prompts"][0]
    nodes = {n["class_type"]: n["inputs"] for n in g.values()}
    assert nodes["UNETLoader"]["unet_name"] == "acestep_v1.5_xl_turbo_bf16.safetensors"
    assert (nodes["DualCLIPLoader"]["clip_name1"], nodes["DualCLIPLoader"]["clip_name2"],
            nodes["DualCLIPLoader"]["type"]) == ("qwen_0.6b_ace15.safetensors", "qwen_4b_ace15.safetensors", "ace")
    enc = nodes["TextEncodeAceStepAudio1.5"]
    assert (enc["lyrics"], enc["bpm"], enc["keyscale"], enc["language"], enc["duration"], enc["timesignature"],
            enc["seed"]) == (lyrics, 132, "A minor", "da", 45.0, "4", 3)
    assert nodes["EmptyAceStep1.5LatentAudio"]["seconds"] == 45.0
    sampler = nodes["KSampler"]
    assert (sampler["steps"], sampler["cfg"], sampler["sampler_name"]) == (8, 1.0, "euler")
    assert nodes["ModelSamplingAuraFlow"]["shift"] == 3.0
    assert (nodes["SaveAudioAdvanced"]["format"], nodes["SaveAudioAdvanced"]["format.quality"]) == ("mp3", "V0")
    starts = [s["name"] for s in fakes.sup["starts"]]
    assert starts.count("main") == 2                                   # loaded, ComfyUI, loaded again
    res = tool_result(run)
    assert res["ok"] and "Saved jegerup.mp3: 45 s, ACE-Step 1.5 XL Turbo - 132 BPM, A minor, 4/4, lyrics in da" in \
        res["text"], res
    assert (chat_dir(app, c["id"]) / "jegerup.mp3").read_bytes()[:2] == b"\xff\xfb"


async def test_in_a_project_a_song_goes_to_audio_and_settings_choose_the_model(signed_in, app, fakes):
    install(app, "ace-step-1.5-xl-turbo", "ace-step-1.5-turbo")
    r = await signed_in.put("/hub/api/settings", json={"section": "sound", "values": {"music_model":
                                                                                      "ace-step-1.5-turbo"}})
    assert r.status_code == 200, r.text
    p = (await signed_in.post("/hub/api/projects", json={"name": "Club"})).json()
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "music_generate", "arguments": {"prompt": "calm piano"}}]},
        {"content": "Done."}, {"content": "Piano"}]
    c = await new_chat(signed_in, project_id=p["id"])
    run = await finish(app, await say(signed_in, c["id"], "Some calm piano"))
    g = fakes.comfy["prompts"][0]
    nodes = {n["class_type"]: n["inputs"] for n in g.values()}
    assert nodes["UNETLoader"]["unet_name"] == "acestep_v1.5_turbo.safetensors"
    assert nodes["DualCLIPLoader"]["clip_name2"] == "qwen_1.7b_ace15.safetensors"
    assert nodes["TextEncodeAceStepAudio1.5"]["lyrics"] == "[Instrumental]"        # no lyrics: an instrumental
    res = tool_result(run)
    assert res["ok"] and "Saved audio/calm-piano.mp3: 60 s" in res["text"] and "instrumental" in res["text"], res
    folder = app.state.hub.env.workspace_dir / "projects" / str(p["id"])
    assert (folder / "audio" / "calm-piano.mp3").is_file()
    desc = tools_offered(fakes, 0)["music_generate"]["description"]
    assert "ace-step-1.5-turbo: ACE-Step 1.5 Turbo (used without model)" in desc
    # save_as as a sandbox path lands where it says, and a folder gets a name from the prompt
    for save_as, saved in ((f"/workspace/projects/{p['id']}/audio/intro.mp3", "audio/intro.mp3"),
                           ("jingles/", "jingles/calm-piano.mp3"), ("audio", "audio/calm-piano-2.mp3")):
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = [{"tool_calls": [{"name": "music_generate", "arguments": {
            "prompt": "calm piano", "save_as": save_as}}]}, {"content": "Done."}]
        res = tool_result(await finish(app, await say(signed_in, c["id"], "Again")))
        assert res["ok"] and res["text"].startswith(f"Saved {saved}: 60 s"), (save_as, res)
        assert (folder / saved).is_file()
    assert not (folder / "workspace").exists()


async def test_sound_effects_with_stable_audio(signed_in, app, fakes):
    install(app, "stable-audio-3-medium")
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "sound_generate", "arguments": {
            "prompt": "Ice hockey arena horn at the end of a period, loud, huge echoing hall", "duration": 3,
            "save_as": "horn.flac"}}]},
        {"content": "Done."}, {"content": "Horn"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "A period horn"))
    g = fakes.comfy["prompts"][0]
    nodes = {n["class_type"]: n["inputs"] for n in g.values()}
    assert nodes["CheckpointLoaderSimple"]["ckpt_name"] == "stable_audio_3_medium.safetensors"
    assert (nodes["CLIPLoader"]["clip_name"], nodes["CLIPLoader"]["type"]) == ("t5gemma_b_b_ul2.safetensors",
                                                                               "stable_audio")
    texts = [n["inputs"]["text"] for n in g.values() if n["class_type"] == "CLIPTextEncode"]
    assert texts == ["TrackType: SFX, Ice hockey arena horn at the end of a period, loud, huge echoing hall. "
                     "Length: 3 seconds", ""]
    assert nodes["EmptyLatentAudio"]["seconds"] == 3.0
    assert (nodes["KSampler"]["sampler_name"], nodes["KSampler"]["steps"], nodes["KSampler"]["cfg"]) == ("lcm", 8, 1.0)
    assert nodes["SaveAudioAdvanced"]["format"] == "flac" and "format.quality" not in nodes["SaveAudioAdvanced"]
    res = tool_result(run)
    assert res["ok"] and res["text"].startswith("Saved horn.flac: 3 s, Stable Audio 3 Medium"), res
    assert (chat_dir(app, c["id"]) / "horn.flac").is_file()
    # a ComfyUI without the nodes says what to do
    fakes.comfy["missing_nodes"] = ["EmptyLatentAudio"]
    app.state.hub.comfy._object_info = None
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [fakes.llama["replies"][0], {"content": "?"}]
    run = await finish(app, await say(signed_in, c["id"], "Again"))
    res = tool_result(run)
    assert not res["ok"] and "ComfyUI does not know these nodes: EmptyLatentAudio - update ComfyUI" in res["text"]


async def test_a_persons_sound_goes_to_her_device_and_comfyui_keeps_nothing(signed_in, app, fakes, tmp_path):
    hub = app.state.hub
    hub.comfy.files = fakes.comfy["files"] = tmp_path / "comfy"
    install(app, "stable-audio-3-medium", "roest-v3")
    await add_person(signed_in)
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "sound_generate", "arguments": {"prompt": "goal horn", "duration": 2}}]},
        {"tool_calls": [{"name": "speech_generate", "arguments": {"text": "Mål!", "language": "da",
                                                                  "voice": ".hub/attachments/me.wav"}}]},
        # a long FLAC would be too big to reach her device: refused before the card works on it
        {"tool_calls": [{"name": "sound_generate", "arguments": {"prompt": "rain on a roof", "duration": 300,
                                                                 "save_as": "rain.flac"}}]},
        {"content": "Both ready."}]
    seen: list[dict] = []

    def ffmpeg(body):                                   # her clip is converted in the sandbox, confined to her turn
        seen.append(body)
        dst = shlex.split(body["cmd"])[-1]
        (hub.env.workspace_dir / dst.removeprefix("/workspace/")).write_bytes(wav_tone(6))
        return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "duration_ms": 5, "truncated": False}

    fakes.sandbox["handler"] = ffmpeg
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Goal", "messages": [user_msg("A goal horn and my voice saying goal")],
                                      "files": [{"path": ".hub/attachments/me.wav", "mime": "audio/wav",
                                                 "data": base64.b64encode(wav_tone(7)).decode()}]})
        assert res["status"] == "done", res
        names = {f["path"] for f in res["files"]}
        assert names == {"goal-horn.mp3", "maal.mp3"}
        results = json.dumps([b for m in res["messages"] for b in m["content"] if b["type"] == "tool_result"])
        assert "a FLAC file of 300 s would be too big to reach the user's device" in results
        assert len(fakes.comfy["prompts"]) == 1
        card = {f["path"] for f in next(s for s in res["steps"] if s["type"] == "files")["files"]}
        assert card == names
        r = await phone.get(f"/hub/api/device/turns/{res['id']}/file", params={"path": "maal.mp3"})
        assert r.content[:2] == b"\xff\xfb"
        await phone.post(f"/hub/api/device/turns/{res['id']}/done", headers={"Origin": "http://hub"})
    assert fakes.comfy["history_deleted"] == ["p1"]                     # the prompt and the file name
    assert not any((tmp_path / "comfy").rglob("*.mp3"))                 # and the sound in its output folder
    spoken = fakes.speech["requests"][0]
    assert spoken["text"] == "Mål!" and spoken["voice"]["audio"]
    assert spoken["cache_voice"] is False                                # her voice is not kept in the service
    root = seen[0]["confine"]["root"]
    assert root.startswith("/workspace/device/") and seen[0]["confine"]["require"] is True
    assert f"-i {root}/.hub/attachments/me.wav" in seen[0]["cmd"]


async def test_a_file_too_big_for_her_device_is_said_so(signed_in, app, fakes, monkeypatch):
    from app.agent import device
    monkeypatch.setattr(device, "MAX_OUT_FILE", 4096)                  # 25 MB in real life
    install(app, "roest-v3")
    await add_person(signed_in)
    fakes.llama["replies"] = [{"tool_calls": [{"name": "speech_generate", "arguments": {
        "text": "Mål til Jegerup! " * 6, "language": "da"}}]}, {"content": "Here."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Big", "messages": [user_msg("Say it")]})
    results = json.dumps([b for m in res["messages"] for b in m["content"] if b["type"] == "tool_result"])
    assert "a file may have to reach the user's device in this chat, so it will not get there" in results
    assert "The user can play it under your answer" not in results


# ---------------------------------------------------------------- pieces

def test_graphs_and_prompts():
    assert W.ace_key("F#m") == "F# minor" and W.ace_key("bb") == "Bb major" and W.ace_key("H-dur") == "C major"
    g = W.ace_step(caption="x", lyrics="", seconds=900, seed=2**64, bpm=999, key="e minor", time_signature=7,
                   language="xx", unet="u", text_encoder="t", planner="p", vae="v", fmt="ogg")
    enc = g["5"]["inputs"]
    assert (enc["duration"], enc["bpm"], enc["timesignature"], enc["language"]) == (600.0, 300, "4", "unknown")
    assert enc["seed"] < 2**63 and g["10"]["inputs"]["format"] == "opus"
    assert W.sa3_prompt("Rain on a tin roof.", "ambience", 12.4) == \
        "TrackType: SFX, Rain on a tin roof. Length: 12 seconds"
    assert W.sa3_prompt("Lo-fi drum loop", "loop", 8, bpm=80) == \
        "TrackType: Instrument, Lo-fi drum loop. BPM: 80. Length: 8 seconds"
    assert W.sa3_prompt("TrackType: Music, jazz. Length: 30 seconds", "music", 30) == \
        "TrackType: Music, jazz. Length: 30 seconds"
    assert slug("Tredive sekunder tilbage!") == "tredive-sekunder-tilbage" and slug("Mål på ø!") == "maal-paa-oe"
    assert slug("!!!") == "audio" and len(slug("ord " * 40)) <= 48


def test_a_voice_is_heard_as_low_or_high(tmp_path):
    for hz, label in ((100, "male voice"), (125, "male voice"), (190, "female voice"), (240, "female voice")):
        p = tmp_path / f"{hz}.wav"
        p.write_bytes(_voiced(hz))
        assert abs(median_pitch(p) - hz) < hz * 0.06 and describe_voice(p) == label
    noise = tmp_path / "noise.wav"
    import numpy as np
    import wave
    with wave.open(str(noise), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes((np.random.default_rng(1).normal(0, 3000, 24000 * 3)).astype(np.int16).tobytes())
    assert describe_voice(noise) == "" and describe_voice(tmp_path / "missing.wav") == ""


def _speech_server():
    spec = importlib.util.spec_from_file_location("speech_server", ROOT / "services" / "speech" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wav(tag: int, bits: int, frames: bytes, channels: int = 1, rate: int = 24000, extensible: bool = False,
         before_data: bytes = b"", data_size: int | None = None) -> bytes:
    """A WAV with any format tag and sample size; extensible puts the tag in the sub-format GUID."""
    import struct
    align = channels * bits // 8
    if extensible:
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * align, align, bits, 22, bits, 0) + \
            struct.pack("<H", tag) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    else:
        fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * align, align, bits)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt + before_data + b"data" + \
        struct.pack("<I", len(frames) if data_size is None else data_size) + frames
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def test_both_wav_readers_take_pcm_and_float_and_refuse_the_rest():
    """The Hub (voice labels) and the speech service (voices, clips) read WAVs with the same small reader: the
    Røst samples are 32-bit float, ffmpeg writes 16-bit PCM with a LIST chunk, other tools 24-bit or extensible."""
    import struct

    import numpy as np

    from app.audio.wavfile import parse_wav as hub_parse
    service_parse = _speech_server().parse_wav
    x = (0.5 * np.sin(2 * np.pi * 220 * np.arange(2400) / 24000)).astype(np.float32)
    s24 = (x * 8388607).astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    cases = {
        "8-bit": (_wav(1, 8, np.round(x * 127 + 128).astype(np.uint8).tobytes()), 1 / 64),
        "16-bit": (_wav(1, 16, (x * 32767).astype("<i2").tobytes()), 1e-4),
        "24-bit": (_wav(1, 24, s24), 1e-6),
        "32-bit": (_wav(1, 32, (x * 2147483647).astype("<i4").tobytes()), 1e-6),
        "float": (float_wav(x), 1e-7),
        "double": (_wav(3, 64, x.astype("<f8").tobytes()), 1e-7),
        "extensible 16-bit": (_wav(1, 16, (x * 32767).astype("<i2").tobytes(), extensible=True), 1e-4),
        "extensible float": (_wav(3, 32, x.astype("<f4").tobytes(), extensible=True), 1e-7),
        "stereo, mixed down": (_wav(1, 16, (np.stack([x, x], 1) * 32767).astype("<i2").tobytes(), channels=2), 1e-4),
        "an odd LIST chunk first, as ffmpeg": (_wav(1, 16, (x * 32767).astype("<i2").tobytes(), before_data=b"LIST" +
                                               struct.pack("<I", 3) + b"abc\x00"), 1e-4),
        "a data size from a stream": (_wav(1, 16, (x * 32767).astype("<i2").tobytes(), data_size=0xFFFFFFFF), 1e-4),
    }
    for name, (data, tol) in cases.items():
        for parse in (hub_parse, service_parse):
            got, rate = parse(data)
            assert rate == 24000 and got.dtype == np.float32 and len(got) == len(x), name
            assert float(np.max(np.abs(got - x))) <= tol, name
    got, _ = hub_parse(float_wav(np.concatenate([x] * 20)), max_seconds=1.0)
    assert len(got) == 24000
    nan = _wav(3, 32, np.array([np.nan, np.inf, -2.0, 0.25], "<f4").tobytes())
    assert service_parse(nan)[0].tolist() == [0.0, 0.0, -1.0, 0.25]
    pcm = (x * 32767).astype("<i2").tobytes()
    for bad in (b"not a wav at all", b"RIFF\x04\x00\x00\x00WAVE", _wav(2, 4, pcm), _wav(1, 12, pcm),
                _wav(1, 16, pcm, channels=0), _wav(1, 16, pcm, rate=1000), b"OggS" + bytes(100)):
        for parse in (hub_parse, service_parse):
            try:
                parse(bad)
                raise AssertionError(bad[:16])
            except ValueError as exc:
                assert "must be a WAV file" in str(exc)


def test_the_speech_service_keeps_a_clip_voice_only_when_asked():
    from collections import OrderedDict

    import numpy as np
    S = _speech_server()
    m = S.Model.__new__(S.Model)
    m.voices, m.default = OrderedDict(), None
    made: list[float] = []
    m._prepare = lambda clip, ex: made.append(ex) or object()
    clip = np.zeros(3 * 24000, np.float32)
    m.voice(clip, "hers", 0.5, cache=False)                             # a person's clip: used, not kept
    assert not m.voices
    mine = m.voice(clip, "mine", 0.5)
    assert list(m.voices) == ["mine:0.500"] and m.voice(clip, "mine", 0.5) is mine and len(made) == 2


def test_the_speech_service_cuts_text_into_pieces():
    S = _speech_server()
    text = ("Velkommen til kampen. Holdene er klar! Vi starter om lidt...\n\n"
            "Andet afsnit kommer her. " + "Dette er en meget lang sætning, som bliver ved og ved, " * 8 + "og slutter.")
    pieces = S.split_text(text, limit=120)
    assert all(len(p) <= 120 for p, _ in pieces)
    assert pieces[0] == ("Velkommen til kampen. Holdene er klar! Vi starter om lidt...", S.PARAGRAPH_PAUSE)
    assert pieces[-1][1] == 0.0 and all(pause == S.PAUSE for _, pause in pieces[1:-1])
    assert " ".join(p for p, _ in pieces).split() == text.split()           # nothing lost, nothing added
    assert S.split_text("  \n  ") == [] and S.split_text("Ja") == [("Ja", 0.0)]
    assert S.max_tokens("Ja") == 81 and S.max_tokens("x" * 1000) == 1000
    p = S.params({"temperature": 9, "top_k": 50, "exaggeration": None})
    assert p["temperature"] == 2.0 and p["top_k"] == 50 and p["exaggeration"] == 0.5
    import numpy as np
    wav = np.concatenate([np.zeros(24000), 0.3 * np.sin(np.arange(24000) / 5), np.zeros(12000)]).astype(np.float32)
    cut = S.trim(wav)
    assert 24000 <= len(cut) <= 24000 + 2 * 0.05 * 24000


def test_the_speech_service_files_stay_in_the_models_folder(tmp_path, monkeypatch):
    S = _speech_server()
    monkeypatch.setattr(S, "MODELS_ROOT", tmp_path)
    (tmp_path / "a.pt").write_bytes(b"x")
    assert S.inside_models(str(tmp_path / "a.pt")) == tmp_path / "a.pt"
    for bad in ("/etc/passwd", str(tmp_path / ".." / "x"), str(tmp_path / "missing.pt")):
        try:
            S.inside_models(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_the_compose_file_runs_the_speech_service_without_the_gpu():
    text = (ROOT / "deploy" / "docker-compose.yml").read_text()
    speech = text.split("\n  speech:\n", 1)[1].split("\n\n  ", 1)[0]
    assert "<<: *gpu" not in speech and "/dev/kfd" not in speech
    # models/ read-only, not models/speech: Docker would make a missing mount folder as root on the first update
    assert "/models:/models:ro" in speech and "models/speech:" not in speech and "networks: [core]" in speech
    # no hard CPU cap: one above the VM's cores stops the container from starting
    assert "cpus:" not in speech and "cpu_shares:" in speech
    assert "HUB_SPEECH_URL: http://speech:7100" in text
    assert json.loads((ROOT / "backend" / "app" / "catalog" / "models.json").read_text())["audio"]
