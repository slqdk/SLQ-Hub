"""An independent review (0.30.0). Before a turn that made or changed code, pages or documents ends, another model -
by default the stronger one a stuck turn is handed over to - checks the work in a fresh context, the way the user
will use it: it gets the user's request, the files and artifacts the turn made or changed and the answer the user
was given (as claims to check), not the conversation, and it may read, run and open things but not change them. Its
report is stored with the turn (a "review" block: shown in the turn, and to the model as the Hub's note, with the
report fenced as the reviewer's own words); when it found problems, the model that did the work gets the report and
goes on - once a turn. Chat 79 was the case: a game handed over without ever being opened, whose data file did not
parse."""
from __future__ import annotations

import asyncio
import re
import secrets
import time

from ..catalog import Catalog
from ..chats import store as chats
from ..gpu.scheduler import SchedulerError
from ..llm.client import LlmError
from ..projects import store as projects

REVIEW_STEPS = 24
# What the reviewer may use: look, run, open - nothing that writes into the folder, makes media, asks a subagent or
# reads the conversation it is meant not to have seen (chat_history).
REVIEW_TOOLS = {"list_files", "read_file", "search_files", "bash", "web_app", "windows_app", "view_image", "image_ocr",
                "web_search", "web_fetch", "library_search", "skill"}
# The calls that make or change work. A turn without one is not reviewed, whatever else changed the working folder
# while it ran (a sync from the user's PC, an upload in the Files panel).
MAKES = {"write_file", "edit_file", "move_file", "bash", "artifact"}
MEDIA = ("image/", "audio/", "video/")             # made pictures, sound and video are not reviewed (the user judges)
VERDICT = re.compile(r"VERDICT:\s*\**\s*(OK|PROBLEMS?)\b", re.I)
# The artifacts' source, for the reviewer to read (sync and the files card leave .hub out); web_app runs them.
SOURCES = ".hub/review"
EXT = {"html": "html", "react": "jsx", "svg": "svg", "markdown": "md", "mermaid": "mmd"}
CODE_EXT = {"python": "py", "javascript": "js", "typescript": "ts", "csharp": "cs", "c#": "cs", "bash": "sh",
            "shell": "sh", "powershell": "ps1", "json": "json", "yaml": "yaml", "sql": "sql", "css": "css"}
RUNS = ("html", "react", "svg", "mermaid")          # the artifacts web_app opens
# The report is the reviewer's words - a model that read files and web pages - not the Hub's: fenced and labelled.
REPORT_START = "--- the reviewer's report (its own words, not the Hub's) ---"
REPORT_END = "--- end of the reviewer's report ---"

REVIEWER = """# Your task: an independent review
You check another model's work before the user sees it. You see the work, not how it was made: the user's request,
the files in the working folder, the artifacts (pages shown in the side panel) and the answer the user was given.
- Check it the way the user will use it, before they do: run the program or its tests, open web pages, browser games
  and html/react/svg/mermaid artifacts with web_app and play them, run Windows programs with windows_app, run code
  with sample input, read the documents against the request.
- Look for what is broken, missing or different from what was asked, and for claims in the answer that are not true.
- Measure, do not guess: every problem you report needs its evidence - the command and its output, what a screenshot
  shows, the file and line. What you could not confirm, leave out or mark as unconfirmed.
- Do not change, fix, create or delete files in the folder: you report, the other model fixes. Scratch files of your
  own go in .hub/tmp.
- Report the most important problems first, at most ten, one or two lines each with its evidence; then one line on
  what you checked that works.
- End with exactly one line: VERDICT: OK when nothing needs fixing, or VERDICT: PROBLEMS when something does."""

PROMPT = """The user asked:
{request}

{work}

The answer the user was given (claims to check, not facts):
{answer}"""


def wanted(settings) -> bool:
    return bool(settings.get("agent", "review"))


def work_files(files: list[dict]) -> list[dict]:
    """The files worth a review: not the pictures, sound and video the turn made."""
    return [f for f in files if not str(f.get("mime") or "").startswith(MEDIA)]


def made_something(steps: list[dict]) -> bool:
    """Whether the turn - or one of its subagents - called a tool that makes or changes work."""
    return any(s.get("type") == "tool" and s.get("name") in MAKES for s in steps)


