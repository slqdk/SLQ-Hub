"""0.19.0 - video: Wan 2.2 through ComfyUI and the scheduler - from a description, from a picture, from a first to a
last picture; the catalog, the tool and when it is offered, the graphs it queues (checked against the official
templates' settings), what it saves and says, a person's turn, and the Hugging Face dialog pointing to the Hub's own
models. The graphs themselves ran in a CPU ComfyUI v0.42.3 with tiny stand-in models (docs/HANDOFF.md)."""
from __future__ import annotations

import base64
import io
import json
import re

from PIL import Image

from app import hf
from app.video import workflows as W
from app.video.pipeline import one_bar, size_for

from .mocks import fake_mp4
from .test_agent import finish, new_chat, say
from .test_audio import chat_dir, install, system, tool_result, tools_offered
from .test_people import add_person, client_from, sign_in, turn, user_msg

UMT5 = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"


def picture(size=(640, 480), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (40, 120, 200)).save(buf, fmt)
    return buf.getvalue()


def put(app, chat_id: int, name: str, data: bytes):
    d = chat_dir(app, chat_id)
    (d / name).parent.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)


def nodes(graph: dict, class_type: str) -> list[dict]:
    return [n["inputs"] for n in graph.values() if n["class_type"] == class_type]


def one(graph: dict, class_type: str) -> dict:
    found = nodes(graph, class_type)
    assert len(found) == 1, (class_type, found)
    return found[0]


async def call(app, client, chat_id: int, fakes, args: dict, text: str = "Make a video") -> dict:
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": "video_generate", "arguments": args}]}, {"content": "Done."},
                              {"content": "Title"}]
    return tool_result(await finish(app, await say(client, chat_id, text)))


def catalog(app) -> dict:
    return {e["id"]: e for e in app.state.hub.catalog.entries("video")}


# ---------------------------------------------------------------- catalog and offer

async def test_the_video_models_are_in_the_catalog_and_download(signed_in, app):
    r = await signed_in.get("/hub/api/models")
    video = {m["id"]: m for m in r.json()["video"]}
    assert list(video) == ["wan2.2-5b-turbo", "wan2.2-5b", "wan2.2-i2v-14b", "wan2.2-t2v-14b"]   # the fast one first
    assert not any(m["installed"] for m in video.values())
    assert {m["engine"] for m in video.values()} == {"comfy"}
    assert video["wan2.2-5b"]["abilities"] == video["wan2.2-5b-turbo"]["abilities"] == ["t2v", "i2v"]
    assert video["wan2.2-i2v-14b"]["abilities"] == ["i2v", "flf2v"] and video["wan2.2-t2v-14b"]["abilities"] == ["t2v"]
    for m in video.values():
        assert m["description"].endswith("Apache 2.0 licence." if m["id"] != "wan2.2-5b-turbo" else
                                          "Turbo: CC BY-NC-SA 4.0, not for commercial use; Wan 2.2: Apache 2.0.")
        for f in m["files"]:
            assert f["dest"].startswith("comfy/") and f["size"] > 0 and f["repo"] and f["path"] and f["role"], f
            assert f["path"].rsplit("/", 1)[-1] == f["dest"].rsplit("/", 1)[-1]
            assert f["path"].startswith("split_files/") or f["repo"] == "Kijai/WanVideo_comfy"
    total = {k: sum(f["size"] for f in m["files"]) / 1e9 for k, m in video.items()}
    assert round(total["wan2.2-5b"], 1) == 18.1 and round(total["wan2.2-i2v-14b"], 1) == 38.0      # the templates'
    # the 14B pair shares the text encoder and the VAE, which download once
    r = await signed_in.post("/hub/api/models/wan2.2-i2v-14b/download")
    assert r.status_code == 200 and len(r.json()["queued"]) == 6
    r = await signed_in.post("/hub/api/models/wan2.2-t2v-14b/download")
    assert r.status_code == 200 and len(r.json()["queued"]) == 4
    assert not any(UMT5 in q or "vae" in q for q in r.json()["queued"])
    for m in ("wan2.2-i2v-14b", "wan2.2-t2v-14b"):
        app.state.hub.extras["downloads"].cancel(m)
    # the Turbo is the 5B's files and its LoRA: with the 5B there, only the LoRA downloads
    install(app, "wan2.2-5b")
    r = await signed_in.post("/hub/api/models/wan2.2-5b-turbo/download")
    assert r.json()["queued"] == ["comfy/loras/Wan22_TI2V_5B_Turbo_lora_rank_64_fp16.safetensors"]
    app.state.hub.extras["downloads"].cancel("wan2.2-5b-turbo")


