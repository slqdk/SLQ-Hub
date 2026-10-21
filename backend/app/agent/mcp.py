"""MCP servers: their tools become agent tools named mcp__<server>__<tool>.

Two transports, configured in Settings -> MCP:

* http  - Streamable HTTP, the current MCP transport: JSON-RPC POSTed to one
  URL, answered with JSON or an SSE stream. Headers (an API key) can be set.
* stdio - a command started inside the sandbox through execd's /proc API
  (no LAN access, like everything the agent runs), JSON-RPC as lines on
  stdin/stdout. Example: `npx -y @modelcontextprotocol/server-everything`.

A small client of our own (initialize, tools/list, tools/call, ping) rather
than the SDK: it is all the Hub needs and easy to test. Calls to an MCP tool
ask the user first unless the server is marked trusted or the user chose
"Always allow" for that tool.
"""
from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import re
import time

import httpx

from ..chats import store as chats
from .tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger("hub.mcp")
PROTOCOL = "2025-06-18"
RETRY_AFTER_S = 60
NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


class McpError(RuntimeError):
    pass


def _unwrap(msg: dict) -> dict:
    if "error" in msg:
        err = msg["error"] or {}
        raise McpError(f"{err.get('message') or 'error'} (code {err.get('code')})")
    return msg.get("result") or {}


async def _sse_messages(r: httpx.Response):
    data: list[str] = []
    async for line in r.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line == "" and data:
            try:
                yield json.loads("\n".join(data))
            except ValueError:
                pass
            data = []
    if data:
        try:
            yield json.loads("\n".join(data))
        except ValueError:
            pass


class HttpConnection:
    def __init__(self, url: str, headers: dict | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10), transport=transport)
        self.session_id: str | None = None
        self.protocol: str | None = None
        self._ids = itertools.count(1)
        self.dead = False

    def _headers(self) -> dict:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        return h

    async def start(self) -> None:
        return None

    async def request(self, method: str, params: dict | None = None, timeout: float = 120) -> dict:
        rid = next(self._ids)
        body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        return await asyncio.wait_for(self._post(body, rid), timeout)

    async def _post(self, body: dict, rid: int) -> dict:
        try:
            async with self.client.stream("POST", self.url, json=body, headers=self._headers()) as r:
                if r.headers.get("mcp-session-id"):
                    self.session_id = r.headers["mcp-session-id"]
                if r.status_code == 404 and self.session_id:
                    self.dead = True
                    raise McpError("the server ended the session")
                if r.status_code >= 400:
                    text = (await r.aread()).decode("utf-8", "replace")[:300]
                    raise McpError(f"HTTP {r.status_code}: {text}")
                if "text/event-stream" in r.headers.get("content-type", ""):
                    async for msg in _sse_messages(r):
                        if isinstance(msg, dict) and msg.get("id") == rid and ("result" in msg or "error" in msg):
                            return _unwrap(msg)
                    raise McpError("the stream ended without an answer")
                data = json.loads(await r.aread() or b"{}")
        except httpx.HTTPError as exc:
            raise McpError(f"cannot reach {self.url} ({exc.__class__.__name__})") from exc
        if isinstance(data, list):
            data = next((m for m in data if isinstance(m, dict) and m.get("id") == rid), {})
        return _unwrap(data)

    async def notify(self, method: str, params: dict | None = None) -> None:
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        try:
            r = await self.client.post(self.url, json=body, headers=self._headers())
            await r.aclose()
        except httpx.HTTPError:
            pass

    async def close(self) -> None:
        if self.session_id:
            try:
                await self.client.delete(self.url, headers=self._headers(), timeout=5)
            except httpx.HTTPError:
                pass
        await self.client.aclose()


