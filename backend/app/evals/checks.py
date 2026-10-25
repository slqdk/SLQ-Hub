"""What the Hub checks itself after a test case's turn: the answer's text (a line it was asked to end with, numbers in
Danish or English writing, JSON, Danish, bullet points) and the tools it used. Anything that opens the turn's files
or runs its code is checked in the sandbox instead (check_lib.py)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class Turn:
    """One case's turn, as the checks see it."""
    status: str = "done"                      # done | error | cancelled | timeout | stopped
    error: str | None = None
    answer: str = ""                          # the final answer's text
    texts: list[str] = field(default_factory=list)       # everything the model wrote to the user, in order
    calls: list[dict] = field(default_factory=list)      # {"name", "arguments", "ok", "text"}
    files: list[dict] = field(default_factory=list)      # what the turn made or changed in the folder
    images: list[dict] = field(default_factory=list)     # pictures from the picture tools {"sha", "tool", "path"}
    audio: list[dict] = field(default_factory=list)      # {"path", "seconds", "tool"}
    video: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)  # {"id", "title", "type", "content"}
    approvals: list[dict] = field(default_factory=list)  # what the turn asked and the answer the test gave

    def used(self, *names: str) -> list[dict]:
        return [c for c in self.calls if c["name"] in names]


def C(name: str, ok, detail="", level: str = "must") -> dict:
    """A check: must (the case fails without it), should (it passes in part), info (a measurement)."""
    return {"name": name, "ok": None if ok is None else bool(ok), "level": level, "detail": str(detail)[:600]}


def verdict(status: str, checks: list[dict]) -> str:
    if status != "done":
        return "error"
    if any(c["ok"] is False and c["level"] == "must" for c in checks):
        return "fail"
    if any(c["ok"] is False and c["level"] == "should" for c in checks):
        return "partial"
    return "pass"


# ---------------------------------------------------------------- reading an answer

def plain(text: str) -> str:
    """Markdown emphasis and code marks off: **SVAR:** 13:00 -> SVAR: 13:00."""
    return re.sub(r"[*_`]+", "", text or "")


def answer_line(text: str, *labels: str) -> str | None:
    """What follows the last "LABEL:" the answer ends with (SVAR: ..., ANSWER: ...)."""
    found = None
    for line in plain(text).splitlines():
        m = re.match(rf"^\W*(?:{'|'.join(re.escape(lab) for lab in labels)})\s*:\s*(.+?)\s*$", line.strip(), re.I)
        if m:
            found = m.group(1)
    return found


_NUM = re.compile(r"(?<![\w.,])-?\d[\d.,   ]*\d|(?<![\w.,])-?\d")


def numbers(text: str) -> list[float]:
    """Every value the numbers in a text can mean, Danish or English: "1.800" is 1800 or 1.8, "5.397,00" is 5397,
    "1,5" is 1.5, "6 746,25" is 6746.25."""
    out: list[float] = []
    for m in _NUM.finditer(text or ""):
        raw = m.group(0).replace(" ", " ").replace(" ", " ").strip(" .,")
        for cand in _readings(raw):
            out.append(cand)
    return out


def _readings(raw: str) -> list[float]:
    vals: set[float] = set()
    sign = -1.0 if raw.startswith("-") else 1.0
    s = raw.lstrip("-")
    pieces = [s] + (s.split() if " " in s else [])     # "14 385,50": 14385.5, or 14 and 385.5
    for k, piece in enumerate(pieces):
        for thousands, decimal in ((".", ","), (",", "."), (" ", ","), (" ", ".")):
            if piece.count(decimal) > 1:
                continue
            whole, _, frac = piece.partition(decimal)
            if frac and not frac.isdigit():
                continue
            if thousands in whole:          # groups of three after the first
                groups = whole.split(thousands)
                if not (groups[0].isdigit() and len(groups[0]) <= 3
                        and all(len(g) == 3 and g.isdigit() for g in groups[1:])):
                    continue
                whole = "".join(groups)
            if not whole.isdigit():
                continue
            vals.add((sign if k == 0 or len(pieces) == 1 else 1.0) * float(whole + ("." + frac if frac else "")))
    return sorted(vals)


def near(text: str, value: float, rel: float = 0.005, absolute: float = 0.011) -> bool:
    return any(abs(n - value) <= max(absolute, abs(value) * rel) for n in numbers(text))


_DA = {"og", "i", "at", "det", "en", "til", "er", "som", "på", "de", "med", "for", "ikke", "der", "af", "har", "vi",
       "du", "kan", "skal", "vil", "jer", "os", "den", "et", "om", "fra", "men", "så", "eller", "hvis", "når", "også",
       "jeres", "vores", "være", "bliver", "kl", "hilsen", "venlig", "tilmelding", "jul"}
_EN = {"the", "and", "of", "to", "is", "you", "with", "for", "that", "are", "will", "be", "this", "on", "your", "we",
       "at", "please", "by", "it"}


def language(text: str) -> tuple[str, float, float]:
    """"da" or "en" by common words (and æøå), with the shares."""
    words = re.findall(r"[a-zæøå]+", (text or "").lower())
    if not words:
        return "", 0.0, 0.0
    da = sum(w in _DA for w in words) / len(words)
    en = sum(w in _EN for w in words) / len(words)
    if re.search(r"[æøå]", text.lower()):
        da += 0.05
    return ("da" if da > en else "en"), da, en


def bullets(text: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^\s*(?:[-*•–]|\d+[.)])\s+(\S.*)$", text or "", re.M)]


def word_count(s: str) -> int:
    return len(re.findall(r"[\wæøåÆØÅ'-]+", plain(s)))


def json_object(text: str):
    """The JSON object an answer gives (in a code fence or not); (object, None) or (None, why not)."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    body = m.group(1).strip() if m else t
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        return None, "no JSON object in the answer"
    try:
        obj = json.loads(body[start:end + 1])
    except ValueError as exc:
        return None, f"not valid JSON: {exc}"
    return (obj, None) if isinstance(obj, dict) else (None, "the JSON is not an object")


def only_json(text: str) -> bool:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    return t.startswith("{") and t.endswith("}")


def urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s)\]>\"']+", text or "")


# ---------------------------------------------------------------- the usual checks

def tools_ok(turn: Turn) -> dict:
    """How the tools went: a measurement (a failed call the model then mended is normal)."""
    bad = [c["name"] for c in turn.calls if not c["ok"]]
    names = ", ".join(dict.fromkeys(c["name"] for c in turn.calls)) or "none"
    return C("tools", None, f"{len(turn.calls)} calls ({names})" + (f"; {len(bad)} failed: {', '.join(bad[:6])}"
                                                                     if bad else ""), "info")


def used(turn: Turn, names: tuple[str, ...], what: str, level: str = "must") -> dict:
    calls = turn.used(*names)
    ok = [c for c in calls if c["ok"]]
    return C(what, bool(ok), f"{len(ok)} of {len(calls)} {'/'.join(names)} calls worked" if calls else
             f"no {'/'.join(names)} call", level)
