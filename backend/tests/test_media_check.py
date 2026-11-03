"""Pictures and videos checked before the user gets them, at the Effort Extra (0.71.0). Søren: "When i create a
picture or a video, and the thinking is set on Extra.. i would like it to verify the picture. Like check for extra or
missing bodyparts on humans, animals or just out of shape stuff before delivering the result to me.. if it cant make
it right, then suggest improvements to my prompt." The fake model answers the check's requests by their first line;
the fake ComfyUI gives one picture per queued prompt (fakes.comfy["pictures"])."""
from __future__ import annotations

import io
import json
import re

from PIL import Image

from app.agent import media_check as M
from app.chats import store as chats
from app.projects import blobs

from .mocks import _png
from .test_agent import finish, last_user, new_chat, say
from .test_audio import chat_dir, install, tool_result
from .test_images_chain import SAVED, colour
from .test_images_models import _install
from .test_loras import KLEIN, ORIGIN, safetensors

RED, BLUE, GREEN = (200, 30, 30, 255), (30, 30, 200, 255), (30, 200, 30, 255)
PICTURE_MARK = M.PICTURE.splitlines()[0]
VIDEO_MARK = M.VIDEO.splitlines()[0].split(":")[0]
BODIES = "Before you answer, look at the people in them"


def wrong(*what: str, minor: str = "") -> dict:
    mistakes = [{"what": w, "serious": True} for w in what] + ([{"what": minor, "serious": False}] if minor else [])
    return {"content": json.dumps({"figures": [{"what": "woman", "heads": 1, "arms": 3, "hands": 2, "legs": 2}],
                                   "mistakes": mistakes, "verdict": "mistakes"})}


def right(minor: str = "") -> dict:
    return {"content": json.dumps({"figures": [{"what": "woman", "heads": 1, "arms": 2, "hands": 2, "legs": 2}],
                                   "mistakes": [{"what": minor, "serious": False}] if minor else [],
                                   "verdict": "ok"})}


def answers(turn: list[dict], checks: list[dict], seen: dict):
    """The turn's replies and the check's, each in order (the last repeats); titles apart."""
    def answer(body):
        text = last_user(body)
        if "short title" in text:
            return {"content": "Title"}
        who = "checks" if PICTURE_MARK in text or VIDEO_MARK in text else "turn"
        seen.setdefault(who, []).append(body)
        replies = checks if who == "checks" else turn
        return replies[min(len(seen[who]) - 1, len(replies) - 1)]
    return answer


def extra(app, **agent) -> None:
    app.state.hub.settings.update("agent", {"level_chats": "extra", **agent})


def make(prompt: str, **args) -> list[dict]:
    return [{"tool_calls": [{"name": "image_generate", "arguments": {"prompt": prompt, **args}}]},
            {"content": "Here it is."}]


async def draw(app, client, fakes, turn: list[dict], checks: list[dict], pictures: list[bytes],
               text: str = "Draw it") -> tuple:
    fakes.comfy["pictures"] = pictures
    seen: dict = {}
    fakes.llama["responder"] = answers(turn, checks, seen)
    c = await new_chat(client)
    run = await finish(app, await say(client, c["id"], text))
    assert run.status == "done", run.events[-1]
    return c, run, tool_result(run), seen


