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
    base = {"yes": True, "force": False, "no_backup": False, "keep": 3, "timeout": 300.0, "resume": None,
            "now": False, "here": False, "quiet": 60.0}
    return argparse.Namespace(**{**base, **kw})


STATE = {"v": 1, "old": "a" * 40, "old_version": "0.16.0", "new_version": "0.16.1", "target": "b" * 40,
         "old_images": {"hub": "sha256:a"}}


def popen(run):
    """subprocess.Popen for the new updater: `run(cmd)` is what it does, and gives its exit code."""
    class Proc:
        def __init__(self, cmd, **kw):
            self.rc = run(cmd)

        def wait(self):
            return self.rc
    return Proc


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
    monkeypatch.setattr(hu.subprocess, "Popen", popen(child))
    assert hu.hand_over(args(no_backup=True), dict(STATE)) == 0
    assert seen["cmd"][1].endswith("tools/hub_update.py") and seen["cmd"][2:4] == ["update", "--resume"]
    assert seen["cmd"][5:] == ["--keep", "3", "--timeout", "300.0", "--quiet", "60.0", "--no-backup"]
    assert seen["state"] == STATE and not hu.resume_file().exists()
    assert not [c for c in hu.calls if c[:2] == ("git", "reset")]          # nothing put back


def test_a_new_updater_that_does_not_start_puts_the_code_back(hu, monkeypatch):
    monkeypatch.setattr(hu.subprocess, "Popen", popen(lambda cmd: 1))      # e.g. a SyntaxError: the file stays
    with pytest.raises(hu.Stop, match="did not start"):
        hu.hand_over(args(), dict(STATE))
    assert ("git", "reset", "--hard", "a" * 40) in hu.calls and ("tag", "rollback", "latest") in hu.calls
    assert not hu.resume_file().exists()


def test_its_failure_is_the_result(hu, monkeypatch):
    """Taken over, the new updater's own outcome (here: its build failed and it put things back) is the update's."""
    def child(cmd):
        hu.take_over(cmd[cmd.index("--resume") + 1])
        return 1
    monkeypatch.setattr(hu.subprocess, "Popen", popen(child))
    assert hu.hand_over(args(), dict(STATE)) == 1
    assert not [c for c in hu.calls if c[:2] == ("git", "reset")]


def test_ctrl_c_while_the_new_updater_works_is_its_business(hu, monkeypatch):
    """The old updater does not kill the new one on Ctrl+C (Python's subprocess.call does, a quarter of a second
    later - in the middle of an undo): the new one got the Ctrl+C too and decides what it means."""
    class Proc:
        def __init__(self, cmd, **kw):
            hu.take_over(cmd[cmd.index("--resume") + 1])
            self.waits = 0

        def wait(self):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            return 0

        def kill(self):
            pytest.fail("killed the new updater")
    monkeypatch.setattr(hu.subprocess, "Popen", Proc)
    assert hu.hand_over(args(), dict(STATE)) == 0


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


# ---------------------------------------------------------------- waiting until nobody uses the Hub (0.26.0)