async def test_no_video_tool_until_a_model_is_downloaded(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "Hi."}, {"content": "Hi"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hello"))
    assert "video_generate" not in tools_offered(fakes, 0) and "# Video" not in system(fakes, 0)
    install(app, "wan2.2-5b", "wan2.2-i2v-14b")
    fakes.llama["requests"].clear()
    await finish(app, await say(signed_in, c["id"], "Hello again"))
    tool = tools_offered(fakes, 0)["video_generate"]
    assert tool["parameters"]["properties"]["model"]["enum"] == ["wan2.2-5b", "wan2.2-i2v-14b"]
    desc = tool["description"]
    assert "- wan2.2-5b: Wan 2.2 5B - video from a description, video from a picture; 1280x704 at 24 fps by default" \
        in desc
    assert "- wan2.2-i2v-14b: Wan 2.2 14B picture to video - video from a picture, video from a first and a last " \
           "picture; 832x480 at 16 fps by default" in desc
    assert "Without model: from a description: Wan 2.2 5B; from a picture: Wan 2.2 5B - pick another only when " \
           "the user asks for it" in desc
    assert "describe the motion and the camera" in desc and "-map 0:v -map 1:a" in desc
    assert "none of the downloaded models makes one" not in desc            # the 14B picture to video does
    assert "# Video\n- video_generate makes a short video" in system(fakes, 0)


# ---------------------------------------------------------------- Wan 2.2 5B

async def test_a_video_from_a_description_with_wan_5b(signed_in, app, fakes):
    install(app, "wan2.2-5b")
    c = await new_chat(signed_in)
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "A red fox runs through snow", "seed": 7,
                                                      "negative": "people"})
    assert res["ok"], res
    g = fakes.comfy["prompts"][0]
    assert one(g, "UNETLoader")["unet_name"] == "wan2.2_ti2v_5B_fp16.safetensors"
    assert one(g, "ModelSamplingSD3")["shift"] == 8.0
    assert (one(g, "CLIPLoader")["clip_name"], one(g, "CLIPLoader")["type"]) == (UMT5, "wan")
    assert one(g, "VAELoader")["vae_name"] == "wan2.2_vae.safetensors"
    latent = one(g, "Wan22ImageToVideoLatent")
    assert (latent["width"], latent["height"], latent["length"]) == (1280, 704, 121) and "start_image" not in latent
    k = one(g, "KSampler")
    assert (k["steps"], k["cfg"], k["sampler_name"], k["scheduler"], k["seed"]) == (20, 5.0, "uni_pc", "simple", 7)
    texts = [n["text"] for n in nodes(g, "CLIPTextEncode")]
    assert texts == ["A red fox runs through snow", W.WAN_NEGATIVE + "，people"]
    assert one(g, "CreateVideo")["fps"] == 24.0
    assert one(g, "SaveVideo") == {"video": ["11", 0], "filename_prefix": "video/hub", "format": "mp4",
                                   "format.codec": "h264"}
    assert "LoadImage" not in W.class_types(g)
    assert res["text"].startswith("Saved a-red-fox-runs-through-snow.mp4: 5.04 s, 121 frames at 24 fps, 1280x704, "
                                  "Wan 2.2 5B, seed 7 (")
    assert "It has no sound. The user can play it under your answer." in res["text"]
    assert res["display"]["video"] == [{"path": "a-red-fox-runs-through-snow.mp4", "seconds": 5.04,
                                        "model": "Wan 2.2 5B"}]
    assert (chat_dir(app, c["id"]) / "a-red-fox-runs-through-snow.mp4").read_bytes() == fake_mp4()
    starts = [s["name"] for s in fakes.sup["starts"]]
    assert starts.count("main") == 2                                   # loaded, ComfyUI, loaded again
    # the next one gets a free name; seconds are capped at 10 and say so
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "A red fox runs through snow", "seconds": 30,
                                                      "hd": False})
    assert res["ok"] and res["text"].startswith("Saved a-red-fox-runs-through-snow-2.mp4: 10.04 s (seconds is at "
                                                "most 10), 241 frames at 24 fps, 832x480"), res
    assert one(fakes.comfy["prompts"][1], "Wan22ImageToVideoLatent")["length"] == 241


