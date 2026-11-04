"""ComfyUI's log (0.77.0, Søren: "There are no ComfyUI log ?"): its container's pump (services/comfyui/logpump.py),
run here for real with a stand-in for ComfyUI, and the Hub's view of the file (app/gpu/comfy_log.py, Settings ->
System -> Logs -> ComfyUI)."""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.gpu import comfy_log

from .test_people import add_person, client_from, sign_in

PUMP = Path(__file__).resolve().parents[2] / "services" / "comfyui" / "logpump.py"
STAMPED = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ")


def _load():
    spec = importlib.util.spec_from_file_location("logpump", PUMP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pump = _load()

# ComfyUI as its log of 2026-11-04 shows it: logging lines in colour, a progress bar redrawn with carriage returns,
# a line PyTorch's C++ writes straight to stderr (on the same line as the bar), then the error.
COMFY = r'''
import os, sys
sys.stdout.write("\x1b[32m[INFO]\x1b[0m Requested to load WanVAE\n"); sys.stdout.flush()
for i in range(5):
    sys.stderr.write(f"\r{i * 25:3d}%|{'#' * i:4s}| {i}/4"); sys.stderr.flush()
os.write(2, b"[W1104 13:15:29.243267866 HIPCachingAllocator.cpp:3935] memory allocation failed with OOM on device 0\n")
sys.stdout.write("\x1b[1m\x1b[31m[ERROR]\x1b[0m !!! Exception during processing !!! HIP out of memory\n")
sys.stdout.flush()
sys.exit(int(sys.argv[1]))
'''

# A crash: more than a pipe holds, the last of it written just before the process dies
CRASH = r'''
import os
for i in range(3000):
    os.write(1, b"%05d " % i + b"x" * 100 + b"\n")
os.write(2, b"Memory access fault by GPU node-1 - the last words\n")
os.abort()
'''

# Docker's stop, passed on
TERM = r'''
import signal, sys, time
def bye(n, _f):
    print("ComfyUI got", signal.Signals(n).name, flush=True)
    sys.exit(0)
signal.signal(signal.SIGTERM, bye)
print("ready", flush=True)
time.sleep(60)
'''

# Something ComfyUI started that lives on, holding the pipe
HOLD = r'''
import subprocess, sys
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print("grandchild", p.pid, flush=True)
'''


def _pump_cmd(tmp_path: Path, code: str, *args: str, log: Path | None = None) -> tuple[list[str], Path]:
    script = tmp_path / "comfy.py"
    script.write_text(code)
    log = log or tmp_path / "logs" / "comfyui.log"
    return [sys.executable, str(PUMP), str(log), "--", sys.executable, str(script), *args], log


def _lines(path: Path) -> list[str]:
    return path.read_bytes().decode().split("\n")


def test_comfyuis_output_goes_on_and_into_the_file_each_line_with_its_time(tmp_path):
    (tmp_path / "logs").mkdir()
    cmd, log = _pump_cmd(tmp_path, COMFY, "3")
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    assert r.returncode == 3                                    # ComfyUI's, for docker's restart policy
    out = r.stdout.decode()
    assert r.stderr == b""                                      # both of its streams, in the order written
    assert out.index("Requested to load") < out.index("HIPCachingAllocator") < out.index("!!! Exception")
    assert "\x1b[32m[INFO]" in out and "\r 25%" in out          # docker logs gets it as it came
    lines = _lines(log)
    assert lines[-1] == ""
    assert all(STAMPED.match(x) for x in lines[:-1]), lines
    assert lines[0].endswith(f"===== start: {cmd[4]} {cmd[5]} 3")
    assert lines[-2].endswith("===== exited with code 3")
    raw = log.read_bytes()
    assert b"\r 25%|#   | 1/4\r 50%" in raw and b"4/4[W1104 13:15:29" in raw   # the file as it came, but stamped
    # as the System tab shows it: no colour codes, the bar as it ended (with the C++ line that followed it)
    shown = comfy_log.tail(log, 400).split("\n")
    assert [STAMPED.sub("", x) for x in shown[1:-1]] == [
        "[INFO] Requested to load WanVAE",
        "100%|####| 4/4[W1104 13:15:29.243267866 HIPCachingAllocator.cpp:3935] memory allocation failed with OOM "
        "on device 0",
        "[ERROR] !!! Exception during processing !!! HIP out of memory"]
    assert all(STAMPED.match(x) for x in shown)


def test_what_a_crashing_comfyui_wrote_last_is_kept(tmp_path):
    (tmp_path / "logs").mkdir()
    cmd, log = _pump_cmd(tmp_path, CRASH)
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    assert r.returncode == 128 + signal.SIGABRT
    assert r.stdout.count(b"x" * 100) == 3000 and r.stdout.endswith(b"the last words\n")
    text = log.read_text()
    assert text.count("x" * 100) == 3000
    assert "Memory access fault by GPU node-1 - the last words\n" in text
    assert text.rstrip().endswith("===== stopped by SIGABRT")


def test_dockers_stop_reaches_comfyui(tmp_path):
    (tmp_path / "logs").mkdir()
    cmd, log = _pump_cmd(tmp_path, TERM)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    try:
        assert p.stdout.readline() == b"ready\n"
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=10) == 0
        assert p.stdout.read() == b"ComfyUI got SIGTERM\n"
    finally:
        p.kill()
    assert log.read_text().rstrip().endswith("===== exited with code 0")


