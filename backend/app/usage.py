"""Usage (0.24.0): what each one used of the card - answers, tokens, the card's energy and what it cost - per
local day, per who and per project. Settings -> Usage shows it, a project's page its total, each answer its own
share (the run's usage). Limits for a person (planned) can read the same rows.

who: "owner" (the owner's chats), "user:<id>" (a person's), "key:<name>" (an app's key on the model API),
"tests" (Settings -> Tests), "library" (indexing), "system" (loading models) and "idle" (the card while nobody
uses it). An agent turn is counted once, when it ends (finish_turn); any other job when it finishes (job_finished).
Rows stay when chats go, so a project's and a person's totals stay too.
"""
from __future__ import annotations

import logging
from datetime import datetime

from . import auth, db

log = logging.getLogger("hub.usage")

FIELDS = ("turns", "jobs", "tokens_in", "tokens_out", "tokens_cached", "energy_wh", "cost", "seconds")
PERIODS = ("today", "month", "all")


def _tz():
    from .agent.prompt import local_tz                 # TZ from .env, else Copenhagen
    return local_tz()


def today() -> str:
    return datetime.now(_tz()).date().isoformat()


def first_day(period: str) -> str | None:
    """The first day a period counts ("today", "month"; None for all time)."""
    d = datetime.now(_tz()).date()
    if period == "today":
        return d.isoformat()
    if period == "month":
        return d.replace(day=1).isoformat()
    return None


def price(hub) -> float:
    try:
        return max(0.0, float(hub.settings.get("costs", "price_per_kwh") or 0))
    except (TypeError, ValueError):
        return 0.0


def cost_of(hub, wh: float) -> float:
    return round(wh / 1000 * price(hub), 6)


def record(who: str, *, project_id: int | None = None, day: str | None = None, **counts) -> None:
    vals = {f: counts.get(f) or 0 for f in FIELDS}
    if not any(vals.values()):
        return
    sets = ", ".join(f"{f}={f}+excluded.{f}" for f in FIELDS)
    try:
        with db.connect() as con:
            con.execute(f"INSERT INTO usage_daily (day, who, project_id, {', '.join(FIELDS)})"
                        f" VALUES (?,?,?,{','.join('?' * len(FIELDS))})"
                        f" ON CONFLICT(day, who, project_id) DO UPDATE SET {sets}",
                        (day or today(), who, int(project_id or 0), *(vals[f] for f in FIELDS)))
    except Exception:  # never let statistics break a turn or the queue
        log.exception("could not keep the usage")


def who_of_job(job) -> str:
    if job.meta.get("who"):
        return str(job.meta["who"])
    if job.meta.get("user_id"):
        return f"user:{job.meta['user_id']}"
    if job.kind in ("preload", "load", "unload"):
        return "system"
    if job.owner in ("web", "tests", "library", "system"):
        return {"web": "owner"}.get(job.owner, job.owner)
    return f"key:{job.owner}"                          # the model API, and its pictures, sound and video


def _tokens(prompt: int, cached: int, out: int) -> dict:
    """Tokens read (the prompt less what came from the cache), written, and read from the cache."""
    return {"tokens_in": max(0, prompt - cached), "tokens_out": max(0, out), "tokens_cached": max(0, cached)}


def job_finished(hub, job) -> None:
    """The scheduler's on_finish, after the meter: a job outside an agent turn into the day's usage."""
    if job.meta.get("run_id") or job.started_at is None or job.ended_at is None:
        return                                         # part of a turn (finish_turn counts it), or it never ran
    wh = job.energy_j / 3600
    t = job.meta.get("tokens") or {}
    record(who_of_job(job), jobs=1, energy_wh=wh, cost=cost_of(hub, wh),
           seconds=max(0.0, job.ended_at - job.started_at),
           **_tokens(int(t.get("prompt") or 0), int(t.get("cached") or 0), int(t.get("out") or 0)))


def finish_turn(hub, run_id: str, usage: dict, who: str, project_id: int | None = None) -> None:
    """When an agent turn ends: the card's energy for it into its usage (Wh, kr, seconds on the card) - shown under
    the answer - and the turn into the day's usage."""
    meter = hub.extras.get("energy")
    wh, secs = meter.take_run(run_id) if meter is not None else (0.0, 0.0)
    cost = cost_of(hub, wh)
    usage["energy_wh"] = round(wh, 4)
    usage["cost"] = cost
    usage["gpu_seconds"] = round(secs, 1)
    record(who, project_id=project_id, turns=1, energy_wh=wh, cost=cost, seconds=secs,
           **_tokens(int(usage.get("prompt_tokens") or 0), int(usage.get("cached_tokens") or 0),
                     int(usage.get("completion_tokens") or 0)))


def idle(hub, joules: float) -> None:
    wh = joules / 3600
    record("idle", energy_wh=wh, cost=cost_of(hub, wh))


def note_tokens(ticket, usage: dict | None, timings: dict | None) -> None:
    """An API call's tokens on its job, before the job ends (job_finished counts them)."""
    u, t = usage or {}, timings or {}
    cached = int(t.get("cache_n") or 0)
    prompt = int(u.get("prompt_tokens") or 0) or int(t.get("prompt_n") or 0) + cached
    out = int(u.get("completion_tokens") or 0) or int(t.get("predicted_n") or 0)
    try:
        ticket.job.meta["tokens"] = {"prompt": prompt, "cached": cached, "out": out}
    except AttributeError:
        pass


# ---------------------------------------------------------------- reading

def _sum_sql(where: str) -> str:
    return (f"SELECT {', '.join(f'COALESCE(SUM({f}), 0) {f}' for f in FIELDS)}, MIN(day) since"
            f" FROM usage_daily WHERE {where}")


def totals(period: str = "month") -> list[dict]:
    """Per who, for a period: the sums and the first day counted."""
    start = first_day(period)
    sql = (f"SELECT who, {', '.join(f'SUM({f}) {f}' for f in FIELDS)}, MIN(day) since FROM usage_daily"
           f"{' WHERE day >= ?' if start else ''} GROUP BY who")
    with db.connect() as con:
        return [dict(r) for r in con.execute(sql, (start,) if start else ()).fetchall()]


def project_totals(project_id: int) -> dict:
    with db.connect() as con:
        r = con.execute(_sum_sql("project_id=?"), (int(project_id),)).fetchone()
    return dict(r) if r else {f: 0 for f in FIELDS}


def who_total(who: str, period: str = "today") -> dict:
    """One who's sums for a period (for a limit on a person's use, later)."""
    start = first_day(period)
    with db.connect() as con:
        r = con.execute(_sum_sql("who=?" + (" AND day >= ?" if start else "")),
                        (who, start) if start else (who,)).fetchone()
    return dict(r) if r else {f: 0 for f in FIELDS}


def label(hub, who: str) -> tuple[str, str]:
    """(what to call it, its kind) for Settings -> Usage."""
    if who == "owner":
        name = hub.settings.get("agent", "user_name") or (hub.env.admin_user or "the owner").title()
        return name, "owner"
    if who.startswith("user:"):
        try:
            person = auth.get_person(int(who[5:]))
        except (TypeError, ValueError):
            person = None
        return (auth.display_name(person) if person else "A removed person"), "person"
    if who.startswith("key:"):
        return who[4:] or "An app", "app"
    return {"tests": ("Tests", "tests"), "library": ("Libraries, indexing", "library"),
            "system": ("Loading models", "system"), "idle": ("The card, idle", "idle")}.get(who, (who, "other"))
