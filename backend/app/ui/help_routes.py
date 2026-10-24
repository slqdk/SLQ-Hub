"""What the help page needs to know about this Hub (0.20.0): which of the things it explains work here now - the
downloaded models, the sandbox, the internet setting, and whether it is the owner's chat or a person's. The same
checks decide which tools a turn is offered; the help text itself lives in the UI (frontend/src/lib/help.ts)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import auth
from ..agent import internet
from ..agent.loop import local_target
from ..agent.tools import windows as windows_tool
from ..state import get_hub

router = APIRouter()

SOUND = ("speech", "music", "sound", "voice", "separate")
VIDEO = ("t2v", "i2v", "flf2v")


def _handover(hub) -> bool:
    """A stronger model on this server takes over a stuck turn (Settings -> Assistant) - for any other model."""
    return local_target(hub, {"id": ""}) is not None


@router.get("/hub/api/help")
async def help_caps(request: Request):
    hub = get_hub(request)
    user = getattr(request.state, "user", None)
    member = auth.is_member(user)
    images, audio, video = (hub.extras.get(k) for k in ("images", "audio", "video"))
    if member:                                       # as a device turn decides it (agent/device.py)
        code = bool((user or {}).get("may_run_code")) and await hub.extras["device"].can_confine()
        windows = False
    else:
        code = True
        await windows_tool.refresh(hub)
        windows = windows_tool.ready(hub)
    libs, mcp = hub.extras.get("libraries"), hub.extras.get("mcp")
    return {
        "member": member,
        "internet_asks": internet.asking(hub.settings),
        "code": code,
        "windows": windows,
        "memory": not member,
        "projects": not member,
        "libraries": not member and bool(libs and libs.available()),
        "mcp": 0 if member or mcp is None else len(mcp.tools()),
        "handover": _handover(hub),
        "images": {"generate": bool(images and images.available()),
                   "edit": bool(images and images.installed("edit")),
                   "cutout": bool(images and images.available("qwen-image-2.1"))},
        "sound": {a: bool(audio and audio.available(a)) for a in SOUND},
        "video": {a: bool(video and video.available(a)) for a in VIDEO},
    }
