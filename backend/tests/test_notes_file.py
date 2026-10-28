"""NOTES.md (0.31.0) and moving a chat into a project. Chat 126: 54 steps on a file format without a notes file,
although the prompt asked for one - the Hub now writes the chat's notes into NOTES.md (condensed, continued in a
new chat, corrected in the menu) unless someone else keeps the file, reminds the model in long work and tells it to
read the file first; and a plain chat can become one of a project's, its files moved into the project."""
from __future__ import annotations

import json

from pathlib import Path

import pytest

from app.agent import notes_file as N
from app.chats import store as chats
from app.projects import store as projects
from app.projects import syncstore

from .test_agent import finish, last_user, new_chat, say
from .test_long_chats import notes_writer


def workdir(app, chat_id: int):
    return app.state.hub.extras["agent"].workdir(chats.get_chat(chat_id))


class _Wd:                                    # enough of a Workdir for notes_file.write
    def __init__(self, root):
        self.root = root

    def checkin(self, *a):
        return None


def test_the_hub_keeps_to_its_own_copy(tmp_path):
    wd = _Wd(tmp_path)
    assert N.write(wd, 7, "Spillet", "## Goal\n- a game", "the chat was condensed") == "NOTES.md"
    text = (tmp_path / "NOTES.md").read_text()
    assert text.startswith("<!-- SLQ Hub notes ") and "# Notes: Spillet\n\n## Goal\n- a game\n" in text
    assert N.owner(tmp_path / "NOTES.md") == 7
    assert N.write(wd, 7, "Spillet", "## Goal\n- a better game", "again")               # its own: written again
    assert "a better game" in (tmp_path / "NOTES.md").read_text()
    # in a project: another chat leaves it; the chat that continues it writes over it
    assert N.write(wd, 8, "Other", "## Goal\n- other work", "x") is None
    assert N.write(wd, 9, "Spillet (2)", "## Goal\n- go on", "x", owners=(7,)) == "NOTES.md"
    assert N.owner(tmp_path / "NOTES.md") == 9
    (tmp_path / "NOTES.md").write_text((tmp_path / "NOTES.md").read_text() + "- checked: the header is 1132 bytes\n")
    assert N.owner(tmp_path / "NOTES.md") is None                                       # changed: someone else's
    assert N.write(wd, 9, "Spillet", "## Goal\n- overwrite", "again") is None
    assert "1132 bytes" in (tmp_path / "NOTES.md").read_text()
    # the model's own notes.md, in any case
    (tmp_path / "NOTES.md").unlink()
    (tmp_path / "notes.md").write_text("mine\n")
    assert N.find(tmp_path).name == "notes.md" and N.write(wd, 9, "x", "y", "z") is None
    assert N.stamp(tmp_path)[0] == "notes.md"
    # a link in the file's place: replaced, never written through
    (tmp_path / "notes.md").unlink()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("keep")
    (tmp_path / "NOTES.md").symlink_to(outside)
    assert N.find(tmp_path) is None and N.write(wd, 9, "x", "## Goal", "z") == "NOTES.md"
    assert outside.read_text() == "keep" and not (tmp_path / "NOTES.md").is_symlink()
    # notes cleared: the chat's own copy goes, someone else's stays
    assert N.write(wd, 9, "x", "   ", "z") is None and not (tmp_path / "NOTES.md").exists()
    (tmp_path / "NOTES.md").write_text("theirs\n")
    assert N.write(wd, 9, "x", "", "z") is None and (tmp_path / "NOTES.md").read_text() == "theirs\n"
    # a FIFO in its place does not hang the Hub; a working folder that is a link is left alone
    (tmp_path / "NOTES.md").unlink()
    import os
    os.mkfifo(tmp_path / "NOTES.md")
    assert N.owner(tmp_path / "NOTES.md") is None
    (tmp_path / "NOTES.md").unlink()
    link = tmp_path.parent / f"{tmp_path.name}-link"
    link.symlink_to(tmp_path)
    assert N.write(_Wd(link), 9, "x", "## Goal", "z") is None and not (tmp_path / "NOTES.md").exists()