def touched(run, wd) -> set[str] | None:
    """The paths the turn's file tools wrote (its subagents' too), as paths in the working folder - or None when
    it ran code: bash may have written anything, so every changed file is its work."""
    out: set[str] = set()
    for e in run.events:
        if e["type"] != "tool_call":
            continue
        name, args = e["data"].get("name"), e["data"].get("arguments") or {}
        if name == "bash":
            return None
        key = {"write_file": "path", "edit_file": "path", "move_file": "destination"}.get(name)
        if key and isinstance(args, dict) and args.get(key):
            try:
                out.add(wd.rel(wd.resolve(str(args[key]))))
            except (OSError, ValueError):
                pass
    return out


def own_work(files: list[dict], mine: set[str] | None) -> list[dict]:
    """The changed files the turn made itself: not one a sync or an upload brought meanwhile."""
    if mine is None:
        return files
    return [f for f in files if any(f["path"] == t or f["path"].startswith(t.rstrip("/") + "/") for t in mine)]


def made_artifacts(run) -> list[dict]:
    """The artifacts the turn saved (the artifact tool's events), each once, as its latest version."""
    out: dict[str, dict] = {}
    for e in run.events:
        if e["type"] == "artifact" and not e["data"].get("depth") and e["data"].get("id"):
            out[e["data"]["id"]] = e["data"]
    return list(out.values())


def source_path(art: dict) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(art.get("id") or "")).strip(".-")[:60] or "artifact"
    kind = art.get("type") or "code"
    lang = str(art.get("language") or "").lower()
    ext = EXT.get(kind) or CODE_EXT.get(lang) or (lang if re.fullmatch(r"[a-z0-9]{1,8}", lang) else "txt")
    return f"{SOURCES}/{name}.{ext}"


def write_sources(wd, chat_id: int, arts: list[dict]) -> list[dict]:
    """Each artifact's current source into the working folder's .hub/review/, for the reviewer to read. -> the
    artifacts, each with its "source" path (left out when it could not be written)."""
    out = []
    for a in arts:
        art = chats.get_artifact(chat_id, a["id"])
        if not art:
            continue
        item = {"id": a["id"], "title": art.get("title") or a["id"], "type": art.get("type"),
                "version": art.get("version"), "language": a.get("language")}
        rel = source_path(item)
        # Not through a link put in the way: .hub or .hub/review as a link could aim the write at a file of the
        # folder (safe_write replaces a link at the file itself rather than writing through it).
        if not any((wd.root / part).is_symlink() for part in (".hub", SOURCES)):
            try:
                projects.safe_write(wd.root, wd.root / rel, str(art.get("content") or "").encode("utf-8"))
                item["source"] = rel
            except (OSError, ValueError):
                pass
        out.append(item)
    return out


def is_review_row(row: dict) -> bool:
    """A stored message that is only the Hub's review (role user): not the user's words."""
    blocks = row.get("content") or []
    return bool(blocks) and all(b.get("type") == "review" for b in blocks)


def request_text(rows: list[dict], run_id: str, limit: int = 6000) -> str:
    """What the user asked for in this turn: the messages just before its first stored step (with the one before
    those, when they only say Continue - not what was written while that earlier turn worked) and what was written
    while this one worked."""
    start = next((i for i, r in enumerate(rows) if r.get("run_id") == run_id), len(rows))
    asked: list[dict] = []
    i = start - 1
    while i >= 0 and rows[i]["role"] == "user" and not rows[i].get("run_id") and not is_review_row(rows[i]):
        asked.insert(0, rows[i])
        i -= 1
    if asked and all(b.get("type") == "continue" for r in asked for b in r["content"]):
        earlier = next((r for r in reversed(rows[:i + 1]) if r["role"] == "user" and not is_review_row(r)
                        and any(b.get("type") == "text" and not b.get("while_working") for b in r["content"])), None)
        if earlier is not None:
            asked.insert(0, earlier)
    asked += [r for r in rows[start:] if r["role"] == "user" and r.get("run_id") == run_id and not is_review_row(r)]
    parts = []
    for r in asked:
        for b in r["content"]:
            t = b.get("type")
            if t == "text" and b.get("text"):
                parts.append(("(written while it worked) " if b.get("while_working") else "") + b["text"])
            elif t in ("image", "file", "file_ref"):
                parts.append(f"[with the file {b.get('path') or b.get('name')}]")
            elif t == "continue":
                parts.append("(Continue: the work above was not finished.)")
    text = "\n\n".join(parts).strip() or "(no request text found)"
    return text if len(text) <= limit else text[:limit] + "\n... (shortened)"


