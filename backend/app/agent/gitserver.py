"""The assistant's git server (0.81.0 GitHub; 0.82.0 GitHub or Forgejo - Søren: "Vi laver noget andet i stedet for
github... en Forgejo container på min proxmox... Forgejo er master"): it clones a repository into the working folder,
works on it with git in the sandbox, pushes a branch of its own and opens a pull request - with the owner's token,
which never enters the sandbox (DESIGN §6 "Git: GitHub or Forgejo").

* The server: Settings -> Assistant -> Git server ("git": server github | forgejo, Forgejo's address, the token -
  masked, the owner's alone, in no export). One server at a time.
* A mirror of each repository in data/git/<host>/<owner>/<name>.git: the server's branches and tags in a bare
  repository that only the Hub writes. The Hub runs git there itself (this container), and git gets the token in its
  environment, for the server's address alone - never in a file, a URL or a command line. The sandbox sees data/git
  read-only at /hub-git. The sandbox is kept off the LAN anyway, so a Forgejo there is the Hub's to reach.
* The working copy is cloned in the sandbox from /hub-git (no network), so its `origin` is the mirror: git fetch and
  pull there get what the Hub fetched last (git_fetch fetches from the server first), and git push there is refused
  (its push address is a path that says to use git_push).
* git_push reads the branch's commit from the working copy's files (no git runs in a folder the sandbox can change:
  its config and hooks could run anything, or send the token elsewhere) and pushes that commit from a throwaway
  repository of the Hub's that borrows the working copy's objects. Never the repository's default branch, never
  forced, and the owner is asked first - the card shows the commits and the files changed. It can open the pull
  request.
"""
from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

log = logging.getLogger("hub.git")

GITHUB = "https://github.com"
GITHUB_API = "https://api.github.com"
GIT_OVERRIDE = ""                        # tests: where git fetches and pushes, for any server (a file:// folder)
SERVERS = ("github", "forgejo")
PUSH_HINT = "push-with-the-git_push-tool"   # the working copy's push address: `git push` there says what to use
CLONE_S, FETCH_S, PUSH_S = 1800, 600, 1800  # seconds for git's network work
LOCAL_S = 120                            # git's work in the Hub's own copies
MAX_SMALL = 64 * 1024                    # .git/config, HEAD, a ref
MAX_PACKED = 4 * 1024 * 1024             # .git/packed-refs
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
HOST_RE = re.compile(r"^[a-z0-9._-]{1,253}$")


class GitError(RuntimeError):
    """What went wrong, said for the model (and the user)."""


# ---------------------------------------------------------------- the server

@dataclass(frozen=True)
class Server:
    kind: str                 # "github" | "forgejo"
    base: str                 # its web and git address, no trailing slash: https://github.com, https://git.slq.dk
    token: str

    @property
    def name(self) -> str:
        """For the model and the user: "GitHub", "Forgejo (git.slq.dk)"."""
        return "GitHub" if self.kind == "github" else f"Forgejo ({urlparse(self.base).netloc})"

    @property
    def host(self) -> str:
        """host[:port], as the internet question names the site."""
        return urlparse(self.base).netloc.lower()

    @property
    def key(self) -> str:
        """Its folder in data/git: the host, with the port after an underscore."""
        return self.host.replace(":", "_")

    @property
    def path(self) -> str:
        """Forgejo served under a path (https://example.com/git): "/git"; else ""."""
        return urlparse(self.base).path.rstrip("/")

    @property
    def git_base(self) -> str:
        return GIT_OVERRIDE or self.base

    @property
    def api(self) -> str:
        return GITHUB_API if self.kind == "github" else f"{self.base}/api/v1"

    def auth(self) -> dict:
        if self.kind == "github":
            return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28"}
        return {"Authorization": f"token {self.token}", "Accept": "application/json"}

    def web(self, owner: str, name: str) -> str:
        return f"{self.base}/{owner}/{name}"

    def branch_url(self, owner: str, name: str, branch: str) -> str:
        return f"{self.web(owner, name)}/{'tree' if self.kind == 'github' else 'src/branch'}/{branch}"

    def compare_url(self, owner: str, name: str, base: str, head: str) -> str:
        return f"{self.web(owner, name)}/compare/{base}...{head}" + ("?expand=1" if self.kind == "github" else "")


def normal_url(text: str) -> str:
    """A Forgejo address as given ("git.slq.dk", "http://192.168.1.50:3000/") made plain: https:// when no scheme,
    no trailing slash. Raises ValueError for anything else than an http(s) address of a host."""
    t = (text or "").strip()
    if not t:
        return ""
    if "://" not in t:
        t = "https://" + t
    p = urlparse(t)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError(f"{text!r} is not an address like https://git.example.com or http://192.168.1.50:3000")
    if not HOST_RE.match(p.hostname):
        raise ValueError(f"{text!r} has no plain host name in it")
    try:
        port = p.port
    except ValueError as exc:
        raise ValueError(f"{text!r} has a port that is not a number") from exc
    return f"{p.scheme}://{p.hostname}{':' + str(port) if port else ''}{p.path.rstrip('/')}"


