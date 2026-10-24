"""Windows programs on a virtual screen (0.16.0; typing through HubKeys since 0.16.1): windows_app in the Hub,
winshot.py and dotnet_windows.py in the sandbox image. The sandbox is faked here; winshot's own parts are tested
without Wine (its whole run was tried by hand in a container - docs/HANDOFF.md), and with SLQ_TEST_WINE=1 on a
machine that has Wine, Xvfb and the Windows .NET runtime (HUB_WINE_PREFIX - a prefix from winshot --setup -,
HUB_DOTNET_WINDOWS) it is run for real."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.agent.loop import vision

from .test_agent import finish, new_chat, say
from .test_internet import answer, approvals, asking, result_of
from .test_people import add_person, client_from, sign_in, turn, user_msg

SANDBOX = Path(__file__).resolve().parents[2] / "services" / "sandbox"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"sandbox_{name}", SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


winshot = _load("winshot")
dotnet_windows = _load("dotnet_windows")


def png(w: int = 40, h: int = 30) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def fake_winshot(app, fakes, answer_: dict, shots: int = 2):
    """The sandbox runs winshot: it writes the screenshots where it was told and prints its JSON."""
    def handler(body):
        if not body["cmd"].startswith("winshot --json "):
            return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "duration_ms": 1, "truncated": False}
        req = json.loads(shlex.split(body["cmd"])[2])
        fakes.sandbox["winshot"] = req
        out = app.state.hub.env.workspace_dir / req["out"].removeprefix("/workspace/")
        out.mkdir(parents=True, exist_ok=True)
        for i in range(1, shots + 1):
            (out / f"{i}.png").write_bytes(png(40 + i, 30))
        return {"exit_code": 0, "stdout": "noise from dotnet\n" + json.dumps(answer_) + "\n", "stderr": "",
                "timed_out": False, "duration_ms": 9000, "truncated": False}
    fakes.sandbox["handler"] = handler


RAN = {"ok": True, "program": "PumpPanel.dll", "built": True,
       "shots": [{"file": "1.png", "label": "start", "width": 428, "height": 294,
                  "windows": [{"title": "Pump panel", "x": 0, "y": 0, "width": 428, "height": 294}]},
                 {"file": "2.png", "label": "end", "width": 428, "height": 294,
                  "windows": [{"title": "Pump panel", "x": 0, "y": 0, "width": 428, "height": 294},
                              {"title": "", "x": 20, "y": 131, "width": 200, "height": 41}]}],
       "steps": [{"step": 1, "did": 'clicked "Start" at (68, 196)'},
                 {"step": 2, "error": '"Stop" not found on the screen (OCR read: Pump 3 | Start)'}],
       "windows": [{"title": "Pump panel", "x": 0, "y": 0, "width": 428, "height": 294}],
       "output": "starting\n", "seconds": 9.4}


def call(fakes, args: dict, title: str = "Vindue") -> None:
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": "windows_app", "arguments": args}]}, {"content": "Set."},
                              {"content": title}]


def tool_names(fakes) -> set[str]:
    return {t["function"]["name"] for t in fakes.llama["requests"][0].get("tools") or []}


async def test_only_where_the_sandbox_has_wine(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "Hej."}, {"content": "Hej"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hej"))
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "windows_app" not in tool_names(fakes) and "cannot run here (no Windows, no screen)" in system
    fakes.sandbox["windows"] = True
    app.state.hub.extras.pop("windows_app", None)                    # asked again every few minutes
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Hej."}]
    await finish(app, await say(signed_in, c["id"], "Hej igen"))
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "windows_app" in tool_names(fakes)
    assert "windows_app runs one on a virtual screen (Wine)" in system and "cannot run here" not in system
    assert "-p:DefaultAppHostRuntimeIdentifier=win-x64 -o publish" in system


async def test_a_run_shows_its_screenshots(signed_in, app, fakes):
    fakes.sandbox["windows"] = True
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    (folder / "PumpPanel").mkdir(parents=True)
    (folder / "PumpPanel" / "PumpPanel.csproj").write_text("<Project/>")
    fake_winshot(app, fakes, RAN)
    steps = [{"click": "Start"}, {"click": "Stop"}]
    call(fakes, {"target": "PumpPanel", "steps": steps})
    run = await finish(app, await say(signed_in, c["id"], "Vis pumpepanelet"))
    res = result_of(run, "windows_app")
    req = fakes.sandbox["winshot"]
    assert req["target"] == f"/workspace/chats/{c['id']}/PumpPanel" and req["steps"] == steps
    assert req["screen"] == [1024, 768] and req["timeout"] == 60
    assert req["out"].startswith(f"/workspace/chats/{c['id']}/.hub/windows/")
    model = app.state.hub.catalog.get_llm(next(e for e in run.events if e["type"] == "started")["data"]["model"])
    assert req["ocr"] is (not vision(model))                         # text from the screen for a model that cannot see
    ex = fakes.sandbox["execs"][-1]
    assert ex["env"]["ArtifactsPath"] == f"/workspace/chats/{c['id']}/.hub/dotnet" and "offline" not in ex
    assert ex["timeout"] >= 60 + 300                                  # the build has its own time
    assert len(res["images"]) == 2 and res["ok"] is True              # a failed step is news, not a failed call
    text = res["text"]
    assert "Built and ran PumpPanel.dll." in text and 'Step 1: clicked "Start" at (68, 196)' in text
    assert 'Step 2: FAILED - "Stop" not found' in text
    assert 'Screenshot 2 (end): 428x294 px; windows: "Pump panel" at (0, 0) 428x294; "(menu or list)"' in text
    assert "The program was still running and was closed." in text and "starting" in text
    assert not (folder / ".hub" / "windows").exists() or not any((folder / ".hub" / "windows").iterdir())


async def test_steps_as_text_a_screen_size_and_bad_steps(signed_in, app, fakes):
    fakes.sandbox["windows"] = True
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "App.dll").write_bytes(b"MZ")
    fake_winshot(app, fakes, {**RAN, "built": False, "program": "App.dll", "steps": []}, shots=1)
    call(fakes, {"target": "App.dll", "steps": '[{"key": "alt+F4"}]', "screen": "1280x800", "timeout": 999})
    run = await finish(app, await say(signed_in, c["id"], "Luk den"))
    req = fakes.sandbox["winshot"]
    assert req["steps"] == [{"key": "alt+F4"}] and req["screen"] == [1280, 800] and req["timeout"] == 300
    assert "Ran App.dll." in result_of(run, "windows_app")["text"]
    n = len(fakes.sandbox["execs"])
    for bad in ({"target": "App.dll", "steps": [{"jump": 1}]}, {"target": "App.dll", "steps": "not json"},
                {"target": "../../etc"}, {"target": "Missing.csproj"}):
        call(fakes, bad)
        run = await finish(app, await say(signed_in, c["id"], "Igen"))
        assert not result_of(run, "windows_app")["ok"], bad
    assert len(fakes.sandbox["execs"]) == n                          # none of them reached the sandbox


async def test_no_answer_from_the_sandbox(signed_in, app, fakes):
    fakes.sandbox["windows"] = True
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "App.exe").write_bytes(b"MZ")
    fakes.sandbox["handler"] = lambda body: {"exit_code": 137, "stdout": "", "stderr": "Killed", "timed_out": True,
                                             "duration_ms": 9, "truncated": False}
    call(fakes, {"target": "App.exe"})
    run = await finish(app, await say(signed_in, c["id"], "Start"))
    res = result_of(run, "windows_app")
    assert not res["ok"] and "gave no result (timed out)" in res["text"] and "Killed" in res["text"]


async def test_asking_first_it_runs_offline(signed_in, app, fakes):
    """Like code: without the internet and without a question - unless internet for code is always allowed, or
    the sandbox cannot keep it off (then every run asks)."""
    asking(app)
    fakes.sandbox["windows"] = True
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "App.dll").write_bytes(b"MZ")
    fake_winshot(app, fakes, RAN, shots=1)
    call(fakes, {"target": "App.dll"})
    run = await finish(app, await say(signed_in, c["id"], "Kør"))
    assert not approvals(run) and fakes.sandbox["execs"][-1]["offline"] is True
    tools = {t["function"]["name"]: t["function"] for t in fakes.llama["requests"][0]["tools"]}
    assert "it runs offline" in tools["windows_app"]["description"]
    from app.agent.loop import remember_allowed
    remember_allowed("internet:code")
    call(fakes, {"target": "App.dll"})
    run = await finish(app, await say(signed_in, c["id"], "Kør igen"))
    assert not approvals(run) and "offline" not in fakes.sandbox["execs"][-1]
    assert (await signed_in.delete("/hub/api/tool-permissions/internet:code")).status_code == 200
    fakes.sandbox["offline"] = False
    app.state.hub.extras.pop("offline_code", None)
    call(fakes, {"target": "App.dll"})
    rid = await say(signed_in, c["id"], "Og igen")
    q = await answer(signed_in, app, rid, "allow")
    assert q["tool"] == "windows_app" and q["ask"]["what"] == "code" and q["ask"]["unblockable"] is True
    await finish(app, rid)
    assert "offline" not in fakes.sandbox["execs"][-1] and fakes.sandbox["execs"][-1]["end_leftovers"] is True


async def test_not_in_a_persons_chat(signed_in, app, fakes):
    fakes.sandbox["windows"] = True
    await add_person(signed_in, may_run_code=True)
    fakes.llama["replies"] = [{"content": "Hej."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        await turn(app, phone, {"title": "C#", "messages": [user_msg("Hej")]})
    assert "bash" in tool_names(fakes) and "windows_app" not in tool_names(fakes)
    assert "windows_app" not in fakes.llama["requests"][0]["messages"][0]["content"]


# ---------------------------------------------------------------- winshot.py, without Wine

def pe(machine: int, pe32plus: bool = True, clr: bool = False) -> bytes:
    """A minimal PE header: what pe_kind reads."""
    head = bytearray(1024)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", head, 0x84, machine)
    struct.pack_into("<H", head, 0x98, 0x20B if pe32plus else 0x10B)
    if clr:
        struct.pack_into("<I", head, 0x98 + (112 if pe32plus else 96) + 14 * 8, 0x2008)
    return bytes(head)


def test_which_programs_can_run(tmp_path):
    cases = {"x64.exe": (pe(0x8664), "x64"), "x86.exe": (pe(0x14C, False), "x86"),
             "netfx.exe": (pe(0x14C, False, clr=True), "netfx"), "arm.exe": (pe(0xAA64), "arm64"),
             "text.exe": (b"hello", "other")}
    for name, (data, kind) in cases.items():
        (tmp_path / name).write_bytes(data)
        assert winshot.pe_kind(str(tmp_path / name)) == kind, name
    cmd, folder, built = winshot.program(str(tmp_path / "x64.exe"))
    assert cmd == [winshot.winpath(str(tmp_path / "x64.exe"))] and folder == str(tmp_path) and not built
    for name, why in (("x86.exe", "not a 64-bit Windows program"), ("netfx.exe", ".NET Framework program")):
        with pytest.raises(winshot.Fail, match=why):
            winshot.program(str(tmp_path / name))
    (tmp_path / "Lib.dll").write_bytes(b"MZ")
    with pytest.raises(winshot.Fail, match="not a .NET program"):
        winshot.program(str(tmp_path / "Lib.dll"))
    (tmp_path / "App.dll").write_bytes(b"MZ")
    (tmp_path / "App.runtimeconfig.json").write_text("{}")
    cmd, _, _ = winshot.program(str(tmp_path / "App.dll"))
    assert cmd == [winshot.winpath(os.path.join(winshot.DOTNET_DIR, "dotnet.exe")), winshot.winpath(str(tmp_path / "App.dll"))]
    (tmp_path / "two").mkdir()
    for p in ("A.csproj", "B.csproj"):
        (tmp_path / "two" / p).write_text("<Project/>")
    with pytest.raises(winshot.Fail, match="more than one project"):
        winshot.program(str(tmp_path / "two"))
    assert winshot.winpath("/workspace/chats/7/App.dll") == "Z:\\workspace\\chats\\7\\App.dll"


def test_the_build_gives_its_program_or_its_errors(tmp_path, monkeypatch):
    proj = tmp_path / "PumpPanel.csproj"
    proj.write_text("<Project/>")
    dll = tmp_path / ".hub" / "dotnet" / "bin" / "PumpPanel" / "debug" / "PumpPanel.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        ok = seen.get("ok", True)
        log = (f"{tmp_path}/Form1.cs(12,23): warning CS0168: The variable 'x' is declared but never used "
               f"[{proj}]\n")
        if not ok:
            log += f"{tmp_path}/Form1.cs(14,5): error CS0246: The type 'Labell' could not be found [{proj}]\n"
        res = {"Properties": {"TargetPath": str(dll)}, "TargetResults": {"Build": {"Result": "Success" if ok else "Failure"}}}
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, log + json.dumps(res, indent=2) + "\n", "")

    monkeypatch.setattr(winshot.subprocess, "run", fake_run)
    target, warnings = winshot.build(str(proj))
    assert target == str(dll) and warnings == "Form1.cs(12,23): warning CS0168: The variable 'x' is declared but never used"
    assert "-getTargetResult:Build" in seen["cmd"] and "-getProperty:TargetPath" in seen["cmd"]
    seen["ok"] = False
    with pytest.raises(winshot.Fail) as exc:
        winshot.build(str(proj))
    assert "Form1.cs(14,5): error CS0246: The type 'Labell' could not be found" in str(exc.value)
    assert str(tmp_path) not in str(exc.value) and ".csproj]" not in str(exc.value)


XWININFO = """
xwininfo: Window id: 0x21f (the root window) (has no name)

  Root window id: 0x21f (the root window) (has no name)
  Parent window id: 0x0 (none)
     6 children:
     0xc00009 "Greeting": ("tester.exe" "tester.exe")  104x95+460+336  +460+336
     0xc00007 (has no name): ("tester.exe" "tester.exe")  104x26+10+51  +10+51
     0xc00005 (has no name): ("tester.exe" "tester.exe")  200x41+348+388  +348+388
     0x400009 (has no name): ("explorer.exe" "explorer.exe")  1024x20+0+748  +0+748
     0xc00004 "Default IME": ("tester.exe" "tester.exe")  1x1+0+0  +0+0
     0xc00003 "Tester: "quoted" name": ("tester.exe" "tester.exe")  368x254+328+257  +328+257
