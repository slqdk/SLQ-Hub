"""windows_app: a Windows desktop program on a virtual screen in the sandbox (winshot there: Wine, Xvfb, xdotool),
built if it is a project, clicked and typed into, and shown to the model in screenshots."""
from __future__ import annotations

import io
import json
import re
import shlex
import shutil
import time

import httpx
from PIL import Image

from .base import ABOUT, Tool, ToolContext, ToolResult, clip, obj, register
from .shell import SandboxError, sandbox_exec

MAX_STEPS, MAX_SHOTS = 40, 8
BUILD_S = 300                     # winshot's own limit for dotnet build, on top of the run's timeout
SCREEN = (1024, 768)
STEP_KEYS = {"click", "drag", "scroll", "type", "key", "wait", "shot"}


async def refresh(hub) -> None:
    """Whether the sandbox can run Windows programs (its daemon's /health says windows: winshot --check), asked
    every few minutes - before a turn picks its tools and writes its prompt."""
    at, _ = hub.extras.get("windows_app", (float("-inf"), False))
    if time.monotonic() - at < 300:
        return
    try:
        async with httpx.AsyncClient(timeout=5, transport=hub.transports.sandbox) as c:
            r = await c.get(hub.env.sandbox_url + "/health")
        ok = r.json().get("windows") is True
    except (httpx.HTTPError, ValueError, AttributeError):
        ok = False
    hub.extras["windows_app"] = (time.monotonic(), ok)


def ready(hub) -> bool:
    return bool(hub.extras.get("windows_app", (0.0, False))[1])


def _available(ctx: ToolContext) -> bool:
    """The owner's chats (a person's turn folder could not hold Wine's prefix), where the sandbox has Wine."""
    return getattr(ctx.engine, "device", None) is None and ready(ctx.hub)


def _steps(raw) -> list[dict]:
    """The steps as winshot takes them. A model may send them as a JSON string. Raises ValueError."""
    if isinstance(raw, str):
        raw = json.loads(raw) if raw.strip() else []
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_STEPS:
        raise ValueError(f"steps must be a list of at most {MAX_STEPS} steps")
    out = []
    for i, step in enumerate(raw, 1):
        if not isinstance(step, dict) or not (set(step) & STEP_KEYS):
            raise ValueError(f"step {i} must be an object with one of: {', '.join(sorted(STEP_KEYS))}")
        out.append(step)
    return out


def _screen(raw) -> list[int]:
    m = re.fullmatch(r"\s*(\d{3,4})\s*[x×*]\s*(\d{3,4})\s*", str(raw or ""))
    if not m:
        return list(SCREEN)
    return [max(320, min(2560, int(m.group(1)))), max(240, min(1600, int(m.group(2))))]


def _sees(ctx: ToolContext) -> bool:
    from ..loop import vision
    return vision(ctx.model) or getattr(ctx.engine, "cloud", None) is not None


def _report(res: dict, shots: int) -> str:
    """winshot's answer in words."""
    lines = []
    if res.get("built"):
        lines.append(f"Built and ran {res.get('program')}.")
    elif res.get("program"):
        lines.append(f"Ran {res.get('program')}.")
    for s in res.get("steps") or []:
        lines.append(f"Step {s.get('step')}: " + (f"FAILED - {s['error']}" if s.get("error") else str(s.get("did"))))
    for i, shot in enumerate((res.get("shots") or [])[:shots], 1):
        wins = "; ".join(f"\"{w.get('title') or '(menu or list)'}\" at ({w.get('x')}, {w.get('y')}) "
                         f"{w.get('width')}x{w.get('height')}" for w in shot.get("windows") or [])
        lines.append(f"Screenshot {i}{' (' + shot['label'] + ')' if shot.get('label') else ''}: "
                     f"{shot.get('width')}x{shot.get('height')} px; windows: {wins or 'none'}")
        if shot.get("text"):
            lines.append("  Text on it (OCR): " + " | ".join(shot["text"].splitlines()))
    if res.get("exit_code") is not None:
        lines.append(f"The program ended with exit code {res['exit_code']}.")
    elif res.get("ok"):
        lines.append("The program was still running and was closed.")
    if res.get("error"):
        lines.append("Problem: " + str(res["error"]))
    if (res.get("output") or "").strip():
        lines.append("Its console output:\n" + clip(res["output"].strip(), 4000)[0])
    return "\n".join(lines)


