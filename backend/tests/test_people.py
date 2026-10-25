"""People: accounts the owner adds (Settings -> People). They sign in, maybe
only at home, reach only what their chat needs, and their chats live on their
own device: a device turn leaves nothing on the Hub."""
from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx

from app import db
from app.chats import store as chats
from app.projects import blobs

from .mocks import PNG_1x1, _png

PASSWORD = "a long password 1"


async def until(cond, timeout: float = 10) -> None:
    t0 = time.monotonic()
    while not cond():
        assert time.monotonic() - t0 < timeout, "timed out"
        await asyncio.sleep(0.01)


async def add_person(owner, **kw) -> dict:
    body = {"display_name": "Maria", "username": "maria", "password": PASSWORD, **kw}
    r = await owner.post("/hub/api/people", json=body, headers={"Origin": "http://hub"})
    assert r.status_code == 200, r.text
    return r.json()


def client_from(app, ip: str = "127.0.0.1", forwarded: str | None = None) -> httpx.AsyncClient:
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 4000)), base_url="http://hub",
                             headers=headers)


async def sign_in(c: httpx.AsyncClient, username: str = "maria", password: str = PASSWORD) -> httpx.Response:
    return await c.post("/hub/api/auth/login", json={"username": username, "password": password})


async def turn(app, c, body: dict) -> dict:
    """Start a device turn, wait for it, return the result."""
    r = await c.post("/hub/api/device/turns", json=body, headers={"Origin": "http://hub"})
    assert r.status_code == 200, r.text
    tid = r.json()["turn_id"]
    t = app.state.hub.extras["device"].turns[tid]
    await until(lambda: t.result is not None, 15)
    await app.state.hub.scheduler.drain()
    res = await c.get(f"/hub/api/device/turns/{tid}/result")
    assert res.status_code == 200, res.text
    return {"id": tid, **res.json()}


def user_msg(text: str, **more) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}, *more.get("blocks", [])]}


# ---------------------------------------------------------------- accounts

async def test_the_owner_adds_changes_and_removes_a_person(signed_in, app):
    p = await add_person(signed_in, away=False, may_run_code=True)
    assert p["display_name"] == "Maria" and p["username"] == "maria" and p["away"] is False and p["may_run_code"]
    assert [x["username"] for x in (await signed_in.get("/hub/api/people")).json()] == ["maria"]
    bad = [({"username": "x"}, "user name"), ({"username": "maria"}, "taken"), ({"username": "MARIA"}, "taken"),
           ({"username": "soren"}, "taken"), ({"username": "per", "password": "short"}, "at least 10")]
    for change, words in bad:
        r = await signed_in.post("/hub/api/people", json={"display_name": "X", "username": "x", "password": PASSWORD,
                                                          **change}, headers={"Origin": "http://hub"})
        assert r.status_code == 400 and words in r.json()["detail"], (change, r.text)
    r = await signed_in.patch(f"/hub/api/people/{p['id']}", json={"away": True, "display_name": "Maria L"},
                              headers={"Origin": "http://hub"})
    assert r.json()["away"] is True and r.json()["display_name"] == "Maria L"
    # a new password signs her out everywhere
    async with client_from(app) as phone:
        assert (await sign_in(phone)).status_code == 200
        assert (await phone.get("/hub/api/models")).status_code == 200
        await signed_in.patch(f"/hub/api/people/{p['id']}", json={"password": "another long one"},
                              headers={"Origin": "http://hub"})
        assert (await phone.get("/hub/api/models")).status_code == 401
        assert (await sign_in(phone, password="another long one")).status_code == 200
        r = await signed_in.post(f"/hub/api/people/{p['id']}/sign-out", headers={"Origin": "http://hub"})
        assert r.json()["sessions"] == 1 and (await phone.get("/hub/api/models")).status_code == 401
    assert (await signed_in.delete(f"/hub/api/people/{p['id']}", headers={"Origin": "http://hub"})).status_code == 200
    assert (await signed_in.get("/hub/api/people")).json() == []
    # the owner cannot be changed or removed here
    with db.connect() as con:
        owner_id = con.execute("SELECT id FROM users WHERE username='soren'").fetchone()["id"]
    assert (await signed_in.delete(f"/hub/api/people/{owner_id}", headers={"Origin": "http://hub"})).status_code == 404