"""


def test_the_programs_windows(monkeypatch):
    def fake_quiet(cmd, env=None, timeout=30):
        if cmd[:3] == ["xwininfo", "-root", "-children"]:
            out = XWININFO
        else:                                                   # mapped: all but the hidden menu (0xc00007)
            out = "  Map State: " + ("IsUnMapped" if cmd[-1] == "0xc00007" else "IsViewable") + "\n"
        return subprocess.CompletedProcess(cmd, 0, out.encode(), b"")
    monkeypatch.setattr(winshot, "quiet", fake_quiet)
    wins = winshot.windows(":5")
    assert [(w["title"], w["x"], w["y"], w["width"], w["height"]) for w in wins] == [
        ('Tester: "quoted" name', 328, 257, 368, 254), ("", 348, 388, 200, 41), ("Greeting", 460, 336, 104, 95)]
    s = winshot.Session(":5", (1024, 768), "/tmp", False)
    s.origin = (328, 257)
    assert s.placed(wins)[2] == {"title": "Greeting", "x": 132, "y": 79, "width": 104, "height": 95}
    assert s.point([68, 196], "click")[:3] == (396, 453, "(68, 196)")
    with pytest.raises(winshot.Fail, match="off the screen"):
        s.point([800, 10], "click")
    with pytest.raises(winshot.Fail, match=r"needs \[x, y\] or a text"):
        s.point([True, 3], "click")


def words(*rows):
    out = []
    for n, row in enumerate(rows):
        x = 0
        for text in row.split():
            out.append({"text": text, "line": ("1", "1", "1", str(n)), "x": x, "y": 20 * n, "w": 8 * len(text), "h": 10})
            x += 8 * len(text) + 6
    return out


def test_finding_text_on_the_screen():
    w = words("File Edit", "Pump 3", "980 rpm", "Start Stop", "Automatic", "Connected to PLC 192.168.1.10")
    assert winshot.find_text(w, "Start") == (20, 65, "Start")
    assert winshot.find_text(w, "connected to plc")[2] == "Connected to PLC"      # case, several words
    assert winshot.find_text(w, "Auto")[2] == "Automatic"                         # the start of a word
    assert winshot.find_text(w, "192.168")[2] == "192.168.1.10"                   # inside a word (one word)
    assert winshot.find_text(w, "Pump 4") is None and winshot.find_text(w, "...") is None
    assert winshot.screen_text(w).splitlines()[3] == "Start Stop"


def test_the_prefix_settings():
    s = winshot.SETTINGS
    for line in ('"Version"="win81"', '"Decorated"="N"', '"Managed"="N"', '"ShowCrashDialog"=dword:00000000',
                 '"winedbg.exe"=""', '"winemenubuilder.exe"=""', '"mshtml"=""'):
        assert line in s, line
    assert "Explorer" not in s                                   # no virtual desktop
    assert winshot.decode("S\xf8ren\r\n".encode("cp1252")) == "Søren\n" and winshot.decode("Søren".encode()) == "Søren"


def test_check_says_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(winshot, "PREFIX", str(tmp_path / "no-prefix"))
    assert winshot.check() is False
    r = subprocess.run(["python3", "-I", str(SANDBOX / "winshot.py"), "--json", json.dumps({"target": "x"})],
                       capture_output=True, text=True, env={**os.environ, "HUB_WINE_PREFIX": str(tmp_path / "none")},
                       check=False)
    assert json.loads(r.stdout) == {"ok": False, "error": "Windows programs cannot run in this sandbox (Wine, the "
                                                          "Windows .NET runtime or the typing helper is missing)"}


def test_check_needs_the_typing_helper(monkeypatch, tmp_path):
    for name in ("wine64", "wineserver", "dotnet/dotnet.exe", "prefix/user.reg"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    for attr, name in (("WINE", "wine64"), ("WINESERVER", "wineserver"), ("DOTNET_DIR", "dotnet"), ("PREFIX", "prefix")):
        monkeypatch.setattr(winshot, attr, str(tmp_path / name))
    monkeypatch.setattr(winshot, "TOOLS", ("sh",))
    assert winshot.check() is False                              # a prefix from before 0.16.1
    (tmp_path / "prefix" / "hubkeys").mkdir()
    (tmp_path / "prefix" / "hubkeys" / "HubKeys.dll").write_bytes(b"MZ")
    assert winshot.check() is True and winshot.hubkeys() == str(tmp_path / "prefix" / "hubkeys" / "HubKeys.dll")


# ---------------------------------------------------------------- typing (0.16.1)

FAKE_HUBKEYS = r"""
import json, sys
mode, log = sys.argv[1], open(sys.argv[2], "a", encoding="utf-8")
if mode == "dies":
    sys.exit(3)
