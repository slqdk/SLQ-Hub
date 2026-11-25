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

Files from the working folder (0.36.0): a tool argument whose schema has
"format": "slq-hub-file" (a string, or the items of an array of strings) names
files in the chat's working folder. The model gives their paths; the Hub reads
them and sends their content with the call in params._meta["slq-hub/files"]: a
list of {param, arg (the path as the model gave it), path (the file's path in
the folder), name, size, data (base64)}. Only files inside the folder: a link is
followed only when it leads to a file inside it, and `path` is where that file
is. At most MAX_COUNT files, 20 MB a file and 50 MB a call; a file named twice
for one argument goes once. A server on another machine - a build PC - gets the
code without the model copying it into its arguments, and a server that does
not know the convention sees the paths.

A tool marked readOnlyHint (its annotations) only looks (Tool.only_looks): the
independent review may use it when its server is trusted and the owner lets the
review use that server ("review" in its settings). The review should not wait
for the user, and an untrusted server's tools would ask. Unlike the Hub's own
look-ups such a call is not refused when it is repeated: its result can change
with nothing changed in the chat (a build after a file was changed on the PC, a
live value).
"""
from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import os
import re
import time

import httpx

from ..chats import store as chats
from .tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger("hub.mcp")
PROTOCOL = "2025-06-18"
RETRY_AFTER_S = 60
NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
FILE_FORMAT = "slq-hub-file"            # an argument naming files in the working folder (0.36.0)
META_LIST, META_CALL = "mcp_tools", "mcp_call"   # 0.85.0: the servers' tools on demand, in two tools
FILE_META = "slq-hub/files"             # where their content goes: params._meta[FILE_META]
MAX_FILE = 20 * 1024 * 1024
MAX_FILES = 50 * 1024 * 1024            # all of one call's files together
MAX_COUNT = 1000                        # files in one call


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
        body = await _encoded({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
                              _with_files(params))
        return await asyncio.wait_for(self._post(body, rid), timeout)

    async def _post(self, body: bytes, rid: int) -> dict:
        try:
            async with self.client.stream("POST", self.url, content=body, headers=self._headers()) as r:
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

    async def _call(self, path: str, body: dict | bytes) -> dict:
        try:
            if isinstance(body, bytes):
                r = await self.client.post(self.base_url + path, content=body,
                                           headers={"Content-Type": "application/json"})
            else:
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
        """One message on the server's stdin: a line of JSON, itself a string in the request to the sandbox."""
        def body() -> bytes:
            return _dumps({"id": self.pid, "data": json.dumps(obj, ensure_ascii=False) + "\n"})
        await self._call("/proc/write", await asyncio.to_thread(body) if _with_files(obj.get("params")) else body())

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


def _text(spec: dict) -> str:
    d = spec.get("description") or spec.get("title") or spec.get("name") or ""
    return d.strip() if isinstance(d, str) else ""


def _gist(text: str, limit: int = 200) -> str:
    """A description's first sentence, short."""
    flat = " ".join(text.strip().split("\n\n", 1)[0].split())
    first = re.split(r"(?<=[.!?])\s", flat, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit - 3].rstrip() + "..."


def file_params(schema) -> list[tuple[str, bool]]:
    """The tool's top-level arguments that name working-folder files: (name, a list of them)."""
    props = schema.get("properties") if isinstance(schema, dict) else None
    out = []
    for name, p in (props if isinstance(props, dict) else {}).items():
        if not isinstance(p, dict):
            continue
        if p.get("format") == FILE_FORMAT:
            out.append((name, False))
        elif isinstance(p.get("items"), dict) and p["items"].get("format") == FILE_FORMAT:
            out.append((name, True))
    return out


