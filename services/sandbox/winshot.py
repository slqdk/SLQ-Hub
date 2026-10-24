#!/usr/bin/env python3
"""Run a Windows program on a virtual screen and look at it (stdlib only, with Wine, Xvfb, xdotool, ImageMagick
and Tesseract from the image). The Hub's windows_app tool runs it in the sandbox like a bash command.

    winshot --json REQUEST     run a program; REQUEST is JSON (below), the result is JSON on stdout
    winshot --setup PREFIX     make the Wine prefix (the image build does this, as the sandbox user)
    winshot --check            exit code 0 when everything it needs is in place

REQUEST: {"target": path, "args": [str], "steps": [step], "screen": [w, h], "timeout": s, "out": dir, "ocr": bool}

The target is a project (.csproj, .vbproj or .fsproj, or a folder with one; built with dotnet build first, then
its .dll runs), a .NET .dll (run by the Windows build of the .NET 10 runtime in /opt/dotnet-win) or a 64-bit .exe
(the .exe a .NET build makes next to its .dll finds that runtime by DOTNET_ROOT). The steps, run one by one:

    {"click": [x, y]} or {"click": "OK"} (text found on the screen by OCR); "button": "right"/"middle",
                                          "double": true
    {"drag": [x1, y1, x2, y2]}            {"scroll": [x, y, n]}   (n > 0: down, < 0: up)
    {"type": "text"}                      {"key": "ctrl+s"} or {"key": "Tab Tab Return"}   (xdotool key names)
    {"wait": seconds}                     {"shot": "label"}

Coordinates are screen pixels, the same as in the screenshots: every screenshot starts at the top-left corner of
the program's first window (so that corner is (0, 0)). A screenshot is taken when that window has appeared, at
every shot step, and after the last step; each one covers the program's windows (popups, menus and dialogs too).
ocr adds the text Tesseract reads in each screenshot (for a model that cannot see pictures).

How Wine runs here (all set once, in the prefix): 64-bit only (no 32-bit programs); no virtual desktop - each
window is an X window of its own, so menus and drop-down lists open where they belong (in Wine 9's virtual
desktop a window that moved itself, as WinForms' CenterScreen does, was drawn at twice its position, and its
menus opened at the real one); Wine draws Windows' frames itself (X11 Driver\\Decorated=N, there is no window
manager); and programs are told it is Windows 8.1 - as Windows 10, opening a WinForms menu ended the program
("Failed to get thread's DpiAwareness context", Wine 9.0 with .NET 10). The copies of Wine's own DLLs that
wineboot puts in the prefix are links to the originals, so the prefix is ~20 MB instead of ~700. One program
at a time (a lock in the prefix); whatever Wine started is ended afterwards (wineserver -k).
"""
from __future__ import annotations

import fcntl
import filecmp
import glob
import json
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time

WINE = "/usr/lib/wine/wine64"
WINESERVER = "/usr/lib/wine/wineserver"
PREFIX = os.environ.get("HUB_WINE_PREFIX", "/opt/hub-wine")
DOTNET_DIR = os.environ.get("HUB_DOTNET_WINDOWS", "/opt/dotnet-win")
TOOLS = ("Xvfb", "xdotool", "xwininfo", "import", "convert", "tesseract")
PROJECTS = (".csproj", ".vbproj", ".fsproj")
SCREEN = (1024, 768)
MAX_STEPS, MAX_SHOTS, MAX_TEXT = 40, 8, 2000
BUILD_TIMEOUT = 300
TYPE_KEYS = {"\n": 0xFF0D, "\r": 0xFF0D, "\t": 0xFF09}      # Return, Return, Tab (the rest go by code point)
NOT_PROGRAM_WINDOWS = ("explorer.exe",)          # Wine's own hidden desktop and tray
SETTINGS = r"""REGEDIT4

[HKEY_CURRENT_USER\Software\Wine]
"Version"="win81"

[HKEY_CURRENT_USER\Software\Wine\X11 Driver]
"Decorated"="N"
"Managed"="N"

[HKEY_CURRENT_USER\Software\Wine\WineDbg]
"ShowCrashDialog"=dword:00000000

[HKEY_CURRENT_USER\Software\Wine\DllOverrides]
"winemenubuilder.exe"=""
"winedbg.exe"=""
"mshtml"=""

"""