if mode == "silent":
    sys.stdin.read()
    sys.exit(0)
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    log.write(line)
    log.flush()
    if "shift" in req:
        ok = not (mode == "busy" and req["shift"])
        print(json.dumps({"ok": ok, "ms": 3 if ok else req["wait"]}), flush=True)
    elif mode == "refuses":
        print(json.dumps({"ok": False, "error": "SendInput did not take the keys (error 5)"}), flush=True)
    elif mode == "garbage":
        print("Unhandled exception", flush=True)
    elif mode == "quits":
        sys.exit(1)
    else:
        print(json.dumps({"ok": True, "sent": len(req["text"])}), flush=True)
"""


def _typist(tmp_path, mode: str):
    script = tmp_path / "fake_hubkeys.py"
    script.write_text(FAKE_HUBKEYS)
    log, xdo = tmp_path / "requests.jsonl", []
    t = winshot.Typist(":99", lambda *a: xdo.append(a), cmd=[sys.executable, "-I", str(script), mode, str(log)])
    return t, xdo, log


def _requests(log) -> list[dict]:
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def test_typing_goes_through_the_helper_behind_a_barrier(tmp_path):
    """Each text: Shift down through X, the helper waits until Wine has it, Shift up, the same for up, then the
    text - so a click or key sent through X before it cannot be overtaken. The helper starts once per run."""
    t, xdo, log = _typist(tmp_path, "ok")
    try:
        assert t.type("Søren: ÆØÅ æøå") == "typed 14 characters"
        assert t.type("😀\n") == "typed 2 characters"
    finally:
        t.close()
    assert _requests(log) == [{"shift": True, "wait": 5000}, {"shift": False, "wait": 5000},
                              {"text": "Søren: ÆØÅ æøå", "delay": 10},
                              {"shift": True, "wait": 5000}, {"shift": False, "wait": 5000},
                              {"text": "😀\n", "delay": 10}]
    assert xdo == [("keydown", "Shift_L"), ("keyup", "Shift_L")] * 2
    assert t.proc.returncode == 0                                # it ended at the end of its input


def test_typing_says_when_the_program_had_not_read_the_input_before_it(tmp_path):
    t, xdo, log = _typist(tmp_path, "busy")
    try:
        did = t.type("abc")
    finally:
        t.close()
    assert did == ("typed 3 characters - the program had not read the input before them within 5 s, so they may "
                   "have gone ahead of it")
    assert _requests(log) == [{"shift": True, "wait": 5000}, {"text": "abc", "delay": 10}]
    assert xdo == [("keydown", "Shift_L"), ("keyup", "Shift_L")]


@pytest.mark.parametrize("mode, error", [
    ("refuses", "could not type: SendInput did not take the keys (error 5)"),
    ("garbage", "could not type: the typing helper said b'Unhandled exception'"),
    ("quits", "could not type: the typing helper ended"),
    ("dies", "could not type: the typing helper ended"),
    ("silent", "could not type: the typing helper did not answer"),
])
def test_what_typing_says_when_the_helper_fails(tmp_path, monkeypatch, mode, error):
    monkeypatch.setattr(winshot, "HELPER_START_S", 1.0)
    monkeypatch.setattr(winshot, "ANSWER_S", 1.0)
    t, _, _ = _typist(tmp_path, mode)
    try:
        with pytest.raises(winshot.Fail) as exc:
            t.type("abc")
    finally:
        t.close()
    assert str(exc.value) == error


def test_the_typing_helpers_source():
    src = winshot.HUBKEYS_PROGRAM
    for part in ("SendInput", "KEYUP = 2, UNICODE = 4", "VkKeyScanW", "ToUnicode", "GetAsyncKeyState",
                 "\\r\\n is one Return", "char.IsHighSurrogate", "{\\\"ready\\\":true}"):
        assert part in src, part
    proj = winshot.HUBKEYS_PROJECT
    assert "<TargetFramework>net10.0</TargetFramework>" in proj and "<UseAppHost>false</UseAppHost>" in proj
    assert '"""' not in src