async def test_extra_makes_a_picture_with_a_mistake_again(signed_in, app, fakes):
    extra(app)
    c, run, res, seen = await draw(app, signed_in, fakes, make("a woman waving at a dog in a park"),
                                   [wrong("the woman has a third arm on her left side"), right("a blurry leaf")],
                                   [_png((16, 16), RED), _png((16, 16), BLUE)])
    assert len(fakes.comfy["prompts"]) == 2                             # made again once
    assert len(seen["checks"]) == 2
    first = seen["checks"][0]
    assert len(first["messages"]) == 1                                  # its own request, not the conversation
    parts = first["messages"][0]["content"]
    assert parts[0]["text"].startswith(PICTURE_MARK) and "a woman waving at a dog in a park" in parts[0]["text"]
    assert "It was made from this description:" in parts[0]["text"]
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/")
    assert first["response_format"]["type"] == "json_schema"
    assert first["response_format"]["json_schema"]["schema"] == M.PICTURE_SCHEMA
    assert first["chat_template_kwargs"] == {"enable_thinking": False} and not first.get("tools")
    # the user gets the second try, saved as the file; the first is kept with the call
    path = SAVED.search(res["text"]).group(1)
    assert colour((chat_dir(app, c["id"]) / path).read_bytes()) == BLUE
    assert [colour(blobs.read(i["sha"])) for i in res["images"]] == [BLUE]
    checked = res["display"]["checked"]
    assert checked["by"] == "Qwen3.8 27B"
    assert checked["pictures"] == [{"state": "ok", "tries": 2, "try": 2, "mistakes": [], "minor": ["a blurry leaf"]}]
    assert [(r["picture"], r["try"], r["mistakes"]) for r in checked["rejected"]] == [
        (1, 1, ["the woman has a third arm on her left side"])]
    assert colour(blobs.read(checked["rejected"][0]["sha"])) == RED
    assert res["display"]["generated"][0]["sha"] == res["images"][0]["sha"]
    assert ("The picture: no serious mistake. It was made 2 times: the other tries had the woman has a third arm on "
            "her left side. Small things it noticed: a blurry leaf.") in res["text"]
    assert BODIES not in res["text"]                                    # it was looked at already
    # the model's next step reads it; the try shows in the tool row; the checks are in the turn's steps
    after = seen["turn"][1]["messages"]
    assert any(m["role"] == "tool" and "no serious mistake" in str(m["content"]) for m in after)
    labels = [e["data"].get("label") for e in run.events if e["type"] == "image_progress"]
    assert labels == ["checking", "checking try 2"]             # (the fake ComfyUI sends no sampler steps)
    steps = chats.runs_for_chat(c["id"])[-1]["steps"]
    assert [(s["what"], s["verdict"], s["mistakes"]) for s in steps if s["type"] == "check"] == [
        ("picture", "mistakes", 1), ("picture", "ok", 0)]
    # the export shows the check and the try it threw away
    md = (await signed_in.get(f"/hub/api/chats/{c['id']}/export", params={"format": "md"})).text
    assert "*(Checked by Qwen3.8 27B: no mistakes found on try 2.)*" in md
    assert "try 1, thrown away: the woman has a third arm on her left side" in md


async def test_still_wrong_after_its_tries_the_fewest_mistakes_win_and_the_prompt_is_questioned(signed_in, app,
                                                                                                fakes):
    extra(app)
    c, run, res, seen = await draw(
        app, signed_in, fakes, make("three acrobats juggling on a unicycle"),
        [wrong("a third arm", "a hand without fingers"), wrong("two heads", "a third arm", "a leg missing"),
         wrong("an acrobat merged with the unicycle")],
        [_png((16, 16), RED), _png((16, 16), BLUE), _png((16, 16), GREEN)])
    assert len(fakes.comfy["prompts"]) == 3                             # the first and its 2 tries (the default)
    assert [colour(blobs.read(i["sha"])) for i in res["images"]] == [GREEN]
    checked = res["display"]["checked"]
    assert checked["pictures"][0] == {"state": "mistakes", "tries": 3, "try": 3,
                                      "mistakes": ["an acrobat merged with the unicycle"], "minor": []}
    assert [(r["try"], colour(blobs.read(r["sha"]))) for r in checked["rejected"]] == [(1, RED), (2, BLUE)]
    assert ("The picture still has serious mistakes after 3 tries: an acrobat merged with the unicycle. The other "
            "tries had: a third arm; a hand without fingers; two heads.") in res["text"]   # (two of each try)
    assert "Tell the user plainly what is wrong - do not call it fine - and suggest how to change the prompt" in \
        res["text"]
    assert "fewer people or animals, a simpler pose" in res["text"]
    md = (await signed_in.get(f"/hub/api/chats/{c['id']}/export", params={"format": "md"})).text
    assert "still wrong after 3 tries (kept try 3) - an acrobat merged with the unicycle" in md


async def test_only_the_pictures_with_a_mistake_are_made_again(signed_in, app, fakes):
    extra(app)
    c, run, res, seen = await draw(app, signed_in, fakes, make("two cats on a sofa", count=2),
                                   [right(), wrong("a cat with five legs"), right()],
                                   [_png((16, 16), RED), _png((16, 16), BLUE), _png((16, 16), GREEN)])
    assert len(fakes.comfy["prompts"]) == 3
    assert [colour(blobs.read(i["sha"])) for i in res["images"]] == [RED, GREEN]      # in their places
    pics = res["display"]["checked"]["pictures"]
    assert [(p["state"], p["tries"]) for p in pics] == [("ok", 1), ("ok", 2)]
    assert "Picture 1: no serious mistake. Picture 2: no serious mistake. It was made 2 times: the other tries " \
           "had a cat with five legs." in res["text"]
    files = sorted((chat_dir(app, c["id"]) / "images").iterdir())
    assert [colour(f.read_bytes()) for f in files] == [RED, GREEN]                   # only those are saved


