#!/usr/bin/env python3
"""The sandbox daemon: runs the agent's commands for the Hub (stdlib only).

Runs as the unprivileged `sandbox` user inside the sandbox container. Every
call needs the shared token in X-Internal-Token. Working directories must be
inside /workspace (the Hub's project and chat folders).

    GET  /health
    POST /exec        {cmd, cwd, timeout, env, max_output}
                      -> {exit_code, stdout, stderr, timed_out, duration_ms, truncated}
    POST /proc/start  {cmd, cwd, env}     -> {id}         long-lived process with
    POST /proc/write  {id, data}          -> {ok}         pipes (MCP stdio servers)
    POST /proc/read   {id, timeout}       -> {data, eof, exit_code}
    POST /proc/kill   {id}                -> {ok}
"""
from __future__ import annotations

import collections
import json
import os
import secrets
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

TOKEN = os.environ.get("EXECD_TOKEN", "")
ROOT = os.path.realpath(os.environ.get("EXECD_ROOT", "/workspace"))
PORT = int(os.environ.get("EXECD_PORT", "7000"))
MAX_TIMEOUT = 3600
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_OUTPUT = 100_000
MAX_PROCS = 16


def _inside_root(path: str) -> str:
    real = os.path.realpath(path or ROOT)
    if real != ROOT and not real.startswith(ROOT + os.sep):
        raise ValueError(f"cwd must be inside {ROOT}")
    if not os.path.isdir(real):
        raise ValueError(f"cwd does not exist: {real}")
    return real


def _env(extra: dict | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "EXECD_TOKEN"}
    env.setdefault("HOME", "/home/sandbox")
    env["TERM"] = "dumb"
    env["PYTHONUNBUFFERED"] = "1"
    for k, v in (extra or {}).items():
        if isinstance(k, str) and isinstance(v, str) and k != "EXECD_TOKEN":
            env[k] = v
    return env


class Capped:
    """Keeps the head of a stream and a rolling tail once it passes the cap."""

    def __init__(self, cap: int):
        self.cap = max(1000, cap)
        self.head = bytearray()
        self.tail: collections.deque[bytes] = collections.deque()
        self.tail_len = 0
        self.total = 0

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self.cap - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self.tail.append(chunk)
            self.tail_len += len(chunk)
            limit = self.cap // 4
            while self.tail_len > limit and self.tail:
                drop = self.tail.popleft()
                self.tail_len -= len(drop)

    def text(self) -> tuple[str, bool]:
        head = self.head.decode("utf-8", "replace")
        if self.total <= len(self.head):
            return head, False
        tail = b"".join(self.tail).decode("utf-8", "replace")
        omitted = self.total - len(self.head) - self.tail_len
        return f"{head}\n... [{omitted} bytes omitted] ...\n{tail}", True


def _drain(stream, sink: Capped) -> None:
    for chunk in iter(lambda: stream.read(65536), b""):
        sink.add(chunk)


def run_exec(body: dict) -> dict:
    cmd = body.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError("cmd must be a non-empty string")
    cwd = _inside_root(body.get("cwd") or ROOT)
    timeout = max(1, min(MAX_TIMEOUT, int(body.get("timeout") or DEFAULT_TIMEOUT)))
    cap = max(1000, min(2_000_000, int(body.get("max_output") or DEFAULT_MAX_OUTPUT)))
    t0 = time.monotonic()
    p = subprocess.Popen(["bash", "-c", cmd], cwd=cwd, env=_env(body.get("env")),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    out, err = Capped(cap), Capped(cap // 2)
    threads = [threading.Thread(target=_drain, args=(p.stdout, out), daemon=True),
               threading.Thread(target=_drain, args=(p.stderr, err), daemon=True)]
    for t in threads:
        t.start()
    timed_out = False
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=10)
    for t in threads:
        t.join(timeout=5)
    stdout, t1 = out.text()
    stderr, t2 = err.text()
    return {"exit_code": p.returncode, "stdout": stdout, "stderr": stderr, "timed_out": timed_out,
            "duration_ms": int((time.monotonic() - t0) * 1000), "truncated": t1 or t2}


class LongProc:
    def __init__(self, cmd: str, cwd: str, env: dict):
        self.p = subprocess.Popen(["bash", "-c", cmd], cwd=cwd, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        self.buf: collections.deque[bytes] = collections.deque()
        self.cond = threading.Condition()
        self.eof = False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.p.stdout
        for chunk in iter(lambda: self.p.stdout.read1(65536), b""):
            with self.cond:
                self.buf.append(chunk)
                self.cond.notify_all()
        with self.cond:
            self.eof = True
            self.cond.notify_all()

    def read(self, timeout: float) -> dict:
        with self.cond:
            if not self.buf and not self.eof:
                self.cond.wait(timeout=timeout)
            data = b"".join(self.buf)
            self.buf.clear()
            return {"data": data.decode("utf-8", "replace"), "eof": self.eof and not data,
                    "exit_code": self.p.poll()}

    def write(self, data: str) -> None:
        assert self.p.stdin
        self.p.stdin.write(data.encode())
        self.p.stdin.flush()

    def kill(self) -> None:
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


_procs: dict[str, LongProc] = {}
_plock = threading.Lock()


def proc_start(body: dict) -> dict:
    cmd = body.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError("cmd must be a non-empty string")
    with _plock:
        for pid, lp in list(_procs.items()):
            if lp.p.poll() is not None:
                del _procs[pid]
        if len(_procs) >= MAX_PROCS:
            raise ValueError(f"at most {MAX_PROCS} long-lived processes")
        pid = secrets.token_hex(8)
        _procs[pid] = LongProc(cmd, _inside_root(body.get("cwd") or ROOT), _env(body.get("env")))
    return {"id": pid}


def _get(pid: str) -> LongProc:
    with _plock:
        lp = _procs.get(pid or "")
    if lp is None:
        raise ValueError("no such process")
    return lp


class Handler(BaseHTTPRequestHandler):
    server_version = "slq-hub-sandbox/1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path == "/health":
            return self._send(200, {"ok": True, "root": ROOT, "uid": os.getuid()})
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if TOKEN and self.headers.get("X-Internal-Token") != TOKEN:
            return self._send(401, {"error": "bad or missing X-Internal-Token"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "body must be JSON"})
        path = urlparse(self.path).path
        try:
            if path == "/exec":
                return self._send(200, run_exec(body))
            if path == "/proc/start":
                return self._send(200, proc_start(body))
            if path == "/proc/write":
                _get(body.get("id")).write(str(body.get("data") or ""))
                return self._send(200, {"ok": True})
            if path == "/proc/read":
                return self._send(200, _get(body.get("id")).read(
                    max(0.0, min(60.0, float(body.get("timeout") or 10)))))
            if path == "/proc/kill":
                lp = _get(body.get("id"))
                lp.kill()
                with _plock:
                    _procs.pop(body.get("id"), None)
                return self._send(200, {"ok": True})
        except (ValueError, TypeError, BrokenPipeError) as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        self._send(404, {"error": "not found"})


def main() -> None:
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True
    print(f"slq-hub sandbox daemon on :{PORT}, root {ROOT}, uid {os.getuid()}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