def server(settings) -> Server | None:
    """The git server Settings -> Assistant names, with its token; None when it is not set up (no token, or Forgejo
    without an address)."""
    try:
        s = settings.get("git") or {}
    except Exception:  # noqa: BLE001 - a settings file from before 0.82.0 has no such section
        return None
    tok = str(s.get("token") or "").strip()
    kind = str(s.get("server") or "github")
    if not tok or kind not in SERVERS:
        return None
    if kind == "github":
        return Server("github", GITHUB, tok)
    try:
        url = normal_url(str(s.get("url") or ""))
    except ValueError:
        return None
    return Server("forgejo", url, tok) if url else None


def configured(settings) -> bool:
    return server(settings) is not None


def need(settings) -> Server:
    srv = server(settings)
    if srv is None:
        raise ValueError("The git server is not set up any more (Settings -> Assistant -> Git server).")
    return srv


async def api(hub, srv: Server, method: str, path: str, body: dict | None = None,
              params: dict | None = None) -> httpx.Response:
    """The server's REST API with the token. Raises GitError when it cannot be reached."""
    try:
        async with httpx.AsyncClient(timeout=30, transport=hub.transports.web) as c:
            return await c.request(method, srv.api + path, json=body, params=params,
                                   headers={**srv.auth(), "User-Agent": "slq-hub"})
    except httpx.HTTPError as exc:
        raise GitError(f"{srv.name} could not be reached ({exc.__class__.__name__}) - try again.") from exc


def _message(r: httpx.Response) -> str:
    try:
        data = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    msg = str(data.get("message") or f"HTTP {r.status_code}") if isinstance(data, dict) else f"HTTP {r.status_code}"
    errors = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errors, list):
        more = [str(e.get("message") or e) if isinstance(e, dict) else str(e) for e in errors]
        msg += ": " + "; ".join(m for m in more if m)
    return msg[:500]


