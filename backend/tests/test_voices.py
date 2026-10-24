"""0.18.0 - voices and remakes: a song that takes on a clip's sound (ACE-Step's timbre encoder), a song re-made in
another's sound (ACE-Step cover), stems (Mel-Band RoFormer) and another voice on a recording (Seed-VC) - the
tools, the files they convert in the sandbox, the graphs they queue, what they save and say, people's turns -
against the fake services. The nodes themselves (services/comfyui/nodes/slq_audio) run in ComfyUI only; here
their names, defaults and the image that installs them are checked against the Hub's side."""
from __future__ import annotations

import ast
import base64
import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from app.agent.tools.audio import flac_seconds
from app.audio import workflows as W

from .mocks import fake_flac, wav_tone
from .test_agent import finish, new_chat, say
from .test_audio import chat_dir, install, system, tool_result, tools_offered
from .test_people import add_person, client_from, sign_in, turn, user_msg

ROOT = Path(__file__).resolve().parents[2]
NODES = ROOT / "services" / "comfyui" / "nodes" / "slq_audio"


def sandbox_ffmpeg(app, lengths: dict[str, float]):
    """The sandbox for the sound tools: ffprobe tells a file's length (lengths by name, else 60 s) and ffmpeg
    writes a FLAC of its first -t seconds where asked."""
    hub = app.state.hub
    seen: list[dict] = []

    def handler(body):
        seen.append(body)
        argv = shlex.split(body["cmd"].split("&&")[-1])
        src, dst = argv[argv.index("-i") + 1], argv[-1]
        full = lengths.get(Path(src).name, 60.0)
        (hub.env.workspace_dir / dst.removeprefix("/workspace/")).write_bytes(
            fake_flac(min(float(argv[argv.index("-t") + 1]), full)))
        return {"exit_code": 0, "stdout": f"{full}\n", "stderr": "", "timed_out": False, "duration_ms": 5,
                "truncated": False}
    return seen, handler


def files_in(app, chat_id: int, *names: str) -> Path:
    d = chat_dir(app, chat_id)
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).parent.mkdir(parents=True, exist_ok=True)
        (d / n).write_bytes(b"sound")
    return d


def nodes_of(graph: dict, class_type: str) -> list[dict]:
    return [n["inputs"] for n in graph.values() if n["class_type"] == class_type]


async def call(app, client, chat_id: int, fakes, name: str, args: dict, text: str = "Do it") -> dict:
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": name, "arguments": args}]}, {"content": "Done."},
                              {"content": "Title"}]
    run = await finish(app, await say(client, chat_id, text))
    return tool_result(run)


# ---------------------------------------------------------------- a song with a clip's sound

async def test_a_song_takes_on_the_sound_of_a_clip(signed_in, app, fakes, tmp_path):
    hub = app.state.hub
    hub.comfy.files = fakes.comfy["files"] = tmp_path / "comfy"
    install(app, "ace-step-1.5-xl-turbo")
    c = await new_chat(signed_in)
    files_in(app, c["id"], "soren.m4a")
    seen, fakes.sandbox["handler"] = sandbox_ffmpeg(app, {"soren.m4a": 47.0})
    res = await call(app, signed_in, c["id"], fakes, "music_generate", {
        "prompt": "warm Danish folk, acoustic guitar", "lyrics": "[verse]\nLangt mod nord", "duration": 30,
        "language": "da", "reference": "soren.m4a"})
    assert res["ok"], res
    cmd = seen[0]["cmd"]
    assert "-protocol_whitelist file" in cmd and " -t 30 " in cmd and "-ac 2 -ar 44100 -sample_fmt s16 -c:a flac" in cmd
    up = fakes.comfy["uploads"][0]
    assert re.fullmatch(r"hub-\d+-reference\.flac", up)
    g = fakes.comfy["prompts"][0]
    assert g["11"] == {"class_type": "LoadAudio", "inputs": {"audio": up}}
    assert g["12"] == {"class_type": "VAEEncodeAudio", "inputs": {"audio": ["11", 0], "vae": ["4", 0]}}
    assert g["13"] == {"class_type": "SLQAceStepReference", "inputs": {"model": ["2", 0], "timbre": ["12", 0]}}
    assert g["8"]["inputs"]["model"] == ["13", 0] and g["8"]["inputs"]["latent_image"] == ["7", 0]
    assert g["5"]["inputs"]["generate_audio_codes"] is True          # the planner still writes the structure
    assert "with the sound of soren.m4a" in res["text"]
    assert "Only the first 0:30 of the reference clip (0:47) were used." in res["text"]
    # the clip's copy left ComfyUI's input folder, also in the owner's chat (a song is tens of MB)
    assert not any((tmp_path / "comfy" / "input").iterdir())
    desc = tools_offered(fakes, 0)["music_generate"]
    assert "reference" in desc["parameters"]["properties"] and "it does not copy a particular person's voice" in \
        desc["description"]
    # a missing clip is said so, and nothing is queued
    res = await call(app, signed_in, c["id"], fakes, "music_generate", {"prompt": "folk", "reference": "nope.wav"})
    assert not res["ok"] and "No such file in the working folder: nope.wav" in res["text"]
    assert len(fakes.comfy["prompts"]) == 1


