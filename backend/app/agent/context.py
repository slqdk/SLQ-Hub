"""From stored chat messages to the messages the model sees, and keeping
them inside the context window (compaction)."""
from __future__ import annotations

import json

from ..chats import store as chats
from ..llm.messages import image_part
from ..projects import blobs

IMAGE_TOKENS = 1000


def _image(block: dict) -> dict | None:
    sha = block.get("sha")
    if not sha or not blobs.exists(sha):
        return None
    try:
        return image_part(blobs.read(sha))
    except ValueError:
        return None


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
        recent = vision and r["id"] >= keep_images_from
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
            parts: list[dict] = []
            for b in blocks:
                t = b.get("type")
                if t == "text" and b.get("text"):
                    parts.append({"type": "text", "text": b["text"]})
                elif t == "image":
                    part = _image(b) if recent else None
                    if part:
                        parts.append(part)
                    note = f"[Attached image: {b.get('path') or b.get('name')}]"
                    parts.append({"type": "text", "text": note if part else note + " (not shown - use view_image "
                                                                                    "to look at it)"})
                elif t == "file":
                    parts.append({"type": "text", "text": f"[Attached file: {b.get('path') or b.get('name')} "
                                                          f"({b.get('size', 0)} bytes) - read it with read_file]"})
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
    return int(chars / 3.2) + images * IMAGE_TOKENS


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


def trim_tool_results(messages: list[dict], keep_last: int = 6, limit: int = 1500) -> list[dict]:
    """Shorten older tool outputs in the current turn (the full text stays in
    the chat history and .hub/outputs)."""
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old = set(tool_idx[:-keep_last]) if len(tool_idx) > keep_last else set()
    out = []
    for i, m in enumerate(messages):
        if i in old and isinstance(m.get("content"), str) and len(m["content"]) > limit:
            m = {**m, "content": m["content"][:limit] + f"\n... [trimmed {len(m['content']) - limit} characters]"}
        out.append(m)
    return out


def chat_rows(chat_id: int, after_id: int = 0) -> list[dict]:
    return chats.messages(chat_id, after_id)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
