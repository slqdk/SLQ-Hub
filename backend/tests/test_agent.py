"""The agent: turns in chats and projects, tools, approvals, stopping, MCP,
escalation, subagents, compaction, attachments - against the fake services."""
from __future__ import annotations

import asyncio
import json
import re
import time

from app import db
from app.chats import store as chats

from .mocks import _png


async def until(cond, timeout: float = 10) -> None:
    t0 = time.monotonic()
    while not cond():
        assert time.monotonic() - t0 < timeout, "timed out"
        await asyncio.sleep(0.01)


async def finish(app, run_id: str, timeout: float = 15):
    run = app.state.hub.extras["agent"].runs.get(run_id)
    await until(lambda: run.finished, timeout)
    await app.state.hub.scheduler.drain()
    return run


def texts(run) -> str:
    return "".join(e["data"]["text"] for e in run.events if e["type"] == "text" and not e["data"].get("depth"))


STAMP = re.compile(r"^\[(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}\]\n?")


def unstamp(text: str) -> str:
    """A user message as the model gets it, without the time it was sent ("[Fri 23 Oct 2026 06:57]")."""
    return STAMP.sub("", text, count=1)


def last_user(body: dict) -> str:
    for m in reversed(body["messages"]):
        if m["role"] == "user":
            c = m["content"]
            return unstamp(c if isinstance(c, str) else " ".join(p.get("text", "") for p in c))
    return ""


async def new_chat(client, **kw) -> dict:
    r = await client.post("/hub/api/chats", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


async def say(client, chat_id: int, text: str, **kw) -> str:
    r = await client.post(f"/hub/api/chats/{chat_id}/messages", json={"text": text, **kw})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


# ---------------------------------------------------------------- plain turns

async def test_a_simple_turn_streams_saves_and_titles(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "Hej Søren!", "reasoning": "a greeting"}, {"content": "Danish greeting."}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Hej"))
    assert run.status == "done"
    kinds = [e["type"] for e in run.events]
    assert kinds[0] == "queued" and "started" in kinds and kinds[-1] == "done"
    assert texts(run) == "Hej Søren!"
    assert any(e["type"] == "usage" and e["data"]["tps"] == 70.0 for e in run.events)

    data = (await signed_in.get(f"/hub/api/chats/{c['id']}")).json()
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    assert data["messages"][1]["content"] == [{"type": "thinking", "text": "a greeting"},
                                               {"type": "text", "text": "Hej Søren!"}]
    assert data["chat"]["title"] == "Danish greeting"
    assert data["run"] is None and data["runs"][0]["status"] == "done"
    assert {s["type"] for s in data["runs"][0]["steps"]} >= {"queue", "model"}

    first = fakes.llama["requests"][0]
    system = first["messages"][0]
    assert system["role"] == "system" and "SLQ Hub" in system["content"] and "Soren" in system["content"]
    assert first["chat_template_kwargs"] == {"enable_thinking": False}      # plain chats answer without thinking
    names = {t["function"]["name"] for t in first["tools"]}
    assert {"bash", "read_file", "write_file", "web_search", "memory", "artifact", "task"} <= names
    assert "escalate" not in names                                         # only in projects that allow it
    title_req = fakes.llama["requests"][1]
    assert "short title" in last_user(title_req) and "tools" not in title_req


async def test_tools_work_in_the_chat_folder(signed_in, app, fakes):
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "hello.py", "content": "print('hi')\n"}}]},
        {"tool_calls": [{"name": "bash", "arguments": {"command": "python hello.py"}}]},
        {"content": "It prints hi."},
        {"content": "Hello script"},
    ]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Write and run a hello script"))
    assert run.status == "done", run.events[-1]
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    assert (folder / "hello.py").read_text() == "print('hi')\n"
    assert fakes.sandbox["execs"][0]["cwd"] == f"/workspace/chats/{c['id']}"
    msgs = chats.messages(c["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    third = fakes.llama["requests"][2]["messages"]
    assert third[-1]["role"] == "tool" and "ran: python hello.py" in third[-1]["content"]
    assert third[-2]["tool_calls"][0]["function"]["name"] == "bash"
    kinds = [e["type"] for e in run.events]
    assert kinds.count("tool_call") == 2 and kinds.count("tool_result") == 2


async def test_the_files_an_answer_made_are_listed_under_it(signed_in, app, fakes):
    """What a turn made or changed in its folder - by a tool or by a command - is a step of the run, for the card
    under the answer; scratch files it removed again, .hub and untouched files are not."""
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "notes.md").write_text("old\n")
    (folder / "untouched.txt").write_text("same\n")

    def ffmpeg(body):                                  # what a command in the sandbox leaves behind
        (folder / "30-seconds.mp3").write_bytes(b"ID3" + bytes(200))
        (folder / ".hub").mkdir(exist_ok=True)
        (folder / ".hub" / "tmp.wav").write_bytes(b"RIFF")
        return {"exit_code": 0, "stdout": "done\n", "stderr": "", "timed_out": False, "duration_ms": 5,
                "truncated": False}

    fakes.sandbox["handler"] = ffmpeg
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "bash", "arguments": {"command": "ffmpeg -i voice.mp3 30-seconds.mp3"}}]},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "scratch.txt", "content": "x"}}]},
        {"tool_calls": [{"name": "delete_file", "arguments": {"path": "scratch.txt"}}]},
        {"tool_calls": [{"name": "edit_file", "arguments": {"path": "notes.md", "old_string": "old",
                                                            "new_string": "new"}}]},
        {"content": "Saved as 30-seconds.mp3."}, {"content": "Jingle"}]
    run = await finish(app, await say(signed_in, c["id"], "Make the jingle"))
    assert run.status == "done"
    data = (await signed_in.get(f"/hub/api/chats/{c['id']}")).json()
    files = next(s for s in data["runs"][-1]["steps"] if s["type"] == "files")["files"]
    assert [(f["path"], f["new"]) for f in files] == [("notes.md", False), ("30-seconds.mp3", True)]  # newest first
    assert files[1] == {"path": "30-seconds.mp3", "size": 203, "mime": "audio/mpeg", "new": True}
    assert any(e["type"] == "files" for e in run.events)
    md = (await signed_in.get(f"/hub/api/chats/{c['id']}/export", params={"format": "md"})).text
    assert "*Files from this turn: `notes.md` (changed, 4 bytes), `30-seconds.mp3` (new, 203 bytes).*" in md
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "shown to the user under your answer" in system
    # the card plays it (a range, for the player) and downloads it; a page from the folder stays sandboxed
    raw = f"/hub/api/folders/chat/{c['id']}/raw"
    r = await signed_in.get(raw, params={"path": "30-seconds.mp3"}, headers={"Range": "bytes=0-2"})
    assert r.status_code == 206 and r.content == b"ID3" and r.headers["content-type"] == "audio/mpeg"
    assert r.headers["content-security-policy"] == "sandbox"
    r = await signed_in.get(raw, params={"path": "30-seconds.mp3", "download": "true"})
    assert "attachment" in r.headers["content-disposition"]
    # a PDF opens in the browser's viewer, which a sandboxed response would not allow
    (folder / "guide.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    r = await signed_in.get(raw, params={"path": "guide.pdf"})
    assert r.headers["content-type"] == "application/pdf" and "content-security-policy" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"


def test_a_folder_snapshot_skips_what_sync_ignores(tmp_path):
    from app.projects import store as projects
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "index.js").write_text("x")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print(1)")
    (tmp_path / "app.pyc").write_bytes(b"\0")
    (tmp_path / "link.py").symlink_to(tmp_path / "src" / "app.py")
    snap = projects.snapshot(tmp_path)
    assert set(snap) == {"src/app.py"}
    assert projects.snapshot(tmp_path, limit=0) is None                         # too many to compare
    later = {**snap, "out.mp3": (10, snap["src/app.py"][1] + 5)}
    assert projects.produced(snap, later) == [{"path": "out.mp3", "size": 10, "mime": "audio/mpeg", "new": True}]


async def test_project_turns_think_and_files_become_revisions(signed_in, app, fakes):
    p = (await signed_in.post("/hub/api/projects", json={"name": "Demo", "instructions": "Answer in English."})).json()
    c = await new_chat(signed_in, project_id=p["id"])
    fakes.llama["replies"] = [
        {"reasoning": "plan it", "tool_calls": [{"name": "write_file",
                                                  "arguments": {"path": "notes/plan.md", "content": "# Plan\n"}}]},
        {"content": "I wrote the plan."}, {"content": "Plan"}]
    run = await finish(app, await say(signed_in, c["id"], "Make a plan"))
    assert run.status == "done"
    first = fakes.llama["requests"][0]
    assert first["chat_template_kwargs"] == {"enable_thinking": True} and first["reasoning_effort"] == "medium"
    assert "Answer in English." in first["messages"][0]["content"]
    assert f"/workspace/projects/{p['id']}" in first["messages"][0]["content"]
    revs = (await signed_in.get(f"/hub/api/projects/{p['id']}/revisions")).json()
    rev_list = revs["revisions"] if isinstance(revs, dict) else revs
    assert any(f["path"] == "notes/plan.md" for r in rev_list for f in r["files"])
    # The model's own reasoning goes back to it inside the same turn only.
    second = fakes.llama["requests"][1]["messages"]
    assert second[-2]["reasoning_content"] == "plan it"