@pytest.mark.skipif(not shutil.which("dotnet"), reason="the .NET SDK")
def test_the_typing_helper_builds_offline(tmp_path):
    dll = winshot.build_hubkeys(str(tmp_path / "hubkeys"))
    assert dll == str(tmp_path / "hubkeys" / "HubKeys.dll") and os.path.isfile(dll)
    cfg = json.loads((tmp_path / "hubkeys" / "HubKeys.runtimeconfig.json").read_text())
    assert cfg["runtimeOptions"]["tfm"] == "net10.0"
    assert not (tmp_path / "hubkeys" / "HubKeys.exe").exists()  # dotnet.exe runs it: no app host


def test_the_self_test_types_danish_letters():
    """selftest's "windows programs" line: the probe types WINDOWS_TYPED, and its Close button says what arrived
    and sets the exit code (0: exactly that, 3: something else)."""
    from app import cli
    shot = [{"windows": [{"title": "SLQ Hub self-test"}]}]
    assert cli.WINDOWS_TYPED == "Søren: ÆØÅ æøå" and f'box.Text == "{cli.WINDOWS_TYPED}" ? 0 : 3' in cli.WINDOWS_PROBE["Program.cs"]
    status, name, what, _ = cli.windows_verdict({"ok": True, "exit_code": 0, "seconds": 10.5, "shots": shot,
                                                 "output": "typed: Søren: ÆØÅ æøå"}, True)
    assert (status, name) == ("PASS", "windows programs")
    assert what == ('a Windows Forms program built offline and ran under Wine; "Søren: ÆØÅ æøå" typed into it arrived '
                    'exactly, and its button, found by its text, closed it (10.5 s)')
    got = "\ufffdren: \ufffd\ufffd\ufffd \ufffd\ufffd\ufffd"                   # what 0.16.0's typing gave it
    status, _, what, hint = cli.windows_verdict({"ok": True, "exit_code": 3, "shots": shot,
                                                 "output": f"typed: {got}"}, True)
    assert status == "FAIL" and what == f'typing into a Windows program: "Søren: ÆØÅ æøå" arrived as "{got}"'
    assert "HubKeys" in hint
    assert cli.windows_verdict({"ok": True, "exit_code": None, "shots": shot, "steps": []}, False)[0] == "WARN"
    assert cli.windows_verdict({"ok": False, "error": "no window appeared within 90 s"}, False) == (
        "FAIL", "windows programs", "no window appeared within 90 s", "docker compose logs sandbox")