async def test_home_only_means_home_only(signed_in, app):
    await add_person(signed_in, away=False)
    async with client_from(app, "10.9.9.9", "203.0.113.7") as away:          # through the proxy, from outside
        r = await sign_in(away)
        assert r.status_code == 403 and "home network" in r.json()["detail"]
    async with client_from(app, "10.9.9.9", "30.11.0.50") as home:           # through the proxy, from the LAN
        assert (await sign_in(home)).status_code == 200
        cookie = home.cookies.get("hub_session")
        assert (await home.get("/hub/api/models")).status_code == 200
    async with client_from(app, "10.9.9.9", "203.0.113.7") as away:          # the same session, taken outside
        away.cookies.set("hub_session", cookie)
        assert (await away.get("/hub/api/models")).status_code == 401
        assert (await away.get("/hub/api/auth/status")).json()["signedIn"] is False


async def test_home_only_is_refused_outside_before_the_password(signed_in, app):
    await add_person(signed_in, away=False)
    async with client_from(app, "10.9.9.9", "203.0.113.7") as away:
        for password in (PASSWORD, "a wrong password"):                 # nothing to learn about the password
            r = await sign_in(away, password=password)
            assert r.status_code == 403 and "home network" in r.json()["detail"]


async def test_away_works_outside_and_the_owner_is_not_limited(signed_in, app):
    await add_person(signed_in, away=True)
    async with client_from(app, "10.9.9.9", "203.0.113.7") as away:
        r = await sign_in(away)
        assert r.status_code == 200 and r.json()["user"]["role"] == "member"
        st = (await away.get("/hub/api/auth/status")).json()
        assert st["signedIn"] and st["user"]["display_name"] == "Maria" and st["user"]["may_run_code"] is False
        assert (await sign_in(away, "soren", "correct horse battery")).status_code == 200


async def test_a_person_reaches_only_what_the_chat_needs(signed_in, app, api_key):
    await signed_in.put("/hub/api/settings", json={"section": "cloud", "values": {"api_key": "sk-secret-123"}},
                        headers={"Origin": "http://hub"})
    await add_person(signed_in)
    async with client_from(app) as phone:
        assert (await sign_in(phone)).status_code == 200
        for path in ("/hub/api/chats", "/hub/api/projects", "/hub/api/libraries", "/hub/api/keys", "/hub/api/people",
                     "/hub/api/memory", "/hub/api/mcp", "/hub/api/system", "/hub/api/connection", "/hub/api/runs",
                     "/hub/api/skills/anything", "/hub/api/sync-tokens", "/hub/api/models/hf/search?q=x"):
            r = await phone.get(path)
            assert r.status_code == 403 and "owner" in r.json()["detail"], path
        for method, path in (("PUT", "/hub/api/settings"), ("POST", "/hub/api/chats"), ("POST", "/hub/api/projects"),
                             ("POST", "/hub/api/keys"), ("POST", "/hub/api/uploads"),
                             ("DELETE", "/hub/api/models/qwen3.8-27b"), ("POST", "/hub/api/models/unload")):
            r = await phone.request(method, path, json={}, headers={"Origin": "http://hub"})
            assert r.status_code == 403, (method, path)
        assert (await phone.get("/api/sync/projects")).status_code == 401
        s = (await phone.get("/hub/api/settings")).json()["settings"]
        assert set(s) == {"models", "agent", "images"} and "sk-secret" not in json.dumps(s)
        assert (await phone.get("/hub/api/models")).status_code == 200
        assert (await phone.get("/hub/api/skills")).status_code == 200
        # the model API takes keys: her sign-in is for her chat (where the Hub keeps nothing)
        for method, path in (("GET", "/v1/models"), ("POST", "/v1/chat/completions"), ("POST", "/v1/images/generations"),
                             ("GET", "/api/tags"), ("POST", "/api/chat")):
            r = await phone.request(method, path, json={"model": "qwen3.8-27b", "prompt": "x", "messages": []})
            assert r.status_code == 401, (method, path, r.status_code)


