"""0.84.0 - a project's folder as a clone of a repository on the git server (app/projects/repo.py): linked, synced
both ways through the Hub's own copy, conflicts kept on a branch, and nothing in the folder - no link, no token -
reaching what it should not. Against bare repositories in a folder standing in for the server (test_git's)."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from app.projects import repo, store, syncstore

from .test_agent import finish, new_chat, say
from .test_git import FJ_TOKEN, apis, bash, git, hub_env, remotes, sandbox, someone_pushes, tools_offered  # noqa: F401

ORIGIN = {"Origin": "http://hub"}


def forgejo(app) -> None:
    app.state.hub.settings.update("git", {"server": "forgejo", "url": "https://forgejo.test", "token": FJ_TOKEN})


def new_project(app, name: str = "Game", folder: str | None = None) -> dict:
    p = store.create_project(name, sync_folder=folder)
    store.make_folders(app.state.hub.env.workspace_dir, p)
    return p


def folder_of(app, p: dict) -> Path:
    root = app.state.hub.env.workspace_dir / "projects" / str(p["id"])
    sub = store.sync_folder(store.get_project(p["id"]))
    return root / sub if sub else root


def remote(remotes: Path, path: str, ref: str = "main", name: str = "game") -> str:
    return git("show", f"{ref}:{path}", cwd=remotes / "octo" / f"{name}.git")


def remote_files(remotes: Path, ref: str = "main", name: str = "game") -> set[str]:
    return set(git("ls-tree", "-r", "--name-only", ref, cwd=remotes / "octo" / f"{name}.git").split())


async def link(client, p: dict, what: str = "octo/game") -> dict:
    r = await client.put(f"/hub/api/projects/{p['id']}/repo", json={"repo": what}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    return r.json()


async def sync(client, p: dict, **params) -> dict:
    r = await client.post(f"/hub/api/projects/{p['id']}/repo/sync", params=params, headers=ORIGIN)
    assert r.status_code == 200, r.text
    return r.json()["status"]


def write(folder: Path, rel: str, text: str) -> None:
    """As code in the sandbox would: straight into the folder."""
    p = folder / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# ---------------------------------------------------------------- linking

async def test_a_linked_folder_is_a_clone_and_the_token_stays_out_of_it(signed_in, app, remotes, apis):
    forgejo(app)
    hub = app.state.hub
    p = new_project(app)
    view = await link(signed_in, p, "https://forgejo.test/octo/game.git")
    assert view["linked"] and view["repo"] == "octo/game" and view["branch"] == "main"
    assert view["web"] == "https://forgejo.test/octo/game" and view["status"]["state"] == "ok"
    assert "Cloned octo/game: 2 files" in view["status"]["message"]
    folder = folder_of(app, p)
    assert (folder / "README.md").read_text() == "# The game\n" and (folder / "bar.js").read_text() == \
        "const beer = 10;\n"
    assert not (folder / ".git").exists()                      # git's own folder is the Hub's, outside it
    assert store.get_project(p["id"])["settings"]["repo"] == {"repo": "octo/game", "branch": "main",
                                                              "server": "https://forgejo.test"}
    config = (repo.git_dir(hub, p["id"]) / "config").read_text()
    assert f"url = file://{remotes}/octo/game.git" in config and "symlinks = false" in config
    for f in [*repo.base_dir(hub, p["id"]).rglob("*"), *hub.env.workspace_dir.rglob("*")]:
        if f.is_file():
            assert FJ_TOKEN.encode() not in f.read_bytes(), f
    revs = syncstore.revisions(p["id"])                        # Files -> History has what came
    assert revs[0]["origin"] == "git" and "from octo/game" in revs[0]["message"]

    # once linked, linked: another link is refused until it is unlinked
    r = await signed_in.put(f"/hub/api/projects/{p['id']}/repo", json={"repo": "octo/other"}, headers=ORIGIN)
    assert r.status_code == 409 and "unlink it first" in r.json()["detail"]
    # a repository on another server is not taken
    q = new_project(app, "Other")
    r = await signed_in.put(f"/hub/api/projects/{q['id']}/repo", json={"repo": "https://github.com/octo/game"},
                            headers=ORIGIN)
    assert r.status_code == 400 and "is not on Forgejo (forgejo.test)" in r.json()["detail"]


async def test_a_folder_with_files_goes_only_to_an_empty_repository(signed_in, app, remotes, apis):
    forgejo(app)
    p = new_project(app, "Conveyor")
    write(folder_of(app, p), "MAIN.TcPOU", "PROGRAM MAIN\n")
    r = await signed_in.put(f"/hub/api/projects/{p['id']}/repo", json={"repo": "octo/game"}, headers=ORIGIN)
    assert r.status_code == 409 and "Conveyor/ has files already, and octo/game has its own" in r.json()["detail"]
    assert "repo" not in store.get_project(p["id"])["settings"] and not repo.base_dir(app.state.hub, p["id"]).exists()

    git("init", "--bare", "-q", "-b", "main", str(remotes / "octo" / "conveyor.git"))       # an empty one
    view = await link(signed_in, p, "octo/conveyor")
    assert view["status"]["state"] == "ok" and "the folder's 1 files are its first commit" in view["status"]["message"]
    assert remote(remotes, "MAIN.TcPOU", name="conveyor") == "PROGRAM MAIN\n"
    assert git("log", "-1", "--format=%an <%ae>|%s", cwd=remotes / "octo" / "conveyor.git").strip() == \
        "SLQ Hub <octo@slq.dk>|Conveyor: the files from SLQ Hub"


# ---------------------------------------------------------------- syncing

async def test_changes_go_both_ways(signed_in, app, remotes, apis, tmp_path):
    forgejo(app)
    p = new_project(app)
    await link(signed_in, p)
    folder = folder_of(app, p)
    wd = store.project_workdir(app.state.hub.env.workspace_dir, p["id"])
    wd.write("Game/bar.js", "const beer = 8;\n")              # through the Hub (the editor) ...
    write(folder, "levels/one.json", "{}\n")                   # ... and as the sandbox writes
    (folder / "README.md").unlink()
    st = await sync(signed_in, p)
    assert st["state"] == "ok" and st["pushed"] == 1 and "sent 1 commit" in st["message"]
    assert remote(remotes, "bar.js") == "const beer = 8;\n" and remote(remotes, "levels/one.json") == "{}\n"
    assert "README.md" not in remote_files(remotes)
    assert git("log", "-1", "--format=%an <%ae>", cwd=remotes / "octo" / "game.git").strip() == "SLQ Hub <octo@slq.dk>"

    # the server's changes come in; nothing new here goes out
    someone_pushes(remotes, tmp_path, "main", "Snow on level two")
    st = await sync(signed_in, p)
    assert st["state"] == "ok" and st["pulled"] == 1 and st["pushed"] == 0
    assert (folder / "news.txt").read_text() == "Snow on level two"
    assert syncstore.revisions(p["id"])[0]["origin"] == "git"

    # both sides changed, different files: the folder's commit goes on top of the server's
    write(folder, "bar.js", "const beer = 7;\n")
    someone_pushes(remotes, tmp_path, "main", "Rain on level three")
    st = await sync(signed_in, p)
    assert st["state"] == "ok" and st["pushed"] == 1, st
    assert remote(remotes, "bar.js") == "const beer = 7;\n" and remote(remotes, "news.txt") == "Rain on level three"
    assert (folder / "news.txt").read_text() == "Rain on level three"
    subjects = git("log", "--format=%s", "-3", cwd=remotes / "octo" / "game.git").splitlines()
    assert subjects[0] == "Update bar.js" and subjects[1] == "Someone: Rain on level three"   # no merge, in a row


async def test_the_same_file_changed_on_both_sides_keeps_both(signed_in, app, remotes, apis, tmp_path):
    forgejo(app)
    p = new_project(app)
    await link(signed_in, p)
    folder = folder_of(app, p)
    write(folder, "news.txt", "Ours")
    someone_pushes(remotes, tmp_path, "main", "Theirs")
    st = await sync(signed_in, p)
    assert st["state"] == "conflict" and st["branch"].startswith("slqhub/conflict-") and st["files"] == ["news.txt"]
    assert "news.txt" in st["message"] and st["branch"] in st["message"]
    assert remote(remotes, "news.txt") == "Theirs"                         # the server's branch is not touched
    assert remote(remotes, "news.txt", ref=st["branch"]) == "Ours"         # ours is kept beside it
    assert (folder / "news.txt").read_text() == "Theirs"                   # the folder takes the server's
    assert (await sync(signed_in, p))["state"] == "ok"                     # and goes on from there


async def test_what_stays_out_of_the_repository(signed_in, app, remotes, apis, monkeypatch):
    """Links are never followed - not to a file, not to a folder of the Hub's data - nor copied; big files, what the
    Hub always leaves out and what .gitignore names stay in the folder."""
    forgejo(app)
    hub = app.state.hub
    monkeypatch.setattr(repo, "MAX_FILE", 1000)
    secret = hub.env.data_dir / "secret.txt"
    secret.write_text("the Hub's secret")
    p = new_project(app)
    await link(signed_in, p)
    folder = folder_of(app, p)
    os.symlink(secret, folder / "innocent.txt")
    os.symlink(hub.env.data_dir, folder / "data")
    write(folder, "big.bin", "x" * 2000)
    write(folder, "node_modules/left/index.js", "module.exports = 1;\n")
    write(folder, ".gitignore", "dist/\n")
    write(folder, "dist/game.min.js", "minified\n")
    write(folder, "game.js", "play();\n")
    st = await sync(signed_in, p)
    assert st["state"] == "ok", st
    assert remote_files(remotes) == {"README.md", "bar.js", ".gitignore", "game.js"}
    assert st["big"] == [{"path": "big.bin", "size": 2000}] and {"innocent.txt", "data"} <= set(st["left"])
    pushed = git("log", "-p", "main", cwd=remotes / "octo" / "game.git")
    assert "the Hub's secret" not in pushed and "settings" not in pushed

    # a link put in place of the whole folder: nothing is synced, and nothing in it is deleted on the server
    moved = folder.with_name("moved-away")
    folder.rename(moved)
    os.symlink(hub.env.data_dir, folder)
    st = await sync(signed_in, p)
    assert st["state"] == "error" and "a link was put in its place" in st["message"]
    assert remote_files(remotes) == {"README.md", "bar.js", ".gitignore", "game.js"}


async def test_many_files_gone_wait_for_the_button(signed_in, app, remotes, apis, tmp_path):
    forgejo(app)
    seed = tmp_path / "seed12"
    git("clone", "-q", str(remotes / "octo" / "game.git"), str(seed))
    for i in range(12):
        (seed / f"level{i}.json").write_text(f"{i}\n")
    git("add", ".", cwd=seed)
    git("commit", "-qm", "Levels", cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    p = new_project(app)
    await link(signed_in, p)
    repos = app.state.hub.extras["repos"]
    folder = folder_of(app, p)
    for f in folder.glob("level*.json"):
        f.unlink()
    st = await repos.sync(p["id"], auto=True)                   # after a change or an answer
    assert st["state"] == "waiting" and st["deleted"] == 12 and "Press Sync" in st["message"]
    assert len([f for f in remote_files(remotes) if f.startswith("level")]) == 12
    st = await sync(signed_in, p, force=True)                   # the button
    assert st["state"] == "ok" and not [f for f in remote_files(remotes) if f.startswith("level")]


# ---------------------------------------------------------------- when it syncs

async def test_an_answer_and_a_change_sync_by_themselves(signed_in, app, fakes, remotes, apis, sandbox, monkeypatch):
    forgejo(app)
    monkeypatch.setattr(repo, "AFTER_TURN_S", 0.05)
    monkeypatch.setattr(repo, "DEBOUNCE_S", 0.05)
    monkeypatch.setattr(repo, "BUSY_WAIT_S", 0.05)
    hub = app.state.hub
    p = new_project(app)
    await link(signed_in, p)
    repos = hub.extras["repos"]
    repos.bind()
    store.CHECKIN_HOOKS[:] = [repos.changed]
    folder = folder_of(app, p)
    fakes.llama["replies"] = [bash("cd Game && sed -i 's/10/9/' bar.js"), {"content": "Done."},
                              {"content": "Cheaper beer"}]
    c = await new_chat(signed_in, project_id=p["id"])
    run = await finish(app, await say(signed_in, c["id"], "Make the beer cheaper"))
    assert run.status == "done", run.events[-3:]
    offered = tools_offered(fakes)
    assert "git_clone" not in offered and "git_push" not in offered          # the Hub does git here
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "It is a clone of the repository octo/game on forgejo.test (branch main)" in system
    assert f"git --git-dir={hub.env.data_dir / 'git'}/projects/{p['id']}/repo.git log" in system

    async def pushed(text: str, path: str = "bar.js") -> None:
        for _ in range(200):
            try:
                if remote(remotes, path) == text:
                    return
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.05)
        raise AssertionError(f"{path} never became {text!r}")

    await pushed("const beer = 9;\n")
    log = git("log", "-1", "--format=%s%n%b", cwd=remotes / "octo" / "game.git")
    assert log.startswith("Cheaper beer") and 'the project "Game"' in log

    # a change through the Hub - the editor, an upload - goes a while after, with what it was
    store.project_workdir(hub.env.workspace_dir, p["id"]).write("Game/notes.md", "Beer at 9.\n",
                                                               author="user", origin="web")
    await pushed("Beer at 9.\n", "notes.md")
    assert "wrote Game/notes.md" in git("log", "-1", "--format=%b", cwd=remotes / "octo" / "game.git")

    # while a chat of the project works, it waits
    calls = []
    monkeypatch.setattr(repos, "busy", lambda pid, but=None: len(calls) < 3 and not calls.append(pid))
    write(folder, "wait.txt", "later\n")
    repos.arm(p["id"], "later", 0.01)
    await pushed("later\n", "wait.txt")
    assert len(calls) == 3


async def test_unlinking_keeps_the_files_and_the_repository(signed_in, app, remotes, apis):
    forgejo(app)
    hub = app.state.hub
    p = new_project(app)
    await link(signed_in, p)
    r = await signed_in.delete(f"/hub/api/projects/{p['id']}/repo", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["linked"] is False
    assert (folder_of(app, p) / "bar.js").exists() and not repo.base_dir(hub, p["id"]).exists()
    assert "repo" not in store.get_project(p["id"])["settings"] and remote_files(remotes) == {"README.md", "bar.js"}
    r = await signed_in.post(f"/hub/api/projects/{p['id']}/repo/sync", headers=ORIGIN)
    assert r.status_code == 404
    # linked again: the folder is a part of the repository, file for file - what it lacks comes
    (folder_of(app, p) / "README.md").unlink()
    view = await link(signed_in, p)
    assert "the folder had 1 of its files already, 1 more came" in view["status"]["message"]
    assert (folder_of(app, p) / "README.md").exists()
    # but not when a file differs - nor with one the repository does not have
    await signed_in.delete(f"/hub/api/projects/{p['id']}/repo", headers=ORIGIN)
    write(folder_of(app, p), "bar.js", "const beer = 1;\n")
    write(folder_of(app, p), "mine.txt", "mine\n")
    r = await signed_in.put(f"/hub/api/projects/{p['id']}/repo", json={"repo": "octo/game"}, headers=ORIGIN)
    assert r.status_code == 409 and "not the same: bar.js, mine.txt" in r.json()["detail"]
    assert not repo.base_dir(hub, p["id"]).exists() and (folder_of(app, p) / "bar.js").read_text() == \
        "const beer = 1;\n"
    # a project deleted takes the Hub's copy with it
    (folder_of(app, p) / "mine.txt").unlink()
    write(folder_of(app, p), "bar.js", "const beer = 10;\n")
    await link(signed_in, p)
    assert (await signed_in.delete(f"/hub/api/projects/{p['id']}", headers=ORIGIN)).status_code == 200
    assert not repo.base_dir(hub, p["id"]).exists()


async def test_the_server_changed_in_settings(signed_in, app, remotes, apis):
    forgejo(app)
    p = new_project(app)
    await link(signed_in, p)
    app.state.hub.settings.update("git", {"server": "github", "url": "", "token": "github_pat_x"})
    r = await signed_in.post(f"/hub/api/projects/{p['id']}/repo/sync", headers=ORIGIN)
    assert r.status_code == 409 and "is on https://forgejo.test" in r.json()["detail"]


async def test_a_person_reaches_no_repository(signed_in, app, remotes, apis):
    from .test_people import add_person, client_from, sign_in
    forgejo(app)
    p = new_project(app)
    await add_person(signed_in)
    async with client_from(app) as phone:
        assert (await sign_in(phone)).status_code == 200
        for method in ("GET", "PUT", "DELETE"):
            r = await phone.request(method, f"/hub/api/projects/{p['id']}/repo", json={"repo": "octo/game"},
                                    headers=ORIGIN)
            assert r.status_code == 403, method
        assert (await phone.post(f"/hub/api/projects/{p['id']}/repo/sync", headers=ORIGIN)).status_code == 403


async def test_slq_sync_is_gone(signed_in, app):
    r = await signed_in.get("/api/sync/projects")
    assert r.status_code == 410 and "git clone the project's repo" in r.json()["detail"]
    assert len(r.json()["detail"]) <= 60                       # what SLQSync.exe's window shows of it
    assert (await signed_in.get("/hub/api/sync-tokens")).status_code == 404


def test_names_from_git(tmp_path):
    assert repo._default_branch("ref: refs/heads/trunk\tHEAD\nabc\tHEAD\nabc\trefs/heads/trunk\n") == "trunk"
    assert repo._default_branch("abc\trefs/heads/master\n") == "master"
    assert repo._default_branch("") == "main"
    assert repo._name_status("M\0a.js\0D\0b c.txt\0") == [("M", "a.js"), ("D", "b c.txt")]
    msg = repo._message({"name": "Game"}, ["bash: npm test", "Chat: Cheaper beer"], [("M", "src/bar.js")])
    assert msg.splitlines()[0] == "Cheaper beer" and "- bash: npm test" in msg
    msg = repo._message({"name": "Game"}, [], [("M", f"f{i}.js") for i in range(5)])
    assert msg.splitlines()[0] == "Update f0.js, f1.js, f2.js and 2 more"
    assert json.loads(json.dumps(repo.load_state(type("H", (), {"env": type("E", (), {
        "git_dir": tmp_path})()})(), 1))) == {"known": {}, "status": {}}


def test_writing_into_the_folder_follows_no_link_and_leaves_its_changes(tmp_path):
    """Step 4 writes what the server brought - not through a link planted where the repository has a folder, not
    over a file the folder changed since step 1 saw it, and no link or submodule from the repository."""
    ws = tmp_path / "ws"
    folder = ws / "projects" / "1" / "F"
    folder.mkdir(parents=True)
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    for rel, text in (("a.txt", "repo a"), ("sub/x.txt", "repo x"), ("b.sh", "echo b"), ("link", "../../etc")):
        (tree / rel).write_text(text)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, folder / "sub")
    (folder / "a.txt").write_text("changed here")
    files = {"a.txt": (1, 1, 0o644)}                         # what step 1 saw - not what is there now
    fd = repo.open_folder(ws, ["projects", "1", "F"])
    try:
        out = repo.copy_out(fd, tree, [("M", "a.txt", "100644"), ("A", "sub/x.txt", "100644"),
                                       ("A", "b.sh", "100755"), ("A", "link", "120000")], files)
    finally:
        os.close(fd)
    assert (folder / "a.txt").read_text() == "changed here" and not list(outside.iterdir())
    assert (folder / "b.sh").read_text() == "echo b" and os.stat(folder / "b.sh").st_mode & 0o111
    assert not (folder / "link").exists() and set(out.left) == {"a.txt", "sub/x.txt", "link"} and out.written == 1
    assert files["b.sh"][2] == 0o755