async def test_a_video_from_a_picture_keeps_its_shape(signed_in, app, fakes, tmp_path):
    hub = app.state.hub
    hub.comfy.files = fakes.comfy["files"] = tmp_path / "comfy"
    install(app, "wan2.2-5b")
    c = await new_chat(signed_in)
    put(app, c["id"], "photos/me.jpg", picture((600, 900), "JPEG"))
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "she smiles, the wind moves her hair, slow push-in",
                                                      "image": "photos/me.jpg", "orientation": "landscape"})
    assert res["ok"], res
    up = fakes.comfy["uploads"][0]
    assert re.fullmatch(r"hub-\d+-start\.png", up)
    g = fakes.comfy["prompts"][0]
    assert g["7"] == {"class_type": "LoadImage", "inputs": {"image": up}}
    latent = one(g, "Wan22ImageToVideoLatent")
    assert latent["start_image"] == ["7", 0]
    assert (latent["width"], latent["height"]) == (768, 1152)          # the picture's shape, not landscape
    assert "Saved she-smiles-the-wind-moves-her-hair-slow-push-in.mp4 from photos/me.jpg: 5.04 s" in res["text"]
    assert not any((tmp_path / "comfy" / "input").iterdir())           # the picture left ComfyUI's input folder
    # what is not a picture is said so, before anything is queued
    put(app, c["id"], "notes.png", b"not a picture at all")
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "x", "image": "notes.png"})
    assert not res["ok"] and "the picture could not be read" in res["text"]
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "x", "image": "nope.png"})
    assert not res["ok"] and "No such picture in the working folder: nope.png" in res["text"]
    assert len(fakes.comfy["prompts"]) == 1


# ---------------------------------------------------------------- Wan 2.2 14B

