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

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


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
