"""Tool plumbing: what a tool is, what it gets, what it returns."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:  # pragma: no cover
    from ...gpu.scheduler import Ticket
    from ...projects.store import Workdir
    from ...state import Hub
    from ..runs import Run


@dataclass
class ToolResult:
    text: str
    images: list[dict] = field(default_factory=list)      # [{"sha", "mime"}] shown to the model and the UI
    is_error: bool = False
    display: dict | None = None                            # extra for the UI (artifact ref, generated images)
    # The card or ComfyUI failed, not the call: it does not count towards a hand-over (a stronger model would meet
    # the same card - the test run of 2026-10-25 handed a video turn over after three out-of-memory failures).
    hub_fault: bool = False


OOM_NOTE = (" The graphics card ran out of memory: the same call fails the same way, so do not repeat it - ask for "
            "less (smaller, shorter) or tell the user.")


def gpu_failure(what: str, exc: Exception) -> ToolResult:
    """A picture, sound or video job that failed: `what` failed: why. A failure on the card or in ComfyUI (the
    pipeline's error says hub=True, or it ran out of memory) is the Hub's, and running out of memory says what to
    do instead of trying again - each try of a 720p video took 10-13 minutes before it ran out at the end."""
    text = f"{what}: {exc}"
    hub = bool(getattr(exc, "hub", False))
    if "out of memory" in text.lower():
        text += OOM_NOTE
        hub = True
    return ToolResult(text, is_error=True, hub_fault=hub)


@dataclass
class ToolContext:
    hub: "Hub"
    run: "Run"
    chat: dict
    project: dict | None
    workdir: "Workdir"
    ticket: "Ticket | None"
    model: dict
    depth: int = 0                                        # 1 inside a subagent
    call_id: str = ""
    state: dict = field(default_factory=dict)             # per-turn scratch (todo list, ...)
    engine: Any = None                                    # the Engine running this tool (subagents need it)
    online: bool = True                                   # False: bash runs without the internet (ask first)
    online_once: bool = False                             # online by "Allow once": leftovers are ended

    def emit(self, kind: str, **data) -> None:
        self.run.emit(kind, **data)

    @property
    def settings(self):
        return self.hub.settings


Handler = Callable[[ToolContext, dict], Awaitable[ToolResult]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Handler
    needs_approval: bool = False
    available: Callable[[ToolContext], bool] = lambda ctx: True
    in_subagent: bool = True
    group: str = "core"
    # Only looks things up: the same call again, with nothing changed in between, gives the same result
    # (the loop refuses such repeats - small models can get stuck repeating a call).
    read_only: bool = False
    # Changes nothing, by its server's word (an MCP tool's readOnlyHint, 0.36.0), but unlike read_only the same call
    # may give another result later - a build after the user changed a file on their PC, a live value. Only for
    # choosing the independent review's tools; the loop does not refuse its repeats.
    only_looks: bool = False
    # The arguments that name files in the working folder whose content goes with the call (MCP, 0.36.0): the
    # approval card lists those files.
    sends_files: tuple[str, ...] = ()
    # Description and parameters that depend on the turn (which models are downloaded, the chat's choice).
    dynamic: Callable[[ToolContext], tuple[str, dict]] | None = None

    def schema(self, ctx: ToolContext | None = None) -> dict:
        description, parameters = self.description, self.parameters
        if self.dynamic is not None and ctx is not None:
            try:
                description, parameters = self.dynamic(ctx)
            except Exception:  # noqa: BLE001 - the static schema is a fine fallback
                pass
        return {"type": "function", "function": {"name": self.name, "description": description,
                                                 "parameters": parameters}}


REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.25):]
    return f"{head}\n\n... [{len(text) - len(head) - len(tail)} characters omitted] ...\n\n{tail}", True
