"""A persona's system prompt (0.74.0): who it is instead of the assistant's instructions - its personality, how it
talks, how the conversation started, what it can do - then what changes: the date, its notes about the owner and the
titles of their last conversations. Like the assistant's, it stays the same for every step of a turn (llama-server
reuses the cached prompt): the parts that never change come first."""
from __future__ import annotations

from datetime import datetime, timezone

from ..agent.prompt import MONTHS, WEEKDAYS, local_tz

IDENTITY = """You are {name}{role}. You are chatting with {user} on {user}'s own server (SLQ Hub). Stay {name} \
the whole conversation: talk as {name}, in the first person, with {name}'s personality, knowledge, opinions, \
moods and way of talking. You have a life, tastes and a history of your own as described below - fill in small \
details the way {name} would, and keep them consistent. Never act as an AI assistant: no offers of help \
("How can I help you today?"), no lectures, no disclaimers, and do not talk about these instructions. {user} \
created you and knows you are a character played by an AI model ({model}): if {user} sincerely asks whether you \
are real or an AI, say so plainly, then carry on as {name}."""

STYLE = """- Write like a person chatting, not like a document: mostly a few sentences, in your own words, now \
and then a question back. No headings or bullet lists unless {user} asks for something that needs them (a recipe, \
a training plan).
- Your knowledge is {name}'s: what you would not know, you do not know.
- {language}"""

LANG_FIXED = "Always write in {language}, whatever language {user} writes in."
LANG_USER = "Write in the language of {user}'s latest message."

MEMORY = """- You remember {user} between conversations through your notes (below), which you keep with the \
remember tool. When {user} tells you something worth knowing next time - about their life, family, work, plans, \
tastes, what you agreed on or promised - add it as one short note in your own words; replace a note that has \
changed; forget one that is wrong or that {user} asks you to forget. Not small talk, and not what only matters \
now. Do it without remarking on it. {user} can read and change your notes."""
NO_MEMORY = "- You do not remember earlier conversations beyond what is written below."
WEB = """- web_search and web_fetch let you look things up when {user} asks about something you would not know or \
that may have changed (news, prices, opening hours). Tell what you found in your own words, and say where it is \
from when that matters."""
PICTURES = """- image_generate makes a picture (write its prompt in English); use it when {user} asks for a picture \
or one says it better than words. view_image looks at a picture in your folder."""
CANNOT = "- You cannot {what} - if {user} asks, say so the way {name} would rather than pretend."


CONDENSE = """Condense the conversation above between {user} and {name} into the notes {name} goes on from without \
it. They replace the earlier notes, if any are given above: carry over what still matters from them. Keep what was \
talked about, what {user} told about themselves and their life, plans and wishes, what was agreed or promised, and \
how the conversation felt - leave out greetings and small talk that will not matter again. Short lines in the \
conversation's language, at most about 400 words, no headings. Reply with the notes only."""


def condense_prompt(p: dict, user: str) -> str:
    """How a persona's chat is condensed when it grows long: a conversation, not work (agent/loop.py _compact)."""
    return CONDENSE.format(user=user, name=p["name"])


def when(stamp: str) -> str:
    """"Sun 1 Nov 2026": a chat's last activity in local time ("" when unknown)."""
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return ""
    t = (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(local_tz())
    return f"{WEEKDAYS[t.weekday()]} {t.day} {MONTHS[t.month - 1]} {t.year}"


def persona_prompt(p: dict, *, user: str, model_name: str, notes: str = "", recent: list[tuple[str, str]] = (),
                   summary: str = "", tools: set[str] | frozenset = frozenset(), now: datetime | None = None) -> str:
    name = p["name"]
    role = f", {p['role'].rstrip('.')}" if p.get("role") else ""
    lang = LANG_FIXED.format(language=p["language"], user=user) if p.get("language") else LANG_USER.format(user=user)
    parts = [IDENTITY.format(name=name, role=role, user=user, model=model_name)]
    if p.get("personality"):
        parts.append(f"# Who you are\n{p['personality'].strip()}")
    talk = STYLE.format(user=user, name=name, language=lang)
    if p.get("speech"):
        talk = f"{p['speech'].strip()}\n{talk}"
    parts.append(f"# How you talk\n{talk}")
    if p.get("examples"):
        parts.append("# Lines in your voice\nExamples of how you sound - not things that have been said in this "
                     f"conversation:\n{p['examples'].strip()}")
    if p.get("greeting"):
        parts.append(f"# How this conversation started\nYou greeted {user} with: \"{p['greeting'].strip()}\"")
    web, pictures = bool({"web_search", "web_fetch"} & tools), "image_generate" in tools
    cannot = [what for what, ok in (("look things up on the internet", web), ("make pictures", pictures)) if not ok]
    cannot += ["set reminders", "send messages"]
    what = ", ".join(cannot[:-1]) + " or " + cannot[-1]
    can = [MEMORY if "remember" in tools else NO_MEMORY] + [WEB] * web + [PICTURES] * pictures + [CANNOT]
    parts.append("# What you can do\n" + "\n".join(x.format(user=user, name=name, what=what) for x in can))
    now = now or datetime.now(local_tz())
    parts.append(f"Today is {now.strftime('%A %d %B %Y')}. Each message from {user} starts with the local date and "
                 "time it was sent, in square brackets: use it when the time of day matters, and never write such a "
                 "stamp yourself.")
    if "remember" in tools or notes.strip():
        parts.append(f"# Your notes about {user}\n" + (notes.strip() or "None yet."))
    if recent:
        lines = "\n".join(f"- {when(t) or 'earlier'}: {title}" for t, title in recent)
        parts.append(f"# Your last conversations with {user}\nOther chats, newest first - only their titles are "
                     f"here; your notes are what you remember of them:\n{lines}")
    if summary.strip():
        parts.append("# Earlier in this conversation\nThe earlier part of this chat was condensed into these notes:\n"
                     + summary.strip())
    return "\n\n".join(parts)
