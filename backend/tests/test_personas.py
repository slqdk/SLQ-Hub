"""Personas (0.74.0, Søren: "if i need a virtual personality that i can chat with ... always have that personality
answer me ... like a sports women, or a housewife" - "also with memory"): who the persona is instead of the
assistant's instructions, only the tools it is given, notes about the owner it keeps itself, its picture and voice."""
from __future__ import annotations

import asyncio
import base64
import io
import json

from PIL import Image

from app.chats import store as chats
from app.personas import store as S
from app.personas.prompt import persona_prompt

from .mocks import _png, wav_tone
from .test_agent import finish, last_user
from .test_agent import say as send
from .test_audio import install
from .test_images_models import _install
from .test_loras import KLEIN, ORIGIN, safetensors
from .test_people import add_person, client_from, sign_in

API = "/hub/api/personas"
ANNA = {"name": "Anna", "role": "marathon runner and physiotherapist from Aarhus",
        "personality": "Warm, direct and a little dry. Runs every morning, loves rye bread and hates excuses.",
        "speech": "Short sentences. Calls the user 'makker'.", "examples": "- Nej, nu må du lige tage dig sammen.",
        "greeting": "Hej makker! Har du løbet i dag?", "language": "Danish"}


async def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


async def anna(client, **kw) -> dict:
    return await ok(await client.post(API, json={**ANNA, **kw}, headers=ORIGIN))


async def chat_with(client, pid: str) -> dict:
    return (await ok(await client.post(f"{API}/{pid}/chat", headers=ORIGIN)))["chat"]


def tools_of(body: dict) -> set[str]:
    return {t["function"]["name"] for t in body.get("tools") or []}


async def heard(client, pid: str, body: dict | None = None) -> dict:
    """An answer read aloud (or the persona's sample): asked again while it is made, as the page does."""
    url = f"{API}/{pid}/say" if body else f"{API}/{pid}/sample"
    for _ in range(300):
        got = await ok(await client.post(url, json=body, headers=ORIGIN))
        if "path" in got:
            return got
        assert got == {"making": True}
        await asyncio.sleep(0.02)
    raise AssertionError("never read aloud")