async def test_wan_14b_from_a_picture_fast_full_and_to_a_last_picture(signed_in, app, fakes, tmp_path):
    hub = app.state.hub
    hub.comfy.files = fakes.comfy["files"] = tmp_path / "comfy"
    install(app, "wan2.2-i2v-14b")
    c = await new_chat(signed_in)
    put(app, c["id"], "start.png", picture((1024, 1024)))
    put(app, c["id"], "end.jpg", picture((800, 800), "JPEG"))
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "the dragon turns its head", "image": "start.png",
                                                      "seed": 11})
    assert res["ok"], res
    g = fakes.comfy["prompts"][0]
    assert [n["unet_name"] for n in nodes(g, "UNETLoader")] == ["wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                                                                 "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"]
    assert [(n["model"], n["lora_name"], n["strength_model"]) for n in nodes(g, "LoraLoaderModelOnly")] == [
        (["1", 0], "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", 1.0),
        (["2", 0], "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", 1.0)]
    assert [(n["model"], n["shift"]) for n in nodes(g, "ModelSamplingSD3")] == [(["3", 0], 5.0), (["4", 0], 5.0)]
    assert one(g, "VAELoader")["vae_name"] == "wan_2.1_vae.safetensors"
    i2v = one(g, "WanImageToVideo")
    assert (i2v["width"], i2v["height"], i2v["length"], i2v["start_image"]) == (624, 624, 81, ["11", 0])
    high, low = nodes(g, "KSamplerAdvanced")
    assert (high["model"], high["add_noise"], high["noise_seed"], high["steps"], high["cfg"], high["start_at_step"],
            high["end_at_step"], high["return_with_leftover_noise"], high["latent_image"]) == \
        (["5", 0], "enable", 11, 4, 1.0, 0, 2, "enable", ["13", 2])
    assert (low["model"], low["add_noise"], low["start_at_step"], low["end_at_step"], low["return_with_leftover_noise"],
            low["latent_image"], low["positive"], low["negative"]) == \
        (["6", 0], "disable", 2, 10000, "disable", ["14", 0], ["13", 0], ["13", 1])
    assert high["sampler_name"] == low["sampler_name"] == "euler" and one(g, "CreateVideo")["fps"] == 16.0
    assert "Saved the-dragon-turns-its-head.mp4 from start.png: 5.06 s, 81 frames at 16 fps, 624x624, Wan 2.2 14B " \
           "picture to video, fast quality, seed 11" in res["text"]
    # full quality: no LoRAs, 20 steps split at 10, cfg 3.5
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "the dragon turns its head", "image": "start.png",
                                                      "quality": "full"})
    g = fakes.comfy["prompts"][1]
    assert "LoraLoaderModelOnly" not in W.class_types(g)
    assert [n["model"] for n in nodes(g, "ModelSamplingSD3")] == [["1", 0], ["2", 0]]
    high, low = nodes(g, "KSamplerAdvanced")
    assert (high["steps"], high["end_at_step"], high["cfg"], low["start_at_step"]) == (20, 10, 3.5, 10)
    assert "full quality" in res["text"]
    # first to last picture, full quality: the template's shift 8 and cfg 4
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "day turns to night", "image": "start.png",
                                                      "last_image": "end.jpg", "quality": "full"})
    assert res["ok"], res
    g = fakes.comfy["prompts"][2]
    start, end = fakes.comfy["uploads"][-2:]
    assert start.endswith("-start.png") and end.endswith("-end.png")
    flf = one(g, "WanFirstLastFrameToVideo")
    assert (flf["start_image"], flf["end_image"]) == (["11", 0], ["12", 0])
    assert g["11"]["inputs"]["image"] == start and g["12"]["inputs"]["image"] == end
    assert [n["shift"] for n in nodes(g, "ModelSamplingSD3")] == [8.0, 8.0]
    assert {n["cfg"] for n in nodes(g, "KSamplerAdvanced")} == {4.0}
    assert "from start.png to end.jpg" in res["text"]
    assert not any((tmp_path / "comfy" / "input").iterdir())
    # a last picture needs a first one
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "x", "last_image": "end.jpg"})
    assert not res["ok"] and "last_image needs image too" in res["text"]
    assert len(fakes.comfy["prompts"]) == 3


async def test_wan_14b_from_a_description_and_the_settings_choose_the_model(signed_in, app, fakes):
    install(app, "wan2.2-5b", "wan2.2-t2v-14b")
    r = await signed_in.put("/hub/api/settings", json={"section": "video", "values": {"t2v_model": "wan2.2-t2v-14b"}})
    assert r.status_code == 200, r.text
    p = (await signed_in.post("/hub/api/projects", json={"name": "Club"})).json()
    c = await new_chat(signed_in, project_id=p["id"])
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "fireworks over Copenhagen harbour",
                                                      "orientation": "portrait"})
    assert res["ok"], res
    g = fakes.comfy["prompts"][0]
    assert [n["unet_name"] for n in nodes(g, "UNETLoader")] == ["wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
                                                                 "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"]
    assert [n["lora_name"] for n in nodes(g, "LoraLoaderModelOnly")] == [
        "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors"]
    empty = one(g, "EmptyHunyuanLatentVideo")
    assert (empty["width"], empty["height"], empty["length"]) == (480, 832, 81)
    high, low = nodes(g, "KSamplerAdvanced")
    assert (high["positive"], high["negative"], high["latent_image"]) == (["9", 0], ["10", 0], ["13", 0])
    assert res["text"].startswith("Saved video/fireworks-over-copenhagen-harbour.mp4: 5.06 s")
    folder = app.state.hub.env.workspace_dir / "projects" / str(p["id"])
    assert (folder / "video" / "fireworks-over-copenhagen-harbour.mp4").is_file()
    # hd, a named model, save_as
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "fireworks", "hd": True, "model": "wan2.2-5b",
                                                      "save_as": "clips/finale"})
    assert res["ok"] and res["text"].startswith("Saved clips/finale.mp4: 5.04 s, 121 frames at 24 fps, 1280x704, "
                                                "Wan 2.2 5B"), res
    assert (folder / "clips" / "finale.mp4").is_file()
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "fireworks", "save_as": "finale.gif"})
    assert not res["ok"] and "save_as must end in .mp4" in res["text"]
    # what the downloaded models cannot do is said so
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "x", "model": "wan2.2-i2v-14b"})
    assert not res["ok"] and "Wan 2.2 14B picture to video does not make video from a description; use one of " \
                             "wan2.2-5b, wan2.2-t2v-14b" in res["text"]
    put_project = folder / "a.png"
    put_project.write_bytes(picture())
    (folder / "b.png").write_bytes(picture())
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "x", "image": "a.png", "last_image": "b.png"})
    assert not res["ok"] and "no downloaded video model makes video from a first and a last picture: Wan 2.2 5B, " \
                             "Wan 2.2 14B text to video cannot" in res["text"]
    assert len(fakes.comfy["prompts"]) == 2
    # 0.28.1 (Søren deleted the 14B picture to video): the description says so before anything is tried
    assert "\n- A video from a first to a last picture: none of the downloaded models makes one (Wan 2.2 14B " \
           "picture to video would - Settings -> Models -> Video). Tell the user instead of trying.\n" in \
           tools_offered(fakes, 0)["video_generate"]["description"]


