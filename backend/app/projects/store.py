"""Projects and the folders the agent works in.

    <data>/workspace/projects/<id>/   a project's files (the sandbox sees
                                      /workspace/projects/<id>/)
    <data>/workspace/chats/<id>/      scratch folder of a chat outside a project

Every write to a project folder through the Hub is checked in as a sync
revision, so it can be restored and the Windows client picks it up.
"""
from __future__ import annotations

import codecs
import errno
import json
import mimetypes
import os
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .. import db
from . import syncstore

PROJECT_DEFAULTS = {"model": None, "cloud_escalation": False, "skills": [], "libraries": [], "thinking": None,
                    "image_model": None, "level": None}    # level (0.38.0): the project's effort; None = Settings'
TEXT_LIMIT = 2_000_000


def _row(r) -> dict:
    d = dict(r)
    try:
        d["settings"] = {**PROJECT_DEFAULTS, **json.loads(d.get("settings") or "{}")}
    except ValueError:
        d["settings"] = dict(PROJECT_DEFAULTS)
    return d


def create_project(name: str, instructions: str = "", settings: dict | None = None) -> dict:
    name = " ".join((name or "").split())[:120] or "Untitled project"
    now = db.utcnow()
    with db.connect() as con:
        cur = con.execute("INSERT INTO projects (name, instructions, settings, created_at, updated_at)"
                          " VALUES (?,?,?,?,?)",
                          (name, instructions or "", json.dumps({**PROJECT_DEFAULTS, **(settings or {})}), now, now))
        return get_project(cur.lastrowid)


def get_project(pid: int) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return _row(r) if r else None


def list_projects(archived: bool = False) -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT p.*, (SELECT COUNT(*) FROM chats c WHERE c.project_id=p.id) chats"
                           " FROM projects p WHERE archived=? ORDER BY updated_at DESC", (1 if archived else 0,)).fetchall()
    return [_row(r) for r in rows]


def update_project(pid: int, name: str | None = None, instructions: str | None = None,
                   settings: dict | None = None, archived: bool | None = None) -> dict | None:
    p = get_project(pid)
    if not p:
        return None
    fields: dict = {"updated_at": db.utcnow()}
    if name is not None:
        fields["name"] = " ".join(name.split())[:120] or p["name"]
    if instructions is not None:
        fields["instructions"] = instructions
    if settings is not None:
        merged = {**p["settings"], **{k: v for k, v in settings.items() if k in PROJECT_DEFAULTS}}
        fields["settings"] = json.dumps(merged)
    if archived is not None:
        fields["archived"] = 1 if archived else 0
    sets = ", ".join(f"{k}=?" for k in fields)
    with db.connect() as con:
        con.execute(f"UPDATE projects SET {sets} WHERE id=?", (*fields.values(), pid))
    return get_project(pid)


def touch_project(pid: int) -> None:
    with db.connect() as con:
        con.execute("UPDATE projects SET updated_at=? WHERE id=?", (db.utcnow(), pid))


def delete_project(pid: int, workspace: Path, trash: Path) -> bool:
    """The database rows go; the folder is moved to <data>/trash, not deleted."""
    with db.connect() as con:
        n = con.execute("DELETE FROM projects WHERE id=?", (pid,)).rowcount
    folder = workspace / "projects" / str(pid)
    if folder.exists():
        trash.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder), str(trash / f"{time.strftime('%Y%m%d-%H%M%S')}-project-{pid}"))
    return n > 0


# ---------------------------------------------------------------- folders

def real_dir(base: Path, *parts: str) -> Path:
    """base/parts... as real folders inside base (0.27.0). Code in the sandbox sees the whole workspace and may plant
    a symlink where a folder belongs - chats/7 -> /data - which the Hub, with more of the disk in view, would follow:
    a link in the way is removed, never followed."""
    base.mkdir(parents=True, exist_ok=True)
    d = base
    for part in parts:
        d = d / part
        if d.is_symlink() or (d.exists() and not d.is_dir()):
            d.unlink()
        d.mkdir(exist_ok=True)
    if not d.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"{d} is not a folder of {base}")
    return d


