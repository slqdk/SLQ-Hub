"""Tool calls the end of the context cut off, and arguments a model server cannot read back (0.16.2).

Chat 21 (0.16.0): with 48,596 tokens of conversation in a 65,536-token context, the model wrote a whole game into
one write_file call; after 16,940 tokens the context was full and the call ended inside its content. The Hub
answered that step, but kept the cut-off call in the history as it was, and llama-server - which parses every
earlier call to render the chat template - refused the next request with a 500, so the turn failed and so would
every later message in that chat. The fake model server here refuses such a history the same way."""
from __future__ import annotations

import json

import pytest

from app.agent import context as C
from app.chats import store as chats
from app.llm.stream import salvage_cut_write, valid_arguments

from .test_agent import finish, new_chat, say

PAGE = '<!doctype html>\n<html lang="da">\n<p>Æble — ø 😀 "citat"</p>\n</html>\n'


def _cut(full: str, at: str) -> str:
    """The arguments as a model sent them until the context ended, just after `at`."""
    return full[:full.index(at) + len(at)]


def _calls(request: dict) -> list[dict]:
    return [tc for m in request["messages"] for tc in m.get("tool_calls") or []]


def _tool_texts(request: dict) -> list[str]:
    return [m["content"] for m in request["messages"] if m["role"] == "tool"]


async def test_a_write_cut_off_by_the_context_keeps_what_it_had_and_the_chat_goes_on(signed_in, app, fakes):
    full = json.dumps({"path": "game/index.html", "content": PAGE}, ensure_ascii=False)
    raw = _cut(full, "Æble — ø")
    rest = PAGE[PAGE.index("ø") + 1:]
    fakes.llama["replies"] = [
        {"reasoning": "the whole game in one file", "content": "Writing the game:",
         "tool_calls": [{"name": "write_file", "raw": raw}], "finish": "length",
         "usage": {"prompt_tokens": 48596, "completion_tokens": 16940, "total_tokens": 65536}},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "game/index.html", "content": rest,
                                                              "append": True}}]},
        {"content": "The game is in game/index.html."},
        {"content": "Bomberman"},
    ]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Make the game from the .exe"))
    assert run.status == "done", run.events[-1]
    assert not fakes.llama.get("refused")
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    assert (folder / "game" / "index.html").read_text(encoding="utf-8") == PAGE

    second = fakes.llama["requests"][1]
    cut_call = _calls(second)[0]["function"]
    assert cut_call["name"] == "write_file"
    sent = json.loads(cut_call["arguments"])                 # readable, and small: not the whole cut-off text
    assert list(sent) == ["_not_valid_json"] and sent["_not_valid_json"].startswith('{"path": "game/index.html"')
    ctx = app.state.hub.catalog.context_per_request(next(e for e in run.events if e["type"] == "started")["data"]["model"])
    told = _tool_texts(second)[0]
    kept = PAGE[:PAGE.index("ø") + 1]
    assert told.startswith(f"This call was cut off: your answer reached the end of the context ({ctx:,} tokens: "
                           f"48,596 for the conversation, 16,940 that you wrote) after {len(raw):,} characters of its "
                           f"arguments, so it did not finish. What it had is kept: Created game/index.html (")
    assert f"That is the first {len(kept):,} characters of the file, ending with:\n```\n{kept}\n```\n" in told
    assert "Add the rest with write_file and append: true, starting exactly after those characters." in told
    assert "write_file with append: true for each next part" in told
    assert _tool_texts(fakes.llama["requests"][2])[1].startswith("Added ")
    results = [e["data"] for e in run.events if e["type"] == "tool_result"]
    assert [r["ok"] for r in results] == [False, True]       # the cut-off call shows as failed

    # The stored chat goes on: the cut-off call comes back from the history readable (0.16.0 sent it as it was).
    fakes.llama["replies"] = [{"content": "It runs in the browser."}]
    before = len(fakes.llama["requests"])
    run2 = await finish(app, await say(signed_in, c["id"], "Does it work?"))
    assert run2.status == "done", run2.events[-1]
    later = fakes.llama["requests"][before]
    assert all(isinstance(json.loads(tc["function"]["arguments"]), dict) for tc in _calls(later))
    assert not fakes.llama.get("refused")
    stored = [b for m in chats.messages(c["id"]) for b in m["content"] if b.get("type") == "tool_call"]
    assert stored[0]["arguments"] == raw                     # the chat keeps what the model sent, for the record


