"""ComfyUI's log as Settings -> System shows it (0.77.0, Søren: "There are no ComfyUI log ?").

Its container writes everything ComfyUI prints - what `docker logs slq-hub-comfyui` shows - to
data/logs/comfyui/comfyui.log, and past 10 MB moves it to comfyui.log.1 (services/comfyui/logpump.py). Each line
starts with the time it began, and a line "===== start: ..." or "===== exited with code ..." marks ComfyUI's runs.
Read here as a person reads a terminal: the older file before the newer, ComfyUI's colour codes left out, and a
progress bar that redrew itself (carriage returns) as it ended. A file, not ComfyUI's API: it can be read while
ComfyUI does not answer, and it keeps the run before a crash or an update.

Only ComfyUI's container writes that folder, so what is there is not trusted: a link is not followed (one to the
Hub's /proc/self/environ would hand out its secrets in a download - found in the review), and no more than 16 MB of a
file is read (the pump keeps each under 10 MB and a chunk).
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ")
MAX_READ = 16 * 1024 * 1024
NONE_YET = ("(No ComfyUI log yet. ComfyUI's container writes it from 0.77.0 on: update.sh rebuilds its image and "
            "starts it again - on the VM the log is in /srv/slq-hub/data/logs/comfyui.)")


def parts(path: Path) -> list[Path]:
    """The files, the older first."""
    return [path.with_name(path.name + ".1"), path]


def clean(line: str) -> str:
    line = ANSI.sub("", line)
    if "\r" in line:
        m = STAMP.match(line)
        head = m.group(0) if m else ""
        drawn = [s for s in line[len(head):].split("\r") if s.strip()]
        line = head + (drawn[-1] if drawn else "")
    return line.rstrip()


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _open(path: Path):
    """The file, or None: not there, or not a plain file (a link is not followed, a pipe not waited on)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    return os.fdopen(fd, "rb")


def _last_lines(path: Path, n: int) -> list[str]:
    """The last n lines of a file, read from its end (a line still being written counts)."""
    f = _open(path)
    if f is None or n <= 0:
        return []
    with f:
        size = f.seek(0, 2)
        step = 64 * 1024
        while True:
            start = max(0, size - min(step, MAX_READ))
            f.seek(start)
            lines = f.read(size - start).split(b"\n")
            if start > 0:
                lines = lines[1:]                         # cut at the front
            if lines and not lines[-1]:
                lines.pop()                               # the file ends with a newline
            if len(lines) >= n or start == 0 or size - start >= MAX_READ:
                return [x.decode("utf-8", "replace") for x in lines[-n:]]
            step *= 4


def tail(path: Path, n: int) -> str | None:
    """The last n lines, None when there is no log."""
    if not any(_regular(p) for p in parts(path)):
        return None
    lines: list[str] = []
    for p in reversed(parts(path)):
        if len(lines) >= n:
            break
        lines = _last_lines(p, n - len(lines)) + lines
    return "\n".join(clean(x) for x in lines)


def whole(path: Path) -> str | None:
    """All of it (both files: 20 MB at most), None when there is no log."""
    chunks = []
    for p in parts(path):
        f = _open(p)
        if f is None:
            continue
        with f:
            size = f.seek(0, 2)
            start = max(0, size - MAX_READ)
            f.seek(start)
            data = f.read(size - start)
        if start > 0:
            data = data[data.find(b"\n") + 1:]           # from a whole line
        chunks.append(data.decode("utf-8", "replace"))
    if not chunks:
        return None
    text = "".join(chunks)
    return "\n".join(clean(x) for x in text.split("\n")).rstrip("\n") + "\n"