def read_files(wd, args: dict, params: list[tuple[str, bool]]) -> list[dict]:
    """The files the arguments name, read from the working folder the way the ZIP download reads them: the path is
    resolved inside the folder (a link only when it stays inside), then the file opened without following a link on
    the way, regular files only. Raises ValueError with a message for the model."""
    files, total, seen = [], 0, set()
    for name, many in params:
        value = args.get(name)
        if value in (None, "", []):
            continue
        paths = value if many and isinstance(value, list) else [value]
        for path in paths:
            if not isinstance(path, str) or not path.strip():
                raise ValueError(f"{name}: give the path of a file in your working folder")
            rel = wd.rel(wd.resolve(path))
            if (name, rel) in seen:
                continue
            seen.add((name, rel))
            if len(files) >= MAX_COUNT:
                raise ValueError(f"more than {MAX_COUNT} files for one call")
            try:
                fd = wd.open_inside(rel)
            except FileNotFoundError:
                raise ValueError(f"{path}: there is no such file in your working folder") from None
            except OSError as exc:
                raise ValueError(f"{path}: cannot be read ({exc.strerror or exc})") from None
            with os.fdopen(fd, "rb") as f:
                data = f.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise ValueError(f"{path} is larger than {MAX_FILE // 2**20} MB")
            total += len(data)
            if total > MAX_FILES:
                raise ValueError(f"the files for one call are larger than {MAX_FILES // 2**20} MB together")
            files.append({"param": name, "arg": path, "path": rel, "name": rel.rsplit("/", 1)[-1],
                          "size": len(data), "data": base64.b64encode(data).decode()})
    return files


def _only_looks(spec: dict) -> bool:
    """The server says the tool does not change anything (MCP annotations.readOnlyHint)."""
    ann = spec.get("annotations") if isinstance(spec, dict) else None
    return bool(isinstance(ann, dict) and ann.get("readOnlyHint") is True)


def _tool_spec(t) -> dict | None:
    """A tool as tools/list gave it, kept when it has a name; a description or title that is not text is dropped
    (a server's mistake must not take the Settings page or the turn down)."""
    if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t["name"].strip():
        return None
    return {**t, **{k: "" for k in ("description", "title") if k in t and not isinstance(t[k], str)}}


