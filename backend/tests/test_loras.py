"""0.49.0 - LoRAs for the picture models: the admin's alone, added in Settings -> Models and used in the admin's own
chats (Søren: "LoRa is Admin only! also for using"). A file must be a .safetensors LoRA; its family is guessed from
its tensor names; one works only with models of its family; ComfyUI gets a loader chain after the model's loader."""
from __future__ import annotations

import json
import struct

import pytest

from app.images import workflows as W
from app.images.loras import LoraError, Loras, flux_size, guess_base, with_triggers

from .test_agent import finish, new_chat, say
from .test_images_models import _install, _node
from .test_people import add_person, client_from, sign_in, turn, user_msg

ORIGIN = {"Origin": "http://hub"}
T = {"dtype": "F16", "shape": [16, 3072], "data_offsets": [0, 0]}


def safetensors(keys: list[str], meta: dict | None = None) -> bytes:
    header = {k: T for k in keys}
    if meta:
        header["__metadata__"] = meta
    raw = json.dumps(header).encode()
    return struct.pack("<Q", len(raw)) + raw


KLEIN = ["diffusion_model.single_blocks.0.linear1.lora_A.weight", "diffusion_model.single_blocks.0.linear1.lora_B.weight"]
QWEN = ["transformer.transformer_blocks.0.img_mlp.net.0.proj.lora_A.weight"]
SDXL = ["lora_unet_input_blocks_4_1_proj_in.lora_down.weight", "lora_te1_text_model_encoder_layers_0_mlp_fc1.alpha"]
ZIMG = ["diffusion_model.noise_refiner.0.attention.qkv.lora_A.weight"]


def test_the_family_is_read_from_the_file():
    assert guess_base({k: T for k in KLEIN}) == "flux2-klein"
    assert guess_base({k: T for k in QWEN}) == "qwen-image"
    assert guess_base({k: T for k in SDXL}) == "sdxl"
    assert guess_base({k: T for k in ZIMG}) == "z-image"
    assert guess_base({"x.lora_A.weight": T, "__metadata__": {"ss_base_model_version": "flux2_klein_4b"}}) == "flux2-klein"
    assert guess_base({"x.lora_A.weight": T}) == ""
    # klein 4B against FLUX.1 and klein 9B, by block numbers and widths (0.54.0)
    def blocks(double, single, width=3072):
        h = {f"diffusion_model.double_blocks.{i}.img_attn.qkv.lora_A.weight":
             {"dtype": "F16", "shape": [16, width], "data_offsets": [0, 0]} for i in range(double)}
        h.update({f"diffusion_model.single_blocks.{i}.linear1.lora_A.weight":
                  {"dtype": "F16", "shape": [16, width], "data_offsets": [0, 0]} for i in range(single)})
        return h
    assert flux_size(blocks(5, 20)) == "" and guess_base(blocks(5, 20)) == "flux2-klein"
    assert flux_size(blocks(19, 38)) == "flux1" and guess_base(blocks(19, 38)) == ""
    assert flux_size(blocks(8, 24, 4096)) == "klein-9b" and flux_size(blocks(2, 0, 4096)) == "klein-9b"
    assert flux_size({"transformer.single_transformer_blocks.23.attn.to_qkv_mlp_proj.lora_A.weight": T}) == "klein-9b"
    # 0.61.0: klein 9B is a family of its own (a LoRA works only with the size it was trained on) - by its blocks and
    # widths, or by what ai-toolkit wrote into it
    assert guess_base(blocks(8, 24, 4096)) == "flux2-klein-9b"
    assert guess_base({"x.lora_A.weight": T, "__metadata__": {"ss_base_model_version": "flux2_klein_9b"}}) == \
        "flux2-klein-9b"
    assert guess_base({**{k: T for k in KLEIN}, "__metadata__": {"name": "FLUX.2-klein-base-9B portrait"}}) == \
        "flux2-klein-9b"
    # FLUX's double blocks have img_mlp too (double_blocks.N.img_mlp.0, as ai-toolkit saves them): FLUX, not Qwen-Image
    mlp = {f"diffusion_model.double_blocks.{i}.{part}.lora_A.weight":
           {"dtype": "F16", "shape": [16, width], "data_offsets": [0, 0]}
           for i in range(5) for part, width in (("img_mlp.0", 3072), ("txt_mlp.2", 9216))}
    assert guess_base(mlp) == "flux2-klein"
    mlp9 = {k: {**v, "shape": [16, 4096 if v["shape"][1] == 3072 else 12288]} for k, v in mlp.items()}
    assert guess_base({**mlp9, "__metadata__": {"name": "qwen look"}}) == "flux2-klein-9b"      # the blocks decide
    sdxl = {f"lora_unet_output_blocks_2_1_transformer_blocks_{i}_attn1_to_q.lora_down.weight":
            {"dtype": "F16", "shape": [16, 1280], "data_offsets": [0, 0]} for i in range(6)}
    assert flux_size(sdxl) == ""                                   # SDXL's transformer blocks: not a FLUX at all
    assert with_triggers("a man on a bike", ["sorenx"]) == "sorenx, a man on a bike"
    assert with_triggers("sorenx on a bike", ["sorenx"]) == "sorenx on a bike"
    assert with_triggers("an explorative walk", ["lora"]) == "lora, an explorative walk"     # a word, not a part