# ---------------------------------------------------------------- device turns

async def test_a_device_turn_answers_and_keeps_nothing(signed_in, app, fakes):
    hub = app.state.hub
    await add_person(signed_in)
    fakes.llama["replies"] = [{"content": "Hej Maria, det er en kat."}, {"content": "Kat på foto"}]
    photo = _png((40, 30))
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {
            "title": "New chat", "settings": {"model": "qwen3.8-27b", "thinking": False, "libraries": [3]},
            "messages": [user_msg("Hvad er det?", blocks=[
                {"type": "image", "sha": "local-photo-1", "name": "kat.png", "mime": "image/png",
                 "path": ".hub/attachments/kat.png"}])],
            "files": [{"path": ".hub/attachments/kat.png", "mime": "image/png",
                       "data": base64.b64encode(photo).decode()}]})
    assert res["status"] == "done" and res["title"] == "Kat på foto"
    assert res["messages"][-1]["content"][-1] == {"type": "text", "text": "Hej Maria, det er en kat."}
    req = fakes.llama["requests"][0]
    system = req["messages"][0]["content"]
    assert "The user is Maria." in system and "# Memory" not in system and "no code execution" in system
    assert "temporary folder" in system and "live on the user's own device" in system
    names = {t["function"]["name"] for t in req.get("tools") or []}
    assert {"web_search", "image_generate", "read_file", "artifact", "task"} <= names
    assert not names & {"bash", "memory", "library_search"}
    assert any(p.get("type") == "image_url" for p in req["messages"][-1]["content"])      # the photo was seen
    # nothing stayed: no chat, no run, no prompt, no folder, not the photo
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM run_prompts").fetchone()[0] == 0
    turns = hub.extras["device"]
    folder = turns.turns[res["id"]].folder
    sha = next(iter(turns.turns[res["id"]].created))
    assert folder.is_dir() and blobs.exists(sha)
    async with client_from(app) as phone:
        await sign_in(phone)
        assert (await phone.get(f"/hub/media/blob/{sha}")).status_code == 200       # her answer's picture
        r = await phone.post(f"/hub/api/device/turns/{res['id']}/done", headers={"Origin": "http://hub"})
        assert r.status_code == 200
        assert (await phone.get(f"/hub/api/device/turns/{res['id']}/result")).status_code == 404
    assert not folder.exists() and not blobs.exists(sha) and chats.get_upload(sha) is None
    # the owner's own chats know nothing of it, and her queue job carried her name but no title
    assert (await signed_in.get("/hub/api/chats", params={"all": True})).json() == []
    with db.connect() as con:
        jobs = [dict(r) for r in con.execute("SELECT owner, label FROM jobs_log WHERE kind='agent'").fetchall()]
    assert jobs == [{"owner": "Maria", "label": "Chat"}]


async def test_a_hand_over_keeps_her_own_prompt(signed_in, app, fakes):
    """When her small model hands the turn over to the stronger one, the stronger model gets her prompt - her
    name, no memory - not the owner's (0.15.0: the hand-over rebuilt the owner's prompt, memory index and all)."""
    r = await signed_in.put("/hub/api/memory/file", json={"path": "/memories/owner.md",
                                                         "content": "# The owner's private note\nNot for others."})
    assert r.status_code == 200, r.text
    await add_person(signed_in)

    def responder(body):
        if "short title" in json.dumps(body["messages"][-1]):
            return {"content": "Hjælp"}
        if body["model"] == "qwen3.8-27b":
            return {"content": "Her er svaret."}
        return {"tool_calls": [{"name": "escalate", "arguments": {"reason": "too hard for me"}}]}
    fakes.llama["responder"] = responder
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "New chat", "settings": {"model": "qwen3.6-35b-a3b"},
                                      "messages": [user_msg("Et svært spørgsmål")]})
    assert res["status"] == "done", res
    big = [b for b in fakes.llama["requests"] if b["model"] == "qwen3.8-27b"]
    assert big, "no hand-over"
    system = big[0]["messages"][0]["content"]
    assert "The user is Maria." in system and "# Memory" not in system and "private note" not in system
    assert "handed over to you by" in system