REAL_WINE = pytest.mark.skipif(os.environ.get("SLQ_TEST_WINE") != "1" or not shutil.which("dotnet"),
                               reason="SLQ_TEST_WINE=1 with Wine, Xvfb, xdotool, ImageMagick, Tesseract and dotnet")


def _real_run(tmp_path, program: str, steps: list[dict]) -> dict:
    proj = tmp_path / "Tester"
    proj.mkdir()
    (proj / "Tester.csproj").write_text(dotnet_windows.PROJECT.replace("<EnableWindowsTargeting>true"
                                                                       "</EnableWindowsTargeting>", ""))
    (proj / "Program.cs").write_text(program, encoding="utf-8")
    env = {**os.environ, "ArtifactsPath": str(proj / ".hub" / "dotnet"), "EnableWindowsTargeting": "true"}
    req = {"target": str(proj), "out": str(tmp_path / "shots"), "steps": steps}
    r = subprocess.run(["python3", "-I", str(SANDBOX / "winshot.py"), "--json", json.dumps(req)], env=env,
                       capture_output=True, text=True, timeout=600, check=False)
    return json.loads(r.stdout)


SAY = """
    static void Say(string text)
    {
        var bytes = System.Text.Encoding.UTF8.GetBytes(text + "\\n");
        using (var o = System.Console.OpenStandardOutput()) o.Write(bytes, 0, bytes.Length);
    }
"""


