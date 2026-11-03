"""Movies (0.72.0, Søren: "Does it make sense to make a Movie workflow section that can do this? With checkpoints?"):
a series' look, cast and sets; an episode from a premise to a storyboard (the chat model), keyframes (the Hub sets
each shot's LoRAs), clips and a silent preview - each stage a checkpoint, and what a result was made from stamped on
it, so a change makes only what depends on it out of date."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re

from PIL import Image

from app.movies import studio as S

from .mocks import _png
from .test_audio import install
from .test_images_models import _install
from .test_loras import KLEIN, ORIGIN, safetensors
from .test_people import add_person, client_from, sign_in

RED, BLUE, GREEN, GREY = (200, 30, 30, 255), (30, 30, 200, 255), (30, 200, 30, 255), (120, 120, 120, 255)
API = "/hub/api/movies"


def colour(data: bytes) -> tuple:
    with Image.open(io.BytesIO(data)) as img:
        return img.convert("RGBA").getpixel((2, 2))


def said(lines: list[dict]) -> list[dict]:
    """Who says what (the page's lines carry their timing and take too, 0.73.0)."""
    return [{k: ln[k] for k in ("who", "name", "text")} for ln in lines]


def nodes(graph: dict, kind: str) -> list[dict]:
    return [n["inputs"] for n in graph.values() if n["class_type"] == kind]


def loras_in(graph: dict) -> list[tuple[str, float]]:
    return [(n["inputs"]["lora_name"], n["inputs"].get("strength_model")) for n in graph.values()
            if "lora_name" in (n.get("inputs") or {})]


def prompt_in(graph: dict) -> str:
    return " | ".join(n["inputs"]["text"] for n in graph.values()
                      if isinstance((n.get("inputs") or {}).get("text"), str))


async def done(app, sid: str, key: str) -> None:
    task = app.state.hub.extras["movies"].tasks[(sid, key)]
    await asyncio.wait_for(asyncio.shield(task), 15)


async def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


async def lora(client, name: str, trigger: str) -> str:
    return (await ok(await client.post("/hub/api/loras", files={"file": (f"{name}.safetensors", safetensors(KLEIN),
                                                                         "x")},
                                       data={"trigger": trigger}, headers=ORIGIN)))["id"]


async def series(client, app) -> dict:
    """A series on FLUX.2 klein 4B with a cartoon LoRA, Far (his own LoRA), Mor (words only) and a kitchen."""
    _install(app.state.hub, "flux2-klein-4b")
    far_lora, toon = await lora(client, "far", "farxv"), await lora(client, "toon", "toonxv")
    s = await ok(await client.post(API, json={"name": "Hjemme hos os"}, headers=ORIGIN))
    sid = s["id"]
    s = await ok(await client.patch(f"{API}/{sid}", json={"style": {"model": "flux2-klein-4b", "lora": toon,
                                                                     "strength": 0.7, "variants": 2}}, headers=ORIGIN))
    s = await ok(await client.post(f"{API}/{sid}/cast", json={"name": "Far", "look": "tall, glasses, green jumper",
                                                              "lora": far_lora, "strength": 0.9}, headers=ORIGIN))
    s = await ok(await client.post(f"{API}/{sid}/cast", json={"name": "Mor", "look": "curly red hair, yellow dress"},
                                   headers=ORIGIN))
    s = await ok(await client.post(f"{API}/{sid}/sets", json={"name": "Køkken", "look": "a small Danish kitchen, "
                                                              "white cupboards, a big fridge"}, headers=ORIGIN))
    return {"sid": sid, "far_lora": far_lora, "toon": toon, "far": s["cast"][0]["id"], "mor": s["cast"][1]["id"],
            "kitchen": s["sets"][0]["id"]}


async def make(client, app, sid: str, kind: str, iid: str) -> dict:
    await ok(await client.post(f"{API}/{sid}/{kind}/{iid}/make", headers=ORIGIN))
    await done(app, sid, f"{kind}:{iid}")
    return await ok(await client.get(f"{API}/{sid}"))


async def test_a_series_its_cast_and_a_sheet_made_with_their_loras(signed_in, app, fakes):
    x = await series(signed_in, app)
    sid = x["sid"]
    listed = await ok(await signed_in.get(API))
    assert [s["name"] for s in listed["series"]] == ["Hjemme hos os"]
    opts = listed["options"]
    assert any(m["id"] == "flux2-klein-4b" and m["combine"] for m in opts["models"])
    assert {lo["id"]: lo["base"] for lo in opts["loras"]} == {x["far_lora"]: "flux2-klein", x["toon"]: "flux2-klein"}
    # names are unique, a LoRA must be there, the format one of three
    assert (await signed_in.post(f"{API}/{sid}/cast", json={"name": "far"}, headers=ORIGIN)).status_code == 400
    assert (await signed_in.patch(f"{API}/{sid}/cast/{x['mor']}", json={"lora": "gone"},
                                  headers=ORIGIN)).status_code == 400
    assert (await signed_in.patch(f"{API}/{sid}", json={"style": {"format": "wide"}},
                                  headers=ORIGIN)).status_code == 400
    # Far's sheet: two pictures (the series' variants) with his LoRA and the style LoRA, portrait 720x1280
    fakes.comfy["pictures"] = [_png((16, 16), RED), _png((16, 16), BLUE)]
    s = await make(signed_in, app, sid, "cast", x["far"])
    g = fakes.comfy["prompts"][0]
    assert loras_in(g) == [(f"hub/{x['far_lora']}.safetensors", 0.9), (f"hub/{x['toon']}.safetensors", 0.7)]
    text = prompt_in(g)
    assert "farxv" in text and "toonxv" in text and "Character design sheet of Far: tall, glasses, green jumper" in text
    assert "Style: 2D cartoon" in text
    assert [(n["width"], n["height"]) for n in nodes(g, "EmptyFlux2LatentImage")] == [(720, 1280)]
    far = s["cast"][0]
    assert len(far["variants"]) == 2 and far["sheet"] == "" and far["sheet_state"] == "none"
    # chosen; the files are the series' own
    second = far["variants"][1]["file"]
    s = await ok(await signed_in.post(f"{API}/{sid}/cast/{x['far']}/pick", json={"file": second}, headers=ORIGIN))
    assert s["cast"][0]["sheet"] == second and s["cast"][0]["sheet_state"] == "fresh"
    r = await signed_in.get(f"{API}/{sid}/file", params={"path": second})
    assert r.status_code == 200 and colour(r.content) == BLUE
    for bad in ("../series.json", "cast/../../x.png", "series.json", "/etc/passwd"):
        assert (await signed_in.get(f"{API}/{sid}/file", params={"path": bad})).status_code == 404
    # a change to his look makes the sheet out of date; keeping it as it is takes the change
    s = await ok(await signed_in.patch(f"{API}/{sid}/cast/{x['far']}", json={"look": "tall, glasses, red jumper"},
                                       headers=ORIGIN))
    assert s["cast"][0]["sheet_state"] == "stale"
    s = await ok(await signed_in.post(f"{API}/{sid}/cast/{x['far']}/pick", json={"keep": True}, headers=ORIGIN))
    assert s["cast"][0]["sheet_state"] == "fresh"
    # Mor has no LoRA: only the style LoRA; the set's picture: the style LoRA, no one in it
    fakes.comfy["pictures"] = [_png((16, 16), GREEN)]
    await make(signed_in, app, sid, "cast", x["mor"])
    assert loras_in(fakes.comfy["prompts"][-1]) == [(f"hub/{x['toon']}.safetensors", 0.7)]
    await make(signed_in, app, sid, "sets", x["kitchen"])
    assert loras_in(fakes.comfy["prompts"][-1]) == [(f"hub/{x['toon']}.safetensors", 0.7)]
    assert "Køkken: a small Danish kitchen, white cupboards, a big fridge. The empty place, with no people" in \
        prompt_in(fakes.comfy["prompts"][-1])
    # the series' folder keeps only what it points at
    folder = app.state.hub.env.data_dir / "movies" / sid
    on_disk = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.png"))
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    pointed = sorted(v["file"] for item in [*s["cast"], *s["sets"]] for v in item["variants"])
    assert on_disk == pointed


STORY = {"shots": [
    {"seconds": 5, "set": "Køkken", "characters": ["Far"], "picture": "Far tiptoes through the dark kitchen at night",
     "motion": "he sneaks forward, slow push-in", "sound": "creaking floor", "lines": []},
    {"seconds": 14, "set": "køkken", "characters": ["Far", "Mor", "Bedstemor"],
     "picture": "Far and Mor face each other by the open fridge, forks raised", "motion": "both lean in",
     "sound": "western whistle", "lines": [{"who": "Mor", "text": "Seriøst?"}]},
    {"seconds": 4, "set": "Garage", "characters": [], "picture": "An empty plate on the table",
     "motion": "slow push-in", "sound": "", "lines": [{"who": "Narrator", "text": "Og så var der ingen kage."}]},
]}


def story_writer(seen: dict, reply: dict = STORY):
    def answer(body):
        text = body["messages"][-1]["content"]
        if isinstance(text, str) and text.startswith("You write the storyboard"):
            seen.setdefault("story", []).append(body)
            return {"content": json.dumps(reply)}
        return {"content": "Title"}
    return answer


async def episode_with_storyboard(client, app, fakes, x: dict) -> tuple[str, dict, dict]:
    sid = x["sid"]
    s = await ok(await client.post(f"{API}/{sid}/episodes", json={"title": "Det sidste stykke", "premise":
                                   "Far sneaks to the fridge at night for the last slice of cake."}, headers=ORIGIN))
    eid = s["new_episode"]
    seen: dict = {}
    fakes.llama["responder"] = story_writer(seen)
    await ok(await client.post(f"{API}/{sid}/episodes/{eid}/storyboard", json={}, headers=ORIGIN))
    await done(app, sid, f"storyboard:{eid}")
    s = await ok(await client.get(f"{API}/{sid}"))
    return eid, s, seen


async def test_the_storyboard_comes_from_the_chat_model_held_to_the_series(signed_in, app, fakes):
    x = await series(signed_in, app)
    sid = x["sid"]
    eid, s, seen = await episode_with_storyboard(signed_in, app, fakes, x)
    body = seen["story"][0]
    assert body["response_format"]["json_schema"]["schema"] == S.STORYBOARD_SCHEMA
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    text = body["messages"][0]["content"]
    assert "- Far: tall, glasses, green jumper" in text and "- Køkken: a small Danish kitchen" in text
    assert "Far sneaks to the fridge at night for the last slice of cake." in text and "in Danish" in text
    assert "at most 30 seconds" in text and "the video has no lip sync" in text
    e = s["episodes"][0]
    shots = e["shots"]
    assert [(sh["seconds"], sh["set"], sh["characters"]) for sh in shots] == [
        (5.0, x["kitchen"], [x["far"]]), (10.0, x["kitchen"], [x["far"], x["mor"]]), (4.0, "", [])]
    assert said(shots[1]["lines"]) == [{"who": x["mor"], "name": "Mor", "text": "Seriøst?"}]
    assert said(shots[2]["lines"]) == [{"who": "Narrator", "name": "Narrator", "text": "Og så var der ingen kage."}]
    assert e["notes"] == ["Bedstemor is not in the cast: left out of a shot", "Garage is not one of the sets: a shot "
                                                                                "has none"]
    # the group shot wants to be combined from the pictures, which are not made yet: until then a new picture
    assert [(sh["method_wanted"], sh["method_used"]) for sh in shots] == [("new", "new"), ("combine", "new"),
                                                                          ("new", "new")]
    assert shots[1]["missing"] == ["Køkken", "Far", "Mor"]
    assert e["progress"] == {"shots": 3, "seconds": 19.0, "approved": False, "keyframes": 0, "clips": 0, "kept": 0,
                             "preview": "none", "lines": 2, "spoken": 0, "effects": 2, "effects_made": 0,
                             "final": "none", "final_why": "", "total": 21.5,
                             "cut_missing": ["shot 1 has no clip", "shot 2 has no clip", "shot 2 has lines not spoken",
                                             "shot 3 has no clip", "shot 3 has lines not spoken"]}
    # the checkpoint: no keyframes before the storyboard is approved; writing it again asks first
    r = await signed_in.post(f"{API}/{sid}/episodes/{eid}/keyframes", json={}, headers=ORIGIN)
    assert r.status_code == 400 and "Approve the storyboard first" in r.text
    r = await signed_in.post(f"{API}/{sid}/episodes/{eid}/storyboard", json={}, headers=ORIGIN)
    assert r.status_code == 409 and "replaces them" in r.text
    # the owner edits a shot, adds one, moves it, deletes it; then approves
    h1 = shots[0]["id"]
    s = await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}/shots/{h1}", json={
        "seconds": 6, "lines": [{"who": "Far", "text": "Kom til far..."}], "method": "combine"}, headers=ORIGIN))
    sh = s["episodes"][0]["shots"][0]
    assert sh["seconds"] == 6.0 and sh["lines"][0]["who"] == x["far"] and sh["method"] == "combine"
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/shots", json={"after": h1}, headers=ORIGIN))
    new = s["episodes"][0]["shots"][1]["id"]
    s = await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}/shots/{new}", json={"move": -1}, headers=ORIGIN))
    assert s["episodes"][0]["shots"][0]["id"] == new
    r = await signed_in.patch(f"{API}/{sid}/episodes/{eid}", json={"approved": True}, headers=ORIGIN)
    assert r.status_code == 400 and "picture description" in r.text      # the new shot has none
    await ok(await signed_in.delete(f"{API}/{sid}/episodes/{eid}/shots/{new}", headers=ORIGIN))
    s = await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}", json={"approved": True}, headers=ORIGIN))
    assert s["episodes"][0]["progress"]["approved"] is True


