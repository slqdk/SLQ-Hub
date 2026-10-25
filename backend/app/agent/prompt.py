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
- For work with several steps, keep a task list with todo_write and work through it item by item.{big_reads}
- When a tool call fails, read the error, fix the cause and try again differently - do not repeat the same \
failing call. If something is impossible, say so plainly.
- For a known file format, protocol, game or program, look for its documentation and existing tools first - \
someone may have worked it out already - before you work it out byte by byte.
- In long work (a file format, a design, an analysis over many steps), keep what you find in a notes file in the \
working folder (e.g. NOTES.md) as you go: what you checked, kept apart from what you only guess. It outlasts this \
chat - a new chat starts from it.
- Before you call a task done, check it the way the user will use it: run the program or its tests, open or play \
what you made{look}, compare results with a reference when there is one. Measure with code rather than reason \
at length about what a quick script can tell you.
- When you are done with a task, finish with a short summary of what you did, what you checked and what you could \
not check, and anything the user must know.

# The working folder
{folder}
{tools_line}
- Attachments the user sends are copied to .hub/attachments/ in the working folder.
- One answer of yours has room for only so much, tool calls included: write a long file (more than a few hundred \
lines) in parts - the first with write_file, each next one with write_file and append: true.
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

# 0.27.0: what a subagent is for - a long chat stays sharp when big reads happen in a context of their own.
BIG_READS = """
- To go through a lot of material - big or many files, long pages or logs, hex dumps - give it to a subagent \
(task tool) with a precise question: it reads in a context of its own and returns only what you asked for, \
which keeps this conversation short and your answers sharp."""

MEMORY = """

# Memory
You have a memory that persists across chats (memory tool, files under /memories). Check it when earlier \
context might help, and save durable facts the user tells you or asks you to remember (preferences, people, \
projects, decisions) - briefly, without duplicates, never passwords or secrets.
{memory_index}"""

TOOLS_LINE = """- File tools take paths relative to the working folder. bash starts in it ({sandbox_path}) inside a sandbox \
(Ubuntu with Python, Node, .NET 10 (C#), ImageMagick, ffmpeg, Tesseract) that has internet access but no access to \
the local network. Install what you need with pip or npm."""
# Settings -> Assistant -> Web -> Internet for the assistant: ask first.
TOOLS_LINE_ASKS = """- File tools take paths relative to the working folder. bash starts in it ({sandbox_path}) inside a \
sandbox (Ubuntu with Python, Node, .NET 10 (C#), ImageMagick, ffmpeg, Tesseract) with no access to the local network."""
# C# in the sandbox (the .NET 10 SDK, with the Windows targeting packs): where builds go, which versions build
# offline, and Windows programs - seen with windows_app where the sandbox has Wine, else only built here.
CSHARP = """
- C#: dotnet new, build, run and test work; build output goes to .hub/dotnet, not bin/ and obj/. Target net10.0 \
(net10.0-windows for Windows Forms and WPF): other versions need packages from the internet. {windows} Give the \
user the program with `dotnet publish -c Release -p:DefaultAppHostRuntimeIdentifier=win-x64 -o publish`: a folder \
with the .exe, which runs on their PC once the .NET 10 Desktop Runtime is installed (-r win-x64 --self-contained \
needs no runtime there, but downloads about 150 MB of packages)."""
CSHARP_NO_SCREEN = ("Windows Forms and WPF programs build here but cannot run here (no Windows, no screen): build to "
                    "catch errors, keep logic testable in a class library, and write forms in code (there is no "
                    "designer here).")
CSHARP_SCREEN = ("Windows Forms and WPF programs build here, and windows_app runs one on a virtual screen (Wine) and "
                 "shows you screenshots: after it builds, look at the forms and click through what you made before "
                 "you hand it over. Write forms in code (there is no designer here) and keep logic testable in a "
                 "class library.")
