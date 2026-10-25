"""Long chats (0.27.0): the whole conversation searchable (chat_history), notes that replace each other and can be
corrected, and continuing in a new chat."""
from __future__ import annotations

import asyncio
import json

from app.chats import store as chats

from .test_agent import finish, last_user, new_chat, say


def tool_names(body: dict) -> set[str]:
    return {t["function"]["name"] for t in body.get("tools") or []}


def results(chat_id: int) -> list[dict]:
    return [b for m in chats.messages(chat_id) if m["role"] == "tool" for b in m["content"]]


def history_calls(*calls: dict, answer: str = "Found it."):
    """A responder that makes the chat_history calls given, one per step, then answers."""
    queue = list(calls)

    def responder(body):
        if "short title" in last_user(body):
            return {"content": "Title"}
        if "Condense the conversation" in last_user(body):
            return {"content": "## Goal\n- a game"}
        if queue:
            return {"tool_calls": [{"name": "chat_history", "arguments": queue.pop(0)}]}
        return {"content": answer}
    return responder


async def test_a_condensed_chat_can_look_up_what_its_notes_leave_out(signed_in, app, fakes):
    c = await new_chat(signed_in)
    chats.add_message(c["id"], "user", [{"type": "text", "text": "The jump height must be 140 pixels, not 120."}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "Noted: jump height 140 px."}])
    for i in range(6):
        chats.add_message(c["id"], "user", [{"type": "text", "text": f"Level {i} gets more enemies."}])
        chats.add_message(c["id"], "assistant", [{"type": "text", "text": f"Level {i} done."}])
    last = chats.messages(c["id"])[-1]["id"]
    chats.update_chat(c["id"], summary="## Goal\n- a platform game", summary_upto=last)
    fakes.llama["responder"] = history_calls({"action": "search", "query": "jump height"})
    run = await finish(app, await say(signed_in, c["id"], "What did we decide about jumping?"))
    assert run.status == "done", run.events[-1]
    first = fakes.llama["requests"][0]
    assert "chat_history" in tool_names(first)
    assert "chat_history looks up what they leave out" in first["messages"][0]["content"]
    found = results(c["id"])[0]["text"]
    assert found.startswith('2 best matches for "jump height"') and "140 pixels" in found
    first_id = chats.messages(c["id"])[0]["id"]
    assert f"#{first_id} (the user, " in found and 'Read one whole with action "read"' in found
    # read it whole
    fakes.llama["responder"] = history_calls({"action": "read", "id": first_id})
    await finish(app, await say(signed_in, c["id"], "Quote it"))
    assert results(c["id"])[-1]["text"].endswith("The jump height must be 140 pixels, not 120.")
    # nothing matching, a bad id, no query
    fakes.llama["responder"] = history_calls({"action": "search", "query": "submarine"},
                                             {"action": "read", "id": 999999}, {"action": "search"})
    await finish(app, await say(signed_in, c["id"], "Anything about submarines?"))
    texts = [r["text"] for r in results(c["id"])[-3:]]
    assert texts[0].startswith('Nothing in the ') and "No message #999999" in texts[1] and "needs a query" in texts[2]


async def test_the_tool_is_offered_only_when_there_is_more_than_the_model_sees(signed_in, app, fakes):
    c = await new_chat(signed_in)
    fakes.llama["replies"] = [{"content": "Hi."}, {"content": "Hello"}]
    await finish(app, await say(signed_in, c["id"], "Hi"))
    assert "chat_history" not in tool_names(fakes.llama["requests"][0])
    for i in range(10):                                         # a long chat: older turns' output is shortened
        chats.add_message(c["id"], "user", [{"type": "text", "text": f"q{i}"}])
        chats.add_message(c["id"], "assistant", [{"type": "text", "text": f"a{i}"}])
    fakes.llama["replies"] = [{"content": "Hi again."}]
    await finish(app, await say(signed_in, c["id"], "Hi"))
    assert "chat_history" in tool_names(fakes.llama["requests"][-1])