async def test_a_turn_can_be_stopped_in_a_slow_tool(signed_in, app, fakes):
    fakes.sandbox["delay"] = 5
    fakes.llama["replies"] = [{"content": "Let me run it.", "tool_calls": [{"name": "bash", "arguments": {"command": "sleep 100"}}]},
                              {"content": "never"}]
    c = await new_chat(signed_in)
    run_id = await say(signed_in, c["id"], "run something slow")
    run = app.state.hub.extras["agent"].runs.get(run_id)
    await until(lambda: any(e["type"] == "tool_call" for e in run.events))
    r = await signed_in.post(f"/hub/api/runs/{run_id}/stop")
    assert r.status_code == 200
    await finish(app, run_id)
    assert run.status == "cancelled"
    msgs = chats.messages(c["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool"]
    assert msgs[2]["content"][0]["text"].startswith("Stopped by the user")
    assert app.state.hub.scheduler.running == {}
    # The next turn still sees complete call/result pairs.
    fakes.sandbox["delay"] = 0
    fakes.llama["replies"] = [{"content": "ok"}]
    run2 = await finish(app, await say(signed_in, c["id"], "never mind"))
    assert run2.status == "done"
    hist = [b for b in fakes.llama["requests"] if b.get("tools")][-1]["messages"]
    assert [m["role"] for m in hist[1:]] == ["user", "assistant", "tool", "user"]


async def test_stopping_while_the_model_writes_keeps_the_text(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "partial answer", "delay": 3}]
    c = await new_chat(signed_in)
    run_id = await say(signed_in, c["id"], "hi")
    run = app.state.hub.extras["agent"].runs.get(run_id)
    await until(lambda: any(e["type"] == "step" for e in run.events))
    await signed_in.post(f"/hub/api/runs/{run_id}/stop")
    await finish(app, run_id)
    assert run.status == "cancelled"
    assert [m["role"] for m in chats.messages(c["id"])] == ["user"]      # nothing written yet: nothing saved


async def test_a_message_sent_while_it_works_reaches_the_model(signed_in, app, fakes):
    agent = app.state.hub.extras["agent"]
    fakes.sandbox["delay"] = 0.6
    fakes.llama["replies"] = [{"content": "Let me run it.", "tool_calls": [{"name": "bash", "arguments": {"command": "make"}}]},
                              {"content": "Moved the popup."}, {"content": "Popup"}]
    c = await new_chat(signed_in)
    run_id = await say(signed_in, c["id"], "fix the game")
    run = agent.runs.get(run_id)
    await until(lambda: any(e["type"] == "tool_call" for e in run.events))
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/messages", json={"text": "I can't see Mario for the popup"})
    assert r.status_code == 200, r.text
    assert r.json()["queued"].startswith("note_") and r.json()["run_id"] == run_id and r.json()["message"] is None
    await finish(app, run_id)
    assert run.status == "done"
    # After the tool's result, before its next step, the model reads the note.
    second = [b for b in fakes.llama["requests"] if b.get("tools")][1]["messages"]
    assert [m["role"] for m in second[1:]] == ["user", "assistant", "tool", "user"]
    assert unstamp(second[-1]["content"]).startswith("(The user wrote this while you were working")
    assert second[-1]["content"].endswith("I can't see Mario for the popup")
    rows = chats.messages(c["id"])
    assert [m["role"] for m in rows] == ["user", "assistant", "tool", "user", "assistant"]
    assert rows[3]["run_id"] == run_id and rows[3]["content"][0]["while_working"] is True
    kinds = [e["type"] for e in run.events]
    assert kinds.index("user_queued") < kinds.index("user_message")

    # Written while it writes its last answer: it answers that too, in the same turn.
    fakes.sandbox["delay"] = 0
    fakes.llama["requests"] = []
    fakes.llama["replies"] = [{"content": "Done.", "delay": 0.5}, {"content": "Blue it is."}]
    run_id = await say(signed_in, c["id"], "colour?")
    run = agent.runs.get(run_id)
    await until(lambda: any(e["type"] == "step" for e in run.events))
    assert (await signed_in.post(f"/hub/api/chats/{c['id']}/messages", json={"text": "make it blue"})).status_code == 200
    await finish(app, run_id)
    assert [m["role"] for m in chats.messages(c["id"])][-4:] == ["user", "assistant", "user", "assistant"]
    assert chats.messages(c["id"])[-1]["content"][0]["text"] == "Blue it is."


async def test_a_late_message_or_stop_starts_the_next_turn(signed_in, app, fakes, monkeypatch):
    agent = app.state.hub.extras["agent"]
    c = await new_chat(signed_in)
    # Arrives after the last step it could be read in: it becomes the next turn, as a normal message.
    sent = []
    orig = agent._title

    async def late(chat, engine):
        if not sent:
            sent.append(1)
            engine.run.inbox.append({"id": "note_late", "blocks": [{"type": "text", "text": "one more thing"}]})
        await orig(chat, engine)

    monkeypatch.setattr(agent, "_title", late)
    fakes.llama["replies"] = [{"content": "first"}, {"content": "Title"}, {"content": "About that thing."}]
    run1 = await finish(app, await say(signed_in, c["id"], "hello"))
    assert run1.status == "done"
    await until(lambda: len(chats.messages(c["id"])) == 4)
    rows = chats.messages(c["id"])
    assert [m["role"] for m in rows] == ["user", "assistant", "user", "assistant"]
    assert rows[2]["content"] == [{"type": "text", "text": "one more thing"}] and rows[2]["run_id"] is None
    assert rows[3]["content"][0]["text"] == "About that thing." and rows[3]["run_id"] != run1.id
    await finish(app, rows[3]["run_id"])

    # Stop with a note waiting: the run stops, and the note starts the next turn.
    fakes.sandbox["delay"] = 5
    fakes.llama["requests"] = []                    # the fake model answers by request number
    fakes.llama["replies"] = [{"content": "Running.", "tool_calls": [{"name": "bash", "arguments": {"command": "sleep 9"}}]},
                              {"content": "Looking at your note."}]
    run_id = await say(signed_in, c["id"], "build it")
    run = agent.runs.get(run_id)
    await until(lambda: any(e["type"] == "tool_call" for e in run.events))
    assert (await signed_in.post(f"/hub/api/chats/{c['id']}/messages", json={"text": "wait, use the other map"})).status_code == 200
    await signed_in.post(f"/hub/api/runs/{run_id}/stop")
    await finish(app, run_id)
    assert run.status == "cancelled"
    await until(lambda: chats.messages(c["id"])[-1]["role"] == "assistant"
                and chats.messages(c["id"])[-1]["run_id"] != run_id)
    rows = chats.messages(c["id"])
    assert [b.get("text") for b in rows[-2]["content"]] == ["wait, use the other map"]
    assert rows[-1]["content"][0]["text"] == "Looking at your note."


async def test_errors_are_reported(signed_in, app, fakes):
    c = await new_chat(signed_in)
    fakes.llama["replies"] = [{"error": "model exploded"}]
    run = await finish(app, await say(signed_in, c["id"], "again"))
    assert run.status == "error" and "model exploded" in run.events[-1]["data"]["message"]


def test_the_time_a_message_was_sent(monkeypatch):
    from app.agent.prompt import sent_stamp
    monkeypatch.delenv("TZ", raising=False)
    assert sent_stamp("2026-10-23T04:57:43+00:00") == "[Fri 23 Oct 2026 06:57]"        # summer time
    assert sent_stamp("2026-12-01T12:00:00Z") == "[Tue 1 Dec 2026 13:00]"              # winter time, a browser's Z
    assert sent_stamp("2026-12-01T12:00:00") == "[Tue 1 Dec 2026 13:00]"               # naive = UTC
    assert sent_stamp("") == sent_stamp(None) == sent_stamp("yesterday") == ""
    monkeypatch.setenv("TZ", "America/New_York")
    assert sent_stamp("2026-10-23T04:57:43+00:00") == "[Fri 23 Oct 2026 00:57]"
    monkeypatch.setenv("TZ", "CET-1CEST")                                            # not a zone name: Copenhagen
    assert sent_stamp("2026-10-23T04:57:43+00:00") == "[Fri 23 Oct 2026 06:57]"