class Fail(Exception):
    """Something to tell the model, in words (no traceback)."""


def winpath(path: str) -> str:
    """A Unix path as Wine sees it (drive Z: is /)."""
    return "Z:" + os.path.abspath(path).replace("/", "\\")


def wine_env(display: str = "") -> dict:
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    env.update(WINEPREFIX=PREFIX, WINEDEBUG="-all", WINEDLLOVERRIDES="winemenubuilder.exe=d;mshtml=",
               DOTNET_ROOT=winpath(DOTNET_DIR), DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")
    if display:
        env["DISPLAY"] = display
    return env


def quiet(cmd: list[str], env: dict | None = None, timeout: float = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, env=env, timeout=timeout, stdin=subprocess.DEVNULL, capture_output=True,
                              check=False)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, -1, b"", b"timed out")


# ---------------------------------------------------------------- what to run

def pe_kind(path: str) -> str:
    """'x64', 'x86', 'arm64', 'netfx' (a .NET Framework program: IL with a CLR header) or 'other'."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return "other"
    if len(head) < 64 or head[:2] != b"MZ":
        return "other"
    off = struct.unpack_from("<I", head, 0x3C)[0]
    if off + 26 > len(head) or head[off:off + 4] != b"PE\0\0":
        return "other"
    machine, magic = struct.unpack_from("<H", head, off + 4)[0], struct.unpack_from("<H", head, off + 24)[0]
    dirs = off + 24 + (96 if magic == 0x10B else 112)
    clr = struct.unpack_from("<I", head, dirs + 14 * 8)[0] if dirs + 15 * 8 <= len(head) else 0
    if clr:
        return "netfx"
    return {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64"}.get(machine, "other")


def find_project(folder: str) -> str:
    found = sorted(f for f in os.listdir(folder) if f.endswith(PROJECTS))
    if len(found) == 1:
        return os.path.join(folder, found[0])
    if not found:
        raise Fail(f"no project file (.csproj) in {folder}")
    raise Fail(f"more than one project in {folder} ({', '.join(found)}): give the one to run")


def build(project: str) -> tuple[str, str]:
    """dotnet build -> (the .dll it made, the warnings). Raises Fail with the errors."""
    cmd = ["dotnet", "build", project, "-nologo", "-tl:off", "-getProperty:TargetPath", "-getTargetResult:Build"]
    try:
        p = subprocess.run(cmd, cwd=os.path.dirname(project), stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=BUILD_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise Fail(f"the build took longer than {BUILD_TIMEOUT} s") from exc
    out = p.stdout or ""
    start = re.search(r"^\{\s*$", out, re.MULTILINE)
    log = (out[:start.start()] if start else out) + (p.stderr or "")
    try:
        res = json.loads(out[start.start():]) if start else {}
    except ValueError:
        res = {}
    ok = p.returncode == 0 and (res.get("TargetResults") or {}).get("Build", {}).get("Result") == "Success"
    target = (res.get("Properties") or {}).get("TargetPath") or ""
    folder = os.path.dirname(project) + os.sep
    lines = list(dict.fromkeys(re.sub(r"\s+\[[^\]]+\.(?:cs|vb|fs)proj\]$", "", ln.strip()).replace(folder, "")
                               for ln in log.splitlines() if ln.strip()))
    if not ok:
        raise Fail("the build failed:\n" + tail("\n".join(lines) or f"dotnet build exited with {p.returncode}", 6000))
    if not target.endswith(".dll") or not os.path.isfile(target):
        raise Fail(f"the build made no .dll to run ({target or 'no TargetPath'})")
    return target, tail("\n".join(ln for ln in lines if ": warning " in ln), 3000)


def program(target: str) -> tuple[list[str], str, bool]:
    """(the command after wine64, the folder to run in, whether it was built)."""
    target = os.path.abspath(target)
    built = False
    if os.path.isdir(target):
        target = find_project(target)
    if target.endswith(PROJECTS):
        if not os.path.isfile(target):
            raise Fail(f"no such project: {target}")
        target, _ = build(target)
        built = True
    if not os.path.isfile(target):
        raise Fail(f"no such file: {target}")
    folder = os.path.dirname(target)
    if target.lower().endswith(".dll"):
        if not os.path.isfile(target[:-4] + ".runtimeconfig.json"):
            raise Fail(f"{os.path.basename(target)} is not a .NET program (no .runtimeconfig.json next to it); "
                       "give its project, or the .dll a build made")
        return [winpath(os.path.join(DOTNET_DIR, "dotnet.exe")), winpath(target)], folder, built
    if not target.lower().endswith(".exe"):
        raise Fail("the target must be a project, a .dll or an .exe")
    kind = pe_kind(target)
    if kind == "netfx":
        raise Fail(f"{os.path.basename(target)} is a .NET Framework program: only .NET 6 and later run here "
                   "(build it for net10.0-windows)")
    if kind != "x64":
        raise Fail(f"{os.path.basename(target)} is not a 64-bit Windows program ({kind}): only x64 programs run here")
    return [winpath(target)], folder, built


# ---------------------------------------------------------------- the screen

def start_screen(width: int, height: int) -> tuple[subprocess.Popen, str]:
    """Xvfb on a free display (it picks one and says which); no TCP."""
    r, w = os.pipe()
    p = subprocess.Popen(["Xvfb", "-displayfd", str(w), "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp",
                          "-noreset"], pass_fds=(w,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    os.close(w)
    buf, deadline = b"", time.monotonic() + 15
    try:
        while not buf.endswith(b"\n"):
            ready, _, _ = select.select([r], [], [], max(0.0, deadline - time.monotonic()))
            chunk = os.read(r, 64) if ready else b""
            if not chunk:
                p.kill()
                raise Fail("the virtual screen (Xvfb) did not start")
            buf += chunk
    finally:
        os.close(r)
    return p, ":" + buf.decode().strip()


GEOMETRY = re.compile(r':\s+\((.*)\)\s+(\d+)x(\d+)\+(-?\d+)\+(-?\d+)\s+[+-]-?\d+[+-]-?\d+\s*$')


def windows(display: str) -> list[dict]:
    """The program's windows on the screen, top-level and mapped, in stacking order (bottom first)."""
    env = {**os.environ, "DISPLAY": display}
    res = quiet(["xwininfo", "-root", "-children"], env, 10)
    out = []
    for line in res.stdout.decode("utf-8", "replace").splitlines():
        m = re.match(r"\s+(0x[0-9a-f]+) (.*)$", line)
        g = GEOMETRY.search(line) if m else None
        if not g:
            continue
        name = m.group(2)[:m.group(2).rfind(": (")] if ": (" in m.group(2) else ""
        name = name[1:-1] if name.startswith('"') and name.endswith('"') else ""
        cls = g.group(1).split('" "')[-1].strip('"').lower()
        w, h, x, y = (int(v) for v in g.group(2, 3, 4, 5))
        if w <= 2 or h <= 2 or cls in NOT_PROGRAM_WINDOWS:
            continue
        info = quiet(["xwininfo", "-id", m.group(1)], env, 10).stdout.decode("utf-8", "replace")
        if "Map State: IsViewable" not in info:
            continue
        out.append({"id": m.group(1), "title": name, "x": x, "y": y, "width": w, "height": h})
    out.reverse()                                    # xwininfo lists the top one first
    return out


