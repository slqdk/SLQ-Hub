"""From stored chat messages to the messages the model sees, and keeping
them inside the context window (compaction)."""
from __future__ import annotations

import json
import re

from ..chats import store as chats
from ..llm.messages import image_part
from ..llm.stream import valid_arguments
from ..projects import blobs
from .prompt import sent_stamp

IMAGE_TOKENS = 1000
CHARS_PER_TOKEN = 3.2
# Tool output from turns before the previous one (0.27.0): kept short - read again when needed. The same rows are
# shortened the same way every turn, so llama-server's prompt cache still holds.
OLD_RESULT_CHARS = 1500
OLD_RESULT_HEAD = 400
FULL_OUTPUT = re.compile(r"\n\n\(The full output is in (\S+) - read it with read_file and offset/limit\.\)$")
SHORTENED = re.compile(r"\n?\[(?:\.\.\. )?(\d+) (?:more )?characters - [^;\]]*; ([^\]]*)\]$")
LOOK_IT_UP = "look it up whole with chat_history, or call the tool again"


def shorten_result(text: str, head: int, why: str) -> str:
    """A tool result cut to its first `head` characters, with what is left out and where to get it whole. Shortening
    a shortened one again changes nothing unless it cuts deeper - and keeps the count and the pointer: the same text
    every time, so llama-server's prompt cache holds."""
    m = SHORTENED.search(text)
    if m:
        body, extra, where = text[:m.start()], int(m.group(1)), m.group(2)
    else:
        f = FULL_OUTPUT.search(text)
        body, extra = (text[:f.start()] if f else text), 0
        where = f"the whole output is in {f.group(1)}" if f else LOOK_IT_UP
    if len(body) <= max(head, 0):
        return text
    left = extra + len(body) - max(head, 0)
    if head <= 0:
        return f"[{left} characters - {why}; {where}]"
    return body[:head] + f"\n[... {left} more characters - {why}; {where}]"


def results_from(rows: list[dict], current_run: str | None) -> int:
    """The first message of the turn before this one: tool output older than that is kept short."""
    earlier = [r["run_id"] for r in rows if r.get("run_id") and r["run_id"] != current_run]
    if not earlier:
        return 0
    return min(r["id"] for r in rows if r.get("run_id") == earlier[-1])


def _image(block: dict) -> dict | None:
    sha = block.get("sha")
    if not sha or not blobs.exists(sha):
        return None
    try:
        return image_part(blobs.read(sha))
    except ValueError:
        return None


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def continue_text(block: dict) -> str:
    """What the model is told when the user presses Continue under an unfinished turn (0.22.0): why it stopped."""
    if block.get("reason") == "limit":
        why = (f"Your last turn ended at the step limit ({int(block.get('steps') or 0)} steps, the most one turn may "
               "take) before the work was done.")
    else:
        why = "They stopped your last turn before it was done."
    return (f"(SLQ Hub: the user pressed Continue. {why} Go on from where you stopped: check the state of what you "
            "were making before you change it, and do not start over.)")


def handover_text(block: dict, notes: bool = True) -> str:
    """The first message of a chat that continues another (0.27.0); the notes from it follow as the answer - unless
    writing them was stopped."""
    return (f"(SLQ Hub: this chat continues the chat \"{block.get('title') or ''}\". Its files are in the working "
            + ("folder, and your notes from it follow - work from them. " if notes else "folder. ")
            + "chat_history looks up anything from that chat.)")


def _file_ref(block: dict, fresh: bool) -> str:
    """A file of the working folder that the user put in a message: its text
    as it was then (while the message is recent), else where to read it."""
    path = block.get("path") or block.get("name") or "?"
    sha = block.get("sha")
    if fresh and block.get("inline") and sha and blobs.exists(sha):
        text = blobs.read(sha).decode("utf-8", "replace")
        fence = "`" * max(3, 1 + max((len(m) for m in re.findall(r"`+", text)), default=0))
        return (f"[File from the working folder: {path} ({block.get('lines', 0)} lines), as it was when this "
                f"message was sent]\n{fence}\n{text.rstrip(chr(10))}\n{fence}")
    if block.get("inline"):
        return (f"[File from the working folder: {path} ({_size(int(block.get('size') or 0))}) - it was shown "
                "with this message; read it again with read_file if you need it]")
    return (f"[File from the working folder: {path} ({_size(int(block.get('size') or 0))}) - "
            "read it with read_file]")