class SandboxConnection:
    """A stdio MCP server running in the sandbox container."""

    def __init__(self, base_url: str, token: str, command: str, env: dict | None = None, cwd: str = "/workspace",
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url, self.command, self.env, self.cwd = base_url, command, env or {}, cwd
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), transport=transport,
                                        headers={"X-Internal-Token": token} if token else {})
        self.pid: str | None = None
        self.protocol: str | None = None
        self.pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._reader: asyncio.Task | None = None
        self.dead = False

    async def _call(self, path: str, body: dict) -> dict:
        try:
            r = await self.client.post(self.base_url + path, json=body)
        except httpx.HTTPError as exc:
            raise McpError(f"the sandbox is not answering ({exc.__class__.__name__})") from exc
        if r.status_code >= 400:
            try:
                msg = r.json().get("error") or r.text
            except ValueError:
                msg = r.text
            raise McpError(f"sandbox: {msg}")
        return r.json()

    async def start(self) -> None:
        res = await self._call("/proc/start", {"cmd": self.command, "cwd": self.cwd, "env": self.env})
        self.pid = res["id"]
        self._reader = asyncio.get_running_loop().create_task(self._read_loop())

    async def _read_loop(self) -> None:
        buf = ""
        why = "the server process ended"
        try:
            while True:
                res = await self._call("/proc/read", {"id": self.pid, "timeout": 20})
                buf += res.get("data") or ""
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    await self._dispatch(line.strip())
                if res.get("eof"):
                    why = f"the server process ended (exit code {res.get('exit_code')})"
                    break
        except asyncio.CancelledError:
            why = "closed"
            raise
        except McpError as exc:
            why = str(exc)
        finally:
            self.dead = True
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(McpError(why))
            self.pending.clear()

    async def _dispatch(self, line: str) -> None:
        if not line.startswith("{"):
            return                                   # a server logging to stdout
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self.pending.pop(msg["id"], None)
            if fut and not fut.done():
                fut.set_result(msg)
        elif "id" in msg and "method" in msg:        # a request from the server
            if msg["method"] == "ping":
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
            elif msg["method"] == "roots/list":
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {"roots": []}}
            else:
                reply = {"jsonrpc": "2.0", "id": msg["id"],
                         "error": {"code": -32601, "message": f"{msg['method']} is not supported by SLQ Hub"}}
            await self._write(reply)

    async def _write(self, obj: dict) -> None:
        await self._call("/proc/write", {"id": self.pid, "data": json.dumps(obj, ensure_ascii=False) + "\n"})

    async def request(self, method: str, params: dict | None = None, timeout: float = 120) -> dict:
        if self.dead:
            raise McpError("the server process is not running")
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        try:
            await self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            msg = await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)
        return _unwrap(msg)

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self.pid:
            try:
                await self._call("/proc/kill", {"id": self.pid})
            except McpError:
                pass
        await self.client.aclose()


def slug(s: str) -> str:
    return NAME_RE.sub("_", s or "").strip("_") or "x"


