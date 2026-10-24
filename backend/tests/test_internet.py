"""Internet for the assistant (Settings -> Assistant -> Web): allowed, or ask first. Asking first, web search, a
page from a site and code that goes online wait for the user's answer - allow once, always (search, the site,
code) or deny - and code otherwise runs without the internet."""
from __future__ import annotations

import json
import shlex

from app import db
from app.agent import internet

from .test_agent import finish, new_chat, say
from .test_people import add_person, client_from, sign_in, until, user_msg


def asking(app, on: bool = True) -> None:
    app.state.hub.settings.update("web", {"access": "ask" if on else "allow"})


def approvals(run) -> list[dict]:
    return [e["data"] for e in run.events if e["type"] == "approval_needed"]


async def answer(client, app, run_id: str, decision: str, nth: int = 1) -> dict:
    """Wait for the turn's nth question, answer it, return what was asked."""
    run = app.state.hub.extras["agent"].runs.get(run_id)
    await until(lambda: len(approvals(run)) >= nth)
    q = approvals(run)[nth - 1]
    r = await client.post(f"/hub/api/runs/{run_id}/approve", json={"call_id": q["id"], "decision": decision})
    assert r.status_code == 200, r.text
    return q


def result_of(run, name: str) -> dict:
    return [e["data"] for e in run.events if e["type"] == "tool_result" and e["data"]["name"] == name][-1]


def permissions() -> set[str]:
    with db.connect() as con:
        return {r["tool"] for r in con.execute("SELECT tool FROM tool_permissions")}


