"""git_clone, git_fetch, git_push (0.81.0; Forgejo 0.82.0): a repository from the owner's git server - GitHub or
Forgejo - in the working folder, worked on with git in the sandbox, pushed back as a branch of its own with a pull
request. The network work and the token are the Hub's (app/agent/gitserver.py, DESIGN §6 "Git: GitHub or
Forgejo"); the sandbox only ever sees the Hub's copy of the repository."""
from __future__ import annotations

import asyncio
import shlex

from ...projects import store as projects
from .. import gitserver as gs
from .base import Tool, ToolContext, ToolResult, obj, register
from .shell import SandboxError, sandbox_exec

WORK = ("Work in it with bash (cwd: {rel}): git switch -c <a-short-branch-name> before you change anything, then "
        "git add and git commit as you go. git_push sends the branch to {server} and can open a pull request, which "
        "the user merges; git_fetch gets what is new there. git push and git pull in the sandbox do not reach "
        "{server} (its origin is the Hub's copy).")


def _available(ctx: ToolContext) -> bool:
    """The owner's chats, with a git server set up - not a person's chat, not a persona's task."""
    eng = ctx.engine
    if eng is not None and (getattr(eng, "device", None) is not None or getattr(eng, "persona", None) is not None):
        return False
    return gs.configured(ctx.settings)


def _explain(msg: str, srv: gs.Server, owner: str, name: str) -> str:
    low = msg.lower()
    if any(w in low for w in ("not found", "403", "401", "authentication", "could not read", "denied")):
        need = "Repository access must include it, with Contents: Read" if srv.kind == "github" else \
            "access must include it, with the repository permission Read (and write, to push)"
        msg += f"\n(The token may not reach {owner}/{name}: on {srv.name} its {need} - or the name is wrong.)"
    return msg


async def _run(ctx: ToolContext, cmd: str, timeout: int = 900) -> tuple[bool, str]:
    """A git command in the sandbox (no token there: it reads the Hub's copy at /hub-git)."""
    try:
        res = await sandbox_exec(ctx, cmd, ctx.workdir.sandbox_path, timeout)
    except SandboxError as exc:
        return False, str(exc)
    out = ((res.get("stdout") or "") + (res.get("stderr") or "")).strip()
    return res.get("exit_code") == 0 and not res.get("timed_out"), out[-2000:]


async def _keep_out_of_the_card(ctx: ToolContext, rel: str) -> None:
    """A clone's files count as there before the turn: the files card and the independent review show what the turn
    changed in them, not the whole repository."""
    before = ctx.state.get("files_before")
    if not isinstance(before, dict):
        return
    snap = await asyncio.to_thread(projects.snapshot, ctx.workdir.root)
    prefix = f"{rel}/"
    before.update({p: v for p, v in (snap or {}).items() if p.startswith(prefix)})


async def git_clone(ctx: ToolContext, args: dict) -> ToolResult:
    wd, hub = ctx.workdir, ctx.hub
    try:
        srv = gs.need(ctx.settings)
        owner, name = gs.parse_repo(str(args.get("repo") or ""), srv)
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    target = wd.resolve(str(args.get("folder") or "").strip() or f"{wd.own}{name}")
    rel = wd.rel(target)
    if rel == ".":
        return ToolResult("Clone into a folder of its own (folder: a new folder's name).", is_error=True)
    branch = str(args.get("branch") or "").strip()
    if branch and not gs.valid_branch(branch):
        return ToolResult(f"{branch!r} is not a branch name.", is_error=True)
    if target.exists() and not (target.is_dir() and not any(target.iterdir())):
        try:
            wc = gs.working_copy(hub, srv, wd, rel)
        except ValueError:
            return ToolResult(f"{rel} is there already and is not a clone of {owner}/{name}: give another folder.",
                              is_error=True)
        if (wc.owner, wc.name) != (owner.lower(), name.lower()):
            return ToolResult(f"{rel} is a clone of {wc.owner}/{wc.name}: give another folder.", is_error=True)
        return await _fetch(ctx, srv, wc, already=True)
    try:
        f = await gs.update_mirror(hub, srv, owner, name)
    except gs.GitError as exc:
        return ToolResult(_explain(f"Could not fetch {owner}/{name} from {srv.name}: {exc}", srv, owner, name),
                          is_error=True)
    if branch and f"refs/heads/{branch}" not in f.after:
        heads = sorted(r[len("refs/heads/"):] for r in f.after if r.startswith("refs/heads/"))
        return ToolResult(f"{owner}/{name} has no branch {branch}. Its branches: {', '.join(heads[:30]) or 'none'}.",
                          is_error=True)
    who, email = await gs.identity(hub, srv)
    q = shlex.quote
    dest = f"{wd.sandbox_path}/{rel}"
    cmd = (f"git clone --no-hardlinks --quiet {'--branch ' + q(branch) + ' ' if branch else ''}"
           f"{q(gs.sandbox_mirror(hub, srv, owner, name))} {q(dest)} && cd {q(dest)} && "
           f"git config remote.origin.pushurl {q(hub.env.sandbox_git.rstrip('/') + '/' + gs.PUSH_HINT)} && "
           f"git config user.name {q(who)} && git config user.email {q(email)} && "
           "git log -1 --format='at %h \"%s\"' 2>/dev/null; git rev-parse --abbrev-ref HEAD 2>/dev/null")
    ok, out = await _run(ctx, cmd)
    if not ok:
        return ToolResult(f"The clone in the sandbox failed:\n{out}", is_error=True)
    await _keep_out_of_the_card(ctx, rel)
    await asyncio.to_thread(wd.checkin, "agent", "sandbox", f"git_clone {owner}/{name}")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    at = next((ln for ln in lines if ln.startswith("at ")), "")
    on = lines[-1] if lines and not lines[-1].startswith("at ") else (branch or f.default)
    title = gs.display_name(f.path) or f"{owner}/{name}"
    if not f.after:
        head = f"Cloned {title} from {srv.name} into {rel}: the repository is empty."
    else:
        head = f"Cloned {title} from {srv.name} into {rel} (branch {on}{', ' + at if at else ''})."
    return ToolResult(f"{head}\n{WORK.format(rel=rel, server=srv.name)}")