def test_search_ranks_the_message_that_says_it():
    from app.agent.tools.history import search, snippet
    rows = [("", {"id": 1, "role": "user", "content": [{"type": "text", "text": "Make the game faster and the "
                                                                                "jump higher please"}]}),
            ("", {"id": 2, "role": "assistant", "content": [{"type": "text", "text": "The jump height is now "
                                                                                     "140 px."}]}),
            ("", {"id": 3, "role": "tool", "content": [{"type": "tool_result", "name": "bash",
                                                        "text": "height=140\nwidth=320"}]}),
            ("", {"id": 4, "role": "user", "content": [{"type": "text", "text": "Hvad er højden på hoppet?"}]})]
    hits = search(rows, "jump height")
    assert hits[0][2]["id"] == 2 and {h[2]["id"] for h in hits} == {1, 2, 3}           # both words first
    assert search(rows, "højden")[0][2]["id"] == 4                     # Danish letters are part of words
    assert search(rows, "?!") == []
    long = "x " * 400 + "the secret word is plum " + "y " * 400
    s = snippet(long, "plum")
    assert "the secret word is plum" in s and s.startswith("...") and s.endswith("...") and len(s) < 400


async def test_a_persons_turn_searches_what_her_device_sent(signed_in, app, fakes, monkeypatch):
    """Her chat lives on her device: the search covers the conversation it sent with the message, the messages
    left out of the model's context included - and nothing of it is stored on the Hub."""
    from .test_people import add_person, client_from, sign_in, turn
    monkeypatch.setattr(app.state.hub.catalog, "context_per_request", lambda model_id: 16000)
    await add_person(signed_in)
    msgs = [{"role": "user", "content": [{"type": "text", "text": "My bike's frame number is WTU-1234."}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Saved in this chat."}]}]
    for i in range(20):
        msgs += [{"role": "user", "content": [{"type": "text", "text": f"Question {i} " + "z" * 3000}]},
                 {"role": "assistant", "content": [{"type": "text", "text": f"Answer {i}"}]}]
    msgs.append({"role": "user", "content": [{"type": "text", "text": "What was my frame number?"}]})
    fakes.llama["responder"] = history_calls({"action": "search", "query": "frame number"})
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "Bike", "settings": {"model": "qwen3.8-27b"}, "messages": msgs})
    assert res["status"] == "done", res
    first = fakes.llama["requests"][0]
    assert "chat_history" in tool_names(first) and "were left out to fit your context" in first["messages"][0]["content"]
    assert "WTU-1234" not in json.dumps(first["messages"])                    # left out of the model's context ...
    tool = next(b for m in res["messages"] if m["role"] == "tool" for b in m["content"])
    assert "WTU-1234" in tool["text"] and tool["text"].startswith('1 best matches for "frame number"')   # ... found


# ---------------------------------------------------------------- notes and Continue in a new chat

async def test_the_notes_replace_each_other_and_the_user_can_correct_them(signed_in, app, fakes, monkeypatch):
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 3000)
    c = await new_chat(signed_in)
    chats.update_chat(c["id"], summary="## Goal\n- OLD NOTES")
    for i in range(4):
        chats.add_message(c["id"], "user", [{"type": "text", "text": f"Question {i}: " + "x" * 2000}])
        chats.add_message(c["id"], "assistant", [{"type": "text", "text": f"Answer {i}: " + "y" * 2000}])
    asked = []

    def responder(body):
        if "Condense the conversation" in last_user(body):
            asked.append(body)
            return {"content": "## Goal\n- NEW NOTES"}
        return {"content": "Fine."}
    fakes.llama["responder"] = responder
    await finish(app, await say(signed_in, c["id"], "And now?"))
    prompt = last_user(asked[0])
    assert "They replace the earlier notes" in prompt and "## Decisions and facts" in prompt
    assert {"role": "user", "content": "The notes so far, from earlier in the conversation:\n## Goal\n- OLD NOTES"} \
        in asked[0]["messages"]
    assert chats.get_chat(c["id"])["summary"] == "## Goal\n- NEW NOTES"            # replaced, not appended
    r = await signed_in.put(f"/hub/api/chats/{c['id']}/notes", json={"text": "## Goal\n- corrected by Søren  "})
    assert r.status_code == 200 and r.json()["summary"] == "## Goal\n- corrected by Søren"
    assert (await signed_in.put(f"/hub/api/chats/{c['id']}/notes", json={"text": "x" * 20001})).status_code == 400
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 65536)    # room: no condensing now
    fakes.llama["responder"] = None
    fakes.llama["replies"] = [{"content": "Ok."}]
    await finish(app, await say(signed_in, c["id"], "Next"))
    assert "- corrected by Søren" in fakes.llama["requests"][-1]["messages"][0]["content"]