def _json(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        return None


async def whoami(hub) -> dict:
    """Whether the server takes the token, whose it is, and which repositories it reaches (the first 30) - for
    Settings. Never the token itself."""
    srv = server(hub.settings)
    if srv is None:
        return {"ok": False, "error": "No token is set."}
    kind = ("fine-grained" if srv.token.startswith("github_pat_") else "classic" if srv.token.startswith("ghp_")
            else "other") if srv.kind == "github" else ""
    try:
        r = await api(hub, srv, "GET", "/user")
    except GitError as exc:
        return {"ok": False, "error": str(exc)}
    if r.status_code == 401:
        return {"ok": False, "error": f"{srv.name} does not take this token - wrong, expired or deleted? Make a new "
                                      "one."}
    if r.status_code == 403 and srv.kind == "forgejo":
        return {"ok": False, "error": f"{srv.name} took the token but it may not read who you are: give it the "
                                      "permission user: Read (and repository: Read and write)."}
    if r.status_code >= 400:
        return {"ok": False, "error": f"{srv.name} answered {_message(r)}"}
    user = _json(r)
    user = user if isinstance(user, dict) else {}
    out = {"ok": True, "server": srv.kind, "login": str(user.get("login") or ""), "kind": kind, "repos": [],
           "more": False}
    try:
        params = {"per_page": "30", "sort": "pushed"} if srv.kind == "github" else {"limit": "30"}
        rr = await api(hub, srv, "GET", "/user/repos", params=params)
        listed = _json(rr) if rr.status_code < 400 else []
        if isinstance(listed, list):
            out["repos"] = [str(x.get("full_name")) for x in listed if isinstance(x, dict) and x.get("full_name")]
            out["more"] = 'rel="next"' in rr.headers.get("link", "")
    except GitError:
        pass
    _remember_user(hub, srv, user)
    return out


def _user_key(srv: Server) -> str:
    return hashlib.sha256(f"{srv.kind}|{srv.base}|{srv.token}".encode()).hexdigest()


def _remember_user(hub, srv: Server, user: dict) -> None:
    hub.extras["git_user"] = (_user_key(srv), {"login": str(user.get("login") or ""), "id": user.get("id"),
                                               "email": str(user.get("email") or "")})


async def identity(hub, srv: Server) -> tuple[str, str]:
    """(name, email) for the working copy's commits: "SLQ Hub" with the token owner's address - on GitHub its
    no-reply address, so GitHub shows them as the owner's; on Forgejo the address Forgejo gives."""
    cached = hub.extras.get("git_user")
    if not cached or cached[0] != _user_key(srv):
        try:
            r = await api(hub, srv, "GET", "/user")
            if r.status_code < 400 and isinstance(_json(r), dict):
                _remember_user(hub, srv, r.json())
        except GitError:
            pass
        cached = hub.extras.get("git_user")
    user = cached[1] if cached and cached[0] == _user_key(srv) else {}
    if srv.kind == "github" and user.get("login") and user.get("id"):
        return "SLQ Hub", f"{user['id']}+{user['login']}@users.noreply.github.com"
    if srv.kind == "forgejo" and user.get("email"):
        return "SLQ Hub", user["email"]
    if srv.kind == "forgejo" and user.get("login"):
        return "SLQ Hub", f"{user['login']}@noreply.{srv.host.split(':')[0]}"
    return "SLQ Hub", f"slq-hub@noreply.{srv.host.split(':')[0]}"


# ---------------------------------------------------------------- names

def parse_repo(text: str, srv: Server) -> tuple[str, str]:
    """("owner", "name") from owner/name or the repository's address on the server (https, git@, with or without
    .git). An address on another server is refused: the Hub works with the one in Settings."""
    t = (text or "").strip().strip("/")
    t = re.sub(r"\.git$", "", t, flags=re.I).strip("/")
    hosts = {srv.host, f"www.{srv.host}"}
    if "://" in t or re.match(r"^[^/@\s]+@[^/:\s]+:", t):
        url = t if "://" in t else "ssh://" + re.sub(r":", "/", t, count=1)
        p = urlparse(url)
        try:
            host = (p.hostname or "").lower() + (f":{p.port}" if p.port and p.scheme in ("http", "https") else "")
        except ValueError:
            host = ""
        if host not in hosts and (p.hostname or "").lower() not in hosts:
            raise ValueError(f"{text!r} is not on {srv.name}, the git server the Hub is set up for "
                             "(Settings -> Assistant -> Git server).")
        t = p.path.strip("/")
        if srv.path and ("/" + t).startswith(srv.path + "/"):
            t = ("/" + t)[len(srv.path) + 1:]
        t = re.sub(r"\.git$", "", t, flags=re.I)
    elif t.split("/")[0].lower() in hosts and t.count("/") >= 2:
        t = t.split("/", 1)[1]
        if srv.path and ("/" + t).startswith(srv.path + "/"):
            t = ("/" + t)[len(srv.path) + 1:]
    parts = t.split("/")
    if len(parts) != 2 or not all(NAME_RE.match(p) and p not in (".", "..") for p in parts):
        raise ValueError(f"{text!r} is not a repository: give it as owner/name, e.g. octocat/hello-world.")
    return parts[0], parts[1]


def valid_branch(b: str) -> bool:
    """A branch name git and the servers take (git check-ref-format's rules, and plain characters only)."""
    if not b or len(b) > 200 or b in ("HEAD", "@") or b.startswith(("/", "-")) or b.endswith(("/", ".")):
        return False
    if not re.fullmatch(r"[A-Za-z0-9._/+-]+", b) or "//" in b or ".." in b:
        return False
    return all(p and not p.startswith(".") and not p.endswith(".lock") for p in b.split("/"))


def mirror_path(hub, srv: Server, owner: str, name: str) -> Path:
    return hub.env.git_dir / srv.key / owner.lower() / f"{name.lower()}.git"


def sandbox_mirror(hub, srv: Server, owner: str, name: str) -> str:
    return f"{hub.env.sandbox_git}/{srv.key}/{owner.lower()}/{name.lower()}.git"


def remote_url(srv: Server, owner: str, name: str) -> str:
    return f"{srv.git_base}/{owner.lower()}/{name.lower()}.git"


# ---------------------------------------------------------------- git, run by the Hub

def _env(hub, srv: Server, tok: str = "") -> dict:
    """git's environment: no system or user config, no prompts, no hooks, https only (http too for a Forgejo the
    owner gave an http:// address; file:// where the tests keep their server), and the token - for the server's
    address alone - as config in the environment."""
    home = hub.env.git_dir / ".home"
    home.mkdir(parents=True, exist_ok=True)
    cfg = [("core.hooksPath", "/dev/null"), ("core.quotePath", "false"), ("credential.helper", ""),
           ("protocol.allow", "never"), ("protocol.https.allow", "always"), ("gc.auto", "0"),
           ("advice.detachedHead", "false"), ("init.defaultBranch", "main")]
    if srv.git_base.startswith("http://"):
        cfg.append(("protocol.http.allow", "always"))
    if srv.git_base.startswith("file:"):
        cfg.append(("protocol.file.allow", "always"))
    if tok:
        basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        cfg.append((f"http.{srv.git_base}/.extraheader", f"Authorization: Basic {basic}"))
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(home),
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": str(len(cfg))}
    for i, (k, v) in enumerate(cfg):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
    return env


@dataclass
class Git:
    code: int
    out: str
    err: str


async def git(hub, srv: Server, args: list[str], *, git_dir: Path | None = None, tok: str = "",
              timeout: float = LOCAL_S, check: bool = True) -> Git:
    """git with the Hub's environment, in one of the Hub's own repositories (git_dir). check: raise GitError when it
    fails, with what git said (never the token: it is in no argument, and git does not print its headers)."""
    env = _env(hub, srv, tok)
    if git_dir is not None:
        env["GIT_DIR"] = str(git_dir)
    proc = await asyncio.create_subprocess_exec("git", *args, env=env, cwd=str(hub.env.git_dir),
                                                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise GitError(f"git {args[0]} took longer than {timeout / 60:.0f} minutes and was stopped.") from None
    res = Git(proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))
    if check and res.code != 0:
        raise GitError(_said(res.err, tok) or f"git {args[0]} failed (exit code {res.code}).")
    return res


def _said(text: str, tok: str = "") -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    said = "\n".join(lines[-12:])[-1500:]
    return said.replace(tok, "***") if tok else said


# ---------------------------------------------------------------- the mirrors

def _lock(hub, path: Path) -> asyncio.Lock:
    return hub.extras.setdefault("git_locks", {}).setdefault(str(path), asyncio.Lock())


async def refs(hub, srv: Server, path: Path) -> dict[str, str]:
    """The mirror's branches and tags: {"refs/heads/main": sha, ...}."""
    res = await git(hub, srv, ["for-each-ref", "--format=%(objectname) %(refname)", "refs/heads", "refs/tags"],
                    git_dir=path)
    out = {}
    for line in res.out.splitlines():
        sha, _, ref = line.partition(" ")
        if sha and ref:
            out[ref] = sha
    return out


@dataclass
class Fetched:
    owner: str
    name: str
    path: Path
    default: str                       # the default branch ("" for an empty repository)
    before: dict[str, str] = field(default_factory=dict)
    after: dict[str, str] = field(default_factory=dict)
    new: bool = False                  # the mirror was made now


async def update_mirror(hub, srv: Server, owner: str, name: str) -> Fetched:
    """Fetch the repository from the server into its mirror - cloned the first time. One at a time per repository."""
    path = mirror_path(hub, srv, owner, name)
    url = remote_url(srv, owner, name)
    async with _lock(hub, path):
        new = not (path / "HEAD").is_file()
        if new:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}")
            try:
                await git(hub, srv, ["clone", "--bare", "--quiet", url, str(tmp)], tok=srv.token, timeout=CLONE_S)
                await git(hub, srv, ["config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*"], git_dir=tmp)
                await git(hub, srv, ["config", "--add", "remote.origin.fetch", "+refs/tags/*:refs/tags/*"],
                          git_dir=tmp)
                await git(hub, srv, ["config", "hub.repo", f"{owner}/{name}"], git_dir=tmp)
                if path.exists():                       # a broken one from before
                    shutil.rmtree(path, ignore_errors=True)
                os.replace(tmp, path)
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            before: dict[str, str] = {}
        else:
            before = await refs(hub, srv, path)
            await git(hub, srv, ["fetch", "--prune", "--quiet", "origin"], git_dir=path, tok=srv.token,
                      timeout=FETCH_S)
        default = await _default_branch(hub, srv, path, url)
        after = await refs(hub, srv, path)
    return Fetched(owner, name, path, default, before, after, new)


