"""A persona's tasks and posts (0.75.0, Søren: "I need to be able to give my persona tasks... like: At 7PM every
monday, Prepare a social media post with a picture or video about something ... and then list the posts for me to
review"): the schedule, a run in the task's own chat, the post handed in with post_draft, the owner's review - and
pictures of the persona itself, with its LoRA ("i have a lora for her") or from its portrait."""
from __future__ import annotations

import asyncio
import time
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.chats import store as chats
from app.personas import tasks as T
from app.personas.store import PersonaError
from app.projects import store as projects

from .mocks import _png, fake_mp4
from .test_agent import finish
from .test_agent import say as send
from .test_images_models import _install
from .test_loras import KLEIN, ORIGIN, safetensors
from .test_people import add_person, client_from, sign_in
from .test_personas import ANNA, anna, chat_with, ok, tools_of

API = "/hub/api/personas"
CPH = ZoneInfo("Europe/Copenhagen")
MONDAY_POST = {"title": "Monday post", "days": [0], "time": "19:00", "media": "picture",
               "instruction": "A short post about your Monday run, with a picture of you running."}


def schema_of(body: dict, name: str) -> dict:
    return next(t["function"]["parameters"] for t in body.get("tools") or [] if t["function"]["name"] == name)


async def with_lora(client, app) -> tuple[dict, str]:
    """Anna, with a LoRA for FLUX.2 klein (and a klein model to use it with)."""
    _install(app.state.hub, "flux2-klein-4b")
    lid = (await ok(await client.post("/hub/api/loras", files={"file": ("anna.safetensors", safetensors(KLEIN), "x")},
                                      data={"trigger": "annaxv"}, headers=ORIGIN)))["id"]
    return await anna(client, lora=lid, strength=0.9, look="Short blond hair, freckles, about 38."), lid


async def done(app, pid: str, tid: str) -> None:
    job = app.state.hub.extras["personas"].task_jobs.get(f"{pid}/{tid}")
    if job is not None:
        await asyncio.wait_for(asyncio.shield(job), 20)


def test_the_next_time_follows_the_week_and_summer_time():
    sunday = datetime(2026, 11, 1, 12, 0, tzinfo=CPH).timestamp()
    assert datetime.fromtimestamp(T.next_time([0], "19:00", sunday, CPH), CPH) == datetime(2026, 11, 2, 19, 0,
                                                                                             tzinfo=CPH)
    monday_late = datetime(2026, 11, 2, 19, 0, tzinfo=CPH).timestamp()        # at the time itself: the next week
    assert datetime.fromtimestamp(T.next_time([0], "19:00", monday_late, CPH), CPH).day == 9
    assert datetime.fromtimestamp(T.next_time([0, 3], "07:30", monday_late, CPH), CPH) == \
        datetime(2026, 11, 5, 7, 30, tzinfo=CPH)
    before_summer = datetime(2026, 3, 27, 12, 0, tzinfo=CPH).timestamp()       # summer time from 29 March
    nxt = T.next_time([0], "19:00", before_summer, CPH)
    assert datetime.fromtimestamp(nxt, ZoneInfo("UTC")).hour == 17             # 19:00 CEST
    assert T.next_time([], "19:00", sunday, CPH) is None and T.next_time([0], "7pm", sunday, CPH) is None