async def test_continue_in_a_new_chat_writes_notes_md(signed_in, app, fakes):
    c = await new_chat(signed_in)
    chats.update_chat(c["id"], title="Platform game")
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Make the jump height 140 pixels."}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "Done: jump height 140."}])
    responder, _ = notes_writer("## Goal\n- a platform game\n## Files\n- game.html")
    fakes.llama["responder"] = responder
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/handover")
    new = r.json()["chat"]
    await finish(app, r.json()["run_id"])
    notes = workdir(app, new["id"]).root / "NOTES.md"
    assert N.owner(notes) == new["id"] and "# Notes: Platform game (2)" in notes.read_text()
    assert "## Files\n- game.html" in notes.read_text() and 'continued from the chat "Platform game"' in notes.read_text()
    # the next turn's system prompt points at it
    fakes.llama["responder"] = None
    fakes.llama["replies"] = [{"content": "Reading it."}]
    await finish(app, await say(signed_in, new["id"], "Go on"))
    system = fakes.llama["requests"][-1]["messages"][0]["content"]
    assert "- NOTES.md in the working folder holds the notes of this work, kept across chats: read it before you " \
           "start" in system
    assert "keep what you find in NOTES.md in the working folder as you go" in system
    # notes the model keeps itself are copied and left alone; corrected notes go nowhere then
    notes.write_text("# CM98\n- checked: 215 bytes a record\n")
    chats.add_message(new["id"], "user", [{"type": "text", "text": "More"}])
    chats.add_message(new["id"], "assistant", [{"type": "text", "text": "Yes."}])
    fakes.llama["responder"] = responder
    r = await signed_in.post(f"/hub/api/chats/{new['id']}/handover")
    third = r.json()["chat"]
    await finish(app, r.json()["run_id"])
    assert (workdir(app, third["id"]).root / "NOTES.md").read_text() == "# CM98\n- checked: 215 bytes a record\n"
    assert (await signed_in.put(f"/hub/api/chats/{third['id']}/notes", json={"text": "## Goal\n- new"})).status_code == 200
    assert (workdir(app, third["id"]).root / "NOTES.md").read_text().startswith("# CM98")


async def test_condensing_and_corrected_notes_write_it_and_the_setting_turns_it_off(signed_in, app, fakes, monkeypatch):
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 8000)
    c = await new_chat(signed_in)
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Compare the two cars"}])
    for i in range(40):
        chats.add_message(c["id"], "assistant", [{"type": "text", "text": f"Fetching page {i}."},
                                                 {"type": "tool_call", "id": f"c{i}", "name": "web_fetch",
                                                  "arguments": json.dumps({"url": f"https://cars.test/{i}"})}])
        chats.add_message(c["id"], "tool", [{"type": "tool_result", "tool_call_id": f"c{i}", "name": "web_fetch",
                                             "text": f"page {i} " + "z" * 4000}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "The last answer before the new question."}])

    def responder(body):
        if "Condense the conversation" in last_user(body):
            return {"content": "## Goal\n- compare two cars"}
        return {"content": "Fine."}
    fakes.llama["responder"] = responder
    await finish(app, await say(signed_in, c["id"], "Continue"))
    notes = workdir(app, c["id"]).root / "NOTES.md"
    assert "## Goal\n- compare two cars" in notes.read_text() and "(the chat was condensed)" in notes.read_text()
    r = await signed_in.put(f"/hub/api/chats/{c['id']}/notes", json={"text": "## Goal\n- compare three cars"})
    assert r.status_code == 200 and "three cars" in notes.read_text() and "corrected in the chat" in notes.read_text()
    hub.settings.update("agent", {"notes_file": False})
    notes.unlink()
    await signed_in.put(f"/hub/api/chats/{c['id']}/notes", json={"text": "## Goal\n- four"})
    assert not notes.exists()


def making(n: int, then: list[dict] | None = None):
    """A turn that writes a file each step for n steps (then the replies given), then answers."""
    steps = [{"tool_calls": [{"name": "write_file", "arguments": {"path": f"parts/p{i}.txt", "content": str(i)}}]}
             for i in range(n)]
    return steps + (then or []) + [{"content": "Done."}]


def reminders(bodies: list[dict]) -> list[str]:
    return [m["content"] for b in bodies for m in b["messages"]
            if m["role"] == "user" and "has not been written in this turn" in str(m["content"])]


