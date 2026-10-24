"""Fetching from the internet on behalf of a model or an app - never from the
LAN or the Hub's own containers (an SSRF guard).

Every hop is checked: the host's addresses must all be public, and a
redirect is followed by hand so its target is checked too. The connection
then goes to an address the check approved - the name is not looked up a
second time, so a name server that answers differently the second time
(DNS rebinding) cannot steer the request into the LAN. TLS is still verified
against the host name.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpcore
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


def blocked_networks(hub) -> list:
    """The ranges a fetch refuses besides the private ones: the LAN's (HUB_LAN_CIDRS - a home network may use
    public addresses, as 30.11.0.0/20 does) and Settings -> Web's blocked ranges."""
    out = []
    for cidr in [str(n) for n in hub.env.lan_networks] + list(hub.settings.get("web", "blocked_cidrs") or []):
        try:
            out.append(ipaddress.ip_network(str(cidr), strict=False))
        except ValueError:
            pass
    return out


class Redirected(FetchError):
    """A redirect the caller did not want followed (to another site while the internet asks first)."""

    def __init__(self, url: str):
        super().__init__(f"redirects to {url}")
        self.url = url


def host_key(url: str) -> str:
    """The host as the connection names it - httpx's form: lower case, IDNA 2008 (straße.de is
    xn--strae-oqa.de, not strasse.de), no brackets. The check looks this name up, and the pins, TLS and the Host
    header use it, so what was checked is what is connected to."""
    try:
        host = httpx.URL(url).raw_host.decode("ascii")
    except (httpx.InvalidURL, UnicodeError, ValueError) as exc:
        raise FetchError(f"not a valid web address: {str(url)[:200]}") from exc
    if not host:
        raise FetchError("only http(s) addresses can be fetched")
    return host


async def check_url(url: str, extra_blocked=()) -> list[str]:
    """The host's addresses, all public - IPv4 first - or FetchError."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("only http(s) addresses can be fetched")
    host = host_key(url)
    port = httpx.URL(url).port or (443 if p.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError(f"cannot resolve {host}") from exc
    addrs = list(dict.fromkeys(i[4][0] for i in infos))
    if not addrs or not all(_public(a, extra_blocked) for a in addrs):
        raise FetchError(f"{host} is not a public internet address - refused")
    return sorted(addrs, key=lambda a: ":" in a)          # IPv4 first: home networks often lack IPv6 routes


class _Pinned(httpcore.AsyncNetworkBackend):
    """Connects to the addresses the check approved for a host, in order, instead of resolving its name again."""

    def __init__(self, pins: dict[str, list[str]]):
        self.pins = pins
        self.inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addrs = self.pins.get(host)
        if not addrs:
            raise httpcore.ConnectError(f"{host} was not checked")
        last: Exception | None = None
        for addr in addrs:
            try:
                return await self.inner.connect_tcp(addr, port, timeout=timeout, local_address=local_address,
                                                    socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last = exc
        raise last if last else httpcore.ConnectError(f"cannot connect to {host}")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("not a web address")

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


class _PinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, pins: dict[str, list[str]]):
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(ssl_context=httpx.create_ssl_context(trust_env=False),
                                                  network_backend=_Pinned(pins))


async def fetch(url: str, *, max_bytes: int = 15_000_000, timeout: float = 30, extra_blocked=(),
                accept: str = "*/*", transport: httpx.AsyncBaseTransport | None = None,
                skip_check: bool = False, redirect_ok=None) -> tuple[bytes, str, str]:
    """(body, content type, final url). Follows up to 5 redirects, each checked, each connection to an address
    its check approved. redirect_ok(url) -> bool: whether to follow a redirect there (Redirected if not)."""
    current = url
    pins: dict[str, list[str]] = {}
    if transport is None:
        transport = _PinnedTransport(pins)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=transport, trust_env=False,
                                 headers={"User-Agent": USER_AGENT, "Accept": accept}) as c:
        for _ in range(6):
            if not skip_check:
                pins[host_key(current)] = await check_url(current, extra_blocked)
            async with c.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    nxt = urljoin(current, r.headers["location"])
                    if redirect_ok is not None and not redirect_ok(nxt):
                        raise Redirected(nxt)
                    current = nxt
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
