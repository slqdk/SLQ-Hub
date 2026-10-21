"""Project sync: the revision store, the /api/sync protocol (the same as SLQ
Companion's, so SLQSync.exe works unchanged), and - when the client's source
is next to this repo - a round trip with the real client against a live
server."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

from app import auth
from app.projects import store, syncstore as s

CLIENT = Path(os.environ.get("SLQ_SYNC_CLIENT", "/home/claude/slq-companion/tools/slq-sync/slq_sync.py"))


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def project(app):
    s.reset_cache()
    p = store.create_project("Demo")
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
    ch = s.changes_since(pid, 1)
    assert ch["head"] == 2 and ch["changes"] == {"src/main.py": _sha(b"print(2)\n"), "README.md": None}
    assert s.manifest(pid, 1)["README.md"] == _sha(b"readme")
    revs = s.revisions(pid)
    assert [r["rev"] for r in revs] == [2, 1] and revs[0]["fileCount"] == 2
    with pytest.raises(s.SyncError):
        s.changes_since(pid, 9)


def test_checkin_accepts_and_detects_conflicts(project):
    pid, wd = project
    s.snapshot(pid, wd)
    new = b"print('from the PC')\n"
    res = s.checkin(pid, wd, [
        {"path": "src/main.py", "baseSha": _sha(b"print(1)\n"), "sha": _sha(new), "content": new},
        {"path": "src/new.py", "baseSha": None, "sha": _sha(b"x = 1\n"), "content": b"x = 1\n"},
        {"path": "build/out.tmp", "baseSha": None, "sha": _sha(b"t"), "content": b"t"},   # ignored
    ], author="soren", origin="DESKTOP")
    assert res["rev"] == 2 and res["accepted"] == ["src/main.py", "src/new.py"]
    assert (wd / "src" / "main.py").read_bytes() == new and not (wd / "build").exists()
    (wd / "src" / "main.py").write_text("print('by the Hub')\n", encoding="utf-8")
    stale = b"print('stale')\n"
    res = s.checkin(pid, wd, [{"path": "src/main.py", "baseSha": _sha(new), "sha": _sha(stale), "content": stale}],
                    author="soren", origin="PC")
    assert res["accepted"] == [] and res["conflicts"][0]["serverSha"] == _sha(b"print('by the Hub')\n")
    assert res["head"] == 3
    with pytest.raises(s.SyncError):
        s.checkin(pid, wd, [{"path": "../evil", "baseSha": None, "sha": _sha(b"x"), "content": b"x"}],
                  author="a", origin="b")
    with pytest.raises(s.SyncError):
        s.checkin(pid, wd, [{"path": "a.txt", "baseSha": None, "sha": _sha(b"x"), "content": b"y"}],
                  author="a", origin="b")


def test_a_blob_is_only_served_to_its_own_project(project, app):
    pid, wd = project
    s.snapshot(pid, wd)
    assert s.blob(pid, _sha(b"readme")) == b"readme"
    other = store.create_project("Other")
    with pytest.raises(s.SyncError):
        s.blob(other["id"], _sha(b"readme"))


async def test_device_tokens_reach_sync_and_nothing_else(signed_in, app, project):
    pid, wd = project
    tok = (await signed_in.post("/hub/api/sync-tokens", json={"name": "Work PC"})).json()
    assert tok["token"].startswith("slqsync_")
    assert [t["name"] for t in (await signed_in.get("/hub/api/sync-tokens")).json()] == ["Work PC"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub") as dev:
        h = {"Authorization": f"Bearer {tok['token']}"}
        assert (await dev.get("/api/sync/whoami", headers=h)).json()["user"] == "soren"
        head = (await dev.get(f"/api/sync/projects/{pid}/head", headers=h)).json()
        assert head["head"] == 1 and set(head["files"]) == {"src/main.py", "README.md"}
        assert [p["id"] for p in (await dev.get("/api/sync/projects", headers=h)).json()] == [pid]
        assert (await dev.get("/hub/api/projects", headers=h)).status_code == 401       # nothing else
        assert (await dev.get("/api/tags", headers=h)).status_code == 401               # not the model API
        assert (await dev.get("/api/sync/whoami", headers={"Authorization": "Bearer slqsync_nope"})).status_code == 401
        body = {"origin": "PC", "files": [{"path": "README.md", "baseSha": _sha(b"readme"), "sha": _sha(b"v2"),
                                           "content": base64.b64encode(b"v2").decode()}]}
        r = (await dev.post(f"/api/sync/projects/{pid}/checkin", json=body, headers=h)).json()
        assert r["accepted"] == ["README.md"] and (wd / "README.md").read_text() == "v2"
        blob = await dev.get(f"/api/sync/projects/{pid}/blob/{_sha(b'v2')}", headers=h)
        assert blob.content == b"v2"
        revs = (await dev.get(f"/api/sync/projects/{pid}/revisions", headers=h)).json()["revisions"]
        assert revs[0]["author"] == "soren" and revs[0]["origin"] == "PC"
        # Revoked: the device is out.
        assert (await signed_in.delete(f"/hub/api/sync-tokens/{tok['id']}")).status_code == 200
        assert (await dev.get("/api/sync/whoami", headers=h)).status_code == 401


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

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live(hub_env, fakes, monkeypatch):
    import uvicorn
    from app.main import create_app
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    application = create_app(hub_env, startup=False)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", application
    server.should_exit = True
    t.join(timeout=5)


@pytest.mark.skipif(not CLIENT.is_file(), reason="the SLQ sync client source is not next to this repo")
def test_the_real_client_round_trip(live, tmp_path):
    url, application = live
    s.reset_cache()
    p = store.create_project("Conveyor")
    srv = store.project_dir(application.state.hub.env.workspace_dir, p["id"])
    (srv / "src").mkdir(parents=True)
    (srv / "src" / "main.py").write_text("v1", encoding="utf-8")
    (srv / "plan.md").write_text("plan", encoding="utf-8")
    token = auth.create_sync_token(1, "Work PC")["token"]     # user 1: the admin create_app made

    spec = importlib.util.spec_from_file_location("slq_sync", CLIENT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    cfg = str(tmp_path / "cfg.json")
    pc = tmp_path / "pc" / "Conveyor"
    assert m.main(["--config", cfg, "setup", "--server", url, "--token", token]) == 0
    assert m.main(["--config", cfg, "link", str(p["id"]), str(pc)]) == 0
    assert (pc / "src" / "main.py").read_text() == "v1" and (pc / "plan.md").exists()

    (pc / "src" / "main.py").write_text("from the PC", encoding="utf-8")
    (pc / "src" / "extra.py").write_text("extra", encoding="utf-8")
    assert m.main(["--config", cfg, "sync"]) == 0
    assert (srv / "src" / "main.py").read_text() == "from the PC" and (srv / "src" / "extra.py").exists()

    (srv / "src" / "extra.py").write_text("extra by the Hub", encoding="utf-8")
    (srv / "plan.md").unlink()
    assert m.main(["--config", cfg, "sync"]) == 0
    assert (pc / "src" / "extra.py").read_text() == "extra by the Hub" and not (pc / "plan.md").exists()

    (srv / "src" / "main.py").write_text("by the Hub", encoding="utf-8")
    (pc / "src" / "main.py").write_text("by me", encoding="utf-8")
    assert m.main(["--config", cfg, "sync"]) == 0
    copies = list((pc / "src").glob("main.slqconflict-r*.py"))
    assert len(copies) == 1 and copies[0].read_text() == "by the Hub"
    assert (srv / "src" / "main.py").read_text() == "by the Hub"
    (pc / "src" / "main.py").write_text("merged", encoding="utf-8")
    copies[0].unlink()
    assert m.main(["--config", cfg, "sync"]) == 0
    assert (srv / "src" / "main.py").read_text() == "merged"
    assert len(s.revisions(p["id"])) >= 5