async def test_keyframes_one_character_gets_his_lora_a_group_is_combined_from_the_pictures(signed_in, app, fakes):
    x = await series(signed_in, app)
    sid = x["sid"]
    fakes.comfy["pictures"] = [_png((16, 16), GREY)]
    for kind, iid in (("cast", x["far"]), ("cast", x["mor"]), ("sets", x["kitchen"])):
        s = await make(signed_in, app, sid, kind, iid)
        item = next(i for i in [*s["cast"], *s["sets"]] if i["id"] == iid)
        await ok(await signed_in.post(f"{API}/{sid}/{kind}/{iid}/pick", json={"file": item["variants"][0]["file"]},
                                      headers=ORIGIN))
    eid, s, _ = await episode_with_storyboard(signed_in, app, fakes, x)
    await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}", json={"approved": True}, headers=ORIGIN))
    h1, h2, h3 = (sh["id"] for sh in s["episodes"][0]["shots"])
    fakes.comfy["prompts"].clear()
    fakes.comfy["uploads"].clear()
    fakes.comfy["pictures"] = [_png((16, 16), c) for c in (RED, BLUE, GREEN, GREY, RED, BLUE)]
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/keyframes", json={}, headers=ORIGIN))
    assert "keyframes:" + eid in s["jobs"]
    await done(app, sid, f"keyframes:{eid}")
    g1, g2 = fakes.comfy["prompts"][0], fakes.comfy["prompts"][2]
    # shot 1: Far alone - a new picture with his LoRA and the style's, his look and the kitchen in words
    assert loras_in(g1) == [(f"hub/{x['far_lora']}.safetensors", 0.9), (f"hub/{x['toon']}.safetensors", 0.7)]
    assert "Far tiptoes through the dark kitchen at night. Far: tall, glasses, green jumper. The place: a small " \
           "Danish kitchen" in prompt_in(g1)
    assert not nodes(g1, "LoadImage")
    # shot 2: Far and Mor - combined from the kitchen's picture and both sheets, the style LoRA only
    assert loras_in(g2) == [(f"hub/{x['toon']}.safetensors", 0.7)]
    assert len(nodes(g2, "LoadImage")) == 3 and len(fakes.comfy["uploads"]) == 6        # 2 pictures x 3
    assert "Picture 1 shows the place, Køkken. Picture 2 is Far. Picture 3 is Mor. Make one new picture of that " \
           "place with them in it: Far and Mor face each other by the open fridge, forks raised." in prompt_in(g2)
    assert "Keep each character exactly as in their picture" in prompt_in(g2)
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    shots = s["episodes"][0]["shots"]
    assert [len(sh["variants"]) for sh in shots] == [2, 2, 2]
    assert [sh["keyframe_state"] for sh in shots] == ["none"] * 3 and s["episodes"][0]["progress"]["keyframes"] == 0
    # chosen: up to date; a change to the shot's picture makes only that one out of date
    for sh in shots:
        s = await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/shots/{sh['id']}/pick",
                                          json={"file": sh["variants"][0]["file"]}, headers=ORIGIN))
    assert s["episodes"][0]["progress"]["keyframes"] == 3
    s = await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}/shots/{h3}",
                                       json={"picture": "A plate with crumbs"}, headers=ORIGIN))
    assert [sh["keyframe_state"] for sh in s["episodes"][0]["shots"]] == ["fresh", "fresh", "stale"]
    # a new sheet for Mor: the combined shot is out of date, Far's own shot is not
    s = await ok(await signed_in.post(f"{API}/{sid}/cast/{x['mor']}/pick",
                                      json={"file": s["cast"][1]["variants"][0]["file"]}, headers=ORIGIN))
    assert [sh["keyframe_state"] for sh in s["episodes"][0]["shots"]] == ["fresh", "fresh", "stale"]   # same file
    await make(signed_in, app, sid, "cast", x["mor"])
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    newest = s["cast"][1]["variants"][0]["file"]
    s = await ok(await signed_in.post(f"{API}/{sid}/cast/{x['mor']}/pick", json={"file": newest}, headers=ORIGIN))
    assert [sh["keyframe_state"] for sh in s["episodes"][0]["shots"]] == ["fresh", "stale", "stale"]
    # again for one shot: its new pictures first, the chosen one kept; Keep as it is takes the change
    fakes.comfy["prompts"].clear()
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/keyframes", json={"shots": [h3]}, headers=ORIGIN))
    await done(app, sid, f"keyframes:{eid}")
    assert len(fakes.comfy["prompts"]) == 2
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/shots/{h2}/pick", json={"keep": True},
                                      headers=ORIGIN))
    shots = s["episodes"][0]["shots"]
    assert len(shots[2]["variants"]) == 4 and shots[2]["keyframe"] == shots[2]["variants"][2]["file"]
    assert [sh["keyframe_state"] for sh in shots] == ["fresh", "fresh", "stale"]
    assert h1


