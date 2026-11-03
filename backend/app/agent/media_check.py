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

The check runs on the turn's model (in the cloud when the turn runs there): the card switches back to it after the
picture anyway, so a picture without mistakes costs only the check; each new try costs a switch to ComfyUI and back.
A model that cannot see pictures cannot check: the result says so, and the model is asked to look itself. A call
with a seed is checked but not made again - its seed makes the same picture. The independent review (review.py)
leaves pictures, sound and video out: this is their check."""
from __future__ import annotations

import io
import json
import logging
import secrets
import shlex
import shutil
import time
from dataclasses import dataclass, field
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

MISTAKES = """- People and animals: a part too many (a third arm, a second head, six fingers) or one missing where
  it should show (an arm that stops at the shoulder, a hand missing at the wrist); a limb that grows from the
  wrong place, bends the wrong way or merges with another body or a thing; a melted, doubled or twisted face. A
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

Then list its mistakes - only what you can see:
{mistakes}
What the description asks for is not a mistake (a six-legged robot, a melting clock in a surreal painting).

serious: true when someone would notice it at first look (a third arm, a missing hand, a melted face), false for
small things. verdict: "ok" when nothing serious is wrong, otherwise "mistakes".

Answer with JSON only:
{{"figures": [{{"what": "<who, where>", "heads": <n>, "arms": <n>, "hands": <n>, "legs": <n>}}, ...],
 "mistakes": [{{"what": "<what is wrong, and where>", "serious": <true|false>}}, ...],
 "verdict": "<ok|mistakes>"}}"""

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
    "mistakes": {"type": "array", "maxItems": 8, "items": _MISTAKE},
    "verdict": _VERDICT}, "required": ["figures", "mistakes", "verdict"]}
VIDEO_SCHEMA = {"type": "object", "properties": {
    "mistakes": {"type": "array", "maxItems": 8, "items": {
        "type": "object", "properties": {**_MISTAKE["properties"], "frame": {"type": "integer"}},
        "required": ["what", "frame", "serious"]}},
    "verdict": _VERDICT}, "required": ["mistakes", "verdict"]}

# For the model, when a picture or video keeps a serious mistake.
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
    """None: no check - not Extra, or Settings -> Assistant has it off; else the new tries a picture may get."""
    if getattr(ctx.engine, "level", "quick") != LEVEL:
        return None
    return tries(ctx.hub.settings)


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

    @property
    def bad(self) -> bool:
        return self.state == "mistakes"

    def rank(self) -> tuple[int, int]:
        """Lower is better: no mistake, no clear answer, then the fewest serious ones."""
        return {"ok": 0, "unclear": 1}.get(self.state, 2), len(self.serious)


def _grave(value) -> bool:
    return not (value is False or str(value).strip().lower() in ("false", "no", "0"))


def parse(text: str, frames: list[float] | None = None) -> Verdict:
    """The check's answer: JSON, alone or in a code fence or among words. A mistake without serious counts as
    serious; a verdict "mistakes" with nothing listed is no clear answer. frames: a video's frame times, to name
    the frame a mistake is in."""
    raw = text or ""
    start, end = raw.find("{"), raw.rfind("}")
    try:
        data = json.loads(raw[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
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
    return Verdict(state, serious[:8], minor[:8], len(figures))


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


def _step(ctx, what: str, verdict: Verdict, t0: float) -> None:
    """The check in the turn's steps: how long it took (the card's time per check is not measured yet)."""
    if ctx.engine is not None:
        ctx.engine.steps.append({"type": "check", "what": what, "model": checker(ctx), "verdict": verdict.state,
                                 "mistakes": len(verdict.serious), "seconds": round(time.monotonic() - t0, 2)})


async def look(ctx, sha: str, prompt: str, edit: bool = False, loras: list[str] | None = None) -> Verdict:
    """One picture checked."""
    from ..llm.messages import image_part
    t0 = time.monotonic()
    text = PICTURE.format(source=SOURCE[edit], prompt=prompt.strip()[:4000], mistakes=MISTAKES,
                          loras=LORAS.format(names=", ".join(loras)) if loras else "")
    verdict = parse(await ask(ctx, [{"type": "text", "text": text}, image_part(blobs.read(sha))], PICTURE_SCHEMA))
    _step(ctx, "picture", verdict, t0)
    return verdict


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
                            "minor": s.verdict.minor if s.verdict else []} for s in slots],
              "rejected": [{"sha": it["sha"], "seed": it.get("seed"), "picture": n + 1, "try": at,
                            "mistakes": v.serious or v.minor or ["no clear answer"]}
                           for n, s in enumerate(slots) for at, it, v in s.thrown]}
    if seeded:
        record["seeded"] = True
    elif not allowed:
        record["only"] = True                       # Settings: check, do not make again
    if stopped:
        record["stopped"] = stopped
    return Outcome([s.item for s in slots], record, picture_text(slots, by, seeded, allowed, stopped))


def picture_text(slots: list[Slot], by: str, seeded: bool, allowed: int, stopped: str) -> str:
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
        tail += " " + SUGGEST.format(how=PICTURE_HOW)
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


def sheet(frames: list[tuple[float, bytes]], width: int = 320) -> bytes:
    """The frames side by side, three to a row, each with its number and time: what was checked, for the user."""
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
        label = f"{k + 1} - {t:g} s"
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
        verdict = parse(await ask(ctx, content, VIDEO_SCHEMA), taken)
    except Exception as exc:  # noqa: BLE001
        log.warning("video check stopped: %s", exc)
        record.update(state="unchecked", mistakes=[], minor=[], seconds=round(time.monotonic() - t0, 1),
                      stopped=f"the check stopped: {exc}")
        return Outcome(None, record, f"\nNot checked before the user got it (Effort Extra): the check stopped ({exc}).")
    _step(ctx, "video", verdict, t0)
    record.update(state=verdict.state, mistakes=verdict.serious, minor=verdict.minor,
                  seconds=round(time.monotonic() - t0, 1))
    return Outcome(None, record, video_text(verdict, by, len(frames)))


def video_text(v: Verdict, by: str, n: int) -> str:
    head = (f"\nChecked before the user got it (Effort Extra): {by} looked at {n} frames from across it for wrong "
            "bodies, broken shapes and things that change between frames. ")
    if v.state == "ok":
        small = f" Small things it noticed: {'; '.join(v.minor[:SHOWN])}." if v.minor else ""
        return head + "No serious mistake." + small
    if v.state == "unclear":
        return head + "The check gave no clear answer."
    return (head + f"Serious mistakes: {'; '.join(v.serious)}. It was not made again (a video takes minutes). "
            + SUGGEST.format(how=VIDEO_HOW))


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
    out = [(r["sha"], f"try {r.get('try') or '?'}, thrown away: {'; '.join(r.get('mistakes') or [])}")
           for r in record.get("rejected") or [] if isinstance(r, dict) and r.get("sha")]
    if record.get("sheet"):
        out.append((record["sheet"], "the frames that were checked"))
    return out

