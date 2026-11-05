"""Pictures and videos checked before the user gets them, at the Effort Extra (0.71.0). Søren: "When i create a
picture or a video, and the thinking is set on Extra.. i would like it to verify the picture. Like check for extra or
missing bodyparts on humans, animals or just out of shape stuff before delivering the result to me.. if it cant make
it right, then suggest improvements to my prompt."

image_generate and image_edit show each picture they made to the turn's model in a request of its own - the
picture, the description it was made from and a checklist, not the conversation - which counts every person's and
animal's heads, arms, hands and legs, then lists the mistakes it sees (JSON; on this server held to a schema). A
picture with a serious mistake is made again with a new seed - a call's such pictures together, in one go on the
card - up to Settings -> Assistant's tries (2 to begin with). The user gets the pictures without one or, when every
try had one, the try with the fewest; the result tells the model what is still wrong, to say so and suggest how to
change the prompt. The tries thrown away stay with the call - under the picture (closed) and in the export - so the
user can see what the check found, and whether it was right.

A video takes minutes on the card: it is not made again. Frames from across it are checked together - also between
each other: parts that appear, vanish or change - and the result says what is wrong.

A persona's pictures are checked the same way when it has "Check pictures" on (0.78.0, Søren, of his persona
Laura's pictures: "There are something in almost all pictures that is not correct"): in its chats and in its tasks,
whatever its Answers setting - a persona answers at once or thinks first, never at Extra.

The hands get a look of their own (0.80.0, Søren: "way to many anatomi errors" - five fingers lined up on a mug). A
vision model sees a whole picture coarsely: llama.cpp gives Gemma 4 one token for every 48x48 px (patches of 16,
pooled by 3), so a hand 200 px across is four tokens wide and a finger half of one - enough for a third hand, not for
fingers. So the first look also says where the hands are, and a picture it finds nothing wrong with goes on to a
second request with each hand enlarged, finger by finger: about two tokens to a finger. The crops show under the
picture.

The hands are counted by a detector as well (0.80.4, Søren, of chat 298: "the 3rd hand here is not good"; hand_count.py):
Gemma 4 had said two arms, two hands, and given two boxes, so the third hand never reached the close look either. A
language model knows how many hands a person has. More than two a person is a serious mistake, and the detector's boxes
go to the close look, Gemma 4's only when it has none.

A task's post is never held back by the check (0.80.1, Søren, of chat 292: "I dont think the latest picture is wrong.
But it was flagged wrong" - and no post was handed in: told to offer a new try, the persona asked, and nobody answers
in a task). There the model is told to hand the post in, and the post says what the check found.

The check runs on the turn's model (in the cloud when the turn runs there): the card switches back to it after the
picture anyway, so a picture without mistakes costs only the check; each new try costs a switch to ComfyUI and back.
A model that cannot see pictures cannot check: the result says so, and the model is asked to look itself. A call
with a seed is checked but not made again - its seed makes the same picture. The independent review (review.py)
leaves pictures, sound and video out: this is their check."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from PIL import Image, ImageDraw, ImageOps

from ..projects import blobs

log = logging.getLogger(__name__)

LEVEL = "extra"
DEFAULT_TRIES = 2                   # Settings -> Assistant (agent.check_media): "off", or 0-4 new tries
MAX_TRIES = 4
FRAMES = 6                          # a video's frames that are checked: across it, not its first and last
FRAME_SIDE = 896                    # each frame's long side for the check (a 720p frame is 1280 wide)
MAX_TOKENS = 1200                   # 8 figures and 8 mistakes fit in about 600
SHOWN = 3                           # small things named in the result
HANDS_AT = 6                        # hands the first look gives the place of
HANDS_LOOKED_AT = 4                 # crops in the close look, the biggest hands first
HAND_SIDE = 1024                    # each crop's side for the close look
HAND_MIN = 0.2                      # a crop's side at least, of the picture's short side: a model's boxes are rough
HAND_ROOM = 1.35                    # a crop's side, of the hand's (or grip's) longer side
HAND_NEAR = 0.25                    # hands this close (of the longer side) are one crop: a grip on a phone or a mug
ANSWER_KEPT = 1500                  # of a check's answer, in the turn's steps: what it said, for the export
HAND_MODEL = Path(os.environ.get("HUB_HAND_MODEL") or "/app/hand_landmarker.task")     # backend/Dockerfile
HAND_COUNT = Path(__file__).with_name("hand_count.py")
HAND_COUNT_SECONDS = 30
HAND_TWICE = 0.5                    # one hand given twice: the same side, and this much of the smaller box shared
MIRROR = re.compile(r"\b(?:mirrors?|reflect\w*|spejl\w*)\b", re.I)    # a reflection shows the same hands again

MISTAKES = """- People and animals: a part too many (a third arm, a second head, six fingers) or one missing where
  it should show (an arm that stops at the shoulder, a hand missing at the wrist); a limb that grows from the
  wrong place, bends the wrong way or merges with another body or a thing; a melted, doubled or twisted face; a
  body that ends too soon (a torso rising straight out of a flat duvet or sofa, with no room for hips and legs). A
  part hidden behind something or cut off by the edge of the picture is not missing.
- Things: objects melted into each other, broken where they should be whole, or shapes that could not exist (a
  chair leg that ends in mid-air, two cups grown together).
