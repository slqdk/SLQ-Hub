"""The front door (pure ASGI middleware, so streaming and disconnects pass
straight through).

* /k/<key>/...  - an API key in the path, for Ollama clients that cannot send
  headers: stripped here, remembered for the auth check.
* model API (/v1/*, /api/* except /api/sync) - LAN only, an API key (Bearer,
  x-api-key or path) or a signed-in browser, CORS for the origins listed in
  Settings. From outside, only through the trusted proxy: with a key set to
  work away from home, or with any key when Settings allows it for all.
* sync API (/api/sync/*) - a slqsync_ device token or a signed-in browser.
* UI API (/hub/api/*) - a signed-in browser, except login/status/health;
  state-changing requests from another origin are refused.
* everything else - the web app's files.

People (accounts the owner added, role "member") reach only what their chat
needs (MEMBER_ROUTES): the models, a few settings, the queue, their own
device turns - not the model API (apps use keys). A person limited to the
home network is not signed in at all when the request comes from elsewhere.
"""
from __future__ import annotations

import json
import re

from starlette.datastructures import Headers

from . import auth, netguard
from .api.common import Caller

PATH_KEY = re.compile(r"^/k/(slqhub_[A-Za-z0-9_\-]{10,})(/.*)?$")
# What a person (role member) may use of the UI API; everything else is the owner's.
MEMBER_ROUTES = [
    ({"GET"}, re.compile(r"^/hub/api/(models|settings|skills|events|queue)$")),
    ({"POST"}, re.compile(r"^/hub/api/queue/\d+/cancel$")),
    ({"POST"}, re.compile(r"^/hub/api/auth/password$")),
    ({"POST"}, re.compile(r"^/hub/api/device/turns$")),
    ({"GET", "POST"}, re.compile(r"^/hub/api/device/turns/[A-Za-z0-9_]+(/[A-Za-z0-9_./-]*)?$")),
]


def member_may(method: str, path: str) -> bool:
    return any(method in methods and pat.match(path) for methods, pat in MEMBER_ROUTES)
PUBLIC_UI = {"/hub/api/health", "/hub/api/auth/login", "/hub/api/auth/status", "/hub/api/auth/logout",
             "/hub/api/auth/setup"}
CORS_HEADERS = ("authorization, content-type, x-api-key, anthropic-version, anthropic-beta, "
                "anthropic-dangerous-direct-browser-access, openai-organization, x-stainless-arch, "
                "x-stainless-lang, x-stainless-os, x-stainless-package-version, x-stainless-runtime, "
                "x-stainless-runtime-version, x-stainless-retry-count, x-stainless-timeout")


def classify(path: str) -> str:
    if path.startswith("/hub/api/"):
        return "public" if path in PUBLIC_UI else "ui"
    if path.startswith("/hub/media/"):
        return "media"
    if path.startswith("/api/sync/") or path == "/api/sync":
        return "sync"
    if path.startswith("/v1/") or path.startswith("/api/"):
        return "model"
    return "static"


async def _json(send, status: int, body: dict, extra: list | None = None) -> None:
    data = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]
    headers += extra or []
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})


def _bearer(headers: Headers) -> str | None:
    a = headers.get("authorization") or ""
    if a.lower().startswith("bearer "):
        return a[7:].strip()
    return headers.get("x-api-key") or None


