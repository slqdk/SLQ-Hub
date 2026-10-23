"""The sandbox daemon's confined commands (services/sandbox, a person's code): Landlock keeps them inside their
folder, and what they leave running is killed when they end. Skipped where the kernel has no Landlock."""
from __future__ import annotations

import importlib.util
import os
import signal
import time
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[2] / "services" / "sandbox"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"sandbox_{name}", SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


confine = _load("confine")
pytestmark = pytest.mark.skipif(confine.abi_version() < 1, reason="no Landlock on this kernel")


@pytest.fixture
def execd(tmp_path, monkeypatch):
    monkeypatch.setenv("EXECD_ROOT", str(tmp_path))
    monkeypatch.syspath_prepend(str(SANDBOX))
    return _load("execd")


def _procs(seconds: str) -> list[int]:
    """The `sleep <seconds>` processes running."""
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                if Path(f"/proc/{d}/cmdline").read_bytes() == f"sleep\0{seconds}\0".encode():
                    out.append(int(d))
            except OSError:
                pass
    return out


def test_a_confined_command_stays_in_its_folder(execd, tmp_path):
    box = tmp_path / "device" / "run_a"
    box.mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("the owner's file")
    (tmp_path / "device" / "run_b.txt").write_text("another turn")
    r = execd.run_exec({"cmd": "echo hi > mine.txt; cat mine.txt; cat ../../secret.txt; cat ../run_b.txt; "
                               "ls /root 2>&1 | head -1; echo x > ../escape.txt; echo tmp=$TMPDIR",
                        "cwd": str(box), "confine": {"root": str(box), "require": True}})
    assert r["exit_code"] is not None
    assert "hi" in r["stdout"] and (box / "mine.txt").read_text() == "hi\n"
    assert "the owner's file" not in r["stdout"] and "another turn" not in r["stdout"]
    assert "Permission denied" in r["stderr"]
    assert not (tmp_path / "device" / "escape.txt").exists()
    assert f"tmp={box}/.hub/tmp" in r["stdout"]


def test_no_shared_memory_or_terminals_but_the_usual_devices_work(execd, tmp_path):
    box = tmp_path / "run_e"
    box.mkdir()
    r = execd.run_exec({"cmd": "echo x > /dev/null && echo null-ok; head -c 4 /dev/urandom | wc -c; cat <(echo subst-ok); "
                               "python3 -c 'import subprocess; print(subprocess.run([\"echo\", \"child-ok\"], "
                               "capture_output=True, text=True).stdout.strip())'; "
                               "echo y > /dev/shm/slq-test-shm; ls /dev/shm; ls /dev/pts",
                        "cwd": str(box), "confine": {"root": str(box), "require": True}})
    out = r["stdout"]
    assert "null-ok" in out and "4" in out.split() and "subst-ok" in out and "child-ok" in out
    assert not os.path.exists("/dev/shm/slq-test-shm")
    assert r["stderr"].count("Permission denied") >= 3          # write and list /dev/shm, list /dev/pts


def test_a_confined_command_leaves_nothing_running(execd, tmp_path):
    box = tmp_path / "run_c"
    box.mkdir()
    r = execd.run_exec({"cmd": "sleep 3127 & setsid sleep 3128 & nohup sleep 3129 >/dev/null 2>&1 & echo started",
                        "cwd": str(box), "confine": {"root": str(box), "require": True}})
    assert "started" in r["stdout"] and r["duration_ms"] < 4000
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and (_procs("3127") or _procs("3128") or _procs("3129")):
        time.sleep(0.1)
    assert not _procs("3127") and not _procs("3128") and not _procs("3129")


def test_the_owners_commands_are_not_touched(execd, tmp_path):
    r = execd.run_exec({"cmd": "sleep 3131 >/dev/null 2>&1 & echo started", "cwd": str(tmp_path)})
    try:
        assert "started" in r["stdout"]
        time.sleep(0.2)
        assert _procs("3131")                 # a server the owner's agent started keeps running
    finally:
        for pid in _procs("3131"):
            os.kill(pid, signal.SIGKILL)


def test_a_confined_command_cannot_leave_the_folder_through_its_settings(execd, tmp_path):
    box = tmp_path / "run_d"
    box.mkdir()
    (tmp_path / "elsewhere").mkdir()
    for conf in ({"root": str(tmp_path / "elsewhere")}, {"root": str(box), "home": str(tmp_path)}, "yes"):
        with pytest.raises(ValueError):
            execd.run_exec({"cmd": "true", "cwd": str(box), "confine": conf})
