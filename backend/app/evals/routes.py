"""Settings -> Tests: the cases, the runs, a run's export."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ..state import get_hub
from . import export
from .suite import AREAS

router = APIRouter(prefix="/hub/api/tests")


def _tests(request: Request):
    return get_hub(request).extras["tests"]


def _run_or_404(request: Request, run_id: str) -> dict:
    rec = _tests(request).get(run_id)
    if rec is None:
        raise HTTPException(404, "no such test run")
    return rec


@router.get("")
async def overview(request: Request):
    t = _tests(request)
    hub = get_hub(request)
    return {"areas": [{"id": a, "title": title} for a, title in AREAS], "cases": await t.cases(),
            "models": t.models(), "default_model": hub.settings.get("models", "default"),
            "thinking_in_chats": bool(hub.settings.get("agent", "thinking_in_chats")),
            "internet_asks": hub.settings.get("web", "access") == "ask",
            "running": t.running(), "live": t.live, "runs": t.runs()}


class RunIn(BaseModel):
    cases: list[str]
    model: str | None = None
    thinking: bool | None = None          # None: as set for chats (Settings -> Assistant, or the model's own)


@router.post("/runs")
async def start(body: RunIn, request: Request):
    try:
        rec = await _tests(request).start(body.cases, body.model, body.thinking)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return rec


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    t = _tests(request)
    rec = _run_or_404(request, run_id)
    return {**rec, "live": t.live if t.running() == run_id else None}


@router.post("/runs/{run_id}/stop")
async def stop(run_id: str, request: Request):
    _run_or_404(request, run_id)
    if not _tests(request).stop(run_id):
        raise HTTPException(409, "this run is not going")
    return {"ok": True}


@router.delete("/runs/{run_id}")
async def delete(run_id: str, request: Request):
    _run_or_404(request, run_id)
    try:
        _tests(request).delete(run_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


@router.get("/runs/{run_id}/export")
async def export_run(run_id: str, request: Request):
    rec = _run_or_404(request, run_id)
    path = await export.build(get_hub(request), rec)
    return FileResponse(path, media_type="application/zip", filename=export.filename(rec),
                        background=BackgroundTask(path.unlink, missing_ok=True))