async def git_fetch(ctx: ToolContext, args: dict) -> ToolResult:
    try:
        srv = gs.need(ctx.settings)
        wc = gs.working_copy(ctx.hub, srv, ctx.workdir, str(args.get("path") or "").strip() or None)
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    return await _fetch(ctx, srv, wc)


async def _fetch(ctx: ToolContext, srv: gs.Server, wc: gs.WorkingCopy, already: bool = False) -> ToolResult:
    known = gs.tracking(ctx.workdir, wc)                 # what the working copy knew of the server before
    try:
        f = await gs.update_mirror(ctx.hub, srv, wc.owner, wc.name)
    except gs.GitError as exc:
        return ToolResult(_explain(f"Could not fetch from {srv.name}: {exc}", srv, wc.owner, wc.name), is_error=True)
    news = await gs.changes(ctx.hub, srv, f, known)
    q = shlex.quote
    ok, out = await _run(ctx, f"git -C {q(wc.sandbox)} fetch --prune --quiet origin && "
                              f"git -C {q(wc.sandbox)} status -sb | head -n 1", 600)
    title = gs.display_name(f.path) or f"{wc.owner}/{wc.name}"
    lines = [f"{wc.rel} is a clone of {title} already, so this fetched instead." if already else ""]
    lines.append(f"Fetched {title} from {srv.name}. Against what the working copy knew: "
                 + ("; ".join(news) if news else "nothing new") + ".")
    if ok:
        lines.append(f"In the working copy origin/* are up to date ({out.strip() or 'git status says nothing'}). "
                     "Nothing in its files changed: merge or rebase yourself (e.g. git merge origin/"
                     f"{f.default or 'main'}).")
    else:
        lines.append(f"The working copy's own fetch failed:\n{out}")
    if already:
        lines.append(WORK.format(rel=wc.rel, server=srv.name))
    return ToolResult("\n".join(x for x in lines if x), is_error=not ok)


async def _push_preview(ctx: ToolContext, args: dict) -> str:
    """For the approval card: what would be pushed, checked first (an error here fails the call without asking)."""
    plan = await gs.plan_push(ctx.hub, ctx.workdir, args)
    ctx.state.setdefault("git_push", {})[ctx.call_id] = plan
    return plan.preview()


