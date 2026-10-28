"""Question cards (0.43.0): a skill's fixed questions, answered on a card when it is chosen and sent with the
message; and ask_user, the assistant's own card with buttons, which the turn waits for."""
from __future__ import annotations

from app.agent.skills import Skills, parse_questions
from app.chats import store as chats

from .test_agent import finish, last_user, new_chat, say, until
from .test_skills import md

XMAS = """Make the user's picture Christmassy, with the answers to the questions below.

```questions
# the card the chat shows
Hvor mange nisser skal der være på billedet? | 0 | 1 | *2 | 3
Skal der være sne? | Ja | Nej
+ Hvilke farver? | *Rød | Grøn | Guld
One option only | x
```
"""


def test_a_skill_reads_its_questions_from_its_block(tmp_path):
    qs = parse_questions(XMAS)
    assert qs == [
        {"text": "Hvor mange nisser skal der være på billedet?", "options": ["0", "1", "2", "3"], "chosen": ["2"],
         "multi": False},
        {"text": "Skal der være sne?", "options": ["Ja", "Nej"], "chosen": [], "multi": False},
        {"text": "Hvilke farver?", "options": ["Rød", "Grøn", "Guld"], "chosen": ["Rød"], "multi": True}]
    assert parse_questions("no block here") == []
    s = Skills(tmp_path / "skills")
    s.install(None, md("billede-med-juletema", "Christmas pictures.", XMAS), "SKILL.md")
    assert len(s.entry(None, "billede-med-juletema")["questions"]) == 3
    assert s.entry(None, "hub/skill-creator")["questions"] == []


async def test_answers_go_with_the_message_and_the_model_reads_them(signed_in, app, fakes):
    r = await signed_in.post("/hub/api/skills", files={"file": ("SKILL.md", md("billede-med-juletema", "Jul.", XMAS),
                                                                "text/markdown")})
    assert r.status_code == 200, r.text
    c = await new_chat(signed_in)
    await signed_in.patch(f"/hub/api/chats/{c['id']}", json={"settings": {"skills": ["billede-med-juletema"]}})
    fakes.llama["replies"] = [{"content": "Here is your picture."}, {"content": "Jul"}]
    answers = [{"skill": "billede-med-juletema", "title": "billede-med-juletema", "items": [
        {"q": "Hvor mange nisser skal der være på billedet?", "a": ["2"]}, {"q": "Skal der være sne?", "a": "Ja"},
        {"q": "Hvilke farver?", "a": ["Rød", "Guld"]}, {"q": "Unanswered", "a": []}]}]
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/messages", json={"text": "", "answers": answers})
    assert r.status_code == 200, r.text                              # no text: the answers are the message
    await finish(app, r.json()["run_id"])
    block = chats.messages(c["id"])[0]["content"][0]
    assert block["type"] == "answers" and [it["a"] for it in block["items"]] == [["2"], ["Ja"], ["Rød", "Guld"]]
    sent = last_user(fakes.llama["requests"][0])
    assert "[The user's answers to the questions of the skill \"billede-med-juletema\"" in sent
    assert "- Hvilke farver? Rød, Guld" in sent
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "Its questions (the ```questions block) are put to the user on a card in the chat" in system
    assert (await signed_in.post(f"/hub/api/chats/{c['id']}/messages", json={"text": " "})).status_code == 400


async def test_ask_user_shows_a_card_and_the_turn_waits_for_the_answer(signed_in, app, fakes):
    q = {"questions": [{"question": "Which style?", "options": ["Classic", "Modern", "Cartoon"]},
                       {"question": "Which colours?", "options": ["Red", "Green", "Gold"], "multi": True}]}
    fakes.llama["replies"] = [{"tool_calls": [{"name": "ask_user", "arguments": q}]}, {"content": "Classic it is."},
                              {"content": "Style"}]
    c = await new_chat(signed_in)
    run_id = await say(signed_in, c["id"], "Make me a Christmas card")
    run = app.state.hub.extras["agent"].runs.get(run_id)
    await until(lambda: any(e["type"] == "questions_needed" for e in run.events))
    ev = next(e for e in run.events if e["type"] == "questions_needed")["data"]
    assert [x["options"] for x in ev["questions"]] == [["Classic", "Modern", "Cartoon"], ["Red", "Green", "Gold"]]
    assert ev["questions"][1]["multi"] is True
    assert (await signed_in.post(f"/hub/api/runs/{run_id}/answer", json={"call_id": "nope", "answers": []})).status_code == 404
    r = await signed_in.post(f"/hub/api/runs/{run_id}/answer",
                             json={"call_id": ev["id"], "answers": [["Classic"], ["Red", "Gold"]]})
    assert r.status_code == 200
    run = await finish(app, run_id)
    assert run.status == "done" and any(e["type"] == "questions_answered" for e in run.events)
    tool = [m for m in chats.messages(c["id"]) if m["role"] == "tool"][0]["content"][0]
    assert tool["text"] == "The user answered:\n- Which style? Classic\n- Which colours? Red, Gold"
    assert tool["display"] == {"answers": [["Classic"], ["Red", "Gold"]]}
    offered = {t["function"]["name"] for t in fakes.llama["requests"][0]["tools"]}
    assert "ask_user" in offered


async def test_a_message_written_instead_answers_the_card(signed_in, app, fakes):
    q = {"questions": [{"question": "Which style?", "options": ["Classic", "Modern"]}]}
    fakes.llama["replies"] = [{"tool_calls": [{"name": "ask_user", "arguments": q}]}, {"content": "Pink then."},
                              {"content": "Style"}]
    c = await new_chat(signed_in)
    run_id = await say(signed_in, c["id"], "Make me a card")
    run = app.state.hub.extras["agent"].runs.get(run_id)
    await until(lambda: any(e["type"] == "questions_needed" for e in run.events))
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/messages", json={"text": "Neither - make it pink"})
    assert r.status_code == 200 and r.json()["queued"]
    await finish(app, run_id)
    tool = [m for m in chats.messages(c["id"]) if m["role"] == "tool"][0]["content"][0]
    assert "wrote a message instead" in tool["text"]
    assert "Neither - make it pink" in str(fakes.llama["requests"][1]["messages"])


async def test_ask_user_refuses_a_question_without_real_options(signed_in, app, fakes):
    fakes.llama["replies"] = [{"tool_calls": [{"name": "ask_user", "arguments": {"questions": [
        {"question": "What do you want?", "options": ["Something"]}]}}]}, {"content": "OK"}, {"content": "T"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hi"))
    tool = [m for m in chats.messages(c["id"]) if m["role"] == "tool"][0]["content"][0]
    assert tool["is_error"] and "each with 2-6 options" in tool["text"]
