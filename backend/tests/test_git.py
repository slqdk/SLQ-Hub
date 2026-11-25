"""0.81.0, 0.82.0 - the assistant's git server, GitHub or Forgejo: git_clone, git_fetch and git_push
(app/agent/gitserver.py, tools/git.py), against bare repositories in a folder standing in for the server, fake
GitHub and Forgejo APIs, and a sandbox that runs its commands for real (with the sandbox's paths the same as the
Hub's here)."""
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

from app.agent import gitserver as gs

from .test_agent import finish, new_chat, say
from .test_internet import answer, approvals, asking, result_of

GH_TOKEN = "github_pat_11AAAATESTTOKEN0123456789"
FJ_TOKEN = "0123456789abcdef0123456789abcdef01234567"
SERVERS = {"github": {"server": "github", "url": "", "token": GH_TOKEN},
           "forgejo": {"server": "forgejo", "url": "https://forgejo.test", "token": FJ_TOKEN}}
HOST = {"github": "github.com", "forgejo": "forgejo.test"}


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
    """The server's repositories: bare ones in a folder; octo/game has main with two files."""
    root = tmp_path / "remotes"
    monkeypatch.setattr(gs, "GIT_OVERRIDE", f"file://{root}")
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
    """Somebody else pushes a commit to a branch on the server."""
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
        return request.headers.get("authorization") == f"Bearer {GH_TOKEN}"

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


def make_forgejo(state: dict) -> FastAPI:
    """Forgejo's API as the Hub uses it: /api/v1, the token as `Authorization: token ...`."""
    app = FastAPI()

    def signed(request: Request) -> bool:
        return request.headers.get("authorization") == f"token {FJ_TOKEN}"

    @app.get("/api/v1/user")
    async def user(request: Request):
        if not signed(request):
            return JSONResponse({"message": "user does not exist [uid: 0, name: ]"}, status_code=401)
        return {"login": "octo", "id": 1, "email": "octo@slq.dk", "full_name": "Octo"}

    @app.get("/api/v1/user/repos")
    async def repos(request: Request, limit: int = 0):
        state["repo_limit"] = limit
        return [{"full_name": "octo/game"}] if signed(request) else JSONResponse({}, status_code=401)

    @app.post("/api/v1/repos/{owner}/{name}/pulls")
    async def open_pr(owner: str, name: str, request: Request):
        body = await request.json()
        state.setdefault("opened", []).append({"owner": owner, "name": name, **body,
                                               "auth": request.headers.get("authorization")})
        if state.get("exists"):
            return JSONResponse({"message": "pull request already exists for these targets [id: 3, issue_id: 4, "
                                            f"head_repo_id: 1, base_repo_id: 1, head_branch: {body['head']}, "
                                            "base_branch: main]", "url": "https://forgejo.test/api/swagger"},
                                status_code=422)
        return JSONResponse({"number": 7, "html_url": f"https://forgejo.test/{owner}/{name}/pulls/7"},
                            status_code=201)

    @app.get("/api/v1/repos/{owner}/{name}/pulls")
    async def list_prs(owner: str, name: str, state_: str = ""):
        state.setdefault("listed", []).append("open")
        return [{"number": 4, "html_url": f"https://forgejo.test/{owner}/{name}/pulls/4", "head": {"ref": "other"}},
                {"number": 5, "html_url": f"https://forgejo.test/{owner}/{name}/pulls/5", "head": {"ref": "fix"}}]
    return app


@pytest.fixture
def apis(app) -> dict:
    state: dict = {"github": {}, "forgejo": {}}
    app.state.router.transports["api.github.com"] = httpx.ASGITransport(app=make_github(state["github"]))
    app.state.router.transports["forgejo.test"] = httpx.ASGITransport(app=make_forgejo(state["forgejo"]))
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


def use(app, kind: str = "github") -> None:
    app.state.hub.settings.update("git", SERVERS[kind])


def bash(command: str) -> dict:
    return {"tool_calls": [{"name": "bash", "arguments": {"about": "Work on the game", "command": command}}]}