# How a screenshot is made ready for Tesseract: three times larger (UI text is small). On a whole screenshot the
# first is the best single one on Wine's Windows Forms and WPF text (23 of 24 words in the tests); the others
# read what it misses (sharper edges; light text on dark - a title bar). A list or menu that opened (a window of
# its own) is read best alone, thresholded around each spot (-lat): its highlighted row otherwise hides the rows
# under it from Tesseract. Clicks by text try these in turn.
WHOLE = (("-filter", "Triangle", "-resize", "300%"), ("-resize", "300%", "-unsharp", "0x2"))
ALONE = ("-filter", "Triangle", "-resize", "300%", "-lat", "30x30-5%")
NEGATE = ("-filter", "Triangle", "-resize", "300%", "-negate")


def ocr_words(png: str, prep: tuple[str, ...] = WHOLE[0], box: tuple[int, int, int, int] | None = None) -> list[dict]:
    """Words Tesseract reads in a screenshot - or in a box (x, y, width, height) of it - with their boxes in
    screenshot pixels."""
    big = png[:-4] + "-ocr.png"
    crop = ["-crop", "{2}x{3}+{0}+{1}".format(*box), "+repage"] if box else []
    quiet(["convert", png, *crop, "-colorspace", "Gray", *prep, big], timeout=30)
    res = quiet(["tesseract", big, "-", "--psm", "11", "-l", "eng+dan", "-c", "tessedit_do_invert=0", "tsv"],
                timeout=60)
    try:
        os.unlink(big)
    except OSError:
        pass
    dx, dy = (box[0], box[1]) if box else (0, 0)
    words = []
    for row in res.stdout.decode("utf-8", "replace").splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or not re.search(r"\w", cols[11]):
            continue
        try:
            if float(cols[10]) < 40:
                continue
            left, top, width, height = (int(c) / 3 for c in cols[6:10])
        except ValueError:
            continue
        words.append({"text": cols[11].strip(), "line": (box, *cols[1:5]), "x": left + dx, "y": top + dy,
                      "w": width, "h": height})
    return words