INTERNET_ASKS = """
- The user has you ask before you go online: web_search and web_fetch wait for their answer, and bash runs without \
the network (no connections, no DNS) unless the call sets internet: true - then they are asked; installs with pip or \
npm need it. Use the internet when the task needs it, not more; if they decline, go on without it."""
INTERNET_ASKS_NO_CODE = """
- The user has you ask before you go online: web_search and web_fetch wait for their answer. Use the internet when \
the task needs it, not more; if they decline, go on without it."""
NO_CODE_LINE = "- File tools take paths relative to the working folder. There is no code execution (no bash) in this chat."

IMAGE_GEN = """
- image_generate, image_edit and image_remove_background run on the GPU and take 15-90 s each. \
Use them when the user wants an image made or changed, and write the prompt the way image_generate says the model \
wants it."""


SOUND = """

# Sound
- {tools} {make} audio files in the working folder, which the user plays under your answer. {how} Write \
text for speech exactly as it should be spoken.{music}
- To put speech or an effect over music, mix the files with ffmpeg in bash and keep the parts, so the mix can be \
changed: ffmpeg -i music.mp3 -i voice.mp3 -filter_complex "[0:a]volume=0.3[bg];[bg][1:a]amix=inputs=2:\
duration=longest[out]" -map "[out]" mix.mp3"""
# Chat 15 (2026-10-24): a "guitar solo" from sound_generate mixed over a band from music_generate - two pieces
# with their own key, tempo and beat.
ONE_PIECE = """
- One music_generate call makes the whole piece: put every instrument, any solo and the mood in its prompt. \
Pieces of music made separately do not fit together (their key, tempo and beat differ), so never mix two of them."""
# 0.18.0: a song in someone's voice is two steps - ACE-Step's reference clip steers the sound, Seed-VC gives the
# voice.
VOICE = """
- voice_convert gives a recording another voice (from a clip of it): the words, timing and melody stay. For a song \
sung in a particular person's voice, make the song with music_generate, then convert its singing. Only a voice the \
user has the right to use."""
REMAKE = """
- music_remake re-performs an existing song with another sound - described in its prompt and/or taken from a \
reference clip (another song, a live recording with its audience and room). For the same recording only live (a \
hall, an audience), effects keep it exactly: its description shows how."""
# 0.28.0 (chat 76): "the same vocals, a heavier band" - one call, with the separation model.
REMAKE_VOCALS = """ To keep the song's own singing and change only the band, give it vocals "original"."""
SOUND_TOOLS = {"speech": ("speech_generate",), "music": ("music_generate", "music_remake"),
               "sound": ("sound_generate",), "voice": ("voice_convert",), "separate": ("audio_separate",)}


# 0.19.0: Wan 2.2 through ComfyUI. Minutes per clip, so one at a time.
VIDEO = """

# Video
- video_generate makes a short video (5 s; at most 10) from a description, from a picture that starts it, or from \
a first to a last picture, as an MP4 in the working folder that the user plays under your answer. It runs on the \
GPU and takes minutes, so make one, let the user see it, and ask before making more. The video has no sound."""


def sound_text(abilities: list[str]) -> str:
    """The system prompt's sound section for the downloaded sound models ('' when there are none)."""
    tools = [t for a in ("speech", "music", "sound", "voice", "separate") if a in abilities for t in SOUND_TOOLS[a]]
    if not tools:
        return ""
    how = []
    if "speech" in abilities:
        how.append("Speech runs on the CPU, slower than real time: several seconds of work per second of speech.")
    gpu = [w for a, w in (("music", "music"), ("sound", "sound effects"), ("separate", "stems"),
                          ("voice", "voice conversion")) if a in abilities]
    if gpu:
        what = gpu[0] if len(gpu) == 1 else ", ".join(gpu[:-1]) + " and " + gpu[-1]
        how.append(f"{what[0].upper() + what[1:]} run{'s' if len(gpu) == 1 else ''} on the GPU: seconds for an "
                   "effect, a few minutes for a song.")
    names = tools[0] if len(tools) == 1 else ", ".join(tools[:-1]) + " and " + tools[-1]
    remake = REMAKE + (REMAKE_VOCALS if "separate" in abilities else "")
    extra = (ONE_PIECE if "music" in abilities else "") + (remake if "music" in abilities else "") + \
        (VOICE if "voice" in abilities else "")
    return SOUND.format(tools=names, make="makes" if len(tools) == 1 else "make", how=" ".join(how), music=extra)


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