@pytest.mark.skipif(not shutil.which("unshare"), reason="no unshare")
def test_as_the_containers_first_process_it_still_passes_the_stop_on(tmp_path):
    """In a container the pump is PID 1, which the kernel spares every signal it has no handler for."""
    (tmp_path / "logs").mkdir()
    cmd, log = _pump_cmd(tmp_path, TERM)
    try:
        p = subprocess.Popen(["unshare", "--pid", "--fork", "--mount-proc", *cmd], stdout=subprocess.PIPE)
    except OSError as exc:
        pytest.skip(f"unshare: {exc}")
    try:
        first = p.stdout.readline()
        if first != b"ready\n":
            pytest.skip(f"no PID namespace here ({first!r})")
        kids = Path(f"/proc/{p.pid}/task/{p.pid}/children").read_text().split()
        assert len(kids) == 1                                   # the pump, PID 1 inside
        assert Path(f"/proc/{kids[0]}/status").read_text().count("NSpid:\t" + kids[0] + "\t1\n") == 1
        os.kill(int(kids[0]), signal.SIGTERM)
        assert p.wait(timeout=10) == 0
        assert p.stdout.read() == b"ComfyUI got SIGTERM\n"
    finally:
        p.kill()


def test_something_left_holding_the_pipe_does_not_keep_the_container_up(tmp_path):
    (tmp_path / "logs").mkdir()
    cmd, log = _pump_cmd(tmp_path, HOLD)
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    took = time.monotonic() - t0
    os.kill(int(r.stdout.split()[1]), signal.SIGKILL)
    assert r.returncode == 0 and took < pump.QUIET + 4, took
    assert log.read_text().rstrip().endswith("===== exited with code 0")


def test_a_log_that_cannot_be_written_costs_only_the_file(tmp_path):
    cmd, log = _pump_cmd(tmp_path, COMFY, "0", log=tmp_path / "not-mounted" / "comfyui.log")
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    assert r.returncode == 0
    out = r.stdout.decode()
    assert out.count("[logpump] cannot write") == 1 and "!!! Exception" in out
    assert not log.parent.exists()                              # not made: no folder is mounted there


def test_past_its_cap_the_file_becomes_the_older_part(tmp_path):
    path = tmp_path / "comfyui.log"
    log = pump.Log(str(path), cap=2000)
    for i in range(60):
        log.write(b"line %02d %s\n" % (i, b"." * 80))
    older = Path(str(path) + ".1")
    assert older.is_file() and path.stat().st_size <= 2000 + 120 and older.stat().st_size > 2000
    shown = comfy_log.tail(path, 15).split("\n")
    assert [int(x.split()[3]) for x in shown] == list(range(45, 60))   # across both files, in order
    everything = comfy_log.whole(path).split("\n")
    assert everything[-1] == "" and int(everything[0].split()[3]) == 60 - len(everything) + 1
    assert comfy_log.tail(tmp_path / "none.log", 10) is None and comfy_log.whole(tmp_path / "none.log") is None


@pytest.mark.skipif(not Path("/dev/full").exists(), reason="no /dev/full")
def test_a_disk_that_fills_up_costs_only_the_file(capfd):
    log = pump.Log("/dev/full")
    log.write(b"Requested to load WanVAE\n")                  # opened, but the write fails: no space left
    log.write(b"loaded completely\n")                         # not tried again within the minute
    assert log.f is None and log.failed_at is not None
    said = capfd.readouterr().out
    assert said.count("[logpump] cannot write /dev/full") == 1 and "No space left" in said