async def test_the_model_knows_when_each_message_was_sent(signed_in, app, fakes):
    """The time goes with each user message, not into the system prompt: earlier messages read the same in every
    later turn, so llama-server's cached prompt stays valid."""
    fakes.llama["replies"] = [{"content": "Prices for the rest of today."}, {"content": "Elpriser"},
                              {"content": "Tonight it is cheapest at 23."}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hvad koster strømmen det næste døgn?"))
    first = fakes.llama["requests"][0]["messages"]
    assert "starts with the local date and time it was sent" in first[0]["content"]
    with db.connect() as con:
        con.execute("UPDATE messages SET created_at='2026-10-23T04:57:43+00:00' WHERE chat_id=? AND role='user'",
                    (c["id"],))
    await finish(app, await say(signed_in, c["id"], "Og i aften?"))
    second = [m for m in fakes.llama["requests"][-1]["messages"] if m["role"] == "user"]
    assert second[0]["content"] == "[Fri 23 Oct 2026 06:57]\nHvad koster strømmen det næste døgn?"
    assert STAMP.match(second[1]["content"]) and unstamp(second[1]["content"]) == "Og i aften?"


async def test_retry_and_edit(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "answer one"}, {"content": "Title"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "question"))
    fakes.llama["replies"] = [{"content": "answer two"}]
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/retry", json={})
    await finish(app, r.json()["run_id"])
    msgs = chats.messages(c["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["content"][-1]["text"] == "answer two"
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/edit", json={"message_id": msgs[0]["id"], "text": "better question"})
    await finish(app, r.json()["run_id"])
    msgs = chats.messages(c["id"])
    assert msgs[0]["content"][0]["text"] == "better question" and len(msgs) == 2
    assert last_user(fakes.llama["requests"][-1]) == "better question"


async def test_events_stream_over_sse(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "streamed"}, {"content": "T"}]
    c = await new_chat(signed_in)
    run_id = await say(signed_in, c["id"], "hi")
    await finish(app, run_id)
    r = await signed_in.get(f"/hub/api/runs/{run_id}/events")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith('data: {"')]
    assert events[-1]["type"] == "done" and "".join(e["data"].get("text", "") for e in events if e["type"] == "text") == "streamed"
    r = await signed_in.get(f"/hub/api/runs/{run_id}/events", headers={"Last-Event-ID": str(events[-2]["seq"])})
    later = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith('data: {"')]
    assert [e["seq"] for e in later] == [events[-1]["seq"]]


# ---------------------------------------------------------------- attachments and images

async def test_an_attached_image_reaches_the_model_and_the_folder(signed_in, app, fakes):
    up = (await signed_in.post("/hub/api/uploads", files={"file": ("photo.png", _png((20, 10)), "image/png")})).json()
    assert up["width"] == 20 and up["url"].startswith("/hub/media/blob/")
    assert (await signed_in.get(up["url"])).content == _png((20, 10))
    fakes.llama["replies"] = [{"content": "A red rectangle."}, {"content": "Photo"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "What is this?", attachments=[up["sha"]]))
    assert run.status == "done"
    parts = fakes.llama["requests"][0]["messages"][-1]["content"]
    assert parts[0]["type"] == "text" and STAMP.match(parts[0]["text"]) and unstamp(parts[0]["text"]) == "What is this?"
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/")
    assert ".hub/attachments/photo.png" in parts[2]["text"]
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    assert (folder / ".hub" / "attachments" / "photo.png").read_bytes() == _png((20, 10))


async def test_project_files_can_be_put_in_a_message(signed_in, app, fakes):
    from app.agent import context as C
    from app.projects import store as projects
    p = (await signed_in.post("/hub/api/projects", json={"name": "Refs"})).json()
    base = f"/hub/api/folders/project/{p['id']}"
    r = await signed_in.put(f"{base}/file", json={"path": "src/main.py", "text": "print('hi')\n# ```not the end```\n"})
    assert r.status_code == 200, r.text
    folder = app.state.hub.env.workspace_dir / "projects" / str(p["id"])
    (folder / "pics").mkdir()
    (folder / "pics" / "logo.png").write_bytes(_png((8, 8)))
    (folder / "big.log").write_text("x" * 300_000)            # more than the model's share for files
    (folder / "blob.bin").write_bytes(b"\x00\x01" * 100)
    c = await new_chat(signed_in, project_id=p["id"])
    fakes.llama["replies"] = [{"content": "Done."}, {"content": "Refs"}]
    run = await finish(app, await say(signed_in, c["id"], "Change the greeting",
                                      refs=["src/main.py", "pics/logo.png", "big.log", "blob.bin", "./src/main.py"]))
    assert run.status == "done"
    parts = fakes.llama["requests"][0]["messages"][-1]["content"]
    # The files' text comes first, as it was when the message was sent; then what the user wrote.
    assert unstamp(parts[0]["text"]).startswith("[File from the working folder: src/main.py (2 lines), as it was")
    assert "\n````\nprint('hi')\n# ```not the end```\n````" in parts[0]["text"]
    assert "big.log (300.0 KB) - read it with read_file" in parts[1]["text"]
    assert "blob.bin (200 bytes) - read it with read_file" in parts[2]["text"]
    assert parts[3] == {"type": "text", "text": "Change the greeting"}
    assert parts[4]["type"] == "image_url"
    assert parts[5]["text"] == "[Image from the working folder: pics/logo.png]"
    row = chats.messages(c["id"])[0]
    assert [b["type"] for b in row["content"]] == ["text", "file_ref", "image", "file_ref", "file_ref"]
    assert row["content"][1]["inline"] and not row["content"][3].get("inline")
    # Once the message is old, the text gives way to a pointer, like pictures do.
    old = C.to_model_messages([row], keep_images_from=10**12)[0]["content"]
    assert "src/main.py (32 bytes) - it was shown with this message" in old
    # A message can be just files; paths must be files inside the folder.
    fakes.llama["replies"] = [{"content": "Seen."}]
    assert (await finish(app, await say(signed_in, c["id"], "", refs=["big.log"]))).status == "done"
    for path, status in (("../../etc/passwd", 400), ("nope.txt", 404), ("src", 404)):
        r = await signed_in.post(f"/hub/api/chats/{c['id']}/messages", json={"text": "x", "refs": [path]})
        assert r.status_code == status, (path, r.text)
    # A character cut in two at byte 8000 does not make a text file binary.
    assert projects.is_text(("a" * 7999 + "ø").encode())


