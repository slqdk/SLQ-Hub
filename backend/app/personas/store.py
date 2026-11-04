"""Personas (0.74.0, Søren: "if i need a virtual personality that i can chat with ... always have that personality
answer me ... like a sports women, or a housewife" - "also with memory").

A persona is someone to talk to, not an assistant: a name, who they are, how they talk (with lines in their voice),
a greeting, a language and a model - a picture (portraits made with the picture model, or one of the owner's) and a
voice (one of the speech model's, or a clip of a real one). A chat with a persona has the persona's instructions
instead of the assistant's (prompt.py), no tools unless it is given some - the web, pictures - and a memory: notes
about the owner that the persona keeps with the remember tool, which the owner can read and change, and the titles
of their last conversations. Its answers can be read aloud in its voice.

Everything of a persona's lives in data/personas/<id>/ (persona.json, pictures/, voice/, notes.md, said/ - answers
read aloud, kept to play again); deleting the persona deletes it, and its chats become ordinary chats. The owner's
alone, like Movies."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import logging
import math
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

from .instagram import InstagramJobs
from .tasks import TaskJobs

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^p[0-9a-f]{8}$")
FILE_RE = re.compile(r"^(?:pictures|voice|said|posts)/[a-z0-9][a-z0-9-]{0,80}\.(?:png|jpg|webp|gif|wav|mp3|mp4|webm)$")
LIMITS = {"name": 40, "role": 160, "personality": 4000, "speech": 2000, "examples": 3000, "greeting": 600,
          "look": 600}
NOTES_MAX = 6000                 # the persona's notes about the owner: in every prompt of theirs
NOTE_MAX = 300                   # one note
RECENT = 6                       # the titles of the last chats in the prompt
MAX_PERSONAS = 50
MAX_VARIANTS = 8                 # portraits kept (the chosen one always)
MAX_PICTURE = 20 * 1024 * 1024
SAY_MAX = 2000                   # characters of an answer read aloud (the speech model works on the CPU)
MAX_SAID = 200                   # answers kept read aloud, the newest
LEVELS = ("quick", "think")
PRIORITY = "normal"              # a chat's turn goes first
DEFAULTS = {"name": "", "role": "", "personality": "", "speech": "", "examples": "", "greeting": "", "language": "",
            "model": "", "level": "quick", "look": "", "lora": "", "strength": 1.0, "picture": "", "variants": [],
            "voice": "", "voice_clip": "", "voice_clip_seconds": 0.0, "expressiveness": 0.5, "web": False,
            "pictures": False, "check_pictures": False, "memory": True, "read_aloud": False, "tasks": []}
PORTRAIT = ("Portrait of {who}. {look}Head and shoulders, looking into the camera with a natural, friendly "
            "expression, soft light, a simple background.")
SAMPLE = {"da": "Hej, jeg hedder {name}. Hvad skal vi snakke om i dag?",
          "en": "Hi, I'm {name}. What shall we talk about today?"}
DA_WORDS = {"og", "ikke", "jeg", "det", "er", "du", "på", "med", "har", "hvad", "hvor", "godt", "meget", "hej", "at",
            "en", "et", "til", "af", "som", "men", "så", "kan", "vil", "skal", "mig", "dig", "os", "den", "der", "ja",
            "nej", "tak", "også", "lige", "bare", "nu", "her", "være", "havde", "var", "fra", "om", "hvis", "jo"}
EN_WORDS = {"the", "and", "is", "you", "to", "of", "in", "it", "that", "for", "with", "have", "what", "how", "good",
            "very", "are", "this", "but", "can", "will", "hi", "hello", "yes", "no", "thanks", "just", "now", "here",
            "be", "had", "was", "from", "about", "if", "my", "your", "me", "we", "so", "do", "not", "a", "an"}
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
MARK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")      # a list item's mark, which a note is written without


class PersonaError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> int:
    return int(time.time())


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _para(value, limit: int) -> str:
    lines = [" ".join(x.split()) for x in str(value or "").replace("\r", "").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:limit]


def speakable(text: str) -> str:
    """An answer as it is said: no code, links as their words, no Markdown marks or emoji."""
    t = re.sub(r"```.*?(```|$)", " ", text or "", flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"^\s*(#+|>|[-*+]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"[*_~|#]+", "", t)
    t = EMOJI.sub("", t)
    return " ".join(t.split())[:SAY_MAX]


def guess_language(text: str) -> str:
    """Danish or English, for reading an answer aloud when the persona answers in the owner's language: by their
    small words, and æ, ø and å in words that are not names ("Søren" says nothing about the language)."""
    words = re.findall(r"[^\W\d_]+", text or "")
    da = sum(w.lower() in DA_WORDS for w in words) + sum(1 for w in words if w[0].islower() and re.search("[æøå]", w))
    en = sum(w.lower() in EN_WORDS for w in words)
    return "da" if da > en else "en"


def tool_names(p: dict, task: dict | None = None) -> set[str]:
    """The tools a chat with the persona may have: its memory, and the web and pictures when it is given them - and
    in a task's chat (0.75.0) what the task's post needs, and post_draft to hand it in."""
    out: set[str] = set()
    if p.get("memory"):
        out.add("remember")
    if p.get("web") or (task or {}).get("web"):
        out |= {"web_search", "web_fetch"}
    if p.get("pictures") or (task and task.get("media") != "none"):
        out |= {"image_generate", "image_edit", "view_image"}
    if task and task.get("media") in ("video", "either"):
        out.add("video_generate")
    if task:
        out.add("post_draft")
    return out


def someone(role: str) -> str:
    """Who a portrait is of: "a marathon runner from Aarhus" from the persona's role ("a person" without one)."""
    role = role.strip().rstrip(".")
    if not role:
        return "a person"
    if re.match(r"(a|an|the|my|his|her|their|our)\s|\S+'s\s", role, re.I):
        return role
    return f"{'an' if role[:1].lower() in 'aeiou' else 'a'} {role}"


