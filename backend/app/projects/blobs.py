"""A content-addressed file store: data/blobs/<aa>/<sha256>. A file is
stored once however often it is checked in."""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Callable

log = logging.getLogger("hub.blobs")
SHA = re.compile(r"^[0-9a-f]{64}$")
_root: Path | None = None
_on_store: list[Callable[[str], None]] = []


def configure(root: Path) -> None:
    global _root
    _root = root
    root.mkdir(parents=True, exist_ok=True)


def _path(sha: str) -> Path:
    if _root is None:
        raise RuntimeError("blobs.configure() has not been called")
    if not isinstance(sha, str) or not SHA.match(sha):
        raise ValueError("not a sha256")
    return _root / sha[:2] / sha


def on_store(hook: Callable[[str], None]) -> None:
    """Call hook(sha) after every store - a person's chat uses it to learn that a file it would delete when it is
    done is now kept by someone else too."""
    _on_store.append(hook)


def path(sha: str) -> Path:
    return _path(sha)


def store(data: bytes) -> str:
    sha = hashlib.sha256(data).hexdigest()
    target = _path(sha)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + f".tmp{os.getpid()}")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    for hook in list(_on_store):
        try:
            hook(sha)
        except Exception:  # noqa: BLE001 - storing worked; a listener's problem is its own
            log.exception("a blob listener failed")
    return sha


def read(sha: str) -> bytes:
    try:
        return _path(sha).read_bytes()
    except ValueError:
        raise FileNotFoundError(sha) from None


def exists(sha: str) -> bool:
    try:
        return _path(sha).is_file()
    except ValueError:
        return False


def delete(sha: str) -> None:
    try:
        _path(sha).unlink()
    except (FileNotFoundError, ValueError):
        pass


def size(sha: str) -> int:
    try:
        return _path(sha).stat().st_size
    except (OSError, ValueError):
        return 0