async def test_image_generation_switches_the_card_and_back(signed_in, app, fakes):
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a red fox in snow", "fast": True}}]},
        {"content": "Here is your fox."}, {"content": "Fox"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Draw a fox"))
    assert run.status == "done", run.events[-1]
    assert len(fakes.comfy["prompts"]) == 1
    result = next(e for e in run.events if e["type"] == "tool_result")
    assert result["data"]["ok"] and result["data"]["images"] and "The call asked for" not in result["data"]["text"]
    # the model looked at the picture it made
    after = fakes.llama["requests"][1]["messages"]
    assert after[-1]["role"] == "user" and after[-1]["content"][1]["type"] == "image_url"
    starts = [s["name"] for s in fakes.sup["starts"]]
    assert starts.count("main") == 2                              # loaded, ComfyUI, loaded again
    assert app.state.hub.scheduler.current == "llm:qwen3.8-27b"



async def test_ernie_image_with_its_prompt_enhancer(signed_in, app, fakes):
    hub = app.state.hub
    for f in hub.catalog.get("comfy", "ernie-image-turbo")["files"]:        # only Turbo is downloaded
        p = hub.env.models_dir / f["dest"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake")
    fakes.comfy["preview_text"] = "A red fox stands in deep fresh snow at dusk, soft blue light, photorealistic."
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a fox in snow", "model": "ernie-image"}}]},
        {"content": "Here it is."}, {"content": "Fox"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Draw a fox"))
    assert run.status == "done", run.events[-1]
    graph = fakes.comfy["prompts"][-1]
    assert {n["class_type"] for n in graph.values()} >= {"TextGenerate", "PreviewAny", "KSampler"}
    assert any(n["inputs"].get("unet_name") == "ernie-image-turbo.safetensors" for n in graph.values())
    result = next(e for e in run.events if e["type"] == "tool_result")
    assert result["data"]["ok"] and "prompt enhancer turned the prompt into: A red fox stands" in result["data"]["text"]
    # the call named another model than the chat's (Settings: Qwen-Image 2.1): the result says so
    assert "The call asked for ernie-image; this chat's picture model is Qwen-Image 2.1." in result["data"]["text"]
    tool_msg = next(m for m in fakes.llama["requests"][1]["messages"] if m["role"] == "tool")
    assert "A red fox stands in deep fresh snow" in json.dumps(tool_msg)

    # Off in Settings: no enhancer in the graph. ERNIE cannot edit: an edit goes to Qwen-Image.
    hub.settings.update("images", {"enhance_prompts": False, "model": "ernie-image"})
    items = await hub.extras["images"].generate("a fox", 512, 512)
    graph = fakes.comfy["prompts"][-1]
    assert "TextGenerate" not in {n["class_type"] for n in graph.values()} and "prompt_used" not in items[0]
    items = await hub.extras["images"].edit("make it blue", [_png((8, 8))])
    assert items[0]["model"] == "qwen-image-2.1"



async def test_the_chat_or_project_chooses_the_picture_model(signed_in, app, fakes):
    hub = app.state.hub
    for f in hub.catalog.get("comfy", "ernie-image-turbo")["files"]:
        p = hub.env.models_dir / f["dest"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake")

    def unet() -> str:
        return next(n["inputs"]["unet_name"] for n in fakes.comfy["prompts"][-1].values()
                    if n["class_type"] == "UNETLoader")

    async def draw(chat_id: int) -> None:
        fakes.llama["requests"] = []                                  # the fake answers by request number
        fakes.llama["replies"] = [{"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a fox"}}]},
                                  {"content": "Done."}, {"content": "Fox"}]
        run = await finish(app, await say(signed_in, chat_id, "Draw a fox"))
        assert run.status == "done", run.events[-1]

    proj = (await signed_in.post("/hub/api/projects", json={"name": "Photos"})).json()
    r = await signed_in.patch(f"/hub/api/projects/{proj['id']}", json={"settings": {"image_model": "ernie-image"}})
    assert r.json()["settings"]["image_model"] == "ernie-image"
    c = await new_chat(signed_in, project_id=proj["id"])
    await draw(c["id"])
    assert unet() == "ernie-image-turbo.safetensors"                 # the project's choice (Turbo is what is here)
    r = await signed_in.patch(f"/hub/api/chats/{c['id']}", json={"settings": {"image_model": "qwen-image-2.1"}})
    assert r.json()["settings"]["image_model"] == "qwen-image-2.1"
    await draw(c["id"])
    assert unet().startswith("qwen_image_2.1")                       # the chat's own choice wins
    plain = await new_chat(signed_in)
    await draw(plain["id"])
    assert unet().startswith("qwen_image_2.1")                       # Settings' default


# ---------------------------------------------------------------- MCP

async def test_mcp_over_http_asks_first_and_can_be_always_allowed(signed_in, app, fakes):
    fakes.mcp["auth"] = "Bearer s3cret"
    server = {"name": "calc", "transport": "http", "url": "http://mcp.test/mcp",
              "headers": {"Authorization": "Bearer s3cret"}, "enabled": True}
    r = await signed_in.put("/hub/api/settings", json={"section": "mcp", "values": {"servers": [server]}})
    assert r.json()["settings"]["mcp"]["servers"][0]["headers"]["Authorization"] == "********"
    status = (await signed_in.post("/hub/api/mcp/reload")).json()
    assert status[0]["status"] == "ready", status
    assert [t["agent_name"] for t in status[0]["tools"]] == ["mcp__calc__add", "mcp__calc__picture"]
    # Saving the page again with the mask keeps the real header.
    await signed_in.put("/hub/api/settings", json={"section": "mcp", "values": {"servers": [
        {**server, "headers": {"Authorization": "********"}}]}})
    assert app.state.hub.settings.get("mcp", "servers")[0]["headers"]["Authorization"] == "Bearer s3cret"
    status = (await signed_in.post("/hub/api/mcp/reload")).json()
    assert status[0]["status"] == "ready"

    fakes.llama["replies"] = [{"tool_calls": [{"name": "mcp__calc__add", "arguments": {"a": 2, "b": 3}}]},
                              {"content": "2 + 3 = 5"}, {"content": "Sum"}]
    c = await new_chat(signed_in)
    run_id = await say(signed_in, c["id"], "add 2 and 3")
    run = app.state.hub.extras["agent"].runs.get(run_id)
    await until(lambda: any(e["type"] == "approval_needed" for e in run.events))
    ask = next(e for e in run.events if e["type"] == "approval_needed")["data"]
    assert ask["tool"] == "mcp__calc__add" and ask["arguments"] == {"a": 2, "b": 3}
    r = await signed_in.post(f"/hub/api/runs/{run_id}/approve", json={"call_id": ask["id"], "decision": "always"})
    assert r.status_code == 200
    await finish(app, run_id)
    tool_msg = [m for m in chats.messages(c["id"]) if m["role"] == "tool"][0]["content"][0]
    assert tool_msg["text"] == "5" and not tool_msg["is_error"]
    schema = next(t for t in fakes.llama["requests"][0]["tools"] if t["function"]["name"] == "mcp__calc__add")
    assert "$schema" not in schema["function"]["parameters"]
    assert [p["tool"] for p in (await signed_in.get("/hub/api/tool-permissions")).json()] == ["mcp__calc__add"]
    # Second time: no question.
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": "mcp__calc__add", "arguments": {"a": 1, "b": 1}}]}, {"content": "2"}]
    run = await finish(app, await say(signed_in, c["id"], "and 1+1"))
    assert run.status == "done" and not any(e["type"] == "approval_needed" for e in run.events)


async def test_a_tool_allowed_in_this_chat(signed_in, app, fakes):
    """A tool that asks first, answered "Allow in this chat": not asked again in the chat, asked in the next."""
    await signed_in.put("/hub/api/settings", json={"section": "mcp", "values": {"servers": [
        {"name": "calc", "transport": "http", "url": "http://mcp.test/mcp", "enabled": True}]}})
    await signed_in.post("/hub/api/mcp/reload")

    def add(a: int):
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = [{"tool_calls": [{"name": "mcp__calc__add", "arguments": {"a": a, "b": 1}}]},
                                  {"content": "ok"}, {"content": "Sum"}]

    c = await new_chat(signed_in)
    add(1)
    run_id = await say(signed_in, c["id"], "add")
    run = app.state.hub.extras["agent"].runs.get(run_id)
    await until(lambda: any(e["type"] == "approval_needed" for e in run.events))
    ask = next(e for e in run.events if e["type"] == "approval_needed")["data"]
    assert ask["can_chat"] is True and "ask" not in ask
    r = await signed_in.post(f"/hub/api/runs/{run_id}/approve", json={"call_id": ask["id"], "decision": "chat"})
    assert r.status_code == 200
    await finish(app, run_id)
    add(2)
    run = await finish(app, await say(signed_in, c["id"], "again"))
    assert not any(e["type"] == "approval_needed" for e in run.events)
    assert (await signed_in.get("/hub/api/tool-permissions")).json() == []
    other = await new_chat(signed_in)
    add(3)
    run_id = await say(signed_in, other["id"], "add")
    run = app.state.hub.extras["agent"].runs.get(run_id)
    await until(lambda: any(e["type"] == "approval_needed" for e in run.events))
    ask = next(e for e in run.events if e["type"] == "approval_needed")["data"]
    await signed_in.post(f"/hub/api/runs/{run_id}/approve", json={"call_id": ask["id"], "decision": "deny"})
    await finish(app, run_id)


async def test_a_declined_tool_call_is_reported_to_the_model(signed_in, app, fakes):
    await signed_in.put("/hub/api/settings", json={"section": "mcp", "values": {"servers": [
        {"name": "calc", "transport": "http", "url": "http://mcp.test/mcp", "enabled": True}]}})
    await signed_in.post("/hub/api/mcp/reload")
    fakes.llama["replies"] = [{"tool_calls": [{"name": "mcp__calc__add", "arguments": {"a": 2, "b": 3}}]},
                              {"content": "OK, I will not."}, {"content": "T"}]
    c = await new_chat(signed_in)
    run_id = await say(signed_in, c["id"], "add")
    run = app.state.hub.extras["agent"].runs.get(run_id)
    await until(lambda: any(e["type"] == "approval_needed" for e in run.events))
    ask = next(e for e in run.events if e["type"] == "approval_needed")["data"]
    await signed_in.post(f"/hub/api/runs/{run_id}/approve", json={"call_id": ask["id"], "decision": "deny"})
    await finish(app, run_id)
    assert "declined" in fakes.llama["requests"][1]["messages"][-1]["content"]


async def test_mcp_over_stdio_in_the_sandbox(signed_in, app, fakes):
    await signed_in.put("/hub/api/settings", json={"section": "mcp", "values": {"servers": [
        {"name": "pics", "transport": "stdio", "command": "npx -y fake-mcp", "enabled": True, "trusted": True}]}})
    status = (await signed_in.post("/hub/api/mcp/reload")).json()
    assert status[0]["status"] == "ready" and status[0]["server"]["name"] == "fake-stdio"
    assert next(iter(fakes.sandbox["procs"].values()))["cmd"] == "npx -y fake-mcp"
    fakes.llama["replies"] = [{"tool_calls": [{"name": "mcp__pics__picture", "arguments": {}}]},
                              {"content": "A picture."}, {"content": "T"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "show me"))
    assert run.status == "done" and not any(e["type"] == "approval_needed" for e in run.events)   # trusted
    after = fakes.llama["requests"][1]["messages"]
    assert after[-2] == {"role": "tool", "tool_call_id": after[-3]["tool_calls"][0]["id"], "content": "here"}
    assert after[-1]["content"][1]["type"] == "image_url"
    await app.state.hub.extras["mcp"].close()
    assert all(p["killed"] for p in fakes.sandbox["procs"].values())


# ---------------------------------------------------------------- escalation, subagents, compaction

async def _cloud_project(client, app) -> dict:
    app.state.hub.settings.update("cloud", {"provider": "openai", "base_url": "http://cloud.test/v1",
                                            "api_key": "k", "model": "big-model"})
    p = (await client.post("/hub/api/projects", json={"name": "Hard", "settings": {"cloud_escalation": True}})).json()
    return await new_chat(client, project_id=p["id"])


async def test_the_model_can_escalate_to_the_cloud(signed_in, app, fakes):
    c = await _cloud_project(signed_in, app)
    fakes.llama["replies"] = [{"tool_calls": [{"name": "escalate", "arguments": {"reason": "needs deep proof"}}]}]
    fakes.cloud["replies"] = [{"content": "The cloud solved it."}]
    run = await finish(app, await say(signed_in, c["id"], "prove it"))
    assert run.status == "done", run.events[-1]
    assert "escalate" in {t["function"]["name"] for t in fakes.llama["requests"][0]["tools"]}
    esc = next(e for e in run.events if e["type"] == "escalated")["data"]
    assert esc == {"model": "openai:big-model", "reason": "needs deep proof"}
    cloud = fakes.cloud["requests"][0]
    assert cloud["model"] == "big-model" and "escalate" not in {t["function"]["name"] for t in cloud["tools"]}
    assert "# Escalated" in cloud["messages"][0]["content"]
    assert "handed this turn over" in cloud["messages"][-1]["content"]
    assert not any("reasoning_content" in m for m in cloud["messages"])
    assert texts(run) == "The cloud solved it."
    assert len(fakes.llama["requests"]) == 1                      # no title: the card was given back


async def test_repeated_failures_escalate_automatically(signed_in, app, fakes):
    c = await _cloud_project(signed_in, app)
    fakes.llama["replies"] = [{"tool_calls": [{"name": "read_file", "arguments": {"path": "missing.txt"}}]}]
    fakes.cloud["replies"] = [{"content": "The file does not exist; here is what to do."}]
    run = await finish(app, await say(signed_in, c["id"], "read it"))
    assert run.status == "done"
    assert len(fakes.llama["requests"]) == 3
    # the third identical step is seen before its call fails a third time
    assert next(e for e in run.events if e["type"] == "escalated")["data"]["reason"] == "the same step (read_file) 3 times"


async def test_api_calls_never_escalate(signed_in, app, fakes, api_key):
    await _cloud_project(signed_in, app)
    fakes.llama["replies"] = [{"content": "local only"}]
    r = await signed_in.post("/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}"},
                             json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]})
    assert r.json()["choices"][0]["message"]["content"] == "local only"
    assert fakes.cloud.get("requests", []) == []


