"""A persona's tasks and posts (0.75.0, Søren: "I need to be able to give my persona tasks... like: At 7PM every
monday, Prepare a social media post with a picture or video about something.. a comment on the news, that resembles
the personas opinions, an exercise statement, a breakfast picture and quote.. and then list the posts for me to
review").

A task: a title, what to do in the owner's words, the days and the time (the household's time zone), what its post
needs (a picture, a video, either, or text only) and whether the persona may look things up for it. At its time the
Hub writes the task into the task's own chat with the persona - archived: the posts list is where the owner looks -
and starts a turn there: the persona's prompt, model and voice, the tools the task needs and post_draft, with which
the persona hands in the post: its text, and its picture or video copied into the persona's posts/. Nothing is
published: the posts wait for the owner (approve, change the text, discard, make another). A time missed while the
Hub was off is made up for within CATCH_UP, else left out (the task says so). The task's earlier posts are in its
chat, so the persona does not repeat itself."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MEDIA = ("picture", "video", "either", "none")
MAX_TASKS = 20
CATCH_UP = 6 * 3600              # a time missed while the Hub was off: made up for within six hours, else left out
TICK = 30                        # how often the schedule is looked at (s)
MAX_MEDIA = 4                    # files a post may have
MAX_TEXT = 5000
MAX_POSTS = 500                  # a persona's posts kept: the oldest handled ones go first, drafts never
LIMIT = {"picture": 50 * 1024 * 1024, "video": 500 * 1024 * 1024}
KINDS = {".png": ("picture", "png"), ".jpg": ("picture", "jpg"), ".jpeg": ("picture", "jpg"),
         ".webp": ("picture", "webp"), ".gif": ("picture", "gif"), ".mp4": ("video", "mp4"),
         ".webm": ("video", "webm")}
MAX_TASK_LORAS = 3               # a task's own LoRAs (0.79.0), after the persona's
TASK_DEFAULTS = {"title": "", "instruction": "", "days": [], "time": "19:00", "media": "picture", "web": False,
                 "loras": [], "enabled": True, "chat_id": None, "next_run": None, "last_run": None, "last_error": "",
                 "last_post": ""}
WEEKDAY = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
         "November", "December")


def next_time(days: list[int], at: str, after: float, tz) -> float | None:
    """The first time after `after` (epoch s) on one of `days` (0 Monday) at `at` ("19:00"), in the time zone."""
    m = TIME_RE.match(at or "")
    if not m or not days:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    start = datetime.fromtimestamp(after, tz).date()
    for i in range(8):
        d = start + timedelta(days=i)
        if d.weekday() in days:
            ts = datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz).timestamp()
            if ts > after:
                return ts
    return None


def latest_time(days: list[int], at: str, due: float, now: float, tz) -> float:
    """The last of a task's times from `due` up to `now` (the Hub was off for a while: the newest one counts)."""
    last = due
    for _ in range(4000):                                # (ten years of daily times)
        nxt = next_time(days, at, last, tz)
        if nxt is None or nxt > now:
            break
        last = nxt
    return last


def spoken_time(ts: float, tz) -> str:
    """"Monday 3 November 2026, 19:00"."""
    t = datetime.fromtimestamp(ts, tz)
    return f"{WEEKDAY[t.weekday()]} {t.day} {MONTH[t.month - 1]} {t.year}, {t:%H:%M}"


def _tz():
    from ..agent.prompt import local_tz
    return local_tz()


