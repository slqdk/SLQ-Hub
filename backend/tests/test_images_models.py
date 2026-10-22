"""More picture models: Z-Image Turbo and Chroma1-HD from the catalog, and
SDXL checkpoints added from a Hugging Face or Civitai link (lookup, the
checkpoint check, presets, download, settings, removal, use in a chat)."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.images import sources
from app.images import workflows as W

from .test_agent import finish, new_chat, say


def _node(g: dict, cls: str) -> list[dict]:
    return [n for n in g.values() if n["class_type"] == cls]


def sdxl_header(clip: bool = True, vae: bool = True, vpred: bool = False) -> dict:
    t = {"dtype": "F16", "shape": [4], "data_offsets": [0, 8]}
    h = {"__metadata__": {"modelspec.architecture": "stable-diffusion-xl-v1-base"},
         "model.diffusion_model.input_blocks.0.0.weight": {**t, "shape": [320, 4, 3, 3]},
         "model.diffusion_model.label_emb.0.0.weight": {**t, "shape": [1280, 2816]}}
    if clip:
        h["conditioner.embedders.0.transformer.text_model.final_layer_norm.weight"] = t
        h["conditioner.embedders.1.model.ln_final.weight"] = t
    if vae:
        h["first_stage_model.decoder.conv_in.weight"] = t
    if vpred:
        h["v_pred"] = {"dtype": "F32", "shape": [], "data_offsets": [0, 0]}
    return h


SD15_HEADER = {"cond_stage_model.transformer.text_model.final_layer_norm.weight": {"shape": [768]},
               "model.diffusion_model.input_blocks.0.0.weight": {"shape": [320, 4, 3, 3]},
               "first_stage_model.decoder.conv_in.weight": {"shape": [512, 4, 3, 3]}}
GB = 1_000_000_000


def _f(path: str, size: int, header: dict | None = None) -> dict:
    d = {"type": "file", "oid": "x", "size": size, "path": path}
    if header is not None:
        d["header"] = header
    return d


def _repos(fakes) -> None:
    fakes.hf["tiny_downloads"] = True
    fakes.hf.setdefault("repos", {}).update({
        "someone/pony-mirror": {"tree": [_f("ponyDiffusionV6XL.safetensors", int(6.9 * GB), sdxl_header()),
                                         _f("old/sd15-model.safetensors", 2 * GB, SD15_HEADER),
                                         _f("README.md", 900)]},
        "someone/noob-vpred": {"tree": [_f("noobai-vpred.safetensors", int(6.9 * GB), sdxl_header(vpred=True))]},
        "someone/diffusers-only": {"tree": [_f("model_index.json", 500),
                                            _f("unet/diffusion_pytorch_model.safetensors", int(5.1 * GB))]},
    })


def _civitai(fakes, token: str | None = None) -> None:
    def version(vid: int, name: str, base: str, kb: float = 6_775_430.0, ftype: str = "Model") -> dict:
        return {"id": vid, "name": name, "baseModel": base, "files": [
            {"id": vid * 10, "name": f"{name}.safetensors", "sizeKB": kb, "type": ftype, "primary": True,
             "metadata": {"format": "SafeTensor", "size": "pruned", "fp": "fp16"},
             "hashes": {"SHA256": "67AB2FD8EC439A89B3FEDB15CC65F54336AF163C7EB5E4F2ACC98F090A29B0B3"},
             "downloadUrl": f"https://civitai.com/api/download/models/{vid}?fileId={vid * 10}"},
            {"id": vid * 10 + 1, "name": "vae.safetensors", "sizeKB": 326_000.0, "type": "VAE",
             "metadata": {"format": "SafeTensor"}, "downloadUrl": f"https://civitai.com/api/download/models/{vid}?type=VAE"}]}
    fakes.civitai["models"] = {
        257749: {"id": 257749, "name": "Pony Diffusion V6 XL", "type": "Checkpoint", "nsfw": True,
                 "creator": {"username": "PurpleSmartAI"},
                 "modelVersions": [version(290640, "V6 (start with this one)", "Pony"),
                                   version(290641, "Flux port", "Flux.1 D")]},
        111: {"id": 111, "name": "A style LoRA", "type": "LORA", "nsfw": False,
              "modelVersions": [version(1110, "v1", "SDXL 1.0", kb=200_000.0)]},
    }
    if token:
        fakes.civitai["token"] = token


async def _drain(hub, timeout: float = 10.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if not hub.extras["downloads"].snapshot()["active"]:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("downloads did not finish")


def _install(hub, model_id: str) -> None:
    for f in hub.catalog.get("comfy", model_id)["files"]:
        p = hub.env.models_dir / f["dest"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake")


# ---------------------------------------------------------------- graphs

def test_the_graphs_follow_the_official_templates():
    g = W.zimage_t2i("a fox", 1000, 760, seed=3)
    assert _node(g, "CLIPLoader")[0]["inputs"] == {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2",
                                                   "device": "default"}
    assert _node(g, "ModelSamplingAuraFlow")[0]["inputs"]["shift"] == 3.0
    k = _node(g, "KSampler")[0]["inputs"]
    assert (k["steps"], k["cfg"], k["sampler_name"], k["scheduler"]) == (8, 1.0, "res_multistep", "simple")
    assert k["model"] == ["2", 0] and _node(g, "ConditioningZeroOut")
    assert _node(g, "EmptySD3LatentImage")[0]["inputs"] == {"width": 992, "height": 768, "batch_size": 1}

    g = W.chroma_t2i("a fox", 1024, 1024, seed=4)
    assert _node(g, "CLIPLoader")[0]["inputs"]["type"] == "chroma"
    assert _node(g, "T5TokenizerOptions")[0]["inputs"] == {"clip": ["3", 0], "min_padding": 0, "min_length": 0}
    assert _node(g, "ModelSamplingAuraFlow")[0]["inputs"]["shift"] == 1.0
    assert _node(g, "BasicScheduler")[0]["inputs"] == {"model": ["2", 0], "scheduler": "beta", "steps": 26,
                                                       "denoise": 1.0}
    assert _node(g, "CFGGuider")[0]["inputs"]["cfg"] == 3.5
    texts = [n["inputs"]["text"] for n in _node(g, "CLIPTextEncode")]
    assert texts == ["a fox", W.CHROMA_NEGATIVE]
    g = W.chroma_t2i("a fox", 1024, 1024, seed=4, negative="")
    assert [n["inputs"]["text"] for n in _node(g, "CLIPTextEncode")] == ["a fox", ""]

    g = W.sdxl_t2i("hub/pony.safetensors", "1girl, smile", 1920, 1080, seed=5, steps=25, cfg=7.0,
                   sampler="euler_ancestral", scheduler="normal", clip_skip=2,
                   prompt_prefix="score_9, score_8_up,", negative="lowres")
    assert _node(g, "CheckpointLoaderSimple")[0]["inputs"] == {"ckpt_name": "hub/pony.safetensors"}
    assert _node(g, "CLIPSetLastLayer")[0]["inputs"] == {"clip": ["1", 1], "stop_at_clip_layer": -2}
    assert [n["inputs"]["text"] for n in _node(g, "CLIPTextEncode")] == ["score_9, score_8_up, 1girl, smile", "lowres"]
    assert all(n["inputs"]["clip"] == ["2", 0] for n in _node(g, "CLIPTextEncode"))
    assert _node(g, "EmptyLatentImage")[0]["inputs"] == {"width": 1344, "height": 768, "batch_size": 1}
    assert _node(g, "VAEDecode")[0]["inputs"]["vae"] == ["1", 2]
    g = W.sdxl_t2i("hub/x.safetensors", "a fox", 1024, 1024, seed=6)
    assert not _node(g, "CLIPSetLastLayer") and _node(g, "CLIPTextEncode")[0]["inputs"]["text"] == "a fox"
    assert W.sdxl_size(512, 2048) == (640, 1536) and W.sdxl_size(1000, 1000) == (1024, 1024)


# ---------------------------------------------------------------- links, checkpoints, settings

def test_links_and_checkpoints():
    assert sources.parse_link("https://huggingface.co/someone/pony-mirror") == \
        {"site": "hf", "repo": "someone/pony-mirror", "file": None}
    assert sources.parse_link("https://huggingface.co/someone/m/blob/main/sub/model.safetensors?x=1")["file"] == \
        "sub/model.safetensors"
    assert sources.parse_link("someone/pony-mirror")["repo"] == "someone/pony-mirror"
    assert sources.parse_link("https://civitai.com/models/257749/pony-diffusion-v6-xl?modelVersionId=290640") == \
        {"site": "civitai", "model_id": 257749, "version_id": 290640}
    assert sources.parse_link("civitai.com/api/download/models/290640") == \
        {"site": "civitai", "model_id": None, "version_id": 290640}
    assert sources.parse_link("https://civitai.green/models/12")["model_id"] == 12
    for bad in ("", "https://example.com/model.safetensors", "https://civitai.com/user/someone"):
        with pytest.raises(sources.SourceError):
            sources.parse_link(bad)

    assert sources.classify(sdxl_header()) == (True, "")
    assert sources.classify(sdxl_header(vpred=True)) == (True, "v-prediction")
    assert "text encoders" in sources.classify(sdxl_header(clip=False))[1]
    assert "VAE" in sources.classify(sdxl_header(vae=False))[1]
    assert "1.x or 2.x" in sources.classify(SD15_HEADER)[1]
    assert "FLUX" in sources.classify({"double_blocks.0.img_attn.qkv.weight": {}})[1]
    assert "LoRA" in sources.classify({"lora_unet_down_blocks_0.alpha": {}})[1]

    assert sources.preset_for("Pony") == "pony" and sources.preset_for("NoobAI") == "illustrious"
    assert sources.preset_for("SDXL 1.0") == "sdxl"
    s = sources.clean_settings({"preset": "pony"})
    assert s["clip_skip"] == 2 and s["sampler"] == "euler_ancestral" and s["prefix"].startswith("score_9")
    assert sources.clean_settings({"preset": "illustrious", "cfg": "4.5", "steps": 30})["cfg"] == 4.5
    for bad in ({"preset": "sd15"}, {"sampler": "magic"}, {"steps": 0}, {"cfg": 99}, {"clip_skip": 3},
                {"steps": "many"}):
        with pytest.raises(sources.SourceError):
            sources.clean_settings(bad)


# ---------------------------------------------------------------- the API

async def test_sdxl_from_hugging_face(signed_in, app, fakes):
    hub = app.state.hub
    _repos(fakes)
    r = await signed_in.get("/hub/api/models/images/lookup", params={"link": "https://huggingface.co/someone/pony-mirror"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert [o["key"] for o in res["options"]] == ["old/sd15-model.safetensors", "ponyDiffusionV6XL.safetensors"]
    assert res["pick"] is None and all(o["supported"] is None for o in res["options"])     # two files: you choose
    assert res["presets"]["pony"]["clip_skip"] == 2 and "euler_ancestral" in res["samplers"]
    r = await signed_in.get("/hub/api/models/images/lookup",
                            params={"link": "someone/pony-mirror", "pick": "ponyDiffusionV6XL.safetensors"})
    res = r.json()
    chosen = next(o for o in res["options"] if o["key"] == res["pick"])
    assert chosen["supported"] is True and chosen["preset"] == "pony"
    assert res["suggested"]["preset"] == "pony" and res["suggested"]["id"] == "pony-mirror-ponydiffusionv6xl"
    r = await signed_in.get("/hub/api/models/images/lookup",
                            params={"link": "someone/pony-mirror", "pick": "old/sd15-model.safetensors"})
    sd15 = next(o for o in r.json()["options"] if o["key"] == "old/sd15-model.safetensors")
    assert sd15["supported"] is False and "1.x or 2.x" in sd15["reason"]
    # A link straight to a file picks it; a v-prediction model is fine (ComfyUI reads the marker).
    r = await signed_in.get("/hub/api/models/images/lookup",
                            params={"link": "https://huggingface.co/someone/noob-vpred/blob/main/noobai-vpred.safetensors"})
    o = r.json()["options"][0]
    assert o["supported"] is True and o["vpred"] is True and o["preset"] == "illustrious"
    r = await signed_in.get("/hub/api/models/images/lookup", params={"link": "someone/diffusers-only"})
    assert r.status_code == 400 and "diffusers format" in r.json()["detail"]

    body = {"link": "someone/pony-mirror", "pick": "ponyDiffusionV6XL.safetensors", "id": "pony-v6",
            "name": "Pony V6", "settings": {"preset": "pony", "cfg": 6.5}}
    assert (await signed_in.post("/hub/api/models/images",
                                 json={**body, "pick": "old/sd15-model.safetensors"})).status_code == 400
    r = await signed_in.post("/hub/api/models/images", json=body)
    assert r.status_code == 200, r.text
    m = r.json()["model"]
    assert m["custom"] and m["family"] == "sdxl" and m["abilities"] == ["generate"]
    assert m["files"] == [{"repo": "someone/pony-mirror", "path": "ponyDiffusionV6XL.safetensors",
                           "dest": "comfy/checkpoints/hub/pony-v6.safetensors", "size": int(6.9 * GB)}]
    assert m["sdxl"]["cfg"] == 6.5 and m["sdxl"]["clip_skip"] == 2 and m["source"]["site"] == "huggingface"
    await _drain(hub)
    assert hub.catalog.installed(hub.catalog.get("comfy", "pony-v6"))
    assert (await signed_in.post("/hub/api/models/images", json=body)).status_code == 400     # the name is taken
    listed = {e["id"]: e for e in (await signed_in.get("/hub/api/models")).json()["comfy"]}
    assert listed["pony-v6"]["installed"] and listed["pony-v6"]["custom"]

    # Settings can change later; bad ones are refused. Only added models have them.
    r = await signed_in.put("/hub/api/models/images/pony-v6", json={"name": "Pony", "settings": {
        "preset": "pony", "steps": 30, "negative": "lowres"}})
    assert r.status_code == 200 and r.json()["model"]["sdxl"]["steps"] == 30 and r.json()["model"]["name"] == "Pony"
    assert (await signed_in.put("/hub/api/models/images/pony-v6",
                                json={"settings": {"sampler": "magic"}})).status_code == 400
    assert (await signed_in.put("/hub/api/models/images/qwen-image-2.1", json={"name": "x"})).status_code == 404

    # Removal: not while it is the Settings model for pictures; then the file goes.
    hub.settings.update("images", {"model": "pony-v6"})
    assert (await signed_in.delete("/hub/api/models/pony-v6")).status_code == 409
    hub.settings.update("images", {"model": "qwen-image-2.1"})
    path = hub.env.models_dir / "comfy/checkpoints/hub/pony-v6.safetensors"
    assert path.is_file()
    assert (await signed_in.delete("/hub/api/models/pony-v6")).status_code == 200
    assert not path.exists() and hub.catalog.get("comfy", "pony-v6") is None
    assert json.loads((hub.env.data_dir / "models.custom.json").read_text())["comfy"] == []


async def test_sdxl_from_civitai(signed_in, app, fakes, monkeypatch):
    hub = app.state.hub
    _civitai(fakes, token="civ-tok")
    monkeypatch.delenv("CIVITAI_TOKEN", raising=False)
    r = await signed_in.get("/hub/api/models/images/lookup",
                            params={"link": "https://civitai.com/models/257749/pony-diffusion-v6-xl"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["title"] == "Pony Diffusion V6 XL" and res["nsfw"] is True and res["creator"] == "PurpleSmartAI"
    pony, flux = res["options"]
    assert pony["supported"] and pony["base"] == "Pony" and pony["preset"] == "pony" and res["pick"] == "290640"
    assert pony["size"] == int(6_775_430.0 * 1024) and pony["file"]["sha256"].startswith("67ab2fd8")
    assert not flux["supported"] and "Flux.1 D" in flux["reason"]
    assert res["suggested"]["name"] == "Pony Diffusion V6 XL" and res["tokens"]["civitai"] is False
    # A version link finds its model; a LoRA is not a whole model.
    r = await signed_in.get("/hub/api/models/images/lookup", params={"link": "civitai.com/api/download/models/290640"})
    assert r.json()["pick"] == "290640" and r.json()["model_id"] == 257749
    r = await signed_in.get("/hub/api/models/images/lookup", params={"link": "https://civitai.com/models/111"})
    assert r.json()["options"][0]["supported"] is False and "LORA" in r.json()["options"][0]["reason"]
    assert (await signed_in.get("/hub/api/models/images/lookup",
                                params={"link": "https://civitai.com/models/404"})).status_code == 404

    r = await signed_in.post("/hub/api/models/images", json={
        "link": "https://civitai.com/models/257749", "pick": "290640", "id": "pony-civitai", "name": "Pony",
        "settings": {"preset": "pony"}})
    assert r.status_code == 200, r.text
    f = r.json()["model"]["files"][0]
    assert f["url"] == "https://civitai.com/api/download/models/290640?fileId=2906400"
    assert f["dest"] == "comfy/checkpoints/hub/pony-civitai.safetensors" and f["sha256"].startswith("67ab")
    assert r.json()["model"]["source"]["nsfw"] is True and r.json()["model"]["source"]["version_id"] == 290640
    # Civitai wants a signed-in user: the download fails and says how to fix it.
    await _drain(hub)
    st = next(d for d in hub.extras["downloads"].snapshot()["files"] if d["model"] == "pony-civitai")
    assert st["status"] == "failed" and "CIVITAI_TOKEN" in st["error"]
    # With the key, the download goes through Civitai's redirect; the key stays with Civitai.
    monkeypatch.setenv("CIVITAI_TOKEN", "civ-tok")
    assert (await signed_in.post("/hub/api/models/pony-civitai/download")).status_code == 200
    await _drain(hub)
    assert hub.catalog.installed(hub.catalog.get("comfy", "pony-civitai"))
    assert fakes.civitai["download_auth"][-1] == "Bearer civ-tok"
    assert fakes.civitai["delivered"] == ["290640.safetensors"] and fakes.civitai["delivery_auth"] == [None]


# ---------------------------------------------------------------- in a chat

async def test_new_models_in_chats(signed_in, app, fakes):
    hub = app.state.hub
    pipe = hub.extras["images"]
    for mid in ("z-image-turbo", "chroma1-hd"):
        _install(hub, mid)
    # Z-Image Turbo: 8 steps (the fast-image setting); Chroma: the template's negative unless one is given.
    await pipe.generate("a fox", 1024, 1024, model="z-image-turbo")
    assert _node(fakes.comfy["prompts"][-1], "UNETLoader")[0]["inputs"]["unet_name"] == W.ZIMAGE["unet"]
    assert _node(fakes.comfy["prompts"][-1], "KSampler")[0]["inputs"]["steps"] == 8
    await pipe.generate("a fox", 1024, 1024, model="chroma1-hd")
    assert _node(fakes.comfy["prompts"][-1], "CLIPTextEncode")[1]["inputs"]["text"] == W.CHROMA_NEGATIVE
    assert _node(fakes.comfy["prompts"][-1], "BasicScheduler")[0]["inputs"]["steps"] == 25
    await pipe.generate("a fox", 1024, 1024, model="chroma1-hd", negative="rain")
    assert _node(fakes.comfy["prompts"][-1], "CLIPTextEncode")[1]["inputs"]["text"] == "rain"
    # They only generate: an edit goes to a model that edits.
    from .mocks import _png
    items = await pipe.edit("make it blue", [_png((8, 8))], model="chroma1-hd")
    assert items[0]["model"] == "qwen-image-2.1"

    # An SDXL model added from a link, chosen for one chat.
    _repos(fakes)
    r = await signed_in.post("/hub/api/models/images", json={
        "link": "someone/pony-mirror", "pick": "ponyDiffusionV6XL.safetensors", "id": "pony-v6", "name": "Pony V6",
        "settings": {"preset": "pony"}})
    assert r.status_code == 200, r.text
    await _drain(hub)
    c = await new_chat(signed_in, settings={"image_model": "pony-v6"})
    fakes.llama["requests"] = []
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "1girl, solo, smile", "width": 1920,
                                                                  "height": 1080, "negative": "blurry"}}]},
        {"content": "Here."}, {"content": "Pony"}]
    run = await finish(app, await say(signed_in, c["id"], "Draw her"))
    assert run.status == "done", run.events[-1]
    g = fakes.comfy["prompts"][-1]
    assert _node(g, "CheckpointLoaderSimple")[0]["inputs"]["ckpt_name"] == "hub/pony-v6.safetensors"
    assert _node(g, "CLIPSetLastLayer")[0]["inputs"]["stop_at_clip_layer"] == -2
    pos, neg = (n["inputs"]["text"] for n in _node(g, "CLIPTextEncode"))
    assert pos == "score_9, score_8_up, score_7_up, score_6_up, score_5_up, score_4_up, 1girl, solo, smile"
    assert neg == "blurry" and _node(g, "EmptyLatentImage")[0]["inputs"]["width"] == 1344
    # The tool told the model which picture model this chat uses and how to prompt it.
    tool = next(t for t in fakes.llama["requests"][0]["tools"] if t["function"]["name"] == "image_generate")
    desc = tool["function"]["description"]
    assert "this chat uses Pony V6" in desc and "Danbooru-style tags" in desc and "Z-Image Turbo" in desc
    assert set(tool["function"]["parameters"]["properties"]["model"]["enum"]) == {
        "qwen-image-2.1", "z-image-turbo", "chroma1-hd", "pony-v6"}
    # A chat whose model was removed falls back to the Settings model.
    assert (await signed_in.delete("/hub/api/models/pony-v6")).status_code == 200
    items = await pipe.generate("a fox", 1024, 1024, model="pony-v6")
    assert items[0]["model"] == "qwen-image-2.1"
