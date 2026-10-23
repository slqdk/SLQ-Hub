"""The speech service's client (services/speech: Chatterbox on the CPU), and what the Hub knows about voices."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Awaitable, Callable

import httpx
import numpy as np

from .wavfile import parse_wav

MAX_VOICE_FILE = 50 * 1024 * 1024        # a model's voice sample is a few seconds; anything bigger is not one


class SpeechError(RuntimeError):
    pass


NOT_RUNNING = ("the speech service is not answering ({}) - see `sudo bash /opt/slq-hub/update.sh compose ps speech` "
               "and `... compose logs speech`")
ProgressFn = Callable[[int, int], Awaitable[None] | None]


class SpeechClient:
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url.rstrip("/")
        self._transport = transport

    def _client(self, timeout: float | httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def health(self) -> dict:
        try:
            async with self._client(5) as c:
                r = await c.get(self.url + "/health")
        except httpx.HTTPError as exc:
            raise SpeechError(NOT_RUNNING.format(exc.__class__.__name__)) from exc
        if r.status_code != 200:
            raise SpeechError(f"the speech service answered {r.status_code}")
        return r.json()

    async def speak(self, body: dict, on_progress: ProgressFn | None = None, timeout: float = 3600) -> dict:
        """One /speak request: progress lines while it works, then the audio. Returns the "done" event with the
        audio as bytes. Cancelling the caller closes the stream; the service stops after the piece in hand."""
        done: dict | None = None
        try:
            async with self._client(httpx.Timeout(timeout, connect=10)) as c:
                async with c.stream("POST", self.url + "/speak", json=body) as r:
                    if r.status_code != 200:
                        text = (await r.aread()).decode(errors="replace")
                        try:
                            message = json.loads(text).get("error") or text
                        except ValueError:
                            message = text
                        raise SpeechError(str(message)[:500])
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue
                        kind = ev.get("event")
                        if kind == "progress" and on_progress:
                            res = on_progress(int(ev.get("piece", 0)), int(ev.get("pieces", 0)))
                            if hasattr(res, "__await__"):
                                await res
                        elif kind == "error":
                            raise SpeechError(str(ev.get("message") or "the speech service failed")[:500])
                        elif kind == "done":
                            done = ev
        except httpx.HTTPError as exc:
            raise SpeechError(NOT_RUNNING.format(exc.__class__.__name__)) from exc
        if done is None or not done.get("audio"):
            raise SpeechError("the speech service stopped before the audio was ready (see `compose logs speech`)")
        done["audio"] = base64.b64decode(done["audio"])
        return done


# ---------------------------------------------------------------- voices

_PITCH: dict[tuple[str, float], float | None] = {}


def median_pitch(path: Path, seconds: float = 20.0) -> float | None:
    """The median fundamental frequency (Hz) of the voiced parts of a WAV, by autocorrelation, or None when it
    cannot tell. Good enough to say whether a voice is low (most men) or high (most women)."""
    try:
        st = path.stat()
        key = (str(path), st.st_mtime)
    except OSError:
        return None
    if key in _PITCH:
        return _PITCH[key]
    f0 = None
    try:
        if st.st_size > MAX_VOICE_FILE:
            raise ValueError("too big for a voice sample")
        x, sr = parse_wav(path.read_bytes(), max_seconds=seconds)
        if sr >= 8000:
            frame, hop = int(0.04 * sr), int(0.02 * sr)
            lo, hi = int(sr / 400), int(sr / 65)
            level = float(np.sqrt(np.mean(x ** 2))) + 1e-9
            found = []
            for start in range(0, len(x) - frame, hop):
                f = x[start:start + frame]
                if np.sqrt(np.mean(f ** 2)) < 0.6 * level:          # a pause or a breath
                    continue
                f = (f - f.mean()) * np.hanning(frame)
                spec = np.fft.rfft(f, 2 * frame)
                ac = np.fft.irfft(spec * np.conj(spec))[:frame]
                if ac[0] <= 0:
                    continue
                seg = ac[lo:hi]
                peak = float(seg.max())
                if peak / ac[0] < 0.35:                            # not periodic: unvoiced
                    continue
                lag = lo + int(np.argmax(seg >= 0.85 * peak))      # the first strong peak, not a sub-harmonic
                found.append(sr / lag)
            if len(found) >= 10:
                f0 = float(np.median(found))
    except (OSError, ValueError):
        f0 = None
    _PITCH[key] = f0
    return f0


def describe_voice(path: Path) -> str:
    """'male voice' (a low median pitch) / 'female voice', or '' when the pitch cannot be read."""
    f0 = median_pitch(path)
    if f0 is None:
        return ""
    return "male voice" if f0 < 165 else "female voice"
