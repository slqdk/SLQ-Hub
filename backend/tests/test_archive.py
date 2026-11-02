"""Archived chats (0.69.0, Søren: "i need an archive chat function... So it remains somewhere hidden, and i can pull
it up if i want.. so they dont float around in the chats list"): out of the lists, kept as they are, found by the
search and on the Archived page, back in the lists when unarchived or worked in again; Continue in a new chat
archives the chat it continues, and the chains already in the list can be archived in one go."""
from __future__ import annotations

import json

from app import db
from app.chats import store as chats

from .test_agent import finish, new_chat, say


async def _ids(client, **query) -> list[int]:
    r = await client.get("/hub/api/chats", params=query)
    assert r.status_code == 200, r.text
    return [c["id"] for c in r.json()]


async def test_an_archived_chat_leaves_the_lists_and_comes_back(signed_in, app, fakes):
    p = (await signed_in.post("/hub/api/projects", json={"name": "Garage"})).json()
    loose, kept, in_project = (await new_chat(signed_in)), (await new_chat(signed_in)), \
        (await new_chat(signed_in, project_id=p["id"]))
    chats.update_chat(loose["id"], title="Old stadium countdown")
    chats.update_chat(in_project["id"], title="Old shelf plan")
    before = chats.get_chat(loose["id"])["updated_at"]
    for c in (loose, in_project):
        r = await signed_in.patch(f"/hub/api/chats/{c['id']}", json={"archived": True})
        assert r.status_code == 200 and r.json()["archived"] == 1
    assert chats.get_chat(loose["id"])["updated_at"] == before                 # its place in the list is kept
    sidebar = await _ids(signed_in, all=True)
    assert kept["id"] in sidebar and loose["id"] not in sidebar and in_project["id"] not in sidebar
    assert loose["id"] not in await _ids(signed_in)                             # the chats without a project
    assert in_project["id"] not in await _ids(signed_in, project_id=p["id"])    # the project's page
    assert set(await _ids(signed_in, all=True, archived=True)) == {loose["id"], in_project["id"]}
    found = (await signed_in.get("/hub/api/chats", params={"all": True, "q": "Old", "with_archived": True})).json()
    assert {(c["id"], c["archived"]) for c in found} == {(loose["id"], 1), (in_project["id"], 1)}
    assert not (await signed_in.get("/hub/api/chats", params={"all": True, "q": "Old"})).json()
    opened = (await signed_in.get(f"/hub/api/chats/{loose['id']}")).json()      # it opens as it was
    assert opened["chat"]["archived"] == 1 and opened["chat"]["title"] == "Old stadium countdown"

    r = await signed_in.patch(f"/hub/api/chats/{loose['id']}", json={"archived": False, "title": "Stadium countdown"})
    assert r.json()["archived"] == 0 and r.json()["title"] == "Stadium countdown"
    assert loose["id"] in await _ids(signed_in, all=True)
    assert await _ids(signed_in, all=True, archived=True) == [in_project["id"]]


async def test_a_message_brings_an_archived_chat_back(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "Hello again."}, {"content": "Hello"}]
    c = await new_chat(signed_in)
    await signed_in.patch(f"/hub/api/chats/{c['id']}", json={"archived": True})
    run = await finish(app, await say(signed_in, c["id"], "Hi"))
    assert run.status == "done" and chats.get_chat(c["id"])["archived"] == 0
    assert c["id"] in await _ids(signed_in, all=True)


async def test_continue_in_a_new_chat_archives_the_old_one(signed_in, app, fakes):
    c = await new_chat(signed_in)
    chats.update_chat(c["id"], title="Ret PL-liste fra NOTES")
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Fix the list"}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "Fixed."}])

    def responder(body):
        last = body["messages"][-1]["content"]
        last = last if isinstance(last, str) else str(last)
        return {"content": "Title" if "short title" in last else "## Goal\n- the list"}
    fakes.llama["responder"] = responder
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/handover")
    assert r.status_code == 200, r.text
    new = r.json()["chat"]
    assert r.json()["archived"] == c["id"] and chats.get_chat(c["id"])["archived"] == 1
    await finish(app, r.json()["run_id"])
    sidebar = await _ids(signed_in, all=True)
    assert new["id"] in sidebar and c["id"] not in sidebar
    assert (await signed_in.get(f"/hub/api/chats/{c['id']}")).status_code == 200    # a click away, as it was


async def test_the_chains_already_in_the_list_are_archived_in_one_go(signed_in, app, fakes):
    """Chats a newer chat continues - not the newest of a chain, not one without a continuation, not one that is
    working."""
    first, second, third, alone = [await new_chat(signed_in) for _ in range(4)]
    for newer, older in ((second, first), (third, second)):    # as the handover route writes it (no API takes it)
        s = {**chats.get_chat(newer["id"])["settings"], "continued_from": older["id"]}
        with db.connect() as con:
            con.execute("UPDATE chats SET settings=? WHERE id=?", (json.dumps(s), newer["id"]))
    assert sorted(chats.continued_chats()) == sorted([first["id"], second["id"]])
    assert (await signed_in.get("/hub/api/chats/continued")).json() == {"count": 2}
    fakes.llama["replies"] = [{"content": "slow", "delay": 0.6}, {"content": "Title"}]
    run_id = await say(signed_in, second["id"], "Still here?")                  # working: left in the list
    r = await signed_in.post("/hub/api/chats/continued/archive")
    assert r.status_code == 200 and r.json() == {"archived": 1}
    await finish(app, run_id)
    assert chats.get_chat(first["id"])["archived"] == 1
    assert chats.get_chat(second["id"])["archived"] == 0
    assert chats.get_chat(third["id"])["archived"] == 0 and chats.get_chat(alone["id"])["archived"] == 0
    assert (await signed_in.post("/hub/api/chats/continued/archive")).json() == {"archived": 1}
    assert (await signed_in.get("/hub/api/chats/continued")).json() == {"count": 0}
    assert set(await _ids(signed_in, all=True)) == {third["id"], alone["id"]}
