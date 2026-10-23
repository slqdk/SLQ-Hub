"""skill, memory, artifact and todo_write."""
from __future__ import annotations

import re
import secrets

from ...chats import store as chats
from .base import Tool, ToolContext, ToolResult, obj, register

ARTIFACT_TYPES = ["html", "react", "svg", "markdown", "mermaid", "code"]


def _enabled_skills(ctx: ToolContext) -> list[str]:
    return [s["name"] for s in ctx.hub.extras["skills"].chosen(ctx.chat, ctx.project)]


async def skill(ctx: ToolContext, args: dict) -> ToolResult:
    store = ctx.hub.extras["skills"]
    name = (args.get("name") or "").strip()
    if getattr(ctx.engine, "device", None) is not None:          # a person's chat: the skills switched on, no more
        allowed = _enabled_skills(ctx)
        if name not in allowed:
            return ToolResult(f"No skill {name!r}. Skills here: {', '.join(allowed) or 'none'}.", is_error=True)
    try:
        if args.get("file"):
            data = store.read_file(name, args["file"])
            return ToolResult(f"{name}/{args['file']}:\n" + data.decode("utf-8", "replace")[:150_000])
        text = store.get(name)
    except FileNotFoundError:
        names = ", ".join(s["name"] for s in store.list()) or "none"
        return ToolResult(f"No skill {name!r}. Installed skills: {names}.", is_error=True)
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    files = next((s["files"] for s in store.list() if s["name"] == name), [])
    more = f"\n\nOther files in this skill (read with skill(name, file)): {', '.join(files)}" if files else ""
    return ToolResult(f"Skill {name}:\n\n{text}{more}")


async def memory(ctx: ToolContext, args: dict) -> ToolResult:
    m = ctx.hub.extras["memory"]
    pid = ctx.project["id"] if ctx.project else None
    cmd = args.get("command")
    try:
        if cmd == "view":
            return ToolResult(m.view(args.get("path") or "/memories", pid))
        if cmd == "create":
            return ToolResult(m.create(args.get("path") or "", args.get("file_text") or "", pid))
        if cmd == "str_replace":
            return ToolResult(m.str_replace(args.get("path") or "", args.get("old_str") or "",
                                            args.get("new_str") or "", pid))
        if cmd == "insert":
            return ToolResult(m.insert(args.get("path") or "", int(args.get("insert_line") or 0),
                                       args.get("insert_text") or "", pid))
        if cmd == "delete":
            return ToolResult(m.delete(args.get("path") or "", pid))
        if cmd == "rename":
            return ToolResult(m.rename(args.get("old_path") or "", args.get("new_path") or "", pid))
    except FileNotFoundError as exc:
        return ToolResult(f"No such memory file: {exc}", is_error=True)
    except (ValueError, OSError) as exc:
        return ToolResult(str(exc), is_error=True)
    return ToolResult("command must be view, create, str_replace, insert, delete or rename", is_error=True)


def _artifact_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "artifact").lower()).strip("-")[:40]
    return slug or "artifact-" + secrets.token_hex(3)


async def artifact(ctx: ToolContext, args: dict) -> ToolResult:
    device = getattr(ctx.engine, "device", None)             # a person's chat: artifacts live on their device
    action = args.get("action") or "create"
    aid = (args.get("id") or "").strip() or _artifact_id(args.get("title") or "")
    current = device.get_artifact(aid) if device is not None else chats.get_artifact(ctx.chat["id"], aid)
    if action == "update":
        if not current:
            return ToolResult(f"There is no artifact {aid!r} to update.", is_error=True)
        old, new = args.get("old_str") or "", args.get("new_str") or ""
        n = current["content"].count(old) if old else 0
        if n != 1:
            return ToolResult(f"old_str occurs {n} times in the artifact (must be exactly once).", is_error=True)
        content, title, kind = current["content"].replace(old, new, 1), current["title"], current["type"]
    else:
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            return ToolResult("content is required to create or rewrite an artifact", is_error=True)
        kind = args.get("type") or (current or {}).get("type") or "html"
        if kind not in ARTIFACT_TYPES:
            return ToolResult(f"type must be one of {', '.join(ARTIFACT_TYPES)}", is_error=True)
        title = args.get("title") or (current or {}).get("title") or aid
    if device is not None:
        version = device.save_artifact(aid, title, kind, content)
    else:
        version = chats.save_artifact(ctx.chat["id"], aid, title, kind, content)
    ref = {"id": aid, "version": version, "title": title, "type": kind, "language": args.get("language")}
    if device is not None:
        ref["content"] = content                             # the device keeps it; the Hub does not
    ctx.emit("artifact", **ref)
    return ToolResult(f"Artifact {aid!r} saved as version {version}. It is shown to the user in the side panel.",
                      display={"artifact": ref})


