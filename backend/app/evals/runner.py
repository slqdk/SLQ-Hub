"""Settings -> Tests: test cases run as real chats, one after another, and checked.

A run is a row in test_runs; each case gets a chat of its own (chats.test_run is the run's id: not in the chat list,
deleted with the run). A case's turn goes the way any chat's turn goes - the agent, the queue (at background
priority, so a person's chats go first), the tools, the sandbox - with the model the run chose. When the turn asks
first (internet for the assistant, a tool that needs approval), the run answers as a careful user would: Allow once
for a case that is about the web, and for code the sandbox cannot keep offline unless the code asks for the
internet; otherwise Deny. Then the case's checks: the Hub's on the answer, the sandbox's on what the turn made.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shlex
import shutil
import time
from pathlib import Path

from .. import db
from ..chats import store as chats
from ..projects import blobs
from .checks import C, Turn, tools_ok, verdict
from .suite import AREAS, BY_ID, CASES, Case, Inputs

log = logging.getLogger("hub.tests")

CHECK_LIB = Path(__file__).with_name("check_lib.py")
ANSWER_CHARS = 8000
STATUSES = ("pass", "partial", "fail", "error", "skipped", "stopped")


def _now() -> str:
    return db.utcnow()


def _row(r) -> dict:
    d = dict(r)
    for k, empty in (("options", "{}"), ("cases", "[]"), ("results", "[]")):
        try:
            d[k] = json.loads(d.get(k) or empty)
        except ValueError:
            d[k] = json.loads(empty)
    return d


def summary(rec: dict) -> dict:
    counts = {s: 0 for s in STATUSES}
    for r in rec["results"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {k: rec[k] for k in ("id", "created_at", "ended_at", "status", "model")} | {
        "model_name": rec["options"].get("model_name", rec["model"]), "thinking": rec["options"].get("thinking"),
        "total": len(rec["cases"]), "done": len(rec["results"]), "counts": counts,
        "seconds": round(sum(r.get("seconds") or 0 for r in rec["results"]), 1)}


def activity(events: list[dict]) -> str:
    """What a case's turn is doing now, in a few words (from its latest events)."""
    for ev in reversed(events[-80:]):
        kind, d = ev["type"], ev.get("data") or {}
        if kind in ("image_progress", "audio_progress", "video_progress") and d.get("max"):
            return f"{kind.split('_')[0]} {round(100 * (d.get('value') or 0) / d['max'])}%"
        if kind == "tool_call":
            return str(d.get("name") or "a tool")
        if kind == "tool_result":
            return f"{d.get('name')} {'done' if d.get('ok') else 'failed'}"
        if kind == "approval_needed":
            return f"asked about {d.get('tool')}"
        if kind == "escalated":
            return f"handed over to {d.get('model')}"
        if kind == "thinking":
            return "thinking"
        if kind == "text":
            return "writing"
        if kind == "queued":
            return "waiting for the card" + (f" (place {d['position']})" if d.get("position") else "")
        if kind == "started":
            return "started"
    return "starting"