async def test_a_task_at_its_time_makes_a_post_for_review(signed_in, app, fakes):
    p, lid = await with_lora(signed_in, app)
    pid, store = p["id"], app.state.hub.extras["personas"]
    for bad in ({**MONDAY_POST, "days": []}, {**MONDAY_POST, "time": "25:00"}, {**MONDAY_POST, "media": "gif"},
                {**MONDAY_POST, "title": " "}, {**MONDAY_POST, "instruction": ""}, {**MONDAY_POST, "days": [7]}):
        assert (await signed_in.post(f"{API}/{pid}/tasks", json=bad, headers=ORIGIN)).status_code in (400, 422), bad
    d = await ok(await signed_in.post(f"{API}/{pid}/tasks", json=MONDAY_POST, headers=ORIGIN))
    t = d["tasks"][0]
    tid, due = t["id"], t["next_run"]
    when = datetime.fromtimestamp(due, CPH)
    assert (when.weekday(), when.hour, when.minute) == (0, 19, 0) and t["enabled"] and not t["running"]

    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {
            "prompt": "A woman running along the harbour at sunset", "me": True, "save_as": "run.png"}}]},
        {"tool_calls": [{"name": "post_draft", "arguments": {
            "text": "Mandagsløb ✅ 10 km langs havnen. #løb", "media": ["run.png"]}}]},
        {"content": "Lagt klar."},
    ]
    assert await store.tick(now=due - 60) == []                                # not yet
    assert await store.tick(now=due + 60) == [f"{pid}/{tid}"]
    await done(app, pid, tid)
    first = fakes.llama["requests"][0]
    system = first["messages"][0]["content"]
    assert "# Scheduled tasks" in system and "post_draft" in system and "With me: true the picture is of you" in system
    assert tools_of(first) == {"remember", "image_generate", "image_edit", "view_image", "post_draft"}
    assert "me" in schema_of(first, "image_generate")["properties"]
    user = first["messages"][-1]["content"]
    user = user if isinstance(user, str) else " ".join(b.get("text", "") for b in user)
    assert 'Scheduled task "Monday post" - Monday' in user and "picture of you running" in user
    # her picture: her LoRA, on a model of its kind
    graph = fakes.comfy["prompts"][-1]
    assert [(n["inputs"]["lora_name"], n["inputs"].get("strength_model")) for n in graph.values()
            if "lora_name" in n["inputs"]] == [(f"hub/{lid}.safetensors", 0.9)]
    assert "annaxv" in json.dumps(graph)
    # the post, waiting for review
    d = await ok(await signed_in.get(f"{API}/{pid}"))
    t = d["tasks"][0]
    assert t["last_error"] == "" and t["last_post"] and t["last_run"] and not t["running"]
    nxt = datetime.fromtimestamp(t["next_run"], CPH)                      # a week on, at the same time of day
    assert (nxt.date() - when.date()).days == 7 and (nxt.hour, nxt.minute) == (19, 0)
    review = await ok(await signed_in.get(f"{API}/posts", params={"status": "draft"}))
    assert len(review["posts"]) == 1
    post = review["posts"][0]
    assert post["text"] == "Mandagsløb ✅ 10 km langs havnen. #løb" and post["title"] == "Monday post"
    assert post["persona"] == {"id": pid, "name": "Anna", "picture": "", "instagram": ""} and post["status"] == "draft"
    assert [m["kind"] for m in post["media"]] == ["picture"]
    pic = await signed_in.get(f"{API}/{pid}/file", params={"path": post["media"][0]["file"]})
    assert pic.status_code == 200 and pic.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (await ok(await signed_in.get(API)))["drafts"] == 1
    # the task's chat: archived, its own - and not one of her conversations
    chat = chats.get_chat(t["chat_id"])
    assert chat["archived"] and chat["settings"]["persona_task"] == tid and chat["title"] == "Monday post"
    assert all(r["id"] != chat["id"] for r in await ok(await signed_in.get("/hub/api/chats")))
    assert [c["task"] for c in d["chats"]] == [tid]
    c = await chat_with(signed_in, pid)
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Hej!"}, {"content": "Hello"}]
    await finish(app, await send(signed_in, c["id"], "Hej"))
    assert "Monday post" not in fakes.llama["requests"][0]["messages"][0]["content"]
    assert "# Scheduled tasks" not in fakes.llama["requests"][0]["messages"][0]["content"]

    # the owner's review: the text changed, approved; then gone
    changed = await ok(await signed_in.patch(f"{API}/{pid}/posts/{post['id']}",
                                             json={"text": "Mandagsløb! #løb", "status": "approved"}, headers=ORIGIN))
    assert changed["text"] == "Mandagsløb! #løb" and changed["status"] == "approved"
    assert (await ok(await signed_in.get(f"{API}/posts", params={"status": "draft"})))["posts"] == []
    assert (await signed_in.patch(f"{API}/{pid}/posts/{post['id']}", json={"status": "posted"},
                                  headers=ORIGIN)).status_code == 400
    assert (await signed_in.delete(f"{API}/{pid}/posts/{post['id']}", headers=ORIGIN)).status_code == 200
    assert (await signed_in.get(f"{API}/{pid}/file", params={"path": post["media"][0]["file"]})).status_code == 404
    assert (await ok(await signed_in.get(f"{API}/{pid}/posts")))["posts"] == []

    # Make one now: the same chat, the earlier post in it
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Jeg glemte billedet."}]
    d = await ok(await signed_in.post(f"{API}/{pid}/tasks/{tid}/run", headers=ORIGIN))
    assert d["tasks"][0]["running"]
    await done(app, pid, tid)
    t = (await ok(await signed_in.get(f"{API}/{pid}")))["tasks"][0]
    assert t["chat_id"] == chat["id"] and t["last_error"] == "No post was handed in."
    assert "10 km langs havnen" in json.dumps(fakes.llama["requests"][0]["messages"])     # what she posted before

    # changed, off, deleted
    d = await ok(await signed_in.patch(f"{API}/{pid}/tasks/{tid}", json={"enabled": False}, headers=ORIGIN))
    assert d["tasks"][0]["next_run"] is None and await store.tick(now=due + 7 * 86400 + 60) == []
    d = await ok(await signed_in.patch(f"{API}/{pid}/tasks/{tid}", json={"enabled": True, "days": [2],
                                                                        "time": "07:15"}, headers=ORIGIN))
    nxt = datetime.fromtimestamp(d["tasks"][0]["next_run"], CPH)
    assert (nxt.weekday(), nxt.hour, nxt.minute) == (2, 7, 15)
    d = await ok(await signed_in.delete(f"{API}/{pid}/tasks/{tid}", headers=ORIGIN))
    assert d["tasks"] == []


