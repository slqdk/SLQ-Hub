"""Internet for the assistant (Settings -> Assistant -> Web -> Internet for the assistant): allowed, or ask first.

Asking first, a turn that wants the internet stops and asks the user in the chat, like a tool that needs approval:
Allow once, Always, or Deny. What is asked and what "always" remembers:

* web_search - "Always allow web search" (internet:search);
* web_fetch - per site: "Always allow dr.dk" (internet:site:dr.dk);
* bash - code runs without the internet (Landlock in the sandbox: no TCP connections), unless the call says
  internet: true, which asks: "Always allow internet for code" (internet:code). Where the sandbox cannot keep code
  off the internet (no Landlock ABI 4), every command asks;
* windows_app - the program (and its build) runs without the internet, or with it where internet for code is
  always allowed; where the sandbox cannot keep it off, every run asks (internet:code);
* web_app - the same for the browser: offline, the page gets only its own files (the rest is listed as blocked).

The owner's "always" answers are kept with the tools they always allow (tool_permissions) and listed in Settings,
where each can be taken back. A person (a member's chat) answers once each time: their chats leave nothing on the
Hub, and the owner's answers are the owner's.
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlparse

import httpx

from .. import netfetch

SEARCH = "internet:search"
CODE = "internet:code"
SITE = "internet:site:"
TOOLS = ("web_search", "web_fetch", "bash", "windows_app", "web_app")


def asking(settings) -> bool:
    return settings.get("web", "access") == "ask"


def host_of(url) -> str:
    """The site of a URL as asked about and remembered: the host the fetch connects to (netfetch.host_key: IDNA
    2008, so a look-alike name shows as xn--...), without a final dot."""
    url = str(url or "").strip()
    try:
        if urlparse(url).scheme not in ("http", "https"):
            return ""
        return netfetch.host_key(url).rstrip(".")
    except (ValueError, netfetch.FetchError):
        return ""


def same_site(a: str, b: str) -> bool:
    """dr.dk and www.dr.dk are one site for a redirect."""
    return bool(a) and a.removeprefix("www.") == b.removeprefix("www.")


def redirect_rule(settings, url: str, allowed):
    """While asking first, web_fetch follows a redirect only within the site the user said yes to (www. or not) or
    to a site `allowed(key)` says the owner always allows; elsewhere it stops, and the model fetches the new
    address itself - which asks. None (follow every redirect) when the internet is allowed."""
    if not asking(settings):
        return None
    first = host_of(url)

    def ok(new_url: str) -> bool:
        host = host_of(new_url)
        return bool(host) and (same_site(first, host) or allowed(site_key(host)))
    return ok


def site_key(host: str) -> str:
    return SITE + host


def label(key: str) -> str:
    """What an "always" answer allows, in words (Settings lists them)."""
    if key == SEARCH:
        return "Web search"
    if key == CODE:
        return "Internet for code"
    if key.startswith(SITE):
        return key[len(SITE):]
    return key


def wants_internet(args: dict) -> bool:
    """bash's internet flag - true, or what a model writes for it ("true", 1)."""
    return str(args.get("internet")).strip().lower() in ("true", "1", "yes")


def shown(text: str, limit: int) -> str:
    """What the question shows of a query, URL or command: all of it, or a cut that says so."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} more characters: open the call to see all of it)"


def question(name: str, args: dict, allowed, can_block_code: bool) -> tuple[dict | None, bool]:
    """(what to ask before this call - None when nothing - and whether the call may use the internet).
    `allowed(key)` says whether the owner always allows it (always False in a person's chat)."""
    if name == "web_search":
        if allowed(SEARCH):
            return None, True
        return {"kind": "internet", "key": SEARCH, "what": "search", "detail": shown(args.get("query"), 1000),
                "always": "Always allow web search"}, True
    if name == "web_fetch":
        host = host_of(args.get("url"))
        if not host or allowed(site_key(host)):
            return None, True                          # no host: the fetch refuses it by itself
        return {"kind": "internet", "key": site_key(host), "what": "site", "host": host,
                "detail": shown(args.get("url"), 2000), "always": f"Always allow {host}"}, True
    if name == "bash":
        if allowed(CODE):
            return None, True
        if wants_internet(args) or not can_block_code:
            return {"kind": "internet", "key": CODE, "what": "code", "detail": shown(args.get("command"), 4000),
                    "always": "Always allow internet for code", "unblockable": not can_block_code}, True
        return None, False                             # runs offline
    if name in ("windows_app", "web_app"):
        if allowed(CODE):
            return None, True
        if not can_block_code:
            what = (f"Run {args.get('target') or 'a Windows program'} on the virtual screen" if name == "windows_app"
                    else f"Open {args.get('target') or args.get('artifact') or 'a page'} in a browser" + _evals(args))
            return {"kind": "internet", "key": CODE, "what": "code", "detail": shown(what, 1000),
                    "always": "Always allow internet for code", "unblockable": True}, True
        return None, False
    return None, True


def _evals(args: dict) -> str:
    """The JavaScript a web_app call runs in the page (its eval steps), for the question."""
    steps = args.get("steps")
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except ValueError:
            steps = []
    if not isinstance(steps, list):                    # what a model sent - the tool refuses it, the question must not fail
        return ""
    code = [str(s["eval"]) for s in steps if isinstance(s, dict) and "eval" in s]
    return ("\nJavaScript it runs in the page:\n" + "\n".join(code)) if code else ""


async def code_can_go_offline(hub) -> bool:
    """Whether the sandbox can run a command without the internet (its daemon tried confine.py --offline: Landlock
    ABI 4 and the seccomp filter); asked every few minutes."""
    at, ok = hub.extras.get("offline_code", (float("-inf"), False))
    if time.monotonic() - at < 300:
        return ok
    try:
        async with httpx.AsyncClient(timeout=5, transport=hub.transports.sandbox) as c:
            r = await c.get(hub.env.sandbox_url + "/health")
        h = r.json()
        ok = bool(h["offline"]) if "offline" in h else int(h.get("landlock") or 0) >= 4    # an older daemon: ABI
    except (httpx.HTTPError, ValueError, TypeError):
        ok = False
    hub.extras["offline_code"] = (time.monotonic(), ok)
    return ok