async def test_clips_from_the_chosen_keyframes_kept_retaken_and_joined_into_a_preview(signed_in, app, fakes):
    x = await series(signed_in, app)
    install(app, "wan2.2-5b")
    sid = x["sid"]
    eid, s, _ = await episode_with_storyboard(signed_in, app, fakes, x)
    await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}", json={"approved": True}, headers=ORIGIN))
    shots = s["episodes"][0]["shots"]
    h1, h2 = shots[0]["id"], shots[1]["id"]
    r = await signed_in.post(f"{API}/{sid}/episodes/{eid}/clips", json={}, headers=ORIGIN)
    assert r.status_code == 400 and "keyframe" in r.text                 # the checkpoint before the clips
    fakes.comfy["pictures"] = [_png((90, 160), RED)]                    # portrait keyframes
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/keyframes", json={"shots": [h1, h2]}, headers=ORIGIN))
    await done(app, sid, f"keyframes:{eid}")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    for sh in s["episodes"][0]["shots"][:2]:
        await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/shots/{sh['id']}/pick",
                                      json={"file": sh["variants"][0]["file"]}, headers=ORIGIN))
    fakes.comfy["prompts"].clear()
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/clips", json={}, headers=ORIGIN))
    await done(app, sid, f"clips:{eid}")
    assert len(fakes.comfy["prompts"]) == 2                             # the two with a chosen keyframe
    g = fakes.comfy["prompts"][0]
    latent = nodes(g, "Wan22ImageToVideoLatent")[0]
    assert latent["start_image"] and latent["width"] < latent["height"]  # the keyframe's shape
    assert "he sneaks forward, slow push-in. 2D cartoon" in prompt_in(g)
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    sh1 = s["episodes"][0]["shots"][0]
    clip = sh1["clip"]
    assert clip["file"].endswith(".mp4") and clip["kept"] is False and sh1["clip_state"] == "fresh"
    assert (await signed_in.get(f"{API}/{sid}/file", params={"path": clip["file"]})).status_code == 200
    # kept: the checkpoint; again: the earlier take kept and back with one click
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/shots/{h1}/clip", json={"kept": True},
                                      headers=ORIGIN))
    assert s["episodes"][0]["progress"]["kept"] == 1
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/clips", json={"shots": [h1]}, headers=ORIGIN))
    await done(app, sid, f"clips:{eid}")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    sh1 = s["episodes"][0]["shots"][0]
    assert sh1["clip"]["kept"] is False and [t["file"] for t in sh1["takes"]] == [clip["file"]]
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/shots/{h1}/clip", json={"take": clip["file"]},
                                      headers=ORIGIN))
    sh1 = s["episodes"][0]["shots"][0]
    assert sh1["clip"]["file"] == clip["file"] and sh1["clip"]["kept"] is True and len(sh1["takes"]) == 1
    # a new motion makes the clip out of date (not the keyframe)
    s = await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}/shots/{h1}", json={"motion": "he waves"},
                                       headers=ORIGIN))
    sh1 = s["episodes"][0]["shots"][0]
    assert (sh1["keyframe_state"], sh1["clip_state"]) == ("fresh", "stale")
    assert s["episodes"][0]["progress"]["kept"] == 0
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/shots/{h1}/clip", json={"accept": True},
                                      headers=ORIGIN))
    assert s["episodes"][0]["shots"][0]["clip_state"] == "fresh"
    # the preview: the clips joined by ffmpeg in the sandbox, in a folder of its own, removed afterwards
    ws = app.state.hub.env.workspace_dir

    def ffmpeg(body):
        out = ws / body["cwd"].removeprefix("/workspace/") / "preview.mp4"
        out.write_bytes(b"joined clips")
        return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "duration_ms": 5, "truncated": False}
    fakes.sandbox["handler"] = ffmpeg
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/preview", headers=ORIGIN))
    await done(app, sid, f"preview:{eid}")
    cmd = fakes.sandbox["execs"][-1]
    assert cmd["offline"] and cmd["cwd"].startswith("/workspace/movies-tmp/")
    assert "-i c1.mp4 -i c2.mp4" in cmd["cmd"] and "concat=n=2:v=1:a=0" in cmd["cmd"]
    assert f"scale={clip['width']}:{clip['height']}" in cmd["cmd"] and "fps=24" in cmd["cmd"]
    assert not list((ws / "movies-tmp").iterdir())
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    e = s["episodes"][0]
    assert e["progress"]["preview"] == "fresh"
    r = await signed_in.get(f"{API}/{sid}/file", params={"path": e["preview"]["file"]})
    assert r.content == b"joined clips"
    # a clip it joined made before a change: the preview too; kept as it is, it is up to date again
    s = await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}/shots/{h2}", json={"motion": "they glare"},
                                       headers=ORIGIN))
    assert s["episodes"][0]["progress"]["preview"] == "stale"
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/shots/{h2}/clip", json={"accept": True},
                                      headers=ORIGIN))
    assert s["episodes"][0]["progress"]["preview"] == "fresh"
    # ffmpeg that fails (or runs out of time) leaves the preview as it was
    before = s["episodes"][0]["preview"]["file"]

    def broken(body):
        out = ws / body["cwd"].removeprefix("/workspace/") / "preview.mp4"
        out.write_bytes(b"half a file")
        return {"exit_code": -9, "stdout": "", "stderr": "killed", "timed_out": True, "duration_ms": 5,
                "truncated": False}
    fakes.sandbox["handler"] = broken
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/preview", headers=ORIGIN))
    await done(app, sid, f"preview:{eid}")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert s["episodes"][0]["preview"]["file"] == before and "The preview failed: killed" in s["errors"][f"preview:{eid}"]
    assert not list((ws / "movies-tmp").iterdir())
    # a new take for a shot makes the preview out of date
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/clips", json={"shots": [h2]}, headers=ORIGIN))
    await done(app, sid, f"clips:{eid}")
    assert (await ok(await signed_in.get(f"{API}/{sid}")))["episodes"][0]["progress"]["preview"] == "stale"