async def todo_write(ctx: ToolContext, args: dict) -> ToolResult:
    items = args.get("todos") or []
    clean = []
    for it in items[:50]:
        if not isinstance(it, dict) or not str(it.get("content", "")).strip():
            continue
        status = it.get("status") if it.get("status") in ("pending", "in_progress", "completed") else "pending"
        clean.append({"content": str(it["content"]).strip()[:300], "status": status})
    ctx.state["todos"] = clean
    ctx.emit("todo", items=clean)
    done = sum(1 for t in clean if t["status"] == "completed")
    return ToolResult(f"Task list updated: {done}/{len(clean)} done.", display={"todos": clean})


register(Tool("skill", "Load an installed skill's instructions (SKILL.md), or one of its other files. Skills "
              "enabled for this chat are listed in the system prompt; load one when the task matches it.",
              obj({"name": {"type": "string"}, "file": {"type": "string", "description": "A file inside the skill"}},
                  ["name"]), skill, available=lambda ctx: bool(_enabled_skills(ctx)), group="knowledge",
              read_only=True))
register(Tool("memory", "Your persistent memory across chats: Markdown files under /memories (about the user "
              "and their preferences) and /memories/project (this project). Check it when earlier context "
              "would help; save durable facts the user tells you, or asks you to remember. Keep files short "
              "and organised; update instead of duplicating.",
              obj({"command": {"type": "string", "enum": ["view", "create", "str_replace", "insert", "delete", "rename"]},
                   "path": {"type": "string"}, "file_text": {"type": "string"}, "old_str": {"type": "string"},
                   "new_str": {"type": "string"}, "insert_line": {"type": "integer"}, "insert_text": {"type": "string"},
                   "old_path": {"type": "string"}, "new_path": {"type": "string"}}, ["command"]),
              memory, group="knowledge"))
register(Tool("artifact", "Show the user a live artifact in the side panel: an HTML page, a React component "
              "(one file, default export, Tailwind classes allowed), SVG, Mermaid diagram, Markdown document or "
              "code. Use it for anything substantial the user will look at, reuse or iterate on - not for short "
              "answers. 'create' a new one, 'rewrite' it whole, or 'update' with an exact old_str/new_str.",
              obj({"action": {"type": "string", "enum": ["create", "rewrite", "update"]},
                   "id": {"type": "string", "description": "Kebab-case id; reuse it to change the same artifact"},
                   "title": {"type": "string"}, "type": {"type": "string", "enum": ARTIFACT_TYPES},
                   "language": {"type": "string", "description": "For type code"},
                   "content": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"}},
                  ["action"]), artifact, in_subagent=False, group="knowledge"))
register(Tool("todo_write", "Keep a short task list for a job with several steps. Send the whole list each "
              "time; mark one item in_progress while you work on it and completed when done.",
              obj({"todos": {"type": "array", "items": obj({"content": {"type": "string"},
                                                            "status": {"type": "string",
                                                                       "enum": ["pending", "in_progress", "completed"]}},
                                                           ["content", "status"])}}, ["todos"]),
              todo_write, in_subagent=False, group="knowledge"))
