"""What the updater asks before it stops the Hub (0.26.0): is anybody using the card or the Hub right now?

update.sh builds the new version while this one serves, then waits until the Hub has had nothing to do for a
while before it stops it - so an update started at any time does not cut off an answer, a video, a person's chat,
a test run or a download. The question comes from the VM itself with the internal token from .env; through the
proxy it is refused.
"""
from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, HTTPException, Request

from .. import auth
from ..state import get_hub

router = APIRouter()

DEVICE_FETCH_S = 120        # a person's device fetching the answer's pictures and files after it ended


def _gone(run) -> bool:
    """A turn whose task ended without saying so can never finish: no reason to wait for it (forever)."""
    task = getattr(run, "task", None)
    return task is not None and task.done()


def busy_reasons(hub) -> list[str]:
    """What is going on, in words for the updater's screen (empty: nothing)."""
    why: list[str] = []
    snap = hub.scheduler.snapshot()
    for j in snap["running"]:
        who = {"web": "a chat", "tests": "a test", "library": "library indexing", "system": "the Hub",
               "training": "a LoRA training"}.get(j["owner"], j["owner"])
        why.append(f"{who} is working on the card" + (f" ({j['label'][:60]})" if j.get("label") else ""))
    if snap["switching"]:
        why.append(f"the card is loading {snap['switching']}")
    if snap["pending"]:
        n = len(snap["pending"])
        why.append(f"{n} job{'s' if n != 1 else ''} wait{'' if n != 1 else 's'} for the card")
    agent = hub.extras.get("agent")
    if agent is not None:
        busy_chats = {j["meta"].get("chat_id") for j in snap["running"] + snap["pending"]}
        for r in list(agent.runs.runs.values()):
            if r.chat_id is None:                                  # a person's turn: below
                continue
            if not r.finished and r.chat_id not in busy_chats and not _gone(r):   # between two steps, or asking
                why.append("a chat is answering" + (" and waits for an answer to its question" if r.approvals else ""))
    device = hub.extras.get("device")
    if device is not None:
        now = time.time()
        for t in list(device.turns.values()):
            name = auth.display_name(t.user) or "a person"
            if t.ended is None:
                if _gone(getattr(t, "run", None)):
                    continue
                if not any(j["meta"].get("user_id") == t.user.get("id") for j in snap["running"] + snap["pending"]):
                    why.append(f"{name}'s chat is answering")
            elif now - t.ended < DEVICE_FETCH_S:
                why.append(f"{name}'s device is fetching an answer")
    training = hub.extras.get("training")
    if training is not None:                                       # the base model's download is not a GPU job
        for run in training.list():
            if run["state"] == "preparing":
                why.append(f"the LoRA training {run['name']} is downloading its base model")
    tests = hub.extras.get("tests")
    if tests is not None and tests.running():
        why.append("a test run is going (Settings -> Tests)")
    libraries = hub.extras.get("libraries")
    if libraries is not None and getattr(libraries, "current", None):          # on the CPU it never queues
        why.append(f"a library is reading {libraries.current.get('filename') or 'a document'}")
    uploads = hub.extras.get("uploads")
    if uploads is not None:                                        # 0.62.0: a model file coming from the computer
        for name in uploads.busy():
            why.append(f"{name} is being uploaded")
    downloads = hub.extras.get("downloads")
    if downloads is not None:
        snap = downloads.snapshot()
        active = [f for f in snap["files"] if f.get("status") in ("downloading", "queued")] if snap["active"] else []
        if active:
            names = sorted({f["model"] for f in active})
            why.append(f"downloading {', '.join(names)}")
    return why


@router.get("/hub/api/internal/busy")
async def busy(request: Request):
    hub = get_hub(request)
    token = request.headers.get("x-internal-token") or ""
    if not hub.env.internal_token or not hmac.compare_digest(token, hub.env.internal_token) \
            or getattr(request.state, "via_proxy", False):
        raise HTTPException(403, "for the updater on the VM")
    why = busy_reasons(hub)
    return {"busy": bool(why), "why": why, "version": hub.env.version}
