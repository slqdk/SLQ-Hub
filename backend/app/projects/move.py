"""A chat's own folder into a project's (0.31.0: Move to a project).

Code in the sandbox has the same uid and sees the same workspace, and may plant a link or swap a file between the
Hub's looking and its writing; a chat's folder may also get new files from a background job. So:

* the chat's folder is first renamed away, in one step, to a private name (`chats/.moving-<id>-<hex>`) - nothing
  that opens `chats/<id>` afterwards writes into what is being moved;
* the plan and the copy go through folder handles opened one level at a time with O_NOFOLLOW, from the workspace
  down - a link in the way stops it, it is never followed;
* nothing in the project is replaced: a file is written to a fresh temp file and linked to its name, which fails
  when the name is taken meanwhile; a link is made with symlink(), which fails the same way. On any failure what
  the move made is taken away again and the chat's folder goes back;
* the project's sync lock is held from the final plan to the check-in, so SLQ Sync and another move wait;
* the chat's old folder goes to the trash (`data/trash`), not away.

Regular files keep their mode and time, links are made again as links (their text, never followed), empty folders
come along; FIFOs, sockets and devices stay behind. Names that are not UTF-8 are refused: the sync history cannot
hold them."""
from __future__ import annotations

import errno
import hashlib
import os
import secrets
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import syncstore

SKIP = {"skills", "dotnet", "tmp", "home", "web", "windows", "review", "tests"}   # under .hub: made again
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Refused(Exception):
    """The move cannot go ahead: status and a message for the user."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass
class Entry:
    kind: str                    # dir | file | link
    rel: str                     # in the chat's folder
    to: str                      # in the project's folder (an attachment may get another name)
    target: str = ""             # a link's text


@dataclass
class Plan:
    make: list[Entry] = field(default_factory=list)
    same: list[str] = field(default_factory=list)
    clash: list[str] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)
    bad_names: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)          # not a file, folder or link (a FIFO, a socket)

    @property
    def files(self) -> int:
        return sum(1 for e in self.make if e.kind == "file")


# ---------------------------------------------------------------- handles

def _open_dir(base_fd: int, parts: list[str] | tuple[str, ...], made: list | None = None) -> int:
    """A folder opened from base_fd down, a level at a time with O_NOFOLLOW; with made, missing ones are made (and
    listed in made as (parent parts, name)). A link or a file in the way raises OSError (ELOOP, ENOTDIR)."""
    fd = os.dup(base_fd)
    try:
        for i, part in enumerate(parts):
            try:
                nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if made is None:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                made.append((tuple(parts[:i]), part))
                nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_file(base_fd: int, parts: tuple[str, ...]) -> int:
    """A regular file opened from base_fd down without following a link anywhere, not a FIFO that would hang."""
    pfd = _open_dir(base_fd, parts[:-1])
    try:
        f = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
    finally:
        os.close(pfd)
    if not stat.S_ISREG(os.fstat(f).st_mode):
        os.close(f)
        raise OSError(errno.EINVAL, f"{'/'.join(parts)}: not a regular file")
    return f


def _same_content(a_fd: int, b_fd: int) -> bool:
    if os.fstat(a_fd).st_size != os.fstat(b_fd).st_size:
        return False
    os.lseek(a_fd, 0, 0)
    os.lseek(b_fd, 0, 0)
    while True:
        x, y = os.read(a_fd, 1 << 20), os.read(b_fd, 1 << 20)
        if x != y:
            return False
        if not x:
            return True


def _sha(fd: int) -> str:
    h = hashlib.sha256()
    os.lseek(fd, 0, 0)
    while chunk := os.read(fd, 1 << 20):
        h.update(chunk)
    return h.hexdigest()


def _utf8(name: str) -> bool:
    try:
        name.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


# ---------------------------------------------------------------- the steps

def take_away(workspace: Path, chat_id: int) -> Path | None:
    """chats/<id> renamed to a private name, in one step - None when the chat has no folder. A link planted in its
    place is renamed as a link and refused when it is opened."""
    from .store import real_dir
    chats = real_dir(workspace, "chats")               # (a link planted in the way of chats/ is removed, not followed)
    src = chats / str(chat_id)
    if not os.path.lexists(src):
        return None
    private = chats / f".moving-{chat_id}-{secrets.token_hex(4)}"
    os.rename(src, private)
    return private


def put_back(workspace: Path, chat_id: int, private: Path | None) -> bool:
    """The chat's folder back where it was (the move failed). False when something is at its place again - it stays
    at its private name, nothing is lost."""
    if private is None:
        return True
    src = workspace / "chats" / str(chat_id)
    if os.path.lexists(src):
        return False
    os.rename(private, src)
    return True


def to_trash(private: Path | None, trash: Path, chat_id: int) -> Path | None:
    """The chat's old folder into the trash, not away (the move is done)."""
    if private is None:
        return None
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / f"{time.strftime('%Y%m%d-%H%M%S')}-chat-{chat_id}"
    shutil.move(str(private), str(dest))
    return dest