- The description: a main subject it asks for missing, or the wrong number of them; text it asks for, spelled
  wrong."""

# The first line is how the tests find the check's requests.
PICTURE = """You check a picture an image model just made, before the user sees it.

{source}
\"\"\"
{prompt}
\"\"\"
{loras}
First list every person and animal in it (at most the 8 biggest) with the heads, arms, hands and legs you count -
what is drawn, not what should be there.

Then say where the hands you can see are (at most {hands_at}, the biggest first), each as [ymin, xmin, ymax, xmax] on
a scale of 0 to 1000 across the picture.

Then list its mistakes - only what you can see:
{mistakes}
What the description asks for is not a mistake (a six-legged robot, a melting clock in a surreal painting).

serious: true when someone would notice it at first look (a third arm, a missing hand, a melted face), false for
small things. verdict: "ok" when nothing serious is wrong, otherwise "mistakes".

Answer with JSON only:
{{"figures": [{{"what": "<who, where>", "heads": <n>, "arms": <n>, "hands": <n>, "legs": <n>}}, ...],
 "hands_at": [[<ymin>, <xmin>, <ymax>, <xmax>], ...],
 "mistakes": [{{"what": "<what is wrong, and where>", "serious": <true|false>}}, ...],
 "verdict": "<ok|mistakes>"}}"""

# The first line is how the tests find these requests too. Vision models answer five for a hand with six ("Vision
# Language Models are Biased", Vo et al., 2025): hence finger by finger, and what is drawn. 0.80.1: 0.80.0's list began
# "more than five fingers and thumb together", and chat 292's Gemma 4 gave exactly that for two hands round a phone,
# three pictures in a row, all of them right: a hand at a time, then, and a mistake of its own words.
HANDS = """You check the hands in a picture an image model just made, before the user sees it: {n} enlarged crop(s)
of it, each with its number.

It was made from this description:
\"\"\"
{prompt}
\"\"\"

A crop often shows two hands, one over the other. Take each hand on its own: follow its fingers back to the palm they
grow from, and count the fingers and the thumb of that one hand, each nail as one finger - what is drawn, not what a
hand should have. Do not answer five because hands have five. Never add two hands up.

Then say in your own words what is wrong with that hand, if anything - only what you can see. For example: a sixth
finger on one hand, two fingers of one hand grown into one, a finger with a joint too many, a finger bent the wrong way
or growing from the wrong place, fingers melted into what they hold.
The fingers of two hands lying over or between each other are no mistake. A finger hidden behind something or cut off
by the edge of the crop is not missing. A hand out of focus or in motion is not a mistake.

serious: true when someone would notice it looking at that hand in the picture, false for small things.

Answer with JSON only, one entry for each hand:
{{"hands": [{{"crop": <its number>, "which": "<which hand, where in the crop>", "digits": <that hand's fingers and thumb>,
 "mistake": "<what is wrong, or empty>", "serious": <true|false>}}, ...]}}"""

VIDEO = """You check a video a video model just made, before the user sees it: {n} frames from across it, in
order, each with its time.

It was made from this description:
\"\"\"
{prompt}
\"\"\"

List its mistakes - only what you can see:
{mistakes}
- Between the frames: a body part or thing that appears, vanishes or changes shape; a person or animal that turns
  into another or merges with one; a face that changes.
What the description asks for is not a mistake.

serious: true when someone would notice it while watching (a third arm, a missing hand, a melted face), false for
small things. verdict: "ok" when nothing serious is wrong, otherwise "mistakes".

