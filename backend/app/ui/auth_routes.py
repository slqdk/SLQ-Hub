"""Sign-in, sign-out, status, first-time setup, password change."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth, netguard
from ..state import get_hub

router = APIRouter()


class Login(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current: str
    new: str


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=bool(request.state.https), path="/")


@router.get("/hub/api/health")
async def health(request: Request):
    hub = get_hub(request)
    return {"ok": True, "version": hub.env.version}


@router.get("/hub/api/auth/status")
async def status(request: Request):
    hub = get_hub(request)
    user = getattr(request.state, "user", None)
    return {"signedIn": user is not None, "user": user, "version": hub.env.version,
            "setupNeeded": auth.user_count() == 0}


@router.post("/hub/api/auth/login")
async def login(body: Login, request: Request, response: Response):
    ip = request.state.client_ip
    if auth.throttle.blocked(ip):
        raise HTTPException(429, "Too many failed sign-ins - wait ten minutes.")
    user = auth.get_user(body.username.strip())
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        auth.throttle.fail(ip)
        raise HTTPException(401, "Wrong user name or password.")
    auth.throttle.reset(ip)
    token = auth.create_session(user["id"], request.headers.get("user-agent", ""))
    _set_cookie(response, request, token)
    return {"user": {"id": user["id"], "username": user["username"]}}


@router.post("/hub/api/auth/logout")
async def logout(request: Request, response: Response):
    auth.delete_session(request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/hub/api/auth/setup")
async def setup(body: Login, request: Request, response: Response):
    """Only when there is no account yet (install.sh normally creates it),
    and only from the LAN."""
    hub = get_hub(request)
    if auth.user_count() > 0:
        raise HTTPException(409, "The admin account exists already.")
    if not netguard.is_lan(request.state.client_ip, hub.env.lan_networks, hub.env.sandbox_ip):
        raise HTTPException(403, "Set up the account from the LAN.")
    if len(body.password) < 10:
        raise HTTPException(400, "Use at least 10 characters.")
    auth.create_user(body.username.strip() or "admin", body.password)
    user = auth.get_user(body.username.strip() or "admin")
    token = auth.create_session(user["id"], request.headers.get("user-agent", ""))
    _set_cookie(response, request, token)
    return {"user": {"id": user["id"], "username": user["username"]}}


@router.post("/hub/api/auth/password")
async def change_password(body: PasswordChange, request: Request, response: Response):
    me = request.state.user
    user = auth.get_user(me["username"])
    if not user or not auth.verify_password(body.current, user["password_hash"]):
        raise HTTPException(401, "The current password is wrong.")
    if len(body.new) < 10:
        raise HTTPException(400, "Use at least 10 characters.")
    auth.set_password(me["username"], body.new)
    token = auth.create_session(user["id"], request.headers.get("user-agent", ""))
    _set_cookie(response, request, token)
    return {"ok": True}