async def test_a_cut_off_call_that_cannot_be_kept_does_not_run(signed_in, app, fakes):
    """A bash command cut off at the limit for one answer: it did not run, and the model is told why."""
    raw = '{"command": "python3 - <<\'EOF\'\\nprint(1'
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "bash", "raw": raw}], "finish": "length",
         "usage": {"prompt_tokens": 900, "completion_tokens": 32768, "total_tokens": 33668}},
        {"content": "I will do it in smaller steps."},
        {"content": "Steps"},
    ]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Run it"))
    assert run.status == "done", run.events[-1]
    assert not fakes.sandbox.get("execs")                     # nothing ran
    told = _tool_texts(fakes.llama["requests"][1])[0]
    assert told == (f"This call was cut off: your answer reached the limit for one answer (32,768 tokens) after "
                    f"{len(raw)} characters of its arguments, so it did not run. Write a long file in parts of at "
                    "most a few hundred lines: write_file with the first part, then write_file with append: true for "
                    "each next part.")
    assert not fakes.llama.get("refused")


async def test_arguments_that_are_not_json_are_sent_back_readable(signed_in, app, fakes):
    """Not cut off, just not JSON: as before the model hears so - and the next request carries a readable stand-in."""
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "write_file", "raw": '{"path": a.txt, "content": "x"}'}]},
        {"content": "Fixed."},
        {"content": "Fix"},
    ]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Write a.txt"))
    assert run.status == "done", run.events[-1]
    second = fakes.llama["requests"][1]
    assert json.loads(_calls(second)[0]["function"]["arguments"]) == {
        "_not_valid_json": '{"path": a.txt, "content": "x"}'}
    assert _tool_texts(second)[0].startswith("The arguments are not valid JSON (the tool arguments are not valid JSON:")
    assert not fakes.llama.get("refused")


async def test_write_file_appends(signed_in, app, fakes):
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "a.txt", "content": "one\n"}}]},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "a.txt", "content": "two\n", "append": True}}]},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "new.txt", "content": "first", "append": True}}]},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "dir", "content": "x", "append": True}}]},
        {"content": "Done."},
        {"content": "Parts"},
    ]
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    (folder / "dir").mkdir(parents=True)
    run = await finish(app, await say(signed_in, c["id"], "Write it in parts"))
    assert run.status == "done", run.events[-1]
    assert (folder / "a.txt").read_text() == "one\ntwo\n" and (folder / "new.txt").read_text() == "first"
    texts = [e["data"]["text"] for e in run.events if e["type"] == "tool_result"]
    assert texts[1] == "Added 4 bytes to a.txt (now 8 bytes, 3 lines)."
    assert texts[2] == "Created new.txt (5 bytes, 1 lines)."         # append to a missing file creates it
    assert texts[3] == "dir is a folder, not a file"


async def test_a_file_written_in_parts_is_not_replaced_by_a_part(signed_in, app, fakes):
    """Chat 21 (0.20.2): the third part came without append: true and replaced the game - twice in one turn. Now a
    file the turn is writing in parts is not replaced unless the call says replace: true; a file written whole may
    still be written whole again."""
    w = "write_file"
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": w, "arguments": {"path": "game/index.html", "content": "<html>\n"}}]},
        {"tool_calls": [{"name": w, "arguments": {"path": "game/index.html", "content": "<p>part 2</p>\n",
                                                  "append": True}}]},
        {"tool_calls": [{"name": w, "arguments": {"path": "/workspace/chats/{chat}/game/index.html",
                                                  "content": "<p>part 3</p>\n"}}]},
        {"tool_calls": [{"name": w, "arguments": {"path": "game/index.html", "content": "<p>part 3</p>\n",
                                                  "append": True}}]},
        {"tool_calls": [{"name": w, "arguments": {"path": "notes.txt", "content": "one"}}]},
        {"tool_calls": [{"name": w, "arguments": {"path": "notes.txt", "content": "two"}}]},
        {"tool_calls": [{"name": w, "arguments": {"path": "game/index.html", "content": "<html>new</html>\n",
                                                  "replace": True}}]},
        {"content": "Done."},
        {"content": "Parts"},
    ]
    c = await new_chat(signed_in)
    third = fakes.llama["replies"][2]["tool_calls"][0]["arguments"]
    third["path"] = third["path"].format(chat=c["id"])            # the sandbox path, as chat 21's model wrote it
    run = await finish(app, await say(signed_in, c["id"], "Make the game in parts"))
    assert run.status == "done", run.events[-1]
    texts = [e["data"]["text"] for e in run.events if e["type"] == "tool_result"]
    assert texts[2].startswith("Not written: you are writing /workspace/chats/") and "21 bytes there with these 14" \
        in texts[2] and "append: true" in texts[2] and "replace: true" in texts[2]
    assert texts[3] == "Added 14 bytes to game/index.html (now 35 bytes, 4 lines)."
    assert texts[5] == "Overwrote notes.txt (3 bytes, 1 lines)."       # written whole, then whole again: fine
    assert texts[6] == "Overwrote game/index.html (17 bytes, 2 lines)."
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    assert (folder / "game" / "index.html").read_text() == "<html>new</html>\n"
    assert (folder / "notes.txt").read_text() == "two"