def _clean_schema(schema) -> dict:
    """Drop the JSON-schema keys llama.cpp's grammar converter does not need."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    drop = {"$schema", "$id", "$comment", "examples"}

    def walk(x):
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items() if k not in drop}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    out = walk(schema)
    out.setdefault("type", "object")
    if out.get("type") == "object":
        out.setdefault("properties", {})
    return out


class McpServer:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.conn: HttpConnection | SandboxConnection | None = None
        self.tools: list[dict] = []
        self.status = "stopped"
        self.error = ""
        self.info: dict = {}
        self.failed_at = 0.0
        self.lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return str(self.cfg.get("name") or "server")

    def _connect(self, hub):
        if self.cfg.get("transport", "http") == "stdio":
            if not (self.cfg.get("command") or "").strip():
                raise McpError("no command set")
            return SandboxConnection(hub.env.sandbox_url, hub.env.internal_token, self.cfg["command"],
                                     self.cfg.get("env") or {}, cwd=self.cfg.get("cwd") or "/workspace",
                                     transport=hub.transports.sandbox)
        if not (self.cfg.get("url") or "").startswith(("http://", "https://")):
            raise McpError("the URL must start with http:// or https://")
        return HttpConnection(self.cfg["url"], self.cfg.get("headers") or {}, transport=hub.transports.web)

    async def start(self, hub) -> None:
        async with self.lock:
            if self.status == "ready" and self.conn and not self.conn.dead:
                return
            await self._close()
            self.status, self.error = "starting", ""
            try:
                self.conn = self._connect(hub)
                await self.conn.start()
                init = await self.conn.request("initialize", {
                    "protocolVersion": PROTOCOL, "capabilities": {},
                    "clientInfo": {"name": "slq-hub", "version": hub.env.version}}, timeout=45)
                self.conn.protocol = init.get("protocolVersion") or PROTOCOL
                self.info = init.get("serverInfo") or {}
                await self.conn.notify("notifications/initialized")
                tools, cursor = [], None
                for _ in range(20):
                    res = await self.conn.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=30)
                    tools += [t for t in res.get("tools") or [] if isinstance(t, dict) and t.get("name")]
                    cursor = res.get("nextCursor")
                    if not cursor:
                        break
                self.tools = tools
                self.status = "ready"
            except (McpError, asyncio.TimeoutError, OSError, ValueError, KeyError) as exc:
                self.status, self.error = "error", str(exc) or exc.__class__.__name__
                self.failed_at = time.monotonic()
                log.warning("MCP server %s: %s", self.name, self.error)
                await self._close()
            except asyncio.CancelledError:
                self.status, self.error = "error", "took too long to start (it is tried again later)"
                self.failed_at = time.monotonic()
                await self._close()
                raise

    async def call(self, hub, tool: str, args: dict, timeout: float) -> dict:
        for attempt in (1, 2):
            if self.status != "ready" or self.conn is None or self.conn.dead:
                await self.start(hub)
                if self.status != "ready":
                    raise McpError(f"the MCP server {self.name} is not running: {self.error}")
            try:
                return await self.conn.request("tools/call", {"name": tool, "arguments": args}, timeout=timeout)
            except McpError:
                if attempt == 2 or not (self.conn and self.conn.dead):
                    raise
                self.status = "stopped"                # the session or the process went away: once more
        raise McpError("unreachable")

    async def _close(self) -> None:
        if self.conn is not None:
            try:
                await self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.conn = None

    async def stop(self) -> None:
        async with self.lock:
            await self._close()
            self.status = "stopped"


class McpManager:
    def __init__(self, hub):
        self.hub = hub
        self.servers: dict[str, McpServer] = {}

    def configs(self) -> list[dict]:
        out = []
        for c in self.hub.settings.get("mcp", "servers") or []:
            if isinstance(c, dict) and c.get("name"):
                out.append(c)
        return out

    async def reload(self) -> None:
        """Apply Settings -> MCP: restart what changed, start what is new."""
        wanted = {slug(c["name"]): c for c in self.configs()}
        for key in list(self.servers):
            s = self.servers[key]
            if wanted.get(key) != s.cfg:
                await s.stop()
                self.servers.pop(key)
        for key, cfg in wanted.items():
            self.servers.setdefault(key, McpServer(cfg))
        for s in self.servers.values():
            if not s.cfg.get("enabled", True) and s.status != "stopped":
                await s.stop()
        await self.ensure_started(force=True, wait=180)

    async def ensure_started(self, force: bool = False, wait: float = 20) -> None:
        for c in self.configs():
            self.servers.setdefault(slug(c["name"]), McpServer(c))
        todo = [s for s in self.servers.values()
                if s.cfg.get("enabled", True) and s.status != "ready"
                and (force or time.monotonic() - s.failed_at > RETRY_AFTER_S)]
        if todo:
            await asyncio.gather(*(asyncio.wait_for(s.start(self.hub), wait) for s in todo), return_exceptions=True)

    def status(self) -> list[dict]:
        out = []
        for c in self.configs():
            s = self.servers.get(slug(c["name"]))
            out.append({"name": c["name"], "transport": c.get("transport", "http"), "enabled": c.get("enabled", True),
                        "trusted": bool(c.get("trusted")), "status": s.status if s else "stopped",
                        "error": s.error if s else "", "server": s.info if s else {},
                        "tools": [{"name": t["name"], "agent_name": self.tool_name(c["name"], t["name"]),
                                   "description": (t.get("description") or "")[:300]}
                                  for t in (s.tools if s else [])]})
        return out

    @staticmethod
    def tool_name(server: str, tool: str) -> str:
        return f"mcp__{slug(server)}__{slug(tool)}"[:64]

    def tools(self) -> list[Tool]:
        out = []
        for key, s in self.servers.items():
            if s.status != "ready" or not s.cfg.get("enabled", True):
                continue
            for t in s.tools:
                name = self.tool_name(s.name, t["name"])

                async def handler(ctx: ToolContext, args: dict, _s=s, _t=t["name"]) -> ToolResult:
                    return await self._call(ctx, _s, _t, args)
                desc = (t.get("description") or t.get("title") or t["name"]).strip()
                out.append(Tool(name, f"[MCP server {s.name}] {desc}"[:1500], _clean_schema(t.get("inputSchema")),
                                handler, needs_approval=not s.cfg.get("trusted"), group="mcp"))
        return out

    async def _call(self, ctx: ToolContext, server: McpServer, tool: str, args: dict) -> ToolResult:
        timeout = float(server.cfg.get("timeout_s") or 300)
        try:
            res = await server.call(self.hub, tool, args, timeout)
        except (McpError, asyncio.TimeoutError) as exc:
            return ToolResult(f"MCP {server.name}/{tool}: {exc or 'timed out'}", is_error=True)
        texts, images = [], []
        for part in res.get("content") or []:
            kind = part.get("type")
            if kind == "text":
                texts.append(part.get("text") or "")
            elif kind == "image" and part.get("data"):
                try:
                    data = base64.b64decode(part["data"])
                except ValueError:
                    continue
                mime = part.get("mimeType") or "image/png"
                up = chats.save_upload(data, f"{slug(tool)}.{mime.split('/')[-1]}", mime)
                images.append({"sha": up["sha"], "mime": mime})
            elif kind == "resource":
                r = part.get("resource") or {}
                texts.append(r.get("text") or f"[resource {r.get('uri')} ({r.get('mimeType')})]")
            elif kind == "resource_link":
                texts.append(f"[resource link: {part.get('name') or ''} {part.get('uri')}]")
            elif kind == "audio":
                texts.append("[audio content - not supported here]")
        if not texts and res.get("structuredContent") is not None:
            texts.append(json.dumps(res["structuredContent"], ensure_ascii=False, indent=1))
        return ToolResult("\n".join(texts).strip() or "(no output)", images=images, is_error=bool(res.get("isError")),
                          display={"mcp": {"server": server.name, "tool": tool}})

    async def close(self) -> None:
        for s in self.servers.values():
            await s.stop()
