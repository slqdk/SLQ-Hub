"""bash in the sandbox container."""
from __future__ import annotations

import httpx

from .base import Tool, ToolContext, ToolResult, clip, obj, register


class SandboxError(RuntimeError):
    pass


async def sandbox_exec(ctx: ToolContext, cmd: str, cwd: str, timeout: int) -> dict:
    hub = ctx.hub
    headers = {"X-Internal-Token": hub.env.internal_token} if hub.env.internal_token else {}
    body: dict = {"cmd": cmd, "cwd": cwd, "timeout": timeout, "max_output": 200_000}
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
    try:
        res = await sandbox_exec(ctx, cmd, cwd, timeout)
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
    return ToolResult("\n".join(parts), is_error=bool(res.get("timed_out")) or res.get("exit_code") not in (0, None))


register(Tool(
    "bash",
    "Run a bash command in the sandbox (Ubuntu with Python 3 + numpy/pandas/matplotlib/pillow/opencv, Node.js, "
    "git, curl, ImageMagick, ffmpeg, Tesseract OCR, poppler). It starts in the working folder, has internet "
    "access but no access to the local network, and runs as an unprivileged user. Each call is a fresh shell "
    "(cd and variables do not carry over; files do). Use it to run code, tests, conversions and installs "
    "(pip install --user is not needed: pip install works).",
    obj({"command": {"type": "string"},
         "timeout": {"type": "integer", "description": "Seconds (default 120, max 1800)"},
         "cwd": {"type": "string", "description": "Sub-folder of the working folder to start in"}}, ["command"]),
    bash))
