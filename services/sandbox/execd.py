#!/usr/bin/env python3
"""The sandbox daemon: runs the agent's commands for the Hub (stdlib only).

Runs as the unprivileged `sandbox` user inside the sandbox container. Every
call needs the shared token in X-Internal-Token. Working directories must be
inside /workspace (the Hub's project and chat folders).

    GET  /health                          -> {ok, root, uid, landlock: ABI or 0, offline: bool, windows: bool,
                                              browser: bool}
    POST /exec        {cmd, cwd, timeout, env, max_output, confine, offline, end_leftovers}
                      -> {exit_code, stdout, stderr, timed_out, duration_ms, truncated}
                      confine {root, home, require}: the command may touch only
                      that folder (confine.py, Landlock); with require it is not
                      run where Landlock is missing (exit code 126). Processes it
                      leaves running are killed when it ends.
                      offline: no network at all (Landlock ABI 4 and a seccomp
                      filter; not run where that cannot be enforced: exit code 126).
                      end_leftovers: kill what an unconfined command leaves running
                      too (the owner's command allowed online once).
                      windows (in /health): Windows programs can run here
                      (winshot.py --check: Wine, its prefix, the Windows .NET runtime).
                      browser (in /health): web pages can be opened in a headless
                      browser here (pageshot.py --check: Chromium starts).
    POST /proc/start  {cmd, cwd, env}     -> {id}         long-lived process with
    POST /proc/write  {id, data}          -> {ok}         pipes (MCP stdio servers)
    POST /proc/read   {id, timeout}       -> {data, eof, exit_code}
    POST /proc/kill   {id}                -> {ok}
"""
from __future__ import annotations

import collections
import ctypes
import json
import os
import secrets
import signal
import subprocess
import sys
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
CONFINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "confine.py")
WINSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "winshot.py")
PAGESHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pageshot.py")
PAGESHOT_PYTHON = os.environ.get("PAGESHOT_PYTHON", "/opt/pageshot/bin/python")


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


def _confined(cmd: str, cwd: str, conf, offline: bool = False) -> list[str]:
    """The command line for a command that may touch only one folder (inside ROOT)."""
    if not isinstance(conf, dict):
        raise ValueError("confine must be an object")
    root = _inside_root(conf.get("root") or "")
    if cwd != root and not cwd.startswith(root + os.sep):
        raise ValueError("cwd must be inside the confined folder")
    home = os.path.realpath(conf.get("home") or os.path.join(root, ".hub", "home"))
    if home != root and not home.startswith(root + os.sep):
        raise ValueError("home must be inside the confined folder")
    return [sys.executable, CONFINE, "--root", root, "--home", home,
            *(["--require"] if conf.get("require") or offline else []), *(["--offline"] if offline else []),
            "--", "bash", "-c", cmd]


def landlock_abi() -> int:
    try:
        sys.path.insert(0, os.path.dirname(CONFINE))
        import confine                                   # noqa: PLC0415 - next to this file
        return confine.abi_version()
    except Exception:  # noqa: BLE001
        return 0
    finally:
        if sys.path and sys.path[0] == os.path.dirname(CONFINE):
            sys.path.pop(0)


LANDLOCK = landlock_abi()
RUN_MARK = "SLQ_HUB_RUN"


def offline_works() -> bool:
    """Whether a command can run offline here (confine.py --offline: Landlock ABI 4 and the seccomp filter) - tried
    once, so the Hub asks before every command instead of failing them where it cannot."""
    if LANDLOCK < 4:
        return False
    try:
        return subprocess.run([sys.executable, CONFINE, "--offline", "--require", "--", "true"], timeout=20,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


OFFLINE = offline_works()


def windows_works() -> bool:
    """Whether winshot can run Windows programs here (its --check: files only, no Wine started)."""
    if not os.path.isfile(WINSHOT):
        return False
    try:
        return subprocess.run([sys.executable, "-I", WINSHOT, "--check"], timeout=20, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


WINDOWS = windows_works()


def browser_works() -> bool:
    """Whether pageshot can open web pages here (its --check starts Chromium's headless shell once)."""
    if not (os.path.isfile(PAGESHOT) and os.path.isfile(PAGESHOT_PYTHON)):
        return False
    try:
        return subprocess.run([PAGESHOT_PYTHON, "-I", PAGESHOT, "--check"], timeout=60, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


BROWSER = browser_works()


def _end_leftovers(pgid: int, mark: str) -> int:
    """Kill what a confined command left running - in its process group, or (started with setsid) anywhere,
    found by the mark in its environment. Twice, for anything forked in between. Returns how many."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    needle, me, killed = f"{RUN_MARK}={mark}".encode(), os.getpid(), 0
    for _ in range(2):
        for d in os.listdir("/proc"):
            if not d.isdigit() or int(d) == me:
                continue
            try:
                with open(f"/proc/{d}/environ", "rb") as f:
                    if needle not in f.read().split(b"\0"):
                        continue
                os.kill(int(d), signal.SIGKILL)
                killed += 1
            except (OSError, ValueError):
                continue
    return killed


def run_exec(body: dict) -> dict:
    cmd = body.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError("cmd must be a non-empty string")
    cwd = _inside_root(body.get("cwd") or ROOT)
    timeout = max(1, min(MAX_TIMEOUT, int(body.get("timeout") or DEFAULT_TIMEOUT)))
    cap = max(1000, min(2_000_000, int(body.get("max_output") or DEFAULT_MAX_OUTPUT)))
    offline = body.get("offline") is True
    if body.get("confine"):
        argv = _confined(cmd, cwd, body["confine"], offline)
    elif offline:                                      # the owner's command, without the internet
        argv = [sys.executable, CONFINE, "--offline", "--require", "--", "bash", "-c", cmd]
    else:
        argv = ["bash", "-c", cmd]
    env = _env(body.get("env"))
    mark = ""
    if body.get("confine") or body.get("end_leftovers") is True:
        mark = env[RUN_MARK] = secrets.token_hex(12)
    t0 = time.monotonic()
    p = subprocess.Popen(argv, cwd=cwd, env=env,
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
    if mark:
        _end_leftovers(p.pid, mark)
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
            return self._send(200, {"ok": True, "root": ROOT, "uid": os.getuid(), "landlock": LANDLOCK,
                                    "offline": OFFLINE, "windows": WINDOWS, "browser": BROWSER})
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
    # Not dumpable: commands run as the same user must not read this process's environment (the token)
    # from /proc, nor attach to it.
    try:
        ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)          # PR_SET_DUMPABLE = 4
    except (OSError, AttributeError):
        pass
    print(f"slq-hub sandbox daemon on :{PORT}, root {ROOT}, uid {os.getuid()}, landlock ABI {LANDLOCK}, "
          f"offline commands {'possible' if OFFLINE else 'not possible'}, "
          f"Windows programs {'possible' if WINDOWS else 'not possible'}, "
          f"web pages in a browser {'possible' if BROWSER else 'not possible'}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
