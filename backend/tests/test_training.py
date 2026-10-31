"""0.50.0 - LoRA training, the admin's alone: pictures in, captions by the chat model (or typed), the base model
fetched before the card is taken, the whole card through the GPU queue while ai-toolkit trains, a checkpoint chosen
by its samples becomes a LoRA. The trainer service (services/trainer/traind.py) is tested on its own with a stand-in
for ai-toolkit."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from app.training import runs as R

from .test_people import add_person, client_from, sign_in

ORIGIN = {"Origin": "http://hub"}
TRAINER = Path(__file__).resolve().parents[2] / "services" / "trainer"


def photo(size=(900, 700), colour=(30, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "JPEG")
    return buf.getvalue()


async def wait_for(cond, timeout: float = 10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


KLEIN_REPOS = {
    "black-forest-labs/FLUX.2-klein-base-4B": {"sha": "abc123", "tree": [
        {"type": "file", "path": "model_index.json", "size": 40},
        {"type": "file", "path": "flux-2-klein-base-4b.safetensors", "size": 3000},
        {"type": "file", "path": "transformer/diffusion_pytorch_model.safetensors", "size": 3000},
        {"type": "file", "path": "README.md", "size": 10}, {"type": "directory", "path": "vae"}]},
    "ai-toolkit/flux2_vae": {"sha": "fed789", "tree": [{"type": "file", "path": "ae.safetensors", "size": 300}]},
    "Qwen/Qwen3-4B": {"sha": "def456", "tree": [{"type": "file", "path": "model-00001-of-00001.safetensors",
                                                "size": 2000}, {"type": "file", "path": "LICENSE", "size": 5},
                                               {"type": "file", "path": "README.md", "size": 10}]}}


async def test_a_lora_is_trained_and_added(signed_in, app, fakes, monkeypatch):
    monkeypatch.setattr(R, "POLL_S", 0.01)
    hub = app.state.hub
    t = hub.extras["training"]
    over = (await signed_in.get("/hub/api/training")).json()
    assert over["service"]["toolkit"] is True and over["base"]["model"] == "flux2-klein-4b"
    for bad in ({"name": "", "trigger": "cx9020x"}, {"name": "CX", "trigger": "two words"},
                {"name": "CX", "trigger": "cx9020x", "kind": "car"}, {"name": "CX", "trigger": "cx9020x", "steps": 10}):
        assert (await signed_in.post("/hub/api/training/runs", json=bad, headers=ORIGIN)).status_code == 400
    run = (await signed_in.post("/hub/api/training/runs", json={"name": "CX9020 controller", "trigger": "cx9020x",
                                                                "kind": "thing", "steps": 500}, headers=ORIGIN)).json()
    rid = run["id"]
    assert (await signed_in.patch(f"/hub/api/training/runs/{rid}", json={"rank": 32}, headers=ORIGIN)).json()["rank"] == 32
    assert (await signed_in.patch(f"/hub/api/training/runs/{rid}", json={"steps": 10}, headers=ORIGIN)).status_code == 400
    assert (await signed_in.get("/hub/api/training")).json()["runs"][0]["id"] == rid
    files = [("files", (f"IMG_{i}.JPG", photo(colour=(i * 40, 90, 160)), "image/jpeg")) for i in range(6)]
    r = await signed_in.post(f"/hub/api/training/runs/{rid}/images", files=files, headers=ORIGIN)
    assert r.status_code == 200 and len(r.json()["added"]) == 6
    small = await signed_in.post(f"/hub/api/training/runs/{rid}/images",
                                 files=[("files", ("tiny.png", photo((100, 80)), "image/png"))], headers=ORIGIN)
    assert small.status_code == 400 and "too small" in small.json()["detail"]
    assert (await signed_in.delete(f"/hub/api/training/runs/{rid}/images/img-5.jpg", headers=ORIGIN)).status_code == 200
    r = await signed_in.post(f"/hub/api/training/runs/{rid}/start", headers=ORIGIN)
    assert r.status_code == 400 and "no caption" in r.json()["detail"]

    # Captions by the chat model (it can see), through the queue; one typed by hand gets the trigger word.
    fakes.llama["replies"] = [{"content": f"The object on a desk, picture {i}."} for i in range(4)] + [{"content": ""}]
    assert (await signed_in.post(f"/hub/api/training/runs/{rid}/captions/auto", json={},
                                 headers=ORIGIN)).status_code == 200
    await wait_for(lambda: t._load(rid)["state"] == "draft")
    detail = (await signed_in.get(f"/hub/api/training/runs/{rid}")).json()
    assert [x["caption"] for x in detail["images_list"]][0] == "cx9020x, The object on a desk, picture 0."
    assert detail["images_list"][4]["caption"] == "" and "no caption for 1 picture" in detail["error"]
    fakes.llama["replies"] = [{"content": "The object on a shelf."}]          # only the one without a caption
    await signed_in.post(f"/hub/api/training/runs/{rid}/captions/auto", json={}, headers=ORIGIN)
    await wait_for(lambda: t._load(rid)["state"] == "draft" and t._load(rid)["error"] == "")
    assert (t.root / rid / "images" / "img-4.txt").read_text() == "cx9020x, The object on a shelf."
    req = fakes.llama["requests"][-1]["messages"][0]["content"]
    assert req[1]["type"] == "image_url" and "the object" in req[0]["text"]
    await signed_in.put(f"/hub/api/training/runs/{rid}/captions", json={"captions": {"img-1.jpg": "on a shelf"}},
                        headers=ORIGIN)
    assert (t.root / rid / "images" / "img-1.txt").read_text() == "cx9020x, on a shelf"

    # Training: the base fetched first (by the Hub, into Hugging Face's cache layout - the trainer is offline), then
    # the card as "train", then ai-toolkit's job with the run's config.
    fakes.hf["repos"] = KLEIN_REPOS
    fakes.trainer["hold"] = True
    r = await signed_in.post(f"/hub/api/training/runs/{rid}/start", headers=ORIGIN)
    assert r.status_code == 200, r.text
    await wait_for(lambda: t._load(rid)["state"] == "training" and rid in fakes.trainer["jobs"])
    assert hub.scheduler.current == "train"
    hf = hub.env.models_dir / "hf" / "hub"
    klein = hf / "models--black-forest-labs--FLUX.2-klein-base-4B"
    assert (klein / "refs" / "main").read_text() == "abc123"
    # only what ai-toolkit reads: the base's single file, not its diffusers folders (that would be twice the size)
    assert (klein / "snapshots/abc123/flux-2-klein-base-4b.safetensors").stat().st_size == 3000
    assert not (klein / "snapshots/abc123/transformer").exists() and not (klein / "snapshots/abc123/README.md").exists()
    assert (hf / "models--ai-toolkit--flux2_vae/snapshots/fed789/ae.safetensors").is_file()
    assert (hf / "models--Qwen--Qwen3-4B/snapshots/def456/model-00001-of-00001.safetensors").is_file()
    assert not (hf / "models--Qwen--Qwen3-4B/snapshots/def456/README.md").exists()
    cfg = fakes.trainer["jobs"][rid]["config"]["config"]["process"][0]
    assert cfg["trigger_word"] == "cx9020x" and cfg["model"]["arch"] == "flux2_klein_4b"
    assert cfg["datasets"][0]["folder_path"] == f"/training/{rid}/images" and cfg["train"]["steps"] == 500
    assert cfg["train"]["optimizer"] == "adamw" and cfg["model"]["quantize"] is False
    assert cfg["train"]["cache_text_embeddings"] is True and cfg["train"]["timestep_type"] == "weighted"
    await wait_for(lambda: (t.live.get(rid) or {}).get("step") == 250)
    assert (await signed_in.get(f"/hub/api/training/runs/{rid}")).json()["progress"]["total"] == 500
    fakes.trainer["hold"] = False
    await wait_for(lambda: t._load(rid)["state"] == "done")
    await wait_for(lambda: hub.scheduler.current != "train" or not hub.scheduler.running)
    detail = (await signed_in.get(f"/hub/api/training/runs/{rid}")).json()
    assert [c["step"] for c in detail["checkpoints"]] == [250, 500] and detail["samples"][0]["step"] == 250
    assert not (t.root / rid / "output" / rid / "optimizer.pt").exists()         # finished: not kept
    pic = await signed_in.get(f"/hub/api/training/runs/{rid}/file", params={"path": detail["samples"][0]["path"]})
    assert pic.status_code == 200 and pic.headers["content-type"] == "image/jpeg"
    assert (await signed_in.get(f"/hub/api/training/runs/{rid}/file",
                                params={"path": "../../hub.db"})).status_code == 404
    lora = (await signed_in.post(f"/hub/api/training/runs/{rid}/use", json={"step": 250}, headers=ORIGIN)).json()
    assert lora["base"] == "flux2-klein" and lora["trigger"] == "cx9020x" and lora["name"] == "CX9020 controller (step 250)"
    assert lora["source"] == {"kind": "trained", "run": rid, "step": 250}
    assert hub.extras["loras"].get(lora["id"]) is not None

    # Cancelled while it trains: the trainer is told, the card is given back.
    run2 = (await signed_in.post("/hub/api/training/runs", json={"name": "Style", "trigger": "stylx",
                                                                 "kind": "style"}, headers=ORIGIN)).json()
    rid2 = run2["id"]
    await signed_in.post(f"/hub/api/training/runs/{rid2}/images",
                         files=[("files", (f"s{i}.png", photo(), "image/png")) for i in range(5)], headers=ORIGIN)
    t.set_captions(rid2, {f"s{i}.jpg": "a fox" for i in range(5)})
    fakes.trainer["hold"] = True
    await signed_in.post(f"/hub/api/training/runs/{rid2}/start", headers=ORIGIN)
    await wait_for(lambda: t._load(rid2)["state"] == "training" and rid2 in fakes.trainer["jobs"])
    assert fakes.trainer["jobs"][rid2]["config"]["config"]["process"][0]["train"]["lr"] == 8e-5
    await signed_in.post(f"/hub/api/training/runs/{rid2}/cancel", headers=ORIGIN)
    await wait_for(lambda: (t.live.get(rid2) or {}).get("what") == "stopping")
    assert any(j.need == "train" for j in hub.scheduler.running.values())     # held until it has really stopped
    await wait_for(lambda: t._load(rid2)["state"] == "cancelled")
    assert fakes.trainer["cancels"].count(rid2) == 1
    assert (await signed_in.delete(f"/hub/api/training/runs/{rid2}", headers=ORIGIN)).status_code == 200

    # The trainer restarted under a training: the run fails with the reason, and the card is given back.
    fakes.trainer.update(hold=True, forget=False)
    await signed_in.post(f"/hub/api/training/runs/{rid}/start", headers=ORIGIN)
    await wait_for(lambda: t._load(rid)["state"] == "training")
    fakes.trainer["forget"] = True
    await wait_for(lambda: t._load(rid)["state"] == "failed")
    assert "restart" in t._load(rid)["error"]
    await wait_for(lambda: not hub.scheduler.running)
    fakes.trainer.update(hold=False, forget=False)

    # Free space: the checkpoints and caches go; pictures, captions, samples and the added LoRA stay.
    (t.root / rid / "images" / "_latent_cache").mkdir(exist_ok=True)
    (t.root / rid / "images" / "_latent_cache" / f"img-0_{'A' * 22}.safetensors").write_bytes(b"x" * 500)
    before = (await signed_in.get(f"/hub/api/training/runs/{rid}")).json()
    assert before["freeable"] > 500 and [r["size"] for r in t.list() if r["id"] == rid][0] > before["freeable"]
    freed = (await signed_in.post(f"/hub/api/training/runs/{rid}/free", headers=ORIGIN)).json()["freed"]
    assert freed == before["freeable"]
    after = (await signed_in.get(f"/hub/api/training/runs/{rid}")).json()
    assert after["checkpoints"] == [] and after["freeable"] == 0 and after["samples"] and len(after["images_list"]) == 5
    assert not (t.root / rid / "images" / "_latent_cache").exists() and hub.extras["loras"].get(lora["id"]) is not None

    # A file ai-toolkit expects that Hugging Face does not have: said plainly, nothing half-written.
    with pytest.raises(R.TrainingError, match="no flux-2-klein-base-9b.safetensors"):
        await t.fetch("black-forest-labs/FLUX.2-klein-base-4B", None, ("flux-2-klein-base-9b.safetensors",))

    # The trainer not running: a clear refusal.
    fakes.trainer["down"] = True
    r = await signed_in.post(f"/hub/api/training/runs/{rid}/start", headers=ORIGIN)
    assert r.status_code == 503 and "COMPOSE_PROFILES=training" in r.json()["detail"]

    # A person: none of it.
    await add_person(signed_in)
    async with client_from(app) as phone:
        await sign_in(phone)
        assert (await phone.get("/hub/api/training")).status_code in (401, 403)
        assert (await phone.post("/hub/api/training/runs", json={"name": "x", "trigger": "xxxy"},
                                 headers=ORIGIN)).status_code in (401, 403)


async def test_a_training_left_from_before_a_restart_is_stopped_first(app, fakes, monkeypatch):
    """ai-toolkit still at work when the Hub starts: the run is ended, the job stopped, and the card held as "train"
    through the queue until the trainer is idle - no model may load into memory the training still holds."""
    monkeypatch.setattr(R, "STOP_POLL_S", 0.01)
    hub = app.state.hub
    t = hub.extras["training"]
    run = t.create("Left over", "leftx", "thing", 500)
    t._set(run["id"], state="training")
    fakes.trainer.update(busy_looks=5, busy_job=run["id"])
    await t.resume_after_restart()
    assert t._load(run["id"])["state"] == "failed" and "restarted" in t._load(run["id"])["error"]
    await asyncio.wait_for(t.tasks["-restart"], 10)
    assert fakes.trainer["cancels"] == [run["id"]] and fakes.trainer["busy_looks"] == 0
    assert hub.scheduler.current == "train" and not hub.scheduler.running      # it had the card, and gave it back


async def test_a_lora_is_trained_on_an_sdxl_checkpoint(signed_in, app, fakes, monkeypatch):
    """0.56.0: an SDXL checkpoint the Hub has (RealVisXL 5.0) as the base - ai-toolkit's arch "sdxl" on the file as
    the trainer sees it, SDXL base's config files fetched (not its weights), and the LoRA marked SDXL."""
    monkeypatch.setattr(R, "POLL_S", 0.01)
    hub = app.state.hub
    t = hub.extras["training"]
    monkeypatch.setattr(t, "sdxl_checkpoints", lambda: [{"id": "realvisxl-v5-0", "name": "RealVisXL V5.0",
                                                         "path": "hub/realvisxl-v5-0.safetensors"}])
    over = (await signed_in.get("/hub/api/training")).json()
    assert [b["id"] for b in over["bases"]] == ["flux2-klein-4b", "flux2-klein-9b", "sdxl:realvisxl-v5-0"]
    for bad in ("sdxl:gone", "sd15"):
        r = await signed_in.post("/hub/api/training/runs", json={"name": "X", "trigger": "rkxv", "base": bad},
                                 headers=ORIGIN)
        assert r.status_code == 400
    run = (await signed_in.post("/hub/api/training/runs", json={"name": "Mira", "trigger": "mirx", "kind": "person",
                                                                "steps": 500, "base": "sdxl:realvisxl-v5-0"},
                                headers=ORIGIN)).json()
    rid = run["id"]
    assert run["base"] == "sdxl" and run["checkpoint_path"] == "hub/realvisxl-v5-0.safetensors"
    t.add_images(rid, [(f"m{i}.jpg", photo()) for i in range(5)])
    t.set_captions(rid, {f"m{i}.jpg": "on a beach" for i in range(5)})
    fakes.hf["repos"] = {"stabilityai/stable-diffusion-xl-base-1.0": {"sha": "5d1", "tree": [
        {"type": "file", "path": "model_index.json", "size": 60}, {"type": "file", "path": "unet/config.json", "size": 80},
        {"type": "file", "path": "tokenizer_2/merges.txt", "size": 50},
        {"type": "file", "path": "sd_xl_base_1.0.safetensors", "size": 6000},
        {"type": "file", "path": "unet/diffusion_pytorch_model.safetensors", "size": 5000}]}}
    fakes.trainer["hold"] = True
    assert (await signed_in.post(f"/hub/api/training/runs/{rid}/start", headers=ORIGIN)).status_code == 200
    await wait_for(lambda: t._load(rid)["state"] == "training" and rid in fakes.trainer["jobs"])
    snap = hub.env.models_dir / "hf" / "hub" / "models--stabilityai--stable-diffusion-xl-base-1.0" / "snapshots" / "5d1"
    assert (snap / "model_index.json").is_file() and (snap / "unet/config.json").is_file()
    assert (snap / "tokenizer_2/merges.txt").is_file() and not list(snap.rglob("*.safetensors"))
    cfg = fakes.trainer["jobs"][rid]["config"]["config"]["process"][0]
    # No vae_dtype (0.59.3): ai-toolkit sets the model dtype to the train dtype (bf16, which does not overflow) and
    # casts the VAE to it before caching latents; 0.59.2's fp32 handed fp32 pictures to a bf16 VAE and crashed.
    assert cfg["model"] == {"name_or_path": "/models/comfy/checkpoints/hub/realvisxl-v5-0.safetensors", "arch": "sdxl",
                            "quantize": False, "quantize_te": False}
    assert cfg["train"]["noise_scheduler"] == "ddpm" and cfg["sample"]["sampler"] == "k_dpmsolver++"   # 0.59.4
    assert "timestep_type" not in cfg["train"] and "cache_text_embeddings" not in cfg["train"]
    assert cfg["sample"]["neg"] == R.SDXL_NEGATIVE and cfg["sample"]["prompts"][0].startswith("mirx, ")
    fakes.trainer["hold"] = False
    await wait_for(lambda: t._load(rid)["state"] == "done")
    assert t.detail(rid)["base_label"] == "RealVisXL V5.0 (SDXL)"
    lora = (await signed_in.post(f"/hub/api/training/runs/{rid}/use", json={}, headers=ORIGIN)).json()
    assert lora["base"] == "sdxl" and "RealVisXL V5.0 (SDXL)" in lora["note"]
    copy = t.duplicate(rid)                                                    # a v2 keeps the base
    assert copy["base"] == "sdxl" and copy["checkpoint"] == "realvisxl-v5-0"
    # 0.60.0: a copy trained on another model - the SDXL person moved to FLUX.2 klein with pictures and captions
    r = await signed_in.post(f"/hub/api/training/runs/{rid}/duplicate", json={"name": "Mira klein",
                                                                            "base": "flux2-klein-4b"}, headers=ORIGIN)
    moved = r.json()
    assert r.status_code == 200 and moved["base"] == "flux2-klein-4b" and "checkpoint" not in moved
    assert sorted(p.name for p in (t.root / moved["id"] / "images").iterdir()) == \
        sorted(f"m{i}.{ext}" for i in range(5) for ext in ("jpg", "txt"))
    bad = await signed_in.post(f"/hub/api/training/runs/{rid}/duplicate", json={"base": "sdxl:gone"}, headers=ORIGIN)
    assert bad.status_code == 400 and "not downloaded" in bad.json()["detail"]