def call(name: str, **arguments) -> dict:
    return {"tool_calls": [{"name": name, "arguments": arguments}]}


def tools_offered(fakes) -> dict[str, str]:
    return {t["function"]["name"]: t["function"]["description"]
            for t in fakes.llama["requests"][0].get("tools") or []}


def chat_dir(app, chat_id: int) -> Path:
    return app.state.hub.env.workspace_dir / "chats" / str(chat_id)


# ---------------------------------------------------------------- the whole way, on either server

@pytest.mark.parametrize("kind", ["github", "forgejo"])
async def test_clone_work_push_and_open_a_pull_request(signed_in, app, fakes, remotes, apis, sandbox, kind):
    use(app, kind)
    token = SERVERS[kind]["token"]
    web = "https://github.com/octo/game" if kind == "github" else "https://forgejo.test/octo/game"
    fakes.llama["replies"] = [
        call("git_clone", repo=f"{web}.git"),
        bash("cd game && git switch -q -c cheaper-beer && sed -i 's/10/8/' bar.js && git commit -qam 'Cheaper beer'"),
        call("git_push", pull_request=True, body="The beer costs 8 now."),
        {"content": "Pull request 7 is open."}, {"content": "Cheaper beer"}]
    c = await new_chat(signed_in)
    rid = await say(signed_in, c["id"], "Make the beer cheaper and open a pull request")
    q = await answer(signed_in, app, rid, "allow")
    run = await finish(app, rid)
    assert run.status == "done", run.events[-3:]
    name = "GitHub" if kind == "github" else "Forgejo (forgejo.test)"
    offered = tools_offered(fakes)
    assert {"git_clone", "git_fetch", "git_push"} <= set(offered)
    assert f"Clone a repository from {name}" in offered["git_clone"]

    clone = result_of(run, "git_clone")
    assert clone["ok"] and f"Cloned octo/game from {name} into game (branch main" in clone["text"], clone["text"]
    preview = q["preview"]                       # what the card asked: checked before asking
    assert q["tool"] == "git_push" and f"Push to {web}" in preview
    assert f"Branch: cheaper-beer - new on {name}; main is not touched" in preview
    assert "1 commit:" in preview and "Cheaper beer" in preview and "bar.js" in preview
    assert 'Then a pull request into main: "Cheaper beer"' in preview
    pushed = result_of(run, "git_push")
    branch_url = f"{web}/tree/cheaper-beer" if kind == "github" else f"{web}/src/branch/cheaper-beer"
    assert pushed["ok"] and f"Pushed 1 commit to cheaper-beer on {name} ({branch_url})" in pushed["text"]
    pr_url = f"{web}/pull/7" if kind == "github" else f"{web}/pulls/7"
    assert f"Pull request #7 opened: {pr_url}" in pushed["text"], pushed["text"]

    bare = remotes / "octo" / "game.git"
    assert git("show", "cheaper-beer:bar.js", cwd=bare) == "const beer = 8;\n"
    assert git("show", "main:bar.js", cwd=bare) == "const beer = 10;\n"          # main is never touched
    author = git("log", "-1", "--format=%an <%ae>", "cheaper-beer", cwd=bare).strip()
    assert author == ("SLQ Hub <42+octo@users.noreply.github.com>" if kind == "github" else "SLQ Hub <octo@slq.dk>")
    pr = apis[kind]["opened"][0]
    assert (pr["owner"], pr["name"], pr["head"], pr["base"], pr["title"], pr["body"]) == \
        ("octo", "game", "cheaper-beer", "main", "Cheaper beer", "The beer costs 8 now.")
    assert pr["auth"] == (f"Bearer {token}" if kind == "github" else f"token {token}")

    # the token never reached the sandbox: no command, no file of the working copy, nothing in the Hub's mirror
    assert all(token not in json.dumps(b) for b in sandbox)
    work = chat_dir(app, c["id"]) / "game"
    for p in [*(work / ".git").rglob("*"), *(app.state.hub.env.git_dir).rglob("*")]:
        if p.is_file() and p.stat().st_size < 1_000_000:
            assert token.encode() not in p.read_bytes(), p
    assert (app.state.hub.env.git_dir / HOST[kind] / "octo" / "game.git" / "HEAD").is_file()   # a mirror per server
    # the working copy tracks the branch on its origin, the Hub's copy - and a push from the sandbox goes nowhere
    assert git("rev-parse", "--abbrev-ref", "cheaper-beer@{upstream}", cwd=work).strip() == "origin/cheaper-beer"
    assert git("config", "remote.origin.url", cwd=work).strip() == \
        f"{app.state.hub.env.sandbox_git}/{HOST[kind]}/octo/game.git"
    assert git("config", "remote.origin.pushurl", cwd=work).strip().endswith("/push-with-the-git_push-tool")

    # the files card shows what the turn changed in the clone, not the whole repository
    files = [e["data"]["files"] for e in run.events if e["type"] == "files"]
    assert [f["path"] for f in files[-1]] == ["game/bar.js"], files