async def test_a_job_can_be_stopped_and_a_failure_is_said(signed_in, app, fakes):
    x = await series(signed_in, app)
    sid = x["sid"]
    fakes.comfy["running_s"] = 30                                        # ComfyUI works for a long time
    await ok(await signed_in.post(f"{API}/{sid}/cast/{x['far']}/make", headers=ORIGIN))
    key = f"cast:{x['far']}"
    for _ in range(50):
        s = await ok(await signed_in.get(f"{API}/{sid}"))
        if s["jobs"].get(key, {}).get("what") == "sheet":
            break
        await asyncio.sleep(0.05)
    assert s["jobs"][key]["name"] == "Far"
    r = await signed_in.post(f"{API}/{sid}/cast/{x['far']}/make", headers=ORIGIN)
    assert r.status_code == 409                                          # one at a time
    s = await ok(await signed_in.post(f"{API}/{sid}/jobs/cancel", json={"key": key}, headers=ORIGIN))
    assert key not in s["jobs"] and s["errors"][key] == "Stopped."
    assert s["cast"][0]["variants"] == []
    fakes.comfy["running_s"] = 0
    # a deleted series' folder goes; it cannot be deleted while it works
    fakes.comfy["fail_with"] = "HIP out of memory"
    await make(signed_in, app, sid, "cast", x["far"])
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert "HIP out of memory" in s["errors"][key]
    await ok(await signed_in.delete(f"{API}/{sid}", headers=ORIGIN))
    assert not (app.state.hub.env.data_dir / "movies" / sid).exists()
    assert (await signed_in.get(f"{API}/{sid}")).status_code == 404