def screen_text(words: list[dict]) -> str:
    lines: dict = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w)
    rows = sorted(lines.values(), key=lambda ws: (min(w["y"] for w in ws), min(w["x"] for w in ws)))
    return "\n".join(" ".join(w["text"] for w in sorted(ws, key=lambda w: w["x"])) for ws in rows)


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def find_text(words: list[dict], text: str) -> tuple[int, int, str] | None:
    """Where `text` is on the screen: the middle of the first place it is read, as a whole word sequence, else as
    the start of words, else (one word) inside a word."""
    want = [_norm(t) for t in text.split() if _norm(t)]
    if not want:
        return None
    lines: dict = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w)
    rows = sorted((sorted(ws, key=lambda w: w["x"]) for ws in lines.values()),
                  key=lambda ws: (min(w["y"] for w in ws), ws[0]["x"]))
    tests = (lambda got, exp: got == exp, lambda got, exp: got.startswith(exp))
    if len(want) == 1:
        tests += (lambda got, exp: exp in got,)
    for test in tests:
        for ws in rows:
            for i in range(len(ws) - len(want) + 1):
                run = ws[i:i + len(want)]
                if all(test(_norm(w["text"]), exp) for w, exp in zip(run, want, strict=True)):
                    x0, y0 = min(w["x"] for w in run), min(w["y"] for w in run)
                    x1, y1 = max(w["x"] + w["w"] for w in run), max(w["y"] + w["h"] for w in run)
                    return round((x0 + x1) / 2), round((y0 + y1) / 2), " ".join(w["text"] for w in run)
    return None


_XLIB = None


def _xlib():
    """libX11 and libXtst (already in the image as xdotool's own dependencies), set up once for type_text."""
    global _XLIB
    if _XLIB is None:
        import ctypes
        import ctypes.util
        x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        xtst = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        ks = ctypes.c_ulong
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XGetKeyboardMapping.restype = ctypes.POINTER(ks)
        x11.XGetKeyboardMapping.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        x11.XChangeKeyboardMapping.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ks),
                                               ctypes.c_int]
        x11.XDisplayKeycodes.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XFree.argtypes = [ctypes.POINTER(ks)]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        _XLIB = (ctypes, x11, xtst, ks)
    return _XLIB