class TestRuns:
    def __init__(self, hub):
        self.hub = hub
        self.task: asyncio.Task | None = None
        self.live: dict | None = None
        self._stop = False

    # ------------------------------------------------------------ stored runs

    def runs(self, limit: int = 30) -> list[dict]:
        with db.connect() as con:
            rows = con.execute("SELECT * FROM test_runs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                               (limit,)).fetchall()
        return [summary(_row(r)) for r in rows]

    def get(self, run_id: str) -> dict | None:
        with db.connect() as con:
            r = con.execute("SELECT * FROM test_runs WHERE id=?", (run_id,)).fetchone()
        return _row(r) if r else None

    def _save(self, run_id: str, **fields) -> None:
        for k in ("options", "cases", "results"):
            if k in fields:
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in fields)
        with db.connect() as con:
            con.execute(f"UPDATE test_runs SET {sets} WHERE id=?", (*fields.values(), run_id))

    def running(self) -> str | None:
        return self.live["run"] if self.live and self.task and not self.task.done() else None

    def interrupted(self) -> None:
        """At start-up: a run the last stop of the Hub cut short."""
        with db.connect() as con:
            con.execute("UPDATE test_runs SET status='interrupted', ended_at=? WHERE status='running'", (_now(),))

    def delete(self, run_id: str) -> bool:
        if self.running() == run_id:
            raise RuntimeError("Stop the run first")
        agent = self.hub.extras["agent"]
        with db.connect() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM chats WHERE test_run=?", (run_id,))]
        for chat_id in ids:
            run = agent.runs.active_for_chat(chat_id)
            if run:
                agent.runs.cancel(run.id)
            chats.delete_chat(chat_id)
            shutil.rmtree(self.hub.env.workspace_dir / "chats" / str(chat_id), ignore_errors=True)
        with db.connect() as con:
            return con.execute("DELETE FROM test_runs WHERE id=?", (run_id,)).rowcount > 0

    # ------------------------------------------------------------ what can run here

    async def caps(self) -> dict:
        from ..ui.help_routes import capabilities
        return await capabilities(self.hub, None)

    async def cases(self) -> list[dict]:
        from ..ui.help_routes import has
        caps = await self.caps()
        last = self._last_seconds()
        out = []
        for c in CASES:
            missing = [n for n in c.needs if not has(caps, n)]
            out.append({**c.info(), "ready": not missing, "missing": missing, "last_seconds": last.get(c.id)})
        return out

    def _last_seconds(self) -> dict[str, float]:
        """How long each case took the last time it ran to the end (measured, for the page)."""
        with db.connect() as con:
            rows = con.execute("SELECT results FROM test_runs ORDER BY created_at DESC LIMIT 10").fetchall()
        out: dict[str, float] = {}
        for r in rows:
            for res in json.loads(r[0] or "[]"):
                if res.get("status") in ("pass", "partial", "fail") and res["id"] not in out:
                    out[res["id"]] = res.get("seconds") or 0
        return out

    def models(self) -> list[dict]:
        from ..catalog import Catalog
        cat = self.hub.catalog
        return [{"id": e["id"], "name": Catalog.label(e)} for e in cat.llm_models() if cat.installed(e)]

    # ------------------------------------------------------------ a run

    async def start(self, case_ids: list[str], model: str | None = None, thinking: bool | None = None) -> dict:
        if self.running():
            raise RuntimeError("A test run is already going")
        cases = sorted((BY_ID[i] for i in set(case_ids) if i in BY_ID), key=CASES.index)   # the suite's order
        if not cases:
            raise ValueError("Choose at least one test")
        from ..catalog import Catalog
        cat = self.hub.catalog
        wanted = model or self.hub.settings.get("models", "default")
        entry = cat.get_llm(cat.resolve(wanted) or wanted)
        if not entry or not cat.installed(entry):
            raise ValueError(f"The model {wanted} is not downloaded")
        run_id = "test_" + secrets.token_hex(6)
        options = {"model_name": Catalog.label(entry), "thinking": thinking, "version": self.hub.env.version}
        with db.connect() as con:
            con.execute("INSERT INTO test_runs (id, created_at, status, model, options, cases, results) "
                        "VALUES (?,?,?,?,?,?,?)", (run_id, _now(), "running", entry["id"],
                                                   json.dumps(options), json.dumps([c.id for c in cases]), "[]"))
        self._stop = False
        self.live = {"run": run_id, "index": 0, "case": None, "chat_id": None, "started": None,
                     "activity": "starting"}
        self.task = asyncio.get_running_loop().create_task(self._run(run_id, cases, entry["id"], thinking),
                                                           name=run_id)
        return self.get(run_id)

    def stop(self, run_id: str) -> bool:
        if self.running() != run_id:
            return False
        self._stop = True
        return True

    async def _run(self, run_id: str, cases: list[Case], model: str, thinking: bool | None) -> None:
        results: list[dict] = []
        status = "done"
        try:
            from ..ui.help_routes import has
            caps = await self.caps()
            for i, case in enumerate(cases):
                if self._stop:
                    status = "stopped"
                    break
                self.live.update(index=i, case=case.id, chat_id=None, started=time.time(), activity="starting")
                missing = [n for n in case.needs if not has(caps, n)]
                if missing:
                    res = self._result(case, "skipped", why="Needs " + ", ".join(missing))
                else:
                    try:
                        res = await self._case(run_id, case, model, thinking)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - one broken case does not end the run
                        log.exception("test case %s failed", case.id)
                        res = self._result(case, "error", why=f"{type(exc).__name__}: {exc}")
                results.append(res)
                self._save(run_id, results=results)
            if self._stop:
                status = "stopped"
        except asyncio.CancelledError:
            status = "stopped"
            raise
        except Exception:  # noqa: BLE001
            log.exception("test run %s failed", run_id)
            status = "failed"
        finally:
            self._save(run_id, results=results, status=status, ended_at=_now())
            self.live = None

    def _result(self, case: Case, status: str, why: str = "", **extra) -> dict:
        return {"id": case.id, "area": case.area, "title": case.title, "status": status, "why": why, "checks": [],
                "review": case.review, **extra}

    async def _case(self, run_id: str, case: Case, model: str, thinking: bool | None) -> dict:
        from ..agent.context import CHARS_PER_TOKEN
        from ..agent.loop import REF_SHARE, attachment_block, ref_blocks
        agent = self.hub.extras["agent"]
        chat = chats.create_chat(title=f"Test: {case.title}", settings={"model": model}, test_run=run_id)
        self.live["chat_id"] = chat["id"]
        wd = agent.workdir(chat)
        wd.root.mkdir(parents=True, exist_ok=True)
        inp: Inputs = case.inputs()
        for rel, data in inp.files.items():
            wd.write(rel, data)
        blocks: list[dict] = [{"type": "text", "text": case.prompt}]
        given: dict[str, str] = {}
        for name, data, mime in inp.attach:
            block = attachment_block(wd, chats.save_upload(data, name, mime))
            blocks.append(block)
            given[name] = block["path"]
            if mime.startswith("image/"):
                given.setdefault("picture", block["path"])
        if inp.refs:
            budget = int(self.hub.catalog.context_per_request(model) * REF_SHARE * CHARS_PER_TOKEN)
            blocks.extend(ref_blocks(wd, inp.refs, budget))
        msg = chats.add_message(chat["id"], "user", blocks)
        options: dict = {"model": model, "test": True}
        if thinking is not None:
            options["thinking"] = thinking
        started_at, t0 = _now(), time.monotonic()
        run = agent.start(chat["id"], options)
        approvals: list[dict] = []
        status = await self._watch(run, case, approvals)
        seconds = round(time.monotonic() - t0, 1)
        try:
            from ..chats.export import merge_events
            wd.write(".hub/tests/events.json", json.dumps(merge_events(run.events), ensure_ascii=False, indent=1))
        except Exception:  # noqa: BLE001 - only for the export
            log.exception("could not keep the events of %s", case.id)
        turn, meta = self._turn(chat["id"], msg["id"], status, approvals)
        checks = list(case.check(turn, inp.expected)) if turn.status == "done" else []
        checks.append(tools_ok(turn))
        if case.sandbox and turn.status == "done":
            self.live["activity"] = "checking"
            checks += await self._sandbox(case, chat, wd, turn, inp, given)
        scored = [c for c in checks if c["level"] in ("must", "should")]
        return self._result(
            case, "stopped" if turn.status == "stopped" else verdict(turn.status, checks), why=turn.error or "",
            checks=checks, chat_id=chat["id"],
            seconds=seconds, started_at=started_at, ended_at=_now(), answer=turn.answer[:ANSWER_CHARS],
            score=[sum(1 for c in scored if c["ok"]), len(scored)],
            tools=[{"name": c["name"], "ok": c["ok"]} for c in turn.calls], files=[f["path"] for f in turn.files],
            approvals=approvals, **meta)

    async def _watch(self, run, case: Case, approvals: list[dict]) -> str:
        """Follow the turn to its end: answer its questions, stop it at the case's time limit or on Stop."""
        agent = self.hub.extras["agent"]
        deadline = time.monotonic() + case.timeout
        seen, ended = 0, ""
        while not run.finished:
            events = run.events
            for ev in events[seen:]:
                if ev["type"] == "approval_needed":
                    approvals.append(self._answer(run, case, ev.get("data") or {}))
            seen = len(events)
            if self.live is not None:
                self.live["activity"] = activity(events)
            if self._stop and not ended:
                ended = "stopped"
                agent.runs.cancel(run.id)
            elif time.monotonic() > deadline and not ended:
                ended = "timeout"
                agent.runs.cancel(run.id)
            if ended:
                if run.task is not None:
                    await asyncio.wait({run.task}, timeout=60)      # it saves what it had and ends
                break
            await asyncio.sleep(0.5)
        return ended or run.status

    @staticmethod
    def _answer(run, case: Case, data: dict) -> dict:
        tool, ask = data.get("tool") or "", data.get("ask") or {}
        args = data.get("arguments") or {}
        if ask.get("what") in ("search", "site") and case.internet:
            decision, why = "allow", "the case is about the web"
        elif ask.get("what") == "code" and ask.get("unblockable") and not args.get("internet"):
            decision, why = "allow", "the sandbox cannot keep code offline here, and the code did not ask to go online"
        else:
            decision, why = "deny", "a test answers No to anything it does not need"
        run.answer_approval(data.get("id"), decision)
        asked = ask.get("detail") or json.dumps(args, ensure_ascii=False)[:300]
        return {"tool": tool, "what": ask.get("what") or "approval", "asked": str(asked)[:300], "answer": decision,
                "why": why}

    def _turn(self, chat_id: int, after_id: int, status: str, approvals: list[dict]) -> tuple[Turn, dict]:
        rows = chats.messages(chat_id, after_id=after_id)
        turn = Turn(status=status, approvals=approvals)
        by_id: dict[str, dict] = {}
        for r in rows:
            if r["role"] == "assistant":
                text = "\n".join(b.get("text") or "" for b in r["content"] if b.get("type") == "text").strip()
                if text:
                    turn.texts.append(text)
                    turn.answer = text
                for b in r["content"]:
                    if b.get("type") == "tool_call":
                        try:
                            args = json.loads(b.get("arguments") or "{}")
                        except ValueError:
                            args = {"raw": (b.get("arguments") or "")[:2000]}
                        call = {"name": b.get("name"), "arguments": args, "ok": False, "text": ""}
                        by_id[b.get("id")] = call
                        turn.calls.append(call)
            elif r["role"] == "tool":
                for b in r["content"]:
                    if b.get("type") != "tool_result":
                        continue
                    call = by_id.get(b.get("tool_call_id"))
                    if call is not None:
                        call["ok"], call["text"] = not b.get("is_error"), (b.get("text") or "")[:3000]
                    shown = b.get("display") or {}
                    for g in shown.get("generated") or []:
                        turn.images.append({"sha": g.get("sha"), "tool": b.get("name"), "path": g.get("path")})
                    for a in shown.get("audio") or []:
                        turn.audio.append({**a, "tool": b.get("name")})
                    for v in shown.get("video") or []:
                        turn.video.append({**v, "tool": b.get("name")})
        runs = chats.runs_for_chat(chat_id)
        last = runs[-1] if runs else {}
        steps = last.get("steps") or []
        turn.files = [f for s in steps if s.get("type") == "files" for f in s.get("files") or []]
        turn.error = last.get("error") or (None if status == "done" else
                                           {"timeout": "The case's time ran out", "stopped": "Stopped"}.get(status))
        for a in chats.list_artifacts(chat_id):
            full = chats.get_artifact(chat_id, a["id"]) or {}
            turn.artifacts.append({"id": a["id"], "title": a["title"], "type": a["type"],
                                   "content": full.get("content") or ""})
        def total(kind: str) -> float:
            return round(sum(s.get("seconds") or 0 for s in steps if s.get("type") == kind), 1)
        model_steps = [s for s in steps if s.get("type") == "model"]
        timed = [s for s in model_steps if s.get("tps") and s.get("completion_tokens")]
        made = sum(s["completion_tokens"] for s in timed)
        meta = {"model": last.get("model") or "", "usage": last.get("usage") or {},
                "queue_seconds": total("queue"), "load_seconds": total("load"), "model_seconds": total("model"),
                "tool_seconds": total("tool"), "model_steps": len(model_steps),
                # generated tokens a second over the turn's model steps (llama-server's own timings)
                "tps": round(made / sum(s["completion_tokens"] / s["tps"] for s in timed), 1) if timed else None,
                "handed_over": [{"to": s.get("model"), "from": s.get("from"), "reason": s.get("reason")}
                                for s in steps if s.get("type") == "escalated"]}
        return turn, meta

    async def _sandbox(self, case: Case, chat: dict, wd, turn: Turn, inp: Inputs, given: dict) -> list[dict]:
        """The case's checks in check_lib.py, in the sandbox, in the case's folder (offline where it can be)."""
        from ..agent import internet
        from ..agent.tools.base import ToolContext
        from ..agent.tools.shell import SandboxError, sandbox_exec
        wd.write(".hub/tests/check_lib.py", CHECK_LIB.read_bytes())
        for name, data in inp.hidden.items():
            wd.write(f".hub/tests/{name}", data)
        pictures = []
        for k, im in enumerate(turn.images, 1):
            up = chats.get_upload(im["sha"] or "") or {}
            ext = {"image/jpeg": ".jpg", "image/webp": ".webp"}.get(up.get("mime", ""), ".png")
            if not im.get("sha") or not blobs.exists(im["sha"]):
                continue
            rel = f".hub/tests/out/picture-{k}{ext}"
            wd.write(rel, blobs.read(im["sha"]))
            pictures.append({"path": rel, "tool": im["tool"]})
        payload = {"case": case.id, "answer": turn.answer, "files": turn.files, "images": pictures,
                   "audio": [{"path": a.get("path"), "tool": a.get("tool")} for a in turn.audio if a.get("path")],
                   "video": [{"path": v.get("path"), "tool": v.get("tool")} for v in turn.video if v.get("path")],
                   "expected": inp.expected, "inputs": given}
        wd.write(".hub/tests/input.json", json.dumps(payload, ensure_ascii=False, indent=1))
        ctx = ToolContext(hub=self.hub, run=None, chat=chat, project=None, workdir=wd, ticket=None, model={})
        offline = await internet.code_can_go_offline(self.hub)
        try:
            res = await sandbox_exec(ctx, f"python3 .hub/tests/check_lib.py {shlex.quote(case.id)} "
                                          ".hub/tests/input.json", wd.sandbox_path, case.check_timeout,
                                     offline=offline)
        except SandboxError as exc:
            return [C("the sandbox's checks ran", False, str(exc))]
        out = res.get("stdout") or ""
        m = re.search(r"@@CHECKS@@(.*)", out)
        if not m:
            tail = (res.get("stderr") or out or "no output").strip()[-500:]
            return [C("the sandbox's checks ran", False, f"exit code {res.get('exit_code')}: {tail}")]
        try:
            found = json.loads(m.group(1))
        except ValueError as exc:
            return [C("the sandbox's checks ran", False, f"unreadable result: {exc}")]
        return [C(str(c.get("name"))[:120], c.get("ok"), c.get("detail", ""),
                  c.get("level") if c.get("level") in ("must", "should", "info") else "must") for c in found]


def area_title(area: str) -> str:
    return dict(AREAS).get(area, area)
