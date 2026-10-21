#!/usr/bin/env python3
"""llama-server supervisor for SLQ Hub (standard library only).

The Hub's GPU scheduler decides WHAT runs; this only does it: start a named
llama-server with the arguments the Hub sends, stop it, report state, logs
and VRAM. One process per name ("main", "embed-text", ...), each on its own
port. Internal network only; every call except /health needs the shared
token in X-Internal-Token.

    GET  /health                      -> {"ok": true}
    GET  /status                      -> {"processes": {...}, "gpu": {...}}
    POST /start {name, port, args}    -> starts (or keeps an identical one)
    POST /stop  {name} | {all: true}  -> SIGTERM, then SIGKILL after a grace
    GET  /logs?name=main&tail=200     -> text
    GET  /gpu                         -> VRAM numbers from amdgpu sysfs
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BIN = os.environ.get("LLAMA_SERVER_BIN", "/app/llama-server")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
LOG_DIR = os.environ.get("SUPERVISOR_LOG_DIR", "/logs")
LISTEN_PORT = int(os.environ.get("SUPERVISOR_PORT", "9000"))
PORT_RANGE = range(9100, 9200)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
FORBIDDEN_ARGS = {"--host", "--port", "--api-key", "--api-key-file"}
LOG_FILE_MAX = 20 * 1024 * 1024
STOP_GRACE = float(os.environ.get("SUPERVISOR_STOP_GRACE", "15"))


class Proc:
    def __init__(self, name: str, port: int, args: list[str]):
        self.name = name
        self.port = port
        self.args = list(args)
        self.started = time.time()
        self.popen: subprocess.Popen | None = None
        self.lines: collections.deque[str] = collections.deque(maxlen=3000)
        self.returncode: int | None = None
        self.stopping = False

    @property
    def alive(self) -> bool:
        return self.popen is not None and self.popen.poll() is None

    def info(self) -> dict:
        rc = self.popen.poll() if self.popen else self.returncode
        return {
            "name": self.name, "port": self.port, "args": self.args,
            "pid": self.popen.pid if self.popen else None,
            "state": "running" if rc is None and self.popen else ("stopped" if self.stopping else "exited"),
            "returncode": rc, "started": self.started,
            "uptime": round(time.time() - self.started, 1),
        }


_procs: dict[str, Proc] = {}
_lock = threading.RLock()


def _child_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_ARG_")}
    env.pop("SUPERVISOR_TOKEN", None)
    return env


def _pump(proc: Proc) -> None:
    """Copy the child's output into memory (for /logs) and a capped file."""
    path = os.path.join(LOG_DIR, f"{proc.name}.log")
    try:
        f = open(path, "a", encoding="utf-8", errors="replace")
    except OSError:
        f = None
    if f:
        f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start {proc.name} port {proc.port}: "
                f"{' '.join(proc.args)}\n")
        f.flush()
    assert proc.popen and proc.popen.stdout
    for raw in proc.popen.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\n")
        proc.lines.append(line)
        if f:
            try:
                f.write(line + "\n")
                f.flush()
                if f.tell() > LOG_FILE_MAX:
                    f.close()
                    os.replace(path, path + ".1")
                    f = open(path, "a", encoding="utf-8", errors="replace")
            except OSError:
                pass
    rc = proc.popen.wait()
    proc.returncode = rc
    proc.lines.append(f"[supervisor] {proc.name} exited with code {rc}")
    if f:
        f.write(f"[supervisor] exited with code {rc}\n")
        f.close()