async def test_a_time_missed_while_the_hub_was_off_is_left_out(signed_in, app):
    p = await anna(signed_in)
    pid, store = p["id"], app.state.hub.extras["personas"]
    d = await ok(await signed_in.post(f"{API}/{pid}/tasks", json={**MONDAY_POST, "media": "none"}, headers=ORIGIN))
    due = d["tasks"][0]["next_run"]
    assert await store.tick(now=due + T.CATCH_UP + 60) == []
    t = (await ok(await signed_in.get(f"{API}/{pid}")))["tasks"][0]
    assert "the Hub was not running" in t["last_error"] and t["next_run"] > due + T.CATCH_UP


async def test_what_a_post_may_be_handed_in_with(signed_in, app):
    p = await anna(signed_in)
    pid, store = p["id"], app.state.hub.extras["personas"]
    d = await ok(await signed_in.post(f"{API}/{pid}/tasks", json={**MONDAY_POST, "media": "none"}, headers=ORIGIN))
    tid = d["tasks"][0]["id"]
    async with store.edit(pid) as q:                   # a video task (no video model here to make one with)
        q["tasks"][0]["media"] = "video"
    ws = app.state.hub.env.workspace_dir
    wd = projects.Workdir(projects.chat_dir(ws, 9999), None, ws, app.state.hub.env.sandbox_workspace)
    wd.write("pic.png", _png((16, 16)))
    wd.write("clip.mp4", fake_mp4())
    wd.write("fake.png", b"not a picture")
    wd.write("notes.txt", "hello")
    for media, why in ((["pic.png"], "has a video"), (["../x.png"], "no file"), (["gone.png"], "no file"),
                       (["fake.png"], "not a picture"), (["notes.txt"], "takes pictures"),
                       (["pic.png"] * 5, "at most 4")):
        try:
            await store.hand_in(pid, tid, None, None, wd, "Text", media)
        except PersonaError as exc:
            assert why in str(exc), (media, str(exc))
        else:
            raise AssertionError(media)
    try:
        await store.hand_in(pid, tid, None, None, wd, " ", ["clip.mp4"])
    except PersonaError as exc:
        assert "text" in str(exc)
    post = await store.hand_in(pid, tid, None, None, wd, "Se mig løbe!", ["clip.mp4", "pic.png"])
    assert [m["kind"] for m in post["media"]] == ["video", "picture"]
    assert post["media"][0]["file"].endswith(".mp4")
    got = await signed_in.get(f"{API}/{pid}/file", params={"path": post["media"][0]["file"]})
    assert got.status_code == 200 and got.content == fake_mp4()


