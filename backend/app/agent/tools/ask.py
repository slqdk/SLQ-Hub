"""ask_user (0.43.0): the assistant asks the user to choose, on a card with buttons in the chat - like Claude's own
question cards. Only for real options; the turn waits for the answer (or a message written instead)."""
from __future__ import annotations

from .base import Tool, ToolContext, ToolResult, obj, register

MAX_QUESTIONS, MIN_OPTIONS, MAX_OPTIONS = 4, 2, 6

TEXT = ("Ask the user to choose, on a card with buttons in the chat - only when the choice is theirs and there are "
        "real options: 2-6 short ones per question (the user can also write their own), up to 4 questions at once. "
        "Not for what you can find out or decide sensibly yourself, and not to ask whether to go on. The turn waits "
        "for the answer.")
PARAMS = obj({"questions": {"type": "array", "minItems": 1, "maxItems": MAX_QUESTIONS, "items": obj({
    "question": {"type": "string", "description": "One short question, in the language of the conversation"},
    "options": {"type": "array", "minItems": MIN_OPTIONS, "maxItems": MAX_OPTIONS, "items": {"type": "string"},
                "description": "The choices, a few words each"},
    "multi": {"type": "boolean", "description": "The user may choose several"}}, ["question", "options"])}},
    ["questions"])


def clean(raw) -> list[dict]:
    out = []
    for q in (raw if isinstance(raw, list) else [])[:MAX_QUESTIONS]:
        if not isinstance(q, dict) or not isinstance(q.get("question"), str) or not q["question"].strip():
            continue
        opts = [str(o).strip()[:120] for o in q.get("options") or [] if isinstance(o, (str, int, float))
                and str(o).strip()]
        opts = list(dict.fromkeys(opts))[:MAX_OPTIONS]
        if len(opts) >= MIN_OPTIONS:
            out.append({"question": q["question"].strip()[:300], "options": opts, "multi": bool(q.get("multi"))})
    return out


async def ask_user(ctx: ToolContext, args: dict) -> ToolResult:
    questions = clean(args.get("questions"))
    if not questions:
        return ToolResult(f"questions must be 1-{MAX_QUESTIONS} questions, each with {MIN_OPTIONS}-{MAX_OPTIONS} "
                          "options. For an open question, ask it in your answer instead.", is_error=True)
    got = await ctx.run.ask_questions(ctx.call_id, questions)
    if got is None:
        return ToolResult("No answer came within 30 minutes. Go on with the choice you think best, and say which "
                          "one you made.")
    if got.get("typed"):
        return ToolResult("The user wrote a message instead of choosing on the card - it comes next: go on from "
                          "what they wrote.")
    answers = got.get("answers") or []
    lines = []
    for i, q in enumerate(questions):
        a = answers[i] if i < len(answers) else []
        lines.append(f"- {q['question']} {', '.join(a) if a else '(no answer)'}")
    return ToolResult("The user answered:\n" + "\n".join(lines),
                      display={"answers": [answers[i] if i < len(answers) else [] for i in range(len(questions))]})


register(Tool("ask_user", TEXT, PARAMS, ask_user, in_subagent=False))