# ---------------------------------------------------------------- Wan 2.2 5B Turbo (0.25.0)

async def test_wan_5b_turbo_is_the_fast_default_and_full_quality_is_the_5b(signed_in, app, fakes, tmp_path):
    install(app, "wan2.2-5b-turbo")                       # its files are the 5B's and the LoRA: both are there
    c = await new_chat(signed_in)
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "A red fox runs through snow", "seed": 7})
    assert res["ok"], res
    tool = tools_offered(fakes, 0)["video_generate"]
    assert tool["parameters"]["properties"]["model"]["enum"] == ["wan2.2-5b-turbo", "wan2.2-5b"]
    assert "Wan 2.2 5B Turbo: Wan 2.2 5B's 20 steps instead of 4" in tool["parameters"]["properties"]["quality"][
        "description"]
    assert "Without model: from a description: Wan 2.2 5B Turbo; from a picture: Wan 2.2 5B Turbo" in \
        tool["description"]
    g = fakes.comfy["prompts"][0]
    assert one(g, "UNETLoader")["unet_name"] == "wan2.2_ti2v_5B_fp16.safetensors"
    assert one(g, "LoraLoaderModelOnly") == {"model": ["1", 0], "lora_name":
                                             "Wan22_TI2V_5B_Turbo_lora_rank_64_fp16.safetensors", "strength_model": 1.0}
    assert one(g, "ModelSamplingSD3") == {"model": ["13", 0], "shift": 5.0}         # the LoRA, then the sampling
    k = one(g, "KSampler")
    assert (k["model"], k["steps"], k["cfg"], k["sampler_name"], k["scheduler"]) == (["2", 0], 4, 1.0, "uni_pc_bh2",
                                                                                     "simple")
    latent = one(g, "Wan22ImageToVideoLatent")
    assert (latent["width"], latent["height"], latent["length"]) == (1280, 704, 121)
    assert res["text"].startswith("Saved a-red-fox-runs-through-snow.mp4: 5.04 s, 121 frames at 24 fps, 1280x704, "
                                  "Wan 2.2 5B Turbo, seed 7 (")
    assert res["display"]["video"][0]["model"] == "Wan 2.2 5B Turbo"
    # from a picture too
    put(app, c["id"], "fox.png", picture((1024, 768)))
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "the fox turns its head", "image": "fox.png"})
    assert res["ok"] and "Wan 2.2 5B Turbo" in res["text"], res
    g = fakes.comfy["prompts"][1]
    assert one(g, "LoraLoaderModelOnly")["lora_name"] == "Wan22_TI2V_5B_Turbo_lora_rank_64_fp16.safetensors"
    assert "start_image" in one(g, "Wan22ImageToVideoLatent") and one(g, "KSampler")["steps"] == 4
    # full quality: the 5B's own 20 steps, no LoRA
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "A red fox runs through snow", "quality": "full"},
                     text="The same in the best quality")
    assert res["ok"] and "1280x704, Wan 2.2 5B, seed" in res["text"], res
    g = fakes.comfy["prompts"][2]
    assert "LoraLoaderModelOnly" not in W.class_types(g)
    k = one(g, "KSampler")
    assert (k["steps"], k["cfg"], k["sampler_name"]) == (20, 5.0, "uni_pc") and one(g, "ModelSamplingSD3")["shift"] == 8.0
    # the owner's choice of the 5B stands: the default quality does not make it the Turbo
    await signed_in.put("/hub/api/settings", json={"section": "video", "values": {"t2v_model": "wan2.2-5b"}})
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "A red fox runs through snow"})
    assert res["ok"] and "Wan 2.2 5B, seed" in res["text"] and "LoraLoaderModelOnly" not in W.class_types(
        fakes.comfy["prompts"][3])