async def test_a_persona_makes_pictures_of_itself_with_its_lora_or_portrait(signed_in, app, fakes):
    p, lid = await with_lora(signed_in, app)
    pid = p["id"]
    await ok(await signed_in.patch(f"{API}/{pid}", json={"pictures": True}, headers=ORIGIN))
    c = await chat_with(signed_in, pid)
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "A breakfast table with rye bread"}}]},
        {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "A woman with a coffee", "me": True}}]},
        {"content": "Værsgo."}, {"content": "Breakfast"}]
    await finish(app, await send(signed_in, c["id"], "Vis mig din morgenmad, og dig selv"))
    loras = [[n["inputs"]["lora_name"] for n in g.values() if "lora_name" in n["inputs"]]
             for g in fakes.comfy["prompts"][-2:]]
    assert loras == [[], [f"hub/{lid}.safetensors"]]                       # the breakfast without her, then her
    assert "me" in schema_of(fakes.llama["requests"][0], "image_generate")["properties"]
    # no LoRA, but a portrait: made from it (an edit with the portrait as its picture)
    await ok(await signed_in.patch(f"{API}/{pid}", json={"lora": ""}, headers=ORIGIN))
    store = app.state.hub.extras["personas"]
    await store.set_picture(pid, _png((64, 64), (10, 200, 10, 255)))
    c2 = await chat_with(signed_in, pid)
    fakes.llama["requests"].clear()
    fakes.comfy["uploads"] = fakes.comfy.get("uploads") or []
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "jogging in a park", "me": True}}]},
        {"content": "Her."}, {"content": "Jogging"}]
    await finish(app, await send(signed_in, c2["id"], "Send et billede af dig"))
    graph = json.dumps(fakes.comfy["prompts"][-1])
    assert "LoadImage" in graph and "The person in the picture, with the same face" in graph
    # an ordinary chat has no me
    plain = await ok(await signed_in.post("/hub/api/chats", json={}))
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Hi"}, {"content": "Hi"}]
    await finish(app, await send(signed_in, plain["id"], "Hi"))
    req = fakes.llama["requests"][0]
    assert "image_generate" not in tools_of(req) or "me" not in schema_of(req, "image_generate")["properties"]


async def test_people_cannot_reach_tasks_or_posts(signed_in, app):
    p = await anna(signed_in)
    await add_person(signed_in)
    async with client_from(app) as phone:
        await sign_in(phone)
        assert (await phone.get(f"{API}/posts")).status_code in (401, 403)
        assert (await phone.post(f"{API}/{p['id']}/tasks", json=MONDAY_POST, headers=ORIGIN)).status_code in (401,
                                                                                                             403)


def test_the_task_tools():
    from app.personas.store import tool_names
    base = {"memory": True, "web": False, "pictures": False}
    assert tool_names(base) == {"remember"}
    assert tool_names(base, {"media": "none", "web": True}) == {"remember", "web_search", "web_fetch", "post_draft"}
    assert tool_names(base, {"media": "either"}) == {"remember", "image_generate", "image_edit", "view_image",
                                                     "video_generate", "post_draft"}
    assert ANNA["name"] == "Anna"


# ---------------------------------------------------------------- the review's findings (fixed)

EVERY_DAY = {"title": "Daily post", "days": [0, 1, 2, 3, 4, 5, 6], "time": "19:00", "media": "none",
             "instruction": "A short post about your day."}


def post_then_done(text="Dagens post #løb"):
    return [{"tool_calls": [{"name": "post_draft", "arguments": {"text": text}}]}, {"content": "Lagt klar."}]


async def test_after_days_off_the_newest_missed_time_is_made_up_for(signed_in, app, fakes):
    p = await anna(signed_in)
    pid, store = p["id"], app.state.hub.extras["personas"]
    d = await ok(await signed_in.post(f"{API}/{pid}/tasks", json=EVERY_DAY, headers=ORIGIN))
    tid, due = d["tasks"][0]["id"], d["tasks"][0]["next_run"]
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = post_then_done()
    newest = T.next_time(EVERY_DAY["days"], "19:00", T.next_time(EVERY_DAY["days"], "19:00", due, CPH), CPH)
    now = newest + 3600                                # off from before the time until two days and an hour later
    assert await store.tick(now=now) == [f"{pid}/{tid}"]      # the time an hour ago
    await done(app, pid, tid)
    user = fakes.llama["requests"][0]["messages"][-1]["content"]
    user = user if isinstance(user, str) else " ".join(b.get("text", "") for b in user)
    assert T.spoken_time(newest, CPH) in user and T.spoken_time(due, CPH) not in user
    t = store.load(pid)["tasks"][0]
    assert t["next_run"] > now and t["last_post"] and not t["working"]