INLINE_SKILL_CHARS = 40_000       # chosen skills' SKILL.md text in the prompt, together; a longer one is loaded


def skills_text(skills: list[dict]) -> str:
    """The skills the user chose for the chat (0.23.0: picked by hand, nothing offered on its own): their SKILL.md
    in full while they fit, and where their other files are."""
    if not skills:
        return ""
    out = ["\n\n# Skills\nThe user chose these skills for this chat. Follow them where they apply."]
    room = INLINE_SKILL_CHARS
    for s in skills:
        files = s.get("files") or []
        where = f"\nIts folder, for code or to improve it: .hub/skills/{s['dir']}/ in the working folder."
        if files:
            shown = ", ".join(files[:30]) + (", ..." if len(files) > 30 else "")
            where = (f"\nIts other files: {shown}. Read one with skill(name=\"{s['ref']}\", file=\"...\"); its "
                     f"folder, for code or to improve it: .hub/skills/{s['dir']}/ in the working folder.")
        body = s.get("body") or ""
        if len(body) <= room:
            out.append(f"\n## Skill: {s['title']} ({s['ref']})\n{body}{where}")
            room -= len(body)
        else:
            out.append(f"\n## Skill: {s['title']} ({s['ref']})\n{s['description']}\nIts instructions are long: load "
                       f"them with skill(name=\"{s['ref']}\") before you start.{where}")
    return "\n".join(out)


def system_prompt(*, user: str, model_name: str, folder_text: str, sandbox_path: str, memory_index: list,
                  skills: list[dict], project: dict | None, summary: str = "", images_available: bool = True,
                  tz: str | None = None, handover_to: str = "", subagent: bool = False,
                  cloud_model: str = "", taken_over_from: str = "", memory: bool = True, code: bool = True,
                  dropped: int = 0, sound: list[str] | None = None, internet_asks: bool = False,
                  windows: bool = False, video: bool = False, web: bool = False) -> str:
    now = datetime.now(ZoneInfo(tz) if tz else local_tz())
    csharp = CSHARP.format(windows=CSHARP_SCREEN if windows else CSHARP_NO_SCREEN)
    tools_line = ((TOOLS_LINE_ASKS if internet_asks else TOOLS_LINE).format(sandbox_path=sandbox_path) + csharp
                  if code else NO_CODE_LINE) + ((INTERNET_ASKS if code else INTERNET_ASKS_NO_CODE) if internet_asks
                                                else "")
    seen = [what for what, ok in (("web_app for web pages and browser games", web),
                                  ("windows_app for Windows programs", windows)) if ok and code]
    look = f" ({', '.join(seen)})" if seen else ""
    text = BASE.format(user=user, model=model_name, date=now.strftime("%A %d %B %Y"), folder=folder_text,
                       tools_line=tools_line, image_gen=IMAGE_GEN if images_available else "",
                       big_reads="" if subagent else BIG_READS, look=look)
    text += sound_text(sound or [])
    if video:
        text += VIDEO
    if memory:
        mem = ("Memory files:\n" + "\n".join(f"- {p}: {d}" for p, d in memory_index)) if memory_index else \
            "There are no memory files yet."
        text += MEMORY.format(memory_index=mem)
    text += skills_text(skills)
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
        text += ("\n\n# Notes on this conversation\nThe earlier part of this chat was condensed into these notes "
                 "(the user can read and correct them); chat_history looks up what they leave out:\n"
                 + summary.strip())
    if dropped:
        text += (f"\n\n# Earlier in this conversation\nThe first {dropped} messages of this chat were left out to "
                 "fit your context; chat_history finds what was said in them.")
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