@REAL_WINE
def test_a_real_run_under_wine(tmp_path):
    """Build a Windows Forms program, type Danish letters and an emoji into it, click its button by its text, see
    the dialog - and the program says exactly what it got (in 0.16.0 Æ Ø Å arrived as U+FFFD, the first key was
    lost)."""
    res = _real_run(tmp_path, """
using System.Windows.Forms;
static class P {
    [System.STAThread] static void Main() {
        var f = new Form { Text = "Tester", Width = 360, Height = 200 };
        var box = new TextBox { Left = 16, Top = 16, Width = 200 };
        var b = new Button { Text = "Greet", Left = 230, Top = 14, Width = 90 };
        b.Click += (_, _) => { Say("got:" + box.Text); MessageBox.Show(f, "Hello " + box.Text, "Greeting"); };
        f.Controls.Add(box); f.Controls.Add(b);
        Application.Run(f);
    }
""" + SAY + "}\n", [{"click": [100, 50]}, {"type": "Søren: ÆØÅ æøå 😀"}, {"click": "Greet"}, {"shot": "dialog"},
                    {"key": "Return"}])
    assert res["ok"], res
    assert [s.get("error") for s in res["steps"]] == [None] * 5
    titles = [[w["title"] for w in s["windows"]] for s in res["shots"]]
    assert titles[1] == ["Tester", "Greeting"] and "Greeting" not in titles[-1]
    assert "got:Søren: ÆØÅ æøå 😀" in res["output"].splitlines()