class TaskJobs:
    """Personas' tasks and posts - a part of Personas (store.py), apart for its size."""

    # ------------------------------------------------------------ tasks
    def task_of(self, p: dict | None, chat: dict | None) -> dict | None:
        """The task a chat is the chat of (None for a conversation)."""
        tid = ((chat or {}).get("settings") or {}).get("persona_task")
        return next((t for t in (p or {}).get("tasks") or [] if t.get("id") == tid), None) if tid else None

    def task_running(self, pid: str, tid: str) -> bool:
        job = self.task_jobs.get(f"{pid}/{tid}")
        return job is not None and not job.done()

    def _clean_task(self, fields: dict, t: dict) -> dict:
        from .store import PersonaError, _para, _text
        t = dict(t)
        if fields.get("title") is not None:
            t["title"] = _text(fields["title"], 80)
        if fields.get("instruction") is not None:
            t["instruction"] = _para(fields["instruction"], 2000)
        if fields.get("days") is not None:
            days = fields["days"]
            if not isinstance(days, list) or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6
                                                      for d in days):
                raise PersonaError("The days are 0 (Monday) to 6 (Sunday).")
            t["days"] = sorted(set(days))
        if fields.get("time") is not None:
            if not TIME_RE.match(str(fields["time"])):
                raise PersonaError("The time is hours and minutes, like 19:00.")
            t["time"] = str(fields["time"])
        if fields.get("media") is not None:
            if fields["media"] not in MEDIA:
                raise PersonaError("The post has a picture, a video, either, or text only.")
            missing = self.media_missing(fields["media"])
            if missing:
                raise PersonaError(missing)
            t["media"] = fields["media"]
        for key in ("web", "enabled"):
            if fields.get(key) is not None:
                t[key] = bool(fields[key])
        if fields.get("loras") is not None:
            t["loras"] = self._task_loras(fields["loras"])
        if not t["title"]:
            raise PersonaError("Give the task a title.")
        if not t["instruction"]:
            raise PersonaError("Say what the persona is to do.")
        if not t["days"]:
            raise PersonaError("Choose at least one day.")
        return t

    def _task_loras(self, raw) -> list[dict]:
        """A task's own LoRAs (0.79.0, Søren: "can we add a second lora to the tasks ? If i want to add filter or
        something like that"): a style or a filter on every new picture its posts get, after the persona's own LoRA
        (agent/tools/images.py). Each one in the Hub's LoRAs, once; its strength in its range, or None for its
        own. One that does not fit the picture model is left out at the picture, with a note (images/loras.py)."""
        from ..images.loras import strength_range
        from .store import PersonaError
        if not isinstance(raw, list):
            raise PersonaError("The task's LoRAs are a list.")
        store = self.hub.extras.get("loras")
        out: list[dict] = []
        for x in raw:
            lid = x.get("id") if isinstance(x, dict) else None
            lora = store.get(lid) if store is not None and isinstance(lid, str) and lid else None
            if lora is None:
                raise PersonaError("That LoRA is not there (Settings -> Models -> LoRAs).")
            if any(y["id"] == lid for y in out):
                continue
            strength = x.get("strength")
            if strength is not None:
                lo, hi = strength_range(bool(lora.get("slider")))
                try:
                    strength = round(max(lo, min(hi, float(strength))), 2)
                except (TypeError, ValueError):
                    raise PersonaError("A LoRA's strength is a number.") from None
            out.append({"id": lid, "strength": strength})
        if len(out) > MAX_TASK_LORAS:
            raise PersonaError(f"A task has at most {MAX_TASK_LORAS} LoRAs of its own.")
        return out

    def media_missing(self, media: str) -> str:
        """Why the Hub cannot make what a task's post has ("" when it can)."""
        images, video = self.hub.extras.get("images"), self.hub.extras.get("video")
        pictures = bool(images and images.available())
        videos = bool(video and video.available("t2v") or video and video.available("i2v"))
        if media == "picture" and not pictures:
            return "No picture model is downloaded (Settings -> Models)."
        if media == "video" and not videos:
            return "No video model is downloaded (Settings -> Models)."
        if media == "either" and not (pictures or videos):
            return "No picture or video model is downloaded (Settings -> Models)."
        return ""

    def _next(self, t: dict, after: float) -> float | None:
        return next_time(t["days"], t["time"], after, _tz()) if t.get("enabled") else None

    @staticmethod
    def _find(p: dict, tid: str) -> dict:
        from .store import PersonaError
        t = next((t for t in p.get("tasks") or [] if t.get("id") == tid), None)
        if t is None:
            raise PersonaError("No such task.", 404)
        return t

    async def add_task(self, pid: str, fields: dict) -> dict:
        from .store import PersonaError
        async with self.edit(pid) as p:
            if len(p["tasks"]) >= MAX_TASKS:
                raise PersonaError(f"A persona has at most {MAX_TASKS} tasks.")
            t = self._clean_task({"media": TASK_DEFAULTS["media"], **fields},
                                 {**TASK_DEFAULTS, "days": [], "id": "t" + secrets.token_hex(3),
                                  "created": int(time.time())})
            t["next_run"] = self._next(t, time.time())
            p["tasks"] = [*p["tasks"], t]
        return self.detail(pid)

    async def update_task(self, pid: str, tid: str, fields: dict) -> dict:
        async with self.edit(pid) as p:
            old = self._find(p, tid)
            t = self._clean_task(fields, old)
            if any(t.get(k) != old.get(k) for k in ("days", "time", "enabled")) or not old.get("next_run"):
                t["next_run"] = self._next(t, time.time())      # a new schedule counts from now
            p["tasks"] = [t if x is old else x for x in p["tasks"]]
        return self.detail(pid)

    async def delete_task(self, pid: str, tid: str) -> dict:
        from .store import PersonaError
        if self.task_running(pid, tid):
            raise PersonaError("The task is being done now - wait for its post.", 409)
        async with self.edit(pid) as p:
            old = self._find(p, tid)
            p["tasks"] = [x for x in p["tasks"] if x is not old]
        return self.detail(pid)

    # ------------------------------------------------------------ the schedule
    async def start_schedule(self) -> None:
        """A startup hook: a task a restart cut off says so, and the schedule is looked at every TICK seconds while
        the Hub runs."""
        from .store import PersonaError
        for item in self.list():
            p = self.get(item["id"])
            if not any(t.get("working") for t in (p or {}).get("tasks") or []):
                continue
            try:
                async with self.edit(item["id"]) as q:
                    for t in q["tasks"]:
                        if t.get("working"):
                            t["working"] = ""
                            t["last_error"] = "Stopped: the Hub restarted while the post was being made."
            except PersonaError:
                continue
        self.schedule_job = asyncio.get_running_loop().create_task(self._schedule(), name="persona-tasks")

    async def _schedule(self) -> None:
        while True:
            await asyncio.sleep(TICK)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - the next look comes all the same
                log.exception("persona tasks")
            try:
                await self.ig_tick()                   # 0.76.0: the Instagram tokens renewed (hourly)
            except Exception:  # noqa: BLE001
                log.exception("persona instagram")

    async def tick(self, now: float | None = None) -> list[str]:
        """Start each task whose time has come -> "<persona>/<task>" of those started. A time missed by more than
        CATCH_UP (the Hub was off) is left out and the task says so; one whose last run still works waits for the
        next time."""
        from .store import PersonaError
        now = time.time() if now is None else now
        started = []
        for item in self.list():
            p = self.get(item["id"])
            for t in (p or {}).get("tasks") or []:
                stored = t.get("next_run")
                if not t.get("enabled") or not stored or stored > now:
                    continue
                due = latest_time(t["days"], t["time"], stored, now, _tz())     # the Hub was off: the newest
                try:
                    async with self.edit(p["id"]) as q:
                        u = next((x for x in q["tasks"] if x.get("id") == t["id"]), None)
                        if u is None or u.get("next_run") != stored:          # changed meanwhile
                            continue
                        missed, busy = now - due > CATCH_UP, self.task_running(p["id"], t["id"])
                        u["next_run"] = self._next(u, now)
                        if missed:
                            u["last_error"] = f"Not made {spoken_time(due, _tz())}: the Hub was not running."
                        elif busy:
                            u["last_error"] = f"Not made {spoken_time(due, _tz())}: the one before was still working."
                    if not missed and not busy:
                        self.start_task(p["id"], t["id"], due)
                        started.append(f"{p['id']}/{t['id']}")
                except PersonaError as exc:                  # deleted, or started meanwhile: the others still go
                    log.info("task %s/%s not started: %s", p["id"], t["id"], exc)
        return started

    def start_task(self, pid: str, tid: str, due: float | None = None) -> None:
        """Do the task now (at its time, or the owner's Make one now)."""
        from .store import PersonaError
        self._find(self.load(pid), tid)
        if self.task_running(pid, tid):
            raise PersonaError("The task is being done now.", 409)
        self.task_jobs[f"{pid}/{tid}"] = asyncio.get_running_loop().create_task(self._do_task(pid, tid, due))

    async def _do_task(self, pid: str, tid: str, due: float | None) -> None:
        from ..chats import store as chats
        from .store import PersonaError, persona_id
        agent = self.hub.extras["agent"]
        run = None
        try:
            p = self.load(pid)
            t = self._find(p, tid)
            missing = self.media_missing(t["media"])
            if missing:
                raise RuntimeError(missing.rstrip("."))
            chat = chats.get_chat(t["chat_id"]) if t.get("chat_id") else None
            if chat is None or persona_id(chat) != pid or chat["settings"].get("persona_task") != tid:
                chat = chats.create_chat(None, t["title"], {"persona": pid, "persona_task": tid})
                chats.set_archived(chat["id"], True)         # the posts list is where the owner looks
            if agent.runs.active_for_chat(chat["id"]):        # the owner is talking with it there: nothing is added
                raise RuntimeError("its chat is working on an answer")
            rows = chats.messages(chat["id"])
            last = rows[-1] if rows else None
            if last and last["role"] == "user" and any(str(b.get("text") or "").startswith("Scheduled task")
                                                       for b in last["content"]):
                chats.delete_messages_after(chat["id"], last["id"] - 1)    # a run a restart cut off: no answer came
            text = (f"Scheduled task \"{t['title']}\" - {spoken_time(due or time.time(), _tz())}:\n"
                    f"{t['instruction']}")
            chats.add_message(chat["id"], "user", [{"type": "text", "text": text}])
            # what the persona knows of the owner as it is now (a week has gone since the last post)
            chats.update_chat(chat["id"], settings={"persona_memory": None})
            run = agent.start(chat["id"], {})
            async with self.edit(pid) as q:
                u = self._find(q, tid)
                u.update(chat_id=chat["id"], last_run=int(time.time()), last_error="", run=run.id, working=run.id)
            try:
                await asyncio.shield(run.task)
            except BaseException:  # noqa: BLE001 - the run's own end is what counts
                pass
            made = [s for s in self.posts_of(pid) if s.get("run") == run.id]
            why = next((e["data"].get("message") or "" for e in reversed(run.events) if e["type"] == "error"), "")
            async with self.edit(pid) as q:
                u = self._find(q, tid)
                u["working"] = ""
                if made:
                    u["last_post"] = made[-1]["id"]
                elif run.status == "cancelled":
                    u["last_error"] = "Stopped before a post was handed in."
                else:
                    u["last_error"] = "No post was handed in" + (f": {why}" if why else ".")
        except PersonaError:
            pass                                               # the persona or the task was deleted meanwhile
        except RuntimeError as exc:                            # the chat is working on something else
            await self._task_failed(pid, tid, f"Not started: {exc}.")
        except Exception as exc:  # noqa: BLE001 - the task says what stopped it
            log.exception("task %s/%s", pid, tid)
            await self._task_failed(pid, tid, f"It stopped: {exc}")
        finally:
            self.task_jobs.pop(f"{pid}/{tid}", None)

    async def _task_failed(self, pid: str, tid: str, why: str) -> None:
        from .store import PersonaError
        try:
            async with self.edit(pid) as q:
                u = self._find(q, tid)
                u.update(last_error=why, working="")
        except PersonaError:
            pass

    # ------------------------------------------------------------ posts
    def _posts_path(self, pid: str) -> Path:
        return self._dir(pid) / "posts.json"

    def posts_of(self, pid: str) -> list[dict]:
        try:
            data = json.loads(self._posts_path(pid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _write_posts(self, pid: str, posts: list[dict]) -> None:
        d = self._dir(pid)
        tmp = d / f".posts.{secrets.token_hex(3)}.tmp"
        tmp.write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(d / "posts.json")

    def post_face(self, pid: str) -> dict | None:
        """A post's persona as its card shows it: the name, the picture, the Instagram account its posts can go to
        ("" when none - 0.76.0)."""
        face = self.face(pid)
        return {**face, "instagram": self.ig_username(pid)} if face else None

    def posts(self, pid: str | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
        """Posts, the newest first - one persona's or all of them, each with its persona's name and picture. Status
        "review" (0.76.0): the drafts, and those on their way to Instagram or that did not get there."""
        def wanted(s: dict) -> bool:
            if status == "review":
                return s["status"] == "draft" or (s.get("instagram") or {}).get("state") in ("posting", "failed")
            return not status or s["status"] == status
        out = []
        for item in ([self.face(pid)] if pid else self.list()):
            if item is None:
                continue
            face = {**{k: item[k] for k in ("id", "name", "picture")}, "instagram": self.ig_username(item["id"])}
            out += [{**s, "persona": face} for s in self.posts_of(item["id"]) if wanted(s)]
        return sorted(out, key=lambda s: s.get("made") or 0, reverse=True)[:limit]

    async def hand_in(self, pid: str, tid: str, chat_id: int | None, run_id: str | None, workdir, text,
                      media, check: list[str] | None = None) -> dict:
        """post_draft: the post's text and its files from the chat's folder, kept with the persona as a draft. check:
        what Check pictures still found in those files (0.80.1) - the owner decides, the card says it."""
        from PIL import Image, UnidentifiedImageError

        from .store import PersonaError
        t = self._find(self.load(pid), tid)
        text = str(text or "").replace("\r", "").strip()
        if not text:
            raise PersonaError("Write the post's text (text).")
        if len(text) > MAX_TEXT:
            raise PersonaError(f"A post is at most {MAX_TEXT} characters.")
        paths = [str(x) for x in (media or []) if str(x).strip()]
        if len(paths) > MAX_MEDIA:
            raise PersonaError(f"A post has at most {MAX_MEDIA} pictures or videos.")

        def read() -> list[tuple[str, str, bytes]]:            # in a thread: a video may be hundreds of MB
            files = []
            for rel in paths:
                kind, ext = KINDS.get(Path(rel).suffix.lower(), (None, None))
                if kind is None:
                    raise PersonaError(f"{rel}: a post takes pictures (png, jpg, webp, gif) and videos (mp4, webm).")
                try:
                    inside = workdir.rel(workdir.resolve(rel))
                    with os.fdopen(workdir.open_inside(inside), "rb") as fh:   # not through a link put in its place
                        data = fh.read(LIMIT[kind] + 1)
                except (OSError, ValueError):
                    raise PersonaError(f"There is no file {rel} in your folder - give the path the tool that made it "
                                       "said.") from None
                if len(data) > LIMIT[kind]:
                    raise PersonaError(f"{rel} is over {LIMIT[kind] // 2**20} MB.")
                if kind == "picture":
                    try:
                        with Image.open(io.BytesIO(data)) as img:
                            img.verify()
                    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                        raise PersonaError(f"{rel} is not a picture.") from None
                elif not (data[4:8] == b"ftyp" or data[:4] == b"\x1a\x45\xdf\xa3"):
                    raise PersonaError(f"{rel} is not a video.")
                files.append((kind, ext, data))
            return files
        files = await asyncio.to_thread(read)
        kinds = {k for k, _, _ in files}
        need = t["media"]
        if need == "picture" and "picture" not in kinds:
            raise PersonaError("This task's post has a picture: make it (image_generate), then hand the post in with "
                               "its path in media.")
        if need == "video" and "video" not in kinds:
            raise PersonaError("This task's post has a video: make it (video_generate), then hand the post in with "
                               "its path in media.")
        if need == "either" and not kinds:
            raise PersonaError("This task's post has a picture or a video: make one, then hand the post in with its "
                               "path in media.")
        sid = "s" + secrets.token_hex(4)
        async with self._lock(pid):
            stored = []
            for i, (kind, ext, data) in enumerate(files):
                stored.append({"file": await asyncio.to_thread(self._put, pid, f"posts/{sid}-{i + 1}.{ext}", data),
                               "kind": kind})
            post = {"id": sid, "task": tid, "title": t["title"], "text": text, "media": stored, "status": "draft",
                    "made": int(time.time()), "chat": chat_id, "run": run_id,
                    **({"check": list(dict.fromkeys(check))[:4]} if check else {})}
            posts = self.posts_of(pid)
            again = [s for s in posts if run_id and s.get("run") == run_id and s["status"] == "draft"]
            for old in again:                                  # handed in again in the same run: this one counts
                self._drop_files(pid, old)
                posts.remove(old)
            posts.append(post)
            handled = [s for s in posts if s["status"] != "draft"
                       and (s.get("instagram") or {}).get("state") != "posting"]
            for old in handled[:max(0, len(posts) - MAX_POSTS)]:      # the oldest handled ones, when there are many
                self._drop_files(pid, old)
                posts.remove(old)
            self._write_posts(pid, posts)
        return {**post, "replaced": bool(again)}

    def _drop_files(self, pid: str, post: dict) -> None:
        for m in post.get("media") or []:
            try:
                self.path(pid, m["file"]).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 - a name that is not one of ours is left alone
                continue

    async def update_post(self, pid: str, sid: str, fields: dict) -> dict:
        """The owner's review: the text changed, approved (or back to a draft)."""
        from .store import PersonaError
        self.load(pid)
        async with self._lock(pid):
            posts = self.posts_of(pid)
            post = next((s for s in posts if s["id"] == sid), None)
            if post is None:
                raise PersonaError("No such post.", 404)
            state = (post.get("instagram") or {}).get("state")
            if state in ("posting", "posted") or self.ig_posting(pid, sid):
                raise PersonaError("It is on Instagram (or on its way) - the Hub cannot change it there." if state ==
                                   "posted" else "It is being posted - wait for it.", 409)
            if fields.get("text") is not None:
                text = str(fields["text"]).replace("\r", "").strip()
                if not text or len(text) > MAX_TEXT:
                    raise PersonaError(f"A post's text is 1 to {MAX_TEXT} characters.")
                post["text"] = text
            if fields.get("status") is not None:
                if fields["status"] not in ("draft", "approved"):
                    raise PersonaError("A post is a draft or approved.")
                post["status"] = fields["status"]
            self._write_posts(pid, posts)
        return {**post, "persona": self.post_face(pid)}

    async def delete_post(self, pid: str, sid: str) -> None:
        from .store import PersonaError
        self.load(pid)
        async with self._lock(pid):
            posts = self.posts_of(pid)
            post = next((s for s in posts if s["id"] == sid), None)
            if post is None:
                raise PersonaError("No such post.", 404)
            if self.ig_posting(pid, sid) or (post.get("instagram") or {}).get("state") == "posting":
                raise PersonaError("It is being posted - wait for it.", 409)
            self._drop_files(pid, post)
            self._write_posts(pid, [s for s in posts if s is not post])
