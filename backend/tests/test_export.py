"""Exporting a chat: Markdown to read, a ZIP to find out why a chat behaved
as it did."""
from __future__ import annotations

import io
import json
import os
import zipfile

from app.chats.export import merge_events, slug, strip_images

from .test_agent import finish, new_chat, say


def noisy_png(w: int = 32, h: int = 32) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.frombytes("RGB", (w, h), os.urandom(w * h * 3)).save(buf, "PNG")
    return buf.getvalue()


async def test_markdown_reads_like_the_chat(signed_in, app, fakes):
    fakes.llama["replies"] = [
        {"content": "", "reasoning": "I should look it up",
         "tool_calls": [{"name": "web_search", "arguments": {"query": "vejret i Vojens"}}]},
        {"content": "Det bliver **sol** i morgen.", "reasoning": "the results say sun"},
        {"content": "Vejret i Vojens"},
    ]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hvordan bliver vejret?"))
    r = await signed_in.get(f"/hub/api/chats/{c['id']}/export", params={"format": "md"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    cd = r.headers["content-disposition"]
    assert cd.startswith(f'attachment; filename="chat-{c["id"]}-vejret-i-vojens-') and cd.endswith('.md"')
    md = r.text
    assert md.startswith("# Vejret i Vojens\n")
    assert "## You - " in md and "Hvordan bliver vejret?" in md
    assert "<summary>Thinking</summary>" in md and "I should look it up" in md
    assert "**Tool call** `web_search`" in md and '"query": "vejret i Vojens"' in md
    assert "**Result** `web_search`" in md
    assert "Det bliver **sol** i morgen." in md
    assert "*Turn done: 2 model steps, 1 tool call" in md
    assert md.count("## Assistant") == 1                  # one section per turn, however many steps


async def test_the_bundle_has_everything_but_secrets(signed_in, app, fakes):
    await signed_in.put("/hub/api/settings", json={"section": "cloud", "values": {"api_key": "sk-secret-123456"}},
                        headers={"Origin": "http://hub"})
    photo = noisy_png()
    up = (await signed_in.post("/hub/api/uploads", files={"file": ("kat.png", photo, "image/png")})).json()
    fakes.llama["replies"] = [
        {"content": "", "tool_calls": [{"name": "write_file", "arguments": {"path": "noter.txt", "content": "x" * 5000}}]},
        {"content": "Skrevet.", "reasoning": "wrote it"},
        {"content": "Noter om katten"},
    ]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Se billedet og skriv noter", attachments=[up["sha"]]))
    r = await signed_in.get(f"/hub/api/chats/{c['id']}/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].endswith('.zip"')
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"README.txt", "chat.json", "context.json", "transcript.md", "logs/queue.json", "logs/llama-server.txt",
            f"events/{run.id}.json"} <= names

    data = json.loads(z.read("chat.json"))
    assert data["exported"]["app"] == "SLQ Hub" and data["chat"]["id"] == c["id"]
    assert [m["role"] for m in data["messages"]] == ["user", "assistant", "tool", "assistant"]
    call = data["messages"][1]["content"][-1]
    assert call["type"] == "tool_call" and len(json.loads(call["arguments"])["content"]) == 5000     # whole
    assert data["runs"][0]["status"] == "done" and {s["type"] for s in data["runs"][0]["steps"]} >= {"model", "tool"}
    assert data["model"]["id"] == "qwen3.8-27b" and data["model"]["context_per_request"] > 0
    assert data["settings"]["agent"]["max_steps"] == 60
    assert data["settings"]["cloud"]["api_key_set"] is True and "api_key" not in data["settings"]["cloud"]
    assert data["runs_in_memory"][0]["id"] == run.id
    pic = next(f for f in data["files"] if f["sha"] == up["sha"])
    assert pic["path"].startswith("files/") and pic["path"].endswith("kat.png") and z.read(pic["path"]) == photo

    ctx = json.loads(z.read("context.json"))
    assert ctx["model"] == "qwen3.8-27b" and ctx["thinking"] is False
    assert ctx["messages"][0]["role"] == "system" and "SLQ Hub" in ctx["messages"][0]["content"]
    assert [m["role"] for m in ctx["messages"][1:]] == ["user", "assistant", "tool", "assistant"]
    assert {"read_file", "write_file", "bash", "web_search"} <= {t["name"] for t in ctx["tools"]}
    req = ctx["request_without_messages"]
    assert req["tools"] and req["chat_template_kwargs"] == {"enable_thinking": False} and "messages" not in req
    raw_ctx = z.read("context.json").decode()
    assert "left out" in raw_ctx and "iVBORw0KGgo" not in raw_ctx            # the picture is a note, not data

    events = json.loads(z.read(f"events/{run.id}.json"))["events"]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "queued" and kinds[-1] == "done"
    assert kinds.count("tool_args") == 1                                     # the streamed pieces joined

    jobs = json.loads(z.read("logs/queue.json"))
    assert any(j["owner"] == "web" and j["kind"] == "agent" for j in jobs)

    md = z.read("transcript.md").decode()
    assert f"![kat.png]({pic['path']})" in md and "(5000 characters)" in md

    everything = b"".join(z.read(n) for n in names)
    assert b"sk-secret-123456" not in everything


async def test_export_of_an_unknown_chat_or_format(signed_in):
    assert (await signed_in.get("/hub/api/chats/9999/export")).status_code == 404
    c = await new_chat(signed_in)
    assert (await signed_in.get(f"/hub/api/chats/{c['id']}/export", params={"format": "pdf"})).status_code == 400


async def test_export_needs_a_signed_in_browser(client):
    assert (await client.get("/hub/api/chats/1/export")).status_code == 401


def test_streamed_pieces_are_joined_per_kind_and_call():
    ev = lambda seq, kind, **data: {"seq": seq, "type": kind, "t": seq / 10, "data": data}   # noqa: E731
    out = merge_events([ev(1, "step", step=1), ev(2, "thinking", text="a"), ev(3, "thinking", text="b"),
                        ev(4, "text", text="Hej"), ev(5, "text", text=" der"),
                        ev(6, "tool_start", id="c1", name="bash"), ev(7, "tool_args", id="c1", delta='{"co'),
                        ev(8, "tool_args", id="c1", delta='mmand"}'), ev(9, "tool_args", id="c2", delta="{}"),
                        ev(10, "text", text="x", depth=1, parent="c3"), ev(11, "text", text="y")])
    assert [(e["type"], e["data"].get("text") or e["data"].get("delta")) for e in out] == [
        ("step", None), ("thinking", "ab"), ("text", "Hej der"), ("tool_start", None), ("tool_args", '{"command"}'),
        ("tool_args", "{}"), ("text", "x"), ("text", "y")]
    assert out[1]["seq"] == 2 and out[1]["seq_to"] == 3 and out[1]["pieces"] == 2


def test_names_and_pictures_left_out():
    assert slug("Mario spil på hjemmesiden!") == "mario-spil-paa-hjemmesiden"
    assert slug("") == "chat"
    big = "data:image/png;base64," + "A" * 4000
    assert strip_images({"a": [big, "data:x,short"]}) == {"a": ["data:image/png;base64,... (3.0 KB picture, left out)",
                                                                "data:x,short"]}
