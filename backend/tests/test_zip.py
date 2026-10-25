"""A folder, or an answer's files, as one ZIP (0.29.0 - Søren, chat 79: a browser game is four files and a data
folder, downloaded one by one): GET /hub/api/folders/{kind}/{id}/zip."""
from __future__ import annotations

import io
import re
import zipfile
from urllib.parse import unquote

from .test_agent import new_chat


def filename(r) -> str:
    cd = r.headers["content-disposition"]
    m = re.search(r"filename\*=utf-8''([^;]+)", cd)
    return unquote(m.group(1)) if m else re.search(r'filename="([^"]+)"', cd).group(1)


def names(r) -> list[str]:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    return sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist())


async def test_a_chat_folder_and_an_answers_files(signed_in, app, tmp_path):
    c = await new_chat(signed_in)
    await signed_in.patch(f"/hub/api/chats/{c['id']}", json={"title": "Snow: the game?"})
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    for rel, text in (("index.html", "<p>hej</p>"), ("game.js", "let x;"), ("data/level.json", "{}"),
                      ("notes/NOTES.md", "# notes"), (".hub/attachments/photo.png", "png"),
                      ("node_modules/pkg/index.js", "x"), ("old.bak", "x"), ("deep/a/b/c.txt", "c")):
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text)
    (tmp_path / "secret.txt").write_text("no")
    (folder / "link.txt").symlink_to(tmp_path / "secret.txt")              # a link is not followed out
    base = f"/hub/api/folders/chat/{c['id']}/zip"
    r = await signed_in.get(base)
    assert names(r) == ["data/level.json", "deep/a/b/c.txt", "game.js", "index.html", "notes/NOTES.md"]
    assert filename(r) == "Snow- the game.zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.read("data/level.json") == b"{}"
    # the files card: what one answer made, named from the folder that holds them all
    r = await signed_in.get(base, params=[("path", "index.html"), ("path", "game.js"), ("path", "data/level.json")])
    assert names(r) == ["data/level.json", "game.js", "index.html"]
    r = await signed_in.get(base, params=[("path", "deep/a/b/c.txt"), ("path", "deep/a/x.txt")])
    assert names(r) == ["c.txt"]                              # one moved or deleted since the answer: left out
    r = await signed_in.get(base, params={"path": "deep/a/x.txt"})
    assert r.status_code == 404                                               # none there
    (folder / "deep" / "a" / "x.txt").write_text("x")
    r = await signed_in.get(base, params=[("path", "deep/a/b/c.txt"), ("path", "deep/a/x.txt")])
    assert names(r) == ["b/c.txt", "x.txt"]
    r = await signed_in.get(base, params=[("path", "deep"), ("path", "old.bak")])   # a folder, and a file by name
    assert names(r) == ["deep/a/b/c.txt", "deep/a/x.txt", "old.bak"]
    r = await signed_in.get(base, params={"path": "../../etc/passwd"})
    assert r.status_code == 400
    r = await signed_in.get(base, params={"path": "data", "name": "level/data"})
    assert names(r) == ["data/level.json"] and filename(r) == "level-data.zip"
    assert not list((app.state.hub.env.data_dir / "tmp").glob("folder-*.zip"))    # each deleted once sent


async def test_a_project_folder_is_named_after_the_project(signed_in, app):
    p = (await signed_in.post("/hub/api/projects", json={"name": "Pumpestyring"})).json()
    r = await signed_in.get(f"/hub/api/folders/project/{p['id']}/zip")
    assert r.status_code == 404 and "no files" in r.json()["detail"]               # an empty folder
    await signed_in.put(f"/hub/api/folders/project/{p['id']}/file", json={"path": "src/main.st", "text": "x"})
    r = await signed_in.get(f"/hub/api/folders/project/{p['id']}/zip")
    assert names(r) == ["src/main.st"] and filename(r) == "Pumpestyring.zip"
    c = await new_chat(signed_in, project_id=p["id"])                          # a project's chat: the project's folder
    r = await signed_in.get(f"/hub/api/folders/chat/{c['id']}/zip")
    assert names(r) == ["src/main.st"] and filename(r) == "Pumpestyring.zip"


async def test_asked_first_and_its_refusal_is_a_message(signed_in, app):
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    (folder / "game").mkdir(parents=True)
    (folder / "game" / "index.html").write_text("<p>hej</p>")
    r = await signed_in.get(f"/hub/api/folders/chat/{c['id']}/zip", params={"path": "game", "check": "true"})
    assert r.status_code == 200 and r.json() == {"files": 1, "bytes": 10, "name": "New chat.zip"}
    r = await signed_in.get(f"/hub/api/folders/chat/{c['id']}/zip", params={"path": "nope", "check": "true"})
    assert r.status_code == 404


def test_what_is_swapped_in_after_the_listing_is_not_read(app, tmp_path):
    """Sandboxed code (the same uid, the same folder) may put a link in place of a listed file or folder before the
    ZIP is written - to the Hub's database, to /dev/urandom - or a FIFO: none of it is followed or read, and a
    file that grew comes no longer than it was listed."""
    import os

    from app.projects import routes, store
    ws = app.state.hub.env.workspace_dir
    wd = store.Workdir(store.chat_dir(ws, 1), None, ws)
    for rel in ("a.txt", "b.txt", "c.txt", "d/e.txt", "f.txt"):
        (wd.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (wd.root / rel).write_text(rel)
    files = wd.zip_files()
    assert [n for _, n, _ in files] == ["a.txt", "b.txt", "c.txt", "d/e.txt", "f.txt"]
    secret = tmp_path / "hub.db"
    secret.write_text("the Hub's database")
    (wd.root / "a.txt").unlink()
    (wd.root / "a.txt").symlink_to(secret)                                   # a file swapped for a link
    (wd.root / "d" / "e.txt").rename(tmp_path / "e.txt")
    (wd.root / "d").rmdir()
    (wd.root / "d").symlink_to(tmp_path)                                     # a folder swapped for one
    (wd.root / "c.txt").unlink()
    os.mkfifo(wd.root / "c.txt")                                             # a FIFO would hang a reader
    (wd.root / "f.txt").write_text("f.txt and much more")                    # grew after the listing
    out = tmp_path / "out.zip"
    routes._write_zip(str(out), wd, files)
    z = zipfile.ZipFile(out)
    assert sorted(z.namelist()) == ["b.txt", "f.txt"] and z.read("f.txt") == b"f.txt"
    other = store.Workdir(store.chat_dir(ws, 2), None, ws)               # the folder itself swapped for another's
    (other.root / "b.txt").write_text("chat 2's")
    files = wd.zip_files()
    wd.root.rename(tmp_path / "chat1")
    wd.root.symlink_to(other.root)
    routes._write_zip(str(out), wd, files)
    assert zipfile.ZipFile(out).namelist() == []
