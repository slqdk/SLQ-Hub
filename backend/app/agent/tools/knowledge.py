"""skill, skill_propose, memory, artifact and todo_write."""
from __future__ import annotations

import re
import secrets
from pathlib import PurePosixPath

from ...chats import store as chats
from ...projects.store import is_text, mime_of
from ..skills import turn_skills, who_of
from .base import Tool, ToolContext, ToolResult, clip, obj, register
from .files import IMAGE_EXT, MAX_READ_CHARS, _pdf_text, document_text

ARTIFACT_TYPES = ["html", "react", "svg", "markdown", "mermaid", "code"]


def _device(ctx: ToolContext):
    return getattr(ctx.engine, "device", None)


def _chosen(ctx: ToolContext) -> list[dict]:
    """The skills the user chose for this chat (and its project) - the only ones the skill tool reads (0.23.0)."""
    return turn_skills(ctx.hub.extras["skills"], ctx.chat, ctx.project, _device(ctx))


async def skill(ctx: ToolContext, args: dict) -> ToolResult:
    store = ctx.hub.extras["skills"]
    asked = (args.get("name") or "").strip()
    chosen = _chosen(ctx)
    hit = next((s for s in chosen if asked in (s["ref"], s["name"], s["dir"], s["title"])), None)
    if hit is None:
        names = ", ".join(s["ref"] for s in chosen) or "none"
        return ToolResult(f"No skill {asked!r} in this chat. The skills the user chose for it: {names}.",
                          is_error=True)
    ref, who = hit["ref"], who_of(_device(ctx))
    rel = (args.get("file") or "").strip().lstrip("/")
    if rel and rel != "SKILL.md":
        try:
            data = store.read_file(who, ref, rel)
        except (FileNotFoundError, ValueError):
            return ToolResult(f"No file {rel!r} in the skill {ref}. Its files: {', '.join(hit['files']) or 'none'}.",
                              is_error=True)
        name = PurePosixPath(rel).name
        ext = PurePosixPath(rel).suffix.lower()
        if ext in IMAGE_EXT:
            up = chats.save_upload(data, name, mime_of(name))
            return ToolResult(f"{ref}/{rel} is shown below.", images=[{"sha": up["sha"], "mime": up["mime"]}])
        if ext == ".pdf":
            text, pages = _pdf_text(data, 1, None)
            return ToolResult(f"{ref}/{rel} (PDF, {pages} pages):\n{clip(text, MAX_READ_CHARS)[0]}")
        doc = document_text(name, data)
        if doc is not None:
            return ToolResult(f"{ref}/{rel} ({doc[0]} text):\n{clip(doc[1], MAX_READ_CHARS)[0]}")
        if not is_text(data):
            return ToolResult(f"{ref}/{rel} is a binary file ({len(data):,} bytes); code can use it at "
                              f".hub/skills/{hit['dir']}/{rel}.")
        return ToolResult(f"{ref}/{rel}:\n{clip(data.decode('utf-8', 'replace'), MAX_READ_CHARS)[0]}")
    text = store.get(who, ref)
    more = (f"\n\nIts other files (read one with skill(name, file); for code they are in .hub/skills/{hit['dir']}/): "
            f"{', '.join(hit['files'])}") if hit["files"] else ""
    return ToolResult(f"Skill {ref}:\n\n{text}{more}")


async def skill_propose(ctx: ToolContext, args: dict) -> ToolResult:
    """A skill the assistant wrote in the working folder, checked and shown to the user in a card with Add skill
    (0.23.0). Nothing is added until they press it."""
    rel = (args.get("path") or "").strip().rstrip("/")
    try:
        folder = ctx.workdir.resolve(rel)
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    if not rel or not folder.is_dir():
        return ToolResult(f"No folder {rel or '(none given)'}: write the skill's SKILL.md and files under "
                          "skills/<name>/ first, then give that folder as path.", is_error=True)
    for inc in args.get("include") or []:                    # files kept as they are: into assets/
        try:
            src = ctx.workdir.resolve(str(inc))
        except ValueError as exc:
            return ToolResult(str(exc), is_error=True)
        if not src.is_file():
            return ToolResult(f"No file {inc} to include.", is_error=True)
        ctx.workdir.write(ctx.workdir.rel(folder / "assets" / src.name), src.read_bytes(), "agent", "hub",
                          f"put {src.name} in the skill")
    store = ctx.hub.extras["skills"]
    try:
        d = store.propose(who_of(_device(ctx)), folder)
    except ValueError as exc:
        return ToolResult(f"The skill is not ready: {exc}. Fix it and call skill_propose again.", is_error=True)
    card = {k: d[k] for k in ("draft", "name", "title", "description", "files", "size", "replaces", "ref")}
    count = len(d["files"]) + 1
    note = f" Adding it replaces the user's skill {d['name']}." if d["replaces"] else ""
    return ToolResult(f"The skill {d['name']} is ready ({count} file{'s' if count != 1 else ''}, {d['size']:,} bytes). "
                      f"The user sees it in a card with an Add skill button; it is not added until they press it."
                      f"{note}", display={"skill": card})


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
    from .files import copied_mark, marked
    mark = copied_mark(content, (current or {}).get("content") or "")
    if mark:
        return marked(mark, f"the artifact {aid!r}")
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


register(Tool("skill", "Read a file of a skill the user chose for this chat (the system prompt lists them), or its "
              "SKILL.md again. PDF, Word, Excel and PowerPoint files come back as text.",
              obj({"name": {"type": "string", "description": "The skill, as the system prompt names it"},
                   "file": {"type": "string", "description": "A file inside the skill, e.g. references/prices.md"}},
                  ["name"]), skill, available=lambda ctx: bool(_chosen(ctx)), group="knowledge", read_only=True))
register(Tool("skill_propose", "Hand the user a skill you wrote, for them to add to their skills. A skill is a folder "
              "skills/<name>/ in the working folder with SKILL.md - front matter (--- name: ..., description: ... "
              "---) then the instructions - and the files it points to (references/, assets/, scripts/), in the "
              "format Claude uses. name: lowercase letters, digits and single hyphens; description: what it does and "
              "when to use it, at most 1024 characters. This checks the folder and shows the user a card with Add "
              "skill; it says what to fix if something is wrong.",
              obj({"path": {"type": "string", "description": "The skill's folder, e.g. skills/invoice-checker"},
                   "include": {"type": "array", "items": {"type": "string"},
                               "description": "Files in the working folder to put in the skill's assets/ as they are "
                                              "(a template, a spreadsheet)"}}, ["path"]),
              skill_propose, group="knowledge", in_subagent=False))
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
