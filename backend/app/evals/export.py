"""A test run, exported for a closer look (Settings -> Tests -> Export): a ZIP with an overview to read, the results
for programs, and per case its question, what a good answer does, the checks, the answer, the turn's events, what the
sandbox's checks got, and the case's chat as its troubleshooting export (messages, runs, attachments, the files and
pictures the turn made)."""
from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from ..chats import export as chat_export
from ..chats import store as chats
from .runner import area_title, summary
from .suite import BY_ID

MAX_FILE = 25 * 1024 * 1024

README = """# SLQ Hub test run

Made by Settings -> Tests. Each case was a real chat on this Hub - the same agent, queue, tools and sandbox as any
chat - with a hard task in one area. The Hub checked what it could by machine; how a picture looks or a voice sounds
is for a person (each case says what to look at).

- summary.md: the whole run at a glance, then each case: what it asked, what a good answer does, the checks, the
  answer.
- results.json: the same, for programs.
- cases/NN-<case>/
  - case.json: the case and its result (checks, time, model, tools, what the turn asked and was answered)
  - answer.md: the final answer
  - events.json: the turn's events, as the chat page saw them live
  - input.json: what the sandbox's checks were given (files, pictures, the expected values)
  - chat/: the case's chat as "Export for troubleshooting" makes it - messages, runs, the model's steps,
    attachments and the files and pictures the turn made

Results: pass (every check held), partial (the must-checks held, a should-check did not), fail (a must-check did
not hold), error (the turn did not finish: an error, or the case's time ran out), skipped (this Hub lacks what the
case needs), stopped.
"""


def _duration(seconds: float | None) -> str:
    s = int(round(seconds or 0))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s} s"
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


def _fence(text: str) -> str:
    longest = max((len(run) for run in text.split("\n") if set(run) == {"~"}), default=0)
    ticks = "~" * max(4, longest + 1)
    return f"{ticks}\n{text.rstrip()}\n{ticks}"


def summary_md(rec: dict) -> str:
    s = summary(rec)
    c = s["counts"]
    thinking = {True: "on", False: "off", None: "as set for chats"}[rec["options"].get("thinking")]
    queued = sum(r.get("queue_seconds") or 0 for r in rec["results"])
    lines = [f"# SLQ Hub tests - {rec['created_at'][:16].replace('T', ' ')} UTC", "",
             f"- Model: {s['model_name']} ({rec['model']}), thinking: {thinking}",
             f"- Hub: {rec['options'].get('version', '?')}",
             f"- Run {rec['id']}: {rec['status']}; {s['done']} of {s['total']} cases: {c['pass']} passed, "
             f"{c['partial']} in part, {c['fail']} failed, {c['error']} errors, {c['skipped']} skipped"
             + (f", {c['stopped']} stopped" if c.get("stopped") else ""),
             f"- Time: {_duration(s['seconds'])} (of that, {_duration(queued)} waiting for the card)", "",
             "| # | Area | Case | Result | Checks | Time | Not held |", "|---|---|---|---|---|---|---|"]
    for k, r in enumerate(rec["results"], 1):
        bad = [x["name"] for x in r.get("checks", []) if x["ok"] is False]
        score = r.get("score") or [0, 0]
        lines.append(f"| {k} | {area_title(r['area'])} | {r['title']} | {r['status']} | {score[0]}/{score[1]} | "
                     f"{_duration(r.get('seconds'))} | {'; '.join(bad) or r.get('why', '')} |".replace("\n", " "))
    for k, r in enumerate(rec["results"], 1):
        case = BY_ID.get(r["id"])
        lines += ["", f"## {k}. {area_title(r['area'])} - {r['title']}: {r['status']}", ""]
        if r.get("why"):
            lines += [f"Why: {r['why']}", ""]
        if case:
            lines += [f"Asked{' (chat ' + str(r['chat_id']) + ')' if r.get('chat_id') else ''}:", "",
                      *[("> " + x) if x else ">" for x in case.prompt.splitlines()], "",
                      f"A good answer: {case.expect}", ""]
        if r.get("checks"):
            lines.append("Checks:")
            for x in r["checks"]:
                mark = {True: "[x]", False: "[ ]", None: "(i)"}[x["ok"]]
                lines.append(f"- {mark} {x['level']}: {x['name']}" + (f" - {x['detail']}" if x.get("detail") else ""))
            lines.append("")
        if r.get("chat_id"):
            u = r.get("usage") or {}
            parts = [f"waiting {_duration(r.get('queue_seconds'))}"]
            if r.get("load_seconds"):
                parts.append(f"loading models {_duration(r['load_seconds'])}")
            parts.append(f"the model {_duration(r.get('model_seconds'))} in {r.get('model_steps', 0)} steps"
                         + (f" at {r['tps']} tokens/s" if r.get("tps") else ""))
            if r.get("tool_seconds"):
                parts.append(f"tools {_duration(r['tool_seconds'])}")
            tokens = (f"; tokens: {u.get('prompt_tokens', 0):,} in ({u.get('cached_tokens', 0):,} from the cache), "
                      f"{u.get('completion_tokens', 0):,} out") if u else ""
            lines.append(f"Model: {r.get('model') or '?'}; {_duration(r.get('seconds'))}: {', '.join(parts)}{tokens}")
            for h in r.get("handed_over") or []:
                lines.append(f"Handed over from {h.get('from')} to {h.get('to')}: {h.get('reason')}")
            for a in r.get("approvals") or []:
                lines.append(f"It asked ({a['tool']}, {a['what']}): {a['asked'][:160]} - answered {a['answer']} "
                             f"({a['why']})")
            if r.get("files"):
                lines.append("Files it made: " + ", ".join(r["files"][:20]))
            lines.append("")
        if r.get("review"):
            lines += [f"Look at: {r['review']}", ""]
        if r.get("answer"):
            lines += ["Answer:", "", _fence(r["answer"]), ""]
    return "\n".join(lines).rstrip() + "\n"