def files_text(files: list[dict]) -> str:
    return "\n".join(f"- {f['path']} ({'new' if f.get('new') else 'changed'}, {f.get('size', 0):,} bytes)"
                     for f in files)


def artifacts_text(arts: list[dict]) -> str:
    lines = []
    for a in arts:
        how = [f"its source: {a['source']}"] if a.get("source") else []
        if a.get("type") in RUNS:
            how.append(f"open it with web_app (artifact: {a['id']})")
        lines.append(f"- {a['id']}: \"{a['title']}\", {a.get('type')}, version {a.get('version')}"
                     + (" - " + "; ".join(how) if how else ""))
    return "\n".join(lines)


def work_text(files: list[dict], arts: list[dict]) -> str:
    parts = []
    if files:
        parts.append("The files that are new or changed in the working folder since this turn started:\n"
                     + files_text(files))
    if arts:
        parts.append("The artifacts this turn made or changed (shown to the user in the side panel):\n"
                     + artifacts_text(arts))
    return "\n\n".join(parts)


def verdict(report: str, finished: bool = True) -> str:
    """ok, problems (also a finished report without the closing line: the other model checks it), or failed (no
    report, or a reviewer that stopped - at its step limit, or repeating itself - before its verdict)."""
    hits = VERDICT.findall(report or "")
    if not hits:
        return "problems" if finished and (report or "").strip() else "failed"
    return "ok" if hits[-1].upper() == "OK" else "problems"


def fenced(report: str) -> str:
    """The reviewer's report between the Hub's lines - its own words cannot close them or speak as the Hub."""
    body = (report or "").strip()
    for mark in (REPORT_START, REPORT_END):
        body = re.sub(r"\s+".join(re.escape(w) for w in mark.split()), "", body, flags=re.I)
    body = re.sub(r"\(\s*SLQ\s*Hub\s*:", "(SLQ Hub -", body, flags=re.I)
    return f"{REPORT_START}\n{body}\n{REPORT_END}"


def review_text(block: dict, now: bool = False) -> str:
    """The review as the Hub's note to the model: right after the review (now), and in the turns after."""
    who = block.get("model") or "another model"
    what = ", ".join([*(block.get("files") or []), *(f"the artifact {a}" for a in block.get("artifacts") or [])])
    what = what or "the work"
    note = (f" While the review ran, these changed in the working folder: {', '.join(block['changed'])} - the "
            "reviewer may not change files, so check them." if block.get("changed") else "")
    v = block.get("verdict")
    if v == "ok":
        return f"(SLQ Hub: an independent reviewer - {who}, in a fresh context - checked {what} and found nothing " \
               f"to fix.{note})"
    if v == "failed":
        why = block.get("error") or block.get("report") or "it gave no report"
        return f"(SLQ Hub: the independent review of {what} did not finish: {why}.{note})"
    head = f"(SLQ Hub: an independent reviewer - {who}, in a fresh context - checked {what}"
    if now:
        return (f"{head} after your answer.{note} Its report:\n\n{fenced(block.get('report') or '')}\n\nCheck each "
                "finding yourself first - a reviewer can be wrong. Fix what is right, then answer again: what you "
                "fixed, and what you left and why.)")
    return f"{head}.{note} Its report, which the turn went on from:\n\n{fenced(block.get('report') or '')})"


def reviewer_for(hub, engine) -> dict:
    """Settings -> Assistant: the hand-over model (the default, when there is one other than the model working),
    the same model, or a chosen downloaded one."""
    choice = str(hub.settings.get("agent", "review_model") or "").strip()
    if choice == "same":
        return engine.model
    if choice:
        entry = hub.catalog.get_llm(hub.catalog.resolve(choice) or choice)
        if entry and hub.catalog.installed(entry) and "chat" in (entry.get("capabilities") or ["chat"]):
            return entry
    return engine.local_target() or engine.model