def test_the_prompt_and_the_tool_say_to_write_long_files_in_parts():
    from app.agent import prompt
    from app.agent.tools import REGISTRY
    assert "write a long file (more than a few hundred lines) in parts" in prompt.BASE
    t = REGISTRY["write_file"]
    assert "append: true" in t.description and "append" in t.parameters["properties"]


def test_valid_arguments():
    good = '{"path": "a.txt",  "content": "x"}'
    assert valid_arguments(good) is good                       # unchanged: the prompt cache stays valid
    assert valid_arguments(None) == "{}" and valid_arguments("") == "{}"
    assert valid_arguments({"a": "ø"}) == '{"a": "ø"}'
    assert json.loads(valid_arguments('{"path": "a.txt", "content": "x",}')) == {"path": "a.txt", "content": "x"}
    assert json.loads(valid_arguments('[1, 2]')) == {"value": [1, 2]}
    long = '{"content": "' + "x" * 1000
    assert json.loads(valid_arguments(long)) == {"_not_valid_json": long[:300] + "... [713 more characters]"}


@pytest.mark.parametrize("raw, kept", [
    ('{"path": "a.html", "content": "<p>Hej', {"path": "a.html", "content": "<p>Hej"}),
    ('{"path": "a.html", "content": "<p class=\\"x', {"path": "a.html", "content": '<p class="x'}),
    ('{"path": "a.html", "content": "line\\', {"path": "a.html", "content": "line"}),          # \ of an escape
    ('{"path": "a.html", "content": "ø \\u00', {"path": "a.html", "content": "ø "}),           # half a \u
    ('{"path": "a.html", "content": "\\ud83d\\ude00 x \\ud83d', {"path": "a.html", "content": "😀 x "}),
    ('{"path": "a.html", "append": true, "content": "more', {"path": "a.html", "content": "more", "append": True}),
    ('{"path": "a.ht', None),                                   # cut in the path
    ('{"content": "<p>Hej", "path": "a.html', None),            # the path is not whole
    ('{"path": "a.html", "content": "', None),                  # nothing written yet
    ('{"path": "a.html", "mode": "x", "content": "<p>', None),  # something else in it
    ('{"path": "", "content": "<p>', None),
])
def test_what_a_cut_off_write_keeps(raw, kept):
    assert salvage_cut_write(raw) == kept


def test_a_stored_cut_off_call_comes_back_readable():
    """A chat from 0.16.0 with the cut-off call stored: the history sent from it can be read by the model server."""
    rows = [{"id": 1, "role": "user", "content": [{"type": "text", "text": "Make the game"}], "created_at": None},
            {"id": 2, "role": "assistant", "content": [
                {"type": "text", "text": "Writing it:"},
                {"type": "tool_call", "id": "c1", "name": "write_file", "arguments": '{"path": "g.html", "content": "<p'}]},
            {"id": 3, "role": "tool", "content": [{"type": "tool_result", "tool_call_id": "c1", "name": "write_file",
                                                   "text": "The arguments are not valid JSON (...)", "is_error": True}]}]
    msgs = C.to_model_messages(rows)
    call = msgs[1]["tool_calls"][0]["function"]
    assert json.loads(call["arguments"]) == {"_not_valid_json": '{"path": "g.html", "content": "<p'}
    assert msgs[2] == {"role": "tool", "tool_call_id": "c1", "content": "Error: The arguments are not valid JSON (...)"}