def test_adding_changing_and_removing(tmp_path):
    store = Loras(tmp_path / "data", tmp_path / "models")
    src = tmp_path / "Soren face v2.safetensors"
    src.write_bytes(safetensors(KLEIN, {"trigger_word": "sorenx"}))
    entry = store.add_file(src, src.name)
    assert entry["id"] == "soren-face-v2" and entry["base"] == "flux2-klein" and entry["trigger"] == "sorenx"
    assert entry["strength"] == 1.0 and (tmp_path / "models/comfy/loras/hub/soren-face-v2.safetensors").is_file()
    assert not src.exists()                                               # moved into place
    # A slider LoRA (0.55.0): told by its name, strength -10 to 10; plain LoRAs stay 0-2; off again, it is clamped.
    zit = tmp_path / "natural-beauty-slider-zit.safetensors"
    zit.write_bytes(safetensors(["diffusion_model.noise_refiner.0.attention.qkv.lora_A.weight"]))
    sl = store.add_file(zit, zit.name)
    assert sl["slider"] is True and sl["base"] == "z-image"
    assert store.update(sl["id"], strength=4)["strength"] == 4 and store.update(sl["id"], strength=-3)["strength"] == -3
    with pytest.raises(LoraError, match="slider"):
        store.update(entry["id"], strength=4)                        # a plain LoRA: 0-2
    assert store.resolve([{"id": sl["id"], "strength": -12}], "z-image")[0][0][1] == -10.0
    assert store.resolve([{"id": entry["id"], "strength": -1}], "flux2-klein")[0][0][1] == 0.0
    off = store.update(sl["id"], slider=False)
    assert off["slider"] is False and off["strength"] == 0.0           # -3 back into 0-2
    store.delete(sl["id"])
    nine = tmp_path / "klein9b.safetensors"            # klein 9B (0.61.0): its own family, no longer refused
    raw = json.dumps({"diffusion_model.double_blocks.7.img_attn.qkv.lora_A.weight":
                      {"dtype": "F16", "shape": [16, 4096], "data_offsets": [0, 0]}}).encode()
    nine.write_bytes(struct.pack("<Q", len(raw)) + raw)
    big = store.add_file(nine, nine.name)
    assert big["base"] == "flux2-klein-9b" and big["base_label"] == "FLUX.2 klein 9B"
    assert store.resolve([big["id"]], "flux2-klein")[3] == [f"the LoRA {big['name']} was left out: it is for FLUX.2 "
                                                            "klein 9B models, not this one"]
    assert store.resolve([big["id"]], "flux2-klein-9b")[0] == [(f"hub/{big['id']}.safetensors", 1.0)]
    store.delete(big["id"])
    again = tmp_path / "again.safetensors"
    again.write_bytes(safetensors(KLEIN))
    assert store.add_file(again, again.name, name="Soren face v2")["id"] == "soren-face-v2-2"
    assert store.update("soren-face-v2", strength=0.8, note="Me, for course pictures")["strength"] == 0.8
    for bad in ({"strength": 3}, {"base": "flux1"}, {"name": "  "}):
        with pytest.raises(LoraError):
            store.update("soren-face-v2", **bad)
    whole = tmp_path / "model.safetensors"
    whole.write_bytes(safetensors(["model.diffusion_model.input_blocks.0.0.weight"]))
    with pytest.raises(LoraError, match="not a LoRA"):
        store.add_file(whole, whole.name)
    junk = tmp_path / "junk.safetensors"
    junk.write_bytes(b"hello")
    with pytest.raises(LoraError, match="not a .safetensors"):
        store.add_file(junk, junk.name)
    # what fits a family; the strength of a call wins; another family's is left out with a note
    nodes, triggers, names, notes, used = store.resolve([{"id": "soren-face-v2", "strength": 0.6}, "soren-face-v2-2",
                                                         "nope"], "flux2-klein")
    assert nodes == [("hub/soren-face-v2.safetensors", 0.6), ("hub/soren-face-v2-2.safetensors", 1.0)]
    assert triggers == ["sorenx"] and names == ["Soren face v2", "Soren face v2"] and "no longer there" in notes[0]
    assert used[0] == {"name": "Soren face v2", "strength": 0.6, "slider": False}      # for the line under a picture
    other = store.resolve(["soren-face-v2"], "qwen-image")
    assert other[0] == [] and other[3] == ["the LoRA Soren face v2 was left out: it is for FLUX.2 klein 4B models, "
                                           "not this one"]
    assert store.resolve(["soren-face-v2"], "flux2-klein-9b")[0] == []           # a 4B LoRA does not fit 9B either
    store.delete("soren-face-v2-2")
    assert [x["id"] for x in store.list()] == ["soren-face-v2"]