async def test_a_subagent_reports_back(signed_in, app, fakes):
    def responder(body):
        system = body["messages"][0]["content"]
        if "You are a subagent" in system:
            if body["messages"][-1]["role"] == "tool":
                return {"content": "Report: the answer is 42."}
            return {"tool_calls": [{"name": "web_search", "arguments": {"query": "meaning of life"}}]}
        if "short title" in last_user(body):
            return {"content": "Research"}
        if body["messages"][-1]["role"] == "tool":
            return {"content": "The subagent found 42."}
        return {"tool_calls": [{"name": "task", "arguments": {"description": "Look it up",
                                                               "prompt": "Find the meaning of life."}}]}
    fakes.llama["responder"] = responder
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "What is the meaning of life?"))
    assert run.status == "done", run.events[-1]
    sub_reqs = [b for b in fakes.llama["requests"] if "You are a subagent" in b["messages"][0]["content"]]
    assert len(sub_reqs) == 2 and sub_reqs[0]["messages"][1] == {"role": "user", "content": "Find the meaning of life."}
    assert not {"task", "artifact", "todo_write"} & {t["function"]["name"] for t in sub_reqs[0]["tools"]}
    nested = [e for e in run.events if e["data"].get("depth") == 1]
    assert any(e["type"] == "tool_call" and e["data"]["name"] == "web_search" for e in nested)
    main_tool = [m for m in chats.messages(c["id"]) if m["role"] == "tool"]
    assert len(main_tool) == 1 and main_tool[0]["content"][0]["text"] == "Report: the answer is 42."
    assert texts(run) == "The subagent found 42."


async def test_long_chats_are_compacted(signed_in, app, fakes, monkeypatch):
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 3000)
    c = await new_chat(signed_in)
    for i in range(4):
        chats.add_message(c["id"], "user", [{"type": "text", "text": f"Question {i}: " + "x" * 2000}])
        chats.add_message(c["id"], "assistant", [{"type": "text", "text": f"Answer {i}: " + "y" * 2000}])

    def responder(body):
        if "Condense the conversation" in last_user(body):
            return {"content": "SUMMARY: four questions about x."}
        return {"content": "Fine."}
    fakes.llama["responder"] = responder
    run = await finish(app, await say(signed_in, c["id"], "And now?"))
    assert run.status == "done", run.events[-1]
    assert any(e["type"] == "compacted" for e in run.events)
    chat = chats.get_chat(c["id"])
    assert chat["summary"] == "SUMMARY: four questions about x." and chat["summary_upto"] > 0
    main = [b for b in fakes.llama["requests"] if "Condense" not in last_user(b) and "short title" not in last_user(b)][-1]
    assert "SUMMARY: four questions about x." in main["messages"][0]["content"]
    assert [m["role"] for m in main["messages"]] == ["system", "user"]


async def test_the_summary_request_fits_the_context(signed_in, app, fakes, monkeypatch):
    """A chat whose earlier turn ran long (as a model stuck in a loop leaves it):
    the request that condenses it must itself fit the model's context."""
    from app.agent import context as C
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 8000)
    c = await new_chat(signed_in)
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Compare the two cars"}])
    for i in range(40):
        chats.add_message(c["id"], "assistant", [{"type": "text", "text": f"Fetching page {i}."},
                                                 {"type": "tool_call", "id": f"c{i}", "name": "web_fetch",
                                                  "arguments": json.dumps({"url": f"https://cars.test/{i}"})}])
        chats.add_message(c["id"], "tool", [{"type": "tool_result", "tool_call_id": f"c{i}", "name": "web_fetch",
                                             "text": f"page {i} " + "z" * 4000}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "The last answer before the new question."}])

    def responder(body):
        if "Condense the conversation" in last_user(body):
            return {"content": "SUMMARY of the car comparison."}
        return {"content": "Fine."}
    fakes.llama["responder"] = responder
    run = await finish(app, await say(signed_in, c["id"], "Continue"))
    assert run.status == "done", run.events[-1]
    condense = next(b for b in fakes.llama["requests"] if "Condense the conversation" in last_user(b))
    assert C.estimate_tokens(condense["messages"]) <= 8000 * 0.8 and condense["max_tokens"] == 2000
    text = json.dumps(condense["messages"])
    assert "The last answer before the new question." in text          # the newest of the old part stays
    assert "earlier messages are left out here" in text and "page 39" in text
    assert chats.get_chat(c["id"])["summary"] == "SUMMARY of the car comparison."
    for body in fakes.llama["requests"]:
        assert C.estimate_tokens(body["messages"]) + C.schema_tokens(body.get("tools")) <= 8000


# ---------------------------------------------------------------- the loop guard

async def test_a_repeated_look_up_is_refused_and_a_loop_ends_in_an_answer(signed_in, app, fakes):
    """The model asks for the same search (and a failing read) step after
    step, as Ornith 1.5 9B did for 60 steps: the search runs once, the failing
    read may be retried once, then both are refused and the model has to
    answer without tools."""
    with_tools = []

    def responder(body):
        if "short title" in last_user(body):
            return {"content": "Bilerne"}
        with_tools.append(bool(body.get("tools")))
        if not body.get("tools"):
            return {"content": "Her er hvad jeg fandt, og hvad jeg ikke fandt."}
        return {"content": "Lad mig finde tallene.",
                "tool_calls": [{"name": "web_search", "arguments": {"query": "xc60 diesel co2"}},
                               {"name": "read_file", "arguments": {"path": "tal.txt"}}]}
    fakes.llama["responder"] = responder
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Hvor meget forurener en XC60?"))
    assert run.status == "done", run.events[-1]
    assert with_tools == [True, True, True, False]
    results = [b for m in chats.messages(c["id"]) if m["role"] == "tool" for b in m["content"]]
    assert [(r["name"], r["is_error"], "already called" in r["text"]) for r in results] == [
        ("web_search", False, False), ("read_file", True, False),        # step 1: both run (the file is missing)
        ("web_search", True, True), ("read_file", True, False),          # step 2: the search is refused, a retry
        ("web_search", True, True), ("read_file", True, True)]           # step 3: both refused
    forced = [b for b in fakes.llama["requests"] if "short title" not in last_user(b)][-1]
    assert "tools" not in forced and "Stop using tools now" in forced["messages"][-1]["content"]
    assert texts(run).endswith("Her er hvad jeg fandt, og hvad jeg ikke fandt.")
    stored = chats.runs_for_chat(c["id"])[0]["steps"]
    assert {"type": "loop", "reason": "the same step (web_search, read_file) 3 times", "depth": 0} in stored
    assert any(e["type"] == "loop" for e in run.events)
    answer = chats.messages(c["id"])[-1]["content"]
    assert answer[0] == {"type": "notice", "text": "It was repeating itself (the same step (web_search, read_file) "
                                                   "3 times), so it was asked to answer with what it has."}
    from app.agent import context as C
    assert "repeating itself" not in json.dumps(C.to_model_messages(chats.messages(c["id"])))    # never sent on