# ---------------------------------------------------------------- remake

async def test_a_song_remade_with_the_sound_of_another(signed_in, app, fakes):
    install(app, "ace-step-1.5-turbo")
    c = await new_chat(signed_in)
    files_in(app, c["id"], "sang.wav", "live/koncert.mp3", "lang.wav")
    seen, fakes.sandbox["handler"] = sandbox_ffmpeg(app, {"sang.wav": 200.0, "koncert.mp3": 95.0, "lang.wav": 400.0})
    res = await call(app, signed_in, c["id"], fakes, "music_remake", {
        "audio": "sang.wav", "prompt": "live concert recording, cheering audience, big arena reverb",
        "lyrics": "[verse]\nVi spiller på is", "reference": "live/koncert.mp3", "keep": 0.3, "language": "da",
        "seed": 5})
    assert res["ok"], res
    assert " -t 300 " in seen[0]["cmd"] and " -t 30 " in seen[1]["cmd"]
    song, ref = fakes.comfy["uploads"]
    assert song.endswith("-song.flac") and ref.endswith("-reference.flac")
    g = fakes.comfy["prompts"][0]
    assert g["11"]["inputs"] == {"audio": song} and g["13"]["inputs"] == {"audio": ref}
    assert g["15"] == {"class_type": "SLQAceStepReference",
                       "inputs": {"model": ["2", 0], "source": ["12", 0], "timbre": ["14", 0]}}
    enc = g["5"]
    assert enc["class_type"] == "SLQAceStepEncode"
    assert enc["inputs"] == {"clip": ["3", 0], "tags": "live concert recording, cheering audience, big arena reverb",
                             "lyrics": "[verse]\nVi spiller på is", "language": "da", "duration": 200.0, "seed": 5,
                             "bpm": 0, "keyscale": "", "timesignature": ""}           # unknown: left out, not guessed
    ks = g["8"]["inputs"]
    assert ks["model"] == ["15", 0] and ks["latent_image"] == ["12", 0] and ks["denoise"] == 0.7
    assert not nodes_of(g, "EmptyAceStep1.5LatentAudio") and not nodes_of(g, "TextEncodeAceStepAudio1.5")
    assert res["text"].startswith("Saved sang-remake.mp3: a new version of sang.wav with the sound of live/koncert.mp3, "
                                  "200 s, ACE-Step 1.5 Turbo, keep 0.3, lyrics in da, seed 5")
    assert "Only the first 0:30 of the reference clip (1:35) were used." in res["text"]
    assert (chat_dir(app, c["id"]) / "sang-remake.mp3").read_bytes()[:2] == b"\xff\xfb"
    desc = tools_offered(fakes, 0)["music_remake"]["description"]
    assert "The model hears the reference, you do not" in desc and "without them the singing loses its words" in desc
    # known metadata goes in; no lyrics is said so; a long song is cut to 5 minutes
    res = await call(app, signed_in, c["id"], fakes, "music_remake", {
        "audio": "lang.wav", "prompt": "lo-fi", "bpm": 92, "key": "f#m", "time_signature": 3})
    assert res["ok"], res
    g = fakes.comfy["prompts"][1]
    assert (g["5"]["inputs"]["bpm"], g["5"]["inputs"]["keyscale"], g["5"]["inputs"]["timesignature"]) == \
        (92, "F# minor", "3")
    assert g["8"]["inputs"]["denoise"] == 1.0 and "timbre" not in g["15"]["inputs"]
    assert "no lyrics given, so the words are not kept, 92 BPM, F# minor, 3/4" in res["text"]
    assert "Only the first 5:00 of the song (6:40) were used." in res["text"]
    assert "music_remake re-performs an existing song" in system(fakes, 0)