def to_model_messages(rows: list[dict], current_run: str | None = None, keep_images_from: int = 0,
                      vision: bool = True, keep_results_from: int = 0) -> list[dict]:
    """Stored rows -> OpenAI-format messages. Images older than message id
    `keep_images_from` are replaced by a note (they cost ~1000 tokens each),
    and so is every image when the model has no vision; long tool output older
    than `keep_results_from` is shortened. A tool call without a stored result
    (the turn was stopped) gets a stand-in result, because every API - and the
    chat templates - expect the pairs to be complete."""
    out: list[dict] = []
    pending_images: list[dict] = []
    open_calls: list[str] = []
    answered = {rows[i]["id"] for i in range(len(rows) - 1) if rows[i + 1]["role"] == "assistant"}

    def flush():
        for cid in open_calls:
            out.append({"role": "tool", "tool_call_id": cid, "content": "(No result: the turn was stopped.)"})
        open_calls.clear()
        if pending_images:
            out.append({"role": "user", "content": [{"type": "text", "text": "(Images returned by the tools above.)"},
                                                    *pending_images]})
            pending_images.clear()

    for r in rows:
        role, blocks = r["role"], r["content"]
        fresh = r["id"] >= keep_images_from
        recent = vision and fresh
        if role == "tool":
            for b in blocks:
                if b.get("type") != "tool_result":
                    continue
                cid = b.get("tool_call_id")
                if cid not in open_calls:
                    continue                  # a result without its call: drop it
                open_calls.remove(cid)
                text = b.get("text") or ""
                if b.get("is_error") and not text.startswith("Error"):
                    text = "Error: " + text
                if r["id"] < keep_results_from and len(text) > OLD_RESULT_CHARS:
                    text = shorten_result(text, OLD_RESULT_HEAD, "a result from an earlier turn")
                out.append({"role": "tool", "tool_call_id": cid, "content": text})
                for img in b.get("images") or []:
                    part = _image(img) if recent else None
                    if part:
                        pending_images.append(part)
            continue
        flush()
        if role == "user":
            docs: list[dict] = []             # files from the working folder go before what the user wrote
            parts: list[dict] = []
            for b in blocks:
                t = b.get("type")
                if t == "text" and b.get("text"):
                    text = b["text"]
                    if b.get("while_working"):
                        text = "(The user wrote this while you were working - take it into account:)\n" + text
                    parts.append({"type": "text", "text": text})
                elif t == "image":
                    part = _image(b) if recent else None
                    if part:
                        parts.append(part)
                    what = "Image from the working folder" if b.get("ref") else "Attached image"
                    note = f"[{what}: {b.get('path') or b.get('name')}]"
                    parts.append({"type": "text", "text": note if part else note + " (not shown - use view_image "
                                                                                    "to look at it)"})
                elif t == "file":
                    parts.append({"type": "text", "text": f"[Attached file: {b.get('path') or b.get('name')} "
                                                          f"({b.get('size', 0)} bytes) - read it with read_file]"})
                elif t == "file_ref":
                    docs.append({"type": "text", "text": _file_ref(b, fresh)})
                elif t == "continue":
                    parts.append({"type": "text", "text": continue_text(b)})
                elif t == "handover":
                    parts.append({"type": "text", "text": handover_text(b, notes=r["id"] in answered)})
            parts = docs + parts
            if not parts:
                continue
            stamp = sent_stamp(r.get("created_at"))          # when it was sent: the model has no clock
            if stamp:
                if parts[0]["type"] == "text":
                    parts[0] = {"type": "text", "text": f"{stamp}\n{parts[0]['text']}"}
                else:
                    parts.insert(0, {"type": "text", "text": stamp})
            if all(p["type"] == "text" for p in parts):
                out.append({"role": "user", "content": "\n\n".join(p["text"] for p in parts)})
            else:
                out.append({"role": "user", "content": parts})
        elif role == "assistant":
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            msg: dict = {"role": "assistant", "content": text}
            calls = [{"id": b["id"], "type": "function",      # as stored, unless the model server could not read it back
                      "function": {"name": b["name"], "arguments": valid_arguments(b.get("arguments"))}}
                     for b in blocks if b.get("type") == "tool_call"]
            if calls:
                msg["tool_calls"] = calls
                open_calls.extend(c["id"] for c in calls)
            if current_run and r.get("run_id") == current_run:
                reasoning = "".join(b.get("text", "") for b in blocks if b.get("type") == "thinking")
                if reasoning:
                    msg["reasoning_content"] = reasoning
            if text or calls:
                out.append(msg)
    flush()
    return out