@REAL_WINE
def test_real_typing_keeps_its_place_behind_a_click(tmp_path):
    """The first box is slow (150 ms of work per change), so it is still busy when the click on the second box
    is sent: the second text must not overtake that click in Wine's input queue (0.16.1's barrier)."""
    res = _real_run(tmp_path, """
using System.Windows.Forms;
static class P {
    [System.STAThread] static void Main() {
        var f = new Form { Text = "Two", Width = 560, Height = 150 };
        var a = new TextBox { Left = 12, Top = 12, Width = 520 };
        var b = new TextBox { Left = 12, Top = 52, Width = 520 };
        a.TextChanged += (_, _) => System.Threading.Thread.Sleep(150);
        f.FormClosing += (_, _) => Say("a:" + a.Text + "|b:" + b.Text);
        f.Controls.Add(a); f.Controls.Add(b);
        Application.Run(f);
    }
""" + SAY + "}\n", [{"click": [200, 45]}, {"type": "first ÆØÅ"}, {"click": [200, 85]}, {"type": "second æøå"},
                    {"wait": 4}, {"key": "alt+F4"}, {"wait": 1}])
    assert res["ok"], res
    assert "a:first ÆØÅ|b:second æøå" in res["output"].splitlines(), res


# ---------------------------------------------------------------- dotnet_windows.py