def test_a_link_put_in_the_folder_is_not_followed(tmp_path):
    """Only ComfyUI's container writes the folder: a link there to the Hub's secrets is not read (review of 0.77.0)."""
    secret = tmp_path / "environ"
    secret.write_text("HUB_SECRET_KEY=s3cret\n")
    path = tmp_path / "comfyui.log"
    path.symlink_to(secret)
    assert comfy_log.tail(path, 10) is None and comfy_log.whole(path) is None
    Path(str(path) + ".1").write_text("2026-11-04 13:10:00 ===== start: python main.py\n")
    assert "s3cret" not in comfy_log.tail(path, 10) and "s3cret" not in comfy_log.whole(path)
    assert comfy_log.whole(path) == "2026-11-04 13:10:00 ===== start: python main.py\n"
    os.mkfifo(tmp_path / "fifo.log")                            # nor waited on
    assert comfy_log.tail(tmp_path / "fifo.log", 10) is None and comfy_log.whole(tmp_path / "fifo.log") is None


def test_a_file_bigger_than_the_pump_makes_is_read_only_at_its_end(tmp_path, monkeypatch):
    monkeypatch.setattr(comfy_log, "MAX_READ", 1000)
    path = tmp_path / "comfyui.log"
    path.write_bytes(b"".join(b"2026-11-04 13:10:00 line %03d\n" % i for i in range(200)))   # 6 KB
    text = comfy_log.whole(path)
    assert text.startswith("2026-11-04 13:10:00 line ") and text.endswith("line 199\n") and len(text) <= 1000
    assert comfy_log.tail(path, 500).split("\n")[-1].endswith("line 199")
    assert len(comfy_log.tail(path, 500).split("\n")) <= 1000 // 29 + 1


async def test_the_system_tab_shows_comfyuis_log_and_hands_it_out(signed_in, app):
    path = app.state.hub.env.comfy_log
    r = await signed_in.get("/hub/api/system/comfyui-log")
    assert r.status_code == 200 and r.json()["exists"] is False and r.json()["text"].startswith("(No ComfyUI log yet")
    assert (await signed_in.get("/hub/api/system/comfyui-log", params={"download": 1})).status_code == 404
    path.parent.mkdir(parents=True)
    Path(str(path) + ".1").write_bytes(b"2026-11-04 13:10:00 ===== start: python main.py\n"
                                       b"2026-11-04 13:10:05 \x1b[32m[INFO]\x1b[0m Total VRAM 24560 MB\n")
    path.write_bytes(b"2026-11-04 13:15:28 \r  0%|    | 0/4\r100%|####| 4/4[W1104 HIPCachingAllocator] OOM\n"
                     b"2026-11-04 13:15:30 \x1b[31m[ERROR]\x1b[0m !!! Exception during processing !!!\n"
                     b"2026-11-04 13:15:31 \r 50%|##  | 2/4")             # a line still being written
    r = await signed_in.get("/hub/api/system/comfyui-log", params={"tail": 3})
    assert r.status_code == 200 and r.json()["exists"] is True
    assert r.json()["text"] == ("2026-11-04 13:15:28 100%|####| 4/4[W1104 HIPCachingAllocator] OOM\n"
                                "2026-11-04 13:15:30 [ERROR] !!! Exception during processing !!!\n"
                                "2026-11-04 13:15:31  50%|##  | 2/4")
    r = await signed_in.get("/hub/api/system/comfyui-log", params={"tail": 4})
    assert r.json()["text"].startswith("2026-11-04 13:10:05 [INFO] Total VRAM 24560 MB\n")
    r = await signed_in.get("/hub/api/system/comfyui-log", params={"download": 1})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert re.fullmatch(r'attachment; filename="comfyui-\d{4}-\d{2}-\d{2}-\d{4}\.log"',
                        r.headers["content-disposition"])
    assert r.text.splitlines() == ["2026-11-04 13:10:00 ===== start: python main.py",
                                   "2026-11-04 13:10:05 [INFO] Total VRAM 24560 MB",
                                   "2026-11-04 13:15:28 100%|####| 4/4[W1104 HIPCachingAllocator] OOM",
                                   "2026-11-04 13:15:30 [ERROR] !!! Exception during processing !!!",
                                   "2026-11-04 13:15:31  50%|##  | 2/4"]
    # the owner's, as the rest of Settings -> System
    await add_person(signed_in)
    async with client_from(app) as phone:
        assert (await sign_in(phone)).status_code == 200
        assert (await phone.get("/hub/api/system/comfyui-log")).status_code == 403
        assert (await phone.get("/hub/api/system/comfyui-log", params={"download": 1})).status_code == 403