# ---------------------------------------------------------------- stems

async def test_a_song_taken_apart(signed_in, app, fakes):
    install(app, "mel-band-roformer-vocals")
    c = await new_chat(signed_in)
    files_in(app, c["id"], "Min Sang.flac")
    seen, fakes.sandbox["handler"] = sandbox_ffmpeg(app, {"Min Sang.flac": 185.0})
    res = await call(app, signed_in, c["id"], fakes, "audio_separate", {"audio": "Min Sang.flac"})
    assert res["ok"], res
    offered = tools_offered(fakes, 0)
    assert "audio_separate" in offered and not {"voice_convert", "music_remake"} & set(offered)
    assert " -t 600 " in seen[0]["cmd"]
    g = fakes.comfy["prompts"][0]
    assert g["2"] == {"class_type": "SLQSeparateVocals", "inputs": {
        "audio": ["1", 0], "model": "separation/MelBandRoformer.ckpt",
        "config": "configs/KimberleyJensen/config_vocals_mel_band_roformer_kj.yaml", "overlap": 2, "batch": 2}}
    assert (g["3"]["inputs"]["audio"], g["3"]["inputs"]["filename_prefix"]) == (["2", 0], "audio/hub-vocals")
    assert (g["4"]["inputs"]["audio"], g["4"]["inputs"]["filename_prefix"]) == (["2", 1], "audio/hub-accompaniment")
    assert res["text"].startswith("Saved min-sang-vocals.mp3 (vocals) and min-sang-accompaniment.mp3 (accompaniment): "
                                  "3:05, Mel-Band RoFormer vocals")
    d = chat_dir(app, c["id"])
    assert (d / "min-sang-vocals.mp3").is_file() and (d / "min-sang-accompaniment.mp3").is_file()
    assert [a["path"] for a in res["display"]["audio"]] == ["min-sang-vocals.mp3", "min-sang-accompaniment.mp3"]
    # again: one number for both; only the vocals, under a name of its own, as FLAC
    res = await call(app, signed_in, c["id"], fakes, "audio_separate", {"audio": "Min Sang.flac"})
    assert res["text"].startswith("Saved min-sang-vocals-2.mp3 (vocals) and min-sang-accompaniment-2.mp3")
    res = await call(app, signed_in, c["id"], fakes, "audio_separate", {
        "audio": "Min Sang.flac", "keep": "vocals", "save_as": "stems/acapella.flac"})
    g = fakes.comfy["prompts"][2]
    assert "4" not in g and g["3"]["inputs"]["format"] == "flac"
    assert res["text"].startswith("Saved stems/acapella-vocals.flac (vocals): 3:05") and (d / "stems" /
                                                                                         "acapella-vocals.flac").is_file()
    res = await call(app, signed_in, c["id"], fakes, "audio_separate", {"audio": "Min Sang.flac", "keep": "drums"})
    assert not res["ok"] and "keep must be both, vocals or accompaniment" in res["text"]


# ---------------------------------------------------------------- another voice

