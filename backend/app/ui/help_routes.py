"""What the help page needs to know about this Hub (0.20.0): which of the things it explains work here now - the
downloaded models, the sandbox, the internet setting, and whether it is the owner's chat or a person's. The same
checks decide which tools a turn is offered; the help text itself lives in the UI (frontend/src/lib/help.ts)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import auth
from ..agent import internet
from ..agent.loop import local_target
from ..agent.tools import webapp as webapp_tool
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
    return await capabilities(get_hub(request), getattr(request.state, "user", None))


def has(caps: dict, need: str) -> bool:
    """As the help page reads a need (frontend/src/lib/help.ts has()): "video.t2v" or "code"."""
    group, _, key = need.partition(".")
    value = caps.get(group)
    if key:
        return bool((value or {}).get(key)) if isinstance(value, dict) else False
    return value > 0 if isinstance(value, int) and not isinstance(value, bool) else bool(value)


async def capabilities(hub, user) -> dict:
    """What works on this Hub now, for this user (the owner when user is None). Settings -> Tests asks too."""
    member = auth.is_member(user)
    images, audio, video = (hub.extras.get(k) for k in ("images", "audio", "video"))
    if member:                                       # as a device turn decides it (agent/device.py)
        code = bool((user or {}).get("may_run_code")) and await hub.extras["device"].can_confine()
        windows = browser = False
    else:
        code = True
        await windows_tool.refresh(hub)
        await webapp_tool.refresh(hub)
        windows, browser = windows_tool.ready(hub), webapp_tool.ready(hub)
    libs, mcp = hub.extras.get("libraries"), hub.extras.get("mcp")
    return {
        "member": member,
        "internet_asks": internet.asking(hub.settings),
        "code": code,
        "windows": windows,
        "browser": browser,
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