async def git_push(ctx: ToolContext, args: dict) -> ToolResult:
    plan = ctx.state.setdefault("git_push", {}).pop(ctx.call_id, None)
    try:
        if plan is None:                   # always allowed: nothing was asked, so nothing was planned yet
            plan = await gs.plan_push(ctx.hub, ctx.workdir, args)
        done = await gs.push(ctx.hub, ctx.workdir, plan)
        srv = gs.need(ctx.settings)
    except (ValueError, gs.GitError) as exc:
        return ToolResult(str(exc), is_error=True)
    owner, name = plan.title.split("/", 1)
    if done.pushed:
        what = (f"{plan.count} commit{'' if plan.count == 1 else 's'}" if plan.count else "the branch")
        lines = [f"Pushed {what} to {plan.branch} on {srv.name} ({srv.branch_url(owner, name, plan.branch)})."]
        try:                                # the working copy learns origin/<branch> and tracks it
            wc = gs.working_copy(ctx.hub, srv, ctx.workdir, plan.path)
            q = shlex.quote
            await _run(ctx, f"git -C {q(wc.sandbox)} fetch --quiet origin && git -C {q(wc.sandbox)} branch --quiet "
                            f"--set-upstream-to={q('origin/' + plan.branch)} {q(plan.branch)}", 300)
        except ValueError:
            pass
    else:
        lines = [f"{srv.name}'s {plan.branch} has these commits already."]
    if done.pr_url:
        lines.append(f"Pull request #{done.pr_number} {'opened' if done.pr_new else 'was open already'}: "
                     f"{done.pr_url} - the user reviews and merges it.")
    elif done.pr_error:
        lines.append(f"The pull request could not be opened: {done.pr_error}. The branch is on {srv.name}; the user "
                     f"can open it from {srv.compare_url(owner, name, plan.default, plan.branch)}")
    return ToolResult("\n".join(lines), is_error=bool(done.pr_error) and not done.pushed)


CLONE_TEXT = ("Clone a repository from {server} - the user's git server - into the working folder, with the user's "
              "token (private repositories too). The clone is a normal git working copy: work in it with bash (git "
              "switch -c, git add, git commit, git log, git diff) and the file tools. Its origin is the Hub's copy of "
              "the repository: git fetch and git pull there get what was last fetched from {server}, and git push "
              "there does not reach it - git_fetch and git_push do. Cloning a repository that is in the folder "
              "already fetches instead.")
FETCH_TEXT = ("Fetch what is new on {server} into a working copy made with git_clone: its origin/* branches are "
              "updated and the answer says what came. Nothing in its files changes - merge or rebase yourself (git "
              "merge origin/main).")
PUSH_TEXT = ("Push a branch of a working copy made with git_clone to {server}, and open a pull request for it. Commit "
             "first (git add, git commit). Never the repository's default branch (main): work on a branch of your own "
             "(git switch -c a-short-name) - the user reviews and merges the pull request. Never forced: when the "
             "server's branch has commits yours lacks, git_fetch and merge or rebase first. The user is asked first "
             "and sees the commits and the files changed.")
PATH = {"type": "string", "description": "The working copy's folder (leave out when there is only one)"}
FETCH_PARAMS = obj({"path": PATH})
PUSH_PARAMS = obj({
    "path": PATH,
    "branch": {"type": "string", "description": "The branch to push (default: the one checked out)"},
    "pull_request": {"type": "boolean", "description": "Open a pull request into the default branch (or name the "
                                                       "one that is open already)"},
    "title": {"type": "string", "description": "The pull request's title (default: the first commit's subject)"},
    "body": {"type": "string", "description": "The pull request's description: what changed, why, and how it was "
                                              "tested"},
})


def _clone_params(server: str) -> dict:
    return obj({
        "repo": {"type": "string", "description": f"owner/name, or the repository's address on {server}"},
        "folder": {"type": "string", "description": "A new folder in the working folder for it (default: named after "
                                                    "the repository; in hub/ in a project with a synced folder)"},
        "branch": {"type": "string", "description": "The branch to check out (default: the repository's default "
                                                    "branch)"},
    }, ["repo"])


def _named(text: str, params):
    """The description (and parameters) with the server's name in them: GitHub, Forgejo (git.slq.dk)."""
    def dynamic(ctx: ToolContext) -> tuple[str, dict]:
        srv = gs.server(ctx.settings)
        name = srv.name if srv else "the git server"
        return text.format(server=name), (params(name) if callable(params) else params)
    return dynamic


register(Tool("git_clone", CLONE_TEXT.format(server="the git server"), _clone_params("the git server"), git_clone,
              available=_available, in_subagent=False, group="git", dynamic=_named(CLONE_TEXT, _clone_params)))

register(Tool("git_fetch", FETCH_TEXT.format(server="the git server"), FETCH_PARAMS, git_fetch,
              available=_available, group="git", dynamic=_named(FETCH_TEXT, FETCH_PARAMS)))

register(Tool("git_push", PUSH_TEXT.format(server="the git server"), PUSH_PARAMS, git_push, needs_approval=True,
              preview=_push_preview, available=_available, in_subagent=False, group="git",
              dynamic=_named(PUSH_TEXT, PUSH_PARAMS)))