async def test_a_seed_is_checked_but_not_made_again(signed_in, app, fakes):
    extra(app)
    _, _, res, seen = await draw(app, signed_in, fakes, make("a horse", seed=42), [wrong("a horse with six legs")],
                                 [_png((16, 16), RED), _png((16, 16), BLUE)])
    assert len(fakes.comfy["prompts"]) == 1 and len(seen["checks"]) == 1
    assert res["display"]["checked"]["seeded"] is True
    assert "It was not made again: the call gave a seed" in res["text"] and "suggest how to change" in res["text"]


async def test_the_settings_turn_it_off_or_check_only(signed_in, app, fakes):
    extra(app, check_media="0")
    _, _, res, seen = await draw(app, signed_in, fakes, make("a horse"), [wrong("a horse with six legs")],
                                 [_png((16, 16), RED), _png((16, 16), BLUE)])
    assert len(fakes.comfy["prompts"]) == 1 and res["display"]["checked"]["only"] is True
    assert "It was not made again (Settings -> Assistant: check only)." in res["text"]
    fakes.comfy["prompts"].clear()
    extra(app, check_media="off")
    _, _, res, seen = await draw(app, signed_in, fakes, make("a horse"), [wrong("a horse with six legs")],
                                 [_png((16, 16), RED)])
    assert "checks" not in seen and "checked" not in res["display"] and BODIES in res["text"]
    assert M.tries(app.state.hub.settings) is None


async def test_quick_does_not_check(signed_in, app, fakes):
    app.state.hub.settings.update("agent", {"level_chats": "quick"})
    _, run, res, seen = await draw(app, signed_in, fakes, make("a horse"), [wrong("a horse with six legs")],
                                   [_png((16, 16), RED)])
    assert "checks" not in seen and "checked" not in res["display"] and len(fakes.comfy["prompts"]) == 1
    assert BODIES in res["text"]                                        # it looks itself, as before


async def test_a_model_that_cannot_see_does_not_check(signed_in, app, fakes, monkeypatch):
    extra(app)
    monkeypatch.setattr("app.agent.loop.vision", lambda model: False)
    _, _, res, seen = await draw(app, signed_in, fakes, make("a horse"), [wrong("x")], [_png((16, 16), RED)])
    assert "checks" not in seen and res["display"]["checked"] == {"skipped": "Qwen3.8 27B cannot see pictures"}
    assert "Not checked before the user got it (Effort Extra): Qwen3.8 27B cannot see pictures." in res["text"]


async def test_no_clear_answer_is_not_made_again(signed_in, app, fakes):
    extra(app)
    _, _, res, seen = await draw(app, signed_in, fakes, make("a horse"), [{"content": "It looks great to me!"}],
                                 [_png((16, 16), RED), _png((16, 16), BLUE)])
    assert len(fakes.comfy["prompts"]) == 1
    assert res["display"]["checked"]["pictures"][0]["state"] == "unclear"
    assert "The picture: the check gave no clear answer - look at it yourself." in res["text"]


async def test_when_the_check_fails_the_pictures_are_given(signed_in, app, fakes):
    extra(app)
    _, run, res, seen = await draw(app, signed_in, fakes, make("a horse"), [{"error": "the model fell over"}],
                                   [_png((16, 16), RED)])
    assert res["ok"] and [colour(blobs.read(i["sha"])) for i in res["images"]] == [RED]
    assert res["display"]["checked"]["pictures"][0]["state"] == "unchecked"
    assert "the check stopped" in res["display"]["checked"]["stopped"]
    assert "The picture was not checked." in res["text"] and "the pictures are given as they are" in res["text"]


async def test_when_a_new_try_fails_the_best_so_far_is_given(signed_in, app, fakes):
    extra(app)
    fakes.comfy["pictures"] = [_png((16, 16), RED)]
    seen: dict = {}
    checks = answers(make("a horse"), [wrong("a horse with six legs")], seen)

    def answer(body):                                   # the card runs out of memory after the first picture
        if PICTURE_MARK in last_user(body):
            fakes.comfy["fail_with"] = "HIP out of memory"
        return checks(body)
    fakes.llama["responder"] = answer
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Draw a horse"))
    res = tool_result(run)
    assert res["ok"] and [colour(blobs.read(i["sha"])) for i in res["images"]] == [RED]
    assert res["display"]["checked"]["stopped"].startswith("a new try could not be made:")
    assert "The picture still has serious mistakes: a horse with six legs." in res["text"]
    assert "(A new try could not be made: " in res["text"] and "the pictures are given as they are.)" in res["text"]