class Answer:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_hub_says_whether_it_is_busy_and_an_older_one_leaves_it_to_the_card(hu, monkeypatch, tmp_path, capsys):
    (tmp_path / ".env").write_text("HUB_PORT=8081\nHUB_INTERNAL_TOKEN=s3cret\n")
    monkeypatch.setattr(hu, "ENV_FILE", tmp_path / ".env")
    asked = []

    def urlopen(req, timeout):
        asked.append((req.full_url, req.get_header("X-internal-token")))
        return Answer({"busy": True, "why": ["a chat is working on the card (Kan du ...)"]})
    monkeypatch.setattr(hu.urllib.request, "urlopen", urlopen)
    assert hu.hub_busy() == (True, ["a chat is working on the card (Kan du ...)"])
    assert asked == [("http://127.0.0.1:8081/hub/api/internal/busy", "s3cret")]
    monkeypatch.setattr(hu.urllib.request, "urlopen", lambda req, timeout: Answer({"busy": False, "why": []}))
    assert hu.hub_busy() == (False, [])

    def answering(code):
        def urlopen(req, timeout):
            raise hu.urllib.error.HTTPError(req.full_url, code, "no", {}, None)
        return urlopen
    for code in (401, 404):                                       # a Hub before 0.26.0 has no such question
        monkeypatch.setattr(hu.urllib.request, "urlopen", answering(code))
        monkeypatch.setattr(hu, "card_load", lambda: 97)
        assert hu.hub_busy() == (True, ["the card is 97 % busy (the running Hub cannot say what for)"])
        monkeypatch.setattr(hu, "card_load", lambda: 2)
        assert hu.hub_busy() == (False, [])
    monkeypatch.setattr(hu, "card_load", lambda: None)
    assert hu.hub_busy() == (False, [])
    assert "refused" not in capsys.readouterr().out
    monkeypatch.setattr(hu.urllib.request, "urlopen", answering(403))       # another token: said, once
    hu.hub_busy()
    hu.hub_busy()
    assert capsys.readouterr().out.count("The Hub refused the updater's question (403)") == 1

    def cut_off(req, timeout):                                    # the Hub restarting in the middle of its answer
        raise hu.http.client.IncompleteRead(b"{")
    monkeypatch.setattr(hu.urllib.request, "urlopen", cut_off)
    monkeypatch.setattr(hu, "card_load", lambda: 50)
    assert hu.hub_busy()[0] is True

    def cancelled(req, timeout):                                  # `update.sh cancel` while it asks
        raise hu.Cancelled("SIGTERM")
    monkeypatch.setattr(hu.urllib.request, "urlopen", cancelled)
    with pytest.raises(hu.Cancelled):
        hu.hub_busy()


def test_the_cards_own_load(hu, tmp_path):
    for card, pct in (("card0", "3"), ("card1", "72")):
        (tmp_path / card / "device").mkdir(parents=True)
        (tmp_path / card / "device" / "gpu_busy_percent").write_text(pct + "\n")
    assert hu.card_load(str(tmp_path)) == 72
    assert hu.card_load(str(tmp_path / "none")) is None