def test_loaders_go_after_the_models_loader():
    g = W.with_loras(W.klein_t2i("a fox", 1024, 1024, 1), [("hub/a.safetensors", 0.8), ("hub/b.safetensors", 1.0)])
    a, b = g["lora1"], g["lora2"]
    assert a["class_type"] == "LoraLoaderModelOnly" and a["inputs"]["model"] == ["1", 0]
    assert b["inputs"]["model"] == ["lora1", 0] and _node(g, "CFGGuider")[0]["inputs"]["model"] == ["lora2", 0]
    q = W.with_loras(W.qwen_t2i("a fox", 1024, 1024, 1), [("hub/q.safetensors", 1.0)])
    assert _node(q, "QwenImage21Cache")[0]["inputs"]["model"] == ["lora1", 0]
    s = W.with_loras(W.sdxl_t2i("hub/x.safetensors", "a fox", 1024, 1024, 1, clip_skip=2), [("hub/s.safetensors", 0.7)])
    assert s["lora1"]["class_type"] == "LoraLoader" and s["lora1"]["inputs"]["strength_clip"] == 0.7
    assert _node(s, "CLIPSetLastLayer")[0]["inputs"]["clip"] == ["lora1", 1]
    assert _node(s, "KSampler")[0]["inputs"]["model"] == ["lora1", 0]
    assert _node(s, "VAEDecode")[0]["inputs"]["vae"] == ["1", 2]                # the VAE stays the checkpoint's
    assert W.with_loras(W.klein_t2i("a fox", 1024, 1024, 1), []) == W.klein_t2i("a fox", 1024, 1024, 1)


