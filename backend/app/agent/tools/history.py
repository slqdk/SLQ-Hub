"""chat_history (0.27.0): the whole conversation, searchable - also what was condensed into the notes, what was
left out of the context, the long tool output of older turns, and the chat this one continues from.

A long chat keeps the model's context small (notes, cleared tool output); this is how a detail comes back when it
is needed. Owner chats are searched in the database (this chat, then the chats it continues, at most five back);
a person's turn searches the conversation their device sent with the message - nothing of it is stored here."""
from __future__ import annotations

import asyncio
import math
import re

from ...chats import store as chats
from .base import Tool, ToolContext, ToolResult, clip, obj, register

MIN_ROWS = 20                       # a chat this long has older turns whose tool output is shortened
MAX_HITS = 6
SNIPPET = 140                       # characters on each side of the match
CHAIN = 5                           # chats followed back through "continued from"
WORD = re.compile(r"[\wæøåäöüéè]+", re.IGNORECASE)


def _device(ctx: ToolContext):
    return getattr(ctx.engine, "device", None)


def chain(chat: dict) -> list[dict]:
    """This chat and the chats it continues from (newest first)."""
    out, seen, cur = [], set(), chat
    while cur and cur.get("id") not in seen and len(out) <= CHAIN:
        out.append(cur)
        seen.add(cur.get("id"))
        prev = (cur.get("settings") or {}).get("continued_from")
        cur = chats.get_chat(int(prev)) if prev else None
    return out


def available(ctx: ToolContext) -> bool:
    """Offered when there is more than the model sees: condensed or left out, a chat continued from another, tool
    output kept short (older turns', or cleared in this one), or simply a long chat."""
    from ..context import results_from
    device = _device(ctx)
    if device is not None:
        return bool(device.dropped) or results_from(device.history, None) > 0 or len(device.history) >= MIN_ROWS
    chat = ctx.chat or {}
    if not chat.get("id"):
        return False
    if chat.get("summary_upto") or (chat.get("settings") or {}).get("continued_from") \
            or getattr(ctx.engine, "cleared_any", False):
        return True
    return chats.count_runs(chat["id"]) >= 3 or chats.count_messages(chat["id"]) >= MIN_ROWS


def message_text(row: dict) -> str:
    """What a stored message says, as plain text: its text, its tool calls, its tool results."""
    parts = []
    for b in row.get("content") or []:
        t = b.get("type")
        if t in ("tool_call", "tool_result") and b.get("name") == "chat_history":
            continue                                  # its own searches would find themselves
        if t == "text" and b.get("text"):
            parts.append(b["text"])
        elif t == "tool_call":
            parts.append(f"[called {b.get('name')}: {b.get('arguments') or '{}'}]")
        elif t == "tool_result":
            parts.append(f"[{b.get('name')} returned:]\n{b.get('text') or ''}")
        elif t in ("image", "file", "file_ref"):
            parts.append(f"[{t}: {b.get('path') or b.get('name') or ''}]")
        elif t == "handover":
            parts.append(f"[continued from the chat \"{b.get('title') or ''}\"]")
    return "\n".join(parts)


def _label(row: dict) -> str:
    if row["role"] == "tool":
        names = {b.get("name") for b in row.get("content") or [] if b.get("type") == "tool_result"}
        return "tool " + ", ".join(sorted(n for n in names if n)) if names else "tool"
    return {"user": "the user", "assistant": "you"}.get(row["role"], row["role"])


def _when(row: dict) -> str:
    stamp = str(row.get("created_at") or "")
    return stamp[:16].replace("T", " ") if stamp else ""


def _terms(text: str) -> list[str]:
    return [w.lower() for w in WORD.findall(text) if len(w) > 1]