async def test_a_chat_with_a_persona_has_its_prompt_its_tools_and_its_memory(signed_in, app, fakes):
    # what a persona must have, and may be
    for bad in ({"name": " "}, {"name": "X", "language": "Klingon"}, {"name": "X", "level": "extra"},
                {"name": "X", "model": "no-such-model"}, {"name": "X", "lora": "gone"}):
        assert (await signed_in.post(API, json=bad, headers=ORIGIN)).status_code == 400, bad
    p = await anna(signed_in)
    pid = p["id"]
    assert p["memory"] and not p["web"] and p["level"] == "quick" and p["notes"] == ""
    listed = await ok(await signed_in.get(API))
    assert [x["name"] for x in listed["personas"]] == ["Anna"]
    assert "Danish" in listed["options"]["languages"] and listed["options"]["models"]

    c = await chat_with(signed_in, pid)
    assert c["settings"]["persona"] == pid and c["title"] == "New chat"
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "remember", "arguments": {"action": "add",
                                                            "text": "Training for a marathon in April"}}]},
        {"content": "Godt, makker. Så starter vi i morgen kl. 6."},
        {"content": "Marathon training"},
    ]
    run = await finish(app, await send(signed_in, c["id"], "Jeg træner til et maraton i april",
                                       options={"model": "qwen3.6-35b-a3b", "level": "think"}))
    assert run.status == "done", run.events[-1]
    first = fakes.llama["requests"][0]
    system = first["messages"][0]["content"]
    assert system.startswith("You are Anna, marathon runner and physiotherapist from Aarhus. You are chatting with "
                             "Soren")
    assert "Runs every morning, loves rye bread" in system and "Calls the user 'makker'" in system
    assert "Nej, nu må du lige tage dig sammen" in system and "Always write in Danish" in system
    assert 'You greeted Soren with: "Hej makker! Har du løbet i dag?"' in system
    assert "# Your notes about Soren\nNone yet." in system
    assert "self-hosted AI workspace" not in system and "working folder" not in system      # not the assistant
    assert "You cannot look things up on the internet, make pictures, set reminders or send messages" in system
    assert tools_of(first) == {"remember"}
    # the persona's model and level, whatever the message asked for
    assert first["chat_template_kwargs"] == {"enable_thinking": False}
    assert run.events[[e["type"] for e in run.events].index("started")]["data"]["model"] == "qwen3.8-27b"
    assert app.state.hub.extras["personas"].notes(pid) == "- Training for a marathon in April\n"
    data = await ok(await signed_in.get(f"/hub/api/chats/{c['id']}"))
    assert data["chat"]["title"] == "Marathon training"
    assert data["persona"] == {"id": pid, "name": "Anna", "role": ANNA["role"], "picture": "",
                               "greeting": ANNA["greeting"], "read_aloud": False, "speech": False}
    row = next(r for r in await ok(await signed_in.get("/hub/api/chats")) if r["id"] == c["id"])
    assert row["persona"] == {"id": pid, "name": "Anna", "picture": ""}

    # the next chat with her: her notes, and the last conversation by its title
    c2 = await chat_with(signed_in, pid)
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Hvordan gik turen?"}, {"content": "Morning run"}]
    await finish(app, await send(signed_in, c2["id"], "Godmorgen"))
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "# Your notes about Soren\n- Training for a marathon in April" in system
    assert "# Your last conversations with Soren\n" in system and ": Marathon training" in system
    assert "Godmorgen" in last_user(fakes.llama["requests"][0])
    detail = await ok(await signed_in.get(f"{API}/{pid}"))
    assert [x["title"] for x in detail["chats"]] == ["Morning run", "Marathon training"]
    # a note written in a chat: the chat's prompt stays as it started (the model server keeps its cache of the
    # conversation); the next chat starts from it
    first_system = system
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": "remember", "arguments": {"action": "add",
                                                                                "text": "Has a dog called Bølle"}}]},
                              {"content": "Hils Bølle!"}]
    await finish(app, await send(signed_in, c2["id"], "Min hund Bølle løb med i dag"))
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Godt."}]
    await finish(app, await send(signed_in, c2["id"], "Hvad så nu?"))
    assert fakes.llama["requests"][0]["messages"][0]["content"] == first_system
    assert "Bølle" in app.state.hub.extras["personas"].notes(pid)
    c3 = await chat_with(signed_in, pid)
    assert c3["id"] != c2["id"] and (await chat_with(signed_in, pid))["id"] == c3["id"]     # an empty one is reused
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Hej igen."}, {"content": "Hello again"}]
    await finish(app, await send(signed_in, c3["id"], "Hej"))
    assert "- Has a dog called Bølle" in fakes.llama["requests"][0]["messages"][0]["content"]
    # the export shows what the next turn would be sent: the persona's prompt, model and tools
    from app.chats import export
    ctx = export._context(app.state.hub, app.state.hub.extras["agent"], chats.get_chat(c3["id"]), None)
    assert ctx["messages"][0]["content"].startswith("You are Anna") and ctx["hand_over_to"] == ""
    assert [t["name"] for t in ctx["tools"]] == ["remember"] and ctx["model"] == "qwen3.8-27b"

    # the web and no memory: other tools, no notes
    await ok(await signed_in.patch(f"{API}/{pid}", json={"web": True, "memory": False}, headers=ORIGIN))
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Ja."}]
    await finish(app, await send(signed_in, c2["id"], "Er der løb i Aarhus i weekenden?"))
    req = fakes.llama["requests"][0]
    assert tools_of(req) == {"web_search", "web_fetch"}
    system = req["messages"][0]["content"]
    assert "web_search and web_fetch let you look things up" in system and "remember tool" not in system
    assert "Your notes about" not in system and "do not remember earlier conversations" in system
    assert "Your last conversations" not in system

    # a chat cannot be given a persona, or lose it, by its settings - nor go into a project or a handover
    other = await ok(await signed_in.post("/hub/api/chats", json={"settings": {"persona": pid,
                                                                               "persona_memory": {"notes": "x"}}}))
    assert other["settings"]["persona"] is None and other["settings"]["persona_memory"] is None
    await ok(await signed_in.patch(f"/hub/api/chats/{c['id']}", json={"settings": {"persona": None,
                                                                                  "persona_memory": None}}))
    assert chats.get_chat(c["id"])["settings"]["persona"] == pid
    assert chats.get_chat(c["id"])["settings"]["persona_memory"]["notes"] == ""
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"new_project": "Running"}, headers=ORIGIN)
    assert r.status_code == 409 and "persona" in r.text
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/handover", json={}, headers=ORIGIN)
    assert r.status_code == 409 and "persona" in r.text

    # deleted: its chats become ordinary chats
    assert (await signed_in.delete(f"{API}/{pid}", headers=ORIGIN)).status_code == 200
    assert (await signed_in.get(f"{API}/{pid}")).status_code == 404
    assert chats.get_chat(c["id"])["settings"]["persona"] is None
    assert "persona_memory" not in chats.get_chat(c["id"])["settings"] or \
        chats.get_chat(c["id"])["settings"]["persona_memory"] is None
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Hello."}]
    await finish(app, await send(signed_in, c["id"], "Hej"))
    assert "self-hosted AI workspace" in fakes.llama["requests"][0]["messages"][0]["content"]
    assert (await ok(await signed_in.get(f"/hub/api/chats/{c['id']}")))["persona"] is None
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"new_project": "Running"}, headers=ORIGIN)
    assert r.status_code == 200, r.text


