"""The agent: turns in chats and projects, tools, approvals, stopping, MCP,
escalation, subagents, compaction, attachments - against the fake services."""
from __future__ import annotations

import asyncio
import json
import time

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


def last_user(body: dict) -> str:
    for m in reversed(body["messages"]):
        if m["role"] == "user":
            c = m["content"]
            return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c)
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


async def test_one_turn_per_chat_and_errors_are_reported(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "slow", "delay": 0.5}]
    c = await new_chat(signed_in)
    run_id = await say(signed_in, c["id"], "first")
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/messages", json={"text": "second"})
    assert r.status_code == 409
    await finish(app, run_id)
    fakes.llama["replies"] = [{"error": "model exploded"}]
    run = await finish(app, await say(signed_in, c["id"], "again"))
    assert run.status == "error" and "model exploded" in run.events[-1]["data"]["message"]


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
    assert parts[0] == {"type": "text", "text": "What is this?"}
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
    assert parts[0]["text"].startswith("[File from the working folder: src/main.py (2 lines), as it was")
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
    assert result["data"]["ok"] and result["data"]["images"]
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
    assert next(e for e in run.events if e["type"] == "escalated")["data"]["reason"] == "three tool calls in a row failed"


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


# ---------------------------------------------------------------- skills and memory

async def test_skills_are_offered_loaded_and_can_be_switched_off(signed_in, app, fakes):
    skill = b"---\nname: haiku-writer\ndescription: Write haiku in 5-7-5.\n---\nCount the syllables.\n"
    r = await signed_in.post("/hub/api/skills", files={"file": ("SKILL.md", skill, "text/markdown")})
    assert r.json() == {"name": "haiku-writer"}
    assert (await signed_in.get("/hub/api/skills")).json()[0]["enabled"] is True
    fakes.llama["replies"] = [{"tool_calls": [{"name": "skill", "arguments": {"name": "haiku-writer"}}]},
                              {"content": "Autumn moon..."}, {"content": "Haiku"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "a haiku please"))
    first = fakes.llama["requests"][0]
    assert "haiku-writer: Write haiku in 5-7-5." in first["messages"][0]["content"]
    assert "Count the syllables." in fakes.llama["requests"][1]["messages"][-1]["content"]
    await signed_in.patch("/hub/api/skills/haiku-writer", json={"enabled": False})
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "ok"}]
    await finish(app, await say(signed_in, c["id"], "again"))
    req = fakes.llama["requests"][0]
    assert "haiku-writer" not in req["messages"][0]["content"]
    assert "skill" not in {t["function"]["name"] for t in req["tools"]}
    # A chat can still add it.
    await signed_in.patch(f"/hub/api/chats/{c['id']}", json={"settings": {"skills": ["haiku-writer"]}})
    fakes.llama["requests"].clear()
    await finish(app, await say(signed_in, c["id"], "once more"))
    assert "haiku-writer" in fakes.llama["requests"][0]["messages"][0]["content"]


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