async def test_long_work_is_reminded_to_keep_notes_md(signed_in, app, fakes):
    fakes.llama["replies"] = making(17) + [{"content": "Title"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Lav mange dele"))
    assert run.status == "done", run.events[-1]
    seen = reminders(fakes.llama["requests"][-2:])
    assert seen and seen[-1].startswith("(SLQ Hub: this work has gone on for 15 steps, and NOTES.md in the working "
                                        "folder has not been written in this turn.")
    assert len({r for b in fakes.llama["requests"] for r in reminders([b])}) == 1          # once in 18 steps
    assert [e["data"] for e in run.events if e["type"] == "notes_reminder"] == [{"step": 15, "name": "NOTES.md"}]
    notices = [b["text"] for m in chats.messages(c["id"]) for b in m["content"] if b.get("type") == "notice"]
    assert notices == ["It was reminded to write what it found in NOTES.md (step 15)."]
    # the model writes it before then: no reminder
    fakes.llama["requests"].clear()
    notes = {"tool_calls": [{"name": "write_file", "arguments": {"path": "NOTES.md", "content": "- checked: x"}}]}
    fakes.llama["replies"] = [notes] + making(17)
    run = await finish(app, await say(signed_in, c["id"], "Igen"))
    assert not reminders(fakes.llama["requests"]) and not any(e["type"] == "notes_reminder" for e in run.events)
    # a test run measures the model as it is, and the setting turns it off
    for options, settings in (({"test": True}, {}), ({}, {"notes_file": False})):
        app.state.hub.settings.update("agent", {"notes_file": True, **settings})
        fakes.llama["requests"].clear()
        fakes.llama["replies"] = making(17)
        d = await new_chat(signed_in)
        chats.add_message(d["id"], "user", [{"type": "text", "text": "Mange dele"}])
        run = app.state.hub.extras["agent"].start(d["id"], options)
        await finish(app, run.id)
        assert run.status == "done" and not reminders(fakes.llama["requests"])


async def test_moving_a_chat_into_a_new_project(signed_in, app, fakes):
    hub = app.state.hub
    c = await new_chat(signed_in)
    chats.update_chat(c["id"], title="Kan du gøre det til et browserspil? (5)")
    chats.add_message(c["id"], "user", [{"type": "image", "sha": "ab" * 32, "name": "skud.png",
                                         "path": ".hub/attachments/skud.png"}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "I see the game."}])
    old = workdir(app, c["id"])
    for rel, text in (("index.html", "<h1>CM98</h1>"), ("data/players.json", "[]"), (".hub/attachments/skud.png", "png"),
                      (".hub/outputs/c1.txt", "long"), (".hub/skills/x/SKILL.md", "made again"), ("NOTES.md", "- mine")):
        (old.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (old.root / rel).write_text(text)
    (old.root / "latest.json").symlink_to("data/players.json")              # links stay links, never followed
    (old.root / "run.sh").write_text("#!/bin/sh\n")
    (old.root / "run.sh").chmod(0o755)
    (old.root / "output").mkdir()                                             # an empty folder comes too
    import os
    os.mkfifo(old.root / "pipe")                                              # not a file: stays behind
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"new_project": "CM98"})
    assert r.status_code == 200, r.text
    body = r.json()
    p = body["project"]
    assert p["name"] == "CM98" and body["chat"]["project_id"] == p["id"] and body["copied"] == 6
    assert body["chat"]["settings"]["moved_from"] == old.sandbox_path and body["left"] == ["pipe"]
    top = projects.project_dir(hub.env.workspace_dir, p["id"])
    dst = top / "hub"              # a new project syncs its folder CM98/ (0.40.0): the chat's files go to hub/ beside it
    assert p["settings"]["sync_folder"] == "CM98" and (top / "CM98").is_dir() and not any((top / "CM98").iterdir())
    assert (dst / "index.html").read_text() == "<h1>CM98</h1>" and (dst / "data/players.json").exists()
    assert (dst / "attachments/skud.png").exists() and (top / ".hub/outputs/c1.txt").exists()
    assert not (top / ".hub/skills").exists() and not (dst / "pipe").exists() and not (dst / ".hub").exists()
    assert (dst / "latest.json").is_symlink() and os.readlink(dst / "latest.json") == "data/players.json"
    assert (dst / "run.sh").stat().st_mode & 0o777 == 0o755 and (dst / "output").is_dir()
    assert not old.root.exists()                                              # moved, not copied
    trashed = Path(body["old_folder"])                                        # the old folder is kept in the trash
    assert trashed.parent == hub.env.data_dir / "trash" and (trashed / "index.html").exists()
    assert {"hub/index.html", "hub/data/players.json", "hub/NOTES.md"} <= set(syncstore.manifest(p["id"]))
    assert chats.messages(c["id"])[0]["content"][0]["path"] == "hub/attachments/skud.png"     # the chat points there
    # the next turn works in the project, and is told where the old paths went
    fakes.llama["replies"] = [{"content": "Here."}]
    await finish(app, await say(signed_in, c["id"], "Hvor er filerne?"))
    system = fakes.llama["requests"][-1]["messages"][0]["content"]
    assert (f'You work in the files of the project "CM98" ({hub.env.sandbox_workspace}/projects/{p["id"]}), in two '
            "folders:\n- CM98/ is the user's.") in system
    assert (f"This chat was moved into the project from its own folder ({old.sandbox_path}): the files the "
            "conversation above shows there are in hub/ now, its attachments in hub/attachments/.") in system
    assert "- hub/NOTES.md in the working folder holds the notes of this work" in system
    assert "Attachments the user sends are copied to hub/attachments/ in the working folder." in system
    # once in a project: not again
    assert (await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"new_project": "Again"})).status_code == 409