async def windows_app(ctx: ToolContext, args: dict) -> ToolResult:
    wd = ctx.workdir
    target = str(args.get("target") or "").strip()
    if not target:
        return ToolResult("target is required: the program's project (.csproj or its folder) or a built .dll/.exe",
                          is_error=True)
    try:
        path = wd.resolve(target)
        steps = _steps(args.get("steps"))
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    if not path.exists():
        return ToolResult(f"No such file or folder in the working folder: {target}", is_error=True)
    prog_args = args.get("args") or []
    if not isinstance(prog_args, list):
        prog_args = shlex.split(str(prog_args))
    timeout = max(10, min(300, int(args.get("timeout") or 60)))
    run_id = re.sub(r"[^A-Za-z0-9_-]", "", ctx.call_id)[-32:] or str(int(time.time()))
    out_rel = f".hub/windows/{run_id}"
    rel = wd.rel(path)
    req = {"target": wd.sandbox_path if rel == "." else f"{wd.sandbox_path}/{rel}", "steps": steps,
           "args": [str(a) for a in prog_args][:50], "screen": _screen(args.get("screen")), "timeout": timeout,
           "out": f"{wd.sandbox_path}/{out_rel}", "ocr": not _sees(ctx)}
    try:
        res = await sandbox_exec(ctx, "winshot --json " + shlex.quote(json.dumps(req)), wd.sandbox_path,
                                 timeout + BUILD_S + 60, offline=not ctx.online, end_leftovers=ctx.online_once)
    except SandboxError as exc:
        return ToolResult(str(exc), is_error=True)
    try:
        lines = (res.get("stdout") or "").strip().splitlines()
        answer = json.loads(lines[-1]) if lines else None
        if not isinstance(answer, dict):
            raise ValueError("no answer")
    except ValueError:
        detail = clip(((res.get("stdout") or "") + "\n" + (res.get("stderr") or "")).strip(), 3000)[0]
        why = " (timed out)" if res.get("timed_out") else ""
        return ToolResult(f"The Windows run gave no result{why}, exit code {res.get('exit_code')}:\n{detail}",
                          is_error=True)
    images = []
    try:
        for i in range(1, min(MAX_SHOTS, len(answer.get("shots") or [])) + 1):
            p = wd.resolve(f"{out_rel}/{i}.png")      # only where winshot was told to write, inside the folder
            if not p.is_file() or p.stat().st_size > 20_000_000:
                break
            data = p.read_bytes()
            with Image.open(io.BytesIO(data)) as img:
                img.verify()
            from .images import _shown
            images.append(_shown(data, f"windows-{i}.png"))
    except (OSError, ValueError, Image.DecompressionBombError):
        pass
    finally:
        try:
            shutil.rmtree(wd.resolve(out_rel), ignore_errors=True)
        except ValueError:
            pass
    wd.checkin("agent", "sandbox", f"windows_app: {target[:120]}")
    text = _report(answer, len(images))
    if images and _sees(ctx):
        text += "\nThe screenshots are shown below; click coordinates are pixels in them."
    return ToolResult(text, images=images, is_error=not answer.get("ok"))


TEXT = ("Run a Windows desktop program (Windows Forms or WPF on .NET 10) on a virtual screen in the sandbox and "
        "see it. target: its project (.csproj, or the folder with it - built first) or a built .dll or .exe in the "
        "working folder. You get a screenshot when its first window is up, at each shot step, and once at the end if "
        "it is still open. steps "
        "act on it in order: {\"click\": [x, y]} or {\"click\": \"OK\"} (finds that text on the screen), with "
        "\"double\": true or \"button\": \"right\"; {\"type\": \"text\"}; {\"key\": \"Return\"} (or \"ctrl+s\", "
        "\"Tab Tab space\", \"alt+F4\"); {\"drag\": [x1, y1, x2, y2]}; {\"scroll\": [x, y, 3]}; {\"wait\": 2}; "
        "{\"shot\": \"label\"}. Coordinates are pixels in the screenshots (0, 0 is the top-left corner of the "
        "program's first window). Each call starts the program afresh and closes it at the end, so give every step "
        "for what you want to see in one call. It runs under Wine (64-bit, as Windows 8.1): close to Windows, not "
        "identical - fonts and some controls look a little different, so the user should still try it on a real "
        "PC.")
PARAMS = obj({"about": ABOUT, "target": {"type": "string", "description": "Project (.csproj) or its folder, or a built .dll/.exe"},
              "steps": {"type": "array", "items": {"type": "object"},
                        "description": "e.g. [{\"click\": [120, 80]}, {\"type\": \"42\"}, {\"click\": \"Start\"}, "
                                       "{\"shot\": \"running\"}]"},
              "args": {"type": "array", "items": {"type": "string"},
                       "description": "Command-line arguments for the program"},
              "screen": {"type": "string", "description": "Screen size, default 1024x768"},
              "timeout": {"type": "integer", "description": "Seconds for the run after the build (default 60, "
                                                            "max 300)"}},
             ["target"])


ASKS = (" While the user has you ask before going online, it runs offline: a plain Windows Forms or WPF build "
        "works (its packs are in the image), but a project that pulls other NuGet packages fails - restore those "
        "first with a bash call and internet: true.")


def _dynamic(ctx: ToolContext) -> tuple[str, dict]:
    from .. import internet
    return (TEXT + ASKS if internet.asking(ctx.settings) else TEXT), PARAMS


register(Tool("windows_app", TEXT, PARAMS, windows_app, available=_available, dynamic=_dynamic))