async def test_each_run_starts_from_what_the_persona_knows_now(signed_in, app, fakes):
    p = await anna(signed_in)
    pid, store = p["id"], app.state.hub.extras["personas"]
    d = await ok(await signed_in.post(f"{API}/{pid}/tasks", json=EVERY_DAY, headers=ORIGIN))
    tid = d["tasks"][0]["id"]
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = post_then_done()
    store.start_task(pid, tid)
    await done(app, pid, tid)
    assert "Became vegan" not in fakes.llama["requests"][0]["messages"][0]["content"]
    await store.remember(pid, "add", "Became vegan in November")             # told her in another chat
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = post_then_done("Vegansk og glad")
    store.start_task(pid, tid)
    await done(app, pid, tid)
    assert "Became vegan in November" in fakes.llama["requests"][0]["messages"][0]["content"]
    # one user message a run: the one a restart cut off is replaced, not stacked
    cid = store.load(pid)["tasks"][0]["chat_id"]
    chats.add_message(cid, "user", [{"type": "text", "text": 'Scheduled task "Daily post" - cut off'}])
    async with store.edit(pid) as q:
        q["tasks"][0]["working"] = "run_gone"
    await store.start_schedule()                       # the Hub starts again
    store.schedule_job.cancel()
    t = store.load(pid)["tasks"][0]
    assert "the Hub restarted" in t["last_error"] and not t["working"]
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = post_then_done("Igen")
    store.start_task(pid, tid)
    await done(app, pid, tid)
    rows = chats.messages(cid)
    roles = [r["role"] for r in rows if r["role"] in ("user", "assistant")]
    assert all(not (a == b == "user") for a, b in zip(roles, roles[1:]))
    assert not any("cut off" in json.dumps(r["content"]) for r in rows)


async def test_a_task_that_looks_things_up_does_not_wait_for_an_answer(signed_in, app, fakes):
    app.state.hub.settings.update("web", {"access": "ask"})
    p = await anna(signed_in)
    pid, store = p["id"], app.state.hub.extras["personas"]
    d = await ok(await signed_in.post(f"{API}/{pid}/tasks", json={**EVERY_DAY, "web": True}, headers=ORIGIN))
    tid = d["tasks"][0]["id"]
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": "web_search", "arguments": {"query": "nyheder i dag"}}]},
                              *post_then_done()]
    store.start_task(pid, tid)
    await done(app, pid, tid)
    t = store.load(pid)["tasks"][0]
    run = app.state.hub.extras["agent"].runs.get(t["run"])
    assert "approval_needed" not in [e["type"] for e in run.events] and t["last_post"]


async def test_a_task_that_cannot_start_does_not_hold_up_the_others(signed_in, app, fakes, monkeypatch):
    a, b = await anna(signed_in), await anna(signed_in, name="Grete")
    store = app.state.hub.extras["personas"]
    tasks = {}
    for p in (a, b):
        tasks[p["id"]] = (await ok(await signed_in.post(f"{API}/{p['id']}/tasks", json=EVERY_DAY,
                                                        headers=ORIGIN)))["tasks"][0]
    first, second = [i["id"] for i in store.list()]    # the one the schedule looks at first fails
    real = store.start_task

    def start(pid, tid, due=None):
        if pid == first:
            raise PersonaError("Deleted meanwhile.", 404)
        real(pid, tid, due)

    monkeypatch.setattr(store, "start_task", start)
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = post_then_done()
    assert await store.tick(now=max(t["next_run"] for t in tasks.values()) + 60) == \
        [f"{second}/{tasks[second]['id']}"]
    await done(app, second, tasks[second]["id"])
    assert store.load(second)["tasks"][0]["last_post"]


async def test_a_task_still_working_waits_for_its_next_time(signed_in, app, fakes):
    a, b = await anna(signed_in), await anna(signed_in, name="Grete")
    store = app.state.hub.extras["personas"]
    ta = (await ok(await signed_in.post(f"{API}/{a['id']}/tasks", json=EVERY_DAY, headers=ORIGIN)))["tasks"][0]
    tb = (await ok(await signed_in.post(f"{API}/{b['id']}/tasks", json=EVERY_DAY, headers=ORIGIN)))["tasks"][0]
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "...", "delay": 1.0}] + post_then_done() * 2
    store.start_task(a["id"], ta["id"])                # Make one now, just before the time
    started = await store.tick(now=max(ta["next_run"], tb["next_run"]) + 60)
    assert started == [f"{b['id']}/{tb['id']}"]
    assert "the one before was still working" in store.load(a["id"])["tasks"][0]["last_error"]
    await done(app, a["id"], ta["id"])
    await done(app, b["id"], tb["id"])
    # a busy chat: nothing added, the task says why
    cid = store.load(a["id"])["tasks"][0]["chat_id"]
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Hej", "delay": 1.5}]
    rid = await send(signed_in, cid, "Hvordan gik det?")
    n = len(chats.messages(cid))
    store.start_task(a["id"], ta["id"])
    await done(app, a["id"], ta["id"])
    assert "Not started" in store.load(a["id"])["tasks"][0]["last_error"] and len(chats.messages(cid)) == n
    await finish(app, rid)