def without_images(messages: list[dict]) -> list[dict]:
    """The messages for a model that cannot see: pictures become a short note."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(p.get("type") == "image_url" for p in c):
            texts = [p.get("text", "") if p.get("type") != "image_url" else "[a picture - this model cannot see it]"
                     for p in c]
            m = {**m, "content": "\n\n".join(t for t in texts if t)}
        out.append(m)
    return out


def estimate_tokens(messages: list[dict]) -> int:
    chars, images = 0, 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            chars += len(c)
        else:
            for p in c or []:
                if p.get("type") == "image_url":
                    images += 1
                else:
                    chars += len(p.get("text", ""))
        for tc in m.get("tool_calls") or []:
            chars += len(tc["function"].get("arguments") or "") + 20
        chars += len(m.get("reasoning_content") or "")
    return int(chars / CHARS_PER_TOKEN) + images * IMAGE_TOKENS


# The notes a chat is condensed into (0.27.0): structured, and each time they replace the earlier ones - appended,
# the summary grew with every condensing until it left no room for anything else.
NOTES_HEADINGS = """## Goal
## Decisions and facts
## Files
## Done
## Open questions
## Next"""
NOTES_RULES = """Keep what the user asked for and decided, facts and numbers that matter, the files with their \
paths in the working folder (attachments are in .hub/attachments/) and what they hold, what was tried and did not \
work, and the next step. Say which findings were checked and which are still guesses. Leave out pleasantries and what is settled and \
will not matter again. Write in the conversation's language, as short bullet points under these headings \
(leave out a heading with nothing under it), at most about 600 words. Reply with the notes only.

""" + NOTES_HEADINGS
SUMMARY_PROMPT = ("Condense the conversation above into notes that let you continue the work without it. They "
                  "replace the earlier notes, if any are given above: carry over what still matters from them. "
                  + NOTES_RULES)
HANDOVER_PROMPT = ("The conversation above continues in a new chat, which starts with nothing but these notes "
                   "(and the files of the working folder). Write the notes you will continue the work from: the "
                   "earlier notes, if any are given above, and everything after them. " + NOTES_RULES)
MAX_NOTES = 20000                   # characters the user may give the notes (Settings-free: the chat's menu)


def compaction_split(rows: list[dict]) -> int | None:
    """The id of the last message to fold into the summary: everything before
    the latest user message. None when there is nothing old enough."""
    last_user = None
    for r in rows:
        if r["role"] == "user":
            last_user = r["id"]
    if last_user is None:
        return None
    older = [r["id"] for r in rows if r["id"] < last_user]
    return older[-1] if older else None


def schema_tokens(schemas: list[dict] | None) -> int:
    """The room the tool definitions take in a request (they are not in the messages)."""
    return int(len(json.dumps(schemas or [], ensure_ascii=False)) / CHARS_PER_TOKEN)


def trim_tool_results(messages: list[dict], keep_last: int = 6, limit: int = 1500,
                      why: str = "shortened to make room", unread: bool = False) -> list[dict]:
    """Shorten older tool outputs in the current turn (the full text stays in
    the chat history, and long ones in .hub/outputs). The results of the last
    step stay whole whatever keep_last says - the model has not read them yet -
    unless `unread` (the last resort: they do not fit together)."""
    last_answer = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old = {i for i in tool_idx[:max(0, len(tool_idx) - keep_last)] if unread or i < last_answer}
    out = []
    for i, m in enumerate(messages):
        if i in old and isinstance(m.get("content"), str) and len(m["content"]) > limit:
            m = {**m, "content": shorten_result(m["content"], limit, why)}
        out.append(m)
    return out