def test_it_goes_on_after_a_quiet_minute(hu, monkeypatch, capsys):
    clock = [0.0]
    monkeypatch.setattr(hu.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(hu.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    seq = iter([(True, ["a chat is answering"])] * 3 + [(False, [])] * 4 + [(True, ["Maria's chat is answering"])]
               + [(False, [])] * 20)
    monkeypatch.setattr(hu, "hub_busy", lambda: next(seq))
    hu.wait_until_free(30)
    said = capsys.readouterr().out
    assert said.count("waiting: a chat is answering") == 1 and "waiting: Maria's chat is answering" in said
    assert said.count("free - going on after 30 s without work") == 2
    assert clock[0] == 5 * (3 + 4 + 1 + 6)                     # 30 s of quiet after the last use: 6 checks


def fake_update(hu, monkeypatch, busy=(False, [])):
    """finish_update's surroundings, faked: docker, the backup, the firewall."""
    monkeypatch.setattr(hu, "compose", lambda *a, **k: hu.calls.append(("compose", *a)) or
                        subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(hu, "container_state", lambda svc="hub": "running")
    monkeypatch.setattr(hu, "container_id", lambda svc="hub": "c0ffee")
    monkeypatch.setattr(hu, "hub_busy", lambda: busy)
    monkeypatch.setattr(hu, "make_backup", lambda name, info: hu.calls.append(("backup", name)) or
                        (hu.HUB_ROOT / "b.tar.gz"))
    (hu.HUB_ROOT / "b.tar.gz").write_bytes(b"x")
    monkeypatch.setattr(hu, "wait_healthy", lambda t: True)
    monkeypatch.setattr(hu, "sync_firewall", lambda: "applied")
    monkeypatch.setattr(hu, "report_ways_out", lambda: True)
    monkeypatch.setattr(hu, "prune_backups", lambda keep: [])
    monkeypatch.setattr(hu, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(hu, "read_version_at", lambda ref="HEAD": "0.26.0")
    monkeypatch.setattr(hu, "backup_dir", lambda: hu.HUB_ROOT / "backups")
    hu.signals = {}
    monkeypatch.setattr(hu.signal, "signal", lambda s, h: hu.signals.__setitem__(s, h))
    monkeypatch.setattr(hu, "LOCK_MARK", b"pytest")             # this process stands in for the updater


def undone(hu) -> bool:
    return ("git", "reset", "--hard", "a" * 40) in hu.calls and ("tag", "rollback", "latest") in hu.calls


def test_with_nobody_using_the_hub_it_waits_its_quiet_time_then_updates(hu, monkeypatch):
    fake_update(hu, monkeypatch)
    waited = []
    monkeypatch.setattr(hu, "wait_until_free", lambda quiet: waited.append(quiet))
    assert hu.finish_update(args(quiet=45.0), dict(STATE)) == 0
    assert waited == [45.0]
    order = [c[:2] for c in hu.calls if c[0] in ("compose", "backup")]
    assert order.index(("compose", "build")) < order.index(("compose", "stop")) < [c[0] for c in order].index("backup")
    assert not hu.lock_file().exists()                          # gone at the end
    assert all(hu.signals[s] == hu.signal.SIG_IGN for s in (hu.signal.SIGTERM, hu.signal.SIGHUP, hu.signal.SIGINT))
    # --now: no wait at all
    waited.clear()
    assert hu.finish_update(args(now=True), dict(STATE)) == 0 and waited == []


class Background:
    """The updater in a session of its own, faked: it takes over at once, writes its log, ends with `rc`."""
    def __init__(self, hu, started: dict, rc: int = 0, lines: str = "Backing up the data...\n=== Update complete ===\n"):
        self.hu, self.started, self.rc, self.lines, self.polls = hu, started, rc, lines, 0

    def __call__(self, cmd, **kw):
        self.started.update(cmd=cmd, kw=kw, state=self.hu.take_over(cmd[cmd.index("--resume") + 1]))
        return self

    def poll(self):
        self.polls += 1
        if self.polls == 1:
            return None
        if self.polls == 2:
            with open(self.hu.log_file(), "a", encoding="utf-8") as f:
                f.write(self.lines)
            return None
        return self.rc

    def kill(self):
        self.started["killed"] = True


def test_in_a_terminal_the_rest_runs_in_its_own_session_and_the_window_shows_it(hu, monkeypatch, capsys):
    fake_update(hu, monkeypatch, busy=(True, ["a chat is working on the card (Kan du gøre det)"]))
    monkeypatch.setattr(hu, "attached", lambda: True)
    monkeypatch.setattr(hu.time, "sleep", lambda s: None)
    started = {}
    monkeypatch.setattr(hu.subprocess, "Popen", Background(hu, started))
    assert hu.finish_update(args(), dict(STATE)) == 0
    assert ("compose", "build") in hu.calls                                     # built here first
    assert started["state"] == {**STATE, "built": True, "detached": True}        # it starts at the wait
    assert started["cmd"][2:] == ["update", "--resume", str(hu.resume_file()), "--yes", "--keep", "3", "--timeout",
                                  "300.0", "--quiet", "60.0"]
    assert started["kw"]["start_new_session"] and started["kw"]["stdin"] == subprocess.DEVNULL
    assert ("compose", "stop") not in hu.calls and "killed" not in started            # nothing stopped here
    assert hu.log_file().read_text().startswith("SLQ Hub update 0.16.0 -> 0.16.1, from ")
    said = capsys.readouterr().out
    assert "Ctrl+C or closing this window does not stop it" in said and "sudo bash update.sh cancel" in said
    assert said.endswith("Backing up the data...\n=== Update complete ===\n")      # its log, shown here
    # --now goes with it, and its outcome is this one's
    hu.calls.clear()
    monkeypatch.setattr(hu.subprocess, "Popen", Background(hu, started, rc=1))
    assert hu.finish_update(args(now=True), dict(STATE)) == 1
    assert started["cmd"][-1] == "--now"
    # one that never takes over: the code goes back
    hu.calls.clear()
    monkeypatch.setattr(hu.subprocess, "Popen", lambda cmd, **kw: type("C", (), {"poll": lambda self: 1,
                                                                               "kill": lambda self: None})())
    with pytest.raises(hu.Stop, match="could not go on in the background"):
        hu.finish_update(args(), dict(STATE))
    assert undone(hu) and not hu.resume_file().exists()


def test_handed_over_in_a_terminal_it_builds_in_its_own_session_too(hu, monkeypatch):
    """The old updater kills this one a quarter of a second after a Ctrl+C: nothing that matters may run here."""
    fake_update(hu, monkeypatch)
    monkeypatch.setattr(hu, "attached", lambda: True)
    monkeypatch.setattr(hu.time, "sleep", lambda s: None)
    started = {}
    monkeypatch.setattr(hu.subprocess, "Popen", Background(hu, started))
    assert hu.finish_update(args(resume="/srv/slq-hub/update-resume.json"), dict(STATE)) == 0
    assert ("compose", "build") not in hu.calls and started["state"] == {**STATE, "detached": True}


def test_ctrl_c_only_stops_showing_the_log(hu, monkeypatch, capsys):
    hu.log_file().write_text("header\nwaiting: a chat is answering\n")

    class Child:
        def poll(self):
            raise KeyboardInterrupt

        def kill(self):
            pytest.fail("killed the update")
    monkeypatch.setattr(hu.signal, "signal", lambda s, h: None)
    assert hu.follow(Child(), hu.log_file(), start=len("header\n")) == 0
    said = capsys.readouterr().out
    assert "No longer shown here - the update goes on" in said and "header" not in said


def test_the_background_updater_waits_in_place(hu, monkeypatch):
    fake_update(hu, monkeypatch, busy=(True, ["a test run is going (Settings -> Tests)"]))
    monkeypatch.setattr(hu, "attached", lambda: True)
    phases = []
    monkeypatch.setattr(hu, "wait_until_free", lambda quiet: phases.append(json.loads(hu.lock_file().read_text())))
    assert hu.finish_update(args(), {**STATE, "built": True, "detached": True}) == 0
    lock = phases[0]
    assert lock["phase"] == "waiting for the Hub to be free" and lock["pid"] == hu.os.getpid()
    assert lock["old"] == "a" * 40 and lock["boot"] == hu.boot_id() and lock["version"] == "0.16.1"
    assert ("compose", "build") not in hu.calls                  # built before it went to the background
    assert not list(hu.HUB_ROOT.glob("*.tmp"))                  # the lock is written whole


def test_cancelled_while_building_or_waiting_nothing_is_stopped_and_the_code_goes_back(hu, monkeypatch):
    fake_update(hu, monkeypatch, busy=(True, ["a chat is answering"]))

    def cancelled(quiet):
        raise hu.Cancelled("SIGTERM")
    monkeypatch.setattr(hu, "wait_until_free", cancelled)
    with pytest.raises(hu.Stop, match=r"cancelled while waiting for the Hub to be free \(SIGTERM\)"):
        hu.finish_update(args(here=True), dict(STATE))
    assert undone(hu) and ("compose", "stop") not in hu.calls and not hu.lock_file().exists()
    assert hu.signals[hu.signal.SIGTERM] == hu.signal.SIG_IGN    # a second signal cannot cut the undo short
    # while building
    hu.calls.clear()

    def build(*a, **k):
        if a[0] == "build":
            raise hu.Cancelled("SIGTERM")
        return subprocess.CompletedProcess(a, 0, "", "")
    monkeypatch.setattr(hu, "compose", build)
    with pytest.raises(hu.Stop, match=r"cancelled while building \(SIGTERM\)"):
        hu.finish_update(args(here=True), dict(STATE))
    assert undone(hu)


def test_anything_going_wrong_before_the_install_puts_the_code_back(hu, monkeypatch):
    fake_update(hu, monkeypatch)

    def broken(quiet):
        raise RuntimeError("the clock went backwards")
    monkeypatch.setattr(hu, "wait_until_free", broken)
    with pytest.raises(hu.Stop, match="RuntimeError while waiting for the Hub to be free: the clock went backwards"):
        hu.finish_update(args(), dict(STATE))
    assert undone(hu) and ("compose", "stop") not in hu.calls


def test_one_update_at_a_time_and_cancel(hu, monkeypatch):
    monkeypatch.setattr(hu, "LOCK_MARK", b"pytest")
    me = {"pid": hu.os.getpid(), "boot": hu.boot_id(), "version": "0.26.0", "phase": "waiting for the Hub to be free",
          "since": "2026-10-25T16:52:00", "log": "/srv/slq-hub/update.log", "old": "a" * 40}
    hu.lock_file().write_text(json.dumps(me))
    monkeypatch.setattr(hu, "preflight", lambda *a, **k: "main")
    with pytest.raises(hu.Stop, match="an update to 0.26.0 is already waiting for the Hub to be free"):
        hu.cmd_update(args())
    killed = []
    real_kill = hu.os.kill

    def kill(pid, sig):
        if sig == hu.signal.SIGTERM:
            killed.append(pid)
            hu.lock_file().unlink()                              # it cancels and removes its lock
            return None
        return real_kill(pid, sig)
    monkeypatch.setattr(hu.os, "kill", kill)
    monkeypatch.setattr(hu.os, "geteuid", lambda: 0)
    assert hu.cmd_cancel(argparse.Namespace()) == 0 and killed == [hu.os.getpid()]
    hu.lock_file().write_text(json.dumps({**me, "phase": "building"}))
    assert hu.cmd_cancel(argparse.Namespace()) == 0 and len(killed) == 2      # a build can be cancelled too
    hu.lock_file().write_text(json.dumps({**me, "phase": "being installed"}))
    with pytest.raises(hu.Stop, match="too late to cancel"):
        hu.cmd_cancel(argparse.Namespace())


def test_a_lock_names_a_running_updater_or_none(hu, monkeypatch):
    me = {"pid": hu.os.getpid(), "boot": hu.boot_id(), "version": "0.26.0", "phase": "building"}
    hu.lock_file().write_text(json.dumps(me))
    assert hu.read_lock() == {}                                  # this pid is no updater (a number used again)
    monkeypatch.setattr(hu, "LOCK_MARK", b"pytest")
    assert hu.read_lock()["pid"] == hu.os.getpid()
    hu.lock_file().write_text(json.dumps({**me, "boot": "another-boot"}))
    assert hu.read_lock() == {}                                  # from before a reboot
    hu.lock_file().write_text(json.dumps({**me, "pid": 2 ** 22 + 12345}))
    assert hu.read_lock() == {} and hu.lock_file().exists()      # gone: left for recover_cut_off
    hu.lock_file().write_text("{half a lock")
    assert hu.read_lock() == {} and not hu.lock_file().exists()  # written whole, so a broken one is just broken


def test_an_update_cut_off_before_the_install_is_put_back_by_the_next(hu, monkeypatch, capsys):
    gone = {"pid": 2 ** 22 + 12345, "boot": hu.boot_id(), "version": "0.26.0", "since": "2026-10-25T17:00:00",
            "phase": "waiting for the Hub to be free", "old": "a" * 40, "old_version": "0.25.0",
            "log": "/srv/slq-hub/update.log"}
    hu.lock_file().write_text(json.dumps(gone))
    hu.recover_cut_off(args())
    assert undone(hu) and not hu.lock_file().exists()
    assert "Putting the code back at 0.25.0 first - nothing was stopped" in capsys.readouterr().out
    # cut off while it installed: the Hub may be stopped - said, and --force starts this version again
    hu.calls.clear()
    hu.lock_file().write_text(json.dumps({**gone, "phase": "being installed"}))
    with pytest.raises(hu.Stop, match=r"cut off while it was being installed .* update.sh --force"):
        hu.recover_cut_off(args())
    assert hu.lock_file().exists() and not hu.calls
    hu.recover_cut_off(args(force=True))
    assert not hu.lock_file().exists() and not hu.calls
    # a live one is left alone
    monkeypatch.setattr(hu, "LOCK_MARK", b"pytest")
    hu.lock_file().write_text(json.dumps({**gone, "pid": hu.os.getpid()}))
    hu.recover_cut_off(args())
    assert hu.lock_file().exists() and not hu.calls


def test_the_windows_container_gets_a_password_when_its_profile_is_on(hu, tmp_path):
    """0.39.0: with COMPOSE_PROFILES=windows and no WINDOWS_PASSWORD, a random one is written into .env (RDP is open
    to the LAN, and dockur's default password is admin); once there it is kept."""
    env = tmp_path / ".env"
    env.write_text("HUB_PORT=8080\n")
    assert hu.ensure_windows_password(env) is None                         # the profile is off: nothing
    env.write_text("HUB_PORT=8080\nCOMPOSE_PROFILES=windows")
    pw = hu.ensure_windows_password(env)
    assert pw and len(pw) >= 12 and env.read_text().endswith(f"\nWINDOWS_PASSWORD={pw}\n")
    assert hu.read_env(env)["COMPOSE_PROFILES"] == "windows" and hu.read_env(env)["WINDOWS_PASSWORD"] == pw
    assert hu.ensure_windows_password(env) is None                         # kept from then on
    env.write_text("COMPOSE_PROFILES=other, windows\nWINDOWS_PASSWORD=mine\n")
    assert hu.ensure_windows_password(env) is None and hu.read_env(env)["WINDOWS_PASSWORD"] == "mine"
    assert {"windows/storage", "windows/oem", "windows/shared"} <= set(hu.FOLDERS)


def test_windows_is_under_the_sandbox_rules():
    """The Windows container: only with its profile, on the sandbox's network at its fixed address, with KVM - and
    the firewall gives its address the sandbox's rules (no LAN, no containers, no VM; internet open)."""
    root = TOOLS.parent
    compose = (root / "deploy" / "docker-compose.yml").read_text()
    svc = compose.split("\n  windows:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    assert 'profiles: ["windows"]' in svc and "ipv4_address: 172.31.250.30" in svc and "- /dev/kvm" in svc
    assert "- /dev/net/tun" in svc and "dns: [1.1.1.1, 9.9.9.9]" in svc and '"127.0.0.1:8006:8006"' in svc
    fw = (root / "deploy" / "firewall" / "slq-hub-firewall.sh").read_text()
    assert 'SANDBOX_IPS="${SANDBOX_IPS:-172.31.250.20 172.31.250.30}"' in fw
    assert 'add DOCKER-USER -s "$ip" -d "$net" -m conntrack --ctstate NEW -j DROP' in fw


def test_a_profile_turned_on_since_the_last_update_is_built_and_started(hu, monkeypatch):
    """COMPOSE_PROFILES=training added to .env with nothing new to fetch (0.50.1): the trainer, which never had a
    container, is built and started; services that exist - running or stopped - are left alone."""
    lists = {"config": "hub\nllm\ncomfyui\ntrainer\n", "ps": "hub\nllm\ncomfyui\n"}

    def compose(*a, **k):
        hu.calls.append(("compose", *a))
        return subprocess.CompletedProcess(a, 0, lists.get(a[0], ""), "")
    monkeypatch.setattr(hu, "compose", compose)
    monkeypatch.setattr(hu, "say_windows_password", lambda: None)
    assert hu.start_new_services() == ["trainer"]
    assert ("compose", "up", "-d", "--build", "trainer") in hu.calls
    assert (hu.HUB_ROOT / "data" / "training").is_dir() and (hu.HUB_ROOT / "models" / "hf").is_dir()
    hu.calls.clear()
    lists["ps"] = lists["config"]                      # all there: nothing is started
    assert hu.start_new_services() == [] and not [c for c in hu.calls if c[1] == "up"]