async def test_people_cannot_reach_movies(signed_in, app):
    await add_person(signed_in)
    async with client_from(app) as phone:
        await sign_in(phone)
        assert (await phone.get(API)).status_code in (401, 403)
        assert (await phone.post(API, json={"name": "x"}, headers=ORIGIN)).status_code in (401, 403)


def test_the_join_command_and_the_plans():
    cmd = S.join_command(["c1.mp4", "c 2.mp4"], "preview.mp4", 704, 1248, 24)
    assert cmd.startswith("ffmpeg -nostdin -v error -y -i c1.mp4 -i 'c 2.mp4' -filter_complex ")
    assert "[0:v]scale=704:1248:force_original_aspect_ratio=decrease,pad=704:1248:(ow-iw)/2:(oh-ih)/2,setsar=1," \
           "fps=24[v0]" in cmd and "[v0][v1]concat=n=2:v=1:a=0[out]" in cmd and cmd.endswith(" preview.mp4")
    series = {"style": dict(S.DEFAULT_STYLE), "cast": [{"id": "c1", "name": "Far", "look": "tall"},
                                                       {"id": "c2", "name": "Mor", "look": "red hair",
                                                        "sheet": "cast/c2-a-1.png"}],
              "sets": [{"id": "k1", "name": "Køkken", "look": "kitchen", "picture": ""}], "episodes": []}
    shot = S.new_shot(picture="They argue", characters=["c1", "c2"], set="k1")
    p = S.plan(series, shot)
    # combined from what has a picture: Mor's sheet; Far and the kitchen in words
    assert p["method"] == "combine" and p["refs"] == ["cast/c2-a-1.png"]
    assert p["prompt"].startswith("Picture 1 is Mor. Make one new picture with them in it: They argue. The place: "
                                  "kitchen. Far: tall.")
    series["cast"][1]["sheet"] = ""
    assert S.plan(series, shot)["method"] == "new"                      # nothing to combine from
    assert re.fullmatch(r"[0-9a-f]{16}", p["stamp"])