def _read(wd, rel: str) -> bytes | None:
    """A file of a case's folder, if it is one inside it (a link the model planted is not followed out)."""
    try:
        p = wd.resolve(rel)
    except ValueError:
        return None
    if not p.is_file() or p.stat().st_size > MAX_FILE:
        return None
    return p.read_bytes()


def _copy(zf: zipfile.ZipFile, inner: bytes, prefix: str) -> None:
    with zipfile.ZipFile(io.BytesIO(inner)) as z:
        for item in z.infolist():
            if item.is_dir():
                continue
            zf.writestr(zipfile.ZipInfo(prefix + item.filename, date_time=item.date_time), z.read(item),
                        compress_type=item.compress_type)


async def build(hub, rec: dict) -> Path:
    """The ZIP, in a temporary file (the caller deletes it once sent)."""
    import asyncio
    agent = hub.extras["agent"]
    tmp_dir = hub.env.data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=tmp_dir, prefix="tests-", suffix=".zip")
    os.close(fd)
    path = Path(name)
    base = f"slq-hub-tests-{rec['created_at'][:16].replace(':', '').replace('T', '-')}/"
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(base + "README.md", README)
            zf.writestr(base + "summary.md", summary_md(rec))
            zf.writestr(base + "results.json", json.dumps(
                {"run": rec, "cases": [BY_ID[i].info() for i in rec["cases"] if i in BY_ID]}, indent=1,
                ensure_ascii=False))
            for k, r in enumerate(rec["results"], 1):
                folder = f"{base}cases/{k:02d}-{r['id']}/"
                case = BY_ID.get(r["id"])
                zf.writestr(folder + "case.json", json.dumps({"case": case.info() if case else None, "result": r},
                                                             indent=1, ensure_ascii=False))
                if r.get("answer"):
                    zf.writestr(folder + "answer.md", r["answer"])
                chat = chats.get_chat(r["chat_id"]) if r.get("chat_id") else None
                if chat is None:
                    continue
                wd = agent.workdir(chat)
                for rel in (".hub/tests/events.json", ".hub/tests/input.json"):
                    data = _read(wd, rel)
                    if data is not None:
                        zf.writestr(folder + rel.rsplit("/", 1)[-1], data)
                try:
                    inner = await chat_export.bundle(hub, agent, chat)
                except Exception as exc:  # noqa: BLE001 - the rest of the export is still worth having
                    zf.writestr(folder + "chat-export-failed.txt", f"{type(exc).__name__}: {exc}\n")
                    continue
                await asyncio.to_thread(_copy, zf, inner, folder + "chat/")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def filename(rec: dict) -> str:
    return f"slq-hub-tests-{rec['created_at'][:10]}-{rec['id'][-6:]}.zip"