async def test_never_the_default_branch_and_nothing_asked(signed_in, app, fakes, remotes, apis, sandbox):
    use(app, "forgejo")
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


async def test_never_forced_and_git_fetch_says_what_came(signed_in, app, fakes, remotes, apis, sandbox, tmp_path):
    use(app, "github")
    fakes.llama["replies"] = [
        call("git_clone", repo="octo/game"),
        bash("cd game && git switch -q -c feature && echo a > a.txt && git add a.txt && git commit -qm 'A'"),
        {"content": "Committed."}, {"content": "Feature"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Start the feature"))
    assert run.status == "done"
    someone_pushes(remotes, tmp_path, "feature", "theirs")            # the server's feature has a commit ours lacks
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


@pytest.mark.parametrize("kind", ["github", "forgejo"])
async def test_an_open_pull_request_is_named_and_always_asks_nothing(signed_in, app, fakes, remotes, apis, sandbox,
                                                                       kind):
    use(app, kind)
    apis[kind]["exists"] = True
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
    url = "https://github.com/octo/game/pull/5" if kind == "github" else "https://forgejo.test/octo/game/pulls/5"
    assert res["ok"] and f"Pull request #5 was open already: {url}" in res["text"], res["text"]
    assert apis[kind]["listed"] == (["octo:fix"] if kind == "github" else ["open"])


async def test_a_clone_from_the_other_server_is_refused(signed_in, app, fakes, remotes, apis, sandbox):
    use(app, "github")
    fakes.llama["replies"] = [call("git_clone", repo="octo/game"),
                              bash("cd game && git switch -q -c x && echo c > c.txt && git add . && git commit -qm C"),
                              {"content": "Ok."}, {"content": "x"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Clone it"))
    use(app, "forgejo")                                            # the owner switched servers since
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [call("git_push"), {"content": "No."}, {"content": "x"}]
    run = await finish(app, await say(signed_in, c["id"], "Push it"))
    res = result_of(run, "git_push")
    assert not res["ok"] and "is a clone from github.com" in res["text"] and "Forgejo (forgejo.test)" in res["text"]
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [call("git_clone", repo="https://github.com/octo/game"), {"content": "No."},
                              {"content": "x"}]
    run = await finish(app, await say(signed_in, c["id"], "Clone it from GitHub"))
    res = result_of(run, "git_clone")
    assert not res["ok"] and "is not on Forgejo (forgejo.test)" in res["text"]


async def test_without_a_server_there_are_no_git_tools(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "Hej."}, {"content": "Hej"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hej"))
    assert not {"git_clone", "git_fetch", "git_push"} & set(tools_offered(fakes))
    assert (await signed_in.get("/hub/api/help")).json()["git"] is False
    app.state.hub.settings.update("git", {"server": "forgejo", "url": "", "token": FJ_TOKEN})
    assert (await signed_in.get("/hub/api/help")).json()["git"] is False             # Forgejo needs its address
    use(app, "forgejo")
    assert (await signed_in.get("/hub/api/help")).json()["git"] is True


@pytest.mark.parametrize("kind", ["github", "forgejo"])
async def test_asking_first_a_clone_asks_for_the_server(signed_in, app, fakes, remotes, apis, sandbox, kind):
    use(app, kind)
    asking(app)
    fakes.llama["replies"] = [call("git_clone", repo="octo/game"), {"content": "Cloned."}, {"content": "Clone"}]
    c = await new_chat(signed_in)
    rid = await say(signed_in, c["id"], "Clone the game")
    q = await answer(signed_in, app, rid, "deny")
    run = await finish(app, rid)
    assert q["ask"]["what"] == "git" and q["ask"]["key"] == f"internet:site:{HOST[kind]}"
    assert q["ask"]["detail"] == "git clone octo/game" and q["ask"]["always"] == f"Always allow {HOST[kind]}"
    assert not result_of(run, "git_clone")["ok"]
    assert not (app.state.hub.env.git_dir / HOST[kind]).exists()


async def test_the_server_in_settings(signed_in, app, apis):
    assert (await signed_in.get("/hub/api/git?check=true")).json() == \
        {"server": "github", "ready": False, "plain_http": False}
    r = await signed_in.put("/hub/api/settings", json={"section": "git", "values": SERVERS["forgejo"]})
    assert r.status_code == 200 and r.json()["settings"]["git"]["token"] == "********"
    r = await signed_in.put("/hub/api/settings", json={"section": "git", "values": {"token": "********"}})
    assert app.state.hub.settings.get("git", "token") == FJ_TOKEN              # the mask saved again keeps it
    check = (await signed_in.get("/hub/api/git?check=true")).json()
    assert check == {"server": "forgejo", "ready": True, "plain_http": False,
                     "check": {"ok": True, "server": "forgejo", "login": "octo", "kind": "", "repos": ["octo/game"],
                               "more": False}}
    assert apis["forgejo"]["repo_limit"] == 30
    use(app, "github")
    check = (await signed_in.get("/hub/api/git?check=true")).json()
    assert check["check"] == {"ok": True, "server": "github", "login": "octo", "kind": "fine-grained",
                              "repos": ["octo/game"], "more": False}
    app.state.hub.settings.update("git", {"token": "ghp_wrong"})
    check = (await signed_in.get("/hub/api/git?check=true")).json()
    assert check["check"]["ok"] is False and "does not take this token" in check["check"]["error"]
    app.state.hub.settings.update("git", {"server": "forgejo", "url": "http://192.168.1.50:3000", "token": FJ_TOKEN})
    out = (await signed_in.get("/hub/api/git")).json()
    assert out == {"server": "forgejo", "ready": True, "plain_http": True}
    app.state.hub.settings.update("git", {"url": "ftp://x"})
    out = (await signed_in.get("/hub/api/git")).json()
    assert out["ready"] is False and "is not an address" in out["problem"]


def test_addresses_and_names():
    assert gs.normal_url("git.slq.dk") == "https://git.slq.dk"
    assert gs.normal_url("http://192.168.1.50:3000/") == "http://192.168.1.50:3000"
    assert gs.normal_url("https://slq.dk/git/") == "https://slq.dk/git"
    for bad in ("ftp://x", "https://u:p@x", "https://x/?a=1", "http://", "https://x:port"):
        with pytest.raises(ValueError):
            gs.normal_url(bad)
    github = gs.Server("github", gs.GITHUB, "t")
    assert gs.parse_repo("octo/game", github) == ("octo", "game")
    assert gs.parse_repo("https://github.com/slqdk/SLQ-Game-Lobby.git", github) == ("slqdk", "SLQ-Game-Lobby")
    assert gs.parse_repo("git@github.com:octo/game.git", github) == ("octo", "game")
    assert gs.parse_repo("github.com/octo/.github/", github) == ("octo", ".github")
    for bad in ("octo", "octo/game/x", "../x", "octo/..", "https://gitlab.com/a/b", "a b/c"):
        with pytest.raises(ValueError):
            gs.parse_repo(bad, github)
    lan = gs.Server("forgejo", "http://192.168.1.50:3000", "t")
    assert gs.parse_repo("http://192.168.1.50:3000/slq/game", lan) == ("slq", "game")
    assert gs.parse_repo("first.last/game", lan) == ("first.last", "game")
    with pytest.raises(ValueError, match="not on Forgejo"):
        gs.parse_repo("https://github.com/octo/game", lan)
    under = gs.Server("forgejo", "https://slq.dk/git", "t")
    assert gs.parse_repo("https://slq.dk/git/slq/game.git", under) == ("slq", "game")
    assert (under.name, under.key, under.api) == ("Forgejo (slq.dk)", "slq.dk", "https://slq.dk/git/api/v1")
    assert lan.key == "192.168.1.50_3000" and lan.branch_url("a", "b", "x") == "http://192.168.1.50:3000/a/b/src/branch/x"
    for ok in ("fix", "hub/fix-bar", "v1.2", "a+b"):
        assert gs.valid_branch(ok), ok
    for bad in ("", "-x", "/x", "x/", "x..y", "x.lock", "a//b", ".x", "a/.b", "HEAD", "x y", "x~1", "x:y", "x."):
        assert not gs.valid_branch(bad), bad


def test_plain_http_only_for_an_http_forgejo(app):
    hub = app.state.hub
    env = gs._env(hub, gs.Server("forgejo", "http://192.168.1.50:3000", "t"), "t")
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pairs["protocol.http.allow"] == "always" and "http.http://192.168.1.50:3000/.extraheader" in pairs
    env = gs._env(hub, gs.Server("forgejo", "https://git.slq.dk", "t"), "t")
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert "protocol.http.allow" not in pairs and pairs["protocol.allow"] == "never"
    assert set(k for k in pairs if k.endswith(".extraheader")) == {"http.https://git.slq.dk/.extraheader"}


# ---------------------------------------------------------------- what the sandbox could plant

async def test_a_copy_not_made_by_git_clone_or_with_links_is_refused(app, tmp_path):
    from app.projects import store as projects
    hub = app.state.hub
    srv = gs.Server("github", gs.GITHUB, "t")
    ws = hub.env.workspace_dir
    wd = projects.Workdir(projects.chat_dir(ws, 99), None, ws, hub.env.sandbox_workspace)
    wd.root.mkdir(parents=True, exist_ok=True)
    other = wd.root / "other"
    git("init", "-q", str(other))
    git("remote", "add", "origin", "https://github.com/octo/game.git", cwd=other)
    with pytest.raises(ValueError, match="not cloned with git_clone"):
        gs.working_copy(hub, srv, wd, "other")
    with pytest.raises(ValueError, match="no working copy here"):
        gs.working_copy(hub, srv, wd, None)
    # one made to look like a clone, with HEAD a link to a file outside: refused, the file never read
    fake = wd.root / "fake"
    git("init", "-q", str(fake))
    git("remote", "add", "origin", f"{hub.env.sandbox_git}/github.com/octo/game.git", cwd=fake)
    mirror = hub.env.git_dir / "github.com" / "octo" / "game.git"
    mirror.mkdir(parents=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main\n")
    secret = tmp_path / "secret.txt"
    secret.write_text("ref: refs/heads/stolen\n")
    (fake / ".git" / "HEAD").unlink()
    (fake / ".git" / "HEAD").symlink_to(secret)
    wc = gs.working_copy(hub, srv, wd, "fake")
    (wd.root / "up").symlink_to(tmp_path)                      # a link out of the folder is not walked into
    assert gs.find_copies(hub, wd) == ["fake"]
    with pytest.raises(ValueError, match="control characters"):
        gs.working_copy(hub, srv, wd, "fake\n/data")
    with pytest.raises(ValueError, match="link"):
        gs.current_branch(wd, wc)
    (fake / ".git" / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (fake / ".git" / "refs" / "heads" / "x").symlink_to(secret)
    with pytest.raises(ValueError, match="link"):
        gs.branch_sha(wd, wc, "x")