def notes_writer(notes: str = "## Goal\n- a platform game\n## Next\n- add the second level"):
    asked = []

    def responder(body):
        if "continues in a new chat" in last_user(body):
            asked.append(body)
            return {"content": notes}
        if "short title" in last_user(body):
            return {"content": "Title"}
        return {"content": "Working on it."}
    return responder, asked


async def test_continue_in_a_new_chat_carries_the_work_on(signed_in, app, fakes):
    hub = app.state.hub
    c = await new_chat(signed_in)
    chats.update_chat(c["id"], title="Platform game", settings={"model": "qwen3.8-27b", "thinking": True})
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Make the jump height 140 pixels."}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "Done: jump height 140."}])
    old = chats.get_chat(c["id"])
    wd = hub.extras["agent"].workdir(old)
    wd.write("game.html", "<h1>game</h1>", author="test", origin="test", message="game")
    for rel in (".hub/attachments/sprite.png", ".hub/skills/x/SKILL.md", ".hub/outputs/c1.txt"):
        p = wd.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    chats.update_chat(c["id"], summary="## Goal\n- a platform game")
    responder, asked = notes_writer()
    fakes.llama["responder"] = responder
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/handover")
    assert r.status_code == 200, r.text
    new, run_id = r.json()["chat"], r.json()["run_id"]
    assert new["title"] == "Platform game (2)" and new["project_id"] is None
    assert new["settings"]["model"] == "qwen3.8-27b" and new["settings"]["thinking"] is True
    assert new["settings"]["continued_from"] == c["id"]
    run = await finish(app, run_id)
    assert run.status == "done", run.events[-1]
    msgs = chats.messages(new["id"])
    assert msgs[0]["content"] == [{"type": "handover", "chat_id": c["id"], "title": "Platform game"}]
    assert msgs[1]["role"] == "assistant" and msgs[1]["content"][0]["text"].startswith("## Goal\n- a platform game")
    assert "jump height 140" in json.dumps(asked[0]["messages"])                    # written from the old chat
    assert "The notes so far, from earlier in the conversation" in json.dumps(asked[0]["messages"])
    assert "tools" not in asked[0] and asked[0].get("chat_template_kwargs", {}).get("enable_thinking") is not True
    new_wd = hub.extras["agent"].workdir(chats.get_chat(new["id"]))
    assert (new_wd.root / "game.html").read_text() == "<h1>game</h1>"
    assert (new_wd.root / ".hub/attachments/sprite.png").exists()
    assert not (new_wd.root / ".hub/skills").exists()                          # made again for each turn
    assert (new_wd.root / ".hub/outputs/c1.txt").exists()                      # old results point at them
    assert not [p for p in new_wd.root.parent.iterdir() if p.name.startswith(".handover-")]   # no copy left over
    assert chats.messages(c["id"])[-1]["content"][0]["text"] == "Done: jump height 140."    # the old chat as it was
    # the next turn: the handover, the notes, and the old chat searchable
    fakes.llama["responder"] = history_calls({"action": "search", "query": "jump height"})
    await finish(app, await say(signed_in, new["id"], "What was the jump height again?"))
    turn = [b for b in fakes.llama["requests"] if "chat_history" in tool_names(b)][0]
    sent = json.dumps(turn["messages"])
    assert 'this chat continues the chat \\"Platform game\\"' in sent and "add the second level" in sent
    found = results(new["id"])[-1]["text"]
    assert 'in the earlier chat "Platform game"' in found and "140 pixels" in found


async def test_continue_in_a_new_chat_in_a_project_and_what_is_refused(signed_in, app, fakes):
    p = (await signed_in.post("/hub/api/projects", json={"name": "Garage"})).json()
    c = await new_chat(signed_in, project_id=p["id"])
    assert (await signed_in.post(f"/hub/api/chats/{c['id']}/handover")).status_code == 409        # nothing yet
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Plan the shelves"}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "Three shelves."}])
    responder, _ = notes_writer("## Goal\n- shelves")
    fakes.llama["responder"] = responder
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/handover")
    assert r.status_code == 200, r.text
    new = r.json()["chat"]
    assert new["project_id"] == p["id"] and new["title"] == "New chat (2)"
    await finish(app, r.json()["run_id"])
    again = await signed_in.post(f"/hub/api/chats/{new['id']}/handover")
    assert again.status_code == 200 and again.json()["chat"]["title"] == "New chat (3)"
    await finish(app, again.json()["run_id"])
    # working: refused
    fakes.llama["responder"] = None
    fakes.llama["replies"] = [{"content": "slow", "delay": 0.5}]
    run_id = await say(signed_in, c["id"], "Go on")
    assert (await signed_in.post(f"/hub/api/chats/{c['id']}/handover")).status_code == 409
    await finish(app, run_id)