def _cookie(headers: Headers, name: str) -> str | None:
    for part in (headers.get("cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def _ip_allowed(ip: str, allowed: str) -> bool:
    items = [x.strip() for x in (allowed or "").split(",") if x.strip()]
    if not items:
        return True
    import ipaddress
    nets = []
    for it in items:
        try:
            nets.append(ipaddress.ip_network(it, strict=False))
        except ValueError:
            continue
    return netguard.in_networks(ip, nets)


class Gate:
    def __init__(self, app, hub_getter):
        self.app = app
        self.hub_getter = hub_getter

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hub = self.hub_getter()
        path = scope["path"]
        path_key = None
        m = PATH_KEY.match(path)
        if m:
            path_key = m.group(1)
            path = m.group(2) or "/"
            scope = {**scope, "path": path, "raw_path": path.encode()}
        headers = Headers(scope=scope)
        peer = (scope.get("client") or ("", 0))[0]
        ip, via_proxy = netguard.client_ip(peer, headers.get("x-forwarded-for"), hub.env.trusted_proxies)
        state = dict(scope.get("state") or {})
        state.update(client_ip=ip, via_proxy=via_proxy,
                     https=headers.get("x-forwarded-proto") == "https" if via_proxy else scope.get("scheme") == "https")
        scope = {**scope, "state": state}
        kind = classify(path)
        method = scope["method"]
        lan = netguard.is_lan(ip, hub.env.lan_networks, hub.env.sandbox_ip)

        if kind == "model":
            origin = headers.get("origin")
            cors_ok = bool(origin) and origin in (hub.settings.get("api", "cors_origins") or [])
            cors = [(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin"),
                    (b"access-control-allow-credentials", b"true")] if cors_ok else []
            if method == "OPTIONS" and origin:
                if not cors_ok:
                    return await _json(send, 403, {"error": f"origin {origin} is not allowed (Settings -> API)"})
                pre = cors + [(b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                              (b"access-control-allow-headers", CORS_HEADERS.encode()),
                              (b"access-control-max-age", b"600")]
                await send({"type": "http.response.start", "status": 204, "headers": pre})
                return await send({"type": "http.response.body", "body": b""})
            raw = path_key or _bearer(headers)
            key = auth.find_api_key(raw) if raw else None
            # From outside the home network only through the proxy (hub.slq.dk): every key when Settings allows
            # it, otherwise only keys set to work away from home.
            away = via_proxy and (bool(hub.settings.get("api", "allow_external")) or bool(key and key.get("away")))
            if not lan and not away:
                return await _json(send, 403, {"error": f"the model API answers only on the LAN (this request came "
                                                        f"from {ip})", "client_ip": ip, "via_proxy": via_proxy}, cors)
            if raw and key is None:                  # wrong credentials beat a browser session
                return await _json(send, 401, {"error": {"message": "invalid API key",
                                                         "type": "authentication_error"}}, cors)
            if key is not None:
                if not _ip_allowed(ip, key.get("allowed_ips", "")):
                    return await _json(send, 403, {"error": f"this key may not be used from {ip}", "client_ip": ip,
                                                   "via_proxy": via_proxy}, cors)
                auth.touch_api_key(key["id"])
                state["caller"] = Caller(owner=key["name"], priority=key["priority"], via="key")
            else:
                user = auth.session_user(_cookie(headers, auth.SESSION_COOKIE))
                if user is None or auth.is_member(user):     # a person's sign-in is for their chat, not the API
                    msg = "invalid API key" if raw else "an API key is required (Settings -> API keys)"
                    return await _json(send, 401, {"error": {"message": msg, "type": "authentication_error"}}, cors)
                state["caller"] = Caller(owner="web", priority="interactive", via="session")
                state["user"] = user
            if cors:
                async def send_cors(message):
                    if message["type"] == "http.response.start":
                        message = {**message, "headers": list(message.get("headers") or []) + cors}
                    await send(message)
                return await self.app(scope, receive, send_cors)
            return await self.app(scope, receive, send)

        if kind == "sync":
            raw = _bearer(headers)
            user = auth.user_for_sync_token(raw) if raw else None
            user = _signed_in(user or auth.session_user(_cookie(headers, auth.SESSION_COOKIE)), lan)
            if user is None or auth.is_member(user):            # people have no projects to sync
                return await _json(send, 401, {"detail": "sign in with a sync device token"})
            state["user"] = user
            return await self.app(scope, receive, send)

        if kind in ("ui", "public", "media"):
            user = _signed_in(auth.session_user(_cookie(headers, auth.SESSION_COOKIE)), lan)
            if user is not None:
                state["user"] = user
            if kind == "ui" and user is None:
                return await _json(send, 401, {"detail": "not signed in"})
            if kind == "ui" and auth.is_member(user) and not member_may(method, path):
                return await _json(send, 403, {"detail": "Only the owner of the Hub can do that."})
            if kind in ("ui", "public") and method in ("POST", "PUT", "PATCH", "DELETE"):
                origin = headers.get("origin")
                if origin and not _same_origin(origin, headers, hub.env.public_url):
                    return await _json(send, 403, {"detail": "cross-origin request refused"})
            return await self.app(scope, receive, _with_version(send, hub.env.version) if kind != "media" else send)

        return await self.app(scope, receive, send)


def _with_version(send, version: str):
    """The Hub's version on every answer to the UI. A page loaded before an update runs the old code until it is
    reloaded (a phone's tab may stay open for days): when the version changes under it, it offers a reload."""
    extra = (b"x-hub-version", version.encode())

    async def wrapped(message):
        if message["type"] == "http.response.start":
            message = {**message, "headers": [*(message.get("headers") or []), extra]}
        await send(message)
    return wrapped


def _signed_in(user: dict | None, lan: bool) -> dict | None:
    """A person limited to the home network counts as signed out anywhere else."""
    if user is not None and auth.is_member(user) and not user.get("away") and not lan:
        return None
    return user


def _same_origin(origin: str, headers: Headers, public_url: str) -> bool:
    from urllib.parse import urlparse
    o = urlparse(origin)
    host = headers.get("x-forwarded-host") or headers.get("host") or ""
    if o.netloc == host:
        return True
    if public_url and urlparse(public_url).netloc == o.netloc:
        return True
    return False