def plan(private: Path, dst: Path | None) -> Plan:
    """What the move does. dst None: a new project's empty folder. Raises OSError when the chat's folder is not a
    folder any more (swapped for a link)."""
    out = Plan()
    src_fd = os.open(private, DIR_FLAGS)
    dst_fd = os.open(dst, DIR_FLAGS) if dst is not None else None
    listings: dict[tuple[str, ...], list[str] | None] = {}
    try:
        for dirpath, dirs, files in os.walk(private):
            here = Path(dirpath)
            rel_dir = here.relative_to(private)
            keep = []
            for d in sorted(dirs):
                rel = (rel_dir / d).as_posix()
                if rel_dir.parts == (".hub",) and d in SKIP:
                    continue
                if not _utf8(d):
                    out.bad_names.append(rel)
                    continue
                if (here / d).is_symlink():
                    _plan_entry(out, src_fd, dst_fd, listings, Entry("link", rel, rel, os.readlink(here / d)))
                    continue
                keep.append(d)
                _plan_entry(out, src_fd, dst_fd, listings, Entry("dir", rel, rel))
            dirs[:] = keep
            for name in sorted(files):
                rel = (rel_dir / name).as_posix()
                if not _utf8(name):
                    out.bad_names.append(rel)
                    continue
                p = here / name
                if p.is_symlink():
                    _plan_entry(out, src_fd, dst_fd, listings, Entry("link", rel, rel, os.readlink(p)))
                elif p.is_file():
                    _plan_entry(out, src_fd, dst_fd, listings, Entry("file", rel, rel))
                else:
                    out.left.append(rel)
    finally:
        os.close(src_fd)
        if dst_fd is not None:
            os.close(dst_fd)
    return out


def _listing(dst_fd: int, parts: tuple[str, ...], cache: dict) -> list[str] | None:
    """The names in a folder of the project (None: not there - it will be made; [] when a link is in the way is
    caught by the caller)."""
    if parts not in cache:
        try:
            fd = _open_dir(dst_fd, parts)
        except FileNotFoundError:
            cache[parts] = None
        else:
            try:
                cache[parts] = os.listdir(fd)
            finally:
                os.close(fd)
    return cache[parts]


def _plan_entry(out: Plan, src_fd: int, dst_fd: int | None, cache: dict, e: Entry) -> None:
    state = _state(src_fd, dst_fd, cache, e, e.to)
    if state == "clash" and e.kind == "file" and e.rel.startswith(".hub/attachments/") and e.rel.count("/") == 2:
        # an attachment the project has by that name: the name a second upload of it would get there
        f = _open_file(src_fd, tuple(e.rel.split("/")))
        try:
            alt = f".hub/attachments/{_sha(f)[:8]}-{e.rel.rsplit('/', 1)[1]}"
        finally:
            os.close(f)
        state = _state(src_fd, dst_fd, cache, e, alt)
        if state != "clash":
            out.renamed[e.rel] = alt
            e = Entry(e.kind, e.rel, alt, e.target)
    if state == "make":
        out.make.append(e)
    elif state == "same":
        out.same.append(e.rel)
    elif state == "clash":
        out.clash.append(e.rel)


def _state(src_fd: int, dst_fd: int | None, cache: dict, e: Entry, to: str) -> str:
    """make (nothing there), same (there already, alike), here (a folder that is there), clash."""
    if dst_fd is None:
        return "make"
    parts = tuple(to.split("/"))
    try:
        names = _listing(dst_fd, parts[:-1], cache)
    except OSError:                                     # a link or a file where a folder belongs
        return "clash"
    if names is None:
        return "make"
    name = parts[-1]
    if name not in names:
        if any(n.casefold() == name.casefold() for n in names):
            return "clash"                              # README.md beside Readme.md - one file on a Windows PC
        return "make"
    pfd = _open_dir(dst_fd, parts[:-1])
    try:
        st = os.stat(name, dir_fd=pfd, follow_symlinks=False)
        if e.kind == "dir":
            return "here" if stat.S_ISDIR(st.st_mode) else "clash"
        if e.kind == "link":
            return "same" if stat.S_ISLNK(st.st_mode) and os.readlink(name, dir_fd=pfd) == e.target else "clash"
        if not stat.S_ISREG(st.st_mode):
            return "clash"
        a = _open_file(src_fd, tuple(e.rel.split("/")))
        try:
            b = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
            try:
                return "same" if stat.S_ISREG(os.fstat(b).st_mode) and _same_content(a, b) else "clash"
            finally:
                os.close(b)
        finally:
            os.close(a)
    finally:
        os.close(pfd)