async def test_what_the_review_found(signed_in, app, fakes):
    """An independent review of 0.72.0 (a separate agent) found these; each is fixed."""
    x = await series(signed_in, app)
    sid = x["sid"]
    hub = app.state.hub
    studio = hub.extras["movies"]
    # the pictures the pipeline stored as uploads go again: the series keeps its own copies
    fakes.comfy["pictures"] = [_png((16, 16), RED), _png((16, 16), BLUE)]
    s = await make(signed_in, app, sid, "cast", x["far"])
    folder = hub.env.data_dir / "movies" / sid
    from app.projects import blobs
    for v in s["cast"][0]["variants"]:
        assert not blobs.exists(hashlib.sha256((folder / v["file"]).read_bytes()).hexdigest())
    # a character deleted while the storyboard is written is not in it; a line of his keeps his name
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes", json={"title": "Kage", "premise": "Cake."},
                                      headers=ORIGIN))
    eid = s["new_episode"]
    seen: dict = {}
    slow = story_writer(seen)
    fakes.llama["responder"] = lambda body: {**slow(body), "delay": 0.5}
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/storyboard", json={}, headers=ORIGIN))
    await asyncio.sleep(0.1)
    r = await signed_in.post(f"{API}/{sid}/episodes/{eid}/keyframes", json={}, headers=ORIGIN)
    assert r.status_code == 409 and "writing the storyboard" in r.text  # nothing is made from what is rewritten
    await ok(await signed_in.delete(f"{API}/{sid}/cast/{x['mor']}", headers=ORIGIN))
    await done(app, sid, f"storyboard:{eid}")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    shots = s["episodes"][-1]["shots"]
    assert [sh["characters"] for sh in shots] == [[x["far"]], [x["far"]], []]
    assert said(shots[1]["lines"]) == [{"who": "Mor", "name": "Mor", "text": "Seriøst?"}]
    assert "Mor is not in the cast: left out of a shot" in s["episodes"][-1]["notes"]
    # shots added by hand while it writes are not replaced unasked
    s = await ok(await signed_in.post(f"{API}/{sid}/episodes", json={"title": "To", "premise": "More cake."},
                                      headers=ORIGIN))
    e2 = s["new_episode"]
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{e2}/storyboard", json={}, headers=ORIGIN))
    await asyncio.sleep(0.1)
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{e2}/shots", json={}, headers=ORIGIN))
    await done(app, sid, f"storyboard:{e2}")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert len(s["episodes"][-1]["shots"]) == 1
    assert s["errors"][f"storyboard:{e2}"] == "Shots were added while it wrote - write it again to replace them."
    # one tap on a character's chip at a time, whatever the other taps did
    h = shots[2]["id"]
    s = await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}/shots/{h}", json={"toggle_character": x["far"]},
                                       headers=ORIGIN))
    s = await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}/shots/{h}", json={"characters": [x["far"], "gone"]},
                                       headers=ORIGIN))
    assert s["episodes"][0]["shots"][2]["characters"] == [x["far"]]   # one no longer in the cast is left out
    # pictures that fail say so on the shot as the pictures' failure, apart from the clip's
    await ok(await signed_in.patch(f"{API}/{sid}/episodes/{eid}", json={"approved": True}, headers=ORIGIN))
    fakes.comfy["fail_with"] = "HIP out of memory"
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/keyframes", json={"shots": [h]}, headers=ORIGIN))
    await done(app, sid, f"keyframes:{eid}")
    fakes.comfy.pop("fail_with")
    sh = (await ok(await signed_in.get(f"{API}/{sid}")))["episodes"][0]["shots"][2]
    assert "HIP out of memory" in sh["keyframe_error"] and "clip_error" not in sh and "error" not in sh
    # numbers that are not numbers are refused, and the look stays as it was
    for raw in (b'{"style": {"strength": NaN}}', b'{"style": {"max_seconds": Infinity}}',
                b'{"style": {"variants": -Infinity}}'):            # as a hand-made request
        r = await signed_in.patch(f"{API}/{sid}", content=raw, headers={**ORIGIN, "Content-Type": "application/json"})
        assert r.status_code == 400 and "must be a number" in r.text
    st = (await ok(await signed_in.get(f"{API}/{sid}")))["style"]
    assert (st["strength"], st["max_seconds"], st["variants"]) == (0.7, 30, 2)
    # a shot asked for while a job stops is made by a new job, not lost
    fakes.comfy["running_s"] = 30
    h1 = shots[0]["id"]
    await ok(await signed_in.post(f"{API}/{sid}/episodes/{eid}/keyframes", json={"shots": [h1]}, headers=ORIGIN))
    await asyncio.sleep(0.2)
    stopping = asyncio.create_task(studio.cancel(sid, f"keyframes:{eid}"))
    await asyncio.sleep(0)
    studio.make_keyframes(sid, eid, [h])
    await stopping
    fakes.comfy["running_s"] = 0
    for _ in range(100):
        if studio.working(sid, f"keyframes:{eid}"):
            break
        await asyncio.sleep(0.02)
    await done(app, sid, f"keyframes:{eid}")
    s = await ok(await signed_in.get(f"{API}/{sid}"))
    assert len(s["episodes"][0]["shots"][2]["variants"]) == 2 and not s["episodes"][0]["shots"][0]["variants"]


