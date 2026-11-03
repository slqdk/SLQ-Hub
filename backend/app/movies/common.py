"""What the parts of Movies share: the error the routes turn into an answer, and small helpers for text, numbers and
stamps (studio.py makes the pictures and clips, sound.py the sound and the cut, cut.py says what goes where)."""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import time

PRIORITY = "normal"                        # chats go first, then movies, then background work
NARRATOR = "Narrator"


class MovieError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> int:
    return int(time.time())


def _id(prefix: str) -> str:
    return prefix + secrets.token_hex(3)


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _para(value, limit: int) -> str:
    """Text that keeps its line breaks (a premise, the rules)."""
    lines = [" ".join(x.split()) for x in str(value or "").replace("\r", "").split("\n")]
    return "\n".join(lines).strip()[:limit]


def _stamp(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _number(value, default: float) -> float:
    """A finite number, or the default (NaN and infinity from a hand-made request too)."""
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


def _whole(value, lo: int, hi: int, default: int) -> int:
    return int(max(lo, min(hi, _number(value, default))))


def _given(value, what: str) -> float:
    """A number the owner gave: refused when it is none (NaN and infinity from a hand-made request too)."""
    v = _number(value, math.nan)
    if math.isnan(v):
        raise MovieError(f"{what} must be a number.")
    return v


def _by_id(items: list[dict], iid: str) -> dict | None:
    return next((x for x in items if x.get("id") == iid), None)