async def test_repeats_with_new_words_are_counted_too(signed_in, app, fakes):
    """The words change, the search does not: three refused repeats are enough."""
    n = {"step": 0}

    def responder(body):
        if "short title" in last_user(body):
            return {"content": "Søgning"}
        if not body.get("tools"):
            return {"content": "Svaret."}
        n["step"] += 1
        return {"content": f"Forsøg {n['step']}.", "tool_calls": [{"name": "web_search", "arguments": {"query": "x"}}]}
    fakes.llama["responder"] = responder
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "søg"))
    assert n["step"] == 4 and texts(run).endswith("Svaret.")
    assert {"type": "loop", "reason": "3 repeated tool calls", "depth": 0} in chats.runs_for_chat(c["id"])[0]["steps"]


async def test_the_same_step_three_times_ends_in_an_answer(signed_in, app, fakes):
    """A loop of calls that are not look-ups (they run each time) is still
    stopped: the third identical step makes the model answer."""
    calls = []

    def responder(body):
        if "short title" in last_user(body):
            return {"content": "Echo"}
        calls.append(bool(body.get("tools")))
        if not body.get("tools"):
            return {"content": "Done echoing."}
        return {"content": "Again.", "tool_calls": [{"name": "bash", "arguments": {"command": "echo hi"}}]}
    fakes.llama["responder"] = responder
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "echo"))
    assert calls == [True, True, True, False]
    bash = [b for m in chats.messages(c["id"]) if m["role"] == "tool" for b in m["content"]]
    assert len(bash) == 3 and not any("already called" in b["text"] for b in bash)     # bash ran every time
    assert {"type": "loop", "reason": "the same step (bash) 3 times", "depth": 0} in chats.runs_for_chat(c["id"])[0]["steps"]
    assert texts(run).endswith("Done echoing.")


async def test_the_same_look_up_after_a_change_runs_again(signed_in, app, fakes):
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "a.txt", "content": "one\n"}}]},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "a.txt"}}]},
        {"tool_calls": [{"name": "edit_file", "arguments": {"path": "a.txt", "old_string": "one", "new_string": "two"}}]},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "a.txt"}}]},
        {"content": "It says two now."}, {"content": "Edit"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "write, read, change, read"))
    assert run.status == "done"
    reads = [b for m in chats.messages(c["id"]) if m["role"] == "tool" for b in m["content"] if b["name"] == "read_file"]
    assert "one" in reads[0]["text"] and "two" in reads[1]["text"] and not reads[1]["is_error"]
    assert not any(s["type"] == "loop" for s in chats.runs_for_chat(c["id"])[0]["steps"])


async def test_a_long_turn_stays_inside_the_context(signed_in, app, fakes, monkeypatch):
    """One question, many big tool results and long notes in a single turn:
    nothing to condense, so the turn's own results and answers are shortened
    - every request still fits, the tool definitions counted."""
    from app.agent import context as C
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 14000)
    c = await new_chat(signed_in)
    wd = hub.extras["agent"].workdir(chats.get_chat(c["id"]))
    for i in range(12):
        wd.write(f"page{i}.txt", f"page {i}\n" + "w" * 9000 + "\n", author="test", origin="test", message="page")
    fakes.llama["replies"] = [{"content": f"Notes on page {i}: " + "n" * 1500,
                               "tool_calls": [{"name": "read_file", "arguments": {"path": f"page{i}.txt"}}]}
                              for i in range(12)] + [{"content": "All read."}, {"content": "Pages"}]
    run = await finish(app, await say(signed_in, c["id"], "Read every page"))
    assert run.status == "done", run.events[-1]
    main = [b for b in fakes.llama["requests"] if "short title" not in last_user(b)]
    assert len(main) == 13
    for body in main:
        assert C.estimate_tokens(body["messages"]) + C.schema_tokens(body.get("tools")) <= 14000
    last = main[-1]["messages"]
    assert "page 11" in last[-1]["content"]                                   # the newest result is whole
    assert any("shortened by" in (m.get("content") or "") for m in last if m["role"] == "assistant")


# ---------------------------------------------------------------- the real token count

def real_tokens(body: dict, dense: float = 1.0, hex_cost: float = 1.0) -> int:
    """What the fake model server counts for a request: `dense` tokens per estimated token, and a tool result that
    holds a hex dump (HEX) `hex_cost` times that - Qwen's tokenizer reads hex at far fewer characters a token."""
    from app.agent import context as C
    n = C.schema_tokens(body.get("tools")) * dense
    for m in body["messages"]:
        k = C.estimate_tokens([m]) * dense
        n += k * hex_cost if m["role"] == "tool" and "HEX" in (m.get("content") or "") else k
    return int(n)


def counting(ctx: int, replies: list[dict], sizes: list, **cost):
    """A responder that reports the real token count and refuses a request too long for the context, in
    llama-server's words. `sizes` collects (real, refused) for each request but the titles."""
    queue = list(replies)

    def responder(body):
        if "short title" in last_user(body):
            return {"content": "Title"}
        real = real_tokens(body, **cost)
        if real > ctx:
            sizes.append((real, True))
            return {"error": f"request ({real} tokens) exceeds the available context size ({ctx} tokens), "
                             "try increasing it"}
        sizes.append((real, False))
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return {**reply, "usage": {"prompt_tokens": real, "completion_tokens": 50, "total_tokens": real + 50}}
    return responder


async def test_dense_text_is_fitted_by_the_real_token_count(signed_in, app, fakes, monkeypatch):
    """Chat 74: every character of its hex dumps cost more tokens than the estimate says (62,286 real for 45,004
    estimated), so it was never condensed and ran out of room. The last step's real count scales the estimate."""
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 14000)
    c = await new_chat(signed_in)
    wd = hub.extras["agent"].workdir(chats.get_chat(c["id"]))
    for i in range(10):
        wd.write(f"dump{i}.txt", f"dump {i}\n" + "0f a3 " * 1000 + "\n", author="test", origin="test", message="dump")
    sizes: list = []
    fakes.llama["responder"] = counting(14000, [
        *({"content": f"Dump {i} read.", "tool_calls": [{"name": "read_file", "arguments": {"path": f"dump{i}.txt"}}]}
          for i in range(10)), {"content": "All read."}], sizes, dense=1.6)
    run = await finish(app, await say(signed_in, c["id"], "Read every dump"))
    assert run.status == "done", (run.events[-1], sizes)
    assert len(sizes) == 11 and not any(refused for _, refused in sizes), sizes
    model_steps = [s for s in chats.runs_for_chat(c["id"])[0]["steps"] if s["type"] == "model"]
    assert "token_ratio" not in model_steps[0]["sent"]                       # nothing measured yet
    assert all(abs(s["sent"]["token_ratio"] - 1.6) < 0.05 for s in model_steps[1:]), [s["sent"] for s in model_steps]


async def test_a_request_the_model_server_refuses_as_too_long_is_fitted_again(signed_in, app, fakes, monkeypatch):
    """Three hex dumps arrive in one step: the last step measured plain text, so the estimate says the next request
    fits. The model server refuses it with its real size; that size sets the ratio and the request is shortened."""
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 14000)
    c = await new_chat(signed_in)
    wd = hub.extras["agent"].workdir(chats.get_chat(c["id"]))
    for i in range(3):
        wd.write(f"hex{i}.txt", f"HEX {i}\n" + "0f a3 " * 800 + "\n", author="test", origin="test", message="hex")
    sizes: list = []
    fakes.llama["responder"] = counting(14000, [
        {"content": "Reading the three dumps.",
         "tool_calls": [{"name": "read_file", "arguments": {"path": f"hex{i}.txt"}} for i in range(3)]},
        {"content": "The dumps differ at byte 4."}], sizes, hex_cost=3.0)
    run = await finish(app, await say(signed_in, c["id"], "Compare the dumps"))
    assert run.status == "done", (run.events[-1], sizes)
    assert [refused for _, refused in sizes] == [False, True, False], sizes
    steps = chats.runs_for_chat(c["id"])[0]["steps"]
    refit = next(s for s in steps if s["type"] == "refit")
    assert refit["tokens"] == sizes[1][0] and refit["step"] == 2
    last = [b for b in fakes.llama["requests"] if "short title" not in last_user(b)][-1]["messages"]
    assert "HEX 2" in last[-1]["content"] and "0f a3 " * 800 in last[-1]["content"]   # the newest result is whole
    assert texts(run).endswith("The dumps differ at byte 4.")