def test_the_turbo_graph_names_its_lora_and_loads_it_before_the_sampling():
    g = W.wan_5b(prompt="p", seconds=5, width=1280, height=704, seed=1, unet="u.safetensors", text_encoder=UMT5,
                 vae="v.safetensors", steps=4, cfg=1.0, shift=5.0, sampler="uni_pc_bh2", lora="turbo.safetensors",
                 lora_strength=0.8)
    assert g["13"] == {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "turbo.safetensors",
                                                                        "strength_model": 0.8}}
    assert g["2"]["inputs"]["model"] == ["13", 0] and g["9"]["inputs"]["model"] == ["2", 0]
    plain = W.wan_5b(prompt="p", seconds=5, width=1280, height=704, seed=1, unet="u.safetensors", text_encoder=UMT5,
                     vae="v.safetensors")
    assert "13" not in plain and plain["2"]["inputs"]["model"] == ["1", 0]


# ---------------------------------------------------------------- people and errors

async def test_a_persons_video_goes_to_her_device_and_comfyui_keeps_nothing(signed_in, app, fakes, tmp_path,
                                                                            monkeypatch):
    hub = app.state.hub
    hub.comfy.files = fakes.comfy["files"] = tmp_path / "comfy"
    install(app, "wan2.2-5b")
    await add_person(signed_in)
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "video_generate", "arguments": {"prompt": "the dog wags its tail",
                                                                 "image": ".hub/attachments/dog.png"}}]},
        {"content": "Here it is."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Dog", "messages": [user_msg("Make my dog move")],
                                      "files": [{"path": ".hub/attachments/dog.png", "mime": "image/png",
                                                 "data": base64.b64encode(picture()).decode()}]})
        assert res["status"] == "done", res
        assert {f["path"] for f in res["files"]} == {"the-dog-wags-its-tail.mp4"}
        r = await phone.get(f"/hub/api/device/turns/{res['id']}/file", params={"path": "the-dog-wags-its-tail.mp4"})
        assert r.content == fake_mp4()
        await phone.post(f"/hub/api/device/turns/{res['id']}/done", headers={"Origin": "http://hub"})
    assert fakes.comfy["history_deleted"] == ["p1"]                     # the prompt and the file name
    assert not any((tmp_path / "comfy").rglob("*.mp4"))                 # the video in its output folder
    assert not any((tmp_path / "comfy").rglob("*.png"))                 # and her picture in its input folder
    # a video too big to reach her device is said so
    from app.agent import device
    monkeypatch.setattr(device, "MAX_OUT_FILE", 1024)
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": "video_generate", "arguments": {"prompt": "rain"}}]},
                              {"content": "Here."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Rain", "messages": [user_msg("Rain")]})
    results = json.dumps([b for m in res["messages"] for b in m["content"] if b["type"] == "tool_result"])
    assert "a file may have to reach the user's device in this chat, so it will not get there" in results


async def test_video_errors_are_reported(signed_in, app, fakes):
    install(app, "wan2.2-i2v-14b", "wan2.2-t2v-14b")
    c = await new_chat(signed_in)
    res = await call(app, signed_in, c["id"], fakes, {"prompt": " "})
    assert not res["ok"] and "prompt is required" in res["text"]
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "x", "quality": "best"})
    assert not res["ok"] and "quality must be fast or full" in res["text"]
    assert not fakes.comfy["prompts"]
    fakes.comfy["missing_nodes"] = ["EmptyHunyuanLatentVideo"]
    app.state.hub.comfy._object_info = None
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "x"})
    assert not res["ok"] and "ComfyUI does not know these nodes: EmptyHunyuanLatentVideo - update ComfyUI" in \
        res["text"]
    fakes.comfy["missing_nodes"] = []
    app.state.hub.comfy._object_info = None
    fakes.comfy["no_video"] = True
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "x"})
    assert not res["ok"] and "the workflow finished without a video" in res["text"]
    fakes.comfy["no_video"] = False
    fakes.comfy["fail_with"], fakes.comfy["fail_node"] = "HIP out of memory", "KSamplerAdvanced"
    res = await call(app, signed_in, c["id"], fakes, {"prompt": "x"})
    assert not res["ok"] and "KSamplerAdvanced: HIP out of memory" in res["text"]
    assert not list(chat_dir(app, c["id"]).glob("*.mp4"))


