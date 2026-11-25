"""0.81.0 - GitHub for the assistant: git_clone, git_fetch and git_push (app/agent/github.py, tools/git.py), against
bare repositories in a folder standing in for GitHub, a fake GitHub API, and a sandbox that runs its commands for
real (with the sandbox's paths the same as the Hub's here)."""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.agent import github

from .test_agent import finish, new_chat, say
from .test_internet import answer, approvals, asking, result_of

TOKEN = "github_pat_11AAAATESTTOKEN0123456789"


def git(*args, cwd=None) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(cwd or "/tmp"), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "Someone", "GIT_AUTHOR_EMAIL": "s@example.com",
           "GIT_COMMITTER_NAME": "Someone", "GIT_COMMITTER_EMAIL": "s@example.com"}
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout


@pytest.fixture
def hub_env(hub_env):
    """The sandbox's paths are the Hub's here, so the fake sandbox can run what the tools send it as it is."""
    return dataclasses.replace(hub_env, sandbox_workspace=str(hub_env.workspace_dir),
                               sandbox_git=str(hub_env.data_dir / "git"))


@pytest.fixture
def remotes(tmp_path, monkeypatch) -> Path:
    """"GitHub": bare repositories in a folder; octo/game has main with two files."""
    root = tmp_path / "remotes"
    monkeypatch.setattr(github, "GITHUB", f"file://{root}")
    bare = root / "octo" / "game.git"
    bare.parent.mkdir(parents=True)
    git("init", "--bare", "-q", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    git("clone", "-q", str(bare), str(seed))
    (seed / "README.md").write_text("# The game\n")
    (seed / "bar.js").write_text("const beer = 10;\n")
    git("add", ".", cwd=seed)
    git("commit", "-qm", "First", cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    return root


def someone_pushes(remotes: Path, tmp_path: Path, branch: str, text: str) -> None:
    """Somebody else pushes a commit to a branch on "GitHub"."""
    other = tmp_path / f"other-{branch.replace('/', '-')}-{len(text)}"
    git("clone", "-q", str(remotes / "octo" / "game.git"), str(other))
    if branch != "main":
        try:
            git("switch", "-q", branch, cwd=other)
        except subprocess.CalledProcessError:
            git("switch", "-q", "-c", branch, cwd=other)
    (other / "news.txt").write_text(text)
    git("add", ".", cwd=other)
    git("commit", "-qm", f"Someone: {text}", cwd=other)
    git("push", "-q", "origin", branch, cwd=other)


def make_github(state: dict) -> FastAPI:
    app = FastAPI()

    def signed(request: Request) -> bool:
        return request.headers.get("authorization") == f"Bearer {TOKEN}"

    @app.get("/user")
    async def user(request: Request):
        if not signed(request):
            return JSONResponse({"message": "Bad credentials"}, status_code=401)
        return {"login": "octo", "id": 42, "name": "Octo Cat"}

    @app.get("/user/repos")
    async def repos(request: Request):
        return [{"full_name": "octo/game"}] if signed(request) else JSONResponse({}, status_code=401)

    @app.post("/repos/{owner}/{name}/pulls")
    async def open_pr(owner: str, name: str, request: Request):
        body = await request.json()
        state.setdefault("opened", []).append({"owner": owner, "name": name, **body,
                                               "auth": request.headers.get("authorization")})
        if state.get("exists"):
            return JSONResponse({"message": "Validation Failed", "errors": [
                {"resource": "PullRequest", "code": "custom",
                 "message": f"A pull request already exists for {owner}:{body['head']}."}]}, status_code=422)
        return JSONResponse({"number": 7, "html_url": f"https://github.com/{owner}/{name}/pull/7"}, status_code=201)

    @app.get("/repos/{owner}/{name}/pulls")
    async def list_prs(owner: str, name: str, head: str = ""):
        state.setdefault("listed", []).append(head)
        return [{"number": 5, "html_url": f"https://github.com/{owner}/{name}/pull/5"}]
    return app


@pytest.fixture
def gh_api(app) -> dict:
    state: dict = {}
    app.state.router.transports["api.github.com"] = httpx.ASGITransport(app=make_github(state))
    return state


@pytest.fixture
def sandbox(app, fakes, hub_env, tmp_path) -> list[dict]:
    """A sandbox that runs the commands: bash in the folder, its own HOME, no git config of the machine's."""
    home = tmp_path / "sandbox-home"
    home.mkdir()
    ran: list[dict] = []

    def handler(body: dict) -> dict:
        ran.append(body)
        env = {"PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "LANG": "C.UTF-8"}
        p = subprocess.run(["bash", "-c", body["cmd"]], cwd=body["cwd"], capture_output=True, text=True, env=env,
                           timeout=120)
        return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "timed_out": False,
                "duration_ms": 5, "truncated": False}
    fakes.sandbox["handler"] = handler
    return ran


def with_token(app) -> None:
    app.state.hub.settings.update("github", {"token": TOKEN})


def bash(command: str) -> dict:
    return {"tool_calls": [{"name": "bash", "arguments": {"about": "Work on the game", "command": command}}]}


def call(name: str, **arguments) -> dict:
    return {"tool_calls": [{"name": name, "arguments": arguments}]}


def tools_offered(fakes) -> set[str]:
    return {t["function"]["name"] for t in fakes.llama["requests"][0].get("tools") or []}


def chat_dir(app, chat_id: int) -> Path:
    return app.state.hub.env.workspace_dir / "chats" / str(chat_id)


# ---------------------------------------------------------------- the whole way

async def test_clone_work_push_and_open_a_pull_request(signed_in, app, fakes, remotes, gh_api, sandbox):
    with_token(app)
    fakes.llama["replies"] = [
        call("git_clone", repo="https://github.com/octo/game"),
        bash("cd game && git switch -q -c cheaper-beer && sed -i 's/10/8/' bar.js && git commit -qam 'Cheaper beer'"),
        call("git_push", pull_request=True, body="The beer costs 8 now."),
        {"content": "Pull request 7 is open."}, {"content": "Cheaper beer"}]
    c = await new_chat(signed_in)
    rid = await say(signed_in, c["id"], "Make the beer cheaper and open a pull request")
    q = await answer(signed_in, app, rid, "allow")
    run = await finish(app, rid)
    assert run.status == "done", run.events[-3:]
    assert {"git_clone", "git_fetch", "git_push"} <= tools_offered(fakes)

    clone = result_of(run, "git_clone")
    assert clone["ok"] and "Cloned octo/game into game (branch main" in clone["text"], clone["text"]
    # what the card asked: the branch, the commit and the files changed - checked before asking
    preview = q["preview"]
    assert q["tool"] == "git_push" and "Push to https://github.com/octo/game" in preview
    assert "Branch: cheaper-beer (a new branch on GitHub) - main is not touched" in preview
    assert "1 commit:" in preview and "Cheaper beer" in preview and "bar.js" in preview
    assert 'Then a pull request into main: "Cheaper beer"' in preview
    pushed = result_of(run, "git_push")
    assert pushed["ok"] and "Pushed 1 commit to cheaper-beer on GitHub" in pushed["text"], pushed["text"]
    assert "Pull request #7 opened: https://github.com/octo/game/pull/7" in pushed["text"]

    bare = remotes / "octo" / "game.git"
    assert git("show", "cheaper-beer:bar.js", cwd=bare) == "const beer = 8;\n"
    assert git("show", "main:bar.js", cwd=bare) == "const beer = 10;\n"          # main is never touched
    author = git("log", "-1", "--format=%an <%ae>", "cheaper-beer", cwd=bare).strip()
    assert author == "SLQ Hub <42+octo@users.noreply.github.com>"
    pr = gh_api["opened"][0]
    assert (pr["owner"], pr["name"], pr["head"], pr["base"], pr["title"], pr["body"]) == \
        ("octo", "game", "cheaper-beer", "main", "Cheaper beer", "The beer costs 8 now.")
    assert pr["auth"] == f"Bearer {TOKEN}"

    # the token never reached the sandbox: no command, no file of the working copy, nothing in the Hub's mirror
    assert all(TOKEN not in json.dumps(b) for b in sandbox)
    work = chat_dir(app, c["id"]) / "game"
    for p in [*(work / ".git").rglob("*"), *(app.state.hub.env.git_dir).rglob("*")]:
        if p.is_file() and p.stat().st_size < 1_000_000:
            assert TOKEN.encode() not in p.read_bytes(), p
    # the working copy tracks the branch on its origin, the Hub's copy - and a push from the sandbox goes nowhere
    assert git("rev-parse", "--abbrev-ref", "cheaper-beer@{upstream}", cwd=work).strip() == "origin/cheaper-beer"
    assert git("config", "remote.origin.url", cwd=work).strip() == f"{app.state.hub.env.sandbox_git}/octo/game.git"
    assert git("config", "remote.origin.pushurl", cwd=work).strip().endswith("/push-with-the-git_push-tool")

    # the files card shows what the turn changed in the clone, not the whole repository
    files = [e["data"]["files"] for e in run.events if e["type"] == "files"]
    assert [f["path"] for f in files[-1]] == ["game/bar.js"], files


async def test_never_the_default_branch_and_nothing_asked(signed_in, app, fakes, remotes, gh_api, sandbox):
    with_token(app)
    fakes.llama["replies"] = [
        call("git_clone", repo="octo/game"),
        bash("cd game && sed -i 's/10/9/' bar.js && git commit -qam 'On main'"),
        call("git_push"),
        {"content": "I need a branch."}, {"content": "Main"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Push to main"))
    res = result_of(run, "git_push")
    assert not res["ok"] and "never pushes to main" in res["text"] and "git switch -c" in res["text"]
    assert not approvals(run)                                    # it cannot be done: nothing to ask about
    assert git("show", "main:bar.js", cwd=remotes / "octo" / "game.git") == "const beer = 10;\n"


async def test_never_forced_and_git_fetch_says_what_came(signed_in, app, fakes, remotes, gh_api, sandbox, tmp_path):
    with_token(app)
    app.state.hub.settings.update("agent", {})
    fakes.llama["replies"] = [
        call("git_clone", repo="octo/game"),
        bash("cd game && git switch -q -c feature && echo a > a.txt && git add a.txt && git commit -qm 'A'"),
        {"content": "Committed."}, {"content": "Feature"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Start the feature"))
    assert run.status == "done"
    someone_pushes(remotes, tmp_path, "feature", "theirs")            # GitHub's feature has a commit ours lacks
    someone_pushes(remotes, tmp_path, "main", "news on main")

    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [call("git_push", branch="feature"), call("git_fetch"),
                              {"content": "Fetched."}, {"content": "x"}]
    run = await finish(app, await say(signed_in, c["id"], "Push it"))
    res = result_of(run, "git_push")
    assert not res["ok"] and "never forces" in res["text"] and "git_fetch" in res["text"]
    assert not approvals(run)
    fetched = result_of(run, "git_fetch")
    assert fetched["ok"], fetched["text"]
    assert "main: " in fetched["text"] and "1 new commit" in fetched["text"]
    assert "new branch feature" in fetched["text"]
    work = chat_dir(app, c["id"]) / "game"
    assert git("rev-parse", "origin/main", cwd=work) == git("rev-parse", "main", cwd=remotes / "octo" / "game.git")


async def test_an_open_pull_request_is_named_and_always_allowed_asks_nothing(signed_in, app, fakes, remotes, gh_api,
                                                                               sandbox):
    with_token(app)
    gh_api["exists"] = True
    from app.agent.loop import remember_allowed
    remember_allowed("git_push")                                  # the owner chose Always
    fakes.llama["replies"] = [
        call("git_clone", repo="octo/game", folder="src/game"),
        bash("cd src/game && git switch -q -c fix && echo b > b.txt && git add b.txt && git commit -qm 'B'"),
        call("git_push", path="src/game", pull_request=True, title="Fix it"),
        {"content": "Done."}, {"content": "Fix"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Fix it"))
    assert not approvals(run)
    res = result_of(run, "git_push")
    assert res["ok"] and "Pull request #5 was open already: https://github.com/octo/game/pull/5" in res["text"]
    assert gh_api["listed"] == ["octo:fix"]


async def test_without_a_token_there_are_no_git_tools(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "Hej."}, {"content": "Hej"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hej"))
    assert not {"git_clone", "git_fetch", "git_push"} & tools_offered(fakes)
    h = (await signed_in.get("/hub/api/help")).json()
    assert h["github"] is False
    with_token(app)
    assert (await signed_in.get("/hub/api/help")).json()["github"] is True


async def test_asking_first_a_clone_asks_for_github(signed_in, app, fakes, remotes, gh_api, sandbox):
    with_token(app)
    asking(app)
    fakes.llama["replies"] = [call("git_clone", repo="octo/game"), {"content": "Cloned."}, {"content": "Clone"}]
    c = await new_chat(signed_in)
    rid = await say(signed_in, c["id"], "Clone the game")
    q = await answer(signed_in, app, rid, "deny")
    run = await finish(app, rid)
    assert q["ask"]["what"] == "git" and q["ask"]["key"] == "internet:site:github.com"
    assert q["ask"]["detail"] == "git clone octo/game"
    assert not result_of(run, "git_clone")["ok"]
    assert not (app.state.hub.env.git_dir / "octo").exists()


async def test_the_token_in_settings(signed_in, app, gh_api):
    r = await signed_in.get("/hub/api/github?check=true")
    assert r.json() == {"from": ""}
    r = await signed_in.put("/hub/api/settings", json={"section": "github", "values": {"token": TOKEN}})
    assert r.status_code == 200 and r.json()["settings"]["github"]["token"] == "********"
    r = await signed_in.put("/hub/api/settings", json={"section": "github", "values": {"token": "********"}})
    assert app.state.hub.settings.get("github", "token") == TOKEN             # the mask saved again keeps it
    check = (await signed_in.get("/hub/api/github?check=true")).json()
    assert check == {"from": "settings", "check": {"ok": True, "login": "octo", "kind": "fine-grained",
                                                   "repos": ["octo/game"], "more": False}}
    app.state.hub.settings.update("github", {"token": "ghp_wrong"})
    check = (await signed_in.get("/hub/api/github?check=true")).json()
    assert check["check"]["ok"] is False and "does not take this token" in check["check"]["error"]


async def test_env_token(monkeypatch, app):
    assert github.token(app.state.hub.settings) == ""
    monkeypatch.setenv("GITHUB_TOKEN", " ghp_env ")
    assert github.token(app.state.hub.settings) == "ghp_env"
    assert github.token_from(app.state.hub.settings) == "env"
    app.state.hub.settings.update("github", {"token": "github_pat_x"})
    assert (github.token(app.state.hub.settings), github.token_from(app.state.hub.settings)) == \
        ("github_pat_x", "settings")


# ---------------------------------------------------------------- what the sandbox could plant

async def test_a_copy_not_made_by_git_clone_or_with_links_is_refused(app, tmp_path):
    from app.projects import store as projects
    hub = app.state.hub
    ws = hub.env.workspace_dir
    wd = projects.Workdir(projects.chat_dir(ws, 99), None, ws, hub.env.sandbox_workspace)
    wd.root.mkdir(parents=True, exist_ok=True)
    other = wd.root / "other"
    git("init", "-q", str(other))
    git("remote", "add", "origin", "https://github.com/octo/game.git", cwd=other)
    with pytest.raises(ValueError, match="not cloned with git_clone"):
        github.working_copy(hub, wd, "other")
    with pytest.raises(ValueError, match="no working copy here"):
        github.working_copy(hub, wd, None)
    # one made to look like a clone, with HEAD a link to a file outside: refused, the file never read
    fake = wd.root / "fake"
    git("init", "-q", str(fake))
    git("remote", "add", "origin", f"{hub.env.sandbox_git}/octo/game.git", cwd=fake)
    (hub.env.git_dir / "octo" / "game.git").mkdir(parents=True)
    (hub.env.git_dir / "octo" / "game.git" / "HEAD").write_text("ref: refs/heads/main\n")
    secret = tmp_path / "secret.txt"
    secret.write_text("ref: refs/heads/stolen\n")
    (fake / ".git" / "HEAD").unlink()
    (fake / ".git" / "HEAD").symlink_to(secret)
    wc = github.working_copy(hub, wd, "fake")
    (wd.root / "up").symlink_to(tmp_path)                      # a link out of the folder is not walked into
    assert github.find_copies(hub, wd) == ["fake"]
    with pytest.raises(ValueError, match="control characters"):
        github.working_copy(hub, wd, "fake\n/data")
    with pytest.raises(ValueError, match="link"):
        github.current_branch(wd, wc)
    (fake / ".git" / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (fake / ".git" / "refs" / "heads" / "x").symlink_to(secret)
    with pytest.raises(ValueError, match="link"):
        github.branch_sha(wd, wc, "x")


def test_names():
    assert github.parse_repo("octo/game") == ("octo", "game")
    assert github.parse_repo("https://github.com/slqdk/SLQ-Game-Lobby.git") == ("slqdk", "SLQ-Game-Lobby")
    assert github.parse_repo("git@github.com:octo/game.git") == ("octo", "game")
    assert github.parse_repo("github.com/octo/.github/") == ("octo", ".github")
    for bad in ("octo", "octo/game/x", "../x", "octo/..", "https://gitlab.com/a/b", "a b/c"):
        with pytest.raises(ValueError):
            github.parse_repo(bad)
    for ok in ("fix", "hub/fix-bar", "v1.2", "a+b"):
        assert github.valid_branch(ok), ok
    for bad in ("", "-x", "/x", "x/", "x..y", "x.lock", "a//b", ".x", "a/.b", "HEAD", "x y", "x~1", "x:y", "x."):
        assert not github.valid_branch(bad), bad