async def test_pictures_files_and_artifacts_go_back_to_the_device(signed_in, app, fakes):
    await add_person(signed_in)
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a red fox in snow", "fast": True}}]},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "notes.md", "content": "# Fox\n"}}]},
        {"tool_calls": [{"name": "artifact", "arguments": {"action": "create", "id": "fox-page", "title": "Fox",
                                                           "type": "html", "content": "<h1>Fox</h1>"}}]},
        {"content": "Here is your fox."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Fox", "messages": [user_msg("Draw a fox")],
                                      "files": [{"path": "old.txt", "data": base64.b64encode(b"keep").decode()}]})
        assert res["status"] == "done", res
        tool = next(b for m in res["messages"] for b in m["content"] if b["type"] == "tool_result")
        pic = tool["images"][0]["sha"]
        assert (await phone.get(f"/hub/api/device/turns/{res['id']}/blob/{pic}")).content.startswith(b"\x89PNG")
        assert {f["path"] for f in res["files"]} >= {"notes.md"} and "old.txt" not in {f["path"] for f in res["files"]}
        card = next(s for s in res["steps"] if s["type"] == "files")["files"]          # the card under the answer
        assert {(f["path"], f["new"]) for f in card} == {("notes.md", True)}
        r = await phone.get(f"/hub/api/device/turns/{res['id']}/file", params={"path": "notes.md"})
        assert r.text == "# Fox\n"
        assert (await phone.get(f"/hub/api/device/turns/{res['id']}/file", params={"path": "old.txt"})).status_code == 404
        assert res["artifacts"] == [{"id": "fox-page", "title": "Fox", "type": "html", "version": 1,
                                     "content": "<h1>Fox</h1>"}]
        with db.connect() as con:
            assert con.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        # the owner cannot fetch her answer, nor its picture
        assert (await signed_in.get(f"/hub/api/device/turns/{res['id']}/result")).status_code == 404
        await phone.post(f"/hub/api/device/turns/{res['id']}/done", headers={"Origin": "http://hub"})
        assert not blobs.exists(pic)


async def test_the_device_says_when_each_message_was_sent(signed_in, app, fakes):
    await add_person(signed_in)
    fakes.llama["replies"] = [{"content": "Hi again."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        await turn(app, phone, {"title": "Hi", "messages": [
            {**user_msg("Hi"), "created_at": "2026-10-23T04:57:43.120Z"},
            {"role": "assistant", "content": [{"type": "text", "text": "Hello."}], "created_at": "2026-10-23T04:58:00Z"},
            user_msg("And now?")]})
    users = [m["content"] for m in fakes.llama["requests"][0]["messages"] if m["role"] == "user"]
    assert users == ["[Fri 23 Oct 2026 06:57]\nHi", "And now?"]                  # (an old device sends no time)


async def test_the_artifact_can_be_changed_in_the_next_turn(signed_in, app, fakes):
    await add_person(signed_in)
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "artifact", "arguments": {"action": "update", "id": "fox-page", "old_str": "Fox",
                                                           "new_str": "Red fox"}}]},
        {"content": "Changed."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Fox", "messages": [user_msg("Make it a red fox")],
                                      "artifacts": [{"id": "fox-page", "title": "Fox", "type": "html", "version": 1,
                                                     "content": "<h1>Fox</h1>"}]})
    assert res["artifacts"][0]["content"] == "<h1>Red fox</h1>" and res["artifacts"][0]["version"] == 2