async def test_a_new_turn_starts_from_the_last_turns_measure(signed_in, app, fakes, monkeypatch):
    """The first request of a turn has nothing measured in this turn: the chat's last model step's ratio is used,
    so a chat of dense text is condensed before it is sent - not refused first."""
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 20000)
    c = await new_chat(signed_in)
    for i in range(3):
        chats.add_message(c["id"], "user", [{"type": "text", "text": f"Dump {i}: " + "0f a3 " * 650}])
        chats.add_message(c["id"], "assistant", [{"type": "text", "text": f"Noted dump {i}: " + "1b 2c " * 650}])
    chats.run_started("run_before", c["id"], "qwen3.8-27b")
    chats.run_finished("run_before", "done", [{"type": "model", "step": 1, "prompt_tokens": 8000,
                                              "sent": {"estimated_tokens": 4000}}], {})
    sizes: list = []
    plain = counting(20000, [{"content": "Fine."}], sizes, dense=2.0)

    def responder(body):
        if "Condense the conversation" in last_user(body):
            sizes.append((real_tokens(body, dense=2.0), "condense"))
            return {"content": "SUMMARY: three dumps."}
        return plain(body)
    fakes.llama["responder"] = responder
    run = await finish(app, await say(signed_in, c["id"], "And the fourth?"))
    assert run.status == "done", (run.events[-1], sizes)
    assert [kind for _, kind in sizes] == ["condense", False] and sizes[0][0] <= 20000, sizes
    assert sizes[1][0] <= 20000 * 0.75, sizes
    assert any(e["type"] == "compacted" for e in run.events)
    assert chats.get_chat(c["id"])["summary"] == "SUMMARY: three dumps."


async def test_a_server_with_less_context_than_the_settings_say_is_believed(signed_in, app, fakes, monkeypatch):
    """Context per chat raised in Settings while the model is loaded: llama-server keeps its old size until it is
    loaded again. Its refusal names that size, and the turn fits itself to it."""
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 30000)      # what Settings say now
    c = await new_chat(signed_in)
    wd = hub.extras["agent"].workdir(chats.get_chat(c["id"]))
    for i in range(6):
        wd.write(f"page{i}.txt", f"page {i}\n" + "w" * 9000 + "\n", author="test", origin="test", message="page")
    sizes: list = []
    fakes.llama["responder"] = counting(14000, [                                        # what the server has
        *({"content": f"Page {i} read.", "tool_calls": [{"name": "read_file", "arguments": {"path": f"page{i}.txt"}}]}
          for i in range(6)), {"content": "All read."}], sizes)
    run = await finish(app, await say(signed_in, c["id"], "Read every page"))
    assert run.status == "done", (run.events[-1], sizes)
    refused = [n for n, (_, r) in enumerate(sizes) if r]
    assert len(refused) == 1 and all(real <= 14000 for real, r in sizes[refused[0] + 1:]), sizes
    refit = next(s for s in chats.runs_for_chat(c["id"])[0]["steps"] if s["type"] == "refit")
    assert refit["context"] == 14000


async def test_no_room_to_condense_leaves_the_history_alone(signed_in, app, fakes, monkeypatch):
    """A summary so long that the request to condense more could take in nothing: the old messages are not folded
    into it unread (they would be lost), the turn is shortened instead."""
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 16000)
    c = await new_chat(signed_in)
    for i in range(3):
        chats.add_message(c["id"], "user", [{"type": "text", "text": f"Question {i}: " + "x" * 3000}])
        chats.add_message(c["id"], "assistant", [{"type": "text", "text": f"Answer {i}: " + "y" * 3000}])
    chats.update_chat(c["id"], summary="S" * 22000, summary_upto=0)
    chats.run_started("run_before", c["id"], "qwen3.8-27b")
    chats.run_finished("run_before", "done", [{"type": "model", "step": 1, "prompt_tokens": 9000,
                                              "sent": {"estimated_tokens": 6000}}], {})
    asked = []

    def responder(body):
        asked.append(last_user(body))
        return {"content": "Fine."}
    fakes.llama["responder"] = responder
    await finish(app, await say(signed_in, c["id"], "And now?"))
    assert not any("Condense the conversation" in a for a in asked)
    chat = chats.get_chat(c["id"])
    assert chat["summary"] == "S" * 22000 and not chat["summary_upto"]


# ---------------------------------------------------------------- skills and memory

async def test_memory_files_are_listed_in_the_prompt(signed_in, app, fakes):
    r = await signed_in.put("/hub/api/memory/file", json={"path": "/memories/user.md",
                                                         "content": "# Prefers short answers\nDanish or English."})
    assert r.status_code == 200, r.text
    assert (await signed_in.get("/hub/api/memory")).json()[0]["path"] == "/memories/user.md"
    fakes.llama["replies"] = [{"content": "ok"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "hi"))
    assert "/memories/user.md: Prefers short answers" in fakes.llama["requests"][0]["messages"][0]["content"]
    assert (await signed_in.delete("/hub/api/memory/file", params={"path": "/memories/user.md"})).status_code == 200


async def test_artifacts_are_versioned_and_can_be_saved(signed_in, app, fakes):
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "artifact", "arguments": {"action": "create", "id": "page", "title": "Page",
                                                           "type": "html", "content": "<h1>v1</h1>"}}]},
        {"tool_calls": [{"name": "artifact", "arguments": {"action": "update", "id": "page",
                                                           "old_str": "v1", "new_str": "v2"}}]},
        {"content": "Made a page."}, {"content": "Page"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "make a page"))
    assert [e["data"]["version"] for e in run.events if e["type"] == "artifact"] == [1, 2]
    a = (await signed_in.get(f"/hub/api/chats/{c['id']}/artifacts/page")).json()
    assert a["content"] == "<h1>v2</h1>" and a["latest"] == 2
    old = (await signed_in.get(f"/hub/api/chats/{c['id']}/artifacts/page", params={"version": 1})).json()
    assert old["content"] == "<h1>v1</h1>"
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/artifacts/page/save", json={"path": "site/index.html"})
    assert r.json() == {"path": "site/index.html"}
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    assert (folder / "site" / "index.html").read_text() == "<h1>v2</h1>"


async def test_deleting_a_chat_removes_its_folder(signed_in, app, fakes):
    fakes.llama["replies"] = [{"tool_calls": [{"name": "write_file", "arguments": {"path": "a.txt", "content": "a"}}]},
                              {"content": "ok"}, {"content": "T"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "write"))
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    assert folder.is_dir()
    assert (await signed_in.delete(f"/hub/api/chats/{c['id']}")).status_code == 200
    assert not folder.exists() and chats.get_chat(c["id"]) is None
    lst = (await signed_in.get("/hub/api/chats")).json()
    assert all(x["id"] != c["id"] for x in lst)


# ---------------------------------------------------------------- handing over to a stronger local model

def _main_models(fakes) -> list[str]:
    return [s["args"][1].rsplit("/", 1)[-1] for s in fakes.sup["starts"] if s["name"] == "main"]


async def test_a_stuck_small_model_hands_over_to_the_strong_one(signed_in, app, fakes):
    """The chat runs on the fast model; when it loops, the turn goes on with
    Qwen3.8 27B (Settings -> Assistant), on the same lease, with its own
    system prompt and a note - and the chat says so."""
    hub = app.state.hub
    hub.settings.update("models", {"default": "qwen3.6-35b-a3b"})

    def responder(body):
        if "short title" in last_user(body):
            return {"content": "Biler"}
        if body["model"] == "qwen3.8-27b":
            return {"content": "Her er sammenligningen, med kilder."}
        return {"content": "Lad mig hente tallene.", "reasoning": "fetch again",
                "tool_calls": [{"name": "web_search", "arguments": {"query": "xc60 co2"}}]}
    fakes.llama["responder"] = responder
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "XC60 mod Tesla?"))
    assert run.status == "done", run.events[-1]
    small = [b for b in fakes.llama["requests"] if b["model"] == "qwen3.6-35b-a3b"]
    big = [b for b in fakes.llama["requests"] if b["model"] == "qwen3.8-27b" and "short title" not in last_user(b)]
    assert len(small) == 3 and len(big) == 1
    esc = next(t for t in small[0]["tools"] if t["function"]["name"] == "escalate")["function"]["description"]
    assert "Qwen3.8 27B, a stronger model on this server" in esc
    assert "Qwen3.8 27B, a stronger model on this server takes over" in small[0]["messages"][0]["content"]
    first = big[0]
    assert "You run as Qwen3.8 27B" in first["messages"][0]["content"]
    assert "handed over to you by Qwen3.6 35B-A3B" in first["messages"][0]["content"]
    assert "handed this turn over to you because the same step (web_search) 3 times" in first["messages"][-1]["content"]
    assert not any("reasoning_content" in m for m in first["messages"])            # not the other model's thinking
    assert "escalate" not in {t["function"]["name"] for t in first["tools"]}      # nobody stronger left (no cloud)
    assert _main_models(fakes) == ["Qwen3.6-35B-A3B-UD-IQ4_XS.gguf", "Qwen3.8-27B-UD-Q4_K_M.gguf"]
    esc_ev = next(e for e in run.events if e["type"] == "escalated")["data"]
    assert esc_ev == {"model": "Qwen3.8 27B", "model_id": "qwen3.8-27b", "reason": "the same step (web_search) 3 times",
                      "local": True}
    steps = chats.runs_for_chat(c["id"])[0]["steps"]
    assert {"type": "escalated", "model": "qwen3.8-27b", "from": "qwen3.6-35b-a3b",
            "reason": "the same step (web_search) 3 times", "local": True} in steps
    assert not any(s["type"] == "loop" for s in steps)                             # handed over, not forced
    answer = chats.messages(c["id"])[-1]["content"]
    assert answer[0] == {"type": "notice", "text": "Handed over from Qwen3.6 35B-A3B to Qwen3.8 27B: the same step "
                                                   "(web_search) 3 times."}
    assert answer[-1] == {"type": "text", "text": "Her er sammenligningen, med kilder."}
    prompts = chats.prompts_for_chat(c["id"])
    assert [(p["reason"], p["model"]) for p in prompts] == [("start", "qwen3.6-35b-a3b"), ("handed over", "qwen3.8-27b")]
    assert "web_search" in prompts[0]["tools"] and "escalate" in prompts[0]["tools"]
    assert "handed over to you by Qwen3.6 35B-A3B" in prompts[1]["system"]

    # The same system prompt is stored once however many turns use it, and goes with the last of them.
    from app import db
    fakes.llama["responder"] = None
    fakes.llama["replies"] = [{"content": "Ja."}]
    hub.settings.update("models", {"default": "qwen3.8-27b"})
    await finish(app, await say(signed_in, c["id"], "Tak"))
    fakes.llama["replies"] = [{"content": "Ja igen."}]
    await finish(app, await say(signed_in, c["id"], "Tak igen"))
    prompts = chats.prompts_for_chat(c["id"])
    assert len(prompts) == 4 and prompts[3]["system"] is None and prompts[3]["same_as"] == prompts[2]["id"]
    with db.connect() as con:
        texts = con.execute("SELECT COUNT(*) FROM prompt_texts").fetchone()[0]
    assert texts == 3                                     # start, handed over, and the plain turns' prompt once
    assert (await signed_in.delete(f"/hub/api/chats/{c['id']}")).status_code == 200
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM run_prompts").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM prompt_texts").fetchone()[0] == 0