async def test_the_chat_says_how_full_its_context_was(signed_in, app, fakes, monkeypatch):
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 65536)
    c = await new_chat(signed_in)
    fakes.llama["replies"] = [{"content": "Hi.", "usage": {"prompt_tokens": 41000, "completion_tokens": 3,
                                                           "total_tokens": 41003}}, {"content": "Hi"}]
    await finish(app, await say(signed_in, c["id"], "Hi"))
    ctx = (await signed_in.get(f"/hub/api/chats/{c['id']}")).json()["context"]
    assert ctx == {"used": 41000, "limit": 65536, "condensed": False}


async def test_big_reads_go_to_a_subagent(signed_in, app, fakes):
    """The main agent is told what a subagent is for; the subagent itself is not told to use one."""
    def responder(body):
        system = body["messages"][0]["content"]
        if "You are a subagent" in system:
            return {"content": "Report: done."}
        if "short title" in last_user(body):
            return {"content": "Dumps"}
        if body["messages"][-1]["role"] == "tool":
            return {"content": "Done."}
        return {"tool_calls": [{"name": "task", "arguments": {"description": "Read the dumps",
                                                               "prompt": "Find where the two dumps differ."}}]}
    fakes.llama["responder"] = responder
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Compare the two hex dumps"))
    main = fakes.llama["requests"][0]["messages"][0]["content"]
    sub = next(b for b in fakes.llama["requests"] if "You are a subagent" in b["messages"][0]["content"])
    assert "give it to a subagent (task tool) with a precise question" in main
    assert "give it to a subagent" not in sub["messages"][0]["content"]


async def test_after_the_notes_a_message_is_answered_and_retry_writes_them_again(signed_in, app, fakes):
    """A message sent while the notes are written is answered after them - not with the notes again; the circular
    arrow under the notes writes them again."""
    c = await new_chat(signed_in)
    chats.update_chat(c["id"], title="Game")
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Make a game"}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "Made."}])
    calls = []

    def responder(body):
        if "continues in a new chat" in last_user(body):
            calls.append("notes")
            return {"content": "## Goal\n- the game", "delay": 0.3}
        calls.append("answer")
        return {"content": "Level 2 added."}
    fakes.llama["responder"] = responder
    r = (await signed_in.post(f"/hub/api/chats/{c['id']}/handover")).json()
    new_id = r["chat"]["id"]
    sent = await signed_in.post(f"/hub/api/chats/{new_id}/messages", json={"text": "Now add level 2"})
    assert sent.status_code == 200, sent.text
    await finish(app, r["run_id"])
    agent = app.state.hub.extras["agent"]
    for _ in range(200):
        if not agent.runs.active_for_chat(new_id):
            break
        await asyncio.sleep(0.02)
    texts = [b.get("text") for m in chats.messages(new_id) for b in m["content"] if b.get("type") == "text"]
    assert calls == ["notes", "answer"] and texts[-2:] == ["Now add level 2", "Level 2 added."], (calls, texts)
    # retry under the notes of a fresh continued chat: they are written again
    r = (await signed_in.post(f"/hub/api/chats/{c['id']}/handover")).json()
    await finish(app, r["run_id"])
    calls.clear()
    again = await signed_in.post(f"/hub/api/chats/{r['chat']['id']}/retry", json={})
    await finish(app, again.json()["run_id"])
    assert calls == ["notes"] and len(chats.messages(r["chat"]["id"])) == 2
    # and the client cannot start a handover itself
    calls.clear()
    fakes.llama["responder"] = responder
    await finish(app, await say(signed_in, c["id"], "Hi", options={"handover_from": c["id"]}))
    assert calls == ["answer"]