async def test_moving_into_a_project_overwrites_nothing(signed_in, app, fakes):
    hub = app.state.hub
    p = (await signed_in.post("/hub/api/projects", json={"name": "CM98", "sync_folder": "."})).json()
    dst = projects.project_dir(hub.env.workspace_dir, p["id"])
    (dst / "index.html").write_text("the project's own")
    (dst / "same.txt").write_text("same")
    c = await new_chat(signed_in)
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Hej"}])
    old = workdir(app, c["id"])
    (old.root / "index.html").write_text("the chat's")
    (old.root / "same.txt").write_text("same")
    (old.root / "new.txt").write_text("new")
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"project_id": p["id"]})
    assert r.status_code == 409 and "index.html" in r.json()["detail"] and "same.txt" not in r.json()["detail"]
    assert (dst / "index.html").read_text() == "the project's own" and not (dst / "new.txt").exists()   # nothing moved
    assert chats.get_chat(c["id"])["project_id"] is None and (old.root / "new.txt").exists()
    (old.root / "index.html").unlink()
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"project_id": p["id"]})
    assert r.status_code == 200 and r.json()["copied"] == 1 and r.json()["same"] == 1
    assert (dst / "new.txt").read_text() == "new"


@pytest.mark.parametrize("folder, att", [(".", ".hub/attachments"), (None, "hub/attachments")])
async def test_an_attachment_by_the_same_name_is_renamed_and_the_chat_points_at_it(signed_in, app, fakes, folder, att):
    hub = app.state.hub
    p = (await signed_in.post("/hub/api/projects", json={"name": "Garage", "sync_folder": folder})).json()
    dst = projects.project_dir(hub.env.workspace_dir, p["id"])
    (dst / att).mkdir(parents=True)
    (dst / att / "image.png").write_text("the project's picture")
    c = await new_chat(signed_in)
    block = {"type": "image", "sha": "cd" * 32, "name": "image.png", "path": ".hub/attachments/image.png"}
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Se her"}, block])
    old = workdir(app, c["id"])
    (old.root / ".hub/attachments").mkdir(parents=True)
    (old.root / ".hub/attachments/image.png").write_text("the chat's picture")
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"project_id": p["id"]})
    assert r.status_code == 200, r.text
    import hashlib
    alt = f"{att}/{hashlib.sha256(b'the chat' + chr(39).encode() + b's picture').hexdigest()[:8]}-image.png"
    assert (dst / alt).read_text() == "the chat's picture"
    assert (dst / att / "image.png").read_text() == "the project's picture"
    moved = chats.messages(c["id"])[0]["content"][1]
    assert moved["path"] == alt and moved["sha"] == "cd" * 32


