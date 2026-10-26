"""A thinking budget and Stop thinking (0.30.0): chat 78's Qwen3.8 27B thought 12,808 and 13,354 tokens in single
steps, about nine minutes each. llama-server b11783 streams the thinking about a token a piece, and with
reasoning_control on, POST /v1/chat/completions/control forces the request's reasoning_budget_message and the end
of the thinking; the Hub counts the pieces and ends the thinking at the budget the same way Stop thinking does
(measured with a tiny model on that build: docs/HANDOFF.md). The fake llama-server does the same with 9-character
pieces."""
from __future__ import annotations

from app.chats import store as chats
from app.llm import payload as P

from .test_agent import finish, new_chat, say, until

THINKER = {"id": "m", "capabilities": ["chat", "thinking"], "thinking": {"efforts": ["low", "medium"]}}


def streaming(app) -> None:
    """The model's answer arrives as it is written (the default test transport hands it over when it is done)."""
    from .mocks import StreamingASGITransport
    router = app.state.router
    router.transports["llm"] = StreamingASGITransport(router.transports["llm"].app)


def test_the_request_turns_reasoning_control_on():
    out = P.thinking_fields(THINKER, True, "low", control=True)
    assert out["reasoning_control"] is True and out["reasoning_budget_message"] == P.BUDGET_MESSAGE
    assert out["reasoning_effort"] == "low"
    assert "reasoning_budget_tokens" not in out            # the Hub counts: see P.thinking_fields
    for off in (P.thinking_fields(THINKER, False, None, control=True), P.thinking_fields(THINKER, True, None),
                P.thinking_fields({"id": "x", "capabilities": ["chat"]}, True, None, control=True)):
        assert "reasoning_control" not in off               # thinking off, the model API's callers, no thinking
    assert P.thinking_end("plan... " + P.BUDGET_MESSAGE) and not P.thinking_end("plan, then answer")


async def test_a_turn_stops_thinking_at_its_budget(signed_in, app, fakes):
    streaming(app)
    await signed_in.put("/hub/api/settings", json={"section": "agent", "values": {"thinking_in_chats": True,
                                                                                    "thinking_budget": "20"}})
    fakes.llama["replies"] = [{"reasoning": "x" * 900, "pace": 0.002, "content": "Done."}, {"content": "Title"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Think about it"))
    body = fakes.llama["requests"][0]
    assert body["reasoning_control"] is True and "reasoning_budget_tokens" not in body
    assert fakes.llama["controls"] == [{"id": "chatcmpl-1", "action": "reasoning_end"}]     # once, at 20 pieces
    step = next(s for s in chats.runs_for_chat(c["id"])[-1]["steps"] if s.get("type") == "model")
    assert step["sent"]["thinking_budget"] == 20 and step["sent"]["thinking_ended"] == "budget"   # the form saves text
    thought = next(b["text"] for m in chats.messages(c["id"]) for b in m["content"] if b.get("type") == "thinking")
    assert thought.endswith(P.BUDGET_MESSAGE) and len(thought) < 400 + len(P.BUDGET_MESSAGE)    # not all 900
    assert run.status == "done" and chats.messages(c["id"])[-1]["content"][-1]["text"] == "Done."
    assert not any(e["type"] == "thinking_stopped" for e in run.events)        # the user did not press Stop
    # thinking that ends before the budget: nothing is sent, however long the answer after it
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"reasoning": "x" * 90, "pace": 0.002, "content": "y" * 600}]
    await finish(app, await say(signed_in, c["id"], "Short thought"))
    assert len(fakes.llama["controls"]) == 1
    # no limit: nothing is sent
    await signed_in.put("/hub/api/settings", json={"section": "agent", "values": {"thinking_budget": 0}})
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"reasoning": "x" * 900, "pace": 0.001, "content": "Done."}]
    await finish(app, await say(signed_in, c["id"], "Again"))
    body = fakes.llama["requests"][0]
    assert body["reasoning_control"] is True and len(fakes.llama["controls"]) == 1
    step = [s for s in chats.runs_for_chat(c["id"])[-1]["steps"] if s.get("type") == "model"][0]
    assert "thinking_budget" not in step["sent"] and "thinking_ended" not in step["sent"]
    await signed_in.put("/hub/api/settings", json={"section": "agent", "values": {"thinking_in_chats": False}})
    fakes.llama["requests"].clear()
    await finish(app, await say(signed_in, c["id"], "Without thinking"))
    assert "reasoning_control" not in fakes.llama["requests"][0]