def test_the_stamps_follow_what_the_defaults_mean_and_the_set_name():
    series = {"style": dict(S.DEFAULT_STYLE), "cast": [{"id": "c1", "name": "Far", "look": "tall", "sheet": "cast/a.png"},
                                                       {"id": "c2", "name": "Mor", "look": "red", "sheet": "cast/b.png"}],
              "sets": [{"id": "k1", "name": "Køkken", "look": "kitchen", "picture": "sets/k.png"}], "episodes": []}
    shot = S.new_shot(picture="They argue", characters=["c1", "c2"], set="k1")
    a = S.plan(series, shot, {"model": "flux2-klein-4b", "combine": True, "video": "wan2.2-5b"})
    b = S.plan(series, shot, {"model": "flux2-klein-9b", "combine": True, "video": "wan2.2-5b"})
    c = S.plan(series, shot, {"model": "z-image-turbo", "combine": False, "video": "wan2.2-5b"})
    assert a["method"] == "combine" and c["method"] == "new" and len({a["stamp"], b["stamp"], c["stamp"]}) == 3
    series["sets"][0]["name"] = "Garagen"                               # its name is in the prompt: in the stamp
    assert S.plan(series, shot, {"model": "flux2-klein-4b", "combine": True, "video": ""})["stamp"] != a["stamp"]
    shot["keyframe"] = "e/x.png"
    env = {"model": "m", "combine": True, "video": "wan2.2-5b"}
    assert S.clip_stamp(series, shot, env) != S.clip_stamp(series, shot, {**env, "video": "wan2.2-5b-turbo"})


async def test_a_picture_model_that_cannot_change_pictures_does_not_combine(signed_in, app):
    _install(app.state.hub, "z-image-turbo")
    s = await ok(await signed_in.post(API, json={"name": "Z"}, headers=ORIGIN))
    s = await ok(await signed_in.patch(f"{API}/{s['id']}", json={"style": {"model": "z-image-turbo"}}, headers=ORIGIN))
    studio = app.state.hub.extras["movies"]
    assert studio.env(studio.load(s["id"]))["combine"] is False
