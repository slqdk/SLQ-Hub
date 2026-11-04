"""0.79.1: the start of another call leaked into a call's arguments (Gemma 4, chat 292 on 2026-11-04).

Laura's task "Søndag morgen sengefoto" made its picture and then found no file to hand in, twice in a row: Gemma 4
31B closed the save_as string with a backtick instead of its <|"|>, llama-server read on into the next call, and the
picture was saved as 'sunday_morning_news_vibe.jpg`}<tool_call|><|tool_call>call:post_draft{media:['. post_draft
asked for sunday_morning_news_vibe.jpg three times and the turn gave up. The next run copied the broken call from
the chat's history - the strings below are from his export."""
from __future__ import annotations

import json

from app.agent import context as C
from app.llm.stream import Accumulator, clean_arguments, clean_result_text, cut_leak, valid_arguments

from .test_loras import ORIGIN
from .test_persona_tasks import MONDAY_POST, done, ok, with_lora
from .test_personas import API

FIRST = "sunday_morning_news_vibe.jpg`}<tool_call|><|tool_call>call:post_draft{media:["
SECOND = "sunday_morning_cozy_vibe.jpg`}<tool_call|><|tool_call>call:post_draft{media:["
RESULT = ("Generated 1 image(s) with FLUX.2 [klein] 9B base with the LoRAs instalorry (step 4000), amateur_selfie "
          "(1024x1024 seed 640314206). They are shown to the user and below, with a line saying what made them. Saved "
          f"as {SECOND} - to change a picture further, edit that file.")


def test_the_leaked_start_of_a_call_is_cut_from_an_argument():
    assert cut_leak(FIRST) == "sunday_morning_news_vibe.jpg"
    assert cut_leak(SECOND) == "sunday_morning_cozy_vibe.jpg"
    assert cut_leak('photo.jpg"}<|tool_call>call:view_image{path:') == "photo.jpg"
    for fine in ("sunday_morning_news_vibe.jpg", "a tool_call in plain words", "<tool_call>{\"name\": 1}", ""):
        assert cut_leak(fine) == fine                        # nothing of Gemma 4's markers: left as it is
    args = {"me": True, "seed": 7, "prompt": "A cozy bed", "save_as": SECOND, "media": [FIRST, "b.jpg"],
            "nested": {"x": FIRST}}
    assert clean_arguments(args) == ({"me": True, "seed": 7, "prompt": "A cozy bed",
                                      "save_as": "sunday_morning_cozy_vibe.jpg",
                                      "media": ["sunday_morning_news_vibe.jpg", "b.jpg"],
                                      "nested": {"x": "sunday_morning_news_vibe.jpg"}}, True)
    assert clean_arguments({"prompt": "x"}) == ({"prompt": "x"}, False)
    assert clean_result_text(RESULT).endswith("Saved as sunday_morning_cozy_vibe.jpg - to change a picture further, "
                                              "edit that file.")
    assert clean_result_text("Saved as a.jpg.") == "Saved as a.jpg."


def test_the_call_read_from_the_model_server_comes_out_whole():
    acc = Accumulator()
    raw = json.dumps({"me": True, "prompt": "A cozy bed", "save_as": FIRST}, ensure_ascii=False)
    for i, piece in enumerate((raw[:30], raw[30:90], raw[90:])):        # streamed in pieces, as llama-server does
        acc.feed({"choices": [{"delta": {"tool_calls": [{"index": 0, **({"id": "c1"} if i == 0 else {}), "function": {
            **({"name": "image_generate"} if i == 0 else {}), "arguments": piece}}]}}]})
    acc.feed({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
    call = acc.result()["tool_calls"][0]
    assert json.loads(call["arguments"]) == {"me": True, "prompt": "A cozy bed",
                                             "save_as": "sunday_morning_news_vibe.jpg"}


def test_a_broken_call_stored_in_a_chat_is_not_shown_to_the_model_again():
    """Chat 292's two runs, stored before 0.79.1: the history its next run is sent has neither the broken name in the
    call nor in the result - the model copied it the time before."""
    good = '{"prompt": "A cozy bed", "save_as": "a.jpg"}'
    rows = [{"id": 1, "role": "user", "content": [{"type": "text", "text": "Scheduled task"}], "created_at": None},
            {"id": 2, "role": "assistant", "content": [
                {"type": "tool_call", "id": "c1", "name": "image_generate",
                 "arguments": json.dumps({"me": True, "prompt": "A cozy bed", "save_as": SECOND})},
                {"type": "tool_call", "id": "c2", "name": "image_generate", "arguments": good}]},
            {"id": 3, "role": "tool", "content": [
                {"type": "tool_result", "tool_call_id": "c1", "name": "image_generate", "text": RESULT},
                {"type": "tool_result", "tool_call_id": "c2", "name": "image_generate", "text": "Saved as a.jpg."}]}]
    msgs = C.to_model_messages(rows)
    calls = msgs[1]["tool_calls"]
    assert json.loads(calls[0]["function"]["arguments"])["save_as"] == "sunday_morning_cozy_vibe.jpg"
    assert calls[1]["function"]["arguments"] is good                          # untouched: the prompt cache holds
    assert "<|tool_call>" not in json.dumps(msgs) and "<tool_call|>" not in json.dumps(msgs)
    assert "Saved as sunday_morning_cozy_vibe.jpg - to change" in msgs[2]["content"]
    assert valid_arguments({"save_as": FIRST}) == '{"save_as": "sunday_morning_news_vibe.jpg"}'


async def test_the_task_hands_in_its_post_when_the_model_slips(signed_in, app, fakes):
    """The run of chat 292 again, with the fix: the picture is saved under the name meant, and post_draft finds it."""
    p, _lid = await with_lora(signed_in, app)
    pid, store = p["id"], app.state.hub.extras["personas"]
    d = await ok(await signed_in.post(f"{API}/{pid}/tasks", json=MONDAY_POST, headers=ORIGIN))
    tid = d["tasks"][0]["id"]
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {"me": True, "prompt": "A cozy bed in the morning",
                                                                 "save_as": SECOND}}]},
        {"tool_calls": [{"name": "post_draft", "arguments": {"text": "Søndag morgen...",
                                                             "media": ["sunday_morning_cozy_vibe.jpg"]}}]},
        {"content": "Færdig!"},
    ]
    due = store.get(pid)["tasks"][0]["next_run"]
    assert await store.tick(now=due + 60) == [f"{pid}/{tid}"]
    await done(app, pid, tid)
    t = store.get(pid)["tasks"][0]
    assert t["last_error"] == "" and t["last_post"], t
    post = (await ok(await signed_in.get(f"{API}/{pid}/posts")))["posts"][0]
    assert [m["kind"] for m in post["media"]] == ["picture"]
    pic = await signed_in.get(f"{API}/{pid}/file", params={"path": post["media"][0]["file"]})
    assert pic.status_code == 200 and pic.content[:2] in (b"\xff\xd8", b"\x89P")    # the picture itself
    sent = json.dumps(fakes.llama["requests"][1]["messages"], ensure_ascii=False)
    assert "<|tool_call>" not in sent and "Saved as sunday_morning_cozy_vibe.jpg" in sent