async def test_the_admin_adds_and_uses_a_lora_and_a_person_never_sees_one(signed_in, app, fakes):
    hub = app.state.hub
    _install(hub, "flux2-klein-4b")
    r = await signed_in.post("/hub/api/loras", files={"file": ("cx9020.safetensors", safetensors(KLEIN), "application/octet-stream")},
                             data={"name": "CX9020", "trigger": "cx9020x"}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    lora = r.json()
    assert lora["id"] == "cx9020" and lora["base"] == "flux2-klein" and lora["fits"][0]["id"] == "flux2-klein-4b"
    bad = await signed_in.post("/hub/api/loras", files={"file": ("x.ckpt", b"x", "application/octet-stream")},
                               headers=ORIGIN)
    assert bad.status_code == 400
    r = await signed_in.patch("/hub/api/loras/cx9020", json={"strength": 0.9}, headers=ORIGIN)
    assert r.json()["strength"] == 0.9
    assert [x["id"] for x in (await signed_in.get("/hub/api/loras")).json()["loras"]] == ["cx9020"]

    # In a chat with FLUX.2 klein and the LoRA chosen in its picture menu: the loader goes in, the trigger too.
    c = await new_chat(signed_in, settings={"image_model": "flux2-klein-4b", "image_loras": ["cx9020"]})
    fakes.llama["requests"] = []
    fakes.llama["replies"] = [{"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "on a workbench"}}]},
                              {"content": "Here."}, {"content": "CX"}]
    run = await finish(app, await say(signed_in, c["id"], "Draw the controller"))
    assert run.status == "done", run.events[-1]
    g = fakes.comfy["prompts"][-1]
    assert _node(g, "LoraLoaderModelOnly")[0]["inputs"] == {"model": ["1", 0], "lora_name": "hub/cx9020.safetensors",
                                                            "strength_model": 0.9}
    assert _node(g, "CLIPTextEncode")[0]["inputs"]["text"] == "cx9020x, on a workbench"
    tool = next(t for t in fakes.llama["requests"][0]["tools"] if t["function"]["name"] == "image_generate")
    desc = tool["function"]["description"]
    assert "On in this chat, used on every picture:\n- CX9020 (for FLUX.2 klein 4B models" in desc
    props = tool["function"]["parameters"]["properties"]
    assert "loras" not in props and "model" not in props                  # 0.59.0: the picture menu decides
    res = next(e["data"] for e in run.events if e["type"] == "tool_result")
    assert "with the LoRA CX9020" in res["text"]
    assert res["display"]["made_with"] == {"model": "FLUX.2 [klein] 4B", "left_out": [],
                                           "loras": [{"name": "CX9020", "strength": 0.9, "slider": False}]}

    # A call that still names a model and LoRAs (copied from an earlier call) cannot override the menu: a chat on
    # FLUX.2 klein without the LoRA gets neither the other model nor the LoRA, and the result says why.
    c2 = await new_chat(signed_in, settings={"image_model": "flux2-klein-4b", "image_loras": []})
    fakes.llama["requests"] = []
    fakes.llama["replies"] = [{"tool_calls": [{"name": "image_generate", "arguments": {
        "prompt": "on a shelf", "model": "qwen-image-2.1", "loras": [{"id": "cx9020", "strength": 1.2}]}}]},
        {"content": "Here."}, {"content": "CX"}]
    run = await finish(app, await say(signed_in, c2["id"], "Draw it again"))
    g = fakes.comfy["prompts"][-1]
    assert not _node(g, "LoraLoaderModelOnly") and _node(g, "CLIPTextEncode")[0]["inputs"]["text"] == "on a shelf"
    res = next(e["data"] for e in run.events if e["type"] == "tool_result")
    assert res["display"]["made_with"]["model"] == "FLUX.2 [klein] 4B" and not res["display"]["made_with"]["loras"]
    assert "model and LoRAs were not used" in res["text"] and "picture menu" in res["text"]

    # A LoRA on in the chat that does not fit its picture model: left out, said under the picture and to the model.
    c3 = await new_chat(signed_in, settings={"image_model": "qwen-image-2.1", "image_loras": ["cx9020"]})
    fakes.llama["requests"] = []
    fakes.llama["replies"] = [{"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a desk"}}]},
                              {"content": "Here."}, {"content": "CX"}]
    run = await finish(app, await say(signed_in, c3["id"], "Draw a desk"))
    res = next(e["data"] for e in run.events if e["type"] == "tool_result")
    assert res["display"]["made_with"]["left_out"] == ["the LoRA CX9020 was left out: it is for FLUX.2 klein "
                                                       "4B models, not this one"]
    tool = next(t for t in fakes.llama["requests"][0]["tools"] if t["function"]["name"] == "image_generate")
    assert "NOT used: it does not fit this chat's picture model" in tool["function"]["description"]

    # The chat's own strength (the slider in its picture menu, 0.54.0) wins over the LoRA's; nonsense is ignored.
    from types import SimpleNamespace
    from app.agent.tools import images as IT
    store = app.state.hub.extras["loras"]
    for given, used in ((0.65, 0.65), ("lots", 0.9), (7, 2.0), (12, 0.9), (None, 0.9)):     # 7: into its 0-2
        ctx = SimpleNamespace(engine=SimpleNamespace(device=None),          # the admin's turn
                              chat={"settings": {"image_loras": ["cx9020"], "image_lora_strength": {"cx9020": given}}})
        assert store.resolve(IT._loras(ctx), "flux2-klein")[0] == [("hub/cx9020.safetensors", used)]

    # A person: no LoRA routes, no LoRA in the tool, and one named in a device turn is not used.
    await add_person(signed_in)
    async with client_from(app) as phone:
        await sign_in(phone)
        assert (await phone.get("/hub/api/loras")).status_code in (401, 403)
        assert (await phone.post("/hub/api/loras", files={"file": ("a.safetensors", safetensors(KLEIN), "x")},
                                 headers=ORIGIN)).status_code in (401, 403)
        assert "cx9020" not in json.dumps((await phone.get("/hub/api/models")).json())
        fakes.llama["requests"] = []
        fakes.llama["replies"] = [
            {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a fox", "model": "flux2-klein-4b",
                                                                     "loras": [{"id": "cx9020"}]}}]},
            {"content": "Done."}]
        n = len(fakes.comfy["prompts"])
        await turn(app, phone, {"title": "S", "settings": {"image_loras": ["cx9020"]},
                                "messages": [user_msg("Draw a fox")]})
        tool = next(t for t in fakes.llama["requests"][0]["tools"] if t["function"]["name"] == "image_generate")
        assert "loras" not in tool["function"]["parameters"]["properties"]
        assert "LoRA" not in tool["function"]["description"]
        assert len(fakes.comfy["prompts"]) == n + 1 and not _node(fakes.comfy["prompts"][-1], "LoraLoaderModelOnly")
    assert (await signed_in.delete("/hub/api/loras/cx9020", headers=ORIGIN)).status_code == 200


