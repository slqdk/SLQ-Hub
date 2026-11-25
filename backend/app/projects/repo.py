"""A project's folder as a clone of a repository on the git server (0.84.0).

Søren, 2026-11-25: "Hvert projekt kan kobles til et repo på https://git.slq.dk ... Projektets filmappe er så en
git-clone af repoet. Hubben laver git pull ved åbning og commit + git push når filer ændres, eller via en knap" - and
it replaces SLQ Sync: the PC clones the repository from the server instead.

The folder is the project's synced folder (0.40.0; hub/ beside it, the assistant's, stays out), or the whole project
for one made before. To the user and the assistant it is the clone's working tree - but git never works in it.
Sandboxed code writes that folder with the Hub's uid, and could put a link where a folder was while git runs, so that
git - with the Hub's data in view - read or wrote through it. Git works in a copy only the Hub writes:

    data/git/projects/<id>/repo.git     the repository (its config the Hub's own; the token never in it)
    data/git/projects/<id>/tree/        its working tree: a copy of the folder
    data/git/projects/<id>/state.json   what the copy was made from, and the last sync's outcome

A sync - one at a time per project, never while a chat of the project works:
1. in: the folder's changed files are copied to tree/, read from the workspace down without following a link at any
   step (each folder opened from the one before with O_NOFOLLOW), regular files only, not what the Hub's sync always
   left out (node_modules, .hub, *.tmp, ...), not above MAX_FILE (named in the status instead); a file that is gone
   from the folder is deleted from tree/;
2. commit: git add -A (the repository's .gitignore applies) and a commit when anything changed. Deleting more than
   half of the files (at least MASS_DELETE) waits for the Sync button;
3. fetch the branch and, when both sides have new commits, rebase onto the server's. A conflict: the Hub's commits go
   to a branch of their own on the server (slqhub/conflict-<time>), and the copy takes the server's branch;
4. out: what the server brought - the difference in the copy before and after - is written into the folder the same
   way (each folder opened without following a link, a temp file renamed into place); a file the folder changed since
   step 1 is left alone, and the next sync takes it in;
5. push, never forced.
The token is git's for one command at a time, as a header in its environment (gitserver._env).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .. import db
from ..agent import gitserver as gs
from . import store, syncstore

log = logging.getLogger("hub.repo")

MAX_FILE = 50 * 1024 * 1024            # bigger files stay out of the repository, named in the status
MAX_FILES = 20_000                     # a folder with more is not synced
MAX_DEPTH = 64                         # folders deeper than this are left out (named in the status)
MASS_DELETE = 10                       # deleting this many files, and more than half, waits for the Sync button
DEBOUNCE_S = 20.0                      # a sync this long after the folder last changed
AFTER_TURN_S = 3.0                     # ... after an answer
BUSY_WAIT_S = 10.0                     # while a chat of the project works: look again this often
TURN_SYNC_S = 45.0                     # before an answer the turn waits this long for its sync at most
CONFLICT_BRANCH = "slqhub/conflict-"
KEEP_REASONS = 12


class RepoError(RuntimeError):
    """Something the owner can act on - shown as it is. status: the HTTP status for a route."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- the link, and where things are

def link_of(project: dict | None) -> dict | None:
    """The project's repository: {"repo": "owner/name", "branch": ..., "server": the server's address}, or None."""
    v = ((project or {}).get("settings") or {}).get("repo")
    return v if isinstance(v, dict) and v.get("repo") and v.get("branch") and v.get("server") else None


def base_dir(hub, pid: int) -> Path:
    return hub.env.git_dir / "projects" / str(int(pid))


def git_dir(hub, pid: int) -> Path:
    return base_dir(hub, pid) / "repo.git"


def tree_dir(hub, pid: int) -> Path:
    return base_dir(hub, pid) / "tree"


def sandbox_git_dir(hub, pid: int) -> str:
    """The repository as the sandbox sees it (data/git is mounted read-only at /hub-git): its history for git log."""
    return f"{hub.env.sandbox_git}/projects/{int(pid)}/repo.git"


def folder_parts(project: dict) -> list[str]:
    """The folder's path in the workspace, as names: projects/<id>[/<synced folder>]."""
    parts = ["projects", str(int(project["id"]))]
    folder = store.sync_folder(project)
    return parts + [folder] if folder else parts


def folder_name(project: dict) -> str:
    return store.sync_folder(project) or "the project's folder"


def _state_path(hub, pid: int) -> Path:
    return base_dir(hub, pid) / "state.json"