async def test_a_post_handed_in_twice_in_a_run_is_one_post(signed_in, app, fakes):
    p = await anna(signed_in)
    pid, store = p["id"], app.state.hub.extras["personas"]
    tid = (await ok(await signed_in.post(f"{API}/{pid}/tasks", json=EVERY_DAY, headers=ORIGIN)))["tasks"][0]["id"]
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": "post_draft", "arguments": {"text": "Første udkast"}}]},
                              {"tool_calls": [{"name": "post_draft", "arguments": {"text": "Bedre udkast"}}]},
                              {"content": "Klar."}]
    store.start_task(pid, tid)
    await done(app, pid, tid)
    assert [s["text"] for s in store.posts(pid)] == ["Bedre udkast"]
    assert "replaces the one you handed in before" in json.dumps(fakes.llama["requests"][2]["messages"])


async def test_what_cannot_be_made_is_said_before(signed_in, app, fakes):
    p = await anna(signed_in)
    pid, store = p["id"], app.state.hub.extras["personas"]
    assert not app.state.hub.extras["video"].available()
    r = await signed_in.post(f"{API}/{pid}/tasks", json={**MONDAY_POST, "media": "video"}, headers=ORIGIN)
    assert r.status_code == 400 and "No video model" in r.text
    # a time due but not yet started: changing the words keeps it, changing the schedule moves it
    d = await ok(await signed_in.post(f"{API}/{pid}/tasks", json=EVERY_DAY, headers=ORIGIN))
    tid = d["tasks"][0]["id"]
    async with store.edit(pid) as q:
        q["tasks"][0]["next_run"] = time.time() - 5
    due = store.load(pid)["tasks"][0]["next_run"]
    d = await ok(await signed_in.patch(f"{API}/{pid}/tasks/{tid}", json={"instruction": "Om din dag."},
                                       headers=ORIGIN))
    assert d["tasks"][0]["next_run"] == due
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "...", "delay": 1.0}] + post_then_done()
    assert await store.tick() == [f"{pid}/{tid}"]
    r = await signed_in.delete(f"{API}/{pid}", headers=ORIGIN)              # not while it works
    assert r.status_code == 409
    await done(app, pid, tid)
    d = await ok(await signed_in.patch(f"{API}/{pid}/tasks/{tid}", json={"time": "06:00"}, headers=ORIGIN))
    assert d["tasks"][0]["next_run"] > time.time()


async def test_no_picture_of_itself_where_it_cannot_be_made(signed_in, app, fakes):
    hub = app.state.hub
    gens = [e["id"] for e in hub.catalog.entries("comfy") if "generate" in (e.get("abilities") or [])
            and "edit" not in (e.get("abilities") or []) and not e.get("requires")]
    _install(hub, gens[0])
    for mid in ("qwen-image-2.1", "qwen-image-2.1-turbo"):          # no model that edits
        for f in hub.catalog.get("comfy", mid)["files"]:
            (hub.env.models_dir / f["dest"]).unlink(missing_ok=True)
    hub.settings.update("images", {"model": gens[0]})
    assert hub.extras["images"].available() and not hub.extras["images"].installed("edit")
    p = await anna(signed_in, pictures=True)
    store = hub.extras["personas"]
    await store.set_picture(p["id"], _png((64, 64), (10, 200, 10, 255)))
    assert store.me_way(store.load(p["id"])) == ""
    c = await chat_with(signed_in, p["id"])
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Hej"}, {"content": "Hej"}]
    await finish(app, await send(signed_in, c["id"], "Hej"))
    req = fakes.llama["requests"][0]
    assert "made from your portrait" not in req["messages"][0]["content"]
    assert "me" not in schema_of(req, "image_generate")["properties"]