Answer with JSON only:
{{"mistakes": [{{"what": "<what is wrong, and where>", "frame": <its number>, "serious": <true|false>}}, ...],
 "verdict": "<ok|mistakes>"}}"""

SOURCE = {False: "It was made from this description:",
          True: "It was made by changing another picture, as asked here:"}
# With LoRAs: their trigger words are made-up words in the description, not text to draw.
LORAS = ("It was made with the LoRAs {names}: a made-up word in the description (a LoRA's trigger word) names a person, "
         "a thing or a style the image model learned - it is not text to draw.\n")

_MISTAKE = {"type": "object", "properties": {"what": {"type": "string"}, "serious": {"type": "boolean"}},
            "required": ["what", "serious"]}
_VERDICT = {"type": "string", "enum": ["ok", "mistakes"]}
# llama-server holds the answer to these (response_format): a model that cannot see well still answers in JSON.
PICTURE_SCHEMA = {"type": "object", "properties": {
    "figures": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {
        "what": {"type": "string"}, "heads": {"type": "integer"}, "arms": {"type": "integer"},
        "hands": {"type": "integer"}, "legs": {"type": "integer"}},
        "required": ["what", "heads", "arms", "hands", "legs"]}},
    "hands_at": {"type": "array", "maxItems": HANDS_AT, "items": {
        "type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "integer"}}},
    "mistakes": {"type": "array", "maxItems": 8, "items": _MISTAKE},
    "verdict": _VERDICT}, "required": ["figures", "hands_at", "mistakes", "verdict"]}
HANDS_SCHEMA = {"type": "object", "properties": {"hands": {"type": "array", "maxItems": 8, "items": {
    "type": "object", "properties": {"crop": {"type": "integer"}, "which": {"type": "string"},
                                     "digits": {"type": "integer"}, "mistake": {"type": "string"},
                                     "serious": {"type": "boolean"}},
    "required": ["crop", "which", "digits", "mistake", "serious"]}}}, "required": ["hands"]}
VIDEO_SCHEMA = {"type": "object", "properties": {
    "mistakes": {"type": "array", "maxItems": 8, "items": {
        "type": "object", "properties": {**_MISTAKE["properties"], "frame": {"type": "integer"}},
        "required": ["what", "frame", "serious"]}},
    "verdict": _VERDICT}, "required": ["mistakes", "verdict"]}

# For the model, when a picture or video keeps a serious mistake - in a persona's task (0.80.1) there is nobody to
# tell or to offer anything to, and the post goes to the owner's review with what the check found.
IN_TASK = ("This is a scheduled task and nobody is here to answer: do not ask and do not offer anything. Hand the post "
           "in with post_draft as it is. The owner reviews it and sees what the check found.")
SUGGEST = ("Tell the user plainly what is wrong - do not call it fine - and suggest how to change the prompt so the "
           "model gets it right: {how}.")
PICTURE_HOW = ("fewer people or animals, a simpler pose, say what the arms and hands do (or keep them out of the "
               "frame), a closer or simpler framing - or another picture model; offer to make it again")
VIDEO_HOW = ("fewer people or animals, simpler and slower movement, a steadier camera, a shorter clip - or start it "
             "from a picture (make the picture first, then the video from it); offer to make it again with another "
             "seed")


# ---------------------------------------------------------------- when

def tries(settings) -> int | None:
    """Settings -> Assistant: how often a picture with a serious mistake is made again (0: only checked), or None:
    nothing is checked."""
    raw = str(settings.get("agent", "check_media", DEFAULT_TRIES)).strip().lower()
    if raw in ("off", "false", "no", "none"):
        return None
    try:
        return max(0, min(MAX_TRIES, int(float(raw))))
    except ValueError:
        return DEFAULT_TRIES


def plan(ctx) -> int | None:
    """None: no check - not Extra (nor a persona's chat with Check pictures on), or Settings -> Assistant has it
    off; else the new tries a picture may get."""
    if getattr(ctx.engine, "level", "quick") != LEVEL and not persona_checks(ctx):
        return None
    return tries(ctx.hub.settings)


def in_task(ctx) -> bool:
    """A chat of a persona's task (0.75.0): nobody answers there."""
    store = ctx.hub.extras.get("personas")
    p = store.of_chat(ctx.chat) if store is not None else None
    return bool(p and store.task_of(p, ctx.chat))


def for_post(ctx, paths: list[str], outcome: Outcome | None) -> None:
    """What the check still finds in files saved at these paths, for this turn's post_draft: the post says it."""
    if outcome is None:
        return
    found = outcome.record.get("pictures") or [outcome.record]
    ctx.state.setdefault("checked", {}).update(
        {path: list(what.get("mistakes") or []) for path, what in zip(paths, found) if isinstance(what, dict)})


def persona_checks(ctx) -> bool:
    """A chat of a persona - its own or one of its tasks' - that has Check pictures on (0.78.0)."""
    store = ctx.hub.extras.get("personas")
    p = store.of_chat(ctx.chat) if store is not None else None
    return bool(p and p.get("check_pictures"))


def _cloud(ctx):
    engine = ctx.engine
    return getattr(engine, "cloud", None) if engine is not None else None


def blind(ctx) -> str:
    """Why the turn's model cannot check, or ""."""
    if _cloud(ctx) is not None:
        return ""
    from .loop import vision
    if vision(ctx.model):
        return ""
    return f"{ctx.model.get('name') or ctx.model['id']} cannot see pictures"


def checker(ctx) -> str:
    """Who checks, for the user and the model."""
    cloud = _cloud(ctx)
    return cloud.label if cloud is not None else (ctx.model.get("name") or ctx.model["id"])


# ---------------------------------------------------------------- one check

@dataclass
class Verdict:
    state: str                                     # ok | mistakes | unclear (no answer the Hub could read)
    serious: list[str] = field(default_factory=list)
    minor: list[str] = field(default_factory=list)
    figures: int = 0
    hands: list[tuple] = field(default_factory=list)   # where the hands are: (left, top, right, bottom), parts of 1
    crops: list[bytes] = field(default_factory=list)   # the hands as the close look saw them

    @property
    def bad(self) -> bool:
        return self.state == "mistakes"

    def rank(self) -> tuple[int, int]:
        """Lower is better: no mistake, no clear answer, then the fewest serious ones."""
        return {"ok": 0, "unclear": 1}.get(self.state, 2), len(self.serious)


def _grave(value) -> bool:
    return not (value is False or str(value).strip().lower() in ("false", "no", "0"))


def _json(text: str) -> dict | None:
    """The JSON object in an answer: alone, in a code fence or among words."""
    raw = text or ""
    start, end = raw.find("{"), raw.rfind("}")
    try:
        data = json.loads(raw[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _box(value) -> tuple | None:
    """[ymin, xmin, ymax, xmax] on a scale of 1000 (of 1 from a model that took it so) as (left, top, right, bottom),
    parts of the picture; None when it is no box, or covers half the picture and so shows no hand."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        y0, x0, y1, x1 = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    scale = 1.0 if max(y0, x0, y1, x1) <= 1 else 1000.0
    left, right = sorted(max(0.0, min(1.0, v / scale)) for v in (x0, x1))
    top, bottom = sorted(max(0.0, min(1.0, v / scale)) for v in (y0, y1))
    wide, high = right - left, bottom - top
    return (left, top, right, bottom) if wide > 0 and high > 0 and wide * high <= 0.5 else None


def parse(text: str, frames: list[float] | None = None) -> Verdict:
    """The check's answer. A mistake without serious counts as serious; a verdict "mistakes" with nothing listed is
    no clear answer. frames: a video's frame times, to name the frame a mistake is in."""
    data = _json(text)
    if data is None:
        return Verdict("unclear")
    serious: list[str] = []
    minor: list[str] = []
    for m in data.get("mistakes") or []:
        if isinstance(m, str):
            what, grave, frame = m, True, None
        elif isinstance(m, dict):
            what, grave, frame = str(m.get("what") or ""), _grave(m.get("serious", True)), m.get("frame")
        else:
            continue
        what = " ".join(what.split())[:300]
        if not what:
            continue
        if frames and isinstance(frame, int) and 1 <= frame <= len(frames):
            what = f"frame {frame} ({frames[frame - 1]:g} s): {what}"
        (serious if grave else minor).append(what)
    figures = data.get("figures") if isinstance(data.get("figures"), list) else []
    said = str(data.get("verdict") or "").strip().lower()
    state = "mistakes" if serious else "unclear" if said == "mistakes" and not minor else "ok"
    at = data.get("hands_at") if isinstance(data.get("hands_at"), list) else []
    return Verdict(state, serious[:8], minor[:8], len(figures), [b for b in map(_box, at) if b])


TOO_MANY = re.compile(r"\b(more than five|six or more|too many)\b.*\b(fingers?|digits?)\b|"
                      r"\b(extra|additional|sixth|six|seven|eight|nine|ten|\d+)\s+(\w+\s+)?(fingers?|digits?)\b", re.I)


def parse_hands(text: str) -> tuple[list[str], list[str]]:
    """The close look's answer: (serious mistakes, small ones). Six digits on a hand is serious whatever the model
    calls it. Seven or more is two hands added up, and five or fewer no mistake by itself - a finger can be hidden -
    so "too many fingers" counts only with a count of six behind it (0.80.1)."""
    serious: list[str] = []
    minor: list[str] = []
    for h in (_json(text) or {}).get("hands") or []:
        if not isinstance(h, dict):
            continue
        what = " ".join(str(h.get("mistake") or "").split())[:300]
        if what.strip(" .").lower() in ("", "none", "no", "nothing", "ok", "n/a"):
            what = ""
        six = h.get("digits") == 6
        if not six and TOO_MANY.search(what):
            what = ""
        if six and not what:
            what = "a hand with six fingers"
        if what:
            (serious if six or _grave(h.get("serious", True)) else minor).append(what)
    return serious, minor


async def ask(ctx, content: list[dict], schema: dict) -> str:
    """One check: a request of its own - the picture(s) and the checklist, not the conversation - to the turn's
    model, without thinking. On this server the card switches to that model first (inside the turn's lease: the
    loop's next step needs it anyway); a turn in the cloud asks the cloud model, which sees the pictures anyway."""
    engine, hub = ctx.engine, ctx.hub
    messages = [{"role": "user", "content": content}]
    cloud = _cloud(ctx)
    if cloud is not None:
        res: dict = {}
        async for kind, data in cloud.stream(messages, []):
            if kind == "result":
                res = data
        _count(engine, res.get("usage") or {})
        return res.get("content") or ""
    from ..llm import payload as P
    from ..llm.stream import Accumulator
    entry = ctx.model
    need = hub.model_need(entry["id"])
    pl = P.build(entry, messages, thinking=False, sampling={"max_tokens": MAX_TOKENS, "temperature": 0.2},
                 response_format={"type": "json_schema", "json_schema": {"name": "check", "schema": schema}})
    own = None
    if ctx.ticket is None:            # (a turn always holds one; the Hub's other callers may not)
        own = await hub.scheduler.acquire("web", "check", need, "interactive", label="Checking a picture",
                                          meta={"chat_id": (ctx.chat or {}).get("id")})
    try:
        if own is None:
            t0 = time.monotonic()
            await ctx.ticket.switch(need)
            loaded = time.monotonic() - t0
            if loaded > 1 and engine is not None:
                engine.steps.append({"type": "load", "model": entry["id"], "seconds": round(loaded, 2)})
        acc = Accumulator()
        async for chunk in hub.llm.stream_chat(hub.main_url, pl):
            acc.feed(chunk)
        res = acc.result()
    finally:
        if own is not None:
            await own.release()
    _count(engine, res.get("usage") or {})
    return res.get("content") or ""


def _count(engine, usage: dict) -> None:
    if engine is None:
        return
    for k in ("prompt_tokens", "completion_tokens"):
        engine.usage[k] = engine.usage.get(k, 0) + int(usage.get(k) or 0)


def _step(ctx, what: str, verdict: Verdict, t0: float, answer: str = "") -> None:
    """The check in the turn's steps: how long it took (the card's time per check is not measured yet), and what the
    model answered (0.80.1: chat 292's export could only show the sentence the Hub took from it)."""
    if ctx.engine is not None:
        ctx.engine.steps.append({"type": "check", "what": what, "model": checker(ctx), "verdict": verdict.state,
                                 "mistakes": len(verdict.serious), "seconds": round(time.monotonic() - t0, 2),
                                 "answer": (answer or "")[:ANSWER_KEPT]})


def _shared(a: list, b: list) -> float:
    """How much of the smaller of two boxes (left, top, right, bottom) lies in the other."""
    wide, high = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return max(0, wide) * max(0, high) / smaller if smaller > 0 else 0.0


def distinct_hands(found: list) -> list[dict]:
    """Each hand once. The detector can give a hand twice; the two hands of a grip overlap as much, but are a left and
    a right (chat 292's picture with a third hand was two of them over a phone, and one on her thigh)."""
    out: list[dict] = []
    for hand in found if isinstance(found, list) else []:
        box = hand.get("box") if isinstance(hand, dict) else None
        if not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, (int, float)) for v in box):
            continue
        if not any(h["side"] == hand.get("side") and _shared(h["box"], box) >= HAND_TWICE for h in out):
            out.append({"box": box, "side": hand.get("side")})
    return out


async def count_hands(sha: str) -> list[dict] | None:
    """The hands in a picture, by a process of its own (hand_count.py). None: it could not say, and the check goes on
    without it."""
    if not HAND_MODEL.exists():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-I", str(HAND_COUNT), str(blobs.path(sha)),
                                                    str(HAND_MODEL), stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
    except OSError as exc:
        log.warning("the hand count did not start: %s", exc)
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), HAND_COUNT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("the hand count took too long")
        return None
    lines = out.decode("utf-8", "replace").strip().splitlines()      # a native library can write to stdout too
    try:
        return distinct_hands(json.loads(lines[-1]))
    except (IndexError, ValueError):
        log.warning("the hand count failed (exit code %s)", proc.returncode)
        return None