async def test_the_notes_kept_by_the_tool_and_corrected_by_the_owner(signed_in, app):
    p = await anna(signed_in)
    pid, store = p["id"], app.state.hub.extras["personas"]
    assert await store.remember(pid, "add", "Has two kids, Ida and Karl") == "Noted."
    assert await store.remember(pid, "add", "has two kids, ida and karl") == "That is in your notes already."
    assert await store.remember(pid, "add", "Runs in Riis Skov on Sundays") == "Noted."
    assert await store.remember(pid, "replace", "Runs in Marselisborg on Sundays", "Riis Skov") == "Changed."
    assert await store.remember(pid, "forget", "", "two kids") == "Forgotten."
    assert store.notes(pid) == "- Runs in Marselisborg on Sundays\n"
    # written as a list item, it is the same note; replaced by one that is there, it is there once
    assert await store.remember(pid, "add", "- Is running a marathon") == "Noted."
    assert await store.remember(pid, "add", "Is running a marathon") == "That is in your notes already."
    assert await store.remember(pid, "add", "Likes running in the rain") == "Noted."
    assert await store.remember(pid, "replace", "Is running a marathon", "in the rain") == "Changed."
    assert store.notes(pid) == "- Runs in Marselisborg on Sundays\n- Is running a marathon\n"
    assert await store.remember(pid, "add", "Likes running in the rain") == "Noted."
    for action, text, old, why in (("replace", "x", "nothing like it", "No note says that"),
                                   ("forget", "", "nothing like it", "No note says that"),
                                   ("forget", "", "running", "Several notes say that: Is running a marathon; Likes "
                                                             "running in the rain"),
                                   ("add", "y" * 301, "", "A note is at most 300 characters"),
                                   ("add", "", "", "Say what to remember"), ("shout", "x", "", "action is")):
        try:
            await store.remember(pid, action, text, old)
        except S.PersonaError as exc:
            assert why in str(exc)
        else:
            raise AssertionError(action)
    for i in range(30):                                    # full: the oldest must go first
        try:
            await store.remember(pid, "add", f"Note number {i} " + "x" * 280)
        except S.PersonaError as exc:
            assert "notes are full" in str(exc)
            break
    else:
        raise AssertionError("never full")
    # the owner reads and corrects them - not over a note the persona wrote meanwhile
    before = (await ok(await signed_in.get(f"{API}/{pid}")))["notes"]
    d = await ok(await signed_in.put(f"{API}/{pid}/notes", json={"text": "- Likes rye bread\r\n\n\n\n- Has a dog",
                                                                 "base": before}, headers=ORIGIN))
    assert d["notes"] == "- Likes rye bread\n\n- Has a dog\n"
    await store.remember(pid, "add", "Starts a new job on Monday")
    r = await signed_in.put(f"{API}/{pid}/notes", json={"text": "- Likes rye bread", "base": d["notes"]},
                            headers=ORIGIN)
    assert r.status_code == 409 and "changed while you edited" in r.text
    assert "new job" in store.notes(pid)
    r = await signed_in.put(f"{API}/{pid}/notes", json={"text": "x" * (S.NOTES_MAX + 1)}, headers=ORIGIN)
    assert r.status_code == 400
    d = await ok(await signed_in.put(f"{API}/{pid}/notes", json={"text": ""}, headers=ORIGIN))
    assert d["notes"] == ""


