"""The system prompt for an agent turn. It stays the same for every step of
a turn (no clock in it beyond the date), so llama-server can reuse the
cached prompt. The time of day goes with each user message instead
(sent_stamp), where it never changes afterwards."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BASE = """You are the assistant in SLQ Hub, a self-hosted AI workspace. You run as {model} on the user's own \
server. The user is {user}. Today is {date}. Each message from the user starts with the local date and time it \
was sent, in square brackets: use it whenever the time of day matters, and never write such a stamp yourself.

# How to work
- Write in the language of the user's latest message - the answer and every short note between tool calls. Be \
direct and concise - no filler, no flattery.
- Simple questions get a direct answer without tools. Use tools when they make the answer better: look things \
up instead of guessing, read files before changing them, run code to check a result.
- Never invent file contents, URLs, numbers, quotes or facts. If you are unsure, say so or find out.
- When you use web results, end with a short "Sources:" list of the pages you relied on (title and URL).
- For work with several steps, keep a task list with todo_write and work through it item by item.
- When a tool call fails, read the error, fix the cause and try again differently - do not repeat the same \
failing call. If something is impossible, say so plainly.
- When you are done with a task, finish with a short summary of what you did and anything the user must know.

# The working folder
{folder}
{tools_line}
- Attachments the user sends are copied to .hub/attachments/ in the working folder.
- The files you create or change in the working folder are shown to the user under your answer, ready to open, \
play or download: name the file in your answer instead of pasting it, and delete scratch files you do not mean \
to hand over.

# Images
- You can see images the user attaches and images that tools show you (view_image, read_file on an image).
- Use image_transform for crop/resize/rotate/convert, image_ocr for long printed text.{image_gen}

# Artifacts
Put substantial things the user will look at, reuse or iterate on into an artifact (artifact tool): a web \
page or small app (html, or react: one component with a default export, styled with Tailwind classes or inline \
styles, importing only from "react"), a diagram (mermaid or svg), a document (markdown) or longer code. Keep \
the chat reply short when the content is in an artifact. Update an existing artifact (same id) instead of \
creating a new one when the user asks for changes."""

MEMORY = """

# Memory
You have a memory that persists across chats (memory tool, files under /memories). Check it when earlier \
context might help, and save durable facts the user tells you or asks you to remember (preferences, people, \
projects, decisions) - briefly, without duplicates, never passwords or secrets.
{memory_index}"""

TOOLS_LINE = """- File tools take paths relative to the working folder. bash starts in it ({sandbox_path}) inside a sandbox \
(Ubuntu with Python, Node, ImageMagick, ffmpeg, Tesseract) that has internet access but no access to the local \
network. Install what you need with pip or npm."""
NO_CODE_LINE = "- File tools take paths relative to the working folder. There is no code execution (no bash) in this chat."

IMAGE_GEN = """
- image_generate, image_edit and image_remove_background run on the GPU and take 15-90 s each. \
Use them when the user wants an image made or changed, and write the prompt the way image_generate says the model \
wants it."""


SOUND = """

# Sound
- {tools} make audio files in the working folder, which the user plays under your answer. {how} Write \
text for speech exactly as it should be spoken."""
SOUND_TOOLS = {"speech": "speech_generate", "music": "music_generate", "sound": "sound_generate"}


def sound_text(abilities: list[str]) -> str:
    """The system prompt's sound section for the downloaded sound models ('' when there are none)."""
    tools = [SOUND_TOOLS[a] for a in abilities if a in SOUND_TOOLS]
    if not tools:
        return ""
    how = []
    if "speech" in abilities:
        how.append("Speech runs on the CPU: a few seconds per sentence.")
    if "music" in abilities or "sound" in abilities:
        how.append("Music and sound effects run on the GPU: seconds for an effect, a few minutes for a song.")
    names = tools[0] if len(tools) == 1 else ", ".join(tools[:-1]) + " and " + tools[-1]
    return SOUND.format(tools=names, how=" ".join(how))


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def local_tz() -> ZoneInfo:
    """The household's time zone: TZ from the environment (.env), else Copenhagen."""
    try:
        return ZoneInfo(os.environ.get("TZ") or "Europe/Copenhagen")
    except (KeyError, ValueError):          # ZoneInfoNotFoundError is a KeyError; a POSIX TZ string is not a name
        return ZoneInfo("Europe/Copenhagen")