def apply(private: Path, dst: Path, entries: list[Entry]) -> None:
    """Make the plan's entries in dst. Nothing is replaced (a name taken meanwhile stops it); on any failure what
    was made is taken away again and the error raised."""
    src_fd = os.open(private, DIR_FLAGS)
    dst_fd = os.open(dst, DIR_FLAGS)
    made_dirs: list[tuple[tuple[str, ...], str]] = []
    made: list[tuple[tuple[str, ...], str]] = []
    try:
        for e in entries:
            parts = tuple(e.to.split("/"))
            if e.kind == "dir":
                os.close(_open_dir(dst_fd, parts, made_dirs))
                continue
            pfd = _open_dir(dst_fd, parts[:-1], made_dirs)
            try:
                if e.kind == "link":
                    os.symlink(e.target, parts[-1], dir_fd=pfd)        # FileExistsError: taken meanwhile
                else:
                    _copy_file(src_fd, tuple(e.rel.split("/")), pfd, parts[-1])
                made.append((parts[:-1], parts[-1]))
            finally:
                os.close(pfd)
    except BaseException:
        _undo(dst_fd, made, made_dirs)
        raise
    finally:
        os.close(src_fd)
        os.close(dst_fd)


def _copy_file(src_fd: int, src_parts: tuple[str, ...], pfd: int, name: str) -> None:
    f = _open_file(src_fd, src_parts)
    tmp = f".{name[:80]}.{secrets.token_hex(4)}.hub-tmp"
    try:
        st = os.fstat(f)
        out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=pfd)
        try:
            os.lseek(f, 0, 0)
            while chunk := os.read(f, 1 << 20):
                os.write(out, chunk)
            os.fchmod(out, stat.S_IMODE(st.st_mode) & 0o777)
            os.utime(out, ns=(st.st_atime_ns, st.st_mtime_ns))
        finally:
            os.close(out)
        try:
            os.link(tmp, name, src_dir_fd=pfd, dst_dir_fd=pfd, follow_symlinks=False)   # never over a name
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV):
                raise
            try:                                         # a file system without hard links
                os.stat(name, dir_fd=pfd, follow_symlinks=False)
                raise FileExistsError(errno.EEXIST, f"{name} is there")
            except FileNotFoundError:
                os.rename(tmp, name, src_dir_fd=pfd, dst_dir_fd=pfd)
                return
    finally:
        os.close(f)
        try:
            os.unlink(tmp, dir_fd=pfd)
        except OSError:
            pass


def _undo(dst_fd: int, made: list, made_dirs: list) -> None:
    for parent, name in reversed(made):
        try:
            pfd = _open_dir(dst_fd, parent)
        except OSError:
            continue
        try:
            os.unlink(name, dir_fd=pfd)
        except OSError:
            pass
        finally:
            os.close(pfd)
    for parent, name in reversed(made_dirs):
        try:
            pfd = _open_dir(dst_fd, parent)
        except OSError:
            continue
        try:
            os.rmdir(name, dir_fd=pfd)
        except OSError:
            pass
        finally:
            os.close(pfd)


def merge_locked(private: Path, dst: Path, pid: int, message: str) -> Plan:
    """Under the project's sync lock: plan again against the project as it is now, refuse a clash, make it, check it
    in. -> the plan that was made."""
    with syncstore.lock(pid):
        p = plan(private, dst)
        refuse(p)
        apply(private, dst, p.make)
        try:
            syncstore._snapshot_locked(pid, dst, "user", "web", message)
        except Exception:
            _undo_all(private, dst, p)
            raise
    return p


def _undo_all(private: Path, dst: Path, p: Plan) -> None:
    dst_fd = os.open(dst, DIR_FLAGS)
    try:
        made = [(tuple(e.to.split("/"))[:-1], e.to.rsplit("/", 1)[-1]) for e in p.make if e.kind != "dir"]
        dirs = [(tuple(e.to.split("/"))[:-1], e.to.rsplit("/", 1)[-1]) for e in p.make if e.kind == "dir"]
        _undo(dst_fd, made, dirs)
    finally:
        os.close(dst_fd)


def refuse(p: Plan) -> None:
    """409 for names the sync history cannot hold, and for files the project has by the same names."""
    def shown(names: list[str]) -> str:
        names = [n.encode("utf-8", "surrogateescape").decode("utf-8", "replace") for n in names]   # (sayable)
        return ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
    if p.bad_names:
        raise Refused(409, f"Some names are not UTF-8, which the project's history cannot hold: "
                           f"{shown(p.bad_names)}. Rename them first.")
    if p.clash:
        raise Refused(409, f"The project has other files by the same names: {shown(p.clash)}. Rename or delete "
                           "them in one of the two, then move the chat.")