def project_dir(workspace: Path, pid: int) -> Path:
    return real_dir(workspace, "projects", str(pid))


def chat_dir(workspace: Path, chat_id: int) -> Path:
    return real_dir(workspace, "chats", str(chat_id))


@dataclass
class Workdir:
    """A folder the UI or the agent works in, with every path kept inside it.
    For a project folder, writes become sync revisions."""
    root: Path
    project_id: int | None
    workspace: Path
    sandbox_root: str = "/workspace"

    @property
    def sandbox_path(self) -> str:
        return f"{self.sandbox_root}/{self.root.relative_to(self.workspace).as_posix()}"

    def resolve(self, rel: str | None) -> Path:
        rel = (rel or ".").replace("\\", "/").strip()
        if rel.startswith(self.sandbox_path):           # the agent may use sandbox paths
            rel = rel[len(self.sandbox_path):].lstrip("/") or "."
        if rel.startswith("/"):
            rel = rel.lstrip("/")
        p = (self.root / rel).resolve()
        root = self.root.resolve()
        if self.root.is_symlink() or not root.is_relative_to(self.workspace.resolve()):
            raise ValueError("the working folder was replaced by a link")      # planted while the turn ran
        if p != root and root not in p.parents:
            raise ValueError(f"{rel} is outside the working folder")
        return p

    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root.resolve()).as_posix() or "."

    def zip_files(self, paths: list[str] | None = None) -> list[tuple[str, str, int]]:
        """(its path in the folder, its name in the ZIP, its size) for a ZIP of the folder, or of the files and folders
        given - named from the folder that holds them all, so a game's files from one answer come out as index.html,
        game.js, data/... (0.29.0). A folder brings what it holds, without what sync leaves out (.hub, .git,
        node_modules, ...); a file given by name comes as it is, and one given that is not there any more is left
        out. Regular files only, no links (open_inside reads them the same way). Raises ValueError, and
        FileNotFoundError when none of those given is there."""
        root = self.root.resolve()
        picked = [p for p in (self.resolve(rel) for rel in paths or ["."]) if p.exists()]
        if not picked:
            raise FileNotFoundError(", ".join(paths or ["."]))
        base = Path(os.path.commonpath([str(p if p == root else p.parent) for p in picked]))
        out: dict[str, tuple[str, int]] = {}

        def add(f: Path) -> None:
            try:
                st = f.lstat()
            except OSError:
                return
            if stat.S_ISREG(st.st_mode):
                out.setdefault(f.relative_to(base).as_posix(), (f.relative_to(root).as_posix(), st.st_size))

        for p in picked:
            if not p.is_dir():
                add(p)
                continue
            for dirpath, dirnames, filenames in os.walk(p):
                here = Path(dirpath)
                dirnames[:] = sorted(d for d in dirnames
                                     if d.lower() not in syncstore.IGNORE_DIRS and not (here / d).is_symlink())
                for name in sorted(filenames):
                    if not syncstore.ignored((here / name).relative_to(root).as_posix()):
                        add(here / name)
        return sorted(((rel, name, size) for name, (rel, size) in out.items()), key=lambda x: x[1])

    def open_inside(self, rel: str) -> int:
        """A file of the folder opened for reading without following a link anywhere on its way - not even one that
        sandboxed code (the same uid, the same folder) puts in place of a file or a folder after it was listed: from
        the workspace down, each folder opened from the one before with O_NOFOLLOW, the file too, and only a regular
        file (not a FIFO that would hang, not a device). A file descriptor. Raises OSError (ELOOP: a link), ValueError."""
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or ".." in parts:
            raise ValueError(f"{rel}: not a path inside the folder")
        ws = self.workspace.resolve()
        parts = list(self.root.relative_to(self.workspace).parts) + parts      # the folder itself is not taken on trust
        fd = os.open(ws, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            f = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        finally:
            os.close(fd)
        if not stat.S_ISREG(os.fstat(f).st_mode):
            os.close(f)
            raise OSError(errno.EINVAL, f"{rel}: not a regular file")
        return f

    def tree(self, rel: str = ".", max_entries: int = 2000) -> list[dict]:
        base = self.resolve(rel)
        out: list[dict] = []
        if not base.is_dir():
            return out
        for p in sorted(base.rglob("*")):
            r = self.rel(p)
            if syncstore.ignored(r) or any(part in syncstore.IGNORE_DIRS for part in Path(r).parts):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({"path": r, "dir": p.is_dir(), "size": st.st_size if p.is_file() else 0,
                        "modified": int(st.st_mtime)})
            if len(out) >= max_entries:
                break
        return out

    def read_bytes(self, rel: str) -> bytes:
        p = self.resolve(rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        return p.read_bytes()

    def write(self, rel: str, data: bytes | str, author: str = "hub", origin: str = "hub",
              message: str = "") -> Path:
        p = self.resolve(rel)
        safe_write(self.root, p, data.encode("utf-8") if isinstance(data, str) else data)
        self.checkin(author, origin, message or f"wrote {self.rel(p)}")
        return p

    def delete(self, rel: str, author: str = "hub", origin: str = "hub") -> None:
        p = self.resolve(rel)
        if p == self.root.resolve():
            raise ValueError("refusing to delete the whole folder")
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        else:
            raise FileNotFoundError(rel)
        self.checkin(author, origin, f"deleted {rel}")

    def move(self, src: str, dst: str, author: str = "hub", origin: str = "hub") -> Path:
        a, b = self.resolve(src), self.resolve(dst)
        if not a.exists():
            raise FileNotFoundError(src)
        b.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(a), str(b))
        self.checkin(author, origin, f"moved {src} -> {dst}")
        return b

    def mkdir(self, rel: str) -> Path:
        p = self.resolve(rel)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def checkin(self, author: str = "hub", origin: str = "hub", message: str = "") -> int | None:
        if self.project_id is None:
            return None
        rev = syncstore.snapshot(self.project_id, self.root, author=author, origin=origin, message=message)
        touch_project(self.project_id)
        return rev


def safe_write(root: Path, p: Path, data: bytes) -> None:
    """Write `p` inside `root` without following a symlink that a sandboxed
    process (the same uid, the same folder) may have planted: a fresh temp
    file (O_EXCL) in the checked folder, then a rename over the target - a
    rename replaces a symlink rather than writing through it."""
    root = root.resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    parent = p.parent.resolve()
    if parent != root and root not in parent.parents:
        raise ValueError(f"{p.name}: the folder is outside the working folder")
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=f".{p.name[:80]}.", suffix=".hub-tmp")
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, parent / p.name)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mime_of(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


# ---------------------------------------------------------------- what a turn made

SNAPSHOT_LIMIT = 20_000                  # files; a bigger folder is not compared
PRODUCED_LIMIT = 50                      # files listed for one answer


def snapshot(root: Path, limit: int = SNAPSHOT_LIMIT) -> dict[str, tuple[int, int]] | None:
    """path -> (size, mtime_ns) of the folder's files, without what sync ignores (.hub, .git, node_modules, ...).
    None when it has more than `limit` files."""
    out: dict[str, tuple[int, int]] = {}
    if not root.is_dir():
        return out
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in syncstore.IGNORE_DIRS]
        for name in files:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if syncstore.ignored(rel):
                continue
            try:
                st = p.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):              # not a link, a pipe or a socket
                continue
            out[rel] = (st.st_size, st.st_mtime_ns)
            if len(out) > limit:
                return None
    return out


def produced(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]],
             limit: int = PRODUCED_LIMIT) -> list[dict]:
    """The files that are new or changed from one snapshot to the next, the last written first."""
    changed = [(rel, size, mtime, rel not in before) for rel, (size, mtime) in after.items()
               if before.get(rel) != (size, mtime)]
    changed.sort(key=lambda c: (-c[2], c[0]))
    return [{"path": rel, "size": size, "mime": mime_of(rel), "new": new} for rel, size, _, new in changed[:limit]]


def is_text(data: bytes) -> bool:
    if b"\x00" in data[:8000]:
        return False
    try:
        # Incremental, so a character cut in two at byte 8000 is not taken for binary.
        codecs.getincrementaldecoder("utf-8")().decode(data[:8000], final=False)
        return True
    except UnicodeDecodeError:
        return False
