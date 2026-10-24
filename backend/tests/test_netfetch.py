"""The fetch guard (app/netfetch.py) behind web_fetch and image URLs from apps: only public addresses, every
redirect checked, and the connection made to the address the check approved - a name server that answers
differently the second time (DNS rebinding) cannot steer it into the LAN."""
from __future__ import annotations

import asyncio
import socket

import pytest

from app import netfetch


def fake_dns(monkeypatch, answers: dict[str, list[str]]) -> list[str]:
    """getaddrinfo as the check sees it; returns the list of names it was asked for."""
    asked: list[str] = []
    loop = asyncio.get_running_loop()

    async def getaddrinfo(host, port, *a, **k):
        asked.append(host)
        if host not in answers:
            raise socket.gaierror("no such name")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in answers[host]]
    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)
    return asked


async def serve(handler) -> tuple[asyncio.AbstractServer, int]:
    """A tiny HTTP server on 127.0.0.1: handler(request head) -> raw response bytes."""
    async def on(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        writer.write(handler(head.decode("latin-1")))
        await writer.drain()
        writer.close()
    server = await asyncio.start_server(on, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def ok(body: bytes, ctype: str = "text/plain") -> bytes:
    return (f"HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            .encode() + body)


async def test_only_public_addresses(monkeypatch):
    fake_dns(monkeypatch, {"router.lan": ["192.168.1.1"], "meta.test": ["169.254.169.254"], "mixed.test":
                           ["93.184.215.14", "10.0.0.5"], "cgnat.test": ["100.64.1.2"], "v6.test": ["::1"],
                           "public.test": ["2606:4700::1111", "93.184.215.14"]})
    for host in ("router.lan", "meta.test", "mixed.test", "cgnat.test", "v6.test"):
        with pytest.raises(netfetch.FetchError, match="not a public internet address"):
            await netfetch.check_url(f"https://{host}/")
    assert await netfetch.check_url("https://public.test/") == ["93.184.215.14", "2606:4700::1111"]   # IPv4 first
    import ipaddress
    with pytest.raises(netfetch.FetchError):                       # the LAN ranges and Settings' extra ranges
        await netfetch.check_url("https://public.test/", extra_blocked=[ipaddress.ip_network("93.184.0.0/16")])
    for bad in ("file:///etc/passwd", "ftp://public.test/", "http:///nohost"):
        with pytest.raises(netfetch.FetchError, match="only http"):
            await netfetch.check_url(bad)
    with pytest.raises(netfetch.FetchError, match="cannot resolve"):
        await netfetch.check_url("https://nowhere.test/")


async def test_the_connection_goes_to_the_checked_address(monkeypatch):
    """rebind.test resolves only for the check (to 127.0.0.1, allowed here): the fetch still reaches the server,
    so the connection used the checked address and never looked the name up again - and the Host header and
    the final URL keep the name."""
    seen: list[str] = []

    def handler(head: str) -> bytes:
        seen.append(head)
        return ok(b"hello from the checked address")
    server, port = await serve(handler)
    asked = fake_dns(monkeypatch, {"rebind.test": ["127.0.0.1"]})
    monkeypatch.setattr(netfetch, "_public", lambda addr, extra=(): True)
    real = socket.getaddrinfo

    def no_second_lookup(host, *a, **k):            # what the connection layer would use to resolve again
        assert host != "rebind.test", "the name was looked up a second time"
        return real(host, *a, **k)
    monkeypatch.setattr(socket, "getaddrinfo", no_second_lookup)
    try:
        data, ctype, final = await netfetch.fetch(f"http://rebind.test:{port}/page")
    finally:
        server.close()
    assert data == b"hello from the checked address" and ctype == "text/plain"
    assert final == f"http://rebind.test:{port}/page" and asked == ["rebind.test"]
    assert f"host: rebind.test:{port}" in seen[0].lower()


async def test_an_apps_picture_url_on_the_lan_is_refused(client, api_key, monkeypatch):
    """The home network may use public addresses (30.11.0.0/20 here): an image URL an app sends to the model API
    is refused there too, not only in the private ranges - as web_fetch does."""
    fake_dns(monkeypatch, {"camera.home.test": ["30.11.0.50"]})
    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json={
        "model": "qwen3.8-27b", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What is on the camera?"},
            {"type": "image_url", "image_url": {"url": "http://camera.home.test/snap.jpg"}}]}]})
    assert r.status_code == 400 and "not a public internet address" in r.text, r.text