def _dumps(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


async def _encoded(obj: dict, files: bool) -> bytes:
    """A message as JSON - off the event loop when files go with it (up to 50 MB of them, as base64)."""
    return await asyncio.to_thread(_dumps, obj) if files else _dumps(obj)


def _with_files(params: dict | None) -> bool:
    return isinstance(params, dict) and isinstance(params.get("_meta"), dict) and FILE_META in params["_meta"]


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
                    listed = res.get("tools")
                    tools += [t for t in map(_tool_spec, listed if isinstance(listed, list) else []) if t]
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

    async def call(self, hub, tool: str, args: dict, timeout: float, meta: dict | None = None) -> dict:
        params = {"name": tool, "arguments": args}
        if meta:
            params["_meta"] = meta
        for attempt in (1, 2):
            if self.status != "ready" or self.conn is None or self.conn.dead:
                await self.start(hub)
                if self.status != "ready":
                    raise McpError(f"the MCP server {self.name} is not running: {self.error}")
            try:
                return await self.conn.request("tools/call", params, timeout=timeout)
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
                        "trusted": bool(c.get("trusted")), "review": bool(c.get("review")),
                        "status": s.status if s else "stopped", "error": s.error if s else "",
                        "server": s.info if s else {},
                        "tools": [{"name": t["name"], "agent_name": self.tool_name(c["name"], t["name"]),
                                   "description": (t.get("description") or "")[:300], "only_looks": _only_looks(t),
                                   "files": [n for n, _ in file_params(t.get("inputSchema"))]}
                                  for t in (s.tools if s else [])]})
        return out

    @staticmethod
    def tool_name(server: str, tool: str) -> str:
        return f"mcp__{slug(server)}__{slug(tool)}"[:64]

    def tools(self, review: bool = False) -> list[Tool]:
        """The running servers' tools for the agent. review: those the independent review may use (0.36.0) - the
        tools that only look, of trusted servers the owner lets the review use."""
        out = []
        for key, s in self.servers.items():
            if s.status != "ready" or not s.cfg.get("enabled", True):
                continue
            if review and not (s.cfg.get("trusted") and s.cfg.get("review")):
                continue
            for t in s.tools:
                if review and not _only_looks(t):
                    continue
                name = self.tool_name(s.name, t["name"])

                async def handler(ctx: ToolContext, args: dict, _s=s, _t=t["name"]) -> ToolResult:
                    return await self._call(ctx, _s, _t, args)
                desc = (t.get("description") or t.get("title") or t["name"]).strip()
                files = [n for n, _ in file_params(t.get("inputSchema"))]
                note = (f" (Give {', '.join(files)} as paths in your working folder: the Hub sends those files' "
                        f"content to the server.)" if files else "")
                # Not read_only: the loop would refuse a repeated build or a live value read again (Tool.only_looks).
                out.append(Tool(name, f"[MCP server {s.name}] {desc}"[:1500 - len(note)] + note,
                                _clean_schema(t.get("inputSchema")), handler,
                                needs_approval=not s.cfg.get("trusted"), group="mcp", only_looks=_only_looks(t),
                                sends_files=tuple(files)))
        return out

    # ------------------------------------------------ on demand (0.85.0)

    def meta_tools(self, review: bool = False) -> list[Tool]:
        """Two tools in place of every MCP tool's schema in every request (0.85.0). Søren's turn of 2026-11-25 was
        48,158 tokens before it had begun, for a model that takes 32,768; the Hub's own part is about 7,000 - the
        rest was the MCP servers' tools (with them off, it fit). mcp_tools lists the servers' tools and describes one with its arguments; mcp_call calls
        it, and the loop unwraps that call into the tool itself (Engine._unwrap_mcp): its approval, its card, its
        "Always" are the tool's own, as before. Ten servers cost what one does, and the request stays the same from
        turn to turn, so the model server's prompt cache holds. review: those the independent review may use."""
        servers: dict[str, int] = {}
        for t in self.tools(review):
            name = self._server_of(t.name)
            servers[name] = servers.get(name, 0) + 1
        if not servers:
            return []
        listed = ", ".join(f"{n} ({k} tool{'s' if k != 1 else ''})" for n, k in servers.items())

        async def describe(ctx: ToolContext, args: dict) -> ToolResult:
            names = set(getattr(ctx.engine, "mcp_hidden", None) or {t.name for t in self.tools(review)})
            text, ok, full = self.describe(names, str(args.get("server") or ""), str(args.get("tool") or ""))
            if full and ctx.engine is not None:          # its schema is in the turn: a failed call needs no copy
                ctx.engine.mcp_described.add(full)
            return ToolResult(text, is_error=not ok)

        async def unwrapped(ctx: ToolContext, args: dict) -> ToolResult:      # the loop runs the tool itself
            return ToolResult("mcp_call is run by the Hub: call it again.", is_error=True)

        return [
            Tool(META_LIST,
                 f"The tools of the MCP servers connected to the Hub - {listed[:600]}. With no arguments: every "
                 "server's tools by name; with server: its tools, a line each; with server and tool: that tool's "
                 "description and its arguments (a JSON schema). Then call it with mcp_call.",
                 {"type": "object", "properties": {
                     "server": {"type": "string", "description": "A server's name: its tools, a line each."},
                     "tool": {"type": "string", "description": "With server: that tool's description and arguments."}}},
                 describe, read_only=True, group="mcp"),
            Tool(META_CALL,
                 "Call a tool of an MCP server - server and tool as mcp_tools names them, arguments as its schema in "
                 "mcp_tools says. A tool of a server the user has not marked as trusted asks the user first.",
                 {"type": "object", "properties": {
                     "server": {"type": "string", "description": "The server's name."},
                     "tool": {"type": "string", "description": "The tool's name on that server."},
                     "arguments": {"type": "object", "description": "The tool's arguments, a JSON object."}},
                  "required": ["server", "tool"]},
                 unwrapped, group="mcp"),
        ]

    def _server_of(self, full: str) -> str:
        """The server's name for a tool's agent name (mcp__<server>__<tool>)."""
        for s in self.servers.values():
            if full.startswith(f"mcp__{slug(s.name)}__"):
                return s.name
        return full.split("__")[1] if full.count("__") >= 2 else full

    def _offered(self, names: set[str]) -> list[tuple["McpServer", dict, str]]:
        """(server, its tool's spec, the agent name) of the tools among `names`, in the servers' order."""
        out = []
        for s in self.servers.values():
            if s.status != "ready" or not s.cfg.get("enabled", True):
                continue
            for spec in s.tools:
                full = self.tool_name(s.name, spec["name"])
                if full in names:
                    out.append((s, spec, full))
        return out

    def find(self, names: set[str], server: str, tool: str) -> str | None:
        """The agent name of `tool` on `server` among `names` (names as mcp_tools gives them, or close to it)."""
        if tool in names:                  # the tool's agent name, as before 0.85.0
            return tool
        full = self.tool_name(server or "", tool or "")
        if full in names:
            return full
        want_s, want_t = slug(server).lower() if server else "", slug(tool or "").lower()
        hits = [f for s, spec, f in self._offered(names)
                if slug(spec["name"]).lower() == want_t and (not want_s or slug(s.name).lower() == want_s)]
        return hits[0] if len(hits) == 1 else None      # no server: the tool's name alone, when only one has it

    def describe(self, names: set[str], server: str = "", tool: str = "") -> tuple[str, bool, str | None]:
        """mcp_tools' answer: every server's tools by name, one server's a line each, or one tool's description and
        arguments - of the tools among `names` (what this turn may use). (The text, whether what it asked for is
        there - a server or tool that is not gets the names that are -, the tool it describes.)"""
        rows = self._offered(names)
        if not rows:
            return "No MCP server's tools can be used now.", False, None
        servers = list(dict.fromkeys(s.name for s, _, _ in rows))
        if not server:
            out = ["The MCP servers' tools (mcp_tools with server for a line each, with server and tool for its "
                   "arguments; then mcp_call):"]
            for name in servers:
                tools = [spec["name"] for s, spec, _ in rows if s.name == name]
                out.append(f"- {name}: " + ", ".join(tools))
            return "\n".join(out)[:12000], True, None
        mine = [r for r in rows if slug(r[0].name).lower() == slug(server).lower()]
        if not mine:
            return f"There is no MCP server {server!r} here. The servers: {', '.join(servers)}.", False, None
        name = mine[0][0].name
        asks = "" if mine[0][0].cfg.get("trusted") else " (each call asks the user first)"
        if not tool:
            lines = [f"The tools of {name}{asks} - mcp_tools with server and tool gives one's arguments:"]
            for _, spec, _ in mine:
                lines.append(f"- {spec['name']}: {_gist(_text(spec))}")
            return "\n".join(lines)[:12000], True, None
        hit = next((r for r in mine if slug(r[1]["name"]).lower() == slug(tool).lower()), None)
        if hit is None:
            return (f"{name} has no tool {tool!r}. Its tools: {', '.join(spec['name'] for _, spec, _ in mine)}.",
                    False, None)
        _, spec, full = hit
        files = [n for n, _ in file_params(spec.get("inputSchema"))]
        note = (f"\nGive {', '.join(files)} as paths in your working folder: the Hub sends those files' content to "
                "the server." if files else "")
        schema = json.dumps(_clean_schema(spec.get("inputSchema")), ensure_ascii=False)
        return (f"{spec['name']} on {name}{asks}:\n{_text(spec)[:4000]}{note}\n"
                f"Its arguments (a JSON schema): {schema[:8000]}\n"
                f"Call it: mcp_call with server \"{name}\", tool \"{spec['name']}\" and arguments."), True, full

    def schema_of(self, full: str) -> str:
        """The arguments of a tool, by its agent name - for a call that went wrong."""
        for s, spec, f in self._offered({full}):
            return json.dumps(_clean_schema(spec.get("inputSchema")), ensure_ascii=False)[:4000]
        return ""

    async def _call(self, ctx: ToolContext, server: McpServer, tool: str, args: dict) -> ToolResult:
        timeout = float(server.cfg.get("timeout_s") or 300)
        spec = next((t for t in server.tools if t.get("name") == tool), {})
        params = file_params(spec.get("inputSchema"))
        meta = None
        if params:
            try:
                meta = {FILE_META: await asyncio.to_thread(read_files, ctx.workdir, args, params)}
            except ValueError as exc:
                return ToolResult(f"MCP {server.name}/{tool}: {exc}", is_error=True)
        try:
            res = await server.call(self.hub, tool, args, timeout, meta=meta)
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
