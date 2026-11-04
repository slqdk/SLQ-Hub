"""remember (0.74.0): a persona's notes about the owner, which it keeps itself - in a chat with a persona that has
its memory on, nowhere else (personas/). post_draft (0.75.0): a persona's task hands in its post."""
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

# 0.75.0: a persona's task hands in its post for the owner's review (personas/tasks.py)
POST_TEXT = ("Hand in the post you made for this task, for the owner's review: its text exactly as it is to be "
             "posted, in your voice, and the picture(s) or the video from your folder that go with it - the paths "
             "the tools that made them gave. Nothing is published: the owner looks at it first. Hand in each post "
             "once.")
POST_PARAMS = obj({"text": {"type": "string", "description": "The post's text, as it is to be posted"},
                   "media": {"type": "array", "items": {"type": "string"},
                             "description": "Paths in your folder: the picture(s) or the video of the post"}}, ["text"])


def _task(ctx: ToolContext):
    store, p = _persona(ctx)
    return store, p, (store.task_of(p, ctx.chat) if p is not None else None)


def _post_available(ctx: ToolContext) -> bool:
    return _task(ctx)[2] is not None and not ctx.depth


async def post_draft(ctx: ToolContext, args: dict) -> ToolResult:
    from ...personas.store import PersonaError
    store, p, task = _task(ctx)
    if task is None:
        return ToolResult("This chat is not a task's chat.", is_error=True)
    media = args.get("media") or []
    media = [media] if isinstance(media, str) else media if isinstance(media, list) else []
    try:
        post = await store.hand_in(p["id"], task["id"], (ctx.chat or {}).get("id"),
                                   getattr(ctx.run, "id", None), ctx.workdir, args.get("text"), media)
    except PersonaError as exc:
        return ToolResult(str(exc), is_error=True)
    files = len(post["media"])
    with_files = f" with its {files} file{'s' if files > 1 else ''}" if files else ""
    again = " It replaces the one you handed in before in this turn." if post.get("replaced") else ""
    return ToolResult(f"Handed in: the post waits for the owner's review{with_files}.{again}",
                      display={"post": {"id": post["id"], "persona": p["id"], "text": post["text"],
                                        "media": post["media"]}})


register(Tool("post_draft", POST_TEXT, POST_PARAMS, post_draft, available=_post_available, in_subagent=False,
              group="persona"))