def search(rows: list[tuple[str, dict]], query: str, limit: int = MAX_HITS) -> list[tuple[float, str, dict, str]]:
    """BM25 over the messages' words, the whole query as a phrase counting extra. -> (score, where, row, text)."""
    terms = list(dict.fromkeys(_terms(query)))
    if not terms:
        return []
    docs = [(where, r, message_text(r)) for where, r in rows]
    words = [_terms(t) for _, _, t in docs]
    n = len(docs) or 1
    avg = sum(len(w) for w in words) / n or 1.0
    df = {t: sum(1 for w in words if t in set(w)) for t in terms}
    phrase = " ".join(terms)
    scored = []
    for (where, r, text), w in zip(docs, words):
        if not w:
            continue
        score = 0.0
        for t in terms:
            tf = w.count(t)
            if tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(w) / avg))
        if score and len(terms) > 1 and phrase in " ".join(w):
            score *= 1.5
        if score:
            scored.append((score, where, r, text))
    scored.sort(key=lambda s: -s[0])
    return scored[:limit]


def snippet(text: str, query: str) -> str:
    low = text.lower()
    at = -1
    for t in sorted(set(_terms(query)), key=len, reverse=True):
        at = low.find(t)
        if at >= 0:
            break
    at = max(0, at)
    start, end = max(0, at - SNIPPET), min(len(text), at + SNIPPET)
    s = " ".join(text[start:end].split())
    return ("..." if start else "") + s + ("..." if end < len(text) else "")


def _without_question(rows: list[dict], run_id: str | None) -> list[dict]:
    """The rows without the message that started this turn: the model has it in front of it."""
    first_of_turn = next((r["id"] for r in rows if run_id and r.get("run_id") == run_id), None)
    asked = [r for r in rows if r["role"] == "user" and (first_of_turn is None or r["id"] < first_of_turn)]
    return [r for r in rows if not asked or r is not asked[-1]]


def _rows(ctx: ToolContext) -> list[tuple[str, dict]]:
    device = _device(ctx)
    if device is not None:
        return [("", r) for r in _without_question(list(device.history), None)]
    out: list[tuple[str, dict]] = []
    for i, c in enumerate(chain(ctx.chat)):
        where = "" if i == 0 else f" in the earlier chat \"{c['title']}\""
        rows = chats.messages(c["id"])
        out += [(where, r) for r in (_without_question(rows, ctx.run.id) if i == 0 else rows)]
    return out


async def chat_history(ctx: ToolContext, args: dict) -> ToolResult:
    action = (args.get("action") or "").strip()
    rows = await asyncio.to_thread(_rows, ctx)         # a long chain of long chats: not on the event loop
    if action == "read":
        try:
            want = int(args.get("id"))
        except (TypeError, ValueError):
            return ToolResult("read needs the id of a message, as search gives it (#123).", is_error=True)
        hit = next(((w, r) for w, r in rows if r.get("id") == want), None)
        if hit is None:
            return ToolResult(f"No message #{want} in this conversation.", is_error=True)
        where, r = hit
        limit = int(ctx.settings.get("agent", "tool_output_chars") or 12000)
        text, _ = clip(message_text(r) or "(empty)", limit)
        return ToolResult(f"#{want}{where} ({_label(r)}, {_when(r)}):\n{text}")
    if action != "search":
        return ToolResult('action is "search" (with query) or "read" (with id).', is_error=True)
    query = " ".join(str(args.get("query") or "").split())
    if not query:
        return ToolResult("search needs a query: a few words that the message you look for would contain.",
                          is_error=True)
    hits = await asyncio.to_thread(search, rows, query)
    if not hits:
        return ToolResult(f"Nothing in the {len(rows)} messages of this conversation matches \"{query}\". Try "
                          "other words - the ones the message itself would use.")
    lines = [f"{len(hits)} best matches for \"{query}\" among {len(rows)} messages:"]
    for _, where, r, text in hits:
        lines.append(f"#{r.get('id')}{where} ({_label(r)}, {_when(r)}): {snippet(text, query)}")
    lines.append('Read one whole with action "read" and its id.')
    return ToolResult("\n".join(lines))


register(Tool("chat_history", "Search this whole conversation - also the part condensed into your notes, what was "
              "left out of your context, the full tool output of earlier turns and the chat this one continues "
              "from - for what was said, decided, written or returned; then read a message whole. "
              'action "search" with query (words the message would contain), action "read" with id.',
              obj({"action": {"type": "string", "enum": ["search", "read"]},
                   "query": {"type": "string", "description": "For search: a few words"},
                   "id": {"type": "integer", "description": "For read: a message id from the search (#123)"}},
                  ["action"]),
              chat_history, available=available, read_only=True, group="knowledge"))