async def test_a_song_sung_in_another_voice(signed_in, app, fakes):
    install(app, "seed-vc", "mel-band-roformer-vocals")
    c = await new_chat(signed_in)
    files_in(app, c["id"], "sang.mp3", "soren synger.wav")
    seen, fakes.sandbox["handler"] = sandbox_ffmpeg(app, {"sang.mp3": 180.0, "soren synger.wav": 12.0})
    res = await call(app, signed_in, c["id"], fakes, "voice_convert", {
        "audio": "sang.mp3", "voice": "soren synger.wav", "vocals_only": True})
    assert res["ok"], res
    assert " -t 600 " in seen[0]["cmd"] and " -t 30 " in seen[1]["cmd"]
    audio, voice = fakes.comfy["uploads"]
    g = fakes.comfy["prompts"][0]
    assert g["1"]["inputs"] == {"audio": audio} and g["2"]["inputs"] == {"audio": voice}
    assert g["3"]["inputs"]["audio"] == ["1", 0] and g["4"]["inputs"]["audio"] == ["2", 0]
    assert g["3"]["class_type"] == g["4"]["class_type"] == "SLQSeparateVocals"
    assert g["5"] == {"class_type": "SLQSeedVC", "inputs": {
        "source": ["3", 0], "reference": ["4", 0],
        "dit": "seed-vc/DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth",
        "config": "configs/presets/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml", "whisper": "seed-vc/whisper-small",
        "vocoder": "seed-vc/bigvgan_v2_44khz_128band_512x", "rmvpe": "seed-vc/rmvpe.pt", "steps": 40, "cfg": 0.7,
        "pitch": 0, "auto_octave": True, "match_loudness": True, "reference_seconds": 25.0}}
    assert g["6"] == {"class_type": "AudioMerge", "inputs": {"audio1": ["3", 1], "audio2": ["5", 0],
                                                             "merge_method": "add"}}
    assert g["7"]["inputs"]["filename_prefix"] == "audio/hub-mix" and g["8"]["inputs"]["audio"] == ["5", 0]
    assert res["text"].startswith("Saved sang-as-soren-synger-mix.mp3: sang.mp3 in the voice of soren synger.wav - "
                                  "singing, the vocals separated from the music, converted and mixed back, the "
                                  "melody moved up 12 semitones (1 octave); 18.5 s of the clip had voice in it.")
    assert "The converted vocals alone: sang-as-soren-synger-vocals.mp3." in res["text"]
    d = chat_dir(app, c["id"])
    assert (d / "sang-as-soren-synger-mix.mp3").is_file() and (d / "sang-as-soren-synger-vocals.mp3").is_file()
    offered = tools_offered(fakes, 0)
    assert "a clip from a song works: its vocals are separated first" in offered["voice_convert"]["description"]
    assert "Only a voice the user has the right to use" in offered["voice_convert"]["description"]
    assert "make the song with music_generate, then convert its singing" in system(fakes, 0)
    # a chosen shift, fewer steps
    fakes.comfy["vc_text"] = json.dumps({"semitones": -3, "reference_seconds": 9.0, "seconds": 180.0})
    res = await call(app, signed_in, c["id"], fakes, "voice_convert", {
        "audio": "sang.mp3", "voice": "soren synger.wav", "pitch": -3, "auto_octave": False, "steps": 30})
    vc = fakes.comfy["prompts"][1]["5"]["inputs"]
    assert (vc["pitch"], vc["auto_octave"], vc["steps"]) == (-3, False, 30) and "8" not in fakes.comfy["prompts"][1]
    assert "the melody moved down 3 semitones; 9 s of the clip" in res["text"] and "-mix" not in res["text"]


async def test_speech_in_another_voice_and_what_is_refused(signed_in, app, fakes):
    install(app, "seed-vc")                                            # no separation model
    c = await new_chat(signed_in)
    files_in(app, c["id"], "eventyr.wav", "stemme.wav", "sang.mp3")
    _, fakes.sandbox["handler"] = sandbox_ffmpeg(app, {"eventyr.wav": 40.0, "stemme.wav": 8.0})
    res = await call(app, signed_in, c["id"], fakes, "voice_convert", {
        "audio": "eventyr.wav", "voice": "stemme.wav", "singing": False})
    assert res["ok"], res
    g = fakes.comfy["prompts"][0]
    assert not nodes_of(g, "SLQSeparateVocals") and not nodes_of(g, "AudioMerge")
    vc = g["5"]["inputs"]
    assert (vc["source"], vc["reference"], vc["dit"], vc["config"], vc["vocoder"], vc["rmvpe"], vc["steps"],
            vc["auto_octave"]) == (["1", 0], ["2", 0], "seed-vc/DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth",
                                   "configs/presets/config_dit_mel_seed_uvit_whisper_small_wavenet.yml",
                                   "seed-vc/bigvgan_v2_22khz_80band_256x", "", 25, False)
    assert g["7"]["inputs"]["filename_prefix"] == "audio/hub-voice"
    assert res["text"].startswith("Saved eventyr-as-stemme.mp3: eventyr.wav in the voice of stemme.wav - speech;")
    assert "melody" not in res["text"]
    desc = tools_offered(fakes, 0)["voice_convert"]["description"]
    assert "need Mel-Band RoFormer vocals, which is not downloaded" in desc
    # singing over music without the separation model: refused before anything is queued
    res = await call(app, signed_in, c["id"], fakes, "voice_convert", {"audio": "sang.mp3", "voice": "stemme.wav"})
    assert not res["ok"] and "a voice in music has to be separated from it first: download Mel-Band RoFormer vocals" \
        in res["text"]
    assert len(fakes.comfy["prompts"]) == 1
    # a ComfyUI image from before 0.18.0 is named as such
    fakes.comfy["missing_nodes"] = ["SLQSeedVC"]
    app.state.hub.comfy._object_info = None
    res = await call(app, signed_in, c["id"], fakes, "voice_convert", {
        "audio": "eventyr.wav", "voice": "stemme.wav", "singing": False})
    assert "ComfyUI does not have the Hub's own nodes (SLQSeedVC): its image is older than this Hub - update.sh " \
           "rebuilds it" in res["text"]
    # what a node reports when it fails reaches the model
    fakes.comfy["missing_nodes"] = []
    app.state.hub.comfy._object_info = None
    fakes.comfy["fail_with"] = "the voice clip has less than a second of voice in it"
    res = await call(app, signed_in, c["id"], fakes, "voice_convert", {
        "audio": "eventyr.wav", "voice": "stemme.wav", "singing": False})
    assert not res["ok"] and "SLQSeedVC: the voice clip has less than a second of voice in it" in res["text"]