async def test_a_stopped_training_goes_on_from_its_last_checkpoint(signed_in, app, fakes, monkeypatch):
    """0.58.0: Stop is a pause - the checkpoints and ai-toolkit's optimizer state stay, and Continue gives ai-toolkit
    the same folder, where it goes on from the newest checkpoint. A finished run goes on the same way once it has more
    steps (its last file becomes a numbered checkpoint first). Another rank cannot go on; Train again starts over."""
    monkeypatch.setattr(R, "POLL_S", 0.01)
    t = app.state.hub.extras["training"]
    fakes.hf["repos"] = KLEIN_REPOS
    rid = t.create("Rikke", "rkxv", "person", 1000)["id"]
    t.add_images(rid, [(f"p{i}.jpg", photo()) for i in range(5)])
    t.set_captions(rid, {f"p{i}.jpg": "a woman in a garden" for i in range(5)})
    out = t.root / rid / "output" / rid

    async def go(action: str, finish: bool = True) -> dict:
        fakes.trainer["hold"] = not finish
        r = await signed_in.post(f"/hub/api/training/runs/{rid}/{action}", headers=ORIGIN)
        assert r.status_code == 200, r.text
        if finish:
            await wait_for(lambda: t._load(rid)["state"] == "done")
        else:
            await wait_for(lambda: (t.live.get(rid) or {}).get("what") == "training")
        return fakes.trainer["jobs"][rid]

    job = await go("start", finish=False)
    assert job["start"] == 0
    await signed_in.post(f"/hub/api/training/runs/{rid}/cancel", headers=ORIGIN)
    await wait_for(lambda: t._load(rid)["state"] == "cancelled")
    detail = (await signed_in.get(f"/hub/api/training/runs/{rid}")).json()
    assert [c["step"] for c in detail["checkpoints"]] == [250, 500, 750] and detail["resume_step"] == 750
    assert (out / "optimizer.pt").is_file()                                   # kept for an exact continue

    job = await go("continue")
    assert job["start"] == 750 and job["optimizer"] is True
    detail = (await signed_in.get(f"/hub/api/training/runs/{rid}")).json()
    assert [c["step"] for c in detail["checkpoints"]] == [250, 500, 750, 1000] and detail["resume_step"] is None
    assert not (out / "optimizer.pt").exists() and t._load(rid)["trained"] == 1000
    r = await signed_in.post(f"/hub/api/training/runs/{rid}/continue", headers=ORIGIN)
    assert r.status_code == 400 and "start over" in r.json()["detail"]

    # Train further: more steps, and the last checkpoint keeps its step.
    await signed_in.patch(f"/hub/api/training/runs/{rid}", json={"steps": 1500}, headers=ORIGIN)
    detail = (await signed_in.get(f"/hub/api/training/runs/{rid}")).json()
    assert detail["checkpoints"][-1]["step"] == 1000 and detail["resume_step"] == 1000
    job = await go("continue")
    assert job["start"] == 1000 and job["optimizer"] is False
    assert [c["step"] for c in t.checkpoints(rid)] == [250, 500, 750, 1000, 1250, 1500]
    assert (out / f"{rid}_000001000.safetensors").is_file()

    # Another rank cannot load these checkpoints; Train again empties the folder.
    await signed_in.patch(f"/hub/api/training/runs/{rid}", json={"steps": 2000, "rank": 32}, headers=ORIGIN)
    assert t.detail(rid)["resume_step"] is None
    assert (await signed_in.post(f"/hub/api/training/runs/{rid}/continue", headers=ORIGIN)).status_code == 400
    job = await go("start")
    assert job["start"] == 0 and [c["step"] for c in t.checkpoints(rid)] == [250, 500, 750, 1000, 1250, 1500,
                                                                             1750, 2000]
    assert t._load(rid)["trained_rank"] == 32