async def _default_branch(hub, srv: Server, path: Path, url: str) -> str:
    """The server's default branch (what its HEAD points at), kept as the mirror's HEAD; the mirror's HEAD when the
    server's cannot be read."""
    res = await git(hub, srv, ["ls-remote", "--symref", url, "HEAD"], git_dir=path, tok=srv.token, timeout=FETCH_S,
                    check=False)
    m = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", res.out, re.M)
    if m and valid_branch(m.group(1)):
        await git(hub, srv, ["symbolic-ref", "HEAD", f"refs/heads/{m.group(1)}"], git_dir=path)
        return m.group(1)
    head = await git(hub, srv, ["symbolic-ref", "--short", "HEAD"], git_dir=path, check=False)
    branch = head.out.strip()
    return branch if head.code == 0 and valid_branch(branch) and \
        f"refs/heads/{branch}" in await refs(hub, srv, path) else ""


def display_name(path: Path) -> str:
    """owner/name as it was first given (the mirror's own config, which only the Hub writes)."""
    try:
        text = (path / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r"^\s*repo\s*=\s*(\S+)\s*$", text.split("[hub]", 1)[1] if "[hub]" in text else "", re.M)
    return m.group(1) if m else ""


async def changes(hub, srv: Server, f: Fetched, known: dict[str, str]) -> list[str]:
    """What is new on the server against what the working copy knew (`known`: its origin/* as refs/heads/*), one
    line each: new branches, branches with new commits, deleted ones."""
    lines = []
    for ref in sorted(set(known) | {r for r in f.after if r.startswith("refs/heads/")}):
        if not ref.startswith("refs/heads/"):
            continue
        b = ref[len("refs/heads/"):]
        old, new = known.get(ref), f.after.get(ref)
        if old == new:
            continue
        if old is None:
            lines.append(f"new branch {b} at {new[:7]}")
        elif new is None:
            lines.append(f"branch {b} deleted on {srv.name}")
        else:
            n = await git(hub, srv, ["rev-list", "--count", f"{old}..{new}"], git_dir=f.path, check=False)
            forced = (await git(hub, srv, ["merge-base", "--is-ancestor", old, new], git_dir=f.path,
                                check=False)).code
            count = n.out.strip() if n.code == 0 else "?"
            lines.append(f"{b}: {old[:7]}..{new[:7]}, {count} new commit{'' if count == '1' else 's'}"
                         + (f" (history rewritten on {srv.name})" if forced else ""))
    return lines