def judge_count(verdict: Verdict, found: list[dict], prompt: str) -> None:
    """The detector's hands against the people the model counted: more than two a person is a serious mistake, unless
    the picture has a mirror in it. Its boxes are better than a model's: they go to the close look."""
    if found:
        verdict.hands = [tuple(h["box"]) for h in found][:HANDS_AT]
    people = max(1, verdict.figures)
    if len(found) > 2 * people and not MIRROR.search(prompt):
        who = "one person" if people == 1 else f"{people} people"
        verdict.serious = [f"{len(found)} hands on {who}", *verdict.serious][:8]
        verdict.state = "mistakes"


async def look(ctx, sha: str, prompt: str, edit: bool = False, loras: list[str] | None = None) -> Verdict:
    """One picture checked."""
    from ..llm.messages import image_part
    t0 = time.monotonic()
    found = await count_hands(sha)
    text = PICTURE.format(source=SOURCE[edit], prompt=prompt.strip()[:4000], mistakes=MISTAKES, hands_at=HANDS_AT,
                          loras=LORAS.format(names=", ".join(loras)) if loras else "")
    answer = await ask(ctx, [{"type": "text", "text": text}, image_part(blobs.read(sha))], PICTURE_SCHEMA)
    verdict = parse(answer)
    _step(ctx, "picture", verdict, t0, answer)
    if found is not None:
        judge_count(verdict, found, prompt)
        _step(ctx, "hand count", verdict, t0, json.dumps(found))
    if verdict.hands and not verdict.bad:
        try:
            await look_closer(ctx, sha, prompt, verdict)
        except Exception as exc:  # noqa: BLE001 - the first look stands
            log.warning("the close look at the hands stopped: %s", exc)
    return verdict