async def test_what_cannot_be_moved(signed_in, app, fakes):
    hub = app.state.hub
    c = await new_chat(signed_in)
    assert (await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={})).status_code == 400
    assert (await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"project_id": 9999})).status_code == 404
    p = (await signed_in.post("/hub/api/projects", json={"name": "Old", "sync_folder": "."})).json()
    assert (await signed_in.patch(f"/hub/api/projects/{p['id']}", json={"archived": True})).status_code == 200
    assert (await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"project_id": p["id"]})).status_code == 409
    # a link where a folder belongs in the project: in the way, not followed
    q = (await signed_in.post("/hub/api/projects", json={"name": "Links", "sync_folder": "."})).json()
    dst = projects.project_dir(hub.env.workspace_dir, q["id"])
    (dst / "game").symlink_to("/tmp")
    (workdir(app, c["id"]).root / "game").mkdir()
    (workdir(app, c["id"]).root / "game/x.js").write_text("x")
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"project_id": q["id"]})
    assert r.status_code == 409 and "game/x.js" in r.json()["detail"]
    # working, or a test case's chat
    fakes.llama["replies"] = [{"content": "slow", "delay": 0.5}]
    run_id = await say(signed_in, c["id"], "Go on")
    assert (await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"new_project": "X"})).status_code == 409
    await finish(app, run_id)
    t = chats.create_chat(None, "case", test_run="test_1")
    assert (await signed_in.post(f"/hub/api/chats/{t['id']}/move", json={"new_project": "X"})).status_code == 409
    assert [x["name"] for x in projects.list_projects()].count("X") == 0           # nothing made for nothing


async def test_nothing_touches_a_chat_while_it_moves(signed_in, app, fakes):
    agent = app.state.hub.extras["agent"]
    c = await new_chat(signed_in)
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Hej"}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "Hej."}])
    agent.moving.add(c["id"])
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/messages", json={"text": "Hej igen"})
    assert r.status_code == 409 and "being moved into a project" in r.json()["detail"]
    assert len(chats.messages(c["id"])) == 2                                   # nothing stored that waits forever
    for method, url, body in (("post", "retry", {}), ("post", "edit", {"message_id": 1, "text": "x"}),
                              ("post", "continue", {}), ("post", "handover", None), ("put", "notes", {"text": "x"}),
                              ("post", "move", {"new_project": "Y"})):
        r = await getattr(signed_in, method)(f"/hub/api/chats/{c['id']}/{url}", **({"json": body} if body is not None else {}))
        assert r.status_code == 409, (url, r.text)
    assert len(chats.messages(c["id"])) == 2                                   # retry and edit deleted nothing
    assert (await signed_in.get(f"/hub/api/folders/chat/{c['id']}/tree")).status_code == 409    # the Files panel too
    agent.moving.discard(c["id"])


async def test_a_failed_copy_puts_everything_back(signed_in, app, fakes, monkeypatch):
    from app.projects import move
    hub = app.state.hub
    c = await new_chat(signed_in)
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Hej"}])
    (workdir(app, c["id"]).root / "game.js").write_text("x")

    def broken(*a, **kw):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(move, "apply", broken)
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"new_project": "Fails"})
    assert r.status_code == 507 and "No space left" in r.json()["detail"] and "stayed where it was" in r.json()["detail"]
    assert (hub.env.workspace_dir / "chats" / str(c["id"]) / "game.js").read_text() == "x"    # back where it was
    assert chats.get_chat(c["id"])["project_id"] is None
    assert "Fails" not in [x["name"] for x in projects.list_projects()]                       # the new project gone
    assert not [x for x in (hub.env.workspace_dir / "chats").iterdir() if x.name.startswith(".moving-")]


async def test_names_the_history_cannot_hold_and_names_alike_but_for_case(signed_in, app, fakes):
    import os
    hub = app.state.hub
    p = (await signed_in.post("/hub/api/projects", json={"name": "Case", "sync_folder": "."})).json()
    (projects.project_dir(hub.env.workspace_dir, p["id"]) / "Readme.md").write_text("theirs")
    c = await new_chat(signed_in)
    root = workdir(app, c["id"]).root
    (root / "README.md").write_text("mine")
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"project_id": p["id"]})
    assert r.status_code == 409 and "README.md" in r.json()["detail"]          # one file on a Windows PC
    (root / "README.md").unlink()
    fd = os.open(bytes(root) + b"/spiller\xe6.db", os.O_WRONLY | os.O_CREAT, 0o644)       # a Latin-1 name
    os.close(fd)
    r = await signed_in.post(f"/hub/api/chats/{c['id']}/move", json={"project_id": p["id"]})
    assert r.status_code == 409 and "not UTF-8" in r.json()["detail"]
    assert chats.get_chat(c["id"])["project_id"] is None and any(root.iterdir())