def test_the_host_is_named_as_the_connection_names_it():
    """IDNA 2008, as httpx: straße.de is xn--strae-oqa.de (Python's own lookup would ask for strasse.de - another
    domain, and the approval would have named one site while the connection went to another)."""
    from app.agent import internet
    assert netfetch.host_key("https://straße.de/x") == "xn--strae-oqa.de"
    assert netfetch.host_key("http://WWW.DR.DK./") == "www.dr.dk."
    assert internet.host_of("http://WWW.DR.DK./") == "www.dr.dk" and internet.host_of("https://straße.de") == \
        "xn--strae-oqa.de"
    assert internet.host_of("ftp://dr.dk") == "" and internet.host_of("http://[bad") == ""
    for bad in ("http://exa\u0000mple.com/", "http:///nohost"):
        with pytest.raises(netfetch.FetchError):
            netfetch.host_key(bad)


async def test_idn_looks_up_the_name_it_connects_to(monkeypatch):
    asked = fake_dns(monkeypatch, {"xn--strae-oqa.de": ["93.184.215.14"], "strasse.de": ["93.184.215.99"]})
    assert await netfetch.check_url("https://straße.de/") == ["93.184.215.14"] and asked == ["xn--strae-oqa.de"]


def _cert(tmp_path, name: str) -> tuple[str, str]:
    """A self-signed certificate for `name` (openssl), as (cert file, key file)."""
    import shutil
    import subprocess
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    cert, key = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                    "-subj", f"/CN={name}", "-addext", f"subjectAltName=DNS:{name}", "-days", "1",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    return str(cert), str(key)


async def test_tls_goes_by_the_name_on_the_pinned_address(monkeypatch, tmp_path):
    """HTTPS through the pinned connection: the TCP connection goes to the checked address, while the TLS
    handshake names the host (SNI) and the certificate is verified against the name - a certificate for
    another name is refused."""
    import ssl

    import httpx
    cert, key = _cert(tmp_path, "pinned.test")
    sni: list[str] = []
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)
    server_ctx.sni_callback = lambda sock, name, ctx: sni.append(name)

    async def on(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(ok(b"secure hello"))
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            pass
        writer.close()
    server = await asyncio.start_server(on, "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]
    asked = fake_dns(monkeypatch, {"pinned.test": ["127.0.0.1"], "wrong.test": ["127.0.0.1"]})
    monkeypatch.setattr(netfetch, "_public", lambda addr, extra=(): True)
    monkeypatch.setattr(httpx, "create_ssl_context", lambda **k: ssl.create_default_context(cafile=cert))
    try:
        data, _, final = await netfetch.fetch(f"https://pinned.test:{port}/x")
        assert data == b"secure hello" and final == f"https://pinned.test:{port}/x"
        assert sni == ["pinned.test"] and asked == ["pinned.test"]
        with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED|certificate"):
            await netfetch.fetch(f"https://wrong.test:{port}/x")       # the certificate names pinned.test
    finally:
        server.close()


async def test_every_redirect_is_checked_and_pinned(monkeypatch):
    """A public page that redirects into the LAN is refused at the redirect; a redirect to another public host
    is checked and connected to on its own."""
    def handler(head: str) -> bytes:
        path = head.split(" ", 2)[1]
        if path == "/to-lan":
            return b"HTTP/1.1 302 Found\r\nLocation: http://router.lan/admin\r\nContent-Length: 0\r\n\r\n"
        if path == "/to-other":
            return (f"HTTP/1.1 301 Moved\r\nLocation: http://other.test:{port}/end\r\nContent-Length: 0\r\n\r\n"
                    .encode())
        return ok(b"end")
    server, port = await serve(handler)
    asked = fake_dns(monkeypatch, {"start.test": ["127.0.0.1"], "other.test": ["127.0.0.1"],
                                   "router.lan": ["192.168.1.1"]})
    monkeypatch.setattr(netfetch, "_public", lambda addr, extra=(): not addr.startswith("192.168."))
    try:
        with pytest.raises(netfetch.FetchError, match="router.lan is not a public internet address"):
            await netfetch.fetch(f"http://start.test:{port}/to-lan")
        data, _, final = await netfetch.fetch(f"http://start.test:{port}/to-other")
    finally:
        server.close()
    assert data == b"end" and final == f"http://other.test:{port}/end"
    assert asked == ["start.test", "router.lan", "start.test", "other.test"]
