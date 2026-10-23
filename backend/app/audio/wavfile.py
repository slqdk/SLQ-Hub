"""A small WAV reader: integer PCM of 8-32 bits and 32/64-bit float, plain or WAVE_FORMAT_EXTENSIBLE - what ffmpeg,
libsndfile and torchaudio write (the Røst voice samples are 32-bit float, which Python's wave module cannot read).
The speech service has the same reader (services/speech/server.py, parse_wav); the tests hold them to the same
answers."""
from __future__ import annotations

import struct

import numpy as np

# (format tag, bits) -> sample type
WAV_KINDS = {(1, 8): "u8", (1, 16): "<i2", (1, 24): "i24", (1, 32): "<i4", (3, 32): "<f4", (3, 64): "<f8"}
NOT_WAV = "the voice clip must be a WAV file (the Hub converts other formats)"


def parse_wav(data: bytes, max_seconds: float = 60.0) -> tuple[np.ndarray, int]:
    """A WAV -> (mono float32 samples, sample rate), at most max_seconds of it. ValueError for anything else."""
    mv = memoryview(data)
    if len(mv) < 12 or bytes(mv[:4]) != b"RIFF" or bytes(mv[8:12]) != b"WAVE":
        raise ValueError(NOT_WAV)
    fmt = payload = None
    pos = 12
    while pos + 8 <= len(mv) and (fmt is None or payload is None):
        cid, size = bytes(mv[pos:pos + 4]), int.from_bytes(mv[pos + 4:pos + 8], "little")
        body = mv[pos + 8:pos + 8 + size]               # a data chunk may claim more than there is (a stream)
        if cid == b"fmt " and len(body) >= 16:
            tag, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", body)
            if tag == 0xFFFE and len(body) >= 26:         # extensible: the sub-format GUID starts with the tag
                tag = struct.unpack_from("<H", body, 24)[0]
            fmt = (tag, channels, rate, bits)
        elif cid == b"data":
            payload = body
        pos += 8 + size + (size & 1)
    if fmt is None or payload is None:
        raise ValueError(NOT_WAV)
    tag, channels, rate, bits = fmt
    kind = WAV_KINDS.get((tag, bits))
    if kind is None or not 1 <= channels <= 8 or not 4000 <= rate <= 192000:
        raise ValueError(NOT_WAV + f" (this one is format {tag}, {bits} bits, {channels} channels, {rate} Hz)")
    frame = bits // 8 * channels
    raw = payload[: min(len(payload) // frame, int(rate * max_seconds)) * frame]
    if kind == "u8":
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif kind == "i24":
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float32) / float(1 << 23)
    elif kind[1] == "f":
        x = np.clip(np.nan_to_num(np.frombuffer(raw, kind).astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0),
                    -1.0, 1.0)
    else:
        x = np.frombuffer(raw, kind).astype(np.float32) / float(1 << (bits - 1))
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x.astype(np.float32), rate