CUT = re.compile(r"\n?\.\.\. \[shortened by (\d+) characters\]$")
# The Hub's marks for text it left out of what the model is shown - cut_text's, shorten_result's (with the reasons the
# Hub gives) and tools/base.py clip's - anywhere in a text: what the model writes into a file with one in it copied
# shortened context (chat 79: a 9B model wrote "<p>Brow... [shortened by 24 characters]" into its game's index.html -
# tools/files.py refuses it).
WHYS = ("shortened to make room", "cleared to keep the conversation short", "left out to make room",
        "a result from an earlier turn")
MARK = re.compile(r"\.\.\. \[shortened by \d+ characters\]|\.\.\. \[\d+ characters omitted\] \.\.\."
                  r"|\[(?:\.\.\. )?\d+ (?:more )?characters - (?:" + "|".join(map(re.escape, WHYS)) + r"); [^\]\n]*\]")


def cut_text(text: str, limit: int, sep: str = "\n") -> str:
    """Text cut to `limit` characters, with how many were cut. Cutting a cut text again changes nothing unless it
    cuts deeper (the count then adds up): the same text every time, so llama-server's prompt cache holds (chat 78:
    re-cutting changed "shortened by 33" to "32" - an older message changed at every step)."""
    m = CUT.search(text)
    body, extra = (text[:m.start()], int(m.group(1))) if m else (text, 0)
    if len(body) <= limit:
        return text
    return body[:limit] + f"{sep}... [shortened by {extra + len(body) - limit} characters]"


def _shrink_args(raw: str, limit: int) -> str:
    """Tool call arguments with long strings cut (still valid JSON; shrinking them again changes nothing)."""
    if len(raw or "") <= limit:
        return raw
    try:
        args = json.loads(raw)
    except ValueError:
        return json.dumps({"_shortened": raw[:limit]}, ensure_ascii=False)

    def cut(v):
        if isinstance(v, str):
            return cut_text(v, limit, "")
        if isinstance(v, dict):
            return {k: cut(x) for k, x in v.items()}
        if isinstance(v, list):
            return [cut(x) for x in v]
        return v
    return json.dumps(cut(args), ensure_ascii=False)


def trim_reasoning(messages: list[dict], keep_last: int = 2) -> list[dict]:
    """The thinking of all but the newest `keep_last` answers of the turn left out: a thinking model's working-out
    is most of a long turn (chat 78: 134,000 characters in 33 steps, more than its results and calls together), and
    what came of it stays - the answers, the calls, the results."""
    idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("reasoning_content")]
    old = set(idx[:max(0, len(idx) - keep_last)])
    return [{k: v for k, v in m.items() if k != "reasoning_content"} if i in old else m
            for i, m in enumerate(messages)]


def trim_assistant(messages: list[dict], keep_last: int = 4, limit: int = 400) -> list[dict]:
    """Shorten older answers, their thinking and the arguments of their tool
    calls - the last resort in a turn too long for the context."""
    idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    old = set(idx[:max(0, len(idx) - keep_last)])
    out = []
    for i, m in enumerate(messages):
        if i in old:
            m = {k: v for k, v in m.items() if k != "reasoning_content"}
            c = m.get("content")
            if isinstance(c, str):
                m["content"] = cut_text(c, limit)
            if m.get("tool_calls"):
                m["tool_calls"] = [{**tc, "function": {**tc["function"],
                                                       "arguments": _shrink_args(tc["function"].get("arguments") or "{}", limit)}}
                                   for tc in m["tool_calls"]]
        out.append(m)
    return out


def shrink_for_summary(messages: list[dict], budget: int) -> list[dict]:
    """Old messages cut down to fit a summary request: tool output first (the
    start of each result stays), then long answers, then the oldest messages
    go - with a note that they did."""
    msgs = trim_tool_results(messages, keep_last=0, limit=600)
    if estimate_tokens(msgs) > budget:
        msgs = trim_assistant(msgs, keep_last=2, limit=800)
    dropped = 0
    while msgs and estimate_tokens(msgs) > budget:
        msgs = msgs[1:]
        dropped += 1
    while msgs and msgs[0].get("role") == "tool":       # a result without its call
        msgs = msgs[1:]
        dropped += 1
    if dropped:
        msgs = [{"role": "user", "content": f"({dropped} earlier messages are left out here: they did not fit.)"},
                *msgs]
    return msgs


def chat_rows(chat_id: int, after_id: int = 0) -> list[dict]:
    return chats.messages(chat_id, after_id)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