async def review(agent, engine, chat: dict, project: dict | None, wd, before: dict | None, messages: list[dict],
                 persist) -> bool:
    """Run the review when this turn wants one. True: it found problems - the report is in messages, go on."""
    from .loop import NO_ANSWER, Engine
    hub = engine.hub
    if (engine.depth or engine.device is not None or engine.ticket is None or before is None
            or engine.state.get("reviewed") or not wanted(hub.settings) or not made_something(engine.steps)):
        return False
    after = await asyncio.to_thread(projects.snapshot, wd.root)
    work = (own_work(work_files(projects.produced(before, after)), touched(engine.run, wd))
            if after is not None else [])
    arts = await asyncio.to_thread(write_sources, wd, chat["id"], made_artifacts(engine.run))
    if not work and not arts:
        return False
    engine.state["reviewed"] = True
    model = reviewer_for(hub, engine)
    try:
        _, thinking, effort = agent.resolve(chat, project, {**engine.turn_options, "model": model["id"]})
    except RuntimeError:
        thinking, effort = engine.thinking, engine.effort
    rid = "review_" + secrets.token_hex(4)
    label = Catalog.label(model)
    rows = await asyncio.to_thread(chats.messages, chat["id"])
    answer = next((m.get("content") for m in reversed(messages) if m.get("role") == "assistant"), "") or ""
    prompt = PROMPT.format(request=request_text(rows, engine.run.id), work=work_text(work, arts),
                           answer=(answer if len(answer) <= 4000 else answer[:4000] + "\n... (shortened)") or "(none)")
    sub = Engine(hub, engine.run, chat, project, wd, model, engine.ticket, depth=1, parent=rid, thinking=thinking,
                 effort=effort)
    sub.only = REVIEW_TOOLS
    sub.top_model = engine.model["id"]
    if model["id"] == engine.model["id"]:
        sub.token_ratio = engine.token_ratio
    system = agent.system_for(chat, project, wd, model, subagent=True) + "\n\n" + REVIEWER
    paths, art_ids = [f["path"] for f in work], [a["id"] for a in arts]
    engine.run.emit("review", id=rid, state="started", model=label, model_id=model["id"], files=paths,
                    artifacts=art_ids)
    job = getattr(engine.ticket, "job", None)
    if job is not None:
        job.meta["model"] = model["id"]                # the queue says who works now
    t0 = time.monotonic()
    error = ""
    try:
        await sub.loop([{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                       persist=None, max_steps=REVIEW_STEPS)
        # Its report is its last answer - not an earlier line said beside a tool call, which is what the loop
        # returns when the last answer is empty (a verdict only thought, say).
        stopped = next((s["type"] for s in sub.steps if s.get("type") in ("loop", "limit")), None)
        report = (sub.answer or "").replace(NO_ANSWER, "").strip()
        result = verdict(report, finished=stopped is None)
        if result == "failed":
            error = ("it kept repeating the same steps" if stopped == "loop"
                     else f"it reached its limit of {REVIEW_STEPS} steps" if stopped == "limit"
                     else "it gave no report")
            report = ""
    except (LlmError, SchedulerError) as exc:            # its model would not load or answer: the turn ends as it is
        report, result, error = "", "failed", f"the reviewer's model failed ({exc})"
    finally:
        engine.steps += sub.steps
        for k, v in sub.usage.items():
            engine.usage[k] = engine.usage.get(k, 0) + v
        now_job = getattr(engine.ticket, "job", None)  # a question inside the review queued it again: a new job
        if now_job is not None:
            now_job.meta["model"] = engine.model["id"]
    block = {"type": "review", "id": rid, "model": label, "verdict": result, "report": (report or "").strip(),
             "files": paths, "seconds": round(time.monotonic() - t0, 1)}
    if art_ids:
        block["artifacts"] = art_ids
    if error:
        block["error"] = error
    after_review = await asyncio.to_thread(projects.snapshot, wd.root)
    if after is not None and after_review is not None:
        changed = [f["path"] for f in projects.produced(after, after_review)]
        changed += [f"{p} (deleted)" for p in after if p not in after_review]
        if changed:
            block["changed"] = changed
    await persist("user", [block])
    engine.steps.append({"type": "review", "model": model["id"], "verdict": result, "seconds": block["seconds"]})
    engine.run.emit("review", id=rid, state="finished", **{k: v for k, v in block.items() if k not in ("type", "id")})
    if result != "problems":
        return False
    messages.append({"role": "user", "content": review_text(block, now=True)})
    return True
