"""What chat 335 taught the agent (0.105.0): the Hub's references come through whole, a check that gives back the
same after a change is said - and stops the turn the third time - and a stuck turn is not handed to a smaller model."""
from __future__ import annotations

from app.catalog import Catalog

from .test_agent import finish, new_chat, say


def _out(text: str) -> dict:
    return {"exit_code": 0, "stdout": text, "stderr": "", "timed_out": False, "duration_ms": 5, "truncated": False}


async def test_hub3d_format_comes_through_whole(signed_in, app, fakes):
    """hub3d format's 13.8k characters lost 2.4k from their middle to the 12,000 limit: the references the prompt
    sends the model to come through whole (up to 24,000); other output is shortened as before."""
    guide = "".join(f"line {i:05d} of the guide\n" for i in range(700))       # 16,800 characters
    fakes.sandbox["handler"] = lambda body: _out(guide)
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "bash", "arguments": {"about": "Read the format", "command": "hub3d format"}}]},
        {"tool_calls": [{"name": "bash", "arguments": {"about": "Show it", "command": "cat guide.txt"}}]},
        {"content": "Read."}, {"content": "Format"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Read the 3D format"))
    assert run.status == "done", run.events[-1]
    tools = [m["content"] for m in fakes.llama["requests"][2]["messages"] if m["role"] == "tool"]
    assert guide in tools[0] and "characters omitted" not in tools[0] and ".hub/outputs/" not in tools[0]
    assert "characters omitted" in tools[1] and guide not in tools[1]


async def test_the_same_result_after_a_change_is_said_then_it_answers(signed_in, app, fakes):
    """A check that gives back what it gave before the last change is told so - the picture's new name in the call
    left out; the second time it is told to ask; the third time the turn is stuck, and with nobody to hand it to it
    answers. The same check twice with no change between is only a look again."""
    fakes.sandbox["handler"] = lambda body: _out(f"size 20.0 x 20.0 x 20.0 mm\nWrote {body['cmd'].rsplit(' ', 1)[-1]}\n")

    def check(n: int) -> dict:
        return {"tool_calls": [{"name": "bash", "arguments": {
            "about": "Check the design", "command": f"hub3d design d.3d.json --views out/v{n}.png"}}]}

    def edit(n: int) -> dict:
        return {"tool_calls": [{"name": "write_file", "arguments": {"path": "d.3d.json", "content": f'{{"v": {n}}}'}}]}

    fakes.llama["replies"] = [check(1), check(2), edit(1), check(3), edit(2), check(4), edit(3), check(5),
                              {"content": "The size stays 20 mm whatever I change - I could not find why."},
                              {"content": "Hul"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Lav et hul i bunden"))
    assert run.status == "done", run.events[-1]
    said = [m["content"] for m in fakes.llama["requests"][-2]["messages"] if m["role"] == "tool"
            and "size 20.0" in m["content"]]
    assert len(said) == 5
    assert "SLQ Hub" not in said[0] and "SLQ Hub" not in said[1]         # no change between: only a look again
    assert ('this is exactly what the command "hub3d design d.3d.json --views out/v2.png" gave back before your last '
            "change - the change made no difference to it") in said[2] and "twice" not in said[2]
    assert "That is twice this turn: if you cannot see why, tell the user what you found and ask." in said[3]
    assert "made no difference" in said[4]
    kinds = [e["type"] for e in run.events]
    assert kinds.count("same_again") == 3
    loop = next(e["data"] for e in run.events if e["type"] == "loop")
    assert loop["reason"] == "a check gave back the same as before a change 3 times"
    assert not fakes.llama["requests"][-2].get("tools")                    # its last step only answers
    from app.chats import store as chats
    notices = [b["text"] for m in chats.messages(c["id"]) if m["role"] == "assistant" for b in m["content"]
               if b["type"] == "notice"]
    assert any(n.startswith("It was told a check gave back the same as before its last change") for n in notices)


async def test_a_stuck_turn_is_not_handed_to_a_smaller_model(signed_in, app, fakes):
    """Chat 335's gemma 4 31B was told a 9B model was "a stronger model on this server": a smaller model is not
    handed to - no escalate tool, no word of one - and one of the same size is "another model"."""
    hub = app.state.hub
    hub.settings.update("agent", {"escalate_to": "qwen3.6-35b-a3b"})      # 35B-A3B: about 10B, under Qwen3.8 27B
    fakes.llama["replies"] = [{"content": "Hej."}, {"content": "Hilsen"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Hej"))
    assert run.status == "done", run.events[-1]
    first = fakes.llama["requests"][0]
    assert "escalate" not in {t["function"]["name"] for t in first.get("tools") or []}
    assert "takes over" not in first["messages"][0]["content"]


def test_which_model_is_the_stronger():
    assert Catalog.strength({"parameter_size": "30.7B"}) == 30.7
    assert Catalog.strength({"parameter_size": "27B"}) == 27
    assert 10 < Catalog.strength({"parameter_size": "35B-A3B"}) < 10.5          # a mixture: all and active, meaned
    assert Catalog.strength({"id": "qwen3.5-9b-uncensored-hauhaucs-aggressive"}) == 9
    assert Catalog.strength({"id": "qwen3.6-35b-a3b"}) == Catalog.strength({"parameter_size": "35B-A3B"})
    assert Catalog.strength({"id": "ornith-1.5-9b-2", "name": "Ornith 1.5 Lightweight"}) == 9
    assert Catalog.strength({"id": "mystery", "name": "Mystery"}) is None