async def test_a_training_is_duplicated_with_a_new_trigger_word(signed_in, app):
    """Duplicate (0.51.0): the pictures, captions and settings into a new training - not its results or ai-toolkit's
    caches - with a new trigger word put in the captions; the name counts up."""
    t = app.state.hub.extras["training"]
    run = (await signed_in.post("/hub/api/training/runs", json={"name": "Rikke", "trigger": "lora", "kind": "person",
                                                                "steps": 2000, "rank": 32}, headers=ORIGIN)).json()
    rid = run["id"]
    await signed_in.post(f"/hub/api/training/runs/{rid}/images",
                         files=[("files", (f"p{i}.jpg", photo(), "image/jpeg")) for i in range(3)], headers=ORIGIN)
    t.set_captions(rid, {"p0.jpg": "on a bench in the park, Lora's bike behind", "p1.jpg": "explorative light"})
    src = t.root / rid
    (src / "output" / rid).mkdir(parents=True)
    (src / "output" / rid / f"{rid}.safetensors").write_bytes(b"x")
    (src / "images" / "_latent_cache").mkdir()
    (src / "images" / "_latent_cache" / f"p0_{'A' * 22}.safetensors").write_bytes(b"x")
    r = await signed_in.post(f"/hub/api/training/runs/{rid}/duplicate", json={"trigger": "waterx55"}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    copy = r.json()
    assert (copy["name"], copy["trigger"], copy["kind"], copy["steps"], copy["rank"]) == \
        ("Rikke v2", "waterx55", "person", 2000, 32)
    d = t.detail(copy["id"])
    assert [im["name"] for im in d["images_list"]] == ["p0.jpg", "p1.jpg", "p2.jpg"]
    assert d["images_list"][0]["caption"] == "on a bench in the park, waterx55's bike behind"   # had it as a word
    assert d["images_list"][1]["caption"] == "waterx55, explorative light"          # "lora" inside a word is not it
    assert d["checkpoints"] == [] and not (t.root / copy["id"] / "images" / "_latent_cache").exists()
    assert t.detail(rid)["images_list"][1]["caption"] == "lora, explorative light"   # the original is untouched
    again = (await signed_in.post(f"/hub/api/training/runs/{copy['id']}/duplicate", json={}, headers=ORIGIN)).json()
    assert again["name"] == "Rikke v3" and again["trigger"] == "waterx55"
    bad = await signed_in.post(f"/hub/api/training/runs/{rid}/duplicate", json={"trigger": "two words"}, headers=ORIGIN)
    assert bad.status_code == 400


def test_a_removed_picture_takes_its_caches_along(app):
    """ai-toolkit keys its latent cache by the picture's file name, not its content: a picture removed, and another
    added under the same name, must not train on the old one's latents."""
    t = app.state.hub.extras["training"]
    rid = t.create("Cache", "cachx", "thing", 500)["id"]
    t.add_images(rid, [("img-1.jpg", photo()), ("img-10.jpg", photo())])
    cache = t.root / rid / "images" / "_latent_cache"
    cache.mkdir()
    for name in (f"img-1_{'b' * 22}.safetensors", f"img-10_{'c' * 22}.safetensors"):
        (cache / name).write_bytes(b"x")
    t.remove_image(rid, "img-1.jpg")
    assert sorted(f.name for f in cache.iterdir()) == [f"img-10_{'c' * 22}.safetensors"]


# ---------------------------------------------------------------- the trainer service

FAKE_TOOLKIT = r'''
import os, sys, time
name = os.path.basename(os.path.dirname(sys.argv[1]))      # ai-toolkit's bar has the job's name; the run's folder
for i in range(1, 36, 17):                                  # a caching bar first: not training steps
    sys.stdout.write(f"\rCaching latents to disk: {i * 2}%|#| {i}/35 [00:0{i % 9}<00:01, 9.0it/s]")
print("")
for i in range(1, 5):
    sys.stdout.write(f"\r{name}:  {i * 25}%|##| {i}/4 [00:0{i}<00:0{4 - i},  1.00s/it, lr: 1.0e-04 loss: 0.{50 - i}e+00]")
    sys.stdout.flush()
    time.sleep(0.05)
sys.stdout.write("\r 40%|####| 10/25 [00:02<00:03,  4.0it/s]")      # a sample picture's bar: not a step either
print("")
print("\nSaving at step 4")
if "slow" in open(sys.argv[1]).read():
    time.sleep(30)
'''


@pytest.fixture
def traind(tmp_path, monkeypatch):
    toolkit = tmp_path / "toolkit"
    toolkit.mkdir()
    (toolkit / "run.py").write_text(FAKE_TOOLKIT)
    root = tmp_path / "training"
    (root / "r1").mkdir(parents=True)
    monkeypatch.setenv("AI_TOOLKIT", str(toolkit))
    monkeypatch.setenv("TRAINER_ROOT", str(root))
    spec = importlib.util.spec_from_file_location("traind_test", TRAINER / "traind.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.subprocess, "Popen", _python_popen(mod.subprocess.Popen))
    return mod, root


def _python_popen(real):
    def popen(cmd, **kw):                       # "python" is this interpreter here
        return real([sys.executable, *cmd[1:]], **kw)
    return popen


def test_the_trainer_follows_ai_toolkit(traind):
    mod, root = traind
    assert mod.start_job({"id": "r1", "config": {"x": 1}, "dir": "/etc"})[0] == 400       # outside its root
    code, body = mod.start_job({"id": "r1", "config": {"job": "extension"}, "dir": str(root / "r1")})
    assert code == 200, body
    assert mod.start_job({"id": "r2", "config": {}, "dir": str(root / "r1")})[0] == 409    # one at a time
    end = time.monotonic() + 10
    while mod.jobs["r1"]["state"] == "running" and time.monotonic() < end:
        time.sleep(0.05)
    job = mod.jobs["r1"]
    assert job["state"] == "done" and (job["step"], job["total"]) == (4, 4) and job["loss"] == 0.46
    assert job["log"] == ["Saving at step 4"] and (root / "r1" / "config.json").is_file()


def test_ai_toolkit_runs_without_expandable_segments(traind, monkeypatch):
    mod, _ = traind
    monkeypatch.setenv("PYTORCH_HIP_ALLOC_CONF", "expandable_segments:True")
    monkeypatch.delenv("TRAINER_ALLOC_CONF", raising=False)
    env = mod.toolkit_env()
    assert "PYTORCH_HIP_ALLOC_CONF" not in env and env["PYTHONUNBUFFERED"] == "1"
    monkeypatch.setenv("TRAINER_ALLOC_CONF", "garbage_collection_threshold:0.8")
    assert mod.toolkit_env()["PYTORCH_HIP_ALLOC_CONF"] == "garbage_collection_threshold:0.8"


def test_the_trainer_stops_a_job(traind):
    mod, root = traind
    assert mod.start_job({"id": "r1", "config": {"slow": True}, "dir": str(root / "r1")})[0] == 200
    time.sleep(0.4)
    assert mod.cancel_job("r1") == (200, {"ok": True, "state": "stopping"})
    assert mod.busy()                               # until ai-toolkit has exited: the card is still in use
    end = time.monotonic() + 10
    while mod.jobs["r1"]["state"] == "stopping" and time.monotonic() < end:
        time.sleep(0.05)
    assert mod.jobs["r1"]["proc"].poll() is not None and mod.jobs["r1"]["state"] == "cancelled"
    assert not mod.busy()


async def test_a_lora_is_trained_on_klein_9b(signed_in, app, fakes, monkeypatch):
    """0.61.0: FLUX.2 klein 9B as the base. Gated on Hugging Face: without HF_TOKEN a start stops before anything is
    fetched, with the steps to take; with a token its account has not accepted the licence for, Hugging Face's 403 is
    explained. Then its base file, Qwen3-8B and the shared VAE are fetched, the transformer and text encoder are
    quantised to 8 bit (18 GB in bf16 does not fit the card), and the LoRA belongs to the 9B family."""
    monkeypatch.setattr(R, "POLL_S", 0.01)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    hub = app.state.hub
    t = hub.extras["training"]
    over = (await signed_in.get("/hub/api/training")).json()
    nine = next(b for b in over["bases"] if b["id"] == "flux2-klein-9b")
    assert nine["family"] == "flux2-klein-9b" and nine["lora_for"] == "FLUX.2 [klein] 9B" and nine["ready"] is False
    assert nine["gated"] == "https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9B" and over["hf_token"] is False
    assert "gated" not in over["bases"][0] and over["bases"][0]["download_gb"] == 16
    run = (await signed_in.post("/hub/api/training/runs", json={"name": "Rikke 9B", "trigger": "rkxv", "kind": "person",
                                                                "steps": 500, "base": "flux2-klein-9b"},
                                headers=ORIGIN)).json()
    rid = run["id"]
    assert run["base"] == "flux2-klein-9b" and t.detail(rid)["base_label"] == "FLUX.2 klein 9B"
    t.add_images(rid, [(f"p{i}.jpg", photo()) for i in range(5)])
    t.set_captions(rid, {f"p{i}.jpg": "a woman in a garden" for i in range(5)})
    repos = {"black-forest-labs/FLUX.2-klein-base-9B": {"sha": "9b9b9b", "gated": "auto", "token": "hf_ok", "tree": [
                 {"type": "file", "path": "flux-2-klein-base-9b.safetensors", "size": 4000},
                 {"type": "file", "path": "transformer/diffusion_pytorch_model-00001-of-00002.safetensors",
                  "size": 2000}, {"type": "file", "path": "LICENSE.md", "size": 10}]},
             "ai-toolkit/flux2_vae": KLEIN_REPOS["ai-toolkit/flux2_vae"],
             "Qwen/Qwen3-8B": {"sha": "8b8b8b", "tree": [
                 {"type": "file", "path": "model-00001-of-00002.safetensors", "size": 1500},
                 {"type": "file", "path": "model-00002-of-00002.safetensors", "size": 1500},
                 {"type": "file", "path": "tokenizer.json", "size": 20}]}}
    fakes.hf["repos"] = repos
    fakes.hf["downloaded"] = []
    r = await signed_in.post(f"/hub/api/training/runs/{rid}/start", headers=ORIGIN)
    assert r.status_code == 200, r.text
    await wait_for(lambda: t._load(rid)["state"] == "failed")
    err = t._load(rid)["error"]
    assert "gated" in err and "HF_TOKEN" in err and "huggingface.co/black-forest-labs/FLUX.2-klein-base-9B" in err
    assert fakes.hf["downloaded"] == [] and rid not in fakes.trainer["jobs"]     # nothing fetched, the card not taken

    monkeypatch.setenv("HF_TOKEN", "hf_someone_else")                # a token whose account has not accepted it
    await signed_in.post(f"/hub/api/training/runs/{rid}/start", headers=ORIGIN)
    await wait_for(lambda: t._load(rid)["state"] == "failed")
    assert "accept its licence" in t._load(rid)["error"] and "account the token belongs to" in t._load(rid)["error"]
    # a token Hugging Face does not take at all (wrong or expired) answers 401: said as such, not as the licence
    assert "did not take the HF_TOKEN" in R.gated_help("black-forest-labs/FLUX.2-klein-base-9B", True, 401)

    monkeypatch.setenv("HF_TOKEN", "hf_ok")
    assert (await signed_in.get("/hub/api/training")).json()["hf_token"] is True
    fakes.trainer["hold"] = True
    assert (await signed_in.post(f"/hub/api/training/runs/{rid}/start", headers=ORIGIN)).status_code == 200
    await wait_for(lambda: t._load(rid)["state"] == "training" and rid in fakes.trainer["jobs"])
    hf = hub.env.models_dir / "hf" / "hub"
    base = hf / "models--black-forest-labs--FLUX.2-klein-base-9B" / "snapshots" / "9b9b9b"
    assert (base / "flux-2-klein-base-9b.safetensors").stat().st_size == 4000 and not (base / "transformer").exists()
    assert (hf / "models--Qwen--Qwen3-8B/snapshots/8b8b8b/model-00002-of-00002.safetensors").is_file()
    assert (hf / "models--ai-toolkit--flux2_vae/snapshots/fed789/ae.safetensors").is_file()
    cfg = fakes.trainer["jobs"][rid]["config"]["config"]["process"][0]
    assert cfg["model"] == {"name_or_path": "black-forest-labs/FLUX.2-klein-base-9B", "arch": "flux2_klein_9b",
                            "quantize": True, "qtype": "qfloat8", "quantize_te": True, "qtype_te": "qfloat8",
                            "low_vram": False}
    assert cfg["train"]["cache_text_embeddings"] is True and cfg["sample"]["guidance_scale"] == 4
    fakes.trainer["hold"] = False
    await wait_for(lambda: t._load(rid)["state"] == "done")
    lora = (await signed_in.post(f"/hub/api/training/runs/{rid}/use", json={}, headers=ORIGIN)).json()
    assert lora["base"] == "flux2-klein-9b" and "FLUX.2 klein 9B" in lora["note"]
    assert t.duplicate(rid)["base"] == "flux2-klein-9b"                        # a v2 stays on 9B
    assert t.duplicate(rid, base="flux2-klein-4b")["base"] == "flux2-klein-4b"

    # Started again with the files here: no token needed for what is already fetched.
    monkeypatch.delenv("HF_TOKEN")
    fakes.hf["downloaded"] = []
    await signed_in.post(f"/hub/api/training/runs/{rid}/start", headers=ORIGIN)
    await wait_for(lambda: t._load(rid)["state"] == "done")
    assert fakes.hf["downloaded"] == []


def test_sdxl_samples_are_made_as_the_hub_makes_that_models_pictures(app, monkeypatch):
    """0.61.0: the samples of a training on Pony get its score tags and its own (empty) negative prompt, as Pony's
    pictures in a chat do - without them Pony's samples looked worse than its LoRA. They are kept with the run when
    it is made: a training made before keeps the samples it had, so its steps stay comparable."""
    t = app.state.hub.extras["training"]
    pony = {"id": "pony-v6", "name": "Pony Diffusion V6 XL", "path": "hub/pony-v6.safetensors",
            "prefix": "score_9, score_8_up, score_7_up,", "negative": ""}            # a trailing comma, as typed
    monkeypatch.setattr(t, "sdxl_checkpoints", lambda: [pony])
    run = t.create("Mira", "mirx", "person", 500, base="sdxl:pony-v6")
    assert run["sample_prefix"] == "score_9, score_8_up, score_7_up," and run["sample_negative"] == ""
    cfg = t.config(t._load(run["id"]))["config"]["process"][0]
    assert cfg["sample"]["prompts"][0] == "score_9, score_8_up, score_7_up, mirx, smiling, a portrait photo outdoors " \
                                          "in soft light"
    assert cfg["sample"]["neg"] == "" and cfg["model"]["name_or_path"].endswith("hub/pony-v6.safetensors")
    assert t.duplicate(run["id"])["sample_prefix"] == pony["prefix"]               # a v2 takes the model's again
    older = t._set(run["id"], sample_prefix=None, sample_negative=None)              # as a run from before 0.61.0
    older.pop("sample_prefix"), older.pop("sample_negative")
    cfg = t.config(older)["config"]["process"][0]
    assert cfg["sample"]["neg"] == R.SDXL_NEGATIVE and cfg["sample"]["prompts"][0].startswith("mirx, ")
