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


# ---------------------------------------------------------------- the whole header (0.62.0: files uploaded)

_TEXT_MAX = 256 * 1024                    # a chat template is a few KB; nothing longer is kept


class _Short(Exception):
    """The file ends before its header does: an upload that is still arriving."""


def _read(f, n: int) -> bytes:
    b = f.read(n)
    if len(b) < n:
        raise _Short()
    return b


def _value(f, vtype: int):
    """One value; arrays are skipped (the tokenizer's lists are long) - None for them."""
    if vtype in _SCALAR:
        raw = _read(f, _SCALAR[vtype])
        fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q",
               12: "<d"}[vtype]
        return struct.unpack(fmt, raw)[0]
    if vtype == _STRING:
        (n,) = struct.unpack("<Q", _read(f, 8))
        if n > _TEXT_MAX:
            f.seek(n, 1)
            return None
        return _read(f, n).decode("utf-8", "replace")
    if vtype == _ARRAY:
        (etype,) = struct.unpack("<I", _read(f, 4))
        (count,) = struct.unpack("<Q", _read(f, 8))
        if etype in _SCALAR:
            f.seek(_SCALAR[etype] * count, 1)
        elif etype == _STRING:
            for _ in range(count):
                (n,) = struct.unpack("<Q", _read(f, 8))
                f.seek(n, 1)
        else:
            raise ValueError("nested arrays")
        return None
    raise ValueError(f"unknown value type {vtype}")


def describe(path: Path) -> dict | None:
    """What a GGUF file is - for a model uploaded from the owner's computer, where no Hugging Face page says it:
    its architecture ("clip" for a vision projector), general.name, general.type, the context it was trained for,
    its chat template, and its parameters, summed from its tensors. None when it is not a GGUF the Hub can read;
    {"partial": True} when the file stops before its metadata does (an upload still arriving). `parameters` is
    missing when only the tensor list is cut off."""
    out: dict = {}
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if _read(f, 4) != b"GGUF":
                return None
            (version,) = struct.unpack("<I", _read(f, 4))
            if version < 2:
                return None
            tensors, kvs = struct.unpack("<QQ", _read(f, 16))
            if tensors > 1_000_000 or kvs > 100_000:
                return None
            meta: dict = {}
            for _ in range(kvs):
                (n,) = struct.unpack("<Q", _read(f, 8))
                if n > 4096:
                    return None
                key = _read(f, n).decode("utf-8", "replace")
                (vtype,) = struct.unpack("<I", _read(f, 4))
                v = _value(f, vtype)
                if v is not None:
                    meta[key] = v
                if f.tell() > size:
                    raise _Short()
            arch = str(meta.get("general.architecture") or "")
            ctx = meta.get(f"{arch}.context_length")
            out = {"architecture": arch or None, "name": meta.get("general.name"), "type": meta.get("general.type"),
                   "size_label": meta.get("general.size_label"),
                   "context_length": int(ctx) if isinstance(ctx, int) and ctx > 0 else None,
                   "chat_template": meta.get("tokenizer.chat_template")}
            total = 0
            for _ in range(tensors):                     # name, dimensions, type, offset
                (n,) = struct.unpack("<Q", _read(f, 8))
                f.seek(n, 1)
                (dims,) = struct.unpack("<I", _read(f, 4))
                if dims > 8:
                    raise ValueError("not a tensor")
                count = 1
                for d in struct.unpack(f"<{dims}Q", _read(f, 8 * dims)):
                    count *= d
                total += count
                _read(f, 12)
            out["parameters"] = total
            return out
    except _Short:
        return out or {"partial": True}
    except (OSError, struct.error, ValueError, UnicodeDecodeError):
        return out or None


def is_projector(facts: dict | None) -> bool:
    """A vision projector (mmproj) for a language model, not a model itself."""
    return bool(facts) and (facts.get("architecture") == "clip" or str(facts.get("type") or "").lower() == "mmproj")
