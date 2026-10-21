"""Serving stored files (uploads, generated images) to the browser, and
signed links for API callers who asked for an image URL."""
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .chats import store as chats
from .projects import blobs
from .state import get_hub

router = APIRouter()
INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif", "application/pdf",
          "text/plain"}


def sign(secret: str, sha: str, ttl: int = 3600) -> str:
    exp = int(time.time()) + ttl
    sig = hmac.new(secret.encode(), f"{sha}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"/hub/media/blob/{sha}?exp={exp}&sig={sig}"


def _valid(secret: str, sha: str, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    want = hmac.new(secret.encode(), f"{sha}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(want, sig or "")


@router.get("/hub/media/blob/{sha}")
async def blob(sha: str, request: Request, exp: int = 0, sig: str = "", download: bool = False):
    hub = get_hub(request)
    if getattr(request.state, "user", None) is None and not _valid(hub.env.secret_key, sha, exp, sig):
        raise HTTPException(401, "not signed in")
    if len(sha) != 64 or not blobs.exists(sha):
        raise HTTPException(404, "no such file")
    meta = chats.get_upload(sha) or {}
    mime = meta.get("mime") or "application/octet-stream"
    headers = {"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"}
    # Stored files come from the web and from tools: nothing may run as the Hub.
    # CSP sandbox is harmless for <img>, and stops an SVG or HTML opened directly.
    if mime != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox"
    if download or mime not in INLINE:
        name = (meta.get("filename") or sha).replace('"', "")
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return Response(blobs.read(sha), media_type=mime, headers=headers)
