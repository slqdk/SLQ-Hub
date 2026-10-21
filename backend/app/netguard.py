"""Who is asking: the client's real address, and whether it is on the LAN.

The Hub sits behind Nginx Proxy Manager for the web UI. Requests from the
proxy carry the real client in X-Forwarded-For; that header is believed only
when the request comes from an address in HUB_TRUSTED_PROXIES (uvicorn runs
with --no-proxy-headers, so the peer address is the true TCP peer).
"""
from __future__ import annotations

import ipaddress


def _ip(value: str):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def client_ip(peer: str | None, forwarded_for: str | None, trusted_proxies) -> tuple[str, bool]:
    """(client address, came through a trusted proxy)."""
    peer = (peer or "").strip()
    trusted = {p.strip() for p in trusted_proxies if p.strip()}
    if peer not in trusted:
        return peer, False
    if forwarded_for:
        hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
        for hop in reversed(hops):              # nearest hop last; skip our own proxies
            if hop not in trusted and _ip(hop) is not None:
                return hop, True
    return peer, True


def in_networks(ip: str, networks) -> bool:
    addr = _ip(ip)
    if addr is None:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in networks if net.version == addr.version)


def is_lan(ip: str, networks, sandbox_ip: str = "") -> bool:
    """True for addresses allowed to use the model API. The agent's sandbox
    is never one of them, whatever range it sits in."""
    if sandbox_ip and ip.strip() == sandbox_ip:
        return False
    return in_networks(ip, networks)