def _joined(boxes: list[tuple]) -> list[tuple]:
    """Boxes (left, top, right, bottom, in pixels) that touch or nearly do, as one: two hands round a phone are looked
    at together, each whole - chat 292's two crops of one grip each cut the other hand in half."""
    out: list[tuple] = []
    for box in boxes:
        near = HAND_NEAR * max(box[2] - box[0], box[3] - box[1])
        for i, o in enumerate(out):
            if box[0] - near < o[2] and o[0] < box[2] + near and box[1] - near < o[3] and o[1] < box[3] + near:
                out[i] = (min(box[0], o[0]), min(box[1], o[1]), max(box[2], o[2]), max(box[3], o[3]))
                break
        else:
            out.append(box)
    return out


def hand_crops(data: bytes, hands: list[tuple]) -> list[bytes]:
    """Each hand enlarged, or a grip's two hands together: a square round it with some of what it touches, inside the
    picture."""
    with Image.open(io.BytesIO(data)) as img:
        im = ImageOps.exif_transpose(img).convert("RGB")
    w, h = im.size
    out = []
    for left, top, right, bottom in _joined([(a * w, b * h, c * w, d * h) for a, b, c, d in hands])[:HANDS_LOOKED_AT]:
        side = int(min(min(w, h), max(HAND_MIN * min(w, h), HAND_ROOM * max(right - left, bottom - top))))
        x = int(max(0, min(w - side, (left + right - side) / 2)))
        y = int(max(0, min(h - side, (top + bottom - side) / 2)))
        buf = io.BytesIO()
        im.crop((x, y, x + side, y + side)).resize((HAND_SIDE, HAND_SIDE), Image.LANCZOS).save(buf, "JPEG", quality=92)
        out.append(buf.getvalue())
    return out


