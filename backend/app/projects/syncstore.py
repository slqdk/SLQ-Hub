"""Project sync: the Hub's own check-in history, shared with SLQSync.exe.

Ported from SLQ Companion's syncstore.py (2.64.0) with the same protocol, so
the existing Windows tray app works against the Hub. The TwinCAT-specific
ignore patterns are left out; the client keeps its own list.

* Linear history per project. Revision 1, 2, 3 ... Each revision records
  only the files that changed (path -> sha256, or None for a delete). The
  state at revision R is, per path, the newest change at or before R.
  Contents live in the blob store, so a file is stored once.
* The server folder is the head. snapshot() compares it with the head and
  checks in the difference - after the Hub writes, and before every client
  request, so nothing written on the server side is missed.
* Conflicts are per file, decided by the client's base. A client sends each
  changed file with the sha it was based on. If the server's current sha is
  that base (or already the new content) the change is accepted; if the
  server changed the file differently meanwhile, it is a conflict and
  NOTHING is overwritten - the client keeps its copy and gets the server's
  beside it.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import threading
from pathlib import Path, PurePosixPath

from .. import db
from . import blobs

MAX_FILE_BYTES = 25 * 1024 * 1024

IGNORE_DIRS = {".git", ".svn", ".hg", ".slqsync", "__pycache__", "node_modules", ".venv", "venv",
               ".pytest_cache", ".mypy_cache", ".idea", ".vs", ".hub"}
IGNORE_GLOBS = ["*.tmp", "*.bak", "*.orig", "*.swp", "*.slqconflict*", "*.slqsync-tmp", "thumbs.db",
                "desktop.ini", ".ds_store", "~$*", "*.pyc", "*.hub-tmp"]


class SyncError(ValueError):
    """Something the client can fix - shown as it is."""


def ignored(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    if any(p.lower() in IGNORE_DIRS for p in parts[:-1]):
        return True
    name = parts[-1].lower() if parts else ""
    return any(fnmatch.fnmatch(name, g) for g in IGNORE_GLOBS)


def clean_path(rel: str) -> str:
    rel = (rel or "").replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    p = PurePosixPath(rel)
    if not rel or p.is_absolute() or ":" in p.parts[0] or any(x in ("..", "") for x in p.parts):
        raise SyncError(f"Not a valid project path: {rel!r}")
    return str(p)


_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_sha_cache: dict[str, tuple[int, int, str]] = {}


def lock(pid: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(pid, threading.Lock())


def file_sha(p: Path) -> str | None:
    try:
        st = p.stat()
    except OSError:
        return None
    key = str(p)
    hit = _sha_cache.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None
    _sha_cache[key] = (st.st_mtime_ns, st.st_size, sha)
    return sha


def scan(workdir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not workdir.is_dir():
        return out
    for root, dirs, files in os.walk(workdir):
        dirs[:] = [d for d in dirs if d.lower() not in IGNORE_DIRS]
        for f in files:
            p = Path(root) / f
            rel = p.relative_to(workdir).as_posix()
            if ignored(rel):
                continue
            try:
                if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            sha = file_sha(p)
            if sha:
                out[rel] = sha
    return out


# ---------------------------------------------------------------- reading

def head(pid: int) -> int:
    with db.connect() as con:
        r = con.execute("SELECT MAX(rev) FROM sync_revs WHERE project_id=?", (pid,)).fetchone()
    return r[0] or 0


def manifest(pid: int, rev: int | None = None) -> dict[str, str]:
    if rev is None:
        rev = head(pid)
    with db.connect() as con:
        rows = con.execute(
            "SELECT c.path, c.sha FROM sync_changes c"
            " JOIN (SELECT path, MAX(rev) r FROM sync_changes WHERE project_id=? AND rev<=?"
            "       GROUP BY path) last ON last.path = c.path AND last.r = c.rev"
            " WHERE c.project_id=?", (pid, rev, pid)).fetchall()
    return {r["path"]: r["sha"] for r in rows if r["sha"]}


def changes_since(pid: int, since: int) -> dict:
    h = head(pid)
    if since > h:
        raise SyncError(f"Revision {since} does not exist yet (head is {h}).")
    with db.connect() as con:
        paths = [r[0] for r in con.execute(
            "SELECT DISTINCT path FROM sync_changes WHERE project_id=? AND rev>?", (pid, since))]
    now = manifest(pid, h)
    return {"head": h, "since": since, "changes": {p: now.get(p) for p in paths}}


def in_view(changes: dict, prefix: str) -> dict:
    """path -> value of what lies under prefix (a synced folder and "/", 0.40.0), named as the PC names it - without
    the prefix. prefix "": all of it, as it is."""
    if not prefix:
        return changes
    return {k[len(prefix):]: v for k, v in changes.items() if k.startswith(prefix) and len(k) > len(prefix)}


def revisions(pid: int, limit: int = 50, before: int | None = None, prefix: str = "") -> list[dict]:
    """The newest revisions first. prefix (a synced folder and "/", 0.40.0): only those that changed something
    under it, their files named without it - what the PC sees."""
    cut = len(prefix)
    q = ("SELECT r.rev, r.author, r.origin, r.message, r.created_at,"
         " COUNT(c.path) n FROM sync_revs r " + ("JOIN" if prefix else "LEFT JOIN") + " sync_changes c"
         " ON c.project_id=r.project_id AND c.rev=r.rev"
         + (" AND substr(c.path, 1, ?) = ? AND length(c.path) > ?" if prefix else "") + " WHERE r.project_id=?")
    params: list = [cut, prefix, cut, pid] if prefix else [pid]
    if before:
        q += " AND r.rev < ?"
        params.append(before)
    q += " GROUP BY r.rev ORDER BY r.rev DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with db.connect() as con:
        rows = con.execute(q, params).fetchall()
        out = []
        for r in rows:
            files = [{"path": c["path"][cut:], "deleted": c["sha"] is None} for c in con.execute(
                "SELECT path, sha FROM sync_changes WHERE project_id=? AND rev=?"
                + (" AND substr(path, 1, ?) = ? AND length(path) > ?" if prefix else "") + " ORDER BY path LIMIT 30",
                (pid, r["rev"], cut, prefix, cut) if prefix else (pid, r["rev"]))]
            out.append({"rev": r["rev"], "author": r["author"], "origin": r["origin"], "message": r["message"],
                        "createdAt": r["created_at"], "fileCount": r["n"], "files": files})
    return out


def file_history(pid: int, path: str, limit: int = 100) -> list[dict]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT c.rev, c.sha, c.size, r.author, r.origin, r.message, r.created_at FROM sync_changes c"
            " JOIN sync_revs r ON r.project_id=c.project_id AND r.rev=c.rev"
            " WHERE c.project_id=? AND c.path=? ORDER BY c.rev DESC LIMIT ?", (pid, path, limit)).fetchall()
    return [{"rev": r["rev"], "sha": r["sha"], "size": r["size"], "author": r["author"], "origin": r["origin"],
             "message": r["message"], "createdAt": r["created_at"], "deleted": r["sha"] is None} for r in rows]


def blob(pid: int, sha: str) -> bytes:
    if not sha or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise SyncError("Not a blob id.")
    with db.connect() as con:
        known = con.execute("SELECT 1 FROM sync_changes WHERE project_id=? AND sha=? LIMIT 1",
                            (pid, sha)).fetchone()
    if not known or not blobs.exists(sha):
        raise SyncError("No such blob in this project.")
    return blobs.read(sha)


# ---------------------------------------------------------------- writing

def _record(con, pid: int, changes: dict[str, str | None], author: str, origin: str, message: str) -> int:
    rev = (con.execute("SELECT MAX(rev) FROM sync_revs WHERE project_id=?", (pid,)).fetchone()[0] or 0) + 1
    con.execute("INSERT INTO sync_revs (project_id, rev, author, origin, message, created_at) VALUES (?,?,?,?,?,?)",
                (pid, rev, author[:80], origin[:80], (message or "")[:500], db.utcnow()))
    for path, sha in sorted(changes.items()):
        con.execute("INSERT INTO sync_changes (project_id, rev, path, sha, size) VALUES (?,?,?,?,?)",
                    (pid, rev, path, sha, blobs.size(sha) if sha else 0))
    return rev


def _snapshot_locked(pid: int, workdir: Path, author: str, origin: str, message: str) -> int | None:
    now = scan(workdir)
    before = manifest(pid)
    changes: dict[str, str | None] = {}
    for path, sha in now.items():
        if before.get(path) != sha:
            changes[path] = blobs.store((workdir / path).read_bytes())
    for path in before:
        if path not in now:
            changes[path] = None
    if not changes:
        return None
    with db.connect() as con:
        return _record(con, pid, changes, author, origin, message)


def snapshot(pid: int, workdir: Path, author: str = "hub", origin: str = "hub", message: str = "") -> int | None:
    """Check in whatever differs between the folder and the head. Returns the
    new revision number, or None when nothing changed."""
    with lock(pid):
        return _snapshot_locked(pid, Path(workdir), author, origin, message)


def checkin(pid: int, workdir: Path, files: list[dict], author: str, origin: str, message: str = "") -> dict:
    """A client's changes: [{path, baseSha, sha (None = deleted), content (bytes)}]."""
    workdir = Path(workdir)
    accepted: dict[str, str | None] = {}
    conflicts: list[dict] = []
    with lock(pid):
        _snapshot_locked(pid, workdir, "hub", "hub", "Server changes before a check-in")
        current = manifest(pid)
        for f in files:
            path = clean_path(f.get("path") or "")
            if ignored(path):
                continue
            new_sha = f.get("sha")
            content = f.get("content")
            if new_sha is not None:
                if content is None:
                    raise SyncError(f"{path}: no content sent.")
                if len(content) > MAX_FILE_BYTES:
                    raise SyncError(f"{path}: larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
                if hashlib.sha256(content).hexdigest() != new_sha:
                    raise SyncError(f"{path}: content does not match its sha.")
            server_sha = current.get(path)
            if server_sha == new_sha:
                continue
            if server_sha != f.get("baseSha"):
                conflicts.append({"path": path, "serverSha": server_sha})
                continue
            target = workdir / path
            if not target.resolve().is_relative_to(workdir.resolve()):
                raise SyncError(f"{path}: outside the project.")
            accepted[path] = new_sha
            if new_sha is None:
                if target.exists():
                    target.unlink()
            else:
                blobs.store(content)
                from .store import safe_write
                try:
                    safe_write(workdir, target, content)
                except ValueError as exc:
                    raise SyncError(str(exc)) from exc
        rev = None
        if accepted:
            with db.connect() as con:
                rev = _record(con, pid, accepted, author, origin, message or f"{len(accepted)} file(s) from {origin}")
        h = head(pid)
    return {"head": h, "rev": rev, "accepted": sorted(accepted), "conflicts": conflicts}


def reset_cache() -> None:
    _sha_cache.clear()
