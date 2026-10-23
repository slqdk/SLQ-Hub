"""A chat, exported.

* Markdown: the conversation to read or keep - what was said, the thinking
  folded away, tool calls and their results shortened.
* A troubleshooting bundle (ZIP): everything the Hub knows about the chat,
  for finding out why it behaved as it did - the stored messages and runs,
  the live event log of the runs still in memory, what the model would be
  sent next (system prompt, history after condensing, tools, the request),
  the settings that shape a turn (never a key or a token), the queue around
  the runs, the model server's recent log, the chat's pictures and
  attachments, and the files its turns made or changed (as they are now) -
  a song, a mix, a script that failed.
"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile
from datetime import datetime, timezone

from .. import db
from ..projects import blobs
from . import store

FORMAT = 1
MAX_FILES_BYTES = 60 * 1024 * 1024        # pictures, attachments and the turns' files in one bundle
MAX_FILE_BYTES = 25 * 1024 * 1024
RESULT_CHARS = 1500                        # a tool result in the transcript
ARG_CHARS = 300                            # a long string in a tool call's arguments, in the transcript
SAFE_SECTIONS = ("models", "queue", "agent", "web", "sandbox", "images", "sound", "libraries")
STORED = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp3", ".ogg", ".opus", ".flac", ".m4a", ".mp4", ".webm", ".zip",
          ".gz", ".xz", ".7z")                   # compressed already: deflating them again only costs time
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif", "application/pdf": ".pdf",
       "text/plain": ".txt", "text/markdown": ".md", "application/json": ".json"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(text: str) -> str:
    t = (text or "").lower()
    for a, b in (("æ", "ae"), ("ø", "oe"), ("å", "aa"), ("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:40].rstrip("-") or "chat"


def filename(chat: dict, ext: str) -> str:
    return f"chat-{chat['id']}-{slug(chat.get('title') or '')}-{datetime.now(timezone.utc):%Y-%m-%d}.{ext}"


def _when(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def _fence(text: str, lang: str = "") -> str:
    ticks = "`" * max(3, 1 + max((len(m) for m in re.findall(r"`+", text)), default=0))
    return f"{ticks}{lang}\n{text.rstrip(chr(10))}\n{ticks}"


def _short_args(raw: str) -> str:
    """A tool call's arguments with long strings shortened (the bundle has them whole)."""
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        return raw if len(raw) <= ARG_CHARS * 2 else raw[:ARG_CHARS * 2] + f"... ({len(raw)} characters)"

    def cut(v):
        if isinstance(v, str) and len(v) > ARG_CHARS:
            return v[:ARG_CHARS] + f"... ({len(v)} characters)"
        if isinstance(v, dict):
            return {k: cut(x) for k, x in v.items()}
        if isinstance(v, list):
            return [cut(x) for x in v]
        return v
    return json.dumps(cut(args), ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- files the chat points at

def referenced_files(rows: list[dict]) -> list[dict]:
    """Pictures and attachments in the chat's messages, once each, in order."""
    seen: dict[str, dict] = {}

    def add(sha: str | None, name: str | None, mime: str | None, origin: str) -> None:
        if sha and len(sha) == 64 and sha not in seen:
            seen[sha] = {"sha": sha, "name": name or "", "mime": mime or "", "origin": origin}

    for r in rows:
        for b in r["content"]:
            t = b.get("type")
            if t in ("image", "file", "file_ref"):
                add(b.get("sha"), b.get("path") or b.get("name"), b.get("mime"), r["role"])
            elif t == "tool_result":
                disp = b.get("display") or {}
                for img in [*(b.get("images") or []), *(disp.get("images") or [])]:
                    add(img.get("sha"), img.get("caption"), img.get("mime"), f"tool {b.get('name')}")
    for f in seen.values():
        up = store.get_upload(f["sha"]) or {}
        f["name"] = f["name"] or up.get("filename") or ""
        f["mime"] = f["mime"] or up.get("mime") or ""
        f["size"] = up.get("size")
    return list(seen.values())


def made_files(runs: list[dict]) -> list[dict]:
    """The files the turns made or changed in the working folder (each run's "files" step, as the card under the
    answer showed them), the newest turn first, once each."""
    seen: dict[str, dict] = {}
    for r in reversed(runs):                    # runs_for_chat gives them in the order they ran
        for step in r.get("steps") or []:
            if step.get("type") != "files":
                continue
            for f in step.get("files") or []:
                path = str(f.get("path") or "")
                if path and path not in seen:
                    seen[path] = {"path": path, "run": r.get("id"), "new": bool(f.get("new", True))}
    return list(seen.values())


def _zip_name(f: dict) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", (f.get("name") or "").replace("\\", "/").split("/")[-1])[:60].strip("._")
    if not base or "." not in base:
        base = (base or "file") + EXT.get(f.get("mime") or "", "")
    return f"files/{f['sha'][:12]}-{base}"


# ---------------------------------------------------------------- the transcript

def transcript(chat: dict, project: dict | None, rows: list[dict], runs: list[dict], version: str,
               paths: dict[str, str] | None = None) -> str:
    """The conversation in Markdown. With `paths` (sha -> path in the bundle) pictures are linked."""
    paths = paths or {}
    models = sorted({r["model"] for r in runs if r.get("model")})
    head = [f"Exported from SLQ Hub {version} on {_when(now_iso())}", f"chat {chat['id']}"]
    if project:
        head.append(f'project "{project["name"]}"')
    if models:
        head.append("model " + ", ".join(models))
    out = [f"# {chat.get('title') or 'Chat'}", "", ", ".join(head) + "."]
    if chat.get("summary"):
        out += ["", "> **Condensed** - the model reads this summary instead of the messages before message "
                f"{chat.get('summary_upto')}:", ">"] + [f"> {line}" for line in chat["summary"].splitlines()]

    def picture(sha: str | None, label: str) -> str:
        if sha and sha in paths:
            return f"![{label}]({paths[sha]})"
        return f"*(picture: {label})*"

    by_run = {r["id"]: r for r in runs}
    section: str | None = None             # the run of the open assistant section

    def close_section() -> None:
        nonlocal section
        if section is None:
            return
        r = by_run.get(section)
        if r:
            steps = r.get("steps") or []
            models_used = sorted({s.get("model") for s in steps if s.get("type") == "model" and s.get("model")})
            n_model = sum(1 for s in steps if s.get("type") == "model")
            tools = sum(1 for s in steps if s.get("type") == "tool")
            u = r.get("usage") or {}
            secs = ""
            try:
                if r.get("ended_at"):
                    secs = f", {(datetime.fromisoformat(r['ended_at']) - datetime.fromisoformat(r['started_at'])).total_seconds():.0f} s"
            except ValueError:
                pass
            line = (f"*Turn {r['status']}: {n_model} model step{'s' if n_model != 1 else ''}, {tools} tool "
                    f"call{'s' if tools != 1 else ''}{secs}, {', '.join(models_used) or r.get('model', '')}; "
                    f"tokens {u.get('prompt_tokens', 0)} in ({u.get('cached_tokens', 0)} cached), "
                    f"{u.get('completion_tokens', 0)} out.*")
            out.extend(["", line])
            made = [f for s in steps if s.get("type") == "files" for f in s.get("files") or []]
            if made:
                out.extend(["", "*Files from this turn: " + ", ".join(
                    f"`{f['path']}` ({'new' if f.get('new', True) else 'changed'}, {f.get('size', 0)} bytes)"
                    for f in made) + ".*"])
            if r.get("error"):
                out.extend(["", f"**Error:** {r['error']}"])
        section = None

    for row in rows:
        role, blocks = row["role"], row["content"]
        if role == "user":
            close_section()
            out += ["", "---", "", f"## You - {_when(row['created_at'])}", ""]
            for b in blocks:
                t = b.get("type")
                if t == "text":
                    if b.get("while_working"):
                        out.append("*(sent while it worked)*")
                    out += [b.get("text", ""), ""]
                elif t == "image":
                    out += [picture(b.get("sha"), b.get("name") or b.get("path") or "image"), ""]
                elif t == "file":
                    out += [f"*(attached file: {b.get('path') or b.get('name')}, {_size(int(b.get('size') or 0))})*", ""]
                elif t == "file_ref":
                    extra = f"{b['lines']} lines" if b.get("lines") else _size(int(b.get("size") or 0))
                    out += [f"*(file from the folder: {b.get('path')}, {extra})*", ""]
            continue
        if row.get("run_id") != section or section is None:
            close_section()
            section = row.get("run_id") or f"none-{row['id']}"
            out += ["", f"## Assistant - {_when(row['created_at'])}", ""]
        for b in blocks:
            t = b.get("type")
            if t == "notice" and b.get("text"):
                out += [f"*(SLQ Hub: {b['text']})*", ""]
            elif t == "thinking" and b.get("text"):
                out += ["<details><summary>Thinking</summary>", "", b["text"].strip(), "", "</details>", ""]
            elif t == "text" and b.get("text"):
                out += [b["text"], ""]
            elif t == "tool_call":
                out += [f"**Tool call** `{b.get('name')}`", "", _fence(_short_args(b.get("arguments") or ""), "json"), ""]
            elif t == "tool_result":
                text = b.get("text") or ""
                more = f"\n... ({len(text) - RESULT_CHARS} more characters)" if len(text) > RESULT_CHARS else ""
                label = f"**Result** `{b.get('name')}`" + (" (error)" if b.get("is_error") else "")
                out += [label, "", _fence(text[:RESULT_CHARS] + more), ""]
                disp = b.get("display") or {}
                for img in [*(b.get("images") or []), *(disp.get("images") or [])]:
                    out += [picture(img.get("sha"), img.get("caption") or "picture"), ""]
                if disp.get("artifact"):
                    a = disp["artifact"]
                    out += [f"*(artifact: {a.get('title')}, version {a.get('version')})*", ""]
    close_section()
    return "\n".join(out).rstrip() + "\n"


def markdown(chat: dict, version: str) -> str:
    project = _project(chat)
    return transcript(chat, project, store.messages(chat["id"]), store.runs_for_chat(chat["id"]), version)


def _project(chat: dict) -> dict | None:
    if not chat.get("project_id"):
        return None
    from ..projects import store as projects
    return projects.get_project(chat["project_id"])


# ---------------------------------------------------------------- the bundle

def strip_images(obj):
    """Data URLs (pictures in the model's messages) replaced by a note of their size."""
    if isinstance(obj, str):
        if obj.startswith("data:") and len(obj) > 200:
            head = obj.split(",", 1)[0]
            return f"{head},... ({_size(len(obj) * 3 // 4)} picture, left out)"
        return obj
    if isinstance(obj, dict):
        return {k: strip_images(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_images(v) for v in obj]
    return obj


# Known noise in llama-server's log: vision models (Qwen-VL) warn about the token positions of every batch of
# image tokens, which pushes everything else out of the tail.
LOG_NOISE = re.compile(r"find_slot: non-consecutive token position")
LOG_LINES = 800


def quiet_log(text: str, keep: int = LOG_LINES) -> str:
    """The model server's log tail without its known noise (counted instead of listed)."""
    lines = text.splitlines()
    kept = [line for line in lines if not LOG_NOISE.search(line)]
    left_out = len(lines) - len(kept)
    head = [f"({left_out} \"non-consecutive token position\" warnings left out: harmless, they come from the image "
            "tokens of vision models.)"] if left_out else []
    return "\n".join(head + kept[-keep:]) + ("\n" if kept else "")


def merge_events(events: list[dict]) -> list[dict]:
    """A run's event log with the streamed pieces (text, thinking, tool
    arguments) joined, so it reads as steps rather than thousands of deltas."""
    out: list[dict] = []
    for ev in events:
        kind, data = ev["type"], ev.get("data") or {}
        if kind in ("text", "thinking", "tool_args") and out:
            prev = out[-1]
            pd = prev.get("data") or {}
            same = (prev["type"] == kind and pd.get("depth") == data.get("depth") and pd.get("parent") == data.get("parent")
                    and pd.get("id") == data.get("id"))
            if same:
                key = "delta" if kind == "tool_args" else "text"
                pd[key] = (pd.get(key) or "") + (data.get(key) or "")
                prev["seq_to"], prev["t_to"] = ev["seq"], ev["t"]
                prev["pieces"] = prev.get("pieces", 1) + 1
                continue
        out.append({"seq": ev["seq"], "type": kind, "t": ev["t"], "data": dict(data)})
    return out


def _settings(hub) -> dict:
    s = hub.settings
    out = {name: s.get(name) for name in SAFE_SECTIONS}
    cloud = s.get("cloud") or {}
    out["cloud"] = {k: cloud.get(k) for k in ("provider", "model", "base_url", "max_tokens")}
    out["cloud"]["api_key_set"] = bool(cloud.get("api_key"))
    mcp = hub.extras.get("mcp")
    out["mcp"] = [{**{k: v for k, v in m.items() if k != "error"}, "error": (m.get("error") or "")[:300]}
                  for m in (mcp.status() if mcp is not None else [])]
    return out


def _queue_around(runs: list[dict]) -> list[dict]:
    """The queue's finished jobs while the chat's runs went on (the queue keeps
    monotonic times; they are turned into clock times here)."""
    spans = []
    for r in runs:
        try:
            start = datetime.fromisoformat(r["started_at"]).timestamp() - 120
            end = datetime.fromisoformat(r["ended_at"]).timestamp() + 30 if r.get("ended_at") else time.time()
        except (TypeError, ValueError):
            continue
        spans.append((start, end))
    if not spans:
        return []
    offset = time.time() - time.monotonic()
    lo, hi = min(s for s, _ in spans) - offset, max(e for _, e in spans) - offset
    with db.connect() as con:
        rows = con.execute("SELECT * FROM jobs_log WHERE enqueued_at BETWEEN ? AND ? ORDER BY enqueued_at LIMIT 500",
                           (lo, hi)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        enq = d["enqueued_at"] + offset
        if not any(s <= enq <= e for s, e in spans):
            continue
        for k in ("enqueued_at", "started_at", "ended_at"):
            if d.get(k) is not None:
                d[k] = datetime.fromtimestamp(d[k] + offset, timezone.utc).isoformat(timespec="seconds")
        if d.get("started_at") and d.get("enqueued_at"):
            d["waited_s"] = round(r["started_at"] - r["enqueued_at"], 2)
        if d.get("ended_at") and r["started_at"] is not None:
            d["ran_s"] = round(r["ended_at"] - r["started_at"], 2)
        out.append(d)
    return out


def _context(hub, agent, chat: dict, project: dict | None) -> dict:
    """What the model would be sent if a message were sent now."""
    from ..agent import cloud as cloudmod
    from ..agent import context as C
    from ..agent.loop import Engine, vision
    from ..agent.runs import Run
    from ..llm import payload as P
    try:
        entry, thinking, effort = agent.resolve(chat, project, {})
    except RuntimeError as exc:
        return {"error": str(exc)}
    wd = agent.workdir(chat)
    escalation = bool(project and project["settings"].get("cloud_escalation") and cloudmod.configured(hub.settings))
    engine = Engine(hub, Run(chat["id"]), chat, project, wd, entry, None, thinking=thinking, effort=effort,
                    escalation=escalation)
    system = agent.system_for(chat, project, wd, entry, handover_to=engine.handover_label())
    rows = store.messages(chat["id"], after_id=int(chat.get("summary_upto") or 0))
    keep_from = rows[-12]["id"] if len(rows) > 12 else 0
    messages = [{"role": "system", "content": system},
                *C.to_model_messages(rows, None, keep_from, vision=vision(entry))]
    tools = engine.tools()
    schemas = engine.schemas(tools)
    ctx = hub.catalog.context_per_request(entry["id"])
    used = C.estimate_tokens(messages)
    request = P.build(entry, messages, tools=schemas or None, thinking=thinking, effort=effort,
                      sampling={"max_tokens": max(1024, min(32768, ctx - used))})
    return strip_images({
        "note": "What the model would be sent if a message were sent now, with today's settings and skills. "
                "Older messages are condensed into the chat's summary (in the system prompt).",
        "model": entry["id"], "thinking": thinking, "effort": effort, "escalation": escalation,
        "hand_over_to": engine.handover_label(),
        "context_per_request": ctx, "estimated_tokens": used,
        "compact_at": hub.settings.get("agent", "compact_at"),
        "messages": messages,
        "tools": [{"name": t.name, "description": (getattr(t, "description", "") or "")[:300]} for t in tools],
        "request_without_messages": {k: v for k, v in request.items() if k != "messages"},
    })


async def bundle(hub, agent, chat: dict) -> bytes:
    """The troubleshooting ZIP for one chat."""
    project = _project(chat)
    rows = store.messages(chat["id"])
    runs = store.runs_for_chat(chat["id"])
    version = hub.env.version
    try:
        context = _context(hub, agent, chat, project)
    except Exception as exc:  # noqa: BLE001 - the rest of the bundle is still worth having
        context = {"error": f"{type(exc).__name__}: {exc}"}
    live = [r for r in agent.runs.runs.values() if r.chat_id == chat["id"]]
    log_text = quiet_log(await hub.runner.logs("main", 3000))
    entry = hub.catalog.get_llm(context.get("model") or "") if isinstance(context, dict) else None
    model_card = dict(entry or {})
    if entry:
        model_card["context_per_request"] = hub.catalog.context_per_request(entry["id"])
    artifacts = []
    for a in store.list_artifacts(chat["id"]):
        versions = []
        for v in range(1, int(a["version"]) + 1):
            got = store.get_artifact(chat["id"], a["id"], v)
            if got:
                versions.append({"version": v, "created_at": got["created_at"], "content": got["content"]})
        artifacts.append({"id": a["id"], "title": a["title"], "type": a["type"], "versions": versions})
    files = referenced_files(rows)
    made = made_files(runs)
    wd = agent.workdir(chat)

    def build() -> bytes:
        buf = io.BytesIO()
        paths: dict[str, str] = {}
        total = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                size = int(f.get("size") or 0)
                if not blobs.exists(f["sha"]):
                    f["skipped"] = "not on the Hub any more"
                    continue
                if size > MAX_FILE_BYTES or total + size > MAX_FILES_BYTES:
                    f["skipped"] = "too big for the bundle"
                    continue
                data = blobs.read(f["sha"])
                total += len(data)
                name = _zip_name(f)
                packed = f["mime"].startswith(("image/png", "image/jpeg", "image/webp", "image/gif"))
                z.writestr(name, data, compress_type=zipfile.ZIP_STORED if packed else zipfile.ZIP_DEFLATED)
                f["path"] = paths[f["sha"]] = name
            for f in made:                      # as they are in the folder now: a later turn may have changed them
                try:
                    p = wd.resolve(f["path"])
                    size = p.stat().st_size if p.is_file() else -1
                except (OSError, ValueError):
                    size = -1
                if size < 0:
                    f["skipped"] = "not in the folder any more"
                    continue
                f["size"] = size
                if size > MAX_FILE_BYTES or total + size > MAX_FILES_BYTES:
                    f["skipped"] = "too big for the bundle"
                    continue
                data = p.read_bytes()
                total += len(data)
                f["zip"] = "made/" + wd.rel(p)
                z.writestr(f["zip"], data, compress_type=zipfile.ZIP_STORED if p.suffix.lower() in STORED
                           else zipfile.ZIP_DEFLATED)
            chat_json = {
                "exported": {"app": "SLQ Hub", "version": version, "at": now_iso(), "format": FORMAT},
                "chat": chat,
                "project": {k: project.get(k) for k in ("id", "name", "instructions", "settings", "created_at")}
                if project else None,
                "model": model_card or None,
                "loaded_now": hub.loaded_model(),
                "settings": _settings(hub),
                "messages": rows,
                "runs": runs,
                "prompts": store.prompts_for_chat(chat["id"]),
                "runs_in_memory": [{"id": r.id, "status": r.status, "events": len(r.events),
                                    "started": datetime.fromtimestamp(r.started, timezone.utc).isoformat(timespec="seconds"),
                                    "ended": datetime.fromtimestamp(r.ended, timezone.utc).isoformat(timespec="seconds")
                                    if r.ended else None} for r in live],
                "artifacts": artifacts,
                "files": files,
                "made": made,
            }
            z.writestr("chat.json", json.dumps(chat_json, ensure_ascii=False, indent=1))
            z.writestr("context.json", json.dumps(context, ensure_ascii=False, indent=1))
            z.writestr("transcript.md", transcript(chat, project, rows, runs, version, paths))
            for r in live:
                z.writestr(f"events/{r.id}.json", json.dumps(
                    {"run": r.id, "status": r.status, "inbox_waiting": len(r.inbox), "events": merge_events(r.events)},
                    ensure_ascii=False, indent=1))
            z.writestr("logs/queue.json", json.dumps(_queue_around(runs), ensure_ascii=False, indent=1))
            z.writestr("logs/llama-server.txt", log_text)
            z.writestr("README.txt", README.format(id=chat["id"], title=chat.get("title") or "", at=_when(now_iso()),
                                                   version=version, runs=len(live)))
        return buf.getvalue()

    import asyncio
    return await asyncio.to_thread(build)


README = """SLQ Hub - chat export for troubleshooting
Chat {id} "{title}", exported {at} from SLQ Hub {version}.

transcript.md          the conversation, readable (tool results shortened)
chat.json              everything stored about the chat: every message block (text,
                       thinking, tool calls and their full results), the runs (steps,
                       timings, tokens, errors; each model step with what it sent -
                       sampling, thinking, tools - and why it stopped), the system
                       prompt and tools each turn's model got (prompts), artifacts
                       with all versions, the model card, and the settings that shape
                       a turn (no keys or tokens)
context.json           what the model would be sent if a message were sent now: system
                       prompt, the history after condensing, the tools and the request
events/<run>.json      the live event log of the {runs} run(s) still in the Hub's memory
                       (runs that ended in the last hour), streamed pieces joined
logs/queue.json        the queue's jobs while the chat's runs went on
logs/llama-server.txt  the model server's recent output
files/                 the chat's pictures and attachments
made/                  the files the turns made or changed in the working folder, as
                       they are there now (chat.json "made" says which were left out)
"""