async def test_answers_read_aloud_in_the_personas_voice_and_kept(signed_in, app, fakes):
    install(app, "roest-v3")
    p = await anna(signed_in, voice="nic", read_aloud=True)
    pid = p["id"]
    assert [v["id"] for v in p["audio"]["voices"]] == ["mic", "nic"] and p["audio"]["language"] == "da"
    r = await signed_in.patch(f"{API}/{pid}", json={"voice": "bob"}, headers=ORIGIN)
    assert r.status_code == 400 and "no voice called bob" in r.text
    assert (await signed_in.patch(f"{API}/{pid}", json={"voice": "clip"}, headers=ORIGIN)).status_code == 400
    c = await chat_with(signed_in, pid)
    fakes.llama["replies"] = [{"content": "**Hej** Søren! Se [linket](https://x.dk/a) 😀\n\n```py\nprint(1)\n```"},
                              {"content": "Hej"}]
    await finish(app, await send(signed_in, c["id"], "Hej Anna"))
    msgs = chats.messages(c["id"])
    mid = next(m["id"] for m in msgs if m["role"] == "assistant")
    said = await heard(signed_in, pid, {"chat_id": c["id"], "message_id": mid})
    assert said["path"].startswith(f"said/m{mid}-") and said["path"].endswith(".mp3")
    req = fakes.speech["requests"][-1]
    assert req["text"] == "Hej Søren! Se linket" and req["language"] == "da"
    assert req["voice"]["path"].endswith("roest-v3/voices/nic.wav")
    audio = await signed_in.get(f"{API}/{pid}/file", params={"path": said["path"]})
    assert audio.status_code == 200 and audio.content
    n = len(fakes.speech["requests"])
    again = await ok(await signed_in.post(f"{API}/{pid}/say", json={"chat_id": c["id"], "message_id": mid},
                                          headers=ORIGIN))
    assert again == said and len(fakes.speech["requests"]) == n               # made once: there at once
    await ok(await signed_in.patch(f"{API}/{pid}", json={"voice": "mic"}, headers=ORIGIN))
    other = await heard(signed_in, pid, {"chat_id": c["id"], "message_id": mid})
    assert other["path"] != said["path"]
    assert fakes.speech["requests"][-1]["voice"]["path"].endswith("roest-v3/voices/mic.wav")
    assert (await signed_in.get(f"{API}/{pid}/file", params={"path": said["path"]})).status_code == 404
    # not a question, not another chat's answer
    user_mid = next(m["id"] for m in msgs if m["role"] == "user")
    assert (await signed_in.post(f"{API}/{pid}/say", json={"chat_id": c["id"], "message_id": user_mid},
                                 headers=ORIGIN)).status_code == 404
    plain = await ok(await signed_in.post("/hub/api/chats", json={}))
    assert (await signed_in.post(f"{API}/{pid}/say", json={"chat_id": plain["id"], "message_id": mid},
                                 headers=ORIGIN)).status_code == 404
    # an answer with a step in it: what was said before and after it, read as one
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [
        {"content": "Lige et øjeblik.", "tool_calls": [{"name": "remember", "arguments": {
            "action": "add", "text": "Runs in Marselisborg"}}]},
        {"content": "Så er det noteret, makker."}]
    await finish(app, await send(signed_in, c["id"], "Jeg løber i Marselisborg"))
    turn = [m for m in chats.messages(c["id"]) if m["id"] > mid and m["role"] == "assistant"]
    assert len(turn) == 2
    await heard(signed_in, pid, {"chat_id": c["id"], "message_id": turn[0]["id"]})
    assert fakes.speech["requests"][-1]["text"] == "Lige et øjeblik. Så er det noteret, makker."
    # a failure is said when the page asks again
    fakes.speech["error"] = "the model broke"
    body = {"chat_id": c["id"], "message_id": mid}
    await ok(await signed_in.patch(f"{API}/{pid}", json={"voice": "nic"}, headers=ORIGIN))
    for _ in range(300):
        r = await signed_in.post(f"{API}/{pid}/say", json=body, headers=ORIGIN)
        if r.status_code != 200:
            break
        await asyncio.sleep(0.02)
    assert r.status_code == 400 and "Reading it aloud failed" in r.text and "the model broke" in r.text
    fakes.speech.pop("error")
    # how it sounds: its greeting
    sample = await heard(signed_in, pid)
    assert sample["path"].startswith("said/sample-") and fakes.speech["requests"][-1]["text"] == ANNA["greeting"]

    # a clip of a real voice, converted in the sandbox; spoken with the clip itself
    ws = app.state.hub.env.workspace_dir
    clip = wav_tone(9.0)

    def convert(body):
        (ws / body["cwd"].removeprefix("/workspace/") / "voice.wav").write_bytes(clip)
        return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False}
    fakes.sandbox["handler"] = convert
    d = await ok(await signed_in.post(f"{API}/{pid}/voice", files={"file": ("anna.m4a", b"a recording", "audio/mp4")},
                                      headers=ORIGIN))
    assert d["voice"] == "clip" and d["voice_clip"].startswith("voice/") and d["voice_clip_seconds"] == 9.0
    assert not list((ws / "voice-tmp").iterdir())
    await heard(signed_in, pid, {"chat_id": c["id"], "message_id": mid})
    assert fakes.speech["requests"][-1]["voice"] == {"audio": base64.b64encode(clip).decode()}