async def test_a_persons_voice_conversion_leaves_nothing_in_comfyui(signed_in, app, fakes, tmp_path):
    hub = app.state.hub
    hub.comfy.files = fakes.comfy["files"] = tmp_path / "comfy"
    install(app, "seed-vc")
    await add_person(signed_in)
    seen, fakes.sandbox["handler"] = sandbox_ffmpeg(app, {})
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "voice_convert", "arguments": {
            "audio": ".hub/attachments/me.wav", "voice": ".hub/attachments/me.wav", "singing": False}}]},
        {"content": "Here."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Voice", "messages": [user_msg("My voice on my voice")],
                                      "files": [{"path": ".hub/attachments/me.wav", "mime": "audio/wav",
                                                 "data": base64.b64encode(wav_tone(3)).decode()}]})
        assert res["status"] == "done", res
        assert {f["path"] for f in res["files"]} == {"me-as-me.mp3"}
    root = seen[0]["confine"]["root"]
    assert root.startswith("/workspace/device/") and seen[0]["confine"]["require"] is True
    assert fakes.comfy["history_deleted"] == ["p1"]
    assert not any((tmp_path / "comfy").rglob("*.flac")) and not any((tmp_path / "comfy").rglob("*.mp3"))


# ---------------------------------------------------------------- pieces

def test_a_flac_files_length_from_its_header():
    assert flac_seconds(fake_flac(61.5)) == pytest.approx(61.5, abs=1e-4)
    assert flac_seconds(fake_flac(3, rate=48000, channels=1)) == pytest.approx(3.0)
    assert flac_seconds(b"ID3....") == 0.0 and flac_seconds(b"") == 0.0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_flac_from_ffmpeg(tmp_path):
    out = tmp_path / "x.flac"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2.5",
                    "-ac", "2", "-ar", "44100", "-sample_fmt", "s16", "-c:a", "flac", str(out)], check=True)
    assert flac_seconds(out.read_bytes()) == pytest.approx(2.5, abs=0.01)


def test_graphs():
    g = W.ace_remake(song="s.flac", caption="c", lyrics="", seconds=900, seed=2**64, bpm=999, key="x", time_signature=5,
                     language="xx", unet="u", text_encoder="t", planner="p", vae="v", keep=3)
    enc = g["5"]["inputs"]
    assert (enc["duration"], enc["bpm"], enc["keyscale"], enc["timesignature"], enc["language"]) == \
        (600.0, 300, "C major", "", "unknown")
    assert enc["seed"] < 2**63 and g["8"]["inputs"]["denoise"] == 0.1 and "13" not in g
    s = W.separate(audio="a.flac", model="m", config="c", stems=("accompaniment",), fmt="ogg")
    assert set(s) == {"1", "2", "4"} and s["4"]["inputs"]["format"] == "opus"
    with pytest.raises(ValueError, match="needs the vocal separation model"):
        W.seed_vc(audio="a", voice="v", dit="d", config="c", whisper="w", vocoder="o", music=True)
    v = W.seed_vc(audio="a", voice="v", dit="d", config="c", whisper="w", vocoder="o", steps=999, cfg=3, pitch=-99)
    assert (v["5"]["inputs"]["steps"], v["5"]["inputs"]["cfg"], v["5"]["inputs"]["pitch"]) == (200, 1.0, -24)
    assert v["5"]["inputs"]["reference"] == ["2", 0] and set(v) == {"1", "2", "5", "7"}


