"""A project's history: the revision store (Files -> History, Restore). It was also the server side of SLQ Sync's
Windows client until 0.84.0, when a project's repository took that over (tests/test_repo.py)."""
from __future__ import annotations

import hashlib

import pytest

from app.projects import store, syncstore as s


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def project(app):
    s.reset_cache()
    p = store.create_project("Demo", sync_folder=".")           # synced as a whole, the way before 0.40.0
    wd = store.project_dir(app.state.hub.env.workspace_dir, p["id"])
    (wd / "src").mkdir(parents=True)
    (wd / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (wd / "README.md").write_text("readme", encoding="utf-8")
    (wd / "__pycache__").mkdir()
    (wd / "__pycache__" / "x.pyc").write_bytes(b"junk")
    (wd / "notes.tmp").write_text("temp")
    (wd / ".hub").mkdir()
    (wd / ".hub" / "outputs.txt").write_text("internal")
    return p["id"], wd


def test_paths_and_ignored_files():
    assert s.ignored("a/__pycache__/x.pyc") and s.ignored(".git/config") and s.ignored("x.tmp")
    assert s.ignored(".hub/attachments/a.png") and s.ignored("main.slqconflict-r3.py")
    assert not s.ignored("src/main.py") and not s.ignored("Makefile")
    assert s.clean_path(".\\src\\main.py") == "src/main.py"
    for bad in ("../x", "/etc/passwd", "C:/Windows/x", "a/../../x", ""):
        with pytest.raises(s.SyncError):
            s.clean_path(bad)


def test_snapshot_records_only_what_changed(project):
    pid, wd = project
    assert s.snapshot(pid, wd, message="first") == 1
    assert set(s.manifest(pid)) == {"src/main.py", "README.md"}
    assert s.snapshot(pid, wd) is None
    (wd / "src" / "main.py").write_text("print(2)\n", encoding="utf-8")
    (wd / "README.md").unlink()
    assert s.snapshot(pid, wd, message="edit") == 2
    assert s.manifest(pid, 2) == {"src/main.py": _sha(b"print(2)\n")}
    assert s.manifest(pid, 1)["README.md"] == _sha(b"readme")
    revs = s.revisions(pid)
    assert [r["rev"] for r in revs] == [2, 1] and revs[0]["fileCount"] == 2


def test_a_blob_is_only_served_to_its_own_project(project, app):
    pid, wd = project
    s.snapshot(pid, wd)
    assert s.blob(pid, _sha(b"readme")) == b"readme"
    other = store.create_project("Other")
    with pytest.raises(s.SyncError):
        s.blob(other["id"], _sha(b"readme"))


async def test_ui_history_and_restore(signed_in, app, project):
    pid, wd = project
    s.snapshot(pid, wd)
    (wd / "README.md").write_text("changed", encoding="utf-8")
    s.snapshot(pid, wd, message="change")
    hist = (await signed_in.get(f"/hub/api/projects/{pid}/history", params={"path": "README.md"})).json()
    assert [h["rev"] for h in hist] == [2, 1]
    old = await signed_in.get(f"/hub/api/projects/{pid}/rev-file", params={"path": "README.md", "rev": 1})
    assert old.json()["text"] == "readme"
    r = await signed_in.post(f"/hub/api/projects/{pid}/restore", json={"path": "README.md", "rev": 1})
    assert r.status_code == 200, r.text
    assert (wd / "README.md").read_text() == "readme" and s.head(pid) == 3


# ---------------------------------------------------------------- the real client

def test_writes_do_not_follow_symlinks_planted_by_the_sandbox(app, tmp_path):
    p = store.create_project("Planted")
    ws = app.state.hub.env.workspace_dir
    wd = store.Workdir(store.project_dir(ws, p["id"]), p["id"], ws)
    secret = tmp_path / "settings.json"
    secret.write_text("original")
    (wd.root / "notes.txt.hub-tmp").symlink_to(secret)          # the old temp-file trick
    wd.write("notes.txt", "hello")
    assert secret.read_text() == "original" and (wd.root / "notes.txt").read_text() == "hello"
    (wd.root / "link.txt").symlink_to(secret)                   # the target itself
    with pytest.raises(ValueError):
        wd.write("link.txt", "pwned")
    (wd.root / "outside").symlink_to(tmp_path)                  # a folder on the way
    with pytest.raises(ValueError):
        wd.write("outside/settings.json", "pwned")
    assert secret.read_text() == "original"


async def test_a_project_has_its_own_files_folder_and_hub(signed_in, app):
    """0.40.0: a new project's own files are in its synced folder - named from the project when not given, set for
    good (0.84.0: the folder a repository is cloned into) - and hub/ beside it holds the assistant's files, the
    attachments and uploads to no folder; the history keeps both."""
    s.reset_cache()
    ws = app.state.hub.env.workspace_dir
    r = await signed_in.post("/hub/api/projects", json={"name": "TwinCAT HUB Project"})
    p = r.json()
    assert r.status_code == 200 and p["settings"]["sync_folder"] == "TwinCAT-HUB-Project"
    top = store.project_dir(ws, p["id"])
    sf = top / "TwinCAT-HUB-Project"
    assert sf.is_dir() and (top / "hub").is_dir()
    for bad in ("hub", "HUB", "node_modules", "///"):
        r = await signed_in.post("/hub/api/projects", json={"name": "X", "sync_folder": bad})
        assert r.status_code == 400, (bad, r.text)
    assert (await signed_in.post("/hub/api/projects", json={"name": "X", "sync_folder": " My PLC "})).json()[
        "settings"]["sync_folder"] == "My-PLC"
    whole = (await signed_in.post("/hub/api/projects", json={"name": "Y", "sync_folder": "."})).json()
    assert store.sync_folder(whole) == "" and not (store.project_dir(ws, whole["id"]) / "hub").exists()
    # set for good: a settings change leaves it
    r = await signed_in.patch(f"/hub/api/projects/{p['id']}", json={"settings": {"sync_folder": "other", "level": "think"}})
    assert r.json()["settings"]["sync_folder"] == "TwinCAT-HUB-Project" and r.json()["settings"]["level"] == "think"

    (sf / "PLC").mkdir()
    (sf / "PLC" / "MAIN.TcPOU").write_text("main v1", encoding="utf-8")
    (top / "hub" / "attachments").mkdir()
    (top / "hub" / "NOTES.md").write_text("notes", encoding="utf-8")
    (top / "hub" / "attachments" / "screen.png").write_bytes(b"png")
    (top / "loose.txt").write_text("at the top", encoding="utf-8")
    assert set(s.manifest(p["id"])) == set()                  # (nothing checked in yet)
    store.project_workdir(ws, p["id"]).checkin("hub", "hub", "files")
    assert set(s.manifest(p["id"])) == {"TwinCAT-HUB-Project/PLC/MAIN.TcPOU", "hub/NOTES.md",
                                        "hub/attachments/screen.png", "loose.txt"}     # the history has it all
