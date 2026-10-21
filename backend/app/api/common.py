"""Shared by the three API formats: who is calling, which model they mean,
and a turn on the card."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Request

from ..gpu.scheduler import Ticket
from ..state import Hub, get_hub


@dataclass
class Caller:
    owner: str            # the API key's name, or "web" for a signed-in browser
    priority: str
    via: str              # "key" | "session"


def caller(request: Request) -> Caller:
    c = getattr(request.state, "caller", None)
    return c or Caller(owner="anonymous", priority="normal", via="none")


def resolve_model(hub: Hub, name: str | None) -> dict | None:
    fallback = hub.settings.get("models", "fallback_alias") or ""
    mid = hub.catalog.resolve(name, fallback)
    entry = hub.catalog.get_llm(mid) if mid else None
    if entry and not hub.catalog.installed(entry):
        return None
    return entry


async def acquire_llm(hub: Hub, who: Caller, model_id: str, label: str) -> Ticket:
    max_wait = float(hub.settings.get("queue", "max_wait_s") or 1800)
    ticket = await hub.scheduler.acquire(who.owner, "api", hub.model_need(model_id), who.priority,
                                         label=label, meta={"model": model_id}, max_wait=max_wait)
    task = asyncio.current_task()
    if task is not None:
        ticket.job.meta["cancel"] = task.cancel        # the queue drawer's Stop button
    return ticket


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ndjson(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


def sse(obj: dict, event: str | None = None) -> bytes:
    head = f"event: {event}\n" if event else ""
    return (head + "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()


class Clock:
    """Wall-clock marks for Ollama-style durations (nanoseconds)."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.admitted = self.t0

    def admit(self) -> None:
        self.admitted = time.monotonic()

    def ns(self, a: float, b: float) -> int:
        return int((b - a) * 1e9)


def hub_of(request: Request) -> Hub:
    return get_hub(request)