async def look_closer(ctx, sha: str, prompt: str, verdict: Verdict) -> None:
    """The hands enlarged, in a request of their own: what is found joins the verdict."""
    from ..llm.messages import image_part
    t0 = time.monotonic()
    crops = hand_crops(blobs.read(sha), verdict.hands)
    if not crops:
        return
    content: list[dict] = [{"type": "text", "text": HANDS.format(n=len(crops), prompt=prompt.strip()[:4000])}]
    for k, data in enumerate(crops):
        content += [{"type": "text", "text": f"Crop {k + 1}:"}, image_part(data)]
    answer = await ask(ctx, content, HANDS_SCHEMA)
    serious, minor = parse_hands(answer)
    verdict.crops = crops
    verdict.serious = [*verdict.serious, *serious][:8]
    verdict.minor = [*verdict.minor, *minor][:8]
    if serious:
        verdict.state = "mistakes"
    _step(ctx, "hands", verdict, t0, answer)


# ---------------------------------------------------------------- pictures

@dataclass
class Slot:
    """One picture the call gives, with the tries made for it."""
    item: dict
    verdict: Verdict | None = None                 # None: not checked (the check stopped first)
    tries: int = 1
    at: int = 1                                    # which try the picture kept is
    thrown: list[tuple[int, dict, Verdict]] = field(default_factory=list)      # (its try, picture, verdict)

    def offer(self, item: dict, verdict: Verdict) -> None:
        """A new try: it takes the place when it is better, else it is thrown away."""
        self.tries += 1
        if self.verdict is None or verdict.rank() < self.verdict.rank():
            if self.verdict is not None:
                self.thrown.append((self.at, self.item, self.verdict))
            self.item, self.verdict, self.at = item, verdict, self.tries
        else:
            self.thrown.append((self.tries, item, verdict))
        self.thrown.sort(key=lambda t: t[0])


@dataclass
class Outcome:
    items: list[dict] | None                       # the pictures to give, in their order (None for a video)
    record: dict                                   # display["checked"]: for the user, and the export
    text: str                                      # for the model, after the result


Progress = Callable[[str, int, int], Awaitable[None]]
Make = Callable[[int, str], Awaitable[list[dict]]]


async def pictures(ctx, items: list[dict], make: Make, prompt: str, *, edit: bool = False, seeded: bool = False,
                   progress: Progress | None = None) -> Outcome | None:
    """Check the pictures a call made and make the ones with a serious mistake again (make(n, label) -> n new ones,
    with new seeds). None: no check at this effort. The pictures' "loras" (the names of those used) go with the
    checklist, so their trigger words are not taken for text to draw."""
    allowed = plan(ctx)
    if allowed is None or not items:
        return None
    why = blind(ctx)
    if why:
        return Outcome(items, {"skipped": why}, f"\nNot checked before the user got it (Effort Extra): {why}.")
    by = checker(ctx)
    t0 = time.monotonic()
    slots = [Slot(it) for it in items]
    stopped = ""
    rounds = 0 if seeded else allowed
    loras = items[0].get("loras") or []
    try:
        for i, s in enumerate(slots):
            if progress is not None:
                await progress("checking", i + 1, len(slots))
            s.verdict = await look(ctx, s.item["sha"], prompt, edit, loras)
        for r in range(rounds):
            bad = [s for s in slots if s.verdict is not None and s.verdict.bad]
            if not bad:
                break
            try:
                fresh = await make(len(bad), f"try {r + 2}")
            except Exception as exc:  # noqa: BLE001 - the card or ComfyUI: the best so far are given
                log.warning("a new try for the check failed: %s", exc)
                stopped = f"a new try could not be made: {exc}"
                break
            for i, (s, it) in enumerate(zip(bad, fresh)):
                if progress is not None:
                    await progress(f"checking try {r + 2}", i + 1, len(bad))
                s.offer(it, await look(ctx, it["sha"], prompt, edit, loras))
    except Exception as exc:  # noqa: BLE001 - the pictures made so far are given; what stopped is said
        log.warning("picture check stopped: %s", exc)
        stopped = f"the check stopped: {exc}"
    record = {"by": by, "seconds": round(time.monotonic() - t0, 1),
              "pictures": [{"state": s.verdict.state if s.verdict else "unchecked", "tries": s.tries, "try": s.at,
                            "mistakes": s.verdict.serious if s.verdict else [],
                            "minor": s.verdict.minor if s.verdict else [],
                            **_hands_shown(s.verdict)} for s in slots],
              "rejected": [{"sha": it["sha"], "seed": it.get("seed"), "picture": n + 1, "try": at,
                            "mistakes": v.serious or v.minor or ["no clear answer"], **_hands_shown(v)}
                           for n, s in enumerate(slots) for at, it, v in s.thrown]}
    if seeded:
        record["seeded"] = True
    elif not allowed:
        record["only"] = True                       # Settings: check, do not make again
    if stopped:
        record["stopped"] = stopped
    return Outcome([s.item for s in slots], record, picture_text(slots, by, seeded, allowed, stopped, in_task(ctx)))


def _hands_shown(verdict: Verdict | None) -> dict:
    """For the record: the crops the close look saw, on one sheet - to see whether it was right."""
    if verdict is None or not verdict.crops:
        return {}
    from ..chats import store as chats
    made = sheet([(0.0, c) for c in verdict.crops], labels=[f"crop {k + 1}" for k in range(len(verdict.crops))])
    return {"hands": chats.save_upload(made, "checked-hands.jpg", "image/jpeg")["sha"]}


