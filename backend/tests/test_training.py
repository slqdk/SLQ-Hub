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
    fakes.hf["repos"] = {
        "black-forest-labs/FLUX.2-klein-base-4B": {"sha": "abc123", "tree": [
            {"type": "file", "path": "model_index.json", "size": 40},
            {"type": "file", "path": "flux-2-klein-base-4b.safetensors", "size": 3000},
            {"type": "file", "path": "transformer/diffusion_pytorch_model.safetensors", "size": 3000},
            {"type": "file", "path": "README.md", "size": 10}, {"type": "directory", "path": "vae"}]},
        "ai-toolkit/flux2_vae": {"sha": "fed789", "tree": [{"type": "file", "path": "ae.safetensors", "size": 300}]},
        "Qwen/Qwen3-4B": {"sha": "def456", "tree": [{"type": "file", "path": "model-00001-of-00001.safetensors",
                                                    "size": 2000}, {"type": "file", "path": "LICENSE", "size": 5},
                                                   {"type": "file", "path": "README.md", "size": 10}]}}
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
import sys, time
total = int(open(sys.argv[1]).read().count("x") or 0) or 4
for i in range(1, 5):
    sys.stdout.write(f"\rrun:  {i * 25}%|##| {i}/4 [00:0{i}<00:0{4 - i},  1.00s/it, lr: 1.0e-04 loss: 0.{50 - i}e+00]")
    sys.stdout.flush()
    time.sleep(0.05)
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