def persona_id(chat: dict | None) -> str:
    return str(((chat or {}).get("settings") or {}).get("persona") or "")


def _note(value) -> str:
    return MARK.sub("", " ".join(str(value or "").split()))


class Personas(TaskJobs, InstagramJobs):
    def __init__(self, hub):
        self.hub = hub
        self.root = Path(hub.env.data_dir) / "personas"
        self.locks: dict[str, asyncio.Lock] = {}
        self.task_jobs: dict[str, asyncio.Task] = {}    # tasks being done: "<id>/<task>" -> the job (tasks.py)
        self.schedule_job: asyncio.Task | None = None
        self.ig_states: dict[str, dict] = {}            # Instagram sign-ins started (instagram.py)
        self.ig_jobs: dict[str, asyncio.Task] = {}      # posts being posted: "<id>/<post>" -> the job
        self.ig_checked = 0.0                           # when the Instagram tokens were last looked at
        self.ig_settling: asyncio.Task | None = None    # posts a restart cut off, asked about at Instagram
        self.tasks: dict[str, asyncio.Task] = {}        # portraits being made
        self.live: dict[str, dict] = {}                 # their progress
        self.errors: dict[str, str] = {}                # why the last ones failed
        self.speaking: dict[str, asyncio.Task] = {}     # answers being read aloud: "<id>/<file>" -> the job
        self.unsaid: dict[str, str] = {}                # why one could not be: "<id>/<file>" -> the error

    # ------------------------------------------------------------ storage
    def _dir(self, pid: str) -> Path:
        if not ID_RE.match(pid or ""):
            raise PersonaError("No such persona.", 404)
        return self.root / pid

    def load(self, pid: str) -> dict:
        try:
            data = json.loads((self._dir(pid) / "persona.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise PersonaError("No such persona.", 404) from None
        return {**copy.deepcopy(DEFAULTS), **data}        # (a fresh list each: "tasks" is changed in place)

    def get(self, pid: str | None) -> dict | None:
        """The persona, or None (a chat's persona may have been deleted)."""
        try:
            return self.load(pid or "")
        except PersonaError:
            return None

    def of_chat(self, chat: dict | None) -> dict | None:
        pid = persona_id(chat)
        return self.get(pid) if pid else None

    def _save(self, p: dict) -> None:
        d = self._dir(p["id"])
        d.mkdir(parents=True, exist_ok=True)
        p["updated"] = _now()
        tmp = d / f".persona.{secrets.token_hex(4)}.tmp"
        tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(d / "persona.json")

    def _lock(self, pid: str) -> asyncio.Lock:
        return self.locks.setdefault(pid, asyncio.Lock())

    @asynccontextmanager
    async def edit(self, pid: str):
        async with self._lock(pid):
            p = self.load(pid)
            yield p
            self._tidy(p)
            self._save(p)

    def path(self, pid: str, rel: str) -> Path:
        if not FILE_RE.match(rel or ""):
            raise PersonaError("No such file.", 404)
        root = self._dir(pid)
        p = root / rel
        if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):
            raise PersonaError("No such file.", 404)
        return p

    def file(self, pid: str, rel: str) -> Path:
        p = self.path(pid, rel)
        if not p.is_file():
            raise PersonaError("No such file.", 404)
        return p

    def _put(self, pid: str, rel: str, data: bytes) -> str:
        p = self.path(pid, rel)
        if not (self._dir(pid) / "persona.json").is_file():      # deleted meanwhile: no folder made again for it
            raise PersonaError("No such persona.", 404)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{secrets.token_hex(3)}.tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return rel

    def _tidy(self, p: dict) -> None:
        """Pictures and voice clips nothing points at any more go (what was read aloud stays - it is a cache)."""
        keep = {v["file"] for v in p.get("variants") or []} | {x for x in (p.get("picture"), p.get("voice_clip")) if x}
        for folder in ("pictures", "voice"):
            d = self._dir(p["id"]) / folder
            for f in d.glob("*") if d.is_dir() else []:
                if f.is_file() and not f.name.startswith(".") and f"{folder}/{f.name}" not in keep:
                    f.unlink(missing_ok=True)

    def sweep(self) -> None:
        """At start-up: files a restart cut off while they were written, a voice clip's folder in the workspace."""
        from .. import sandbox_job
        from ..audio import clips
        sandbox_job.sweep(self.hub, clips.TMP)
        if self.root.is_dir():
            for f in self.root.glob("*/**/.*.tmp"):
                if f.is_file() and not f.is_symlink():
                    f.unlink(missing_ok=True)

    async def sweep_after_start(self) -> None:
        self.sweep()

    # ------------------------------------------------------------ the personas
    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for d in self.root.iterdir():
                p = self.get(d.name)
                if p is not None:
                    out.append({"id": p["id"], "name": p["name"], "role": p["role"], "picture": p["picture"],
                                "updated": p.get("updated") or 0})
        return sorted(out, key=lambda x: x["name"].lower())

    def speech_ready(self) -> bool:
        audio = self.audio()
        return bool(audio and audio.available("speech"))

    def face(self, pid: str | None) -> dict | None:
        """A persona as the chat list shows it: its name and picture."""
        p = self.get(pid)
        return {"id": p["id"], "name": p["name"], "picture": p["picture"]} if p is not None else None

    def brief(self, pid: str | None) -> dict | None:
        """What a chat with the persona shows: the name, role, picture, greeting, whether answers are read aloud."""
        p = self.get(pid)
        if p is None:
            return None
        return {"id": p["id"], "name": p["name"], "role": p["role"], "picture": p["picture"],
                "greeting": p["greeting"], "read_aloud": bool(p["read_aloud"]), "speech": self.speech_ready()}

    def chats(self, pid: str, limit: int = 50) -> list[dict]:
        """The persona's chats, the last active first: those with something said in them (a task's chat says
        which task it is - "task")."""
        from ..chats import store as chats
        return [c for c in chats.persona_chats(pid, limit) if c.get("messages")]

    def detail(self, pid: str) -> dict:
        p = self.load(pid)
        p["tasks"] = [{**t, "running": self.task_running(pid, t["id"])} for t in p["tasks"]]
        return {**p, "notes": self.notes(pid), "notes_max": NOTES_MAX, "chats": self.chats(pid),
                "audio": self.audio_info(p.get("language") or ""), "instagram": self.ig_public(pid),
                "job": self.live.get(pid) if self.working(pid) else None, "error": self.errors.get(pid, "")}

    def create(self, fields: dict) -> dict:
        if self.root.is_dir() and sum(1 for d in self.root.iterdir() if (d / "persona.json").is_file()) >= MAX_PERSONAS:
            raise PersonaError(f"There are {MAX_PERSONAS} personas already.")
        p = {**DEFAULTS, "id": "p" + secrets.token_hex(4), "created": _now()}
        p.update(self._clean(fields, p))
        if not p["name"]:
            raise PersonaError("Give the persona a name.")
        self._save(p)
        return self.detail(p["id"])

    async def update(self, pid: str, fields: dict) -> dict:
        async with self.edit(pid) as p:
            p.update(self._clean(fields, p))
            if not p["name"]:
                raise PersonaError("Give the persona a name.")
        return self.detail(pid)

    def delete(self, pid: str) -> None:
        """The persona and its files go; its chats become ordinary chats (the assistant's from their next message)."""
        from ..chats import store as chats
        if self.working(pid):
            raise PersonaError("Portraits are being made - stop them first.", 409)
        if any(self.task_running(pid, t["id"]) for t in self.load(pid)["tasks"]):
            raise PersonaError("A task's post is being made - wait for it first.", 409)
        if any(k.startswith(f"{pid}/") for k in self.ig_jobs):
            raise PersonaError("A post is being put on Instagram - wait for it first.", 409)
        d = self._dir(pid)
        if not (d / "persona.json").is_file():
            raise PersonaError("No such persona.", 404)
        shutil.rmtree(d, ignore_errors=True)
        chats.clear_persona(pid)
        self.errors.pop(pid, None)

    def _clean(self, fields: dict, p: dict) -> dict:
        from ..audio.pipeline import LANGUAGE_NAMES
        out: dict = {}
        for key, limit in LIMITS.items():
            if fields.get(key) is not None:
                out[key] = _text(fields[key], limit) if key in ("name", "role") else _para(fields[key], limit)
        if fields.get("language") is not None:
            lang = _text(fields["language"], 40)
            if lang and lang not in LANGUAGE_NAMES.values():
                raise PersonaError(f"{lang} is not one of the languages the speech models know - choose one of the "
                                   "list.")
            out["language"] = lang
        if fields.get("model") is not None:
            model = _text(fields["model"], 120)
            if model:
                entry = self.hub.catalog.get_llm(model)
                if entry is None or not self.hub.catalog.installed(entry):
                    raise PersonaError("That chat model is not downloaded (Settings -> Models).")
            out["model"] = model
        if fields.get("level") is not None:
            if fields["level"] not in LEVELS:
                raise PersonaError("A persona answers quickly or thinks first (quick or think).")
            out["level"] = fields["level"]
        if fields.get("lora") is not None:
            lora = _text(fields["lora"], 80)
            store = self.hub.extras.get("loras")
            if lora and (store is None or store.get(lora) is None):
                raise PersonaError("That LoRA is not there (Settings -> Models -> LoRAs).")
            out["lora"] = lora
        if fields.get("strength") is not None:
            out["strength"] = round(max(0.0, min(2.0, self._number(fields["strength"], "The strength"))), 2)
        if fields.get("expressiveness") is not None:
            out["expressiveness"] = round(max(0.25, min(2.0, self._number(fields["expressiveness"],
                                                                           "The expression"))), 2)
        for key in ("web", "pictures", "check_pictures", "memory", "read_aloud"):
            if fields.get(key) is not None:
                out[key] = bool(fields[key])
        if fields.get("voice") is not None:
            voice = _text(fields["voice"], 40).lower()
            if voice == "clip":
                if not p.get("voice_clip"):
                    raise PersonaError("Give a clip of the voice first.")
            elif voice:
                info = self.audio_info(out.get("language", p.get("language")) or "")
                names = [v["id"] for v in info["voices"]]
                if names and voice not in names:
                    raise PersonaError(f"{info['speech']} has no voice called {voice} - its voices: "
                                       f"{', '.join(names)}.")
            out["voice"] = voice
        return out

    @staticmethod
    def _number(value, what: str) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError, OverflowError):
            raise PersonaError(f"{what} must be a number.") from None
        if not math.isfinite(v):
            raise PersonaError(f"{what} must be a number.")
        return v

    def options(self) -> dict:
        """What the page offers: the chat models, the languages, the LoRAs for portraits, what is downloaded."""
        from ..audio.pipeline import LANGUAGE_NAMES
        from ..catalog import Catalog
        cat = self.hub.catalog
        models = [{"id": e["id"], "name": Catalog.label(e)} for e in cat.llm_models() if cat.installed(e)]
        pipe, store = self.hub.extras.get("images"), self.hub.extras.get("loras")
        loras = [{k: x.get(k) for k in ("id", "name", "base", "base_label", "trigger", "category", "strength",
                                         "slider")}
                 for x in (store.list() if store else [])]
        return {"models": models, "default_model": self.hub.settings.get("models", "default") or "",
                "languages": sorted(LANGUAGE_NAMES.values()), "loras": loras,
                "pictures": bool(pipe and pipe.available()), "speech": self.speech_ready()}

    # ------------------------------------------------------------ memory
    def _notes_path(self, pid: str) -> Path:
        return self._dir(pid) / "notes.md"

    def notes(self, pid: str) -> str:
        try:
            return self._notes_path(pid).read_text(encoding="utf-8")[:NOTES_MAX]
        except OSError:
            return ""

    def _write_notes(self, pid: str, text: str) -> None:
        d = self._dir(pid)
        tmp = d / f".notes.{secrets.token_hex(3)}.tmp"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(d / "notes.md")

    async def set_notes(self, pid: str, text: str, base: str | None = None) -> dict:
        """The owner reads and corrects what the persona remembers. base: the notes the owner's edit started from -
        when the persona wrote in them meanwhile, nothing is saved (409) and the page shows the new ones."""
        self.load(pid)
        text = _para(text, NOTES_MAX + 1)
        if len(text) > NOTES_MAX:
            raise PersonaError(f"The notes can be at most {NOTES_MAX} characters.")
        async with self._lock(pid):
            if base is not None and _para(base, NOTES_MAX + 1) != _para(self.notes(pid), NOTES_MAX + 1):
                raise PersonaError("The notes changed while you edited them - the persona wrote in them. Your text "
                                   "is still there: look at what is new (Undo shows it), then save again.", 409)
            self._write_notes(pid, text + ("\n" if text else ""))
        return self.detail(pid)

    async def remember(self, pid: str, action: str, text: str, old: str = "") -> str:
        """The remember tool: a note added, replaced or forgotten - one line each. -> what happened, for the model."""
        self.load(pid)
        text, old = _note(text), _note(old)
        if len(text) > NOTE_MAX:
            raise PersonaError(f"A note is at most {NOTE_MAX} characters - say it shorter, or in two notes.")
        async with self._lock(pid):
            lines = [x for x in self.notes(pid).splitlines() if x.strip()]
            bare = [_note(x) for x in lines]
            same = [i for i, x in enumerate(bare) if text and x.lower() == text.lower()]

            def find(words: str) -> int:
                w = words.lower()
                exact = [i for i, x in enumerate(bare) if w and w == x.lower()]
                part = [i for i, x in enumerate(bare) if w and w in x.lower()]
                if exact:
                    return exact[0]
                if len(part) > 1:
                    raise PersonaError("Several notes say that: " + "; ".join(bare[i] for i in part[:5])
                                       + " - give more of the one you mean (old).")
                if not part:
                    raise PersonaError("No note says that (old)." + (" Add it instead." if action == "replace"
                                                                     else ""))
                return part[0]
            if action == "add":
                if not text:
                    raise PersonaError("Say what to remember (text).")
                if same:
                    return "That is in your notes already."
                lines.append(f"- {text}")
            elif action == "replace":
                if not text:
                    raise PersonaError("Give the new note (text).")
                i = find(old)
                lines[i] = f"- {text}"
                for j in sorted((j for j in same if j != i), reverse=True):     # the new note was there already
                    del lines[j]
            elif action == "forget":
                del lines[find(old or text)]
            else:
                raise PersonaError("action is add, replace or forget.")
            out = "\n".join(lines) + ("\n" if lines else "")
            if len(out) > NOTES_MAX:
                raise PersonaError("Your notes are full - replace or forget an old note first.")
            self._write_notes(pid, out)
        return {"add": "Noted.", "replace": "Changed.", "forget": "Forgotten."}[action]

    def recent(self, pid: str, exclude: int | None = None, limit: int = RECENT) -> list[tuple[str, str]]:
        """The persona's last conversations with the owner: (when, title), newest first - not the one going on, none
        where nothing was said, and no task's chat (its posts are not a conversation)."""
        return [(str(c.get("updated_at") or ""), c["title"]) for c in self.chats(pid, limit + 20)
                if c["id"] != exclude and not c.get("task")][:limit]

    def memory_of(self, p: dict, chat_id: int | None) -> dict:
        """What a chat with the persona starts from: its notes and the titles of the last chats, as they are now. A chat
        keeps it until it is condensed (agent/loop.py), so a note written during the chat does not change the start of
        every request after it - the model server would read the whole conversation again."""
        if not p.get("memory"):
            return {"notes": "", "recent": []}
        return {"notes": self.notes(p["id"]), "recent": [list(x) for x in self.recent(p["id"], exclude=chat_id)]}

    def start_chat(self, pid: str) -> dict:
        """A chat with the persona: an empty one of its own if it has one (a click on Chat leaves no pile of empty
        chats), else a new one. Its model and level come from the persona at each turn (agent/loop.py), its title
        from the first message, like any chat's."""
        from ..chats import store as chats
        self.load(pid)
        empty = next((c for c in chats.persona_chats(pid, 20) if not c.get("messages") and not c.get("archived")
                      and not c.get("task")), None)
        if empty is not None:
            return chats.get_chat(empty["id"])
        return chats.create_chat(None, "New chat", {"persona": pid})

    # ------------------------------------------------------------ the picture
    async def set_picture(self, pid: str, data: bytes) -> dict:
        """A picture of the owner's as the persona's: any picture Pillow reads, upright as it is shown (a phone
        photo's EXIF turn), kept as a PNG at most 1024 px."""
        from PIL import Image, ImageOps, UnidentifiedImageError
        self.load(pid)
        if not data or len(data) > MAX_PICTURE:
            raise PersonaError("A picture of at most 20 MB.", 413 if data else 400)
        try:
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                img = ImageOps.exif_transpose(img).convert("RGB")
                img.thumbnail((1024, 1024))
                buf = io.BytesIO()
                img.save(buf, "PNG")
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise PersonaError("That is not a picture.") from None
        async with self.edit(pid) as p:
            rel = self._put(pid, f"pictures/up-{secrets.token_hex(4)}.png", buf.getvalue())
            p["variants"] = self._trim([{"file": rel, "made": _now(), "own": True}, *p["variants"]], rel)
            p["picture"] = rel
        return self.detail(pid)

    async def pick(self, pid: str, file: str) -> dict:
        async with self.edit(pid) as p:
            if not any(v["file"] == file for v in p["variants"]):
                raise PersonaError("No such picture.", 404)
            p["picture"] = file
        return self.detail(pid)

    @staticmethod
    def _trim(variants: list[dict], chosen: str) -> list[dict]:
        out = variants[:MAX_VARIANTS]
        if chosen and all(v["file"] != chosen for v in out):
            out += [v for v in variants if v["file"] == chosen]
        return out

    def working(self, pid: str) -> bool:
        t = self.tasks.get(pid)
        return t is not None and not t.done()

    def me_way(self, p: dict) -> str:
        """How a picture of the persona itself is made here (0.75.0): "lora" - its LoRA, on a downloaded model of
        its kind; "portrait" - an edit of its chosen portrait; "" - neither can be made now."""
        pipe, store = self.hub.extras.get("images"), self.hub.extras.get("loras")
        if pipe is None or not pipe.available():
            return ""
        if p.get("lora") and store is not None and store.get(p["lora"]) is not None:
            try:
                self.portrait_model(p)
                return "lora"
            except PersonaError:
                pass
        try:
            if p.get("picture") and pipe.installed("edit") and self.path(p["id"], p["picture"]).is_file():
                return "portrait"
        except PersonaError:
            pass
        return ""

    def portrait_model(self, p: dict) -> str | None:
        """Settings' picture model - or, for a LoRA made for another kind of model, a downloaded one of its kind."""
        from ..images.pipeline import family
        pipe, store = self.hub.extras["images"], self.hub.extras.get("loras")
        lora = store.get(p["lora"]) if store and p["lora"] else None
        if lora is None or family(pipe.entry(pipe.chosen(None))) == lora.get("base"):
            return None
        fits = [e for e in pipe.installed("generate") if family(e) == lora.get("base")]
        if not fits:
            raise PersonaError(f"The LoRA {lora['name']} is for {lora.get('base_label') or lora.get('base')} - no "
                               "picture model of that kind is downloaded (Settings -> Models).")
        return fits[0]["id"]

    def make_portrait(self, pid: str) -> None:
        p = self.load(pid)
        pipe = self.hub.extras.get("images")
        if pipe is None or not pipe.available():
            raise PersonaError("No picture model is downloaded (Settings -> Models).")
        if not (p["look"] or p["role"] or p["lora"]):
            raise PersonaError("Describe how the persona looks first.")
        if self.working(pid):
            raise PersonaError("Portraits are being made already.", 409)
        model = self.portrait_model(p)
        self.errors.pop(pid, None)
        self.live[pid] = {"what": "portraits"}
        self.tasks[pid] = asyncio.get_running_loop().create_task(self._portrait(pid, model))

    async def cancel(self, pid: str) -> None:
        t = self.tasks.get(pid)
        if t is not None and not t.done():
            t.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(t), 10)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass

    async def _portrait(self, pid: str, model: str | None) -> None:
        from ..chats import store as chats
        from ..images.pipeline import ImageError
        from ..projects import blobs
        p = self.load(pid)
        look = p["look"].rstrip(". ")
        prompt = PORTRAIT.format(who=someone(p["role"]), look=f"{look}. " if look else "")
        loras = [{"id": p["lora"], "strength": p["strength"]}] if p["lora"] else []

        async def progress(value: int, maximum: int, node=None):
            self.live.get(pid, {}).update(value=value, max=maximum)
        made: set[str] = set()
        listening = chats.upload_listener.set(lambda sha, new: made.add(sha) if new else None)
        try:
            items = await self.hub.extras["images"].generate(prompt, 1024, 1024, count=4, model=model,
                                                              owner="personas", priority=PRIORITY,
                                                              on_progress=progress, loras=loras)
            pictures = [blobs.read(it["sha"]) for it in items]
            async with self.edit(pid) as p:
                tok = secrets.token_hex(3)
                new = [{"file": self._put(pid, f"pictures/{tok}-{i + 1}.png", data), "made": _now()}
                       for i, data in enumerate(pictures)]
                p["variants"] = self._trim(new + p["variants"], p["picture"])
                if not p["picture"] and new:
                    p["picture"] = new[0]["file"]
        except asyncio.CancelledError:
            self.errors[pid] = "Stopped."
        except ImageError as exc:
            self.errors[pid] = f"The portraits failed: {exc}"
        except PersonaError:
            pass                                              # deleted meanwhile
        except Exception as exc:  # noqa: BLE001 - the page says what stopped it
            log.exception("portraits of %s", pid)
            self.errors[pid] = f"It stopped: {exc}"
        finally:
            chats.upload_listener.reset(listening)
            for sha in made:                                  # the persona keeps its own copies
                try:
                    chats.forget_upload(sha)
                except Exception:  # noqa: BLE001 - the rest still goes
                    log.exception("could not delete %s", sha[:12])
            self.live.pop(pid, None)

    # ------------------------------------------------------------ the voice
    def audio(self):
        return self.hub.extras.get("audio")

    def audio_info(self, language: str) -> dict:
        """The speech model for the persona's language (Danish when it answers in the owner's) and its voices."""
        from ..audio.pipeline import AudioError
        from ..movies.cut import lang_code
        audio = self.audio()
        code = lang_code(language) or "da"
        info = {"language": code, "speech": "", "voices": [], "note": "", "expressive": False}
        if audio is None:
            return info
        try:
            entry, note = audio.speech_model(code)
            info.update(speech=entry["name"], note=note, expressive=bool(entry.get("expressive")),
                        voices=[{"id": k, "description": v} for k, v in audio.voices(entry).items()
                                if k != "default"])
        except AudioError as exc:
            info["note"] = str(exc)
        return info

    async def set_voice_clip(self, pid: str, data: bytes, filename: str = "") -> dict:
        from ..audio import clips
        self.load(pid)
        try:
            out, seconds = await clips.convert(self.hub, data, filename)
        except clips.ClipError as exc:
            raise PersonaError(str(exc), exc.status) from exc
        async with self.edit(pid) as p:
            p["voice_clip"] = self._put(pid, f"voice/{secrets.token_hex(4)}.wav", out)
            p["voice_clip_seconds"] = round(seconds, 1)
            p["voice"] = "clip"
        return self.detail(pid)

    def say(self, pid: str, chat_id: int, message_id: int) -> dict:
        """One of the persona's answers read aloud in its voice: {"path"} when it is there, {"making": true} while it
        is made - ask again (a long answer takes minutes on the CPU: no request waits that long, and a proxy in front
        of the Hub would give up). Made once, kept while the voice is the same. The answer starts at message_id and
        goes on as the chat shows it: the turn's messages up to the next message of the owner's."""
        from ..chats import store as chats
        p = self.load(pid)
        chat = chats.get_chat(chat_id)
        if chat is None or persona_id(chat) != pid:
            raise PersonaError("No such chat with this persona.", 404)
        rows = chats.messages(chat_id, after_id=int(message_id) - 1)
        if not rows or rows[0]["id"] != message_id or rows[0]["role"] != "assistant":
            raise PersonaError("No such answer.", 404)
        run, said = rows[0]["run_id"], []
        for m in rows:
            if m["role"] == "user" or (run and m["run_id"] and m["run_id"] != run):
                break
            if m["role"] == "assistant":
                said += [b["text"] for b in m["content"] if b.get("type") == "text" and b.get("text")]
        text = speakable("\n\n".join(said))
        if not text:
            raise PersonaError("There is nothing to read aloud in that answer.")
        return self._speak(p, text, f"m{int(message_id)}")

    def sample(self, pid: str) -> dict:
        """How the persona sounds: its greeting, or a line to hear the voice by."""
        from ..movies.cut import lang_code
        p = self.load(pid)
        code = lang_code(p["language"]) if p["language"] else "da"
        text = speakable(p["greeting"]) or SAMPLE.get(code, SAMPLE["en"]).format(name=p["name"])
        return self._speak(p, text, "sample")

    def _speak(self, p: dict, text: str, prefix: str) -> dict:
        from ..audio.pipeline import AudioError
        from ..movies.cut import lang_code
        audio = self.audio()
        if audio is None or not audio.available("speech"):
            raise PersonaError("No speech model is downloaded (Settings -> Models -> Sound, music and speech).")
        code = (lang_code(p["language"]) if p["language"] else "") or guess_language(text)
        try:
            entry = audio.speech_model(code)[0]
        except AudioError as exc:
            raise PersonaError(f"It cannot be read aloud: {exc}") from exc
        clip = p["voice_clip"] if p["voice"] == "clip" and p["voice_clip"] else ""
        name = p["voice"] if p["voice"] not in ("", "clip") and p["voice"] in audio.voices(entry) else ""
        stamp = hashlib.sha256(json.dumps([text, clip or name, p["expressiveness"], entry["id"], code])
                               .encode()).hexdigest()[:12]
        rel = f"said/{prefix}-{stamp}.mp3"
        key = f"{p['id']}/{rel}"
        if self.path(p["id"], rel).is_file():
            return {"path": rel}
        if key in self.unsaid:                               # the last try failed: say why, once
            raise PersonaError(self.unsaid.pop(key))
        job = self.speaking.get(key)
        if job is None or job.done():
            voice = self.file(p["id"], clip).read_bytes() if clip else (name or None)
            self.speaking[key] = asyncio.get_running_loop().create_task(
                self._make_speech(p["id"], key, rel, prefix, dict(text=text, language=code, voice=voice,
                                                                     expressiveness=p["expressiveness"],
                                                                     model=entry["id"], fmt="mp3")))
        return {"making": True}

    async def _make_speech(self, pid: str, key: str, rel: str, prefix: str, args: dict) -> None:
        from ..audio.pipeline import AudioError
        try:
            out = await self.audio().speak(**args)
            folder = self.path(pid, rel).parent
            if folder.is_dir():                              # the same answer in a voice the persona no longer has
                for old in folder.glob(f"{prefix}-*.mp3"):
                    old.unlink(missing_ok=True)
            self._put(pid, rel, out["data"])
            self._prune(pid)
        except AudioError as exc:
            self.unsaid[key] = f"Reading it aloud failed: {exc}"
        except PersonaError:
            pass                                             # deleted meanwhile
        except Exception as exc:  # noqa: BLE001 - the page says what stopped it
            log.exception("reading aloud %s", key)
            self.unsaid[key] = f"Reading it aloud stopped: {exc}"
        finally:
            self.speaking.pop(key, None)

    def _prune(self, pid: str) -> None:
        """The answers read aloud: the newest MAX_SAID kept (one is made again when it is asked for)."""
        folder = self._dir(pid) / "said"
        files = sorted((f for f in folder.glob("*.mp3") if f.is_file()), key=lambda f: f.stat().st_mtime,
                       reverse=True) if folder.is_dir() else []
        for f in files[MAX_SAID:]:
            f.unlink(missing_ok=True)