async def test_an_edit_is_checked_too(signed_in, app, fakes):
    extra(app)
    up = (await signed_in.post("/hub/api/uploads", files={"file": ("photo.png", _png((20, 10), RED),
                                                                    "image/png")})).json()
    fakes.comfy["pictures"] = [_png((20, 10), BLUE), _png((20, 10), GREEN)]
    seen: dict = {}
    fakes.llama["responder"] = answers(
        [{"tool_calls": [{"name": "image_edit", "arguments": {"prompt": "give her a red hat",
                                                              "images": [".hub/attachments/photo.png"]}}]},
         {"content": "A red hat."}],
        [wrong("her hand has seven fingers"), right()], seen)
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Give her a red hat", attachments=[up["sha"]]))
    res = tool_result(run)
    assert res["ok"], res
    assert "It was made by changing another picture, as asked here:" in seen["checks"][0]["messages"][0]["content"][0][
        "text"]
    assert [colour(blobs.read(i["sha"])) for i in res["images"]] == [GREEN]
    assert "It was made 2 times: the other tries had her hand has seven fingers." in res["text"]


async def test_a_loras_trigger_word_is_not_text_to_draw(signed_in, app, fakes):
    extra(app)
    _install(app.state.hub, "flux2-klein-4b")
    lid = (await signed_in.post("/hub/api/loras", files={"file": ("rikke.safetensors", safetensors(KLEIN), "x")},
                                data={"trigger": "rkxv"}, headers=ORIGIN)).json()["id"]
    fakes.comfy["pictures"] = [_png((16, 16), RED)]
    seen: dict = {}
    fakes.llama["responder"] = answers(make("rkxv riding a bike"), [right()], seen)
    c = await new_chat(signed_in, settings={"image_model": "flux2-klein-4b", "image_loras": [lid]})
    run = await finish(app, await say(signed_in, c["id"], "Draw Rikke on a bike"))
    assert tool_result(run)["ok"]
    text = seen["checks"][0]["messages"][0]["content"][0]["text"]
    assert "It was made with the LoRAs rikke: a made-up word in the description (a LoRA's trigger word) names a " \
           "person, a thing or a style the image model learned - it is not text to draw." in text


def _jpeg(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), color[:3]).save(buf, "JPEG")
    return buf.getvalue()


async def test_a_video_is_checked_from_its_frames_and_not_made_again(signed_in, app, fakes):
    extra(app)
    install(app, "wan2.2-5b")
    c = await new_chat(signed_in)
    folder = chat_dir(app, c["id"])

    def ffmpeg(body):                                   # the sandbox's ffmpeg: the frames into the folder it names
        dst = re.search(r"mkdir -p (\S+)", body["cmd"]).group(1)
        out = folder / dst[len(body["cwd"]) + 1:]
        out.mkdir(parents=True, exist_ok=True)
        for k in range(len(re.findall(r"ffmpeg ", body["cmd"]))):
            (out / f"f{k + 1}.jpg").write_bytes(_jpeg(RED if k != 2 else BLUE))
        return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "duration_ms": 5, "truncated": False}
    fakes.sandbox["handler"] = ffmpeg
    seen: dict = {}
    fakes.llama["responder"] = answers(
        [{"tool_calls": [{"name": "video_generate", "arguments": {"prompt": "A red fox runs through snow"}}]},
         {"content": "Done."}],
        [{"content": json.dumps({"mistakes": [{"what": "the fox has five legs", "frame": 3, "serious": True},
                                              {"what": "snow flickers", "frame": 5, "serious": False}],
                                 "verdict": "mistakes"})}], seen)
    run = await finish(app, await say(signed_in, c["id"], "Make a video of a fox"))
    res = tool_result(run)
    assert res["ok"], res
    assert len(fakes.comfy["prompts"]) == 1                             # not made again: minutes on the card
    cmd = fakes.sandbox["execs"][-1]
    assert cmd["offline"] and cmd["cmd"].count("ffmpeg -nostdin -v error -y -ss ") == M.FRAMES
    assert "-ss 2.1 -i " in cmd["cmd"] and "a-red-fox-runs-through-snow.mp4" in cmd["cmd"]
    assert not list((folder / ".hub" / "tmp").iterdir())                # its frames folder is gone
    parts = seen["checks"][0]["messages"][0]["content"]
    assert parts[0]["text"].startswith(VIDEO_MARK) and "A red fox runs through snow" in parts[0]["text"]
    assert [p["text"] for p in parts if p["type"] == "text"][1:4] == ["Frame 1 (0.42 s):", "Frame 2 (1.26 s):",
                                                                      "Frame 3 (2.1 s):"]
    assert sum(p["type"] == "image_url" for p in parts) == M.FRAMES
    assert seen["checks"][0]["response_format"]["json_schema"]["schema"] == M.VIDEO_SCHEMA
    checked = res["display"]["checked"]
    assert checked["state"] == "mistakes" and checked["mistakes"] == ["frame 3 (2.1 s): the fox has five legs"]
    assert checked["minor"] == ["frame 5 (3.78 s): snow flickers"]
    assert checked["frames"] == [0.42, 1.26, 2.1, 2.94, 3.78, 4.62]
    with Image.open(io.BytesIO(blobs.read(checked["sheet"]))) as sheet:  # what was checked, for the user
        assert sheet.format == "JPEG" and sheet.size[0] > 64 * 3
    assert "Serious mistakes: frame 3 (2.1 s): the fox has five legs. It was not made again (a video takes " \
           "minutes)." in res["text"]
    assert "simpler and slower movement" in res["text"]
    labels = [e["data"].get("label") for e in run.events if e["type"] == "video_progress"]
    assert "checking" in labels
    md = (await signed_in.get(f"/hub/api/chats/{c['id']}/export", params={"format": "md"})).text
    assert "*(Checked 6 frames with Qwen3.8 27B: frame 3 (2.1 s): the fox has five legs.)*" in md


