"""A content-addressed file store: data/blobs/<aa>/<sha256>. A file is
stored once however often it is checked in."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

_root: Path | None = None


def configure(root: Path) -> None:
    global _root
    _root = root
    root.mkdir(parents=True, exist_ok=True)


def _path(sha: str) -> Path:
    if _root is None:
        raise RuntimeError("blobs.configure() has not been called")
    return _root / sha[:2] / sha


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
    return sha


def read(sha: str) -> bytes:
    return _path(sha).read_bytes()


def exists(sha: str) -> bool:
    return bool(sha) and _path(sha).is_file()


def size(sha: str) -> int:
    try:
        return _path(sha).stat().st_size
    except OSError:
        return 0