async def test_stop_thinking_ends_it_at_once(signed_in, app, fakes):
    streaming(app)
    await signed_in.put("/hub/api/settings", json={"section": "agent", "values": {"thinking_in_chats": True}})
    fakes.llama["replies"] = [{"reasoning": "pondering " * 200, "pace": 0.02, "content": "Here it is."},
                              {"content": "Title"}]
    c = await new_chat(signed_in)
    rid = await say(signed_in, c["id"], "A hard one")
    run = app.state.hub.extras["agent"].runs.get(rid)
    await until(lambda: sum(1 for e in run.events if e["type"] == "thinking") >= 5)
    r = await signed_in.post(f"/hub/api/runs/{rid}/stop-thinking")
    assert r.status_code == 200 and r.json() == {"ok": True}
    await finish(app, rid)
    assert fakes.llama["controls"] == [{"id": "chatcmpl-1", "action": "reasoning_end"}]
    assert any(e["type"] == "thinking_stopped" for e in run.events)
    step = next(s for s in chats.runs_for_chat(c["id"])[-1]["steps"] if s.get("type") == "model")
    assert step["sent"]["thinking_ended"] == "stopped"
    thought = next(b["text"] for m in chats.messages(c["id"]) for b in m["content"] if b.get("type") == "thinking")
    assert thought.endswith(P.BUDGET_MESSAGE) and len(thought) < 1000                     # not all 2,000 characters
    assert (await signed_in.post(f"/hub/api/runs/{rid}/stop-thinking")).status_code == 404     # finished
    assert run.thinking is None
    # the thinking is over and the answer is being written: ok false, nothing sent
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"reasoning": "brief", "pace": 0.01, "content": "A slow answer. " * 8,
                               "content_pace": 0.03}]
    rid = await say(signed_in, c["id"], "And this")
    run = app.state.hub.extras["agent"].runs.get(rid)
    await until(lambda: any(e["type"] == "text" for e in run.events))
    r = await signed_in.post(f"/hub/api/runs/{rid}/stop-thinking")
    assert r.status_code == 200 and r.json() == {"ok": False}
    await finish(app, rid)
    assert len(fakes.llama["controls"]) == 1 and run.thinking is None


async def test_a_stopped_thinking_is_not_ended_again_at_the_budget(signed_in, app, fakes):
    streaming(app)
    await signed_in.put("/hub/api/settings", json={"section": "agent", "values": {"thinking_in_chats": True,
                                                                                    "thinking_budget": 8}})
    fakes.llama["replies"] = [{"reasoning": "pondering " * 100, "pace": 0.03, "content": "Here."},
                              {"content": "Title"}]
    c = await new_chat(signed_in)
    rid = await say(signed_in, c["id"], "A hard one")
    run = app.state.hub.extras["agent"].runs.get(rid)
    await until(lambda: sum(1 for e in run.events if e["type"] == "thinking") >= 2)
    assert (await signed_in.post(f"/hub/api/runs/{rid}/stop-thinking")).json() == {"ok": True}
    await finish(app, rid)
    pieces = sum(1 for e in run.events if e["type"] == "thinking")
    assert pieces > 8                                  # the forced message took it past the budget ...
    assert len(fakes.llama["controls"]) == 1           # ... and only the user's stop was sent
    step = next(s for s in chats.runs_for_chat(c["id"])[-1]["steps"] if s.get("type") == "model")
    assert step["sent"]["thinking_ended"] == "stopped"


async def test_stop_thinking_needs_a_live_answer(signed_in):
    assert (await signed_in.post("/hub/api/runs/run_nope/stop-thinking")).status_code == 404
    assert (await signed_in.post("/hub/api/device/turns/run_nope/stop-thinking")).status_code in (401, 403, 404)