async def test_portraits_made_with_the_picture_model_and_chosen(signed_in, app, fakes):
    RED, BLUE = (200, 30, 30, 255), (30, 30, 200, 255)
    p = await anna(signed_in, look="Short blond hair, freckles, about 35, a red running jacket.")
    pid, store = p["id"], app.state.hub.extras["personas"]
    fakes.comfy["pictures"] = [_png((16, 16), RED), _png((16, 16), BLUE)]
    d = await ok(await signed_in.post(f"{API}/{pid}/portraits", headers=ORIGIN))
    assert d["job"] == {"what": "portraits"}
    await asyncio.wait_for(asyncio.shield(store.tasks[pid]), 15)
    graph = fakes.comfy["prompts"][-1]
    text = json.dumps(graph, ensure_ascii=False)
    assert ("Portrait of a marathon runner and physiotherapist from Aarhus. Short blond hair, freckles, about 35, a red "
            "running jacket. Head and shoulders") in text
    d = await ok(await signed_in.get(f"{API}/{pid}"))
    assert len(d["variants"]) >= 2 and d["picture"] == d["variants"][0]["file"] and not d["error"]
    from app import db
    with db.connect() as con:                              # the persona keeps its own copies, not the uploads
        assert con.execute("SELECT COUNT(*) FROM uploads").fetchone()[0] == 0
    second = d["variants"][1]["file"]
    d = await ok(await signed_in.post(f"{API}/{pid}/pick", json={"file": second}, headers=ORIGIN))
    assert d["picture"] == second
    for bad in ("../persona.json", "pictures/../notes.md", "notes.md", "/etc/passwd"):
        assert (await signed_in.get(f"{API}/{pid}/file", params={"path": bad})).status_code == 404
    # a picture of the owner's
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1500), (10, 200, 10)).save(buf, "JPEG")
    d = await ok(await signed_in.post(f"{API}/{pid}/picture", files={"file": ("me.jpg", buf.getvalue(),
                                                                              "image/jpeg")}, headers=ORIGIN))
    assert d["picture"].startswith("pictures/up-") and d["variants"][0]["own"]
    got = await signed_in.get(f"{API}/{pid}/file", params={"path": d["picture"]})
    with Image.open(io.BytesIO(got.content)) as img:
        assert img.size == (1024, 768)
    r = await signed_in.post(f"{API}/{pid}/picture", files={"file": ("x.png", b"not a picture", "image/png")},
                             headers=ORIGIN)
    assert r.status_code == 400
    # a phone's photo, stored on its side with "turn it" in its EXIF: kept as it is shown
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (400, 200), (200, 10, 10)).save(buf, "JPEG", exif=exif.tobytes())
    d = await ok(await signed_in.post(f"{API}/{pid}/picture", files={"file": ("phone.jpg", buf.getvalue(),
                                                                              "image/jpeg")}, headers=ORIGIN))
    got = await signed_in.get(f"{API}/{pid}/file", params={"path": d["picture"]})
    with Image.open(io.BytesIO(got.content)) as img:
        assert img.size == (200, 400)
    # a LoRA for FLUX.2 klein: made with a klein model - and refused while none is downloaded
    lid = (await ok(await signed_in.post("/hub/api/loras", files={"file": ("anna.safetensors", safetensors(KLEIN),
                                                                           "x")},
                                         data={"trigger": "annaxv"}, headers=ORIGIN)))["id"]
    await ok(await signed_in.patch(f"{API}/{pid}", json={"lora": lid, "strength": 0.8}, headers=ORIGIN))
    r = await signed_in.post(f"{API}/{pid}/portraits", headers=ORIGIN)
    assert r.status_code == 400 and "no picture model of that kind is downloaded" in r.text
    _install(app.state.hub, "flux2-klein-4b")
    await ok(await signed_in.post(f"{API}/{pid}/portraits", headers=ORIGIN))
    await asyncio.wait_for(asyncio.shield(store.tasks[pid]), 15)
    graph = fakes.comfy["prompts"][-1]
    assert [(n["inputs"]["lora_name"], n["inputs"].get("strength_model")) for n in graph.values()
            if "lora_name" in n["inputs"]] == [(f"hub/{lid}.safetensors", 0.8)]
    assert "annaxv" in json.dumps(graph)
    # the folder keeps only what the persona points at
    folder = app.state.hub.env.data_dir / "personas" / pid / "pictures"
    d = await ok(await signed_in.get(f"{API}/{pid}"))
    assert sorted(f"pictures/{f.name}" for f in folder.iterdir()) == sorted(v["file"] for v in d["variants"])