def test_a_name_taken_meanwhile_is_not_replaced(tmp_path):
    from app.projects import move
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "a").mkdir(parents=True)
    dst.mkdir()
    (src / "a" / "one.txt").write_text("1")
    (src / "two.txt").write_text("2")
    pl = move.plan(src, dst)
    assert [e.to for e in pl.make] == ["a", "two.txt", "a/one.txt"]             # the folder first
    (dst / "two.txt").write_text("written meanwhile by someone else")
    import pytest
    with pytest.raises(FileExistsError):
        move.apply(src, dst, pl.make)
    assert (dst / "two.txt").read_text() == "written meanwhile by someone else"
    assert not (dst / "a").exists()                                            # what the move made is gone again


async def test_condensing_at_the_start_is_not_the_model_keeping_notes(signed_in, app, fakes, monkeypatch):
    hub = app.state.hub
    monkeypatch.setattr(hub.catalog, "context_per_request", lambda model_id: 8000)
    c = await new_chat(signed_in)
    chats.add_message(c["id"], "user", [{"type": "text", "text": "Compare the two cars"}])
    for i in range(40):
        chats.add_message(c["id"], "assistant", [{"type": "text", "text": f"Fetching page {i}."},
                                                 {"type": "tool_call", "id": f"c{i}", "name": "web_fetch",
                                                  "arguments": json.dumps({"url": f"https://cars.test/{i}"})}])
        chats.add_message(c["id"], "tool", [{"type": "tool_result", "tool_call_id": f"c{i}", "name": "web_fetch",
                                             "text": f"page {i} " + "z" * 4000}])
    chats.add_message(c["id"], "assistant", [{"type": "text", "text": "The last answer before the new question."}])
    steps, seen = making(17), []

    def responder(body):
        if "Condense the conversation" in last_user(body):
            return {"content": "## Goal\n- compare two cars"}
        seen.append(body)
        return steps[min(len(seen) - 1, len(steps) - 1)]
    fakes.llama["responder"] = responder
    run = await finish(app, await say(signed_in, c["id"], "Write the parts"))
    assert run.status == "done", run.events[-1]
    assert "(the chat was condensed)" in (workdir(app, c["id"]).root / "NOTES.md").read_text()
    assert [e["data"]["step"] for e in run.events if e["type"] == "notes_reminder"] == [15]


async def test_in_a_project_that_syncs_one_folder_the_assistant_keeps_to_hub(signed_in, app, fakes):
    """0.40.0: the prompt names both folders, the attachments and the notes go to hub/, not to the PC's folder."""
    from .mocks import _png
    p = (await signed_in.post("/hub/api/projects", json={"name": "Garage"})).json()
    top = projects.project_dir(app.state.hub.env.workspace_dir, p["id"])
    up = (await signed_in.post("/hub/api/uploads", files={"file": ("photo.png", _png((20, 10)), "image/png")})).json()
    fakes.llama["replies"] = [{"content": "A red rectangle."}, {"content": "Photo"}]
    c = await new_chat(signed_in, project_id=p["id"])
    await finish(app, await say(signed_in, c["id"], "What is this?", attachments=[up["sha"]]))
    assert (top / "hub" / "attachments" / "photo.png").read_bytes() == _png((20, 10))
    assert not (top / ".hub" / "attachments").exists() and not any((top / "Garage").iterdir())
    req = fakes.llama["requests"][0]["messages"]
    assert "hub/attachments/photo.png" in req[-1]["content"][2]["text"]
    system = req[0]["content"]
    assert "in two folders:\n- Garage/ is the user's. SLQ Sync keeps it the same as a folder on their PC" in system
    assert "- hub/ is yours and never leaves the Hub: your notes (hub/NOTES.md)" in system
    r = await signed_in.put(f"/hub/api/chats/{c['id']}/notes", json={"text": "## Goal\n- tidy the garage"})
    assert r.status_code == 200 and "tidy the garage" in (top / "hub" / "NOTES.md").read_text()
    assert not (top / "NOTES.md").exists() and N.find(top / "hub") == top / "hub" / "NOTES.md"
