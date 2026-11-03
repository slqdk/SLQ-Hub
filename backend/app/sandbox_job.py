"""A job's command in the sandbox (0.72.0 Movies' preview and cut, 0.74.0 voice clips): in a folder of its own under
the workspace - the Hub's own folders are out of the sandbox's reach - offline, and the file it made read back
without following a link that sandboxed code could have put in its place. The folder is removed by the caller."""
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path


class SandboxJobError(Exception):
    pass


def tmp_folder(hub, kind: str) -> Path:
    """A fresh folder <workspace>/<kind>/<token> (kind: "movies-tmp", "voice-tmp")."""
    from .projects import store as projects
    return projects.real_dir(hub.env.workspace_dir, kind, secrets.token_hex(6))


def sweep(hub, kind: str) -> None:
    """At start-up: what a job that a restart cut off left in <workspace>/<kind>."""
    shutil.rmtree(Path(hub.env.workspace_dir) / kind, ignore_errors=True)


async def run(hub, folder: Path, cmd: str, timeout: int, out_name: str, failed: str) -> bytes:
    """Run `cmd` in the sandbox in `folder`, offline, and read the file it made. SandboxJobError ("<failed>: why")
    when the command failed, ran out of time or left nothing - or a link - behind."""
    from .agent.tools.base import ToolContext
    from .agent.tools.shell import SandboxError, sandbox_exec
    from .projects import store as projects
    ws = hub.env.workspace_dir
    wd = projects.Workdir(folder, None, ws, hub.env.sandbox_workspace)
    ctx = ToolContext(hub=hub, run=None, chat={}, project=None, workdir=wd, ticket=None, model={})
    try:
        res = await sandbox_exec(ctx, cmd, wd.sandbox_path, timeout, offline=True)
    except SandboxError as exc:
        raise SandboxJobError(f"{failed}: {exc}") from exc
    err = " ".join(str(res.get("stderr") or "").split())[-300:]
    if res.get("exit_code") != 0 or res.get("timed_out"):
        raise SandboxJobError(f"{failed}{': ' + err if err else ''}.")
    try:                                       # not through a link the sandbox could have put in its place
        with os.fdopen(wd.open_inside(out_name), "rb") as fh:
            data = fh.read()
    except (OSError, ValueError):
        data = b""
    if not data:
        raise SandboxJobError(f"{failed}: it left nothing.")
    return data