def picture_text(slots: list[Slot], by: str, seeded: bool, allowed: int, stopped: str, task: bool = False) -> str:
    """What the model is told: what the check found for each picture, and what to do when one is still wrong."""
    one = len(slots) == 1
    lines = []
    for n, s in enumerate(slots):
        name = "The picture" if one else f"Picture {n + 1}"
        v = s.verdict
        if v is None:
            lines.append(f"{name} was not checked.")
            continue
        others = "; ".join(dict.fromkeys(m for _, _, tv in s.thrown for m in tv.serious[:2])) or "no clear answer"
        made = f" It was made {s.tries} times: the other tries had {others}." if s.tries > 1 else ""
        if v.state == "ok":
            small = f" Small things it noticed: {'; '.join(v.minor[:SHOWN])}." if v.minor else ""
            lines.append(f"{name}: no serious mistake.{made}{small}")
        elif v.state == "unclear":
            lines.append(f"{name}: the check gave no clear answer - look at it yourself.{made}")
        else:
            after = f" after {s.tries} tries" if s.tries > 1 else ""
            other = f" The other tries had: {others}." if s.thrown else ""
            lines.append(f"{name} still has serious mistakes{after}: {'; '.join(v.serious)}.{other}")
    head = (f"\nChecked before the user got {'it' if one else 'them'} (Effort Extra): {by} looked at "
            f"{'the picture' if one else 'each picture'} for wrong bodies (heads, arms, hands, legs), broken shapes "
            "and missing subjects. ")
    tail = ""
    if any(s.verdict is not None and s.verdict.bad for s in slots):
        if seeded:
            tail += (" It was not made again: the call gave a seed, which makes the same picture - without one, a new "
                     "call makes it with another.")
        elif not allowed:
            tail += " It was not made again (Settings -> Assistant: check only)."
        tail += " " + (IN_TASK if task else SUGGEST.format(how=PICTURE_HOW))
    if stopped:
        tail += f" ({stopped[0].upper()}{stopped[1:]}; the pictures are given as they are.)"
    return head + " ".join(lines) + tail


# ---------------------------------------------------------------- videos

def frame_times(seconds: float, n: int = FRAMES) -> list[float]:
    """Across the video, not its very first and last frame: the middle of each of n equal parts."""
    return [round(seconds * (k + 0.5) / n, 2) for k in range(n)]


def frames_command(src: str, dst: str, times: list[float]) -> str:
    """ffmpeg's command for the frames: one seek and one frame each, as dst/f1.jpg, f2.jpg, ..."""
    return " && ".join([f"mkdir -p {shlex.quote(dst)}"] + [
        f"ffmpeg -nostdin -v error -y -ss {t:g} -i {shlex.quote(src)} -frames:v 1 -q:v 3 "
        f"{shlex.quote(f'{dst}/f{k + 1}.jpg')}" for k, t in enumerate(times)])


async def grab(ctx, rel: str, times: list[float]) -> list[tuple[float, bytes]]:
    """The video's frames at these times, by ffmpeg in the sandbox (the Hub's own image has no ffmpeg), through a
    folder of its own under .hub/tmp that is removed afterwards."""
    from .tools.shell import SandboxError, sandbox_exec
    wd = ctx.workdir
    tmp = f".hub/tmp/check-{secrets.token_hex(4)}"
    folder = wd.root / tmp
    try:
        res = await sandbox_exec(ctx, frames_command(f"{wd.sandbox_path}/{rel}", f"{wd.sandbox_path}/{tmp}", times),
                                 wd.sandbox_path, 120, offline=True)
        out = [(t, (folder / f"f{k + 1}.jpg").read_bytes()) for k, t in enumerate(times)
               if (folder / f"f{k + 1}.jpg").is_file()]
    except SandboxError as exc:
        raise ValueError(str(exc)) from exc
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if not out:
        err = " ".join(str(res.get("stderr") or res.get("stdout") or "").split())[:300]
        raise ValueError(f"ffmpeg gave no frames{': ' + err if err else ''}")
    return out


