"""The updater (tools/hub_update.py, 0.16.0): when a fast-forward changes the updater itself, the new updater does
the rest of the update, so what it changed applies at once - and if it cannot start, the old one puts the code
back as after a failed build. git, docker and the new updater's process are faked."""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"


@pytest.fixture
def hu(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("hub_update_test", TOOLS / "hub_update.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HUB_ROOT", tmp_path)
    mod.calls = []

    def git(*args):
        mod.calls.append(("git", *args))
        if args[0] == "diff":
            return subprocess.CompletedProcess(args, 1 if mod.changed else 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    mod.changed = True
    monkeypatch.setattr(mod, "git", git)
    monkeypatch.setattr(mod, "tag_images", lambda src, dst: mod.calls.append(("tag", src, dst)) or {"hub": "sha256:a"})
    return mod


def args(**kw) -> argparse.Namespace:
    base = {"yes": True, "force": False, "no_backup": False, "keep": 3, "timeout": 300.0, "resume": None}
    return argparse.Namespace(**{**base, **kw})


STATE = {"v": 1, "old": "a" * 40, "old_version": "0.16.0", "new_version": "0.16.1", "target": "b" * 40,
         "old_images": {"hub": "sha256:a"}}


def test_only_a_changed_updater_hands_over(hu):
    assert hu.updater_changed("a" * 40)
    assert ("git", "diff", "--quiet", "a" * 40, "HEAD", "--", "tools/hub_update.py", "update.sh") in hu.calls
    hu.changed = False
    assert not hu.updater_changed("a" * 40)


def test_the_new_updater_takes_over(hu, monkeypatch):
    seen = {}

    def child(cmd):                                       # the new updater: reads the state, deletes the file
        seen["cmd"] = cmd
        i = cmd.index("--resume")
        seen["state"] = hu.take_over(cmd[i + 1])
        return 0
    monkeypatch.setattr(hu.subprocess, "call", child)
    assert hu.hand_over(args(no_backup=True), dict(STATE)) == 0
    assert seen["cmd"][1].endswith("tools/hub_update.py") and seen["cmd"][2:4] == ["update", "--resume"]
    assert seen["cmd"][5:] == ["--keep", "3", "--timeout", "300.0", "--no-backup"]
    assert seen["state"] == STATE and not hu.resume_file().exists()
    assert not [c for c in hu.calls if c[:2] == ("git", "reset")]          # nothing put back


def test_a_new_updater_that_does_not_start_puts_the_code_back(hu, monkeypatch):
    monkeypatch.setattr(hu.subprocess, "call", lambda cmd: 1)              # e.g. a SyntaxError: the file stays
    with pytest.raises(hu.Stop, match="did not start"):
        hu.hand_over(args(), dict(STATE))
    assert ("git", "reset", "--hard", "a" * 40) in hu.calls and ("tag", "rollback", "latest") in hu.calls
    assert not hu.resume_file().exists()


def test_its_failure_is_the_result(hu, monkeypatch):
    """Taken over, the new updater's own outcome (here: its build failed and it put things back) is the update's."""
    def child(cmd):
        hu.take_over(cmd[cmd.index("--resume") + 1])
        return 1
    monkeypatch.setattr(hu.subprocess, "call", child)
    assert hu.hand_over(args(), dict(STATE)) == 1
    assert not [c for c in hu.calls if c[:2] == ("git", "reset")]


def test_a_resumed_update_continues_from_the_build(hu, monkeypatch):
    hu.resume_file().write_text(json.dumps(STATE))
    got = {}
    monkeypatch.setattr(hu, "finish_update", lambda a, state: got.setdefault("state", state) and 0)
    monkeypatch.setattr(hu, "preflight", lambda *a, **k: pytest.fail("a resumed update does not start over"))
    assert hu.cmd_update(args(resume=str(hu.resume_file()))) == 0
    assert got["state"] == STATE and not hu.resume_file().exists()
    for bad in ("not json", json.dumps({**STATE, "v": 99}), json.dumps({"v": 1})):
        hu.resume_file().write_text(bad)
        with pytest.raises(hu.Stop):
            hu.take_over(str(hu.resume_file()))
        assert hu.resume_file().exists()      # left in place, so the old updater rolls back as "did not start"


def test_the_flag_is_there_but_not_shown(hu, capsys):
    with pytest.raises(SystemExit):
        hu.main(["update", "--help"])
    assert "--resume" not in capsys.readouterr().out
    parsed = {}
    hu.cmd_update = lambda a: parsed.setdefault("args", a) and 0
    hu.main(["update", "--resume", "/srv/slq-hub/update-resume.json"])
    assert parsed["args"].resume == "/srv/slq-hub/update-resume.json"


def test_an_update_hands_over_after_the_fast_forward(hu, monkeypatch):
    """The whole first half with git faked: fetch, new commits, the question skipped (--yes), the images tagged,
    the fast-forward - then the hand-over, because the updater changed."""
    def git(*a):
        hu.calls.append(("git", *a))
        out = {"rev-parse": "a" * 40 if a[-1] == "HEAD" else "b" * 40,
               "log": "bbbbbbb Windows programs\n"}.get(a[0], "")
        return subprocess.CompletedProcess(a, 1 if a[0] == "diff" else 0, out, "")
    monkeypatch.setattr(hu, "git", git)
    monkeypatch.setattr(hu, "preflight", lambda *a, **k: "main")
    monkeypatch.setattr(hu, "read_version_at", lambda ref="HEAD": "0.16.1" if "origin" in ref else "0.16.0")
    monkeypatch.setattr(hu, "run", lambda cmd, **k: hu.calls.append(("run", *cmd)) or
                        subprocess.CompletedProcess(cmd, 0, "", ""))
    handed = {}
    monkeypatch.setattr(hu, "hand_over", lambda a, state: handed.setdefault("state", state) and 0)
    monkeypatch.setattr(hu, "finish_update", lambda a, state: pytest.fail("the old updater went on"))
    assert hu.cmd_update(args()) == 0
    assert ("run", "git", "merge", "--ff-only", "origin/main") in hu.calls
    assert handed["state"] == {"v": 1, "old": "a" * 40, "old_version": "0.16.0", "new_version": "0.16.1",
                               "target": "b" * 40, "old_images": {"hub": "sha256:a"}}