def type_text(display: str, text: str, delay: float = 0.012) -> None:
    """Type text into the focused window as exact Unicode code points. A spare key code is remapped to each
    character's keysym at every shift level, so pressing it with XTEST needs no Shift and cannot be read at the
    wrong case. (xdotool's own `type` breaks a letter like AE into its Latin-1 pair and presses no Shift, so Æ
    arrives as æ; and in a non-UTF-8 locale it refuses non-ASCII outright. Going by code point sidesteps both.)"""
    ctypes, x11, xtst, KeySym = _xlib()
    dpy = x11.XOpenDisplay(display.encode())
    if not dpy:
        raise Fail("could not open the screen to type")
    try:
        lo, hi = ctypes.c_int(), ctypes.c_int()
        x11.XDisplayKeycodes(dpy, ctypes.byref(lo), ctypes.byref(hi))
        per = ctypes.c_int()
        count = hi.value - lo.value + 1
        arr = x11.XGetKeyboardMapping(dpy, lo.value, count, ctypes.byref(per))
        width = per.value
        spare = next((lo.value + i for i in range(count) if all(arr[i * width + j] == 0 for j in range(width))),
                     hi.value)
        x11.XFree(arr)

        def remap(sym: int) -> None:
            row = (KeySym * width)(*([sym] * width))
            x11.XChangeKeyboardMapping(dpy, spare, width, row, 1)
            x11.XSync(dpy, 0)

        for c in text:
            remap(TYPE_KEYS.get(c, 0x01000000 | ord(c)))
            xtst.XTestFakeKeyEvent(dpy, spare, 1, 0)
            xtst.XTestFakeKeyEvent(dpy, spare, 0, 0)
            x11.XSync(dpy, 0)
            time.sleep(delay)
        remap(0)                                     # leave the spare key code as we found it
    finally:
        x11.XCloseDisplay(dpy)


