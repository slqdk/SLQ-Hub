"""Client for the llama-server supervisor in the `llm` container."""
from __future__ import annotations

import asyncio
import re
import time

import httpx

MAIN = ("main", 9100)            # the one LLM on the GPU
AUX_PORTS = {"embed-text": 9101, "rerank": 9102, "embed-vl": 9103, "embed-gpu": 9104}


class RunnerError(RuntimeError):
    pass


class Runner:
    def __init__(self, url: str, token: str, llm_host: str, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url.rstrip("/")
        self.token = token
        self.llm_host = llm_host
        self._transport = transport

    def client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport,
                                 headers={"X-Internal-Token": self.token} if self.token else {})

    def base_url(self, port: int) -> str:
        return f"http://{self.llm_host}:{port}"

    async def _call(self, method: str, path: str, timeout: float = 30, **kw):
        try:
            async with self.client(timeout) as c:
                r = await c.request(method, self.url + path, **kw)
        except httpx.HTTPError as exc:
            raise RunnerError(f"the model supervisor at {self.url} is not answering ({exc.__class__.__name__})") from exc
        if r.status_code >= 400:
            try:
                msg = r.json().get("error") or r.text
            except ValueError:
                msg = r.text
            raise RunnerError(f"supervisor {path}: {r.status_code} {msg}")
        return r

    async def status(self) -> dict:
        return (await self._call("GET", "/status")).json()

    async def gpu(self) -> dict:
        try:
            return (await self._call("GET", "/gpu", timeout=5)).json()
        except RunnerError:
            return {}

    async def start(self, name: str, port: int, args: list[str]) -> dict:
        return (await self._call("POST", "/start", json={"name": name, "port": port, "args": args})).json()

    async def stop(self, name: str | None = None, everything: bool = False) -> dict:
        body = {"all": True} if everything else {"name": name}
        return (await self._call("POST", "/stop", timeout=60, json=body)).json()

    async def logs(self, name: str, tail: int = 200) -> str:
        try:
            return (await self._call("GET", "/logs", params={"name": name, "tail": tail})).text
        except RunnerError as exc:
            return f"(no log: {exc})"

    async def wait_ready(self, name: str, port: int, timeout: float = 900, poll: float = 1.0) -> None:
        """Until llama-server's /health says ok. Fails early when the process exits."""
        deadline = time.monotonic() + timeout
        url = self.base_url(port) + "/health"
        async with httpx.AsyncClient(timeout=5, transport=self._transport) as c:
            while time.monotonic() < deadline:
                try:
                    r = await c.get(url)
                    if r.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                st = await self.status()
                proc = (st.get("processes") or {}).get(name)
                if not proc or proc.get("state") != "running":
                    tail = await self.logs(name, 80)
                    raise RunnerError(f"llama-server '{name}' stopped while loading "
                                      f"(exit {proc.get('returncode') if proc else '?'}): {_why(tail)}")
                await asyncio.sleep(poll)
        raise RunnerError(f"llama-server '{name}' was not ready within {int(timeout)} s")


_STAMP = re.compile(r"^\d+\.\d+\.\d+\.\d+ ([EWID]) +(.*)$")


def _why(log: str) -> str:
    """The lines of a llama-server log that say why it stopped: its E-level
    lines (without the "fit params" retry noise), else the last lines."""
    errors, plain = [], []
    for raw in log.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _STAMP.match(line)
        text = m.group(2).strip() if m else line
        plain.append(text)
        if (m and m.group(1) == "E") or (not m and line.lower().startswith(("error", "terminate"))):
            if "fit params" not in text:
                errors.append(text)
    picked = list(dict.fromkeys(errors))[:4] or plain[-6:]
    return " | ".join(picked)[:1500]
