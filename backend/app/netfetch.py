"""Fetching from the internet on behalf of a model or an app - never from the
LAN or the Hub's own containers (an SSRF guard).

Every hop is checked: the host's addresses must all be public, and a
redirect is followed by hand so its target is checked too.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) SLQ-Hub/1.0 (+self-hosted assistant)"


class FetchError(RuntimeError):
    pass


def _public(addr: str, extra_blocked=()) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or not ip.is_global):
        return False
    return not any(ip in net for net in extra_blocked if net.version == ip.version)


async def check_url(url: str, extra_blocked=()) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("only http(s) addresses can be fetched")
    host = p.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                                              type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve {host}") from exc
    addrs = {i[4][0] for i in infos}
    if not addrs or not all(_public(a, extra_blocked) for a in addrs):
        raise FetchError(f"{host} is not a public internet address - refused")


async def fetch(url: str, *, max_bytes: int = 15_000_000, timeout: float = 30, extra_blocked=(),
                accept: str = "*/*", transport: httpx.AsyncBaseTransport | None = None,
                skip_check: bool = False) -> tuple[bytes, str, str]:
    """(body, content type, final url). Follows up to 5 redirects, each checked."""
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=transport,
                                 headers={"User-Agent": USER_AGENT, "Accept": accept}) as c:
        for _ in range(6):
            if not skip_check:
                await check_url(current, extra_blocked)
            async with c.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise FetchError(f"{current} answered {r.status_code}")
                ctype = r.headers.get("content-type", "")
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise FetchError(f"{current} is larger than {max_bytes // 1_000_000} MB")
                return bytes(buf), ctype, str(r.url)
    raise FetchError("too many redirects")
