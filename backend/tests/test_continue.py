"""0.22.0 - Continue under an unfinished turn. Chat 21's turn stopped at the step limit with the game half fixed and
its file in the card as if it were done. Now a turn that stops at the limit (or is stopped) says so in its steps,
and Continue adds a message that tells the model why it stopped, then starts a turn - in the owner's chats and in a
person's."""
from __future__ import annotations

import asyncio

from app.chats import store as chats
from app.chats.export import markdown

from .test_agent import finish, new_chat, say
from .test_people import add_person, client_from, sign_in, turn, user_msg


def _calls_forever(fakes):
    """A model that writes one more file each step: never done, never the same step twice."""
    def reply(body: dict) -> dict:
        n = len(fakes.llama["requests"])
        return {"tool_calls": [{"name": "write_file", "arguments": {"path": f"part{n}.txt", "content": "x"}}]}
    return reply


def _told(fakes, words: str) -> str:
    """The user text of the last model request that has `words` (the chat's title request comes after a turn)."""
    for req in reversed(fakes.llama["requests"]):
        for t in reversed(_user_texts(req)):
            if words in t:
                return t
    return ""


def _user_texts(request: dict) -> list[str]:
    out = []
    for m in request["messages"]:
        if m["role"] == "user":
            c = m["content"]
            out.append(c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if p.get("type") == "text"))
    return out


async def test_a_turn_at_the_step_limit_is_continued(signed_in, app, fakes):
    await signed_in.put("/hub/api/settings", json={"section": "agent", "values": {"max_steps": 4}})
    fakes.llama["responder"] = _calls_forever(fakes)
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Make the whole game"))
    assert run.status == "done"
    assert any(e["type"] == "step_limit" and e["data"] == {"steps": 4} for e in run.events)
    stored = chats.runs_for_chat(c["id"])[-1]
    assert {"type": "limit", "steps": 4, "depth": 0} in stored["steps"]
    note = chats.messages(c["id"])[-1]["content"][-1]["text"]
    assert note.startswith("(Stopped after 4 steps, the most one turn may take. Press Continue")

    fakes.llama["responder"] = lambda body: {"content": "Done now."}
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/continue", json={})
    assert r.status_code == 200, r.text
    assert r.json()["message"]["content"] == [{"type": "continue", "reason": "limit", "steps": 4}]
    run2 = await finish(app, r.json()["run_id"])
    assert run2.status == "done"
    told = _told(fakes, "pressed Continue")
    assert "the user pressed Continue. Your last turn ended at the step limit (4 steps" in told
    assert "do not start over" in told
    # finished now: nothing to continue; the transcript says what happened
    again = await signed_in.post(f"/hub/api/chats/{c['id']}/continue", json={})
    assert again.status_code == 409 and "finished" in again.json()["detail"]
    text = markdown(chats.get_chat(c["id"]), "test")
    assert "*Not finished: the turn stopped at the step limit (4 steps).*" in text
    assert "*(pressed Continue: the last turn was not done - the step limit, 4 steps)*" in text


async def test_a_stopped_turn_is_continued_but_not_one_that_works(signed_in, app, fakes):
    fakes.llama["responder"] = lambda body: {"content": "slow", "delay": 5}
    c = await new_chat(signed_in)
    rid = await say(signed_in, c["id"], "Write a long story")
    run = app.state.hub.extras["agent"].runs.get(rid)
    for _ in range(200):
        if run.status == "running":
            break
        await asyncio.sleep(0.01)
    busy = await signed_in.post(f"/hub/api/chats/{c['id']}/continue", json={})
    assert busy.status_code == 409                                  # it is still working
    assert (await signed_in.post(f"/hub/api/runs/{rid}/stop")).status_code == 200
    await finish(app, rid)
    assert run.status == "cancelled"
    fakes.llama["responder"] = lambda body: {"content": "Here is the rest."}
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/continue", json={})
    assert r.status_code == 200, r.text
    assert r.json()["message"]["content"] == [{"type": "continue", "reason": "stopped"}]
    await finish(app, r.json()["run_id"])
    assert "They stopped your last turn before it was done." in _told(fakes, "pressed Continue")
    assert (await signed_in.post("/hub/api/chats/99999/continue", json={})).status_code == 404


async def test_a_person_continues_on_her_device(signed_in, app, fakes):
    await add_person(signed_in)
    fakes.llama["replies"] = [{"content": "Nu fortsætter jeg."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Spil", "messages": [
            user_msg("Lav spillet"),
            {"role": "assistant", "content": [{"type": "text", "text": "(Stopped after 60 steps, ...)"}]},
            {"role": "user", "content": [{"type": "continue", "reason": "limit", "steps": 60, "extra": "x"}]}]})
        assert res["status"] == "done", res
        assert "step limit (60 steps" in _told(fakes, "pressed Continue")
        # what the device sends is only why: anything else in the block is dropped, odd values made safe
        res = await turn(app, phone, {"title": "Spil", "messages": [
            user_msg("Lav spillet"), {"role": "user", "content": [{"type": "continue", "reason": "x", "steps": "lots"}]}]})
        assert res["status"] == "done"
        assert "They stopped your last turn" in _told(fakes, "pressed Continue")
