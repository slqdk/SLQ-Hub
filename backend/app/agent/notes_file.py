"""NOTES.md in the working folder (0.31.0): the work's notes as a file - under Files, editable, copied to the chat
that continues the work and shared by a project's chats.

The Hub writes the chat's notes into it when it condenses a chat, when it writes the notes for Continue in a new
chat and when the user corrects the notes in the chat's menu - unless someone else keeps the file: the Hub's own
copy starts with a mark that holds a hash of the rest and the chat it is from, and once the user or the assistant
changes the file it is theirs and the Hub leaves it alone. In a project the Hub's copy is one chat's: another chat
of the project does not write over it (the one that continues it does). In long work the Hub reminds the model to
write it (loop.py), and the system prompt says to read it first (prompt.py). Chat 126: 54 steps and 31 minutes on a
file format, and no notes file, although the prompt asked for one (0.29.0)."""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from ..projects import store as projects

NAME = "NOTES.md"
MARK = re.compile(r"\A<!-- SLQ Hub notes ([0-9a-f]{12}) chat (\d+)\b[^\n]*-->\n")
MAX_READ = 1_000_000
REMIND_AFTER = 15          # model steps into a turn that made something, before the first reminder
REMIND_EVERY = 25          # and again after this many more, while the file is not written
REMINDER = ("(SLQ Hub: this work has gone on for {steps} steps, and {name} in the working folder has not been "
            "written in this turn. Write what you have found into {name} now - what you checked and how, apart from "
            "what you still only guess, and what comes next - then go on. A new chat starts from that file.)")


def wanted(settings) -> bool:
    return bool(settings.get("agent", "notes_file"))


def find(root: Path) -> Path | None:
    """The notes file at the top of the folder for the notes, in any case (notes.md) - a regular file, not a link.
    That folder is the working folder's top, or hub/ in a project that syncs one folder (Workdir.own_root, 0.40.0) -
    and not a link either."""
    if Path(root).is_symlink():
        return None
    try:
        for p in sorted(Path(root).iterdir()):
            if p.name.lower() == NAME.lower() and not p.is_symlink() and p.is_file():
                return p
    except OSError:
        pass
    return None


def stamp(root: Path) -> tuple | None:
    """What the notes file is now (name, size, time), to tell whether it was written since."""
    p = find(root)
    if p is None:
        return None
    try:
        st = p.lstat()
    except OSError:
        return None
    return (p.name, st.st_size, st.st_mtime_ns)


def text_for(title: str, notes: str, why: str, chat_id: int) -> str:
    body = f"# Notes: {title}\n\n{notes.strip()}\n"
    mark = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return (f"<!-- SLQ Hub notes {mark} chat {chat_id} - written from the chat's notes ({why}). Once you or the "
            f"assistant change this file, the Hub leaves it to you. -->\n{body}")


def _read(p: Path) -> str | None:
    """The file's text, without following a link or hanging on a FIFO put in its place; None when it is not a
    regular UTF-8 file of reasonable size."""
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_READ:
            return None
        data = b""
        while chunk := os.read(fd, 1 << 16):
            data += chunk
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(fd)


def owner(p: Path) -> int | None:
    """The chat whose notes the Hub wrote into the file - while it is unchanged since (its mark matches the rest);
    None: someone else's."""
    text = _read(p)
    m = MARK.match(text or "")
    if not m or hashlib.sha256(text[m.end():].encode("utf-8")).hexdigest()[:12] != m.group(1):
        return None
    return int(m.group(2))


def write(wd, chat_id: int, title: str, notes: str, why: str, owners: tuple = ()) -> str | None:
    """The chat's notes into NOTES.md, unless the file is someone else's: no mark, changed since the Hub wrote it, or
    the Hub's copy of another chat than this one or the one it continues (owners). Notes cleared: this chat's copy
    goes. -> the file's name, or None when nothing was written. A link named NOTES.md is replaced, never written
    through (safe_write); a working folder that is a link is left alone; in a project it is checked in."""
    root = Path(wd.root)
    folder = Path(getattr(wd, "own_root", root))          # hub/ in a project that syncs one folder (0.40.0)
    if root.is_symlink() or not root.is_dir() or folder.is_symlink():
        return None
    p = find(folder)
    if p is not None and owner(p) not in {chat_id, *owners}:
        return None
    if not (notes or "").strip():
        if p is not None:
            p.unlink()
            wd.checkin("hub", "hub", "notes cleared")
        return None
    target = p if p is not None else folder / NAME
    projects.safe_write(root, target, text_for(title, notes, why, chat_id).encode("utf-8"))
    wd.checkin("hub", "hub", f"notes ({why})")
    return target.relative_to(root).as_posix()