async def test_people_cannot_reach_personas(signed_in, app):
    p = await anna(signed_in)
    await add_person(signed_in)
    async with client_from(app) as phone:
        await sign_in(phone)
        assert (await phone.get(API)).status_code in (401, 403)
        assert (await phone.post(API, json={"name": "x"}, headers=ORIGIN)).status_code in (401, 403)
        assert (await phone.get(f"{API}/{p['id']}")).status_code in (401, 403)


def test_what_is_read_aloud_and_the_prompt_without_tools():
    assert S.speakable("# Overskrift\n- **Løb** 5 km\n1. Spis `havregryn`\n> citat ~~nej~~ | ok") == \
        "Overskrift Løb 5 km Spis havregryn citat nej ok"
    assert [S.someone(x) for x in ("", "housewife.", "Engineer", "an old sailor", "Søren's coach")] == \
        ["a person", "a housewife", "an Engineer", "an old sailor", "Søren's coach"]
    assert S.guess_language("Hej, jeg har det godt") == "da" and S.guess_language("I am fine, thanks") == "en"
    assert S.guess_language("Good morning Søren! How are you today, Søren?") == "en"
    assert S.guess_language("Godmorgen Søren! Løb du i går?") == "da"
    assert S.tool_names({"memory": True, "web": False, "pictures": True}) == {"remember", "image_generate",
                                                                             "view_image"}
    text = persona_prompt({**S.DEFAULTS, "name": "Grete", "role": "", "language": ""}, user="Søren",
                          model_name="Qwen", tools=frozenset())
    assert text.startswith("You are Grete. You are chatting with Søren")
    assert "Write in the language of Søren's latest message." in text
    assert "do not remember earlier conversations" in text and "Your notes about" not in text
    assert "Your last conversations" not in text
    text = persona_prompt({**S.DEFAULTS, "name": "{x}", "role": "a {y}"}, user="S", model_name="M",
                          tools=frozenset({"image_generate", "remember"}), notes="- {z}",
                          recent=[("2026-11-01T08:00:00+00:00", "Kager {}")], summary="Talked about {cake}.")
    assert "You are {x}, a {y}." in text and "image_generate makes a picture" in text
    assert "You cannot look things up on the internet, set reminders or send messages" in text
    assert "- Sun 1 Nov 2026: Kager {}" in text and "- {z}" in text and "Talked about {cake}." in text