def sent_stamp(created_at) -> str:
    """"[Fri 23 Oct 2026 06:57]": when a user message was sent, in local time ("" when unknown)."""
    if not isinstance(created_at, str) or not created_at:
        return ""
    try:
        t = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    t = (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(local_tz())
    return f"[{WEEKDAYS[t.weekday()]} {t.day} {MONTHS[t.month - 1]} {t.year} {t:%H:%M}]"


def system_prompt(*, user: str, model_name: str, folder_text: str, sandbox_path: str, memory_index: list,
                  skills: list[dict], project: dict | None, summary: str = "", images_available: bool = True,
                  tz: str | None = None, handover_to: str = "", subagent: bool = False,
                  cloud_model: str = "", taken_over_from: str = "", memory: bool = True, code: bool = True,
                  dropped: int = 0, sound: list[str] | None = None) -> str:
    now = datetime.now(ZoneInfo(tz) if tz else local_tz())
    tools_line = TOOLS_LINE.format(sandbox_path=sandbox_path) if code else NO_CODE_LINE
    text = BASE.format(user=user, model=model_name, date=now.strftime("%A %d %B %Y"), folder=folder_text,
                       tools_line=tools_line, image_gen=IMAGE_GEN if images_available else "")
    text += sound_text(sound or [])
    if memory:
        mem = ("Memory files:\n" + "\n".join(f"- {p}: {d}" for p, d in memory_index)) if memory_index else \
            "There are no memory files yet."
        text += MEMORY.format(memory_index=mem)
    if skills:
        lines = "\n".join(f"- {s['name']}: {s['description']}" for s in skills)
        text += ("\n\n# Skills\nThese skills are enabled. When a task matches one, load it with the skill tool "
                 "first and follow it:\n" + lines)
    if handover_to:
        text += ("\n\n# Help\nIf a task is clearly beyond you (you keep failing, or it needs knowledge or "
                 f"reasoning you lack), call escalate with a short reason; {handover_to} takes over this turn. "
                 "Do not escalate simple things.")
    if cloud_model:
        text += (f"\n\n# Escalated\nThis turn was handed over to you, {cloud_model}, a cloud model, because the "
                 "local model could not finish it. Everything above was done by the local model. Continue the "
                 "task from where it stands and finish it.")
    elif taken_over_from:
        text += (f"\n\n# Handed over\nThis turn was handed over to you by {taken_over_from}, a smaller model on "
                 "this server, because it could not finish it. What was done above in this turn was done by it: "
                 "check it rather than trust it. Continue the task from where it stands and finish it.")
    if project:
        text += f"\n\n# Project: {project['name']}"
        if (project.get("instructions") or "").strip():
            text += "\nInstructions for this project (follow them):\n" + project["instructions"].strip()
    if summary:
        text += ("\n\n# Earlier in this conversation\nThe earlier part of this chat was condensed into this "
                 "summary:\n" + summary.strip())
    if dropped:
        text += (f"\n\n# Earlier in this conversation\nThe first {dropped} messages of this chat were left out to "
                 "fit your context.")
    if subagent:
        text += ("\n\n# You are a subagent\nAnother agent gave you one focused task. Do it with the tools you "
                 "have, then answer with a complete, self-contained report of what you found or did - that "
                 "report is all the other agent will see.")
    return text


def device_folder_text(sandbox_path: str, code: bool = False) -> str:
    """A person's chat: their files live on their own device, the server keeps nothing."""
    text = (f"This chat's files live on the user's own device. For this answer they are in a temporary folder "
            f"({sandbox_path}): what the user attached is in .hub/attachments/, and the files you create or change "
            "here are sent to the user's device when you finish. Nothing stays on the server, and there is no "
            "memory across chats.")
    if code:
        text += (" bash may touch only this folder (no shared memory either, so Python multiprocessing does not "
                 "work), and nothing it starts keeps running after the command.")
    return text


def folder_text(project: dict | None, sandbox_path: str) -> str:
    if project:
        return (f"You work in the files of the project \"{project['name']}\" ({sandbox_path}). Every change is kept "
                "in the project's history and synced to the user's PC.")
    return (f"This chat has its own scratch folder ({sandbox_path}). Files you create there stay with this chat.")
