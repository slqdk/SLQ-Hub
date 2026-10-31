"""0.62.0 - model files from the owner's computer (Søren: "så jeg selv kan uploade modeller fra min pc"): sent in
pieces that can be resumed, read for what they are from their own header while they arrive, and made into a language
model (.gguf, with a vision projector), a picture model (an SDXL checkpoint) or the missing file of a model in the
Hub's own list."""
from __future__ import annotations

import hashlib
import json
import struct
import time

from app.uploads import Uploads

from .mocks import safetensors_bytes
from .test_images_models import sdxl_header
from .test_people import add_person, client_from, sign_in

ORIGIN = {"Origin": "http://hub"}
PIECE = 1000


def _s(text: str) -> bytes:
    raw = text.encode()
    return struct.pack("<Q", len(raw)) + raw


def gguf_bytes(arch: str = "qwen35", kind: str = "model", tokens: int = 5000, template: str = "",
               tensors: tuple = ((4096, 1000), (1000,))) -> bytes:
    """A small GGUF: the metadata llama.cpp writes first (architecture, name, context, a tokenizer list longer than
    the old reader skipped, the chat template) and a few tensors' shapes."""
    kv = [("general.architecture", 8, _s(arch)), ("general.type", 8, _s(kind)), ("general.name", 8, _s("Tiny Qwen")),
          (f"{arch}.context_length", 4, struct.pack("<I", 131072)),
          ("tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, tokens) + b"".join(_s(f"t{i}") for i in range(tokens))),
          ("tokenizer.ggml.scores", 9, struct.pack("<IQ", 6, tokens) + b"\0\0\0\0" * tokens)]
    if template:
        kv.append(("tokenizer.chat_template", 8, _s(template)))
    out = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(kv))
    for key, vtype, value in kv:
        out += _s(key) + struct.pack("<I", vtype) + value
    for i, dims in enumerate(tensors):
        out += _s(f"blk.{i}.weight") + struct.pack("<I", len(dims)) + struct.pack(f"<{len(dims)}Q", *dims)
        out += struct.pack("<IQ", 0, 0)
    return out + b"\0" * 4096


async def upload(client, name: str, data: bytes, modified: int = 1, piece: int = PIECE, stop_at: int | None = None) -> dict:
    """What the browser does: ask for the upload (or the one begun before), then send the rest in pieces."""
    up = (await client.post("/hub/api/model-uploads", json={"name": name, "size": len(data), "modified": modified},
                            headers=ORIGIN)).json()
    at = up["received"]
    while at < len(data) and (stop_at is None or at < stop_at):
        r = await client.put(f"/hub/api/model-uploads/{up['id']}", params={"offset": at}, content=data[at:at + piece],
                             headers=ORIGIN)
        assert r.status_code == 200, r.text
        at = r.json()["received"]
    return (await client.get(f"/hub/api/model-uploads/{up['id']}")).json()


async def test_pieces_resume_and_are_checked(signed_in, app):
    data = bytes(range(256)) * 20                        # 5,120 bytes
    first = await upload(signed_in, "a.gguf", data, stop_at=2000)
    assert first["received"] == 2000 and not first["done"]
    # a piece at the wrong place is refused with where the Hub's copy ends
    r = await signed_in.put(f"/hub/api/model-uploads/{first['id']}", params={"offset": 1000}, content=b"x" * 10,
                            headers=ORIGIN)
    assert r.status_code == 409 and "2000" in r.json()["detail"]
    # past the end: refused whole, nothing written
    r = await signed_in.put(f"/hub/api/model-uploads/{first['id']}", params={"offset": 2000}, content=b"y" * 4000,
                            headers=ORIGIN)
    assert r.status_code == 400 and (await signed_in.get(f"/hub/api/model-uploads/{first['id']}")).json()["received"] == 2000
    # the same file again (a closed page): the upload begun before, which goes on where it stopped
    again = await upload(signed_in, "a.gguf", data)
    assert again["id"] == first["id"] and again["done"] and again["received"] == len(data)
    ups = app.state.hub.extras["uploads"]
    assert ups.part(first["id"]).read_bytes() == data
    other = (await signed_in.post("/hub/api/model-uploads", json={"name": "a.gguf", "size": len(data), "modified": 2},
                                  headers=ORIGIN)).json()
    assert other["id"] != first["id"]                      # another date: another file
    assert (await signed_in.delete(f"/hub/api/model-uploads/{other['id']}", headers=ORIGIN)).status_code == 200
    assert (await signed_in.get(f"/hub/api/model-uploads/{other['id']}")).status_code == 404
    for bad in ({"name": "", "size": 5}, {"name": "x.gguf", "size": 0}, {"name": "x.gguf", "size": 10**15}):
        assert (await signed_in.post("/hub/api/model-uploads", json=bad, headers=ORIGIN)).status_code in (400, 507)
    assert (await signed_in.get("/hub/api/model-uploads/../../etc")).status_code == 404
    # a folder in the name is not a path
    sneaky = (await signed_in.post("/hub/api/model-uploads", json={"name": "../../x.gguf", "size": 3}, headers=ORIGIN)).json()
    assert sneaky["name"] == "x.gguf"
    # the updater waits for an upload that is coming in
    from app.ui.update_routes import busy_reasons
    half = await upload(signed_in, "big.safetensors", b"z" * 3000, stop_at=1000)
    assert "big.safetensors is being uploaded" in busy_reasons(app.state.hub)
    ups.delete(half["id"])
    # people never reach it
    await add_person(signed_in)
    async with client_from(app) as phone:
        await sign_in(phone)
        assert (await phone.post("/hub/api/model-uploads", json={"name": "x.gguf", "size": 3},
                                 headers=ORIGIN)).status_code in (401, 403)
        assert (await phone.get("/hub/api/model-uploads")).status_code in (401, 403)