async def test_klein_9b_draws_and_edits_with_its_own_files_and_loras(signed_in, app, fakes):
    """0.61.0: FLUX.2 klein 9B is a picture model of its own family - the klein graph with its 8-bit files from
    ComfyUI's template - and takes only LoRAs made for 9B (a 4B one is left out, with the reason)."""
    from .mocks import _png
    hub = app.state.hub
    pipe = hub.extras["images"]
    _install(hub, "flux2-klein-9b")
    nine = {"diffusion_model.single_blocks.23.linear1.lora_A.weight":
            {"dtype": "F16", "shape": [16, 4096], "data_offsets": [0, 0]}}
    raw = json.dumps(nine).encode()
    r = await signed_in.post("/hub/api/loras", files={"file": ("rikke9.safetensors", struct.pack("<Q", len(raw)) + raw,
                                                               "application/octet-stream")},
                             data={"trigger": "rkxv"}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    lora = r.json()
    assert lora["base"] == "flux2-klein-9b" and [m["id"] for m in lora["fits"]] == ["flux2-klein-9b"]
    four = (await signed_in.post("/hub/api/loras", files={"file": ("cx.safetensors", safetensors(KLEIN), "x")},
                                 headers=ORIGIN)).json()
    assert four["base"] == "flux2-klein" and four["fits"] == []                  # klein 4B is not downloaded here

    items = await pipe.generate("a fox", 1024, 1024, model="flux2-klein-9b", loras=[lora["id"], four["id"]])
    g = fakes.comfy["prompts"][-1]
    assert _node(g, "UNETLoader")[0]["inputs"]["unet_name"] == "flux-2-klein-9b-fp8.safetensors"
    assert _node(g, "CLIPLoader")[0]["inputs"] == {"clip_name": "qwen_3_8b_fp8mixed.safetensors", "type": "flux2",
                                                   "device": "default"}
    assert _node(g, "VAELoader")[0]["inputs"]["vae_name"] == "flux2-vae.safetensors"
    assert _node(g, "Flux2Scheduler")[0]["inputs"]["steps"] == 4
    assert [n["inputs"]["lora_name"] for n in _node(g, "LoraLoaderModelOnly")] == [f"hub/{lora['id']}.safetensors"]
    assert _node(g, "CLIPTextEncode")[0]["inputs"]["text"] == "rkxv, a fox"
    assert items[0]["model"] == "flux2-klein-9b" and "FLUX.2 klein 4B models" in items[0]["lora_notes"][0]

    items = await pipe.edit("make it red", [_png((8, 8))], model="flux2-klein-9b", loras=[lora["id"]])
    g = fakes.comfy["prompts"][-1]
    assert items[0]["model"] == "flux2-klein-9b" and _node(g, "ReferenceLatent")
    assert _node(g, "UNETLoader")[0]["inputs"]["unet_name"] == "flux-2-klein-9b-fp8.safetensors"
    assert _node(g, "LoraLoaderModelOnly")[0]["inputs"]["lora_name"] == f"hub/{lora['id']}.safetensors"
    families = (await signed_in.get("/hub/api/loras")).json()["families"]
    assert families["flux2-klein"] == "FLUX.2 klein 4B" and families["flux2-klein-9b"] == "FLUX.2 klein 9B"
