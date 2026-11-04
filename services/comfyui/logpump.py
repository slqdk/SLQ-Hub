#!/usr/bin/env python3
"""ComfyUI's output to the container's own (docker logs) and to a file the Hub shows (0.77.0, Søren: "There are no
ComfyUI log ?").

    python logpump.py /logs/comfyui.log -- python main.py --listen ...

The file is what `docker logs slq-hub-comfyui` shows - Python's logging and also what PyTorch's C++ writes straight
to stderr: the "HIPCachingAllocator.cpp ... memory allocation failed with OOM" lines that explained the decode of
2026-11-04 are not logging records, so ComfyUI's own log file (--verbose LEVEL FILE) would not have them. Each line
starts with the time it began; a start and an exit line mark ComfyUI's runs, so the run before a crash or an update
stays readable. Past 10 MB the file becomes comfyui.log.1 and a new one begins. On the VM it is
/srv/slq-hub/data/logs/comfyui, which the Hub reads (Settings -> System -> Logs -> ComfyUI).

ComfyUI runs as this process's child and this process is the container's first: when ComfyUI crashes, what it wrote
last is still in the pipe and is read to the end before this one leaves. (A reader started beside ComfyUI as PID 1
would be killed with it, perhaps before it read the last lines.) The signals docker sends are passed on to ComfyUI,
and the exit code is ComfyUI's. A file that cannot be written - no folder mounted, a full disk - costs only the
file: docker logs gets everything. Standard library only; tests: backend/tests/test_comfy_log.py.
"""
from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time

MAX = 10 * 1024 * 1024      # past this the file becomes .1
QUIET = 2.0                 # after ComfyUI exits: read on while output comes, until this long without any
DRAIN_MAX = 10.0            # ... and no longer than this (something it started may hold the pipe and keep writing)
RETRY = 60.0                # a file that could not be written is tried again after this long
FORWARD = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT, signal.SIGUSR1, signal.SIGUSR2)


def stamp() -> bytes:
    return time.strftime("%Y-%m-%d %H:%M:%S ").encode()


class Log:
    """The capped file, each line stamped with the time it began."""

    def __init__(self, path: str, cap: int = MAX):
        self.path, self.cap = path, cap
        self.f = None
        self.failed_at: float | None = None
        self.told = False
        self.at_line_start = True

    def _open(self) -> bool:
        if self.f is not None:
            return True
        if self.failed_at is not None and time.monotonic() - self.failed_at < RETRY:
            return False
        try:
            self.f = open(self.path, "ab")
            self.failed_at = None
            return True
        except OSError as exc:
            self._failed(exc)
            return False

    def _failed(self, exc: OSError) -> None:
        if self.f is not None:
            try:
                self.f.close()
            except OSError:
                pass
        self.f, self.failed_at = None, time.monotonic()
        if not self.told:                         # once: the Hub's System tab will miss what follows
            self.told = True
            out(f"[logpump] cannot write {self.path} ({exc}) - ComfyUI's log in the Hub misses what follows; "
                f"docker logs has it all\n".encode())

    def write(self, data: bytes) -> None:
        """Data as it came (whole lines or not), a time at the start of each line."""
        stamped = bytearray()
        parts = data.split(b"\n")
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            if last and not part:                 # it ended with a newline
                break
            if self.at_line_start:
                stamped += stamp()
            stamped += part
            if last:
                self.at_line_start = False
            else:
                stamped += b"\n"
                self.at_line_start = True
        self._put(bytes(stamped))

    def mark(self, text: str) -> None:
        """A line of this script's own (a run's start and end), on a line of its own."""
        lead = b"" if self.at_line_start else b"\n"
        self.at_line_start = True
        self._put(lead + stamp() + text.encode("utf-8", "replace") + b"\n")

    def _put(self, data: bytes) -> None:
        if not data or not self._open():
            return
        try:
            self.f.write(data)
            self.f.flush()
            if self.f.tell() > self.cap:
                self.f.close()
                self.f = None
                os.replace(self.path, self.path + ".1")
        except OSError as exc:
            self._failed(exc)


_out_broken = False


def out(data: bytes) -> None:
    """To the container's output (docker logs), whole; given up if that end went away - the file still gets it."""
    global _out_broken
    view = memoryview(data)
    while view and not _out_broken:
        try:
            view = view[os.write(1, view):]
        except BlockingIOError:                   # (a non-blocking output: wait for room, as a blocking one would)
            select.select([], [1], [], 1.0)
        except OSError:
            _out_broken = True


def describe(code: int) -> str:
    if code < 0:
        try:
            return f"stopped by {signal.Signals(-code).name}"
        except ValueError:
            return f"stopped by signal {-code}"
    return f"exited with code {code}"


def run(path: str, cmd: list[str], cap: int = MAX) -> int:
    log = Log(path, cap)
    log.mark("===== start: " + " ".join(cmd))
    try:
        child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as exc:
        msg = f"[logpump] could not start {cmd[0]}: {exc}"
        out((msg + "\n").encode())
        log.mark(msg)
        return 127
    for sig in FORWARD:
        signal.signal(sig, lambda n, _frame: child.send_signal(n))
    fd = child.stdout.fileno()
    deadline = None
    while True:
        if deadline is None and child.poll() is not None:
            deadline = time.monotonic() + DRAIN_MAX         # ComfyUI is gone: what is left in the pipe, then out
        wait = 1.0 if deadline is None else max(0.0, min(QUIET, deadline - time.monotonic()))
        if not select.select([fd], [], [], wait)[0]:
            if deadline is not None:
                break
            continue
        data = os.read(fd, 65536)
        if not data:                                        # every writer closed the pipe
            break
        log.write(data)                                     # the file first: the Hub's view, should docker lag
        out(data)
        if deadline is not None and time.monotonic() >= deadline:
            break
    code = child.wait()
    log.mark("===== " + describe(code))
    return code if code >= 0 else 128 - code


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[2] != "--":
        print("usage: logpump.py LOGFILE -- COMMAND [ARGS...]", file=sys.stderr)
        return 2
    return run(argv[1], argv[3:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
