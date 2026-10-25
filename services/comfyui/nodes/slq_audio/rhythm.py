"""Whether a remake keeps its song's time - measured, because the assistant cannot hear it.

Tempo and beats from librosa's beat tracker, the key from the chroma (Krumhansl-Kessler profiles), and how many
of the new version's beats land within 70 ms of the song's. Chat 76 (a 214-s pop song at about 97 BPM, six
remakes measured this way): keep 0.6-0.85 put 99-100 % of their beats on the song's, keep 0.4-0.5 about a
quarter - what chance gives - at about 150 BPM and in another key.

Plain numpy and librosa, no ComfyUI: the node (beats.py) hands it mono waves at RATE."""
from __future__ import annotations

import numpy as np

RATE = 22050
HOP = 256                    # 11.6 ms frames for the beat tracker
WINDOW = 0.07                # a beat within 70 ms of one of the song's is on it
MIN_BEATS = 8
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def key_of(profile: np.ndarray) -> str | None:
    """The key whose profile the average chroma follows best ('F# minor'); None for silence."""
    p = np.asarray(profile, dtype=float)
    if p.size != 12 or not np.isfinite(p).all() or p.std() < 1e-9:
        return None
    scores = [(float(np.corrcoef(np.roll(MAJOR, k), p)[0, 1]), f"{NAMES[k]} major") for k in range(12)]
    scores += [(float(np.corrcoef(np.roll(MINOR, k), p)[0, 1]), f"{NAMES[k]} minor") for k in range(12)]
    return max(scores)[1]


def analyse(mono: np.ndarray, rate: int = RATE) -> dict:
    """{"beats": beat times in s, "bpm": the average tempo (a line fitted through the beats), "key"}."""
    import librosa

    y = np.nan_to_num(np.asarray(mono, dtype=np.float32))
    if rate != RATE:
        y = librosa.resample(y, orig_sr=rate, target_sr=RATE)
    if y.size < RATE * 5 or float(np.max(np.abs(y))) < 1e-4:
        return {"beats": np.zeros(0), "bpm": None, "key": None}
    tempo, beats = librosa.beat.beat_track(y=y, sr=RATE, hop_length=HOP, units="time")
    beats = np.asarray(beats, dtype=float)
    bpm = None
    if beats.size >= MIN_BEATS:
        slope = float(np.polyfit(np.arange(beats.size), beats, 1)[0])
        bpm = 60.0 / slope if slope > 0 else None
    if bpm is None and np.size(tempo):
        bpm = float(np.atleast_1d(tempo)[0]) or None
    chroma = librosa.feature.chroma_cqt(y=y, sr=RATE, hop_length=2048)
    return {"beats": beats, "bpm": bpm, "key": key_of(chroma.mean(axis=1))}


def _near(times: np.ndarray, grid: np.ndarray) -> float:
    """The share of `times` within WINDOW of a time in `grid` (both sorted)."""
    idx = np.clip(np.searchsorted(grid, times), 1, len(grid) - 1)
    gap = np.minimum(np.abs(times - grid[idx - 1]), np.abs(times - grid[idx]))
    return float(np.mean(gap <= WINDOW))


def compare(song: dict, new: dict) -> dict:
    """How the new version's time relates to the song's. verdict: follows (at least 80 % of one version's beats on
    the other's - a double or half tempo on the same beat counts), lost (no better than chance gives, plus a
    margin), partly, or unknown (too few beats to tell)."""
    out = {"song_bpm": round(song["bpm"]) if song.get("bpm") else None,
           "new_bpm": round(new["bpm"]) if new.get("bpm") else None,
           "song_key": song.get("key"), "new_key": new.get("key")}
    sb, nb = np.sort(song["beats"]), np.sort(new["beats"])
    if sb.size < MIN_BEATS or nb.size < MIN_BEATS:
        return {**out, "verdict": "unknown"}
    on_beat, covered = _near(nb, sb), _near(sb, nb)
    chance_on = min(1.0, 2 * WINDOW / float(np.median(np.diff(sb))))      # the song's grid, hit at random
    chance_cov = min(1.0, 2 * WINDOW / float(np.median(np.diff(nb))))
    best = max(on_beat, covered)
    if best >= 0.8:
        verdict = "follows"
    elif best < max(0.45, max(chance_on, chance_cov) + 0.2):
        verdict = "lost"
    else:
        verdict = "partly"
    return {**out, "on_beat": round(on_beat, 3), "covered": round(covered, 3), "chance": round(chance_on, 3),
            "verdict": verdict}


def report(song: np.ndarray, new: np.ndarray, rate: int = RATE) -> dict:
    return compare(analyse(song, rate), analyse(new, rate))