async def test_code_only_when_allowed_and_confined(signed_in, app, fakes):
    p = await add_person(signed_in, may_run_code=True)
    fakes.llama["replies"] = [{"tool_calls": [{"name": "bash", "arguments": {"command": "ls"}}]}, {"content": "Done."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Code", "messages": [user_msg("List the files")]})
        assert res["status"] == "done"
        ex = fakes.sandbox["execs"][-1]
        root = ex["confine"]["root"]
        assert ex["confine"]["require"] is True and root.startswith("/workspace/device/run_")
        assert ex["cwd"] == root and ex["confine"]["home"] == root + "/.hub/home"
        system = fakes.llama["requests"][0]["messages"][0]["content"]
        assert "bash starts in it" in system
        # without Landlock in the sandbox there is no bash for people (the owner keeps it)
        fakes.sandbox["landlock"] = 0
        app.state.hub.extras["device"]._confine = (float("-inf"), False)
        fakes.llama["requests"] = []
        fakes.llama["replies"] = [{"content": "No code here."}]
        await turn(app, phone, {"title": "Code", "messages": [user_msg("List the files")]})
        names = {t["function"]["name"] for t in fakes.llama["requests"][0].get("tools") or []}
        assert "bash" not in names
    # and switched off by the owner: none either
    fakes.sandbox["landlock"] = 7
    app.state.hub.extras["device"]._confine = (float("-inf"), False)
    await signed_in.patch(f"/hub/api/people/{p['id']}", json={"may_run_code": False}, headers={"Origin": "http://hub"})
    async with client_from(app) as phone:
        await sign_in(phone)
        fakes.llama["requests"] = []
        fakes.llama["replies"] = [{"content": "No code."}]
        await turn(app, phone, {"title": "Code", "messages": [user_msg("List the files")]})
        assert "bash" not in {t["function"]["name"] for t in fakes.llama["requests"][0].get("tools") or []}


async def test_turns_are_private_and_requests_checked(signed_in, app, fakes):
    await add_person(signed_in)
    await add_person(signed_in, display_name="Per", username="per")
    fakes.llama["replies"] = [{"content": "Hi."}]
    async with client_from(app) as maria, client_from(app) as per:
        await sign_in(maria)
        await sign_in(per, "per")
        res = await turn(app, maria, {"title": "Hi", "messages": [user_msg("Hi")]})
        assert (await per.get(f"/hub/api/device/turns/{res['id']}/result")).status_code == 404
        assert (await per.post(f"/hub/api/device/turns/{res['id']}/done", headers={"Origin": "http://hub"})).status_code == 404
        for bad in ({}, {"messages": []}, {"messages": [{"role": "assistant", "content": "x"}]},
                    {"messages": [user_msg("x")], "files": [{"path": "../../etc/passwd", "data": "eA=="}]},
                    {"messages": [user_msg("x")], "files": [{"path": ".hub/home/.bashrc", "data": "eA=="}]}):
            r = await maria.post("/hub/api/device/turns", json=bad, headers={"Origin": "http://hub"})
            assert r.status_code == 400, (bad, r.text)
        assert not any(t.user["username"] == "maria" and t.ended is None
                       for t in app.state.hub.extras["device"].turns.values())


async def test_a_device_that_never_comes_back_cannot_pile_up_turns(signed_in, app, fakes):
    hub = app.state.hub
    await add_person(signed_in)
    fakes.llama["replies"] = [{"content": "Hi."}] * 8
    async with client_from(app) as phone:
        await sign_in(phone)
        ids = [(await turn(app, phone, {"title": "Hi", "messages": [user_msg("Hi")]}))["id"] for _ in range(6)]
    waiting = [t.run.id for t in hub.extras["device"].turns.values() if t.user["username"] == "maria"]
    assert len(waiting) == 5 and ids[0] not in waiting           # four kept for the device, and the newest
    assert not (hub.extras["device"].root / ids[0]).exists()


async def test_the_queue_as_a_person_sees_it(signed_in, app, fakes):
    from app.ui.system_routes import queue_for
    snap = {"current": "llm:qwen3.8-27b", "switching": None, "last_error": "boom",
            "running": [{"id": 1, "owner": "web", "label": "Secret project chat", "meta": {"chat_id": 4, "model": "m"}}],
            "pending": [{"id": 2, "owner": "Maria", "label": "Chat", "meta": {"user_id": 7, "model": "m"}}]}
    seen = queue_for(snap, {"id": 7, "role": "member"})
    assert seen["running"][0]["label"] == "Another job" and seen["running"][0]["owner"] == ""
    assert "chat_id" not in seen["running"][0]["meta"] and seen["last_error"] is None
    assert seen["pending"][0]["label"] == "Chat"
    assert queue_for(snap, {"id": 1, "role": "admin"}) == snap


async def test_left_behind_turns_are_swept(signed_in, app, fakes):
    hub = app.state.hub
    await add_person(signed_in)
    fakes.llama["replies"] = [{"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "fox", "fast": True}}]},
                              {"content": "Fox."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Fox", "messages": [user_msg("Draw a fox")]})
    t = hub.extras["device"].turns[res["id"]]
    created, folder = set(t.created), t.folder
    assert created and all(blobs.exists(s) for s in created)
    # the list of files to delete is kept outside the folder her code and tools may write to
    assert t.ledger.parent == hub.env.data_dir / "device-turns" and set(t.ledger.read_text().split()) == created
    t.ended = time.time() - 4000                       # the device never came back
    hub.extras["device"].sweep()
    assert res["id"] not in hub.extras["device"].turns and not folder.exists() and not t.ledger.exists()
    assert not any(blobs.exists(s) for s in created)
    assert hub.extras["agent"].runs.get(res["id"]) is None
    # what a restart left behind goes too: the folder, and the files its ledger names
    sha = chats.save_upload(_png((9, 9)), "left.png", "image/png")["sha"]
    owners = chats.save_upload(_png((11, 11)), "owner.png", "image/png")["sha"]
    left = hub.extras["device"].root / "run_old"
    (left / ".hub").mkdir(parents=True)
    (left / ".hub" / "created").write_text(owners + "\n")       # written by her code: not trusted
    (hub.env.data_dir / "device-turns" / "run_old").write_text(sha + "\n")
    hub.extras["device"].sweep()
    assert not left.exists() and not blobs.exists(sha) and blobs.exists(owners)
    assert not (hub.env.data_dir / "device-turns" / "run_old").exists()


async def test_the_hub_forgets_the_run_and_hears_about_deleted_files(signed_in, app, fakes):
    hub = app.state.hub
    await add_person(signed_in)
    fakes.llama["replies"] = [{"tool_calls": [{"name": "delete_file", "arguments": {"path": "old.txt"}}]},
                              {"tool_calls": [{"name": "move_file", "arguments": {"source": "a.txt",
                                                                                  "destination": "b.txt"}}]},
                              {"content": "Tidied up."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Tidy", "messages": [user_msg("Tidy up")], "files": [
            {"path": "old.txt", "data": base64.b64encode(b"old").decode()},
            {"path": "a.txt", "data": base64.b64encode(b"a").decode()},
            {"path": "keep.txt", "data": base64.b64encode(b"keep").decode()}]})
        assert res["removed"] == ["a.txt", "old.txt"] and [f["path"] for f in res["files"]] == ["b.txt"]
        assert hub.extras["agent"].runs.get(res["id"]) is not None          # until the device has it
        await phone.post(f"/hub/api/device/turns/{res['id']}/done", headers={"Origin": "http://hub"})
        assert hub.extras["agent"].runs.get(res["id"]) is None


async def test_comfyui_keeps_no_copy_of_her_pictures(signed_in, app, fakes, tmp_path):
    hub = app.state.hub
    hub.comfy.files = fakes.comfy["files"] = tmp_path / "comfy"
    await add_person(signed_in)
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a red fox", "fast": True}}]},
        {"tool_calls": [{"name": "image_edit", "arguments": {"prompt": "make it blue", "image": "fox.png"}}]},
        {"content": "Two foxes."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Fox", "messages": [user_msg("Draw a fox, then a blue one")],
                                      "files": [{"path": "fox.png", "mime": "image/png",
                                                 "data": base64.b64encode(_png((20, 20))).decode()}]})
        assert res["status"] == "done", res
    assert len(fakes.comfy["prompts"]) == 2 and fakes.comfy["uploads"]
    assert sorted(fakes.comfy["history_deleted"]) == ["p1", "p2"]        # the prompts and file names
    assert not any((tmp_path / "comfy").rglob("*.png"))                   # its output and input pictures
    # the owner's pictures stay where they always were
    fakes.comfy["history_deleted"] = []
    items = await hub.extras["images"].generate("an owl", fast=True)
    assert items and (tmp_path / "comfy" / "output" / "p3.png").is_file() and fakes.comfy["history_deleted"] == []


async def test_a_picture_someone_else_stores_meanwhile_is_kept(signed_in, app, fakes):
    """Files are stored by content. One her turn stored first and the owner then stored too (or another of her
    answers uses) is not deleted when her turn is done."""
    hub = app.state.hub
    await add_person(signed_in)
    await add_person(signed_in, display_name="Per", username="per")
    photo = _png((33, 21))
    blob = {"sha": "p1", "name": "p.png", "mime": "image/png", "data": base64.b64encode(photo).decode()}
    ask = user_msg("What is it?", blocks=[{"type": "image", "sha": "p1", "name": "p.png", "mime": "image/png"}])
    fakes.llama["replies"] = [{"content": "A picture."}] * 3
    async with client_from(app) as maria, client_from(app) as per:
        await sign_in(maria)
        await sign_in(per, "per")
        a = await turn(app, maria, {"title": "P", "messages": [ask], "blobs": [blob]})
        b = await turn(app, per, {"title": "P", "messages": [ask], "blobs": [blob]})
        sha = next(iter(hub.extras["device"].turns[a["id"]].created))
        await maria.post(f"/hub/api/device/turns/{a['id']}/done", headers={"Origin": "http://hub"})
        assert blobs.exists(sha)                                         # Per's answer still shows it
        await per.post(f"/hub/api/device/turns/{b['id']}/done", headers={"Origin": "http://hub"})
        assert not blobs.exists(sha)                                     # now nobody does
        c = await turn(app, maria, {"title": "P", "messages": [ask], "blobs": [blob]})
        assert sha in hub.extras["device"].turns[c["id"]].created
        assert chats.save_upload(photo, "mine.png", "image/png")["sha"] == sha    # the owner stores it too
        await maria.post(f"/hub/api/device/turns/{c['id']}/done", headers={"Origin": "http://hub"})
    assert blobs.exists(sha) and chats.get_upload(sha) is not None


async def test_an_owners_picture_is_not_deleted_by_a_persons_turn(signed_in, app, fakes):
    """Files are stored by content: a person's turn deletes only files it created, not the same picture the
    owner already had."""
    owner_pic = chats.save_upload(PNG_1x1, "owner.png", "image/png")["sha"]
    await add_person(signed_in)
    fakes.llama["replies"] = [{"content": "A pixel."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Px", "messages": [user_msg("What is it?", blocks=[
            {"type": "image", "sha": "p1", "name": "px.png", "mime": "image/png"}])],
            "blobs": [{"sha": "p1", "name": "px.png", "mime": "image/png", "data": base64.b64encode(PNG_1x1).decode()}]})
        await phone.post(f"/hub/api/device/turns/{res['id']}/done", headers={"Origin": "http://hub"})
    assert blobs.exists(owner_pic) and chats.get_upload(owner_pic) is not None
