"""A clip of a real voice, as the speech models take it (0.73.0 Movies' characters, 0.74.0 personas): its first 30 s
as 24 kHz mono WAV, converted by ffmpeg in the sandbox - the place for files from outside - with local files only, so
a playlist inside a crafted file cannot make ffmpeg fetch anything (as speech_generate's clips)."""
from __future__ import annotations

import io
import re
import shlex
import shutil
import wave
from pathlib import Path

MAX_UPLOAD = 50 * 1024 * 1024
MIN_SECONDS, MAX_SECONDS = 3.0, 30.0         # the least the speech models can use, and what they hear
SUFFIX_RE = re.compile(r"^\.[a-z0-9]{1,8}$")
TMP = "voice-tmp"


class ClipError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def command(src: str, dst: str) -> str:
    return (f"ffmpeg -nostdin -v error -protocol_whitelist file -y -i {shlex.quote(src)} -t {MAX_SECONDS:g} -vn -ac 1 "
            f"-ar 24000 -c:a pcm_s16le -f wav {shlex.quote(dst)}")


def seconds(data: bytes) -> float:
    """A WAV's length (0.0 when it is not one)."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError, ValueError):
        return 0.0


async def convert(hub, data: bytes, filename: str = "") -> tuple[bytes, float]:
    """An uploaded recording (any format ffmpeg reads, a video too) as the speech models' WAV, and its length."""
    from .. import sandbox_job
    from ..projects import store as projects
    if not data:
        raise ClipError("The file is empty.")
    if len(data) > MAX_UPLOAD:
        raise ClipError("The file is over 50 MB - 10-20 seconds of one person speaking is enough.", 413)
    suffix = Path(filename or "").suffix.lower()
    name = f"in{suffix if SUFFIX_RE.match(suffix) else '.bin'}"
    folder = sandbox_job.tmp_folder(hub, TMP)
    try:
        projects.safe_write(folder, folder / name, data)
        out = await sandbox_job.run(hub, folder, command(name, "voice.wav"), 120, "voice.wav",
                                    "The clip could not be read")
    except sandbox_job.SandboxJobError as exc:
        raise ClipError(str(exc)) from exc
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    length = seconds(out)
    if length < MIN_SECONDS:
        raise ClipError(f"The clip has {length:.1f} s of sound - a voice needs 5-20 s of one person speaking.")
    return out, length