# ---------------------------------------------------------------- a working copy, read without git

@dataclass
class WorkingCopy:
    rel: str                  # its folder, relative to the working folder
    folder: Path              # the folder (the Hub's view)
    sandbox: str              # the folder as the sandbox sees it
    gitdir: Path              # its .git (a real folder inside it)
    host: str                 # the server's folder in data/git (Server.key)
    owner: str
    name: str


def _read(wd, rel: str, limit: int) -> str | None:
    """A file of the working copy, read without following links (sandboxed code can plant them); None when it is not
    there. Raises ValueError for a link or a file too big for what it should be."""
    try:
        fd = wd.open_inside(rel)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EINVAL):
            raise ValueError(f"{rel} is a link or not a plain file - the Hub reads none of those") from exc
        if exc.errno == errno.ENOENT:
            return None
        raise
    with os.fdopen(fd, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{rel} is too big for what it should be")
    return data.decode("utf-8", "replace")


def _origin(config: str) -> list[str]:
    """remote "origin"'s url values in a git config file (enough of git's format for what git_clone writes)."""
    urls, section = [], ""
    for raw in config.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            section = re.sub(r"\s+", " ", line.strip("[] ")).lower()
            continue
        if section in ('remote "origin"', "remote.origin"):
            key, _, value = line.partition("=")
            if key.strip().lower() == "url":
                urls.append(value.strip().strip('"'))
    return urls


def find_copies(hub, wd) -> list[str]:
    """The working copies made by git_clone in the working folder (up to three folders deep, no links followed), for
    a call that names none."""
    found: list[str] = []
    base = hub.env.sandbox_git.rstrip("/") + "/"

    def walk(d: Path, rel: str, depth: int) -> None:
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if not e.is_dir(follow_symlinks=False):
                continue
            if e.name == ".git":
                try:
                    cfg = _read(wd, f"{rel}/.git/config" if rel else ".git/config", MAX_SMALL)
                except (ValueError, OSError):
                    cfg = None
                if cfg and any(u.startswith(base) for u in _origin(cfg)):
                    found.append(rel or ".")
            elif depth < 3 and e.name not in ("node_modules", ".venv", "venv", "__pycache__"):
                walk(Path(e.path), f"{rel}/{e.name}" if rel else e.name, depth + 1)
    walk(wd.root, "", 0)
    return found


def working_copy(hub, srv: Server, wd, rel: str | None) -> WorkingCopy:
    """The working copy at rel (a folder of the working folder), made by git_clone from this server: its origin is
    one of the server's mirrors."""
    if not rel:
        found = find_copies(hub, wd)
        copies = [c for c in found if _is_ours(hub, srv, wd, c)]
        if len(copies) == 1:
            rel = copies[0]
        elif not copies:
            if found:                                   # why the one here will not do: another server, or gone
                working_copy(hub, srv, wd, found[0])
            raise ValueError("There is no working copy here: clone the repository with git_clone first.")
        else:
            raise ValueError("Say which working copy (path): " + ", ".join(copies))
    if any(ord(c) < 32 for c in rel):
        raise ValueError("A working copy's folder name has no control characters in it.")
    folder = wd.resolve(rel)
    rel = wd.rel(folder)
    prefix = "" if rel == "." else f"{rel}/"
    gitdir = folder / ".git"
    if gitdir.is_symlink() or not gitdir.is_dir():
        raise ValueError(f"{rel} is not a working copy made with git_clone (no .git folder in it).")
    root = wd.root.resolve()
    if root not in gitdir.resolve().parents:
        raise ValueError(f"{rel}/.git leads outside the working folder.")
    cfg = _read(wd, f"{prefix}.git/config", MAX_SMALL) or ""
    urls = _origin(cfg)
    base = re.escape(hub.env.sandbox_git.rstrip("/") + "/")
    m = re.fullmatch(base + r"([a-z0-9._-]+)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)\.git", urls[0]) \
        if len(urls) == 1 else None
    if not m:
        raise ValueError(f"{rel} was not cloned with git_clone: its origin is not the Hub's copy of a repository. "
                         "Clone it with git_clone and move the work there (git format-patch, git am).")
    host, owner, name = m.group(1), m.group(2), m.group(3)
    if host != srv.key:
        raise ValueError(f"{rel} is a clone from {host}, and the Hub is set up for {srv.name} now. Clone the "
                         f"repository from {srv.name} with git_clone, and move the work there.")
    if not (mirror_path(hub, srv, owner, name) / "HEAD").is_file():
        raise ValueError(f"The Hub has no copy of {owner}/{name} any more: clone it again with git_clone.")
    sandbox = f"{wd.sandbox_path}/{rel}".rstrip("/.") if rel != "." else wd.sandbox_path
    return WorkingCopy(rel, folder, sandbox, gitdir, host, owner, name)


def _is_ours(hub, srv: Server, wd, rel: str) -> bool:
    try:
        working_copy(hub, srv, wd, rel)
        return True
    except (ValueError, OSError):
        return False


def current_branch(wd, wc: WorkingCopy) -> str:
    head = (_read(wd, f"{_prefix(wc)}.git/HEAD", MAX_SMALL) or "").strip()
    if not head.startswith("ref: refs/heads/"):
        raise ValueError("No branch is checked out in the working copy (a detached HEAD): git switch -c <branch> "
                         "first, or name the branch.")
    return head[len("ref: refs/heads/"):]


def branch_sha(wd, wc: WorkingCopy, branch: str) -> str:
    """The branch's commit in the working copy: its loose ref, else packed-refs."""
    loose = _read(wd, f"{_prefix(wc)}.git/refs/heads/{branch}", MAX_SMALL)
    if loose is not None:
        sha = loose.strip()
        if SHA_RE.match(sha):
            return sha
        raise ValueError(f"The working copy's branch {branch} is not a plain commit.")
    packed = _read(wd, f"{_prefix(wc)}.git/packed-refs", MAX_PACKED) or ""
    for line in packed.splitlines():
        sha, _, ref = line.strip().partition(" ")
        if ref == f"refs/heads/{branch}" and SHA_RE.match(sha):
            return sha
    raise ValueError(f"The working copy has no branch {branch}. Commit on a branch first (git switch -c {branch}, "
                     "git add, git commit).")


def _prefix(wc: WorkingCopy) -> str:
    return "" if wc.rel == "." else f"{wc.rel}/"


def tracking(wd, wc: WorkingCopy) -> dict[str, str]:
    """The working copy's origin/* branches (what it knows of the server), as refs/heads/*: from packed-refs and the
    loose refs under .git/refs/remotes/origin, read without following links."""
    out: dict[str, str] = {}
    pre = "refs/remotes/origin/"
    packed = _read(wd, f"{_prefix(wc)}.git/packed-refs", MAX_PACKED) or ""
    for line in packed.splitlines():
        sha, _, ref = line.strip().partition(" ")
        if ref.startswith(pre) and SHA_RE.match(sha):
            out["refs/heads/" + ref[len(pre):]] = sha
    base = wc.gitdir / "refs" / "remotes" / "origin"
    if base.is_dir() and not base.is_symlink():
        for d, _dirs, files in os.walk(base):              # (os.walk does not go into linked folders)
            for fn in files:
                rel = (Path(d) / fn).relative_to(base).as_posix()
                try:
                    sha = (_read(wd, f"{_prefix(wc)}.git/{pre}{rel}", MAX_SMALL) or "").strip()
                except ValueError:
                    continue
                if rel != "HEAD" and SHA_RE.match(sha):
                    out["refs/heads/" + rel] = sha
    return out


# ---------------------------------------------------------------- push

@dataclass
class PushPlan:
    path: str                 # the working copy's folder, relative to the working folder
    owner: str
    name: str
    title: str                # owner/name for the user
    branch: str
    sha: str
    default: str
    remote: str | None        # the server's branch now (None: new)
    commits: list[str]        # "abc1234 subject", newest first
    count: int                # commits the server's branch does not have yet
    stat: str                 # the files changed against the default branch
    pr: bool
    pr_title: str = ""
    pr_body: str = ""
    server: str = "GitHub"    # Server.name
    web: str = ""             # the repository's page

    def preview(self) -> str:
        """What the approval card shows."""
        lines = [f"Push to {self.web or self.title}"]
        state = f"new on {self.server}" if self.remote is None else f"{self.server} has it at {self.remote[:7]}"
        lines.append(f"Branch: {self.branch} - {state}; {self.default or 'the default branch'} is not touched")
        if self.count:
            lines.append(f"{self.count} commit{'' if self.count == 1 else 's'}:")
            lines += [f"  {c}" for c in self.commits]
            if self.count > len(self.commits):
                lines.append(f"  ... and {self.count - len(self.commits)} more")
        else:
            lines.append(f"Nothing new to push: {self.server}'s branch has these commits already.")
        if self.stat:
            lines.append(f"Against {self.default}:" if self.default else "Files:")
            lines += [f"  {s}" for s in self.stat.splitlines()]
        if self.pr:
            lines.append(f"Then a pull request into {self.default}: \"{self.pr_title}\"" if self.pr_title
                         else f"Then a pull request into {self.default}")
        return "\n".join(lines)


async def plan_push(hub, wd, args: dict) -> PushPlan:
    """What git_push would do, checked: the commit, the branch on the server, what is new, the files changed. Raises
    ValueError or GitError when it cannot be done (nothing is asked then)."""
    srv = need(hub.settings)
    wc = working_copy(hub, srv, wd, str(args.get("path") or "").strip() or None)
    branch = str(args.get("branch") or "").strip() or current_branch(wd, wc)
    if branch.startswith("refs/heads/"):
        branch = branch[len("refs/heads/"):]
    if not valid_branch(branch):
        raise ValueError(f"{branch!r} is not a branch name git_push takes (letters, digits, . _ - + /).")
    sha = branch_sha(wd, wc, branch)
    f = await update_mirror(hub, srv, wc.owner, wc.name)
    default = f.default
    if (default and branch.lower() == default.lower()) or (not default and branch.lower() in ("main", "master")):
        raise ValueError(f"git_push never pushes to {branch}, the repository's default branch - the user merges "
                         f"into it. Put the work on a branch of its own (git switch -c a-short-name, which takes "
                         f"the commits along) and push that branch.")
    remote = f.after.get(f"refs/heads/{branch}")
    default_sha = f.after.get(f"refs/heads/{default}") if default else None
    async with _scratch(hub, srv, wc, f.path) as tmp:
        if (await git(hub, srv, ["cat-file", "-e", f"{sha}^{{commit}}"], git_dir=tmp, check=False)).code != 0:
            raise ValueError(f"The working copy's {branch} points at a commit whose objects are not in it.")
        if remote and remote != sha and (await git(hub, srv, ["merge-base", "--is-ancestor", remote, sha],
                                                   git_dir=tmp, check=False)).code != 0:
            raise ValueError(f"{srv.name}'s {branch} has commits that the working copy's {branch} does not have, "
                             "and git_push never forces. Run git_fetch, then in the working copy git merge (or git "
                             f"rebase) origin/{branch}, and push again.")
        exclude = [f"^{x}" for x in (remote, default_sha) if x]
        count = int((await git(hub, srv, ["rev-list", "--count", sha, *exclude], git_dir=tmp)).out.strip() or 0)
        log_res = await git(hub, srv, ["log", "--no-decorate", "--format=%h %s", "-n", "15", sha, *exclude],
                            git_dir=tmp)
        commits = [ln[:120] for ln in log_res.out.splitlines() if ln.strip()]
        stat = ""
        if default_sha:
            base = await git(hub, srv, ["merge-base", default_sha, sha], git_dir=tmp, check=False)
            if base.code == 0 and base.out.strip():
                b = base.out.strip()
                short = (await git(hub, srv, ["diff", "--shortstat", b, sha], git_dir=tmp)).out.strip()
                files = (await git(hub, srv, ["diff", "--stat=90,60", "--stat-count=12", b, sha], git_dir=tmp)).out
                files = "\n".join(ln.rstrip() for ln in files.splitlines()[:-1] if ln.strip())
                stat = "\n".join(x for x in (short, files) if x)
    pr = bool(args.get("pull_request"))
    if pr and not default:
        raise ValueError("The repository has no default branch yet, so a pull request has nothing to go into.")
    if remote == sha and not pr:
        raise ValueError(f"Nothing to push: {srv.name}'s {branch} is at {sha[:7]} already. Commit first (git add, "
                         "git commit), or ask for the pull request (pull_request: true).")
    if remote is None and not count:
        raise ValueError(f"{branch} has no commits that {default or srv.name} does not have: commit the work on it "
                         "first (git add, git commit).")
    title = " ".join(str(args.get("title") or "").split())[:250]
    if pr and not title:
        title = commits[-1].split(" ", 1)[1] if commits and " " in commits[-1] else branch
    shown = display_name(f.path) or f"{wc.owner}/{wc.name}"
    return PushPlan(wc.rel, wc.owner, wc.name, shown, branch, sha, default, remote, commits, count, stat, pr, title,
                    str(args.get("body") or "")[:60000], srv.name, srv.web(*shown.split("/", 1)))


class _scratch:
    """A throwaway bare repository of the Hub's that borrows the working copy's objects and the mirror's: git runs
    there, never in the working copy (whose config and hooks the sandbox could set)."""

    def __init__(self, hub, srv: Server, wc: WorkingCopy, mirror: Path):
        self.hub, self.srv, self.wc, self.mirror = hub, srv, wc, mirror
        self.path = hub.env.git_dir / ".scratch" / f"{uuid.uuid4().hex}.git"

    async def __aenter__(self) -> Path:
        objects = self.wc.gitdir / "objects"
        if objects.is_symlink() or not objects.is_dir():
            raise ValueError(f"{self.wc.rel}/.git/objects is not a plain folder.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await git(self.hub, self.srv, ["init", "--bare", "--quiet", str(self.path)])
        info = self.path / "objects" / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "alternates").write_text(f"{objects.resolve()}\n{(self.mirror / 'objects').resolve()}\n",
                                         encoding="utf-8")
        return self.path

    async def __aexit__(self, *exc) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


@dataclass
class Pushed:
    plan: PushPlan
    pushed: bool
    pr_url: str = ""
    pr_number: int = 0
    pr_new: bool = False
    pr_error: str = ""


async def push(hub, wd, plan: PushPlan) -> Pushed:
    """Push the planned commit to the branch on the server (no force), update the mirror, and open the pull request."""
    srv = need(hub.settings)
    if srv.name != plan.server:
        raise ValueError(f"The Hub was set up for {plan.server} when this was planned, and is set up for {srv.name} "
                         "now: plan the push again.")
    wc = working_copy(hub, srv, wd, plan.path)
    pushed = False
    if plan.remote != plan.sha:
        async with _scratch(hub, srv, wc, mirror_path(hub, srv, plan.owner, plan.name)) as tmp:
            res = await git(hub, srv, ["push", "--porcelain", remote_url(srv, plan.owner, plan.name),
                                       f"{plan.sha}:refs/heads/{plan.branch}"], git_dir=tmp, tok=srv.token,
                            timeout=PUSH_S, check=False)
        if res.code != 0:
            said = _said(res.err + "\n" + res.out, srv.token)
            if "non-fast-forward" in said or ("rejected" in said and "fetch first" in said):
                raise GitError(f"{srv.name} refused the push: its {plan.branch} has commits the working copy "
                               f"lacks. Run git_fetch, merge or rebase origin/{plan.branch}, and push again.")
            if "protected" in said.lower():
                said += f"\n(The branch is protected on {srv.name}: push another branch and open a pull request.)"
            elif "403" in said or "denied" in said.lower() or "not found" in said.lower():
                said += (f"\n(The token may not write to this repository: on {srv.name} it needs "
                         + ("Contents: Read and write." if srv.kind == "github"
                            else "the repository permission Read and write.") + ")")
            raise GitError(f"{srv.name} refused the push:\n{said}")
        pushed = True
        try:
            await update_mirror(hub, srv, plan.owner, plan.name)
        except GitError:
            log.warning("could not fetch %s/%s after the push", plan.owner, plan.name)
    out = Pushed(plan, pushed)
    if plan.pr:
        await _pull_request(hub, srv, plan, out)
    return out


async def _pull_request(hub, srv: Server, plan: PushPlan, out: Pushed) -> None:
    owner, name = quote(plan.owner, safe=""), quote(plan.name, safe="")
    r = await api(hub, srv, "POST", f"/repos/{owner}/{name}/pulls",
                  body={"title": plan.pr_title or plan.branch, "head": plan.branch, "base": plan.default,
                        "body": plan.pr_body or ""})
    if r.status_code == 201 and isinstance(_json(r), dict):
        data = r.json()
        out.pr_url, out.pr_number, out.pr_new = str(data.get("html_url") or ""), int(data.get("number") or 0), True
        return
    if r.status_code in (409, 422) and "already exist" in _message(r).lower():
        found = await _open_pull_request(hub, srv, plan)
        if found:
            out.pr_url, out.pr_number = found
            return
    msg = _message(r)
    if r.status_code in (403, 404):
        msg += (" (the token may lack Pull requests: Read and write for this repository)" if srv.kind == "github"
                else " (the token may lack the repository permission Read and write)")
    out.pr_error = msg


async def _open_pull_request(hub, srv: Server, plan: PushPlan) -> tuple[str, int] | None:
    """The open pull request from the plan's branch: GitHub filters by head; Forgejo's list is read through."""
    owner, name = quote(plan.owner, safe=""), quote(plan.name, safe="")
    params = {"head": f"{plan.owner}:{plan.branch}", "state": "open"} if srv.kind == "github" else \
        {"state": "open", "limit": "50"}
    try:
        rr = await api(hub, srv, "GET", f"/repos/{owner}/{name}/pulls", params=params)
    except GitError:
        return None
    found = _json(rr) if rr.status_code < 400 else []
    for p in found if isinstance(found, list) else []:
        if not isinstance(p, dict):
            continue
        head = p.get("head") if isinstance(p.get("head"), dict) else {}
        if srv.kind == "github" or head.get("ref") == plan.branch:
            return str(p.get("html_url") or ""), int(p.get("number") or 0)
    return None