class Session:
    """One program on one screen: its windows, screenshots and the steps. Screenshots and coordinates start at the
    top-left corner of the program's first window (the origin): a click at (x, y) is that pixel of a screenshot."""

    def __init__(self, display: str, screen: tuple[int, int], out: str, ocr: bool):
        self.display, self.screen, self.out, self.ocr = display, screen, out, ocr
        self.env = {**os.environ, "DISPLAY": display}
        self.origin = (0, 0)
        self.shots: list[dict] = []

    def xdo(self, *args: str) -> None:
        res = quiet(["xdotool", *args], self.env, 30)
        if res.returncode != 0:
            raise Fail("xdotool: " + (res.stderr.decode("utf-8", "replace").strip() or f"exit {res.returncode}"))

    def placed(self, wins: list[dict]) -> list[dict]:
        """The windows where the screenshots show them."""
        ox, oy = self.origin
        return [{"title": w["title"], "x": w["x"] - ox, "y": w["y"] - oy, "width": w["width"], "height": w["height"]}
                for w in wins]

    def grab(self, path: str) -> tuple[int, int, list[dict]] | None:
        """A screenshot from the origin to the right and bottom edges of the program's windows."""
        wins = windows(self.display)
        if not wins:
            return None
        ox, oy = self.origin
        width = max(40, min(self.screen[0], max(w["x"] + w["width"] for w in wins)) - ox)
        height = max(40, min(self.screen[1], max(w["y"] + w["height"] for w in wins)) - oy)
        res = quiet(["import", "-window", "root", "-crop", f"{width}x{height}+{ox}+{oy}", "+repage", path],
                    self.env, 30)
        if res.returncode != 0 or not os.path.isfile(path):
            raise Fail("could not take a screenshot: " + res.stderr.decode("utf-8", "replace").strip()[:300])
        return width, height, wins

    def boxes(self, wins: list[dict], width: int, height: int) -> list[tuple[dict, tuple[int, int, int, int]]]:
        """The windows (the top one first) and their boxes in a screenshot of that size."""
        out = []
        for w in reversed(wins):
            x0, y0 = max(0, w["x"] - self.origin[0]), max(0, w["y"] - self.origin[1])
            x1 = min(width, w["x"] - self.origin[0] + w["width"])
            y1 = min(height, w["y"] - self.origin[1] + w["height"])
            if x1 - x0 > 4 and y1 - y0 > 4:
                out.append((w, (x0, y0, x1 - x0, y1 - y0)))
        return out

    def shot(self, label: str) -> str:
        if len(self.shots) >= MAX_SHOTS:
            return f"no screenshot ({MAX_SHOTS} is the most for one run)"
        path = os.path.join(self.out, f"{len(self.shots) + 1}.png")
        got = self.grab(path)
        if got is None:
            return "no screenshot: no window is open"
        width, height, wins = got
        item = {"file": os.path.basename(path), "label": label, "width": width, "height": height,
                "windows": self.placed(wins)}
        if self.ocr:                                 # each window read alone, the top one first
            parts = []
            for w, box in self.boxes(wins, width, height):
                text = screen_text(ocr_words(path, WHOLE[0] if w["title"] else ALONE, box))
                if text:
                    parts.append(f"[{w['title'] or 'menu or list'}] " + " / ".join(text.splitlines()))
            item["text"] = "\n".join(parts)
        self.shots.append(item)
        return f"screenshot {len(self.shots)}"

    def locate(self, text: str) -> tuple[int, int, str]:
        """Where a text is in a screenshot taken now (OCR)."""
        path = os.path.join(self.out, "find.png")
        read: list[list[dict]] = []
        try:
            got = self.grab(path)
            if got is None:
                raise Fail(f'"{text}" not found: no window is open')
            width, height, wins = got
            tries = [(prep, None) for prep in WHOLE] + [(ALONE, box) for _, box in self.boxes(wins, width, height)]
            for prep, box in tries + [(NEGATE, None)]:
                read.append(ocr_words(path, prep, box))
                hit = find_text(read[-1], text)
                if hit is not None:
                    return hit
        finally:
            if os.path.exists(path):
                os.unlink(path)
        seen = tail(screen_text(read[0]).replace("\n", " | "), 400)
        raise Fail(f'"{text}" not found on the screen (OCR read: {seen or "nothing"})')

    def point(self, value, what: str) -> tuple[int, int, str, str]:
        """(x, y on the screen, the same in screenshot pixels as words, the text found there)."""
        found = ""
        if isinstance(value, str):
            x, y, found = self.locate(value)
        elif (isinstance(value, (list, tuple)) and len(value) == 2
              and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
            if not all(-1e9 < v < 1e9 for v in value):       # also rejects inf and nan (every comparison is false)
                raise Fail(f"{what} needs numbers for [x, y]")
            x, y = int(value[0]), int(value[1])
        else:
            raise Fail(f"{what} needs [x, y] or a text")
        sx, sy = x + self.origin[0], y + self.origin[1]
        if not (0 <= sx < self.screen[0] and 0 <= sy < self.screen[1]):
            raise Fail(f"{what} ({x}, {y}) is off the screen")
        return sx, sy, f"({x}, {y})", found

    def step(self, s: dict) -> str:
        if "click" in s:
            x, y, at, found = self.point(s["click"], "click")
            button = {"left": "1", "middle": "2", "right": "3"}.get(str(s.get("button") or "left").lower())
            if button is None:
                raise Fail("button is left, middle or right")
            repeat = ["--repeat", "2", "--delay", "120"] if s.get("double") else []
            self.xdo("mousemove", "--sync", str(x), str(y), "click", *repeat, button)
            time.sleep(0.6)
            what = ("double-clicked" if s.get("double") else "right-clicked" if button == "3" else "clicked")
            return f'{what} "{found}" at {at}' if found else f"{what} at {at}"
        if "drag" in s:
            v = s["drag"]
            if not (isinstance(v, (list, tuple)) and len(v) == 4):
                raise Fail("drag needs [x1, y1, x2, y2]")
            x1, y1, at1, _ = self.point(list(v[:2]), "drag start")
            x2, y2, at2, _ = self.point(list(v[2:]), "drag end")
            moves = []
            for i in range(1, 9):
                moves += ["mousemove", "--sync", str(x1 + (x2 - x1) * i // 8), str(y1 + (y2 - y1) * i // 8),
                          "sleep", "0.03"]
            self.xdo("mousemove", "--sync", str(x1), str(y1), "mousedown", "1", "sleep", "0.1", *moves, "mouseup", "1")
            time.sleep(0.6)
            return f"dragged from {at1} to {at2}"
        if "scroll" in s:
            v = s["scroll"]
            if not (isinstance(v, (list, tuple)) and len(v) == 3 and isinstance(v[2], int)):
                raise Fail("scroll needs [x, y, n]")
            x, y, at, _ = self.point(list(v[:2]), "scroll")
            n = max(-50, min(50, v[2]))
            if n:
                self.xdo("mousemove", "--sync", str(x), str(y), "click", "--repeat", str(abs(n)), "--delay", "40",
                         "5" if n > 0 else "4")
            time.sleep(0.5)
            return f"scrolled {abs(n)} {'down' if n > 0 else 'up'} at {at}"
        if "type" in s:
            text = str(s["type"])
            if len(text) > MAX_TEXT:
                raise Fail(f"type takes at most {MAX_TEXT} characters")
            if "\x00" in text:
                raise Fail("type text cannot contain a null byte")
            type_text(self.display, text)
            time.sleep(0.4)
            return f"typed {len(text)} characters"
        if "key" in s:
            keys = str(s["key"]).split()
            if not keys or len(keys) > 20 or not all(re.fullmatch(r"[A-Za-z0-9_+]{1,40}", k) for k in keys):
                raise Fail('key takes xdotool key names, like "Return", "ctrl+s" or "Tab Tab space"')
            self.xdo("key", "--delay", "80", "--", *keys)
            time.sleep(0.6)
            return "pressed " + " ".join(keys)
        if "wait" in s:
            try:
                secs = max(0.0, min(30.0, float(s["wait"])))
            except (TypeError, ValueError) as exc:
                raise Fail("wait takes seconds") from exc
            time.sleep(secs)
            return f"waited {secs:g} s"
        if "shot" in s:
            return self.shot(str(s["shot"]) if isinstance(s["shot"], str) else "")
        raise Fail("a step is one of click, drag, scroll, type, key, wait, shot")


def tail(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def decode(data: bytes) -> str:
    """A Windows program writes in its console code page, not UTF-8."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", "replace")
    return text.replace("\r\n", "\n")


class PrefixLock:
    """One program at a time: they would share Wine's server, and wineserver -k ends all of them."""

    def __init__(self, deadline: float):
        self.deadline = deadline

    def __enter__(self):
        self.f = open(os.path.join(PREFIX, ".winshot.lock"), "a+")      # held until __exit__
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() > self.deadline:
                    self.f.close()
                    raise Fail("another Windows program is running in the sandbox; try again in a moment") from None
                time.sleep(0.5)

    def __exit__(self, *exc):
        self.f.close()


def end_wine() -> None:
    env = wine_env()
    quiet([WINESERVER, "-k"], env, 20)
    quiet([WINESERVER, "-w"], env, 20)


def run(req: dict) -> dict:
    t0 = time.monotonic()
    result: dict = {"ok": False, "shots": [], "steps": [], "windows": []}
    steps = req.get("steps") or []
    if not isinstance(steps, list) or len(steps) > MAX_STEPS or not all(isinstance(s, dict) for s in steps):
        raise Fail(f"steps must be a list of at most {MAX_STEPS} objects")
    args = req.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise Fail("args must be a list of strings")
    try:
        width, height = (int(v) for v in (req.get("screen") or SCREEN))
    except (TypeError, ValueError) as exc:
        raise Fail("screen is [width, height]") from exc
    screen = (max(320, min(2560, width)), max(240, min(1600, height)))
    timeout = max(10.0, min(600.0, float(req.get("timeout") or 60)))
    out = os.path.abspath(req.get("out") or os.path.join(os.getcwd(), ".hub", "windows"))
    os.makedirs(out, exist_ok=True)
    for old in glob.glob(os.path.join(out, "*.png")):
        os.unlink(old)
    cmd, folder, built = program(str(req.get("target") or "."))
    result.update(program=cmd[-1].split("\\")[-1], built=built)
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as log, PrefixLock(deadline):
        end_wine()                                   # anything a killed run left behind
        xvfb, display = start_screen(*screen)
        proc = s = None
        try:
            proc = subprocess.Popen([WINE, *cmd, *args], cwd=folder, env=wine_env(display), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT)
            s = Session(display, screen, out, bool(req.get("ocr")))
            first = None
            while time.monotonic() < deadline and proc.poll() is None:
                wins = windows(display)
                if wins:
                    first = next((w for w in wins if w["title"]), wins[0])
                    break
                time.sleep(0.25)
            if first is None:
                if proc.poll() is not None:
                    result["error"] = f"the program ended (exit code {proc.returncode}) without opening a window"
                else:
                    result["error"] = f"no window appeared within {timeout:.0f} s"
            else:
                s.origin = (max(0, first["x"]), max(0, first["y"]))
                time.sleep(0.8)                      # let it paint
                s.shot("start")
                for i, step in enumerate(steps, 1):
                    if proc.poll() is not None:
                        result["steps"].append({"step": i, "error": "not done: the program had ended"})
                        break
                    if time.monotonic() > deadline:
                        result["steps"].append({"step": i, "error": f"not done: the {timeout:.0f} s were up"})
                        break
                    try:
                        result["steps"].append({"step": i, "did": s.step(step)})
                    except Fail as exc:
                        result["steps"].append({"step": i, "error": str(exc)})
                        break
                if steps and "shot" not in steps[-1] and proc.poll() is None:
                    s.shot("end")
                result["windows"] = s.placed(windows(display)) if proc.poll() is None else []
                result["ok"] = True
        finally:
            if s is not None:
                result["shots"] = s.shots
            if proc is not None and proc.poll() is not None:
                result["exit_code"] = proc.returncode
            end_wine()
            if proc is not None and proc.poll() is None:
                proc.kill()
            xvfb.terminate()
            try:
                xvfb.wait(5)
            except subprocess.TimeoutExpired:
                xvfb.kill()
        log.seek(0)
        result["output"] = tail(decode(log.read()), 6000)
    result["seconds"] = round(time.monotonic() - t0, 1)
    return result


# ---------------------------------------------------------------- the image build

def setup(prefix: str) -> None:
    """A new prefix: wineboot (without Wine's Mono and Gecko, no menu entries), the settings above, and links in
    place of the copies of Wine's DLLs. Run as the user that will use it (Wine wants the prefix to be its own)."""
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    env.update(WINEPREFIX=prefix, WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d")
    for cmd in ([WINE, "wineboot", "-i"], [WINESERVER, "-w"]):
        subprocess.run(cmd, env=env, check=True, timeout=600, stdin=subprocess.DEVNULL)
    reg = os.path.join(prefix, "hub-settings.reg")
    with open(reg, "w", encoding="ascii", newline="\r\n") as f:
        f.write(SETTINGS)
    for cmd in ([WINE, "regedit", "/S", winpath(reg)], [WINESERVER, "-w"]):
        subprocess.run(cmd, env=env, check=True, timeout=120, stdin=subprocess.DEVNULL)
    with open(os.path.join(prefix, "user.reg"), encoding="utf-8", errors="replace") as f:
        user = f.read()
    for want in ('"Version"="win81"', '"Decorated"="N"', '"Managed"="N"', '"ShowCrashDialog"=dword:00000000'):
        if want not in user:
            raise SystemExit(f"winshot --setup: {want} did not reach the registry")
    builtin = (glob.glob("/usr/lib/*/wine/x86_64-windows") + glob.glob("/usr/lib/wine/x86_64-windows"))[0]
    linked = size = 0
    for root, _, files in os.walk(os.path.join(prefix, "drive_c", "windows")):
        for name in files:
            path, orig = os.path.join(root, name), os.path.join(builtin, name)
            if not os.path.islink(path) and os.path.isfile(orig) and filecmp.cmp(path, orig, shallow=False):
                size += os.path.getsize(path)
                os.unlink(path)
                os.symlink(orig, path)
                linked += 1
    print(f"winshot --setup: prefix {prefix}, {linked} DLLs ({size // 2**20} MB) linked to {builtin}")


def check() -> bool:
    need = (WINE, WINESERVER, os.path.join(DOTNET_DIR, "dotnet.exe"), os.path.join(PREFIX, "user.reg"))
    if not (all(os.path.exists(p) for p in need) and all(shutil.which(t) for t in TOOLS)):
        return False
    try:
        _xlib()                                      # libX11 and libXtst, for type_text
    except OSError:
        return False
    return True


def main(argv: list[str]) -> int:
    if argv[:1] == ["--check"]:
        return 0 if check() else 1
    if argv[:1] == ["--setup"] and len(argv) == 2:
        setup(argv[1])
        return 0
    if argv[:1] != ["--json"] or len(argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        req = json.loads(sys.stdin.read() if argv[1] == "-" else argv[1])
        if not isinstance(req, dict):
            raise Fail("the request must be a JSON object")
        if not check():
            raise Fail("Windows programs cannot run in this sandbox (Wine or the Windows .NET runtime is missing)")
        result = run(req)
    except Fail as exc:
        result = {"ok": False, "error": str(exc)}
    except ValueError as exc:
        result = {"ok": False, "error": f"bad request: {exc}"}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
