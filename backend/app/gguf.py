"""A GGUF file's header, read just far enough to learn its architecture
(general.architecture: qwen35, llama, gemma3, ...) and the context it was
trained for ({arch}.context_length). llama.cpp writes the architecture first
and the model's numbers before the tokenizer's long lists; the reader gives up
on anything unexpected."""
from __future__ import annotations

import functools
import struct
from pathlib import Path

_SCALAR = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}   # value type -> bytes
_STRING, _ARRAY = 8, 9


def architecture(path: Path) -> str | None:
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            (version,) = struct.unpack("<I", f.read(4))
            if version < 2:
                return None
            _tensors, kvs = struct.unpack("<QQ", f.read(16))
            for _ in range(min(kvs, 64)):
                key = _string(f)
                (vtype,) = struct.unpack("<I", f.read(4))
                if key == "general.architecture" and vtype == _STRING:
                    return _string(f) or None
                if not _skip(f, vtype):
                    return None
    except (OSError, struct.error, UnicodeDecodeError, ValueError):
        return None
    return None


_INTS = {4: "<I", 5: "<i", 10: "<Q", 11: "<q"}             # uint32, int32, uint64, int64


def context_length(path: Path) -> int | None:
    arch = None
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            (version,) = struct.unpack("<I", f.read(4))
            if version < 2:
                return None
            _tensors, kvs = struct.unpack("<QQ", f.read(16))
            for _ in range(min(kvs, 256)):
                key = _string(f)
                (vtype,) = struct.unpack("<I", f.read(4))
                if key == "general.architecture" and vtype == _STRING:
                    arch = _string(f)
                    continue
                if arch and key == f"{arch}.context_length" and vtype in _INTS:
                    (n,) = struct.unpack(_INTS[vtype], f.read(_SCALAR[vtype]))
                    return int(n) if n > 0 else None
                if not _skip(f, vtype):
                    return None
    except (OSError, struct.error, UnicodeDecodeError, ValueError):
        return None
    return None


@functools.lru_cache(maxsize=64)
def context_length_cached(path: str, stamp: int) -> int | None:
    """context_length, read once per version of the file (stamp: its modification time)."""
    return context_length(Path(path))


def _string(f) -> str:
    (n,) = struct.unpack("<Q", f.read(8))
    if n > 1 << 20:
        raise ValueError("not a GGUF string")
    return f.read(n).decode("utf-8")


def _skip(f, vtype: int) -> bool:
    if vtype in _SCALAR:
        f.seek(_SCALAR[vtype], 1)
        return True
    if vtype == _STRING:
        (n,) = struct.unpack("<Q", f.read(8))
        f.seek(n, 1)
        return True
    if vtype == _ARRAY:
        (etype,) = struct.unpack("<I", f.read(4))
        (count,) = struct.unpack("<Q", f.read(8))
        if etype in _SCALAR:
            f.seek(_SCALAR[etype] * count, 1)
            return True
        if etype == _STRING and count <= 4096:
            for _ in range(count):
                (n,) = struct.unpack("<Q", f.read(8))
                f.seek(n, 1)
            return True
    return False