def _node_ids() -> set[str]:
    ids = set()
    for p in NODES.glob("*.py"):
        ids |= set(re.findall(r'node_id="(SLQ\w+)"', p.read_text(encoding="utf-8")))
    return ids


def test_the_hubs_nodes_are_the_ones_its_graphs_use():
    for p in NODES.glob("*.py"):
        ast.parse(p.read_text(encoding="utf-8"), str(p))               # they compile (they import ComfyUI, not here)
    used = set()
    for g in (W.ace_step(caption="c", lyrics="", seconds=10, seed=1, bpm=90, key="", time_signature=4, language="en",
                         unet="u", text_encoder="t", planner="p", vae="v", timbre="t.flac"),
              W.ace_remake(song="s", caption="c", lyrics="", seconds=10, seed=1, bpm=None, key=None, time_signature=None,
                           language="en", unet="u", text_encoder="t", planner="p", vae="v", reference="r"),
              W.separate(audio="a", model="m", config="c"),
              W.seed_vc(audio="a", voice="v", dit="d", config="c", whisper="w", vocoder="o", separator="s", music=True,
                        vocals_only=True)):
        used |= {c for c in W.class_types(g) if c.startswith("SLQ")}
    assert used == _node_ids() == {"SLQAceStepReference", "SLQAceStepEncode", "SLQSeparateVocals", "SLQSeedVC"}


def test_the_catalog_the_nodes_and_the_image_agree():
    cat = json.loads((ROOT / "backend" / "app" / "catalog" / "models.json").read_text())
    audio = {e["id"]: e for e in cat["audio"]}
    vc, sep = audio["seed-vc"], audio["mel-band-roformer-vocals"]
    src = (NODES / "seedvc.py").read_text() + (NODES / "separate.py").read_text()
    # the nodes' defaults are the catalog's files and configs (a graph built by hand in ComfyUI finds them)
    for entry, role in ((vc, "dit_singing"), (vc, "rmvpe"), (sep, "separator")):
        dest = next(f["dest"] for f in entry["files"] if f["role"] == role)
        assert f'default="{dest.removeprefix("comfy/")}"' in src, role
    for entry, role in ((vc, "whisper"), (vc, "vocoder_singing")):
        folder = Path(next(f["dest"] for f in entry["files"] if f["role"] == role)).parent.as_posix()
        assert f'default="{folder.removeprefix("comfy/")}"' in src, role
    assert f'default="{vc["settings"]["config_singing"]}"' in src and f'default="{sep["settings"]["config"]}"' in src
    # every file of both lives in ComfyUI's models folder, whisper and BigVGAN as the folders they load from
    for e in (vc, sep):
        assert all(f["dest"].startswith("comfy/") and f["size"] > 0 for f in e["files"]) and e["engine"] == "comfy"
    names = {Path(f["dest"]).name for f in vc["files"] if f["role"] in ("whisper", "vocoder_singing", "vocoder_speech")}
    assert {"config.json", "model.safetensors", "preprocessor_config.json", "bigvgan_generator.pt"} == names
    # the image clones the code the nodes load at the commits they were written for, and installs the package
    docker = (ROOT / "services" / "comfyui" / "Dockerfile").read_text()
    assert re.search(r"ARG SEEDVC_REF=[0-9a-f]{40}\n", docker) and re.search(r"ARG MSST_REF=[0-9a-f]{40}\n", docker)
    assert "COPY nodes/slq_audio /opt/ComfyUI/custom_nodes/slq_audio" in docker
    assert "test -f /opt/seed-vc/campplus_cn_common.bin" in docker
    common = (NODES / "common.py").read_text()
    assert '"SLQ_SEEDVC_DIR", "/opt/seed-vc"' in common and '"SLQ_MSST_DIR", "/opt/msst"' in common
