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


def test_online_once_leaves_nothing_running(execd, tmp_path):
    """The owner's command allowed online once (internet asking first): what it leaves running is ended with it,
    so nothing keeps the internet for later commands."""
    r = execd.run_exec({"cmd": "sleep 3133 & setsid sleep 3134 & nohup sleep 3135 >/dev/null 2>&1 & echo started",
                        "cwd": str(tmp_path), "end_leftovers": True})
    assert "started" in r["stdout"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and (_procs("3133") or _procs("3134") or _procs("3135")):
        time.sleep(0.1)
    assert not _procs("3133") and not _procs("3134") and not _procs("3135")


def test_a_confined_command_cannot_leave_the_folder_through_its_settings(execd, tmp_path):
    box = tmp_path / "run_d"
    box.mkdir()
    (tmp_path / "elsewhere").mkdir()
    for conf in ({"root": str(tmp_path / "elsewhere")}, {"root": str(box), "home": str(tmp_path)}, "yes"):
        with pytest.raises(ValueError):
            execd.run_exec({"cmd": "true", "cwd": str(box), "confine": conf})


def test_offline_means_no_tcp_connections(execd, tmp_path):
    """Internet for the assistant set to ask first: a command without permission runs with no TCP connections at all
    (Landlock ABI 4) - the owner's with no folder limits, a person's on top of them - and is not run at all where
    that cannot be enforced."""
    import socket
    import sys
    import threading
    if confine.abi_version() < 4:
        pytest.skip("this kernel's Landlock cannot limit network connections")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    threading.Thread(target=lambda: [srv.accept() for _ in range(8)], daemon=True).start()
    probe = (f"{sys.executable} -c \"import socket; socket.create_connection(('127.0.0.1', {port}), 3); "
             f"print('connected')\"")
    online = execd.run_exec({"cmd": probe, "cwd": str(tmp_path)})
    assert online["exit_code"] == 0 and "connected" in online["stdout"]
    off = execd.run_exec({"cmd": probe + "; echo hi > note.txt; cat note.txt", "cwd": str(tmp_path), "offline": True})
    assert "connected" not in off["stdout"] and "Permission denied" in off["stderr"]
    assert "hi" in off["stdout"] and (tmp_path / "note.txt").exists()            # files as usual for the owner
    box = tmp_path / "device" / "run_c"
    box.mkdir(parents=True)
    person = execd.run_exec({"cmd": probe, "cwd": str(box), "confine": {"root": str(box)}, "offline": True})
    assert "connected" not in person["stdout"] and "Permission denied" in person["stderr"]
    srv.close()


WAYS_OUT = r"""
import socket, sys
port = int(sys.argv[1])
def mptcp():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 262); s.settimeout(3); s.connect(("127.0.0.1", port))
def fast_open():                                  # a connection opened by sendto, without connect
    s = socket.socket(); s.sendto(b"GET / HTTP/1.0\r\n\r\n", 0x20000000, ("127.0.0.1", port))
def fast_open_sendmsg():
    s = socket.socket(); s.sendmsg([b"x"], [], 0x20000000, ("127.0.0.1", port))
def udp():
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("127.0.0.1", 9))
def unix():
    a, b = socket.socketpair(); a.send(b"x"); assert b.recv(1) == b"x"
for name, way in (("mptcp", mptcp), ("fast_open", fast_open), ("fast_open_sendmsg", fast_open_sendmsg),
                  ("udp", udp), ("unix", unix)):
    try:
        way(); print(name, "open")
    except OSError as e:
        print(name, "refused", e.errno)
"""


def test_the_ways_around_the_tcp_rule_are_closed(execd, tmp_path):
    """Landlock's TCP rule sees only connect() on a TCP socket. Where it limits connections, the seccomp filter
    refuses MPTCP (which talks to any TCP server), TCP Fast Open (a connection made by sendto/sendmsg) and other
    socket kinds; offline UDP goes too, while a person's online command keeps it for DNS. Unix sockets stay."""
    import shlex
    import socket
    import threading
    if confine.abi_version() < 4:
        pytest.skip("this kernel's Landlock cannot limit network connections")
    assert execd.OFFLINE is True                     # what /health tells the Hub: offline commands work here
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(32)
    port = srv.getsockname()[1]
    threading.Thread(target=lambda: [srv.accept() for _ in range(32)], daemon=True).start()
    probe = tmp_path / "ways_out.py"
    probe.write_text(WAYS_OUT)
    box = tmp_path / "device" / "run_w"
    box.mkdir(parents=True)
    (box / "ways_out.py").write_text(WAYS_OUT)

    def ways(body: dict) -> dict[str, str]:
        res = execd.run_exec(body)
        return dict(line.split()[:2] for line in res["stdout"].splitlines() if line.strip())
    cmd = f"python3 {shlex.quote(str(probe))} {port}"
    unlimited = ways({"cmd": cmd, "cwd": str(tmp_path)})
    assert unlimited["fast_open"] == "open" and unlimited["udp"] == "open"        # the probe sees a way out
    mptcp = Path("/proc/sys/net/mptcp/enabled")
    if mptcp.is_file() and mptcp.read_text().strip() == "1":
        assert unlimited["mptcp"] == "open"
    off = ways({"cmd": cmd, "cwd": str(tmp_path), "offline": True})
    assert off == {"mptcp": "refused", "fast_open": "refused", "fast_open_sendmsg": "refused", "udp": "refused",
                   "unix": "open"}, off
    person_cmd = f"python3 ways_out.py {port}"
    person = ways({"cmd": person_cmd, "cwd": str(box), "confine": {"root": str(box)}})
    assert person == {"mptcp": "refused", "fast_open": "refused", "fast_open_sendmsg": "refused", "udp": "open",
                      "unix": "open"}, person
    person_off = ways({"cmd": person_cmd, "cwd": str(box), "confine": {"root": str(box)}, "offline": True})
    assert person_off["udp"] == "refused" and person_off["mptcp"] == "refused"
    srv.close()


def test_the_self_test_checks_offline_code(execd, capsys):
    """The self-test's "offline code" line runs its probe through the daemon: PASS where offline holds, FAIL where
    a command run "offline" still connects, WARN without Landlock ABI 4."""
    import types

    from app import cli
    if confine.abi_version() < 4:
        pytest.skip("this kernel's Landlock cannot limit network connections")

    class Client:
        def __init__(self, honour: bool):
            self.honour = honour

        def post(self, url, headers=None, json=None, timeout=None):
            body = {**json, "cwd": execd.ROOT}
            if not self.honour:
                body.pop("offline")
            return types.SimpleNamespace(json=lambda: execd.run_exec(body))
    env = types.SimpleNamespace(sandbox_url="http://sandbox", internal_token="t")
    for honour, want in ((True, "PASS"), (False, "FAIL")):
        rep = cli.Report()
        cli._offline(rep, Client(honour), env, {"landlock": confine.abi_version()})
        line = capsys.readouterr().out
        assert want in line and "offline code" in line, line
    rep = cli.Report()
    cli._offline(rep, Client(True), env, {"landlock": 3})
    assert "WARN" in capsys.readouterr().out and rep.warned == 1


def test_the_socket_filter_builds_for_both_machines():
    for machine in ("x86_64", "aarch64"):
        for offline in (True, False):
            prog = confine.socket_program(machine, offline)
            assert prog[0] == (confine.LD_ABS, 0, 0, 4) and prog[1][3] == confine.AUDIT_ARCH[machine]
            assert all(0 <= jt <= 255 and 0 <= jf <= 255 for _, jt, jf, _ in prog)
            assert prog[-1] == (confine.RET, 0, 0, confine.RET_ERRNO | confine.ENOSYS)
    with pytest.raises(OSError, match="no seccomp filter"):
        confine.socket_program("riscv64", True)


def test_offline_is_refused_where_landlock_cannot_do_it(execd, tmp_path, monkeypatch):
    monkeypatch.setattr(confine, "abi_version", lambda: 3)
    argv = execd._confined("ls", str(tmp_path), {"root": str(tmp_path)}, offline=True)
    assert "--offline" in argv and "--require" in argv
    with pytest.raises(OSError, match="cannot limit network connections"):
        confine.confine([], [], ports=(), files=False, need_net=True)
