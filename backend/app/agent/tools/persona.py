"""remember (0.74.0): a persona's notes about the owner, which it keeps itself - in a chat with a persona that has
its memory on, nowhere else (personas/)."""
from __future__ import annotations

from .base import Tool, ToolContext, ToolResult, obj, register

TEXT = ("Your notes about the person you are talking with, kept between conversations (your instructions show them "
        "under 'Your notes'). add: a new note - one short fact in your own words. replace: the note that contains "
        "`old` becomes `text`. forget: the note that contains `old` goes.")
PARAMS = obj({"action": {"type": "string", "enum": ["add", "replace", "forget"]},
              "text": {"type": "string", "description": "The note (add, replace)"},
              "old": {"type": "string", "description": "Words from the note to replace or forget"}}, ["action"])


def _persona(ctx: ToolContext):
    store = ctx.hub.extras.get("personas")
    return store, (store.of_chat(ctx.chat) if store is not None else None)


def _available(ctx: ToolContext) -> bool:
    _, p = _persona(ctx)
    return bool(p and p.get("memory")) and not ctx.depth


async def remember(ctx: ToolContext, args: dict) -> ToolResult:
    from ...personas.store import PersonaError
    store, p = _persona(ctx)
    if p is None or not p.get("memory"):
        return ToolResult("There are no notes to keep in this chat.", is_error=True)
    try:
        said = await store.remember(p["id"], str(args.get("action") or ""), str(args.get("text") or ""),
                                    str(args.get("old") or ""))
    except PersonaError as exc:
        return ToolResult(str(exc), is_error=True)
    return ToolResult(said)


register(Tool("remember", TEXT, PARAMS, remember, available=_available, in_subagent=False, group="persona"))