def _kill(proc: Proc) -> None:
    if not proc.alive:
        return
    proc.stopping = True
    assert proc.popen
    try:
        os.killpg(proc.popen.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + STOP_GRACE
    while time.time() < deadline and proc.popen.poll() is None:
        time.sleep(0.1)
    if proc.popen.poll() is None:
        try:
            os.killpg(proc.popen.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.popen.wait(timeout=10)


def start(name: str, port: int, args: list[str]) -> dict:
    if not NAME_RE.match(name or ""):
        raise ValueError("name must be lowercase letters, digits, - or _ (max 40)")
    if port not in PORT_RANGE:
        raise ValueError(f"port must be in {PORT_RANGE.start}-{PORT_RANGE.stop - 1}")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("args must be a list of strings")
    bad = [a for a in args if a.split("=", 1)[0] in FORBIDDEN_ARGS]
    if bad:
        raise ValueError(f"not allowed (the supervisor sets them): {', '.join(bad)}")
    with _lock:
        cur = _procs.get(name)
        if cur and cur.alive and cur.port == port and cur.args == args:
            return {**cur.info(), "reused": True}
        if cur:
            _kill(cur)
        for other in list(_procs.values()):        # one process per port
            if other.name != name and other.port == port and other.alive:
                _kill(other)
        if not os.path.exists(BIN):
            raise RuntimeError(f"{BIN} does not exist in this image")
        proc = Proc(name, port, args)
        cmd = [BIN, "--host", "0.0.0.0", "--port", str(port), *args]
        proc.popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, env=_child_env(),
                                      start_new_session=True)
        threading.Thread(target=_pump, args=(proc,), daemon=True).start()
        _procs[name] = proc
        return {**proc.info(), "reused": False}


def stop(name: str | None = None, everything: bool = False) -> dict:
    with _lock:
        targets = list(_procs.values()) if everything else [p for p in _procs.values() if p.name == name]
        for p in targets:
            _kill(p)
        return {"stopped": [p.name for p in targets]}


def gpu() -> dict:
    best: dict = {}
    for dev in glob.glob("/sys/class/drm/card*/device"):
        try:
            total = int(open(os.path.join(dev, "mem_info_vram_total")).read())
            used = int(open(os.path.join(dev, "mem_info_vram_used")).read())
        except (OSError, ValueError):
            continue
        if total <= best.get("vram_total", 0):
            continue
        info = {"device": dev, "vram_total": total, "vram_used": used}
        for key, fname in (("busy_percent", "gpu_busy_percent"), ("gtt_used", "mem_info_gtt_used")):
            try:
                info[key] = int(open(os.path.join(dev, fname)).read())
            except (OSError, ValueError):
                pass
        best = info
    return best


def status() -> dict:
    with _lock:
        return {"processes": {n: p.info() for n, p in _procs.items()}, "gpu": gpu(),
                "kfd": os.path.exists("/dev/kfd"), "bin": BIN}


class Handler(BaseHTTPRequestHandler):
    server_version = "slq-hub-llm/1"

    def log_message(self, fmt, *args):  # quiet: the Hub logs what matters
        pass

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self) -> bool:
        if not TOKEN:
            return True
        if self.headers.get("X-Internal-Token") == TOKEN:
            return True
        self._send(401, {"error": "bad or missing X-Internal-Token"})
        return False

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(200, {"ok": True})
        if not self._authorised():
            return
        if url.path == "/status":
            return self._send(200, status())
        if url.path == "/gpu":
            return self._send(200, gpu())
        if url.path == "/logs":
            q = parse_qs(url.query)
            name = (q.get("name") or [""])[0]
            tail = max(1, min(3000, int((q.get("tail") or ["200"])[0])))
            with _lock:
                p = _procs.get(name)
                lines = list(p.lines)[-tail:] if p else []
            return self._send(200, "\n".join(lines), "text/plain; charset=utf-8")
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._authorised():
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "body must be JSON"})
        path = urlparse(self.path).path
        try:
            if path == "/start":
                return self._send(200, start(body.get("name", ""), int(body.get("port", 0)),
                                             body.get("args", [])))
            if path == "/stop":
                return self._send(200, stop(body.get("name"), bool(body.get("all"))))
        except (ValueError, TypeError) as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - report, never die
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        self._send(404, {"error": "not found"})


def main() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    httpd.daemon_threads = True

    def shutdown(signum, frame):  # stop the children with us
        stop(everything=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"slq-hub llm supervisor on :{LISTEN_PORT}, binary {BIN}, "
          f"/dev/kfd {'present' if os.path.exists('/dev/kfd') else 'MISSING'}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
