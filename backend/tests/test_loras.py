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
    nine = tmp_path / "klein9b.safetensors"                              # klein 9B: refused, with the reason
    raw = json.dumps({"diffusion_model.double_blocks.7.img_attn.qkv.lora_A.weight":
                      {"dtype": "F16", "shape": [16, 4096], "data_offsets": [0, 0]}}).encode()
    nine.write_bytes(struct.pack("<Q", len(raw)) + raw)
    with pytest.raises(LoraError, match="klein 9B"):
        store.add_file(nine, nine.name)
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
    nodes, triggers, names, notes = store.resolve([{"id": "soren-face-v2", "strength": 0.6}, "soren-face-v2-2",
                                                   "nope"], "flux2-klein")
    assert nodes == [("hub/soren-face-v2.safetensors", 0.6), ("hub/soren-face-v2-2.safetensors", 1.0)]
    assert triggers == ["sorenx"] and names == ["Soren face v2", "Soren face v2"] and "not there" in notes[0]
    assert store.resolve(["soren-face-v2"], "qwen-image")[0] == []
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
    assert "cx9020: CX9020" in tool["function"]["description"]
    assert tool["function"]["parameters"]["properties"]["loras"]["items"]["properties"]["id"]["enum"] == ["cx9020"]
    result = next(e["data"]["text"] for e in run.events if e["type"] == "tool_result")
    assert "with the LoRA CX9020" in result

    # The chat's own strength (the slider in its picture menu, 0.54.0) wins over the LoRA's; nonsense is ignored,
    # and a strength the call names wins over both.
    from types import SimpleNamespace
    from app.agent.tools import images as IT
    store = app.state.hub.extras["loras"]
    for given, used in ((0.65, 0.65), ("lots", 0.9), (7, 0.9), (None, 0.9)):
        ctx = SimpleNamespace(engine=SimpleNamespace(device=None),          # the admin's turn
                              chat={"settings": {"image_loras": ["cx9020"], "image_lora_strength": {"cx9020": given}}})
        assert store.resolve(IT._loras(ctx, {}), "flux2-klein")[0] == [("hub/cx9020.safetensors", used)]
    ctx = SimpleNamespace(engine=SimpleNamespace(device=None),
                          chat={"settings": {"image_loras": ["cx9020"], "image_lora_strength": {"cx9020": 0.65}}})
    named = IT._loras(ctx, {"loras": [{"id": "cx9020", "strength": 1.2}]})
    assert store.resolve(named, "flux2-klein")[0] == [("hub/cx9020.safetensors", 1.2)]

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
