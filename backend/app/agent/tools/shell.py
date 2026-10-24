"""bash in the sandbox container."""
from __future__ import annotations

import httpx

from .base import Tool, ToolContext, ToolResult, clip, obj, register


class SandboxError(RuntimeError):
    pass


async def sandbox_exec(ctx: ToolContext, cmd: str, cwd: str, timeout: int, offline: bool = False,
                       end_leftovers: bool = False) -> dict:
    hub = ctx.hub
    headers = {"X-Internal-Token": hub.env.internal_token} if hub.env.internal_token else {}
    # .NET builds into .hub/dotnet (ignored by sync and the files card), not bin/ and obj/ beside the project:
    # nothing of a build reaches the user's PC through sync or fights with the build there.
    body: dict = {"cmd": cmd, "cwd": cwd, "timeout": timeout, "max_output": 200_000,
                  "env": {"ArtifactsPath": f"{ctx.workdir.sandbox_path}/.hub/dotnet"}}
    if offline:                       # internet for the assistant asks first: no network (Landlock + seccomp)
        body["offline"] = True
    if end_leftovers:                 # online once: nothing it starts stays online after it
        body["end_leftovers"] = True
    if getattr(ctx.engine, "device", None) is not None:
        # A person's chat: the command may touch only this turn's temporary folder (Landlock in the sandbox;
        # refused there when it cannot be enforced), with a home of its own inside it.
        root = ctx.workdir.sandbox_path
        body["confine"] = {"root": root, "home": f"{root}/.hub/home", "require": True}
    try:
        async with httpx.AsyncClient(timeout=timeout + 30, transport=hub.transports.sandbox) as c:
            r = await c.post(hub.env.sandbox_url + "/exec", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise SandboxError(f"the sandbox is not answering ({exc.__class__.__name__})") from exc
    if r.status_code >= 400:
        try:
            msg = r.json().get("error") or r.text
        except ValueError:
            msg = r.text
        raise SandboxError(f"the sandbox refused: {msg}")
    return r.json()


async def bash(ctx: ToolContext, args: dict) -> ToolResult:
    cmd = (args.get("command") or "").strip()
    if not cmd:
        return ToolResult("command is required", is_error=True)
    s = ctx.settings.get("sandbox")
    timeout = max(1, min(int(args.get("timeout") or s.get("timeout_s", 120)), int(s.get("max_timeout_s", 1800))))
    sub = args.get("cwd") or "."
    wd = ctx.workdir
    target = wd.resolve(sub)
    cwd = f"{wd.sandbox_path}/{wd.rel(target)}".rstrip("/.") if wd.rel(target) != "." else wd.sandbox_path
    offline = not ctx.online
    try:
        res = await sandbox_exec(ctx, cmd, cwd, timeout, offline=offline, end_leftovers=ctx.online_once)
    except SandboxError as exc:
        return ToolResult(str(exc), is_error=True)
    wd.checkin("agent", "sandbox", f"bash: {cmd[:120]}")
    limit = int(ctx.settings.get("agent", "tool_output_chars") or 12000)
    out, _ = clip(res.get("stdout") or "", limit)
    err, _ = clip(res.get("stderr") or "", limit // 2)
    parts = [f"exit code {res.get('exit_code')}" + (" (TIMED OUT - killed)" if res.get("timed_out") else "")
             + f", {res.get('duration_ms', 0) / 1000:.1f} s"]
    if out:
        parts.append("stdout:\n" + out)
    if err:
        parts.append("stderr:\n" + err)
    failed = bool(res.get("timed_out")) or res.get("exit_code") not in (0, None)
    if offline and failed:
        parts.append(OFFLINE_NOTE)
    return ToolResult("\n".join(parts), is_error=failed)


OFFLINE_NOTE = ("(This command ran without the network - no connections, no DNS: the user has the assistant ask "
                "before it goes online. If it needs the internet - pip install, npm, a dotnet build that restores NuGet "
                "packages, git clone, curl - run it again with internet: true, and the user is asked.)")
BASH_TEXT = ("Run a bash command in the sandbox (Ubuntu with Python 3 + numpy/pandas/matplotlib/pillow/opencv, "
             "Node.js, the .NET 10 SDK (C#), git, curl, ImageMagick, ffmpeg, Tesseract OCR, poppler). It starts in "
             "the working folder, "
             "has internet access but no access to the local network, and runs as an unprivileged user. Each call "
             "is a fresh shell (cd and variables do not carry over; files do). Use it to run code, tests, "
             "conversions and installs (pip install --user is not needed: pip install works).")
BASH_PARAMS = {"command": {"type": "string"},
               "timeout": {"type": "integer", "description": "Seconds (default 120, max 1800)"},
               "cwd": {"type": "string", "description": "Sub-folder of the working folder to start in"}}


def _bash_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    from .. import internet
    if not internet.asking(ctx.settings):
        return BASH_TEXT, obj(BASH_PARAMS, ["command"])
    text = BASH_TEXT.replace("has internet access but no access to the local network",
                             "has no access to the local network and, unless the call says internet: true, no "
                             "network at all (no connections, not even to localhost, and no DNS)")
    return (text + " The user has the assistant ask before it goes online: set internet: true only for a command "
            "that needs it (pip install, npm install, NuGet packages for dotnet, git clone, curl, downloads) - the user "
            "is asked first. "
            "Allowed once, whatever the command leaves running is stopped when it ends.",
            obj({**BASH_PARAMS, "internet": {"type": "boolean", "description": "The command needs the internet "
                                                                                "(the user is asked)."}},
                ["command"]))


register(Tool("bash", BASH_TEXT, obj(BASH_PARAMS, ["command"]), bash, dynamic=_bash_dynamic))