async def test_a_video_whose_frames_cannot_be_taken_is_given_unchecked(signed_in, app, fakes):
    extra(app)
    install(app, "wan2.2-5b")
    fakes.sandbox["handler"] = lambda body: {"exit_code": 1, "stdout": "", "stderr": "moov atom not found",
                                             "timed_out": False, "duration_ms": 5, "truncated": False}
    seen: dict = {}
    fakes.llama["responder"] = answers(
        [{"tool_calls": [{"name": "video_generate", "arguments": {"prompt": "A red fox"}}]}, {"content": "Done."}],
        [right()], seen)
    c = await new_chat(signed_in)
    res = tool_result(await finish(app, await say(signed_in, c["id"], "Make a video of a fox")))
    assert res["ok"] and "checks" not in seen
    assert res["display"]["checked"] == {"skipped": "its frames could not be taken (ffmpeg gave no frames: moov atom "
                                                    "not found)"}


def test_the_answer_is_read_however_it_comes():
    fenced = "Sure!\n```json\n" + json.dumps({"figures": [{}, {}], "mistakes": [
        {"what": "  a third\narm ", "serious": "true"}, {"what": "odd shadow", "serious": "false"}, "a melted face",
        {"what": ""}, 7], "verdict": "mistakes"}) + "\n```"
    v = M.parse(fenced)
    assert (v.state, v.serious, v.minor, v.figures) == ("mistakes", ["a third arm", "a melted face"], ["odd shadow"], 2)
    assert M.parse('{"mistakes": [], "verdict": "ok"}').state == "ok"
    assert M.parse('{"mistakes": [{"what": "a smudge", "serious": false}], "verdict": "mistakes"}').state == "ok"
    assert M.parse('{"mistakes": [], "verdict": "mistakes"}').state == "unclear"     # nothing it could name
    assert M.parse('{"mistakes": [{"what": "an extra paw"}], "verdict": "ok"}').state == "mistakes"
    assert M.parse("no JSON here").state == "unclear" and M.parse('{"broken": ').state == "unclear"
    assert M.parse('{"mistakes": [{"what": "x", "frame": 9}]}', [1.0, 2.0]).serious == ["x"]
    ranks = sorted([M.Verdict("mistakes", ["a", "b"]), M.Verdict("ok"), M.Verdict("unclear"),
                    M.Verdict("mistakes", ["a"])], key=M.Verdict.rank)
    assert [(r.state, len(r.serious)) for r in ranks] == [("ok", 0), ("unclear", 0), ("mistakes", 1), ("mistakes", 2)]
    assert M.frame_times(6.0, 3) == [1.0, 3.0, 5.0]


def test_the_setting(app):
    s = app.state.hub.settings
    assert M.tries(s) == 2                                              # the default
    for raw, want in (("0", 0), ("3", 3), ("9", M.MAX_TRIES), ("off", None), ("nonsense", 2), (1, 1)):
        s.update("agent", {"check_media": raw})
        assert M.tries(s) == want, raw