async def test_failing_calls_and_the_escalate_tool_hand_over_too(signed_in, app, fakes):
    hub = app.state.hub
    hub.settings.update("models", {"default": "qwen3.6-35b-a3b"})
    n = {"i": 0}

    def responder(body):
        if "short title" in last_user(body):
            return {"content": "Fil"}
        if body["model"] == "qwen3.8-27b":
            return {"content": "Done by the big one."}
        n["i"] += 1
        return {"tool_calls": [{"name": "read_file", "arguments": {"path": f"missing{n['i']}.txt"}}]}
    fakes.llama["responder"] = responder
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "read the files"))
    assert next(e for e in run.events if e["type"] == "escalated")["data"]["reason"] == "three tool calls in a row failed"
    assert texts(run).endswith("Done by the big one.")

    fakes.llama["requests"] = []
    fakes.llama["responder"] = lambda body: ({"content": "Big answer."} if body["model"] == "qwen3.8-27b" else
                                             {"tool_calls": [{"name": "escalate", "arguments": {"reason": "needs a real proof"}}]})
    run = await finish(app, await say(signed_in, c["id"], "prove it"))
    esc = next(e for e in run.events if e["type"] == "escalated")["data"]
    assert esc["reason"] == "needs a real proof" and esc["model_id"] == "qwen3.8-27b"
    result = [b for m in chats.messages(c["id"]) if m["role"] == "tool" for b in m["content"]][-1]
    assert "Handing this turn over to Qwen3.8 27B" in result["text"]


async def test_nobody_to_hand_over_to_means_an_answer_without_tools(signed_in, app, fakes):
    hub = app.state.hub
    hub.settings.update("models", {"default": "qwen3.6-35b-a3b"})
    hub.settings.update("agent", {"escalate_to": ""})
    fakes.llama["responder"] = lambda body: (
        {"content": "Kort svar."} if not body.get("tools") else
        {"content": "Igen.", "tool_calls": [{"name": "web_search", "arguments": {"query": "x"}}]})
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "søg"))
    assert not any(e["type"] == "escalated" for e in run.events)
    assert any(e["type"] == "loop" for e in run.events) and texts(run).endswith("Kort svar.")
    assert all(b["model"] == "qwen3.6-35b-a3b" for b in fakes.llama["requests"])
    assert "escalate" not in {t["function"]["name"] for t in fakes.llama["requests"][0]["tools"]}


async def test_local_first_then_the_cloud(signed_in, app, fakes):
    """In a project with cloud escalation: the small model's loop goes to
    Qwen3.8 27B, and when that one keeps failing, to the cloud."""
    hub = app.state.hub
    hub.settings.update("models", {"default": "qwen3.6-35b-a3b"})
    c = await _cloud_project(signed_in, app)
    n = {"i": 0}

    def responder(body):
        if "short title" in last_user(body):
            return {"content": "Svær"}
        n["i"] += 1
        if body["model"] == "qwen3.8-27b":
            return {"tool_calls": [{"name": "read_file", "arguments": {"path": f"nope{n['i']}.txt"}}]}
        return {"content": "Same.", "tool_calls": [{"name": "web_search", "arguments": {"query": "x"}}]}
    fakes.llama["responder"] = responder
    fakes.cloud["replies"] = [{"content": "The cloud finished it."}]
    run = await finish(app, await say(signed_in, c["id"], "hard task"))
    assert run.status == "done", run.events[-1]
    escalations = [e["data"] for e in run.events if e["type"] == "escalated"]
    assert [(e["model"], e["reason"]) for e in escalations] == [
        ("Qwen3.8 27B", "the same step (web_search) 3 times"), ("openai:big-model", "three tool calls in a row failed")]
    assert texts(run).endswith("The cloud finished it.")
    assert "# Escalated" in fakes.cloud["requests"][0]["messages"][0]["content"]


# ---------------------------------------------------------------- what each step sent, and the model's own settings

async def test_steps_record_what_was_sent_and_why_it_stopped(signed_in, app, fakes):
    fakes.llama["replies"] = [{"tool_calls": [{"name": "list_files", "arguments": {}}]}, {"content": "Tom mappe."},
                              {"content": "Mappe"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "hvad er der i mappen?"))
    model_steps = [s for s in chats.runs_for_chat(c["id"])[0]["steps"] if s["type"] == "model"]
    first, last = model_steps[0], model_steps[-1]
    assert first["finish"] == "tool_calls" and last["finish"] == "stop"
    sent = first["sent"]
    assert sent["thinking"] is False and sent["sampling"] == {"temperature": 0.7, "top_p": 0.8, "top_k": 20,
                                                              "min_p": 0.0, "presence_penalty": 1.5}
    assert sent["tools"] > 10 and sent["messages"] == 2 and sent["max_tokens"] >= 1024 and sent["estimated_tokens"] > 0
    prompt = chats.prompts_for_chat(c["id"])
    assert len(prompt) == 1 and prompt[0]["reason"] == "start" and "SLQ Hub" in prompt[0]["system"]
    r = await signed_in.get(f"/hub/api/chats/{c['id']}/export")
    import io
    import zipfile
    data = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("chat.json"))
    assert data["prompts"][0]["system"] == prompt[0]["system"]
    assert data["runs"][0]["steps"][1]["sent"]["sampling"]["presence_penalty"] == 1.5


async def test_a_models_own_thinking_and_sampling(signed_in, app, fakes):
    """Settings -> Models: thinking for chats without a choice of their own,
    and sampling values that replace the card's - checked, and resettable."""
    put = lambda body: signed_in.put("/hub/api/models/qwen3.8-27b", json=body, headers={"Origin": "http://hub"})  # noqa: E731
    r = await put({"thinking_mode": "on", "sampling": {"instruct": {"temperature": 0.3, "top_k": "40", "min_p": ""},
                                                       "thinking": {"temperature": 0.9}}})
    assert r.status_code == 200, r.text
    assert r.json()["reloadNeeded"] is False                     # no server change: nothing to reload
    m = r.json()["model"]
    assert m["thinking_mode"] == "on" and m["sampling_own"] is True
    assert m["sampling"]["instruct"] == {"temperature": 0.3, "top_k": 40} and m["sampling_card"]["instruct"]["top_k"] == 20
    fakes.llama["replies"] = [{"content": "Tænkt."}, {"content": "Titel"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "hej"))
    body = fakes.llama["requests"][0]
    assert body["chat_template_kwargs"]["enable_thinking"] is True and body["temperature"] == 0.9   # the thinking values
    fakes.llama["requests"] = []
    await signed_in.patch(f"/hub/api/chats/{c['id']}", json={"settings": {"thinking": False}}, headers={"Origin": "http://hub"})
    await finish(app, await say(signed_in, c["id"], "igen"))
    body = fakes.llama["requests"][0]
    assert body["chat_template_kwargs"]["enable_thinking"] is False                 # the chat's own choice wins
    assert body["temperature"] == 0.3 and body["top_k"] == 40 and "min_p" not in body
    # refused values, then back to the card's
    assert (await put({"sampling": {"instruct": {"temperature": 5}}})).status_code == 400
    assert (await put({"sampling": {"instruct": {"seed": 1}}})).status_code == 400
    assert (await put({"sampling": {"fast": {}}})).status_code == 400
    assert (await put({"thinking_mode": "maybe"})).status_code == 400
    m = (await put({"reset_sampling": True, "thinking_mode": "follow"})).json()["model"]
    assert m["sampling"]["instruct"]["temperature"] == 0.7 and "sampling_own" not in m and "thinking_mode" not in m