# ---------------------------------------------------------------- pieces

def test_frames_and_sizes(app):
    assert [W.frames(s, f) for s, f in ((5, 16), (5, 24), (10, 16), (0.1, 16), (3, 16))] == [81, 121, 161, 5, 49]
    e = catalog(app)
    five, big = e["wan2.2-5b"], e["wan2.2-i2v-14b"]
    assert size_for(five) == (1280, 704) and size_for(five, hd=False) == (832, 480)
    assert size_for(five, "portrait") == (704, 1280) and size_for(five, "square") == (960, 960)
    assert size_for(big) == (832, 480) and size_for(big, hd=True) == (1280, 720)
    assert size_for(big, picture=(1920, 1080)) == (848, 480)
    assert size_for(five, picture=(4000, 500)) == (1504, 608)          # a panorama is held at 2.5:1
    for w, h in (size_for(five, "square"), size_for(five, picture=(4000, 500))):
        assert w % 32 == 0 and h % 32 == 0
    assert W.negative("") == W.WAN_NEGATIVE
    try:
        W.wan_14b(prompt="x", seconds=5, width=64, height=64, seed=1, high="h", low="l", text_encoder="t", vae="v",
                  end_image="e.png")
        raise AssertionError("an end picture without a start picture was taken")
    except ValueError:
        pass


async def test_the_14b_pair_shows_one_progress_bar():
    """What the CPU ComfyUI v0.42.3 sent for a 4-step 14B job (split at 2): each expert's sampler counts its own
    steps; the tool row shows them as one bar."""
    seen = []
    bar = one_bar(lambda value, maximum, node: seen.append((value, maximum)), 2, 4)
    for value, maximum, node in [(1, 2, "14"), (2, 2, "14"), (1, 2, "15"), (2, 2, "15")]:
        await bar(value, maximum, node)
    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert one_bar(None, 2, 4) is None


async def test_the_hugging_face_dialog_points_to_the_hubs_own_models(signed_in, app, monkeypatch):
    """Søren searched "Seed-VC" in the add dialog (2026-10-24), which finds only chat models on Hugging Face: the
    Hub's own list has it under Sound, music and speech."""
    r = await signed_in.get("/hub/api/models/hf/search", params={"q": "Seed-VC"})
    assert r.status_code == 200, r.text
    assert r.json()["results"] == []
    assert r.json()["in_hub"] == [{"id": "seed-vc", "name": "Seed-VC", "group": "audio",
                                   "section": "Sound, music and speech", "installed": False}]
    r = await signed_in.get("/hub/api/models/hf/search", params={"q": "wan 2.2"})
    assert [m["id"] for m in r.json()["in_hub"]] == ["wan2.2-5b-turbo", "wan2.2-5b", "wan2.2-i2v-14b",
                                                     "wan2.2-t2v-14b"]
    assert {m["section"] for m in r.json()["in_hub"]} == {"Video models"}
    assert (await signed_in.get("/hub/api/models/hf/search", params={"q": "zz"})).json()["in_hub"] == []

    async def down(*a, **kw):
        raise hf.HFError("Could not reach Hugging Face (ConnectError).", 502)
    monkeypatch.setattr(hf, "search", down)
    r = await signed_in.get("/hub/api/models/hf/search", params={"q": "seedvc"})
    assert r.status_code == 200 and r.json()["in_hub"][0]["id"] == "seed-vc"     # still pointed there
    r = await signed_in.get("/hub/api/models/hf/search", params={"q": "nothing like it"})
    assert r.status_code == 502
