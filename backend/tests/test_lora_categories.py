"""LoRA categories (0.70.0, Søren: "i need trained loras to be in categories in the selection list(chat)"): a LoRA has a
category - its folder in a chat's picture menu, as skills have - set in its settings; a training has one (by what
it learns unless set: People, Things, Styles) that the LoRAs made from it get and follow; LoRAs trained before get
their training's at start-up."""
from __future__ import annotations

import json

from app.training import runs as R

from .test_agent import finish, new_chat, say
from .test_images_models import _install
from .test_loras import KLEIN, ORIGIN, safetensors
from .test_training import photo


async def test_a_lora_takes_a_category(signed_in, app):
    r = await signed_in.post("/hub/api/loras", files={"file": ("cx9020.safetensors", safetensors(KLEIN), "x")},
                             headers=ORIGIN)
    assert r.status_code == 200 and r.json()["category"] == ""
    lid = r.json()["id"]
    r = await signed_in.patch(f"/hub/api/loras/{lid}", json={"category": "  Course   hardware "}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["category"] == "Course hardware"
    assert r.json()["strength"] == 1.0                                      # nothing else changed
    r = await signed_in.patch(f"/hub/api/loras/{lid}", json={"category": "x" * 60}, headers=ORIGIN)
    assert r.json()["category"] == "x" * 40
    r = await signed_in.patch(f"/hub/api/loras/{lid}", json={"category": ""}, headers=ORIGIN)
    assert r.json()["category"] == ""
    listed = (await signed_in.get("/hub/api/loras")).json()["loras"]
    assert [x["category"] for x in listed] == [""]


async def test_trained_loras_go_into_their_trainings_category(signed_in, app):
    hub = app.state.hub
    t, store = hub.extras["training"], hub.extras["loras"]
    rikke = (await signed_in.post("/hub/api/training/runs", json={"name": "Rikke", "trigger": "rkxv",
                                                                  "kind": "person"}, headers=ORIGIN)).json()
    assert rikke["category"] == "People"                                    # by what it learns
    own = (await signed_in.post("/hub/api/training/runs", json={"name": "Oak", "trigger": "oakxv", "kind": "style",
                                                                "category": " Wood  looks "}, headers=ORIGIN)).json()
    assert own["category"] == "Wood looks"
    rid = rikke["id"]
    out = t.root / rid / "output" / rid
    out.mkdir(parents=True)
    for step in (250, 500):
        (out / f"{rid}_{step:09d}.safetensors").write_bytes(safetensors(KLEIN))
    first = (await signed_in.post(f"/hub/api/training/runs/{rid}/use", json={"step": 250}, headers=ORIGIN)).json()
    second = (await signed_in.post(f"/hub/api/training/runs/{rid}/use", json={"step": 500}, headers=ORIGIN)).json()
    assert first["category"] == second["category"] == "People"
    # the training's category changed - also while it works: its LoRAs move with it, others stay
    other = (await signed_in.post("/hub/api/loras", files={"file": ("cx.safetensors", safetensors(KLEIN), "x")},
                                  headers=ORIGIN)).json()
    t._set(rid, state="training")
    r = await signed_in.patch(f"/hub/api/training/runs/{rid}", json={"category": "Family"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["category"] == "Family"
    assert [store.get(x["id"])["category"] for x in (first, second, other)] == ["Family", "Family", ""]
    assert (await signed_in.patch(f"/hub/api/training/runs/{rid}", json={"steps": 2000},
                                  headers=ORIGIN)).status_code == 409    # the rest waits until it stops, as before
    t._set(rid, state="done")
    # a copy keeps a category of its own, and takes its kind's when it learns something else
    await signed_in.post(f"/hub/api/training/runs/{rid}/images", files=[("files", ("p0.jpg", photo(), "image/jpeg"))],
                         headers=ORIGIN)
    same = (await signed_in.post(f"/hub/api/training/runs/{rid}/duplicate", json={"kind": "person"},
                                 headers=ORIGIN)).json()
    assert same["category"] == "Family"
    style = (await signed_in.post(f"/hub/api/training/runs/{rid}/duplicate", json={"kind": "style"},
                                  headers=ORIGIN)).json()
    assert style["category"] == "Styles"


async def test_loras_trained_before_get_a_category_at_start_up(app):
    """Only those that never had one: by their training's category or kind, by the kind in their note when the
    training is gone, else "Trained". A LoRA from a file, or one whose category was cleared, is left alone."""
    hub = app.state.hub
    t, store = hub.extras["training"], hub.extras["loras"]
    run = t.create("Gear", "gearxv", kind="thing")
    raw = json.loads(json.dumps(run))
    raw.pop("category")                                                     # a training made before 0.70.0
    t._save(raw)
    entries = [
        {"id": "gear", "name": "Gear", "base": "flux2-klein", "source": {"kind": "trained", "run": run["id"]}},
        {"id": "anna", "name": "Anna", "base": "flux2-klein", "source": {"kind": "trained", "run": "gone-1"},
         "note": "Trained here on FLUX.2 klein 4B from 20 pictures (person, step 1500 of 1500)."},
        {"id": "mystery", "name": "Mystery", "base": "flux2-klein", "source": {"kind": "trained", "run": "gone-2"}},
        {"id": "file", "name": "From a file", "base": "flux2-klein", "source": {"kind": "upload"}},
        {"id": "cleared", "name": "Cleared", "base": "flux2-klein", "category": "",
         "source": {"kind": "trained", "run": run["id"]}},
    ]
    store.dir.mkdir(parents=True, exist_ok=True)
    for e in entries:
        store.path(e["id"]).write_bytes(safetensors(KLEIN))
    store._save(entries)
    assert t.categorize_loras() == 3
    assert {x["id"]: x["category"] for x in store.list()} == {"gear": "Things", "anna": "People",
                                                              "mystery": "Trained", "file": "", "cleared": ""}
    assert t.categorize_loras() == 0                                         # once
    assert R.KIND_CATEGORY == {"person": "People", "thing": "Things", "style": "Styles"}


async def test_the_assistant_reads_the_category(signed_in, app, fakes):
    hub = app.state.hub
    _install(hub, "flux2-klein-4b")
    lid = (await signed_in.post("/hub/api/loras", files={"file": ("rikke.safetensors", safetensors(KLEIN), "x")},
                                data={"trigger": "rkxv"}, headers=ORIGIN)).json()["id"]
    await signed_in.patch(f"/hub/api/loras/{lid}", json={"category": "Family"}, headers=ORIGIN)
    c = await new_chat(signed_in, settings={"image_model": "flux2-klein-4b", "image_loras": [lid]})
    fakes.llama["replies"] = [{"content": "Hi."}, {"content": "Hi"}]
    await finish(app, await say(signed_in, c["id"], "Hi"))
    tool = next(x for x in fakes.llama["requests"][0]["tools"] if x["function"]["name"] == "image_generate")
    assert "- rikke (category Family; for FLUX.2 klein 4B models" in tool["function"]["description"]
