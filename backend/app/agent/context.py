"""From stored chat messages to the messages the model sees, and keeping
them inside the context window (compaction)."""
from __future__ import annotations

import json
import re

from ..chats import store as chats
from ..llm.messages import image_part
from ..projects import blobs

IMAGE_TOKENS = 1000
CHARS_PER_TOKEN = 3.2


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
                      vision: bool = True) -> list[dict]:
    """Stored rows -> OpenAI-format messages. Images older than message id
    `keep_images_from` are replaced by a note (they cost ~1000 tokens each),
    and so is every image when the model has no vision. A tool call without
    a stored result (the turn was stopped) gets a stand-in result, because
    every API - and the chat templates - expect the pairs to be complete."""
    out: list[dict] = []
    pending_images: list[dict] = []
    open_calls: list[str] = []

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
            parts = docs + parts
            if not parts:
                continue
            if all(p["type"] == "text" for p in parts):
                out.append({"role": "user", "content": "\n\n".join(p["text"] for p in parts)})
            else:
                out.append({"role": "user", "content": parts})
        elif role == "assistant":
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            msg: dict = {"role": "assistant", "content": text}
            calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": b.get("arguments") or "{}"}}
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


SUMMARY_PROMPT = """Condense the conversation above into a summary that lets you continue the work without it. \
Keep: the user's goals and requests, decisions made, facts and numbers that matter, file names and what was \
done to them, open questions and the next steps. Leave out pleasantries and anything already resolved that \
won't matter again. Write it in the conversation's language, as compact notes (at most ~600 words). \
Reply with the summary only."""


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


def trim_tool_results(messages: list[dict], keep_last: int = 6, limit: int = 1500) -> list[dict]:
    """Shorten older tool outputs in the current turn (the full text stays in
    the chat history and .hub/outputs)."""
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old = set(tool_idx[:max(0, len(tool_idx) - keep_last)])
    out = []
    for i, m in enumerate(messages):
        if i in old and isinstance(m.get("content"), str) and len(m["content"]) > limit:
            m = {**m, "content": m["content"][:limit] + f"\n... [trimmed {len(m['content']) - limit} characters]"}
        out.append(m)
    return out


def _shrink_args(raw: str, limit: int) -> str:
    """Tool call arguments with long strings cut (still valid JSON)."""
    if len(raw or "") <= limit:
        return raw
    try:
        args = json.loads(raw)
    except ValueError:
        return json.dumps({"_shortened": raw[:limit]})

    def cut(v):
        if isinstance(v, str) and len(v) > limit:
            return v[:limit] + f"... [shortened by {len(v) - limit} characters]"
        if isinstance(v, dict):
            return {k: cut(x) for k, x in v.items()}
        if isinstance(v, list):
            return [cut(x) for x in v]
        return v
    return json.dumps(cut(args), ensure_ascii=False)


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
            if isinstance(c, str) and len(c) > limit:
                m["content"] = c[:limit] + f"\n... [shortened by {len(c) - limit} characters]"
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