async def test_a_planted_link_is_never_followed(signed_in, app, fakes, tmp_path):
    """Code in the sandbox sees the whole workspace: links planted where a chat's folder or its skills belong are
    removed, not followed into the Hub's own files."""
    hub = app.state.hub
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    c = await new_chat(signed_in)
    chats.add_message(c["id"], "user", [{"type": "text", "text": "hi"}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "hello"}])
    ws = hub.env.workspace_dir
    nxt = c["id"] + 1
    (ws / "chats" / str(nxt)).mkdir(parents=True, exist_ok=True)
    (ws / "chats" / str(nxt) / ".hub").symlink_to(outside)                   # the next chat's folder, planted
    responder, _ = notes_writer()
    fakes.llama["responder"] = responder
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/handover")
    assert r.status_code == 200 and r.json()["chat"]["id"] == nxt
    await finish(app, r.json()["run_id"])
    assert sorted(p.name for p in outside.iterdir()) == ["precious.txt"]
    assert not (ws / "chats" / str(nxt) / ".hub").is_symlink()
    # a chat's folder replaced by a link, and .hub/skills planted: the next turn removes them, keeps the target
    other = await new_chat(signed_in)
    folder = ws / "chats" / str(other["id"])
    if folder.exists():
        folder.rmdir()
    folder.symlink_to(outside)
    fakes.llama["responder"] = None
    fakes.llama["replies"] = [{"content": "ok"}, {"content": "Title"}]
    await finish(app, await say(signed_in, other["id"], "hi"))
    assert not folder.is_symlink() and (outside / "precious.txt").read_text() == "keep me"
    (folder / ".hub").mkdir(exist_ok=True)
    (folder / ".hub" / "skills").symlink_to(outside)
    from app.agent.skills import materialize
    materialize(hub.extras["skills"], [], folder)
    assert (outside / "precious.txt").exists() and not (folder / ".hub" / "skills").is_symlink()


def test_cutting_again_changes_nothing():
    """0.28.4 (chat 78): an older answer or call cut again changed in its tail - "shortened by 33 characters" became
    "32" - so an older message changed at every step, and llama-server read the request again from there."""
    import json
    from app.agent.context import _shrink_args, cut_text, trim_assistant, trim_reasoning
    once = cut_text("a" * 1000, 400)
    assert once == "a" * 400 + "\n... [shortened by 600 characters]"
    assert cut_text(once, 400) == once and cut_text(once, 500) == once
    deeper = cut_text(once, 100)
    assert deeper == "a" * 100 + "\n... [shortened by 900 characters]" and cut_text(deeper, 100) == deeper
    script = json.dumps({"command": "python3 << 'EOF'\n" + 'print("æøå \\"quoted\\"")\n' * 80 + "EOF",
                         "timeout": 60})
    once = _shrink_args(script, 400)
    assert json.loads(once)["command"].endswith("characters]") and json.loads(once)["timeout"] == 60
    assert _shrink_args(once, 400) == once and _shrink_args(_shrink_args(once, 400), 400) == once
    broken = "{not json: æøå " + "x" * 600
    assert _shrink_args(_shrink_args(broken, 400), 400) == _shrink_args(broken, 400)
    msgs = [{"role": "user", "content": "go"}] + [
        {"role": "assistant", "content": f"Step {i}: " + "t" * 900, "reasoning_content": "r" * 3000,
         "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "bash", "arguments": script}}]}
        for i in range(6)]
    cut = trim_assistant(msgs, keep_last=2, limit=400)
    assert trim_assistant(cut, keep_last=2, limit=400) == cut
    assert trim_assistant(trim_assistant(cut, keep_last=2, limit=400), keep_last=2, limit=400) == cut
    assert "reasoning_content" not in cut[1] and cut[-1]["reasoning_content"] == "r" * 3000
    # older thinking leaves first: the newest two steps keep theirs, the others lose only that
    lean = trim_reasoning(msgs, keep_last=2)
    assert [("reasoning_content" in m) for m in lean[1:]] == [False] * 4 + [True] * 2
    assert [m["content"] for m in lean] == [m["content"] for m in msgs] and trim_reasoning(lean, keep_last=2) == lean


def test_shortening_is_stable_and_keeps_where_the_whole_is():
    """Shortened again, a result stays the same (llama-server's prompt cache holds) unless it is cut deeper - and
    keeps the true count and where the whole output is."""
    from app.agent.context import shorten_result, trim_tool_results
    saved = "x" * 12000 + "\n\n(The full output is in .hub/outputs/c1.txt - read it with read_file and offset/limit.)"
    once = shorten_result(saved, 400, "cleared")
    assert once.startswith("x" * 400) and once.endswith("[... 11600 more characters - cleared; the whole output is in "
                                                        ".hub/outputs/c1.txt]")
    assert shorten_result(once, 400, "cleared") == once and shorten_result(once, 1500, "made room") == once
    line = shorten_result(once, 0, "left out")
    assert line == "[12000 characters - left out; the whole output is in .hub/outputs/c1.txt]"
    assert shorten_result(line, 0, "left out") == line
    plain = shorten_result("y" * 3000, 400, "an earlier turn")
    assert plain.endswith("[... 2600 more characters - an earlier turn; look it up whole with chat_history, or call "
                          "the tool again]")
    # the last step's results are the model's next read: kept whole, unless nothing else fits
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "type": "function",
                                                                "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "A" * 2000},
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"b{i}", "type": "function",
                                                                "function": {"name": "read_file", "arguments": "{}"}}
                                                               for i in range(3)]},
            *({"role": "tool", "tool_call_id": f"b{i}", "content": "B" * 2000} for i in range(3))]
    kept = trim_tool_results(msgs, keep_last=1, limit=100)
    assert len(kept[2]["content"]) < 300 and all(m["content"] == "B" * 2000 for m in kept[4:])
    last_resort = trim_tool_results(msgs, keep_last=1, limit=100, unread=True)
    assert [len(m["content"]) < 300 for m in last_resort[4:]] == [True, True, False]