def sheet(frames: list[tuple[float, bytes]], width: int = 320, labels: list[str] | None = None) -> bytes:
    """The frames side by side, three to a row, each with its number and time (or its label): what was checked, for
    the user."""
    cells = []
    for _, data in frames:
        with Image.open(io.BytesIO(data)) as img:
            im = ImageOps.exif_transpose(img).convert("RGB")
            im.thumbnail((width, width))
            cells.append(im)
    cols = min(3, len(cells))
    w, h = max(c.width for c in cells), max(c.height for c in cells)
    rows = (len(cells) + cols - 1) // cols
    out = Image.new("RGB", (cols * w + (cols - 1) * 4, rows * h + (rows - 1) * 4), (24, 24, 24))
    draw = ImageDraw.Draw(out)
    for k, (c, (t, _)) in enumerate(zip(cells, frames)):
        x, y = (k % cols) * (w + 4), (k // cols) * (h + 4)
        out.paste(c, (x, y))
        label = labels[k] if labels else f"{k + 1} - {t:g} s"
        draw.rectangle((x, y, x + 8 + 7 * len(label), y + 16), fill=(0, 0, 0))
        draw.text((x + 4, y + 3), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=85)
    return buf.getvalue()


async def video(ctx, rel: str, seconds: float, prompt: str, progress: Progress | None = None) -> Outcome | None:
    """Check a video the call made (saved at rel in the working folder): frames from across it, at once. It is
    not made again - minutes on the card - the result says what is wrong. None: no check at this effort."""
    if plan(ctx) is None:
        return None
    why = blind(ctx)
    if why:
        return Outcome(None, {"skipped": why}, f"\nNot checked before the user got it (Effort Extra): {why}.")
    from ..chats import store as chats
    from ..llm.messages import image_part, prepare_image
    by = checker(ctx)
    t0 = time.monotonic()
    if progress is not None:
        await progress("checking", 0, 0)
    times = frame_times(seconds)
    try:
        frames = await grab(ctx, rel, times)
    except Exception as exc:  # noqa: BLE001 - the video is given; that it was not checked is said
        log.warning("video frames for the check: %s", exc)
        why = f"its frames could not be taken ({exc})"
        return Outcome(None, {"skipped": why}, f"\nNot checked before the user got it (Effort Extra): {why}.")
    taken = [t for t, _ in frames]
    content: list[dict] = [{"type": "text", "text": VIDEO.format(n=len(frames), prompt=prompt.strip()[:4000],
                                                                 mistakes=MISTAKES)}]
    for k, (t, data) in enumerate(frames):
        small, _ = prepare_image(data, FRAME_SIDE)
        content += [{"type": "text", "text": f"Frame {k + 1} ({t:g} s):"}, image_part(small)]
    shown = chats.save_upload(sheet(frames), "checked-frames.jpg", "image/jpeg")
    record = {"by": by, "frames": taken, "sheet": shown["sha"]}
    try:
        answer = await ask(ctx, content, VIDEO_SCHEMA)
        verdict = parse(answer, taken)
    except Exception as exc:  # noqa: BLE001
        log.warning("video check stopped: %s", exc)
        record.update(state="unchecked", mistakes=[], minor=[], seconds=round(time.monotonic() - t0, 1),
                      stopped=f"the check stopped: {exc}")
        return Outcome(None, record, f"\nNot checked before the user got it (Effort Extra): the check stopped ({exc}).")
    _step(ctx, "video", verdict, t0, answer)
    record.update(state=verdict.state, mistakes=verdict.serious, minor=verdict.minor,
                  seconds=round(time.monotonic() - t0, 1))
    return Outcome(None, record, video_text(verdict, by, len(frames), in_task(ctx)))


def video_text(v: Verdict, by: str, n: int, task: bool = False) -> str:
    head = (f"\nChecked before the user got it (Effort Extra): {by} looked at {n} frames from across it for wrong "
            "bodies, broken shapes and things that change between frames. ")
    if v.state == "ok":
        small = f" Small things it noticed: {'; '.join(v.minor[:SHOWN])}." if v.minor else ""
        return head + "No serious mistake." + small
    if v.state == "unclear":
        return head + "The check gave no clear answer."
    return (head + f"Serious mistakes: {'; '.join(v.serious)}. It was not made again (a video takes minutes). "
            + (IN_TASK if task else SUGGEST.format(how=VIDEO_HOW)))


# ---------------------------------------------------------------- the export

def summary(record: dict) -> str:
    """The check in one line, for the export (the UI writes its own from the same record)."""
    if not record:
        return ""
    if record.get("skipped"):
        return f"Not checked: {record['skipped']}."
    by = record.get("by") or "the model"
    if "frames" in record:
        state, n = record.get("state"), len(record.get("frames") or [])
        found = ("no mistakes found" if state == "ok" else "no clear answer" if state == "unclear"
                 else "not checked" if state == "unchecked" else "; ".join(record.get("mistakes") or []))
        return f"Checked {n} frames with {by}: {found}."
    pics = record.get("pictures") or []
    parts = []
    for n, p in enumerate(pics):
        name = "" if len(pics) == 1 else f"picture {n + 1}: "
        tries_, at = p.get("tries") or 1, p.get("try") or 1
        if p.get("state") == "ok":
            found = "no mistakes found" + (f" on try {at}" if tries_ > 1 else "")
        elif p.get("state") == "mistakes":
            found = (f"still wrong after {tries_} tries (kept try {at}) - " if tries_ > 1 else "mistakes - ") + \
                "; ".join(p.get("mistakes") or [])
        elif p.get("state") == "unclear":
            found = "no clear answer"
        else:
            found = "not checked"
        parts.append(name + found)
    line = f"Checked by {by}: {', '.join(parts)}."
    return line + (f" ({record['stopped']})" if record.get("stopped") else "")


def shown_shas(record: dict) -> list[tuple[str, str]]:
    """(sha, caption) of the pictures the check keeps beside the result: the tries thrown away, a video's frames."""
    thrown = [r for r in record.get("rejected") or [] if isinstance(r, dict)]
    out = [(r["sha"], f"try {r.get('try') or '?'}, thrown away: {'; '.join(r.get('mistakes') or [])}")
           for r in thrown if r.get("sha")]
    out += [(r["hands"], f"try {r.get('try') or '?'}: the hands it looked at") for r in thrown if r.get("hands")]
    out += [(p["hands"], "the hands it looked at") for p in record.get("pictures") or []
            if isinstance(p, dict) and p.get("hands")]
    if record.get("sheet"):
        out.append((record["sheet"], "the frames that were checked"))
    return out