PROPS = """<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup>
    <KnownFrameworkReference Include="Microsoft.WindowsDesktop.App"
                              TargetFramework="net10.0"
                              TargetingPackName="Microsoft.WindowsDesktop.App.Ref"
                              TargetingPackVersion="10.0.13" />
    <KnownFrameworkReference Include="Microsoft.WindowsDesktop.App"
                              TargetFramework="net9.0"
                              TargetingPackVersion="9.0.21" />
    <KnownAppHostPack Include="Microsoft.NETCore.App"
                      TargetFramework="net9.0"
                      AppHostPackVersion="9.0.21" />
    <KnownAppHostPack Include="Microsoft.NETCore.App"
                      TargetFramework="net10.0"
                      AppHostPackNamePattern="Microsoft.NETCore.App.Host.**RID**"
                      AppHostPackVersion="10.0.13" />
  </ItemGroup>
</Project>
"""


def test_the_packs_go_where_the_sdk_asks(tmp_path):
    sdk = tmp_path / "sdk" / "10.0.113"
    sdk.mkdir(parents=True)
    (sdk / "Microsoft.NETCoreSdk.BundledVersions.props").write_text(PROPS)
    assert dotnet_windows.wanted(str(tmp_path)) == {"Microsoft.WindowsDesktop.App.Ref": "10.0.13",
                                                    "Microsoft.NETCore.App.Host.win-x64": "10.0.13"}
    assert all(len(sha) == 64 for _, sha in dotnet_windows.PACKAGES.values())
    assert all(name.endswith(".conda") for name, _ in dotnet_windows.PACKAGES.values())


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd")
def test_unpacking_takes_only_what_it_asks_for(tmp_path):
    def conda(name: str, members: dict[str, bytes]) -> Path:
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            for path, data in members.items():
                info = tarfile.TarInfo(path)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        zst = subprocess.run(["zstd", "-q", "-c"], input=tar_buf.getvalue(), capture_output=True, check=True).stdout
        p = tmp_path / f"{name}.conda"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("metadata.json", "{}")
            z.writestr(f"pkg-{name}.tar.zst", zst)
        return p

    good = conda("good", {"dotnet/packs/Microsoft.WindowsDesktop.App.Ref/10.0.12/data/FrameworkList.xml": b"<x/>",
                          "dotnet/sdk/10.0.401/dotnet.dll": b"big", "etc/conda/activate.d/x.sh": b"echo"})
    out = tmp_path / "out"
    out.mkdir()
    dotnet_windows.unpack(str(good), str(out), ("dotnet/packs/Microsoft.WindowsDesktop.App.Ref",))
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    assert files == ["dotnet/packs/Microsoft.WindowsDesktop.App.Ref/10.0.12/data/FrameworkList.xml"]
    evil = conda("evil", {"dotnet/../../escaped.txt": b"x"})
    with pytest.raises((tarfile.TarError, SystemExit)):
        dotnet_windows.unpack(str(evil), str(out), ("dotnet",))
    assert not (tmp_path / "escaped.txt").exists()