async def test_a_mark_of_shortened_context_is_not_written_into_a_file(signed_in, app, fakes):
    """Chat 79: the 9B model wrote "<p>Brow... [shortened by 24 characters]" into its game's index.html - the Hub's
    mark for text left out of what it was shown, copied. write_file, edit_file and artifact refuse a mark the file
    does not have already."""
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    page = "<h1>CM 98</h1>\n<p>Brow... [shortened by 24 characters]\n"
    good = "<h1>CM 98</h1>\n<p>Browser Edition</p>\n"
    cleared = "[... 900 more characters - cleared to keep the conversation short; look it up whole with chat_history]"
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "index.html", "content": page}}]},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "index.html", "content": good}}]},
        {"tool_calls": [{"name": "edit_file", "arguments": {"path": "index.html", "old_string": "Browser Edition",
                                                            "new_string": cleared}}]},
        {"tool_calls": [{"name": "artifact", "arguments": {"action": "create", "id": "p", "title": "P", "type": "html",
                                                           "content": page}}]},
        {"content": "Done."}, {"content": "Title"}]
    run = await finish(app, await say(signed_in, c["id"], "Lav siden"))
    res = [e["data"] for e in run.events if e["type"] == "tool_result"]
    assert [r["ok"] for r in res] == [False, True, False, False]
    assert res[0]["text"].startswith('Not written: the text for index.html has "... [shortened by 24 characters]" in '
                                     "it. That is the Hub's mark for text left out of what you were shown")
    assert cleared in res[2]["text"] and "the artifact 'p'" in res[3]["text"]
    assert (folder / "index.html").read_text() == good and not chats.get_artifact(c["id"], "p")
    (folder / "log.txt").write_text("x... [shortened by 3 characters]\n")            # a file with the mark already
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "log.txt",
                                                             "content": "x... [shortened by 3 characters]\ny\n"}}]},
        {"content": "Done."}]
    run = await finish(app, await say(signed_in, c["id"], "Tilføj en linje"))
    assert [e["data"]["ok"] for e in run.events if e["type"] == "tool_result"] == [True]


def test_every_mark_the_hub_makes_is_known_to_the_guard():
    """The guard knows each mark the Hub puts where it left text out, with every reason the code gives - and not
    ordinary text that looks a bit like one."""
    import re
    from pathlib import Path

    from app.agent import context as C
    from app.agent.tools.base import clip
    from app.agent.tools.files import copied_mark
    src = "".join(Path(C.__file__).with_name(n).read_text() for n in ("loop.py", "context.py"))
    whys = {a or b for a, b in re.findall(r'\bwhy="([^"]+)"|\bwhy: str = "([^"]+)"', src)}
    whys |= set(re.findall(r'shorten_result\([^)]*?, "([^"]+)"\)', src))
    assert whys and whys <= set(C.WHYS), whys - set(C.WHYS)
    text = "x" * 5000
    full = text + "\n\n(The full output is in .hub/outputs/x.txt - read it with read_file and offset/limit.)"
    marks = [C.cut_text(text, 100), C.cut_text(text, 100, ""), clip(text, 1000)[0], C.shorten_result(full, 100, C.WHYS[0])]
    marks += [C.shorten_result(text, head, why) for why in C.WHYS for head in (0, 100)]
    for m in marks:
        assert copied_mark(m), m[-160:]
    for plain in ("[12 characters - letters; digits]", "... [see below]", "shortened by 3 characters", "[3 characters omitted]"):
        assert copied_mark(plain) is None, plain