def load_state(hub, pid: int) -> dict:
    try:
        data = json.loads(_state_path(hub, pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    known = data.get("known")
    data["known"] = {k: tuple(v) for k, v in known.items()} if isinstance(known, dict) else {}
    data.setdefault("status", {})
    return data


def save_state(hub, pid: int, data: dict) -> None:
    p = _state_path(hub, pid)
    tmp = p.with_name(f".state.{uuid.uuid4().hex[:8]}.json")
    tmp.write_text(json.dumps({**data, "known": {k: list(v) for k, v in data.get("known", {}).items()}}),
                   encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- the folder, without following a link

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def open_folder(workspace: Path, parts: list[str]) -> int:
    """The folder opened from the workspace down, each step with O_NOFOLLOW: a link put in place of the folder - or of
    one above it - is refused (OSError), never followed."""
    fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _utf8(name: str) -> bool:
    try:
        name.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def _sig(st: os.stat_result) -> tuple[int, int, int]:
    return st.st_size, st.st_mtime_ns, 0o755 if st.st_mode & 0o100 else 0o644


def _write_file(dir_fd: int, name: str, src: Path | None, data: bytes | None, mode: int) -> None:
    """name in the folder dir_fd: a temp file of its own, renamed over it (a rename replaces a link, never writes
    through one). From src (a file in the Hub's copy) or data."""
    tmp = f".{name[:80]}.{uuid.uuid4().hex[:8]}.hub-tmp"          # (sync and the copy leave *.hub-tmp out)
    out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    try:
        with os.fdopen(out, "wb") as f:
            if src is not None:
                with open(src, "rb") as s:
                    shutil.copyfileobj(s, f, 1024 * 1024)
            else:
                f.write(data or b"")
            os.fchmod(f.fileno(), mode)
        os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise


def _open_parent(root_fd: int, rel: str, make: bool) -> tuple[int, str]:
    """(a file descriptor of rel's folder, rel's name) - each folder on the way opened from the one before with
    O_NOFOLLOW, made when `make` and missing. The caller closes it."""
    parts = rel.split("/")
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            try:
                nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not make:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd, parts[-1]


@dataclass
class CopiedIn:
    files: dict[str, tuple[int, int, int]] = field(default_factory=dict)   # what the copy now holds of the folder
    copied: int = 0
    deleted: int = 0
    big: list[tuple[str, int]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)                       # links, names not UTF-8, unreadable


def _tree_file(tree: Path, rel: str) -> Path:
    """rel in the Hub's copy - no link on the way (the copy is the Hub's, and git checks links out as plain files
    there: core.symlinks false), so a name from the folder cannot lead out of it."""
    p = tree
    for part in rel.split("/"):
        p = p / part
        if p.is_symlink():
            raise OSError(f"{rel}: a link in the Hub's copy")
    return p


def _place(tree: Path, rel: str, tmp: Path) -> None:
    """tmp (a finished copy) as rel in the Hub's copy. What the folder no longer has in the way - a file where it has
    a folder now, or a folder where it has a file - goes."""
    dest = _tree_file(tree, rel)
    d = tree
    for part in rel.split("/")[:-1]:
        d = d / part
        if d.exists() and not d.is_dir():
            d.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir():
        shutil.rmtree(dest)
    os.replace(tmp, dest)


def copy_in(root_fd: int, tree: Path, known: dict[str, tuple[int, int, int]]) -> CopiedIn:
    """Step 1: the folder's files into the Hub's copy (see the module's docstring). known: each file's (size, time,
    mode) when it was last copied, or written by step 4."""
    res = CopiedIn()
    tmpdir = tree.parent / "tmp"                     # beside the copy, never in it: git would take a leftover
    tmpdir.mkdir(exist_ok=True)

    def walk(fd: int, prefix: str, depth: int) -> None:
        for name in sorted(os.listdir(fd)):
            rel = prefix + name
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                continue
            if not _utf8(name):
                res.skipped.append(rel.encode("utf-8", "replace").decode("utf-8"))
                continue
            if stat.S_ISDIR(st.st_mode):
                if name.lower() in syncstore.IGNORE_DIRS:
                    continue
                if depth >= MAX_DEPTH:
                    res.skipped.append(rel + "/")
                    continue
                try:
                    sub = os.open(name, DIR_FLAGS, dir_fd=fd)
                except OSError:
                    res.skipped.append(rel + "/")
                    continue
                try:
                    walk(sub, rel + "/", depth + 1)
                finally:
                    os.close(sub)
            elif stat.S_ISREG(st.st_mode):
                if syncstore.ignored(rel):
                    continue
                if st.st_size > MAX_FILE:
                    res.big.append((rel, st.st_size))
                    continue
                sig = _sig(st)
                if len(res.files) >= MAX_FILES:
                    raise RepoError(f"The folder has more than {MAX_FILES:,} files - too many to sync.")
                dest = _tree_file(tree, rel)
                if known.get(rel) == sig and dest.is_file():
                    res.files[rel] = sig
                    continue
                try:
                    src = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                except OSError:
                    res.skipped.append(rel)
                    continue
                try:
                    now = os.fstat(src)
                    if not stat.S_ISREG(now.st_mode):
                        res.skipped.append(rel)
                        continue
                    tmp = tmpdir / uuid.uuid4().hex
                    try:
                        with os.fdopen(os.dup(src), "rb") as s, open(tmp, "wb") as d:
                            shutil.copyfileobj(s, d, 1024 * 1024)
                        os.chmod(tmp, sig[2])
                        _place(tree, rel, tmp)
                    except BaseException:
                        tmp.unlink(missing_ok=True)
                        raise
                finally:
                    os.close(src)
                res.files[rel] = _sig(now)
                res.copied += 1
            elif stat.S_ISLNK(st.st_mode):
                res.skipped.append(rel)

    walk(root_fd, "", 0)
    kept = {rel for rel, _ in res.big} | set(res.skipped)
    under = tuple(k.rstrip("/") + "/" for k in res.skipped)     # in a folder that is a link now, or unreadable
    for rel in known:
        if rel in res.files or rel in kept or rel.startswith(under) or syncstore.ignored(rel):
            continue
        if any(p.lower() in syncstore.IGNORE_DIRS for p in rel.split("/")[:-1]):
            continue
        try:
            p = _tree_file(tree, rel)
        except OSError:
            continue
        if p.is_file():
            p.unlink()
            res.deleted += 1
    return res


@dataclass
class CopiedOut:
    written: int = 0
    deleted: int = 0
    left: list[str] = field(default_factory=list)          # changed in the folder meanwhile, or in the way


def copy_out(root_fd: int, tree: Path, changes: list[tuple[str, str, str]], files: dict[str, tuple[int, int, int]]
             ) -> CopiedOut:
    """Step 4: what the server brought into the folder. changes: (status A/M/D/T, path, git mode) between the copy's
    commit before and after; files: the folder as step 1 saw it - updated here for what is written or deleted."""
    out = CopiedOut()
    for status, rel, mode in changes:
        try:
            dir_fd, name = _open_parent(root_fd, rel, make=status != "D")
        except FileNotFoundError:
            if status == "D":                              # its folder is gone already
                files.pop(rel, None)
            else:
                out.left.append(rel)
            continue
        except OSError:
            out.left.append(rel)
            continue
        try:
            try:
                cur = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                cur = None
            if cur is not None and (not stat.S_ISREG(cur.st_mode) or files.get(rel) != _sig(cur)):
                out.left.append(rel)                       # the folder's own change (or a folder, a link) wins
                continue
            if status == "D":
                if cur is not None:
                    os.unlink(name, dir_fd=dir_fd)
                    out.deleted += 1
                files.pop(rel, None)
                continue
            if mode not in ("100644", "100755"):            # a link or a submodule: left in the repository as it is
                out.left.append(rel)
                continue
            src = _tree_file(tree, rel)
            _write_file(dir_fd, name, src, None, 0o755 if mode == "100755" else 0o644)
            files[rel] = _sig(os.stat(name, dir_fd=dir_fd, follow_symlinks=False))
            out.written += 1
        except OSError as exc:
            log.warning("repo: could not write %s: %s", rel, exc)
            out.left.append(rel)
        finally:
            os.close(dir_fd)
    return out


# ---------------------------------------------------------------- the syncs

def _now() -> str:
    return db.utcnow()


@dataclass
class Plan:
    project: dict
    link: dict
    srv: gs.Server


class Repos:
    """The projects' repositories: links, syncs, and when they run. hub.extras["repos"]."""

    def __init__(self, hub):
        self.hub = hub
        self.loop: asyncio.AbstractEventLoop | None = None
        self.locks: dict[int, asyncio.Lock] = {}
        self.timers: dict[int, asyncio.Task] = {}
        self.reasons: dict[int, list[str]] = {}
        self.running: dict[int, dict] = {}                 # a sync now: its start

    # ------------------------------------------------ when

    def bind(self) -> None:
        self.loop = asyncio.get_running_loop()

    def changed(self, pid: int | None, origin: str = "", message: str = "") -> None:
        """A write to a project's folder (Workdir.checkin, from any thread): a sync DEBOUNCE_S later."""
        if pid is None or origin == "git" or self.loop is None or self.loop.is_closed():
            return
        try:
            if link_of(store.get_project(pid)) is None:
                return
        except Exception:  # noqa: BLE001
            return
        self.loop.call_soon_threadsafe(self.arm, pid, message, DEBOUNCE_S)

    def turn_ended(self, pid: int, title: str) -> None:
        """An answer in a chat of the project ended: its changes go to the server shortly (its title in the commit)."""
        self.arm(pid, f"Chat: {title}" if title else "", AFTER_TURN_S)

    def arm(self, pid: int, reason: str, delay: float) -> None:
        if reason:
            pending = self.reasons.setdefault(pid, [])
            if reason not in pending:
                pending.append(reason)
                del pending[:-KEEP_REASONS * 4]
        old = self.timers.get(pid)
        if old is not None and not old.done():
            old.cancel()
        self.timers[pid] = asyncio.get_running_loop().create_task(self._later(pid, delay))

    async def _later(self, pid: int, delay: float) -> None:
        await asyncio.sleep(delay)
        while self.busy(pid):
            await asyncio.sleep(BUSY_WAIT_S)
        if self.timers.get(pid) is asyncio.current_task():
            self.timers.pop(pid, None)
        try:
            await self.sync(pid, auto=True)
        except RepoError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("repo: sync of project %s failed", pid)

    def busy(self, pid: int, but: int | None = None) -> bool:
        """Whether a chat of the project is working on an answer (not counting chat `but`)."""
        agent = self.hub.extras.get("agent")
        if agent is None:
            return False
        ids = [r.chat_id for r in list(agent.runs.runs.values()) if not r.finished and r.chat_id != but]
        if not ids:
            return False
        with db.connect() as con:
            row = con.execute(f"SELECT 1 FROM chats WHERE project_id=? AND id IN ({','.join('?' * len(ids))}) LIMIT 1",
                              (pid, *ids)).fetchone()
        return row is not None

    async def before_turn(self, project: dict, chat_id: int) -> None:
        """Before an answer: the server's changes in, the folder's last ones out - so the assistant starts from what
        is there. Not while another chat of the project works; at most TURN_SYNC_S (the sync goes on beyond that)."""
        pid = project["id"]
        if link_of(project) is None or self.busy(pid, but=chat_id):
            return
        task = asyncio.get_running_loop().create_task(self.sync(pid, auto=True))
        done, _ = await asyncio.wait({task}, timeout=TURN_SYNC_S)
        if task in done and not task.cancelled() and task.exception() is not None \
                and not isinstance(task.exception(), RepoError):
            log.warning("repo: the sync before an answer in project %s failed: %s", pid, task.exception())

    # ------------------------------------------------ status

    def status(self, pid: int) -> dict:
        st = dict(load_state(self.hub, pid).get("status") or {})
        if pid in self.running:
            st = {**st, "state": "syncing", "since": self.running[pid]["since"]}
        if self.reasons.get(pid):
            st["pending"] = True
        return st

    def _record(self, pid: int, status: dict) -> dict:
        data = load_state(self.hub, pid)
        data["status"] = {**status, "at": _now()}
        try:
            save_state(self.hub, pid, data)
        except OSError:
            pass
        return data["status"]

    # ------------------------------------------------ link

    def _plan(self, pid: int) -> Plan:
        project = store.get_project(pid)
        if project is None:
            raise RepoError("No such project.", 404)
        link = link_of(project)
        if link is None:
            raise RepoError("The project has no repository.", 404)
        srv = gs.server(self.hub.settings)
        if srv is None:
            raise RepoError("No git server is set up (Settings -> Assistant -> Git server).", 409)
        if srv.base != link["server"]:
            raise RepoError(f"The repository is on {link['server']}, and the Hub's git server is {srv.base} now "
                            "(Settings -> Assistant -> Git server): set that back, or unlink the repository.", 409)
        return Plan(project, link, srv)

    async def _ident(self, srv: gs.Server) -> dict:
        name, email = await gs.identity(self.hub, srv)
        return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email}

    def _git(self, srv: gs.Server, pid: int, ident: dict):
        gd, tree = git_dir(self.hub, pid), tree_dir(self.hub, pid)

        async def run(args: list[str], *, net: bool = False, check: bool = True, timeout: float = gs.LOCAL_S):
            return await gs.git(self.hub, srv, args, git_dir=gd, work_tree=tree, env=ident,
                                tok=srv.token if net else "", timeout=timeout, check=check)
        return run

    async def link(self, pid: int, text: str) -> dict:
        """Make the project's folder a clone of a repository on the git server. The folder must be empty unless the
        repository is: then the folder's files are its first commit. Raises RepoError."""
        project = store.get_project(pid)
        if project is None:
            raise RepoError("No such project.", 404)
        if link_of(project) is not None:
            raise RepoError(f"The project is linked to {link_of(project)['repo']} - unlink it first.", 409)
        srv = gs.server(self.hub.settings)
        if srv is None:
            raise RepoError("Set up the git server first (Settings -> Assistant -> Git server).", 409)
        try:
            owner, name = gs.parse_repo(text, srv)
        except ValueError as exc:
            raise RepoError(str(exc)) from exc
        lock = self.locks.setdefault(pid, asyncio.Lock())
        async with lock:
            store.make_folders(self.hub.env.workspace_dir, project)
            base = base_dir(self.hub, pid)
            if base.exists():                               # what an earlier link left
                shutil.rmtree(base, ignore_errors=True)
            gd, tree = git_dir(self.hub, pid), tree_dir(self.hub, pid)
            tree.mkdir(parents=True)
            ident = await self._ident(srv)
            run = self._git(srv, pid, ident)
            url = gs.remote_url(srv, owner, name)
            try:
                heads = await gs.git(self.hub, srv, ["ls-remote", "--symref", url, "HEAD", "refs/heads/*"],
                                     tok=srv.token, timeout=gs.FETCH_S, check=False)
                if heads.code != 0:
                    raise RepoError(self._explain(gs._said(heads.err, srv.token), srv, owner, name), 502)
                branch = _default_branch(heads.out)
                empty = not any(line.split("\t")[-1].startswith("refs/heads/") for line in heads.out.splitlines())
                fd = await asyncio.to_thread(open_folder, self.hub.env.workspace_dir, folder_parts(project))
                try:
                    has = await asyncio.to_thread(_folder_has_files, fd)
                    # (without its working tree: git would write that into the config)
                    await gs.git(self.hub, srv, ["init", "-q", f"--initial-branch={branch}"], git_dir=gd)
                    for k, v in (("core.bare", "false"), ("core.symlinks", "false"), ("core.autocrlf", "false"),
                                 ("core.fileMode", "true"), ("remote.origin.url", url),
                                 ("remote.origin.fetch", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"),
                                 ("hub.repo", f"{owner}/{name}")):
                        await run(["config", k, v])
                    known: dict[str, tuple[int, int, int]] = {}
                    pulled = pushed = had = 0
                    if not empty:
                        await run(["fetch", "-q", "--no-tags", "origin"], net=True, timeout=gs.CLONE_S)
                        await run(["checkout", "-q", "-B", branch, f"refs/remotes/origin/{branch}"])
                        changes = await self._changes(run, None, "HEAD")
                        if has:                         # only as a part of the repository: each file the same
                            known = (await asyncio.to_thread(copy_in, fd, tree, {})).files
                            state = await run(["status", "--porcelain", "-z", "--untracked-files=all"])
                            differ = [e[3:] for e in state.out.split("\0") if len(e) > 3]
                            if differ:
                                raise RepoError(
                                    f"{folder_name(project)}/ has files already, and {owner}/{name} has its own - "
                                    f"these are not the same: {', '.join(differ[:5])}"
                                    + (f" and {len(differ) - 5} more" if len(differ) > 5 else "") + ". Link an empty "
                                    "repository to put the folder's files there, or empty the folder and link "
                                    "again.", 409)
                            had = len(known)
                            changes = [c for c in changes if c[1] not in known]
                        res = await asyncio.to_thread(copy_out, fd, tree, changes, known)
                        pulled = res.written
                    else:
                        inres = await asyncio.to_thread(copy_in, fd, tree, known)
                        known = inres.files
                        await run(["add", "-A"])
                        if (await run(["diff", "--cached", "--quiet"], check=False)).code == 1:
                            await run(["commit", "-q", "--no-verify", "-m",
                                       f"{project['name']}: the files from SLQ Hub"])
                            push = await run(["push", "-q", "origin", f"HEAD:refs/heads/{branch}"], net=True,
                                             timeout=gs.PUSH_S, check=False)
                            if push.code != 0:
                                raise RepoError(self._explain(gs._said(push.err, srv.token), srv, owner, name), 502)
                            pushed = len(known)
                finally:
                    os.close(fd)
            except BaseException:
                shutil.rmtree(base, ignore_errors=True)
                raise
            settings = {**project["settings"], "repo": {"repo": f"{owner}/{name}", "branch": branch,
                                                         "server": srv.base}}
            store.set_settings(pid, settings)
            save_state(self.hub, pid, {"known": known, "status": {
                "state": "ok", "at": _now(), "pulled": pulled, "pushed": pushed,
                "message": (f"Linked to {owner}/{name}: the folder had {had} of its files already, {pulled} more "
                            "came." if had else
                            f"Cloned {owner}/{name}: {pulled} files." if not empty else
                            f"{owner}/{name} was empty: the folder's {pushed} files are its first commit." if pushed
                            else f"{owner}/{name} is empty, and so is the folder.")}})
        if pulled:
            self._history(pid, f"from {owner}/{name}")
        return self.view(pid)

    async def unlink(self, pid: int) -> dict:
        """The folder stays as it is; the Hub's copy goes, and the server keeps the repository."""
        if store.get_project(pid) is None:
            raise RepoError("No such project.", 404)
        timer = self.timers.pop(pid, None)
        if timer is not None:
            timer.cancel()
        async with self.locks.setdefault(pid, asyncio.Lock()):            # not under a sync
            project = store.get_project(pid)
            self.reasons.pop(pid, None)
            store.set_settings(pid, {k: v for k, v in project["settings"].items() if k != "repo"})
            shutil.rmtree(base_dir(self.hub, pid), ignore_errors=True)
        return self.view(pid)

    def forget(self, pid: int) -> None:
        """The project was deleted."""
        timer = self.timers.pop(pid, None)
        if timer is not None:
            timer.cancel()
        self.reasons.pop(pid, None)
        shutil.rmtree(base_dir(self.hub, pid), ignore_errors=True)

    def view(self, pid: int) -> dict:
        project = store.get_project(pid)
        link = link_of(project)
        srv = gs.server(self.hub.settings)
        out = {"linked": link is not None, "server": srv.name if srv else "", "ready": srv is not None,
               "folder": store.sync_folder(project) if project else "", "whole": not store.sync_folder(project)}
        if link is not None:
            owner, name = link["repo"].split("/", 1)
            out.update(repo=link["repo"], branch=link["branch"], status=self.status(pid),
                       history=sandbox_git_dir(self.hub, pid),
                       web=gs.Server(srv.kind, link["server"], "").web(owner, name) if srv else "")
        return out

    @staticmethod
    def _explain(said: str, srv: gs.Server, owner: str, name: str) -> str:
        low = said.lower()
        if any(w in low for w in ("not found", "403", "401", "authentication", "could not read", "denied")):
            said += (f"\n(The token may not reach {owner}/{name}: on {srv.name} it needs the repository permission "
                     "Read and write for it - or the name is wrong.)")
        return said or "git failed."

    def _history(self, pid: int, message: str) -> None:
        """What the server brought, in the project's history (Files -> History) like any change in the folder."""
        try:
            root = store.project_dir(self.hub.env.workspace_dir, pid)
            syncstore.snapshot(pid, root, author="git", origin="git", message=message)
            store.touch_project(pid)
        except Exception:  # noqa: BLE001
            log.exception("repo: could not record the history of project %s", pid)

    # ------------------------------------------------ sync

    async def sync(self, pid: int, *, auto: bool = False, force: bool = False) -> dict:
        """One sync of the project's folder with its repository (see the module's docstring); its status. auto: by
        a change or an answer - waits for the button before deleting many files. Raises RepoError when it cannot
        start (no link, no server)."""
        plan = self._plan(pid)
        lock = self.locks.setdefault(pid, asyncio.Lock())
        async with lock:
            self.running[pid] = {"since": _now()}
            reasons = self.reasons.pop(pid, [])
            try:
                status = await self._sync(plan, reasons, auto=auto and not force)
            except (gs.GitError, RepoError, OSError) as exc:
                if reasons:
                    self.reasons[pid] = reasons + [r for r in self.reasons.get(pid, []) if r not in reasons]
                msg = str(exc)
                if isinstance(exc, OSError) and not isinstance(exc, RepoError):
                    msg = f"The folder could not be read or written ({exc.strerror or exc}) - nothing was synced."
                status = {"state": "error", "message": msg}
            finally:
                self.running.pop(pid, None)
            return self._record(pid, status)

    async def _sync(self, plan: Plan, reasons: list[str], *, auto: bool) -> dict:
        hub, pid, branch = self.hub, plan.project["id"], plan.link["branch"]
        gd, tree = git_dir(hub, pid), tree_dir(hub, pid)
        if not (gd / "HEAD").is_file() or not tree.is_dir():
            raise RepoError("The Hub's copy of the repository is gone - unlink the repository and link it again.")
        run = self._git(plan.srv, pid, await self._ident(plan.srv))
        await _recover(gd, run)
        data = load_state(hub, pid)
        try:
            fd = await asyncio.to_thread(open_folder, hub.env.workspace_dir, folder_parts(plan.project))
        except OSError as exc:
            raise RepoError(f"{folder_name(plan.project)}/ is not there, or a link was put in its place - nothing was "
                            "synced.") from exc
        try:
            inres = await asyncio.to_thread(copy_in, fd, tree, data["known"])
            files = inres.files
            await run(["add", "-A"])
            staged = _name_status((await run(["diff", "--cached", "--name-status", "-z", "--no-renames"])).out)
            if staged:
                deleted = sum(1 for s, _ in staged if s == "D")
                tracked = len([x for x in (await run(["ls-files", "-z"])).out.split("\0") if x])
                if auto and deleted >= MASS_DELETE and deleted * 2 > tracked:
                    await run(["reset", "-q"])
                    data["known"] = files
                    save_state(hub, pid, data)
                    return {"state": "waiting", "deleted": deleted,
                            "message": f"{deleted} of the repository's {tracked} files are gone from the folder. "
                                       "Press Sync to delete them in the repository too - or bring them back "
                                       "(Files -> History) first."}
                await run(["commit", "-q", "--no-verify", "-m", _message(plan.project, reasons, staged)])
            before = await _head(run)
            fetched = await run(["fetch", "-q", "--no-tags", "origin",
                                 f"+refs/heads/{branch}:refs/remotes/origin/{branch}"], net=True,
                                timeout=gs.FETCH_S, check=False)
            if fetched.code != 0 and "couldn't find remote ref" not in fetched.err.lower():
                raise RepoError(self._explain(gs._said(fetched.err, plan.srv.token), plan.srv,
                                              *plan.link["repo"].split("/", 1)), 502)
            remote = (await run(["rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}^{{commit}}"],
                                check=False)).out.strip() if fetched.code == 0 else ""
            outcome = {"state": "ok"}
            if remote and before and remote != before:
                if await _is_ancestor(run, before, remote):
                    await run(["merge", "-q", "--ff-only", f"refs/remotes/origin/{branch}"])
                elif not await _is_ancestor(run, remote, before):
                    rebased = await run(["rebase", "-q", f"refs/remotes/origin/{branch}"], check=False)
                    if rebased.code != 0:
                        clashing = [x for x in (await run(["diff", "--name-only", "-z", "--diff-filter=U"],
                                                          check=False)).out.split("\0") if x]
                        await run(["rebase", "--abort"], check=False)
                        side = CONFLICT_BRANCH + time.strftime("%Y%m%d-%H%M%S")
                        kept = await run(["push", "-q", "origin", f"{before}:refs/heads/{side}"], net=True,
                                         timeout=gs.PUSH_S, check=False)
                        if kept.code != 0:
                            raise RepoError("Both the folder and the repository changed the same files, and the "
                                            "folder's version could not be kept on a branch of its own: "
                                            + gs._said(kept.err, plan.srv.token), 502)
                        await run(["reset", "-q", "--hard", f"refs/remotes/origin/{branch}"])
                        outcome = {"state": "conflict", "branch": side, "files": clashing[:20],
                                   "message": f"Both the folder and {plan.link['repo']} changed "
                                              f"{', '.join(clashing[:5]) or 'the same files'}. The folder's version "
                                              f"is on the branch {side} on the server; the folder has the "
                                              "repository's now (and Files -> History has both)."}
            elif remote and not before:
                await run(["checkout", "-q", "-B", branch, f"refs/remotes/origin/{branch}"])
            after = await _head(run)
            pulled = await self._bring_out(run, fd, tree, before, after, files)
            data["known"] = files
            save_state(hub, pid, data)
            pushed = 0
            if after and after != remote and outcome["state"] != "conflict":
                ahead = (await run(["rev-list", "--count", after, *([f"^{remote}"] if remote else [])])).out.strip()
                push = await run(["push", "-q", "origin", f"{after}:refs/heads/{branch}"], net=True,
                                 timeout=gs.PUSH_S, check=False)
                if push.code != 0:
                    said = gs._said(push.err, plan.srv.token)
                    if "rejected" in said or "fetch first" in said or "non-fast-forward" in said:
                        self.arm(pid, "", 1.0)             # someone pushed meanwhile: once more, from the fetch
                    raise RepoError(f"The push to {plan.link['repo']} failed: {said}", 502)
                pushed = int(ahead or 0)
        finally:
            os.close(fd)
        if pulled.written or pulled.deleted:
            self._history(pid, f"from {plan.link['repo']}")
        outcome.update(pulled=pulled.written + pulled.deleted, pushed=pushed,
                       big=[{"path": p, "size": n} for p, n in inres.big[:20]],
                       left=(pulled.left + inres.skipped)[:20])
        if outcome["state"] == "ok":
            bits = []
            if pushed:
                bits.append(f"sent {pushed} commit{'s' if pushed != 1 else ''}")
            if outcome["pulled"]:
                bits.append(f"took {outcome['pulled']} file{'s' if outcome['pulled'] != 1 else ''} from the "
                            "repository")
            outcome["message"] = ("; ".join(bits) or "Nothing new") + "."
        return outcome

    async def _bring_out(self, run, fd: int, tree: Path, before: str, after: str,
                         files: dict[str, tuple[int, int, int]]) -> CopiedOut:
        if not after or before == after:
            return CopiedOut()
        changes = await self._changes(run, before or None, after)
        return await asyncio.to_thread(copy_out, fd, tree, changes, files)

    @staticmethod
    async def _changes(run, before: str | None, after: str) -> list[tuple[str, str, str]]:
        """(A/M/D/T, path, the git mode after) from `before` to `after` (everything in `after` when before is None)."""
        modes = {}
        listed = (await run(["ls-tree", "-r", "-z", "--full-tree", after])).out
        for entry in listed.split("\0"):
            meta, _, path = entry.partition("\t")
            if path:
                modes[path] = meta.split(" ")[0]
        if before is None:
            return [("A", p, m) for p, m in sorted(modes.items())]
        diff = _name_status((await run(["diff", "--name-status", "-z", "--no-renames", before, after])).out)
        return [(s, p, modes.get(p, "")) for s, p in diff]


# ---------------------------------------------------------------- git's answers

def _default_branch(ls_remote: str) -> str:
    for line in ls_remote.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            b = line[len("ref: refs/heads/"):-len("\tHEAD")]
            if gs.valid_branch(b):
                return b
    heads = [ln.split("\t")[-1][len("refs/heads/"):] for ln in ls_remote.splitlines()
             if ln.split("\t")[-1].startswith("refs/heads/")]
    for b in ("main", "master"):
        if b in heads:
            return b
    return heads[0] if heads and gs.valid_branch(heads[0]) else "main"


def _name_status(out: str) -> list[tuple[str, str]]:
    parts = [p for p in out.split("\0")]
    res, i = [], 0
    while i + 1 < len(parts):
        s, p = parts[i], parts[i + 1]
        if not s:
            break
        res.append((s[0], p))
        i += 2
    return res


async def _head(run) -> str:
    r = await run(["rev-parse", "-q", "--verify", "HEAD^{commit}"], check=False)
    return r.out.strip() if r.code == 0 else ""


async def _is_ancestor(run, a: str, b: str) -> bool:
    return (await run(["merge-base", "--is-ancestor", a, b], check=False)).code == 0


async def _recover(gd: Path, run) -> None:
    """What a sync cut off (the Hub stopped, a timeout) left: a rebase or a merge halfway, a lock."""
    if (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists():
        await run(["rebase", "--abort"], check=False)
    if (gd / "MERGE_HEAD").exists():
        await run(["merge", "--abort"], check=False)
    lock = gd / "index.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > 300:
        lock.unlink(missing_ok=True)


def _folder_has_files(fd: int) -> bool:
    """Whether the folder holds a file sync would carry (not only empty folders or what it leaves out)."""
    def walk(d: int, prefix: str, depth: int) -> bool:
        for name in os.listdir(d):
            try:
                st = os.stat(name, dir_fd=d, follow_symlinks=False)
            except OSError:
                continue
            rel = prefix + name
            if stat.S_ISDIR(st.st_mode):
                if name.lower() in syncstore.IGNORE_DIRS:
                    continue
                if depth >= MAX_DEPTH:
                    return True
                try:
                    sub = os.open(name, DIR_FLAGS, dir_fd=d)
                except OSError:
                    return True
                try:
                    if walk(sub, rel + "/", depth + 1):
                        return True
                finally:
                    os.close(sub)
            elif not syncstore.ignored(rel):
                return True
        return False
    return walk(fd, "", 0)


def _message(project: dict, reasons: list[str], staged: list[tuple[str, str]]) -> str:
    """A commit's message: the chat's title when an answer made the change, else what changed; then what the Hub
    did, from the project's history notes."""
    chats = [r[len("Chat: "):] for r in reasons if r.startswith("Chat: ")]
    if chats:
        subject = chats[-1]
    else:
        names = [p.rsplit("/", 1)[-1] for _, p in staged]
        verb = "Delete" if all(s == "D" for s, _ in staged) else "Update"
        subject = f"{verb} {', '.join(names[:3])}" + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    subject = " ".join(subject.split())[:72] or "Changes from SLQ Hub"
    body = [r for r in reasons if not r.startswith("Chat: ")][-KEEP_REASONS:]
    lines = [subject, "", f"From SLQ Hub, the project \"{project['name']}\"."]
    if len(chats) > 1:
        lines += ["", "Chats: " + "; ".join(dict.fromkeys(chats))]
    if body:
        lines += [""] + [f"- {b[:120]}" for b in body]
    return "\n".join(lines)