async def test_allowed_is_as_before(signed_in, app, fakes):
    fakes.llama["replies"] = [{"tool_calls": [{"name": "web_search", "arguments": {"query": "vejret i Vojens"}}]},
                              {"content": "Sol."}, {"content": "Vejret"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Hvordan bliver vejret?"))
    assert run.status == "done" and not approvals(run) and result_of(run, "web_search")["ok"]
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "has internet access" in system and "ask before you go online" not in system


async def test_web_search_asks_first_once_always_or_deny(signed_in, app, fakes):
    asking(app)
    c = await new_chat(signed_in)

    def search(q: str):
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = [{"tool_calls": [{"name": "web_search", "arguments": {"query": q}}]},
                                  {"content": "Svar."}, {"content": "Titel"}]

    search("vejret i Vojens")
    rid = await say(signed_in, c["id"], "Vejret?")
    q = await answer(signed_in, app, rid, "allow")
    assert q["tool"] == "web_search" and q["ask"] == {
        "kind": "internet", "key": "internet:search", "what": "search", "detail": "vejret i Vojens",
        "always": "Always allow web search", "can_always": True, "can_chat": True}
    run = await finish(app, rid)
    assert run.status == "done" and result_of(run, "web_search")["ok"]
    tools = {t["function"]["name"]: t["function"] for t in fakes.llama["requests"][0]["tools"]}
    assert "ask before it goes online" in tools["web_search"]["description"]
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "ask before you go online: web_search and web_fetch wait" in system and "has internet access" not in system
    # deny: the model hears it was declined
    search("SLQ Hub")
    rid = await say(signed_in, c["id"], "Søg igen")
    await answer(signed_in, app, rid, "deny")
    run = await finish(app, rid)
    res = result_of(run, "web_search")
    assert not res["ok"] and "The user declined this tool call" in res["text"]
    # always: kept, and the next search goes ahead without asking
    search("ishockey")
    rid = await say(signed_in, c["id"], "Og igen")
    await answer(signed_in, app, rid, "always")
    await finish(app, rid)
    assert "internet:search" in permissions()
    search("floorball")
    run = await finish(app, await say(signed_in, c["id"], "En sidste"))
    assert not approvals(run) and result_of(run, "web_search")["ok"]


async def test_a_page_asks_per_site(signed_in, app, fakes):
    asking(app)
    c = await new_chat(signed_in)

    def fetch(url: str):
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = [{"tool_calls": [{"name": "web_fetch", "arguments": {"url": url}}]},
                                  {"content": "Læst."}, {"content": "Side"}]

    fetch("https://www.dr.dk/nyheder")
    rid = await say(signed_in, c["id"], "Læs DR")
    q = await answer(signed_in, app, rid, "always")
    assert (q["ask"]["what"], q["ask"]["host"], q["ask"]["always"]) == ("site", "www.dr.dk", "Always allow www.dr.dk")
    await finish(app, rid)
    assert "internet:site:www.dr.dk" in permissions()
    fetch("https://WWW.DR.DK/sporten")                                  # the same site: no question
    run = await finish(app, await say(signed_in, c["id"], "Sporten"))
    assert not approvals(run)
    fetch("https://example.org/b")                                       # another site asks
    rid = await say(signed_in, c["id"], "En anden")
    q = await answer(signed_in, app, rid, "allow")
    assert q["ask"]["host"] == "example.org"
    await finish(app, rid)
    assert "internet:site:example.org" not in permissions()                # once is once


def _site(routes: dict[str, tuple[int, str]]):
    """A fake web site: path -> (status, Location or body)."""
    import httpx
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse, Response
    site = FastAPI()

    @site.get("/{path:path}")
    async def page(path: str):
        status, value = routes["/" + path]
        if status in (301, 302):
            return Response(status_code=status, headers={"Location": value})
        return PlainTextResponse(value)
    return httpx.ASGITransport(app=site)


async def test_a_redirect_to_another_site_asks_again(signed_in, app, fakes):
    """Yes to one site is not yes to wherever it redirects: within the site (www. or not) the fetch follows; to
    another it stops and the model fetches that address itself - which asks. A site always allowed is followed."""
    asking(app)
    router = app.state.router.transports
    router["dr.dk"] = _site({"/nyheder": (301, "https://www.dr.dk/nyheder")})
    router["www.dr.dk"] = _site({"/nyheder": (200, "Dagens nyheder")})
    router["short.link"] = _site({"/x": (302, "https://example.net/landing")})
    router["example.net"] = _site({"/landing": (200, "Landed")})
    c = await new_chat(signed_in)

    def fetch(url: str):
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = [{"tool_calls": [{"name": "web_fetch", "arguments": {"url": url}}]},
                                  {"content": "Ok."}, {"content": "Side"}]
    fetch("https://dr.dk/nyheder")
    rid = await say(signed_in, c["id"], "Læs DR")
    await answer(signed_in, app, rid, "allow")
    res = result_of(await finish(app, rid), "web_fetch")
    assert res["ok"] and "Dagens nyheder" in res["text"]                         # dr.dk -> www.dr.dk: one site
    fetch("https://short.link/x")
    rid = await say(signed_in, c["id"], "Følg linket")
    await answer(signed_in, app, rid, "always")
    res = result_of(await finish(app, rid), "web_fetch")
    assert "redirects to https://example.net/landing, on another site" in res["text"] and "Landed" not in res["text"]
    with db.connect() as con:                                                    # the owner always allows it
        con.execute("INSERT INTO tool_permissions (tool, decision, created_at) VALUES "
                    "('internet:site:example.net', 'always', '2026-10-24T10:00:00')")
    fetch("https://short.link/x")
    run = await finish(app, await say(signed_in, c["id"], "Igen"))
    assert not approvals(run) and "Landed" in result_of(run, "web_fetch")["text"]
    asking(app, False)                                                           # allowed: every redirect followed
    with db.connect() as con:
        con.execute("DELETE FROM tool_permissions")
    fetch("https://short.link/x")
    run = await finish(app, await say(signed_in, c["id"], "Uden at spørge"))
    assert "Landed" in result_of(run, "web_fetch")["text"]


async def test_code_runs_offline_unless_it_asks(signed_in, app, fakes):
    asking(app)
    c = await new_chat(signed_in)

    def bash(**args):
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = [{"tool_calls": [{"name": "bash", "arguments": args}]}, {"content": "Ok."},
                                  {"content": "Kode"}]

    fakes.sandbox["handler"] = lambda body: {"exit_code": 1, "stdout": "", "stderr": "Network is unreachable",
                                             "timed_out": False, "duration_ms": 5, "truncated": False}
    bash(command="pip install rich")
    run = await finish(app, await say(signed_in, c["id"], "Installer rich"))
    assert not approvals(run) and fakes.sandbox["execs"][-1]["offline"] is True
    res = result_of(run, "bash")
    assert "ran without the network" in res["text"] and "internet: true" in res["text"]
    tools = {t["function"]["name"]: t["function"] for t in fakes.llama["requests"][0]["tools"]}
    assert "internet" in tools["bash"]["parameters"]["properties"] and "unless the call says internet: true" in \
        tools["bash"]["description"]
    fakes.sandbox.pop("handler")
    # internet: true asks; allowed, it runs online
    bash(command="pip install rich", internet=True)
    rid = await say(signed_in, c["id"], "Med internet")
    q = await answer(signed_in, app, rid, "allow")
    assert (q["ask"]["what"], q["ask"]["detail"], q["ask"]["always"]) == \
        ("code", "pip install rich", "Always allow internet for code")
    run = await finish(app, rid)
    assert "offline" not in fakes.sandbox["execs"][-1]
    assert fakes.sandbox["execs"][-1]["end_leftovers"] is True                 # once: nothing stays online
    assert [e["data"]["decision"] for e in run.events if e["type"] == "approval_answered"] == ["allow"]
    # always: code goes online from then on, flag or not
    bash(command="git clone https://example.org/x.git", internet=True)
    rid = await say(signed_in, c["id"], "Klon")
    await answer(signed_in, app, rid, "always")
    await finish(app, rid)
    bash(command="curl -s https://example.org")
    run = await finish(app, await say(signed_in, c["id"], "Hent"))
    assert not approvals(run) and "offline" not in fakes.sandbox["execs"][-1]
    assert "end_leftovers" not in fakes.sandbox["execs"][-1]                   # always: as before
    # and back to asking when the permission is taken back in Settings
    listed = (await signed_in.get("/hub/api/tool-permissions")).json()
    assert {p["tool"] for p in listed} >= {"internet:code"}
    assert (await signed_in.delete("/hub/api/tool-permissions/internet:code")).status_code == 200
    bash(command="curl -s https://example.org")
    run = await finish(app, await say(signed_in, c["id"], "Igen"))
    assert fakes.sandbox["execs"][-1]["offline"] is True


async def test_no_means_no_not_a_hand_over(signed_in, app, fakes):
    """Three declined calls are not three failed calls: the turn is not handed to a stronger model (or the
    cloud) that would go online in its place."""
    asking(app)
    app.state.hub.settings.update("models", {"default": "qwen3.6-35b-a3b"})
    fakes.llama["replies"] = [{"tool_calls": [{"name": "web_search", "arguments": {"query": f"vejret {i}"}}]}
                              for i in range(3)] + [{"content": "Så svarer jeg uden."}, {"content": "Vejret"}]
    c = await new_chat(signed_in)
    rid = await say(signed_in, c["id"], "Vejret?")
    for nth in (1, 2, 3):
        await answer(signed_in, app, rid, "deny", nth)
    run = await finish(app, rid)
    assert run.status == "done" and not [e for e in run.events if e["type"] == "escalated"]
    assert {b["model"] for b in fakes.llama["requests"]} == {"qwen3.6-35b-a3b"}


def test_what_counts_as_asking_for_the_internet():
    """bash's flag as models write it: true, "true", 1 - anything else runs offline."""
    def never(key):
        return False
    for flag in (True, "true", "True", 1, "yes"):
        ask, online = internet.question("bash", {"command": "pip install rich", "internet": flag}, never, True)
        assert ask is not None and ask["what"] == "code" and online, flag
    for flag in (False, "false", None, 0, ""):
        assert internet.question("bash", {"command": "ls", "internet": flag}, never, True) == (None, False), flag


async def test_a_file_name_is_never_code(signed_in, app, fakes):
    """The Hub's own commands in the sandbox (here OCR) quote file names for the shell: a name with $(...) in it
    stays a name, so no file can take code online around the question."""
    asking(app)
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    folder.mkdir(parents=True, exist_ok=True)
    name = "it's $(curl -s example.org).png"
    (folder / name).write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(32))
    fakes.sandbox["handler"] = lambda body: {"exit_code": 0, "stdout": "Pumpe 3", "stderr": "", "timed_out": False,
                                             "duration_ms": 5, "truncated": False}
    fakes.llama["replies"] = [{"tool_calls": [{"name": "image_ocr", "arguments": {"path": name}}]},
                              {"content": "Der står Pumpe 3."}, {"content": "Skilt"}]
    run = await finish(app, await say(signed_in, c["id"], "Hvad står der?"))
    assert result_of(run, "image_ocr")["ok"]
    cmd = fakes.sandbox["execs"][-1]["cmd"]
    assert cmd.startswith("tesseract " + shlex.quote(f"/workspace/chats/{c['id']}/{name}") + " - -l "), cmd


async def test_without_offline_every_command_asks(signed_in, app, fakes):
    """Where the sandbox cannot keep code off the internet - no Landlock ABI 4, or its daemon could not run a
    command offline (the seccomp filter) - asking first means asking before each command."""
    asking(app)
    c = await new_chat(signed_in)
    for health in ({"landlock": 3}, {"landlock": 8, "offline": False}):
        fakes.sandbox.update(health)
        app.state.hub.extras.pop("offline_code", None)
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = [{"tool_calls": [{"name": "bash", "arguments": {"command": "ls"}}]},
                                  {"content": "Ok."}, {"content": "Ls"}]
        rid = await say(signed_in, c["id"], "List")
        q = await answer(signed_in, app, rid, "allow")
        assert q["ask"]["what"] == "code" and q["ask"]["unblockable"] is True, health
        await finish(app, rid)
        assert "offline" not in fakes.sandbox["execs"][-1]


async def test_a_person_answers_once(signed_in, app, fakes):
    """In a person's chat the question goes to them, without "always" - their chats leave nothing on the Hub, and
    the owner's answers are the owner's."""
    asking(app)
    with db.connect() as con:                                            # the owner always allows search...
        con.execute("INSERT INTO tool_permissions (tool, decision, created_at) VALUES ('internet:search', 'always', "
                    "'2026-10-24T10:00:00')")
    await add_person(signed_in)
    fakes.llama["replies"] = [{"tool_calls": [{"name": "web_search", "arguments": {"query": "opskrift"}}]},
                              {"content": "Her."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        r = await phone.post("/hub/api/device/turns", json={"title": "Mad", "messages": [user_msg("Find en opskrift")]},
                             headers={"Origin": "http://hub"})
        tid = r.json()["turn_id"]
        t = app.state.hub.extras["device"].turns[tid]
        await until(lambda: approvals(t.run))                            # ...but she is asked
        q = approvals(t.run)[0]
        assert q["ask"]["can_always"] is False
        bad = await phone.post(f"/hub/api/device/turns/{tid}/approve", json={"call_id": q["id"], "decision": "always"})
        assert bad.status_code == 400
        ok = await phone.post(f"/hub/api/device/turns/{tid}/approve", json={"call_id": q["id"], "decision": "allow"})
        assert ok.status_code == 200
        await until(lambda: t.result is not None, 15)
        msgs = json.dumps(t.result["messages"])
        assert "Result for opskrift" in msgs
        system = fakes.llama["requests"][0]["messages"][0]["content"]
        assert "ask before you go online: web_search and web_fetch wait for their answer." in system
        assert "bash runs without" not in system                         # she has no code


async def test_the_owner_sets_it_in_settings(signed_in, app, client):
    assert (await signed_in.get("/hub/api/auth/status")).json()["internetAsks"] is False
    r = await signed_in.put("/hub/api/settings", json={"section": "web", "values": {"access": "ask"}})
    assert r.status_code == 200 and r.json()["settings"]["web"]["access"] == "ask"
    assert (await signed_in.get("/hub/api/settings")).json()["settings"]["web"]["access"] == "ask"
    # the UI hears it (pictures from the web in answers become links), signed in only
    assert (await signed_in.get("/hub/api/auth/status")).json()["internetAsks"] is True
    async with client_from(app) as stranger:
        assert (await stranger.get("/hub/api/auth/status")).json()["internetAsks"] is False


def test_the_model_containers_have_no_way_out():
    """llm, comfyui and speech sit on their own network with fixed addresses, which the VM's firewall keeps from
    opening connections to anything outside it; the Hub reaches them there, and the internet through core."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    compose = (root / "deploy" / "docker-compose.yml").read_text()

    def service(name: str) -> str:
        return compose.split(f"\n  {name}:\n", 1)[1].split("\n\n  ", 1)[0]
    ips = {}
    for name in ("llm", "comfyui", "speech"):
        block = service(name)
        assert "\n    networks:\n      inner:\n        ipv4_address: 172.31.251." in block, name
        assert "core" not in block.split("\n    networks:", 1)[1], name
        ips[name] = block.split("ipv4_address: ", 1)[1].split()[0]
    hub = service("hub")
    assert all(n in hub for n in ("core: {}", "inner:\n        ipv4_address: 172.31.251.10", "sbx:"))
    assert "searxng" in compose and "networks: [core]" in service("searxng")
    assert "inner:\n    driver: bridge" in compose and "subnet: 172.31.251.0/24" in compose
    fw = (root / "deploy" / "firewall" / "slq-hub-firewall.sh").read_text()
    assert f'MODEL_IPS="${{MODEL_IPS:-{ips["llm"]} {ips["comfyui"]} {ips["speech"]}}}"' in fw
    assert 'INNER_NET="${INNER_NET:-172.31.251.0/24}"' in fw
    assert 'add DOCKER-USER -s "$ip" ! -d "$INNER_NET" -m conntrack --ctstate NEW -j DROP' in fw


async def test_allow_in_this_chat(signed_in, app, fakes):
    """"Allow in this chat": no question for the rest of the chat (and its subagents), the next chat asks again; the
    chat lists it and can take it back; it goes with the chat. A person is not offered it."""
    asking(app)
    c = await new_chat(signed_in)

    def search(q: str):
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = [{"tool_calls": [{"name": "web_search", "arguments": {"query": q}}]},
                                  {"content": "Svar."}, {"content": "Titel"}]

    search("vejret i Vojens")
    rid = await say(signed_in, c["id"], "Vejret?")
    q = await answer(signed_in, app, rid, "chat")
    assert q["ask"]["can_chat"] is True and q["ask"]["can_always"] is True
    run = await finish(app, rid)
    assert result_of(run, "web_search")["ok"]
    assert [e["data"]["decision"] for e in run.events if e["type"] == "approval_answered"] == ["chat"]
    assert not permissions()                                             # not "always"
    data = (await signed_in.get(f"/hub/api/chats/{c['id']}")).json()
    assert [p["tool"] for p in data["allowed"]] == ["internet:search"]
    search("vejret i morgen")                                            # the same chat: no question
    run = await finish(app, await say(signed_in, c["id"], "Og i morgen?"))
    assert not approvals(run) and result_of(run, "web_search")["ok"]
    fakes.llama["requests"].clear()                                      # its subagent neither
    fakes.llama["replies"] = [{"tool_calls": [{"name": "task", "arguments": {"description": "Find it",
                                                                               "prompt": "Search the weather."}}]},
                              {"tool_calls": [{"name": "web_search", "arguments": {"query": "vejr"}}]},
                              {"content": "Sol."}, {"content": "Det bliver sol."}]
    run = await finish(app, await say(signed_in, c["id"], "Spørg en hjælper"))
    assert not approvals(run) and run.status == "done"
    other = await new_chat(signed_in)                                    # another chat asks again
    search("vejret i Aabenraa")
    rid = await say(signed_in, other["id"], "Vejret?")
    await answer(signed_in, app, rid, "deny")
    await finish(app, rid)
    # taken back: it asks again
    r = await signed_in.delete(f"/hub/api/chats/{c['id']}/allowed", params={"tool": "internet:search"},
                               headers={"Origin": "http://hub"})
    assert r.status_code == 200 and r.json() == {"allowed": []}
    search("vejret igen")
    rid = await say(signed_in, c["id"], "Igen?")
    await answer(signed_in, app, rid, "chat")
    await finish(app, rid)
    # deleting the chat takes its answers with it
    assert (await signed_in.delete(f"/hub/api/chats/{c['id']}", headers={"Origin": "http://hub"})).status_code == 200
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM chat_permissions").fetchone()[0] == 0
    bad = await signed_in.post("/hub/api/runs/nope/approve", json={"call_id": "x", "decision": "forever"})
    assert bad.status_code == 400


async def test_a_person_is_not_offered_this_chat(signed_in, app, fakes):
    asking(app)
    await add_person(signed_in)
    fakes.llama["replies"] = [{"tool_calls": [{"name": "web_search", "arguments": {"query": "opskrift"}}]},
                              {"content": "Her."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        r = await phone.post("/hub/api/device/turns", json={"title": "Mad", "messages": [user_msg("Find en opskrift")]},
                             headers={"Origin": "http://hub"})
        tid = r.json()["turn_id"]
        t = app.state.hub.extras["device"].turns[tid]
        await until(lambda: approvals(t.run))
        q = approvals(t.run)[0]
        assert q["ask"]["can_chat"] is False and q["ask"]["can_always"] is False
        bad = await phone.post(f"/hub/api/device/turns/{tid}/approve", json={"call_id": q["id"], "decision": "chat"})
        assert bad.status_code == 400
        ok = await phone.post(f"/hub/api/device/turns/{tid}/approve", json={"call_id": q["id"], "decision": "deny"})
        assert ok.status_code == 200
        await until(lambda: t.result is not None, 15)


async def test_the_card_is_free_while_a_question_waits(signed_in, app, fakes):
    """While the turn waits for the user, its lease is given up: other jobs run on the card (here background jobs
    for the same model take every slot); after the answer the turn queues, says so, and goes on."""
    asking(app)
    sched = app.state.hub.scheduler
    fakes.llama["replies"] = [{"tool_calls": [{"name": "web_search", "arguments": {"query": "vejret"}}]},
                              {"content": "Sol."}, {"content": "Vejret"}]
    c = await new_chat(signed_in)
    rid = await say(signed_in, c["id"], "Vejret?")
    run = app.state.hub.extras["agent"].runs.get(rid)
    await until(lambda: approvals(run))
    assert not sched.running                                          # nothing holds the card
    need = app.state.hub.model_need(next(e for e in run.events if e["type"] == "started")["data"]["model"])
    others = [await sched.acquire("famlog", "chat", need, "background") for _ in range(sched.backend.slots(need))]
    q = approvals(run)[0]
    r = await signed_in.post(f"/hub/api/runs/{rid}/approve", json={"call_id": q["id"], "decision": "allow"})
    assert r.status_code == 200

    def after_answer(kind: str) -> list:
        types = [e["type"] for e in run.events]
        if "approval_answered" not in types:
            return []
        return [e for e in run.events[types.index("approval_answered"):] if e["type"] == kind]
    await until(lambda: after_answer("queued"))
    assert isinstance(after_answer("queued")[0]["data"]["position"], int) and not run.finished
    assert not [e for e in run.events if e["type"] == "tool_result"]
    for t in others:
        await t.release()
    run = await finish(app, rid)
    assert run.status == "done" and result_of(run, "web_search")["ok"]
    assert len(after_answer("started")) == 1                            # and it went on


async def test_stop_while_a_question_waits_does_not_take_the_card_back(signed_in, app, fakes):
    """Stop while the turn waits for the user must not make it queue for the card again: the turn is ending, so the
    card it gave up stays free. Other jobs hold every slot here, so a wrong re-take would wait behind them."""
    asking(app)
    sched = app.state.hub.scheduler
    fakes.llama["replies"] = [{"tool_calls": [{"name": "web_search", "arguments": {"query": "vejret"}}]},
                              {"content": "unreached"}]
    c = await new_chat(signed_in)
    rid = await say(signed_in, c["id"], "Vejret?")
    run = app.state.hub.extras["agent"].runs.get(rid)
    await until(lambda: approvals(run))
    assert not sched.running                                            # the card is free while it asks
    need = app.state.hub.model_need(next(e for e in run.events if e["type"] == "started")["data"]["model"])
    others = [await sched.acquire("famlog", "chat", need, "background") for _ in range(sched.backend.slots(need))]
    await signed_in.post(f"/hub/api/runs/{rid}/stop")
    await until(lambda: run.finished)                                   # (not finish(): the card is held below)
    assert run.status == "cancelled"
    assert not [e for e in run.events if e["type"] == "approval_answered"]       # it was never answered
    assert set(sched.running) == {t.job.id for t in others} and not sched.pending   # the turn did not re-take
    for t in others:
        await t.release()
    await sched.drain()