def test_old_uploads_are_tidied(tmp_path):
    ups = Uploads(tmp_path)
    old = ups.create("old.gguf", 10)
    new = ups.create("new.gguf", 10)
    past = time.time() - 4 * 86400
    import os
    for p in (ups.part(old["id"]), ups.part(old["id"]).with_suffix(".json")):
        os.utime(p, (past, past))
    ups.cleanup()
    assert [u["id"] for u in ups.list()] == [new["id"]]


async def test_a_gguf_becomes_a_language_model(signed_in, app):
    hub = app.state.hub
    model = gguf_bytes(template="{% if tools %}...{% endif %}{% if enable_thinking %}<think>{% endif %}")
    up = await upload(signed_in, "Tiny-Qwen3.5-Q4_K_M.gguf", model, stop_at=PIECE)
    # read while it arrives: its metadata is not all there after the first piece
    assert (await signed_in.get(f"/hub/api/model-uploads/{up['id']}/inspect")).json() == {"pending": True}
    up = await upload(signed_in, "Tiny-Qwen3.5-Q4_K_M.gguf", model)
    facts = (await signed_in.get(f"/hub/api/model-uploads/{up['id']}/inspect")).json()
    assert facts["kind"] == "gguf" and facts["architecture"] == "qwen35" and facts["context_length"] == 131072
    assert facts["parameters"] == 4096 * 1000 + 1000 and facts["tools"] is True and facts["thinking"] is True
    assert facts["quantization"] == "Q4_K_M" and facts["suggested"] == {"id": "tiny-qwen3.5-q4_k_m", "name": "Tiny Qwen"}
    mm = await upload(signed_in, "mmproj-F16.gguf", gguf_bytes(arch="clip", kind="mmproj", tokens=0))
    assert (await signed_in.get(f"/hub/api/model-uploads/{mm['id']}/inspect")).json()["kind"] == "mmproj"
    # the projector is not a model, and the model is not a projector
    bad = await signed_in.post("/hub/api/models/local", json={"upload": mm["id"], "id": "x"}, headers=ORIGIN)
    assert bad.status_code == 400 and "vision projector" in bad.json()["detail"]
    bad = await signed_in.post("/hub/api/models/local", json={"upload": up["id"], "mmproj": up["id"], "id": "x"},
                               headers=ORIGIN)
    assert bad.status_code == 400 and "not a vision projector" in bad.json()["detail"]
    assert (await signed_in.post("/hub/api/models/local", json={"upload": up["id"], "id": "qwen3.8-27b"},
                                 headers=ORIGIN)).status_code == 400               # a bundled model's name
    r = await signed_in.post("/hub/api/models/local", json={
        "upload": up["id"], "mmproj": mm["id"], "id": "tiny-qwen", "name": "Tiny Qwen", "ctx": 999999,
        "tools": True, "thinking": True}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    m = r.json()["model"]
    assert m["custom"] and m["source"]["site"] == "upload" and m["source"]["file"] == "Tiny-Qwen3.5-Q4_K_M.gguf"
    assert [f["dest"] for f in m["files"]] == ["llm/upload/tiny-qwen/Tiny-Qwen3.5-Q4_K_M.gguf",
                                               "llm/upload/tiny-qwen/mmproj-F16.gguf"]
    assert m["capabilities"] == ["chat", "vision", "tools", "thinking"] and m["server"]["ctx"] == 131072
    assert m["parameter_size"] == "0.0B" and m["sampling"]["thinking"]["presence_penalty"] == 1.5  # qwen3 family
    assert hub.catalog.installed(m) and (hub.env.models_dir / m["files"][0]["dest"]).read_bytes() == model
    args = hub.catalog.server_args("tiny-qwen")
    assert "/models/llm/upload/tiny-qwen/mmproj-F16.gguf" in args
    assert hub.extras["uploads"].list() == []                                     # both taken
    # gone from the disk: it cannot be downloaded again, only uploaded
    (hub.env.models_dir / m["files"][0]["dest"]).unlink()
    r = await signed_in.post("/hub/api/models/tiny-qwen/download", headers=ORIGIN)
    assert r.status_code == 400 and "uploaded from a computer" in r.json()["detail"]
    again = await upload(signed_in, "Tiny-Qwen3.5-Q4_K_M.gguf", model, modified=5)
    r = await signed_in.post("/hub/api/models/tiny-qwen/files", json={"upload": again["id"],
                                                                      "dest": m["files"][0]["dest"]}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["installed"] is True
    # Remove takes its folder too
    assert (await signed_in.delete("/hub/api/models/tiny-qwen", headers=ORIGIN)).status_code == 200
    assert not (hub.env.models_dir / "llm/upload/tiny-qwen").exists()


async def test_an_sdxl_checkpoint_becomes_a_picture_model(signed_in, app):
    hub = app.state.hub
    ckpt = safetensors_bytes(sdxl_header(), pad=3000)
    up = await upload(signed_in, "myPonyMix_v2.safetensors", ckpt, stop_at=PIECE // 2)
    facts = (await signed_in.get(f"/hub/api/model-uploads/{up['id']}/inspect")).json()
    if facts.get("pending") is not True:                        # the whole header fits in the first piece
        assert facts["kind"] == "sdxl"
    up = await upload(signed_in, "myPonyMix_v2.safetensors", ckpt)
    facts = (await signed_in.get(f"/hub/api/model-uploads/{up['id']}/inspect")).json()
    assert facts["kind"] == "sdxl" and facts["supported"] is True and facts["preset"] == "pony"
    assert facts["suggested"]["id"] == "myponymix_v2"
    r = await signed_in.post("/hub/api/models/images/local", json={"upload": up["id"], "id": "my-pony",
                                                                   "name": "My Pony", "settings": {"preset": "pony"}},
                             headers=ORIGIN)
    assert r.status_code == 200, r.text
    m = r.json()["model"]
    assert m["family"] == "sdxl" and m["source"]["site"] == "upload" and m["sdxl"]["clip_skip"] == 2
    assert m["files"] == [{"dest": "comfy/checkpoints/hub/my-pony.safetensors", "size": len(ckpt)}]
    assert hub.catalog.installed(m)
    assert any(c["id"] == "my-pony" for c in hub.extras["training"].sdxl_checkpoints())    # a training base too
    # a LoRA or a FLUX file is said for what it is, and not added
    lora = await upload(signed_in, "face.safetensors", safetensors_bytes(
        {"lora_unet_input_blocks_4_1_proj_in.lora_down.weight": {"dtype": "F16", "shape": [4, 4],
                                                                 "data_offsets": [0, 0]}}))
    facts = (await signed_in.get(f"/hub/api/model-uploads/{lora['id']}/inspect")).json()
    assert facts["kind"] == "lora" and facts["supported"] is False
    r = await signed_in.post("/hub/api/models/images/local", json={"upload": lora["id"], "id": "face"}, headers=ORIGIN)
    assert r.status_code == 400 and "LoRA" in r.json()["detail"]
    assert hub.extras["uploads"].get(lora["id"])["done"]                          # still there to use or delete
    assert (await signed_in.delete("/hub/api/models/my-pony", headers=ORIGIN)).status_code == 200
    assert not (hub.env.models_dir / "comfy/checkpoints/hub/my-pony.safetensors").exists()


async def test_a_file_of_a_model_in_the_list(signed_in, app):
    """Instead of a download: checked against the catalog (size within 3%, kind, SHA-256 when it has one) and put
    where the download would have put it."""
    hub = app.state.hub
    entry = hub.catalog.get("audio", "roest-v3-turbo")
    f = next(x for x in entry["files"] if x["dest"].endswith("ve.safetensors"))       # 5.7 MB in the catalog
    small = await upload(signed_in, "ve.safetensors", safetensors_bytes({"w": {"dtype": "F16", "shape": [1],
                                                                              "data_offsets": [0, 2]}}))
    r = await signed_in.post("/hub/api/models/roest-v3-turbo/files", json={"upload": small["id"], "dest": f["dest"]},
                             headers=ORIGIN)
    assert r.status_code == 400 and "Is it the right file" in r.json()["detail"]
    junk = await upload(signed_in, "ve.safetensors", b"x" * f["size"], modified=3, piece=2_000_000)
    r = await signed_in.post("/hub/api/models/roest-v3-turbo/files", json={"upload": junk["id"], "dest": f["dest"]},
                             headers=ORIGIN)
    assert r.status_code == 400 and "safetensors" in r.json()["detail"]
    good = safetensors_bytes({"w": {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}}, pad=f["size"] - 100)
    up = await upload(signed_in, "ve.safetensors", good, modified=4, piece=2_000_000)
    hub.extras["downloads"].state[f["dest"]] = {"model": "roest-v3-turbo", "status": "downloading"}
    r = await signed_in.post("/hub/api/models/roest-v3-turbo/files", json={"upload": up["id"], "dest": f["dest"]},
                             headers=ORIGIN)
    assert r.status_code == 409 and "downloading" in r.json()["detail"]
    hub.extras["downloads"].state.pop(f["dest"])
    r = await signed_in.post("/hub/api/models/roest-v3-turbo/files", json={"upload": up["id"], "dest": f["dest"]},
                             headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert r.json() == {"installed": False, "dest": f["dest"]}             # its other files are still missing
    assert (hub.env.models_dir / f["dest"]).read_bytes() == good
    up2 = await upload(signed_in, "ve.safetensors", good, modified=6, piece=2_000_000)
    r = await signed_in.post("/hub/api/models/roest-v3-turbo/files", json={"upload": up2["id"], "dest": f["dest"]},
                             headers=ORIGIN)
    assert r.status_code == 409                                             # there already
    assert (await signed_in.post("/hub/api/models/roest-v3-turbo/files", json={"upload": up2["id"],
                                                                               "dest": "../../hub.db"},
                                 headers=ORIGIN)).status_code == 404
    # a file whose catalog entry names its SHA-256 (from Civitai): only that file
    data = safetensors_bytes(sdxl_header(), pad=500)
    hub.catalog.add_custom({"id": "civ-sdxl", "name": "Civ", "family": "sdxl", "abilities": ["generate"],
                            "files": [{"url": "https://civitai.com/api/download/models/1",
                                       "dest": "comfy/checkpoints/hub/civ-sdxl.safetensors", "size": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest()}]}, "comfy")
    wrong = await upload(signed_in, "civ.safetensors", data[:-1] + b"!", modified=7)
    r = await signed_in.post("/hub/api/models/civ-sdxl/files", json={"upload": wrong["id"],
                                                                     "dest": "comfy/checkpoints/hub/civ-sdxl.safetensors"},
                             headers=ORIGIN)
    assert r.status_code == 400 and "SHA-256" in r.json()["detail"]
    right = await upload(signed_in, "civ.safetensors", data, modified=8)
    r = await signed_in.post("/hub/api/models/civ-sdxl/files", json={"upload": right["id"],
                                                                     "dest": "comfy/checkpoints/hub/civ-sdxl.safetensors"},
                             headers=ORIGIN)
    assert r.status_code == 200 and r.json()["installed"] is True


def test_the_gguf_reader_reads_past_long_lists(tmp_path):
    """The old reader gave up at a list of more than 4096 strings (every tokenizer has one): the chat template comes
    after it."""
    from app import gguf
    p = tmp_path / "m.gguf"
    p.write_bytes(gguf_bytes(tokens=20000, template="{{ tools }}"))
    facts = gguf.describe(p)
    assert facts["chat_template"] == "{{ tools }}" and facts["parameters"] == 4_097_000
    p.write_bytes(gguf_bytes(tokens=20000)[:5000])
    assert gguf.describe(p) == {"partial": True}
    p.write_bytes(b"not a gguf at all")
    assert gguf.describe(p) is None
    assert json.dumps(gguf.describe(tmp_path / "m.gguf")) is not None
